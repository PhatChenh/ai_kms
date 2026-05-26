from __future__ import annotations

from pathlib import Path

import storage.audit_log as audit_log
from core.confidence import AIDecision
from core.result import Result
from storage.audit_log import AuditEntry


def write(
    decision: AIDecision,
    *,
    pipeline: str,
    stage: str,
    outcome: str,
    db_path: Path | None = None,
) -> Result[int]:
    """Record an AI decision in the audit log.

    Args:
        decision: The AIDecision produced by a pipeline stage.
        pipeline: Name of the pipeline that made the decision (e.g. "capture").
        stage: Name of the stage within the pipeline (e.g. "classify").
        outcome: Result of the routing gate (e.g. "AUTO", "SUGGEST", "CLUELESS").

    Returns:
        Success(rowid) on insert, or Failure if the insert fails or
        correlation_id is missing from contextvars.
    """
    entry = AuditEntry(
        pipeline=pipeline,
        stage=stage,
        source_ids=tuple(decision.source_ids),
        decision=decision.action,
        confidence=decision.confidence,
        reasoning=decision.reasoning,
        outcome=outcome,
    )
    return audit_log.append(entry, db_path=db_path)
