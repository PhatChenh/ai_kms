"""Integration tests for pipelines/capture.py — Phase 4.

Uses a real SQLite DB + real vault tmp_path. LLM provider is mocked to return
fixed JSON so the tests stay fast and deterministic.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.result import Success
from llm.provider import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_provider(title: str, tags: list[str] | None = None) -> AsyncMock:
    """Return a mock LLM provider whose complete() returns fixed summary + metadata."""
    import json
    if tags is None:
        tags = ["test"]
    provider = AsyncMock()
    meta_json = json.dumps({"title": title, "type": "note", "tags": tags})
    provider.complete.side_effect = [
        Success(LLMResponse(content="Integration test summary.", model="test", usage={})),
        Success(LLMResponse(content=meta_json, model="test", usage={})),
    ]
    return provider


def _make_old_file(path: Path, content: str) -> Path:
    """Write a file and backdate its mtime to exceed cooldown (60s)."""
    path.write_text(content, encoding="utf-8")
    old_time = time.time() - 120
    import os
    os.utime(path, (old_time, old_time))
    return path


# ===========================================================================
# Test A — inbox .md drop
# ===========================================================================


@pytest.mark.asyncio
async def test_integration_inbox_md_drop_writes_audit_and_documents(
    vault_root, pipeline_ctx, monkeypatch
):
    """Full pipeline on an inbox .md creates one audit_log row and one documents row."""
    from pipelines.capture import capture_file
    from storage.audit_log import query
    from storage.documents import get_by_path

    md_file = _make_old_file(
        vault_root / "inbox" / "inbox-note.md",
        "# Inbox Note\n\nThis is the body of the note.",
    )

    provider = _mock_provider("inbox-note")
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success), f"Expected Success, got {result}"

    # audit_log now has 2 rows: metadata (CAPTURED) + rename_gate (SKIP/AUGMENT/FULL_RENAME)
    audit_result = query(pipeline="capture", db_path=pipeline_ctx.db_path)
    assert isinstance(audit_result, Success)
    assert len(audit_result.value) >= 1
    captured_rows = [r for r in audit_result.value if r.outcome == "CAPTURED"]
    assert len(captured_rows) == 1, "Expected exactly one CAPTURED audit row"
    assert captured_rows[0].stage == "metadata"

    # One documents row at inbox/inbox-note.md
    doc_result = get_by_path("inbox/inbox-note.md", db_path=pipeline_ctx.db_path)
    assert isinstance(doc_result, Success)
    assert doc_result.value is not None
    assert doc_result.value.vault_path == "inbox/inbox-note.md"


# ===========================================================================
# Test B — non-inbox .md drop
# ===========================================================================


@pytest.mark.asyncio
async def test_integration_noninbox_md_drop_stays_in_original_folder(
    vault_root, pipeline_ctx, monkeypatch
):
    """Pipeline on Projects/foo/note.md keeps document at Projects/foo/, not inbox/."""
    from pipelines.capture import capture_file
    from storage.documents import get_by_path

    foo_dir = vault_root / "Projects" / "foo"
    foo_dir.mkdir(parents=True, exist_ok=True)
    md_file = _make_old_file(
        foo_dir / "project-note.md",
        "# Project Note\n\nThis is a project note.",
    )

    provider = _mock_provider("project-note")
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert result.value.vault_path.startswith("Projects/foo/"), (
        f"Expected vault_path under Projects/foo/, got {result.value.vault_path!r}"
    )

    # Confirm NOT in inbox
    inbox_row = get_by_path("inbox/project-note.md", db_path=pipeline_ctx.db_path)
    assert isinstance(inbox_row, Success)
    assert inbox_row.value is None, "Note should NOT appear in inbox documents row"


# ===========================================================================
# NFC normalization
# ===========================================================================


@pytest.mark.asyncio
async def test_integration_vault_path_is_nfc_normalized(
    vault_root, pipeline_ctx, monkeypatch
):
    """vault_path stored in documents is NFC-normalized POSIX string."""
    import unicodedata
    from pipelines.capture import capture_file

    md_file = _make_old_file(
        vault_root / "inbox" / "note.md",
        "# Note\n\nBody.",
    )

    provider = _mock_provider("note")
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success)
    vp = result.value.vault_path
    assert unicodedata.normalize("NFC", vp) == vp, f"vault_path not NFC: {vp!r}"
    assert "/" in vp, f"Expected POSIX path with /, got {vp!r}"
    assert not vp.startswith("/"), f"vault_path should be relative, got {vp!r}"
