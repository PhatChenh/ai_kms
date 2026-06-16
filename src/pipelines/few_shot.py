"""Few-shot Correction Selector — picks relevant past AI errors as teaching examples.

Phase 10 self-learning: past corrections feed back into the extraction prompt
so the AI learns from its mistakes.
"""

from __future__ import annotations

from pathlib import Path

from core.result import Failure, Result, Success
from storage.db import get_connection


def select_corrections(
    dimension: str,
    doc_entities: list[str],
    *,
    cap: int,
    db_path: Path | None = None,
) -> Result[list[dict]]:
    """Select the most relevant ai_error corrections for a dimension.

    Selection algorithm:
    1. Query fact_corrections WHERE reason_category = 'ai_error',
       joined with knowledge_entries for dimension/entity.
    2. Score: +3 dimension match, +2 entity overlap, +1 recency.
    3. Sort descending, take top cap.

    Returns list of dicts: {old_fact, new_fact, feedback, dimension, entity}.
    """
    try:
        import sqlite3

        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT fc.old_fact, fc.new_fact, fc.feedback,
                       ke.dimension, ke.entity, fc.created_at
                FROM fact_corrections fc
                JOIN knowledge_entries ke ON fc.entry_id = ke.id
                WHERE fc.reason_category = 'ai_error'
                ORDER BY fc.created_at DESC
                LIMIT 200""",
            ).fetchall()

        if not rows:
            return Success([])

        entity_set = set(e.lower() for e in doc_entities)

        scored: list[tuple[float, dict]] = []
        for idx, row in enumerate(rows):
            score = 0.0
            if row["dimension"] == dimension:
                score += 3.0
            if row["entity"] and row["entity"].lower() in entity_set:
                score += 2.0
            # Recency: position penalty (first = most recent)
            score += max(0, 1.0 - idx * 0.1)

            scored.append((score, {
                "old_fact": row["old_fact"] or "",
                "new_fact": row["new_fact"] or "",
                "feedback": row["feedback"] or "",
                "dimension": row["dimension"] or "",
                "entity": row["entity"] or "",
            }))

        scored.sort(key=lambda x: x[0], reverse=True)
        return Success([item[1] for item in scored[:cap]])

    except Exception as exc:
        return Failure(str(exc), recoverable=True, context={"dimension": dimension})


def format_few_shot(corrections: list[dict]) -> str:
    """Format corrections as teaching text for the extraction prompt.

    Returns empty string if corrections list is empty.
    """
    if not corrections:
        return ""

    lines = ["Previous extraction mistakes to avoid:"]
    for c in corrections:
        line = f'- For [{c["entity"]}] in [{c["dimension"]}]: The AI incorrectly extracted "{c["old_fact"]}".'
        if c["new_fact"]:
            line += f' The correct fact is "{c["new_fact"]}".'
        if c["feedback"]:
            line += f' {c["feedback"]}'
        lines.append(line)

    return "\n".join(lines)
