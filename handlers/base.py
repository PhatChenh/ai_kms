"""Shared types for the handlers subsystem.

RawContent is the output of every handler's extract() call.
BaseHandler is the ABC that all concrete handlers implement.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from core.result import Result

__all__ = ["RawContent", "BaseHandler"]


@dataclass(frozen=True)
class RawContent:
    """Immutable extraction result passed to the capture pipeline.

    Args:
        text:        Extracted plain text (body only, no frontmatter).
        source_path: Absolute path to the original dropped file.
        is_md:       True if source was a Markdown file (affects write-back path).
    """

    text: str
    source_path: Path
    is_md: bool


class BaseHandler(ABC):
    """Contract every handler must satisfy.

    Handlers are registered with HandlerRegistry via the @register decorator.
    The registry calls can_handle() to find the first matching handler, then
    extract() to produce a RawContent for the pipeline.
    """

    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        """Return True if this handler can process the given file.

        Args:
            path: Path to the dropped file.

        Returns:
            True if this handler claims the file.
        """

    @abstractmethod
    def extract(self, path: Path) -> Result[RawContent]:
        """Extract plain text from the file at path.

        Args:
            path: Path to the dropped file.

        Returns:
            Success(RawContent) on success.
            Failure(recoverable=False) on parse error or unreadable file.
        """
