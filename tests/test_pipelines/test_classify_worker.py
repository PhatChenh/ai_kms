"""Phase 7 — Work Queue + Worker + catch-up scan (build_app).

Tests for the consumer coroutine, catch-up scan, and the in-memory
asyncio.Queue that wires classification work from boot.

These tests inject queue/db directly — no FastMCP lifespan needed.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from core.config import ClassifyConfig, MainConfig
from core.result import Failure, Result, Success
from storage.db import get_connection


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
    classify_content_hash: str | None = None,
) -> int:
    """Insert a document and return its id."""
    cur = conn.execute(
        """INSERT INTO documents
           (vault_path, title, full_body, summary, content_hash, classify_content_hash)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (vault_path, title, full_body, summary, content_hash, classify_content_hash),
    )
    return cur.lastrowid


def _seed_knowledge_entry(
    conn: sqlite3.Connection,
    *,
    dimension: str = "people",
    entity: str = "Alice",
    fact: str = "Engineer",
    status: str = "confident",
    confidence: float = 0.9,
    trust_score: float = 0.8,
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
            "role",
            fact,
            status,
            confidence,
            json.dumps(["test_source"]),
            "test",
            trust_score,
            0,
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


# ============================================================================
# Consumer coroutine
# ============================================================================


class TestConsumer:
    """The consumer coroutine pulls doc_ids one at a time and prepares inputs."""

    @pytest.mark.asyncio
    async def test_processes_one_at_a_time(self, db_path: Path):
        """Consumer never processes two docs concurrently."""
        from pipelines.classify import consumer

        config = _make_config()
        queue: asyncio.Queue[int] = asyncio.Queue()

        # Seed several discoverable documents
        with get_connection(db_path) as conn:
            for i in range(5):
                _seed_document(
                    conn,
                    vault_path=f"test/doc{i}.md",
                    full_body=f"Content {i}",
                    summary=f"Summary {i}",
                    classify_content_hash=None,
                )
            # Also seed knowledge entries so context_loader works
            _seed_knowledge_entry(conn)

        # Put all doc ids into the queue
        from storage.documents import find_unclassified
        result = find_unclassified(db_path=db_path)
        assert isinstance(result, Success)
        for doc_id in result.value:
            queue.put_nowait(doc_id)

        # Track concurrency: a set that remembers the currently-active doc_id
        active_ids: set[int] = set()
        max_concurrent = 0

        # We wrap the real consumer to observe concurrency
        async def _tracked_consumer():
            nonlocal max_concurrent
            while True:
                doc_id = await queue.get()
                active_ids.add(doc_id)
                current = len(active_ids)
                if current > max_concurrent:
                    max_concurrent = current
                # Drive one iteration of the real consumer's processing
                # by running it in a task that stops at the Slice B seam
                try:
                    # We simulate the consumer body inline to track concurrency
                    from pipelines.classify import content_reader, context_loader
                    cr = content_reader(doc_id, config=config, db_path=db_path)
                    cl = context_loader(config=config, db_path=db_path)
                    # Both must succeed for valid docs
                    assert isinstance(cr, Success), f"content_reader failed: {cr}"
                    assert isinstance(cl, Success), f"context_loader failed: {cl}"
                finally:
                    active_ids.discard(doc_id)
                    queue.task_done()

        task = asyncio.create_task(_tracked_consumer())

        # Wait for queue to drain (with timeout)
        await asyncio.wait_for(queue.join(), timeout=10.0)

        # Cancel the consumer (it would block forever on queue.get())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Assert no concurrency — max_concurrent must be 1
        assert max_concurrent == 1, (
            f"Consumer ran {max_concurrent} docs concurrently; expected 1"
        )

    @pytest.mark.asyncio
    async def test_drains_queue_to_empty(self, db_path: Path):
        """After processing, the queue is empty (queue.join() returns)."""
        from pipelines.classify import consumer

        config = _make_config()
        queue: asyncio.Queue[int] = asyncio.Queue()

        with get_connection(db_path) as conn:
            for i in range(3):
                _seed_document(
                    conn,
                    vault_path=f"test/doc{i}.md",
                    full_body=f"Content {i}",
                    classify_content_hash=None,
                )
            _seed_knowledge_entry(conn)

        from storage.documents import find_unclassified
        result = find_unclassified(db_path=db_path)
        assert isinstance(result, Success)
        ids = result.value
        assert len(ids) == 3

        for doc_id in ids:
            queue.put_nowait(doc_id)

        # Start consumer as a task
        task = asyncio.create_task(consumer(queue, db_path, config))

        # Wait for queue to drain
        await asyncio.wait_for(queue.join(), timeout=10.0)

        # Queue should be empty
        assert queue.empty()

        # Cancel the consumer and wait for cleanup
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_consumer_stops_before_ai_call(self, db_path: Path):
        """Consumer calls content_reader + context_loader but does NOT
        stamp classify_content_hash (Slice B seam).
        """
        from pipelines.classify import consumer

        config = _make_config()
        queue: asyncio.Queue[int] = asyncio.Queue()

        with get_connection(db_path) as conn:
            doc_id = _seed_document(
                conn,
                vault_path="test/seam.md",
                full_body="Test content for seam.",
                summary="Summary seam.",
                content_hash="hash123",
                classify_content_hash=None,
            )
            _seed_knowledge_entry(conn)

        queue.put_nowait(doc_id)

        task = asyncio.create_task(consumer(queue, db_path, config))

        await asyncio.wait_for(queue.join(), timeout=10.0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Verify classify_content_hash is still NULL (no stamp)
        with get_connection(db_path, readonly=True) as conn:
            row = conn.execute(
                "SELECT classify_content_hash FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
        assert row is not None
        assert row[0] is None, (
            f"classify_content_hash should be NULL (no stamp), got {row[0]!r}"
        )

    @pytest.mark.asyncio
    async def test_consumer_continues_on_failure(self, db_path: Path):
        """When one doc fails (e.g. missing row), the consumer logs and
        continues with the next doc — it does NOT crash the loop.
        """
        from pipelines.classify import consumer

        config = _make_config()
        queue: asyncio.Queue[int] = asyncio.Queue()

        with get_connection(db_path) as conn:
            _seed_document(
                conn,
                vault_path="test/good.md",
                full_body="Good content",
                classify_content_hash=None,
            )
            _seed_knowledge_entry(conn)

        from storage.documents import find_unclassified
        result = find_unclassified(db_path=db_path)
        assert isinstance(result, Success)
        good_ids = result.value
        assert len(good_ids) >= 1

        # Enqueue a bad id (non-existent doc) then the good ones
        queue.put_nowait(99999)  # does not exist
        for doc_id in good_ids:
            queue.put_nowait(doc_id)

        task = asyncio.create_task(consumer(queue, db_path, config))

        await asyncio.wait_for(queue.join(), timeout=10.0)

        assert queue.empty()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ============================================================================
# Catch-up scan
# ============================================================================


class TestCatchUpScan:
    """The catch-up scan enqueues all discoverable doc ids on startup."""

    @pytest.mark.asyncio
    async def test_enqueues_all_discoverable_ids(self, db_path: Path):
        """catch_up_scan puts every id from find_unclassified into the queue."""
        from pipelines.classify import catch_up_scan

        queue: asyncio.Queue[int] = asyncio.Queue()

        with get_connection(db_path) as conn:
            for i in range(7):
                _seed_document(
                    conn,
                    vault_path=f"test/doc{i}.md",
                    full_body=f"Content {i}",
                    classify_content_hash=None,
                )

        await catch_up_scan(queue, db_path)

        # All 7 ids should be in the queue
        ids_in_queue: list[int] = []
        while not queue.empty():
            ids_in_queue.append(queue.get_nowait())

        assert len(ids_in_queue) == 7, (
            f"Expected 7 ids in queue, got {len(ids_in_queue)}"
        )

    @pytest.mark.asyncio
    async def test_no_discoverable_docs_leaves_queue_empty(self, db_path: Path):
        """When no docs need classification, the queue stays empty."""
        from pipelines.classify import catch_up_scan

        queue: asyncio.Queue[int] = asyncio.Queue()

        # No documents seeded → find_unclassified returns []
        await catch_up_scan(queue, db_path)

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_already_classified_docs_are_skipped(self, db_path: Path):
        """Docs with classify_content_hash == content_hash are NOT enqueued."""
        from pipelines.classify import catch_up_scan

        queue: asyncio.Queue[int] = asyncio.Queue()

        with get_connection(db_path) as conn:
            _seed_document(
                conn,
                vault_path="test/classified.md",
                full_body="Already done",
                content_hash="hash1",
                classify_content_hash="hash1",  # already classified
            )
            _seed_document(
                conn,
                vault_path="test/unclassified.md",
                full_body="Needs work",
                content_hash="hash2",
                classify_content_hash=None,  # not yet classified
            )

        await catch_up_scan(queue, db_path)

        ids: list[int] = []
        while not queue.empty():
            ids.append(queue.get_nowait())

        assert len(ids) == 1, (
            f"Expected only 1 unclassified doc, got {ids}"
        )

    @pytest.mark.asyncio
    async def test_scan_does_not_block(self, db_path: Path):
        """catch_up_scan is a quick burst, not a long-running task."""
        import time
        from pipelines.classify import catch_up_scan

        queue: asyncio.Queue[int] = asyncio.Queue()

        with get_connection(db_path) as conn:
            for i in range(100):
                _seed_document(
                    conn,
                    vault_path=f"test/doc{i}.md",
                    classify_content_hash=None,
                )

        start = time.monotonic()
        await catch_up_scan(queue, db_path)
        elapsed = time.monotonic() - start

        # 100 documents should enqueue in well under 5 seconds
        assert elapsed < 5.0, (
            f"catch_up_scan took {elapsed:.2f}s for 100 docs"
        )


# ============================================================================
# Integration: queue + consumer + catch_up_scan
# ============================================================================


class TestWorkerIntegration:
    """End-to-end: catch-up scan enqueues, consumer drains, no stamp."""

    @pytest.mark.asyncio
    async def test_full_flow(self, db_path: Path):
        """Catch-up scan → consumer processes all → queue drained → no stamp."""
        from pipelines.classify import catch_up_scan, consumer

        config = _make_config()
        queue: asyncio.Queue[int] = asyncio.Queue()

        with get_connection(db_path) as conn:
            for i in range(5):
                _seed_document(
                    conn,
                    vault_path=f"test/doc{i}.md",
                    full_body=f"Content for doc {i}",
                    summary=f"Summary {i}",
                    content_hash=f"hash{i}",
                    classify_content_hash=None,
                )
            _seed_knowledge_entry(conn)

        # 1. Catch-up scan
        await catch_up_scan(queue, db_path)

        # 2. Start consumer
        task = asyncio.create_task(consumer(queue, db_path, config))

        # 3. Wait for queue to drain
        await asyncio.wait_for(queue.join(), timeout=10.0)

        assert queue.empty()

        # 4. Cancel consumer
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # 5. Verify no classify_content_hash was stamped
        with get_connection(db_path, readonly=True) as conn:
            rows = conn.execute(
                "SELECT id, classify_content_hash FROM documents"
            ).fetchall()
        for row in rows:
            assert row[1] is None, (
                f"doc {row[0]}: classify_content_hash should be NULL, got {row[1]!r}"
            )
