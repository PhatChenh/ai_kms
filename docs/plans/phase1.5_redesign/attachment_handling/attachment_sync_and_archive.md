# Plan: attachment_sync_and_archive (Brief #3)
_Last updated: 2026-05-24_
_Status: [x] done_

> **Depends on**: Brief #1 (revise_attachment_layout) — per-project attachment layout, `.summaries/`
> traversal, `NoteMetadata.attachment_path` field — shipped 2026-05-23.
>
> **Depends on**: Brief #2 (attachment_capture_pipeline) — sibling write ordering (DECISION-025),
> CLUELESS pending-routing markers (DECISION-027), `_is_in_managed_attachment` helper — shipped 2026-05-24.

---

## Architecture

### Component overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  User actions                                                             │
│  Delete a PDF · Rename/move a PDF · Archive a project · kms reconcile    │
└────────────────────┬─────────────────────────────────────┬───────────────┘
                     │ OS file system events                │ kms reconcile command
                     ▼                                      ▼
┌────────────────────────────────┐   ┌──────────────────────────────────────┐
│  vault/watcher.py               │   │  pipelines/reconcile.py  [NEW]       │
│  [MODIFIED — Phases 1 & 3]      │   │  [BUILT — Phase 4]                   │
│  [closed]                       │   │  [closed]                            │
│                                 │   │                                      │
│  Watches vault in real-time     │   │  Full sync sweep — runs on demand    │
│                                 │   │  or via Phase 8 scheduler:           │
│  Phase 1 fix (TD-023):          │   │                                      │
│  · Broken attachment-skip       │   │  Stage 1 — Fix moved / deleted       │
│    replaced with per-project    │   │    note paths in the search index    │
│    path check using VaultConfig │   │                                      │
│                                 │   │  Stage 2 — Capture new binaries      │
│  Phase 3 adds:                  │   │    that have no summary yet          │
│  · Non-.md file deleted         │   │                                      │
│    → remove summary DB row      │   │  Stage 3 — Re-summarize binaries     │
│      (sibling .md stays on disk │   │    changed since last capture        │
│       until kms reconcile)      │   │                                      │
│  · Non-.md file renamed         │   │  Stage 4 — Remove index rows for     │
│    → rename sibling file        │   │    summaries whose binary is gone    │
│    → update attachment_path     │   │    (sibling .md stays on disk)       │
│      pointer in sibling         │   │                                      │
│  · Project folder archived      │   │  Returns ReconcileResult counts      │
│    → log "run kms reconcile"    │   │  (for Phase 8 daily briefing)        │
└─────────────────┬───────────────┘   └───────────────────┬──────────────────┘
                  │                                        │
         ┌────────▼────────────────────────────────────────▼──────────┐
         │  Shared infrastructure (all existing)                        │
         │                                                              │
         │  storage/documents.py — delete_by_path, rename, all_paths() │
         │  vault/writer.py      — write_note, move_note, read_note     │
         │  core/audit.py        — write() for every decision           │
         │  vault/indexer.py     — detect_changes, scan_vault           │
         │  pipelines/capture.py — capture_file (reconcile delegates    │
         │                         to this for new + stale binaries)    │
         └──────────────────────────────────────────────────────────────┘

vault/paths.py  [MODIFIED — Phase 2]                   [extensible: config]
  + domain_archive(name, vault_config) → Path
    "Where does a completed project's archive live?"
    Returns: vault_root / "Domain" / <D> / archive_dir
    Pattern identical to domain_attachment() already present

core/config.py VaultConfig  [MODIFIED — Phase 2]
  - archive_path @property REMOVED (TD-AS-2)
    was: vault_root / "Archive"  — global folder that no longer exists
    zero pipeline callers; test at test_config.py:350-351 also deleted
  archive_dir: str = "Archive" field KEPT — used by domain_archive() helper
```

---

### Real-time sync — user deletes a non-note file (e.g. a PDF)

```
  User (Finder)       vault/watcher.py            Search index         Audit log
       │                     │                          │                   │
       │  Delete              │                          │                   │
       │  "Q2 Report.pdf"     │                          │                   │
       │  from Projects/A/    │                          │                   │
       │  attachment/         │                          │                   │
       │─────────────────────▶  file-deleted event       │                   │
       │                      │                          │                   │
       │                      │  Should I skip this?     │                   │
       │                      │  (TD-023 fixed — uses    │                   │
       │                      │   per-project path check │                   │
       │                      │   not broken global dir) │                   │
       │                      │  → No, process it        │                   │
       │                      │                          │                   │
       │                      │  Is this a non-.md file? │                   │
       │                      │  (not a note — any ext   │                   │
       │                      │   other than .md)        │                   │
       │                      │  → YES                   │                   │
       │                      │                          │                   │
       │                      │  Find its summary path:  │                   │
       │                      │  Projects/A/attachment/  │                   │
       │                      │  .summaries/             │                   │
       │                      │  "Q2 Report.md"          │                   │
       │                      │                          │                   │
       │                      │  Remove summary from ────▶                   │
       │                      │  search index            │  1 row removed    │
       │                      │                          │───────────────────▶
       │                      │                          │  SIBLING_ORPHANED │
       │                      │                          │                   │
       │                      │  Note: "Q2 Report.md" stays on disk until   │
       │                      │  kms reconcile runs (Stage 4 deletes it).   │
       │                      │  The file is invisible to Obsidian and       │
       │                      │  already removed from AI search — no        │
       │                      │  harm accumulating until nightly reconcile.  │
```

---

### Real-time sync — user renames a non-note file

```
  Before rename:                                After rename:
  ┌──────────────────────────────────┐          ┌──────────────────────────────────┐
  │ attachment/                      │          │ attachment/                      │
  │   Q2 Report.pdf      ◀─ points ──┤          │   Q2 Strategy.pdf    ◀─ points ──┤
  │   .summaries/                    │          │   .summaries/                    │
  │     Q2 Report.md ────────────────┘          │     Q2 Strategy.md ──────────────┘
  └──────────────────────────────────┘          └──────────────────────────────────┘
        ↑ before                                       ↑ watcher produces this

  Steps watcher runs when the PDF is renamed:

  Step 1 — RENAME the summary file on disk
           .summaries/Q2 Report.md  →  .summaries/Q2 Strategy.md
           (via vault/writer.py move_note)

  Step 2 — UPDATE the pointer inside the renamed summary
           attachment_path: "Projects/A/attachment/Q2 Strategy.pdf"
           (read existing content with read_note, then write_note with updated attachment_path)

  Step 3 — UPDATE the search index row
           old path: .summaries/Q2 Report.md  →  new path: .summaries/Q2 Strategy.md
           (documents.rename)

  Step 4 — RECORD ATTACHMENT_MOVED in audit log

  Edge case — binary moved to a different project or outside attachment/:
    Old sibling is orphaned (Steps 3+4 only, same as delete).
    Reconcile Stage 2 will capture the binary at its new location.
```

---

### kms reconcile — decision flow

```
  kms reconcile
       │
       ▼
  ┌────────────────────────────────────────────────────────┐
  │  Stage 1 — Fix moved and deleted notes in search index  │
  │  Walk vault, compare all paths to the index             │
  │  Use content fingerprinting to detect moves             │
  │  (Same logic as kms capture --scan, applied here as     │
  │   the first step of every reconcile run)                │
  └──────────────────────────┬─────────────────────────────┘
                             │ DB paths updated
                             ▼
  ┌────────────────────────────────────────────────────────┐
  │  Stage 2 — Find binaries with no summary               │
  │  Walk every attachment/ folder in the vault            │
  │  For each binary file found:                           │
  └─────────────────────┬──────────────────────────────────┘
                        │
           Has a summary in .summaries/?
                        │
         NO ────────────┘─────────────── YES
          ▼                                ▼
  Run full capture pipeline            (go to Stage 3)
  (same as kms capture <file>)
  Creates summary + indexes it
  Audit: ORPHAN_BINARY_CAPTURED
          │
          ▼
  ┌────────────────────────────────────────────────────────┐
  │  Stage 3 — Find stale binaries                         │
  │  "Was the file changed AFTER its summary was written?" │
  │  Compare: binary file date > summary file date         │
  └─────────────────────┬──────────────────────────────────┘
                        │
              Binary is newer than summary?
                        │
         YES ───────────┘─────────────── NO
          ▼                                ▼
  Re-run full capture pipeline          Skip (up to date)
  (re-reads binary, writes updated
   summary, preserves attachment_path
   pointer — TD-AS-1 fix ensures this)
  Audit: BINARY_STALE_RESUMMARIZED
          │
          ▼
  ┌────────────────────────────────────────────────────────┐
  │  Stage 4 — Find orphaned summaries                     │
  │  Walk every .summaries/ folder in the vault            │
  │  For each summary .md found:                           │
  │  Does the file at attachment_path still exist on disk? │
  └─────────────────────┬──────────────────────────────────┘
                        │
              Binary still exists?
                        │
         YES ───────────┘─────────────── NO
          ▼                                ▼
        Keep                      Remove summary .md from disk
                                  Remove row from search index
                                  Audit: ORPHAN_SIBLING_CLEANED
                                  (Note: attachment_path=None or
                                   stale pointer → same treatment)
          │
          ▼
  ✅ Print summary:
     "N paths reconciled, N new binaries captured,
      N stale binaries re-summarized, N orphans cleaned"
```

---

## Approach

Fix two pre-existing bugs first (Phase 1) so the new sync logic has a correct foundation. Add archive path helpers as a standalone cleanup (Phase 2). Wire real-time sync into the watcher callbacks (Phase 3). Build the comprehensive reconcile command last (Phase 4) — it reuses `capture_file` from the existing capture pipeline and provides the "full sync" surface the Phase 8 scheduler will call.

The watcher handles the common real-time case (single binary event). The reconcile command handles bulk correction, stale detection, and orphan cleanup. These two surfaces are complementary — neither duplicates the other.

---

## Phases

### Phase 1 — Prerequisite Fixes (TD-023 + TD-AS-1 + false-success logging)

**Goal**: Fix two bugs that would corrupt sync behavior if left in place before Phase 3 code is added.

**Diagrams**:

```
FIX 1 (TD-023): _should_skip() — watcher was checking the wrong folder

  _should_skip(path)
  "Should I completely ignore this file event and do nothing?"

  ┌─ BEFORE (broken) ────────────────────────────────────────────────────┐
  │  Check: is this file inside  Vault/attachment/  ?                    │
  │                                       ↑                              │
  │                              global folder that no longer exists     │
  │  → Answer: NEVER (folder doesn't exist, so nothing can be inside it) │
  │  → Every binary in every project passes through — never skipped      │
  │  → watcher fires capture_file() again on binaries the pipeline       │
  │    already captured 3 seconds ago (double LLM calls, wasted cost)    │
  └──────────────────────────────────────────────────────────────────────┘

  ┌─ AFTER (fixed) ──────────────────────────────────────────────────────┐
  │  Calls _is_in_managed_attachment(path, vault_config)                 │
  │         ↑ "Is this file under any Projects/*/attachment/ or          │
  │            Domain/*/attachment/ folder?" (reads vault_config for     │
  │            correct subfolder names — no hardcoding)                  │
  │                                                                      │
  │  path = Projects/A/attachment/Q2 Report.pdf                          │
  │  → Answer: YES → SKIP (create event; sync callbacks still run later) │
  └──────────────────────────────────────────────────────────────────────┘
```

```
FIX 2 (TD-AS-1): scan_capture modified loop was silently wiping attachment_path

  kms capture --scan  →  scan_capture()
  "Find all changed files and re-process them"

  ┌─ BEFORE (bug) ───────────────────────────────────────────────────────┐
  │  Modified file list includes:                                        │
  │    Projects/A/attachment/.summaries/Q2 Report.md                    │
  │            ↑ this is a SUMMARY file, not a source note               │
  │                                                                      │
  │  capture_file(".summaries/Q2 Report.md") called                      │
  │    → _store_md() builds NoteMetadata from scratch                    │
  │         attachment_path field = None  ← not set anywhere             │
  │    → write_note() writes this to disk                                │
  │    → RESULT: frontmatter "attachment_path: Projects/A/attachment/    │
  │              Q2 Report.pdf" is gone — binary pointer wiped silently  │
  │    Phase 4 MCP tools can no longer find the binary from search       │
  └──────────────────────────────────────────────────────────────────────┘

  ┌─ AFTER (fixed) ──────────────────────────────────────────────────────┐
  │  Before calling capture_file(), modified loop checks:                │
  │    vault_config.summaries_subdir in path.parts                       │
  │    ↑ "Is this file inside a .summaries/ folder?"                    │
  │  → YES → skip  (summary files are owned by the sync pipeline,        │
  │                  not the capture pipeline)                           │
  │  attachment_path frontmatter preserved on disk                       │
  └──────────────────────────────────────────────────────────────────────┘
```

```
FIX 3: False-success logging in on_delete / on_move

  ┌─ BEFORE (misleading) ────────────────────────────────────────────────┐
  │  watcher.on_delete("Projects/A/attachment/Q2 Report.pdf")            │
  │                                                                      │
  │  documents.delete_by_path("...Q2 Report.pdf")                        │
  │  ↑ tries to remove binary from search index                          │
  │  ← binaries are NOT indexed (only .md summaries are — DECISION-018)  │
  │  Returns Success(rows_deleted=0)                                     │
  │                                                                      │
  │  Code: case Success() → log "watcher.deleted" ← FALSE               │
  │  Log says something was deleted. Nothing was.                        │
  └──────────────────────────────────────────────────────────────────────┘

  ┌─ AFTER (honest) ─────────────────────────────────────────────────────┐
  │  Same call returns Success(rows_deleted=0)                           │
  │  Code checks rows_deleted value:                                     │
  │    if 0  → _wlog.warning("watcher.binary_not_in_index")             │
  │    if >0 → _wlog.info("watcher.deleted")                            │
  └──────────────────────────────────────────────────────────────────────┘
```

**Steps**:

1. **TD-023 — Fix watcher constructor signature** (`vault/watcher.py`)
   - Change `_VaultEventHandler.__init__(self, attachment_path: Path, ...)` →
     `_VaultEventHandler.__init__(self, vault_config: VaultConfig, ...)`
   - In `_should_skip`: replace `self._attachment_path in path.parents` with
     `_is_in_managed_attachment(path, self._vault_config)` for non-.md files
   - Move `_is_in_managed_attachment` from `vault/indexer.py` to `vault/paths.py`
     (it is a path utility; watcher and indexer both import it from paths)
   - Update `VaultWatcher.__init__` to accept `vault_config: VaultConfig` and pass through
   - Update `cli/main.py:204`: `VaultWatcher(attachment_path=...)` →
     `VaultWatcher(vault_config=CONFIG.main.vault)` (retires last `# COUPLING:` marker from TD-022)

2. **TD-AS-1 — Fix sibling re-capture bug** (`pipelines/capture.py`)
   - In `scan_capture` modified loop: add filter to skip paths where the path is under a
     `.summaries/` directory (i.e. `vault_config.summaries_subdir in path.parts`)
   - 3-line change. Prevents `capture_file(sibling_path)` from running on a sibling that
     was written by the sync pipeline — which would call `_store_md` and wipe `attachment_path`
     from frontmatter (TD-AS-1 latent bug).

3. **False-success logging fix** (`vault/watcher.py`)
   - In `on_delete` and `on_move` handlers: check rowcount from `delete_by_path` / `rename_doc`
   - If rowcount = 0, log `_wlog.warning("watcher.binary_not_in_index")` instead of
     `_wlog.info("watcher.renamed")` / `_wlog.info("watcher.deleted")`
   - This surfaces the "binary event fired but no DB row found" case that currently logs false success

**Files to modify**:
- `vault/watcher.py` — constructor, `_should_skip`, false-success fix
- `vault/indexer.py` — remove `_is_in_managed_attachment` (moved to paths.py); update internal call
- `vault/paths.py` — add `_is_in_managed_attachment` (moved from indexer.py)
- `pipelines/capture.py` — 3-line sibling skip in modified loop
- `cli/main.py` — VaultWatcher instantiation (retire final `# COUPLING:` marker)

**Test files to modify**:
- `tests/test_vault/test_watcher.py` — update all tests using `attachment_path = root / "attachment"`
  fixture and `_make_handler` helper to new `vault_config` arg
- `tests/test_pipelines/test_capture_phase9.py` — add test: `.summaries/**` path in modified list
  is skipped and `capture_file` is NOT called for it
- `tests/test_vault/test_paths.py` — add test for `_is_in_managed_attachment` (moved function)

**Test criteria**:
- [ ] All existing watcher tests pass with new `vault_config` constructor signature
- [ ] New test: `.summaries/report.md` in `scan_capture` modified list → `capture_file` not called
- [ ] New test: `_is_in_managed_attachment` returns True for `Projects/A/attachment/report.pdf`,
      False for `Projects/A/report.md` and `inbox/report.pdf`
- [ ] `uv run pytest tests/test_vault/ tests/test_pipelines/` passes

**Status**: [x] done

**Completed**: 2026-05-24
**Notes**:
- Moved `_is_in_managed_attachment` from `vault/indexer.py` to `vault/paths.py` (now importable by both indexer and watcher).
- Updated `_VaultEventHandler` + `VaultWatcher` constructors: `attachment_path: Path` → `vault_config: VaultConfig`.
- `_should_skip` now uses `_is_in_managed_attachment` for non-.md files only — .md files in attachment dirs are valid notes.
- `scan_capture` modified loop skips paths containing `summaries_subdir` to prevent re-capturing sibling .md files (TD-AS-1).
- False-success logging fixed in `cli/main.py`: `on_delete`/`on_move` now check `rowcount` and log `watcher.binary_not_in_index` warning when 0.
- `cli/main.py:204` `VaultWatcher(attachment_path=...)` → `VaultWatcher(vault_config=CONFIG.main.vault)` — last TD-022 COUPLING marker retired.
- Watcher test `_make_handler` helper updated to create `VaultConfig`; all 18 watcher tests pass.
- 7 new path tests for `_is_in_managed_attachment`; 1 new phase9 test for `.summaries/` skip.
- Full suite: 626 passed, 1 skipped, 1 warning (pre-existing RuntimeWarning in test_claude_cli_provider.py).

---

### Phase 2 — Archive Layout Helpers (TD-AS-2 + TD-AS-3)

**Goal**: Add the `domain_archive` path helper and remove the stale global `archive_path` property.

**Diagram**:

```
  REMOVED: VaultConfig.archive_path  (the @property — a computed shortcut)
  ┌──────────────────────────────────────────────────────────────────┐
  │  vault_config.archive_path  →  Vault/Archive/                    │
  │                                       ↑                          │
  │                               global folder — does not exist     │
  │  • zero callers in any pipeline file (confirmed by code search)  │
  │  • test at test_config.py:350-351 also deleted (same cleanup     │
  │    pattern as when attachment_path was removed in Phase 1.5)     │
  └──────────────────────────────────────────────────────────────────┘

  ADDED: domain_archive(name, vault_config)  (a plain function in vault/paths.py)
  ┌──────────────────────────────────────────────────────────────────┐
  │  domain_archive("Finance", vault_config)                         │
  │  → vault_root / "Domain" / "Finance" / archive_dir              │
  │                                               ↑                  │
  │                               reads archive_dir from config      │
  │                               (default: "Archive")               │
  │                                                                  │
  │  Vault layout this enables:                                      │
  │  Domain/Finance/                                                 │
  │    Archive/              ← domain_archive("Finance", cfg)        │
  │      Q1 Planning/        ← completed project dragged here        │
  │        brief.pdf                                                 │
  │        .summaries/                                               │
  │          brief.md        ← still searchable via kms reconcile   │
  │    Active Project/       ← current active work                   │
  │      notes.md                                                    │
  │                                                                  │
  │  Same shape as domain_attachment() already in vault/paths.py:   │
  │    domain_attachment("Finance", cfg) → Domain/Finance/attachment/│
  │    domain_archive("Finance", cfg)    → Domain/Finance/Archive/   │
  └──────────────────────────────────────────────────────────────────┘
```

**Steps**:

1. **Add `domain_archive` helper** (`vault/paths.py`)
   - New function: `domain_archive(name: str, vault_config: VaultConfig) -> Path`
   - Returns: `vault_config.root / "Domain" / name / vault_config.archive_dir`
   - Pattern identical to `domain_attachment(name, vault_config)` already present
   - Example: `domain_archive("Product", cfg)` → `Vault/Domain/Product/Archive/`

2. **Remove `VaultConfig.archive_path` @property** (`core/config.py`)
   - Delete the `@property archive_path(self) -> Path` method (TD-AS-2)
   - The property pointed to `vault_root / archive_dir` (global `Vault/Archive/` folder)
   - Zero pipeline callers confirmed by research grep
   - Keep `archive_dir: str = Field(default="Archive")` — used by `domain_archive()` helper
   - Note: `archive_dir` was already a Field (not a @property) so no semantic change needed there

3. **Update tests** (two test files)
   - `tests/test_core/test_config.py`: delete test at lines 350-351 that asserts
     `vault.archive_path == tmp_path / "Archive"` (same pattern as TD-RAL-5 for attachment_path)
   - `tests/test_vault/test_paths.py`: add 2 tests for `domain_archive`:
     (a) returns correct path for a named domain
     (b) respects custom `archive_dir` in VaultConfig

**Files to modify**:
- `vault/paths.py` — add `domain_archive`
- `core/config.py` — remove `archive_path` @property
- `tests/test_core/test_config.py` — delete archive_path test
- `tests/test_vault/test_paths.py` — add domain_archive tests

**Test criteria**:
- [ ] `domain_archive("Product", cfg)` returns `cfg.root / "Domain" / "Product" / "Archive"`
- [ ] `domain_archive` respects `archive_dir` override in VaultConfig
- [ ] No test in `test_config.py` references `archive_path` after deletion
- [ ] `uv run pytest tests/test_vault/test_paths.py tests/test_core/test_config.py` passes

**Status**: [x] done

**Completed**: 2026-05-24
**Notes**:
- Added `domain_archive(name, vault_config)` to `vault/paths.py` — pattern matches `domain_attachment`.
- Takes `vault_config: VaultConfig` parameter (unlike older helpers that use CONFIG singleton) for testability.
- Removed `VaultConfig.archive_path` @property — zero callers, pointed to global `Vault/Archive/` which no longer exists.
- Kept `archive_dir: str = "Archive"` Field — used by `domain_archive()`.
- Deleted `test_archive_path_is_root_plus_archive_dir` from `tests/test_core/test_config.py`.
- 2 new tests in `TestDomainArchive` class.

---

### Phase 3 — Watcher Sync Callbacks (OQ-AS1)

**Goal**: Real-time propagation of binary delete and rename events to sibling summaries.

**Diagrams**:

```
DIAGRAM A: Binary deleted → summary row removed from search index

  User deletes "Q2 Report.pdf" in Finder
       │
       ▼
  watcher on_delete fires
       │
       ▼
  _is_binary(path)  ─── "Is the file extension anything other than .md?"
       │ YES (it's a .pdf)
       ▼
  _sibling_for(path, vault_config)  ─── "Where would this file's summary live?"
  formula: same parent folder / ".summaries" / same filename + ".md"
  → Projects/A/attachment/.summaries/Q2 Report.md
       │
       ▼
  documents.delete_by_path(sibling_path)  ─── "Remove summary row from search index"
  ← if rowcount = 0: log warning (summary was already gone — no problem)
       │
       ▼
  audit.write(outcome="SIBLING_ORPHANED")  ─── "Record: why this summary disappeared"
       │
       ▼
  "Q2 Report.md" stays on disk in .summaries/ (ghost file — invisible to
  Obsidian and AI search). kms reconcile Stage 4 will delete it on next run.
```

```
DIAGRAM B: Binary renamed → summary renamed + pointer updated

  User renames "Q2 Report.pdf" → "Q2 Strategy.pdf" in Finder
  (file stays in the same attachment/ folder — just a name change)
       │
       ▼
  watcher on_move fires  (src="Q2 Report.pdf",  dst="Q2 Strategy.pdf")
       │
       ▼
  _is_binary(src)  ─── "Non-.md file?" → YES
       │
       ▼
  Same folder? (dst.parent == src.parent)  → YES
       │
       ▼
  Step 1 ─── move_note(old_sibling, new_sibling, actor="ai")
              "Rename the summary file on disk"
              .summaries/Q2 Report.md  →  .summaries/Q2 Strategy.md

  Step 2 ─── read_note(new_sibling)
              "Read the renamed summary's current content and metadata"

  Step 3 ─── Update metadata.attachment_path in memory
              OLD: "Projects/A/attachment/Q2 Report.pdf"
              NEW: "Projects/A/attachment/Q2 Strategy.pdf"

  Step 4 ─── write_note(new_sibling, body, updated_metadata, actor="ai")
              "Write the summary back to disk with the corrected pointer"
              (write_note checks updated_by_human — if True, skips write
               and logs a conflict instead)

  Step 5 ─── documents.rename(old_sibling_path, new_sibling_path)
              "Update the search index row to point to the new summary path"

  Step 6 ─── audit.write(outcome="ATTACHMENT_MOVED")

  ┌──────────────────────────────────────────────────────────┐
  │  RESULT:                                                  │
  │  attachment/                                              │
  │    Q2 Strategy.pdf        ← renamed by user              │
  │    .summaries/                                            │
  │      Q2 Strategy.md       ← renamed by watcher (Step 1)  │
  │        attachment_path:                                   │
  │          "attachment/Q2 Strategy.pdf"                     │
  │          ↑ pointer updated by watcher (Steps 3–4)        │
  └──────────────────────────────────────────────────────────┘

  Edge case — binary moved to a DIFFERENT project or outside attachment/:
    Steps 1–5 above do NOT run.
    Old summary is orphaned instead (same as Diagram A).
    kms reconcile Stage 2 will capture the binary at its new location.
```

**Steps**:

1. **Add `_is_binary(path: Path) -> bool` helper** (`vault/watcher.py`)
   - Returns `path.suffix.lower() != ".md"`
   - Used to distinguish note files from binary attachments in all callbacks

2. **Add `_sibling_for(binary: Path, vault_config: VaultConfig) -> Path`** (`vault/watcher.py`)
   - Returns `binary.parent / vault_config.summaries_subdir / (binary.stem + ".md")`
   - Always returns a path — no None case. Callers handle `Success(0)` from `delete_by_path`
     gracefully (rowcount = 0 → log warning, not error).
   - Works for all binary locations: `Projects/<A>/attachment/`, `Domain/<D>/attachment/`,
     and `inbox/` (CLUELESS path — DECISION-027 places siblings at `inbox/.summaries/`).

3. **Extend `on_delete`** (`vault/watcher.py`)
   - After existing logic (which handles `.md` delete → `delete_by_path` correctly):
   - If `_is_binary(path)`:
     - Derive `sibling_vp = _sibling_for(path, vault_config)` as vault-relative string
     - If `sibling_vp` is not None: call `documents.delete_by_path(sibling_vp)`
     - Log warning if rowcount = 0 (sibling had no DB row — already unindexed)
     - Call `core.audit.write(outcome="SIBLING_ORPHANED", ...)`
   - Sibling `.md` file stays on disk (deferred to reconcile Stage 4)

4. **Extend `on_move`** (`vault/watcher.py`)
   - If `_is_binary(src)`:
     - **Same `attachment/` folder (simple rename)**: `dst.parent == src.parent`
       1. `move_note(old_sibling_abs, new_sibling_abs, actor="ai")` — renames file on disk + updates DB vault_path
       2. `read_note(new_sibling_abs)` → get existing body + metadata
       3. Update `metadata.attachment_path` to new binary vault-relative path
       4. `write_note(new_sibling_abs, body, metadata, actor="ai")` — persists updated pointer
       5. `core.audit.write(outcome="ATTACHMENT_MOVED", ...)`
     - **Different folder or outside `attachment/`**: treat as delete (Steps from `on_delete`)
       - Old sibling orphaned. Reconcile Stage 2 will capture binary at new location.
   - If `event.is_directory`:
     - Log `_wlog.warning("watcher.directory_moved", msg="run kms reconcile to sync search index")`
     - Do NOT attempt DB updates (scan_capture reconciliation window is the correct path per OQ-AS3)

5. **New audit outcome strings**
   - `SIBLING_ORPHANED` — binary deleted or moved away from attachment/; sibling DB row removed
   - `ATTACHMENT_MOVED` — binary renamed within same attachment/; sibling file + DB row updated
   - These follow existing ALLCAPS_UNDERSCORE convention (`CAPTURED`, `ROUTED`, `CLUELESS`)
   - No schema change needed — `audit_log.outcome` is free-form TEXT (confirmed research)

**Files to modify**:
- `vault/watcher.py` — `_is_binary`, `_sibling_for`, `on_delete`, `on_move`

**Test files to modify**:
- `tests/test_vault/test_watcher.py` — add tests:
  - Binary delete → sibling DB row deleted, sibling .md stays on disk, SIBLING_ORPHANED audited
  - Binary rename (same folder) → sibling file renamed, attachment_path updated, ATTACHMENT_MOVED audited
  - Binary moved to different attachment/ → old sibling orphaned (SIBLING_ORPHANED), not renamed
  - Directory moved → warning logged, no DB changes

**Test criteria**:
- [ ] Binary delete event → `documents.delete_by_path(sibling_vp)` called with correct path
- [ ] Binary rename event → sibling file moved + frontmatter `attachment_path` updated to new binary path
- [ ] Binary moved to different folder → old sibling orphaned (not renamed)
- [ ] Directory moved event → `_wlog.warning` called, no `delete_by_path` / `rename` calls
- [ ] `_sibling_for` always returns a path; `delete_by_path` returns `Success(0)` gracefully when row not found
- [ ] `uv run pytest tests/test_vault/test_watcher.py` passes

**Status**: [x] done

**Completed**: 2026-05-24
**Notes**:
- Added `_is_binary(path)` and `_sibling_for(binary, vault_config)` module-level helpers to `vault/watcher.py`.
- Extended `on_deleted`: binary files trigger `_handle_binary_delete` which calls `documents.delete_by_path(sibling_vp)` + `audit.write(outcome="SIBLING_ORPHANED")`.
- Extended `on_moved`: binary files trigger `_handle_binary_move` — same-folder renames invoke `move_note` + `write_note` (update `attachment_path`) + `documents.rename` + `audit.write(outcome="ATTACHMENT_MOVED")`; different-folder moves orphan old sibling (same as delete path).
- Binary sync uses unique debounce key prefix (`bin:`) to avoid collisions with user callbacks.
- Vault-relative paths computed from `self._root` (not CONFIG singleton) for testability.
- Directory move events: `event.is_directory` returns early (existing behavior); binary sync callbacks log warning on rowcount=0.
- 7 new helper tests (`_is_binary` × 4, `_sibling_for` × 3) + 3 new sync callback tests.
- Full suite: 637 passed, 1 skipped, 1 warning (pre-existing).

---

### Phase 4 — `kms reconcile` Command (OQ-AS2 + OQ-AS3)

**Goal**: A comprehensive, scheduler-friendly CLI command that syncs the search index to the vault
in full — handles all cases the real-time watcher may have missed and re-summarizes stale binaries.

**Diagram**:

```
  kms reconcile  (cli/main.py → pipelines/reconcile.py)
       │
       ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │  reconcile_paths(ctx)                                               │
  │  "Bring search index paths in sync with what's actually on disk"    │
  │                                                                     │
  │  detect_changes(vault_config, db_path)                              │
  │    ↑ walks entire vault + diffs every path against the index        │
  │    returns: moved entries, deleted entries, new entries             │
  │                                                                     │
  │  documents.rename(old_path, new_path)  ← for each moved file        │
  │  documents.delete_by_path(path)        ← for each deleted file      │
  │                                                                     │
  │  Returns: paths_reconciled count                                    │
  └─────────────────────────────────────┬───────────────────────────────┘
                                        │
                                        ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │  reconcile_orphan_binaries(ctx, result)                             │
  │  "Find binaries that have no summary yet"                           │
  │                                                                     │
  │  Scans all attachment/ folders  (vault_config.root.rglob("attachment/"))
  │  For each binary file:                                              │
  │    Does .summaries/<same-name>.md exist on disk?                    │
  │    NO →  capture_file(binary_path, ctx)                             │
  │          ↑ full capture pipeline (same as kms capture <file>)       │
  │          creates summary + indexes it                               │
  │          Audit: ORPHAN_BINARY_CAPTURED                              │
  │                                                                     │
  │  Returns: new_captures count                                        │
  └─────────────────────────────────────┬───────────────────────────────┘
                                        │
                                        ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │  reconcile_stale_binaries(ctx, result)                              │
  │  "Find binaries that changed after their summary was written"        │
  │                                                                     │
  │  For each binary that HAS a summary:                                │
  │    binary file date  >  summary file date?                          │
  │    YES → capture_file(binary_path, ctx)                             │
  │           ↑ re-reads binary, rewrites summary with new content,     │
  │             preserves attachment_path pointer (TD-AS-1 fix in       │
  │             Phase 1 guarantees _store_md does not wipe the pointer) │
  │           Audit: BINARY_STALE_RESUMMARIZED                          │
  │                                                                     │
  │  Returns: restale_count                                             │
  └─────────────────────────────────────┬───────────────────────────────┘
                                        │
                                        ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │  reconcile_orphan_siblings(ctx, result)                             │
  │  "Find summaries whose binary is gone"                              │
  │                                                                     │
  │  Scans all .summaries/ folders in vault                             │
  │  For each summary .md:                                              │
  │    read_note(sibling) → metadata.attachment_path                    │
  │    Does that binary path still exist on disk?                       │
  │    NO (or attachment_path is None):                                 │
  │      updated_by_human = True? → skip + log warning (human edited)  │
  │      otherwise:                                                     │
  │        documents.delete_by_path(sibling_vault_path) ← remove row   │
  │        sibling_abs.unlink()  ← delete ghost .md file from disk     │
  │        Audit: ORPHAN_SIBLING_CLEANED                                │
  │                                                                     │
  │  Returns: orphans_cleaned count                                     │
  └─────────────────────────────────────┬───────────────────────────────┘
                                        │
                                        ▼
  ReconcileResult printed to terminal:
  "3 paths reconciled, 1 new binary captured,
   2 stale binaries re-summarized, 0 orphans cleaned"

  Also returned as structured data for Phase 8 briefing pipeline to read.
```

**Steps**:

1. **Define `ReconcileResult` dataclass** (`pipelines/reconcile.py`)
   ```python
   @dataclass(frozen=True)
   class ReconcileResult:
       paths_reconciled: int    # moved/deleted note paths fixed in DB
       new_captures: int        # orphan binaries captured (new summaries created)
       restale_count: int       # stale binaries re-summarized
       orphans_cleaned: int     # orphan sibling DB rows removed
   ```

2. **Stage 1 — `reconcile_paths(ctx)`** (`pipelines/reconcile.py`)
   - Call `detect_changes(vault_config, db_path)` from `vault/indexer.py`
   - For each entry in `changes.moved`: call `documents.rename(old_vp, new_vp)`
   - For each entry in `changes.deleted`: call `documents.delete_by_path(vp)`
   - Returns `Success(ReconcileResult(paths_reconciled=N, ...))`

3. **Stage 2 — `reconcile_orphan_binaries(ctx, result)`** (`pipelines/reconcile.py`)
   - Walk all `attachment/` folders in vault (using `vault/paths.py` helpers and
     `vault_config.root` to find `Projects/*/attachment/` and `Domain/*/attachment/`)
   - For each binary found: check if `.summaries/<stem>.md` exists on disk
   - If no sibling: call `capture_file(binary_path, ctx)` from `pipelines/capture.py`
   - Accumulate `new_captures` count
   - Returns `Success(result.replace(new_captures=N))`

4. **Stage 3 — `reconcile_stale_binaries(ctx, result)`** (`pipelines/reconcile.py`)
   - For each binary that HAS a sibling (`.summaries/<stem>.md` exists):
   - Compare `binary.stat().st_mtime > sibling.stat().st_mtime`
   - If stale: call `capture_file(binary_path, ctx)` — this re-summarizes and re-writes sibling
     (`attachment_path` preserved — TD-AS-1 fix from Phase 1 guarantees this)
   - Accumulate `restale_count`
   - Known limitation: mtime-based detection is imperfect (macOS may touch mtime on attribute
     changes). Pragmatic for CLI/scheduled use.
   - Returns `Success(result.replace(restale_count=N))`

5. **Stage 4 — `reconcile_orphan_siblings(ctx, result)`** (`pipelines/reconcile.py`)
   - Walk all `.summaries/` folders in vault
   - For each sibling `.md`: read `metadata.attachment_path` from frontmatter
   - If `attachment_path` is None or the path does not exist on disk:
     - If `metadata.updated_by_human`: skip + log warning (human-edited summary, do not delete)
     - Otherwise: `documents.delete_by_path(sibling_vault_path)` then `sibling_abs.unlink()`
     - `core.audit.write(outcome="ORPHAN_SIBLING_CLEANED", ...)`
   - Accumulate `orphans_cleaned`
   - Returns `Success(result.replace(orphans_cleaned=N))`

6. **Wire pipeline** (`pipelines/reconcile.py`)
   ```python
   async def reconcile(config: Config) -> Result[ReconcileResult]:
       cid = new_correlation_id()
       ctx = PipelineContext(config=config, correlation_id=cid)
       return await run_pipeline(ctx, reconcile_paths, reconcile_orphan_binaries,
                                 reconcile_stale_binaries, reconcile_orphan_siblings)
   ```

7. **Add `kms reconcile` CLI command** (`cli/main.py`)
   - `@click.command() def reconcile(): asyncio.run(_async_reconcile())`
   - Prints per-stage counts on completion
   - Accepts optional `--vault-path` override for testing

**New audit outcomes**:
- `ORPHAN_BINARY_CAPTURED` — reconcile Stage 2 found binary with no summary; captured
- `BINARY_STALE_RESUMMARIZED` — reconcile Stage 3 found stale binary; re-summarized
- `ORPHAN_SIBLING_CLEANED` — reconcile Stage 4 found sibling with missing binary; removed

**Notes**:
- **TD-026 retirement**: Phase 4 Stage 4 directly addresses TD-026 ("Orphan reconciliation —
  binary in `attachment/` with no sibling `.md`"). Mark TD-026 resolved when Phase 4 ships.
- **Stage 2 attachment/ discovery**: use `vault_config.root.rglob(vault_config.attachment_dir)`
  to find all `attachment/` folders recursively (covers Projects/, Domain/, and Archive/ subtrees)
  without needing to enumerate project/domain names explicitly.
- **Watcher `db_path` for audit**: if `VaultWatcher` constructor does not already take `db_path`,
  add it alongside the `vault_config` change in Phase 1. Audit writes in Phase 3 require it.

**Files to create**:
- `pipelines/reconcile.py` — new file (4 stages + ReconcileResult)

**Files to modify**:
- `cli/main.py` — add `kms reconcile` command

**Test files to create**:
- `tests/test_pipelines/test_reconcile.py` — new file

**Test criteria**:
- [ ] Stage 1: directory move (Projects/A/ → Domain/D/Archive/A/) → all vault_paths updated
- [ ] Stage 2: binary with no sibling → `capture_file` called; sibling created in `.summaries/`
- [ ] Stage 3: binary mtime newer than sibling mtime → `capture_file` called; sibling updated;
      `attachment_path` NOT wiped (TD-AS-1 fix confirmed working end-to-end)
- [ ] Stage 3: binary mtime older than sibling mtime → no re-capture
- [ ] Stage 4: sibling with `attachment_path` pointing to missing file → `sibling_abs.unlink()` called + DB row removed
- [ ] Stage 4: sibling with `updated_by_human=True` → NOT deleted; warning logged
- [ ] `kms reconcile` CLI prints action counts on success
- [ ] `uv run pytest tests/test_pipelines/test_reconcile.py` passes

**Status**: [x] done

**Completed**: 2026-05-24
**Notes**:
- Created `pipelines/reconcile.py` with 4 async stages + `ReconcileResult` frozen dataclass + `reconcile()` entry point.
- Stage 1 (`reconcile_paths`): calls `scan_vault` + `detect_changes`, applies `documents.rename` for moved and `documents.delete_by_path` for deleted.
- Stage 2 (`reconcile_orphan_binaries`): walks `attachment/` dirs via `rglob(attachment_dir)`, calls `capture_file` for binaries with no `.summaries/` sibling.
- Stage 3 (`reconcile_stale_binaries`): compares `binary.stat().st_mtime > sibling.stat().st_mtime`, re-captures if stale.
- Stage 4 (`reconcile_orphan_siblings`): walks `.summaries/` dirs, reads `attachment_path` from frontmatter, unlinks ghost `.md` files + removes DB rows. Skips `updated_by_human=True` siblings.
- Audit outcomes: `ORPHAN_BINARY_CAPTURED`, `BINARY_STALE_RESUMMARIZED`, `ORPHAN_SIBLING_CLEANED`.
- Added `kms reconcile` CLI command — prints per-stage counts.
- TD-026 (orphan reconciliation) retired — Stage 4 directly addresses it.
- 12 new tests in `tests/test_pipelines/test_reconcile.py` covering all 4 stages + ReconcileResult + end-to-end.
- Full suite: 649 passed, 1 skipped, 1 warning (pre-existing).

---

## Open Questions

| ID | Question | Blocks |
|---|---|---|
| OQ-AS4 | Phase 2 Classify must exclude `Domain/Uncategorized/` from active-note routing targets. `load_valid_domains` returns `"Uncategorized"` as a valid domain if the folder exists. Classify pipeline must add an explicit exclusion. | Phase 2 Classify (not this plan) |
| Q-001 | Edit + move detection gap: if a note or sibling is edited AND moved simultaneously, content_hash matching fails and the old row is deleted (cascade removes corrections). Audit trail loses continuity. Cross-cuts with archive move edge case but pre-exists this plan. | Known open question |

---

## Out of Scope

- **Binary content-update sync via watcher** (TD-C6 / OQ-AS2): `on_modify` remains skipped for non-.md
  files. Re-summarization on binary content change is handled by `kms reconcile` Stage 3 only.
  Real-time binary-modify → re-summarize requires unblocking TD-C6 and adds LLM cost per edit.
  Deferred post-Phase 4.
- **`documents.bulk_rename_prefix`** (TD-AS-4): O(N) per-row renames from `documents.rename` are
  acceptable for CLI-triggered reconcile at this scale. Bulk SQL `REPLACE` optimization deferred.
- **`move_project()` in `vault/writer.py`**: user chose scan_capture reconciliation window for archive
  consistency (OQ-AS3). No atomic project-tree move function needed in this plan.
- **CLAUDE.md co-author section-merge** (TD-015): separate deferred feature, not touched here.
- **Phase 8 scheduler wiring**: `kms reconcile` is designed to be scheduler-friendly (returns
  `ReconcileResult` counts readable by briefing pipeline), but the scheduler itself is Phase 8+.
- **`Domain/Uncategorized/` enforcement**: convention only; code enforcement is Phase 2 Classify scope.
