"""tests/test_vault/test_indexer.py"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from core.result import Failure, Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


def _write_md(path: Path, body: str = "body") -> None:
    """Write a minimal valid .md note."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n---\n{body}", encoding="utf-8")


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.rstrip("\n").encode("utf-8")).hexdigest()


def _seed_db(db_path: Path, entries: list[tuple[str, str]]) -> None:
    """Insert (vault_path, content_hash) rows directly into documents."""
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        for vault_path, content_hash in entries:
            conn.execute(
                "INSERT OR REPLACE INTO documents"
                " (vault_path, title, content_hash, updated_by_human)"
                " VALUES (?, ?, ?, 0)",
                (vault_path, Path(vault_path).stem, content_hash),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# scan_vault tests
# ---------------------------------------------------------------------------


def test_scan_finds_only_md_files(vault_root):
    """scan_vault returns only .md files, skipping images and plain text."""
    _write_md(vault_root / "inbox" / "note.md")
    (vault_root / "inbox" / "image.png").write_bytes(b"\x89PNG")
    (vault_root / "inbox" / "note.txt").write_text("plain", encoding="utf-8")

    from vault.indexer import scan_vault

    result = scan_vault(vault_root)
    assert isinstance(result, Success)
    paths = [e.vault_path for e in result.value]
    assert paths == ["inbox/note.md"]


def test_scan_skips_ignore_dirs(vault_root):
    """scan_vault skips .git, .obsidian, and other ignored directories."""
    (vault_root / ".git").mkdir()
    _write_md(vault_root / ".git" / "foo.md")
    (vault_root / ".obsidian").mkdir()
    _write_md(vault_root / ".obsidian" / "x.md")
    _write_md(vault_root / "inbox" / "real.md")

    from vault.indexer import scan_vault

    result = scan_vault(vault_root)
    assert isinstance(result, Success)
    paths = [e.vault_path for e in result.value]
    assert paths == ["inbox/real.md"]


def test_scan_skips_dot_prefixed_dirs_and_files(vault_root):
    """scan_vault skips directories and files whose names start with '.'."""
    (vault_root / ".cache").mkdir()
    _write_md(vault_root / ".cache" / "x.md")
    (vault_root / "inbox" / ".tmp_abc.md").write_text(
        "---\n---\nbody", encoding="utf-8"
    )

    from vault.indexer import scan_vault

    result = scan_vault(vault_root)
    assert isinstance(result, Success)
    assert result.value == []


def test_scan_skips_sync_conflict_files(vault_root):
    """scan_vault skips files containing '.sync-conflict-' in the name."""
    _write_md(vault_root / "inbox" / "note.md")
    _write_md(
        vault_root / "inbox" / "note.sync-conflict-20260514-123456-ABCDEF.md"
    )

    from vault.indexer import scan_vault

    result = scan_vault(vault_root)
    assert isinstance(result, Success)
    paths = [e.vault_path for e in result.value]
    assert paths == ["inbox/note.md"]


def test_scan_does_not_follow_symlinks(vault_root, tmp_path):
    """scan_vault terminates at symlink dirs and does not include their contents."""
    real_dir = tmp_path / "outside"
    real_dir.mkdir()
    _write_md(real_dir / "secret.md")

    link = vault_root / "inbox" / "linked"
    os.symlink(real_dir, link)

    from vault.indexer import scan_vault

    result = scan_vault(vault_root)
    assert isinstance(result, Success)
    paths = [e.vault_path for e in result.value]
    assert not any("secret" in p for p in paths)


def test_scan_partial_success_on_bad_yaml(vault_root, caplog):
    """scan_vault returns Success with valid notes even when one note has bad YAML."""
    import logging

    _write_md(vault_root / "inbox" / "good.md")
    bad = vault_root / "inbox" / "bad.md"
    bad.write_text("---\n: broken: yaml: [\n---\nbody", encoding="utf-8")

    from vault.indexer import scan_vault

    with caplog.at_level(logging.WARNING):
        result = scan_vault(vault_root)

    assert isinstance(result, Success)
    paths = [e.vault_path for e in result.value]
    assert paths == ["inbox/good.md"]
    assert any("skipped" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------------------------------------
# detect_changes tests
# ---------------------------------------------------------------------------


def test_detect_changes_added_only(vault_root, db):
    """Empty DB + one note on disk → added=1, others zero."""
    _write_md(vault_root / "inbox" / "new.md", "hello")

    from vault.indexer import detect_changes, scan_vault

    current = scan_vault(vault_root).value
    result = detect_changes(current, db_path=db)

    assert isinstance(result, Success)
    cs = result.value
    assert len(cs.added) == 1
    assert cs.added[0].vault_path == "inbox/new.md"
    assert cs.modified == []
    assert cs.deleted == []
    assert cs.moved == []


def test_detect_changes_modified(vault_root, db):
    """Note in DB with old hash, same path on disk with new hash → modified=1."""
    note_path = vault_root / "inbox" / "note.md"
    _write_md(note_path, "new content")
    _seed_db(db, [("inbox/note.md", "oldhash000")])

    from vault.indexer import detect_changes, scan_vault

    current = scan_vault(vault_root).value
    result = detect_changes(current, db_path=db)

    assert isinstance(result, Success)
    cs = result.value
    assert len(cs.modified) == 1
    assert cs.modified[0].vault_path == "inbox/note.md"
    assert cs.added == []
    assert cs.deleted == []
    assert cs.moved == []


def test_detect_changes_deleted(vault_root, db):
    """Path in DB but not on disk → deleted=[path]."""
    _seed_db(db, [("inbox/gone.md", "somehash")])

    from vault.indexer import detect_changes

    result = detect_changes([], db_path=db)

    assert isinstance(result, Success)
    cs = result.value
    assert cs.deleted == ["inbox/gone.md"]
    assert cs.added == []
    assert cs.modified == []
    assert cs.moved == []


def test_detect_changes_moved_when_hash_matches(vault_root, db):
    """Same hash, new path → moved, not added+deleted."""
    body = "moved content"
    h = _body_hash(body)
    _seed_db(db, [("inbox/foo.md", h)])
    _write_md(vault_root / "Projects" / "X" / "foo.md", body)

    from vault.indexer import detect_changes, scan_vault

    current = scan_vault(vault_root).value
    result = detect_changes(current, db_path=db)

    assert isinstance(result, Success)
    cs = result.value
    assert len(cs.moved) == 1
    old_path, new_entry = cs.moved[0]
    assert old_path == "inbox/foo.md"
    assert new_entry.vault_path == "Projects/X/foo.md"
    assert cs.added == []
    assert cs.deleted == []


def test_detect_changes_does_not_collapse_ambiguous_move(vault_root, db):
    """Two added entries share a hash with one deleted → no move collapse."""
    body = "duplicate"
    h = _body_hash(body)
    _seed_db(db, [("inbox/orig.md", h)])
    _write_md(vault_root / "Projects" / "A" / "copy1.md", body)
    _write_md(vault_root / "Projects" / "B" / "copy2.md", body)

    from vault.indexer import detect_changes, scan_vault

    current = scan_vault(vault_root).value
    result = detect_changes(current, db_path=db)

    assert isinstance(result, Success)
    cs = result.value
    assert cs.moved == []
    assert len(cs.added) == 2
    assert "inbox/orig.md" in cs.deleted


# ---------------------------------------------------------------------------
# scan_non_md_drops tests
# ---------------------------------------------------------------------------


def test_scan_non_md_drops_returns_non_md_paths_not_in_attachment(vault_root):
    """Returns non-.md files outside attachment/ subtree."""
    att = vault_root / "attachment"
    att.mkdir(exist_ok=True)
    (vault_root / "inbox" / "report.pdf").write_bytes(b"%PDF content")
    (vault_root / "inbox" / "note.md").write_text("---\n---\nbody", encoding="utf-8")
    (att / "already.pdf").write_bytes(b"%PDF already captured")

    from vault.indexer import scan_non_md_drops

    result = scan_non_md_drops(vault_root, att)
    assert len(result) == 1
    assert result[0] == vault_root / "inbox" / "report.pdf"


def test_scan_non_md_drops_excludes_md_files(vault_root):
    """scan_non_md_drops skips .md files (handled by scan_vault)."""
    att = vault_root / "attachment"
    att.mkdir(exist_ok=True)
    (vault_root / "inbox" / "note.md").write_text("---\n---\nbody", encoding="utf-8")

    from vault.indexer import scan_non_md_drops

    result = scan_non_md_drops(vault_root, att)
    assert result == []


def test_scan_non_md_drops_excludes_ignore_dirs(vault_root):
    """scan_non_md_drops skips files inside IGNORE_DIRS."""
    att = vault_root / "attachment"
    att.mkdir(exist_ok=True)
    (vault_root / ".git").mkdir()
    (vault_root / ".git" / "pack.bin").write_bytes(b"git data")
    (vault_root / "inbox" / "real.pdf").write_bytes(b"%PDF content")

    from vault.indexer import scan_non_md_drops

    result = scan_non_md_drops(vault_root, att)
    paths = [p.name for p in result]
    assert "pack.bin" not in paths
    assert "real.pdf" in paths


def test_scan_non_md_drops_excludes_dotfiles(vault_root):
    """scan_non_md_drops skips dotfiles and .sync-conflict-* files."""
    att = vault_root / "attachment"
    att.mkdir(exist_ok=True)
    (vault_root / "inbox" / ".DS_Store").write_bytes(b"macos junk")
    (vault_root / "inbox" / "report.sync-conflict-20260514-123456-ABCDEF.pdf").write_bytes(b"conflict")
    (vault_root / "inbox" / "real.pdf").write_bytes(b"%PDF")

    from vault.indexer import scan_non_md_drops

    result = scan_non_md_drops(vault_root, att)
    names = [p.name for p in result]
    assert ".DS_Store" not in names
    assert not any(".sync-conflict-" in n for n in names)
    assert "real.pdf" in names


def test_scan_non_md_drops_returns_empty_when_all_in_attachment(vault_root):
    """Returns [] when all non-md files are already inside attachment/."""
    att = vault_root / "attachment"
    att.mkdir(exist_ok=True)
    (att / "captured.pdf").write_bytes(b"%PDF captured")

    from vault.indexer import scan_non_md_drops

    result = scan_non_md_drops(vault_root, att)
    assert result == []
