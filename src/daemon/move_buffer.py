"""
daemon/move_buffer.py

Move Detective — an in-memory buffer that holds pending deletes and matches
them with creates by fingerprint, so a delete-then-create pair becomes a
single move event.

Shares the ``threading.Lock`` from ``DaemonSyncState`` (defined in
``daemon.cache``) so the cache and move buffer share one lock.
"""

from __future__ import annotations

import time
from daemon.cache import DaemonSyncState


class MoveBuffer:
    """In-memory delete-create correlation buffer.

    Holds pending deletes keyed by content fingerprint.  A ``match_create``
    call with the same fingerprint consumes the entry and returns the old
    vault path, confirming a move.  Entries that outlive the move window are
    returned by ``expire()`` as confirmed deletes.

    Thread-safe via the shared lock from ``DaemonSyncState``.
    """

    def __init__(self, sync_state: DaemonSyncState) -> None:
        self._lock = sync_state.lock
        self._entries: dict[str, tuple[str, float]] = {}
        # {fingerprint: (vault_path, time.monotonic())}

    def park_delete(self, fingerprint: str, vault_path: str) -> None:
        """Record a delete candidate with the current monotonic timestamp.

        If a fingerprint collision exists (two deletes with same hash), the
        latest entry wins.
        """
        with self._lock:
            self._entries[fingerprint] = (vault_path, time.monotonic())

    def match_create(self, fingerprint: str) -> str | None:
        """If *fingerprint* is in the buffer, pop it and return the old
        ``vault_path``.  Returns ``None`` if no match.

        Does NOT check the time window — if the entry is still in the buffer
        it is a valid match.  ``expire()`` handles time-based cleanup.
        """
        with self._lock:
            entry = self._entries.pop(fingerprint, None)
            if entry is None:
                return None
            return entry[0]

    def expire(self, move_window_seconds: float) -> list[tuple[str, str]]:
        """Remove and return all entries older than *move_window_seconds*.

        Returns a list of ``(fingerprint, vault_path)`` tuples representing
        confirmed deletes (the create never arrived within the window).
        """
        with self._lock:
            now = time.monotonic()
            expired: list[tuple[str, str]] = []
            still_pending: dict[str, tuple[str, float]] = {}
            for fingerprint, (vault_path, ts) in self._entries.items():
                if now - ts > move_window_seconds:
                    expired.append((fingerprint, vault_path))
                else:
                    still_pending[fingerprint] = (vault_path, ts)
            self._entries = still_pending
            return expired
