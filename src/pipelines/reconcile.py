"""pipelines/reconcile.py

5-stage reconcile pipeline: paths → orphan_binaries → stale_binaries → orphan_siblings → stale_tags.
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
from vault.paths import _is_in_managed_attachment, _is_managed_summaries_area

_log = logging.getLogger(__name__)

__all__ = [
    "ReconcileResult",
    "reconcile_paths",
    "reconcile_orphan_binaries",
    "reconcile_stale_binaries",
    "reconcile_orphan_siblings",
    "reconcile_stale_tags",
    "reconcile",
]


@dataclass(frozen=True)
class ReconcileResult:
    """Counters accumulated across the 5 reconcile stages."""

    paths_reconciled: int = 0
    new_captures: int = 0
    restale_count: int = 0
    orphans_cleaned: int = 0
    tags_updated: int = 0

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
    - Skips notes with updated_by_human=True (write_note returns Failure recoverable=True).

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
            case Failure(recoverable=False) as f if "human" in str(f.error).lower():
                # updated_by_human=True — write_note returns recoverable=False with
                # "note locked by human edit". Skip silently; no retry will fix this.
                pass
            case Failure() as f:
                _log.warning("reconcile_stale_tags.write_failed path=%s error=%s",
                             entry.vault_path, f.error)

    return Success(result.replace(tags_updated=result.tags_updated + tags_updated))


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------


async def reconcile(ctx: PipelineContext) -> Result[ReconcileResult]:
    """Run all 5 reconcile stages in sequence.

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
    return Success(result)
