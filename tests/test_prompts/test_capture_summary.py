"""Tests for the capture_summary prompt (Phase 7A Component 1).

The prompt must:
- Exist in the PROMPTS registry under key "capture_summary"
- Render to a (system, user) tuple when given text=
- Contain all five fixed headers (## Overview, ## Key points, ## Decisions,
  ## Action items, ## People mentioned) in order
- Include a title instruction
"""

from llm.prompt_loader import PROMPTS


class TestCaptureSummaryPrompt:
    """Verify the structured-summary prompt loads and renders correctly."""

    def test_prompt_exists_in_registry(self):
        """The prompt must be auto-discovered from prompts/*.yaml."""
        assert "capture_summary" in PROMPTS, (
            "capture_summary prompt not found in PROMPTS registry. "
            "Expected file: src/prompts/capture_summary.yaml"
        )

    def test_render_returns_system_user_tuple(self):
        """render(text=...) must return a (system, user) tuple."""
        prompt = PROMPTS["capture_summary"]
        result = prompt.render(text="Sample meeting notes go here.")
        assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
        assert len(result) == 2, f"Expected 2 elements, got {len(result)}"
        system, user = result
        assert isinstance(system, str), f"system must be str, got {type(system)}"
        assert isinstance(user, str), f"user must be str, got {type(user)}"
        assert len(system) > 0, "system prompt must not be empty"
        assert len(user) > 0, "user prompt must not be empty"

    def test_system_contains_five_fixed_headers_in_order(self):
        """The system prompt must contain all five headers in order."""
        prompt = PROMPTS["capture_summary"]
        system, _user = prompt.render(text="Test content.")
        headers = [
            "## Overview",
            "## Key points",
            "## Decisions",
            "## Action items",
            "## People mentioned",
        ]
        positions = []
        for header in headers:
            idx = system.find(header)
            assert idx >= 0, (
                f"Header '{header}' not found in system prompt:\n{system[:500]}..."
            )
            positions.append(idx)
        # Verify they appear in order
        assert positions == sorted(positions), (
            f"Headers must appear in order. Found at positions: {positions}"
        )

    def test_system_instructs_descriptive_title(self):
        """The system prompt must ask for a short descriptive title."""
        prompt = PROMPTS["capture_summary"]
        system, _user = prompt.render(text="Test content.")
        title_hints = ["title", "Title", "descriptive"]
        found = any(hint in system for hint in title_hints)
        assert found, (
            f"System prompt must mention a descriptive title. "
            f"System (first 500 chars):\n{system[:500]}..."
        )

    def test_user_contains_the_passed_text(self):
        """The user template must include the {text} variable content."""
        prompt = PROMPTS["capture_summary"]
        sample = "This is unique test content 42."
        _system, user = prompt.render(text=sample)
        assert sample in user, (
            f"User prompt must contain the passed text. "
            f"User (first 500 chars):\n{user[:500]}..."
        )
