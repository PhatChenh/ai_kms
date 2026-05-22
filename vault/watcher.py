"""
vault/watcher.py

Dispatches vault filesystem events to pipeline callbacks with debounce.

No pipeline/, llm/, or core.config imports at module scope — watcher.py
must remain importable without a configured vault root.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from vault.indexer import IGNORE_DIRS


class _VaultEventHandler(FileSystemEventHandler):
    """Internal event handler — filters and debounces watchdog filesystem events.

    Args:
        root:             Vault root Path.
        attachment_path:  Path to the attachment/ folder; events here are skipped.
        on_create:        Callback for file-created (or external-drop) events.
        on_modify:        Callback for .md file modifications.
        on_delete:        Callback for file deletions.
        on_move:          Callback for internal vault renames.
        debounce_seconds: Timer delay before firing a callback.
    """

    def __init__(
        self,
        root: Path,
        attachment_path: Path,
        on_create: Callable[[Path], None],
        on_modify: Callable[[Path], None],
        on_delete: Callable[[Path], None],
        on_move: Callable[[Path, Path], None],
        debounce_seconds: float,
    ) -> None:
        super().__init__()
        self._root = root
        self._attachment_path = attachment_path
        self._on_create = on_create
        self._on_modify = on_modify
        self._on_delete = on_delete
        self._on_move = on_move
        self._debounce_seconds = debounce_seconds
        # key: str(path) → (active timer, callable, args)
        self._timers: dict[str, tuple[threading.Timer, Callable, tuple]] = {}
        self._lock = threading.Lock()

    def _should_skip(self, path: Path) -> bool:
        """Return True if this path should never trigger a callback.

        Skips: attachment/ subtree, dotfiles, .sync-conflict-* files, IGNORE_DIRS.
        """
        if self._attachment_path in path.parents:
            return True
        if path.name.startswith("."):
            return True
        if ".sync-conflict-" in path.name:
            return True
        for part in path.parts:
            if part in IGNORE_DIRS:
                return True
        return False

    def _is_internal(self, path: Path) -> bool:
        """Return True if path is inside vault root."""
        try:
            path.relative_to(self._root)
            return True
        except ValueError:
            return False

    def _debounce(self, key: str, fn: Callable, args: tuple) -> None:
        """Cancel any running timer for key, then start a new one."""
        with self._lock:
            existing = self._timers.get(key)
            if existing:
                existing[0].cancel()
            timer = threading.Timer(self._debounce_seconds, fn, args)
            self._timers[key] = (timer, fn, args)
            timer.start()

    def on_created(self, event: DirCreatedEvent | FileCreatedEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if self._should_skip(path):
            return
        self._debounce(str(path), self._on_create, (path,))

    def on_modified(self, event: DirModifiedEvent | FileModifiedEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if self._should_skip(path):
            return
        if path.suffix.lower() != ".md":
            # Binary modify deferred — TD-C6 (requires reverse attachment lookup)
            return
        self._debounce(str(path), self._on_modify, (path,))

    def on_deleted(self, event: DirDeletedEvent | FileDeletedEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if self._should_skip(path):
            return
        self._debounce(str(path), self._on_delete, (path,))

    def on_moved(self, event: DirMovedEvent | FileMovedEvent) -> None:
        if event.is_directory:
            return
        src = Path(str(event.src_path))
        dst = Path(str(event.dest_path))
        if self._should_skip(dst):
            return
        if self._is_internal(src):
            self._debounce(str(dst), self._on_move, (src, dst))
        else:
            # External drop via OS move (src outside vault root)
            self._debounce(str(dst), self._on_create, (dst,))


class VaultWatcher:
    """Watch a vault root and dispatch filesystem events to pipeline callbacks.

    Args:
        root:             Vault root path to watch recursively.
        attachment_path:  Path to attachment/ folder; events here are skipped.
        on_create:        Called with Path when a file is created or externally moved in.
        on_modify:        Called with Path when a .md file is modified.
        on_delete:        Called with Path when a file is deleted.
        on_move:          Called with (src, dst) for internal vault renames.
        debounce_seconds: Coalesces rapid events on the same path; default 3.0s.
    """

    def __init__(
        self,
        root: Path,
        attachment_path: Path,
        on_create: Callable[[Path], None],
        on_modify: Callable[[Path], None],
        on_delete: Callable[[Path], None],
        on_move: Callable[[Path, Path], None],
        debounce_seconds: float = 3.0,
    ) -> None:
        self._handler = _VaultEventHandler(
            root=root,
            attachment_path=attachment_path,
            on_create=on_create,
            on_modify=on_modify,
            on_delete=on_delete,
            on_move=on_move,
            debounce_seconds=debounce_seconds,
        )
        self._observer = Observer()
        self._observer.schedule(self._handler, str(root), recursive=True)

    def start(self) -> None:
        """Start the observer thread."""
        self._observer.start()

    def stop(self) -> None:
        """Signal the observer thread to stop."""
        self._observer.stop()

    def join(self) -> None:
        """Wait for the observer thread to finish."""
        self._observer.join()
