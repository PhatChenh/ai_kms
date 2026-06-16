"""kms_correct backing — apply corrections to knowledge entries."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from core.audit import write as audit_write
from core.config import CONFIG
from core.confidence import AIDecision
from core.logging_setup import new_correlation_id
from core.result import Failure, Result, Success
from pipelines.trust import adjust_trust


def correct_entry(
    entry_id: int,
    operation: str,
    *,
    new_fact: str | None = None,
    new_tag: str | None = None,
    new_entity: str | None = None,
    reason: str | None = None,
    reason_category: str | None = None,
    feedback: str | None = None,
    db_path: Path | None = None,
) -> Result[dict]:
    """Apply a correction operation to a knowledge entry.

    Valid operations: ``confirm``, ``reject``, ``revise`` (Phase 10),
    ``retire``, ``edit_fact``, ``change_tag``, ``change_entity``,
    ``promote``, ``un_retire`` (Phase 9).

    Every correction is audited via ``core.audit.write`` with a synthetic
    ``AIDecision`` (confidence 1.0 — it's a human directive).

    # COUPLING: reject and revise span multiple independent get_connection()
    # calls (upsert + retire / retire + upsert).  If the second call fails
    # the first call's changes are already committed, leaving the entry in
    # an intermediate state (e.g. lowered trust but not retired).  The
    # codebase uses per-call-connection semantics systemically; a single
    # explicit transaction would require passing a connection through all
    # called functions, which is deferred to a future connection-manager
    # refactor (TD-P10-TXN-01).
    """
    from storage.knowledge_entries import (  # noqa: C0415
        get_entry_by_id,
        retire,
        upsert,
    )

    # -- validate entry exists ------------------------------------------------
    match get_entry_by_id(entry_id, db_path=db_path):
        case Success(value=None):
            return Failure(f"Entry {entry_id} not found", recoverable=False)
        case Failure() as f:
            return f
        case Success(value=entry):
            pass

    old_trust = entry.trust_score

    def _safe_adjust_trust(op: str) -> float | Failure:
        """Call adjust_trust and unwrap, returning Failure on error."""
        result = adjust_trust(old_trust, op, CONFIG.main.self_learning)
        if isinstance(result, Failure):
            return result
        return result.value

    # -- apply operation -----------------------------------------------------
    if operation == "retire":
        if reason is None:
            return Failure("reason is required for retire", recoverable=False)
        match retire(entry_id, reason, db_path=db_path):
            case Failure() as f:
                return f

    elif operation in (
        "edit_fact",
        "change_tag",
        "change_entity",
        "promote",
        "un_retire",
    ):
        if operation == "edit_fact" and new_fact is None:
            return Failure("new_fact is required for edit_fact", recoverable=False)
        if operation == "change_tag" and new_tag is None:
            return Failure("new_tag is required for change_tag", recoverable=False)
        if operation == "change_entity" and new_entity is None:
            return Failure(
                "new_entity is required for change_entity", recoverable=False
            )

        fields = asdict(entry)
        if operation == "edit_fact":
            fields["fact"] = new_fact
        elif operation == "change_tag":
            fields["tag"] = new_tag
        elif operation == "change_entity":
            fields["entity"] = new_entity
        elif operation == "promote":
            fields["status"] = "confident"
            new_trust = _safe_adjust_trust("confirm")
            if isinstance(new_trust, Failure):
                return new_trust
            fields["trust_score"] = new_trust
        elif operation == "un_retire":
            fields["status"] = "confident"
            fields["reasoning"] = None

        updated = type(entry)(**fields)
        match upsert(updated, db_path=db_path):
            case Failure() as f:
                return f

    elif operation == "confirm":
        # Confirm = promote + trust bump
        fields = asdict(entry)
        fields["status"] = "confident"
        new_trust = _safe_adjust_trust("confirm")
        if isinstance(new_trust, Failure):
            return new_trust
        fields["trust_score"] = new_trust
        updated = type(entry)(**fields)
        match upsert(updated, db_path=db_path):
            case Failure() as f:
                return f

    elif operation == "reject":
        # Reject = retire + trust drop
        new_trust = _safe_adjust_trust("reject")
        if isinstance(new_trust, Failure):
            return new_trust
        fields = asdict(entry)
        fields["trust_score"] = new_trust
        updated = type(entry)(**fields)
        match upsert(updated, db_path=db_path):
            case Failure() as f:
                return f
        if reason is None:
            reason = "Rejected by user"
        match retire(entry_id, reason, db_path=db_path):
            case Failure() as f:
                return f

    elif operation == "revise":
        if new_fact is None:
            return Failure("new_fact is required for revise", recoverable=False)
        # Retire old entry
        match retire(entry_id, reason or "Revised by user", db_path=db_path):
            case Failure() as f:
                return f
        # Create new entry with revised fact at trust_revise_base
        new_trust = _safe_adjust_trust("revise")
        if isinstance(new_trust, Failure):
            return new_trust
        new_entry = type(entry)(
            id=None,
            dimension=entry.dimension,
            entity=entry.entity,
            tag=entry.tag,
            fact=new_fact,
            confidence=entry.confidence,
            sources=entry.sources,
            reasoning=reason or f"Revised from entry {entry_id}",
            trust_score=new_trust,
        )
        match upsert(new_entry, db_path=db_path):
            case Failure() as f:
                return f
            case Success(value=new_id):
                pass

    else:
        return Failure(f"Unknown operation: {operation}", recoverable=False)

    # -- record correction in fact_corrections table (best-effort) -----------
    import sqlite3 as _sqlite3
    from storage.db import get_connection as _get_conn

    try:
        # Determine new trust and fact text for snapshot
        match get_entry_by_id(entry_id, db_path=db_path):
            case Success(value=updated_entry) if updated_entry is not None:
                new_trust = updated_entry.trust_score
                snapshot_new_fact = updated_entry.fact
            case _:
                new_trust = old_trust
                snapshot_new_fact = new_fact

        with _get_conn(db_path) as rec_conn:
            rec_conn.execute(
                """INSERT INTO fact_corrections
                   (entry_id, operation, reason_category, feedback,
                    old_fact, new_fact, old_trust_score, new_trust_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_id,
                    operation,
                    reason_category,
                    feedback,
                    entry.fact,
                    snapshot_new_fact if operation in ("edit_fact", "revise") else None,
                    old_trust,
                    new_trust,
                ),
            )
    except _sqlite3.Error:
        pass  # Best-effort recording — do not fail the correction itself

    # -- audit ----------------------------------------------------------------
    new_correlation_id()
    decision = AIDecision(
        action=f"correct:{operation}",
        confidence=1.0,
        reasoning=reason or f"Consumer AI requested {operation}",
        source_ids=[str(entry_id)],
    )
    audit_write(decision, pipeline="correct", stage=operation, outcome="APPLIED")

    # Include comments in response
    from mcp_server._comment import get_comments as _get_comments

    comments_result = _get_comments(entry_id, db_path=db_path)
    comments = comments_result.value if isinstance(comments_result, Success) else []

    return Success(
        {
            "entry_id": entry_id,
            "operation": operation,
            "result": "applied",
            "comments": comments,
            **({"new_entry_id": new_id} if operation == "revise" else {}),
        }
    )
