"""
vault/registry.py

Project Registry — maps Projects/ folders to Domain/ groups by reading each
project's CLAUDE.md tags.  Phase 2 Classify, Search, and Daily Briefing query
the registry to group content by domain.

Pure in-memory — no database writes, no SQL migrations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.result import Failure, Result, Success
from vault.paths import load_valid_domains
from vault.reader import read_note

#: Folder names skipped when scanning Projects/ for project directories.
_SKIP_PROJECT_NAMES: frozenset[str] = frozenset({"attachment", ".summaries"})


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
