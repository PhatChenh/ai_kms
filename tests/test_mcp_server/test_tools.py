"""
tests/test_mcp_server/test_tools.py

Phase 9: Tool Shim Layer tests — 7 tools, C-14 clean.
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

from core.result import Success

TOOLS_PY = Path(__file__).parent.parent.parent / "src" / "mcp_server" / "tools.py"


# ============================================================================
# In-memory MCP transport
# ============================================================================


@asynccontextmanager
async def _in_memory_mcp_client(mcp_app):
    srv_read_send, srv_read_recv = anyio.create_memory_object_stream(10)
    srv_write_send, srv_write_recv = anyio.create_memory_object_stream(10)
    init_opts = mcp_app._mcp_server.create_initialization_options()
    async with anyio.create_task_group() as tg:
        tg.start_soon(mcp_app._mcp_server.run, srv_read_recv, srv_write_send, init_opts)
        async with ClientSession(read_stream=srv_write_recv, write_stream=srv_read_send) as session:
            await session.initialize()
            yield session
        tg.cancel_scope.cancel()


# ============================================================================
# C-14 Cleanliness
# ============================================================================


class TestC14Cleanliness:
    def test_tools_py_has_no_statement_level_branches(self):
        if not TOOLS_PY.exists():
            pytest.fail(f"{TOOLS_PY} does not exist")
        result = subprocess.run(
            ["grep", "-nE", r"^\s+(if|elif|for|while)\s", str(TOOLS_PY)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            pytest.fail(f"tools.py has statement-level branches:\n{result.stdout.strip()}")
        if result.returncode > 1:
            pytest.fail(f"grep error (code {result.returncode}): {result.stderr}")


# ============================================================================
# Lists Five Tools
# ============================================================================


class TestListsSevenTools:
    @pytest.mark.asyncio
    async def test_lists_exactly_five_tools(self):
        from mcp_server.context import ContextInjectionEngine

        @asynccontextmanager
        async def _lifespan(app):
            yield {"engine": ContextInjectionEngine()}

        app = FastMCP("test_kms_tools_list", lifespan=_lifespan)
        from mcp_server.tools import register_tools
        register_tools(app)

        expected_tools = {
            "kms_vault_info", "kms_search", "kms_inspect",
            "kms_write", "kms_correct", "kms_comment", "kms_reports",
        }

        async with _in_memory_mcp_client(app) as session:
            result = await session.list_tools()
            tool_names = {t.name for t in result.tools}

        missing = expected_tools - tool_names
        extra = tool_names - expected_tools
        assert not missing, f"Missing: {missing}"
        assert not extra, f"Unexpected: {extra}"
        assert tool_names == expected_tools

    @pytest.mark.asyncio
    async def test_list_tools_returns_no_connection_error(self):
        from mcp_server.context import ContextInjectionEngine

        @asynccontextmanager
        async def _lifespan(app):
            yield {"engine": ContextInjectionEngine()}

        app = FastMCP("test_kms_tools_noop", lifespan=_lifespan)
        from mcp_server.tools import register_tools
        register_tools(app)

        async with _in_memory_mcp_client(app) as session:
            result = await session.list_tools()
            assert len(result.tools) == 7


# ============================================================================
# Pass-Through
# ============================================================================


class TestPassThrough:

    # -- kms_vault_info ------------------------------------------------------

    def test_kms_vault_info_returns_engine_value(self):
        from mcp_server.tools import kms_vault_info

        fake = [{"type": "context", "source": "entity_map", "content": "# Map"}]
        engine = MagicMock()
        engine.build_vault_info_response.return_value = Success(fake)
        ctx = MagicMock()
        ctx.request_context.lifespan_context = {"engine": engine}

        result = kms_vault_info(ctx)
        assert result == fake
        engine.build_vault_info_response.assert_called_once()

    # -- kms_search ----------------------------------------------------------

    def test_kms_search_returns_engine_value(self):
        from mcp_server.tools import kms_search

        fake = [{"type": "result_card", "data": {}}]
        engine = MagicMock()
        engine.build_search_response.return_value = Success(fake)
        ctx = MagicMock()
        ctx.request_context.lifespan_context = {"engine": engine}

        result = kms_search(query="q", project="A", since="2026-01-01",
                            until="2026-06-01", location="Projects",
                            max_results=10, ctx=ctx)
        assert result == fake
        engine.build_search_response.assert_called_once_with(
            query="q", project="A", since="2026-01-01",
            until="2026-06-01", location="Projects", max_results=10,
        )

    def test_kms_search_defaults(self):
        from mcp_server.tools import kms_search

        engine = MagicMock()
        engine.build_search_response.return_value = Success([])
        ctx = MagicMock()
        ctx.request_context.lifespan_context = {"engine": engine}

        kms_search(query="q", ctx=ctx)
        engine.build_search_response.assert_called_once_with(
            query="q", project=None, since=None, until=None,
            location=None, max_results=None,
        )

    # -- kms_inspect ---------------------------------------------------------

    def test_kms_inspect_returns_resolve_dicts_value(self):
        from mcp_server.tools import kms_inspect

        fake = [{"doc_id": 1, "mode": "summary", "content": "C",
                 "title": "T", "degraded": False}]
        with patch("mcp_server._resolve.resolve_dicts") as mock:
            mock.return_value = fake
            result = kms_inspect(doc_ids=[1], mode="summary", ctx=MagicMock())
            assert result == fake
            mock.assert_called_once_with([1], "summary")

    def test_kms_inspect_ctx_optional(self):
        from mcp_server.tools import kms_inspect
        with patch("mcp_server._resolve.resolve_dicts") as mock:
            mock.return_value = [{"doc_id": 1, "mode": "summary",
                                  "content": "x", "title": "T", "degraded": False}]
            result = kms_inspect(doc_ids=[1])
            assert result[0]["content"] == "x"

    # -- kms_write -----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_kms_write_returns_document_id(self):
        from mcp_server.tools import kms_write

        with patch("mcp_server._write.write_from_chat") as mock:
            mock.return_value = Success(42)
            ctx = MagicMock()
            ctx.request_context.lifespan_context = {"classify_queue": None}
            result = await kms_write(content="hello", title_hint="greeting", ctx=ctx)
            assert result == {"document_id": 42}
            mock.assert_awaited_once_with(
                "hello", "greeting", classify_queue=None
            )

    # -- kms_correct ---------------------------------------------------------

    def test_kms_correct_returns_result_dict(self):
        from mcp_server.tools import kms_correct

        with patch("mcp_server._correct.correct_entry") as mock:
            mock.return_value = Success({"entry_id": 1, "operation": "retire", "result": "applied"})
            result = kms_correct(entry_id=1, operation="retire", reason="stale")
            assert result == {"entry_id": 1, "operation": "retire", "result": "applied"}
            mock.assert_called_once_with(
                1, "retire", new_fact=None, new_tag=None,
                new_entity=None, reason="stale",
                reason_category=None, feedback=None,
            )

    def test_kms_correct_ctx_optional(self):
        from mcp_server.tools import kms_correct

        with patch("mcp_server._correct.correct_entry") as mock:
            mock.return_value = Success({"entry_id": 1, "operation": "promote", "result": "applied"})
            result = kms_correct(entry_id=1, operation="promote")
            assert result["result"] == "applied"


# ============================================================================
# ctx Excluded from Schema
# ============================================================================


class TestCtxExcludedFromSchema:
    @pytest.mark.asyncio
    async def test_kms_search_schema_has_no_ctx_parameter(self):
        from mcp_server.context import ContextInjectionEngine

        @asynccontextmanager
        async def _lifespan(app):
            yield {"engine": ContextInjectionEngine()}

        app = FastMCP("test_kms_schema", lifespan=_lifespan)
        from mcp_server.tools import register_tools
        register_tools(app)

        async with _in_memory_mcp_client(app) as session:
            tools_result = await session.list_tools()

        kms_search_tool = next(t for t in tools_result.tools if t.name == "kms_search")
        schema_props = kms_search_tool.inputSchema.get("properties", {})
        assert "ctx" not in schema_props
        for param in ("query", "project", "since", "until", "location", "max_results"):
            assert param in schema_props, f"Expected {param!r} in schema, got: {list(schema_props.keys())}"

    @pytest.mark.asyncio
    async def test_kms_vault_info_has_no_ctx_in_schema(self):
        from mcp_server.context import ContextInjectionEngine

        @asynccontextmanager
        async def _lifespan(app):
            yield {"engine": ContextInjectionEngine()}

        app = FastMCP("test_kms_schema_vi", lifespan=_lifespan)
        from mcp_server.tools import register_tools
        register_tools(app)

        async with _in_memory_mcp_client(app) as session:
            tools_result = await session.list_tools()

        vault_info_tool = next(t for t in tools_result.tools if t.name == "kms_vault_info")
        schema_props = vault_info_tool.inputSchema.get("properties", {})
        assert "ctx" not in schema_props
