"""
tests/test_vault/test_paths.py

Tests for vault/paths.py — parametrized vault path helpers.
All tests use the `vault_root` fixture from conftest.py which patches
core.config._CONFIG to point at a temp vault directory.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path


from vault.paths import (
    _is_in_managed_attachment,
    briefings_for,
    briefings_today,
    documentation,
    domain_archive,
    domain_attachment,
    domain_dir,
    domain_notes,
    domain_summaries,
    load_valid_domains,
    project_attachment,
    project_dir,
    project_materials,
    project_summaries,
    synthesis_week,
)


class TestProjectDir:
    def test_project_dir_returns_subfolder_of_projects(self, vault_root: Path):
        result = project_dir("X")
        assert result == vault_root / "Projects" / "X"
        assert result.is_dir()

    def test_project_materials_returns_materials_subfolder(self, vault_root: Path):
        result = project_materials("X")
        assert result == vault_root / "Projects" / "X" / "materials"
        assert result.is_dir()


class TestDomainDir:
    def test_domain_dir_returns_subfolder_of_domain(self, vault_root: Path):
        result = domain_dir("Movies")
        assert result == vault_root / "Domain" / "Movies"
        assert result.is_dir()

    def test_domain_notes_returns_notes_subfolder(self, vault_root: Path):
        result = domain_notes("Movies")
        assert result == vault_root / "Domain" / "Movies" / "notes"
        assert result.is_dir()


class TestDocumentation:
    def test_documentation_returns_md_path_with_parent_created(self, vault_root: Path):
        result = documentation("Y")
        assert result == vault_root / "Documentation" / "Y.md"
        assert not result.exists()
        assert result.parent.is_dir()


class TestBriefings:
    def test_briefings_today_year_nested_format(self, vault_root: Path):
        result = briefings_today()
        today = date.today()
        expected_name = f"{today.month:02d}_{today.day:02d}.md"
        assert result.name == expected_name
        assert result.parent.name == str(today.year)
        assert result.parent.is_dir()

    def test_briefings_for_specific_date(self, vault_root: Path):
        result = briefings_for(date(2026, 4, 25))
        assert result.name == "04_25.md"
        assert result.parent.name == "2026"
        assert str(result).endswith("2026/04_25.md")


class TestSynthesisWeek:
    def test_synthesis_week_iso_week_format(self, vault_root: Path):
        result = synthesis_week(date(2026, 4, 25))
        assert result.name == "2026-W17.md"


class TestToVaultPath:
    """to_vault_path converts absolute path to NFC-normalised POSIX vault-relative string."""

    def test_returns_posix_relative_path(self, vault_root: Path):
        from vault.paths import to_vault_path
        result = to_vault_path(vault_root / "inbox" / "note.md")
        assert result == "inbox/note.md"

    def test_nested_path_includes_full_relative_segments(self, vault_root: Path):
        from vault.paths import to_vault_path
        result = to_vault_path(vault_root / "Projects" / "foo" / "bar.md")
        assert result == "Projects/foo/bar.md"

    def test_result_is_nfc_normalised(self, vault_root: Path):
        import unicodedata
        from vault.paths import to_vault_path
        # NFD Vietnamese name (decomposed) — macOS stores filenames this way
        nfd_name = unicodedata.normalize("NFD", "nôi-dung.md")
        result = to_vault_path(vault_root / "inbox" / nfd_name)
        assert result == unicodedata.normalize("NFC", f"inbox/{nfd_name}")

    def test_result_uses_forward_slashes(self, vault_root: Path):
        from vault.paths import to_vault_path
        result = to_vault_path(vault_root / "Projects" / "x.md")
        assert "\\" not in result


class TestLoadValidDomains:
    def test_returns_domain_folder_names(self, tmp_path: Path) -> None:
        vault_root = tmp_path / "vault"
        domain_dir = vault_root / "Domain"
        domain_dir.mkdir(parents=True)
        (domain_dir / "finance").mkdir()
        (domain_dir / "strategy").mkdir()
        result = load_valid_domains(vault_root)
        assert result == frozenset(["finance", "strategy"])

    def test_returns_empty_frozenset_when_domain_folder_absent(self, tmp_path: Path) -> None:
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        result = load_valid_domains(vault_root)
        assert result == frozenset()

    def test_excludes_hidden_folders(self, tmp_path: Path) -> None:
        vault_root = tmp_path / "vault"
        domain_dir = vault_root / "Domain"
        domain_dir.mkdir(parents=True)
        (domain_dir / "finance").mkdir()
        (domain_dir / ".obsidian").mkdir()
        result = load_valid_domains(vault_root)
        assert ".obsidian" not in result
        assert "finance" in result


class TestIdempotent:
    def test_helpers_idempotent(self, vault_root: Path):
        for _ in range(2):
            project_dir("IdempotentTest")
            project_materials("IdempotentTest")
            domain_dir("IdempotentTest")
            domain_notes("IdempotentTest")
            documentation("IdempotentTest")
            briefings_for(date(2026, 1, 1))
            synthesis_week(date(2026, 1, 1))

        assert (vault_root / "Projects" / "IdempotentTest").is_dir()
        assert (vault_root / "Domain" / "IdempotentTest").is_dir()


class TestAttachmentHelpers:
    def test_project_attachment_returns_correct_path(self, vault_root: Path):
        result = project_attachment("Strategy")
        assert result == vault_root / "Projects" / "Strategy" / "attachment"
        assert result.is_dir()

    def test_project_summaries_returns_correct_path(self, vault_root: Path):
        result = project_summaries("Strategy")
        assert result == vault_root / "Projects" / "Strategy" / "attachment" / ".summaries"
        assert result.is_dir()

    def test_domain_attachment_returns_correct_path(self, vault_root: Path):
        result = domain_attachment("Finance")
        assert result == vault_root / "Domain" / "Finance" / "attachment"
        assert result.is_dir()

    def test_domain_summaries_returns_correct_path(self, vault_root: Path):
        result = domain_summaries("Finance")
        assert result == vault_root / "Domain" / "Finance" / "attachment" / ".summaries"
        assert result.is_dir()

    def test_project_attachment_respects_config_override(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "vault"
        root.mkdir()
        from core.config import VaultConfig
        import core.config as cfg_module
        from unittest.mock import MagicMock
        vc = VaultConfig(root=root, attachment_dir="files")
        fake_config = MagicMock()
        fake_config.main.vault = vc
        monkeypatch.setattr(cfg_module, "_CONFIG", fake_config)

        result = project_attachment("Strategy")
        assert result == root / "Projects" / "Strategy" / "files"
        assert result.is_dir()

    def test_project_summaries_respects_config_override(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "vault"
        root.mkdir()
        from core.config import VaultConfig
        import core.config as cfg_module
        from unittest.mock import MagicMock
        vc = VaultConfig(root=root, attachment_dir="files", summaries_subdir=".sums")
        fake_config = MagicMock()
        fake_config.main.vault = vc
        monkeypatch.setattr(cfg_module, "_CONFIG", fake_config)

        result = project_summaries("Strategy")
        assert result == root / "Projects" / "Strategy" / "files" / ".sums"
        assert result.is_dir()

    def test_domain_attachment_respects_config_override(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "vault"
        root.mkdir()
        from core.config import VaultConfig
        import core.config as cfg_module
        from unittest.mock import MagicMock
        vc = VaultConfig(root=root, attachment_dir="files")
        fake_config = MagicMock()
        fake_config.main.vault = vc
        monkeypatch.setattr(cfg_module, "_CONFIG", fake_config)

        result = domain_attachment("Finance")
        assert result == root / "Domain" / "Finance" / "files"
        assert result.is_dir()

    def test_domain_summaries_respects_config_override(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "vault"
        root.mkdir()
        from core.config import VaultConfig
        import core.config as cfg_module
        from unittest.mock import MagicMock
        vc = VaultConfig(root=root, attachment_dir="files", summaries_subdir=".sums")
        fake_config = MagicMock()
        fake_config.main.vault = vc
        monkeypatch.setattr(cfg_module, "_CONFIG", fake_config)

        result = domain_summaries("Finance")
        assert result == root / "Domain" / "Finance" / "files" / ".sums"
        assert result.is_dir()


class TestIsInManagedAttachment:
    def test_returns_true_for_project_attachment_pdf(self, tmp_path: Path):
        from core.config import VaultConfig

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        pdf = root / "Projects" / "Alpha" / "attachment" / "report.pdf"
        assert _is_in_managed_attachment(pdf, vc) is True

    def test_returns_true_for_domain_attachment_file(self, tmp_path: Path):
        from core.config import VaultConfig

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        docx = root / "Domain" / "Finance" / "attachment" / "budget.docx"
        assert _is_in_managed_attachment(docx, vc) is True

    def test_returns_false_for_project_md_outside_attachment(self, tmp_path: Path):
        from core.config import VaultConfig

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        note = root / "Projects" / "Alpha" / "notes.md"
        assert _is_in_managed_attachment(note, vc) is False

    def test_returns_false_for_inbox_pdf(self, tmp_path: Path):
        from core.config import VaultConfig

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        pdf = root / "inbox" / "report.pdf"
        assert _is_in_managed_attachment(pdf, vc) is False

    def test_returns_false_for_nested_but_not_under_projects_or_domain(self, tmp_path: Path):
        from core.config import VaultConfig

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        pdf = root / "Documentation" / "attachment" / "file.pdf"
        assert _is_in_managed_attachment(pdf, vc) is False

    def test_respects_custom_attachment_dir(self, tmp_path: Path):
        from core.config import VaultConfig

        root = tmp_path / "vault"
        vc = VaultConfig(root=root, attachment_dir="files")
        pdf = root / "Projects" / "Alpha" / "files" / "report.pdf"
        assert _is_in_managed_attachment(pdf, vc) is True

    def test_returns_false_when_attachment_dir_mismatch(self, tmp_path: Path):
        from core.config import VaultConfig

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        pdf = root / "Projects" / "Alpha" / "other" / "report.pdf"
        assert _is_in_managed_attachment(pdf, vc) is False


class TestLocationContext:
    """Tests for the private _location_context() helper."""

    def test_path_under_domain_returns_domain_tuple(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _location_context

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Domain" / "Engineering" / "foo.md"
        result = _location_context(path, vc)
        assert result == ("domain", "Engineering")

    def test_path_under_project_returns_project_tuple(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _location_context

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Projects" / "Alpha" / "bar.md"
        result = _location_context(path, vc)
        assert result == ("project", "Alpha")

    def test_path_under_inbox_returns_inbox_tuple(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _location_context

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "inbox" / "baz.md"
        result = _location_context(path, vc)
        assert result == ("inbox", None)

    def test_path_elsewhere_returns_none_tuple(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _location_context

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Documentation" / "some-note.md"
        result = _location_context(path, vc)
        assert result == (None, None)

    def test_deeply_nested_domain_path_returns_top_level_domain(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _location_context

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Domain" / "Engineering" / "subdir" / "deep.md"
        result = _location_context(path, vc)
        assert result == ("domain", "Engineering")

    def test_deeply_nested_project_path_returns_top_level_project(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _location_context

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Projects" / "Alpha" / "attachment" / "file.pdf"
        result = _location_context(path, vc)
        assert result == ("project", "Alpha")

    def test_respects_custom_domain_and_projects_dirs(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _location_context

        root = tmp_path / "vault"
        vc = VaultConfig(root=root, domain_dir="Domains", projects_dir="Work")
        path = root / "Domains" / "Finance" / "report.md"
        result = _location_context(path, vc)
        assert result == ("domain", "Finance")


class TestDomainArchive:
    def test_returns_correct_path_for_domain(self, tmp_path: Path):
        from core.config import VaultConfig

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        result = domain_archive("Finance", vc)
        assert result == root / "Domain" / "Finance" / "Archive"
        assert result.is_dir()

    def test_respects_custom_archive_dir(self, tmp_path: Path):
        from core.config import VaultConfig

        root = tmp_path / "vault"
        vc = VaultConfig(root=root, archive_dir="Completed")
        result = domain_archive("Product", vc)
        assert result == root / "Domain" / "Product" / "Completed"
        assert result.is_dir()


# ---------------------------------------------------------------------------
# Tests for _is_ai_output predicate (Phase 5, T4)
# ---------------------------------------------------------------------------


class TestIsAiOutput:
    """7 unit tests for the _is_ai_output predicate.

    _is_ai_output returns True if any part of the path matches a folder the
    system writes to itself (Briefings, Synthesis, Documentation). Name-match
    is depth-agnostic — the folder name can appear at any Path.parts position.
    """

    def test_returns_true_for_briefings_dir(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_ai_output

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Briefings" / "2026" / "06_04.md"
        assert _is_ai_output(path, vc) is True

    def test_returns_true_for_synthesis_dir(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_ai_output

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Synthesis" / "2026-W23.md"
        assert _is_ai_output(path, vc) is True

    def test_returns_true_for_documentation_dir(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_ai_output

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Documentation" / "Alpha.md"
        assert _is_ai_output(path, vc) is True

    def test_returns_false_for_project_path(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_ai_output

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Projects" / "Alpha" / "note.md"
        assert _is_ai_output(path, vc) is False

    def test_returns_false_for_domain_path(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_ai_output

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Domain" / "Finance" / "report.md"
        assert _is_ai_output(path, vc) is False

    def test_returns_false_for_inbox_path(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_ai_output

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "inbox" / "note.md"
        assert _is_ai_output(path, vc) is False

    def test_no_filesystem_io_and_no_config_import(self, tmp_path: Path):
        """Verify _is_ai_output does no filesystem I/O and works on non-existent paths."""
        from core.config import VaultConfig
        from vault.paths import _is_ai_output

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        # Path that does not exist on disk — still returns True (pure path math, no I/O)
        path = root / "Briefings" / "nonexistent.md"
        assert path.exists() is False
        assert _is_ai_output(path, vc) is True


# ---------------------------------------------------------------------------
# Tests for _is_misplaced predicate (Phase 5, T4)
# ---------------------------------------------------------------------------


class TestIsMisplaced:
    """8 unit tests for the _is_misplaced predicate.

    _is_misplaced returns True when an .md file sits at the bare root of
    Projects/ or Domain/ without a real subfolder (e.g. Projects/stray.md).
    Inbox and AI-output folders are always valid; anywhere else returns False.
    """

    def test_returns_true_for_stray_md_in_projects_root(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_misplaced

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Projects" / "stray.md"
        assert _is_misplaced(path, vc) is True

    def test_returns_true_for_stray_md_in_domain_root(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_misplaced

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Domain" / "stray.md"
        assert _is_misplaced(path, vc) is True

    def test_returns_false_for_file_inside_project_subfolder(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_misplaced

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Projects" / "Alpha" / "note.md"
        assert _is_misplaced(path, vc) is False

    def test_returns_false_for_file_inside_domain_subfolder(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_misplaced

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Domain" / "Finance" / "report.md"
        assert _is_misplaced(path, vc) is False

    def test_returns_false_for_file_in_inbox(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_misplaced

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "inbox" / "note.md"
        assert _is_misplaced(path, vc) is False

    def test_returns_false_for_file_in_ai_output_dir(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_misplaced

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Briefings" / "2026" / "daily.md"
        assert _is_misplaced(path, vc) is False

    def test_returns_false_for_file_elsewhere(self, tmp_path: Path):
        from core.config import VaultConfig
        from vault.paths import _is_misplaced

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "some-random-folder" / "file.md"
        assert _is_misplaced(path, vc) is False

    def test_returns_true_for_any_file_at_projects_bare_root(self, tmp_path: Path):
        """_is_misplaced is a pure path check — it returns True for any file
        at the bare root of Projects/, regardless of extension. The .md
        restriction is enforced in the caller (scan_capture sweep loop)."""
        from core.config import VaultConfig
        from vault.paths import _is_misplaced

        root = tmp_path / "vault"
        vc = VaultConfig(root=root)
        path = root / "Projects" / "stray.pdf"
        assert _is_misplaced(path, vc) is True


# ---------------------------------------------------------------------------
# Phase 4 — is_batch_subfolder predicate
# ---------------------------------------------------------------------------


class TestIsBatchSubfolder:
    """Tests for is_batch_subfolder() — batch-worthiness predicate."""

    def _vc(self, vault_root: Path) -> "VaultConfig":
        from core.config import VaultConfig
        return VaultConfig(root=vault_root)

    def test_project_subfolder_true(self, vault_root: Path):
        """Projects/<A>/subdir/ is batch-worthy."""
        from vault.paths import is_batch_subfolder

        vc = self._vc(vault_root)
        (vault_root / "Projects" / "Alpha").mkdir(exist_ok=True)
        subdir = vault_root / "Projects" / "Alpha" / "Q2-reports"
        subdir.mkdir()
        assert is_batch_subfolder(subdir, vc) is True

    def test_domain_subfolder_true(self, vault_root: Path):
        """Domain/<D>/subdir/ is batch-worthy."""
        from vault.paths import is_batch_subfolder

        vc = self._vc(vault_root)
        (vault_root / "Domain" / "Finance").mkdir(exist_ok=True)
        subdir = vault_root / "Domain" / "Finance" / "sub"
        subdir.mkdir()
        assert is_batch_subfolder(subdir, vc) is True

    def test_inbox_subfolder_true(self, vault_root: Path):
        """inbox/subdir/ is batch-worthy."""
        from vault.paths import is_batch_subfolder

        vc = self._vc(vault_root)
        subdir = vault_root / "inbox" / "Q2-drop"
        subdir.mkdir()
        assert is_batch_subfolder(subdir, vc) is True

    def test_project_root_false(self, vault_root: Path):
        """Projects/<A>/ (root, not nested) is NOT batch-worthy."""
        from vault.paths import is_batch_subfolder

        vc = self._vc(vault_root)
        proj = vault_root / "Projects" / "Alpha"
        proj.mkdir()
        assert is_batch_subfolder(proj, vc) is False

    def test_domain_root_false(self, vault_root: Path):
        """Domain/<D>/ (root) is NOT batch-worthy."""
        from vault.paths import is_batch_subfolder

        vc = self._vc(vault_root)
        dom = vault_root / "Domain" / "Finance"
        dom.mkdir()
        assert is_batch_subfolder(dom, vc) is False

    def test_inbox_root_false(self, vault_root: Path):
        """inbox/ itself is NOT batch-worthy."""
        from vault.paths import is_batch_subfolder

        vc = self._vc(vault_root)
        assert is_batch_subfolder(vault_root / "inbox", vc) is False

    def test_attachment_blocked(self, vault_root: Path):
        """Projects/<A>/attachment/ is NOT batch-worthy."""
        from vault.paths import is_batch_subfolder

        vc = self._vc(vault_root)
        (vault_root / "Projects" / "Alpha").mkdir(exist_ok=True)
        att = vault_root / "Projects" / "Alpha" / "attachment"
        att.mkdir()
        assert is_batch_subfolder(att, vc) is False

    def test_summaries_blocked(self, vault_root: Path):
        """Projects/<A>/.summaries/ is NOT batch-worthy."""
        from vault.paths import is_batch_subfolder

        vc = self._vc(vault_root)
        (vault_root / "Projects" / "Alpha").mkdir(exist_ok=True)
        sdir = vault_root / "Projects" / "Alpha" / ".summaries"
        sdir.mkdir()
        assert is_batch_subfolder(sdir, vc) is False

    def test_archive_blocked(self, vault_root: Path):
        """Domain/<D>/Archive/ is NOT batch-worthy."""
        from vault.paths import is_batch_subfolder

        vc = self._vc(vault_root)
        (vault_root / "Domain" / "Finance").mkdir(exist_ok=True)
        arch = vault_root / "Domain" / "Finance" / "Archive"
        arch.mkdir()
        assert is_batch_subfolder(arch, vc) is False

    def test_outside_vault_false(self, tmp_path: Path, vault_root: Path):
        """Path outside vault returns False."""
        from vault.paths import is_batch_subfolder

        vc = self._vc(vault_root)
        outside = tmp_path / "outside"
        outside.mkdir()
        assert is_batch_subfolder(outside, vc) is False
