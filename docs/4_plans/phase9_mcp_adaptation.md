# Phase 9 — MCP Adaptation: Implementation Plan

_Created: 2026-06-15_
_Input: Spec + Research corrections (4 items incorporated)_
_Build order: E -> B -> D -> C -> A -> F -> G (bottom-up, per design recommendation)_

---

## Architecture Overview

Phase 9 rewires the MCP server from a vault-disk reader to a cloud-database consumer. The five MCP tools switch from reading files on the user's laptop to querying two searchable corpora in the cloud DB: distilled knowledge facts (`knowledge_entries`) and document summaries (`documents`). Two new tools (`kms_write`, `kms_correct`) let the consumer AI persist insights and fix mistakes. The context injection engine is fully rewritten to assemble orientation fact bullets from DB rows ranked by a 4-key sort (trust, retrieval, confidence, recency), with zero disk reads and zero CLAUDE.md.

---

## Research Corrections Applied

| Correction | What changed from spec | Where in plan |
|---|---|---|
| **A7 (INVALIDATED)** — `audit.write` takes `AIDecision` object, not raw fields | P9-A-02 must construct `AIDecision(action=f"correct:{op}", confidence=1.0, reasoning=..., source_ids=[str(entry_id)])` then call `audit.write(decision, pipeline="correct", stage=op, outcome="APPLIED")` | Phase 5, P9-A-02 step 3 |
| **A1 (PARTIAL)** — FTS5 external content sync requires special syntax | Delete: `INSERT INTO facts_fts(facts_fts, entry_id, entity, fact) VALUES('delete', ?, ?, ?)`. Insert: `INSERT INTO facts_fts(rowid, entry_id, entity, fact) VALUES(?, ?, ?, ?)`. NOT plain DELETE/INSERT. | Phase 2, P9-B-02 all sync steps |
| **A9 (PARTIAL)** — Two `SearchResult` builders need `id` field | Add `id` field to BOTH `_card_from_row` in `search.py:29` AND the inline `SearchResult(...)` in `reranker.py:163-170` | Phase 2, P9-B-04 |
| **A12 (PARTIAL)** — Classify queue not on MCP lifespan context | Inject `classify_queue` into the dict that inner FastMCP lifespan yields, inside the composed lifespan in `cloud_entry.py` | Phase 5, P9-A-01 step wiring note |

---

## Implementation Phases

### Phase 1: Bug Fixes (Cluster E)

_Components: P9-E-01 through P9-E-04_
_Exit criteria: Container boots via `python -m mcp_server.cloud_entry`. Async blob/delete ops non-blocking. API key read once at startup._

#### P9-E-01: Restore Container Boot

**File:** `src/mcp_server/cloud_entry.py`
**Behavior IDs:** P9-MCP-16

**What to do:**
1. Add the `if __name__ == "__main__":` block at the very bottom of `cloud_entry.py` (after line 182). Content:
   ```python
   if __name__ == "__main__":
       import uvicorn
       uvicorn.run(build_app(), host="0.0.0.0", port=8080)
   ```
2. The file's docstring (line 6) already references this block — the code was simply lost in commit `1b1f33d`.

**Test:**
- Source inspection test: read `cloud_entry.py` source text, assert `if __name__ == "__main__":` is present.
- Optional smoke test: `subprocess.run(["python", "-c", "import mcp_server.cloud_entry"])` succeeds without error.

---

#### P9-E-02: API Key Read-Once

**File:** `src/mcp_server/api.py`
**Behavior IDs:** P9-MCP-17

**What to do:**
1. Add a module-level cached variable after the `_blob_store` line (around line 63):
   ```python
   _daemon_api_key: str | None = os.environ.get("KMS_DAEMON_API_KEY")
   ```
2. In `require_key()` (line 71), replace `expected = os.environ.get("KMS_DAEMON_API_KEY")` (line 86) with `expected = _daemon_api_key`.

**Test:**
- Unit: Set `_daemon_api_key = "test-key"`, call `require_key` with matching bearer token, verify returns key.
- Unit: After `_daemon_api_key` is set, mutate `os.environ["KMS_DAEMON_API_KEY"]` to a different value, verify `require_key` still uses the cached value (proves read-once).

---

#### P9-E-03: Async Blob Put

**File:** `src/pipelines/capture.py`
**Behavior IDs:** P9-MCP-18

**What to do:**
1. In `_capture_binary()`, line 360, replace `put_result = blob_store.put(content_hash, raw_bytes, mime_type)` with `put_result = await blob_store.async_put(content_hash, raw_bytes, mime_type)`. The `async_put` method exists at `storage/blobs.py:316`.

**Test:**
- Async unit test: mock `blob_store.async_put` as an async mock, verify it is awaited. Verify `blob_store.put` (sync) is NOT called.

---

#### P9-E-04: Async Delete Cleanup

**File:** `src/mcp_server/api.py`
**Behavior IDs:** P9-MCP-18

**What to do:**
1. Add `import asyncio` at the top of `api.py` (was removed as unused in M10 fix).
2. In `event_handler()`, the deleted branch (line 524) currently calls `_delete_with_blob_cleanup(...)` synchronously. Wrap it:
   ```python
   result = await asyncio.to_thread(
       _delete_with_blob_cleanup,
       vault_path=path,
       db_path=_db_path,
       blob_store=_blob_store,
   )
   ```

**Test:**
- Async unit test: mock `_delete_with_blob_cleanup`, verify it runs via `asyncio.to_thread` (does not block the event loop).

---

### Phase 2: Retrieval Foundation (Cluster B)

_Components: P9-B-01 through P9-B-06_
_Exit criteria: Migration 012 applied. Facts searchable by keyword and embedding. `get_by_id` works. `SearchResult.id` populated in both card-building paths. Retrieval score increment + sweep works. Dual-corpus search returns merged, deduped results._

#### P9-B-01: Fact Search Migration (012)

**File:** `src/storage/migrations/012_fact_search_index.sql` (NEW)
**Behavior IDs:** P9-MCP-11

**What to do:**
1. Create the migration file with the following SQL:
   ```sql
   -- Migration 012: Fact search index for Phase 9
   CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
       entry_id UNINDEXED,
       entity,
       fact,
       content='knowledge_entries',
       content_rowid='id'
   );
   CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(
       entry_id INTEGER PRIMARY KEY,
       embedding FLOAT[384]
   );
   UPDATE schema_version SET version = 12;
   ```
2. Bump version-pin assertions in `tests/test_storage/test_migration_011.py` from `11` to `12` (standard cascade per CLAUDE.md migration gotcha).

**Test:**
- Migration test: after `init_db`, `facts_fts` and `facts_vec` tables exist (query `sqlite_master`).
- Schema version test: `SELECT version FROM schema_version` returns 12.

---

#### P9-B-02: Fact FTS + Embedding Sync in Upsert/Retire

**File:** `src/storage/knowledge_entries.py`
**Behavior IDs:** P9-MCP-11

**What to do — RESEARCH CORRECTION A1 APPLIED: use external content FTS5 special syntax.**

1. **In `upsert()` INSERT path** (after the INSERT into `knowledge_entries`, line 118, before `return Success(cursor.lastrowid)`):
   - Compute embedding: lazy-import `_get_model` from `retrieval.embeddings` inside the function body, encode the `entry.fact` text to get a 384-dim vector.
   - INSERT into `facts_fts` using external content syntax:
     ```python
     conn.execute(
         "INSERT INTO facts_fts(rowid, entry_id, entity, fact) VALUES(?, ?, ?, ?)",
         (cursor.lastrowid, cursor.lastrowid, entry.entity, entry.fact),
     )
     ```
   - INSERT into `facts_vec`:
     ```python
     conn.execute(
         "INSERT INTO facts_vec(entry_id, embedding) VALUES(?, ?)",
         (cursor.lastrowid, embedding_blob),
     )
     ```

2. **In `upsert()` UPDATE path** (after the UPDATE, line 100, before `return Success(entry.id)`):
   - Read old entity+fact BEFORE updating (or capture from the entry object before SQL). Delete old rows from both search tables using external content delete syntax:
     ```python
     # Delete old FTS entry (external content special syntax)
     conn.execute(
         "INSERT INTO facts_fts(facts_fts, entry_id, entity, fact) VALUES('delete', ?, ?, ?)",
         (entry.id, old_entity, old_fact),
     )
     conn.execute("DELETE FROM facts_vec WHERE entry_id = ?", (entry.id,))
     ```
   - Then re-insert with new values (same as INSERT path but using `entry.id` instead of `cursor.lastrowid`).
   - Note: to get old_entity/old_fact for the delete command, do a SELECT before the UPDATE, or restructure to capture old values. A SELECT before UPDATE is simplest.

3. **In `retire()`** (after the UPDATE, line 169, before `return Success(cursor.rowcount)`):
   - Read old entity+fact before retiring (SELECT by entry_id).
   - Delete from both search tables:
     ```python
     conn.execute(
         "INSERT INTO facts_fts(facts_fts, entry_id, entity, fact) VALUES('delete', ?, ?, ?)",
         (entry_id, old_entity, old_fact),
     )
     conn.execute("DELETE FROM facts_vec WHERE entry_id = ?", (entry_id,))
     ```

4. **Embedding helper:** Create a private function `_embed_fact(fact_text: str) -> bytes` that lazy-imports `_get_model` from `retrieval.embeddings`, calls `.encode(fact_text)`, and returns the numpy array serialized as bytes (same pattern as `embeddings.py:index_embedding`).

**Test:**
- Unit: after `upsert(new_entry)`, query `facts_fts` for the entity+fact text via `SELECT * FROM facts_fts WHERE facts_fts MATCH ?` — returns the entry.
- Unit: after `upsert(existing_entry_with_changed_fact)`, old fact text gone from `facts_fts`, new text present.
- Unit: after `retire(entry_id)`, `facts_fts` and `facts_vec` rows for that id are gone.
- Unit: embedding inserted into `facts_vec` has correct dimension (384 floats).

---

#### P9-B-03: `get_by_id` on Documents

**File:** `src/storage/documents.py`
**Behavior IDs:** P9-MCP-20

**What to do:**
1. Add a new function after `get_by_path` (which is the closest analog):
   ```python
   def get_by_id(
       doc_id: int, db_path: Path | None = None
   ) -> Result[DocumentRow | None]:
       """Fetch the documents row for a given integer id."""
       try:
           with get_connection(db_path, readonly=True) as conn:
               conn.row_factory = sqlite3.Row
               row = conn.execute(
                   "SELECT * FROM documents WHERE id = ?", (doc_id,)
               ).fetchone()
               if row is None:
                   return Success(None)
               return Success(_row_from_sqlite(row))
       except sqlite3.Error as exc:
           return Failure(str(exc), recoverable=False, context={"doc_id": doc_id})
   ```

**Test:**
- Unit: existing row -> `Success(DocumentRow)` with correct fields including `id`.
- Unit: nonexistent id -> `Success(None)`.
- Unit: DB error (closed connection mock) -> `Failure(recoverable=False)`.

---

#### P9-B-04: Expose `id` on Search Results

**Files:** `src/retrieval/reranker.py`, `src/retrieval/search.py`
**Behavior IDs:** P9-MCP-21

**What to do — RESEARCH CORRECTION A9 APPLIED: update BOTH card-building paths.**

1. **`reranker.py:35`** — Add `id: int | None = None` to the `SearchResult` dataclass:
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

2. **`reranker.py:163-170`** — In the `rerank()` function's card builder, add `id=row.id`:
   ```python
   results.append(
       SearchResult(
           vault_path=candidate.vault_path,
           summary=row.summary,
           snippet=candidate.snippet,
           score=score,
           metadata=metadata,
           id=row.id,  # NEW
       )
   )
   ```

3. **`search.py:29`** — In `_card_from_row`, add `id=row.id`:
   ```python
   return SearchResult(
       vault_path=row.vault_path,
       summary=row.summary,
       snippet=snippet,
       score=score,
       metadata={...},
       id=row.id,  # NEW
   )
   ```

**Test:**
- Unit: `_card_from_row` with a `DocumentRow` that has `id=42` -> `SearchResult.id == 42`.
- Unit: `rerank()` output cards have `id` populated from the document row.
- Unit: `search()` results from filter-only branch also have `id` populated.

---

#### P9-B-05: `retrieval_score` Increment + Sweep

**Files:** `src/storage/knowledge_entries.py`, `src/mcp_server/cloud_entry.py`
**Behavior IDs:** P9-MCP-12

**What to do:**

1. **Python rename in `KnowledgeEntry` dataclass** (line 32): Change `retrieval_count: int = 0` to `retrieval_score: float = 0.0`. Update `_row_to_entry` (line 50) to read as float: `retrieval_score=float(row["retrieval_count"]) if "retrieval_count" in row.keys() else 0.0`.

2. **New function `bump_retrieval_score`** in `knowledge_entries.py`:
   ```python
   def bump_retrieval_score(
       entry_id: int,
       *,
       decay_factor: float = 0.95,
       db_path: Path | None = None,
   ) -> Result[int]:
       """Increment retrieval_score for one entry.
       Formula: retrieval_count = retrieval_count * decay_factor + 1.0"""
       try:
           with get_connection(db_path) as conn:
               cursor = conn.execute(
                   "UPDATE knowledge_entries SET retrieval_count = retrieval_count * ? + 1.0 WHERE id = ?",
                   (decay_factor, entry_id),
               )
               return Success(cursor.rowcount)
       except sqlite3.Error as exc:
           return Failure(str(exc), recoverable=False, context={"entry_id": entry_id})
   ```

3. **New function `sweep_retrieval_scores`** in `knowledge_entries.py`:
   ```python
   def sweep_retrieval_scores(
       *,
       decay_factor: float = 0.95,
       db_path: Path | None = None,
   ) -> Result[int]:
       """Decay all retrieval scores. Returns rows affected."""
       try:
           with get_connection(db_path) as conn:
               cursor = conn.execute(
                   "UPDATE knowledge_entries SET retrieval_count = retrieval_count * ?",
                   (decay_factor,),
               )
               return Success(cursor.rowcount)
       except sqlite3.Error as exc:
           return Failure(str(exc), recoverable=False)
   ```

4. **Background sweep task** in `cloud_entry.py` `_wrap_lifespan`: Add a second background task `_retrieval_sweep_worker` that sleeps for `sweep_interval_hours` (from config) then calls `sweep_retrieval_scores`. Start it alongside the classify worker, cancel it in the `finally` block.

**Test:**
- Unit: `bump_retrieval_score` on entry with score 0.0 -> score becomes 1.0.
- Unit: `bump_retrieval_score` on entry with score 5.0 (decay=0.95) -> score becomes 5.75.
- Unit: `sweep_retrieval_scores(decay_factor=0.95)` decays all entries by 0.95.
- Integration: background task starts in composed lifespan (mock the sleep to avoid delay).

---

#### P9-B-06: Dual-Corpus Search + Identity Dedup

**Files:** `src/retrieval/fact_search.py` (NEW), `src/retrieval/search.py` (modified)
**Behavior IDs:** P9-MCP-02, P9-MCP-03

**What to do:**

1. **Create `src/retrieval/fact_search.py`** with:
   - `FactResult` dataclass: `entry_id`, `dimension`, `entity`, `fact`, `confidence`, `trust_score`, `retrieval_score`, `sources`, `score`.
   - `search_facts(query, *, max_results=20, keyword_weight=0.5, db_path=None) -> Result[list[FactResult]]`:
     - FTS5 keyword search: `SELECT entry_id, rank FROM facts_fts WHERE facts_fts MATCH ?`.
     - Semantic search: embed query via `_get_model().encode(query)`, query `facts_vec` with `WHERE embedding MATCH ? AND k = ?`.
     - RRF fusion of keyword + semantic scores (same pattern as `ranker.py`), weighted by `keyword_weight`.
     - Join results back to `knowledge_entries` for full row data.
     - Return ranked `FactResult` list.

2. **Add `search_dual()` to `search.py`** (or as a new coordinator):
   - `DualCorpusResult` dataclass: `facts: list[FactResult]`, `documents: list[SearchResult]`.
   - Calls `search_facts(query)` for fact results.
   - Calls existing `search(query, ...)` for document results.
   - Identity dedup within each list (no duplicate `entry_id` in facts, no duplicate `id` in docs).
   - Returns `DualCorpusResult`.

3. **Research spike (embedded):** During implementation, embed 50+ real short facts, measure nearest-neighbor separation. If separation is poor, increase `keyword_weight` default above 0.5 in config. Document the finding.

**Test:**
- Unit: `search_facts` returns facts matching keyword query.
- Unit: `search_facts` returns facts matching semantic query.
- Unit: `search_dual` merges fact + doc results into `DualCorpusResult`.
- Unit: unclassified document (no facts yet) surfaces via document search leg.
- Unit: identity dedup — same fact id appearing from both keyword and semantic is collapsed to one.

---

### Phase 3: Three-Tier Resolve (Cluster D)

_Components: P9-D-01, P9-D-02_
_Exit criteria: All three resolve modes work from DB. NULL full_body degrades to summary. Batched ids work. Text cap enforced. `kms_inspect` shim delegates correctly._

#### P9-D-01: Three-Tier Resolve Rewrite

**File:** `src/mcp_server/_resolve.py` (complete rewrite)
**Behavior IDs:** P9-MCP-13

**What to do:**

1. **Delete all current content** of `_resolve.py` (60 lines of disk-based binary resolver).

2. **Replace with DB-first three-tier resolver:**
   ```python
   @dataclass(frozen=True)
   class ResolveResult:
       doc_id: int
       mode: str          # "summary" | "text" | "file"
       content: str
       title: str
       degraded: bool     # True if text mode fell back to summary

   def resolve(
       doc_ids: list[int],
       mode: str = "summary",
       *,
       max_text_refs: int = 5,
       db_path: Path | None = None,
   ) -> Result[list[ResolveResult]]:
   ```

3. **Implementation logic:**
   - For each `doc_id`, call `documents.get_by_id(doc_id, db_path=db_path)`.
   - If `Success(None)` — skip (missing doc, not an error).
   - If `Failure` — return the failure.
   - For `mode="summary"`: use `row.summary or "[Summary pending]"`. `degraded=False`.
   - For `mode="text"`: if index < `max_text_refs` and `row.full_body` is not None, use `row.full_body`. If `full_body` is None, fall back to `row.summary or "[Summary pending]"` with `degraded=True`. Beyond `max_text_refs`, degrade to summary.
   - For `mode="file"`: use `row.vault_path`. `degraded=False`.

4. **Remove old imports** (`HandlerRegistry`, `read_note`, `core.config`). Add new imports (`documents.get_by_id`, `dataclasses.dataclass`).

**Test:**
- Unit: `resolve([42], "summary")` with existing row -> returns summary text.
- Unit: `resolve([42], "text")` with `full_body` present -> returns full body.
- Unit: `resolve([42], "text")` with NULL `full_body` -> degrades to summary, `degraded=True`.
- Unit: `resolve([42], "file")` -> returns vault_path.
- Unit: `resolve([999], "summary")` with nonexistent id -> empty list.
- Unit: `resolve([1,2,...,6], "text", max_text_refs=5)` -> first 5 get text, 6th gets summary.
- Unit: `resolve([], "summary")` -> empty list.

---

#### P9-D-02: `kms_inspect` Shim Rewrite

**File:** `src/mcp_server/tools.py`
**Behavior IDs:** P9-MCP-04, P9-MCP-05

**What to do:**

1. Replace the current `kms_inspect` function (line 67-69) with:
   ```python
   def kms_inspect(
       doc_ids: list[int],
       mode: str = "summary",
       ctx: Context = None,
   ) -> list[dict]:
       """Drill into documents by integer id. Mode: summary, text, file."""
       return [
           {"doc_id": r.doc_id, "mode": r.mode, "content": r.content,
            "title": r.title, "degraded": r.degraded}
           for r in resolve(doc_ids, mode).unwrap()
       ]
   ```
   Note: The list comprehension is a data transform, not control-flow logic, so it does not violate C-14. However, if the hook is strict about `for`, wrap the `resolve` call and `.unwrap()` result in a helper function in `_resolve.py` that returns `list[dict]` directly, and have the shim call that.

2. Update the import at the top: replace `from mcp_server._resolve import inspect` with `from mcp_server._resolve import resolve`.

3. Update `register_tools` description for `kms_inspect` to reflect the new signature.

**Test:**
- Unit: shim correctly delegates to `resolve()` with the right args.
- Unit: default mode is `"summary"` when not specified.

---

### Phase 4: Context Engine (Cluster C)

_Components: P9-C-01, P9-C-02, P9-C-03_
_Exit criteria: `build_vault_info_response` returns entity map + orientation facts from DB, zero disk reads. `build_search_response` returns orientation + query facts + document results. Identity dedup works across conversation._

#### P9-C-03: Context Engine — Identity Dedup Memory

**File:** `src/mcp_server/context.py`
**Behavior IDs:** P9-MCP-10

**What to do (build this FIRST — P9-C-01 and P9-C-02 depend on it):**

1. Replace the `__init__` method's `_dedup_memory: dict[str, str]` with two sets:
   ```python
   def __init__(self) -> None:
       self._seen_fact_ids: set[int] = set()
       self._seen_doc_ids: set[int] = set()
   ```

2. Add helper methods:
   ```python
   def is_fact_seen(self, entry_id: int) -> bool:
       return entry_id in self._seen_fact_ids

   def record_fact_seen(self, entry_id: int) -> None:
       self._seen_fact_ids.add(entry_id)

   def is_doc_seen(self, doc_id: int) -> bool:
       return doc_id in self._seen_doc_ids

   def record_doc_seen(self, doc_id: int) -> None:
       self._seen_doc_ids.add(doc_id)
   ```

3. Remove `is_already_provided` and `record_sent` methods (hash-based dedup, replaced by identity dedup).

**Test:**
- Unit: fresh engine -> `is_fact_seen(1)` returns False.
- Unit: after `record_fact_seen(1)` -> `is_fact_seen(1)` returns True.
- Unit: fact id 1 and doc id 1 are independent namespaces.

---

#### P9-C-01: Context Engine Rewrite — Vault Info

**File:** `src/mcp_server/context.py`
**Behavior IDs:** P9-MCP-01, P9-MCP-09, P9-MCP-10

**What to do:**

1. **Rewrite `build_vault_info_response`** to query DB instead of disk:
   - Remove all `Path.read_text`, `Path.is_file`, `Path.exists` calls.
   - Remove all `ProjectRegistry` references.
   - Remove CLAUDE.md reading.

2. **Entity map assembly:**
   - Query `knowledge_entries` grouped by dimension for all non-retired entries: `SELECT DISTINCT dimension, entity FROM knowledge_entries WHERE status != 'retired' ORDER BY dimension, entity`.
   - Group entities by dimension. If a dimension has more than `max_entities_per_dimension` entities, show top-K (by count of entries per entity) and append `"+N more"` note.
   - Read `max_entities_per_dimension` from config (new `ContextInjectionConfig` field).

3. **Orientation facts per dimension:**
   - For each dimension, query facts with 4-key ranking. Modify `query_ranked_by_dimension` (or add a new `query_ranked_for_orientation`) to use the 4-key ORDER BY: `trust_score DESC, retrieval_count DESC, confidence DESC, updated_at DESC`. Pass `limit = max_orientation_facts_per_dimension` (from config).
   - Format each fact as a bullet: `"- [{entity}] {fact} (confidence: {confidence})"`.

4. **Record injected fact ids** in `_seen_fact_ids` via `record_fact_seen`.

5. **Inbox stats:** Query `SELECT COUNT(*) FROM documents WHERE vault_path LIKE 'inbox/%'`.

6. **Return format:** `Success([entity_map_block, orientation_block, inbox_stats_block])`.

**Test:**
- Unit: entity map correctly groups entities by dimension.
- Unit: entity cap respected (15 entities, cap 10 -> show 10 + "+5 more").
- Unit: orientation facts ranked by 4-key sort.
- Unit: orientation facts capped per dimension.
- Unit: injected fact ids recorded in `_seen_fact_ids`.
- Unit: zero disk reads (mock filesystem, verify no file I/O calls).

---

#### P9-C-02: Context Engine Rewrite — Search Response

**File:** `src/mcp_server/context.py`
**Behavior IDs:** P9-MCP-02, P9-MCP-09, P9-MCP-10

**What to do:**

1. **Rewrite `build_search_response`** signature to match spec (remove `cards`, `registry`, `include_context` params; add `max_results`).

2. **Implementation:**
   - Call `search_dual(query, project=project, ...)` from `retrieval/search.py`.
   - Extract entities from fact results.
   - For each entity, fetch orientation facts (use same ranking helper as P9-C-01). Filter out facts already in `_seen_fact_ids`.
   - Assemble response: orientation fact blocks -> query fact blocks -> document result blocks.
   - Record all injected fact ids and doc ids via `record_fact_seen` / `record_doc_seen`.

3. **Delete `build_read_response`** method — only caller was `kms_read` which is being removed.

4. **Remove all disk-reading code:** CLAUDE.md reading, `context.yaml` reading, concentration gating.

**Test:**
- Unit: orientation facts prepended before query results.
- Unit: conversation-level dedup — calling twice with overlapping entities does not re-inject same facts.
- Unit: unclassified document surfaces in document results even with no matching facts.
- Integration: engine -> dual-corpus search -> fact assembly -> response blocks end-to-end.

---

### Phase 5: Tool Surface + Cleanup (Cluster A)

_Components: P9-A-01 through P9-A-05_
_Exit criteria: 5 tools registered. `kms_write` creates documents. `kms_correct` patches entries with audit. `kms_read` and `kms_move` gone. `_move.py` deleted._

#### P9-A-01: `kms_write` Backing + Shim

**Files:** `src/mcp_server/_write.py` (NEW), `src/mcp_server/tools.py`
**Behavior IDs:** P9-MCP-06

**What to do:**

1. **Create `src/mcp_server/_write.py`** with:
   ```python
   async def write_from_chat(
       content: str,
       title_hint: str | None = None,
       *,
       classify_queue=None,
       db_path: Path | None = None,
   ) -> Result[int]:
   ```
   - Generate a vault_path: `f"chat/{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slugify(title_hint or 'insight')}.md"` (use a simple slug function — lowercase, replace spaces with hyphens, truncate).
   - Compute `content_hash` from `hashlib.sha256(content.encode()).hexdigest()`.
   - Call `await capture_upload(vault_path=vault_path, extracted_text=content, content_hash=content_hash, db_path=db_path)`.
   - If success and `classify_queue` is not None, `classify_queue.put_nowait(doc_id)`.
   - Return `Success(doc_id)`.

2. **Wiring note — RESEARCH CORRECTION A12 APPLIED:**
   - In `cloud_entry.py` `_wrap_lifespan`, inject the classify queue into the FastMCP lifespan context dict. Modify the `_composed` lifespan to pass the queue through to the inner lifespan's yield dict:
     ```python
     async with inner(app_ref) as ctx_dict:
         ctx_dict["classify_queue"] = queue
         yield ctx_dict
     ```
   - This allows MCP tool handlers to access via `ctx.request_context.lifespan_context["classify_queue"]`.

3. **Add `kms_write` shim** to `tools.py`:
   ```python
   async def kms_write(
       content: str,
       title_hint: str | None = None,
       ctx: Context = None,
   ) -> dict:
       """Save a chat insight to the knowledge system."""
       queue = ctx.request_context.lifespan_context.get("classify_queue")
       result = await write_from_chat(content, title_hint, classify_queue=queue)
       return {"document_id": result.unwrap()}
   ```

4. **Register** in `register_tools` with appropriate description.

**Test:**
- Unit: `write_from_chat` calls `capture_upload` with `extracted_text=content`.
- Unit: vault_path starts with `chat/` and ends with `.md`.
- Unit: returns `Success(int)` on success.
- Unit: classify queue receives the new doc_id when available.

---

#### P9-A-02: `kms_correct` Backing + Shim

**Files:** `src/mcp_server/_correct.py` (NEW), `src/mcp_server/tools.py`
**Behavior IDs:** P9-MCP-07, P9-MCP-08

**What to do — RESEARCH CORRECTION A7 APPLIED: use AIDecision object for audit.**

1. **Create `src/mcp_server/_correct.py`** with:
   ```python
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
   ```

2. **Add `get_entry_by_id`** to `knowledge_entries.py` (does not exist yet — A8 confirmed):
   ```python
   def get_entry_by_id(entry_id: int, *, db_path: Path | None = None) -> Result[KnowledgeEntry | None]:
       try:
           with get_connection(db_path, readonly=True) as conn:
               conn.row_factory = sqlite3.Row
               row = conn.execute("SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)).fetchone()
               if row is None:
                   return Success(None)
               return Success(_row_to_entry(row))
       except sqlite3.Error as exc:
           return Failure(str(exc), recoverable=False, context={"entry_id": entry_id})
   ```

3. **`correct_entry` implementation:**
   - Step 1: Validate entry exists via `get_entry_by_id(entry_id)`. If `Success(None)` -> return `Failure("entry not found")`.
   - Step 2: Apply operation:
     - `edit_fact`: Copy existing entry, set `fact=new_fact`, call `upsert(entry)` (triggers facts_fts/vec re-sync from P9-B-02).
     - `change_tag`: Copy, set `tag=new_tag`, call `upsert(entry)`.
     - `change_entity`: Copy, set `entity=new_entity`, call `upsert(entry)`.
     - `promote`: Copy, call `upsert(entry, status="confident")`.
     - `retire`: Call `retire(entry_id, reason)`. Require `reason` — if None, return `Failure`.
     - `un_retire`: Copy, set `status="confident"`, clear `reasoning`, call `upsert(entry, status="confident")`.
   - Step 3: Audit (A7 correction):
     ```python
     from core.confidence import AIDecision
     import core.audit as audit
     from core.logging_setup import new_correlation_id

     new_correlation_id()
     decision = AIDecision(
         action=f"correct:{operation}",
         confidence=1.0,
         reasoning=reason or f"Consumer AI requested {operation}",
         source_ids=[str(entry_id)],
     )
     audit.write(decision, pipeline="correct", stage=operation, outcome="APPLIED")
     ```
   - Step 4: Return `Success({"entry_id": entry_id, "operation": operation, "result": "applied"})`.

4. **Add `kms_correct` shim** to `tools.py` — delegates to `correct_entry(...)`.

5. **Register** in `register_tools`.

**Test:**
- Unit: each of 6 operations produces correct DB state.
- Unit: nonexistent entry_id -> `Failure(recoverable=False)`.
- Unit: `retire` without `reason` -> `Failure`.
- Unit: every operation writes to `audit_log` with `pipeline="correct"`.
- Unit: `edit_fact` triggers facts_fts/vec re-sync.
- Unit: `un_retire` sets status to `confident` and clears reasoning.

---

#### P9-A-03: `kms_vault_info` Shim Rewrite

**File:** `src/mcp_server/tools.py`
**Behavior IDs:** P9-MCP-01

**What to do:**
1. The current shim (line 19-24) already delegates to the engine. No structural change needed — the shim body stays the same. The engine method it calls (`build_vault_info_response`) changes internally in Phase 4 (P9-C-01).
2. Verify no arguments changed. The shim passes no arguments — still correct.

**Test:**
- Unit: shim delegates to `build_vault_info_response()` with no arguments.

---

#### P9-A-04: `kms_search` Shim Rewrite

**File:** `src/mcp_server/tools.py`
**Behavior IDs:** P9-MCP-02, P9-MCP-03

**What to do:**
1. Remove the `include_context: bool = False` parameter from `kms_search` (concentration gating dropped).
2. Remove `include_context=include_context` from the engine call.
3. Add `max_results: int | None = None` parameter and pass it to the engine.
4. The engine's `build_search_response` signature changes in Phase 4 (P9-C-02) to accept the new params.

**Test:**
- Unit: shim delegates to `build_search_response()` with correct params.
- Unit: `include_context` parameter is gone from the tool signature.

---

#### P9-A-05: Remove `kms_read` + `kms_move` + `_move.py`

**Files:** `src/mcp_server/tools.py`, `src/mcp_server/_move.py` (DELETE)
**Behavior IDs:** P9-MCP-14

**What to do:**
1. Delete `kms_read` function (lines 51-64) from `tools.py`.
2. Delete `kms_move` function (lines 72-79) from `tools.py`.
3. Delete `from mcp_server._move import move` import (line 14).
4. Delete `from mcp_server._resolve import inspect` import (line 15) — already replaced in P9-D-02.
5. Delete `src/mcp_server/_move.py` file entirely.
6. In `register_tools`, remove `kms_read` and `kms_move` registrations. Add `kms_write` and `kms_correct` registrations (if not already added in P9-A-01/A-02).
7. Verify `register_tools` registers exactly 5 tools: `kms_vault_info`, `kms_search`, `kms_inspect`, `kms_write`, `kms_correct`.

**Test:**
- Verify `_move.py` does not exist on disk.
- Verify `register_tools` registers exactly 5 tools.
- Verify no import errors from removed modules.

---

### Phase 6: Seam + Docs (Clusters F, G)

_Components: P9-F-01, P9-G-01, P9-G-02_
_Exit criteria: `_should_overwrite` exists and is called in the update path. `AI_INSTRUCTIONS.md` documents 5-tool surface. Deployment guide reviewed._

#### P9-F-01: `_should_overwrite` Seam in classify_writer

**File:** `src/pipelines/classify_writer.py`
**Behavior IDs:** P9-MCP-15

**What to do:**

1. Add the decision point function after the existing DRY helpers (after `_merge_reasoning`, around line 47):
   ```python
   def _should_overwrite(existing_entry) -> bool:
       """Decision point for whether classify may overwrite an existing entry.

       Phase 9: always True (current behavior).
       Phase 10: trust_score > 0.5 -> False (write conflicting new entry instead).
       """
       return True
   ```

2. Insert a call to `_should_overwrite` in the update path of `write_entries`. The update branch (line 142, `elif action == "update":`) currently reads the existing entry, then calls `ke_upsert`. Before the `ke_upsert` call (line 202), add:
   ```python
   # Phase 10 seam — check if overwrite is permitted
   from storage.knowledge_entries import _row_to_entry as _rte
   existing_entry = _rte(row)
   if not _should_overwrite(existing_entry):
       # Phase 10: would write conflicting new entry instead
       pass  # Phase 9: always overwrites
   ```
   Simplest approach: call `_should_overwrite` with the existing `row` data (convert to `KnowledgeEntry` or just pass relevant fields). Since Phase 9 always returns True, the else branch is never taken. The call must be visibly present for Phase 10 to slot the guard in.

3. Similarly, in the "new" action twin-fold path (line 228, where `twin_id is not None`), add the same check before the `ke_upsert` of the twin entry.

**Test:**
- Unit: `_should_overwrite` exists and is callable.
- Unit: `_should_overwrite(any_entry)` returns `True`.
- Structural: grep `classify_writer.py` source for `_should_overwrite` — verify it appears in both the update and twin-fold paths.

---

#### P9-G-01: `AI_INSTRUCTIONS.md` Rewrite

**File:** `src/mcp_server/AI_INSTRUCTIONS.md`
**Behavior IDs:** P9-MCP-19

**What to do:**
1. Rewrite the entire document to cover:
   - **Tool inventory:** 5 tools (`kms_vault_info`, `kms_search`, `kms_inspect`, `kms_write`, `kms_correct`).
   - **Discovery workflow:** vault_info -> search -> inspect (not discover -> search -> read -> inspect -> move).
   - **Facts vs summaries model:** Facts = targeted extracted insights with entity/dimension/tag. Summaries = general 5-section digests.
   - **Correct vs write routing:** `kms_correct` = fix existing fact by id (confirm-first). `kms_write` = add new insight from conversation (proactive + transparent).
   - **Inspect modes:** summary (default) -> text (full body, opt-in) -> file (vault path, laptop-dependent).
   - **Reference model:** Integer document ids, not vault paths.
   - **Binary note caveat:** `kms_inspect text` on a binary returns the vision description, not raw bytes.

**Test:**
- Manual review: all 5 tools documented, removed tools absent, routing clear.

---

#### P9-G-02: AgentBase Deployment Guide

**File:** `docs/deployment/agentbase_guide.md` (NEW)
**Behavior IDs:** —

**What to do:**
1. Two-part non-technical guide:
   - **Builder part:** Stand up deployment, configure env vars (IAM/API/daemon keys), verify `/health`.
   - **Tester part:** Connect Claude Desktop to gateway endpoint, run daemon, verify 5 tools work.
2. Single-tenant per deployment. No secrets embedded.

**Test:**
- Manual: guide enables a non-technical person to stand up an instance and connect Claude Desktop.

---

## Migration Checklist

| Item | Detail |
|---|---|
| New file | `src/storage/migrations/012_fact_search_index.sql` |
| Tables created | `facts_fts` (FTS5 external content), `facts_vec` (vec0, FLOAT[384]) |
| Version bump | `schema_version` -> 12 |
| Test cascade | Bump version-pin in `test_migration_011.py` from 11 to 12 |
| No column rename | `retrieval_count` stays in SQL; Python field becomes `retrieval_score: float` |

---

## Config Additions

All new keys go in `config/config.yaml` under `mcp:` and in Pydantic models in `core/config.py`.

| Section | Key | Type | Default | Purpose |
|---|---|---|---|---|
| `mcp.context_injection` | `max_entities_per_dimension` | int | 15 | Entity map cap in vault_info |
| `mcp.context_injection` | `max_orientation_facts_per_dimension` | int | 5 | Orientation bullet cap per dimension |
| `mcp.inspect` | `max_text_refs` | int | 5 | Max ids that may use text mode per call |
| `mcp.retrieval_score` | `decay_factor` | float | 0.95 | Multiplier per injection and per sweep |
| `mcp.retrieval_score` | `sweep_interval_hours` | int | 24 | Background sweep frequency |
| `mcp.fact_search` | `keyword_weight` | float | 0.5 | Blend weight (0=all semantic, 1=all keyword) |
| `mcp.fact_search` | `max_results` | int | 20 | Default max fact results per search |

**Config fields to REMOVE** from `ContextInjectionConfig`:
- `frequency_threshold` (concentration gating dropped)
- `max_context_files` (replaced by per-dimension caps)
- `include_context_yaml` (no more disk reads)

**New Pydantic models** in `core/config.py`:
- `InspectConfig(max_text_refs: int = 5)`
- `RetrievalScoreConfig(decay_factor: float = 0.95, sweep_interval_hours: int = 24)`
- `FactSearchConfig(keyword_weight: float = 0.5, max_results: int = 20)`
- Update `MCPConfig` to include `inspect`, `retrieval_score`, `fact_search` fields.
- Update `ContextInjectionConfig` to add the two new fields and remove the three old ones.

---

## Test Strategy

| Phase | Approach | Key technique |
|---|---|---|
| Phase 1 (Bug Fixes) | Pure unit tests + one async test per fix | Source inspection for __main__ block; mock blob_store.async_put; mock asyncio.to_thread |
| Phase 2 (Retrieval) | Migration tests + unit tests + embedded research spike | `init_db` -> check tables; mock embedding model for unit tests; real model for research spike |
| Phase 3 (Resolve) | Unit tests per resolve mode | Fixture `DocumentRow` with various NULL patterns |
| Phase 4 (Context Engine) | Unit tests + integration test | Mock DB queries; integration: engine -> search -> assembly end-to-end |
| Phase 5 (Tools) | Unit tests per tool + structural test for tool count | Mock backing functions; count registered tools; verify audit_log writes |
| Phase 6 (Seam + Docs) | Structural test + manual review | Grep source for `_should_overwrite`; manual doc review |

---

## Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Short-fact embedding separation is poor | MEDIUM | Config-driven `keyword_weight` blend; research spike in Phase 2 measures before locking |
| FTS5 external content mode is fragile across SQLite versions | LOW | Alternative: drop external content, use regular FTS5 (duplicates data but simpler sync) |
| C-14 violation in `kms_inspect` shim (list comprehension contains `for`) | LOW | Move the dict-building loop into `_resolve.py` as a `resolve_as_dicts()` helper; shim calls it directly |
| `query_ranked_by_dimension` modification breaks classify callers | MEDIUM | Add a NEW function `query_ranked_for_orientation` with 4-key sort instead of modifying existing 3-key function |
| Composed lifespan inner yield dict injection may not work with FastMCP | MEDIUM | Test early in Phase 5; fallback: pass queue as a constructor parameter to `write_from_chat` |

---

## Out of Scope (Phase 10)

| Item | Why deferred |
|---|---|
| `adjust_trust()` function + trust_score movement | All entries start at trust_score 0.5 in Phase 9 |
| Classify overwrite guard (`trust_score > 0.5`) | `_should_overwrite` seam is placed but always returns True |
| Pending requests system (table + tools + housekeeping) | Entire system deferred |
| Corrections table reshape | Exists but inert and wrong-shaped |
| Few-shot injector (recent corrections -> extraction prompts) | Needs corrections table |
| `min_trust` filtering | No effect until trust_scores diverge from 0.5 |
| Volatility flag (entries with >3 corrections) | Needs correction count tracking |
| Web UI (conflict queue, comment feature, parked doc dashboard) | Future phase |
| Content-level document dedup for `kms_write` | Fact-level dedup catches duplicates |
| `retrieval_count` SQL column rename | Python reinterprets; no migration needed |
| Global token budget for context injection | Two config knobs suffice |
