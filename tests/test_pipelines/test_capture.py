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
    assert "domain/finance" in result.value.metadata.tags


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


# ===========================================================================
# Phase 1 — FILE_LOST guard
# ===========================================================================


@pytest.mark.asyncio
async def test_capture_file_entry_guard_fires_when_file_not_found(
    vault_root, pipeline_ctx, monkeypatch
):
    """path.stat() raises FileNotFoundError → entry guard fires, Failure(recoverable=True) returned, FILE_LOST audit written."""
    from pipelines.capture import capture_file
    from storage.audit_log import query

    md_file = vault_root / "inbox" / "ghost.md"
    # File does NOT exist on disk

    run_pipeline_called = []

    async def mock_run_pipeline(*args, **kwargs):
        run_pipeline_called.append(True)
        return Failure(error="should not be called", recoverable=False, context={})

    monkeypatch.setattr("pipelines.capture.run_pipeline", mock_run_pipeline)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Failure)
    assert result.recoverable is True
    assert len(run_pipeline_called) == 0, "pipeline must not run when file not found at entry"

    entries = query(pipeline="capture", db_path=pipeline_ctx.db_path)
    assert isinstance(entries, Success)
    file_lost = [e for e in entries.value if e.outcome == "FILE_LOST"]
    assert len(file_lost) == 1
    assert file_lost[0].stage == "entry"


@pytest.mark.asyncio
async def test_store_guard_fires_when_file_gone_during_pipeline(
    vault_root, pipeline_ctx, monkeypatch
):
    """store() guard: source_path gone → Failure(recoverable=False), FILE_LOST audit at stage='store', no vault write."""
    from pipelines.capture import store, MetadataResult
    from storage.audit_log import query
    from core.confidence import AIDecision as _AIDecision

    # Path that never exists on disk
    gone_path = vault_root / "inbox" / "gone.md"

    mr = MetadataResult(
        raw=_make_raw(gone_path),
        summary="Summary.",
        ai_title="Gone",
        ai_type="note",
        ai_domain=None,
        ai_tags=[],
        decision=_AIDecision(action="auto", confidence=0.9, reasoning="ok", source_ids=[]),
    )

    write_note_called = []
    monkeypatch.setattr("pipelines.capture.write_note", lambda *a, **kw: write_note_called.append(True))

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert len(write_note_called) == 0

    entries = query(pipeline="capture", db_path=pipeline_ctx.db_path)
    assert isinstance(entries, Success)
    file_lost = [e for e in entries.value if e.outcome == "FILE_LOST"]
    assert len(file_lost) == 1
    assert file_lost[0].stage == "store"


@pytest.mark.asyncio
async def test_store_guard_no_documents_row_inserted(
    vault_root, pipeline_ctx
):
    """store() guard fires → no documents row inserted into DB."""
    from pipelines.capture import store, MetadataResult
    from core.confidence import AIDecision as _AIDecision
    import storage.documents as docs_mod

    gone_path = vault_root / "inbox" / "gone2.md"

    mr = MetadataResult(
        raw=_make_raw(gone_path),
        summary="Summary.",
        ai_title="Gone2",
        ai_type="note",
        ai_domain=None,
        ai_tags=[],
        decision=_AIDecision(action="auto", confidence=0.9, reasoning="ok", source_ids=[]),
    )

    await store(mr, pipeline_ctx)

    rows = docs_mod.all_paths(db_path=pipeline_ctx.db_path)
    assert isinstance(rows, Success)
    assert len(rows.value) == 0


# ===========================================================================
# apply_location_tags stage
# ===========================================================================


def _make_mr(
    source_path: Path,
    ai_tags: list[str] | None = None,
    ai_project: str | None = None,
) -> "MetadataResult":
    """Build a minimal MetadataResult for apply_location_tags tests."""
    from pipelines.capture import MetadataResult
    from core.confidence import AIDecision

    return MetadataResult(
        raw=_make_raw(source_path),
        summary="Summary.",
        ai_title="Title",
        ai_type=None,
        ai_domain=None,
        ai_tags=ai_tags if ai_tags is not None else [],
        decision=AIDecision(action="capture:metadata", confidence=0.9, reasoning="ok", source_ids=[]),
        ai_project=ai_project,
    )


def _make_taxonomy_ctx(pipeline_ctx, valid_domains: frozenset[str]):
    """Return a PipelineContext with a real TagTaxonomy."""
    from core.tags import TagTaxonomy
    from core.pipeline import PipelineContext

    taxonomy = TagTaxonomy(
        allowed_types=frozenset(["report"]),
        valid_domains=valid_domains,
    )
    return PipelineContext(
        config=pipeline_ctx.config,
        correlation_id=pipeline_ctx.correlation_id,
        db_path=pipeline_ctx.db_path,
        taxonomy=taxonomy,
    )


@pytest.mark.asyncio
async def test_apply_location_tags_domain_file_adds_tag(vault_root, pipeline_ctx):
    """File under Domain/Engineering/ → domain/Engineering tag appended."""
    from pipelines.capture import apply_location_tags

    ctx = _make_taxonomy_ctx(pipeline_ctx, frozenset(["Engineering"]))
    path = vault_root / "Domain" / "Engineering" / "note.md"
    mr = _make_mr(path, ai_tags=["type/report"])

    result = await apply_location_tags(mr, ctx)

    assert isinstance(result, Success)
    assert "domain/Engineering" in result.value.ai_tags
    assert "type/report" in result.value.ai_tags  # existing tag preserved
    assert result.value.ai_domain == "Engineering"


@pytest.mark.asyncio
async def test_apply_location_tags_domain_file_no_duplicate_tag(vault_root, pipeline_ctx):
    """File already tagged domain/Engineering → no duplicate added."""
    from pipelines.capture import apply_location_tags

    ctx = _make_taxonomy_ctx(pipeline_ctx, frozenset(["Engineering"]))
    path = vault_root / "Domain" / "Engineering" / "note.md"
    mr = _make_mr(path, ai_tags=["domain/Engineering", "type/report"])

    result = await apply_location_tags(mr, ctx)

    assert isinstance(result, Success)
    assert result.value.ai_tags.count("domain/Engineering") == 1


@pytest.mark.asyncio
async def test_apply_location_tags_invalid_domain_skips_tag(vault_root, pipeline_ctx, monkeypatch):
    """Domain folder not in valid_domains → tag NOT added, result still Success."""
    from pipelines.capture import apply_location_tags

    warning_calls: list[tuple] = []
    monkeypatch.setattr("pipelines.capture.logger.warning", lambda *args, **kwargs: warning_calls.append(args))

    ctx = _make_taxonomy_ctx(pipeline_ctx, frozenset(["Finance"]))  # Engineering not valid
    path = vault_root / "Domain" / "Engineering" / "note.md"
    mr = _make_mr(path, ai_tags=["type/report"])

    result = await apply_location_tags(mr, ctx)

    assert isinstance(result, Success)
    assert "domain/Engineering" not in result.value.ai_tags
    # Existing tags preserved unmodified
    assert result.value.ai_tags == ["type/report"]
    # Warning must be emitted for the invalid domain
    assert len(warning_calls) == 1
    assert warning_calls[0][0] == "apply_location_tags.invalid_domain"


@pytest.mark.asyncio
async def test_apply_location_tags_project_file_sets_ai_project(vault_root, pipeline_ctx):
    """File under Projects/Alpha/ → ai_project set, no domain tag added."""
    from pipelines.capture import apply_location_tags

    ctx = _make_taxonomy_ctx(pipeline_ctx, frozenset(["Engineering"]))
    path = vault_root / "Projects" / "Alpha" / "note.md"
    mr = _make_mr(path, ai_tags=["type/report"])

    result = await apply_location_tags(mr, ctx)

    assert isinstance(result, Success)
    assert result.value.ai_project == "Alpha"
    # No domain tag added for project files
    assert not any(t.startswith("domain/") for t in result.value.ai_tags)


@pytest.mark.asyncio
async def test_apply_location_tags_inbox_file_no_changes(vault_root, pipeline_ctx):
    """File under inbox/ → no ai_tags or ai_project changes."""
    from pipelines.capture import apply_location_tags

    ctx = _make_taxonomy_ctx(pipeline_ctx, frozenset(["Engineering"]))
    path = vault_root / "inbox" / "note.md"
    original_tags = ["type/report", "quarterly"]
    mr = _make_mr(path, ai_tags=list(original_tags))

    result = await apply_location_tags(mr, ctx)

    assert isinstance(result, Success)
    assert result.value.ai_tags == original_tags
    assert result.value.ai_project is None


@pytest.mark.asyncio
async def test_apply_location_tags_taxonomy_none_skips_domain_tag(vault_root, pipeline_ctx):
    """taxonomy=None → treat as no valid_domains → skip domain tag for domain file."""
    from pipelines.capture import apply_location_tags

    # pipeline_ctx has taxonomy=None by default
    assert pipeline_ctx.taxonomy is None

    path = vault_root / "Domain" / "Engineering" / "note.md"
    mr = _make_mr(path, ai_tags=["type/report"])

    result = await apply_location_tags(mr, pipeline_ctx)

    assert isinstance(result, Success)
    assert "domain/Engineering" not in result.value.ai_tags


@pytest.mark.asyncio
async def test_audit_file_lost_fails_silently(vault_root, pipeline_ctx, monkeypatch):
    """_audit_file_lost audit write failure must not propagate — Failure still returned."""
    from pipelines.capture import capture_file

    md_file = vault_root / "inbox" / "ghost2.md"
    # File does not exist

    monkeypatch.setattr("pipelines.capture.audit.write", lambda *a, **kw: Failure(
        error="audit db unavailable", recoverable=False, context={}
    ))

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Failure)
    assert result.recoverable is True


# ===========================================================================
# Phase 6 — Idempotent Capture
# ===========================================================================


@pytest.mark.asyncio
async def test_idempotent_md_unchanged_returns_skipped(vault_root, pipeline_ctx, monkeypatch):
    """.md with hash matching DB → SKIPPED audit written, run_pipeline NOT called, Success returned."""
    from pipelines.capture import capture_file
    from vault.writer import WriteOutcome, write_note
    from vault.frontmatter import NoteMetadata
    from vault.reader import read_note
    import storage.documents as docs_mod
    from unittest.mock import MagicMock
    from storage.audit_log import query

    md_file = vault_root / "inbox" / "unchanged-note.md"
    md_file.write_text("# Unchanged\n\nThis body will not change.", encoding="utf-8")

    # Seed DB: write note then upsert into documents table with its current content_hash
    meta = NoteMetadata(type="note")
    write_note(md_file, "This body will not change.", meta, actor="ai")
    note_result = read_note(md_file)
    assert isinstance(note_result, Success)
    note = note_result.value
    upsert_outcome = WriteOutcome(
        vault_path="inbox/unchanged-note.md",
        absolute_path=md_file,
        content_hash=note.content_hash,
        metadata=note.metadata,
    )
    docs_mod.upsert(upsert_outcome, db_path=pipeline_ctx.db_path)

    # Make file appear old enough to pass cooldown
    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    run_pipeline_called = []

    async def mock_run_pipeline(*args, **kwargs):
        run_pipeline_called.append(True)
        return Failure(error="should not be called", recoverable=False, context={})

    monkeypatch.setattr("pipelines.capture.run_pipeline", mock_run_pipeline)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success)
    assert isinstance(result.value, WriteOutcome)
    assert result.value.vault_path == "inbox/unchanged-note.md"
    assert len(run_pipeline_called) == 0, "pipeline must NOT run when content hash matches DB"

    # SKIPPED audit row written
    entries = query(pipeline="capture", db_path=pipeline_ctx.db_path)
    assert isinstance(entries, Success)
    skipped = [e for e in entries.value if e.outcome == "SKIPPED"]
    assert len(skipped) == 1
    assert skipped[0].stage == "entry"


@pytest.mark.asyncio
async def test_idempotent_md_edited_runs_pipeline(vault_root, pipeline_ctx, monkeypatch):
    """.md with hash differing from DB → pipeline runs normally."""
    from pipelines.capture import capture_file
    from vault.writer import WriteOutcome
    from vault.reader import read_note
    import storage.documents as docs_mod
    from unittest.mock import MagicMock

    md_file = vault_root / "inbox" / "edited-note.md"
    md_file.write_text("# Old Content\n\nOld body.", encoding="utf-8")

    # Seed DB with OLD body hash
    note_result = read_note(md_file)
    assert isinstance(note_result, Success)
    upsert_outcome = WriteOutcome(
        vault_path="inbox/edited-note.md",
        absolute_path=md_file,
        content_hash=note_result.value.content_hash,
        metadata=note_result.value.metadata,
    )
    docs_mod.upsert(upsert_outcome, db_path=pipeline_ctx.db_path)

    # Now change the file content — DB hash is stale
    md_file.write_text("# New Content\n\nNew body that changed.", encoding="utf-8")

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    run_pipeline_called = []

    async def tracking_run_pipeline(*args, **kwargs):
        run_pipeline_called.append(True)
        return Failure(error="pipeline ran (expected)", recoverable=False, context={})

    monkeypatch.setattr("pipelines.capture.run_pipeline", tracking_run_pipeline)

    await capture_file(md_file, context=pipeline_ctx)

    # Pipeline was called (content changed → not skipped)
    assert len(run_pipeline_called) == 1, "pipeline MUST run when content hash differs"


@pytest.mark.asyncio
async def test_idempotent_md_not_in_db_runs_pipeline(vault_root, pipeline_ctx, monkeypatch):
    """.md not yet in DB (first capture) → pipeline runs normally."""
    from pipelines.capture import capture_file
    from unittest.mock import MagicMock

    md_file = vault_root / "inbox" / "new-note.md"
    md_file.write_text("# Brand New\n\nNever captured before.", encoding="utf-8")

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    run_pipeline_called = []

    async def tracking_run_pipeline(*args, **kwargs):
        run_pipeline_called.append(True)
        return Failure(error="pipeline ran (expected)", recoverable=False, context={})

    monkeypatch.setattr("pipelines.capture.run_pipeline", tracking_run_pipeline)

    await capture_file(md_file, context=pipeline_ctx)

    assert len(run_pipeline_called) == 1, "pipeline MUST run when file is not in DB"


@pytest.mark.asyncio
async def test_idempotent_binary_matching_source_hash_skipped(vault_root, pipeline_ctx, monkeypatch):
    """Binary with sibling whose source_hash matches → SKIPPED, pipeline not called."""
    from pipelines.capture import capture_file
    from vault.writer import WriteOutcome, write_note
    from vault.frontmatter import NoteMetadata
    from unittest.mock import MagicMock
    from storage.audit_log import query
    import hashlib

    # Create a binary in inbox
    binary_file = vault_root / "inbox" / "report.pdf"
    binary_file.write_bytes(b"PDF binary content here")

    # Create sibling with source_hash matching the binary
    source_hash = hashlib.sha256(b"PDF binary content here").hexdigest()
    summaries_dir = vault_root / "inbox" / ".summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    sibling_path = summaries_dir / "report.pdf.md"

    sibling_meta = NoteMetadata(
        type="attachment-summary",
        attachment_path="inbox/report.pdf",
        source_hash=source_hash,
        tags=["type/attachment-summary"],
    )
    write_note(sibling_path, "Existing sibling body.", sibling_meta, actor="ai")

    # Make file appear old enough
    mtime = binary_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    run_pipeline_called = []

    async def mock_run_pipeline(*args, **kwargs):
        run_pipeline_called.append(True)
        return Failure(error="should not be called", recoverable=False, context={})

    monkeypatch.setattr("pipelines.capture.run_pipeline", mock_run_pipeline)

    result = await capture_file(binary_file, context=pipeline_ctx)

    assert isinstance(result, Success)
    assert len(run_pipeline_called) == 0, "pipeline must NOT run when source_hash matches"
    assert result.value.vault_path == "inbox/.summaries/report.pdf.md"

    entries = query(pipeline="capture", db_path=pipeline_ctx.db_path)
    assert isinstance(entries, Success)
    skipped = [e for e in entries.value if e.outcome == "SKIPPED"]
    assert len(skipped) == 1


@pytest.mark.asyncio
async def test_idempotent_binary_differing_source_hash_runs_pipeline(vault_root, pipeline_ctx, monkeypatch):
    """Binary with sibling whose source_hash differs → pipeline runs normally."""
    from pipelines.capture import capture_file
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    from unittest.mock import MagicMock
    import hashlib

    binary_file = vault_root / "inbox" / "changed.pdf"
    binary_file.write_bytes(b"Updated PDF content")

    # Create sibling with STALE source_hash
    stale_hash = hashlib.sha256(b"Old PDF content").hexdigest()
    summaries_dir = vault_root / "inbox" / ".summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    sibling_path = summaries_dir / "changed.pdf.md"

    sibling_meta = NoteMetadata(
        type="attachment-summary",
        attachment_path="inbox/changed.pdf",
        source_hash=stale_hash,
        tags=["type/attachment-summary"],
    )
    write_note(sibling_path, "Old sibling body.", sibling_meta, actor="ai")

    mtime = binary_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    run_pipeline_called = []

    async def tracking_run_pipeline(*args, **kwargs):
        run_pipeline_called.append(True)
        return Failure(error="pipeline ran (expected)", recoverable=False, context={})

    monkeypatch.setattr("pipelines.capture.run_pipeline", tracking_run_pipeline)

    await capture_file(binary_file, context=pipeline_ctx)

    assert len(run_pipeline_called) == 1, "pipeline MUST run when source_hash differs"


@pytest.mark.asyncio
async def test_idempotent_binary_no_sibling_runs_pipeline(vault_root, pipeline_ctx, monkeypatch):
    """Binary with no sibling at all → first capture, pipeline runs."""
    from pipelines.capture import capture_file
    from unittest.mock import MagicMock

    binary_file = vault_root / "inbox" / "fresh.pdf"
    binary_file.write_bytes(b"Fresh PDF content")

    mtime = binary_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    run_pipeline_called = []

    async def tracking_run_pipeline(*args, **kwargs):
        run_pipeline_called.append(True)
        return Failure(error="pipeline ran (expected)", recoverable=False, context={})

    monkeypatch.setattr("pipelines.capture.run_pipeline", tracking_run_pipeline)

    await capture_file(binary_file, context=pipeline_ctx)

    assert len(run_pipeline_called) == 1, "pipeline MUST run when no sibling exists"


@pytest.mark.asyncio
async def test_audit_skipped_fails_silently_success_still_returned(vault_root, pipeline_ctx, monkeypatch):
    """_audit_skipped failure must not abort the SKIPPED path — Success still returned."""
    from pipelines.capture import capture_file
    from vault.writer import WriteOutcome, write_note
    from vault.frontmatter import NoteMetadata
    from vault.reader import read_note
    import storage.documents as docs_mod
    from unittest.mock import MagicMock

    md_file = vault_root / "inbox" / "audit-fail-test.md"
    md_file.write_text("# Audit Fail Test\n\nBody content.", encoding="utf-8")

    # Seed DB
    meta = NoteMetadata(type="note")
    write_note(md_file, "Body content.", meta, actor="ai")
    note_result = read_note(md_file)
    assert isinstance(note_result, Success)
    upsert_outcome = WriteOutcome(
        vault_path="inbox/audit-fail-test.md",
        absolute_path=md_file,
        content_hash=note_result.value.content_hash,
        metadata=note_result.value.metadata,
    )
    docs_mod.upsert(upsert_outcome, db_path=pipeline_ctx.db_path)

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    # Make audit.write always fail
    monkeypatch.setattr("pipelines.capture.audit.write", lambda *a, **kw: Failure(
        error="audit db unavailable", recoverable=False, context={}
    ))

    run_pipeline_called = []

    async def mock_run_pipeline(*args, **kwargs):
        run_pipeline_called.append(True)
        return Failure(error="should not run", recoverable=False, context={})

    monkeypatch.setattr("pipelines.capture.run_pipeline", mock_run_pipeline)

    result = await capture_file(md_file, context=pipeline_ctx)

    # Should still return Success SKIPPED despite audit failure
    assert isinstance(result, Success)
    assert len(run_pipeline_called) == 0


@pytest.mark.asyncio
async def test_idempotent_located_binary_matching_hash_skipped(vault_root, pipeline_ctx, monkeypatch):
    """LOCATED binary (Projects/Alpha/attachment/) with sibling at .summaries/ whose source_hash matches → SKIPPED, pipeline not called."""
    from pipelines.capture import capture_file
    from vault.writer import WriteOutcome, write_note
    from vault.frontmatter import NoteMetadata
    from unittest.mock import MagicMock
    import hashlib

    # Binary at a LOCATED path (already in attachment/)
    att_dir = vault_root / "Projects" / "Alpha" / "attachment"
    att_dir.mkdir(parents=True, exist_ok=True)
    binary_file = att_dir / "report.pdf"
    binary_bytes = b"LOCATED PDF binary content"
    binary_file.write_bytes(binary_bytes)

    # Sibling at Projects/Alpha/attachment/.summaries/report.pdf.md
    summaries_dir = att_dir / ".summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    sibling_path = summaries_dir / "report.pdf.md"
    source_hash = hashlib.sha256(binary_bytes).hexdigest()

    sibling_meta = NoteMetadata(
        type="attachment-summary",
        attachment_path="Projects/Alpha/attachment/report.pdf",
        source_hash=source_hash,
        tags=["type/attachment-summary"],
    )
    write_note(sibling_path, "Existing LOCATED sibling body.", sibling_meta, actor="ai")

    # Make file appear old enough to pass cooldown
    mtime = binary_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    run_pipeline_called = []

    async def mock_run_pipeline(*args, **kwargs):
        run_pipeline_called.append(True)
        return Failure(error="should not be called", recoverable=False, context={})

    monkeypatch.setattr("pipelines.capture.run_pipeline", mock_run_pipeline)

    result = await capture_file(binary_file, context=pipeline_ctx)

    assert isinstance(result, Success)
    assert len(run_pipeline_called) == 0, "pipeline must NOT run when LOCATED source_hash matches"
