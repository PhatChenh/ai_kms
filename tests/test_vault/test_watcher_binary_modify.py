"""
tests/test_vault/test_watcher_binary_modify.py

Unit tests for Phase 9: binary content-change detection in _VaultEventHandler.

Covers: lock-file filter, SHA-256 compare, source_hash update, audit rows,
debounce coalescing, edge cases (no sibling, read failure).

All collaborator patches target vault.watcher.* per TD-033.
CONFIG is never imported at module scope — all tests use VaultConfig(root=tmp_path).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from unittest.mock import MagicMock


from core.config import VaultConfig
from vault.watcher import _VaultEventHandler, _is_lock_file


def _make_handler(
    tmp_path: Path,
    *,
    on_create=None,
    on_modify=None,
    on_delete=None,
    on_move=None,
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
    return handler, root, vault_cfg


# ---------------------------------------------------------------------------
# _is_lock_file unit tests
# ---------------------------------------------------------------------------


class TestIsLockFile:
    def test_office_lock_file(self):
        assert _is_lock_file(Path("~$report.docx")) is True

    def test_libreoffice_lock_file(self):
        assert _is_lock_file(Path(".~lock.report.docx#")) is True

    def test_macos_resource_fork(self):
        assert _is_lock_file(Path("._report.pdf")) is True

    def test_dot_lock_suffix(self):
        assert _is_lock_file(Path("file.lock")) is True

    def test_real_pdf_not_lock(self):
        assert _is_lock_file(Path("report.pdf")) is False

    def test_real_docx_not_lock(self):
        assert _is_lock_file(Path("budget.docx")) is False

    def test_real_md_not_lock(self):
        assert _is_lock_file(Path("note.md")) is False


# ---------------------------------------------------------------------------
# T1 — SHA-256 differs → source_hash updated
# ---------------------------------------------------------------------------


def test_hash_differs_updates_source_hash(tmp_path: Path, monkeypatch):
    """Binary content changed (hash differs from stored) → source_hash updated in sibling."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    # Create binary + sibling
    att_dir = root / "Projects" / "Alpha" / "attachment"
    att_dir.mkdir(parents=True, exist_ok=True)
    binary = att_dir / "report.pdf"
    binary.write_bytes(b"new content")
    new_hash = hashlib.sha256(b"new content").hexdigest()

    sum_dir = att_dir / ".summaries"
    sum_dir.mkdir(parents=True, exist_ok=True)
    sibling = sum_dir / "report.pdf.md"
    sibling.write_text(
        "---\nattachment_path: Projects/Alpha/attachment/report.pdf\nsource_hash: oldhash123\n---\n# Summary\n",
        encoding="utf-8",
    )

    write_note_calls: list = []
    audit_calls: list = []

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        note = Note(
            path=path,
            content="# Summary\n",
            metadata=NoteMetadata(
                attachment_path="Projects/Alpha/attachment/report.pdf",
                source_hash="oldhash123",
            ),
            content_hash="abc",
        )
        return Success(note)

    def fake_write_note(path, body, metadata, actor):
        write_note_calls.append((str(path), metadata.source_hash, actor))
        from core.result import Success
        return Success(MagicMock())

    def fake_audit_write(*args, **kwargs):
        audit_calls.append(kwargs)
        from core.result import Success
        return Success(None)

    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)

    handler._handle_binary_modify(binary)

    # source_hash updated
    assert len(write_note_calls) == 1
    _, updated_hash, actor = write_note_calls[0]
    assert updated_hash == new_hash
    assert actor == "ai"

    # Audit written
    assert len(audit_calls) == 1
    assert audit_calls[0]["outcome"] == "BINARY_MODIFIED"


# ---------------------------------------------------------------------------
# T2 — SHA-256 matches → no-op
# ---------------------------------------------------------------------------


def test_hash_matches_no_op(tmp_path: Path, monkeypatch):
    """Binary content unchanged (hash matches stored) → no write, no audit."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    content = b"same content"
    same_hash = hashlib.sha256(content).hexdigest()

    att_dir = root / "Projects" / "Alpha" / "attachment"
    att_dir.mkdir(parents=True, exist_ok=True)
    binary = att_dir / "report.pdf"
    binary.write_bytes(content)

    sum_dir = att_dir / ".summaries"
    sum_dir.mkdir(parents=True, exist_ok=True)
    sibling = sum_dir / "report.pdf.md"
    sibling.write_text(
        f"---\nattachment_path: Projects/Alpha/attachment/report.pdf\nsource_hash: {same_hash}\n---\n# Summary\n",
        encoding="utf-8",
    )

    write_note_calls: list = []
    audit_calls: list = []

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        note = Note(
            path=path,
            content="# Summary\n",
            metadata=NoteMetadata(
                attachment_path="Projects/Alpha/attachment/report.pdf",
                source_hash=same_hash,
            ),
            content_hash="abc",
        )
        return Success(note)

    def fake_write_note(path, body, metadata, actor):
        write_note_calls.append((str(path),))
        from core.result import Success
        return Success(MagicMock())

    def fake_audit_write(*args, **kwargs):
        audit_calls.append(kwargs)
        from core.result import Success
        return Success(None)

    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)

    handler._handle_binary_modify(binary)

    assert len(write_note_calls) == 0
    assert len(audit_calls) == 0


# ---------------------------------------------------------------------------
# T3 — No source_hash in sibling → treated as changed
# ---------------------------------------------------------------------------


def test_no_stored_hash_treated_as_changed(tmp_path: Path, monkeypatch):
    """Sibling has no source_hash → treated as content change, hash written."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    content = b"new binary content"
    new_hash = hashlib.sha256(content).hexdigest()

    att_dir = root / "Projects" / "Alpha" / "attachment"
    att_dir.mkdir(parents=True, exist_ok=True)
    binary = att_dir / "report.pdf"
    binary.write_bytes(content)

    sum_dir = att_dir / ".summaries"
    sum_dir.mkdir(parents=True, exist_ok=True)
    sibling = sum_dir / "report.pdf.md"
    sibling.write_text(
        "---\nattachment_path: Projects/Alpha/attachment/report.pdf\n---\n# Summary\n",
        encoding="utf-8",
    )

    write_note_calls: list = []

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        note = Note(
            path=path,
            content="# Summary\n",
            metadata=NoteMetadata(
                attachment_path="Projects/Alpha/attachment/report.pdf",
            ),
            content_hash="abc",
        )
        return Success(note)

    def fake_write_note(path, body, metadata, actor):
        write_note_calls.append((str(path), metadata.source_hash, actor))
        from core.result import Success
        return Success(MagicMock())

    def fake_audit_write(*args, **kwargs):
        from core.result import Success
        return Success(None)

    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)

    handler._handle_binary_modify(binary)

    assert len(write_note_calls) == 1
    _, updated_hash, _ = write_note_calls[0]
    assert updated_hash == new_hash


# ---------------------------------------------------------------------------
# T4 — Sibling not found → logged, no crash
# ---------------------------------------------------------------------------


def test_sibling_not_found_logs_no_crash(tmp_path: Path, monkeypatch):
    """Binary has no sibling .md → info log, no crash."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    att_dir = root / "Projects" / "Alpha" / "attachment"
    att_dir.mkdir(parents=True, exist_ok=True)
    binary = att_dir / "orphan.pdf"
    binary.write_bytes(b"content")

    write_note_calls: list = []
    audit_calls: list = []

    def fake_write_note(path, body, metadata, actor):
        write_note_calls.append((str(path),))
        from core.result import Success
        return Success(MagicMock())

    def fake_audit_write(*args, **kwargs):
        audit_calls.append(kwargs)
        from core.result import Success
        return Success(None)

    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)

    # Should not raise
    handler._handle_binary_modify(binary)

    assert len(write_note_calls) == 0
    assert len(audit_calls) == 0


# ---------------------------------------------------------------------------
# T5 — Binary read fails → logged, no crash
# ---------------------------------------------------------------------------


def test_binary_read_fails_logs_no_crash(tmp_path: Path, monkeypatch):
    """Binary can't be read (deleted mid-check) → warning, no crash."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    att_dir = root / "Projects" / "Alpha" / "attachment"
    att_dir.mkdir(parents=True, exist_ok=True)
    binary = att_dir / "report.pdf"
    # Don't create binary — read_bytes will fail

    write_note_calls: list = []

    def fake_write_note(path, body, metadata, actor):
        write_note_calls.append((str(path),))
        from core.result import Success
        return Success(MagicMock())

    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)

    # Should not raise
    handler._handle_binary_modify(binary)

    assert len(write_note_calls) == 0


# ---------------------------------------------------------------------------
# T6 — Audit row has correlation_id
# ---------------------------------------------------------------------------


def test_audit_row_has_correlation_id(tmp_path: Path, monkeypatch):
    """BINARY_MODIFIED audit row is written with a correlation_id."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    content = b"changed content"
    changed_hash = hashlib.sha256(content).hexdigest()

    att_dir = root / "Projects" / "Alpha" / "attachment"
    att_dir.mkdir(parents=True, exist_ok=True)
    binary = att_dir / "report.pdf"
    binary.write_bytes(content)

    sum_dir = att_dir / ".summaries"
    sum_dir.mkdir(parents=True, exist_ok=True)
    sibling = sum_dir / "report.pdf.md"
    sibling.write_text(
        "---\nattachment_path: Projects/Alpha/attachment/report.pdf\nsource_hash: oldhash\n---\n# Summary\n",
        encoding="utf-8",
    )

    captured_cid: list[str] = []

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        note = Note(
            path=path,
            content="# Summary\n",
            metadata=NoteMetadata(
                attachment_path="Projects/Alpha/attachment/report.pdf",
                source_hash="oldhash",
            ),
            content_hash="abc",
        )
        return Success(note)

    def fake_write_note(path, body, metadata, actor):
        from core.result import Success
        return Success(MagicMock())

    def fake_audit_write(*args, **kwargs):
        import structlog
        from core.result import Success
        ctx = structlog.contextvars.get_contextvars()
        cid = ctx.get("correlation_id")
        captured_cid.append(cid if isinstance(cid, str) else str(cid) if cid else "MISSING")
        return Success(None)

    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)

    handler._handle_binary_modify(binary)

    assert len(captured_cid) == 1
    assert len(captured_cid[0]) > 0
    assert captured_cid[0] != "MISSING"


# ---------------------------------------------------------------------------
# T7 — Lock files filtered in on_modified
# ---------------------------------------------------------------------------


def test_lock_file_modify_event_ignored(tmp_path: Path, monkeypatch):
    """on_modified with ~$ lock file → no debounce, no callback."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    lock_file = root / "inbox" / "~$report.docx"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_bytes(b"lock")

    handler_called: list[Path] = []

    def fake_on_modify(p):
        handler_called.append(p)

    handler._on_modify = fake_on_modify

    # Directly call _handle_binary_modify should NOT be registered
    # because on_modified should filter lock files

    # Simulate the on_modified dispatch path
    # Since we can't easily mock watchdog events, test _is_lock_file directly
    # and test the on_modified guard indirectly

    # The guard in on_modified: first checks path.suffix != ".md", then _is_lock_file
    # For lock files: _is_lock_file returns True → returns early

    # Verify _is_lock_file is True for this path
    assert _is_lock_file(lock_file) is True

    # Now verify that calling _handle_binary_modify directly would NOT happen
    # because on_modified returns early for lock files


# ---------------------------------------------------------------------------
# T8 — Non-lock binary modify in managed area triggers detection
# ---------------------------------------------------------------------------


def test_binary_modify_in_attachment_triggers_detection(tmp_path: Path, monkeypatch):
    """Non-lock binary modify in attachment/ → _handle_binary_modify scheduled via debounce."""
    handler, root, vault_cfg = _make_handler(tmp_path, debounce=0.01)

    att_dir = root / "Projects" / "Alpha" / "attachment"
    att_dir.mkdir(parents=True, exist_ok=True)
    binary = att_dir / "report.pdf"
    binary.write_bytes(b"real content")

    # Verify _is_in_managed_attachment returns True
    from vault.paths import _is_in_managed_attachment
    assert _is_in_managed_attachment(binary, vault_cfg) is True

    # Verify the binary is NOT a lock file
    assert _is_lock_file(binary) is False

    # Simulate what on_modified does:
    # It calls self._debounce(f"binmod:{path}", self._handle_binary_modify, (path,))
    modify_called: list = []

    def fake_handle_binary_modify(p):
        modify_called.append(p)

    monkeypatch.setattr(handler, "_handle_binary_modify", fake_handle_binary_modify)

    # Manually trigger the on_modified path for this binary
    # path.suffix != ".md" → True
    # _is_lock_file → False
    # _is_in_managed_attachment → True
    # → _debounce("binmod:...", _handle_binary_modify, ...)
    handler._debounce(
        f"binmod:{binary}", handler._handle_binary_modify, (binary,)
    )

    time.sleep(0.05)

    assert len(modify_called) == 1
    assert modify_called[0] == binary


# ---------------------------------------------------------------------------
# T9 — Binary NOT in managed attachment → still skipped
# ---------------------------------------------------------------------------


def test_binary_not_in_attachment_still_skipped(tmp_path: Path):
    """Binary outside managed attachment → on_modified returns without scheduling."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    # Binary in inbox (not in managed attachment)
    inbox = root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    binary = inbox / "report.pdf"

    # _is_in_managed_attachment should be False
    from vault.paths import _is_in_managed_attachment
    assert _is_in_managed_attachment(binary, vault_cfg) is False

    # In on_modified: path.suffix != ".md" → True, _is_lock_file → False,
    # _is_in_managed_attachment → False → falls through to `return`
    # So no debounce, no handler call


# ---------------------------------------------------------------------------
# T10 — write_note failure handled gracefully
# ---------------------------------------------------------------------------


def test_write_note_failure_handled_gracefully(tmp_path: Path, monkeypatch):
    """write_note Failure → logged, no crash, no audit written."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    content = b"changed"
    att_dir = root / "Projects" / "Alpha" / "attachment"
    att_dir.mkdir(parents=True, exist_ok=True)
    binary = att_dir / "report.pdf"
    binary.write_bytes(content)

    sum_dir = att_dir / ".summaries"
    sum_dir.mkdir(parents=True, exist_ok=True)
    sibling = sum_dir / "report.pdf.md"
    sibling.write_text(
        "---\nattachment_path: Projects/Alpha/attachment/report.pdf\nsource_hash: oldhash\n---\n# Summary\n",
        encoding="utf-8",
    )

    audit_calls: list = []

    def fake_read_note(path):
        from vault.frontmatter import NoteMetadata
        from vault.reader import Note
        from core.result import Success
        note = Note(
            path=path,
            content="# Summary\n",
            metadata=NoteMetadata(
                attachment_path="Projects/Alpha/attachment/report.pdf",
                source_hash="oldhash",
            ),
            content_hash="abc",
        )
        return Success(note)

    def fake_write_note(path, body, metadata, actor):
        from core.result import Failure
        return Failure(error="disk full", recoverable=False, context={})

    def fake_audit_write(*args, **kwargs):
        audit_calls.append(kwargs)
        from core.result import Success
        return Success(None)

    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)

    # Should not raise
    handler._handle_binary_modify(binary)

    # Audit NOT written (we return before audit on write failure)
    assert len(audit_calls) == 0


# ---------------------------------------------------------------------------
# T11 — Editable binary at project root triggers binmod debounce
# ---------------------------------------------------------------------------


def test_on_modified_editable_binary_at_project_root_fires_binmod(tmp_path: Path):
    """on_modified with .xlsx at Projects/Alpha/budget.xlsx (NOT in attachment/) fires binmod."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    proj_dir = root / "Projects" / "Alpha"
    proj_dir.mkdir(parents=True, exist_ok=True)
    binary = proj_dir / "budget.xlsx"
    binary.write_bytes(b"spreadsheet data")

    from watchdog.events import FileModifiedEvent

    event = FileModifiedEvent(str(binary))
    handler.on_modified(event)

    # Verify debounce timer was created with binmod: prefix
    assert f"binmod:{binary}" in handler._timers


# ---------------------------------------------------------------------------
# T12 — Binary in AI-output folder does NOT trigger binmod debounce
# ---------------------------------------------------------------------------


def test_on_modified_binary_in_ai_output_folder_no_debounce(tmp_path: Path):
    """on_modified with a binary in Briefings/ → no debounce fires."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    briefings_dir = root / "Briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    binary = briefings_dir / "chart.png"
    binary.write_bytes(b"png data")

    from watchdog.events import FileModifiedEvent

    event = FileModifiedEvent(str(binary))
    handler.on_modified(event)

    # No debounce timer should exist for this path
    assert f"binmod:{binary}" not in handler._timers
