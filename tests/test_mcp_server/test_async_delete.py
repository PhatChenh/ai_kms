"""
tests/test_mcp_server/test_async_delete.py

P9-E-04: Async Delete Cleanup — verify _delete_with_blob_cleanup runs
via asyncio.to_thread so it does not block the event loop.

TDD: RED → GREEN → REFACTOR per test.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from core.result import Success

API_KEY = "test-api-key-123"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Fresh temp database with the full schema applied."""
    from storage.db import init_db

    db_path = tmp_path / "kb.db"
    init_db(db_path)
    return db_path


class TestAsyncDeleteCleanup:
    """Verify the delete branch of event_handler wraps _delete_with_blob_cleanup
    in asyncio.to_thread."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)
        # Reset module-level caches so require_key() lazy-falls-back
        # to os.environ (which we just patched).  Other tests (e.g.
        # test_api_key_cache, test_api_blob_delete) may have set these
        # to values that persist across the suite.
        import mcp_server.api as api

        api._daemon_api_key = None
        api._blob_store = None

    # ------------------------------------------------------------------
    # P9-MCP-18: the synchronous function is actually executed by to_thread
    # ------------------------------------------------------------------

    def test_delete_actually_invokes_sync_function(self, db):
        """End-to-end: when asyncio.to_thread runs the sync function on a
        worker thread, the handler sees its return value."""
        import mcp_server.api as api

        api._db_path = db

        mock_result = Success(1)

        # Do NOT mock asyncio.to_thread — let it really schedule on a thread.
        # Mock only the sync function so we don't touch the real DB.
        with patch.object(
            api, "_delete_with_blob_cleanup", autospec=True
        ) as mock_delete:
            mock_delete.return_value = mock_result

            app = Starlette(routes=api.api_routes)
            client = TestClient(app)

            body = {"type": "deleted", "path": "another/doc.md"}

            resp = client.post(
                "/api/event",
                json=body,
                headers={"Authorization": f"Bearer {API_KEY}"},
            )

            assert resp.status_code == 200, resp.text
            assert resp.json() == {"status": "ok"}

            # The sync function must have been called (by to_thread on the
            # worker thread).
            mock_delete.assert_called_once_with(
                vault_path="another/doc.md",
                db_path=db,
                blob_store=None,
            )


