"""Markdown handler — extracts body text from .md drops.

Uses vault.reader.read_note to parse the file, which strips YAML frontmatter
and normalises trailing newlines. The handler itself adds no parsing logic.
"""
from pathlib import Path

import structlog

from handlers.base import BaseHandler, RawContent
from handlers.registry import HandlerRegistry
from core.result import Failure, Result, Success
from vault.reader import read_note

__all__ = ["MarkdownHandler"]

logger = structlog.get_logger(__name__)


@HandlerRegistry.register
class MarkdownHandler(BaseHandler):
    """Handles .md (Markdown) files dropped into the vault inbox.

    Delegates all parsing to vault.reader.read_note so frontmatter stripping
    and content hashing stay in one place.
    """

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".md"

    def extract(self, path: Path) -> Result[RawContent]:
        """Extract body text from a Markdown file.

        Returns:
            Success(RawContent) with body-only text, is_md=True.
            Failure(recoverable=False) if the file is missing or unparseable.
            Underlying read_note errors are re-wrapped to force
            recoverable=False — a missing or malformed .md drop will not
            recover on retry.
        """
        match read_note(path):
            case Failure() as f:
                logger.warning(
                    "md.extract.failed",
                    path=str(path),
                    error=f.error,
                    upstream_recoverable=f.recoverable,
                )
                return Failure(
                    error=f.error,
                    recoverable=False,
                    context={
                        **(f.context or {}),
                        "handler": "markdown",
                        "path": str(path),
                    },
                )
            case Success(value=note):
                logger.info(
                    "md.extract.ok", path=str(path), chars=len(note.content)
                )
                return Success(
                    RawContent(
                        text=note.content,
                        source_path=path,
                        is_md=True,
                    )
                )
