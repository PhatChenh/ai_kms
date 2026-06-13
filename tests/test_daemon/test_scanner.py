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

    def test_explicit_values(self):
        sr = ScanResult(uploaded=1, re_uploaded=2, deleted=3, skipped=4)
        assert sr.uploaded == 1
        assert sr.re_uploaded == 2
        assert sr.deleted == 3
        assert sr.skipped == 4

    def test_mutable(self):
        sr = ScanResult()
        sr.uploaded += 1
        assert sr.uploaded == 1


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

        async def tracking_upload_one(sem, cfg, cl, vp, tag):
            nonlocal max_concurrent, current
            async with lock:
                current += 1
                max_concurrent = max(max_concurrent, current)
            try:
                return await original_upload_one(sem, cfg, cl, vp, tag)
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

        tag, ok = await _upload_one(sem, config, client, "notes/doc.md", _OUTCOME_UPLOADED)
        assert tag == _OUTCOME_UPLOADED
        assert ok is True

    async def test_uploads_binary_content(self, tmp_path: Path):
        """When extract falls back to BinaryContent, upload_binary is called."""
        config = _make_config(tmp_path=tmp_path, vault_root=tmp_path)
        f = tmp_path / "data" / "file.txt"
        _make_file(f, b"plain text data")

        sem = asyncio.Semaphore(1)
        transport = _mock_transport_dispatcher()
        client = httpx.AsyncClient(transport=transport)

        tag, ok = await _upload_one(sem, config, client, "data/file.txt", _OUTCOME_UPLOADED)
        assert tag == _OUTCOME_UPLOADED
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

        tag, ok = await _delete_one(sem, config, client, "notes/gone.md")
        assert tag == _OUTCOME_DELETED
        assert ok is True
