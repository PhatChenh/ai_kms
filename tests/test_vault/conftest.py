"""
tests/test_vault/conftest.py

Shared fixtures for all vault-layer tests.

`vault_root` builds an empty vault skeleton at a temp path and monkeypatches
core.config._CONFIG so every vault function that lazy-imports CONFIG
sees the temp vault root instead of the real one.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.config import VaultConfig


@pytest.fixture()
def vault_root(tmp_path: Path, monkeypatch) -> Path:
    """
    Empty vault skeleton at tmp_path/vault.
    Monkeypatches core.config._CONFIG so lazy CONFIG imports in vault/*
    resolve to a VaultConfig pointing at this temp root.
    """
    root = tmp_path / "vault"
    root.mkdir()
    for d in ["inbox", "Projects", "Domain", "Documentation",
              "Briefings", "Synthesis", "Archive"]:
        (root / d).mkdir()

    vc = VaultConfig(root=root)

    import core.config as cfg_module
    fake_config = MagicMock()
    fake_config.main.vault = vc
    monkeypatch.setattr(cfg_module, "_CONFIG", fake_config)

    return root
