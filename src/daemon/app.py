"""
daemon/app.py

App Supervisor — the single entry point for the desktop daemon.

Decides setup-vs-run, runs the Sync Engine on a background thread,
shows the system tray, and shuts down cleanly on Quit.

Implements spec component 5 (App Supervisor).
Behaviors: P6-SLICEB-08, P6-SLICEB-09, P6-SLICEB-10.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import yaml

from daemon.cli import _run_with_stop
from daemon.config import load_daemon_config
from daemon.secret_vault import load_key_into_env, read_key


def _show_error_tray(error_msg: str) -> None:
    """Show a tray icon indicating error state (non-blocking fallback)."""
    try:
        from daemon.os_glue import get_os_adapter

        adapter = get_os_adapter()
        adapter.show_tray(
            on_quit=lambda: None,
            state_provider=lambda: f"error: {error_msg}",
        )
    except Exception:
        # Last resort — can't even show tray
        pass


def run_app() -> None:
    """Launch the desktop daemon: setup check → engine → tray.

    1. Check if already set up (config file present + key in vault).
       - If NO: run the setup wizard, then register at login on success.
       - If YES: skip the wizard.
    2. Load the API key into the environment.
    3. Validate the config file.
    4. Start the sync engine on a worker thread with a stop event.
    5. Show the system tray on the main thread (blocks until Quit).
    """
    # ── 1. Setup check ──────────────────────────────────────────────────
    config_path = Path.home() / ".kms-daemon" / "config.yaml"
    key_result = read_key()

    setup_ok = config_path.exists() and key_result.is_success()

    if not setup_ok:
        # Fresh machine — run wizard
        from daemon.wizard import run_wizard

        run_wizard()

        # After wizard, register at login once
        try:
            from daemon.os_glue import get_os_adapter

            get_os_adapter().register_at_login(Path(sys.executable))
        except Exception:
            pass  # non-fatal — user can manually start

    # ── 2. Load key into env ────────────────────────────────────────────
    load_key_into_env()

    # ── 3. Validate config loads ────────────────────────────────────────
    try:
        cfg = load_daemon_config(config_path)
    except (ValueError, yaml.YAMLError) as e:
        # Half-written config — report via tray then exit
        _show_error_tray(str(e))
        return

    # ── 4. Start engine on worker thread ────────────────────────────────
    stop_event = asyncio.Event()

    def _engine_thread() -> None:
        asyncio.run(_run_with_stop(cfg, config_path, stop_event))

    thread = threading.Thread(target=_engine_thread, daemon=True)
    thread.start()

    # ── 5. Show tray on MAIN thread (blocks until Quit) ─────────────────
    from daemon.os_glue import get_os_adapter

    def _on_quit() -> None:
        # thread-safe: asyncio.Event.set() is safe to call from any thread
        stop_event.set()

    def _state_provider() -> str:
        if thread.is_alive():
            return "alive"
        return "stopped"

    get_os_adapter().show_tray(_on_quit, _state_provider)
