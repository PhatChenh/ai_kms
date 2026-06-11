# Phase 3 search uses RRF + cross-encoder re-rank with a metadata pre-filter, not a max_cost tier dispatcher

Phase 3 builds vault search as a metadata pre-filter → hybrid ranker (Reciprocal Rank Fusion of FTS5 BM25 + sqlite-vec KNN) → cross-encoder re-ranker → capped result cards, and explicitly does NOT build the roadmap's "three-tier retrieval / max_cost budget / hot-warm-cold escalation" narrative. The "three tiers" promise is realized instead as a cheap triage-card payload plus lazy full-content fetch. We choose this because the roadmap's cost-dispatcher concept was never grounded in code and the grill (2026-06-10) locked the RRF+rerank design as the buildable contract for the Phase 4 MCP consumer.

**Status:** proposed

## Context

- The roadmap's "Phase 3 — Retrieval Infrastructure" narrative (TIER DISPATCHER, `max_cost`, hot/warm/cold escalation) is stale: it predates the Session A index layer and references config keys and a dispatcher that were never built. Only the roadmap's **Stable Interfaces** table is treated as living.
- Session A (merged 2026-06-10, 1147 tests) shipped the index layer: `embeddings_vec` (sqlite-vec `vec0`, `FLOAT[384]`) and `notes_fts` (FTS5, columns `vault_path, title, summary, body`), populated best-effort at capture time.
- The **primary consumer** of `search()` is the Phase 4 MCP AI, not a human. The AI needs a cheap structured payload to triage on, then pulls full content via `read_note` only for relevant notes. This favours a lightweight card + lazy fetch over a cost-budget dispatcher.

## Decision

1. `search(query?, project?, date_range?, max_results?)` runs: metadata pre-filter on `documents` → candidate `vault_path`s; if no query, filter-only mode (sort by `updated_at` desc, cap, return); if query, Hybrid Ranker (RRF over FTS5 BM25 + sqlite-vec KNN, both scoped to candidates) → cross-encoder re-ranker → capped `SearchResult[]`.
2. No tier dispatcher, no `max_cost`, no hot/warm/cold. The "three-tier" idea is delivered as: a cheap `SearchResult(vault_path, summary, snippet, score, metadata)` card + lazy `read_note(vault_path)` for full content.
3. All ranking inference is in-process `sentence-transformers` (embeddings) and its bundled `CrossEncoder` (re-rank). Search never calls Ollama or any chat provider, and writes no audit entry (it makes no AI decision).

### KNN-scoping sub-decision (load-bearing)

The meaning search is scoped to the filtered candidates **in-database** via `embedding MATCH ? AND k = ? AND vault_path IN (<candidates>)`. This was verified against the installed **sqlite-vec v0.1.9**: the `IN (...)` clause acts as a partition/pre-filter applied *before* selecting the k nearest, and the query returns real distances restricted to the candidate set. The alternative form the source draft assumed — `WHERE vault_path IN (...) ORDER BY distance` with no `MATCH` — executes but returns `NULL` distances (no KNN happens) and was rejected as a silent correctness trap.

## Considered Options

- **Global KNN then filter in Python** — can return fewer than the wanted results after filtering (forces an unbounded raise-k retry loop) and does work the database can do natively. Rejected.
- **Store vectors in a plain column and compute cosine in Python** — discards the optimised vector index and is slow/memory-heavy for large candidate sets. Rejected (its only upside, no dependency on a vec0 capability, is moot now the capability is proven).
- **Roadmap tier dispatcher / `max_cost` budget** — never grounded in code; superseded by the cheap-card + lazy-fetch model. Rejected per grill.
- **Draft `IN (...) ORDER BY distance` (no MATCH)** — returns NULL distances, no semantic ranking. Rejected on empirical probe.

## Consequences

- **Dependency bet:** the meaning search depends on sqlite-vec honouring `MATCH + k + IN (...)`. Pin `sqlite-vec` and add a test that fails if a future upgrade changes filtered-KNN semantics (assert a far-but-in-set candidate is returned and a near-but-out-of-set is excluded). Hard to reverse cheaply once callers depend on the contract.
- **No cost ceiling:** if a real per-query cost budget is ever needed, it is a new feature, not a parameter tweak.
- **Roadmap divergence is now formal:** future readers comparing the roadmap's tier narrative to the code should read this ADR. The roadmap Phase 3 narrative should be annotated as superseded by this ADR (the Stable Interfaces table remains authoritative).
- **Query-side embedding asymmetry** (stored vectors built from a composite context string; queries are bare text) and **CPU-bound sync search inside the async CLI** are tracked as open questions OQ-P3B-2 / OQ-P3B-1 in `docs/1_design/P3_session_b_query_path.md`.
- **Re-ranker cost:** question-mode search runs a cross-encoder over the top `max_candidates`; filter-only mode skips it. Accepted for relevance quality, bounded by config.
