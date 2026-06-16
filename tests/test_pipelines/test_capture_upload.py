"""Tests for capture_upload — Phase 7A orchestration entry point.

Uses a stub provider (via monkeypatch on _summarize_upload) so no real LLM
call happens.  All tests use explicit db_path — no module-scope CONFIG (C-17).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import pytest

from core.result import Failure, Result, Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# Stub vision provider (for binary path attach_summary tests)
# ---------------------------------------------------------------------------


class StubVisionProvider:
    """Stub provider whose describe_image returns canned success."""

    def __init__(self, *, fail: bool = False):
        self._fail = fail
        self.call_count = 0

    async def describe_image(
        self, system: str, user: str, image_bytes: bytes, mime_type: str
    ) -> Result:
        self.call_count += 1
        if self._fail:
            return Failure(error="Vision AI timeout", recoverable=True, context={})
        return Success(
            _StubLLMResponse(
                content=("## Visual Description\n\nA screenshot.\n\nTitle: Test Image"),
                model="test-vision",
            )
        )


@dataclass(frozen=True)
class _StubLLMResponse:
    content: str
    model: str = "test-vision-model"
    usage: dict = None

    def __post_init__(self):
        if self.usage is None:
            object.__setattr__(self, "usage", {})


class StubVisionProviderFactory:
    """Factory that returns a pre-built StubVisionProvider instance."""

    def __init__(self, provider: StubVisionProvider):
        self._provider = provider

    def __call__(self, task: str, config) -> StubVisionProvider:
        return self._provider


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

        monkeypatch.setattr("pipelines.capture._summarize_upload", counting_stub)
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
        """P7-CAP-08: Successful capture emits classify_ready log line.

        Pins logging to dev_mode (ConsoleRenderer → stdout) so the assertion
        is deterministic regardless of run order. Without this the test relies
        on structlog's default PrintLogger (writes to stdout only while
        structlog is *unconfigured*); once any earlier test calls
        ``setup_logging`` that assumption breaks and stdout is empty. Calling
        ``setup_logging`` here (after capsys has swapped sys.stdout) binds the
        console StreamHandler to capsys's stream.
        """
        from core.logging_setup import setup_logging

        setup_logging(log_level="DEBUG", dev_mode=True)

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
        clean = re.sub(r"\x1b\[[0-9;]*m", "", captured.out + captured.err)
        assert "capture.classify_ready" in clean, (
            f"Expected classify_ready in console output, got: {clean[:500]}"
        )
        assert "vault_path=inbox/classify_log_test.md" in clean


class TestBestEffortIndex:
    """Tests for _best_effort_index — shared indexing helper."""

    def test_best_effort_index_calls_both_indexers(self):
        """_best_effort_index should call both index_keywords and index_embedding."""
        from pipelines.capture import _best_effort_index

        with (
            mock.patch("retrieval.keyword.index_keywords") as mock_kw,
            mock.patch("retrieval.embeddings.index_embedding") as mock_emb,
        ):
            _best_effort_index(
                vault_path="inbox/test.md",
                title="Test Title",
                summary="A summary",
                body="Full body text",
                db_path=Path("/tmp/test.db"),
            )

        mock_kw.assert_called_once_with(
            vault_path="inbox/test.md",
            title="Test Title",
            summary="A summary",
            body="Full body text",
            db_path=Path("/tmp/test.db"),
        )
        mock_emb.assert_called_once_with(
            vault_path="inbox/test.md",
            title="Test Title",
            note_type=None,
            tags=[],
            summary="A summary",
            db_path=Path("/tmp/test.db"),
        )

    def test_best_effort_index_logs_keyword_error_without_propagating(self):
        """_best_effort_index should log keyword errors but not raise."""
        from pipelines.capture import _best_effort_index

        with mock.patch("retrieval.keyword.index_keywords") as mock_kw:
            mock_kw.side_effect = RuntimeError("keyword index boom")
            with mock.patch("retrieval.embeddings.index_embedding"):
                with mock.patch("pipelines.capture.logger") as mock_logger:
                    # Should not raise
                    _best_effort_index(
                        vault_path="inbox/test.md",
                        title="T",
                        summary="S",
                        body="B",
                        db_path=None,
                    )

        mock_logger.exception.assert_called_with("capture.index_keywords_failed")

    def test_best_effort_index_logs_embedding_error_without_propagating(self):
        """_best_effort_index should log embedding errors but not raise."""
        from pipelines.capture import _best_effort_index

        with mock.patch("retrieval.embeddings.index_embedding") as mock_emb:
            mock_emb.side_effect = RuntimeError("embedding index boom")
            with mock.patch("retrieval.keyword.index_keywords"):
                with mock.patch("pipelines.capture.logger") as mock_logger:
                    # Should not raise
                    _best_effort_index(
                        vault_path="inbox/test.md",
                        title="T",
                        summary="S",
                        body="B",
                        db_path=None,
                    )

        mock_logger.exception.assert_called_with("capture.index_embedding_failed")


class TestAttachSummaryFailure:
    """Tests for attach_summary failure logging (M11)."""

    @pytest.mark.asyncio
    async def test_attach_summary_failure_logged_text_path(self, db, monkeypatch):
        """Text path: attach_summary Failure is logged at warning; capture still returns Success."""
        monkeypatch.setattr(
            "pipelines.capture._summarize_upload", _stub_summarize_success
        )
        from pipelines.capture import capture_upload

        with mock.patch("pipelines.capture.documents.attach_summary") as mock_attach:
            mock_attach.return_value = Failure(
                error="db locked", recoverable=True, context={}
            )
            with mock.patch("pipelines.capture.logger") as mock_logger:
                result = await capture_upload(
                    vault_path="inbox/attach_fail.md",
                    extracted_text="Some text content.",
                    content_hash="hash_attach_fail_001",
                    db_path=db,
                )

        # Must still return Success — attach_summary failure is non-fatal
        assert isinstance(result, Success)

        # Warning must be logged
        mock_logger.warning.assert_any_call(
            "capture.attach_summary_failed vault_path=%s error=%s",
            "inbox/attach_fail.md",
            "db locked",
        )

    @pytest.mark.asyncio
    async def test_attach_summary_failure_logged_binary_path(
        self, db, monkeypatch, tmp_path
    ):
        """Binary path: attach_summary Failure is logged at warning; capture still returns Success."""
        from storage.blobs import LocalBlobStore

        blob_store = LocalBlobStore(tmp_path / "blob_root")

        stub = StubVisionProvider(fail=False)
        factory = StubVisionProviderFactory(stub)
        monkeypatch.setattr("pipelines.capture.get_provider", factory)

        from pipelines.capture import capture_upload

        raw = b"\x89PNG\r\n\x1a\nfake png"

        with mock.patch("pipelines.capture.documents.attach_summary") as mock_attach:
            mock_attach.return_value = Failure(
                error="db locked", recoverable=True, context={}
            )
            with mock.patch("pipelines.capture.logger") as mock_logger:
                result = await capture_upload(
                    vault_path="Projects/A/attachment/bin_attach_fail.png",
                    extracted_text=None,
                    content_hash="hash_bin_attach_fail",
                    raw_bytes=raw,
                    mime_type="image/png",
                    blob_store=blob_store,
                    file_size_bytes=len(raw),
                    db_path=db,
                )

        assert isinstance(result, Success)

        mock_logger.warning.assert_any_call(
            "capture.binary.attach_summary_failed vault_path=%s error=%s",
            "Projects/A/attachment/bin_attach_fail.png",
            "db locked",
        )
