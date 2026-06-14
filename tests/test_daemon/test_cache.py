"""
tests/test_daemon/test_cache.py

Tests for daemon/cache.py — LocalCache and DaemonSyncState.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from unittest.mock import patch

from daemon.cache import DaemonSyncState, LocalCache


class TestLoad:
    """Tests for LocalCache.load()."""

    def test_load_valid_json_populates_memory_and_get_returns_entry(
        self, tmp_path: Path
    ):
        cache_file = tmp_path / "cache.json"
        entries = {
            "Projects/Alpha/note.md": {"hash": "abc123", "size": 1024, "mtime": 1.23},
            "inbox/report.pdf.md": {"hash": "def456", "size": 2048, "mtime": 2.34},
        }
        cache_file.write_text(json.dumps(entries))

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        cache.load(cache_file)

        # get() returns the right entry
        assert cache.get("Projects/Alpha/note.md") == {
            "hash": "abc123",
            "size": 1024,
            "mtime": 1.23,
        }
        assert cache.get("inbox/report.pdf.md") == {
            "hash": "def456",
            "size": 2048,
            "mtime": 2.34,
        }
        # Non-existent key returns None
        assert cache.get("nonexistent") is None

    def test_load_garbled_json_starts_with_empty_dict(self, tmp_path: Path, caplog):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("this is not json {{{")  # garbled

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        with caplog.at_level(logging.WARNING, logger="daemon.cache"):
            cache.load(cache_file)

        assert cache.snapshot() == {}
        assert any("garbled JSON" in r.message for r in caplog.records)

    def test_load_non_dict_root_starts_with_empty_dict(self, tmp_path: Path, caplog):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps([1, 2, 3]))  # list, not dict

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        with caplog.at_level(logging.WARNING, logger="daemon.cache"):
            cache.load(cache_file)

        assert cache.snapshot() == {}
        assert any("not a dict" in r.message for r in caplog.records)

    def test_load_malformed_entries_starts_with_empty_dict(
        self, tmp_path: Path, caplog
    ):
        cache_file = tmp_path / "cache.json"
        # One valid entry, one malformed (missing "hash")
        entries = {
            "good.md": {"hash": "abc123", "size": 1024, "mtime": 1.23},
            "bad.md": {"size": 2048, "mtime": 2.34},  # NO hash
        }
        cache_file.write_text(json.dumps(entries))

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        with caplog.at_level(logging.WARNING, logger="daemon.cache"):
            cache.load(cache_file)

        # Malformed entry causes entire cache to be discarded
        assert cache.snapshot() == {}
        assert any("malformed" in r.message for r in caplog.records)

    def test_load_missing_file_starts_with_empty_dict(self, tmp_path: Path, caplog):
        cache_file = tmp_path / "nonexistent.json"

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        with caplog.at_level(logging.WARNING, logger="daemon.cache"):
            cache.load(cache_file)

        assert cache.snapshot() == {}
        assert any("not found" in r.message for r in caplog.records)


class TestSave:
    """Tests for LocalCache.save()."""

    def test_save_writes_file_that_load_can_read_back_identically(self, tmp_path: Path):
        cache_file = tmp_path / "cache.json"
        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        cache.set_after_ack("a.md", "hash1", 100, 1.0)
        cache.set_after_ack("b.md", "hash2", 200, 2.0)
        cache.save(cache_file)

        # Load into a fresh cache instance
        cache2 = LocalCache(DaemonSyncState())
        cache2.load(cache_file)
        assert cache2.snapshot() == {
            "a.md": {"hash": "hash1", "size": 100, "mtime": 1.0},
            "b.md": {"hash": "hash2", "size": 200, "mtime": 2.0},
        }

    def test_save_creates_parent_directory_if_missing(self, tmp_path: Path):
        cache_file = tmp_path / "subdir" / "nested" / "cache.json"
        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        cache.set_after_ack("x.md", "hash", 50, 3.0)
        cache.save(cache_file)

        assert cache_file.exists()
        # Load back for sanity
        cache2 = LocalCache(DaemonSyncState())
        cache2.load(cache_file)
        assert cache2.get("x.md") == {"hash": "hash", "size": 50, "mtime": 3.0}

    def test_save_uses_atomic_rename_old_file_preserved_on_write_failure(
        self, tmp_path: Path
    ):
        """Simulate a mid-save crash: old file must survive intact."""
        cache_file = tmp_path / "cache.json"
        old_entries = {"old.md": {"hash": "old", "size": 1, "mtime": 0.0}}
        cache_file.write_text(json.dumps(old_entries))

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        cache.load(cache_file)

        # Add a new entry
        cache.set_after_ack("new.md", "newhash", 2, 1.0)

        # Now simulate a crash during write: patch os.replace to raise
        original_replace = os.replace

        def crashing_replace(src, dst):
            # Before replace happens, old file should still be intact
            assert cache_file.read_text() == json.dumps(old_entries)
            raise OSError("simulated disk full")

        with patch("os.replace", crashing_replace):
            cache.save(cache_file)

        # After failed save, old file should still be intact
        assert cache_file.read_text() == json.dumps(old_entries)

        # Restore and save properly
        os.replace = original_replace
        cache.save(cache_file)
        reloaded = LocalCache(DaemonSyncState())
        reloaded.load(cache_file)
        assert reloaded.get("new.md") == {"hash": "newhash", "size": 2, "mtime": 1.0}


class TestConcurrency:
    """Thread-safety tests."""

    def test_concurrent_get_and_set_after_ack_do_not_corrupt_map(self):
        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        errors = []
        ready = threading.Barrier(4)

        def writer():
            ready.wait()
            for i in range(100):
                try:
                    cache.set_after_ack(f"file{i}.md", f"hash{i}", i, float(i))
                except Exception as e:
                    errors.append(e)

        def reader():
            ready.wait()
            for _ in range(1000):
                try:
                    cache.get("file0.md")  # any key
                    cache.snapshot()
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(2)] + [
            threading.Thread(target=reader) for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Thread errors: {errors}"
        # After all writes, we should have 100 entries
        snapshot = cache.snapshot()
        assert len(snapshot) == 100
        for i in range(100):
            assert snapshot[f"file{i}.md"] == {
                "hash": f"hash{i}",
                "size": i,
                "mtime": float(i),
            }


class TestSnapshot:
    """Tests for LocalCache.snapshot()."""

    def test_snapshot_returns_frozen_copy_unaffected_by_subsequent_sets(self):
        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        cache.set_after_ack("a.md", "hash1", 100, 1.0)
        snap = cache.snapshot()

        # Modify cache after snapshot
        cache.set_after_ack("b.md", "hash2", 200, 2.0)
        cache.forget("a.md")

        # Snapshot should be unchanged
        assert snap == {"a.md": {"hash": "hash1", "size": 100, "mtime": 1.0}}
        # Live cache should have changed
        assert cache.get("a.md") is None
        assert cache.get("b.md") == {"hash": "hash2", "size": 200, "mtime": 2.0}


class TestRebuild:
    """Tests for LocalCache.rebuild()."""

    def test_rebuild_replaces_entire_map_old_entries_gone(self):
        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        cache.set_after_ack("old1.md", "h1", 10, 1.0)
        cache.set_after_ack("old2.md", "h2", 20, 2.0)

        new_entries = {
            "new1.md": {"hash": "nh1", "size": 30, "mtime": 3.0},
            "new2.md": {"hash": "nh2", "size": 40, "mtime": 4.0},
        }
        cache.rebuild(new_entries)

        # Old entries gone
        assert cache.get("old1.md") is None
        assert cache.get("old2.md") is None
        # New entries present
        assert cache.get("new1.md") == {"hash": "nh1", "size": 30, "mtime": 3.0}
        assert cache.get("new2.md") == {"hash": "nh2", "size": 40, "mtime": 4.0}
        # Snapshot matches
        assert cache.snapshot() == new_entries
