"""tests/test_vault/test_writer.py"""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path

import pytest

from core.result import Failure, Success
from vault.frontmatter import NoteMetadata


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_locked_note(path: Path) -> None:
    """Write a note pre-locked (updated_by_human=true) directly to disk."""
    from vault.frontmatter import dumps
    content = dumps(NoteMetadata(updated_by_human=True), "original body")
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# write_note tests
# ---------------------------------------------------------------------------

def test_write_new_note_creates_file_with_frontmatter(vault_root):
    """write_note creates the file; content parses back to supplied metadata fields."""
    from vault.writer import write_note

    path = vault_root / "inbox" / "note.md"
    meta = NoteMetadata(project="X", tags=["a"])

    result = write_note(path, "hello", meta, actor="ai")

    assert isinstance(result, Success)
    assert path.exists()

    from vault.frontmatter import parse
    r = parse(path)
    assert isinstance(r, Success)
    parsed_meta, body = r.value
    assert parsed_meta.project == "X"
    assert parsed_meta.tags == ["a"]
    assert body.strip() == "hello"


def test_write_sets_updated_by_human_per_actor(vault_root):
    """actor='human' stamps updated_by_human=True; actor='ai' stamps False."""
    from vault.writer import write_note

    path_human = vault_root / "inbox" / "human.md"
    path_ai = vault_root / "inbox" / "ai.md"

    r1 = write_note(path_human, "body", NoteMetadata(), actor="human")
    r2 = write_note(path_ai, "body", NoteMetadata(), actor="ai")

    assert isinstance(r1, Success)
    assert isinstance(r2, Success)
    assert r1.value.metadata.updated_by_human is True
    assert r2.value.metadata.updated_by_human is False


def test_write_ai_blocked_when_updated_by_human_true(vault_root):
    """AI write on a human-locked note → Failure(recoverable=False), file unchanged."""
    from vault.writer import write_note

    path = vault_root / "inbox" / "locked.md"
    _make_locked_note(path)
    original_bytes = path.read_bytes()

    result = write_note(path, "new content", NoteMetadata(), actor="ai")

    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert path.read_bytes() == original_bytes


def test_write_human_succeeds_over_updated_by_human(vault_root):
    """Human write on locked note succeeds and keeps updated_by_human=True."""
    from vault.writer import write_note

    path = vault_root / "inbox" / "locked.md"
    _make_locked_note(path)

    result = write_note(path, "new content", NoteMetadata(), actor="human")

    assert isinstance(result, Success)
    assert result.value.metadata.updated_by_human is True


def test_write_preserves_created_on_overwrite(vault_root):
    """Re-writing an existing note preserves the original created date."""
    from vault.writer import write_note
    from datetime import date

    path = vault_root / "inbox" / "note.md"
    original_created = date(2025, 1, 1)
    meta = NoteMetadata(created=original_created)

    write_note(path, "v1", meta, actor="ai")
    result = write_note(path, "v2", NoteMetadata(), actor="ai")

    assert isinstance(result, Success)
    assert result.value.metadata.created == original_created


def test_write_idempotent_content_hash(vault_root):
    """Writing the same content twice produces identical content_hash."""
    from vault.writer import write_note

    path = vault_root / "inbox" / "note.md"

    r1 = write_note(path, "same content", NoteMetadata(), actor="ai")
    r2 = write_note(path, "same content", NoteMetadata(), actor="ai")

    assert isinstance(r1, Success)
    assert isinstance(r2, Success)
    assert r1.value.content_hash == r2.value.content_hash


def test_write_returns_write_outcome_with_relative_vault_path(vault_root):
    """outcome.vault_path is POSIX relative to vault root (e.g. 'inbox/foo.md')."""
    from vault.writer import write_note

    path = vault_root / "inbox" / "foo.md"
    result = write_note(path, "body", NoteMetadata(), actor="ai")

    assert isinstance(result, Success)
    assert result.value.vault_path == "inbox/foo.md"


def test_write_atomic_no_partial_on_failure(vault_root, monkeypatch):
    """When os.replace raises, original file is unchanged and no .tmp_* remains."""
    import vault.writer as writer_mod
    from vault.writer import write_note

    path = vault_root / "inbox" / "note.md"
    original = "original content"
    # Pre-create a note using write_note itself so it's a valid locked-free note
    write_note(path, original, NoteMetadata(), actor="ai")
    original_bytes = path.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(writer_mod.os, "replace", boom)

    result = write_note(path, "new content", NoteMetadata(), actor="ai")

    assert isinstance(result, Failure)
    assert path.read_bytes() == original_bytes
    tmp_files = list(path.parent.glob(".tmp_*"))
    assert tmp_files == []


def test_write_temp_file_uses_dot_prefix(vault_root, monkeypatch):
    """The tmp file used in atomic write starts with '.tmp_'."""
    import vault.writer as writer_mod
    from vault.writer import write_note

    captured_src: list[str] = []
    real_replace = os.replace

    def capturing_replace(src, dst):
        captured_src.append(str(src))
        real_replace(src, dst)

    monkeypatch.setattr(writer_mod.os, "replace", capturing_replace)

    path = vault_root / "inbox" / "note.md"
    write_note(path, "body", NoteMetadata(), actor="ai")

    assert len(captured_src) >= 1
    tmp_name = Path(captured_src[0]).name
    assert tmp_name.startswith(".tmp_")


# ---------------------------------------------------------------------------
# move_note tests
# ---------------------------------------------------------------------------

def test_move_note_changes_location_and_updates_metadata(vault_root):
    """move_note: src gone, dst exists with bumped updated, body intact."""
    from vault.writer import move_note, write_note

    src = vault_root / "inbox" / "a.md"
    dst = vault_root / "inbox" / "b.md"

    write_note(src, "body text", NoteMetadata(project="P"), actor="ai")
    original_updated = None  # updated is server-set

    result = move_note(src, dst, actor="ai")

    assert isinstance(result, Success)
    assert not src.exists()
    assert dst.exists()
    assert result.value.vault_path == "inbox/b.md"

    from vault.frontmatter import parse
    r = parse(dst)
    assert isinstance(r, Success)
    meta, body = r.value
    assert body.strip() == "body text"
    assert meta.project == "P"


def test_move_note_blocked_when_locked(vault_root):
    """move_note on locked note → Failure; both files in original state."""
    from vault.writer import move_note

    src = vault_root / "inbox" / "locked.md"
    dst = vault_root / "inbox" / "dest.md"
    _make_locked_note(src)
    src_bytes = src.read_bytes()

    result = move_note(src, dst, actor="ai")

    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert src.exists()
    assert not dst.exists()
    assert src.read_bytes() == src_bytes


def test_unicode_normalization_on_vault_path(vault_root):
    """vault_path returned by write_note is NFC even when the path has NFD characters."""
    from vault.writer import _to_vault_path

    # Construct path with NFD 'é' (e + combining accent)
    nfd_name = "caf́e.md"
    nfd_path = vault_root / "inbox" / nfd_name

    result = _to_vault_path(nfd_path)

    nfc_expected = unicodedata.normalize("NFC", f"inbox/{nfd_name}")
    assert result == nfc_expected


# ---------------------------------------------------------------------------
# move_attachment tests
# ---------------------------------------------------------------------------


def test_move_attachment_relocates_binary(vault_root):
    """move_attachment moves binary bytes from src to dst; src gone, dst has same bytes."""
    from vault.writer import move_attachment

    src = vault_root / "inbox" / "Report.pdf"
    dst = vault_root / "attachment" / "Report.pdf"
    original_bytes = b"\x00\x01\x02\x03binary data"
    src.write_bytes(original_bytes)

    result = move_attachment(src, dst)

    assert isinstance(result, Success)
    assert result.value == dst
    assert not src.exists()
    assert dst.read_bytes() == original_bytes


def test_move_attachment_creates_dst_parent(vault_root):
    """move_attachment creates the dst parent directory if it doesn't exist."""
    from vault.writer import move_attachment

    src = vault_root / "inbox" / "doc.pdf"
    dst = vault_root / "attachment" / "doc.pdf"
    src.write_bytes(b"data")
    # attachment/ does NOT pre-exist in this test
    assert not (vault_root / "attachment").exists()

    result = move_attachment(src, dst)

    assert isinstance(result, Success)
    assert dst.parent.is_dir()
    assert dst.exists()


def test_move_attachment_fails_when_dst_exists(vault_root):
    """move_attachment returns Failure(recoverable=False) when dst already exists; both files unchanged."""
    from vault.writer import move_attachment

    src = vault_root / "inbox" / "report.pdf"
    dst = vault_root / "attachment" / "report.pdf"
    (vault_root / "attachment").mkdir()
    src.write_bytes(b"source bytes")
    dst.write_bytes(b"existing bytes")

    result = move_attachment(src, dst)

    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert src.read_bytes() == b"source bytes"
    assert dst.read_bytes() == b"existing bytes"


def test_move_attachment_missing_src(vault_root):
    """move_attachment returns Failure(recoverable=False) when src does not exist."""
    from vault.writer import move_attachment

    src = vault_root / "inbox" / "ghost.pdf"
    dst = vault_root / "attachment" / "ghost.pdf"

    result = move_attachment(src, dst)

    assert isinstance(result, Failure)
    assert result.recoverable is False


def test_move_attachment_does_not_parse_frontmatter(vault_root):
    """move_attachment succeeds even when file is not valid UTF-8 markdown."""
    from vault.writer import move_attachment

    src = vault_root / "inbox" / "binary.bin"
    dst = vault_root / "attachment" / "binary.bin"
    # Non-UTF-8 bytes — reader.read_note would fail on this
    src.write_bytes(b"\x00\x01\x02not utf8\xff")

    result = move_attachment(src, dst)

    assert isinstance(result, Success)
    assert dst.read_bytes() == b"\x00\x01\x02not utf8\xff"


def test_move_attachment_atomic_no_partial_on_failure(vault_root, monkeypatch):
    """When os.replace raises, src is unchanged and no .tmp_* file remains in dst parent."""
    import vault.writer as writer_mod
    from vault.writer import move_attachment

    src = vault_root / "inbox" / "report.pdf"
    dst = vault_root / "attachment" / "report.pdf"
    original_bytes = b"precious bytes"
    src.write_bytes(original_bytes)
    (vault_root / "attachment").mkdir()

    def boom(s, d):
        raise OSError("disk full")

    monkeypatch.setattr(writer_mod.os, "replace", boom)

    result = move_attachment(src, dst)

    assert isinstance(result, Failure)
    assert src.read_bytes() == original_bytes
    tmp_files = list((vault_root / "attachment").glob(".tmp_*"))
    assert tmp_files == []
