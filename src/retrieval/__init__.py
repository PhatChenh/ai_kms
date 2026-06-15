from retrieval import search as _search_module
from retrieval.reranker import SearchResult

search = _search_module.search
search_dual = _search_module.search_dual
DualCorpusResult = _search_module.DualCorpusResult

__all__ = ["search", "search_dual", "DualCorpusResult", "SearchResult"]
