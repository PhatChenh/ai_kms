"""Tests for sqlite-vec extension loading in _connect()."""

import sqlite3
from pathlib import Path

import pytest

from storage.db import get_connection, init_db


def test_vec_extension_loaded(tmp_path: Path):
    """P3-IDX-08: SELECT vec_version() succeeds after _connect()."""
    db_path = tmp_path / "test.db"
    init_db(db_path)  # Run migrations including 007
    with get_connection(db_path) as conn:
        result = conn.execute("SELECT vec_version()").fetchone()
        assert result is not None


def test_fk_pragma_still_enforced(tmp_path: Path):
    """FK pragma still works after adding extension loading."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with get_connection(db_path) as conn:
        # Try FK violation -- should raise IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO documents (vault_path) VALUES (NULL)")
