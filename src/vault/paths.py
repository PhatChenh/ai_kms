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

import unicodedata
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import VaultConfig


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
    """Return True if path lives inside an area where AI-managed `.summaries/` siblings exist.

    Managed summaries areas (where the capture pipeline writes sibling `.md`
    files for binaries, per DECISION-021 + DECISION-027):

      - `Projects/<A>/<attachment_dir>/` and its `.summaries/` subdir
        (LOCATED captures — rich sibling next to project binary)
      - `Domain/<D>/<attachment_dir>/` and its `.summaries/` subdir
        (LOCATED captures — rich sibling next to domain binary)
      - `<inbox_dir>/` and its `.summaries/` subdir
        (CLUELESS pending-routing markers — Phase 2 Classify resolves them)

    Differs from `_is_in_managed_attachment`: that one is the *binary* pipeline
    area (used to suppress double-capture). This one is the *sibling* hosting
    area (used by reconcile Stage 4 to scope `.summaries/` walks safely).

    Args:
        path: Absolute path to a file or directory being tested.
        vault_cfg: VaultConfig with projects_path, domain_path, attachment_dir,
                   inbox_path.

    Returns:
        True if path is inside any managed summaries area (or IS the area itself).
    """
    inbox_path = vault_cfg.inbox_path
    if path == inbox_path or inbox_path in path.parents:
        return True
    return _is_in_managed_attachment(path, vault_cfg)


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
        p.name for p in domain_dir.iterdir()
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
