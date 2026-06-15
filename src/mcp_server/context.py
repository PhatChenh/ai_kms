"""ContextInjectionEngine — the per-conversation decision engine.

Phase 9 rewrite: reads from knowledge_entries (structured facts) and
dual-corpus search (search_dual), zero disk reads.  Provides orientation
context blocks for the user-facing AI at conversation start.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from core.result import Failure, Result, Success

_log = logging.getLogger(__name__)

_INBOX_LIKE_PATTERN = "inbox/%"


class ContextInjectionEngine:
    """Per-conversation engine that builds orientation context from DB.

    Instantiated once per conversation via FastMCP lifespan.  Tracks which
    facts and documents have already been surfaced so later queries don't
    repeat the same context (identity-based dedup, not hash-based).
    """

    def __init__(self) -> None:
        self._seen_fact_ids: set[int] = set()
        self._seen_doc_ids: set[int] = set()

    # ======================================================================
    # Identity dedup memory
    # ======================================================================

    def is_fact_seen(self, entry_id: int) -> bool:
        """Return True if this knowledge entry was already injected."""
        return entry_id in self._seen_fact_ids

    def record_fact_seen(self, entry_id: int) -> None:
        """Mark a knowledge entry as injected this conversation."""
        self._seen_fact_ids.add(entry_id)

    def is_doc_seen(self, doc_id: int) -> bool:
        """Return True if this document was already surfaced."""
        return doc_id in self._seen_doc_ids

    def record_doc_seen(self, doc_id: int) -> None:
        """Mark a document as surfaced this conversation."""
        self._seen_doc_ids.add(doc_id)

    # ======================================================================
    # Public API
    # ======================================================================

    def build_vault_info_response(
        self,
        db_path: Path | None = None,
    ) -> Result[list[dict]]:
        """Build a vault-overview response: entity map, orientation facts, inbox stats.

        All data comes from the cloud DB — zero disk reads.
        """
        try:
            blocks: list[dict] = []

            # ---- Entity map ------------------------------------------------
            entity_map_result = self._build_entity_map(db_path)
            if isinstance(entity_map_result, Failure):
                return entity_map_result
            blocks.append(
                {
                    "type": "context",
                    "source": "entity_map",
                    "content": entity_map_result.value,
                }
            )

            # ---- Orientation facts -----------------------------------------
            orientation_result = self._build_orientation_facts(db_path)
            if isinstance(orientation_result, Failure):
                return orientation_result
            blocks.append(
                {
                    "type": "context",
                    "source": "orientation_facts",
                    "content": orientation_result.value,
                }
            )

            # ---- Inbox stats -----------------------------------------------
            inbox_count, last_capture = self._get_inbox_stats(db_path)
            inbox_text = (
                f"# Inbox\n\n"
                f"- Unprocessed notes: {inbox_count}\n"
                f"- Last capture: {last_capture or 'N/A'}"
            )
            blocks.append(
                {
                    "type": "context",
                    "source": "inbox_stats",
                    "content": inbox_text,
                }
            )

            return Success(blocks)

        except Exception as exc:
            _log.warning("build_vault_info_response failed: %s", exc)
            return Failure(str(exc), recoverable=True, context={})

    def build_search_response(
        self,
        query: str,
        *,
        project: str | None = None,
        since: str | None = None,
        until: str | None = None,
        location: str | None = None,
        max_results: int | None = None,
        db_path: Path | None = None,
    ) -> Result[list[dict]]:
        """Build a search response: orientation facts + query facts + documents.

        Uses dual-corpus search (facts + documents), extracts entities from
        fact results, fetches orientation facts for those entities, and
        assembles everything with identity dedup.
        """
        try:
            from datetime import datetime  # noqa: C0415

            from retrieval.search import search_dual  # noqa: C0415

            # Parse date strings if provided
            since_dt: datetime | None = None
            if since is not None:
                since_dt = datetime.fromisoformat(since)
            until_dt: datetime | None = None
            if until is not None:
                until_dt = datetime.fromisoformat(until)

            date_range: tuple | None = None
            if since_dt is not None or until_dt is not None:
                date_range = (since_dt, until_dt)

            # ---- Step 1: dual-corpus search --------------------------------
            match search_dual(
                query=query,
                project=project,
                date_range=date_range,
                max_results=max_results,
                location=location,
                db_path=db_path,
            ):
                case Success(value=dual):
                    facts, docs = dual.facts, dual.documents
                case Failure() as f:
                    return f

            blocks: list[dict] = []

            # ---- Step 2: orientation facts for matched entities ------------
            if facts:
                entities = list({f.entity for f in facts if f.entity})
                orientation_blocks = self._build_orientation_for_entities(
                    entities, db_path
                )
                if isinstance(orientation_blocks, Failure):
                    return orientation_blocks
                blocks.extend(orientation_blocks.value)

            # ---- Step 3: query fact blocks ---------------------------------
            for fact in facts:
                if self.is_fact_seen(fact.entry_id):
                    continue
                self.record_fact_seen(fact.entry_id)
                blocks.append(
                    {
                        "type": "fact_result",
                        "entry_id": fact.entry_id,
                        "dimension": fact.dimension,
                        "entity": fact.entity,
                        "fact": fact.fact,
                        "confidence": fact.confidence,
                        "trust_score": fact.trust_score,
                        "score": fact.score,
                    }
                )

            # ---- Step 4: document result blocks ----------------------------
            for doc in docs:
                doc_id = getattr(doc, "id", None)
                if doc_id is not None and self.is_doc_seen(doc_id):
                    continue
                if doc_id is not None:
                    self.record_doc_seen(doc_id)
                blocks.append(
                    {
                        "type": "result_card",
                        "data": doc,
                    }
                )

            return Success(blocks)

        except Exception as exc:
            _log.warning("build_search_response failed: %s", exc)
            return Failure(str(exc), recoverable=True, context={"query": query})

    # ======================================================================
    # Entity map
    # ======================================================================

    def _build_entity_map(self, db_path: Path | None = None) -> Result[str]:
        """Build an entity map grouped by dimension from knowledge_entries.

        Caps entities per dimension at max_entities_per_dimension (config).

        Uses raw SQL for the grouped aggregate query
        (DISTINCT dimension, entity, COUNT … GROUP BY) — the
        ``knowledge_entries`` module does not expose an equivalent.
        """
        try:
            from storage.db import get_connection  # noqa: C0415
            from core.config import CONFIG  # noqa: C0415

            max_entities = CONFIG.main.mcp.context_injection.max_entities_per_dimension

            with get_connection(db_path, readonly=True) as conn:
                rows = conn.execute(
                    "SELECT dimension, entity, COUNT(*) as cnt "
                    "FROM knowledge_entries "
                    "WHERE (status IS NULL OR status != 'retired') AND entity IS NOT NULL AND entity != '' "
                    "GROUP BY dimension, entity "
                    "ORDER BY dimension, cnt DESC"
                ).fetchall()

            # Group by dimension
            by_dim: dict[str, list[tuple[str, int]]] = {}
            for dim, entity, cnt in rows:
                by_dim.setdefault(dim, []).append((entity, cnt))

            lines = ["# Knowledge Map", ""]
            for dim in sorted(by_dim.keys()):
                entities = by_dim[dim]
                shown = entities[:max_entities]
                lines.append(f"## {dim}")
                for entity, cnt in shown:
                    lines.append(f"- {entity} ({cnt})")
                if len(entities) > max_entities:
                    extra = len(entities) - max_entities
                    lines.append(f"  +{extra} more")
                lines.append("")

            return Success("\n".join(lines))

        except Exception as exc:
            return Failure(str(exc), recoverable=True, context={"op": "entity_map"})

    # ======================================================================
    # Orientation facts
    # ======================================================================

    def _build_orientation_facts(
        self, db_path: Path | None = None
    ) -> Result[str]:
        """Build orientation fact bullets for each dimension.

        Uses 4-key ranking from query_ranked_for_orientation.
        Capped per dimension at max_orientation_facts_per_dimension.
        """
        try:
            from storage.knowledge_entries import (  # noqa: C0415
                query_ranked_for_orientation,
            )
            from core.config import CONFIG  # noqa: C0415

            max_per_dim = CONFIG.main.mcp.context_injection.max_orientation_facts_per_dimension

            from storage.db import get_connection  # noqa: C0415

            with get_connection(db_path, readonly=True) as conn:
                dim_rows = conn.execute(
                    "SELECT DISTINCT dimension FROM knowledge_entries "
                    "WHERE (status IS NULL OR status != 'retired') ORDER BY dimension"
                ).fetchall()

            lines = ["# Key Facts", ""]
            for (dim,) in dim_rows:
                match query_ranked_for_orientation(
                    dimension=dim,
                    limit=max_per_dim,
                    db_path=db_path,
                ):
                    case Success(value=entries):
                        if not entries:
                            continue
                        lines.append(f"## {dim}")
                        for entry in entries:
                            self.record_fact_seen(entry.id)
                            lines.append(
                                f"- [{entry.entity}] {entry.fact} "
                                f"(confidence: {entry.confidence})"
                            )
                        lines.append("")
                    case Failure() as f:
                        return f

            return Success("\n".join(lines))

        except Exception as exc:
            return Failure(
                str(exc), recoverable=True, context={"op": "orientation_facts"}
            )

    def _build_orientation_for_entities(
        self,
        entities: list[str],
        db_path: Path | None = None,
    ) -> Result[list[dict]]:
        """Fetch orientation facts for specific entities, with dedup.

        A ``seen_dimensions`` set tracks which dimensions have already been
        injected for earlier entities in *entities*.  Once a dimension has
        been claimed, later entities that belong to the same dimension are
        skipped — this prevents repeating the same dimension block multiple
        times in a single response.
        """
        try:
            from storage.knowledge_entries import (  # noqa: C0415
                query_ranked_for_orientation,
            )
            from core.config import CONFIG  # noqa: C0415

            max_per_dim = CONFIG.main.mcp.context_injection.max_orientation_facts_per_dimension

            blocks: list[dict] = []
            seen_dimensions: set[str] = set()

            for entity in entities:
                match query_ranked_for_orientation(
                    entity=entity,
                    limit=max_per_dim,
                    db_path=db_path,
                ):
                    case Success(value=entries):
                        by_dim: dict[str, list] = {}
                        for e in entries:
                            if e.dimension not in seen_dimensions:
                                by_dim.setdefault(e.dimension, []).append(e)

                        for dim, dim_entries in by_dim.items():
                            seen_dimensions.add(dim)
                            lines = [f"## {dim} — {entity}"]
                            for entry in dim_entries:
                                if not self.is_fact_seen(entry.id):
                                    self.record_fact_seen(entry.id)
                                lines.append(
                                    f"- [{entry.entity}] {entry.fact} "
                                    f"(confidence: {entry.confidence})"
                                )
                            blocks.append(
                                {
                                    "type": "context",
                                    "source": f"orientation:{dim}:{entity}",
                                    "content": "\n".join(lines),
                                }
                            )
                    case Failure() as f:
                        return f

            return Success(blocks)

        except Exception as exc:
            return Failure(
                str(exc), recoverable=True, context={"op": "orientation_entities"}
            )

    # ======================================================================
    # Inbox stats
    # ======================================================================

    def _get_inbox_stats(self, db_path: Path | None = None) -> tuple[int, str]:
        """Get inbox note count and most recent capture time from the DB."""
        try:
            from storage.db import get_connection  # noqa: C0415

            with get_connection(db_path, readonly=True) as conn:
                row = conn.execute(
                    "SELECT COUNT(*), MAX(updated_at) FROM documents "
                    "WHERE vault_path LIKE 'inbox/%'"
                ).fetchone()
                count = row[0] if row else 0
                last_updated = row[1] or "" if row else ""
                return count, last_updated
        except Exception:
            return 0, ""
