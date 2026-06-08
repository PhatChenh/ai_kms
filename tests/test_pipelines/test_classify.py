from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.result import Failure, Success
from llm.provider import LLMResponse
from pipelines.classify import (
    ClassifyResult,
    build_folder_subject,
    build_subject,
    classify,
)


# ---------------------------------------------------------------------------
# Phase 1 tests — ClassifyResult dataclass
# ---------------------------------------------------------------------------


class TestClassifyResult:
    """Phase 3 — ClassifyResult frozen dataclass (reshaped)."""

    def test_constructs_and_fields_accessible(self):
        """All five fields are readable after construction."""
        result = ClassifyResult(
            project="Alpha",
            domains=["finance"],
            primary_domain="Finance",
            confidence=0.9,
            reasoning="Meeting notes.",
        )
        assert result.project == "Alpha"
        assert result.domains == ["finance"]
        assert result.primary_domain == "Finance"
        assert result.confidence == 0.9
        assert result.reasoning == "Meeting notes."

    def test_is_frozen(self):
        """Assigning to any field after construction raises FrozenInstanceError."""
        from dataclasses import FrozenInstanceError

        result = ClassifyResult(
            project="Alpha",
            domains=["finance"],
            primary_domain="Finance",
            confidence=0.9,
            reasoning="Meeting notes.",
        )
        with pytest.raises(FrozenInstanceError):
            result.project = "Beta"

    def test_no_validation_on_construction(self):
        """Construction succeeds even with invalid values.
        Validation is classify()'s job, not the dataclass's.
        """
        result = ClassifyResult(
            project=None,
            domains=[],
            primary_domain=None,
            confidence=0.5,
            reasoning="Should not happen but dataclass accepts it.",
        )
        assert result.project is None
        assert result.primary_domain is None

    def test_project_none_accepted(self):
        """project=None is a valid state — means no project assignment."""
        result = ClassifyResult(
            project=None,
            domains=["finance"],
            primary_domain="Finance",
            confidence=0.8,
            reasoning="General finance reference, no specific project.",
        )
        assert result.project is None
        assert result.primary_domain == "Finance"
        assert result.domains == ["finance"]

    def test_primary_domain_none_accepted(self):
        """primary_domain=None is a valid state — means no primary domain."""
        result = ClassifyResult(
            project="Alpha",
            domains=[],
            primary_domain=None,
            confidence=0.8,
            reasoning="Project note, no domain context.",
        )
        assert result.project == "Alpha"
        assert result.primary_domain is None


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
    '{"project": "Alpha", "domains": ["finance"], '
    '"primary_domain": "Finance", "confidence": 0.9, '
    '"reasoning": "Meeting notes."}'
)

VALID_DESTINATIONS = "Projects:\n  - Alpha\nDomains:\n  - Finance"


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
# Phase 9 (CIC) tests — build_folder_subject() pure function
# ---------------------------------------------------------------------------


class TestBuildFolderSubject:
    """P2-CIC Phase 9 — build_folder_subject normalises a folder into one classify input block."""

    def test_contains_folder_name_and_file_manifest(self):
        """Output contains both the folder name and the file list."""
        result = build_folder_subject(
            folder_name="Q3 Reports",
            file_manifest="report.pdf\nsummary.md",
        )
        assert "Q3 Reports" in result
        assert "report.pdf" in result
        assert "summary.md" in result

    def test_folder_name_appears_first(self):
        """Folder name line appears before the file list."""
        result = build_folder_subject(
            folder_name="Q3 Reports",
            file_manifest="report.pdf\nsummary.md",
        )
        assert result.startswith("Folder: Q3 Reports")

    def test_empty_file_manifest(self):
        """Empty file list — no crash, folder name still present."""
        result = build_folder_subject(
            folder_name="Empty Folder",
            file_manifest="",
        )
        assert "Empty Folder" in result
        # No crash means test passes

    def test_single_file(self):
        """Single file — no trailing artifacts."""
        result = build_folder_subject(
            folder_name="Solo",
            file_manifest="only.txt",
        )
        assert "Solo" in result
        assert "only.txt" in result

    def test_truncation_long_manifest(self):
        """Very long file manifest is truncated to _MAX_SUBJECT_LENGTH."""
        long_manifest = "\n".join(f"file_{i:04d}.pdf" for i in range(500))
        result = build_folder_subject(
            folder_name="Huge Folder",
            file_manifest=long_manifest,
        )
        # 3000-char limit from classify module
        assert len(result) <= 3100  # small buffer for folder name prefix


# ---------------------------------------------------------------------------
# Phase 3 tests — classify() async function (reshaped)
# ---------------------------------------------------------------------------


class TestClassify:
    """Phase 3 — classify() pure function (P2-CL-01 through P2-CL-06 + new)."""

    # P2-CL-01 / P2-CL-03 -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_valid_json_returns_success(self, monkeypatch):
        """Mock returns well-formed JSON with new fields → Success(ClassifyResult)."""
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content=VALID_JSON, model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            subject="Title: Q1 Review\nSummary: Financial overview\nTags: finance, quarterly",
            valid_destinations=VALID_DESTINATIONS,
            config=_make_config(),
        )

        assert isinstance(result, Success)
        assert result.value.project == "Alpha"
        assert result.value.domains == ["finance"]
        assert result.value.primary_domain == "Finance"
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
            subject="Title: Q1 Review\nSummary: Financial overview\nTags: finance",
            valid_destinations=VALID_DESTINATIONS,
            config=_make_config(),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True
        assert "API timeout" in result.error

    # P2-CL-04 ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_bad_json_returns_retryable(self, monkeypatch):
        """Mock returns Success with non-JSON content → Failure(recoverable=True)."""
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content="Sorry, cannot help", model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            subject="Title: Ambiguous note\nSummary: Something confusing\nTags: misc",
            valid_destinations=VALID_DESTINATIONS,
            config=_make_config(),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True
        assert "JSON parse error" in result.error

    # P2-CL-05 ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_missing_required_fields_returns_retryable(self, monkeypatch):
        """Valid JSON but missing 'domains' field → Failure(recoverable=True)."""
        bad_json = (
            '{"project": "Alpha", "confidence": 0.8, "reasoning": "Missing domains."}'
        )
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content=bad_json, model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            subject="Title: Note\nSummary: Summary\nTags: tag",
            valid_destinations=VALID_DESTINATIONS,
            config=_make_config(),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True
        assert "missing" in result.error.lower()

    # P2-CL-06 ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_template_render_error_returns_unrecoverable(self, monkeypatch):
        """Prompt render raises → Failure(recoverable=False)."""
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content=VALID_JSON, model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        # Replace PROMPTS["classify"] with a mock whose render() raises
        import pipelines.classify as clf

        mock_prompt = MagicMock()
        mock_prompt.render = MagicMock(side_effect=Exception("template syntax error"))
        monkeypatch.setitem(clf.PROMPTS, "classify", mock_prompt)

        result = await classify(
            subject="Title: Test\nSummary: S\nTags: t",
            valid_destinations=VALID_DESTINATIONS,
            config=_make_config(),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is False
        assert "render error" in result.error

    # --------------------------------------------------------------------
    # Existing combined-failure test
    # --------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_never_raises(self, monkeypatch):
        """All failure scenarios — no exception raised."""

        # Scenario 1: Provider failure
        mock_p = _make_mock_provider(
            Failure(error="Network error", recoverable=True, context={})
        )
        _patch_get_provider(monkeypatch, mock_p)

        r1 = await classify(
            subject="Title: T1\nSummary: S1\nTags: t",
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
            subject="Title: T2\nSummary: S2\nTags: t",
            valid_destinations="D",
            config=_make_config(),
        )
        assert isinstance(r2, Failure)

        # Scenario 3: Missing required fields
        mock_p3 = _make_mock_provider(
            Success(
                LLMResponse(
                    content='{"project": "X", "confidence": 0.5, "reasoning": "?"}',
                    model="test",
                    usage={},
                )
            )
        )
        _patch_get_provider(monkeypatch, mock_p3)

        r3 = await classify(
            subject="Title: T3\nSummary: S3\nTags: t",
            valid_destinations="D",
            config=_make_config(),
        )
        assert isinstance(r3, Failure)

        # Scenario 4: project not in valid_destinations
        mock_p4 = _make_mock_provider(
            Success(
                LLMResponse(
                    content='{"project": "UnknownProj", "domains": ["finance"], '
                    '"primary_domain": "Finance", "confidence": 0.7, '
                    '"reasoning": "Guess."}',
                    model="test",
                    usage={},
                )
            )
        )
        _patch_get_provider(monkeypatch, mock_p4)

        r4 = await classify(
            subject="Title: T4\nSummary: S4\nTags: t",
            valid_destinations="Projects:\n  - Alpha",
            config=_make_config(),
        )
        assert isinstance(r4, Failure)

        # None raised — test passes by reaching here

    # --------------------------------------------------------------------
    # Source-code hygiene tests
    # --------------------------------------------------------------------

    def test_no_prompt_in_source(self):
        """Source file contains no classify prompt text hardcoded as string."""
        src = Path(__file__).parent.parent.parent / "src" / "pipelines" / "classify.py"
        text = src.read_text()

        # No f-string or string literal containing classify-specific prompt
        # phrases from the YAML template.
        forbidden = [
            "Classify the following inbox note",
            "Routing rules:",
            "Confidence guidance:",
            "Respond with valid JSON only",
            "available destinations",
        ]
        for phrase in forbidden:
            assert phrase not in text, (
                f"Prompt text found in source: {phrase!r}. "
                f"Prompts must live in YAML files, not Python."
            )

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

    # --------------------------------------------------------------------
    # New Phase 3 tests (7–10)
    # --------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_classify_result_project_none(self, monkeypatch):
        """AI returns project: null → ClassifyResult.project is None."""
        json_with_null_project = (
            '{"project": null, "domains": ["finance"], '
            '"primary_domain": "Finance", "confidence": 0.8, '
            '"reasoning": "General finance reference."}'
        )
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content=json_with_null_project, model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            subject="Title: Finance Overview\nSummary: General finance knowledge\nTags: finance",
            valid_destinations=VALID_DESTINATIONS,
            config=_make_config(),
        )

        assert isinstance(result, Success)
        assert result.value.project is None
        assert result.value.domains == ["finance"]
        assert result.value.primary_domain == "Finance"

    @pytest.mark.asyncio
    async def test_classify_result_validates_project_against_destinations(
        self, monkeypatch
    ):
        """AI returns project NOT in valid_destinations → Failure(recoverable=True)."""
        json_bad_project = (
            '{"project": "GhostProject", "domains": ["finance"], '
            '"primary_domain": "Finance", "confidence": 0.7, '
            '"reasoning": "Made-up project."}'
        )
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content=json_bad_project, model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            subject="Title: Note\nSummary: Some note\nTags: finance",
            valid_destinations=VALID_DESTINATIONS,
            config=_make_config(),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True
        assert "GhostProject" in result.error
        assert "destination" in result.error.lower()

    @pytest.mark.asyncio
    async def test_classify_result_validates_primary_domain(self, monkeypatch):
        """AI returns primary_domain NOT in valid_destinations → Failure(recoverable=True)."""
        json_bad_domain = (
            '{"project": "Alpha", "domains": ["ghost"], '
            '"primary_domain": "GhostDomain", "confidence": 0.7, '
            '"reasoning": "Made-up domain."}'
        )
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content=json_bad_domain, model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            subject="Title: Note\nSummary: Some note\nTags: ghost",
            valid_destinations=VALID_DESTINATIONS,
            config=_make_config(),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True
        assert "GhostDomain" in result.error
        assert "destination" in result.error.lower()

    @pytest.mark.asyncio
    async def test_classify_result_no_project_no_domain(self, monkeypatch):
        """AI returns both null — still Success. CLUELESS routing is caller's job."""
        json_both_null = (
            '{"project": null, "domains": [], '
            '"primary_domain": null, "confidence": 0.3, '
            '"reasoning": "Cannot determine — too vague."}'
        )
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content=json_both_null, model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            subject="Title: Misc\nSummary: Vague note\nTags: unknown",
            valid_destinations=VALID_DESTINATIONS,
            config=_make_config(),
        )

        assert isinstance(result, Success)
        assert result.value.project is None
        assert result.value.domains == []
        assert result.value.primary_domain is None
        assert result.value.confidence == 0.3

    @pytest.mark.asyncio
    async def test_classify_rejects_substring_project(self, monkeypatch):
        """A project name that is only a SUBSTRING of a valid name is rejected.

        "Alph" is a substring of the valid destination "Alpha" but is not an
        exact destination name — validation must use exact membership, not a
        substring `in` test.
        """
        json_substr_project = (
            '{"project": "Alph", "domains": ["finance"], '
            '"primary_domain": "Finance", "confidence": 0.8, '
            '"reasoning": "Substring of a real project."}'
        )
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content=json_substr_project, model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            subject="Title: Note\nSummary: Some note\nTags: finance",
            valid_destinations=VALID_DESTINATIONS,
            config=_make_config(),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True
        assert "Alph" in result.error
        assert "destination" in result.error.lower()

    @pytest.mark.asyncio
    async def test_classify_rejects_substring_primary_domain(self, monkeypatch):
        """A primary_domain that is only a SUBSTRING of a valid name is rejected.

        "inance" is a substring of the valid destination "Finance" but is not
        an exact destination name.
        """
        json_substr_domain = (
            '{"project": "Alpha", "domains": ["finance"], '
            '"primary_domain": "inance", "confidence": 0.8, '
            '"reasoning": "Substring of a real domain."}'
        )
        mock_provider = _make_mock_provider(
            Success(LLMResponse(content=json_substr_domain, model="test", usage={}))
        )
        _patch_get_provider(monkeypatch, mock_provider)

        result = await classify(
            subject="Title: Note\nSummary: Some note\nTags: finance",
            valid_destinations=VALID_DESTINATIONS,
            config=_make_config(),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True
        assert "inance" in result.error
        assert "destination" in result.error.lower()
