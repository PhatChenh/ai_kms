"""
tests/test_mcp_server/test_move.py

Phase 5 -- Note Mover Helper (kms_move backing).
TDD: RED tests for _move.py.

RED 1 (P4-MCP-07): project move -- file at new path, frontmatter updated,
    index updated, guard registered.
RED 2 (A7b trap guard): replace_path called with WriteOutcome, not dst path.
RED 3 (A7 trap -- label changes): frontmatter project matches new home.
RED 4 (human-locked, C-02): human-locked note returns clear Failure.
RED 5 (order -- register before move): guard registered before move_note.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.result import Failure, Success
from storage.db import init_db
from vault.frontmatter import NoteMetadata, parse
from vault.move_guard import MoveGuard
from vault.paths import to_vault_path
from vault.reader import read_note
from vault.writer import WriteOutcome, move_note, write_note


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def vault_root(tmp_path: Path, monkeypatch) -> Path:
    """Empty vault skeleton at tmp_path/vault with patched CONFIG.

    Overrides the autouse _setup_env from tests/test_mcp_server/conftest.py
    so that vault functions see this temp vault instead of the config-file one.
    """
    root = tmp_path / "vault"
    root.mkdir(exist_ok=True)
    for d in ["inbox", "Projects", "Domain"]:
        (root / d).mkdir(exist_ok=True)

    from core.config import VaultConfig

    vc = VaultConfig(root=root)

    import core.config as cfg_module

    fake_config = MagicMock()
    fake_config.main.vault = vc
    monkeypatch.setattr(cfg_module, "_CONFIG", fake_config)

    return root


@pytest.fixture()
def test_db(tmp_path: Path) -> Path:
    """Temp SQLite DB with full schema (documents + FTS5 + vec0)."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


@pytest.fixture()
def active_guard(monkeypatch) -> MoveGuard:
    """A MoveGuard set as the active global guard (via vault.move_guard._active)."""
    guard = MoveGuard()
    monkeypatch.setattr("vault.move_guard._active", guard)
    return guard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_note(path: Path, content: str, **meta_kwargs) -> WriteOutcome:
    """Write an AI-authored note and return the WriteOutcome."""
    metadata = NoteMetadata(**meta_kwargs)
    result = write_note(path, content, metadata, actor="ai")
    assert isinstance(result, Success), f"Failed to write note: {result}"
    return result.value


def _make_human_note(path: Path, content: str, **meta_kwargs) -> WriteOutcome:
    """Write a human-authored note and return the WriteOutcome."""
    metadata = NoteMetadata(**meta_kwargs)
    result = write_note(path, content, metadata, actor="human")
    assert isinstance(result, Success), f"Failed to write human note: {result}"
    return result.value


def _upsert_to_db(outcome: WriteOutcome, db_path: Path) -> None:
    """Insert the outcome into the documents DB so get_by_path can find it."""
    from storage.documents import upsert_from_upload

    r = upsert_from_upload(
        vault_path=outcome.vault_path,
        extracted_text="dummy text content",
        content_hash=outcome.content_hash or "dummy_hash",
        db_path=db_path,
    )
    assert isinstance(r, Success), f"Failed to upsert_from_upload: {r}"


# ---------------------------------------------------------------------------
# RED Test 1 -- project move (P4-MCP-07)
# ---------------------------------------------------------------------------


class TestProjectMove:
    """Move a note from inbox to a named project."""

    def test_move_to_project_file_at_new_path(
        self, vault_root: Path, test_db: Path, active_guard: MoveGuard
    ):
        """After move(src, 'Alpha', 'project'):
        - File exists at Projects/Alpha/<name>
        - File is NOT at old inbox path
        - get_by_path returns row at new vault_path
        - get_by_path returns None for old vault_path
        """
        from mcp_server._move import move

        # Write a note in inbox
        src = vault_root / "inbox" / "my_note.md"
        outcome = _make_note(src, "Hello world", type="note", project=None)
        _upsert_to_db(outcome, test_db)
        old_vault_path = outcome.vault_path

        # Move to project Alpha
        result = move(src, "Alpha", "project", db_path=test_db)

        assert isinstance(result, Success)
        # File at new path
        new_path = vault_root / "Projects" / "Alpha" / "my_note.md"
        assert new_path.exists()
        # File NOT at old path
        assert not src.exists()

        # DB: new path exists, old path gone
        from storage.documents import get_by_path

        new_row = get_by_path(to_vault_path(new_path), db_path=test_db)
        assert isinstance(new_row, Success)
        assert new_row.value is not None
        assert new_row.value.vault_path == to_vault_path(new_path)

        old_row = get_by_path(old_vault_path, db_path=test_db)
        assert isinstance(old_row, Success)
        assert old_row.value is None, "Old vault_path should be deleted from index"

    def test_move_to_project_frontmatter_has_project(
        self, vault_root: Path, test_db: Path, active_guard: MoveGuard
    ):
        """After move to 'Alpha', frontmatter project == 'Alpha'."""
        from mcp_server._move import move

        src = vault_root / "inbox" / "note2.md"
        _make_note(src, "Content", type="note", project=None)
        outcome = _make_note(src, "Content", type="note", project=None)
        _upsert_to_db(outcome, test_db)

        result = move(src, "Alpha", "project", db_path=test_db)
        assert isinstance(result, Success)

        new_path = vault_root / "Projects" / "Alpha" / "note2.md"
        parse_result = parse(new_path)
        assert isinstance(parse_result, Success)
        metadata, _body = parse_result.value
        assert metadata.project == "Alpha"

    def test_move_to_project_upsert_preserves_type(
        self, vault_root: Path, test_db: Path, active_guard: MoveGuard
    ):
        """After move, index row preserves original type (C-03: carry all fields)."""
        from mcp_server._move import move

        src = vault_root / "inbox" / "typed_note.md"
        outcome = _make_note(src, "Typed content", type="meeting-notes", project=None)
        _upsert_to_db(outcome, test_db)

        result = move(src, "Alpha", "project", db_path=test_db)
        assert isinstance(result, Success)

        new_path = vault_root / "Projects" / "Alpha" / "typed_note.md"
        from storage.documents import get_by_path

        row_r = get_by_path(to_vault_path(new_path), db_path=test_db)
        assert isinstance(row_r, Success)
        assert row_r.value is not None
        assert row_r.value.note_type == "meeting-notes"


# ---------------------------------------------------------------------------
# RED Test 2 -- A7b trap guard
# ---------------------------------------------------------------------------


class TestA7bTrapGuard:
    """replace_path MUST be called with WriteOutcome, NOT dst path."""

    def test_replace_path_receives_write_outcome_not_path(
        self, vault_root: Path, test_db: Path, active_guard: MoveGuard, monkeypatch
    ):
        """Capture the second arg to replace_path -- assert it has
        .metadata and .vault_path attributes (WriteOutcome), NOT Path attributes.
        """
        from mcp_server._move import move

        src = vault_root / "inbox" / "trap_note.md"
        outcome = _make_note(src, "Trap test", type="note", project=None)
        _upsert_to_db(outcome, test_db)

        captured_arg = None

        def _fake_replace_path(old_vp: str, second_arg, db_path=None, batch_id=None):
            nonlocal captured_arg
            captured_arg = second_arg
            # Call the real one so the DB is actually updated
            from storage.documents import replace_path as real_replace

            return real_replace(old_vp, second_arg, db_path=db_path, batch_id=batch_id)

        monkeypatch.setattr("mcp_server._move.replace_path", _fake_replace_path)

        result = move(src, "Beta", "project", db_path=test_db)
        assert isinstance(result, Success)

        # A7b: second arg must be WriteOutcome, NOT a Path
        assert captured_arg is not None, "replace_path was not called"
        assert not isinstance(captured_arg, Path), (
            f"A7b REGRESSION: replace_path received a Path ({captured_arg}), "
            f"not a WriteOutcome"
        )
        assert hasattr(captured_arg, "metadata"), (
            "A7b: replace_path arg has no .metadata -- not a WriteOutcome"
        )
        assert hasattr(captured_arg, "vault_path"), (
            "A7b: replace_path arg has no .vault_path -- not a WriteOutcome"
        )


# ---------------------------------------------------------------------------
# RED Test 3 -- A7 trap (label changes)
# ---------------------------------------------------------------------------


class TestA7TrapLabelChanges:
    """After move, on-disk frontmatter project matches the NEW home (A7)."""

    def test_project_label_set_on_disk_after_move(
        self, vault_root: Path, test_db: Path, active_guard: MoveGuard
    ):
        """A move-only path would leave the old (or no) project label.
        Prove the separate write_note(dst, new_meta) ran by checking the
        frontmatter on disk.
        """
        from mcp_server._move import move

        src = vault_root / "inbox" / "label_test.md"
        _make_note(src, "Label check", type="note", project=None)
        outcome = _make_note(src, "Label check", type="note", project=None)
        _upsert_to_db(outcome, test_db)

        result = move(src, "Gamma", "project", db_path=test_db)
        assert isinstance(result, Success)

        # Read the on-disk note
        new_path = vault_root / "Projects" / "Gamma" / "label_test.md"
        read_r = read_note(new_path)
        assert isinstance(read_r, Success)
        assert read_r.value.metadata.project == "Gamma", (
            f"A7 TRAP: on-disk project is '{read_r.value.metadata.project}', "
            f"expected 'Gamma'. The write_note label update did not run."
        )

    def test_domain_move_replaces_domain_tag(
        self, vault_root: Path, test_db: Path, active_guard: MoveGuard
    ):
        """Domain move sets domain/<D> tag and clears project."""
        from mcp_server._move import move

        # Create the destination domain folder
        (vault_root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)

        src = vault_root / "inbox" / "domain_test.md"
        _make_note(
            src,
            "Domain check",
            type="note",
            project="OldProject",
            tags=["domain/OldDomain"],
        )
        outcome = _make_note(
            src,
            "Domain check",
            type="note",
            project="OldProject",
            tags=["domain/OldDomain"],
        )
        _upsert_to_db(outcome, test_db)

        result = move(src, "Finance", "domain", db_path=test_db)
        assert isinstance(result, Success)

        new_path = vault_root / "Domain" / "Finance" / "domain_test.md"
        read_r = read_note(new_path)
        assert isinstance(read_r, Success)
        meta = read_r.value.metadata
        # Project cleared
        assert meta.project is None, (
            f"Domain move should clear project, got: {meta.project}"
        )
        # Domain tag set
        assert "domain/Finance" in meta.tags, (
            f"Domain move should set domain/Finance tag, got: {meta.tags}"
        )
        # Old domain tag removed
        assert "domain/OldDomain" not in meta.tags, (
            f"Domain move should remove old domain tag, got: {meta.tags}"
        )


# ---------------------------------------------------------------------------
# RED Test 4 -- human-locked (C-02)
# ---------------------------------------------------------------------------


class TestHumanLocked:
    """A human-locked note must NOT be moved by AI. Return clear Failure."""

    def test_human_locked_note_returns_failure(
        self, vault_root: Path, test_db: Path, active_guard: MoveGuard
    ):
        """move() on a note with updated_by_human=True returns Failure."""
        from mcp_server._move import move

        src = vault_root / "inbox" / "locked_note.md"
        _make_human_note(src, "Human content", type="note", project=None)
        # Human note is auto-upserted by write_note? No, we need to upsert manually
        # Actually write_note doesn't upsert to DB. We need to upsert for the test.
        # But the key test is: move_note should block the human-locked note.
        # No DB needed for this test -- the block is in move_note.

        result = move(src, "Alpha", "project")
        assert isinstance(result, Failure), (
            f"C-02: Human-locked note should return Failure, got {type(result).__name__}"
        )
        assert result.recoverable is False
        # Source file should still exist (not moved)
        assert src.exists(), "Human-locked note should not be moved"

    def test_human_locked_note_not_overwritten(
        self, vault_root: Path, test_db: Path, active_guard: MoveGuard
    ):
        """Verify the human-locked note content is intact after failed move."""
        from mcp_server._move import move

        src = vault_root / "inbox" / "locked2.md"
        _make_human_note(src, "Precious human text", type="note", project=None)

        result = move(src, "Beta", "project")
        assert isinstance(result, Failure)

        # Content intact
        read_r = read_note(src)
        assert isinstance(read_r, Success)
        assert "Precious human text" in read_r.value.content


# ---------------------------------------------------------------------------
# RED Test 5 -- order: register before move_note
# ---------------------------------------------------------------------------


class TestRegisterBeforeMove:
    """The move guard MUST be registered BEFORE move_note is called."""

    def test_guard_registered_before_move_note(
        self, vault_root: Path, test_db: Path, active_guard, monkeypatch
    ):
        """Prove get_active().register(dst) happens before move_note by
        tracking call order.
        """
        from mcp_server._move import move

        src = vault_root / "inbox" / "order_test.md"
        outcome = _make_note(src, "Order test", type="note", project=None)
        _upsert_to_db(outcome, test_db)

        call_log = []

        # Wrap move_note to log when it's called.
        # _move.py imports move_note at module scope, so we must patch
        # the reference inside _move.py, not vault.writer.
        import mcp_server._move as move_mod

        _real_move_note = move_mod.move_note

        def _logging_move_note(s, d, actor="ai"):
            call_log.append("move_note")
            return _real_move_note(s, d, actor=actor)

        monkeypatch.setattr(move_mod, "move_note", _logging_move_note)

        # Also track register calls by wrapping the guard
        _real_register = active_guard.register

        def _logging_register(path, ttl=None):
            call_log.append("register")
            return _real_register(path, ttl=ttl)

        monkeypatch.setattr(active_guard, "register", _logging_register)

        result = move(src, "Delta", "project", db_path=test_db)
        assert isinstance(result, Success)

        # Find the first occurrence of each call
        try:
            reg_idx = call_log.index("register")
            move_idx = call_log.index("move_note")
        except ValueError as e:
            pytest.fail(
                f"Expected both 'register' and 'move_note' in call log: {call_log}"
            )

        assert reg_idx < move_idx, (
            f"Order violation: register (idx {reg_idx}) must happen BEFORE "
            f"move_note (idx {move_idx}). Full log: {call_log}"
        )
