"""
vault/indexer.py

Filesystem scan + diff against the SQLite mirror.

scan_vault(root) recursively walks the vault and returns a list of VaultEntry
objects for every readable .md note.  Unreadable notes are logged and skipped;
partial results are still returned as Success.

detect_changes(current, db_path) diffs the live scan against the documents
table, returning a ChangeSummary with four sets:
  added    – paths on disk not in the DB
  modified – paths in both but with a different content_hash
  deleted  – paths in the DB but not on disk
  moved    – hash-matched deleted+added pairs (exactly-1-match rule, DECISION-001)

The indexer does NOT write to the database.  The caller (a pipeline) applies
the summary: upsert() for added/modified, rename() for moved, delete_by_path()
for deleted.

Only .md files are indexed — attachments (PDFs, images, etc.) live in
attachment/ and are intentionally invisible to the indexer.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core.result import Failure, Result, Success
from vault.frontmatter import NoteMetadata
from vault.paths import _is_in_managed_attachment

if TYPE_CHECKING:
    from core.config import VaultConfig

_log = logging.getLogger(__name__)

IGNORE_DIRS = frozenset(
    {
        ".git",
        ".obsidian",
        ".trash",
        ".stversions",
        "node_modules",
        "_assets",
        "_system",
    }
)

# Dotfolders allowed only when their parent folder is named "attachment".
# All other dotfolders are pruned unconditionally.
_DOT_ALLOWLIST: frozenset[str] = frozenset({".summaries"})
# CLAUDE.md is a project/domain index + instructions file, co-authored by AI
# and human. It is not a captured note — exclude it from the documents mirror,
# FTS5 search, and the capture/classify pipelines.
IGNORE_FILES = frozenset({".DS_Store", "Thumbs.db", "CLAUDE.md"})


@dataclass(frozen=True)
class VaultEntry:
    """Parsed representation of one .md note in the vault."""

    path: Path
    vault_path: str
    content_hash: str
    metadata: NoteMetadata


@dataclass(frozen=True)
class ChangeSummary:
    """Four-set diff between live vault and documents mirror."""

    added: list[VaultEntry]
    modified: list[VaultEntry]
    deleted: list[str]
    moved: list[tuple[str, VaultEntry]]


def _has_inbox_sibling(file_path: Path, vault_cfg: VaultConfig) -> bool:
    """Return True if file_path is in inbox/ and already has a .summaries sibling.

    A sibling exists when `inbox/.summaries/<stem>.md` is present — meaning
    the binary has already been indexed (pending-routing or fully classified).
    Re-scanning it would duplicate capture work.

    Args:
        file_path: Absolute path to the binary file.
        vault_cfg: VaultConfig with inbox_path and summaries_subdir.

    Returns:
        True if a sibling .md exists for this binary in inbox/.summaries/.
    """
    inbox_path = vault_cfg.inbox_path
    if inbox_path not in file_path.parents:
        return False
    sibling = inbox_path / vault_cfg.summaries_subdir / f"{file_path.name}.md"
    return sibling.exists()


def scan_non_md_drops(root: Path, vault_config: VaultConfig) -> list[Path]:
    """Return non-.md files in the vault that need to be captured.

    Applies the same skip rules as scan_vault (IGNORE_DIRS, dotfiles,
    .sync-conflict-*, symlinks, .md extension), plus two drop-specific rules:
      Rule 1: Skip files inside any per-project or per-domain attachment/ subtree
              (they are pipeline artifacts — already captured).
      Rule 2: Skip files in inbox/ that already have a .summaries sibling
              (they are pending-routing — Phase 2 Classify will handle them).

    Args:
        root:         Vault root path.
        vault_config: VaultConfig used for rule evaluation.

    Returns:
        list[Path] — plain list; per-file I/O errors are silently skipped.
    """
    drops: list[Path] = []
    inbox_dir = vault_config.inbox_dir
    for dirpath, dirnames, filenames in root.walk():
        dirnames[:] = [
            d
            for d in dirnames
            if d not in IGNORE_DIRS
            and (
                not d.startswith(".")
                or (d in _DOT_ALLOWLIST and dirpath.name in ("attachment", inbox_dir))
            )
            and not (dirpath / d).is_symlink()
        ]

        for name in filenames:
            if name.startswith("."):
                continue
            if ".sync-conflict-" in name:
                continue
            if name.lower().endswith(".md"):
                continue

            file_path = dirpath / name
            if file_path.is_symlink():
                continue
            if _is_in_managed_attachment(file_path, vault_config):
                continue
            if _has_inbox_sibling(file_path, vault_config):
                continue

            drops.append(file_path)

    return drops


def scan_vault(root: Path | None = None) -> Result[list[VaultEntry]]:
    """
    Walk the vault and return VaultEntry for every readable .md note.

    Args:
        root: Vault root path. If None, lazy-imports CONFIG.

    Returns:
        Success([VaultEntry, ...]) — partial on read errors.
        Logs a WARNING listing skipped paths when any note was unreadable.
    """
    _inbox_dir = "inbox"  # VaultConfig default; overridden from CONFIG when root is None
    if root is None:
        from core.config import CONFIG

        root = CONFIG.main.vault.root
        _inbox_dir = CONFIG.main.vault.inbox_dir

    from vault.reader import read_note

    entries: list[VaultEntry] = []
    errors: list[str] = []

    for dirpath, dirnames, filenames in root.walk():
        # Prune directories in-place (controls Path.walk recursion).
        # Allow .summaries/ when parent is attachment/ (per-project summaries)
        # or inbox/ (pending-routing siblings — DECISION-027).
        dirnames[:] = [
            d
            for d in dirnames
            if d not in IGNORE_DIRS
            and (
                not d.startswith(".")
                or (d in _DOT_ALLOWLIST and dirpath.name in ("attachment", _inbox_dir))
            )
            and not (dirpath / d).is_symlink()
        ]

        for name in filenames:
            if name in IGNORE_FILES:
                continue
            if name.startswith("."):
                continue
            if ".sync-conflict-" in name:
                continue
            if not name.lower().endswith(".md"):
                continue

            file_path = dirpath / name
            if file_path.is_symlink():
                continue

            match read_note(file_path):
                case Failure() as f:
                    errors.append(str(file_path))
                    _log.debug("scan_vault skip: %s — %s", file_path, f.error)
                case Success(note):
                    vault_path = unicodedata.normalize(
                        "NFC",
                        str(file_path.relative_to(root).as_posix()),
                    )
                    entries.append(
                        VaultEntry(
                            path=file_path,
                            vault_path=vault_path,
                            content_hash=note.content_hash,
                            metadata=note.metadata,
                        )
                    )

    if errors:
        sample = errors[:3]
        _log.warning(
            "scan_vault: %d files skipped due to read errors (first 3: %s)",
            len(errors),
            sample,
        )

    return Success(entries)


def detect_changes(
    current: list[VaultEntry],
    db_path: Path | None = None,
) -> Result[ChangeSummary]:
    """
    Diff current vault entries against the documents mirror.

    Args:
        current: Output of scan_vault().
        db_path: Override DB path; defaults to CONFIG.main.database.path.

    Returns:
        Success(ChangeSummary) or Failure propagated from all_paths().
    """
    from storage.documents import all_paths

    match all_paths(db_path):
        case Failure() as f:
            return f
        case Success(db_rows):
            pass

    current_by_path: dict[str, VaultEntry] = {e.vault_path: e for e in current}
    db_by_path: dict[str, str] = dict(db_rows)

    # First pass — three sets.
    added_raw: list[VaultEntry] = [
        e for e in current if e.vault_path not in db_by_path
    ]
    deleted_raw: list[str] = [p for p in db_by_path if p not in current_by_path]
    modified: list[VaultEntry] = [
        e
        for e in current
        if e.vault_path in db_by_path
        and e.content_hash != db_by_path[e.vault_path]
    ]

    # Move detection (DECISION-001): collapse deleted+added pairs when exactly
    # one hash match exists, to avoid re-processing notes that were only moved.
    moved: list[tuple[str, VaultEntry]] = []
    remaining_added: list[VaultEntry] = list(added_raw)
    remaining_deleted: list[str] = list(deleted_raw)

    for entry in added_raw:
        candidates = [p for p in remaining_deleted if db_by_path[p] == entry.content_hash]
        # Ambiguous if multiple added entries share the same hash — don't collapse.
        same_hash_added = [e for e in remaining_added if e.content_hash == entry.content_hash]
        if len(candidates) == 1 and len(same_hash_added) == 1:
            moved.append((candidates[0], entry))
            remaining_added.remove(entry)
            remaining_deleted.remove(candidates[0])

    total = len(remaining_added) + len(modified) + len(remaining_deleted) + len(moved)
    _log.info(
        "scan complete: %d added, %d modified, %d deleted, %d moved (total %d)",
        len(remaining_added),
        len(modified),
        len(remaining_deleted),
        len(moved),
        total,
    )

    return Success(
        ChangeSummary(
            added=remaining_added,
            modified=modified,
            deleted=remaining_deleted,
            moved=moved,
        )
    )
