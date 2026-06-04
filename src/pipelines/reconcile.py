"""pipelines/reconcile.py

6-stage reconcile pipeline: paths → orphan_binaries → stale_binaries → orphan_siblings → stale_tags → stale_batch_refs.
Entry point: reconcile(ctx) -> Result[ReconcileResult]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

from core.audit import write as audit_write
from core.confidence import AIDecision
from core.pipeline import PipelineContext
from core.result import Failure, Result, Success
from pipelines.capture import capture_file
from vault.paths import _is_in_managed_attachment, _is_managed_summaries_area, _location_context, resolve_placement

_log = logging.getLogger(__name__)

__all__ = [
    "ReconcileResult",
    "reconcile_paths",
    "reconcile_orphan_binaries",
    "reconcile_stale_binaries",
    "reconcile_orphan_siblings",
    "reconcile_stale_tags",
    "reconcile_stale_batch_refs",
    "reconcile_editable_migration",
    "reconcile",
]


@dataclass(frozen=True)
class ReconcileResult:
    """Counters accumulated across the 6 reconcile stages."""

    paths_reconciled: int = 0
    new_captures: int = 0
    restale_count: int = 0
    orphans_cleaned: int = 0
    tags_updated: int = 0
    batch_refs_cleared: int = 0
    editable_migrations: int = 0

    def replace(self, **kwargs: int) -> ReconcileResult:
        return replace(self, **kwargs)


# ---------------------------------------------------------------------------
# Stage 1 — fix moved / deleted note paths in the search index
# ---------------------------------------------------------------------------


async def reconcile_paths(
    result: ReconcileResult, ctx: PipelineContext, entries: list
) -> Result[ReconcileResult]:
    """Diff vault against DB mirror; apply renames and deletes.

    Uses the same detect_changes + scan_vault logic as ``kms capture --scan``,
    applied here as the first step of every reconcile run.

    Args:
        entries: Pre-scanned VaultEntry list from reconcile() entry point.
    """
    from vault.indexer import detect_changes

    vault_root = ctx.config.vault.root
    db_path = ctx.db_path

    match detect_changes(entries, db_path=db_path):
        case Failure() as f:
            return f
        case Success(changes):
            pass

    reconciled = 0

    import storage.documents as documents

    for old_vp, entry in changes.moved:
        match documents.rename(old_vp, entry.vault_path, db_path=db_path):
            case Success(value=n):
                reconciled += n
            case Failure() as f:
                _log.warning("reconcile.rename_failed old=%s new=%s error=%s",
                             old_vp, entry.vault_path, f.error)

    for vp in changes.deleted:
        match documents.delete_by_path(vp, db_path=db_path):
            case Success(value=n):
                reconciled += n
            case Failure() as f:
                _log.warning("reconcile.delete_failed path=%s error=%s",
                             vp, f.error)

    return Success(result.replace(paths_reconciled=reconciled))


# ---------------------------------------------------------------------------
# Stage 2 — find binaries with no summary, capture them
# ---------------------------------------------------------------------------


async def reconcile_orphan_binaries(
    result: ReconcileResult, ctx: PipelineContext
) -> Result[ReconcileResult]:
    """Walk every attachment/ folder; capture binaries missing a .summaries sibling."""
    vault_cfg = ctx.config.vault
    new_captures = 0

    for att_dir in vault_cfg.root.rglob(vault_cfg.attachment_dir):
        if not att_dir.is_dir():
            continue
        for entry in att_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.name.startswith("."):
                continue
            if entry.suffix.lower() == ".md":
                continue
            if entry.is_symlink():
                continue
            if not _is_in_managed_attachment(entry, vault_cfg):
                continue

            sibling = att_dir / vault_cfg.summaries_subdir / f"{entry.name}.md"
            if sibling.exists():
                continue

            match await capture_file(entry, context=ctx):
                case Success():
                    new_captures += 1
                    audit_write(
                        AIDecision(action="capture", confidence=1.0,
                                   source_ids=[str(entry)], reasoning="orphan binary"),
                        pipeline="reconcile", stage="reconcile_orphan_binaries",
                        outcome="ORPHAN_BINARY_CAPTURED", db_path=ctx.db_path,
                    )
                case Failure() as f:
                    _log.warning("reconcile.orphan_capture_failed path=%s error=%s",
                                 entry, f.error)

    return Success(result.replace(new_captures=new_captures))


# ---------------------------------------------------------------------------
# Stage 3 — find stale binaries (changed after summary was written)
# ---------------------------------------------------------------------------


async def reconcile_stale_binaries(
    result: ReconcileResult, ctx: PipelineContext
) -> Result[ReconcileResult]:
    """Re-summarize binaries whose mtime is newer than their sibling's mtime."""
    vault_cfg = ctx.config.vault
    restale_count = 0

    for att_dir in vault_cfg.root.rglob(vault_cfg.attachment_dir):
        if not att_dir.is_dir():
            continue
        summaries_dir = att_dir / vault_cfg.summaries_subdir
        if not summaries_dir.is_dir():
            continue

        for entry in att_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.name.startswith("."):
                continue
            if entry.suffix.lower() == ".md":
                continue
            if entry.is_symlink():
                continue
            if not _is_in_managed_attachment(entry, vault_cfg):
                continue

            sibling = summaries_dir / f"{entry.name}.md"
            if not sibling.exists():
                continue

            if entry.stat().st_mtime > sibling.stat().st_mtime:
                match await capture_file(entry, context=ctx):
                    case Success():
                        restale_count += 1
                        audit_write(
                            AIDecision(action="capture", confidence=1.0,
                                       source_ids=[str(entry)],
                                       reasoning="stale binary"),
                            pipeline="reconcile", stage="reconcile_stale_binaries",
                            outcome="BINARY_STALE_RESUMMARIZED", db_path=ctx.db_path,
                        )
                    case Failure() as f:
                        _log.warning("reconcile.restale_capture_failed path=%s error=%s",
                                     entry, f.error)

    return Success(result.replace(restale_count=restale_count))


# ---------------------------------------------------------------------------
# Stage 4 — find orphaned summaries whose binary is gone
# ---------------------------------------------------------------------------


async def reconcile_orphan_siblings(
    result: ReconcileResult, ctx: PipelineContext
) -> Result[ReconcileResult]:
    """Remove sibling .md files whose attachment_path binary no longer exists."""
    from vault.reader import read_note
    import storage.documents as documents

    vault_cfg = ctx.config.vault
    db_path = ctx.db_path
    orphans_cleaned = 0

    for summaries_dir in vault_cfg.root.rglob(vault_cfg.summaries_subdir):
        if not summaries_dir.is_dir():
            continue
        # Scope guard (issue #2): only walk .summaries/ inside managed areas
        # (Projects/<A>/attachment/, Domain/<D>/attachment/, or inbox/). A
        # stray .summaries/ folder elsewhere in the vault is user-owned.
        if not _is_managed_summaries_area(summaries_dir, vault_cfg):
            continue
        for entry in summaries_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.suffix.lower() == ".md":
                continue
            if entry.name.startswith("."):
                continue

            match read_note(entry):
                case Failure():
                    continue
                case Success(note):
                    pass

            # Type guard (issue #3): only unlink AI-written attachment
            # summaries. A user-placed .md inside .summaries/ (different
            # type, or no type at all) is left alone.
            if note.metadata.type != "attachment-summary":
                continue

            attachment_path = note.metadata.attachment_path
            if attachment_path is None:
                # No binary pointer — orphan by definition
                pass
            else:
                binary = vault_cfg.root / attachment_path
                if binary.exists():
                    continue  # Binary exists — healthy sibling

            if note.metadata.updated_by_human:
                _log.warning("reconcile.orphan_skip_human path=%s", entry)
                continue

            # Compute vault-relative path for DB deletion.
            # entry is relative to vault_root — use parts after root.
            try:
                vp = str(entry.relative_to(vault_cfg.root).as_posix())
            except ValueError:
                vp = str(entry)

            match documents.delete_by_path(vp, db_path=db_path):
                case Success():
                    pass
                case Failure() as f:
                    _log.warning("reconcile.orphan_db_delete_failed path=%s error=%s",
                                 vp, f.error)
                    continue

            try:
                entry.unlink()
            except OSError as exc:
                _log.warning("reconcile.orphan_unlink_failed path=%s error=%s",
                             entry, exc)
                continue

            orphans_cleaned += 1
            audit_write(
                AIDecision(action="delete", confidence=1.0,
                           source_ids=[vp], reasoning="orphan sibling"),
                pipeline="reconcile", stage="reconcile_orphan_siblings",
                outcome="ORPHAN_SIBLING_CLEANED", db_path=ctx.db_path,
            )

    return Success(result.replace(orphans_cleaned=orphans_cleaned))


# ---------------------------------------------------------------------------
# Stage 5 — fix stale domain/<X> tags and project: fields vault-wide
# ---------------------------------------------------------------------------


async def reconcile_stale_tags(
    result: ReconcileResult, ctx: PipelineContext, entries: list
) -> Result[ReconcileResult]:
    """Remove stale domain/<X> tags and fix stale project: fields for every note.

    For each .md note in entries:
    - Strips any domain/<X> tag where X is no longer a valid domain folder.
    - Adds missing domain/<X> tag when the note lives under Domain/<X>/.
    - Sets project: to the folder name when the note lives under Projects/<A>/.
    - Skips notes with updated_by_human=True (write_note returns Failure recoverable=False).

    Args:
        entries: Pre-scanned VaultEntry list from reconcile() entry point.
    """
    from vault.paths import _location_context, load_valid_domains
    from vault.reader import read_note
    from vault.writer import write_note

    vault_cfg = ctx.config.vault
    valid_domains = load_valid_domains(vault_cfg.root)
    tags_updated = 0

    for entry in entries:
        if not entry.vault_path.lower().endswith(".md"):
            continue

        # Skip sibling summaries — Stage 5 must not touch attachment-summary files
        # or set project: on them (they live under attachment/.summaries/, not Projects/<A>/).
        if vault_cfg.summaries_subdir in entry.vault_path.split("/"):
            continue

        match read_note(entry.path):
            case Failure() as f:
                _log.warning("reconcile_stale_tags.read_failed path=%s error=%s",
                             entry.vault_path, f.error)
                continue
            case Success(note):
                pass

        dirty = False
        new_tags = list(note.metadata.tags)
        new_project = note.metadata.project

        # Remove stale domain/<X> tags (domains that no longer exist as folders)
        cleaned_tags = [
            t for t in new_tags
            if not (t.startswith("domain/") and t[len("domain/"):] not in valid_domains)
        ]
        if len(cleaned_tags) != len(new_tags):
            new_tags = cleaned_tags
            dirty = True

        # Apply location context: add missing domain tag or fix project field
        loc_type, loc_name = _location_context(entry.path, vault_cfg)
        if loc_type == "domain" and loc_name is not None:
            domain_tag = f"domain/{loc_name}"
            if domain_tag not in new_tags:
                new_tags.append(domain_tag)
                dirty = True
        elif loc_type == "project" and loc_name is not None:
            if note.metadata.project != loc_name:
                new_project = loc_name
                dirty = True

        if not dirty:
            continue

        new_meta = note.metadata.model_copy(update={"tags": new_tags, "project": new_project})
        match write_note(entry.path, note.content, new_meta, actor="ai"):
            case Success():
                tags_updated += 1
            case Failure(recoverable=False, context=fc) as f if fc and "vault_path" in fc:
                # updated_by_human=True — write_note's human-lock branch is the
                # only recoverable=False failure that carries a "vault_path"
                # context key (writer.py human-lock guard). Keying off that
                # context key avoids coupling to the prose error message.
                # Skip silently; no retry will fix this.
                pass
            case Failure() as f:
                _log.warning("reconcile_stale_tags.write_failed path=%s error=%s",
                             entry.vault_path, f.error)

    return Success(result.replace(tags_updated=result.tags_updated + tags_updated))


# ---------------------------------------------------------------------------
# Stage 6 — null out batch_id on documents that moved away from batch destination
# ---------------------------------------------------------------------------


async def reconcile_stale_batch_refs(
    result: ReconcileResult, ctx: PipelineContext
) -> Result[ReconcileResult]:
    """Stage 6: null out batch_id on documents that moved away from their batch destination.

    For each documents row with a non-NULL batch_id, computes the expected
    vault_path prefix from batches.destination_type + destination_name.
    If the row's vault_path no longer starts with that prefix, sets batch_id = NULL.

    Safe to run before Phase 4 is deployed: if the batches table does not exist,
    returns Success(result) unchanged.
    """
    import sqlite3

    from storage.db import get_connection

    counter = 0
    try:
        with get_connection(ctx.db_path) as conn:
            rows = conn.execute(
                """
                SELECT d.vault_path, b.destination_type, b.destination_name
                FROM documents d
                JOIN batches b ON d.batch_id = b.batch_id
                WHERE d.batch_id IS NOT NULL
                """,
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return Success(result)
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"stage": "reconcile_stale_batch_refs"},
        )

    vault_cfg = ctx.config.vault
    stale_paths = []
    for vault_path, destination_type, destination_name in rows:
        # Derive the prefix from config (not hardcoded "Projects/"/"Domain/"),
        # so non-default projects_dir/domain_dir deployments still match. Keep
        # the trailing slash so Projects/Alpha/ does not match Projects/AlphaBeta/.
        if destination_type == "project":
            top_dir = vault_cfg.projects_dir
        else:
            top_dir = vault_cfg.domain_dir
        expected_prefix = f"{top_dir}/{destination_name}/"

        if not vault_path.startswith(expected_prefix):
            stale_paths.append(vault_path)

    if stale_paths:
        try:
            with get_connection(ctx.db_path) as conn:
                for vault_path in stale_paths:
                    conn.execute(
                        "UPDATE documents SET batch_id = NULL WHERE vault_path = ?",
                        (vault_path,),
                    )
                    counter += 1
        except sqlite3.OperationalError as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"stage": "reconcile_stale_batch_refs"},
            )

    return Success(replace(result, batch_refs_cleared=result.batch_refs_cleared + counter))


# ---------------------------------------------------------------------------
# Stage 7 — migrate binaries whose location doesn't match resolve_placement
# ---------------------------------------------------------------------------


async def reconcile_editable_migration(
    result: ReconcileResult, ctx: PipelineContext
) -> Result[ReconcileResult]:
    """Migrate binaries + siblings when ``no_edit_extensions`` config changes.

    Walks all managed summaries areas looking for ``type=attachment-summary``
    sibling ``.md`` files.  For each one whose binary still exists, calls
    ``resolve_placement`` to determine where the binary *should* live given
    the current config.  If the binary's actual parent differs from the
    expected ``final_dir``, the binary and its sibling are migrated.

    This handles both directions:
    - Editable → No-edit (e.g. ``.xlsx`` added to ``no_edit_extensions``):
      binary moves to ``attachment/``, sibling moves to ``attachment/.summaries/``
    - No-edit → Editable (e.g. ``.xlsx`` removed from ``no_edit_extensions``):
      binary moves to project/domain root, sibling moves to root ``.summaries/``

    Skips entries with ``updated_by_human=True`` (human-lock guard).
    """
    from vault.reader import read_note
    from vault.writer import move_attachment, move_note, write_note
    import storage.documents as documents

    vault_cfg = ctx.config.vault
    db_path = ctx.db_path
    migrations = 0

    for summaries_dir in vault_cfg.root.rglob(vault_cfg.summaries_subdir):
        if not summaries_dir.is_dir():
            continue
        # Scope guard: only walk .summaries/ inside managed areas (now includes
        # editable-file areas via the extended _is_managed_summaries_area).
        if not _is_managed_summaries_area(summaries_dir, vault_cfg):
            continue

        for entry in summaries_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.suffix.lower() == ".md":
                continue
            if entry.name.startswith("."):
                continue

            match read_note(entry):
                case Failure():
                    continue
                case Success(note):
                    pass

            # Must be an AI-written attachment summary
            if note.metadata.type != "attachment-summary":
                continue

            # Human-lock guard
            if note.metadata.updated_by_human:
                continue

            attachment_vp = note.metadata.attachment_path
            if attachment_vp is None:
                continue

            binary = vault_cfg.root / attachment_vp
            if not binary.is_file():
                continue  # Binary gone — Stage 4 handles orphan cleanup

            # Determine where the binary SHOULD live per current config
            loc_type, loc_name = _location_context(binary, vault_cfg)
            if loc_type is None:
                continue  # Can't determine — skip

            placement = resolve_placement(binary, loc_type, loc_name, vault_cfg)
            if not placement.needs_move:
                continue  # Binary already in the right place

            # Compute destination paths
            dst_binary = placement.final_dir / binary.name
            dst_sibling = placement.sibling_dir / f"{binary.name}.md"

            # Collision avoidance
            counter = 0
            while dst_binary.exists() and counter < 99:
                counter += 1
                dst_binary = placement.final_dir / f"{binary.stem}-{counter}{binary.suffix}"
                dst_sibling = placement.sibling_dir / f"{dst_binary.name}.md"
            if dst_binary.exists():
                _log.warning(
                    "reconcile.editable_migration_collision binary=%s",
                    binary,
                )
                continue

            # Ensure destination directories exist
            placement.final_dir.mkdir(parents=True, exist_ok=True)
            placement.sibling_dir.mkdir(parents=True, exist_ok=True)

            # Step 1: move binary
            match move_attachment(binary, dst_binary):
                case Failure(error=e):
                    _log.warning(
                        "reconcile.editable_migration_binary_move_failed src=%s dst=%s error=%s",
                        binary, dst_binary, e,
                    )
                    continue
                case Success():
                    pass

            # Step 2: move or rebuild sibling
            old_sibling_vp = str(
                entry.relative_to(vault_cfg.root).as_posix()
            )
            if entry.exists():
                match move_note(entry, dst_sibling, actor="ai"):
                    case Failure(error=e):
                        _log.warning(
                            "reconcile.editable_migration_sibling_move_failed src=%s dst=%s error=%s",
                            entry, dst_sibling, e,
                        )
                        # Binary already moved — log and continue with DB update
                    case Success():
                        pass
                # Update attachment_path in moved sibling
                match read_note(dst_sibling):
                    case Success(value=moved_note):
                        new_attachment_vp = str(
                            dst_binary.relative_to(vault_cfg.root).as_posix()
                        )
                        moved_note.metadata.attachment_path = new_attachment_vp
                        match write_note(
                            dst_sibling, moved_note.content,
                            moved_note.metadata, actor="ai",
                        ):
                            case Failure(error=e):
                                _log.warning(
                                    "reconcile.editable_migration_pointer_update_failed sibling=%s error=%s",
                                    dst_sibling, e,
                                )
                            case Success():
                                pass
                    case Failure(error=e):
                        _log.warning(
                            "reconcile.editable_migration_read_moved_sibling_failed sibling=%s error=%s",
                            dst_sibling, e,
                        )
            else:
                # Sibling absent on disk — rebuild from existing note metadata
                new_attachment_vp = str(
                    dst_binary.relative_to(vault_cfg.root).as_posix()
                )
                note.metadata.attachment_path = new_attachment_vp
                match write_note(
                    dst_sibling, note.content, note.metadata, actor="ai",
                ):
                    case Failure(error=e):
                        _log.warning(
                            "reconcile.editable_migration_rebuild_failed sibling=%s error=%s",
                            dst_sibling, e,
                        )
                        continue
                    case Success():
                        pass

            # Step 3: update DB row (rename old sibling VP → new sibling VP)
            new_sibling_vp = str(
                dst_sibling.relative_to(vault_cfg.root).as_posix()
            )
            match documents.rename(old_sibling_vp, new_sibling_vp, db_path=db_path):
                case Success(value=0):
                    _log.warning(
                        "reconcile.editable_migration_db_row_not_found old=%s new=%s",
                        old_sibling_vp, new_sibling_vp,
                    )
                case Failure(error=e):
                    _log.warning(
                        "reconcile.editable_migration_rename_failed old=%s error=%s",
                        old_sibling_vp, e,
                    )
                case Success():
                    pass

            # Step 4: audit
            is_no_edit = dst_binary.suffix.lower() in vault_cfg.no_edit_extensions
            direction = "→attachment" if is_no_edit else "→root"
            match audit_write(
                AIDecision(
                    action="reconcile:editable_migration",
                    confidence=1.0,
                    reasoning=(
                        f"Config-driven migration: {binary.name} {direction} "
                        f"({binary.parent.name} → {placement.final_dir.name})"
                    ),
                    source_ids=[new_sibling_vp],
                ),
                pipeline="reconcile",
                stage="reconcile_editable_migration",
                outcome="EDITABLE_MIGRATED",
                db_path=db_path,
            ):
                case Failure(error=e):
                    _log.warning(
                        "reconcile.editable_migration_audit_failed error=%s", e
                    )
                case Success():
                    pass

            migrations += 1

    return Success(result.replace(editable_migrations=migrations))


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------


async def reconcile(ctx: PipelineContext) -> Result[ReconcileResult]:
    """Run all 6 reconcile stages in sequence.

    Args:
        ctx: PipelineContext with config, correlation_id, and db_path.

    Returns:
        Success(ReconcileResult) with per-stage counts, or Failure.
    """
    from vault.indexer import scan_vault

    match scan_vault(ctx.config.vault.root):
        case Failure() as f:
            return f
        case Success(entries):
            pass

    result = ReconcileResult()
    match await reconcile_paths(result, ctx, entries):
        case Failure() as f:
            return f
        case Success(value=r):
            result = r
    match await reconcile_orphan_binaries(result, ctx):
        case Failure() as f:
            return f
        case Success(value=r):
            result = r
    match await reconcile_stale_binaries(result, ctx):
        case Failure() as f:
            return f
        case Success(value=r):
            result = r
    match await reconcile_orphan_siblings(result, ctx):
        case Failure() as f:
            return f
        case Success(value=r):
            result = r
    match await reconcile_stale_tags(result, ctx, entries):
        case Failure() as f:
            return f
        case Success(value=r):
            result = r
    match await reconcile_stale_batch_refs(result, ctx):
        case Failure() as f:
            return f
        case Success(value=r):
            result = r
    match await reconcile_editable_migration(result, ctx):
        case Failure() as f:
            return f
        case Success(value=r):
            result = r
    return Success(result)
