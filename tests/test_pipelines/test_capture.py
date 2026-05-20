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

    content = '{"title": "My Note", "type": "note", "tags": ["ai", "capture"]}'
    result = _parse_metadata_json(content, source_stem="fallback")

    assert isinstance(result, dict)
    assert result["title"] == "My Note"
    assert result["type"] == "note"
    assert result["tags"] == ["ai", "capture"]


def test_parse_metadata_json_fenced_json():
    from pipelines.capture import _parse_metadata_json

    content = "```json\n{\"title\": \"My Note\", \"type\": \"note\", \"tags\": [\"ai\"]}\n```"
    result = _parse_metadata_json(content, source_stem="fallback")

    assert isinstance(result, dict)
    assert result["title"] == "My Note"
    assert result["tags"] == ["ai"]


def test_parse_metadata_json_bad_tags_type_coerced_to_empty():
    from pipelines.capture import _parse_metadata_json

    content = '{"title": "My Note", "type": "note", "tags": "ai"}'
    result = _parse_metadata_json(content, source_stem="fallback")

    assert isinstance(result, dict)
    assert result["tags"] == []


def test_parse_metadata_json_missing_title_falls_back_to_stem():
    from pipelines.capture import _parse_metadata_json

    content = '{"type": "note", "tags": ["ai"]}'
    result = _parse_metadata_json(content, source_stem="my-note-stem")

    assert isinstance(result, dict)
    assert result["title"] == "my-note-stem"


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
