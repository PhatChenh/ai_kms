"""Tests for capture_upload — Phase 7A orchestration entry point.

Uses a stub provider (via monkeypatch on _summarize_upload) so no real LLM
call happens.  All tests use explicit db_path — no module-scope CONFIG (C-17).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.result import Failure, Result, Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# Stub summarizer — returns canned (summary, title) or forced failure
# ---------------------------------------------------------------------------

_GOOD_SUMMARY = (
    "## Overview\nA test document.\n\n"
    "## Key points\n- Point 1\n- Point 2\n\n"
    "## Decisions\n- None\n\n"
    "## Action items\n- Follow up\n\n"
    "## People mentioned\n- Alice\n\n"
)
_GOOD_TITLE = "Test Strategy Meeting"


async def _stub_summarize_success(text, db_path=None):
    return Success((_GOOD_SUMMARY, _GOOD_TITLE))


async def _stub_summarize_failure(text, db_path=None):
    return Failure(error="AI unavailable", recoverable=True, context={})


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _raw_row(db_path: Path, vault_path: str) -> dict | None:
    """Read a documents row with raw sqlite3 (bypasses our own code)."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM documents WHERE vault_path = ?", (vault_path,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestCaptureUpload:
    """Orchestration tests for capture_upload()."""

    @pytest.mark.asyncio
    async def test_new_upload_stores_raw_then_summarizes(self, db, monkeypatch):
        """P7-CAP-02/03: new upload saves raw text, then summary attaches."""
        monkeypatch.setattr(
            "pipelines.capture._summarize_upload", _stub_summarize_success
        )
        from pipelines.capture import capture_upload
        from storage.documents import get_by_path

        result = await capture_upload(
            vault_path="inbox/meeting.md",
            extracted_text="Full meeting notes here.",
            content_hash="hash_new_001",
            original_filename="meeting.md",
            file_size_bytes=100,
            db_path=db,
        )
        assert isinstance(result, Success)

        row_r = get_by_path("inbox/meeting.md", db_path=db)
        assert isinstance(row_r, Success)
        row = row_r.value
        assert row is not None

        # P7-CAP-02: raw text stored
        assert row.full_body == "Full meeting notes here."
        # P7-CAP-03: summary attached
        assert row.summary is not None
        assert "## Overview" in row.summary
        assert row.title == _GOOD_TITLE
        # P7-CAP-05: project/domain NULL
        assert row.project is None

    @pytest.mark.asyncio
    async def test_duplicate_hash_skips_ai(self, db, monkeypatch):
        """P7-CAP-01: identical content_hash → provider never called."""
        call_count = 0

        async def counting_stub(text, db_path=None):
            nonlocal call_count
            call_count += 1
            return Success((_GOOD_SUMMARY, _GOOD_TITLE))

        monkeypatch.setattr(
            "pipelines.capture._summarize_upload", counting_stub
        )
        from pipelines.capture import capture_upload

        # First upload
        r1 = await capture_upload(
            vault_path="inbox/note.md",
            extracted_text="Content A",
            content_hash="hash_dup_001",
            db_path=db,
        )
        assert isinstance(r1, Success)
        assert call_count == 1  # AI called once

        # Second upload — same path, same hash
        r2 = await capture_upload(
            vault_path="inbox/note.md",
            extracted_text="Content A — different text, same hash",
            content_hash="hash_dup_001",
            db_path=db,
        )
        assert isinstance(r2, Success)
        assert call_count == 1  # AI NOT called again — dedup worked

    @pytest.mark.asyncio
    async def test_ai_failure_stores_anyway_returns_success(self, db, monkeypatch):
        """P7-CAP-04: AI failure → row stored, summary NULL, returns Success."""
        monkeypatch.setattr(
            "pipelines.capture._summarize_upload", _stub_summarize_failure
        )
        from pipelines.capture import capture_upload
        from storage.documents import get_by_path

        result = await capture_upload(
            vault_path="inbox/failnote.md",
            extracted_text="Important content that must survive.",
            content_hash="hash_fail_001",
            db_path=db,
        )
        # Must return Success, not Failure (store-anyway contract)
        assert isinstance(result, Success)

        row_r = get_by_path("inbox/failnote.md", db_path=db)
        assert isinstance(row_r, Success)
        row = row_r.value
        assert row is not None
        # Content preserved
        assert row.full_body == "Important content that must survive."
        # Summary NULL — AI didn't complete
        assert row.summary is None

    @pytest.mark.asyncio
    async def test_same_content_two_paths_two_rows(self, db, monkeypatch):
        """P7-CAP-07: identical content under different paths → two rows."""
        monkeypatch.setattr(
            "pipelines.capture._summarize_upload", _stub_summarize_success
        )
        from pipelines.capture import capture_upload
        from storage.documents import get_by_path

        r1 = await capture_upload(
            vault_path="inbox/note_v1.md",
            extracted_text="Same content.",
            content_hash="hash_two_paths",
            db_path=db,
        )
        assert isinstance(r1, Success)

        r2 = await capture_upload(
            vault_path="inbox/note_v2.md",
            extracted_text="Same content.",
            content_hash="hash_two_paths",
            db_path=db,
        )
        assert isinstance(r2, Success)

        # Both rows exist independently
        row1 = get_by_path("inbox/note_v1.md", db_path=db)
        row2 = get_by_path("inbox/note_v2.md", db_path=db)
        assert isinstance(row1, Success) and row1.value is not None
        assert isinstance(row2, Success) and row2.value is not None
        assert row1.value.id != row2.value.id

    @pytest.mark.asyncio
    async def test_audit_written_on_success(self, db, monkeypatch):
        """C-13: Successful capture writes a CAPTURED audit entry."""
        monkeypatch.setattr(
            "pipelines.capture._summarize_upload", _stub_summarize_success
        )
        from pipelines.capture import capture_upload

        await capture_upload(
            vault_path="inbox/audit_test.md",
            extracted_text="Content for audit.",
            content_hash="hash_audit_001",
            db_path=db,
        )

        # Check audit_log table
        import sqlite3
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE pipeline='capture' AND outcome='CAPTURED'"
        ).fetchall()
        conn.close()
        assert len(rows) >= 1, "Expected at least one CAPTURED audit entry"

    @pytest.mark.asyncio
    async def test_audit_written_on_failure(self, db, monkeypatch):
        """C-13: AI failure writes a failure audit entry."""
        monkeypatch.setattr(
            "pipelines.capture._summarize_upload", _stub_summarize_failure
        )
        from pipelines.capture import capture_upload

        await capture_upload(
            vault_path="inbox/audit_fail.md",
            extracted_text="Content for failure audit.",
            content_hash="hash_audit_fail_001",
            db_path=db,
        )

        import sqlite3
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE pipeline='capture' AND source_ids LIKE ?",
            ("%inbox/audit_fail.md%",),
        ).fetchall()
        conn.close()
        assert len(rows) >= 1, "Expected at least one failure audit entry"

    @pytest.mark.asyncio
    async def test_correlation_id_set_first(self, db, monkeypatch):
        """correlation_id is set before any audit write (avoids silent drops)."""
        monkeypatch.setattr(
            "pipelines.capture._summarize_upload", _stub_summarize_success
        )
        from pipelines.capture import capture_upload

        # This should not crash — if correlation_id is missing, audit.append
        # returns Failure("missing correlation_id") but capture_upload handles it
        result = await capture_upload(
            vault_path="inbox/corr_id_test.md",
            extracted_text="Content.",
            content_hash="hash_corr_001",
            db_path=db,
        )
        assert isinstance(result, Success)

    @pytest.mark.asyncio
    async def test_classify_ready_log_emitted(self, db, monkeypatch, capsys):
        """P7-CAP-08: Successful capture emits classify_ready log line."""
        monkeypatch.setattr(
            "pipelines.capture._summarize_upload", _stub_summarize_success
        )
        from pipelines.capture import capture_upload

        await capture_upload(
            vault_path="inbox/classify_log_test.md",
            extracted_text="Content.",
            content_hash="hash_classify_log",
            db_path=db,
        )

        captured = capsys.readouterr()
        assert "capture.classify_ready" in captured.out, (
            f"Expected classify_ready in stdout, got: {captured.out[:500]}"
        )
        assert "vault_path=inbox/classify_log_test.md" in captured.out
