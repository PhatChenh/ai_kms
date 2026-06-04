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
from core.logging_setup import new_correlation_id
from core.result import Failure, Success
from storage.documents import delete_by_path, get_by_path, rename as rename_doc
from vault.indexer import IGNORE_DIRS
from vault.paths import (
    _is_ai_output,
    _is_in_managed_attachment,
    _is_misplaced,
    _location_context,
    resolve_placement,
)
from vault.reader import read_note
from vault.move_guard import MoveGuard
from vault.writer import move_attachment, move_note, write_note

if TYPE_CHECKING:
    from core.config import VaultConfig

_log = logging.getLogger(__name__)


def _is_binary(path: Path) -> bool:
    """Return True if path is not a markdown note."""
    return path.suffix.lower() != ".md"


# Lock-file prefixes from Office (~$...) and LibreOffice (.~lock...).
# macOS resource forks (._...) are also skipped — they are metadata, not content.
_LOCK_PREFIXES: tuple[str, ...] = ("~$", ".~lock", "._")
_LOCK_SUFFIXES: tuple[str, ...] = (".lock",)


def _is_lock_file(path: Path) -> bool:
    """Return True if *path* is a temporary lock file from an editor or office app."""
    name = path.name
    if name.startswith(_LOCK_PREFIXES):
        return True
    if name.endswith(_LOCK_SUFFIXES):
        return True
    return False


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
        move_guard: MoveGuard | None = None,
        binary_settle_seconds: float = 5.0,
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
        self._move_guard = move_guard
        self._binary_settle_seconds = binary_settle_seconds
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
        # Binary-move settle registry: coalesces rapid multi-hop moves so only
        # the final src→dst pair triggers a re-home (T7 settle window).
        # Key: stable binary identity (str of the *first* src path seen in the chain).
        # Value: (current_src, current_dst, timer).
        self._pending_binary_moves: dict[str, tuple[Path, Path, threading.Timer]] = {}
        # Per-key identity token — guards against stale-timer-pop race (same as folder tokens).
        self._binary_move_tokens: dict[str, int] = {}
        self._binary_move_lock = threading.Lock()

    def _should_skip(self, path: Path) -> bool:
        """Return True if this path should never trigger a callback.

        Skips: managed attachment/ subtrees (non-.md only), dotfiles,
        .sync-conflict-* files, IGNORE_DIRS.
        """
        if path.suffix.lower() != ".md" and _is_in_managed_attachment(
            path, self._vault_config
        ):
            return True
        if _is_ai_output(path, self._vault_config):
            _log.debug("watcher.skip.ai_output path=%s", path.name)
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
        if path.suffix.lower() == ".md" and _is_misplaced(
            path, self._vault_config
        ):
            self._debounce(
                f"misplaced:{path}", self._handle_misplaced_md, (path,)
            )
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

    # ── binary-move settle window helpers (T7) ─────────────────────────────

    def _register_binary_move(self, src: Path, dst: Path) -> None:
        """Register src→dst as a pending cross-folder move, starting/resetting its timer.

        Uses the FIRST src path in the chain as the stable identity key so
        subsequent hops (where src == previous dst) update the same entry.
        When coalescing, preserves the original first_src and only updates dst.
        """
        # Try to find an existing entry where the current_dst matches this src
        # (i.e., this is a second hop of the same binary).
        settle_key: str | None = None
        first_src: Path = src
        with self._binary_move_lock:
            for key, (cur_src, cur_dst, _timer) in self._pending_binary_moves.items():
                if cur_dst == src:
                    settle_key = key
                    first_src = cur_src  # Preserve the first src in the chain.
                    break
        if settle_key is None:
            settle_key = unicodedata.normalize("NFC", str(src))
        self._reset_binary_move_timer(settle_key, first_src, dst)

    def _reset_binary_move_timer(self, settle_key: str, src: Path, dst: Path) -> None:
        """Cancel the existing timer for *settle_key*, then install a new one."""
        with self._binary_move_lock:
            existing = self._pending_binary_moves.pop(settle_key, None)
            if existing is not None:
                existing[2].cancel()
            token = self._binary_move_tokens.get(settle_key, 0) + 1
            self._binary_move_tokens[settle_key] = token
            timer = threading.Timer(
                self._binary_settle_seconds,
                self._fire_binary_move_settled,
                args=[settle_key, src, dst, token],
            )
            self._pending_binary_moves[settle_key] = (src, dst, timer)
            timer.start()

    def _fire_binary_move_settled(
        self, settle_key: str, src: Path, dst: Path, token: int
    ) -> None:
        """Called when the settle timer fires — execute the actual re-home.

        *token* guards against stale timers popping newer entries (same pattern
        as _fire_folder_stable's C2 fix).
        """
        with self._binary_move_lock:
            if self._binary_move_tokens.get(settle_key) != token:
                return  # Stale fire — a newer timer owns this key.
            self._pending_binary_moves.pop(settle_key, None)
            self._binary_move_tokens.pop(settle_key, None)
        # Execute the actual re-home with the final (src, dst) pair.
        self._handle_binary_move(src, dst, _settled=True)

    def on_modified(self, event: DirModifiedEvent | FileModifiedEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))

        # Binary modify detection fires for non-.md files inside the vault
        # that are NOT AI-output (Briefings, Synthesis, Documentation).
        # This covers both attachment/ binaries AND editable files at project
        # root (e.g. Projects/Alpha/budget.xlsx).
        # Lock files (~$... , .~lock... , ._... , *.lock) are filtered first —
        # they are editor/Office artifacts, not real content changes.
        # Mirrors on_deleted / on_moved ordering (TD-030 fix).
        if path.suffix.lower() != ".md":
            if _is_lock_file(path):
                return
            if self._is_internal(path) and not _is_ai_output(
                path, self._vault_config
            ):
                self._debounce(
                    f"binmod:{path}", self._handle_binary_modify, (path,)
                )
            return

        if self._should_skip(path):
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

    # ── misplaced-md sweep helper ────────────────────────────────────────

    def _handle_misplaced_md(self, path: Path) -> None:
        """Move a misplaced .md from bare Projects/Domain root to inbox."""
        import structlog

        structlog.contextvars.bind_contextvars(correlation_id=new_correlation_id())

        if not path.exists():
            return

        inbox_dst = self._vault_config.inbox_path / path.name
        # Collision handling
        counter = 0
        stem = inbox_dst.stem
        suffix = inbox_dst.suffix
        while inbox_dst.exists() and counter < 99:
            counter += 1
            inbox_dst = self._vault_config.inbox_path / f"{stem}-{counter}{suffix}"
        if inbox_dst.exists():
            _log.warning(
                "watcher.misplaced_md_collision_exhausted path=%s", path
            )
            return

        match move_note(path, inbox_dst, actor="ai"):
            case Failure(error=e):
                _log.warning(
                    "watcher.misplaced_md_move_failed path=%s error=%s",
                    path,
                    e,
                )
                return
            case Success():
                pass

        match audit_write(
            AIDecision(
                action="watcher:misplaced_sweep",
                confidence=1.0,
                reasoning=f"Misplaced md swept to inbox: {path.name}",
                source_ids=[
                    unicodedata.normalize(
                        "NFC",
                        str(path.relative_to(self._root).as_posix()),
                    )
                ],
            ),
            pipeline="watcher",
            stage="sync",
            outcome="MISPLACED",
        ):
            case Failure():
                pass
            case Success():
                pass

        self._on_create(inbox_dst)

    # ── binary sync helpers ────────────────────────────────────────────────

    def _handle_binary_delete(self, path: Path) -> None:
        """Remove sibling DB row + audit when a binary file is deleted."""
        import structlog
        structlog.contextvars.bind_contextvars(correlation_id=new_correlation_id())
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

    def _handle_binary_move(self, src: Path, dst: Path, *, _settled: bool = False) -> None:
        """Sync sibling when a binary file is renamed or moved."""
        import structlog
        structlog.contextvars.bind_contextvars(correlation_id=new_correlation_id())
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
            # ── cross-folder re-home ────────────────────────────────────────
            # Sub-step a — MoveGuard check
            if self._move_guard is not None and self._move_guard.check_and_consume(dst):
                _log.info("watcher.rehome_skip path=%s reason=pipeline_initiated", dst)
                return

            # Sub-step a2 — Settle window (T7): coalesce multi-hop moves.
            # Register src→dst and start/reset a settle timer.  When the
            # timer fires, _fire_binary_move_settled calls _handle_binary_move
            # again with _settled=True and the final accumulated pair.  This
            # prevents N separate re-home operations for an N-hop drag.
            if not _settled:
                self._register_binary_move(src, dst)
                return

            # Sub-step b — determine new location
            loc_type, loc_name = _location_context(dst, self._vault_config)
            if loc_type is None:
                # Unknown location: fall back to orphan path
                old_sibling_vp = _vp(old_sibling)
                match delete_by_path(old_sibling_vp):
                    case Success(value=rowcount):
                        if rowcount == 0:
                            _log.warning(
                                "watcher.binary_move_rehome_unknown_location orphan_not_found binary=%s sibling=%s",
                                src, old_sibling_vp,
                            )
                    case Failure(error=e):
                        _log.warning(
                            "watcher.binary_move_rehome_unknown_location_orphan_failed binary=%s error=%s",
                            src, e,
                        )
                match audit_write(
                    AIDecision(
                        action="watcher:binary_move",
                        confidence=1.0,
                        reasoning=(
                            f"Binary moved to unknown location: {src.name} → {dst.name}"
                        ),
                        source_ids=[_vp(src)] if src.exists() else [],
                    ),
                    pipeline="watcher",
                    stage="sync",
                    outcome="SIBLING_ORPHANED",
                ):
                    case Failure(error=e):
                        _log.warning(
                            "watcher.binary_move_rehome_unknown_location_audit_failed error=%s",
                            e,
                        )
                    case Success():
                        pass
                return

            # Sub-step c — pre-check updated_by_human on old sibling
            old_sibling_vp = _vp(old_sibling)
            match get_by_path(old_sibling_vp):
                case Failure(error=e):
                    _log.warning(
                        "watcher.binary_rehome_get_row_failed sibling=%s error=%s",
                        old_sibling_vp, e,
                    )
                    # Fall back to orphan path
                    match delete_by_path(old_sibling_vp):
                        case Success():
                            pass
                        case Failure():
                            pass
                    match audit_write(
                        AIDecision(
                            action="watcher:binary_move",
                            confidence=1.0,
                            reasoning=(
                                f"Binary moved, DB lookup failed: {src.name} → {dst.name}"
                            ),
                            source_ids=[_vp(src)] if src.exists() else [],
                        ),
                        pipeline="watcher",
                        stage="sync",
                        outcome="SIBLING_ORPHANED",
                    ):
                        case Failure():
                            pass
                        case Success():
                            pass
                    return
                case Success(value=None):
                    _log.info(
                        "watcher.binary_rehome_row_not_found sibling=%s",
                        old_sibling_vp,
                    )
                    # Fall back to orphan path
                    match delete_by_path(old_sibling_vp):
                        case Success():
                            pass
                        case Failure():
                            pass
                    match audit_write(
                        AIDecision(
                            action="watcher:binary_move",
                            confidence=1.0,
                            reasoning=(
                                f"Binary moved, no DB row: {src.name} → {dst.name}"
                            ),
                            source_ids=[_vp(src)] if src.exists() else [],
                        ),
                        pipeline="watcher",
                        stage="sync",
                        outcome="SIBLING_ORPHANED",
                    ):
                        case Failure():
                            pass
                        case Success():
                            pass
                    return
                case Success(value=row):
                    if row.updated_by_human:
                        _log.info(
                            "watcher.binary_rehome_human_lock binary=%s sibling=%s",
                            src, old_sibling_vp,
                        )
                        return

            # Sub-step d — compute placement
            placement = resolve_placement(dst, loc_type, loc_name, self._vault_config)
            final_binary = placement.final_dir / dst.name
            new_sibling_path = placement.sibling_dir / f"{dst.name}.md"
            new_sibling_vp = _vp(new_sibling_path)

            # Sub-step e — move binary if needed
            if placement.needs_move and final_binary != dst:
                match move_attachment(dst, final_binary):
                    case Failure(error=e):
                        _log.warning(
                            "watcher.binary_rehome_move_failed src=%s dst=%s error=%s",
                            dst, final_binary, e,
                        )
                        return
                    case Success():
                        pass

            # Sub-step f — write new sibling card
            if old_sibling.exists():
                match move_note(old_sibling, new_sibling_path, actor="ai"):
                    case Failure(error=e):
                        _log.warning(
                            "watcher.binary_rehome_move_note_failed src=%s dst=%s error=%s",
                            old_sibling, new_sibling_path, e,
                        )
                        return
                    case Success():
                        pass
                match read_note(new_sibling_path):
                    case Success(value=note):
                        note.metadata.attachment_path = _vp(final_binary)
                        match write_note(
                            new_sibling_path, note.content, note.metadata, actor="ai"
                        ):
                            case Failure(error=e):
                                _log.warning(
                                    "watcher.binary_rehome_pointer_update_failed sibling=%s error=%s",
                                    new_sibling_path, e,
                                )
                            case Success():
                                pass
                    case Failure(error=e):
                        _log.warning(
                            "watcher.binary_rehome_read_sibling_failed sibling=%s error=%s",
                            new_sibling_path, e,
                        )
            else:
                # Sibling absent on disk: rebuild from DB row
                from vault.frontmatter import NoteMetadata
                rebuilt_meta = NoteMetadata(
                    type="attachment-summary",
                    confidence=row.confidence,
                    attachment_path=_vp(final_binary),
                    updated_by_human=False,
                    summary=row.summary,
                    extra={
                        "title": row.title,
                        "note_type": row.note_type or "attachment-summary",
                        "content_hash": row.content_hash,
                    },
                )
                match write_note(
                    new_sibling_path,
                    row.summary or "",
                    rebuilt_meta,
                    actor="ai",
                ):
                    case Failure(error=e):
                        _log.warning(
                            "watcher.binary_rehome_rebuild_failed sibling=%s error=%s",
                            new_sibling_path, e,
                        )
                        return
                    case Success():
                        pass

            # Sub-step g — update the database
            match rename_doc(old_sibling_vp, new_sibling_vp):
                case Success(value=0):
                    _log.warning(
                        "watcher.binary_rehome_db_row_not_found old=%s new=%s",
                        old_sibling_vp, new_sibling_vp,
                    )
                case Failure(error=e):
                    _log.warning(
                        "watcher.binary_rehome_rename_failed old=%s error=%s",
                        old_sibling_vp, e,
                    )
                case Success():
                    pass

            # Sub-step h — write audit row
            is_no_edit = dst.suffix.lower() in self._vault_config.no_edit_extensions
            direction = "no-edit→attachment" if is_no_edit else "editable→root"
            match audit_write(
                AIDecision(
                    action="watcher:binary_rehome",
                    confidence=1.0,
                    reasoning=(
                        f"Re-homed {src.name} → {_vp(final_binary)} ({direction})"
                    ),
                    source_ids=[new_sibling_vp],
                ),
                pipeline="watcher",
                stage="sync",
                outcome="REHOMED",
            ):
                case Failure(error=e):
                    _log.warning("watcher.binary_rehome_audit_failed error=%s", e)
                case Success():
                    pass

    # ── binary modify detection (T9) ──────────────────────────────────────────

    def _handle_binary_modify(self, path: Path) -> None:
        """Called after debounce: compute SHA-256, compare with sibling source_hash.

        If the hash differs from the stored value (or no source_hash exists),
        update the sibling frontmatter and write a BINARY_MODIFIED audit row.
        If the hash matches, silently return — the modify event was noise
        (e.g. file-open timestamp update, not a real content change).

        Does NOT re-summarize the sibling body — content-change detection is
        separate from re-capture (future phase).
        """
        import hashlib
        import structlog
        structlog.contextvars.bind_contextvars(correlation_id=new_correlation_id())

        # 1. Compute current hash
        try:
            current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, IOError):
            _log.warning("watcher.binary_modify_read_failed path=%s", path)
            return

        # 2. Find sibling
        sibling = _sibling_for(path, self._vault_config)
        if not sibling.exists():
            _log.info("watcher.binary_modify_no_sibling path=%s", path)
            return

        # 3. Compare against stored hash
        match read_note(sibling):
            case Success(value=note):
                stored_hash = note.metadata.source_hash
                if stored_hash and stored_hash == current_hash:
                    return  # No content change — ignore modify noise
                # Update source_hash in sibling frontmatter
                note.metadata.source_hash = current_hash
                match write_note(sibling, note.content, note.metadata, actor="ai"):
                    case Failure(error=e):
                        _log.warning(
                            "watcher.binary_modify_hash_update_failed path=%s error=%s",
                            path, e,
                        )
                        return
                    case Success():
                        pass
                # 4. Write audit row
                sibling_vp = unicodedata.normalize(
                    "NFC", str(sibling.relative_to(self._root).as_posix())
                )
                match audit_write(
                    AIDecision(
                        action="watcher:binary_modified",
                        confidence=1.0,
                        reasoning=(
                            f"Binary content changed: {path.name} "
                            f"(hash: {current_hash[:12]}...)"
                        ),
                        source_ids=[sibling_vp],
                    ),
                    pipeline="watcher",
                    stage="sync",
                    outcome="BINARY_MODIFIED",
                ):
                    case Failure(error=e):
                        _log.warning(
                            "watcher.binary_modify_audit_failed error=%s", e
                        )
                    case Success():
                        pass
            case Failure(error=e):
                _log.warning(
                    "watcher.binary_modify_read_sibling_failed path=%s error=%s",
                    sibling, e,
                )


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
        binary_settle_seconds: float = 5.0,
        folder_max_workers: int = 4,
        on_folder_create: Callable[[Path], None] | None = None,
        move_guard: MoveGuard | None = None,
    ) -> None:
        self._move_guard = move_guard if move_guard is not None else MoveGuard()
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
            move_guard=self._move_guard,
            binary_settle_seconds=binary_settle_seconds,
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
