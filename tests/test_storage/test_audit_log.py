from __future__ import annotations

import sqlite3

import pytest
import structlog

from core.logging_setup import new_correlation_id
from core.result import Failure, Success
import storage.audit_log as audit_log
from storage.audit_log import AuditEntry
from storage.db import get_connection, init_db


def _entry(**kwargs: object) -> AuditEntry:
    defaults: dict[str, object] = dict(
        pipeline="test_pipe",
        stage="test_stage",
        source_ids=["inbox/note.md"],
        decision="classify:Domain/Test",
        confidence=0.90,
        reasoning="Test reasoning.",
        outcome="AUTO",
    )
    defaults.update(kwargs)
    return AuditEntry(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "kb.db"
    init_db(path)
    return path


def test_append_returns_rowid(db):
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(correlation_id="test-cid-1")
    r1 = audit_log.append(_entry(), db_path=db)
    r2 = audit_log.append(_entry(), db_path=db)
    assert isinstance(r1, Success) and r1.value == 1
    assert isinstance(r2, Success) and r2.value == 2


def test_append_pulls_correlation_id_from_contextvars(db):
    cid = new_correlation_id()
    result = audit_log.append(_entry(), db_path=db)
    assert isinstance(result, Success)
    with get_connection(db) as conn:
        row = conn.execute("SELECT correlation_id FROM audit_log WHERE id=?", (result.value,)).fetchone()
    assert row[0] == cid


def test_append_fails_when_correlation_id_missing(db):
    structlog.contextvars.clear_contextvars()
    result = audit_log.append(_entry(), db_path=db)
    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert "missing correlation_id" in result.error


def test_query_filters_by_date(db):
    structlog.contextvars.bind_contextvars(correlation_id="date-test")
    with get_connection(db) as conn:
        conn.execute(
            "INSERT INTO audit_log (pipeline, stage, source_ids, decision, confidence, "
            "reasoning, outcome, correlation_id, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("pipe", "s", '["x"]', "d", 0.9, "r", "AUTO", "date-test", "2026-01-01 00:00:00"),
        )
        conn.execute(
            "INSERT INTO audit_log (pipeline, stage, source_ids, decision, confidence, "
            "reasoning, outcome, correlation_id, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("pipe", "s", '["x"]', "d", 0.9, "r", "AUTO", "date-test", "2026-06-01 00:00:00"),
        )
    result = audit_log.query(date="2026-01-01", db_path=db)
    assert isinstance(result, Success)
    assert len(result.value) == 1


def test_query_filters_by_correlation_id(db):
    structlog.contextvars.bind_contextvars(correlation_id="cid-a")
    audit_log.append(_entry(), db_path=db)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(correlation_id="cid-b")
    audit_log.append(_entry(), db_path=db)

    result = audit_log.query(correlation_id="cid-a", db_path=db)
    assert isinstance(result, Success)
    assert len(result.value) == 1
    assert result.value[0].correlation_id == "cid-a"
    assert result.value[0].pipeline == "test_pipe"


def test_source_ids_round_trip(db):
    structlog.contextvars.bind_contextvars(correlation_id="rt-test")
    paths = ["inbox/a.md", "inbox/b.md"]
    result = audit_log.append(_entry(source_ids=paths), db_path=db)
    assert isinstance(result, Success)
    qresult = audit_log.query(correlation_id="rt-test", db_path=db)
    assert isinstance(qresult, Success)
    assert qresult.value[0].source_ids == paths
    assert isinstance(qresult.value[0].source_ids, list)


def test_append_only_at_module_level():
    import storage.audit_log as mod
    public = [name for name in dir(mod) if not name.startswith("_")]
    assert not any(name.startswith(("update", "delete")) for name in public)


def test_trigger_blocks_direct_update(db):
    structlog.contextvars.bind_contextvars(correlation_id="trigger-test")
    audit_log.append(_entry(), db_path=db)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with get_connection(db) as conn:
            conn.execute("UPDATE audit_log SET pipeline='x' WHERE id=1")
