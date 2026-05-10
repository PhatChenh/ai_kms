from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import structlog

from core.result import Failure, Result, Success
from storage.db import get_connection


@dataclass(frozen=True)
class AuditEntry:
    pipeline:       str
    stage:          str
    source_ids:     list[str]
    decision:       str
    confidence:     float
    reasoning:      str
    outcome:        str
    timestamp:      str | None = None
    correlation_id: str | None = None


def append(entry: AuditEntry, db_path: Path | None = None) -> Result[int]:
    cid = entry.correlation_id or structlog.contextvars.get_contextvars().get("correlation_id")
    if cid is None:
        return Failure(
            error="missing correlation_id",
            recoverable=False,
            context={"pipeline": entry.pipeline, "stage": entry.stage},
        )
    try:
        with get_connection(db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_log
                    (pipeline, stage, source_ids, decision, confidence,
                     reasoning, outcome, correlation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.pipeline,
                    entry.stage,
                    json.dumps(entry.source_ids),
                    entry.decision,
                    entry.confidence,
                    entry.reasoning,
                    entry.outcome,
                    cid,
                ),
            )
            rowid: int = cur.lastrowid  # type: ignore[assignment]
        return Success(rowid)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"pipeline": entry.pipeline, "stage": entry.stage},
        )


def query(
    *,
    date: str | None = None,
    pipeline: str | None = None,
    correlation_id: str | None = None,
    limit: int = 1000,
    db_path: Path | None = None,
) -> Result[list[AuditEntry]]:
    clauses: list[str] = []
    params: list[str | int] = []

    if date is not None:
        clauses.append("date(timestamp) = ?")
        params.append(date)
    if pipeline is not None:
        clauses.append("pipeline = ?")
        params.append(pipeline)
    if correlation_id is not None:
        clauses.append("correlation_id = ?")
        params.append(correlation_id)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT pipeline, stage, source_ids, decision, confidence, "
        "reasoning, outcome, timestamp, correlation_id "
        f"FROM audit_log {where} ORDER BY id LIMIT ?"
    )
    params.append(limit)

    try:
        with get_connection(db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        entries = [
            AuditEntry(
                pipeline=row[0],
                stage=row[1],
                source_ids=json.loads(row[2]),
                decision=row[3],
                confidence=row[4],
                reasoning=row[5],
                outcome=row[6],
                timestamp=row[7],
                correlation_id=row[8],
            )
            for row in rows
        ]
        return Success(entries)
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={
                "date": str(date),
                "pipeline": str(pipeline),
                "correlation_id": str(correlation_id),
            },
        )
