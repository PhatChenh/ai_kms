from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.result import Failure, Success
from storage.db import get_connection, init_db


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "kb.db"


def test_init_db_creates_file(db_path: Path) -> None:
    result = init_db(db_path)
    assert isinstance(result, Success)
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'"
        ).fetchall()
    }
    conn.close()
    assert tables == {"documents", "audit_log", "corrections", "schema_version"}


def test_init_db_is_idempotent(db_path: Path) -> None:
    result1 = init_db(db_path)
    assert isinstance(result1, Success)
    conn = sqlite3.connect(str(db_path))
    version_after_first = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()

    result2 = init_db(db_path)
    assert isinstance(result2, Success)
    conn = sqlite3.connect(str(db_path))
    version_after_second = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()

    assert version_after_second == version_after_first


def test_pragma_foreign_keys_on(db_path: Path) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        value = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert value == 1


def test_pragma_journal_mode_wal(db_path: Path) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        value = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert value == "wal"


def test_migration_runner_advances_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import storage.db as db_module

    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "002_test.sql").write_text("CREATE TABLE IF NOT EXISTS _test (x INT);")
    monkeypatch.setattr(db_module, "_MIGRATIONS_DIR", mig_dir)

    db_path = tmp_path / "kb.db"
    result = init_db(db_path)
    assert isinstance(result, Success)

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()
    assert version == 2


def test_migration_failure_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import storage.db as db_module

    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "002_test.sql").write_text("CREATE TABLE IF NOT EXISTS _test (x INT);")
    monkeypatch.setattr(db_module, "_MIGRATIONS_DIR", mig_dir)

    db_path = tmp_path / "kb.db"
    init_db(db_path)

    (mig_dir / "003_bad.sql").write_text("THIS IS NOT SQL")
    result = init_db(db_path)
    assert isinstance(result, Failure)

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()
    assert version == 2
