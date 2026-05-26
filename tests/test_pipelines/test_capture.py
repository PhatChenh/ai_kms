"""Unit + integration tests for pipelines/capture.py — Phase 2 (core pipeline, .md branch, no rename).

TDD order: _parse_metadata_json → extract → enrich_urls → summarize → metadata → capture_file
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from core.result import Failure, Success
from handlers.base import RawContent


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _make_raw(path: Path, text: str = "Some note content.", *, is_md: bool = True) -> RawContent:
    return RawContent(text=text, source_path=path, is_md=is_md)


# ===========================================================================
# _parse_metadata_json
# ===========================================================================


def test_parse_metadata_json_valid_json():
    from pipelines.capture import _parse_metadata_json

    content = '{"title": "My Note", "tags": ["type/article", "ai", "capture"]}'
    result = _parse_metadata_json(content, source_stem="fallback")

    assert isinstance(result, Success)
    assert result.value["title"] == "My Note"
    assert result.value["tags"] == ["type/article", "ai", "capture"]
    assert "type" not in result.value


def test_parse_metadata_json_fenced_json():
    from pipelines.capture import _parse_metadata_json

    content = "```json\n{\"title\": \"My Note\", \"tags\": [\"type/article\", \"ai\"]}\n```"
    result = _parse_metadata_json(content, source_stem="fallback")

    assert isinstance(result, Success)
    assert result.value["title"] == "My Note"
    assert result.value["tags"] == ["type/article", "ai"]


def test_parse_metadata_json_bad_tags_type_coerced_to_empty():
    from pipelines.capture import _parse_metadata_json

    content = '{"title": "My Note", "tags": "ai"}'
    result = _parse_metadata_json(content, source_stem="fallback")

    assert isinstance(result, Success)
    assert result.value["tags"] == []


def test_parse_metadata_json_missing_title_falls_back_to_stem():
    from pipelines.capture import _parse_metadata_json

    content = '{"tags": ["type/article", "ai"]}'
    result = _parse_metadata_json(content, source_stem="my-note-stem")

    assert isinstance(result, Success)
    assert result.value["title"] == "my-note-stem"


def test_parse_metadata_json_strips_type_key():
    from pipelines.capture import _parse_metadata_json

    content = '{"title": "T", "type": "report", "tags": ["type/report"]}'
    result = _parse_metadata_json(content, source_stem="fallback")

    assert isinstance(result, Success)
    assert "type" not in result.value
    assert result.value["title"] == "T"
    assert result.value["tags"] == ["type/report"]


def test_parse_metadata_json_prose_response_falls_back_to_stem():
    """LLM returns prose (not JSON) → Success with stem title and empty tags."""
    from pipelines.capture import _parse_metadata_json

    prose = (
        'Need full note content. Headings alone ("Q1 performance", "Q2 Performance") '
        "insufficient for metadata extraction.\n\nProvide:\n- Body text / data / findings"
    )
    result = _parse_metadata_json(prose, source_stem="finance")

    assert isinstance(result, Success)
    assert result.value["title"] == "finance"
    assert result.value["tags"] == []


def test_parse_metadata_json_empty_string_falls_back_to_stem():
    """Empty LLM response → Success with stem title and empty tags."""
    from pipelines.capture import _parse_metadata_json

    result = _parse_metadata_json("", source_stem="report")

    assert isinstance(result, Success)
    assert result.value["title"] == "report"
    assert result.value["tags"] == []


# ===========================================================================
# extract_metadata prompt render tests
# ===========================================================================


def test_extract_metadata_prompt_renders_domain_list():
    from llm.prompt_loader import PROMPTS

    sys_str, _user = PROMPTS["extract_metadata"].render(
        text="t", summary="s", domain_list="finance, ops"
    )
    assert "finance, ops" in sys_str


def test_extract_metadata_prompt_instructs_json_on_thin_content():
    """Prompt system string must tell LLM to return JSON even for thin content."""
    from llm.prompt_loader import PROMPTS

    sys_str, _user = PROMPTS["extract_metadata"].render(
        text="t", summary="s", domain_list="finance"
    )
    assert "too thin" in sys_str


def test_extract_metadata_prompt_renders_empty_domain_list():
    from llm.prompt_loader import PROMPTS

    sys_str, _user = PROMPTS["extract_metadata"].render(
        text="t", summary="s", domain_list="(none — no Domain/ folders configured)"
    )
    assert "(none" in sys_str


# ===========================================================================
# extract stage
# ===========================================================================


@pytest.mark.asyncio
async def test_extract_md_returns_raw_content_with_is_md_true(tmp_path):
    from pipelines.capture import extract
    from core.pipeline import PipelineContext

    md_file = tmp_path / "note.md"
    md_file.write_text("# My Note\n\nSome content here.", encoding="utf-8")

    ctx = PipelineContext(config=MagicMock(), correlation_id="test-extract-1")
    result = await extract(md_file, ctx)

    assert isinstance(result, Success)
    assert result.value.is_md is True
    assert result.value.source_path == md_file
    assert "Some content here" in result.value.text


# ===========================================================================
# enrich_urls stage
# ===========================================================================


@pytest.mark.asyncio
async def test_enrich_urls_no_urls_returns_raw_unchanged(vault_root, pipeline_ctx):
    from pipelines.capture import enrich_urls

    md_file = vault_root / "inbox" / "note.md"
    raw = _make_raw(md_file, text="No URLs here, just plain text.")

    result = await enrich_urls(raw, pipeline_ctx)

    assert isinstance(result, Success)
    assert result.value is raw


@pytest.mark.asyncio
async def test_enrich_urls_sparse_text_fetches_and_augments(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import enrich_urls

    sparse_text = "See https://example.com and https://example.org for details."
    raw = _make_raw(vault_root / "inbox" / "note.md", text=sparse_text)

    monkeypatch.setattr(
        "pipelines.capture.fetch_url_content",
        lambda url: Success(f"Fetched content from {url}"),
    )

    result = await enrich_urls(raw, pipeline_ctx)

    assert isinstance(result, Success)
    assert result.value.text != sparse_text
    assert "Referenced URL Content" in result.value.text
    assert "example.com" in result.value.text
    assert result.value.source_path == raw.source_path


@pytest.mark.asyncio
async def test_enrich_urls_dense_text_with_many_urls_skips(vault_root, pipeline_ctx):
    from pipelines.capture import enrich_urls

    # 4 URLs (> max_urls=3) and > 500 chars body — both conditions fail the gate
    body = "A" * 600
    urls = " https://a.com https://b.com https://c.com https://d.com"
    raw = _make_raw(vault_root / "inbox" / "note.md", text=body + urls)

    result = await enrich_urls(raw, pipeline_ctx)

    assert isinstance(result, Success)
    assert result.value is raw


@pytest.mark.asyncio
async def test_enrich_urls_never_returns_failure_when_fetches_fail(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import enrich_urls

    sparse_text = "See https://example.com and https://example.org here."
    raw = _make_raw(vault_root / "inbox" / "note.md", text=sparse_text)

    monkeypatch.setattr(
        "pipelines.capture.fetch_url_content",
        lambda url: Failure(error="fetch failed", recoverable=True, context={}),
    )

    result = await enrich_urls(raw, pipeline_ctx)

    assert isinstance(result, Success)
    assert result.value is raw  # original returned when all fetches fail


# ===========================================================================
# summarize stage
# ===========================================================================


@pytest.mark.asyncio
async def test_summarize_returns_summarize_result_with_non_empty_summary(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import summarize, SummarizeResult
    from llm.provider import LLMResponse

    raw = _make_raw(vault_root / "inbox" / "note.md", text="This is the note body.")

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(
        LLMResponse(content="  This is the AI summary.  ", model="test-model", usage={})
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await summarize(raw, pipeline_ctx)

    assert isinstance(result, Success)
    assert isinstance(result.value, SummarizeResult)
    assert result.value.summary == "This is the AI summary."  # stripped
    assert result.value.raw is raw


# ===========================================================================
# metadata stage
# ===========================================================================


@pytest.mark.asyncio
async def test_metadata_writes_audit_log_row(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import metadata, MetadataResult, SummarizeResult
    from llm.provider import LLMResponse
    from storage.audit_log import query

    md_file = vault_root / "inbox" / "note.md"
    md_file.write_text("# Note\n\nContent.", encoding="utf-8")
    raw = _make_raw(md_file, text="Content.")
    sr = SummarizeResult(raw=raw, summary="Test summary.")

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(
        LLMResponse(
            content='{"title": "My Note", "type": "note", "tags": ["test"]}',
            model="test-model",
            usage={},
        )
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await metadata(sr, pipeline_ctx)

    assert isinstance(result, Success)
    assert isinstance(result.value, MetadataResult)
    assert result.value.ai_title == "My Note"
    assert result.value.ai_tags == ["test"]

    # Verify one audit_log row written
    entries = query(pipeline="capture", db_path=pipeline_ctx.db_path)
    assert isinstance(entries, Success)
    assert len(entries.value) == 1
    row = entries.value[0]
    assert row.stage == "metadata"
    assert row.outcome == "CAPTURED"
    assert row.pipeline == "capture"


@pytest.mark.asyncio
async def test_metadata_ai_type_derived_from_type_tag(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import metadata, MetadataResult, SummarizeResult
    from llm.provider import LLMResponse

    md_file = vault_root / "inbox" / "note.md"
    md_file.write_text("# Note\n\nContent.", encoding="utf-8")
    sr = SummarizeResult(raw=_make_raw(md_file), summary="Summary.")

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(
        LLMResponse(content='{"title": "Note", "tags": ["type/report", "quarterly"]}', model="test", usage={})
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await metadata(sr, pipeline_ctx)

    assert isinstance(result, Success)
    assert result.value.ai_type == "report"


@pytest.mark.asyncio
async def test_metadata_ai_type_none_when_no_type_tag(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import metadata, SummarizeResult
    from llm.provider import LLMResponse

    md_file = vault_root / "inbox" / "note.md"
    md_file.write_text("# Note\n\nContent.", encoding="utf-8")
    sr = SummarizeResult(raw=_make_raw(md_file), summary="Summary.")

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(
        LLMResponse(content='{"title": "Note", "tags": ["quarterly"]}', model="test", usage={})
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await metadata(sr, pipeline_ctx)

    assert isinstance(result, Success)
    assert result.value.ai_type is None


@pytest.mark.asyncio
async def test_metadata_ai_domain_derived_from_domain_tag(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import metadata, SummarizeResult
    from llm.provider import LLMResponse
    from core.tags import TagTaxonomy
    from core.pipeline import PipelineContext

    md_file = vault_root / "inbox" / "note.md"
    md_file.write_text("# Note\n\nContent.", encoding="utf-8")
    sr = SummarizeResult(raw=_make_raw(md_file), summary="Summary.")

    taxonomy = TagTaxonomy(
        allowed_types=frozenset(["report"]),
        valid_domains=frozenset(["finance"]),
    )
    ctx = PipelineContext(
        config=pipeline_ctx.config,
        correlation_id=pipeline_ctx.correlation_id,
        db_path=pipeline_ctx.db_path,
        taxonomy=taxonomy,
    )

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(
        LLMResponse(
            content='{"title": "Note", "tags": ["type/report", "domain/finance", "quarterly"]}',
            model="test",
            usage={},
        )
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await metadata(sr, ctx)

    assert isinstance(result, Success)
    assert result.value.ai_domain == "finance"


@pytest.mark.asyncio
async def test_metadata_ai_domain_none_when_no_domain_tag(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import metadata, SummarizeResult
    from llm.provider import LLMResponse

    md_file = vault_root / "inbox" / "note.md"
    md_file.write_text("# Note\n\nContent.", encoding="utf-8")
    sr = SummarizeResult(raw=_make_raw(md_file), summary="Summary.")

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(
        LLMResponse(content='{"title": "Note", "tags": ["type/report", "quarterly"]}', model="test", usage={})
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await metadata(sr, pipeline_ctx)

    assert isinstance(result, Success)
    assert result.value.ai_domain is None


@pytest.mark.asyncio
async def test_metadata_taxonomy_none_skips_validation(vault_root, pipeline_ctx, monkeypatch):
    """With taxonomy=None, tags stored as-is without validation."""
    from pipelines.capture import metadata, SummarizeResult
    from llm.provider import LLMResponse

    md_file = vault_root / "inbox" / "note.md"
    md_file.write_text("# Note\n\nContent.", encoding="utf-8")
    sr = SummarizeResult(raw=_make_raw(md_file), summary="Summary.")

    # pipeline_ctx has taxonomy=None by default
    assert pipeline_ctx.taxonomy is None

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(
        LLMResponse(
            content='{"title": "Note", "tags": ["invalid/tag", "free-tag"]}',
            model="test",
            usage={},
        )
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await metadata(sr, pipeline_ctx)

    assert isinstance(result, Success)
    # Tags stored as-is — no violation filtering
    assert "invalid/tag" in result.value.ai_tags


@pytest.mark.asyncio
async def test_metadata_with_violations_writes_two_audit_rows(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import metadata, SummarizeResult
    from llm.provider import LLMResponse
    from storage.audit_log import query
    from core.tags import TagTaxonomy
    from core.pipeline import PipelineContext

    md_file = vault_root / "inbox" / "note.md"
    md_file.write_text("# Note\n\nContent.", encoding="utf-8")
    sr = SummarizeResult(raw=_make_raw(md_file), summary="Summary.")

    taxonomy = TagTaxonomy(
        allowed_types=frozenset(["report"]),
        valid_domains=frozenset(["finance"]),
    )
    ctx = PipelineContext(
        config=pipeline_ctx.config,
        correlation_id=pipeline_ctx.correlation_id,
        db_path=pipeline_ctx.db_path,
        taxonomy=taxonomy,
    )

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(
        LLMResponse(
            content='{"title": "Note", "tags": ["type/bad-type", "quarterly"]}',
            model="test",
            usage={},
        )
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await metadata(sr, ctx)

    assert isinstance(result, Success)

    entries = query(pipeline="capture", db_path=ctx.db_path)
    assert isinstance(entries, Success)
    assert len(entries.value) == 2
    outcomes = {e.outcome for e in entries.value}
    assert "CAPTURED" in outcomes
    assert "TAG_VIOLATION" in outcomes


@pytest.mark.asyncio
async def test_metadata_zero_violations_writes_one_audit_row(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import metadata, SummarizeResult
    from llm.provider import LLMResponse
    from storage.audit_log import query
    from core.tags import TagTaxonomy
    from core.pipeline import PipelineContext

    md_file = vault_root / "inbox" / "note.md"
    md_file.write_text("# Note\n\nContent.", encoding="utf-8")
    sr = SummarizeResult(raw=_make_raw(md_file), summary="Summary.")

    taxonomy = TagTaxonomy(
        allowed_types=frozenset(["report"]),
        valid_domains=frozenset(["finance"]),
    )
    ctx = PipelineContext(
        config=pipeline_ctx.config,
        correlation_id=pipeline_ctx.correlation_id,
        db_path=pipeline_ctx.db_path,
        taxonomy=taxonomy,
    )

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(
        LLMResponse(
            content='{"title": "Note", "tags": ["type/report", "domain/finance", "quarterly"]}',
            model="test",
            usage={},
        )
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await metadata(sr, ctx)

    assert isinstance(result, Success)
    entries = query(pipeline="capture", db_path=ctx.db_path)
    assert isinstance(entries, Success)
    assert len(entries.value) == 1
    assert entries.value[0].outcome == "CAPTURED"


@pytest.mark.asyncio
async def test_metadata_tag_violation_audit_failure_is_nonfatal(vault_root, pipeline_ctx, monkeypatch):
    """TAG_VIOLATION audit write failure must not abort the pipeline."""
    from pipelines.capture import metadata, SummarizeResult
    from llm.provider import LLMResponse
    from core.tags import TagTaxonomy
    from core.pipeline import PipelineContext
    import core.audit as audit_mod

    md_file = vault_root / "inbox" / "note.md"
    md_file.write_text("# Note\n\nContent.", encoding="utf-8")
    sr = SummarizeResult(raw=_make_raw(md_file), summary="Summary.")

    taxonomy = TagTaxonomy(
        allowed_types=frozenset(["report"]),
        valid_domains=frozenset(["finance"]),
    )
    ctx = PipelineContext(
        config=pipeline_ctx.config,
        correlation_id=pipeline_ctx.correlation_id,
        db_path=pipeline_ctx.db_path,
        taxonomy=taxonomy,
    )

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(
        LLMResponse(
            content='{"title": "Note", "tags": ["type/bad-type", "quarterly"]}',
            model="test",
            usage={},
        )
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    write_call_count = [0]
    original_write = audit_mod.write

    def failing_second_write(decision, pipeline, stage, outcome, db_path):
        write_call_count[0] += 1
        if write_call_count[0] == 2:
            return Failure(error="audit db full", recoverable=False, context={})
        return original_write(decision, pipeline=pipeline, stage=stage, outcome=outcome, db_path=db_path)

    monkeypatch.setattr("pipelines.capture.audit.write", failing_second_write)

    result = await metadata(sr, ctx)

    # Pipeline continues and returns Success despite TAG_VIOLATION audit failure
    assert isinstance(result, Success)


# ===========================================================================
# capture_file end-to-end
# ===========================================================================


@pytest.mark.asyncio
async def test_capture_file_md_end_to_end_returns_write_outcome(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import capture_file
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse
    from unittest.mock import MagicMock

    md_file = vault_root / "inbox" / "test-note.md"
    md_file.write_text("# Test Note\n\nThis is the body.", encoding="utf-8")

    # Stability gate: make file appear 120s old (cooldown=60s)
    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="AI-generated summary.", model="test", usage={})),
        Success(LLMResponse(
            # title matches stem → no rename; vault_path stays test-note.md
            content='{"title": "test-note", "type": "note", "tags": ["test"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success)
    assert isinstance(result.value, WriteOutcome)
    assert result.value.vault_path == "inbox/test-note.md"
    assert result.value.metadata.summary == "AI-generated summary."


@pytest.mark.asyncio
async def test_capture_file_domain_written_to_note_metadata(vault_root, pipeline_ctx, monkeypatch):
    """NoteMetadata.domain in written note matches ai_domain derived from domain/ tag."""
    from pipelines.capture import capture_file
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse
    from unittest.mock import MagicMock
    from core.tags import TagTaxonomy
    from core.pipeline import PipelineContext

    md_file = vault_root / "inbox" / "domain-note.md"
    md_file.write_text("# Domain Note\n\nContent.", encoding="utf-8")

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    taxonomy = TagTaxonomy(
        allowed_types=frozenset(["report", "article", "meeting-note", "email", "reflection", "task-list", "transcript", "capture"]),
        valid_domains=frozenset(["finance"]),
    )
    ctx = PipelineContext(
        config=pipeline_ctx.config,
        correlation_id=pipeline_ctx.correlation_id,
        db_path=pipeline_ctx.db_path,
        taxonomy=taxonomy,
    )

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Domain note summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "domain-note", "tags": ["type/report", "domain/finance", "quarterly"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await capture_file(md_file, context=ctx)

    assert isinstance(result, Success)
    assert isinstance(result.value, WriteOutcome)
    assert result.value.metadata.domain == "finance"


@pytest.mark.asyncio
async def test_capture_file_with_explicit_context_does_not_scan_domain_folder(
    vault_root, pipeline_ctx, monkeypatch
):
    """capture_file(path, context=explicit_ctx) must not call load_valid_domains."""
    from pipelines.capture import capture_file
    from llm.provider import LLMResponse
    from unittest.mock import MagicMock

    md_file = vault_root / "inbox" / "domain-test.md"
    md_file.write_text("# Domain Scan Test\n\nContent.", encoding="utf-8")

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "domain-test", "type": "note", "tags": ["test"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    # Track whether load_valid_domains is called
    scan_called = False

    def mock_load_valid_domains(root: Path) -> frozenset:
        nonlocal scan_called
        scan_called = True
        return frozenset()

    monkeypatch.setattr("vault.paths.load_valid_domains", mock_load_valid_domains)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success)
    assert scan_called is False, "load_valid_domains must NOT be called when context is provided"
