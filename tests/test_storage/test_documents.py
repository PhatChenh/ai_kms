"""tests/test_storage/test_documents.py"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.result import Failure, Success
from storage.db import init_db
from vault.frontmatter import NoteMetadata
from vault.writer import WriteOutcome


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


def _outcome(
    vault_path: str = "inbox/foo.md",
    content_hash: str = "abc123",
    **meta_kwargs,
) -> WriteOutcome:
    """Build a minimal WriteOutcome for testing."""
    return WriteOutcome(
        vault_path=vault_path,
        absolute_path=Path(f"/fake/vault/{vault_path}"),
        content_hash=content_hash,
        metadata=NoteMetadata(**meta_kwargs),
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_upsert_inserts_new_row(db):
    """upsert inserts a new row; get_by_path returns matching data."""
    from storage.documents import get_by_path, upsert

    outcome = _outcome("inbox/foo.md", content_hash="hash1", project="X")
    r = upsert(outcome, db_path=db)

    assert isinstance(r, Success)
    assert isinstance(r.value, int)

    row_r = get_by_path("inbox/foo.md", db_path=db)
    assert isinstance(row_r, Success)
    row = row_r.value
    assert row is not None
    assert row.vault_path == "inbox/foo.md"
    assert row.content_hash == "hash1"


def test_upsert_replaces_existing_row(db):
    """Second upsert with same vault_path but different hash → latest hash stored."""
    from storage.documents import get_by_path, upsert

    upsert(_outcome("inbox/foo.md", content_hash="old_hash"), db_path=db)
    upsert(_outcome("inbox/foo.md", content_hash="new_hash"), db_path=db)

    row_r = get_by_path("inbox/foo.md", db_path=db)
    assert isinstance(row_r, Success)
    assert row_r.value.content_hash == "new_hash"


def test_upsert_persists_updated_by_human(db):
    """Outcome with updated_by_human=True → row's updated_by_human column is truthy."""
    from storage.documents import get_by_path, upsert

    outcome = _outcome("inbox/foo.md", updated_by_human=True)
    upsert(outcome, db_path=db)

    row_r = get_by_path("inbox/foo.md", db_path=db)
    assert isinstance(row_r, Success)
    assert row_r.value.updated_by_human is True


def test_all_paths_returns_path_hash_pairs(db):
    """all_paths returns exactly the upserted (vault_path, content_hash) pairs."""
    from storage.documents import all_paths, upsert

    upsert(_outcome("inbox/a.md", content_hash="h1"), db_path=db)
    upsert(_outcome("inbox/b.md", content_hash="h2"), db_path=db)
    upsert(_outcome("inbox/c.md", content_hash="h3"), db_path=db)

    r = all_paths(db_path=db)
    assert isinstance(r, Success)
    pairs = {(p, h) for p, h in r.value}
    assert pairs == {
        ("inbox/a.md", "h1"),
        ("inbox/b.md", "h2"),
        ("inbox/c.md", "h3"),
    }


def test_delete_by_path_removes_row(db):
    """After delete_by_path, get_by_path returns Success(None)."""
    from storage.documents import delete_by_path, get_by_path, upsert

    upsert(_outcome("inbox/foo.md"), db_path=db)
    delete_by_path("inbox/foo.md", db_path=db)

    r = get_by_path("inbox/foo.md", db_path=db)
    assert isinstance(r, Success)
    assert r.value is None


def test_rename_updates_vault_path_preserves_id(db):
    """rename() changes vault_path and keeps the same row id (DECISION-001)."""
    from storage.documents import get_by_path, rename, upsert

    r_insert = upsert(_outcome("inbox/foo.md", content_hash="h"), db_path=db)
    assert isinstance(r_insert, Success)
    original_id = r_insert.value

    rename("inbox/foo.md", "projects/X/foo.md", db_path=db)

    old = get_by_path("inbox/foo.md", db_path=db)
    new = get_by_path("projects/X/foo.md", db_path=db)

    assert isinstance(old, Success)
    assert old.value is None  # old path gone

    assert isinstance(new, Success)
    assert new.value is not None
    assert new.value.id == original_id  # same row, just renamed


def test_upsert_returns_failure_on_locked_db(db, monkeypatch):
    """get_connection raising OperationalError → Failure(recoverable=False)."""
    import storage.documents as docs_mod

    def raise_locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(docs_mod, "get_connection", raise_locked)

    r = docs_mod.upsert(_outcome("inbox/foo.md"), db_path=db)
    assert isinstance(r, Failure)
    assert r.recoverable is False
