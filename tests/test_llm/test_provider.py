import dataclasses

import pytest

from llm.provider import LLMProvider, LLMResponse, get_provider
from core.config import OpenAICompatConfig


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
