"""
tests/test_daemon/test_app.py

Tests for daemon/app.py — the App Supervisor (Phase 5).

Tests use monkeypatching extensively to stub system boundaries.
We patch at the importing module (daemon.app.*) for module-level imports
and at source modules for lazy imports inside functions.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest
import yaml

from core.result import Failure, Success

# Import app FIRST so its module-level imports resolve, then we can patch them.
import daemon.app as app_module


# ── helpers ──────────────────────────────────────────────────────────────────


class _FakeAdapter:
    """Stub OsAdapter for testing App Supervisor tray/register logic."""

    def __init__(self):
        self.register_calls: list[str] = []
        self.unregister_calls: int = 0
        self.tray_on_quit = None
        self.tray_state_provider = None
        self.tray_shown = False
        # Signal that show_tray was called (for synchronization)
        self.tray_ready = threading.Event()

    def register_at_login(self, app_path):
        self.register_calls.append(str(app_path))
        return Success(None)

    def unregister_at_login(self):
        self.unregister_calls += 1
        return Success(None)

    def show_tray(self, on_quit, state_provider):
        self.tray_on_quit = on_quit
        self.tray_state_provider = state_provider
        self.tray_shown = True
        self.tray_ready.set()
        # Do NOT block — return immediately for test
        return Success(None)


# ── fixture ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_adapter():
    """Return a fresh FakeAdapter for each test."""
    return _FakeAdapter()


# ── helper to create a fake async _run_with_stop ────────────────────────────


def _make_fake_run_with_stop(flag_list, started_event=None):
    """Return an async function that appends True to *flag_list* when called.
    If *started_event* is provided, it is set when the function is entered.
    """
    async def _fake(cfg, config_path, stop_event=None):
        flag_list.append(True)
        if started_event:
            started_event.set()
    return _fake


# ── fresh machine tests ─────────────────────────────────────────────────────


class TestFreshMachine:
    """Tests for the fresh-machine path: no config, no key → wizard."""

    def test_fresh_machine_invokes_wizard(self, tmp_path, monkeypatch, fake_adapter):
        """When config file is missing and key is absent, wizard is launched."""
        monkeypatch.setattr(
            app_module, "read_key",
            lambda: Failure(error="no key", recoverable=False, context={}),
        )
        monkeypatch.setattr(
            app_module, "load_key_into_env",
            lambda: Success(None),
        )
        monkeypatch.setattr(
            "daemon.os_glue.get_os_adapter", lambda: fake_adapter,
        )

        engine_called = []
        engine_started = threading.Event()
        monkeypatch.setattr(
            app_module, "_run_with_stop",
            _make_fake_run_with_stop(engine_called, engine_started),
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        from daemon.config import DaemonConfig
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        fake_cfg = DaemonConfig(
            vault_root=vault_dir,
            cloud_endpoint="http://localhost:8080",
            api_key="test-key",
            cache_path=str(tmp_path / "cache.json"),
        )
        monkeypatch.setattr(
            app_module, "load_daemon_config", lambda path: fake_cfg,
        )

        wizard_called = []
        monkeypatch.setattr(
            "daemon.wizard.run_wizard",
            lambda: wizard_called.append(True),
        )

        app_module.run_app()

        assert len(wizard_called) == 1, "Expected wizard to be invoked on fresh machine"
        # Wait for the engine thread to execute
        assert engine_started.wait(timeout=2), "Engine thread did not start"
        assert len(engine_called) == 1, "Expected engine to start after wizard"
        assert fake_adapter.tray_shown is True

    def test_fresh_machine_registers_at_login_after_successful_wizard(
        self, tmp_path, monkeypatch, fake_adapter
    ):
        """After a successful wizard, register_at_login is called once."""
        monkeypatch.setattr(
            app_module, "read_key",
            lambda: Failure(error="no key", recoverable=False, context={}),
        )
        monkeypatch.setattr(
            app_module, "load_key_into_env", lambda: Success(None),
        )
        monkeypatch.setattr(
            "daemon.os_glue.get_os_adapter", lambda: fake_adapter,
        )
        monkeypatch.setattr(
            app_module, "_run_with_stop", _make_fake_run_with_stop([], threading.Event()),
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        from daemon.config import DaemonConfig
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        fake_cfg = DaemonConfig(
            vault_root=vault_dir,
            cloud_endpoint="http://localhost:8080",
            api_key="test-key",
            cache_path=str(tmp_path / "cache.json"),
        )
        monkeypatch.setattr(
            app_module, "load_daemon_config", lambda path: fake_cfg,
        )

        wizard_called = []
        monkeypatch.setattr(
            "daemon.wizard.run_wizard",
            lambda: wizard_called.append(True),
        )

        app_module.run_app()

        assert len(fake_adapter.register_calls) == 1, (
            "Expected register_at_login to be called once after wizard"
        )
        assert fake_adapter.register_calls[0] == sys.executable


# ── already-set-up tests ────────────────────────────────────────────────────


class TestAlreadySetUp:
    """Tests for the already-set-up path: config present + key in vault."""

    def test_already_set_up_skips_wizard(self, tmp_path, monkeypatch, fake_adapter):
        """When config exists and key is readable, wizard is NOT invoked."""
        monkeypatch.setattr(
            app_module, "read_key", lambda: Success("test-api-key"),
        )
        monkeypatch.setattr(
            app_module, "load_key_into_env", lambda: Success(None),
        )
        monkeypatch.setattr(
            "daemon.os_glue.get_os_adapter", lambda: fake_adapter,
        )
        engine_called = []
        engine_started = threading.Event()
        monkeypatch.setattr(
            app_module, "_run_with_stop",
            _make_fake_run_with_stop(engine_called, engine_started),
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        config_dir = tmp_path / ".kms-daemon"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("vault_root: /tmp\ncloud_endpoint: http://x\n")

        from daemon.config import DaemonConfig
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        fake_cfg = DaemonConfig(
            vault_root=vault_dir,
            cloud_endpoint="http://localhost:8080",
            api_key="test-api-key",
            cache_path=str(tmp_path / "cache.json"),
        )
        monkeypatch.setattr(
            app_module, "load_daemon_config", lambda path: fake_cfg,
        )

        wizard_called = []
        monkeypatch.setattr(
            "daemon.wizard.run_wizard",
            lambda: wizard_called.append(True),
        )

        app_module.run_app()

        assert len(wizard_called) == 0, "Expected wizard to be skipped when already set up"
        # Wait for the engine thread to execute
        assert engine_started.wait(timeout=2), "Engine thread did not start"
        assert len(engine_called) == 1, "Expected engine to start"
        assert len(fake_adapter.register_calls) == 0, (
            "Expected register_at_login to NOT be called when already set up"
        )


# ── quit / stop tests ───────────────────────────────────────────────────────


class TestQuitStopsEngine:
    """Tests for the Quit → stop_event → engine shutdown path."""

    def test_quit_sets_stop_event(self, tmp_path, monkeypatch, fake_adapter):
        """When Quit is triggered, stop_event.set() is called."""
        monkeypatch.setattr(
            app_module, "read_key", lambda: Success("test-api-key"),
        )
        monkeypatch.setattr(
            app_module, "load_key_into_env", lambda: Success(None),
        )
        monkeypatch.setattr(
            "daemon.os_glue.get_os_adapter", lambda: fake_adapter,
        )

        # Capture the stop_event passed to _run_with_stop
        captured_stop_event = []
        engine_started = threading.Event()

        async def _fake_run_with_stop(cfg, config_path, stop_event):
            captured_stop_event.append(stop_event)
            engine_started.set()

        monkeypatch.setattr(
            app_module, "_run_with_stop", _fake_run_with_stop,
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        config_dir = tmp_path / ".kms-daemon"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("vault_root: /tmp\ncloud_endpoint: http://x\n")

        from daemon.config import DaemonConfig
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        fake_cfg = DaemonConfig(
            vault_root=vault_dir,
            cloud_endpoint="http://localhost:8080",
            api_key="test-api-key",
            cache_path=str(tmp_path / "cache.json"),
        )
        monkeypatch.setattr(
            app_module, "load_daemon_config", lambda path: fake_cfg,
        )

        app_module.run_app()

        # Wait for engine thread to execute
        assert engine_started.wait(timeout=2), "Engine thread did not start"
        assert len(captured_stop_event) == 1
        stop_event = captured_stop_event[0]
        assert stop_event is not None

        # Simulate Quit
        assert fake_adapter.tray_on_quit is not None
        fake_adapter.tray_on_quit()

        assert stop_event.is_set(), "Expected stop_event to be set after Quit"

    def test_state_provider_reports_alive(self, tmp_path, monkeypatch, fake_adapter):
        """The state_provider returns 'alive' while the engine thread is running."""
        monkeypatch.setattr(
            app_module, "read_key", lambda: Success("test-api-key"),
        )
        monkeypatch.setattr(
            app_module, "load_key_into_env", lambda: Success(None),
        )
        monkeypatch.setattr(
            "daemon.os_glue.get_os_adapter", lambda: fake_adapter,
        )

        # Engine thread: async function that blocks until signalled
        thread_started = threading.Event()
        thread_can_exit = threading.Event()

        async def _fake_run_with_stop(cfg, config_path, stop_event):
            thread_started.set()
            while not thread_can_exit.is_set():
                await asyncio.sleep(0.05)

        monkeypatch.setattr(
            app_module, "_run_with_stop", _fake_run_with_stop,
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        config_dir = tmp_path / ".kms-daemon"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("vault_root: /tmp\ncloud_endpoint: http://x\n")

        from daemon.config import DaemonConfig
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        fake_cfg = DaemonConfig(
            vault_root=vault_dir,
            cloud_endpoint="http://localhost:8080",
            api_key="test-api-key",
            cache_path=str(tmp_path / "cache.json"),
        )
        monkeypatch.setattr(
            app_module, "load_daemon_config", lambda path: fake_cfg,
        )

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(app_module.run_app)

            # Wait for engine to start AND tray to be shown
            assert thread_started.wait(timeout=5), "Engine thread did not start"
            assert fake_adapter.tray_ready.wait(timeout=5), "show_tray was not called"

            assert fake_adapter.tray_state_provider is not None
            state = fake_adapter.tray_state_provider()
            assert state == "alive", f"Expected 'alive', got {state!r}"

            # Clean up
            thread_can_exit.set()
            if fake_adapter.tray_on_quit:
                fake_adapter.tray_on_quit()
            future.result(timeout=5)


# ── half-written config tests ───────────────────────────────────────────────


class TestHalfWrittenConfig:
    """Tests for the half-written config → tray-error path."""

    def test_half_written_config_shows_tray_error(self, tmp_path, monkeypatch, fake_adapter):
        """When config loading raises ValueError, tray shows error state."""
        monkeypatch.setattr(
            app_module, "read_key", lambda: Success("test-api-key"),
        )
        monkeypatch.setattr(
            app_module, "load_key_into_env", lambda: Success(None),
        )
        monkeypatch.setattr(
            "daemon.os_glue.get_os_adapter", lambda: fake_adapter,
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        config_dir = tmp_path / ".kms-daemon"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("vault_root: /tmp\ncloud_endpoint: http://x\n")

        monkeypatch.setattr(
            app_module, "load_daemon_config",
            lambda path: (_ for _ in ()).throw(ValueError("half-written config: missing field")),
        )

        engine_called = []
        monkeypatch.setattr(
            app_module, "_run_with_stop", _make_fake_run_with_stop(engine_called),
        )

        app_module.run_app()

        assert len(engine_called) == 0, "Engine must NOT start on half-written config"
        assert fake_adapter.tray_shown is True
        assert fake_adapter.tray_state_provider is not None
        state = fake_adapter.tray_state_provider()
        assert "error" in state.lower(), f"Expected error state, got {state!r}"

    def test_yaml_error_shows_tray_error(self, tmp_path, monkeypatch, fake_adapter):
        """YAML syntax errors also route to tray-error, not crash."""
        monkeypatch.setattr(
            app_module, "read_key", lambda: Success("test-api-key"),
        )
        monkeypatch.setattr(
            app_module, "load_key_into_env", lambda: Success(None),
        )
        monkeypatch.setattr(
            "daemon.os_glue.get_os_adapter", lambda: fake_adapter,
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        config_dir = tmp_path / ".kms-daemon"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("invalid: [unclosed")

        monkeypatch.setattr(
            app_module, "load_daemon_config",
            lambda path: (_ for _ in ()).throw(yaml.YAMLError("syntax error")),
        )

        engine_called = []
        monkeypatch.setattr(
            app_module, "_run_with_stop", _make_fake_run_with_stop(engine_called),
        )

        app_module.run_app()

        assert len(engine_called) == 0
        assert fake_adapter.tray_shown is True
        assert fake_adapter.tray_state_provider is not None
        state = fake_adapter.tray_state_provider()
        assert "error" in state.lower()

    def test_validation_error_shows_tray_error(self, tmp_path, monkeypatch, fake_adapter):
        """Pydantic ValidationError routes to tray-error, not crash."""
        monkeypatch.setattr(
            app_module, "read_key", lambda: Success("test-api-key"),
        )
        monkeypatch.setattr(
            app_module, "load_key_into_env", lambda: Success(None),
        )
        monkeypatch.setattr(
            "daemon.os_glue.get_os_adapter", lambda: fake_adapter,
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        config_dir = tmp_path / ".kms-daemon"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("vault_root: /tmp\ncloud_endpoint: http://x\n")

        from pydantic import ValidationError
        try:
            from daemon.config import DaemonConfig
            DaemonConfig(vault_root="/not/a/path", cloud_endpoint="", api_key="k")
        except ValidationError as e:
            pydantic_error = e

        monkeypatch.setattr(
            app_module, "load_daemon_config",
            lambda path: (_ for _ in ()).throw(pydantic_error),
        )

        engine_called = []
        monkeypatch.setattr(
            app_module, "_run_with_stop", _make_fake_run_with_stop(engine_called),
        )

        app_module.run_app()

        assert len(engine_called) == 0
        assert fake_adapter.tray_shown is True
        state = fake_adapter.tray_state_provider()
        assert "error" in state.lower()


# ── wizard failure tests ────────────────────────────────────────────────────


class TestWizardFailure:
    """Tests for wizard failure/cancellation → no engine start."""

    def test_wizard_cancelled_does_not_start_engine(self, tmp_path, monkeypatch, fake_adapter):
        """When the user cancels the wizard, engine does NOT start."""
        monkeypatch.setattr(
            app_module, "read_key",
            lambda: Failure(error="no key", recoverable=False, context={}),
        )
        monkeypatch.setattr(
            app_module, "load_key_into_env",
            lambda: Success(None),
        )
        monkeypatch.setattr(
            "daemon.os_glue.get_os_adapter", lambda: fake_adapter,
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        engine_called = []
        monkeypatch.setattr(
            app_module, "_run_with_stop", _make_fake_run_with_stop(engine_called),
        )

        wizard_called = []
        monkeypatch.setattr(
            "daemon.wizard.run_wizard",
            lambda: wizard_called.append(True),
        )

        monkeypatch.setattr(
            app_module, "load_daemon_config",
            lambda path: (_ for _ in ()).throw(ValueError("no config after cancel")),
        )

        app_module.run_app()

        assert len(wizard_called) == 1
        assert len(engine_called) == 0
        assert fake_adapter.tray_shown is True
