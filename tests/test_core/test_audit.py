from __future__ import annotations


import pytest
import structlog

import core.audit as audit
import storage.audit_log as audit_log
from core.confidence import AIDecision
from core.logging_setup import new_correlation_id
from core.result import Failure, Success
from storage.db import init_db


def _decision(**kwargs: object) -> AIDecision:
    defaults: dict[str, object] = dict(
        action="classify:Domain/Test",
        confidence=0.90,
        reasoning="Strong keyword match.",
        source_ids=["inbox/note.md"],
    )
    defaults.update(kwargs)
    return AIDecision(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "kb.db"
    init_db(path)
    return path


def test_write_lifts_aidecision(db):
    structlog.contextvars.bind_contextvars(correlation_id="lift-test")
    decision = _decision()

    result = audit.write(decision, pipeline="capture", stage="classify", outcome="AUTO", db_path=db)
    assert isinstance(result, Success)

    rows = audit_log.query(correlation_id="lift-test", db_path=db)
    assert isinstance(rows, Success)
    entry = rows.value[0]
    assert entry.decision == decision.action
    assert entry.confidence == decision.confidence
    assert entry.reasoning == decision.reasoning
    assert entry.source_ids == list(decision.source_ids)
    assert entry.pipeline == "capture"
    assert entry.stage == "classify"
    assert entry.outcome == "AUTO"


def test_write_propagates_failure(db):
    structlog.contextvars.clear_contextvars()
    decision = _decision()

    # No correlation_id in context → audit_log.append returns Failure
    result = audit.write(decision, pipeline="capture", stage="classify", outcome="AUTO", db_path=db)
    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert "missing correlation_id" in result.error


def test_write_pulls_correlation_id_via_contextvars(db):
    cid = new_correlation_id()
    decision = _decision()

    result = audit.write(decision, pipeline="smoke", stage="test", outcome="AUTO", db_path=db)
    assert isinstance(result, Success)

    rows = audit_log.query(correlation_id=cid, db_path=db)
    assert isinstance(rows, Success)
    assert len(rows.value) == 1
    assert rows.value[0].correlation_id == cid
