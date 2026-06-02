"""tests/test_storage/test_batches.py

Tests for storage/batches.py — insert and update_status only.
DB fixture: init_db(tmp_path / "test.db") — same pattern as test_documents.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.result import Failure, Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_insert_returns_batch_id(db):
    """insert returns Success(int) with batch_id > 0."""
    from storage.batches import insert

    r = insert(
        folder_name="2024-Q1-Reports",
        destination_type="project",
        destination_name="Alpha",
        confidence=0.9,
        status="ROUTING",
        file_count=5,
        db_path=db,
    )

    assert isinstance(r, Success)
    assert isinstance(r.value, int)
    assert r.value > 0


def test_insert_row_readable(db):
    """After insert, raw SQL reads back all columns correctly."""
    from storage.batches import insert

    r = insert(
        folder_name="my-folder",
        destination_type="domain",
        destination_name="Engineering",
        confidence=0.75,
        status="ROUTING",
        file_count=3,
        db_path=db,
    )
    assert isinstance(r, Success)
    batch_id = r.value

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["folder_name"] == "my-folder"
    assert row["destination_type"] == "domain"
    assert row["destination_name"] == "Engineering"
    assert abs(row["confidence"] - 0.75) < 1e-9
    assert row["status"] == "ROUTING"
    assert row["file_count"] == 3
    assert row["created_at"] is not None


def test_update_status_changes_status(db):
    """update_status changes status from ROUTING to COMPLETE."""
    from storage.batches import insert, update_status

    r_insert = insert(
        folder_name="folder-x",
        destination_type=None,
        destination_name=None,
        confidence=0.5,
        status="ROUTING",
        file_count=1,
        db_path=db,
    )
    assert isinstance(r_insert, Success)
    batch_id = r_insert.value

    r_update = update_status(batch_id, "COMPLETE", db_path=db)
    assert isinstance(r_update, Success)
    assert r_update.value == 1  # rowcount

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status FROM batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    conn.close()

    assert row["status"] == "COMPLETE"


def test_update_status_unknown_id_returns_zero_rowcount(db):
    """update_status on non-existent id returns Success(0), not Failure."""
    from storage.batches import update_status

    r = update_status(99999, "COMPLETE", db_path=db)

    assert isinstance(r, Success)
    assert r.value == 0


def test_insert_fails_if_table_missing(tmp_path):
    """insert on a fresh DB without running init_db returns Failure."""
    from storage.batches import insert

    # Create a minimal SQLite file WITHOUT the batches table.
    bare_db = tmp_path / "bare.db"
    conn = sqlite3.connect(str(bare_db))
    conn.close()

    r = insert(
        folder_name="x",
        destination_type=None,
        destination_name=None,
        confidence=0.0,
        status="ROUTING",
        file_count=0,
        db_path=bare_db,
    )

    assert isinstance(r, Failure)
    assert r.recoverable is False
