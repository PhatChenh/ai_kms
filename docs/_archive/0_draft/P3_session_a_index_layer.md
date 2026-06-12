# Phase 3 Session A — Index Layer + Pre-flight Fixes
_Status: DONE_
## Context

Phase 3 (Retrieval Infrastructure) is split into two sessions. This is Session A. Session B spec will be written after Session A ships.

Full Phase 3 spec lives in `docs/roadmap/roadmap.md` under "## Phase 3 — Retrieval Infrastructure" (components: EMBEDDING INDEXER, KEYWORD INDEXER, HYBRID RANKER, RERANKER, SEARCH FUNCTION, SEARCH CLI). Session A builds only the index layer (first two components). Session B builds the query path (last four).

Current test count: 1080 passing.

## Grill decisions (2026-06-10)

These decisions were validated in a grill interview and are locked for this session:

1. **Scope:** All 7 pre-flight fixes + 3 build targets ship as one deliverable.
2. **Embedding model loading:** Lazy load `SentenceTransformer` on first call, cache at module level. No pre-load at startup — avoids penalizing CLI commands that never touch indexing.
3. **sqlite-vec extension:** Load on every `_connect()` call (~1ms). No split between "with vectors" and "without vectors" connections.
4. **No-summary notes:** Embedding indexer uses metadata-only contextual string (e.g. `"[Project: Alpha | Type: meeting-notes | Date: 2026-06-10]"`) when summary is empty. Partial visibility beats total invisibility.
5. **Note update/move/delete:** Check if existing `documents.py` mechanisms (`delete_by_path`, `rename`, `replace_path`) can piggyback index cleanup. No new hooks unless existing ones are insufficient.
6. **Sibling .md notes:** Frontmatter summary → embedding indexer (consistent with standard .md notes). Body text (detailed expanded summary) → FTS5 keyword indexer. Binary never indexed; sibling is proxy. Both summaries describe the binary's content.
7. **TD-019 (validate_tags in classify):** Already resolved in `capture.py` line 240. `classify()` stays pure. No additional work needed — spec claim was stale.
8. **Indexer failure handling:** Retry once silently. If retry fails, emit warning to terminal log (for developer debugging), no user-facing error. Capture pipeline succeeds regardless.
9. **TD-050 fix approach:** Test-only autouse fixture in `tests/test_vault/conftest.py`. No production code change — the bug is test isolation, not production behavior.
10. **Migration 007:** One file for both `embeddings_vec` and `notes_fts`. Fail fast if sqlite-vec can't load — no degraded keyword-only mode.
11. **No `--reindex` in Session A.** Idempotent upsert self-heals individual entries. Batch reindex deferred to Session B.
12. **SearchConfig fields:** Add all four fields now (`embedding_model`, `reranker_model`, `max_candidates`, `max_results`) even though Session A only uses `embedding_model`. Zero runtime cost, saves Session B from touching config schema again.

---

## Pre-flight fixes (build these before any Phase 3 retrieval code)

These are pre-existing gaps that block or corrupt Phase 3 if left unresolved.

### I-1 · Migration number conflict

Roadmap says `006_add_search_indexes.sql` but `006_batches_folder_path.sql` already exists in `src/storage/migrations/`. Use `007_add_search_indexes.sql` instead. Update any roadmap or spec references that say "006" for search indexes.

### I-2 · Embedding architecture mismatch

Config has `embedding_model: voyage-3` (API-based, per-provider field) but `docs/roadmap/roadmap.md` and `CLAUDE.md` tech stack both specify `sentence-transformers all-MiniLM-L6-v2` (local offline Python library). These are fundamentally different: one is an API call, the other is a local `SentenceTransformer.encode()` call.

Fix: Add a new `search:` section to `src/config/config.yaml` with `embedding_model: all-MiniLM-L6-v2`. The existing per-provider `embedding_model` fields (voyage-3, nomic-embed-text-v1.5, etc.) are kept for future API embedding calls and are NOT touched. The `search:` section is the config namespace for the retrieval layer only.

Also update `src/core/config.py` with a `SearchConfig` Pydantic model containing `embedding_model`, `reranker_model`, `max_candidates`, `max_results`. Add `search: SearchConfig` field to `MainConfig`.

### I-3 · Missing reranker_model config key

`CONFIG.main.providers.reranker_model` referenced in roadmap does not exist. Add `reranker_model: cross-encoder/ms-marco-MiniLM-L-6-v2` to the new `search:` config section (not under `providers:`).

### I-4 · Missing search tuning keys

Add to `search:` config section:
- `max_candidates: 50` — how many candidates HYBRID RANKER returns to RERANKER
- `max_results: 10` — default result count returned to callers

### I-5 · sentence-transformers missing from pyproject.toml

`sentence-transformers` is in `CLAUDE.md` tech stack but not in `pyproject.toml` dependencies. Add it.

### I-6 · sqlite-vec missing from pyproject.toml

`sqlite-vec` Python package (the SQLite extension for KNN vector search) is not in `pyproject.toml`. Add it. Also wire the extension load into `storage/db.py::_connect()` — call `conn.enable_load_extension(True)` then load the sqlite-vec extension. Pattern: `import sqlite_vec; db.load_extension(sqlite_vec.loadable_path())`.

### TD-019 · validate_tags never called in classify.py — ✅ ALREADY RESOLVED

~~`core/tags.py::validate_tags` is wired in `pipelines/capture.py` (line 240) but never called in `pipelines/classify.py` despite Phase 2 being complete.~~

**Status:** Already fixed. `validate_tags()` is called in `capture.py` line 240 (metadata stage), with `TAG_VIOLATION` audit logging at line 291-303. `classify()` stays pure (no writes, no audit) — tag validation happens in the caller (`classify_step`), consistent with the architecture. No work needed.

### TD-050 · Watcher test timer leak (opportunistic)

`tests/test_vault/test_watcher_rehome.py::test_no_edit_pdf_cross_folder_rehome` intermittently fails with `assert len(move_note_calls) == 1` returning `2` when the full suite runs. Root cause: a `threading.Timer` debounce from a different test fires during this test's `time.sleep(0.05)` and hits the module-level `vault.watcher.move_note` monkeypatch.

Fix: Add an autouse fixture to `tests/test_vault/conftest.py` that tracks all `threading.Timer` instances created by `_VaultEventHandler._debounce` and cancels them in teardown. This stops leaked timers from one test firing in another test's scope.

---

## Build targets (Session A proper)

### Migration 007

File: `src/storage/migrations/007_add_search_indexes.sql`

Two virtual tables:
1. `embeddings_vec` — sqlite-vec virtual table: `vault_path TEXT PRIMARY KEY, embedding FLOAT[384]` (384 = all-MiniLM-L6-v2 output dimension)
2. `notes_fts` — FTS5 virtual table: `vault_path UNINDEXED, body, summary` with `content_rowid` pointing to the documents table rowid

Follow the same migration pattern as existing files in `src/storage/migrations/`. The migration runner in `storage/db.py` must load it in order.

### EMBEDDING INDEXER

File: `src/retrieval/embeddings.py`

New module. Function signature:
```python
async def index_note(vault_path: str, summary: str, metadata: dict) -> Result[int]:
```

- Build contextual string: `"[Project: {project} | Type: {note_type} | Date: {date}] {summary}"` — omit brackets for None fields
- **No-summary fallback:** When summary is empty/None, use metadata-only contextual string (e.g. `"[Project: Alpha | Type: meeting-notes | Date: 2026-06-10]"`). Partial visibility beats total invisibility.
- **Sibling .md notes:** Use frontmatter summary (same as standard .md notes). The detailed body text goes to keyword indexer only. Keeps embedding strategy consistent across all note types.
- Load model name from `CONFIG.main.search.embedding_model`
- Call `SentenceTransformer(model_name).encode([contextual_string])` to get the embedding vector
- Upsert into `embeddings_vec` table by `vault_path` (idempotent)
- **Failure handling:** Retry once silently on failure. If retry fails, emit warning to terminal log, do not fail capture pipeline.
- Return `Success(rowcount)` or `Failure` — never raise (C-12)

Model loading: lazy load `SentenceTransformer` on first call, cache at module level (load once, reuse). Do not pre-load at startup — avoids penalizing CLI commands that never touch indexing.

### KEYWORD INDEXER

File: `src/retrieval/keyword.py`

New module. Function signature:
```python
def index_note(vault_path: str, body: str, summary: str) -> Result[int]:
```

- `INSERT OR REPLACE INTO notes_fts(vault_path, body, summary) VALUES (?, ?, ?)`
- **Sibling .md notes:** Body text is the detailed expanded summary of the binary — index it as `body` for keyword coverage. `summary` is the frontmatter summary. Both describe the binary's content. This gives keyword search more terms to match on sibling notes.
- Idempotent by vault_path
- **Failure handling:** Retry once silently on failure. If retry fails, emit warning to terminal log, do not fail capture pipeline.
- Return `Success(rowcount)` or `Failure` — never raise (C-12)

### Wire indexers into capture pipeline

File: `src/pipelines/capture.py`

After the store stage (Stage 5) completes successfully, call both indexers:
- `retrieval.embeddings.index_note(vault_path, summary, metadata)`
- `retrieval.keyword.index_note(vault_path, body, summary)`

Indexer failures: retry once silently, then emit warning to terminal log. Capture pipeline succeeds regardless. Indexing is best-effort; the note is already stored.

### Index maintenance on note update/move/delete

Check whether existing `storage/documents.py` mechanisms (`delete_by_path`, `rename`, `replace_path`) can piggyback index cleanup — the design subagent must verify what hooks are available and whether index rows need to be updated/removed when a note is moved, renamed, or deleted. No new watcher hooks unless existing mechanisms are insufficient. Key cases:
- **Note deleted:** index rows for that `vault_path` should be removed from both `embeddings_vec` and `notes_fts`
- **Note moved/renamed:** `vault_path` in index rows must match the new path
- **Note updated:** re-index with new content (idempotent upsert handles this naturally if called)

---

## Acceptance criteria (Session A)

- [x] `uv sync` succeeds with `sentence-transformers` and `sqlite-vec` installed
- [x] `uv run pytest tests/` passes (1080+ tests, no regressions from pre-flight fixes)
- [x] `kms capture <file>` completes and a row appears in both `embeddings_vec` and `notes_fts`
- [x] Running capture twice on the same file produces no duplicate rows (idempotent)
- [x] TD-019: Already resolved — `validate_tags()` in `capture.py` line 240, `TAG_VIOLATION` audit at line 291. No work needed.
- [x] TD-050: `uv run pytest tests/test_vault/` passes 10 consecutive runs without intermittent failure

---

## What Session A does NOT build

- HYBRID RANKER — Session B
- RERANKER — Session B
- SEARCH FUNCTION — Session B
- `kms search` CLI — Session B
- `--reindex` flag — Session B

---

---

# Phase 3 Session B — Query Path + Post-Phase-3 Cleanup

_Status: ALIGNED 2026-06-10 — grill-validated + code-grounded. **Supersedes the original pre-code-reference draft**, which used stale function names (`index_note`), a stale `notes_fts` schema (3 cols), a wrong `snippet()` column index, and signatures never checked against code._

## Context

Session A is complete and merged (1147 tests). `embeddings_vec` (vec0) and `notes_fts` (FTS5) are populated at capture time via best-effort indexing. Session B builds the **query path** on top of that index layer: HYBRID RANKER → RERANKER → SEARCH FUNCTION → SEARCH CLI, plus post-Phase-3 debt cleanup.

**Primary consumer of `search()` is the MCP AI (Phase 4) — NOT a human at a terminal.** The AI queries the vault to write reports, track project progress, and synthesize insights. So `search()` returns a structured **AI-triage payload** (`vault_path` handle + `summary` + `snippet` + `score` + `metadata`); the AI reads the cheap payload, then pulls full content via `read_note(vault_path)` only for the notes it judges relevant. The `metadata` field is therefore **load-bearing** (the AI triages on it), not optional. The `kms search` CLI is the **C-15/C-16 verification proxy** for what the MCP tool will later do — it is not the end-user reading surface (humans read in Obsidian).

**The roadmap is stale — do NOT build from it.** The roadmap's "Phase 3 — Retrieval Infrastructure" narrative (TIER DISPATCHER, `max_cost` budget, hot/warm/cold escalation) is dead and explicitly disregarded. The ONLY living part of the roadmap is its **Stable Interfaces table** (`docs/roadmap/roadmap.md` lines 116–140). Build from THIS document.

## Architecture (locked — grill 2026-06-10)

```
search(query?, project?, date_range?, max_results?)
   │
   ▼
[1] METADATA PRE-FILTER  — documents table → candidate vault_paths
   │                       (project and/or date_range; neither → all paths)
   ├── query is None ─▶ [2] FILTER-ONLY: sort candidates by updated_at desc,
   │                        cap at max_results, return. (skip ranker+reranker)
   └── query given ──▶ [3] HYBRID RANKER (RRF: FTS5 BM25 + sqlite-vec KNN, scoped to candidates)
                            ▼
                        RERANKER (cross-encoder rescoring; attaches summary + metadata)
                            ▼
                        SearchResult[] capped at max_results
```

No tier dispatcher. No `max_cost`. The roadmap's "three-tier retrieval" promise is realized as: a cheap triage payload (summary / snippet / metadata) + lazy full-content fetch via `read_note` — not a cost-budget dispatcher.

## Verified Session A interfaces (read from code 2026-06-10 — THESE are the contract)

| What | Real signature / fact (verified) |
|---|---|
| Embedding index write | `retrieval/embeddings.py::index_embedding(vault_path, title, note_type, tags, summary, db_path=None) -> Result[None]` |
| Cached embed model | `retrieval/embeddings.py::_get_model()` → module-cached `SentenceTransformer` — **reuse this for query embedding in HYBRID RANKER** |
| Context builder | `retrieval/embeddings.py::_build_context_text(title, note_type, tags, summary) -> str` (so query/doc embeddings stay symmetric) |
| Keyword index write | `retrieval/keyword.py::index_keywords(vault_path, title, summary, body, db_path=None) -> Result[None]` |
| FTS5 table | `notes_fts(vault_path, title, summary, body)` — **4 columns**; `body` is column **index 3** (matters for `snippet()`) |
| Vector table | `embeddings_vec(vault_path, embedding)` — vec0 virtual table, 384-dim float32 blob |
| DB connection | `storage/db.py::get_connection(db_path=None, readonly=False)` — loads sqlite-vec, WAL, FK |
| Document columns | `documents`: `vault_path, title, summary, note_type, confidence, created_at, updated_at, updated_by_human, content_hash, batch_id, project, status, key_topics` |
| Search config | `core/config.py::SearchConfig`: `embedding_model=all-MiniLM-L6-v2`, `reranker_model=cross-encoder/ms-marco-MiniLM-L-6-v2`, `max_candidates=20`, `max_results=10` |
| CrossEncoder dep | Ships INSIDE `sentence-transformers` (no separate package); runs in-process. Search NEVER calls Ollama. |

**Stale names from the original draft — DO NOT USE:** `index_note()` never existed. `notes_fts` was never 3-column. `snippet(notes_fts, 1, ...)` targeted the wrong column. Use the verified table above.

## Build targets

### 1 · HYBRID RANKER — `src/retrieval/ranker.py` (new)
Intended (research verifies): `def rank(query, candidate_paths, max_candidates) -> Result[list[RankedResult]]`; `RankedResult(vault_path, rrf_score, snippet)`.
- FTS5 BM25 search on `notes_fts` scoped to `candidate_paths`.
- sqlite-vec KNN on `embeddings_vec` scoped to `candidate_paths`; embed query via cached `_get_model()` from `retrieval/embeddings.py`.
- Merge via Reciprocal Rank Fusion: `score = 1/(60+rank_fts5) + 1/(60+rank_vec)`. Never normalize+add raw scores.
- `snippet` via FTS5 `snippet()` targeting the **`body` column (index 3)** — the original draft's index 1 was wrong.
- Return top `max_candidates`; `Success`/`Failure`, never raise (C-12); `max_candidates` passed in, never hardcoded (C-06).
- ⚠ **See research-must-verify #1** — `WHERE vault_path IN (...) ORDER BY distance` may NOT be valid vec0 KNN syntax; candidate scoping may need a different mechanism.

### 2 · RERANKER — `src/retrieval/reranker.py` (new)
Intended: `def rerank(query, candidates) -> Result[list[SearchResult]]`; `SearchResult(vault_path, summary, snippet, score, metadata)`.
- Load cross-encoder from `CONFIG.main.search.reranker_model`; cache the instance at module level (mirror the `_get_model()` pattern in `embeddings.py`).
- Score `(query, candidate.snippet)` pairs via `CrossEncoder.predict`.
- Attach `summary` + `metadata` (project, note_type, updated_at, tags) from `storage/documents.py::get_by_path`.
- Order by cross-encoder score desc. `SearchResult` carries NO full body — full content via `read_note`. `Success`/`Failure` (C-12).

### 3 · SEARCH FUNCTION — `src/retrieval/search.py` (new — the public contract Phase 4 MCP consumes)
Intended: `def search(query=None, project=None, date_range=None, max_results=None, db_path=None) -> Result[list[SearchResult]]`.
- [1] Metadata pre-filter on `documents` (project and/or date_range → candidate `vault_path`s; neither → all).
- [2] Filter-only mode (query None): candidates sorted by `updated_at` desc, capped; skip ranker + reranker.
- [3] Query mode: candidates + query → HYBRID RANKER → RERANKER → capped `SearchResult[]`.
- Defaults from `CONFIG.main.search.max_results` / `max_candidates`. `Success`/`Failure` (C-12).
- Must **gracefully skip index rows whose underlying note was deleted** (carried decision #5).

### 4 · SEARCH CLI — `src/cli/main.py` (extend existing Click group)
Commands: `kms search "<query>"`, `--project Alpha`, `"<query>" --project Alpha`, `--since 7d|30d|YYYY-MM-DD`, `--max N`, `--reindex`.
- Output per result: real note **title** (from `documents` — NOT the raw filename; sibling files are named `report.pdf.md`), score, snippet. One block per result.
- `--reindex`: `documents.all_paths()` → per path `read_note()` → call `index_embedding(...)` + `index_keywords(...)` (**REAL names**). Report count. Idempotent — the batch self-heal.
- Wrap with `asyncio.run` (C-10); zero logic in CLI — logic lives in `retrieval/search.py`. Closes TD-012.

### 5 · TD-051 (isolated final phase) — `src/pipelines/classify.py` + `tests/test_pipelines/test_classify.py`
`classify()` validates `project` and `primary_domain` against ONE pooled name set — a domain name passes silently as a valid project (and vice versa). Fix: split into `project_names` vs `domain_names`; source via `ProjectRegistry.get_groups()` (group names = domains, entry names = projects); pass separate sets at the call site. Rewrite `VALID_DESTINATIONS` fixtures to the real header shape. ~6 tests. **Isolated phase — independent of the search work; cut cleanly if research finds it bigger than this paragraph.**

## Out of scope / deferred
- **TD-010** (Ollama httpx async): kept ONLY as a conditional post-ship check. Search never calls Ollama (in-process sentence-transformers). After Session B ships, measure the *capture/classify* Ollama path; rewrite only if >200ms/call. Do NOT rewrite speculatively this cycle.
- MCP `kms_search` tool — Phase 4 (after CLI verified end-to-end, C-15/C-16).
- Scheduling / automation — Phase 4+.
- Full-content terminal dump — not built; AI consumer uses `read_note`, human uses Obsidian.

## Research MUST-VERIFY (code-truth — do NOT trust this doc's signatures; this doc was once stale)
1. **CRITICAL — vec0 KNN candidate scoping.** Can `embeddings_vec` (vec0) actually do `WHERE vault_path IN (...) ORDER BY distance`? vec0 KNN usually requires `embedding MATCH ? AND k=?`. If IN-list scoping is invalid, HYBRID RANKER's candidate scoping needs redesign (KNN-then-filter, or a normal table + manual cosine). #1 design risk.
2. Migration 007 real shape: `embeddings_vec` columns/dim; `notes_fts` columns (expected `vault_path,title,summary,body`); does `snippet(notes_fts, 3, ...)` target `body`?
3. `documents.created_at`/`updated_at` are TEXT ISO strings via `datetime('now')` → confirm `date_range` (datetime tuple) filtering via string comparison works.
4. `search()`/`rank()`/`rerank()` are sync; `.encode()`/`.predict()` are CPU-bound. Reconcile with CLI `asyncio.run` (C-10) — confirm no event-loop block, or decide whether search runs in a worker thread.
5. `ProjectRegistry.get_groups()` real API + real `format_for_prompt` header shape (TD-051).
6. Degenerate "no filter → candidate_paths = all" case — does `IN (...)` blow up / should it be skipped in favour of global KNN?
7. `storage/documents.py::get_by_path` / `all_paths` real signatures + return types.

## ADR
The RRF+rerank-over-tier-dispatcher decision passes all three ADR gates (hard to reverse, surprising vs the roadmap, real trade-off). **The design step writes an ADR** documenting why Phase 3 search diverges from the roadmap's tier-dispatcher narrative.

## Acceptance criteria (Session B)

- [ ] Capture 5+ diverse notes across 2+ projects (meeting notes, research, project update, email)
- [ ] `kms search "stakeholder resistance"` → finds note about "managing pushback in meetings" (semantic match, different words)
- [ ] `kms search --project Alpha` → returns all Alpha notes, no query needed (filter-only mode)
- [ ] `kms search "budget Q3" --project Alpha` → semantic search scoped to Alpha only
- [ ] `kms search --reindex` → rebuilds both indexes; running twice produces identical results (idempotent)
- [ ] Results carry `vault_path`, `summary`, `snippet`, `score`, `metadata` — no full body in result
- [ ] `kms search` on `.md` notes AND sibling summaries both work; sibling results show a usable title (not `report.pdf.md`)
- [ ] Phase 9 Synthesis can call `search(date_range=last_week)` and receive results without a query term
- [ ] TD-051: `classify()` no longer validates a domain name as a valid project destination

---

## Grill decisions carried forward from Session A (2026-06-10) — still binding

1. **Sibling .md notes in search:** Frontmatter summary → embedding; body text (detailed expanded summary) → FTS5. Both describe the binary. Binary never indexed — sibling is the proxy. HYBRID RANKER and RERANKER must work correctly with sibling notes as results.
2. **No-summary fallback:** Notes with empty summaries were indexed with metadata-only contextual strings. HYBRID RANKER may encounter these — they rank via keyword matches (body) and metadata-only embeddings.
3. **Indexer failure handling:** Session A retries once silently, then emits a warning log. If `--reindex` finds notes with missing index entries, it re-indexes them — the batch self-heal.
4. **`--reindex` scope:** Rebuild both `embeddings_vec` and `notes_fts` for all notes in the documents table. Idempotent.
5. **Index maintenance on update/move/delete:** Session A wired index-row lifecycle into `documents.py` (`delete_by_path`/`rename`/`replace_path`). Session B's SEARCH FUNCTION must handle the case where an index row exists but the note was deleted (graceful skip, not crash).

## What Session B does NOT build

- MCP `kms_search` tool — Phase 4 (after this CLI is verified working end-to-end per C-15/C-16)
- Scheduling / automation — Phase 4+
