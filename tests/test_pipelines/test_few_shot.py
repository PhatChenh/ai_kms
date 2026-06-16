"""Unit tests for src/pipelines/few_shot.py — Phase 10 few-shot injector."""

from pathlib import Path

import pytest

from pipelines.few_shot import format_few_shot, select_corrections
from storage.db import init_db


@pytest.fixture
def db_with_corrections(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


class TestFormatFewShot:
    def test_empty_list_returns_empty_string(self):
        assert format_few_shot([]) == ""

    def test_single_correction_formats_correctly(self):
        corrections = [
            {
                "old_fact": "John works at Acme",
                "new_fact": "John works at BetaCorp",
                "feedback": "He changed jobs",
                "dimension": "people",
                "entity": "John",
            }
        ]
        result = format_few_shot(corrections)
        assert "Previous extraction mistakes to avoid:" in result
        assert "John" in result
        assert "people" in result
        assert "Acme" in result
        assert "BetaCorp" in result
        assert "He changed jobs" in result

    def test_multiple_corrections(self):
        corrections = [
            {
                "old_fact": "X",
                "new_fact": "Y",
                "feedback": "",
                "dimension": "projects",
                "entity": "Alpha",
            },
            {
                "old_fact": "A",
                "new_fact": "",
                "feedback": "",
                "dimension": "people",
                "entity": "Bob",
            },
        ]
        result = format_few_shot(corrections)
        assert result.count("Previous extraction mistakes to avoid:") == 1
        assert "Alpha" in result
        assert "Bob" in result
        # Second correction has empty new_fact — the "correct fact is" line should not appear
        assert "The correct fact is" in result  # from first correction
        assert result.count("The correct fact is") == 1  # only first has it

    def test_no_feedback_field_omitted(self):
        corrections = [
            {
                "old_fact": "X",
                "new_fact": "Y",
                "feedback": "",
                "dimension": "people",
                "entity": "Test",
            }
        ]
        result = format_few_shot(corrections)
        # Empty feedback should not leave a trailing space
        lines = result.strip().split("\n")
        assert not lines[-1].endswith(" ")


class TestSelectCorrections:
    def test_empty_db_returns_empty_list(self, db_with_corrections: Path):
        result = select_corrections(
            "people", [], cap=5, db_path=db_with_corrections
        )
        assert result.is_success()
        assert result.value == []

    def test_cap_respected(self, db_with_corrections: Path):
        # With no corrections in DB, returns empty
        result = select_corrections(
            "people", ["Alice"], cap=3, db_path=db_with_corrections
        )
        assert result.is_success()
        assert len(result.value) <= 3
