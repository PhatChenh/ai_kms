# Plan: P3 Session A -- Index Layer + Pre-flight Fixes
_Last updated: 2026-06-10_
_Status: [~] Phases 1-3 done, Phases 4-5 pending — SEE HANDOFF at bottom_

**Spec:** `docs/2_specs/p3_session_a_index_layer.md`
**Research:** `docs/3_research/p3_session_a_index_layer.md`

---

## Architecture

### Q1 -- What happens inside

See spec `## Q1 Diagram` -- two diagrams: Q1-A (capture path) and Q1-B (maintenance path). The plan does not reproduce them here.

### Q2 -- How it connects

See spec `## Q2 Diagram`. The Index Layer connects to: Capture Pipeline (best-effort calls after save), File Index (same-transaction cleanup on delete/rename), Search Settings (model name from config), Database Connector (sqlite-vec loaded on every connect), and Schema Migrator (migration 007 creates both tables). Session B (Search API) is the downstream consumer -- dashed box, not built here.

### Q3 -- Why build it this way

```
# Index Layer -- Why Build It This Way
Scope: Rules and existing patterns that shaped this design.
       Does NOT show internal flow (see Q1) or connections (see Q2).

How to read this:
  Center box        = the feature being built
  Surrounding boxes = rules it must follow and why
  Lines             = which rule applies where

  +----------------------------+     +----------------------------+
  | One connection factory     |     | Same-transaction cleanup   |
  | loads all extensions       |     |                            |
  |                            |     | File Index functions each  |
  | Database Connector is      |     | own their database         |
  | the only way to open a     |     | connection -- search       |
  | database. sqlite-vec       |     | delete/rename runs INSIDE  |
  | loads here so every        |     | that same connection, so   |
  | caller gets vector         |     | either both succeed or     |
  | support automatically.     |     | neither does.              |
  +-------------+--------------+     +-------------+--------------+
                |                                   |
                |     +--------------------+        |
                +---->|   INDEX LAYER      |<-------+
                      |   Meaning Indexer   |
                +---->|   + Word Indexer    |<-------+
                |     +--------------------+         |
                |                |                   |
  +-------------+--------------+ |  +----------------+-------------+
  | Best-effort, not stages    | |  | Decoupled from vault         |
  |                            | |  | types                        |
  | Capture Pipeline's stage   | |  |                              |
  | runner halts on failure.   | |  | Retrieval modules accept     |
  | Search indexing must NOT   | |  | plain text fields (title,    |
  | halt capture. So it runs   | |  | tags, summary), not vault    |
  | as try/except inside the   | |  | objects. This keeps them     |
  | Save step -- failure is    | |  | independent and testable     |
  | caught and logged, never   | |  | without the vault layer.     |
  | propagated.                | |  |                              |
  +----------------------------+ |  +------------------------------+
                                 |
                +----------------+-------------------+
                | DELETE then INSERT everywhere       |
                |                                     |
                | The vector table does not support    |
                | "replace" or "update primary key."  |
                | Every write -- upsert, rename,       |
                | replace -- must DELETE the old row   |
                | first, then INSERT the new one.      |
                | Keyword table uses the same pattern  |
                | (no uniqueness enforcement).         |
                +-------------------------------------+
```

---

## Approach

The plan groups the 9 spec components into 5 phases. Each phase produces a testable artifact before the next begins. The dependency chain is: infrastructure first (schema + extension + config + dependency), then the two indexer modules, then wiring into the capture pipeline, then maintenance cleanup in documents.py. TD-050 is independent and ships in Phase 1 alongside infrastructure to fix existing flaky tests early.

The ordering is TDD: write migration, verify table creation, then build code that writes to those tables. Each indexer is unit-tested in isolation before being wired into the pipeline. Pipeline integration tests come last.

---

## Phases

### Phase 1 -- Infrastructure: schema, extension, config, dependency, TD-050

**Goal**: Land all infrastructure that the indexer modules depend on. After this phase, the database has search tables, every connection loads sqlite-vec, config exposes search settings, and sentence-transformers is importable. Also resolves TD-050 timer leak.

Implements spec components 1, 2, 3, 4, 9. See the spec for full build descriptions, file inventories, and done-when criteria.

**Design**:

```
BEFORE                              AFTER

_connect() in db.py:                _connect() in db.py:
  PRAGMA journal_mode=WAL             PRAGMA journal_mode=WAL
  PRAGMA foreign_keys=ON              PRAGMA foreign_keys=ON
  (return conn)                       sqlite_vec.load(conn)
                                      (return conn)

migrations/:                        migrations/:
  001 ... 006                         001 ... 006
                                      007_search_indexes.sql   <-- NEW

MainConfig:                         MainConfig:
  vault, database, ...                vault, database, ...
                                      search: SearchConfig     <-- NEW

config.yaml:                        config.yaml:
  vault: ...                          vault: ...
                                      search:                  <-- NEW
                                        embedding_model: ...
                                        reranker_model: ...

tests/test_vault/conftest.py:       tests/test_vault/conftest.py:
  vault_config fixture                vault_config fixture
  vault_root fixture                  vault_root fixture
                                      _cancel_leaked_timers    <-- NEW (autouse)
```

**Steps (TDD order)**:

1. **TD-050 fixture** (spec component 9). Add the autouse `_cancel_leaked_timers` fixture to `tests/test_vault/conftest.py`. Run `pytest tests/test_vault/ -x` 3 times to confirm no flaky timer leaks. This ships first because it stabilizes the test suite for all subsequent phases.

2. **Migration 007** (spec component 1). Create `src/storage/migrations/007_search_indexes.sql` with the exact SQL from the spec. Note the `# COUPLING:` comment re: float[384] tied to `all-MiniLM-L6-v2`. Write a test in `tests/test_storage/test_db.py` (or a new `test_migration_007.py`) that calls `init_db(tmp_path / "test.db")` and asserts: (a) both tables exist, (b) schema_version is 7, (c) calling `init_db` again does not error (P3-IDX-09).

3. **sqlite-vec extension loading** (spec component 2). Modify `_connect()` in `src/storage/db.py` -- add the 3-line block after the FK pragma (line 19). Write a test that opens a connection via `get_connection()` and runs `SELECT vec_version()` (P3-IDX-08). Verify the existing FK pragma still works by running an FK-violating INSERT and confirming it raises.

4. **SearchConfig** (spec component 3). Add the `SearchConfig` Pydantic model to `src/core/config.py` and wire it onto `MainConfig` as `search: SearchConfig = Field(default_factory=SearchConfig)`. Add the `search:` section to `src/config/config.yaml`. Write a test that loads config and asserts all 4 defaults (P3-IDX-10). [extensible: config]

5. **Dependency** (spec component 4). Add `"sentence-transformers>=2.2.0"` to `pyproject.toml` dependencies. Note: `sqlite-vec>=0.1.9` is already present (research confirmed, line 31) -- do NOT add a duplicate. Run `uv sync`. Verify `import sentence_transformers` succeeds.

**Files to modify**:
- `tests/test_vault/conftest.py` -- add autouse fixture (TD-050)
- `src/storage/migrations/007_search_indexes.sql` -- NEW file (migration)
- `src/storage/db.py` -- 3 lines added to `_connect()` after line 19
- `src/core/config.py` -- add `SearchConfig` class + field on `MainConfig` (after line 332)
- `src/config/config.yaml` -- add `search:` section
- `pyproject.toml` -- add `sentence-transformers` dependency

**Test criteria**:
- [ ] `pytest tests/test_vault/ -x` passes 3x with no timer-leak flakes (TD-050)
- [ ] P3-IDX-09: fresh `init_db()` creates both `embeddings_vec` and `notes_fts`; second call idempotent; schema_version=7
- [ ] P3-IDX-08: `SELECT vec_version()` via `get_connection()` succeeds
- [ ] P3-IDX-10: `CONFIG.main.search.embedding_model == "all-MiniLM-L6-v2"` with defaults; YAML override works
- [ ] `import sentence_transformers` succeeds after `uv sync`
- [ ] Existing test suite still passes (no regressions from extension loading or config change)

**Status**: [x] done

**Completed**: 2026-06-10
**Notes**: All 5 steps completed in TDD order. TD-050 fixture uses `threading.enumerate()` to cancel leaked Timer threads (no production code changes needed). Migration 007 creates `embeddings_vec` (vec0) and `notes_fts` (FTS5) tables. sqlite-vec loaded in `_connect()` on every connection. SearchConfig wired with defaults + config.yaml section. sentence-transformers>=2.2.0 installed. Pre-existing `test_init_db_creates_file` table-count assertion updated to include new tables. 6 new tests; 1113 total passing (0 failures).

---

### Phase 2 -- Meaning Indexer

**Goal**: A standalone module that takes a note's metadata fields and stores a 384-dim vector embedding in the database. Fully testable without the capture pipeline.

Implements spec component 5. See the spec for full build description and done-when criteria.

**Design**:

```
CALL SIGNATURE (decoupled from vault types):

  index_embedding(
      vault_path: str,
      title: str,
      note_type: str | None,
      tags: list[str],
      summary: str | None,
      db_path: Path | None = None,
  ) -> Result[None]

INTERNAL FLOW:
  1. Lazy-load SentenceTransformer (module-level _model cache)
     Model name from CONFIG.main.search.embedding_model (lazy import)
  2. Build contextual string:
     "title: {title} | type: {type} | tags: {csv} | {summary}"
     (omit summary suffix if None/empty)
  3. Encode -> float32 bytes
  4. DELETE existing row for vault_path
  5. INSERT new row
  6. On sqlite3.OperationalError: retry once immediately
  7. Any other exception: Failure(recoverable=True)

RESULT: embeddings_vec row with vault_path PK + 1536-byte embedding
```

**Steps (TDD order)**:

1. Create `src/retrieval/__init__.py` (empty or re-exports). Create `src/retrieval/embeddings.py` with the `index_embedding` function. Use lazy CONFIG import inside the function body (C-17 compliance). Module-level `_model: SentenceTransformer | None = None` for lazy caching.

2. Write unit tests in `tests/test_retrieval/test_embeddings.py` (new directory + `__init__.py`). Mock `SentenceTransformer` to return a fixed 384-dim numpy array. Test cases:
   - Insert: new vault_path creates a row in `embeddings_vec`
   - Upsert (DELETE+INSERT): calling twice with same vault_path leaves exactly one row
   - Contextual string format: verify the string passed to `.encode()` matches spec format (P3-IDX-07)
   - No-summary fallback: when summary is None, string omits the summary suffix
   - Retry on OperationalError: mock the first INSERT to raise, verify retry succeeds
   - Double failure: mock both attempts to raise, verify `Failure(recoverable=True)` returned
   - Model load failure: mock SentenceTransformer constructor to raise, verify `Failure(recoverable=True)`

3. All tests must use an in-memory SQLite DB with sqlite-vec loaded and migration 007 applied (fixture from Phase 1). No CONFIG at module scope in tests.

**Files to modify**:
- `src/retrieval/__init__.py` -- NEW file
- `src/retrieval/embeddings.py` -- NEW file
- `tests/test_retrieval/__init__.py` -- NEW file
- `tests/test_retrieval/test_embeddings.py` -- NEW file

**Test criteria**:
- [ ] P3-IDX-07: contextual string contains title, type, tags, summary in deterministic format
- [ ] Embedding stored is exactly 384 floats (1536 bytes)
- [ ] DELETE+INSERT pattern: no duplicate rows after multiple calls
- [ ] Retry: recovers from single OperationalError
- [ ] Failure path: returns `Failure(recoverable=True)` on persistent error or model failure
- [ ] All tests pass with mocked SentenceTransformer (no real model download)

**Notes**:
- [extensible: config] -- model name driven by `SearchConfig.embedding_model`
- Known coupling: 384 dimensions hardcoded in migration, coupled to `all-MiniLM-L6-v2`. Documented via `# COUPLING:` comment in migration file.

**Status**: [x] done

**Completed**: 2026-06-10
**Notes**: All 7 test cases pass. Implementation uses lazy SentenceTransformer loading via module-level `_model` cache + `_get_model()` helper (C-17 compliant: CONFIG imported inside function body). Contextual string format: `title: {title} | type: {note_type} | tags: {csv} | {summary}` with summary suffix omitted when None/empty. DELETE+INSERT pattern for vec0 PK semantics. Single retry on `sqlite3.OperationalError`. All tests mock SentenceTransformer -- no real model download needed.

---

### Phase 3 -- Word Indexer

**Goal**: A standalone module that inserts a note's text content into the FTS5 full-text search table. Fully testable without the capture pipeline.

Implements spec component 6. See the spec for full build description and done-when criteria.

**Design**:

```
CALL SIGNATURE:

  index_keywords(
      vault_path: str,
      title: str,
      summary: str,
      body: str,
      db_path: Path | None = None,
  ) -> Result[None]

INTERNAL FLOW (single get_connection transaction):
  1. DELETE FROM notes_fts WHERE vault_path = ?
  2. INSERT INTO notes_fts(vault_path, title, summary, body) VALUES (?, ?, ?, ?)
  3. On sqlite3.OperationalError: retry once
  4. Other exceptions: Failure(recoverable=True)
```

**Steps (TDD order)**:

1. Create `src/retrieval/keyword.py` with the `index_keywords` function. Both DELETE and INSERT in the same `get_connection()` context manager.

2. Write unit tests in `tests/test_retrieval/test_keyword.py`. Test cases:
   - Insert: new vault_path creates a row findable by `SELECT vault_path FROM notes_fts WHERE notes_fts MATCH ?`
   - DELETE+INSERT (no duplicates): calling twice with same vault_path leaves exactly one row
   - FTS5 MATCH: a distinctive keyword in the body is findable (P3-IDX-02 unit-level)
   - Retry on OperationalError: same pattern as Meaning Indexer tests
   - Double failure: returns `Failure(recoverable=True)`

3. Tests use in-memory SQLite with migration 007 applied. No sqlite-vec needed for FTS5-only tests, but `_connect()` loads it anyway -- that is fine.

**Files to modify**:
- `src/retrieval/keyword.py` -- NEW file
- `tests/test_retrieval/test_keyword.py` -- NEW file

**Test criteria**:
- [x] `SELECT vault_path FROM notes_fts WHERE notes_fts MATCH 'stakeholder'` returns the note after indexing
- [x] No duplicate rows after multiple calls with same vault_path
- [x] Retry: recovers from single OperationalError
- [x] Failure path: returns `Failure(recoverable=True)` on persistent error

**Status**: [x] done

**Completed**: 2026-06-10
**Notes**: All 5 tests pass. Implementation follows the same pattern as embeddings.py: single get_connection() transaction for DELETE+INSERT, retry once on sqlite3.OperationalError, Failure(recoverable=True) on all other exceptions. Tests cover: basic insert+FTS5 MATCH, DELETE+INSERT no-duplicates, distinctive keyword MATCH (P3-IDX-02), single retry recovery, and double-failure path. No CONFIG import needed -- db_path passed explicitly.

---

### Phase 4 -- Capture pipeline wiring + index maintenance

**Goal**: Every successful capture automatically indexes the note for search (best-effort). Deleting, renaming, or replacing a note cleans up search entries in the same transaction. This is the integration phase -- the indexers from Phases 2-3 are wired into real code paths.

Implements spec components 7 and 8. See the spec for full build descriptions, exact SQL, and done-when criteria.

**Design -- Capture wiring (Component 7)**:

```
4 call sites in pipelines/capture.py gain best-effort try/except blocks.
Each site fires AFTER a successful documents.upsert() or documents.replace_path().

  CALL SITE 1 -- _store_md rename path (line 993, after replace_path succeeds):
    Body source: original_body (from read_note at line 929)

  CALL SITE 2 -- _store_md in-place path (line 1011, after upsert succeeds):
    Body source: original_body (same variable)

  CALL SITE 3 -- _store_nonmd LOCATED (line 1182, after upsert succeeds):
    Body source: rich_body (from provider.complete at line 1100)

  CALL SITE 4 -- _store_nonmd CLUELESS (line 1286, after upsert succeeds):
    Body source: rich_body (from missing-file fallback at 1210 or provider.complete at 1227)

Each site:
  try:
      from retrieval.embeddings import index_embedding
      from retrieval.keyword import index_keywords
      _title = mr.ai_title or Path(outcome.vault_path).stem
      index_embedding(vault_path=..., title=_title, note_type=mr.ai_type,
                      tags=mr.ai_tags, summary=mr.summary, db_path=ctx.db_path)
  except Exception:
      logger.warning("store.embedding_index_failed", vault_path=...)
  try:
      index_keywords(vault_path=..., title=_title, summary=mr.summary or "",
                     body=<body_variable>, db_path=ctx.db_path)
  except Exception:
      logger.warning("store.keyword_index_failed", vault_path=...)
```

**Design -- Index maintenance (Component 8)**:

```
3 functions in storage/documents.py gain search-table cleanup SQL.

  delete_by_path (line 212, inside existing `with get_connection`):
    BEFORE the documents DELETE, add:
      conn.execute("DELETE FROM embeddings_vec WHERE vault_path = ?", (vault_path,))
      conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (vault_path,))

  rename (line 304, inside existing `with get_connection`):
    AFTER the documents UPDATE, add:
      # vec0: copy embedding, delete old, insert new (PK update not supported)
      row = conn.execute("SELECT embedding FROM embeddings_vec WHERE vault_path = ?", (old,)).fetchone()
      if row:
          conn.execute("DELETE FROM embeddings_vec WHERE vault_path = ?", (old,))
          conn.execute("INSERT INTO embeddings_vec(vault_path, embedding) VALUES (?, ?)", (new, row[0]))
      # FTS5: copy content, delete old, insert new
      fts_row = conn.execute("SELECT title, summary, body FROM notes_fts WHERE vault_path = ?", (old,)).fetchone()
      if fts_row:
          conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (old,))
          conn.execute("INSERT INTO notes_fts(vault_path, title, summary, body) VALUES (?, ?, ?, ?)", (new, *fts_row))

  replace_path (line 252, inside existing `with get_connection`):
    BEFORE the documents DELETE, add:
      conn.execute("DELETE FROM embeddings_vec WHERE vault_path = ?", (old_vault_path,))
      conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (old_vault_path,))
    NOTE: new path's search entries are created by capture pipeline's best-effort indexing,
    not by replace_path itself. Avoids duplicating embedding logic in the data layer.
```

**Steps (TDD order)**:

1. **Index maintenance first** (spec component 8). Modify `delete_by_path`, `rename`, and `replace_path` in `src/storage/documents.py`. Write tests in `tests/test_storage/test_documents.py` (or new file `test_documents_search.py`):
   - P3-IDX-05: after `delete_by_path()`, both search tables have zero rows for that vault_path
   - P3-IDX-06: after `rename()`, old path has zero rows, new path has one row in each table; embedding bytes are preserved
   - `replace_path`: old path's search entries deleted; new path has no search entries (those come from pipeline indexing)
   - Edge case: rename/delete when search tables have no row for that path -- no error (zero rows affected)

2. **Capture pipeline wiring** (spec component 7). Modify `src/pipelines/capture.py` at all 4 call sites. Use lazy imports inside the try blocks (`from retrieval.embeddings import index_embedding`). The title comes from `mr.ai_title` (NOT `outcome.metadata.extra["title"]` -- see A9 correction in research). The body source differs per call site as documented above.

3. Write integration tests in `tests/test_pipelines/test_capture_search.py` (new file):
   - P3-IDX-01: capture a .md note with mocked LLM, verify `embeddings_vec` row exists
   - P3-IDX-02: capture a .md note with distinctive keyword, verify `notes_fts MATCH` returns it
   - P3-IDX-03: mock `index_embedding` to raise, verify capture returns `Success`
   - P3-IDX-04: mock `index_keywords` to raise, verify capture returns `Success`
   - Non-md path: capture a PDF (mocked), verify sibling's vault_path is in both search tables

**Files to modify**:
- `src/storage/documents.py` -- add search cleanup SQL in `delete_by_path` (line ~213), `rename` (line ~308), `replace_path` (line ~253)
- `src/pipelines/capture.py` -- add try/except indexing blocks at lines ~993, ~1011, ~1182, ~1286
- `tests/test_storage/test_documents_search.py` -- NEW file (maintenance tests)
- `tests/test_pipelines/test_capture_search.py` -- NEW file (integration tests)

**Test criteria**:
- [ ] P3-IDX-01: after capture, `embeddings_vec` row exists for the note
- [ ] P3-IDX-02: after capture, `notes_fts MATCH` returns the note
- [ ] P3-IDX-03: embedding failure does not block capture (returns Success)
- [ ] P3-IDX-04: keyword failure does not block capture (returns Success)
- [ ] P3-IDX-05: delete removes search entries from both tables
- [ ] P3-IDX-06: rename moves search entries (old path gone, new path present, embedding preserved)
- [ ] Edge case: rename/delete with no search entries does not error
- [ ] Full existing test suite still passes

**Notes**:
- The classify step's `_classify_auto_md_move` calls `documents.replace_path` at line 515 BEFORE `_store_md`. At that point the note has no search entries (hasn't been indexed yet), so the Phase 4 cleanup in `replace_path` is a no-op (zero rows deleted). This is correct. See research "Edge Cases" item 1.
- sqlite-vec extension loading on every `_connect()` call adds minor overhead to read-only paths. Acceptable for correctness -- any query might touch vec0 tables. See research "Edge Cases" item 2.

**Status**: [ ] pending

---

### Phase 5 -- Full suite verification + commit

**Goal**: Confirm the entire test suite passes after all 4 phases. Run the full suite, address any regressions, and verify end-to-end behavior.

**Steps**:

1. Run `uv run pytest tests/ -x` -- full suite must pass with zero failures.
2. Run `uv run ruff check .` and `uv run ruff format --check .` -- no lint/format violations.
3. Verify test count increased by the expected number of new tests (estimate: ~25-30 new tests across Phases 1-4).
4. Smoke test: if a vault is available, run `kms capture <file>` and verify both search tables have rows. If no vault is available, rely on the integration tests from Phase 4.

**Test criteria**:
- [ ] Full `pytest tests/` passes with 0 failures
- [ ] `ruff check .` clean
- [ ] `ruff format --check .` clean
- [ ] Test count delta matches expectation

**Status**: [ ] pending

---

## Open Questions

1. **Embedding model download in CI.** `sentence-transformers` first import pulls ~500MB of PyTorch dependencies. Tests mock the model, so no download is needed in CI. But if someone runs tests with `SENTENCE_TRANSFORMERS_HOME` unset and a test accidentally skips the mock, it could trigger a slow download. The spec defers this to environment variable caching. No action needed now, but worth documenting in a test-level comment.

2. **vec0 table rebuild on model change.** If `SearchConfig.embedding_model` is changed, existing vectors become incompatible. A model change requires dropping/recreating `embeddings_vec` plus a full re-index. No automated enforcement exists. Documented via `# COUPLING:` comment in migration 007. A future `kms reindex` command (Session B scope) would handle this.

---

## Out of Scope

See spec `## Out of scope` for the full list. Key items:
- Search API (query, rank, return results) -- Session B
- Reranker execution -- Session B (config stored, no runtime use)
- Reciprocal rank fusion -- Session B
- MCP search tool -- Phase 4 (after Session B)
- Backfill existing notes -- Session B reconcile or `kms reindex`
- Embedding model download/caching in CI -- resolved by env variable

---

## Tech debt resolved by this plan

| TD | Resolution |
|---|---|
| TD-004 | Embeddings table + FTS5 virtual table -- RESOLVED by migration 007 (Phase 1) |
| TD-050 | Watcher timer leak -- RESOLVED by autouse fixture (Phase 1) |

---

## Extension points

| Component | Extension category | Notes |
|---|---|---|
| SearchConfig | [extensible: config] | Model names, limits all in YAML |
| Meaning Indexer | [extensible: config] | Model name from SearchConfig |
| Word Indexer | [closed] | FTS5 schema coupled to migration 007 |
| Migration 007 | [closed] | float[384] coupled to model -- `# COUPLING:` documented |
| retrieval/ package | [extensible: protocol] | New search backends can be added as new modules |

---

# HANDOFF — 2026-06-10

## What is DONE (Phases 1-3 merged to main)

### Phase 1 — Infrastructure
- **Migration 007:** `src/storage/migrations/007_search_indexes.sql`
  - `embeddings_vec` — vec0 virtual table, `vault_path TEXT PRIMARY KEY, embedding FLOAT[384]`
  - `notes_fts` — FTS5 virtual table, `vault_path UNINDEXED, title, summary, body, tokenize='porter unicode61'`
  - COUPLING comment: float[384] tied to `all-MiniLM-L6-v2`
- **sqlite-vec extension:** `_connect()` in `src/storage/db.py` (lines 21-24) loads `sqlite_vec` after FK pragma
- **SearchConfig:** `src/core/config.py` (lines ~303-312) — `SearchConfig(BaseModel)` with 4 fields: `embedding_model`, `reranker_model`, `max_candidates`, `max_results`. Wired as `search: SearchConfig = Field(default_factory=SearchConfig)` on `MainConfig` (line ~342)
- **Config YAML:** `src/config/config.yaml` — `search:` section with all 4 keys
- **Dependencies:** `pyproject.toml` — `sentence-transformers>=2.2.0` + `sqlite-vec>=0.1.9`
- **TD-050:** `tests/test_vault/conftest.py` (lines 62-89) — autouse `_cancel_leaked_timers` fixture
- **Tests:** `tests/test_storage/test_migration_007.py` (5 tests), `tests/test_core/test_config.py::TestSearchConfig` (6 tests + 4 smoke), `tests/test_storage/test_db.py` (updated table-count assertions)

### Phase 2 — Meaning Indexer
- **Module:** `src/retrieval/embeddings.py` (99 lines) — `index_embedding(vault_path, title, note_type, tags, summary, db_path) -> Result[None]`
  - Lazy `SentenceTransformer` via `_get_model()`, model name from `CONFIG.main.search.embedding_model`
  - Contextual string: `"title: {title} | type: {type} | tags: {csv} | {summary}"` (summary omitted if None/empty)
  - DELETE+INSERT pattern (vec0 no PK update support)
  - Retry once on `sqlite3.OperationalError`
- **Tests:** `tests/test_retrieval/test_embeddings.py` (7 tests, 247 lines) — all use mocked SentenceTransformer

### Phase 3 — Word Indexer
- **Module:** `src/retrieval/keyword.py` (52 lines) — `index_keywords(vault_path, title, summary, body, db_path) -> Result[None]`
  - DELETE+INSERT in single `get_connection()` transaction
  - Retry once on `sqlite3.OperationalError`
- **Tests:** `tests/test_retrieval/test_keyword.py` (5 tests, 188 lines)

### Test count: 1135 passing
- `pytest tests/ -x` = 1135 passed, 0 failures (6 deselected integration)
- `ruff check .` clean on all modified files

---

## What is REMAINING (Phases 4-5)

### Phase 4 — Capture pipeline wiring + index maintenance

**Two sub-components:**

#### Component 7 — Capture wiring (best-effort try/except blocks)
4 call sites in `src/pipelines/capture.py` need indexing calls AFTER successful `documents.upsert()` or `documents.replace_path()`:

| Call Site | Location | After | Body source |
|-----------|----------|-------|-------------|
| 1 — `_store_md` rename | ~line 993, after `replace_path` | `original_body` (from `read_note` at ~line 929) |
| 2 — `_store_md` in-place | ~line 1011, after `upsert` | `original_body` (same variable) |
| 3 — `_store_nonmd` LOCATED | ~line 1182, after `upsert` | `rich_body` (from `provider.complete` at ~line 1100) |
| 4 — `_store_nonmd` CLUELESS | ~line 1286, after `upsert` | `rich_body` (from fallback at ~1210 or provider at ~1227) |

Each site adds:
```python
try:
    from retrieval.embeddings import index_embedding
    from retrieval.keyword import index_keywords
    _title = mr.ai_title or Path(outcome.vault_path).stem
    index_embedding(vault_path=..., title=_title, note_type=mr.ai_type,
                    tags=mr.ai_tags, summary=mr.summary, db_path=ctx.db_path)
except Exception:
    logger.warning("store.embedding_index_failed", vault_path=...)
try:
    index_keywords(vault_path=..., title=_title, summary=mr.summary or "",
                   body=<body_variable>, db_path=ctx.db_path)
except Exception:
    logger.warning("store.keyword_index_failed", vault_path=...)
```

#### Component 8 — Index maintenance (search-table cleanup in documents.py)
3 functions in `src/storage/documents.py` need search-table SQL:

1. **`delete_by_path`** (~line 212, inside existing `with get_connection`): BEFORE the documents DELETE, add:
   ```python
   conn.execute("DELETE FROM embeddings_vec WHERE vault_path = ?", (vault_path,))
   conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (vault_path,))
   ```

2. **`rename`** (~line 304, inside existing `with get_connection`): AFTER the documents UPDATE, add:
   ```python
   # vec0: copy embedding, delete old, insert new (PK update not supported)
   row = conn.execute("SELECT embedding FROM embeddings_vec WHERE vault_path = ?", (old,)).fetchone()
   if row:
       conn.execute("DELETE FROM embeddings_vec WHERE vault_path = ?", (old,))
       conn.execute("INSERT INTO embeddings_vec(vault_path, embedding) VALUES (?, ?)", (new, row[0]))
   # FTS5: copy content, delete old, insert new
   fts_row = conn.execute("SELECT title, summary, body FROM notes_fts WHERE vault_path = ?", (old,)).fetchone()
   if fts_row:
       conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (old,))
       conn.execute("INSERT INTO notes_fts(vault_path, title, summary, body) VALUES (?, ?, ?, ?)", (new, *fts_row))
   ```

3. **`replace_path`** (~line 252, inside existing `with get_connection`): BEFORE the documents DELETE, add:
   ```python
   conn.execute("DELETE FROM embeddings_vec WHERE vault_path = ?", (old_vault_path,))
   conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (old_vault_path,))
   ```
   NOTE: new path's search entries are created by capture pipeline's best-effort indexing, not by `replace_path` itself.

**Tests needed for Phase 4:**
- `tests/test_storage/test_documents_search.py` — NEW: P3-IDX-05 (delete removes search entries), P3-IDX-06 (rename moves search entries), edge cases
- `tests/test_pipelines/test_capture_search.py` — NEW: P3-IDX-01 (capture creates embedding row), P3-IDX-02 (capture creates FTS5 row), P3-IDX-03 (embedding failure doesn't block capture), P3-IDX-04 (keyword failure doesn't block capture), non-md path

**Key gotcha:** `_classify_auto_md_move` calls `documents.replace_path` at ~line 515 BEFORE `_store_md`. At that point the note has no search entries yet, so the Phase 4 cleanup in `replace_path` is a no-op (zero rows deleted). This is correct behavior — see research "Edge Cases" item 1.

### Phase 5 — Full suite verification + commit
- Run `pytest tests/ -x` — must pass with zero failures
- Run `ruff check .` — must be clean
- Run `ruff format --check .` — must be clean
- Verify test count increased (~10-15 new tests in Phase 4)
- Commit and push

---

## How to continue

1. Read this file for the full plan and architecture
2. Read `docs/roadmap/roadmap.md#1-141` for project context and stable interfaces (IGNORE lines 285-432 — stale Phase 3 design, this plan supersedes it)
3. Read CLAUDE.md for codebase rules and patterns
4. Create a worktree: branch off main, work there
5. Implement Phase 4 using `/tdd-implement` (TDD order: maintenance tests first, then wiring, then integration tests)
6. Verify Phase 5 — full suite green
7. Commit each phase separately

**Key files to read before starting Phase 4:**
- `src/pipelines/capture.py` — find the 4 call sites around the line numbers listed above (they may have shifted after Phase 2 changes)
- `src/storage/documents.py` — understand `delete_by_path`, `rename`, `replace_path` structure
- `src/retrieval/embeddings.py` + `src/retrieval/keyword.py` — understand the `index_embedding` / `index_keywords` signatures

**Branch state:** Merged to `main` (commit `da5a0f5`). Ahead of `origin/main` by 2 commits. Ready to push.
