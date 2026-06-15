# Phase 9 — MCP Adaptation: Spec

_Created: 2026-06-15_
_Input: Design doc (docs/1_design/phase9_mcp_adaptation.md), Grill v3 (docs/0_draft/P9_mcp_adaptation_grill.md)_

---

## Feature Overview

Phase 9 rewrites the MCP server so the consuming AI (Claude Desktop) talks to a cloud database of distilled knowledge facts instead of reading files from the user's laptop. The five tools change from "search files, read files, move files" to "discover what the system knows, search across both facts and documents, drill into specific items, save new insights, and correct existing knowledge." This is the bridge between the extraction engine (Phase 8) and the user-facing AI experience.

```
Consumer AI (Claude Desktop)
        │ calls 5 MCP tools
        ▼
   Tool Shims ──────────────────────────────────────┐
   │              │                │                  │
   │ vault_info   │ inspect        │ write            │ correct
   │ + search     │                │                  │
   ▼              ▼                ▼                  ▼
Context Engine  Document       Capture Pipeline   Knowledge Store
(fact assembly  Resolver       (summarize +       (patch entry +
 + dual-corpus  (3-tier:       classify chat      audit log)
 search)        summary/text/  content)
   │    │       file from DB)      │
   │    │           │              │ creates docs +
   │    │           │ reads by     │ triggers classify
   │    │           │ integer id   │
   │    │           ▼              ▼
   │    │       Document Store  Audit Logger
   │    │           ▲
   │    │           │ syncs via HTTP
   │    │       Laptop Daemon
   │    │
   │    └──→ Search Coordinator (existing doc search)
   │
   └──→ Knowledge Store → Fact Search Index (NEW: keyword + meaning)
```

---

## Resolved Open Questions

These were flagged as open in the design doc. All are now locked.

| # | Question | Decision | Rationale |
|---|---|---|---|
| OQ-1 | FTS5 sync strategy (triggers vs Python) | **Python-side sync.** Explicit INSERT/DELETE into `facts_fts` inside `knowledge_entries.upsert()` and `retire()`, in the same DB transaction. | Matches existing pattern in `documents.py` where search cleanup (`notes_fts`, `embeddings_vec`) is inside the same `with get_connection()` block. Avoids trigger fragility across migrations. |
| OQ-2 | Fact embedding timing (sync vs async) | **Synchronous during upsert.** Embed the fact text and INSERT into `facts_vec` in the same `upsert()` call. | Matches existing document embedding pattern in `retrieval/embeddings.py`. Classify already tolerates latency. Keeps the index always consistent with the entry table. |
| OQ-3 | `kms_correct` un-retire operation | **Add it.** One more operation: `un_retire` sets status back to `confident`, clears reasoning. | Low cost. Workaround (`kms_write` → new entry) is awkward and creates duplicate facts. Adding `un_retire` is one UPDATE. |
| OQ-4 | `kms_write` pipeline invocation path | **Direct in-process function call** to `capture_upload()`. Not HTTP loopback. The MCP tool and capture pipeline live in the same container. | Avoids network overhead and auth complexity for an in-process call. `capture_upload` is already an async function that can be awaited directly. |
| OQ-5 | `retrieval_score` sweep scheduling | **Background task in composed lifespan**, same pattern as the classify worker. Runs once per `sweep_interval` (config, default 24h). | Simpler than cron. Already have the composed-lifespan pattern from Phase 8 Slice A. |
| OQ-6 | `kms_inspect` text mode cap | **Default 5 refs per call.** Config-driven via `mcp.inspect.max_text_refs`. | Balances utility (enough for a multi-doc comparison) with context-window cost (5 full bodies is already heavy). |

---

## Component Inventory

| ID | Name | Cluster | Depends On | Behavior IDs | Test Requirements |
|---|---|---|---|---|---|
| P9-E-01 | Restore container boot | E — Bug Fixes | — | P9-MCP-16 | Boot test: import `cloud_entry`, verify `__main__` block |
| P9-E-02 | API key read-once | E — Bug Fixes | — | P9-MCP-17 | Unit: `require_key` uses stored value, not `os.environ` per call |
| P9-E-03 | Async blob put | E — Bug Fixes | — | P9-MCP-18 | Async test: `capture_upload` blob branch does not block event loop |
| P9-E-04 | Async delete cleanup | E — Bug Fixes | — | P9-MCP-18 | Async test: delete handler does not block event loop |
| P9-B-01 | Fact search migration (012) | B — Retrieval Foundation | — | P9-MCP-11 | Migration test: tables exist, schema version = 12 |
| P9-B-02 | Fact FTS + embedding sync | B — Retrieval Foundation | P9-B-01 | P9-MCP-11 | Unit: upsert populates `facts_fts` + `facts_vec`; retire removes from both |
| P9-B-03 | `get_by_id` on documents | B — Retrieval Foundation | — | P9-MCP-20 | Unit: returns `Success(DocumentRow)`, `Success(None)`, `Failure` |
| P9-B-04 | Expose `id` on search results | B — Retrieval Foundation | P9-B-03 | P9-MCP-21 | Unit: `SearchResult.id` populated in `_card_from_row` |
| P9-B-05 | `retrieval_score` increment + sweep | B — Retrieval Foundation | — | P9-MCP-12 | Unit: increment formula correct; sweep decays all entries; background task starts in lifespan |
| P9-B-06 | Dual-corpus search + identity dedup | B — Retrieval Foundation | P9-B-01, P9-B-02, P9-B-04 | P9-MCP-02, P9-MCP-03 | Unit: fact + doc results merged; same-id deduped; unclassified docs still surface |
| P9-D-01 | Three-tier resolve rewrite | D — Three-Tier Resolve | P9-B-03 | P9-MCP-13 | Unit: summary/text/file modes; NULL full_body degradation; batched ids |
| P9-D-02 | `kms_inspect` shim rewrite | D — Three-Tier Resolve | P9-D-01 | P9-MCP-04, P9-MCP-05 | Unit: shim delegates to resolve; mode defaults; text cap enforced |
| P9-C-01 | Context engine rewrite — vault_info | C — Context Engine | P9-B-05 | P9-MCP-01, P9-MCP-09, P9-MCP-10 | Unit: entity map from DB; orientation facts ranked; budget-capped; zero disk reads |
| P9-C-02 | Context engine rewrite — search response | C — Context Engine | P9-B-06, P9-C-01 | P9-MCP-02, P9-MCP-09, P9-MCP-10 | Unit: orientation facts prepended; identity dedup across conversation; dual-corpus merge piped through |
| P9-C-03 | Context engine — identity dedup memory | C — Context Engine | — | P9-MCP-10 | Unit: `_dedup_memory` tracks fact row ids + doc ids; re-injection blocked |
| P9-A-01 | `kms_write` backing + shim | A — Tool Surface | P9-E-01 | P9-MCP-06 | Unit: capture_upload called with `source_type=chat_session`; returns new doc id |
| P9-A-02 | `kms_correct` backing + shim | A — Tool Surface | — | P9-MCP-07, P9-MCP-08 | Unit: each op (edit/tag/entity/promote/retire/un-retire) works; audit logged; nonexistent id → Failure |
| P9-A-03 | `kms_vault_info` shim rewrite | A — Tool Surface | P9-C-01 | P9-MCP-01 | Unit: shim delegates to new engine; no disk reads |
| P9-A-04 | `kms_search` shim rewrite | A — Tool Surface | P9-C-02 | P9-MCP-02, P9-MCP-03 | Unit: shim delegates to new engine; returns merged results |
| P9-A-05 | Remove `kms_read` + `kms_move` + `_move.py` | A — Tool Surface | — | P9-MCP-14 | Verify: `kms_read` gone; `kms_move` gone; `_move.py` deleted; `register_tools` registers exactly 5 tools |
| P9-F-01 | `_should_overwrite` seam in classify_writer | F — Phase 10 Seam | — | P9-MCP-15 | Unit: function exists; called in update path; always returns True |
| P9-G-01 | `AI_INSTRUCTIONS.md` rewrite | G — Documentation | P9-A-01 through P9-A-05 | P9-MCP-19 | Manual review: 5-tool surface; facts-vs-summary model; correct-vs-write routing; behavioral stance |
| P9-G-02 | AgentBase deployment guide | G — Documentation | P9-E-01 | — | Manual: guide enables non-technical person to stand up instance + connect Claude Desktop |

---

## Detailed Components

### P9-E-01: Restore Container Boot

**Cluster:** E — Bug Fixes
**Depends on:** none
**Delivers:** P9-MCP-16
**File(s):** `src/mcp_server/cloud_entry.py`

**What it does:** Restores the `if __name__ == "__main__": uvicorn.run(build_app(), host="0.0.0.0", port=8080)` block that was lost in commit `1b1f33d` during Phase 7B. Without this, `scripts/start.sh` starts nothing and the container sits idle.

**Changes:**
- Add `if __name__ == "__main__":` block at the bottom of `cloud_entry.py` with `uvicorn.run(build_app(), host="0.0.0.0", port=8080)`.

**Constraints:** None specific.

**Test requirements:**
- Import `cloud_entry` module, verify the `__main__` block exists (source text inspection or importlib check).
- Optionally: subprocess test that `python -m mcp_server.cloud_entry` starts without error (smoke-level).

**Assumptions for research verification:**
- Confirm the exact content of `scripts/start.sh` to verify it invokes `cloud_entry.py` via `__main__`.

---

### P9-E-02: API Key Read-Once

**Cluster:** E — Bug Fixes
**Depends on:** none
**Delivers:** P9-MCP-17
**File(s):** `src/mcp_server/api.py`

**What it does:** The `require_key` function at `api.py:86` currently calls `os.environ.get("KMS_DAEMON_API_KEY")` on every request. This is moved to a module-level variable set once at import time (or in `build_app()`), so the env lookup happens once.

**Function signatures** (changed):
```python
# Module-level (new)
_daemon_api_key: str | None = os.environ.get("KMS_DAEMON_API_KEY")

# require_key compares against _daemon_api_key instead of os.environ.get()
def require_key(request: Request) -> str | None:
    ...
```

**Data flow:** At module load or `build_app()` time, `KMS_DAEMON_API_KEY` is read from `os.environ` into `_daemon_api_key`. `require_key` uses the cached value.

**Constraints:** C-11 (no `load_dotenv` in library code — this is just `os.environ.get`, which is fine).

**Test requirements:**
- Unit: with `_daemon_api_key` set, `require_key` returns key on match, `None` on mismatch.
- Unit: changing `os.environ["KMS_DAEMON_API_KEY"]` after module load does not affect `require_key` (proves read-once behavior).

**Assumptions for research verification:**
- Confirm whether `build_app()` is a better place to set the cached key (if the env var is not available at import time in some deployment modes).

---

### P9-E-03: Async Blob Put

**Cluster:** E — Bug Fixes
**Depends on:** none
**Delivers:** P9-MCP-18
**File(s):** `src/pipelines/capture.py`

**What it does:** At `capture.py:342` (approximate), the binary branch calls sync `blob_store.put()` from an async handler, blocking the event loop. Switch to `blob_store.async_put()` which already exists at `blobs.py:316`.

**Changes:**
- Replace `blob_store.put(key, data)` with `await blob_store.async_put(key, data)` in the binary capture branch.

**Constraints:** C-10 (asyncio patterns).

**Test requirements:**
- Async test: mock `blob_store.async_put`, verify it is awaited (not the sync `put`).

**Assumptions for research verification:**
- Confirm exact line number and call site in `capture.py` for the sync `blob_store.put()` call.
- Confirm `async_put` signature and return type in `blobs.py`.

---

### P9-E-04: Async Delete Cleanup

**Cluster:** E — Bug Fixes
**Depends on:** none
**Delivers:** P9-MCP-18
**File(s):** `src/mcp_server/api.py`

**What it does:** `_delete_with_blob_cleanup` at `api.py:358` is a sync function called from an async route handler at `api.py:524`. Wrap the call in `asyncio.to_thread()` so the DB + blob cleanup does not block the event loop.

**Changes:**
- In the async delete handler, replace `result = _delete_with_blob_cleanup(...)` with `result = await asyncio.to_thread(_delete_with_blob_cleanup, ...)`.
- Add `import asyncio` at the top of `api.py` (was previously removed as unused — M10 fix).

**Constraints:** C-10 (asyncio patterns).

**Test requirements:**
- Async test: mock `_delete_with_blob_cleanup`, verify it is called via `asyncio.to_thread` (does not block event loop).

**Assumptions for research verification:**
- Confirm the exact async handler function name that calls `_delete_with_blob_cleanup`.

---

### P9-B-01: Fact Search Migration (012)

**Cluster:** B — Retrieval Foundation
**Depends on:** none
**Delivers:** P9-MCP-11
**File(s):** `src/storage/migrations/012_fact_search_index.sql`

**What it does:** Creates two new search tables for knowledge facts: `facts_fts` (FTS5 keyword index on `knowledge_entries.fact` + `entity`) and `facts_vec` (vec0 embedding index on fact text). These enable hybrid search over the short fact texts in `knowledge_entries`.

**Migration SQL:** See [Migration Spec](#migration-spec) section below.

**Constraints:** C-05 (all schema changes via versioned .sql deltas).

**Test requirements:**
- Migration test: after `init_db`, `facts_fts` and `facts_vec` tables exist.
- Schema version test: version = 12 after migration.
- Cascade: bump version-pin assertions in `test_migration_011.py` from 11 to 12.

**Assumptions for research verification:**
- Confirm that `vec0` supports `FLOAT[384]` syntax for the `all-MiniLM-L6-v2` model dimension.
- Confirm FTS5 external content mode syntax is compatible with the `knowledge_entries` table layout.

---

### P9-B-02: Fact FTS + Embedding Sync in Upsert/Retire

**Cluster:** B — Retrieval Foundation
**Depends on:** P9-B-01
**Delivers:** P9-MCP-11
**File(s):** `src/storage/knowledge_entries.py`

**What it does:** Adds Python-side sync of `facts_fts` and `facts_vec` inside `upsert()` and `retire()`, within the same DB transaction. On INSERT: add to both search tables. On UPDATE: delete old + re-insert in both. On RETIRE: delete from both. Embedding is computed synchronously via `_get_model().encode()`.

**Function signatures** (changed — internal additions to existing functions):
```python
# knowledge_entries.py — upsert() gains internal FTS/vec sync
def upsert(
    entry: KnowledgeEntry,
    *,
    status: str | None = None,
    band: ConfidenceBand | None = None,
    db_path: Path | None = None,
) -> Result[int]:
    """Insert or update a knowledge entry.
    Now also syncs facts_fts and facts_vec in the same transaction."""
    ...

# knowledge_entries.py — retire() gains internal FTS/vec cleanup
def retire(entry_id: int, reason: str, *, db_path: Path | None = None) -> Result[int]:
    """Retire a knowledge entry. Now also removes from facts_fts and facts_vec."""
    ...
```

**Data flow:**
1. `upsert()` INSERT path: after inserting into `knowledge_entries`, INSERT into `facts_fts` (entry_id, entity, fact), compute embedding via `_get_model().encode(fact)`, INSERT into `facts_vec` (entry_id, embedding blob).
2. `upsert()` UPDATE path: DELETE old rows from `facts_fts` and `facts_vec` by entry_id, then re-INSERT with updated values.
3. `retire()`: DELETE from `facts_fts` and `facts_vec` by entry_id within the same `get_connection()` context.

**Constraints:** C-05 (no in-code schema changes — this is data manipulation, not schema). C-04 (FK pragma on connections).

**Test requirements:**
- Unit: after `upsert(new_entry)`, query `facts_fts` for the entity+fact text → returns the entry.
- Unit: after `upsert(existing_entry_with_changed_fact)`, old fact text gone from `facts_fts`, new text present.
- Unit: after `retire(entry_id)`, `facts_fts` and `facts_vec` rows for that id are gone.
- Unit: embedding dimension matches 384 floats.

**Assumptions for research verification:**
- Confirm `_get_model()` from `retrieval/embeddings.py` can be imported without triggering CONFIG validation (C-17 compliance — may need lazy import).
- Confirm `facts_fts` external content mode DELETE syntax: `DELETE FROM facts_fts WHERE entry_id = ?` or requires `INSERT INTO facts_fts(facts_fts, entry_id, entity, fact) VALUES ('delete', ?, ?, ?)`.

---

### P9-B-03: `get_by_id` on Documents

**Cluster:** B — Retrieval Foundation
**Depends on:** none
**Delivers:** P9-MCP-20
**File(s):** `src/storage/documents.py`

**What it does:** Adds a new `get_by_id` function to look up a document row by its integer primary key. Currently only `get_by_path` (by vault_path) exists. The three-tier resolve and MCP tools need to reference documents by stable integer id.

**Function signatures** (new):
```python
def get_by_id(
    doc_id: int, db_path: Path | None = None
) -> Result[DocumentRow | None]:
    """Fetch the documents row for a given integer id.

    Args:
        doc_id: Integer primary key of the document.
        db_path: Override DB path.

    Returns:
        Success(DocumentRow) if found, Success(None) if not found,
        or Failure(recoverable=False) on sqlite3.Error.
    """
    ...
```

**Data flow:** `SELECT * FROM documents WHERE id = ?` → `_row_from_sqlite()` → `Result[DocumentRow | None]`.

**Constraints:** C-12 (Result returns).

**Test requirements:**
- Unit: existing row → `Success(DocumentRow)` with correct fields.
- Unit: nonexistent id → `Success(None)`.
- Unit: DB error (e.g., closed connection) → `Failure(recoverable=False)`.

**Assumptions for research verification:**
- Confirm `documents.id` is `INTEGER PRIMARY KEY` in `schema.sql` (not `AUTOINCREMENT` — affects existence check).

---

### P9-B-04: Expose `id` on Search Results

**Cluster:** B — Retrieval Foundation
**Depends on:** P9-B-03
**Delivers:** P9-MCP-21
**File(s):** `src/retrieval/reranker.py`, `src/retrieval/search.py`

**What it does:** Adds an `id` field to the `SearchResult` dataclass so consumers can use integer id-based references for `kms_inspect` and `kms_correct`. The `_card_from_row` helper in `search.py` populates it from `DocumentRow.id`.

**Function signatures** (changed dataclass):
```python
@dataclass(frozen=True)
class SearchResult:
    vault_path: str
    summary: str | None
    snippet: str
    score: float
    metadata: dict
    id: int | None = None  # NEW — document integer id
```

**Data flow:** `_card_from_row(row, ...)` reads `row.id` and passes it to `SearchResult(id=row.id, ...)`.

**Constraints:** None specific. The `id=None` default preserves backward compatibility.

**Test requirements:**
- Unit: `_card_from_row` with a `DocumentRow` that has `id=42` → `SearchResult.id == 42`.
- Unit: search results from `search()` have `id` populated.

**Assumptions for research verification:**
- Confirm the reranker's `_build_card` (or equivalent) also needs updating, not just `search.py`'s `_card_from_row`. Both paths that create `SearchResult` must set `id`.

---

### P9-B-05: `retrieval_score` Increment + Sweep

**Cluster:** B — Retrieval Foundation
**Depends on:** none
**Delivers:** P9-MCP-12
**File(s):** `src/storage/knowledge_entries.py`, `src/mcp_server/cloud_entry.py`

**What it does:** Three parts:

1. **Python rename:** The `KnowledgeEntry` dataclass field `retrieval_count: int = 0` is retyped to `retrieval_score: float = 0.0`. The `_row_to_entry` function reads the column as float. No SQL column rename (SQLite type-flexible).

2. **Increment function:** New `bump_retrieval_score(entry_id, decay_factor)` updates one entry: `SET retrieval_count = retrieval_count * decay_factor + 1.0`.

3. **Sweep function + background task:** New `sweep_retrieval_scores(decay_factor)` runs `UPDATE knowledge_entries SET retrieval_count = retrieval_count * decay_factor`. A background `asyncio.Task` in the composed lifespan runs this once per `sweep_interval`.

**Function signatures** (new):
```python
# knowledge_entries.py
def bump_retrieval_score(
    entry_id: int,
    *,
    decay_factor: float = 0.95,
    db_path: Path | None = None,
) -> Result[int]:
    """Increment retrieval_score for one entry.
    Formula: retrieval_count = retrieval_count * decay_factor + 1.0
    Returns Success(rowcount)."""
    ...

def sweep_retrieval_scores(
    *,
    decay_factor: float = 0.95,
    db_path: Path | None = None,
) -> Result[int]:
    """Decay all retrieval scores.
    Formula: retrieval_count = retrieval_count * decay_factor
    Returns Success(rows_affected)."""
    ...
```

**Data flow:** Context engine calls `bump_retrieval_score` each time a fact is surfaced. Composed lifespan starts `_retrieval_sweep_worker()` that sleeps for `sweep_interval` then calls `sweep_retrieval_scores`.

**Constraints:** C-06 (decay_factor and sweep_interval from config, not hardcoded).

**Test requirements:**
- Unit: `bump_retrieval_score` on entry with score 0.0 → score becomes 1.0.
- Unit: `bump_retrieval_score` on entry with score 5.0 (decay=0.95) → score becomes 5.75.
- Unit: `sweep_retrieval_scores` decays all entries.
- Unit: background task starts and runs in composed lifespan.

**Assumptions for research verification:**
- Confirm the `retrieval_count` column in `knowledge_entries` is typed as `INTEGER` in schema but SQLite will accept REAL values without error. Test with an actual INSERT/UPDATE to be sure.

---

### P9-B-06: Dual-Corpus Search + Identity Dedup

**Cluster:** B — Retrieval Foundation
**Depends on:** P9-B-01, P9-B-02, P9-B-04
**Delivers:** P9-MCP-02, P9-MCP-03
**File(s):** `src/retrieval/fact_search.py` (NEW), `src/retrieval/search.py` (modified)

**What it does:** Adds a new fact-search module that runs hybrid keyword+semantic search over `facts_fts` and `facts_vec`, then a dual-corpus coordinator that runs fact search and document search independently, merges results, and deduplicates by identity (row id).

**Function signatures** (new):
```python
# retrieval/fact_search.py (NEW)
@dataclass(frozen=True)
class FactResult:
    """A knowledge fact matching a search query."""
    entry_id: int
    dimension: str
    entity: str
    fact: str
    confidence: float | None
    trust_score: float
    retrieval_score: float
    sources: list[str]   # doc id strings
    score: float         # RRF or blend score

def search_facts(
    query: str,
    *,
    max_results: int = 20,
    keyword_weight: float = 0.5,
    db_path: Path | None = None,
) -> Result[list[FactResult]]:
    """Hybrid keyword+semantic search over knowledge_entries facts.
    keyword_weight is config-driven (research gate for short-fact separation).
    Returns ranked FactResults."""
    ...
```

```python
# retrieval/search.py — new coordinator function
@dataclass(frozen=True)
class DualCorpusResult:
    """Merged search result across facts and documents."""
    facts: list[FactResult]
    documents: list[SearchResult]

def search_dual(
    query: str,
    *,
    project: str | None = None,
    date_range: tuple[datetime, datetime] | tuple[datetime, None] | None = None,
    max_fact_results: int | None = None,
    max_doc_results: int | None = None,
    location: str | None = None,
    db_path: Path | None = None,
) -> Result[DualCorpusResult]:
    """Run fact search + document search independently, merge, identity-dedup.
    Returns DualCorpusResult with separate fact and doc lists (already deduped)."""
    ...
```

**Data flow:**
1. `search_dual` calls `search_facts(query)` → fact results.
2. `search_dual` calls existing `search(query, ...)` → document results.
3. Identity dedup: if a document id appears in both a fact's `sources` list AND in the document results, both are kept (they are different identities — a fact and a document). Dedup is within each list: no duplicate fact ids, no duplicate doc ids.
4. Return `DualCorpusResult(facts, documents)`.

**Constraints:** C-06 (keyword_weight from config). C-12 (Result returns).

**Test requirements:**
- Unit: `search_facts` returns facts matching keyword query.
- Unit: `search_facts` returns facts matching semantic query (requires embedding).
- Unit: `search_dual` merges fact + doc results.
- Unit: unclassified document (no facts yet) surfaces via document search leg (P9-MCP-03).
- Unit: identity dedup — same fact id appearing twice is collapsed to one.
- Research spike (embedded in implementation): embed 50+ real short facts, measure embedding separation. Adjust `keyword_weight` default based on results.

**Assumptions for research verification:**
- Confirm FTS5 MATCH syntax works on `facts_fts` (external content mode — may need special handling for queries).
- Confirm `facts_vec` MATCH + k syntax works identically to `embeddings_vec`.

---

### P9-D-01: Three-Tier Resolve Rewrite

**Cluster:** D — Three-Tier Resolve
**Depends on:** P9-B-03
**Delivers:** P9-MCP-13
**File(s):** `src/mcp_server/_resolve.py` (complete rewrite)

**What it does:** Replaces the current disk-based binary resolver with a DB-first three-tier lookup by integer document id. Three modes:
- `summary` (default): reads `documents.summary` from DB. Always available.
- `text`: reads `documents.full_body` from DB. If NULL, degrades to summary with a note.
- `file`: returns `documents.vault_path`. Consumer handles laptop availability.

**Function signatures** (rewrite):
```python
@dataclass(frozen=True)
class ResolveResult:
    """One resolved document reference."""
    doc_id: int
    mode: str          # "summary" | "text" | "file"
    content: str       # the summary text, full body, or vault path
    title: str
    degraded: bool     # True if text mode fell back to summary

def resolve(
    doc_ids: list[int],
    mode: str = "summary",
    *,
    max_text_refs: int = 5,
    db_path: Path | None = None,
) -> Result[list[ResolveResult]]:
    """Resolve documents by integer id at the requested detail tier.

    Args:
        doc_ids:       List of integer document ids to resolve.
        mode:          One of "summary", "text", "file".
        max_text_refs: Cap on how many ids may use text mode (config-driven).
        db_path:       Override DB path.

    Returns:
        Success(list[ResolveResult]) or Failure on error.
        Missing ids are skipped (not an error).
    """
    ...
```

**Data flow:** For each id in `doc_ids`: call `get_by_id(id)` → if found, extract the requested tier from the `DocumentRow` → build `ResolveResult`. For `text` mode beyond `max_text_refs`, degrade to `summary`.

**Constraints:** C-12 (Result returns). C-14 (logic stays out of tools.py — all branching here).

**Test requirements:**
- Unit: `resolve([42], "summary")` returns summary text.
- Unit: `resolve([42], "text")` returns full_body when present.
- Unit: `resolve([42], "text")` with NULL `full_body` → degrades to summary, `degraded=True`.
- Unit: `resolve([42], "file")` returns vault_path.
- Unit: `resolve([999], "summary")` with nonexistent id → empty list (skip, not error).
- Unit: `resolve([1,2,3,4,5,6], "text", max_text_refs=5)` → first 5 get text, 6th gets summary.
- Unit: `resolve([], "summary")` → empty list.

**Assumptions for research verification:**
- Confirm `DocumentRow.summary` is never NULL for documents that have been through capture (it is set by `attach_summary`). For documents that only have `upsert_from_upload` without `attach_summary` yet, summary may be NULL — resolve should handle this (return empty string or "[Summary pending]").

---

### P9-D-02: `kms_inspect` Shim Rewrite

**Cluster:** D — Three-Tier Resolve
**Depends on:** P9-D-01
**Delivers:** P9-MCP-04, P9-MCP-05
**File(s):** `src/mcp_server/tools.py`

**What it does:** Rewrites the `kms_inspect` tool shim to accept batched integer ids and a uniform mode. The shim remains logic-free (C-14) — it calls `resolve()` and returns.

**Function signatures** (changed shim):
```python
def kms_inspect(
    doc_ids: list[int],
    mode: str = "summary",
    ctx: Context = None,
) -> list[dict]:
    """Drill into documents by integer id.
    Mode: summary (default), text (full body), file (vault path).
    Returns list of resolved references."""
    ...
```

**Data flow:** Shim calls `resolve(doc_ids, mode, max_text_refs=config_value)` → unwrap → return.

**Constraints:** C-14 (tools.py logic-free — no if/for/while at statement level).

**Test requirements:**
- Unit: shim correctly delegates to `resolve()` with the right args.
- Unit: default mode is `"summary"` when not specified.

**Assumptions for research verification:**
- Confirm FastMCP framework correctly exposes `list[int]` and `str` params in the tool schema for consumer AIs.

---

### P9-C-01: Context Engine Rewrite — Vault Info

**Cluster:** C — Context Engine
**Depends on:** P9-B-05
**Delivers:** P9-MCP-01, P9-MCP-09, P9-MCP-10
**File(s):** `src/mcp_server/context.py` (complete rewrite)

**What it does:** Rewrites `build_vault_info_response` to query `knowledge_entries` for all non-retired entities grouped by dimension, rank them, and cap per dimension. Produces orientation fact bullets. Zero disk reads. Zero CLAUDE.md. Zero `ProjectRegistry` dependency.

**Function signatures** (rewrite):
```python
class ContextInjectionEngine:
    def __init__(self) -> None:
        self._dedup_memory: set[int] = set()  # fact row ids already injected

    def build_vault_info_response(
        self,
        *,
        db_path: Path | None = None,
    ) -> Result[list[dict]]:
        """Build structural overview from knowledge_entries.

        Returns:
            Success(list[dict]) — blocks:
              1. Entity map (all dimensions → entity names, capped)
              2. Per-dimension orientation fact bullets (ranked, capped)
              3. Inbox stats (from documents table)
        """
        ...
```

**Data flow:**
1. Query `knowledge_entries` grouped by dimension → entity names. Cap per dimension at `max_entities_per_dimension`.
2. For each dimension, query ranked facts (trust_score DESC, retrieval_score DESC, confidence DESC, updated_at DESC). Cap at `max_orientation_facts_per_dimension`.
3. Record injected fact ids in `_dedup_memory`.
4. Query inbox stats from `documents` table (count of `vault_path LIKE 'inbox/%'`).
5. Assemble response blocks.

**Constraints:** C-06 (caps from config). C-17 (lazy CONFIG import in tests).

**Test requirements:**
- Unit: entity map correctly groups entities by dimension.
- Unit: entity cap respected (e.g., max 10 entities per dimension, 15 exist → only 10 shown + "+5 more" note).
- Unit: orientation facts ranked by 4-key sort.
- Unit: orientation facts capped per dimension.
- Unit: injected fact ids recorded in `_dedup_memory`.
- Unit: zero disk reads (no `Path.read_text`, no `Path.is_file` calls).

**Assumptions for research verification:**
- Confirm `query_ranked_by_dimension` in `knowledge_entries.py` uses only trust+confidence+recency (3-key). Phase 9 needs to add `retrieval_count` (retrieval_score) as 2nd key. This requires either modifying the existing function or adding a new one.

---

### P9-C-02: Context Engine Rewrite — Search Response

**Cluster:** C — Context Engine
**Depends on:** P9-B-06, P9-C-01
**Delivers:** P9-MCP-02, P9-MCP-09, P9-MCP-10
**File(s):** `src/mcp_server/context.py`

**What it does:** Rewrites `build_search_response` to call dual-corpus search, prepend orientation facts for entities mentioned in results, and apply conversation-level identity dedup. Concentration gating is dropped — facts are always injected, controlled by budget caps.

**Function signatures** (rewrite):
```python
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
        """Build search response with orientation facts + dual-corpus results.

        Returns:
            Success(list[dict]) — blocks:
              1. Orientation fact bullets (for entities appearing in results)
              2. Query fact results (from knowledge_entries search)
              3. Document result cards (from documents search)
        """
        ...
```

**Data flow:**
1. Call `search_dual(query, project=..., ...)` → `DualCorpusResult`.
2. Extract entities from fact results.
3. For each entity, fetch orientation facts (already ranked by P9-C-01 helpers).
4. Filter out facts already in `_dedup_memory`.
5. Assemble: orientation blocks → fact result blocks → document result blocks.
6. Record all injected fact ids and doc ids in `_dedup_memory`.

**Constraints:** C-06 (caps from config). C-14 (no logic in tools.py — all in engine).

**Test requirements:**
- Unit: orientation facts prepended before query results.
- Unit: conversation-level dedup — calling twice with overlapping entities does not re-inject same facts.
- Unit: unclassified document surfaces in document results even with no matching facts.
- Integration: engine → dual-corpus search → fact assembly → response blocks end-to-end.

**Assumptions for research verification:**
- Confirm that the `build_read_response` method can be safely deleted (no callers after `kms_read` removal).

---

### P9-C-03: Context Engine — Identity Dedup Memory

**Cluster:** C — Context Engine
**Depends on:** none
**Delivers:** P9-MCP-10
**File(s):** `src/mcp_server/context.py`

**What it does:** Changes the dedup memory from content-hash-keyed (`dict[str, str]`) to identity-keyed (`set[int]` for fact row ids, `set[int]` for doc ids). This is simpler and more correct — identity dedup means "don't show the same DB row twice," not "don't show the same text twice."

**Function signatures** (rewrite):
```python
class ContextInjectionEngine:
    def __init__(self) -> None:
        self._seen_fact_ids: set[int] = set()
        self._seen_doc_ids: set[int] = set()

    def is_fact_seen(self, entry_id: int) -> bool:
        """Check if a fact row id has already been injected this conversation."""
        ...

    def record_fact_seen(self, entry_id: int) -> None:
        """Record that a fact was injected."""
        ...

    def is_doc_seen(self, doc_id: int) -> bool:
        """Check if a document id has already been injected this conversation."""
        ...

    def record_doc_seen(self, doc_id: int) -> None:
        """Record that a document was injected."""
        ...
```

**Data flow:** P9-C-01 and P9-C-02 call `record_fact_seen` / `record_doc_seen` after injecting. Before injecting, they call `is_fact_seen` / `is_doc_seen` to skip already-sent items.

**Constraints:** None specific.

**Test requirements:**
- Unit: fresh engine → `is_fact_seen(1)` returns False.
- Unit: after `record_fact_seen(1)` → `is_fact_seen(1)` returns True.
- Unit: fact id 1 and doc id 1 are independent (same number, different namespaces).

**Assumptions for research verification:**
- None — this is a straightforward data structure change.

---

### P9-A-01: `kms_write` Backing + Shim

**Cluster:** A — Tool Surface
**Depends on:** P9-E-01 (container must boot for integration test)
**Delivers:** P9-MCP-06
**File(s):** `src/mcp_server/_write.py` (NEW), `src/mcp_server/tools.py`

**What it does:** New tool that sends consumer-provided content through the cloud capture pipeline. Source type = `chat_session`. Consumer may pass a title hint. Creates a new document + triggers classify. Returns the new document id.

**Function signatures** (new):
```python
# mcp_server/_write.py (NEW)
async def write_from_chat(
    content: str,
    title_hint: str | None = None,
    *,
    db_path: Path | None = None,
) -> Result[int]:
    """Send chat content through the capture pipeline.

    Args:
        content:    The text content to save.
        title_hint: Optional title hint from the consumer AI.
        db_path:    Override DB path.

    Returns:
        Success(new_doc_id) or Failure.
    """
    ...

# tools.py shim
def kms_write(
    content: str,
    title_hint: str | None = None,
    ctx: Context = None,
) -> dict:
    """Save a chat insight to the knowledge system. Returns the new document id."""
    ...
```

**Data flow:**
1. Shim calls `write_from_chat(content, title_hint)`.
2. `write_from_chat` generates a vault_path (`chat/<timestamp>-<slug>.md`), content_hash from content.
3. Calls `await capture_upload(vault_path, extracted_text=content, content_hash=..., title=title_hint)`.
4. If a classify queue is available (via `app.state`), enqueues the new doc id.
5. Returns `Success(doc_id)`.

**Constraints:** C-14 (tools.py logic-free). C-15 (tool added only after backing pipeline tested). C-13 (audit — capture pipeline already audits).

**Test requirements:**
- Unit: `write_from_chat` calls `capture_upload` with `extracted_text=content`.
- Unit: vault_path starts with `chat/` and ends with `.md`.
- Unit: returns `Success(int)` on success.
- Unit: `kms_write` shim delegates correctly.
- Integration: end-to-end content → document row in DB with summary.

**Assumptions for research verification:**
- Confirm `capture_upload` can accept any vault_path (not just paths matching existing vault structure). The `chat/` prefix needs to work without a physical vault directory.
- Confirm whether the classify queue is on `ctx.request_context.lifespan_context` or `request.app.state` — the shim has access to `ctx` but `write_from_chat` may not.

---

### P9-A-02: `kms_correct` Backing + Shim

**Cluster:** A — Tool Surface
**Depends on:** none
**Delivers:** P9-MCP-07, P9-MCP-08
**File(s):** `src/mcp_server/_correct.py` (NEW), `src/mcp_server/tools.py`

**What it does:** New tool that patches an existing `knowledge_entries` row by id. Supported operations: `edit_fact`, `change_tag`, `change_entity`, `promote`, `retire`, `un_retire`. Every mutation is logged to `audit_log` with `pipeline="correct"`.

**Function signatures** (new):
```python
# mcp_server/_correct.py (NEW)
def correct_entry(
    entry_id: int,
    operation: str,
    *,
    new_fact: str | None = None,
    new_tag: str | None = None,
    new_entity: str | None = None,
    reason: str | None = None,
    db_path: Path | None = None,
) -> Result[dict]:
    """Apply a correction to a knowledge entry.

    Args:
        entry_id:   Integer id of the entry to correct.
        operation:  One of: "edit_fact", "change_tag", "change_entity",
                    "promote", "retire", "un_retire".
        new_fact:   Required for "edit_fact".
        new_tag:    Required for "change_tag".
        new_entity: Required for "change_entity".
        reason:     Required for "retire". Optional for others (logged if provided).
        db_path:    Override DB path.

    Returns:
        Success({"entry_id": int, "operation": str, "result": str})
        or Failure(recoverable=False) if entry not found or invalid op.
    """
    ...

# tools.py shim
def kms_correct(
    entry_id: int,
    operation: str,
    new_fact: str | None = None,
    new_tag: str | None = None,
    new_entity: str | None = None,
    reason: str | None = None,
    ctx: Context = None,
) -> dict:
    """Correct an existing knowledge entry. Operations: edit_fact, change_tag,
    change_entity, promote, retire, un_retire."""
    ...
```

**Data flow:**
1. Validate: entry_id exists (read current entry via new `get_entry_by_id` helper). If not → `Failure`.
2. Apply operation:
   - `edit_fact`: update entry's `fact` field via `upsert()` (triggers facts_fts/vec re-sync from P9-B-02).
   - `change_tag`: update entry's `tag` field via `upsert()`.
   - `change_entity`: update entry's `entity` field via `upsert()`.
   - `promote`: set `status="confident"` via `upsert()`.
   - `retire`: call `retire(entry_id, reason)`.
   - `un_retire`: set `status="confident"`, clear `reasoning`, via `upsert()`.
3. Write to `audit_log` via `core.audit.write(...)` with `pipeline="correct"`, `stage=operation`, `source_ids=[entry_id]`.
4. Return success dict.

**Constraints:** C-13 (audit every mutation). C-14 (tools.py logic-free). C-12 (Result returns).

**Test requirements:**
- Unit: each of 6 operations produces correct DB state.
- Unit: nonexistent entry_id → `Failure(recoverable=False)`.
- Unit: `retire` without `reason` → `Failure` (reason required).
- Unit: every operation writes to `audit_log` with `pipeline="correct"`.
- Unit: `edit_fact` triggers facts_fts/vec re-sync (fact text changed in search index).
- Unit: `un_retire` sets status to `confident` and clears reasoning.

**Assumptions for research verification:**
- Confirm `core.audit.write` signature and required fields. Confirm `pipeline` is an accepted field (it may be called `source` or similar).
- Confirm whether a `get_entry_by_id` function needs to be added to `knowledge_entries.py` (currently only `query_by_entity` and `query_by_dimension` exist — no single-row lookup by id).

---

### P9-A-03: `kms_vault_info` Shim Rewrite

**Cluster:** A — Tool Surface
**Depends on:** P9-C-01
**Delivers:** P9-MCP-01
**File(s):** `src/mcp_server/tools.py`

**What it does:** Rewrites the `kms_vault_info` shim to call the new engine API. Removes all references to `ProjectRegistry`.

**Function signatures** (rewrite):
```python
def kms_vault_info(ctx: Context) -> list[dict]:
    """Discover what the knowledge system knows: entities, dimensions, and orientation facts."""
    return (
        ctx.request_context.lifespan_context["engine"]
        .build_vault_info_response()
        .unwrap()
    )
```

**Constraints:** C-14 (logic-free).

**Test requirements:**
- Unit: shim delegates to `build_vault_info_response()` with no arguments.

**Assumptions for research verification:**
- None — the shim signature barely changes, just the engine method it calls.

---

### P9-A-04: `kms_search` Shim Rewrite

**Cluster:** A — Tool Surface
**Depends on:** P9-C-02
**Delivers:** P9-MCP-02, P9-MCP-03
**File(s):** `src/mcp_server/tools.py`

**What it does:** Rewrites the `kms_search` shim to call the new engine API. Removes `include_context` param (concentration gating dropped). The engine always injects orientation facts.

**Function signatures** (rewrite):
```python
def kms_search(
    query: str,
    project: str | None = None,
    since: str | None = None,
    until: str | None = None,
    location: str | None = None,
    max_results: int | None = None,
    ctx: Context = None,
) -> list[dict]:
    """Search the knowledge system. Returns orientation facts + query facts + document summaries."""
    return (
        ctx.request_context.lifespan_context["engine"]
        .build_search_response(
            query=query,
            project=project,
            since=since,
            until=until,
            location=location,
            max_results=max_results,
        )
        .unwrap()
    )
```

**Constraints:** C-14 (logic-free).

**Test requirements:**
- Unit: shim delegates to `build_search_response()` with correct params.
- Unit: `include_context` parameter is gone from the tool signature.

**Assumptions for research verification:**
- None.

---

### P9-A-05: Remove `kms_read` + `kms_move` + `_move.py`

**Cluster:** A — Tool Surface
**Depends on:** none
**Delivers:** P9-MCP-14
**File(s):** `src/mcp_server/tools.py`, `src/mcp_server/_move.py` (DELETE)

**What it does:** Deletes the `kms_read` and `kms_move` tool functions from `tools.py`, removes their registration from `register_tools`, and deletes `_move.py` entirely. `kms_read` functionality is covered by `kms_search` (summaries) and `kms_inspect` (drill-down). `kms_move` is removed because the system never moves files in cloud-native mode.

**Changes:**
- Delete `kms_read` function and its registration.
- Delete `kms_move` function and its registration.
- Delete `src/mcp_server/_move.py` file.
- Remove `from mcp_server._move import move` import.
- `register_tools` registers exactly 5 tools: `kms_vault_info`, `kms_search`, `kms_inspect`, `kms_write`, `kms_correct`.

**Constraints:** None specific.

**Test requirements:**
- Verify `_move.py` does not exist on disk after change.
- Verify `register_tools` registers exactly 5 tools (not 3, not 7).
- Verify no import errors from removed modules.

**Assumptions for research verification:**
- Confirm there are no other callers of `move()` from `_move.py` outside of `tools.py`.
- Confirm `kms_read` is not called from `AI_INSTRUCTIONS.md` examples that might confuse testing.

---

### P9-F-01: `_should_overwrite` Seam in classify_writer

**Cluster:** F — Phase 10 Seam
**Depends on:** none
**Delivers:** P9-MCP-15
**File(s):** `src/pipelines/classify_writer.py`

**What it does:** Adds an explicit `_should_overwrite(existing_entry: KnowledgeEntry) -> bool` decision point in the update path of `write_entries`. In Phase 9 it always returns `True` (current behavior preserved). Phase 10 slots the trust guard here: `trust_score > 0.5` means do NOT overwrite.

**Function signatures** (new):
```python
# classify_writer.py
def _should_overwrite(existing_entry: KnowledgeEntry) -> bool:
    """Decision point for whether classify may overwrite an existing entry.

    Phase 9: always True (current behavior).
    Phase 10: trust_score > 0.5 → False (write conflicting new entry instead).
    """
    return True
```

**Data flow:** In the update branch of `write_entries` (where a twin is found via `_find_twin`), call `_should_overwrite(existing)` before updating. If False, write a new `pending` entry instead (Phase 10 behavior — not wired in Phase 9).

**Constraints:** None specific. Purely structural — no behavioral change.

**Test requirements:**
- Unit: `_should_overwrite` exists and is callable.
- Unit: `_should_overwrite(any_entry)` returns `True`.
- Structural: the function is called in the update path of `write_entries` (grep or AST check).

**Assumptions for research verification:**
- Confirm the exact location in `write_entries` where the twin-update happens (the `_find_twin` call site) to insert the guard correctly.

---

### P9-G-01: `AI_INSTRUCTIONS.md` Rewrite

**Cluster:** G — Documentation
**Depends on:** P9-A-01 through P9-A-05
**Delivers:** P9-MCP-19
**File(s):** `src/mcp_server/AI_INSTRUCTIONS.md`

**What it does:** Rewrites the consumer AI's operating manual to document the new 5-tool surface. Key sections:

1. **Tool inventory:** `kms_vault_info`, `kms_search`, `kms_inspect`, `kms_write`, `kms_correct`.
2. **Discovery workflow:** vault_info → search → inspect (replaces old discover → search → read → inspect → move).
3. **Facts vs summaries model:** Facts = targeted extracted insights with entity/dimension/tag. Summaries = general 5-section digests of source documents.
4. **Correct vs write routing:** `kms_correct` = fix existing fact by id (confirm-first behavioral stance). `kms_write` = add new insight from conversation (proactive + transparent — save clearly-valuable insights without blocking question, tell user it saved).
5. **Inspect modes:** summary (default, lightweight) → text (full body, opt-in) → file (laptop path, tier 3 requires laptop open).
6. **Reference model:** Integer document ids (stable across renames), not vault paths.
7. **Binary note caveat:** `kms_inspect text` on a binary returns the vision description, not raw bytes.

**Constraints:** None code-level.

**Test requirements:**
- Manual review: all 5 tools documented, all removed tools absent, correct-vs-write routing clear, behavioral stance documented.

**Assumptions for research verification:**
- None — documentation only.

---

### P9-G-02: AgentBase Deployment Guide

**Cluster:** G — Documentation
**Depends on:** P9-E-01
**Delivers:** —
**File(s):** `docs/deployment/agentbase_guide.md` (NEW)

**What it does:** Two-part non-technical guide for deploying and testing the knowledge system on AgentBase:

1. **Builder part:** Stand up deployment, drop in IAM/API/daemon keys, configure gateway auth, verify container health (`/health` endpoint).
2. **Tester part:** Connect Claude Desktop to the gateway endpoint, run the daemon on laptop, verify the 5 tools work.

Single-tenant per deployment. No secrets embedded in the guide.

**Constraints:** None code-level. Guide must not embed secrets.

**Test requirements:**
- Manual: guide enables a non-technical person to stand up an instance and connect Claude Desktop.

**Assumptions for research verification:**
- Confirm AgentBase Resource Gateway auth mechanism (IAM specifics may need input from platform team).

---

## Migration Spec

**One new migration file: `src/storage/migrations/012_fact_search_index.sql`**

```sql
-- Migration 012: Fact search index for Phase 9

-- FTS5 keyword index on knowledge_entries fact + entity text.
-- External content mode avoids data duplication — Python-side sync in
-- knowledge_entries.upsert() and retire() keeps this in sync.
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    entry_id UNINDEXED,
    entity,
    fact,
    content='knowledge_entries',
    content_rowid='id'
);

-- vec0 embedding index on fact text.
-- Dimension 384 matches all-MiniLM-L6-v2 output.
CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(
    entry_id INTEGER PRIMARY KEY,
    embedding FLOAT[384]
);

-- Bump schema version
UPDATE schema_version SET version = 12;
```

**Cascade requirement:** Bump version-pin assertions in `tests/test_storage/test_migration_011.py` from `11` to `12`.

**No column rename for `retrieval_count`.** The SQL column stays `retrieval_count`. Python code (`KnowledgeEntry` dataclass) renames the field to `retrieval_score: float = 0.0` and reads the column value as float. SQLite type flexibility means the existing INT column accepts REAL values.

---

## Config Changes

New config keys with defaults, organized by section. All go in `config/config.yaml` under the `mcp:` section.

```yaml
mcp:
  # --- Context injection (existing section, modified) ---
  context_injection:
    # REMOVED: frequency_threshold (concentration gating dropped)
    # REMOVED: max_context_files (replaced by per-dimension caps)
    # REMOVED: include_context_yaml (no more CLAUDE.md/context.yaml reads)
    max_entities_per_dimension: 15       # entity map cap for kms_vault_info
    max_orientation_facts_per_dimension: 5  # orientation bullet cap per dimension

  # --- Inspect (new section) ---
  inspect:
    max_text_refs: 5                     # max ids that may use text mode per call

  # --- Retrieval score (new section) ---
  retrieval_score:
    decay_factor: 0.95                   # multiplier per injection and per sweep
    sweep_interval_hours: 24             # background sweep frequency

  # --- Fact search (new section) ---
  fact_search:
    keyword_weight: 0.5                  # blend weight: 0.0 = all semantic, 1.0 = all keyword
    max_results: 20                      # default max fact results per search
```

**Pydantic model additions:** New config classes in `core/config.py`:
- `InspectConfig(max_text_refs: int = 5)`
- `RetrievalScoreConfig(decay_factor: float = 0.95, sweep_interval_hours: int = 24)`
- `FactSearchConfig(keyword_weight: float = 0.5, max_results: int = 20)`
- Modified `ContextInjectionConfig`: remove `frequency_threshold`, `max_context_files`, `include_context_yaml`; add `max_entities_per_dimension: int = 15`, `max_orientation_facts_per_dimension: int = 5`.

---

## Out of Scope

These items are explicitly NOT in Phase 9. They belong to Phase 10 or later.

| Item | Why deferred |
|---|---|
| `adjust_trust()` function + trust_score movement | Phase 10 — all entries start at trust_score 0.5 in Phase 9 |
| Classify overwrite guard (`trust_score > 0.5`) | Phase 10 — `_should_overwrite` seam is placed in Phase 9 but always returns True |
| Pending requests system (table + tools + housekeeping) | Phase 10 — entire system deferred |
| Corrections table reshape | Phase 10 — exists but inert and wrong-shaped |
| Few-shot injector (recent corrections → extraction prompts) | Phase 10 |
| `min_trust` filtering | Phase 10 — no effect until trust_scores diverge from 0.5 |
| Volatility flag (entries with >3 corrections) | Phase 10 |
| Web UI (conflict queue, comment feature, parked doc dashboard) | Phase 10+ |
| Multi-tenancy | Architectural decision: separate deployments |
| Content-level document dedup for `kms_write` | Fact-level dedup in classify catches duplicate facts |
| `retrieval_count` SQL column rename | Python code reinterprets; no migration for a rename |
| Global token budget for context injection | Two config knobs (entity cap + orientation cap) suffice |

---

## Assumptions for Research Verification

These are things the spec assumes but that must be verified against actual code during the research step:

1. **FTS5 external content mode sync syntax.** Verify whether DELETE from `facts_fts` requires `INSERT INTO facts_fts(facts_fts, entry_id, entity, fact) VALUES ('delete', ?, ?, ?)` or if standard `DELETE FROM facts_fts WHERE entry_id = ?` works. The answer determines the Python sync code in P9-B-02.

2. **`facts_vec` MATCH+k syntax.** Verify `facts_vec` (vec0) supports the same `WHERE embedding MATCH ? AND k = ?` syntax as `embeddings_vec`. Same sqlite-vec version constraint applies (ADR-0009).

3. **Short-fact embedding separation (research gate).** Embed 50+ real short facts (from `knowledge_entries.fact` — one-liners like "Anthony leads the Alpha project"), measure nearest-neighbor separation vs distractor facts. If separation is poor, increase `keyword_weight` default above 0.5. This is a config-driven fallback, not a blocking risk.

4. **`retrieval_count` column accepts REAL values.** Verify that `UPDATE knowledge_entries SET retrieval_count = retrieval_count * 0.95 + 1.0` stores a float correctly in the INT-typed column. SQLite type affinity should handle this, but needs a concrete test.

5. **`_get_model()` import path for C-17 compliance.** Verify that importing `retrieval.embeddings._get_model` inside `knowledge_entries.upsert()` does not trigger CONFIG validation at module scope. May need a lazy import pattern.

6. **`capture_upload` accepts arbitrary vault_path.** Verify that `capture_upload(vault_path="chat/2026-06-15-insight.md", ...)` works without needing a physical vault directory at that path. The function writes to the DB, not to disk.

7. **`core.audit.write` signature.** Verify exact parameter names: is it `pipeline=` or `source=`? Is `stage=` supported? Is `source_ids=` a list of ints or strings? The `kms_correct` audit logging depends on this.

8. **`get_entry_by_id` existence.** Verify whether `knowledge_entries.py` has a single-row lookup by id. If not (it currently only has `query_by_dimension` and `query_by_entity`), P9-A-02 must add one.

9. **Reranker `_build_card` function.** Verify whether the reranker in `reranker.py` has its own card-building function that also needs the `id` field (P9-B-04). The `_card_from_row` in `search.py` is one path; the reranker's `rerank()` function may build cards via a different path.

10. **`build_read_response` callers.** Verify that `build_read_response` is only called from `kms_read` in `tools.py`. If so, it can be safely deleted when `kms_read` is removed (P9-A-05).

11. **`scripts/start.sh` content.** Verify the exact command in `start.sh` to confirm it invokes `cloud_entry.py` via `__main__` (P9-E-01).

12. **Classify queue wiring.** Verify whether `kms_write` (running inside an MCP tool call) has access to `app.state.classify_queue`. The MCP lifespan context may be different from the Starlette app state. Need to trace the access path.

13. **`DocumentRow.summary` nullability.** Verify that `summary` can be NULL for documents that have gone through `upsert_from_upload` but not yet `attach_summary`. The three-tier resolve `summary` mode must handle this gracefully (P9-D-01).

14. **Existing `query_ranked_by_dimension` ORDER BY.** The current function uses `ORDER BY trust_score DESC, confidence DESC, updated_at DESC` (3-key). Phase 9 needs 4-key: `trust_score DESC, retrieval_score DESC, confidence DESC, updated_at DESC`. Determine whether to modify the existing function or add a new `query_ranked_for_orientation`.

---

## Phase Boundary Recommendations

The build order follows the design recommendation: **E → B → D → C → A → F → G**. Suggested phase boundaries for implementation:

### Implementation Phase 1: Bug Fixes (E)
**Components:** P9-E-01, P9-E-02, P9-E-03, P9-E-04
**Rationale:** Container must boot before anything else is testable. These are localized fixes with no cross-dependencies. Start here.
**Exit criteria:** Container boots. Async operations do not block. API key read once. All 4 bug fix tests pass.

### Implementation Phase 2: Retrieval Foundation (B)
**Components:** P9-B-01, P9-B-02, P9-B-03, P9-B-04, P9-B-05, P9-B-06
**Rationale:** Everything else depends on the fact search index, `get_by_id`, and dual-corpus search. Build the data layer first. Includes the embedded research spike on fact embedding separation.
**Exit criteria:** Migration 012 applied. Facts searchable by keyword and embedding. `get_by_id` works. `SearchResult.id` populated. Retrieval score increment + sweep works. Dual-corpus search returns merged, deduped results. Research spike completed with keyword_weight recommendation.

### Implementation Phase 3: Three-Tier Resolve (D)
**Components:** P9-D-01, P9-D-02
**Rationale:** Depends on `get_by_id` from Phase 2. Small, self-contained rewrite.
**Exit criteria:** All three resolve modes work from DB. NULL full_body degradation works. Batched ids work. Text cap enforced. `kms_inspect` shim delegates correctly.

### Implementation Phase 4: Context Engine (C)
**Components:** P9-C-01, P9-C-02, P9-C-03
**Rationale:** Depends on dual-corpus search (Phase 2) and retrieval score (Phase 2). This is the heaviest rewrite — entire class replaced.
**Exit criteria:** `build_vault_info_response` returns entity map + orientation facts from DB, zero disk reads. `build_search_response` returns orientation + query facts + document results. Identity dedup works across conversation. Integration test passes end-to-end.

### Implementation Phase 5: Tool Surface + Cleanup (A)
**Components:** P9-A-01, P9-A-02, P9-A-03, P9-A-04, P9-A-05
**Rationale:** Depends on everything above. Tools are the consumer-facing layer.
**Exit criteria:** 5 tools registered. `kms_write` creates documents. `kms_correct` patches entries with audit. `kms_read` and `kms_move` gone. `_move.py` deleted.

### Implementation Phase 6: Seam + Docs (F, G)
**Components:** P9-F-01, P9-G-01, P9-G-02
**Rationale:** Purely additive. Seam is trivial. Docs come last because they describe the final state.
**Exit criteria:** `_should_overwrite` exists and is called. `AI_INSTRUCTIONS.md` documents 5-tool surface. Deployment guide reviewed.

---

## Behavior Inventory Coverage Matrix

Every behavior inventory entry maps to at least one component:

| Behavior ID | Component(s) |
|---|---|
| P9-MCP-01 | P9-C-01, P9-A-03 |
| P9-MCP-02 | P9-B-06, P9-C-02, P9-A-04 |
| P9-MCP-03 | P9-B-06, P9-A-04 |
| P9-MCP-04 | P9-D-02 |
| P9-MCP-05 | P9-D-02 |
| P9-MCP-06 | P9-A-01 |
| P9-MCP-07 | P9-A-02 |
| P9-MCP-08 | P9-A-02 |
| P9-MCP-09 | P9-C-01, P9-C-02 |
| P9-MCP-10 | P9-C-01, P9-C-02, P9-C-03 |
| P9-MCP-11 | P9-B-01, P9-B-02 |
| P9-MCP-12 | P9-B-05 |
| P9-MCP-13 | P9-D-01 |
| P9-MCP-14 | P9-A-05 |
| P9-MCP-15 | P9-F-01 |
| P9-MCP-16 | P9-E-01 |
| P9-MCP-17 | P9-E-02 |
| P9-MCP-18 | P9-E-03, P9-E-04 |
| P9-MCP-19 | P9-G-01 |
| P9-MCP-20 | P9-B-03 |
| P9-MCP-21 | P9-B-04 |
