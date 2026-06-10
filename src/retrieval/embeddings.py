from __future__ import annotations

import sqlite3
from pathlib import Path

from core.result import Failure, Result, Success
from storage.db import get_connection

_model = None


def _get_model():
    """Lazy-load SentenceTransformer from config model name (C-17 compliant)."""
    global _model
    if _model is None:
        from core.config import CONFIG  # noqa: C0415  -- lazy import (C-17)
        from sentence_transformers import SentenceTransformer

        model_name = CONFIG.main.search.embedding_model
        _model = SentenceTransformer(model_name)
    return _model


def _build_context_text(
    title: str,
    note_type: str | None,
    tags: list[str],
    summary: str | None,
) -> str:
    """Build the contextual string for embedding encoding.

    Format: ``title: {title} | type: {type} | tags: {csv} | {summary}``
    The summary suffix is omitted when summary is None or empty.
    """
    tags_csv = ", ".join(tags)
    parts = [f"title: {title}", f"type: {note_type}", f"tags: {tags_csv}"]
    if summary:
        parts.append(summary)
    return " | ".join(parts)


def index_embedding(
    vault_path: str,
    title: str,
    note_type: str | None,
    tags: list[str],
    summary: str | None,
    db_path: Path | None = None,
) -> Result[None]:
    """Store a semantic embedding for a note in the embeddings_vec table.

    Lazy-loads SentenceTransformer on first call.  Failures are logged but
    never propagate -- callers treat this as best-effort.
    """
    try:
        model = _get_model()
    except Exception as exc:
        return Failure(
            error=str(exc), recoverable=True, context={"vault_path": vault_path}
        )

    text = _build_context_text(title, note_type, tags, summary)
    embedding = model.encode(text)

    # Convert to raw float32 bytes for sqlite-vec blob storage.
    if hasattr(embedding, "numpy"):
        embedding = embedding.numpy()
    blob = embedding.astype("float32").tobytes()

    try:
        with get_connection(db_path) as conn:
            conn.execute(
                "DELETE FROM embeddings_vec WHERE vault_path = ?", (vault_path,)
            )
            conn.execute(
                "INSERT INTO embeddings_vec(vault_path, embedding) VALUES (?, ?)",
                (vault_path, blob),
            )
    except sqlite3.OperationalError:
        try:
            with get_connection(db_path) as conn:
                conn.execute(
                    "DELETE FROM embeddings_vec WHERE vault_path = ?", (vault_path,)
                )
                conn.execute(
                    "INSERT INTO embeddings_vec(vault_path, embedding) VALUES (?, ?)",
                    (vault_path, blob),
                )
        except Exception as exc:
            return Failure(
                error=str(exc), recoverable=True, context={"vault_path": vault_path}
            )
    except Exception as exc:
        return Failure(
            error=str(exc), recoverable=True, context={"vault_path": vault_path}
        )

    return Success(None)
