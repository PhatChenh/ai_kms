"""Tests for SearchConfig."""

from core.config import SearchConfig


def test_search_config_defaults():
    """P3-IDX-10: SearchConfig has correct defaults."""
    sc = SearchConfig()
    assert sc.embedding_model == "all-MiniLM-L6-v2"
    assert sc.embedding_dim == 1024
    assert sc.reranker_model == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert sc.max_candidates == 20
    assert sc.max_results == 10
