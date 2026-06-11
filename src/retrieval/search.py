"""Search Coordinator -- Component 4 of P3 Session B Query Path.

Wires the Candidate Filter, Hybrid Ranker, and Re-ranker into a single
public entry point (``search()``) that is the stable contract for both
the CLI and the future Phase 4 MCP tool.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.result import Failure, Result, Success
from retrieval.reranker import SearchResult
from storage.documents import filter_paths, get_by_path


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _card_from_row(row, snippet: str = "", score: float = 0.0) -> SearchResult:
    """Build a ``SearchResult`` card from a ``DocumentRow``.

    Used by the filter-only branch to produce cards with the same shape
    as the re-ranker.
    """
    return SearchResult(
        vault_path=row.vault_path,
        summary=row.summary,
        snippet=snippet,
        score=score,
        metadata={
            "title": row.title,
            "project": row.project,
            "note_type": row.note_type,
            "updated_at": row.updated_at,
            "key_topics": row.key_topics,
            "tags": row.key_topics,
        },
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search(
    query: str | None = None,
    project: str | None = None,
    date_range: tuple[datetime, datetime] | tuple[datetime, None] | None = None,
    max_results: int | None = None,
    db_path: Path | None = None,
) -> Result[list[SearchResult]]:
    """Search notes by keyword, project, and/or date range.

    This is the single public entry point for all search -- used by the CLI
    now and the MCP server later.

    Args:
        query:      Natural-language search query.  ``None`` triggers
                    filter-only mode (no ranking, sorted by updated_at desc).
        project:    Optional project name to filter by.
        date_range: Optional ``(since, until)`` pair of timezone-naive
                    datetimes.  ``None`` upper bound = open-ended.
        max_results: Maximum number of results to return.  Overrides the
                    config default when given.
        db_path:    Override DB path (used in tests).

    Returns:
        ``Success(list[SearchResult])`` or ``Failure`` on error.
    """
    # An empty/whitespace query is not a ranking query -- treat it as
    # no query (filter-only mode).  Without this, ``rank("")`` errors on
    # the FTS MATCH.  Done here so both the CLI and the future MCP tool
    # share the contract.
    if query is not None and not query.strip():
        query = None

    # Lazy CONFIG import (C-17 compliant)
    from core.config import CONFIG  # noqa: C0415

    search_cfg = CONFIG.main.search
    limit = max_results if max_results is not None else search_cfg.max_results
    max_candidates = search_cfg.max_candidates

    # ------------------------------------------------------------------
    # Step 1 -- Candidate Filter
    # ------------------------------------------------------------------
    since = date_range[0] if date_range else None
    until = date_range[1] if date_range else None

    match filter_paths(project=project, since=since, until=until, db_path=db_path):
        case Failure() as f:
            return f
        case Success(None):
            candidate_paths: list[str] | None = None  # global
        case Success([]):
            return Success([])
        case Success(paths):
            candidate_paths = paths

    # ------------------------------------------------------------------
    # Step 2 -- Branch
    # ------------------------------------------------------------------

    # Filter-only branch (no query)
    if query is None:
        return _search_filter_only(candidate_paths, db_path, limit)

    # Query branch
    return _search_query(query, candidate_paths, max_candidates, db_path, limit)


# ---------------------------------------------------------------------------
# Branch helpers
# ---------------------------------------------------------------------------


def _search_filter_only(
    candidate_paths: list[str] | None,
    db_path: Path | None,
    limit: int,
) -> Result[list[SearchResult]]:
    """Filter-only mode: fetch cards from the catalog, sort by
    updated_at descending, cap at *limit*.
    """
    from storage.documents import all_paths  # noqa: C0415

    # Resolve paths: global → all_paths; scoped → given list
    if candidate_paths is None:
        match all_paths(db_path):
            case Failure() as f:
                return f
            case Success(rows):
                resolved = [r[0] for r in rows]
    else:
        resolved = candidate_paths

    cards: list[tuple[str, SearchResult]] = []
    for vp in resolved:
        match get_by_path(vp, db_path):
            case Success(row):
                if row is not None:
                    card = _card_from_row(row)
                    cards.append((row.updated_at or "", card))
            case Failure():
                continue

    # Sort by updated_at descending
    cards.sort(key=lambda x: x[0], reverse=True)

    return Success([c for _, c in cards[:limit]])


def _search_query(
    query: str,
    candidate_paths: list[str] | None,
    max_candidates: int,
    db_path: Path | None,
    limit: int,
) -> Result[list[SearchResult]]:
    """Query branch: rank → rerank → cap."""
    from retrieval.ranker import rank  # noqa: C0415
    from retrieval.reranker import rerank  # noqa: C0415

    match rank(query, candidate_paths, max_candidates, db_path=db_path):
        case Failure() as f:
            return f
        case Success(ranked):
            pass

    match rerank(query, ranked, db_path=db_path):
        case Failure() as f:
            return f
        case Success(cards):
            pass

    return Success(cards[:limit])
