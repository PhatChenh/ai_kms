"""
vault/paths.py

Parametrized vault path helpers.

These functions return Path objects for named locations inside the vault.
Each call ensures the TARGET DIRECTORY exists (mkdir parents + exist_ok).
Functions do NOT write files.

Static folder roots are on VaultConfig — use CONFIG.main.vault.inbox_path,
.attachment_path, etc. directly. Functions here cover only the parametrized
sub-paths that require a name or date argument.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path


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
