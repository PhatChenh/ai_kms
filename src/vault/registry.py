"""
vault/registry.py

Project Registry — maps Projects/ folders to Domain/ groups by reading each
project's CLAUDE.md tags.  Phase 2 Classify, Search, and Daily Briefing query
the registry to group content by domain.

Pure in-memory — no database writes, no SQL migrations.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from core.result import Failure, Result, Success
from vault.paths import load_valid_domains
from vault.reader import read_note

if TYPE_CHECKING:
    from core.config import VaultConfig

#: Folder names skipped when scanning Projects/ for project directories.
_SKIP_PROJECT_NAMES: frozenset[str] = frozenset({"attachment", ".summaries"})

_log = logging.getLogger(__name__)


@dataclass
class ProjectEntry:
    """A single project discovered in Projects/."""

    name: str
    path: Path
    domain_unknown: bool = False


@dataclass
class ProjectGroup:
    """All projects that share a domain, plus the domain folder itself."""

    domain_name: str
    domain_path: Path | None = None
    projects: list[ProjectEntry] = field(default_factory=list)


@dataclass
class ProjectRegistry:
    """Complete project→domain mapping built at startup and kept live."""

    groups: dict[str, ProjectGroup] = field(default_factory=dict)

    @property
    def all_project_names(self) -> frozenset[str]:
        return frozenset(
            entry.name for group in self.groups.values() for entry in group.projects
        )


def build_registry(vault_cfg) -> Result[ProjectRegistry]:
    """Scan Projects/ and Domain/ to build the initial project→domain mapping.

    Args:
        vault_cfg: VaultConfig with ``root``, ``projects_path``, and
                   ``domain_path`` set.

    Returns:
        Success(ProjectRegistry) or Failure if Projects/ is missing.
    """
    projects_path = vault_cfg.projects_path
    domain_path = vault_cfg.domain_path

    if not projects_path.is_dir():
        return Failure(
            "Projects directory does not exist",
            recoverable=False,
            context={"projects_path": str(projects_path)},
        )

    valid_domains = load_valid_domains(vault_cfg.root)

    # Seed groups for every valid domain folder (even empty ones).
    groups: dict[str, ProjectGroup] = {}
    for domain_name in sorted(valid_domains):
        groups[domain_name] = ProjectGroup(
            domain_name=domain_name,
            domain_path=domain_path / domain_name,
        )

    uncategorized: list[ProjectEntry] = []

    for entry in sorted(projects_path.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name in _SKIP_PROJECT_NAMES:
            continue

        project_name = entry.name
        claude_path = entry / "CLAUDE.md"

        match read_note(claude_path):
            case Success(note):
                # Scan tags for first "domain/X" where X is a valid domain.
                assigned = False
                for tag in note.metadata.tags:
                    if tag.startswith("domain/"):
                        domain_name = tag[len("domain/") :]
                        if domain_name in valid_domains:
                            groups[domain_name].projects.append(
                                ProjectEntry(
                                    name=project_name,
                                    path=entry,
                                )
                            )
                            assigned = True
                            break
                if not assigned:
                    uncategorized.append(
                        ProjectEntry(
                            name=project_name,
                            path=entry,
                            domain_unknown=True,
                        )
                    )
            case Failure():
                uncategorized.append(
                    ProjectEntry(
                        name=project_name,
                        path=entry,
                        domain_unknown=True,
                    )
                )

    # Sort projects within each group alphabetically.
    for group in groups.values():
        group.projects.sort(key=lambda e: e.name)

    uncategorized.sort(key=lambda e: e.name)

    groups["Uncategorized"] = ProjectGroup(
        domain_name="Uncategorized",
        projects=uncategorized,
    )

    return Success(ProjectRegistry(groups=groups))


def format_for_prompt(registry: ProjectRegistry) -> str:
    """Serialize the registry as a readable prompt text block.

    Domain groups appear in alphabetical order.  Uncategorized is always last.
    Projects within each group are alphabetically sorted.

    Args:
        registry: The ``ProjectRegistry`` to format.

    Returns:
        A human-readable string suitable for inclusion in an LLM prompt.
    """
    lines: list[str] = []
    group_names = sorted(name for name in registry.groups if name != "Uncategorized")
    # Uncategorized always last.
    if "Uncategorized" in registry.groups:
        group_names.append("Uncategorized")

    for name in group_names:
        group = registry.groups[name]
        lines.append(f"{name}:")
        if group.projects:
            for entry in group.projects:
                lines.append(f"  - {entry.name}")
        else:
            lines.append("  - No active projects")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper — domain resolution from CLAUDE.md tags
# ---------------------------------------------------------------------------


def _resolve_domain(
    project_path: Path,
    valid_domains: frozenset[str],
) -> str | None:
    """Read ``CLAUDE.md`` in *project_path* and return the first valid domain.

    Returns the domain name (str) if a matching ``domain/X`` tag is found and
    X is in *valid_domains*.  Returns ``None`` otherwise (missing CLAUDE.md,
    no domain tag, stale tag, read error).
    """
    claude_path = project_path / "CLAUDE.md"
    match read_note(claude_path):
        case Success(note):
            for tag in note.metadata.tags:
                if tag.startswith("domain/"):
                    domain_name = tag[len("domain/") :]
                    if domain_name in valid_domains:
                        return domain_name
            return None
        case Failure():
            return None


# ---------------------------------------------------------------------------
# LiveRegistry — thread-safe live-update wrapper
# ---------------------------------------------------------------------------


class LiveRegistry:
    """Thread-safe registry that mutates in response to watcher events.

    Wraps the startup ``build_registry()`` snapshot and exposes five mutation
    methods for the watcher to call when project folders / CLAUDE.md files
    change.  All mutations are guarded by a ``threading.Lock`` — same pattern
    as the three locks already in ``VaultWatcher``.

    Args:
        vault_cfg: VaultConfig with ``root``, ``projects_path``, and
                   ``domain_path`` set.
    """

    def __init__(self, vault_cfg: VaultConfig) -> None:
        self._vault_cfg = vault_cfg
        self._lock = threading.Lock()

        match build_registry(vault_cfg):
            case Success(reg):
                self._registry = reg
            case Failure() as f:
                _log.warning(
                    "LiveRegistry: build_registry failed — starting empty. %s",
                    f.error,
                )
                self._registry = ProjectRegistry()

    # ── public mutation API ──────────────────────────────────────────────

    def add_project(self, name: str) -> None:
        """Add a project that was just created under ``Projects/<name>/``."""
        with self._lock:
            self._add_project_locked(name)

    def remove_project(self, name: str) -> None:
        """Remove a project that was deleted from ``Projects/<name>/``."""
        with self._lock:
            self._remove_project_locked(name)

    def rename_project(self, old: str, new: str) -> None:
        """Handle a folder rename inside ``Projects/``."""
        with self._lock:
            self._rename_project_locked(old, new)

    def refresh_domain(self, project_name: str) -> None:
        """Re-read ``CLAUDE.md`` and move the project if its domain changed."""
        with self._lock:
            self._refresh_domain_locked(project_name)

    def invalidate_domain(self, domain_name: str) -> None:
        """Move all projects in *domain_name* group to Uncategorized."""
        with self._lock:
            self._invalidate_domain_locked(domain_name)

    def get_groups(self) -> dict[str, ProjectGroup]:
        """Return a shallow copy of the current groups dict (thread-safe)."""
        with self._lock:
            return dict(self._registry.groups)

    # ── internal (callers must hold self._lock) ──────────────────────────

    def _valid_domains(self) -> frozenset[str]:
        return load_valid_domains(self._vault_cfg.root)

    def _ensure_group(self, domain_name: str) -> ProjectGroup:
        groups = self._registry.groups
        if domain_name not in groups:
            groups[domain_name] = ProjectGroup(
                domain_name=domain_name,
                domain_path=self._vault_cfg.domain_path / domain_name,
            )
        return groups[domain_name]

    def _add_project_locked(self, name: str) -> None:
        project_path = self._vault_cfg.projects_path / name
        valid_domains = self._valid_domains()
        domain = _resolve_domain(project_path, valid_domains)

        entry = ProjectEntry(
            name=name,
            path=project_path,
            domain_unknown=domain is None,
        )

        if domain is not None:
            group = self._ensure_group(domain)
            group.projects.append(entry)
            group.projects.sort(key=lambda e: e.name)
        else:
            uncat = self._ensure_group("Uncategorized")
            uncat.projects.append(entry)
            uncat.projects.sort(key=lambda e: e.name)

    def _remove_project_locked(self, name: str) -> None:
        for group in self._registry.groups.values():
            group.projects = [e for e in group.projects if e.name != name]

    def _rename_project_locked(self, old: str, new: str) -> None:
        for group in self._registry.groups.values():
            for entry in group.projects:
                if entry.name == old:
                    entry.name = new
                    return

    def _refresh_domain_locked(self, project_name: str) -> None:
        project_path = self._vault_cfg.projects_path / project_name
        valid_domains = self._valid_domains()
        new_domain = _resolve_domain(project_path, valid_domains)

        # Find the existing entry and its current group.
        old_group: ProjectGroup | None = None
        entry: ProjectEntry | None = None
        for group in self._registry.groups.values():
            for e in group.projects:
                if e.name == project_name:
                    old_group = group
                    entry = e
                    break
            if entry is not None:
                break

        if entry is None:
            return  # Not tracked — nothing to refresh

        # If domain is unchanged, do nothing (but mark domain_unknown correctly).
        old_domain = old_group.domain_name if old_group is not None else None
        if new_domain == old_domain:
            entry.domain_unknown = False
            return

        # Remove from old group.
        old_group.projects = [e for e in old_group.projects if e.name != project_name]

        # Add to new group (or Uncategorized if None).
        entry.domain_unknown = new_domain is None
        if new_domain is not None:
            target = self._ensure_group(new_domain)
        else:
            target = self._ensure_group("Uncategorized")
        target.projects.append(entry)
        target.projects.sort(key=lambda e: e.name)

        # If old group is now empty AND it was a domain group (not Uncategorized),
        # keep it (domains exist even with zero projects).

    def _invalidate_domain_locked(self, domain_name: str) -> None:
        if domain_name == "Uncategorized":
            return
        group = self._registry.groups.pop(domain_name, None)
        if group is None or not group.projects:
            return
        uncat = self._ensure_group("Uncategorized")
        for entry in group.projects:
            entry.domain_unknown = True
            uncat.projects.append(entry)
        uncat.projects.sort(key=lambda e: e.name)
