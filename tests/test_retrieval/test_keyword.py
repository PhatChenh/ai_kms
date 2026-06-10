"""Tests for the word indexer (retrieval/keyword.py)."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from storage.db import _connect, init_db


@pytest.fixture
def search_db(tmp_path: Path) -> Path:
    """Create a temp DB with migration 007 applied (embeddings_vec + notes_fts)."""
    db_path = tmp_path / "test_search.db"
    result = init_db(db_path)
    assert result.is_success(), f"init_db failed: {result}"
    return db_path


class TestIndexKeywordsInsert:
    """Tracer bullet: insert a row and verify it's findable via FTS5 MATCH."""

    def test_insert_creates_row_findable_by_fts5_match(self, search_db: Path) -> None:
        from retrieval.keyword import index_keywords

        result = index_keywords(
            vault_path="Projects/Alpha/note.md",
            title="Stakeholder Update",
            summary="Meeting about stakeholders.",
            body="We discussed the stakeholder requirements for Q2.",
            db_path=search_db,
        )

        assert result.is_success(), f"Expected Success, got {result}"

        conn = _connect(search_db)
        try:
            row = conn.execute(
                "SELECT vault_path FROM notes_fts WHERE notes_fts MATCH ?",
                ("stakeholder",),
            ).fetchone()
            assert row is not None, "Expected a row matching 'stakeholder'"
            assert row[0] == "Projects/Alpha/note.md"
        finally:
            conn.close()


class TestIndexKeywordsUpsert:
    """DELETE+INSERT: calling twice with same vault_path leaves exactly one row."""

    def test_calling_twice_with_same_path_leaves_one_row(self, search_db: Path) -> None:
        from retrieval.keyword import index_keywords

        # First call
        r1 = index_keywords(
            vault_path="Projects/Beta/doc.md",
            title="First Title",
            summary="First summary.",
            body="First body content here.",
            db_path=search_db,
        )
        assert r1.is_success()

        # Second call -- same vault_path, different content
        r2 = index_keywords(
            vault_path="Projects/Beta/doc.md",
            title="Second Title",
            summary="Second summary.",
            body="Second body content here.",
            db_path=search_db,
        )
        assert r2.is_success()

        conn = _connect(search_db)
        try:
            rows = conn.execute(
                "SELECT vault_path FROM notes_fts WHERE vault_path = ?",
                ("Projects/Beta/doc.md",),
            ).fetchall()
            assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        finally:
            conn.close()


class TestFts5Match:
    """P3-IDX-02: a distinctive keyword in the body is findable via FTS5 MATCH."""

    def test_distinctive_keyword_in_body_is_findable(self, search_db: Path) -> None:
        from retrieval.keyword import index_keywords

        result = index_keywords(
            vault_path="inbox/meeting.md",
            title="Weekly Sync",
            summary="Regular team sync.",
            body="Discussed budget allocation for the new initiative. "
            "The zebra pattern was noted as an anomaly in the data.",
            db_path=search_db,
        )

        assert result.is_success()

        conn = _connect(search_db)
        try:
            # The distinctive word "zebra" should match
            row = conn.execute(
                "SELECT vault_path FROM notes_fts WHERE notes_fts MATCH ?",
                ("zebra",),
            ).fetchone()
            assert row is not None, "Expected 'zebra' to match in body"
            assert row[0] == "inbox/meeting.md"

            # Common word "budget" should also match
            row2 = conn.execute(
                "SELECT vault_path FROM notes_fts WHERE notes_fts MATCH ?",
                ("budget",),
            ).fetchone()
            assert row2 is not None, "Expected 'budget' to match in body"
            assert row2[0] == "inbox/meeting.md"

            # Non-existent word should not match
            row3 = conn.execute(
                "SELECT vault_path FROM notes_fts WHERE notes_fts MATCH ?",
                ("nonexistent",),
            ).fetchone()
            assert row3 is None, "Expected no match for 'nonexistent'"
        finally:
            conn.close()


class TestRetryOnOperationalError:
    """Single OperationalError is retried once and recovers."""

    def test_retry_succeeds_after_single_operational_error(
        self, search_db: Path
    ) -> None:
        import retrieval.keyword as kw
        from storage.db import get_connection as real_get_connection

        call_count = [0]

        def side_effect(*args: object, **kwargs: object):
            call_count[0] += 1
            if call_count[0] == 1:
                raise sqlite3.OperationalError("simulated transient error")
            return real_get_connection(*args, **kwargs)

        with patch.object(kw, "get_connection", side_effect=side_effect):
            result = kw.index_keywords(
                vault_path="retry.md",
                title="Retry Test",
                summary="Testing retry.",
                body="This is a retry test body.",
                db_path=search_db,
            )

        assert result.is_success(), f"Expected Success after retry, got {result}"
        assert call_count[0] == 2, f"Expected 2 calls, got {call_count[0]}"

        # Verify the row exists after successful retry
        conn = _connect(search_db)
        try:
            row = conn.execute(
                "SELECT vault_path FROM notes_fts WHERE notes_fts MATCH ?",
                ("retry",),
            ).fetchone()
            assert row is not None, "Expected row after retry succeeded"
        finally:
            conn.close()

    def test_double_failure_returns_recoverable_failure(self, search_db: Path) -> None:
        import retrieval.keyword as kw

        def always_raise(*args: object, **kwargs: object):
            raise sqlite3.OperationalError("persistent error")

        with patch.object(kw, "get_connection", side_effect=always_raise):
            result = kw.index_keywords(
                vault_path="fail.md",
                title="Fail",
                summary="Failing.",
                body="This will fail.",
                db_path=search_db,
            )

        assert result.is_failure(), "Expected Failure after two failures"
        assert result.recoverable is True
