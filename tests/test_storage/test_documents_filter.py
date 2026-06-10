"""tests/test_storage/test_documents_filter.py — Candidate Filter (Component 1)"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.result import Success
from storage.db import init_db


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Empty temp database with full schema (documents + search tables)."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Tracer bullet — no-args sentinel
# ---------------------------------------------------------------------------


def test_filter_no_args_returns_none_sentinel(db):
    """filter_paths() with no project and no since → Success(None)."""
    from storage.documents import filter_paths

    r = filter_paths(db_path=db)
    assert isinstance(r, Success)
    assert r.value is None


# ---------------------------------------------------------------------------
# Project filter
# ---------------------------------------------------------------------------


def test_filter_by_project_returns_only_matching(db):
    """Seed 3 rows (2 Alpha, 1 Beta). filter_paths(project="Alpha") returns 2 Alpha paths."""
    import sqlite3

    from storage.documents import filter_paths

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executemany(
            """INSERT INTO documents
               (vault_path, title, updated_at, project)
               VALUES (?, ?, ?, ?)""",
            [
                ("Projects/Alpha/note1.md", "Note 1", "2026-06-01 12:00:00", "Alpha"),
                ("Projects/Alpha/note2.md", "Note 2", "2026-06-02 12:00:00", "Alpha"),
                ("Projects/Beta/note3.md", "Note 3", "2026-06-03 12:00:00", "Beta"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    r = filter_paths(project="Alpha", db_path=db)
    assert isinstance(r, Success)
    assert r.value is not None
    assert len(r.value) == 2
    assert all("Alpha" in p for p in r.value)


# ---------------------------------------------------------------------------
# Date filter
# ---------------------------------------------------------------------------


def test_filter_by_date_returns_recent_only(db):
    """Seed 2 rows with different dates. filter_paths(since=2026-05-01) returns only June row."""
    import sqlite3
    from datetime import datetime

    from storage.documents import filter_paths

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executemany(
            """INSERT INTO documents
               (vault_path, title, updated_at)
               VALUES (?, ?, ?)""",
            [
                ("inbox/recent.md", "Recent", "2026-06-01 00:00:00"),
                ("inbox/old.md", "Old", "2026-01-01 00:00:00"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    r = filter_paths(since=datetime(2026, 5, 1), db_path=db)
    assert isinstance(r, Success)
    assert r.value is not None
    assert r.value == ["inbox/recent.md"]


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------


def test_filter_by_project_and_date_combined(db):
    """Seed 3 rows across 2 projects and 2 dates. Filter by both → intersection only."""
    import sqlite3
    from datetime import datetime

    from storage.documents import filter_paths

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executemany(
            """INSERT INTO documents
               (vault_path, title, updated_at, project)
               VALUES (?, ?, ?, ?)""",
            [
                (
                    "Projects/Alpha/recent.md",
                    "Alpha Recent",
                    "2026-06-01 00:00:00",
                    "Alpha",
                ),
                ("Projects/Alpha/old.md", "Alpha Old", "2026-01-01 00:00:00", "Alpha"),
                (
                    "Projects/Beta/recent.md",
                    "Beta Recent",
                    "2026-06-02 00:00:00",
                    "Beta",
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    r = filter_paths(project="Alpha", since=datetime(2026, 5, 1), db_path=db)
    assert isinstance(r, Success)
    assert r.value is not None
    # Only the Alpha recent row should match (intersection: Alpha AND since)
    assert r.value == ["Projects/Alpha/recent.md"]


def test_filter_no_matches_returns_empty_list(db):
    """Filter with a project that has no rows → Success([]), not None."""
    from storage.documents import filter_paths

    r = filter_paths(project="GhostProject", db_path=db)
    assert isinstance(r, Success)
    assert r.value == []


def test_filter_with_until_upper_bound(db):
    """Seed rows. Filter with both since and until. Assert only rows in the window."""
    import sqlite3
    from datetime import datetime

    from storage.documents import filter_paths

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executemany(
            """INSERT INTO documents
               (vault_path, title, updated_at)
               VALUES (?, ?, ?)""",
            [
                ("inbox/may.md", "May", "2026-05-15 00:00:00"),
                ("inbox/jun.md", "June", "2026-06-15 00:00:00"),
                ("inbox/jul.md", "July", "2026-07-15 00:00:00"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    r = filter_paths(
        since=datetime(2026, 6, 1),
        until=datetime(2026, 6, 30),
        db_path=db,
    )
    assert isinstance(r, Success)
    assert r.value is not None
    assert r.value == ["inbox/jun.md"]
