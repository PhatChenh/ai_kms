"""Tests for handlers/base.py — RawContent dataclass and BaseHandler ABC."""
from pathlib import Path

import pytest

from handlers.base import BaseHandler, RawContent
from core.result import Success


class _StubHandler(BaseHandler):
    """Minimal concrete implementation for testing the ABC contract."""

    def can_handle(self, path: Path) -> bool:
        return path.suffix == ".stub"

    def extract(self, path: Path) -> "Success[RawContent]":
        return Success(RawContent(text="hello", source_path=path, is_md=False))


def test_stub_is_instance_of_base_handler():
    handler = _StubHandler()
    assert isinstance(handler, BaseHandler)


def test_base_handler_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        BaseHandler()  # type: ignore[abstract]


def test_raw_content_is_frozen():
    path = Path("note.md")
    content = RawContent(text="body", source_path=path, is_md=True)
    with pytest.raises((AttributeError, TypeError)):
        content.text = "mutated"  # type: ignore[misc]


def test_raw_content_fields():
    path = Path("doc.pdf")
    content = RawContent(text="extracted", source_path=path, is_md=False)
    assert content.text == "extracted"
    assert content.source_path == path
    assert content.is_md is False


def test_stub_extract_returns_raw_content():
    handler = _StubHandler()
    path = Path("file.stub")
    result = handler.extract(path)
    assert isinstance(result, Success)
    assert isinstance(result.value, RawContent)
    assert result.value.text == "hello"
    assert result.value.source_path == path
    assert result.value.is_md is False


def test_stub_can_handle_matching_suffix():
    handler = _StubHandler()
    assert handler.can_handle(Path("file.stub")) is True


def test_stub_can_handle_non_matching_suffix():
    handler = _StubHandler()
    assert handler.can_handle(Path("file.md")) is False
