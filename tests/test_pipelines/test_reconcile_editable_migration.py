"""
tests/test_pipelines/test_reconcile_editable_migration.py

Unit tests for Phase 10 Stage 7: reconcile_editable_migration.

Tests both migration directions:
- Editable → No-edit (ext added to no_edit_extensions → binary moves to attachment/)
- No-edit → Editable (ext removed from no_edit_extensions → binary moves to root)

All collaborator patches target pipelines.reconcile.* per TD-033.
CONFIG is never imported at module scope.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.config import VaultConfig
from core.pipeline import PipelineContext


def _make_ctx(tmp_path: Path, **overrides) -> tuple[PipelineContext, VaultConfig]:
    """Build a PipelineContext with a temp vault root and optional overrides."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir(exist_ok=True)
    db_path = tmp_path / "test.db"

    from core.config import MainConfig

    vault_cfg = VaultConfig(root=vault_root)
    # Build a minimal MainConfig-like mock
    mock_config = MagicMock(spec=MainConfig)
    mock_config.vault = vault_cfg
    mock_config.database = MagicMock()
    mock_config.database.path = db_path

    ctx = PipelineContext(
        config=mock_config,
        db_path=db_path,
        correlation_id="test-cid",
        taxonomy=None,
    )
    return ctx, vault_cfg


# ---------------------------------------------------------------------------
# T1 — Editable → No-edit: binary + sibling migrate to attachment/
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_editable_to_no_edit_migration(tmp_path: Path, monkeypatch):
    """When .xlsx is no-edit per config, binary at project root migrates to attachment/."""
    ctx, vault_cfg = _make_ctx(tmp_path)

    # Override no_edit_extensions to include .xlsx
    vault_cfg.no_edit_extensions = [".pdf", ".png", ".xlsx"]

    # Create binary at project root (editable location)
    proj_dir = vault_cfg.projects_path / "Alpha"
    proj_dir.mkdir(parents=True, exist_ok=True)
    binary = proj_dir / "budget.xlsx"
    binary.write_bytes(b"xlsx content")

    # Create sibling at root .summaries/ (editable sibling location)
    sum_dir = proj_dir / vault_cfg.summaries_subdir
    sum_dir.mkdir(parents=True, exist_ok=True)
    sibling = sum_dir / "budget.xlsx.md"
    sibling.write_text(
        "---\ntype: attachment-summary\nattachment_path: Projects/Alpha/budget.xlsx\n---\n# Summary\n",
        encoding="utf-8",
    )

    move_attachment_calls: list = []
    move_note_calls: list = []
    rename_doc_calls: list = []
    audit_calls: list = []

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        meta = NoteMetadata(
            type="attachment-summary",
            attachment_path="Projects/Alpha/budget.xlsx",
            source_hash="abc123",
        )
        return Success(Note(path=path, content="# Summary\n", metadata=meta, content_hash="abc"))

    def fake_move_attachment(s, d):
        move_attachment_calls.append((str(s), str(d)))
        from core.result import Success
        return Success(d)

    def fake_move_note(s, d, actor):
        move_note_calls.append((str(s), str(d), actor))
        from core.result import Success
        return Success(None)

    def fake_write_note(path, body, metadata, actor):
        from core.result import Success
        return Success(MagicMock())

    def fake_rename_doc(old, new, db_path=None):
        rename_doc_calls.append((old, new))
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append(kwargs)
        from core.result import Success
        return Success(None)

    monkeypatch.setattr("vault.reader.read_note", fake_read_note)
    monkeypatch.setattr("vault.writer.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.writer.move_note", fake_move_note)
    monkeypatch.setattr("vault.writer.write_note", fake_write_note)
    monkeypatch.setattr("storage.documents.rename", fake_rename_doc)
    monkeypatch.setattr("pipelines.reconcile.audit_write", fake_audit_write)

    from pipelines.reconcile import ReconcileResult, reconcile_editable_migration

    result = await reconcile_editable_migration(ReconcileResult(), ctx)

    assert result.is_success()
    assert result.value.editable_migrations == 1

    # Binary moved to attachment/ (no-edit destination)
    assert len(move_attachment_calls) == 1
    src, dst = move_attachment_calls[0]
    assert dst.endswith(f"attachment/budget.xlsx")

    # Sibling moved to attachment/.summaries/
    assert len(move_note_calls) == 1
    _, dst_sibling, _ = move_note_calls[0]
    assert ".summaries/budget.xlsx.md" in dst_sibling

    # DB renamed
    assert len(rename_doc_calls) == 1

    # Audit row
    assert len(audit_calls) == 1
    assert audit_calls[0]["outcome"] == "EDITABLE_MIGRATED"


# ---------------------------------------------------------------------------
# T2 — No-edit → Editable: binary + sibling migrate to project root
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_edit_to_editable_migration(tmp_path: Path, monkeypatch):
    """When .docx is NOT no-edit, binary in attachment/ migrates to project root."""
    ctx, vault_cfg = _make_ctx(tmp_path)

    # Default no_edit_extensions: [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]
    # .docx is NOT in the list → editable

    # Create binary in attachment/ (no-edit location, but should be editable)
    att_dir = vault_cfg.projects_path / "Alpha" / vault_cfg.attachment_dir
    att_dir.mkdir(parents=True, exist_ok=True)
    binary = att_dir / "report.docx"
    binary.write_bytes(b"docx content")

    # Create sibling at attachment/.summaries/
    sum_dir = att_dir / vault_cfg.summaries_subdir
    sum_dir.mkdir(parents=True, exist_ok=True)
    sibling = sum_dir / "report.docx.md"
    sibling.write_text(
        "---\ntype: attachment-summary\nattachment_path: Projects/Alpha/attachment/report.docx\n---\n# Summary\n",
        encoding="utf-8",
    )

    move_attachment_calls: list = []
    move_note_calls: list = []
    audit_calls: list = []

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        meta = NoteMetadata(
            type="attachment-summary",
            attachment_path="Projects/Alpha/attachment/report.docx",
            source_hash="def456",
        )
        return Success(Note(path=path, content="# Summary\n", metadata=meta, content_hash="def"))

    def fake_move_attachment(s, d):
        move_attachment_calls.append((str(s), str(d)))
        from core.result import Success
        return Success(d)

    def fake_move_note(s, d, actor):
        move_note_calls.append((str(s), str(d), actor))
        from core.result import Success
        return Success(None)

    def fake_write_note(path, body, metadata, actor):
        from core.result import Success
        return Success(MagicMock())

    def fake_rename_doc(old, new, db_path=None):
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append(kwargs)
        from core.result import Success
        return Success(None)

    monkeypatch.setattr("vault.reader.read_note", fake_read_note)
    monkeypatch.setattr("vault.writer.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.writer.move_note", fake_move_note)
    monkeypatch.setattr("vault.writer.write_note", fake_write_note)
    monkeypatch.setattr("storage.documents.rename", fake_rename_doc)
    monkeypatch.setattr("pipelines.reconcile.audit_write", fake_audit_write)

    from pipelines.reconcile import ReconcileResult, reconcile_editable_migration

    result = await reconcile_editable_migration(ReconcileResult(), ctx)

    assert result.is_success()
    assert result.value.editable_migrations == 1

    # Binary moved to project root (editable destination)
    assert len(move_attachment_calls) == 1
    src, dst = move_attachment_calls[0]
    assert dst.endswith("Alpha/report.docx")

    # Sibling moved to root .summaries/
    assert len(move_note_calls) == 1
    _, dst_sibling, _ = move_note_calls[0]
    assert "Alpha/.summaries/report.docx.md" in dst_sibling

    # Audit row
    assert len(audit_calls) == 1
    assert audit_calls[0]["outcome"] == "EDITABLE_MIGRATED"


# ---------------------------------------------------------------------------
# T3 — Already in correct location → no migration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_correct_location_no_migration(tmp_path: Path, monkeypatch):
    """Binary already at resolve_placement's final_dir → needs_move=False, skipped."""
    ctx, vault_cfg = _make_ctx(tmp_path)

    # .pdf is no-edit → should be in attachment/
    att_dir = vault_cfg.projects_path / "Alpha" / vault_cfg.attachment_dir
    att_dir.mkdir(parents=True, exist_ok=True)
    binary = att_dir / "report.pdf"
    binary.write_bytes(b"pdf content")

    sum_dir = att_dir / vault_cfg.summaries_subdir
    sum_dir.mkdir(parents=True, exist_ok=True)
    sibling = sum_dir / "report.pdf.md"
    sibling.write_text(
        "---\ntype: attachment-summary\nattachment_path: Projects/Alpha/attachment/report.pdf\n---\n# Summary\n",
        encoding="utf-8",
    )

    move_attachment_calls: list = []

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        meta = NoteMetadata(
            type="attachment-summary",
            attachment_path="Projects/Alpha/attachment/report.pdf",
        )
        return Success(Note(path=path, content="# Summary\n", metadata=meta, content_hash="abc"))

    def fake_move_attachment(s, d):
        move_attachment_calls.append((str(s), str(d)))
        from core.result import Success
        return Success(d)

    monkeypatch.setattr("vault.reader.read_note", fake_read_note)
    monkeypatch.setattr("vault.writer.move_attachment", fake_move_attachment)

    from pipelines.reconcile import ReconcileResult, reconcile_editable_migration

    result = await reconcile_editable_migration(ReconcileResult(), ctx)

    assert result.is_success()
    assert result.value.editable_migrations == 0
    assert len(move_attachment_calls) == 0


# ---------------------------------------------------------------------------
# T4 — Domain symmetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_domain_editable_to_no_edit_migration(tmp_path: Path, monkeypatch):
    """Domain/<D>/ editable binary migrates to attachment/ when extension becomes no-edit."""
    ctx, vault_cfg = _make_ctx(tmp_path)

    vault_cfg.no_edit_extensions = [".pdf", ".png", ".xlsx"]

    # Binary at domain root (editable location, but .xlsx is now no-edit)
    dom_dir = vault_cfg.domain_path / "Finance"
    dom_dir.mkdir(parents=True, exist_ok=True)
    binary = dom_dir / "ledger.xlsx"
    binary.write_bytes(b"ledger")

    sum_dir = dom_dir / vault_cfg.summaries_subdir
    sum_dir.mkdir(parents=True, exist_ok=True)
    sibling = sum_dir / "ledger.xlsx.md"
    sibling.write_text(
        "---\ntype: attachment-summary\nattachment_path: Domain/Finance/ledger.xlsx\n---\n# Ledger\n",
        encoding="utf-8",
    )

    move_attachment_calls: list = []
    audit_calls: list = []

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        meta = NoteMetadata(
            type="attachment-summary",
            attachment_path="Domain/Finance/ledger.xlsx",
        )
        return Success(Note(path=path, content="# Ledger\n", metadata=meta, content_hash="xyz"))

    def fake_move_attachment(s, d):
        move_attachment_calls.append((str(s), str(d)))
        from core.result import Success
        return Success(d)

    def fake_move_note(s, d, actor):
        from core.result import Success
        return Success(None)

    def fake_write_note(path, body, metadata, actor):
        from core.result import Success
        return Success(MagicMock())

    def fake_rename_doc(old, new, db_path=None):
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append(kwargs)
        from core.result import Success
        return Success(None)

    monkeypatch.setattr("vault.reader.read_note", fake_read_note)
    monkeypatch.setattr("vault.writer.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.writer.move_note", fake_move_note)
    monkeypatch.setattr("vault.writer.write_note", fake_write_note)
    monkeypatch.setattr("storage.documents.rename", fake_rename_doc)
    monkeypatch.setattr("pipelines.reconcile.audit_write", fake_audit_write)

    from pipelines.reconcile import ReconcileResult, reconcile_editable_migration

    result = await reconcile_editable_migration(ReconcileResult(), ctx)

    assert result.is_success()
    assert result.value.editable_migrations == 1
    assert len(move_attachment_calls) == 1
    assert len(audit_calls) == 1


# ---------------------------------------------------------------------------
# T5 — updated_by_human=True → skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_updated_by_human_skipped(tmp_path: Path, monkeypatch):
    """Sibling has updated_by_human=True → no migration."""
    ctx, vault_cfg = _make_ctx(tmp_path)

    # Binary at project root (editable), .docx in no_edit_extensions
    vault_cfg.no_edit_extensions = [".pdf", ".png", ".docx"]

    proj_dir = vault_cfg.projects_path / "Alpha"
    proj_dir.mkdir(parents=True, exist_ok=True)
    binary = proj_dir / "report.docx"
    binary.write_bytes(b"content")

    sum_dir = proj_dir / vault_cfg.summaries_subdir
    sum_dir.mkdir(parents=True, exist_ok=True)
    sibling = sum_dir / "report.docx.md"
    sibling.write_text(
        "---\ntype: attachment-summary\nattachment_path: Projects/Alpha/report.docx\n---\n# Summary\n",
        encoding="utf-8",
    )

    move_calls: list = []

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        meta = NoteMetadata(
            type="attachment-summary",
            attachment_path="Projects/Alpha/report.docx",
            updated_by_human=True,
        )
        return Success(Note(path=path, content="# Summary\n", metadata=meta, content_hash="abc"))

    def fake_move_attachment(s, d):
        move_calls.append(("move_attachment", str(s), str(d)))
        from core.result import Success
        return Success(d)

    monkeypatch.setattr("vault.reader.read_note", fake_read_note)
    monkeypatch.setattr("vault.writer.move_attachment", fake_move_attachment)

    from pipelines.reconcile import ReconcileResult, reconcile_editable_migration

    result = await reconcile_editable_migration(ReconcileResult(), ctx)

    assert result.is_success()
    assert result.value.editable_migrations == 0
    assert len(move_calls) == 0


# ---------------------------------------------------------------------------
# T6 — Binary not found → skipped (Stage 4 handles orphan cleanup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_binary_not_found_skipped(tmp_path: Path, monkeypatch):
    """Sibling points to missing binary → skipped (Stage 4 handles orphans)."""
    ctx, vault_cfg = _make_ctx(tmp_path)

    # Create sibling but no binary
    att_dir = vault_cfg.projects_path / "Alpha" / vault_cfg.attachment_dir
    sum_dir = att_dir / vault_cfg.summaries_subdir
    sum_dir.mkdir(parents=True, exist_ok=True)
    sibling = sum_dir / "missing.pdf.md"
    sibling.write_text(
        "---\ntype: attachment-summary\nattachment_path: Projects/Alpha/attachment/missing.pdf\n---\n# Ghost\n",
        encoding="utf-8",
    )

    move_calls: list = []

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        meta = NoteMetadata(
            type="attachment-summary",
            attachment_path="Projects/Alpha/attachment/missing.pdf",
        )
        return Success(Note(path=path, content="# Ghost\n", metadata=meta, content_hash="abc"))

    def fake_move_attachment(s, d):
        move_calls.append(("move_attachment", str(s), str(d)))
        from core.result import Success
        return Success(d)

    monkeypatch.setattr("vault.reader.read_note", fake_read_note)
    monkeypatch.setattr("vault.writer.move_attachment", fake_move_attachment)

    from pipelines.reconcile import ReconcileResult, reconcile_editable_migration

    result = await reconcile_editable_migration(ReconcileResult(), ctx)

    assert result.is_success()
    assert result.value.editable_migrations == 0
    assert len(move_calls) == 0
