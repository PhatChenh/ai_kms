"""kms_comment backing — add comments to knowledge entries."""

from __future__ import annotations

from pathlib import Path

from core.result import Failure, Result, Success


def add_comment(
    entry_id: int,
    text: str,
    *,
    db_path: Path | None = None,
) -> Result[dict]:
    """Add a comment to a knowledge entry.

    Validates entry exists, inserts into entry_comments table.
    Returns Success({"comment_id": ..., "entry_id": ...}).
    """
    import sqlite3

    from storage.db import get_connection
    from storage.knowledge_entries import get_entry_by_id

    match get_entry_by_id(entry_id, db_path=db_path):
        case Success(value=None):
            return Failure(f"Entry {entry_id} not found", recoverable=False)
        case Failure() as f:
            return f
        case Success():
            pass

    try:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO entry_comments (entry_id, comment_text) VALUES (?, ?)",
                (entry_id, text),
            )
            return Success({"comment_id": cursor.lastrowid, "entry_id": entry_id})
    except sqlite3.Error as exc:
        return Failure(str(exc), recoverable=False, context={"entry_id": entry_id})


def get_comments(
    entry_id: int,
    *,
    db_path: Path | None = None,
) -> Result[list[dict]]:
    """Retrieve all comments for a knowledge entry."""
    import sqlite3

    from storage.db import get_connection

    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, comment_text, created_at FROM entry_comments WHERE entry_id = ? ORDER BY created_at",
                (entry_id,),
            ).fetchall()
            return Success([
                {"comment_id": r["id"], "text": r["comment_text"], "created_at": r["created_at"]}
                for r in rows
            ])
    except sqlite3.Error as exc:
        return Failure(str(exc), recoverable=False, context={"entry_id": entry_id})
