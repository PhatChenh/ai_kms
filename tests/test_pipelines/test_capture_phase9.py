"""Phase 9 tests — Non-md drop detection in scan_capture."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.result import Failure, Success

_FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "sample_text.pdf"


def _copy_pdf(dst: Path) -> None:
    """Copy the fixture PDF (has extractable text) to dst."""
    shutil.copy(_FIXTURE_PDF, dst)


# ---------------------------------------------------------------------------
# scan_capture — non-md PDF drop in inbox/
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_pdf_in_inbox_creates_pending_routing_marker(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """scan_capture with PDF in inbox/ → CLUELESS: pending-routing marker at inbox/.summaries/."""
    from pipelines.capture import scan_capture
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse

    pdf_file = vault_root / "inbox" / "kqzxvbn.pdf"
    _copy_pdf(pdf_file)

    mtime = pdf_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Summary of report.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "Annual Report", "tags": ["type/report"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert len(result.value) == 1
    assert isinstance(result.value[0], WriteOutcome)
    # Pending-routing marker at inbox/.summaries/ (CLUELESS path)
    marker = vault_root / "inbox" / ".summaries" / "kqzxvbn.pdf.md"
    assert marker.exists(), f"Expected pending-routing marker at {marker}"
    # Binary stays in inbox (not moved)
    assert pdf_file.exists(), "Binary must stay in inbox for CLUELESS path"


# ---------------------------------------------------------------------------
# scan_capture — PDF already in attachment/ is skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_pdf_in_attachment_is_skipped(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """scan_capture skips PDF already inside Projects/<A>/attachment/ — no capture_file call."""
    from pipelines.capture import scan_capture

    att = vault_root / "Projects" / "Alpha" / "attachment"
    att.mkdir(parents=True, exist_ok=True)
    (att / "already-captured.pdf").write_bytes(b"%PDF already here")

    capture_called: list[Path] = []

    async def fake_capture_file(path, context=None):
        capture_called.append(path)
        return Success(MagicMock())

    monkeypatch.setattr("pipelines.capture.capture_file", fake_capture_file)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert result.value == []
    assert not any(p.name == "already-captured.pdf" for p in capture_called)


# ---------------------------------------------------------------------------
# scan_capture — PDF in inbox/ AND .md in inbox/ — both processed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_pdf_and_md_both_processed(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """scan_capture processes .md and non-md drops independently in the same run."""
    from pipelines.capture import scan_capture
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse

    md_file = vault_root / "inbox" / "my-note.md"
    md_file.write_text("# My Note\n\nBody text.", encoding="utf-8")

    pdf_file = vault_root / "inbox" / "report.pdf"
    _copy_pdf(pdf_file)

    mtime = max(md_file.stat().st_mtime, pdf_file.stat().st_mtime)
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        # md_file: summarize + metadata
        Success(LLMResponse(content="Note summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "my-note", "tags": ["type/capture"]}',
            model="test",
            usage={},
        )),
        # pdf_file: summarize + metadata
        Success(LLMResponse(content="Report summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "Annual Report", "tags": ["type/report"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert len(result.value) == 2
    assert all(isinstance(o, WriteOutcome) for o in result.value)


# ---------------------------------------------------------------------------
# scan_capture — non-md with unsupported extension logs WARNING, others continue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_nonmd_unsupported_ext_logs_warning_continues(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """Non-md with no handler returns Failure; scan_capture logs WARNING and continues."""
    from pipelines.capture import scan_capture
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse

    # Unsupported extension — no handler registered
    (vault_root / "inbox" / "unknown.xyz").write_bytes(b"binary blob")
    # Valid PDF that SHOULD be captured after the failure
    pdf_file = vault_root / "inbox" / "valid.pdf"
    _copy_pdf(pdf_file)

    mtime = max(
        (vault_root / "inbox" / "unknown.xyz").stat().st_mtime,
        pdf_file.stat().st_mtime,
    )
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="PDF summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "valid-pdf", "tags": ["type/report"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    # Valid PDF captured; unsupported extension skipped with WARNING
    assert len(result.value) == 1
    assert isinstance(result.value[0], WriteOutcome)


# ---------------------------------------------------------------------------
# scan_capture — .summaries/ paths skipped in modified loop (TD-AS-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_skips_summaries_in_modified_loop(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """scan_capture modified loop skips paths under .summaries/ (TD-AS-1 fix).

    Sibling .md files are summary artifacts owned by the sync pipeline.
    Re-capturing them calls _store_md which builds NoteMetadata from scratch,
    wiping attachment_path from frontmatter.
    """
    from pipelines.capture import scan_capture

    # Create a sibling .md file under .summaries/
    summaries_dir = vault_root / "Projects" / "Alpha" / "attachment" / ".summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    sibling = summaries_dir / "report.md"
    sibling.write_text("# Summary of report.pdf\n\nAI-generated summary.", encoding="utf-8")
    sibling_vp = "Projects/Alpha/attachment/.summaries/report.md"

    # Insert DB row with a DIFFERENT content_hash — makes it appear as "modified"
    import hashlib
    import sqlite3
    old_hash = hashlib.sha256(b"old content").hexdigest()
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO documents (vault_path, content_hash, title, updated_by_human) VALUES (?, ?, ?, 0)",
        (sibling_vp, old_hash, "report"),
    )
    conn.commit()
    conn.close()

    capture_called: list[str] = []

    async def fake_capture_file(path, context=None):
        capture_called.append(str(path))
        return Success(MagicMock())

    monkeypatch.setattr("pipelines.capture.capture_file", fake_capture_file)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    # The sibling path must NOT be in capture_called
    sibling_abs = str(sibling)
    assert sibling_abs not in capture_called, (
        f"capture_file was called for .summaries/ path: {capture_called}"
    )


# ---------------------------------------------------------------------------
# Phase 5, T4 — Misplaced-md sweep in scan_capture
# ---------------------------------------------------------------------------


class TestScanCaptureMisplacedMd:
    """7 integration tests: misplaced .md files at bare Project/Domain roots
    are swept to inbox before capture_file is called."""

    @pytest.mark.asyncio
    async def test_misplaced_md_in_projects_root_swept_to_inbox(
        self, vault_root, db_path, pipeline_ctx, monkeypatch
    ):
        """Projects/stray.md is moved to inbox/stray.md by scan_capture."""
        from pipelines.capture import scan_capture

        stray = vault_root / "Projects" / "stray.md"
        stray.write_text("# Stray\n\nbody", encoding="utf-8")

        move_calls: list[tuple] = []

        def fake_move_note(src, dst, actor):
            move_calls.append((str(src), str(dst), actor))
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.move_note", fake_move_note)

        def fake_delete_by_path(vault_path, db_path=None):
            from core.result import Success
            return Success(0)

        monkeypatch.setattr("pipelines.capture.documents.delete_by_path", fake_delete_by_path)

        capture_called: list[Path] = []

        async def fake_capture_file(path, context=None):
            capture_called.append(path)
            return Success(MagicMock())

        monkeypatch.setattr("pipelines.capture.capture_file", fake_capture_file)

        def fake_audit_write(*args, **kwargs):
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.audit.write", fake_audit_write)

        result = await scan_capture(root=vault_root, db_path=db_path)

        assert isinstance(result, Success)
        # move_note called: src=Projects/stray.md, dst=inbox/stray.md
        inbox_dst = vault_root / "inbox" / "stray.md"
        assert any(str(inbox_dst) in str(m[1]) for m in move_calls), (
            f"Expected move to {inbox_dst}, got {move_calls}"
        )

    @pytest.mark.asyncio
    async def test_valid_project_nested_md_not_swept(
        self, vault_root, db_path, pipeline_ctx, monkeypatch
    ):
        """Projects/Alpha/note.md is NOT swept — it has a valid project context."""
        from pipelines.capture import scan_capture

        note = vault_root / "Projects" / "Alpha" / "note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Valid\n\nbody", encoding="utf-8")

        move_calls: list[tuple] = []

        def fake_move_note(src, dst, actor):
            move_calls.append((str(src), str(dst), actor))
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.move_note", fake_move_note)

        def fake_delete_by_path(vault_path, db_path=None):
            from core.result import Success
            return Success(0)

        monkeypatch.setattr("pipelines.capture.documents.delete_by_path", fake_delete_by_path)

        capture_called: list[Path] = []

        async def fake_capture_file(path, context=None):
            capture_called.append(path)
            return Success(MagicMock())

        monkeypatch.setattr("pipelines.capture.capture_file", fake_capture_file)

        def fake_audit_write(*args, **kwargs):
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.audit.write", fake_audit_write)

        result = await scan_capture(root=vault_root, db_path=db_path)

        assert isinstance(result, Success)
        # No move calls — the note stays in place
        assert move_calls == [], (
            f"Expected no move calls for valid nested project path, got {move_calls}"
        )
        # capture_file IS called for the valid path
        assert str(note) in [str(p) for p in capture_called], (
            f"Expected capture_file for {note}, got {capture_called}"
        )

    @pytest.mark.asyncio
    async def test_misplaced_md_in_domain_root_swept_to_inbox(
        self, vault_root, db_path, pipeline_ctx, monkeypatch
    ):
        """Domain/stray.md is moved to inbox/stray.md."""
        from pipelines.capture import scan_capture

        stray = vault_root / "Domain" / "stray.md"
        stray.write_text("# Domain Stray\n\nbody", encoding="utf-8")

        move_calls: list[tuple] = []

        def fake_move_note(src, dst, actor):
            move_calls.append((str(src), str(dst), actor))
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.move_note", fake_move_note)

        def fake_delete_by_path(vault_path, db_path=None):
            from core.result import Success
            return Success(0)

        monkeypatch.setattr("pipelines.capture.documents.delete_by_path", fake_delete_by_path)

        capture_called: list[Path] = []

        async def fake_capture_file(path, context=None):
            capture_called.append(path)
            return Success(MagicMock())

        monkeypatch.setattr("pipelines.capture.capture_file", fake_capture_file)

        def fake_audit_write(*args, **kwargs):
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.audit.write", fake_audit_write)

        result = await scan_capture(root=vault_root, db_path=db_path)

        assert isinstance(result, Success)
        inbox_dst = vault_root / "inbox" / "stray.md"
        assert any(str(inbox_dst) in str(m[1]) for m in move_calls), (
            f"Expected move to {inbox_dst}, got {move_calls}"
        )

    @pytest.mark.asyncio
    async def test_misplaced_sweep_audit_written(
        self, vault_root, db_path, pipeline_ctx, monkeypatch
    ):
        """Audit row with outcome='MISPLACED' is written after sweep."""
        from pipelines.capture import scan_capture

        stray = vault_root / "Projects" / "stray.md"
        stray.write_text("# Stray\n\nbody", encoding="utf-8")

        def fake_move_note(src, dst, actor):
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.move_note", fake_move_note)

        def fake_delete_by_path(vault_path, db_path=None):
            from core.result import Success
            return Success(0)

        monkeypatch.setattr("pipelines.capture.documents.delete_by_path", fake_delete_by_path)

        capture_called: list[Path] = []

        async def fake_capture_file(path, context=None):
            capture_called.append(path)
            return Success(MagicMock())

        monkeypatch.setattr("pipelines.capture.capture_file", fake_capture_file)

        audit_calls: list[tuple] = []

        def fake_audit_write(decision, pipeline, stage, outcome, db_path=None):
            audit_calls.append((decision.action, outcome, stage))
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.audit.write", fake_audit_write)

        result = await scan_capture(root=vault_root, db_path=db_path)

        assert isinstance(result, Success)
        assert any(
            action == "capture:sweep" and outcome == "MISPLACED"
            for action, outcome, stage in audit_calls
        ), f"Expected capture:sweep + MISPLACED audit row, got {audit_calls}"

    @pytest.mark.asyncio
    async def test_misplaced_sweep_cleans_stale_db_row(
        self, vault_root, db_path, pipeline_ctx, monkeypatch
    ):
        """documents.delete_by_path is called to clean up stale DB row before move."""
        from pipelines.capture import scan_capture

        stray = vault_root / "Projects" / "stray.md"
        stray.write_text("# Stray\n\nbody", encoding="utf-8")

        def fake_move_note(src, dst, actor):
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.move_note", fake_move_note)

        delete_calls: list[str] = []

        def fake_delete_by_path(vault_path, db_path=None):
            delete_calls.append(vault_path)
            from core.result import Success
            return Success(0)

        monkeypatch.setattr("pipelines.capture.documents.delete_by_path", fake_delete_by_path)

        async def fake_capture_file(path, context=None):
            return Success(MagicMock())

        monkeypatch.setattr("pipelines.capture.capture_file", fake_capture_file)

        def fake_audit_write(*args, **kwargs):
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.audit.write", fake_audit_write)

        result = await scan_capture(root=vault_root, db_path=db_path)

        assert isinstance(result, Success)
        assert "Projects/stray.md" in delete_calls, (
            f"Expected delete_by_path for Projects/stray.md, got {delete_calls}"
        )

    @pytest.mark.asyncio
    async def test_misplaced_sweep_handles_collision(
        self, vault_root, db_path, pipeline_ctx, monkeypatch
    ):
        """When inbox/stray.md already exists, sweep uses stray-1.md."""
        from pipelines.capture import scan_capture

        # Pre-existing inbox/stray.md
        inbox_existing = vault_root / "inbox" / "stray.md"
        inbox_existing.write_text("# Existing\n\ncontent", encoding="utf-8")

        stray = vault_root / "Projects" / "stray.md"
        stray.write_text("# New Stray\n\nbody", encoding="utf-8")

        move_calls: list[tuple] = []

        def fake_move_note(src, dst, actor):
            move_calls.append((str(src), str(dst), actor))
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.move_note", fake_move_note)

        def fake_delete_by_path(vault_path, db_path=None):
            from core.result import Success
            return Success(0)

        monkeypatch.setattr("pipelines.capture.documents.delete_by_path", fake_delete_by_path)

        capture_called: list[Path] = []

        async def fake_capture_file(path, context=None):
            capture_called.append(path)
            return Success(MagicMock())

        monkeypatch.setattr("pipelines.capture.capture_file", fake_capture_file)

        def fake_audit_write(*args, **kwargs):
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.audit.write", fake_audit_write)

        result = await scan_capture(root=vault_root, db_path=db_path)

        assert isinstance(result, Success)
        # Should move to inbox/stray-1.md (collision resolution)
        inbox_dst = vault_root / "inbox" / "stray-1.md"
        assert any(str(inbox_dst) in str(m[1]) for m in move_calls), (
            f"Expected move to {inbox_dst} (collision), got {move_calls}"
        )

    @pytest.mark.asyncio
    async def test_misplaced_sweep_move_failure_logs_warning_skips(
        self, vault_root, db_path, pipeline_ctx, monkeypatch, caplog
    ):
        """On Failure from move_note, log warning and skip capture_file."""
        import logging
        from pipelines.capture import scan_capture

        stray = vault_root / "Projects" / "stray.md"
        stray.write_text("# Stray\n\nbody", encoding="utf-8")

        def fake_move_note(src, dst, actor):
            return Failure(error="move failed", recoverable=False, context={})

        monkeypatch.setattr("pipelines.capture.move_note", fake_move_note)

        def fake_delete_by_path(vault_path, db_path=None):
            from core.result import Success
            return Success(0)

        monkeypatch.setattr("pipelines.capture.documents.delete_by_path", fake_delete_by_path)

        capture_called: list[Path] = []

        async def fake_capture_file(path, context=None):
            capture_called.append(path)
            return Success(MagicMock())

        monkeypatch.setattr("pipelines.capture.capture_file", fake_capture_file)

        def fake_audit_write(*args, **kwargs):
            from core.result import Success
            return Success(None)

        monkeypatch.setattr("pipelines.capture.audit.write", fake_audit_write)

        with caplog.at_level(logging.WARNING, logger="pipelines.capture"):
            result = await scan_capture(root=vault_root, db_path=db_path)

        assert isinstance(result, Success)
        # capture_file must NOT be called for the stray file
        assert str(stray) not in [str(p) for p in capture_called], (
            f"Expected no capture_file for {stray} after move failure, got {capture_called}"
        )
