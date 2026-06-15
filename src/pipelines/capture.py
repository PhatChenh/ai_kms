"""pipelines/capture.py

Phase 7A — Text Capture (DB-only, zero vault writes).

Entry point: capture_upload(vault_path, extracted_text, content_hash, ...) -> Result[int]
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from core.confidence import AIDecision
from core.exceptions import ConfigError
from core.result import Failure, Result, Success
from llm.prompt_loader import PROMPTS
from llm.provider import get_provider
import core.audit as audit
from core.logging_setup import new_correlation_id
import storage.documents as documents

if TYPE_CHECKING:
    from storage.blobs import BlobStore

logger = structlog.get_logger(__name__)

__all__ = ["capture_upload"]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_summary_and_title(content: str) -> tuple[str, str]:
    """Split an LLM response into (summary, title).

    The prompt asks for the title on the very last line as ``Title: <text>``.
    Everything before that line is the summary body.  If no title line is
    found the whole content is treated as the summary and the title falls
    back to an empty string (caller should provide a fallback).
    """
    lines = content.strip().split("\n")
    # Walk backwards to find the title line
    title = ""
    summary_lines: list[str] = []
    found_title = False
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if not found_title and line.startswith("Title:"):
            title = line[len("Title:") :].strip()
            found_title = True
            # Everything before this line is the summary
            summary_lines = lines[:i]
            break
    if not found_title:
        summary_lines = lines
    summary = "\n".join(summary_lines).strip()
    return summary, title


# ---------------------------------------------------------------------------
# Summarizer stage (Housekeeping AI + dormant context injection)
# ---------------------------------------------------------------------------


async def _summarize_upload(
    text: str,
    db_path: Path | None = None,
) -> Result[tuple[str, str]]:
    """Ask the Housekeeping AI for a structured Markdown summary + title.

    This is the **summarize beat** of the store-raw-first contract.
    The raw text is already saved by the time this is called; this stage
    only returns the AI's output — the caller is responsible for attaching
    it via ``attach_summary``.

    Args:
        text:    Bare document text (do NOT pre-wrap in context builders).
        db_path: Override DB path (unused currently, reserved for future context injection).

    Returns:
        ``Success((summary, title))`` or ``Failure`` if the AI call fails.
    """
    # Ask the AI via the provider factory (C-08).
    from core.config import CONFIG  # noqa: C0415  -- lazy import

    provider = get_provider("capture", CONFIG.main)
    system, user = PROMPTS["capture_summary"].render(text=text)

    response_result = await provider.complete(system=system, user=user)
    match response_result:
        case Success(llm_response):
            summary, title = _parse_summary_and_title(llm_response.content)
            if not title:
                # Fallback: derive from first line of summary
                title = "Untitled"
            return Success((summary, title))
        case Failure() as failure:
            return failure


# ---------------------------------------------------------------------------
# Capture entry point (orchestrator)
# ---------------------------------------------------------------------------


def _best_effort_index(
    vault_path: str,
    title: str,
    summary: str,
    body: str,
    db_path: Path | None,
) -> None:
    """Index keywords and embeddings, logging but never propagating errors."""
    try:
        from retrieval.keyword import index_keywords

        index_keywords(
            vault_path=vault_path,
            title=title,
            summary=summary or "",
            body=body,
            db_path=db_path,
        )
    except Exception:
        logger.exception("capture.index_keywords_failed")

    try:
        from retrieval.embeddings import index_embedding

        index_embedding(
            vault_path=vault_path,
            title=title,
            note_type=None,
            tags=[],
            summary=summary or "",
            db_path=db_path,
        )
    except Exception:
        logger.exception("capture.index_embedding_failed")


async def capture_upload(
    vault_path: str,
    extracted_text: str | None = None,
    content_hash: str = "",
    original_filename: str | None = None,
    file_size_bytes: int | None = None,
    raw_bytes: bytes | None = None,
    mime_type: str | None = None,
    blob_store: BlobStore | None = None,
    db_path: Path | None = None,
) -> Result[int]:
    """Run the capture pipeline on an upload from the daemon.

    Two branches:

    * **Text branch** (7A): when *extracted_text* is present, runs the
      existing store-first / summarise-second text pipeline unchanged.
    * **Binary branch** (7B): when *extracted_text* is None and *raw_bytes*
      is present, runs the binary capture beats: dedup → store blob →
      store row → describable check → vision describe → attach summary.

    Args:
        vault_path:        POSIX-relative document path.
        extracted_text:    Full extracted text content (None for binary).
        content_hash:      Content fingerprint (over raw bytes — ADR-0013).
        original_filename: Original upload filename (optional).
        file_size_bytes:   File size in bytes (optional).
        raw_bytes:         Raw file bytes for binary uploads (optional).
        mime_type:         MIME type for binary uploads (optional).
        blob_store:        Blob store instance for binary uploads (optional).
        db_path:           Override DB path.

    Returns:
        ``Success(row_id)`` — the document row id (even on AI failure).
        ``Failure(recoverable=False)`` on database error (not on AI failure).
    """
    # 0. Set correlation ID FIRST — or every audit write silently drops.
    new_correlation_id()

    # ── Binary branch (Phase 7B) ────────────────────────────────────────
    if extracted_text is None and raw_bytes is not None:
        return await _capture_binary(
            vault_path=vault_path,
            raw_bytes=raw_bytes,
            content_hash=content_hash,
            mime_type=mime_type or "application/octet-stream",
            original_filename=original_filename,
            file_size_bytes=file_size_bytes,
            blob_store=blob_store,
            db_path=db_path,
        )

    # ── Guard: neither text nor bytes ──────────────────────────────────
    if extracted_text is None and raw_bytes is None:
        return Failure(
            error="neither text nor bytes supplied",
            recoverable=False,
            context={"vault_path": vault_path},
        )

    # ── Text branch (Phase 7A) — unchanged ─────────────────────────────
    # 1. Front-loaded dedup (P7-CAP-01): peek BEFORE the AI.
    existing = documents.get_by_path(vault_path, db_path=db_path)
    match existing:
        case Success(row) if row is not None and row.content_hash == content_hash:
            logger.info(
                "capture.dedup_skip vault_path=%s content_hash=%s",
                vault_path,
                content_hash,
            )
            return Success(row.id)
        case Failure(error=err):
            logger.warning("capture.dedup_lookup_failed error=%s", err)
            # Proceed — dedup is a speed optimisation, not a gate.

    # 2. Store raw first (P7-CAP-02 / P7-CAP-05).
    stem_title = Path(vault_path).stem
    store_result = documents.upsert_from_upload(
        vault_path=vault_path,
        extracted_text=extracted_text,
        content_hash=content_hash,
        original_filename=original_filename,
        file_size_bytes=file_size_bytes,
        title=stem_title,
        db_path=db_path,
    )
    match store_result:
        case Success(row_id):
            pass  # fall through to summarise
        case Failure() as store_failure:
            return store_failure

    # 3. Summarize (Phase 3 stage).
    # Broad try/except guards against unexpected exceptions (e.g. ConfigError
    # from get_provider, KeyError from PROMPTS) — the raw content is already
    # safely stored; an unhandled exception must not escape.
    try:
        summary_result = await _summarize_upload(text=extracted_text, db_path=db_path)
    except Exception as exc:
        summary_result = Failure(
            error=str(exc), recoverable=True, context={"stage": "summarize"}
        )

    match summary_result:
        case Success((summary, title)):
            # 4. AI SUCCESS path
            # Attach summary to the stored row
            attach_result = documents.attach_summary(
                vault_path=vault_path,
                summary=summary,
                title=title,
                db_path=db_path,
            )
            match attach_result:
                case Failure() as af:
                    logger.warning(
                        "capture.attach_summary_failed vault_path=%s error=%s",
                        vault_path,
                        af.error,
                    )

            # Best-effort indexing
            _best_effort_index(vault_path, title, summary, extracted_text, db_path)

            # Audit: CAPTURED (after physical writes succeed)
            audit.write(
                decision=AIDecision(
                    action="capture:summarize",
                    confidence=1.0,
                    reasoning="AI summarization succeeded",
                    source_ids=[vault_path],
                ),
                pipeline="capture",
                stage="summarize",
                outcome="CAPTURED",
                db_path=db_path,
            )

            # Phase 5 — classify trigger log stub
            logger.info("capture.classify_ready", vault_path=vault_path)

            return Success(row_id)

        case Failure() as ai_failure:
            # 5. AI FAILURE — store-anyway (P7-CAP-04)
            logger.warning(
                "capture.summarize_failed vault_path=%s error=%s",
                vault_path,
                ai_failure.error,
            )

            audit.write(
                decision=AIDecision(
                    action="capture:summarize_failed",
                    confidence=0.0,
                    reasoning=f"AI summarization failed: {ai_failure.error}",
                    source_ids=[vault_path],
                ),
                pipeline="capture",
                stage="summarize",
                outcome="FAILED",
                db_path=db_path,
            )

            # Still return Success — content is safe, summary fills in later
            return Success(row_id)


# ---------------------------------------------------------------------------
# Binary capture branch (Phase 7B)
# ---------------------------------------------------------------------------


async def _capture_binary(
    vault_path: str,
    raw_bytes: bytes,
    content_hash: str,
    mime_type: str,
    original_filename: str | None,
    file_size_bytes: int | None,
    blob_store: BlobStore | None,
    db_path: Path | None,
) -> Result[int]:
    """Run the five-beat binary capture branch.

    Beat 1: Front-loaded dedup — get_by_path; same content_hash → Success.
    Beat 2: Store blob FIRST — blob_store.put(content_hash, raw_bytes, mime_type).
    Beat 3: Store raw row — upsert_from_upload with blob_ref/mime_type.
    Beat 4: Describable check — mime in prefixes AND size <= max_vision_bytes?
    Beat 5: Vision describe — AI description → attach_summary, index, audit.
    """

    # Beat 1: Front-loaded dedup
    existing = documents.get_by_path(vault_path, db_path=db_path)
    match existing:
        case Success(row) if row is not None and row.content_hash == content_hash:
            logger.info(
                "capture.binary.dedup_skip vault_path=%s content_hash=%s",
                vault_path,
                content_hash,
            )
            return Success(row.id)
        case Failure(error=err):
            logger.warning("capture.binary.dedup_lookup_failed error=%s", err)
            # Proceed — dedup is a speed optimisation, not a gate.

    # Beat 2: Store blob FIRST
    if blob_store is None:
        return Failure(
            error="blob_store is required for binary capture",
            recoverable=False,
            context={"vault_path": vault_path},
        )

    put_result = await blob_store.async_put(content_hash, raw_bytes, mime_type)
    match put_result:
        case Success():
            pass  # blob safe
        case Failure() as blob_failure:
            logger.error(
                "capture.binary.blob_store_failed vault_path=%s error=%s",
                vault_path,
                blob_failure.error,
            )
            return Failure(
                error=f"blob store failed: {blob_failure.error}",
                recoverable=False,
                context={"vault_path": vault_path},
            )

    # Beat 3: Store raw row — file is safe now
    stem_title = Path(vault_path).stem
    store_result = documents.upsert_from_upload(
        vault_path=vault_path,
        extracted_text=None,
        content_hash=content_hash,
        original_filename=original_filename,
        file_size_bytes=file_size_bytes,
        title=stem_title,
        blob_ref=content_hash,
        mime_type=mime_type,
        db_path=db_path,
    )
    match store_result:
        case Success(row_id):
            pass
        case Failure() as store_failure:
            return store_failure

    # Beat 4: Describable check
    try:
        from core.config import CONFIG  # noqa: C0415 -- lazy import

        vision_cfg = CONFIG.main.capture.vision
        describable_prefixes = vision_cfg.describable_mime_prefixes
        max_bytes = vision_cfg.max_vision_bytes
    except ConfigError:
        # If config can't be loaded, skip describing
        logger.error("capture.binary.config_load_failed — skipping vision")
        _audit_skip(vault_path, "config_load_failed", db_path)
        return Success(row_id)

    size_for_check = file_size_bytes if file_size_bytes is not None else len(raw_bytes)

    # Check MIME type prefix
    mime_ok = any(mime_type.startswith(prefix) for prefix in describable_prefixes)
    if not mime_ok:
        _audit_skip(vault_path, "unsupported type", db_path)
        return Success(row_id)

    # Check size cap
    if size_for_check > max_bytes:
        _audit_skip(vault_path, "too big", db_path)
        return Success(row_id)

    # Beat 5: Vision describe
    try:
        provider = get_provider("vision", CONFIG.main)
        prompt = PROMPTS["describe_image"]
        system, user = prompt.render(mime_type=mime_type)

        desc_result = await provider.describe_image(
            system=system,
            user=user,
            image_bytes=raw_bytes,
            mime_type=mime_type,
        )
    except Exception as exc:
        logger.warning(
            "capture.binary.vision_setup_failed vault_path=%s error=%s",
            vault_path,
            exc,
        )
        _audit_failed(vault_path, f"vision setup failed: {exc}", db_path)
        return Success(row_id)

    match desc_result:
        case Success(llm_response):
            # Parse description + title (same pattern as text path)
            summary, title = _parse_summary_and_title(llm_response.content)
            if not title:
                title = stem_title

            # Attach summary (with full_body so description is keyword-searchable)
            attach_result = documents.attach_summary(
                vault_path=vault_path,
                summary=summary,
                title=title,
                full_body=summary,
                db_path=db_path,
            )
            match attach_result:
                case Failure() as af:
                    logger.warning(
                        "capture.binary.attach_summary_failed vault_path=%s error=%s",
                        vault_path,
                        af.error,
                    )

            # Best-effort indexing
            _best_effort_index(vault_path, title, summary, summary or "", db_path)

            # Audit: DESCRIBED
            audit.write(
                decision=AIDecision(
                    action="capture:describe_image",
                    confidence=1.0,
                    reasoning="Binary image described successfully",
                    source_ids=[vault_path],
                ),
                pipeline="capture",
                stage="describe",
                outcome="DESCRIBED",
                db_path=db_path,
            )

            logger.info("capture.classify_ready", vault_path=vault_path)

            return Success(row_id)

        case Failure() as ai_failure:
            logger.warning(
                "capture.binary.vision_failed vault_path=%s error=%s",
                vault_path,
                ai_failure.error,
            )
            _audit_failed(
                vault_path, f"vision describe failed: {ai_failure.error}", db_path
            )
            return Success(row_id)


# ---------------------------------------------------------------------------
# Binary branch audit helpers
# ---------------------------------------------------------------------------


def _audit_skip(vault_path: str, reason: str, db_path: Path | None) -> None:
    """Write a SKIPPED audit entry for the binary branch."""
    audit.write(
        decision=AIDecision(
            action="capture:describe_skip",
            confidence=0.0,
            reasoning=reason,
            source_ids=[vault_path],
        ),
        pipeline="capture",
        stage="describe",
        outcome="SKIPPED",
        db_path=db_path,
    )


def _audit_failed(vault_path: str, reason: str, db_path: Path | None) -> None:
    """Write a FAILED audit entry for the binary branch."""
    audit.write(
        decision=AIDecision(
            action="capture:describe_failed",
            confidence=0.0,
            reasoning=reason,
            source_ids=[vault_path],
        ),
        pipeline="capture",
        stage="describe",
        outcome="FAILED",
        db_path=db_path,
    )
