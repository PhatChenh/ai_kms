from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from core.exceptions import StorageError
from core.result import Failure, Result, Success

_PROJECT_ROOT = Path(__file__).parent.parent
_SCHEMA_FILE = _PROJECT_ROOT / "storage" / "schema.sql"
_MIGRATIONS_DIR = _PROJECT_ROOT / "storage" / "migrations"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
    version: int = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    for path in sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        file_version = int(path.name[:3])
        if file_version > version:
            try:
                conn.executescript(path.read_text())
                conn.execute("UPDATE schema_version SET version = ?", (file_version,))
                conn.commit()
                version = file_version
            except sqlite3.Error as exc:
                conn.rollback()
                raise StorageError(str(exc)) from exc


def init_db(db_path: Path | None = None) -> Result[None]:
    if db_path is None:
        from core.config import CONFIG
        resolved: Path = CONFIG.main.database.path
    else:
        resolved = db_path
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(resolved)
        conn.executescript(_SCHEMA_FILE.read_text())
        _run_migrations(conn)
        conn.close()
        return Success(None)
    except StorageError as exc:
        return Failure(error=str(exc), recoverable=False, context={"db_path": str(resolved)})
    except sqlite3.Error as exc:
        return Failure(error=str(exc), recoverable=False, context={"db_path": str(resolved)})


@contextmanager
def get_connection(db_path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    if db_path is None:
        from core.config import CONFIG
        resolved: Path = CONFIG.main.database.path
    else:
        resolved = db_path
    conn = _connect(resolved)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
