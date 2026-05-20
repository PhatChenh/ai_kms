"""Tests for handlers/pdf_handler.py — PdfHandler end-to-end."""
from pathlib import Path

import pypdf
import pytest

from handlers.pdf_handler import PdfHandler
from handlers.base import RawContent
from core.result import Failure, Success

FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "sample_text.pdf"


@pytest.fixture
def blank_pdf(tmp_path: Path) -> Path:
    """A PDF with a blank page — pypdf yields empty string from extract_text()."""
    path = tmp_path / "blank.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        writer.write(f)
    return path


def test_text_pdf_returns_success_with_non_empty_text():
    result = PdfHandler().extract(FIXTURE_PDF)
    assert isinstance(result, Success)
    assert isinstance(result.value, RawContent)
    assert len(result.value.text) > 0


def test_text_pdf_sets_is_md_false():
    result = PdfHandler().extract(FIXTURE_PDF)
    assert isinstance(result, Success)
    assert result.value.is_md is False


def test_text_pdf_sets_source_path():
    result = PdfHandler().extract(FIXTURE_PDF)
    assert isinstance(result, Success)
    assert result.value.source_path == FIXTURE_PDF


def test_text_pdf_contains_expected_text():
    result = PdfHandler().extract(FIXTURE_PDF)
    assert isinstance(result, Success)
    assert "Sample text fixture" in result.value.text


def test_blank_pdf_returns_failure(blank_pdf: Path):
    result = PdfHandler().extract(blank_pdf)
    assert isinstance(result, Failure)
    assert result.recoverable is False


def test_nonexistent_path_returns_failure(tmp_path: Path):
    result = PdfHandler().extract(tmp_path / "missing.pdf")
    assert isinstance(result, Failure)
    assert result.recoverable is False


def test_can_handle_pdf_lowercase():
    assert PdfHandler().can_handle(Path("report.pdf")) is True


def test_can_handle_pdf_uppercase():
    assert PdfHandler().can_handle(Path("REPORT.PDF")) is True


def test_can_handle_rejects_md():
    assert PdfHandler().can_handle(Path("note.md")) is False
