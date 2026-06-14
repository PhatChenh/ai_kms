"""pipelines/capture.py

Phase 7A — Text Capture (DB-only, zero vault writes).

Entry point: capture_upload(vault_path, extracted_text, content_hash, ...) -> Result[int]
"""

from __future__ import annotations

from pathlib import Path

import structlog

from core.confidence import AIDecision
from core.result import Failure, Result, Success
from llm.prompt_loader import PROMPTS
from llm.provider import get_provider
import core.audit as audit
from core.logging_setup import new_correlation_id
import storage.documents as documents

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
        db_path: Override DB path for knowledge-facts lookups.

    Returns:
        ``Success((summary, title))`` or ``Failure`` if the AI call fails.
    """
    # (a) Dormant context injection — consult knowledge facts, degrade
    #     gracefully on empty.
    try:
        from storage.knowledge_entries import get_confident_and_pending as _get_facts
    except ImportError:
        _get_facts = None  # type: ignore[assignment]

    if _get_facts is not None:
        facts_result = _get_facts(db_path=db_path)
        match facts_result:
            case Success(facts):
                if facts:
                    logger.debug("summarize.context_facts count=%d", len(facts))
                # else: empty knowledge base → normal, proceed
            case Failure(error=err):
                logger.debug("summarize.context_facts_failed error=%s", err)
                # Non-fatal — proceed without context

    # (b) Ask the AI via the provider factory (C-08).
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


async def capture_upload(
    vault_path: str,
    extracted_text: str,
    content_hash: str,
    original_filename: str | None = None,
    file_size_bytes: int | None = None,
    db_path: Path | None = None,
) -> Result[int]:
    """Run the full text-capture pipeline on an upload from the daemon.

    This is the **single entry point** for text capture.  It orchestrates:

    1. Front-loaded dedup — peek at the existing row; skip AI if same hash
    2. Store raw first — ``upsert_from_upload`` saves the text immediately
    3. Summarize — ask the Housekeeping AI for a structured summary
    4. On AI success: attach summary, index, audit CAPTURED
    5. On AI failure: audit the failure, return Success (store-anyway)

    Args:
        vault_path:        POSIX-relative document path.
        extracted_text:    Full extracted text content.
        content_hash:      Content fingerprint (over raw bytes — ADR-0013).
        original_filename: Original upload filename (optional).
        file_size_bytes:   File size in bytes (optional).
        db_path:           Override DB path.

    Returns:
        ``Success(row_id)`` — the document row id (even on AI failure).
        ``Failure(recoverable=False)`` on database error (not on AI failure).
    """
    # 0. Set correlation ID FIRST — or every audit write silently drops.
    new_correlation_id()

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
            documents.attach_summary(
                vault_path=vault_path,
                summary=summary,
                title=title,
                db_path=db_path,
            )

            # Best-effort indexing
            try:
                from retrieval.keyword import index_keywords

                index_keywords(
                    vault_path=vault_path,
                    title=title,
                    summary=summary or "",
                    body=extracted_text,
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
