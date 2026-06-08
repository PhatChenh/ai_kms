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


def _classify_response(
    target_type: str, target_name: str, confidence: float
) -> Success:
    """A Success(LLMResponse) carrying a classify_folder JSON verdict."""
    payload = json.dumps(
        {
            "target_type": target_type,
            "target_name": target_name,
            "confidence": confidence,
        }
    )
    return Success(LLMResponse(content=payload, model="test", usage={}))


@pytest.fixture()
def folder_ctx(pipeline_ctx):
    """pipeline_ctx with a real classify ConfidenceBand wired onto config.thresholds.

    pipeline_ctx.config is a MagicMock, so config.thresholds must be set to a real
    Thresholds for capture_folder's routing band to behave deterministically.
    """
    pipeline_ctx.config.thresholds = (
        Thresholds()
    )  # global band: auto=0.85, suggest=0.60
    return pipeline_ctx


def _batches_rows(db_path: Path) -> list[dict]:
    """Read all batches rows as dicts (test-only DB inspection)."""
    from storage.db import get_connection

    with get_connection(db_path) as conn:
        conn.row_factory = lambda c, r: {
            d[0]: r[i] for i, d in enumerate(c.description)
        }
        return list(conn.execute("SELECT * FROM batches"))


def _documents_rows(db_path: Path) -> list[dict]:
    """Read all documents rows as dicts (test-only DB inspection)."""
    from storage.db import get_connection

    with get_connection(db_path) as conn:
        conn.row_factory = lambda c, r: {
            d[0]: r[i] for i, d in enumerate(c.description)
        }
        return list(conn.execute("SELECT * FROM documents"))


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
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

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
async def test_inbox_suggest_confidence_no_folder_move(
    folder_ctx, vault_root, monkeypatch
):
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
        LLMResponse(
            content=json.dumps({"title": "T", "tags": ["test"]}), model="t", usage={}
        )
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
        LLMResponse(
            content=json.dumps({"title": "T", "tags": ["test"]}), model="t", usage={}
        )
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


# ===========================================================================
# C1 — batch_id is written onto each captured document row
# ===========================================================================


@pytest.mark.asyncio
async def test_project_drop_writes_batch_id_on_document_rows(
    folder_ctx, vault_root, monkeypatch
):
    """Folder capture: every captured documents row carries the batch's id (C1).

    Regression for batch_id never being threaded into the document-write sites —
    Phase 7 reconcile_stale_batch_refs JOINs documents.batch_id and got zero rows.
    """
    from pipelines.capture import capture_folder

    folder = vault_root / "Projects" / "Alpha" / "sub-drop"
    _make_old_file(folder / "a.md", "# A\n\nbody a")
    _make_old_file(folder / "b.md", "# B\n\nbody b")

    provider = AsyncMock()
    provider.complete.return_value = Success(
        LLMResponse(
            content=json.dumps({"title": "T", "tags": ["test"]}), model="t", usage={}
        )
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_folder(folder, context=folder_ctx)
    assert isinstance(result, Success)

    batch_rows = _batches_rows(folder_ctx.db_path)
    assert len(batch_rows) == 1
    batch_id = batch_rows[0]["batch_id"]

    doc_rows = _documents_rows(folder_ctx.db_path)
    assert len(doc_rows) == 2
    assert all(r["batch_id"] == batch_id for r in doc_rows), (
        f"Expected every documents.batch_id == {batch_id}, got {[r['batch_id'] for r in doc_rows]}"
    )


@pytest.mark.asyncio
async def test_single_file_capture_leaves_batch_id_null(
    folder_ctx, vault_root, monkeypatch
):
    """capture_file (no batch context) must leave documents.batch_id NULL (C1)."""
    from pipelines.capture import capture_file

    note = _make_old_file(vault_root / "inbox" / "solo.md", "# Solo\n\nbody")

    provider = AsyncMock()
    provider.complete.return_value = Success(
        LLMResponse(
            content=json.dumps({"title": "T", "tags": ["test"]}), model="t", usage={}
        )
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_file(note, folder_ctx)
    assert isinstance(result, Success)

    doc_rows = _documents_rows(folder_ctx.db_path)
    assert len(doc_rows) == 1
    assert doc_rows[0]["batch_id"] is None, (
        f"Single-file capture must leave batch_id NULL, got {doc_rows[0]['batch_id']}"
    )


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


# ===========================================================================
# TD-049 NFC Fix — folder_path NFC normalization
# ===========================================================================


@pytest.mark.asyncio
async def test_folder_path_nfc_normalized_on_insert(
    folder_ctx, vault_root, monkeypatch
):
    """capture_folder writes NFC-normalized folder_path even when folder has NFD name.

    macOS filesystem normalizes to NFD; without NFC normalization the batch
    folder_path would mismatch the NFC-normalized lookup in capture_file,
    creating duplicate batch rows (TD-049).
    """
    import unicodedata

    from pipelines.capture import capture_folder

    # "Phật" — NFD form has 6 codepoints (NFC has 5)
    nfc_name = "Phật"
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    assert nfc_name != nfd_name, "NFD must differ from NFC for this test to be valid"

    # Create folder with NFD name in Projects/Alpha/ so we hit the Case B
    # path (no LLM, confidence 1.0) — simplest code path with _insert_batch.
    (vault_root / "Projects" / "Alpha").mkdir(parents=True, exist_ok=True)
    folder = vault_root / "Projects" / "Alpha" / nfd_name
    _make_old_file(folder / "a.md", "# A\n\nbody a")

    provider = AsyncMock()
    provider.complete.return_value = Success(
        LLMResponse(
            content=json.dumps({"title": "T", "tags": ["test"]}), model="t", usage={}
        )
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_folder(folder, context=folder_ctx)
    assert isinstance(result, Success)

    rows = _batches_rows(folder_ctx.db_path)
    assert len(rows) == 1
    assert rows[0]["destination_type"] == "project"
    assert rows[0]["destination_name"] == "Alpha"

    # The folder_path column must be NFC-normalized
    actual = rows[0]["folder_path"]
    expected_nfc = f"Projects/Alpha/{nfc_name}"
    assert actual == expected_nfc, (
        f"folder_path must be NFC-normalized.\n"
        f"  Expected: {expected_nfc!r} (len={len(expected_nfc)})\n"
        f"  Got:      {actual!r} (len={len(actual)})"
    )


@pytest.mark.asyncio
async def test_folder_path_nfc_matches_batch_lookup(
    folder_ctx, vault_root, monkeypatch
):
    """capture_file batch-stamp lookup finds the batch created by capture_folder.

    End-to-end proof of TD-049 fix: when capture_folder inserts with NFC
    folder_path and capture_file later looks up the same folder with NFC,
    they match — no duplicate batch rows are created.
    """
    import unicodedata

    from pipelines.capture import capture_file, capture_folder

    nfc_name = "Phật"
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    assert nfc_name != nfd_name, "NFD must differ from NFC for this test to be valid"

    # Create folder with NFD name in Projects/Alpha/ (Case B — no LLM).
    (vault_root / "Projects" / "Alpha").mkdir(parents=True, exist_ok=True)
    folder = vault_root / "Projects" / "Alpha" / nfd_name
    _make_old_file(folder / "first.md", "# First\n\nbody")

    provider = AsyncMock()
    provider.complete.return_value = Success(
        LLMResponse(
            content=json.dumps({"title": "T", "tags": ["test"]}), model="t", usage={}
        )
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    # Step 1: capture_folder inserts batch with NFC folder_path (the fix).
    result = await capture_folder(folder, context=folder_ctx)
    assert isinstance(result, Success)

    batch_rows_before = _batches_rows(folder_ctx.db_path)
    assert len(batch_rows_before) == 1
    batch_id = batch_rows_before[0]["batch_id"]

    # Step 2: place a second file and run capture_file.
    # capture_file's batch-stamp pre-step normalizes to NFC for lookup,
    # so it should find the existing batch (no duplicate).
    second_file = _make_old_file(folder / "second.md", "# Second\n\nbody")

    # Provider already returns valid capture JSON from step 1's mock.
    result2 = await capture_file(second_file, folder_ctx)
    assert isinstance(result2, Success)

    # Verify: still exactly ONE batch row (no duplicate).
    batch_rows_after = _batches_rows(folder_ctx.db_path)
    assert len(batch_rows_after) == 1, (
        f"Expected exactly 1 batch row after capture_file lookup, "
        f"got {len(batch_rows_after)}. TD-049 NFC mismatch may have "
        f"created a duplicate."
    )
    assert batch_rows_after[0]["batch_id"] == batch_id

    # Verify: the second file's documents row got the existing batch_id.
    doc_rows = _documents_rows(folder_ctx.db_path)
    second_doc = [r for r in doc_rows if r["vault_path"].endswith("second.md")]
    assert len(second_doc) == 1
    assert second_doc[0]["batch_id"] == batch_id, (
        f"capture_file must stamp batch_id={batch_id} from existing batch, "
        f"got batch_id={second_doc[0]['batch_id']}"
    )
