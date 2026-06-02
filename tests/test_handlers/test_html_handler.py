"""Tests for HtmlHandler — HTML text extraction."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.result import Failure, Success
from handlers.html_handler import HtmlHandler


@pytest.fixture
def html_path(tmp_path: Path) -> Path:
    """Basic HTML page with paragraph text and a script tag."""
    path = tmp_path / "page.html"
    path.write_text(
        "<html><body><p>Hello world</p><script>alert('x')</script></body></html>",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def htm_path(tmp_path: Path) -> Path:
    """.htm extension variant."""
    path = tmp_path / "page.htm"
    path.write_text("<html><body><p>HTM file content</p></body></html>", encoding="utf-8")
    return path


@pytest.fixture
def empty_html_path(tmp_path: Path) -> Path:
    """HTML with no extractable body text."""
    path = tmp_path / "empty.html"
    path.write_text("<html><head><title>Only head</title></head><body></body></html>", encoding="utf-8")
    return path


@pytest.fixture
def rich_html_path(tmp_path: Path) -> Path:
    """HTML with style/nav/footer tags that should be stripped."""
    path = tmp_path / "rich.html"
    path.write_text(
        """<html>
        <head><style>body { color: red; }</style></head>
        <body>
          <nav>Nav link</nav>
          <p>Main article text</p>
          <footer>Footer info</footer>
        </body>
        </html>""",
        encoding="utf-8",
    )
    return path


class TestHtmlHandlerCanHandle:
    def test_lowercase_html(self) -> None:
        assert HtmlHandler().can_handle(Path("file.html")) is True

    def test_uppercase_html(self) -> None:
        assert HtmlHandler().can_handle(Path("file.HTML")) is True

    def test_lowercase_htm(self) -> None:
        assert HtmlHandler().can_handle(Path("file.htm")) is True

    def test_uppercase_htm(self) -> None:
        assert HtmlHandler().can_handle(Path("file.HTM")) is True

    def test_txt_not_handled(self) -> None:
        assert HtmlHandler().can_handle(Path("file.txt")) is False

    def test_xml_not_handled(self) -> None:
        assert HtmlHandler().can_handle(Path("file.xml")) is False


class TestHtmlHandlerExtract:
    def test_valid_html_returns_success(self, html_path: Path) -> None:
        result = HtmlHandler().extract(html_path)
        assert isinstance(result, Success)

    def test_paragraph_text_present(self, html_path: Path) -> None:
        result = HtmlHandler().extract(html_path)
        assert isinstance(result, Success)
        assert "Hello world" in result.value.text

    def test_script_content_stripped(self, html_path: Path) -> None:
        result = HtmlHandler().extract(html_path)
        assert isinstance(result, Success)
        assert "alert" not in result.value.text

    def test_style_and_nav_and_footer_stripped(self, rich_html_path: Path) -> None:
        result = HtmlHandler().extract(rich_html_path)
        assert isinstance(result, Success)
        assert "Main article text" in result.value.text
        assert "color: red" not in result.value.text
        assert "Nav link" not in result.value.text
        assert "Footer info" not in result.value.text

    def test_htm_extension_handled(self, htm_path: Path) -> None:
        result = HtmlHandler().extract(htm_path)
        assert isinstance(result, Success)
        assert "HTM file content" in result.value.text

    def test_is_md_false(self, html_path: Path) -> None:
        result = HtmlHandler().extract(html_path)
        assert isinstance(result, Success)
        assert result.value.is_md is False

    def test_source_path(self, html_path: Path) -> None:
        result = HtmlHandler().extract(html_path)
        assert isinstance(result, Success)
        assert result.value.source_path == html_path

    def test_empty_html_returns_failure(self, empty_html_path: Path) -> None:
        result = HtmlHandler().extract(empty_html_path)
        assert isinstance(result, Failure)
        assert result.recoverable is False

    def test_nonexistent_path_returns_failure(self, tmp_path: Path) -> None:
        result = HtmlHandler().extract(tmp_path / "ghost.html")
        assert isinstance(result, Failure)
        assert result.recoverable is False
