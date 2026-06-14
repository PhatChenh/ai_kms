"""Tests for migration 008 — knowledge_entries table + document columns."""

import sqlite3

from storage.db import init_db


def test_migration_008_creates_knowledge_entries_table(tmp_path):
    """Fresh init_db creates knowledge_entries table with all 11 columns."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_entries'"
    )
    assert cursor.fetchone() is not None, "knowledge_entries table should exist"

    # Verify all 11 columns
    columns = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_entries)")}
    expected = {
        "id",
        "dimension",
        "entity",
        "tag",
        "fact",
        "status",
        "confidence",
        "sources",
        "reasoning",
        "created_at",
        "updated_at",
        "trust_score",
        "retrieval_count",
    }
    assert columns == expected, f"Expected columns {expected}, got {columns}"

    conn.close()


def test_migration_008_sets_schema_version_to_8(tmp_path):
    """After init_db, schema_version reads 8."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 10, f"Expected schema_version 10, got {version}"
    conn.close()


def test_migration_008_document_columns_added(tmp_path):
    """After init_db, documents table has the 3 new columns, NULL for new rows."""
    from storage.documents import _row_from_sqlite

    db_path = tmp_path / "kb.db"
    init_db(db_path)

    # Insert a minimal documents row (simulating a pre-existing captured file)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO documents (vault_path, title, summary, note_type, confidence)
        VALUES (?, ?, ?, ?, ?)
    """,
        ("test/path.md", "Test Title", "Test summary", "note", 0.85),
    )
    conn.commit()

    # Verify the 3 new columns exist and are NULL
    row = conn.execute(
        "SELECT full_body, original_filename, file_size_bytes FROM documents WHERE vault_path = ?",
        ("test/path.md",),
    ).fetchone()
    assert row is not None
    assert row[0] is None, "full_body should be NULL"
    assert row[1] is None, "original_filename should be NULL"
    assert row[2] is None, "file_size_bytes should be NULL"

    # Read via _row_from_sqlite and verify it doesn't crash
    conn.row_factory = sqlite3.Row
    sqlite_row = conn.execute(
        "SELECT * FROM documents WHERE vault_path = ?", ("test/path.md",)
    ).fetchone()
    doc_row = _row_from_sqlite(sqlite_row)
    assert doc_row is not None
    assert doc_row.full_body is None
    assert doc_row.original_filename is None
    assert doc_row.file_size_bytes is None

    conn.close()
