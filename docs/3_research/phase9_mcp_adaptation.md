# Phase 9 — MCP Adaptation: Research Verification

_Created: 2026-06-15_
_Input: Spec (docs/2_specs/phase9_mcp_adaptation.md)_
_Status: COMPLETE_

## Overview

Phase 9 rewires the MCP server from a file-based vault reader to a cloud database consumer. This research verifies the spec's 14 assumptions against the actual codebase — checking that claimed APIs, SQL patterns, and function signatures match what the code really does. Every finding below was confirmed by reading the named source file, not by trusting the spec.

## Spec Verification Table

| # | Assumption | Spec Claim | Code Reality | Status |
|---|---|---|---|---|
| 1 | FTS5 external content sync syntax | Spec says to match `notes_fts` sync pattern (plain DELETE/INSERT). Proposes `facts_fts` with `content='knowledge_entries', content_rowid='id'` (external content mode). | `notes_fts` (migration 007) is a **regular** FTS5 table, NOT external content. Sync uses plain `DELETE FROM notes_fts WHERE vault_path = ?` then `INSERT INTO notes_fts(...)`. External content FTS5 tables require special delete syntax: `INSERT INTO facts_fts(facts_fts, entry_id, entity, fact) VALUES('delete', ?, ?, ?)`. The spec proposes external content mode but claims the sync matches the existing pattern — it does not. The Python sync code in P9-B-02 must use the special external-content delete/insert commands. | ⚠️ PARTIALLY VALID |
| 2 | `facts_vec` MATCH+k syntax | `WHERE embedding MATCH ? AND k = ?` filtered KNN works on vec0. Same ADR-0009 pattern as `embeddings_vec`. | Confirmed in `ranker.py:192-198`. Exact syntax: `SELECT vault_path, distance FROM embeddings_vec WHERE embedding MATCH ? AND k = ?` with optional `AND vault_path IN (...)`. Same pattern applies to `facts_vec` with `entry_id` instead of `vault_path`. | ✅ VALIDATED |
| 3 | Short-fact embedding separation | Research gate — cannot be verified by code reading alone. Config-driven `keyword_weight` fallback exists. | Embedding model is `all-MiniLM-L6-v2` (configured in `CONFIG.main.search.embedding_model`, loaded via `_get_model()` in `embeddings.py:12`). The keyword_weight config fallback is a sound mitigation. Runtime experiment required. | 🔬 UNVERIFIABLE |
| 4 | `retrieval_count` as REAL | SQLite type flexibility lets the existing `INTEGER` column store REAL values. | Migration 010 defines `ALTER TABLE knowledge_entries ADD COLUMN retrieval_count INTEGER DEFAULT 0`. SQLite type affinity is "type-flexible" — an INTEGER-affinity column will store any value type including REAL. `UPDATE ... SET retrieval_count = retrieval_count * 0.95 + 1.0` will store the float result correctly. Verified by SQLite docs on type affinity. | ✅ VALIDATED |
| 5 | `_get_model` import path | `embeddings.py::_get_model()` loads the sentence-transformer. Importing inside `knowledge_entries.upsert()` may trigger CONFIG at module scope. | `_get_model()` at `embeddings.py:12` uses lazy CONFIG import inside the function body (`from core.config import CONFIG` at line 16, inside `if _model is None` guard). Importing `_get_model` from `retrieval.embeddings` does NOT trigger CONFIG validation — the import only binds the function reference; CONFIG is loaded on first _call_. Safe for use in `knowledge_entries.upsert()` with a function-level import. | ✅ VALIDATED |
| 6 | `capture_upload` vault_path flexibility | Spec claims `capture_upload` accepts any vault_path including synthetic `chat/...` paths, and writes to DB not disk. | Confirmed at `capture.py:146`. `capture_upload(vault_path, extracted_text, content_hash, ...)` takes `vault_path: str` as a POSIX-relative path. The text branch (when `extracted_text` is provided) calls `documents.upsert_from_upload(vault_path=vault_path, ...)` which does a DB INSERT/UPDATE — no disk write to vault. The `chat/2026-06-15-insight.md` pattern works: `vault_path` is just a string key, no validation that the path exists on disk. The only risk: the `get_by_path` dedup check at line 208 and `Path(vault_path).stem` at line 222 both work on any well-formed POSIX string. | ✅ VALIDATED |
| 7 | `audit.write` signature | Spec claims `audit.write(decision, source_ids, pipeline, stage, ...)`. | **Signature differs from spec claim.** `core/audit.py:11` shows `write(decision: AIDecision, *, pipeline: str, stage: str, outcome: str, db_path=None) -> Result[int]`. First arg is an `AIDecision` Pydantic model (not raw fields). `AIDecision` has: `action: str`, `confidence: float`, `reasoning: str`, `source_ids: list[str]`. `source_ids` is embedded INSIDE the `AIDecision` object, not passed separately. For `kms_correct`, callers must construct an `AIDecision(action="correct:edit_fact", confidence=1.0, reasoning=reason, source_ids=[str(entry_id)])` then pass it to `audit.write(decision, pipeline="correct", stage="edit_fact", outcome="APPLIED")`. | ❌ INVALIDATED |
| 8 | `get_entry_by_id` existence | Spec assumes no single-row lookup by id exists in `knowledge_entries.py`. | Confirmed: NO `get_entry_by_id` function exists. Available functions: `query_by_dimension`, `query_by_entity`, `retire`, `get_confident_and_pending`, `query_ranked_by_dimension`, `prune_sources`, `upsert`. P9-A-02 must add `get_entry_by_id`. | ✅ VALIDATED |
| 9 | Reranker card builder | Spec claims `_card_from_row` in `search.py` builds `SearchResult` cards and can be extended to include `id`. Implies only this path needs updating. | **Two paths create `SearchResult`.** (1) `search.py:23` `_card_from_row(row, snippet, score)` — filter-only branch. (2) `reranker.py:163-170` builds `SearchResult(...)` inline inside `rerank()` from row data + cross-encoder score. Both paths must add `id=row.id`. The `SearchResult` dataclass is defined in `reranker.py:35` (not `search.py`). It currently has 5 fields: `vault_path, summary, snippet, score, metadata` — no `id` field yet. | ⚠️ PARTIALLY VALID |
| 10 | `build_read_response` callers | Spec claims only `tools.py::kms_read` calls `build_read_response`. | Confirmed. Only caller: `tools.py:59` calls `ctx.request_context.lifespan_context["engine"].build_read_response(...)`. The method is defined at `context.py:294`. When `kms_read` is removed (P9-A-05), `build_read_response` can be safely deleted. | ✅ VALIDATED |
| 11 | `scripts/start.sh` content | Spec asks to confirm `start.sh` invokes `cloud_entry.py` via `__main__`. | Confirmed. `start.sh` line 25: `python -m mcp_server.cloud_entry &`. This invokes the module as `__main__`, which requires a `if __name__ == "__main__":` block in `cloud_entry.py`. That block is currently MISSING (docstring at line 6/14-15 references it, but no actual code exists after line 182). P9-E-01 bug fix is confirmed necessary. | ✅ VALIDATED |
| 12 | Classify queue wiring | Spec claims `kms_write` needs access to `app.state.classify_queue`. Asks whether MCP tool handler can access it. | **Access path is NOT straightforward from an MCP tool.** The classify queue lives on `app_ref.state.classify_queue` (Starlette state), set by the composed lifespan at `cloud_entry.py:120`. The REST upload handler accesses it via `request.app.state` (`api.py:45`). MCP tool handlers receive `ctx: Context` with `ctx.request_context.lifespan_context` (FastMCP's per-session context dict), which is different from Starlette's `app.state`. To reach the queue from an MCP tool, either: (A) pass the queue into the FastMCP lifespan context dict so tools can access it via `ctx.request_context.lifespan_context["classify_queue"]`, or (B) have `write_from_chat` accept the queue as a parameter. The composed lifespan already yields into the inner FastMCP lifespan — the queue could be added to whatever dict that inner lifespan yields. | ⚠️ PARTIALLY VALID |
| 13 | `DocumentRow.summary` nullability | Spec asks whether `summary` can be NULL for documents that went through `upsert_from_upload` but not `attach_summary`. | Confirmed: `summary` CAN be NULL. `upsert_from_upload` (documents.py:114) INSERT does NOT set the `summary` column — only `vault_path, title, full_body, original_filename, file_size_bytes, content_hash, blob_ref, mime_type, updated_at`. The `summary` column is set later by `attach_summary` (documents.py:497). Between these two calls, `summary` is NULL. The three-tier resolve `summary` mode must handle NULL (return empty string or "[Summary pending]"). | ✅ VALIDATED |
| 14 | `query_ranked_by_dimension` ORDER BY | Spec claims current function uses 3-key ORDER BY: trust_score DESC, confidence DESC, updated_at DESC. Phase 9 needs 4-key with retrieval_score added. | Confirmed at `knowledge_entries.py:224`: `ORDER BY trust_score DESC, confidence DESC, updated_at DESC LIMIT ?`. Exactly 3 keys as claimed. Phase 9 needs to add `retrieval_count DESC` (the SQL column name) as 2nd key: `ORDER BY trust_score DESC, retrieval_count DESC, confidence DESC, updated_at DESC`. Either modify the existing function or add `query_ranked_for_orientation`. | ✅ VALIDATED |

## Summary

**9 validated / 1 invalidated / 3 partially valid / 1 unverifiable**

- 9 assumptions fully match what the code does.
- 1 assumption is wrong about a function signature (audit.write takes AIDecision, not raw fields).
- 3 assumptions are partially correct but miss important details (FTS5 external content delete syntax differs from regular FTS5, reranker also builds cards that need `id`, classify queue not directly accessible from MCP tool context).
- 1 assumption requires a runtime experiment (short-fact embedding separation).

## Invalidated Assumptions

### A7 — `audit.write` signature is wrong

**Spec claimed:** `audit.write(decision, source_ids, pipeline, stage, ...)` — implying `source_ids` is a separate parameter alongside a plain `decision` string.

**Code shows:** `core/audit.py:11` — `write(decision: AIDecision, *, pipeline: str, stage: str, outcome: str, db_path=None)`. The first argument is an `AIDecision` Pydantic model object containing `action`, `confidence`, `reasoning`, and `source_ids` as fields. There is no separate `source_ids` parameter. Additionally, there is a required `outcome: str` keyword argument that the spec does not mention.

**Why this matters:** P9-A-02 (`kms_correct`) plans to call `audit.write(...)` for every correction operation. If the call site uses the spec's claimed signature (raw fields), it will fail at runtime. The correct pattern requires constructing an `AIDecision` object first.

**Suggested resolution directions:**
1. Update the spec's P9-A-02 data flow to show `AIDecision` construction before calling `audit.write`. The correct call pattern is:
   ```python
   decision = AIDecision(
       action=f"correct:{operation}",
       confidence=1.0,
       reasoning=reason or f"Consumer AI requested {operation}",
       source_ids=[str(entry_id)],
   )
   audit.write(decision, pipeline="correct", stage=operation, outcome="APPLIED")
   ```
2. Alternatively, add a simpler `audit.write_simple(pipeline, stage, outcome, source_ids, reason)` wrapper — but this adds unnecessary API surface when the existing pattern works.

## Partially Valid Assumptions — Details

### A1 — FTS5 external content mode sync differs from existing pattern

The spec correctly proposes external content mode for `facts_fts` (avoiding data duplication), but incorrectly claims the sync code matches the existing `notes_fts` pattern. The existing `notes_fts` is a regular FTS5 table that uses plain `DELETE FROM` / `INSERT INTO`. External content FTS5 requires the special command form:

- **Delete:** `INSERT INTO facts_fts(facts_fts, entry_id, entity, fact) VALUES('delete', old_id, old_entity, old_fact)`
- **Insert:** `INSERT INTO facts_fts(rowid, entry_id, entity, fact) VALUES(new_id, new_id, new_entity, new_fact)`

The P9-B-02 implementation must use this form. The alternative is to drop external content mode and use a regular FTS5 table (same as `notes_fts`), which simplifies the sync code but duplicates data.

### A9 — Two paths build SearchResult, not one

The spec identifies `_card_from_row` in `search.py` as the card builder, but the reranker (`reranker.py:163-170`) also constructs `SearchResult` directly inside `rerank()`. Both paths must add `id=row.id` when P9-B-04 adds the field. Missing the reranker path would mean search results from the reranked path have `id=None`.

### A12 — Classify queue not directly accessible from MCP tools

MCP tool handlers access per-session context via `ctx.request_context.lifespan_context` (a dict yielded by FastMCP's inner lifespan). The classify queue lives on `app.state` (Starlette state), which is a different object. The composed lifespan in `cloud_entry.py` sets `app_ref.state.classify_queue` but does not inject it into the FastMCP lifespan context dict. Resolution: inject the queue into the dict that the inner lifespan yields, or pass it as a constructor parameter to the backing function.

## Impact Assessment

No invalidations affect the build order (E -> B -> D -> C -> A -> F -> G). All three partially-valid assumptions are implementation-detail corrections, not architectural changes:

1. **A1 (FTS5 syntax):** Affects P9-B-02 implementation only. No design change needed — just use the correct external content commands.
2. **A7 (audit.write):** Affects P9-A-02 implementation only. Construct `AIDecision` instead of passing raw fields. No new dependencies.
3. **A9 (two card builders):** Affects P9-B-04 scope — update both `_card_from_row` in `search.py` AND the inline `SearchResult(...)` in `reranker.py:163`.
4. **A12 (queue access):** Affects P9-A-01 wiring. The composed lifespan must inject the queue into the FastMCP context dict, or `write_from_chat` must accept the queue as a parameter.

None of these require revisiting the spec's component boundaries or dependency graph. They are mechanical corrections for the plan phase.

## Technical Debt Spotted

- `reranker.py:160` has `"tags": row.key_topics` — the `tags` key duplicates `key_topics` in the metadata dict. Likely a copy-paste error that should be `row.tags` or removed.
- `embeddings.py:79-88` has a retry block that catches `OperationalError` and re-executes the same code — fragile pattern that could be replaced with a single transaction with retry.
