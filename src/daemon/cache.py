"""
daemon/cache.py

Local Cache ("Local Notebook") — a thread-safe, advisory in-memory manifest
of vault_path → file fingerprint.  The cache is disposable by design (C-18).

Public API:
    DaemonSyncState  — lightweight holder for a shared threading.Lock
    LocalCache       — in-memory dict + 7 public methods:

        load(path)           read JSON, discard on any error
        save(path)           temp file + atomic rename
        get(vp)              return cached entry or None
        set_after_ack(...)   write entry (only call after cloud confirms)
        touch(vp, size, mt)  update size/mtime for an existing entry
        forget(vp)           remove entry
        snapshot()           frozen copy for 3-way compare
        rebuild(entries)     replace entire map (post-reconcile)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)


# ── Shared lock holder ──────────────────────────────────────────────────────


@dataclass
class DaemonSyncState:
    """Lightweight holder for a shared ``threading.Lock``.

    The cache and the move buffer both reference the same instance so they
    can share a single lock without either module knowing about the other.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)


# ── Cache entry shape ───────────────────────────────────────────────────────
#  {"hash": str, "size": int, "mtime": float}


def _validate_entry(vault_path: str, entry: object) -> dict | None:
    """Return the entry dict if it matches the expected shape, else None."""
    if not isinstance(entry, dict):
        return None
    if not isinstance(entry.get("hash"), str):
        return None
    size = entry.get("size")
    if isinstance(size, bool) or not isinstance(size, int):
        return None
    if not isinstance(entry.get("mtime"), (int, float)):
        return None
    return {
        "hash": entry["hash"],
        "size": entry["size"],
        "mtime": float(entry["mtime"]),
    }


# ── LocalCache ──────────────────────────────────────────────────────────────


class LocalCache:
    """Thread-safe in-memory manifest of vault_path → file fingerprint.

    The cache is **advisory** — the cloud is always the authority (C-18).
    Load errors, save errors, and corruption are all non-fatal: the cache
    degrades to empty and the daemon falls back to a full reconcile.
    """

    def __init__(self, sync_state: DaemonSyncState) -> None:
        self._lock = sync_state.lock
        self._entries: dict[str, dict] = {}

    # ── load ────────────────────────────────────────────────────────────────

    def load(self, path: Path) -> None:
        """Read JSON from *path*, validate, and populate entries.

        On **any** error — missing file, invalid JSON, non-dict root,
        malformed entries — reset to an empty dict and log a warning.
        Never raises.
        """
        with self._lock:
            self._entries = self._load_unlocked(path)

    def _load_unlocked(self, path: Path) -> dict[str, dict]:
        """Internal load logic; caller must hold the lock."""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            _log.warning("cache file not found, starting empty  path=%s", path)
            return {}
        except Exception:
            _log.warning(
                "cannot read cache file, starting empty  path=%s", path, exc_info=True
            )
            return {}

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            _log.warning("cache file is garbled JSON, starting empty  path=%s", path)
            return {}

        if not isinstance(data, dict):
            _log.warning("cache file root is not a dict, starting empty  path=%s", path)
            return {}

        entries: dict[str, dict] = {}
        for vp, entry in data.items():
            valid = _validate_entry(vp, entry)
            if valid is None:
                _log.warning(
                    "cache entry malformed, discarding entire cache  path=%s entry_key=%s",
                    path,
                    vp,
                )
                return {}
            entries[vp] = valid

        return entries

    # ── save ────────────────────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        """Write entries to *path* atomically (temp file + ``os.replace()``).

        On write failure, log a warning and continue — cache is advisory.
        Never raises.
        """
        with self._lock:
            self._save_unlocked(path)

    def _save_unlocked(self, path: Path) -> None:
        """Internal save logic; caller must hold the lock."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            _log.warning(
                "cannot create cache directory  path=%s", path.parent, exc_info=True
            )
            return

        try:
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as f:
                    json.dump(self._entries, f, indent=2)
                    f.flush()
                    os.fsync(fd)
                os.close(fd)
                os.replace(tmp_name, path)
            except Exception:
                try:
                    os.close(fd)
                except OSError:
                    pass
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except Exception:
            _log.warning("cache save failed, continuing  path=%s", path, exc_info=True)

    # ── get ─────────────────────────────────────────────────────────────────

    def get(self, vault_path: str) -> dict | None:
        """Return a shallow copy of the cached entry for *vault_path*, or ``None``."""
        with self._lock:
            entry = self._entries.get(vault_path)
            return dict(entry) if entry is not None else None

    # ── touch ───────────────────────────────────────────────────────────────

    def touch(self, vault_path: str, size: int, mtime: float) -> None:
        """Update the *size* and *mtime* metadata for an existing entry.

        Unlike :meth:`set_after_ack`, this does NOT touch the content hash —
        it is intended for the bail-early path where the content is confirmed
        unchanged (hash matched) but the stat metadata is stale.  Does nothing
        if the entry does not exist.
        """
        with self._lock:
            entry = self._entries.get(vault_path)
            if entry is not None:
                entry["size"] = size
                entry["mtime"] = float(mtime)

    # ── set_after_ack ───────────────────────────────────────────────────────

    def set_after_ack(
        self, vault_path: str, hash: str, size: int, mtime: float
    ) -> None:
        """Store a fingerprint for *vault_path* (only call after cloud ack)."""
        with self._lock:
            self._entries[vault_path] = {"hash": hash, "size": size, "mtime": mtime}

    # ── forget ──────────────────────────────────────────────────────────────

    def forget(self, vault_path: str) -> None:
        """Remove *vault_path* from the cache if present."""
        with self._lock:
            self._entries.pop(vault_path, None)

    # ── snapshot ────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, dict]:
        """Return a shallow copy of all entries (top-level dict is new, value dicts
        are shared — the caller must not mutate them)."""
        with self._lock:
            return dict(self._entries)

    # ── rebuild ─────────────────────────────────────────────────────────────

    def rebuild(self, entries: dict[str, dict]) -> None:
        """Replace the entire in-memory map with *entries*.

        Each entry is validated via :func:`_validate_entry`; invalid entries are
        skipped with a warning.
        """
        with self._lock:
            clean: dict[str, dict] = {}
            for vp, entry in entries.items():
                valid = _validate_entry(vp, entry)
                if valid is None:
                    _log.warning(
                        "rebuild skipping malformed entry  vault_path=%s", vp
                    )
                    continue
                clean[vp] = valid
            self._entries = clean
