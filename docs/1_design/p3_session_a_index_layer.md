# Phase 3 Session A — Index Layer Design

**Status:** Design complete, pending spec
**Date:** 2026-06-10
**Scope:** Search infrastructure — vector + keyword indexes, wiring into capture pipeline, index maintenance
**Does NOT cover:** Query/search API (Session B), reranker, MCP search tool, reciprocal rank fusion

---

## 1. What we are building (plain English)

After a note is captured and saved, the system will also index it for two kinds of search:

1. **Vector search** — finding notes with similar meaning ("show me notes related to AI strategy") using a local AI model that converts text into numbers (embeddings)
2. **Keyword search** — finding notes with exact words ("find notes mentioning 'Q2 revenue'") using SQLite's built-in full-text search

When notes are deleted, moved, or renamed, the search indexes are cleaned up automatically. If indexing fails for any reason, the note is still captured successfully — search indexing is best-effort.

---

## 2. Pre-flight fixes (must land before build targets)

| ID | Fix | Rationale |
|---|---|---|
| I-1 | Use migration 007, not 006 | 006 already exists (batches folder_path) |
| I-2 | Add `search:` section to config.yaml + SearchConfig model | Config surface for embedding_model, reranker_model, max_candidates, max_results |
| I-3 | reranker_model in SearchConfig | Prepared for Session B; no runtime use in Session A |
| I-4 | max_candidates / max_results in SearchConfig | Prepared for Session B query API |
| I-5 | Add sentence-transformers to pyproject.toml | Vector embedding dependency |
| I-6 | Add sqlite-vec to pyproject.toml + wire into _connect() | Vector similarity search extension for SQLite |
| TD-019 | Verify resolved (validate_tags called in capture.py) | Already confirmed — no work needed |
| TD-050 | Autouse fixture in tests/test_vault/conftest.py for timer leak | Cancel leaked threading.Timer instances |

---

## 3. Design decisions

### Decision 1: Where to wire indexers in capture.py

**Chosen: Inside store() as best-effort calls (Option A)**

After `documents.upsert()` or `documents.replace_path()` succeeds, two try/except calls fire:
1. `index_embedding(outcome, ctx)` — builds contextual string, computes vector, upserts
2. `index_keywords(outcome, body_text, ctx)` — INSERT OR REPLACE into notes_fts

If either fails, a warning is logged. The pipeline returns the original Success(WriteOutcome) unchanged.

**Why not separate pipeline stages?** `run_pipeline` halts on first Failure. Making stages that never fail (wrapping Failure in Success) is a lie — it defeats the pipeline pattern. Best-effort calls inside store() are simpler, more honest, and easier to test.

```
# Decision 1A — Best-effort indexing inside Save step
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

### Decision 2: How to handle index maintenance

**Chosen: Piggyback on documents.py (Option 2A)**

Extend `delete_by_path()`, `rename()`, and `replace_path()` to also clean/update `embeddings_vec` and `notes_fts` within the same database transaction.

**Why not watcher hooks?** Atomicity and completeness. Every caller of `delete_by_path()` gets search cleanup for free. Watcher hooks would create a parallel cleanup path with gaps (direct `documents.py` calls from `scan_capture` would miss watcher cleanup).

```
# Decision 2A — Piggyback on File Index
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

### Decision 3: Package structure for src/retrieval/

**Chosen: Flat modules (Option 3A)**

```
src/retrieval/
├── __init__.py       # re-exports index_embedding, index_keywords
├── embeddings.py     # SentenceTransformer lazy-load, contextual string, upsert
└── keyword.py        # FTS5 INSERT OR REPLACE
```

**Why not shared base?** Two indexers. No evidence of a third. CLAUDE.md §3: "No speculative flexibility, no abstractions for single-use code." Extracting a Protocol later is a 15-minute refactor.

### Decision 4: sqlite-vec extension loading

**Chosen: Load on every connection (Option 4A)**

`_connect()` unconditionally loads sqlite-vec after setting WAL mode and FK pragma. ~1ms overhead per connection. Zero bug surface — no "forgot to pass a flag" failures.

**Why not lazy?** Locked requirement says "on every connection." A `load_vec` parameter creates a new failure mode ("no such module: vec0") that would surface only at query time. Premature optimization for negligible savings.

---

## 4. Build targets

### 4.1 Migration 007

File: `src/storage/migrations/007_search_indexes.sql`

Creates two virtual tables:
- `embeddings_vec` via sqlite-vec (`vec0`): vault_path TEXT PRIMARY KEY, embedding float[384]
- `notes_fts` via FTS5: vault_path, title, summary, body (content table)

### 4.2 SearchConfig

File: `src/core/config.py`

```python
class SearchConfig(BaseModel):
    embedding_model: str = "all-MiniLM-L6-v2"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    max_candidates: int = 50
    max_results: int = 10
```

Added to MainConfig: `search: SearchConfig = Field(default_factory=SearchConfig)`

Config.yaml section:
```yaml
search:
  embedding_model: all-MiniLM-L6-v2
  reranker_model: cross-encoder/ms-marco-MiniLM-L-6-v2
  max_candidates: 50
  max_results: 10
```

### 4.3 sqlite-vec in _connect()

```python
def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.enable_load_extension(True)
    import sqlite_vec
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)  # re-lock after loading
    return conn
```

### 4.4 Embedding Indexer

File: `src/retrieval/embeddings.py`

Public function: `index_embedding(outcome: WriteOutcome, metadata: NoteMetadata, db_path: Path | None = None) -> Result[None]`

Internals:
- Lazy-load SentenceTransformer on first call (module-level `_model: SentenceTransformer | None = None`)
- Build contextual string: `f"title: {title} | type: {type_tag} | tags: {tags_csv} | {summary}"`
- `model.encode(contextual_string)` → 384-dim numpy array
- Serialize to bytes → INSERT OR REPLACE into embeddings_vec
- Retry once on transient sqlite3.OperationalError, then return Failure(recoverable=True)

### 4.5 Keyword Indexer

File: `src/retrieval/keyword.py`

Public function: `index_keywords(vault_path: str, title: str, summary: str, body: str, db_path: Path | None = None) -> Result[None]`

Internals:
- DELETE FROM notes_fts WHERE vault_path = ? (FTS5 does not support UPDATE)
- INSERT INTO notes_fts (vault_path, title, summary, body) VALUES (?, ?, ?, ?)
- Both in same transaction
- Retry once on transient sqlite3.OperationalError, then return Failure(recoverable=True)

### 4.6 Wiring in capture.py store()

After every successful `documents.upsert()` or `documents.replace_path()` in both `_store_md()` and `_store_nonmd()`, add:

```python
# Best-effort search indexing (P3-IDX-01, P3-IDX-02)
try:
    from retrieval.embeddings import index_embedding
    index_embedding(outcome, note_meta, db_path=ctx.db_path)
except Exception:
    logger.warning("store.embedding_index_failed", vault_path=outcome.vault_path)

try:
    from retrieval.keyword import index_keywords
    index_keywords(outcome.vault_path, title, note_meta.summary or "", body, db_path=ctx.db_path)
except Exception:
    logger.warning("store.keyword_index_failed", vault_path=outcome.vault_path)
```

### 4.7 Index maintenance in documents.py

**delete_by_path()**: Add before the documents DELETE:
```python
conn.execute("DELETE FROM embeddings_vec WHERE vault_path = ?", (vault_path,))
conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (vault_path,))
```

**rename()**: FTS5 does not support UPDATE on rowid tables, so:
```python
# embeddings_vec: UPDATE vault_path
conn.execute("UPDATE embeddings_vec SET vault_path = ? WHERE vault_path = ?", (new, old))
# notes_fts: DELETE + re-INSERT (FTS5 limitation)
row = conn.execute("SELECT title, summary, body FROM notes_fts WHERE vault_path = ?", (old,)).fetchone()
if row:
    conn.execute("DELETE FROM notes_fts WHERE vault_path = ?", (old,))
    conn.execute("INSERT INTO notes_fts (vault_path, title, summary, body) VALUES (?, ?, ?, ?)", (new, *row))
```

**replace_path()**: Already does DELETE old + INSERT new. Add search table DELETE for old path + fresh index for new path.

### 4.8 Dependencies (pyproject.toml)

```toml
dependencies = [
    # ... existing ...
    "sentence-transformers>=2.2.0",
    "sqlite-vec>=0.1.0",
]
```

### 4.9 TD-050 fixture

File: `tests/test_vault/conftest.py`

```python
@pytest.fixture(autouse=True)
def _cancel_leaked_timers():
    """Cancel any threading.Timer instances leaked by watcher debounce."""
    yield
    import threading
    for t in threading.enumerate():
        if isinstance(t, threading.Timer):
            t.cancel()
```

---

## 5. Constraints satisfied

| ID | Rule | How satisfied |
|---|---|---|
| C-04 | FK pragma on every connection | _connect() still sets FK pragma before extension load |
| C-05 | Versioned migrations only | Migration 007 is a new numbered file |
| C-12 | Result returns | All indexer public functions return Result[None] |
| C-13 | Audit log for AI decisions | Indexing is not an AI decision — no audit needed |
| C-17 | No module-scope CONFIG in tests | Indexer tests use injected db_path parameter |

---

## 6. Success criteria

10 behaviors registered in `docs/system_behavior/behavior_inventory.yaml` as P3-IDX-01 through P3-IDX-10.

| ID | One-line |
|---|---|
| P3-IDX-01 | Captured note is vector-searchable |
| P3-IDX-02 | Captured note is keyword-searchable |
| P3-IDX-03 | Embedding failure does not block capture |
| P3-IDX-04 | Keyword failure does not block capture |
| P3-IDX-05 | Delete removes search entries |
| P3-IDX-06 | Rename updates search entries |
| P3-IDX-07 | Embedding encodes metadata context, not just raw text |
| P3-IDX-08 | sqlite-vec loaded on every connection |
| P3-IDX-09 | Migration 007 creates both tables idempotently |
| P3-IDX-10 | SearchConfig provides all fields with defaults |

---

## 7. Tech debt resolved

| TD | Status |
|---|---|
| TD-004 | Embeddings table + FTS5 — RESOLVED by migration 007 |
| TD-050 | Watcher timer leak — RESOLVED by autouse fixture |

---

## 8. Open questions (deferred to spec or implementation)

**OQ-IDX-1: Exact sqlite-vec virtual table syntax.**
The `vec0` module's CREATE VIRTUAL TABLE syntax varies by version. The exact column definition for `embedding float[384]` must be verified against the installed sqlite-vec version during spec. If the syntax is `CREATE VIRTUAL TABLE embeddings_vec USING vec0(vault_path text primary key, embedding float[384])`, confirm it supports INSERT OR REPLACE or if DELETE+INSERT is required (like FTS5).

**OQ-IDX-2: SentenceTransformer model caching in CI.**
First load downloads ~90MB model from HuggingFace. CI environments need either pre-cached models or network access. Spec should define the caching strategy (environment variable `SENTENCE_TRANSFORMERS_HOME` or vendored model).

**OQ-IDX-3: FTS5 content table vs contentless.**
FTS5 supports "content" tables (stores original text, supports DELETE by rowid) and "contentless" tables (smaller, but DELETE requires external content table). The design assumes a content table for simplicity. If storage size becomes a concern, contentless with external content is an optimization for later.

**OQ-IDX-4: Embedding dimension lock.**
The 384 dimension is hardcoded in the migration and coupled to the model name in SearchConfig. If the model changes, the migration must also change (or the table must be rebuilt). This coupling should be documented but not over-engineered — model changes are infrequent and warrant a new migration anyway.

**OQ-IDX-5: Best-effort retry semantics.**
The design says "retry once" for both indexers. The exact retry trigger (which sqlite3 errors are transient?) and delay (immediate vs 100ms backoff) should be decided at spec time.

**OQ-IDX-6: Body text source for keyword indexer.**
In `_store_md()`, the original body is available from `read_note()`. In `_store_nonmd()`, the body is the rich sibling summary. The keyword indexer receives different content depending on file type. This is correct behavior (sibling IS the search proxy for binaries) but should be explicitly tested.
