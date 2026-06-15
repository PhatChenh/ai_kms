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


def _seed(
    vault_path: str = "inbox/foo.md",
    content_hash: str = "abc123",
    title: str | None = None,
    extracted_text: str = "dummy text",
    db: Path | None = None,
    **kwargs,
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


def _outcome(
    vault_path: str = "inbox/foo.md",
    content_hash: str = "abc123",
    **meta_kwargs,
) -> WriteOutcome:
    """Build a minimal WriteOutcome for _derive_title / replace_path tests."""
    return WriteOutcome(
        vault_path=vault_path,
        absolute_path=Path(f"/fake/vault/{vault_path}"),
        content_hash=content_hash,
        metadata=NoteMetadata(**meta_kwargs),
    )


# ---------------------------------------------------------------------------
# tests — basic CRUD (using upsert_from_upload as seeder)
# ---------------------------------------------------------------------------


def test_insert_and_get_by_path(db):
    """upsert_from_upload inserts a new row; get_by_path returns matching data."""
    from storage.documents import get_by_path

    _seed("inbox/foo.md", content_hash="hash1", db=db)

    row_r = get_by_path("inbox/foo.md", db_path=db)
    assert isinstance(row_r, Success)
    row = row_r.value
    assert row is not None
    assert row.vault_path == "inbox/foo.md"
    assert row.content_hash == "hash1"


def test_upsert_from_upload_replace_on_changed_hash(db):
    """Second upsert with same path but different hash updates the row."""
    from storage.documents import get_by_path, upsert_from_upload

    _seed("inbox/foo.md", content_hash="old_hash", db=db)
    upsert_from_upload(
        vault_path="inbox/foo.md",
        extracted_text="new text",
        content_hash="new_hash",
        db_path=db,
    )

    row_r = get_by_path("inbox/foo.md", db_path=db)
    assert isinstance(row_r, Success)
    assert row_r.value.content_hash == "new_hash"


def test_all_paths_returns_path_hash_pairs(db):
    """all_paths returns exactly the seeded (vault_path, content_hash) pairs."""
    from storage.documents import all_paths

    _seed("inbox/a.md", content_hash="h1", db=db)
    _seed("inbox/b.md", content_hash="h2", db=db)
    _seed("inbox/c.md", content_hash="h3", db=db)

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
    from storage.documents import delete_by_path, get_by_path

    _seed("inbox/foo.md", db=db)
    delete_by_path("inbox/foo.md", db_path=db)

    r = get_by_path("inbox/foo.md", db_path=db)
    assert isinstance(r, Success)
    assert r.value is None


def test_rename_updates_vault_path_preserves_id(db):
    """rename() changes vault_path and keeps the same row id (DECISION-001)."""
    from storage.documents import get_by_path, rename

    row_id = _seed("inbox/foo.md", content_hash="h", db=db)

    rename("inbox/foo.md", "projects/X/foo.md", db_path=db)

    old = get_by_path("inbox/foo.md", db_path=db)
    new = get_by_path("projects/X/foo.md", db_path=db)

    assert isinstance(old, Success)
    assert old.value is None  # old path gone

    assert isinstance(new, Success)
    assert new.value is not None
    assert new.value.id == row_id  # same row, just renamed


def test_update_batch_id_sets_value(db):
    """update_batch_id sets batch_id on the matching row."""
    from storage.batches import insert as insert_batch
    from storage.documents import get_by_path, update_batch_id

    # Create a batch row first — FK constraint enforced.
    br = insert_batch(
        folder_name="test-batch",
        destination_type=None,
        destination_name=None,
        confidence=1.0,
        status="ROUTING",
        file_count=1,
        db_path=db,
    )
    assert isinstance(br, Success)
    batch_id = br.value

    _seed("inbox/foo.md", content_hash="hash1", db=db)

    r2 = update_batch_id("inbox/foo.md", batch_id, db_path=db)
    assert isinstance(r2, Success)
    assert r2.value == 1

    row_r = get_by_path("inbox/foo.md", db_path=db)
    assert isinstance(row_r, Success)
    assert row_r.value is not None
    assert row_r.value.batch_id == batch_id


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


# ---------------------------------------------------------------------------
# Phase 7A — attach_summary (UPDATE-only DB writer)
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


# ---------------------------------------------------------------------------
# Phase Pre-2 — new columns existence check
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 7A — attach_summary (UPDATE-only DB writer)
# ---------------------------------------------------------------------------


def test_attach_summary_updates_summary_and_title_preserves_others(db):
    """attach_summary sets summary+title+updated_at; other columns unchanged."""
    from storage.documents import attach_summary, get_by_path, upsert_from_upload

    vault_path = "inbox/meeting.md"
    # Seed via upsert_from_upload (the raw-store beat)
    upsert_from_upload(
        vault_path=vault_path,
        extracted_text="Full meeting text here.",
        content_hash="hash_abc123",
        original_filename="meeting.md",
        file_size_bytes=42,
        db_path=db,
    )

    # Read the initial state
    before = get_by_path(vault_path, db_path=db)
    assert isinstance(before, Success)
    row_before = before.value
    assert row_before is not None
    assert row_before.summary is None
    assert row_before.title == "meeting"  # filename stem

    # Attach summary
    result = attach_summary(
        vault_path=vault_path,
        summary="## Overview\nTest summary content.",
        title="Q2 Strategy Review",
        db_path=db,
    )
    assert isinstance(result, Success)
    assert result.value == 1  # one row updated

    # Verify after
    after = get_by_path(vault_path, db_path=db)
    assert isinstance(after, Success)
    row_after = after.value
    assert row_after is not None

    # Changed columns
    assert row_after.summary == "## Overview\nTest summary content."
    assert row_after.title == "Q2 Strategy Review"
    # updated_at is second-granularity; both ops may be same second
    assert row_after.updated_at >= row_before.updated_at

    # Preserved columns
    assert row_after.full_body == "Full meeting text here."
    assert row_after.content_hash == "hash_abc123"
    assert row_after.original_filename == "meeting.md"
    assert row_after.file_size_bytes == 42
    assert row_after.vault_path == vault_path


def test_attach_summary_not_found_returns_zero(db):
    """attach_summary on nonexistent path returns Success(0)."""
    from storage.documents import attach_summary

    result = attach_summary(
        vault_path="nonexistent/note.md",
        summary="Summary",
        title="Title",
        db_path=db,
    )
    assert isinstance(result, Success)
    assert result.value == 0


def test_attach_summary_db_error(tmp_path):
    """attach_summary on non-existent DB returns Failure."""
    from storage.documents import attach_summary

    bad_db = tmp_path / "nonexistent" / "kb.db"
    result = attach_summary(
        vault_path="inbox/note.md",
        summary="Summary",
        title="Title",
        db_path=bad_db,
    )
    assert isinstance(result, Failure)
    assert result.recoverable is False


# ---------------------------------------------------------------------------
# Phase 5 — Work Finder + Classified-Stamp
# ---------------------------------------------------------------------------


def test_work_finder_returns_unclassified_and_stale(db: Path):
    """Work Finder returns ids where classify_content_hash is NULL or != content_hash."""
    import json

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        # Doc 1: classify_content_hash IS NULL (never classified)
        conn.execute(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, confidence,
                updated_at, updated_by_human, content_hash, key_topics,
                classify_content_hash)
               VALUES (?, ?, ?, ?, ?, datetime('now'), 0, ?, ?, NULL)""",
            ("inbox/doc1.md", "Doc1", "s", "note", 0.9, "hash_abc", json.dumps([])),
        )
        # Doc 2: classify_content_hash != content_hash (stale)
        conn.execute(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, confidence,
                updated_at, updated_by_human, content_hash, key_topics,
                classify_content_hash)
               VALUES (?, ?, ?, ?, ?, datetime('now'), 0, ?, ?, ?)""",
            ("inbox/doc2.md", "Doc2", "s", "note", 0.9, "hash_def", json.dumps([]), "old_hash"),
        )
        # Doc 3: classify_content_hash == content_hash (up-to-date, should NOT be returned)
        conn.execute(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, confidence,
                updated_at, updated_by_human, content_hash, key_topics,
                classify_content_hash)
               VALUES (?, ?, ?, ?, ?, datetime('now'), 0, ?, ?, ?)""",
            ("inbox/doc3.md", "Doc3", "s", "note", 0.9, "hash_ghi", json.dumps([]), "hash_ghi"),
        )
        conn.commit()
    finally:
        conn.close()

    from storage.documents import find_unclassified

    result = find_unclassified(db_path=db)
    assert isinstance(result, Success)
    ids = result.value
    # Doc 1 and Doc 2 should be returned; Doc 3 should not
    assert len(ids) == 2
    # Find the actual ids
    conn2 = sqlite3.connect(str(db))
    try:
        id1 = conn2.execute(
            "SELECT id FROM documents WHERE vault_path = 'inbox/doc1.md'"
        ).fetchone()[0]
        id2 = conn2.execute(
            "SELECT id FROM documents WHERE vault_path = 'inbox/doc2.md'"
        ).fetchone()[0]
    finally:
        conn2.close()
    assert set(ids) == {id1, id2}


def test_stamp_classified_removes_from_work_finder(db: Path):
    """After stamping a doc, Work Finder no longer returns it."""
    import json

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        # Doc A: NULL classify_content_hash
        conn.execute(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, confidence,
                updated_at, updated_by_human, content_hash, key_topics,
                classify_content_hash)
               VALUES (?, ?, ?, ?, ?, datetime('now'), 0, ?, ?, NULL)""",
            ("inbox/docA.md", "DocA", "s", "note", 0.9, "hash_a", json.dumps([])),
        )
        # Doc B: stale classify_content_hash (different from content_hash)
        conn.execute(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, confidence,
                updated_at, updated_by_human, content_hash, key_topics,
                classify_content_hash)
               VALUES (?, ?, ?, ?, ?, datetime('now'), 0, ?, ?, ?)""",
            ("inbox/docB.md", "DocB", "s", "note", 0.9, "hash_b", json.dumps([]), "old_b"),
        )
        conn.commit()
    finally:
        conn.close()

    from storage.documents import find_unclassified, stamp_classified

    # Get docA's id
    conn2 = sqlite3.connect(str(db))
    try:
        id_a = conn2.execute(
            "SELECT id FROM documents WHERE vault_path = 'inbox/docA.md'"
        ).fetchone()[0]
        id_b = conn2.execute(
            "SELECT id FROM documents WHERE vault_path = 'inbox/docB.md'"
        ).fetchone()[0]
    finally:
        conn2.close()

    # Both are unclassified now
    r1 = find_unclassified(db_path=db)
    assert isinstance(r1, Success)
    assert set(r1.value) == {id_a, id_b}

    # Stamp docA (the NULL one)
    r_stamp = stamp_classified(id_a, db_path=db)
    assert isinstance(r_stamp, Success)
    assert r_stamp.value == 1  # one row updated

    # Now Work Finder should only return docB (still unstamped)
    r2 = find_unclassified(db_path=db)
    assert isinstance(r2, Success)
    assert r2.value == [id_b], f"Expected only docB, got {r2.value}"


def test_stamp_classified_missing_id_returns_zero(db: Path):
    """Classified-Stamp on a missing id returns Success(0)."""
    from storage.documents import stamp_classified

    result = stamp_classified(99999, db_path=db)
    assert isinstance(result, Success)
    assert result.value == 0


def test_document_row_classify_content_hash_round_trips(db: Path):
    """DocumentRow.classify_content_hash survives a get_by_path round-trip."""
    import json

    from storage.documents import get_by_path

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, confidence,
                updated_at, updated_by_human, content_hash, key_topics,
                classify_content_hash)
               VALUES (?, ?, ?, ?, ?, datetime('now'), 0, ?, ?, ?)""",
            ("inbox/roundtrip.md", "RT", "s", "note", 0.9, "hash_rt", json.dumps([]), "classify_rt"),
        )
        conn.commit()
    finally:
        conn.close()

    row_r = get_by_path("inbox/roundtrip.md", db_path=db)
    assert isinstance(row_r, Success)
    assert row_r.value is not None
    assert row_r.value.classify_content_hash == "classify_rt"


def test_document_row_classify_content_hash_default():
    """DocumentRow without classify_content_hash → default is None."""
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
    assert row.classify_content_hash is None


def test_find_unclassified_empty_db(db: Path):
    """Work Finder on an empty DB returns an empty list."""
    from storage.documents import find_unclassified

    result = find_unclassified(db_path=db)
    assert isinstance(result, Success)
    assert result.value == []


def test_find_unclassified_db_error(tmp_path):
    """Work Finder on non-existent DB returns Failure."""
    from storage.documents import find_unclassified

    bad_db = tmp_path / "nonexistent" / "kb.db"
    result = find_unclassified(db_path=bad_db)
    assert isinstance(result, Failure)
    assert result.recoverable is False


# ---------------------------------------------------------------------------
# Phase 4 — Retry-state tests (Slice B)
# ---------------------------------------------------------------------------


def test_work_finder_excludes_needs_review(db: Path):
    """find_unclassified skips documents with status='needs-review' even
    when their classify fingerprint is NULL."""
    from storage.documents import find_unclassified

    conn = sqlite3.connect(str(db))
    # Seed: parked doc (NULL fingerprint + status=needs-review)
    conn.execute(
        """INSERT INTO documents (vault_path, title, status, content_hash)
           VALUES (?, ?, 'needs-review', ?)""",
        ("parked.md", "Parked", "hash1"),
    )
    # Seed: normal unclassified doc (NULL fingerprint, NULL status)
    conn.execute(
        """INSERT INTO documents (vault_path, title, content_hash)
           VALUES (?, ?, ?)""",
        ("normal.md", "Normal", "hash2"),
    )
    # Seed: stale fingerprint doc (non-NULL, mismatched)
    conn.execute(
        """INSERT INTO documents (vault_path, title, content_hash, classify_content_hash)
           VALUES (?, ?, ?, ?)""",
        ("stale.md", "Stale", "hash3", "oldhash"),
    )
    # Seed: already-classified doc (matching fingerprints)
    conn.execute(
        """INSERT INTO documents (vault_path, title, content_hash, classify_content_hash)
           VALUES (?, ?, ?, ?)""",
        ("done.md", "Done", "hash4", "hash4"),
    )
    conn.commit()
    conn.close()

    result = find_unclassified(db_path=db)
    assert isinstance(result, Success)
    ids = result.value
    # parked.md should NOT be in the list
    parked_id = _doc_id(db, "parked.md")
    assert parked_id not in ids, "needs-review doc should be excluded"
    # normal.md and stale.md SHOULD be in the list
    normal_id = _doc_id(db, "normal.md")
    stale_id = _doc_id(db, "stale.md")
    done_id = _doc_id(db, "done.md")
    assert normal_id in ids, "NULL-fingerprint doc should be included"
    assert stale_id in ids, "stale-fingerprint doc should be included"
    assert done_id not in ids, "matching-fingerprint doc should be excluded"


def test_document_row_classify_attempts_round_trips(db: Path):
    """classify_attempts field on DocumentRow is read from the DB."""
    from storage.documents import DocumentRow, get_by_path, _row_from_sqlite

    _seed(vault_path="test.md", content_hash="h1", db=db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE documents SET classify_attempts = 2 WHERE vault_path = ?",
        ("test.md",),
    )
    conn.commit()
    conn.close()

    result = get_by_path("test.md", db_path=db)
    assert isinstance(result, Success)
    row = result.value
    assert row is not None
    assert row.classify_attempts == 2


def test_document_row_classify_last_error_round_trips(db: Path):
    """classify_last_error field on DocumentRow is read from the DB."""
    from storage.documents import get_by_path

    _seed(vault_path="test.md", content_hash="h1", db=db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE documents SET classify_last_error = ? WHERE vault_path = ?",
        ("bad JSON", "test.md"),
    )
    conn.commit()
    conn.close()

    result = get_by_path("test.md", db_path=db)
    assert isinstance(result, Success)
    row = result.value
    assert row is not None
    assert row.classify_last_error == "bad JSON"


def test_document_row_retry_fields_default(db: Path):
    """New documents have classify_attempts=0, classify_last_error=None."""
    from storage.documents import get_by_path

    _seed(vault_path="test.md", content_hash="h1", db=db)
    result = get_by_path("test.md", db_path=db)
    assert isinstance(result, Success)
    row = result.value
    assert row is not None
    assert row.classify_attempts == 0
    assert row.classify_last_error is None


def test_record_classify_failure_increments_and_saves_error(db: Path):
    """record_classify_failure bumps attempts and stores the error string."""
    from storage.documents import record_classify_failure, load_classify_retry_state

    doc_id = _seed(vault_path="test.md", content_hash="h1", db=db)

    # First failure
    r1 = record_classify_failure(doc_id, "parse error", db_path=db)
    assert isinstance(r1, Success)
    assert r1.value == 1

    state = load_classify_retry_state(doc_id, db_path=db)
    assert isinstance(state, Success)
    attempts, error = state.value
    assert attempts == 1
    assert error == "parse error"

    # Second failure
    r2 = record_classify_failure(doc_id, "timeout", db_path=db)
    assert isinstance(r2, Success)
    state2 = load_classify_retry_state(doc_id, db_path=db)
    assert isinstance(state2, Success)
    assert state2.value[0] == 2
    assert state2.value[1] == "timeout"


def test_clear_classify_retry_state_resets_both(db: Path):
    """clear_classify_retry_state resets attempts to 0 and error to None."""
    from storage.documents import (
        clear_classify_retry_state,
        load_classify_retry_state,
        record_classify_failure,
    )

    doc_id = _seed(vault_path="test.md", content_hash="h1", db=db)
    record_classify_failure(doc_id, "some error", db_path=db)

    result = clear_classify_retry_state(doc_id, db_path=db)
    assert isinstance(result, Success)
    assert result.value == 1

    state = load_classify_retry_state(doc_id, db_path=db)
    assert isinstance(state, Success)
    assert state.value == (0, None)


def test_park_document_sets_needs_review_status(db: Path):
    """park_document sets status='needs-review'."""
    from storage.documents import park_document, get_by_path

    doc_id = _seed(vault_path="test.md", content_hash="h1", db=db)

    result = park_document(doc_id, db_path=db)
    assert isinstance(result, Success)
    assert result.value == 1

    row_result = get_by_path("test.md", db_path=db)
    assert isinstance(row_result, Success)
    row = row_result.value
    assert row is not None
    assert row.status == "needs-review"


def test_load_classify_retry_state_missing_id_returns_defaults(db: Path):
    """load_classify_retry_state for a non-existent id returns (0, None)."""
    from storage.documents import load_classify_retry_state

    result = load_classify_retry_state(99999, db_path=db)
    assert isinstance(result, Success)
    assert result.value == (0, None)


def test_record_classify_failure_missing_id_returns_zero(db: Path):
    """record_classify_failure for a non-existent id returns 0 rowcount."""
    from storage.documents import record_classify_failure

    result = record_classify_failure(99999, "error", db_path=db)
    assert isinstance(result, Success)
    assert result.value == 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _doc_id(db: Path, vault_path: str) -> int:
    """Return the id of a documents row by vault_path."""
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT id FROM documents WHERE vault_path = ?", (vault_path,)
    ).fetchone()
    conn.close()
    assert row is not None, f"No row for {vault_path}"
    return row[0]
