"""
tests/test_daemon/test_extractor.py

Comprehensive tests for daemon/extractor.py — TextContent, BinaryContent,
text extraction dispatch, binary fallback, and error handling.

Test map:
  Section 1 — TextContent and BinaryContent dataclasses
  Section 2 — extract(): text extraction via handler (PDF)
  Section 3 — extract(): binary fallback (PNG image → failure handler)
  Section 4 — extract(): hash is over raw bytes (not extracted text)
  Section 5 — extract(): vault_path NFC-normalised POSIX
  Section 6 — extract(): unknown extension → BinaryContent with octet-stream
  Section 7 — extract(): vanished file → Failure(recoverable=True)
  Section 8 — extract(): path outside vault_root → Failure
"""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

import pytest

from core.result import Failure, Success
from daemon.extractor import BinaryContent, TextContent, extract


# ── helpers ──────────────────────────────────────────────────────────────────

SAMPLE_PDF = Path(__file__).resolve().parent.parent / "fixtures" / "sample_text.pdf"

# ── Section 1 — dataclasses ──────────────────────────────────────────────────


class TestTextContent:
    """TextContent frozen dataclass tests."""

    def test_construction(self):
        tc = TextContent(
            text="hello world",
            content_hash="abc123",
            vault_path="inbox/note.pdf",
            original_filename="note.pdf",
            file_size_bytes=1024,
        )
        assert tc.text == "hello world"
        assert tc.content_hash == "abc123"
        assert tc.vault_path == "inbox/note.pdf"
        assert tc.original_filename == "note.pdf"
        assert tc.file_size_bytes == 1024

    def test_frozen(self):
        tc = TextContent(
            text="hello",
            content_hash="abc",
            vault_path="x",
            original_filename="x.pdf",
            file_size_bytes=0,
        )
        with pytest.raises(Exception):
            tc.text = "mutated"  # type: ignore[misc]


class TestBinaryContent:
    """BinaryContent frozen dataclass tests."""

    def test_construction(self):
        bc = BinaryContent(
            raw_bytes=b"\x89PNG\r\n\x1a\n",
            content_hash="def456",
            vault_path="inbox/img.png",
            original_filename="img.png",
            file_size_bytes=8,
            mime_type="image/png",
        )
        assert bc.raw_bytes == b"\x89PNG\r\n\x1a\n"
        assert bc.content_hash == "def456"
        assert bc.vault_path == "inbox/img.png"
        assert bc.original_filename == "img.png"
        assert bc.file_size_bytes == 8
        assert bc.mime_type == "image/png"

    def test_frozen(self):
        bc = BinaryContent(
            raw_bytes=b"",
            content_hash="abc",
            vault_path="x",
            original_filename="x",
            file_size_bytes=0,
            mime_type="application/octet-stream",
        )
        with pytest.raises(Exception):
            bc.mime_type = "mutated"  # type: ignore[misc]


# ── Section 2 — extract(): text extraction via handler (PDF) ──────────────────


class TestExtractTextPdf:
    """A .pdf file returns TextContent with extracted text."""

    @pytest.fixture(autouse=True)
    def _ensure_handlers_loaded(self):
        """Ensure handler imports have happened so registry is populated."""
        import handlers  # noqa: F401 — triggers all @register decorators

    def test_pdf_returns_text_content(self, tmp_path: Path):
        """A PDF file with extractable text returns TextContent."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        # Copy the sample PDF into the vault
        dest = inbox / "sample.pdf"
        dest.write_bytes(SAMPLE_PDF.read_bytes())

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=tc):
                assert isinstance(tc, TextContent)
                assert tc.text  # extracted text is non-empty
                assert tc.original_filename == "sample.pdf"
                assert tc.file_size_bytes == len(SAMPLE_PDF.read_bytes())
                assert tc.vault_path == "inbox/sample.pdf"
                # content_hash is a 64-char hex string
                assert len(tc.content_hash) == 64
                assert all(c in "0123456789abcdef" for c in tc.content_hash)
            case _:
                pytest.fail(f"expected Success(TextContent), got {result}")

    def test_pdf_hash_matches_raw_bytes(self, tmp_path: Path):
        """The content_hash in TextContent is SHA-256 of the raw PDF bytes."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        dest = inbox / "sample.pdf"
        raw = SAMPLE_PDF.read_bytes()
        dest.write_bytes(raw)

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=tc):
                expected_hash = hashlib.sha256(raw).hexdigest()
                assert tc.content_hash == expected_hash
            case _:
                pytest.fail(f"expected Success, got {result}")


# ── Section 3 — extract(): binary fallback (PNG) ─────────────────────────────


class TestExtractBinaryPng:
    """A .png file returns BinaryContent because the image handler fails."""

    @pytest.fixture(autouse=True)
    def _ensure_handlers_loaded(self):
        import handlers  # noqa: F401

    def test_png_returns_binary_content(self, tmp_path: Path):
        """PNG handler returns Failure → extract falls back to BinaryContent."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        # Create a minimal valid PNG (8-byte signature + minimal chunks)
        png_data = (
            b"\x89PNG\r\n\x1a\n"  # PNG signature
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde"  # IHDR
            b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"  # IEND
        )
        dest = inbox / "image.png"
        dest.write_bytes(png_data)

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=bc):
                assert isinstance(bc, BinaryContent)
                assert bc.raw_bytes == png_data
                assert bc.mime_type == "image/png"
                assert bc.original_filename == "image.png"
                assert bc.file_size_bytes == len(png_data)
                assert bc.vault_path == "inbox/image.png"
                expected_hash = hashlib.sha256(png_data).hexdigest()
                assert bc.content_hash == expected_hash
            case _:
                pytest.fail(f"expected Success(BinaryContent), got {result}")


# ── Section 4 — hash over raw bytes ──────────────────────────────────────────


class TestHashOverRawBytes:
    """Content hash is always SHA-256 of raw bytes, never extracted text."""

    @pytest.fixture(autouse=True)
    def _ensure_handlers_loaded(self):
        import handlers  # noqa: F401

    def test_pdf_hash_not_over_extracted_text(self, tmp_path: Path):
        """Prove content_hash != SHA-256 of extracted text."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        dest = inbox / "sample.pdf"
        raw = SAMPLE_PDF.read_bytes()
        dest.write_bytes(raw)

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=tc):
                text_hash = hashlib.sha256(tc.text.encode("utf-8")).hexdigest()
                # The content_hash (over raw bytes) should differ from
                # a hash of the extracted text.
                assert tc.content_hash != text_hash, (
                    "content_hash should be over raw bytes, not extracted text"
                )
            case _:
                pytest.fail(f"expected Success, got {result}")

    def test_binary_hash_matches_raw(self, tmp_path: Path):
        """BinaryContent hash equals SHA-256 of raw_bytes."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        data = b"just some binary data that no handler will claim"
        dest = inbox / "unknown.bin"
        dest.write_bytes(data)

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=bc):
                expected = hashlib.sha256(data).hexdigest()
                assert bc.content_hash == expected
                # Also confirm hash of raw_bytes matches
                assert hashlib.sha256(bc.raw_bytes).hexdigest() == expected
            case _:
                pytest.fail(f"expected Success(BinaryContent), got {result}")


# ── Section 5 — vault_path NFC-normalised POSIX ──────────────────────────────


class TestVaultPathNormalization:
    """vault_path is a NFC-normalised POSIX string relative to vault_root."""

    @pytest.fixture(autouse=True)
    def _ensure_handlers_loaded(self):
        import handlers  # noqa: F401

    def test_nfc_normalization(self, tmp_path: Path):
        """NFC decomposition is applied to vault_path."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        # Use NFD form as the filename (é = e + combining accent)
        name_nfd = unicodedata.normalize("NFD", "café.md")
        name_nfc = unicodedata.normalize("NFC", "café.md")
        assert name_nfd != name_nfc  # sanity check

        dest = inbox / name_nfd
        dest.write_text("# Hello")

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=tc):
                # vault_path should be NFC-normalised
                assert tc.vault_path == f"inbox/{name_nfc}"
                # It should NOT be in NFD form
                assert tc.vault_path != f"inbox/{name_nfd}"
            case _:
                pytest.fail(f"expected Success, got {result}")

    def test_uses_posix_separators(self, tmp_path: Path):
        """vault_path always uses forward slashes (POSIX)."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        deep = vault_root / "a" / "b" / "c"
        deep.mkdir(parents=True)

        dest = deep / "deep.md"
        dest.write_text("# Deep")

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=tc):
                assert "\\" not in tc.vault_path
                assert tc.vault_path == "a/b/c/deep.md"
            case _:
                pytest.fail(f"expected Success, got {result}")

    def test_file_at_root_level(self, tmp_path: Path):
        """A file directly in vault_root has a flat vault_path."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()

        dest = vault_root / "root_note.md"
        dest.write_text("# Root")

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=tc):
                assert tc.vault_path == "root_note.md"
            case _:
                pytest.fail(f"expected Success, got {result}")


# ── Section 6 — unknown extension → BinaryContent ────────────────────────────


class TestUnknownExtension:
    """Files with no matching handler return BinaryContent."""

    @pytest.fixture(autouse=True)
    def _ensure_handlers_loaded(self):
        import handlers  # noqa: F401

    def test_unknown_extension_returns_binary(self, tmp_path: Path):
        """A .foobar file has no handler → BinaryContent with octet-stream."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        data = b"some data"
        dest = inbox / "mystery.foobar"
        dest.write_bytes(data)

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=bc):
                assert isinstance(bc, BinaryContent)
                assert bc.mime_type == "application/octet-stream"
                assert bc.original_filename == "mystery.foobar"
                assert bc.file_size_bytes == len(data)
                assert bc.content_hash == hashlib.sha256(data).hexdigest()
            case _:
                pytest.fail(f"expected Success(BinaryContent), got {result}")

    def test_no_extension_returns_binary(self, tmp_path: Path):
        """A file with no extension → BinaryContent with octet-stream."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        data = b"no extension file"
        dest = inbox / "Makefile"  # no extension
        dest.write_bytes(data)

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=bc):
                assert isinstance(bc, BinaryContent)
                assert bc.mime_type == "application/octet-stream"
                assert bc.original_filename == "Makefile"
            case _:
                pytest.fail(f"expected Success(BinaryContent), got {result}")


# ── Section 7 — vanished file → Failure(recoverable=True) ────────────────────


class TestVanishedFile:
    """A file that disappears before/during read returns a recoverable Failure."""

    @pytest.fixture(autouse=True)
    def _ensure_handlers_loaded(self):
        import handlers  # noqa: F401

    def test_vanished_file_returns_failure(self, tmp_path: Path):
        """Calling extract on a path that doesn't exist returns Failure."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()

        non_existent = vault_root / "inbox" / "vanished.pdf"

        result = extract(non_existent, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Failure(recoverable=True) as f:
                assert "cannot read file" in f.error
            case _:
                pytest.fail(f"expected Failure(recoverable=True), got {result}")

    def test_deleted_before_read(self, tmp_path: Path):
        """Create a file then delete it before extract → Failure."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        dest = inbox / "delete_me.md"
        dest.write_text("# will be deleted")
        dest.unlink()  # remove before reading

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Failure(recoverable=True) as f:
                assert "cannot read file" in f.error
            case _:
                pytest.fail(f"expected Failure(recoverable=True), got {result}")


# ── Section 8 — path outside vault_root → Failure ────────────────────────────


class TestPathOutsideVaultRoot:
    """A path not inside vault_root returns a non-recoverable Failure."""

    @pytest.fixture(autouse=True)
    def _ensure_handlers_loaded(self):
        import handlers  # noqa: F401

    def test_outside_vault_returns_failure(self, tmp_path: Path):
        """Path outside vault_root → Failure(recoverable=False)."""
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        other_dir = tmp_path / "outside"
        other_dir.mkdir()

        outside_file = other_dir / "stray.md"
        outside_file.write_text("# stray")

        result = extract(outside_file, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Failure(recoverable=False) as f:
                assert "not inside vault_root" in f.error
            case _:
                pytest.fail(f"expected Failure(recoverable=False), got {result}")


# ── Section 9 — file_size_bytes matches actual file size ──────────────────────


class TestFileSizeBytes:
    """file_size_bytes always matches len(raw_bytes)."""

    @pytest.fixture(autouse=True)
    def _ensure_handlers_loaded(self):
        import handlers  # noqa: F401

    def test_text_content_size_match(self, tmp_path: Path):
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        content = "# A note with some text\n\n" * 50
        dest = inbox / "note.md"
        dest.write_text(content)

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=tc):
                assert tc.file_size_bytes == len(content.encode("utf-8"))
            case _:
                pytest.fail(f"expected Success, got {result}")

    def test_binary_content_size_match(self, tmp_path: Path):
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        data = b"\x00\x01\x02\x03" * 250  # 1000 bytes
        dest = inbox / "binary.xyz"
        dest.write_bytes(data)

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=bc):
                assert bc.file_size_bytes == len(data)
                assert bc.file_size_bytes == 1000
            case _:
                pytest.fail(f"expected Success, got {result}")


# ── Section 10 — original_filename matches path.name ─────────────────────────


class TestOriginalFilename:
    """original_filename always equals path.name."""

    @pytest.fixture(autouse=True)
    def _ensure_handlers_loaded(self):
        import handlers  # noqa: F401

    def test_text_content_filename(self, tmp_path: Path):
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        dest = inbox / "My Document (final).pdf"
        dest.write_bytes(SAMPLE_PDF.read_bytes())

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=tc):
                assert tc.original_filename == "My Document (final).pdf"
            case _:
                pytest.fail(f"expected Success, got {result}")

    def test_binary_content_filename(self, tmp_path: Path):
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        inbox = vault_root / "inbox"
        inbox.mkdir(parents=True)

        dest = inbox / "Screenshot 2024-01-01.png"
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")

        result = extract(dest, vault_root, max_file_size_bytes=50_000_000)

        match result:
            case Success(value=bc):
                assert bc.original_filename == "Screenshot 2024-01-01.png"
            case _:
                pytest.fail(f"expected Success, got {result}")
