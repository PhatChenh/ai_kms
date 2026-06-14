"""
tests/test_daemon/test_move_buffer.py

Tests for daemon/move_buffer.py — MoveBuffer: delete-create correlation buffer.
"""

from __future__ import annotations

import time
import threading

import pytest

from daemon.cache import DaemonSyncState
from daemon.move_buffer import MoveBuffer


# ===========================================================================
# Helpers
# ===========================================================================


@pytest.fixture
def sync_state() -> DaemonSyncState:
    return DaemonSyncState()


@pytest.fixture
def move_buffer(sync_state: DaemonSyncState) -> MoveBuffer:
    return MoveBuffer(sync_state)


# ===========================================================================
# Test 1 — Tracer bullet: park_delete + match_create = move detected
# ===========================================================================


def test_park_delete_matched_by_create_returns_old_path(
    move_buffer: MoveBuffer,
) -> None:
    """A parked delete matched by a create with the same fingerprint returns
    the old vault path, confirming a move."""
    fingerprint = "abc123"
    old_path = "Projects/Alpha/old_note.md"

    move_buffer.park_delete(fingerprint, old_path)
    result = move_buffer.match_create(fingerprint)

    assert result == old_path


def test_match_create_with_no_match_returns_none(
    move_buffer: MoveBuffer,
) -> None:
    """A create with a fingerprint not in the buffer returns None."""
    result = move_buffer.match_create("nonexistent")
    assert result is None


# ===========================================================================
# Test 2 — Expiry: expired entries returned as confirmed deletes
# ===========================================================================


def test_expired_entry_returned_by_expire(
    move_buffer: MoveBuffer,
) -> None:
    """A parked delete that outlives the move window is returned by
    ``expire()`` as a confirmed delete."""
    fingerprint = "expired_hash"
    old_path = "Projects/Beta/deleted_note.md"

    move_buffer.park_delete(fingerprint, old_path)
    # Wait past a very short window
    time.sleep(0.15)
    expired = move_buffer.expire(move_window_seconds=0.1)

    assert expired == [(fingerprint, old_path)]


def test_fresh_entry_not_returned_by_expire(
    move_buffer: MoveBuffer,
) -> None:
    """A freshly parked entry is NOT returned by expire when the window
    hasn't elapsed."""
    move_buffer.park_delete("fresh", "some/path.md")
    expired = move_buffer.expire(move_window_seconds=1.0)

    assert expired == []


# ===========================================================================
# Test 3 — Fingerprint collision: latest entry wins
# ===========================================================================


def test_fingerprint_collision_keeps_latest_entry(
    move_buffer: MoveBuffer,
) -> None:
    """Two deletes with the same fingerprint keep the latest entry."""
    fingerprint = "collision_hash"
    first_path = "Projects/A/first.md"
    second_path = "Projects/B/second.md"

    move_buffer.park_delete(fingerprint, first_path)
    move_buffer.park_delete(fingerprint, second_path)

    result = move_buffer.match_create(fingerprint)
    assert result == second_path


# ===========================================================================
# Test 4 — Second match_create after consumption returns None
# ===========================================================================


def test_second_match_create_after_consumption_returns_none(
    move_buffer: MoveBuffer,
) -> None:
    """After ``match_create`` consumes an entry, a second call with the
    same fingerprint returns ``None``."""
    move_buffer.park_delete("consumed", "old.md")

    first = move_buffer.match_create("consumed")
    assert first == "old.md"

    second = move_buffer.match_create("consumed")
    assert second is None


# ===========================================================================
# Test 5 — Thread safety: concurrent park + match don't corrupt
# ===========================================================================


def test_concurrent_park_and_match_do_not_corrupt(
    sync_state: DaemonSyncState,
) -> None:
    """Two concurrent ``park_delete`` + ``match_create`` calls from
    different threads do not corrupt the buffer."""
    buffer = MoveBuffer(sync_state)
    errors: list[str] = []

    def parker() -> None:
        for i in range(500):
            try:
                buffer.park_delete(f"fp-{i}", f"path/{i}")
            except Exception as exc:
                errors.append(f"park_delete failed: {exc}")

    def matcher() -> None:
        for i in range(500):
            try:
                # Some matches, some non-matches — shouldn't matter
                buffer.match_create(f"fp-{i}")
            except Exception as exc:
                errors.append(f"match_create failed: {exc}")

    t1 = threading.Thread(target=parker)
    t2 = threading.Thread(target=matcher)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == [], f"concurrent errors: {errors}"
    # After all operations, the buffer should still be usable
    buffer.park_delete("final", "final_path")
    assert buffer.match_create("final") == "final_path"
