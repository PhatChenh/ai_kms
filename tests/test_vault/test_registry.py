"""
tests/test_vault/test_registry.py

Tests for vault/registry.py — ProjectRegistry, build_registry, format_for_prompt.
"""

from __future__ import annotations

from pathlib import Path


from core.result import Failure, Success
from vault.frontmatter import NoteMetadata
from vault.registry import (
    LiveRegistry,
    ProjectEntry,
    ProjectGroup,
    ProjectRegistry,
    build_registry,
    format_for_prompt,
)
from vault.writer import write_note


def _write_claude_md(project_dir: Path, tags: list[str] | None = None) -> None:
    """Write a minimal CLAUDE.md with frontmatter tags for a project."""
    project_dir.mkdir(parents=True, exist_ok=True)
    metadata = NoteMetadata(tags=tags or [])
    write_note(
        project_dir / "CLAUDE.md",
        "# Project Index",
        metadata,
        actor="ai",
    )


class TestBuildRegistry:
    def test_p2_reg_01_projects_grouped_by_domain(self, vault_config):
        """Project with valid domain/ tag lands in that domain group."""
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        _write_claude_md(
            vault_config.root / "Projects" / "Alpha",
            tags=["domain/Finance"],
        )

        result = build_registry(vault_config)

        assert isinstance(result, Success)
        reg = result.value
        assert "Finance" in reg.groups
        group = reg.groups["Finance"]
        assert group.domain_path == vault_config.root / "Domain" / "Finance"
        names = [e.name for e in group.projects]
        assert "Alpha" in names
        # Domain path must exist on disk
        assert group.domain_path.is_dir()

    def test_p2_reg_02_stale_domain_tag_goes_to_uncategorized(self, vault_config):
        """Project with domain/ tag pointing to non-existent Domain/ folder → Uncategorized."""
        _write_claude_md(
            vault_config.root / "Projects" / "Beta",
            tags=["domain/OldDomain"],
        )
        # Do NOT create Domain/OldDomain/

        result = build_registry(vault_config)

        assert isinstance(result, Success)
        reg = result.value
        assert "OldDomain" not in reg.groups
        assert "Uncategorized" in reg.groups
        beta = next(
            (e for e in reg.groups["Uncategorized"].projects if e.name == "Beta"),
            None,
        )
        assert beta is not None
        assert beta.domain_unknown is True

    def test_p2_reg_03_no_claude_md_goes_to_uncategorized(self, vault_config):
        """Project folder without CLAUDE.md → Uncategorized."""
        (vault_config.root / "Projects" / "Gamma").mkdir(parents=True, exist_ok=True)
        # No CLAUDE.md written

        result = build_registry(vault_config)

        assert isinstance(result, Success)
        reg = result.value
        assert "Uncategorized" in reg.groups
        gamma = next(
            (e for e in reg.groups["Uncategorized"].projects if e.name == "Gamma"),
            None,
        )
        assert gamma is not None
        assert gamma.domain_unknown is True

    def test_p2_reg_04_first_domain_tag_wins(self, vault_config):
        """First matching domain/ tag is used; subsequent domain/ tags ignored."""
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        (vault_config.root / "Domain" / "Movies").mkdir(parents=True, exist_ok=True)
        _write_claude_md(
            vault_config.root / "Projects" / "Alpha",
            tags=["domain/Finance", "domain/Movies"],
        )

        result = build_registry(vault_config)

        assert isinstance(result, Success)
        reg = result.value
        assert "Finance" in reg.groups
        assert "Alpha" in [e.name for e in reg.groups["Finance"].projects]
        # Should NOT be in Movies
        movies_alpha = [e for e in reg.groups["Movies"].projects if e.name == "Alpha"]
        assert movies_alpha == []

    def test_domain_folders_appear_even_without_projects(self, vault_config):
        """Domain/<D>/ should appear as a ProjectGroup even with zero projects."""
        # vault_root fixture creates the Domain/ directory but no subdirectories.
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        (vault_config.root / "Domain" / "Health").mkdir(parents=True, exist_ok=True)

        result = build_registry(vault_config)

        assert isinstance(result, Success)
        reg = result.value
        assert "Finance" in reg.groups
        assert "Health" in reg.groups
        assert reg.groups["Finance"].projects == []
        assert reg.groups["Health"].projects == []

    def test_skips_dotfiles_and_attachment(self, vault_config):
        """Dotfiles, attachment/, and .summaries/ inside Projects/ are skipped."""
        (vault_config.root / "Projects" / ".DS_Store").mkdir(
            parents=True, exist_ok=True
        )
        (vault_config.root / "Projects" / "attachment").mkdir(
            parents=True, exist_ok=True
        )
        (vault_config.root / "Projects" / ".summaries").mkdir(
            parents=True, exist_ok=True
        )

        result = build_registry(vault_config)

        assert isinstance(result, Success)
        reg = result.value
        all_names = reg.all_project_names
        assert ".DS_Store" not in all_names
        assert "attachment" not in all_names
        assert ".summaries" not in all_names

    def test_non_directory_children_skipped(self, vault_config):
        """Files directly inside Projects/ are not treated as project folders."""
        (vault_config.root / "Projects" / "notes.txt").touch()

        result = build_registry(vault_config)

        assert isinstance(result, Success)
        reg = result.value
        assert "notes.txt" not in reg.all_project_names

    def test_returns_failure_when_projects_missing(self, vault_config):
        """If Projects/ doesn't exist, build_registry returns Failure."""
        import shutil

        shutil.rmtree(vault_config.root / "Projects")

        result = build_registry(vault_config)

        assert isinstance(result, Failure)

    def test_all_project_names_property(self, vault_config):
        """all_project_names returns frozenset of every project name."""
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        _write_claude_md(
            vault_config.root / "Projects" / "Alpha",
            tags=["domain/Finance"],
        )
        _write_claude_md(
            vault_config.root / "Projects" / "Beta",
            tags=[],
        )

        result = build_registry(vault_config)

        assert isinstance(result, Success)
        reg = result.value
        assert "Alpha" in reg.all_project_names
        assert "Beta" in reg.all_project_names


class TestFormatForPrompt:
    def test_format_for_prompt_basic(self):
        """format_for_prompt produces string with domain groups and Uncategorized last."""
        reg = ProjectRegistry(
            groups={
                "Finance": ProjectGroup(
                    domain_name="Finance",
                    domain_path=Path("/vault/Domain/Finance"),
                    projects=[
                        ProjectEntry(name="Alpha", path=Path("/vault/Projects/Alpha")),
                    ],
                ),
                "Uncategorized": ProjectGroup(
                    domain_name="Uncategorized",
                    projects=[
                        ProjectEntry(
                            name="Beta",
                            path=Path("/vault/Projects/Beta"),
                            domain_unknown=True,
                        ),
                    ],
                ),
            }
        )

        output = format_for_prompt(reg)

        assert isinstance(output, str)
        assert "Finance:" in output
        assert "Alpha" in output
        assert "Uncategorized" in output
        # Uncategorized must appear AFTER domain groups
        finance_pos = output.index("Finance:")
        uncat_pos = output.index("Uncategorized")
        assert finance_pos < uncat_pos, "Uncategorized must come after domain groups"

    def test_format_for_prompt_uncategorized_last(self, vault_config):
        """Uncategorized always last regardless of alphabetical order."""
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        _write_claude_md(
            vault_config.root / "Projects" / "Zeta",
            tags=["domain/Finance"],
        )

        result = build_registry(vault_config)
        assert isinstance(result, Success)
        output = format_for_prompt(result.value)

        # Uncategorized should appear after all domain groups
        last_section_start = output.rfind("\n\n")
        uncat_pos = output.rfind("Uncategorized")
        assert uncat_pos > last_section_start, (
            "Uncategorized should be the final section"
        )

    def test_format_for_prompt_alphabetical_within_group(self, vault_config):
        """Project names are sorted alphabetically within each group."""
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        _write_claude_md(
            vault_config.root / "Projects" / "Zeta",
            tags=["domain/Finance"],
        )
        _write_claude_md(
            vault_config.root / "Projects" / "Alpha",
            tags=["domain/Finance"],
        )

        result = build_registry(vault_config)
        assert isinstance(result, Success)
        output = format_for_prompt(result.value)

        # "Alpha" should appear before "Zeta" in the output
        alpha_pos = output.index("Alpha")
        zeta_pos = output.index("Zeta")
        assert alpha_pos < zeta_pos, "Projects must be sorted alphabetically"

    def test_format_for_prompt_empty_group(self):
        """Domain group with no projects shows placeholder."""
        reg = ProjectRegistry(
            groups={
                "Movies": ProjectGroup(
                    domain_name="Movies",
                    domain_path=Path("/vault/Domain/Movies"),
                    projects=[],
                ),
            }
        )

        output = format_for_prompt(reg)

        assert "Movies:" in output
        assert "No active projects" in output


class TestLiveRegistry:
    def test_p2_reg_05_add_project(self, vault_config):
        """LiveRegistry.add_project() adds a project without restart."""
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        live = LiveRegistry(vault_config)

        # Create a new project and add it
        _write_claude_md(
            vault_config.root / "Projects" / "NewProject",
            tags=["domain/Finance"],
        )
        live.add_project("NewProject")

        groups = live.get_groups()
        assert "Finance" in groups
        assert "NewProject" in [e.name for e in groups["Finance"].projects]

    def test_live_registry_remove_project(self, vault_config):
        """LiveRegistry.remove_project() removes a project from all groups."""
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        _write_claude_md(
            vault_config.root / "Projects" / "Alpha",
            tags=["domain/Finance"],
        )
        live = LiveRegistry(vault_config)

        # Should exist after startup
        groups = live.get_groups()
        assert "Alpha" in [e.name for e in groups["Finance"].projects]

        live.remove_project("Alpha")
        groups = live.get_groups()
        assert "Alpha" not in [e.name for e in groups["Finance"].projects]

    def test_p2_reg_06_refresh_domain_changes_group(self, vault_config):
        """refresh_domain re-reads CLAUDE.md and moves project to new group."""
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        (vault_config.root / "Domain" / "Movies").mkdir(parents=True, exist_ok=True)
        _write_claude_md(
            vault_config.root / "Projects" / "Alpha",
            tags=["domain/Finance"],
        )
        live = LiveRegistry(vault_config)

        # Verify initial group
        groups = live.get_groups()
        assert "Alpha" in [e.name for e in groups["Finance"].projects]

        # Rewrite CLAUDE.md with new domain tag
        _write_claude_md(
            vault_config.root / "Projects" / "Alpha",
            tags=["domain/Movies"],
        )
        live.refresh_domain("Alpha")

        groups = live.get_groups()
        assert "Alpha" in [e.name for e in groups["Movies"].projects]
        assert "Alpha" not in [e.name for e in groups["Finance"].projects]

    def test_live_registry_invalidate_domain(self, vault_config):
        """invalidate_domain moves all projects to Uncategorized."""
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        _write_claude_md(
            vault_config.root / "Projects" / "Alpha",
            tags=["domain/Finance"],
        )
        live = LiveRegistry(vault_config)

        # Alpha starts in Finance
        groups = live.get_groups()
        assert "Alpha" in [e.name for e in groups["Finance"].projects]

        live.invalidate_domain("Finance")
        groups = live.get_groups()

        # Finance domain group should be gone
        assert "Finance" not in groups
        # Alpha should move to Uncategorized
        assert "Uncategorized" in groups
        alpha = next(
            (e for e in groups["Uncategorized"].projects if e.name == "Alpha"),
            None,
        )
        assert alpha is not None
        assert alpha.domain_unknown is True

    def test_live_registry_rename_project(self, vault_config):
        """rename_project updates project name in the registry."""
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        _write_claude_md(
            vault_config.root / "Projects" / "Alpha",
            tags=["domain/Finance"],
        )
        live = LiveRegistry(vault_config)

        live.rename_project("Alpha", "AlphaRenamed")
        groups = live.get_groups()

        assert "Alpha" not in [e.name for g in groups.values() for e in g.projects]
        assert "AlphaRenamed" in [e.name for e in groups["Finance"].projects]

    def test_live_registry_thread_safe_concurrent_adds(self, vault_config):
        """Concurrent add_project calls do not cause data loss or errors."""
        import threading

        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        live = LiveRegistry(vault_config)

        errors: list[Exception] = []

        def add_project_safe(name: str) -> None:
            try:
                _write_claude_md(
                    vault_config.root / "Projects" / name,
                    tags=["domain/Finance"],
                )
                live.add_project(name)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=add_project_safe, args=(f"Proj{i}",))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent add: {errors}"
        groups = live.get_groups()
        all_names = {e.name for g in groups.values() for e in g.projects}
        for i in range(10):
            assert f"Proj{i}" in all_names, f"Proj{i} missing from registry"

    def test_get_groups_returns_copy(self, vault_config):
        """get_groups() returns a shallow copy; mutations not reflected."""
        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        _write_claude_md(
            vault_config.root / "Projects" / "Alpha",
            tags=["domain/Finance"],
        )
        live = LiveRegistry(vault_config)

        groups = live.get_groups()
        # Mutate the returned dict
        groups["Fake"] = ProjectGroup(domain_name="Fake")

        # Original registry should be unchanged
        groups2 = live.get_groups()
        assert "Fake" not in groups2

    def test_startup_with_no_projects_dir_uses_empty_registry(self, vault_config):
        """If Projects/ doesn't exist at startup, LiveRegistry starts empty."""
        import shutil

        shutil.rmtree(vault_config.root / "Projects")
        live = LiveRegistry(vault_config)

        groups = live.get_groups()
        # Should still work, just no projects
        assert isinstance(groups, dict)
