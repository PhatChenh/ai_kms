"""
tests/test_llm/test_ollama_provider.py

Unit tests for OllamaProvider. _post() is mocked — no real Ollama server needed.
"""

from unittest.mock import MagicMock

import pytest

from core.config import OllamaConfig
from core.result import Failure, Success
from llm.provider import LLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> OllamaConfig:
    defaults = dict(
        base_url="http://localhost:11434",
        chat_model="llama3",
        synthesis_model="llama3",
        embedding_model="nomic-embed-text",
        timeout=120,
        delay_between_calls=2,
    )
    defaults.update(overrides)
    return OllamaConfig(**defaults)


# ---------------------------------------------------------------------------
# ABC conformance
# ---------------------------------------------------------------------------

class TestOllamaProviderABC:
    def test_is_subclass_of_llm_provider(self):
        from llm.ollama_provider import OllamaProvider
        assert issubclass(OllamaProvider, LLMProvider)


class TestOllamaProviderInit:
    def test_selects_synthesis_model_for_synthesis_task(self):
        from llm.ollama_provider import OllamaProvider
        config = _make_config()
        provider = OllamaProvider(config, task="synthesis")
        assert provider._model == config.synthesis_model

    def test_selects_chat_model_for_capture_task(self):
        from llm.ollama_provider import OllamaProvider
        config = _make_config()
        provider = OllamaProvider(config, task="capture")
        assert provider._model == config.chat_model


# ---------------------------------------------------------------------------
# complete() tests
# ---------------------------------------------------------------------------

class TestOllamaProviderComplete:
    @pytest.fixture
    def provider(self):
        from llm.ollama_provider import OllamaProvider
        return OllamaProvider(_make_config())

    @pytest.mark.asyncio
    async def test_complete_returns_success_with_llm_response(self, provider):
        provider._post = MagicMock(return_value={"response": "hello world"})

        result = await provider.complete("be helpful", "say hello")

        assert isinstance(result, Success)
        assert result.value.content == "hello world"
        assert result.value.model == provider._model

    @pytest.mark.asyncio
    async def test_complete_returns_empty_string_when_response_key_missing(self, provider):
        provider._post = MagicMock(return_value={})

        result = await provider.complete("system", "user")

        assert isinstance(result, Success)
        assert result.value.content == ""

    @pytest.mark.asyncio
    async def test_connection_error_returns_failure_recoverable(self, provider):
        provider._post = MagicMock(side_effect=ConnectionError("Ollama not running"))

        result = await provider.complete("system", "user")

        assert isinstance(result, Failure)
        assert result.recoverable is True

    @pytest.mark.asyncio
    async def test_complete_usage_is_empty_dict(self, provider):
        provider._post = MagicMock(return_value={"response": "ok"})

        result = await provider.complete("system", "user")

        assert isinstance(result, Success)
        assert result.value.usage == {}

    @pytest.mark.asyncio
    async def test_timeout_error_returns_failure_recoverable(self, provider):
        provider._post = MagicMock(side_effect=TimeoutError("timed out"))

        result = await provider.complete("system", "user")

        assert isinstance(result, Failure)
        assert result.recoverable is True

    @pytest.mark.asyncio
    async def test_complete_passes_system_and_model_to_post(self, provider):
        mock_post = MagicMock(return_value={"response": "pong"})
        provider._post = mock_post

        await provider.complete("you are helpful", "ping")

        call_args = mock_post.call_args
        payload = call_args[0][1]  # second positional arg
        assert payload["system"] == "you are helpful"
        assert payload["model"] == provider._model
        assert payload["prompt"] == "ping"


# ---------------------------------------------------------------------------
# Integration test — requires Ollama server running locally
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_ollama_real_call():
    """Calls real Ollama server. Requires `ollama serve` running locally."""
    import requests as req
    try:
        req.get("http://localhost:11434", timeout=2)
    except req.exceptions.ConnectionError:
        pytest.skip("Ollama not running — start with: ollama serve")

    from llm.ollama_provider import OllamaProvider
    config = _make_config(timeout=180)  # cold model load can take 30s+
    provider = OllamaProvider(config)
    result = await provider.complete(
        system="respond with one word only",
        user="ping",
    )
    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert result.value.content.strip(), "Expected non-empty content"
    print(f"\n[Ollama] model={result.value.model!r} response={result.value.content!r}")
