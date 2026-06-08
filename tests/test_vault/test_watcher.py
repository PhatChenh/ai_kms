"""
tests/test_vault/test_watcher.py

Unit tests for vault/watcher.py — VaultWatcher event dispatch and debounce.

Tests call _VaultEventHandler methods directly with synthetic watchdog events
(no real observer started, no real filesystem events). debounce_seconds is set
to a short value and tests sleep briefly to let threading.Timer fire.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from watchdog.events import (
    DirCreatedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from core.config import VaultConfig
from vault.watcher import VaultWatcher, _VaultEventHandler

DEBOUNCE = 0.02  # 20ms — short enough for fast tests
WAIT = 0.1  # 100ms — long enough for timer to fire after DEBOUNCE


def _make_handler(
    tmp_path: Path,
    *,
    on_create=None,
    on_modify=None,
    on_delete=None,
    on_move=None,
    debounce: float = DEBOUNCE,
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
# on_create
# ---------------------------------------------------------------------------


def test_on_create_fires_for_md_in_inbox(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, _ = _make_handler(tmp_path, on_create=calls.append)

    md_path = root / "inbox" / "note.md"
    handler.on_created(FileCreatedEvent(str(md_path)))

    time.sleep(WAIT)
    assert calls == [md_path]


def test_on_create_fires_for_md_in_projects(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, _ = _make_handler(tmp_path, on_create=calls.append)

    md_path = root / "Projects" / "foo" / "note.md"
    handler.on_created(FileCreatedEvent(str(md_path)))

    time.sleep(WAIT)
    assert calls == [md_path]


def test_on_create_fires_for_pdf_in_inbox(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, _ = _make_handler(tmp_path, on_create=calls.append)

    pdf_path = root / "inbox" / "report.pdf"
    handler.on_created(FileCreatedEvent(str(pdf_path)))

    time.sleep(WAIT)
    assert calls == [pdf_path]


def test_on_create_skips_attachment_dir(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, vault_cfg = _make_handler(tmp_path, on_create=calls.append)

    att_dir = root / "Projects" / "Alpha" / vault_cfg.attachment_dir
    att_dir.mkdir(parents=True, exist_ok=True)
    att_file = att_dir / "doc.pdf"
    handler.on_created(FileCreatedEvent(str(att_file)))

    time.sleep(WAIT)
    assert calls == []


def test_on_create_skips_dotfile(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, _ = _make_handler(tmp_path, on_create=calls.append)

    handler.on_created(FileCreatedEvent(str(root / "inbox" / ".hidden")))

    time.sleep(WAIT)
    assert calls == []


def test_on_create_skips_sync_conflict(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, _ = _make_handler(tmp_path, on_create=calls.append)

    conflict = root / "inbox" / "note.sync-conflict-2024-01-01.md"
    handler.on_created(FileCreatedEvent(str(conflict)))

    time.sleep(WAIT)
    assert calls == []


def test_on_create_skips_ignore_dirs(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, _ = _make_handler(tmp_path, on_create=calls.append)

    # File inside .obsidian/ (in IGNORE_DIRS)
    obsidian_file = root / ".obsidian" / "config.json"
    handler.on_created(FileCreatedEvent(str(obsidian_file)))

    time.sleep(WAIT)
    assert calls == []


# ---------------------------------------------------------------------------
# Debounce: rapid events on same path coalesced to one callback
# ---------------------------------------------------------------------------


def test_debounce_rapid_creates_fire_once(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, _ = _make_handler(tmp_path, on_create=calls.append, debounce=0.05)

    md_path = root / "inbox" / "note.md"
    event = FileCreatedEvent(str(md_path))
    # Three rapid events — each cancels previous timer
    handler.on_created(event)
    handler.on_created(event)
    handler.on_created(event)

    time.sleep(0.2)  # > debounce window
    assert len(calls) == 1
    assert calls[0] == md_path


# ---------------------------------------------------------------------------
# on_modify
# ---------------------------------------------------------------------------


def test_on_modify_fires_for_md(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, _ = _make_handler(tmp_path, on_modify=calls.append)

    md_path = root / "Projects" / "foo" / "note.md"
    handler.on_modified(FileModifiedEvent(str(md_path)))

    time.sleep(WAIT)
    assert calls == [md_path]


def test_on_modify_skips_binary(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, _ = _make_handler(tmp_path, on_modify=calls.append)

    pdf_path = root / "inbox" / "report.pdf"
    handler.on_modified(FileModifiedEvent(str(pdf_path)))

    time.sleep(WAIT)
    assert calls == []


def test_on_modify_fires_for_md_in_attachment_dir(tmp_path: Path) -> None:
    """MD files inside attachment/ dirs are notes — watcher fires for them."""
    calls: list[Path] = []
    handler, root, vault_cfg = _make_handler(tmp_path, on_modify=calls.append)

    att_dir = root / "Projects" / "Alpha" / vault_cfg.attachment_dir
    att_dir.mkdir(parents=True, exist_ok=True)
    md_in_att = att_dir / "doc.md"
    handler.on_modified(FileModifiedEvent(str(md_in_att)))

    time.sleep(WAIT)
    assert calls == [md_in_att]


def test_on_modify_skips_binary_in_attachment_dir(tmp_path: Path) -> None:
    """Non-.md files inside per-project attachment/ are skipped (TD-023 fix)."""
    calls: list[Path] = []
    handler, root, vault_cfg = _make_handler(tmp_path, on_modify=calls.append)

    att_dir = root / "Projects" / "Alpha" / vault_cfg.attachment_dir
    att_dir.mkdir(parents=True, exist_ok=True)
    pdf_in_att = att_dir / "report.pdf"
    handler.on_modified(FileModifiedEvent(str(pdf_in_att)))

    time.sleep(WAIT)
    assert calls == []


# ---------------------------------------------------------------------------
# on_delete
# ---------------------------------------------------------------------------


def test_on_delete_fires_for_md(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, _ = _make_handler(tmp_path, on_delete=calls.append)

    md_path = root / "inbox" / "gone.md"
    handler.on_deleted(FileDeletedEvent(str(md_path)))

    time.sleep(WAIT)
    assert calls == [md_path]


def test_on_delete_skips_attachment_dir(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, vault_cfg = _make_handler(tmp_path, on_delete=calls.append)

    att_dir = root / "Projects" / "Alpha" / vault_cfg.attachment_dir
    att_dir.mkdir(parents=True, exist_ok=True)
    handler.on_deleted(FileDeletedEvent(str(att_dir / "old.pdf")))

    time.sleep(WAIT)
    assert calls == []


# ---------------------------------------------------------------------------
# on_moved
# ---------------------------------------------------------------------------


def test_on_move_fires_for_internal_rename(tmp_path: Path) -> None:
    move_calls: list[tuple[Path, Path]] = []
    handler, root, _ = _make_handler(
        tmp_path, on_move=lambda s, d: move_calls.append((s, d))
    )

    src = root / "inbox" / "old.md"
    dst = root / "Projects" / "new.md"
    handler.on_moved(FileMovedEvent(str(src), str(dst)))

    time.sleep(WAIT)
    assert move_calls == [(src, dst)]


def test_on_moved_external_src_triggers_on_create(tmp_path: Path) -> None:
    """FileMovedEvent where src is outside vault fires on_create(dst)."""
    create_calls: list[Path] = []
    move_calls: list[tuple[Path, Path]] = []
    handler, root, _ = _make_handler(
        tmp_path,
        on_create=create_calls.append,
        on_move=lambda s, d: move_calls.append((s, d)),
    )

    # src is outside vault root
    external_src = tmp_path / "downloads" / "file.md"
    dst = root / "inbox" / "file.md"
    handler.on_moved(FileMovedEvent(str(external_src), str(dst)))

    time.sleep(WAIT)
    assert create_calls == [dst]
    assert move_calls == []


def test_on_moved_skips_when_dst_in_attachment(tmp_path: Path) -> None:
    move_calls: list[tuple[Path, Path]] = []
    create_calls: list[Path] = []
    handler, root, vault_cfg = _make_handler(
        tmp_path,
        on_create=create_calls.append,
        on_move=lambda s, d: move_calls.append((s, d)),
    )

    # Pipeline artifact: binary moved to Projects/*/attachment/
    att_dir = root / "Projects" / "Alpha" / vault_cfg.attachment_dir
    att_dir.mkdir(parents=True, exist_ok=True)
    src = root / "inbox" / "report.pdf"
    dst = att_dir / "report.pdf"
    handler.on_moved(FileMovedEvent(str(src), str(dst)))

    time.sleep(WAIT)
    assert move_calls == []
    assert create_calls == []


# ---------------------------------------------------------------------------
# Module import guard
# ---------------------------------------------------------------------------


def test_watcher_module_has_no_pipeline_or_llm_imports() -> None:
    """vault/watcher.py must not import from pipelines/ or llm/ at module scope.

    core.config types (VaultConfig) are allowed under TYPE_CHECKING — they do not
    trigger vault validation at import time.

    Lazy imports inside function/method bodies are allowed (they do not execute at
    import time and therefore do not trigger vault validation).
    """
    import ast
    import pathlib

    watcher_src = (
        pathlib.Path(__file__).parent.parent.parent / "src" / "vault" / "watcher.py"
    )
    tree = ast.parse(watcher_src.read_text())

    forbidden_prefixes = ("pipelines", "llm")
    # Only check top-level statements (module scope), not nested function bodies.
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in forbidden_prefixes:
                    assert not alias.name.startswith(prefix), (
                        f"vault/watcher.py imports '{alias.name}' at module scope"
                    )
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for prefix in forbidden_prefixes:
                assert not module.startswith(prefix), (
                    f"vault/watcher.py imports from '{module}' at module scope"
                )


# ---------------------------------------------------------------------------
# _is_binary helper
# ---------------------------------------------------------------------------


def test_is_binary_returns_true_for_pdf():
    from vault.watcher import _is_binary

    assert _is_binary(Path("report.pdf")) is True


def test_is_binary_returns_true_for_docx():
    from vault.watcher import _is_binary

    assert _is_binary(Path("budget.docx")) is True


def test_is_binary_returns_false_for_md():
    from vault.watcher import _is_binary

    assert _is_binary(Path("note.md")) is False


def test_is_binary_case_insensitive():
    from vault.watcher import _is_binary

    assert _is_binary(Path("README.MD")) is False
    assert _is_binary(Path("image.PNG")) is True


# ---------------------------------------------------------------------------
# _sibling_for helper
# ---------------------------------------------------------------------------


def test_sibling_for_returns_summaries_path_for_project_binary():
    from vault.watcher import _sibling_for
    from core.config import VaultConfig

    root = Path("/vault")
    vc = VaultConfig(root=root)
    binary = root / "Projects" / "Alpha" / "attachment" / "report.pdf"
    result = _sibling_for(binary, vc)
    assert (
        result
        == root / "Projects" / "Alpha" / "attachment" / ".summaries" / "report.pdf.md"
    )


def test_sibling_for_returns_summaries_path_for_inbox_binary():
    from vault.watcher import _sibling_for
    from core.config import VaultConfig

    root = Path("/vault")
    vc = VaultConfig(root=root)
    binary = root / "inbox" / "report.pdf"
    result = _sibling_for(binary, vc)
    assert result == root / "inbox" / ".summaries" / "report.pdf.md"


def test_sibling_for_respects_custom_summaries_subdir():
    from vault.watcher import _sibling_for
    from core.config import VaultConfig

    root = Path("/vault")
    vc = VaultConfig(root=root, summaries_subdir=".sums")
    binary = root / "inbox" / "slides.pptx"
    result = _sibling_for(binary, vc)
    assert result == root / "inbox" / ".sums" / "slides.pptx.md"


def test_sibling_for_distinct_for_same_stem_different_extensions():
    """report.pdf and report.docx must yield distinct siblings (issue #4 fix)."""
    from vault.watcher import _sibling_for
    from core.config import VaultConfig

    root = Path("/vault")
    vc = VaultConfig(root=root)
    pdf = root / "inbox" / "report.pdf"
    docx = root / "inbox" / "report.docx"
    assert _sibling_for(pdf, vc) != _sibling_for(docx, vc)


# ---------------------------------------------------------------------------
# Phase 3 — Binary sync callbacks
# ---------------------------------------------------------------------------


def test_on_deleted_binary_removes_sibling_from_db(tmp_path: Path, monkeypatch):
    """Binary delete → documents.delete_by_path called for sibling path."""
    from vault.watcher import _VaultEventHandler
    from core.config import VaultConfig

    root = tmp_path / "vault"
    root.mkdir()
    vault_cfg = VaultConfig(root=root)

    delete_calls: list[str] = []

    def fake_delete_by_path(vault_path, db_path=None):
        delete_calls.append(vault_path)
        from core.result import Success

        return Success(1)

    monkeypatch.setattr("vault.watcher.delete_by_path", fake_delete_by_path)

    def _fake_audit_write(*args, **kwargs):
        from core.result import Success

        return Success(None)

    monkeypatch.setattr("vault.watcher.audit_write", _fake_audit_write)

    handler = _VaultEventHandler(
        root=root,
        vault_config=vault_cfg,
        on_create=lambda p: None,
        on_modify=lambda p: None,
        on_delete=lambda p: None,
        on_move=lambda s, d: None,
        debounce_seconds=0.01,
    )

    # Binary file at inbox/report.pdf → sibling at inbox/.summaries/report.pdf.md
    pdf_path = root / "inbox" / "report.pdf"
    handler.on_deleted(FileDeletedEvent(str(pdf_path)))

    time.sleep(0.1)
    expected_sibling = "inbox/.summaries/report.pdf.md"
    assert expected_sibling in delete_calls, (
        f"Expected delete_by_path for {expected_sibling}, got {delete_calls}"
    )


def test_on_deleted_binary_in_managed_attachment_dir_fires_sync(
    tmp_path: Path, monkeypatch
):
    """TD-030 regression: binary deleted from Projects/<A>/attachment/ MUST fire _handle_binary_delete.

    Previously _should_skip ran before binary-sync dispatch, so non-.md files inside a managed
    attachment subtree never reached _handle_binary_delete — the headline Brief #3 Phase 3
    scenario (user deletes Projects/A/attachment/Q2.pdf in Finder) was silently broken.
    """
    from vault.watcher import _VaultEventHandler
    from core.config import VaultConfig

    root = tmp_path / "vault"
    root.mkdir()
    vault_cfg = VaultConfig(root=root)

    delete_calls: list[str] = []

    def fake_delete_by_path(vault_path, db_path=None):
        delete_calls.append(vault_path)
        from core.result import Success

        return Success(1)

    monkeypatch.setattr("vault.watcher.delete_by_path", fake_delete_by_path)

    def _fake_audit_write(*args, **kwargs):
        from core.result import Success

        return Success(None)

    monkeypatch.setattr("vault.watcher.audit_write", _fake_audit_write)

    user_delete_calls: list[Path] = []

    handler = _VaultEventHandler(
        root=root,
        vault_config=vault_cfg,
        on_create=lambda p: None,
        on_modify=lambda p: None,
        on_delete=lambda p: user_delete_calls.append(p),
        on_move=lambda s, d: None,
        debounce_seconds=0.01,
    )

    # Binary in managed attachment subtree — _should_skip would skip the user callback,
    # but _handle_binary_delete MUST still fire for sibling sync.
    pdf_path = root / "Projects" / "Alpha" / "attachment" / "Q2.pdf"
    handler.on_deleted(FileDeletedEvent(str(pdf_path)))

    time.sleep(0.1)
    expected_sibling = "Projects/Alpha/attachment/.summaries/Q2.pdf.md"
    assert expected_sibling in delete_calls, (
        f"Expected delete_by_path for {expected_sibling}, got {delete_calls}"
    )
    # User callback should NOT fire (binary in managed dir → _should_skip filters)
    assert user_delete_calls == [], (
        f"User on_delete callback must NOT fire for managed-attachment binary, got {user_delete_calls}"
    )


def test_on_moved_binary_same_folder_renames_sibling(tmp_path: Path, monkeypatch):
    """Binary rename in same folder → sibling renamed + attachment_path updated."""
    from vault.watcher import _VaultEventHandler
    from core.config import VaultConfig
    from vault.frontmatter import NoteMetadata

    root = tmp_path / "vault"
    root.mkdir()
    vault_cfg = VaultConfig(root=root)

    # Create old sibling file on disk so move_note can find it
    summaries_dir = root / "Projects" / "Alpha" / "attachment" / ".summaries"
    summaries_dir.mkdir(parents=True)
    old_sibling = summaries_dir / "Q2 Report.pdf.md"
    old_sibling.write_text(
        "---\nattachment_path: Projects/Alpha/attachment/Q2 Report.pdf\n---\n# Summary\n",
        encoding="utf-8",
    )

    move_calls: list[tuple] = []
    write_calls: list[tuple] = []
    rename_calls: list[tuple] = []

    def fake_move_note(src, dst, actor):
        move_calls.append((str(src), str(dst), actor))
        from core.result import Success

        return Success(None)

    def fake_write_note(path, body, metadata, actor):
        write_calls.append((str(path), body, metadata.attachment_path, actor))
        from core.result import Success

        return Success(MagicMock())

    def fake_rename(old, new, db_path=None):
        rename_calls.append((old, new))
        from core.result import Success

        return Success(1)

    # read_note must return the existing sibling metadata
    from vault.reader import Note

    fake_note = Note(
        path=old_sibling,
        content="# Summary\n",
        metadata=NoteMetadata(
            attachment_path="Projects/Alpha/attachment/Q2 Report.pdf",
        ),
        content_hash="abc",
    )

    def fake_read_note(path):
        from core.result import Success

        return Success(fake_note)

    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.write_note", fake_write_note)
    monkeypatch.setattr("vault.watcher.rename_doc", fake_rename)
    monkeypatch.setattr("vault.watcher.read_note", fake_read_note)

    def _fake_audit_write(*args, **kwargs):
        from core.result import Success

        return Success(None)

    monkeypatch.setattr("vault.watcher.audit_write", _fake_audit_write)

    handler = _VaultEventHandler(
        root=root,
        vault_config=vault_cfg,
        on_create=lambda p: None,
        on_modify=lambda p: None,
        on_delete=lambda p: None,
        on_move=lambda s, d: None,
        debounce_seconds=0.01,
    )

    src = root / "Projects" / "Alpha" / "attachment" / "Q2 Report.pdf"
    dst = root / "Projects" / "Alpha" / "attachment" / "Q2 Strategy.pdf"
    handler.on_moved(FileMovedEvent(str(src), str(dst)))

    time.sleep(0.1)
    # Verify sibling renamed via move_note
    old_sibling_str = str(old_sibling)
    new_sibling_str = str(
        root / "Projects" / "Alpha" / "attachment" / ".summaries" / "Q2 Strategy.pdf.md"
    )
    assert (old_sibling_str, new_sibling_str, "ai") in move_calls, (
        f"Expected move_note({old_sibling_str}, {new_sibling_str}), got {move_calls}"
    )
    # Verify documents.rename called
    assert (
        "Projects/Alpha/attachment/.summaries/Q2 Report.pdf.md",
        "Projects/Alpha/attachment/.summaries/Q2 Strategy.pdf.md",
    ) in rename_calls, f"Expected rename in DB, got {rename_calls}"
    # Verify attachment_path updated in write_note
    assert len(write_calls) == 1
    assert write_calls[0][2] == "Projects/Alpha/attachment/Q2 Strategy.pdf"


def test_on_moved_binary_different_folder_orphans_old_sibling(
    tmp_path: Path, monkeypatch
):
    """Binary moved to different folder → old sibling orphaned, not renamed."""
    from vault.watcher import _VaultEventHandler
    from core.config import VaultConfig

    root = tmp_path / "vault"
    root.mkdir()
    vault_cfg = VaultConfig(root=root)

    move_calls: list[tuple] = []
    delete_calls: list[str] = []

    def fake_move_note(src, dst, actor):
        move_calls.append((str(src), str(dst), actor))
        from core.result import Success

        return Success(None)

    def fake_delete_by_path(vault_path, db_path=None):
        delete_calls.append(vault_path)
        from core.result import Success

        return Success(1)

    def fake_get_by_path(vault_path, db_path=None):
        from core.result import Success

        return Success(None)  # Not in DB → triggers orphan fallback

    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.delete_by_path", fake_delete_by_path)
    monkeypatch.setattr("vault.watcher.get_by_path", fake_get_by_path)

    def _fake_audit_write(*args, **kwargs):
        from core.result import Success

        return Success(None)

    monkeypatch.setattr("vault.watcher.audit_write", _fake_audit_write)

    handler = _VaultEventHandler(
        root=root,
        vault_config=vault_cfg,
        on_create=lambda p: None,
        on_modify=lambda p: None,
        on_delete=lambda p: None,
        on_move=lambda s, d: None,
        debounce_seconds=0.01,
        binary_settle_seconds=0.01,
    )

    # Binary moved from Projects/Alpha/attachment/ to Domain/Finance/attachment/
    src = root / "Projects" / "Alpha" / "attachment" / "report.pdf"
    dst = root / "Domain" / "Finance" / "attachment" / "report.pdf"
    handler.on_moved(FileMovedEvent(str(src), str(dst)))

    time.sleep(0.1)
    # Old sibling should be deleted from DB, NOT renamed
    expected_sibling = "Projects/Alpha/attachment/.summaries/report.pdf.md"
    assert expected_sibling in delete_calls, (
        f"Expected delete_by_path for {expected_sibling}, got {delete_calls}"
    )
    assert len(move_calls) == 0, f"Expected no move_note calls, got {move_calls}"


# ---------------------------------------------------------------------------
# Phase 4.3 — Folder Handling (pending-folder registry + ThreadPoolExecutor)
# ---------------------------------------------------------------------------

FOLDER_COOLDOWN = 0.05  # 50ms — short enough for fast tests
FOLDER_WAIT = 0.2  # 200ms — long enough for folder timer to fire


def _make_handler_with_folder(
    tmp_path: Path,
    *,
    on_create=None,
    on_modify=None,
    on_delete=None,
    on_move=None,
    on_folder_stable=None,
    debounce: float = DEBOUNCE,
    folder_cooldown: float = FOLDER_COOLDOWN,
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
        folder_cooldown=folder_cooldown,
        on_folder_stable=on_folder_stable,
    )
    return handler, root, vault_cfg


def test_dir_created_event_starts_pending_timer(tmp_path: Path) -> None:
    """DirCreatedEvent → folder registered in _pending_folders and _pending_folder_paths."""
    handler, root, _ = _make_handler_with_folder(tmp_path)

    folder_path = root / "inbox" / "mydir"
    handler.on_created(DirCreatedEvent(str(folder_path)))

    folder_key = str(folder_path)
    assert folder_key in handler._pending_folders, (
        f"Expected {folder_key!r} in _pending_folders, got {list(handler._pending_folders.keys())}"
    )
    assert folder_key in handler._pending_folder_paths, (
        f"Expected {folder_key!r} in _pending_folder_paths"
    )
    # Clean up timer to avoid thread leaks
    handler._pending_folders[folder_key].cancel()


def test_file_in_pending_folder_suppresses_on_create(tmp_path: Path) -> None:
    """File created inside a pending folder → on_create NOT called; folder timer reset."""
    create_calls: list[Path] = []
    # Use a very long cooldown so the timer doesn't fire during assertion checks
    handler, root, _ = _make_handler_with_folder(
        tmp_path, on_create=create_calls.append, folder_cooldown=60.0
    )

    folder_path = root / "inbox" / "mydir"
    # Register folder manually (simulate DirCreatedEvent already fired)
    handler._register_pending_folder(folder_path)
    folder_key = str(folder_path)
    original_timer = handler._pending_folders[folder_key]

    # Now fire a FileCreatedEvent for a file inside that folder
    file_inside = folder_path / "note.md"
    handler.on_created(FileCreatedEvent(str(file_inside)))

    # on_create must NOT have been called (no sleep needed — suppression is synchronous)
    assert create_calls == [], (
        f"Expected on_create suppressed for file inside pending folder, got {create_calls}"
    )
    # Folder must still be pending (long cooldown — timer hasn't fired)
    assert folder_key in handler._pending_folders, (
        "Expected folder to remain pending after file-inside event"
    )
    # A new timer was started (original cancelled and replaced)
    new_timer = handler._pending_folders[folder_key]
    assert new_timer is not original_timer, (
        "Expected folder timer to be reset (new timer object)"
    )
    # Clean up
    handler._pending_folders[folder_key].cancel()


def test_file_outside_pending_folder_dispatches_normally(tmp_path: Path) -> None:
    """File created outside any pending folder → normal on_create callback fires."""
    create_calls: list[Path] = []
    handler, root, _ = _make_handler_with_folder(
        tmp_path, on_create=create_calls.append, folder_cooldown=60.0
    )

    # Register a pending folder at /vault/inbox/mydir
    folder_path = root / "inbox" / "mydir"
    handler._register_pending_folder(folder_path)

    # Fire a FileCreatedEvent for a file OUTSIDE that folder
    other_file = root / "inbox" / "other.md"
    handler.on_created(FileCreatedEvent(str(other_file)))

    time.sleep(WAIT)
    assert create_calls == [other_file], (
        f"Expected on_create fired for file outside pending folder, got {create_calls}"
    )
    # Clean up
    handler._pending_folders[str(folder_path)].cancel()


def test_folder_stable_fires_on_folder_create_callback(tmp_path: Path) -> None:
    """VaultWatcher with on_folder_create: after cooldown, callback is called with folder path."""
    folder_calls: list[Path] = []
    root = tmp_path / "vault"
    root.mkdir()
    vault_cfg = VaultConfig(root=root)

    watcher = VaultWatcher(
        root=root,
        vault_config=vault_cfg,
        on_create=lambda p: None,
        on_modify=lambda p: None,
        on_delete=lambda p: None,
        on_move=lambda s, d: None,
        folder_cooldown_seconds=FOLDER_COOLDOWN,
        on_folder_create=folder_calls.append,
    )

    folder_path = root / "inbox" / "mydir"
    watcher._handler.on_created(DirCreatedEvent(str(folder_path)))

    time.sleep(FOLDER_WAIT)
    assert folder_calls == [folder_path], (
        f"Expected on_folder_create called with {folder_path}, got {folder_calls}"
    )


def test_stale_folder_timer_fire_is_ignored(tmp_path: Path) -> None:
    """C2 regression: a stale timer (reset by a later file event) must NOT fire the callback.

    Race: timer A is installed, a FileCreatedEvent resets it (installing timer B under
    the same key) AFTER A fired but BEFORE A's _fire_folder_stable grabbed the lock.
    A's _fire then ran with a stale token and used to pop B without cancelling it →
    double capture_folder on the same folder. The token guard must make stale fires no-ops.

    Deterministic simulation: drive _fire_folder_stable directly with controlled tokens.
    """
    folder_calls: list[Path] = []
    # Long cooldown so no real timer fires during the simulation.
    handler, root, _ = _make_handler_with_folder(
        tmp_path, on_folder_stable=folder_calls.append, folder_cooldown=60.0
    )

    folder_path = root / "inbox" / "racy"
    folder_key = str(folder_path)

    # Install timer A (token captured here).
    handler._register_pending_folder(folder_path)
    token_a = handler._folder_tokens[folder_key]

    # A reset installs timer B under the same key (token advances).
    handler._reset_folder_timer(folder_key)
    token_b = handler._folder_tokens[folder_key]
    assert token_b != token_a

    # Stale fire from timer A — must be ignored (token mismatch).
    handler._fire_folder_stable(folder_path, token_a)
    assert folder_calls == [], "Stale timer A fire must be a no-op"
    # B must still be pending and uncancelled.
    assert folder_key in handler._pending_folders

    # Genuine fire from timer B — fires exactly once and clears the registry.
    handler._fire_folder_stable(folder_path, token_b)
    assert folder_calls == [folder_path]
    assert folder_key not in handler._pending_folders
    assert folder_key not in handler._pending_folder_paths

    # Clean up any lingering timer object.
    leftover = handler._pending_folders.get(folder_key)
    if leftover is not None:
        leftover.cancel()


# ---------------------------------------------------------------------------
# I6 — folder-capture concurrency (the gap that hid C1/C2)
# ---------------------------------------------------------------------------


def test_folder_max_workers_one_serializes_captures(
    tmp_path: Path, monkeypatch
) -> None:
    """folder_max_workers=1: a second folder drop queues behind the first (I6).

    Two folders go stable back-to-back. With a single executor worker the second
    capture_folder must not start until the first releases the worker.
    """
    root = tmp_path / "vault"
    root.mkdir()
    vault_cfg = VaultConfig(root=root)

    started = threading.Event()
    release_first = threading.Event()
    order: list[str] = []
    order_lock = threading.Lock()

    async def fake_capture_folder(folder_path):
        with order_lock:
            order.append(f"start:{folder_path.name}")
        if folder_path.name == "first":
            started.set()
            # Block the only worker until the test releases it.
            release_first.wait(timeout=2.0)
        with order_lock:
            order.append(f"end:{folder_path.name}")
        from core.result import Success

        return Success([])

    import pipelines.capture as capture_mod

    monkeypatch.setattr(capture_mod, "capture_folder", fake_capture_folder)

    watcher = VaultWatcher(
        root=root,
        vault_config=vault_cfg,
        on_create=lambda p: None,
        on_modify=lambda p: None,
        on_delete=lambda p: None,
        on_move=lambda s, d: None,
        folder_cooldown_seconds=FOLDER_COOLDOWN,
        folder_max_workers=1,
    )

    cb = watcher._on_folder_stable_callback
    cb(root / "inbox" / "first")
    assert started.wait(timeout=2.0), "first capture never started"
    cb(root / "inbox" / "second")

    # While the worker is held by "first", "second" must not have started.
    time.sleep(0.1)
    with order_lock:
        assert "start:second" not in order, (
            f"second capture ran before first released the worker: {order}"
        )

    # Release first; second now runs.
    release_first.set()
    deadline = time.time() + 2.0
    while time.time() < deadline:
        with order_lock:
            if "end:second" in order:
                break
        time.sleep(0.01)

    with order_lock:
        assert order[0] == "start:first"
        assert "end:first" in order
        assert order.index("start:second") > order.index("end:first"), (
            f"second must start only after first ends: {order}"
        )
    watcher.stop()


def test_capture_runs_on_executor_thread_not_caller(
    tmp_path: Path, monkeypatch
) -> None:
    """C-10: asyncio.run(capture_folder(...)) executes on a ThreadPoolExecutor worker,
    never on the calling (observer/timer) thread (I6).

    Uses the REAL executor path (no on_folder_create override) so the production
    asyncio.run-on-worker behaviour is exercised.
    """
    root = tmp_path / "vault"
    root.mkdir()
    vault_cfg = VaultConfig(root=root)

    done = threading.Event()
    seen: dict[str, int] = {}

    async def fake_capture_folder(folder_path):
        seen["worker_ident"] = threading.current_thread().ident
        done.set()
        from core.result import Success

        return Success([])

    import pipelines.capture as capture_mod

    monkeypatch.setattr(capture_mod, "capture_folder", fake_capture_folder)

    watcher = VaultWatcher(
        root=root,
        vault_config=vault_cfg,
        on_create=lambda p: None,
        on_modify=lambda p: None,
        on_delete=lambda p: None,
        on_move=lambda s, d: None,
        folder_cooldown_seconds=FOLDER_COOLDOWN,
        folder_max_workers=2,
    )

    caller_ident = threading.current_thread().ident
    # Drive the production stable-callback directly (real executor submit + asyncio.run).
    watcher._on_folder_stable_callback(root / "inbox" / "drop")

    assert done.wait(timeout=2.0), "capture_folder never ran"
    assert seen["worker_ident"] is not None
    assert seen["worker_ident"] != caller_ident, (
        "capture_folder ran on the calling thread, not an executor worker"
    )
    watcher.stop()


def test_pending_folder_removed_after_stable_fires(tmp_path: Path) -> None:
    """After folder timer fires, folder is no longer in _pending_folders or _pending_folder_paths."""
    handler, root, _ = _make_handler_with_folder(
        tmp_path, folder_cooldown=FOLDER_COOLDOWN
    )

    folder_path = root / "inbox" / "mydir"
    handler.on_created(DirCreatedEvent(str(folder_path)))

    folder_key = str(folder_path)
    # Initially present
    assert folder_key in handler._pending_folders

    # Wait for timer to fire
    time.sleep(FOLDER_WAIT)

    assert folder_key not in handler._pending_folders, (
        "Expected folder removed from _pending_folders after stable fires"
    )
    assert folder_key not in handler._pending_folder_paths, (
        "Expected folder removed from _pending_folder_paths after stable fires"
    )


# ---------------------------------------------------------------------------
# Phase 5, T4 — AI-output exclusion in _should_skip
# ---------------------------------------------------------------------------


class TestShouldSkipAiOutput:
    """6 tests: _should_skip returns True for files inside AI-output folders.

    AI-output folders are Briefings, Synthesis, Documentation — the folders
    the system writes to itself. Skipping them prevents infinite feedback loops.
    """

    def test_should_skip_returns_true_for_briefings_file(self, tmp_path: Path):
        handler, root, _ = _make_handler(tmp_path)
        path = root / "Briefings" / "2026" / "06_04.md"
        assert handler._should_skip(path) is True

    def test_should_skip_returns_true_for_synthesis_file(self, tmp_path: Path):
        handler, root, _ = _make_handler(tmp_path)
        path = root / "Synthesis" / "2026-W23.md"
        assert handler._should_skip(path) is True

    def test_should_skip_returns_true_for_documentation_file(self, tmp_path: Path):
        handler, root, _ = _make_handler(tmp_path)
        path = root / "Documentation" / "Alpha.md"
        assert handler._should_skip(path) is True

    def test_should_skip_returns_false_for_valid_project_path(self, tmp_path: Path):
        handler, root, _ = _make_handler(tmp_path)
        path = root / "Projects" / "Alpha" / "note.md"
        assert handler._should_skip(path) is False

    def test_should_skip_logs_debug_for_ai_output(self, tmp_path: Path, caplog):
        import logging

        handler, root, _ = _make_handler(tmp_path)
        path = root / "Briefings" / "daily.md"
        with caplog.at_level(logging.DEBUG, logger="vault.watcher"):
            result = handler._should_skip(path)
        assert result is True
        assert "watcher.skip.ai_output" in caplog.text, (
            f"Expected 'watcher.skip.ai_output' in logs, got: {caplog.text}"
        )

    def test_td033_guard_patch_vault_watcher_not_paths(
        self, tmp_path: Path, monkeypatch
    ):
        """TD-033: _is_ai_output must be patchable at vault.watcher._is_ai_output.

        Module-level imports in watcher.py copy the reference — patching
        vault.paths._is_ai_output would leave vault.watcher._is_ai_output
        pointing at the original. Tests MUST patch vault.watcher._is_ai_output.
        """
        handler, root, _ = _make_handler(tmp_path)

        # Patch vault.watcher._is_ai_output (correct TD-033 target)
        monkeypatch.setattr(
            "vault.watcher._is_ai_output",
            lambda path, vault_cfg: True,
        )

        # Even a valid project path should be skipped when _is_ai_output is forced True
        path = root / "Projects" / "Alpha" / "note.md"
        assert handler._should_skip(path) is True

        # Verify the original vault.paths._is_ai_output is NOT patched
        from vault.paths import _is_ai_output as real_is_ai_output
        from core.config import VaultConfig

        r = root
        vc = VaultConfig(root=r)
        # Real function still returns False for project paths
        assert real_is_ai_output(r / "Projects" / "Alpha" / "note.md", vc) is False


# ---------------------------------------------------------------------------
# TestHandleBinaryMoveG2BatchStamp
# ---------------------------------------------------------------------------


class TestHandleBinaryMoveG2BatchStamp:
    """Sub-step g2: stamp batch_id when a binary is re-homed into a batch subfolder.

    All tests drive _handle_binary_move(..., _settled=True) on a cross-folder move
    so the settle window is bypassed and g2 is reached directly.
    """

    def _make_handler_with_db(
        self, tmp_path: Path
    ) -> tuple[_VaultEventHandler, Path, VaultConfig]:
        root = tmp_path / "vault"
        root.mkdir(exist_ok=True)
        vault_cfg = VaultConfig(root=root)
        handler = _VaultEventHandler(
            root=root,
            vault_config=vault_cfg,
            on_create=lambda p: None,
            on_modify=lambda p: None,
            on_delete=lambda p: None,
            on_move=lambda s, d: None,
            debounce_seconds=DEBOUNCE,
            db_path=tmp_path / "kb.db",
        )
        return handler, root, vault_cfg

    def _fake_row(self):
        from storage.documents import DocumentRow

        return DocumentRow(
            id=1,
            vault_path="Projects/Alpha/attachment/.summaries/report.pdf.md",
            title="Report",
            summary="A summary",
            note_type="attachment-summary",
            confidence=0.9,
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            updated_by_human=False,
            content_hash="abc123",
        )

    def _common_mocks(
        self, monkeypatch, *, dst: Path, sibling_dir: Path, is_batch: bool = True
    ):
        from core.result import Success
        from vault.paths import Placement

        fake_placement = Placement(
            final_dir=dst.parent,
            sibling_dir=sibling_dir,
            needs_move=False,
        )
        monkeypatch.setattr(
            "vault.watcher._location_context", lambda p, vc: ("project", "Alpha")
        )
        monkeypatch.setattr(
            "vault.watcher.get_by_path", lambda vp: Success(self._fake_row())
        )
        monkeypatch.setattr(
            "vault.watcher.resolve_placement",
            lambda fp, lt, ln, vc: fake_placement,
        )
        monkeypatch.setattr("vault.watcher.write_note", lambda *a, **kw: Success(None))
        monkeypatch.setattr("vault.watcher.rename_doc", lambda *a, **kw: Success(1))
        monkeypatch.setattr("vault.watcher.audit_write", lambda *a, **kw: Success(None))
        monkeypatch.setattr("vault.watcher.delete_by_path", lambda vp: Success(1))
        monkeypatch.setattr("vault.watcher.is_batch_subfolder", lambda p, vc: is_batch)

    def test_g2_batch_stamp_reuses_existing_batch_id(self, tmp_path: Path, monkeypatch):
        """find_by_folder_path returns Success(42) → update_batch_id called with 42."""
        import vault.watcher as watcher_mod
        from core.result import Success

        handler, root, _ = self._make_handler_with_db(tmp_path)

        src = root / "Projects" / "Alpha" / "attachment" / "report.pdf"
        dst = root / "Projects" / "Alpha" / "subfolder" / "report.pdf"
        dst.parent.mkdir(parents=True, exist_ok=True)
        sibling_dir = dst.parent / ".summaries"
        sibling_dir.mkdir(parents=True, exist_ok=True)

        self._common_mocks(monkeypatch, dst=dst, sibling_dir=sibling_dir)

        update_calls: list[tuple] = []

        monkeypatch.setattr(
            watcher_mod.batches,
            "find_by_folder_path",
            lambda folder_vp, *, db_path: Success(42),
        )
        monkeypatch.setattr(
            "vault.watcher.update_batch_id",
            lambda vault_path, batch_id, db_path: (
                update_calls.append((vault_path, batch_id)) or Success(1)
            ),
        )

        handler._handle_binary_move(src, dst, _settled=True)

        assert len(update_calls) == 1
        assert update_calls[0][1] == 42

    def test_g2_batch_stamp_creates_new_batch_when_none_exists(
        self, tmp_path: Path, monkeypatch
    ):
        """find_by_folder_path returns Success(None) → insert new batch, update_batch_id with new ID."""
        import vault.watcher as watcher_mod
        from core.result import Success

        handler, root, _ = self._make_handler_with_db(tmp_path)

        src = root / "Projects" / "Alpha" / "attachment" / "report.pdf"
        dst = root / "Projects" / "Alpha" / "subfolder" / "report.pdf"
        dst.parent.mkdir(parents=True, exist_ok=True)
        sibling_dir = dst.parent / ".summaries"
        sibling_dir.mkdir(parents=True, exist_ok=True)

        self._common_mocks(monkeypatch, dst=dst, sibling_dir=sibling_dir)

        insert_calls: list[str] = []
        update_calls: list[tuple] = []

        monkeypatch.setattr(
            watcher_mod.batches,
            "find_by_folder_path",
            lambda folder_vp, *, db_path: Success(None),
        )
        monkeypatch.setattr(
            watcher_mod.batches,
            "insert",
            lambda *, folder_name, destination_type, destination_name, confidence, status, file_count, folder_path, db_path: (
                insert_calls.append(folder_path) or Success(99)
            ),
        )
        monkeypatch.setattr(
            "vault.watcher.update_batch_id",
            lambda vault_path, batch_id, db_path: (
                update_calls.append((vault_path, batch_id)) or Success(1)
            ),
        )

        handler._handle_binary_move(src, dst, _settled=True)

        assert len(insert_calls) == 1
        assert len(update_calls) == 1
        assert update_calls[0][1] == 99

    def test_g2_batch_stamp_skipped_when_not_batch_subfolder(
        self, tmp_path: Path, monkeypatch
    ):
        """is_batch_subfolder returns False → update_batch_id NOT called."""
        import vault.watcher as watcher_mod
        from core.result import Success

        handler, root, _ = self._make_handler_with_db(tmp_path)

        src = root / "Projects" / "Alpha" / "attachment" / "report.pdf"
        dst = root / "Projects" / "Alpha" / "report.pdf"
        dst.parent.mkdir(parents=True, exist_ok=True)
        sibling_dir = dst.parent / ".summaries"
        sibling_dir.mkdir(parents=True, exist_ok=True)

        self._common_mocks(
            monkeypatch, dst=dst, sibling_dir=sibling_dir, is_batch=False
        )

        update_calls: list = []
        monkeypatch.setattr(
            watcher_mod.batches,
            "find_by_folder_path",
            lambda *a, **kw: Success(42),
        )
        monkeypatch.setattr(
            "vault.watcher.update_batch_id",
            lambda *a, **kw: update_calls.append(a) or Success(1),
        )

        handler._handle_binary_move(src, dst, _settled=True)

        assert update_calls == []

    def test_g2_batch_stamp_lookup_failure_does_not_prevent_completion(
        self, tmp_path: Path, monkeypatch
    ):
        """find_by_folder_path returns Failure → update_batch_id NOT called; function completes."""
        import vault.watcher as watcher_mod
        from core.result import Failure, Success

        handler, root, _ = self._make_handler_with_db(tmp_path)

        src = root / "Projects" / "Alpha" / "attachment" / "report.pdf"
        dst = root / "Projects" / "Alpha" / "subfolder" / "report.pdf"
        dst.parent.mkdir(parents=True, exist_ok=True)
        sibling_dir = dst.parent / ".summaries"
        sibling_dir.mkdir(parents=True, exist_ok=True)

        self._common_mocks(monkeypatch, dst=dst, sibling_dir=sibling_dir)

        update_calls: list = []
        monkeypatch.setattr(
            watcher_mod.batches,
            "find_by_folder_path",
            lambda *a, **kw: Failure(error="db error", recoverable=True, context={}),
        )
        monkeypatch.setattr(
            "vault.watcher.update_batch_id",
            lambda *a, **kw: update_calls.append(a) or Success(1),
        )

        handler._handle_binary_move(src, dst, _settled=True)

        assert update_calls == []
