"""Tests for core/tags.py — TagTaxonomy, validate_tags, load_taxonomy."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.tags import TagTaxonomy, load_taxonomy, validate_tags

TAGS_YAML = Path(__file__).parent.parent.parent / "config" / "tags.yaml"

SAMPLE_TAXONOMY = TagTaxonomy(
    allowed_types=frozenset(
        ["meeting-note", "email", "report", "article", "reflection", "task-list", "transcript", "capture"]
    ),
    valid_domains=frozenset(["finance", "strategy"]),
)


class TestTagsYaml:
    def test_tags_yaml_is_valid_and_has_eight_types(self) -> None:
        raw = yaml.safe_load(TAGS_YAML.read_text())
        assert isinstance(raw, dict)
        allowed = raw.get("allowed_types", [])
        assert isinstance(allowed, list)
        assert len(allowed) == 8


class TestValidateTags:
    def test_all_valid_tags_produce_no_violations(self) -> None:
        valid, violations = validate_tags(
            ["type/report", "domain/finance", "quarterly-kpi"], SAMPLE_TAXONOMY
        )
        assert violations == []
        assert "type/report" in valid
        assert "domain/finance" in valid
        assert "quarterly-kpi" in valid

    def test_unknown_type_tag_is_dropped_with_violation(self) -> None:
        valid, violations = validate_tags(["type/bad-value"], SAMPLE_TAXONOMY)
        assert any("unknown type tag" in v for v in violations)
        assert "type/bad-value" not in valid

    def test_unknown_domain_tag_is_dropped_with_violation(self) -> None:
        valid, violations = validate_tags(["type/report", "domain/nonexistent"], SAMPLE_TAXONOMY)
        assert any("unknown domain tag" in v for v in violations)
        assert "domain/nonexistent" not in valid

    def test_namespaced_free_tag_is_dropped_with_violation(self) -> None:
        valid, violations = validate_tags(["type/report", "status/active"], SAMPLE_TAXONOMY)
        assert any("namespace prefix" in v for v in violations)
        assert "status/active" not in valid

    def test_empty_list_produces_no_type_violation(self) -> None:
        valid, violations = validate_tags([], SAMPLE_TAXONOMY)
        assert any("no type/ tag found" in v for v in violations)
        assert valid == []

    def test_multiple_type_tags_violation_keeps_first(self) -> None:
        valid, violations = validate_tags(
            ["type/report", "type/article", "free-tag"], SAMPLE_TAXONOMY
        )
        assert any("multiple type/ tags" in v for v in violations)
        type_tags = [t for t in valid if t.startswith("type/")]
        assert type_tags == ["type/report"]
        assert "free-tag" in valid

    def test_empty_valid_domains_makes_all_domain_tags_violations(self) -> None:
        taxonomy = TagTaxonomy(
            allowed_types=SAMPLE_TAXONOMY.allowed_types,
            valid_domains=frozenset(),
        )
        valid, violations = validate_tags(["type/report", "domain/anything"], taxonomy)
        assert any("unknown domain tag" in v for v in violations)
        assert "domain/anything" not in valid


class TestLoadTaxonomy:
    def test_load_taxonomy_returns_correct_taxonomy(self) -> None:
        taxonomy = load_taxonomy(TAGS_YAML, frozenset(["finance"]))
        assert isinstance(taxonomy, TagTaxonomy)
        assert isinstance(taxonomy.allowed_types, frozenset)
        assert len(taxonomy.allowed_types) == 8
        assert taxonomy.valid_domains == frozenset(["finance"])

    def test_load_taxonomy_populates_allowed_types_from_file(self) -> None:
        taxonomy = load_taxonomy(TAGS_YAML, frozenset())
        assert "report" in taxonomy.allowed_types
        assert "meeting-note" in taxonomy.allowed_types
