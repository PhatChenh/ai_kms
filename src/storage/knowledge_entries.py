"""Knowledge entry store — CRUD, search sync, ranking for the knowledge_entries table.

Note: this module has grown to ~500 lines covering five concerns (CRUD,
FTS sync, vec0 sync, orientation ranking, retrieval-score maintenance).
If it grows further, consider splitting into:
- knowledge_entries.py — CRUD only
- knowledge_entries_search.py — FTS + vec sync helpers
- knowledge_entries_ranking.py — orientation query + retrieval score
"""

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
    trust_score: float = 0.5
    retrieval_score: float = 0.0


def _sync_search_indexes(
    conn, entry_id: int, entity: str, fact: str, *, delete_old: bool = False
) -> None:
    """Best-effort sync of facts_fts + facts_vec for a single entry.

    If *delete_old* is True, removes existing rows before inserting.
    Embedding failure is silently swallowed — the entry is still stored
    in knowledge_entries even when search indexes cannot be populated.
    """
    embedding_result = _embed_fact(fact)
    if isinstance(embedding_result, Success):
        if delete_old:
            conn.execute("DELETE FROM facts_fts WHERE rowid = ?", (entry_id,))
            conn.execute("DELETE FROM facts_vec WHERE entry_id = ?", (entry_id,))
        blob = embedding_result.value
        conn.execute(
            "INSERT INTO facts_fts(rowid, entry_id, entity, fact) VALUES(?, ?, ?, ?)",
            (entry_id, entry_id, entity, fact),
        )
        conn.execute(
            "INSERT INTO facts_vec(entry_id, embedding) VALUES(?, ?)",
            (entry_id, blob),
        )


def _delete_search_indexes(conn, entry_id: int) -> None:
    """Remove an entry from facts_fts + facts_vec."""
    conn.execute("DELETE FROM facts_fts WHERE rowid = ?", (entry_id,))
    conn.execute("DELETE FROM facts_vec WHERE entry_id = ?", (entry_id,))


def _embed_fact(fact_text: str) -> Result[bytes]:
    """Encode *fact_text* into a float32 embedding blob.

    Lazy-imports ``_get_model`` from ``retrieval.embeddings`` and returns
    the numpy array serialized as raw bytes (same pattern as
    ``embeddings.py:index_embedding``).

    Returns ``Failure(recoverable=True)`` on any error so callers can
    treat embedding as best-effort — the fact is still stored in
    ``knowledge_entries`` even when search indexes cannot be populated.
    """
    try:
        from retrieval.embeddings import _get_model  # noqa: C0415

        model = _get_model()
        embedding = model.encode(fact_text)
        if hasattr(embedding, "numpy"):
            embedding = embedding.numpy()
        return Success(embedding.astype("float32").tobytes())
    except Exception as exc:
        return Failure(
            str(exc),
            recoverable=True,
            context={"fact_text_preview": fact_text[:100]},
        )


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
        trust_score=row["trust_score"]
        if "trust_score" in row.keys() and row["trust_score"] is not None
        else 0.5,
        retrieval_score=float(row["retrieval_count"])
        if "retrieval_count" in row.keys()
        else 0.0,
    )


def get_entry_by_id(
    entry_id: int, *, db_path: Path | None = None
) -> Result[KnowledgeEntry | None]:
    """Fetch a single knowledge entry by its integer id."""
    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM knowledge_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()
            if row is None:
                return Success(None)
            return Success(_row_to_entry(row))
    except sqlite3.Error as exc:
        return Failure(str(exc), recoverable=False, context={"entry_id": entry_id})


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
                # Read old entity+fact for search table cleanup
                old = conn.execute(
                    "SELECT entity, fact FROM knowledge_entries WHERE id = ?",
                    (entry.id,),
                ).fetchone()

                cursor = conn.execute(
                    """UPDATE knowledge_entries
                       SET dimension=?, entity=?, tag=?, fact=?, status=?,
                           confidence=?, sources=?, reasoning=?,
                           trust_score=?,
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
                        entry.trust_score,
                        entry.id,
                    ),
                )
                if cursor.rowcount == 0:
                    return Failure(
                        f"no knowledge entry with id={entry.id}",
                        recoverable=False,
                        context={"entry_id": entry.id},
                    )

                _sync_search_indexes(
                    conn,
                    entry.id,
                    entry.entity,
                    entry.fact,
                    delete_old=bool(old),
                )

                return Success(entry.id)
            else:
                cursor = conn.execute(
                    """INSERT INTO knowledge_entries
                       (dimension, entity, tag, fact, status, confidence,
                        sources, reasoning, trust_score)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry.dimension,
                        entry.entity,
                        entry.tag,
                        entry.fact,
                        status,
                        entry.confidence,
                        sources_json,
                        entry.reasoning,
                        entry.trust_score,
                    ),
                )

                _sync_search_indexes(
                    conn,
                    cursor.lastrowid,
                    entry.entity,
                    entry.fact,
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
    """Retire a knowledge entry (never delete). Returns rowcount.

    Retired entries remain in search indexes so they are still findable.
    Search consumers that should exclude retired entries do so via
    ``WHERE status != 'retired'`` in their queries.
    """
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


def query_ranked_by_dimension(
    dimension: str,
    *,
    limit: int,
    db_path: Path | None = None,
) -> Result[list[KnowledgeEntry]]:
    """Return non-retired entries for *dimension*, ranked by trust_score DESC,
    confidence DESC, updated_at DESC, capped at *limit*.

    The caller supplies the cap (typically CONFIG.classify.max_entries_per_dimension);
    this function does NOT read config itself.
    """
    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM knowledge_entries
                   WHERE status != 'retired' AND dimension = ?
                   ORDER BY trust_score DESC, confidence DESC, updated_at DESC
                   LIMIT ?""",
                (dimension, limit),
            ).fetchall()
            return Success([_row_to_entry(r) for r in rows])
    except sqlite3.Error as exc:
        return Failure(
            str(exc),
            recoverable=False,
            context={"dimension": dimension, "limit": limit},
        )


# ---------------------------------------------------------------------------
# Phase 9 — Orientation query (P9-B-06)
# ---------------------------------------------------------------------------


def query_ranked_for_orientation(
    *,
    dimension: str | None = None,
    entity: str | None = None,
    limit: int = 5,
    min_trust: float | None = None,
    db_path: Path | None = None,
) -> Result[list[KnowledgeEntry]]:
    """Return non-retired entries ranked by a 4-key sort for orientation.

    Sort order:
        1. trust_score DESC
        2. retrieval_count DESC
        3. confidence DESC
        4. updated_at DESC

    Optionally filtered by *dimension*, *entity*, and/or *min_trust*
    (Phase 10 trust-floor). Capped at *limit* (default 5).
    """
    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row

            query = "SELECT * FROM knowledge_entries WHERE status != 'retired'"
            params: list[str] = []

            if min_trust is not None:
                query += " AND trust_score >= ?"
                params.append(str(min_trust))

            if dimension is not None:
                query += " AND dimension = ?"
                params.append(dimension)
            if entity is not None:
                query += " AND entity = ?"
                params.append(entity)

            query += (
                " ORDER BY trust_score DESC, retrieval_count DESC,"
                " confidence DESC, updated_at DESC LIMIT ?"
            )
            params.append(str(limit))

            rows = conn.execute(query, params).fetchall()
            return Success([_row_to_entry(r) for r in rows])
    except sqlite3.Error as exc:
        return Failure(
            str(exc),
            recoverable=False,
            context={
                "dimension": dimension,
                "entity": entity,
                "limit": limit,
            },
        )


# ---------------------------------------------------------------------------
# Phase 9 — Retrieval score increment + sweep (Slice B)
# ---------------------------------------------------------------------------


def bump_retrieval_score(
    entry_id: int,
    *,
    decay_factor: float = 0.95,
    db_path: Path | None = None,
) -> Result[int]:
    """Increment retrieval_score for one entry.

    Formula: retrieval_count = retrieval_count * decay_factor + 1.0
    """
    try:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                "UPDATE knowledge_entries SET retrieval_count = retrieval_count * ? + 1.0 WHERE id = ?",
                (decay_factor, entry_id),
            )
            return Success(cursor.rowcount)
    except sqlite3.Error as exc:
        return Failure(str(exc), recoverable=False, context={"entry_id": entry_id})


def sweep_retrieval_scores(
    *,
    decay_factor: float = 0.95,
    db_path: Path | None = None,
) -> Result[int]:
    """Decay all retrieval scores. Returns rows affected."""
    try:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                "UPDATE knowledge_entries SET retrieval_count = retrieval_count * ?",
                (decay_factor,),
            )
            return Success(cursor.rowcount)
    except sqlite3.Error as exc:
        return Failure(str(exc), recoverable=False)


# ---------------------------------------------------------------------------
# Phase 9 — Source-prune on delete (Slice B)
# ---------------------------------------------------------------------------


def prune_sources(
    doc_id: int,
    *,
    db_path: Path | None = None,
) -> Result[int]:
    """Remove *doc_id* from the ``sources`` list of every non-retired
    knowledge entry.  If an entry is left with an empty sources list,
    its status is set to ``'pending'`` — the fact is never auto-deleted.

    Returns the number of entries touched.  OQ-P8B-01: scan-and-filter
    in Python (swappable for a JSON1 query later (SQLite ≥ 3.38 supports JSON_REMOVE)).
    """
    import json as _json

    touched = 0
    sid = str(doc_id)

    try:
        with get_connection(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, sources FROM knowledge_entries
                   WHERE status != 'retired'"""
            ).fetchall()

            for row in rows:
                sources: list[str] = (
                    _json.loads(row["sources"]) if row["sources"] else []
                )
                if sid not in sources:
                    continue

                # Remove the doc id and dedupe
                new_sources = [s for s in sources if s != sid]
                # Dedupe preserving order
                seen: set[str] = set()
                deduped: list[str] = []
                for s in new_sources:
                    if s not in seen:
                        seen.add(s)
                        deduped.append(s)

                if not deduped:
                    # Empty sources → flag pending, never delete
                    conn.execute(
                        """UPDATE knowledge_entries
                           SET sources = '[]', status = 'pending',
                               updated_at = datetime('now')
                           WHERE id = ?""",
                        (row["id"],),
                    )
                else:
                    conn.execute(
                        """UPDATE knowledge_entries
                           SET sources = ?, updated_at = datetime('now')
                           WHERE id = ?""",
                        (_json.dumps(deduped), row["id"]),
                    )
                touched += 1

        return Success(touched)
    except sqlite3.Error as exc:
        return Failure(
            str(exc),
            recoverable=False,
            context={"doc_id": doc_id, "op": "prune_sources"},
        )
