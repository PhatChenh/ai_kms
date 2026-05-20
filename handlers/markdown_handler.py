"""Markdown handler — extracts body text from .md drops.

Uses vault.reader.read_note to parse the file, which strips YAML frontmatter
and normalises trailing newlines. The handler itself adds no parsing logic.
"""
from pathlib import Path

from handlers.base import BaseHandler, RawContent
from handlers.registry import HandlerRegistry
from core.result import Failure, Result, Success
from vault.reader import read_note

__all__ = ["MarkdownHandler"]


@HandlerRegistry.register
class MarkdownHandler(BaseHandler):
    """Handles .md (Markdown) files dropped into the vault inbox.

    Delegates all parsing to vault.reader.read_note so frontmatter stripping
    and content hashing stay in one place.
    """

    def can_handle(self, path: Path) -> bool:
        """Return True for .md files (case-insensitive).

        Args:
            path: Path to the dropped file.

        Returns:
            True if the file extension is .md regardless of case.
        """
        return path.suffix.lower() == ".md"

    def extract(self, path: Path) -> Result[RawContent]:
        """Extract body text from a Markdown file.

        Args:
            path: Path to the .md file.

        Returns:
            Success(RawContent) with body-only text, is_md=True.
            Failure(recoverable=False) if the file is missing or unparseable.
        """
        match read_note(path):
            case Failure() as f:
                return f
            case Success(value=note):
                return Success(
                    RawContent(
                        text=note.content,
                        source_path=path,
                        is_md=True,
                    )
                )
