"""
tests/test_mcp_server/test_server.py

Phase 4 Component 5: MCP Server Shell tests.

RED 1 — bootstrap order
RED 2 — lifespan holds one engine
RED 3 — context isolation (no cross-wipe of correlation ids)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.server.fastmcp import Context, FastMCP
from mcp_server.context import ContextInjectionEngine


# ============================================================================
# In-memory MCP transport helper
# ============================================================================


@asynccontextmanager
async def _in_memory_mcp_client(mcp_app):
    """Create an in-memory MCP client connected to *mcp_app* (a FastMCP).

    Uses anyio memory object streams so the full MCP protocol runs without
    spawning a subprocess or binding to a port.
    """
    # Server side: server reads from srv_read_recv, writes to srv_write_send
    srv_read_send, srv_read_recv = anyio.create_memory_object_stream(10)
    srv_write_send, srv_write_recv = anyio.create_memory_object_stream(10)

    # Client side: flipped — client reads from srv_write_recv, writes to srv_read_send
    init_opts = mcp_app._mcp_server.create_initialization_options()

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            mcp_app._mcp_server.run,
            srv_read_recv,
            srv_write_send,
            init_opts,
        )

        async with ClientSession(
            read_stream=srv_write_recv,
            write_stream=srv_read_send,
        ) as session:
            await session.initialize()
            yield session

        tg.cancel_scope.cancel()


# ============================================================================
# RED 1 — Bootstrap order
# ============================================================================


class TestBootstrapOrder:
    """Bootstrap mirrors cli/main.py: load_dotenv → setup_logging → CONFIG → set_active."""

    def test_bootstrap_calls_in_order_exactly_once(self):
        """_bootstrap() calls load_dotenv then setup_logging then set_active, once each."""
        order: list[str] = []

        def track_ld(*args: object, **kwargs: object) -> None:
            order.append("load_dotenv")

        def track_sl(*args: object, **kwargs: object) -> None:
            order.append("setup_logging")

        def track_sa(*args: object, **kwargs: object) -> None:
            order.append("set_active")

        # ------------------------------------------------------------------
        # Patch BEFORE importing the server module so the module-level
        # bootstrap code also goes through mocks (idempotent anyway).
        # ------------------------------------------------------------------
        with (
            patch("mcp_server.server.load_dotenv", side_effect=track_ld) as mock_ld,
            patch("mcp_server.server.setup_logging", side_effect=track_sl) as mock_sl,
            patch("mcp_server.server.set_active", side_effect=track_sa) as mock_sa,
            patch("mcp_server.server.MoveGuard", MagicMock()),
            patch("mcp_server.server.CONFIG", MagicMock()),
        ):
            from mcp_server.server import _bootstrap  # noqa: E402

            # The module-level bootstrap already ran once during import.
            # Reset tracking so we assert on a clean second call.
            order.clear()
            mock_ld.reset_mock()
            mock_sl.reset_mock()
            mock_sa.reset_mock()

            # ---- call _bootstrap a second time -------------------------------
            _bootstrap()

            # Assert order
            assert order == [
                "load_dotenv",
                "setup_logging",
                "set_active",
            ], f"Expected [load_dotenv, setup_logging, set_active], got {order}"

            # Assert exactly once each in the second call
            assert mock_ld.call_count == 1, (
                f"load_dotenv called {mock_ld.call_count} times"
            )
            assert mock_sl.call_count == 1, (
                f"setup_logging called {mock_sl.call_count} times"
            )
            assert mock_sa.call_count == 1, (
                f"set_active called {mock_sa.call_count} times"
            )


# ============================================================================
# RED 2 — Lifespan holds one engine
# ============================================================================


class TestLifespanEngine:
    """The FastMCP lifespan yields one engine, same object across two tool calls."""

    @pytest.mark.asyncio
    async def test_lifespan_exposes_engine(self):
        """Connect via in-memory transport and assert lifespan has one engine."""
        # Build a one-off FastMCP app with its own engine
        engine_instance = ContextInjectionEngine()

        @asynccontextmanager
        async def _lifespan(app):
            yield {"engine": engine_instance}

        app = FastMCP("test_kms_lifespan", lifespan=_lifespan)

        # Register a dummy tool that reads the engine from lifespan context
        engine_ids: list[int] = []

        @app.tool()
        def get_engine_id(ctx: Context) -> int:
            engine = ctx.request_context.lifespan_context["engine"]
            engine_ids.append(id(engine))
            return id(engine)

        async with _in_memory_mcp_client(app) as session:
            result1 = await session.call_tool("get_engine_id", {})
            result2 = await session.call_tool("get_engine_id", {})

        assert result1 is not None
        assert result2 is not None

        # Both calls should see the SAME engine instance
        assert len(engine_ids) == 2
        assert engine_ids[0] == engine_ids[1], (
            f"Engine instance differs across calls: {engine_ids[0]} vs {engine_ids[1]}"
        )

    @pytest.mark.asyncio
    async def test_lifespan_context_has_engine_key(self):
        """The lifespan context dict has exactly the 'engine' key."""
        engine_instance = ContextInjectionEngine()

        @asynccontextmanager
        async def _lifespan(app):
            yield {"engine": engine_instance}

        app = FastMCP("test_kms_keys", lifespan=_lifespan)

        keys_seen: list[list[str]] = []

        @app.tool()
        def list_keys(ctx: Context) -> list[str]:
            keys = sorted(ctx.request_context.lifespan_context.keys())
            keys_seen.append(keys)
            return keys

        async with _in_memory_mcp_client(app) as session:
            await session.call_tool("list_keys", {})

        assert len(keys_seen) == 1
        assert keys_seen[0] == ["engine"], f"Expected ['engine'], got {keys_seen[0]}"


# ============================================================================
# RED 3 — Context isolation
# ============================================================================


class TestContextIsolation:
    """Two tool calls must not wipe each other's contextvars."""

    @pytest.mark.asyncio
    async def test_run_in_isolated_context_preserves_caller_state(self):
        """When a tool calls new_correlation_id() (which calls clear_contextvars()),
        the caller's structlog-bound contextvars must survive.

        Without copy_context().run(...), clear_contextvars() inside the tool
        would wipe the parent context's bound vars.
        """
        from structlog.contextvars import (
            bind_contextvars,
            clear_contextvars,
            get_contextvars,
        )

        from core.logging_setup import new_correlation_id
        from mcp_server.server import run_in_isolated_context

        # Clear any pre-existing contextvars from other tests so we start
        # from a known-empty state (contextvars are thread-local and can
        # bleed between tests in the same thread).
        clear_contextvars()

        # The function whose context we want to protect:
        # new_correlation_id calls clear_contextvars() internally, which
        # wipes structlog-bound contextvars in the current context.
        # Wrapping this in run_in_isolated_context should confine the
        # clear to a copy, leaving the caller's state untouched.

        @run_in_isolated_context
        def isolated_tool():
            """Simulate a tool that generates a fresh correlation id."""
            return new_correlation_id()

        def non_isolated_tool():
            """Same as above but WITHOUT isolation."""
            return new_correlation_id()

        # ---- Isolated path: caller state must survive -----------------------
        bind_contextvars(caller_state="survive-me-isolated")
        cid_iso = isolated_tool()
        ctx_after_iso = get_contextvars()

        assert ctx_after_iso.get("caller_state") == "survive-me-isolated", (
            f"ISOLATED: caller_state was wiped by clear_contextvars()! "
            f"Context after isolated call: {ctx_after_iso}"
        )
        assert cid_iso is not None
        assert "correlation_id" not in ctx_after_iso, (
            f"ISOLATED: callee's correlation_id leaked back — got {ctx_after_iso}"
        )

        # ---- Non-isolated path: caller state SHOULD be wiped (proves the
        #      test is actually testing the right thing) --------------------
        clear_contextvars()
        bind_contextvars(caller_state="survive-me-nonisolated")
        cid_non = non_isolated_tool()
        ctx_after_non = get_contextvars()

        assert ctx_after_non.get("caller_state") is None, (
            f"NON-ISOLATED: caller_state survived but should have been wiped. "
            f"Context after non-isolated call: {ctx_after_non}"
        )
        assert cid_non is not None

    @pytest.mark.asyncio
    async def test_two_isolated_tool_calls_in_same_context(self):
        """Two back-to-back isolated tool calls each keep their own correlation id
        and neither leaks contextvars into the caller."""
        from structlog.contextvars import (
            clear_contextvars,
            get_contextvars,
        )

        from core.logging_setup import new_correlation_id
        from mcp_server.server import run_in_isolated_context

        clear_contextvars()

        @run_in_isolated_context
        def isolated_tool():
            return new_correlation_id()

        # Call twice sequentially in the same context
        cid1 = isolated_tool()
        cid2 = isolated_tool()
        ctx = get_contextvars()

        # Each call gets a unique correlation id
        assert cid1 != cid2, f"Expected distinct IDs, got {cid1} twice"

        # No correlation_id from either call leaks back to the caller
        assert "correlation_id" not in ctx, f"correlation_id leaked back: {ctx}"
