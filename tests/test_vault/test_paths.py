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
    briefings_for,
    briefings_today,
    documentation,
    domain_dir,
    domain_notes,
    project_dir,
    project_materials,
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
