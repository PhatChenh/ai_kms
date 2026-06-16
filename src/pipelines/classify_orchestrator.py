"""
pipelines/classify_orchestrator.py

Orchestrator and retry loop for the classify pipeline.

Extracted from pipelines/classify.py -- move-only refactoring, no logic changes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.config import ConfidenceBand, MainConfig
from core.result import Failure, Result, Success

from pipelines.classify_extract import extract
from pipelines.classify_writer import write_entries

_orch_log = logging.getLogger(__name__)


def _fail_and_record(
    doc_id: int, stage: str, error: str, *, db_path: Path | None = None
) -> Failure:
    from storage.documents_classify import record_classify_failure

    rf = record_classify_failure(doc_id, error, db_path=db_path)
    if isinstance(rf, Failure):
        _orch_log.warning(
            "orchestrate record_classify_failure failed doc_id=%s error=%s",
            doc_id,
            rf.error,
        )
    return Failure(
        error=f"{stage} failed: {error}",
        recoverable=True,
        context={"doc_id": doc_id, "stage": "orchestrate"},
    )


def _audit_dimension(
    dim_name: str,
    dim_clean: bool,
    facts_count: int,
    doc_id: int,
    *,
    db_path: Path | None = None,
) -> Failure | None:
    from core.audit import write as audit_write
    from core.confidence import AIDecision

    audit_decision = AIDecision(
        action=f"extract:{dim_name}",
        confidence=0.8,
        reasoning=f"Extracted {facts_count} facts from doc {doc_id}",
        source_ids=[str(doc_id)],
    )
    audit_result = audit_write(
        audit_decision,
        pipeline="classify",
        stage=dim_name,
        outcome="classify" if dim_clean else "needs-retry",
        db_path=db_path,
    )
    if isinstance(audit_result, Failure):
        return audit_result
    return None


def _handle_stamp(doc_id: int, *, db_path: Path | None, cid: str) -> Result[str]:
    from storage.documents_classify import (
        clear_classify_retry_state,
        stamp_classified,
    )

    stamp_result = stamp_classified(doc_id, db_path=db_path)
    if isinstance(stamp_result, Failure) or stamp_result.value == 0:
        return _fail_and_record(
            doc_id,
            "stamp_classified",
            "failed or returned 0",
            db_path=db_path,
        )

    clear_result = clear_classify_retry_state(doc_id, db_path=db_path)
    if isinstance(clear_result, Failure):
        _orch_log.warning(
            "orchestrate clear_classify_retry_state failed doc_id=%s error=%s",
            doc_id,
            clear_result.error,
        )
    _orch_log.info("orchestrate stamped doc_id=%s cid=%s", doc_id, cid)
    return Success("stamped")


def _handle_retry(
    doc_id: int,
    failure_reasons: list[str],
    attempts: int,
    *,
    config: MainConfig,
    db_path: Path | None,
    cid: str,
) -> Result[str]:
    from core.audit import write as audit_write
    from core.confidence import AIDecision
    from storage.documents_classify import (
        load_classify_retry_state,
        park_document,
        record_classify_failure,
    )

    error_msg = "; ".join(failure_reasons)
    rf_result = record_classify_failure(doc_id, error_msg, db_path=db_path)
    if isinstance(rf_result, Failure):
        _orch_log.warning(
            "orchestrate record_classify_failure failed doc_id=%s error=%s",
            doc_id,
            rf_result.error,
        )

    state2 = load_classify_retry_state(doc_id, db_path=db_path)
    new_attempts = state2.value[0] if isinstance(state2, Success) else attempts + 1

    if new_attempts >= config.classify.max_retries:
        park_document(doc_id, db_path=db_path)
        park_decision = AIDecision(
            action="park:max_retries",
            confidence=1.0,
            reasoning=f"Parked after {new_attempts} failed attempts: {error_msg}",
            source_ids=[str(doc_id)],
        )
        park_audit_result = audit_write(
            park_decision,
            pipeline="classify",
            stage="park",
            outcome="parked",
            db_path=db_path,
        )
        if isinstance(park_audit_result, Failure):
            _orch_log.warning(
                "orchestrate park audit failed doc_id=%s error=%s",
                doc_id,
                park_audit_result.error,
            )
        _orch_log.warning(
            "orchestrate parked doc_id=%s attempts=%s cid=%s",
            doc_id,
            new_attempts,
            cid,
        )
        return Success("parked")

    _orch_log.info(
        "orchestrate retry doc_id=%s attempts=%s cid=%s",
        doc_id,
        new_attempts,
        cid,
    )
    return Success("retried")


async def orchestrate(
    doc_id: int,
    *,
    config: MainConfig,
    db_path: Path | None = None,
    band: ConfidenceBand | None = None,
    dimensions_path: Path | None = None,
) -> Result[str]:
    """Run one document through the full classify extraction pipeline.

    1. Tag the run with a fresh correlation id (load-bearing for audit).
    2. Read the document text and load known facts per dimension.
    3. Load retry state (attempts + last error).
    4. For each dimension: extract -> write -> audit.
    5. If all clean: stamp the document done + clear retry state.
    6. If any failure: save error + increment attempts; park at the cap.

    Args:
        band: Optional ConfidenceBand for status re-gating.  If None,
              falls back to CONFIG.thresholds.for_pipeline("classify").

    Returns:
        Success("stamped"), Success("retried"), or Success("parked").
    """
    from core.config import CONFIG
    from core.logging_setup import new_correlation_id
    from core.tags import load_dimensions
    from pipelines.classify import content_reader, context_loader
    from storage.documents_classify import load_classify_retry_state

    # 1. Fresh correlation id -- MUST precede any audit write (A7)
    cid = new_correlation_id()

    # 2. Read the document text
    text_result = content_reader(doc_id, config=config, db_path=db_path)
    if isinstance(text_result, Failure):
        return _fail_and_record(
            doc_id, "content_reader", text_result.error, db_path=db_path
        )

    text = text_result.value

    # 3. Load known facts per dimension
    ctx_result = context_loader(
        config=config, db_path=db_path, dimensions_path=dimensions_path
    )
    if isinstance(ctx_result, Failure):
        return _fail_and_record(
            doc_id, "context_loader", ctx_result.error, db_path=db_path
        )

    facts_by_dim: dict[str, list] = ctx_result.value

    # 4. Load retry state
    state_result = load_classify_retry_state(doc_id, db_path=db_path)
    if isinstance(state_result, Failure):
        return Failure(
            error=f"load_classify_retry_state failed: {state_result.error}",
            recoverable=False,
            context={"doc_id": doc_id},
        )

    attempts, last_error = state_result.value

    # 5. Load dimensions for guidance + band
    if dimensions_path is None:
        dimensions_path = (
            Path(__file__).resolve().parent.parent / "config" / "dimensions.yaml"
        )
    dims_result = load_dimensions(dimensions_path)
    if isinstance(dims_result, Failure):
        return _fail_and_record(
            doc_id, "load_dimensions", dims_result.error, db_path=db_path
        )

    rulebook: dict = dims_result.value

    # Confidence band for status re-gating
    if band is None:
        band = CONFIG.thresholds.for_pipeline("classify")

    # 6. Per-dimension extract -> write -> audit
    all_clean = True
    failure_reasons: list[str] = []

    for dim_name in rulebook:
        dim_clean = True  # per-dimension tracker (C1)
        guidance_text = rulebook[dim_name].get("guidance", "")
        existing = facts_by_dim.get(dim_name, [])

        # Few-shot correction injection (Phase 10)
        few_shot_text = ""
        if CONFIG.main.self_learning.enabled:
            from pipelines.few_shot import select_corrections, format_few_shot

            doc_entities = [e.entity for e in existing]
            sel_result = select_corrections(
                dim_name,
                doc_entities,
                cap=CONFIG.main.self_learning.max_corrections_per_prompt,
                db_path=db_path,
            )
            if isinstance(sel_result, Success) and sel_result.value:
                few_shot_text = format_few_shot(sel_result.value)

        # 6a. Extract
        extracted = await extract(
            dim_name,
            text,
            existing,
            guidance_text,
            feedback=last_error or "",
            config=config,
            few_shot_corrections=few_shot_text,
        )

        if isinstance(extracted, Failure):
            all_clean = False
            dim_clean = False
            failure_reasons.append(f"{dim_name}: {extracted.error}")
        else:
            # 6b. Write entries
            summary = write_entries(
                extracted.value,
                doc_id,
                dim_name,
                band=band,
                db_path=db_path,
            )

            if isinstance(summary, Failure):
                all_clean = False
                dim_clean = False
                failure_reasons.append(f"{dim_name} write: {summary.error}")
            elif not summary.value.clean:
                all_clean = False
                dim_clean = False
                failure_reasons.append(
                    f"{dim_name}: skipped_ids={summary.value.skipped_ids}"
                )

        # 6c. Audit per dimension -- always write, even on failure (C1)
        facts_count = len(extracted.value) if not isinstance(extracted, Failure) else 0
        audit_fail = _audit_dimension(
            dim_name, dim_clean, facts_count, doc_id, db_path=db_path
        )
        if audit_fail is not None:
            all_clean = False
            failure_reasons.append(f"{dim_name} audit: {audit_fail.error}")

    # 7. Decide: stamp or retry
    if all_clean:
        return _handle_stamp(doc_id, db_path=db_path, cid=cid)

    return _handle_retry(
        doc_id,
        failure_reasons,
        attempts,
        config=config,
        db_path=db_path,
        cid=cid,
    )
