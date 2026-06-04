"""tests/test_vault/test_reader.py"""
from __future__ import annotations

import hashlib
from pathlib import Path


from core.result import Failure, Success


def write_file(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_read_note_returns_note_with_hash(tmp_path):
    """Note.content_hash is 64 hex chars matching sha256(body.rstrip('\\n').encode())."""
    from vault.reader import read_note

    body = "hello world"
    f = write_file(tmp_path, "n.md", f"---\ntype: note\n---\n{body}")
    result = read_note(f)

    assert isinstance(result, Success)
    note = result.value
    expected_hash = hashlib.sha256(body.rstrip("\n").encode("utf-8")).hexdigest()
    assert note.content_hash == expected_hash
    assert len(note.content_hash) == 64


def test_read_note_hash_stable_across_trailing_newlines(tmp_path):
    """Body 'x' and 'x\\n' produce identical hash — phantom-modified guard."""
    from vault.reader import read_note

    f1 = write_file(tmp_path, "a.md", "---\n---\nx")
    f2 = write_file(tmp_path, "b.md", "---\n---\nx\n")
    r1 = read_note(f1)
    r2 = read_note(f2)

    assert isinstance(r1, Success)
    assert isinstance(r2, Success)
    assert r1.value.content_hash == r2.value.content_hash


def test_read_note_propagates_parse_failure(tmp_path):
    """Malformed YAML → Failure with path in context."""
    from vault.reader import read_note

    f = write_file(tmp_path, "bad.md", "---\nkey: [\n---\nbody")
    result = read_note(f)

    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert str(f) in str(result.context)


def test_read_note_missing_file(tmp_path):
    """Non-existent path → Failure(recoverable=False)."""
    from vault.reader import read_note

    result = read_note(tmp_path / "ghost.md")

    assert isinstance(result, Failure)
    assert result.recoverable is False


def test_read_note_returns_metadata_object(tmp_path):
    """Parsed metadata is a NoteMetadata instance, not a raw dict."""
    from vault.frontmatter import NoteMetadata
    from vault.reader import read_note

    f = write_file(tmp_path, "n.md", "---\nproject: X\n---\nbody")
    result = read_note(f)

    assert isinstance(result, Success)
    assert isinstance(result.value.metadata, NoteMetadata)
    assert result.value.metadata.project == "X"
