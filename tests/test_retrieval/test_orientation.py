"""Tests for query_ranked_for_orientation (P9-B-06).

Tests:
- query_ranked_for_orientation returns entries sorted by 4-key ranking
- Filtering by dimension and entity
- Limit cap
- Retired exclusion
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.result import Success
from storage.db import get_connection, init_db
from storage.knowledge_entries import query_ranked_for_orientation


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _seed_orientation_db(db_path: Path) -> None:
    """Insert knowledge_entries with varied trust_score, retrieval_count,
    confidence, and updated_at to test 4-key ranking."""
    with get_connection(db_path) as conn:
        # Entry 1: highest trust_score (0.95), should rank first
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "people", "Alice", "role", "Fact A1",
                "confident", 0.9, '[]', 0.95, 10.0, "",
                "2026-06-01 10:00:00",
            ),
        )
        # Entry 2: lower trust_score (0.8) but should rank second
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "people", "Alice", "role", "Fact A2",
                "confident", 0.85, '[]', 0.8, 5.0, "",
                "2026-06-01 09:00:00",
            ),
        )
        # Entry 3: same trust_score as 2 but higher retrieval_count
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "people", "Alice", "role", "Fact A3",
                "confident", 0.85, '[]', 0.8, 7.0, "",
                "2026-06-01 08:00:00",
            ),
        )
        # Entry 4: retired, should never appear
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "people", "Bob", "role", "Fact B1",
                "retired", 0.99, '[]', 0.99, 100.0, "outdated",
                "2026-06-01 11:00:00",
            ),
        )
        # Entry 5: different dimension (process)
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "process", "standup", "schedule", "Fact P1",
                "confident", 0.7, '[]', 0.6, 2.0, "",
                "2026-06-01 07:00:00",
            ),
        )
        # Entry 6: same trust_score as 3, same retrieval_count, but higher confidence
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "people", "Charlie", "role", "Fact C1",
                "confident", 0.95, '[]', 0.8, 7.0, "",
                "2026-06-01 06:00:00",
            ),
        )
        # Entry 7: same as 6 but lower confidence, higher updated_at (confidence > updated_at)
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "people", "Diana", "role", "Fact D1",
                "confident", 0.9, '[]', 0.8, 7.0, "",
                "2026-06-02 00:00:00",
            ),
        )


@pytest.fixture
def seeded_orientation_db(tmp_path: Path) -> Path:
    """Temp DB with varied knowledge_entries for ranking tests."""
    db_path = tmp_path / "test_orientation.db"
    init_db(db_path)
    _seed_orientation_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Ranking tests
# ---------------------------------------------------------------------------


def test_query_ranked_for_orientation_returns_sorted_by_trust(seeded_orientation_db):
    """Results sorted by trust_score DESC first."""
    db_path = seeded_orientation_db

    result = query_ranked_for_orientation(limit=10, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    entries = result.value
    assert len(entries) >= 2

    for i in range(len(entries) - 1):
        assert entries[i].trust_score >= entries[i + 1].trust_score, (
            f"trust_score not descending at index {i}: "
            f"{entries[i].trust_score} < {entries[i + 1].trust_score}"
        )


def test_query_ranked_for_orientation_second_key_retrieval_count(seeded_orientation_db):
    """When trust_score ties, retrieval_count DESC is the tiebreaker."""
    db_path = seeded_orientation_db

    result = query_ranked_for_orientation(limit=10, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    entries = result.value

    # Find entries with trust_score=0.8
    trust_08 = [e for e in entries if e.trust_score == 0.8]
    if len(trust_08) >= 2:
        for i in range(len(trust_08) - 1):
            # Within same trust_score, retrieval_count must be non-increasing
            assert trust_08[i].retrieval_score >= trust_08[i + 1].retrieval_score, (
                f"retrieval_count not descending within trust_score=0.8 group"
            )


def test_query_ranked_for_orientation_third_key_confidence(seeded_orientation_db):
    """When trust_score and retrieval_count tie, confidence DESC is the tiebreaker."""
    db_path = seeded_orientation_db

    result = query_ranked_for_orientation(limit=10, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    entries = result.value

    # Charlie (confidence=0.95) should come before Diana (confidence=0.9)
    # both have trust_score=0.8, retrieval_count=7.0
    charlie_idx = None
    diana_idx = None
    for i, e in enumerate(entries):
        if e.entity == "Charlie":
            charlie_idx = i
        elif e.entity == "Diana":
            diana_idx = i

    if charlie_idx is not None and diana_idx is not None:
        assert charlie_idx < diana_idx, (
            f"Charlie (confidence=0.95) should rank before Diana (confidence=0.9), "
            f"got Charlie@{charlie_idx}, Diana@{diana_idx}"
        )


def test_query_ranked_for_orientation_excludes_retired(seeded_orientation_db):
    """Retired entries should never appear."""
    db_path = seeded_orientation_db

    result = query_ranked_for_orientation(limit=10, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    entries = result.value
    statuses = {e.status for e in entries}
    assert "retired" not in statuses, f"Retired entries found: {statuses}"
    entities = {e.entity for e in entries}
    assert "Bob" not in entities, "Bob (retired) should be excluded"


def test_query_ranked_for_orientation_respects_limit(seeded_orientation_db):
    """Results should be capped at limit."""
    db_path = seeded_orientation_db

    for lim in (1, 2, 5):
        result = query_ranked_for_orientation(limit=lim, db_path=db_path)
        assert isinstance(result, Success)
        assert len(result.value) <= lim, (
            f"Expected <= {lim} results, got {len(result.value)}"
        )


def test_query_ranked_for_orientation_filter_by_dimension(seeded_orientation_db):
    """Filtering by dimension returns only matching entries."""
    db_path = seeded_orientation_db

    result = query_ranked_for_orientation(dimension="process", limit=10, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    entries = result.value
    assert len(entries) > 0
    for e in entries:
        assert e.dimension == "process", (
            f"Expected process only, got {e.dimension}"
        )


def test_query_ranked_for_orientation_filter_by_entity(seeded_orientation_db):
    """Filtering by entity returns only matching entries."""
    db_path = seeded_orientation_db

    result = query_ranked_for_orientation(entity="Alice", limit=10, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    entries = result.value
    assert len(entries) > 0
    for e in entries:
        assert e.entity == "Alice", (
            f"Expected Alice only, got {e.entity}"
        )


def test_query_ranked_for_orientation_filter_by_both(seeded_orientation_db):
    """Filtering by both dimension and entity."""
    db_path = seeded_orientation_db

    result = query_ranked_for_orientation(
        dimension="people", entity="Alice", limit=10, db_path=db_path
    )

    assert isinstance(result, Success), f"Expected Success, got {result}"
    entries = result.value
    assert len(entries) > 0
    for e in entries:
        assert e.dimension == "people"
        assert e.entity == "Alice"


def test_query_ranked_for_orientation_no_match(seeded_orientation_db):
    """Filtering by non-existent entity returns empty list."""
    db_path = seeded_orientation_db

    result = query_ranked_for_orientation(entity="NonExistent", limit=10, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert result.value == [], f"Expected empty, got {result.value}"


def test_query_ranked_for_orientation_default_limit(seeded_orientation_db):
    """Default limit is 5."""
    db_path = seeded_orientation_db

    result = query_ranked_for_orientation(db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert len(result.value) <= 5, (
        f"Default limit should cap at 5, got {len(result.value)}"
    )
