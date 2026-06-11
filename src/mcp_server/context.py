"""ContextInjectionEngine — the per-conversation decision engine.

Phase 3 fills in the methods (build response blocks, dedup, count, gate).
Phase 2 only needs the constructor so the server lifespan can instantiate it.
"""

from __future__ import annotations


class ContextInjectionEngine:
    """Context injection decision engine.  Built in Phase 3."""

    def __init__(self) -> None:
        self._dedup_memory: dict[str, str] = {}  # content_hash -> filename
