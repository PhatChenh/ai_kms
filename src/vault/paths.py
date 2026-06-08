"""
vault/paths.py

Parametrized vault path helpers.

These functions return Path objects for named locations inside the vault.
Each call ensures the TARGET DIRECTORY exists (mkdir parents + exist_ok).
Functions do NOT write files.

Static folder roots are on VaultConfig — use CONFIG.main.vault.inbox_path,
.projects_path, etc. directly. Functions here cover only the parametrized
sub-paths that require a name or date argument.
"""

from __future__ import annotations

import dataclasses
import unicodedata
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import VaultConfig


# ---------------------------------------------------------------------------
# Placement — frozen result of resolve_placement
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Placement:
    """Where a captured binary should live.

    Returned by ``resolve_placement`` — the single authoritative answer to
    "where does this file belong?".  All later consumers (capture pipeline,
    watcher re-home, Phase 2 Classify) call the same function so the
    editable / no-edit routing rule lives in exactly one place.

    Attributes:
        final_dir:   Directory the binary should land in.
        sibling_dir: Directory for the AI-written ``.summaries/`` summary
                     (always *final_dir* / ``vault_cfg.summaries_subdir``).
        needs_move:  True when *final_dir* differs from the file's current
                     parent — the caller must move the binary.
    """

    final_dir: Path
    sibling_dir: Path
    needs_move: bool


# ---------------------------------------------------------------------------
# resolve_placement — pure path arithmetic
# ---------------------------------------------------------------------------


def resolve_placement(
    file_path: Path,
    target_type: str,
    target_name: str,
    vault_cfg: VaultConfig,
) -> Placement:
    """Return the Placement for *file_path* given its resolved home.

    Pure path arithmetic — no filesystem calls, no CONFIG import, no side
    effects.  The editable / no-edit rule is *only* encoded here; every
    consumer calls this one function.

    Args:
        file_path:   Absolute path to the binary file.
        target_type: ``"project"`` or ``"domain"``.
        target_name: Project or domain folder name (e.g. ``"Alpha"``).
        vault_cfg:   VaultConfig with layout fields.

    Returns:
        ``Placement`` with *final_dir*, *sibling_dir*, and *needs_move*.
    """
    # 1. Determine the base directory for this project/domain.
    if target_type == "project":
        base_dir = vault_cfg.projects_path / target_name
    else:
        base_dir = vault_cfg.domain_path / target_name

    # 2. Is this a no-edit file?
    is_no_edit = file_path.suffix.lower() in vault_cfg.no_edit_extensions

    # 3. Final directory: attachment/ for no-edit files, root for editable.
    final_dir = base_dir / vault_cfg.attachment_dir if is_no_edit else base_dir

    # 4. Sibling directory always follows the binary's final parent.
    sibling_dir = final_dir / vault_cfg.summaries_subdir

    # 5. Does the file need to move?
    needs_move = file_path.parent != final_dir

    return Placement(
        final_dir=final_dir,
        sibling_dir=sibling_dir,
        needs_move=needs_move,
    )


def _is_in_managed_attachment(file_path: Path, vault_cfg: VaultConfig) -> bool:
    """Return True if file_path lives inside a per-project or per-domain attachment/ subtree.

    A managed attachment subtree is any directory named `vault_cfg.attachment_dir`
    whose grandparent is `vault_cfg.projects_path` or `vault_cfg.domain_path`
    (i.e. Projects/<A>/attachment/ or Domain/<D>/attachment/).

    Used by watcher `_should_skip` and indexer Rule 1 to identify files that
    are pipeline artifacts (already captured, not drop targets). Also used by
    reconcile Stages 2 + 3 to scope binary scans.

    Args:
        file_path: Absolute path to the file being tested.
        vault_cfg: VaultConfig with projects_path, domain_path, attachment_dir.

    Returns:
        True if file_path is inside a managed attachment subtree.
    """
    attachment_dir = vault_cfg.attachment_dir
    projects_path = vault_cfg.projects_path
    domain_path = vault_cfg.domain_path

    for parent in file_path.parents:
        if parent.name == attachment_dir:
            top = parent.parent.parent
            if top == projects_path or top == domain_path:
                return True
    return False


def _is_managed_summaries_area(path: Path, vault_cfg: VaultConfig) -> bool:
    """Return True if path lives inside an area where AI-managed ``.summaries/`` siblings exist.

    Managed summaries areas (where the capture pipeline writes sibling ``.md``
    files for binaries, per DECISION-021 + DECISION-027):

      - ``Projects/<A>/<attachment_dir>/`` and its ``.summaries/`` subdir
        (LOCATED captures — rich sibling next to project binary)
      - ``Domain/<D>/<attachment_dir>/`` and its ``.summaries/`` subdir
        (LOCATED captures — rich sibling next to domain binary)
      - ``Projects/<A>/.summaries/`` (editable-file siblings — Phase 3/4
        editable→root routing; e.g. ``.docx`` binary at project root)
      - ``Domain/<D>/.summaries/`` (same for domain editable binaries)
      - ``<inbox_dir>/`` and its ``.summaries/`` subdir
        (CLUELESS pending-routing markers — Phase 2 Classify resolves them)

    Differs from ``_is_in_managed_attachment``: that one is the *binary* pipeline
    area (used to suppress double-capture). This one is the *sibling* hosting
    area (used by reconcile Stage 4 to scope ``.summaries/`` walks safely, and
    Stage 7 editable-migration).

    Args:
        path: Absolute path to a file or directory being tested.
        vault_cfg: VaultConfig with projects_path, domain_path, attachment_dir,
                   inbox_path, summaries_subdir.

    Returns:
        True if path is inside any managed summaries area (or IS the area itself).
    """
    inbox_path = vault_cfg.inbox_path
    if path == inbox_path or inbox_path in path.parents:
        return True
    if _is_in_managed_attachment(path, vault_cfg):
        return True
    # Editable-file summaries: Projects/<A>/.summaries/ and Domain/<D>/.summaries/.
    # These house siblings for binaries that resolve_placement routes to the
    # project/domain root (editable extension → final_dir = base_dir).
    projects_path = vault_cfg.projects_path
    domain_path = vault_cfg.domain_path
    summaries = vault_cfg.summaries_subdir
    # Check if *path itself* is a managed .summaries/ directory.
    if path.name == summaries:
        grandparent = path.parent
        if grandparent.parent == projects_path or grandparent.parent == domain_path:
            return True
    for parent in path.parents:
        if parent.name == summaries:
            grandparent = parent.parent
            if grandparent.parent == projects_path or grandparent.parent == domain_path:
                return True
    return False


def _is_ai_output(path: Path, vault_cfg: "VaultConfig") -> bool:
    """Return True if any part of *path* matches a folder the system writes to itself.

    The system writes to ``Briefings/``, ``Synthesis/``, and ``Documentation/``.
    This predicate prevents the capture pipeline from re-ingesting its own output
    and creating an infinite feedback loop.

    Name-matching is depth-agnostic — the folder name can appear at any position
    in ``path.parts``.  Pure path arithmetic: no filesystem I/O, no CONFIG import.

    Args:
        path:      Absolute path to the file being tested.
        vault_cfg: VaultConfig with ai_output_dirs.

    Returns:
        True if any part of *path* appears in ``vault_cfg.ai_output_dirs``.
    """
    ai_dirs = vault_cfg.ai_output_dirs
    for part in path.parts:
        if part in ai_dirs:
            return True
    return False


def _is_misplaced(path: Path, vault_cfg: "VaultConfig") -> bool:
    """Return True if *path* is a file dropped at the bare root of Projects/ or Domain/.

    The predicate is intentionally type-agnostic — callers add their own
    extension filter (e.g. ``.md``-only in the watcher).  A file is misplaced
    when it sits directly under ``Projects/`` or ``Domain/`` without a real
    subfolder (e.g. ``Projects/stray.md`` or ``Domain/loose.xlsx``).  Such
    files have no project or domain context — they are orphan drops that
    should be swept to inbox.

    Inbox and AI-output folders are always valid (they have their own handlers).
    Files nested inside a subfolder (e.g. ``Projects/Alpha/note.md``) are NOT
    misplaced — they have a valid project/domain container.

    Uses ``len(rel.parts) >= 2`` directly rather than calling ``_location_context``,
    which would treat ``Projects/<file>.md`` as ``("project", "<file>")`` and create
    a phantom project.

    Pure path arithmetic — no filesystem I/O, no CONFIG import.

    Args:
        path:      Absolute path to the file being tested.
        vault_cfg: VaultConfig with projects_path, domain_path, inbox_path.

    Returns:
        True if *path* is at the bare root of Projects/ or Domain/.
    """
    inbox_path = vault_cfg.inbox_path

    # Inbox is always valid.
    if path.parent == inbox_path or inbox_path in path.parents:
        return False

    # AI-output folders are always valid.
    if _is_ai_output(path, vault_cfg):
        return False

    # Check Projects/ bare root: file is directly under Projects/, not nested.
    projects_path = vault_cfg.projects_path
    if projects_path in path.parents:
        rel = path.relative_to(projects_path)
        if len(rel.parts) < 2:
            return True

    # Check Domain/ bare root: file is directly under Domain/, not nested.
    domain_path = vault_cfg.domain_path
    if domain_path in path.parents:
        rel = path.relative_to(domain_path)
        if len(rel.parts) < 2:
            return True

    return False


def _location_context(
    path: Path, vault_cfg: "VaultConfig"
) -> tuple[str | None, str | None]:
    """Return the location context for a vault path.

    Inspects the path against vault_cfg layout to determine whether the file
    lives inside a known domain folder, project folder, or the inbox.

    Args:
        path:      Absolute path to the file.
        vault_cfg: VaultConfig with domain_path, projects_path, inbox_path.

    Returns:
        ("domain", "<D>")   — path is under Domain/<D>/
        ("project", "<A>")  — path is under Projects/<A>/
        ("inbox", None)     — path is under inbox/
        (None, None)        — path does not match any known location
    """
    domain_path = vault_cfg.domain_path
    projects_path = vault_cfg.projects_path
    inbox_path = vault_cfg.inbox_path

    # Check domain first: path must have domain_path as a parent,
    # and the immediate child of domain_path is the domain name.
    if domain_path in path.parents:
        # path relative to domain_path gives ("<D>", ...)
        rel = path.relative_to(domain_path)
        if rel.parts:
            return ("domain", rel.parts[0])

    # Check projects: same pattern
    if projects_path in path.parents:
        rel = path.relative_to(projects_path)
        if rel.parts:
            return ("project", rel.parts[0])

    # Inbox fallback
    if inbox_path == path.parent or inbox_path in path.parents:
        return ("inbox", None)

    return (None, None)


#: Folder names that are system-managed and should never be treated as
#: batch-worthy subfolders, regardless of their position in the vault.
_BATCH_SUBFOLDER_BLOCKLIST: frozenset[str] = frozenset(
    {"attachment", ".summaries", "Archive"}
)


def is_batch_subfolder(path: Path, vault_cfg: "VaultConfig") -> bool:
    """Return True if *path* is a named subfolder that warrants a batch record.

    A batch-worthy subfolder is any named directory inside inbox/, Projects/<A>/,
    or Domain/<D>/ that is NOT:
      - the root of those trees (must be at least one level deeper), OR
      - a system-managed folder: attachment/, .summaries/, or Archive/.

    Both capture_file() and _handle_binary_move() call this function so the
    batch-worthiness rule is defined in exactly one place.

    Pure path arithmetic — no filesystem I/O, no CONFIG import, no side effects.

    Args:
        path:      Absolute path to the directory being tested.
        vault_cfg: VaultConfig with projects_path, domain_path, inbox_path.

    Returns:
        True if path should receive a batch record; False otherwise.
    """
    # Blocklist: system-managed folder names are never batch-worthy.
    if path.name in _BATCH_SUBFOLDER_BLOCKLIST:
        return False

    loc_type, _ = _location_context(path, vault_cfg)

    if loc_type == "inbox":
        # Any subfolder of inbox/ qualifies — depth >= 1 is guaranteed because
        # _location_context returns ("inbox", None) for inbox/ itself too, so we
        # must check that path is not inbox/ root.
        return path != vault_cfg.inbox_path

    if loc_type in ("project", "domain"):
        # Must be at least two parts deep relative to the tree root
        # (i.e., Projects/<A>/subdir, not just Projects/<A>).
        root = (
            vault_cfg.projects_path if loc_type == "project" else vault_cfg.domain_path
        )
        try:
            rel = path.relative_to(root)
            return len(rel.parts) >= 2
        except ValueError:
            return False

    return False


def load_valid_domains(vault_root: Path) -> frozenset[str]:
    """Return folder names directly under vault_root/Domain/ as the valid domain set.

    Args:
        vault_root: Absolute path to the vault root directory.

    Returns:
        Frozenset of domain folder names. Empty frozenset if Domain/ does not exist.
        Hidden folders (dotfiles) are excluded.
    """
    domain_dir = vault_root / "Domain"
    if not domain_dir.is_dir():
        return frozenset()
    return frozenset(
        p.name
        for p in domain_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def to_vault_path(absolute: Path) -> str:
    """
    Convert an absolute vault file path to an NFC-normalised POSIX vault-relative string.

    Args:
        absolute: Absolute path to a file inside the vault root.

    Returns:
        POSIX-style path relative to the vault root, NFC-normalised for consistent
        SQLite storage on macOS (which uses NFD internally for filenames).
    """
    from core.config import CONFIG

    rel = absolute.relative_to(CONFIG.main.vault.root).as_posix()
    return unicodedata.normalize("NFC", rel)


def project_dir(name: str) -> Path:
    """Return Projects/<name>/ and ensure it exists."""
    from core.config import CONFIG

    d = CONFIG.main.vault.projects_path / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_materials(name: str) -> Path:
    """Return Projects/<name>/materials/ and ensure it exists."""
    from core.config import CONFIG

    d = CONFIG.main.vault.projects_path / name / "materials"
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_index(name: str) -> Path:
    """Return Projects/<name>/CLAUDE.md path; ensure parent dir exists. Does not create the file."""
    return project_dir(name) / "CLAUDE.md"


def domain_dir(name: str) -> Path:
    """Return Domain/<name>/ and ensure it exists."""
    from core.config import CONFIG

    d = CONFIG.main.vault.domain_path / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def domain_notes(name: str) -> Path:
    """Return Domain/<name>/notes/ and ensure it exists."""
    from core.config import CONFIG

    d = CONFIG.main.vault.domain_path / name / "notes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def domain_index(name: str) -> Path:
    """Return Domain/<name>/CLAUDE.md path; ensure parent dir exists. Does not create the file."""
    return domain_dir(name) / "CLAUDE.md"


def project_attachment(name: str) -> Path:
    """Return Projects/<name>/<attachment_dir>/ and ensure it exists."""
    from core.config import CONFIG

    d = CONFIG.main.vault.projects_path / name / CONFIG.main.vault.attachment_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_summaries(name: str) -> Path:
    """Return Projects/<name>/<attachment_dir>/<summaries_subdir>/ and ensure it exists."""
    from core.config import CONFIG

    d = (
        CONFIG.main.vault.projects_path
        / name
        / CONFIG.main.vault.attachment_dir
        / CONFIG.main.vault.summaries_subdir
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


def domain_attachment(name: str) -> Path:
    """Return Domain/<name>/<attachment_dir>/ and ensure it exists."""
    from core.config import CONFIG

    d = CONFIG.main.vault.domain_path / name / CONFIG.main.vault.attachment_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def domain_summaries(name: str) -> Path:
    """Return Domain/<name>/<attachment_dir>/<summaries_subdir>/ and ensure it exists."""
    from core.config import CONFIG

    d = (
        CONFIG.main.vault.domain_path
        / name
        / CONFIG.main.vault.attachment_dir
        / CONFIG.main.vault.summaries_subdir
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


def domain_archive(name: str, vault_config: VaultConfig) -> Path:
    """Return Domain/<name>/<archive_dir>/ and ensure it exists.

    Args:
        name: Domain folder name.
        vault_config: VaultConfig with domain_path and archive_dir.

    Returns:
        Path to the domain's archive directory.
    """
    d = vault_config.domain_path / name / vault_config.archive_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def documentation(project: str) -> Path:
    """Return Documentation/<project>.md path; ensure parent dir exists. Does not create the file."""
    from core.config import CONFIG

    parent = CONFIG.main.vault.documentation_path
    parent.mkdir(parents=True, exist_ok=True)
    return parent / f"{project}.md"


def briefings_for(d: date) -> Path:
    """Return Briefings/<YYYY>/<MM>_<DD>.md for date d; ensure year dir exists."""
    from core.config import CONFIG

    year_dir = CONFIG.main.vault.briefings_path / str(d.year)
    year_dir.mkdir(parents=True, exist_ok=True)
    return year_dir / f"{d.month:02d}_{d.day:02d}.md"


def briefings_today() -> Path:
    """Return Briefings/<YYYY>/<MM>_<DD>.md for today; ensure year dir exists."""
    return briefings_for(date.today())


def synthesis_week(d: date) -> Path:
    """Return Synthesis/<YYYY>-W<WW>.md for the ISO week containing d; ensure Synthesis dir exists."""
    from core.config import CONFIG

    iso_year, iso_week, _ = d.isocalendar()
    parent = CONFIG.main.vault.synthesis_path
    parent.mkdir(parents=True, exist_ok=True)
    return parent / f"{iso_year}-W{iso_week:02d}.md"
