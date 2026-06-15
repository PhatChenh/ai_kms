"""Re-ranker -- Component 3 of P3 Session B Query Path.

Takes blended top candidates from the Hybrid Ranker, has a small local
cross-encoder re-score them against the exact question, and builds the
final cheap result cards with summary and metadata.

Key behaviours:
- Lazy-loads + caches a ``CrossEncoder`` at module level (same pattern as
  ``_get_model`` in ``embeddings.py``).
- Builds ``(query, candidate.snippet)`` pairs and calls ``model.predict()``.
- Fetches ``DocumentRow`` via ``get_by_path`` for each candidate.  Stale rows
  (``Success(None)``) are skipped silently (P3-SRCH-06).  Failures are skipped
  with a warning log.
- Cards carry ``summary``, ``snippet``, ``score`` (cross-encoder), and a
  ``metadata`` dict with title, project, note_type, updated_at, key_topics,
  and tags -- no full note body (cards are cheap).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from core.result import Failure, Result, Success

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchResult:
    """A cheap result card carrying summary + metadata, never the full body.

    Attributes:
        vault_path: Vault-relative path of the note.
        summary:    Short AI-generated summary, or ``None``.
        snippet:    Highlighted body snippet from the Hybrid Ranker.
        score:      Cross-encoder relevance score (higher = more relevant).
        metadata:   Dict with ``title``, ``project``, ``note_type``,
                    ``updated_at``, ``key_topics``, ``tags``.
        id:         Integer document ID from the Note Catalog, or ``None``
                    for backward compatibility.
    """

    vault_path: str
    summary: str | None
    snippet: str
    score: float
    metadata: dict
    id: int | None = None


# ---------------------------------------------------------------------------
# Module-level cached cross-encoder loader
# ---------------------------------------------------------------------------

_reranker = None


def _get_reranker():
    """Lazy-load + cache a ``CrossEncoder`` from config (C-17 compliant)."""
    global _reranker
    if _reranker is None:
        from core.config import CONFIG  # noqa: C0415  -- lazy import (C-17)
        from sentence_transformers import CrossEncoder

        model_name = CONFIG.main.search.reranker_model
        _reranker = CrossEncoder(model_name)
    return _reranker


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def rerank(
    query: str,
    candidates: list,
    db_path: Path | None = None,
) -> Result[list[SearchResult]]:
    """Re-score *candidates* against *query* with a cross-encoder, then build
    cheap result cards with summary + metadata from the Note Catalog.

    Args:
        query:      Natural-language search query.
        candidates: List of ``RankedResult`` from the Hybrid Ranker.
        db_path:    Override DB path.

    Returns:
        ``Success(list[SearchResult])`` ordered by cross-encoder score
        descending, or ``Success([])`` when *candidates* is empty.
        ``Failure`` on unexpected errors.
    """
    # ------------------------------------------------------------------
    # Early exit -- empty candidates
    # ------------------------------------------------------------------
    if len(candidates) == 0:
        return Success([])

    # ------------------------------------------------------------------
    # Lazy import (C-17 compliant)
    # ------------------------------------------------------------------
    from storage.documents import get_by_path  # noqa: C0415

    try:
        model = _get_reranker()
    except Exception as exc:
        return Failure(
            error=str(exc),
            recoverable=True,
            context={"op": "rerank", "phase": "model_load"},
        )

    # ------------------------------------------------------------------
    # 1. Cross-encoder scoring
    # ------------------------------------------------------------------
    pairs = [(query, c.snippet) for c in candidates]
    try:
        scores = model.predict(pairs)
    except Exception as exc:
        return Failure(
            error=str(exc),
            recoverable=True,
            context={"op": "rerank", "phase": "predict"},
        )

    # predict() can return a float for a single pair or a list for multiple.
    # Normalise to a list.
    if not hasattr(scores, "__len__"):
        scores = [scores]
    scores_list: list[float] = [float(s) for s in scores]

    # ------------------------------------------------------------------
    # 2. Build SearchResult cards (skip stale rows -- P3-SRCH-06)
    # ------------------------------------------------------------------
    results: list[SearchResult] = []
    for candidate, score in zip(candidates, scores_list):
        row_result = get_by_path(candidate.vault_path, db_path)
        match row_result:
            case Success(row):
                if row is None:
                    # Stale index row -- no matching documents row (P3-SRCH-06)
                    continue
            case Failure(err):
                _log.warning(
                    "rerank skipping candidate vault_path=%s error=%s",
                    candidate.vault_path,
                    err.error,
                )
                continue

        metadata: dict = {
            "title": row.title,
            "project": row.project,
            "note_type": row.note_type,
            "updated_at": row.updated_at,
            "key_topics": row.key_topics,
            "tags": row.key_topics,
        }

        results.append(
            SearchResult(
                vault_path=candidate.vault_path,
                summary=row.summary,
                snippet=candidate.snippet,
                score=score,
                metadata=metadata,
                id=row.id,
            )
        )

    # ------------------------------------------------------------------
    # 3. Sort descending by cross-encoder score
    # ------------------------------------------------------------------
    results.sort(key=lambda r: r.score, reverse=True)

    return Success(results)
