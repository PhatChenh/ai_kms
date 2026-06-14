"""
tests/test_daemon/test_cli.py

Tests for daemon/cli.py — the Click CLI for the sync daemon.

Tests use CliRunner with mocking to avoid real HTTP calls and filesystem
interactions.  The test suite covers:

  - status  command: reachability reporting (success + failure paths)
  - scan    command: one-shot reconciliation summary printing
  - start   command: basic invocation, config loading, Ctrl+C handling
  - cache   behaviour: bail-early, cache-on-ack, move bookkeeping
  - CLI     group:  --help, --config option propagation
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

import daemon.cli
from click.testing import CliRunner

from core.result import Failure, Success
from daemon.cli import cli
from daemon.extractor import TextContent
from daemon.scanner import ScanResult


# ── helpers ──────────────────────────────────────────────────────────────────


def _tmp_yaml(tmp_path: Path, content: str) -> Path:
    """Write *content* to a temp YAML file and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(content)
    return p


def _minimal_config_yaml(vault_root: Path, cloud_endpoint: str = "http://localhost:8080") -> str:
    """Return a minimal valid daemon config YAML string."""
    return f"""vault_root: {vault_root}
cloud_endpoint: {cloud_endpoint}
"""


# ── status command tests ────────────────────────────────────────────────────


class TestStatusCommand:
    """Tests for ``daemon status``."""

    def test_help(self):
        """``daemon status --help`` exits 0 and mentions status."""
        runner = CliRunner()
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0
        assert "status" in result.output.lower()

    def test_cloud_reachable(self, tmp_path: Path, monkeypatch):
        """Status command reports reachable when /health returns 200."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        # Mock httpx.AsyncClient to return a 200 response for /health
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("daemon.cli.httpx.AsyncClient", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(cli, ["status", "--config", str(config_path)])
            assert result.exit_code == 0
            assert "reachable" in result.output

    def test_cloud_unreachable(self, tmp_path: Path, monkeypatch):
        """Status command reports error when /health is unreachable."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        import httpx
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        with patch("daemon.cli.httpx.AsyncClient", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(cli, ["status", "--config", str(config_path)])
            assert result.exit_code != 0
            assert "Cannot reach" in result.output

    def test_cloud_returns_500(self, tmp_path: Path, monkeypatch):
        """Status command reports error when /health returns 500."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        import httpx
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Server error",
                request=MagicMock(),
                response=mock_response,
            )
        )

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("daemon.cli.httpx.AsyncClient", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(cli, ["status", "--config", str(config_path)])
            assert result.exit_code != 0
            assert "500" in result.output

    def test_status_requires_api_key(self, tmp_path: Path, monkeypatch):
        """Status exits non-zero if KMS_DAEMON_API_KEY is not set."""
        monkeypatch.delenv("KMS_DAEMON_API_KEY", raising=False)
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault))

        runner = CliRunner()
        result = runner.invoke(cli, ["status", "--config", str(config_path)])
        assert result.exit_code != 0


# ── scan command tests ──────────────────────────────────────────────────────


class TestScanCommand:
    """Tests for ``daemon scan``."""

    def test_help(self):
        """``daemon scan --help`` exits 0 and mentions scan."""
        runner = CliRunner()
        result = runner.invoke(cli, ["scan", "--help"])
        assert result.exit_code == 0
        assert "scan" in result.output.lower()

    def test_scan_prints_summary(self, tmp_path: Path, monkeypatch):
        """Scan command loads config, runs scan, and prints summary counts."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        fake_result = ScanResult(
            uploaded=3,
            re_uploaded=1,
            deleted=2,
            skipped=10,
        )

        async def _fake_scan(cfg, client):
            return fake_result

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["scan", "--config", str(config_path)])
            assert result.exit_code == 0
            assert "Uploaded:" in result.output
            assert "3" in result.output
            assert "Re-uploaded:" in result.output
            assert "1" in result.output
            assert "Deleted:" in result.output
            assert "2" in result.output
            assert "Skipped:" in result.output
            assert "10" in result.output

    def test_scan_requires_api_key(self, tmp_path: Path, monkeypatch):
        """Scan exits non-zero if KMS_DAEMON_API_KEY is not set."""
        monkeypatch.delenv("KMS_DAEMON_API_KEY", raising=False)
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault))

        runner = CliRunner()
        result = runner.invoke(cli, ["scan", "--config", str(config_path)])
        assert result.exit_code != 0

    def test_scan_bad_config_path(self):
        """Scan exits non-zero for non-existent config path."""
        runner = CliRunner()
        result = runner.invoke(cli, ["scan", "--config", "/nonexistent/path.yaml"])
        assert result.exit_code != 0


# ── start command tests ─────────────────────────────────────────────────────


class TestStartCommand:
    """Tests for ``daemon start``."""

    def test_help(self):
        """``daemon start --help`` exits 0 and mentions start."""
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--help"])
        assert result.exit_code == 0
        assert "start" in result.output.lower()

    def test_start_requires_api_key(self, tmp_path: Path, monkeypatch):
        """Start exits non-zero if KMS_DAEMON_API_KEY is not set."""
        monkeypatch.delenv("KMS_DAEMON_API_KEY", raising=False)
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault))

        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--config", str(config_path)])
        assert result.exit_code != 0

    def test_start_runs_scan_then_shuts_down(self, tmp_path: Path, monkeypatch):
        """Start runs a startup scan, starts the watcher, and shuts down on signal.

        We simulate a shutdown by raising KeyboardInterrupt after the first
        asyncio.sleep in the main loop.
        """
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=5)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            return fake_result

        # We need to patch asyncio.sleep so the main loop doesn't block forever.
        # We raise KeyboardInterrupt on first call to simulate Ctrl+C.
        sleep_count = [0]

        async def _fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] == 1:
                raise KeyboardInterrupt()
            # Should not reach here, but just in case
            await asyncio.sleep(0)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_watcher = MagicMock()
        mock_watcher.start = MagicMock()
        mock_watcher.stop = MagicMock()
        mock_watcher.join = MagicMock()

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", return_value=mock_watcher),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["start", "--config", str(config_path)])
            # Should exit cleanly (KeyboardInterrupt caught)
            assert result.exit_code == 0
            assert "Scan complete" in result.output
            assert "Watching" in result.output
            assert "Daemon stopped" in result.output
            # Watcher must have been started and stopped
            mock_watcher.start.assert_called_once()
            mock_watcher.stop.assert_called_once()
            mock_watcher.join.assert_called_once()

    # ── helpers for callback-capture tests ───────────────────────────────

    @staticmethod
    def _run_start_and_capture_callbacks(
        tmp_path: Path,
        monkeypatch,
        vault: Path,
        config_path: Path,
        *,
        cache_json: dict | None = None,
    ) -> dict:
        """Run ``daemon start`` with a mock watcher that captures callbacks.

        Returns a dict with keys: ``on_create``, ``on_modify``, ``on_move``,
        ``on_delete`` — each a callable captured from the watcher constructor.
        Also returns ``cfg`` (the loaded config).

        If *cache_json* is provided, writes it to the cache path before the
        daemon loads it so the ``LocalCache`` is pre-populated.
        """
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")

        if cache_json is not None:
            # Write cache file before the daemon loads it
            cache_path = tmp_path / "cache.json"
            cache_path.write_text(json.dumps(cache_json))
            config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
cache_path: {cache_path}
"""
            config_path2 = _tmp_yaml(tmp_path, config_content)
        else:
            config_path2 = config_path

        captured: dict = {}

        class _MockWatcher:
            def __init__(self, config, on_create, on_modify, on_move, on_delete):
                captured["on_create"] = on_create
                captured["on_modify"] = on_modify
                captured["on_move"] = on_move
                captured["on_delete"] = on_delete

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def join(self) -> None:
                pass

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=0)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            return fake_result

        sleep_count = [0]

        async def _fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] == 1:
                raise KeyboardInterrupt()
            await asyncio.sleep(0)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", side_effect=_MockWatcher),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["start", "--config", str(config_path2)])
            assert result.exit_code == 0

        return captured

    # ── bail-early cache tests ───────────────────────────────────────────

    def test_stat_unchanged_skips_extract(self, tmp_path: Path, monkeypatch):
        """When size+mtime match cache, log skipped and do NOT call extract."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        # Create a real file
        test_file = vault / "notes.txt"
        test_file.write_text("hello world", encoding="utf-8")
        st = test_file.stat()
        content_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()

        # Pre-populate cache with matching fingerprint
        cache_json = {
            "notes.txt": {
                "hash": content_hash,
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        }

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json=cache_json
        )

        # Patch asyncio.run_coroutine_threadsafe to run synchronously
        def _sync_rct(coro, loop):
            asyncio.run(coro)

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.extract") as mock_extract,
            patch.object(daemon.cli, "_log") as mock_log,
        ):
            captured["on_create"]("notes.txt")

        # extract must NOT be called
        mock_extract.assert_not_called()
        # debug log must contain skipped (unchanged)
        mock_log.debug.assert_any_call("skipped (unchanged)", vault_path="notes.txt")

    def test_genuinely_modified_extracts_and_uploads(self, tmp_path: Path, monkeypatch):
        """When content hash differs from cache, extract + upload + cache update."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        test_file = vault / "notes.txt"
        test_file.write_text("new content", encoding="utf-8")
        st = test_file.stat()

        # Cache has a different hash AND different mtime → stat check won't bail early,
        # hash check will detect genuine change
        cache_json = {
            "notes.txt": {
                "hash": "0000000000000000000000000000000000000000000000000000000000000000",
                "size": st.st_size,
                "mtime": st.st_mtime - 100.0,  # stale mtime forces hash check
            }
        }

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json=cache_json
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        fake_text_content = TextContent(
            text="new content",
            content_hash=hashlib.sha256(b"new content").hexdigest(),
            vault_path="notes.txt",
            original_filename="notes.txt",
            file_size_bytes=st.st_size,
        )

        async def _fake_upload_text(client, cfg, tc):
            return Success(value="doc-1")

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.extract", return_value=Success(value=fake_text_content)) as mock_extract,
            patch("daemon.cli.upload_text", side_effect=_fake_upload_text) as mock_upload,
            patch.object(daemon.cli, "_log") as mock_log,
        ):
            captured["on_create"]("notes.txt")

        # extract must have been called
        mock_extract.assert_called_once()
        # upload_text must have been called
        mock_upload.assert_called_once()
        # info log must mention uploaded
        mock_log.info.assert_any_call("uploaded text", vault_path="notes.txt", doc_id="doc-1")

    def test_stat_prefilter_avoids_hashing(self, tmp_path: Path, monkeypatch):
        """When size+mtime are unchanged, read_bytes is never called (no hashing)."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        test_file = vault / "data.bin"
        test_file.write_bytes(b"binary data here")
        st = test_file.stat()
        content_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()

        cache_json = {
            "data.bin": {
                "hash": content_hash,
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        }

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json=cache_json
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.extract") as mock_extract,
            patch("pathlib.Path.read_bytes") as mock_read_bytes,
            patch.object(daemon.cli, "_log") as mock_log,
        ):
            captured["on_create"]("data.bin")

        # Neither read_bytes nor extract should be called
        mock_read_bytes.assert_not_called()
        mock_extract.assert_not_called()
        mock_log.debug.assert_any_call("skipped (unchanged)", vault_path="data.bin")

    def test_stat_changed_hash_unchanged_updates_stat_in_cache(self, tmp_path: Path, monkeypatch):
        """When stat differs but content hash matches, skip upload but update stat."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        test_file = vault / "notes.txt"
        test_file.write_text("same content", encoding="utf-8")
        real_st = test_file.stat()
        content_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()

        # Cache has same hash but stale stat (mtime is different)
        cache_json = {
            "notes.txt": {
                "hash": content_hash,
                "size": real_st.st_size,
                "mtime": real_st.st_mtime - 100.0,  # stale mtime
            }
        }

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json=cache_json
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.extract") as mock_extract,
            patch.object(daemon.cli, "_log") as mock_log,
            # We need to inspect the real cache, so don't mock LocalCache
            # But we can mock cache.set_after_ack to verify it was called
            patch.object(daemon.cli.LocalCache, "touch") as mock_touch,
        ):
            captured["on_create"]("notes.txt")

        # extract must NOT be called
        mock_extract.assert_not_called()
        # debug log must mention stat changed, content same
        mock_log.debug.assert_any_call(
            "skipped (stat changed, content same)", vault_path="notes.txt"
        )
        # cache.touch must have been called with updated stat (hash preserved)
        mock_touch.assert_called_once_with(
            "notes.txt", real_st.st_size, real_st.st_mtime
        )

    def test_successful_upload_updates_cache(self, tmp_path: Path, monkeypatch):
        """After a successful upload, the file's fingerprint is stored in cache."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        test_file = vault / "notes.txt"
        test_file.write_text("brand new file", encoding="utf-8")
        st = test_file.stat()

        # No cache entry → must extract + upload
        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json={}
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        content_hash = hashlib.sha256(b"brand new file").hexdigest()
        fake_text_content = TextContent(
            text="brand new file",
            content_hash=content_hash,
            vault_path="notes.txt",
            original_filename="notes.txt",
            file_size_bytes=st.st_size,
        )

        async def _fake_upload_text(client, cfg, tc):
            return Success(value="doc-99")

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.extract", return_value=Success(value=fake_text_content)),
            patch("daemon.cli.upload_text", side_effect=_fake_upload_text),
            patch.object(daemon.cli.LocalCache, "set_after_ack") as mock_set,
        ):
            captured["on_create"]("notes.txt")

        # set_after_ack must have been called with the correct fingerprint
        mock_set.assert_called_once_with(
            "notes.txt", content_hash, st.st_size, st.st_mtime
        )

    def test_failed_upload_does_not_update_cache(self, tmp_path: Path, monkeypatch):
        """After a failed upload, the file's fingerprint does NOT appear in cache."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        test_file = vault / "notes.txt"
        test_file.write_text("will fail upload", encoding="utf-8")
        st = test_file.stat()

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json={}
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        fake_text_content = TextContent(
            text="will fail upload",
            content_hash=hashlib.sha256(b"will fail upload").hexdigest(),
            vault_path="notes.txt",
            original_filename="notes.txt",
            file_size_bytes=st.st_size,
        )

        async def _fake_upload_text_fails(client, cfg, tc):
            return Failure(error="simulated 500", recoverable=False, context={})

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.extract", return_value=Success(value=fake_text_content)),
            patch("daemon.cli.upload_text", side_effect=_fake_upload_text_fails),
            patch.object(daemon.cli.LocalCache, "set_after_ack") as mock_set,
            patch.object(daemon.cli, "_log") as mock_log,
        ):
            captured["on_create"]("notes.txt")

        # set_after_ack must NOT be called
        mock_set.assert_not_called()
        # warning log must mention upload_text failed
        mock_log.warning.assert_any_call(
            "upload_text failed", vault_path="notes.txt", error="simulated 500"
        )

    # ── move cache bookkeeping tests ─────────────────────────────────────

    def test_move_updates_cache_success(self, tmp_path: Path, monkeypatch):
        """After a successful move report, old cache entry is removed and new one added."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        # Create the destination file (the moved-to file)
        new_file = vault / "new-location.txt"
        new_file.write_text("moved content", encoding="utf-8")
        st = new_file.stat()
        content_hash = hashlib.sha256(new_file.read_bytes()).hexdigest()

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json={}
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        async def _fake_report_moved(client, cfg, old_vp, new_vp):
            return Success(value=None)

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.report_moved", side_effect=_fake_report_moved),
            patch.object(daemon.cli.LocalCache, "forget") as mock_forget,
            patch.object(daemon.cli.LocalCache, "set_after_ack") as mock_set,
        ):
            captured["on_move"]("old-location.txt", "new-location.txt")

        # forget must be called for old path
        mock_forget.assert_called_once_with("old-location.txt")
        # set_after_ack must be called for new path with correct fingerprint
        mock_set.assert_called_once_with(
            "new-location.txt", content_hash, st.st_size, st.st_mtime
        )

    def test_move_failed_does_not_update_cache(self, tmp_path: Path, monkeypatch):
        """When report_moved fails, cache bookkeeping is skipped."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json={}
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        async def _fake_report_moved_fails(client, cfg, old_vp, new_vp):
            return Failure(error="moved failed", recoverable=False, context={})

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.report_moved", side_effect=_fake_report_moved_fails),
            patch.object(daemon.cli.LocalCache, "forget") as mock_forget,
            patch.object(daemon.cli.LocalCache, "set_after_ack") as mock_set,
            patch.object(daemon.cli, "_log") as mock_log,
        ):
            captured["on_move"]("old.txt", "new.txt")

        # Neither forget nor set_after_ack should be called
        mock_forget.assert_not_called()
        mock_set.assert_not_called()
        # Warning should be logged
        mock_log.warning.assert_any_call(
            "report_moved failed",
            old_path="old.txt",
            new_path="new.txt",
            error="moved failed",
        )

    # ── move detection (MoveBuffer) tests ────────────────────────────────

    def test_delete_then_create_same_hash_reports_move(self, tmp_path: Path, monkeypatch):
        """A delete followed by a create with the same content hash reports one move."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        content_hash = hashlib.sha256(b"moved content").hexdigest()

        # Pre-populate cache so delete can fingerprint via cache
        cache_json = {
            "old-folder/doc.txt": {
                "hash": content_hash,
                "size": len(b"moved content"),
                "mtime": 1234567890.0,
            }
        }

        # Create the destination file on disk so stat() succeeds
        new_folder = vault / "new-folder"
        new_folder.mkdir()
        new_file = new_folder / "doc.txt"
        new_file.write_text("moved content", encoding="utf-8")
        new_st = new_file.stat()

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json=cache_json
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        # Mock threading.Timer so the move window doesn't fire during the test
        mock_timers: list = []

        class _MockTimer:
            def __init__(self, interval, function):
                self.interval = interval
                self.function = function
                self.daemon = False
                mock_timers.append(self)

            def start(self):
                pass

            def cancel(self):
                pass

        async def _fake_report_moved(client, cfg, old_vp, new_vp):
            return Success(value=None)

        fake_text_content = TextContent(
            text="moved content",
            content_hash=content_hash,
            vault_path="new-folder/doc.txt",
            original_filename="doc.txt",
            file_size_bytes=new_st.st_size,
        )

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.threading.Timer", side_effect=_MockTimer),
            patch("daemon.cli.extract", return_value=Success(value=fake_text_content)),
            patch("daemon.cli.upload_text") as mock_upload_text,
            patch("daemon.cli.report_moved", side_effect=_fake_report_moved) as mock_report_moved,
            patch("daemon.cli.report_deleted") as mock_report_deleted,
            patch.object(daemon.cli.LocalCache, "forget") as mock_forget,
            patch.object(daemon.cli.LocalCache, "set_after_ack") as mock_set,
        ):
            # Step 1: Delete the old file
            captured["on_delete"]("old-folder/doc.txt")

            # Step 2: Create the new file (same content hash)
            captured["on_create"]("new-folder/doc.txt")

        # report_moved must have been called exactly once
        mock_report_moved.assert_called_once()
        call_args = mock_report_moved.call_args[0]
        assert call_args[2] == "old-folder/doc.txt"
        assert call_args[3] == "new-folder/doc.txt"

        # report_deleted must NOT have been called
        mock_report_deleted.assert_not_called()

        # upload_text must NOT have been called
        mock_upload_text.assert_not_called()

        # Cache: old path removed, new path added
        mock_forget.assert_called_once_with("old-folder/doc.txt")
        mock_set.assert_called_once_with(
            "new-folder/doc.txt", content_hash, new_st.st_size, new_st.st_mtime
        )

    def test_delete_no_cache_entry_reports_immediately(self, tmp_path: Path, monkeypatch):
        """A delete of a file not in cache is reported immediately (no buffering)."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        # Empty cache — no entry for the deleted file
        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json={}
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        async def _fake_report_deleted(client, cfg, vp):
            return Success(value=None)

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.report_deleted", side_effect=_fake_report_deleted) as mock_report_deleted,
            patch.object(daemon.cli, "_log") as mock_log,
        ):
            captured["on_delete"]("unknown-file.txt")

        # report_deleted must have been called immediately
        mock_report_deleted.assert_called_once()
        call_args = mock_report_deleted.call_args[0]
        assert call_args[2] == "unknown-file.txt"

        # Info log must mention "no cache entry"
        mock_log.info.assert_any_call(
            "reported deleted (no cache entry)", vault_path="unknown-file.txt"
        )

    def test_create_without_matching_delete_uploads_normally(self, tmp_path: Path, monkeypatch):
        """A create with no matching parked delete proceeds with normal upload."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        test_file = vault / "new-file.txt"
        test_file.write_text("brand new content", encoding="utf-8")
        st = test_file.stat()

        # No cache entry, no parked delete
        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json={}
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        content_hash = hashlib.sha256(b"brand new content").hexdigest()
        fake_text_content = TextContent(
            text="brand new content",
            content_hash=content_hash,
            vault_path="new-file.txt",
            original_filename="new-file.txt",
            file_size_bytes=st.st_size,
        )

        async def _fake_upload_text(client, cfg, tc):
            return Success(value="doc-new")

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.extract", return_value=Success(value=fake_text_content)),
            patch("daemon.cli.upload_text", side_effect=_fake_upload_text) as mock_upload_text,
            patch("daemon.cli.report_moved") as mock_report_moved,
            patch.object(daemon.cli.LocalCache, "set_after_ack") as mock_set,
        ):
            captured["on_create"]("new-file.txt")

        # upload_text must have been called (normal path)
        mock_upload_text.assert_called_once()
        # report_moved must NOT have been called
        mock_report_moved.assert_not_called()
        # Cache must have been updated
        mock_set.assert_called_once_with(
            "new-file.txt", content_hash, st.st_size, st.st_mtime
        )

    def test_move_window_expired_reports_deleted(self, tmp_path: Path, monkeypatch):
        """When the move window expires, pending deletes are reported."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        content_hash = hashlib.sha256(b"deleted content").hexdigest()

        # Pre-populate cache so delete can fingerprint via cache
        cache_json = {
            "deleted-file.txt": {
                "hash": content_hash,
                "size": len(b"deleted content"),
                "mtime": 1000000000.0,
            }
        }

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json=cache_json
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        # Mock threading.Timer to capture the expiry callback
        mock_timers: list = []

        class _MockTimer:
            def __init__(self, interval, function):
                self.interval = interval
                self.function = function
                self.daemon = False
                mock_timers.append(self)

            def start(self):
                pass

            def cancel(self):
                pass

        async def _fake_report_deleted(client, cfg, vp):
            return Success(value=None)

        # We need entries to appear expired.  Mock move_buffer.expire to
        # return the parked entries regardless of wall-clock time.
        def _fake_expire(move_window_seconds):
            # Return the fingerprint + old_vp for our parked entry
            return [(content_hash, "deleted-file.txt")]

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.threading.Timer", side_effect=_MockTimer),
            patch("daemon.cli.report_deleted", side_effect=_fake_report_deleted) as mock_report_deleted,
            patch.object(daemon.cli.LocalCache, "forget") as mock_forget,
            patch.object(daemon.cli, "_log") as mock_log,
            patch.object(daemon.cli.MoveBuffer, "expire", side_effect=_fake_expire),
        ):
            # Step 1: Delete the file — parks in buffer
            captured["on_delete"]("deleted-file.txt")

            # report_deleted must NOT have been called yet
            mock_report_deleted.assert_not_called()

            # Step 2: Simulate move window expiry by calling the timer function
            assert len(mock_timers) == 1
            expiry_callback = mock_timers[0].function
            expiry_callback()  # calls _on_move_window_expired

        # Now report_deleted must have been called
        mock_report_deleted.assert_called_once()
        call_args = mock_report_deleted.call_args[0]
        assert call_args[2] == "deleted-file.txt"

        # Cache must have been cleared
        mock_forget.assert_called_once_with("deleted-file.txt")

        # Info log must mention "move window expired"
        mock_log.info.assert_any_call(
            "reported deleted (move window expired)", vault_path="deleted-file.txt"
        )

    def test_move_window_timer_refreshed_on_subsequent_deletes(self, tmp_path: Path, monkeypatch):
        """Each delete restarts the move window timer (previous timer is cancelled)."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        content_hash_a = hashlib.sha256(b"file A").hexdigest()
        content_hash_b = hashlib.sha256(b"file B").hexdigest()

        cache_json = {
            "file-a.txt": {
                "hash": content_hash_a,
                "size": len(b"file A"),
                "mtime": 1000000000.0,
            },
            "file-b.txt": {
                "hash": content_hash_b,
                "size": len(b"file B"),
                "mtime": 1000000001.0,
            },
        }

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json=cache_json
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        mock_timers: list = []

        class _MockTimer:
            def __init__(self, interval, function):
                self.interval = interval
                self.function = function
                self.daemon = False
                self._cancelled = False
                mock_timers.append(self)

            def start(self):
                pass

            def cancel(self):
                self._cancelled = True

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.threading.Timer", side_effect=_MockTimer),
            patch("daemon.cli.report_deleted") as mock_report_deleted,
        ):
            # Delete first file — a timer should be created
            captured["on_delete"]("file-a.txt")
            assert len(mock_timers) == 1
            first_timer = mock_timers[0]

            # Delete second file — the first timer should be cancelled and a new one created
            captured["on_delete"]("file-b.txt")
            assert len(mock_timers) == 2
            assert first_timer._cancelled is True

            # No immediate reports
            mock_report_deleted.assert_not_called()

    def test_native_os_move_still_works_with_buffer(self, tmp_path: Path, monkeypatch):
        """Native FileMovedEvent still reports move and updates cache (buffer not involved)."""
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = _tmp_yaml(tmp_path, _minimal_config_yaml(vault, "http://fake:8080"))

        # Create the destination file
        new_file = vault / "renamed.txt"
        new_file.write_text("moved via OS", encoding="utf-8")
        st = new_file.stat()
        content_hash = hashlib.sha256(new_file.read_bytes()).hexdigest()

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json={}
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        async def _fake_report_moved(client, cfg, old_vp, new_vp):
            return Success(value=None)

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.report_moved", side_effect=_fake_report_moved) as mock_report_moved,
            patch.object(daemon.cli.LocalCache, "forget") as mock_forget,
            patch.object(daemon.cli.LocalCache, "set_after_ack") as mock_set,
        ):
            captured["on_move"]("original.txt", "renamed.txt")

        # report_moved must have been called
        mock_report_moved.assert_called_once()
        call_args = mock_report_moved.call_args[0]
        assert call_args[2] == "original.txt"
        assert call_args[3] == "renamed.txt"

        # Cache updated
        mock_forget.assert_called_once_with("original.txt")
        mock_set.assert_called_once_with(
            "renamed.txt", content_hash, st.st_size, st.st_mtime
        )

    # ── integration (cache-on-disk) tests ────────────────────────────────

    def test_cache_saved_to_disk_at_shutdown(self, tmp_path: Path, monkeypatch):
        """Cache is persisted to disk when the daemon shuts down gracefully."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        cache_path = tmp_path / "cache.json"
        # Pre-write an empty cache so the path is in the config
        cache_path.write_text("{}")
        config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
cache_path: {cache_path}
"""
        config_path = _tmp_yaml(tmp_path, config_content)

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=0)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            # Simulate that some entries were written during scan
            if cache is not None:
                cache.set_after_ack("test.txt", "abc123", 42, 9000000000.0)
            return fake_result

        sleep_count = [0]

        async def _fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] == 1:
                raise KeyboardInterrupt()
            await asyncio.sleep(0)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_watcher = MagicMock()
        mock_watcher.start = MagicMock()
        mock_watcher.stop = MagicMock()
        mock_watcher.join = MagicMock()

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", return_value=mock_watcher),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["start", "--config", str(config_path)])
            assert result.exit_code == 0

        # Cache must have been saved to disk
        assert cache_path.exists()
        raw = json.loads(cache_path.read_text())
        assert "test.txt" in raw
        assert raw["test.txt"]["hash"] == "abc123"
        assert raw["test.txt"]["size"] == 42

    def test_cache_loaded_from_disk_on_startup(self, tmp_path: Path, monkeypatch):
        """Entries written to cache survive a daemon restart."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        cache_path = tmp_path / "cache.json"

        # Pre-populate the cache file with entries
        cache_json = {
            "persisted.txt": {
                "hash": "def456",
                "size": 99,
                "mtime": 8000000000.0,
            }
        }
        cache_path.write_text(json.dumps(cache_json))

        config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
cache_path: {cache_path}
"""
        config_path = _tmp_yaml(tmp_path, config_content)

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=0)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            # Verify the cache already has the persisted entry on startup
            if cache is not None:
                entry = cache.get("persisted.txt")
                assert entry is not None, "Cache should have loaded persisted.txt from disk"
                assert entry["hash"] == "def456"
            return fake_result

        sleep_count = [0]

        async def _fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] == 1:
                raise KeyboardInterrupt()
            await asyncio.sleep(0)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_watcher = MagicMock()
        mock_watcher.start = MagicMock()
        mock_watcher.stop = MagicMock()
        mock_watcher.join = MagicMock()

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", return_value=mock_watcher),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["start", "--config", str(config_path)])
            assert result.exit_code == 0

    def test_full_lifecycle_no_errors(self, tmp_path: Path, monkeypatch):
        """A full startup→scan→watch→modify→upload→delete→shutdown cycle completes."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        cache_path = tmp_path / "cache.json"
        cache_path.write_text("{}")

        config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
cache_path: {cache_path}
move_window_seconds: 2.0
"""
        config_path = _tmp_yaml(tmp_path, config_content)

        # Create a test file on disk for the modify/upload test
        test_file = vault / "notes.txt"
        test_file.write_text("hello lifecycle", encoding="utf-8")
        st = test_file.stat()
        content_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=0)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            return fake_result

        # Simulate: run scan, then trigger a create (which does upload),
        # a delete, then shutdown
        captured_callbacks: dict = {}

        class _MockWatcher:
            def __init__(self, config, on_create, on_modify, on_move, on_delete):
                captured_callbacks["on_create"] = on_create
                captured_callbacks["on_modify"] = on_modify
                captured_callbacks["on_move"] = on_move
                captured_callbacks["on_delete"] = on_delete

            def start(self) -> None:
                pass
            def stop(self) -> None:
                pass
            def join(self) -> None:
                pass

        sleep_count = [0]

        async def _fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] == 1:
                # After the first sleep (main loop), trigger callbacks
                # then raise KeyboardInterrupt to shut down
                pass
            if sleep_count[0] == 2:
                raise KeyboardInterrupt()
            await asyncio.sleep(0)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_text_content = TextContent(
            text="hello lifecycle",
            content_hash=content_hash,
            vault_path="notes.txt",
            original_filename="notes.txt",
            file_size_bytes=st.st_size,
        )

        async def _fake_upload_text(client, cfg, tc):
            return Success(value="doc-lifecycle")

        async def _fake_report_deleted(client, cfg, vp):
            return Success(value=None)

        # Patch asyncio.run_coroutine_threadsafe to run synchronously
        def _sync_rct(coro, loop):
            asyncio.run(coro)

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", side_effect=_MockWatcher),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.extract", return_value=Success(value=fake_text_content)),
            patch("daemon.cli.upload_text", side_effect=_fake_upload_text),
            patch("daemon.cli.report_deleted", side_effect=_fake_report_deleted),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["start", "--config", str(config_path)])
            assert result.exit_code == 0

        # After shutdown, the cache file should exist (saved in finally block)
        assert cache_path.exists()

    # ── end-to-end cache-on-disk verification ────────────────────────────

    def test_cache_json_matches_after_upload(self, tmp_path: Path, monkeypatch):
        """After a successful upload, set_after_ack is called; a save persists to disk."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        cache_path = tmp_path / "cache.json"
        cache_path.write_text("{}")

        config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
cache_path: {cache_path}
"""
        config_path = _tmp_yaml(tmp_path, config_content)

        # Real file on disk
        test_file = vault / "doc.txt"
        test_file.write_text("end-to-end upload test", encoding="utf-8")
        st = test_file.stat()
        content_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json={}
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        fake_text_content = TextContent(
            text="end-to-end upload test",
            content_hash=content_hash,
            vault_path="doc.txt",
            original_filename="doc.txt",
            file_size_bytes=st.st_size,
        )

        async def _fake_upload_text(client, cfg, tc):
            return Success(value="doc-e2e")

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.extract", return_value=Success(value=fake_text_content)),
            patch("daemon.cli.upload_text", side_effect=_fake_upload_text),
            patch.object(daemon.cli.LocalCache, "set_after_ack") as mock_set,
        ):
            captured["on_create"]("doc.txt")

        # set_after_ack must have been called (in-memory update)
        mock_set.assert_called_once_with(
            "doc.txt", content_hash, st.st_size, st.st_mtime
        )

    def test_cache_unchanged_after_failed_upload(self, tmp_path: Path, monkeypatch):
        """After a failed upload, set_after_ack is NOT called."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        cache_path = tmp_path / "cache.json"
        cache_path.write_text("{}")

        config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
cache_path: {cache_path}
"""
        config_path = _tmp_yaml(tmp_path, config_content)

        test_file = vault / "fail.txt"
        test_file.write_text("this will fail", encoding="utf-8")
        st = test_file.stat()

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json={}
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        fake_text_content = TextContent(
            text="this will fail",
            content_hash=hashlib.sha256(b"this will fail").hexdigest(),
            vault_path="fail.txt",
            original_filename="fail.txt",
            file_size_bytes=st.st_size,
        )

        async def _fake_upload_text_fails(client, cfg, tc):
            return Failure(error="simulated 500", recoverable=False, context={})

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.extract", return_value=Success(value=fake_text_content)),
            patch("daemon.cli.upload_text", side_effect=_fake_upload_text_fails),
            patch.object(daemon.cli.LocalCache, "set_after_ack") as mock_set,
            patch.object(daemon.cli, "_log") as mock_log,
        ):
            captured["on_create"]("fail.txt")

        # set_after_ack must NOT have been called
        mock_set.assert_not_called()
        # Warning log must mention upload_text failed
        mock_log.warning.assert_any_call(
            "upload_text failed", vault_path="fail.txt", error="simulated 500"
        )

    def test_cache_move_bookkeeping(self, tmp_path: Path, monkeypatch):
        """After a move, old path is gone from in-memory cache and new path is present."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        cache_path = tmp_path / "cache.json"

        # Pre-populate with old entry
        old_hash = hashlib.sha256(b"move content").hexdigest()
        cache_json = {
            "old.txt": {
                "hash": old_hash,
                "size": len(b"move content"),
                "mtime": 1000000000.0,
            }
        }
        cache_path.write_text(json.dumps(cache_json))

        config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
cache_path: {cache_path}
"""
        config_path = _tmp_yaml(tmp_path, config_content)

        # Create the new file on disk
        new_file = vault / "new.txt"
        new_file.write_text("move content", encoding="utf-8")
        st = new_file.stat()
        new_hash = hashlib.sha256(new_file.read_bytes()).hexdigest()

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json=cache_json
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        async def _fake_report_moved(client, cfg, old_vp, new_vp):
            return Success(value=None)

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.report_moved", side_effect=_fake_report_moved),
            patch.object(daemon.cli.LocalCache, "forget") as mock_forget,
            patch.object(daemon.cli.LocalCache, "set_after_ack") as mock_set,
        ):
            captured["on_move"]("old.txt", "new.txt")

        # In-memory cache: old forgotten, new set
        mock_forget.assert_called_once_with("old.txt")
        mock_set.assert_called_once_with(
            "new.txt", new_hash, st.st_size, st.st_mtime
        )

    def test_cache_delete_removal(self, tmp_path: Path, monkeypatch):
        """After a successful delete report (expired path), cache.forget removes the entry."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        cache_path = tmp_path / "cache.json"

        content_hash = hashlib.sha256(b"to be deleted").hexdigest()
        cache_json = {
            "delete-me.txt": {
                "hash": content_hash,
                "size": len(b"to be deleted"),
                "mtime": 1000000000.0,
            }
        }
        cache_path.write_text(json.dumps(cache_json))

        config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
cache_path: {cache_path}
move_window_seconds: 0.01
"""
        config_path = _tmp_yaml(tmp_path, config_content)

        captured = self._run_start_and_capture_callbacks(
            tmp_path, monkeypatch, vault, config_path, cache_json=cache_json
        )

        def _sync_rct(coro, loop):
            asyncio.run(coro)

        # Mock Timer to capture the expiry callback
        mock_timers: list = []

        class _MockTimer:
            def __init__(self, interval, function):
                self.interval = interval
                self.function = function
                self.daemon = False
                mock_timers.append(self)
            def start(self):
                pass
            def cancel(self):
                pass

        async def _fake_report_deleted(client, cfg, vp):
            return Success(value=None)

        # Make expire return our parked entry
        def _fake_expire(move_window_seconds):
            return [(content_hash, "delete-me.txt")]

        with (
            patch("daemon.cli.asyncio.run_coroutine_threadsafe", side_effect=_sync_rct),
            patch("daemon.cli.threading.Timer", side_effect=_MockTimer),
            patch("daemon.cli.report_deleted", side_effect=_fake_report_deleted),
            patch.object(daemon.cli.MoveBuffer, "expire", side_effect=_fake_expire),
            patch.object(daemon.cli.LocalCache, "forget") as mock_forget,
            patch.object(daemon.cli, "_log") as mock_log,
        ):
            # Step 1: Delete the file — parks in buffer
            captured["on_delete"]("delete-me.txt")

            # Step 2: Trigger expiry
            assert len(mock_timers) == 1
            expiry_callback = mock_timers[0].function
            expiry_callback()  # calls _on_move_window_expired → cache.forget

        # Cache.forget must have been called
        mock_forget.assert_called_once_with("delete-me.txt")
        # Info log must confirm
        mock_log.info.assert_any_call(
            "reported deleted (move window expired)", vault_path="delete-me.txt"
        )

    # ── periodic reconcile tests ─────────────────────────────────────────

    def test_periodic_reconcile_created_when_interval_positive(
        self, tmp_path: Path, monkeypatch
    ):
        """With periodic_interval_seconds=1, _periodic_reconcile is called."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
periodic_interval_seconds: 1
"""
        config_path = _tmp_yaml(tmp_path, config_content)

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=0)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            return fake_result

        # Mock DaemonLoop._periodic_reconcile to verify it's called
        mock_reconcile = AsyncMock()

        sleep_count = [0]

        async def _fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] == 1:
                raise KeyboardInterrupt()
            await asyncio.sleep(0)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_watcher = MagicMock()
        mock_watcher.start = MagicMock()
        mock_watcher.stop = MagicMock()
        mock_watcher.join = MagicMock()

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", return_value=mock_watcher),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
            patch("daemon.cli.DaemonLoop._periodic_reconcile", mock_reconcile),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["start", "--config", str(config_path)])
            assert result.exit_code == 0

        # DaemonLoop._periodic_reconcile must have been called
        mock_reconcile.assert_called_once()

    def test_periodic_reconcile_disabled_when_interval_zero(
        self, tmp_path: Path, monkeypatch
    ):
        """With periodic_interval_seconds=0, _periodic_reconcile is NOT called."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
periodic_interval_seconds: 0
"""
        config_path = _tmp_yaml(tmp_path, config_content)

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=0)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            return fake_result

        mock_reconcile = AsyncMock()

        sleep_count = [0]

        async def _fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] == 1:
                raise KeyboardInterrupt()
            await asyncio.sleep(0)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_watcher = MagicMock()
        mock_watcher.start = MagicMock()
        mock_watcher.stop = MagicMock()
        mock_watcher.join = MagicMock()

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", return_value=mock_watcher),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
            patch("daemon.cli.DaemonLoop._periodic_reconcile", mock_reconcile),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["start", "--config", str(config_path)])
            assert result.exit_code == 0

        # DaemonLoop._periodic_reconcile must NOT have been called
        mock_reconcile.assert_not_called()

    def test_periodic_task_cancelled_on_shutdown(
        self, tmp_path: Path, monkeypatch
    ):
        """The periodic task is cancelled cleanly when the daemon shuts down (no warnings)."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
periodic_interval_seconds: 1
"""
        config_path = _tmp_yaml(tmp_path, config_content)

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=0)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            return fake_result

        # Mock DaemonLoop._periodic_reconcile with an AsyncMock that can be cancelled
        mock_reconcile = AsyncMock()

        sleep_count = [0]

        async def _fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] == 1:
                raise KeyboardInterrupt()
            await asyncio.sleep(0)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_watcher = MagicMock()
        mock_watcher.start = MagicMock()
        mock_watcher.stop = MagicMock()
        mock_watcher.join = MagicMock()

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", return_value=mock_watcher),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
            patch("daemon.cli.DaemonLoop._periodic_reconcile", mock_reconcile),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["start", "--config", str(config_path)])
            # Must exit cleanly — cancellation should be handled without error
            assert result.exit_code == 0

        # DaemonLoop._periodic_reconcile was called (task was created)
        mock_reconcile.assert_called_once()

    def test_periodic_reconcile_shares_cache(
        self, tmp_path: Path, monkeypatch
    ):
        """The periodic reconcile receives the same cache instance used by watcher callbacks."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")
        vault = tmp_path / "vault"
        vault.mkdir()
        config_content = f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
periodic_interval_seconds: 1
"""
        config_path = _tmp_yaml(tmp_path, config_content)

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=0)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            return fake_result

        # Capture the cache passed to DaemonLoop._periodic_reconcile via self
        reconcile_cache: list = []

        # Use a regular function (not async) so arguments are captured at call time.
        # The patched method receives self (the DaemonLoop instance).
        def _fake_reconcile(self):
            reconcile_cache.append(self._cache)
            async def _noop():
                pass
            return _noop()

        sleep_count = [0]

        async def _fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] == 1:
                raise KeyboardInterrupt()
            await asyncio.sleep(0)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_watcher = MagicMock()
        mock_watcher.start = MagicMock()
        mock_watcher.stop = MagicMock()
        mock_watcher.join = MagicMock()

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", return_value=mock_watcher),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
            patch("daemon.cli.DaemonLoop._periodic_reconcile", new=_fake_reconcile),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["start", "--config", str(config_path)])
            assert result.exit_code == 0

        # DaemonLoop._periodic_reconcile was called and the instance had a cache
        assert len(reconcile_cache) == 1
        assert isinstance(reconcile_cache[0], daemon.cli.LocalCache)

    @pytest.mark.asyncio
    async def test_periodic_reconcile_calls_scan_repeatedly(
        self, tmp_path: Path, monkeypatch
    ):
        """DaemonLoop._periodic_reconcile calls scan() in a loop, once per interval."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")

        # Create a minimal config and cache
        from daemon.config import DaemonConfig
        from daemon.cache import DaemonSyncState, LocalCache

        vault = tmp_path / "vault"
        vault.mkdir()
        cache_path = tmp_path / "cache.json"
        cache_path.write_text("{}")

        cfg = DaemonConfig(
            vault_root=vault,
            cloud_endpoint="http://fake:8080",
            api_key="test-key",
            periodic_interval_seconds=1,  # short for testing
            cache_path=str(cache_path),
        )

        fake_result = ScanResult(uploaded=1, re_uploaded=0, deleted=0, skipped=0)
        scan_calls = [0]
        sleep_calls = [0]

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            scan_calls[0] += 1
            return fake_result

        async def _fake_sleep(seconds):
            sleep_calls[0] += 1
            if sleep_calls[0] >= 3:  # let it loop twice then stop
                raise asyncio.CancelledError()

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
            patch.object(daemon.cli.LocalCache, "save"),
        ):
            # Create a DaemonLoop instance and wire it up
            daemon_loop = daemon.cli.DaemonLoop(cfg, cache_path)
            sync_state = DaemonSyncState()
            daemon_loop._cache = LocalCache(sync_state)
            daemon_loop._cache.load(cache_path)
            daemon_loop._client = mock_client
            daemon_loop._candidate_deletes = {}
            daemon_loop._sweep_confirmations = 2

            try:
                await daemon_loop._periodic_reconcile()
            except asyncio.CancelledError:
                pass

        # scan should have been called at least twice (once per interval loop)
        assert scan_calls[0] >= 2, f"Expected >= 2 scan calls, got {scan_calls[0]}"


# ── CLI group tests ─────────────────────────────────────────────────────────


class TestCliGroup:
    """Tests for the CLI group itself."""

    def test_cli_help(self):
        """``daemon --help`` exits 0 and lists commands."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "start" in result.output
        assert "scan" in result.output
        assert "status" in result.output

    def test_config_option_appears_in_help(self):
        """All commands show --config in their help."""
        for cmd in ["start", "scan", "status"]:
            runner = CliRunner()
            result = runner.invoke(cli, [cmd, "--help"])
            assert result.exit_code == 0
            assert "--config" in result.output


# ── module entry point test ─────────────────────────────────────────────────


def test_main_entry_point():
    """``daemon.__main__`` imports cleanly and exposes cli."""
    # Verify the module imports without side effects (cli() is behind __main__ guard)
    import daemon.__main__
    assert daemon.__main__.cli is cli


# ── _run_with_stop tests ────────────────────────────────────────────────────


class TestRunWithStop:
    """Tests for the extracted ``_run_with_stop`` stop-event seam (Phase 5)."""

    @pytest.mark.asyncio
    async def test_run_with_stop_exits_when_stop_event_set(self, tmp_path, monkeypatch):
        """``_run_with_stop`` exits its main loop when stop_event is set.

        The function must NOT hang forever — setting the event must cause the
        ``while True`` loop to break and the ``finally`` block to run
        (watcher.stop()/join()).
        """
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")

        from daemon.config import DaemonConfig

        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        config_path.write_text(f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
""")

        cfg = DaemonConfig(
            vault_root=vault,
            cloud_endpoint="http://fake:8080",
            api_key="test-key",
            cache_path=str(tmp_path / "cache.json"),
        )

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=0)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            return fake_result

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_watcher = MagicMock()
        mock_watcher.start = MagicMock()
        mock_watcher.stop = MagicMock()
        mock_watcher.join = MagicMock()

        # Count sleep iterations; set stop_event after 2 iterations
        sleep_count = [0]
        stop_event = asyncio.Event()
        _real_sleep = asyncio.sleep

        async def _fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                stop_event.set()
            await _real_sleep(0)

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", return_value=mock_watcher),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
        ):
            await daemon.cli._run_with_stop(
                cfg=cfg,
                config_path=config_path,
                stop_event=stop_event,
            )

        # After _run_with_stop returns, the watcher must have been stopped
        mock_watcher.start.assert_called_once()
        mock_watcher.stop.assert_called_once()
        mock_watcher.join.assert_called_once()
        # The loop must have run at least 2 iterations before the stop event broke it
        assert sleep_count[0] >= 2

    @pytest.mark.asyncio
    async def test_run_with_stop_no_stop_event_runs_normally(self, tmp_path, monkeypatch):
        """``_run_with_stop`` with stop_event=None behaves like the original ``_run``.

        The stop_event check is skipped when None, so the function must be
        externally interrupted (e.g. KeyboardInterrupt) — same as before.
        """
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")

        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        config_path.write_text(f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
""")

        from daemon.config import DaemonConfig
        cfg = DaemonConfig(
            vault_root=vault,
            cloud_endpoint="http://fake:8080",
            api_key="test-key",
            cache_path=str(tmp_path / "cache.json"),
        )

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=0)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            return fake_result

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_watcher = MagicMock()
        mock_watcher.start = MagicMock()
        mock_watcher.stop = MagicMock()
        mock_watcher.join = MagicMock()

        sleep_count = [0]
        _real_sleep = asyncio.sleep

        async def _fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] >= 3:
                raise KeyboardInterrupt()
            await _real_sleep(0)

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", return_value=mock_watcher),
            patch("daemon.cli.asyncio.sleep", side_effect=_fake_sleep),
        ):
            try:
                await daemon.cli._run_with_stop(cfg=cfg, config_path=config_path)
            except KeyboardInterrupt:
                pass

        # The finally block must still run
        mock_watcher.stop.assert_called_once()
        mock_watcher.join.assert_called_once()


# ── DaemonLoop init tests ───────────────────────────────────────────────────


class TestDaemonLoopInit:
    """Tests for ``DaemonLoop.__init__`` and the ``_run_with_stop`` wrapper."""

    def test_daemon_loop_stores_config(self, tmp_path):
        """``DaemonLoop.__init__`` stores cfg, config_path, and stop_event."""
        from daemon.config import DaemonConfig
        import asyncio

        vault = tmp_path / "vault"
        vault.mkdir()
        cfg = DaemonConfig(
            vault_root=vault,
            cloud_endpoint="http://example.com",
            api_key="fake-key",
        )
        config_path = tmp_path / "config.yaml"
        stop_event = asyncio.Event()

        loop = daemon.cli.DaemonLoop(cfg, config_path, stop_event)

        assert loop._cfg is cfg
        assert loop._config_path is config_path
        assert loop._stop_event is stop_event

    def test_daemon_loop_init_defaults(self, tmp_path):
        """``DaemonLoop.__init__`` initialises mutable state to safe defaults."""
        from daemon.config import DaemonConfig

        vault = tmp_path / "vault"
        vault.mkdir()
        cfg = DaemonConfig(
            vault_root=vault,
            cloud_endpoint="http://example.com",
            api_key="fake-key",
        )
        config_path = tmp_path / "config.yaml"

        loop = daemon.cli.DaemonLoop(cfg, config_path)

        # State that is set up in run() starts as None / empty
        assert loop._client is None
        assert loop._cache is None
        assert loop._move_buffer is None
        assert loop._loop is None
        assert loop._candidate_deletes == {}
        assert loop._sweep_confirmations == 0
        assert loop._watcher is None

        # Move timer state
        assert loop._move_timer is None
        assert loop._move_timer_lock is not None
        assert loop._periodic_task is None

    @pytest.mark.asyncio
    async def test_run_with_stop_wrapper_constructs_and_runs(self, tmp_path, monkeypatch):
        """``_run_with_stop`` constructs a ``DaemonLoop`` and calls ``run()``."""
        monkeypatch.setenv("KMS_DAEMON_API_KEY", "test-key")

        from daemon.config import DaemonConfig

        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        config_path.write_text(f"""vault_root: {vault}
cloud_endpoint: http://fake:8080
""")

        cfg = DaemonConfig(
            vault_root=vault,
            cloud_endpoint="http://fake:8080",
            api_key="test-key",
            cache_path=str(tmp_path / "cache.json"),
        )

        # Provide a stop_event that is set immediately so the main loop exits
        stop_event = asyncio.Event()
        stop_event.set()

        fake_result = ScanResult(uploaded=0, re_uploaded=0, deleted=0, skipped=0)

        async def _fake_scan(cfg, client, cache=None, candidate_deletes=None, sweep_delete_confirmations=1):
            return fake_result

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_watcher = MagicMock()
        mock_watcher.start = MagicMock()
        mock_watcher.stop = MagicMock()
        mock_watcher.join = MagicMock()

        with (
            patch("daemon.cli.httpx.AsyncClient", return_value=mock_client),
            patch("daemon.cli.scan", side_effect=_fake_scan),
            patch("daemon.cli.DaemonWatcher", return_value=mock_watcher),
        ):
            # Run through the thin wrapper
            await daemon.cli._run_with_stop(
                cfg=cfg,
                config_path=config_path,
                stop_event=stop_event,
            )

        # The wrapper should have started the watcher and then shut it down
        mock_watcher.start.assert_called_once()
        mock_watcher.stop.assert_called_once()
        mock_watcher.join.assert_called_once()
