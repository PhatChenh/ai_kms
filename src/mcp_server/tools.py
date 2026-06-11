"""
mcp_server/tools.py — MCP Tool Shim Layer (Component 7)

Five tools, each ONE expression (C-14 hard block).
ctx is auto-excluded from the public schema by the FastMCP framework.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import Context

from mcp_server._move import move
from mcp_server._resolve import inspect


def kms_vault_info(ctx: Context) -> list[dict]:
    """Discover the vault: projects, domains, inbox stats, and global context."""
    return (
        ctx.request_context.lifespan_context["engine"]
        .build_vault_info_response()
        .unwrap()
    )


def kms_search(
    query: str,
    project: str | None = None,
    since: str | None = None,
    until: str | None = None,
    location: str | None = None,
    include_context: bool = False,
    ctx: Context = None,
) -> list[dict]:
    """Search the vault with context injection. Returns context blocks + result cards."""
    return (
        ctx.request_context.lifespan_context["engine"]
        .build_search_response(
            query=query,
            project=project,
            since=since,
            until=until,
            location=location,
            include_context=include_context,
        )
        .unwrap()
    )


def kms_read(
    paths: list[str],
    include_context: bool = False,
    ctx: Context = None,
) -> list[dict]:
    """Read full note bodies with optional minority-domain context injection."""
    return (
        ctx.request_context.lifespan_context["engine"]
        .build_read_response(
            paths=[Path(p) for p in paths],
            include_context=include_context,
        )
        .unwrap()
    )


def kms_inspect(path: str, ctx: Context = None) -> str:
    """Re-extract raw text from a binary source (via sibling .md or direct path). No AI call."""
    return inspect(Path(path)).unwrap()


def kms_move(
    src: str,
    dest_name: str,
    dest_kind: str,
    ctx: Context = None,
) -> str:
    """Move a note to a project or domain folder. dest_kind is 'project' or 'domain'."""
    return move(Path(src), dest_name, dest_kind).unwrap()


# ---------------------------------------------------------------------------
# Register tools on a FastMCP application
# ---------------------------------------------------------------------------


def register_tools(mcp):
    """Register all five KMS tools on *mcp* (a ``FastMCP`` instance)."""
    mcp.tool(
        description="Discover the vault structure: projects, domains, inbox statistics, and global context. Call this FIRST before any search."
    )(kms_vault_info)
    mcp.tool(
        description="Search the vault. Returns context blocks (if query is focused) followed by result cards. Use project/since/until/location to filter. For broad queries context is automatically skipped."
    )(kms_search)
    mcp.tool(
        description="Read full note bodies for one or more vault paths. Use after kms_search to get full content. Accepts multiple paths for batch reading."
    )(kms_read)
    mcp.tool(
        description="Re-extract raw text from a binary source file (PDF, DOCX, etc.). Accepts either the binary path directly or its sibling .md summary path. No AI call — returns original text."
    )(kms_inspect)
    mcp.tool(
        description="Move a note to a project or domain folder. Updates frontmatter labels, reindexes, and prevents watcher undo. dest_kind must be 'project' or 'domain'."
    )(kms_move)
