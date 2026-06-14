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

        async def _fake_scan(cfg, client):
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

        async def _fake_scan(cfg, client):
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
            patch.object(daemon.cli.LocalCache, "set_after_ack") as mock_set,
        ):
            captured["on_create"]("notes.txt")

        # extract must NOT be called
        mock_extract.assert_not_called()
        # debug log must mention stat changed, content same
        mock_log.debug.assert_any_call(
            "skipped (stat changed, content same)", vault_path="notes.txt"
        )
        # cache.set_after_ack must have been called with updated stat
        mock_set.assert_called_once_with(
            "notes.txt", content_hash, real_st.st_size, real_st.st_mtime
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
