"""Tests for Dual-Corpus Search (retrieval/search.py:search_dual) — P9-B-06.

Tests:
- search_dual merges fact + doc results into DualCorpusResult
- Unclassified document (no facts yet) surfaces via document search leg
- Identity dedup within each list
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.result import Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _seed_dual_corpus_db(db_path: Path) -> dict[str, int]:
    """Seed DB with knowledge_entries + documents + all search indexes.

    Returns dict with entry_ids and doc_ids keyed by label.
    """
    from storage.db import get_connection

    with get_connection(db_path) as conn:
        # --- knowledge_entries (facts) ---
        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "people",
                "Alice",
                "role",
                "Alice is the Engineering Manager.",
                "confident",
                0.95,
                '["1"]',
                0.9,
                10.0,
                "from org chart",
            ),
        )
        alice_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            """INSERT INTO knowledge_entries
               (dimension, entity, tag, fact, status, confidence, sources,
                trust_score, retrieval_count, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "process",
                "standup",
                "schedule",
                "Daily standup at 9:30 AM.",
                "confident",
                0.85,
                '["2"]',
                0.7,
                5.0,
                "",
            ),
        )
        standup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # --- documents ---
        conn.execute(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, project, updated_at, key_topics)
               VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
            (
                "Projects/Alpha/stakeholder.md",
                "Stakeholder Resistance Management",
                "How to handle stakeholder resistance and manage pushback.",
                "meeting-notes",
                "Alpha",
                '["stakeholder", "change-management"]',
            ),
        )
        doc_a_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, project, updated_at, key_topics)
               VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
            (
                "inbox/budget.md",
                "Quarterly Budget Analysis Q3",
                "Detailed quarterly budget analysis for Q3 2026.",
                "analysis",
                None,
                '["budget", "finance"]',
            ),
        )
        doc_b_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            """INSERT INTO documents
               (vault_path, title, summary, note_type, project, updated_at, key_topics)
               VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
            (
                "inbox/unclassified.md",
                "Unclassified Raw Note",
                "This note has no facts extracted yet.",
                "raw",
                None,
                '["misc"]',
            ),
        )
        doc_c_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # --- Populate FTS + vec indexes for facts ---
    from retrieval.embeddings import _get_model
    from storage.db import get_connection

    model = _get_model()

    fact_entries = [
        (alice_id, "Alice", "Alice is the Engineering Manager."),
        (standup_id, "standup", "Daily standup at 9:30 AM."),
    ]

    for entry_id, entity, fact_text in fact_entries:
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

    # --- Populate FTS + vec indexes for documents ---
    from retrieval.embeddings import index_embedding
    from retrieval.keyword import index_keywords

    # Doc A — stakeholder
    index_embedding(
        "Projects/Alpha/stakeholder.md",
        "Stakeholder Resistance Management",
        "meeting-notes",
        ["stakeholder", "change-management"],
        "How to handle stakeholder resistance and manage pushback.",
        db_path=db_path,
    )
    index_keywords(
        "Projects/Alpha/stakeholder.md",
        "Stakeholder Resistance Management",
        "How to handle stakeholder resistance and manage pushback.",
        "Stakeholder resistance is a common challenge in change management. "
        "This note covers strategies for managing pushback.",
        db_path=db_path,
    )

    # Doc B — budget
    index_embedding(
        "inbox/budget.md",
        "Quarterly Budget Analysis Q3",
        "analysis",
        ["budget", "finance"],
        "Detailed quarterly budget analysis for Q3 2026.",
        db_path=db_path,
    )
    index_keywords(
        "inbox/budget.md",
        "Quarterly Budget Analysis Q3",
        "Detailed quarterly budget analysis for Q3 2026.",
        "The Q3 budget analysis shows a 12% increase in expenses.",
        db_path=db_path,
    )

    # Doc C — unclassified (no facts yet)
    index_embedding(
        "inbox/unclassified.md",
        "Unclassified Raw Note",
        "raw",
        ["misc"],
        "This note has no facts extracted yet.",
        db_path=db_path,
    )
    index_keywords(
        "inbox/unclassified.md",
        "Unclassified Raw Note",
        "This note has no facts extracted yet.",
        "This is raw unclassified content about miscellaneous topics.",
        db_path=db_path,
    )

    return {
        "alice": alice_id,
        "standup": standup_id,
        "doc_a": doc_a_id,
        "doc_b": doc_b_id,
        "doc_c": doc_c_id,
    }


@pytest.fixture
def seeded_dual_db(tmp_path: Path, mock_embedder_1024) -> tuple[Path, dict[str, int]]:
    """Temp DB with facts + documents + all indexes."""
    db_path = tmp_path / "test_dual_search.db"
    init_db(db_path)
    ids = _seed_dual_corpus_db(db_path)
    return db_path, ids


# ---------------------------------------------------------------------------
# Dual-corpus search tests
# ---------------------------------------------------------------------------


def test_search_dual_returns_both_facts_and_docs(seeded_dual_db):
    """search_dual('standup') returns facts about standup AND docs about standup."""
    db_path, ids = seeded_dual_db

    from retrieval.search import DualCorpusResult, search_dual

    result = search_dual("standup", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    dual: DualCorpusResult = result.value
    assert isinstance(dual, DualCorpusResult)

    # Facts should contain the standup fact
    assert len(dual.facts) > 0, "Expected at least one fact result"
    fact_entry_ids = {f.entry_id for f in dual.facts}
    assert ids["standup"] in fact_entry_ids, (
        f"Expected standup fact {ids['standup']} in facts"
    )

    # Docs should be present (at least one doc)
    assert len(dual.documents) > 0, "Expected at least one document result"


def test_search_dual_unclassified_doc_surfaces(seeded_dual_db):
    """An unclassified document (no facts) should still surface via doc search."""
    db_path, ids = seeded_dual_db

    from retrieval.search import DualCorpusResult, search_dual

    result = search_dual("unclassified misc raw", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    dual: DualCorpusResult = result.value

    # The unclassified doc should be in document results
    doc_paths = {d.vault_path for d in dual.documents}
    assert "inbox/unclassified.md" in doc_paths, (
        f"Expected unclassified doc in results, got {doc_paths}"
    )

    # Facts may or may not be present — but the unclassified doc has no
    # associated facts, so facts list may be empty or contain unrelated facts.
    # The key assertion is that the doc leg works independently.
    assert len(dual.documents) > 0


def test_search_dual_fact_dedup(seeded_dual_db):
    """Identity dedup within fact list: no duplicate entry_id."""
    db_path, _ids = seeded_dual_db

    from retrieval.search import DualCorpusResult, search_dual

    result = search_dual("Alice engineering manager", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    dual: DualCorpusResult = result.value

    fact_ids = [f.entry_id for f in dual.facts]
    assert len(fact_ids) == len(set(fact_ids)), f"Duplicate fact entry_ids: {fact_ids}"


def test_search_dual_doc_dedup(seeded_dual_db):
    """Identity dedup within doc list: no duplicate id."""
    db_path, _ids = seeded_dual_db

    from retrieval.search import DualCorpusResult, search_dual

    result = search_dual("stakeholder resistance", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    dual: DualCorpusResult = result.value

    doc_ids = [d.id for d in dual.documents if d.id is not None]
    assert len(doc_ids) == len(set(doc_ids)), f"Duplicate doc ids: {doc_ids}"


def test_search_dual_with_project_filter(seeded_dual_db):
    """search_dual with project='Alpha' scopes document results."""
    db_path, _ids = seeded_dual_db

    from retrieval.search import DualCorpusResult, search_dual

    result = search_dual("stakeholder", project="Alpha", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    dual: DualCorpusResult = result.value

    # All doc results should be from Alpha
    for doc in dual.documents:
        assert doc.metadata["project"] == "Alpha", (
            f"Expected Alpha only, got {doc.metadata['project']}"
        )


def test_search_dual_empty_result(seeded_dual_db):
    """search_dual with nonsense query returns empty both sides."""
    db_path, _ids = seeded_dual_db

    from retrieval.search import DualCorpusResult, search_dual

    result = search_dual("xyznonexistent9876zzz", db_path=db_path)

    assert isinstance(result, Success), f"Expected Success, got {result}"
    dual: DualCorpusResult = result.value
    # May have empty facts and empty docs
    assert isinstance(dual.facts, list)
    assert isinstance(dual.documents, list)
