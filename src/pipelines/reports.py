"""Report Synthesis Pipeline — on-demand knowledge health reports.

Report definitions are data (YAML). Adding a new report type
requires zero code changes (extension point rule).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from core.config import MainConfig
from core.result import Failure, Result, Success
from storage.db import get_connection


async def synthesize_report(
    report_type: str,
    *,
    config: MainConfig,
    db_path: Path | None = None,
) -> Result[dict]:
    """Generate a report by type.

    Steps:
    1. Load report definition from config/reports.yaml.
    2. Gather data per sources config.
    3. Render prompt with gathered data as context.
    4. Call synthesis LLM.
    5. Store result in reports table.
    6. Return report dict.
    """
    # 1. Load report definition
    reports_path = Path(__file__).resolve().parent.parent / "config" / "reports.yaml"
    try:
        with open(reports_path) as f:
            report_defs = yaml.safe_load(f)
    except Exception as exc:
        return Failure(f"Cannot load reports.yaml: {exc}", recoverable=False)

    if report_type not in report_defs.get("report_types", {}):
        return Failure(
            f"Unknown report type: {report_type}. Available: {list(report_defs['report_types'].keys())}",
            recoverable=False,
        )

    definition = report_defs["report_types"][report_type]
    title = definition["title"]
    prompt_template = definition["prompt"]
    sources = definition.get("sources", [])
    filters = definition.get("filters", {})

    # 2. Gather data
    context_parts: list[str] = []
    source_ids: list[int] = []

    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row

            if "facts" in sources:
                rows = conn.execute(
                    "SELECT id, dimension, entity, tag, fact, status, confidence, trust_score "
                    "FROM knowledge_entries ORDER BY dimension, entity"
                ).fetchall()
                facts_text = _format_facts(rows)
                context_parts.append(facts_text)
                source_ids.extend(r["id"] for r in rows)

                # Conflict detection
                if filters.get("detect_conflicts"):
                    conflicts = _detect_conflicts(conn)
                    if conflicts:
                        context_parts.append(_format_conflicts(conflicts))

            if "corrections" in sources:
                rows = conn.execute(
                    "SELECT fc.*, ke.dimension, ke.entity "
                    "FROM fact_corrections fc "
                    "JOIN knowledge_entries ke ON fc.entry_id = ke.id "
                    "ORDER BY fc.created_at DESC"
                ).fetchall()
                context_parts.append(_format_corrections(rows))

            if "summaries" in sources:
                rows = conn.execute(
                    "SELECT id, title, summary FROM documents ORDER BY id"
                ).fetchall()
                context_parts.append(_format_summaries(rows))

    except sqlite3.Error as exc:
        return Failure(
            str(exc), recoverable=False, context={"report_type": report_type}
        )

    # 3. Render prompt
    full_prompt = prompt_template + "\n\n--- DATA ---\n\n" + "\n\n".join(context_parts)

    # 4. Call synthesis LLM
    try:
        from llm.provider import get_provider

        provider = get_provider("synthesis", config)
        llm_result = await provider.complete(
            system="You are a knowledge base analyst. Produce clear, data-driven reports.",
            user=full_prompt,
        )
        if isinstance(llm_result, Failure):
            return Failure(
                f"LLM synthesis failed: {llm_result.error}",
                recoverable=True,
                context={"report_type": report_type},
            )
        body = llm_result.value.content
    except Exception as exc:
        return Failure(
            f"LLM synthesis failed: {exc}",
            recoverable=True,
            context={"report_type": report_type},
        )

    # 5. Store in reports table
    try:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO reports (report_type, title, body, prompt_used, filters_used, sources_used)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    report_type,
                    title,
                    body,
                    full_prompt,
                    json.dumps(filters),
                    json.dumps(source_ids),
                ),
            )
            report_id = cursor.lastrowid
    except sqlite3.Error as exc:
        return Failure(f"Failed to store report: {exc}", recoverable=False)

    # 6. Audit
    from core.audit import write as audit_write
    from core.confidence import AIDecision
    from core.logging_setup import new_correlation_id

    new_correlation_id()
    decision = AIDecision(
        action=f"report:{report_type}",
        confidence=1.0,
        reasoning=f"On-demand report generation: {title}",
        source_ids=[str(report_id)],
    )
    audit_write(decision, pipeline="reports", stage="synthesize", outcome="GENERATED")

    return Success({"report_id": report_id, "title": title, "body": body})


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def _format_facts(rows) -> str:
    lines = ["KNOWLEDGE ENTRIES:"]
    for r in rows:
        lines.append(
            f"  [{r['id']}] {r['dimension']}/{r['entity']}/{r['tag']}: "
            f"{r['fact']} (status={r['status']}, conf={r['confidence']}, trust={r['trust_score']})"
        )
    return "\n".join(lines)


def _format_corrections(rows) -> str:
    lines = ["CORRECTIONS:"]
    for r in rows:
        lines.append(
            f"  [{r['id']}] {r['dimension']}/{r['entity']} op={r['operation']} "
            f"category={r['reason_category']} trust:{r['old_trust_score']}->{r['new_trust_score']} "
            f"at {r['created_at']}"
        )
    return "\n".join(lines)


def _format_summaries(rows) -> str:
    lines = ["DOCUMENTS:"]
    for r in rows:
        lines.append(f"  [{r['id']}] {r['title']}: {(r['summary'] or '')[:200]}")
    return "\n".join(lines)


def _detect_conflicts(conn) -> list[tuple]:
    """Find same entity+dimension+tag with both confident and pending entries."""
    return conn.execute(
        """SELECT e1.id as trusted_id, e1.entity, e1.dimension, e1.tag,
                  e1.fact as trusted_fact, e1.trust_score,
                  e2.id as pending_id, e2.fact as pending_fact
           FROM knowledge_entries e1
           JOIN knowledge_entries e2
             ON e1.entity = e2.entity AND e1.dimension = e2.dimension AND e1.tag = e2.tag
           WHERE e1.status = 'confident' AND e1.trust_score > 0.5
             AND e2.status = 'pending'
             AND e1.id != e2.id"""
    ).fetchall()


def _format_conflicts(conflicts) -> str:
    lines = ["CONFLICTS (trusted entry vs pending entry):"]
    for c in conflicts:
        lines.append(
            f'  Trusted [{c[0]}] {c[1]}/{c[2]}/{c[3]}: "{c[4]}" (trust={c[5]}) '
            f'vs Pending [{c[6]}]: "{c[7]}"'
        )
    return "\n".join(lines)
