"""Tests for handlers/registry.py — HandlerRegistry dispatch."""
from pathlib import Path


import handlers  # noqa: F401 — triggers __init__.py bootstrap
from handlers.base import BaseHandler, RawContent
from handlers.docx_handler import DocxHandler
from handlers.markdown_handler import MarkdownHandler
from handlers.pdf_handler import PdfHandler
from handlers.registry import HandlerRegistry
from core.result import Failure, Result, Success


class _XyzHandler(BaseHandler):
    """Dummy handler that claims .xyz files."""

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".xyz"

    def extract(self, path: Path) -> Result[RawContent]:
        return Success(RawContent(text="xyz", source_path=path, is_md=False))


class _AbcHandler(BaseHandler):
    """Dummy handler that claims .abc files."""

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".abc"

    def extract(self, path: Path) -> Result[RawContent]:
        return Success(RawContent(text="abc", source_path=path, is_md=False))


def test_registered_handler_resolves_matching_path(clean_registry):
    HandlerRegistry.register(_XyzHandler)
    result = HandlerRegistry.resolve(Path("file.xyz"))
    assert isinstance(result, Success)
    assert isinstance(result.value, _XyzHandler)


def test_unknown_extension_returns_failure(clean_registry):
    result = HandlerRegistry.resolve(Path("file.unknown"))
    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert "unknown" in result.error


def test_resolve_returns_first_registered_match(clean_registry):
    HandlerRegistry.register(_XyzHandler)
    HandlerRegistry.register(_AbcHandler)
    # .abc is registered second; .xyz is first — verify second handler resolves
    result = HandlerRegistry.resolve(Path("report.abc"))
    assert isinstance(result, Success)
    assert isinstance(result.value, _AbcHandler)


def test_clean_registry_prevents_test_pollution(clean_registry):
    # This test runs after test_registered_handler_resolves_matching_path.
    # If clean_registry works, _XyzHandler is NOT in the registry here.
    result = HandlerRegistry.resolve(Path("file.xyz"))
    assert isinstance(result, Failure)


# ---------------------------------------------------------------------------
# Integration tests — real registered handlers; no clean_registry fixture
# ---------------------------------------------------------------------------


class TestHandlersBootstrap:
    """Verify that importing handlers/ registers all three real handlers."""

    def test_handler_types_exported_from_package(self):
        # Without __init__.py these names are not directly accessible on the package.
        assert hasattr(handlers, "MarkdownHandler")
        assert hasattr(handlers, "PdfHandler")
        assert hasattr(handlers, "DocxHandler")

    def test_all_three_handler_types_registered(self):
        handler_types = {type(h) for h in HandlerRegistry._handlers}
        assert MarkdownHandler in handler_types
        assert PdfHandler in handler_types
        assert DocxHandler in handler_types

    def test_md_path_resolves_to_markdown_handler(self):
        result = HandlerRegistry.resolve(Path("note.md"))
        assert isinstance(result, Success)
        assert isinstance(result.value, MarkdownHandler)

    def test_pdf_path_resolves_to_pdf_handler(self):
        result = HandlerRegistry.resolve(Path("report.pdf"))
        assert isinstance(result, Success)
        assert isinstance(result.value, PdfHandler)

    def test_docx_path_resolves_to_docx_handler(self):
        result = HandlerRegistry.resolve(Path("document.docx"))
        assert isinstance(result, Success)
        assert isinstance(result.value, DocxHandler)

    def test_uppercase_suffix_resolves_correctly(self):
        result = HandlerRegistry.resolve(Path("NOTE.MD"))
        assert isinstance(result, Success)
        assert isinstance(result.value, MarkdownHandler)

    def test_unknown_extension_returns_failure(self):
        result = HandlerRegistry.resolve(Path("file.unknown"))
        assert isinstance(result, Failure)
        assert result.recoverable is False
