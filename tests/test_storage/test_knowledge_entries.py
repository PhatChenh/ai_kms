"""Tests for knowledge_entries store — Phase 3 of P5 Slice 1 + Phase 4 ranking."""

from __future__ import annotations

import sqlite3

from core.config import ConfidenceBand
from core.result import Failure, Success
from storage.db import init_db
from storage.knowledge_entries import (
    KnowledgeEntry,
    get_confident_and_pending,
    query_by_dimension,
    query_by_entity,
    query_ranked_by_dimension,
    retire,
    upsert,
)


# ---------------------------------------------------------------------------
# P5-DATA-03: upsert + query_by_dimension (tracer bullet)
# ---------------------------------------------------------------------------


def test_upsert_and_query_by_dimension(tmp_path):
    """Store a fact, read it back by dimension — sources round-trip as a real list."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    entry = KnowledgeEntry(
        dimension="people",
        entity="Alice",
        tag="role",
        fact="Engineering Manager",
        confidence=0.9,
        sources=["notes/alice.md"],
        reasoning="stated in bio",
    )
    band = ConfidenceBand(auto=0.8, suggest=0.5)
    result = upsert(entry, band=band, db_path=db_path)
    assert result.is_success()
    row_id = result.value
    assert isinstance(row_id, int)
    assert row_id > 0

    # Read back by dimension
    results = query_by_dimension("people", db_path=db_path)
    assert results.is_success()
    entries = results.value
    assert len(entries) == 1
    e = entries[0]
    assert e.id == row_id
    assert e.fact == "Engineering Manager"
    assert e.status == "confident"  # confidence 0.9 → AUTO → confident
    assert isinstance(e.sources, list)  # round-trips as real list, not string
    assert "notes/alice.md" in e.sources


# ---------------------------------------------------------------------------
# P5-DATA-04: query_by_entity returns only that entity's facts
# ---------------------------------------------------------------------------


def test_query_by_entity_returns_only_that_entity(tmp_path):
    """query_by_entity('Alice') returns Alice's 2 facts, excludes Bob's."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    band = ConfidenceBand(auto=0.8, suggest=0.5)

    # Insert 2 facts for Alice (different tags), 1 for Bob
    upsert(
        KnowledgeEntry(
            dimension="people",
            entity="Alice",
            tag="role",
            fact="Engineering Manager",
            confidence=0.9,
        ),
        band=band,
        db_path=db_path,
    )
    upsert(
        KnowledgeEntry(
            dimension="people",
            entity="Alice",
            tag="other",
            fact="Prefers async communication",
            confidence=0.7,
        ),
        band=band,
        db_path=db_path,
    )
    upsert(
        KnowledgeEntry(
            dimension="people",
            entity="Bob",
            tag="role",
            fact="Staff Engineer",
            confidence=0.9,
        ),
        band=band,
        db_path=db_path,
    )

    results = query_by_entity("Alice", db_path=db_path)
    assert results.is_success()
    entries = results.value
    assert len(entries) == 2
    alice_entities = {e.entity for e in entries}
    assert alice_entities == {"Alice"}
    facts = {e.fact for e in entries}
    assert "Engineering Manager" in facts
    assert "Prefers async communication" in facts
    tags = {e.tag for e in entries}
    assert "role" in tags
    assert "other" in tags


# ---------------------------------------------------------------------------
# P5-DATA-05: retire keeps row, flips status
# ---------------------------------------------------------------------------


def test_retire_keeps_row_flips_status(tmp_path):
    """retire() keeps the row, flips status to 'retired', records reason."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    band = ConfidenceBand(auto=0.8, suggest=0.5)

    result = upsert(
        KnowledgeEntry(
            dimension="people",
            entity="Alice",
            tag="role",
            fact="Engineering Manager",
            confidence=0.9,
        ),
        band=band,
        db_path=db_path,
    )
    assert result.is_success()
    entry_id = result.value

    # Retire it
    retire_result = retire(entry_id, "outdated info", db_path=db_path)
    assert retire_result.is_success()
    assert retire_result.value == 1  # rowcount

    # Read back — status should be 'retired'
    entries = query_by_dimension("people", db_path=db_path)
    assert entries.is_success()
    assert len(entries.value) == 1
    e = entries.value[0]
    assert e.status == "retired"
    assert e.reasoning == "outdated info"
    assert e.updated_at != ""  # timestamp refreshed


def test_retire_nonexistent_id_returns_zero(tmp_path):
    """retire() on a nonexistent id returns Success(rowcount=0), not a crash."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    result = retire(9999, "no such entry", db_path=db_path)
    assert result.is_success()
    assert result.value == 0


# ---------------------------------------------------------------------------
# P5-DATA-06: get_confident_and_pending excludes retired
# ---------------------------------------------------------------------------


def test_get_confident_and_pending_excludes_retired(tmp_path):
    """Live set returns confident + pending, excludes retired entries."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    # Insert one confident (high confidence)
    upsert(
        KnowledgeEntry(
            dimension="people",
            entity="TestEntity",
            tag="role",
            fact="Confident fact",
            confidence=0.95,
        ),
        band=ConfidenceBand(auto=0.8, suggest=0.5),
        db_path=db_path,
    )
    # Insert one pending (by explicit status)
    upsert(
        KnowledgeEntry(
            dimension="people",
            entity="TestEntity",
            tag="other",
            fact="Pending fact",
            status="pending",
        ),
        db_path=db_path,
    )
    # Insert one retired (then retire it)
    r = upsert(
        KnowledgeEntry(
            dimension="people",
            entity="TestEntity",
            tag="other",
            fact="Retired fact",
            confidence=0.9,
        ),
        band=ConfidenceBand(auto=0.8, suggest=0.5),
        db_path=db_path,
    )
    assert r.is_success()
    retire(r.value, "outdated", db_path=db_path)

    # Live set should exclude retired
    results = get_confident_and_pending(entity="TestEntity", db_path=db_path)
    assert results.is_success()
    entries = results.value
    assert len(entries) == 2
    statuses = {e.status for e in entries}
    assert "retired" not in statuses
    facts = {e.fact for e in entries}
    assert "Confident fact" in facts
    assert "Pending fact" in facts
    assert "Retired fact" not in facts


def test_upsert_update_path(tmp_path):
    """upsert() with id set updates existing row — no duplicates, facts change, timestamps refresh."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    band = ConfidenceBand(auto=0.8, suggest=0.5)

    # 1. Insert
    entry = KnowledgeEntry(
        dimension="people",
        entity="Alice",
        tag="role",
        fact="Original fact",
        confidence=0.95,
        sources=["notes/alice.md"],
        reasoning="initial capture",
    )
    result = upsert(entry, band=band, db_path=db_path)
    assert result.is_success()
    row_id = result.value
    assert isinstance(row_id, int) and row_id > 0

    # Capture original timestamps
    entries = query_by_dimension("people", db_path=db_path)
    assert entries.is_success()
    original = entries.value[0]
    assert original.fact == "Original fact"
    assert original.confidence == 0.95
    original_created_at = original.created_at
    original_updated_at = original.updated_at

    # 2. Modify and upsert again
    entry.id = row_id
    entry.fact = "Updated fact"
    entry.confidence = 0.99
    result2 = upsert(entry, band=band, db_path=db_path)
    assert result2.is_success()
    assert result2.value == row_id  # same id

    # 3. Read back — verify UPDATE semantics
    entries2 = query_by_dimension("people", db_path=db_path)
    assert entries2.is_success()
    assert len(entries2.value) == 1  # no duplicate
    updated = entries2.value[0]
    assert updated.id == row_id
    assert updated.fact == "Updated fact"
    assert updated.confidence == 0.99
    assert updated.created_at == original_created_at  # preserved
    # updated_at may be same second as original in fast tests — >= confirms it was set
    assert updated.updated_at >= original_updated_at


def test_get_confident_and_pending_no_filters(tmp_path):
    """get_confident_and_pending() with no filters returns all non-retired across entities."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    # Insert facts for two different entities
    upsert(
        KnowledgeEntry(
            dimension="people",
            entity="Alice",
            tag="role",
            fact="Alice fact",
            confidence=0.9,
        ),
        band=ConfidenceBand(auto=0.8, suggest=0.5),
        db_path=db_path,
    )
    upsert(
        KnowledgeEntry(
            dimension="people",
            entity="Bob",
            tag="role",
            fact="Bob fact",
            confidence=0.9,
        ),
        band=ConfidenceBand(auto=0.8, suggest=0.5),
        db_path=db_path,
    )

    results = get_confident_and_pending(db_path=db_path)
    assert results.is_success()
    entries = results.value
    assert len(entries) == 2
    entity_names = {e.entity for e in entries}
    assert entity_names == {"Alice", "Bob"}


# ---------------------------------------------------------------------------
# Phase 8 Slice A — Phase 4: ranking fields round-trip + ranked query
# ---------------------------------------------------------------------------


def test_upsert_round_trips_trust_score_and_retrieval_count(tmp_path):
    """upsert then read-back preserves trust_score and retrieval_count defaults.

    Per design decision, upsert does NOT include these columns — DB defaults
    cover omitted inserts.  The round-trip verifies that _row_to_entry reads
    the default values correctly.
    """
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    entry = KnowledgeEntry(
        dimension="people",
        entity="Alice",
        tag="role",
        fact="Engineering Manager",
        confidence=0.9,
        sources=["notes/alice.md"],
    )
    band = ConfidenceBand(auto=0.8, suggest=0.5)
    result = upsert(entry, band=band, db_path=db_path)
    assert result.is_success()
    row_id = result.value

    # Read back via existing query
    results = query_by_dimension("people", db_path=db_path)
    assert results.is_success()
    entries = results.value
    assert len(entries) == 1
    e = entries[0]
    assert e.id == row_id
    # DB defaults: trust_score=0.5, retrieval_count=0
    assert e.trust_score == 0.5
    assert e.retrieval_count == 0

    # Insert a second entry (also with defaults) and confirm
    entry2 = KnowledgeEntry(
        dimension="people",
        entity="Bob",
        tag="role",
        fact="Staff Engineer",
        confidence=0.7,
    )
    result2 = upsert(entry2, band=band, db_path=db_path)
    assert result2.is_success()
    results2 = query_by_entity("Bob", db_path=db_path)
    assert results2.is_success()
    e2 = results2.value[0]
    assert e2.trust_score == 0.5
    assert e2.retrieval_count == 0


def test_query_ranked_by_dimension_excludes_retired_orders_and_caps(tmp_path):
    """Ranked query: excludes retired, orders trust→confidence→recency, respects cap."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    # Seed more rows than cap (cap=3) for one dimension, mixed values
    cap = 3
    dimension = "people"

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence,
                trust_score, retrieval_count, sources, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dimension, "Alice", "role", "Fact A", "confident", 0.9,
             0.9, 10, '[]', ''),
        )
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence,
                trust_score, retrieval_count, sources, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dimension, "Bob", "role", "Fact B", "confident", 0.8,
             0.7, 5, '[]', ''),
        )
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence,
                trust_score, retrieval_count, sources, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dimension, "Charlie", "role", "Fact C", "retired", 0.95,
             0.95, 20, '[]', 'retired reason'),
        )
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence,
                trust_score, retrieval_count, sources, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dimension, "Diana", "role", "Fact D", "confident", 0.7,
             0.6, 3, '[]', ''),
        )
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence,
                trust_score, retrieval_count, sources, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dimension, "Eve", "role", "Fact E", "pending", 0.5,
             0.4, 1, '[]', ''),
        )
        conn.commit()

    # Execute the new ranked query
    results = query_ranked_by_dimension(dimension, limit=cap, db_path=db_path)
    assert results.is_success()
    entries = results.value

    # 1) Cap — no more than cap
    assert len(entries) <= cap  # should be exactly cap if non-retired >= cap

    # 2) Exclude retired
    facts = {e.fact for e in entries}
    assert "Fact C" not in facts  # retired row

    # 3) Order: trust_score DESC
    if len(entries) >= 2:
        for i in range(len(entries) - 1):
            assert entries[i].trust_score >= entries[i + 1].trust_score, (
                f"trust_score not descending at index {i}: "
                f"{entries[i].trust_score} < {entries[i + 1].trust_score}"
            )

    # 4) Each entry has id, trust_score, retrieval_count
    for e in entries:
        assert e.id is not None
        assert isinstance(e.id, int)
        assert isinstance(e.trust_score, float)
        assert isinstance(e.retrieval_count, int)

    # 5) The top entry should be the one with highest trust_score (Alice, 0.9)
    assert entries[0].trust_score == 0.9
    assert entries[0].entity == "Alice"


# ---------------------------------------------------------------------------
# Phase 9 — Source-prune (Slice B)
# ---------------------------------------------------------------------------


def test_prune_sources_removes_target_id_from_multi_source_facts(tmp_path):
    """Multi-source facts lose only the target id, rest intact, deduped."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    from storage.knowledge_entries import prune_sources

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO knowledge_entries
           (dimension, entity, tag, fact, status, confidence, sources, reasoning)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("people", "Alice", "role", "Fact A", "confident", 0.9,
         '["100", "200", "300"]', ''),
    )
    conn.execute(
        """INSERT INTO knowledge_entries
           (dimension, entity, tag, fact, status, confidence, sources, reasoning)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("people", "Bob", "role", "Fact B", "confident", 0.8,
         '["100"]', ''),
    )
    conn.commit()
    conn.close()

    result = prune_sources(100, db_path=db_path)
    assert isinstance(result, Success)
    assert result.value == 2  # Both entries touched

    # Multi-source: 100 removed, 200 and 300 remain
    entries = query_by_entity("Alice", db_path=db_path)
    assert isinstance(entries, Success)
    alice = entries.value[0]
    assert "100" not in alice.sources
    assert "200" in alice.sources
    assert "300" in alice.sources

    # Sole-source: sources emptied, status → pending
    bob_entries = query_by_entity("Bob", db_path=db_path)
    assert isinstance(bob_entries, Success)
    bob = bob_entries.value[0]
    assert bob.sources == []
    assert bob.status == "pending"


def test_prune_sources_skips_retired_facts(tmp_path):
    """Retired facts are not touched by prune_sources."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    from storage.knowledge_entries import prune_sources

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO knowledge_entries
           (dimension, entity, tag, fact, status, confidence, sources, reasoning)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("people", "Alice", "role", "Retired fact", "retired", 0.5,
         '["100"]', 'was wrong'),
    )
    conn.commit()
    conn.close()

    result = prune_sources(100, db_path=db_path)
    assert isinstance(result, Success)
    assert result.value == 0  # No entries touched

    entries = query_by_entity("Alice", db_path=db_path)
    assert isinstance(entries, Success)
    assert entries.value[0].sources == ["100"]
    assert entries.value[0].status == "retired"


def test_prune_sources_unrelated_facts_untouched(tmp_path):
    """Facts that don't contain the target id are untouched."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    from storage.knowledge_entries import prune_sources

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO knowledge_entries
           (dimension, entity, tag, fact, status, confidence, sources, reasoning)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("people", "Alice", "role", "Fact A", "confident", 0.9,
         '["200", "300"]', ''),
    )
    conn.commit()
    conn.close()

    result = prune_sources(100, db_path=db_path)
    assert isinstance(result, Success)
    assert result.value == 0

    entries = query_by_entity("Alice", db_path=db_path)
    assert isinstance(entries, Success)
    assert entries.value[0].sources == ["200", "300"]


def test_prune_sources_deduplicates_after_removal(tmp_path):
    """After removing target id, sources list has no duplicates."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    from storage.knowledge_entries import prune_sources

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO knowledge_entries
           (dimension, entity, tag, fact, status, confidence, sources, reasoning)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("people", "Alice", "role", "Fact A", "confident", 0.9,
         '["100", "200", "200"]', ''),
    )
    conn.commit()
    conn.close()

    result = prune_sources(100, db_path=db_path)
    assert isinstance(result, Success)

    entries = query_by_entity("Alice", db_path=db_path)
    assert isinstance(entries, Success)
    assert entries.value[0].sources == ["200"]  # deduped
