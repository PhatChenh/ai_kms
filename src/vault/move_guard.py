"""Thread-safe registry for pipeline-initiated binary move destinations.

When the capture pipeline moves a binary, it registers the destination path
before the move.  The watcher's re-home branch checks the registry and skips
re-home when the destination was registered — preventing the watcher from
re-homing a file the pipeline just intentionally moved.

All public API: MoveGuard, get_active, set_active.
"""

from __future__ import annotations

import threading
import time
import unicodedata
from pathlib import Path

_DEFAULT_TTL_SECONDS: float = 5.0

_active: MoveGuard | None = None


class MoveGuard:
    """Thread-safe set of path strings with TTL-based expiry.

    register(path)    — insert/update an NFC-normalised path with a TTL.
    check_and_consume(path) — return True (and remove) if the NFC-normalised
                               path is present and not expired; False otherwise.
    """

    def __init__(self) -> None:
        self._entries: dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def register(self, path: Path, ttl: float | None = None) -> None:
        """Insert or refresh *path* with the given *ttl* (default 5 s)."""
        key = unicodedata.normalize("NFC", str(path))
        expiry = time.monotonic() + (ttl if ttl is not None else _DEFAULT_TTL_SECONDS)
        with self._lock:
            self._entries[key] = expiry

    # ------------------------------------------------------------------
    def check_and_consume(self, path: Path) -> bool:
        """Return True if *path* was registered and not expired, removing it.

        Lazily drops expired entries on every call so the dict stays bounded.
        """
        key = unicodedata.normalize("NFC", str(path))
        now = time.monotonic()
        with self._lock:
            # Lazy cleanup of expired entries
            stale = [k for k, v in self._entries.items() if v <= now]
            for k in stale:
                del self._entries[k]
            # Check-and-remove
            if key in self._entries:
                del self._entries[key]
                return True
            return False


# ------------------------------------------------------------------
def set_active(guard: MoveGuard) -> None:
    """Publish the watcher's MoveGuard so pipelines can register moves."""
    global _active
    _active = guard


def get_active() -> MoveGuard | None:
    """Return the currently active MoveGuard, or None."""
    return _active
