"""Tests for dimension rulebook validation and confidence→status mapping."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from core.result import Failure
from core.tags import (
    confidence_to_status,
    load_dimensions,
    validate_dimension_tag,
)
from core.config import ConfidenceBand

# Inline rulebook matching dimensions.yaml — no CONFIG import.
RULEBOOK = {
    "people": ["role", "other"],
    "projects": ["status", "timeline", "other"],
    "domains": ["other"],
}


class TestValidateDimensionTag:
    def test_known_dimension_known_tag_returns_success(self) -> None:
        result = validate_dimension_tag("people", "role", RULEBOOK)
        assert result.is_success()
        assert result.unwrap() is True

    def test_known_dimension_invented_tag_returns_failure(self) -> None:
        result = validate_dimension_tag("people", "xyz", RULEBOOK)
        assert result.is_failure()
        assert isinstance(result, Failure)
        assert "xyz" in result.error

    def test_unknown_dimension_returns_failure(self) -> None:
        result = validate_dimension_tag("nope", "role", RULEBOOK)
        assert isinstance(result, Failure)
        assert "nope" in result.error

    def test_catch_all_other_tag_returns_success_for_every_dimension(self) -> None:
        for dim in RULEBOOK:
            result = validate_dimension_tag(dim, "other", RULEBOOK)
            assert result.is_success(), f"other tag failed for dimension {dim}"
            assert result.unwrap() is True


class TestLoadDimensions:
    def test_load_dimensions_returns_correct_structure(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.safe_dump(RULEBOOK, f)
            tmp_path = Path(f.name)

        try:
            loaded = load_dimensions(tmp_path)
            assert isinstance(loaded, dict)
            assert set(loaded.keys()) == {"people", "projects", "domains"}
            assert "other" in loaded["people"]
            assert "other" in loaded["projects"]
            assert "other" in loaded["domains"]
            assert "role" in loaded["people"]
            assert "status" in loaded["projects"]
        finally:
            tmp_path.unlink()


class TestConfidenceToStatus:
    def test_high_score_maps_to_confident(self) -> None:
        band = ConfidenceBand(auto=0.8, suggest=0.5)
        assert confidence_to_status(0.85, band) == "confident"
        assert confidence_to_status(0.80, band) == "confident"  # at boundary

    def test_low_score_below_suggest_maps_to_pending(self) -> None:
        band = ConfidenceBand(auto=0.8, suggest=0.5)
        assert confidence_to_status(0.49, band) == "pending"
        assert confidence_to_status(0.0, band) == "pending"

    def test_mid_score_maps_to_pending(self) -> None:
        band = ConfidenceBand(auto=0.8, suggest=0.5)
        # score 0.5-0.79 → SUGGEST or CLUELESS → both map to pending
        assert confidence_to_status(0.5, band) == "pending"
        assert confidence_to_status(0.7, band) == "pending"
