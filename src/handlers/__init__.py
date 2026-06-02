# Import order = registration order. First match wins per suffix.
from handlers.base import BaseHandler, RawContent
from handlers.registry import HandlerRegistry
from handlers.markdown_handler import MarkdownHandler
from handlers.pdf_handler import PdfHandler
from handlers.docx_handler import DocxHandler
from handlers.xlsx_handler import XlsxHandler
from handlers.csv_handler import CsvHandler
from handlers.pptx_handler import PptxHandler
from handlers.html_handler import HtmlHandler
from handlers.eml_handler import EmlHandler
from handlers.msg_handler import MsgHandler
from handlers.image_handler import PngHandler, JpgHandler
from handlers.url_fetcher import detect_urls, fetch_url_content

__all__ = [
    "BaseHandler",
    "RawContent",
    "HandlerRegistry",
    "MarkdownHandler",
    "PdfHandler",
    "DocxHandler",
    "XlsxHandler",
    "CsvHandler",
    "PptxHandler",
    "HtmlHandler",
    "EmlHandler",
    "MsgHandler",
    "PngHandler",
    "JpgHandler",
    "detect_urls",
    "fetch_url_content",
]
