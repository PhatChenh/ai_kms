"""tests/test_storage/test_get_by_id.py"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from core.result import Failure, Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


def _seed(
    vault_path: str = "inbox/foo.md",
    content_hash: str = "abc123",
    title: str | None = None,
    extracted_text: str = "dummy text",
    db: Path | None = None,
) -> int:
    """Seed a documents row via upsert_from_upload. Returns the row id."""
    from storage.documents import upsert_from_upload

    result = upsert_from_upload(
        vault_path=vault_path,
        extracted_text=extracted_text,
        content_hash=content_hash,
        title=title,
        db_path=db,
    )
    assert isinstance(result, Success), f"Seed failed: {result}"
    return result.value


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_get_by_id_existing_row(db):
    """get_by_id returns Success(DocumentRow) with correct fields for an existing id."""
    from storage.documents import get_by_id

    row_id = _seed("inbox/note.md", content_hash="hash_xyz", db=db)

    result = get_by_id(row_id, db_path=db)
    assert isinstance(result, Success)
    row = result.value
    assert row is not None
    assert row.id == row_id
    assert row.vault_path == "inbox/note.md"
    assert row.content_hash == "hash_xyz"


def test_get_by_id_nonexistent(db):
    """get_by_id returns Success(None) for a nonexistent id."""
    from storage.documents import get_by_id

    result = get_by_id(99999, db_path=db)
    assert isinstance(result, Success)
    assert result.value is None


def test_get_by_id_db_error(db):
    """get_by_id returns Failure(recoverable=False) on a DB error."""
    from storage.documents import get_by_id

    # Use a closed connection as the mock — get_connection must raise sqlite3.Error.
    # We patch get_connection to simulate a DB error.
    with patch(
        "storage.documents.get_connection",
        side_effect=sqlite3.OperationalError("database is locked"),
    ):
        result = get_by_id(1, db_path=db)
        assert isinstance(result, Failure)
        assert result.recoverable is False
