"""Tests for _collect_folder_files — Phase 2 folder-capture exclusion of
attachment/ and .summaries/ subdirectories.

All tests call _collect_folder_files directly.  No CONFIG singleton — each
test constructs VaultConfig(root=tmp_path) and passes it explicitly.
"""

from __future__ import annotations

from pathlib import Path

from core.config import VaultConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file(base: Path, *parts: str, content: str = "") -> Path:
    """Create parent dirs and write a file; return its Path."""
    path = base.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content or f"content of {path.name}", encoding="utf-8")
    return path


def _names(files: list[Path]) -> list[str]:
    """Convenience: sorted filenames from a files list."""
    return sorted(f.name for f in files)


# ---------------------------------------------------------------------------
# TestCollectFolderFiles
# ---------------------------------------------------------------------------


class TestCollectFolderFiles:
    """Nine tests for _collect_folder_files attachment/.summaries/ exclusion."""

    def test_skip_attachment_subfolder(self, tmp_path: Path):
        """doc.docx included; attachment/report.pdf excluded."""
        from pipelines.capture import _collect_folder_files

        vault = tmp_path / "vault"
        dropped = vault / "inbox" / "dropped"
        vault_cfg = VaultConfig(root=vault)

        _make_file(dropped, "doc.docx")
        _make_file(dropped, "attachment", "report.pdf")

        result = _collect_folder_files(dropped, vault_cfg)

        assert _names(result) == ["doc.docx"]

    def test_skip_summaries_subfolder(self, tmp_path: Path):
        """doc.docx included; .summaries/doc.docx.md excluded."""
        from pipelines.capture import _collect_folder_files

        vault = tmp_path / "vault"
        dropped = vault / "inbox" / "dropped"
        vault_cfg = VaultConfig(root=vault)

        _make_file(dropped, "doc.docx")
        _make_file(dropped, ".summaries", "doc.docx.md")

        result = _collect_folder_files(dropped, vault_cfg)

        assert _names(result) == ["doc.docx"]

    def test_skip_nested_attachment_at_depth(self, tmp_path: Path):
        """Attachment at depth > 1 also skipped; sibling at same depth returned."""
        from pipelines.capture import _collect_folder_files

        vault = tmp_path / "vault"
        dropped = vault / "inbox" / "dropped"
        vault_cfg = VaultConfig(root=vault)

        _make_file(dropped, "deep", "nested", "attachment", "report.pdf")
        _make_file(dropped, "deep", "nested", "readme.txt")

        result = _collect_folder_files(dropped, vault_cfg)

        assert _names(result) == ["readme.txt"]

    def test_skip_nested_summaries_under_attachment(self, tmp_path: Path):
        """.summaries/ under attachment/ also excluded; top-level doc returned."""
        from pipelines.capture import _collect_folder_files

        vault = tmp_path / "vault"
        dropped = vault / "inbox" / "dropped"
        vault_cfg = VaultConfig(root=vault)

        _make_file(dropped, "doc.docx")
        _make_file(dropped, "attachment", ".summaries", "report.md")

        result = _collect_folder_files(dropped, vault_cfg)

        assert _names(result) == ["doc.docx"]

    def test_plain_folder_no_managed_dirs_returns_all(self, tmp_path: Path):
        """No attachment/ or .summaries/ — all three files returned (regression guard)."""
        from pipelines.capture import _collect_folder_files

        vault = tmp_path / "vault"
        dropped = vault / "inbox" / "dropped"
        vault_cfg = VaultConfig(root=vault)

        _make_file(dropped, "a.txt")
        _make_file(dropped, "b.docx")
        _make_file(dropped, "c.pdf")

        result = _collect_folder_files(dropped, vault_cfg)

        assert _names(result) == ["a.txt", "b.docx", "c.pdf"]

    def test_empty_result_when_only_attachment_contents(self, tmp_path: Path):
        """Folder with only attachment/ contents → empty list."""
        from pipelines.capture import _collect_folder_files

        vault = tmp_path / "vault"
        dropped = vault / "inbox" / "dropped"
        vault_cfg = VaultConfig(root=vault)

        _make_file(dropped, "attachment", "report.pdf")
        _make_file(dropped, "attachment", "notes.txt")

        result = _collect_folder_files(dropped, vault_cfg)

        assert result == []

    def test_config_sourced_names_custom_attachment_dir(self, tmp_path: Path):
        """Custom attachment_dir="binaries" → binaries/ skipped; attachment/ NOT skipped."""
        from pipelines.capture import _collect_folder_files

        vault = tmp_path / "vault"
        dropped = vault / "inbox" / "dropped"
        vault_cfg = VaultConfig(root=vault, attachment_dir="binaries")

        _make_file(dropped, "binaries", "should-skip.pdf")
        _make_file(dropped, "attachment", "should-not-skip.docx")

        result = _collect_folder_files(dropped, vault_cfg)

        assert _names(result) == ["should-not-skip.docx"]

    def test_dotfiles_still_skipped(self, tmp_path: Path):
        """Dotfile (name starts with '.') at root of dropped folder still excluded."""
        from pipelines.capture import _collect_folder_files

        vault = tmp_path / "vault"
        dropped = vault / "inbox" / "dropped"
        vault_cfg = VaultConfig(root=vault)

        _make_file(dropped, "doc.docx")
        # Dotfile at root of dropped folder
        _make_file(dropped, ".hidden_file")
        # Dotfile deeper in the tree
        _make_file(dropped, "subdir", ".DS_Store")

        result = _collect_folder_files(dropped, vault_cfg)

        assert _names(result) == ["doc.docx"]

    def test_ignore_dirs_members_still_skipped(self, tmp_path: Path):
        """Files under IGNORE_DIRS members (.git, .obsidian, etc.) still excluded."""
        from pipelines.capture import _collect_folder_files

        vault = tmp_path / "vault"
        dropped = vault / "inbox" / "dropped"
        vault_cfg = VaultConfig(root=vault)

        _make_file(dropped, "doc.docx")
        _make_file(dropped, ".git", "config")
        _make_file(dropped, ".obsidian", "workspace.json")
        _make_file(dropped, "node_modules", "pkg", "index.js")

        result = _collect_folder_files(dropped, vault_cfg)

        assert _names(result) == ["doc.docx"]
