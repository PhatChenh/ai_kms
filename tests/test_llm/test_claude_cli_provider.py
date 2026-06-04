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
from llm.provider import LLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    cli_path: str = "claude",
    model: str = "claude-haiku-4-5-20251001",
    synthesis_model: str = "claude-sonnet-4-20250514",
    embedding_model: str = "voyage-3",
    max_tokens: int = 1024,
    timeout: int = 60,
) -> ClaudeCliConfig:
    return ClaudeCliConfig(
        cli_path=cli_path,
        model=model,
        synthesis_model=synthesis_model,
        embedding_model=embedding_model,
        max_tokens=max_tokens,
        timeout=timeout,
    )


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


# ---------------------------------------------------------------------------
# Phase 4 — __init__ failure paths
# ---------------------------------------------------------------------------

class TestClaudeCliProviderInit:
    def test_raises_config_error_when_binary_not_found(self):
        from llm.claude_cli_provider import ClaudeCliProvider
        with patch("shutil.which", return_value=None):
            with pytest.raises(ConfigError, match="not found"):
                ClaudeCliProvider(_make_config())


# ---------------------------------------------------------------------------
# Phase 4 — complete() paths
# ---------------------------------------------------------------------------

class TestClaudeCliProviderComplete:
    @pytest.fixture
    def provider(self):
        from llm.claude_cli_provider import ClaudeCliProvider
        with patch("shutil.which", return_value="/usr/bin/claude"):
            return ClaudeCliProvider(_make_config(), task="capture")

    def _make_proc(
        self, stdout_data: bytes, stderr_data: bytes = b"", returncode: int = 0
    ) -> AsyncMock:
        proc = AsyncMock()
        proc.communicate.return_value = (stdout_data, stderr_data)
        proc.returncode = returncode
        proc.kill = MagicMock()
        return proc

    @pytest.mark.asyncio
    async def test_happy_path_returns_success_with_content_model_usage(self, provider):
        data = {"result": "Summary text", "cost_usd": 0.001, "duration_ms": 500, "is_error": False}
        proc = self._make_proc(json.dumps(data).encode())
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await provider.complete("system prompt", "user content")
        assert isinstance(result, Success)
        assert result.value.content == "Summary text"
        assert result.value.model == "claude-haiku-4-5-20251001"
        assert result.value.usage == {"cost_usd": 0.001, "duration_ms": 500}

    @pytest.mark.asyncio
    async def test_is_error_flag_returns_failure_recoverable(self, provider):
        data = {"is_error": True, "result": "auth failed"}
        proc = self._make_proc(json.dumps(data).encode())
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await provider.complete("system", "user")
        assert isinstance(result, Failure)
        assert result.recoverable is True

    @pytest.mark.asyncio
    async def test_timeout_returns_failure_recoverable_and_kills_process(self, provider):
        proc = AsyncMock()
        proc.kill = MagicMock()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
                result = await provider.complete("system", "user")
        assert isinstance(result, Failure)
        assert result.recoverable is True
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_json_stdout_returns_failure_recoverable(self, provider):
        proc = self._make_proc(b"not valid json at all")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await provider.complete("system", "user")
        assert isinstance(result, Failure)
        assert result.recoverable is True

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_returns_failure_recoverable(self, provider):
        data = {"result": "output", "is_error": False}
        proc = self._make_proc(json.dumps(data).encode(), returncode=1)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await provider.complete("system", "user")
        assert isinstance(result, Failure)
        assert result.recoverable is True

    @pytest.mark.asyncio
    async def test_markdown_fenced_result_is_stripped(self, provider):
        fenced = '```json\n{"key": "value"}\n```'
        data = {"result": fenced, "cost_usd": 0.0, "duration_ms": 0, "is_error": False}
        proc = self._make_proc(json.dumps(data).encode())
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await provider.complete("system", "user")
        assert isinstance(result, Success)
        assert result.value.content == '{"key": "value"}'


# ---------------------------------------------------------------------------
# Phase 4 — Smoke test (requires real claude binary + auth)
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_real_claude_binary_completes():
    """Calls real claude binary. Skipped if binary absent."""
    import shutil
    from llm.claude_cli_provider import ClaudeCliProvider
    if shutil.which("claude") is None:
        pytest.skip("claude binary not found")
    provider = ClaudeCliProvider(_make_config(timeout=30), task="capture")
    result = await provider.complete(
        "You are a test assistant.",
        "Reply with the single word: OK",
    )
    assert isinstance(result, Success)
    assert "OK" in result.value.content
