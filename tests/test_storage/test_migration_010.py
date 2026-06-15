"""Tests for migration 010 — classify_content_hash on documents, trust_score and
retrieval_count on knowledge_entries, plus supporting indexes."""

import sqlite3

from storage.db import init_db


def test_migration_010_sets_schema_version_to_10(tmp_path):
    """After init_db, schema_version reads 10."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 12, f"Expected schema_version 12, got {version}"
    conn.close()


def test_migration_010_adds_classify_content_hash_column(tmp_path):
    """Fresh init_db adds nullable classify_content_hash to documents."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    columns = {
        row[1]: row
        for row in conn.execute("PRAGMA table_info(documents)")
    }
    conn.close()

    assert "classify_content_hash" in columns, (
        f"classify_content_hash missing; columns={list(columns.keys())}"
    )
    col = columns["classify_content_hash"]
    assert col[2] == "TEXT", (
        f"classify_content_hash type should be TEXT, got {col[2]}"
    )
    # nullable: notnull == 0 means nullable in SQLite pragma
    assert col[3] == 0, (
        f"classify_content_hash should be nullable (NOT NULL=0), got notnull={col[3]}"
    )


def test_migration_010_adds_trust_score_and_retrieval_count_columns(tmp_path):
    """Fresh init_db adds trust_score (REAL, default 0.5) and retrieval_count
    (INTEGER, default 0) to knowledge_entries."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    columns = {
        row[1]: row
        for row in conn.execute("PRAGMA table_info(knowledge_entries)")
    }
    conn.close()

    assert "trust_score" in columns, (
        f"trust_score missing; columns={list(columns.keys())}"
    )
    assert "retrieval_count" in columns, (
        f"retrieval_count missing; columns={list(columns.keys())}"
    )

    trust = columns["trust_score"]
    assert trust[2] == "REAL", (
        f"trust_score type should be REAL, got {trust[2]}"
    )
    assert trust[4] == "0.5", (
        f"trust_score default should be '0.5', got '{trust[4]}'"
    )

    retrieval = columns["retrieval_count"]
    assert retrieval[2] == "INTEGER", (
        f"retrieval_count type should be INTEGER, got {retrieval[2]}"
    )
    assert retrieval[4] == "0", (
        f"retrieval_count default should be '0', got '{retrieval[4]}'"
    )


def test_migration_010_insert_defaults_for_trust_score_and_retrieval_count(tmp_path):
    """Insert a knowledge_entries row omitting the new columns and verify defaults."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO knowledge_entries
            (dimension, entity, tag, fact, status, confidence, sources, reasoning)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("test-dim", "test-entity", "test-tag", "test-fact",
         "active", 0.9, "[]", "no reasoning"),
    )
    conn.commit()

    row = conn.execute(
        "SELECT trust_score, retrieval_count FROM knowledge_entries WHERE entity = ?",
        ("test-entity",),
    ).fetchone()
    assert row is not None
    assert row[0] == 0.5, f"trust_score default should be 0.5, got {row[0]}"
    assert row[1] == 0, f"retrieval_count default should be 0, got {row[1]}"

    conn.close()


def test_migration_010_indexes_exist(tmp_path):
    """After init_db, idx_docs_classify_hash and idx_ke_trust indexes exist."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    indexes = {
        r[1]
        for r in conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    conn.close()

    assert "idx_docs_classify_hash" in indexes, (
        f"idx_docs_classify_hash missing; indexes={indexes}"
    )
    assert "idx_ke_trust" in indexes, (
        f"idx_ke_trust missing; indexes={indexes}"
    )


def test_migration_010_preserves_existing_rows(tmp_path):
    """Pre-existing documents and knowledge_entries rows survive the migration intact."""
    db_path = tmp_path / "kb.db"

    # First, create a DB at version 9 (apply all migrations up to 009).
    # We'll insert rows, then re-init to trigger migration 010, and verify.
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    # Insert a document
    conn.execute(
        """
        INSERT INTO documents (vault_path, title, summary, note_type, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("doc1.md", "Doc One", "A summary", "note", 0.95),
    )
    # Insert a knowledge entry
    conn.execute(
        """
        INSERT INTO knowledge_entries
            (dimension, entity, tag, fact, status, confidence, sources, reasoning)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("dim", "ent", "tag", "fact", "active", 0.8, "[]", "because"),
    )
    conn.commit()

    # Verify inserted rows exist before re-init
    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    ke_count = conn.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    assert doc_count == 1
    assert ke_count == 1

    conn.close()

    # Re-init (idempotent — should apply migration 010 if not yet applied)
    init_db(db_path)

    conn2 = sqlite3.connect(str(db_path))
    doc_count2 = conn2.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    ke_count2 = conn2.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    assert doc_count2 == 1, f"Documents row lost; count={doc_count2}"
    assert ke_count2 == 1, f"Knowledge entries row lost; count={ke_count2}"

    # Verify the document row is intact
    doc = conn2.execute(
        "SELECT vault_path, title, summary, note_type, confidence FROM documents WHERE vault_path = ?",
        ("doc1.md",),
    ).fetchone()
    assert doc is not None
    assert doc[0] == "doc1.md"
    assert doc[1] == "Doc One"
    assert doc[2] == "A summary"
    assert doc[3] == "note"
    assert doc[4] == 0.95
    # classify_content_hash should be NULL for existing row
    doc_hash = conn2.execute(
        "SELECT classify_content_hash FROM documents WHERE vault_path = ?",
        ("doc1.md",),
    ).fetchone()[0]
    assert doc_hash is None

    # Verify the knowledge entry row is intact and new columns have defaults
    ke = conn2.execute(
        "SELECT dimension, entity, tag, fact, trust_score, retrieval_count "
        "FROM knowledge_entries WHERE entity = ?",
        ("ent",),
    ).fetchone()
    assert ke is not None
    assert ke[0] == "dim"
    assert ke[1] == "ent"
    assert ke[2] == "tag"
    assert ke[3] == "fact"
    assert ke[4] == 0.5, f"trust_score should be 0.5, got {ke[4]}"
    assert ke[5] == 0, f"retrieval_count should be 0, got {ke[5]}"

    conn2.close()
