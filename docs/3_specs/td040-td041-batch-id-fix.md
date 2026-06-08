# Spec: TD-040 + TD-041 — Batch-ID Fix

**ID prefix:** `P2-BAT`
**Date:** 2026-06-07
**Status:** Spec — awaiting research
**Design doc:** `docs/1_design/td040-td041-batch-id-fix.md`
**Chosen option:** A1 — dedicated `is_batch_subfolder()` predicate in `vault/paths.py`

---

## What this is (plain English)

Right now, when you drop a single file into a project subfolder and run `kms capture`, the system processes it but never records *which batch it belongs to*. That means two files from the same folder look unrelated in the database, and the daily briefing cannot group them together.

This spec fixes two gaps:

- **TD-040:** `capture_file()` does not set `batch_id` — even when the file sits inside a named subfolder that clearly signals "these belong together."
- **TD-041:** `scan_capture()` (the `kms capture --scan` sweep) does not detect subfolders at all — it only sees individual files.

The fix adds a single named rule (`is_batch_subfolder`) that both the capture pipeline and the file-move watcher use to decide whether a path deserves a batch ID, then wires that rule into three places: single-file capture, scan capture, and the watcher's move handler.

No new AI calls. No new prompts. No confidence thresholds. This is pure path arithmetic and one small DB write.

---

## Cast of Characters

| Name | What it is |
|------|-----------|
| Batch | A group of files that belong together because they came from the same subfolder drop. One row in the `batches` database table per group. |
| Batch ID | The unique number (foreign key) that links a file's database row to its batch. NULL if no batch applies. |
| Batch-worthy subfolder | A folder whose position in the vault signals grouped intent — any named subfolder *inside* `inbox/`, `Projects/<A>/`, or `Domain/<D>/`. NOT the root of those trees, and NOT these system-managed folders: `attachment/` (binary storage), `.summaries/` (hidden AI output), or `Archive/` inside a domain folder. |
| Folder path | The vault-relative path of the subfolder that created the batch (e.g. `inbox/Q2-reports`). Stored on the `batches` row for lookup. |
| Live batch membership | The current batch a file belongs to, based on where it sits *now* — updates when a file moves. |
| Capture pipeline | The six-stage process (extract → enrich → summarize → metadata → tag → store) that runs on each file. |
| Pipeline context | The shared object threaded through one pipeline run — holds config, DB path, correlation ID, and now batch ID. |
| `_location_context` | Existing helper in `vault/paths.py` that returns a two-item tuple `(zone_type, zone_name)` — e.g. `("project", "Alpha")` — or `(None, None)` when the path is outside the vault. The first element (`zone_type`) is `"project"`, `"domain"`, or `"inbox"`. |

---

## Q1 Diagram — What Happens Inside (Decision Flow)

Scope: shows what happens when a single file is captured or moved under the new batch-aware logic. Does NOT cover folder drops (already handled by `capture_folder`) or the classify pipeline.

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

Watcher-move path (batch ID updated in-place without re-capture) follows the same YES/NO decision branch but exits after the DB update — no pipeline re-run.

---

## Q2 Diagram — How It Connects

Scope: Shows what Batch-ID Fix touches.
       Does NOT show internal steps (see Q1 for that).

```
# Batch-ID Fix — How It Connects

How to read this:
  Center box    = the feature being added
  Solid boxes   = existing parts being extended
  Arrow labels  = what flows between them

                 ┌────────────────────────┐
                 │  Folder Detector       │
                 │  Tells whether a       │
                 │  folder means "grouped"│
                 └──────────┬─────────────┘
                            │ asked by both callers
                      ┌─────┴──────┐
                      │            │
                      ▼            ▼
  ┌───────────────────────┐  ┌───────────────────────┐
  │  Capture Pipeline     │  │  File Watcher         │
  │  Runs on new files    │  │  Runs when a file     │
  │  (single or scan)     │  │  is moved             │
  └───────────┬───────────┘  └────────────┬──────────┘
              │                           │
              └──────────┬────────────────┘
                         │ trigger batch assignment
                         ▼
            ┌─────────────────────────────┐
            │       BATCH-ID FIX          │
            │  Links each file to the     │
            │  group it came from         │
            └──────────────┬──────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
┌─────────────────────────┐  ┌──────────────────────┐
│  Batch Tracker          │  │  File Index          │
│  Creates or finds the   │  │  Records which batch │
│  group record           │  │  each file is in     │
└─────────────────────────┘  └──────────────────────┘
```

New functions are shown in **bold** below in the component specs. Everything else is an existing function being extended.

---

## Component Specs

Each component is described in plain English first. Code details follow in sub-bullets.

### 1. New SQL migration — `src/storage/migrations/006_batches_folder_path.sql`

The `batches` table does not have a `folder_path` column. We need one so we can look up "is there already a batch for this folder?" without scanning every row.

- Add column `folder_path TEXT` to the `batches` table.
- The column is nullable. Existing rows (watcher-created batches that predate this fix) will have NULL — that is correct, they were created before we tracked `folder_path`.
- The column is NOT UNIQUE. Re-drops of the same folder are valid and create a second batch row. The `find_by_folder_path` function picks the most-recent row (`ORDER BY created_at DESC LIMIT 1`).
- Follow the existing migration pattern: filename `006_batches_folder_path.sql`, SQL body is `ALTER TABLE batches ADD COLUMN folder_path TEXT;`.

### 2. `storage/batches.py` — two changes

**2a. Extend `insert()` to accept `folder_path`**

The existing `insert()` function creates a new batch row. It currently takes `folder_name`, `destination_type`, `destination_name`, `confidence`, `status`, `file_count`. It does NOT write `folder_path`.

- Add `folder_path: str` as a required positional-or-keyword argument. All existing call sites in `capture_folder()` are updated as part of this fix to pass `folder_path=vault_relative(subfolder)`.
- Write the value into the `batches` row.

**2b. New function `find_by_folder_path(folder_path, db_path) -> Result[int | None]`**

This is the lookup that prevents duplicate batch rows for the same subfolder.

- Query: `SELECT id FROM batches WHERE folder_path = ? ORDER BY created_at DESC LIMIT 1`
- Returns `Success(int)` if a row is found (the batch id), `Success(None)` if no row exists yet, `Failure(...)` if the DB call errors.
- Uses `get_connection(db_path)` — never raw `sqlite3.connect()` (constraint C-04).
- Two callers: `capture_file()` and `scan_capture()` subfolder pass.

### 3. `storage/documents.py` — new function `update_batch_id()`

When a file moves into a batch-worthy subfolder, we need to update its `batch_id` without re-running the full capture pipeline.

- New function: `update_batch_id(vault_path: str, batch_id: int, db_path: Path) -> Result[int]`
- SQL: `UPDATE documents SET batch_id = ? WHERE vault_path = ?`
- Returns `Success(rowcount)`. Caller checks for `rowcount == 0` to detect "document not in index."
- Uses `get_connection(db_path)`.
- Single caller for now: `_handle_binary_move()` in `vault/watcher.py`. (A second caller may appear if `scan_capture` needs to back-fill moved files, but that is out of scope for this phase.)

### 4. `vault/paths.py` — new predicate `is_batch_subfolder()`

This is the single authoritative answer to "does this path qualify for batch association?" Both the capture pipeline and the watcher call this function — the rule cannot drift between them.

- New function: `is_batch_subfolder(path: Path, vault_cfg: VaultConfig) -> bool`
- Returns `True` when ALL of the following hold:
  - `_location_context(path, vault_cfg)` returns a tuple whose first element (`zone_type`) is `"project"`, `"domain"`, or `"inbox"`. The implementation MUST unpack the tuple before comparing: `loc_type, _ = _location_context(path, vault_cfg)` — a bare comparison against the tuple always evaluates `False` because a tuple never equals a string.
  - `path` is NOT the root of `inbox/`, `Projects/<A>/`, or `Domain/<D>/` — it must be at least one level deeper (`len(rel.parts) >= 2` for projects/domain; any sub-path for inbox).
  - `path.name` is NOT in the system folder blocklist: `{"attachment", ".summaries", "Archive"}`.
- Returns `False` in all other cases, including when the path does not exist on disk.
- Follows the existing `vault/paths.py` pattern: takes `(path, vault_cfg)`, returns a plain bool, no side effects.

### 5. `pipelines/capture.py` — three changes

**5a. `capture_file()` — new pre-pipeline batch step**

`capture_file()` is the entry point for single-file capture. It currently sets no `batch_id`.

- Before calling `run_pipeline()`, add a new step:
  1. Call `is_batch_subfolder(path.parent, vault_cfg)`.
  2. If True: call `find_by_folder_path(vault_relative(path.parent), db_path)`.
     - If result is `Success(None)`: call `_insert_batch(folder_path=..., file_count=1, ...)` to create a new batch row.
     - If result is `Success(int)`: use that existing batch id.
     - If result is `Failure`: log a warning, proceed without batch_id (do not abort capture).
  3. Set `ctx.batch_id` to the resolved id (or leave as None on failure).
- The downstream `upsert()` call already reads `ctx.batch_id` and writes it — no further changes needed in `run_pipeline`.

**5b. `_insert_batch()` — add `folder_path` parameter**

`_insert_batch()` is the internal helper that creates a batch row and returns its id.

- Add `folder_path: str` as a required argument (now required; all 4 call sites updated in this fix).
- Pass it through to `batches.insert()`.
- All 4 existing call sites inside `capture_folder()` now pass `folder_path=vault_relative(subfolder)`. This is a mechanical change; the call sites have existing tests that will catch regressions.

**5c. `scan_capture()` — new subfolder detection pass**

`scan_capture()` currently iterates files only. It does not detect or dispatch subfolders.

- Before the per-file loop, add a subfolder detection pass:
  1. Walk `inbox/`, `Projects/<A>/`, `Domain/<D>/` top-level for directories.
  2. For each directory: call `is_batch_subfolder(dir, vault_cfg)`.
  3. If True: call `find_by_folder_path(vault_relative(dir), db_path)`.
     - `Success(int)` → skip (already captured, log at DEBUG level).
     - `Success(None)` → dispatch `capture_folder(dir, vault_cfg, db_path)`.
     - `Failure` → log warning, skip.
  4. Continue to the per-file loop as today (individual files not inside any detected subfolder).

### 6. `vault/watcher.py` — extend `_handle_binary_move()`

When a file moves into a new folder, the watcher currently calls `rename_doc()` (path-only UPDATE) and stops. It does not update `batch_id`.

- After `rename_doc()` succeeds:
  1. Call `is_batch_subfolder(dst.parent, vault_cfg)`.
  2. If True: call `find_by_folder_path(vault_relative(dst.parent), db_path)`.
     - `Success(None)` → call `batches.insert(folder_path=..., ...)` to create a new batch, then call `documents.update_batch_id(vault_path, batch_id, db_path)`.
     - `Success(int)` → call `documents.update_batch_id(vault_path, batch_id, db_path)`.
     - `Failure` → log warning, do not update batch_id.
  3. No re-capture. No LLM calls. Content hash and summary remain unchanged.

---

## Assumption Table

Every claim from the design doc that research must verify before implementation begins.

| # | Assumption | Where it comes from | What to check |
|---|-----------|---------------------|---------------|
| A1 | ~~`_location_context(path, vault_cfg)` returns a plain string in `{"project", "domain", "inbox", None}`~~ **CORRECTED (2026-06-07):** `_location_context()` returns a two-item tuple `(zone_type, zone_name)` — e.g. `("project", "Alpha")` — or `(None, None)` for paths outside the vault (confirmed at `src/vault/paths.py:264–304`). The zone_type is the first element. `is_batch_subfolder()` MUST unpack the tuple (`loc_type, _ = _location_context(...)`) before comparing — comparing a string against the tuple always evaluates `False`. | Design doc §Implications | Resolved — do not re-check |
| A2 | `batches.insert()` currently accepts `folder_name`, `destination_type`, `destination_name`, `confidence`, `status`, `file_count` — and nothing else | Design doc §Implications | Read `storage/batches.py` — confirm exact signature and all call sites |
| A3 | `_insert_batch()` in `capture.py` has exactly 4 call sites inside `capture_folder()` | Design doc §Implications | Read `pipelines/capture.py` — count call sites with `grep` |
| A4 | `documents.upsert()` already reads `batch_id` from `ctx` and writes it to the DB — no change needed downstream | Design doc §Implications (C1 fix from Phase 1.5 code-review) | Read `storage/documents.py` — confirm `batch_id` is in the upsert SQL and the function signature |
| A5 | The `batches` table exists with at least `id`, `folder_name`, `created_at` columns; no `folder_path` column yet | Design doc §Implications | Read `src/storage/migrations/` — confirm highest migration number and `batches` table schema |
| A6 | `get_connection(db_path)` sets `PRAGMA foreign_keys=ON` automatically | Constraint C-04 | Read `storage/` connection helper — confirm pragma is set in `get_connection` |
| A7 | `capture_folder()` Case B path (loc_type in "project"/"domain") skips LLM and proceeds per-file — so dispatching a project subfolder via `capture_folder()` from `scan_capture` does NOT trigger redundant LLM classification | Design doc §OQ-BATCH-3 | Read `pipelines/capture.py` — confirm Case B logic in `capture_folder` |
| A8 | `ctx.batch_id` field exists on the pipeline context object and is read by `run_pipeline()` / `upsert()` | Design doc §Implications | Read `core/` pipeline context definition and `pipelines/capture.py` `run_pipeline` |
| A9 | `rename_doc()` in `storage/documents.py` returns `Result[int]` and the int is rowcount | Design doc §Implications (watcher internals note in CLAUDE.md) | Read `storage/documents.py` — confirm return type |
| A10 | `reconcile_stale_batch_refs` (Stage 6 of `kms reconcile`) exists and can normalize duplicate batch rows created by concurrent single-file captures | Design doc §Risks | Read `pipelines/capture.py` or `cli/main.py` reconcile command — confirm Stage 6 exists |

---

## Success Criteria

These entries are already written in `docs/system_behavior/behavior_inventory.yaml` under prefix `P2-BAT`. They are the acceptance bar for implementation.

| ID | Tier | Trigger | What must be true when it passes |
|----|------|---------|----------------------------------|
| P2-BAT-01 | full | `kms capture Projects/Alpha/subdir/report.pdf` | `documents.batch_id` is non-NULL; the `batches` row has `folder_path = 'Projects/Alpha/subdir'` |
| P2-BAT-02 | full | `kms capture <file1>` then `kms capture <file2>` (same subfolder) | Both `documents` rows have identical `batch_id`; one `batches` row covers the folder |
| P2-BAT-03 | full | `kms capture inbox/report.pdf` (inbox root, not a subfolder) | `documents.batch_id` is NULL |
| P2-BAT-04 | smoke | `kms capture --scan` with an unprocessed inbox subfolder on disk | Both files in the subfolder appear as `documents` rows with a shared non-NULL `batch_id`; a `batches` row exists with the subfolder name as `folder_path` |
| P2-BAT-05 | full | `kms capture --scan` after watcher already captured the folder | No second `batches` row created for the same `folder_path`; existing `documents` rows unchanged; log shows subfolder skipped |
| P2-BAT-06 | full | File moved into `Projects/Alpha/subdir/` while watcher is running | `documents.batch_id` updated to the batch for that subfolder; no re-capture (summary and `content_hash` unchanged) |

**Tier meaning:**
- `full` — must pass in CI on every run (no real vault required, uses fixtures).
- `smoke` — requires a real vault on disk; run with `uv run pytest -m smoke`.

---

## Open Questions

These map directly to the OQ entries in the design doc. Research must resolve them before the plan phase.

| ID | Question | Why it matters | Default if unresolved |
|----|---------|----------------|----------------------|
| OQ-BATCH-1 | Should `batches.insert()` accept `folder_path` as optional (defaults to None) or required (breaks existing callers)? | Required is safer — no caller can accidentally omit it. Optional avoids touching all 4 `capture_folder` call sites now. | **LOCKED: Required.** All 4 capture_folder() call sites updated to pass folder_path. |
| OQ-BATCH-2 | Is an accurate `file_count` on the `batches` row required for Phase 2/8 briefing, or is `1` (never updated) acceptable? | If required, a separate UPDATE after each `capture_file` is needed. If not, document as known approximation. | **LOCKED: file_count = 1 (approximation).** TD-043 logged in TECH_DEBT.md. No sibling scan. |
| OQ-BATCH-3 | Does `capture_folder()` Case B (project/domain loc_type) actually skip LLM? If not, `scan_capture` dispatching via `capture_folder` will trigger redundant AI calls. | Avoids surprise LLM cost during scan. | **Verify in research:** Read capture_folder() body to confirm Case B (loc_type in project/domain) skips LLM before dispatching per-file. Decision locked pending confirmation. |

---

## Files Touched (summary for the plan phase)

| File | Change type | What changes |
|------|------------|-------------|
| `src/storage/migrations/006_batches_folder_path.sql` | New file | `ALTER TABLE batches ADD COLUMN folder_path TEXT;` |
| `src/storage/batches.py` | Extend + new function | `insert()` gains `folder_path` param; new `find_by_folder_path()` |
| `src/storage/documents.py` | New function | `update_batch_id(vault_path, batch_id, db_path)` |
| `src/vault/paths.py` | New predicate | `is_batch_subfolder(path, vault_cfg) -> bool` |
| `src/pipelines/capture.py` | Extend (3 changes) | `capture_file()` pre-pipeline step; `_insert_batch()` signature; `scan_capture()` subfolder pass |
| `src/vault/watcher.py` | Extend | `_handle_binary_move()` batch_id update after `rename_doc()` |

---

## Constraints Verified (from design doc)

| Constraint | Status | Note |
|-----------|--------|------|
| C-04 · `PRAGMA foreign_keys=ON` | Satisfied | All new DB functions use `get_connection()` |
| C-05 · Schema changes via migration files | Satisfied | New column added via `006_batches_folder_path.sql` |
| C-12 · Public functions return `Result[T]` | Satisfied | `find_by_folder_path` → `Result[int \| None]`; `update_batch_id` → `Result[int]` |
| C-13 · Audit log for AI decisions | Not applicable | Batch ID assignment is deterministic path arithmetic, not an AI decision |
| C-17 · No CONFIG import at module scope in tests | Satisfied | New test helpers pass `db_path=tmp_path / "kb.db"` explicitly |

---

## Known Tradeoffs (carried from design doc)

**Concurrent single-file captures of the same subfolder may get different batch IDs.** If two `kms capture` calls run simultaneously on files in the same subfolder, both may find `find_by_folder_path` returning None and each create their own batch row. This is acceptable for Phase 2 (single-threaded CLI). `reconcile_stale_batch_refs` handles cleanup if it becomes an issue.

**`file_count` on single-file batch rows starts at 1 and never updates.** Subsequent `capture_file` calls for the same subfolder find the existing batch and reuse its ID, but do not increment `file_count`. This is documented as a known approximation (see OQ-BATCH-2 above).

**Watcher-created batches before this fix have `folder_path = NULL`.** After the migration, these rows remain with NULL. `find_by_folder_path` will never match them, so `scan_capture` may create duplicate batch rows for folders the watcher processed before this fix shipped. Eventual consistency via `reconcile_stale_batch_refs`.
