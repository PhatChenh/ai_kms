"""Tests for classify orchestrator — orchestrate() function."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from core.result import Success, Failure
from storage.db import init_db


# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------


@dataclass
class _FakeCompleteResult:
    content: str


class _FakeProvider:
    """Stub provider for testing the orchestrator."""

    def __init__(self, replies: list[str] | None = None):
        self._replies = replies or []
        self._call_index = 0

    async def complete(self, system: str, user: str):
        if self._call_index >= len(self._replies):
            return Failure("no more replies", recoverable=True, context={})
        reply = self._replies[self._call_index]
        self._call_index += 1
        return Success(_FakeCompleteResult(content=reply))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(tmp_path: Path, max_retries: int = 3) -> "MainConfig":
    from core.config import MainConfig, VaultConfig, ClassifyConfig

    return MainConfig(
        vault=VaultConfig(root=str(tmp_path)),
        classify=ClassifyConfig(max_retries=max_retries),
    )


def _seed_doc(
    db_path: Path,
    vault_path: str = "test.md",
    full_body: str = "Sample document text for testing.",
    content_hash: str = "abc123",
    classify_attempts: int = 0,
    classify_last_error: str | None = None,
    status: str | None = None,
    classify_content_hash: str | None = None,
) -> int:
    """Insert a documents row and return its id."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO documents (vault_path, title, full_body, content_hash,
           classify_attempts, classify_last_error, status, classify_content_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            vault_path,
            vault_path,
            full_body,
            content_hash,
            classify_attempts,
            classify_last_error,
            status,
            classify_content_hash,
        ),
    )
    conn.commit()
    doc_id = conn.execute(
        "SELECT id FROM documents WHERE vault_path = ?", (vault_path,)
    ).fetchone()[0]
    conn.close()
    return doc_id


def _valid_extract_reply(facts: list[dict] | None = None) -> str:
    """Return a valid entity_extract JSON reply."""
    if facts is None:
        facts = [
            {
                "action": "new",
                "entity": "Anthony",
                "tag": "other",
                "fact": "Anthony leads Movie Q2.",
                "confidence": 0.9,
            }
        ]
    return json.dumps(facts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOrchestratorHappyPath:
    """Phase 7 — orchestrator happy path."""

    async def test_full_success_stamps_and_clears_retry(self, tmp_path, monkeypatch):
        """Every dimension succeeds → stamp, retry cleared, one audit per dim."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        doc_id = _seed_doc(db_path)

        from pipelines.classify_orchestrator import orchestrate

        # Fake provider returning valid facts for every dimension call
        import pipelines.classify_extract as _pc

        provider = _FakeProvider(
            replies=[
                _valid_extract_reply(),
                _valid_extract_reply(),
                _valid_extract_reply(),
            ]
        )
        _pc.get_provider = lambda task, cfg: provider

        config = _cfg(tmp_path)

        from core.config import ConfidenceBand

        band = ConfidenceBand(auto=0.85, suggest=0.60)

        # Verify the patch is in effect
        assert _pc.get_provider("classify", config) is provider
        result = await orchestrate(doc_id, config=config, db_path=db_path, band=band)

        assert isinstance(result, Success), f"Expected Success, got {result}"
        assert result.value == "stamped", f"Expected 'stamped', got {result.value!r}"

        # Doc should be stamped (classify_content_hash == content_hash)
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT classify_content_hash, classify_attempts, classify_last_error "
            "FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        conn.close()
        assert row[0] == "abc123", "classify_content_hash should equal content_hash"
        assert row[1] == 0, "classify_attempts should be cleared"
        assert row[2] is None, "classify_last_error should be None"

    async def test_doc_not_returned_by_find_unclassified_after_stamp(
        self, tmp_path, monkeypatch
    ):
        """After a clean orchestrate, find_unclassified no longer returns the doc."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        doc_id = _seed_doc(db_path)

        from pipelines.classify_orchestrator import orchestrate
        from storage.documents_classify import find_unclassified

        provider = _FakeProvider(
            replies=[
                _valid_extract_reply(),
                _valid_extract_reply(),
                _valid_extract_reply(),
            ]
        )
        import pipelines.classify_extract as _pc

        _pc.get_provider = lambda task, cfg: provider

        config = _cfg(tmp_path)
        result = await orchestrate(doc_id, config=config, db_path=db_path)
        assert isinstance(result, Success)

        unclassified = find_unclassified(db_path=db_path)
        assert isinstance(unclassified, Success)
        assert doc_id not in unclassified.value


class TestOrchestratorRetryPath:
    """Phase 7 — retry and park paths."""

    async def test_failure_increments_attempts_and_saves_error(
        self, tmp_path, monkeypatch
    ):
        """A failed dimension → no stamp, attempts++, error saved, still discoverable."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        doc_id = _seed_doc(db_path)

        from pipelines.classify_orchestrator import orchestrate
        from storage.documents_classify import find_unclassified

        # First dimension: gives unparseable reply
        provider = _FakeProvider(
            replies=[
                "not valid json!",  # first dimension fails
                _valid_extract_reply(),  # second dimension succeeds (but all_clean stays False)
            ]
        )
        import pipelines.classify_extract as _pc

        _pc.get_provider = lambda task, cfg: provider

        config = _cfg(tmp_path)
        result = await orchestrate(doc_id, config=config, db_path=db_path)
        assert isinstance(
            result, Success
        )  # orchestrate itself succeeds (retry state saved)

        # No stamp
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT classify_content_hash, classify_attempts, classify_last_error "
            "FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        conn.close()
        assert row[0] is None or row[0] != "abc123", "should NOT be stamped"
        assert row[1] >= 1, "classify_attempts should be incremented"
        assert row[2] is not None, "classify_last_error should be saved"

        # Still discoverable
        unclassified = find_unclassified(db_path=db_path)
        assert isinstance(unclassified, Success)
        assert doc_id in unclassified.value

    async def test_at_cap_parks_document(self, tmp_path, monkeypatch):
        """When attempts reach max_retries, doc is parked as needs-review."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        doc_id = _seed_doc(
            db_path,
            classify_attempts=2,  # One below cap (max_retries=3)
            classify_last_error="Previous parse error",
        )

        from pipelines.classify_orchestrator import orchestrate
        from storage.documents_classify import find_unclassified

        provider = _FakeProvider(
            replies=[
                "bad json!",  # fail
                _valid_extract_reply(),
            ]
        )
        import pipelines.classify_extract as _pc

        _pc.get_provider = lambda task, cfg: provider

        config = _cfg(tmp_path, max_retries=3)
        result = await orchestrate(doc_id, config=config, db_path=db_path)
        assert isinstance(result, Success)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT status, classify_attempts FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "needs-review", f"Should be parked, got {row[0]}"
        assert row[1] >= 3, f"Should have at least 3 attempts, got {row[1]}"

        # Work finder skips parked docs
        unclassified = find_unclassified(db_path=db_path)
        assert isinstance(unclassified, Success)
        assert doc_id not in unclassified.value

    async def test_feedback_passed_on_retry(self, tmp_path, monkeypatch):
        """On retry, the saved classify_last_error is passed as feedback to extract."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        doc_id = _seed_doc(
            db_path,
            classify_attempts=1,
            classify_last_error="JSON parse error: trailing comma",
        )

        from pipelines.classify_orchestrator import orchestrate

        provider = _FakeProvider(
            replies=[
                _valid_extract_reply(),
                _valid_extract_reply(),
            ]
        )

        # Spy on extract to capture feedback
        captured_feedback = []

        import pipelines.classify_extract as _pc

        _pc.get_provider = lambda task, cfg: provider

        # Patch extract on classify_orchestrator (where it's bound at import time)
        import pipelines.classify_orchestrator as classify_orch_mod

        _original_extract = classify_orch_mod.extract

        async def spy_extract(
            dimension, text, existing_facts, guidance, feedback, config
        ):
            captured_feedback.append(feedback)
            return await _original_extract(
                dimension, text, existing_facts, guidance, feedback, config
            )

        classify_orch_mod.extract = spy_extract

        config = _cfg(tmp_path)
        await orchestrate(doc_id, config=config, db_path=db_path)

        # At least one call should have the saved error as feedback
        assert any("JSON parse error" in fb for fb in captured_feedback if fb), (
            f"Feedback not passed: {captured_feedback}"
        )


class TestOrchestratorCorrelationId:
    """Phase 7 — correlation id guard."""

    async def test_new_correlation_id_is_called_before_audit(
        self, tmp_path, monkeypatch
    ):
        """orchestrate calls new_correlation_id() before any audit write."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        doc_id = _seed_doc(db_path)

        from pipelines.classify_orchestrator import orchestrate

        provider = _FakeProvider(
            replies=[
                _valid_extract_reply(),
                _valid_extract_reply(),
            ]
        )
        import pipelines.classify_extract as _pc

        _pc.get_provider = lambda task, cfg: provider

        # Spy on new_correlation_id
        call_count = 0
        import core.logging_setup

        _orig_new_cid = core.logging_setup.new_correlation_id

        def spy_new_cid():
            nonlocal call_count
            call_count += 1
            return _orig_new_cid()

        monkeypatch.setattr(core.logging_setup, "new_correlation_id", spy_new_cid)

        config = _cfg(tmp_path)
        await orchestrate(doc_id, config=config, db_path=db_path)

        assert call_count >= 1, "new_correlation_id should be called at least once"


class TestConsumerSeam:
    """Phase 7 — consumer calls orchestrate at the Slice B seam."""

    @pytest.mark.asyncio
    async def test_consumer_calls_orchestrate(self, tmp_path, monkeypatch):
        """The consumer coroutine now calls orchestrate() instead of stopping
        at the Slice B seam."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        doc_id = _seed_doc(db_path)

        from pipelines.classify import consumer
        import asyncio

        provider = _FakeProvider(
            replies=[
                _valid_extract_reply(),
                _valid_extract_reply(),
                _valid_extract_reply(),
            ]
        )
        import pipelines.classify_extract as _pc

        _pc.get_provider = lambda task, cfg: provider

        config = _cfg(tmp_path)
        queue: asyncio.Queue[int] = asyncio.Queue()
        queue.put_nowait(doc_id)

        # Run consumer for a short time — it should process the doc and stamp it
        worker = asyncio.create_task(consumer(queue, db_path, config))

        # Wait for queue to drain or timeout
        try:
            await asyncio.wait_for(queue.join(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

        # Doc should be stamped now
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT classify_content_hash FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "abc123", (
            "Consumer should have called orchestrate and stamped the doc"
        )
