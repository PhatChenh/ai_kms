"""Suite-wide fixtures.

Resets the cached embedding model (``retrieval.embeddings._model``) around
every test so a real or mock model loaded by one test cannot leak into
another. Without this, a test that loads the local 384-dim SentenceTransformer
leaves it in the module-global cache; a later test that seeds knowledge_entries
(facts_vec is FLOAT[1024]) then inserts a 384-dim vector and fails with a
dimension mismatch. The leak is invisible when files run in isolation.

test_storage/conftest.py keeps its own save/restore variant plus the
``mock_embedder_1024`` fixture; this root fixture is the suite-wide backstop.
"""

from __future__ import annotations

import pytest

import retrieval.embeddings as _embeddings


@pytest.fixture(autouse=True)
def _reset_embedding_model_global():
    saved = _embeddings._model
    _embeddings._model = None
    try:
        yield
    finally:
        _embeddings._model = saved
