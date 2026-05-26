"""Phase 12 bug-fix tests for pipelines/capture.py and storage/documents.py.

Covers:
- Fix 1: _parse_metadata_json returns Result[dict]
- Fix 2: _store_nonmd match is exhaustive (Success branch)
- Fix 3: _store_nonmd reorders to move-attachment-first
- Fix 4: _store_md rename uses replace_path with disk rollback on DB failure
- Fix 5: capture_file entry point dedup (single stability gate, single run_pipeline)
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.result import Failure, Success
from handlers.base import RawContent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw(path: Path, *, is_md: bool = True, text: str = "body text") -> RawContent:
    return RawContent(text=text, source_path=path, is_md=is_md)


def _make_metadata_result(raw: RawContent, ai_title: str):
    from pipelines.capture import MetadataResult
    from core.confidence import AIDecision

    decision = AIDecision(
        action="capture:metadata",
        confidence=0.9,
        reasoning="test",
        source_ids=["inbox/note.md"],
    )
    return MetadataResult(
        raw=raw,
        summary="A test summary.",
        ai_title=ai_title,
        ai_type="note",
        ai_domain=None,
        ai_tags=["test"],
        decision=decision,
    )


# ===========================================================================
# Fix 1 — _parse_metadata_json returns Result[dict]
# ===========================================================================


def test_parse_metadata_json_valid_json_returns_success():
    from pipelines.capture import _parse_metadata_json

    content = '{"title": "My Note", "tags": ["type/article", "ai"]}'
    result = _parse_metadata_json(content, source_stem="fallback")

    assert isinstance(result, Success)
    assert result.value["title"] == "My Note"
    assert result.value["tags"] == ["type/article", "ai"]


def test_parse_metadata_json_fenced_returns_success():
    from pipelines.capture import _parse_metadata_json

    content = '```json\n{"title": "Fenced", "tags": ["type/report"]}\n```'
    result = _parse_metadata_json(content, source_stem="fallback")

    assert isinstance(result, Success)
    assert result.value["title"] == "Fenced"


def test_parse_metadata_json_bad_json_falls_back_to_stem():
    """Unparseable JSON → Success with stem title and empty tags (TD-028 fix)."""
    from pipelines.capture import _parse_metadata_json

    result = _parse_metadata_json("not json at all {{", source_stem="fallback")

    assert isinstance(result, Success)
    assert result.value["title"] == "fallback"
    assert result.value["tags"] == []


def test_parse_metadata_json_missing_title_falls_back_to_stem_returns_success():
    from pipelines.capture import _parse_metadata_json

    result = _parse_metadata_json('{"tags": ["type/report"]}', source_stem="my-stem")

    assert isinstance(result, Success)
    assert result.value["title"] == "my-stem"


def test_parse_metadata_json_bad_tags_coerced_to_empty_returns_success():
    from pipelines.capture import _parse_metadata_json

    result = _parse_metadata_json('{"title": "T", "tags": "not-a-list"}', source_stem="s")

    assert isinstance(result, Success)
    assert result.value["tags"] == []


# ===========================================================================
# Fix 1 — metadata stage propagates _parse_metadata_json Failure
# ===========================================================================


@pytest.mark.asyncio
async def test_metadata_falls_back_and_writes_audit_row_on_unparseable_json(
    vault_root, pipeline_ctx, monkeypatch
):
    """When LLM returns unparseable JSON, metadata stage falls back to stem title and writes audit row (TD-028 fix)."""
    from pipelines.capture import metadata, SummarizeResult
    from llm.provider import LLMResponse
    from storage.audit_log import query

    md_file = vault_root / "inbox" / "note.md"
    md_file.write_text("# Note\n\nContent.", encoding="utf-8")
    sr = SummarizeResult(raw=_make_raw(md_file), summary="Summary.")

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(
        LLMResponse(content="not valid json {{{{ }", model="test", usage={})
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await metadata(sr, pipeline_ctx)

    assert isinstance(result, Success)
    assert result.value.ai_title == "note"  # stem of note.md
    assert result.value.ai_tags == []

    entries = query(pipeline="capture", db_path=pipeline_ctx.db_path)
    assert isinstance(entries, Success)
    assert len(entries.value) == 1


# ===========================================================================
# Brief #2 Phase 3 — _store_nonmd: sibling-first, CLUELESS + LOCATED
# (Supersedes Phase 12 "attachment-first" ordering tests.)
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_clueless_inbox_missing_binary_writes_marker(
    vault_root, pipeline_ctx
):
    """CLUELESS inbox: binary missing → pending-routing marker written (broken pointer, TD-026)."""
    from pipelines.capture import store

    pdf_file = vault_root / "inbox" / "ghost.pdf"
    # Binary not created — simulates ghost/missing file

    raw = _make_raw(pdf_file, is_md=False, text="Extracted text.")
    mr = _make_metadata_result(raw, ai_title="ghost")

    result = await store(mr, pipeline_ctx)

    # CLUELESS inbox: no move needed → marker written regardless (broken pointer is accepted)
    assert isinstance(result, Success)
    marker = vault_root / "inbox" / ".summaries" / "ghost.pdf.md"
    assert marker.exists(), "Pending-routing marker must be written even for missing binary"
    from vault.reader import read_note
    note = read_note(marker)
    assert isinstance(note, Success)
    assert note.value.metadata.status == "pending-routing"


@pytest.mark.asyncio
async def test_store_nonmd_located_happy_path_documents_row(vault_root, pipeline_ctx, monkeypatch):
    """LOCATED project happy path: sibling at .summaries/, binary moved, documents row present."""
    from pipelines.capture import store
    from storage.documents import get_by_path
    from llm.provider import LLMResponse

    project_dir = vault_root / "Projects" / "Alpha"
    project_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = project_dir / "deck.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 content")

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(LLMResponse(
        content=(
            "## What this file is\nA deck.\n\n"
            "## Key content\nSlides.\n\n"
            "## Key facts / findings\nTwo findings."
        ),
        model="test",
        usage={},
    ))
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    raw = _make_raw(pdf_file, is_md=False, text="Deck content.")
    mr = _make_metadata_result(raw, ai_title="deck")

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    sibling = vault_root / "Projects" / "Alpha" / "attachment" / ".summaries" / "deck.pdf.md"
    assert sibling.exists()
    binary_dst = vault_root / "Projects" / "Alpha" / "attachment" / "deck.pdf"
    assert binary_dst.exists()
    assert not pdf_file.exists()

    row = get_by_path("Projects/Alpha/attachment/.summaries/deck.pdf.md", db_path=pipeline_ctx.db_path)
    assert isinstance(row, Success)
    assert row.value is not None


@pytest.mark.asyncio
async def test_store_nonmd_clueless_write_note_failure_returns_failure(
    vault_root, pipeline_ctx, monkeypatch
):
    """CLUELESS path: if write_note (marker) fails → Failure; binary stays in inbox (sibling-first)."""
    from pipelines.capture import store

    pdf_file = vault_root / "inbox" / "write-fail.pdf"
    pdf_file.write_bytes(b"%PDF content")

    raw = _make_raw(pdf_file, is_md=False, text="Extracted text.")
    mr = _make_metadata_result(raw, ai_title="write-fail")

    monkeypatch.setattr(
        "pipelines.capture.write_note",
        lambda *a, **kw: Failure(error="disk full", recoverable=False, context={}),
    )

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Failure)
    # Binary stays in inbox (sibling-first: no move attempted before write)
    assert pdf_file.exists(), "Binary must stay in inbox when write_note fails"
    # No marker written
    marker = vault_root / "inbox" / ".summaries" / "write-fail.pdf.md"
    assert not marker.exists()


# ===========================================================================
# Fix 4 — _store_md rename: replace_path + disk rollback
# ===========================================================================


@pytest.mark.asyncio
async def test_store_md_rename_happy_path_single_documents_row(vault_root, pipeline_ctx):
    """Rename happy path: only new vault_path in documents, old path gone."""
    from pipelines.capture import store
    from storage.documents import get_by_path

    # "xkdhgksjfs" is keyboard mash (no vowels) → gate Rule 4 → FULL_RENAME → "New Title"
    md_file = vault_root / "inbox" / "xkdhgksjfs.md"
    md_file.write_text("# xkdhgksjfs\n\nBody.", encoding="utf-8")

    raw = _make_raw(md_file)
    mr = _make_metadata_result(raw, ai_title="New Title")

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    new_file = vault_root / "inbox" / "New Title.md"
    assert new_file.exists()
    assert not md_file.exists()

    old_row = get_by_path("inbox/xkdhgksjfs.md", db_path=pipeline_ctx.db_path)
    assert isinstance(old_row, Success)
    assert old_row.value is None  # old row deleted

    new_row = get_by_path("inbox/New Title.md", db_path=pipeline_ctx.db_path)
    assert isinstance(new_row, Success)
    assert new_row.value is not None  # new row present


@pytest.mark.asyncio
async def test_store_md_rename_db_failure_rolls_back_disk_rename(
    vault_root, pipeline_ctx, monkeypatch
):
    """DB replace_path failure → disk rename rolled back; original filename restored."""
    from pipelines.capture import store
    import storage.documents as docs_mod

    # "kqzxvbn" is keyboard mash (no vowels) → gate Rule 4 → FULL_RENAME → triggers rename path
    md_file = vault_root / "inbox" / "kqzxvbn.md"
    md_file.write_text("# kqzxvbn\n\nBody.", encoding="utf-8")

    raw = _make_raw(md_file)
    mr = _make_metadata_result(raw, ai_title="New Name")

    monkeypatch.setattr(
        docs_mod,
        "replace_path",
        lambda *a, **kw: Failure(error="db locked", recoverable=False, context={}),
    )

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Failure)
    # Original file restored
    assert md_file.exists(), "Original file must be restored after DB failure rollback"
    new_file = vault_root / "inbox" / "New Name.md"
    assert not new_file.exists(), "Renamed file must be removed after rollback"


# ===========================================================================
# Fix 5 — capture_file: single stability gate (dedup regression guard)
# ===========================================================================


def test_capture_file_has_single_stability_gate_string():
    """'file too recent' appears exactly once in capture.py (regression guard)."""
    import subprocess
    import os

    capture_py = (
        Path(__file__).parent.parent.parent / "src" / "pipelines" / "capture.py"
    )
    result = subprocess.run(
        ["grep", "-c", "file too recent", str(capture_py)],
        capture_output=True,
        text=True,
    )
    count = int(result.stdout.strip())
    assert count == 1, f"Expected 1 occurrence of 'file too recent', found {count}"


@pytest.mark.asyncio
async def test_capture_file_cooldown_rejects_too_recent_file(vault_root, pipeline_ctx, monkeypatch):
    """capture_file rejects file whose age < cooldown (recoverable Failure)."""
    from pipelines.capture import capture_file

    md_file = vault_root / "inbox" / "fresh.md"
    md_file.write_text("# Fresh\n\nBody.", encoding="utf-8")

    # age = 10s < cooldown = 60s
    monkeypatch.setattr(
        "pipelines.capture.time",
        MagicMock(time=lambda: md_file.stat().st_mtime + 10),
    )

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Failure)
    assert result.recoverable is True


@pytest.mark.asyncio
async def test_capture_file_stale_file_with_explicit_context_proceeds(
    vault_root, pipeline_ctx, monkeypatch
):
    """capture_file with explicit context and stale file proceeds to pipeline."""
    from pipelines.capture import capture_file
    from llm.provider import LLMResponse

    md_file = vault_root / "inbox" / "stale.md"
    md_file.write_text("# Stale\n\nBody.", encoding="utf-8")

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr(
        "pipelines.capture.time",
        MagicMock(time=lambda: mtime + 120),
    )

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "stale", "tags": ["type/capture"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success)


# ===========================================================================
# storage/documents.replace_path
# ===========================================================================


def test_replace_path_atomically_swaps_rows(tmp_path):
    """replace_path deletes old row and upserts new row in one transaction."""
    from storage.db import init_db
    from storage.documents import replace_path, get_by_path, upsert
    from vault.writer import WriteOutcome
    from vault.frontmatter import NoteMetadata

    db = tmp_path / "test.db"
    init_db(db)

    # Seed an old row
    old_outcome = WriteOutcome(
        vault_path="inbox/old.md",
        absolute_path=tmp_path / "old.md",
        content_hash="abc123",
        metadata=NoteMetadata(),
    )
    upsert(old_outcome, db_path=db)

    old_row = get_by_path("inbox/old.md", db_path=db)
    assert isinstance(old_row, Success) and old_row.value is not None

    # New outcome (what the renamed note will look like)
    new_outcome = WriteOutcome(
        vault_path="inbox/new.md",
        absolute_path=tmp_path / "new.md",
        content_hash="def456",
        metadata=NoteMetadata(summary="Updated summary."),
    )

    result = replace_path("inbox/old.md", new_outcome, db_path=db)

    assert isinstance(result, Success)

    # Old row gone
    old_row = get_by_path("inbox/old.md", db_path=db)
    assert isinstance(old_row, Success) and old_row.value is None

    # New row present with updated summary
    new_row = get_by_path("inbox/new.md", db_path=db)
    assert isinstance(new_row, Success) and new_row.value is not None
    assert new_row.value.summary == "Updated summary."


def test_replace_path_returns_failure_on_db_error(tmp_path, monkeypatch):
    """replace_path returns Failure on sqlite3.Error."""
    from storage.db import init_db
    from storage.documents import replace_path, upsert
    from vault.writer import WriteOutcome
    from vault.frontmatter import NoteMetadata
    import storage.documents as docs_mod
    import sqlite3

    db = tmp_path / "test.db"
    init_db(db)

    old_outcome = WriteOutcome(
        vault_path="inbox/old.md",
        absolute_path=tmp_path / "old.md",
        content_hash="abc",
        metadata=NoteMetadata(),
    )
    upsert(old_outcome, db_path=db)

    new_outcome = WriteOutcome(
        vault_path="inbox/new.md",
        absolute_path=tmp_path / "new.md",
        content_hash="def",
        metadata=NoteMetadata(),
    )

    class _BrokenConn:
        def __enter__(self): raise sqlite3.OperationalError("db locked")
        def __exit__(self, *a): pass

    monkeypatch.setattr(docs_mod, "get_connection", lambda *_a, **_kw: _BrokenConn())

    result = replace_path("inbox/old.md", new_outcome, db_path=db)

    assert isinstance(result, Failure)
    assert result.recoverable is False
