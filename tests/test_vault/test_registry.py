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


# ---------------------------------------------------------------------------
# Phase 3 — VaultWatcher LiveRegistry hookup tests
# ---------------------------------------------------------------------------


class TestWatcherRegistryHookups:
    """Test that _VaultEventHandler dispatches to LiveRegistry on vault events.

    These use MagicMock for the registry and call handler event methods directly
    with synthetic watchdog events.  Debounce is set to a tiny value to make the
    threading.Timer fire quickly.
    """

    DEBOUNCE = 0.02
    WAIT = 0.1

    @staticmethod
    def _make_handler(tmp_path: Path, registry=None, **kwargs):

        from core.config import VaultConfig
        from vault.watcher import _VaultEventHandler

        root = tmp_path / "vault"
        root.mkdir(exist_ok=True)
        vault_cfg = VaultConfig(root=root)

        handler = _VaultEventHandler(
            root=root,
            vault_config=vault_cfg,
            on_create=lambda p: None,
            on_modify=lambda p: None,
            on_delete=lambda p: None,
            on_move=lambda s, d: None,
            debounce_seconds=kwargs.pop(
                "debounce", TestWatcherRegistryHookups.DEBOUNCE
            ),
            registry=registry,
            **kwargs,
        )
        return handler, root, vault_cfg

    def test_registry_add_project_on_dir_created(self, tmp_path):
        """DirCreatedEvent on Projects/<New>/ → registry.add_project() called."""
        import time
        from unittest.mock import MagicMock
        from watchdog.events import DirCreatedEvent

        mock_registry = MagicMock()
        handler, root, _ = self._make_handler(tmp_path, registry=mock_registry)
        (root / "Projects" / "NewProject").mkdir(parents=True)

        handler.on_created(DirCreatedEvent(str(root / "Projects" / "NewProject")))

        time.sleep(self.WAIT)
        mock_registry.add_project.assert_called_once_with("NewProject")

    def test_registry_remove_project_on_dir_deleted(self, tmp_path):
        """DirDeletedEvent on Projects/<A>/ → registry.remove_project() called."""
        import time
        from unittest.mock import MagicMock
        from watchdog.events import DirDeletedEvent

        mock_registry = MagicMock()
        handler, root, _ = self._make_handler(tmp_path, registry=mock_registry)

        handler.on_deleted(DirDeletedEvent(str(root / "Projects" / "Alpha")))

        time.sleep(self.WAIT)
        mock_registry.remove_project.assert_called_once_with("Alpha")

    def test_registry_invalidate_domain_on_domain_deleted(self, tmp_path):
        """DirDeletedEvent on Domain/<D>/ → registry.invalidate_domain() called."""
        import time
        from unittest.mock import MagicMock
        from watchdog.events import DirDeletedEvent

        mock_registry = MagicMock()
        handler, root, _ = self._make_handler(tmp_path, registry=mock_registry)

        handler.on_deleted(DirDeletedEvent(str(root / "Domain" / "Finance")))

        time.sleep(self.WAIT)
        mock_registry.invalidate_domain.assert_called_once_with("Finance")

    def test_registry_rename_project_on_dir_moved(self, tmp_path):
        """DirMovedEvent inside Projects/ → registry.rename_project() called."""
        import time
        from unittest.mock import MagicMock
        from watchdog.events import DirMovedEvent

        mock_registry = MagicMock()
        handler, root, _ = self._make_handler(tmp_path, registry=mock_registry)

        handler.on_moved(
            DirMovedEvent(
                str(root / "Projects" / "Alpha"),
                str(root / "Projects" / "AlphaRenamed"),
            )
        )

        time.sleep(self.WAIT)
        mock_registry.rename_project.assert_called_once_with("Alpha", "AlphaRenamed")

    def test_registry_remove_on_project_moved_outside(self, tmp_path):
        """DirMovedEvent from Projects/ to Archive/ → registry.remove_project()."""
        import time
        from unittest.mock import MagicMock
        from watchdog.events import DirMovedEvent

        mock_registry = MagicMock()
        handler, root, _ = self._make_handler(tmp_path, registry=mock_registry)

        handler.on_moved(
            DirMovedEvent(
                str(root / "Projects" / "Alpha"),
                str(root / "Archive" / "Alpha"),
            )
        )

        time.sleep(self.WAIT)
        mock_registry.remove_project.assert_called_once_with("Alpha")

    def test_registry_invalidate_domain_on_domain_moved(self, tmp_path):
        """DirMovedEvent from Domain/<D>/ → registry.invalidate_domain() called."""
        import time
        from unittest.mock import MagicMock
        from watchdog.events import DirMovedEvent

        mock_registry = MagicMock()
        handler, root, _ = self._make_handler(tmp_path, registry=mock_registry)

        handler.on_moved(
            DirMovedEvent(
                str(root / "Domain" / "OldDomain"),
                str(root / "Domain" / "NewName"),
            )
        )

        time.sleep(self.WAIT)
        mock_registry.invalidate_domain.assert_called_once_with("OldDomain")

    def test_registry_refresh_domain_on_claude_md_modified(self, tmp_path):
        """FileModifiedEvent on Projects/<A>/CLAUDE.md → registry.refresh_domain()."""
        import time
        from unittest.mock import MagicMock
        from watchdog.events import FileModifiedEvent

        mock_registry = MagicMock()
        handler, root, _ = self._make_handler(tmp_path, registry=mock_registry)
        (root / "Projects" / "Alpha").mkdir(parents=True)

        handler.on_modified(
            FileModifiedEvent(str(root / "Projects" / "Alpha" / "CLAUDE.md"))
        )

        time.sleep(self.WAIT)
        mock_registry.refresh_domain.assert_called_once_with("Alpha")

    def test_registry_not_called_when_registry_is_none(self, tmp_path):
        """When registry=None (default), all events fire normally with no errors."""
        import time
        from watchdog.events import (
            DirCreatedEvent,
            DirDeletedEvent,
            DirMovedEvent,
            FileModifiedEvent,
        )

        from core.config import VaultConfig
        from vault.watcher import _VaultEventHandler

        root = tmp_path / "vault"
        root.mkdir(exist_ok=True)
        vault_cfg = VaultConfig(root=root)

        # No registry kwarg — defaults to None
        handler = _VaultEventHandler(
            root=root,
            vault_config=vault_cfg,
            on_create=lambda p: None,
            on_modify=lambda p: None,
            on_delete=lambda p: None,
            on_move=lambda s, d: None,
            debounce_seconds=self.DEBOUNCE,
        )

        (root / "Projects" / "Alpha").mkdir(parents=True)
        handler.on_created(DirCreatedEvent(str(root / "Projects" / "Alpha")))
        handler.on_deleted(DirDeletedEvent(str(root / "Projects" / "Alpha")))
        handler.on_moved(
            DirMovedEvent(
                str(root / "Projects" / "Alpha"),
                str(root / "Projects" / "Beta"),
            )
        )
        handler.on_modified(FileModifiedEvent(str(root / "inbox" / "note.md")))

        time.sleep(self.WAIT)

    def test_registry_dotfile_project_dir_not_added(self, tmp_path):
        """Dotfile dirs in Projects/ are not sent to registry.add_project()."""
        import time
        from unittest.mock import MagicMock
        from watchdog.events import DirCreatedEvent

        mock_registry = MagicMock()
        handler, root, _ = self._make_handler(tmp_path, registry=mock_registry)
        (root / "Projects" / ".hidden").mkdir(parents=True)

        handler.on_created(DirCreatedEvent(str(root / "Projects" / ".hidden")))

        time.sleep(self.WAIT)
        mock_registry.add_project.assert_not_called()


class TestVaultContextIntegration:
    """Verify _build_vault_context uses format_for_prompt from registry."""

    def test_build_vault_context_uses_registry(self, vault_config):
        """_build_vault_context returns registry-formatted output."""
        from pipelines.capture import _build_vault_context

        (vault_config.root / "Domain" / "Finance").mkdir(parents=True, exist_ok=True)
        _write_claude_md(
            vault_config.root / "Projects" / "Alpha",
            tags=["domain/Finance"],
        )

        output, project_names, domain_names = _build_vault_context(vault_config)

        # Should use format_for_prompt format (not raw folder listing)
        assert "Finance:" in output
        assert "Alpha" in output
        # Should show project under its domain
        assert "  - Alpha" in output
        # Should return non-empty typed name sets
        assert isinstance(project_names, frozenset)
        assert isinstance(domain_names, frozenset)

    def test_build_vault_context_fallback_on_missing_projects(self, vault_config):
        """_build_vault_context falls back to flat listing if registry fails."""
        import shutil
        from pipelines.capture import _build_vault_context

        shutil.rmtree(vault_config.root / "Projects")

        output, project_names, domain_names = _build_vault_context(vault_config)

        # Should NOT crash — fallback to flat listing
        assert isinstance(output, str)
        assert "Domains:" in output
        assert "Projects:" in output
        # Fallback should return None to trigger backward-compat pooled validation
        assert project_names is None
        assert domain_names is None
