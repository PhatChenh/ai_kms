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


def test_summarize_prompt_exists_in_prompts() -> None:
    assert "summarize" in PROMPTS


def test_summarize_render_injects_text_into_user_message() -> None:
    _, user = PROMPTS["summarize"].render(text="hello")
    assert "hello" in user


def test_summarize_render_returns_nonempty_system_message() -> None:
    system, _ = PROMPTS["summarize"].render(text="any content")
    assert len(system) > 0


def test_extract_metadata_prompt_exists_in_prompts() -> None:
    assert "extract_metadata" in PROMPTS


def test_extract_metadata_render_injects_text_and_summary() -> None:
    _, user = PROMPTS["extract_metadata"].render(text="t", summary="s", domain_list="(none)")
    assert "t" in user
    assert "s" in user


def test_extract_metadata_render_returns_nonempty_system_message() -> None:
    system, _ = PROMPTS["extract_metadata"].render(text="t", summary="s", domain_list="(none)")
    assert len(system) > 0


def test_summarize_attachment_prompt_exists_in_prompts() -> None:
    assert "summarize_attachment" in PROMPTS


def test_summarize_attachment_render_returns_both_strings() -> None:
    result = PROMPTS["summarize_attachment"].render(file_type=".pdf", short_summary="x", text="y")
    assert isinstance(result, tuple)
    assert len(result) == 2
    system, user = result
    assert len(system) > 0
    assert len(user) > 0


def test_summarize_attachment_render_injects_all_three_variables() -> None:
    _, user = PROMPTS["summarize_attachment"].render(
        file_type=".pdf", short_summary="quarterly results", text="revenue up 12 percent"
    )
    assert ".pdf" in user
    assert "quarterly results" in user
    assert "revenue up 12 percent" in user


def test_summarize_attachment_render_missing_variable_raises_undefined_error() -> None:
    with pytest.raises(jinja2.UndefinedError):
        PROMPTS["summarize_attachment"].render(file_type=".pdf", short_summary="x")
