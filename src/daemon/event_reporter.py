"""
daemon/event_reporter.py

HTTP event reporting with exponential-backoff retry for the sync daemon.

Sends moved/deleted events to the cloud event endpoint.  Uses the shared
``_retry_with_backoff`` helper from ``daemon.uploader``.

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
from daemon.config import DaemonConfig
from daemon.uploader import _retry_with_backoff


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
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    match await _retry_with_backoff(client, config, _request):
        case Success():
            return Success(None)
        case Failure() as f:
            return f


async def report_deleted(
    client: httpx.AsyncClient,
    config: DaemonConfig,
    path: str,
) -> Result[None]:
    """Report a deleted document event to the cloud.

    POST ``{cloud_endpoint}/api/event`` with JSON body::

        {"type": "deleted", "path": "..."}

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
                "path": path,
            },
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    match await _retry_with_backoff(client, config, _request):
        case Success():
            return Success(None)
        case Failure() as f:
            return f
