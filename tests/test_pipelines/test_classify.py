from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.result import Failure, Success
from llm.provider import LLMResponse
from pipelines.classify import ClassifyResult, build_subject, classify


# ---------------------------------------------------------------------------
# Phase 1 tests — ClassifyResult dataclass
# ---------------------------------------------------------------------------


class TestClassifyResult:
    """Phase 1 — ClassifyResult frozen dataclass."""

    def test_constructs_and_fields_accessible(self):
        """All four fields are readable after construction."""
        result = ClassifyResult(
            target_type="project",
            target_name="Alpha",
            confidence=0.9,
            reasoning="Meeting notes.",
        )
        assert result.target_type == "project"
        assert result.target_name == "Alpha"
        assert result.confidence == 0.9
        assert result.reasoning == "Meeting notes."

    def test_is_frozen(self):
        """Assigning to any field after construction raises FrozenInstanceError."""
        from dataclasses import FrozenInstanceError

        result = ClassifyResult(
            target_type="project",
            target_name="Alpha",
            confidence=0.9,
            reasoning="Meeting notes.",
        )
        with pytest.raises(FrozenInstanceError):
            result.target_type = "domain"

    def test_no_validation_on_construction(self):
        """Construction succeeds even with an invalid target_type value.
        Validation is classify()'s job, not the dataclass's.
        """
        result = ClassifyResult(
            target_type="inbox",
            target_name="Somewhere",
            confidence=0.5,
            reasoning="Should not happen but dataclass accepts it.",
        )
        assert result.target_type == "inbox"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> MagicMock:
    """Minimal MainConfig stub for classify() tests."""
    return MagicMock()


def _make_mock_provider(complete_return):
    """Build an AsyncMock provider whose .complete() returns `complete_return`."""
    mock = MagicMock()
    mock.complete = AsyncMock(return_value=complete_return)
    return mock


def _patch_get_provider(monkeypatch, mock_provider):
    """Replace get_provider in the classify module with a lambda returning mock_provider."""
    monkeypatch.setattr(
        "pipelines.classify.get_provider",
        lambda task, config: mock_provider,
    )


VALID_JSON = (
    '{"target_type": "project", "target_name": "Alpha", '
    '"confidence": 0.9, "reasoning": "Meeting notes."}'
)


# ---------------------------------------------------------------------------
# Phase 2 (CIC) tests — build_subject() pure function
# ---------------------------------------------------------------------------


class TestBuildSubject:
    """P2-CIC Phase 2 — build_subject normalises a note into one classify input block."""

    def test_all_fields(self):
        """Title, summary, and tags all present — all appear in output."""
        result = build_subject(
            title="Report Q3",
            summary="Quarterly financials for Q3 2025.",
            tags=["finance", "domain/treasury"],
        )
        assert "Report Q3" in result
        assert "Quarterly financials for Q3 2025." in result
        assert "finance" in result
        assert "domain/treasury" in result

    def test_empty_tags(self):
        """tags=[] — output contains no tags line or graceful placeholder."""
        result = build_subject(
            title="Report Q3",
            summary="Quarterly financials.",
            tags=[],
        )
        assert "Report Q3" in result
        # No stray "Tags:" with nothing after it
        assert "Tags: \n" not in result

    def test_empty_summary(self):
        """summary=\"\" — output omits summary line."""
        result = build_subject(
            title="Report Q3",
            summary="",
            tags=["finance"],
        )
        assert "Report Q3" in result
        # summary empty → no Summary: line
        lines = result.split("\n")
        summary_lines = [ln for ln in lines if ln.startswith("Summary:")]
        assert len(summary_lines) == 0

    def test_none_summary(self):
        """summary=None → no crash, no \"None\" literal in output."""
        result = build_subject(
            title="Report Q3",
            summary=None,
            tags=["finance"],
        )
        assert "Report Q3" in result
        assert "None" not in result

    def test_truncation(self):
        """Long summary (5000+ chars) truncated to avoid blowing prompt budget."""
        long_summary = "x" * 6000
        result = build_subject(
            title="Report Q3",
            summary=long_summary,
            tags=["finance"],
        )
        assert len(result) < 4000  # substantially shorter than 6000


# ---------------------------------------------------------------------------
# Phase 2 tests — classify() async function
# ---------------------------------------------------------------------------


class TestClassify:
    """Phase 2 — classify() pure function (P2-CL-01 through P2-CL-06)."""

    # P2-CL-01 ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_valid_json_returns_success(self, monkeypatch):
        """Mock returns well-formed JSON → Success(ClassifyResult)."""
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content=VALID_JSON, model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            title="Q1 Review",
            summary="Financial overview",
            tags="finance, quarterly",
            valid_destinations="Projects:\n  - Alpha\nDomains:\n  - Finance",
            config=_make_config(),
        )

        assert isinstance(result, Success)
        assert result.value.target_type == "project"
        assert result.value.target_name == "Alpha"
        assert result.value.confidence == 0.9
        assert result.value.reasoning == "Meeting notes."

    # P2-CL-02 ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_provider_failure_returns_retryable(self, monkeypatch):
        """Mock .complete() returns Failure → Failure(recoverable=True)."""
        mock_provider = _make_mock_provider(
            Failure(error="API timeout", recoverable=True, context={})
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            title="Q1 Review",
            summary="Financial overview",
            tags="finance",
            valid_destinations="Projects:\n  - Alpha",
            config=_make_config(),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True
        assert "API timeout" in result.error

    @pytest.mark.asyncio
    async def test_bad_json_returns_retryable(self, monkeypatch):
        """Mock returns Success with non-JSON content → Failure(recoverable=True)."""
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content="Sorry, cannot help", model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            title="Ambiguous note",
            summary="Something confusing",
            tags="misc",
            valid_destinations="Projects:\n  - Alpha",
            config=_make_config(),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True
        assert "JSON parse error" in result.error

    # P2-CL-03 ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_invalid_target_type_returns_retryable(self, monkeypatch):
        """Valid JSON but target_type="inbox" → Failure(recoverable=True)."""
        bad_json = (
            '{"target_type": "inbox", "target_name": "Somewhere", '
            '"confidence": 0.8, "reasoning": "N/A."}'
        )
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content=bad_json, model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            title="Note",
            summary="Summary",
            tags="tag",
            valid_destinations="Projects:\n  - Alpha",
            config=_make_config(),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True
        assert "invalid target_type" in result.error

    # P2-CL-04 ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_never_raises(self, monkeypatch):
        """All failure scenarios — no exception raised."""

        # Scenario 1: Provider failure
        mock_p = _make_mock_provider(
            Failure(error="Network error", recoverable=True, context={})
        )
        _patch_get_provider(monkeypatch, mock_p)

        r1 = await classify(
            title="T1",
            summary="S1",
            tags="t",
            valid_destinations="D",
            config=_make_config(),
        )
        assert isinstance(r1, Failure)

        # Scenario 2: Bad JSON
        mock_p2 = _make_mock_provider(
            Success(LLMResponse(content="garbage", model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_p2)

        r2 = await classify(
            title="T2",
            summary="S2",
            tags="t",
            valid_destinations="D",
            config=_make_config(),
        )
        assert isinstance(r2, Failure)

        # Scenario 3: Invalid target_type
        mock_p3 = _make_mock_provider(
            Success(
                LLMResponse(
                    content='{"target_type": "archive", "target_name": "X", '
                    '"confidence": 0.5, "reasoning": "?"}',
                    model="test",
                    usage={},
                )
            )
        )
        _patch_get_provider(monkeypatch, mock_p3)

        r3 = await classify(
            title="T3",
            summary="S3",
            tags="t",
            valid_destinations="D",
            config=_make_config(),
        )
        assert isinstance(r3, Failure)

        # None raised — test passes by reaching here

    # P2-CL-05 ----------------------------------------------------------------

    def test_no_prompt_in_source(self):
        """Source file contains no classify prompt text hardcoded as string."""
        src = Path(__file__).parent.parent.parent / "src" / "pipelines" / "classify.py"
        text = src.read_text()

        # No f-string or string literal containing classify-specific prompt
        # phrases like "Classify the following inbox note" or routing rules.
        forbidden = [
            "Classify the following inbox note",
            "Routing rules:",
            "Confidence guidance:",
            "Respond with valid JSON only",
        ]
        for phrase in forbidden:
            assert phrase not in text, (
                f"Prompt text found in source: {phrase!r}. "
                f"Prompts must live in YAML files, not Python."
            )

    # P2-CL-06 ----------------------------------------------------------------

    def test_no_direct_ai_import(self):
        """Source file imports only from provider factory, not concrete providers."""
        src = Path(__file__).parent.parent.parent / "src" / "pipelines" / "classify.py"
        text = src.read_text()

        forbidden_imports = [
            "ClaudeProvider",
            "ClaudeCliProvider",
            "OllamaProvider",
            "OpenAIProvider",
            "import anthropic",
            "from anthropic",
            "import ollama",
            "from ollama",
            "import openai",
            "from openai",
        ]
        for name in forbidden_imports:
            assert name not in text, (
                f"Direct AI import found: {name!r}. Use get_provider() factory instead."
            )
