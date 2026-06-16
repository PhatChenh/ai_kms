"""Tests for entity_extract.yaml prompt rendering."""

from dataclasses import dataclass

from llm.prompt_loader import PROMPTS


@dataclass
class _FakeFact:
    """Minimal fact stub for prompt rendering tests."""

    id: int
    entity: str
    tag: str
    fact: str
    confidence: float


class TestEntityExtractPrompt:
    """Phase 3 Slice B — entity_extract.yaml renders correctly for all template vars."""

    def test_render_with_all_vars_produces_system_and_user(self):
        """Rendering with all vars returns a (system, user) tuple."""
        prompt = PROMPTS["entity_extract"]
        system, user = prompt.render(
            document_text="Anthony leads the Movie Q2 project.",
            dimension_guidance="Tag: status — extract project status facts.",
            existing_facts=[
                _FakeFact(
                    id=1,
                    entity="Anthony",
                    tag="other",
                    fact="Anthony works in engineering.",
                    confidence=0.9,
                ),
            ],
            previous_attempt_feedback="",
            few_shot_corrections="",
        )
        assert isinstance(system, str)
        assert isinstance(user, str)
        assert len(system) > 0
        assert len(user) > 0

    def test_render_contains_existing_fact_ids(self):
        """The rendered user template shows each existing fact's id so the AI
        can target update/retire by id."""
        prompt = PROMPTS["entity_extract"]
        _, user = prompt.render(
            document_text="Some text.",
            dimension_guidance="Tag: status",
            existing_facts=[
                _FakeFact(
                    id=42,
                    entity="Anthony",
                    tag="other",
                    fact="Test fact.",
                    confidence=0.8,
                ),
            ],
            previous_attempt_feedback="",
            few_shot_corrections="",
        )
        assert "42" in user, "Rendered user should contain fact id 42"
        assert "[id=42]" in user, "Rendered user should show [id=42] marker"

    def test_render_contains_dimension_guidance(self):
        """The guidance text from dimensions.yaml appears in the prompt."""
        prompt = PROMPTS["entity_extract"]
        _, user = prompt.render(
            document_text="Some text.",
            dimension_guidance="Tag: people — extract people-related facts.",
            existing_facts=[],
            previous_attempt_feedback="",
            few_shot_corrections="",
        )
        assert "Tag: people" in user
        assert "extract people-related facts" in user

    def test_render_contains_feedback_when_nonempty(self):
        """Feedback from a previous failed attempt is rendered so the AI can
        avoid repeating the same mistake."""
        prompt = PROMPTS["entity_extract"]
        _, user = prompt.render(
            document_text="Some text.",
            dimension_guidance="Tag: status",
            existing_facts=[],
            previous_attempt_feedback="JSON parse error: trailing comma at line 12",
            few_shot_corrections="",
        )
        assert "JSON parse error" in user
        assert "Avoid repeating the same mistake" in user

    def test_render_empty_feedback_is_clean(self):
        """Empty feedback renders without stray template markers."""
        prompt = PROMPTS["entity_extract"]
        _, user = prompt.render(
            document_text="Some text.",
            dimension_guidance="Tag: status",
            existing_facts=[],
            previous_attempt_feedback="",
            few_shot_corrections="",
        )
        # No leftover template syntax
        assert "{%" not in user, "No Jinja2 tags should remain"
        assert "Avoid repeating the same mistake" not in user, (
            "The feedback-only block should not appear when feedback is empty"
        )

    def test_render_states_json_only_reply_contract(self):
        """The system prompt instructs JSON-only output with the per-fact action schema."""
        prompt = PROMPTS["entity_extract"]
        system, user = prompt.render(
            document_text="Some text.",
            dimension_guidance="Tag: status",
            existing_facts=[],
            previous_attempt_feedback="",
            few_shot_corrections="",
        )
        combined = system + user
        assert "JSON" in combined, "Prompt should mention JSON output"
        assert '"action"' in combined, "Prompt should show the action field"
        assert '"new"' in combined, "Prompt should mention new action"
        assert '"update"' in combined, "Prompt should mention update action"
        assert '"retire"' in combined, "Prompt should mention retire action"

    def test_render_handles_missing_feedback_arg(self):
        """The prompt's feedback block uses Jinja2 if-check, so the variable
        must be passed even when empty.  Verify StrictUndefined raises on
        a missing variable."""
        import pytest
        from jinja2 import UndefinedError

        prompt = PROMPTS["entity_extract"]
        with pytest.raises(UndefinedError):
            prompt.render(
                document_text="Some text.",
                dimension_guidance="Tag: status",
                existing_facts=[],
                # previous_attempt_feedback intentionally missing
            )
