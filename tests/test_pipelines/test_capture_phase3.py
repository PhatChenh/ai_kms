"""Phase 3 + Phase 8 tests for pipelines/capture.py."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
        ai_domain=None,
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

    # "xkdhgksjfs" is keyboard mash (no vowels) → gate Rule 4 → FULL_RENAME
    md_file = vault_root / "inbox" / "xkdhgksjfs.md"
    md_file.write_text("# xkdhgksjfs\n\nBody.", encoding="utf-8")

    raw = _make_raw(md_file)
    mr = _make_metadata_result(raw, ai_title="New Title", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    # Renamed file should exist with sanitized title
    new_file = vault_root / "inbox" / "New Title.md"
    assert new_file.exists(), f"Expected renamed file at {new_file}"
    assert not md_file.exists(), "Old file should be removed after rename"

    # documents row for old path should be gone
    old_row = get_by_path("inbox/xkdhgksjfs.md", db_path=pipeline_ctx.db_path)
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

    # "jkqzxvbn" is keyboard mash (no vowels) → gate Rule 4 → FULL_RENAME
    md_file = vault_root / "inbox" / "jkqzxvbn.md"
    md_file.write_text("# jkqzxvbn\n\nBody.", encoding="utf-8")

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
# store — non-md CLUELESS path (inbox drop)
# Binary stays in inbox/; pending-routing marker written to inbox/.summaries/
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_clueless_inbox_binary_stays_pending_marker(vault_root, pipeline_ctx):
    """PDF dropped in inbox → CLUELESS: binary stays, pending-routing sibling at inbox/.summaries/."""
    from pipelines.capture import store

    pdf_file = vault_root / "inbox" / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 content")

    raw = _make_raw(pdf_file, is_md=False, text="Extracted PDF text.")
    mr = _make_metadata_result(raw, ai_title="report", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    # Binary stays in inbox/
    assert pdf_file.exists(), "Binary should NOT be moved — stays in inbox/"
    # Pending-routing marker at inbox/.summaries/report.pdf.md
    marker = vault_root / "inbox" / ".summaries" / "report.pdf.md"
    assert marker.exists(), f"Expected pending-routing marker at {marker}"
    # Marker frontmatter: status=pending-routing, attachment_path set, type=attachment-summary
    from vault.reader import read_note
    note = read_note(marker)
    assert isinstance(note, Success)
    meta = note.value.metadata
    assert meta.status == "pending-routing"
    assert meta.attachment_path == "inbox/report.pdf"
    assert meta.type == "attachment-summary"
    # No sibling written in inbox/ root (only in .summaries/)
    root_sibling = vault_root / "inbox" / "report.md"
    assert not root_sibling.exists()


# ===========================================================================
# store — non-md CLUELESS path (outside inbox: Briefings/ stray drop)
# Binary moves to inbox/; pending-routing marker at inbox/.summaries/
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_clueless_outside_inbox_moves_binary_to_inbox(vault_root, pipeline_ctx):
    """PDF dropped in Briefings/ → CLUELESS: binary moved to inbox/, pending-routing marker."""
    from pipelines.capture import store

    briefings_dir = vault_root / "Briefings"
    pdf_file = briefings_dir / "stray.pdf"
    pdf_file.write_bytes(b"%PDF stray content")

    raw = _make_raw(pdf_file, is_md=False, text="Stray PDF text.")
    mr = _make_metadata_result(raw, ai_title="stray", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    # Binary moved to inbox/stray.pdf
    inbox_binary = vault_root / "inbox" / "stray.pdf"
    assert inbox_binary.exists(), f"Expected binary at {inbox_binary}"
    assert not pdf_file.exists(), "Original should be gone"
    # Pending-routing marker at inbox/.summaries/stray.pdf.md
    marker = vault_root / "inbox" / ".summaries" / "stray.pdf.md"
    assert marker.exists(), f"Expected pending-routing marker at {marker}"
    from vault.reader import read_note
    note = read_note(marker)
    assert isinstance(note, Success)
    assert note.value.metadata.status == "pending-routing"


# ===========================================================================
# store — non-md CLUELESS: Projects/ root drop (no sub-project)
# Dropped in Projects/ root (not Projects/<A>/) → CLUELESS
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_clueless_projects_root_drop(vault_root, pipeline_ctx):
    """PDF dropped directly in Projects/ root (no sub-project) → CLUELESS."""
    from pipelines.capture import store

    pdf_file = vault_root / "Projects" / "stray.pdf"
    pdf_file.write_bytes(b"%PDF projects root content")

    raw = _make_raw(pdf_file, is_md=False, text="Projects root drop.")
    mr = _make_metadata_result(raw, ai_title="stray", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    marker = vault_root / "inbox" / ".summaries" / "stray.pdf.md"
    assert marker.exists(), f"Expected pending-routing marker at {marker}"
    from vault.reader import read_note
    note = read_note(marker)
    assert isinstance(note, Success)
    assert note.value.metadata.status == "pending-routing"


# ===========================================================================
# store — non-md LOCATED path: Projects/<A>/ (needs_move=True)
# PDF in Projects/Strategy/ → sibling at attachment/.summaries/, binary moved
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_located_project_needs_move(vault_root, pipeline_ctx, monkeypatch):
    """PDF in Projects/Strategy/report.pdf → LOCATED: sibling first, binary moved to attachment/."""
    from pipelines.capture import store
    from llm.provider import LLMResponse
    from unittest.mock import AsyncMock

    project_dir = vault_root / "Projects" / "Strategy"
    project_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = project_dir / "report.pdf"
    pdf_file.write_bytes(b"%PDF strategy report")

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(LLMResponse(
        content=(
            "## What this file is\nA strategy report.\n\n"
            "## Key content\nQ1 results.\n\n"
            "## Key facts / findings\nRevenue up 10%."
        ),
        model="test",
        usage={},
    ))
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    raw = _make_raw(pdf_file, is_md=False, text="Strategy report content.")
    mr = _make_metadata_result(raw, ai_title="report", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    # Sibling at Projects/Strategy/attachment/.summaries/report.pdf.md
    sibling = vault_root / "Projects" / "Strategy" / "attachment" / ".summaries" / "report.pdf.md"
    assert sibling.exists(), f"Expected sibling at {sibling}"
    # Binary moved to Projects/Strategy/attachment/report.pdf
    binary_dst = vault_root / "Projects" / "Strategy" / "attachment" / "report.pdf"
    assert binary_dst.exists(), f"Expected binary at {binary_dst}"
    assert not pdf_file.exists(), "Original should be gone"
    # attachment_path frontmatter = vault-relative binary path
    from vault.reader import read_note
    note = read_note(sibling)
    assert isinstance(note, Success)
    assert note.value.metadata.attachment_path == "Projects/Strategy/attachment/report.pdf"
    assert note.value.metadata.source_file is None


# ===========================================================================
# store — non-md LOCATED path: Projects/<A>/attachment/ (needs_move=False)
# Binary already in attachment/ → no move; sibling written only
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_located_project_no_move_sibling_only(vault_root, pipeline_ctx, monkeypatch):
    """PDF already in Projects/A/attachment/ → LOCATED needs_move=False: sibling written, no move."""
    from pipelines.capture import store
    from unittest.mock import AsyncMock
    from llm.provider import LLMResponse

    att_dir = vault_root / "Projects" / "Alpha" / "attachment"
    att_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = att_dir / "data.pdf"
    pdf_file.write_bytes(b"%PDF data content")

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(LLMResponse(
        content=(
            "## What this file is\nData file.\n\n"
            "## Key content\nNumbers.\n\n"
            "## Key facts / findings\nKey metric: 42."
        ),
        model="test",
        usage={},
    ))
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    raw = _make_raw(pdf_file, is_md=False, text="Data content.")
    mr = _make_metadata_result(raw, ai_title="data", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    # Sibling at .summaries/data.pdf.md
    sibling = att_dir / ".summaries" / "data.pdf.md"
    assert sibling.exists(), f"Expected sibling at {sibling}"
    # Binary stays at original location (no move)
    assert pdf_file.exists(), "Binary must not move (already at destination)"
    # attachment_path points to binary's vault-relative path
    from vault.reader import read_note
    note = read_note(sibling)
    assert isinstance(note, Success)
    assert note.value.metadata.attachment_path == "Projects/Alpha/attachment/data.pdf"


# ===========================================================================
# store — non-md LOCATED path: Domain/<D>/ (domain attachment)
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_located_domain(vault_root, pipeline_ctx, monkeypatch):
    """PDF in Domain/Finance/ → LOCATED domain: sibling at domain attachment/.summaries/."""
    from pipelines.capture import store
    from unittest.mock import AsyncMock
    from llm.provider import LLMResponse

    domain_dir = vault_root / "Domain" / "Finance"
    domain_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = domain_dir / "budget.pdf"
    pdf_file.write_bytes(b"%PDF budget")

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(LLMResponse(
        content=(
            "## What this file is\nBudget report.\n\n"
            "## Key content\nQ4 numbers.\n\n"
            "## Key facts / findings\nUnder budget by 5%."
        ),
        model="test",
        usage={},
    ))
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    raw = _make_raw(pdf_file, is_md=False, text="Budget content.")
    mr = _make_metadata_result(raw, ai_title="budget", vault_root=vault_root)

    result = await store(mr, pipeline_ctx)

    assert isinstance(result, Success)
    sibling = vault_root / "Domain" / "Finance" / "attachment" / ".summaries" / "budget.pdf.md"
    assert sibling.exists(), f"Expected sibling at {sibling}"
    binary_dst = vault_root / "Domain" / "Finance" / "attachment" / "budget.pdf"
    assert binary_dst.exists(), f"Expected binary at {binary_dst}"
    assert not pdf_file.exists()


# ===========================================================================
# store — non-md LOCATED: sibling body contains all 3 required sections
# ===========================================================================


@pytest.mark.asyncio
async def test_store_nonmd_located_sibling_body_has_three_sections(vault_root, pipeline_ctx, monkeypatch):
    """LOCATED sibling body (from summarize_attachment) has all 3 required markdown sections."""
    from pipelines.capture import store
    from unittest.mock import AsyncMock
    from llm.provider import LLMResponse

    project_dir = vault_root / "Projects" / "Demo"
    project_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = project_dir / "notes.pdf"
    pdf_file.write_bytes(b"%PDF notes")

    mock_provider = AsyncMock()
    mock_provider.complete.return_value = Success(LLMResponse(
        content=(
            "## What this file is\nMeeting notes.\n\n"
            "## Key content\nAction items listed.\n\n"
            "## Key facts / findings\nThree open issues."
        ),
        model="test",
        usage={},
    ))
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    raw = _make_raw(pdf_file, is_md=False, text="Notes content.")
    mr = _make_metadata_result(raw, ai_title="notes", vault_root=vault_root)

    await store(mr, pipeline_ctx)

    summaries_dir = vault_root / "Projects" / "Demo" / "attachment" / ".summaries"
    siblings = list(summaries_dir.glob("*.md"))
    assert siblings, f"Expected at least one sibling in {summaries_dir}"
    body = siblings[0].read_text(encoding="utf-8")
    assert "## What this file is" in body
    assert "## Key content" in body
    assert "## Key facts / findings" in body


# ===========================================================================
# capture_file — pending-routing early-exit guard (Brief #2 Phase 3)
# ===========================================================================


@pytest.mark.asyncio
async def test_capture_file_skips_pending_routing_binary(vault_root, pipeline_ctx, monkeypatch):
    """capture_file returns recoverable Failure early when inbox/.summaries/<filename>.md has status=pending-routing."""
    from pipelines.capture import capture_file
    from vault.frontmatter import NoteMetadata

    # Binary exists in inbox (so cooldown check doesn't blow up)
    pdf_file = vault_root / "inbox" / "parked.pdf"
    pdf_file.write_bytes(b"%PDF content")

    # Pre-create pending-routing marker
    summaries_dir = vault_root / "inbox" / ".summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    marker = summaries_dir / "parked.pdf.md"
    from vault.frontmatter import dumps
    marker.write_text(
        dumps(NoteMetadata(status="pending-routing", type="attachment-summary"), ""),
        encoding="utf-8",
    )

    # Cooldown: make file appear old enough to pass stability gate
    mtime = pdf_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    result = await capture_file(pdf_file, context=pipeline_ctx)

    # Must return recoverable Failure (silent skip — Phase 2 Classify will handle it)
    assert isinstance(result, Failure)
    assert result.recoverable is True
    assert "pending-routing" in result.error


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


# ===========================================================================
# Phase 8 — scan_capture
# ===========================================================================


@pytest.mark.asyncio
async def test_scan_capture_inbox_drop_returns_success(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """scan_capture with one new .md in inbox/ returns Success([WriteOutcome])."""
    from pipelines.capture import scan_capture
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse

    md_file = vault_root / "inbox" / "scan-note.md"
    md_file.write_text("# Scan Note\n\nBody text here.", encoding="utf-8")

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "scan-note", "tags": ["type/capture"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert len(result.value) == 1
    assert isinstance(result.value[0], WriteOutcome)


@pytest.mark.asyncio
async def test_scan_capture_non_inbox_drop_returns_success(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """scan_capture with one new .md in Projects/foo/ returns Success([WriteOutcome])."""
    from pipelines.capture import scan_capture
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse

    proj_dir = vault_root / "Projects" / "foo"
    proj_dir.mkdir(parents=True, exist_ok=True)
    md_file = proj_dir / "project-note.md"
    md_file.write_text("# Project Note\n\nContent here.", encoding="utf-8")

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "project-note", "tags": ["type/capture"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert len(result.value) == 1
    assert isinstance(result.value[0], WriteOutcome)


@pytest.mark.asyncio
async def test_scan_capture_zero_new_files_returns_empty(
    vault_root, db_path, pipeline_ctx
):
    """scan_capture with no new .md files returns Success([])."""
    from pipelines.capture import scan_capture

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert result.value == []


@pytest.mark.asyncio
async def test_scan_capture_skips_files_failing_stability_gate(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """scan_capture skips files that fail the stability gate (recoverable Failure)."""
    from pipelines.capture import scan_capture

    md_file = vault_root / "inbox" / "fresh.md"
    md_file.write_text("# Fresh\n\nBody.", encoding="utf-8")

    # time.time() returns mtime + 10 → age = 10s < 60s cooldown → gate fires
    monkeypatch.setattr(
        "pipelines.capture.time",
        MagicMock(time=lambda: md_file.stat().st_mtime + 10),
    )

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert result.value == []  # file skipped, not captured


@pytest.mark.asyncio
async def test_scan_capture_continues_after_fatal_failure(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """scan_capture logs WARNING but continues when one file fails fatally."""
    from pipelines.capture import scan_capture

    fail_file = vault_root / "inbox" / "fail-note.md"
    fail_file.write_text("# Fail\n\nBody.", encoding="utf-8")
    ok_file = vault_root / "inbox" / "ok-note.md"
    ok_file.write_text("# OK\n\nBody.", encoding="utf-8")

    async def patched_capture_file(path, context=None):
        if "fail-note" in path.name:
            return Failure(error="fatal error", recoverable=False, context={})
        return Success(MagicMock())

    monkeypatch.setattr("pipelines.capture.capture_file", patched_capture_file)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert len(result.value) == 1  # only ok-note.md captured


@pytest.mark.asyncio
async def test_scan_capture_does_not_recapture_modified_files(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """modified entries (in db + on disk, different hash) are not re-captured."""
    import hashlib
    import unicodedata
    from pipelines.capture import scan_capture
    from vault.writer import WriteOutcome
    from vault.frontmatter import NoteMetadata
    import storage.documents as documents_mod

    original_content = "# Known\n\nOriginal content.\n"
    md_file = vault_root / "inbox" / "known.md"
    md_file.write_text(original_content, encoding="utf-8")

    # Seed the documents table with the original hash
    original_hash = hashlib.sha256(
        unicodedata.normalize("NFC", original_content).encode("utf-8")
    ).hexdigest()
    seed_outcome = WriteOutcome(
        vault_path="inbox/known.md",
        absolute_path=md_file,
        content_hash=original_hash,
        metadata=NoteMetadata(),
    )
    documents_mod.upsert(seed_outcome, db_path=db_path)

    # Modify the file → different hash → detect_changes sees it as "modified"
    md_file.write_text("# Known\n\nModified content.\n", encoding="utf-8")

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert result.value == []  # modified → not in added → not re-captured
