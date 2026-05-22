"""pipelines/capture.py

5-stage async capture pipeline: extract → enrich_urls → summarize → metadata → store.
Entry point: capture_file(path, context=None) -> Result[WriteOutcome]
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

import handlers  # noqa: F401 — side-effect: populates HandlerRegistry
from core.confidence import AIDecision
from core.pipeline import PipelineContext, run_pipeline
from core.result import Failure, Result, Success
from handlers.base import RawContent
from handlers.registry import HandlerRegistry
from handlers.url_fetcher import detect_urls, fetch_url_content
from llm.prompt_loader import PROMPTS
from llm.provider import LLMResponse, get_provider
from vault.frontmatter import NoteMetadata
from vault.paths import to_vault_path
from vault.reader import read_note
from vault.writer import WriteOutcome, move_attachment, move_note, write_note
import core.audit as audit
from core.logging_setup import new_correlation_id
from core.tags import validate_tags
import storage.documents as documents

logger = structlog.get_logger(__name__)

__all__ = ["capture_file", "scan_capture"]

# ---------------------------------------------------------------------------
# Private intermediate dataclasses (not exported)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SummarizeResult:
    raw: RawContent
    summary: str


@dataclass(frozen=True)
class MetadataResult:
    raw: RawContent
    summary: str
    ai_title: str
    ai_type: str | None
    ai_domain: str | None
    ai_tags: list[str]
    decision: AIDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FENCE_RE = re.compile(r"^```(?:json)?\n?|^```\n?", re.MULTILINE)


def _sanitize_title(title: str) -> str:
    """Strip path-unsafe chars and trim to 120 characters."""
    return _UNSAFE_CHARS.sub("", title)[:120].strip()


def _should_enrich(text: str, urls: list[str], max_urls: int) -> bool:
    """Return True if the note is URL-sparse enough to warrant URL enrichment.

    Enrich when: URL count within limit AND body text (excluding URLs) is < 500 chars.
    Both conditions must hold — dense text with few URLs is still reference-heavy.
    """
    body_only = re.sub(r"https?://\S+", "", text).strip()
    return len(urls) <= max_urls and len(body_only) < 500


def _parse_metadata_json(content: str, source_stem: str = "") -> dict | Failure:
    """Parse LLM JSON metadata response into a validated dict.

    Args:
        content:     Raw LLM response (may contain markdown code fences).
        source_stem: Filename stem used as title fallback when title is missing/invalid.

    Returns:
        dict with keys "title", "type", "tags" on success.
        Failure(recoverable=False) only on completely unparseable JSON.
        Validation errors (bad types, missing fields) fall back to defaults.
    """
    cleaned = _FENCE_RE.sub("", content).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return Failure(
            error=f"metadata JSON parse error: {exc}",
            recoverable=False,
            context={"content_preview": content[:200]},
        )

    title = parsed.get("title", "")
    if not isinstance(title, str) or not title.strip():
        logger.warning("metadata.title_fallback", source_stem=source_stem)
        title = source_stem
    title = _sanitize_title(title) or source_stem

    raw_tags = parsed.get("tags", [])
    if isinstance(raw_tags, list):
        tags: list[str] = [str(t) for t in raw_tags]
    else:
        logger.warning("metadata.tags_coerced", got=type(raw_tags).__name__)
        tags = []

    # Strip legacy "type" key — type is now derived from type/<name> tag
    return {"title": title, "tags": tags}


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


async def extract(path: Path, ctx: PipelineContext) -> Result[RawContent]:
    """Stage 1: resolve handler and extract raw content from the file."""
    match HandlerRegistry.resolve(path):
        case Failure() as f:
            return f
        case Success(value=handler):
            return handler.extract(path)


async def enrich_urls(raw: RawContent, ctx: PipelineContext) -> Result[RawContent]:
    """Stage 2: fetch URL content and augment text for URL-sparse notes.

    Never returns Failure — worst case returns the original raw unchanged.
    """
    urls = detect_urls(raw.text)
    if not urls:
        return Success(raw)

    max_urls: int = ctx.config.capture.max_urls_per_note
    if not _should_enrich(raw.text, urls, max_urls):
        logger.info("enrich_urls.skip", reason="reference-heavy", url_count=len(urls))
        return Success(raw)

    fetched: list[str] = []
    for url in urls[:max_urls]:
        match await asyncio.to_thread(fetch_url_content, url):
            case Failure() as f:
                logger.warning("enrich_urls.fetch_failed", url=url, error=f.error)
            case Success(value=content):
                fetched.append(f"## {url}\n{content[:5000]}")

    if not fetched:
        return Success(raw)

    augmented = raw.text + "\n\n---\n[Referenced URL Content]\n\n" + "\n\n".join(fetched)
    return Success(RawContent(text=augmented, source_path=raw.source_path, is_md=raw.is_md))


async def summarize(raw: RawContent, ctx: PipelineContext) -> Result[SummarizeResult]:
    """Stage 3: summarize note text via LLM."""
    provider = get_provider("capture", ctx.config)
    system, user = PROMPTS["summarize"].render(text=raw.text)
    match await provider.complete(system, user):
        case Failure() as f:
            return f
        case Success(value=resp):
            return Success(SummarizeResult(raw=raw, summary=resp.content.strip()))


async def metadata(sr: SummarizeResult, ctx: PipelineContext) -> Result[MetadataResult]:
    """Stage 4: extract structured metadata via LLM and write an audit_log row."""
    provider = get_provider("capture", ctx.config)
    domain_list = (
        ", ".join(sorted(ctx.taxonomy.valid_domains))
        if ctx.taxonomy and ctx.taxonomy.valid_domains
        else "(none — no Domain/ folders configured)"
    )
    system, user = PROMPTS["extract_metadata"].render(
        text=sr.raw.text, summary=sr.summary, domain_list=domain_list
    )
    match await provider.complete(system, user):
        case Failure() as f:
            return f
        case Success(value=resp):
            source_stem = sr.raw.source_path.stem
            parsed = _parse_metadata_json(resp.content, source_stem=source_stem)
            if isinstance(parsed, Failure):
                return parsed

            ai_tags: list[str] = parsed.get("tags", [])
            violations: list[str] = []
            if ctx.taxonomy is not None:
                ai_tags, violations = validate_tags(ai_tags, ctx.taxonomy)

            ai_type = next(
                (t[len("type/"):] for t in ai_tags if t.startswith("type/")), None
            )
            ai_domain = next(
                (t[len("domain/"):] for t in ai_tags if t.startswith("domain/")), None
            )

            source_id = to_vault_path(sr.raw.source_path)
            decision = AIDecision(
                action="capture:metadata",
                confidence=0.9,
                reasoning=f"Summarized and extracted metadata. Title: {parsed['title']}",
                source_ids=[source_id],
            )
            match audit.write(
                decision,
                pipeline="capture",
                stage="metadata",
                outcome="CAPTURED",
                db_path=ctx.db_path,
            ):
                case Failure() as f:
                    return f
                case Success():
                    pass

            if violations:
                viol_decision = AIDecision(
                    action="capture:tag_violation",
                    confidence=1.0,
                    reasoning=f"Dropped {len(violations)} tag(s): {violations}",
                    source_ids=[source_id],
                )
                match audit.write(
                    viol_decision,
                    pipeline="capture",
                    stage="metadata",
                    outcome="TAG_VIOLATION",
                    db_path=ctx.db_path,
                ):
                    case Failure():
                        logger.warning("tag_violation.audit_failed", violations=violations)
                    case Success():
                        pass

            return Success(
                MetadataResult(
                    raw=sr.raw,
                    summary=sr.summary,
                    ai_title=parsed["title"],
                    ai_type=ai_type,
                    ai_domain=ai_domain,
                    ai_tags=ai_tags,
                    decision=decision,
                )
            )


def _find_rename_dst(parent: Path, sanitized_stem: str) -> Path | None:
    """Find a non-colliding destination path for a note rename.

    Tries: <stem>.md, <stem>-1.md, ..., <stem>-9.md.
    Returns None if all 10 slots are taken.
    """
    candidate = parent / f"{sanitized_stem}.md"
    if not candidate.exists():
        return candidate
    for i in range(1, 10):
        candidate = parent / f"{sanitized_stem}-{i}.md"
        if not candidate.exists():
            return candidate
    return None


async def store(mr: MetadataResult, ctx: PipelineContext) -> Result[WriteOutcome]:
    """Stage 5: write note to vault and upsert documents row."""
    note_meta = NoteMetadata(
        summary=mr.summary,
        type=mr.ai_type,
        domain=mr.ai_domain,
        tags=mr.ai_tags,
        confidence=mr.decision.confidence,
    )

    if mr.raw.is_md:
        return await _store_md(mr, note_meta, ctx)
    else:
        return await _store_nonmd(mr, note_meta, ctx)


async def _store_md(
    mr: MetadataResult, note_meta: NoteMetadata, ctx: PipelineContext
) -> Result[WriteOutcome]:
    """Handle .md files: in-place write or rename when AI title differs."""
    match read_note(mr.raw.source_path):
        case Failure() as f:
            return f
        case Success(value=note):
            original_body = note.content

    sanitized_stem = _sanitize_title(mr.ai_title)
    src = mr.raw.source_path

    if sanitized_stem != src.stem:
        dst = _find_rename_dst(src.parent, sanitized_stem)
        if dst is not None:
            old_vault_path = to_vault_path(src)
            # move_note carries on-disk metadata; write_note on dst applies AI metadata
            match move_note(src, dst, actor="ai"):
                case Failure() as f:
                    return f
                case Success():
                    pass
            match write_note(dst, original_body, note_meta, actor="ai"):
                case Failure() as f:
                    return f
                case Success(value=outcome):
                    documents.delete_by_path(old_vault_path, db_path=ctx.db_path)
                    match documents.upsert(outcome, db_path=ctx.db_path):
                        case Failure() as f:
                            return f
                        case Success():
                            return Success(outcome)
        else:
            logger.warning(
                "store.rename_collision_fallback",
                src=str(src),
                ai_title=mr.ai_title,
                reason="all 10 rename slots taken",
            )

    # In-place write (no rename or fallback)
    match write_note(src, original_body, note_meta, actor="ai"):
        case Failure() as f:
            return f
        case Success(value=outcome):
            match documents.upsert(outcome, db_path=ctx.db_path):
                case Failure() as f:
                    return f
                case Success():
                    return Success(outcome)


async def _store_nonmd(
    mr: MetadataResult, note_meta: NoteMetadata, ctx: PipelineContext
) -> Result[WriteOutcome]:
    """Handle non-md files: create sibling .md + move binary to attachment/."""
    src = mr.raw.source_path
    sanitized_stem = _sanitize_title(mr.ai_title) or src.stem
    suffix = src.suffix

    sibling = src.parent / f"{sanitized_stem}.md"

    # Resolve attachment destination with collision loop (cap 100)
    attachment_dir: Path = ctx.config.vault.attachment_path
    attachment_dst = attachment_dir / f"{sanitized_stem}{suffix}"
    counter = 0
    while attachment_dst.exists() and counter < 100:
        counter += 1
        attachment_dst = attachment_dir / f"{sanitized_stem}-{counter}{suffix}"

    if attachment_dst.exists():
        return Failure(
            error="attachment collision: all 100 slots taken",
            recoverable=False,
            context={"stem": sanitized_stem, "suffix": suffix},
        )

    sibling_body = f"![[{attachment_dst.name}]]"
    match write_note(sibling, sibling_body, note_meta, actor="ai"):
        case Failure() as f:
            return f
        case Success(value=sibling_outcome):
            pass

    match documents.upsert(sibling_outcome, db_path=ctx.db_path):
        case Failure() as f:
            return f

    if not src.exists():
        logger.warning(
            "store.attachment_already_moved",
            src=str(src),
            reason="source not found; skipping move_attachment",
        )
        return Success(sibling_outcome)

    match move_attachment(src, attachment_dst):
        case Failure() as f:
            logger.warning("store.move_attachment_failed", error=f.error)
        case Success():
            pass

    return Success(sibling_outcome)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def capture_file(
    path: Path,
    context: PipelineContext | None = None,
) -> Result[WriteOutcome]:
    """Run the full capture pipeline on a file.

    Rejects files whose mtime is newer than cooldown_seconds (still being written).

    Args:
        path:    Absolute path to the file to capture.
        context: PipelineContext for tests. None → run_pipeline creates one from CONFIG.

    Returns:
        Success(WriteOutcome) on success, or Failure on any stage error.
    """
    # Stability gate — resolve cooldown from context or lazy CONFIG
    if context is not None:
        cooldown = context.config.capture.cooldown_seconds
    else:
        from core.config import CONFIG  # lazy — avoids module-scope vault validation
        from core.tags import load_taxonomy
        from vault.paths import load_valid_domains

        cooldown = CONFIG.main.capture.cooldown_seconds  # type: ignore[attr-defined]

        age = time.time() - path.stat().st_mtime
        if age < cooldown:
            return Failure(
                error=f"file too recent (age={age:.1f}s < cooldown={cooldown}s); retry later",
                recoverable=True,
                context={"path": str(path), "age_seconds": age, "cooldown_seconds": cooldown},
            )

        valid_domains = load_valid_domains(CONFIG.main.vault.root)  # type: ignore[attr-defined]
        taxonomy = load_taxonomy(
            Path(__file__).parent.parent / "config" / "tags.yaml",
            valid_domains,
        )
        context = PipelineContext(
            config=CONFIG.main,  # type: ignore[attr-defined]
            db_path=CONFIG.main.database.path,  # type: ignore[attr-defined]
            correlation_id=new_correlation_id(),
            taxonomy=taxonomy,
        )
        return await run_pipeline(
            "capture",
            [extract, enrich_urls, summarize, metadata, store],  # type: ignore[list-item]
            path,
            context=context,
        )

    age = time.time() - path.stat().st_mtime
    if age < cooldown:
        return Failure(
            error=f"file too recent (age={age:.1f}s < cooldown={cooldown}s); retry later",
            recoverable=True,
            context={"path": str(path), "age_seconds": age, "cooldown_seconds": cooldown},
        )

    return await run_pipeline(
        "capture",
        [extract, enrich_urls, summarize, metadata, store],  # type: ignore[list-item]
        path,
        context=context,
    )


async def scan_capture(
    root: Path | None = None,
    db_path: Path | None = None,
) -> Result[list[WriteOutcome]]:
    """Scan vault and reconcile all four change types from detect_changes.

    Change types handled:
    - added:    new .md notes → full capture pipeline
    - modified: changed .md notes → full re-capture (fresh summary + frontmatter)
    - deleted:  notes removed from disk → documents row removed
    - moved:    notes renamed on disk → vault_path updated, integer id preserved (DECISION-001)

    Non-md binary drops are also processed (DECISION-018 override for scan).

    Args:
        root:    Vault root. None → lazy-imports CONFIG.
        db_path: SQLite path. None → lazy-imports CONFIG.

    Returns:
        Success([WriteOutcome, ...]) — one per successfully captured/re-captured file.
        Failure if scan_vault itself fails (I/O error, permission denied).
    """
    from core.config import CONFIG  # lazy — avoids module-scope vault validation
    from core.tags import load_taxonomy
    from vault.indexer import detect_changes, scan_vault
    from vault.paths import load_valid_domains

    _root: Path = root or CONFIG.main.vault.root  # type: ignore[attr-defined]
    _db_path: Path = db_path or CONFIG.main.database.path  # type: ignore[attr-defined]

    valid_domains = load_valid_domains(_root)
    taxonomy = load_taxonomy(
        Path(__file__).parent.parent / "config" / "tags.yaml",
        valid_domains,
    )

    match scan_vault(_root):
        case Failure() as f:
            return f
        case Success(value=entries):
            match detect_changes(entries, db_path=_db_path):
                case Failure() as f:
                    return f
                case Success(value=summary):
                    pass
            outcomes: list[WriteOutcome] = []
            for entry in summary.added:
                path = _root / entry.vault_path
                ctx = PipelineContext(
                    config=CONFIG.main,  # type: ignore[attr-defined]
                    db_path=_db_path,
                    correlation_id=new_correlation_id(),
                    taxonomy=taxonomy,
                )
                match await capture_file(path, context=ctx):
                    case Success(value=v):
                        outcomes.append(v)
                    case Failure(error=e, recoverable=True):
                        logger.info("scan_capture.skip", path=str(path), reason=e)
                    case Failure() as f:
                        logger.warning(
                            "scan_capture.failed", path=str(path), error=f.error
                        )
            # Non-md drops: process binaries not yet in attachment/ folder.
            # DECISION-018 override: scan_vault only indexes .md; this loop handles binary drops.
            from vault.indexer import scan_non_md_drops

            _attachment_path: Path = CONFIG.main.vault.attachment_path  # type: ignore[attr-defined]
            non_md_paths = scan_non_md_drops(_root, _attachment_path)
            for path in non_md_paths:
                ctx = PipelineContext(
                    config=CONFIG.main,  # type: ignore[attr-defined]
                    db_path=_db_path,
                    correlation_id=new_correlation_id(),
                    taxonomy=taxonomy,
                )
                match await capture_file(path, context=ctx):
                    case Success(value=v):
                        outcomes.append(v)
                    case Failure(error=e, recoverable=True):
                        logger.info("scan_capture.skip_nonmd", path=str(path), reason=e)
                    case Failure() as f:
                        logger.warning(
                            "scan_capture.failed_nonmd", path=str(path), error=f.error
                        )

            # Modified notes: full re-capture (fresh summary + frontmatter via full pipeline).
            for entry in summary.modified:
                path = _root / entry.vault_path
                ctx = PipelineContext(
                    config=CONFIG.main,  # type: ignore[attr-defined]
                    db_path=_db_path,
                    correlation_id=new_correlation_id(),
                    taxonomy=taxonomy,
                )
                match await capture_file(path, context=ctx):
                    case Success(value=v):
                        outcomes.append(v)
                    case Failure(error=e, recoverable=True):
                        logger.info("scan_capture.skip_modified", path=str(path), reason=e)
                    case Failure() as f:
                        logger.warning(
                            "scan_capture.failed_modified", path=str(path), error=f.error
                        )

            # Moved notes: update vault_path in-place, preserving integer id (DECISION-001).
            # ON DELETE CASCADE keeps audit_log and corrections FKs valid without extra code.
            for old_vault_path, new_entry in summary.moved:
                match documents.rename(old_vault_path, new_entry.vault_path, db_path=_db_path):
                    case Failure() as f:
                        logger.warning(
                            "scan_capture.rename_failed",
                            old=old_vault_path,
                            new=new_entry.vault_path,
                            error=f.error,
                        )
                    case Success():
                        logger.info(
                            "scan_capture.renamed",
                            old=old_vault_path,
                            new=new_entry.vault_path,
                        )

            # Deleted notes: remove documents row (DECISION-008 CASCADE cleans corrections).
            for vault_path in summary.deleted:
                match documents.delete_by_path(vault_path, db_path=_db_path):
                    case Failure() as f:
                        logger.warning(
                            "scan_capture.delete_failed", vault_path=vault_path, error=f.error
                        )
                    case Success():
                        pass

            return Success(outcomes)
