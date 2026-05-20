"""Handler registry — class-level dispatch from Path to handler.

Resolution order equals registration order. The first registered handler
whose can_handle() returns True wins. MarkdownHandler is registered first
(via handlers/__init__.py import order), so .md files always resolve without
ambiguity.

Usage:
    @HandlerRegistry.register
    class MyHandler(BaseHandler): ...

    result = HandlerRegistry.resolve(Path("note.md"))
"""
from pathlib import Path
from typing import ClassVar

from handlers.base import BaseHandler
from core.result import Failure, Result, Success

__all__ = ["HandlerRegistry"]


class HandlerRegistry:
    """Class-level registry mapping file paths to handler instances.

    Handlers self-register at import time via the @register decorator.
    The registry is a plain class-level list — no metaclass magic required.
    """

    _handlers: ClassVar[list[BaseHandler]] = []

    @classmethod
    def register(cls, handler_class: type[BaseHandler]) -> type[BaseHandler]:
        """Register a handler class and return it (decorator pattern).

        Instantiates handler_class immediately and appends it to _handlers.

        Args:
            handler_class: A concrete subclass of BaseHandler.

        Returns:
            handler_class unchanged, so the decorator is transparent.
        """
        cls._handlers.append(handler_class())
        return handler_class

    @classmethod
    def resolve(cls, path: Path) -> Result[BaseHandler]:
        """Return the first registered handler that claims path.

        Args:
            path: Path to the dropped file.

        Returns:
            Success(handler) — first handler whose can_handle() returns True.
            Failure(recoverable=False) — no handler claims this file extension.
        """
        for handler in cls._handlers:
            if handler.can_handle(path):
                return Success(handler)
        return Failure(
            error=f"no handler for extension '{path.suffix}'",
            recoverable=False,
            context={"path": str(path)},
        )
