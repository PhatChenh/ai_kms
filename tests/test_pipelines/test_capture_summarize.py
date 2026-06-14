"""Tests for _summarize_upload — Phase 7A async summarizer stage.

Uses a stub provider so no real LLM call happens.  All tests use an explicit
``db_path`` — no module-scope CONFIG import (C-17).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.result import Failure, Result, Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# Stub provider for testing — yields a canned LLMResponse or forced Failure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StubLLMResponse:
    content: str
    model: str = "stub"
    usage: dict = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.usage is None:
            object.__setattr__(self, "usage", {})


class StubProvider:
    """A fake LLMProvider whose async complete() returns a pre-set Result."""

    def __init__(self, response: Result | None = None):
        self._response = response
        self.call_count = 0

    async def complete(self, system: str, user: str) -> Result:
        self.call_count += 1
        if self._response is not None:
            return self._response
        # Default: return a valid structured summary
        summary_text = (
            "## Overview\nA test document about planning.\n\n"
            "## Key points\n- Point A\n- Point B\n\n"
            "## Decisions\n- Decided on X\n\n"
            "## Action items\n- Do task Y\n\n"
            "## People mentioned\n- Alice\n- Bob\n\n"
            "Title: Q3 Planning Session"
        )
        return Success(StubLLMResponse(content=summary_text))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Fresh temp database with full schema applied."""
    db_path = tmp_path / "kb.db"
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestSummarizeUpload:
    """Verify the async _summarize_upload stage."""

    @pytest.mark.asyncio
    async def test_success_returns_summary_and_title(self, db, monkeypatch):
        """On AI success, returns Success((summary, title)) with five headers."""
        from pipelines.capture import _summarize_upload

        stub = StubProvider()
        monkeypatch.setattr(
            "pipelines.capture.get_provider", lambda task, config: stub
        )

        result = await _summarize_upload(
            text="Test meeting notes content.",
            db_path=db,
        )
        assert isinstance(result, Success), f"Expected Success, got {result}"
        summary, title = result.value
        assert isinstance(summary, str)
        assert isinstance(title, str)
        # Five headers must be present
        for header in (
            "## Overview",
            "## Key points",
            "## Decisions",
            "## Action items",
            "## People mentioned",
        ):
            assert header in summary, f"Missing header: {header}"
        # Title must be non-empty and not just the filename stem
        assert len(title) > 0
        assert title != "meeting"  # not default stem

    @pytest.mark.asyncio
    async def test_ai_failure_returns_failure(self, db, monkeypatch):
        """Forced AI failure → returns Failure, not an exception."""
        from pipelines.capture import _summarize_upload

        stub = StubProvider(
            response=Failure(error="AI unavailable", recoverable=True, context={})
        )
        monkeypatch.setattr(
            "pipelines.capture.get_provider", lambda task, config: stub
        )

        result = await _summarize_upload(
            text="Some text.",
            db_path=db,
        )
        assert isinstance(result, Failure), f"Expected Failure, got {result}"

    @pytest.mark.asyncio
    async def test_empty_knowledge_base_completes_gracefully(self, db, monkeypatch):
        """Zero knowledge-facts rows → summarizer still returns Success."""
        from pipelines.capture import _summarize_upload

        stub = StubProvider()
        monkeypatch.setattr(
            "pipelines.capture.get_provider", lambda task, config: stub
        )

        # The knowledge_entries table is empty (fresh db) → should not fail
        result = await _summarize_upload(
            text="Meeting content.",
            db_path=db,
        )
        assert isinstance(result, Success), f"Expected Success, got {result}"
        # Verify stub was actually called (graceful degradation worked)
        assert stub.call_count == 1

    @pytest.mark.asyncio
    async def test_uses_capture_task_and_prompt(self, db, monkeypatch):
        """Provider reached via get_provider("capture", ...) and prompt via PROMPTS."""
        from pipelines.capture import _summarize_upload

        captured_task = None
        captured_config = None

        def fake_get_provider(task, config):
            nonlocal captured_task, captured_config
            captured_task = task
            captured_config = config
            return StubProvider()

        monkeypatch.setattr(
            "pipelines.capture.get_provider", fake_get_provider
        )

        result = await _summarize_upload(
            text="Notes here.",
            db_path=db,
        )
        assert isinstance(result, Success)
        assert captured_task == "capture", (
            f"Expected task='capture', got '{captured_task}'"
        )
        assert captured_config is not None
