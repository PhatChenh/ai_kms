from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from pipelines.classify import ClassifyResult


class TestClassifyResult:
    """Phase 1 — ClassifyResult frozen dataclass."""

    def test_constructs_and_fields_accessible(self):
        """All four fields are readable after construction."""
        result = ClassifyResult(
            target_type="project",
            target_name="Alpha",
            confidence=0.9,
            reasoning="Meeting notes.",
        )
        assert result.target_type == "project"
        assert result.target_name == "Alpha"
        assert result.confidence == 0.9
        assert result.reasoning == "Meeting notes."

    def test_is_frozen(self):
        """Assigning to any field after construction raises FrozenInstanceError."""
        result = ClassifyResult(
            target_type="project",
            target_name="Alpha",
            confidence=0.9,
            reasoning="Meeting notes.",
        )
        with pytest.raises(FrozenInstanceError):
            result.target_type = "domain"

    def test_no_validation_on_construction(self):
        """Construction succeeds even with an invalid target_type value.
        Validation is classify()'s job, not the dataclass's.
        """
        result = ClassifyResult(
            target_type="inbox",
            target_name="Somewhere",
            confidence=0.5,
            reasoning="Should not happen but dataclass accepts it.",
        )
        assert result.target_type == "inbox"
