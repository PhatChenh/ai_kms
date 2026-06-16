"""Shared fixtures for storage tests.

Resets the cached embedding model between tests so a leftover mock
(or real model) from one test cannot leak into another.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

import retrieval.embeddings as _embeddings


@pytest.fixture(autouse=True)
def _reset_embedding_model():
    saved = _embeddings._model
    try:
        yield
    finally:
        _embeddings._model = saved


@pytest.fixture()
def mock_embedder_1024():
    """Install a 1024-dim mock as the global embedding model and return it."""
    rng = np.random.default_rng(42)
    model = MagicMock()
    model.encode.side_effect = lambda _text: rng.standard_normal(1024).astype(
        np.float32
    )
    _embeddings._model = model
    return model
