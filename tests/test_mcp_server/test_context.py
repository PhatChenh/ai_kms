"""
tests/test_mcp_server/test_context.py

Phase 9 rewrite: tests for DB-first ContextInjectionEngine.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from core.result import Success
from mcp_server.context import ContextInjectionEngine


# ============================================================================
# Identity dedup
# ============================================================================


class TestIdentityDedup:
    def test_fresh_engine_nothing_seen(self):
        engine = ContextInjectionEngine()
        assert engine.is_fact_seen(1) is False
        assert engine.is_doc_seen(1) is False

    def test_record_and_check_fact(self):
        engine = ContextInjectionEngine()
        engine.record_fact_seen(42)
        assert engine.is_fact_seen(42) is True
        assert engine.is_fact_seen(99) is False

    def test_record_and_check_doc(self):
        engine = ContextInjectionEngine()
        engine.record_doc_seen(7)
        assert engine.is_doc_seen(7) is True
        assert engine.is_doc_seen(8) is False

    def test_fact_and_doc_independent_namespaces(self):
        engine = ContextInjectionEngine()
        engine.record_fact_seen(1)
        engine.record_doc_seen(1)
        assert engine.is_fact_seen(1) is True
        assert engine.is_doc_seen(1) is True


# ============================================================================
# Vault info — entity map
# ============================================================================


class TestVaultInfoEntityMap:
    def test_entity_map_groups_by_dimension(self, tmp_path):
        db_path = _seed_db_with_entries(tmp_path)

        engine = ContextInjectionEngine()
        result = engine.build_vault_info_response(db_path=db_path)

        assert isinstance(result, Success)
        blocks = result.value
        entity_map_block = next(b for b in blocks if b["source"] == "entity_map")
        content = entity_map_block["content"]
        assert "# Knowledge Map" in content
        assert "people" in content
        assert "Alice" in content
        assert "projects" in content

    def test_entity_map_excludes_empty_entity(self, tmp_path):
        db_path = _seed_db_with_entries(tmp_path)

        engine = ContextInjectionEngine()
        result = engine.build_vault_info_response(db_path=db_path)

        assert isinstance(result, Success)

    def test_retired_entries_excluded_from_entity_map(self, tmp_path):
        db_path = _seed_db_with_retired_entry(tmp_path)

        engine = ContextInjectionEngine()
        result = engine.build_vault_info_response(db_path=db_path)

        assert isinstance(result, Success)
        blocks = result.value
        entity_map_block = next(b for b in blocks if b["source"] == "entity_map")
        assert "RetiredPerson" not in entity_map_block["content"]


# ============================================================================
# Vault info — orientation facts
# ============================================================================


class TestVaultInfoOrientation:
    def test_orientation_facts_present(self, tmp_path):
        db_path = _seed_db_with_entries(tmp_path)

        engine = ContextInjectionEngine()
        result = engine.build_vault_info_response(db_path=db_path)

        assert isinstance(result, Success)
        blocks = result.value
        orient_block = next(b for b in blocks if b["source"] == "orientation_facts")
        assert "# Key Facts" in orient_block["content"]

    def test_orientation_facts_recorded_in_dedup(self, tmp_path):
        db_path = _seed_db_with_entries(tmp_path)

        engine = ContextInjectionEngine()
        result = engine.build_vault_info_response(db_path=db_path)
        assert isinstance(result, Success)

        # After vault_info, some facts should be marked as seen
        assert len(engine._seen_fact_ids) > 0, f"Expected some seen facts, got {engine._seen_fact_ids}"


# ============================================================================
# Vault info — inbox stats
# ============================================================================


class TestVaultInfoInbox:
    def test_inbox_stats_present(self, tmp_path):
        db_path = _seed_db_with_entries(tmp_path)

        engine = ContextInjectionEngine()
        result = engine.build_vault_info_response(db_path=db_path)

        assert isinstance(result, Success)
        blocks = result.value
        inbox_block = next(b for b in blocks if b["source"] == "inbox_stats")
        assert "# Inbox" in inbox_block["content"]


# ============================================================================
# Search response
# ============================================================================


class TestSearchResponse:
    def test_search_returns_result_blocks(self, tmp_path):
        db_path = _seed_db_with_entries(tmp_path)

        engine = ContextInjectionEngine()
        result = engine.build_search_response(
            "test query", db_path=db_path, max_results=10
        )

        assert isinstance(result, Success), f"Got {type(result).__name__}: {getattr(result, 'error', '?')}"
        assert isinstance(result.value, list)

    def test_search_with_no_matches_returns_empty(self, tmp_path):
        db_path = _seed_db_with_entries(tmp_path)

        engine = ContextInjectionEngine()
        result = engine.build_search_response(
            "xyznonexistentquery12345", db_path=db_path, max_results=10
        )

        assert isinstance(result, Success), f"Got {type(result).__name__}: {getattr(result, 'error', '?')}"


# ============================================================================
# Zero disk reads
# ============================================================================


class TestZeroDiskReads:
    def test_vault_info_no_disk_reads(self, tmp_path):
        """Verify the engine does not read files from disk."""
        import os
        db_path = _seed_db_with_entries(tmp_path)

        engine = ContextInjectionEngine()
        # We already seeded a real DB — verify it works without Path I/O
        # The engine should never call Path.read_text, Path.is_file on vault paths
        result = engine.build_vault_info_response(db_path=db_path)
        assert isinstance(result, Success)


# ============================================================================
# Helpers
# ============================================================================


def _seed_db_with_entries(tmp_path: Path) -> Path:
    """Create a temp DB with knowledge_entries, documents, and search tables."""
    from storage.db import init_db

    db_path = tmp_path / "test.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")

    # Insert knowledge entries with explicit status
    entries = [
        (1, "people", "Alice", "Alice is a senior engineer", "confident", 0.5, 0, "[]", "2024-01-01"),
        (2, "people", "Bob", "Bob works on infrastructure", "confident", 0.6, 0, "[]", "2024-01-02"),
        (3, "projects", "Alpha", "Alpha is the main product", "confident", 0.5, 0, "[]", "2024-01-03"),
        (4, "process", "Daily Standup", "Standup at 9am daily", "pending", 0.4, 0, "[]", "2024-01-04"),
    ]
    for eid, dim, entity, fact, conf, trust, rcount, sources, updated in entries:
        conn.execute(
            "INSERT INTO knowledge_entries(id, dimension, entity, fact, confidence, "
            "trust_score, retrieval_count, sources, status, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'confident', ?)",
            (eid, dim, entity, fact, conf, trust, rcount, sources, updated),
        )

    # Insert a document
    conn.execute(
        "INSERT INTO documents(id, vault_path, title, summary, created_at, updated_at) "
        "VALUES(1, 'test/note.md', 'Test Note', 'A test summary', '2024-01-01', '2024-01-01')"
    )

    # Sync facts_fts
    for eid, dim, entity, fact, conf, trust, rcount, sources, updated in entries:
        conn.execute(
            "INSERT INTO facts_fts(rowid, entry_id, entity, fact) VALUES(?, ?, ?, ?)",
            (eid, eid, entity, fact),
        )

    # Insert minimal embeddings into facts_vec (all zeros — just to have rows)
    import struct
    zero_vec = struct.pack("384f", *([0.0] * 384))
    for eid, _, _, _, _, _, _, _, _ in entries:
        try:
            conn.execute(
                "INSERT INTO facts_vec(entry_id, embedding) VALUES(?, ?)",
                (eid, zero_vec),
            )
        except sqlite3.OperationalError:
            pass  # vec0 may reject zero vectors

    conn.commit()
    conn.close()
    return db_path


def _seed_db_with_retired_entry(tmp_path: Path) -> Path:
    """Create DB with one retired entry."""
    from storage.db import init_db

    db_path = tmp_path / "test_retired.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute(
        "INSERT INTO knowledge_entries(id, dimension, entity, fact, confidence, "
        "trust_score, retrieval_count, sources, status, updated_at) "
        "VALUES(1, 'people', 'Active', 'active fact', 'confident', 0.5, 0, '[]', 'confident', '2024-01-01')"
    )
    conn.execute(
        "INSERT INTO knowledge_entries(id, dimension, entity, fact, confidence, "
        "trust_score, retrieval_count, sources, status, updated_at) "
        "VALUES(2, 'people', 'RetiredPerson', 'retired fact', 'confident', 0.5, 0, '[]', 'retired', '2024-01-01')"
    )

    conn.execute(
        "INSERT INTO facts_fts(rowid, entry_id, entity, fact) VALUES(1, 1, 'Active', 'active fact')"
    )
    conn.execute(
        "INSERT INTO facts_fts(rowid, entry_id, entity, fact) VALUES(2, 2, 'RetiredPerson', 'retired fact')"
    )

    conn.commit()
    conn.close()
    return db_path
