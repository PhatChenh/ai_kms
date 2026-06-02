"""Phase 4 tests — kms reconcile command (4 stages)."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.result import Failure, Success

_FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "sample_text.pdf"


def _copy_pdf(dst: Path) -> None:
    shutil.copy(_FIXTURE_PDF, dst)


# ---------------------------------------------------------------------------
# ReconcileResult dataclass
# ---------------------------------------------------------------------------


def test_reconcile_result_defaults():
    """ReconcileResult initialises all counters to 0 and is frozen."""
    from pipelines.reconcile import ReconcileResult

    r = ReconcileResult()
    assert r.paths_reconciled == 0
    assert r.new_captures == 0
    assert r.restale_count == 0
    assert r.orphans_cleaned == 0


def test_reconcile_result_replace():
    """ReconcileResult.replace() returns new instance with updated field."""
    from pipelines.reconcile import ReconcileResult

    r = ReconcileResult()
    r2 = r.replace(paths_reconciled=3)
    assert r.paths_reconciled == 0  # original unchanged
    assert r2.paths_reconciled == 3
    assert r2.new_captures == 0


# ---------------------------------------------------------------------------
# Stage 1 — reconcile_paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_paths_moved_note_updates_db(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """Stage 1: note moved on disk → documents.rename called for old→new path."""
    from pipelines.reconcile import ReconcileResult, reconcile_paths
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    import storage.documents as documents

    # Create note at original path, index it
    src = vault_root / "Projects" / "Alpha" / "note.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    result = write_note(src, "hello world", NoteMetadata(), actor="ai")
    assert isinstance(result, Success)
    documents.upsert(result.value, db_path=db_path)

    # Move note on disk (rename directory simulates project move)
    dst_dir = vault_root / "Domain" / "Engineering" / "Archive" / "Alpha"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "note.md"
    shutil.move(str(src), str(dst))

    initial = ReconcileResult()
    match await reconcile_paths(initial, pipeline_ctx, []):
        case Success(value=r):
            assert isinstance(r, ReconcileResult)
            assert r.paths_reconciled > 0
        case Failure(error=e):
            pytest.fail(f"Stage 1 failed: {e}")

    # Old vault_path should not exist in DB
    old_vp = "Projects/Alpha/note.md"
    match documents.get_by_path(old_vp, db_path=db_path):
        case Success(value=row):
            assert row is None, f"Old path {old_vp} should be gone from DB"
        case Failure(error=e):
            pytest.fail(f"DB lookup failed: {e}")


@pytest.mark.asyncio
async def test_reconcile_paths_deleted_note_removes_db_row(
    vault_root, db_path, pipeline_ctx
):
    """Stage 1: note deleted on disk → documents.delete_by_path removes DB row."""
    from pipelines.reconcile import ReconcileResult, reconcile_paths
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    import storage.documents as documents

    note = vault_root / "Projects" / "Beta" / "temp.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    result = write_note(note, "temporary note", NoteMetadata(), actor="ai")
    assert isinstance(result, Success)
    documents.upsert(result.value, db_path=db_path)

    vp = "Projects/Beta/temp.md"
    match documents.get_by_path(vp, db_path=db_path):
        case Success(value=row):
            assert row is not None, "Note must be in DB before delete"

    # Delete note from disk
    note.unlink()

    initial = ReconcileResult()
    match await reconcile_paths(initial, pipeline_ctx, []):
        case Success(value=r):
            assert r.paths_reconciled > 0
        case Failure(error=e):
            pytest.fail(f"Stage 1 failed: {e}")

    match documents.get_by_path(vp, db_path=db_path):
        case Success(value=row):
            assert row is None, f"Deleted path {vp} should be gone from DB"


# ---------------------------------------------------------------------------
# Stage 2 — reconcile_orphan_binaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_orphan_binaries_captures_binary_without_sibling(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """Stage 2: binary in attachment/ with no sibling → capture_file called."""
    from pipelines.reconcile import ReconcileResult, reconcile_orphan_binaries

    att = vault_root / "Projects" / "Gamma" / "attachment"
    att.mkdir(parents=True, exist_ok=True)
    pdf = att / "orphan.pdf"
    _copy_pdf(pdf)

    # No .summaries/ sibling exists
    sibling = att / ".summaries" / "orphan.pdf.md"
    assert not sibling.exists(), "Sibling must not exist for orphan test"

    mock_capture = AsyncMock(return_value=Success(MagicMock()))
    monkeypatch.setattr("pipelines.reconcile.capture_file", mock_capture)

    initial = ReconcileResult()
    match await reconcile_orphan_binaries(initial, pipeline_ctx):
        case Success(value=r):
            assert r.new_captures >= 1
        case Failure(error=e):
            pytest.fail(f"Stage 2 failed: {e}")

    # capture_file should have been called for the orphan binary
    assert mock_capture.call_count >= 1
    called_paths = [str(call.args[0]) for call in mock_capture.call_args_list]
    assert str(pdf) in called_paths, f"capture_file should be called for {pdf}"


@pytest.mark.asyncio
async def test_reconcile_orphan_binaries_skips_binary_with_sibling(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """Stage 2: binary that already has a sibling → capture_file NOT called."""
    from pipelines.reconcile import ReconcileResult, reconcile_orphan_binaries

    att = vault_root / "Projects" / "Delta" / "attachment"
    att.mkdir(parents=True, exist_ok=True)
    sum_dir = att / ".summaries"
    sum_dir.mkdir(parents=True, exist_ok=True)

    pdf = att / "existing.pdf"
    _copy_pdf(pdf)
    # Create sibling summary
    (sum_dir / "existing.pdf.md").write_text(
        "---\nattachment_path: Projects/Delta/attachment/existing.pdf\n---\nSummary text.",
        encoding="utf-8",
    )

    mock_capture = AsyncMock(return_value=Success(MagicMock()))
    monkeypatch.setattr("pipelines.reconcile.capture_file", mock_capture)

    initial = ReconcileResult()
    match await reconcile_orphan_binaries(initial, pipeline_ctx):
        case Success(value=r):
            assert r.new_captures == 0
        case Failure(error=e):
            pytest.fail(f"Stage 2 failed: {e}")

    # capture_file should NOT be called — sibling already exists
    mock_capture.assert_not_called()


# ---------------------------------------------------------------------------
# Stage 3 — reconcile_stale_binaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_stale_binaries_recaptures_when_binary_newer(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """Stage 3: binary mtime > sibling mtime → capture_file called (re-summarize)."""
    from pipelines.reconcile import ReconcileResult, reconcile_stale_binaries

    att = vault_root / "Projects" / "Epsilon" / "attachment"
    att.mkdir(parents=True, exist_ok=True)
    sum_dir = att / ".summaries"
    sum_dir.mkdir(parents=True, exist_ok=True)

    pdf = att / "stale.pdf"
    _copy_pdf(pdf)

    sibling = sum_dir / "stale.pdf.md"
    sibling.write_text(
        "---\nattachment_path: Projects/Epsilon/attachment/stale.pdf\n---\nOld summary.",
        encoding="utf-8",
    )

    # Set binary mtime newer than sibling mtime
    import os
    old_time = 1000000000.0
    new_time = old_time + 3600.0  # 1 hour newer
    os.utime(sibling, (old_time, old_time))
    os.utime(pdf, (new_time, new_time))

    mock_capture = AsyncMock(return_value=Success(MagicMock()))
    monkeypatch.setattr("pipelines.reconcile.capture_file", mock_capture)

    initial = ReconcileResult()
    match await reconcile_stale_binaries(initial, pipeline_ctx):
        case Success(value=r):
            assert r.restale_count >= 1
        case Failure(error=e):
            pytest.fail(f"Stage 3 failed: {e}")

    assert mock_capture.call_count >= 1


@pytest.mark.asyncio
async def test_reconcile_stale_binaries_skips_when_binary_older(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """Stage 3: binary mtime <= sibling mtime → no re-capture."""
    from pipelines.reconcile import ReconcileResult, reconcile_stale_binaries

    att = vault_root / "Projects" / "Zeta" / "attachment"
    att.mkdir(parents=True, exist_ok=True)
    sum_dir = att / ".summaries"
    sum_dir.mkdir(parents=True, exist_ok=True)

    pdf = att / "fresh.pdf"
    _copy_pdf(pdf)

    sibling = sum_dir / "fresh.pdf.md"
    sibling.write_text(
        "---\nattachment_path: Projects/Zeta/attachment/fresh.pdf\n---\nFresh summary.",
        encoding="utf-8",
    )

    # Sibling newer than binary (just-written summary)
    import os
    old_time = 1000000000.0
    new_time = old_time + 3600.0
    os.utime(pdf, (old_time, old_time))
    os.utime(sibling, (new_time, new_time))

    mock_capture = AsyncMock(return_value=Success(MagicMock()))
    monkeypatch.setattr("pipelines.reconcile.capture_file", mock_capture)

    initial = ReconcileResult()
    match await reconcile_stale_binaries(initial, pipeline_ctx):
        case Success(value=r):
            assert r.restale_count == 0
        case Failure(error=e):
            pytest.fail(f"Stage 3 failed: {e}")

    mock_capture.assert_not_called()


# ---------------------------------------------------------------------------
# Stage 4 — reconcile_orphan_siblings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_orphan_siblings_removes_ghost_when_binary_gone(
    vault_root, db_path, pipeline_ctx
):
    """Stage 4: sibling with attachment_path → missing binary → unlink + DB row removed."""
    from pipelines.reconcile import ReconcileResult, reconcile_orphan_siblings
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    import storage.documents as documents

    att = vault_root / "Projects" / "Eta" / "attachment"
    att.mkdir(parents=True, exist_ok=True)
    sum_dir = att / ".summaries"
    sum_dir.mkdir(parents=True, exist_ok=True)

    # Write sibling pointing to non-existent binary
    sibling = sum_dir / "ghost.md"
    meta = NoteMetadata(
        attachment_path="Projects/Eta/attachment/deleted.pdf",
        type="attachment-summary",
    )
    result = write_note(sibling, "Ghost summary.", meta, actor="ai")
    assert isinstance(result, Success)
    documents.upsert(result.value, db_path=db_path)

    sibling_vp = "Projects/Eta/attachment/.summaries/ghost.md"
    assert sibling.exists(), "Sibling must exist before Stage 4"

    initial = ReconcileResult()
    match await reconcile_orphan_siblings(initial, pipeline_ctx):
        case Success(value=r):
            assert r.orphans_cleaned >= 1
        case Failure(error=e):
            pytest.fail(f"Stage 4 failed: {e}")

    # Sibling .md file should be deleted
    assert not sibling.exists(), f"Sibling {sibling} should be unlinked"

    # DB row should be removed
    match documents.get_by_path(sibling_vp, db_path=db_path):
        case Success(value=row):
            assert row is None, f"DB row for {sibling_vp} should be gone"


@pytest.mark.asyncio
async def test_reconcile_orphan_siblings_skips_when_binary_exists(
    vault_root, db_path, pipeline_ctx
):
    """Stage 4: sibling with valid attachment_path → binary exists → keep."""
    from pipelines.reconcile import ReconcileResult, reconcile_orphan_siblings
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    import storage.documents as documents

    att = vault_root / "Projects" / "Theta" / "attachment"
    att.mkdir(parents=True, exist_ok=True)
    sum_dir = att / ".summaries"
    sum_dir.mkdir(parents=True, exist_ok=True)

    pdf = att / "alive.pdf"
    _copy_pdf(pdf)

    sibling = sum_dir / "alive.md"
    meta = NoteMetadata(
        attachment_path="Projects/Theta/attachment/alive.pdf",
        type="attachment-summary",
    )
    result = write_note(sibling, "Healthy summary.", meta, actor="ai")
    assert isinstance(result, Success)
    documents.upsert(result.value, db_path=db_path)

    sibling_vp = "Projects/Theta/attachment/.summaries/alive.md"

    initial = ReconcileResult()
    match await reconcile_orphan_siblings(initial, pipeline_ctx):
        case Success(value=r):
            assert r.orphans_cleaned == 0
        case Failure(error=e):
            pytest.fail(f"Stage 4 failed: {e}")

    # Sibling .md file should still exist
    assert sibling.exists(), "Healthy sibling should not be deleted"


@pytest.mark.asyncio
async def test_reconcile_orphan_siblings_skips_human_edited(
    vault_root, db_path, pipeline_ctx
):
    """Stage 4: sibling with updated_by_human=True → NOT deleted, warning logged."""
    from pipelines.reconcile import ReconcileResult, reconcile_orphan_siblings
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata

    att = vault_root / "Projects" / "Iota" / "attachment"
    att.mkdir(parents=True, exist_ok=True)
    sum_dir = att / ".summaries"
    sum_dir.mkdir(parents=True, exist_ok=True)

    sibling = sum_dir / "human.md"
    meta = NoteMetadata(
        attachment_path="Projects/Iota/attachment/nonexistent.pdf",
        type="attachment-summary",
        updated_by_human=True,
    )
    result = write_note(sibling, "Human-edited summary.", meta, actor="human")
    assert isinstance(result, Success)

    initial = ReconcileResult()
    match await reconcile_orphan_siblings(initial, pipeline_ctx):
        case Success(value=r):
            assert r.orphans_cleaned == 0
        case Failure(error=e):
            pytest.fail(f"Stage 4 failed: {e}")

    # Human-edited sibling must not be deleted
    assert sibling.exists(), "Human-edited sibling must NOT be deleted"


# ---------------------------------------------------------------------------
# End-to-end — reconcile() pipeline runner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_runs_all_five_stages(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """reconcile() chains all 5 stages and returns ReconcileResult with counts.

    Stages covered:
      1. reconcile_paths      — delete on disk → removed from DB
      2. reconcile_orphan_binaries — (no orphan binaries; count stays 0)
      3. reconcile_stale_binaries  — (no stale binaries; count stays 0)
      4. reconcile_orphan_siblings — (no orphan siblings; count stays 0)
      5. reconcile_stale_tags      — tags_updated is an int
    """
    from pipelines.reconcile import ReconcileResult, reconcile
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    import storage.documents as documents

    # Set up: one note in DB that we'll delete on disk (Stage 1 detects delete)
    note = vault_root / "Projects" / "Kappa" / "stale_note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    result = write_note(note, "stale note content", NoteMetadata(), actor="ai")
    assert isinstance(result, Success)
    documents.upsert(result.value, db_path=db_path)

    vp = "Projects/Kappa/stale_note.md"
    match documents.get_by_path(vp, db_path=db_path):
        case Success(value=row):
            assert row is not None

    # Delete it from disk
    note.unlink()

    # Mock capture_file for Stages 2 and 3 (prevents real LLM calls)
    mock_capture = AsyncMock(return_value=Success(MagicMock()))
    monkeypatch.setattr("pipelines.reconcile.capture_file", mock_capture)

    match await reconcile(pipeline_ctx):
        case Success(value=r):
            assert isinstance(r, ReconcileResult)
            # Stage 1 should have reconciled the deleted note
            assert r.paths_reconciled >= 1, f"Expected paths_reconciled >= 1, got {r.paths_reconciled}"
            # All stage counters should be ints
            assert isinstance(r.new_captures, int)
            assert isinstance(r.restale_count, int)
            assert isinstance(r.orphans_cleaned, int)
            assert isinstance(r.tags_updated, int)
        case Failure(error=e):
            pytest.fail(f"reconcile() failed: {e}")

    # Verify the deleted note is gone from DB
    match documents.get_by_path(vp, db_path=db_path):
        case Success(value=row):
            assert row is None, f"Deleted note {vp} should be gone from DB"


# ---------------------------------------------------------------------------
# Stage 5 — reconcile_stale_tags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_stale_tags_removes_stale_domain_tag(
    vault_root, db_path, pipeline_ctx
):
    """Stage 5: note with domain/OldDomain tag (folder deleted) → tag removed."""
    from pipelines.reconcile import ReconcileResult, reconcile_stale_tags
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    from vault.indexer import scan_vault
    from core.result import Success

    # Note in Projects/Alpha/ with a stale domain tag (OldDomain folder does not exist)
    note_path = vault_root / "Projects" / "Alpha" / "note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    meta = NoteMetadata(tags=["domain/OldDomain", "some-other-tag"])
    result = write_note(note_path, "hello", meta, actor="ai")
    assert isinstance(result, Success)

    match scan_vault(vault_root):
        case Success(entries):
            pass
        case f:
            pytest.fail(f"scan_vault failed: {f}")

    initial = ReconcileResult()
    match await reconcile_stale_tags(initial, pipeline_ctx, entries):
        case Success(value=r):
            assert r.tags_updated >= 1, "Expected at least one tag update"
        case f:
            pytest.fail(f"Stage 5 failed: {f}")

    # Verify stale tag is gone from the written note
    from vault.reader import read_note
    match read_note(note_path):
        case Success(note):
            assert "domain/OldDomain" not in note.metadata.tags, \
                "Stale domain tag should have been removed"
            assert "some-other-tag" in note.metadata.tags, \
                "Non-domain tags should be preserved"


@pytest.mark.asyncio
async def test_reconcile_stale_tags_adds_missing_domain_tag(
    vault_root, db_path, pipeline_ctx
):
    """Stage 5: note in Domain/Engineering/ missing domain/Engineering tag → tag added."""
    from pipelines.reconcile import ReconcileResult, reconcile_stale_tags
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    from vault.indexer import scan_vault
    from core.result import Success

    # Create Engineering domain folder
    eng_dir = vault_root / "Domain" / "Engineering"
    eng_dir.mkdir(parents=True, exist_ok=True)

    note_path = eng_dir / "spec.md"
    # Note has no domain tag
    meta = NoteMetadata(tags=["some-tag"])
    result = write_note(note_path, "spec content", meta, actor="ai")
    assert isinstance(result, Success)

    match scan_vault(vault_root):
        case Success(entries):
            pass
        case f:
            pytest.fail(f"scan_vault failed: {f}")

    initial = ReconcileResult()
    match await reconcile_stale_tags(initial, pipeline_ctx, entries):
        case Success(value=r):
            assert r.tags_updated >= 1
        case f:
            pytest.fail(f"Stage 5 failed: {f}")

    from vault.reader import read_note
    match read_note(note_path):
        case Success(note):
            assert "domain/Engineering" in note.metadata.tags, \
                "domain/Engineering tag should have been added"


@pytest.mark.asyncio
async def test_reconcile_stale_tags_sets_project_field(
    vault_root, db_path, pipeline_ctx
):
    """Stage 5: note in Projects/Alpha/ → project: Alpha set on frontmatter."""
    from pipelines.reconcile import ReconcileResult, reconcile_stale_tags
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    from vault.indexer import scan_vault
    from core.result import Success

    note_path = vault_root / "Projects" / "Alpha" / "note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    # Note has no project field
    meta = NoteMetadata(project=None)
    result = write_note(note_path, "project note", meta, actor="ai")
    assert isinstance(result, Success)

    match scan_vault(vault_root):
        case Success(entries):
            pass
        case f:
            pytest.fail(f"scan_vault failed: {f}")

    initial = ReconcileResult()
    match await reconcile_stale_tags(initial, pipeline_ctx, entries):
        case Success(value=r):
            assert r.tags_updated >= 1
        case f:
            pytest.fail(f"Stage 5 failed: {f}")

    from vault.reader import read_note
    match read_note(note_path):
        case Success(note):
            assert note.metadata.project == "Alpha", \
                f"Expected project=Alpha, got {note.metadata.project}"


@pytest.mark.asyncio
async def test_reconcile_stale_tags_inbox_note_unchanged(
    vault_root, db_path, pipeline_ctx
):
    """Stage 5: note in inbox/ → project: unchanged (inbox location does not set project)."""
    from pipelines.reconcile import ReconcileResult, reconcile_stale_tags
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    from vault.indexer import scan_vault
    from core.result import Success

    note_path = vault_root / "inbox" / "drop.md"
    original_project = "SomeOtherProject"
    meta = NoteMetadata(project=original_project, tags=[])
    result = write_note(note_path, "inbox note", meta, actor="ai")
    assert isinstance(result, Success)

    match scan_vault(vault_root):
        case Success(entries):
            pass
        case f:
            pytest.fail(f"scan_vault failed: {f}")

    initial = ReconcileResult()
    match await reconcile_stale_tags(initial, pipeline_ctx, entries):
        case Success(value=r):
            # No dirty change — project should be untouched
            pass
        case f:
            pytest.fail(f"Stage 5 failed: {f}")

    from vault.reader import read_note
    match read_note(note_path):
        case Success(note):
            assert note.metadata.project == original_project, \
                "inbox note project field should be unchanged"


@pytest.mark.asyncio
async def test_reconcile_stale_tags_skips_human_edited(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """Stage 5: note with updated_by_human=True → write_note skips silently.

    Expects:
    - tags_updated NOT incremented
    - NO warning logged (the human-lock path is expected, not a warning condition)
    """
    from pipelines.reconcile import ReconcileResult, reconcile_stale_tags
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    from vault.indexer import scan_vault
    from core.result import Success
    import pipelines.reconcile as reconcile_mod

    # Create Engineering domain so domain/Engineering would normally be added
    eng_dir = vault_root / "Domain" / "Engineering"
    eng_dir.mkdir(parents=True, exist_ok=True)

    note_path = eng_dir / "human_note.md"
    # Human-edited note, missing domain tag (would normally be dirty)
    meta = NoteMetadata(tags=[], updated_by_human=True)
    result = write_note(note_path, "human content", meta, actor="human")
    assert isinstance(result, Success)

    match scan_vault(vault_root):
        case Success(entries):
            pass
        case f:
            pytest.fail(f"scan_vault failed: {f}")

    warning_calls = []
    original_warning = reconcile_mod._log.warning

    def capturing_warning(msg, *args, **kwargs):
        warning_calls.append((msg, args, kwargs))
        return original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(reconcile_mod._log, "warning", capturing_warning)

    initial = ReconcileResult()
    match await reconcile_stale_tags(initial, pipeline_ctx, entries):
        case Success(value=r):
            assert r.tags_updated == 0, \
                "Human-edited note should not increment tags_updated"
        case f:
            pytest.fail(f"Stage 5 failed: {f}")

    # Verify no warning was emitted for the human-edited note (it's expected — not an error)
    stale_tag_warnings = [
        (msg, args) for msg, args, _ in warning_calls
        if "reconcile_stale_tags" in msg
    ]
    assert len(stale_tag_warnings) == 0, \
        f"Expected no reconcile_stale_tags warnings for human-edited note, got: {stale_tag_warnings}"


@pytest.mark.asyncio
async def test_reconcile_stale_tags_load_valid_domains_called_once(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """Stage 5: load_valid_domains called once regardless of number of notes."""
    from pipelines.reconcile import ReconcileResult, reconcile_stale_tags
    from vault.writer import write_note
    from vault.frontmatter import NoteMetadata
    from vault.indexer import scan_vault
    from core.result import Success
    from unittest.mock import patch

    # Create a few notes in different locations
    for name in ["a.md", "b.md", "c.md"]:
        p = vault_root / "inbox" / name
        write_note(p, "content", NoteMetadata(), actor="ai")

    match scan_vault(vault_root):
        case Success(entries):
            pass
        case f:
            pytest.fail(f"scan_vault failed: {f}")

    call_count = 0
    original_load = None

    import vault.paths as vp_module
    original_load = vp_module.load_valid_domains

    def counting_load(vault_root_arg):
        nonlocal call_count
        call_count += 1
        return original_load(vault_root_arg)

    # load_valid_domains is imported inside the function body (from vault.paths import ...),
    # so we patch the source module attribute. The top-level module patch would be a no-op.
    monkeypatch.setattr(vp_module, "load_valid_domains", counting_load)

    initial = ReconcileResult()
    match await reconcile_stale_tags(initial, pipeline_ctx, entries):
        case Success():
            pass
        case f:
            pytest.fail(f"Stage 5 failed: {f}")

    assert call_count == 1, f"load_valid_domains should be called once, called {call_count} times"
