"""tests/test_storage/test_upsert_from_upload.py — RED phase for C2-1"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.result import Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Fresh temp database with the full schema applied."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _row_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


def _row_by_vault(db_path: Path, vault_path: str) -> sqlite3.Row | None:
    """Raw read to avoid depending on the function under test for assertions."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM documents WHERE vault_path = ?", (vault_path,)
        ).fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# tests — new path (insert)
# ---------------------------------------------------------------------------


def test_new_path_inserts_row(db):
    """A never-seen vault_path inserts one documents row with all upload fields set.

    P5-DEPLOY-03
    """
    from storage.documents import upsert_from_upload

    result = upsert_from_upload(
        vault_path="uploads/report.pdf",
        extracted_text="Full extracted text content",
        content_hash="abc123",
        original_filename="report.pdf",
        file_size_bytes=1024,
        db_path=db,
    )

    # Returns an int id
    assert isinstance(result, Success)
    assert isinstance(result.value, int)
    new_id = result.value

    # Exactly one row exists
    row = _row_by_vault(db, "uploads/report.pdf")
    assert row is not None
    assert row["id"] == new_id
    assert row["full_body"] == "Full extracted text content"
    assert row["original_filename"] == "report.pdf"
    assert row["file_size_bytes"] == 1024
    assert row["content_hash"] == "abc123"
    # Title derived from vault_path stem
    assert row["title"] == "report"

    # Single row in the table
    conn = sqlite3.connect(str(db))
    try:
        assert _row_count(conn) == 1
    finally:
        conn.close()


def test_new_path_accepts_explicit_title(db):
    """When title is provided, it overrides stem derivation."""
    from storage.documents import upsert_from_upload

    upsert_from_upload(
        vault_path="inbox/note.md",
        extracted_text="body",
        content_hash="hash1",
        title="Explicit Title",
        db_path=db,
    )

    row = _row_by_vault(db, "inbox/note.md")
    assert row is not None
    assert row["title"] == "Explicit Title"


def test_new_path_nullable_fields_omitted(db):
    """original_filename and file_size_bytes can be None / omitted."""
    from storage.documents import upsert_from_upload

    result = upsert_from_upload(
        vault_path="inbox/minimal.md",
        extracted_text="minimal",
        content_hash="minhash",
        db_path=db,
    )

    assert isinstance(result, Success)
    row = _row_by_vault(db, "inbox/minimal.md")
    assert row is not None
    assert row["original_filename"] is None
    assert row["file_size_bytes"] is None


# ---------------------------------------------------------------------------
# tests — same path, same hash (skip)
# ---------------------------------------------------------------------------


def test_same_hash_skips_write(db):
    """Same vault_path + same content_hash → no row change, same id returned.

    P5-DEPLOY-04
    """
    from storage.documents import upsert_from_upload

    first = upsert_from_upload(
        vault_path="inbox/unchanged.md",
        extracted_text="content v1",
        content_hash="hash_same",
        db_path=db,
    )
    first_id = first.value

    # Second call with identical hash
    second = upsert_from_upload(
        vault_path="inbox/unchanged.md",
        extracted_text="content v1",
        content_hash="hash_same",
        db_path=db,
    )

    assert isinstance(second, Success)
    assert second.value == first_id  # same id returned

    # Exactly one row
    conn = sqlite3.connect(str(db))
    try:
        assert _row_count(conn) == 1
    finally:
        conn.close()

    # Row content still the original text
    row = _row_by_vault(db, "inbox/unchanged.md")
    assert row is not None
    assert row["full_body"] == "content v1"


def test_same_hash_ignores_different_body_text(db):
    """Even if caller passes different extracted_text, same hash → skip (hash is truth)."""
    from storage.documents import upsert_from_upload

    upsert_from_upload(
        vault_path="inbox/hash_wins.md",
        extracted_text="original body",
        content_hash="hash_xyz",
        db_path=db,
    )

    # Second call: same hash, different body
    upsert_from_upload(
        vault_path="inbox/hash_wins.md",
        extracted_text="different body — should NOT be stored",
        content_hash="hash_xyz",
        db_path=db,
    )

    row = _row_by_vault(db, "inbox/hash_wins.md")
    assert row is not None
    assert row["full_body"] == "original body"


# ---------------------------------------------------------------------------
# tests — same path, different hash (update)
# ---------------------------------------------------------------------------


def test_different_hash_updates_in_place(db):
    """Same vault_path + different content_hash → UPDATE existing row, same id.

    P5-DEPLOY-05
    """
    from storage.documents import upsert_from_upload

    first = upsert_from_upload(
        vault_path="inbox/changing.md",
        extracted_text="version one",
        content_hash="hash_v1",
        original_filename="file_v1.txt",
        db_path=db,
    )
    first_id = first.value

    second = upsert_from_upload(
        vault_path="inbox/changing.md",
        extracted_text="version two",
        content_hash="hash_v2",
        original_filename="file_v2.txt",
        file_size_bytes=2048,
        db_path=db,
    )

    assert isinstance(second, Success)
    assert second.value == first_id  # same row id, updated in place

    # Exactly one row
    conn = sqlite3.connect(str(db))
    try:
        assert _row_count(conn) == 1
    finally:
        conn.close()

    # Row reflects new data
    row = _row_by_vault(db, "inbox/changing.md")
    assert row is not None
    assert row["id"] == first_id
    assert row["full_body"] == "version two"
    assert row["content_hash"] == "hash_v2"
    assert row["original_filename"] == "file_v2.txt"
    assert row["file_size_bytes"] == 2048


def test_update_preserves_existing_title(db):
    """UPDATE path keeps the existing title unless caller overrides it."""
    from storage.documents import upsert_from_upload

    # Insert with an explicit title
    upsert_from_upload(
        vault_path="inbox/keep_title.md",
        extracted_text="original",
        content_hash="h1",
        title="Original Title",
        db_path=db,
    )

    # Update with different hash, no title provided
    upsert_from_upload(
        vault_path="inbox/keep_title.md",
        extracted_text="updated",
        content_hash="h2",
        db_path=db,
    )

    row = _row_by_vault(db, "inbox/keep_title.md")
    assert row is not None
    assert row["full_body"] == "updated"
    assert row["title"] == "Original Title"


def test_update_can_override_title(db):
    """UPDATE can change title when explicitly provided."""
    from storage.documents import upsert_from_upload

    upsert_from_upload(
        vault_path="inbox/override_title.md",
        extracted_text="v1",
        content_hash="h1",
        title="First Title",
        db_path=db,
    )

    upsert_from_upload(
        vault_path="inbox/override_title.md",
        extracted_text="v2",
        content_hash="h2",
        title="Second Title",
        db_path=db,
    )

    row = _row_by_vault(db, "inbox/override_title.md")
    assert row is not None
    assert row["title"] == "Second Title"


def test_insert_with_blob_ref_and_mime_type(db):
    """INSERT with blob_ref and mime_type writes both columns."""
    from storage.documents import upsert_from_upload

    result = upsert_from_upload(
        vault_path="Projects/A/attachment/img.png",
        extracted_text=None,
        content_hash="hash_blob_001",
        blob_ref="hash_blob_001",
        mime_type="image/png",
        db_path=db,
    )
    assert isinstance(result, Success)
    row = _row_by_vault(db, "Projects/A/attachment/img.png")
    assert row is not None
    assert row["blob_ref"] == "hash_blob_001"
    assert row["mime_type"] == "image/png"
    assert row["full_body"] is None


def test_update_with_blob_ref_and_mime_type(db):
    """UPDATE path preserves blob_ref and mime_type when re-upload happens."""
    from storage.documents import upsert_from_upload

    # Insert binary row
    upsert_from_upload(
        vault_path="Projects/A/attachment/img2.png",
        extracted_text=None,
        content_hash="hash_blob_first",
        blob_ref="hash_blob_first",
        mime_type="image/png",
        db_path=db,
    )

    # Update with same path, different hash
    upsert_from_upload(
        vault_path="Projects/A/attachment/img2.png",
        extracted_text=None,
        content_hash="hash_blob_second",
        blob_ref="hash_blob_second",
        mime_type="image/jpeg",
        db_path=db,
    )

    row = _row_by_vault(db, "Projects/A/attachment/img2.png")
    assert row is not None
    assert row["blob_ref"] == "hash_blob_second"
    assert row["mime_type"] == "image/jpeg"
    assert row["content_hash"] == "hash_blob_second"


def test_text_upload_without_blob_ref_has_nulls(db):
    """Text upload without blob_ref/mime_type parameters leaves both NULL."""
    from storage.documents import upsert_from_upload

    upsert_from_upload(
        vault_path="inbox/text_only.md",
        extracted_text="Hello world",
        content_hash="hash_text",
        db_path=db,
    )

    row = _row_by_vault(db, "inbox/text_only.md")
    assert row is not None
    assert row["blob_ref"] is None
    assert row["mime_type"] is None
    assert row["full_body"] == "Hello world"
