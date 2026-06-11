"""
mcp_server/_move.py

Note Mover Helper — the backing logic for kms_move (Component 9).

Moves a vault note into a named project or domain so its on-disk location,
frontmatter label, and search index all agree — and the watcher doesn't undo it.

Seven-step proven recipe (EXACT ORDER MATTERS):
  1. Resolve dst_name → dst folder path
  2. read_note(src) → build new_meta (C-03: caller owns the merge)
  3. old_vault_path = to_vault_path(src) — capture BEFORE the move
  4. get_active().register(dst) — register BEFORE the move
  5. move_note(src, dst, actor="ai") — carries NO metadata; blocks human-locked
  6. outcome = write_note(dst, new_meta, actor="ai") — sets the new label
  7. replace_path(old_vault_path, outcome) — 2nd arg is WriteOutcome, NOT a path (A7b!)
"""

from __future__ import annotations

from pathlib import Path

from core.result import Failure, Result, Success
from storage.documents import replace_path
from vault.move_guard import get_active
from vault.paths import domain_dir, project_dir, to_vault_path
from vault.reader import read_note
from vault.writer import move_note, write_note


def move(
    src: Path,
    dst_name: str,
    dst_kind: str,
    db_path: Path | None = None,
) -> Result[str]:
    """Move a note into a named project or domain.

    Args:
        src:      Absolute path to the note to move (must exist).
        dst_name: Destination project or domain name (e.g. "Alpha").
        dst_kind: "project" or "domain".
        db_path:  Override DB path for replace_path (test injection).

    Returns:
        Success(str) with a human-readable move confirmation,
        or Failure(recoverable=False).
        Human-locked notes return a clear Failure (C-02).
    """
    # 1. Resolve dst_name → dst folder path
    if dst_kind == "project":
        folder = project_dir(dst_name)
    elif dst_kind == "domain":
        folder = domain_dir(dst_name)
    else:
        return Failure(
            error=f"unknown dst_kind: {dst_kind!r}",
            recoverable=False,
            context={"src": str(src), "dst_name": dst_name},
        )
    dst = folder / src.name

    # 2. Read src → build new_meta (C-03: caller owns the merge)
    match read_note(src):
        case Failure() as f:
            return f
        case Success(value=note):
            pass

    if dst_kind == "project":
        new_meta = note.metadata.model_copy(update={"project": dst_name})
    else:  # domain
        new_tags = [t for t in note.metadata.tags if not t.startswith("domain/")]
        new_tags.append(f"domain/{dst_name}")
        new_meta = note.metadata.model_copy(update={"project": None, "tags": new_tags})

    # 3. Capture old_vault_path BEFORE the move
    old_vault_path = to_vault_path(src)

    # 4. Register guard BEFORE the move
    guard = get_active()
    if guard is not None:
        guard.register(dst)

    # 5. move_note — carries NO metadata; blocks human-locked moves
    match move_note(src, dst, actor="ai"):
        case Failure() as f:
            return f
        case Success():
            pass

    # 6. write_note with new_meta — sets the new label
    match write_note(dst, note.content, new_meta, actor="ai"):
        case Failure() as f:
            return f
        case Success(value=outcome):
            pass

    # 7. replace_path — 2nd arg is WriteOutcome, NOT a path (A7b!)
    match replace_path(old_vault_path, outcome, db_path=db_path):
        case Failure() as f:
            return f
        case Success():
            return Success(f"Moved {src.name} to {dst_kind}/{dst_name}")
