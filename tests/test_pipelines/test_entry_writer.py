"""Tests for Entry Writer — write_entries() function in pipelines/classify.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.config import ClassifyConfig, MainConfig
from core.result import Success
from storage.db import init_db
from storage.knowledge_entries import KnowledgeEntry, upsert, retire, query_by_entity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(tmp_path: Path) -> MainConfig:
    from core.config import VaultConfig
    return MainConfig(
        vault=VaultConfig(root=str(tmp_path)),
        classify=ClassifyConfig(max_retries=3),
    )


def _seed_entry(
    db_path: Path,
    dimension: str = "people",
    entity: str = "Anthony",
    tag: str = "other",
    fact: str = "Anthony works in engineering.",
    status: str = "confident",
    confidence: float = 0.9,
    sources: list[str] | None = None,
) -> int:
    """Insert a knowledge entry and return its id."""
    entry = KnowledgeEntry(
        dimension=dimension,
        entity=entity,
        tag=tag,
        fact=fact,
        status=status,
        confidence=confidence,
        sources=sources or ["10"],
        reasoning="test seed",
    )
    result = upsert(entry, db_path=db_path)
    assert isinstance(result, Success), f"Seed failed: {result}"
    return result.value


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEntryWriter:
    """Phase 6 Slice B — write_entries() function."""

    def test_new_fact_inserts_fresh_row(self, tmp_path):
        """A new fact with no twin creates a fresh row with sources=[doc_id]."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        from pipelines.classify import write_entries

        facts = [{
            "action": "new",
            "entity": "Anthony",
            "tag": "other",
            "fact": "Anthony leads Movie Q2.",
            "confidence": 0.9,
        }]

        summary = write_entries(
            facts=facts,
            doc_id=100,
            dimension="people",
            band=None,
            db_path=db_path,
        )
        assert isinstance(summary, Success)
        assert summary.value.clean is True

        # Verify the row was created
        entries_result = query_by_entity("Anthony", db_path=db_path)
        assert isinstance(entries_result, Success)
        entries = entries_result.value
        assert len(entries) == 1
        e = entries[0]
        assert e.dimension == "people"
        assert e.fact == "Anthony leads Movie Q2."
        assert e.sources == ["100"]

    def test_new_fact_folds_into_existing_twin(self, tmp_path):
        """A 'new' fact that matches an existing non-retired dimension+entity+tag
        is folded into the existing entry — no second row, source appended."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        existing_id = _seed_entry(
            db_path,
            dimension="people",
            entity="Anthony",
            tag="other",
            fact="Anthony works.",
            sources=["10"],
        )

        from pipelines.classify import write_entries

        facts = [{
            "action": "new",
            "entity": "Anthony",
            "tag": "other",
            "fact": "Anthony leads Movie Q2.",
            "confidence": 0.85,
        }]

        summary = write_entries(
            facts=facts,
            doc_id=100,
            dimension="people",
            band=None,
            db_path=db_path,
        )
        assert isinstance(summary, Success)
        assert summary.value.clean is True

        # Still only one row
        entries_result = query_by_entity("Anthony", db_path=db_path)
        assert isinstance(entries_result, Success)
        entries = entries_result.value
        assert len(entries) == 1
        e = entries[0]
        assert e.id == existing_id
        # Sources should now contain both original and new doc_id
        assert "10" in e.sources
        assert "100" in e.sources

    def test_new_fact_does_not_fold_retired_twin(self, tmp_path):
        """A retired entry should not be folded into — a new row is created."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        retired_id = _seed_entry(
            db_path,
            dimension="people",
            entity="Anthony",
            tag="other",
            fact="Anthony works.",
            status="retired",
            sources=["10"],
        )

        from pipelines.classify import write_entries

        facts = [{
            "action": "new",
            "entity": "Anthony",
            "tag": "other",
            "fact": "Anthony leads Movie Q2.",
            "confidence": 0.9,
        }]

        summary = write_entries(
            facts=facts,
            doc_id=100,
            dimension="people",
            band=None,
            db_path=db_path,
        )
        assert isinstance(summary, Success)
        assert summary.value.clean is True

        entries_result = query_by_entity("Anthony", db_path=db_path)
        assert isinstance(entries_result, Success)
        entries = entries_result.value
        # One retired + one new
        non_retired = [e for e in entries if e.status != "retired"]
        assert len(non_retired) == 1
        assert non_retired[0].fact == "Anthony leads Movie Q2."
        assert non_retired[0].sources == ["100"]

    def test_update_preserves_prior_sources_and_appends_doc_id(self, tmp_path):
        """An update to an existing fact keeps its prior sources PLUS doc_id,
        deduped — no duplicate source ids."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        existing_id = _seed_entry(
            db_path,
            dimension="people",
            entity="Anthony",
            tag="other",
            fact="Anthony works in engineering.",
            sources=["10", "20"],
        )

        from pipelines.classify import write_entries

        facts = [{
            "action": "update",
            "id": existing_id,
            "entity": "Anthony",
            "tag": "other",
            "fact": "Anthony works in the Movies division.",
            "confidence": 0.8,
        }]

        summary = write_entries(
            facts=facts,
            doc_id=100,
            dimension="people",
            band=None,
            db_path=db_path,
        )
        assert isinstance(summary, Success)
        assert summary.value.clean is True

        entries_result = query_by_entity("Anthony", db_path=db_path)
        assert isinstance(entries_result, Success)
        e = entries_result.value[0]
        assert e.id == existing_id
        assert e.fact == "Anthony works in the Movies division."
        # Prior sources retained + new doc_id, deduped
        assert set(e.sources) == {"10", "20", "100"}
        # No duplicates
        assert len(e.sources) == 3

    def test_update_with_duplicate_source_is_deduped(self, tmp_path):
        """If doc_id is already in sources, it is not duplicated."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        existing_id = _seed_entry(
            db_path,
            dimension="people",
            entity="Anthony",
            tag="other",
            fact="Anthony works.",
            sources=["100", "200"],
        )

        from pipelines.classify import write_entries

        facts = [{
            "action": "update",
            "id": existing_id,
            "entity": "Anthony",
            "tag": "other",
            "fact": "Anthony works in Movies.",
            "confidence": 0.9,
        }]

        summary = write_entries(
            facts=facts,
            doc_id=100,  # Already in sources
            dimension="people",
            band=None,
            db_path=db_path,
        )
        assert isinstance(summary, Success)

        entries_result = query_by_entity("Anthony", db_path=db_path)
        e = entries_result.value[0]
        assert set(e.sources) == {"100", "200"}
        assert len(e.sources) == 2  # not duplicated

    def test_retire_flips_status_to_retired(self, tmp_path):
        """A retire action sets the entry to status='retired' — never deletes."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        existing_id = _seed_entry(
            db_path,
            dimension="people",
            entity="Anthony",
            tag="other",
            fact="Old fact.",
        )

        from pipelines.classify import write_entries
        from storage.knowledge_entries import query_by_entity

        facts = [{
            "action": "retire",
            "id": existing_id,
            "reason": "No longer relevant.",
        }]

        summary = write_entries(
            facts=facts,
            doc_id=100,
            dimension="people",
            band=None,
            db_path=db_path,
        )
        assert isinstance(summary, Success)
        assert summary.value.clean is True

        entries_result = query_by_entity("Anthony", db_path=db_path)
        assert isinstance(entries_result, Success)
        entries = entries_result.value
        assert len(entries) == 1, "Entry should not be deleted"
        assert entries[0].status == "retired"

    def test_hallucinated_id_is_skipped_and_marks_unclean(self, tmp_path):
        """A fact referencing a non-existent id is skipped, logged, and
        the summary marks 'not clean'."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        from pipelines.classify import write_entries

        facts = [{
            "action": "update",
            "id": 99999,  # Doesn't exist
            "entity": "Anthony",
            "tag": "other",
            "fact": "Updated fact.",
            "confidence": 0.9,
        }]

        summary = write_entries(
            facts=facts,
            doc_id=100,
            dimension="people",
            band=None,
            db_path=db_path,
        )
        assert isinstance(summary, Success)
        assert summary.value.clean is False
        assert len(summary.value.skipped_ids) == 1
        assert 99999 in summary.value.skipped_ids

    def test_mixed_good_and_bad_facts(self, tmp_path):
        """Good facts are written even when some are hallucinated — but
        the summary is not clean."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        existing_id = _seed_entry(
            db_path,
            dimension="people",
            entity="Anthony",
            tag="other",
            fact="Old fact.",
            sources=["10"],
        )

        from pipelines.classify import write_entries

        facts = [
            {
                "action": "new",
                "entity": "Bob",
                "tag": "other",
                "fact": "Bob is the designer.",
                "confidence": 0.8,
            },
            {  # Hallucinated id
                "action": "update",
                "id": 99999,
                "entity": "Anthony",
                "tag": "other",
                "fact": "Bad update.",
                "confidence": 0.5,
            },
            {
                "action": "retire",
                "id": existing_id,
                "reason": "Done.",
            },
        ]

        summary = write_entries(
            facts=facts,
            doc_id=100,
            dimension="people",
            band=None,
            db_path=db_path,
        )
        assert isinstance(summary, Success)
        assert summary.value.clean is False  # One hallucinated
        assert 99999 in summary.value.skipped_ids

        # Bob was created
        bob_entries = query_by_entity("Bob", db_path=db_path)
        assert isinstance(bob_entries, Success)
        assert len(bob_entries.value) == 1
        assert bob_entries.value[0].sources == ["100"]

        # Anthony's old entry was retired
        anthony_entries = query_by_entity("Anthony", db_path=db_path)
        assert isinstance(anthony_entries, Success)
        retired = [e for e in anthony_entries.value if e.id == existing_id]
        assert retired[0].status == "retired"

    def test_status_is_re_gated_via_confidence_to_status(self, tmp_path):
        """Every written fact's status is re-gated from confidence
        via confidence_to_status — never a float literal compare."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        from pipelines.classify import write_entries

        # Also need a ConfidenceBand for the re-gate
        from core.config import ConfidenceBand

        band = ConfidenceBand(auto=0.85, suggest=0.60)

        # High confidence → should be "confident"
        facts_high = [{
            "action": "new",
            "entity": "Anthony",
            "tag": "other",
            "fact": "High confidence fact.",
            "confidence": 0.95,
        }]

        summary = write_entries(
            facts=facts_high,
            doc_id=100,
            dimension="people",
            band=band,
            db_path=db_path,
        )
        assert isinstance(summary, Success)
        entries = query_by_entity("Anthony", db_path=db_path)
        assert entries.value[0].status == "confident"

        # Low confidence → should be "pending"
        facts_low = [{
            "action": "new",
            "entity": "Bob",
            "tag": "other",
            "fact": "Low confidence fact.",
            "confidence": 0.3,
        }]

        summary2 = write_entries(
            facts=facts_low,
            doc_id=100,
            dimension="people",
            band=band,
            db_path=db_path,
        )
        assert isinstance(summary2, Success)
        bob_entries = query_by_entity("Bob", db_path=db_path)
        assert bob_entries.value[0].status == "pending"

    def test_empty_facts_list_is_clean(self, tmp_path):
        """An empty facts list is a valid, clean result."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        from pipelines.classify import write_entries

        summary = write_entries(
            facts=[],
            doc_id=100,
            dimension="people",
            band=None,
            db_path=db_path,
        )
        assert isinstance(summary, Success)
        assert summary.value.clean is True
