"""Shared fixtures for retrieval tests.

The retrieval modules cache their (expensive) ML models in module-level
globals (``embeddings._model``, ``reranker._reranker``).  Several tests
overwrite ``embeddings._model`` with a mock and never restore it.  Without a
reset, a leftover mock (or a leftover real model) leaks into later tests whose
outcome then silently depends on test execution order.  This autouse fixture
snapshots and restores both globals around every test in this package.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

import retrieval.embeddings as _embeddings
import retrieval.reranker as _reranker


@pytest.fixture(autouse=True)
def _reset_retrieval_model_globals():
    saved_model = _embeddings._model
    saved_reranker = _reranker._reranker
    try:
        yield
    finally:
        _embeddings._model = saved_model
        _reranker._reranker = saved_reranker


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
