# Phase 3 Session A — Index Layer + Pre-flight Fixes

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

- [ ] `uv sync` succeeds with `sentence-transformers` and `sqlite-vec` installed
- [ ] `uv run pytest tests/` passes (1080+ tests, no regressions from pre-flight fixes)
- [ ] `kms capture <file>` completes and a row appears in both `embeddings_vec` and `notes_fts`
- [ ] Running capture twice on the same file produces no duplicate rows (idempotent)
- [x] TD-019: Already resolved — `validate_tags()` in `capture.py` line 240, `TAG_VIOLATION` audit at line 291. No work needed.
- [ ] TD-050: `uv run pytest tests/test_vault/` passes 10 consecutive runs without intermittent failure

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

## Context

Session B picks up where Session A left off. Session A must be complete and merged before starting Session B. Both `embeddings_vec` and `notes_fts` tables must be populated and all pre-flight fixes confirmed passing.

Full Phase 3 spec lives in `docs/roadmap/roadmap.md` under "## Phase 3 — Retrieval Infrastructure". Session B builds the last four components: HYBRID RANKER, RERANKER, SEARCH FUNCTION, SEARCH CLI. It also folds in post-Phase-3 debt cleanup.

---

## Pre-flight checks (verify before writing any Session B code)

- Session A acceptance criteria all pass (1080+ tests, both indexes populated on capture)
- `retrieval/embeddings.py` and `retrieval/keyword.py` exist and have passing unit tests
- `embeddings_vec` and `notes_fts` virtual tables exist in the DB (migration 007 applied)
- `CONFIG.main.search.max_candidates` and `CONFIG.main.search.max_results` are readable

---

## Build targets

### HYBRID RANKER

File: `src/retrieval/ranker.py`

Function signature:
```python
def rank(query: str, candidate_paths: list[str], max_candidates: int) -> Result[list[RankedResult]]:
```

where `RankedResult = dataclass(vault_path: str, rrf_score: float, snippet: str)`.

- Run FTS5 BM25 keyword search on `notes_fts` filtered to `candidate_paths` only — use `WHERE vault_path IN (...)` to scope to pre-filtered candidates
- Run sqlite-vec KNN search on `embeddings_vec` filtered to `candidate_paths` — embed the query string using `SentenceTransformer` (reuse cached instance from `retrieval/embeddings.py`), then `SELECT vault_path, distance FROM embeddings_vec WHERE vault_path IN (...)` ORDER BY KNN distance
- Merge via Reciprocal Rank Fusion: `score = 1/(60 + rank_fts5) + 1/(60 + rank_vec)` — never normalize and add raw scores (FTS5 BM25 and cosine distance are not on the same scale)
- `snippet`: use FTS5 `snippet(notes_fts, 1, '<b>', '</b>', '...', 20)` function — not the full body
- Return top `max_candidates` by RRF score; return `Success` or `Failure` — never raise (C-12)
- Read `max_candidates` from caller (passed in) — never hardcode (C-06)

### RERANKER

File: `src/retrieval/reranker.py`

Function signature:
```python
def rerank(query: str, candidates: list[RankedResult]) -> Result[list[SearchResult]]:
```

where `SearchResult = dataclass(vault_path: str, summary: str, snippet: str, score: float, metadata: dict)`.

- Load cross-encoder model from `CONFIG.main.search.reranker_model`
- Score each `(query, candidate.snippet)` pair using `CrossEncoder(model_name).predict([(query, snippet), ...])`
- Load `summary` and `metadata` (project, note_type, updated_at, tags) from `storage/documents.py::get_by_path` for each candidate
- Return candidates ordered by cross-encoder score descending
- `SearchResult` never carries full note body — callers use `vault/reader.py::read_note(vault_path)` for full content
- Cache `CrossEncoder` instance at module level (load once, reuse)
- Return `Success` or `Failure` — never raise (C-12)

### SEARCH FUNCTION

File: `src/retrieval/search.py`

Function signature:
```python
def search(
    query: str | None = None,
    project: str | None = None,
    date_range: tuple[datetime, datetime] | None = None,
    max_results: int | None = None,
    db_path: Path | None = None,
) -> Result[list[SearchResult]]:
```

Three-step execution:

1. **Metadata pre-filter** — query `documents` table for `vault_path` values matching `project` and/or `date_range`; if neither filter provided, use all paths. This narrows the candidate set in SQL before any vector work.
2. **Filter-only mode** — if `query` is None, return candidates sorted by `updated_at` descending, capped at `max_results`. Skip HYBRID RANKER and RERANKER entirely.
3. **Query mode** — pass candidate paths + query to HYBRID RANKER → RERANKER → return `SearchResult` list capped at `max_results`.

- Read `max_results` default from `CONFIG.main.search.max_results` when caller passes None
- Read `max_candidates` for HYBRID RANKER from `CONFIG.main.search.max_candidates`
- Return `Success` or `Failure` — never raise (C-12)

### SEARCH CLI

File: `src/cli/main.py` (extend existing Click group)

Commands:
- `kms search "<query>"` — semantic + keyword search
- `kms search --project Alpha` — filter-only, no query needed
- `kms search "<query>" --project Alpha` — scoped semantic search
- `kms search --since 7d` — filter by date (parse `7d`, `30d`, `YYYY-MM-DD`)
- `kms search --max N` — override result count
- `kms search --reindex` — rebuild both indexes for all notes in documents table

Output format per result: title (from vault_path filename), score, snippet. One result per block.

`--reindex` implementation: call `storage/documents.py::all_paths()` → for each path, call `vault/reader.py::read_note()` to get body + summary, then call both `retrieval/embeddings.index_note()` and `retrieval/keyword.index_note()`. Report count on completion.

Rules:
- Wrap with `asyncio.run(...)` — no async Click adapters (C-10)
- Zero logic in CLI layer — all logic lives in `retrieval/search.py`
- Closes TD-012

---

## Post-Phase-3 debt cleanup (fold into this session)

### TD-051 · classify() cross-type destination validation

**What:** `classify()` in `pipelines/classify.py` validates `project` and `primary_domain` against one pooled name set (`_destination_names`) parsed from the `format_for_prompt` string. A project name used as a `primary_domain` (or vice versa) still validates silently — wrong-kind destination could get created.

**Fix:** Split into two sets: `project_names` and `domain_names`. Use `ProjectRegistry.get_groups()` — group names = domains, entry names = projects. Update `_destination_names` at the call site to pass separate sets. Rewrite `VALID_DESTINATIONS` fixtures in `tests/test_pipelines/test_classify.py` to the real header shape (`domain_name:` → list of project names under each domain).

**Scope:** `src/pipelines/classify.py` + `tests/test_pipelines/test_classify.py`. Fixes ~6 tests that use non-real fixture shape.

### TD-010 · Ollama httpx async rewrite (conditional)

Check actual search latency after Session B is working end-to-end. If `OllamaProvider` embedding calls show measurable thread overhead (>200ms per call), rewrite `OllamaProvider` to use native `httpx` async instead of `asyncio.to_thread(requests.post)`. Only act if evidence warrants it — do not rewrite speculatively.

### TD-012 · search CLI stub

Naturally closed when `kms search` command is built above.

---

## Acceptance criteria (Session B)

- [ ] Capture 5+ diverse notes across 2+ projects (meeting notes, research, project update, email)
- [ ] `kms search "stakeholder resistance"` → finds note about "managing pushback in meetings" (semantic match, different words)
- [ ] `kms search --project Alpha` → returns all Alpha notes, no query needed (filter-only mode)
- [ ] `kms search "budget Q3" --project Alpha` → semantic search scoped to Alpha only
- [ ] `kms search --reindex` → rebuilds both indexes; running twice produces identical results (idempotent)
- [ ] Results carry `vault_path`, `summary`, `snippet`, `score` — no full body in result
- [ ] `kms search` on `.md` notes and sibling summaries both work
- [ ] Phase 9 Synthesis can call `search(date_range=last_week)` and receive ranked results without a query term
- [ ] TD-051: classify() no longer validates a domain name as a valid project destination

---

## Grill decisions carried forward from Session A (2026-06-10)

These decisions were locked during Session A's grill interview. Session B must honor them:

1. **Sibling .md notes in search:** Frontmatter summary was used for embedding; body text (detailed expanded summary) was used for FTS5. Both describe the binary. Binary never indexed — sibling is the proxy. The HYBRID RANKER and RERANKER must work correctly with sibling notes as search results.
2. **No-summary fallback:** Notes with empty summaries were indexed with metadata-only contextual strings. HYBRID RANKER may encounter these — they will rank via keyword matches (body) and metadata-only embeddings.
3. **Indexer failure handling:** Session A retries once silently, then emits warning log. If `--reindex` discovers notes with missing index entries, it should re-index them — this is the batch self-healing mechanism.
4. **`--reindex` scope:** Must rebuild both `embeddings_vec` and `notes_fts` for all notes in documents table. Idempotent — running twice produces identical results.
5. **Index maintenance on update/move/delete:** Session A design determined how index rows track note lifecycle changes via existing `documents.py` mechanisms. Session B's SEARCH FUNCTION must handle the case where an index row exists but the note has been deleted (graceful skip, not crash).

## What Session B does NOT build

- MCP `kms_search` tool — Phase 4 (after this CLI is verified working end-to-end per C-15/C-16)
- Scheduling / automation — Phase 4+
