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


# ---------------------------------------------------------------------------
# Phase 2 — folder_path on insert + find_by_folder_path
# ---------------------------------------------------------------------------


def test_insert_writes_folder_path(db):
    """insert with folder_path writes it to the batches row."""
    from storage.batches import insert

    r = insert(
        folder_name="Q2-reports",
        destination_type="project",
        destination_name="Alpha",
        confidence=0.9,
        status="ROUTING",
        file_count=1,
        folder_path="Projects/Alpha/Q2",
        db_path=db,
    )
    assert isinstance(r, Success)
    batch_id = r.value

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT folder_path FROM batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    conn.close()
    assert row["folder_path"] == "Projects/Alpha/Q2"


def test_insert_folder_path_none(db):
    """insert without folder_path leaves column NULL."""
    from storage.batches import insert

    r = insert(
        folder_name="folder-x",
        destination_type=None,
        destination_name=None,
        confidence=0.5,
        status="ROUTING",
        file_count=1,
        db_path=db,
    )
    assert isinstance(r, Success)
    batch_id = r.value

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT folder_path FROM batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    conn.close()
    assert row["folder_path"] is None


def test_find_by_folder_path_found(db):
    """find_by_folder_path returns Success(int) when batch exists."""
    from storage.batches import find_by_folder_path, insert

    insert(
        folder_name="Q2",
        destination_type="project",
        destination_name="Alpha",
        confidence=1.0,
        status="ROUTING",
        file_count=1,
        folder_path="Projects/Alpha/Q2",
        db_path=db,
    )
    r = find_by_folder_path("Projects/Alpha/Q2", db_path=db)
    assert isinstance(r, Success)
    assert isinstance(r.value, int)


def test_find_by_folder_path_not_found(db):
    """find_by_folder_path returns Success(None) when no batch exists."""
    from storage.batches import find_by_folder_path

    r = find_by_folder_path("inbox/nonexistent", db_path=db)
    assert isinstance(r, Success)
    assert r.value is None


def test_find_by_folder_path_returns_most_recent(db):
    """find_by_folder_path returns the most recently created batch for a folder."""
    from storage.batches import find_by_folder_path, insert

    r1 = insert(
        folder_name="Q2",
        destination_type=None,
        destination_name=None,
        confidence=1.0,
        status="ROUTING",
        file_count=1,
        folder_path="Projects/Alpha/Q2",
        db_path=db,
    )
    assert isinstance(r1, Success)
    first_id = r1.value

    r2 = insert(
        folder_name="Q2-again",
        destination_type=None,
        destination_name=None,
        confidence=0.5,
        status="ROUTING",
        file_count=2,
        folder_path="Projects/Alpha/Q2",
        db_path=db,
    )
    assert isinstance(r2, Success)

    r = find_by_folder_path("Projects/Alpha/Q2", db_path=db)
    assert isinstance(r, Success)
    assert r.value == r2.value
    assert r.value != first_id


def test_find_by_folder_path_db_error(tmp_path):
    """find_by_folder_path on non-existent DB returns Failure."""
    from storage.batches import find_by_folder_path

    bad_db = tmp_path / "nonexistent" / "kb.db"
    r = find_by_folder_path("inbox/foo", db_path=bad_db)
    assert isinstance(r, Failure)
    assert r.recoverable is False
