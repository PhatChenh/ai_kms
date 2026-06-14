"""
tests/test_mcp_server/test_api_blob_delete.py

Phase 5 — Reference-counted blob delete.
TDD: RED → GREEN → REFACTOR per test.

Tests use ``LocalBlobStore`` and the Starlette TestClient against the
real event handler, with ``_blob_store`` injected at module level.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from core.result import Failure
from storage.blobs import LocalBlobStore
from storage.db import init_db
from storage.documents import get_by_path, upsert_from_upload

API_KEY = "test-api-key-123"


# ============================================================================
# Shared fixtures
# ============================================================================


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Fresh temp database with the full schema applied."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    return db_path


@pytest.fixture()
def blob_store(tmp_path: Path) -> LocalBlobStore:
    """A LocalBlobStore rooted in a temp directory."""
    return LocalBlobStore(root=tmp_path / "blobs")


# ============================================================================
# RED 1 — Last-reference delete removes blob (tracer bullet)
# ============================================================================


class TestLastRefDeleteRemovesBlob:
    """Delete the last row referencing a blob → blob is removed from object storage.

    P7-CAP-13 (last-ref delete).
    """

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_delete_last_ref_removes_blob(self, db, blob_store):
        """Delete the only row referencing a blob → blob deleted from store."""
        import mcp_server.api as api

        api._db_path = db
        api._blob_store = blob_store

        # Put a blob in the store
        blob_key = "blobs/sha256-abc123"
        blob_data = b"binary content"
        blob_store.put(blob_key, blob_data, mime_type="image/png")

        # Insert a document row referencing that blob
        upsert_from_upload(
            vault_path="projects/photo.png",
            content_hash="hash-1",
            blob_ref=blob_key,
            mime_type="image/png",
            db_path=db,
        )

        # Confirm blob exists before delete
        assert blob_store.exists(blob_key).value is True

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {"type": "deleted", "path": "projects/photo.png"}
        resp = client.post(
            "/api/event",
            json=body,
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Row should be gone
        row = get_by_path("projects/photo.png", db_path=db)
        assert row.is_success()
        assert row.value is None

        # Blob should be gone from store
        assert blob_store.exists(blob_key).value is False


# ============================================================================
# RED 2 — Shared-reference delete does NOT remove blob
# ============================================================================


class TestSharedRefDeleteKeepsBlob:
    """Delete one of two rows sharing a blob → blob is NOT removed.

    P7-CAP-13 (shared-ref delete).
    """

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_delete_shared_ref_keeps_blob(self, db, blob_store):
        """Delete one row when another shares the same blob → blob remains."""
        import mcp_server.api as api

        api._db_path = db
        api._blob_store = blob_store

        blob_key = "blobs/sha256-shared"
        blob_data = b"shared binary"
        blob_store.put(blob_key, blob_data, mime_type="image/png")

        # Two rows share the same blob_ref
        upsert_from_upload(
            vault_path="projects/a.png",
            content_hash="hash-a",
            blob_ref=blob_key,
            mime_type="image/png",
            db_path=db,
        )
        upsert_from_upload(
            vault_path="projects/b.png",
            content_hash="hash-b",
            blob_ref=blob_key,
            mime_type="image/png",
            db_path=db,
        )

        assert blob_store.exists(blob_key).value is True

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {"type": "deleted", "path": "projects/a.png"}
        resp = client.post(
            "/api/event",
            json=body,
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Row a is gone
        row_a = get_by_path("projects/a.png", db_path=db)
        assert row_a.is_success()
        assert row_a.value is None

        # Row b still exists
        row_b = get_by_path("projects/b.png", db_path=db)
        assert row_b.is_success()
        assert row_b.value is not None

        # Blob still exists (shared reference)
        assert blob_store.exists(blob_key).value is True


# ============================================================================
# RED 3 — Failed blob delete logs but does not fail the event
# ============================================================================


class _FailingDeleteBlobStore(LocalBlobStore):
    """A LocalBlobStore whose delete() always fails."""

    def delete(self, key: str):
        return Failure(
            error="simulated S3 outage",
            recoverable=False,
            context={"key": key},
        )


class TestFailedBlobDelete:
    """When blob_store.delete fails, the document row is still gone and
    the event returns Success.  The failure is logged.

    P7-CAP-13 (failed blob delete).
    """

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_failed_blob_delete_still_returns_success(self, db, tmp_path, caplog):
        """Blob delete failure → row deleted, Success returned, error logged."""
        import mcp_server.api as api

        failing_store = _FailingDeleteBlobStore(root=tmp_path / "failing_blobs")
        api._db_path = db
        api._blob_store = failing_store

        blob_key = "blobs/sha256-faildel"
        # Put the blob in the store (the put works fine, only delete fails)
        failing_store.put(blob_key, b"data", mime_type="image/png")

        upsert_from_upload(
            vault_path="projects/fail.png",
            content_hash="hash-fail",
            blob_ref=blob_key,
            mime_type="image/png",
            db_path=db,
        )

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        with caplog.at_level(logging.ERROR):
            body = {"type": "deleted", "path": "projects/fail.png"}
            resp = client.post(
                "/api/event",
                json=body,
                headers={"Authorization": f"Bearer {API_KEY}"},
            )

        # Event still returns Success
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Row is gone despite blob delete failure
        row = get_by_path("projects/fail.png", db_path=db)
        assert row.is_success()
        assert row.value is None

        # Error was logged
        assert any(
            "simulated S3 outage" in rec.message
            or "simulated S3 outage" in str(getattr(rec, "exc_info", ""))
            for rec in caplog.records
        ), f"Expected error log about blob delete failure, got: {[r.message for r in caplog.records]}"


# ============================================================================
# RED 4 — Text-only row delete (blob_ref NULL) — no blob logic
# ============================================================================


class TestTextOnlyRowDelete:
    """Delete a text-only row (blob_ref NULL) → no blob logic fires.

    P7-CAP-13 (text-only row delete).
    """

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_delete_text_only_row_no_blob_logic(self, db, blob_store):
        """Deleting a row with blob_ref=NULL does not touch the blob store."""
        import mcp_server.api as api

        api._db_path = db
        api._blob_store = blob_store

        upsert_from_upload(
            vault_path="notes/essay.md",
            extracted_text="Just some text",
            content_hash="hash-text",
            db_path=db,
        )

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {"type": "deleted", "path": "notes/essay.md"}
        resp = client.post(
            "/api/event",
            json=body,
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Row is gone
        row = get_by_path("notes/essay.md", db_path=db)
        assert row.is_success()
        assert row.value is None
