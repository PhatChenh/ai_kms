"""Tests for the binary capture branch of capture_upload — Phase 7B.

Uses LocalBlobStore(tmp_path) and a stub vision provider so no real LLM
or S3 call happens. All tests use explicit db_path — no module-scope CONFIG.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.result import Failure, Result, Success
from storage.blobs import LocalBlobStore
from storage.db import init_db


# ---------------------------------------------------------------------------
# Stub vision provider — returns canned description or forced failure
# ---------------------------------------------------------------------------

_GOOD_DESCRIPTION = (
    "## Visual Description\n\nA screenshot showing a dashboard with charts.\n\n"
    "## Key elements\n- Bar chart on the left\n- Line graph on the right\n\n"
    "## Visible text\n- 'Q3 Revenue'\n- '42%'\n\n"
    "Title: Dashboard Screenshot"
)


@dataclass(frozen=True)
class _StubLLMResponse:
    content: str
    model: str = "test-vision-model"
    usage: dict = None

    def __post_init__(self):
        if self.usage is None:
            object.__setattr__(self, "usage", {})


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
            return Failure(
                error="Vision AI timeout", recoverable=True, context={}
            )
        return Success(
            _StubLLMResponse(content=_GOOD_DESCRIPTION, model="test-vision")
        )


class StubVisionProviderFactory:
    """Factory that returns a pre-built StubVisionProvider instance."""

    def __init__(self, provider: StubVisionProvider):
        self._provider = provider

    def __call__(self, task: str, config) -> StubVisionProvider:
        return self._provider


# ---------------------------------------------------------------------------
# Helpers
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


def _audit_entries(db_path: Path, outcome: str) -> list[dict]:
    """Return all audit_log rows for a given outcome."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE outcome = ?", (outcome,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Fresh temp database with full schema applied."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    return db_path


@pytest.fixture()
def blob_store(tmp_path: Path) -> LocalBlobStore:
    """Local blob store rooted in a temp directory."""
    return LocalBlobStore(tmp_path / "blob_root")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCaptureBinary:
    """Binary branch tests for capture_upload()."""

    # (a) Describable binary upload
    @pytest.mark.asyncio
    async def test_describable_binary_upload_stores_blob_and_description(
        self, db, blob_store, monkeypatch
    ):
        """P7-CAP-11: binary upload with describable type → blob stored,
        row has blob_ref + mime_type + summary + title, audit DESCRIBED."""
        stub = StubVisionProvider(fail=False)
        factory = StubVisionProviderFactory(stub)

        # Patch get_provider where it's imported in capture.py
        monkeypatch.setattr(
            "pipelines.capture.get_provider", factory
        )

        from pipelines.capture import capture_upload
        from storage.documents import get_by_path

        raw = b"\x89PNG\r\n\x1a\nfake png data"
        result = await capture_upload(
            vault_path="Projects/A/attachment/screenshot.png",
            extracted_text=None,
            content_hash="hash_bin_001",
            raw_bytes=raw,
            mime_type="image/png",
            blob_store=blob_store,
            original_filename="screenshot.png",
            file_size_bytes=len(raw),
            db_path=db,
        )

        # Must return Success
        assert isinstance(result, Success), f"Expected Success, got {result}"

        # Row in DB
        row_r = get_by_path("Projects/A/attachment/screenshot.png", db_path=db)
        assert isinstance(row_r, Success)
        row = row_r.value
        assert row is not None

        # Blob ref and mime_type set
        assert row.blob_ref == "hash_bin_001", f"blob_ref={row.blob_ref!r}"
        assert row.mime_type == "image/png", f"mime_type={row.mime_type!r}"

        # Summary and title attached
        assert row.summary is not None, "Expected summary to be attached"
        assert "dashboard" in row.summary.lower()
        assert row.title == "Dashboard Screenshot"

        # Blob exists in store
        exists_r = blob_store.exists("hash_bin_001")
        assert isinstance(exists_r, Success)
        assert exists_r.value is True

        # AI was called exactly once
        assert stub.call_count == 1

        # Audit DESCRIBED
        entries = _audit_entries(db, "DESCRIBED")
        assert len(entries) >= 1, "Expected at least one DESCRIBED audit entry"

    # (b) Identical re-upload → dedup
    @pytest.mark.asyncio
    async def test_identical_reupload_skips_blob_and_ai(self, db, blob_store, monkeypatch):
        """P7-CAP-10: re-upload with same content_hash → Success, no blob put, no AI call."""
        stub = StubVisionProvider(fail=False)
        factory = StubVisionProviderFactory(stub)
        monkeypatch.setattr("pipelines.capture.get_provider", factory)

        from pipelines.capture import capture_upload

        raw = b"\x89PNG\r\n\x1a\nsome image bytes"
        # First upload
        r1 = await capture_upload(
            vault_path="Projects/A/attachment/photo.jpg",
            extracted_text=None,
            content_hash="hash_dup_002",
            raw_bytes=raw,
            mime_type="image/jpeg",
            blob_store=blob_store,
            file_size_bytes=len(raw),
            db_path=db,
        )
        assert isinstance(r1, Success)
        assert stub.call_count == 1

        # Second upload — same path, same hash
        stub.call_count = 0  # reset to detect re-calls
        r2 = await capture_upload(
            vault_path="Projects/A/attachment/photo.jpg",
            extracted_text=None,
            content_hash="hash_dup_002",
            raw_bytes=raw,
            mime_type="image/jpeg",
            blob_store=blob_store,
            file_size_bytes=len(raw),
            db_path=db,
        )
        assert isinstance(r2, Success)

        # AI was NOT called again
        assert stub.call_count == 0, (
            f"Expected 0 AI calls on re-upload, got {stub.call_count}"
        )

    # (c) Unsupported type
    @pytest.mark.asyncio
    async def test_unsupported_type_stores_blob_no_description(
        self, db, blob_store, monkeypatch
    ):
        """P7-CAP-12: unsupported MIME type → blob stored, summary NULL, audit skip."""
        stub = StubVisionProvider(fail=False)
        factory = StubVisionProviderFactory(stub)
        monkeypatch.setattr("pipelines.capture.get_provider", factory)

        from pipelines.capture import capture_upload
        from storage.documents import get_by_path

        raw = b"PK\x03\x04fake zip content"
        result = await capture_upload(
            vault_path="Projects/A/attachment/archive.zip",
            extracted_text=None,
            content_hash="hash_unsup_003",
            raw_bytes=raw,
            mime_type="application/zip",
            blob_store=blob_store,
            file_size_bytes=len(raw),
            db_path=db,
        )

        assert isinstance(result, Success)

        row_r = get_by_path("Projects/A/attachment/archive.zip", db_path=db)
        assert isinstance(row_r, Success)
        row = row_r.value
        assert row is not None

        # Blob ref + mime_type set
        assert row.blob_ref == "hash_unsup_003"
        assert row.mime_type == "application/zip"

        # Summary NOT attached (unsupported)
        assert row.summary is None

        # Blob exists in store
        exists_r = blob_store.exists("hash_unsup_003")
        assert isinstance(exists_r, Success)
        assert exists_r.value is True

        # AI was NOT called
        assert stub.call_count == 0

        # Audit has "unsupported type" skip
        entries = _audit_entries(db, "SKIPPED")
        assert any(
            "unsupported type" in (e.get("reasoning") or "") for e in entries
        ), f"Expected 'unsupported type' in SKIPPED audit, got {entries}"

    # (d) Over size cap
    @pytest.mark.asyncio
    async def test_over_size_cap_stores_blob_no_description(
        self, db, blob_store, monkeypatch
    ):
        """P7-CAP-12: file over size cap → blob stored, summary NULL, audit skip."""
        stub = StubVisionProvider(fail=False)
        factory = StubVisionProviderFactory(stub)
        monkeypatch.setattr("pipelines.capture.get_provider", factory)

        from pipelines.capture import capture_upload
        from storage.documents import get_by_path

        # Use a huge file_size_bytes (20 MB) exceeding the default 10 MB cap,
        # but keep raw_bytes small so the test is fast.  The code checks
        # file_size_bytes first, then falls back to len(raw_bytes).
        huge_size = 20 * 1024 * 1024  # 20 MB > 10 MB default cap
        raw = b"\x89PNG\r\n\x1a\nsmall payload"
        result = await capture_upload(
            vault_path="Projects/A/attachment/big.png",
            extracted_text=None,
            content_hash="hash_big_004",
            raw_bytes=raw,
            mime_type="image/png",
            blob_store=blob_store,
            file_size_bytes=huge_size,
            db_path=db,
        )

        assert isinstance(result, Success)

        row_r = get_by_path("Projects/A/attachment/big.png", db_path=db)
        assert isinstance(row_r, Success)
        row = row_r.value
        assert row is not None

        assert row.blob_ref == "hash_big_004"
        assert row.mime_type == "image/png"
        assert row.summary is None

        exists_r = blob_store.exists("hash_big_004")
        assert isinstance(exists_r, Success)
        assert exists_r.value is True

        assert stub.call_count == 0

        entries = _audit_entries(db, "SKIPPED")
        assert any(
            "too big" in (e.get("reasoning") or "") for e in entries
        ), f"Expected 'too big' in SKIPPED audit, got {entries}"

    # (e) Vision AI failure
    @pytest.mark.asyncio
    async def test_vision_ai_failure_stores_blob_returns_success(
        self, db, blob_store, monkeypatch
    ):
        """P7-CAP-11: vision AI fails → blob stored, summary NULL, audit failure, Success."""
        stub = StubVisionProvider(fail=True)
        factory = StubVisionProviderFactory(stub)
        monkeypatch.setattr("pipelines.capture.get_provider", factory)

        from pipelines.capture import capture_upload
        from storage.documents import get_by_path

        raw = b"\x89PNG\r\n\x1a\nimage for failure"
        result = await capture_upload(
            vault_path="Projects/A/attachment/fail_img.png",
            extracted_text=None,
            content_hash="hash_fail_005",
            raw_bytes=raw,
            mime_type="image/png",
            blob_store=blob_store,
            file_size_bytes=len(raw),
            db_path=db,
        )

        # Must return Success (blob is safe)
        assert isinstance(result, Success)

        row_r = get_by_path("Projects/A/attachment/fail_img.png", db_path=db)
        assert isinstance(row_r, Success)
        row = row_r.value
        assert row is not None

        assert row.blob_ref == "hash_fail_005"
        assert row.mime_type == "image/png"
        assert row.summary is None  # AI failed

        exists_r = blob_store.exists("hash_fail_005")
        assert isinstance(exists_r, Success)
        assert exists_r.value is True

        # AI was called
        assert stub.call_count == 1

        # Audit has FAILED entry
        entries = _audit_entries(db, "FAILED")
        assert len(entries) >= 1, "Expected at least one FAILED audit entry"

    # (f) Text upload regression guard
    @pytest.mark.asyncio
    async def test_text_upload_path_runs_unchanged(self, db, monkeypatch):
        """Regression guard: when extracted_text is present, the existing 7A
        text path runs unchanged (no blob_store needed, no binary branch)."""
        # Use the same stub pattern as 7A tests
        async def stub_summarize(text, db_path=None):
            return Success((
                "## Summary\n\nTest content.\n\nTitle: My Note",
                "My Note",
            ))

        monkeypatch.setattr(
            "pipelines.capture._summarize_upload", stub_summarize
        )

        from pipelines.capture import capture_upload
        from storage.documents import get_by_path

        result = await capture_upload(
            vault_path="inbox/text_note.md",
            extracted_text="Full text content here.",
            content_hash="hash_text_regression",
            original_filename="text_note.md",
            file_size_bytes=100,
            db_path=db,
        )

        assert isinstance(result, Success)

        row_r = get_by_path("inbox/text_note.md", db_path=db)
        assert isinstance(row_r, Success)
        row = row_r.value
        assert row is not None
        assert row.full_body == "Full text content here."
        assert row.summary is not None
        assert "Summary" in row.summary
        # blob_ref and mime_type should be NULL for text path
        assert row.blob_ref is None
        assert row.mime_type is None

    # (g) Neither text nor bytes
    @pytest.mark.asyncio
    async def test_neither_text_nor_bytes_returns_failure(self, db):
        """When both extracted_text and raw_bytes are None, return Failure."""
        from pipelines.capture import capture_upload

        result = await capture_upload(
            vault_path="inbox/empty.md",
            extracted_text=None,
            content_hash="hash_empty",
            raw_bytes=None,
            db_path=db,
        )
        assert isinstance(result, Failure)
        assert "neither text nor bytes" in result.error.lower()
