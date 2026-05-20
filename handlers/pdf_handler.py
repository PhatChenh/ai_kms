"""PDF handler — extracts text from .pdf drops.

Uses pypdf to iterate pages and extract text. Image-only PDFs (no text layer)
yield empty strings from extract_text(); these are returned as Failure because
the LLM summarise stage has nothing to work with — OCR is out of scope.
"""
from pathlib import Path

import pypdf

from handlers.base import BaseHandler, RawContent
from handlers.registry import HandlerRegistry
from core.result import Failure, Result, Success

__all__ = ["PdfHandler"]


@HandlerRegistry.register
class PdfHandler(BaseHandler):
    """Handles .pdf files dropped into the vault inbox.

    Returns Failure for image-only PDFs (no extractable text layer) and for
    any file that cannot be opened or parsed by pypdf.
    """

    def can_handle(self, path: Path) -> bool:
        """Return True for .pdf files (case-insensitive).

        Args:
            path: Path to the dropped file.

        Returns:
            True if the file extension is .pdf regardless of case.
        """
        return path.suffix.lower() == ".pdf"

    def extract(self, path: Path) -> Result[RawContent]:
        """Extract plain text from a PDF file.

        Args:
            path: Path to the .pdf file.

        Returns:
            Success(RawContent) with extracted text joined across pages, is_md=False.
            Failure(recoverable=False) if the PDF has no text layer (image-only/blank)
            or if pypdf raises any exception (corrupt, missing file, etc.).
        """
        try:
            reader = pypdf.PdfReader(str(path))
            text = "\n".join(
                page.extract_text() or "" for page in reader.pages
            ).strip()
        except Exception as exc:
            return Failure(
                error=f"PDF read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )

        if not text:
            return Failure(
                error="PDF contains no extractable text (image-only or empty)",
                recoverable=False,
                context={"path": str(path)},
            )

        return Success(RawContent(text=text, source_path=path, is_md=False))
