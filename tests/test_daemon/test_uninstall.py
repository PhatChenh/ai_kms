"""
tests/test_daemon/test_uninstall.py

Tests for ``daemon uninstall`` — the fourth Click command that removes
the key, config file, and auto-start registration.

All external dependencies (secret_vault, os_glue) are stubbed via
monkeypatch so tests never touch the real OS vault or registry.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from click.testing import CliRunner

from core.result import Failure, Success
from daemon.cli import cli


# ── helpers ──────────────────────────────────────────────────────────────────


def _stub_key_present(monkeypatch):
    """Stub vault so the key IS present (read_key → Success, delete_key → Success)."""
    delete_called = [False]

    def fake_read_key():
        return Success("fake-api-key")

    def fake_delete_key():
        delete_called[0] = True
        return Success(None)

    monkeypatch.setattr("daemon.secret_vault.read_key", fake_read_key)
    monkeypatch.setattr("daemon.secret_vault.delete_key", fake_delete_key)
    return delete_called


def _stub_key_absent(monkeypatch):
    """Stub vault so the key is ABSENT (read_key → Failure)."""
    def fake_read_key():
        return Failure(error="API key not found in OS vault", recoverable=False, context={})

    monkeypatch.setattr("daemon.secret_vault.read_key", fake_read_key)


def _stub_registration(monkeypatch):
    """Stub os_glue so unregister_at_login returns Success."""
    called = [False]

    mock_adapter = MagicMock()

    def fake_unregister():
        called[0] = True
        return Success(None)

    mock_adapter.unregister_at_login.side_effect = fake_unregister

    def fake_get_os_adapter():
        return mock_adapter

    monkeypatch.setattr("daemon.os_glue.get_os_adapter", fake_get_os_adapter)
    return called


# ── Tracer Bullet: full uninstall on a set-up machine ──────────────────────


class TestUninstallSetUpMachine:
    """Uninstall on a machine where everything is present."""

    def test_removes_key_config_and_registration(self, tmp_path: Path, monkeypatch):
        """All three items present → all three removed, result lists them."""
        # ── Arrange: "set-up machine" state ──────────────────────────────
        config_path = tmp_path / "config.yaml"
        config_path.write_text("vault_root: /tmp/fake\ncloud_endpoint: http://fake:8080\n")

        delete_called = _stub_key_present(monkeypatch)
        unregister_called = _stub_registration(monkeypatch)

        # ── Act ──────────────────────────────────────────────────────────
        runner = CliRunner()
        result = runner.invoke(cli, ["uninstall", "--config", str(config_path)])

        # ── Assert ───────────────────────────────────────────────────────
        assert result.exit_code == 0, f"unexpected failure: {result.output}"
        # delete_key was called
        assert delete_called[0] is True
        # unregister_at_login was called
        assert unregister_called[0] is True
        # config file was removed
        assert not config_path.exists()
        # Output mentions what was removed
        assert "key" in result.output.lower()
        assert "config" in result.output.lower()
        assert "registration" in result.output.lower()


class TestUninstallIdempotent:
    """Running uninstall twice is safe."""

    def test_second_run_reports_success(self, tmp_path: Path, monkeypatch):
        """After everything is already gone, second run still succeeds."""
        # ── Arrange: "already clean" state ───────────────────────────────
        config_path = tmp_path / "config.yaml"
        # Config file does NOT exist — already gone
        _stub_key_absent(monkeypatch)
        _stub_registration(monkeypatch)

        # ── Act ──────────────────────────────────────────────────────────
        runner = CliRunner()
        result = runner.invoke(cli, ["uninstall", "--config", str(config_path)])

        # ── Assert ───────────────────────────────────────────────────────
        assert result.exit_code == 0, f"unexpected failure: {result.output}"
        output_lower = result.output.lower()
        # Second run is safe — succeeds without error
        assert "uninstall complete" in output_lower
        # Registration is always reported (idempotent, can't distinguish)
        assert "registration" in output_lower


class TestUninstallPartial:
    """Individual items may already be absent — uninstall still succeeds."""

    def test_config_file_absent_other_steps_still_run(self, tmp_path: Path, monkeypatch):
        """When config file is already gone, key + registration are still removed."""
        config_path = tmp_path / "config.yaml"
        # Config file does NOT exist

        delete_called = _stub_key_present(monkeypatch)
        unregister_called = _stub_registration(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["uninstall", "--config", str(config_path)])

        assert result.exit_code == 0, f"unexpected failure: {result.output}"
        assert delete_called[0] is True
        assert unregister_called[0] is True
        # Key and registration reported, config not
        assert "key" in result.output.lower()
        assert "registration" in result.output.lower()

    def test_key_absent_other_steps_still_run(self, tmp_path: Path, monkeypatch):
        """When key is already absent, config + registration are still removed."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("vault_root: /tmp/fake\ncloud_endpoint: http://fake:8080\n")

        _stub_key_absent(monkeypatch)
        unregister_called = _stub_registration(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["uninstall", "--config", str(config_path)])

        assert result.exit_code == 0, f"unexpected failure: {result.output}"
        assert unregister_called[0] is True
        assert not config_path.exists()
        # Config and registration reported, key not
        assert "config" in result.output.lower()
        assert "registration" in result.output.lower()

    def test_registration_absent_other_steps_still_run(self, tmp_path: Path, monkeypatch):
        """When registration is already absent, key + config are still removed."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("vault_root: /tmp/fake\ncloud_endpoint: http://fake:8080\n")

        delete_called = _stub_key_present(monkeypatch)
        _stub_registration(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(cli, ["uninstall", "--config", str(config_path)])

        assert result.exit_code == 0, f"unexpected failure: {result.output}"
        assert delete_called[0] is True
        assert not config_path.exists()
        # Key, config, and registration all reported (registration always reports success)
        assert "key" in result.output.lower()
        assert "config" in result.output.lower()
        assert "registration" in result.output.lower()


class TestUninstallCliCommand:
    """The Click command itself registers and is usable."""

    def test_help(self):
        """``daemon uninstall --help`` exits 0 and mentions uninstall."""
        runner = CliRunner()
        result = runner.invoke(cli, ["uninstall", "--help"])
        assert result.exit_code == 0
        assert "uninstall" in result.output.lower()

    def test_config_option_appears_in_help(self):
        """Uninstall help shows --config option."""
        runner = CliRunner()
        result = runner.invoke(cli, ["uninstall", "--help"])
        assert result.exit_code == 0
        assert "--config" in result.output

    def test_uninstall_appears_in_cli_help(self):
        """``daemon --help`` lists uninstall alongside other commands."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "uninstall" in result.output


class TestRunUninstallFunction:
    """Direct tests for run_uninstall() the function."""

    def test_returns_success_with_removed_list(self, tmp_path: Path, monkeypatch):
        """run_uninstall returns Success with a 'removed' list."""
        from daemon.cli import run_uninstall

        config_path = tmp_path / "config.yaml"
        config_path.write_text("vault_root: /tmp/fake\ncloud_endpoint: http://fake:8080\n")

        _stub_key_present(monkeypatch)
        _stub_registration(monkeypatch)

        result = run_uninstall(config_path)

        assert result.is_success()
        assert "removed" in result.value
        assert "key" in result.value["removed"]
        assert "config" in result.value["removed"]
        assert "registration" in result.value["removed"]

    def test_second_run_returns_only_registration(self, tmp_path: Path, monkeypatch):
        """After uninstall, second run returns only 'registration' (idempotent, can't distinguish)."""
        from daemon.cli import run_uninstall

        config_path = tmp_path / "config.yaml"
        # Config already absent
        _stub_key_absent(monkeypatch)
        _stub_registration(monkeypatch)

        result = run_uninstall(config_path)

        assert result.is_success()
        # Only registration is reported (unregister_at_login always returns Success)
        assert result.value["removed"] == ["registration"]

    def test_delete_key_failure_propagates(self, tmp_path: Path, monkeypatch):
        """If delete_key returns Failure, run_uninstall returns Failure."""
        from daemon.cli import run_uninstall

        config_path = tmp_path / "config.yaml"

        # Key is present (read_key succeeds), but delete_key fails
        def fake_read_key():
            return Success("fake-api-key")

        monkeypatch.setattr("daemon.secret_vault.read_key", fake_read_key)

        def fake_delete_key():
            return Failure(error="vault access denied", recoverable=False, context={})

        monkeypatch.setattr("daemon.secret_vault.delete_key", fake_delete_key)

        result = run_uninstall(config_path)

        assert result.is_failure()
        assert "vault access denied" in result.error
