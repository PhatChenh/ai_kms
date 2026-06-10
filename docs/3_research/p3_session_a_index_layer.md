# Research: P3 Session A -- Index Layer + Pre-flight Fixes
_Last updated: 2026-06-10_

## Overview

The Index Layer adds invisible search infrastructure to the existing capture system. Every captured note gets two search entries (a vector embedding for meaning-based search and a keyword entry for text-based search), both written as best-effort calls that never block the capture pipeline. When notes are deleted, moved, or renamed, their search entries are cleaned up inside the same database transaction.

This research verified 9 assumptions from the spec against the actual codebase. 3 assumptions are validated (A1, A2, A3 -- code confirms what the spec claims), 1 is partially invalidated (A9 -- the WriteOutcome title claim is misleading but the spec's wiring code compensates correctly), and 5 are unverifiable from code alone (A4-A8 -- external library behavior claims for sqlite-vec and sentence-transformers). No assumption requires architectural redesign. All additional verifications passed.

---

## Key Components

These are the files the spec touches, with their current roles confirmed by reading the actual source.

| File | Role | Spec usage |
|---|---|---|
| `src/storage/db.py` | Single database connection factory + migration runner | Gains sqlite-vec extension loading in `_connect()` |
| `src/storage/documents.py` | Data access layer for `documents` table (upsert, delete, rename, replace_path) | Three functions gain search-table cleanup SQL |
| `src/core/config.py` | Typed config singleton via Pydantic models | Gains `SearchConfig` on `MainConfig` |
| `src/config/config.yaml` | YAML config on disk | Gains `search:` section |
| `src/pipelines/capture.py` | 7-stage capture pipeline (2044 lines) | 4 call sites gain best-effort indexing after upsert |
| `src/vault/writer.py` | Atomic vault writes; defines `WriteOutcome` dataclass | Not modified; WriteOutcome structure confirmed |
| `src/vault/frontmatter.py` | `NoteMetadata` typed wrapper; `title` is NOT a known key | Not modified; title routing understood |
| `src/storage/migrations/` | Numbered .sql files (currently 001-006) | Gains migration 007 |
| `pyproject.toml` | Dependency manifest | `sqlite-vec` already present; `sentence-transformers` needs adding |
| `tests/test_vault/conftest.py` | Shared vault test fixtures | Gains TD-050 timer cleanup autouse fixture |

---

## How It Works

The feature integrates into two existing paths:

**Capture path.** When the capture pipeline finishes its 7 stages and calls `store()`, the store function writes the note to the vault and calls `documents.upsert()` (or `documents.replace_path()` for renames). After a successful database write, two new best-effort calls fire: `index_embedding()` builds a contextual string from the note's title, type, tags, and summary, runs it through a local AI model, and stores the resulting 384-number vector in the `embeddings_vec` table. `index_keywords()` inserts the note's title, summary, and body text into the `notes_fts` full-text search table. If either call fails, the capture still succeeds -- a warning is logged.

**Maintenance path.** When `delete_by_path()`, `rename()`, or `replace_path()` in `documents.py` run, they now also execute DELETE (and for rename, re-INSERT) statements on both search tables within the same `get_connection()` context manager, keeping search entries consistent with the documents table.

---

## Spec Verification

Each assumption was verified by reading the actual source code. Verdicts use three categories: Validated (code confirms the claim), Invalidated (code contradicts the claim), or Unverifiable (cannot be determined from code alone -- requires runtime testing of external libraries).

| ID | Spec Claim | Verdict | Evidence |
|----|-----------|---------|----------|
| A1 | `_connect()` is the single connection factory -- all DB access goes through it | Validated | `grep -rn "sqlite3.connect" src/` returns only `src/storage/db.py:17`. `get_connection()` calls `_connect()`. Every function in `documents.py` uses `get_connection()`. No bypass exists in the codebase. |
| A2 | `documents.upsert()` returns `Success(rowid)` | Validated | `src/storage/documents.py:131-132`: `rowid: int = cur.lastrowid` then `return Success(rowid)`. The rowid is available, though the spec's indexer wiring correctly uses `outcome.vault_path` (from WriteOutcome) rather than the rowid. |
| A3 | `delete_by_path()`, `rename()`, and `replace_path()` each open their own connection via `get_connection()` | Validated | All three functions use `with get_connection(db_path) as conn:` (lines 213, 304, 252). Each owns its connection and transaction. Adding SQL statements inside the same `with` block is safe within the same transaction. |
| A4 | `all-MiniLM-L6-v2` produces 384-dimensional float32 vectors | Unverifiable | External library claim. No existing usage of `sentence-transformers` in the codebase. The model's published spec confirms 384 dimensions, but this can only be verified at runtime by loading the model. |
| A5 | `sqlite-vec` `vec0` supports text primary keys and DELETE by primary key | Unverifiable | External library claim. `sqlite-vec>=0.1.9` is already in `pyproject.toml` (line 31), confirming the version is available. The spec author claims this was tested manually. Runtime verification needed. |
| A6 | FTS5 has no UNIQUE constraint -- duplicates accumulate unless explicitly deleted | Unverifiable | SQLite documentation behavior. Standard SQLite FTS5 behavior -- well-documented but can only be confirmed by runtime SQL test. |
| A7 | `vec0` does NOT support `INSERT OR REPLACE` | Unverifiable | External library claim. Spec author claims manual testing confirmed this. Same runtime verification caveat as A5. |
| A8 | `vec0` does NOT support `UPDATE` on primary key columns | Unverifiable | Same as A7 -- external library behavior, claimed-tested. |
| A9 | WriteOutcome contains vault_path and NoteMetadata with summary, tags, type, and title via `extra["title"]` | Partially Invalidated | See detailed analysis below. |

### A9 Detailed Analysis

The spec claims that `WriteOutcome`'s `NoteMetadata` contains `title` via `extra["title"]`. This is misleading.

**What the code actually shows:**

1. `WriteOutcome` (at `src/vault/writer.py:39-45`) has `vault_path: str`, `metadata: NoteMetadata`. Confirmed.

2. `NoteMetadata` (at `src/vault/frontmatter.py:55-77`) has `summary`, `tags`, `type` as direct fields. Confirmed.

3. `title` is NOT in `_KNOWN_KEYS` (frontmatter.py:27-47). When a note on disk has `title: X` in frontmatter, it routes to `extra["title"]` during `parse()`. BUT the capture pipeline creates `NoteMetadata` at `capture.py:889-899` with NO `extra` dict -- meaning `extra` is `{}` and `extra.get("title")` returns `None`.

4. The AI-generated title lives in `MetadataResult.ai_title` (capture.py:79), NOT in `WriteOutcome.metadata.extra["title"]`.

5. The spec's Component 7 wiring code correctly uses `mr.ai_title` to get the title -- NOT `outcome.metadata.extra["title"]`. So the wiring code is correct despite the assumption text being wrong.

6. `documents.py::_derive_title()` (line 70) uses `outcome.metadata.extra.get("title") or Path(outcome.vault_path).stem` -- this falls back to filename stem when `extra["title"]` is absent, which is the normal case for newly captured notes.

**Impact:** The spec's assumption text is misleading but the actual wiring code compensates by reading `mr.ai_title` directly. No redesign needed -- just a documentation correction in the assumption table.

---

## Additional Verifications

### 1. Migration numbering

Files in `src/storage/migrations/`:
- `001_initial.sql`
- `002_batches.sql`
- `003_add_project.sql`
- `004_add_status.sql`
- `005_add_key_topics.sql`
- `006_batches_folder_path.sql`

Migration 006 exists. Migration 007 does NOT yet exist. The spec correctly targets 007 as the next migration number.

### 2. Config structure

`MainConfig` (at `src/core/config.py:313-368`) does NOT have a `search` field. Adding `search: SearchConfig = Field(default_factory=SearchConfig)` is straightforward -- follows the exact same pattern as `capture: CaptureConfig`, `handlers: HandlersConfig`, etc.

`config.yaml` does NOT have a `search:` section. Adding one follows the existing YAML structure.

### 3. pyproject.toml dependencies

- `sqlite-vec>=0.1.9` is ALREADY listed (line 31). The spec says to add it -- this is a no-op (not harmful, but the spec should note it's already there).
- `sentence-transformers` is NOT listed. Needs to be added as the spec says.

### 4. TD-019 status

`validate_tags` IS called at `capture.py:240` with `TAG_VIOLATION` audit logging (lines 284-303). `classify.py` does NOT call `validate_tags` -- confirmed by grep. This matches the spec's assumption that classify is a pure function and validation happens in the capture pipeline.

### 5. TD-050 context

`tests/test_vault/conftest.py` has 2 fixtures: `vault_config` and `vault_root`. No timer cleanup fixture exists. The flaky test `test_no_edit_pdf_cross_folder_rehome` is at line 86 of `test_watcher_rehome.py`. The leak mechanism: watcher tests schedule real `threading.Timer` callbacks via `_debounce` that fire during unrelated tests' `time.sleep()` calls, mutating their monkeypatched module-level functions. The spec's autouse fixture that cancels all `threading.Timer` instances in teardown would address this.

### 6. Body text availability at each call site

| Call site | Line | Body variable | Confirmed |
|-----------|------|---------------|-----------|
| `_store_md` rename path | 957 | `original_body` (from `read_note` at line 929) | Yes -- `note.content` assigned to `original_body` at line 929 |
| `_store_md` in-place path | 1003 | `original_body` (same variable, same scope) | Yes |
| `_store_nonmd` LOCATED | 1100 | `rich_body` (from `provider.complete()` at line 1100) | Yes |
| `_store_nonmd` CLUELESS | 1210-1227 | `rich_body` (from missing-file fallback at 1210 or `provider.complete()` at 1227) | Yes |

The spec's body text source claims are correct for all 4 call sites.

### 7. `_run_migrations()` pattern

`src/storage/db.py:23-40`: Migrations are loaded by `sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))` -- lexical ordering of 3-digit-prefixed filenames. Each migration with `file_version > version` is applied and `schema_version` is updated. Migration 007 will be picked up automatically as long as the filename matches the `NNN_*.sql` pattern.

**Caveat from the code comment (lines 24-28):** `executescript()` issues an implicit COMMIT before running, so DDL inside a migration cannot be rolled back. The spec's migration 007 contains two `CREATE VIRTUAL TABLE IF NOT EXISTS` statements. If the first succeeds and the second fails, the first table exists but `schema_version` is not updated to 7. On retry, both `IF NOT EXISTS` clauses prevent errors, so recovery works. This is safe.

### 8. `src/retrieval/` directory

Does NOT exist. Needs to be created with `__init__.py`, `embeddings.py`, and `keyword.py`.

### 9. Fifth call site (classify step)

There is a 5th `documents.replace_path` call at `capture.py:515` inside `_classify_auto_md_move`. This is NOT one of the 4 spec-listed indexing sites. This is correct because: (a) this call happens BEFORE `store()`, so the note hasn't been indexed yet; (b) after this call, `store()` will run `_store_md` which handles the indexing; (c) Component 8's search cleanup in `replace_path` will automatically clean up the old vault_path's search entries (which will be empty at this point since the note wasn't indexed before classify).

---

## Edge Cases and Silent Failure Modes

1. **Classify-then-store double replace_path.** When a `.md` note is AUTO-classified from inbox to a project folder, `_classify_auto_md_move` calls `documents.replace_path` at line 515, then `_store_md` calls it AGAIN at line 975 (rename path) or `documents.upsert` at line 1007 (in-place path). Component 8's search cleanup in `replace_path` will fire twice on the old path -- the second time is a no-op (zero rows deleted). This is safe but worth noting for test expectations.

2. **sqlite-vec extension loading on every connection.** The spec adds `sqlite_vec.load(conn)` inside `_connect()`. This means every read-only query (`get_connection(readonly=True)`) also loads the extension. Acceptable for correctness (any query might touch vec0 tables) but adds overhead to pure-read paths like `all_paths()`.

3. **Migration 007 with two DDL statements.** The `executescript()` call commits before executing, and the migration file contains two CREATE VIRTUAL TABLE statements. If the second fails, the first is already committed. The `IF NOT EXISTS` clause makes retry safe, but the intermediate state (one table exists, schema_version still 6) could confuse diagnostics.

---

## Dependencies and Coupling

- `src/retrieval/embeddings.py` will import `CONFIG` lazily (inside the function) to avoid C-17 violations. It reads `CONFIG.main.search.embedding_model` for the model name.
- `src/retrieval/keyword.py` uses `get_connection()` from `storage/db.py` -- no CONFIG dependency.
- `src/storage/documents.py` gains SQL DELETE/INSERT for search tables -- these reference `embeddings_vec` and `notes_fts` table names. If migration 007 hasn't run, these queries will fail. This is acceptable: `init_db()` runs migrations before any pipeline code.
- `src/pipelines/capture.py` gains imports of `retrieval.embeddings.index_embedding` and `retrieval.keyword.index_keywords` -- lazy imports inside try/except blocks, so missing modules don't break capture.

**COUPLING comment needed:** Migration 007's `float[384]` is coupled to the `all-MiniLM-L6-v2` model. The spec correctly identifies this.

---

## Extension Points

- **New embedding model:** Change `SearchConfig.embedding_model` in config.yaml. Requires a new migration to drop and recreate `embeddings_vec` with the new dimension size. Documented via `# COUPLING:` comment.
- **New search backend:** The indexer functions are self-contained in `src/retrieval/`. A new backend (e.g., Elasticsearch) could be added as a new module without modifying existing code.
- **New file type:** No change needed -- all file types go through the same capture pipeline, which feeds the same indexing calls.

---

## Open Questions

1. **vec0 behavior with content tables.** The spec uses a content `fts5` table (not contentless). The `DELETE FROM notes_fts WHERE vault_path = ?` syntax assumes content-table FTS5. If the FTS5 implementation were changed to contentless in the future, the DELETE syntax would need to change to use rowid. This is a future concern only.

2. **sentence-transformers download size in CI.** First import pulls ~500MB of PyTorch dependencies. The spec acknowledges this and defers to `SENTENCE_TRANSFORMERS_HOME` caching. No action needed now.

---

## Technical Debt Spotted

1. **sqlite-vec already in pyproject.toml.** The spec's Component 4 says to add `sqlite-vec>=0.1.0` to pyproject.toml, but `sqlite-vec>=0.1.9` is already present (line 31). The spec should note this as a no-op. Minor.

2. **NoteMetadata.extra["title"] confusion.** The `documents.py::_derive_title()` function reads `outcome.metadata.extra.get("title")`, which is `None` for pipeline-created NoteMetadata (since `title` is never set in `extra`). It falls back to filename stem. This means the documents table's `title` column for newly captured notes comes from the filename, NOT from the AI-generated title. This is pre-existing behavior unrelated to the Index Layer, but it means the keyword indexer should use `mr.ai_title` (the AI title) rather than `outcome.metadata.extra.get("title")` (which would be None). The spec's wiring code already does this correctly.

---

## Invalidated Assumptions

### A9 -- WriteOutcome title via extra["title"]

**Spec claimed:** "The WriteOutcome object available at each upsert site contains vault_path and the NoteMetadata with summary, tags, type, and title (via extra['title'])."

**Code shows:** `WriteOutcome.metadata.extra` is an empty dict `{}` for pipeline-created notes (`src/pipelines/capture.py:889-899` creates `NoteMetadata` with no `extra` argument). `title` is not in `_KNOWN_KEYS` (`src/vault/frontmatter.py:27-47`), so it only appears in `extra` when parsed from existing frontmatter on disk -- not when created fresh by the pipeline. The AI-generated title lives in `MetadataResult.ai_title` (`capture.py:79`).

**Why this matters:** If the indexer code were to read `outcome.metadata.extra.get("title")` as the assumption suggests, it would get `None` for every newly captured note and fall back to filename stem. The spec's actual wiring code correctly reads `mr.ai_title` instead, so no functional bug exists. But the assumption text is misleading and should be corrected to avoid confusing the planner.

**Suggested resolution directions:**
1. Correct the assumption text to say: "The AI title is available as `mr.ai_title` at each call site. `WriteOutcome.metadata` carries `summary`, `tags`, and `type` as direct NoteMetadata fields. `title` is NOT on WriteOutcome -- it must be read from `MetadataResult.ai_title`."
2. No code or architecture change needed -- the spec's wiring code is already correct.

**Classification:** Mechanical documentation fix. No redesign needed. No Q4 diagram required.
