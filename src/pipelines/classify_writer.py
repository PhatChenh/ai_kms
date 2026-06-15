"""
pipelines/classify_writer.py

Entry writer for the classify pipeline -- applies extracted facts to the
Fact Store.

Extracted from pipelines/classify.py -- move-only refactoring, no logic changes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.config import ConfidenceBand
from core.result import Failure, Result, Success

_writer_log = logging.getLogger(__name__)


@dataclass
class WriteSummary:
    """Result of write_entries(): was every fact applied cleanly?"""

    clean: bool = True
    skipped_ids: list[int] = field(default_factory=list)


def _merge_sources(existing: list[str], doc_id: int) -> list[str]:
    merged = existing + [str(doc_id)]
    seen: set[str] = set()
    return [s for s in merged if not (s in seen or seen.add(s))]


def _compute_status(confidence: float, band: ConfidenceBand | None) -> str | None:
    from core.tags import confidence_to_status as _re_gate

    if band is None:
        return None
    return _re_gate(confidence, band)


def _merge_reasoning(existing: str, new: str) -> str:
    return new if new else existing


def _find_twin(
    entity: str, dimension: str, tag: str, *, db_path: Path | None = None
) -> Result[int | None]:
    from storage.knowledge_entries import query_by_entity

    twins_result = query_by_entity(entity, db_path=db_path)
    if isinstance(twins_result, Failure):
        return twins_result
    for t in twins_result.value:
        if t.status != "retired" and t.dimension == dimension and t.tag == tag:
            return Success(t.id)
    return Success(None)


def write_entries(
    facts: list[dict],
    doc_id: int,
    dimension: str,
    *,
    band: ConfidenceBand | None = None,
    db_path: Path | None = None,
) -> Result[WriteSummary]:
    """Apply extracted facts to the Fact Store.

    Routes each fact by action:
      - new    -> dedup then insert (or fold into existing twin)
      - update -> merge sources in Python, then upsert
      - retire -> set status='retired'

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
        Success(WriteSummary) -- clean=True when every fact was applied
        without skipping.
    """
    from storage.db import get_connection
    from storage.knowledge_entries import (
        KnowledgeEntry,
        retire as ke_retire,
        upsert as ke_upsert,
    )

    import sqlite3 as _sqlite3

    summary = WriteSummary()

    for fact in facts:
        action = fact.get("action")

        if action == "retire":
            ref_id = fact.get("id")
            if ref_id is None:
                # retire without id -- should not happen (extract validates)
                summary.clean = False
                _writer_log.warning(
                    "write_entries retire missing id doc_id=%s dimension=%s",
                    doc_id,
                    dimension,
                )
                continue

            ret_result = ke_retire(ref_id, fact.get("reason", ""), db_path=db_path)
            if isinstance(ret_result, Failure):
                summary.clean = False
                _writer_log.warning(
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
                _writer_log.warning(
                    "write_entries hallucinated retire id=%s doc_id=%s dimension=%s",
                    ref_id,
                    doc_id,
                    dimension,
                )

        elif action == "update":
            ref_id = fact.get("id")
            if ref_id is None:
                summary.clean = False
                _writer_log.warning(
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
                _writer_log.warning(
                    "write_entries update lookup failed id=%s error=%s",
                    ref_id,
                    exc,
                )
                continue

            if row is None:
                # Hallucinated id
                summary.clean = False
                summary.skipped_ids.append(ref_id)
                _writer_log.warning(
                    "write_entries hallucinated update id=%s doc_id=%s dimension=%s",
                    ref_id,
                    doc_id,
                    dimension,
                )
                continue

            existing_sources: list[str] = (
                json.loads(row["sources"]) if row["sources"] else []
            )
            existing_reasoning: str = (
                row["reasoning"] if "reasoning" in row.keys() else ""
            )

            entry = KnowledgeEntry(
                id=ref_id,
                dimension=dimension,
                entity=fact.get("entity", ""),
                tag=fact.get("tag", ""),
                fact=fact.get("fact", ""),
                confidence=float(fact.get("confidence", 0.5)),
                sources=_merge_sources(existing_sources, doc_id),
                reasoning=_merge_reasoning(existing_reasoning, fact.get("reason", "")),
            )

            status = _compute_status(float(fact.get("confidence", 0.5)), band)

            up_result = ke_upsert(entry, status=status, band=band, db_path=db_path)
            if isinstance(up_result, Failure):
                summary.clean = False
                _writer_log.warning(
                    "write_entries update upsert failed id=%s error=%s",
                    ref_id,
                    up_result.error,
                )

        elif action == "new":
            entity = fact.get("entity", "")
            tag = fact.get("tag", "")
            confidence = float(fact.get("confidence", 0.5))

            twin_result = _find_twin(entity, dimension, tag, db_path=db_path)
            if isinstance(twin_result, Failure):
                _writer_log.warning(
                    "write_entries twin lookup failed entity=%s error=%s doc_id=%s",
                    entity,
                    twin_result.error,
                    doc_id,
                )
                summary.clean = False
                continue
            twin_id: int | None = twin_result.value

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

                twin_entry = KnowledgeEntry(
                    id=twin_id,
                    dimension=dimension,
                    entity=entity,
                    tag=tag,
                    fact=fact.get("fact", ""),
                    confidence=confidence,
                    sources=_merge_sources(existing_sources, doc_id),
                    reasoning=_merge_reasoning(
                        existing_reasoning, fact.get("reason", "")
                    ),
                )

                status = _compute_status(confidence, band)

                up_result = ke_upsert(
                    twin_entry, status=status, band=band, db_path=db_path
                )
                if isinstance(up_result, Failure):
                    summary.clean = False
                    _writer_log.warning(
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

                status = _compute_status(confidence, band)

                up_result = ke_upsert(entry, status=status, band=band, db_path=db_path)
                if isinstance(up_result, Failure):
                    summary.clean = False
                    _writer_log.warning(
                        "write_entries new upsert failed entity=%s error=%s",
                        entity,
                        up_result.error,
                    )

        else:
            # Unknown action -- should not happen (extract validates)
            summary.clean = False
            _writer_log.warning(
                "write_entries unknown action=%s doc_id=%s",
                action,
                doc_id,
            )

    return Success(summary)
