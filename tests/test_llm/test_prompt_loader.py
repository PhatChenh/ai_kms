import pytest
import jinja2
from pathlib import Path

from llm.prompt_loader import load_prompts, PROMPTS


def test_load_prompts_empty_directory_returns_empty_dict(tmp_path: Path) -> None:
    result = load_prompts(tmp_path)
    assert result == {}


def test_prompts_singleton_contains_test_prompt() -> None:
    assert "test" in PROMPTS


def test_render_returns_both_system_and_user_strings() -> None:
    rendered = PROMPTS["test"].render(message="hello")
    assert rendered == ("You are a test assistant.", "Echo this back: hello")


def test_render_missing_variable_raises_undefined_error() -> None:
    with pytest.raises(jinja2.UndefinedError):
        PROMPTS["test"].render()


def test_prompts_missing_key_raises_key_error() -> None:
    with pytest.raises(KeyError):
        _ = PROMPTS["nonexistent"]
