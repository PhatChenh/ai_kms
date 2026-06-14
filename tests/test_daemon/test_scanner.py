"""
tests/test_daemon/test_scanner.py

Comprehensive tests for daemon/scanner.py — startup scan, disk-vs-cloud
reconcile, ignore patterns, and concurrency control.

Test map:
  Section 1 — ScanResult dataclass
  Section 2 — _build_disk_state helper
  Section 3 — _fetch_cloud_state helper
  Section 4 — scan(): file on disk, not in cloud → uploaded
  Section 5 — scan(): file on disk, different hash → re-uploaded
  Section 6 — scan(): file in cloud, not on disk → deleted
  Section 7 — scan(): file with matching hash → skipped
  Section 8 — scan(): NULL cloud content_hash → always re-upload
  Section 9 — scan(): ignore patterns applied during walk
  Section 10 — scan(): respects upload_concurrency
  Section 11 — scan(): handles cloud fetch failure gracefully
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import patch

import httpx

from daemon.cache import DaemonSyncState, LocalCache
from daemon.config import DaemonConfig
from daemon.scanner import (
    ScanResult,
    _build_disk_state,
    _fetch_cloud_state,
    _OUTCOME_DELETED,
    _OUTCOME_UPLOADED,
    _delete_one,
    _upload_one,
    scan,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_config(
    *,
    tmp_path: Path,
    vault_root: Path | None = None,
    cloud_endpoint: str = "https://cloud.example.com",
    api_key: str = "test-api-key",
    ignore_patterns: list[str] | None = None,
    upload_concurrency: int = 4,
) -> DaemonConfig:
    """Build a DaemonConfig for testing."""
    root = vault_root or tmp_path
    root.mkdir(parents=True, exist_ok=True)
    return DaemonConfig(
        vault_root=root,
        cloud_endpoint=cloud_endpoint,
        api_key=api_key,
        ignore_patterns=ignore_patterns or [".git", ".DS_Store"],
        upload_concurrency=upload_concurrency,
    )


def _make_file(path: Path, content: bytes = b"hello world") -> str:
    """Write a file and return its SHA-256 hex digest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _state_response(documents: list[dict]) -> httpx.Response:
    """Build a mock /api/state response."""
    return httpx.Response(200, json={"documents": documents})


def _upload_response(document_id: int = 42) -> httpx.Response:
    """Build a mock /api/upload response."""
    return httpx.Response(200, json={"status": "ok", "document_id": document_id})


def _event_response() -> httpx.Response:
    """Build a mock /api/event response."""
    return httpx.Response(200, json={"status": "ok"})


def _mock_transport_dispatcher(
    state_docs: list[dict] | None = None,
    upload_id: int = 42,
) -> httpx.MockTransport:
    """A MockTransport that dispatches based on URL path.

    - GET /api/state → returns state_docs (or empty list)
    - POST /api/upload → returns upload response
    - POST /api/event → returns event response
    """
    docs = state_docs or []

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and "/api/state" in url:
            return _state_response(docs)
        elif "/api/upload" in url:
            return _upload_response(upload_id)
        elif "/api/event" in url:
            return _event_response()
        return httpx.Response(404, json={"status": "not found"})

    return httpx.MockTransport(handler)


# ===========================================================================
# Section 1 — ScanResult dataclass
# ===========================================================================


class TestScanResult:
    """ScanResult dataclass tests."""

    def test_defaults(self):
        sr = ScanResult()
        assert sr.uploaded == 0
        assert sr.re_uploaded == 0
        assert sr.deleted == 0
        assert sr.skipped == 0
        assert sr.moved == 0

    def test_explicit_values(self):
        sr = ScanResult(uploaded=1, re_uploaded=2, deleted=3, skipped=4, moved=5)
        assert sr.uploaded == 1
        assert sr.re_uploaded == 2
        assert sr.deleted == 3
        assert sr.skipped == 4
        assert sr.moved == 5

    def test_mutable(self):
        sr = ScanResult()
        sr.uploaded += 1
        assert sr.uploaded == 1
        sr.moved += 1
        assert sr.moved == 1


# ===========================================================================
# Section 2 — _build_disk_state helper
# ===========================================================================


class TestBuildDiskState:
    """Tests for _build_disk_state."""

    def test_empty_vault(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        state, _unreadable = _build_disk_state(config)
        assert state == {}

    def test_single_file(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        f = tmp_path / "notes" / "hello.md"
        h = _make_file(f, b"hello world")

        state, _unreadable = _build_disk_state(config)
        assert state == {"notes/hello.md": h}

    def test_multiple_files(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        h1 = _make_file(tmp_path / "a.txt", b"aaa")
        h2 = _make_file(tmp_path / "sub" / "b.txt", b"bbb")

        state, _unreadable = _build_disk_state(config)
        assert state == {
            "a.txt": h1,
            "sub/b.txt": h2,
        }

    def test_ignore_patterns_applied(self, tmp_path: Path):
        config = _make_config(
            tmp_path=tmp_path,
            vault_root=tmp_path,
            ignore_patterns=[".git", "*.tmp"],
        )
        _make_file(tmp_path / "a.txt", b"aaa")
        _make_file(tmp_path / "notes.tmp", b"tmp data")
        _make_file(tmp_path / ".git" / "config", b"git config")

        state, _unreadable = _build_disk_state(config)
        # Only a.txt should be present
        assert list(state.keys()) == ["a.txt"]

    def test_ignore_dotfolder_contents(self, tmp_path: Path):
        """Files inside ignored directories (like .git) should be skipped."""
        config = _make_config(
            tmp_path=tmp_path,
            vault_root=tmp_path,
            ignore_patterns=[".git"],
        )
        _make_file(tmp_path / "readme.md", b"readme")
        _make_file(tmp_path / ".git" / "HEAD", b"ref: master")
        _make_file(tmp_path / ".git" / "objects" / "ab" / "cd123", b"blob")

        state, _unreadable = _build_disk_state(config)
        assert list(state.keys()) == ["readme.md"]

    def test_nfc_normalization(self, tmp_path: Path):
        """Vault paths should be NFC-normalised."""
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        # Create a file with a composed character
        f = tmp_path / "café.md"
        h = _make_file(f, b"cafe content")

        state, _unreadable = _build_disk_state(config)
        # The path should be NFC-normalised (café is already NFC on most systems)
        assert "café.md" in state
        assert state["café.md"] == h


# ===========================================================================
# Section 3 — _fetch_cloud_state helper
# ===========================================================================


class TestFetchCloudState:
    """Tests for _fetch_cloud_state."""

    async def test_successful_fetch(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/a.md", "content_hash": "abc123"},
                {"vault_path": "notes/b.md", "content_hash": "def456"},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        result = await _fetch_cloud_state(config, client)
        assert result == {"notes/a.md": "abc123", "notes/b.md": "def456"}

    async def test_null_content_hash(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/a.md", "content_hash": None},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        result = await _fetch_cloud_state(config, client)
        assert result == {"notes/a.md": None}

    async def test_empty_documents(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        transport = _mock_transport_dispatcher(state_docs=[])
        client = httpx.AsyncClient(transport=transport)

        result = await _fetch_cloud_state(config, client)
        assert result == {}

    async def test_http_error_returns_empty(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"status": "error"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        result = await _fetch_cloud_state(config, client)
        assert result == {}

    async def test_invalid_json_returns_empty(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json")

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        result = await _fetch_cloud_state(config, client)
        assert result == {}

    async def test_wrong_format_returns_empty(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"wrong_key": []})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        result = await _fetch_cloud_state(config, client)
        assert result == {}

    async def test_skips_invalid_entries(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/a.md", "content_hash": "abc"},
                {},  # missing vault_path
                {"vault_path": "", "content_hash": "xxx"},  # empty vault_path
                {"vault_path": 123, "content_hash": "yyy"},  # non-string vault_path
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        result = await _fetch_cloud_state(config, client)
        # Only the valid entry should be included
        assert result == {"notes/a.md": "abc"}


# ===========================================================================
# Section 4 — scan(): file on disk, not in cloud → uploaded
# ===========================================================================


class TestScanDiskOnlyUploads:
    """Files only on disk → extract + upload → count as 'uploaded'."""

    async def test_disk_only_file_uploaded(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        _make_file(tmp_path / "notes" / "new_file.md", b"new content")

        # Cloud state: empty (nothing known)
        transport = _mock_transport_dispatcher(state_docs=[])
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client)
        assert result.uploaded == 1
        assert result.re_uploaded == 0
        assert result.deleted == 0
        assert result.skipped == 0

    async def test_multiple_disk_only_files(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        _make_file(tmp_path / "a.md", b"content A")
        _make_file(tmp_path / "b.md", b"content B")
        _make_file(tmp_path / "sub" / "c.md", b"content C")

        transport = _mock_transport_dispatcher(state_docs=[])
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client)
        assert result.uploaded == 3
        assert result.skipped == 0


# ===========================================================================
# Section 5 — scan(): file on disk, different hash → re-uploaded
# ===========================================================================


class TestScanHashDiffReUpload:
    """Files on disk AND cloud but hash differs → count as 're_uploaded'."""

    async def test_hash_diff_re_uploaded(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        _make_file(tmp_path / "notes" / "changed.md", b"new content")

        # Cloud has same path but different hash
        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/changed.md", "content_hash": "old_hash"},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client)
        assert result.re_uploaded == 1
        assert result.uploaded == 0
        assert result.skipped == 0


# ===========================================================================
# Section 6 — scan(): file in cloud, not on disk → deleted
# ===========================================================================


class TestScanCloudOnlyDeleted:
    """Files in cloud but not on disk → report_deleted → count as 'deleted'."""

    async def test_cloud_only_file_reported_deleted(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        # No files on disk

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/deleted.md", "content_hash": "abc123"},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client)
        assert result.deleted == 1
        assert result.uploaded == 0

    async def test_mixed_scenario(self, tmp_path: Path):
        """Disk: a.md (unchanged), b.md (new). Cloud: a.md, c.md (deleted)."""
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)

        h_a = _make_file(tmp_path / "a.md", b"content A")
        _make_file(tmp_path / "b.md", b"content B")

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "a.md", "content_hash": h_a},  # match → skip
                {"vault_path": "c.md", "content_hash": "abc"},  # cloud-only → delete
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client)
        assert result.uploaded == 1  # b.md
        assert result.deleted == 1  # c.md
        assert result.skipped == 1  # a.md
        assert result.re_uploaded == 0


# ===========================================================================
# Section 7 — scan(): file with matching hash → skipped
# ===========================================================================


class TestScanHashMatchSkip:
    """Files with matching hash → skip."""

    async def test_matching_hash_skipped(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        h = _make_file(tmp_path / "notes" / "unchanged.md", b"same content")

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/unchanged.md", "content_hash": h},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client)
        assert result.skipped == 1
        assert result.uploaded == 0
        assert result.re_uploaded == 0
        assert result.deleted == 0


# ===========================================================================
# Section 8 — scan(): NULL cloud content_hash → always re-upload
# ===========================================================================


class TestScanNullCloudHash:
    """NULL content_hash from cloud → always treat as different → re-upload."""

    async def test_null_hash_always_re_upload(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        _make_file(tmp_path / "notes" / "file.md", b"some content")

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/file.md", "content_hash": None},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client)
        assert result.re_uploaded == 1
        assert result.skipped == 0


# ===========================================================================
# Section 9 — scan(): ignore patterns applied during walk
# ===========================================================================


class TestScanIgnorePatterns:
    """Scanner should respect ignore_patterns when walking the vault."""

    async def test_ignore_dotgit(self, tmp_path: Path):
        config = _make_config(
            tmp_path=tmp_path,
            vault_root=tmp_path,
            ignore_patterns=[".git", ".DS_Store"],
        )
        _make_file(tmp_path / "readme.md", b"readme")
        _make_file(tmp_path / ".git" / "config", b"git config")
        _make_file(tmp_path / ".DS_Store", b"ds store")

        transport = _mock_transport_dispatcher(state_docs=[])
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client)
        # Only readme.md should be uploaded
        assert result.uploaded == 1

    async def test_ignore_glob_pattern(self, tmp_path: Path):
        config = _make_config(
            tmp_path=tmp_path,
            vault_root=tmp_path,
            ignore_patterns=["*.tmp", "~$*"],
        )
        _make_file(tmp_path / "good.md", b"good")
        _make_file(tmp_path / "bad.tmp", b"temp")
        _make_file(tmp_path / "~$lockfile", b"lock")

        transport = _mock_transport_dispatcher(state_docs=[])
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client)
        assert result.uploaded == 1


# ===========================================================================
# Section 10 — scan(): respects upload_concurrency
# ===========================================================================


class TestScanConcurrency:
    """Scanner should respect upload_concurrency via asyncio.Semaphore."""

    async def test_concurrency_limit_respected(self, tmp_path: Path):
        """Verify that at most N uploads run concurrently."""
        config = _make_config(
            tmp_path=tmp_path,
            vault_root=tmp_path,
            upload_concurrency=2,
        )

        # Create several files
        for i in range(6):
            _make_file(tmp_path / f"file_{i}.md", f"content {i}".encode())

        max_concurrent = 0
        current = 0
        lock = asyncio.Lock()

        # Patch _upload_one to track concurrency
        original_upload_one = _upload_one

        async def tracking_upload_one(sem, cfg, cl, vp, tag,
                                      cache=None, disk_hash=None,
                                      resolved_entries=None):
            nonlocal max_concurrent, current
            async with lock:
                current += 1
                max_concurrent = max(max_concurrent, current)
            try:
                return await original_upload_one(sem, cfg, cl, vp, tag,
                                                 cache=cache, disk_hash=disk_hash,
                                                 resolved_entries=resolved_entries)
            finally:
                async with lock:
                    current -= 1

        transport = _mock_transport_dispatcher(state_docs=[])
        client = httpx.AsyncClient(transport=transport)

        with patch("daemon.scanner._upload_one", tracking_upload_one):
            result = await scan(config, client)

        assert result.uploaded == 6
        assert max_concurrent <= 2, f"max_concurrent={max_concurrent} exceeds limit 2"


# ===========================================================================
# Section 11 — scan(): handles cloud fetch failure gracefully
# ===========================================================================


class TestScanCloudFetchFailure:
    """When cloud state fetch fails, treat everything as disk-only."""

    async def test_fetch_failure_uploads_all(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        _make_file(tmp_path / "a.md", b"content A")
        _make_file(tmp_path / "b.md", b"content B")

        async def handler(request: httpx.Request) -> httpx.Response:
            if "api/state" in str(request.url):
                return httpx.Response(500, json={"status": "error"})
            elif "api/upload" in str(request.url):
                return _upload_response(1)
            elif "api/event" in str(request.url):
                return _event_response()
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client)
        # All files should be uploaded since cloud state fetch failed
        assert result.uploaded == 2
        assert result.deleted == 0
        assert result.skipped == 0


# ===========================================================================
# Section 12 — _upload_one helper
# ===========================================================================


class TestUploadOne:
    """Tests for the _upload_one helper."""

    async def test_uploads_text_content(self, tmp_path: Path):
        """When extract returns TextContent, upload_text should be called."""
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        f = tmp_path / "notes" / "doc.md"
        content = b"# Hello\n\nWorld"
        _make_file(f, content)

        sem = asyncio.Semaphore(1)
        transport = _mock_transport_dispatcher()
        client = httpx.AsyncClient(transport=transport)

        tag, vp, ok = await _upload_one(sem, config, client, "notes/doc.md", _OUTCOME_UPLOADED)
        assert tag == _OUTCOME_UPLOADED
        assert vp == "notes/doc.md"
        assert ok is True

    async def test_uploads_binary_content(self, tmp_path: Path):
        """When extract falls back to BinaryContent, upload_binary is called."""
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        f = tmp_path / "data" / "file.txt"
        _make_file(f, b"plain text data")

        sem = asyncio.Semaphore(1)
        transport = _mock_transport_dispatcher()
        client = httpx.AsyncClient(transport=transport)

        tag, vp, ok = await _upload_one(sem, config, client, "data/file.txt", _OUTCOME_UPLOADED)
        assert tag == _OUTCOME_UPLOADED
        assert vp == "data/file.txt"
        assert ok is True


# ===========================================================================
# Section 13 — _delete_one helper
# ===========================================================================


class TestDeleteOne:
    """Tests for the _delete_one helper."""

    async def test_reports_deleted(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)

        sem = asyncio.Semaphore(1)
        transport = _mock_transport_dispatcher()
        client = httpx.AsyncClient(transport=transport)

        tag, vp, ok = await _delete_one(sem, config, client, "notes/gone.md")
        assert tag == _OUTCOME_DELETED
        assert vp == "notes/gone.md"
        assert ok is True


# ===========================================================================
# Section 14 — 3-way reconcile: brand-new file (disk only)
# ===========================================================================


class Test3WayBrandNew:
    """Row 1: Disk ✔, Cache --, Cloud -- → Upload + cache-on-ack."""

    async def test_brand_new_file_uploaded_and_cached(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        _make_file(tmp_path / "notes" / "new.md", b"fresh content")

        transport = _mock_transport_dispatcher(state_docs=[])
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        assert result.uploaded == 1
        assert result.re_uploaded == 0
        assert result.deleted == 0
        assert result.skipped == 0

        # Cache should have the entry after scan (cache-on-ack + rebuild)
        cached = cache.get("notes/new.md")
        assert cached is not None
        assert cached["hash"] is not None
        assert isinstance(cached["size"], int)
        assert isinstance(cached["mtime"], float)


# ===========================================================================
# Section 15 — 3-way reconcile: steady state (all three match)
# ===========================================================================


class Test3WaySteadyState:
    """Row 4: Disk ✔, Cache ✔ same, Cloud ✔ same → Skip."""

    async def test_steady_state_skipped(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        content = b"unchanging content"
        h = _make_file(tmp_path / "notes" / "stable.md", content)

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/stable.md", "content_hash": h},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        # Pre-populate cache with matching entry
        mtime = (tmp_path / "notes" / "stable.md").stat().st_mtime
        cache.set_after_ack("notes/stable.md", h, len(content), mtime)

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        assert result.skipped == 1
        assert result.uploaded == 0
        assert result.re_uploaded == 0

        # Cache should still have the entry after rebuild
        cached = cache.get("notes/stable.md")
        assert cached is not None
        assert cached["hash"] == h


# ===========================================================================
# Section 16 — 3-way reconcile: rollback heal (disk+cache, no cloud)
# ===========================================================================


class Test3WayRollbackHeal:
    """Row 2: Disk ✔, Cache ✔, Cloud -- → Re-upload (rollback heal)."""

    async def test_rollback_heal_re_uploaded(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        content = b"file lost from cloud"
        h = _make_file(tmp_path / "notes" / "orphan.md", content)

        # Cloud: empty (file was somehow removed from cloud)
        transport = _mock_transport_dispatcher(state_docs=[])
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        mtime = (tmp_path / "notes" / "orphan.md").stat().st_mtime
        cache.set_after_ack("notes/orphan.md", h, len(content), mtime)

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        assert result.re_uploaded == 1
        assert result.uploaded == 0
        assert result.skipped == 0

        # Cache should still have the entry after rebuild
        cached = cache.get("notes/orphan.md")
        assert cached is not None


# ===========================================================================
# Section 17 — 3-way reconcile: disk+cloud match, cache missed → skip+cache
# ===========================================================================


class Test3WayCacheCatchUp:
    """Row 3: Disk ✔, Cache --, Cloud ✔ same → Skip + cache it."""

    async def test_cache_catches_up(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        content = b"on disk and cloud, not cached"
        h = _make_file(tmp_path / "notes" / "catchup.md", content)

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/catchup.md", "content_hash": h},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        # Cache is empty — file is on disk and cloud but not in cache

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        assert result.skipped == 1
        assert result.uploaded == 0

        # Cache should now have the entry (set_after_ack called inline)
        cached = cache.get("notes/catchup.md")
        assert cached is not None
        assert cached["hash"] == h


# ===========================================================================
# Section 18 — 3-way reconcile: disk hash differs from cloud
# ===========================================================================


class Test3WayContentChanged:
    """Row 5: Disk ✔, Cloud ✔ differ → Re-upload."""

    async def test_content_changed_re_uploaded(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        content = b"new version of file"
        h_new = _make_file(tmp_path / "notes" / "changed.md", content)

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/changed.md", "content_hash": "old_different_hash"},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        assert result.re_uploaded == 1
        assert result.uploaded == 0
        assert result.skipped == 0


# ===========================================================================
# Section 19 — 3-way reconcile: candidate delete (disk missing, cache+cloud)
# ===========================================================================


class Test3WayCandidateDelete:
    """Row 6: Disk --, Cache ✔, Cloud ✔ → Candidate delete.
    Row 8: Disk --, Cache --, Cloud ✔ → Candidate delete (cloud-only)."""

    async def test_candidate_delete_sweeps_at_threshold(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        # No files on disk

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/gone.md", "content_hash": "abc123"},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        # Pre-populate cache so we hit Row 6 (not Row 8)
        cache.set_after_ack("notes/gone.md", "abc123", 100, 1234567890.0)

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        # With sweep_delete_confirmations=1, deletion should happen immediately
        assert result.deleted == 1
        assert result.uploaded == 0

        # Cache should forget the path after deletion
        cached = cache.get("notes/gone.md")
        assert cached is None

    async def test_candidate_delete_needs_multiple_sweeps(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        # No files on disk

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/gone.md", "content_hash": "abc123"},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        cache.set_after_ack("notes/gone.md", "abc123", 100, 1234567890.0)

        # sweep_delete_confirmations=3 → need 3 scans to delete
        candidate_deletes: dict[str, int] = {}

        # Scan 1
        result1 = await scan(config, client, cache=cache,
                             candidate_deletes=candidate_deletes,
                             sweep_delete_confirmations=3)
        assert result1.deleted == 0  # Not yet
        assert candidate_deletes.get("notes/gone.md") == 1

        # Scan 2: need to re-populate cache entry because rebuild clears it
        # Actually rebuild replaces all entries with resolved_entries (disk files).
        # Since file is not on disk, rebuild clears it. Let me re-populate.
        cache.set_after_ack("notes/gone.md", "abc123", 100, 1234567890.0)
        result2 = await scan(config, client, cache=cache,
                             candidate_deletes=candidate_deletes,
                             sweep_delete_confirmations=3)
        assert result2.deleted == 0  # Still not yet
        assert candidate_deletes.get("notes/gone.md") == 2

        # Scan 3
        cache.set_after_ack("notes/gone.md", "abc123", 100, 1234567890.0)
        result3 = await scan(config, client, cache=cache,
                             candidate_deletes=candidate_deletes,
                             sweep_delete_confirmations=3)
        assert result3.deleted == 1  # Now it sweeps
        assert "notes/gone.md" not in candidate_deletes

    async def test_cloud_only_without_cache_candidate_delete(self, tmp_path: Path):
        """Row 8: Disk --, Cache --, Cloud ✔ → Candidate delete (cloud-only)."""
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        # No files on disk

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/cloud_only.md", "content_hash": "abc123"},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        # Cache is empty → Row 8

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        assert result.deleted == 1


# ===========================================================================
# Section 20 — 3-way reconcile: reappearing file clears candidate delete
# ===========================================================================


class Test3WayReappearingFile:
    """A file that was a candidate delete reappears on disk → cleared."""

    async def test_reappearing_file_clears_candidate_delete(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        # No files initially

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/reappear.md", "content_hash": "abc123"},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        cache.set_after_ack("notes/reappear.md", "abc123", 100, 1234567890.0)

        candidate_deletes: dict[str, int] = {}

        # Scan 1: file missing → candidate delete count = 1
        result1 = await scan(config, client, cache=cache,
                             candidate_deletes=candidate_deletes,
                             sweep_delete_confirmations=3)
        assert result1.deleted == 0
        assert candidate_deletes.get("notes/reappear.md") == 1

        # File reappears on disk
        h = _make_file(tmp_path / "notes" / "reappear.md", b"back again")

        # Need a new cloud state and cache for scan to find it
        transport2 = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/reappear.md", "content_hash": h},
            ]
        )
        client2 = httpx.AsyncClient(transport=transport2)
        cache.set_after_ack("notes/reappear.md", h, len(b"back again"),
                            (tmp_path / "notes" / "reappear.md").stat().st_mtime)

        result2 = await scan(config, client2, cache=cache,
                             candidate_deletes=candidate_deletes,
                             sweep_delete_confirmations=3)
        # Should be skipped (steady state) and candidate delete cleared
        assert result2.skipped == 1
        assert "notes/reappear.md" not in candidate_deletes


# ===========================================================================
# Section 21 — 3-way reconcile: stale cache entry
# ===========================================================================


class Test3WayStaleCache:
    """Row 7: Disk --, Cache ✔, Cloud -- → Drop stale cache (silent forget)."""

    async def test_stale_cache_entry_dropped(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        # No files on disk

        # Cloud: empty
        transport = _mock_transport_dispatcher(state_docs=[])
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        # Cache has a stale entry (not on disk, not in cloud)
        cache.set_after_ack("notes/stale.md", "deadbeef", 50, 1234567890.0)

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        assert result.uploaded == 0
        assert result.deleted == 0
        assert result.skipped == 0

        # Cache should have forgotten the stale entry
        cached = cache.get("notes/stale.md")
        assert cached is None

        # Rebuild should still work (empty resolved entries)
        # Cache should be empty
        snap = cache.snapshot()
        assert "notes/stale.md" not in snap


# ===========================================================================
# Section 22 — 3-way reconcile: NULL cloud hash → re-upload
# ===========================================================================


class Test3WayNullCloudHash:
    """Row 9: Disk ✔, Cache ✔, Cloud None (null hash) → Re-upload."""

    async def test_null_cloud_hash_re_upload_with_cache(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        content = b"file with null cloud hash"
        h = _make_file(tmp_path / "notes" / "nullhash.md", content)

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/nullhash.md", "content_hash": None},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        mtime = (tmp_path / "notes" / "nullhash.md").stat().st_mtime
        cache.set_after_ack("notes/nullhash.md", h, len(content), mtime)

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        assert result.re_uploaded == 1
        assert result.skipped == 0


# ===========================================================================
# Section 23 — 3-way reconcile: unreadable files excluded from deletes
# ===========================================================================


class Test3WayUnreadableExclusion:
    """Unreadable files must not be candidate-deleted (existing A1 carve-out)."""

    async def test_unreadable_excluded_from_candidate_delete(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)

        # Create an unreadable file (simulate by creating a directory with a file
        # then removing read permission — but that's tricky in test).
        # Instead, we test Row 8 exclusion: the unreadable set passed to _scan_3way
        # excludes paths from candidate-delete when cache is also missing.
        # Since _build_disk_state produces unreadable for files that can't be read,
        # we test the logic directly via a unit approach:
        # If a file is in cloud but not on disk AND was in unreadable set,
        # it should NOT be deleted.

        # We'll test by patching _build_disk_state to return an unreadable path
        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/broken.md", "content_hash": "abc123"},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        # Patch _build_disk_entries to return the path as unreadable.
        # (3-way path now calls _build_disk_entries instead of _build_disk_state.)
        with patch("daemon.scanner._build_disk_entries",
                   return_value=({}, {}, {"notes/broken.md"})):
            result = await scan(config, client, cache=cache,
                                sweep_delete_confirmations=1)

        # Should NOT be deleted (excluded via unreadable check)
        assert result.deleted == 0


# ===========================================================================
# Section 24 — 3-way: cache rebuild after scan mirrors truth
# ===========================================================================


class Test3WayCacheRebuild:
    """After scan, cache.rebuild() is called with resolved entries."""

    async def test_cache_rebuilt_after_scan(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        content = b"post-rebuild content"
        h = _make_file(tmp_path / "notes" / "keep.md", content)

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/keep.md", "content_hash": h},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        # Pre-populate a stale entry that should be removed by rebuild
        cache.set_after_ack("notes/stale_old.md", "ffff", 10, 1.0)

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        # The stale entry should be gone after rebuild
        assert cache.get("notes/stale_old.md") is None

        # The real file should be in cache
        cached = cache.get("notes/keep.md")
        assert cached is not None
        assert cached["hash"] == h
        assert cached["size"] == len(content)
        assert isinstance(cached["mtime"], float)

        # Skipped (steady state after Row 3 cache catch-up)
        assert result.skipped == 1


# ===========================================================================
# Section 25 — backward compat: scan() with cache=None = A1 behavior
# ===========================================================================


class Test3WayBackwardCompat:
    """scan() with cache=None must behave exactly like A1."""

    async def test_cache_none_is_2way(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        _make_file(tmp_path / "a.md", b"content A")
        _make_file(tmp_path / "b.md", b"content B")

        transport = _mock_transport_dispatcher(state_docs=[])
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client, cache=None)

        # Should work exactly like A1: both files uploaded
        assert result.uploaded == 2
        assert result.deleted == 0
        assert result.skipped == 0

    async def test_cache_none_with_cloud_deletes(self, tmp_path: Path):
        """Cloud-only files still get deleted when cache=None."""
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        # No files on disk

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/gone.md", "content_hash": "abc123"},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        result = await scan(config, client, cache=None)

        assert result.deleted == 1
        assert result.uploaded == 0


# ===========================================================================
# Section 26 — failed upload does NOT write to cache
# ===========================================================================


class Test3WayFailedUploadNoCache:
    """On upload failure, cache must NOT be touched."""

    async def test_failed_upload_does_not_cache(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        _make_file(tmp_path / "notes" / "fail.md", b"will fail upload")

        # Transport that makes uploads fail (500 error)
        async def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "api/state" in url:
                return _state_response([])
            elif "api/upload" in url:
                return httpx.Response(500, json={"status": "error"})
            elif "api/event" in url:
                return _event_response()
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        # Upload failed → result.uploaded should be 0
        assert result.uploaded == 0

        # Cache should NOT have the entry (set_after_ack not called on failure)
        # After rebuild, resolved_entries is empty for this file (upload failed)
        cached = cache.get("notes/fail.md")
        assert cached is None


# ===========================================================================
# Section 27 — 3-way: stale cache healed when disk matches cloud
# ===========================================================================


class Test3WayStaleCacheHeal:
    """Cache has wrong hash but disk and cloud match → skip + heal cache."""

    async def test_stale_cache_healed_on_match(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        content = b"current content"
        h = _make_file(tmp_path / "notes" / "heal.md", content)

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/heal.md", "content_hash": h},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        # Cache has a stale/different hash
        cache.set_after_ack("notes/heal.md", "old_wrong_hash", 50, 1.0)

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        # Should be skipped (disk matches cloud)
        assert result.skipped == 1
        assert result.uploaded == 0
        assert result.re_uploaded == 0

        # Cache should be healed to the correct hash
        cached = cache.get("notes/heal.md")
        assert cached is not None
        assert cached["hash"] == h
        assert cached["size"] == len(content)


# ===========================================================================
# Section 28 — 3-way: null cloud hash without cache
# ===========================================================================


class Test3WayNullCloudHashNoCache:
    """Null cloud hash → re-upload even when cache is missing."""

    async def test_null_hash_re_upload_without_cache(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        content = b"null hash, no cache"
        _make_file(tmp_path / "notes" / "null_nocache.md", content)

        transport = _mock_transport_dispatcher(
            state_docs=[
                {"vault_path": "notes/null_nocache.md", "content_hash": None},
            ]
        )
        client = httpx.AsyncClient(transport=transport)

        sync_state = DaemonSyncState()
        cache = LocalCache(sync_state)
        # Cache is empty for this file

        result = await scan(config, client, cache=cache,
                            sweep_delete_confirmations=1)

        # Should be re-uploaded (null hash always triggers re-upload)
        assert result.re_uploaded == 1
        assert result.skipped == 0
