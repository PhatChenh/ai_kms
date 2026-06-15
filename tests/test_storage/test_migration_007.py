"""Tests for migration 007 — search indexes (embeddings_vec + notes_fts)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from storage.db import get_connection, init_db


def test_migration_007_creates_search_tables(tmp_path: Path) -> None:
    """P3-IDX-09: fresh init_db() creates both embeddings_vec and notes_fts."""
    db_path = tmp_path / "kb.db"
    result = init_db(db_path)
    assert result.is_success()

    conn = sqlite3.connect(str(db_path))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()

    assert "embeddings_vec" in tables, f"embeddings_vec missing; tables={tables}"
    assert "notes_fts" in tables, f"notes_fts missing; tables={tables}"


def test_migration_007_sets_schema_version_7(tmp_path: Path) -> None:
    """P3-IDX-09: schema_version is 7 after migration 007."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()

    assert version == 12, f"Expected schema_version=12, got {version}"


def test_migration_007_is_idempotent(tmp_path: Path) -> None:
    """P3-IDX-09: calling init_db again does not error (idempotent)."""
    db_path = tmp_path / "kb.db"
    result1 = init_db(db_path)
    assert result1.is_success()

    result2 = init_db(db_path)
    assert result2.is_success()

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()
    assert version == 12


def test_vec_version_succeeds(tmp_path: Path) -> None:
    """P3-IDX-08: SELECT vec_version() via get_connection() succeeds."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        row = conn.execute("SELECT vec_version()").fetchone()
        assert row is not None
        assert isinstance(row[0], str)
        assert row[0]  # non-empty version string


def test_fk_pragma_still_enforced(tmp_path: Path) -> None:
    """FK pragma still enforced after sqlite-vec loading."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    with get_connection(db_path) as conn:
        # documents(id=999) does not exist — FK should block the insert.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO corrections(document_id, field, ai_value, human_value) "
                "VALUES (999, 'title', 'a', 'b')"
            )
