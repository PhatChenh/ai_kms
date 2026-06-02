"""Image handlers — PNG and JPG stubs.

Image extraction requires a vision-capable LLM or OCR (Tesseract).
Neither is in scope. Both handlers register to produce a clear Failure
instead of a confusing 'no handler' error.
"""
from __future__ import annotations

from pathlib import Path

from core.result import Failure, Result
from handlers.base import BaseHandler, RawContent
from handlers.registry import HandlerRegistry


@HandlerRegistry.register
class PngHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".png"

    def extract(self, path: Path) -> Result[RawContent]:
        return Failure(
            error="image extraction requires a vision-capable LLM — not yet implemented",
            recoverable=False,
            context={"path": str(path)},
        )


@HandlerRegistry.register
class JpgHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in (".jpg", ".jpeg")

    def extract(self, path: Path) -> Result[RawContent]:
        return Failure(
            error="image extraction requires a vision-capable LLM — not yet implemented",
            recoverable=False,
            context={"path": str(path)},
        )
