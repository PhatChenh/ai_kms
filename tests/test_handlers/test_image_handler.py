"""Tests for PngHandler and JpgHandler — image stub handlers."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.result import Failure
from handlers.image_handler import JpgHandler, PngHandler


@pytest.fixture
def png_path(tmp_path: Path) -> Path:
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes
    return path


@pytest.fixture
def jpg_path(tmp_path: Path) -> Path:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"\xff\xd8\xff")  # JPEG magic bytes
    return path


@pytest.fixture
def jpeg_path(tmp_path: Path) -> Path:
    path = tmp_path / "photo.jpeg"
    path.write_bytes(b"\xff\xd8\xff")
    return path


class TestPngHandlerCanHandle:
    def test_lowercase_png(self) -> None:
        assert PngHandler().can_handle(Path("file.png")) is True

    def test_uppercase_png(self) -> None:
        assert PngHandler().can_handle(Path("file.PNG")) is True

    def test_jpg_not_handled_by_png(self) -> None:
        assert PngHandler().can_handle(Path("file.jpg")) is False

    def test_gif_not_handled(self) -> None:
        assert PngHandler().can_handle(Path("file.gif")) is False


class TestJpgHandlerCanHandle:
    def test_lowercase_jpg(self) -> None:
        assert JpgHandler().can_handle(Path("file.jpg")) is True

    def test_uppercase_jpg(self) -> None:
        assert JpgHandler().can_handle(Path("file.JPG")) is True

    def test_lowercase_jpeg(self) -> None:
        assert JpgHandler().can_handle(Path("file.jpeg")) is True

    def test_uppercase_jpeg(self) -> None:
        assert JpgHandler().can_handle(Path("file.JPEG")) is True

    def test_png_not_handled_by_jpg(self) -> None:
        assert JpgHandler().can_handle(Path("file.png")) is False

    def test_gif_not_handled(self) -> None:
        assert JpgHandler().can_handle(Path("file.gif")) is False


class TestPngHandlerExtract:
    def test_always_returns_failure(self, png_path: Path) -> None:
        result = PngHandler().extract(png_path)
        assert isinstance(result, Failure)

    def test_failure_not_recoverable(self, png_path: Path) -> None:
        result = PngHandler().extract(png_path)
        assert isinstance(result, Failure)
        assert result.recoverable is False

    def test_failure_mentions_vision(self, png_path: Path) -> None:
        result = PngHandler().extract(png_path)
        assert isinstance(result, Failure)
        assert "vision-capable" in result.error

    def test_explicit_max_size_parameter_accepted(self, png_path: Path) -> None:
        """PngHandler accepts max_file_size_bytes even though it ignores it."""
        result = PngHandler().extract(png_path, max_file_size_bytes=50_000_000)
        assert isinstance(result, Failure)
        assert "vision-capable" in result.error


class TestJpgHandlerExtract:
    def test_jpg_always_returns_failure(self, jpg_path: Path) -> None:
        result = JpgHandler().extract(jpg_path)
        assert isinstance(result, Failure)

    def test_jpeg_always_returns_failure(self, jpeg_path: Path) -> None:
        result = JpgHandler().extract(jpeg_path)
        assert isinstance(result, Failure)

    def test_failure_not_recoverable(self, jpg_path: Path) -> None:
        result = JpgHandler().extract(jpg_path)
        assert isinstance(result, Failure)
        assert result.recoverable is False

    def test_failure_mentions_vision(self, jpg_path: Path) -> None:
        result = JpgHandler().extract(jpg_path)
        assert isinstance(result, Failure)
        assert "vision-capable" in result.error

    def test_explicit_max_size_parameter_accepted(self, jpg_path: Path) -> None:
        """JpgHandler accepts max_file_size_bytes even though it ignores it."""
        result = JpgHandler().extract(jpg_path, max_file_size_bytes=50_000_000)
        assert isinstance(result, Failure)
        assert "vision-capable" in result.error
