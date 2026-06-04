"""
tests/test_vault/test_paths_placement.py

Tests for Placement dataclass and resolve_placement function from vault/paths.py.
All tests construct VaultConfig(root=tmp_path) directly — no CONFIG import at module scope.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from core.config import VaultConfig


# ---------------------------------------------------------------------------
# Lazy imports — avoid module-scope CONFIG trigger
# ---------------------------------------------------------------------------

@pytest.fixture
def vault_cfg(tmp_path: Path) -> VaultConfig:
    """VaultConfig pointing at a temp vault root with default settings."""
    return VaultConfig(root=tmp_path / "vault")


# ============================================================================
# Test 1: Placement is frozen
# ============================================================================

def test_placement_is_frozen(tmp_path: Path):
    """Assigning a field after construction raises FrozenInstanceError."""
    from vault.paths import Placement

    p = Placement(
        final_dir=tmp_path / "Projects" / "Alpha",
        sibling_dir=tmp_path / "Projects" / "Alpha" / ".summaries",
        needs_move=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.final_dir = tmp_path / "other"  # type: ignore[misc]


# ============================================================================
# Test 2: No-edit file NOT in attachment — needs_move=True, final_dir ends with attachment/
# ============================================================================

def test_no_edit_not_in_attachment_project(vault_cfg: VaultConfig):
    """A .pdf in the project root routes to attachment/ and needs_move is True."""
    from vault.paths import resolve_placement

    file_path = vault_cfg.projects_path / "Alpha" / "report.pdf"
    result = resolve_placement(
        file_path=file_path,
        target_type="project",
        target_name="Alpha",
        vault_cfg=vault_cfg,
    )

    assert result.final_dir == vault_cfg.projects_path / "Alpha" / vault_cfg.attachment_dir
    assert result.needs_move is True


# ============================================================================
# Test 3: No-edit file already in attachment — needs_move=False, same final_dir
# ============================================================================

def test_no_edit_already_in_attachment_project(vault_cfg: VaultConfig):
    """A .pdf already in attachment/ stays there with needs_move=False."""
    from vault.paths import resolve_placement

    file_path = (
        vault_cfg.projects_path / "Alpha" / vault_cfg.attachment_dir / "report.pdf"
    )
    result = resolve_placement(
        file_path=file_path,
        target_type="project",
        target_name="Alpha",
        vault_cfg=vault_cfg,
    )

    assert result.final_dir == vault_cfg.projects_path / "Alpha" / vault_cfg.attachment_dir
    assert result.needs_move is False


# ============================================================================
# Test 4: Editable file not in attachment — needs_move=False, final_dir is project root
# ============================================================================

def test_editable_not_in_attachment_project(vault_cfg: VaultConfig):
    """A .docx in the project root stays in root with needs_move=False."""
    from vault.paths import resolve_placement

    file_path = vault_cfg.projects_path / "Alpha" / "report.docx"
    result = resolve_placement(
        file_path=file_path,
        target_type="project",
        target_name="Alpha",
        vault_cfg=vault_cfg,
    )

    assert result.final_dir == vault_cfg.projects_path / "Alpha"
    assert result.needs_move is False


# ============================================================================
# Test 5: Editable file in attachment — needs_move=True, final_dir is project root
# ============================================================================

def test_editable_in_attachment_project(vault_cfg: VaultConfig):
    """A .docx stuck in attachment/ gets pulled out to root with needs_move=True."""
    from vault.paths import resolve_placement

    file_path = (
        vault_cfg.projects_path / "Alpha" / vault_cfg.attachment_dir / "budget.xlsx"
    )
    result = resolve_placement(
        file_path=file_path,
        target_type="project",
        target_name="Alpha",
        vault_cfg=vault_cfg,
    )

    assert result.final_dir == vault_cfg.projects_path / "Alpha"
    assert result.needs_move is True


# ============================================================================
# Test 6: Domain symmetry — all four routing cases
# ============================================================================

def test_no_edit_not_in_attachment_domain(vault_cfg: VaultConfig):
    """A .pdf in domain root routes to attachment/ with needs_move=True."""
    from vault.paths import resolve_placement

    file_path = vault_cfg.domain_path / "Finance" / "invoice.pdf"
    result = resolve_placement(
        file_path=file_path,
        target_type="domain",
        target_name="Finance",
        vault_cfg=vault_cfg,
    )

    assert result.final_dir == vault_cfg.domain_path / "Finance" / vault_cfg.attachment_dir
    assert result.needs_move is True


def test_no_edit_already_in_attachment_domain(vault_cfg: VaultConfig):
    """A .pdf already in domain attachment/ stays there with needs_move=False."""
    from vault.paths import resolve_placement

    file_path = (
        vault_cfg.domain_path / "Finance" / vault_cfg.attachment_dir / "invoice.pdf"
    )
    result = resolve_placement(
        file_path=file_path,
        target_type="domain",
        target_name="Finance",
        vault_cfg=vault_cfg,
    )

    assert result.final_dir == vault_cfg.domain_path / "Finance" / vault_cfg.attachment_dir
    assert result.needs_move is False


def test_editable_not_in_attachment_domain(vault_cfg: VaultConfig):
    """A .docx in domain root stays in root with needs_move=False."""
    from vault.paths import resolve_placement

    file_path = vault_cfg.domain_path / "Finance" / "budget.xlsx"
    result = resolve_placement(
        file_path=file_path,
        target_type="domain",
        target_name="Finance",
        vault_cfg=vault_cfg,
    )

    assert result.final_dir == vault_cfg.domain_path / "Finance"
    assert result.needs_move is False


def test_editable_in_attachment_domain(vault_cfg: VaultConfig):
    """A .docx stuck in domain attachment/ gets pulled out to root with needs_move=True."""
    from vault.paths import resolve_placement

    file_path = (
        vault_cfg.domain_path / "Finance" / vault_cfg.attachment_dir / "budget.xlsx"
    )
    result = resolve_placement(
        file_path=file_path,
        target_type="domain",
        target_name="Finance",
        vault_cfg=vault_cfg,
    )

    assert result.final_dir == vault_cfg.domain_path / "Finance"
    assert result.needs_move is True


# ============================================================================
# Test 7: Uppercase extension routed as no-edit
# ============================================================================

def test_uppercase_extension_routed_as_no_edit(vault_cfg: VaultConfig):
    """.PDF (uppercase) is treated the same as .pdf and routes to attachment/."""
    from vault.paths import resolve_placement

    file_path = vault_cfg.projects_path / "Alpha" / "REPORT.PDF"
    result = resolve_placement(
        file_path=file_path,
        target_type="project",
        target_name="Alpha",
        vault_cfg=vault_cfg,
    )

    assert result.final_dir == vault_cfg.projects_path / "Alpha" / vault_cfg.attachment_dir
    assert result.needs_move is True


# ============================================================================
# Test 8: sibling_dir == final_dir / summaries_subdir in every case
# ============================================================================

def test_sibling_dir_follows_final_dir(vault_cfg: VaultConfig):
    """sibling_dir is always final_dir / summaries_subdir."""
    from vault.paths import resolve_placement

    # No-edit (attachment destination)
    file_path = vault_cfg.projects_path / "Alpha" / "report.pdf"
    result = resolve_placement(
        file_path=file_path,
        target_type="project",
        target_name="Alpha",
        vault_cfg=vault_cfg,
    )
    assert result.sibling_dir == result.final_dir / vault_cfg.summaries_subdir

    # Editable (root destination)
    file_path2 = vault_cfg.projects_path / "Alpha" / "report.docx"
    result2 = resolve_placement(
        file_path=file_path2,
        target_type="project",
        target_name="Alpha",
        vault_cfg=vault_cfg,
    )
    assert result2.sibling_dir == result2.final_dir / vault_cfg.summaries_subdir

    # Editable pulled from attachment
    file_path3 = (
        vault_cfg.projects_path / "Alpha" / vault_cfg.attachment_dir / "budget.xlsx"
    )
    result3 = resolve_placement(
        file_path=file_path3,
        target_type="project",
        target_name="Alpha",
        vault_cfg=vault_cfg,
    )
    assert result3.sibling_dir == result3.final_dir / vault_cfg.summaries_subdir


# ============================================================================
# Test 9: No filesystem side effects
# ============================================================================

def test_no_filesystem_side_effects(tmp_path: Path):
    """Calling resolve_placement against a tmp_path with no directories creates nothing."""
    from core.config import VaultConfig
    from vault.paths import resolve_placement

    vault_root = tmp_path / "empty_vault"
    cfg = VaultConfig(root=vault_root)

    file_path = vault_root / "Projects" / "Alpha" / "report.pdf"
    resolve_placement(
        file_path=file_path,
        target_type="project",
        target_name="Alpha",
        vault_cfg=cfg,
    )

    # No directories should have been created
    assert not vault_root.exists()


# ============================================================================
# Test 10: Custom no_edit_extensions
# ============================================================================

def test_custom_no_edit_extensions(tmp_path: Path):
    """With no_edit_extensions=[".xlsx"], .xlsx routes to attachment, .docx to root."""
    from core.config import VaultConfig
    from vault.paths import resolve_placement

    vault_root = tmp_path / "vault"
    cfg = VaultConfig(
        root=vault_root,
        no_edit_extensions=[".xlsx"],  # only xlsx is no-edit
    )

    # .xlsx (no-edit per custom config) → attachment
    xlsx_path = vault_root / "Projects" / "Alpha" / "budget.xlsx"
    result_xlsx = resolve_placement(
        file_path=xlsx_path,
        target_type="project",
        target_name="Alpha",
        vault_cfg=cfg,
    )
    assert result_xlsx.final_dir == vault_root / "Projects" / "Alpha" / cfg.attachment_dir
    assert result_xlsx.needs_move is True

    # .docx (editable — not in custom no_edit list) → root
    docx_path = vault_root / "Projects" / "Alpha" / "report.docx"
    result_docx = resolve_placement(
        file_path=docx_path,
        target_type="project",
        target_name="Alpha",
        vault_cfg=cfg,
    )
    assert result_docx.final_dir == vault_root / "Projects" / "Alpha"
    assert result_docx.needs_move is False
