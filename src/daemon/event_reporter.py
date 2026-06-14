"""
daemon/event_reporter.py

HTTP event reporting with exponential-backoff retry for the sync daemon.

Sends moved/deleted events to the cloud event endpoint.  Retry logic is
shared via ``daemon._http_retry``.

Usage:
    from daemon.event_reporter import report_moved, report_deleted

    match await report_moved(client, config, old_path, new_path):
        case Success():
            logger.info("moved event reported")
        case Failure() as f:
            logger.error("moved event failed", **f.to_log_dict())
"""

from __future__ import annotations

import httpx

from core.result import Failure, Result, Success
from daemon._http_retry import retry_with_backoff
from daemon.config import DaemonConfig


# ── public event-reporting functions ─────────────────────────────────────────


async def report_moved(
    client: httpx.AsyncClient,
    config: DaemonConfig,
    old_path: str,
    new_path: str,
) -> Result[None]:
    """Report a moved/renamed document event to the cloud.

    POST ``{cloud_endpoint}/api/event`` with JSON body::

        {"type": "moved", "old_path": "...", "new_path": "..."}

    The caller's ``httpx.AsyncClient`` should be configured with a timeout
    to prevent hung requests (e.g. ``httpx.AsyncClient(timeout=30)``).

    Returns:
        ``Success(None)`` on success.
        ``Failure`` on error.
    """

    async def _request() -> httpx.Response:
        url = f"{config.cloud_endpoint}/api/event"
        return await client.post(
            url,
            json={
                "type": "moved",
                "old_path": old_path,
                "new_path": new_path,
            },
        )

    match await retry_with_backoff(client, config, _request):
        case Success():
            return Success(None)
        case Failure() as f:
            return f


async def report_deleted(
    client: httpx.AsyncClient,
    config: DaemonConfig,
    vault_path: str,
) -> Result[None]:
    """Report a deleted document event to the cloud.

    POST ``{cloud_endpoint}/api/event`` with JSON body::

        {"type": "deleted", "path": "..."}

    The caller's ``httpx.AsyncClient`` should be configured with a timeout
    to prevent hung requests (e.g. ``httpx.AsyncClient(timeout=30)``).

    Returns:
        ``Success(None)`` on success.
        ``Failure`` on error.
    """

    async def _request() -> httpx.Response:
        url = f"{config.cloud_endpoint}/api/event"
        return await client.post(
            url,
            json={
                "type": "deleted",
                "path": vault_path,
            },
        )

    match await retry_with_backoff(client, config, _request):
        case Success():
            return Success(None)
        case Failure() as f:
            return f
