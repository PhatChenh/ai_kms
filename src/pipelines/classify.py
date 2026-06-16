"""
pipelines/classify.py

Public API surface for the classify pipeline: content reader, context loader,
async worker consumer, and catch-up scan.

Extraction, entry writing, and orchestration live in sibling modules:
  - classify_extract.py    (_validate_item, extract)
  - classify_writer.py     (WriteSummary, write_entries, helpers)
  - classify_orchestrator.py (orchestrate, retry helpers)
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from core.config import MainConfig
from core.result import Failure, Result, Success

_worker_log = logging.getLogger(__name__)


# ===========================================================================
# Phase 6 -- Content Reader + Context Loader (classify infra helpers)
# ===========================================================================


def content_reader(
    doc_id: int,
    *,
    config: MainConfig,
    db_path: Path | None = None,
) -> Result[str]:
    """Choose full_body or summary for a document based on token budget.

    Uses the // 4 heuristic to estimate token count from character length.
    If full_body fits within config.classify.max_content_tokens, it is used;
    otherwise the summary is used instead.  When full_body is None or empty,
    summary is always used as a fallback.

    Args:
        doc_id: Primary key of the documents row.
        config: Validated MainConfig carrying the classify token cap.
        db_path: Override DB path.

    Returns:
        Success(str) with the chosen text, or Failure if the row is missing
        or the DB is unreachable.
    """
    from storage.db import get_connection

    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT full_body, summary FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"doc_id": doc_id, "op": "content_reader"},
        )

    if row is None:
        return Failure(
            error=f"document not found: id={doc_id}",
            recoverable=False,
            context={"doc_id": doc_id, "op": "content_reader"},
        )

    full_body: str | None = row["full_body"]
    summary: str | None = row["summary"]

    # Fallback: None or empty full_body -> summary
    if not full_body:
        return Success(summary or "")

    max_tokens: int = config.classify.max_content_tokens
    estimated_tokens = len(full_body) // 4

    if estimated_tokens < max_tokens:
        return Success(full_body)

    return Success(summary or "")


def context_loader(
    *,
    config: MainConfig,
    db_path: Path | None = None,
    dimensions_path: Path | None = None,
) -> Result[dict[str, list]]:
    """Load ranked, capped, non-retired knowledge entries for every dimension.

    Reads the dimension list from config/dimensions.yaml (Phase 2), then for
    each dimension calls the Phase 4 ranked query with
    ``config.classify.max_entries_per_dimension`` as the cap.

    Args:
        config:  Validated MainConfig carrying the per-dimension cap.
        db_path: Override DB path.
        dimensions_path: Override path to dimensions.yaml.

    Returns:
        Success(dict[dimension -> list[KnowledgeEntry]]) with ranked, capped,
        non-retired facts.  Dimensions with zero matching facts get an empty
        list (not an error).
    """
    from core.tags import load_dimensions
    from storage.knowledge_entries import query_ranked_by_dimension

    if dimensions_path is None:
        _classify_dir = Path(__file__).resolve().parent  # src/pipelines
        _project_root = _classify_dir.parent.parent  # project root
        dimensions_path = _project_root / "config" / "dimensions.yaml"

    dims_result = load_dimensions(dimensions_path)
    if isinstance(dims_result, Failure):
        return Failure(
            error=f"context_loader cannot load dimensions: {dims_result.error}",
            recoverable=False,
            context={"op": "context_loader", "path": str(dimensions_path)},
        )

    rulebook: dict = dims_result.value
    cap: int = config.classify.max_entries_per_dimension

    result: dict[str, list] = {}

    for dim_name in rulebook:
        ranked = query_ranked_by_dimension(
            dim_name,
            limit=cap,
            db_path=db_path,
        )
        if isinstance(ranked, Failure):
            return Failure(
                error=f"context_loader query failed for dimension {dim_name!r}: {ranked.error}",
                recoverable=False,
                context={"op": "context_loader", "dimension": dim_name},
            )
        result[dim_name] = ranked.value

    # Phase 10: Enrich entries with comments (best-effort, non-mutating)
    import sqlite3 as _sqlite3
    from dataclasses import replace as _replace
    from storage.db import get_connection as _get_conn

    try:
        with _get_conn(db_path, readonly=True) as conn:
            conn.row_factory = _sqlite3.Row
            all_ids: list[int] = []
            for entries in result.values():
                all_ids.extend(e.id for e in entries if e.id is not None)

            if all_ids:
                placeholders = ", ".join("?" for _ in all_ids)
                rows = conn.execute(
                    f"SELECT entry_id, comment_text FROM entry_comments WHERE entry_id IN ({placeholders}) ORDER BY entry_id, created_at",
                    all_ids,
                ).fetchall()
                from collections import defaultdict

                by_entry: dict[int, list[str]] = defaultdict(list)
                for r in rows:
                    by_entry[r["entry_id"]].append(r["comment_text"])
                if by_entry:
                    for dim_name, entries in result.items():
                        result[dim_name] = [
                            _replace(
                                e,
                                reasoning=(e.reasoning or "")
                                + "\nComments: "
                                + "; ".join(by_entry[e.id]),
                            )
                            if e.id in by_entry
                            else e
                            for e in entries
                        ]
    except _sqlite3.Error:
        pass  # Best-effort — comments are supplementary context

    return Success(result)


# ===========================================================================
# Phase 7 -- Work Queue + Worker + catch-up scan
# ===========================================================================


async def consumer(
    queue: asyncio.Queue[int],
    db_path: Path | None,
    config: MainConfig,
) -> None:
    """Single sequential consumer: pulls doc_ids from the queue, prepares
    inputs via Content Reader -> Dimension Loader -> Context Loader, then
    **stops** -- this is the Slice B seam.  No AI call, no stamp on the
    happy path.

    Failures at any stage are logged and the doc is left un-stamped so it
    will be retried on the next startup catch-up scan.
    """
    from pipelines.classify_orchestrator import orchestrate

    _dimensions_path = (
        Path(__file__).resolve().parent.parent / "config" / "dimensions.yaml"
    )

    while True:
        got_item = False
        try:
            doc_id = await queue.get()
            got_item = True

            orch_result = await orchestrate(
                doc_id,
                config=config,
                db_path=db_path,
                dimensions_path=_dimensions_path,
            )
            if isinstance(orch_result, Failure):
                _worker_log.warning(
                    "classify_worker orchestrate failed doc_id=%s error=%s",
                    doc_id,
                    orch_result.error,
                )

        except Exception:
            _worker_log.exception("classify_worker unexpected error doc_id=%s", doc_id)
        finally:
            if got_item:
                queue.task_done()


async def catch_up_scan(
    queue: asyncio.Queue[int],
    db_path: Path | None,
) -> None:
    """One burst at startup: discover every doc that needs classification
    and enqueue its id.

    OQ-P8A-03 -- catch-up scan.
    """
    from storage.documents_classify import find_unclassified

    result = find_unclassified(db_path=db_path)
    if isinstance(result, Failure):
        _worker_log.error(
            "catch_up_scan find_unclassified failed error=%s",
            result.error,
        )
        return

    for doc_id in result.value:
        queue.put_nowait(doc_id)

    _worker_log.info("catch_up_scan enqueued=%d", len(result.value))
