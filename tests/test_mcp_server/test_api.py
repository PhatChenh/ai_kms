"""
tests/test_mcp_server/test_api.py

Phase 3 REST handlers + secret-key gate + health route.
TDD: RED → GREEN → REFACTOR per test.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

# The module under test
from storage.db import init_db
from storage.documents import DocumentRow, get_by_path


# ============================================================================
# Shared fixtures
# ============================================================================


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Fresh temp database with the full schema applied."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    return db_path


API_KEY = "test-api-key-123"


# ============================================================================
# RED 1 — /health returns 200 with no key
# ============================================================================


class TestHealth:
    """/health is open — no key required."""

    @pytest.fixture(autouse=True)
    def _reset_env(self, monkeypatch):
        """Ensure KMS_DAEMON_API_KEY is NOT set for /health tests."""
        monkeypatch.delenv("KMS_DAEMON_API_KEY", raising=False)

    def test_health_returns_200_without_key(self):
        """GET /health → 200 {"status":"ok"} even when no key is set."""
        from mcp_server.api import health_route

        app = Starlette(routes=health_route)
        client = TestClient(app)

        resp = client.get("/health")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ============================================================================
# RED 2 — Upload with valid key + new path
# ============================================================================


class TestUpload:
    """POST /api/upload — requires valid bearer key."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_upload_new_path_returns_document_id(self, db):
        """POST /api/upload with valid key and new path → 200 with document_id.

        P5-DEPLOY-03 at HTTP boundary.
        """
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {
            "vault_path": "uploads/report.pdf",
            "extracted_text": "Full extracted text content",
            "content_hash": "abc123",
            "original_filename": "report.pdf",
            "file_size_bytes": 1024,
        }

        resp = client.post(
            "/api/upload",
            json=body,
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["document_id"], int)

        # Verify the row was actually inserted
        row = get_by_path("uploads/report.pdf", db_path=db)
        assert row.is_success()
        doc = row.value
        assert doc is not None
        assert doc.vault_path == "uploads/report.pdf"
        assert doc.full_body == "Full extracted text content"
        assert doc.id == data["document_id"]


# ============================================================================
# RED 3 — Event moved
# ============================================================================


class TestEventMoved:
    """POST /api/event with type=moved."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_event_moved_updates_vault_path(self, db):
        """POST /api/event type=moved updates vault_path in documents table.

        P5-DEPLOY-06
        """
        import mcp_server.api as api

        api._db_path = db

        # First, insert a document via upload
        from storage.documents import upsert_from_upload

        upsert_from_upload(
            vault_path="old/path/note.md",
            extracted_text="some content",
            content_hash="hash1",
            db_path=db,
        )

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {
            "type": "moved",
            "old_path": "old/path/note.md",
            "new_path": "new/path/note.md",
        }

        resp = client.post(
            "/api/event",
            json=body,
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Old path should not exist
        old_row = get_by_path("old/path/note.md", db_path=db)
        assert old_row.is_success()
        assert old_row.value is None

        # New path should exist
        new_row = get_by_path("new/path/note.md", db_path=db)
        assert new_row.is_success()
        doc = new_row.value
        assert doc is not None
        assert doc.vault_path == "new/path/note.md"


# ============================================================================
# RED 4 — Event deleted
# ============================================================================


class TestEventDeleted:
    """POST /api/event with type=deleted."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_event_deleted_removes_row(self, db):
        """POST /api/event type=deleted removes the document row and index entries.

        P5-DEPLOY-07
        """
        import mcp_server.api as api

        api._db_path = db

        # First, insert a document via upload
        from storage.documents import upsert_from_upload

        upsert_from_upload(
            vault_path="todelete/note.md",
            extracted_text="content to delete",
            content_hash="hash-del",
            db_path=db,
        )

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {
            "type": "deleted",
            "path": "todelete/note.md",
        }

        resp = client.post(
            "/api/event",
            json=body,
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Row should be gone
        row = get_by_path("todelete/note.md", db_path=db)
        assert row.is_success()
        assert row.value is None


# ============================================================================
# RED 5 — Event unknown path (not_found)
# ============================================================================


class TestEventNotFound:
    """POST /api/event targeting a path that doesn't exist."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_event_deleted_unknown_path_returns_not_found(self, db):
        """POST /api/event type=deleted on non-existent path → 200 {"status":"not_found"}.

        P5-DEPLOY-08
        """
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {
            "type": "deleted",
            "path": "nonexistent/note.md",
        }

        resp = client.post(
            "/api/event",
            json=body,
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "not_found"}

    def test_event_moved_unknown_path_returns_not_found(self, db):
        """POST /api/event type=moved on non-existent old_path → 200 {"status":"not_found"}.

        P5-DEPLOY-08
        """
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {
            "type": "moved",
            "old_path": "nonexistent/old.md",
            "new_path": "nonexistent/new.md",
        }

        resp = client.post(
            "/api/event",
            json=body,
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "not_found"}


# ============================================================================
# RED 6 — Wrong/missing key on API
# ============================================================================


class TestUnauthorized:
    """Missing or wrong key on /api/* returns 401."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_upload_missing_key_returns_401(self, db):
        """POST /api/upload without Authorization header → 401."""
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {
            "vault_path": "uploads/test.md",
            "extracted_text": "test",
            "content_hash": "hash",
        }

        resp = client.post("/api/upload", json=body)

        assert resp.status_code == 401
        assert resp.json() == {"status": "unauthorized"}

    def test_upload_wrong_key_returns_401(self, db):
        """POST /api/upload with wrong bearer token → 401."""
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {
            "vault_path": "uploads/test.md",
            "extracted_text": "test",
            "content_hash": "hash",
        }

        resp = client.post(
            "/api/upload",
            json=body,
            headers={"Authorization": "Bearer wrong-key"},
        )

        assert resp.status_code == 401
        assert resp.json() == {"status": "unauthorized"}

    def test_event_missing_key_returns_401(self, db):
        """POST /api/event without Authorization header → 401."""
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {"type": "deleted", "path": "test.md"}

        resp = client.post("/api/event", json=body)

        assert resp.status_code == 401
        assert resp.json() == {"status": "unauthorized"}

    def test_event_wrong_key_returns_401(self, db):
        """POST /api/event with wrong bearer token → 401."""
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {"type": "deleted", "path": "test.md"}

        resp = client.post(
            "/api/event",
            json=body,
            headers={"Authorization": "Bearer wrong-key"},
        )

        assert resp.status_code == 401
        assert resp.json() == {"status": "unauthorized"}

    def test_upload_missing_key_does_not_change_db(self, db):
        """Unauthorized upload doesn't touch the database.

        P5-DEPLOY-09
        """
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        body = {
            "vault_path": "uploads/test.md",
            "extracted_text": "should not be stored",
            "content_hash": "hash",
        }

        resp = client.post("/api/upload", json=body)

        assert resp.status_code == 401

        # Nothing was inserted
        row = get_by_path("uploads/test.md", db_path=db)
        assert row.is_success()
        assert row.value is None


# ============================================================================
# RED 7 — Gate does NOT fire on /health
# ============================================================================


class TestHealthGateIsolation:
    """/health is never gated, even with wrong/missing key."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_health_with_wrong_key_returns_200(self):
        """GET /health with wrong bearer token → 200 (not gated)."""
        from mcp_server.api import health_route

        app = Starlette(routes=health_route)
        client = TestClient(app)

        resp = client.get(
            "/health",
            headers={"Authorization": "Bearer invalid-key"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_health_with_missing_key_returns_200(self):
        """GET /health with no key → 200 (not gated)."""
        from mcp_server.api import health_route

        app = Starlette(routes=health_route)
        client = TestClient(app)

        resp = client.get("/health")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ============================================================================
# P6-A1-01 — GET /api/state returns document list
# ============================================================================


class TestState:
    """GET /api/state — returns all known documents."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_state_empty_db_returns_empty_list(self, db):
        """GET /api/state on empty database → 200 with empty documents list."""
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        resp = client.get(
            "/api/state",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["documents"] == []

    def test_state_returns_all_documents(self, db):
        """GET /api/state returns all (vault_path, content_hash) pairs."""
        import mcp_server.api as api

        api._db_path = db

        # Insert a couple of documents
        from storage.documents import upsert_from_upload

        upsert_from_upload(
            vault_path="docs/alpha.md",
            extracted_text="alpha content",
            content_hash="hash-alpha",
            db_path=db,
        )
        upsert_from_upload(
            vault_path="docs/beta.md",
            extracted_text="beta content",
            content_hash="hash-beta",
            db_path=db,
        )

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        resp = client.get(
            "/api/state",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        docs = data["documents"]
        assert len(docs) == 2

        # Order is insertion order; verify both present
        paths = {d["vault_path"] for d in docs}
        assert paths == {"docs/alpha.md", "docs/beta.md"}
        hashes = {d["content_hash"] for d in docs}
        assert hashes == {"hash-alpha", "hash-beta"}

    def test_state_missing_key_returns_401(self, db):
        """GET /api/state without Authorization → 401."""
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        resp = client.get("/api/state")

        assert resp.status_code == 401
        assert resp.json() == {"status": "unauthorized"}

    def test_state_wrong_key_returns_401(self, db):
        """GET /api/state with wrong bearer token → 401."""
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        resp = client.get(
            "/api/state",
            headers={"Authorization": "Bearer wrong-key"},
        )

        assert resp.status_code == 401
        assert resp.json() == {"status": "unauthorized"}


# ============================================================================
# P6-A1-03 — Multipart binary upload
# ============================================================================


class TestMultipartUpload:
    """POST /api/upload with Content-Type: multipart/form-data."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_multipart_upload_creates_row_with_null_full_body(self, db, tmp_path):
        """Multipart upload creates a documents row with NULL full_body."""
        import mcp_server.api as api
        from storage.blobs import LocalBlobStore

        api._db_path = db
        api._blob_store = LocalBlobStore(tmp_path)

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        resp = client.post(
            "/api/upload",
            data={
                "vault_path": "uploads/binary.pdf",
                "content_hash": "abc123def",
                "original_filename": "report.pdf",
                "file_size_bytes": "2048",
            },
            files={"file": ("report.pdf", b"fake binary content", "application/pdf")},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["document_id"], int)

        # Verify row inserted with NULL full_body
        from storage.documents import get_by_path

        row = get_by_path("uploads/binary.pdf", db_path=db)
        assert row.is_success()
        doc = row.value
        assert doc is not None
        assert doc.vault_path == "uploads/binary.pdf"
        assert doc.full_body is None
        assert doc.content_hash == "abc123def"
        assert doc.original_filename == "report.pdf"
        assert doc.file_size_bytes == 2048

    def test_multipart_upload_same_hash_is_noop(self, db, tmp_path):
        """Re-upload with same content_hash → no-op, returns existing id."""
        import mcp_server.api as api
        from storage.blobs import LocalBlobStore

        api._db_path = db
        api._blob_store = LocalBlobStore(tmp_path)

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        form_data = {
            "vault_path": "uploads/immutable.bin",
            "content_hash": "same-hash-123",
        }

        # First upload (use proper filename so Starlette returns UploadFile)
        resp1 = client.post(
            "/api/upload",
            data=form_data,
            files={"file": ("data.bin", b"hello", "application/octet-stream")},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert resp1.status_code == 200
        id1 = resp1.json()["document_id"]

        # Second upload with same hash
        resp2 = client.post(
            "/api/upload",
            data=form_data,
            files={"file": ("data.bin", b"hello", "application/octet-stream")},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert resp2.status_code == 200
        id2 = resp2.json()["document_id"]

        assert id1 == id2

    def test_multipart_upload_missing_key_returns_401(self, db):
        """Multipart upload without Authorization → 401."""
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        resp = client.post(
            "/api/upload",
            data={
                "vault_path": "uploads/nope.bin",
                "content_hash": "hash-nope",
            },
            files={"file": ("nope.bin", b"")},
        )

        assert resp.status_code == 401
        assert resp.json() == {"status": "unauthorized"}

    def test_multipart_upload_missing_vault_path_returns_400(self, db):
        """Multipart upload without vault_path → 400."""
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        resp = client.post(
            "/api/upload",
            data={"content_hash": "hash"},
            files={"file": ("file.bin", b"")},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "vault_path is required"

    def test_multipart_upload_missing_content_hash_returns_400(self, db):
        """Multipart upload without content_hash → 400."""
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        resp = client.post(
            "/api/upload",
            data={"vault_path": "path.bin"},
            files={"file": ("file.bin", b"")},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "content_hash is required"

    def test_multipart_upload_invalid_file_size_returns_400(self, db):
        """Multipart upload with non-integer file_size_bytes → 400."""
        import mcp_server.api as api

        api._db_path = db

        app = Starlette(routes=api.api_routes)
        client = TestClient(app)

        resp = client.post(
            "/api/upload",
            data={
                "vault_path": "path.bin",
                "content_hash": "hash",
                "file_size_bytes": "not-a-number",
            },
            files={"file": ("file.bin", b"")},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "file_size_bytes must be an integer"


# ============================================================================
# P6-A1-02 — upsert_from_upload with extracted_text=None
# ============================================================================


# ============================================================================
# Phase 6 — Upload endpoint re-point (binary capture branch)
# ============================================================================


class TestMultipartRoutesToCaptureUpload:
    """Multipart uploads must call capture_upload, not upsert_from_upload directly."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("KMS_DAEMON_API_KEY", API_KEY)

    def test_multipart_with_file_calls_capture_upload_with_raw_bytes(self, db, tmp_path):
        """Sending a file in multipart → capture_upload receives raw_bytes + mime_type."""
        import mcp_server.api as api

        api._db_path = db
        api._blob_store = None  # blob_store is None for this test (mock short-circuits)

        from unittest.mock import AsyncMock, patch

        # Mock capture_upload to return a known document id
        async def _fake_capture_upload(**kwargs):
            from core.result import Success
            return Success(99)

        with patch.object(api, "capture_upload", side_effect=_fake_capture_upload) as mock_cap:
            # Also patch upsert_from_upload (create=True since it may not be
            # imported) to detect if it's called directly
            with patch.object(api, "upsert_from_upload", create=True) as mock_upsert:
                app = Starlette(routes=api.api_routes)
                client = TestClient(app)

                resp = client.post(
                    "/api/upload",
                    data={
                        "vault_path": "uploads/photo.png",
                        "content_hash": "abc-photo",
                        "original_filename": "photo.png",
                        "file_size_bytes": "4096",
                    },
                    files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n...fake...", "image/png")},
                    headers={"Authorization": f"Bearer {API_KEY}"},
                )

                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert data["document_id"] == 99

                # capture_upload must have been called exactly once
                mock_cap.assert_awaited_once()
                call_kwargs = mock_cap.call_args.kwargs

                assert call_kwargs["vault_path"] == "uploads/photo.png"
                assert call_kwargs["extracted_text"] is None
                assert call_kwargs["content_hash"] == "abc-photo"
                assert call_kwargs["raw_bytes"] == b"\x89PNG\r\n\x1a\n...fake..."
                assert call_kwargs["mime_type"] == "image/png"
                assert call_kwargs["blob_store"] is None  # _blob_store untouched
                assert call_kwargs["original_filename"] == "photo.png"
                assert call_kwargs["file_size_bytes"] == 4096
                assert call_kwargs["db_path"] == db

                # upsert_from_upload must NOT be called by the multipart handler
                mock_upsert.assert_not_called()

    def test_multipart_without_file_calls_capture_upload_without_raw_bytes(self, db):
        """Multipart upload without a file field → 400, not 500.

        A missing file field is a client error — the server rejects it
        before calling capture_upload.
        """
        import mcp_server.api as api

        api._db_path = db
        api._blob_store = None

        from unittest.mock import patch

        async def _fake_capture_upload(**kwargs):
            from core.result import Failure
            return Failure(error="neither text nor bytes supplied", recoverable=False, context={})

        with patch.object(api, "capture_upload", side_effect=_fake_capture_upload) as mock_cap:
            app = Starlette(routes=api.api_routes)
            client = TestClient(app)

            # Send multipart with data but no "file" field
            resp = client.post(
                "/api/upload",
                data={
                    "vault_path": "uploads/metadata_only.bin",
                    "content_hash": "hash-no-file",
                },
                files={"dummy": ("", b"")},
                headers={"Authorization": f"Bearer {API_KEY}"},
            )

            # Now returns 400 (client error), not 500
            assert resp.status_code == 400
            body = resp.json()
            assert "file field is required" in body.get("detail", "")

            # capture_upload is NOT called — the guard fires first
            mock_cap.assert_not_awaited()

    def test_multipart_mime_type_from_form_field(self, db):
        """Explicit mime_type form field takes precedence over file content_type."""
        import mcp_server.api as api

        api._db_path = db

        from unittest.mock import patch

        async def _fake_capture_upload(**kwargs):
            from core.result import Success
            return Success(1)

        with patch.object(api, "capture_upload", side_effect=_fake_capture_upload) as mock_cap:
            app = Starlette(routes=api.api_routes)
            client = TestClient(app)

            resp = client.post(
                "/api/upload",
                data={
                    "vault_path": "uploads/explicit.bin",
                    "content_hash": "hash-exp",
                    "mime_type": "application/octet-stream",
                },
                files={"file": ("data.bin", b"binary", "text/plain")},
                headers={"Authorization": f"Bearer {API_KEY}"},
            )

            assert resp.status_code == 200
            call_kwargs = mock_cap.call_args.kwargs
            # mime_type from form field wins over file.content_type
            assert call_kwargs["mime_type"] == "application/octet-stream"


class TestUpsertFromUploadNullExtractedText:
    """Call upsert_from_upload(extracted_text=None) directly."""

    def test_null_extracted_text_inserts_null_full_body(self, db):
        """upsert_from_upload(extracted_text=None) → row with NULL full_body."""
        from storage.documents import get_by_path, upsert_from_upload

        result = upsert_from_upload(
            vault_path="binaries/photo.png",
            extracted_text=None,
            content_hash="img-hash-001",
            original_filename="photo.png",
            file_size_bytes=4096,
            db_path=db,
        )

        assert result.is_success()
        doc_id = result.value
        assert isinstance(doc_id, int)

        row = get_by_path("binaries/photo.png", db_path=db)
        assert row.is_success()
        doc = row.value
        assert doc is not None
        assert doc.full_body is None
        assert doc.content_hash == "img-hash-001"
        assert doc.original_filename == "photo.png"
        assert doc.file_size_bytes == 4096
