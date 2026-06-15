"""
mcp_server/api.py — REST handlers + secret-key gate + health route (Phase 3)

Implements C2-2: upload, state, event, and health endpoints with a bearer-token gate
scoped to /api/* only.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core.result import Failure, Result, Success
from pipelines.capture import capture_upload
from storage.blobs import BlobStore
from storage.db import get_connection
from storage.documents import (
    all_paths,
    delete_by_path,
    get_by_path,
    rename,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 8 — Live-enqueue helper
# ---------------------------------------------------------------------------


def _push_to_classify_queue(request: Request, document_id: int) -> None:
    """Push *document_id* onto the classify work queue if one is present.

    The queue is published on app.state by the composed lifespan in
    cloud_entry.py.  When absent (CLI / tests), skip silently — the
    catch-up scan is the safety net.
    """
    queue = getattr(request.app.state, "classify_queue", None)
    if queue is not None:
        try:
            queue.put_nowait(document_id)
        except Exception:
            _log.warning(
                "classify_queue push failed — doc_id=%s will be picked up by next "
                "catch-up scan",
                document_id,
            )


# Testability injection point — set this to an explicit Path in tests to
# override the default CONFIG-derived database path.  None = use CONFIG.
_db_path: Path | None = None

# Blob store injection point — set this in tests to a LocalBlobStore, or
# in production to an S3BlobStore.  None = no blob cleanup (text-only).
_blob_store: BlobStore | None = None

# API key read-once cache — read from env at import time so require_key()
# does not re-read os.environ on every request.
_daemon_api_key: str | None = os.environ.get("KMS_DAEMON_API_KEY")


# ============================================================================
# Secret-key gate
# ============================================================================


def require_key(request: Request) -> str | None:
    """Extract and validate the bearer token from *request*.

    Reads ``KMS_DAEMON_API_KEY`` from the environment.  Returns the key on
    match, ``None`` on mismatch or missing header.

    Short-circuits on missing header or non-Bearer prefix.

    The key is read once: first from the module-level ``_daemon_api_key``
    (set at import time), with a lazy fallback to ``os.environ`` on first
    call if the import-time read yielded ``None`` (e.g. in tests where the
    env var is patched after import).
    """
    global _daemon_api_key

    auth = request.headers.get("Authorization")
    if auth is None:
        return None
    if not auth.startswith("Bearer "):
        return None

    token = auth[len("Bearer ") :]
    expected = _daemon_api_key
    if expected is None:
        expected = os.environ.get("KMS_DAEMON_API_KEY")
        _daemon_api_key = expected  # cache for subsequent calls
    if expected is None:
        return None
    if not hmac.compare_digest(token, expected):
        return None
    return token


def _sanitize_vault_path(vp: str) -> str | None:
    """Reject vault_path values that could cause path-traversal or DB pollution.

    Returns the cleaned path, or None if invalid.
    """
    if not vp or not isinstance(vp, str):
        return None
    if "\x00" in vp:
        return None
    if vp.startswith("/"):
        return None
    if ".." in vp.split("/"):
        return None
    return vp


# ============================================================================
# Handlers
# ============================================================================


async def health_handler(request: Request) -> JSONResponse:
    """Open health check — never gated."""
    return JSONResponse({"status": "ok"})


async def upload_handler(request: Request) -> JSONResponse:
    """Accept an uploaded document, upsert it, return the document id.

    **JSON text path** (Content-Type: application/json)::

        {
            "vault_path": "...",
            "extracted_text": "...",
            "content_hash": "...",
            "original_filename": "...",   (optional)
            "file_size_bytes": 123,       (optional)
            "title": "...",               (optional)
            "metadata": { ... }           (accepted and discarded)
        }

    **Binary path** (Content-Type: multipart/form-data)::

        Form fields:
            vault_path        (required)
            content_hash      (required)
            original_filename (optional)
            file_size_bytes   (optional, integer ≥ 0)
            mime_type         (optional, used for vision routing)
            file              (required, raw file bytes)

        ``extracted_text`` is set to ``None`` — ``full_body`` will be NULL.

    Requires valid ``Authorization: Bearer <KMS_DAEMON_API_KEY>``.
    """
    key = require_key(request)
    if key is None:
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    content_type: str = request.headers.get("content-type", "")

    # ── Multipart binary path ──────────────────────────────────────────
    if content_type.startswith("multipart/form-data"):
        try:
            form = await request.form()
        except Exception:
            return JSONResponse(
                {"status": "error", "detail": "invalid multipart form data"},
                status_code=400,
            )

        # Extract all fields and close the form before validation/early-returns
        try:
            vault_path_raw: str | None = form.get("vault_path")
            content_hash_raw: str | None = form.get("content_hash")
            original_filename: str | None = form.get("original_filename") or None
            file_size_bytes_raw: str | None = form.get("file_size_bytes")

            raw_bytes: bytes | None = None
            mime_type_str: str | None = None
            file_upload = form.get("file")
            if file_upload is not None:
                raw_bytes = await file_upload.read()
                mime_type_str = (
                    form.get("mime_type") or file_upload.content_type or None
                )
        finally:
            await form.close()

        vault_path: str | None = _sanitize_vault_path(vault_path_raw or "")
        if not vault_path:
            return JSONResponse(
                {"status": "error", "detail": "vault_path is required or invalid"},
                status_code=400,
            )

        content_hash: str | None = content_hash_raw
        if not content_hash:
            return JSONResponse(
                {"status": "error", "detail": "content_hash is required"},
                status_code=400,
            )

        file_size_bytes: int | None = None
        if file_size_bytes_raw:
            try:
                file_size_bytes = int(file_size_bytes_raw)
            except (ValueError, TypeError):
                return JSONResponse(
                    {"status": "error", "detail": "file_size_bytes must be an integer"},
                    status_code=400,
                )
            if file_size_bytes < 0:
                return JSONResponse(
                    {
                        "status": "error",
                        "detail": "file_size_bytes must be non-negative",
                    },
                    status_code=400,
                )

        # No file field in multipart upload is a client error (400), not server
        if raw_bytes is None:
            return JSONResponse(
                {
                    "status": "error",
                    "detail": "file field is required for multipart uploads",
                },
                status_code=400,
            )

        result = await capture_upload(
            vault_path=vault_path,
            extracted_text=None,
            content_hash=content_hash,
            raw_bytes=raw_bytes,
            mime_type=mime_type_str,
            blob_store=_blob_store,
            original_filename=original_filename,
            file_size_bytes=file_size_bytes,
            db_path=_db_path,
        )

        match result:
            case Success(document_id):
                # Phase 8 — Live-enqueue: push new doc onto the classify queue
                _push_to_classify_queue(request, document_id)
                return JSONResponse({"status": "ok", "document_id": document_id})
            case Failure(error=err):
                return JSONResponse(
                    {"status": "error", "detail": str(err)},
                    status_code=500,
                )

    # ── JSON text path (existing) ──────────────────────────────────────
    if content_type.startswith("application/json"):
        try:
            body: Any = await request.json()
        except Exception:
            return JSONResponse(
                {"status": "error", "detail": "invalid JSON body"},
                status_code=400,
            )

        if not isinstance(body, dict):
            return JSONResponse(
                {"status": "error", "detail": "body must be a JSON object"},
                status_code=400,
            )

        vault_path = _sanitize_vault_path(body.get("vault_path") or "")
        if not vault_path:
            return JSONResponse(
                {"status": "error", "detail": "vault_path is required or invalid"},
                status_code=400,
            )

        extracted_text: str | None = body.get("extracted_text")
        if not extracted_text:
            return JSONResponse(
                {"status": "error", "detail": "extracted_text is required"},
                status_code=400,
            )

        content_hash = body.get("content_hash")
        if not content_hash:
            return JSONResponse(
                {"status": "error", "detail": "content_hash is required"},
                status_code=400,
            )

        result = await capture_upload(
            vault_path=vault_path,
            extracted_text=extracted_text,
            content_hash=content_hash,
            original_filename=body.get("original_filename"),
            file_size_bytes=body.get("file_size_bytes"),
            db_path=_db_path,
        )

        match result:
            case Success(document_id):
                # Phase 8 — Live-enqueue: push new doc onto the classify queue
                _push_to_classify_queue(request, document_id)
                return JSONResponse({"status": "ok", "document_id": document_id})
            case Failure(error=err):
                return JSONResponse(
                    {"status": "error", "detail": str(err)},
                    status_code=500,
                )

    # ── Unsupported content type ───────────────────────────────────────
    return JSONResponse(
        {
            "status": "error",
            "detail": "Content-Type must be application/json or multipart/form-data",
        },
        status_code=400,
    )


async def state_handler(request: Request) -> JSONResponse:
    """Return all documents known to the cloud: their vault_path and content_hash.

    GET /api/state

    Requires valid ``Authorization: Bearer <KMS_DAEMON_API_KEY>``.

    Returns::

        {
            "status": "ok",
            "documents": [
                {"vault_path": "...", "content_hash": "..."},
                ...
            ]
        }

    content_hash may be ``null`` for pre-P5 data that was inserted without a
    content fingerprint.
    """
    key = require_key(request)
    if key is None:
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    result = all_paths(db_path=_db_path)

    match result:
        case Success(rows):
            documents = [
                {
                    "vault_path": vp,
                    "content_hash": ch,
                }
                for vp, ch in rows
            ]
            return JSONResponse({"status": "ok", "documents": documents})
        case Failure(error=err):
            return JSONResponse(
                {"status": "error", "detail": str(err)},
                status_code=500,
            )


def _delete_with_blob_cleanup(
    vault_path: str,
    *,
    db_path: Path | None = None,
    blob_store: BlobStore | None = None,
) -> Result[int]:
    """Delete a document row and clean up its blob if this was the last reference.

    Reference-counted blob delete (Phase 5 / P7-CAP-13):

    1. Pre-read the row to capture ``blob_ref``.
    2. Delete the row (search entries cleaned in same transaction).
    3. If ``blob_ref`` was not NULL and ``blob_store`` is not None, run
       ``SELECT COUNT(*) FROM documents WHERE blob_ref = ?`` inside its own
       connection.
    4. If count == 0 → ``blob_store.delete(key)`` best-effort.
       Failed blob delete is logged but does NOT fail the result.

    **TOCTOU race window:** Between the ``delete_by_path`` commit and the
    ref-count query, another request may insert a row with the same
    ``blob_ref``, causing a false-positive on the count (0 even though a
    surviving row references it).  In that scenario ``blob_store.delete``
    removes the blob, but the surviving row still references it — the blob
    is re-created on the next ``put`` (idempotent), so no permanent data
    loss.  This is an accepted trade-off for not holding a DB lock during
    the S3 call.

    Args:
        vault_path: POSIX-relative path for the document row to delete.
        db_path:    Override DB path.
        blob_store: Optional blob store for cleanup.  None → skip.

    Returns:
        ``Success(rowcount)`` or ``Failure(recoverable=False)``.
    """
    # 1. Pre-read the row to capture blob_ref and doc id (for source-prune)
    pre_read = get_by_path(vault_path, db_path=db_path)
    blob_ref: str | None = None
    doc_id: int | None = None
    match pre_read:
        case Success(row) if row is not None:
            blob_ref = row.blob_ref
            doc_id = row.id
        case Failure():
            return pre_read
        case _:
            pass  # row is None → treat as no blob, no prune needed

    # 2. Delete the row (existing behavior)
    del_result = delete_by_path(vault_path=vault_path, db_path=db_path)
    if del_result.is_failure():
        return del_result

    rowcount: int = del_result.value  # type: ignore[union-attr]

    # 2b. Phase 9 — Prune deleted doc id from knowledge_entries sources
    if doc_id is not None and rowcount > 0:
        from storage.knowledge_entries import prune_sources

        prune_result = prune_sources(doc_id, db_path=db_path)
        if prune_result.is_failure():
            _log.warning(
                "prune_sources failed doc_id=%s error=%s",
                doc_id,
                prune_result.error,
            )

    # 3-4. Reference-count check + best-effort blob delete
    if blob_ref is not None and blob_store is not None and rowcount > 0:
        try:
            with get_connection(db_path) as conn:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM documents WHERE blob_ref = ?",
                    (blob_ref,),
                )
                count: int = cur.fetchone()[0]
        except Exception as exc:
            _log.warning(
                "blob ref-count query failed vault_path=%s blob_ref=%s err=%s",
                vault_path,
                blob_ref,
                exc,
            )
            return Success(rowcount)

        if count == 0:
            # Last reference — delete the blob best-effort
            del_blob = blob_store.delete(blob_ref)
            if del_blob.is_failure():
                _log.error(
                    "blob delete failed vault_path=%s blob_ref=%s err=%s",
                    vault_path,
                    blob_ref,
                    del_blob.error,
                )

    return Success(rowcount)


async def event_handler(request: Request) -> JSONResponse:
    """Handle document events (moved / deleted).

    Body (JSON)::

        {
            "type": "moved",
            "old_path": "...",
            "new_path": "..."
        }

    or::

        {
            "type": "deleted",
            "path": "..."
        }

    Requires valid ``Authorization: Bearer <KMS_DAEMON_API_KEY>``.
    """
    key = require_key(request)
    if key is None:
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    try:
        body: Any = await request.json()
    except Exception:
        return JSONResponse(
            {"status": "error", "detail": "invalid JSON body"},
            status_code=400,
        )

    if not isinstance(body, dict):
        return JSONResponse(
            {"status": "error", "detail": "body must be a JSON object"},
            status_code=400,
        )

    event_type: str | None = body.get("type")
    if event_type not in ("moved", "deleted"):
        return JSONResponse(
            {"status": "error", "detail": "type must be 'moved' or 'deleted'"},
            status_code=400,
        )

    if event_type == "moved":
        old_path: str | None = _sanitize_vault_path(body.get("old_path") or "")
        new_path: str | None = _sanitize_vault_path(body.get("new_path") or "")
        if not old_path or not new_path:
            return JSONResponse(
                {
                    "status": "error",
                    "detail": "old_path and new_path are required (and must be valid) for moved",
                },
                status_code=400,
            )
        result = await asyncio.to_thread(
            rename, old=old_path, new=new_path, db_path=_db_path
        )
    else:
        path: str | None = _sanitize_vault_path(body.get("path") or "")
        if not path:
            return JSONResponse(
                {
                    "status": "error",
                    "detail": "path is required (and must be valid) for deleted",
                },
                status_code=400,
            )
        result = await asyncio.to_thread(
            _delete_with_blob_cleanup,
            vault_path=path,
            db_path=_db_path,
            blob_store=_blob_store,
        )

    match result:
        case Success(rowcount):
            if rowcount == 0:
                return JSONResponse({"status": "not_found"})
            return JSONResponse({"status": "ok"})
        case Failure(error=err):
            return JSONResponse(
                {"status": "error", "detail": str(err)},
                status_code=500,
            )


# ============================================================================
# Route definitions
# ============================================================================

api_routes: list[Route] = [
    Route("/api/upload", endpoint=upload_handler, methods=["POST"]),
    Route("/api/state", endpoint=state_handler, methods=["GET"]),
    Route("/api/event", endpoint=event_handler, methods=["POST"]),
]

health_route: list[Route] = [
    Route("/health", endpoint=health_handler, methods=["GET"]),
]
