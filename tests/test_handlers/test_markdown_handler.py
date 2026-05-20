"""Tests for handlers/markdown_handler.py — MarkdownHandler end-to-end."""
from pathlib import Path

import pytest

from handlers.markdown_handler import MarkdownHandler
from handlers.base import RawContent
from core.result import Failure, Success


MD_CONTENT = """\
---
title: Test Note
tags: [foo, bar]
---

This is the body text.
Second line.
"""

MD_BODY = "This is the body text.\nSecond line."


@pytest.fixture
def md_file(tmp_path: Path) -> Path:
    p = tmp_path / "note.md"
    p.write_text(MD_CONTENT, encoding="utf-8")
    return p


def test_extract_returns_success_with_raw_content(md_file: Path):
    result = MarkdownHandler().extract(md_file)
    assert isinstance(result, Success)
    assert isinstance(result.value, RawContent)


def test_extract_body_text_matches_known_body(md_file: Path):
    result = MarkdownHandler().extract(md_file)
    assert isinstance(result, Success)
    assert result.value.text == MD_BODY


def test_extract_strips_frontmatter_from_text(md_file: Path):
    result = MarkdownHandler().extract(md_file)
    assert isinstance(result, Success)
    assert "---" not in result.value.text
    assert "title:" not in result.value.text
    assert "tags:" not in result.value.text


def test_extract_sets_is_md_true(md_file: Path):
    result = MarkdownHandler().extract(md_file)
    assert isinstance(result, Success)
    assert result.value.is_md is True


def test_extract_sets_source_path(md_file: Path):
    result = MarkdownHandler().extract(md_file)
    assert isinstance(result, Success)
    assert result.value.source_path == md_file


def test_can_handle_md_lowercase():
    assert MarkdownHandler().can_handle(Path("note.md")) is True


def test_can_handle_rejects_uppercase_pdf():
    assert MarkdownHandler().can_handle(Path("note.PDF")) is False


def test_can_handle_rejects_pdf():
    assert MarkdownHandler().can_handle(Path("doc.pdf")) is False


def test_extract_missing_file_returns_failure(tmp_path: Path):
    missing = tmp_path / "nonexistent.md"
    result = MarkdownHandler().extract(missing)
    assert isinstance(result, Failure)
    assert result.recoverable is False
