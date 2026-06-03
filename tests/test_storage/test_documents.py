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


def test_upsert_with_batch_id(db):
    """upsert with a real batch_id FK → row.batch_id matches."""
    from storage.batches import insert as insert_batch
    from storage.documents import get_by_path, upsert

    # Insert a real batch row first so FK constraint is satisfied.
    r_batch = insert_batch(
        folder_name="test-folder",
        destination_type=None,
        destination_name=None,
        confidence=0.9,
        status="ROUTING",
        file_count=1,
        db_path=db,
    )
    assert isinstance(r_batch, Success)
    batch_id = r_batch.value

    outcome = _outcome("inbox/with_batch.md", content_hash="batchhash")
    r = upsert(outcome, db_path=db, batch_id=batch_id)

    assert isinstance(r, Success)

    row_r = get_by_path("inbox/with_batch.md", db_path=db)
    assert isinstance(row_r, Success)
    row = row_r.value
    assert row is not None
    assert row.batch_id == batch_id


def test_upsert_without_batch_id(db):
    """upsert without batch_id → row.batch_id is None."""
    from storage.documents import get_by_path, upsert

    outcome = _outcome("inbox/no_batch.md", content_hash="nobatch")
    r = upsert(outcome, db_path=db)

    assert isinstance(r, Success)

    row_r = get_by_path("inbox/no_batch.md", db_path=db)
    assert isinstance(row_r, Success)
    row = row_r.value
    assert row is not None
    assert row.batch_id is None


def test_documents_table_has_project_status_key_topics(db: Path):
    """After init_db, PRAGMA table_info(documents) includes project, status, key_topics."""
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("PRAGMA table_info(documents)").fetchall()
        col_names = {row[1] for row in rows}
    finally:
        conn.close()

    assert "project" in col_names, f"expected 'project' column, got {col_names}"
    assert "status" in col_names, f"expected 'status' column, got {col_names}"
    assert "key_topics" in col_names, f"expected 'key_topics' column, got {col_names}"


def test_upsert_returns_failure_on_locked_db(db, monkeypatch):
    """get_connection raising OperationalError → Failure(recoverable=False)."""
    import storage.documents as docs_mod

    def raise_locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(docs_mod, "get_connection", raise_locked)

    r = docs_mod.upsert(_outcome("inbox/foo.md"), db_path=db)
    assert isinstance(r, Failure)
    assert r.recoverable is False


# ---------------------------------------------------------------------------
# Phase Pre-2 — new columns (project, status, key_topics)
# ---------------------------------------------------------------------------


def test_document_row_defaults():
    """DocumentRow without project/status/key_topics → defaults kick in."""
    from storage.documents import DocumentRow

    row = DocumentRow(
        id=1,
        vault_path="x",
        title="y",
        summary="s",
        note_type="note",
        confidence=0.9,
        created_at="2026-01-01",
        updated_at="2026-01-01",
        updated_by_human=False,
        content_hash="h",
        batch_id=None,
    )
    assert row.project is None
    assert row.status is None
    assert row.key_topics == []


def test_row_from_sqlite_reads_key_topics_json(db: Path):
    """Raw insert with key_topics JSON → get_by_path returns parsed list."""
    import json

    from storage.documents import get_by_path

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, confidence,
                updated_at, updated_by_human, content_hash, key_topics)
               VALUES (?, ?, ?, ?, ?, datetime('now'), 0, ?, ?)""",
            (
                "inbox/topics.md",
                "Test",
                "summary",
                "note",
                0.9,
                "hash_topics",
                json.dumps(["quarterly-review", "stakeholder-management"]),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    row_r = get_by_path("inbox/topics.md", db_path=db)
    assert isinstance(row_r, Success)
    assert row_r.value is not None
    assert row_r.value.key_topics == ["quarterly-review", "stakeholder-management"]


def test_row_from_sqlite_handles_null_key_topics(db: Path):
    """Raw insert with key_topics=NULL → get_by_path returns []."""
    from storage.documents import get_by_path

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, confidence,
                updated_at, updated_by_human, content_hash, key_topics)
               VALUES (?, ?, ?, ?, ?, datetime('now'), 0, ?, NULL)""",
            (
                "inbox/null_topics.md",
                "Test",
                "summary",
                "note",
                0.9,
                "hash_null",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    row_r = get_by_path("inbox/null_topics.md", db_path=db)
    assert isinstance(row_r, Success)
    assert row_r.value is not None
    assert row_r.value.key_topics == []


def test_upsert_writes_and_reads_back_new_columns(db):
    """upsert with project, status, tags → get_by_path returns all three."""
    from storage.documents import get_by_path, upsert

    outcome = _outcome(
        "inbox/newcols.md",
        content_hash="hash_newcols",
        project="Alpha",
        status=None,
        tags=["domain/finance", "type/note", "quarterly-review"],
    )
    r = upsert(outcome, db_path=db)
    assert isinstance(r, Success)

    row_r = get_by_path("inbox/newcols.md", db_path=db)
    assert isinstance(row_r, Success)
    row = row_r.value
    assert row is not None
    assert row.project == "Alpha"
    assert row.status is None
    assert row.key_topics == ["quarterly-review"]


def test_upsert_clueless_binary_key_topics_empty(db):
    """Only type/attachment-summary tag → key_topics is empty list."""
    from storage.documents import get_by_path, upsert

    outcome = _outcome(
        "inbox/clueless.md",
        content_hash="hash_clueless",
        tags=["type/attachment-summary"],
    )
    r = upsert(outcome, db_path=db)
    assert isinstance(r, Success)

    row_r = get_by_path("inbox/clueless.md", db_path=db)
    assert isinstance(row_r, Success)
    row = row_r.value
    assert row is not None
    assert row.key_topics == []


def test_replace_path_preserves_new_columns(db):
    """replace_path after upsert → new path still has project, status, key_topics."""
    from storage.documents import get_by_path, replace_path, upsert

    # First upsert at old path
    outcome = _outcome(
        "inbox/old.md",
        content_hash="hash_rp",
        project="Alpha",
        status="active",
        tags=["domain/finance", "type/note", "quarterly-review"],
    )
    r = upsert(outcome, db_path=db)
    assert isinstance(r, Success)

    # Replace path
    new_outcome = _outcome(
        "inbox/new.md",
        content_hash="hash_rp",
        project="Alpha",
        status="active",
        tags=["domain/finance", "type/note", "quarterly-review"],
    )
    rp = replace_path("inbox/old.md", new_outcome, db_path=db)
    assert isinstance(rp, Success)

    # Old path should be gone
    old_r = get_by_path("inbox/old.md", db_path=db)
    assert isinstance(old_r, Success)
    assert old_r.value is None

    # New path should have all columns preserved
    new_r = get_by_path("inbox/new.md", db_path=db)
    assert isinstance(new_r, Success)
    row = new_r.value
    assert row is not None
    assert row.project == "Alpha"
    assert row.status == "active"
    assert row.key_topics == ["quarterly-review"]
