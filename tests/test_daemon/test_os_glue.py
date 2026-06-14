"""
tests/test_daemon/test_os_glue.py

Tests for src/daemon/os_glue/ — OS-Glue Seam + adapter dispatch + Protocol conformance.

Covers:
  - Tracer bullet: get_os_adapter() dispatches on platform.system()
  - "Darwin" → MacOSAdapter
  - "Windows" → WindowsAdapter
  - Unsupported OS → RuntimeError
  - Both adapters satisfy OsAdapter Protocol (have all required methods)

Does NOT test real OS registration or tray rendering (manual-verify only).
"""

from __future__ import annotations

import platform

import pytest

from daemon.os_glue import OsAdapter, get_os_adapter
from daemon.os_glue.macos import MacOSAdapter
from daemon.os_glue.windows import WindowsAdapter


# ── Tracer bullet: dispatch returns correct adapter ─────────────────────

def test_get_os_adapter_darwin_returns_macos_adapter(monkeypatch) -> None:
    """On Darwin, get_os_adapter() returns a MacOSAdapter."""
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    adapter = get_os_adapter()
    assert isinstance(adapter, MacOSAdapter), (
        f"Expected MacOSAdapter on Darwin, got {type(adapter).__name__}"
    )


def test_get_os_adapter_windows_returns_windows_adapter(monkeypatch) -> None:
    """On Windows, get_os_adapter() returns a WindowsAdapter."""
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    adapter = get_os_adapter()
    assert isinstance(adapter, WindowsAdapter), (
        f"Expected WindowsAdapter on Windows, got {type(adapter).__name__}"
    )


def test_get_os_adapter_unsupported_os_raises_runtime_error(monkeypatch) -> None:
    """An unsupported OS raises RuntimeError with a clear message."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="unsupported OS"):
        get_os_adapter()


# ── Protocol conformance: adapters have all required methods ────────────

def test_macos_adapter_satisfies_os_adapter_protocol() -> None:
    """MacOSAdapter satisfies the OsAdapter Protocol."""
    adapter = MacOSAdapter()
    assert isinstance(adapter, OsAdapter), (
        "MacOSAdapter does not satisfy OsAdapter Protocol"
    )
    assert callable(adapter.register_at_login)
    assert callable(adapter.unregister_at_login)
    assert callable(adapter.show_tray)


def test_windows_adapter_satisfies_os_adapter_protocol() -> None:
    """WindowsAdapter satisfies the OsAdapter Protocol."""
    adapter = WindowsAdapter()
    assert isinstance(adapter, OsAdapter), (
        "WindowsAdapter does not satisfy OsAdapter Protocol"
    )
    assert callable(adapter.register_at_login)
    assert callable(adapter.unregister_at_login)
    assert callable(adapter.show_tray)
