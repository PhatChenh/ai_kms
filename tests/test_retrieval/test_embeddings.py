"""Tests for the meaning indexer (retrieval/embeddings.py)."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from storage.db import _connect, init_db


@pytest.fixture
def search_db(tmp_path: Path) -> Path:
    """Create a temp DB with migration 007 applied (embeddings_vec + notes_fts)."""
    db_path = tmp_path / "test_search.db"
    result = init_db(db_path)
    assert result.is_success(), f"init_db failed: {result}"
    return db_path


@pytest.fixture
def mock_encode() -> MagicMock:
    """Return a mock SentenceTransformer that produces 384-dim float32 zeros."""
    model = MagicMock()
    model.encode.return_value = np.zeros(384, dtype=np.float32)
    return model


class TestIndexEmbeddingInsert:
    """Tracer bullet: verify basic insert creates a row with correct embedding."""

    def test_insert_creates_row_with_384_dim_embedding(
        self, search_db: Path, mock_encode: MagicMock
    ) -> None:
        import retrieval.embeddings as em

        em._model = mock_encode

        result = em.index_embedding(
            vault_path="Projects/Alpha/note.md",
            title="Test Note",
            note_type="meeting-notes",
            tags=["project-alpha", "stakeholder"],
            summary="A summary of the meeting.",
            db_path=search_db,
        )

        assert result.is_success(), f"Expected Success, got {result}"

        conn = _connect(search_db)
        try:
            row = conn.execute(
                "SELECT vault_path, length(embedding), vec_length(embedding) "
                "FROM embeddings_vec WHERE vault_path = ?",
                ("Projects/Alpha/note.md",),
            ).fetchone()
            assert row is not None, "Expected a row in embeddings_vec"
            assert row[0] == "Projects/Alpha/note.md"
            assert row[1] == 1536, f"Expected 1536 bytes (384*4), got {row[1]}"
            assert row[2] == 384, f"Expected vec_length 384, got {row[2]}"
        finally:
            conn.close()


class TestIndexEmbeddingUpsert:
    """DELETE+INSERT: calling twice with same vault_path leaves exactly one row."""

    def test_calling_twice_with_same_path_leaves_one_row(
        self, search_db: Path, mock_encode: MagicMock
    ) -> None:
        import retrieval.embeddings as em

        em._model = mock_encode

        # First call
        r1 = em.index_embedding(
            vault_path="Projects/Beta/doc.md",
            title="First",
            note_type="note",
            tags=["a"],
            summary="First summary.",
            db_path=search_db,
        )
        assert r1.is_success()

        # Second call -- same vault_path, different content
        r2 = em.index_embedding(
            vault_path="Projects/Beta/doc.md",
            title="Second",
            note_type="reference",
            tags=["b", "c"],
            summary="Second summary.",
            db_path=search_db,
        )
        assert r2.is_success()

        conn = _connect(search_db)
        try:
            rows = conn.execute(
                "SELECT vault_path FROM embeddings_vec WHERE vault_path = ?",
                ("Projects/Beta/doc.md",),
            ).fetchall()
            assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        finally:
            conn.close()


class TestContextualStringFormat:
    """P3-IDX-07: the string passed to .encode() follows the deterministic format."""

    def test_encode_receives_full_context_string(
        self, search_db: Path, mock_encode: MagicMock
    ) -> None:
        import retrieval.embeddings as em

        em._model = mock_encode

        em.index_embedding(
            vault_path="p.md",
            title="Team Sync",
            note_type="meeting-notes",
            tags=["alpha", "beta"],
            summary="Discussed roadmap.",
            db_path=search_db,
        )

        mock_encode.encode.assert_called_once_with(
            "title: Team Sync | type: meeting-notes | tags: alpha, beta | Discussed roadmap."
        )

    def test_omits_summary_suffix_when_summary_is_none(
        self, search_db: Path, mock_encode: MagicMock
    ) -> None:
        import retrieval.embeddings as em

        em._model = mock_encode

        em.index_embedding(
            vault_path="p.md",
            title="Quick Note",
            note_type=None,
            tags=["x"],
            summary=None,
            db_path=search_db,
        )

        encoded = mock_encode.encode.call_args[0][0]
        assert encoded == "title: Quick Note | type: None | tags: x"
        assert not encoded.endswith(" | ")


class TestRetryOnOperationalError:
    """Single OperationalError is retried once and recovers."""

    def test_retry_succeeds_after_single_operational_error(
        self, search_db: Path, mock_encode: MagicMock
    ) -> None:
        import retrieval.embeddings as em
        from storage.db import get_connection as real_get_connection

        em._model = mock_encode

        call_count = [0]

        def side_effect(*args: object, **kwargs: object):
            call_count[0] += 1
            if call_count[0] == 1:
                raise sqlite3.OperationalError("simulated transient error")
            return real_get_connection(*args, **kwargs)

        with patch.object(em, "get_connection", side_effect=side_effect):
            result = em.index_embedding(
                vault_path="r.md",
                title="R",
                note_type="note",
                tags=["t"],
                summary="s",
                db_path=search_db,
            )

        assert result.is_success(), f"Expected Success after retry, got {result}"
        assert call_count[0] == 2, f"Expected 2 calls, got {call_count[0]}"

        # Verify the row exists after successful retry
        conn = _connect(search_db)
        try:
            row = conn.execute(
                "SELECT vault_path FROM embeddings_vec WHERE vault_path = ?",
                ("r.md",),
            ).fetchone()
            assert row is not None, "Expected row after retry succeeded"
        finally:
            conn.close()

    def test_double_failure_returns_recoverable_failure(
        self, search_db: Path, mock_encode: MagicMock
    ) -> None:
        import retrieval.embeddings as em

        em._model = mock_encode

        def always_raise(*args: object, **kwargs: object):
            raise sqlite3.OperationalError("persistent error")

        with patch.object(em, "get_connection", side_effect=always_raise):
            result = em.index_embedding(
                vault_path="f.md",
                title="F",
                note_type="note",
                tags=["t"],
                summary="s",
                db_path=search_db,
            )

        assert result.is_failure(), "Expected Failure after two failures"
        assert result.recoverable is True


class TestModelLoadFailure:
    """SentenceTransformer load failure returns Failure(recoverable=True)."""

    def test_model_load_failure_returns_recoverable_failure(
        self, search_db: Path
    ) -> None:
        import retrieval.embeddings as em

        # Force _model to None so _get_model will try to create a new one
        em._model = None

        with patch(
            "sentence_transformers.SentenceTransformer",
            side_effect=RuntimeError("cannot load model"),
        ):
            result = em.index_embedding(
                vault_path="m.md",
                title="M",
                note_type="note",
                tags=["t"],
                summary="s",
                db_path=search_db,
            )

        assert result.is_failure(), "Expected Failure on model load error"
        assert result.recoverable is True
        assert "cannot load model" in result.error
