"""
daemon/watcher.py

Simplified vault watcher for the sync daemon — pure filesystem events with
ignore patterns and debounce.  No vault-specific logic (binary sync, sibling
management, move_guard, frontmatter, indexer).

Design heritage: adapted from vault/watcher.py, keeping the battle-tested
watchdog + debounce + NFC patterns.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
import unicodedata
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from daemon.config import DaemonConfig

_log = logging.getLogger(__name__)

# ── helpers ──────────────────────────────────────────────────────────────────


def _is_glob_pattern(pattern: str) -> bool:
    """Return True if *pattern* contains shell-style glob metacharacters."""
    return bool(set(pattern) & {"*", "?", "[", "]"})


def should_skip_path(
    path: Path,
    ignore_patterns: list[str],
    *,
    root: Path | None = None,
) -> bool:
    """Return True if *path* should be ignored based on *ignore_patterns*.

    Rules (order-independent):
      - Non-glob patterns (e.g. ``.git``, ``.DS_Store``) are matched against
        **every path component** (directories and filename).  This catches
        dotfolder subtrees like ``.git/refs/heads/main``.
      - Glob patterns (e.g. ``~$*``, ``*.tmp``) are matched against the
        **filename only** via :func:`fnmatch.fnmatch`.

    If *root* is provided, only path components **after** *root* are checked
    (the root prefix itself is never skipped).  When *root* is ``None`` the
    entire path is checked — useful for the scanner which already works with
    vault-relative paths.

    This is a standalone helper so both the watcher and the scanner (Phase 7)
    can reuse the same matching logic.
    """
    # Compute the parts to inspect.  When root is given we strip the root prefix
    # so that the vault root folder name itself isn't accidentally skipped.
    if root is not None:
        try:
            rel = path.relative_to(root)
            parts: tuple[str, ...] = rel.parts
        except ValueError:
            # Path is outside root — still inspect all parts; root-strip is a
            # best-effort optimisation for the common case.
            parts = path.parts
    else:
        parts = path.parts

    for pattern in ignore_patterns:
        if _is_glob_pattern(pattern):
            # Glob patterns match the filename only
            if fnmatch.fnmatch(parts[-1] if parts else "", pattern):
                return True
        else:
            # Exact patterns match any path component
            if pattern in parts:
                return True
    return False


# ── event handler ────────────────────────────────────────────────────────────


class _DaemonEventHandler(FileSystemEventHandler):
    """Internal watchdog handler — filters, normalises, and debounces events.

    Args:
        root:             Vault root Path (absolute).
        ignore_patterns:  Patterns from ``DaemonConfig.ignore_patterns``.
        debounce_seconds: Coalesce window for rapid events on the same path.
        on_create:        ``(vault_relative_path: str) -> None``
        on_modify:        ``(vault_relative_path: str) -> None``
        on_move:          ``(old_path: str, new_path: str) -> None``
        on_delete:        ``(vault_relative_path: str) -> None``
    """

    def __init__(
        self,
        root: Path,
        ignore_patterns: list[str],
        debounce_seconds: float,
        on_create: Callable[[str], None],
        on_modify: Callable[[str], None],
        on_move: Callable[[str, str], None],
        on_delete: Callable[[str], None],
    ) -> None:
        super().__init__()
        # Resolve to the real path so that macOS /var vs /private/var symlinks
        # don't cause relative_to() failures.
        self._root = root.resolve()
        self._ignore_patterns = ignore_patterns
        self._debounce_seconds = debounce_seconds
        self._on_create = on_create
        self._on_modify = on_modify
        self._on_move = on_move
        self._on_delete = on_delete

        # Debounce state: key = vault-relative path → (Timer, callable, args)
        self._timers: dict[str, tuple[threading.Timer, Callable, tuple]] = {}
        self._lock = threading.Lock()

    # ── helpers ──────────────────────────────────────────────────────────

    def _to_vault_path(self, abs_path: Path) -> str:
        """Return the NFC-normalised POSIX path relative to ``_root``."""
        # Resolve symlinks so that relative_to works even when the OS reports
        # paths via a different symlink chain than the one used to configure
        # the root (e.g. /var vs /private/var on macOS).
        resolved = abs_path.resolve()
        return unicodedata.normalize(
            "NFC",
            str(resolved.relative_to(self._root).as_posix()),
        )

    def _should_skip(self, path: Path) -> bool:
        """Return True if this path should never trigger a callback."""
        return should_skip_path(path, self._ignore_patterns, root=self._root)

    def _debounce(self, key: str, fn: Callable, args: tuple) -> None:
        """Cancel any running timer for *key*, then start a new one.

        The new timer will remove itself from ``_timers`` after firing to
        prevent unbounded memory growth over long-running daemon sessions.
        """
        def _wrapped() -> None:
            fn(*args)
            with self._lock:
                self._timers.pop(key, None)

        with self._lock:
            existing = self._timers.get(key)
            if existing is not None:
                existing[0].cancel()
            timer = threading.Timer(self._debounce_seconds, _wrapped)
            self._timers[key] = (timer, fn, args)
            timer.start()

    # ── lifecycle ────────────────────────────────────────────────────────

    def _shutdown(self) -> None:
        """Cancel all pending timers and clear debounce state.

        Must be called before the observer is stopped to prevent use-after-free
        on the callback references stored in timer threads.
        """
        with self._lock:
            for timer, _fn, _args in self._timers.values():
                timer.cancel()
            self._timers.clear()

    # ── event dispatch ───────────────────────────────────────────────────

    def on_created(self, event) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if self._should_skip(path):
            return
        vp = self._to_vault_path(path)
        self._debounce(f"create:{vp}", self._on_create, (vp,))

    def on_modified(self, event) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if self._should_skip(path):
            return
        vp = self._to_vault_path(path)
        self._debounce(f"modify:{vp}", self._on_modify, (vp,))

    def on_moved(self, event) -> None:
        if event.is_directory:
            return
        src_path = Path(str(event.src_path))
        dst_path = Path(str(event.dest_path))
        if self._should_skip(src_path) or self._should_skip(dst_path):
            return
        old_vp = self._to_vault_path(src_path)
        new_vp = self._to_vault_path(dst_path)
        # Moves are not debounced — they are discrete events
        self._on_move(old_vp, new_vp)

    def on_deleted(self, event) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if self._should_skip(path):
            return
        vp = self._to_vault_path(path)
        # Deletes are not debounced — they are discrete events
        self._on_delete(vp)


# ── public watcher ───────────────────────────────────────────────────────────


class DaemonWatcher:
    """Watch a vault root and dispatch filesystem events to callbacks.

    Args:
        config:      ``DaemonConfig`` with vault_root, ignore_patterns, debounce_seconds.
        on_create:   ``(vault_relative_path: str) -> None``
        on_modify:   ``(vault_relative_path: str) -> None``
        on_move:     ``(old_path: str, new_path: str) -> None``
        on_delete:   ``(vault_relative_path: str) -> None``
    """

    def __init__(
        self,
        config: DaemonConfig,
        on_create: Callable[[str], None],
        on_modify: Callable[[str], None],
        on_move: Callable[[str, str], None],
        on_delete: Callable[[str], None],
    ) -> None:
        self._handler = _DaemonEventHandler(
            root=config.vault_root,
            ignore_patterns=config.ignore_patterns,
            debounce_seconds=config.debounce_seconds,
            on_create=on_create,
            on_modify=on_modify,
            on_move=on_move,
            on_delete=on_delete,
        )
        self._observer = Observer()
        self._observer.schedule(
            self._handler,
            str(config.vault_root),
            recursive=True,
        )

    def start(self) -> None:
        """Start the watchdog observer thread."""
        self._observer.start()

    def stop(self) -> None:
        """Signal the observer to stop, cancelling all pending debounce timers."""
        self._handler._shutdown()
        self._observer.stop()

    def join(self) -> None:
        """Wait for the observer thread to finish.

        Safe to call even if the observer was never started.
        """
        if self._observer.is_alive():
            self._observer.join()
