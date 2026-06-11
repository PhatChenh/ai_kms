"""Tests that the ``mcp`` dependency is installable and importable.

Marked with ``needs_mcp_dep`` so they can be skipped before the dependency is
added to pyproject.toml and installed::

    pytest -m "not needs_mcp_dep"
"""

from __future__ import annotations

import pytest


@pytest.mark.needs_mcp_dep
class TestMCPDependency:
    """Verify the Phase 4 MCP dependency can be imported."""

    def test_can_import_fastmcp_and_context(self) -> None:
        """``mcp>=1.27,<2`` provides FastMCP and Context."""
        from mcp.server.fastmcp import Context, FastMCP  # noqa: F401

        assert FastMCP is not None
        assert Context is not None
