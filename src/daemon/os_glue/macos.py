"""
daemon/os_glue/macos.py

macOS adapter implementing ``OsAdapter``.

- ``register_at_login`` writes a LaunchAgent plist under
  ``~/Library/LaunchAgents/com.kms.daemon.plist``.
- ``unregister_at_login`` deletes the plist file.
- ``show_tray`` uses ``pystray`` to create a tray icon with a Quit menu
  item that calls *on_quit*.  Must run on the MAIN thread.
"""

from __future__ import annotations

import plistlib
import threading
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from core.result import Failure, Result, Success

# ── LaunchAgent constants ────────────────────────────────────────────────

PLIST_LABEL = "com.kms.daemon"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def _plist_content(python_path: str) -> bytes:
    """Return the binary plist for the LaunchAgent (uses stdlib plistlib)."""
    plist: dict = {
        "Label": PLIST_LABEL,
        "ProgramArguments": [python_path, "-m", "daemon"],
        "RunAtLoad": True,
        "KeepAlive": False,
    }
    return plistlib.dumps(plist)


# ── Tray icon generation (programmatic — no icon file) ───────────────────

def _create_tray_image() -> Image.Image:
    """Generate a 64×64 solid-colour square tray icon via PIL."""
    img = Image.new("RGB", (64, 64), color=(66, 133, 244))  # Google Blue
    draw = ImageDraw.Draw(img)
    # Draw a simple "K" letter indicator
    draw.text((18, 8), "K", fill="white")
    return img


# ── Adapter ──────────────────────────────────────────────────────────────


class MacOSAdapter:
    """macOS implementation of the ``OsAdapter`` Protocol."""

    def register_at_login(self, app_path: Path) -> Result[None]:
        """Write a LaunchAgent plist that starts the daemon at login.

        Args:
            app_path: Path to the Python interpreter.
        """
        try:
            plist_dir = PLIST_PATH.parent
            plist_dir.mkdir(parents=True, exist_ok=True)
            content = _plist_content(str(app_path))
            PLIST_PATH.write_bytes(content)
        except OSError as exc:
            return Failure(
                error=f"Failed to write LaunchAgent plist: {exc}",
                recoverable=False,
                context={"path": str(PLIST_PATH)},
            )
        return Success(None)

    def unregister_at_login(self) -> Result[None]:
        """Delete the LaunchAgent plist file.  Idempotent."""
        try:
            PLIST_PATH.unlink(missing_ok=True)
        except OSError as exc:
            return Failure(
                error=f"Failed to delete LaunchAgent plist: {exc}",
                recoverable=False,
                context={"path": str(PLIST_PATH)},
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

            # Schedule next update if the icon is still running
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
