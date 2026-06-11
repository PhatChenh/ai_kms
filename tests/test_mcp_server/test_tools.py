"""
tests/test_mcp_server/test_tools.py

Phase 4 Component 7: Tool Shim Layer tests.

RED 1 — C-14 cleanliness (no statement-level branches in tools.py)
RED 2 — lists five tools (P4-MCP-01)
RED 3 — pass-through (shim returns exactly what engine/helper produces)
RED 4 — ctx excluded from public tool schema
"""

from __future__ import annotations

import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.server.fastmcp import Context, FastMCP

from core.result import Failure, Success


# ============================================================================
# In-memory MCP transport helper (same pattern as test_server.py)
# ============================================================================


@asynccontextmanager
async def _in_memory_mcp_client(mcp_app):
    """Create an in-memory MCP client connected to *mcp_app* (a FastMCP)."""
    srv_read_send, srv_read_recv = anyio.create_memory_object_stream(10)
    srv_write_send, srv_write_recv = anyio.create_memory_object_stream(10)

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
# Path to tools.py for C-14 grep test
# ============================================================================

TOOLS_PY = Path(__file__).parent.parent.parent / "src" / "mcp_server" / "tools.py"


# ============================================================================
# RED 1 — C-14 Cleanliness (no statement-level branches)
# ============================================================================


class TestC14Cleanliness:
    """tools.py must contain no if/elif/for/while at statement level."""

    def test_tools_py_has_no_statement_level_branches(self):
        """C-14: grep for statement-level branches in tools.py finds nothing.

        The file must exist and contain zero statement-level if/elif/for/while.
        """
        if not TOOLS_PY.exists():
            pytest.fail(
                f"{TOOLS_PY} does not exist — create it with five one-line tool shims"
            )

        result = subprocess.run(
            ["grep", "-nE", r"^\s+(if|elif|for|while)\s", str(TOOLS_PY)],
            capture_output=True,
            text=True,
        )
        # grep returns 0 if matches found, 1 if no matches, 2 if error
        if result.returncode == 0:
            lines = result.stdout.strip()
            pytest.fail(f"tools.py has statement-level branches:\n{lines}")
        # returncode == 1 means no matches — that's what we want
        # returncode > 1 means an error (file not readable, etc.)
        if result.returncode > 1:
            pytest.fail(f"grep error (code {result.returncode}): {result.stderr}")


# ============================================================================
# RED 2 — Lists Five Tools (P4-MCP-01)
# ============================================================================


class TestListsFiveTools:
    """Via in-memory transport, exactly five tools are listed."""

    @pytest.mark.asyncio
    async def test_lists_exactly_five_tools(self):
        """Connect and list_tools() returns exactly the five expected tool names."""
        from mcp_server.context import ContextInjectionEngine

        engine_instance = ContextInjectionEngine()

        @asynccontextmanager
        async def _lifespan(app):
            yield {"engine": engine_instance}

        app = FastMCP("test_kms_tools_list", lifespan=_lifespan)

        # Register tools on this test app
        from mcp_server.tools import register_tools

        register_tools(app)

        expected_tools = {
            "kms_vault_info",
            "kms_search",
            "kms_read",
            "kms_inspect",
            "kms_move",
        }

        async with _in_memory_mcp_client(app) as session:
            result = await session.list_tools()
            tool_names = {t.name for t in result.tools}

        missing = expected_tools - tool_names
        extra = tool_names - expected_tools

        assert not missing, f"Missing tools: {missing}"
        assert not extra, f"Unexpected tools: {extra}"
        assert tool_names == expected_tools, (
            f"Expected exactly 5 tools {expected_tools}, got {tool_names}"
        )

    @pytest.mark.asyncio
    async def test_list_tools_returns_no_connection_error(self):
        """A no-op call after list_tools() succeeds without errors (connection alive)."""
        from mcp_server.context import ContextInjectionEngine

        engine_instance = ContextInjectionEngine()

        @asynccontextmanager
        async def _lifespan(app):
            yield {"engine": engine_instance}

        app = FastMCP("test_kms_tools_noop", lifespan=_lifespan)

        from mcp_server.tools import register_tools

        register_tools(app)

        async with _in_memory_mcp_client(app) as session:
            # list_tools should succeed
            result = await session.list_tools()
            assert result.tools, "Expected at least one tool"
            # Connection is still alive — no exception raised
            assert len(result.tools) == 5, f"Expected 5 tools, got {len(result.tools)}"


# ============================================================================
# RED 3 — Pass-Through (shim returns exactly what engine/helper produces)
# ============================================================================


class TestPassThrough:
    """Each tool shim returns the .value of what its engine/helper produces."""

    # ------------------------------------------------------------------
    # kms_vault_info
    # ------------------------------------------------------------------

    def test_kms_vault_info_returns_engine_build_vault_info_response_value(self):
        """kms_vault_info calls engine.build_vault_info_response() and returns .unwrap()."""
        from mcp_server.tools import kms_vault_info

        fake_blocks = [
            {"type": "context", "source": "vault_overview", "content": "# Vault"}
        ]
        engine = MagicMock()
        engine.build_vault_info_response.return_value = Success(fake_blocks)

        ctx = MagicMock()
        ctx.request_context.lifespan_context = {"engine": engine}

        result = kms_vault_info(ctx)
        assert result == fake_blocks, f"Expected {fake_blocks}, got {result}"
        engine.build_vault_info_response.assert_called_once()

    # ------------------------------------------------------------------
    # kms_search
    # ------------------------------------------------------------------

    def test_kms_search_returns_engine_build_search_response_value(self):
        """kms_search passes user-facing params to engine.build_search_response()
        and returns .unwrap()."""
        from mcp_server.tools import kms_search

        fake_blocks = [
            {"type": "context", "source": "project:Alpha", "content": "# Alpha"},
            {"type": "result_card", "data": {}},
        ]
        engine = MagicMock()
        engine.build_search_response.return_value = Success(fake_blocks)

        ctx = MagicMock()
        ctx.request_context.lifespan_context = {"engine": engine}

        result = kms_search(
            query="test query",
            project="Alpha",
            since="2026-01-01",
            until="2026-06-01",
            location="Projects",
            include_context=True,
            ctx=ctx,
        )
        assert result == fake_blocks, f"Expected {fake_blocks}, got {result}"
        engine.build_search_response.assert_called_once_with(
            query="test query",
            project="Alpha",
            since="2026-01-01",
            until="2026-06-01",
            location="Projects",
            include_context=True,
        )

    def test_kms_search_defaults_include_context_to_false(self):
        """When include_context is not passed, it defaults to False."""
        from mcp_server.tools import kms_search

        engine = MagicMock()
        engine.build_search_response.return_value = Success([])

        ctx = MagicMock()
        ctx.request_context.lifespan_context = {"engine": engine}

        kms_search(query="q", ctx=ctx)
        engine.build_search_response.assert_called_once_with(
            query="q",
            project=None,
            since=None,
            until=None,
            location=None,
            include_context=False,
        )

    # ------------------------------------------------------------------
    # kms_read
    # ------------------------------------------------------------------

    def test_kms_read_returns_engine_build_read_response_value(self):
        """kms_read passes paths + include_context to engine.build_read_response()
        and returns .unwrap()."""
        from mcp_server.tools import kms_read

        fake_blocks = [
            {"type": "context", "source": "domain:Ops", "content": "# Ops"},
            {"type": "read_note", "path": "/v/Projects/A/note.md", "content": "body"},
        ]
        engine = MagicMock()
        engine.build_read_response.return_value = Success(fake_blocks)

        ctx = MagicMock()
        ctx.request_context.lifespan_context = {"engine": engine}

        result = kms_read(
            paths=["/v/Projects/A/note.md", "/v/inbox/idea.md"],
            include_context=True,
            ctx=ctx,
        )
        assert result == fake_blocks, f"Expected {fake_blocks}, got {result}"
        engine.build_read_response.assert_called_once_with(
            paths=[Path("/v/Projects/A/note.md"), Path("/v/inbox/idea.md")],
            include_context=True,
        )

    def test_kms_read_defaults_include_context_to_false(self):
        """When include_context is not passed, it defaults to False."""
        from mcp_server.tools import kms_read

        engine = MagicMock()
        engine.build_read_response.return_value = Success([])

        ctx = MagicMock()
        ctx.request_context.lifespan_context = {"engine": engine}

        kms_read(paths=["/v/note.md"], ctx=ctx)
        engine.build_read_response.assert_called_once_with(
            paths=[Path("/v/note.md")],
            include_context=False,
        )

    # ------------------------------------------------------------------
    # kms_inspect
    # ------------------------------------------------------------------

    def test_kms_inspect_returns_resolver_inspect_value(self):
        """kms_inspect calls resolver.inspect(path) and returns .unwrap()."""
        from mcp_server.tools import kms_inspect

        fake_text = "Extracted binary text content."

        with patch("mcp_server.tools.inspect") as mock_inspect:
            mock_inspect.return_value = Success(fake_text)

            result = kms_inspect(
                path="/v/Projects/A/attachment/report.pdf", ctx=MagicMock()
            )
            assert result == fake_text, f"Expected {fake_text!r}, got {result!r}"
            mock_inspect.assert_called_once_with(
                Path("/v/Projects/A/attachment/report.pdf")
            )

    def test_kms_inspect_ctx_is_optional(self):
        """kms_inspect works when ctx is not passed (defaults to None)."""
        from mcp_server.tools import kms_inspect

        with patch("mcp_server.tools.inspect") as mock_inspect:
            mock_inspect.return_value = Success("text")

            result = kms_inspect(path="/v/file.pdf")
            assert result == "text"

    # ------------------------------------------------------------------
    # kms_move
    # ------------------------------------------------------------------

    def test_kms_move_returns_mover_move_value(self):
        """kms_move calls mover.move(src, dest_name, dest_kind) and returns .unwrap()."""
        from mcp_server.tools import kms_move

        success_msg = "Moved report.pdf to project/Alpha"

        with patch("mcp_server.tools.move") as mock_move:
            mock_move.return_value = Success(success_msg)

            result = kms_move(
                src="/v/inbox/report.pdf",
                dest_name="Alpha",
                dest_kind="project",
                ctx=MagicMock(),
            )
            assert result == success_msg, f"Expected {success_msg!r}, got {result!r}"
            mock_move.assert_called_once_with(
                Path("/v/inbox/report.pdf"),
                "Alpha",
                "project",
            )

    def test_kms_move_ctx_is_optional(self):
        """kms_move works when ctx is not passed (defaults to None)."""
        from mcp_server.tools import kms_move

        with patch("mcp_server.tools.move") as mock_move:
            mock_move.return_value = Success("ok")

            result = kms_move(src="/v/x.md", dest_name="Beta", dest_kind="domain")
            assert result == "ok"


# ============================================================================
# RED 4 — ctx Excluded from Public Schema
# ============================================================================


class TestCtxExcludedFromSchema:
    """The `ctx` parameter is auto-excluded from the public tool schema by FastMCP."""

    @pytest.mark.asyncio
    async def test_kms_search_schema_has_no_ctx_parameter(self):
        """The list_tools() schema for kms_search must not include `ctx`."""
        from mcp_server.context import ContextInjectionEngine

        engine_instance = ContextInjectionEngine()

        @asynccontextmanager
        async def _lifespan(app):
            yield {"engine": engine_instance}

        app = FastMCP("test_kms_schema", lifespan=_lifespan)

        from mcp_server.tools import register_tools

        register_tools(app)

        async with _in_memory_mcp_client(app) as session:
            tools_result = await session.list_tools()

        # Find kms_search tool
        kms_search_tool = None
        for tool in tools_result.tools:
            if tool.name == "kms_search":
                kms_search_tool = tool
                break

        assert kms_search_tool is not None, "kms_search tool not found in list_tools()"

        # inputSchema.properties must NOT contain "ctx"
        schema_props = kms_search_tool.inputSchema.get("properties", {})
        assert "ctx" not in schema_props, (
            f"ctx should be excluded from schema, but found in: {list(schema_props.keys())}"
        )

        # It SHOULD contain the user-facing params
        for param in (
            "query",
            "project",
            "since",
            "until",
            "location",
            "include_context",
        ):
            assert param in schema_props, (
                f"Expected {param!r} in kms_search schema, got: {list(schema_props.keys())}"
            )

    @pytest.mark.asyncio
    async def test_kms_vault_info_has_no_ctx_in_schema(self):
        """The list_tools() schema for kms_vault_info must not include `ctx`."""
        from mcp_server.context import ContextInjectionEngine

        engine_instance = ContextInjectionEngine()

        @asynccontextmanager
        async def _lifespan(app):
            yield {"engine": engine_instance}

        app = FastMCP("test_kms_schema_vi", lifespan=_lifespan)

        from mcp_server.tools import register_tools

        register_tools(app)

        async with _in_memory_mcp_client(app) as session:
            tools_result = await session.list_tools()

        vault_info_tool = None
        for tool in tools_result.tools:
            if tool.name == "kms_vault_info":
                vault_info_tool = tool
                break

        assert vault_info_tool is not None, "kms_vault_info tool not found"

        schema_props = vault_info_tool.inputSchema.get("properties", {})
        assert "ctx" not in schema_props, (
            f"ctx should be excluded from kms_vault_info schema, "
            f"got: {list(schema_props.keys())}"
        )
