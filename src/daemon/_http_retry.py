"""
daemon/_http_retry.py

Shared HTTP retry helper with exponential backoff for the sync daemon.

Used by both ``uploader.py`` and ``event_reporter.py`` to provide
consistent retry behavior across all daemon HTTP calls.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import httpx

from core.result import Failure, Result, Success
from daemon.config import DaemonConfig

# ── retry configuration ──────────────────────────────────────────────────────

# HTTP status codes that trigger a retry (transient server errors + rate limit).
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503})

# Connection-level exceptions that trigger a retry.
_TRANSIENT_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
    httpx.NetworkError,  # covers ConnectError, ReadError, WriteError, etc.
)


async def retry_with_backoff(
    client: httpx.AsyncClient,
    config: DaemonConfig,
    request_fn: Callable[[], Awaitable[httpx.Response]],
) -> Result[httpx.Response]:
    """Execute *request_fn* with exponential-backoff retry.

    - Base delay: 1 second, doubled on each retry.
    - Max *config.retry_max* attempts (minimum 1).
    - Retries on transient errors: HTTP 429, 500, 502, 503, or connection errors.
    - Returns ``Failure`` on 4xx responses (not retried — auth / bad request).
    - After exhausting retries, returns ``Failure(recoverable=True)``.
    """
    last_failure: Failure | None = None

    for attempt in range(1, config.retry_max + 1):
        try:
            response = await request_fn()
        except _TRANSIENT_EXCEPTIONS as exc:
            last_failure = Failure(
                error=f"HTTP request failed (attempt {attempt}/{config.retry_max}): {exc}",
                recoverable=True,
                context={"attempt": attempt, "exception": type(exc).__name__},
            )
        else:
            # ── Transient server error / rate limit → retry ──────────────
            if response.status_code in _TRANSIENT_STATUSES:
                last_failure = Failure(
                    error=f"Server error {response.status_code} (attempt {attempt}/{config.retry_max})",
                    recoverable=True,
                    context={
                        "attempt": attempt,
                        "status_code": response.status_code,
                    },
                )
            # ── Client error (4xx, except 429) → fail immediately ─────────
            elif 400 <= response.status_code < 500:
                return Failure(
                    error=f"Client error {response.status_code}: {response.text[:200]}",
                    recoverable=False,
                    context={
                        "status_code": response.status_code,
                        "attempt": attempt,
                    },
                )
            # ── Success (2xx, 3xx) or 1xx → done ─────────────────────────
            else:
                return Success(response)

        # ── Backoff before next retry ─────────────────────────────────────
        if attempt < config.retry_max:
            delay = 1.0 * (2 ** (attempt - 1))  # 1, 2, 4, 8, ...
            await asyncio.sleep(delay)

    # ── Exhausted all retries ─────────────────────────────────────────────
    if last_failure is None:
        return Failure(
            error="No retry attempts made (retry_max may be 0)",
            recoverable=False,
        )
    return last_failure
