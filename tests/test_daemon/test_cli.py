"""
tests/test_daemon/test_cli.py

Tests for daemon/cli.py — the Click CLI for the sync daemon.

Tests use CliRunner with mocking to avoid real HTTP calls and filesystem
interactions.  The test suite covers:

  - status  command: reachability reporting (success + failure paths)
  - scan    command: one-shot reconciliation summary printing
  - start   command: basic invocation, config loading, Ctrl+C handling
  - CLI     group:  --help, --config option propagation
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from daemon.cli import cli
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
