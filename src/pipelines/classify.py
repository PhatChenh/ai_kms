from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from core.config import MainConfig
from core.result import Failure, Success
from core.result import Result
from llm.prompt_loader import PROMPTS
from llm.provider import get_provider


# ===========================================================================
# Phase 6 — Content Reader + Context Loader (classify infra helpers)
# ===========================================================================


def content_reader(
    doc_id: int,
    *,
    config: MainConfig,
    db_path: Path | None = None,
) -> Result[str]:
    """Choose full_body or summary for a document based on token budget.

    Uses the // 4 heuristic to estimate token count from character length.
    If full_body fits within config.classify.max_content_tokens, it is used;
    otherwise the summary is used instead.  When full_body is None or empty,
    summary is always used as a fallback.

    Args:
        doc_id: Primary key of the documents row.
        config: Validated MainConfig carrying the classify token cap.
        db_path: Override DB path.

    Returns:
        Success(str) with the chosen text, or Failure if the row is missing
        or the DB is unreachable.
    """
    from storage.db import get_connection

    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT full_body, summary FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"doc_id": doc_id, "op": "content_reader"},
        )

    if row is None:
        return Failure(
            error=f"document not found: id={doc_id}",
            recoverable=False,
            context={"doc_id": doc_id, "op": "content_reader"},
        )

    full_body: str | None = row["full_body"]
    summary: str | None = row["summary"]

    # Fallback: None or empty full_body → summary
    if not full_body:
        return Success(summary or "")

    max_tokens: int = config.classify.max_content_tokens
    estimated_tokens = len(full_body) // 4

    if estimated_tokens < max_tokens:
        return Success(full_body)

    return Success(summary or "")


def context_loader(
    *,
    config: MainConfig,
    db_path: Path | None = None,
) -> Result[dict[str, list]]:
    """Load ranked, capped, non-retired knowledge entries for every dimension.

    Reads the dimension list from config/dimensions.yaml (Phase 2), then for
    each dimension calls the Phase 4 ranked query with
    ``config.classify.max_entries_per_dimension`` as the cap.

    Args:
        config:  Validated MainConfig carrying the per-dimension cap.
        db_path: Override DB path.

    Returns:
        Success(dict[dimension → list[KnowledgeEntry]]) with ranked, capped,
        non-retired facts.  Dimensions with zero matching facts get an empty
        list (not an error).
    """
    from core.tags import load_dimensions
    from storage.knowledge_entries import query_ranked_by_dimension

    # Locate dimensions.yaml relative to this source file
    _classify_dir = Path(__file__).resolve().parent  # src/pipelines
    _project_root = _classify_dir.parent.parent  # project root
    dimensions_path = _project_root / "config" / "dimensions.yaml"

    dims_result = load_dimensions(dimensions_path)
    if isinstance(dims_result, Failure):
        return Failure(
            error=f"context_loader cannot load dimensions: {dims_result.error}",
            recoverable=False,
            context={"op": "context_loader", "path": str(dimensions_path)},
        )

    rulebook: dict = dims_result.value
    cap: int = config.classify.max_entries_per_dimension

    result: dict[str, list] = {}

    for dim_name in rulebook:
        ranked = query_ranked_by_dimension(
            dim_name,
            limit=cap,
            db_path=db_path,
        )
        if isinstance(ranked, Failure):
            return Failure(
                error=f"context_loader query failed for dimension {dim_name!r}: {ranked.error}",
                recoverable=False,
                context={"op": "context_loader", "dimension": dim_name},
            )
        result[dim_name] = ranked.value

    return Success(result)


# ===========================================================================
# Phase 7 — Work Queue + Worker + catch-up scan
# ===========================================================================

import asyncio
import logging

_worker_log = logging.getLogger(__name__)


async def consumer(
    queue: asyncio.Queue[int],
    db_path: Path | None,
    config: MainConfig,
) -> None:
    """Single sequential consumer: pulls doc_ids from the queue, prepares
    inputs via Content Reader → Dimension Loader → Context Loader, then
    **stops** — this is the Slice B seam.  No AI call, no stamp on the
    happy path.

    Failures at any stage are logged and the doc is left un-stamped so it
    will be retried on the next startup catch-up scan.
    """
    while True:
        got_item = False
        try:
            doc_id = await queue.get()
            got_item = True

            # ---- Slice B seam (Phase 7) ----
            # orchestrate() handles all input preparation internally
            # (content_reader, context_loader, retry state, dimensions).
            orch_result = await orchestrate(doc_id, config=config, db_path=db_path)
            if isinstance(orch_result, Failure):
                _worker_log.warning(
                    "classify_worker orchestrate failed doc_id=%s error=%s",
                    doc_id,
                    orch_result.error,
                )

        except Exception:
            _worker_log.exception(
                "classify_worker unexpected error doc_id=%s", doc_id
            )
        finally:
            if got_item:
                queue.task_done()


async def catch_up_scan(
    queue: asyncio.Queue[int],
    db_path: Path | None,
) -> None:
    """One burst at startup: discover every doc that needs classification
    and enqueue its id.

    OQ-P8A-03 — catch-up scan.
    """
    from storage.documents import find_unclassified

    result = find_unclassified(db_path=db_path)
    if isinstance(result, Failure):
        _worker_log.error(
            "catch_up_scan find_unclassified failed error=%s",
            result.error,
        )
        return

    for doc_id in result.value:
        queue.put_nowait(doc_id)

    _worker_log.info("catch_up_scan enqueued=%d", len(result.value))


async def extract(
    dimension: str,
    text: str,
    existing_facts: list,
    guidance: str,
    feedback: str,
    config: MainConfig,
) -> Result[list[dict]]:
    """Ask the AI to extract structured facts from *text* for *dimension*.

    Returns a list of parsed fact dicts, each validated against the
    entity_extract prompt's reply contract.  The caller is responsible for
    routing each fact (new / update / retire).

    Args:
        dimension:     The knowledge category name (e.g. "people").
        text:          The document text from Content Reader.
        existing_facts: List of KnowledgeEntry-like objects with .id, .entity,
                        .tag, .fact, .confidence attributes.
        guidance:      The dimension's guidance text from dimensions.yaml.
        feedback:      The previous failure reason (empty string on first attempt).
        config:        Validated MainConfig.

    Returns:
        Success(list[dict]) with parsed facts, or Failure with a recoverable
        flag set per the error class.
    """
    # 1. Render the prompt
    try:
        system, user = PROMPTS["entity_extract"].render(
            document_text=text,
            dimension_guidance=guidance,
            existing_facts=existing_facts,
            previous_attempt_feedback=feedback,
        )
    except Exception as exc:
        return Failure(
            error=f"entity_extract render error: {exc}",
            recoverable=False,
            context={"stage": "extract", "dimension": dimension},
        )

    # 2. Get the AI provider via the factory (never instantiate directly)
    provider = get_provider("classify", config)

    # 3. Call the AI
    response = await provider.complete(system, user)

    # 4. Handle provider failure
    if isinstance(response, Failure):
        return Failure(
            error=response.error,
            recoverable=True,
            context={"stage": "extract", "dimension": dimension},
        )

    # 5. Parse JSON
    raw_text = response.value.content
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return Failure(
            error=f"entity_extract JSON parse error: {exc}",
            recoverable=True,
            context={
                "stage": "extract",
                "dimension": dimension,
                "raw": raw_text[:200],
            },
        )

    # 6. Validate top-level is a list
    if not isinstance(data, list):
        return Failure(
            error=f"entity_extract reply must be a JSON array, got {type(data).__name__}",
            recoverable=True,
            context={
                "stage": "extract",
                "dimension": dimension,
                "raw": raw_text[:200],
            },
        )

    # 7. Validate each fact
    parsed: list[dict] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            return Failure(
                error=f"entity_extract item[{i}] is not a dict: {type(item).__name__}",
                recoverable=True,
                context={
                    "stage": "extract",
                    "dimension": dimension,
                    "raw": raw_text[:200],
                },
            )

        action = item.get("action")
        if action not in ("new", "update", "retire"):
            return Failure(
                error=f"entity_extract item[{i}] unknown action {action!r}",
                recoverable=True,
                context={
                    "stage": "extract",
                    "dimension": dimension,
                    "raw": raw_text[:200],
                },
            )

        # Per-action field validation
        if action == "retire":
            # retire: requires id + reason
            if "id" not in item:
                return Failure(
                    error=f"entity_extract item[{i}] 'retire' missing 'id'",
                    recoverable=True,
                    context={
                        "stage": "extract",
                        "dimension": dimension,
                        "raw": raw_text[:200],
                    },
                )
            if "reason" not in item:
                return Failure(
                    error=f"entity_extract item[{i}] 'retire' missing 'reason'",
                    recoverable=True,
                    context={
                        "stage": "extract",
                        "dimension": dimension,
                        "raw": raw_text[:200],
                    },
                )
            parsed.append({
                "action": "retire",
                "id": item["id"],
                "reason": item.get("reason", ""),
            })

        elif action == "update":
            # update: requires id, entity, tag, fact, confidence
            if "id" not in item:
                return Failure(
                    error=f"entity_extract item[{i}] 'update' missing 'id'",
                    recoverable=True,
                    context={
                        "stage": "extract",
                        "dimension": dimension,
                        "raw": raw_text[:200],
                    },
                )
            for field in ("entity", "tag", "fact", "confidence"):
                if field not in item:
                    return Failure(
                        error=f"entity_extract item[{i}] 'update' missing {field!r}",
                        recoverable=True,
                        context={
                            "stage": "extract",
                            "dimension": dimension,
                            "raw": raw_text[:200],
                        },
                    )
            parsed.append({
                "action": "update",
                "id": item["id"],
                "entity": item["entity"],
                "tag": item["tag"],
                "fact": item["fact"],
                "confidence": float(item["confidence"]),
            })

        elif action == "new":
            # new: requires entity, tag, fact, confidence; must NOT have id
            if "id" in item:
                return Failure(
                    error=f"entity_extract item[{i}] 'new' must not include 'id'",
                    recoverable=True,
                    context={
                        "stage": "extract",
                        "dimension": dimension,
                        "raw": raw_text[:200],
                    },
                )
            for field in ("entity", "tag", "fact", "confidence"):
                if field not in item:
                    return Failure(
                        error=f"entity_extract item[{i}] 'new' missing {field!r}",
                        recoverable=True,
                        context={
                            "stage": "extract",
                            "dimension": dimension,
                            "raw": raw_text[:200],
                        },
                    )
                # Non-empty check for string fields (M5)
                if field in ("entity", "tag", "fact") and not str(item[field]).strip():
                    return Failure(
                        error=f"entity_extract item[{i}] 'new' {field!r} must not be empty",
                        recoverable=True,
                        context={
                            "stage": "extract",
                            "dimension": dimension,
                            "raw": raw_text[:200],
                        },
                    )
            # Confidence range validation (I4)
            conf_val = float(item["confidence"])
            if conf_val < 0.0 or conf_val > 1.0:
                return Failure(
                    error=f"entity_extract item[{i}] confidence {conf_val} out of range [0.0, 1.0]",
                    recoverable=True,
                    context={
                        "stage": "extract",
                        "dimension": dimension,
                        "raw": raw_text[:200],
                    },
                )
            parsed.append({
                "action": "new",
                "entity": item["entity"],
                "tag": item["tag"],
                "fact": item["fact"],
                "confidence": float(item["confidence"]),
            })

    return Success(parsed)


# ===========================================================================
# Phase 6 — Entry Writer (Slice B)
# ===========================================================================


@dataclass
class WriteSummary:
    """Result of write_entries(): was every fact applied cleanly?"""
    clean: bool = True
    skipped_ids: list[int] = field(default_factory=list)


def write_entries(
    facts: list[dict],
    doc_id: int,
    dimension: str,
    *,
    band: object | None = None,
    db_path: Path | None = None,
) -> Result[WriteSummary]:
    """Apply extracted facts to the Fact Store.

    Routes each fact by action:
      - new    → dedup then insert (or fold into existing twin)
      - update → merge sources in Python, then upsert
      - retire → set status='retired'

    Every write re-gates the entry's status from its confidence via
    ``confidence_to_status`` when a *band* is provided.  Hallucinated
    ids (update/retire referencing a non-existent entry) are skipped
    and surfaced in the returned ``WriteSummary`` so the orchestrator
    can withhold the stamp.

    Args:
        facts:     Parsed fact dicts from the Entity Extractor.
        doc_id:    The document id to add to sources.
        dimension: The knowledge category these facts belong to.
        band:      Optional ConfidenceBand for status re-gating.
        db_path:   Override DB path.

    Returns:
        Success(WriteSummary) — clean=True when every fact was applied
        without skipping.
    """
    from storage.knowledge_entries import (
        KnowledgeEntry,
        query_by_entity,
        retire as ke_retire,
        upsert as ke_upsert,
    )
    from core.tags import confidence_to_status as _re_gate
    from storage.db import get_connection
    import sqlite3 as _sqlite3

    summary = WriteSummary()

    for fact in facts:
        action = fact.get("action")

        if action == "retire":
            ref_id = fact.get("id")
            if ref_id is None:
                # retire without id — should not happen (extract validates)
                summary.clean = False
                _worker_log.warning(
                    "write_entries retire missing id doc_id=%s dimension=%s",
                    doc_id,
                    dimension,
                )
                continue

            ret_result = ke_retire(ref_id, fact.get("reason", ""), db_path=db_path)
            if isinstance(ret_result, Failure):
                summary.clean = False
                _worker_log.warning(
                    "write_entries retire failed id=%s error=%s doc_id=%s",
                    ref_id,
                    ret_result.error,
                    doc_id,
                )
                continue
            if ret_result.value == 0:
                # Hallucinated id
                summary.clean = False
                summary.skipped_ids.append(ref_id)
                _worker_log.warning(
                    "write_entries hallucinated retire id=%s doc_id=%s dimension=%s",
                    ref_id,
                    doc_id,
                    dimension,
                )

        elif action == "update":
            ref_id = fact.get("id")
            if ref_id is None:
                summary.clean = False
                _worker_log.warning(
                    "write_entries update missing id doc_id=%s dimension=%s",
                    doc_id,
                    dimension,
                )
                continue

            # Read the existing entry to merge sources
            try:
                with get_connection(db_path, readonly=True) as conn:
                    conn.row_factory = _sqlite3.Row
                    row = conn.execute(
                        "SELECT * FROM knowledge_entries WHERE id = ?",
                        (ref_id,),
                    ).fetchone()
            except _sqlite3.Error as exc:
                summary.clean = False
                _worker_log.warning(
                    "write_entries update lookup failed id=%s error=%s",
                    ref_id,
                    exc,
                )
                continue

            if row is None:
                # Hallucinated id
                summary.clean = False
                summary.skipped_ids.append(ref_id)
                _worker_log.warning(
                    "write_entries hallucinated update id=%s doc_id=%s dimension=%s",
                    ref_id,
                    doc_id,
                    dimension,
                )
                continue

            # Merge sources: read existing, append doc_id, dedupe
            existing_sources: list[str] = (
                json.loads(row["sources"]) if row["sources"] else []
            )
            # Preserve existing reasoning unless AI explicitly provides one (I3)
            existing_reasoning: str = row["reasoning"] if "reasoning" in row.keys() else ""
            new_reasoning = fact.get("reason", "")
            merged_reasoning = new_reasoning if new_reasoning else existing_reasoning
            
            merged_sources = existing_sources + [str(doc_id)]
            # Dedupe while preserving order
            seen: set[str] = set()
            deduped: list[str] = []
            for s in merged_sources:
                if s not in seen:
                    seen.add(s)
                    deduped.append(s)

            entry = KnowledgeEntry(
                id=ref_id,
                dimension=dimension,
                entity=fact.get("entity", ""),
                tag=fact.get("tag", ""),
                fact=fact.get("fact", ""),
                confidence=float(fact.get("confidence", 0.5)),
                sources=deduped,
                reasoning=merged_reasoning,
            )

            # Re-gate status
            status = None
            if band is not None:
                status = _re_gate(float(fact.get("confidence", 0.5)), band)

            up_result = ke_upsert(entry, status=status, band=band, db_path=db_path)
            if isinstance(up_result, Failure):
                summary.clean = False
                _worker_log.warning(
                    "write_entries update upsert failed id=%s error=%s",
                    ref_id,
                    up_result.error,
                )

        elif action == "new":
            entity = fact.get("entity", "")
            tag = fact.get("tag", "")
            confidence = float(fact.get("confidence", 0.5))

            # Check for an existing non-retired twin (same dimension+entity+tag)
            twins_result = query_by_entity(entity, db_path=db_path)
            twin_id: int | None = None
            if isinstance(twins_result, Success):
                for t in twins_result.value:
                    if (
                        t.status != "retired"
                        and t.dimension == dimension
                        and t.tag == tag
                    ):
                        twin_id = t.id
                        break
            else:
                # DB error on twin lookup — log, mark unclean, skip this fact
                _worker_log.warning(
                    "write_entries twin lookup failed entity=%s error=%s doc_id=%s",
                    entity,
                    twins_result.error,
                    doc_id,
                )
                summary.clean = False
                continue

            if twin_id is not None:
                # Fold into the existing twin: merge sources, update fact text
                try:
                    with get_connection(db_path, readonly=True) as conn:
                        conn.row_factory = _sqlite3.Row
                        twin_row = conn.execute(
                            "SELECT sources, reasoning FROM knowledge_entries WHERE id = ?",
                            (twin_id,),
                        ).fetchone()
                except _sqlite3.Error:
                    twin_row = None

                existing_sources: list[str] = []
                existing_reasoning: str = ""
                if twin_row:
                    if twin_row["sources"]:
                        existing_sources = json.loads(twin_row["sources"])
                    existing_reasoning = twin_row["reasoning"] or ""

                merged = existing_sources + [str(doc_id)]
                seen = set()
                deduped_sources = []
                for s in merged:
                    if s not in seen:
                        seen.add(s)
                        deduped_sources.append(s)

                # Preserve existing reasoning unless AI explicitly provides one (I3)
                new_reasoning = fact.get("reason", "")
                merged_reasoning = new_reasoning if new_reasoning else existing_reasoning

                twin_entry = KnowledgeEntry(
                    id=twin_id,
                    dimension=dimension,
                    entity=entity,
                    tag=tag,
                    fact=fact.get("fact", ""),
                    confidence=confidence,
                    sources=deduped_sources,
                    reasoning=merged_reasoning,
                )

                status = None
                if band is not None:
                    status = _re_gate(confidence, band)

                up_result = ke_upsert(twin_entry, status=status, band=band, db_path=db_path)
                if isinstance(up_result, Failure):
                    summary.clean = False
                    _worker_log.warning(
                        "write_entries fold upsert failed twin_id=%s error=%s",
                        twin_id,
                        up_result.error,
                    )
            else:
                # Fresh insert
                entry = KnowledgeEntry(
                    dimension=dimension,
                    entity=entity,
                    tag=tag,
                    fact=fact.get("fact", ""),
                    confidence=confidence,
                    sources=[str(doc_id)],
                    reasoning=fact.get("reason", ""),
                )

                status = None
                if band is not None:
                    status = _re_gate(confidence, band)

                up_result = ke_upsert(entry, status=status, band=band, db_path=db_path)
                if isinstance(up_result, Failure):
                    summary.clean = False
                    _worker_log.warning(
                        "write_entries new upsert failed entity=%s error=%s",
                        entity,
                        up_result.error,
                    )

        else:
            # Unknown action — should not happen (extract validates)
            summary.clean = False
            _worker_log.warning(
                "write_entries unknown action=%s doc_id=%s",
                action,
                doc_id,
            )

    return Success(summary)


# ===========================================================================
# Phase 7 — Orchestrator + retry loop (Slice B)
# ===========================================================================


async def orchestrate(
    doc_id: int,
    *,
    config: MainConfig,
    db_path: Path | None = None,
    band: object | None = None,
) -> Result[str]:
    """Run one document through the full classify extraction pipeline.

    1. Tag the run with a fresh correlation id (load-bearing for audit).
    2. Read the document text and load known facts per dimension.
    3. Load retry state (attempts + last error).
    4. For each dimension: extract → write → audit.
    5. If all clean: stamp the document done + clear retry state.
    6. If any failure: save error + increment attempts; park at the cap.

    Args:
        band: Optional ConfidenceBand for status re-gating.  If None,
              falls back to CONFIG.thresholds.for_pipeline("classify").

    Returns:
        Success("stamped"), Success("retried"), or Success("parked").
    """
    from core.audit import write as audit_write
    from core.confidence import AIDecision
    from core.config import CONFIG
    from core.logging_setup import new_correlation_id
    from core.tags import load_dimensions
    from storage.documents import (
        clear_classify_retry_state,
        load_classify_retry_state,
        park_document,
        record_classify_failure,
        stamp_classified,
    )

    # 1. Fresh correlation id — MUST precede any audit write (A7)
    cid = new_correlation_id()

    # 2. Read the document text
    text_result = content_reader(doc_id, config=config, db_path=db_path)
    if isinstance(text_result, Failure):
        rf_result = record_classify_failure(
            doc_id, text_result.error, db_path=db_path
        )
        if isinstance(rf_result, Failure):
            _worker_log.warning(
                "orchestrate record_classify_failure failed doc_id=%s error=%s",
                doc_id, rf_result.error,
            )
        return Failure(
            error=f"content_reader failed: {text_result.error}",
            recoverable=True,
            context={"doc_id": doc_id, "stage": "orchestrate"},
        )

    text = text_result.value

    # 3. Load known facts per dimension
    ctx_result = context_loader(config=config, db_path=db_path)
    if isinstance(ctx_result, Failure):
        rf_result = record_classify_failure(
            doc_id, ctx_result.error, db_path=db_path
        )
        if isinstance(rf_result, Failure):
            _worker_log.warning(
                "orchestrate record_classify_failure failed doc_id=%s error=%s",
                doc_id, rf_result.error,
            )
        return Failure(
            error=f"context_loader failed: {ctx_result.error}",
            recoverable=True,
            context={"doc_id": doc_id, "stage": "orchestrate"},
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
    dimensions_path = (
        Path(__file__).resolve().parent.parent / "config" / "dimensions.yaml"
    )
    dims_result = load_dimensions(dimensions_path)
    if isinstance(dims_result, Failure):
        rf_result = record_classify_failure(
            doc_id, dims_result.error, db_path=db_path
        )
        if isinstance(rf_result, Failure):
            _worker_log.warning(
                "orchestrate record_classify_failure failed doc_id=%s error=%s",
                doc_id, rf_result.error,
            )
        return Failure(
            error=f"load_dimensions failed: {dims_result.error}",
            recoverable=False,
            context={"doc_id": doc_id},
        )

    rulebook: dict = dims_result.value

    # Confidence band for status re-gating
    if band is None:
        band = CONFIG.thresholds.for_pipeline("classify")

    # 6. Per-dimension extract → write → audit
    all_clean = True
    failure_reasons: list[str] = []

    for dim_name in rulebook:
        dim_clean = True  # per-dimension tracker (C1)
        guidance_text = rulebook[dim_name].get("guidance", "")
        existing = facts_by_dim.get(dim_name, [])

        # 6a. Extract
        extracted = await extract(
            dim_name,
            text,
            existing,
            guidance_text,
            feedback=last_error or "",
            config=config,
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

        # 6c. Audit per dimension — always write, even on failure (C1)
        audit_decision = AIDecision(
            action=f"extract:{dim_name}",
            confidence=0.8,
            reasoning=f"Extracted {len(extracted.value) if not isinstance(extracted, Failure) else 0} facts from doc {doc_id}",
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
            all_clean = False
            failure_reasons.append(
                f"{dim_name} audit: {audit_result.error}"
            )

    # 7. Decide: stamp or retry
    if all_clean:
        # All dimensions passed → stamp + clear retry
        stamp_result = stamp_classified(doc_id, db_path=db_path)
        if isinstance(stamp_result, Failure) or stamp_result.value == 0:
            # Rowcount 0 means the doc was deleted mid-run — treat as failure
            rf_result = record_classify_failure(
                doc_id,
                "stamp_classified returned 0 rowcount — doc may have been deleted",
                db_path=db_path,
            )
            if isinstance(rf_result, Failure):
                _worker_log.warning(
                    "orchestrate record_classify_failure failed doc_id=%s error=%s",
                    doc_id, rf_result.error,
                )
            return Failure(
                error="stamp_classified failed or returned 0",
                recoverable=True,
                context={"doc_id": doc_id},
            )

        clear_classify_retry_state(doc_id, db_path=db_path)
        _worker_log.info(
            "orchestrate stamped doc_id=%s cid=%s", doc_id, cid
        )
        return Success("stamped")

    # Partial/full failure
    error_msg = "; ".join(failure_reasons)
    rf_result = record_classify_failure(doc_id, error_msg, db_path=db_path)
    if isinstance(rf_result, Failure):
        _worker_log.warning(
            "orchestrate record_classify_failure failed doc_id=%s error=%s",
            doc_id, rf_result.error,
        )

    # Re-read attempts (just incremented by record_classify_failure)
    state2 = load_classify_retry_state(doc_id, db_path=db_path)
    new_attempts = state2.value[0] if isinstance(state2, Success) else attempts + 1

    if new_attempts >= config.classify.max_retries:
        # Park the document
        park_document(doc_id, db_path=db_path)
        park_decision = AIDecision(
            action="park:max_retries",
            confidence=1.0,
            reasoning=f"Parked after {new_attempts} failed attempts: {error_msg}",
            source_ids=[str(doc_id)],
        )
        audit_write(
            park_decision,
            pipeline="classify",
            stage="park",
            outcome="parked",
            db_path=db_path,
        )
        _worker_log.warning(
            "orchestrate parked doc_id=%s attempts=%s cid=%s",
            doc_id, new_attempts, cid,
        )
        return Success("parked")

    _worker_log.info(
        "orchestrate retry doc_id=%s attempts=%s cid=%s",
        doc_id, new_attempts, cid,
    )
    return Success("retried")
