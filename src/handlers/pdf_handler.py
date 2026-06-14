"""PDF handler — extracts text from .pdf drops.

Uses pypdf to iterate pages and extract text. Image-only PDFs (no text layer)
yield empty strings from extract_text(); these are returned as Failure because
the LLM summarise stage has nothing to work with — OCR is out of scope.

Files larger than CONFIG.main.handlers.max_file_size_bytes are rejected before
any parse work, to prevent a single bad drop from OOMing the capture process.
"""
from pathlib import Path

import pypdf
import structlog

from handlers.base import BaseHandler, RawContent
from handlers.registry import HandlerRegistry
from core.result import Failure, Result, Success

__all__ = ["PdfHandler"]

logger = structlog.get_logger(__name__)


@HandlerRegistry.register
class PdfHandler(BaseHandler):
    """Handles .pdf files dropped into the vault inbox.

    Returns Failure for image-only PDFs (no extractable text layer), files
    exceeding the configured size limit, and any file that pypdf cannot open
    or parse.
    """

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def extract(self, path: Path, *, max_file_size_bytes: int | None = None) -> Result[RawContent]:
        if max_file_size_bytes is None:
            # Lazy import: handlers must not load CONFIG at module scope or unit
            # tests on machines without the vault directory fail at import.
            from core.config import CONFIG

            max_bytes = CONFIG.main.handlers.max_file_size_bytes
        else:
            max_bytes = max_file_size_bytes

        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.warning("pdf.stat.failed", path=str(path), error=str(exc))
            return Failure(
                error=f"PDF stat failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )

        if size > max_bytes:
            logger.warning(
                "pdf.too_large", path=str(path), size=size, limit=max_bytes
            )
            return Failure(
                error=f"PDF too large: {size} > {max_bytes} bytes",
                recoverable=False,
                context={"path": str(path), "size": size, "limit": max_bytes},
            )

        try:
            reader = pypdf.PdfReader(path)
            text = "\n".join(
                page.extract_text() or "" for page in reader.pages
            ).strip()
        except Exception as exc:
            logger.warning("pdf.read.failed", path=str(path), error=str(exc))
            return Failure(
                error=f"PDF read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )

        if not text:
            logger.warning("pdf.empty", path=str(path))
            return Failure(
                error="PDF contains no extractable text (image-only or empty)",
                recoverable=False,
                context={"path": str(path)},
            )

        logger.info(
            "pdf.extract.ok", path=str(path), chars=len(text), bytes=size
        )
        return Success(RawContent(text=text, source_path=path, is_md=False))
