"""
tests/test_daemon/test_uploader.py

Tests for daemon/uploader.py — upload_text, upload_binary, and _retry_with_backoff.

Uses ``httpx.MockTransport`` for deterministic HTTP simulation.

Test map:
  Section 1 — _retry_with_backoff (shared helper)
    - success on first attempt
    - success after transient 500 then 200
    - fail immediately on 401 (client error)
    - fail after exhausting retries on 503
    - retry on httpx.ConnectError
    - exponential backoff timing
  Section 2 — upload_text
    - correct JSON body and auth header
    - returns Success(document_id) on 200
    - returns Failure on missing document_id
    - title derived from vault_path stem
  Section 3 — upload_binary
    - correct multipart body and auth header
    - returns Success(document_id) on 200
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from core.result import Failure, Success
from daemon.config import DaemonConfig
from daemon.extractor import BinaryContent, TextContent
from daemon._http_retry import retry_with_backoff as _retry_with_backoff
from daemon.uploader import upload_binary, upload_text


# ===========================================================================
# Helpers
# ===========================================================================


def _make_config(
    *,
    tmp_path: Path,
    vault_root: Path | None = None,
    cloud_endpoint: str = "https://cloud.example.com",
    api_key: str = "test-api-key",
    retry_max: int = 3,
) -> DaemonConfig:
    """Build a DaemonConfig for testing."""
    root = vault_root or tmp_path
    root.mkdir(parents=True, exist_ok=True)
    return DaemonConfig(
        vault_root=root,
        cloud_endpoint=cloud_endpoint,
        api_key=api_key,
        retry_max=retry_max,
    )


def _text_content(
    *,
    text: str = "hello world",
    content_hash: str = "abc123",
    vault_path: str = "notes/hello.md",
    original_filename: str = "hello.md",
    file_size_bytes: int = 1024,
) -> TextContent:
    return TextContent(
        text=text,
        content_hash=content_hash,
        vault_path=vault_path,
        original_filename=original_filename,
        file_size_bytes=file_size_bytes,
    )


def _binary_content(
    *,
    raw_bytes: bytes = b"\x00\x01\x02",
    content_hash: str = "def456",
    vault_path: str = "images/photo.png",
    original_filename: str = "photo.png",
    file_size_bytes: int = 2048,
    mime_type: str = "image/png",
) -> BinaryContent:
    return BinaryContent(
        raw_bytes=raw_bytes,
        content_hash=content_hash,
        vault_path=vault_path,
        original_filename=original_filename,
        file_size_bytes=file_size_bytes,
        mime_type=mime_type,
    )


def _mock_transport_json(body: dict, status_code: int = 200) -> httpx.MockTransport:
    """Return a MockTransport that returns a JSON response."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


def _mock_transport_sequence(
    responses: list[httpx.Response],
) -> httpx.MockTransport:
    """Return a MockTransport that returns responses in sequence."""
    idx = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal idx
        if idx < len(responses):
            resp = responses[idx]
            idx += 1
            return resp
        return httpx.Response(500, json={"status": "error"})

    return httpx.MockTransport(handler)


# ===========================================================================
# Section 1 — _retry_with_backoff
# ===========================================================================


class TestRetryWithBackoff:
    async def test_success_on_first_attempt(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        transport = _mock_transport_json({"status": "ok", "document_id": 42})
        client = httpx.AsyncClient(transport=transport)

        async def _req():
            return await client.post("https://cloud.example.com/api/upload", json={})

        match await _retry_with_backoff(client, config, _req):
            case Success(value=resp):
                assert resp.status_code == 200
                assert resp.json() == {"status": "ok", "document_id": 42}
            case Failure() as f:
                pytest.fail(f"unexpected failure: {f.error}")

    async def test_success_after_transient_500(self, tmp_path: Path):
        """One 500, then 200 → success."""
        config = _make_config(tmp_path=tmp_path)
        transport = _mock_transport_sequence(
            [
                httpx.Response(500, json={"status": "error"}),
                httpx.Response(200, json={"status": "ok", "document_id": 7}),
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        async def _req():
            return await client.post("https://cloud.example.com/api/upload", json={})

        match await _retry_with_backoff(client, config, _req):
            case Success(value=resp):
                assert resp.status_code == 200
                assert resp.json()["document_id"] == 7
            case Failure() as f:
                pytest.fail(f"unexpected failure: {f.error}")

    async def test_fail_immediately_on_401(self, tmp_path: Path):
        """Client errors (401) are not retried."""
        config = _make_config(tmp_path=tmp_path)
        transport = _mock_transport_json(
            {"status": "unauthorized"}, status_code=401
        )
        client = httpx.AsyncClient(transport=transport)

        call_count = 0

        async def _req():
            nonlocal call_count
            call_count += 1
            return await client.post("https://cloud.example.com/api/upload", json={})

        match await _retry_with_backoff(client, config, _req):
            case Failure(error=err, recoverable=False):
                assert "401" in err
                assert call_count == 1  # no retry
            case _:
                pytest.fail("expected Failure with recoverable=False")

    async def test_fail_after_exhausting_retries(self, tmp_path: Path):
        """All 503s → return Failure(recoverable=True) after retry_max attempts."""
        config = _make_config(tmp_path=tmp_path, retry_max=3)
        responses = [httpx.Response(503, json={"status": "error"}) for _ in range(4)]
        transport = _mock_transport_sequence(responses)
        client = httpx.AsyncClient(transport=transport)

        call_count = 0

        async def _req():
            nonlocal call_count
            call_count += 1
            return await client.post("https://cloud.example.com/api/upload", json={})

        match await _retry_with_backoff(client, config, _req):
            case Failure(error=err, recoverable=True):
                assert "503" in err
                assert call_count == 3  # retry_max attempts, no more
            case _:
                pytest.fail("expected Failure with recoverable=True")

    async def test_retry_on_connect_error(self, tmp_path: Path):
        """Connection errors are transient and retried."""
        config = _make_config(tmp_path=tmp_path, retry_max=3)
        call_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"status": "ok", "document_id": 99})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        async def _req():
            return await client.post("https://cloud.example.com/api/upload", json={})

        match await _retry_with_backoff(client, config, _req):
            case Success(value=resp):
                assert resp.json()["document_id"] == 99
                assert call_count == 2
            case Failure() as f:
                pytest.fail(f"unexpected failure: {f.error}")

    async def test_exponential_backoff_timing(self, tmp_path: Path):
        """Delays should approximately follow 1, 2, 4, ... seconds."""
        config = _make_config(tmp_path=tmp_path, retry_max=4)
        # All requests fail with 503 so we see all backoffs
        responses = [httpx.Response(503, json={"status": "error"}) for _ in range(5)]
        transport = _mock_transport_sequence(responses)
        client = httpx.AsyncClient(transport=transport)

        sleep_times: list[float] = []

        async def fake_sleep(delay: float):
            sleep_times.append(delay)
            # Don't actually sleep — just record

        with patch("daemon._http_retry.asyncio.sleep", fake_sleep):
            async def _req():
                return await client.post("https://cloud.example.com/api/upload", json={})
            await _retry_with_backoff(client, config, _req)

        # Expected delays: 1.0, 2.0, 4.0 (3 delays for 4 attempts)
        assert len(sleep_times) == 3
        assert sleep_times[0] == pytest.approx(1.0)
        assert sleep_times[1] == pytest.approx(2.0)
        assert sleep_times[2] == pytest.approx(4.0)


# ===========================================================================
# Section 2 — upload_text
# ===========================================================================


class TestUploadText:
    async def test_sends_correct_json_and_auth(self, tmp_path: Path):
        """Verify the JSON body and Bearer auth header sent to the cloud."""
        config = _make_config(tmp_path=tmp_path, cloud_endpoint="https://cloud.example.com")
        content = _text_content(
            text="sample text",
            content_hash="hash123",
            vault_path="notes/readme.md",
            original_filename="readme.md",
            file_size_bytes=512,
        )

        sent_json: dict = {}
        sent_url: str = ""
        sent_headers: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal sent_json, sent_url, sent_headers
            sent_url = str(request.url)
            sent_headers = dict(request.headers)
            sent_json = json.loads(request.content)
            return httpx.Response(200, json={"status": "ok", "document_id": 42})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        result = await upload_text(client, config, content)

        match result:
            case Success(value=doc_id):
                assert doc_id == 42
            case _:
                pytest.fail("expected Success")

        # Check URL
        assert sent_url == "https://cloud.example.com/api/upload"

        # Check auth header
        assert sent_headers.get("authorization") == "Bearer test-api-key"

        # Check JSON body
        assert sent_json["vault_path"] == "notes/readme.md"
        assert sent_json["extracted_text"] == "sample text"
        assert sent_json["content_hash"] == "hash123"
        assert sent_json["original_filename"] == "readme.md"
        assert sent_json["file_size_bytes"] == 512
        assert sent_json["title"] == "readme"  # stem of "readme.md"

    async def test_returns_success_with_document_id(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        content = _text_content()
        transport = _mock_transport_json({"status": "ok", "document_id": 99})
        client = httpx.AsyncClient(transport=transport)

        match await upload_text(client, config, content):
            case Success(value=doc_id):
                assert doc_id == 99
            case _:
                pytest.fail("expected Success(99)")

    async def test_returns_failure_on_missing_document_id(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        content = _text_content()
        transport = _mock_transport_json({"status": "ok"})  # no document_id
        client = httpx.AsyncClient(transport=transport)

        match await upload_text(client, config, content):
            case Failure(error=err, recoverable=False):
                assert "document_id" in err.lower()
            case _:
                pytest.fail("expected Failure")

    async def test_title_derived_from_vault_path_stem(self, tmp_path: Path):
        """title should be Path(stem) of vault_path."""
        config = _make_config(tmp_path=tmp_path)
        content = _text_content(vault_path="projects/docs/architecture.md")
        captured_title: str | None = None

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_title
            captured_title = json.loads(request.content).get("title")
            return httpx.Response(200, json={"status": "ok", "document_id": 1})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        await upload_text(client, config, content)
        assert captured_title == "architecture"


# ===========================================================================
# Section 3 — upload_binary
# ===========================================================================


class TestUploadBinary:
    async def test_sends_multipart_with_correct_fields(self, tmp_path: Path):
        """Verify multipart body, metadata fields, and auth header."""
        config = _make_config(tmp_path=tmp_path, cloud_endpoint="https://cloud.example.com")
        content = _binary_content(
            raw_bytes=b"binary-data",
            content_hash="binhash",
            vault_path="images/logo.png",
            original_filename="logo.png",
            file_size_bytes=2048,
            mime_type="image/png",
        )

        sent_url: str = ""
        sent_headers: dict = {}
        sent_body: bytes = b""

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal sent_url, sent_headers, sent_body
            sent_url = str(request.url)
            sent_headers = dict(request.headers)
            sent_body = request.content
            return httpx.Response(200, json={"status": "ok", "document_id": 10})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        result = await upload_binary(client, config, content)

        match result:
            case Success(value=doc_id):
                assert doc_id == 10
            case _:
                pytest.fail("expected Success")

        assert sent_url == "https://cloud.example.com/api/upload"
        assert sent_headers.get("authorization") == "Bearer test-api-key"

        # The content-type should be multipart/form-data
        content_type = sent_headers.get("content-type", "")
        assert "multipart/form-data" in content_type

        # The body should contain our metadata field names
        body_str = sent_body.decode("utf-8", errors="replace")
        assert 'name="vault_path"' in body_str
        assert "images/logo.png" in body_str
        assert 'name="content_hash"' in body_str
        assert "binhash" in body_str
        assert 'name="original_filename"' in body_str
        assert 'name="file_size_bytes"' in body_str
        assert "2048" in body_str
        assert 'name="mime_type"' in body_str
        assert "image/png" in body_str
        # File field
        assert 'name="file"' in body_str
        assert "logo.png" in body_str  # filename in file part

    async def test_returns_success_with_document_id(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        content = _binary_content()
        transport = _mock_transport_json({"status": "ok", "document_id": 55})
        client = httpx.AsyncClient(transport=transport)

        match await upload_binary(client, config, content):
            case Success(value=doc_id):
                assert doc_id == 55
            case _:
                pytest.fail("expected Success(55)")

    async def test_returns_failure_on_missing_document_id(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        content = _binary_content()
        transport = _mock_transport_json({"status": "ok"})  # no document_id
        client = httpx.AsyncClient(transport=transport)

        match await upload_binary(client, config, content):
            case Failure(error=err, recoverable=False):
                assert "document_id" in err.lower()
            case _:
                pytest.fail("expected Failure")

    async def test_retry_on_server_error(self, tmp_path: Path):
        """Binary upload should retry on 500."""
        config = _make_config(tmp_path=tmp_path, retry_max=3)
        content = _binary_content()
        transport = _mock_transport_sequence(
            [
                httpx.Response(500, json={"status": "error"}),
                httpx.Response(200, json={"status": "ok", "document_id": 3}),
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        match await upload_binary(client, config, content):
            case Success(value=doc_id):
                assert doc_id == 3
            case _:
                pytest.fail("expected Success after retry")
