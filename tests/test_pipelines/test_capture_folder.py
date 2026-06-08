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
    from unittest.mock import AsyncMock

    from pipelines.capture import capture_folder
    from pipelines.classify import ClassifyResult

    folder = vault_root / "inbox" / "research-drop"
    _make_old_file(folder / "a.md", "# A\n\nbody a")
    _make_old_file(folder / "b.md", "# B\n\nbody b")

    # Mock classify() to return AUTO confidence (0.95) for project Alpha.
    mock_classify = AsyncMock(
        return_value=Success(
            ClassifyResult(
                project="Alpha",
                domains=["finance"],
                primary_domain="Finance",
                confidence=0.95,
                reasoning="Clear project match.",
            )
        )
    )
    monkeypatch.setattr("pipelines.capture.classify", mock_classify)

    # Per-file capture needs get_provider (capture_file internals).
    provider = AsyncMock()
    provider.complete.return_value = Success(
        LLMResponse(
            content=json.dumps({"title": "T", "tags": ["test"]}),
            model="t",
            usage={},
        )
    )
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
    from unittest.mock import AsyncMock

    from pipelines.capture import capture_folder
    from pipelines.classify import ClassifyResult

    folder = vault_root / "inbox" / "maybe-drop"
    _make_old_file(folder / "a.md", "# A\n\nbody")

    # classify returns confidence 0.75 → SUGGEST band
    mock_classify = AsyncMock(
        return_value=Success(
            ClassifyResult(
                project=None,
                domains=["engineering"],
                primary_domain="Engineering",
                confidence=0.75,
                reasoning="Likely Engineering domain.",
            )
        )
    )
    monkeypatch.setattr("pipelines.capture.classify", mock_classify)

    result = await capture_folder(folder, context=folder_ctx)

    assert isinstance(result, Success)
    assert result.value == []
    # Folder NOT moved.
    assert folder.exists()
    rows = _batches_rows(folder_ctx.db_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "PENDING_REVIEW"
    # classify() called exactly once; no per-file capture for SUGGEST.
    mock_classify.assert_called_once()


# ===========================================================================
# CLUELESS band — no folder move, CLUELESS batch
# ===========================================================================


@pytest.mark.asyncio
async def test_inbox_clueless_no_folder_move(folder_ctx, vault_root, monkeypatch):
    from unittest.mock import AsyncMock

    from pipelines.capture import capture_folder
    from pipelines.classify import ClassifyResult

    folder = vault_root / "inbox" / "mystery-drop"
    _make_old_file(folder / "a.md", "# A\n\nbody")

    # classify returns confidence 0.3 → CLUELESS band
    mock_classify = AsyncMock(
        return_value=Success(
            ClassifyResult(
                project=None,
                domains=[],
                primary_domain=None,
                confidence=0.3,
                reasoning="Cannot determine — too vague.",
            )
        )
    )
    monkeypatch.setattr("pipelines.capture.classify", mock_classify)

    # Per-file capture needs get_provider for the CLUELESS path
    provider = AsyncMock()
    provider.complete.return_value = Success(
        LLMResponse(
            content=json.dumps({"title": "T", "tags": ["test"]}),
            model="t",
            usage={},
        )
    )
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


# ===========================================================================
# Phase 9 — Folder Migration (unified classify engine)
# ===========================================================================


class TestFolderMigrationUnifiedEngine:
    """P2-CIC Phase 9 — capture_folder Case A uses classify() not classify_folder."""

    @pytest.mark.asyncio
    async def test_folder_classify_uses_unified_engine(
        self, folder_ctx, vault_root, monkeypatch
    ):
        """Drop a folder in inbox: classify() is called and returns ClassifyResult."""
        from unittest.mock import AsyncMock

        from pipelines.capture import capture_folder
        from pipelines.classify import ClassifyResult

        folder = vault_root / "inbox" / "migrate-test"
        _make_old_file(folder / "a.md", "# A\n\nbody a")

        mock_result = Success(
            ClassifyResult(
                project="Alpha",
                domains=["finance"],
                primary_domain="Finance",
                confidence=0.95,
                reasoning="Clear project match.",
            )
        )
        mock_classify = AsyncMock(return_value=mock_result)
        monkeypatch.setattr("pipelines.capture.classify", mock_classify)

        result = await capture_folder(folder, context=folder_ctx)

        # classify() was called (the unified engine), not the old folder prompt
        mock_classify.assert_called_once()
        call_args = mock_classify.call_args
        # First positional arg = subject containing folder name
        assert "migrate-test" in call_args[0][0]
        # Second arg = valid_destinations (from _build_vault_context)
        assert isinstance(call_args[0][1], str)
        # Third arg = config
        assert call_args[0][2] is folder_ctx.config

        # Result is success
        assert isinstance(result, Success)

    @pytest.mark.asyncio
    async def test_folder_classify_auto_routes_correctly(
        self, folder_ctx, vault_root, monkeypatch
    ):
        """Folder classify AUTO: folder moves to Projects/<project>/ destination."""
        from unittest.mock import AsyncMock

        from pipelines.capture import capture_folder
        from pipelines.classify import ClassifyResult

        folder = vault_root / "inbox" / "auto-migrate"
        _make_old_file(folder / "a.md", "# A\n\nbody a")

        # classify returns project=Alpha, confidence 0.95 → AUTO band
        mock_result = Success(
            ClassifyResult(
                project="Alpha",
                domains=["finance"],
                primary_domain="Finance",
                confidence=0.95,
                reasoning="Clear project Alpha match.",
            )
        )
        mock_classify = AsyncMock(return_value=mock_result)
        monkeypatch.setattr("pipelines.capture.classify", mock_classify)

        # We also need get_provider for per-file capture (capture_file internals)
        provider = AsyncMock()
        provider.complete.return_value = Success(
            LLMResponse(
                content=json.dumps({"title": "T", "tags": ["test"]}),
                model="t",
                usage={},
            )
        )
        monkeypatch.setattr(
            "pipelines.capture.get_provider", lambda task, config: provider
        )

        result = await capture_folder(folder, context=folder_ctx)

        assert isinstance(result, Success)
        # Folder moved out of inbox
        assert not folder.exists()
        moved = vault_root / "Projects" / "auto-migrate"
        assert moved.exists()

        # Batch row exists with correct destination
        rows = _batches_rows(folder_ctx.db_path)
        assert len(rows) == 1
        assert rows[0]["destination_type"] == "project"
        assert rows[0]["destination_name"] == "Alpha"
        assert rows[0]["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_folder_classify_suggest_unchanged(
        self, folder_ctx, vault_root, monkeypatch
    ):
        """Folder classify SUGGEST: batch PENDING_REVIEW, no folder move."""
        from unittest.mock import AsyncMock

        from pipelines.capture import capture_folder
        from pipelines.classify import ClassifyResult

        folder = vault_root / "inbox" / "suggest-migrate"
        _make_old_file(folder / "a.md", "# A\n\nbody a")

        # classify returns confidence 0.75 → SUGGEST band
        mock_result = Success(
            ClassifyResult(
                project="Alpha",
                domains=["finance"],
                primary_domain="Finance",
                confidence=0.75,
                reasoning="Likely Alpha but some ambiguity.",
            )
        )
        mock_classify = AsyncMock(return_value=mock_result)
        monkeypatch.setattr("pipelines.capture.classify", mock_classify)

        result = await capture_folder(folder, context=folder_ctx)

        assert isinstance(result, Success)
        assert result.value == []
        # Folder NOT moved
        assert folder.exists()
        rows = _batches_rows(folder_ctx.db_path)
        assert len(rows) == 1
        assert rows[0]["status"] == "PENDING_REVIEW"

    @pytest.mark.asyncio
    async def test_folder_classify_clueless_unchanged(
        self, folder_ctx, vault_root, monkeypatch
    ):
        """Folder classify CLUELESS: per-file markers, folder stays, CLUELESS batch."""
        from unittest.mock import AsyncMock

        from pipelines.capture import capture_folder
        from pipelines.classify import ClassifyResult

        folder = vault_root / "inbox" / "clueless-migrate"
        _make_old_file(folder / "a.md", "# A\n\nbody a")

        # classify returns confidence 0.3 → CLUELESS band
        mock_result = Success(
            ClassifyResult(
                project=None,
                domains=[],
                primary_domain=None,
                confidence=0.3,
                reasoning="Cannot determine — too vague.",
            )
        )
        mock_classify = AsyncMock(return_value=mock_result)
        monkeypatch.setattr("pipelines.capture.classify", mock_classify)

        # Per-file capture needs get_provider
        provider = AsyncMock()
        provider.complete.return_value = Success(
            LLMResponse(
                content=json.dumps({"title": "T", "tags": ["test"]}),
                model="t",
                usage={},
            )
        )
        monkeypatch.setattr(
            "pipelines.capture.get_provider", lambda task, config: provider
        )

        result = await capture_folder(folder, context=folder_ctx)

        assert isinstance(result, Success)
        assert result.value == []
        # Folder NOT moved
        assert folder.exists()
        rows = _batches_rows(folder_ctx.db_path)
        assert len(rows) == 1
        assert rows[0]["status"] == "CLUELESS"

    @pytest.mark.asyncio
    async def test_folder_classify_failure_treated_as_clueless(
        self, folder_ctx, vault_root, monkeypatch
    ):
        """classify() returns Failure → treated as CLUELESS (confidence 0.0)."""
        from unittest.mock import AsyncMock

        from pipelines.capture import capture_folder

        folder = vault_root / "inbox" / "failure-migrate"
        _make_old_file(folder / "a.md", "# A\n\nbody a")

        mock_classify = AsyncMock(
            return_value=Failure(
                error="API timeout",
                recoverable=True,
                context={},
            )
        )
        monkeypatch.setattr("pipelines.capture.classify", mock_classify)

        # Per-file capture needs get_provider (CLUELESS path runs capture_file)
        provider = AsyncMock()
        provider.complete.return_value = Success(
            LLMResponse(
                content=json.dumps({"title": "T", "tags": ["test"]}),
                model="t",
                usage={},
            )
        )
        monkeypatch.setattr(
            "pipelines.capture.get_provider", lambda task, config: provider
        )

        result = await capture_folder(folder, context=folder_ctx)

        assert isinstance(result, Success)
        assert result.value == []
        # Folder NOT moved
        assert folder.exists()
        rows = _batches_rows(folder_ctx.db_path)
        assert len(rows) == 1
        assert rows[0]["status"] == "CLUELESS"
        assert rows[0]["confidence"] == 0.0


# ===========================================================================
# Phase 9 — classify_folder.yaml removal verification
# ===========================================================================


class TestClassifyFolderYamlDeleted:
    """P2-CIC Phase 9 — the old classify_folder.yaml prompt file is gone."""

    def test_classify_folder_yaml_deleted(self):
        """Verify prompts/classify_folder.yaml no longer exists."""
        import importlib.resources

        prompt_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "prompts"
            / "classify_folder.yaml"
        )
        assert not prompt_path.exists(), (
            f"classify_folder.yaml still exists at {prompt_path}. "
            f"Phase 9 migration should have deleted it."
        )


# ===========================================================================
# Phase 7 (C7) — SUPPRESS per-file classify in folder capture paths
# ===========================================================================


@pytest.mark.asyncio
async def test_folder_clueless_loop_sets_suppress(folder_ctx, vault_root, monkeypatch):
    """Folder CLUELESS: per-file capture uses skip_classify=True (C7/P2-CIC-06)."""
    from unittest.mock import AsyncMock
    from pipelines.capture import capture_folder
    from pipelines.classify import ClassifyResult

    folder = vault_root / "inbox" / "clueless-folder"
    _make_old_file(folder / "a.md", "# A\n\nbody a")
    _make_old_file(folder / "b.md", "# B\n\nbody b")

    # classify returns CLUELESS
    classify_calls = []

    async def track_classify(*args, **kwargs):
        classify_calls.append(args)
        return Success(
            ClassifyResult(
                project=None,
                domains=[],
                primary_domain=None,
                confidence=0.3,
                reasoning="Too vague.",
            )
        )

    monkeypatch.setattr("pipelines.capture.classify", track_classify)

    # per-file capture needs get_provider for CLUELESS path
    provider = AsyncMock()
    provider.complete.return_value = Success(
        LLMResponse(
            content=json.dumps({"title": "T", "tags": ["test"]}),
            model="t",
            usage={},
        )
    )
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_folder(folder, context=folder_ctx)

    assert isinstance(result, Success)
    # classify must be called exactly once (folder classify only)
    # If per-file classify were running, we would see 1 folder + 2 files = 3 calls
    assert len(classify_calls) == 1, (
        f"Expected 1 classify call (folder only), got {len(classify_calls)}. "
        f"Per-file classify must be suppressed via skip_classify=True."
    )


@pytest.mark.asyncio
async def test_capture_folder_files_sets_suppress(folder_ctx, vault_root, monkeypatch):
    """_capture_folder_files sets skip_classify=True on per-file context (C7)."""
    from pipelines.capture import _capture_folder_files
    from core.pipeline import PipelineContext
    from dataclasses import replace

    project_dir = vault_root / "Projects" / "TestProject"
    project_dir.mkdir(parents=True, exist_ok=True)
    _make_old_file(project_dir / "note.md", "# Note\n\nbody")

    files = [project_dir / "note.md"]

    # Track capture_file calls to inspect context
    ctx_seen: list[PipelineContext] = []

    async def track_capture_file(path, context):
        ctx_seen.append(context)
        from vault.writer import WriteOutcome
        from vault.frontmatter import NoteMetadata

        return Success(
            WriteOutcome(
                vault_path="test/vp",
                absolute_path=path,
                content_hash="abc",
                metadata=NoteMetadata(),
            )
        )

    monkeypatch.setattr("pipelines.capture.capture_file", track_capture_file)

    ctx = replace(folder_ctx, batch_id=42)
    await _capture_folder_files(project_dir, files, ctx)

    assert len(ctx_seen) == 1, f"Expected 1 capture_file call, got {len(ctx_seen)}"
    assert ctx_seen[0].skip_classify is True, (
        f"_capture_folder_files must pass skip_classify=True to per-file context"
    )
