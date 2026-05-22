"""
tests/test_llm/test_claude_provider.py

Unit tests for ClaudeProvider. AsyncAnthropic client is mocked — no real API calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from core.config import ClaudeConfig
from core.exceptions import ConfigError
from core.result import Failure, Success
from llm.provider import LLMProvider, LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> ClaudeConfig:
    defaults = dict(
        model="claude-haiku-4-5-20251001",
        synthesis_model="claude-sonnet-4-20250514",
        max_tokens=1024,
        timeout=60,
    )
    defaults.update(overrides)
    return ClaudeConfig(**defaults)


def _make_message_response(text: str, model: str) -> MagicMock:
    """Build a fake anthropic Messages response."""
    from anthropic.types import TextBlock

    usage = MagicMock()
    usage.model_dump.return_value = {"input_tokens": 10, "output_tokens": 5}

    content_block = TextBlock(type="text", text=text)

    resp = MagicMock()
    resp.content = [content_block]
    resp.model = model
    resp.usage = usage
    return resp


# ---------------------------------------------------------------------------
# ABC conformance
# ---------------------------------------------------------------------------

class TestClaudeProviderABC:
    def test_is_subclass_of_llm_provider(self):
        from llm.claude_provider import ClaudeProvider
        assert issubclass(ClaudeProvider, LLMProvider)


# ---------------------------------------------------------------------------
# Init tests
# ---------------------------------------------------------------------------

class TestClaudeProviderInit:
    def test_raises_config_error_when_api_key_missing(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        config = _make_config()
        from llm.claude_provider import ClaudeProvider
        with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
            ClaudeProvider(config)

    def test_selects_synthesis_model_for_synthesis_task(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        config = _make_config()
        from llm.claude_provider import ClaudeProvider
        with patch("llm.claude_provider.AsyncAnthropic"):
            provider = ClaudeProvider(config, task="synthesis")
        assert provider._model == config.synthesis_model

    def test_selects_default_model_for_capture_task(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        config = _make_config()
        from llm.claude_provider import ClaudeProvider
        with patch("llm.claude_provider.AsyncAnthropic"):
            provider = ClaudeProvider(config, task="capture")
        assert provider._model == config.model


# ---------------------------------------------------------------------------
# complete() tests
# ---------------------------------------------------------------------------

class TestClaudeProviderComplete:
    @pytest.fixture
    def provider(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        from llm.claude_provider import ClaudeProvider
        config = _make_config()
        with patch("llm.claude_provider.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            p = ClaudeProvider(config)
            p._client = mock_client
        return p

    @pytest.mark.asyncio
    async def test_complete_returns_success_with_llm_response(self, provider):
        fake_resp = _make_message_response("Paris", "claude-haiku-4-5-20251001")
        provider._client.messages = MagicMock()
        provider._client.messages.create = AsyncMock(return_value=fake_resp)

        result = await provider.complete("answer geography questions", "Capital of France?")

        assert isinstance(result, Success)
        assert result.value.content == "Paris"
        assert result.value.model == "claude-haiku-4-5-20251001"
        assert isinstance(result.value.usage, dict)

    @pytest.mark.asyncio
    async def test_auth_error_returns_failure_not_recoverable(self, provider):
        provider._client.messages = MagicMock()
        provider._client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="invalid key",
                response=MagicMock(status_code=401, headers={}),
                body={"error": {"message": "invalid key"}},
            )
        )

        result = await provider.complete("system", "user")

        assert isinstance(result, Failure)
        assert result.recoverable is False

    @pytest.mark.asyncio
    async def test_api_error_returns_failure_recoverable(self, provider):
        provider._client.messages = MagicMock()
        provider._client.messages.create = AsyncMock(
            side_effect=anthropic.APIStatusError(
                message="rate limit",
                response=MagicMock(status_code=429, headers={}),
                body={"error": {"message": "rate limit"}},
            )
        )

        result = await provider.complete("system", "user")

        assert isinstance(result, Failure)
        assert result.recoverable is True

    @pytest.mark.asyncio
    async def test_empty_content_array_returns_empty_string(self, provider):
        fake_resp = _make_message_response("", "claude-haiku-4-5-20251001")
        fake_resp.content = []
        provider._client.messages = MagicMock()
        provider._client.messages.create = AsyncMock(return_value=fake_resp)

        result = await provider.complete("system", "user")

        assert isinstance(result, Success)
        assert result.value.content == ""

    @pytest.mark.asyncio
    async def test_network_error_returns_failure_recoverable(self, provider):
        provider._client.messages = MagicMock()
        provider._client.messages.create = AsyncMock(
            side_effect=ConnectionError("network error")
        )

        result = await provider.complete("system", "user")

        assert isinstance(result, Failure)
        assert result.recoverable is True

    @pytest.mark.asyncio
    async def test_complete_populates_usage_dict(self, provider):
        fake_resp = _make_message_response("ok", "claude-haiku-4-5-20251001")
        fake_resp.usage.model_dump.return_value = {"input_tokens": 10, "output_tokens": 3}
        provider._client.messages = MagicMock()
        provider._client.messages.create = AsyncMock(return_value=fake_resp)

        result = await provider.complete("system", "user")

        assert isinstance(result, Success)
        assert result.value.usage == {"input_tokens": 10, "output_tokens": 3}


# ---------------------------------------------------------------------------
# Integration test — requires ANTHROPIC_API_KEY in env/.env
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_claude_real_call():
    """Calls real Claude API. Requires ANTHROPIC_API_KEY in env/.env."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    from llm.claude_provider import ClaudeProvider
    config = _make_config()
    provider = ClaudeProvider(config)
    result = await provider.complete(
        system="respond with one word only",
        user="ping",
    )
    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert result.value.content.strip(), "Expected non-empty content"
    print(f"\n[Claude] model={result.value.model!r} response={result.value.content!r}")
