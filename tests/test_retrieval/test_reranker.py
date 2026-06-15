"""Tests for Re-ranker (retrieval/reranker.py) -- Component 3 of P3 Session B.

Tracer bullet: ``rerank("budget", candidates, db_path=db)`` returns
``Success(list[SearchResult])`` with summary, snippet, score, and metadata.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.result import Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_docs(db_path: Path) -> dict[str, str]:
    """Insert 3 documents rows into a temp DB (no indexes needed for reranker).

    Returns a dict mapping short labels to vault_path strings.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executemany(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, project, updated_at, key_topics)
               VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
            [
                (
                    "Projects/Alpha/budget.md",
                    "Q3 Budget Report",
                    "Detailed quarterly budget analysis for Q3 2026.",
                    "analysis",
                    "Alpha",
                    '["budget", "finance", "q3"]',
                ),
                (
                    "inbox/stakeholder.md",
                    "Stakeholder Resistance Management",
                    "Strategies for handling stakeholder resistance during change.",
                    "meeting-notes",
                    "Alpha",
                    '["stakeholder", "change-management"]',
                ),
                (
                    "inbox/vacation.md",
                    "Vacation Policy Update 2026",
                    "Updated company vacation policy with new PTO rules.",
                    "policy",
                    None,
                    '["hr", "policy"]',
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "budget": "Projects/Alpha/budget.md",
        "stakeholder": "inbox/stakeholder.md",
        "vacation": "inbox/vacation.md",
    }


@pytest.fixture
def seeded_docs_db(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Temp DB with 3 document rows (no search indexes needed)."""
    db_path = tmp_path / "test_reranker.db"
    init_db(db_path)
    paths = _seed_docs(db_path)
    return db_path, paths


# ---------------------------------------------------------------------------
# Tracer bullet -- every card has handle + summary + snippet + score + metadata
# (P3-SRCH-04)
# ---------------------------------------------------------------------------


def test_rerank_returns_search_results_with_metadata(seeded_docs_db):
    """Build RankedResults, call rerank("budget", candidates, db_path=db).
    Assert each SearchResult has summary, snippet, score, and metadata with
    the required keys.
    """
    db_path, paths = seeded_docs_db

    from retrieval.ranker import RankedResult
    from retrieval.reranker import rerank

    candidates = [
        RankedResult(
            vault_path=paths["budget"],
            rrf_score=0.032,
            snippet="Detailed <mark>quarterly</mark> budget <mark>analysis</mark>...",
        ),
        RankedResult(
            vault_path=paths["stakeholder"],
            rrf_score=0.028,
            snippet="Strategies for <mark>handling</mark> stakeholder <mark>resistance</mark>...",
        ),
        RankedResult(
            vault_path=paths["vacation"],
            rrf_score=0.015,
            snippet="Updated company <mark>vacation</mark> policy with new rules...",
        ),
    ]

    result = rerank("budget", candidates, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    cards = result.value
    assert len(cards) == 3, f"Expected 3 results, got {len(cards)}"

    required_metadata_keys = {
        "title",
        "project",
        "note_type",
        "updated_at",
        "key_topics",
        "tags",
    }

    for card in cards:
        assert isinstance(card.summary, (str, type(None))), "summary must be str|None"
        assert isinstance(card.snippet, str) and len(card.snippet) > 0
        assert isinstance(card.score, float), "score must be float"
        assert isinstance(card.metadata, dict), "metadata must be dict"
        assert card.metadata.keys() >= required_metadata_keys, (
            f"metadata missing keys: {required_metadata_keys - card.metadata.keys()}"
        )


# ---------------------------------------------------------------------------
# Cross-encoder score ordering (P3-SRCH-04)
# ---------------------------------------------------------------------------


def test_rerank_score_is_cross_encoder_score(seeded_docs_db):
    """Scores must be floats and results ordered by score descending."""
    db_path, paths = seeded_docs_db

    from retrieval.ranker import RankedResult
    from retrieval.reranker import rerank

    candidates = [
        RankedResult(
            vault_path=paths["budget"],
            rrf_score=0.032,
            snippet="Detailed <mark>quarterly</mark> budget <mark>analysis</mark>...",
        ),
        RankedResult(
            vault_path=paths["stakeholder"],
            rrf_score=0.028,
            snippet="Strategies for <mark>handling</mark> stakeholder <mark>resistance</mark>...",
        ),
    ]

    result = rerank("budget", candidates, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    cards = result.value
    assert len(cards) == 2

    for card in cards:
        assert isinstance(card.score, float), (
            f"score must be float, got {type(card.score)}"
        )

    # Results must be ordered by score descending
    for i in range(len(cards) - 1):
        assert cards[i].score >= cards[i + 1].score, (
            f"Cards not sorted descending: "
            f"score[{i}]={cards[i].score} < score[{i + 1}]={cards[i + 1].score}"
        )


# ---------------------------------------------------------------------------
# Stale row handling (P3-SRCH-06)
# ---------------------------------------------------------------------------


def test_rerank_skips_stale_row(seeded_docs_db):
    """A RankedResult with a vault_path that has no documents row must be
    omitted without crashing (P3-SRCH-06)."""
    db_path, paths = seeded_docs_db

    from retrieval.ranker import RankedResult
    from retrieval.reranker import rerank

    stale_path = "inbox/ghost_note.md"
    candidates = [
        RankedResult(
            vault_path=paths["budget"],
            rrf_score=0.032,
            snippet="budget analysis...",
        ),
        RankedResult(
            vault_path=stale_path,
            rrf_score=0.020,
            snippet="ghost note snippet...",
        ),
    ]

    result = rerank("budget", candidates, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    vault_paths_in_result = {c.vault_path for c in result.value}
    assert paths["budget"] in vault_paths_in_result, "Valid note should be in results"
    assert stale_path not in vault_paths_in_result, (
        "Stale row (no documents entry) must be skipped"
    )
    assert len(result.value) == 1, (
        f"Expected only 1 result (stale row skipped), got {len(result.value)}"
    )


# ---------------------------------------------------------------------------
# Cards are cheap -- no full body
# ---------------------------------------------------------------------------


def test_rerank_card_has_no_body(seeded_docs_db):
    """None of the SearchResult fields contain the full note body."""
    db_path, paths = seeded_docs_db

    from retrieval.ranker import RankedResult
    from retrieval.reranker import rerank

    candidates = [
        RankedResult(
            vault_path=paths["budget"],
            rrf_score=0.032,
            snippet="a short <mark>budget</mark> snippet...",
        ),
    ]

    result = rerank("budget", candidates, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    card = result.value[0]

    # Collect all string fields on the card
    card_fields = [
        card.vault_path,
        card.summary or "",
        card.snippet,
        str(card.score),
    ]
    # Also check metadata values
    for v in card.metadata.values():
        if isinstance(v, str):
            card_fields.append(v)
        elif isinstance(v, list):
            card_fields.extend(str(x) for x in v)

    # The full note body would contain paragraphs -- SearchResult should never
    # have it.  The summary is short; the snippet is truncated.
    full_body = "The Q3 budget analysis shows a 12% increase in operational expenses"
    assert full_body not in card.snippet, "Snippet should not contain full body"
    # The summary field is from the DocumentRow and does NOT contain the body
    assert "operational expenses" not in (card.summary or ""), (
        "Summary should not contain full body text"
    )


# ---------------------------------------------------------------------------
# Descriptive title (P3-SRCH-05)
# ---------------------------------------------------------------------------


def test_rerank_title_is_descriptive(seeded_docs_db):
    """Seed a documents row with title="Q3 Budget Report".
    Assert the card's metadata["title"] is "Q3 Budget Report", not a filename
    like "report.pdf" (P3-SRCH-05).
    """
    db_path, paths = seeded_docs_db

    from retrieval.ranker import RankedResult
    from retrieval.reranker import rerank

    candidates = [
        RankedResult(
            vault_path=paths["budget"],
            rrf_score=0.032,
            snippet="budget snippet...",
        ),
    ]

    result = rerank("budget", candidates, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    card = result.value[0]

    assert card.metadata["title"] == "Q3 Budget Report", (
        f"Expected descriptive title 'Q3 Budget Report', got '{card.metadata['title']}'"
    )
    # Must NOT be a filename
    assert card.metadata["title"] != "budget.md", (
        "Title should be descriptive, not the filename stem"
    )
    assert card.metadata["title"] != "report.pdf", (
        "Title should be the human-readable title, not a filename"
    )


# ---------------------------------------------------------------------------
# Result type contract
# ---------------------------------------------------------------------------


def test_rerank_returns_result_type(seeded_docs_db):
    """rerank() returns a Result -- it never raises."""
    db_path, paths = seeded_docs_db

    from core.result import Failure, Success
    from retrieval.ranker import RankedResult
    from retrieval.reranker import rerank

    candidates = [
        RankedResult(
            vault_path=paths["budget"],
            rrf_score=0.032,
            snippet="budget snippet...",
        ),
    ]

    result = rerank("budget", candidates, db_path=db_path)

    assert isinstance(result, (Success, Failure)), (
        f"rerank() must return Result, got {type(result)}"
    )
    assert isinstance(result, Success), f"Expected Success, got {result}"


# ---------------------------------------------------------------------------
# Empty candidates early return
# ---------------------------------------------------------------------------


def test_rerank_empty_candidates_returns_empty(seeded_docs_db):
    """Call rerank with an empty list -- assert Success([])."""
    db_path, _ = seeded_docs_db

    from retrieval.reranker import rerank

    result = rerank("budget", [], db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert result.value == [], f"Expected empty list, got {result.value}"


# ---------------------------------------------------------------------------
# P9-B-04: SearchResult.id is populated from DocumentRow.id
# ---------------------------------------------------------------------------


def test_rerank_results_have_id_populated(seeded_docs_db):
    """rerank() output cards must have id populated from the document row."""
    db_path, paths = seeded_docs_db

    from retrieval.ranker import RankedResult
    from retrieval.reranker import rerank

    candidates = [
        RankedResult(
            vault_path=paths["budget"],
            rrf_score=0.032,
            snippet="budget snippet...",
        ),
        RankedResult(
            vault_path=paths["stakeholder"],
            rrf_score=0.028,
            snippet="stakeholder snippet...",
        ),
    ]

    result = rerank("budget", candidates, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    cards = result.value
    assert len(cards) == 2

    for card in cards:
        assert card.id is not None, f"Card for {card.vault_path} has id=None"
        assert isinstance(card.id, int), f"id should be int, got {type(card.id)}"
        assert card.id > 0, f"id should be positive, got {card.id}"
