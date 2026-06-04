import pytest

import core.audit as audit
import storage.audit_log as audit_log
from core.confidence import AIDecision
from core.logging_setup import new_correlation_id, setup_logging
from core.result import Success
from storage.db import init_db


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "kb.db"
    init_db(path)
    return path


@pytest.mark.smoke
def test_end_to_end_write_and_query(db):
    setup_logging(log_level="DEBUG", dev_mode=False)
    correlation_id = new_correlation_id()

    decision = AIDecision(
        action="classify:Domain/Movies",
        confidence=0.92,
        reasoning="Strong title match",
        source_ids=["inbox/x.md"],
    )

    for _ in range(10):
        r = audit.write(decision, pipeline="smoke", stage="classify", outcome="AUTO", db_path=db)
        assert isinstance(r, Success)

    rows = audit_log.query(correlation_id=correlation_id, db_path=db)
    assert isinstance(rows, Success)
    assert len(rows.value) == 10

    first = rows.value[0]
    assert first.decision == "classify:Domain/Movies"
    assert first.confidence == 0.92
    assert first.pipeline == "smoke"
    assert first.correlation_id == correlation_id
