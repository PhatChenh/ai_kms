"""Phase 9 tests — Non-md drop detection in scan_capture."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.result import Failure, Success

_FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "sample_text.pdf"


def _copy_pdf(dst: Path) -> None:
    """Copy the fixture PDF (has extractable text) to dst."""
    shutil.copy(_FIXTURE_PDF, dst)


# ---------------------------------------------------------------------------
# scan_capture — non-md PDF drop in inbox/
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_pdf_in_inbox_creates_sibling_and_moves_attachment(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """scan_capture with PDF in inbox/ runs capture_file → sibling .md + PDF moved."""
    from pipelines.capture import scan_capture
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse

    # "kqzxvbn" is keyboard mash (no vowels) → gate Rule 4 → FULL_RENAME → "Annual Report"
    pdf_file = vault_root / "inbox" / "kqzxvbn.pdf"
    _copy_pdf(pdf_file)

    mtime = pdf_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Summary of report.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "Annual Report", "tags": ["type/report"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert len(result.value) == 1
    assert isinstance(result.value[0], WriteOutcome)
    # Sibling .md created in inbox/
    sibling = vault_root / "inbox" / "Annual Report.md"
    assert sibling.exists(), f"Expected sibling md at {sibling}"
    # PDF moved to attachment/
    attachment_dst = vault_root / "attachment" / "Annual Report.pdf"
    assert attachment_dst.exists(), f"Expected PDF at {attachment_dst}"
    assert not pdf_file.exists(), "Original PDF should be gone"


# ---------------------------------------------------------------------------
# scan_capture — PDF already in attachment/ is skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_pdf_in_attachment_is_skipped(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """scan_capture skips PDF already inside attachment/ — no capture_file call."""
    from pipelines.capture import scan_capture

    att = vault_root / "attachment"
    (att / "already-captured.pdf").write_bytes(b"%PDF already here")

    capture_called: list[Path] = []

    async def fake_capture_file(path, context=None):
        capture_called.append(path)
        return Success(MagicMock())

    monkeypatch.setattr("pipelines.capture.capture_file", fake_capture_file)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert result.value == []
    assert not any(p.name == "already-captured.pdf" for p in capture_called)


# ---------------------------------------------------------------------------
# scan_capture — PDF in inbox/ AND .md in inbox/ — both processed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_pdf_and_md_both_processed(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """scan_capture processes .md and non-md drops independently in the same run."""
    from pipelines.capture import scan_capture
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse

    md_file = vault_root / "inbox" / "my-note.md"
    md_file.write_text("# My Note\n\nBody text.", encoding="utf-8")

    pdf_file = vault_root / "inbox" / "report.pdf"
    _copy_pdf(pdf_file)

    mtime = max(md_file.stat().st_mtime, pdf_file.stat().st_mtime)
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        # md_file: summarize + metadata
        Success(LLMResponse(content="Note summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "my-note", "tags": ["type/capture"]}',
            model="test",
            usage={},
        )),
        # pdf_file: summarize + metadata
        Success(LLMResponse(content="Report summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "Annual Report", "tags": ["type/report"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    assert len(result.value) == 2
    assert all(isinstance(o, WriteOutcome) for o in result.value)


# ---------------------------------------------------------------------------
# scan_capture — non-md with unsupported extension logs WARNING, others continue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_capture_nonmd_unsupported_ext_logs_warning_continues(
    vault_root, db_path, pipeline_ctx, monkeypatch
):
    """Non-md with no handler returns Failure; scan_capture logs WARNING and continues."""
    from pipelines.capture import scan_capture
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse

    # Unsupported extension — no handler registered
    (vault_root / "inbox" / "unknown.xyz").write_bytes(b"binary blob")
    # Valid PDF that SHOULD be captured after the failure
    pdf_file = vault_root / "inbox" / "valid.pdf"
    _copy_pdf(pdf_file)

    mtime = max(
        (vault_root / "inbox" / "unknown.xyz").stat().st_mtime,
        pdf_file.stat().st_mtime,
    )
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="PDF summary.", model="test", usage={})),
        Success(LLMResponse(
            content='{"title": "valid-pdf", "tags": ["type/report"]}',
            model="test",
            usage={},
        )),
    ]
    monkeypatch.setattr("pipelines.capture.get_provider", lambda task, config: mock_provider)

    result = await scan_capture(root=vault_root, db_path=db_path)

    assert isinstance(result, Success)
    # Valid PDF captured; unsupported extension skipped with WARNING
    assert len(result.value) == 1
    assert isinstance(result.value[0], WriteOutcome)
