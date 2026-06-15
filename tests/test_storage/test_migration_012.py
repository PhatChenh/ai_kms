"""Tests for migration 012 — facts_fts and facts_vec search indexes for Phase 9."""

import sqlite3

from storage.db import init_db


def test_migration_012_sets_schema_version_to_12(tmp_path):
    """After init_db, schema_version reads 12."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 12, f"Expected schema_version 12, got {version}"
    conn.close()


def test_migration_012_creates_facts_fts_table(tmp_path):
    """After init_db, facts_fts virtual table exists."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()

    assert "facts_fts" in tables, f"facts_fts missing; tables={tables}"


def test_migration_012_creates_facts_vec_table(tmp_path):
    """After init_db, facts_vec virtual table exists."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()

    assert "facts_vec" in tables, f"facts_vec missing; tables={tables}"
