"""
tests/test_vault/test_paths_managed_summaries.py

Unit tests for _is_managed_summaries_area after Phase 10 extension to cover
editable-file .summaries/ areas (Projects/<A>/.summaries/, Domain/<D>/.summaries/).

CONFIG is never imported at module scope — all tests use VaultConfig(root=tmp_path).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import VaultConfig


@pytest.fixture
def vault_cfg(tmp_path: Path) -> VaultConfig:
    return VaultConfig(root=tmp_path / "vault")


class TestIsManagedSummariesArea:
    """Phase 10: _is_managed_summaries_area covers editable-file .summaries/ areas."""

    def test_inbox_is_managed(self, vault_cfg: VaultConfig):
        from vault.paths import _is_managed_summaries_area
        assert _is_managed_summaries_area(vault_cfg.inbox_path, vault_cfg) is True

    def test_inbox_summaries_is_managed(self, vault_cfg: VaultConfig):
        from vault.paths import _is_managed_summaries_area
        p = vault_cfg.inbox_path / vault_cfg.summaries_subdir / "sibling.md"
        assert _is_managed_summaries_area(p, vault_cfg) is True

    def test_attachment_summaries_is_managed(self, vault_cfg: VaultConfig):
        from vault.paths import _is_managed_summaries_area
        p = (
            vault_cfg.projects_path / "Alpha" / vault_cfg.attachment_dir
            / vault_cfg.summaries_subdir / "report.pdf.md"
        )
        assert _is_managed_summaries_area(p, vault_cfg) is True

    def test_domain_attachment_summaries_is_managed(self, vault_cfg: VaultConfig):
        from vault.paths import _is_managed_summaries_area
        p = (
            vault_cfg.domain_path / "Finance" / vault_cfg.attachment_dir
            / vault_cfg.summaries_subdir / "invoice.pdf.md"
        )
        assert _is_managed_summaries_area(p, vault_cfg) is True

    # ── Phase 10 extension: editable-file .summaries/ areas ──────────────

    def test_project_editable_summaries_is_managed(self, vault_cfg: VaultConfig):
        """Projects/<A>/.summaries/sibling.md → True (Phase 10 extension)."""
        from vault.paths import _is_managed_summaries_area
        p = (
            vault_cfg.projects_path / "Alpha"
            / vault_cfg.summaries_subdir / "budget.docx.md"
        )
        assert _is_managed_summaries_area(p, vault_cfg) is True

    def test_domain_editable_summaries_is_managed(self, vault_cfg: VaultConfig):
        """Domain/<D>/.summaries/sibling.md → True (Phase 10 extension)."""
        from vault.paths import _is_managed_summaries_area
        p = (
            vault_cfg.domain_path / "Finance"
            / vault_cfg.summaries_subdir / "report.xlsx.md"
        )
        assert _is_managed_summaries_area(p, vault_cfg) is True

    def test_editable_summaries_dir_itself_is_managed(self, vault_cfg: VaultConfig):
        """The .summaries/ directory itself at project root → True."""
        from vault.paths import _is_managed_summaries_area
        p = vault_cfg.projects_path / "Alpha" / vault_cfg.summaries_subdir
        assert _is_managed_summaries_area(p, vault_cfg) is True

    def test_nested_user_folder_not_managed(self, vault_cfg: VaultConfig):
        """A user-created .summaries/ somewhere else → False."""
        from vault.paths import _is_managed_summaries_area
        # e.g., Projects/Alpha/materials/.summaries/ — not managed
        p = (
            vault_cfg.projects_path / "Alpha" / "materials"
            / vault_cfg.summaries_subdir / "note.md"
        )
        assert _is_managed_summaries_area(p, vault_cfg) is False

    def test_random_vault_location_not_managed(self, vault_cfg: VaultConfig):
        from vault.paths import _is_managed_summaries_area
        p = vault_cfg.root / "some_random_folder" / "file.md"
        assert _is_managed_summaries_area(p, vault_cfg) is False

    def test_materials_folder_not_managed(self, vault_cfg: VaultConfig):
        """Regular project subfolder is not a managed summaries area."""
        from vault.paths import _is_managed_summaries_area
        p = vault_cfg.projects_path / "Alpha" / "notes" / "meeting.md"
        assert _is_managed_summaries_area(p, vault_cfg) is False
