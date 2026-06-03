"""
vault/watcher.py

Dispatches vault filesystem events to pipeline callbacks with debounce.

No pipeline/, llm/, or core.config imports at module scope — watcher.py
must remain importable without a configured vault root.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Callable

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

from core.audit import write as audit_write
from core.confidence import AIDecision
from core.result import Failure, Success
from storage.documents import delete_by_path, rename as rename_doc
from vault.indexer import IGNORE_DIRS
from vault.paths import _is_in_managed_attachment
from vault.reader import read_note
from vault.writer import move_note, write_note

if TYPE_CHECKING:
    from core.config import VaultConfig

_log = logging.getLogger(__name__)


def _is_binary(path: Path) -> bool:
    """Return True if path is not a markdown note."""
    return path.suffix.lower() != ".md"


def _sibling_for(binary: Path, vault_config: VaultConfig) -> Path:
    """Return the expected sibling .md path for a binary file.

    Uses `<binary.name>.md` (full filename including extension) so that two
    binaries with the same stem but different suffixes (e.g. `report.pdf` +
    `report.docx`) get distinct sibling markers and do not collide on the
    `attachment_path` pointer.

    Args:
        binary: Absolute path to the binary file.
        vault_config: VaultConfig with summaries_subdir.

    Returns:
        Path to <parent>/<summaries_subdir>/<binary.name>.md
    """
    return (
        binary.parent
        / vault_config.summaries_subdir
        / f"{binary.name}.md"
    )


class _VaultEventHandler(FileSystemEventHandler):
    """Internal event handler — filters and debounces watchdog filesystem events.

    Args:
        root:             Vault root Path.
        vault_config:     VaultConfig for per-project attachment path checks.
        on_create:        Callback for file-created (or external-drop) events.
        on_modify:        Callback for .md file modifications.
        on_delete:        Callback for file deletions.
        on_move:          Callback for internal vault renames.
        debounce_seconds: Timer delay before firing a callback.
    """

    def __init__(
        self,
        root: Path,
        vault_config: VaultConfig,
        on_create: Callable[[Path], None],
        on_modify: Callable[[Path], None],
        on_delete: Callable[[Path], None],
        on_move: Callable[[Path, Path], None],
        debounce_seconds: float,
        folder_cooldown: float = 5.0,
        on_folder_stable: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__()
        self._root = root
        self._vault_config = vault_config
        self._on_create = on_create
        self._on_modify = on_modify
        self._on_delete = on_delete
        self._on_move = on_move
        self._debounce_seconds = debounce_seconds
        self._folder_cooldown = folder_cooldown
        self._on_folder_stable = on_folder_stable
        # key: str(path) → (active timer, callable, args)
        self._timers: dict[str, tuple[threading.Timer, Callable, tuple]] = {}
        self._lock = threading.Lock()
        # Pending-folder registry: tracks directories being dropped
        self._pending_folders: dict[str, threading.Timer] = {}
        self._pending_folder_paths: set[str] = set()
        # Per-key identity token: incremented every time a folder timer is
        # (re)installed. A fired timer only proceeds if its token still matches
        # the stored one — guards against a stale timer popping a newer one
        # without cancelling it (timer-cancel race, C2).
        self._folder_tokens: dict[str, int] = {}
        self._folder_lock = threading.Lock()

    def _should_skip(self, path: Path) -> bool:
        """Return True if this path should never trigger a callback.

        Skips: managed attachment/ subtrees (non-.md only), dotfiles,
        .sync-conflict-* files, IGNORE_DIRS.
        """
        if path.suffix.lower() != ".md" and _is_in_managed_attachment(
            path, self._vault_config
        ):
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
            folder_path = Path(str(event.src_path))
            self._register_pending_folder(folder_path)
            return

        path = Path(str(event.src_path))

        # If this file is inside a pending folder, reset that folder's timer
        # instead of dispatching the normal file-create callback.
        folder_key = self._pending_folder_for(path)
        if folder_key is not None:
            self._reset_folder_timer(folder_key)
            return

        if self._should_skip(path):
            return
        self._debounce(str(path), self._on_create, (path,))

    # ── pending-folder registry helpers ───────────────────────────────────

    def _register_pending_folder(self, folder_path: Path) -> None:
        """Register a folder as pending and start its debounce timer."""
        key = str(folder_path)
        with self._folder_lock:
            existing = self._pending_folders.pop(key, None)
            if existing:
                existing.cancel()
            self._pending_folder_paths.add(key)
            token = self._folder_tokens.get(key, 0) + 1
            self._folder_tokens[key] = token
            timer = threading.Timer(
                self._folder_cooldown,
                self._fire_folder_stable,
                args=[folder_path, token],
            )
            self._pending_folders[key] = timer
            timer.start()

    def _reset_folder_timer(self, folder_key: str) -> None:
        """Reset the debounce timer for an already-pending folder."""
        with self._folder_lock:
            existing = self._pending_folders.pop(folder_key, None)
            if existing is None:
                return
            existing.cancel()
            folder_path = Path(folder_key)
            token = self._folder_tokens.get(folder_key, 0) + 1
            self._folder_tokens[folder_key] = token
            timer = threading.Timer(
                self._folder_cooldown,
                self._fire_folder_stable,
                args=[folder_path, token],
            )
            self._pending_folders[folder_key] = timer
            timer.start()

    def _pending_folder_for(self, file_path: Path) -> str | None:
        """Return the pending-folder key if file_path is inside a pending folder, else None."""
        with self._folder_lock:
            for key in self._pending_folder_paths:
                try:
                    file_path.relative_to(key)
                    return key
                except ValueError:
                    continue
        return None

    def _fire_folder_stable(self, folder_path: Path, token: int) -> None:
        """Called when a folder's debounce timer fires (no new files for cooldown period).

        `token` is the identity of the timer that scheduled this call. If a later
        FileCreatedEvent reset the timer (installing a new timer under the same key)
        after this timer fired but before it acquired the lock, the stored token will
        have advanced — in that case this is a stale fire and must be a no-op, leaving
        the newer timer to fire. Without this guard a stale fire would pop (and drop)
        the newer timer, then run capture_folder a second time (C2).
        """
        key = str(folder_path)
        with self._folder_lock:
            if self._folder_tokens.get(key) != token:
                # Stale fire — a newer timer owns this key. Do not touch the registry.
                return
            self._pending_folders.pop(key, None)
            self._pending_folder_paths.discard(key)
            self._folder_tokens.pop(key, None)

        if self._on_folder_stable:
            self._on_folder_stable(folder_path)

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

        # Binary sync always fires for internal binary deletes — even when path
        # is inside a managed attachment/ dir. _should_skip would otherwise hide
        # the headline Brief #3 scenario (user deletes Projects/A/attachment/X.pdf).
        # Mirrors on_moved ordering (see lines below). TD-030 fix.
        if _is_binary(path) and self._is_internal(path):
            self._debounce(
                f"bin:{path}", self._handle_binary_delete, (path,)
            )

        if self._should_skip(path):
            return
        self._debounce(str(path), self._on_delete, (path,))

    def on_moved(self, event: DirMovedEvent | FileMovedEvent) -> None:
        if event.is_directory:
            return
        src = Path(str(event.src_path))
        dst = Path(str(event.dest_path))

        # Binary sync always fires for internal binary moves — even when dst
        # is inside a managed attachment/ dir (we need to orphan the old sibling).
        if _is_binary(src) and self._is_internal(src):
            self._debounce(
                f"bin:{dst}", self._handle_binary_move, (src, dst)
            )

        if self._should_skip(dst):
            return
        if self._is_internal(src):
            self._debounce(str(dst), self._on_move, (src, dst))
        else:
            # External drop via OS move (src outside vault root)
            self._debounce(str(dst), self._on_create, (dst,))

    # ── binary sync helpers ────────────────────────────────────────────────

    def _handle_binary_delete(self, path: Path) -> None:
        """Remove sibling DB row + audit when a binary file is deleted."""
        sibling = _sibling_for(path, self._vault_config)
        sibling_vp = unicodedata.normalize(
            "NFC", str(sibling.relative_to(self._root).as_posix())
        )
        match delete_by_path(sibling_vp):
            case Success(value=rowcount):
                if rowcount == 0:
                    _log.warning(
                        "watcher.binary_delete_sibling_not_found binary=%s sibling=%s",
                        path, sibling_vp,
                    )
                else:
                    _log.info(
                        "watcher.binary_delete_sibling_removed binary=%s sibling=%s",
                        path, sibling_vp,
                    )
            case Failure(error=e):
                _log.warning(
                    "watcher.binary_delete_sibling_failed binary=%s error=%s",
                    path, e,
                )
        match audit_write(
            AIDecision(
                action="watcher:binary_delete",
                confidence=1.0,
                reasoning=f"Binary deleted: {path.name}",
                source_ids=[sibling_vp],
            ),
            pipeline="watcher",
            stage="sync",
            outcome="SIBLING_ORPHANED",
        ):
            case Failure(error=e):
                _log.warning("watcher.binary_delete_audit_failed error=%s", e)
            case Success():
                pass

    def _handle_binary_move(self, src: Path, dst: Path) -> None:
        """Sync sibling when a binary file is renamed or moved."""
        old_sibling = _sibling_for(src, self._vault_config)
        new_sibling = _sibling_for(dst, self._vault_config)
        same_folder = dst.parent == src.parent

        def _vp(p: Path) -> str:
            return unicodedata.normalize(
                "NFC", str(p.relative_to(self._root).as_posix())
            )

        if same_folder and old_sibling.exists():
            # Step 1: rename sibling on disk
            match move_note(old_sibling, new_sibling, actor="ai"):
                case Failure(error=e):
                    _log.warning(
                        "watcher.binary_move_sibling_rename_failed src=%s dst=%s error=%s",
                        src, dst, e,
                    )
                    return
                case Success():
                    pass

            # Step 2: update attachment_path pointer in sibling frontmatter
            match read_note(new_sibling):
                case Success(value=note):
                    new_attachment_vp = _vp(dst)
                    note.metadata.attachment_path = new_attachment_vp
                    match write_note(new_sibling, note.content, note.metadata, actor="ai"):
                        case Failure(error=e):
                            _log.warning(
                                "watcher.binary_move_pointer_update_failed sibling=%s error=%s",
                                new_sibling, e,
                            )
                        case Success():
                            pass
                case Failure(error=e):
                    _log.warning(
                        "watcher.binary_move_read_sibling_failed sibling=%s error=%s",
                        new_sibling, e,
                    )

            # Step 3: update DB row
            old_sibling_vp = _vp(old_sibling)
            new_sibling_vp = _vp(new_sibling)
            match rename_doc(old_sibling_vp, new_sibling_vp):
                case Success(value=rowcount):
                    if rowcount == 0:
                        _log.warning(
                            "watcher.binary_move_sibling_not_in_index old=%s new=%s",
                            old_sibling_vp, new_sibling_vp,
                        )
                case Failure(error=e):
                    _log.warning(
                        "watcher.binary_move_rename_failed old=%s error=%s",
                        old_sibling_vp, e,
                    )

            # Step 4: audit
            match audit_write(
                AIDecision(
                    action="watcher:binary_rename",
                    confidence=1.0,
                    reasoning=f"Binary renamed: {src.name} → {dst.name}",
                    source_ids=[_vp(dst)],
                ),
                pipeline="watcher",
                stage="sync",
                outcome="ATTACHMENT_MOVED",
            ):
                case Failure(error=e):
                    _log.warning("watcher.binary_move_audit_failed error=%s", e)
                case Success():
                    pass
        else:
            # Different folder or sibling doesn't exist: orphan old sibling
            old_sibling_vp = _vp(old_sibling)
            match delete_by_path(old_sibling_vp):
                case Success(value=rowcount):
                    if rowcount == 0:
                        _log.warning(
                            "watcher.binary_move_orphan_not_in_index binary=%s sibling=%s",
                            src, old_sibling_vp,
                        )
                case Failure(error=e):
                    _log.warning(
                        "watcher.binary_move_orphan_failed binary=%s error=%s",
                        src, e,
                    )
            match audit_write(
                AIDecision(
                    action="watcher:binary_move",
                    confidence=1.0,
                    reasoning=f"Binary moved outside attachment: {src.name} → {dst.name}",
                    source_ids=[_vp(src)] if src.exists() else [],
                ),
                pipeline="watcher",
                stage="sync",
                outcome="SIBLING_ORPHANED",
            ):
                case Failure(error=e):
                    _log.warning("watcher.binary_move_orphan_audit_failed error=%s", e)
                case Success():
                    pass


class VaultWatcher:
    """Watch a vault root and dispatch filesystem events to pipeline callbacks.

    Args:
        root:             Vault root path to watch recursively.
        vault_config:     VaultConfig for per-project attachment path checks.
        on_create:        Called with Path when a file is created or externally moved in.
        on_modify:        Called with Path when a .md file is modified.
        on_delete:        Called with Path when a file is deleted.
        on_move:          Called with (src, dst) for internal vault renames.
        debounce_seconds: Coalesces rapid events on the same path; default 3.0s.
    """

    def __init__(
        self,
        root: Path,
        vault_config: VaultConfig,
        on_create: Callable[[Path], None],
        on_modify: Callable[[Path], None],
        on_delete: Callable[[Path], None],
        on_move: Callable[[Path, Path], None],
        debounce_seconds: float = 3.0,
        folder_cooldown_seconds: float = 5.0,
        folder_max_workers: int = 4,
        on_folder_create: Callable[[Path], None] | None = None,
    ) -> None:
        self._folder_executor = ThreadPoolExecutor(max_workers=folder_max_workers)

        def _on_folder_stable(folder_path: Path) -> None:
            if on_folder_create is not None:
                # Test override — call the test callback directly
                on_folder_create(folder_path)
            else:
                # Production: submit asyncio.run(capture_folder()) to thread pool
                from pipelines.capture import capture_folder  # lazy import

                self._folder_executor.submit(
                    lambda fp=folder_path: asyncio.run(capture_folder(fp))
                )

        self._on_folder_stable_callback = _on_folder_stable
        self._handler = _VaultEventHandler(
            root=root,
            vault_config=vault_config,
            on_create=on_create,
            on_modify=on_modify,
            on_delete=on_delete,
            on_move=on_move,
            debounce_seconds=debounce_seconds,
            folder_cooldown=folder_cooldown_seconds,
            on_folder_stable=_on_folder_stable,
        )
        self._observer = Observer()
        self._observer.schedule(self._handler, str(root), recursive=True)

    def start(self) -> None:
        """Start the observer thread."""
        self._observer.start()

    def stop(self) -> None:
        """Signal the observer thread to stop."""
        self._observer.stop()
        self._folder_executor.shutdown(wait=False)

    def join(self) -> None:
        """Wait for the observer thread to finish."""
        self._observer.join()
