"""Shared types for the handlers subsystem.

RawContent is the output of every handler's extract() call.
BaseHandler is the ABC that all concrete handlers implement.

Scope:
    BaseHandler + HandlerRegistry dispatch ONLY on filesystem paths
    (files dropped into the vault inbox). The interface takes `Path`
    and nothing else. Non-file sources (web pages, YouTube transcripts,
    future Slack/email/etc.) are NOT BaseHandler implementations and do
    NOT live in the registry.

Why url_fetcher is NOT a UrlHandler:
    `handlers.url_fetcher.fetch_url_content` is intentionally a pipeline
    stage, not a registry handler. A markdown drop is processed as:

        read raw .md → (optional) enhance URL content via fetch_url_content
                     → summarise → classify → store

    The URL-enhance step runs INSIDE the markdown handling pipeline so
    the same code path handles both shapes of input the user actually
    produces:
      - .md note that is just a single link (URL is the content)
      - .md note that contains links interleaved with body text
        (URL content augments the body, doesn't replace it)

    A `UrlHandler` registered in the registry would handle the
    link-only case but would scope-conflict with MarkdownHandler for
    every note containing both prose and URLs — the registry can only
    pick one handler. Keeping URL fetching as an inline pipeline stage
    avoids that conflict and keeps MarkdownHandler the sole owner of
    .md drops.

    If you find yourself wanting a UrlHandler in the registry, read
    this again before adding one.
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
