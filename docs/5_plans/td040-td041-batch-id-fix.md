# Plan: TD-040 + TD-041 — Batch-ID Fix

**ID prefix:** `P2-BAT`
**Date:** 2026-06-07
**Status:** Plan — ready for implementation
**Design doc:** `docs/1_design/td040-td041-batch-id-fix.md`
**Spec:** `docs/3_specs/td040-td041-batch-id-fix.md`
**Chosen option:** A1 — dedicated `is_batch_subfolder()` predicate in `vault/paths.py`

---

## What this is (plain English, non-coder first)

Right now the system processes each file in isolation. If you drop two reports into `Projects/Alpha/Q2-reports/` and run `kms capture`, both files get captured — but the database records them as strangers. The daily briefing cannot group them together. You lose the "these came from the same batch" signal.

This plan fixes two gaps:

- **TD-040:** When `kms capture <file>` runs on a single file inside a named subfolder, it does not record which batch the file belongs to. The fix adds a quick folder-check before the pipeline starts: if the file sits inside a meaningful subfolder, look up or create a batch record for that folder, then stamp the file's database row with the batch ID.

- **TD-041:** When `kms capture --scan` sweeps the whole vault, it only looks at individual files. It never notices subfolders sitting unprocessed. The fix adds a subfolder-detection pass that runs first, dispatches any unprocessed subfolders as folder batches, then continues with the per-file loop as today.

A third related fix: when the file-move watcher sees a file move into a new subfolder, it already updates the file's path in the database — but it does not update the batch ID. After this fix, it does.

No new AI calls. No new prompts. No confidence thresholds. Pure path math and one small database write.

---

## Q1 Diagram — Decision Flow (from design doc)

Scope: shows what happens when a single file is captured or moved under the new batch-aware logic.

```
            File arrives for capture
                      │
                      ▼
          ┌───────────────────────┐
          │ Is the parent folder  │
          │ a batch-worthy        │
          │ subfolder?            │
          └───────────┬───────────┘
                      │
           ┌──────────┴──────────┐
           │                     │
          YES                    NO
           │                     │
           ▼                     ▼
  ┌─────────────────┐    ┌────────────────────┐
  │ Look up or      │    │ Capture normally — │
  │ create a batch  │    │ no batch attached  │
  │ for that folder │    └────────────────────┘
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │ Stamp batch ID  │
  │ on this run     │
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │ Run six-stage   │
  │ capture; save   │
  │ note with batch │
  │ ID attached     │
  └─────────────────┘
```

Watcher-move path: same YES/NO fork, exits after the DB update — no pipeline re-run.

---

## Q2 Diagram — Component Call Flow (from spec)

```
  CAPTURE PATH                          WATCHER-MOVE PATH
  ────────────────────────────────      ─────────────────────────────────
  cli/main.py                           vault/watcher.py
    └─ capture_file(path, ctx)            └─ _handle_binary_move(src, dst)
         │                                     │
         ▼                                     ▼
  pipelines/capture.py                  vault/paths.py
    └─ is_batch_subfolder(               └─ is_batch_subfolder(
         path.parent, vault_cfg)               dst.parent, vault_cfg)
         │                                     │
         ▼ (True)                              ▼ (True)
  storage/batches.py                    storage/batches.py
    └─ find_by_folder_path(               └─ find_by_folder_path(
         folder_path, db_path)                 folder_path, db_path)
         │                                     │
         ▼ (None → create)                     ▼ (None → create)
  pipelines/capture.py                  storage/batches.py
    └─ _insert_batch(                      └─ insert(
         folder_path=..., ...)                 folder_path=..., ...)
         │                                     │
         ▼                                     ▼
  ctx.batch_id = <id>                   storage/documents.py
         │                                └─ update_batch_id(
         ▼                                     vault_path, batch_id,
  pipelines/capture.py                         db_path)
    └─ run_pipeline(ctx)
         │
         ▼
  storage/documents.py
    └─ upsert(note, batch_id=ctx.batch_id)


  SCAN PATH (new subfolder detection pass in scan_capture)
  ─────────────────────────────────────────────────────────
  pipelines/capture.py
    └─ scan_capture(root, vault_cfg, db_path)
         │
         ▼
         for each directory under inbox/, Projects/<A>/, Domain/<D>/
           └─ is_batch_subfolder(dir, vault_cfg)
                │
                ▼ (True)
           storage/batches.py
             └─ find_by_folder_path(folder_path, db_path)
                  │
                  ├─ Result[int] → skip (already captured)
                  │
                  └─ Result[None] → dispatch
                       └─ capture_folder(dir, vault_cfg, db_path)
```

---

## Q3 Diagram — Data Flow (sequence diagram)

```
  kms capture Projects/Alpha/Q2/report.pdf
  ─────────────────────────────────────────────────────────────────────────
  capture_file(path)
       │
       ├─ is_batch_subfolder(path.parent, vault_cfg)     # vault/paths.py
       │       └─ _location_context(path.parent)
       │               → ("project", "Alpha")
       │       └─ depth check: len(rel_to_projects) >= 2  → True
       │       └─ name not in {"attachment",".summaries","Archive"} → True
       │       RETURNS: True
       │
       ├─ vault_relative(path.parent)  →  "Projects/Alpha/Q2"
       │
       ├─ find_by_folder_path("Projects/Alpha/Q2", db_path)   # storage/batches.py
       │       SELECT id FROM batches WHERE folder_path=? ORDER BY created_at DESC LIMIT 1
       │       → Success(None)   [no batch yet]
       │
       ├─ _insert_batch(folder_path="Projects/Alpha/Q2",       # pipelines/capture.py
       │                folder_name="Q2", file_count=1, ...)
       │       INSERT INTO batches (..., folder_path) VALUES (...)
       │       → batch_id = 42
       │
       ├─ ctx = replace(ctx, batch_id=42)
       │
       └─ run_pipeline(ctx)
               └─ store()
                       └─ documents.upsert(outcome, batch_id=42)
                               INSERT OR REPLACE INTO documents (..., batch_id) VALUES (..., 42)


  Second file: kms capture Projects/Alpha/Q2/slides.pptx
  ─────────────────────────────────────────────────────────────────────────
       ├─ is_batch_subfolder(...)     → True
       ├─ find_by_folder_path("Projects/Alpha/Q2", db_path)
       │       → Success(42)   [batch already exists]
       ├─ ctx = replace(ctx, batch_id=42)
       └─ run_pipeline → upsert(..., batch_id=42)
               Both documents share batch_id=42. P2-BAT-02 passes.


  kms capture inbox/report.pdf   (inbox root, NOT a subfolder)
  ─────────────────────────────────────────────────────────────────────────
       ├─ is_batch_subfolder(inbox_path, vault_cfg)
       │       _location_context(inbox_path) → ("inbox", None)
       │       depth check for inbox: path IS the inbox root → False
       │       RETURNS: False
       └─ run_pipeline(ctx)  [batch_id stays None]
               upsert(..., batch_id=None)   P2-BAT-03 passes.


  Watcher sees report.pdf move into Projects/Alpha/Q2/
  ─────────────────────────────────────────────────────────────────────────
  _handle_binary_move(src, dst)  after rename_doc() succeeds
       ├─ is_batch_subfolder(dst.parent, vault_cfg)    → True
       ├─ find_by_folder_path("Projects/Alpha/Q2", db_path)
       │       → Success(42)
       └─ documents.update_batch_id(vault_path, 42, db_path)
               UPDATE documents SET batch_id=42 WHERE vault_path=?
               P2-BAT-06 passes.
```

---

## Implementation Phases

Each phase: write tests first, then implementation, then verify. Each phase must pass its own tests before the next begins.

---

### Phase 1 — SQL Migration: add `folder_path` column to `batches`

**What this does (plain English):** The `batches` database table does not have a column to store which folder path created the batch. We need one so the system can look up "is there already a batch for this folder?" by folder path. This is a one-line SQL change delivered as a migration file — the only safe way to change the database schema in this project.

**TDD steps:**

1. Write test first — `tests/test_storage/test_migrations.py` (existing file):
   - Add a test that applies all migrations (001–006) on a fresh in-memory DB and checks that `PRAGMA table_info(batches)` includes a row where `name = "folder_path"`.
   - Confirm the column is nullable (no `NOT NULL` constraint).

2. Create the file:
   - **`src/storage/migrations/006_batches_folder_path.sql`** (new file)
   - Content: `ALTER TABLE batches ADD COLUMN folder_path TEXT;`
   - Follow the naming convention of `001_initial.sql` through `005_add_key_topics.sql`.

3. Verify:
   - Test passes: `uv run pytest tests/test_storage/test_migrations.py -k "folder_path"`.
   - Existing migration tests still pass (no regression).

**Files touched:**
- `src/storage/migrations/006_batches_folder_path.sql` — new file

---

### Phase 2 — `storage/batches.py`: add `folder_path` to `insert()`, add `find_by_folder_path()`

**What this does (plain English):** Two changes to the database access layer for batches. First, the existing `insert()` function is extended to accept and write the new `folder_path` column. Second, a new `find_by_folder_path()` function is added that answers "does a batch already exist for this folder?" — the key deduplication lookup used by both the capture pipeline and the watcher.

**Exact locations in `src/storage/batches.py`:**
- `insert()` function: lines 19–61. Current signature ends at line 27 (`db_path: Path | None = None`). The INSERT SQL at line 49 lists six columns; line 52 passes six values.
- The file currently ends at line 92. `find_by_folder_path()` will be appended after `update_status()`.

**TDD steps:**

1. Write tests first — `tests/test_storage/test_batches.py`:
   - `test_insert_writes_folder_path`: call `insert(..., folder_path="inbox/Q2-reports")`, fetch the row, assert `folder_path == "inbox/Q2-reports"`.
   - `test_insert_folder_path_none`: call `insert()` without `folder_path` (omit or pass `None`), assert column is NULL — existing rows stay compatible.
   - `test_find_by_folder_path_found`: insert a batch with `folder_path="inbox/Q2-reports"`, call `find_by_folder_path("inbox/Q2-reports", db_path)`, assert `Success(int)`.
   - `test_find_by_folder_path_not_found`: call on a folder that has no batch, assert `Success(None)`.
   - `test_find_by_folder_path_returns_most_recent`: insert two batches for the same folder (re-drop scenario), assert the function returns the ID of the second (most recent) one.
   - `test_find_by_folder_path_db_error`: pass a path to a non-existent DB file, assert `Failure`.

2. Implement:

   **`insert()` signature change** (lines 19–27): add `folder_path: str | None = None` as a keyword argument after `file_count`. Mark with a comment: `# Required for single-file batch lookup — pass folder_path=vault_relative(subfolder)`.

   **`insert()` SQL change** (lines 45–53): extend the INSERT to include `folder_path`:
   ```python
   INSERT INTO batches
       (folder_name, destination_type, destination_name,
        confidence, status, file_count, folder_path, created_at)
   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
   ```
   and add `folder_path` to the tuple on line 52.

   **New function `find_by_folder_path()`** — append after line 92:
   ```python
   def find_by_folder_path(
       folder_path: str,
       db_path: Path | None = None,
   ) -> Result[int | None]:
       """Look up the most recent batch for folder_path.

       Returns Success(int) if found, Success(None) if no batch exists,
       Failure on sqlite3.Error. Uses get_connection() (constraint C-04).
       """
       try:
           with get_connection(db_path, readonly=True) as conn:
               conn.row_factory = sqlite3.Row
               cur = conn.execute(
                   "SELECT id FROM batches WHERE folder_path = ?"
                   " ORDER BY created_at DESC LIMIT 1",
                   (folder_path,),
               )
               row = cur.fetchone()
           if row is None:
               return Success(None)
           return Success(row["id"])
       except sqlite3.Error as exc:
           return Failure(
               error=str(exc),
               recoverable=False,
               context={"folder_path": folder_path, "op": "find_by_folder_path"},
           )
   ```

3. Verify:
   - All new tests pass: `uv run pytest tests/test_storage/test_batches.py`.
   - Full suite still green: `uv run pytest tests/ -m "not smoke"`.

**Files touched:**
- `src/storage/batches.py` — extend `insert()` at lines 19–53; append `find_by_folder_path()` after line 92.

---

### Phase 3 — `storage/documents.py`: add `update_batch_id()`

**What this does (plain English):** When the file-move watcher sees a file land in a new subfolder, we need to update just the `batch_id` column on that file's database row — without re-running the whole six-stage pipeline. This new function is a targeted single-column UPDATE.

**Exact location in `src/storage/documents.py`:**
- File currently ends at line 315. Append `update_batch_id()` after `rename()` (lines 287–315).

**TDD steps:**

1. Write tests first — `tests/test_storage/test_documents.py`:
   - `test_update_batch_id_sets_value`: upsert a document row with `batch_id=None`, then call `update_batch_id(vault_path, 42, db_path)`, fetch the row, assert `batch_id == 42`.
   - `test_update_batch_id_returns_rowcount_1`: assert the return value is `Success(1)`.
   - `test_update_batch_id_not_found_returns_0`: call on a `vault_path` not in the table, assert `Success(0)`.
   - `test_update_batch_id_db_error`: pass a bad db_path, assert `Failure`.

2. Implement — append after line 315:
   ```python
   def update_batch_id(
       vault_path: str,
       batch_id: int,
       db_path: Path | None = None,
   ) -> Result[int]:
       """Set batch_id on the documents row for vault_path.

       Used by the watcher when a file moves into a batch-worthy subfolder,
       to avoid a full re-capture (deterministic path math, not an AI decision).

       Args:
           vault_path: POSIX vault-relative path of the document to update.
           batch_id:   FK to batches.id to stamp on the row.
           db_path:    Override DB path.

       Returns:
           Success(rowcount) — 1 if updated, 0 if no row matched.
           Failure(recoverable=False) on sqlite3.Error.
       """
       try:
           with get_connection(db_path) as conn:
               cur = conn.execute(
                   "UPDATE documents SET batch_id = ? WHERE vault_path = ?",
                   (batch_id, vault_path),
               )
               return Success(cur.rowcount)
       except sqlite3.Error as exc:
           return Failure(
               error=str(exc),
               recoverable=False,
               context={"vault_path": vault_path, "op": "update_batch_id"},
           )
   ```

3. Verify:
   - All new tests pass: `uv run pytest tests/test_storage/test_documents.py`.
   - Full suite still green: `uv run pytest tests/ -m "not smoke"`.

**Files touched:**
- `src/storage/documents.py` — append `update_batch_id()` after line 315.

---

### Phase 4 — `vault/paths.py`: add `is_batch_subfolder()` predicate

**What this does (plain English):** A new named rule that answers "does this path qualify for batch association?" Both the capture pipeline and the watcher call this one function. The rule cannot drift between them because there is exactly one copy.

The rule returns True when:
- The path is at least one level inside `inbox/`, `Projects/<A>/`, or `Domain/<D>/` (not the root of those trees).
- The path name is NOT a system-managed folder: `attachment`, `.summaries`, or `Archive`.

Critical implementation note: `_location_context()` returns a **tuple** `(zone_type, zone_name)`, not a plain string. The new function MUST unpack the tuple before comparing: `loc_type, _ = _location_context(path, vault_cfg)`. Comparing a string directly against a tuple always evaluates `False` (silent bug).

**Exact location in `src/vault/paths.py`:**
- `_location_context()` ends at line 304. Insert `is_batch_subfolder()` after line 304, before `load_valid_domains()` at line 307. (All private `_` helpers are grouped together; this public function goes between the last private helper and the first public utility.)

**TDD steps:**

1. Write tests first — `tests/test_vault/test_paths.py` (existing file):
   - `test_is_batch_subfolder_project_subfolder`: path = `Projects/Alpha/subdir`, vault with real layout — assert `True`.
   - `test_is_batch_subfolder_domain_subfolder`: path = `Domain/D/subdir`, assert `True`.
   - `test_is_batch_subfolder_inbox_subfolder`: path = `inbox/subdir`, assert `True`.
   - `test_is_batch_subfolder_project_root`: path = `Projects/Alpha` (root, not nested), assert `False`.
   - `test_is_batch_subfolder_domain_root`: path = `Domain/D`, assert `False`.
   - `test_is_batch_subfolder_inbox_root`: path = `inbox`, assert `False`.
   - `test_is_batch_subfolder_attachment_blocked`: path = `Projects/Alpha/attachment`, assert `False`.
   - `test_is_batch_subfolder_summaries_blocked`: path = `Projects/Alpha/.summaries`, assert `False`.
   - `test_is_batch_subfolder_archive_blocked`: path = `Domain/D/Archive`, assert `False`.
   - `test_is_batch_subfolder_outside_vault`: path outside vault, assert `False`.

2. Implement — insert after line 304 (end of `_location_context`):
   ```python
   #: Folder names that are system-managed and should never be treated as
   #: batch-worthy subfolders, regardless of their position in the vault.
   _BATCH_SUBFOLDER_BLOCKLIST: frozenset[str] = frozenset(
       {"attachment", ".summaries", "Archive"}
   )


   def is_batch_subfolder(path: Path, vault_cfg: "VaultConfig") -> bool:
       """Return True if *path* is a named subfolder that warrants a batch record.

       A batch-worthy subfolder is any named directory inside inbox/, Projects/<A>/,
       or Domain/<D>/ that is NOT:
         - the root of those trees (must be at least one level deeper), OR
         - a system-managed folder: attachment/, .summaries/, or Archive/.

       Both capture_file() and _handle_binary_move() call this function so the
       batch-worthiness rule is defined in exactly one place.

       Pure path arithmetic — no filesystem I/O, no CONFIG import, no side effects.

       Args:
           path:      Absolute path to the directory being tested.
           vault_cfg: VaultConfig with projects_path, domain_path, inbox_path.

       Returns:
           True if path should receive a batch record; False otherwise.
       """
       # Blocklist: system-managed folder names are never batch-worthy.
       if path.name in _BATCH_SUBFOLDER_BLOCKLIST:
           return False

       loc_type, _ = _location_context(path, vault_cfg)

       if loc_type == "inbox":
           # Any subfolder of inbox/ qualifies — depth >= 1 is guaranteed because
           # _location_context returns ("inbox", None) for inbox/ itself too, so we
           # must check that path is not inbox/ root.
           return path != vault_cfg.inbox_path

       if loc_type in ("project", "domain"):
           # Must be at least two parts deep relative to the tree root
           # (i.e., Projects/<A>/subdir, not just Projects/<A>).
           root = vault_cfg.projects_path if loc_type == "project" else vault_cfg.domain_path
           try:
               rel = path.relative_to(root)
               return len(rel.parts) >= 2
           except ValueError:
               return False

       return False
   ```

   Also update the import in `vault/paths.py`'s public `__all__` (if present) and add `is_batch_subfolder` to the import in `pipelines/capture.py` (done in Phase 5).

3. Verify:
   - All new predicate tests pass.
   - Full suite: `uv run pytest tests/ -m "not smoke"`.

**Files touched:**
- `src/vault/paths.py` — insert `is_batch_subfolder()` and `_BATCH_SUBFOLDER_BLOCKLIST` after line 304.

---

### Phase 5 — `pipelines/capture.py`: three changes

**What this does (plain English):** Three surgical changes to the capture pipeline entry points:

- **5a:** `capture_file()` — before calling the six-stage pipeline, add a pre-check: if the file's parent folder is batch-worthy, look up or create a batch for that folder and stamp the context with the batch ID.
- **5b:** `_insert_batch()` — extend to accept and forward `folder_path` to `batches.insert()`. Update all four existing call sites in `capture_folder()` to pass the folder path.
- **5c:** `scan_capture()` — before the per-file loop, add a subfolder detection pass that dispatches unprocessed subfolders via `capture_folder()`.

**Exact locations in `src/pipelines/capture.py`:**

*5a — capture_file() batch-stamp step:*
- Function starts at line 792, `return await run_pipeline(...)` is at line 909.
- Insert the new batch-stamp block between the idempotent-capture guard (which ends around line 907) and `return await run_pipeline(...)` at line 909.

*5b — _insert_batch() signature:*
- Function definition at lines 1222–1245. Add `folder_path: str | None = None` to the parameter list (line 1229) and pass it to `batches.insert()` (line 1238).
- Four call sites in `capture_folder()`:
  - Line 1309: `_insert_batch(folder_path.name, loc_type, loc_name, 1.0, "ROUTING", len(files), ctx)` — add `folder_path=vault_relative(folder_path)`.
  - Line 1353: `_insert_batch(folder_path.name, target_type, target_name, confidence, "ROUTING", len(new_files), ctx)` — add `folder_path=vault_relative(new_folder)`.
  - Line 1365: `_insert_batch(folder_path.name, target_type, target_name, confidence, "PENDING_REVIEW", len(files), ctx)` — add `folder_path=vault_relative(folder_path)`.
  - Line 1377: `_insert_batch(folder_path.name, target_type, target_name, confidence, "CLUELESS", len(files), ctx)` — add `folder_path=vault_relative(folder_path)`.

*5c — scan_capture() subfolder pass:*
- `scan_capture()` starts at line 917. The per-file loop for `summary.added` starts at line 964.
- Insert the subfolder detection pass after the CONFIG/taxonomy setup (around line 963) and before the `summary.added` loop.

**Import change:** Add `is_batch_subfolder` to the existing import at line 30:
```python
from vault.paths import _is_misplaced, _location_context, is_batch_subfolder, resolve_placement, to_vault_path
```
Add `find_by_folder_path` to the existing import at line 38:
```python
import storage.batches as batches
```
(access via `batches.find_by_folder_path`)

**TDD steps:**

1. Write tests first — `tests/test_pipelines/test_capture.py`:

   *For 5a (capture_file batch-stamp):*
   - `test_capture_file_project_subfolder_sets_batch_id`: mock `is_batch_subfolder` → True, mock `batches.find_by_folder_path` → `Success(None)`, mock `batches.insert` → `Success(42)`. Run `capture_file`. Assert `upsert` was called with `batch_id=42`.
   - `test_capture_file_reuses_existing_batch`: mock `find_by_folder_path` → `Success(99)`. Assert `_insert_batch` is NOT called; `upsert` called with `batch_id=99`.
   - `test_capture_file_inbox_root_no_batch`: mock `is_batch_subfolder` → False. Assert `ctx.batch_id` is None on the `upsert` call.
   - `test_capture_file_batch_lookup_failure_proceeds`: mock `find_by_folder_path` → `Failure(...)`. Assert capture still succeeds (batch_id=None), warning logged.

   *For 5b (_insert_batch folder_path):*
   - `test_insert_batch_passes_folder_path`: call `_insert_batch(..., folder_path="inbox/Q2")`. Assert `batches.insert` was called with `folder_path="inbox/Q2"`.

   *For 5c (scan_capture subfolder pass):*
   - `test_scan_capture_dispatches_unprocessed_subfolder`: set up a fake vault with an inbox subfolder. Mock `find_by_folder_path` → `Success(None)`. Assert `capture_folder` is called for that subfolder.
   - `test_scan_capture_skips_already_captured_subfolder`: mock `find_by_folder_path` → `Success(42)`. Assert `capture_folder` is NOT called.

2. Implement:

   *5a — in `capture_file()`, insert before `return await run_pipeline(...)` at line 909:*
   ```python
   # ── Batch-stamp pre-step (TD-040): if parent folder is batch-worthy,
   # look up or create a batch record and stamp context before pipeline runs.
   _vault_cfg = context.config.vault
   if is_batch_subfolder(path.parent, _vault_cfg):
       import unicodedata as _ud
       _folder_vp = _ud.normalize(
           "NFC",
           str(path.parent.relative_to(_vault_cfg.root).as_posix()),
       )
       match batches.find_by_folder_path(_folder_vp, db_path=context.db_path):
           case Success(value=None):
               _batch_id = _insert_batch(
                   folder_name=path.parent.name,
                   destination_type=None,
                   destination_name=None,
                   confidence=1.0,
                   status="ROUTING",
                   file_count=1,
                   folder_path=_folder_vp,
                   ctx=context,
               )
               if _batch_id is not None:
                   context = replace(context, batch_id=_batch_id)
           case Success(value=_existing_bid):
               context = replace(context, batch_id=_existing_bid)
           case Failure(error=_berr):
               logger.warning(
                   "capture_file.batch_lookup_failed path=%s error=%s",
                   path, _berr,
               )
   ```

   *5b — `_insert_batch()` signature* (line 1222): add `folder_path: str | None = None` parameter; pass `folder_path=folder_path` inside the `batches.insert()` call at line 1238.

   *5b — update four call sites* in `capture_folder()`: pass `folder_path=...` using vault-relative path arithmetic (`str(folder_path.relative_to(vault_cfg.root).as_posix())` or similar).

   *5c — `scan_capture()` subfolder detection pass* — insert after CONFIG/taxonomy setup and before `match scan_vault(...)` call. Walk `inbox/`, `Projects/<A>/`, `Domain/<D>/` directories; for each directory `d`: if `is_batch_subfolder(d, vault_cfg)` → call `batches.find_by_folder_path(vault_relative(d), _db_path)`; if `Success(None)` → `await capture_folder(d, context=ctx)`; if `Success(int)` → log DEBUG skip; if `Failure` → log warning, skip.

3. Verify:
   - New tests pass.
   - Existing `capture_folder` tests still pass (4 call-site changes are mechanical).
   - Full suite: `uv run pytest tests/ -m "not smoke"`.

**Files touched:**
- `src/pipelines/capture.py` — line 30 import; lines 909 (new batch-stamp block); lines 1222–1245 `_insert_batch` signature; lines 1309, 1353, 1365, 1377 call sites; new subfolder pass in `scan_capture`.

---

### Phase 6 — `vault/watcher.py`: update `_handle_binary_move()` to stamp batch ID

**What this does (plain English):** When the watcher sees a file move cross-folder (a re-home), it already updates the file's path and sibling card in the database (Sub-step g, line 801). After this fix, it also checks whether the file's new location is batch-worthy, looks up or creates a batch for that folder, and updates the `batch_id` column on the documents row — without re-capturing the file.

**Exact location in `src/vault/watcher.py`:**
- `_handle_binary_move()` is at line 523.
- Sub-step g (`rename_doc`) is at lines 801–813.
- Sub-step h (audit write) is at lines 818–834.
- The new batch_id update step goes between sub-step g (after line 813) and sub-step h (before line 818), as Sub-step g2.

**Import change:** Add to the existing imports at line 37:
```python
from storage.documents import delete_by_path, get_by_path, rename as rename_doc, update_batch_id
```
Add batches import (add near top, after existing storage imports):
```python
import storage.batches as batches
```
Add `is_batch_subfolder` to vault.paths import at line 39:
```python
from vault.paths import (
    _is_ai_output,
    _is_in_managed_attachment,
    _is_misplaced,
    _location_context,
    is_batch_subfolder,
    resolve_placement,
)
```

**TDD steps:**

1. Write tests first — `tests/test_vault/test_watcher.py`:
   - `test_handle_binary_move_cross_folder_updates_batch_id`: simulate a cross-folder move where `is_batch_subfolder(dst.parent)` → True, `find_by_folder_path` → `Success(42)`. Assert `update_batch_id` is called with `(vault_path_of_sibling, 42, db_path)`.
   - `test_handle_binary_move_cross_folder_creates_new_batch`: `find_by_folder_path` → `Success(None)`. Assert `batches.insert` is called, then `update_batch_id` with the new batch_id.
   - `test_handle_binary_move_cross_folder_non_batch_subfolder`: `is_batch_subfolder` → False. Assert `update_batch_id` is NOT called.
   - `test_handle_binary_move_batch_failure_does_not_abort`: `find_by_folder_path` → `Failure(...)`. Assert existing re-home logic completes normally (no exception); warning logged.

2. Implement — insert between Sub-step g and Sub-step h (between lines 813 and 818):
   ```python
   # Sub-step g2 — stamp batch_id on documents row (TD-041 watcher fix)
   # Deterministic path math; no AI decision; no audit row required (C-13 N/A).
   if is_batch_subfolder(dst.parent, self._vault_config):
       _folder_vp = unicodedata.normalize(
           "NFC",
           str(dst.parent.relative_to(self._root).as_posix()),
       )
       _batch_id: int | None = None
       match batches.find_by_folder_path(_folder_vp, db_path=self._db_path):
           case Success(value=None):
               match batches.insert(
                   folder_name=dst.parent.name,
                   destination_type=loc_type,
                   destination_name=loc_name,
                   confidence=1.0,
                   status="ROUTING",
                   file_count=1,
                   folder_path=_folder_vp,
                   db_path=self._db_path,
               ):
                   case Success(value=_bid):
                       _batch_id = _bid
                   case Failure(error=_berr):
                       _log.warning(
                           "watcher.binary_rehome_batch_insert_failed dst=%s error=%s",
                           dst, _berr,
                       )
           case Success(value=_existing_bid):
               _batch_id = _existing_bid
           case Failure(error=_berr):
               _log.warning(
                   "watcher.binary_rehome_batch_lookup_failed dst=%s error=%s",
                   dst, _berr,
               )
       if _batch_id is not None:
           match update_batch_id(new_sibling_vp, _batch_id, self._db_path):
               case Success(value=0):
                   _log.warning(
                       "watcher.binary_rehome_batch_id_update_no_row sibling=%s",
                       new_sibling_vp,
                   )
               case Failure(error=_berr):
                   _log.warning(
                       "watcher.binary_rehome_batch_id_update_failed sibling=%s error=%s",
                       new_sibling_vp, _berr,
                   )
               case Success():
                   pass
   ```

   Note: `self._db_path` must exist on `_VaultEventHandler`. Confirm it is passed in the constructor (check existing tests for how `db_path` is passed to watcher callbacks). If not present, add it as a constructor parameter alongside `vault_config`.

3. Verify:
   - New watcher tests pass.
   - Full suite: `uv run pytest tests/ -m "not smoke"`.

**Files touched:**
- `src/vault/watcher.py` — lines 37–45 imports; new Sub-step g2 block inserted between lines 813 and 818.

---

## Files Touched Summary

| File | Change type | Phase |
|------|------------|-------|
| `src/storage/migrations/006_batches_folder_path.sql` | New file | Phase 1 |
| `src/storage/batches.py` | Extend `insert()` + new `find_by_folder_path()` | Phase 2 |
| `src/storage/documents.py` | New `update_batch_id()` | Phase 3 |
| `src/vault/paths.py` | New `is_batch_subfolder()` predicate + blocklist constant | Phase 4 |
| `src/pipelines/capture.py` | Import; `capture_file()` batch-stamp; `_insert_batch()` signature; 4 call sites; `scan_capture()` subfolder pass | Phase 5 |
| `src/vault/watcher.py` | Imports; Sub-step g2 in `_handle_binary_move()` | Phase 6 |

Test files (mirrors):
- `tests/test_storage/test_migrations.py` — schema column check
- `tests/test_storage/test_batches.py` — new `find_by_folder_path` tests; `insert` folder_path tests
- `tests/test_storage/test_documents.py` — `update_batch_id` tests
- `tests/test_vault/test_paths.py` — `is_batch_subfolder` predicate tests
- `tests/test_pipelines/test_capture.py` — capture_file batch-stamp; scan_capture subfolder pass
- `tests/test_vault/test_watcher.py` — _handle_binary_move batch_id update

---

## Success Criteria (from spec)

All six behaviors must pass before the implementation is considered done.

| ID | Tier | What must be true |
|----|------|------------------|
| P2-BAT-01 | full | `kms capture Projects/Alpha/subdir/report.pdf` — `documents.batch_id` is non-NULL; `batches.folder_path = 'Projects/Alpha/subdir'` |
| P2-BAT-02 | full | Two `kms capture` calls on files in same subfolder — both documents rows share identical `batch_id`; one `batches` row for the folder |
| P2-BAT-03 | full | `kms capture inbox/report.pdf` (inbox root) — `documents.batch_id` is NULL |
| P2-BAT-04 | smoke | `kms capture --scan` with unprocessed inbox subfolder — files have shared non-NULL `batch_id`; `batches.folder_path` set |
| P2-BAT-05 | full | `kms capture --scan` after watcher already captured folder — no second `batches` row; log shows subfolder skipped |
| P2-BAT-06 | full | File moved into `Projects/Alpha/subdir/` while watcher running — `documents.batch_id` updated; no re-capture |

---

## Known Risks

**R1 — `self._db_path` on `_VaultEventHandler`:** Phase 6 calls `batches.find_by_folder_path(..., db_path=self._db_path)`. Confirm `_db_path` is accessible on the handler class before writing the implementation. If the watcher currently resolves db_path via lazy CONFIG import, a constructor parameter must be added (coordinate with Phase 6 test setup).

**R2 — `_insert_batch()` parameter order:** Adding `folder_path` changes the internal helper's keyword signature. All four call sites in `capture_folder()` pass positional arguments up to `file_count`; adding `folder_path` as a keyword-only parameter after `file_count` avoids any positional breakage. Verify with `grep -n "_insert_batch"` before editing.

**R3 — `capture_folder()` vault-relative path:** The four call sites need to compute `vault_relative(folder_path)`. The existing `to_vault_path()` function imports `CONFIG` at call time (lazy import) — acceptable for production but may cause issues in tests that don't set up CONFIG. Use the `watcher.py` pattern instead: `unicodedata.normalize("NFC", str(folder_path.relative_to(vault_cfg.root).as_posix()))`.

**R4 — Concurrent captures:** Two simultaneous `kms capture` calls on files in the same subfolder may both find `find_by_folder_path` → None and each create a batch row. Acceptable for Phase 2 (single-threaded CLI). `reconcile_stale_batch_refs` (Stage 6 of `kms reconcile`) handles cleanup. P2-BAT-02 tests should use sequential captures to avoid this.

**R5 — `scan_capture` subfolder deduplication against pre-fix watcher batches:** Watcher-created batches before this fix shipped have `folder_path = NULL`. `find_by_folder_path` will return `Success(None)` for those folders, causing `scan_capture` to dispatch `capture_folder()` again and create a second batch row. Acceptable — eventual consistency via `reconcile_stale_batch_refs`.

**R6 — `is_batch_subfolder` depth check for `inbox` vs `project`/`domain`:** Inbox subfolders are one level deep from inbox root (`inbox/Q2-reports`). Project/domain subfolders must be two levels deep from the tree root (`Projects/<A>/subdir`). The predicate handles these differently — confirm the test for `inbox/Q2-reports` returns True and `Projects/Alpha` (root) returns False before proceeding to Phase 5.

---

## Open Questions (resolved)

| ID | Resolution |
|----|-----------|
| OQ-BATCH-1 — `batches.insert()` required vs optional `folder_path` | **LOCKED: required by name only; positional args unchanged.** `folder_path` added as keyword-only argument (position after `file_count`). All four call sites updated in Phase 5. Existing callers that pass all args positionally are unaffected. |
| OQ-BATCH-2 — accurate `file_count` required? | **LOCKED: approximation (file_count=1) acceptable.** TD-043 logged in TECH_DEBT.md. |
| OQ-BATCH-3 — does `capture_folder()` Case B skip LLM? | **CONFIRMED (line 1308):** `if loc_type in ("project", "domain"):` → immediately calls `_insert_batch` + `_capture_folder_files` with no LLM call. `scan_capture` dispatching via `capture_folder()` does NOT trigger redundant AI calls. |
