# Phase 3 Session A — Index Layer + Pre-flight Fixes

## Purpose

After this phase, every captured note is automatically indexed for two kinds of search: meaning-based (vector similarity) and keyword-based (full-text). When notes are deleted, moved, or renamed, their search entries are cleaned up automatically. If indexing fails for any reason, the note is still captured successfully -- search indexing is best-effort and never blocks the capture pipeline.

This phase also lands the database schema, config surface, and dependency wiring that Session B (Search API) will build on.

---

## Already built (reuse, do not rebuild)

| Component | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| Capture Pipeline | `pipelines/capture.py` | Orchestrates 7-stage note processing (extract, enrich, summarize, metadata, location tags, classify, save) | Save step gains two best-effort indexing calls after every successful File Index upsert | deep |
| File Index | `storage/documents.py` | Tracks all notes in the database -- upsert, delete, rename, replace_path | delete_by_path, rename, and replace_path gain search-table cleanup within the same transaction | deep |
| Database Connector | `storage/db.py` | Creates SQLite connections with WAL mode and FK pragma; runs schema migrations | `_connect()` gains sqlite-vec extension loading; `_run_migrations()` applies migration 007 | deep |
| Schema Migrator | `storage/migrations/` | Numbered .sql files applied in lexical order by `_run_migrations()` | Migration 007 creates both virtual tables | shallow |
| Settings Manager | `core/config.py` | Validates and serves config to all modules via `CONFIG` singleton | `MainConfig` gains a `search: SearchConfig` field | deep |
| Settings File | `config/config.yaml` | YAML config on disk | Gains a `search:` section with embedding model, reranker model, and query limits | shallow |
| Note Reader | `vault/reader.py` | Loads a .md note from disk; returns `Note(path, metadata, content, content_hash)` | Body text from `Note.content` feeds the Word Indexer for .md files | deep |
| Frontmatter Parser | `vault/frontmatter.py` | Typed wrapper for note YAML frontmatter; `NoteMetadata` with summary, tags, type, etc. | Metadata fields (title, type, tags, summary) are used to build the contextual embedding string | deep |
| Result Type | `core/result.py` | `Success[T] | Failure` -- every public function returns this | Both indexers return `Result[None]` | shallow |
| Dependency Manifest | `pyproject.toml` | Lists project dependencies | Gains `sentence-transformers` and `sqlite-vec` | shallow |
| Test Fixtures | `tests/test_vault/conftest.py` | Shared vault test setup (vault_root, vault_config, monkeypatched CONFIG) | Gains an autouse fixture to cancel leaked threading.Timer instances (TD-050) | shallow |

---

## Q1 Diagram (from design)

These two diagrams show what happens *inside* the feature. They come directly from the design doc.

**Q1-A: Best-effort indexing inside the Save step**

```
# Index Layer — What Happens Inside (Capture Path)
Scope: Shows what happens AFTER a note finishes capture.
       Does NOT cover note deletion or renaming.

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

     Note finishes Capture Pipeline
     (7 existing steps complete)
               │
               ▼
     ┌───────────────────┐
     │ Save note to      │
     │ vault + database  │
     └────────┬──────────┘
              │
              ▼
     ┌───────────────────┐
     │ Build search text │
     │ (title + type +   │
     │ tags + summary)   │
     └────────┬──────────┘
              │
       ┌──────┴──────┐
       │             │
    SUCCESS       FAILURE
       │             │
       ▼             ▼
  ┌──────────┐  ┌──────────┐
  │ Write to │  │ Log      │
  │ Vector + │  │ warning, │
  │ Keyword  │  │ capture  │
  │ Search   │  │ still OK │
  └──────────┘  └──────────┘

Simplified: Two separate index writes (vector + keyword)
            grouped into one "Write to Search" box.
            Delete/rename cleanup not shown — piggybacks
            on existing File Index functions.
```

**Q1-B: Piggyback on File Index (maintenance path)**

```
# Index Layer — What Happens Inside (Maintenance Path)
Scope: Shows how search indexes stay clean when notes
       are deleted or moved. Does NOT show capture flow.

How to read this:
  Boxes  = components
  Arrows = what triggers what

     Note is deleted or moved
               │
               ▼
     ┌───────────────────┐
     │ File Index        │
     │ (already handles  │
     │  delete/rename)   │
     └────────┬──────────┘
              │
              │ same function also
              │ cleans search tables
              ▼
     ┌───────────────────┐
     │ Remove or update  │
     │ Vector + Keyword  │
     │ search entries    │
     └───────────────────┘

Simplified: "File Index" = existing component that tracks
            all notes in the database. Search cleanup added
            directly inside its delete/rename functions.
```

---

## Q2 Diagram — How it connects to others

```
# Index Layer — How It Connects
Scope: Shows what the Index Layer touches. Does NOT show
       internal steps (see Q1 above for that).

How to read this:
  Center box     = the feature being built
  Solid boxes    = components that already exist
  Dashed boxes   = planned, not built yet
  Arrow labels   = what passes between them

  ┌──────────────────┐                ┌──────────────────┐
  │ Capture Pipeline │                │ File Index       │
  │ Processes notes  │                │ Tracks all notes │
  └────────┬─────────┘                └────────┬─────────┘
           │                                   │
           │ calls after                       │ calls on
           │ save succeeds                     │ delete/rename
           │ (best-effort)                     │ (same transaction)
           │                                   │
           │     ┌─────────────────────────┐   │
           └────►│   INDEX LAYER           │◄──┘
                 │   Meaning Indexer       │
                 │   + Word Indexer        │
                 └──┬──────────┬──────────┘
                    │          │
      reads model   │          │ stores vectors
      name from     │          │ + keywords in
                    │          │
  ┌─────────────────┘          └──────────────────┐
  │                                               │
  ▼                                               ▼
  ┌──────────────────┐                ┌──────────────────┐
  │ Search Settings  │                │ Database         │
  │ Model names +    │                │ Connector        │
  │ query limits     │                │ Loads sqlite-vec │
  └──────────────────┘                │ on every connect │
                                      └────────┬─────────┘
                                               │
                                               │ applies
                                               │ migration 007
                                               ▼
                                      ┌──────────────────┐
                                      │ Schema Migrator  │
                                      │ Creates vector + │
                                      │ keyword tables   │
                                      └──────────────────┘
                                               │
                                               │ queried by
                                               ▼
                                      ┌ ─ ─ ─ ─ ─ ─ ─ ─┐
                                      │ Search API       │
                                      │ (Session B)      │
                                      └ ─ ─ ─ ─ ─ ─ ─ ─┘

Simplified: Dependency Manifest (adds sentence-transformers
            + sqlite-vec) and Test Fixtures (TD-050 timer
            cleanup) omitted — infrastructure-only changes.
```

---

## Feature overview

The Index Layer adds two new capabilities to the existing capture system, both invisible to the user:

**Happy path (capture).** When a note finishes the 7-stage capture pipeline and is saved to the vault and database, two extra calls fire inside the Save step. First, the Meaning Indexer builds a contextual string from the note's title, type tag, tags, and summary, runs it through a local AI model to produce a 384-number vector, and stores that vector in a dedicated SQLite table. Second, the Word Indexer inserts the note's title, summary, and body text into a full-text search table. If either call fails, a warning is logged and the capture still returns success -- the note is saved, just not yet searchable.

**Happy path (maintenance).** When a note is deleted, moved, or renamed through the existing File Index functions, those same functions now also clean up the corresponding search entries. This happens in the same database transaction, so the search tables never contain stale entries for notes that no longer exist.

**Edge cases:**
- If the embedding model is not available (first run, CI, disk space), the Meaning Indexer logs a warning and returns a recoverable failure. The note is captured without a vector.
- For binary files (PDF, DOCX), the sibling `.md` summary serves as the search proxy -- its content gets indexed, not the binary itself.
- If a transient SQLite error occurs during indexing, each indexer retries once immediately. If the retry also fails, it logs a warning and gives up.
- Notes without a summary (edge case) get a metadata-only contextual string for embedding -- title + type + tags, no summary suffix.

---

## Out of scope

- **Search API (query, rank, return results)** -- Session B. This phase only writes indexes; reading them is separate.
- **Reranker execution** -- `reranker_model` is stored in config for Session B but has no runtime use in Session A.
- **Reciprocal rank fusion** -- Session B.
- **MCP search tool** -- Phase 4, after Session B.
- **Backfill existing notes into search indexes** -- Deferred to Session B reconcile or a standalone CLI command (`kms reindex`). Notes captured after this phase ships are indexed; notes captured before are not.
- **Embedding model download/caching in CI** -- Resolved by environment variable `SENTENCE_TRANSFORMERS_HOME`; no custom CI infrastructure needed (see OQ-IDX-2 resolution below).

---

## Constraints

- **C-04: FK pragma on every connection** -- `_connect()` must still set `PRAGMA foreign_keys=ON` before extension loading. Source: CONSTRAINTS.md.
- **C-05: All schema changes via versioned .sql deltas** -- Migration 007 is a new numbered file. No in-code `CREATE TABLE`. Source: CONSTRAINTS.md.
- **C-12: Result returns** -- Both indexer public functions return `Result[None]`. Source: CONSTRAINTS.md.
- **C-13: Audit log for AI decisions** -- Indexing is deterministic (not an AI decision). No audit entry needed. Source: CONSTRAINTS.md.
- **C-17: No module-scope CONFIG in tests** -- Indexer tests use injected `db_path` parameter. Source: CONSTRAINTS.md.
- **Extension point rule** -- New retrieval modules (`embeddings.py`, `keyword.py`) are self-contained. No existing pipeline code gains new branches. The capture pipeline's Save step gains two try/except blocks, not conditional logic. Source: CLAUDE.md.
- **Hook: no direct vault writes** -- Indexers write to SQLite only, never to the vault. Source: `.claude/settings.json`.
- **`core/pipeline.py` cannot import from `vault.`** -- The indexer modules live in `src/retrieval/`, not in `core/pipeline.py`. No vault imports in pipeline.py. Source: CLAUDE.md gotchas.

---

## Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|-----------|-------------------|--------------------------|
| A1 | `_connect()` in `storage/db.py` is the single connection factory -- all database access goes through it or through `get_connection()` which calls it | Design Decision 4 (load on every connection) | Finding a `sqlite3.connect()` call elsewhere in the codebase that bypasses `_connect()` |
| A2 | `documents.upsert()` returns `Success(rowid)` -- the rowid is available to pass to indexers | Design Decision 1 (wire after upsert) | `upsert()` returning `Success(None)` or not exposing the vault_path needed by indexers |
| A3 | `delete_by_path()`, `rename()`, and `replace_path()` each open their own connection via `get_connection()` -- adding SQL statements inside them is safe within the same transaction | Design Decision 2 (piggyback) | These functions using a shared connection passed from outside, which would change transaction boundaries |
| A4 | `sentence-transformers` `all-MiniLM-L6-v2` model produces exactly 384-dimensional float32 vectors | Design build target 4.4 | Model producing a different dimension count |
| A5 | `sqlite-vec` v0.1.9 `vec0` virtual table supports text primary keys and DELETE by primary key | OQ-IDX-1 resolution | `vec0` not supporting text PKs or DELETE |
| A6 | FTS5 virtual tables have no UNIQUE constraint -- duplicate vault_path entries accumulate unless explicitly deleted before insert | OQ-IDX-3 resolution | FTS5 enforcing uniqueness on a column |
| A7 | `vec0` does NOT support `INSERT OR REPLACE` -- must use DELETE + INSERT pattern | OQ-IDX-1 resolution | `INSERT OR REPLACE` working on vec0 tables |
| A8 | `vec0` does NOT support `UPDATE` on primary key columns -- rename requires DELETE + INSERT with embedding copy | OQ-IDX-1 resolution | `UPDATE` on vec0 PKs working |
| A9 | At each upsert site, `WriteOutcome.vault_path` is available and `MetadataResult` (`mr`) provides `ai_title`, `ai_type`, `ai_tags`, and `summary` for indexer arguments | Design Decision 1 wiring | Any of these fields missing at a call site |

---

## Open question resolutions

**OQ-IDX-1: Exact vec0 CREATE TABLE syntax.**
Resolved by testing sqlite-vec v0.1.9. Syntax: `CREATE VIRTUAL TABLE embeddings_vec USING vec0(vault_path text primary key, embedding float[384])`. Critical finding: `INSERT OR REPLACE` raises `UNIQUE constraint failed` -- must use DELETE + INSERT. `UPDATE` on primary key also not supported. `UPDATE` on embedding column works. `DELETE FROM ... WHERE vault_path = ?` works.

**OQ-IDX-2: SentenceTransformer model caching in CI.**
Resolved: set environment variable `SENTENCE_TRANSFORMERS_HOME` to a persistent cache directory. First run downloads ~90MB; subsequent runs use the cache. Tests that don't need real embeddings mock the model -- no download needed. No vendored model.

**OQ-IDX-3: FTS5 content table vs contentless.**
Resolved: use a content table (not contentless). Rationale: content tables support `DELETE FROM ... WHERE vault_path = ?` directly. Contentless tables require an external content table and explicit rowid management -- unnecessary complexity for our scale. Storage cost is negligible (summary + body text per note).

**OQ-IDX-4: Embedding dimension / model coupling.**
Resolved: 384 is hardcoded in migration 007 and coupled to `all-MiniLM-L6-v2` in SearchConfig. Changing the model requires a new migration that drops and recreates the table. This coupling is acceptable -- model changes are infrequent. Document the coupling in a `# COUPLING:` comment in migration 007.

**OQ-IDX-5: Best-effort retry semantics.**
Resolved: retry once immediately (no delay) on `sqlite3.OperationalError` only. This covers transient locks (WAL contention, busy timeout). All other exceptions are not retried. If the retry also raises, return `Failure(recoverable=True)`. The capture pipeline's try/except catches all exceptions, so even a non-retried failure is swallowed with a warning.

**OQ-IDX-6: Body text source for keyword indexer.**
Resolved: in `_store_md()`, body text is the original markdown body from `read_note()`. In `_store_nonmd()`, body text is the rich sibling summary produced by the `summarize_attachment` prompt. This is correct -- the sibling IS the search proxy for binaries. The spec tests both paths explicitly.

---

## Component dependency order

### 1. Migration 007 — Search tables schema

**Goal.** Create the two virtual tables that store search data: one for vector embeddings (meaning search) and one for full-text keywords.

**Build.** Create file `src/storage/migrations/007_search_indexes.sql` containing:

```sql
-- Migration 007: Search indexes (P3-IDX-09)
-- COUPLING: embeddings_vec float[384] is coupled to the all-MiniLM-L6-v2 model
-- in SearchConfig. Changing the embedding model requires a new migration.
CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_vec USING vec0(
    vault_path text primary key,
    embedding float[384]
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    vault_path,
    title,
    summary,
    body
);
```

**Depends on.** None.

**Assumes.** A5 (vec0 supports text PKs), A7 (vec0 does not support INSERT OR REPLACE).

**Done when.** Running `init_db()` on a fresh database creates both `embeddings_vec` and `notes_fts` tables. Running `init_db()` again on the same database does not error. `schema_version` reads 7.

---

### 2. sqlite-vec extension loading in Database Connector

**Goal.** Every database connection can query vector tables without the caller needing to load the extension manually.

**Build.** Modify `_connect()` in `src/storage/db.py`. After the existing `PRAGMA foreign_keys=ON` line, add:

```python
conn.enable_load_extension(True)
import sqlite_vec
sqlite_vec.load(conn)
conn.enable_load_extension(False)  # re-lock after loading
```

The `enable_load_extension(False)` call re-disables extension loading after sqlite-vec is loaded, following security best practice.

**Depends on.** Component 1 (migration must exist for tables to be queryable, but extension loading is independent).

**Assumes.** A1 (_connect is the single connection factory).

**Done when.** Opening a connection via `get_connection()` and querying `SELECT vec_version()` returns a version string without error. The existing `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` still work.

---

### 3. Search Settings in Settings Manager

**Goal.** Provide a typed config section for search-related settings so the embedding model name, reranker model, and query limits are configurable without code changes.

**Build.** Add to `src/core/config.py`:

```python
class SearchConfig(BaseModel):
    embedding_model: str = "all-MiniLM-L6-v2"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    max_candidates: int = 50
    max_results: int = 10
```

Add to `MainConfig`:
```python
search: SearchConfig = Field(default_factory=SearchConfig)
```

Add to `src/config/config.yaml`:
```yaml
search:
  embedding_model: all-MiniLM-L6-v2
  reranker_model: cross-encoder/ms-marco-MiniLM-L-6-v2
  max_candidates: 50
  max_results: 10
```

**Depends on.** None.

**Done when.** `CONFIG.main.search.embedding_model` returns `"all-MiniLM-L6-v2"`. Overriding the value in `config.yaml` changes the returned value. All four fields have working defaults.

---

### 4. Dependencies in Dependency Manifest

**Goal.** Make `sentence-transformers` and `sqlite-vec` available at runtime.

**Build.** Add to `pyproject.toml` `[project] dependencies`:
```
"sentence-transformers>=2.2.0",
"sqlite-vec>=0.1.0",
```

Run `uv sync` to install.

**Depends on.** None.

**Done when.** `import sentence_transformers` and `import sqlite_vec` succeed in a Python shell after `uv sync`.

---

### 5. Meaning Indexer

**Goal.** Convert a note's metadata and summary into a 384-dimensional vector embedding and store it in the `embeddings_vec` table.

**Build.** Create `src/retrieval/__init__.py` (re-exports) and `src/retrieval/embeddings.py`.

Public function signature (verified against real code -- `WriteOutcome` has `vault_path: str` and `metadata: NoteMetadata`):

```python
def index_embedding(
    vault_path: str,
    title: str,
    note_type: str | None,
    tags: list[str],
    summary: str | None,
    db_path: Path | None = None,
) -> Result[None]:
```

Internal behavior:
1. **Lazy-load model.** Module-level `_model: SentenceTransformer | None = None`. On first call, load `SentenceTransformer(CONFIG.main.search.embedding_model)` if `_model` is None. Cache at module level.
2. **Build contextual string.** Format: `"title: {title} | type: {type_tag} | tags: {tags_csv} | {summary}"`. If summary is None or empty, omit the summary suffix. Tags are comma-separated, filtered to exclude `type/` prefix tags (already in the type field).
3. **Encode.** `_model.encode(contextual_string)` returns a numpy array. Convert to `float32` bytes via `.astype(np.float32).tobytes()`.
4. **Upsert.** DELETE existing row for this vault_path, then INSERT. (vec0 does not support INSERT OR REPLACE -- see A7.)
5. **Retry.** On `sqlite3.OperationalError`, retry once immediately. On second failure, return `Failure(error=str(exc), recoverable=True, context={"vault_path": vault_path, "op": "index_embedding"})`.
6. **Any other exception** (model load failure, encoding error): return `Failure(recoverable=True)`.

**Depends on.** Components 1 (table exists), 2 (sqlite-vec loaded), 3 (model name from config), 4 (sentence-transformers installed).

**Assumes.** A4 (384 dimensions), A5 (text PKs), A7 (no INSERT OR REPLACE).

**Interface shape.** Callers pass individual fields (not a WriteOutcome or NoteMetadata) to avoid coupling the retrieval module to vault types. The capture pipeline extracts the fields and passes them.

**Dependency category.** In-process (test directly with an in-memory SQLite database and a mocked SentenceTransformer).

**Decisions.**
- Q: Should `index_embedding` accept a `WriteOutcome` directly or individual fields? Leaning individual fields because `core/pipeline.py` cannot import from `vault.` (CLAUDE.md gotcha), and keeping the retrieval module decoupled from vault types is cleaner. The capture pipeline extracts the 5 fields and passes them.
- Q: Should the model be loaded from config or hardcoded? Leaning config (`CONFIG.main.search.embedding_model`) because the extension point rule requires config-driven behavior. Lazy import of CONFIG inside the function avoids module-scope CONFIG (C-17).

**Done when.** After capturing a note, querying `SELECT vault_path FROM embeddings_vec WHERE vault_path = ?` returns one row. The stored embedding is exactly 384 floats (1536 bytes). Two notes with similar summaries produce vectors closer together (cosine similarity) than two notes with unrelated summaries.

---

### 6. Word Indexer

**Goal.** Insert a note's title, summary, and body text into the `notes_fts` full-text search table so it is findable by keyword.

**Build.** Create `src/retrieval/keyword.py`.

Public function signature:

```python
def index_keywords(
    vault_path: str,
    title: str,
    summary: str,
    body: str,
    db_path: Path | None = None,
) -> Result[None]:
```

Internal behavior:
1. **DELETE existing row** for this vault_path. FTS5 has no UNIQUE constraint (A6), so without a delete, duplicate rows accumulate.
2. **INSERT** `(vault_path, title, summary, body)` into `notes_fts`.
3. Both statements in the same transaction (single `get_connection()` context manager).
4. **Retry.** Same retry logic as the Meaning Indexer: retry once on `sqlite3.OperationalError`, then `Failure(recoverable=True)`.

**Depends on.** Component 1 (table exists).

**Assumes.** A6 (FTS5 no UNIQUE).

**Interface shape.** Individual string fields. Same rationale as Meaning Indexer -- decoupled from vault types.

**Dependency category.** In-process (test directly with in-memory SQLite).

**Done when.** After capturing a note containing the word "stakeholder", querying `SELECT vault_path FROM notes_fts WHERE notes_fts MATCH 'stakeholder'` returns the note's vault_path.

---

### 7. Wiring indexers into Capture Pipeline Save step

**Goal.** Every successful note capture also indexes the note for search, without blocking the capture on indexer failure.

**Build.** Modify `src/pipelines/capture.py`. Add best-effort indexing calls at every site where `documents.upsert()` or `documents.replace_path()` returns `Success`. There are 4 call sites:

1. **`_store_md` rename path** (line ~975): after `documents.replace_path()` succeeds
2. **`_store_md` in-place path** (line ~1007): after `documents.upsert()` succeeds
3. **`_store_nonmd` LOCATED path** (line ~1176): after `documents.upsert()` succeeds
4. **`_store_nonmd` CLUELESS path** (line ~1280): after `documents.upsert()` succeeds

At each site, add:

```python
# Best-effort search indexing (P3-IDX-01, P3-IDX-02)
try:
    from retrieval.embeddings import index_embedding
    from retrieval.keyword import index_keywords

    _title = mr.ai_title or Path(outcome.vault_path).stem
    index_embedding(
        vault_path=outcome.vault_path,
        title=_title,
        note_type=mr.ai_type,
        tags=mr.ai_tags,
        summary=mr.summary,
        db_path=ctx.db_path,
    )
except Exception:
    logger.warning("store.embedding_index_failed", vault_path=outcome.vault_path)

try:
    from retrieval.keyword import index_keywords

    _title = mr.ai_title or Path(outcome.vault_path).stem
    # Body source differs: original body for .md, rich sibling body for non-.md
    _body = <body text variable at this call site>
    index_keywords(
        vault_path=outcome.vault_path,
        title=_title,
        summary=mr.summary or "",
        body=_body,
        db_path=ctx.db_path,
    )
except Exception:
    logger.warning("store.keyword_index_failed", vault_path=outcome.vault_path)
```

**Body text source by call site:**
- `_store_md` (both paths): `original_body` (the markdown body from `read_note()`)
- `_store_nonmd` LOCATED: `rich_body` (the AI-generated sibling summary)
- `_store_nonmd` CLUELESS: `rich_body` (the AI-generated or missing-file fallback body)

**Depends on.** Components 5 (Meaning Indexer) and 6 (Word Indexer).

**Assumes.** A9 (WriteOutcome contains required fields), A2 (upsert returns Success with rowid).

**Done when.** After `kms capture <file>`, both `embeddings_vec` and `notes_fts` contain a row for the captured note. If the embedding model is mocked to fail, the capture still returns `Success` and the `documents` table has the note, but `embeddings_vec` has no row. A warning is logged.

---

### 8. Index maintenance in File Index

**Goal.** When a note is deleted, renamed, or replaced, its search entries are automatically cleaned up in the same database transaction.

**Build.** Modify `src/storage/documents.py`. Three functions gain search-table cleanup:

**`delete_by_path(vault_path, db_path)`** -- add before the documents DELETE (so the search entries are removed in the same transaction):

```python
# Search index cleanup (P3-IDX-05)
conn.execute("DELETE FROM embeddings_vec WHERE vault_path = ?", (vault_path,))
conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (vault_path,))
```

**`rename(old, new, db_path)`** -- add after the documents UPDATE:

```python
# Search index rename (P3-IDX-06)
# vec0 does not support UPDATE on primary key -- must DELETE + INSERT with embedding copy
row = conn.execute("SELECT embedding FROM embeddings_vec WHERE vault_path = ?", (old,)).fetchone()
if row:
    conn.execute("DELETE FROM embeddings_vec WHERE vault_path = ?", (old,))
    conn.execute("INSERT INTO embeddings_vec(vault_path, embedding) VALUES (?, ?)", (new, row[0]))
# FTS5: DELETE old + re-INSERT with new vault_path (FTS5 does not support UPDATE)
fts_row = conn.execute("SELECT title, summary, body FROM notes_fts WHERE vault_path = ?", (old,)).fetchone()
if fts_row:
    conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (old,))
    conn.execute("INSERT INTO notes_fts(vault_path, title, summary, body) VALUES (?, ?, ?, ?)", (new, *fts_row))
```

**`replace_path(old_vault_path, outcome, db_path, batch_id)`** -- add search cleanup for the old path before the documents DELETE:

```python
# Search index cleanup for replaced path (P3-IDX-05)
conn.execute("DELETE FROM embeddings_vec WHERE vault_path = ?", (old_vault_path,))
conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (old_vault_path,))
```

Note: `replace_path` does DELETE old + INSERT new for documents. The new path's search entries will be created by the capture pipeline's best-effort indexing (Component 7), not by `replace_path` itself. This avoids duplicating embedding computation logic in the data access layer.

**Depends on.** Component 1 (tables exist), Component 2 (sqlite-vec loaded for vec0 queries).

**Assumes.** A3 (functions own their connection), A8 (vec0 PK UPDATE not supported).

**Done when.** After deleting a note via `delete_by_path()`, both `embeddings_vec` and `notes_fts` have zero rows for that vault_path. After renaming via `rename()`, the old path has zero rows and the new path has one row in each table. The embedding bytes are preserved through the rename.

---

### 9. TD-050 — Timer leak cleanup fixture

**Goal.** Prevent watcher tests from leaking `threading.Timer` instances that fire during unrelated tests, causing flaky failures.

**Build.** Add to `tests/test_vault/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _cancel_leaked_timers():
    """Cancel any threading.Timer instances leaked by watcher debounce (TD-050)."""
    yield
    import threading
    for t in threading.enumerate():
        if isinstance(t, threading.Timer):
            t.cancel()
```

**Depends on.** None.

**Done when.** The flaky test `test_no_edit_pdf_cross_folder_rehome` no longer fails with `assert len(move_note_calls) == 1` reporting `2` when the full suite runs. Running `pytest tests/test_vault/` 10 times in a row produces zero flaky failures from timer leaks.

---

## Test strategy

### Unit tests for new modules

| Component | Test approach | Key test cases |
|---|---|---|
| Meaning Indexer | In-memory SQLite with sqlite-vec loaded; mock `SentenceTransformer` to return a fixed 384-dim vector | Insert, upsert (DELETE+INSERT), retry on OperationalError, failure returns Failure(recoverable=True), contextual string format verified, no-summary fallback |
| Word Indexer | In-memory SQLite with FTS5 | Insert, DELETE+INSERT (no duplicates), FTS5 MATCH query finds the note, retry on OperationalError |
| SearchConfig | Standard Pydantic model test | Defaults correct, YAML override works |
| Migration 007 | `init_db()` on fresh DB | Both tables exist, schema_version=7, idempotent (second init does not error) |

### Integration tests in capture pipeline

| Behavior | Test approach |
|---|---|
| P3-IDX-01 (vector-searchable after capture) | Capture a note with mocked LLM, verify `embeddings_vec` row exists |
| P3-IDX-02 (keyword-searchable after capture) | Capture a note with distinctive keyword, verify `notes_fts MATCH` returns it |
| P3-IDX-03 (embedding failure does not block) | Mock `index_embedding` to raise, verify capture returns Success |
| P3-IDX-04 (keyword failure does not block) | Mock `index_keywords` to raise, verify capture returns Success |

### Maintenance tests in File Index

| Behavior | Test approach |
|---|---|
| P3-IDX-05 (delete removes search entries) | Insert doc + search entries, call `delete_by_path`, verify zero rows in both search tables |
| P3-IDX-06 (rename updates search entries) | Insert doc + search entries, call `rename`, verify old path gone and new path present |
| P3-IDX-08 (sqlite-vec loaded) | Open connection via `get_connection`, run `SELECT vec_version()` |

### Test fixture requirements

- All tests that touch the database need sqlite-vec loaded. Since `_connect()` now loads it unconditionally, any test using `get_connection()` or `init_db()` gets it automatically.
- Tests mocking `SentenceTransformer` should patch `retrieval.embeddings._model` or use dependency injection via the module-level cache.
- The TD-050 autouse fixture applies to all tests in `tests/test_vault/`.

---

## Acceptance criteria mapped to behavior inventory

| Behavior ID | Criterion | Component |
|---|---|---|
| P3-IDX-01 | After `kms capture <file>`, a row exists in `embeddings_vec` with the note's vault_path and a 384-dimension embedding | 5, 7 |
| P3-IDX-02 | After `kms capture <file>`, a FTS5 MATCH query on a keyword from the note's body returns the note's vault_path | 6, 7 |
| P3-IDX-03 | When the embedding model is unavailable/mocked-to-fail, capture returns `Success(WriteOutcome)` and `documents` row exists, but `embeddings_vec` has no row | 7 |
| P3-IDX-04 | When FTS5 insert is mocked to fail, capture returns `Success(WriteOutcome)` and `documents` row exists, but `notes_fts` has no row | 7 |
| P3-IDX-05 | After `delete_by_path()`, zero rows exist in `embeddings_vec` and `notes_fts` for the deleted vault_path | 8 |
| P3-IDX-06 | After `rename()`, zero rows exist for the old path and one row exists for the new path in both `embeddings_vec` and `notes_fts` | 8 |
| P3-IDX-07 | The string passed to `SentenceTransformer.encode()` contains the note's title, type tag, tags, and summary in a deterministic prefix format | 5 |
| P3-IDX-08 | Opening a connection via `get_connection()` and running `SELECT vec_version()` succeeds | 2 |
| P3-IDX-09 | Running `init_db()` on fresh DB creates both tables; running again does not error; `schema_version` is 7 | 1 |
| P3-IDX-10 | `CONFIG.main.search.embedding_model` returns `"all-MiniLM-L6-v2"` with defaults; all 4 fields are overridable via YAML | 3 |

---

## Tech debt resolved by this phase

| TD | Resolution |
|---|---|
| TD-004 | Embeddings table + FTS5 virtual table -- RESOLVED by migration 007 |
| TD-050 | Watcher timer leak -- RESOLVED by autouse fixture in `tests/test_vault/conftest.py` |

---

## Handoff notes

- **Contract with Session B:** This phase delivers two populated tables (`embeddings_vec`, `notes_fts`) and a `SearchConfig` with `max_candidates` and `max_results`. Session B builds the query API on top: KNN query on `embeddings_vec`, FTS5 MATCH on `notes_fts`, reciprocal rank fusion, reranker, and the `kms search` CLI command.
- **Contract with Phase 4 (MCP):** The MCP search tool calls Session B's search API. No direct dependency on Session A.
- **Open uncertainty: vec0 table rebuild on model change.** If the embedding model is changed in config, existing vectors become incompatible (different dimension or different semantic space). A model change requires dropping and recreating `embeddings_vec` plus a full re-index. This is documented via the `# COUPLING:` comment in migration 007 but has no automated enforcement. A future `kms reindex` command (Session B scope) would handle this.
- **Open uncertainty: sentence-transformers download size.** First import of `sentence-transformers` pulls ~500MB of PyTorch dependencies. This is acceptable for the target user (local Mac install) but may be a concern for lightweight deployments. No action needed now -- flagged for awareness.
- **Suggested research:** Before detailed planning, verify that the `replace_path` function's transaction boundary allows adding DELETE statements for search tables alongside the existing DELETE+INSERT for documents. The function uses a single `get_connection()` context manager, so this should work, but the planner should confirm by reading the actual code path.
- **Deferred: TD-013 (embedding_model field on provider configs).** This spec uses `SearchConfig.embedding_model` for `sentence-transformers` (local model), not the provider's `embedding_model` field (remote API). TD-013 remains open for future provider-hosted embeddings.
