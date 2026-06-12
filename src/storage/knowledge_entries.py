"""Knowledge entry store — CRUD for the knowledge_entries table."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from core.config import ConfidenceBand
from core.result import Failure, Result, Success
from core.tags import confidence_to_status
from storage.db import get_connection


@dataclass
class KnowledgeEntry:
    """One atomic fact about one entity in one dimension."""

    id: int | None = None
    dimension: str = ""
    entity: str = ""
    tag: str = ""
    fact: str = ""
    status: str = ""
    confidence: float | None = None
    sources: list[str] = field(default_factory=list)
    reasoning: str = ""
    created_at: str = ""
    updated_at: str = ""


def _row_to_entry(row: sqlite3.Row) -> KnowledgeEntry:
    """Convert a sqlite3.Row to a KnowledgeEntry."""
    return KnowledgeEntry(
        id=row["id"],
        dimension=row["dimension"],
        entity=row["entity"],
        tag=row["tag"],
        fact=row["fact"],
        status=row["status"],
        confidence=row["confidence"],
        sources=json.loads(row["sources"]) if row["sources"] else [],
        reasoning=row["reasoning"] or "",
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
    )


def upsert(
    entry: KnowledgeEntry,
    *,
    status: str | None = None,
    band: ConfidenceBand | None = None,
    db_path: Path | None = None,
) -> Result[int]:
    """Insert or update a knowledge entry. Returns Success(new row id).

    If status is not provided and a band is given, derives status from
    entry.confidence via confidence_to_status. Otherwise uses status as-is.
    """
    if status is None and band is not None and entry.confidence is not None:
        status = confidence_to_status(entry.confidence, band)
    if status is None:
        status = entry.status or "pending"

    sources_json = json.dumps(entry.sources)

    try:
        with get_connection(db_path) as conn:
            if entry.id is not None:
                cursor = conn.execute(
                    """UPDATE knowledge_entries
                       SET dimension=?, entity=?, tag=?, fact=?, status=?,
                           confidence=?, sources=?, reasoning=?,
                           updated_at=datetime('now')
                       WHERE id=?""",
                    (
                        entry.dimension,
                        entry.entity,
                        entry.tag,
                        entry.fact,
                        status,
                        entry.confidence,
                        sources_json,
                        entry.reasoning,
                        entry.id,
                    ),
                )
                if cursor.rowcount == 0:
                    return Failure(
                        f"no knowledge entry with id={entry.id}",
                        recoverable=False,
                        context={"entry_id": entry.id},
                    )
                return Success(entry.id)
            else:
                cursor = conn.execute(
                    """INSERT INTO knowledge_entries
                       (dimension, entity, tag, fact, status, confidence,
                        sources, reasoning)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry.dimension,
                        entry.entity,
                        entry.tag,
                        entry.fact,
                        status,
                        entry.confidence,
                        sources_json,
                        entry.reasoning,
                    ),
                )
                return Success(cursor.lastrowid)
    except sqlite3.Error as exc:
        return Failure(
            str(exc),
            recoverable=False,
            context={"dimension": entry.dimension, "entity": entry.entity},
        )


def query_by_dimension(
    dimension: str, *, db_path: Path | None = None
) -> Result[list[KnowledgeEntry]]:
    """Return all knowledge entries for a given dimension."""
    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM knowledge_entries WHERE dimension=? ORDER BY entity, tag",
                (dimension,),
            ).fetchall()
            return Success([_row_to_entry(r) for r in rows])
    except sqlite3.Error as exc:
        return Failure(str(exc), recoverable=False, context={"dimension": dimension})


def query_by_entity(
    entity: str, *, db_path: Path | None = None
) -> Result[list[KnowledgeEntry]]:
    """Return all knowledge entries for a given entity."""
    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM knowledge_entries WHERE entity=? ORDER BY dimension, tag",
                (entity,),
            ).fetchall()
            return Success([_row_to_entry(r) for r in rows])
    except sqlite3.Error as exc:
        return Failure(str(exc), recoverable=False, context={"entity": entity})


def retire(entry_id: int, reason: str, *, db_path: Path | None = None) -> Result[int]:
    """Retire a knowledge entry (never delete). Returns rowcount."""
    try:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                """UPDATE knowledge_entries
                   SET status='retired', reasoning=?,
                       updated_at=datetime('now')
                   WHERE id=?""",
                (reason, entry_id),
            )
            return Success(cursor.rowcount)
    except sqlite3.Error as exc:
        return Failure(str(exc), recoverable=False, context={"entry_id": entry_id})


def get_confident_and_pending(
    *,
    entity: str | None = None,
    dimension: str | None = None,
    db_path: Path | None = None,
) -> Result[list[KnowledgeEntry]]:
    """Return all non-retired entries, optionally filtered by entity/dimension."""
    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM knowledge_entries WHERE status != 'retired'"
            params: list[str] = []

            if entity is not None:
                query += " AND entity = ?"
                params.append(entity)
            if dimension is not None:
                query += " AND dimension = ?"
                params.append(dimension)

            query += " ORDER BY dimension, entity, tag"
            rows = conn.execute(query, params).fetchall()
            return Success([_row_to_entry(r) for r in rows])
    except sqlite3.Error as exc:
        return Failure(
            str(exc),
            recoverable=False,
            context={"entity": entity, "dimension": dimension},
        )
