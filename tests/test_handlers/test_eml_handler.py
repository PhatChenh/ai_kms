"""Tests for EmlHandler — RFC 2822 email text extraction."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.result import Failure, Success
from handlers.eml_handler import EmlHandler

_SIMPLE_EML = (
    "From: alice@example.com\r\n"
    "To: bob@example.com\r\n"
    "Subject: Test Subject\r\n"
    "Date: Mon, 01 Jan 2026 09:00:00 +0000\r\n"
    "\r\n"
    "Hello Bob, this is the body.\r\n"
)

_MULTIPART_EML = (
    "From: alice@example.com\r\n"
    "To: bob@example.com\r\n"
    "Subject: Multipart Test\r\n"
    "MIME-Version: 1.0\r\n"
    'Content-Type: multipart/alternative; boundary="boundary42"\r\n'
    "\r\n"
    "--boundary42\r\n"
    "Content-Type: text/plain\r\n"
    "\r\n"
    "Plain text part\r\n"
    "--boundary42\r\n"
    "Content-Type: text/html\r\n"
    "\r\n"
    "<p>HTML part</p>\r\n"
    "--boundary42--\r\n"
)


@pytest.fixture
def eml_path(tmp_path: Path) -> Path:
    path = tmp_path / "simple.eml"
    path.write_text(_SIMPLE_EML, encoding="utf-8")
    return path


@pytest.fixture
def multipart_eml_path(tmp_path: Path) -> Path:
    path = tmp_path / "multipart.eml"
    path.write_text(_MULTIPART_EML, encoding="utf-8")
    return path


class TestEmlHandlerCanHandle:
    def test_lowercase_eml(self) -> None:
        assert EmlHandler().can_handle(Path("file.eml")) is True

    def test_uppercase_eml(self) -> None:
        assert EmlHandler().can_handle(Path("file.EML")) is True

    def test_msg_not_handled(self) -> None:
        assert EmlHandler().can_handle(Path("file.msg")) is False

    def test_txt_not_handled(self) -> None:
        assert EmlHandler().can_handle(Path("file.txt")) is False


class TestEmlHandlerExtract:
    def test_simple_eml_returns_success(self, eml_path: Path) -> None:
        result = EmlHandler().extract(eml_path)
        assert isinstance(result, Success)

    def test_from_header_present(self, eml_path: Path) -> None:
        result = EmlHandler().extract(eml_path)
        assert isinstance(result, Success)
        assert "alice@example.com" in result.value.text

    def test_subject_header_present(self, eml_path: Path) -> None:
        result = EmlHandler().extract(eml_path)
        assert isinstance(result, Success)
        assert "Test Subject" in result.value.text

    def test_body_present(self, eml_path: Path) -> None:
        result = EmlHandler().extract(eml_path)
        assert isinstance(result, Success)
        assert "Hello Bob" in result.value.text

    def test_is_md_false(self, eml_path: Path) -> None:
        result = EmlHandler().extract(eml_path)
        assert isinstance(result, Success)
        assert result.value.is_md is False

    def test_source_path(self, eml_path: Path) -> None:
        result = EmlHandler().extract(eml_path)
        assert isinstance(result, Success)
        assert result.value.source_path == eml_path

    def test_multipart_only_plain_text_extracted(self, multipart_eml_path: Path) -> None:
        result = EmlHandler().extract(multipart_eml_path)
        assert isinstance(result, Success)
        assert "Plain text part" in result.value.text
        # HTML tags from the HTML part should not appear
        assert "<p>" not in result.value.text

    def test_nonexistent_path_returns_failure(self, tmp_path: Path) -> None:
        result = EmlHandler().extract(tmp_path / "ghost.eml")
        assert isinstance(result, Failure)
        assert result.recoverable is False
