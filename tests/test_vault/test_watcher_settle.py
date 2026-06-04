"""
tests/test_vault/test_watcher_settle.py

Unit tests for Phase 8 (T7): settle window — coalesces multi-hop binary moves.

All collaborator patches target vault.watcher.* per TD-033.
CONFIG is never imported at module scope — all tests use VaultConfig(root=tmp_path).
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
    )
    if move_guard is not None:
        handler._move_guard = move_guard
    return handler, root, vault_cfg


def _fake_document_row(*, updated_by_human: bool = False, **overrides):
    """Return a DocumentRow-like object."""
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
# T1: Single-hop cross-folder move processed after settle window
# ---------------------------------------------------------------------------


def test_single_hop_processed_after_settle(tmp_path: Path, monkeypatch):
    """Move from A/attachment/ → B/ is coalesced via settle window, exactly
    1 re-home audit row."""
    handler, root, vault_cfg = _make_handler(tmp_path, debounce=0.01)

    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "Projects" / "B" / "report.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    final_binary = root / "Projects" / "B" / "attachment" / "report.pdf"
    new_sibling = (
        root / "Projects" / "B" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n# Summary\n",
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

    # Call directly — this should register settle, not execute re-home
    handler._handle_binary_move(src, dst)

    # At this point, re-home has NOT happened yet (pending settle)
    assert len(audit_calls) == 0, (
        "No audit should fire before settle timer fires"
    )

    # Wait for settle timer to fire
    time.sleep(0.1)

    # After settle: exactly 1 re-home
    assert len(move_attachment_calls) == 1, (
        f"Expected 1 move_attachment, got {len(move_attachment_calls)}"
    )
    assert move_attachment_calls[0] == (str(src), str(final_binary))

    assert len(move_note_calls) == 1
    assert move_note_calls[0] == (str(old_sibling), str(new_sibling), "ai")

    assert len(audit_calls) == 1
    _, kwargs = audit_calls[0]
    assert kwargs["outcome"] == "REHOMED"


# ---------------------------------------------------------------------------
# T2: Two-hop move coalesced into single re-home
# ---------------------------------------------------------------------------


def test_two_hop_coalesced_into_single_rehome(tmp_path: Path, monkeypatch):
    """A → B → C in rapid succession → only 1 re-home (A → C)."""
    handler, root, vault_cfg = _make_handler(tmp_path, debounce=0.01)

    src1 = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst1 = root / "Projects" / "B" / "report.pdf"
    src2 = dst1  # Second hop: src = previous dst
    dst2 = root / "Projects" / "C" / "report.pdf"

    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n# Summary\n",
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

    # Hop 1: A → B (register settle, no re-home yet)
    handler._handle_binary_move(src1, dst1)
    assert len(audit_calls) == 0, "No audit yet after hop 1"

    # Hop 2: B → C (reset settle timer, coalesced)
    handler._handle_binary_move(src2, dst2)
    assert len(audit_calls) == 0, "No audit yet after hop 2"

    # Wait for settle timer
    time.sleep(0.1)

    # Only 1 re-home (from A → C, the accumulated pair)
    assert len(audit_calls) == 1, (
        f"Expected 1 audit (coalesced), got {len(audit_calls)}"
    )
    _, kwargs = audit_calls[0]
    assert kwargs["outcome"] == "REHOMED"

    # Verify move_attachment uses final src→dst: A/attachment/ → C/attachment/
    assert len(move_attachment_calls) == 1
    assert str(src1) in move_attachment_calls[0][0], (
        f"Expected src={src1}, got {move_attachment_calls[0][0]}"
    )
    assert "Projects/C/attachment/report.pdf" in move_attachment_calls[0][1]


# ---------------------------------------------------------------------------
# T3: Three-hop move coalesced into single re-home
# ---------------------------------------------------------------------------


def test_three_hop_coalesced_into_single_rehome(tmp_path: Path, monkeypatch):
    """A → B → C → D in rapid succession → only 1 re-home (A → D)."""
    handler, root, vault_cfg = _make_handler(tmp_path, debounce=0.01)

    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst_hop1 = root / "Projects" / "B" / "report.pdf"
    dst_hop2 = root / "Projects" / "C" / "report.pdf"
    dst_final = root / "Projects" / "D" / "report.pdf"

    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n# Summary\n",
        encoding="utf-8",
    )

    audit_calls: list = []
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

    # Three hops
    handler._handle_binary_move(src, dst_hop1)
    handler._handle_binary_move(dst_hop1, dst_hop2)
    handler._handle_binary_move(dst_hop2, dst_final)

    assert len(audit_calls) == 0, "No audit yet during hops"

    time.sleep(0.1)

    assert len(audit_calls) == 1, (
        f"Expected 1 coalesced audit, got {len(audit_calls)}"
    )
    _, kwargs = audit_calls[0]
    assert kwargs["outcome"] == "REHOMED"

    # move_attachment uses first src → last dst
    assert len(move_attachment_calls) == 1
    assert str(src) in move_attachment_calls[0][0]
    assert "Projects/D/attachment/report.pdf" in move_attachment_calls[0][1]


# ---------------------------------------------------------------------------
# T4: Settle window token prevents stale-fire race
# ---------------------------------------------------------------------------


def test_stale_token_is_no_op(tmp_path: Path, monkeypatch):
    """Token 1 fire after token 2 installed → no-op, only token 2 re-home fires."""
    handler, root, vault_cfg = _make_handler(tmp_path, debounce=0.01)

    src1 = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst1 = root / "Projects" / "B" / "report.pdf"
    src2 = dst1
    dst2 = root / "Projects" / "C" / "report.pdf"

    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n# Summary\n",
        encoding="utf-8",
    )

    audit_calls: list = []
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

    # Hop 1: A → B (token 1 timer installed)
    handler._handle_binary_move(src1, dst1)

    # Hop 2: B → C (token 2 timer installed, token 1 cancelled)
    handler._handle_binary_move(src2, dst2)

    # Manually fire with stale token 1 — must be ignored
    handler._fire_binary_move_settled(
        settle_key=str(src1), src=src1, dst=dst1, token=1
    )
    assert len(audit_calls) == 0, (
        "Stale token 1 fire must be no-op"
    )

    # Wait for token 2 timer to fire
    time.sleep(0.1)

    # Only token 2's re-home fires
    assert len(audit_calls) == 1, (
        f"Expected exactly 1 re-home from token 2, got {len(audit_calls)}"
    )
    _, kwargs = audit_calls[0]
    assert kwargs["outcome"] == "REHOMED"

    # Verify it re-homed to C (the final destination)
    assert "Projects/C/attachment/report.pdf" in move_attachment_calls[0][1]


# ---------------------------------------------------------------------------
# T5: Two independent binaries do not interfere
# ---------------------------------------------------------------------------


def test_two_independent_binaries_no_interference(tmp_path: Path, monkeypatch):
    """Different binary names → separate settle entries, 2 re-homes."""
    handler, root, vault_cfg = _make_handler(tmp_path, debounce=0.01)

    # Binary 1
    src1 = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst1 = root / "Projects" / "B" / "report.pdf"
    old_sib1 = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "report.pdf.md"
    )
    # Binary 2 (different name)
    src2 = root / "Projects" / "A" / "attachment" / "budget.xlsx"
    dst2 = root / "Projects" / "C" / "budget.xlsx"
    old_sib2 = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "budget.xlsx.md"
    )

    old_sib1.parent.mkdir(parents=True, exist_ok=True)
    old_sib1.write_text(
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\n# Report\n",
        encoding="utf-8",
    )
    old_sib2.write_text(
        "---\nattachment_path: Projects/A/attachment/budget.xlsx\n---\n# Budget\n",
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
        from core.result import Success
        ap = "Projects/A/attachment/report.pdf"
        if "budget" in str(path):
            ap = "Projects/A/attachment/budget.xlsx"
        return Success(Note(
            path=path,
            content="# Content\n",
            metadata=NoteMetadata(attachment_path=ap),
            content_hash="abc",
        ))

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

    # Move both binaries
    handler._handle_binary_move(src1, dst1)
    handler._handle_binary_move(src2, dst2)

    time.sleep(0.1)

    # 2 independent re-homes
    assert len(audit_calls) == 2, (
        f"Expected 2 separate re-homes, got {len(audit_calls)}"
    )
    outcomes = [kwargs["outcome"] for _, kwargs in audit_calls]
    assert outcomes == ["REHOMED", "REHOMED"]


# ---------------------------------------------------------------------------
# T6: Same-folder rename processed immediately (no settle)
# ---------------------------------------------------------------------------


def test_same_folder_rename_no_settle(tmp_path: Path, monkeypatch):
    """Same-folder rename → processed immediately, no settle window."""
    handler, root, vault_cfg = _make_handler(tmp_path, debounce=0.01)

    src = root / "Projects" / "A" / "attachment" / "X.pdf"
    dst = root / "Projects" / "A" / "attachment" / "Y.pdf"
    old_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "X.pdf.md"
    )
    # Create old sibling on disk for same-folder rename path
    old_sibling.parent.mkdir(parents=True, exist_ok=True)
    old_sibling.write_text(
        "---\nattachment_path: Projects/A/attachment/X.pdf\n---\n# Summary\n",
        encoding="utf-8",
    )
    new_sibling = (
        root / "Projects" / "A" / "attachment" / ".summaries" / "Y.pdf.md"
    )

    move_note_calls: list[tuple] = []

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
        from core.result import Success
        return Success(Note(
            path=path,
            content="# Summary\n",
            metadata=NoteMetadata(
                attachment_path="Projects/A/attachment/X.pdf",
            ),
            content_hash="abc",
        ))

    def fake_rename_doc(old, new, db_path=None):
        from core.result import Success
        return Success(1)

    def fake_audit_write(*args, **kwargs):
        from core.result import Success
        return Success(None)

    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.rename_doc", fake_rename_doc)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)

    handler._handle_binary_move(src, dst)

    # Same-folder path uses if-branch (not else), so no settle registration
    # Move_note should be called immediately (not deferred)
    assert len(move_note_calls) == 1, (
        f"Same-folder rename should process immediately, got {move_note_calls}"
    )
    assert move_note_calls[0][0] == str(old_sibling)
    assert move_note_calls[0][1] == str(new_sibling)


# ---------------------------------------------------------------------------
# T7: MoveGuard suppression still works with settle window
# ---------------------------------------------------------------------------


def test_move_guard_suppression_with_settle(tmp_path: Path, monkeypatch):
    """MoveGuard consumes entry → return early, settle NOT registered."""
    handler, root, vault_cfg = _make_handler(tmp_path, debounce=0.01)

    guard = MoveGuard()
    guard.register(root / "Projects" / "B" / "report.pdf")
    handler._move_guard = guard

    src = root / "Projects" / "A" / "attachment" / "report.pdf"
    dst = root / "Projects" / "B" / "report.pdf"

    audit_calls: list = []
    move_attachment_calls: list = []

    def fake_move_attachment(s, d):
        move_attachment_calls.append((str(s), str(d)))
        from core.result import Success
        return Success(d)

    def fake_audit_write(*args, **kwargs):
        audit_calls.append((args, kwargs))
        from core.result import Success
        return Success(None)

    monkeypatch.setattr("vault.watcher.move_attachment", fake_move_attachment)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)

    handler._handle_binary_move(src, dst)

    # MoveGuard consumed → return early before settle registration
    wait_ms = 0
    while len(audit_calls) == 0 and wait_ms < 200:
        time.sleep(0.01)
        wait_ms += 10

    assert len(move_attachment_calls) == 0, (
        "MoveGuard should suppress re-home"
    )
    assert len(audit_calls) == 0, (
        "MoveGuard should suppress audit"
    )
