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
    """

    uploaded: int = 0
    re_uploaded: int = 0
    deleted: int = 0
    skipped: int = 0


# ── task outcome tag values (private) ─────────────────────────────────────────

_OUTCOME_UPLOADED = "uploaded"
_OUTCOME_RE_UPLOADED = "re_uploaded"
_OUTCOME_DELETED = "deleted"
_OUTCOME_SKIPPED = "skipped"


# ── public API ────────────────────────────────────────────────────────────────


async def scan(config: DaemonConfig, client: httpx.AsyncClient) -> ScanResult:
    """Reconcile the vault's on-disk state with the cloud manifest.

    1. Fetch cloud state from ``GET /api/state``.
    2. Walk *vault_root* applying *ignore_patterns* via
       :func:`daemon.watcher.should_skip_path`.
    3. For every file on disk, compute its SHA-256 content hash and
       NFC-normalised vault-relative path.
    4. Compare against the cloud manifest:
       - disk-only          → extract + upload  (``uploaded``)
       - both, hash differs  → extract + upload  (``re_uploaded``)
       - cloud-only         → report_deleted     (``deleted``)
       - both, hash matches → skip               (``skipped``)
    5. Uploads and delete reports run concurrently via
       ``asyncio.Semaphore(config.upload_concurrency)``.

    Returns:
        A :class:`ScanResult` with aggregate counts.
    """

    # ── 1. Fetch cloud state ──────────────────────────────────────────────
    cloud_state = await _fetch_cloud_state(config, client)

    # ── 2. Walk vault + build disk state ──────────────────────────────────
    disk_state, unreadable = _build_disk_state(config)

    # ── 3. Compute the sets of paths ──────────────────────────────────────
    disk_paths = set(disk_state.keys())
    cloud_paths = set(cloud_state.keys())

    both = disk_paths & cloud_paths
    disk_only = disk_paths - cloud_paths
    cloud_only = cloud_paths - disk_paths - unreadable

    result = ScanResult()

    # ── 4. Build task list ────────────────────────────────────────────────
    sem = asyncio.Semaphore(config.upload_concurrency)
    tasks: list[asyncio.Task[tuple[str, bool]]] = []

    # Files only on disk → "uploaded"
    for vp in disk_only:
        tasks.append(
            asyncio.create_task(
                _upload_one(sem, config, client, vp, _OUTCOME_UPLOADED)
            )
        )

    # Files on both but hash differs → "re_uploaded" (or NULL cloud hash)
    # Files on both with matching hash → skip
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
        tasks.append(
            asyncio.create_task(
                _delete_one(sem, config, client, vp)
            )
        )

    # ── 5. Execute all tasks concurrently ─────────────────────────────────
    if tasks:
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                _log.warning("scan task raised exception", exc=outcome)
                continue
            # outcome is (tag, ok) where tag is the category and ok is bool
            if isinstance(outcome, tuple) and len(outcome) == 2:
                tag, ok = outcome
                if ok:
                    if tag == _OUTCOME_UPLOADED:
                        result.uploaded += 1
                    elif tag == _OUTCOME_RE_UPLOADED:
                        result.re_uploaded += 1
                    elif tag == _OUTCOME_DELETED:
                        result.deleted += 1

    return result


# ── internal helpers ──────────────────────────────────────────────────────────


async def _fetch_cloud_state(
    config: DaemonConfig, client: httpx.AsyncClient,
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
        # Return empty dict so we treat everything as disk-only and upload all.
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
        # content_hash can be None / NULL → treat as "always re-upload"
        if ch is not None and not isinstance(ch, str):
            ch = None
        result[vp] = ch

    return result


def _build_disk_state(config: DaemonConfig) -> dict[str, str]:
    """Walk the vault and return ``({vault_path: sha256_hex}, unreadable_set)``.

    Directories are skipped; files matching *ignore_patterns* are skipped.
    Unreadable files are logged at WARNING and added to the ``unreadable`` set
    so the scan() caller can exclude them from cloud-only deletion.
    """
    result: dict[str, str] = {}
    unreadable: set[str] = set()
    vault_root = config.vault_root.resolve()
    ignore_patterns = config.ignore_patterns

    for dirpath_str, _dirnames, filenames in os.walk(str(vault_root)):
        dirpath = Path(dirpath_str)

        for fname in filenames:
            filepath = dirpath / fname

            # Apply ignore patterns (pass root=vault_root so only vault-relative
            # components are checked, not parent directories above the vault).
            if should_skip_path(filepath, ignore_patterns, root=vault_root):
                continue

            # Compute vault-relative path (NFC-normalised POSIX)
            try:
                rel = filepath.resolve().relative_to(vault_root)
            except ValueError:
                _log.warning("File outside vault_root during scan: %s", filepath)
                continue

            vault_path = unicodedata.normalize("NFC", rel.as_posix())

            # Compute content hash
            try:
                raw = filepath.read_bytes()
            except OSError:
                _log.warning("Cannot read file during scan, will not delete cloud copy: %s", filepath)
                unreadable.add(vault_path)
                continue

            content_hash = hashlib.sha256(raw).hexdigest()
            result[vault_path] = content_hash

    return result, unreadable


async def _upload_one(
    sem: asyncio.Semaphore,
    config: DaemonConfig,
    client: httpx.AsyncClient,
    vault_path: str,
    tag: str,
) -> tuple[str, bool]:
    """Extract + upload a single file and return ``(tag, ok)``."""
    async with sem:
        # Reconstruct the absolute path on disk
        disk_path = config.vault_root / vault_path

        # Extract
        match extract(disk_path, config.vault_root, config.max_file_size_bytes):
            case Success(value=TextContent() as tc):
                match await upload_text(client, config, tc):
                    case Success():
                        return (tag, True)
                    case Failure() as f:
                        _log.warning(
                            "upload_text failed for %s: %s", vault_path, f.error
                        )
                        return (tag, False)
            case Success(value=BinaryContent() as bc):
                match await upload_binary(client, config, bc):
                    case Success():
                        return (tag, True)
                    case Failure() as f:
                        _log.warning(
                            "upload_binary failed for %s: %s", vault_path, f.error
                        )
                        return (tag, False)
            case Failure() as f:
                _log.warning(
                    "extract failed for %s: %s", vault_path, f.error
                )
                return (tag, False)


async def _delete_one(
    sem: asyncio.Semaphore,
    config: DaemonConfig,
    client: httpx.AsyncClient,
    vault_path: str,
) -> tuple[str, bool]:
    """Report a deleted file and return ``(tag, ok)``."""
    async with sem:
        match await report_deleted(client, config, vault_path):
            case Success():
                return (_OUTCOME_DELETED, True)
            case Failure() as f:
                _log.warning(
                    "report_deleted failed for %s: %s", vault_path, f.error
                )
                return (_OUTCOME_DELETED, False)
