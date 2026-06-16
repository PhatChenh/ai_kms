from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from core.result import Failure, Result, Success
from storage.db import get_connection

_model = None


class _APIEmbedder:
    """Thin wrapper around OpenAI-compat embeddings endpoint.

    Exposes ``.encode(text) -> np.ndarray`` so callers that previously
    used a SentenceTransformer model need zero changes.
    """

    def __init__(self, client, model_name: str) -> None:
        self._client = client
        self._model_name = model_name

    def encode(self, text: str) -> np.ndarray:
        resp = self._client.embeddings.create(model=self._model_name, input=text)
        return np.array(resp.data[0].embedding, dtype=np.float32)


def _get_model():
    """Lazy-load the embedding model.

    When ``providers.embeddings == "openai"``, uses the OpenAI-compat API
    (greennode endpoint).  Otherwise falls back to local SentenceTransformer.
    """
    global _model
    if _model is None:
        from core.config import CONFIG  # noqa: C0415  -- lazy import (C-17)

        provider_name = CONFIG.main.providers.for_task("embeddings")
        if provider_name == "openai":
            import os

            import openai  # noqa: C0415

            compat_cfg = CONFIG.main.openai_compat
            api_key = os.environ.get(compat_cfg.api_key_env, "")
            _model = _APIEmbedder(
                client=openai.OpenAI(
                    api_key=api_key,
                    base_url=compat_cfg.base_url,
                    timeout=compat_cfg.timeout,
                ),
                model_name=compat_cfg.embedding_model,
            )
        else:
            from sentence_transformers import SentenceTransformer  # noqa: C0415

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

    Lazy-loads the embedding model on first call.  Failures are logged but
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
