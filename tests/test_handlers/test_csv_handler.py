"""Tests for CsvHandler — CSV text extraction."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.result import Failure, Success
from handlers.csv_handler import CsvHandler


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    """Simple CSV with header + two data rows."""
    path = tmp_path / "sample.csv"
    path.write_text("Name,Value\nAlice,1\nBob,2\n", encoding="utf-8")
    return path


@pytest.fixture
def bom_csv_path(tmp_path: Path) -> Path:
    """CSV with UTF-8 BOM prefix."""
    path = tmp_path / "bom.csv"
    path.write_bytes("\xef\xbb\xbfHeader,Count\nFoo,42\n".encode("utf-8"))
    return path


class TestCsvHandlerCanHandle:
    def test_lowercase_csv(self) -> None:
        assert CsvHandler().can_handle(Path("file.csv")) is True

    def test_uppercase_csv(self) -> None:
        assert CsvHandler().can_handle(Path("file.CSV")) is True

    def test_xlsx_not_handled(self) -> None:
        assert CsvHandler().can_handle(Path("file.xlsx")) is False

    def test_txt_not_handled(self) -> None:
        assert CsvHandler().can_handle(Path("file.txt")) is False


class TestCsvHandlerExtract:
    def test_valid_csv_returns_success(self, csv_path: Path) -> None:
        result = CsvHandler().extract(csv_path)
        assert isinstance(result, Success)

    def test_header_row_present(self, csv_path: Path) -> None:
        result = CsvHandler().extract(csv_path)
        assert isinstance(result, Success)
        assert "Name" in result.value.text
        assert "Value" in result.value.text

    def test_data_rows_present(self, csv_path: Path) -> None:
        result = CsvHandler().extract(csv_path)
        assert isinstance(result, Success)
        assert "Alice" in result.value.text
        assert "Bob" in result.value.text

    def test_is_md_false(self, csv_path: Path) -> None:
        result = CsvHandler().extract(csv_path)
        assert isinstance(result, Success)
        assert result.value.is_md is False

    def test_source_path(self, csv_path: Path) -> None:
        result = CsvHandler().extract(csv_path)
        assert isinstance(result, Success)
        assert result.value.source_path == csv_path

    def test_bom_stripped_from_first_cell(self, bom_csv_path: Path) -> None:
        result = CsvHandler().extract(bom_csv_path)
        assert isinstance(result, Success)
        # BOM should not appear as leading character on first cell
        assert not result.value.text.startswith("﻿")
        assert "Header" in result.value.text

    def test_nonexistent_path_returns_failure(self, tmp_path: Path) -> None:
        result = CsvHandler().extract(tmp_path / "ghost.csv")
        assert isinstance(result, Failure)
        assert result.recoverable is False

    def test_empty_csv_returns_success_with_empty_text(self, tmp_path: Path) -> None:
        # Empty CSV (no rows at all) returns Success("") — consistent with
        # DocxHandler behaviour. Empty text is valid input for the LLM
        # summarise stage; it is NOT treated as a failure here.
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")
        result = CsvHandler().extract(path)
        assert isinstance(result, Success)
        assert result.value.text == ""

    def test_file_too_large_returns_failure(self, tmp_path: Path, monkeypatch) -> None:
        import core.config as cfg_module
        from unittest.mock import MagicMock
        from core.config import HandlersConfig

        path = tmp_path / "big.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")

        fake = MagicMock()
        fake.main.handlers = HandlersConfig(max_file_size_bytes=1)
        monkeypatch.setattr(cfg_module, "_CONFIG", fake)

        result = CsvHandler().extract(path)
        assert isinstance(result, Failure)
        assert result.recoverable is False
        assert "too large" in result.error
