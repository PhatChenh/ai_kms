"""Tests for migration 009 — blob_ref and mime_type columns."""

import sqlite3

from storage.db import init_db


def test_migration_009_adds_blob_ref_and_mime_type_columns(tmp_path):
    """Fresh init_db adds blob_ref and mime_type columns to documents table."""
    db_path = tmp_path / "kb.db"
    result = init_db(db_path)
    assert result.is_success()

    conn = sqlite3.connect(str(db_path))
    columns = {
        row[1]: row[2]
        for row in conn.execute("PRAGMA table_info(documents)")
    }
    conn.close()

    assert "blob_ref" in columns, f"blob_ref missing; columns={columns}"
    assert columns["blob_ref"] == "TEXT", (
        f"blob_ref type should be TEXT, got {columns.get('blob_ref')}"
    )
    assert "mime_type" in columns, f"mime_type missing; columns={columns}"
    assert columns["mime_type"] == "TEXT", (
        f"mime_type type should be TEXT, got {columns.get('mime_type')}"
    )


def test_migration_009_sets_schema_version_to_9(tmp_path):
    """After init_db, schema_version reads 9."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 12, f"Expected schema_version 12, got {version}"
    conn.close()


def test_migration_009_document_columns_null_for_new_rows(tmp_path):
    """After init_db, blob_ref and mime_type are NULL for new rows."""
    from storage.documents import _row_from_sqlite

    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO documents (vault_path, title, summary, note_type, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("test/path.md", "Test Title", "Test summary", "note", 0.85),
    )
    conn.commit()

    row = conn.execute(
        "SELECT blob_ref, mime_type FROM documents WHERE vault_path = ?",
        ("test/path.md",),
    ).fetchone()
    assert row is not None
    assert row[0] is None, "blob_ref should be NULL"
    assert row[1] is None, "mime_type should be NULL"

    # Read via _row_from_sqlite and verify it doesn't crash
    conn.row_factory = sqlite3.Row
    sqlite_row = conn.execute(
        "SELECT * FROM documents WHERE vault_path = ?", ("test/path.md",)
    ).fetchone()
    doc_row = _row_from_sqlite(sqlite_row)
    assert doc_row is not None
    assert doc_row.blob_ref is None
    assert doc_row.mime_type is None

    conn.close()
