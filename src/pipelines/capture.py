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
from core.rename_gate import RenameDecision, decide_rename
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


def _parse_metadata_json(content: str, source_stem: str = "") -> Result[dict]:
    """Parse LLM JSON metadata response into a validated dict.

    Args:
        content:     Raw LLM response (may contain markdown code fences).
        source_stem: Filename stem used as title fallback when title is missing/invalid.

    Returns:
        Success(dict) with keys "title" and "tags" on success.
        Failure(recoverable=False) only on completely unparseable JSON.
        Validation errors (bad types, missing fields) fall back to defaults.
    """
    cleaned = _FENCE_RE.sub("", content).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning(
            "metadata.json_parse_failed.fallback",
            source_stem=source_stem,
            content_preview=content[:200],
        )
        return Success({"title": source_stem, "tags": []})

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
    return Success({"title": title, "tags": tags})


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
            match _parse_metadata_json(resp.content, source_stem=source_stem):
                case Failure() as f:
                    return f
                case Success(value=parsed):
                    pass

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

            logger.info(
                "metadata.captured",
                src=str(sr.raw.source_path),
                ai_title=parsed["title"],
                ai_type=ai_type,
                ai_domain=ai_domain,
                tags=ai_tags,
            )

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


def _audit_rename_gate(
    decision: RenameDecision, src: Path, ctx: PipelineContext
) -> None:
    """Write one audit_log row for a rename gate decision.

    Failures are logged and silently discarded — gate audit is best-effort.
    Audit log writes MUST be attempted (Phase 8 briefing reads stage="rename_gate"),
    but a failed write must not abort the capture pipeline itself.
    """
    from core.confidence import AIDecision

    ai_decision = AIDecision(
        action=f"rename_gate:{decision.action.value}",
        confidence=decision.confidence,
        reasoning=decision.reason,
        source_ids=[to_vault_path(src)],
    )
    match audit.write(
        ai_decision,
        pipeline="capture",
        stage="rename_gate",
        outcome=decision.action.value,
        db_path=ctx.db_path,
    ):
        case Failure(error=e):
            logger.warning("rename_gate.audit_failed", src=str(src), error=e)
        case Success():
            pass

    logger.info(
        "rename_gate.decision",
        src=src.name,
        action=decision.action.value,
        final_stem=decision.final_stem,
        reason=decision.reason,
    )


def _audit_file_lost(path: Path, stage: str, ctx: PipelineContext) -> None:
    """Write one audit_log row for a FILE_LOST event.

    Best-effort — failures are logged and silently discarded so the caller
    can always return Failure without worrying about audit write errors.
    """
    ai_decision = AIDecision(
        action=f"file_lost:{stage}",
        confidence=0.0,
        reasoning=f"file not found at capture {stage}",
        source_ids=[to_vault_path(path)],
    )
    match audit.write(
        ai_decision,
        pipeline="capture",
        stage=stage,
        outcome="FILE_LOST",
        db_path=ctx.db_path,
    ):
        case Failure(error=e):
            logger.warning("file_lost.audit_failed", path=str(path), stage=stage, error=e)
        case Success():
            pass


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
    if not mr.raw.source_path.exists():
        _audit_file_lost(mr.raw.source_path, "store", ctx)
        return Failure(
            error="file disappeared during pipeline",
            recoverable=False,
            context={"path": str(mr.raw.source_path)},
        )

    match read_note(mr.raw.source_path):
        case Failure() as f:
            return f
        case Success(value=note):
            original_body = note.content

    src = mr.raw.source_path

    # Gate: check if already in documents table (is_existing_doc = Rule 1 SKIP).
    is_existing_doc = False
    match documents.get_by_path(to_vault_path(src), db_path=ctx.db_path):
        case Success(value=row) if row is not None:
            is_existing_doc = True
        case _:
            pass

    decision = decide_rename(
        src, mr.ai_title, is_existing_doc, config=ctx.config.capture.rename_gate
    )
    _audit_rename_gate(decision, src, ctx)
    sanitized_stem = decision.final_stem

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
                    # Disk rename succeeded but content write failed — roll back rename.
                    match move_note(dst, src, actor="ai"):
                        case Failure(error=rollback_err):
                            logger.error(
                                "store.rename_rollback_failed",
                                src=str(src),
                                dst=str(dst),
                                original_error=f.error,
                                rollback_error=rollback_err,
                            )
                        case Success():
                            pass
                    return f
                case Success(value=outcome):
                    pass
            # Atomic DB swap: delete old row + insert new row in one transaction.
            match documents.replace_path(old_vault_path, outcome, db_path=ctx.db_path):
                case Failure() as f:
                    # DB failed — roll back disk rename to restore consistent state.
                    match move_note(dst, src, actor="ai"):
                        case Failure(error=rollback_err):
                            logger.error(
                                "store.db_replace_rollback_failed",
                                src=str(src),
                                dst=str(dst),
                                original_error=f.error,
                                rollback_error=rollback_err,
                            )
                        case Success():
                            pass
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
    """Handle non-md files: resolve destination (LOCATED or CLUELESS), write sibling, move binary.

    LOCATED: source path reveals project or domain → sibling written first, binary moved second
             (DECISION-025 sibling-first ordering).
    CLUELESS: no path context → binary parked in inbox, pending-routing marker written for
              Phase 2 Classify (DECISION-027).
    """
    from vault.paths import (
        domain_attachment,
        domain_summaries,
        project_attachment,
        project_summaries,
    )

    src = mr.raw.source_path
    suffix = src.suffix
    vault_cfg = ctx.config.vault

    # ── Inline destination resolution (DECISION-026: pure path math, no AI) ─
    target_type: str | None = None
    target_name: str | None = None
    needs_move = False

    if vault_cfg.projects_path in src.parents:
        rel = src.relative_to(vault_cfg.projects_path)
        if len(rel.parts) >= 2:
            target_type, target_name = "project", rel.parts[0]
            needs_move = rel.parts[1] != vault_cfg.attachment_dir
    elif vault_cfg.domain_path in src.parents:
        rel = src.relative_to(vault_cfg.domain_path)
        if len(rel.parts) >= 2:
            target_type, target_name = "domain", rel.parts[0]
            needs_move = rel.parts[1] != vault_cfg.attachment_dir

    if target_type is not None:
        # LOCATED path: rename gate + rich sibling body + binary move
        # Non-md files are never re-processed (DECISION-018) — is_existing_doc=False always.
        if not src.exists():
            _audit_file_lost(src, "store", ctx)
            return Failure(
                error="file disappeared during pipeline",
                recoverable=False,
                context={"path": str(src)},
            )
        decision = decide_rename(
            src, mr.ai_title, is_existing_doc=False, config=ctx.config.capture.rename_gate
        )
        _audit_rename_gate(decision, src, ctx)
        sanitized_stem = decision.final_stem or src.stem

        if target_type == "project":
            att_dir = project_attachment(target_name)  # type: ignore[arg-type]
            sum_dir = project_summaries(target_name)  # type: ignore[arg-type]
        else:
            att_dir = domain_attachment(target_name)  # type: ignore[arg-type]
            sum_dir = domain_summaries(target_name)  # type: ignore[arg-type]

        if needs_move:
            attachment_dst = att_dir / f"{sanitized_stem}{suffix}"
            counter = 0
            while attachment_dst.exists() and counter < 100:
                counter += 1
                attachment_dst = att_dir / f"{sanitized_stem}-{counter}{suffix}"
            if attachment_dst.exists():
                return Failure(
                    error="attachment collision: all 100 slots taken",
                    recoverable=False,
                    context={"stem": sanitized_stem, "suffix": suffix},
                )
            sibling_stem = sanitized_stem
        else:
            attachment_dst = src  # binary already at final destination
            sibling_stem = src.stem

        # Step 3: Rich sibling body via summarize_attachment prompt
        provider = get_provider("capture", ctx.config)
        system, user = PROMPTS["summarize_attachment"].render(
            file_type=suffix.lower(),
            short_summary=mr.summary,
            text=mr.raw.text,
        )
        match await provider.complete(system, user):
            case Failure() as f:
                return f
            case Success(value=resp):
                rich_body = resp.content.strip()

        # Step 4: WRITE SIBLING FIRST (DECISION-025)
        # Sibling name = binary's full filename + ".md" (e.g. report.pdf.md) so
        # that report.pdf and report.docx never collide on the same sibling.
        sibling_path = sum_dir / f"{attachment_dst.name}.md"
        attachment_vault_path = to_vault_path(attachment_dst)
        sibling_meta = NoteMetadata(
            type="attachment-summary",
            attachment_path=attachment_vault_path,
            summary=mr.summary,
            tags=["type/attachment-summary"]
            + ([f"domain/{note_meta.domain}"] if note_meta.domain else []),
            domain=note_meta.domain,
            confidence=note_meta.confidence,
        )
        match write_note(sibling_path, rich_body, sibling_meta, actor="ai"):
            case Failure() as f:
                return f
            case Success(value=sibling_outcome):
                pass

        # Step 5: MOVE BINARY (only if not already at destination)
        if needs_move:
            match move_attachment(src, attachment_dst):
                case Failure() as f:
                    # Sibling written with broken pointer — accepted failure mode
                    # (DECISION-025). TD-026 tracks orphan reconciliation in Brief #3.
                    logger.error(
                        "store.located_move_failed",
                        src=str(src),
                        dst=str(attachment_dst),
                        sibling=str(sibling_path),
                        error=f.error,
                    )
                    return f
                case Success():
                    pass

        # Step 6: Write LOCATED audit entry
        located_decision = AIDecision(
            action="capture:store",
            confidence=1.0,
            reasoning=f"Routed to {target_type}/{target_name}",
            source_ids=[to_vault_path(src)],
        )
        match audit.write(
            located_decision,
            pipeline="capture",
            stage="store",
            outcome="LOCATED",
            db_path=ctx.db_path,
        ):
            case Failure(error=e):
                logger.warning("store.located_audit_failed", error=e)
            case Success():
                pass

        # Step 7: Upsert documents row for sibling
        match documents.upsert(sibling_outcome, db_path=ctx.db_path):
            case Failure() as f:
                return f
            case Success():
                return Success(sibling_outcome)

    else:
        # CLUELESS path: no project/domain context (DECISION-027)
        # Binary parked in inbox; pending-routing marker written for Phase 2 Classify.
        if vault_cfg.inbox_path in src.parents:
            final_src = src  # already in inbox — stays
        else:
            # Move binary to inbox with collision handling
            inbox_dst = vault_cfg.inbox_path / src.name
            counter = 0
            while inbox_dst.exists() and counter < 100:
                counter += 1
                inbox_dst = vault_cfg.inbox_path / f"{src.stem}-{counter}{suffix}"
            match move_attachment(src, inbox_dst):
                case Failure() as f:
                    return f
                case Success():
                    final_src = inbox_dst

        # Write pending-routing marker at inbox/.summaries/<filename>.md
        summaries_dir = vault_cfg.inbox_path / vault_cfg.summaries_subdir
        summaries_dir.mkdir(parents=True, exist_ok=True)
        marker_path = summaries_dir / f"{final_src.name}.md"
        attachment_rel = to_vault_path(final_src)
        marker_meta = NoteMetadata(
            type="attachment-summary",
            status="pending-routing",
            attachment_path=attachment_rel,
            tags=["type/attachment-summary"],
        )
        marker_body = (
            f"_Pending classification — binary at:_ `{attachment_rel}`\n\n"
            "This marker was created by the capture pipeline without project/domain "
            "context. Phase 2 (Classify) will replace this body with a full summary "
            "and route the binary into a project or domain attachment folder.\n"
        )
        match write_note(marker_path, marker_body, marker_meta, actor="ai"):
            case Failure() as f:
                return f
            case Success(value=marker_outcome):
                pass

        # Write CLUELESS audit entry
        clueless_decision = AIDecision(
            action="capture:store",
            confidence=1.0,
            reasoning="No project/domain context — parked for Phase 2 Classify",
            source_ids=[to_vault_path(src)],
        )
        match audit.write(
            clueless_decision,
            pipeline="capture",
            stage="store",
            outcome="CLUELESS",
            db_path=ctx.db_path,
        ):
            case Failure(error=e):
                logger.warning("store.clueless_audit_failed", error=e)
            case Success():
                pass

        match documents.upsert(marker_outcome, db_path=ctx.db_path):
            case Failure() as f:
                return f
            case Success():
                return Success(marker_outcome)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _build_default_context() -> PipelineContext:
    """Build a PipelineContext from CONFIG for callers that don't supply one.

    Lazy-imported to avoid module-scope vault validation in test environments.
    """
    from core.config import CONFIG  # lazy — avoids module-scope vault validation
    from core.tags import load_taxonomy
    from vault.paths import load_valid_domains

    valid_domains = load_valid_domains(CONFIG.main.vault.root)  # type: ignore[attr-defined]
    taxonomy = load_taxonomy(
        Path(__file__).parent.parent / "config" / "tags.yaml",
        valid_domains,
    )
    return PipelineContext(
        config=CONFIG.main,  # type: ignore[attr-defined]
        db_path=CONFIG.main.database.path,  # type: ignore[attr-defined]
        correlation_id=new_correlation_id(),
        taxonomy=taxonomy,
    )


async def capture_file(
    path: Path,
    context: PipelineContext | None = None,
) -> Result[WriteOutcome]:
    """Run the full capture pipeline on a file.

    Rejects files whose mtime is newer than cooldown_seconds (still being written).

    Args:
        path:    Absolute path to the file to capture.
        context: PipelineContext for tests. None → built from CONFIG via lazy import.

    Returns:
        Success(WriteOutcome) on success, or Failure on any stage error.
    """
    if context is None:
        context = await _build_default_context()

    cooldown = context.config.capture.cooldown_seconds
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        _audit_file_lost(path, "entry", context)
        return Failure(
            error="file not found at capture entry",
            recoverable=True,
            context={"path": str(path)},
        )
    if age < cooldown:
        return Failure(
            error=f"file too recent (age={age:.1f}s < cooldown={cooldown}s); retry later",
            recoverable=True,
            context={"path": str(path), "age_seconds": age, "cooldown_seconds": cooldown},
        )

    # Early-exit guard for CLUELESS binaries already parked in inbox (DECISION-027).
    # If inbox/.summaries/<filename>.md has status=pending-routing, skip re-processing.
    if path.suffix.lower() != ".md":
        vault_cfg = context.config.vault
        marker = vault_cfg.inbox_path / vault_cfg.summaries_subdir / f"{path.name}.md"
        if marker.exists():
            match read_note(marker):
                case Success(value=note) if note.metadata.status == "pending-routing":
                    return Failure(
                        error="pending-routing — binary already indexed; awaiting Phase 2 classify",
                        recoverable=True,
                        context={"path": str(path), "marker": str(marker)},
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

            non_md_paths = scan_non_md_drops(_root, CONFIG.main.vault)
            if non_md_paths:
                logger.info("scan_capture.nonmd_found", count=len(non_md_paths))
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
            # Skip .summaries/ paths — sibling .md files are owned by the sync
            # pipeline; re-capturing them would wipe attachment_path from frontmatter
            # (TD-AS-1).
            _summaries_subdir = CONFIG.main.vault.summaries_subdir  # type: ignore[attr-defined]
            for entry in summary.modified:
                if _summaries_subdir in Path(entry.vault_path).parts:
                    continue
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
