"""
tests/test_vault/test_watcher.py

Unit tests for vault/watcher.py — VaultWatcher event dispatch and debounce.

Tests call _VaultEventHandler methods directly with synthetic watchdog events
(no real observer started, no real filesystem events). debounce_seconds is set
to a short value and tests sleep briefly to let threading.Timer fire.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from watchdog.events import (
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
    handler, root, _ = _make_handler(tmp_path, on_move=lambda s, d: move_calls.append((s, d)))

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
    """
    import ast
    import pathlib

    watcher_src = pathlib.Path(__file__).parent.parent.parent / "vault" / "watcher.py"
    tree = ast.parse(watcher_src.read_text())

    forbidden_prefixes = ("pipelines", "llm")
    for node in ast.walk(tree):
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
    assert result == root / "Projects" / "Alpha" / "attachment" / ".summaries" / "report.pdf.md"


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

    monkeypatch.setattr(
        "vault.watcher.delete_by_path", fake_delete_by_path
    )
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


def test_on_deleted_binary_in_managed_attachment_dir_fires_sync(tmp_path: Path, monkeypatch):
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
    summaries_dir = (
        root / "Projects" / "Alpha" / "attachment" / ".summaries"
    )
    summaries_dir.mkdir(parents=True)
    old_sibling = summaries_dir / "Q2 Report.pdf.md"
    old_sibling.write_text("---\nattachment_path: Projects/Alpha/attachment/Q2 Report.pdf\n---\n# Summary\n", encoding="utf-8")

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
    new_sibling_str = str(root / "Projects" / "Alpha" / "attachment" / ".summaries" / "Q2 Strategy.pdf.md")
    assert (old_sibling_str, new_sibling_str, "ai") in move_calls, (
        f"Expected move_note({old_sibling_str}, {new_sibling_str}), got {move_calls}"
    )
    # Verify documents.rename called
    assert ("Projects/Alpha/attachment/.summaries/Q2 Report.pdf.md",
            "Projects/Alpha/attachment/.summaries/Q2 Strategy.pdf.md") in rename_calls, (
        f"Expected rename in DB, got {rename_calls}"
    )
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

    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
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
    assert len(move_calls) == 0, (
        f"Expected no move_note calls, got {move_calls}"
    )
