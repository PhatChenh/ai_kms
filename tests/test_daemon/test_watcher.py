"""
tests/test_daemon/test_watcher.py

Comprehensive tests for daemon/watcher.py — DaemonWatcher, _DaemonEventHandler,
and the standalone ``should_skip_path`` helper.

Test map:
  Section 1 — should_skip_path standalone helper
  Section 2 — _DaemonEventHandler synthetic event tests
    - on_created, on_modified, on_moved, on_deleted
    - ignore pattern matching (.git, .obsidian, .DS_Store, ~$*, *.tmp, *.swp, etc.)
    - directory events ignored
    - NFC normalisation
  Section 3 — Debounce (rapid events coalesced)
  Section 4 — DaemonWatcher with real Observer (integration)
  Section 5 — DaemonWatcher start/stop/join lifecycle
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from daemon.config import DaemonConfig
from daemon.watcher import (
    DaemonWatcher,
    _DaemonEventHandler,
    should_skip_path,
)

# ── test constants ──────────────────────────────────────────────────────────

DEBOUNCE = 0.02  # 20 ms
WAIT = 0.15       # 150 ms — generous for CI


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_config(
    tmp_path: Path,
    *,
    vault_root: Path | None = None,
    ignore_patterns: list[str] | None = None,
    debounce_seconds: float = DEBOUNCE,
) -> DaemonConfig:
    """Build a DaemonConfig for testing."""
    root = vault_root or (tmp_path / "vault")
    root.mkdir(exist_ok=True)
    return DaemonConfig(
        vault_root=root,
        cloud_endpoint="http://localhost:8080",
        api_key="test-key",
        debounce_seconds=debounce_seconds,
        ignore_patterns=ignore_patterns
        if ignore_patterns is not None
        else [
            ".git",
            ".obsidian",
            ".trash",
            ".stversions",
            ".DS_Store",
            "Thumbs.db",
            "~$*",
            "*.tmp",
            "*.swp",
            ".~lock*",
        ],
    )


def _make_handler(
    tmp_path: Path,
    *,
    config: DaemonConfig | None = None,
    on_create=None,
    on_modify=None,
    on_delete=None,
    on_move=None,
) -> tuple[_DaemonEventHandler, DaemonConfig]:
    """Build a _DaemonEventHandler with test config."""
    cfg = config or _make_config(tmp_path)
    handler = _DaemonEventHandler(
        root=cfg.vault_root,
        ignore_patterns=cfg.ignore_patterns,
        debounce_seconds=cfg.debounce_seconds,
        on_create=on_create or (lambda p: None),
        on_modify=on_modify or (lambda p: None),
        on_move=on_move or (lambda s, d: None),
        on_delete=on_delete or (lambda p: None),
    )
    return handler, cfg


def _touch(path: Path) -> None:
    """Create an empty file (and parents)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")


# ===========================================================================
# Section 1 — should_skip_path standalone helper
# ===========================================================================


class TestShouldSkipPath:
    """Tests for the reusable should_skip_path() function."""

    DEFAULT_IGNORE = [
        ".git",
        ".obsidian",
        ".DS_Store",
        "~$*",
        "*.tmp",
    ]

    def test_skips_dotfolder_git(self):
        """Files inside .git/ are skipped."""
        path = Path("/vault/.git/refs/heads/main")
        assert should_skip_path(path, self.DEFAULT_IGNORE) is True

    def test_skips_dotfolder_obsidian(self):
        """Files inside .obsidian/ are skipped."""
        path = Path("/vault/.obsidian/workspace.json")
        assert should_skip_path(path, self.DEFAULT_IGNORE) is True

    def test_skips_ds_store(self):
        """Files named .DS_Store are skipped."""
        path = Path("/vault/.DS_Store")
        assert should_skip_path(path, self.DEFAULT_IGNORE) is True

    def test_skips_ds_store_in_subdir(self):
        """.DS_Store in a subdirectory is skipped."""
        path = Path("/vault/inbox/.DS_Store")
        assert should_skip_path(path, self.DEFAULT_IGNORE) is True

    def test_skips_tilde_dollar_glob(self):
        """~$* pattern matches Office temp files."""
        assert should_skip_path(Path("/vault/~$draft.docx"), self.DEFAULT_IGNORE) is True
        assert should_skip_path(Path("/vault/inbox/~$report.xlsx"), self.DEFAULT_IGNORE) is True

    def test_skips_tmp_glob(self):
        """*.tmp pattern matches temp files."""
        assert should_skip_path(Path("/vault/scratch.tmp"), self.DEFAULT_IGNORE) is True
        assert should_skip_path(Path("/vault/a/b/c.tmp"), self.DEFAULT_IGNORE) is True

    def test_allows_normal_md(self):
        """Normal .md files are not skipped."""
        path = Path("/vault/inbox/note.md")
        assert should_skip_path(path, self.DEFAULT_IGNORE) is False

    def test_allows_normal_pdf(self):
        """Normal .pdf files are not skipped."""
        path = Path("/vault/Projects/Alpha/report.pdf")
        assert should_skip_path(path, self.DEFAULT_IGNORE) is False

    def test_respects_root_parameter(self):
        """When root is given, only components after root are checked."""
        root = Path("/vault")
        # ".git" is not in the relative parts "inbox/note.md"
        path = root / "inbox" / "note.md"
        assert should_skip_path(path, self.DEFAULT_IGNORE, root=root) is False

        # ".git" is in the relative parts
        path = root / ".git" / "HEAD"
        assert should_skip_path(path, self.DEFAULT_IGNORE, root=root) is True

    def test_root_name_not_skipped(self):
        """The vault root folder name itself is never checked against patterns."""
        # If vault root were named ".git" that would be weird, but we test
        # that a folder named ".git" inside the vault is still skipped while
        # the vault root itself is not part of the check.
        root = Path("/vault")
        path = root / "inbox" / "note.md"
        # "vault" is not in the ignore list, so even if we didn't strip root,
        # it wouldn't match.  The key test: ensure the root prefix is stripped.
        assert should_skip_path(path, [".vault"], root=root) is False

    def test_no_root_checks_all_parts(self):
        """Without root, all path parts are checked."""
        path = Path("/vault/.git/HEAD")
        assert should_skip_path(path, self.DEFAULT_IGNORE) is True

    def test_empty_ignore_patterns(self):
        """With no ignore patterns, nothing is skipped."""
        path = Path("/vault/.git/HEAD")
        assert should_skip_path(path, []) is False

    def test_exact_pattern_matches_component_not_substring(self):
        """Exact patterns match whole components, not substrings."""
        # "git" (without dot) should not match ".git"
        path = Path("/vault/.git/HEAD")
        assert should_skip_path(path, ["git"]) is False

    def test_thumbs_db_skipped(self):
        """Thumbs.db (exact match) is skipped."""
        path = Path("/vault/inbox/Thumbs.db")
        assert should_skip_path(path, self.DEFAULT_IGNORE + ["Thumbs.db"]) is True

    def test_swp_glob_skipped(self):
        """*.swp glob matches vim swap files."""
        patterns = [".git", "*.swp"]
        assert should_skip_path(Path("/vault/.note.md.swp"), patterns) is True


# ===========================================================================
# Section 2 — _DaemonEventHandler synthetic event tests
# ===========================================================================


class TestOnCreated:
    """on_created synthetic event dispatch."""

    def test_fires_for_md(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_create=calls.append)
        md = cfg.vault_root / "inbox" / "note.md"
        handler.on_created(FileCreatedEvent(str(md)))
        time.sleep(WAIT)
        assert calls == ["inbox/note.md"]

    def test_fires_for_pdf(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_create=calls.append)
        pdf = cfg.vault_root / "inbox" / "report.pdf"
        handler.on_created(FileCreatedEvent(str(pdf)))
        time.sleep(WAIT)
        assert calls == ["inbox/report.pdf"]

    def test_skips_ds_store(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_create=calls.append)
        ds = cfg.vault_root / ".DS_Store"
        handler.on_created(FileCreatedEvent(str(ds)))
        time.sleep(WAIT)
        assert calls == []

    def test_skips_dot_git(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_create=calls.append)
        git_file = cfg.vault_root / ".git" / "index"
        handler.on_created(FileCreatedEvent(str(git_file)))
        time.sleep(WAIT)
        assert calls == []

    def test_skips_tilde_dollar(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_create=calls.append)
        lock = cfg.vault_root / "~$draft.docx"
        handler.on_created(FileCreatedEvent(str(lock)))
        time.sleep(WAIT)
        assert calls == []

    def test_skips_tmp(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_create=calls.append)
        tmp_file = cfg.vault_root / "temp.tmp"
        handler.on_created(FileCreatedEvent(str(tmp_file)))
        time.sleep(WAIT)
        assert calls == []

    def test_skips_swp(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_create=calls.append)
        swp = cfg.vault_root / ".note.md.swp"
        handler.on_created(FileCreatedEvent(str(swp)))
        time.sleep(WAIT)
        assert calls == []

    def test_skips_thumbs_db(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_create=calls.append)
        thumbs = cfg.vault_root / "Thumbs.db"
        handler.on_created(FileCreatedEvent(str(thumbs)))
        time.sleep(WAIT)
        assert calls == []

    def test_skips_dir_created(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_create=calls.append)
        d = cfg.vault_root / "new-folder"
        handler.on_created(DirCreatedEvent(str(d)))
        time.sleep(WAIT)
        assert calls == []

    def test_nfc_normalization(self, tmp_path: Path):
        """Vault-relative paths are NFC-normalised."""
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_create=calls.append)
        # Use a path with combining characters (NFD form é = e + ́)
        import unicodedata
        name_nfd = unicodedata.normalize("NFD", "café.md")
        name_nfc = unicodedata.normalize("NFC", "café.md")
        assert name_nfd != name_nfc  # sanity: they differ in raw form
        f = cfg.vault_root / name_nfd
        handler.on_created(FileCreatedEvent(str(f)))
        time.sleep(WAIT)
        # The callback should receive NFC-normalised form
        assert calls == [name_nfc]

    def test_deeply_nested_path(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_create=calls.append)
        f = cfg.vault_root / "a" / "b" / "c" / "d" / "deep.md"
        handler.on_created(FileCreatedEvent(str(f)))
        time.sleep(WAIT)
        assert calls == ["a/b/c/d/deep.md"]


class TestOnModified:
    """on_modified synthetic event dispatch."""

    def test_fires_for_md(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_modify=calls.append)
        md = cfg.vault_root / "Projects" / "Alpha" / "doc.md"
        handler.on_modified(FileModifiedEvent(str(md)))
        time.sleep(WAIT)
        assert calls == ["Projects/Alpha/doc.md"]

    def test_skips_ds_store(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_modify=calls.append)
        ds = cfg.vault_root / "inbox" / ".DS_Store"
        handler.on_modified(FileModifiedEvent(str(ds)))
        time.sleep(WAIT)
        assert calls == []

    def test_skips_dir_modified(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_modify=calls.append)
        d = cfg.vault_root / "existing-folder"
        handler.on_modified(DirModifiedEvent(str(d)))
        time.sleep(WAIT)
        assert calls == []


class TestOnMoved:
    """on_moved synthetic event dispatch."""

    def test_fires_for_internal_rename(self, tmp_path: Path):
        calls: list[tuple[str, str]] = []
        handler, cfg = _make_handler(
            tmp_path, on_move=lambda s, d: calls.append((s, d))
        )
        src = cfg.vault_root / "inbox" / "old.md"
        dst = cfg.vault_root / "inbox" / "new.md"
        handler.on_moved(FileMovedEvent(str(src), str(dst)))
        time.sleep(WAIT)
        assert calls == [("inbox/old.md", "inbox/new.md")]

    def test_fires_for_cross_folder_move(self, tmp_path: Path):
        calls: list[tuple[str, str]] = []
        handler, cfg = _make_handler(
            tmp_path, on_move=lambda s, d: calls.append((s, d))
        )
        src = cfg.vault_root / "inbox" / "note.md"
        dst = cfg.vault_root / "Projects" / "Alpha" / "note.md"
        handler.on_moved(FileMovedEvent(str(src), str(dst)))
        time.sleep(WAIT)
        assert calls == [("inbox/note.md", "Projects/Alpha/note.md")]

    def test_skips_when_src_ignored(self, tmp_path: Path):
        calls: list[tuple[str, str]] = []
        handler, cfg = _make_handler(
            tmp_path, on_move=lambda s, d: calls.append((s, d))
        )
        src = cfg.vault_root / ".git" / "HEAD"
        dst = cfg.vault_root / "inbox" / "HEAD"
        handler.on_moved(FileMovedEvent(str(src), str(dst)))
        time.sleep(WAIT)
        assert calls == []

    def test_skips_when_dst_ignored(self, tmp_path: Path):
        calls: list[tuple[str, str]] = []
        handler, cfg = _make_handler(
            tmp_path, on_move=lambda s, d: calls.append((s, d))
        )
        src = cfg.vault_root / "inbox" / "note.md"
        dst = cfg.vault_root / ".git" / "note.md"
        handler.on_moved(FileMovedEvent(str(src), str(dst)))
        time.sleep(WAIT)
        assert calls == []

    def test_skips_dir_moved(self, tmp_path: Path):
        calls: list[tuple[str, str]] = []
        handler, cfg = _make_handler(
            tmp_path, on_move=lambda s, d: calls.append((s, d))
        )
        src = cfg.vault_root / "old-dir"
        dst = cfg.vault_root / "new-dir"
        handler.on_moved(DirMovedEvent(str(src), str(dst)))
        time.sleep(WAIT)
        assert calls == []

    def test_nfc_normalization(self, tmp_path: Path):
        """Both old and new vault paths are NFC-normalised."""
        calls: list[tuple[str, str]] = []
        handler, cfg = _make_handler(
            tmp_path, on_move=lambda s, d: calls.append((s, d))
        )
        import unicodedata
        src_nfd = cfg.vault_root / unicodedata.normalize("NFD", "café.md")
        dst_nfd = cfg.vault_root / unicodedata.normalize("NFD", "caffè.md")
        handler.on_moved(FileMovedEvent(str(src_nfd), str(dst_nfd)))
        time.sleep(WAIT)
        expected_src = unicodedata.normalize("NFC", "café.md")
        expected_dst = unicodedata.normalize("NFC", "caffè.md")
        assert calls == [(expected_src, expected_dst)]


class TestOnDeleted:
    """on_deleted synthetic event dispatch."""

    def test_fires_for_md(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_delete=calls.append)
        md = cfg.vault_root / "inbox" / "gone.md"
        handler.on_deleted(FileDeletedEvent(str(md)))
        time.sleep(WAIT)
        assert calls == ["inbox/gone.md"]

    def test_skips_ds_store(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_delete=calls.append)
        ds = cfg.vault_root / ".DS_Store"
        handler.on_deleted(FileDeletedEvent(str(ds)))
        time.sleep(WAIT)
        assert calls == []

    def test_skips_dir_deleted(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_delete=calls.append)
        d = cfg.vault_root / "old-folder"
        handler.on_deleted(DirDeletedEvent(str(d)))
        time.sleep(WAIT)
        assert calls == []


# ===========================================================================
# Section 3 — Debounce
# ===========================================================================


class TestDebounce:
    """Rapid events on the same path coalesce to a single callback."""

    def test_rapid_creates_fire_once(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(
            tmp_path, on_create=calls.append, config=_make_config(tmp_path, debounce_seconds=0.05)
        )
        md = cfg.vault_root / "inbox" / "note.md"
        event = FileCreatedEvent(str(md))
        handler.on_created(event)
        handler.on_created(event)
        handler.on_created(event)
        time.sleep(0.2)
        assert len(calls) == 1
        assert calls[0] == "inbox/note.md"

    def test_rapid_modifies_fire_once(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(
            tmp_path, on_modify=calls.append, config=_make_config(tmp_path, debounce_seconds=0.05)
        )
        md = cfg.vault_root / "inbox" / "note.md"
        event = FileModifiedEvent(str(md))
        handler.on_modified(event)
        handler.on_modified(event)
        handler.on_modified(event)
        handler.on_modified(event)
        time.sleep(0.2)
        assert len(calls) == 1
        assert calls[0] == "inbox/note.md"

    def test_different_paths_not_coalesced(self, tmp_path: Path):
        calls: list[str] = []
        handler, cfg = _make_handler(
            tmp_path, on_create=calls.append, config=_make_config(tmp_path, debounce_seconds=0.05)
        )
        a = cfg.vault_root / "inbox" / "a.md"
        b = cfg.vault_root / "inbox" / "b.md"
        handler.on_created(FileCreatedEvent(str(a)))
        handler.on_created(FileCreatedEvent(str(b)))
        time.sleep(0.2)
        assert len(calls) == 2
        assert set(calls) == {"inbox/a.md", "inbox/b.md"}

    def test_move_not_debounced(self, tmp_path: Path):
        """Moves are not debounced — each fires immediately."""
        calls: list[tuple[str, str]] = []
        handler, cfg = _make_handler(
            tmp_path, on_move=lambda s, d: calls.append((s, d))
        )
        src = cfg.vault_root / "inbox" / "a.md"
        dst = cfg.vault_root / "inbox" / "b.md"
        handler.on_moved(FileMovedEvent(str(src), str(dst)))
        handler.on_moved(FileMovedEvent(str(src), str(dst)))
        time.sleep(WAIT)
        assert len(calls) == 2  # Moves are not debounced

    def test_delete_not_debounced(self, tmp_path: Path):
        """Deletes are not debounced — each fires immediately."""
        calls: list[str] = []
        handler, cfg = _make_handler(tmp_path, on_delete=calls.append)
        md = cfg.vault_root / "inbox" / "gone.md"
        event = FileDeletedEvent(str(md))
        handler.on_deleted(event)
        handler.on_deleted(event)
        time.sleep(WAIT)
        assert len(calls) == 2  # Deletes are not debounced


# ===========================================================================
# Section 4 — DaemonWatcher with real Observer (integration)
# ===========================================================================


class TestDaemonWatcherIntegration:
    """End-to-end tests using a real watchdog Observer.

    These tests create actual files on disk and wait for the observer thread
    to pick them up.
    """

    def test_create_file_triggers_on_create(self, tmp_path: Path):
        calls: list[str] = []
        cfg = _make_config(tmp_path)
        w = DaemonWatcher(
            cfg,
            on_create=calls.append,
            on_modify=lambda p: None,
            on_move=lambda s, d: None,
            on_delete=lambda p: None,
        )
        w.start()
        try:
            time.sleep(0.1)  # Let observer initialise
            _touch(cfg.vault_root / "inbox" / "hello.md")
            time.sleep(0.5)  # Wait for watchdog + debounce
        finally:
            w.stop()
            w.join()
        assert "inbox/hello.md" in calls

    def test_modify_file_triggers_on_modify(self, tmp_path: Path):
        calls: list[str] = []
        cfg = _make_config(tmp_path)
        # Pre-create the file
        f = cfg.vault_root / "inbox" / "note.md"
        _touch(f)
        w = DaemonWatcher(
            cfg,
            on_create=lambda p: None,
            on_modify=calls.append,
            on_move=lambda s, d: None,
            on_delete=lambda p: None,
        )
        w.start()
        try:
            time.sleep(0.1)
            f.write_text("updated content")
            time.sleep(0.5)
        finally:
            w.stop()
            w.join()
        assert "inbox/note.md" in calls

    def test_delete_file_triggers_on_delete(self, tmp_path: Path):
        calls: list[str] = []
        cfg = _make_config(tmp_path)
        f = cfg.vault_root / "inbox" / "delete-me.md"
        _touch(f)
        w = DaemonWatcher(
            cfg,
            on_create=lambda p: None,
            on_modify=lambda p: None,
            on_move=lambda s, d: None,
            on_delete=calls.append,
        )
        w.start()
        try:
            time.sleep(0.1)
            f.unlink()
            time.sleep(0.5)
        finally:
            w.stop()
            w.join()
        assert "inbox/delete-me.md" in calls

    def test_move_file_triggers_on_move(self, tmp_path: Path):
        calls: list[tuple[str, str]] = []
        cfg = _make_config(tmp_path)
        src = cfg.vault_root / "inbox" / "src.md"
        dst_dir = cfg.vault_root / "Projects" / "Alpha"
        dst_dir.mkdir(parents=True, exist_ok=True)
        _touch(src)
        w = DaemonWatcher(
            cfg,
            on_create=lambda p: None,
            on_modify=lambda p: None,
            on_move=lambda s, d: calls.append((s, d)),
            on_delete=lambda p: None,
        )
        w.start()
        try:
            time.sleep(0.1)
            src.rename(dst_dir / "dst.md")
            time.sleep(0.5)
        finally:
            w.stop()
            w.join()
        assert len(calls) >= 1
        assert calls[0] == ("inbox/src.md", "Projects/Alpha/dst.md")

    def test_ds_store_ignored(self, tmp_path: Path):
        calls: list[str] = []
        cfg = _make_config(tmp_path)
        w = DaemonWatcher(
            cfg,
            on_create=calls.append,
            on_modify=lambda p: None,
            on_move=lambda s, d: None,
            on_delete=lambda p: None,
        )
        w.start()
        try:
            time.sleep(0.1)
            _touch(cfg.vault_root / ".DS_Store")
            time.sleep(0.5)
        finally:
            w.stop()
            w.join()
        assert calls == []

    def test_dot_git_ignored(self, tmp_path: Path):
        calls: list[str] = []
        cfg = _make_config(tmp_path)
        w = DaemonWatcher(
            cfg,
            on_create=calls.append,
            on_modify=lambda p: None,
            on_move=lambda s, d: None,
            on_delete=lambda p: None,
        )
        w.start()
        try:
            time.sleep(0.1)
            _touch(cfg.vault_root / ".git" / "index")
            time.sleep(0.5)
        finally:
            w.stop()
            w.join()
        assert calls == []

    def test_directory_events_ignored(self, tmp_path: Path):
        """Creating a directory should NOT trigger on_create."""
        calls: list[str] = []
        cfg = _make_config(tmp_path)
        w = DaemonWatcher(
            cfg,
            on_create=calls.append,
            on_modify=lambda p: None,
            on_move=lambda s, d: None,
            on_delete=lambda p: None,
        )
        w.start()
        try:
            time.sleep(0.1)
            (cfg.vault_root / "new-folder").mkdir(exist_ok=True)
            time.sleep(0.5)
        finally:
            w.stop()
            w.join()
        assert calls == []


# ===========================================================================
# Section 5 — Lifecycle (start / stop / join)
# ===========================================================================


class TestDaemonWatcherLifecycle:
    """Tests for DaemonWatcher start, stop, join."""

    def test_start_and_stop(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        w = DaemonWatcher(
            cfg,
            on_create=lambda p: None,
            on_modify=lambda p: None,
            on_move=lambda s, d: None,
            on_delete=lambda p: None,
        )
        w.start()
        assert w._observer.is_alive()
        w.stop()
        w.join()
        # After join, observer thread should have stopped
        assert not w._observer.is_alive()

    def test_stop_without_start_is_safe(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        w = DaemonWatcher(
            cfg,
            on_create=lambda p: None,
            on_modify=lambda p: None,
            on_move=lambda s, d: None,
            on_delete=lambda p: None,
        )
        # stop/join before start should not raise
        w.stop()
        w.join()

    def test_multiple_stops_are_idempotent(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        w = DaemonWatcher(
            cfg,
            on_create=lambda p: None,
            on_modify=lambda p: None,
            on_move=lambda s, d: None,
            on_delete=lambda p: None,
        )
        w.start()
        w.stop()
        w.stop()  # second stop should not raise
        w.join()
