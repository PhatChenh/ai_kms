"""
daemon/os_glue/__init__.py

OS-Glue Seam — shared interface for per-OS behaviours (auto-start at login
and tray registration). Exactly two real implementations exist (macOS,
Windows), selected at startup by a plain ``platform.system()`` branch.

.. code-block::

   get_os_adapter()  ── platform.system() ──┐
                                             │
   OsAdapter (Protocol):                     │
     register_at_login()                     │
     unregister_at_login()                   │
     show_tray(on_quit, state_provider)      │
                     ┌───────────────────────┴──────────────────┐
                     ▼                                          ▼
   macos.py    LaunchAgent + pystray (MAIN thread)   windows.py
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from core.result import Result

# ── Protocol ─────────────────────────────────────────────────────────────


@runtime_checkable
class OsAdapter(Protocol):
    """Shared interface for OS-specific behaviours.

    Two methods for auto-start-at-login, one for the system tray.
    Callers depend on this Protocol — never on a concrete adapter.
    """

    def register_at_login(self, app_path: Path) -> Result[None]:
        """Register the application to start at user login.

        Args:
            app_path: Path to the Python interpreter (or bundled executable)
                that should be launched.

        Returns:
            Success(None) if the registration was created.
            Failure if the registration could not be written.
        """
        ...

    def unregister_at_login(self) -> Result[None]:
        """Remove the start-at-login registration.

        Idempotent — safe to call when no registration exists.

        Returns:
            Success(None) if registration was removed or was already absent.
            Failure if the operation failed.
        """
        ...

    def show_tray(
        self,
        on_quit: Callable[[], None],
        state_provider: Callable[[], str],
    ) -> Result[None]:
        """Show the system-tray icon and block until quit.

        MUST run on the MAIN thread (pystray requirement).

        Args:
            on_quit: Called when the user selects "Quit" from the tray menu.
            state_provider: Called periodically to get the current state
                string (e.g. "alive", "syncing", "error: ...").

        Returns:
            Success(None) when the tray has been shut down cleanly.
            Failure if the tray could not be created.
        """
        ...


# ── Dispatch ─────────────────────────────────────────────────────────────

# COUPLING: Linux deferred — add a branch here and a new adapter file when
# Linux support arrives.  The two-case if/else is deliberate (Option C
# self-registering registry rejected — 1-pattern-for-2-cases).
def get_os_adapter() -> OsAdapter:
    """Return the concrete ``OsAdapter`` for the current operating system.

    Dispatches on ``platform.system()``:
    - ``"Darwin"``  → ``MacOSAdapter``
    - ``"Windows"`` → ``WindowsAdapter``
    - anything else → ``RuntimeError``

    Returns:
        An ``OsAdapter`` instance for the current OS.

    Raises:
        RuntimeError: If the OS is unsupported.
    """
    system = platform.system()
    if system == "Darwin":
        from daemon.os_glue.macos import MacOSAdapter  # lazy import

        return MacOSAdapter()
    elif system == "Windows":
        from daemon.os_glue.windows import WindowsAdapter  # lazy import

        return WindowsAdapter()
    else:
        # COUPLING: Linux deferred — add a branch here
        raise RuntimeError(
            f"unsupported OS: {system!r}. "
            "Expected 'Darwin' or 'Windows'."
        )
