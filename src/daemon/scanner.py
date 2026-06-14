"""
daemon/scanner.py

Startup scanner: disk-vs-cloud reconcile.

On daemon boot, compare the vault's current state against what the cloud knows,
and upload/report any differences so the cloud is fully up to date.

Usage:
    from daemon.scanner import scan, ScanResult

    result = await scan(config, client)
    logger.info("scan complete", **vars(result))
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import httpx

from core.result import Failure, Success
from daemon.cache import LocalCache
from daemon.config import DaemonConfig
from daemon.event_reporter import report_deleted
from daemon.extractor import BinaryContent, TextContent, extract
from daemon.uploader import upload_binary, upload_text
from daemon.watcher import should_skip_path

_log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Aggregate counts from a startup scan.

    Attributes:
        uploaded:    Files on disk but not in cloud → extracted + uploaded.
        re_uploaded: Files on disk AND cloud but hash differs → extracted + uploaded.
        deleted:     Files in cloud but not on disk → reported as deleted.
        skipped:     Files on disk AND cloud with matching hash → no action.
        moved:       Files detected as moved/renamed (reserved for future use).
    """

    uploaded: int = 0
    re_uploaded: int = 0
    deleted: int = 0
    skipped: int = 0
    moved: int = 0


# ── task outcome tag values (private) ─────────────────────────────────────────

_OUTCOME_UPLOADED = "uploaded"
_OUTCOME_RE_UPLOADED = "re_uploaded"
_OUTCOME_DELETED = "deleted"
_OUTCOME_SKIPPED = "skipped"


# ── public API ────────────────────────────────────────────────────────────────


async def scan(
    config: DaemonConfig,
    client: httpx.AsyncClient,
    cache: LocalCache | None = None,
    candidate_deletes: dict[str, int] | None = None,
    sweep_delete_confirmations: int = 1,
) -> ScanResult:
    """Reconcile the vault's on-disk state with the cloud manifest.

    When *cache* is ``None`` (backward-compatible A1 path):
      Standard 2-way disk-vs-cloud diff.

    When *cache* is provided (3-way reconcile):
      Applies the 9-row Decision Rulebook (disk vs cache vs cloud).
      Conservative deletes via *candidate_deletes* and
      *sweep_delete_confirmations*.  On successful upload the cache is
      updated; after all tasks the cache is rebuilt from resolved entries.

    Returns:
        A :class:`ScanResult` with aggregate counts.
    """

    # ── 1. Fetch cloud state ──────────────────────────────────────────────
    cloud_state = await _fetch_cloud_state(config, client)

    # ── 2. Walk vault + build disk state ──────────────────────────────────

    # ── A1 backward-compat path (cache=None) ──────────────────────────────
    if cache is None:
        disk_state, unreadable = _build_disk_state(config)
        return await _scan_2way(config, client, cloud_state, disk_state, unreadable)

    # ── 3-way path (cache is provided) ────────────────────────────────────
    # Single vault walk: _build_disk_entries produces entries for cache +
    # disk_state + unreadable for scan logic.
    disk_entries, disk_state, unreadable = _build_disk_entries(config)
    return await _scan_3way(
        config,
        client,
        cloud_state,
        disk_state,
        unreadable,
        cache,
        candidate_deletes,
        sweep_delete_confirmations,
        disk_entries,
    )


# ── 2-way reconcile (original A1 logic, unchanged) ──────────────────────────


async def _scan_2way(
    config: DaemonConfig,
    client: httpx.AsyncClient,
    cloud_state: dict[str, str | None],
    disk_state: dict[str, str],
    unreadable: set[str],
) -> ScanResult:
    """Original A1 2-way disk-vs-cloud reconcile."""
    disk_paths = set(disk_state.keys())
    cloud_paths = set(cloud_state.keys())

    both = disk_paths & cloud_paths
    disk_only = disk_paths - cloud_paths
    cloud_only = cloud_paths - disk_paths - unreadable

    result = ScanResult()

    sem = asyncio.Semaphore(config.upload_concurrency)
    tasks: list[asyncio.Task[tuple[str, str, bool]]] = []

    # Files only on disk → "uploaded"
    for vp in disk_only:
        tasks.append(
            asyncio.create_task(_upload_one(sem, config, client, vp, _OUTCOME_UPLOADED))
        )

    # Files on both but hash differs → "re_uploaded" (or NULL cloud hash)
    for vp in both:
        cloud_hash = cloud_state[vp]
        disk_hash = disk_state[vp]
        if cloud_hash is None or cloud_hash != disk_hash:
            tasks.append(
                asyncio.create_task(
                    _upload_one(sem, config, client, vp, _OUTCOME_RE_UPLOADED)
                )
            )
        else:
            result.skipped += 1

    # Cloud-only files → "deleted"
    for vp in cloud_only:
        tasks.append(asyncio.create_task(_delete_one(sem, config, client, vp)))

    # Execute all tasks concurrently
    if tasks:
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                _log.warning("scan task raised exception exc=%s", outcome)
                continue
            if isinstance(outcome, tuple) and len(outcome) == 3:
                tag, _vp, ok = outcome
                if ok:
                    if tag == _OUTCOME_UPLOADED:
                        result.uploaded += 1
                    elif tag == _OUTCOME_RE_UPLOADED:
                        result.re_uploaded += 1
                    elif tag == _OUTCOME_DELETED:
                        result.deleted += 1

    return result


# ── 3-way reconcile ──────────────────────────────────────────────────────────


async def _scan_3way(
    config: DaemonConfig,
    client: httpx.AsyncClient,
    cloud_state: dict[str, str | None],
    disk_state: dict[str, str],
    unreadable: set[str],
    cache: LocalCache,
    candidate_deletes: dict[str, int] | None,
    sweep_delete_confirmations: int,
    disk_entries: dict[str, dict],
) -> ScanResult:
    """3-way reconcile: disk vs cache vs cloud (9-row Decision Rulebook)."""

    # Take cache snapshot
    cache_state = cache.snapshot()

    # Build all_paths
    disk_paths = set(disk_state.keys())
    all_paths = disk_paths | set(cache_state.keys()) | set(cloud_state.keys())

    result = ScanResult()
    sem = asyncio.Semaphore(config.upload_concurrency)
    tasks: list[asyncio.Task[tuple[str, str, bool]]] = []

    # Resolved entries for cache rebuild (disk files now in cloud)
    resolved_entries: dict[str, dict] = {}

    # Candidate deletes dict (caller-provided or local)
    if candidate_deletes is None:
        candidate_deletes = {}

    # ── Apply 9-row Decision Rulebook ─────────────────────────────────────

    for vp in all_paths:
        disk_hash = disk_state.get(vp)  # None if absent from disk
        cache_entry = cache_state.get(vp)  # None or dict
        cache_hash = cache_entry["hash"] if cache_entry is not None else None
        cloud_present = vp in cloud_state
        cloud_hash = cloud_state.get(vp)  # None if absent OR explicitly null

        # Row 1: Disk ✔, Cache --, Cloud -- → Upload (brand-new)
        if disk_hash is not None and cache_hash is None and not cloud_present:
            tasks.append(
                asyncio.create_task(
                    _upload_one(
                        sem,
                        config,
                        client,
                        vp,
                        _OUTCOME_UPLOADED,
                        cache=cache,
                        disk_hash=disk_hash,
                        resolved_entries=resolved_entries,
                    )
                )
            )
            continue

        # Row 2: Disk ✔, Cache ✔, Cloud -- → Re-upload (rollback heal)
        if disk_hash is not None and cache_hash is not None and not cloud_present:
            tasks.append(
                asyncio.create_task(
                    _upload_one(
                        sem,
                        config,
                        client,
                        vp,
                        _OUTCOME_RE_UPLOADED,
                        cache=cache,
                        disk_hash=disk_hash,
                        resolved_entries=resolved_entries,
                    )
                )
            )
            continue

        # Row 9 (extended): Disk ✔, Cloud None (null hash) → Re-upload
        # Per A1 behavior, NULL cloud hash always triggers re-upload.
        if disk_hash is not None and cloud_present and cloud_hash is None:
            tasks.append(
                asyncio.create_task(
                    _upload_one(
                        sem,
                        config,
                        client,
                        vp,
                        _OUTCOME_RE_UPLOADED,
                        cache=cache,
                        disk_hash=disk_hash,
                        resolved_entries=resolved_entries,
                    )
                )
            )
            continue

        # Row 5: Disk ✔, Cloud ✔ differ (hash differs from disk) → Re-upload
        if (
            disk_hash is not None
            and cloud_present
            and cloud_hash is not None
            and cloud_hash != disk_hash
        ):
            tasks.append(
                asyncio.create_task(
                    _upload_one(
                        sem,
                        config,
                        client,
                        vp,
                        _OUTCOME_RE_UPLOADED,
                        cache=cache,
                        disk_hash=disk_hash,
                        resolved_entries=resolved_entries,
                    )
                )
            )
            continue

        # Row 3+4 (merged): Disk ✔, Cloud ✔ same → Skip + heal cache
        # Covers both Row 3 (cache missing) and Row 4 (cache matching),
        # plus the implicit case where cache is present but stale.
        if disk_hash is not None and cloud_present and cloud_hash == disk_hash:
            result.skipped += 1
            entry = disk_entries.get(vp)
            if entry is not None:
                resolved_entries[vp] = entry
                if cache_hash != disk_hash:
                    # Cache missing or stale → heal it
                    cache.set_after_ack(
                        vp, entry["hash"], entry["size"], entry["mtime"]
                    )
            continue

        # Row 6: Disk --, Cache ✔, Cloud ✔ → Candidate delete
        if (
            disk_hash is None
            and cache_hash is not None
            and cloud_present
            and cloud_hash is not None
            and vp not in unreadable
        ):
            _handle_candidate_delete(
                vp,
                candidate_deletes,
                sweep_delete_confirmations,
                sem,
                config,
                client,
                cache,
                result,
                tasks,
            )
            continue

        # Row 7: Disk --, Cache ✔, Cloud -- → Drop stale cache
        if disk_hash is None and cache_hash is not None and not cloud_present:
            cache.forget(vp)
            continue

        # Row 8: Disk --, Cache --, Cloud ✔ → Candidate delete (cloud-only)
        if (
            disk_hash is None
            and cache_hash is None
            and cloud_present
            and cloud_hash is not None
        ):
            if vp not in unreadable:
                _handle_candidate_delete(
                    vp,
                    candidate_deletes,
                    sweep_delete_confirmations,
                    sem,
                    config,
                    client,
                    cache,
                    result,
                    tasks,
                )
            continue

    # ── Clear candidate_deletes for files that reappeared on disk ─────────
    for vp in disk_paths:
        if vp in candidate_deletes:
            del candidate_deletes[vp]

    # ── Execute all tasks concurrently ─────────────────────────────────────
    if tasks:
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                _log.warning("scan task raised exception exc=%s", outcome)
                continue
            if isinstance(outcome, tuple) and len(outcome) == 3:
                tag, _vp, ok = outcome
                if ok:
                    if tag == _OUTCOME_UPLOADED:
                        result.uploaded += 1
                    elif tag == _OUTCOME_RE_UPLOADED:
                        result.re_uploaded += 1
                    elif tag == _OUTCOME_DELETED:
                        result.deleted += 1

    # ── Rebuild cache from resolved entries ────────────────────────────────
    cache.rebuild(resolved_entries)

    return result


def _handle_candidate_delete(
    vp: str,
    candidate_deletes: dict[str, int],
    sweep_delete_confirmations: int,
    sem: asyncio.Semaphore,
    config: DaemonConfig,
    client: httpx.AsyncClient,
    cache: LocalCache,
    result: ScanResult,
    tasks: list[asyncio.Task[tuple[str, str, bool]]],
) -> None:
    """Track a candidate-delete and trigger deletion when threshold met.

    Increments ``candidate_deletes[vp]``.  When the count reaches
    *sweep_delete_confirmations*, a delete task is appended to *tasks*,
    the path is removed from the cache and candidate tracking.
    """
    count = candidate_deletes.get(vp, 0) + 1
    candidate_deletes[vp] = count

    if count >= sweep_delete_confirmations:
        # Threshold met — schedule actual deletion
        tasks.append(
            asyncio.create_task(
                _delete_one(sem, config, client, vp, forget_cache=cache)
            )
        )
        # Remove from tracking once deletion is scheduled
        del candidate_deletes[vp]


# ── internal helpers ──────────────────────────────────────────────────────────


async def _fetch_cloud_state(
    config: DaemonConfig,
    client: httpx.AsyncClient,
) -> dict[str, str | None]:
    """Fetch the cloud manifest and return ``{vault_path: content_hash}``.

    ``content_hash`` may be ``None`` when the cloud has not stored a hash
    (treated as "always re-upload").
    """
    url = f"{config.cloud_endpoint}/api/state"
    headers = {"Authorization": f"Bearer {config.api_key}"}

    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        _log.error("Failed to fetch cloud state: %s", exc)
        return {}

    try:
        body = response.json()
    except ValueError:
        _log.error("Cloud state response is not valid JSON")
        return {}

    if not isinstance(body, dict) or "documents" not in body:
        _log.error("Unexpected cloud state response format: %s", type(body))
        return {}

    documents = body["documents"]
    if not isinstance(documents, list):
        _log.error("Cloud state 'documents' is not a list")
        return {}

    result: dict[str, str | None] = {}
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        vp = doc.get("vault_path")
        if not isinstance(vp, str) or not vp:
            continue
        ch = doc.get("content_hash")
        if ch is not None and not isinstance(ch, str):
            ch = None
        result[vp] = ch

    return result


def _build_disk_state(config: DaemonConfig) -> tuple[dict[str, str], set[str]]:
    """Walk the vault and return ``({vault_path: sha256_hex}, unreadable_set)``.

    Directories are skipped; files matching *ignore_patterns* are skipped.
    Unreadable files are logged at WARNING and added to the ``unreadable`` set
    so the caller can exclude them from cloud-only deletion.
    """
    result: dict[str, str] = {}
    unreadable: set[str] = set()
    vault_root = config.vault_root.resolve()
    ignore_patterns = config.ignore_patterns

    for dirpath_str, _dirnames, filenames in os.walk(str(vault_root)):
        dirpath = Path(dirpath_str)

        for fname in filenames:
            filepath = dirpath / fname

            if should_skip_path(filepath, ignore_patterns, root=vault_root):
                continue

            try:
                rel = filepath.resolve().relative_to(vault_root)
            except ValueError:
                _log.warning("File outside vault_root during scan: %s", filepath)
                continue

            vault_path = unicodedata.normalize("NFC", rel.as_posix())

            try:
                raw = filepath.read_bytes()
            except OSError:
                _log.warning(
                    "Cannot read file during scan, will not delete cloud copy: %s",
                    filepath,
                )
                unreadable.add(vault_path)
                continue

            content_hash = hashlib.sha256(raw).hexdigest()
            result[vault_path] = content_hash

    return result, unreadable


def _build_disk_entries(
    config: DaemonConfig,
) -> tuple[dict[str, dict], dict[str, str], set[str]]:
    """Walk the vault once and return ``(entries, disk_state, unreadable)``.

    *entries*: ``{vault_path: {"hash": ..., "size": ..., "mtime": ...}}``
        Full fingerprint for cache rebuild / cache-on-ack.
    *disk_state*: ``{vault_path: sha256_hex}``
        Hash-only dict for scan diff logic.
    *unreadable*: ``set[str]``
        Paths that could not be read (excluded from cloud-only deletion).

    This supersedes :func:`_build_disk_state` by producing both outputs from
    a single walk, avoiding a double read+hash in the 3-way scan path.
    """
    entries: dict[str, dict] = {}
    disk_state: dict[str, str] = {}
    unreadable: set[str] = set()
    vault_root = config.vault_root.resolve()
    ignore_patterns = config.ignore_patterns

    for dirpath_str, _dirnames, filenames in os.walk(str(vault_root)):
        dirpath = Path(dirpath_str)

        for fname in filenames:
            filepath = dirpath / fname

            if should_skip_path(filepath, ignore_patterns, root=vault_root):
                continue

            try:
                rel = filepath.resolve().relative_to(vault_root)
            except ValueError:
                _log.warning("File outside vault_root during scan: %s", filepath)
                continue

            vault_path = unicodedata.normalize("NFC", rel.as_posix())

            try:
                stat = filepath.stat()
                raw = filepath.read_bytes()
            except OSError:
                _log.warning(
                    "Cannot read file during scan, will not delete cloud copy: %s",
                    filepath,
                )
                unreadable.add(vault_path)
                continue

            content_hash = hashlib.sha256(raw).hexdigest()
            entries[vault_path] = {
                "hash": content_hash,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
            disk_state[vault_path] = content_hash

    return entries, disk_state, unreadable


async def _upload_one(
    sem: asyncio.Semaphore,
    config: DaemonConfig,
    client: httpx.AsyncClient,
    vault_path: str,
    tag: str,
    cache: LocalCache | None = None,
    disk_hash: str | None = None,
    resolved_entries: dict[str, dict] | None = None,
) -> tuple[str, str, bool]:
    """Extract + upload a single file and return ``(tag, vault_path, ok)``.

    When *cache* and *disk_hash* are provided, on successful upload the
    file's size and mtime are read from disk and ``cache.set_after_ack()``
    is called.  The entry is also added to *resolved_entries* if provided.
    On failure the cache is NOT touched.
    """
    async with sem:
        disk_path = config.vault_root / vault_path

        match extract(disk_path, config.vault_root, config.max_file_size_bytes):
            case Success(value=TextContent() as tc):
                match await upload_text(client, config, tc):
                    case Success():
                        _cache_on_ack(
                            vault_path, disk_path, cache, disk_hash, resolved_entries
                        )
                        return (tag, vault_path, True)
                    case Failure() as f:
                        _log.warning(
                            "upload_text failed for %s: %s", vault_path, f.error
                        )
                        return (tag, vault_path, False)
            case Success(value=BinaryContent() as bc):
                match await upload_binary(client, config, bc):
                    case Success():
                        _cache_on_ack(
                            vault_path, disk_path, cache, disk_hash, resolved_entries
                        )
                        return (tag, vault_path, True)
                    case Failure() as f:
                        _log.warning(
                            "upload_binary failed for %s: %s", vault_path, f.error
                        )
                        return (tag, vault_path, False)
            case Failure() as f:
                _log.warning("extract failed for %s: %s", vault_path, f.error)
                return (tag, vault_path, False)


def _cache_on_ack(
    vault_path: str,
    disk_path: Path,
    cache: LocalCache | None,
    disk_hash: str | None,
    resolved_entries: dict[str, dict] | None,
) -> None:
    """After a successful upload, optionally update the cache and resolved entries."""
    if cache is None or disk_hash is None:
        return
    try:
        stat = disk_path.stat()
    except OSError:
        _log.warning("Cannot stat file after upload for cache: %s", vault_path)
        return
    entry = {"hash": disk_hash, "size": stat.st_size, "mtime": stat.st_mtime}
    cache.set_after_ack(vault_path, disk_hash, stat.st_size, stat.st_mtime)
    if resolved_entries is not None:
        resolved_entries[vault_path] = entry


async def _delete_one(
    sem: asyncio.Semaphore,
    config: DaemonConfig,
    client: httpx.AsyncClient,
    vault_path: str,
    forget_cache: LocalCache | None = None,
) -> tuple[str, str, bool]:
    """Report a deleted file and return ``(tag, vault_path, ok)``.

    On successful deletion, if *forget_cache* is provided the path is
    removed from the cache.
    """
    async with sem:
        match await report_deleted(client, config, vault_path):
            case Success():
                if forget_cache is not None:
                    forget_cache.forget(vault_path)
                return (_OUTCOME_DELETED, vault_path, True)
            case Failure() as f:
                _log.warning("report_deleted failed for %s: %s", vault_path, f.error)
                return (_OUTCOME_DELETED, vault_path, False)
