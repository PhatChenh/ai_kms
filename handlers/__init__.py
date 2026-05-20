# Import order = registration order. MarkdownHandler is registered first.
from handlers.base import BaseHandler, RawContent
from handlers.registry import HandlerRegistry
from handlers.markdown_handler import MarkdownHandler
from handlers.pdf_handler import PdfHandler
from handlers.docx_handler import DocxHandler
from handlers.url_fetcher import detect_urls, fetch_url_content

__all__ = [
    "BaseHandler",
    "RawContent",
    "HandlerRegistry",
    "MarkdownHandler",
    "PdfHandler",
    "DocxHandler",
    "detect_urls",
    "fetch_url_content",
]
