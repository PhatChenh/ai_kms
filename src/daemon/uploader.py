"""
daemon/uploader.py

HTTP uploads with exponential-backoff retry for the sync daemon.

Sends extracted text (JSON) or binary content (multipart) to the cloud
upload endpoint.  Shares a private ``_retry_with_backoff`` helper with
``event_reporter.py``.

Usage:
    from daemon.uploader import upload_text, upload_binary

    match await upload_text(client, config, text_content):
        case Success(value=doc_id):
            logger.info("uploaded", doc_id=doc_id)
        case Failure() as f:
            logger.error("upload failed", **f.to_log_dict())
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from core.result import Failure, Result, Success
from daemon.config import DaemonConfig
from daemon.extractor import BinaryContent, TextContent

# ── shared retry helper ──────────────────────────────────────────────────────


async def _retry_with_backoff(
    client: httpx.AsyncClient,
    config: DaemonConfig,
    request_fn: Callable[[], Awaitable[httpx.Response]],
) -> Result[httpx.Response]:
    """Execute *request_fn* with exponential-backoff retry.

    - Base delay: 1 second, doubled on each retry.
    - Max *config.retry_max* attempts (minimum 1).
    - Retries on transient errors: HTTP 500, 502, 503, or connection errors.
    - Returns ``Failure`` on 4xx responses (not retried — auth / bad request).
    - After exhausting retries, returns ``Failure(recoverable=True)``.
    """
    TRANSIENT_STATUSES = frozenset({500, 502, 503})
    TRANSIENT_EXCEPTIONS = (
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.RemoteProtocolError,
        httpx.NetworkError,
    )

    last_failure: Failure | None = None

    for attempt in range(1, config.retry_max + 1):
        try:
            response = await request_fn()
        except TRANSIENT_EXCEPTIONS as exc:
            last_failure = Failure(
                error=f"HTTP request failed (attempt {attempt}/{config.retry_max}): {exc}",
                recoverable=True,
                context={"attempt": attempt, "exception": type(exc).__name__},
            )
        else:
            # ── Transient server error → retry ──────────────────────────
            if response.status_code in TRANSIENT_STATUSES:
                last_failure = Failure(
                    error=f"Server error {response.status_code} (attempt {attempt}/{config.retry_max})",
                    recoverable=True,
                    context={
                        "attempt": attempt,
                        "status_code": response.status_code,
                    },
                )
            # ── Client error (4xx) → fail immediately ───────────────────
            elif 400 <= response.status_code < 500:
                return Failure(
                    error=f"Client error {response.status_code}: {response.text[:200]}",
                    recoverable=False,
                    context={
                        "status_code": response.status_code,
                        "attempt": attempt,
                    },
                )
            # ── Success (2xx, 3xx) or 1xx → done ────────────────────────
            else:
                return Success(response)

        # ── Backoff before next retry ────────────────────────────────────
        if attempt < config.retry_max:
            delay = 1.0 * (2 ** (attempt - 1))  # 1, 2, 4, 8, ...
            await asyncio.sleep(delay)

    # ── Exhausted all retries ────────────────────────────────────────────
    assert last_failure is not None
    return last_failure


# ── public upload functions ──────────────────────────────────────────────────


async def upload_text(
    client: httpx.AsyncClient,
    config: DaemonConfig,
    content: TextContent,
) -> Result[int]:
    """Upload extracted text content as JSON to the cloud.

    POST ``{cloud_endpoint}/api/upload`` with ``application/json`` body
    containing vault_path, extracted_text, content_hash, original_filename,
    file_size_bytes, and title (derived from the vault_path stem).

    Returns:
        ``Success(document_id)`` on success.
        ``Failure`` on error (recoverable or not based on status code).
    """

    async def _request() -> httpx.Response:
        url = f"{config.cloud_endpoint}/api/upload"
        title = Path(content.vault_path).stem
        return await client.post(
            url,
            json={
                "vault_path": content.vault_path,
                "extracted_text": content.text,
                "content_hash": content.content_hash,
                "original_filename": content.original_filename,
                "file_size_bytes": content.file_size_bytes,
                "title": title,
            },
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    match await _retry_with_backoff(client, config, _request):
        case Success(value=response):
            # Cloud responds with {"status": "ok", "document_id": <int>}
            body = response.json()
            doc_id: int = body.get("document_id", -1)
            if doc_id == -1:
                return Failure(
                    error=f"Upload response missing document_id: {body}",
                    recoverable=False,
                    context={"response": body},
                )
            return Success(doc_id)
        case Failure() as f:
            return f


async def upload_binary(
    client: httpx.AsyncClient,
    config: DaemonConfig,
    content: BinaryContent,
) -> Result[int]:
    """Upload binary content as multipart/form-data to the cloud.

    POST ``{cloud_endpoint}/api/upload`` with multipart body containing
    the file bytes and metadata form fields (vault_path, content_hash,
    original_filename, file_size_bytes, mime_type).

    Returns:
        ``Success(document_id)`` on success.
        ``Failure`` on error (recoverable or not based on status code).
    """

    async def _request() -> httpx.Response:
        url = f"{config.cloud_endpoint}/api/upload"
        return await client.post(
            url,
            files={
                "file": (
                    content.original_filename,
                    content.raw_bytes,
                    content.mime_type,
                ),
            },
            data={
                "vault_path": content.vault_path,
                "content_hash": content.content_hash,
                "original_filename": content.original_filename,
                "file_size_bytes": str(content.file_size_bytes),
                "mime_type": content.mime_type,
            },
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    match await _retry_with_backoff(client, config, _request):
        case Success(value=response):
            body = response.json()
            doc_id: int = body.get("document_id", -1)
            if doc_id == -1:
                return Failure(
                    error=f"Upload response missing document_id: {body}",
                    recoverable=False,
                    context={"response": body},
                )
            return Success(doc_id)
        case Failure() as f:
            return f
