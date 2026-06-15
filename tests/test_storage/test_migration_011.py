"""Tests for migration 011 — classify_attempts and classify_last_error on documents."""

import sqlite3

from storage.db import init_db


def test_migration_011_sets_schema_version_to_11(tmp_path):
    """After init_db, schema_version reads 11."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 12, f"Expected schema_version 12, got {version}"
    conn.close()


def test_migration_011_adds_retry_state_columns(tmp_path):
    """Fresh init_db adds classify_attempts (INTEGER, default 0) and
    classify_last_error (TEXT, nullable) to documents."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    columns = {
        row[1]: row
        for row in conn.execute("PRAGMA table_info(documents)")
    }
    conn.close()

    # classify_attempts
    assert "classify_attempts" in columns, (
        f"classify_attempts missing; columns={list(columns.keys())}"
    )
    col = columns["classify_attempts"]
    assert col[2] == "INTEGER", (
        f"classify_attempts type should be INTEGER, got {col[2]}"
    )
    assert col[4] == "0", (
        f"classify_attempts default should be '0', got '{col[4]}'"
    )

    # classify_last_error
    assert "classify_last_error" in columns, (
        f"classify_last_error missing; columns={list(columns.keys())}"
    )
    col2 = columns["classify_last_error"]
    assert col2[2] == "TEXT", (
        f"classify_last_error type should be TEXT, got {col2[2]}"
    )
    # nullable: notnull == 0 means nullable in SQLite pragma
    assert col2[3] == 0, (
        f"classify_last_error should be nullable (NOT NULL=0), got notnull={col2[3]}"
    )


def test_migration_011_retry_state_defaults_on_insert(tmp_path):
    """Insert a documents row omitting the new columns and verify defaults."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO documents (vault_path, title)
        VALUES (?, ?)
        """,
        ("test.md", "Test"),
    )
    conn.commit()

    row = conn.execute(
        "SELECT classify_attempts, classify_last_error FROM documents WHERE vault_path = ?",
        ("test.md",),
    ).fetchone()
    assert row is not None
    assert row[0] == 0, f"classify_attempts default should be 0, got {row[0]}"
    assert row[1] is None, f"classify_last_error default should be None, got {row[1]}"

    conn.close()


def test_migration_011_preserves_existing_rows(tmp_path):
    """Pre-existing documents rows survive the migration with new columns at defaults."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    # Insert a document at version 10
    conn.execute(
        """
        INSERT INTO documents (vault_path, title, summary, note_type, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("doc1.md", "Doc One", "A summary", "note", 0.95),
    )
    conn.commit()
    conn.close()

    # Re-init (idempotent — migration 011 applies)
    init_db(db_path)

    conn2 = sqlite3.connect(str(db_path))
    doc_count = conn2.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert doc_count == 1, f"Documents row lost; count={doc_count}"

    doc = conn2.execute(
        "SELECT vault_path, title, classify_attempts, classify_last_error "
        "FROM documents WHERE vault_path = ?",
        ("doc1.md",),
    ).fetchone()
    assert doc is not None
    assert doc[0] == "doc1.md"
    assert doc[1] == "Doc One"
    assert doc[2] == 0, f"classify_attempts should be 0 for existing row, got {doc[2]}"
    assert doc[3] is None, f"classify_last_error should be None for existing row, got {doc[3]}"

    conn2.close()
