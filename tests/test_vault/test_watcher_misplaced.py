"""
tests/test_vault/test_watcher_misplaced.py

Unit tests for live-watcher misplaced-md sweep (Fix 4).

When a user drops an .md directly under Projects/ or Domain/ (bare root,
no subfolder), the watcher should sweep it to inbox rather than indexing
it as a phantom project/domain.

All collaborator patches target vault.watcher.* per TD-033.
CONFIG is never imported at module scope.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


from core.config import VaultConfig
from core.result import Success
from vault.watcher import _VaultEventHandler


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
# T1 — Misplaced .md at bare Projects/ root fires misplaced sweep
# ---------------------------------------------------------------------------


def test_on_created_misplaced_md_fires_sweep(tmp_path: Path):
    """Projects/stray.md -> debounce with misplaced: prefix fires."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    projects_dir = root / "Projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    stray = projects_dir / "stray.md"
    stray.write_text("---\ntitle: stray\n---\n# Stray", encoding="utf-8")

    from watchdog.events import FileCreatedEvent

    event = FileCreatedEvent(str(stray))
    handler.on_created(event)

    # Verify debounce timer was created with misplaced: prefix
    assert f"misplaced:{stray}" in handler._timers


# ---------------------------------------------------------------------------
# T2 — Real project .md is NOT swept
# ---------------------------------------------------------------------------


def test_on_created_real_project_md_not_swept(tmp_path: Path):
    """Projects/Alpha/note.md -> normal create path (no misplaced debounce)."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    proj_dir = root / "Projects" / "Alpha"
    proj_dir.mkdir(parents=True, exist_ok=True)
    note = proj_dir / "note.md"
    note.write_text("---\ntitle: note\n---\n# Note", encoding="utf-8")

    from watchdog.events import FileCreatedEvent

    event = FileCreatedEvent(str(note))
    handler.on_created(event)

    # No misplaced debounce key should exist
    assert f"misplaced:{note}" not in handler._timers
    # Normal create debounce key should exist
    assert str(note) in handler._timers


# ---------------------------------------------------------------------------
# T3 — _handle_misplaced_md moves to inbox and triggers on_create
# ---------------------------------------------------------------------------


def test_handle_misplaced_md_moves_to_inbox(tmp_path: Path, monkeypatch):
    """Patch move_note and audit_write; verify move to inbox + audit + on_create."""
    on_create_calls: list[Path] = []
    handler, root, vault_cfg = _make_handler(
        tmp_path, on_create=lambda p: on_create_calls.append(p)
    )

    # Create the inbox dir so inbox_path exists
    inbox = root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    projects_dir = root / "Projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    stray = projects_dir / "stray.md"
    stray.write_text("---\ntitle: stray\n---\n# Stray", encoding="utf-8")

    move_calls: list = []
    audit_calls: list = []

    def fake_move_note(src, dst, actor):
        move_calls.append((src, dst, actor))
        return Success(MagicMock())

    def fake_audit_write(*args, **kwargs):
        audit_calls.append((args, kwargs))
        return Success(None)

    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)
    monkeypatch.setattr("vault.watcher.audit_write", fake_audit_write)

    handler._handle_misplaced_md(stray)

    # move_note called with correct inbox path
    assert len(move_calls) == 1
    src, dst, actor = move_calls[0]
    assert src == stray
    assert dst == inbox / "stray.md"
    assert actor == "ai"

    # audit written with outcome=MISPLACED
    assert len(audit_calls) == 1
    _, audit_kwargs = audit_calls[0]
    assert audit_kwargs["outcome"] == "MISPLACED"
    assert audit_kwargs["pipeline"] == "watcher"

    # on_create called with inbox destination
    assert len(on_create_calls) == 1
    assert on_create_calls[0] == inbox / "stray.md"


# ---------------------------------------------------------------------------
# T4 — _handle_misplaced_md file gone -> early return
# ---------------------------------------------------------------------------


def test_handle_misplaced_md_file_gone(tmp_path: Path, monkeypatch):
    """Path doesn't exist -> returns early, no move_note call."""
    handler, root, vault_cfg = _make_handler(tmp_path)

    projects_dir = root / "Projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    stray = projects_dir / "stray.md"
    # Don't create the file — it doesn't exist

    move_calls: list = []

    def fake_move_note(src, dst, actor):
        move_calls.append((src, dst, actor))
        return Success(MagicMock())

    monkeypatch.setattr("vault.watcher.move_note", fake_move_note)

    handler._handle_misplaced_md(stray)

    # No move_note call
    assert len(move_calls) == 0
