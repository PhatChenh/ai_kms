"""tests/test_mcp_server/test_resolve.py — Binary Resolver Helper (Component 8)

TDD: RED → GREEN complete. Four tests for inspect().
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.result import Failure, Success

FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "sample_text.pdf"
FIXTURE_SIBLING_MD = (
    Path(__file__).parent.parent / "fixtures" / "sibling_inspect_pdf.md"
)
FIXTURE_PLAIN_NOTE = (
    Path(__file__).parent.parent / "fixtures" / "plain_note_no_attachment.md"
)


# ---------------------------------------------------------------------------
# RED Test 1 — inspect(sibling .md) resolves via attachment_path
# ---------------------------------------------------------------------------


class TestInspectFromSiblingMd:
    """inspect() on a sibling .md resolves attachment_path -> extracts binary text."""

    def test_inspect_sibling_returns_binary_text(self, vault_dir: Path):
        """Given sibling .md with attachment_path, return text from the binary."""
        # Copy PDF fixture into vault at the path referenced by attachment_path
        binary_dir = vault_dir / "Projects" / "Alpha" / "attachment"
        binary_dir.mkdir(parents=True, exist_ok=True)
        binary_path = binary_dir / "sample_text.pdf"
        shutil.copy2(FIXTURE_PDF, binary_path)

        # Copy sibling .md into .summaries/ next to the binary
        summaries_dir = binary_dir / ".summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)
        sibling_md = summaries_dir / "sample_text.pdf.md"
        shutil.copy2(FIXTURE_SIBLING_MD, sibling_md)

        from mcp_server._resolve import inspect

        result = inspect(sibling_md)

        assert isinstance(result, Success)
        assert "Sample text fixture" in result.value


# ---------------------------------------------------------------------------
# RED Test 2 — inspect(binary directly) returns same text
# ---------------------------------------------------------------------------


class TestInspectFromBinaryDirect:
    """inspect() on a binary path returns extracted text directly."""

    def test_inspect_binary_direct_returns_text(self, vault_dir: Path):
        """Given binary path directly, return extracted text."""
        binary_dir = vault_dir / "Projects" / "Beta" / "attachment"
        binary_dir.mkdir(parents=True, exist_ok=True)
        binary_path = binary_dir / "sample_text.pdf"
        shutil.copy2(FIXTURE_PDF, binary_path)

        from mcp_server._resolve import inspect

        result = inspect(binary_path)

        assert isinstance(result, Success)
        assert "Sample text fixture" in result.value


# ---------------------------------------------------------------------------
# RED Test 3 — no AI call (no provider, no prompt load)
# ---------------------------------------------------------------------------


class TestNoAiCall:
    """inspect() must not invoke any LLM provider or prompt loader."""

    def test_inspect_does_not_call_llm(self, vault_dir: Path, monkeypatch):
        """Patch get_provider and assert it is never called."""
        import llm.provider as prov

        called = False

        def _fake_get_provider(*args, **kwargs):
            nonlocal called
            called = True
            raise RuntimeError("LLM provider should never be invoked")

        monkeypatch.setattr(prov, "get_provider", _fake_get_provider)

        # Set up a binary in the vault with sibling .md
        binary_dir = vault_dir / "Projects" / "Gamma" / "attachment"
        binary_dir.mkdir(parents=True, exist_ok=True)
        binary_path = binary_dir / "sample_text.pdf"
        shutil.copy2(FIXTURE_PDF, binary_path)

        summaries_dir = binary_dir / ".summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)
        sibling_md = summaries_dir / "sample_text.pdf.md"
        shutil.copy2(FIXTURE_SIBLING_MD, sibling_md)

        from mcp_server._resolve import inspect

        result = inspect(sibling_md)

        assert isinstance(result, Success)
        assert not called, "LLM provider should never be invoked during inspect"


# ---------------------------------------------------------------------------
# RED Test 4 — .md without attachment_path -> Failure
# ---------------------------------------------------------------------------


class TestMdWithoutAttachmentPath:
    """A .md that is not a sibling (no attachment_path) returns a clear Failure."""

    def test_md_without_attachment_path_returns_failure(self, vault_dir: Path):
        """A plain .md note without attachment_path should fail, not crash."""
        note_path = vault_dir / "Projects" / "Delta" / "plain_note.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FIXTURE_PLAIN_NOTE, note_path)

        from mcp_server._resolve import inspect

        result = inspect(note_path)

        assert isinstance(result, Failure)
        assert result.recoverable is False
