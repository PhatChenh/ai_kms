"""mcp_server/cloud_entry.py — Cloud (container-mode) entry point.

Implements C2-3 (entry point) + C2-4 (startup DB ordering).

Startup order:
  1. Import ``mcp`` from ``mcp_server.server`` — triggers load_dotenv (once),
     setup_logging, CONFIG validation (C-11, C2-3).
  2. ``init_db()`` — creates DB if absent, safe no-op on existing (C2-4).
  3. Wire blob store from ``KMS_BLOB_*`` env vars if available.
  4. ``mcp.streamable_http_app()`` — builds the framework's Starlette web app.
  5. Mount Phase 3 REST routes + health route on the app.
  6. ``__main__`` guard runs uvicorn on 0.0.0.0:8080 (C-10).

``build_app()`` is the testable factory; uvicorn only runs inside
``if __name__ == \"__main__\":``.
"""

from __future__ import annotations

import os
import logging

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.applications import Starlette

# ---------------------------------------------------------------------------
# 1. DB init (C2-4)
# ---------------------------------------------------------------------------
from core.result import Failure
from storage.db import init_db

# ---------------------------------------------------------------------------
# 2. REST routes from Phase 3 (api.py)
# ---------------------------------------------------------------------------
from mcp_server.api import api_routes, health_route

_log = logging.getLogger(__name__)


def build_app(db_path: Path | None = None) -> Starlette:
    """Build and return a fully-wired Starlette ASGI app.

    Steps
    -----
    1. Import ``mcp`` from ``mcp_server.server`` — triggers load_dotenv (once),
       setup_logging, CONFIG validation, and MoveGuard (C-11, C2-3).
    2. Ensure the knowledge-base database exists and is migrated (C2-4).
       *db_path* overrides the CONFIG default for testing.
    3. Wire blob store from ``KMS_BLOB_*`` environment variables.
    4. Obtain the framework's Starlette web app from *mcp*.
    5. Mount Phase 3 REST routes (api_routes + health_route).
    6. Wrap the app's lifespan with a composed outer lifespan that runs a
       background classify worker + catch-up scan (Phase 7).

    Returns
    -------
    Starlette
        The ready-to-serve ASGI app.  Does NOT start uvicorn.
    """
    # 1. Import mcp — triggers load_dotenv, setup_logging, CONFIG validation,
    #    and MoveGuard.  No second load_dotenv call (C-11).
    #    Import is inside the function (not module-level) so that tests that call
    #    build_app() with patched CONFIG_DIR get correct isolation — server.py's
    #    module-level bootstrap runs once, at first call, using whatever CONFIG_DIR
    #    is active at that point.
    from mcp_server.server import mcp  # noqa: E402

    # 2. DB init — idempotent (C2-4)
    match init_db(db_path):
        case Failure() as f:
            raise RuntimeError(f"DB init failed: {f.error}") from None

    # 3. Wire blob store from env vars
    _wire_blob_store()

    # 4. Framework's Starlette app (contains /mcp and /_mcp/* routes)
    app = mcp.streamable_http_app()

    # 5. Mount Phase 3 REST routes + health check
    app.routes.extend(api_routes + health_route)

    # 6. Phase 7 — Composed outer lifespan: classify worker + catch-up scan
    _wrap_lifespan(app, db_path)

    return app


# ---------------------------------------------------------------------------
# Phase 7 — Composed outer lifespan
# ---------------------------------------------------------------------------


def _wrap_lifespan(app: Starlette, db_path: Path | None) -> None:
    """Wrap *app*'s existing lifespan with a composed outer lifespan that
    starts a background classify worker and runs a catch-up scan before
    entering the inner FastMCP session-manager lifespan.

    The wrapping happens **in place** inside ``build_app()`` — Starlette
    reads ``app.router.lifespan_context`` at ASGI startup (after
    ``build_app()`` returns), so reassigning it here takes effect.

    ``on_startup`` is deliberately NOT used — it is a silent no-op when a
    lifespan is set (Starlette ignores it).
    """
    import asyncio
    from contextlib import asynccontextmanager

    from core.config import CONFIG
    from pipelines.classify import catch_up_scan, consumer

    inner = app.router.lifespan_context  # session_manager.run()

    @asynccontextmanager
    async def _composed(app_ref):
        queue: asyncio.Queue[int] = asyncio.Queue()
        worker = asyncio.create_task(
            consumer(queue, db_path, CONFIG.main)
        )
        await catch_up_scan(queue, db_path)
        try:
            async with inner(app_ref):
                yield
        finally:
            worker.cancel()
            _log.debug("classify_worker_task_cancelled")

    app.router.lifespan_context = _composed  # reassign IN PLACE


# ---------------------------------------------------------------------------
# Blob store wiring
# ---------------------------------------------------------------------------


def _wire_blob_store() -> None:
    """Read KMS_BLOB_* env vars and wire S3BlobStore into api._blob_store.

    If all four env vars are non-empty, construct an S3BlobStore and assign
    it to ``api._blob_store``.  Otherwise leave ``_blob_store = None``
    (text-only deployment, still valid).  Logs which mode is active.
    """
    endpoint = os.environ.get("KMS_BLOB_ENDPOINT", "").strip()
    bucket = os.environ.get("KMS_BLOB_BUCKET", "").strip()
    access_key = os.environ.get("KMS_BLOB_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("KMS_BLOB_SECRET_ACCESS_KEY", "").strip()

    if endpoint and bucket and access_key and secret_key:
        from storage.blobs import S3BlobStore

        blob = S3BlobStore(
            endpoint=endpoint,
            bucket=bucket,
            access_key_id=access_key,
            secret_access_key=secret_key,
        )
        import mcp_server.api as api_mod

        api_mod._blob_store = blob
        _log.info(
            "blob_store=production endpoint=%s bucket=%s",
            _obscure(endpoint),
            bucket,
        )
    else:
        _log.info("blob_store=disabled (text-only deployment)")


def _obscure(text: str) -> str:
    """Return a shortened, safe-for-log version of *text*."""
    if len(text) <= 8:
        return text[:2] + "***"
    return text[:4] + "***" + text[-4:]
