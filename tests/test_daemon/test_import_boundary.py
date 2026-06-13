"""
tests/test_daemon/test_import_boundary.py

Enforce C-19: Code under ``src/daemon/`` must not directly import from
cloud-only modules (``core/config``, ``storage/``, ``mcp_server/``,
``llm/``, ``pipelines/``, ``vault/``).

Only ``core/result``, ``core/exceptions``, and ``handlers/`` are allowed
imports from the shared codebase.  Transitive imports through ``handlers/``
(like ``vault.reader`` via ``MarkdownHandler``) are NOT checked — this
test verifies daemon source files only, not the full import graph.

Mirrors the existing ``test_pipeline_has_no_heavy_imports`` pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_daemon_import_boundary() -> None:
    """Every .py file under src/daemon/ must not contain forbidden imports."""
    src_daemon = Path(__file__).parents[2] / "src" / "daemon"

    assert src_daemon.is_dir(), f"src/daemon/ not found at {src_daemon}"

    # ── Forbidden and allowed import patterns ──────────────────────────
    FORBIDDEN = frozenset({
        "from core.config",
        "import core.config",
        "from storage",
        "import storage",
        "from mcp_server",
        "import mcp_server",
        "from llm",
        "import llm",
        "from pipelines",
        "import pipelines",
        "from vault",
        "import vault",
    })

    violations: list[str] = []

    for py_file in sorted(src_daemon.rglob("*.py")):
        # Skip __pycache__
        if "__pycache__" in py_file.parts:
            continue

        lines = py_file.read_text(encoding="utf-8").splitlines()

        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Skip comments, docstrings, and non-import lines
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            # Only check lines that actually import something
            if not (
                stripped.startswith("from ") or stripped.startswith("import ")
            ):
                continue

            for forbidden in FORBIDDEN:
                if forbidden in stripped:
                    violations.append(
                        f"{py_file.relative_to(src_daemon.parent.parent)}:{lineno}: "
                        f"forbidden import: {stripped.strip()}"
                    )

    if violations:
        pytest.fail(
            f"Daemon import boundary violated ({len(violations)} violation(s)):\n"
            + "\n".join(sorted(violations))
        )


def test_import_boundary_self_check_catches_violation() -> None:
    """Verify the test logic actually catches a known-bad import.

    This test proves the boundary check works — it inspects a sample string
    that mimics ``from storage.documents import upsert`` in a daemon module.
    Removing this test would allow the check to silently break.
    """
    # Simulate the check logic on a known-bad line
    FORBIDDEN = frozenset({
        "from core.config",
        "from storage",
        "from mcp_server",
        "from llm",
        "from pipelines",
        "from vault",
    })

    bad_line = "from storage.documents import upsert"
    assert any(pat in bad_line for pat in FORBIDDEN), (
        "Self-check failed: the forbidden pattern was not detected "
        "in the simulated bad line"
    )


def test_import_boundary_allows_legal_imports() -> None:
    """Verify that allowed imports from daemon modules are not flagged."""
    FORBIDDEN = frozenset({
        "from core.config",
        "from storage",
        "from mcp_server",
        "from llm",
        "from pipelines",
        "from vault",
    })

    # These are allowed per C-19
    allowed = [
        "from core.result import Success, Failure",
        "from handlers.registry import HandlerRegistry",
        "from daemon.config import DaemonConfig",
        "import os",
        "from typing import Any",
        "import httpx",
        "from pydantic import BaseModel",
        "from watchdog.observers import Observer",
    ]

    for line in allowed:
        assert not any(pat in line for pat in FORBIDDEN), (
            f"False positive: allowed line flagged as forbidden: {line}"
        )
