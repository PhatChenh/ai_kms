import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from llm.provider import LLMProvider, LLMResponse, get_provider
from core.config import ClaudeCliConfig, OpenAICompatConfig


def test_llm_response_is_frozen() -> None:
    r = LLMResponse(content="x", model="y", usage={})
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        r.content = "z"  # type: ignore[misc]


def test_llm_response_stores_fields() -> None:
    r = LLMResponse(content="hello", model="claude-haiku", usage={"input": 10})
    assert r.content == "hello"
    assert r.model == "claude-haiku"
    assert r.usage == {"input": 10}


def test_openai_compat_config_defaults() -> None:
    cfg = OpenAICompatConfig()
    assert cfg.base_url == "https://api.fireworks.ai/inference/v1"
    assert cfg.model == "accounts/fireworks/models/gpt-oss-20b"
    assert cfg.max_tokens == 1024
    assert cfg.timeout == 60
    assert cfg.api_key_env == "FIREWORKS_API_KEY"


def test_llm_provider_is_abstract() -> None:
    # LLMProvider cannot be instantiated directly — complete() is abstract
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Phase 3 — get_provider factory: claude_cli routing
# ---------------------------------------------------------------------------

def _make_cli_config(**overrides) -> ClaudeCliConfig:
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


def _make_mock_main_config(provider_name: str, cli_cfg: ClaudeCliConfig) -> MagicMock:
    """Build a minimal MainConfig mock that routes all tasks to provider_name."""
    cfg = MagicMock()
    cfg.providers.for_task.return_value = provider_name
    cfg.claude_cli = cli_cfg
    return cfg


class TestGetProviderClaudeCli:
    def test_returns_claude_cli_provider_for_capture_task(self):
        from llm.claude_cli_provider import ClaudeCliProvider
        cli_cfg = _make_cli_config()
        mock_main = _make_mock_main_config("claude_cli", cli_cfg)
        with patch("shutil.which", return_value="/usr/bin/claude"):
            provider = get_provider("capture", mock_main)
        assert isinstance(provider, ClaudeCliProvider)

    def test_capture_task_uses_default_model(self):
        cli_cfg = _make_cli_config()
        mock_main = _make_mock_main_config("claude_cli", cli_cfg)
        with patch("shutil.which", return_value="/usr/bin/claude"):
            provider = get_provider("capture", mock_main)
        assert provider._model == cli_cfg.model

    def test_synthesis_task_uses_synthesis_model(self):
        cli_cfg = _make_cli_config()
        mock_main = _make_mock_main_config("claude_cli", cli_cfg)
        with patch("shutil.which", return_value="/usr/bin/claude"):
            provider = get_provider("synthesis", mock_main)
        assert provider._model == cli_cfg.synthesis_model

    def test_unknown_provider_raises_value_error_with_claude_cli_in_message(self):
        mock_main = MagicMock()
        mock_main.providers.for_task.return_value = "unknown_provider"
        with pytest.raises(ValueError, match="claude_cli"):
            get_provider("capture", mock_main)
