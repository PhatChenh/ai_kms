"""Tests for Phase 9 fact FTS + embedding sync in upsert() and retire().

Verifies that ``knowledge_entries.upsert()`` and ``retire()`` correctly
maintain the ``facts_fts`` (FTS5 external content) and ``facts_vec``
(vec0) search indexes.

.. note::

    ``facts_fts`` is an **external-content** FTS5 table (``content='knowledge_entries'``).
    Only MATCH queries work; plain ``WHERE`` / ``COUNT(*)`` queries fail because
    the ``entry_id`` column (stored UNINDEXED) does not exist in the content table.
    All FTS lookups use ``SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?``.
"""

from __future__ import annotations

import struct

from core.config import ConfidenceBand
from storage.db import get_connection, init_db
from storage.knowledge_entries import KnowledgeEntry, retire, upsert


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fts_match(db_path, query: str) -> set[int]:
    """Return the set of ``rowid`` values matching *query* in ``facts_fts``."""
    with get_connection(db_path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?", (query,)
        ).fetchall()
        return {r[0] for r in rows}


def _count_facts_vec(db_path, entry_id: int) -> int:
    """Return how many rows in facts_vec reference *entry_id*."""
    with get_connection(db_path, readonly=True) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM facts_vec WHERE entry_id = ?", (entry_id,)
        ).fetchone()[0]


def _get_embedding_blob(db_path, entry_id: int) -> bytes | None:
    """Return the raw embedding blob from facts_vec, or None."""
    with get_connection(db_path, readonly=True) as conn:
        row = conn.execute(
            "SELECT embedding FROM facts_vec WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# 1. INSERT path: new entry populates both search tables
# ---------------------------------------------------------------------------


def test_upsert_insert_populates_facts_fts(tmp_path):
    """After upsert(new_entry), facts_fts contains the entity+fact and is MATCHable."""
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
    entry_id = result.value
    assert isinstance(entry_id, int) and entry_id > 0

    # MATCH on the entity should find it
    matched = _fts_match(db_path, "Alice")
    assert entry_id in matched, f"entry_id {entry_id} not found via MATCH 'Alice'"

    # MATCH on the fact text should find it
    matched = _fts_match(db_path, "Engineering")
    assert entry_id in matched, f"entry_id {entry_id} not found via MATCH 'Engineering'"


def test_upsert_insert_populates_facts_vec(tmp_path):
    """After upsert(new_entry), facts_vec has an embedding of correct dimension."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    entry = KnowledgeEntry(
        dimension="people",
        entity="Bob",
        tag="role",
        fact="Staff Engineer",
        confidence=0.8,
    )
    band = ConfidenceBand(auto=0.8, suggest=0.5)
    result = upsert(entry, band=band, db_path=db_path)
    assert result.is_success()
    entry_id = result.value

    # facts_vec should have exactly 1 row
    assert _count_facts_vec(db_path, entry_id) == 1

    # Embedding blob should be 384 float32s = 1536 bytes
    blob = _get_embedding_blob(db_path, entry_id)
    assert blob is not None
    # Each float32 is 4 bytes; 384 * 4 = 1536
    assert len(blob) == 384 * 4, f"Expected 1536 bytes, got {len(blob)}"

    # Verify we can unpack as 384 float32s
    floats = struct.unpack(f"{384}f", blob)
    assert len(floats) == 384
    # Embedding should not be all zeros (sanity check)
    assert any(f != 0.0 for f in floats), "Embedding appears to be all zeros"


# ---------------------------------------------------------------------------
# 2. UPDATE path: old fact text gone, new text present
# ---------------------------------------------------------------------------


def test_upsert_update_syncs_facts_fts(tmp_path):
    """After upsert(existing_entry_with_changed_fact), old text gone, new present."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    band = ConfidenceBand(auto=0.8, suggest=0.5)

    # Insert original with a unique-ish fact text
    entry = KnowledgeEntry(
        dimension="people",
        entity="Alice",
        tag="role",
        fact="Original Fact Text",
        confidence=0.9,
    )
    result = upsert(entry, band=band, db_path=db_path)
    assert result.is_success()
    entry_id = result.value

    # Confirm old text is searchable
    assert entry_id in _fts_match(db_path, "Original")

    # Update with new fact text
    entry.id = entry_id
    entry.fact = "Completely Different Role Title"
    result2 = upsert(entry, band=band, db_path=db_path)
    assert result2.is_success()
    assert result2.value == entry_id

    # Old text should NOT be searchable
    assert entry_id not in _fts_match(db_path, "Original"), (
        "Old fact text 'Original' should no longer match after update"
    )

    # New text SHOULD be searchable
    assert entry_id in _fts_match(db_path, "Completely"), (
        "New fact text 'Completely' should match after update"
    )

    # The entry should still be findable by entity (still Alice)
    assert entry_id in _fts_match(db_path, "Alice")


def test_upsert_update_syncs_facts_vec(tmp_path):
    """After upsert(existing_entry), facts_vec still has exactly 1 row and valid blob."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    band = ConfidenceBand(auto=0.8, suggest=0.5)

    # Insert original
    entry = KnowledgeEntry(
        dimension="people",
        entity="Bob",
        tag="role",
        fact="First fact",
        confidence=0.8,
    )
    result = upsert(entry, band=band, db_path=db_path)
    assert result.is_success()
    entry_id = result.value

    old_blob = _get_embedding_blob(db_path, entry_id)
    assert old_blob is not None

    # Update
    entry.id = entry_id
    entry.fact = "Second different fact"
    result2 = upsert(entry, band=band, db_path=db_path)
    assert result2.is_success()

    # Should still have exactly 1 row
    assert _count_facts_vec(db_path, entry_id) == 1

    new_blob = _get_embedding_blob(db_path, entry_id)
    assert new_blob is not None
    assert len(new_blob) == 384 * 4

    # Blob should be different (different fact text → different embedding)
    assert new_blob != old_blob, (
        "Embedding should change when fact text changes"
    )


# ---------------------------------------------------------------------------
# 3. RETIRE path: search rows removed
# ---------------------------------------------------------------------------


def test_retire_removes_from_facts_fts(tmp_path):
    """After retire(entry_id), facts_fts has no rows for that id."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    band = ConfidenceBand(auto=0.8, suggest=0.5)

    entry = KnowledgeEntry(
        dimension="people",
        entity="Charlie",
        tag="role",
        fact="Temporary fact to retire",
        confidence=0.9,
    )
    result = upsert(entry, band=band, db_path=db_path)
    assert result.is_success()
    entry_id = result.value

    # Confirm it's in facts_fts (matchable by entity and fact)
    assert entry_id in _fts_match(db_path, "Charlie")
    assert entry_id in _fts_match(db_path, "Temporary")

    # Retire
    retire_result = retire(entry_id, "no longer relevant", db_path=db_path)
    assert retire_result.is_success()

    # MATCH on the old entity text should no longer find this entry
    assert entry_id not in _fts_match(db_path, "Charlie"), (
        "Retired entry should not appear in facts_fts MATCH on entity"
    )

    # MATCH on the old fact text should no longer find this entry
    assert entry_id not in _fts_match(db_path, "Temporary"), (
        "Retired entry should not appear in facts_fts MATCH on fact"
    )


def test_retire_removes_from_facts_vec(tmp_path):
    """After retire(entry_id), facts_vec has no rows for that id."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    band = ConfidenceBand(auto=0.8, suggest=0.5)

    entry = KnowledgeEntry(
        dimension="people",
        entity="Diana",
        tag="role",
        fact="Another temporary fact",
        confidence=0.7,
    )
    result = upsert(entry, band=band, db_path=db_path)
    assert result.is_success()
    entry_id = result.value

    # Confirm it's in facts_vec
    assert _count_facts_vec(db_path, entry_id) == 1

    # Retire
    retire_result = retire(entry_id, "outdated", db_path=db_path)
    assert retire_result.is_success()

    # Should be gone from facts_vec
    assert _count_facts_vec(db_path, entry_id) == 0
    assert _get_embedding_blob(db_path, entry_id) is None


def test_retire_nonexistent_does_not_crash(tmp_path):
    """retire() on a nonexistent id is still a success (rowcount=0), no crash."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    result = retire(9999, "no such entry", db_path=db_path)
    assert result.is_success()
    assert result.value == 0
    # No crash, no rows in facts_vec
    assert _count_facts_vec(db_path, 9999) == 0
    # facts_fts: a MATCH on a random string shouldn't find 9999
    assert 9999 not in _fts_match(db_path, "nonexistent_phrase_xyz")


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------


def test_upsert_multiple_entries_independent_fts_rows(tmp_path):
    """Each inserted entry gets its own independent facts_fts + facts_vec rows."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    band = ConfidenceBand(auto=0.8, suggest=0.5)

    id1 = upsert(
        KnowledgeEntry(
            dimension="people", entity="Alice", tag="a", fact="Fact A", confidence=0.9
        ),
        band=band,
        db_path=db_path,
    ).value
    id2 = upsert(
        KnowledgeEntry(
            dimension="people", entity="Bob", tag="b", fact="Fact B", confidence=0.8
        ),
        band=band,
        db_path=db_path,
    ).value

    assert id1 != id2

    # Each has exactly 1 vec row with correct dimension
    assert _count_facts_vec(db_path, id1) == 1
    assert _count_facts_vec(db_path, id2) == 1
    assert len(_get_embedding_blob(db_path, id1)) == 384 * 4
    assert len(_get_embedding_blob(db_path, id2)) == 384 * 4

    # MATCH on "Fact" finds both entries (both contain "Fact")
    matched = _fts_match(db_path, "Fact")
    assert id1 in matched
    assert id2 in matched

    # MATCH on "Alice" finds id1 only
    alice_matches = _fts_match(db_path, "Alice")
    assert id1 in alice_matches
    assert id2 not in alice_matches

    # MATCH on "Bob" finds id2 only
    bob_matches = _fts_match(db_path, "Bob")
    assert id2 in bob_matches
    assert id1 not in bob_matches


# ---------------------------------------------------------------------------
# 5. Best-effort embedding — upsert succeeds even when embedding fails
# ---------------------------------------------------------------------------


def test_upsert_insert_succeeds_when_embedding_fails(tmp_path, monkeypatch):
    """When _embed_fact returns Failure, upsert(new) still stores the fact
    but skips the search table inserts (best-effort)."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    from core.result import Failure as F
    from storage import knowledge_entries as ke

    # Force _embed_fact to fail
    def _fail_embed(_fact_text: str):
        return F("model not installed", recoverable=True, context={})

    monkeypatch.setattr(ke, "_embed_fact", _fail_embed)

    entry = KnowledgeEntry(
        dimension="people",
        entity="Alice",
        tag="role",
        fact="Engineering Manager",
        confidence=0.9,
    )
    result = upsert(entry, db_path=db_path)
    assert result.is_success()
    entry_id = result.value
    assert isinstance(entry_id, int) and entry_id > 0

    # Fact is in knowledge_entries
    from storage.knowledge_entries import query_by_dimension
    entries = query_by_dimension("people", db_path=db_path)
    assert entries.is_success()
    assert len(entries.value) == 1
    assert entries.value[0].fact == "Engineering Manager"

    # Search tables should be empty (embedding failed → skipped)
    assert _count_facts_vec(db_path, entry_id) == 0
    assert entry_id not in _fts_match(db_path, "Engineering")


def test_upsert_update_succeeds_when_embedding_fails(tmp_path, monkeypatch):
    """When _embed_fact returns Failure on update, the main row updates
    but search tables are left unchanged (stale index preserved)."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    band = ConfidenceBand(auto=0.8, suggest=0.5)

    from storage import knowledge_entries as ke

    # 1. First upsert with real embedding (so search tables are populated)
    entry = KnowledgeEntry(
        dimension="people",
        entity="Alice",
        tag="role",
        fact="Original Fact",
        confidence=0.9,
    )
    result = upsert(entry, band=band, db_path=db_path)
    assert result.is_success()
    entry_id = result.value

    # Verify search tables are populated
    assert _count_facts_vec(db_path, entry_id) == 1
    assert entry_id in _fts_match(db_path, "Original")

    # 2. Update with embedding failure
    def _fail_embed(_fact_text: str):
        from core.result import Failure as F
        return F("oom", recoverable=True, context={})

    monkeypatch.setattr(ke, "_embed_fact", _fail_embed)

    entry.id = entry_id
    entry.fact = "Updated Fact"
    result2 = upsert(entry, band=band, db_path=db_path)
    assert result2.is_success()
    assert result2.value == entry_id

    # Main row should be updated
    from storage.knowledge_entries import query_by_dimension
    entries = query_by_dimension("people", db_path=db_path)
    assert entries.is_success()
    assert entries.value[0].fact == "Updated Fact"

    # Search tables should still have the OLD data (embedding failed,
    # so we didn't delete old or insert new)
    assert _count_facts_vec(db_path, entry_id) == 1
    # Old text still searchable (we preserved stale index)
    assert entry_id in _fts_match(db_path, "Original")
    # New text NOT searchable (embedding failed, so no re-insert)
    assert entry_id not in _fts_match(db_path, "Updated")
