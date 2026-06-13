"""
tests/test_daemon/test_event_reporter.py

Tests for daemon/event_reporter.py — report_moved and report_deleted.

Uses ``httpx.MockTransport`` for deterministic HTTP simulation.

Test map:
  Section 1 — report_moved
    - correct JSON body with "type": "moved"
    - Bearer auth header
    - Success on 200
    - Retry on 500
    - Fail immediately on 401
  Section 2 — report_deleted
    - correct JSON body with "type": "deleted"
    - Bearer auth header
    - Success on 200
    - Retry on 503
    - Fail immediately on 400
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from core.result import Failure, Success
from daemon.config import DaemonConfig
from daemon.event_reporter import report_deleted, report_moved


# ===========================================================================
# Helpers
# ===========================================================================


def _make_config(
    *,
    tmp_path: Path,
    cloud_endpoint: str = "https://cloud.example.com",
    api_key: str = "test-api-key",
    retry_max: int = 3,
) -> DaemonConfig:
    """Build a DaemonConfig for testing."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    return DaemonConfig(
        vault_root=tmp_path,
        cloud_endpoint=cloud_endpoint,
        api_key=api_key,
        retry_max=retry_max,
    )


def _mock_transport_json(body: dict, status_code: int = 200) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)
    return httpx.MockTransport(handler)


def _mock_transport_sequence(
    responses: list[httpx.Response],
) -> httpx.MockTransport:
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
# Section 1 — report_moved
# ===========================================================================


class TestReportMoved:
    async def test_sends_correct_json_and_auth(self, tmp_path: Path):
        """Verify JSON body with 'type': 'moved' and Bearer auth."""
        config = _make_config(tmp_path=tmp_path, cloud_endpoint="https://cloud.example.com")
        sent_json: dict = {}
        sent_url: str = ""
        sent_headers: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal sent_json, sent_url, sent_headers
            sent_url = str(request.url)
            sent_headers = dict(request.headers)
            sent_json = json.loads(request.content)
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        result = await report_moved(client, config, "notes/old.md", "notes/new.md")

        match result:
            case Success():
                pass
            case _:
                pytest.fail("expected Success")

        assert sent_url == "https://cloud.example.com/api/event"
        assert sent_headers.get("authorization") == "Bearer test-api-key"

        # Check JSON uses "type" NOT "event_type"
        assert sent_json["type"] == "moved"
        assert "event_type" not in sent_json
        assert sent_json["old_path"] == "notes/old.md"
        assert sent_json["new_path"] == "notes/new.md"

    async def test_returns_success_on_200(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        transport = _mock_transport_json({"status": "ok"})
        client = httpx.AsyncClient(transport=transport)

        match await report_moved(client, config, "a.md", "b.md"):
            case Success():
                pass
            case _:
                pytest.fail("expected Success(None)")

    async def test_retry_on_500_then_succeed(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, retry_max=3)
        transport = _mock_transport_sequence(
            [
                httpx.Response(500, json={"status": "error"}),
                httpx.Response(200, json={"status": "ok"}),
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        match await report_moved(client, config, "a.md", "b.md"):
            case Success():
                pass
            case _:
                pytest.fail("expected Success after retry")

    async def test_fail_immediately_on_401(self, tmp_path: Path):
        """401 is a client error — not transient, not retried."""
        config = _make_config(tmp_path=tmp_path, retry_max=3)
        transport = _mock_transport_json({"status": "unauthorized"}, status_code=401)
        client = httpx.AsyncClient(transport=transport)

        match await report_moved(client, config, "a.md", "b.md"):
            case Failure(error=err, recoverable=False):
                assert "401" in err
            case _:
                pytest.fail("expected Failure(recoverable=False)")


# ===========================================================================
# Section 2 — report_deleted
# ===========================================================================


class TestReportDeleted:
    async def test_sends_correct_json_and_auth(self, tmp_path: Path):
        """Verify JSON body with 'type': 'deleted' and Bearer auth."""
        config = _make_config(tmp_path=tmp_path, cloud_endpoint="https://cloud.example.com")
        sent_json: dict = {}
        sent_url: str = ""
        sent_headers: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal sent_json, sent_url, sent_headers
            sent_url = str(request.url)
            sent_headers = dict(request.headers)
            sent_json = json.loads(request.content)
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        result = await report_deleted(client, config, "notes/removed.md")

        match result:
            case Success():
                pass
            case _:
                pytest.fail("expected Success")

        assert sent_url == "https://cloud.example.com/api/event"
        assert sent_headers.get("authorization") == "Bearer test-api-key"

        # Check JSON uses "type" NOT "event_type"
        assert sent_json["type"] == "deleted"
        assert "event_type" not in sent_json
        assert sent_json["path"] == "notes/removed.md"

    async def test_returns_success_on_200(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        transport = _mock_transport_json({"status": "ok"})
        client = httpx.AsyncClient(transport=transport)

        match await report_deleted(client, config, "notes/deleted.md"):
            case Success():
                pass
            case _:
                pytest.fail("expected Success(None)")

    async def test_retry_on_503_then_succeed(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, retry_max=3)
        transport = _mock_transport_sequence(
            [
                httpx.Response(503, json={"status": "error"}),
                httpx.Response(200, json={"status": "ok"}),
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        match await report_deleted(client, config, "notes/deleted.md"):
            case Success():
                pass
            case _:
                pytest.fail("expected Success after retry")

    async def test_fail_immediately_on_400(self, tmp_path: Path):
        """400 is a client error — not transient, not retried."""
        config = _make_config(tmp_path=tmp_path, retry_max=3)
        transport = _mock_transport_json({"status": "error", "detail": "bad request"}, status_code=400)
        client = httpx.AsyncClient(transport=transport)

        match await report_deleted(client, config, "notes/deleted.md"):
            case Failure(error=err, recoverable=False):
                assert "400" in err
            case _:
                pytest.fail("expected Failure(recoverable=False)")
