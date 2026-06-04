"""
tests/test_vault/test_move_guard.py

Tests for MoveGuard + VaultWatcher MoveGuard wiring.

CONFIG is never imported at module scope.
"""

from __future__ import annotations

from pathlib import Path

from core.config import VaultConfig
from vault.move_guard import MoveGuard
from vault.watcher import VaultWatcher


def test_vault_watcher_default_constructs_move_guard(tmp_path: Path):
    """VaultWatcher creates a MoveGuard when none is passed."""
    root = tmp_path / "vault"
    root.mkdir()
    vault_cfg = VaultConfig(root=root)

    noop = lambda *a: None
    watcher = VaultWatcher(
        root=root,
        vault_config=vault_cfg,
        on_create=noop,
        on_modify=noop,
        on_delete=noop,
        on_move=noop,
    )
    try:
        assert isinstance(watcher._move_guard, MoveGuard)
    finally:
        watcher.stop()


def test_vault_watcher_accepts_explicit_move_guard(tmp_path: Path):
    """VaultWatcher uses the MoveGuard passed explicitly."""
    root = tmp_path / "vault"
    root.mkdir()
    vault_cfg = VaultConfig(root=root)
    guard = MoveGuard()

    noop = lambda *a: None
    watcher = VaultWatcher(
        root=root,
        vault_config=vault_cfg,
        on_create=noop,
        on_modify=noop,
        on_delete=noop,
        on_move=noop,
        move_guard=guard,
    )
    try:
        assert watcher._move_guard is guard
    finally:
        watcher.stop()


def test_vault_watcher_move_guard_is_move_guard_instance(tmp_path: Path):
    """VaultWatcher._move_guard is always a MoveGuard instance."""
    root = tmp_path / "vault"
    root.mkdir()
    vault_cfg = VaultConfig(root=root)

    noop = lambda *a: None
    watcher = VaultWatcher(
        root=root,
        vault_config=vault_cfg,
        on_create=noop,
        on_modify=noop,
        on_delete=noop,
        on_move=noop,
    )
    try:
        assert isinstance(watcher._move_guard, MoveGuard)
        # Should be usable
        watcher._move_guard.register(root / "test.pdf")
        assert watcher._move_guard.check_and_consume(root / "test.pdf") is True
        assert watcher._move_guard.check_and_consume(root / "test.pdf") is False
    finally:
        watcher.stop()


def test_vault_watcher_passes_move_guard_to_handler(tmp_path: Path):
    """VaultWatcher passes its MoveGuard to the internal _VaultEventHandler."""
    root = tmp_path / "vault"
    root.mkdir()
    vault_cfg = VaultConfig(root=root)

    noop = lambda *a: None
    watcher = VaultWatcher(
        root=root,
        vault_config=vault_cfg,
        on_create=noop,
        on_modify=noop,
        on_delete=noop,
        on_move=noop,
    )
    try:
        # Handler should share the same MoveGuard instance
        assert watcher._handler._move_guard is watcher._move_guard
    finally:
        watcher.stop()
