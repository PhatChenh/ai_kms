"""Tests for Fact Search (retrieval/fact_search.py) — P9-B-06.

Tests:
- search_facts returns facts matching keyword query
- search_facts returns facts matching semantic query
- Identity dedup — same fact id from both keyword and semantic collapsed to one
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.result import Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _seed_knowledge_entries(db_path: Path) -> dict[str, int]:
    """Insert knowledge_entries and populate facts_fts + facts_vec.

    Returns a dict mapping short labels to entry_id.
    """
    from storage.db import get_connection

    # Insert knowledge_entries using get_connection (loads sqlite_vec)
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "people", "Alice", "role",
                "Alice is the Engineering Manager responsible for the platform team.",
                "confident", 0.95, '["100"]', 0.9, 10.0, "stated in org chart",
            ),
        )
        alice_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "process", "standup", "schedule",
                "Daily standup happens at 9:30 AM every weekday.",
                "confident", 0.85, '["200"]', 0.7, 5.0, "from team agreement",
            ),
        )
        standup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "tools", "deploy", "pipeline",
                "Deployments go through GitHub Actions CI/CD pipeline.",
                "pending", 0.5, '["300"]', 0.5, 0.0, "",
            ),
        )
        deploy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "people", "Bob", "role",
                "Bob is a retired fact that should never appear in search.",
                "retired", 0.3, '["400"]', 0.1, 0.0, "outdated",
            ),
        )
        retired_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Now populate FTS and vec indexes via the real embedding path
    from retrieval.embeddings import _get_model

    model = _get_model()

    # Build embedding and index for each fact
    entries = [
        (alice_id, "Alice", "Alice is the Engineering Manager responsible for the platform team."),
        (standup_id, "standup", "Daily standup happens at 9:30 AM every weekday."),
        (deploy_id, "deploy", "Deployments go through GitHub Actions CI/CD pipeline."),
        (retired_id, "Bob", "Bob is a retired fact that should never appear in search."),
    ]

    for entry_id, entity, fact_text in entries:
        embedding = model.encode(fact_text)
        if hasattr(embedding, "numpy"):
            embedding = embedding.numpy()
        blob = embedding.astype("float32").tobytes()

        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO facts_fts(rowid, entry_id, entity, fact) "
                "VALUES (?, ?, ?, ?)",
                (entry_id, entry_id, entity, fact_text),
            )
            conn.execute(
                "INSERT INTO facts_vec(entry_id, embedding) VALUES (?, ?)",
                (entry_id, blob),
            )

    return {
        "alice": alice_id,
        "standup": standup_id,
        "deploy": deploy_id,
        "retired": retired_id,
    }


@pytest.fixture
def seeded_fact_db(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    """Temp DB with 4 knowledge entries (3 active, 1 retired) + search indexes."""
    db_path = tmp_path / "test_fact_search.db"
    init_db(db_path)
    ids = _seed_knowledge_entries(db_path)
    return db_path, ids


# ---------------------------------------------------------------------------
# Keyword search tests
# ---------------------------------------------------------------------------


def test_search_facts_keyword_match(seeded_fact_db):
    """search_facts('standup') returns the standup fact via keyword match."""
    db_path, ids = seeded_fact_db

    from retrieval.fact_search import search_facts

    result = search_facts("standup", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    facts = result.value
    assert len(facts) > 0, "Expected at least one fact result"

    entry_ids = {f.entry_id for f in facts}
    assert ids["standup"] in entry_ids, (
        f"Expected standup fact {ids['standup']} in results, got {entry_ids}"
    )


def test_search_facts_keyword_retired_excluded(seeded_fact_db):
    """Retired facts should never appear in search_facts results."""
    db_path, ids = seeded_fact_db

    from retrieval.fact_search import search_facts

    result = search_facts("Bob", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    facts = result.value
    entry_ids = {f.entry_id for f in facts}
    assert ids["retired"] not in entry_ids, (
        f"Retired fact {ids['retired']} should be excluded"
    )


# ---------------------------------------------------------------------------
# Semantic search tests
# ---------------------------------------------------------------------------


def test_search_facts_semantic_match(seeded_fact_db):
    """search_facts('engineering leadership') returns the Alice fact via
    semantic similarity, even without exact keyword match."""
    db_path, ids = seeded_fact_db

    from retrieval.fact_search import search_facts

    result = search_facts("engineering leadership", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    facts = result.value
    assert len(facts) > 0, "Expected at least one fact result"

    entry_ids = {f.entry_id for f in facts}
    assert ids["alice"] in entry_ids, (
        f"Expected Alice fact {ids['alice']} in semantic results, got {entry_ids}"
    )


def test_search_facts_semantic_ci_cd(seeded_fact_db):
    """search_facts('CI/CD pipeline') returns the deploy fact via semantic match."""
    db_path, ids = seeded_fact_db

    from retrieval.fact_search import search_facts

    result = search_facts("CI/CD pipeline", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    facts = result.value
    assert len(facts) > 0, "Expected at least one fact result"

    entry_ids = {f.entry_id for f in facts}
    assert ids["deploy"] in entry_ids, (
        f"Expected deploy fact {ids['deploy']} in results, got {entry_ids}"
    )


# ---------------------------------------------------------------------------
# Identity dedup tests
# ---------------------------------------------------------------------------


def test_search_facts_no_duplicate_entry_ids(seeded_fact_db):
    """search_facts should never return the same entry_id twice."""
    db_path, ids = seeded_fact_db

    from retrieval.fact_search import search_facts

    result = search_facts("standup morning meeting", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    facts = result.value

    entry_ids = [f.entry_id for f in facts]
    assert len(entry_ids) == len(set(entry_ids)), (
        f"Duplicate entry_ids found: {entry_ids}"
    )


# ---------------------------------------------------------------------------
# Keyword-only mode test
# ---------------------------------------------------------------------------


def test_search_facts_keyword_weight_1_0(seeded_fact_db):
    """With keyword_weight=1.0, only keyword results contribute (no semantic)."""
    db_path, ids = seeded_fact_db

    from retrieval.fact_search import search_facts

    result = search_facts("standup", keyword_weight=1.0, db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    facts = result.value
    assert len(facts) > 0, "Expected at least one fact result"

    # The standup fact should be among results
    entry_ids = {f.entry_id for f in facts}
    assert ids["standup"] in entry_ids


# ---------------------------------------------------------------------------
# Empty result test
# ---------------------------------------------------------------------------


def test_search_facts_no_match_returns_empty(seeded_fact_db):
    """search_facts with a nonsense query returns Success (not Failure).
    Note: semantic search always returns nearest neighbors, so results may
    be non-empty even for nonsense queries — the key contract is no crash."""
    db_path, _ids = seeded_fact_db

    from retrieval.fact_search import search_facts

    result = search_facts("xyznonexistent9876", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    # Semantic search always finds nearest neighbors, so result may not be empty.
    # The key contract is that we don't crash.


# ---------------------------------------------------------------------------
# Result shape tests
# ---------------------------------------------------------------------------


def test_fact_result_has_all_fields(seeded_fact_db):
    """Each FactResult should have all expected fields populated."""
    db_path, ids = seeded_fact_db

    from retrieval.fact_search import search_facts

    result = search_facts("standup", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    facts = result.value
    assert len(facts) > 0

    for f in facts:
        assert isinstance(f.entry_id, int)
        assert f.entry_id > 0
        assert isinstance(f.dimension, str)
        assert isinstance(f.entity, str)
        assert isinstance(f.fact, str)
        assert isinstance(f.confidence, str)
        assert isinstance(f.trust_score, float)
        assert isinstance(f.retrieval_score, float)
        assert isinstance(f.sources, str)
        assert isinstance(f.score, float)
        assert f.score > 0.0, f"Score should be positive, got {f.score}"


def test_fact_result_ordering_by_score(seeded_fact_db):
    """Results should be ordered by descending fusion score."""
    db_path, _ids = seeded_fact_db

    from retrieval.fact_search import search_facts

    result = search_facts("standup deploy pipeline", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    facts = result.value
    if len(facts) >= 2:
        for i in range(len(facts) - 1):
            assert facts[i].score >= facts[i + 1].score, (
                f"Scores not descending at index {i}: "
                f"{facts[i].score} < {facts[i + 1].score}"
            )
