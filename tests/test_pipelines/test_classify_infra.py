"""Phase 6 — Content Reader + Context Loader (classify infra helpers)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.config import ClassifyConfig, MainConfig
from core.result import Failure, Result, Success
from storage.db import get_connection
from storage.knowledge_entries import KnowledgeEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_document(
    conn: sqlite3.Connection,
    *,
    vault_path: str = "test/doc.md",
    title: str = "Test Doc",
    full_body: str | None = None,
    summary: str | None = None,
    content_hash: str | None = "abc123",
) -> int:
    """Insert a document and return its id."""
    cur = conn.execute(
        """INSERT INTO documents (vault_path, title, full_body, summary, content_hash)
           VALUES (?, ?, ?, ?, ?)""",
        (vault_path, title, full_body, summary, content_hash),
    )
    return cur.lastrowid


def _seed_knowledge_entry(
    conn: sqlite3.Connection,
    *,
    dimension: str = "people",
    entity: str = "Alice",
    tag: str = "role",
    fact: str = "Engineer",
    status: str = "confident",
    confidence: float = 0.9,
    trust_score: float = 0.8,
    retrieval_count: int = 0,
    reasoning: str = "test",
) -> int:
    """Insert a knowledge entry and return its id."""
    import json

    cur = conn.execute(
        """INSERT INTO knowledge_entries
           (dimension, entity, tag, fact, status, confidence, sources, reasoning,
            trust_score, retrieval_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            dimension,
            entity,
            tag,
            fact,
            status,
            confidence,
            json.dumps(["test_source"]),
            reasoning,
            trust_score,
            retrieval_count,
        ),
    )
    return cur.lastrowid


def _make_config(
    max_content_tokens: int = 10000,
    max_entries_per_dimension: int = 50,
) -> MainConfig:
    """Build a MainConfig with an explicit ClassifyConfig for testing."""
    from unittest.mock import MagicMock

    cfg = MagicMock(spec=MainConfig)
    cfg.classify = ClassifyConfig(
        max_content_tokens=max_content_tokens,
        max_entries_per_dimension=max_entries_per_dimension,
    )
    return cfg


# ---------------------------------------------------------------------------
# Content Reader
# ---------------------------------------------------------------------------


class TestContentReader:
    """Phase 6 — Content Reader chooses full_body vs summary by token budget."""

    def test_small_body_returns_full_body(self, db_path: Path):
        """When len(full_body)//4 < max_content_tokens, use full_body."""
        from pipelines.classify import content_reader

        config = _make_config(max_content_tokens=10000)

        # full_body is 100 chars → 100//4 = 25 tokens, well under 10000
        body = "a" * 100
        summary = "short summary"

        with get_connection(db_path) as conn:
            doc_id = _seed_document(conn, full_body=body, summary=summary)

        result = content_reader(doc_id, config=config, db_path=db_path)
        assert isinstance(result, Success), f"Expected Success, got {result}"
        assert result.value == body

    def test_large_body_returns_summary(self, db_path: Path):
        """When len(full_body)//4 >= max_content_tokens, use summary."""
        from pipelines.classify import content_reader

        config = _make_config(max_content_tokens=50)

        # full_body is 400 chars → 400//4 = 100 tokens, over 50 threshold
        body = "x" * 400
        summary = "this is the summary"

        with get_connection(db_path) as conn:
            doc_id = _seed_document(conn, full_body=body, summary=summary)

        result = content_reader(doc_id, config=config, db_path=db_path)
        assert isinstance(result, Success), f"Expected Success, got {result}"
        assert result.value == summary

    def test_none_full_body_falls_back_to_summary(self, db_path: Path):
        """When full_body is None, use summary regardless of token budget."""
        from pipelines.classify import content_reader

        # Even with a huge token budget, None/empty full_body → summary
        config = _make_config(max_content_tokens=1000000)

        summary = "fallback summary"

        with get_connection(db_path) as conn:
            doc_id = _seed_document(conn, full_body=None, summary=summary)

        result = content_reader(doc_id, config=config, db_path=db_path)
        assert isinstance(result, Success), f"Expected Success, got {result}"
        assert result.value == summary

    def test_empty_full_body_falls_back_to_summary(self, db_path: Path):
        """When full_body is an empty string, use summary."""
        from pipelines.classify import content_reader

        config = _make_config(max_content_tokens=1000000)

        summary = "fallback summary for empty body"

        with get_connection(db_path) as conn:
            doc_id = _seed_document(conn, full_body="", summary=summary)

        result = content_reader(doc_id, config=config, db_path=db_path)
        assert isinstance(result, Success), f"Expected Success, got {result}"
        assert result.value == summary

    def test_threshold_boundary(self, db_path: Path):
        """Exact boundary: when len//4 equals threshold, use summary (>=)."""
        from pipelines.classify import content_reader

        config = _make_config(max_content_tokens=10)

        # full_body is 40 chars → 40//4 = 10 tokens, equals threshold → summary
        body = "z" * 40
        summary = "boundary summary"

        with get_connection(db_path) as conn:
            doc_id = _seed_document(conn, full_body=body, summary=summary)

        result = content_reader(doc_id, config=config, db_path=db_path)
        assert isinstance(result, Success), f"Expected Success, got {result}"
        # len//4 = 10, which is NOT < 10, so use summary
        assert result.value == summary

    def test_threshold_from_config_not_literal(self):
        """Verify content_reader reads max_content_tokens from config, not a literal."""
        from pipelines.classify import content_reader
        import inspect

        source = inspect.getsource(content_reader)
        # Must not contain a hard-coded number like 10000 or 50
        assert "10000" not in source, (
            "content_reader must not hardcode max_content_tokens; use config"
        )

    def test_missing_doc_returns_failure(self, db_path: Path):
        """Non-existent doc id returns Failure."""
        from pipelines.classify import content_reader

        config = _make_config()
        result = content_reader(99999, config=config, db_path=db_path)
        assert isinstance(result, Failure)

    def test_no_db_path_uses_default(self):
        """content_reader works without explicit db_path (uses CONFIG default)."""
        # This test verifies the function signature accepts db_path=None
        from pipelines.classify import content_reader
        import inspect

        sig = inspect.signature(content_reader)
        params = sig.parameters
        assert "db_path" in params, "content_reader must accept db_path"
        # db_path default should be None
        assert params["db_path"].default is None, (
            "db_path must default to None so callers can omit it"
        )


# ---------------------------------------------------------------------------
# Context Loader
# ---------------------------------------------------------------------------


class TestContextLoader:
    """Phase 6 — Context Loader returns ranked, capped, non-retired facts per dimension."""

    def test_returns_facts_for_each_dimension(self, db_path: Path):
        """For dimensions with facts, returns ranked lists keyed by dimension."""
        from pipelines.classify import context_loader

        config = _make_config(max_entries_per_dimension=10)

        with get_connection(db_path) as conn:
            _seed_knowledge_entry(
                conn, dimension="people", entity="Alice", fact="Engineer",
                trust_score=0.9, confidence=0.95
            )
            _seed_knowledge_entry(
                conn, dimension="projects", entity="Alpha", fact="Active",
                trust_score=0.7, confidence=0.8
            )

        result = context_loader(config=config, db_path=db_path)
        assert isinstance(result, Success), f"Expected Success, got {result}"
        data = result.value
        assert isinstance(data, dict)
        assert "people" in data
        assert "projects" in data
        assert "domains" in data  # from dimensions.yaml
        assert len(data["people"]) == 1
        assert data["people"][0].entity == "Alice"
        assert len(data["projects"]) == 1
        assert data["projects"][0].entity == "Alpha"
        # domains has no facts → empty list
        assert data["domains"] == []

    def test_empty_dimension_returns_empty_list_not_error(self, db_path: Path):
        """Dimension with no facts returns an empty list, not a Failure."""
        from pipelines.classify import context_loader

        config = _make_config()

        # No entries seeded → all dimensions return []
        result = context_loader(config=config, db_path=db_path)
        assert isinstance(result, Success), f"Expected Success, got {result}"
        data = result.value
        for dim_entries in data.values():
            assert isinstance(dim_entries, list)
            assert len(dim_entries) == 0

    def test_caps_entries_per_dimension(self, db_path: Path):
        """Returns at most max_entries_per_dimension facts per dimension."""
        from pipelines.classify import context_loader

        config = _make_config(max_entries_per_dimension=3)

        with get_connection(db_path) as conn:
            for i in range(10):
                _seed_knowledge_entry(
                    conn,
                    dimension="people",
                    entity=f"Person{i}",
                    fact=f"Fact {i}",
                    trust_score=0.5 + i * 0.04,  # increasing
                    confidence=0.5 + i * 0.04,
                )

        result = context_loader(config=config, db_path=db_path)
        assert isinstance(result, Success), f"Expected Success, got {result}"
        data = result.value
        assert len(data["people"]) == 3

    def test_excludes_retired_entries(self, db_path: Path):
        """Retired entries must not appear in the context."""
        from pipelines.classify import context_loader

        config = _make_config(max_entries_per_dimension=10)

        with get_connection(db_path) as conn:
            _seed_knowledge_entry(
                conn, dimension="people", entity="Alice", fact="Active",
                status="confident", trust_score=0.9
            )
            _seed_knowledge_entry(
                conn, dimension="people", entity="Bob", fact="Retired",
                status="retired", trust_score=0.5
            )

        result = context_loader(config=config, db_path=db_path)
        assert isinstance(result, Success), f"Expected Success, got {result}"
        data = result.value
        people = data["people"]
        assert len(people) == 1
        assert people[0].entity == "Alice"

    def test_ranking_order(self, db_path: Path):
        """Entries are ranked by trust_score DESC, confidence DESC, updated_at DESC."""
        from pipelines.classify import context_loader

        config = _make_config(max_entries_per_dimension=5)

        with get_connection(db_path) as conn:
            # High trust, lower confidence
            _seed_knowledge_entry(
                conn, dimension="people", entity="HighTrust",
                fact="Top", trust_score=0.95, confidence=0.5,
            )
            # Lower trust, high confidence
            _seed_knowledge_entry(
                conn, dimension="people", entity="HighConf",
                fact="Second", trust_score=0.5, confidence=0.95,
            )
            # Lowest both
            _seed_knowledge_entry(
                conn, dimension="people", entity="LowBoth",
                fact="Third", trust_score=0.3, confidence=0.3,
            )

        result = context_loader(config=config, db_path=db_path)
        assert isinstance(result, Success), f"Expected Success, got {result}"
        data = result.value
        people = data["people"]
        assert len(people) == 3
        # First should be HighTrust (trust_score 0.95)
        assert people[0].entity == "HighTrust"
        # Second should be HighConf (trust_score 0.5 > 0.3)
        assert people[1].entity == "HighConf"
        # Third is LowBoth
        assert people[2].entity == "LowBoth"

    def test_returns_knowledge_entry_with_id(self, db_path: Path):
        """Each returned entry has its database id populated."""
        from pipelines.classify import context_loader

        config = _make_config(max_entries_per_dimension=5)

        with get_connection(db_path) as conn:
            kid = _seed_knowledge_entry(
                conn, dimension="people", entity="Alice", fact="HasID"
            )

        result = context_loader(config=config, db_path=db_path)
        assert isinstance(result, Success), f"Expected Success, got {result}"
        data = result.value
        assert data["people"][0].id == kid

    def test_dimensions_from_yaml_rulebook(self, db_path: Path):
        """Dimensions are loaded from dimensions.yaml, not hardcoded."""
        from pipelines.classify import context_loader
        import inspect

        source = inspect.getsource(context_loader)
        # Must not hardcode dimension names — load from dimensions.yaml
        assert '"people"' not in source, "context_loader must not hardcode dimension names"
        assert '"projects"' not in source, "context_loader must not hardcode dimension names"
        assert '"domains"' not in source, "context_loader must not hardcode dimension names"

    def test_no_db_path_uses_default(self):
        """context_loader works without explicit db_path."""
        from pipelines.classify import context_loader
        import inspect

        sig = inspect.signature(context_loader)
        params = sig.parameters
        assert "db_path" in params
        assert params["db_path"].default is None

    def test_cap_from_config_not_literal(self):
        """Verify context_loader reads max_entries_per_dimension from config."""
        from pipelines.classify import context_loader
        import inspect

        source = inspect.getsource(context_loader)
        # Must not contain hard-coded limit number (common ones: 50, 100, 20)
        assert "limit=50" not in source, (
            "context_loader must not hardcode the cap; use config.classify.max_entries_per_dimension"
        )
        assert "limit=100" not in source


# ---------------------------------------------------------------------------
# Integration sanity — both helpers together
# ---------------------------------------------------------------------------


class TestInfraIntegration:
    """Quick integration: seed one doc + one fact, exercise both helpers."""

    def test_full_roundtrip(self, db_path: Path):
        from pipelines.classify import content_reader, context_loader

        config = _make_config(max_content_tokens=10000, max_entries_per_dimension=5)

        with get_connection(db_path) as conn:
            doc_id = _seed_document(
                conn,
                vault_path="roundtrip/doc.md",
                full_body="This is the full body content for testing.",
                summary="Short summary.",
            )
            _seed_knowledge_entry(
                conn,
                dimension="people",
                entity="Alice",
                fact="Engineer",
            )

        # Content reader
        cr = content_reader(doc_id, config=config, db_path=db_path)
        assert isinstance(cr, Success)
        # full_body is 47 chars → 47//4 = 11 tokens < 10000 → use full_body
        assert cr.value == "This is the full body content for testing."

        # Context loader
        cl = context_loader(config=config, db_path=db_path)
        assert isinstance(cl, Success)
        assert "people" in cl.value
        assert len(cl.value["people"]) == 1
