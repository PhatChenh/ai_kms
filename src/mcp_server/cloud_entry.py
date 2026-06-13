"""
mcp_server/cloud_entry.py — Cloud (container-mode) entry point.

Implements C2-3 (entry point) + C2-4 (startup DB ordering).

Startup order:
  1. Import ``mcp`` from ``mcp_server.server`` — triggers load_dotenv (once),
     setup_logging, CONFIG validation (C-11, C2-3).
  2. ``init_db()`` — creates DB if absent, safe no-op on existing (C2-4).
  3. ``mcp.streamable_http_app()`` — builds the framework's Starlette web app.
  4. Mount Phase 3 REST routes + health route on the app.
  5. ``__main__`` guard runs uvicorn on 0.0.0.0:8080 (C-10).

``build_app()`` is the testable factory; uvicorn only runs inside
``if __name__ == \"__main__\":``.
"""

from __future__ import annotations

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


def build_app(db_path: Path | None = None) -> Starlette:
    """Build and return a fully-wired Starlette ASGI app.

    Steps
    -----
    1. Import ``mcp`` from ``mcp_server.server`` — triggers load_dotenv (once),
       setup_logging, CONFIG validation, and MoveGuard (C-11, C2-3).
    2. Ensure the knowledge-base database exists and is migrated (C2-4).
       *db_path* overrides the CONFIG default for testing.
    3. Obtain the framework's Starlette web app from *mcp*.
    4. Mount Phase 3 REST routes (api_routes + health_route).

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

    # 3. Framework's Starlette app (contains /mcp and /_mcp/* routes)
    app = mcp.streamable_http_app()

    # 4. Mount Phase 3 REST routes + health check
    app.routes.extend(api_routes + health_route)

    return app


if __name__ == "__main__":
    import uvicorn

    app = build_app()
    uvicorn.run(app, host="0.0.0.0", port=8080)
