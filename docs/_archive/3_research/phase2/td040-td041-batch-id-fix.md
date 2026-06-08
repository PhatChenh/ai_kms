# Research: TD-040 + TD-041 — Batch-ID Fix
_Last updated: 2026-06-07 (re-check pass — A1 resolved)_

## Overview

This feature fixes two gaps in how the system groups related files together. Right now, when two files arrive from the same subfolder, the system processes them independently and stores no record of their shared origin. The fix adds a single naming rule ("is this folder batch-worthy?") and wires it into three places: single-file capture, the scan sweep, and the file-move watcher.

This research verified all ten spec assumptions against the actual source code. All ten assumptions are now validated or resolved. A1 — which was previously invalidated (spec misdescribed `_location_context` return type as a plain string) — has been corrected in the spec. The spec now explicitly states the function returns a `(zone_type, zone_name)` tuple and that `is_batch_subfolder()` must unpack it (`loc_type, _ = _location_context(...)`) before comparing. This is confirmed accurate against `vault/paths.py:264–304`. No blocking invalids remain.

All other assumptions — DB schema, function signatures, call-site counts, pipeline context shape, reconcile stage existence — remain confirmed accurate.

---

## Key Components

The six files this feature touches, and what each one does in the current codebase.

| File | Current role | What changes |
|------|-------------|-------------|
| `src/vault/paths.py` | Named vault path predicates (pure math, no DB) | Gains `is_batch_subfolder()` predicate |
| `src/storage/batches.py` | Two functions: `insert()` and `update_status()` | Gains `find_by_folder_path()`; `insert()` gains `folder_path` param |
| `src/storage/documents.py` | CRUD for the `documents` table | Gains `update_batch_id()` |
| `src/storage/migrations/` | Numbered SQL delta files (001–005 today) | Gains `006_batches_folder_path.sql` |
| `src/pipelines/capture.py` | Six-stage capture pipeline + folder orchestrator | `capture_file()` gets pre-pipeline batch step; `_insert_batch()` gains `folder_path`; `scan_capture()` gets subfolder pass |
| `src/vault/watcher.py` | Filesystem event handler with debounce | `_handle_binary_move()` settled path gains batch_id update |

---

## How It Works

**Today (broken state):** Every file capture runs independently. The system stores no information about which files came from the same subfolder. Two files from `Projects/Alpha/Q2-reports/` look completely unrelated in the database.

**After fix:** When a file lands in a subfolder (not the root of a project, domain, or inbox), the system checks: "does a batch already exist for this folder?" If yes, it stamps the same batch ID. If no, it creates a new batch row first. The watcher does the same check when a file moves cross-folder, updating the batch ID in-place without re-running the full AI pipeline.

**The critical junction:** Both the capture pipeline and the watcher call the same new predicate — `is_batch_subfolder()` — to decide if a folder qualifies. That predicate calls the existing `_location_context()` helper to determine vault zone, then adds a depth check (must be at least one level inside the zone root) and a system-folder blocklist (`attachment/`, `.summaries/`, `Archive/`).

---

## Spec Verification

All ten assumptions are validated or resolved. A1 was previously invalidated and has been corrected in the spec.

| ID | Spec Claim | Verdict | Evidence |
|----|-----------|---------|----------|
| A1 | ~~`_location_context(path, vault_cfg)` returns a **string** in `{"project", "domain", "inbox", None}`~~ **CORRECTED:** returns `tuple[str \| None, str \| None]`; `is_batch_subfolder()` must unpack with `loc_type, _ = _location_context(...)` before comparing | ✅ Resolved | `vault/paths.py:264–304` — signature is `-> tuple[str \| None, str \| None]`; spec §4 now explicitly states tuple unpack requirement |
| A2 | `batches.insert()` accepts `folder_name, destination_type, destination_name, confidence, status, file_count` and nothing else | ✅ Validated | `storage/batches.py:19–26` — exact match; `db_path` is an optional override, not a semantic param |
| A3 | `_insert_batch()` has exactly 4 call sites inside `capture_folder()` | ✅ Validated | `pipelines/capture.py:1309, 1353, 1365, 1377` — all four inside `capture_folder()`; definition at line 1222 |
| A4 | `documents.upsert()` already reads `batch_id` from context and writes it to the DB — no downstream change needed | ✅ Validated | `storage/documents.py:85–138` — `upsert()` accepts `batch_id` as a keyword arg and writes it in the SQL; the `store` stage in `capture_file` passes `ctx.batch_id` explicitly |
| A5 | `batches` table has `id`, `folder_name`, `created_at`; no `folder_path` column yet | ✅ Validated | `storage/migrations/002_batches.sql` — columns: `batch_id`, `folder_name`, `destination_type`, `destination_name`, `confidence`, `status`, `file_count`, `created_at`; no `folder_path` |
| A6 | `get_connection(db_path)` sets `PRAGMA foreign_keys=ON` automatically | ✅ Validated | `storage/db.py:17–19` — `_connect()` runs `PRAGMA foreign_keys=ON` before yielding; `get_connection` calls `_connect` |
| A7 | `capture_folder()` Case B (project/domain loc_type) skips LLM and proceeds per-file — dispatching via `scan_capture` will NOT trigger redundant AI calls | ✅ Validated | `pipelines/capture.py:1307–1313` — `if loc_type in ("project", "domain"):` immediately calls `_insert_batch` and `_capture_folder_files`; no LLM call on this branch |
| A8 | `ctx.batch_id` field exists on the pipeline context object | ✅ Validated | `core/pipeline.py:62` — `batch_id: int \| None = field(default=None)` on `PipelineContext` |
| A9 | `rename_doc()` in `storage/documents.py` returns `Result[int]` (the int is rowcount) | ✅ Validated | `storage/documents.py:287–315` — `rename()` returns `Success(cur.rowcount)` or `Failure`; watcher imports it as `rename as rename_doc` |
| A10 | `reconcile_stale_batch_refs` (Stage 6 of `kms reconcile`) exists | ✅ Validated | `pipelines/reconcile.py:385` — async function `reconcile_stale_batch_refs` is Stage 6; wired at line 717 |

---

## Edge Cases & Silent Failure Modes

**A1 silent failure (highest risk).** If `is_batch_subfolder()` is written as `if _location_context(path, vault_cfg) in ("project", "domain", "inbox")`, the check compares a tuple like `("project", "Alpha")` against bare strings — Python evaluates this as False always, silently. No error is raised. Every file skips batch grouping. The system continues to function but TD-040 and TD-041 are effectively unresolved.

**`_handle_binary_move` does NOT call `rename_doc` on the binary itself — only on the sibling.** The watcher's cross-folder path (`_settled=True`) calls `rename_doc(old_sibling_vp, new_sibling_vp)` at line 801 — this updates the sibling's DB row, not the binary's. The spec's proposed `update_batch_id()` targets the `documents` row for the sibling (vault_path of sibling), not the binary path. This is correct but requires clarity in the plan: "vault_path" in `update_batch_id` refers to the sibling's vault_path, not the binary's.

**`capture_folder()` Case B creates a batch row before per-file capture runs.** This means `capture_file()` (called by `_capture_folder_files`) runs with `ctx.batch_id` already set. If `capture_file()` later adds its own pre-pipeline batch step (spec §5a), it must not overwrite an already-set `ctx.batch_id`. The plan must guard: "only resolve batch if `ctx.batch_id is None`."

**`scan_capture()` subfolder pass dispatching `capture_folder()` for a project/domain dir.** Case B in `capture_folder()` skips LLM (A7 confirmed). But the subfolder pass in `scan_capture()` currently proposed walks inbox/Projects/Domain looking for unprocessed dirs and dispatches `capture_folder()`. If a subfolder is already partially processed (some files captured but no batch row), `find_by_folder_path` will return None and a new batch is created — which is correct behavior for the current design.

---

## Dependencies & Coupling

- `is_batch_subfolder()` depends on `_location_context()` — a private function in the same file. This is intentional (same module, not a cross-module coupling).
- `capture_file()` will depend on `batches.find_by_folder_path()` and `batches.insert()` — currently `capture.py` already imports `storage.batches` at the top (`import storage.batches as batches`), so no new import is needed.
- `watcher.py` will depend on `documents.update_batch_id()` — `watcher.py` already imports from `storage.documents` at the top (`from storage.documents import delete_by_path, get_by_path, rename as rename_doc`), so only `update_batch_id` needs to be added to that import.
- `vault/paths.py` has no existing import of `storage` — the new `is_batch_subfolder()` must remain pure path arithmetic with no DB calls (consistent with the file's existing pattern).

---

## Extension Points

- `is_batch_subfolder()` is the single rule definition. If the boundary changes (e.g. inbox root becomes batch-worthy, or a new system folder name is added to the blocklist), there is one place to edit.
- `find_by_folder_path()` is the deduplication mechanism. If the "most recent wins" policy (ORDER BY created_at DESC LIMIT 1) needs to change, it changes in one place.
- `update_batch_id()` currently has one planned caller (`_handle_binary_move`). The spec notes a possible second caller if `scan_capture` needs to back-fill moved files — that is out of scope but the function is designed for it.

---

## Open Questions

None. All ten spec assumptions are now verified and confirmed correct (nine originally validated, A1 invalidated then resolved via spec patch).

---

## Technical Debt Spotted

**`_insert_batch()` is called with 4 sites inside `capture_folder()` but `folder_path` is not yet a parameter.** After this fix, all 4 sites must be updated to pass `folder_path`. The plan must enumerate all 4 explicitly (lines 1309, 1353, 1365, 1377) to avoid missing one.

**`capture_file()` pre-pipeline batch step must guard `ctx.batch_id is None`.** When called from `capture_folder()` (which sets `ctx.batch_id` before dispatching per-file), the new step must be a no-op. Otherwise a folder-drop capture creates a second batch row for the same subfolder.

---

---
## Update — 2026-06-07
### Re-check: all assumptions resolved

Re-check mode entered because `## Invalidated Assumptions` was present from the prior run. A1 was the only invalidated assumption. The spec at `docs/3_specs/td040-td041-batch-id-fix.md` has been patched (corrected in the assumption table, Cast of Characters, and §4 component spec for `is_batch_subfolder()`).

| ID | Was | Now | Evidence |
|----|-----|-----|----------|
| A1 | `_location_context` returns a plain string in `{"project","domain","inbox",None}` | Returns `tuple[str \| None, str \| None]`; `is_batch_subfolder()` must unpack: `loc_type, _ = _location_context(path, vault_cfg)` | `vault/paths.py:264–266` — signature `-> tuple[str \| None, str \| None]` confirmed; spec §4 now states the unpack requirement explicitly |

A2–A10 sanity check: the A1 patch introduces no new inconsistency. The tuple-unpack correction is contained entirely within `is_batch_subfolder()` in `vault/paths.py` — it does not change any of the DB schema assumptions (A2, A5), call-site counts (A3), existing function signatures (A4, A9), FK pragma (A6), capture_folder branching (A7), pipeline context shape (A8), or reconcile stage existence (A10). All nine remain ✅ Validated.

All 10 assumptions validated. Ready for /plan td040-td041-batch-id-fix.
