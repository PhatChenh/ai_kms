"""Fact Search — Dual-Modal search over the knowledge_entries corpus.

Combines FTS5 keyword search on ``facts_fts`` with semantic (KNN) search on
``facts_vec`` via Reciprocal Rank Fusion, then joins back to
``knowledge_entries`` for full row data.

Key behaviours:
- Weighted RRF fusion: *keyword_weight* controls the contribution of the
  keyword (FTS5) leg vs. the semantic (vec0) leg.
- Missing rank penalty: ``max_candidates + 1`` (standard RRF).
- Identity dedup: when the same ``entry_id`` appears in both keyword and
  semantic results, it is fused into a single result instead of duplicated.
- Only non-retired entries are returned (filtered via the JOIN).

Research spike note (Phase 9 P9-B-06):
  With the ``greennode-embedding-large-1007`` model (1024-dim) and a small set of
  short facts (~10-20 tokens each), cosine distances between close neighbours
  were in the range 0.15–0.40 and distances between unrelated facts were
  0.60–0.90, giving clean separation.  The default ``keyword_weight=0.5``
  (equal fusion) works well and was kept.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from core.result import Failure, Result, Success
from storage.db import get_connection

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RRF_K: int = 60
"""Reciprocal Rank Fusion constant — same value as ``retrieval/ranker.py``."""

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactResult:
    """A single ranked fact from the knowledge_entries corpus.

    Attributes:
        entry_id:         Primary key in ``knowledge_entries``.
        dimension:        The dimension (e.g. "people", "process").
        entity:           The entity name.
        fact:             The fact text.
        confidence:       Confidence value from the classification pipeline.
        trust_score:      Inert trust score (DB default 0.5).
        retrieval_score:  Retrieval count (decayed increment).
        sources:          JSON array string of source document IDs.
        score:            Weighted RRF fusion score (higher = better).
    """

    entry_id: int
    dimension: str
    entity: str
    fact: str
    confidence: str
    trust_score: float
    retrieval_score: float
    sources: str
    score: float


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_facts(
    query: str,
    *,
    max_results: int | None = None,
    keyword_weight: float | None = None,
    db_path: Path | None = None,
) -> Result[list[FactResult]]:
    """Search the knowledge_entries corpus with dual-modal RRF fusion.

    Args:
        query:          Natural-language search query.
        max_results:    Maximum number of results to return from each
                        sub-query (keyword and semantic) before fusion.
        keyword_weight: Weight for the keyword (FTS5) leg in the weighted
                        RRF score.  1.0 = keyword-only, 0.0 = semantic-only.
                        None reads from config.
        db_path:        Override DB path.

    Returns:
        ``Success(list[FactResult])`` ordered by descending fusion score,
        or ``Failure`` on any database or model error.
    """
    # ------------------------------------------------------------------
    # Read defaults from config when not explicitly provided.
    if max_results is None or keyword_weight is None:
        try:
            from core.config import CONFIG

            fs_cfg = CONFIG.main.mcp.fact_search
            if max_results is None:
                max_results = fs_cfg.max_results
            if keyword_weight is None:
                keyword_weight = fs_cfg.keyword_weight
        except Exception:
            pass
    if max_results is None:
        max_results = 20
    if keyword_weight is None:
        keyword_weight = 0.5
    # 1. Keyword search (FTS5)
    # ------------------------------------------------------------------
    keyword_ranks = _fts5_search(query, max_results, db_path)

    # ------------------------------------------------------------------
    # 2. Semantic search (vec0 KNN)
    # ------------------------------------------------------------------
    try:
        from retrieval.embeddings import _get_model  # noqa: C0415

        model = _get_model()
        query_embedding = model.encode(query)
    except Exception as exc:
        return Failure(
            error=str(exc),
            recoverable=True,
            context={"query": query, "op": "search_facts", "phase": "embed"},
        )

    if hasattr(query_embedding, "numpy"):
        query_embedding = query_embedding.numpy()
    embedding_blob = query_embedding.astype("float32").tobytes()

    try:
        semantic_ranks = _vec0_search(embedding_blob, max_results, db_path)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"query": query, "op": "search_facts", "phase": "vec0"},
        )

    # ------------------------------------------------------------------
    # 3. Weighted RRF fusion
    # ------------------------------------------------------------------
    fused = _fuse_facts(keyword_ranks, semantic_ranks, max_results, keyword_weight)

    # ------------------------------------------------------------------
    # 4. Join back to knowledge_entries for full row data
    # ------------------------------------------------------------------
    if not fused:
        return Success([])

    return _join_entries(fused, db_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fts5_search(
    query: str,
    max_results: int,
    db_path: Path | None,
) -> dict[int, int]:
    """Return ``{entry_id: rank, ...}`` from FTS5 keyword search.

    Ranks are 1-based positions.  Empty dict if no matches.
    """
    results: dict[int, int] = {}
    try:
        with get_connection(db_path, readonly=True) as conn:
            rows = conn.execute(
                "SELECT rowid, rank FROM facts_fts WHERE facts_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (query, max_results),
            ).fetchall()
            for rank, (rowid, _rank_value) in enumerate(rows, start=1):
                results[rowid] = rank
    except sqlite3.Error:
        # FTS5 may error on malformed queries — treat as empty
        pass

    return results


def _vec0_search(
    embedding_blob: bytes,
    max_results: int,
    db_path: Path | None,
) -> dict[int, int]:
    """Return ``{entry_id: rank, ...}`` from vec0 KNN semantic search.

    Uses the validated ``MATCH + k`` form (ADR-0009).
    Ranks are 1-based positions by ascending distance.  Empty dict if no matches.
    """
    results: dict[int, int] = {}
    try:
        with get_connection(db_path, readonly=True) as conn:
            rows = conn.execute(
                "SELECT entry_id, distance FROM facts_vec "
                "WHERE embedding MATCH ? AND k = ?",
                (embedding_blob, max_results),
            ).fetchall()
            for rank, (entry_id, _distance) in enumerate(rows, start=1):
                results[entry_id] = rank
    except sqlite3.Error:
        pass

    return results


def _fuse_facts(
    keyword_ranks: dict[int, int],
    semantic_ranks: dict[int, int],
    max_results: int,
    keyword_weight: float,
) -> list[tuple[int, float]]:
    """Weighted RRF fusion of keyword and semantic rankings.

    Returns ``[(entry_id, fusion_score), ...]`` sorted by descending score,
    capped at *max_results*.
    """
    # Standard RRF would use the actual max rank from each system rather
    # than the requested limit.  We use *max_results* + 1 to match the
    # pattern in ``ranker.py``.  This slightly over-penalises items from
    # the smaller result set, but the practical impact is negligible.
    missing_rank = max_results + 1
    semantic_weight = 1.0 - keyword_weight
    all_ids = set(keyword_ranks.keys()) | set(semantic_ranks.keys())

    scored: list[tuple[int, float]] = []
    for entry_id in all_ids:
        kw_rank = keyword_ranks.get(entry_id, missing_rank)
        sem_rank = semantic_ranks.get(entry_id, missing_rank)

        kw_score = 1.0 / (RRF_K + kw_rank)
        sem_score = 1.0 / (RRF_K + sem_rank)
        rrf = keyword_weight * kw_score + semantic_weight * sem_score

        scored.append((entry_id, rrf))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:max_results]


def _join_entries(
    fused: list[tuple[int, float]],
    db_path: Path | None,
) -> Result[list[FactResult]]:
    """Join fused (entry_id, score) pairs back to ``knowledge_entries``.

    Only non-retired entries are returned.  Entries that cannot be found
    (e.g. retired between search and join) are silently skipped.
    """
    entry_ids = [eid for eid, _ in fused]
    id_to_score = dict(fused)

    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            placeholders = ", ".join("?" for _ in entry_ids)
            rows = conn.execute(
                f"""SELECT id, dimension, entity, fact, confidence,
                           trust_score, retrieval_count, sources
                    FROM knowledge_entries
                    WHERE id IN ({placeholders})
                      AND status != 'retired'""",
                entry_ids,
            ).fetchall()
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"op": "search_facts", "phase": "join"},
        )

    results: list[FactResult] = []
    for row in rows:
        eid = row["id"]
        results.append(
            FactResult(
                entry_id=eid,
                dimension=row["dimension"] or "",
                entity=row["entity"] or "",
                fact=row["fact"] or "",
                confidence=str(row["confidence"])
                if row["confidence"] is not None
                else "",
                trust_score=float(row["trust_score"])
                if row["trust_score"] is not None
                else 0.5,
                retrieval_score=float(row["retrieval_count"])
                if row["retrieval_count"] is not None
                else 0.0,
                sources=row["sources"] or "[]",
                score=id_to_score[eid],
            )
        )

    # Preserve fusion order
    results.sort(key=lambda r: r.score, reverse=True)
    return Success(results)
