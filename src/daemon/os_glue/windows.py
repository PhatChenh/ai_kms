"""
daemon/os_glue/windows.py

Windows adapter implementing ``OsAdapter``.

- ``register_at_login`` writes a value to the Windows registry Run key
  (``HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``).
- ``unregister_at_login`` deletes the registry value.  Idempotent.
- ``show_tray`` uses ``pystray`` to create a tray icon with a Quit menu
  item that calls *on_quit*.  Must run on the MAIN thread.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from core.result import Failure, Result, Success

# ── Registry constants ───────────────────────────────────────────────────

REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_VALUE_NAME = "KMSDaemon"


# ── Tray icon generation (programmatic — no icon file) ───────────────────

def _create_tray_image() -> Image.Image:
    """Generate a 64×64 solid-colour square tray icon via PIL."""
    img = Image.new("RGB", (64, 64), color=(66, 133, 244))  # Google Blue
    draw = ImageDraw.Draw(img)
    # Draw a simple "K" letter indicator
    draw.text((18, 8), "K", fill="white")
    return img


# ── Adapter ──────────────────────────────────────────────────────────────


class WindowsAdapter:
    """Windows implementation of the ``OsAdapter`` Protocol."""

    def register_at_login(self, app_path: Path) -> Result[None]:
        """Add a registry Run key that starts the daemon at login.

        Args:
            app_path: Path to the Python interpreter.
        """
        import winreg

        command = f'"{app_path}" -m daemon'
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(
                    key, REG_VALUE_NAME, 0, winreg.REG_SZ, command
                )
        except OSError as exc:
            return Failure(
                error=f"Failed to write registry Run key: {exc}",
                recoverable=False,
                context={
                    "key": f"HKCU\\{REG_RUN_KEY}",
                    "value": REG_VALUE_NAME,
                },
            )
        return Success(None)

    def unregister_at_login(self) -> Result[None]:
        """Delete the registry Run key value.  Idempotent."""
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, REG_VALUE_NAME)
        except FileNotFoundError:
            return Success(None)  # already absent — idempotent
        except OSError as exc:
            return Failure(
                error=f"Failed to delete registry Run key: {exc}",
                recoverable=False,
                context={
                    "key": f"HKCU\\{REG_RUN_KEY}",
                    "value": REG_VALUE_NAME,
                },
            )
        return Success(None)

    def show_tray(
        self,
        on_quit: Callable[[], None],
        state_provider: Callable[[], str],
    ) -> Result[None]:
        """Create and run the pystray tray icon on the MAIN thread.

        Blocks on ``icon.run()`` until the Quit menu item is selected.
        """
        import pystray

        def _wrapped_quit(icon) -> None:
            on_quit()
            icon.stop()

        icon = pystray.Icon(
            "kms-daemon",
            _create_tray_image(),
            "AI-kms Daemon",
            menu=pystray.Menu(
                pystray.MenuItem("Quit", _wrapped_quit, default=True),
            ),
        )

        # Periodic state update — NOTE: icon.title assignment from a
        # background thread is not main-thread-safe on macOS (OQ-SB1).
        # This is deferred to Phase 5 integration testing.
        _timer_lock = threading.Lock()
        _active_timer: threading.Timer | None = None

        def _update_title() -> None:
            nonlocal _active_timer
            try:
                state = state_provider()
                icon.title = f"AI-kms Daemon — {state}"
            except Exception:
                icon.title = "AI-kms Daemon — error"

            if icon.visible:
                with _timer_lock:
                    _active_timer = threading.Timer(5.0, _update_title)
                    _active_timer.daemon = True
                    _active_timer.start()

        # Start the first timer shortly after the icon appears
        with _timer_lock:
            _active_timer = threading.Timer(1.0, _update_title)
            _active_timer.daemon = True
            _active_timer.start()

        try:
            icon.run()
        except Exception as exc:
            return Failure(
                error=f"Tray icon failed: {exc}",
                recoverable=False,
                context={"phase": "tray-startup"},
            )
        finally:
            with _timer_lock:
                if _active_timer is not None:
                    _active_timer.cancel()
        return Success(None)
