"""
tests/test_daemon/test_connection_check.py

Tests for daemon/connection_check.py — the live authed key test.

Covers:
  - 200 → Success
  - 401 → Failure naming "authentication"
  - Connection error → Failure naming "cannot reach"
  - 500 → Failure with status + body snippet
  - GUARD: request path is /api/state (NOT /health)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx

from core.result import Failure, Success
from daemon.connection_check import check_connection


# ── helpers ──────────────────────────────────────────────────────────────────


def _mock_response(status_code: int, body: str = "") -> MagicMock:
    """Return a mock httpx.Response with the given status_code and text."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = body
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=resp,
        )
    return resp


def _mock_client(response: MagicMock) -> AsyncMock:
    """Return a mock httpx.AsyncClient whose get() returns *response*."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = response
    return client


# ── tracer bullet: 200 → Success ─────────────────────────────────────────────


async def test_200_returns_success():
    """A 200 response from /api/state returns Success(None)."""
    mock_resp = _mock_response(200, '{"status": "ok"}')
    client = _mock_client(mock_resp)

    result = await check_connection("http://localhost:8080", "test-key", client=client)

    assert isinstance(result, Success)
    assert result.value is None

    # Verify the correct URL was called
    client.get.assert_called_once()
    call_args, call_kwargs = client.get.call_args
    url = call_args[0] if call_args else call_kwargs.get("url", "")
    assert url.endswith("/api/state"), f"Expected /api/state, got {url}"
    assert "/health" not in url, f"Must NOT call /health: got {url}"


# ── 401 → Failure naming "authentication" ────────────────────────────────────


async def test_401_returns_failure_naming_authentication():
    """A 401 response returns Failure whose error message contains 'authentication'."""
    mock_resp = _mock_response(401, '{"detail": "Invalid token"}')
    client = _mock_client(mock_resp)

    result = await check_connection("http://localhost:8080", "bad-key", client=client)

    assert isinstance(result, Failure)
    assert "authentication" in result.error.lower()
    assert result.recoverable is False


# ── connection error → Failure naming "cannot reach" ──────────────────────────


async def test_connection_error_returns_failure_naming_cannot_reach():
    """A connection error returns Failure whose error message contains 'cannot reach'."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = httpx.ConnectError("Connection refused")

    result = await check_connection("http://localhost:8080", "test-key", client=client)

    assert isinstance(result, Failure)
    assert "cannot reach" in result.error.lower()
    assert result.recoverable is True


# ── 500 → Failure with status + body snippet ──────────────────────────────────


async def test_500_returns_failure_with_status_and_body():
    """A 500 response returns Failure with the status code and body snippet."""
    body = '{"error": "internal server error", "trace": "abc123"}'
    mock_resp = _mock_response(500, body)
    client = _mock_client(mock_resp)

    result = await check_connection("http://localhost:8080", "test-key", client=client)

    assert isinstance(result, Failure)
    assert "500" in result.error
    assert body[:200] in result.error
    assert result.recoverable is False


# ── GUARD: request path is /api/state, NOT /health ────────────────────────────


async def test_request_path_is_api_state_not_health():
    """The only authed call MUST be to /api/state — never /health.

    /health is un-gated and would false-pass a bad key, making the
    connection check useless.  This is the single most important
    correctness rail in the slice.
    """
    mock_resp = _mock_response(200, '{"status": "ok"}')
    client = _mock_client(mock_resp)

    await check_connection("http://cloud.example.com", "key", client=client)

    client.get.assert_called_once()
    call_args, call_kwargs = client.get.call_args
    url = call_args[0] if call_args else call_kwargs.get("url", "")

    # Must end with /api/state
    assert url.endswith("/api/state"), (
        f"Expected request to /api/state, got {url}"
    )
    # Must NOT contain /health
    assert "/health" not in url, (
        f"GUARD FAILURE: request went to /health instead of /api/state: {url}"
    )
