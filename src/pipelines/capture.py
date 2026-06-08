"""pipelines/capture.py

6-stage async capture pipeline: extract → enrich_urls → summarize → metadata → apply_location_tags → store.
Entry point: capture_file(path, context=None) -> Result[WriteOutcome]
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path

import structlog

import handlers  # noqa: F401 — side-effect: populates HandlerRegistry
from core.confidence import AIDecision
from core.config import VaultConfig
from core.pipeline import PipelineContext, run_pipeline
from core.result import Failure, Result, Success
from handlers.base import RawContent
from handlers.registry import HandlerRegistry
from handlers.url_fetcher import detect_urls, fetch_url_content
from llm.prompt_loader import PROMPTS
from llm.provider import get_provider
from vault.frontmatter import NoteMetadata
from vault.paths import (
    _is_misplaced,
    _location_context,
    is_batch_subfolder,
    resolve_placement,
    to_vault_path,
)
from vault.reader import read_note
from vault.move_guard import get_active
from vault.writer import (
    WriteOutcome,
    move_attachment,
    move_folder,
    move_note,
    write_note,
)
import core.audit as audit
from core.logging_setup import new_correlation_id
from core.rename_gate import RenameDecision, decide_rename
from core.tags import validate_tags
import storage.batches as batches
import storage.documents as documents

logger = structlog.get_logger(__name__)

__all__ = ["capture_file", "capture_folder", "scan_capture"]

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
    ai_project: str | None = None


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

    augmented = (
        raw.text + "\n\n---\n[Referenced URL Content]\n\n" + "\n\n".join(fetched)
    )
    return Success(
        RawContent(text=augmented, source_path=raw.source_path, is_md=raw.is_md)
    )


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
                (t[len("type/") :] for t in ai_tags if t.startswith("type/")), None
            )
            ai_domain = next(
                (t[len("domain/") :] for t in ai_tags if t.startswith("domain/")), None
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
                        logger.warning(
                            "tag_violation.audit_failed", violations=violations
                        )
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


async def apply_location_tags(
    mr: MetadataResult, ctx: PipelineContext
) -> Result[MetadataResult]:
    """Stage 5: derive domain/project tags from file location and set them on the note.

    Inspects the source path against the vault layout:
    - Domain/<D>/  → append "domain/<D>" to ai_tags (if D is a valid domain)
    - Projects/<A>/→ set ai_project = <A>
    - inbox/       → no change
    - elsewhere    → no change

    Returns Success(MetadataResult) in all cases — location inference is
    best-effort and never blocks the pipeline.
    """
    location_type, location_name = _location_context(
        mr.raw.source_path, ctx.config.vault
    )

    if location_type == "domain" and location_name is not None:
        valid_domains = (
            ctx.taxonomy.valid_domains if ctx.taxonomy is not None else frozenset()
        )
        if location_name not in valid_domains:
            logger.warning(
                "apply_location_tags.invalid_domain",
                path=str(mr.raw.source_path),
                domain=location_name,
            )
            return Success(mr)
        tag = f"domain/{location_name}"
        if tag in mr.ai_tags:
            return Success(mr)
        new_tags = list(mr.ai_tags) + [tag]
        return Success(replace(mr, ai_tags=new_tags, ai_domain=location_name))

    if location_type == "project" and location_name is not None:
        return Success(replace(mr, ai_project=location_name))

    # inbox or unknown — no change
    return Success(mr)


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
            logger.warning(
                "file_lost.audit_failed", path=str(path), stage=stage, error=e
            )
        case Success():
            pass


def _audit_skipped(path: Path, ctx: PipelineContext) -> None:
    """Write one audit_log row for a SKIPPED idempotent-check event.

    Best-effort — failures are logged and silently discarded so the caller
    always returns Success(SKIPPED) without worrying about audit write errors.
    """
    ai_decision = AIDecision(
        action="capture:idempotent_skip",
        confidence=1.0,
        reasoning="file unchanged since last capture (content hash match)",
        source_ids=[to_vault_path(path)],
    )
    match audit.write(
        ai_decision,
        pipeline="capture",
        stage="entry",
        outcome="SKIPPED",
        db_path=ctx.db_path,
    ):
        case Failure(error=e):
            logger.warning("idempotent_skip.audit_failed", path=str(path), error=e)
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
    """Stage 6: write note to vault and upsert documents row."""
    note_meta = NoteMetadata(
        summary=mr.summary,
        type=mr.ai_type,
        tags=mr.ai_tags,
        confidence=mr.decision.confidence,
        project=mr.ai_project,
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
            match documents.replace_path(
                old_vault_path, outcome, db_path=ctx.db_path, batch_id=ctx.batch_id
            ):
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
            match documents.upsert(outcome, db_path=ctx.db_path, batch_id=ctx.batch_id):
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

    src = mr.raw.source_path
    suffix = src.suffix
    vault_cfg = ctx.config.vault

    # ── Inline destination resolution (DECISION-026: pure path math, no AI) ─
    target_type: str | None = None
    target_name: str | None = None

    if vault_cfg.projects_path in src.parents:
        rel = src.relative_to(vault_cfg.projects_path)
        if len(rel.parts) >= 2:
            target_type, target_name = "project", rel.parts[0]
    elif vault_cfg.domain_path in src.parents:
        rel = src.relative_to(vault_cfg.domain_path)
        if len(rel.parts) >= 2:
            target_type, target_name = "domain", rel.parts[0]

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
            src,
            mr.ai_title,
            is_existing_doc=False,
            config=ctx.config.capture.rename_gate,
        )
        _audit_rename_gate(decision, src, ctx)
        sanitized_stem = decision.final_stem or src.stem

        placement = resolve_placement(src, target_type, target_name, vault_cfg)
        _is_no_edit = src.suffix.lower() in vault_cfg.no_edit_extensions

        # Ensure destination directories exist (resolve_placement is pure — no mkdir).
        placement.final_dir.mkdir(parents=True, exist_ok=True)
        placement.sibling_dir.mkdir(parents=True, exist_ok=True)

        if placement.needs_move:
            attachment_dst = placement.final_dir / f"{sanitized_stem}{suffix}"
            counter = 0
            while attachment_dst.exists() and counter < 99:
                counter += 1
                attachment_dst = (
                    placement.final_dir / f"{sanitized_stem}-{counter}{suffix}"
                )
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
        sibling_path = placement.sibling_dir / f"{attachment_dst.name}.md"
        attachment_vault_path = to_vault_path(attachment_dst)
        # Compute source_hash from the binary at its FINAL destination path.
        # If placement.needs_move is True, the binary hasn't been moved yet — hash src now.
        # After the move, the bytes are identical, so src hash == dst hash.
        _src_for_hash = src if placement.needs_move else attachment_dst
        _source_hash = hashlib.sha256(_src_for_hash.read_bytes()).hexdigest()
        sibling_meta = NoteMetadata(
            type="attachment-summary",
            attachment_path=attachment_vault_path,
            summary=mr.summary,
            # COUPLING: apply_location_tags must run before _store_nonmd; domain/<D> tag must be in note_meta.tags
            tags=["type/attachment-summary"]
            + [t for t in note_meta.tags if t.startswith("domain/")],
            confidence=note_meta.confidence,
            source_hash=_source_hash,
        )
        match write_note(sibling_path, rich_body, sibling_meta, actor="ai"):
            case Failure() as f:
                return f
            case Success(value=sibling_outcome):
                pass

        # Step 5: MOVE BINARY (only if not already at destination)
        if placement.needs_move:
            _g = get_active()
            if _g:
                _g.register(attachment_dst)
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
            reasoning=f"Routed to {target_type}/{target_name} ({'no-edit→attachment' if _is_no_edit else 'editable→root'})",
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
        match documents.upsert(
            sibling_outcome, db_path=ctx.db_path, batch_id=ctx.batch_id
        ):
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
            while inbox_dst.exists() and counter < 99:
                counter += 1
                inbox_dst = vault_cfg.inbox_path / f"{src.stem}-{counter}{suffix}"
            _g = get_active()
            if _g:
                _g.register(inbox_dst)
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

        match documents.upsert(
            marker_outcome, db_path=ctx.db_path, batch_id=ctx.batch_id
        ):
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
            context={
                "path": str(path),
                "age_seconds": age,
                "cooldown_seconds": cooldown,
            },
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

    # Idempotent-capture guard: skip if file content is unchanged since last capture.
    # For .md files: compare body hash (same method as write_note) against DB content_hash.
    # For binary files: compare file-level hash against source_hash stored in sibling frontmatter.
    if path.suffix.lower() == ".md":
        match read_note(path):
            case Success(value=_existing_note):
                # read_note already computes sha256(body.rstrip("\n").encode()) —
                # same method as write_note, so directly compare against DB content_hash.
                _current_hash = _existing_note.content_hash
                _db_result = documents.get_by_path(
                    to_vault_path(path), db_path=context.db_path
                )
                if (
                    _db_result.is_success()
                    and _db_result.value is not None
                    and _db_result.value.content_hash == _current_hash
                ):
                    _audit_skipped(path, context)
                    return Success(
                        WriteOutcome(
                            vault_path=to_vault_path(path),
                            absolute_path=path,
                            content_hash=_current_hash,
                            metadata=_existing_note.metadata,
                        )
                    )
            case _:
                pass  # parse failure → let pipeline handle it
    else:
        _vault_cfg = context.config.vault
        _sibling_path = (
            _vault_cfg.inbox_path / _vault_cfg.summaries_subdir / f"{path.name}.md"
        )
        # Also check LOCATED sibling (may be in project/domain summaries dir).
        # Try to find sibling by looking in known summaries locations.
        if not _sibling_path.exists():
            # Try LOCATED summaries: Projects/<A>/attachment/.summaries/<name>.md
            # and Domain/<D>/attachment/.summaries/<name>.md — scan parents.
            for _parent in path.parents:
                _candidate = _parent / _vault_cfg.summaries_subdir / f"{path.name}.md"
                if _candidate.exists():
                    _sibling_path = _candidate
                    break
        if _sibling_path.exists():
            match read_note(_sibling_path):
                case Success(value=_sibling_note) if _sibling_note.metadata.source_hash:
                    try:
                        _current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                    except FileNotFoundError:
                        _audit_file_lost(path, "entry", context)
                        return Failure(
                            error="file not found at capture entry",
                            recoverable=True,
                            context={"path": str(path)},
                        )
                    if _sibling_note.metadata.source_hash == _current_hash:
                        _audit_skipped(path, context)
                        return Success(
                            WriteOutcome(
                                vault_path=to_vault_path(_sibling_path),
                                absolute_path=_sibling_path,
                                content_hash=_sibling_note.content_hash,
                                metadata=_sibling_note.metadata,
                            )
                        )
                case _:
                    pass  # no source_hash or parse failure → let pipeline run

    # ── Batch-stamp pre-step (TD-040): if parent folder is batch-worthy,
    # look up or create a batch record and stamp context before pipeline runs.
    _batch_vault_cfg = context.config.vault
    if is_batch_subfolder(path.parent, _batch_vault_cfg):
        import unicodedata as _ud

        _folder_vp = _ud.normalize(
            "NFC",
            str(path.parent.relative_to(_batch_vault_cfg.root).as_posix()),
        )
        match batches.find_by_folder_path(_folder_vp, db_path=context.db_path):
            case Success(value=None):
                _batch_id = _insert_batch(
                    folder_name=path.parent.name,
                    destination_type=None,
                    destination_name=None,
                    confidence=1.0,
                    status="ROUTING",
                    file_count=1,
                    folder_path=_folder_vp,
                    ctx=context,
                )
                if _batch_id is not None:
                    context = replace(context, batch_id=_batch_id)
            case Success(value=_existing_bid):
                context = replace(context, batch_id=_existing_bid)
            case Failure(error=_berr):
                logger.warning(
                    "capture_file.batch_lookup_failed path=%s error=%s",
                    path,
                    _berr,
                )

    return await run_pipeline(
        "capture",
        [extract, enrich_urls, summarize, metadata, apply_location_tags, store],  # type: ignore[list-item]
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

    match scan_vault(_root, vault_cfg=CONFIG.main.vault):
        case Failure() as f:
            return f
        case Success(value=entries):
            match detect_changes(entries, db_path=_db_path):
                case Failure() as f:
                    return f
                case Success(value=summary):
                    pass
            outcomes: list[WriteOutcome] = []
            vault_cfg = CONFIG.main.vault  # type: ignore[attr-defined]

            # ── Subfolder detection pass (TD-041): dispatch unprocessed
            # batch-worthy subfolders before the per-file loop. ────────
            import unicodedata as _ud

            _batch_roots: list[Path] = [vault_cfg.inbox_path]
            if vault_cfg.projects_path.is_dir():
                for _p in vault_cfg.projects_path.iterdir():
                    if _p.is_dir():
                        _batch_roots.append(_p)
            if vault_cfg.domain_path.is_dir():
                for _d in vault_cfg.domain_path.iterdir():
                    if _d.is_dir():
                        _batch_roots.append(_d)
            for _root_dir in _batch_roots:
                for _entry in sorted(_root_dir.iterdir()):
                    if not _entry.is_dir():
                        continue
                    if is_batch_subfolder(_entry, vault_cfg):
                        _entry_vp = _ud.normalize(
                            "NFC",
                            str(_entry.relative_to(vault_cfg.root).as_posix()),
                        )
                        match batches.find_by_folder_path(_entry_vp, db_path=_db_path):
                            case Success(value=None):
                                _cr = await capture_folder(_entry)
                                if isinstance(_cr, Success):
                                    for _wo in _cr.value:
                                        outcomes.append(_wo)
                            case Success(value=int()):
                                logger.debug(
                                    "scan_capture.subfolder_already_batched folder=%s",
                                    _entry_vp,
                                )
                            case Failure(error=_berr):
                                logger.warning(
                                    "scan_capture.subfolder_batch_lookup_failed folder=%s error=%s",
                                    _entry_vp,
                                    _berr,
                                )

            for entry in summary.added:
                path = _root / entry.vault_path
                # ── Phase 5 (T4): Misplaced-md sweep ──────────────────────────
                # If an .md file is at the bare root of Projects/ or Domain/
                # (e.g. Projects/stray.md), sweep it to inbox before capture.
                # This prevents phantom project/domain creation from stray drops.
                if path.suffix.lower() == ".md" and _is_misplaced(path, vault_cfg):
                    inbox_dst = vault_cfg.inbox_path / path.name
                    counter = 0
                    while inbox_dst.exists() and counter < 99:
                        counter += 1
                        inbox_dst = (
                            vault_cfg.inbox_path / f"{path.stem}-{counter}{path.suffix}"
                        )
                    # Clean up stale DB row before moving
                    documents.delete_by_path(to_vault_path(path), db_path=_db_path)
                    match move_note(path, inbox_dst, actor="ai"):
                        case Failure(error=e):
                            logger.warning(
                                "scan_capture.misplaced_sweep_failed path=%s error=%s",
                                path,
                                e,
                            )
                            continue  # skip capture_file for this file
                        case Success():
                            pass
                    # Write MISPLACED audit row
                    audit.write(
                        AIDecision(
                            action="capture:sweep",
                            confidence=1.0,
                            reasoning="Misplaced md swept to inbox",
                            source_ids=[to_vault_path(path)],
                        ),
                        pipeline="capture",
                        stage="store",
                        outcome="MISPLACED",
                        db_path=_db_path,
                    )
                    path = inbox_dst
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
                        logger.info(
                            "scan_capture.skip_modified", path=str(path), reason=e
                        )
                    case Failure() as f:
                        logger.warning(
                            "scan_capture.failed_modified",
                            path=str(path),
                            error=f.error,
                        )

            # Moved notes: update vault_path in-place, preserving integer id (DECISION-001).
            # ON DELETE CASCADE keeps audit_log and corrections FKs valid without extra code.
            for old_vault_path, new_entry in summary.moved:
                match documents.rename(
                    old_vault_path, new_entry.vault_path, db_path=_db_path
                ):
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
                            "scan_capture.delete_failed",
                            vault_path=vault_path,
                            error=f.error,
                        )
                    case Success():
                        pass

            return Success(outcomes)


# ---------------------------------------------------------------------------
# Folder capture entry point (Phase 4.2 — Folder Handling)
# ---------------------------------------------------------------------------


def _build_vault_context(vault_cfg) -> str:
    """Return a human-readable listing of existing domain + project folders.

    Used to ground the classify_folder LLM prompt. Missing Domain/ or Projects/
    directories degrade gracefully to an empty list.
    """

    def _names(path: Path) -> list[str]:
        if not path.is_dir():
            return []
        return sorted(
            p.name for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")
        )

    domains = ", ".join(_names(vault_cfg.domain_path)) or "(none)"
    projects = ", ".join(_names(vault_cfg.projects_path)) or "(none)"
    return f"Domains: {domains}\nProjects: {projects}"


def _collect_folder_files(folder_path: Path, vault_cfg: VaultConfig) -> list[Path]:
    """Walk folder_path, returning capturable files.

    Skips: directories, dotfiles, any path passing through an IGNORE_DIRS or
    managed-subdir (attachment_dir, summaries_subdir) part.
    """
    from vault.indexer import IGNORE_DIRS

    skip_names = {vault_cfg.attachment_dir, vault_cfg.summaries_subdir}

    files: list[Path] = []
    for p in sorted(folder_path.rglob("*")):
        if p.is_dir():
            continue
        if p.name.startswith("."):
            continue
        rel_parts = p.relative_to(folder_path).parts
        if any(part in IGNORE_DIRS or part in skip_names for part in rel_parts):
            continue
        files.append(p)
    return files


def _parse_classify_json(content: str) -> tuple[str | None, str | None, float]:
    """Parse a classify_folder LLM response.

    Returns (target_type, target_name, confidence). On any parse/validation
    failure returns (None, None, 0.0) so the caller routes as CLUELESS.
    """
    cleaned = _FENCE_RE.sub("", content).strip()
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "classify_folder.json_parse_failed", content_preview=content[:200]
        )
        return (None, None, 0.0)

    target_type = parsed.get("target_type")
    target_name = parsed.get("target_name")
    confidence = parsed.get("confidence", 0.0)
    if target_type not in ("domain", "project") or not isinstance(target_name, str):
        return (None, None, 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    return (target_type, target_name, confidence)


def _folder_destination(target_type: str, target_name: str, vault_cfg) -> Path:
    """Resolve the destination *folder path* for a routed drop."""
    base = (
        vault_cfg.projects_path if target_type == "project" else vault_cfg.domain_path
    )
    return base / target_name


async def _capture_folder_files(
    folder_path: Path,
    files: list[Path],
    ctx: PipelineContext,
) -> Result[list[WriteOutcome]]:
    """Stage 2: capture each file via capture_file, then mark batch COMPLETE/PARTIAL.

    Failures (including FILE_LOST) are counted but never abort the loop. Returns
    Success of the successful WriteOutcomes.
    """
    outcomes: list[WriteOutcome] = []
    failures = 0
    for f in files:
        try:
            result = await capture_file(f, ctx)
        except Exception as exc:  # defensive: a file capture must not kill the batch
            failures += 1
            logger.warning("capture_folder.file_exception", path=str(f), error=str(exc))
            continue
        match result:
            case Success(value=outcome):
                outcomes.append(outcome)
            case Failure(error=e):
                failures += 1
                logger.info("capture_folder.file_failed", path=str(f), error=e)

    if ctx.batch_id is not None:
        status = "PARTIAL" if failures else "COMPLETE"
        match batches.update_status(ctx.batch_id, status, db_path=ctx.db_path):
            case Failure(error=e):
                logger.warning(
                    "capture_folder.batch_status_failed",
                    batch_id=ctx.batch_id,
                    status=status,
                    error=e,
                )
            case Success():
                pass

    return Success(outcomes)


def _insert_batch(
    folder_name: str,
    destination_type: str | None,
    destination_name: str | None,
    confidence: float,
    status: str,
    file_count: int,
    ctx: PipelineContext,
    folder_path: str | None = None,
) -> int | None:
    """Insert a batches row; return batch_id or None on failure (logged, non-fatal)."""
    match batches.insert(
        folder_name=folder_name,
        destination_type=destination_type,
        destination_name=destination_name,
        confidence=confidence,
        status=status,
        file_count=file_count,
        db_path=ctx.db_path,
        folder_path=folder_path,
    ):
        case Failure(error=e):
            logger.warning(
                "capture_folder.batch_insert_failed", folder=folder_name, error=e
            )
            return None
        case Success(value=batch_id):
            return batch_id


def _audit_folder_classified(
    folder_name: str,
    target_type: str | None,
    target_name: str | None,
    confidence: float,
    outcome: str,
    ctx: PipelineContext,
) -> None:
    """Write one FOLDER_CLASSIFIED audit row (best-effort)."""
    decision = AIDecision(
        action="capture:classify_folder",
        confidence=confidence,
        reasoning=f"Folder '{folder_name}' routed to {target_type}/{target_name}",
        source_ids=[folder_name],
    )
    match audit.write(
        decision,
        pipeline="capture",
        stage="classify_folder",
        outcome=outcome,
        db_path=ctx.db_path,
    ):
        case Failure(error=e):
            logger.warning("capture_folder.audit_failed", folder=folder_name, error=e)
        case Success():
            pass


async def capture_folder(
    folder_path: Path,
    context: PipelineContext | None = None,
) -> Result[list[WriteOutcome]]:
    """Entry point: classify a dropped folder and capture its files.

    NOT a pipeline stage. Orchestrates: collect files → determine location →
    (inbox) classify via LLM + route by confidence band, or (project/domain)
    skip the LLM → write a batches row → delegate per-file capture to capture_file.

    Args:
        folder_path: Absolute path to the dropped folder.
        context:     PipelineContext for tests. None → built from CONFIG.

    Returns:
        Success([WriteOutcome, ...]) — captured files (possibly empty).
        Failure only on unrecoverable orchestration errors.
    """
    from core.config import RouteDecision

    if context is None:
        context = await _build_default_context()
    ctx = context

    vault_cfg = ctx.config.vault

    # Stage 1: collect capturable files.
    files = _collect_folder_files(folder_path, vault_cfg)
    if not files:
        logger.info("capture_folder.empty", folder=str(folder_path))
        return Success([])

    # Stage 2: determine location.
    loc_type, loc_name = _location_context(folder_path, vault_cfg)

    # ── Case B: project/domain drop — skip LLM, route by path. ──────────────
    if loc_type in ("project", "domain"):
        batch_id = _insert_batch(
            folder_path.name,
            loc_type,
            loc_name,
            1.0,
            "ROUTING",
            len(files),
            ctx,
            folder_path=str(folder_path.relative_to(vault_cfg.root).as_posix()),
        )
        ctx_with_batch = replace(ctx, batch_id=batch_id)
        return await _capture_folder_files(folder_path, files, ctx_with_batch)

    # ── Case A: inbox (or unknown) drop — classify via LLM. ─────────────────
    file_manifest = "\n".join(f.name for f in files)
    vault_context = _build_vault_context(vault_cfg)
    system, user = PROMPTS["classify_folder"].render(
        folder_name=folder_path.name,
        file_manifest=file_manifest,
        vault_context=vault_context,
    )

    # LLM failure → treat as CLUELESS (confidence 0.0), never abort.
    match await get_provider("capture", ctx.config).complete(system, user):
        case Failure(error=e):
            logger.warning(
                "capture_folder.llm_failed", folder=str(folder_path), error=e
            )
            target_type, target_name, confidence = None, None, 0.0
        case Success(value=resp):
            target_type, target_name, confidence = _parse_classify_json(resp.content)

    from core.config import CONFIG as _CONFIG  # lazy — thresholds not on MainConfig

    decision = _CONFIG.thresholds.for_pipeline("classify").route(confidence)

    if decision is RouteDecision.AUTO and target_type and target_name:
        destination = _folder_destination(target_type, target_name, vault_cfg)
        # Folder move goes through the writer chokepoint (M1). On failure the
        # folder is left in inbox — fall through to the CLUELESS handling below
        # (per-file markers in place), mirroring the LLM-failure treatment.
        _g = get_active()
        if _g:
            _g.register(destination)
        match move_folder(folder_path, destination):
            case Failure(error=e):
                logger.warning(
                    "capture_folder.move_failed",
                    folder=str(folder_path),
                    destination=str(destination),
                    error=e,
                )
            case Success(value=new_folder):
                new_files = _collect_folder_files(new_folder, vault_cfg)
                batch_id = _insert_batch(
                    folder_path.name,
                    target_type,
                    target_name,
                    confidence,
                    "ROUTING",
                    len(new_files),
                    ctx,
                    folder_path=str(new_folder.relative_to(vault_cfg.root).as_posix()),
                )
                _audit_folder_classified(
                    folder_path.name,
                    target_type,
                    target_name,
                    confidence,
                    "FOLDER_CLASSIFIED",
                    ctx,
                )
                ctx_with_batch = replace(ctx, batch_id=batch_id)
                return await _capture_folder_files(
                    new_folder, new_files, ctx_with_batch
                )

    if decision is RouteDecision.SUGGEST:
        _insert_batch(
            folder_path.name,
            target_type,
            target_name,
            confidence,
            "PENDING_REVIEW",
            len(files),
            ctx,
            folder_path=str(folder_path.relative_to(vault_cfg.root).as_posix()),
        )
        _audit_folder_classified(
            folder_path.name,
            target_type,
            target_name,
            confidence,
            "FOLDER_CLASSIFIED",
            ctx,
        )
        return Success([])

    # CLUELESS (or AUTO without a valid target): write per-file CLUELESS markers
    # through the existing capture pipeline, then record a CLUELESS batch.
    batch_id = _insert_batch(
        folder_path.name,
        target_type,
        target_name,
        confidence,
        "CLUELESS",
        len(files),
        ctx,
        folder_path=str(folder_path.relative_to(vault_cfg.root).as_posix()),
    )
    ctx_with_batch = replace(ctx, batch_id=batch_id)
    for f in files:
        match await capture_file(f, ctx_with_batch):
            case Failure(error=e):
                logger.info("capture_folder.clueless_file_failed", path=str(f), error=e)
            case Success():
                pass
    _audit_folder_classified(
        folder_path.name, target_type, target_name, confidence, "CLUELESS", ctx
    )
    return Success([])
