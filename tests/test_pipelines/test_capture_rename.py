"""Integration tests for rename gate wired into capture pipeline — Phase 3.

Four scenarios:
  A — Re-capture of existing doc → SKIP (file not renamed)
  B — Generic placeholder name → AUGMENT (AI topic appended)
  C — Illegible binary drop → FULL_RENAME (attachment + sibling use AI title)
  D — LLM call count guard (gate makes zero extra LLM calls)

Uses real tmp filesystem + real SQLite DB. LLM provider is mocked.
"""

from __future__ import annotations

import json
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
    if tags is None:
        tags = ["type/note"]
    provider = AsyncMock()
    meta_json = json.dumps({"title": title, "type": "note", "tags": tags})
    provider.complete.side_effect = [
        Success(LLMResponse(content="Test summary.", model="test", usage={})),
        Success(LLMResponse(content=meta_json, model="test", usage={})),
    ]
    return provider


def _backdated(path: Path, content: str) -> Path:
    """Write file and backdate mtime past cooldown (120s ago)."""
    import os

    path.write_text(content, encoding="utf-8")
    old = time.time() - 120
    os.utime(path, (old, old))
    return path


# Minimal valid PDF with extractable text. The incorrect startxref warning from
# pypdf is benign — the handler accepts it and returns text successfully.
_MINIMAL_PDF = b"""%PDF-1.3
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj

2 0 obj
<< /Type /Pages /Count 1 /Kids [3 0 R] >>
endobj

3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj

4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 72 720 Td (Q2 Movies test content.) Tj ET
endstream
endobj

5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj

xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000274 00000 n
0000000371 00000 n

trailer
<< /Size 6 /Root 1 0 R >>
startxref
452
%%EOF
"""


def _backdated_binary(path: Path) -> Path:
    """Write minimal valid PDF and backdate mtime."""
    import os

    path.write_bytes(_MINIMAL_PDF)
    old = time.time() - 120
    os.utime(path, (old, old))
    return path


# ---------------------------------------------------------------------------
# Test A — Re-capture of active file (SKIP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_gate_skip_on_recapture(vault_root, pipeline_ctx, monkeypatch):
    """Second capture of an existing doc leaves filename unchanged (Rule 1 SKIP)."""
    from pipelines.capture import capture_file
    from storage.audit_log import query
    from storage.documents import upsert
    from vault.frontmatter import NoteMetadata
    from vault.writer import WriteOutcome

    existing = WriteOutcome(
        vault_path="inbox/Q2 Strategy.md",
        absolute_path=vault_root / "inbox" / "Q2 Strategy.md",
        content_hash="abc123",
        metadata=NoteMetadata(summary="Prior capture summary."),
    )
    upsert_result = upsert(existing, db_path=pipeline_ctx.db_path)
    assert isinstance(upsert_result, Success), f"Pre-insert failed: {upsert_result}"

    md_file = _backdated(
        vault_root / "inbox" / "Q2 Strategy.md",
        "# Q2 Strategy\n\nBody text about strategy.",
    )

    provider = _mock_provider("Different Title")
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    # File must not be renamed.
    assert md_file.exists(), "Original file should still exist"
    assert not (vault_root / "inbox" / "Different Title.md").exists(), (
        "File should NOT be renamed to AI title on re-capture"
    )

    # audit_log must have a rename_gate row with outcome=SKIP.
    audit_result = query(pipeline="capture", db_path=pipeline_ctx.db_path)
    assert isinstance(audit_result, Success)
    gate_rows = [r for r in audit_result.value if r.stage == "rename_gate"]
    assert len(gate_rows) >= 1, "Expected at least one rename_gate audit row"
    assert gate_rows[0].outcome == "SKIP", (
        f"Expected SKIP outcome, got {gate_rows[0].outcome!r}"
    )


# ---------------------------------------------------------------------------
# Test B — Generic placeholder name (AUGMENT)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_gate_augment_on_generic_placeholder(
    vault_root, pipeline_ctx, monkeypatch
):
    """'a meeting.md' with AI title 'Phong Q2 Sync' → 'a meeting - Phong Q2 Sync.md'."""
    from pipelines.capture import capture_file
    from storage.audit_log import query

    md_file = _backdated(
        vault_root / "inbox" / "a meeting.md",
        "# Meeting\n\nDiscussed Q2 strategy with Phong.",
    )

    provider = _mock_provider("Phong Q2 Sync")
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    # File should be renamed to augmented form.
    expected_name = "a meeting - Phong Q2 Sync.md"
    renamed = vault_root / "inbox" / expected_name
    assert renamed.exists(), (
        f"Expected augmented rename to {expected_name!r}; files in inbox: "
        + str(list((vault_root / "inbox").iterdir()))
    )

    # audit_log must have a rename_gate row with outcome=AUGMENT.
    audit_result = query(pipeline="capture", db_path=pipeline_ctx.db_path)
    assert isinstance(audit_result, Success)
    gate_rows = [r for r in audit_result.value if r.stage == "rename_gate"]
    assert len(gate_rows) >= 1, "Expected at least one rename_gate audit row"
    assert gate_rows[0].outcome == "AUGMENT", (
        f"Expected AUGMENT outcome, got {gate_rows[0].outcome!r}"
    )


# ---------------------------------------------------------------------------
# Test C — Illegible binary drop (FULL_RENAME)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_gate_full_rename_on_illegible_binary(
    vault_root, pipeline_ctx, monkeypatch
):
    """'xkdhgksjfs.pdf' with AI title 'Q2 Movies Deck' → attachment + sibling use AI title."""
    from pipelines.capture import capture_file
    from storage.audit_log import query

    pdf_file = _backdated_binary(vault_root / "inbox" / "xkdhgksjfs.pdf")

    provider = _mock_provider("Q2 Movies Deck")
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_file(pdf_file, context=pipeline_ctx)

    assert isinstance(result, Success), f"Expected Success, got {result}"

    # Attachment should be at attachments/Q2 Movies Deck.pdf
    attachment = vault_root / "attachment" / "Q2 Movies Deck.pdf"
    assert attachment.exists(), (
        f"Expected attachment at {attachment}; files: "
        + str(list((vault_root / "attachment").iterdir()))
    )

    # Sibling note at inbox/Q2 Movies Deck.md
    sibling = vault_root / "inbox" / "Q2 Movies Deck.md"
    assert sibling.exists(), (
        f"Expected sibling note at {sibling}; files: "
        + str(list((vault_root / "inbox").iterdir()))
    )

    # audit_log must have a rename_gate row with outcome=FULL_RENAME.
    audit_result = query(pipeline="capture", db_path=pipeline_ctx.db_path)
    assert isinstance(audit_result, Success)
    gate_rows = [r for r in audit_result.value if r.stage == "rename_gate"]
    assert len(gate_rows) >= 1, "Expected at least one rename_gate audit row"
    assert gate_rows[0].outcome == "FULL_RENAME", (
        f"Expected FULL_RENAME outcome, got {gate_rows[0].outcome!r}"
    )


# ---------------------------------------------------------------------------
# Test D — LLM call count guard (gate makes zero extra LLM calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_gate_makes_no_extra_llm_calls(
    vault_root, pipeline_ctx, monkeypatch
):
    """Gate introduces zero extra LLM calls — provider called exactly twice."""
    from pipelines.capture import capture_file

    md_file = _backdated(
        vault_root / "inbox" / "my-note.md",
        "# My Note\n\nSome content.",
    )

    provider = _mock_provider("My Note")
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: provider)

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    assert provider.complete.call_count == 2, (
        f"Expected exactly 2 LLM calls (summarize + metadata), "
        f"got {provider.complete.call_count}"
    )
