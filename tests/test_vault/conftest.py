"""
tests/test_vault/conftest.py

Shared fixtures for all vault-layer tests.

`vault_root` builds an empty vault skeleton at a temp path and monkeypatches
core.config._CONFIG so every vault function that lazy-imports CONFIG
sees the temp vault root instead of the real one.

`_cancel_leaked_timers` (autouse) cancels watcher debounce timers that survive
test teardown, preventing PytestUnhandledThreadExceptionWarning (TD-050).
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.config import VaultConfig


@pytest.fixture()
def vault_config(vault_root: Path) -> VaultConfig:
    """VaultConfig pointing at the temp vault root (depends on vault_root)."""
    return VaultConfig(root=vault_root)


@pytest.fixture()
def vault_root(tmp_path: Path, monkeypatch) -> Path:
    """
    Empty vault skeleton at tmp_path/vault.
    Monkeypatches core.config._CONFIG so lazy CONFIG imports in vault/*
    resolve to a VaultConfig pointing at this temp root.
    """
    root = tmp_path / "vault"
    root.mkdir()
    for d in [
        "inbox",
        "Projects",
        "Domain",
        "Documentation",
        "Briefings",
        "Synthesis",
        "Archive",
    ]:
        (root / d).mkdir()

    vc = VaultConfig(root=root)

    import core.config as cfg_module

    fake_config = MagicMock()
    fake_config.main.vault = vc
    monkeypatch.setattr(cfg_module, "_CONFIG", fake_config)

    return root


@pytest.fixture(autouse=True)
def _cancel_leaked_timers(monkeypatch):
    """Cancel watcher debounce timers after each test (TD-050).

    Watcher tests create _VaultEventHandler instances with short debounce
    timers (0.01 s). If a timer fires after the test function returns but
    before pytest fully tears down tmp_path, the callback can see a stale
    vault root or a CONFIG singleton that no longer matches, producing
    PytestUnhandledThreadExceptionWarning.

    This fixture intercepts every threading.Timer created during the test
    and cancels all of them on teardown, regardless of whether they have
    already fired.
    """
    timers: list[threading.Timer] = []
    _real_init = threading.Timer.__init__

    def _tracking_init(self, *args, **kwargs):
        _real_init(self, *args, **kwargs)
        timers.append(self)

    monkeypatch.setattr(threading.Timer, "__init__", _tracking_init)
    yield
    for t in reversed(timers):
        try:
            t.cancel()
        except Exception:
            pass
