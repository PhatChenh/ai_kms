"""
tests/test_vault/test_watcher_rehome.py

Unit + integration tests for Phase 7: _handle_binary_move cross-folder re-home.

All collaborator patches target vault.watcher.* per TD-033.
CONFIG is never imported at module scope — all tests use VaultConfig(root=tmp_path).
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock


from core.config import VaultConfig
from vault.move_guard import MoveGuard
from vault.watcher import _VaultEventHandler


def _make_handler(
    tmp_path: Path,
    *,
    on_create=None,
    on_modify=None,
    on_delete=None,
    on_move=None,
    move_guard: MoveGuard | None = None,
    debounce: float = 0.01,
    binary_settle: float = 0.01,
) -> tuple[_VaultEventHandler, Path, VaultConfig]:
    root = tmp_path / "vault"
    root.mkdir(exist_ok=True)
    vault_cfg = VaultConfig(root=root)

    handler = _VaultEventHandler(
        root=root,
        vault_config=vault_cfg,
        on_create=on_create or (lambda p: None),
        on_modify=on_modify or (lambda p: None),
        on_delete=on_delete or (lambda p: None),
        on_move=on_move or (lambda s, d: None),
        debounce_seconds=debounce,
        binary_settle_seconds=binary_settle,
    )
    if move_guard is not None:
        handler._move_guard = move_guard
    return handler, root, vault_cfg


# ---------------------------------------------------------------------------
# Fake helpers for DB row
# ---------------------------------------------------------------------------


def _fake_document_row(*, updated_by_human: bool = False, **overrides):
    """Return a MagicMock that behaves like a DocumentRow."""
    from storage.documents import DocumentRow

    defaults = {
        "id": 1,
        "vault_path": "inbox/report.pdf.md",
        "title": "report.pdf",
        "summary": "A report summary.",
        "note_type": "attachment-summary",
        "confidence": 0.9,
        "created_at": "2026-06-04T00:00:00",
        "updated_at": "2026-06-04T00:00:00",
        "updated_by_human": updated_by_human,
        "content_hash": "abc123",
        "batch_id": None,
        "project": None,
        "status": None,
        "key_topics": [],
    }
    defaults.update(overrides)
    return DocumentRow(**defaults)


# ---------------------------------------------------------------------------
# T1 — no-edit PDF cross-folder re-home
# ---------------------------------------------------------------------------


def test_no_edit_pdf_cross_folder_rehome(tmp_path: Path, monkeypatch):
    """Binary moved to a different project → re-homed to attachment/, sibling
    moved, DB renamed, audit written."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    # Paths: PDF dragged from Projects/A/attachment/ to Projects/B/
    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "Projects" / "B" / "report.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    final_binary = root / "Projects" / "B" / "attachment" / "report.pdf"
    new_sibling = (
        root / "Projects" / "B" / "attachment" / ".summaries" / "report.pdf.md"
    )
    # Create old sibling on disk so exists() returns True
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n# Summary\n",
        encoding="utf-8",
    )

    # Mocks
    move_attachment_calls: list[tuple] = []
    move_note_calls: list[tuple] = []
    write_note_calls: list[tuple] = []
    rename_doc_calls: list[tuple] = []
    audit_calls: list = []

    def fake_move_attachment(s, d):
        move_attachment_calls.append((str(s), str(d)))
        from core.result import Success
        return Success(d)

    def fake_move_note(s, d, actor):
        move_note_calls.append((str(s), str(d), actor))
        from core.result import Success
        return Success(None)

    def fake_write_note(path, body, metadata, actor):
        write_note_calls.append((str(path), body, metadata.attachment_path, actor))
        from core.result import Success
        return Success(MagicMock())

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        note = Note(
            path=path,
            content="# Summary\n",
            metadata=NoteMetadata(
                attachment_path="Projects/A/attachment/report.pdf",
            ),
            content_hash="abc",
        )
        from core.result import Success
        return Success(note)

    def fake_rename_doc(old, new, db_path=None):
        rename_doc_calls.append((old, new))
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append((args, kwargs))
        from core.result import Success
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        row = _fake_document_row()
        from core.result import Success
        return Success(row)

    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.rename_doc", fake_rename_doc)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)
    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)

    # Call directly (bypass debounce)
    handler._handle_binary_move(src, dst)

    time.sleep(0.05)  # let any async/debounce settle

    # Binary moved to attachment/ (PDF is no-edit)
    assert len(move_attachment_calls) == 1, (
        f"Expected 1 move_attachment, got {len(move_attachment_calls)}"
    )
    assert move_attachment_calls[0] == (str(dst), str(final_binary))

    # Sibling moved via move_note
    assert len(move_note_calls) == 1
    assert move_note_calls[0] == (str(old_sibling), str(new_sibling), "ai")

    # DB renamed
    assert len(rename_doc_calls) == 1
    expected_old_vp = "Projects/A/attachment/.summaries/report.pdf.md"
    expected_new_vp = "Projects/B/attachment/.summaries/report.pdf.md"
    assert rename_doc_calls[0] == (expected_old_vp, expected_new_vp)

    # Audit written
    assert len(audit_calls) == 1
    audit_args, audit_kwargs = audit_calls[0]
    decision = audit_args[0]
    assert decision.action == "watcher:binary_rehome"
    assert audit_kwargs.get("outcome") == "REHOMED"


# ---------------------------------------------------------------------------
# T2 — editable docx cross-folder re-home
# ---------------------------------------------------------------------------


def test_editable_docx_cross_folder_rehome(tmp_path: Path, monkeypatch):
    """Editable binary (.docx) dragged → re-homed to visible root (not attachment),
    sibling to root .summaries/."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    src = root / "Projects" / "A" / "attachment" / "budget.docx"
    dst = root / "Projects" / "B" / "budget.docx"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "budget.docx.md"
    )
    # .docx is editable → final_dir = base_dir (Projects/B/), NOT attachment/
    final_binary = root / "Projects" / "B" / "budget.docx"
    new_sibling = (
        root / "Projects" / "B" / ".summaries" / "budget.docx.md"
    )

    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/budget.docx\n---\n# Budget\n",
        encoding="utf-8",
    )

    move_attachment_calls: list[tuple] = []
    move_note_calls: list[tuple] = []
    audit_calls: list = []

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

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        note = Note(
            path=path,
            content="# Budget\n",
            metadata=NoteMetadata(
                attachment_path="Projects/A/attachment/budget.docx",
            ),
            content_hash="def456",
        )
        from core.result import Success
        return Success(note)

    def fake_rename_doc(old, new, db_path=None):
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append((args, kwargs))
        from core.result import Success
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        from core.result import Success
        return Success(_fake_document_row())

    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.rename_doc", fake_rename_doc)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)
    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)

    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    # Editable file: binary NOT moved (already at dst location = Projects/B/)
    # needs_move = True only if src.parent != final_dir.
    # src = Projects/A/attachment/budget.docx, final_dir = Projects/B/
    # Different parents, so needs_move=True, final_binary = Projects/B/budget.docx
    # BUT wait: src is at Projects/A/attachment/, dst is at Projects/B/
    # resolve_placement computes final_dir from dst: Projects/B/budget.docx → project B, editable → base_dir = Projects/B
    # final_binary = Projects/B/budget.docx = dst → needs_move = False (dst.parent == final_dir)
    # Hmm, no. Let me re-think.
    # src = Projects/A/attachment/budget.docx (where it currently lives)
    # dst = Projects/B/budget.docx (where the user dragged it)
    # resolve_placement(dst, "project", "B", vault_cfg):
    #   target_type = "project", target_name = "B"
    #   base_dir = Projects/B
    #   is_no_edit = False (.docx not in no_edit_extensions)
    #   final_dir = Projects/B (editable → root)
    #   needs_move = (dst.parent != final_dir) → (Projects/B != Projects/B) → False
    # So binary stays at dst, no move_attachment needed.

    # Actually, src was at attachment/. User dragged it to visible root of B.
    # The watcher's src is the old location, dst is where user dropped.
    # resolve_placement uses dst: final_dir = Projects/B (editable).
    # final_binary = Projects/B/budget.docx = dst.
    # needs_move = (src.parent != final_dir) → (Projects/A/attachment != Projects/B) → True!
    # But final_binary == dst, so move_attachment(src, final_binary) would move A/attachment/budget.docx → B/budget.docx
    # That IS the right move — the binary needs to move from its old location to final location.
    # The user already moved it to B/budget.docx, but src is still A/attachment/budget.docx.
    # So we move from src (old) to final_binary (new, = dst). Both are the same path as dst.
    # Actually, move_attachment(src, dst) would fail because dst already exists (user moved it there).
    # Wait, but in the test, the file doesn't actually exist on disk. We're mocking.
    # The mock move_attachment just records the call. So it would be called.

    # Let me recalculate.
    # src = Projects/A/attachment/budget.docx
    # dst = Projects/B/budget.docx
    # resolve_placement(dst, "project", "B", vault_cfg):
    #   target_type="project", target_name="B"
    #   base_dir = Projects/B
    #   is_no_edit = ".docx" not in [".pdf", ".png", ...] → False
    #   final_dir = Projects/B
    #   sibling_dir = Projects/B/.summaries
    #   needs_move = src.parent != final_dir → True (Projects/A/attachment != Projects/B)
    # final_binary = Projects/B/budget.docx (same as dst)
    # Since needs_move and final_binary != dst → False (they're equal). So NO move_attachment.
    # That's correct: user already moved the binary to dst.

    # Hmm wait, the condition is:
    #   if needs_move and final_binary != dst: move
    # Since final_binary = Projects/B/budget.docx = dst, we skip the move.
    # So move_attachment NOT called for editable files.

    assert len(move_attachment_calls) == 0, (
        f"Editable binary should NOT be moved further, got {move_attachment_calls}"
    )

    # Sibling moved to root .summaries/
    assert len(move_note_calls) == 1
    assert move_note_calls[0] == (str(old_sibling), str(new_sibling), "ai")

    # Audit written
    assert len(audit_calls) == 1
    audit_args, audit_kwargs = audit_calls[0]
    decision = audit_args[0]
    assert decision.action == "watcher:binary_rehome"
    assert audit_kwargs.get("outcome") == "REHOMED"


# ---------------------------------------------------------------------------
# T3 — MoveGuard suppression
# ---------------------------------------------------------------------------


def test_move_guard_suppression(tmp_path: Path, monkeypatch):
    """Pipeline-initiated move (MoveGuard True) → no re-home, no audit."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    guard = MoveGuard()
    guard.register(root / "Projects" / "B" / "report.pdf")
    handler._move_guard = guard

    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "Projects" / "B" / "report.pdf"

    move_attachment_calls = []
    move_note_calls = []
    audit_calls = []

    def fake_move_attachment(s, d):
        move_attachment_calls.append((str(s), str(d)))
        from core.result import Success
        return Success(d)

    def fake_move_note(s, d, actor):
        move_note_calls.append((str(s), str(d), actor))
        from core.result import Success
        return Success(None)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append((args, kwargs))
        from core.result import Success
        return Success(None)

    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)

    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    # move_guard consumed → all re-home skipped
    assert len(move_attachment_calls) == 0
    assert len(move_note_calls) == 0
    assert len(audit_calls) == 0


# ---------------------------------------------------------------------------
# T4 — Fallback when DB row missing
# ---------------------------------------------------------------------------


def test_fallback_when_db_row_missing(tmp_path: Path, monkeypatch):
    """get_by_path returns Success(None) → old sibling orphaned, no re-home."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "Projects" / "B" / "report.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n",
        encoding="utf-8",
    )

    delete_calls: list[str] = []
    audit_calls: list = []
    move_attachment_calls: list = []

    def fake_delete_by_path(vp, db_path=None):
        delete_calls.append(vp)
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append((args, kwargs))
        from core.result import Success
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        from core.result import Success
        return Success(None)  # Not in DB

    def fake_move_attachment(s, d):
        move_attachment_calls.append((str(s), str(d)))
        from core.result import Success
        return Success(d)

    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)
    monkeypatch.setattr("vault.watcher.delete_by_path", fake_delete_by_path)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)
    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)

    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    # Old sibling orphaned via delete_by_path
    assert "Projects/A/attachment/.summaries/report.pdf.md" in delete_calls

    # No move_attachment (no re-home)
    assert len(move_attachment_calls) == 0

    # SIBLING_ORPHANED audit
    assert len(audit_calls) == 1
    _, kwargs = audit_calls[0]
    assert kwargs["outcome"] == "SIBLING_ORPHANED"


def test_fallback_when_get_by_path_failure(tmp_path: Path, monkeypatch):
    """get_by_path returns Failure → old sibling orphaned, no re-home."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "Projects" / "B" / "report.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n",
        encoding="utf-8",
    )

    delete_calls: list[str] = []
    audit_calls: list = []

    def fake_delete_by_path(vp, db_path=None):
        delete_calls.append(vp)
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append((args, kwargs))
        from core.result import Success
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        from core.result import Failure
        return Failure(error="db down", recoverable=False, context={})

    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)
    monkeypatch.setattr("vault.watcher.delete_by_path", fake_delete_by_path)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)

    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    assert "Projects/A/attachment/.summaries/report.pdf.md" in delete_calls
    assert len(audit_calls) == 1
    _, kwargs = audit_calls[0]
    assert kwargs["outcome"] == "SIBLING_ORPHANED"


# ---------------------------------------------------------------------------
# T5 — Fallback when unknown location
# ---------------------------------------------------------------------------


def test_fallback_when_unknown_location(tmp_path: Path, monkeypatch):
    """dst not under Projects/ or Domain/ → resolve_placement NOT called, orphan path."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    # dst is at vault root (unknown location)
    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "orphan.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n",
        encoding="utf-8",
    )

    delete_calls: list[str] = []
    audit_calls: list = []
    resolve_placement_calls: list = []

    def fake_delete_by_path(vp, db_path=None):
        delete_calls.append(vp)
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append((args, kwargs))
        from core.result import Success
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        from core.result import Success
        return Success(_fake_document_row())

    def fake_resolve_placement(*args, **kwargs):
        resolve_placement_calls.append((args, kwargs))
        from vault.paths import Placement
        return Placement(
            final_dir=root / "dummy",
            sibling_dir=root / "dummy" / ".summaries",
            needs_move=False,
        )

    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)
    monkeypatch.setattr("vault.watcher.delete_by_path", fake_delete_by_path)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)
    monkeypatch.setattr("vault.watcher.resolve_placement", fake_resolve_placement)

    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    # resolve_placement NOT called (unknown location → early return)
    assert len(resolve_placement_calls) == 0

    # Orphaned
    assert "Projects/A/attachment/.summaries/report.pdf.md" in delete_calls
    assert len(audit_calls) == 1
    _, kwargs = audit_calls[0]
    assert kwargs["outcome"] == "SIBLING_ORPHANED"


# ---------------------------------------------------------------------------
# T6 — updated_by_human lock aborts
# ---------------------------------------------------------------------------


def test_updated_by_human_lock_aborts(tmp_path: Path, monkeypatch):
    """Old sibling has updated_by_human=True → binary NOT moved, early return."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "Projects" / "B" / "report.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n",
        encoding="utf-8",
    )

    move_attachment_calls: list = []
    audit_calls: list = []

    def fake_move_attachment(s, d):
        move_attachment_calls.append((str(s), str(d)))
        from core.result import Success
        return Success(d)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append((args, kwargs))
        from core.result import Success
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        from core.result import Success
        return Success(_fake_document_row(updated_by_human=True))

    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)
    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)

    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    # move_attachment MUST NOT be called
    assert len(move_attachment_calls) == 0, (
        f"updated_by_human=True should block move, got {move_attachment_calls}"
    )
    # No re-home audit
    assert len(audit_calls) == 0


# ---------------------------------------------------------------------------
# T7 — Sibling rebuild from DB when absent on disk
# ---------------------------------------------------------------------------


def test_sibling_rebuild_from_db_when_absent(tmp_path: Path, monkeypatch):
    """Old sibling .md missing on disk → write_note called (not move_note), rebuilt from DB row."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "Projects" / "B" / "report.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    # Do NOT create old_sibling on disk
    final_binary = root / "Projects" / "B" / "attachment" / "report.pdf"
    new_sibling = (
        root / "Projects" / "B" / "attachment" / ".summaries" / "report.pdf.md"
    )

    move_attachment_calls: list = []
    move_note_calls: list = []
    write_note_calls: list = []

    def fake_move_attachment(s, d):
        move_attachment_calls.append((str(s), str(d)))
        from core.result import Success
        return Success(d)

    def fake_move_note(s, d, actor):
        move_note_calls.append((str(s), str(d), actor))
        from core.result import Success
        return Success(None)

    def fake_write_note(path, body, metadata, actor):
        write_note_calls.append((str(path), body, metadata.attachment_path, actor))
        from core.result import Success
        return Success(MagicMock())

    def fake_rename_doc(old, new, db_path=None):
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        from core.result import Success
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        from core.result import Success
        return Success(_fake_document_row(summary="DB summary text"))

    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.rename_doc", fake_rename_doc)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)
    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)

    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    # Binary moved
    assert len(move_attachment_calls) == 1

    # move_note NOT called (sibling absent on disk)
    assert len(move_note_calls) == 0

    # write_note called (rebuild from DB)
    assert len(write_note_calls) == 1
    path_arg, body, att_path, actor = write_note_calls[0]
    assert path_arg == str(new_sibling)
    assert body == "DB summary text"
    assert att_path == "Projects/B/attachment/report.pdf"
    assert actor == "ai"


# ---------------------------------------------------------------------------
# T8 — Audit row has correlation_id
# ---------------------------------------------------------------------------


def test_audit_row_has_correlation_id(tmp_path: Path, monkeypatch):
    """Verify contextvar has correlation_id at audit_write call time."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "Projects" / "B" / "report.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n# Summary\n",
        encoding="utf-8",
    )

    captured_cid: list[str] = []

    def fake_move_attachment(s, d):
        from core.result import Success
        return Success(d)

    def fake_move_note(s, d, actor):
        from core.result import Success
        return Success(None)

    def fake_write_note(path, body, metadata, actor):
        from core.result import Success
        return Success(MagicMock())

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        note = Note(
            path=path,
            content="# Summary\n",
            metadata=NoteMetadata(
                attachment_path="Projects/A/attachment/report.pdf",
            ),
            content_hash="abc",
        )
        from core.result import Success
        return Success(note)

    def fake_rename_doc(old, new, db_path=None):
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        import structlog
        from core.result import Success
        ctx = structlog.contextvars.get_contextvars()
        cid = ctx.get("correlation_id")
        captured_cid.append(cid if isinstance(cid, str) else str(cid) if cid else "MISSING")
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        from core.result import Success
        return Success(_fake_document_row())

    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.rename_doc", fake_rename_doc)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)
    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)

    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    assert len(captured_cid) == 1
    assert len(captured_cid[0]) > 0, f"correlation_id must be non-empty, got {captured_cid[0]!r}"
    assert captured_cid[0] != "MISSING", "correlation_id was not bound"


# ---------------------------------------------------------------------------
# T9 — Domain symmetry
# ---------------------------------------------------------------------------


def test_domain_symmetry(tmp_path: Path, monkeypatch):
    """Same as T1 but target_type='domain' — re-home to Domain/<D>/attachment/."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    src = root / "Domain" / "Finance" / "attachment" / "report.pdf"
    dst = root / "Domain" / "HR" / "report.pdf"
    old_sibling = (
        root / "Domain" / "Finance" / "attachment" / ".summaries" / "report.pdf.md"
    )
    final_binary = root / "Domain" / "HR" / "attachment" / "report.pdf"
    new_sibling = (
        root / "Domain" / "HR" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Domain/Finance/attachment/report.pdf\n---\n# Summary\n",
        encoding="utf-8",
    )

    move_attachment_calls: list[tuple] = []
    move_note_calls: list[tuple] = []
    audit_calls: list = []

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

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        note = Note(
            path=path,
            content="# Summary\n",
            metadata=NoteMetadata(
                attachment_path="Domain/Finance/attachment/report.pdf",
            ),
            content_hash="abc",
        )
        from core.result import Success
        return Success(note)

    def fake_rename_doc(old, new, db_path=None):
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append((args, kwargs))
        from core.result import Success
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        from core.result import Success
        return Success(_fake_document_row())

    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.rename_doc", fake_rename_doc)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)
    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)

    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    # Binary moved to domain attachment
    assert len(move_attachment_calls) == 1
    assert move_attachment_calls[0] == (str(dst), str(final_binary))

    # Sibling moved
    assert len(move_note_calls) == 1
    assert move_note_calls[0] == (str(old_sibling), str(new_sibling), "ai")

    # Audit
    assert len(audit_calls) == 1
    _, kwargs = audit_calls[0]
    assert kwargs["outcome"] == "REHOMED"


# ---------------------------------------------------------------------------
# T10 — move_guard=None no error
# ---------------------------------------------------------------------------


def test_move_guard_none_no_error(tmp_path: Path, monkeypatch):
    """_move_guard = None → no AttributeError, re-home proceeds normally."""
    handler, root, vault_cfg = _make_handler(tmp_path)
    # handler._move_guard is None by default

    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "Projects" / "B" / "report.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n# Summary\n",
        encoding="utf-8",
    )

    audit_calls: list = []

    def fake_move_attachment(s, d):
        from core.result import Success
        return Success(d)

    def fake_move_note(s, d, actor):
        from core.result import Success
        return Success(None)

    def fake_write_note(path, body, metadata, actor):
        from core.result import Success
        return Success(MagicMock())

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        note = Note(
            path=path,
            content="# Summary\n",
            metadata=NoteMetadata(
                attachment_path="Projects/A/attachment/report.pdf",
            ),
            content_hash="abc",
        )
        from core.result import Success
        return Success(note)

    def fake_rename_doc(old, new, db_path=None):
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append((args, kwargs))
        from core.result import Success
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        from core.result import Success
        return Success(_fake_document_row())

    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.rename_doc", fake_rename_doc)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)
    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)

    # Should NOT raise AttributeError
    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    # Re-home proceeded normally
    assert len(audit_calls) == 1
    _, kwargs = audit_calls[0]
    assert kwargs["outcome"] == "REHOMED"


# ---------------------------------------------------------------------------
# T11 (integration) — correlation_id is non-empty string
# ---------------------------------------------------------------------------


def test_correlation_id_is_non_empty_string(tmp_path: Path, monkeypatch):
    """Integration-level: the full _handle_binary_move binds a real correlation_id."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "Projects" / "B" / "report.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n# Summary\n",
        encoding="utf-8",
    )

    captured_cids: list[str] = []

    def fake_move_attachment(s, d):
        from core.result import Success
        return Success(d)

    def fake_move_note(s, d, actor):
        from core.result import Success
        return Success(None)

    def fake_write_note(path, body, metadata, actor):
        from core.result import Success
        return Success(MagicMock())

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        return Success(Note(
            path=path,
            content="# Summary\n",
            metadata=NoteMetadata(
                attachment_path="Projects/A/attachment/report.pdf",
            ),
            content_hash="abc",
        ))

    def fake_rename_doc(old, new, db_path=None):
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        import structlog
        from core.result import Success
        ctx = structlog.contextvars.get_contextvars()
        cid = ctx.get("correlation_id")
        captured_cids.append(cid if isinstance(cid, str) else "")
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        from core.result import Success
        return Success(_fake_document_row())

    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.rename_doc", fake_rename_doc)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)
    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)

    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    assert len(captured_cids) == 1
    assert isinstance(captured_cids[0], str), (
        f"correlation_id must be str, got {type(captured_cids[0])}"
    )
    assert len(captured_cids[0]) > 0, "correlation_id must be non-empty"


# ---------------------------------------------------------------------------
# T12 — move_attachment uses dst (current location), not src (stale path)
# ---------------------------------------------------------------------------


def test_rehome_move_attachment_uses_dst_not_src(tmp_path: Path, monkeypatch):
    """After settle window, OS already moved file to dst. move_attachment must
    use dst (where the binary actually is), not src (stale original path)."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    # Cross-folder re-home: PDF from A/attachment → B/ (user drag)
    src = root / "Projects" / "A" / "attachment" / "data.pdf"
    dst = root / "Projects" / "B" / "data.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "data.pdf.md"
    )
    final_binary = root / "Projects" / "B" / "attachment" / "data.pdf"

    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/data.pdf\n---\n# Data\n",
        encoding="utf-8",
    )

    move_attachment_calls: list[tuple] = []

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

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        return Success(Note(
            path=path,
            content="# Data\n",
            metadata=NoteMetadata(
                attachment_path="Projects/A/attachment/data.pdf",
            ),
            content_hash="abc",
        ))

    def fake_rename_doc(old, new, db_path=None):
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        from core.result import Success
        return Success(None)

    def fake_get_by_path(vp, db_path=None):
        from core.result import Success
        return Success(_fake_document_row())

    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.rename_doc", fake_rename_doc)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)
    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)

    handler._handle_binary_move(src, dst)

    time.sleep(0.05)

    # move_attachment MUST use dst (where OS placed the file), not src
    assert len(move_attachment_calls) == 1
    assert move_attachment_calls[0] == (str(dst), str(final_binary)), (
        f"Expected move_attachment(dst, final), got move_attachment{move_attachment_calls[0]}"
    )
    # Explicitly verify src was NOT used as first arg
    assert move_attachment_calls[0][0] != str(src), (
        "move_attachment must NOT use stale src path"
    )
