"""Tests for MsgHandler — Outlook .msg text extraction (mocked)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.result import Failure, Success
from handlers.msg_handler import MsgHandler


class _FakeMsg:
    """Minimal stand-in for extract_msg.Message."""

    def __init__(self) -> None:
        self.sender = "alice@example.com"
        self.to = "bob@example.com"
        self.subject = "Quarterly Report"
        self.date = "2026-01-15"
        self.body = "Please review the attached report."

    def __enter__(self) -> "_FakeMsg":
        return self

    def __exit__(self, *_: object) -> None:
        pass


@pytest.fixture
def msg_path(tmp_path: Path) -> Path:
    """Dummy .msg file path (content irrelevant — extract_msg is mocked)."""
    path = tmp_path / "sample.msg"
    path.write_bytes(b"\x00" * 16)  # minimal placeholder bytes
    return path


class TestMsgHandlerCanHandle:
    def test_lowercase_msg(self) -> None:
        assert MsgHandler().can_handle(Path("file.msg")) is True

    def test_uppercase_msg(self) -> None:
        assert MsgHandler().can_handle(Path("file.MSG")) is True

    def test_eml_not_handled(self) -> None:
        assert MsgHandler().can_handle(Path("file.eml")) is False

    def test_txt_not_handled(self) -> None:
        assert MsgHandler().can_handle(Path("file.txt")) is False


class TestMsgHandlerExtract:
    def test_valid_msg_returns_success(self, msg_path: Path) -> None:
        with patch("extract_msg.Message", return_value=_FakeMsg()):
            result = MsgHandler().extract(msg_path)
        assert isinstance(result, Success)

    def test_from_header_present(self, msg_path: Path) -> None:
        with patch("extract_msg.Message", return_value=_FakeMsg()):
            result = MsgHandler().extract(msg_path)
        assert isinstance(result, Success)
        assert "alice@example.com" in result.value.text

    def test_subject_header_present(self, msg_path: Path) -> None:
        with patch("extract_msg.Message", return_value=_FakeMsg()):
            result = MsgHandler().extract(msg_path)
        assert isinstance(result, Success)
        assert "Quarterly Report" in result.value.text

    def test_body_present(self, msg_path: Path) -> None:
        with patch("extract_msg.Message", return_value=_FakeMsg()):
            result = MsgHandler().extract(msg_path)
        assert isinstance(result, Success)
        assert "Please review" in result.value.text

    def test_is_md_false(self, msg_path: Path) -> None:
        with patch("extract_msg.Message", return_value=_FakeMsg()):
            result = MsgHandler().extract(msg_path)
        assert isinstance(result, Success)
        assert result.value.is_md is False

    def test_source_path(self, msg_path: Path) -> None:
        with patch("extract_msg.Message", return_value=_FakeMsg()):
            result = MsgHandler().extract(msg_path)
        assert isinstance(result, Success)
        assert result.value.source_path == msg_path

    def test_nonexistent_path_returns_failure(self, tmp_path: Path) -> None:
        # extract_msg raises an exception on missing file — propagate as Failure
        result = MsgHandler().extract(tmp_path / "ghost.msg")
        assert isinstance(result, Failure)
        assert result.recoverable is False
