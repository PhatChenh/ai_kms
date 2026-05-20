"""Fixtures for pipelines tests.

`pipeline_ctx` provides a PipelineContext backed by a real temp vault + SQLite DB.
Monkeypatches core.config._CONFIG so lazy CONFIG imports resolve to the temp vault.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.config import CaptureConfig, HandlersConfig, VaultConfig
from core.pipeline import PipelineContext
from core.result import Success
from structlog.contextvars import bind_contextvars, clear_contextvars


@pytest.fixture()
def vault_root(tmp_path: Path) -> Path:
    """Empty vault skeleton at tmp_path/vault."""
    root = tmp_path / "vault"
    root.mkdir()
    for d in ["inbox", "Projects", "Domain", "Documentation",
              "Briefings", "Synthesis", "Archive", "attachment"]:
        (root / d).mkdir()
    return root


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Initialised SQLite DB at tmp_path/test.db."""
    from storage.db import init_db
    path = tmp_path / "test.db"
    result = init_db(path)
    assert isinstance(result, Success), f"DB init failed: {result}"
    return path


@pytest.fixture()
def pipeline_ctx(vault_root: Path, db_path: Path, monkeypatch):  # type: ignore[return]
    """PipelineContext with real temp vault + DB, stubbed LLM config.

    Monkeypatches core.config._CONFIG so vault path helpers resolve correctly.
    """
    vc = VaultConfig(root=vault_root)

    config = MagicMock()
    config.vault = vc
    config.capture = CaptureConfig(cooldown_seconds=60, max_urls_per_note=3)
    config.handlers = HandlersConfig(  # type: ignore[call-arg]
        max_file_size_bytes=50 * 1024 * 1024,
        max_web_fetch_bytes=10 * 1024 * 1024,
        web_fetch_timeout_seconds=30,
        dns_resolve_timeout_seconds=5,
        max_redirects=5,
    )

    import core.config as cfg_module
    fake_full = MagicMock()
    fake_full.main = config
    monkeypatch.setattr(cfg_module, "_CONFIG", fake_full)

    cid = "test-correlation-id"
    clear_contextvars()
    bind_contextvars(correlation_id=cid)

    yield PipelineContext(config=config, correlation_id=cid, db_path=db_path)

    clear_contextvars()
