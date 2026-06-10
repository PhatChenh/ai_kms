"""Tests for Hybrid Ranker (retrieval/ranker.py) -- Component 2 of P3 Session B.

Tracer bullet: ``rank("stakeholder resistance", candidates=[A,B,C])`` returns
a non-empty list with the semantically relevant note ranked at or above the
irrelevant note.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.result import Failure, Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_db(db_path: Path) -> dict[str, str]:
    """Create a temp DB, insert 3 notes into documents, and index them via
    the real ``index_embedding()`` and ``index_keywords()`` functions.

    Returns a dict mapping short labels ("A", "B", "C") to vault_path strings.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executemany(
            """INSERT INTO documents (vault_path, title, summary, note_type, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            [
                (
                    "Projects/Alpha/stakeholder.md",
                    "Stakeholder Resistance Management",
                    "How to handle stakeholder resistance and manage pushback "
                    "effectively during organizational change.",
                    "meeting-notes",
                ),
                (
                    "inbox/budget.md",
                    "Quarterly Budget Analysis Q3",
                    "Detailed quarterly budget analysis for Q3 2026 including "
                    "revenue forecasts and expense tracking.",
                    "analysis",
                ),
                (
                    "inbox/vacation.md",
                    "Vacation Policy Update 2026",
                    "Updated company vacation policy with new carryover rules "
                    "and PTO accrual rates.",
                    "policy",
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    # Lazy imports so the module can be imported even if the real model
    # isn't available (test discovery will still work).
    from retrieval.embeddings import index_embedding
    from retrieval.keyword import index_keywords

    # Note A -- stakeholder resistance / pushback (the semantic match)
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
        "The updated vacation policy introduces unlimited PTO for senior staff "
        "and a new carryover limit of 40 hours for all other employees. The "
        "policy takes effect on July 1, 2026. Employees must submit vacation "
        "requests at least two weeks in advance.",
        db_path=db_path,
    )

    return {
        "A": "Projects/Alpha/stakeholder.md",
        "B": "inbox/budget.md",
        "C": "inbox/vacation.md",
    }


def _seed_db_for_partition_test(db_path: Path) -> tuple[str, str]:
    """Seed two notes for the filtered-KNN partition regression test.

    Returns ``(far_but_in_set, near_but_out_of_set)`` -- both are
    vault_path strings.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executemany(
            """INSERT INTO documents (vault_path, title, summary, note_type, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            [
                (
                    "inbox/vacation.md",
                    "Vacation Policy",
                    "Updates to the vacation policy for all employees.",
                    "policy",
                ),
                (
                    "Projects/Alpha/budget.md",
                    "Budget Report",
                    "Q3 budget analysis with revenue forecasts.",
                    "analysis",
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    from retrieval.embeddings import index_embedding
    from retrieval.keyword import index_keywords

    # far-but-in-set: vacation policy (distractor for budget query)
    index_embedding(
        "inbox/vacation.md",
        "Vacation Policy",
        "policy",
        ["hr"],
        "Updates to the vacation policy for all employees.",
        db_path=db_path,
    )
    index_keywords(
        "inbox/vacation.md",
        "Vacation Policy",
        "Updates to the vacation policy for all employees.",
        "All employees will now accrue 15 days of PTO per year.",
        db_path=db_path,
    )

    # near-but-out-of-set: budget report (strong semantic match for "budget")
    index_embedding(
        "Projects/Alpha/budget.md",
        "Budget Report",
        "analysis",
        ["budget"],
        "Q3 budget analysis with revenue forecasts.",
        db_path=db_path,
    )
    index_keywords(
        "Projects/Alpha/budget.md",
        "Budget Report",
        "Q3 budget analysis with revenue forecasts.",
        "The quarterly budget report indicates strong revenue growth in Q3.",
        db_path=db_path,
    )

    return "inbox/vacation.md", "Projects/Alpha/budget.md"


@pytest.fixture
def seeded_db(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Temp DB with 3 seeded + indexed notes."""
    db_path = tmp_path / "test_ranker.db"
    init_db(db_path)
    paths = _seed_db(db_path)
    return db_path, paths


# ---------------------------------------------------------------------------
# Tracer bullet -- semantically relevant note ranks first (P3-SRCH-01)
# ---------------------------------------------------------------------------


def test_rank_returns_semantically_relevant_note_first(seeded_db):
    """Call rank("stakeholder resistance", candidates=[A,B,C]).
    Note A (about stakeholder resistance) must be present with an RRF score
    >= the score of Note C (vacation policy -- irrelevant).
    """
    db_path, paths = seeded_db

    from retrieval.ranker import rank

    result = rank(
        "stakeholder resistance",
        candidate_paths=[paths["A"], paths["B"], paths["C"]],
        max_candidates=3,
        db_path=db_path,
    )

    assert isinstance(result, Success), f"Expected Success, got {result}"
    scored = result.value
    assert len(scored) >= 1, "Expected at least one ranked result"

    # Collect scores by vault_path
    scores = {r.vault_path: r.rrf_score for r in scored}
    assert paths["A"] in scores, "Note A (stakeholder) should be in results"
    assert paths["C"] in scores, "Note C (vacation) should be in results"
    assert scores[paths["A"]] >= scores[paths["C"]], (
        f"Note A RRF ({scores[paths['A']]}) should be >= "
        f"Note C RRF ({scores[paths['C']]})"
    )


# ---------------------------------------------------------------------------
# Candidate scoping
# ---------------------------------------------------------------------------


def test_rank_scoped_to_candidates_only(seeded_db):
    """Rank "budget" with candidates=[A, C]. Note B (budget note) is NOT in
    the candidate set and must be excluded, even though it matches best.
    """
    db_path, paths = seeded_db

    from retrieval.ranker import rank

    result = rank(
        "budget",
        candidate_paths=[paths["A"], paths["C"]],
        max_candidates=3,
        db_path=db_path,
    )

    assert isinstance(result, Success), f"Expected Success, got {result}"
    vault_paths_in_result = {r.vault_path for r in result.value}
    assert paths["B"] not in vault_paths_in_result, (
        "Note B (budget note) was excluded from candidates but appeared in results"
    )
    assert len(result.value) >= 1, "Expected at least one result"


def test_rank_global_mode_when_candidates_none(seeded_db):
    """When candidates=None, the search is global (no IN clause).
    Note B (budget) MUST be present.
    """
    db_path, paths = seeded_db

    from retrieval.ranker import rank

    result = rank(
        "budget",
        candidate_paths=None,
        max_candidates=3,
        db_path=db_path,
    )

    assert isinstance(result, Success), f"Expected Success, got {result}"
    vault_paths_in_result = {r.vault_path for r in result.value}
    assert paths["B"] in vault_paths_in_result, (
        "Note B (budget) must be in global search results"
    )


def test_rank_empty_candidates_returns_empty(seeded_db):
    """Empty candidate list returns Success([]) immediately."""
    db_path, _ = seeded_db

    from retrieval.ranker import rank

    result = rank(
        "budget",
        candidate_paths=[],
        max_candidates=3,
        db_path=db_path,
    )

    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert result.value == []


# ---------------------------------------------------------------------------
# Snippet validity
# ---------------------------------------------------------------------------


def test_rank_snippet_comes_from_body(seeded_db):
    """The snippet field must contain text from the note body, not the title
    or summary.  We verify by checking that the snippet contains a body-only
    phrase.
    """
    db_path, paths = seeded_db

    from retrieval.ranker import rank

    result = rank(
        "expenses",
        candidate_paths=[paths["B"]],
        max_candidates=3,
        db_path=db_path,
    )

    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert len(result.value) >= 1, "Expected at least one result"

    budget_result = result.value[0]
    # The word "expenses" appears in the BODY of Note B but NOT in the
    # title or summary.  If the snippet contains "expenses", it proves
    # snippet() targeted the body column (index 3).
    assert "expenses" in budget_result.snippet.lower(), (
        f"Snippet should contain body text matching 'expenses', got: {budget_result.snippet}"
    )


# ---------------------------------------------------------------------------
# Result type contract
# ---------------------------------------------------------------------------


def test_rank_returns_result_type(seeded_db):
    """rank() returns a Result -- it never raises."""
    db_path, paths = seeded_db

    from retrieval.ranker import rank

    result = rank(
        "stakeholder",
        candidate_paths=[paths["A"]],
        max_candidates=3,
        db_path=db_path,
    )

    assert isinstance(result, (Success, Failure)), (
        f"rank() must return Result, got {type(result)}"
    )
    # The valid query should succeed
    assert isinstance(result, Success), f"Expected Success, got {result}"


def test_rank_rrf_score_is_positive(seeded_db):
    """Every returned RankedResult must have a positive rrf_score."""
    db_path, paths = seeded_db

    from retrieval.ranker import rank

    result = rank(
        "budget",
        candidate_paths=[paths["A"], paths["B"], paths["C"]],
        max_candidates=3,
        db_path=db_path,
    )

    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert len(result.value) >= 1, "Expected at least one result"
    for r in result.value:
        assert r.rrf_score > 0, (
            f"RRF score must be positive, got {r.rrf_score} for {r.vault_path}"
        )


# ---------------------------------------------------------------------------
# Filtered KNN partition regression (A1 gotcha lock)
# ---------------------------------------------------------------------------


def test_knn_partition_property(tmp_path: Path):
    """Seed two notes: one far-but-in-set (vacation), one near-but-out-of-set
    (budget).  Query "budget" with only the far note in candidates.
    The far-but-in-set note IS returned; the near-but-out-of-set is excluded.

    This locks the A1 property: ``MATCH + k + IN`` partitions correctly.
    """
    db_path = tmp_path / "test_partition.db"
    init_db(db_path)
    far_in_set, near_out_of_set = _seed_db_for_partition_test(db_path)

    from retrieval.ranker import rank

    result = rank(
        "budget analysis",
        candidate_paths=[far_in_set],
        max_candidates=2,
        db_path=db_path,
    )

    assert isinstance(result, Success), f"Expected Success, got {result}"
    vault_paths = {r.vault_path for r in result.value}

    # The far-but-in-set note must be present
    assert far_in_set in vault_paths, (
        f"Far-but-in-set note {far_in_set} should be in results"
    )
    # The near-but-out-of-set note must NOT be present
    assert near_out_of_set not in vault_paths, (
        f"Near-but-out-of-set note {near_out_of_set} must be excluded "
        f"(candidate scoping failed or IN clause not applied)"
    )
