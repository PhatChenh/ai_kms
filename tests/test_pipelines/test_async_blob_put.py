"""Test that _capture_binary uses async_put, not the blocking sync put.

P9-E-03 / P9-MCP-18: Async Blob Put — verifies the event-loop-safe
blob store path is wired in.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.result import Failure, Result, Success
from storage.db import init_db

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Fresh temp database with full schema applied."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestAsyncBlobPut:
    """P9-E-03: _capture_binary must await blob_store.async_put,
    never call the blocking blob_store.put."""

    @pytest.mark.asyncio
    async def test_async_put_is_awaited(self, db):
        """Verify async_put is called with expected args and put is NOT called."""
        from pipelines.capture import _capture_binary
        from storage.documents import get_by_path

        # Build a mock blob store that tracks both sync and async put
        blob_store = MagicMock()
        blob_store.async_put = AsyncMock(return_value=Success(None))
        blob_store.put = MagicMock(return_value=Success(None))

        raw = b"\x89PNG\r\n\x1a\nfake png"
        result = await _capture_binary(
            vault_path="Projects/test/async_blob.png",
            raw_bytes=raw,
            content_hash="hash_async_001",
            mime_type="image/png",
            original_filename="async_blob.png",
            file_size_bytes=len(raw),
            blob_store=blob_store,
            db_path=db,
        )

        # async_put MUST have been awaited exactly once
        blob_store.async_put.assert_awaited_once_with(
            "hash_async_001", raw, "image/png"
        )

        # sync put MUST NOT have been called
        blob_store.put.assert_not_called()

        # Result should be Success (blob stored, row created)
        assert isinstance(result, Success), f"Expected Success, got {result}"

        # Row should be in DB
        row_r = get_by_path("Projects/test/async_blob.png", db_path=db)
        assert isinstance(row_r, Success)
        row = row_r.value
        assert row is not None
        assert row.blob_ref == "hash_async_001"
        assert row.mime_type == "image/png"

    @pytest.mark.asyncio
    async def test_async_put_failure_propagates(self, db):
        """When async_put returns Failure, the error is propagated."""
        from pipelines.capture import _capture_binary

        blob_store = MagicMock()
        blob_store.async_put = AsyncMock(
            return_value=Failure(
                error="S3 bucket not found",
                recoverable=False,
                context={"key": "hash_fail"},
            )
        )
        # put should not be called
        blob_store.put = MagicMock()

        raw = b"some bytes"
        result = await _capture_binary(
            vault_path="Projects/test/fail_blob.png",
            raw_bytes=raw,
            content_hash="hash_fail_002",
            mime_type="image/png",
            original_filename="fail_blob.png",
            file_size_bytes=len(raw),
            blob_store=blob_store,
            db_path=db,
        )

        # Must return Failure
        assert isinstance(result, Failure), f"Expected Failure, got {result}"
        assert "S3 bucket not found" in result.error

        # async_put was called
        blob_store.async_put.assert_awaited_once()

        # sync put was NOT called
        blob_store.put.assert_not_called()
