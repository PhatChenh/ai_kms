"""Hybrid Ranker -- Component 2 of P3 Session B Query Path.

Combines BM25 word search and KNN meaning search via Reciprocal Rank Fusion
to produce a single blended ranking.

Key behaviours:
- Candidate scoping: ``candidate_paths`` restricts both BM25 and KNN queries
  via SQL ``IN`` clauses.  ``None`` = global search (no IN clause).
- Empty candidates: early return ``Success([])``.
- Filtered KNN: always uses ``MATCH + k + IN`` form -- the no-MATCH form
  silently returns NULL distances (sqlite-vec v0.1.9 gotcha, ADR-0009).

Formula: ``rrf_score = 1/(RRF_K + bm25_rank) + 1/(RRF_K + knn_rank)``
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from core.result import Failure, Result, Success
from storage.db import get_connection

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

RRF_K: int = 60
"""Standard Reciprocal Rank Fusion constant.

This is a named module constant rather than a config key because it is a
well-known RRF parameter, not a tunable routing threshold (C-06 scopes to
``pipelines/`` -- this file lives in ``retrieval/``).
"""


@dataclass(frozen=True)
class RankedResult:
    """A single note with an intermediate RRF score and a body snippet.

    Attributes:
        vault_path: Vault-relative path of the note.
        rrf_score:  Reciprocal Rank Fusion score (higher = better).
        snippet:    Highlighted body text from FTS5 snippet(), or ``""`` if
                    the note appeared only in the meaning (KNN) result set.
    """

    vault_path: str
    rrf_score: float
    snippet: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def rank(
    query: str,
    candidate_paths: list[str] | None,
    max_candidates: int,
    db_path: Path | None = None,
) -> Result[list[RankedResult]]:
    """Hybrid-rank notes against *query*, optionally scoped to *candidate_paths*.

    Args:
        query:            Natural-language search query.
        candidate_paths:  List of vault paths to consider, ``None`` for
                          global search, or ``[]`` for early empty return.
        max_candidates:   Maximum number of results to return from each
                          sub-query (BM25 and KNN).
        db_path:          Override DB path.

    Returns:
        ``Success(list[RankedResult])`` -- up to *max_candidates* results
        ordered by descending RRF score, or ``Failure`` on any database or
        model error.
    """
    # ------------------------------------------------------------------
    # Early exit
    # ------------------------------------------------------------------
    if candidate_paths is not None and len(candidate_paths) == 0:
        return Success([])

    # ------------------------------------------------------------------
    # Lazy model import (C-17 compliant)
    # ------------------------------------------------------------------
    from retrieval.embeddings import _get_model

    try:
        # ------------------------------------------------------------------
        # 1. Word search (BM25 via FTS5)
        # ------------------------------------------------------------------
        bm25_results = _bm25_search(query, candidate_paths, max_candidates, db_path)

        # ------------------------------------------------------------------
        # 2. Meaning search (KNN via vec0)
        # ------------------------------------------------------------------
        try:
            model = _get_model()
            query_embedding = model.encode(query)
        except Exception as exc:
            # Model load / encode failure -- tag the phase so a caller can
            # tell an embed failure from a fusion bug during triage.
            return Failure(
                error=str(exc),
                recoverable=True,
                context={"query": query, "op": "rank", "phase": "embed"},
            )
        # Convert to raw float32 bytes for sqlite-vec (same format as index time)
        if hasattr(query_embedding, "numpy"):
            query_embedding = query_embedding.numpy()
        embedding_blob = query_embedding.astype("float32").tobytes()

        knn_results = _knn_search(
            embedding_blob, candidate_paths, max_candidates, db_path
        )

        # ------------------------------------------------------------------
        # 3. Reciprocal Rank Fusion
        # ------------------------------------------------------------------
        ranked = _fuse(bm25_results, knn_results, max_candidates)

        return Success(ranked)

    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"query": query, "op": "rank"},
        )
    except Exception as exc:
        return Failure(
            error=str(exc),
            recoverable=True,
            context={"query": query, "op": "rank"},
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bm25_search(
    query: str,
    candidate_paths: list[str] | None,
    max_candidates: int,
    db_path: Path | None,
) -> dict[str, tuple[int, str]]:
    """Return ``{vault_path: (rank, snippet), ...}`` from FTS5 BM25 search.

    Ranks are 1-based positions.  Empty dict if no matches.
    """
    params: list[str | int] = [query, max_candidates]
    sql = (
        "SELECT vault_path, bm25(notes_fts) as score, "
        "snippet(notes_fts, 3, '<mark>', '</mark>', '...', 40) as snip "
        "FROM notes_fts WHERE notes_fts MATCH ?"
    )

    if candidate_paths is not None:
        placeholders = ", ".join("?" for _ in candidate_paths)
        sql += f" AND vault_path IN ({placeholders})"
        params = [query] + candidate_paths + [max_candidates]

    sql += " ORDER BY bm25(notes_fts) ASC LIMIT ?"

    results: dict[str, tuple[int, str]] = {}
    with get_connection(db_path, readonly=True) as conn:
        rows = conn.execute(sql, params).fetchall()
        for rank, (vp, _score, snip) in enumerate(rows, start=1):
            results[vp] = (rank, snip)

    return results


def _knn_search(
    embedding_blob: bytes,
    candidate_paths: list[str] | None,
    max_candidates: int,
    db_path: Path | None,
) -> dict[str, int]:
    """Return ``{vault_path: rank, ...}`` from vec0 KNN search.

    Uses the validated ``MATCH + k + IN`` form (ADR-0009).
    Ranks are 1-based positions.  Empty dict if no matches.
    """
    params: list = [embedding_blob, max_candidates]
    sql = (
        "SELECT vault_path, distance FROM embeddings_vec "
        "WHERE embedding MATCH ? AND k = ?"
    )

    if candidate_paths is not None:
        placeholders = ", ".join("?" for _ in candidate_paths)
        sql += f" AND vault_path IN ({placeholders})"
        params = [embedding_blob, max_candidates] + candidate_paths

    results: dict[str, int] = {}
    with get_connection(db_path, readonly=True) as conn:
        rows = conn.execute(sql, params).fetchall()
        for rank, (vp, _distance) in enumerate(rows, start=1):
            results[vp] = rank

    return results


def _fuse(
    bm25: dict[str, tuple[int, str]],
    knn: dict[str, int],
    max_candidates: int,
) -> list[RankedResult]:
    """Fuse BM25 and KNN rankings into a single RRF-ordered list.

    For notes appearing in only one list, the missing rank is
    ``max_candidates + 1`` (standard RRF penalty).

    Returns up to *max_candidates* results sorted by descending RRF score.
    """
    missing_rank = max_candidates + 1
    all_paths = set(bm25.keys()) | set(knn.keys())

    scored: list[tuple[str, float, str]] = []
    for vp in all_paths:
        bm25_rank = bm25[vp][0] if vp in bm25 else missing_rank
        knn_rank = knn[vp] if vp in knn else missing_rank
        rrf = (1.0 / (RRF_K + bm25_rank)) + (1.0 / (RRF_K + knn_rank))
        snip = bm25[vp][1] if vp in bm25 else ""
        scored.append((vp, rrf, snip))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:max_candidates]

    return [
        RankedResult(vault_path=vp, rrf_score=score, snippet=snip)
        for vp, score, snip in top
    ]
