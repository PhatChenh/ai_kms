"""
mcp_server/tools.py — MCP Tool Shim Layer

Five tools, each ONE expression (C-14 hard block).
ctx is auto-excluded from the public schema by the FastMCP framework.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context


def kms_vault_info(ctx: Context) -> list[dict]:
    """Discover the vault: entity map, orientation facts, and inbox stats."""
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
    max_results: int | None = None,
    ctx: Context = None,
) -> list[dict]:
    """Search facts and documents. Returns orientation blocks + fact results + result cards."""
    return (
        ctx.request_context.lifespan_context["engine"]
        .build_search_response(
            query=query,
            project=project,
            since=since,
            until=until,
            location=location,
            max_results=max_results,
        )
        .unwrap()
    )


def kms_inspect(
    doc_ids: list[int],
    mode: str = "summary",
    ctx: Context = None,
) -> list[dict]:
    """Drill into documents by integer id. Mode: summary, text, file."""
    from mcp_server._resolve import resolve_dicts

    return resolve_dicts(doc_ids, mode)


async def kms_write(
    content: str,
    title_hint: str | None = None,
    ctx: Context = None,
) -> dict:
    """Save a chat insight as a new document in the knowledge system."""
    from mcp_server._write import write_from_chat

    return {"document_id": (await write_from_chat(
        content, title_hint,
        classify_queue=ctx.request_context.lifespan_context.get("classify_queue") if ctx else None,
    )).unwrap()}


def kms_correct(
    entry_id: int,
    operation: str,
    new_fact: str | None = None,
    new_tag: str | None = None,
    new_entity: str | None = None,
    reason: str | None = None,
    ctx: Context = None,
) -> dict:
    """Correct a knowledge entry. Operations: retire, edit_fact, change_tag, change_entity, promote, un_retire."""
    from mcp_server._correct import correct_entry

    return correct_entry(
        entry_id,
        operation,
        new_fact=new_fact,
        new_tag=new_tag,
        new_entity=new_entity,
        reason=reason,
    ).unwrap()


# ---------------------------------------------------------------------------
# Register tools on a FastMCP application
# ---------------------------------------------------------------------------


def register_tools(mcp):
    """Register all five KMS tools on *mcp* (a ``FastMCP`` instance)."""
    mcp.tool(
        description="Discover the knowledge base: entity map grouped by dimension, key orientation facts, and inbox statistics. Call this FIRST before any search."
    )(kms_vault_info)
    mcp.tool(
        description="Search facts and documents across the knowledge base. Returns orientation context, fact results, and document result cards. Use project/since/until/location to filter."
    )(kms_search)
    mcp.tool(
        description="Drill into documents by integer id with three modes: summary (always available), text (full body, may degrade), file (vault path, laptop-dependent). Defaults to summary mode."
    )(kms_inspect)
    mcp.tool(
        description="Save a chat insight as a new document in the knowledge system. The insight will be classified and indexed automatically."
    )(kms_write)
    mcp.tool(
        description="Correct a knowledge entry. Operations: retire (requires reason), edit_fact, change_tag, change_entity, promote (pending→confident), un_retire (retired→confident)."
    )(kms_correct)
