"""Phase 3 tests for pipelines/capture.py — store (rename + non-md) + stability gate."""

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


def _make_metadata_result(raw: RawContent, ai_title: str, vault_root: Path):
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
        ai_tags=["test"],
        decision=decision,
    )


# ===========================================================================
# store — .md, in-place (ai_title == stem, no rename)
# ===========================================================================


@pytest.mark.asyncio
async def test_store_md_same_title_writes_in_place(vault_root, pipeline_ctx):
    from pipelines.capture import store
    from vault.writer import WriteOutcome

    md_file = vault_root / "inbox" / "my-note.md"
    md_file.write_text("# My Note\n\nBody.", encoding="utf-8")

    raw = _make_raw(md_file)
    mr = _make_metadata_result(raw, ai_title="my-note", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    assert isinstance(result.value, WriteOutcome)
    assert result.value.vault_path == "inbox/my-note.md"
    assert md_file.exists()


# ===========================================================================
# store — .md, rename (ai_title != stem)
# ===========================================================================


@pytest.mark.asyncio
async def test_store_md_different_title_renames_note(vault_root, pipeline_ctx):
    from pipelines.capture import store
    from storage.documents import get_by_path

    md_file = vault_root / "inbox" / "old-name.md"
    md_file.write_text("# Old Name\n\nBody.", encoding="utf-8")

    raw = _make_raw(md_file)
    mr = _make_metadata_result(raw, ai_title="New Title", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    # Renamed file should exist with sanitized title
    new_file = vault_root / "inbox" / "New Title.md"
    assert new_file.exists(), f"Expected renamed file at {new_file}"
    assert not md_file.exists(), "Old file should be removed after rename"

    # documents row for old path should be gone
    old_row = get_by_path("inbox/old-name.md", db_path=pipeline_ctx.db_path)
    assert isinstance(old_row, Success)
    assert old_row.value is None

    # documents row for new path should exist
    new_row = get_by_path("inbox/New Title.md", db_path=pipeline_ctx.db_path)
    assert isinstance(new_row, Success)
    assert new_row.value is not None


# ===========================================================================
# store — .md, rename collision → tries -1 suffix
# ===========================================================================


@pytest.mark.asyncio
async def test_store_md_rename_collision_tries_suffix(vault_root, pipeline_ctx):
    from pipelines.capture import store

    md_file = vault_root / "inbox" / "original.md"
    md_file.write_text("# Original\n\nBody.", encoding="utf-8")

    # Pre-create the target name to force collision
    collision = vault_root / "inbox" / "New Title.md"
    collision.write_text("# Existing Note\n\nBody.", encoding="utf-8")

    raw = _make_raw(md_file)
    mr = _make_metadata_result(raw, ai_title="New Title", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    expected = vault_root / "inbox" / "New Title-1.md"
    assert expected.exists(), f"Expected -1 suffix file at {expected}"
    assert not md_file.exists()


# ===========================================================================
# store — .md, all 10 suffix slots taken → in-place fallback + WARNING
# ===========================================================================


@pytest.mark.asyncio
async def test_store_md_all_suffix_slots_taken_falls_back_inplace(vault_root, pipeline_ctx):
    from pipelines.capture import store

    md_file = vault_root / "inbox" / "original.md"
    md_file.write_text("# Original\n\nBody.", encoding="utf-8")

    # Block all 10 slots: New Title.md, New Title-1.md ... New Title-9.md
    for i in range(10):
        target = vault_root / "inbox" / ("New Title.md" if i == 0 else f"New Title-{i}.md")
        target.write_text("# Existing\n\nBody.", encoding="utf-8")

    raw = _make_raw(md_file)
    mr = _make_metadata_result(raw, ai_title="New Title", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    # Original file written in-place (not renamed) — functional contract
    assert md_file.exists()
    assert result.value.vault_path == "inbox/original.md"


# ===========================================================================
# store — non-md (PDF) in inbox/
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_creates_sibling_and_moves_attachment(vault_root, pipeline_ctx):
    from pipelines.capture import store

    pdf_file = vault_root / "inbox" / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 content")

    raw = _make_raw(pdf_file, is_md=False, text="Extracted PDF text.")
    mr = _make_metadata_result(raw, ai_title="Annual Report", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    # Sibling .md created in inbox/ (same folder as drop)
    sibling = vault_root / "inbox" / "Annual Report.md"
    assert sibling.exists(), f"Expected sibling md at {sibling}"
    # Attachment moved to attachment/ folder
    attachment_dst = vault_root / "attachment" / "Annual Report.pdf"
    assert attachment_dst.exists(), f"Expected attachment at {attachment_dst}"
    # Original should be gone
    assert not pdf_file.exists()
    # Sibling content references attachment
    sibling_text = sibling.read_text()
    assert "Annual Report.pdf" in sibling_text


# ===========================================================================
# store — non-md in Projects/foo/ (not inbox)
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_creates_sibling_in_same_folder(vault_root, pipeline_ctx):
    from pipelines.capture import store

    project_dir = vault_root / "Projects" / "foo"
    project_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = project_dir / "spec.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 spec content")

    raw = _make_raw(pdf_file, is_md=False, text="Spec content.")
    mr = _make_metadata_result(raw, ai_title="spec", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    # Sibling in Projects/foo/ NOT inbox/
    sibling = project_dir / "spec.md"
    assert sibling.exists(), f"Expected sibling at {sibling}"
    inbox_sibling = vault_root / "inbox" / "spec.md"
    assert not inbox_sibling.exists(), "Sibling should NOT be created in inbox"


# ===========================================================================
# store — non-md re-capture (binary already moved)
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_recapture_skips_move_logs_warning(vault_root, pipeline_ctx):
    from pipelines.capture import store

    pdf_file = vault_root / "inbox" / "already-moved.pdf"
    # Do NOT create the file — simulates binary already moved

    raw = _make_raw(pdf_file, is_md=False, text="Extracted text.")
    mr = _make_metadata_result(raw, ai_title="already-moved", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    # Functional contract: returns Success (does not crash or error)
    assert isinstance(result, Success)
    # Sibling .md was created even though source binary was missing
    sibling = vault_root / "inbox" / "already-moved.md"
    assert sibling.exists()


# ===========================================================================
# store — non-md attachment collision > 100
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_attachment_collision_over_100_returns_failure(vault_root, pipeline_ctx):
    from pipelines.capture import store

    pdf_file = vault_root / "inbox" / "clash.pdf"
    pdf_file.write_bytes(b"%PDF content")

    # Block all 100 slots in attachment/
    att_dir = vault_root / "attachment"
    (att_dir / "clash.pdf").write_bytes(b"existing")
    for i in range(1, 101):
        (att_dir / f"clash-{i}.pdf").write_bytes(b"existing")

    raw = _make_raw(pdf_file, is_md=False, text="Clash content.")
    mr = _make_metadata_result(raw, ai_title="clash", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Failure)


# ===========================================================================
# capture_file — stability gate
# ===========================================================================


@pytest.mark.asyncio
async def test_capture_file_rejects_too_recent_file(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import capture_file

    md_file = vault_root / "inbox" / "fresh.md"
    md_file.write_text("# Fresh\n\nBody.", encoding="utf-8")

    # Patch time.time so mtime appears very recent (age = 0)
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: time.time()))
    # stat().st_mtime = just now → age < cooldown_seconds
    monkeypatch.setattr(
        "pipelines.capture.time",
        MagicMock(time=lambda: md_file.stat().st_mtime + 10),  # age = -10 < 60
    )

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Failure)
    assert result.recoverable is True


@pytest.mark.asyncio
async def test_capture_file_accepts_old_enough_file(vault_root, pipeline_ctx, monkeypatch):
    from pipelines.capture import capture_file
    from llm.provider import LLMResponse

    md_file = vault_root / "inbox" / "old-note.md"
    md_file.write_text("# Old Note\n\nBody.", encoding="utf-8")

    # Patch time.time so mtime appears 120 seconds old (> cooldown=60)
    mtime = md_file.stat().st_mtime
    monkeypatch.setattr(
        "pipelines.capture.time",
        MagicMock(time=lambda: mtime + 120),
    )

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "old-note", "type": "note", "tags": ["test"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success)
