"""
MCP Server Shell — the long-running front door Claude Desktop connects to.

Startup mirrors cli/main.py exactly:
  1. load_dotenv once at the top (C-11)
  2. setup_logging once (C-10 / C-11)
  3. import CONFIG → validates vault root
  4. set_active(MoveGuard()) so kms_move suppresses watcher re-home
"""

from __future__ import annotations

import contextvars
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap — runs once at module load, exactly like cli/main.py
# ---------------------------------------------------------------------------

# 1. Load .env before anything that reads environment variables (C-11).
#    override=False means shell-exported vars take precedence over the file.
load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)

from core.logging_setup import setup_logging  # noqa: E402 — must be after dotenv load

# Read logging settings from config.yaml — avoids full CONFIG load
# (which validates vault root) before logging is ready.
_log_cfg: dict = {}
try:
    with open(
        Path(__file__).parent.parent / "config" / "config.yaml", encoding="utf-8"
    ) as _f:
        _log_cfg = (yaml.safe_load(_f) or {}).get("logging", {})
except Exception:
    pass

# 2. setup_logging once (C-10 / C-11).
setup_logging(
    log_level=str(_log_cfg.get("level", "INFO")),
    dev_mode=bool(_log_cfg.get("console", True)),
)

# 3. Import CONFIG — triggers vault-root validation at first access.
from core.config import CONFIG  # noqa: E402

# 4. Publish a move guard so kms_move can suppress watcher re-home.
from vault.move_guard import MoveGuard, set_active  # noqa: E402

set_active(MoveGuard())

# ---------------------------------------------------------------------------
# Re-exportable bootstrap function — the plan requires a callable for testing.
# The module-level code above already ran; calling this directly repeats the
# sequence (all steps are idempotent) so tests can verify order with mocks.
# ---------------------------------------------------------------------------


def _bootstrap() -> None:
    """Run the bootstrap sequence (idempotent — safe to call again).

    Call order: load_dotenv → setup_logging → CONFIG → set_active(MoveGuard()).
    """
    load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)

    _log_cfg_fn: dict = {}
    try:
        with open(
            Path(__file__).parent.parent / "config" / "config.yaml", encoding="utf-8"
        ) as _f:
            _log_cfg_fn = (yaml.safe_load(_f) or {}).get("logging", {})
    except Exception:
        pass

    setup_logging(
        log_level=str(_log_cfg_fn.get("level", "INFO")),
        dev_mode=bool(_log_cfg_fn.get("console", True)),
    )

    # Trigger CONFIG validation by accessing a field on it.
    _ = CONFIG.main.vault.root

    set_active(MoveGuard())


# ---------------------------------------------------------------------------
# Logger (after bootstrap — setup_logging is already done)
# ---------------------------------------------------------------------------

_log = structlog.get_logger("mcp_server")

# ---------------------------------------------------------------------------
# Lifespan — creates one engine per conversation (one process under stdio)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastMCP):
    """Create ONE Context Injection Engine per conversation.

    Under stdio transport, one process = one conversation (research A1).
    The lifespan is entered exactly once per ``Server.run()`` call.
    """
    from mcp_server.context import ContextInjectionEngine

    _log.info("mcp_server.lifespan_start")
    try:
        yield {"engine": ContextInjectionEngine()}
    finally:
        _log.info("mcp_server.lifespan_end")


# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("kms", lifespan=_lifespan)

# No tools registered in this phase (C-15).
# Tools are added in Phase 6 after the engine and helpers are built and tested.

# ---------------------------------------------------------------------------
# Per-tool-call context isolation wrapper (A11 / OQ-004)
# ---------------------------------------------------------------------------
# FastMCP dispatch runs each tool in its own asyncio task but does NOT
# isolate Python contextvars (``contextvars.copy_context().run()``).
# Wrapping each dispatched tool call prevents ``new_correlation_id()``'s
# ``clear_contextvars()`` from wiping a sibling call's trace id.
#
# Available for Phase 6 when registering tools.  Phase 2 doesn't register
# any tools yet, but the wrapper lands here so Phase 2's isolation test
# can pass.


def run_in_isolated_context(fn):
    """Decorator: run *fn* in its own ``contextvars`` copy.

    Usage (Phase 6)::

        @mcp.tool()
        @run_in_isolated_context
        def kms_search(query: str, ctx: Context) -> list: ...
    """
    import functools

    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        ctx = contextvars.copy_context()
        return ctx.run(fn, *args, **kwargs)

    return _wrapper


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server (stdio transport).  The FastMCP run owns the event loop."""
    mcp.run()


if __name__ == "__main__":
    main()
