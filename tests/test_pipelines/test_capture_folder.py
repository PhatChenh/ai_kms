"""Tests for pipelines/capture.py::capture_folder() — Phase 4.2 Folder Handling.

capture_folder is an entry point (like capture_file), not a pipeline stage.
It classifies a dropped folder, writes a batches row, and delegates per-file
capture to capture_file.

LLM calls are mocked. Real temp vault + real SQLite DB via conftest fixtures.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.config import Thresholds
from core.result import Failure, Success
from llm.provider import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_old_file(path: Path, content: str = "Some content.") -> Path:
    """Write a file and backdate mtime past cooldown so capture_file accepts it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    old = time.time() - 300
    os.utime(path, (old, old))
    return path


def _classify_response(target_type: str, target_name: str, confidence: float) -> Success:
    """A Success(LLMResponse) carrying a classify_folder JSON verdict."""
    payload = json.dumps(
        {"target_type": target_type, "target_name": target_name, "confidence": confidence}
    )
    return Success(LLMResponse(content=payload, model="test", usage={}))


@pytest.fixture()
def folder_ctx(pipeline_ctx):
    """pipeline_ctx with a real classify ConfidenceBand wired onto config.thresholds.

    pipeline_ctx.config is a MagicMock, so config.thresholds must be set to a real
    Thresholds for capture_folder's routing band to behave deterministically.
    """
    pipeline_ctx.config.thresholds = Thresholds()  # global band: auto=0.85, suggest=0.60
    return pipeline_ctx


def _batches_rows(db_path: Path) -> list[dict]:
    """Read all batches rows as dicts (test-only DB inspection)."""
    from storage.db import get_connection

    with get_connection(db_path) as conn:
        conn.row_factory = lambda c, r: {d[0]: r[i] for i, d in enumerate(c.description)}
        return list(conn.execute("SELECT * FROM batches"))


# ===========================================================================
# Tracer bullet — inbox AUTO confidence moves folder and captures files
# ===========================================================================


@pytest.mark.asyncio
async def test_inbox_auto_confidence_moves_folder_and_captures_files(
    folder_ctx, vault_root, monkeypatch
):
    from pipelines.capture import capture_folder

    folder = vault_root / "inbox" / "research-drop"
    _make_old_file(folder / "a.md", "# A\n\nbody a")
    _make_old_file(folder / "b.md", "# B\n\nbody b")

    provider = _UnifiedProvider("project", "Alpha", 0.95)
    monkeypatch.setattr(
        "pipelines.capture.get_provider", lambda task, config: provider
    )

    result = await capture_folder(folder, context=folder_ctx)

    assert isinstance(result, Success), f"expected Success, got {result}"

    rows = _batches_rows(folder_ctx.db_path)
    assert len(rows) == 1
    assert rows[0]["destination_type"] == "project"
    assert rows[0]["destination_name"] == "Alpha"
    assert rows[0]["file_count"] == 2

    # Folder was moved out of inbox under Projects/ (spec: shutil.move to
    # destination.parent → destination.parent / folder.name).
    assert not folder.exists()
    moved = vault_root / "Projects" / "research-drop"
    assert moved.exists()
    # Files were captured (renamed by the rename gate) inside the moved folder.
    captured_md = list(moved.glob("*.md"))
    assert len(captured_md) == 2


# ===========================================================================
# Empty folder
# ===========================================================================


@pytest.mark.asyncio
async def test_empty_folder_returns_empty_success(folder_ctx, vault_root, monkeypatch):
    from pipelines.capture import capture_folder

    folder = vault_root / "inbox" / "empty-drop"
    folder.mkdir(parents=True)

    called = {"llm": False}

    def _no_llm(task, config):
        called["llm"] = True
        raise AssertionError("LLM must not be called for an empty folder")

    monkeypatch.setattr("pipelines.capture.get_provider", _no_llm)

    result = await capture_folder(folder, context=folder_ctx)

    assert isinstance(result, Success)
    assert result.value == []
    assert called["llm"] is False
    assert _batches_rows(folder_ctx.db_path) == []


# ===========================================================================
# SUGGEST band — no folder move, PENDING_REVIEW
# ===========================================================================


@pytest.mark.asyncio
async def test_inbox_suggest_confidence_no_folder_move(folder_ctx, vault_root, monkeypatch):
    from pipelines.capture import capture_folder

    folder = vault_root / "inbox" / "maybe-drop"
    _make_old_file(folder / "a.md", "# A\n\nbody")

    provider = AsyncMock()
    provider.complete.return_value = _classify_response("domain", "Engineering", 0.75)
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_folder(folder, context=folder_ctx)

    assert isinstance(result, Success)
    assert result.value == []
    # Folder NOT moved.
    assert folder.exists()
    rows = _batches_rows(folder_ctx.db_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "PENDING_REVIEW"
    # LLM called exactly once (classify only; no per-file capture).
    assert provider.complete.await_count == 1


# ===========================================================================
# CLUELESS band — no folder move, CLUELESS batch
# ===========================================================================


@pytest.mark.asyncio
async def test_inbox_clueless_no_folder_move(folder_ctx, vault_root, monkeypatch):
    from pipelines.capture import capture_folder

    folder = vault_root / "inbox" / "mystery-drop"
    _make_old_file(folder / "a.md", "# A\n\nbody")

    provider = _UnifiedProvider("project", "Alpha", 0.3)
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_folder(folder, context=folder_ctx)

    assert isinstance(result, Success)
    assert result.value == []
    # Folder NOT moved (stays in inbox).
    assert folder.exists()
    rows = _batches_rows(folder_ctx.db_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "CLUELESS"


# ===========================================================================
# Project drop — skips LLM, confidence 1.0
# ===========================================================================


@pytest.mark.asyncio
async def test_project_drop_skips_llm(folder_ctx, vault_root, monkeypatch):
    from pipelines.capture import capture_folder

    folder = vault_root / "Projects" / "Alpha" / "sub-drop"
    _make_old_file(folder / "a.md", "# A\n\nbody")

    capture_only = AsyncMock()
    capture_only.complete.return_value = Success(
        LLMResponse(content=json.dumps({"title": "T", "tags": ["test"]}), model="t", usage={})
    )
    monkeypatch.setattr(
        "pipelines.capture.get_provider", lambda task, config: capture_only
    )

    # Spy on PROMPTS lookups: classify_folder must never be fetched for a path-routed drop.
    from pipelines import capture as capture_mod

    looked_up: list[str] = []

    class _SpyPrompts(dict):
        def __getitem__(self, key):
            looked_up.append(key)
            return super().__getitem__(key)

    monkeypatch.setattr(capture_mod, "PROMPTS", _SpyPrompts(capture_mod.PROMPTS))

    result = await capture_folder(folder, context=folder_ctx)

    assert "classify_folder" not in looked_up, "classify_folder used for a project drop"

    assert isinstance(result, Success)
    rows = _batches_rows(folder_ctx.db_path)
    assert len(rows) == 1
    assert rows[0]["confidence"] == 1.0
    assert rows[0]["destination_type"] == "project"
    assert rows[0]["destination_name"] == "Alpha"


# ===========================================================================
# Partial failure — one file fails, batch marked PARTIAL
# ===========================================================================


@pytest.mark.asyncio
async def test_partial_failure_marks_batch_partial(folder_ctx, vault_root, monkeypatch):
    from pipelines import capture as capture_mod
    from pipelines.capture import capture_folder

    folder = vault_root / "Projects" / "Alpha" / "partial-drop"
    good = _make_old_file(folder / "good.md", "# Good\n\nbody")
    bad = _make_old_file(folder / "bad.md", "# Bad\n\nbody")

    provider = AsyncMock()
    provider.complete.return_value = Success(
        LLMResponse(content=json.dumps({"title": "T", "tags": ["test"]}), model="t", usage={})
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    real_capture_file = capture_mod.capture_file

    async def flaky_capture_file(path, ctx):
        if path.name == "bad.md":
            return Failure(error="file not found", recoverable=True, context={})
        return await real_capture_file(path, ctx)

    monkeypatch.setattr("pipelines.capture.capture_file", flaky_capture_file)

    result = await capture_folder(folder, context=folder_ctx)

    assert isinstance(result, Success)
    # Only the good file produced an outcome.
    assert len(result.value) == 1
    rows = _batches_rows(folder_ctx.db_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "PARTIAL"


class _UnifiedProvider:
    """Single provider whose first complete() call returns a classify verdict and
    all subsequent calls return capture-pipeline JSON (usable as summary + metadata).

    Sequence per folder run:
      1. classify_folder        → classify JSON
      2..n per-file capture      → summary / extract_metadata JSON
    """

    def __init__(self, target_type: str, target_name: str, confidence: float):
        self._verdict = (target_type, target_name, confidence)
        self._n = 0

    async def complete(self, system, user):
        self._n += 1
        if self._n == 1:
            return _classify_response(*self._verdict)
        return Success(
            LLMResponse(
                content=json.dumps({"title": "Captured", "tags": ["test"]}),
                model="test",
                usage={},
            )
        )
