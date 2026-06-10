"""tests/test_storage/test_documents_search.py — Phase 4: search-table cleanup tests.

P3-IDX-05: delete_by_path cleans up embeddings_vec and notes_fts.
P3-IDX-06: rename copies search-table rows from old to new vault_path.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from storage.db import get_connection, init_db
from vault.frontmatter import NoteMetadata
from vault.writer import WriteOutcome


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fake_embedding() -> bytes:
    """384 float32 values as a blob (1536 bytes)."""
    return struct.pack("f" * 384, *([0.1] * 384))


def _outcome(
    vault_path: str = "inbox/foo.md",
    content_hash: str = "abc123",
    **meta_kwargs,
) -> WriteOutcome:
    return WriteOutcome(
        vault_path=vault_path,
        absolute_path=Path(f"/fake/vault/{vault_path}"),
        content_hash=content_hash,
        metadata=NoteMetadata(**meta_kwargs),
    )


def _insert_search_rows(db_path: Path, vault_path: str) -> None:
    """Insert a row into both embeddings_vec and notes_fts for vault_path."""
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO embeddings_vec(vault_path, embedding) VALUES (?, ?)",
            (vault_path, _fake_embedding()),
        )
        conn.execute(
            "INSERT INTO notes_fts(vault_path, title, summary, body) "
            "VALUES (?, ?, ?, ?)",
            (vault_path, "Test Title", "Test Summary", "Test Body"),
        )


def _count_embeddings(db_path: Path, vault_path: str) -> int:
    with get_connection(db_path, readonly=True) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM embeddings_vec WHERE vault_path = ?",
            (vault_path,),
        ).fetchone()
        return row[0]


def _count_fts(db_path: Path, vault_path: str) -> int:
    with get_connection(db_path, readonly=True) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM notes_fts WHERE vault_path = ?",
            (vault_path,),
        ).fetchone()
        return row[0]


def _get_embedding(db_path: Path, vault_path: str) -> bytes | None:
    with get_connection(db_path, readonly=True) as conn:
        row = conn.execute(
            "SELECT embedding FROM embeddings_vec WHERE vault_path = ?",
            (vault_path,),
        ).fetchone()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# P3-IDX-05: delete_by_path cleans up search tables
# ---------------------------------------------------------------------------


def test_delete_by_path_cleans_up_embeddings_vec(db):
    """After delete_by_path(), embeddings_vec has zero rows for that vault_path."""
    from storage.documents import delete_by_path, upsert

    upsert(_outcome("inbox/foo.md"), db_path=db)
    _insert_search_rows(db, "inbox/foo.md")

    assert _count_embeddings(db, "inbox/foo.md") == 1
    assert _count_fts(db, "inbox/foo.md") == 1

    r = delete_by_path("inbox/foo.md", db_path=db)
    assert r.is_success()

    assert _count_embeddings(db, "inbox/foo.md") == 0
    assert _count_fts(db, "inbox/foo.md") == 0


def test_delete_by_path_search_tables_empty_no_error(db):
    """delete_by_path when search tables have no row for that path — no error."""
    from storage.documents import delete_by_path, upsert

    upsert(_outcome("inbox/bar.md"), db_path=db)
    # Do NOT insert into search tables — they are empty for this path.

    r = delete_by_path("inbox/bar.md", db_path=db)
    assert r.is_success()
    assert r.value == 1  # documents row deleted

    # Search tables still empty (no error raised).
    assert _count_embeddings(db, "inbox/bar.md") == 0
    assert _count_fts(db, "inbox/bar.md") == 0


# ---------------------------------------------------------------------------
# P3-IDX-06: rename copies search-table rows
# ---------------------------------------------------------------------------


def test_rename_preserves_embedding_and_copies_fts(db):
    """After rename(), old path empty, new path has embedding + FTS row, bytes match."""
    from storage.documents import rename, upsert

    upsert(_outcome("inbox/old.md"), db_path=db)
    _insert_search_rows(db, "inbox/old.md")

    # Snapshot the embedding before rename.
    old_embedding = _get_embedding(db, "inbox/old.md")
    assert old_embedding is not None
    assert len(old_embedding) == 1536

    r = rename("inbox/old.md", "Projects/A/new.md", db_path=db)
    assert r.is_success()

    # Old path: zero rows in both search tables.
    assert _count_embeddings(db, "inbox/old.md") == 0
    assert _count_fts(db, "inbox/old.md") == 0

    # New path: one row in each search table.
    assert _count_embeddings(db, "Projects/A/new.md") == 1
    assert _count_fts(db, "Projects/A/new.md") == 1

    # Embedding bytes preserved.
    new_embedding = _get_embedding(db, "Projects/A/new.md")
    assert new_embedding == old_embedding


def test_rename_search_tables_empty_no_error(db):
    """rename when search tables have no row for that path — no error."""
    from storage.documents import rename, upsert

    upsert(_outcome("inbox/noseek.md"), db_path=db)
    # Do NOT insert into search tables.

    r = rename("inbox/noseek.md", "Projects/B/noseek.md", db_path=db)
    assert r.is_success()
    assert r.value == 1  # documents row renamed

    assert _count_embeddings(db, "inbox/noseek.md") == 0
    assert _count_fts(db, "inbox/noseek.md") == 0
    assert _count_embeddings(db, "Projects/B/noseek.md") == 0
    assert _count_fts(db, "Projects/B/noseek.md") == 0


# ---------------------------------------------------------------------------
# replace_path cleanup
# ---------------------------------------------------------------------------


def test_replace_path_cleans_up_search_tables(db):
    """After replace_path(), old path search entries are deleted."""
    from storage.documents import replace_path, upsert

    upsert(_outcome("inbox/old.md", content_hash="h1"), db_path=db)
    _insert_search_rows(db, "inbox/old.md")

    assert _count_embeddings(db, "inbox/old.md") == 1
    assert _count_fts(db, "inbox/old.md") == 1

    new_outcome = _outcome("inbox/new.md", content_hash="h2")
    r = replace_path("inbox/old.md", new_outcome, db_path=db)
    assert r.is_success()

    # Old path search entries deleted.
    assert _count_embeddings(db, "inbox/old.md") == 0
    assert _count_fts(db, "inbox/old.md") == 0

    # New path has no search entries (those come from pipeline indexing).
    assert _count_embeddings(db, "inbox/new.md") == 0
    assert _count_fts(db, "inbox/new.md") == 0
