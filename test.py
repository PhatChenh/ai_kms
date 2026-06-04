"""
Manual smoke test: storage layer end-to-end.

Run: uv run python test.py

Requires: no vault on disk needed. Creates ./data/kb.db if absent.
"""

from pathlib import Path

import core.audit as audit
import storage.audit_log as audit_log
from core.confidence import AIDecision
from core.logging_setup import new_correlation_id, setup_logging
from core.result import Success
from storage.db import init_db

# Hardcoded to match config.yaml `database.path` — avoids importing CONFIG,
# which validates vault root existence on load and fails when vault isn't set up.
DB_PATH = Path("./data/kb.db")

setup_logging(log_level="DEBUG", dev_mode=True)

result = init_db(DB_PATH)
if not isinstance(result, Success):
    raise RuntimeError(f"init_db failed: {result.error}")

correlation_id = new_correlation_id()

decision = AIDecision(
    action="classify:Domain/Movies",
    confidence=0.92,
    reasoning="Strong title match",
    source_ids=["inbox/x.md"],
)

for _ in range(10):
    r = audit.write(decision, pipeline="smoke", stage="classify", outcome="AUTO", db_path=DB_PATH)
    if not isinstance(r, Success):
        raise RuntimeError(f"audit.write failed: {r.error}")

rows = audit_log.query(correlation_id=correlation_id, db_path=DB_PATH)
if not isinstance(rows, Success):
    raise RuntimeError(f"query failed: {rows.error}")

assert len(rows.value) == 10, f"Expected 10 rows, got {len(rows.value)}"
print("10 entries written")
print(f"First entry: {rows.value[0]}")
