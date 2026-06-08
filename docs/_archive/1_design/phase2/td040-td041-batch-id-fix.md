# Design: TD-040 + TD-041 — Batch-ID Fix

**ID prefix for behavior entries:** `P2-BAT`
**Date:** 2026-06-07
**Status:** Design complete — awaiting spec

---

## Cast of Characters

| Name | Role |
|------|------|
| Batch | A group of files that belong together because they came from the same subfolder drop. One row in the `batches` database table per group. |
| Batch ID | The unique number (foreign key) that links a file's database row to its batch. NULL if no batch applies. |
| Batch-worthy subfolder | A folder whose position in the vault signals grouped intent — any named subfolder *inside* `inbox/`, `Projects/<A>/`, or `Domain/<D>/`. NOT the root of those trees, and NOT the following system-managed folders: `attachment/` (binary storage), `.summaries/` (hidden AI output), or `Archive/` inside a domain folder (archived projects). |
| Folder path | The vault-relative path of the subfolder that created the batch (e.g. `inbox/Q2-reports`). Stored on the `batches` row for lookup. |
| Live batch membership | The current batch a file belongs to, based on where it sits *now* — updates when a file moves. |
| Capture pipeline | The six-stage process (extract → enrich → summarize → metadata → tag → store) that runs on each file. |
| Pipeline context | The shared object threaded through one pipeline run — holds config, DB path, correlation ID, and now batch ID. |
| `_location_context` | Existing helper in `vault/paths.py` that returns whether a path is inside a project, domain, or inbox. |

---

## Decision

**Chosen: Option A1 — dedicated `is_batch_subfolder()` predicate in `vault/paths.py`, paired with a new `find_by_folder_path()` function in `storage/batches.py`.**

The predicate cleanly encapsulates the "what counts as batch-worthy" rule in one place. The two callers (`capture_file` and `on_moved`) share the same helper, so boundary definition cannot drift between them. The rule lives next to the other vault path predicates where future readers will find it.

---

## Q1 Diagram — What Happens Inside

```
# TD-040/041 Batch-ID Fix — What Happens Inside (Option A1)
Scope: Shows what happens when a single file is captured or moved under
       the new batch-aware logic. Does NOT cover folder drops (already
       handled by capture_folder) or the classify pipeline.

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with two outcomes

            File arrives for capture
                      │
                      ▼
          ┌───────────────────────┐
          │ Is the parent folder  │
          │ a context-bearing     │
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

Simplified: Watcher-move path (batch ID updated in-place without re-capture)
            omitted to stay within the 7-box limit — same decision branch,
            exits after the DB update instead of running the full pipeline.

---

## Guardrail Checklist

- [x] **C-04 · PRAGMA foreign_keys=ON** — `find_by_folder_path` will use `get_connection()`, which already sets the pragma. No raw `sqlite3.connect()` call.
- [x] **C-05 · All schema changes via versioned .sql deltas** — the `folder_path TEXT` column is added via a new migration file (`006_batches_folder_path.sql`), never via Python `ALTER TABLE`.
- [x] **C-12 · Every public function returns Success or Failure** — `find_by_folder_path` returns `Result[int | None]`. No raw values at module boundary.
- [ ] **C-13 · Audit log** — batch_id assignment on `on_moved` is NOT an AI decision (it is deterministic path arithmetic). C-13 requires an audit row for AI decisions only. **Verdict: not applicable.** The existing `on_moved` audit path (ATTACHMENT_MOVED / SIBLING_ORPHANED / REHOMED) already covers the move event.
- [x] **C-17 · Never import CONFIG at module scope in tests** — all new test helpers pass `db_path=tmp_path / "kb.db"` explicitly.

---

## Implications

- `is_batch_subfolder(path, vault_cfg)` — new predicate in `vault/paths.py` — is the single authoritative answer to "does this path qualify for batch association?" It must return `True` when `path` is a named subfolder inside `inbox/`, `Projects/<A>/`, or `Domain/<D>/`, and `False` for: the root of those trees; and the following system-managed folders regardless of location — `attachment/` (binary storage), `.summaries/` (hidden AI output), and `Archive/` inside any domain folder (archived projects). It uses `_location_context()` plus a depth check (`len(rel.parts) >= 2` for projects/domain, any sub-path for inbox), plus an explicit name-blocklist for system folders.

- `find_by_folder_path(folder_path, db_path)` — new function in `storage/batches.py` — queries `batches` by `folder_path` with `ORDER BY created_at DESC LIMIT 1`. Returns `Result[int | None]` (None = no batch exists yet). This is the mechanism that lets two separately-captured files in the same subfolder share a batch ID without creating duplicate batch rows.

- The `batches` schema needs a new `folder_path TEXT` column (migration `006_batches_folder_path.sql`). The column is nullable (existing rows from watcher-triggered folder capture have no folder_path and will have NULL). NOT UNIQUE because re-drops are valid.

- `capture_file()` — the single-file entry point in `pipelines/capture.py` — currently sets no `batch_id` on any code path (TD-040). The fix inserts a new pre-pipeline step: call `is_batch_subfolder(path.parent, vault_cfg)` → if True, call `find_by_folder_path` → if None, call `_insert_batch()` with a synthetic "single-file" origin → stamp `ctx.batch_id` before `run_pipeline()`.

- `scan_capture()` in `pipelines/capture.py` currently iterates only files, never calls `capture_folder()` for subfolders (TD-041). The fix adds a subfolder-detection pass before the per-file loop: walk `inbox/`, `Projects/<A>/`, `Domain/<D>/` top-level for directories; skip any that already have a `batches` row (deduplication via `find_by_folder_path`); dispatch unprocessed ones via `capture_folder()`.

- `on_moved` in `vault/watcher.py` currently calls `rename_doc()` (path-only UPDATE) when a binary moves cross-folder (`_handle_binary_move` settled path). It does NOT update `batch_id` on the documents row. The fix: after `rename_doc()` succeeds, check `is_batch_subfolder(dst, vault_cfg)` → if True, look up or create batch → call `documents.update_batch_id(vault_path, batch_id)`.

- `documents.update_batch_id(vault_path, batch_id, db_path)` — does NOT currently exist in `storage/documents.py`. It is a targeted `UPDATE documents SET batch_id = ? WHERE vault_path = ?`. This is the only new write-path function needed.

- Existing `documents.upsert()` and `documents.replace_path()` already accept `batch_id` as a keyword argument and write it to the DB (wired in the code-review fix C1 from Phase 1.5). The new code only needs to SET `ctx.batch_id` correctly before the pipeline runs — the downstream write is already handled.

- `_insert_batch()` in `pipelines/capture.py` currently does NOT pass `folder_path` to `batches.insert()` (it passes `folder_name`, `destination_type`, `destination_name`, `confidence`, `status`, `file_count`). The fix adds `folder_path` as a new parameter to both `batches.insert()` and `_insert_batch()`.

- Module depth assessment: `vault/paths.py` is a **deep module** — small interface (named predicates), large implementation (handles all the path arithmetic for vault layout). Adding `is_batch_subfolder` deepens it further. Deletion test: if removed, its callers (`capture_file`, `on_moved`) would re-implement the rule inline with drift risk — earning its keep. `storage/batches.py` is **shallow** today (2 functions); adding `find_by_folder_path` and extending `insert` makes it moderately deeper.

- No new LLM calls. No new prompts. No confidence thresholds. Audit is not required for batch_id updates (deterministic path math, not an AI decision).

---

## Success Criteria

Entries written to `docs/system_behavior/behavior_inventory.yaml` with prefix `P2-BAT`:

| ID | Tier | Behavior |
|----|------|----------|
| P2-BAT-01 | full | Single file captured into a project subfolder gets batch_id |
| P2-BAT-02 | full | Two separately-captured files in same subfolder share batch_id |
| P2-BAT-03 | full | File captured directly into inbox root gets no batch_id |
| P2-BAT-04 | smoke | scan_capture detects and batch-captures an unprocessed inbox subfolder |
| P2-BAT-05 | full | scan_capture skips subfolder already captured by watcher |
| P2-BAT-06 | full | File moved into batch-worthy subfolder gets batch_id updated in-place |

---

## Options Explored

### Option A1 — Dedicated predicate in vault/paths.py (Recommended)

**What this means:** A new named rule (`is_batch_subfolder`) is added to the existing collection of vault path rules. Callers — the capture pipeline and the file-move handler — both call this one function. If the rule ever needs to change (say, `Projects/<A>/` root becomes batch-worthy too), there is exactly one place to edit.

**Approach:** New `is_batch_subfolder(path, vault_cfg) -> bool` in `vault/paths.py`. Uses `_location_context(path.parent)` plus depth check. Called from `capture_file()` and `on_moved` (watcher). `find_by_folder_path()` added to `storage/batches.py`.

**Files touched:**
- `src/storage/migrations/006_batches_folder_path.sql` — new migration
- `src/storage/batches.py` — `find_by_folder_path()`, extend `insert()` with `folder_path`
- `src/storage/documents.py` — `update_batch_id()` (targeted UPDATE)
- `src/vault/paths.py` — `is_batch_subfolder()` predicate
- `src/pipelines/capture.py` — `capture_file()` pre-pipeline batch step, `scan_capture()` subfolder pass, `_insert_batch()` signature
- `src/vault/watcher.py` — `_handle_binary_move()` settled path batch_id update

**Cost:**
- Dev effort: medium (6 files, new SQL migration, new predicate, two call sites)
- Runtime cost: 1 extra DB read per single-file capture (the `find_by_folder_path` lookup)
- Maintenance: `is_batch_subfolder` is a permanent addition to vault/paths.py

**Risk:**
- Schema: `folder_path` column added to `batches` table; existing rows get NULL. `find_by_folder_path` handles NULL gracefully (finds nothing → creates new batch).
- Concurrency: two parallel single-file captures from the same subfolder could both find `find_by_folder_path` returns None and each create a batch row. Because NOT UNIQUE is by design, this results in two batch rows for the same folder_path — the second capture gets a different batch_id. Eventual consistency via `reconcile_stale_batch_refs` (Stage 6) can normalize this, but P2-BAT-02 (shared batch_id) may be flaky under concurrent capture. Acceptable for Phase 2 CLI use (single-threaded).

**Module depth:**
- `is_batch_subfolder` in `vault/paths.py`: deletion test passes — its callers would re-implement boundary logic inline with drift risk. Real seam (2 callers: capture + watcher).
- `find_by_folder_path` in `storage/batches.py`: deletion test passes — callers would write raw SQL. Real seam (2 callers: capture_file + scan_capture subfolder pass).
- `update_batch_id` in `storage/documents.py`: deletion test passes — callers would use raw SQL. Speculative seam for now (1 caller: on_moved), but `scan_capture` subfolder pass may become a second caller.

**What it defers:** Concurrent-capture batch_id coalescence (acceptable, eventual consistency via reconcile). `batches` row `file_count` accuracy for single-file paths (starts at 1, never updates as more files arrive — low priority).

**Constraints check:**
- [x] C-04 — `get_connection()` used throughout
- [x] C-05 — migration file, not Python ALTER
- [x] C-12 — all new public functions return `Result[T]`
- [x] C-13 — N/A (no AI decisions in batch_id path)
- [x] C-17 — tests pass explicit `db_path`

---

### Option A2 — Inline check using existing helpers (Not recommended)

**What this means:** Instead of a named predicate, the batch-worthiness check is written inline in `capture_file` and `on_moved` using the existing `_location_context` helper. No new function is added to `vault/paths.py`.

**Approach:** Call `_location_context(path.parent, vault_cfg)` inline; add `len(rel.parts) >= 2` depth check inline at both call sites. Same `find_by_folder_path` and `update_batch_id` as Option A1.

**Files touched:** Same as A1 except `vault/paths.py` has no new function — the depth-check logic is duplicated across `capture.py` and `watcher.py`.

**Why not recommended:** The batch-worthiness boundary is encoded twice. When the rule changes (e.g. inbox subfolders become non-batch-worthy at some threshold), a developer must find both sites. The rule is already complex enough (depth check PLUS location_type check) that inline duplication is a maintenance liability. Fails the deletion test for the inline snippet — if one copy is deleted, the other runs different logic silently.

---

### Rejected alternatives

**B — Re-run capture_file on the moved file (on_moved watcher path).** Would trigger a full six-stage re-capture (LLM calls) just to update a batch_id. Wasteful and violates idempotency expectations. Rejected: batch_id update is a DB-only operation; no re-summarization needed.

**C — Time-window coalescer (files landing within N seconds share a batch).** Requires a background timer and shared state between capture invocations. Adds concurrency complexity and config parameters. The folder_path lookup is simpler, cheaper, and more semantically correct (grouping by intent, not by arrival time). Rejected: over-engineered for Phase 2 CLI use.

**D — Explicit user signal (`--batch-id` CLI flag).** Puts the burden on the user to declare intent. Contradicts the "zero organisational effort" design principle. Rejected: against core product goal.

---

## Known Tradeoffs

Choosing Option A1 over A2 means adding a new public function to `vault/paths.py`. The module already has many predicates; it stays coherent because the new function follows the same pattern (`path, vault_cfg -> bool`). The cost of A2's duplication outweighs A1's modest module growth.

Choosing a folder_path lookup (find-or-create) over a time-window coalescer means two rapidly-fired captures of the same subfolder may get different batch_ids under concurrent load. This is acceptable for Phase 2 (single-threaded CLI) and will be addressed by `reconcile_stale_batch_refs` if it becomes an issue.

---

## Risks

- **folder_path NULL on existing batches rows:** Migration adds the column nullable. `find_by_folder_path` will never match these rows (they have `folder_path IS NULL`). This is correct — watcher-triggered folder batches were created with a folder_name but no folder_path; they should not be matched by single-file lookup.
- **`_insert_batch()` signature change:** Adding `folder_path` parameter to `_insert_batch()` requires updating all 4 call sites in `capture_folder()`. This is a mechanical change but touches production code paths that have existing tests.
- **`scan_capture` subfolder deduplication:** The check "does a batch already exist for this folder_path?" is only reliable if `folder_path` was set when the watcher created the batch. Watcher-created batches currently have `folder_path = NULL`. After this fix ships, new watcher batches will have `folder_path` set. Until then, `scan_capture` may create duplicate batch rows for folders the watcher already processed before this fix shipped. Acceptable — `reconcile_stale_batch_refs` handles cleanup.

---

## Open Questions

- **OQ-BATCH-1:** Should `batches.insert()` accept `folder_path` as a required argument (breaking change to existing callers in `capture_folder`) or optional (defaults to `None`, existing callers unchanged)? Making it optional avoids touching all 4 `capture_folder` call sites, but allows new code to accidentally omit it. Making it required is safer but requires a coordinated update. Recommended: optional with a deprecation comment.

- **OQ-BATCH-2:** `_insert_batch()` creates a batch row with `file_count=1` for single-file captures. The count never updates as more files are added via subsequent `capture_file` calls. Is an accurate `file_count` required for Phase 2/8 briefing, or is it an approximation? If required, a separate UPDATE after each capture is needed. If not, document as a known approximation.

- **OQ-BATCH-3:** `scan_capture` subfolder detection: should it dispatch subfolders found under `Projects/<A>/` and `Domain/<D>/` (already LOCATED) via `capture_folder()`, which will attempt LLM classification — or should it skip LLM and go directly to the per-file pass (since location is already known from path)? The `capture_folder()` Case B path already handles this (loc_type in ("project", "domain") → skip LLM), so the answer is: dispatch via `capture_folder()` and let Case B handle it. But this should be confirmed before spec is written.

---

## ADR References

- ADR-0006 — Editable/No-Edit Split (accepted 2026-06-04): establishes `vault/paths.py` as the single source of path arithmetic. `is_batch_subfolder` follows this pattern.
- No new ADR proposed — this change is moderate in scope, uses an established pattern, and is not surprising to a future reader familiar with the vault/paths.py predicate collection.
