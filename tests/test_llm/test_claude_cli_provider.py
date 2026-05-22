"""
tests/test_llm/test_claude_cli_provider.py

Unit tests for ClaudeCliProvider. All subprocess calls are mocked — no real
claude binary needed for unit tests.

Test map:
  Phase 2 — import and ABC compliance
  Phase 4 — full failure-path coverage (happy path, error paths, timeout, etc.)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import ClaudeCliConfig
from core.exceptions import ConfigError
from core.result import Failure, Success
from llm.provider import LLMProvider, LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> ClaudeCliConfig:
    defaults = dict(
        cli_path="claude",
        model="claude-haiku-4-5-20251001",
        synthesis_model="claude-sonnet-4-20250514",
        embedding_model="voyage-3",
        max_tokens=1024,
        timeout=60,
    )
    defaults.update(overrides)
    return ClaudeCliConfig(**defaults)


# ---------------------------------------------------------------------------
# Phase 2 — import and ABC compliance
# ---------------------------------------------------------------------------

class TestClaudeCliProviderImport:

    def test_imports_without_error(self):
        from llm.claude_cli_provider import ClaudeCliProvider  # noqa: F401

    def test_satisfies_llmprovider_abc(self):
        from llm.claude_cli_provider import ClaudeCliProvider
        with patch("shutil.which", return_value="/usr/bin/claude"):
            provider = ClaudeCliProvider(_make_config(), task="capture")
        assert isinstance(provider, LLMProvider)
