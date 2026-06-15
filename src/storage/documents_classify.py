"""
storage/documents_classify.py

Classify-specific data access helpers extracted from storage/documents.py.

Work Finder, Classified-Stamp, and retry-state operations for the
classify subsystem.  All functions accept an optional ``db_path`` override
for testability.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core.result import Failure, Result, Success
from storage.db import get_connection


# ---------------------------------------------------------------------------
# Phase 5 -- Work Finder + Classified-Stamp
# ---------------------------------------------------------------------------


def find_unclassified(*, db_path: Path | None = None) -> Result[list[int]]:
    """Return ids of documents whose classify_content_hash is NULL or stale.

    Work-discovery query for the classify subsystem: a document needs
    classification when it has never been classified (NULL) or its content
    has changed since it was last classified (classify_content_hash !=
    content_hash).
    """
    try:
        with get_connection(db_path, readonly=True) as conn:
            # NOTE: OR condition may prevent index use on idx_docs_classify_hash;
            # consider composite index if this becomes a bottleneck.
            cur = conn.execute(
                """SELECT id FROM documents
                   WHERE (classify_content_hash IS NULL
                      OR classify_content_hash != content_hash)
                     AND (status IS NULL OR status != 'needs-review')"""
            )
            ids: list[int] = [row[0] for row in cur.fetchall()]
        return Success(ids)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"op": "find_unclassified"},
        )


def stamp_classified(doc_id: int, *, db_path: Path | None = None) -> Result[int]:
    """Mark a document as classified by setting its classify_content_hash to
    its current content_hash.

    Returns Success(rowcount) -- 1 if the row was updated, 0 if no row with
    that id exists.  In Slice A this function is built and unit-tested but
    not called on the happy path until Slice B (no AI = no successful
    classify).
    """
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                """UPDATE documents
                   SET classify_content_hash = content_hash
                   WHERE id = ?""",
                (doc_id,),
            )
            return Success(cur.rowcount)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"doc_id": doc_id, "op": "stamp_classified"},
        )


# ---------------------------------------------------------------------------
# Phase 4 -- Retry-state helpers (Slice B)
# ---------------------------------------------------------------------------


def record_classify_failure(
    doc_id: int,
    error: str,
    *,
    db_path: Path | None = None,
) -> Result[int]:
    """Increment classify_attempts and save the last error for *doc_id*.

    Returns Success(rowcount) -- 1 if updated, 0 if id not found.
    """
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                """UPDATE documents
                   SET classify_attempts = classify_attempts + 1,
                       classify_last_error = ?
                   WHERE id = ?""",
                (error, doc_id),
            )
            return Success(cur.rowcount)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"doc_id": doc_id, "op": "record_classify_failure"},
        )


def clear_classify_retry_state(
    doc_id: int,
    *,
    db_path: Path | None = None,
) -> Result[int]:
    """Reset classify_attempts to 0 and classify_last_error to NULL.

    Returns Success(rowcount) -- 1 if updated, 0 if id not found.
    """
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                """UPDATE documents
                   SET classify_attempts = 0,
                       classify_last_error = NULL
                   WHERE id = ?""",
                (doc_id,),
            )
            return Success(cur.rowcount)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"doc_id": doc_id, "op": "clear_classify_retry_state"},
        )


def park_document(
    doc_id: int,
    *,
    db_path: Path | None = None,
) -> Result[int]:
    """Set status='needs-review' on *doc_id*.  The work finder will skip it.

    NOTE: classify_attempts is NOT reset here.  If a human manually un-parks
    a document (clears status), they must also reset classify_attempts to 0
    via clear_classify_retry_state -- otherwise the next orchestrate run will
    re-park after a single failure.

    Returns Success(rowcount) -- 1 if updated, 0 if id not found.
    """
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                """UPDATE documents
                   SET status = 'needs-review'
                   WHERE id = ?""",
                (doc_id,),
            )
            return Success(cur.rowcount)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"doc_id": doc_id, "op": "park_document"},
        )


def load_classify_retry_state(
    doc_id: int,
    *,
    db_path: Path | None = None,
) -> Result[tuple[int, str | None]]:
    """Return (classify_attempts, classify_last_error) for *doc_id*.

    Returns Success((0, None)) if the row is not found (treat as first attempt).
    """
    try:
        with get_connection(db_path, readonly=True) as conn:
            row = conn.execute(
                """SELECT classify_attempts, classify_last_error
                   FROM documents WHERE id = ?""",
                (doc_id,),
            ).fetchone()
        if row is None:
            return Success((0, None))
        return Success((row[0] or 0, row[1]))
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"doc_id": doc_id, "op": "load_classify_retry_state"},
        )
