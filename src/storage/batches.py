"""
storage/batches.py

Data access layer for the `batches` table.

Tracks folder drops as batches — one row per folder processed.
All files captured from a folder carry the batch's batch_id as a FK.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core.result import Failure, Result, Success
from storage.db import get_connection


def insert(
    folder_name: str,
    destination_type: str | None,
    destination_name: str | None,
    confidence: float,
    status: str,
    file_count: int,
    db_path: Path | None = None,
) -> Result[int]:
    """Insert a new batches row. Returns Success(batch_id) or Failure.

    Args:
        folder_name:      Name of the dropped folder.
        destination_type: "project", "domain", or None if unresolved.
        destination_name: Target project/domain name, or None if unresolved.
        confidence:       Routing confidence score (0.0–1.0).
        status:           Initial status, e.g. "ROUTING".
        file_count:       Number of files in the folder.
        db_path:          Override DB path; defaults to CONFIG.main.database.path.

    Returns:
        Success(batch_id) — the auto-incremented PK of the new row.
        Failure(recoverable=False) on sqlite3.Error.
    """
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO batches
                    (folder_name, destination_type, destination_name,
                     confidence, status, file_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (folder_name, destination_type, destination_name, confidence, status, file_count),
            )
            batch_id: int = cur.lastrowid  # type: ignore[assignment]
        return Success(batch_id)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"folder_name": folder_name, "op": "insert"},
        )


def update_status(
    batch_id: int,
    status: str,
    db_path: Path | None = None,
) -> Result[int]:
    """Update batches.status. Returns Success(rowcount) or Failure.

    Args:
        batch_id: PK of the row to update.
        status:   New status string.
        db_path:  Override DB path; defaults to CONFIG.main.database.path.

    Returns:
        Success(rowcount) — 1 if updated, 0 if no row matched.
        Failure(recoverable=False) on sqlite3.Error.
    """
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                "UPDATE batches SET status = ? WHERE batch_id = ?",
                (status, batch_id),
            )
            return Success(cur.rowcount)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"batch_id": batch_id, "op": "update_status"},
        )
