"""
tests/test_core/test_pipeline.py

End-to-end behavioral tests for core/pipeline.run_pipeline.
All tests use real DB (tmp_path), real audit_log, and trivial async stages.
No production code is mocked except config (MagicMock — no vault needed).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from structlog.contextvars import get_contextvars

import core.audit as audit
import storage.audit_log as audit_log
from core.confidence import AIDecision
from core.pipeline import PipelineContext, run_pipeline
from core.result import Failure, Result, Success
from storage.db import init_db


# ---------------------------------------------------------------------------
# Trivial stages
# ---------------------------------------------------------------------------


async def add_one(value: int, ctx: PipelineContext) -> Result[int]:
    decision = AIDecision(action="add_one", confidence=1.0, reasoning="test", source_ids=[])
    audit.write(decision, pipeline="test", stage="add_one", outcome="AUTO", db_path=ctx.db_path)
    return Success(value + 1)


async def double(value: int, ctx: PipelineContext) -> Result[int]:
    decision = AIDecision(action="double", confidence=1.0, reasoning="test", source_ids=[])
    audit.write(decision, pipeline="test", stage="double", outcome="AUTO", db_path=ctx.db_path)
    return Success(value * 2)


async def to_string(value: int, ctx: PipelineContext) -> Result[str]:
    decision = AIDecision(action="to_string", confidence=1.0, reasoning="test", source_ids=[])
    audit.write(decision, pipeline="test", stage="to_string", outcome="AUTO", db_path=ctx.db_path)
    return Success(str(value))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "kb.db"
    init_db(path)
    return path


@pytest.fixture()
def ctx(db):
    return PipelineContext(config=MagicMock(), correlation_id="test-cid-123", db_path=db)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_end_to_end(ctx):
    """add_one → double → to_string on input 1 produces Success("4")."""
    result = await run_pipeline("test", [add_one, double, to_string], 1, context=ctx)

    assert isinstance(result, Success)
    assert result.value == "4"


@pytest.mark.asyncio
async def test_failure_in_stage_halts_pipeline(ctx):
    """Failure in stage 2 stops execution — stage 3 must not run."""
    stage3_ran = False

    async def fail_stage(value: int, c: PipelineContext) -> Result[int]:
        return Failure(error="boom", recoverable=False, context={})

    async def sentinel_stage(value: int, c: PipelineContext) -> Result[int]:
        nonlocal stage3_ran
        stage3_ran = True
        return Success(value)

    result = await run_pipeline(
        "test", [add_one, fail_stage, sentinel_stage], 1, context=ctx
    )

    assert isinstance(result, Failure)
    assert result.error == "boom"
    assert stage3_ran is False


@pytest.mark.asyncio
async def test_all_audit_entries_share_correlation_id(ctx, db):
    """Every stage writes an audit entry with the same correlation_id."""
    await run_pipeline("test", [add_one, double, to_string], 1, context=ctx)

    query_result = audit_log.query(correlation_id="test-cid-123", db_path=db)
    assert isinstance(query_result, Success)
    entries = query_result.value

    assert len(entries) == 3
    assert all(e.correlation_id == "test-cid-123" for e in entries)
    assert [e.stage for e in entries] == ["add_one", "double", "to_string"]


@pytest.mark.asyncio
async def test_empty_stages_returns_initial_input(ctx):
    """Empty stage list returns Success wrapping the initial input unchanged."""
    result = await run_pipeline("empty", [], 42, context=ctx)

    assert isinstance(result, Success)
    assert result.value == 42


@pytest.mark.asyncio
async def test_raising_stage_returns_failure_not_exception(ctx):
    """Stage that raises RuntimeError is caught — run_pipeline returns Failure."""

    async def exploding_stage(value: int, c: PipelineContext) -> Result[int]:
        raise RuntimeError("unexpected")

    result = await run_pipeline("test", [exploding_stage], 0, context=ctx)

    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert "unexpected" in result.error


class TestPipelineContextTaxonomy:
    def test_taxonomy_none_is_accepted(self, db) -> None:
        ctx = PipelineContext(
            config=MagicMock(),
            correlation_id="x",
            db_path=db,
            taxonomy=None,
        )
        assert ctx.taxonomy is None

    def test_explicit_taxonomy_accessible_on_ctx(self, db) -> None:
        from core.tags import TagTaxonomy
        taxonomy = TagTaxonomy(
            allowed_types=frozenset(["report"]),
            valid_domains=frozenset(["finance"]),
        )
        ctx = PipelineContext(
            config=MagicMock(),
            correlation_id="x",
            db_path=db,
            taxonomy=taxonomy,
        )
        assert ctx.taxonomy is taxonomy
        assert ctx.taxonomy.valid_domains == frozenset(["finance"])


@pytest.mark.asyncio
async def test_no_explicit_context_generates_correlation_id(monkeypatch):
    """run_pipeline without context auto-generates a correlation_id in contextvars."""
    import core.config as config_mod

    # Bypass vault-root validation: seed the module-level cache so __getattr__
    # returns our mock without calling load_config().
    monkeypatch.setattr(config_mod, "_CONFIG", MagicMock())

    async def passthrough(value: int, ctx: PipelineContext) -> Result[int]:
        return Success(value)

    result = await run_pipeline("test", [passthrough], 0)

    assert isinstance(result, Success)
    cid = get_contextvars().get("correlation_id", "")
    assert cid != ""
