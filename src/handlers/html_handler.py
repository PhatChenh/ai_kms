"""HTML handler — extracts readable text from .html/.htm files via BeautifulSoup.

Files larger than CONFIG.main.handlers.max_file_size_bytes are rejected before
any parse work to prevent a single large drop from OOMing the capture process.
"""
from __future__ import annotations

from pathlib import Path

import structlog

from core.result import Failure, Result, Success
from handlers.base import BaseHandler, RawContent
from handlers.registry import HandlerRegistry

logger = structlog.get_logger(__name__)


@HandlerRegistry.register
class HtmlHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in (".html", ".htm")

    def extract(self, path: Path, *, max_file_size_bytes: int | None = None) -> Result[RawContent]:
        if max_file_size_bytes is None:
            # Lazy import — see handlers/pdf_handler.py for rationale.
            from core.config import CONFIG

            max_bytes = CONFIG.main.handlers.max_file_size_bytes
        else:
            max_bytes = max_file_size_bytes

        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.warning("html.stat.failed", path=str(path), error=str(exc))
            return Failure(
                error=f"HTML stat failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )

        if size > max_bytes:
            logger.warning(
                "html.too_large", path=str(path), size=size, limit=max_bytes
            )
            return Failure(
                error=f"HTML too large: {size} > {max_bytes} bytes",
                recoverable=False,
                context={"path": str(path), "size": size, "limit": max_bytes},
            )

        try:
            from bs4 import BeautifulSoup

            html = path.read_text(encoding="utf-8", errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "head", "nav", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
        except Exception as exc:
            return Failure(
                error=f"HTML read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        if not text:
            return Failure(
                error="HTML contains no extractable text",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))
