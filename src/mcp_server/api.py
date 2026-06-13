"""
mcp_server/api.py — REST handlers + secret-key gate + health route (Phase 3)

Implements C2-2: upload, state, event, and health endpoints with a bearer-token gate
scoped to /api/* only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core.result import Failure, Success
from storage.documents import (
    all_paths,
    delete_by_path,
    rename,
    upsert_from_upload,
)

# Testability injection point — set this to an explicit Path in tests to
# override the default CONFIG-derived database path.  None = use CONFIG.
_db_path: Path | None = None


# ============================================================================
# Secret-key gate
# ============================================================================


def require_key(request: Request) -> str | None:
    """Extract and validate the bearer token from *request*.

    Reads ``KMS_DAEMON_API_KEY`` from the environment.  Returns the key on
    match, ``None`` on mismatch or missing header.

    Short-circuits on missing header or non-Bearer prefix.
    """
    auth = request.headers.get("Authorization")
    if auth is None:
        return None
    if not auth.startswith("Bearer "):
        return None

    token = auth[len("Bearer "):]
    expected = os.environ.get("KMS_DAEMON_API_KEY")
    if expected is None:
        return None
    if token != expected:
        return None
    return token


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
            file_size_bytes   (optional)
            mime_type         (optional, accepted and discarded)
            file              (optional, accepted and discarded in A1)

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

        vault_path: str | None = form.get("vault_path")
        if not vault_path:
            return JSONResponse(
                {"status": "error", "detail": "vault_path is required"},
                status_code=400,
            )

        content_hash: str | None = form.get("content_hash")
        if not content_hash:
            return JSONResponse(
                {"status": "error", "detail": "content_hash is required"},
                status_code=400,
            )

        original_filename: str | None = form.get("original_filename") or None
        file_size_bytes_raw: str | None = form.get("file_size_bytes")
        file_size_bytes: int | None = None
        if file_size_bytes_raw:
            try:
                file_size_bytes = int(file_size_bytes_raw)
            except (ValueError, TypeError):
                return JSONResponse(
                    {"status": "error", "detail": "file_size_bytes must be an integer"},
                    status_code=400,
                )

        # file bytes and mime_type are accepted but discarded in A1
        # (blob storage arrives in Phase 7)

        result = upsert_from_upload(
            vault_path=vault_path,
            extracted_text=None,
            content_hash=content_hash,
            original_filename=original_filename,
            file_size_bytes=file_size_bytes,
            db_path=_db_path,
        )

        match result:
            case Success(document_id):
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

        vault_path = body.get("vault_path")
        if not vault_path:
            return JSONResponse(
                {"status": "error", "detail": "vault_path is required"},
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

        result = upsert_from_upload(
            vault_path=vault_path,
            extracted_text=extracted_text,
            content_hash=content_hash,
            original_filename=body.get("original_filename"),
            file_size_bytes=body.get("file_size_bytes"),
            title=body.get("title"),
            db_path=_db_path,
        )

        match result:
            case Success(document_id):
                return JSONResponse({"status": "ok", "document_id": document_id})
            case Failure(error=err):
                return JSONResponse(
                    {"status": "error", "detail": str(err)},
                    status_code=500,
                )

    # ── Unsupported content type ───────────────────────────────────────
    return JSONResponse(
        {"status": "error", "detail": "Content-Type must be application/json or multipart/form-data"},
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
        old_path: str | None = body.get("old_path")
        new_path: str | None = body.get("new_path")
        if not old_path or not new_path:
            return JSONResponse(
                {"status": "error", "detail": "old_path and new_path are required for moved"},
                status_code=400,
            )
        result = rename(old=old_path, new=new_path, db_path=_db_path)
    else:
        path: str | None = body.get("path")
        if not path:
            return JSONResponse(
                {"status": "error", "detail": "path is required for deleted"},
                status_code=400,
            )
        result = delete_by_path(vault_path=path, db_path=_db_path)

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
