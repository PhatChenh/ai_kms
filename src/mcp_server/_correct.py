"""kms_correct backing — apply corrections to knowledge entries."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from core.audit import write as audit_write
from core.confidence import AIDecision
from core.logging_setup import new_correlation_id
from core.result import Failure, Result, Success


def correct_entry(
    entry_id: int,
    operation: str,
    *,
    new_fact: str | None = None,
    new_tag: str | None = None,
    new_entity: str | None = None,
    reason: str | None = None,
    db_path: Path | None = None,
) -> Result[dict]:
    """Apply a correction operation to a knowledge entry.

    Valid operations: ``retire``, ``edit_fact``, ``change_tag``,
    ``change_entity``, ``promote``, ``un_retire``.

    Every correction is audited via ``core.audit.write`` with a synthetic
    ``AIDecision`` (confidence 1.0 — it's a human directive).
    """
    from storage.knowledge_entries import (  # noqa: C0415
        get_entry_by_id,
        retire,
        upsert,
    )

    # -- validate entry exists ------------------------------------------------
    match get_entry_by_id(entry_id, db_path=db_path):
        case Success(value=None):
            return Failure(
                f"Entry {entry_id} not found", recoverable=False
            )
        case Failure() as f:
            return f
        case Success(value=entry):
            pass

    # -- apply operation -----------------------------------------------------
    if operation == "retire":
        if reason is None:
            return Failure(
                "reason is required for retire", recoverable=False
            )
        match retire(entry_id, reason, db_path=db_path):
            case Failure() as f:
                return f

    elif operation in (
        "edit_fact", "change_tag", "change_entity",
        "promote", "un_retire",
    ):
        # Build updated entry from the existing frozen dataclass
        fields = asdict(entry)  # noqa: F821  (entry is bound above)
        if operation == "edit_fact" and new_fact is not None:
            fields["fact"] = new_fact
        elif operation == "change_tag" and new_tag is not None:
            fields["tag"] = new_tag
        elif operation == "change_entity" and new_entity is not None:
            fields["entity"] = new_entity
        elif operation == "promote":
            fields["status"] = "confident"
        elif operation == "un_retire":
            fields["status"] = "confident"
            fields["reasoning"] = None

        updated = type(entry)(**fields)
        match upsert(updated, db_path=db_path):
            case Failure() as f:
                return f
    else:
        return Failure(
            f"Unknown operation: {operation}", recoverable=False
        )

    # -- audit ----------------------------------------------------------------
    new_correlation_id()
    decision = AIDecision(
        action=f"correct:{operation}",
        confidence=1.0,
        reasoning=reason or f"Consumer AI requested {operation}",
        source_ids=[str(entry_id)],
    )
    audit_write(decision, pipeline="correct", stage=operation, outcome="APPLIED")

    return Success(
        {"entry_id": entry_id, "operation": operation, "result": "applied"}
    )
