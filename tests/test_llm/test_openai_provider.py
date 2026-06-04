"""
tests/test_llm/test_openai_provider.py

Unit tests for OpenAIProvider. No real API calls — openai.AsyncOpenAI is mocked.
Integration test (marked `integration`) calls real Fireworks endpoint.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from core.config import OpenAICompatConfig
from core.exceptions import ConfigError
from core.result import Failure, Success


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> OpenAICompatConfig:
    defaults = dict(
        base_url="https://api.fireworks.ai/inference/v1",
        model="accounts/fireworks/models/gpt-oss-20b",
        synthesis_model="accounts/fireworks/models/llama-v3p1-70b-instruct",
        embedding_model="nomic-ai/nomic-embed-text-v1.5",
        max_tokens=1024,
        timeout=60,
        api_key_env="FIREWORKS_API_KEY",
    )
    defaults.update(overrides)
    return OpenAICompatConfig(**defaults)


def _make_completion_response(content: str, model: str) -> MagicMock:
    """Build a fake openai ChatCompletion response."""
    usage = MagicMock()
    usage.model_dump.return_value = {"prompt_tokens": 10, "completion_tokens": 5}

    message = MagicMock()
    message.content = content

    choice = MagicMock()
    choice.message = message

    resp = MagicMock()
    resp.choices = [choice]
    resp.model = model
    resp.usage = usage
    return resp


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestOpenAIProviderInit:
    def test_selects_synthesis_model_for_synthesis_task(self, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        config = _make_config()
        from llm.openai_provider import OpenAIProvider
        with patch("llm.openai_provider.openai.AsyncOpenAI"):
            provider = OpenAIProvider(config, task="synthesis")
        assert provider._model == config.synthesis_model

    def test_selects_default_model_for_capture_task(self, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        config = _make_config()
        from llm.openai_provider import OpenAIProvider
        with patch("llm.openai_provider.openai.AsyncOpenAI"):
            provider = OpenAIProvider(config, task="capture")
        assert provider._model == config.model

    def test_raises_config_error_when_env_var_missing(self, monkeypatch):
        monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
        config = _make_config()
        from llm.openai_provider import OpenAIProvider
        with pytest.raises(ConfigError, match="FIREWORKS_API_KEY"):
            OpenAIProvider(config)

    def test_accepts_valid_api_key(self, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        config = _make_config()
        from llm.openai_provider import OpenAIProvider
        with patch("llm.openai_provider.openai.AsyncOpenAI"):
            provider = OpenAIProvider(config)
        assert provider is not None

    def test_uses_custom_api_key_env(self, monkeypatch):
        monkeypatch.setenv("MY_CUSTOM_KEY", "custom-key")
        monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
        config = _make_config(api_key_env="MY_CUSTOM_KEY")
        from llm.openai_provider import OpenAIProvider
        with patch("llm.openai_provider.openai.AsyncOpenAI"):
            provider = OpenAIProvider(config)
        assert provider is not None


class TestOpenAIProviderComplete:
    @pytest.fixture
    def provider(self, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        from llm.openai_provider import OpenAIProvider
        config = _make_config()
        with patch("llm.openai_provider.openai.AsyncOpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            p = OpenAIProvider(config)
            p._client = mock_client
        return p

    @pytest.mark.asyncio
    async def test_complete_returns_success_with_llm_response(self, provider):
        fake_resp = _make_completion_response("hello world", "gpt-4o")
        provider._client.chat = MagicMock()
        provider._client.chat.completions = MagicMock()
        provider._client.chat.completions.create = AsyncMock(return_value=fake_resp)

        result = await provider.complete("be helpful", "say hello")

        assert isinstance(result, Success)
        assert result.value.content == "hello world"
        assert result.value.model == "gpt-4o"
        assert isinstance(result.value.usage, dict)

    @pytest.mark.asyncio
    async def test_complete_includes_usage_dict(self, provider):
        fake_resp = _make_completion_response("ok", "gpt-4o")
        fake_resp.usage.model_dump.return_value = {"prompt_tokens": 10, "completion_tokens": 3}
        provider._client.chat = MagicMock()
        provider._client.chat.completions = MagicMock()
        provider._client.chat.completions.create = AsyncMock(return_value=fake_resp)

        result = await provider.complete("system", "user")

        assert isinstance(result, Success)
        assert result.value.usage == {"prompt_tokens": 10, "completion_tokens": 3}

    @pytest.mark.asyncio
    async def test_api_error_returns_failure_recoverable(self, provider):
        provider._client.chat = MagicMock()
        provider._client.chat.completions = MagicMock()
        provider._client.chat.completions.create = AsyncMock(
            side_effect=openai.APIError("timeout", request=MagicMock(), body=None)
        )

        result = await provider.complete("system", "user")

        assert isinstance(result, Failure)
        assert result.recoverable is True

    @pytest.mark.asyncio
    async def test_network_error_returns_failure_recoverable(self, provider):
        provider._client.chat = MagicMock()
        provider._client.chat.completions = MagicMock()
        provider._client.chat.completions.create = AsyncMock(
            side_effect=ConnectionError("network drop")
        )

        result = await provider.complete("system", "user")

        assert isinstance(result, Failure)
        assert result.recoverable is True

    @pytest.mark.asyncio
    async def test_complete_sends_correct_messages(self, provider):
        fake_resp = _make_completion_response("pong", "gpt-4o")
        mock_create = AsyncMock(return_value=fake_resp)
        provider._client.chat = MagicMock()
        provider._client.chat.completions = MagicMock()
        provider._client.chat.completions.create = mock_create

        await provider.complete("you are helpful", "ping")

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["messages"] == [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "ping"},
        ]


# ---------------------------------------------------------------------------
# Integration test — requires real FIREWORKS_API_KEY
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_fireworks_real_call():
    """Calls real Fireworks endpoint. Requires FIREWORKS_API_KEY in env/.env."""
    from llm.openai_provider import OpenAIProvider
    config = _make_config()
    provider = OpenAIProvider(config)
    result = await provider.complete(
        system="respond with one word only",
        user="ping",
    )
    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert result.value.content.strip(), "Expected non-empty content"
    print(f"\n[Fireworks] model={result.value.model!r} response={result.value.content!r}")
