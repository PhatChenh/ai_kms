"""
tests/test_vault/test_smoke.py

End-to-end proof that the vault layer composes correctly.

Exercises the same call sequence that pipelines/capture.py will use in Phase 1,
without any pipeline code existing yet.  Uses vault_root (tmp vault + CONFIG monkeypatch)
and a separate tmp DB — no real vault or config file needed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.result import Failure, Success
from storage import documents
from storage.db import init_db
from vault import indexer
from vault.frontmatter import NoteMetadata
from vault.writer import move_note, write_note


@pytest.mark.smoke
def test_vault_layer_end_to_end(vault_root: Path, tmp_path: Path) -> None:
    """
    Eleven-step composition proof for the vault layer.

    vault_root provides the tmp vault skeleton and CONFIG monkeypatch so that
    write_note/_to_vault_path resolve paths correctly.  db_path is a fresh
    SQLite DB next to the vault directory.
    """
    # ── Step 1: init DB ────────────────────────────────────────────────────
    db_path = tmp_path / "kb.db"
    assert isinstance(init_db(db_path), Success)

    # ── Step 2: write a new note ────────────────────────────────────────────
    note_path = vault_root / "inbox" / "test.md"
    meta = NoteMetadata(project="Zalopay", tags=["smoke"])
    write_result = write_note(note_path, "hello world", meta, actor="ai")
    assert isinstance(write_result, Success), write_result
    outcome = write_result.value
    assert outcome.vault_path == "inbox/test.md"

    # ── Step 3: upsert to SQLite ────────────────────────────────────────────
    upsert_result = documents.upsert(outcome, db_path=db_path)
    assert isinstance(upsert_result, Success)
    assert upsert_result.value > 0

    # ── Step 4: get_by_path matches outcome ────────────────────────────────
    row_result = documents.get_by_path(outcome.vault_path, db_path=db_path)
    assert isinstance(row_result, Success)
    row = row_result.value
    assert row is not None
    assert row.vault_path == outcome.vault_path
    assert row.content_hash == outcome.content_hash

    # ── Step 5: scan_vault returns exactly one entry ───────────────────────
    scan = indexer.scan_vault(vault_root)
    assert isinstance(scan, Success)
    entries = scan.value
    assert len(entries) == 1
    assert entries[0].vault_path == "inbox/test.md"
    assert entries[0].content_hash == outcome.content_hash

    # ── Step 6: detect_changes → empty (DB and disk in sync) ─────────────
    cs = indexer.detect_changes(entries, db_path=db_path).value
    assert cs.added == []
    assert cs.modified == []
    assert cs.deleted == []
    assert cs.moved == []

    # ── Step 7: modify the note ─────────────────────────────────────────────
    write_result2 = write_note(note_path, "updated content", meta, actor="ai")
    assert isinstance(write_result2, Success)
    new_outcome = write_result2.value
    assert new_outcome.content_hash != outcome.content_hash

    # ── Step 8: detect_changes WITHOUT re-upserting → modified=[entry] ────
    entries2 = indexer.scan_vault(vault_root).value
    cs2 = indexer.detect_changes(entries2, db_path=db_path).value
    assert len(cs2.modified) == 1
    assert cs2.modified[0].vault_path == "inbox/test.md"
    assert cs2.added == []
    assert cs2.deleted == []

    # ── Step 9: apply change; detect_changes → empty ───────────────────────
    documents.upsert(new_outcome, db_path=db_path)
    entries3 = indexer.scan_vault(vault_root).value
    cs3 = indexer.detect_changes(entries3, db_path=db_path).value
    assert cs3.added == [] and cs3.modified == [] and cs3.deleted == [] and cs3.moved == []

    # ── Step 10: move note; rename in DB; scan + detect → empty ───────────
    new_path = vault_root / "Projects" / "test_moved.md"
    move_result = move_note(note_path, new_path, actor="ai")
    assert isinstance(move_result, Success)
    moved_outcome = move_result.value

    rename_result = documents.rename(
        "inbox/test.md", moved_outcome.vault_path, db_path=db_path
    )
    assert isinstance(rename_result, Success)

    entries4 = indexer.scan_vault(vault_root).value
    cs4 = indexer.detect_changes(entries4, db_path=db_path).value
    assert cs4.added == [] and cs4.modified == [] and cs4.deleted == [] and cs4.moved == []

    # ── Step 11: lock note; AI write blocked ──────────────────────────────
    lock_result = write_note(new_path, "human revision", meta, actor="human")
    assert isinstance(lock_result, Success)

    ai_result = write_note(new_path, "ai tries to overwrite", meta, actor="ai")
    assert isinstance(ai_result, Failure)
    assert ai_result.recoverable is False
