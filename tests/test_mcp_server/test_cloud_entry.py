"""
tests/test_mcp_server/test_cloud_entry.py

Phase 4 Component: Cloud entry point + startup DB ordering (C2-3, C2-4).
TDD: RED → GREEN → REFACTOR per test.

Test requirements:
  1. C2-4 ordering / idempotency — build_app() creates DB on first call,
     does NOT wipe on second call.
  2. A1 lifespan — app is a valid Starlette ASGI app; /health responds 200.
  3. P5-DEPLOY-12 — importing cloud_entry does NOT alter server.main (stdio).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient


# ============================================================================
# C2-4 ordering / idempotency
# ============================================================================


class TestDbOrdering:
    """build_app() ensures DB exists on first call and is idempotent."""

    def test_first_call_creates_db(self, tmp_path: Path) -> None:
        """First call with empty DB → creates DB at data path, latest schema.

        P5-DEPLOY-10
        """
        db_path = tmp_path / "test.db"
        assert not db_path.exists()

        from mcp_server.cloud_entry import build_app

        build_app(db_path=db_path)

        # DB file was created
        assert db_path.exists(), "DB file should exist after build_app()"

        # Verify schema: documents has full_body, knowledge_entries exists
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        try:
            # documents.full_body column
            cols_docs = {
                row[1]
                for row in conn.execute("PRAGMA table_info(documents)").fetchall()
            }
            assert "full_body" in cols_docs, (
                f"documents table should have full_body column, got {cols_docs}"
            )

            # knowledge_entries exists
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "knowledge_entries" in tables, (
                f"knowledge_entries table missing; got {tables}"
            )

            # schema_version is at the latest migration
            version = conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()[0]
            # Latest migration number (008)
            # Use >= so adding a new migration doesn't break this test.
            # The exact-version pin is enforced by test_migration_NNN tests.
            assert version >= 8, (
                f"Expected schema version 8, got {version}"
            )
        finally:
            conn.close()

    def test_second_call_is_idempotent(self, tmp_path: Path) -> None:
        """Second call on populated DB → prior rows intact, no wipe/duplicate.

        P5-DEPLOY-11
        """
        db_path = tmp_path / "test.db"
        from mcp_server.cloud_entry import build_app

        # First call builds DB
        build_app(db_path=db_path)

        # Insert a row directly
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "INSERT INTO documents (vault_path, title, full_body) "
                "VALUES (?, ?, ?)",
                ("test/path.md", "Test Doc", "Some content"),
            )
            conn.commit()
            row_count_before = conn.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0]
        finally:
            conn.close()

        assert row_count_before == 1, "Expected 1 row before second call"

        # Second call — should NOT wipe
        build_app(db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        try:
            row_count_after = conn.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0]
            # Verify data still there
            row = conn.execute(
                "SELECT vault_path, title, full_body FROM documents WHERE vault_path=?",
                ("test/path.md",),
            ).fetchone()
        finally:
            conn.close()

        assert row_count_after == row_count_before, (
            f"Row count changed: {row_count_before} → {row_count_after}"
        )
        assert row is not None, "Original row should still exist"
        assert row[0] == "test/path.md"
        assert row[1] == "Test Doc"
        assert row[2] == "Some content"


# ============================================================================
# A1 lifespan — app is a valid ASGI app, /health responds 200
# ============================================================================


class TestAppLifespan:
    """build_app() returns a valid Starlette ASGI app."""

    def test_health_returns_200_without_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /health → 200 with no key required.

        P5-DEPLOY-01
        """
        monkeypatch.delenv("KMS_DAEMON_API_KEY", raising=False)

        db_path = tmp_path / "test.db"
        from mcp_server.cloud_entry import build_app

        app = build_app(db_path=db_path)
        client = TestClient(app)

        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_app_is_starlette_asgi(self, tmp_path: Path) -> None:
        """The returned app is a valid Starlette ASGI application."""
        from starlette.applications import Starlette

        db_path = tmp_path / "test.db"
        from mcp_server.cloud_entry import build_app

        app = build_app(db_path=db_path)
        assert isinstance(app, Starlette), (
            f"Expected Starlette app, got {type(app)}"
        )

        # It starts and responds
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200


# ============================================================================
# P5-DEPLOY-12 — importing cloud_entry does NOT break server.main
# ============================================================================


class TestImportDoesNotBreakStdio:
    """Importing cloud_entry leaves the stdio entry point untouched."""

    def test_server_main_unchanged_after_cloud_entry_import(self) -> None:
        """After importing cloud_entry, server.main() still exists and works.

        P5-DEPLOY-12
        """
        # Import cloud_entry first (this should be safe)
        import mcp_server.cloud_entry  # noqa: F401

        # Then import server.main — this must not crash
        from mcp_server.server import main

        assert callable(main), "server.main should be callable"

    def test_cloud_entry_import_does_not_crash(self) -> None:
        """Importing mcp_server.cloud_entry succeeds with no errors."""
        import mcp_server.cloud_entry  # noqa: F401

        # If we got here, the import succeeded
        assert True
