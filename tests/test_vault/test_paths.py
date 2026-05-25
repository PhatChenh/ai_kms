"""
tests/test_vault/test_paths.py

Tests for vault/paths.py — parametrized vault path helpers.
All tests use the `vault_root` fixture from conftest.py which patches
core.config._CONFIG to point at a temp vault directory.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

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
