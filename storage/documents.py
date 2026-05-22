"""
storage/documents.py

Data access layer for the `documents` table.

No business logic here — pure SQL plus type conversion from WriteOutcome to row.
Callers (pipelines) call upsert() after every write_note(); indexer calls
all_paths() for diffing, then applies rename/delete_by_path as needed.

Title derivation: outcome.metadata.extra.get("title") or stem of vault_path.
Pipelines can override by setting extra["title"] before calling upsert.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from core.result import Failure, Result, Success
from storage.db import get_connection
from vault.writer import WriteOutcome


@dataclass(frozen=True)
class DocumentRow:
    """Mirrors one row in the documents table."""

    id: int
    vault_path: str
    title: str
    summary: str | None
    note_type: str | None
    confidence: float | None
    created_at: str
    updated_at: str
    updated_by_human: bool
    content_hash: str | None


def _row_from_sqlite(row: sqlite3.Row) -> DocumentRow:
    return DocumentRow(
        id=row["id"],
        vault_path=row["vault_path"],
        title=row["title"],
        summary=row["summary"],
        note_type=row["note_type"],
        confidence=row["confidence"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        updated_by_human=bool(row["updated_by_human"]),
        content_hash=row["content_hash"],
    )


def _derive_title(outcome: WriteOutcome) -> str:
    return outcome.metadata.extra.get("title") or Path(outcome.vault_path).stem


def upsert(outcome: WriteOutcome, db_path: Path | None = None) -> Result[int]:
    """
    Insert or replace a documents row from a WriteOutcome.

    Args:
        outcome:  Result of write_note or move_note.
        db_path:  Override DB path; defaults to CONFIG.main.database.path.

    Returns:
        Success(rowid) or Failure(recoverable=False) on sqlite3.Error.
    """
    title = _derive_title(outcome)
    meta = outcome.metadata
    created_at = str(meta.created) if meta.created else None

    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO documents
                    (vault_path, title, summary, note_type, confidence,
                     updated_at, updated_by_human, content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, COALESCE(?, datetime('now')))
                """,
                (
                    outcome.vault_path,
                    title,
                    meta.summary,
                    meta.type,
                    meta.confidence,
                    1 if meta.updated_by_human else 0,
                    outcome.content_hash,
                    created_at,
                ),
            )
            rowid: int = cur.lastrowid  # type: ignore[assignment]
        return Success(rowid)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"vault_path": outcome.vault_path, "op": "upsert"},
        )


def get_by_path(
    vault_path: str, db_path: Path | None = None
) -> Result[DocumentRow | None]:
    """
    Fetch the documents row for a given vault_path.

    Args:
        vault_path: POSIX-relative path as stored in the documents table.
        db_path:    Override DB path.

    Returns:
        Success(DocumentRow) if found, Success(None) if not found,
        or Failure(recoverable=False) on sqlite3.Error.
    """
    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM documents WHERE vault_path = ?", (vault_path,)
            )
            row = cur.fetchone()
        if row is None:
            return Success(None)
        return Success(_row_from_sqlite(row))
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"vault_path": vault_path, "op": "get"},
        )


def all_paths(db_path: Path | None = None) -> Result[list[tuple[str, str]]]:
    """
    Return all (vault_path, content_hash) pairs in the documents table.

    Used by indexer.detect_changes() to diff current vault state against the mirror.

    Args:
        db_path: Override DB path.

    Returns:
        Success([(vault_path, content_hash), ...]) or Failure(recoverable=False).
    """
    try:
        with get_connection(db_path, readonly=True) as conn:
            cur = conn.execute("SELECT vault_path, content_hash FROM documents")
            rows: list[tuple[str, str]] = cur.fetchall()
        return Success(rows)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"op": "all_paths"},
        )


def delete_by_path(
    vault_path: str, db_path: Path | None = None
) -> Result[int]:
    """
    Delete the documents row for vault_path.

    Args:
        vault_path: Path to delete.
        db_path:    Override DB path.

    Returns:
        Success(rows_deleted) or Failure(recoverable=False).
    """
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                "DELETE FROM documents WHERE vault_path = ?", (vault_path,)
            )
            return Success(cur.rowcount)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"vault_path": vault_path, "op": "delete"},
        )


def replace_path(
    old_vault_path: str, outcome: WriteOutcome, db_path: Path | None = None
) -> Result[None]:
    """Atomically delete the old documents row and upsert from outcome.

    Used by _store_md rename path to avoid a half-commit window where the
    old row is deleted but the new row is not yet written (or vice versa).

    Args:
        old_vault_path: vault_path of the row to remove.
        outcome:        WriteOutcome from write_note on the new path.
        db_path:        Override DB path.

    Returns:
        Success(None) or Failure(recoverable=False) on sqlite3.Error.
        On failure the transaction is rolled back — neither the delete nor
        the insert is persisted.
    """
    title = _derive_title(outcome)
    meta = outcome.metadata
    created_at = str(meta.created) if meta.created else None

    try:
        with get_connection(db_path) as conn:
            conn.execute("DELETE FROM documents WHERE vault_path = ?", (old_vault_path,))
            conn.execute(
                """
                INSERT OR REPLACE INTO documents
                    (vault_path, title, summary, note_type, confidence,
                     updated_at, updated_by_human, content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, COALESCE(?, datetime('now')))
                """,
                (
                    outcome.vault_path,
                    title,
                    meta.summary,
                    meta.type,
                    meta.confidence,
                    1 if meta.updated_by_human else 0,
                    outcome.content_hash,
                    created_at,
                ),
            )
        return Success(None)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"old_vault_path": old_vault_path, "new_vault_path": outcome.vault_path, "op": "replace_path"},
        )


def rename(old: str, new: str, db_path: Path | None = None) -> Result[int]:
    """
    Rename a vault_path in the documents table, preserving the row id.

    Used by indexer when a moved note is detected (same hash, new path).
    Preserving the id keeps FK references in audit_log and corrections valid
    without any cascade (DECISION-001).

    Args:
        old:     Current vault_path.
        new:     Target vault_path.
        db_path: Override DB path.

    Returns:
        Success(rows_updated) or Failure(recoverable=False).
    """
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                "UPDATE documents SET vault_path = ? WHERE vault_path = ?",
                (new, old),
            )
            return Success(cur.rowcount)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"old": old, "new": new, "op": "rename"},
        )
