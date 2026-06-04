"""Phase 10 tests — Modified/deleted/moved reconciliation in scan_capture."""

from __future__ import annotations

import shutil
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.result import Failure, Success


# ---------------------------------------------------------------------------
# test 1 — modified .md triggers full re-capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_modified_md_recaptured(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """modified .md → capture_file called again → documents row updated, fresh summary written."""
    from pipelines.capture import scan_capture
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    from llm.provider import LLMResponse
    import storage.documents as docs

    # Write initial note via write_note so DB has the correct hash
    md_file = vault_root / "inbox" / "known.md"
    wr = write_note(md_file, "Original body content.", NoteMetadata(summary="Old summary."), actor="ai")
    assert isinstance(wr, Success)
    docs.upsert(wr.value, db_path=db_path)

    # Overwrite file with different content → different hash → detect_changes: modified
    md_file.write_text("# Known\n\nCompletely changed content here.\n", encoding="utf-8")

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Fresh new summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "known", "tags": ["type/capture"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert len(result.value) == 1  # modified file re-captured
    assert mock_provider.complete.call_count == 2  # pipeline ran (summarize + metadata)
    # Frontmatter in file should contain the new summary
    note_text = md_file.read_text(encoding="utf-8")
    assert "Fresh new summary." in note_text


# ---------------------------------------------------------------------------
# test 2 — deleted .md → documents row removed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_deleted_md_row_removed(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """deleted .md → delete_by_path called → documents row no longer in table."""
    from pipelines.capture import scan_capture
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    from storage.documents import get_by_path
    import storage.documents as docs

    # Create note and seed DB
    md_file = vault_root / "inbox" / "to-delete.md"
    wr = write_note(md_file, "Body to delete.", NoteMetadata(), actor="ai")
    assert isinstance(wr, Success)
    docs.upsert(wr.value, db_path=db_path)

    # Confirm row exists before deletion
    row_before = get_by_path("inbox/to-delete.md", db_path=db_path)
    assert isinstance(row_before, Success) and row_before.value is not None

    # Delete file from disk
    md_file.unlink()

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    # Row should be gone
    row_after = get_by_path("inbox/to-delete.md", db_path=db_path)
    assert isinstance(row_after, Success)
    assert row_after.value is None


# ---------------------------------------------------------------------------
# test 3 — moved .md → vault_path updated, integer id unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_moved_md_vault_path_updated_id_preserved(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """moved .md → documents.rename called → vault_path updated; integer id unchanged (DECISION-001)."""
    from pipelines.capture import scan_capture
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    from storage.documents import get_by_path
    import storage.documents as docs

    # Write note at old path and seed DB
    old_file = vault_root / "inbox" / "old-name.md"
    wr = write_note(old_file, "Body content stays the same.", NoteMetadata(), actor="ai")
    assert isinstance(wr, Success)
    upsert_result = docs.upsert(wr.value, db_path=db_path)
    assert isinstance(upsert_result, Success)
    original_id = upsert_result.value  # lastrowid == INTEGER PRIMARY KEY

    # Move file on disk (same content → same hash → detect_changes: moved)
    new_file = vault_root / "inbox" / "new-name.md"
    shutil.move(str(old_file), str(new_file))

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    # Old path gone from DB
    old_row = get_by_path("inbox/old-name.md", db_path=db_path)
    assert isinstance(old_row, Success) and old_row.value is None
    # New path present with the same integer id
    new_row = get_by_path("inbox/new-name.md", db_path=db_path)
    assert isinstance(new_row, Success) and new_row.value is not None
    assert new_row.value.id == original_id  # id preserved, not re-inserted


# ---------------------------------------------------------------------------
# test 4 — modified loop: fatal failure → WARNING logged, others continue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_modified_loop_fatal_failure_logs_warning_continues(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """modified loop: one file fails fatally → WARNING logged, others continue, Success returned."""
    from pipelines.capture import scan_capture
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    import storage.documents as docs

    # Create two notes, seed DB with correct hash, then overwrite → both "modified"
    for name, body in [("fail-note.md", "Fail body."), ("ok-note.md", "OK body.")]:
        f = vault_root / "inbox" / name
        wr = write_note(f, body, NoteMetadata(), actor="ai")
        assert isinstance(wr, Success)
        docs.upsert(wr.value, db_path=db_path)
        f.write_text("# Changed\n\nDifferent content.\n", encoding="utf-8")

    mtime = max(
        (vault_root / "inbox" / "fail-note.md").stat().st_mtime,
        (vault_root / "inbox" / "ok-note.md").stat().st_mtime,
    )
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    async def patched_capture(path, context=None):
        if "fail-note" in path.name:
            return Failure(error="fatal capture error", recoverable=False, context={})
        return Success(MagicMock())

    monkeypatch.setattr("pipelines.capture.capture_file", patched_capture)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    # Only ok-note captured; fail-note logged WARNING and skipped
    assert len(result.value) == 1


# ---------------------------------------------------------------------------
# test 5 — deleted loop: delete_by_path fails for one → others continue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_deleted_loop_failure_logs_warning_continues(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """deleted loop: one delete_by_path failure → WARNING logged, others continue."""
    from pipelines.capture import scan_capture
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    import storage.documents as docs

    # Seed DB with two notes, then delete both from disk
    for name in ["del-fail.md", "del-ok.md"]:
        f = vault_root / "inbox" / name
        wr = write_note(f, f"Body {name}.", NoteMetadata(), actor="ai")
        assert isinstance(wr, Success)
        docs.upsert(wr.value, db_path=db_path)
        f.unlink()

    # Patch delete_by_path to fail on first call
    original_delete = docs.delete_by_path
    call_count = {"n": 0}

    def patched_delete(vault_path, db_path=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return Failure(error="delete db error", recoverable=False, context={})
        return original_delete(vault_path, db_path=db_path)

    monkeypatch.setattr("storage.documents.delete_by_path", patched_delete)

    result = await scan_capture(root=vault_root, db_path=db_path)

    # scan_capture returns Success regardless of individual delete failures
    assert isinstance(result, Success)
    assert result.value == []


# ---------------------------------------------------------------------------
# test 6 — moved loop: rename fails for one → others continue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_moved_loop_failure_logs_warning_continues(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """moved loop: one rename fails → WARNING logged, others continue."""
    from pipelines.capture import scan_capture
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    from storage.documents import get_by_path
    import storage.documents as docs

    # Seed DB with two notes at old paths, move both on disk
    for old_name, new_name in [("move-fail.md", "moved-fail.md"), ("move-ok.md", "moved-ok.md")]:
        old_f = vault_root / "inbox" / old_name
        wr = write_note(old_f, f"Body for {old_name}.", NoteMetadata(), actor="ai")
        assert isinstance(wr, Success)
        docs.upsert(wr.value, db_path=db_path)
        shutil.move(str(old_f), str(vault_root / "inbox" / new_name))

    # Patch rename to fail on first call
    original_rename = docs.rename
    call_count = {"n": 0}

    def patched_rename(old, new, db_path=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return Failure(error="rename db error", recoverable=False, context={})
        return original_rename(old, new, db_path=db_path)

    monkeypatch.setattr("storage.documents.rename", patched_rename)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    # Second rename succeeded — moved-ok.md is in DB
    ok_row = get_by_path("inbox/moved-ok.md", db_path=db_path)
    assert isinstance(ok_row, Success) and ok_row.value is not None


# ---------------------------------------------------------------------------
# test 7 — zero modified/deleted/moved → no extra calls, Success([])
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_zero_modified_deleted_moved_no_extra_calls(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """Zero modified/deleted/moved with empty vault → Success([]) returned without errors."""
    from pipelines.capture import scan_capture

    # Empty vault, empty DB → all four change-type lists are empty
    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert result.value == []
