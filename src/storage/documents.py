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

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
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
    batch_id: int | None = None
    project: str | None = None
    status: str | None = None
    key_topics: list[str] = field(default_factory=list)
    full_body: str | None = None
    original_filename: str | None = None
    file_size_bytes: int | None = None


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
        batch_id=row["batch_id"] if "batch_id" in row.keys() else None,
        project=row["project"] if "project" in row.keys() else None,
        status=row["status"] if "status" in row.keys() else None,
        key_topics=(
            json.loads(row["key_topics"])
            if "key_topics" in row.keys() and row["key_topics"]
            else []
        ),
        full_body=row["full_body"] if "full_body" in row.keys() else None,
        original_filename=row["original_filename"]
        if "original_filename" in row.keys()
        else None,
        file_size_bytes=row["file_size_bytes"]
        if "file_size_bytes" in row.keys()
        else None,
    )


def _derive_title(outcome: WriteOutcome) -> str:
    return (
        outcome.metadata.title
        or outcome.metadata.extra.get("title")
        or Path(outcome.vault_path).stem
    )


def _derive_key_topics(tags: list[str]) -> str:
    """Serialize topic tags to a JSON string for the key_topics column.

    Excludes structural tags (domain/ and type/ prefixes) — those are stored
    in dedicated columns / derived elsewhere. Single source of truth for the
    INSERT OR REPLACE paths in both upsert() and replace_path().
    """
    return json.dumps(
        [t for t in tags if not t.startswith("domain/") and not t.startswith("type/")]
    )


def upsert(
    outcome: WriteOutcome,
    db_path: Path | None = None,
    batch_id: int | None = None,
) -> Result[int]:
    """
    Insert or replace a documents row from a WriteOutcome.

    Args:
        outcome:  Result of write_note or move_note.
        db_path:  Override DB path; defaults to CONFIG.main.database.path.
        batch_id: FK to batches.batch_id for folder-batch tracking. None → column stays NULL.

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
                     updated_at, updated_by_human, content_hash, created_at,
                     batch_id, project, status, key_topics)
                VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, COALESCE(?, datetime('now')),
                        ?, ?, ?, ?)
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
                    batch_id,
                    meta.project,
                    meta.status,
                    _derive_key_topics(meta.tags),
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


def upsert_from_upload(
    vault_path: str,
    extracted_text: str,
    content_hash: str,
    original_filename: str | None = None,
    file_size_bytes: int | None = None,
    title: str | None = None,
    db_path: Path | None = None,
) -> Result[int]:
    """Save-or-update one uploaded file's record, deciding by content fingerprint.

    Decision inside one ``get_connection(db_path)`` transaction:

    * No existing row for *vault_path* → INSERT, return new ``id``.
    * Same ``content_hash``            → no write, return existing ``id``.
    * Different ``content_hash``       → UPDATE in place, return same ``id``.

    Args:
        vault_path:        POSIX-relative path for the documents row.
        extracted_text:    Full extracted text to store in ``full_body``.
        content_hash:      Content fingerprint for dedup / change detection.
        original_filename: Original upload filename (optional).
        file_size_bytes:   File size in bytes (optional).
        title:             Optional override; defaults to ``Path(vault_path).stem``
                           on INSERT, keeps existing title on UPDATE unless provided.
        db_path:           Override DB path.

    Returns:
        ``Success(row_id)`` or ``Failure(recoverable=False)`` on ``sqlite3.Error``.
    """
    try:
        with get_connection(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, content_hash, title FROM documents WHERE vault_path = ?",
                (vault_path,),
            ).fetchone()

            if row is None:
                # ── INSERT ──────────────────────────────────────────────
                resolved_title = title or Path(vault_path).stem
                cur = conn.execute(
                    """
                    INSERT INTO documents
                        (vault_path, title, full_body, original_filename,
                         file_size_bytes, content_hash, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        vault_path,
                        resolved_title,
                        extracted_text,
                        original_filename,
                        file_size_bytes,
                        content_hash,
                    ),
                )
                return Success(cur.lastrowid)

            existing_id: int = row["id"]
            existing_hash: str | None = row["content_hash"]

            if existing_hash == content_hash:
                # ── SKIP — identical content ────────────────────────────
                return Success(existing_id)

            # ── UPDATE — content changed ────────────────────────────────
            resolved_title = title if title is not None else row["title"]
            conn.execute(
                """
                UPDATE documents
                SET full_body = ?,
                    original_filename = ?,
                    file_size_bytes = ?,
                    content_hash = ?,
                    title = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    extracted_text,
                    original_filename,
                    file_size_bytes,
                    content_hash,
                    resolved_title,
                    existing_id,
                ),
            )
            return Success(existing_id)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"vault_path": vault_path, "op": "upsert_from_upload"},
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


def delete_by_path(vault_path: str, db_path: Path | None = None) -> Result[int]:
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
            conn.execute(
                "DELETE FROM embeddings_vec WHERE vault_path = ?", (vault_path,)
            )
            conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (vault_path,))
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
    old_vault_path: str,
    outcome: WriteOutcome,
    db_path: Path | None = None,
    batch_id: int | None = None,
) -> Result[None]:
    """Atomically delete the old documents row and upsert from outcome.

    Used by _store_md rename path to avoid a half-commit window where the
    old row is deleted but the new row is not yet written (or vice versa).

    Args:
        old_vault_path: vault_path of the row to remove.
        outcome:        WriteOutcome from write_note on the new path.
        db_path:        Override DB path.
        batch_id:       FK to batches.batch_id. None → column stays NULL.

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
            conn.execute(
                "DELETE FROM embeddings_vec WHERE vault_path = ?", (old_vault_path,)
            )
            conn.execute(
                "DELETE FROM notes_fts WHERE vault_path = ?", (old_vault_path,)
            )
            conn.execute(
                "DELETE FROM documents WHERE vault_path = ?", (old_vault_path,)
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO documents
                    (vault_path, title, summary, note_type, confidence,
                     updated_at, updated_by_human, content_hash, created_at,
                     batch_id, project, status, key_topics)
                VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, COALESCE(?, datetime('now')),
                        ?, ?, ?, ?)
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
                    batch_id,
                    meta.project,
                    meta.status,
                    _derive_key_topics(meta.tags),
                ),
            )
        return Success(None)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={
                "old_vault_path": old_vault_path,
                "new_vault_path": outcome.vault_path,
                "op": "replace_path",
            },
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
            # vec0: copy embedding, delete old, insert new (PK update not supported)
            row = conn.execute(
                "SELECT embedding FROM embeddings_vec WHERE vault_path = ?", (old,)
            ).fetchone()
            if row:
                conn.execute("DELETE FROM embeddings_vec WHERE vault_path = ?", (old,))
                conn.execute(
                    "INSERT INTO embeddings_vec(vault_path, embedding) VALUES (?, ?)",
                    (new, row[0]),
                )
            # FTS5: copy content, delete old, insert new
            fts_row = conn.execute(
                "SELECT title, summary, body FROM notes_fts WHERE vault_path = ?",
                (old,),
            ).fetchone()
            if fts_row:
                conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (old,))
                conn.execute(
                    "INSERT INTO notes_fts(vault_path, title, summary, body) "
                    "VALUES (?, ?, ?, ?)",
                    (new, *fts_row),
                )
            return Success(cur.rowcount)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"old": old, "new": new, "op": "rename"},
        )


def update_batch_id(
    vault_path: str,
    batch_id: int,
    db_path: Path | None = None,
) -> Result[int]:
    """Set batch_id on the documents row for vault_path.

    Used by the watcher when a file moves into a batch-worthy subfolder,
    to avoid a full re-capture (deterministic path math, not an AI decision).

    Args:
        vault_path: POSIX vault-relative path of the document to update.
        batch_id:   FK to batches.id to stamp on the row.
        db_path:    Override DB path.

    Returns:
        Success(rowcount) — 1 if updated, 0 if no row matched.
        Failure(recoverable=False) on sqlite3.Error.
    """
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                "UPDATE documents SET batch_id = ? WHERE vault_path = ?",
                (batch_id, vault_path),
            )
            return Success(cur.rowcount)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"vault_path": vault_path, "op": "update_batch_id"},
        )


def filter_paths(
    project: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    location: str | None = None,
    db_path: Path | None = None,
) -> Result[list[str] | None]:
    """Return the set of vault_paths matching optional project, date, and/or
    location filters.

    If *project*, *since*, *until*, and *location* are all None, returns
    ``Success(None)`` — the sentinel value that tells callers "all notes; do
    not build an IN clause."  This avoids SQLite variable limits on large
    vaults.

    Args:
        project:  Optional project name to filter by (exact match on ``project``
                  column).
        since:    Optional inclusive lower bound on ``updated_at``.
        until:    Optional inclusive upper bound on ``updated_at``.
        location: Optional folder prefix to filter by (GLOB match on
                  ``vault_path``, e.g. ``"inbox"`` matches ``inbox/*``).
        db_path:  Override DB path.

    Returns:
        ``Success(list_of_vault_paths)`` when a filter is applied (empty list
        if no rows match), ``Success(None)`` when no filters are given
        (all-notes sentinel), or ``Failure(recoverable=False)`` on
        sqlite3.Error.
    """
    if project is None and since is None and until is None and location is None:
        return Success(None)

    clauses: list[str] = []
    params: list[str] = []

    if project is not None:
        clauses.append("project = ?")
        params.append(project)

    if since is not None:
        clauses.append("updated_at >= ?")
        params.append(since.strftime("%Y-%m-%d %H:%M:%S"))

    if until is not None:
        clauses.append("updated_at <= ?")
        params.append(until.strftime("%Y-%m-%d %H:%M:%S"))

    if location is not None:
        clauses.append("vault_path GLOB ?")
        params.append(f"{location}/*")

    sql = f"SELECT vault_path FROM documents WHERE {' AND '.join(clauses)}"

    try:
        with get_connection(db_path, readonly=True) as conn:
            cur = conn.execute(sql, params)
            result: list[str] = [row[0] for row in cur.fetchall()]
        return Success(result)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"project": project, "op": "filter_paths"},
        )
