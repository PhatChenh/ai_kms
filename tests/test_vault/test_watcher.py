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
) -> tuple[_VaultEventHandler, Path, Path]:
    root = tmp_path / "vault"
    root.mkdir(exist_ok=True)
    attachment_path = root / "attachment"
    attachment_path.mkdir(exist_ok=True)

    handler = _VaultEventHandler(
        root=root,
        attachment_path=attachment_path,
        on_create=on_create or (lambda p: None),
        on_modify=on_modify or (lambda p: None),
        on_delete=on_delete or (lambda p: None),
        on_move=on_move or (lambda s, d: None),
        debounce_seconds=debounce,
    )
    return handler, root, attachment_path


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
    handler, root, attachment_path = _make_handler(tmp_path, on_create=calls.append)

    att_file = attachment_path / "doc.pdf"
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


def test_on_modify_skips_attachment_dir(tmp_path: Path) -> None:
    calls: list[Path] = []
    handler, root, attachment_path = _make_handler(tmp_path, on_modify=calls.append)

    md_in_att = attachment_path / "doc.md"
    handler.on_modified(FileModifiedEvent(str(md_in_att)))

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
    handler, root, attachment_path = _make_handler(tmp_path, on_delete=calls.append)

    handler.on_deleted(FileDeletedEvent(str(attachment_path / "old.pdf")))

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
    handler, root, attachment_path = _make_handler(
        tmp_path,
        on_create=create_calls.append,
        on_move=lambda s, d: move_calls.append((s, d)),
    )

    # Pipeline artifact: binary moved to attachment/
    src = root / "inbox" / "report.pdf"
    dst = attachment_path / "report.pdf"
    handler.on_moved(FileMovedEvent(str(src), str(dst)))

    time.sleep(WAIT)
    assert move_calls == []
    assert create_calls == []


# ---------------------------------------------------------------------------
# Module import guard
# ---------------------------------------------------------------------------


def test_watcher_module_has_no_pipeline_or_llm_imports() -> None:
    """vault/watcher.py must not import from pipelines/ or llm/ at module scope."""
    import ast
    import pathlib

    watcher_src = pathlib.Path(__file__).parent.parent.parent / "vault" / "watcher.py"
    tree = ast.parse(watcher_src.read_text())

    forbidden_prefixes = ("pipelines", "llm", "core.config")
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
