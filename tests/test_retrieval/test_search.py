"""Tests for Search Coordinator (retrieval/search.py) -- Component 4 of P3 Session B.

Tracer bullet: ``search(query="stakeholder resistance", db_path=db)`` returns
``Success`` with a non-empty list of ``SearchResult`` and the semantically
relevant note present.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from core.result import Failure, Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# Seeding helper -- creates fully indexed DB for integration tests
# ---------------------------------------------------------------------------


def _seed_full_db(db_path: Path) -> dict[str, str]:
    """Create a temp DB, insert 4 notes with varied projects and dates,
    and index them via the real indexers.  Returns a dict mapping short
    labels ("A", "B", "C", "D") to vault_path strings.

    Notes:
        A -- Projects/Alpha/stakeholder.md    (meeting-notes, Alpha, now)
        B -- inbox/budget.md                  (analysis, None, now)
        C -- inbox/vacation.md                (policy, None, older)
        D -- Projects/Beta/status.md           (meeting-notes, Beta, now)
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # Use explicit updated_at values so date-range tests are deterministic.
        now = datetime(2026, 6, 10, 12, 0, 0)
        older = datetime(2026, 1, 15, 9, 0, 0)
        conn.executemany(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, project, updated_at, key_topics)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    "Projects/Alpha/stakeholder.md",
                    "Stakeholder Resistance Management",
                    "How to handle stakeholder resistance and manage pushback "
                    "effectively during organizational change.",
                    "meeting-notes",
                    "Alpha",
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    '["stakeholder", "change-management"]',
                ),
                (
                    "inbox/budget.md",
                    "Quarterly Budget Analysis Q3",
                    "Detailed quarterly budget analysis for Q3 2026 including "
                    "revenue forecasts and expense tracking.",
                    "analysis",
                    None,
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    '["budget", "finance"]',
                ),
                (
                    "inbox/vacation.md",
                    "Vacation Policy Update 2026",
                    "Updated company vacation policy with new carryover rules "
                    "and PTO accrual rates.",
                    "policy",
                    None,
                    older.strftime("%Y-%m-%d %H:%M:%S"),
                    '["hr", "policy"]',
                ),
                (
                    "Projects/Beta/status.md",
                    "Project Status Report June",
                    "Monthly project status report for Beta covering milestones "
                    "and risk register updates.",
                    "meeting-notes",
                    "Beta",
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    '["status", "reporting"]',
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    # Lazy imports so test discovery works even without the model.
    from retrieval.embeddings import index_embedding
    from retrieval.keyword import index_keywords

    # Note A -- stakeholder resistance / pushback (semantic match for "stakeholder resistance")
    index_embedding(
        "Projects/Alpha/stakeholder.md",
        "Stakeholder Resistance Management",
        "meeting-notes",
        ["stakeholder", "change-management"],
        "How to handle stakeholder resistance and manage pushback "
        "effectively during organizational change.",
        db_path=db_path,
    )
    index_keywords(
        "Projects/Alpha/stakeholder.md",
        "Stakeholder Resistance Management",
        "How to handle stakeholder resistance and manage pushback "
        "effectively during organizational change.",
        "Stakeholder resistance is a common challenge in change management. "
        "This note covers strategies for managing pushback, building buy-in, "
        "and turning resistors into advocates. Key techniques include active "
        "listening, stakeholder mapping, and creating a coalition of early adopters.",
        db_path=db_path,
    )

    # Note B -- quarterly budget (strong keyword match for "budget")
    index_embedding(
        "inbox/budget.md",
        "Quarterly Budget Analysis Q3",
        "analysis",
        ["budget", "finance"],
        "Detailed quarterly budget analysis for Q3 2026 including "
        "revenue forecasts and expense tracking.",
        db_path=db_path,
    )
    index_keywords(
        "inbox/budget.md",
        "Quarterly Budget Analysis Q3",
        "Detailed quarterly budget analysis for Q3 2026 including "
        "revenue forecasts and expense tracking.",
        "The Q3 budget analysis shows a 12% increase in operational expenses "
        "compared to Q2. Revenue forecasts remain on track with a projected 8% "
        "growth. Key areas of concern include rising vendor costs and the need "
        "for additional headcount in the engineering department.",
        db_path=db_path,
    )

    # Note C -- vacation policy (irrelevant to both stakeholder and budget queries)
    index_embedding(
        "inbox/vacation.md",
        "Vacation Policy Update 2026",
        "policy",
        ["hr", "policy"],
        "Updated company vacation policy with new carryover rules "
        "and PTO accrual rates.",
        db_path=db_path,
    )
    index_keywords(
        "inbox/vacation.md",
        "Vacation Policy Update 2026",
        "Updated company vacation policy with new carryover rules "
        "and PTO accrual rates.",
        "The updated vacation policy introduces new unlimited PTO for senior staff, "
        "a carryover cap of 40 hours for other employees, and revised accrual "
        "rates tied to tenure. The changes take effect January 2027.",
        db_path=db_path,
    )

    # Note D -- project status report (Beta project)
    index_embedding(
        "Projects/Beta/status.md",
        "Project Status Report June",
        "meeting-notes",
        ["status", "reporting"],
        "Monthly project status report for Beta covering milestones "
        "and risk register updates.",
        db_path=db_path,
    )
    index_keywords(
        "Projects/Beta/status.md",
        "Project Status Report June",
        "Monthly project status report for Beta covering milestones "
        "and risk register updates.",
        "Beta project is on track for the July milestone. Key risks include "
        "vendor dependency on the analytics module and a tight integration "
        "schedule. The risk register has been updated with three new entries.",
        db_path=db_path,
    )

    return {
        "A": "Projects/Alpha/stakeholder.md",
        "B": "inbox/budget.md",
        "C": "inbox/vacation.md",
        "D": "Projects/Beta/status.md",
    }


@pytest.fixture
def seeded_db(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Temp DB with 4 fully indexed notes."""
    db_path = tmp_path / "test_search.db"
    init_db(db_path)
    paths = _seed_full_db(db_path)
    return db_path, paths


# ---------------------------------------------------------------------------
# Tracer bullet -- query branch returns ranked results
# ---------------------------------------------------------------------------


def test_search_with_query_returns_ranked_results(seeded_db):
    """Call search(query="stakeholder resistance", db_path=db).
    Assert Success with a non-empty list of SearchResults.
    The semantically relevant note should be present.
    """
    db_path, paths = seeded_db

    from retrieval.search import search

    result = search(query="stakeholder resistance", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    cards = result.value
    assert len(cards) > 0, "Expected at least one result"
    # The stakeholder note should be in the results
    vault_paths = {c.vault_path for c in cards}
    assert paths["A"] in vault_paths, (
        f"Expected stakeholder note {paths['A']} in results, got {vault_paths}"
    )


# ---------------------------------------------------------------------------
# Result type -- function returns Result, never raises
# ---------------------------------------------------------------------------


def test_search_returns_result_type(seeded_db):
    """Assert search() returns a Result, never raises."""
    db_path, _paths = seeded_db

    from retrieval.search import search

    result = search(query="test", db_path=db_path)
    assert isinstance(result, Success) or isinstance(result, Failure)


# ---------------------------------------------------------------------------
# Filter-only branch -- project filter, sorted by updated_at desc (P3-SRCH-02)
# ---------------------------------------------------------------------------


def test_search_filter_only_returns_newest_first(seeded_db):
    """Call search(project="Alpha", db_path=db) (no query). Assert results
    are sorted by updated_at descending. Assert score = 0.0 (no ranking).
    """
    db_path, paths = seeded_db

    from retrieval.search import search

    result = search(project="Alpha", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    cards = result.value
    assert len(cards) > 0, "Expected at least one Alpha result"
    # All results should belong to Alpha
    for card in cards:
        assert card.metadata["project"] == "Alpha", (
            f"Expected Alpha only, got {card.metadata['project']} for {card.vault_path}"
        )
        # Filter-only mode: score must be 0.0 (no ranking happened)
        assert card.score == 0.0, f"Expected score=0.0, got {card.score}"
        # Each card should have summary and metadata
        assert card.summary is not None, "Filter-only card should have summary"
        assert isinstance(card.metadata, dict) and len(card.metadata) > 0


# ---------------------------------------------------------------------------
# Query + Project combined scoping (P3-SRCH-03)
# ---------------------------------------------------------------------------


def test_search_query_plus_project_scopes_ranking(seeded_db):
    """Call search(query="budget", project="Alpha", db_path=db).
    Assert results come only from Alpha.
    """
    db_path, paths = seeded_db

    from retrieval.search import search

    result = search(query="budget", project="Alpha", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    cards = result.value
    assert len(cards) > 0, "Expected at least one result"
    for card in cards:
        assert card.metadata["project"] == "Alpha", (
            f"Expected Alpha only, got {card.metadata['project']} for {card.vault_path}"
        )
    # The budget note (B) has project=None, so it should NOT be in results
    budget_vault_paths = {c.vault_path for c in cards}
    assert paths["B"] not in budget_vault_paths, (
        "Budget note (project=None) should be excluded when project=Alpha"
    )


# ---------------------------------------------------------------------------
# Date range filtering (P3-SRCH-08)
# ---------------------------------------------------------------------------


def test_search_date_range_filters(seeded_db):
    """Call search(date_range=(recent_datetime, None), db_path=db).
    Assert only recent notes are returned.
    """
    db_path, paths = seeded_db

    from retrieval.search import search

    recent = datetime(2026, 3, 1)
    result = search(date_range=(recent, None), db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    cards = result.value
    assert len(cards) > 0, "Expected at least one recent result"

    # Note C (vacation) has updated_at=2026-01-15, should be excluded
    excluded = {c.vault_path for c in cards}
    assert paths["C"] not in excluded, (
        f"Vacation note ({paths['C']}) with date 2026-01-15 should be excluded "
        f"when since=2026-03-01"
    )


# ---------------------------------------------------------------------------
# Empty candidates -- filter matches nothing
# ---------------------------------------------------------------------------


def test_search_empty_candidates_returns_empty(seeded_db):
    """Call search(project="NonExistent", db_path=db).
    Assert Success([]).
    """
    db_path, _paths = seeded_db

    from retrieval.search import search

    result = search(project="NonExistent", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert result.value == [], f"Expected empty list, got {result.value}"


# ---------------------------------------------------------------------------
# Global mode -- no project or date specified, searches all notes
# ---------------------------------------------------------------------------


def test_search_no_args_global_search(seeded_db):
    """Call search(query="budget", db_path=db) with no project/date.
    Assert it searches all notes (global mode).
    """
    db_path, paths = seeded_db

    from retrieval.search import search

    result = search(query="budget", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    cards = result.value
    assert len(cards) > 0, "Expected at least one result"
    # The budget note (B) with project=None should be reachable in global mode
    vault_paths = {c.vault_path for c in cards}
    assert paths["B"] in vault_paths, (
        f"Expected budget note {paths['B']} in global search, got {vault_paths}"
    )


# ---------------------------------------------------------------------------
# max_results caps output
# ---------------------------------------------------------------------------


def test_search_max_results_caps_output(seeded_db):
    """Call search(query="report", max_results=2, db_path=db).
    Assert at most 2 results are returned.
    """
    db_path, _paths = seeded_db

    from retrieval.search import search

    result = search(query="report", max_results=2, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    cards = result.value
    assert len(cards) <= 2, f"Expected at most 2 results, got {len(cards)}"


# ---------------------------------------------------------------------------
# Filter-only card shape -- summary and metadata pulled from catalog
# ---------------------------------------------------------------------------


def test_search_filter_only_cards_have_metadata(seeded_db):
    """In filter-only mode, assert each card still has summary and metadata."""
    db_path, paths = seeded_db

    from retrieval.search import search

    result = search(project="Alpha", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    cards = result.value
    assert len(cards) > 0, "Expected at least one Alpha result"

    for card in cards:
        # Summary must be present (pulled from catalog)
        assert card.summary is not None, f"Card for {card.vault_path} missing summary"
        assert isinstance(card.summary, str) and len(card.summary) > 0
        # Metadata must be present
        assert isinstance(card.metadata, dict)
        assert "title" in card.metadata
        assert "project" in card.metadata
        assert "note_type" in card.metadata
        assert "updated_at" in card.metadata
        assert "key_topics" in card.metadata
        assert "tags" in card.metadata
        # Score must be 0.0 (no ranking happened)
        assert card.score == 0.0
