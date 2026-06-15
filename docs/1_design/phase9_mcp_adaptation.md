# Phase 9 — MCP Adaptation: Design

_Created: 2026-06-15_
_Status: DESIGN — produced by build-pipeline design step_
_Input: P9 grill v3 (docs/0_draft/P9_mcp_adaptation_grill.md)_
_Audience: Next AI session running spec step_

## Summary

Phase 9 rewrites the MCP server so the consuming AI talks to a cloud database of distilled knowledge facts instead of reading files from a user's laptop. The five tools change from "search files, read files, move files" to "discover what the system knows, search across both facts and documents, drill into specific items, save new insights, and correct existing knowledge." This is the bridge between the extraction engine (Phase 8) and the user-facing AI experience.

## Glossary

| Term | Plain English | Technical detail |
|---|---|---|
| **query facts** | Facts that match what the user asked about. | `knowledge_entries` rows whose text is returned by hybrid search (semantic + keyword) against the query string. Role is assigned at retrieval time — the same fact is a "query fact" in one search and an "orientation fact" in another. |
| **orientation facts** | Background facts about the entities in scope, regardless of the query. | Per-dimension top-N `knowledge_entries` rows ranked by trust then retrieval_score then confidence then recency, injected to orient the AI before it sees query results. |
| **retrieval_score** | A decaying popularity score on each fact — how often it has been surfaced recently. | On each injection: `retrieval_score = retrieval_score * decay_factor + 1.0`. A periodic sweep multiplies all scores by `decay_factor` (config-driven). Prevents rich-get-richer lock-in. Reinterprets the existing `retrieval_count` INT column as a REAL (SQLite is type-flexible). |
| **fact hybrid index** | A searchable index over the short fact texts in `knowledge_entries`. | Two new search tables: an FTS5 keyword index (`facts_fts`) and a vec0 embedding index (`facts_vec`) on the `fact` column. The same RRF fusion technique as the document search, but on fact text. Research gate: short one-liner facts may not separate well in embedding space — config-driven weight between keyword and semantic. |
| **identity-dedup** | Show each item only once, by its database identity (row id). | Same fact row or same document row appearing in multiple result lists is collapsed to one occurrence. Distinct from content dedup: a fact and a document summary that describe the same real-world event are both kept (content overlap is holistic value). |
| **dual-corpus search** | Searching both knowledge facts and document summaries independently, then merging. | `kms_search` runs two parallel queries — one against `knowledge_entries` (fact hybrid index), one against `documents` (existing `notes_fts` + `embeddings_vec`). Results are merged and identity-deduped. The document search is the recall safety-net for content captured but not yet classified. |
| **three-tier resolve** | Three levels of detail a consumer can request for any document. | `summary` = the structured 5-section digest from DB (`documents.summary`). `text` = the full raw extracted text from DB (`documents.full_body`). `file` = the vault file path (laptop-dependent). Replaces the old vault-disk binary resolver. |
| **budget-capped injection** | A cap on how many orientation facts are injected into a response, so context windows are not flooded. | Per-dimension cap on orientation facts (`max_orientation_facts_per_dimension`) and per-dimension cap on entity names in the structural map (`max_entities_per_dimension`), both config-driven. No global token budget in Phase 9 — two config knobs suffice. |

## Current State

The MCP server today (`src/mcp_server/`, 9 files, ~1931 lines) provides five tools:

- **`kms_vault_info`** — Returns a vault structural overview (domains, projects, inbox count) built from the on-disk `ProjectRegistry` plus the vault-root `CLAUDE.md` file content.
- **`kms_search`** — Runs hybrid search on `documents` (FTS + embeddings), injects CLAUDE.md/context.yaml files from disk when results are concentrated in one domain.
- **`kms_read`** — Reads full note bodies from vault disk via `read_note()`.
- **`kms_inspect`** — Re-extracts raw text from a binary file through the handler registry (disk-dependent).
- **`kms_move`** — Moves a note to a project/domain folder on disk, updates frontmatter and index.

The **context injection engine** (`context.py`, ~700 lines) reads CLAUDE.md and context.yaml files from vault disk, gates injection on domain concentration in search results, and deduplicates by content hash per conversation.

The **binary resolver** (`_resolve.py`, ~60 lines) reads sibling `.md` frontmatter from disk to find a binary's path, then dispatches through the handler registry for raw text extraction.

Key limitation: **everything reads from vault disk**, which does not exist in the cloud container. The `knowledge_entries` table (shipped in Phase 8) and `documents.summary`/`full_body` columns (shipped in Phase 7) are populated but not surfaced through the MCP tools.

## Target State

After Phase 9, the MCP server provides **five tools** with fundamentally different backing:

- **`kms_vault_info`** — Structural overview from `knowledge_entries` (entity names grouped by dimension, per-dimension orientation fact bullets). Zero disk reads. Zero CLAUDE.md.
- **`kms_search`** — Dual-corpus search: query facts from `knowledge_entries` (new fact hybrid index) + document summaries from `documents` (existing search infrastructure). Merged, identity-deduped, orientation facts prepended.
- **`kms_inspect`** — Batched references by integer doc id. Three modes: summary (DB), text (DB), file (vault path). Default summary. Text opt-in + capped.
- **`kms_write`** — New tool. Sends chat content through the capture pipeline (cloud summarize + classify). Source type = `chat_session`.
- **`kms_correct`** — New tool. Patches an existing `knowledge_entries` row by id. Operations: edit fact, change tag, change entity, promote, retire. Audit logged.
- **Removed:** `kms_read` (folded into search + inspect), `kms_move` (system never moves files).

The **context injection engine** is rewritten: fact bullets from `knowledge_entries`, ranked by a 4-key sort (trust_score, retrieval_score, confidence, updated_at), budget-capped per dimension. Conversation-level dedup on fact identity (row id), not content hashes. Zero CLAUDE.md reads.

The **resolver** becomes a 3-tier DB-first lookup. `get_by_id` added to `documents.py`.

## Component Analysis

### Cluster A — Tool Surface Changes

**What exists today:**
- `tools.py` — five tool shims registered via `register_tools()`. Each is one expression delegating to the engine or a helper module.
- `_move.py` (~104 lines) — backing for `kms_move`, calls `vault/writer.py` + `vault/move_guard.py`.
- `AI_INSTRUCTIONS.md` — consumer manual describing the 5-tool workflow (discover → search → read → inspect → move).

**What changes:**
- Delete `_move.py` entirely. Remove `kms_move` from `tools.py` and its registration.
- Remove `kms_read` from `tools.py` and its registration.
- Add `kms_write` shim — delegates to capture pipeline with `source_type=chat_session`. Consumer may pass a title hint.
- Add `kms_correct` shim — validates entry id, delegates to `knowledge_entries.upsert` or `retire`, writes to `audit_log`.
- Rewrite `kms_vault_info` shim to call the new engine API (fact-based, not registry-based).
- Rewrite `kms_search` shim to call the dual-corpus search + fact assembly.
- Rewrite `kms_inspect` shim to accept batched integer ids + mode enum (summary/text/file).
- Rewrite `AI_INSTRUCTIONS.md` for 5-tool surface, facts-vs-summary model, correct-vs-write routing, behavioral stance (write = proactive, correct = confirm-first).

**Key risks:**
- C-14 (tools.py logic-free) — all branching must live in backing modules, not in shims.
- C-15 (no tool before pipeline) — `kms_write` and `kms_correct` shims land only after their backing pipelines are tested.
- C-13 (audit) — `kms_correct` must audit every mutation.

**Constraints:** C-14, C-15, C-13, C-12 (Result returns).

### Cluster B — Retrieval Foundation

**What exists today:**
- `knowledge_entries.py` — CRUD with `upsert`, `retire`, `query_by_dimension`, `query_by_entity`, `query_ranked_by_dimension`, `get_confident_and_pending`, `prune_sources`. No search index on fact text.
- `documents.py` — `get_by_path`, `upsert_from_upload`, `attach_summary`, `filter_paths`, `all_paths`, `delete_by_path`, `rename`, `replace_path`, `update_batch_id`. **No `get_by_id`.**
- `retrieval/search.py` — Search coordinator wiring filter → rank → rerank on `documents` only.
- `retrieval/ranker.py` — Hybrid BM25+KNN ranker on `notes_fts` + `embeddings_vec`. RRF fusion.
- `retrieval/reranker.py` — Cross-encoder reranker on `documents`.
- Search result cards expose `vault_path` but not `id`.

**What changes:**
1. **Fact hybrid index** (new migration 012) — two new tables: `facts_fts` (FTS5 on `knowledge_entries.fact`) and `facts_vec` (vec0 on embedded fact text). Populated on entry insert/update. Research gate: test short-fact embedding separation before locking ranking weights.
2. **`get_by_id`** added to `documents.py` — simple `SELECT * FROM documents WHERE id = ?` returning `Result[DocumentRow | None]`.
3. **Expose `id`** on search result cards — add `id` field to `SearchResult` dataclass in `reranker.py`, populate in `_card_from_row` in `search.py`.
4. **`retrieval_score` decay** — On each fact injection: `UPDATE knowledge_entries SET retrieval_score = retrieval_score * decay_factor + 1.0 WHERE id = ?`. Periodic sweep: `UPDATE knowledge_entries SET retrieval_score = retrieval_score * decay_factor`. Config: `mcp.retrieval_score.decay_factor` (default ~0.95) and `mcp.retrieval_score.sweep_interval` (daily/weekly).
5. **Ranker update** — The 4-key `ORDER BY` (trust_score DESC, retrieval_score DESC, confidence DESC, updated_at DESC) is used for orientation-fact ranking. The existing document ranker (RRF) is unchanged for document search.
6. **Dual-corpus search merge + identity-dedup** — New coordinator function that runs fact search + document search independently, merges into one result set, deduplicates by row identity (fact id or doc id), and orders by relevance.

**Key risks:**
- L6 (research gate): short one-liner facts may cluster in embedding space. If separation is poor, keyword matching carries the weight. Config-driven blend ratio.
- L13 (migration cascade): new migration 012 bumps prior version-pin tests (011 assertions).
- The dual-corpus merge is new logic that does not exist anywhere in the codebase. Needs careful testing of the identity-dedup boundary.

**Constraints:** C-05 (migration only), C-06 (thresholds in config), C-04 (FK pragma).

### Cluster C — Context Injection Engine

**What exists today:**
- `context.py` (~700 lines) — `ContextInjectionEngine` with `build_search_response`, `build_vault_info_response`, `build_read_response`, plus helpers for concentration gating, CLAUDE.md disk reads, hash-based dedup, inbox stats from DB.

**What changes:**
- **Complete rewrite.** The engine no longer reads any vault disk files.
- `build_vault_info_response` → queries `knowledge_entries` for all non-retired entities grouped by dimension, ranks them, caps per dimension (config: `max_entities_per_dimension`). Produces orientation fact bullets per dimension (config: `max_orientation_facts_per_dimension`). No CLAUDE.md.
- `build_search_response` → calls dual-corpus search, prepends orientation facts for entities mentioned in results, applies conversation-level identity dedup.
- `build_read_response` → removed (replaced by `kms_inspect`).
- Concentration gating (domain share threshold) → **dropped**. Facts are always injected; their amount is controlled by budget caps, not domain frequency.
- Conversation-level dedup → kept but changed from content-hash-keyed (CLAUDE.md file hashes) to identity-keyed (fact row ids and doc ids). `_dedup_memory` tracks `set[int]` of injected fact ids and doc ids.
- All CONFIG.main.vault references removed. All `ProjectRegistry` references removed.

**Key risks:**
- The rewrite touches every method in the class. No incremental migration possible — it is a clean replacement.
- The old engine API (`build_search_response`, `build_vault_info_response`) is called by `tools.py` — both must change together.
- `server.py` lifespan creates the engine. The constructor and its dependencies change.

**Constraints:** C-06 (budget caps in config), C-17 (lazy CONFIG import in tests).

### Cluster D — Three-Tier Resolve

**What exists today:**
- `_resolve.py` (~60 lines) — `inspect(path: Path)` reads sibling `.md` frontmatter from disk, resolves to binary path, dispatches through `HandlerRegistry` for raw text extraction. Entirely disk-dependent.

**What changes:**
- **Complete rewrite.** `inspect` becomes `resolve(doc_ids: list[int], mode: str)`.
- Three modes:
  - `summary` (default): reads `documents.summary` from DB. Always available.
  - `text`: reads `documents.full_body` from DB. If `full_body` is NULL, degrades to returning the summary with a note that full text is unavailable.
  - `file`: returns `documents.vault_path`. The consuming AI handles laptop availability.
- Canonical reference = integer `documents.id` (rename-stable).
- Depends on new `get_by_id` from Cluster B.
- Batched: accepts a list of ids, returns results for all. Cap on how many ids may request `text` mode in one call (config-driven).

**Key risks:**
- L14: Binary docs have `summary == full_body` (vision description in both). `kms_inspect text` on a binary returns the description, not raw bytes. This is correct behavior but may confuse the consumer AI — `AI_INSTRUCTIONS.md` must document it.
- NULL `full_body` degradation path must be tested.

**Constraints:** C-12 (Result returns), C-14 (logic stays out of tools.py).

### Cluster E — Bug Fixes

**What exists today (bugs):**
- L2: `cloud_entry.py` lost `if __name__ == "__main__": uvicorn.run(...)` in commit `1b1f33d`. Container boots nothing via `scripts/start.sh`.
- C1: `api.py:62` re-reads `KMS_DAEMON_API_KEY` from `os.environ` on every request.
- C2: `capture.py:342` calls sync `blob_store.put()` from async handler. Blocks event loop.
- C3: `api.py:330` `_delete_with_blob_cleanup` is sync, called from async handler.

**What changes:**
- L2: Restore `if __name__ == "__main__": uvicorn.run(build_app(), host="0.0.0.0", port=8080)` to `cloud_entry.py`. Add boot test.
- C1: Read `KMS_DAEMON_API_KEY` once at module scope or in `build_app()`, store in a closure or module variable.
- C2: Switch `blob_store.put()` to `blob_store.async_put()` (method already exists at `blobs.py:316`).
- C3: Wrap `_delete_with_blob_cleanup` call in `asyncio.to_thread()`, or make the function fully async.

**Key risks:** Minimal — these are localized fixes.

**Constraints:** C-10 (asyncio.run pattern for async fixes).

### Cluster F — Phase 10 Seam

**What exists today:**
- `classify_writer.py` (~304 lines) — entry writing with `write_entries()`, DRY helpers (`_merge_sources`, `_compute_status`, `_find_twin`). Overwrites unconditionally.

**What changes:**
- Add an explicit `_should_overwrite(existing_entry: KnowledgeEntry) -> bool` decision point (or equivalent) in the update path of `write_entries`. In Phase 9 it always returns `True` (current behavior preserved). Phase 10 slots the trust guard here: `trust_score > 0.5 → False` (write conflicting new entry instead).
- The seam is a pure structural addition — no behavioral change in Phase 9.

**Key risks:** Minimal — additive only.

**Constraints:** None directly, but the seam must be clearly documented for Phase 10.

### Cluster G — Documentation

**What exists today:**
- `AI_INSTRUCTIONS.md` — current consumer manual (covered in Cluster A).
- No deployment guide for AgentBase.

**What changes:**
- `AI_INSTRUCTIONS.md` rewrite — covered in Cluster A.
- **AgentBase deployment guide** — 2-part non-technical document:
  - **Builder part:** stand up deployment, drop in IAM/API/daemon keys, configure gateway auth, verify container health.
  - **Tester part:** connect Claude Desktop to gateway endpoint, run daemon, verify tools work.
- Single-tenant per deployment (each tester: own container + DB + vault + daemon key + gateway endpoint).

**Key risks:** Deployment guide accuracy depends on AgentBase platform specifics that may need input from the platform team.

**Constraints:** None code-level. Guide must not embed secrets.

## Build Order Options

### Option A — Bottom-Up (RECOMMENDED)

Foundation layers first, consumer layers last. Each cluster's output is the input for the next.

**Sequence:** E (bug fixes) → B (retrieval foundation) → D (3-tier resolve) → C (context engine) → A (tools + docs) → F (P10 seam) → G (deployment guide)

**Tradeoffs:**
- (+) Each cluster can be fully tested in isolation before the next depends on it.
- (+) Bug fixes first means the container actually boots — essential for any manual verification.
- (+) The retrieval foundation (fact index, get_by_id, dual-corpus search) is the layer everything else depends on — building it first de-risks the hardest new work.
- (+) F (seam) is trivial and slots in cleanly after A, when classify_writer's test context is fresh.
- (-) No tool is usable until Cluster A completes. Demo-ability comes late.
- (-) Longer time to first visible output.

**Test strategy per cluster:**
- **E:** Unit tests for each bug fix. Boot test for L2 (import `cloud_entry`, verify `__main__` block exists). Async test for C2/C3.
- **B:** Migration test (schema version bump, table creation). Unit tests for `get_by_id`, fact FTS + fact embedding queries, retrieval_score increment/decay, dual-corpus merge + identity-dedup. Research spike: embed 50+ real short facts, measure separation, adjust blend weight.
- **D:** Unit tests for each resolve mode (summary, text, file). NULL full_body degradation test. Batched resolve test.
- **C:** Unit tests for new engine methods. Orientation fact assembly test (ranking correctness). Conversation dedup by identity test. Integration test: engine → dual-corpus search → fact assembly → response blocks.
- **A:** Tool shim tests (each shim delegates correctly). `kms_correct` audit log test. `kms_write` capture pipeline invocation test. AI_INSTRUCTIONS.md review (manual).
- **F:** Unit test: `_should_overwrite` always returns True. Structural test: the decision point exists and is called in the update path.
- **G:** Manual verification against a running deployment.

### Option B — Feature-Slice (vertical per tool)

Each tool built end-to-end from storage through to shim.

**Sequence:** Fix bugs → vault_info (engine + tool) → search (index + engine + tool) → inspect (resolve + tool) → write (pipeline + tool) → correct (pipeline + tool) → seam → docs.

**Tradeoffs:**
- (+) Each tool is demo-able as soon as its slice completes.
- (-) Shared infrastructure (fact index, get_by_id, dual-corpus merge) is built piecemeal, discovered mid-slice, leading to rework.
- (-) The context engine rewrite spans multiple tool slices — partial rewrites create an inconsistent intermediate state.
- (-) Harder to test in isolation: each slice touches storage, retrieval, engine, and tool layers simultaneously.

**Dismissed:** The shared retrieval foundation (Cluster B) is too deeply cross-cutting. Building it piecemeal per tool duplicates effort and creates integration risk.

### Option C — Risk-First

Build the highest-risk item first (research spike on fact embedding separation), then proceed bottom-up.

**Sequence:** Research spike (fact embedding separation) → E → B (minus research, already done) → D → C → A → F → G

**Tradeoffs:**
- (+) Resolves the single biggest unknown (L6) before committing to the fact index design.
- (-) The research spike has a clear fallback (lean on keyword matching) — the risk is manageable without front-loading it.
- (-) Adds a phase boundary for a question that can be answered during Cluster B implementation.

**Recommendation:** Fold the research spike INTO Cluster B rather than separating it. Option A with an embedded research gate is the cleanest path.

## Migration Plan

**One new migration file: `012_fact_search_index.sql`**

```
-- Migration 012: Fact search index for Phase 9

-- FTS5 keyword index on knowledge_entries.fact
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    entry_id UNINDEXED,
    entity,
    fact,
    content='knowledge_entries',
    content_rowid='id'
);

-- vec0 embedding index on knowledge_entries.fact
CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(
    entry_id INTEGER PRIMARY KEY,
    embedding FLOAT[384]
);

-- Bump schema version
UPDATE schema_version SET version = 12;
```

**Notes:**
- `facts_fts` uses FTS5 external content mode (`content='knowledge_entries'`) to avoid data duplication. Triggers or manual sync needed on insert/update/delete.
- `facts_vec` dimension (384) matches `all-MiniLM-L6-v2` output. Same model as document embeddings.
- `retrieval_count` column is NOT renamed — SQLite type flexibility means the existing INT column works as REAL. Phase 9 code treats it as REAL.
- `KnowledgeEntry` dataclass: `retrieval_count: int = 0` renamed/retyped to `retrieval_score: float = 0.0` in Python only.
- Existing version-pin test in `test_migration_011.py` must be bumped from 11 → 12.

**No `get_by_id` migration needed** — the column `documents.id` already exists as INTEGER PRIMARY KEY. The new function is Python-only.

## Success Criteria

These are observable outcomes a non-technical user would notice:

1. **The AI knows what you know.** When a user connects Claude Desktop to their knowledge system, the AI can immediately describe what projects, people, and topics exist — without being told.

2. **Search finds facts, not just files.** Asking "what does Anthony work on?" returns direct factual answers extracted from documents, not just links to documents that mention Anthony.

3. **Fresh content surfaces immediately.** A document uploaded 30 seconds ago appears in search results even before the classify pipeline has finished extracting facts from it — because document summaries are searchable instantly.

4. **The AI can save what it learns.** When a conversation produces a valuable insight, the AI stores it as a new document in the knowledge system — no copy-pasting or manual filing.

5. **The AI can fix what it got wrong.** If a fact is outdated or incorrect, the AI can correct it on the spot — and the correction is logged for traceability.

6. **Frequently-asked topics stay prominent.** Facts that are retrieved often rank higher, but this advantage fades over time so stale-but-once-popular facts do not dominate.

7. **The container boots and runs.** The deployment guide lets a non-technical person stand up a working instance and connect Claude Desktop to it.

## Open Questions

These are genuinely unresolved and should be addressed during the spec or research steps.

1. **FTS5 external content sync strategy.** The `facts_fts` table uses external content mode pointing at `knowledge_entries`. Inserts and updates to `knowledge_entries` must be reflected in `facts_fts`. Should this be done via SQLite triggers (automatic but fragile across migrations) or explicit Python-side sync calls in `knowledge_entries.upsert` and `retire` (manual but explicit)? Recommendation: Python-side sync (matches existing pattern in `documents.py` where search cleanup is inside the same transaction).

2. **Fact embedding timing.** When should fact text be embedded into `facts_vec`? Options: (a) synchronously during `knowledge_entries.upsert` — simple but adds latency to classify, (b) asynchronously via the same queue pattern as document indexing — decouples but adds complexity. The document embedding path in `retrieval/embeddings.py` is synchronous today.

3. **`kms_correct` un-retire.** The grill noted no explicit retired→confident operation. Should Phase 9 add an "un-retire" operation to `kms_correct`? The workaround (create a new entry via `kms_write`) works but is awkward. Low cost to add. Spec should decide.

4. **`kms_write` pipeline invocation path.** The cloud capture pipeline currently runs via `/api/upload` (HTTP) or direct function call. Which path does `kms_write` use? If the MCP tool is in the same process as the capture pipeline (stdio mode), it can call directly. In cloud mode, it should use the internal function call (not HTTP loopback). Spec should define the wiring.

5. **Retrieval_score sweep scheduling.** The periodic decay sweep needs a trigger. Options: (a) background task in the composed lifespan (like the classify worker), (b) lazy sweep on first request of the day, (c) cron job. Spec should choose.

6. **`kms_inspect` text mode cap.** How many refs may request `text` mode in one call? Needs a sensible default. Spec should define (suggested: 3-5).

## Out of Scope

These items are explicitly deferred to Phase 10 (or later):

- **Trust_score movement** — `adjust_trust()` pure function, promote/retire/edit deltas. Phase 9 leaves trust_score at its default (0.5 for all entries).
- **Classify overwrite guard** — The `_should_overwrite()` seam is added in Phase 9 but always returns True. Phase 10 wires the `trust_score > 0.5` guard.
- **Pending requests system** — `kms_pending_requests`, `kms_resolve_request`, pending_requests table, housekeeping creation logic. Entire system deferred.
- **Corrections table reshape** — Exists but inert and wrong-shaped. Phase 10 reshapes via migration.
- **Few-shot injector** — Loading recent corrections into extraction prompts.
- **`min_trust` filtering** — Config knob exists but does not filter until trust_scores diverge from 0.5.
- **Volatility flag** — Entries with >3 corrections flagged in context blocks.
- **Web UI** — Conflict queue, comment feature, parked document dashboard.
- **Multi-tenancy** — One instance per user. Separate deployments for separate testers.
- **Content-level document dedup** — Near-duplicate `kms_write` calls create separate documents. Fact-level dedup catches duplicate facts.
- **`retrieval_count` column rename** — Python code reinterprets the column as `retrieval_score`; the SQL column name stays `retrieval_count` to avoid a migration for a rename.
