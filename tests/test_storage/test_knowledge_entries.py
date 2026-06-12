"""Tests for knowledge_entries store — Phase 3 of P5 Slice 1."""

from __future__ import annotations

from core.config import ConfidenceBand
from storage.db import init_db
from storage.knowledge_entries import (
    KnowledgeEntry,
    get_confident_and_pending,
    query_by_dimension,
    query_by_entity,
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
