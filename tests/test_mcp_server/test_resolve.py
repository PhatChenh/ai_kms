"""tests/test_mcp_server/test_resolve.py — Three-Tier DB Resolver

TDD for P9-D-01: resolve() with summary, text, and file modes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from storage.db import init_db, get_connection
from core.result import Success, Failure
from mcp_server._resolve import resolve, ResolveResult


# ============================================================================
# Shared fixture
# ============================================================================


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Fresh temp database with the full schema applied."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    return db_path


# ============================================================================
# Test helpers
# ============================================================================


_counter: int = 0


def _insert_row(
    db_path: Path,
    vault_path: str | None = None,
    title: str = "Test Note",
    summary: str | None = "A test summary.",
    full_body: str | None = "Full body text content.",
) -> int:
    """Insert a minimal document row and return its id.

    If vault_path is not provided, a unique one is generated.
    """
    global _counter
    _counter += 1
    if vault_path is None:
        vault_path = f"test/note_{_counter}.md"
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO documents (vault_path, title, summary, full_body)
            VALUES (?, ?, ?, ?)
            """,
            (vault_path, title, summary, full_body),
        )
        return cur.lastrowid


# ============================================================================
# RED 1 — resolve([42], "summary") returns summary text
# ============================================================================


class TestResolveSummary:
    """resolve() in summary mode returns row.summary."""

    def test_summary_mode_returns_summary(self, db: Path):
        """Given a document with summary, resolve returns it with degraded=False."""
        doc_id = _insert_row(db, summary="The summary.", title="Doc A")
        result = resolve([doc_id], "summary", db_path=db)

        assert isinstance(result, Success)
        assert len(result.value) == 1
        entry = result.value[0]
        assert entry.doc_id == doc_id
        assert entry.mode == "summary"
        assert entry.content == "The summary."
        assert entry.title == "Doc A"
        assert entry.degraded is False

    def test_summary_mode_null_summary_returns_placeholder(self, db: Path):
        """Given a document with NULL summary, resolve returns placeholder."""
        doc_id = _insert_row(db, summary=None, title="Doc B")
        result = resolve([doc_id], "summary", db_path=db)

        assert isinstance(result, Success)
        assert len(result.value) == 1
        entry = result.value[0]
        assert entry.content == "[Summary pending]"
        assert entry.degraded is False

    def test_summary_mode_multiple_docs(self, db: Path):
        """Resolve multiple doc_ids in summary mode returns all found."""
        id1 = _insert_row(db, summary="S1", title="T1", vault_path="test/doc_a.md")
        id2 = _insert_row(db, summary="S2", title="T2", vault_path="test/doc_b.md")
        result = resolve([id1, id2], "summary", db_path=db)

        assert isinstance(result, Success)
        assert len(result.value) == 2
        assert result.value[0].content == "S1"
        assert result.value[1].content == "S2"


# ============================================================================
# RED 2 — resolve([42], "text") returns full_body
# ============================================================================


class TestResolveText:
    """resolve() in text mode returns row.full_body."""

    def test_text_mode_returns_full_body(self, db: Path):
        """Given a document with full_body, text mode returns it."""
        doc_id = _insert_row(
            db, full_body="Complete body text.", summary="Summary.", title="Doc C"
        )
        result = resolve([doc_id], "text", db_path=db)

        assert isinstance(result, Success)
        assert len(result.value) == 1
        entry = result.value[0]
        assert entry.mode == "text"
        assert entry.content == "Complete body text."
        assert entry.title == "Doc C"
        assert entry.degraded is False

    def test_text_mode_null_full_body_degrades(self, db: Path):
        """Given a document with NULL full_body, text mode degrades to summary."""
        doc_id = _insert_row(
            db, full_body=None, summary="Only summary.", title="Doc D"
        )
        result = resolve([doc_id], "text", db_path=db)

        assert isinstance(result, Success)
        assert len(result.value) == 1
        entry = result.value[0]
        assert entry.content == "Only summary."
        assert entry.degraded is True

    def test_text_mode_null_full_body_null_summary(self, db: Path):
        """Given NULL full_body AND NULL summary, text mode returns placeholder."""
        doc_id = _insert_row(
            db, full_body=None, summary=None, title="Doc E"
        )
        result = resolve([doc_id], "text", db_path=db)

        assert isinstance(result, Success)
        assert len(result.value) == 1
        entry = result.value[0]
        assert entry.content == "[Summary pending]"
        assert entry.degraded is True


# ============================================================================
# RED 3 — resolve([42], "file") returns vault_path
# ============================================================================


class TestResolveFile:
    """resolve() in file mode returns vault_path."""

    def test_file_mode_returns_vault_path(self, db: Path):
        """Given a document, file mode returns its vault_path."""
        doc_id = _insert_row(
            db, vault_path="Projects/Alpha/note.md", title="Doc F"
        )
        result = resolve([doc_id], "file", db_path=db)

        assert isinstance(result, Success)
        assert len(result.value) == 1
        entry = result.value[0]
        assert entry.mode == "file"
        assert entry.content == "Projects/Alpha/note.md"
        assert entry.title == "Doc F"
        assert entry.degraded is False


# ============================================================================
# RED 4 — nonexistent id returns empty list
# ============================================================================


class TestMissingDoc:
    """resolve() silently skips missing document ids."""

    def test_nonexistent_id_returns_empty(self, db: Path):
        """Calling resolve with a nonexistent doc_id returns empty list."""
        result = resolve([999], "summary", db_path=db)
        assert isinstance(result, Success)
        assert result.value == []

    def test_mixed_existing_and_missing(self, db: Path):
        """Missing ids are silently skipped; existing ones are included."""
        id1 = _insert_row(db, summary="S1", title="T1")
        result = resolve([id1, 999, 1000], "summary", db_path=db)

        assert isinstance(result, Success)
        assert len(result.value) == 1
        assert result.value[0].doc_id == id1


# ============================================================================
# RED 5 — max_text_refs limit
# ============================================================================


class TestMaxTextRefs:
    """Text mode respects max_text_refs."""

    def test_beyond_max_text_refs_degrades_to_summary(self, db: Path):
        """Documents beyond max_text_refs degrade to summary even with full_body."""
        ids = []
        for i in range(6):
            doc_id = _insert_row(
                db,
                vault_path=f"test/doc{i}.md",
                title=f"Doc {i}",
                summary=f"Summary {i}",
                full_body=f"Full body {i}",
            )
            ids.append(doc_id)

        result = resolve(ids, "text", max_text_refs=5, db_path=db)

        assert isinstance(result, Success)
        assert len(result.value) == 6

        # First 5 get full_body, not degraded
        for i in range(5):
            assert result.value[i].content == f"Full body {i}"
            assert result.value[i].degraded is False

        # 6th gets summary, degraded
        assert result.value[5].content == "Summary 5"
        assert result.value[5].degraded is True

    def test_exactly_max_text_refs_all_text(self, db: Path):
        """When doc count equals max_text_refs, all get full_body."""
        ids = []
        for i in range(3):
            doc_id = _insert_row(
                db,
                vault_path=f"test/doc{i}.md",
                title=f"Doc {i}",
                summary=f"Summary {i}",
                full_body=f"Full body {i}",
            )
            ids.append(doc_id)

        result = resolve(ids, "text", max_text_refs=3, db_path=db)

        assert isinstance(result, Success)
        assert len(result.value) == 3
        for i in range(3):
            assert result.value[i].content == f"Full body {i}"
            assert result.value[i].degraded is False


# ============================================================================
# RED 6 — empty list input
# ============================================================================


class TestEmptyInput:
    """resolve([]) returns an empty list."""

    def test_empty_ids_returns_empty_list(self, db: Path):
        """Calling resolve with empty list returns Success([])."""
        result = resolve([], "summary", db_path=db)
        assert isinstance(result, Success)
        assert result.value == []


# ============================================================================
# RED 7 — invalid mode returns Failure
# ============================================================================


class TestInvalidMode:
    """resolve() with unknown mode returns a Failure."""

    def test_invalid_mode_returns_failure(self, db: Path):
        """Passing an invalid mode string returns Failure."""
        doc_id = _insert_row(db)
        result = resolve([doc_id], "unknown_mode", db_path=db)

        assert isinstance(result, Failure)
        assert result.recoverable is False
        assert "unknown_mode" in result.error


# ============================================================================
# RED 8 — degraded flag semantics
# ============================================================================


class TestDegradedSemantics:
    """degraded flag is True only when text mode falls back."""

    def test_summary_never_degraded(self, db: Path):
        """Summary mode is always degraded=False, even with NULL summary."""
        doc_id = _insert_row(db, summary=None, full_body="Body")
        result = resolve([doc_id], "summary", db_path=db)
        assert result.value[0].degraded is False

    def test_file_never_degraded(self, db: Path):
        """File mode is always degraded=False."""
        doc_id = _insert_row(db)
        result = resolve([doc_id], "file", db_path=db)
        assert result.value[0].degraded is False

    def test_text_with_body_not_degraded(self, db: Path):
        """Text mode with full_body is degraded=False."""
        doc_id = _insert_row(db, full_body="Body", summary="Sum")
        result = resolve([doc_id], "text", db_path=db)
        assert result.value[0].degraded is False

    def test_text_without_body_degraded(self, db: Path):
        """Text mode without full_body is degraded=True."""
        doc_id = _insert_row(db, full_body=None, summary="Sum")
        result = resolve([doc_id], "text", db_path=db)
        assert result.value[0].degraded is True
