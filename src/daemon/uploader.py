"""
daemon/uploader.py

HTTP uploads with exponential-backoff retry for the sync daemon.

Sends extracted text (JSON) or binary content (multipart) to the cloud
upload endpoint.  Retry logic is shared via ``daemon._http_retry``.

Usage:
    from daemon.uploader import upload_text, upload_binary

    match await upload_text(client, config, text_content):
        case Success(value=doc_id):
            logger.info("uploaded", doc_id=doc_id)
        case Failure() as f:
            logger.error("upload failed", **f.to_log_dict())
"""

from __future__ import annotations

from pathlib import Path

import httpx

from core.result import Failure, Result, Success
from daemon._http_retry import retry_with_backoff
from daemon.config import DaemonConfig
from daemon.extractor import BinaryContent, TextContent


# ── helpers ──────────────────────────────────────────────────────────────────


def _parse_upload_response(response: httpx.Response) -> Result[int]:
    """Extract ``document_id`` from a cloud upload response.

    Returns:
        ``Success(document_id)`` on success.
        ``Failure(recoverable=False)`` if the response body is malformed or
        missing the expected ``document_id`` field.
    """
    # Cloud responds with {"status": "ok", "document_id": <int>}
    try:
        body: dict = response.json()
    except Exception as exc:
        return Failure(
            error=f"Invalid JSON in upload response: {exc}",
            recoverable=False,
            context={"status_code": response.status_code},
        )
    if not isinstance(body, dict):
        return Failure(
            error=f"Upload response is not a JSON object: {type(body).__name__}",
            recoverable=False,
            context={"status_code": response.status_code},
        )
    doc_id = body.get("document_id")
    if not isinstance(doc_id, int) or doc_id < 0:
        return Failure(
            error=f"Upload response missing or invalid document_id: {body}",
            recoverable=False,
            context={"response": body},
        )
    return Success(doc_id)


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

    The caller's ``httpx.AsyncClient`` should be configured with a timeout
    to prevent hung requests (e.g. ``httpx.AsyncClient(timeout=30)``).

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

    match await retry_with_backoff(client, config, _request):
        case Success(value=response):
            return _parse_upload_response(response)
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

    The caller's ``httpx.AsyncClient`` should be configured with a timeout
    to prevent hung requests (e.g. ``httpx.AsyncClient(timeout=30)``).

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

    match await retry_with_backoff(client, config, _request):
        case Success(value=response):
            return _parse_upload_response(response)
        case Failure() as f:
            return f
