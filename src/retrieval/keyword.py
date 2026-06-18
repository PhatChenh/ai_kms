from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from core.result import Failure, Result, Success
from storage.db import get_connection

_log = logging.getLogger(__name__)


def index_keywords(
    vault_path: str,
    title: str,
    summary: str,
    body: str,
    db_path: Path | None = None,
) -> Result[None]:
    """Index a note's text content into the FTS5 full-text search table.

    Uses DELETE-then-INSERT to avoid duplicate rows for the same vault_path.
    Retries once on sqlite3.OperationalError.  All other exceptions return
    Failure(recoverable=True).
    """
    from core.config import CONFIG

    resolved_db = db_path if db_path is not None else CONFIG.main.database.path
    _log.info(
        "index_keywords: writing FTS entry db=%s vp=%s title=%s summary_len=%d body_len=%d",
        resolved_db, vault_path, title[:80], len(summary), len(body),
    )
    try:
        with get_connection(db_path) as conn:
            conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (vault_path,))
            conn.execute(
                "INSERT INTO notes_fts(vault_path, title, summary, body) "
                "VALUES (?, ?, ?, ?)",
                (vault_path, title, summary, body),
            )
        _log.info("index_keywords: FTS insert OK vp=%s", vault_path)
    except sqlite3.OperationalError:
        try:
            with get_connection(db_path) as conn:
                conn.execute(
                    "DELETE FROM notes_fts WHERE vault_path = ?", (vault_path,)
                )
                conn.execute(
                    "INSERT INTO notes_fts(vault_path, title, summary, body) "
                    "VALUES (?, ?, ?, ?)",
                    (vault_path, title, summary, body),
                )
            _log.info("index_keywords: FTS insert OK (retry) vp=%s", vault_path)
        except Exception as exc:
            return Failure(
                error=str(exc), recoverable=True, context={"vault_path": vault_path}
            )
    except Exception as exc:
        return Failure(
            error=str(exc), recoverable=True, context={"vault_path": vault_path}
        )

    return Success(None)
