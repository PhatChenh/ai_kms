# Plan: Phase 1.5 Pay-Debt
_Last updated: 2026-06-02_
_Status: [~] in progress_

Source spec: `docs/design/phase1.5_redesign/behavior_adjustment.md`

**Features (in build order):**
- Phase 1 — FILE_LOST guard (handle missing file)
- Phase 2 — `_location_context` helper + `apply_location_tags` capture stage
- Phase 3 — `reconcile_stale_tags` (Stage 5)
- Phase 4 — Folder handling (`capture_folder` + watcher + `batches` table)
- Phase 5 — Handlers extension (8 new handlers)
- Phase 6 — Idempotent capture (content-hash early exit for `.md` + binary files)
- Phase 7 — `reconcile_stale_batch_refs` (Stage 6 — TD-036; requires Phase 4)

**Out of scope:**
- Rename logic rework (deferred — no spec yet)
- TD-034 project-to-domain registry (deferred)
- TD-035 mismatch alert for human-locked notes (deferred)

---

## Architecture

### Diagram 1 — Component Map

```
# Phase 1.5 Pay-Debt — Component Map
# Scope: Where each feature attaches to existing code.
#        Excludes Phase 2+ (Classify, MCP, Search, Briefing).

┌────────────────────────────────────────────────────────────────────┐
│  vault/watcher.py  (exists)                                        │
│  Debounces FS events; dispatches to user callbacks                 │
│                                                                    │
│  Changes [Feature F]:  handle DirCreatedEvent,                     │
│    pending-folder registry + per-folder debounce timer,            │
│    ThreadPoolExecutor bridge (max_workers from config)             │
└──────────────────────────┬─────────────────────────────────────────┘
           file event      │           folder-stable event
     ┌─────────────────────┴──────────────────────────────┐
     │                                                     │
     ▼                                                     ▼
┌─────────────────────────────────┐    ┌────────────────────────────────────┐
│  capture_file()  (exists)       │    │  capture_folder()  [NEW — Ph4]     │
│  Single-file capture entry      │    │  Folder-drop entry point           │
│                                 │    │                                    │
│  + FILE_LOST guard @ entry [Ph1]│◄───│  Stage 1 (inbox): LLM classify     │
│  + FILE_LOST guard @ store [Ph1]│    │  Stage 1 (project/domain): skip    │
│                                 │    │  Stage 2: calls capture_file()     │
│  Pipeline (5 → 6 stages):       │    │            per file in batch       │
│    extract         (exists)     │    └──────────────────┬─────────────────┘
│    enrich_urls     (exists)     │                       │ writes
│    summarize       (exists)     │                       ▼
│    metadata        (exists)     │    ┌────────────────────────────────────┐
│    apply_loc_tags  [NEW — Ph2]  │    │  storage/migrations/  (exists)     │
│    store           (exists)     │    │  Versioned .sql deltas             │
└────────────┬────────────────────┘    │                                    │
             │ reads path              │  + batches table       [Ph4 new]   │
             ▼                         │  + documents.batch_id  [Ph4 new]   │
┌──────────────────────────────┐       └────────────────────────────────────┘
│  vault/paths.py  (exists)    │
│  Path helpers for vault      │◄──────────── also read by reconcile [Ph3]
│                              │
│  + _location_context()  [Ph2]│
│  load_valid_domains() exists │
└──────────────────────────────┘
             │ used by apply_location_tags [Ph2] and reconcile_stale_tags [Ph3]
             ▼
┌────────────────────────────────────────────────────────────────────┐
│  pipelines/reconcile.py  (exists)                                  │
│  4-stage reconcile command                                         │
│                                                                    │
│  reconcile() [Ph3]: hoist scan_vault() once; pass entries to S1+S5│
│  Stage 1 (reconcile_paths): signature gains `entries` param [Ph3] │
│  Stage 5 (reconcile_stale_tags): NEW [Ph3] — per-note, removes    │
│    stale domain/<X> tags; sets project: for project-path notes    │
│  ReconcileResult: + tags_updated: int = 0  [Ph3 new]              │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│  handlers/  (exists)                                               │
│  HandlerRegistry — first-match-wins, self-registration             │
│                                                                    │
│  Existing: MarkdownHandler · PdfHandler · DocxHandler              │
│  New [Ph5]: XlsxHandler · CsvHandler · PptxHandler                │
│             HtmlHandler · EmlHandler · MsgHandler                  │
│             PngHandler (stub) · JpgHandler (stub)                  │
│  New deps: openpyxl · python-pptx · extract-msg                   │
└────────────────────────────────────────────────────────────────────┘

Legend:
  (exists)    component already in codebase — unchanged unless noted
  [NEW — PhN] new file or class, phase number in brackets
  [PhN new]   modification to existing file
  [PhN]       phase cross-reference
```

### Diagram 2 — `capture_folder` Data Flow

```
# capture_folder — Data Flow
# Scope: Full lifecycle from FS event to batch completion.
#        Does NOT cover: scan_capture fallback, PENDING_REVIEW review UI.

   (User drops folder anywhere in vault)
                    │
                    ▼
   ┌────────────────────────────────────────┐
   │  _VaultEventHandler.on_created         │
   │  Receives DirCreatedEvent              │
   │  Registers folder in pending registry  │
   │  Starts debounce timer (5 s default)   │
   │                                        │
   │  Each FileCreatedEvent inside folder:  │
   │    reset folder timer                  │
   │    suppress normal _on_create callback │
   └────────────────────┬───────────────────┘
                        │  timer fires (no new files for 5 s)
                        ▼
   ┌────────────────────────────────────────┐
   │  ThreadPoolExecutor.submit(            │
   │    threading.Thread(                   │
   │      asyncio.run(capture_folder(...)))) │
   │  Capped by capture.folder_max_workers  │
   └────────────────────┬───────────────────┘
                        │
                        ▼
   ┌────────────────────────────────────────┐
   │  capture_folder(folder_path, ctx)      │
   │  _location_context(folder_path) →      │
   │  detect: inbox? project? domain?       │
   └─────────────┬──────────────────────────┘
                 │
      ┌──────────┴──────────────┐
      │                         │
      ▼                         ▼
 ┌─────────────────┐    ┌───────────────────────────────┐
 │  inbox/ drop    │    │  Projects/<A>/  or            │
 └────────┬────────┘    │  Domain/<D>/  drop            │
          │             │  (location already known)     │
          ▼             └──────────────┬────────────────┘
 ┌─────────────────┐                   │
 │  LLM classify   │                   │ write batches row
 │  folder name +  │                   │ (confidence=1.0,
 │  file manifest  │                   │  status=ROUTING)
 │  (classify_     │                   │
 │   folder.yaml)  │                   │
 └────────┬────────┘                   │
          │ confidence score           │
          ▼                            │
 ┌─────────────────┐                   │
 │  Confidence     │                   │
 │  Gate           │                   │
 └──┬──────┬────┬──┘                   │
    │      │    │                      │
   HIGH   MED  LOW                     │
   auto  rev  CLUELESS                 │
    │      │    │                      │
    │      │    ▼                      │
    │      │  per-file CLUELESS        │
    │      │  markers written;         │
    │      │  no folder move;          │
    │      │  batches row → CLUELESS   │
    │      │                           │
    │      ▼                           │
    │  batches row → PENDING_REVIEW;   │
    │  no folder move (human decides)  │
    │                                  │
    ▼                                  │
 move folder to                        │
 Domain/<D>/ or Projects/<A>/;         │
 write batches row (status=ROUTING)    │
          │                            │
          └──────────────┬─────────────┘
                         │
                         ▼
   ┌─────────────────────────────────────────────┐
   │  Stage 2: per-file loop                     │
   │  Walk folder recursively on disk            │
   │  For each file:                             │
   │    capture_file(path, ctx_with_batch_id)    │
   │  One file Failure → continue, mark PARTIAL  │
   │  One file FILE_LOST → continue, mark PARTIAL│
   └───────────────────────┬─────────────────────┘
                           │
                           ▼
   ┌─────────────────────────────────────────────┐
   │  Update batches row                         │
   │  status = COMPLETE | PARTIAL | CLUELESS     │
   └─────────────────────────────────────────────┘

Legend:
  ──────►   control/data flow
  - - - ►   conditional/optional flow
```

### Diagram 3 — `reconcile_stale_tags` Stage 5: Per-Note Logic

```
# reconcile_stale_tags — Per-Note Decision Tree
# Scope: Logic inside Stage 5, applied to every note entry from scan_vault().

  reconcile() entry point
        │
        ▼
  scan_vault() called ONCE
  returns entries (all .md files including .summaries/)
        │
        ▼
  load_valid_domains() called ONCE
  returns set of valid domain folder names
        │
        ▼
  for each entry in entries:
        │
        ▼
  ┌─────────────────────────────┐
  │  read metadata.tags         │
  │  find all domain/<X> entries│
  └──────────────┬──────────────┘
                 │
                 ▼
  ┌─────────────────────────────┐
  │  Remove any domain/<X>      │
  │  where Domain/<X>/ folder   │
  │  no longer exists           │
  │  (check against             │
  │   valid_domains set)        │
  └──────────────┬──────────────┘
                 │
                 ▼
  ┌─────────────────────────────┐
  │  _location_context(path)    │
  │  Where is this note now?    │
  └────────┬────────────────────┘
           │
  ┌────────┼──────────────────────────┐
  │        │                          │
  ▼        ▼                          ▼
Domain/   Projects/<A>/          inbox/ or other
<D>/…        …                        …
  │          │                         │
  ▼          ▼                         │
Is           Set project: <A>          No tag changes
domain/<D>   (overwrite                (project: left
in tags?     existing value)           alone regardless)
  │                                    │
  YES → no-op                          │
  NO  → add domain/<D>                 │
        to tags list                   │
  │          │                         │
  └────┬─────┘                         │
       │                               │
       ▼                               ▼
  Is note dirty?                  Is note dirty?
  (tags or project changed)       (was stale domain
       │                           tag removed?)
      YES                               │
       │                              YES
       ▼                               │
  read_note(path)                      ▼
  construct new NoteMetadata       read_note(path)
  copy ALL existing fields         construct new NoteMetadata
  replace only tags + project      copy ALL existing fields
  write_note(actor="ai")           replace only tags
       │                           write_note(actor="ai")
       └──────────────┬────────────┘
                      │
                      ▼
               tags_updated += 1
               (on ReconcileResult)
```

### Diagram 4 — FILE_LOST guard: Two guard positions

```
# FILE_LOST Guard — Two Guard Clauses in capture_file()
# Scope: Shows where each guard fires and what it catches.

  capture_file(path, ctx)
        │
        ▼
  ┌──────────────────────────────────────────┐
  │  GUARD 1 — entry-time check  [NEW Ph1]   │
  │                                          │
  │  try: path.stat()                        │
  │  except FileNotFoundError:               │
  │    audit.write(FILE_LOST, stage=entry)   │
  │    return Failure(recoverable=True)      │
  └──────────────────┬───────────────────────┘
                     │ file exists — continue
                     ▼
  [existing cooldown + CLUELESS guards]
                     │
                     ▼
  run_pipeline([extract, enrich_urls, summarize, metadata,
                apply_location_tags])
                     │  LLM calls happen here (seconds to minutes)
                     │  FILE MAY DISAPPEAR DURING THIS TIME
                     ▼
  store(result, ctx) dispatcher
        │
        ▼
  ┌──────────────────────────────────────────┐
  │  GUARD 2 — store-time check  [NEW Ph1]   │
  │                                          │
  │  if not path.exists():                   │
  │    audit.write(FILE_LOST, stage=store)   │
  │    return Failure(recoverable=False)     │
  └──────────────────┬───────────────────────┘
                     │ file still exists — continue
                     ▼
  _store_md(result) or _store_nonmd(result)
  vault write proceeds normally

Notes:
  Guard 1 catches: file deleted BEFORE pipeline starts.
                   recoverable=True (scan_capture skips + continues).
                   Fixes pre-existing path.stat() crash at line ~694.
  Guard 2 catches: file deleted DURING LLM calls.
                   recoverable=False (anomalous race — log as warning).
                   Prevents orphaned sibling .md for binary files.
  Only one guard fires per run — never both.
  Both write FILE_LOST audit best-effort; Failure returned regardless
  of whether audit.write() itself fails (see _audit_rename_gate pattern).
```

---

---

## Approach

Phases ordered by dependency depth: standalone guards first (unblocks correctness), then shared helper + capture stage, then reconcile stage that reuses the same helper, then large folder feature that builds on everything, handlers last (standalone, has own research doc).

Each phase is independently committable and testable before the next starts.

---

## Phases

### Phase 1 — FILE_LOST Guard
**Goal**: Prevent uncaught crash and orphaned sibling when file disappears during capture.

**Design**: See Diagram 4 above — two guard positions, one per failure window.

**Steps**:

1. Add `_audit_file_lost(path, stage, ctx)` helper in `src/pipelines/capture.py` following the `_audit_rename_gate` pattern at line 274. Best-effort: match on `audit.write(...)`, log warning on Failure, never raise.

2. Wrap the `path.stat().st_mtime` call at `capture_file` line 694 in a `try/except FileNotFoundError`. On catch: call `_audit_file_lost(path, "entry", ctx)`, return `Failure(error="file not found at capture entry", recoverable=True, context={"path": str(path)})`.

3. Add store-time guard at top of `store()` at line 328, before any dispatch to `_store_md`/`_store_nonmd`. Check `if not mr.raw.source_path.exists()`. On miss: call `_audit_file_lost(mr.raw.source_path, "store", ctx)`, return `Failure(error="file disappeared during pipeline", recoverable=False, context={"path": str(mr.raw.source_path)})`.

4. Write tests in `tests/test_pipelines/test_capture.py`:
   - Mock `path.stat()` to raise `FileNotFoundError` → entry guard fires → `Failure(recoverable=True)` returned, no pipeline run, `FILE_LOST` audit written
   - Mock `path.exists()` returning `False` inside `store()` → store guard fires → `Failure(recoverable=False)`, no vault write, no DB upsert
   - Happy path unchanged: existing tests still pass

**Files to modify**:
- `src/pipelines/capture.py` — add `_audit_file_lost()` helper; wrap `path.stat()` at line 694; guard at `store()` line 328

**Test criteria**:
- [ ] `path.stat()` raises `FileNotFoundError` → `Failure(recoverable=True)`, audit entry with `outcome="FILE_LOST"`, `stage="entry"`
- [ ] File gone during pipeline → `Failure(recoverable=False)`, audit entry `stage="store"`, no `.md` written to vault
- [ ] No partial `documents` row inserted when either guard fires
- [x] `path.stat()` raises `FileNotFoundError` → `Failure(recoverable=True)`, audit entry with `outcome="FILE_LOST"`, `stage="entry"`
- [x] File gone during pipeline → `Failure(recoverable=False)`, audit entry `stage="store"`, no `.md` written to vault
- [x] No partial `documents` row inserted when either guard fires
- [x] `_audit_file_lost` itself fails silently — `Failure` still returned from `capture_file`
- [x] All 650+ existing tests still pass

**Completed**: 2026-06-02
**Notes**: Guard 2 placed in `_store_md` and start of LOCATED block in `_store_nonmd` (not at top of `store()`). Broad guard at `store()` top broke CLUELESS inbox case (TD-026 intentional missing binary). 4 new tests. 642 pass.

**Status**: [x] done

---

### Phase 2 — `_location_context` + `apply_location_tags`
**Goal**: Capture stage derives domain/project tags from file location and sets them on every captured note.

**Design**: New helper in `vault/paths.py`; new Stage 5 in capture pipeline inserted between `metadata` and `store`.

**Steps**:

1. Add `_location_context(path: Path, vault_cfg: VaultConfig) -> tuple[str | None, str | None]` to `src/vault/paths.py`.
   - Returns `("domain", "<D>")` if path is under `vault_cfg.root / vault_cfg.domain_dir / "<D>"/`
   - Returns `("project", "<A>")` if path is under `vault_cfg.root / vault_cfg.projects_dir / "<A>"/`
   - Returns `("inbox", None)` if path is under `vault_cfg.inbox_path`
   - Returns `(None, None)` otherwise
   - Uses `vault_cfg.domain_dir`, `vault_cfg.projects_dir` — no hardcoded strings. Walk path components; compare against config values.

2. Add `ai_project: str | None = None` field to `MetadataResult` dataclass at line 53 in `src/pipelines/capture.py`. Default `None` keeps all existing callers unbroken.

3. Add `apply_location_tags(mr: MetadataResult, ctx: PipelineContext) -> Result[MetadataResult]` stage function in `src/pipelines/capture.py`:
   - Call `_location_context(mr.raw.source_path, ctx.config.vault)`
   - `("domain", d)`:
     - If `d` not in `ctx.taxonomy.valid_domains` → log warning, skip tag (invalid domain folder)
     - If `f"domain/{d}"` already in `mr.ai_tags` → no-op
     - Else append `f"domain/{d}"` to `mr.ai_tags`
     - Return `Success(replace(mr, ai_tags=mr.ai_tags))` — copy tags list, don't mutate
   - `("project", a)`: Return `Success(replace(mr, ai_project=a))`
   - `("inbox", None)` or `(None, None)`: Return `Success(mr)` unchanged

4. Update `run_pipeline` call at line 716-720 — insert `apply_location_tags` between `metadata` and `store`:
   ```python
   [extract, enrich_urls, summarize, metadata, apply_location_tags, store]
   ```

5. Update `_store_md` and `_store_nonmd` to consume `mr.ai_project` when constructing `NoteMetadata`. Read `project:` from existing note first (via `read_note`), then prefer `mr.ai_project` if set. This satisfies C-03 (pipeline owns merge).

6. Write tests in `tests/test_vault/test_paths.py` for `_location_context`:
   - Path under `Domain/Engineering/foo.md` → `("domain", "Engineering")`
   - Path under `Projects/Alpha/bar.md` → `("project", "Alpha")`
   - Path under `inbox/baz.md` → `("inbox", None)`
   - Path elsewhere → `(None, None)`

7. Write tests in `tests/test_pipelines/test_capture.py` for `apply_location_tags`:
   - Domain file: tag added to `ai_tags`, existing tags preserved
   - Domain file already tagged: no duplicate added
   - Invalid domain (folder doesn't exist): tag NOT added, warning logged
   - Project file: `ai_project` set, no domain tag added
   - Inbox file: no changes
   - `updated_by_human=True` on note: `write_note(actor="ai")` blocks automatically (no special-casing needed)

**Files to modify**:
- `src/vault/paths.py` — add `_location_context()`
- `src/pipelines/capture.py` — add `ai_project` to `MetadataResult`; add `apply_location_tags` stage; update `run_pipeline` call; update `_store_md`/`_store_nonmd` to use `mr.ai_project`

**Test criteria**:
- [ ] `_location_context` returns correct tuple for all 4 location types
- [ ] Domain file capture → note on disk has `domain/<D>` in tags
- [ ] Domain file already has tag → idempotent, no duplicate
- [ ] Invalid domain name → tag skipped, warning logged, capture succeeds
- [ ] Project file capture → note on disk has `project: <A>` in frontmatter
- [ ] Inbox file → no tag changes
- [ ] `updated_by_human: true` note → skipped by `write_note` automatically
- [ ] All existing capture tests still pass (no regression from `ai_project=None` default)

**Status**: [ ] pending

---

### Phase 3 — `reconcile_stale_tags` (Stage 5)
**Goal**: Every `kms reconcile` run removes stale `domain/<X>` tags and fixes stale `project:` fields vault-wide.

**Design**: See Diagram 3 above — per-note decision tree, `scan_vault()` hoisted to entry, `run_pipeline()` replaced with explicit await-chain.

**Steps**:

1. Add `tags_updated: int = 0` to `ReconcileResult` dataclass at line 33 in `src/pipelines/reconcile.py`.

2. Hoist `scan_vault()` to `reconcile()` entry. Currently `reconcile_paths` calls `scan_vault()` internally (line 58-63). Move that call to `reconcile()`. Store result in `entries`. Pass `entries` to Stage 1 and Stage 5.

3. Change `reconcile_paths` signature from `(result: ReconcileResult, ctx: PipelineContext)` to `(result: ReconcileResult, ctx: PipelineContext, entries)`. Remove the internal `scan_vault()` call. Use the passed `entries` directly.

4. Replace `run_pipeline(...)` in `reconcile()` with an explicit await-chain:
   ```python
   async def reconcile(ctx: PipelineContext) -> Result[ReconcileResult]:
       from vault.indexer import scan_vault
       match scan_vault(ctx.config.vault.root):
           case Failure() as f:
               return f
           case Success(entries):
               pass

       result = ReconcileResult()
       match await reconcile_paths(result, ctx, entries):
           case Failure() as f: return f
           case Success(value=r): result = r
       match await reconcile_orphan_binaries(result, ctx):
           case Failure() as f: return f
           case Success(value=r): result = r
       match await reconcile_stale_binaries(result, ctx):
           case Failure() as f: return f
           case Success(value=r): result = r
       match await reconcile_orphan_siblings(result, ctx):
           case Failure() as f: return f
           case Success(value=r): result = r
       match await reconcile_stale_tags(result, ctx, entries):
           case Failure() as f: return f
           case Success(value=r): result = r
       return Success(result)
   ```

5. Add `reconcile_stale_tags(result: ReconcileResult, ctx: PipelineContext, entries) -> Result[ReconcileResult]` function in `src/pipelines/reconcile.py`:
   - Import `_location_context` from `vault.paths`
   - Call `load_valid_domains(ctx.config.vault)` **once** before the loop
   - For each entry (only `.md` files, skip `.summaries/` siblings if desired or process them — they get `project:` set per design):
     - Read metadata from entry (use `entry.metadata` if available from indexer, else `read_note`)
     - Compute `dirty = False`
     - Remove any `domain/<X>` from `tags` where `X` not in `valid_domains`; if any removed → `dirty = True`
     - Call `_location_context(entry.path, ctx.config.vault)`
     - On `("domain", d)`: if `f"domain/{d}"` absent from remaining tags → add it; `dirty = True`
     - On `("project", a)`: if `metadata.project != a` → set `project = a`; `dirty = True`
     - On `("inbox", None)` or `(None, None)`: `project:` left alone
     - If `dirty`: `read_note(entry.path)` → copy ALL existing fields → replace only `tags` and `project` → `write_note(actor="ai")`; `write_note` auto-skips `updated_by_human=True` notes
     - On write success: increment local `tags_updated` counter
   - Return `Success(result.replace(tags_updated=result.tags_updated + tags_updated))`

6. Update Stage 1 tests (2 tests in `tests/test_pipelines/test_reconcile.py`): change direct calls from `await reconcile_paths(initial, ctx)` to `await reconcile_paths(initial, ctx, entries)`. Pass a fake `entries` list.

7. Write Stage 5 tests in `tests/test_pipelines/test_reconcile.py`:
   - Note with stale `domain/OldDomain` tag (folder deleted) → tag removed
   - Note in `Domain/Engineering/` missing `domain/engineering` tag → tag added
   - Note in `Projects/Alpha/` → `project: Alpha` set
   - Note in inbox → `project:` unchanged
   - Note with `updated_by_human: true` → write skipped, no tag change
   - `load_valid_domains` called once, not per-note (verify via mock call count)

**Files to modify**:
- `src/pipelines/reconcile.py` — `ReconcileResult` new field; `reconcile_paths` signature change; `reconcile()` explicit await-chain; new `reconcile_stale_tags` stage
- `tests/test_pipelines/test_reconcile.py` — fix 2 Stage 1 tests; add Stage 5 tests

**Test criteria**:
- [ ] Stale `domain/<X>` tag removed when `Domain/<X>/` folder deleted
- [ ] Missing location tag added on next reconcile run
- [ ] `project:` overwritten with correct value for notes under `Projects/<A>/`
- [ ] Notes outside `Projects/` have `project:` left alone
- [ ] `updated_by_human: true` → write skipped automatically via `write_note(actor="ai")`
- [ ] `load_valid_domains` called exactly once per reconcile run (not per note)
- [ ] `ReconcileResult.tags_updated` count correct
- [ ] C-03: `read_note` called before every `write_note`; no existing field wiped
- [ ] 2 Stage 1 tests updated and passing
- [ ] Full reconcile end-to-end: 5-stage pipeline completes without regression

**Status**: [ ] pending

---

### Phase 4 — Folder Handling (`capture_folder`)
**Goal**: Dropping a folder into the vault routes the whole folder as a unit, preserving grouping.

**Design**: See Diagrams 1 and 2 above.

**Steps**:

1. **Migration** — create `src/storage/migrations/002_batches.sql`:
   ```sql
   CREATE TABLE IF NOT EXISTS batches (
       batch_id    INTEGER PRIMARY KEY AUTOINCREMENT,
       folder_name TEXT NOT NULL,
       destination_type TEXT,
       destination_name TEXT,
       confidence  REAL NOT NULL DEFAULT 0.0,
       status      TEXT NOT NULL DEFAULT 'ROUTING',
       file_count  INTEGER NOT NULL DEFAULT 0,
       created_at  TEXT NOT NULL
   );

   ALTER TABLE documents ADD COLUMN batch_id INTEGER REFERENCES batches(batch_id);
   ```

2. **`PipelineContext`** — add `batch_id: int | None = field(default=None)` to `PipelineContext` dataclass in `src/core/pipeline.py`. All existing callers pass no `batch_id` — default `None` is backward-compatible.

3. **`storage/documents.py`** — add optional `batch_id: int | None = None` kwarg to `upsert()`. When set, write it to the `documents.batch_id` column.

4. **New `storage/batches.py`** module with:
   - `insert(folder_name, destination_type, destination_name, confidence, status, file_count, db_path) -> Result[int]` — returns `batch_id`
   - `update_status(batch_id, status, db_path) -> Result[int]`

5. **Prompt** — create `src/prompts/classify_folder.yaml` with system + user templates. Variables: `folder_name`, `file_manifest` (list of filenames). Output: JSON with `target_type` (`domain`|`project`), `target_name`, `confidence`. Follow existing YAML prompt structure from `prompts/summarize.yaml`.

6. **Config** — add to `config/config.yaml` under `capture:`:
   ```yaml
   folder_cooldown_seconds: 5.0
   folder_max_workers: 4
   ```
   Add corresponding fields to `CaptureConfig` in `src/core/config.py`.

7. **`capture_folder()`** — add to `src/pipelines/capture.py`:
   - Signature: `async def capture_folder(folder_path: Path, context: PipelineContext | None = None) -> Result[list[WriteOutcome]]`
   - Call `_location_context(folder_path, ctx.config.vault)`
   - **Inbox drop**: render `classify_folder.yaml` with folder name + file manifest → call `get_provider("capture", ctx.config).complete(system, user)` → parse JSON → `ConfidenceGate.from_config(ctx.config)` to route. On `auto`: move folder to destination (`move_note` or `os.rename` via writer helper) → write batches row → run Stage 2. On `review`: write batches row with `PENDING_REVIEW`, return. On CLUELESS: write per-file markers, write batches row `CLUELESS`, return.
   - **Project/Domain drop**: skip LLM → write batches row (`confidence=1.0`, `status=ROUTING`) → run Stage 2 directly.
   - Stage 2: walk folder recursively → for each file, call `capture_file(file, context_with_batch_id)`. Collect results. Count failures. If any failure: mark batch `PARTIAL`, else `COMPLETE`. Return `Success(outcomes)`.
   - Write `FOLDER_CLASSIFIED` audit entry for inbox drops (both auto and CLUELESS).

8. **`vault/watcher.py`** — extend `_VaultEventHandler`:
   - Add `_pending_folders: dict[str, threading.Timer]` to `__init__`
   - In `on_created`: check `isinstance(event, DirCreatedEvent)` → compute debounce key `f"dir:{event.src_path}"` → cancel existing timer if any → start new `threading.Timer(self._folder_cooldown, self._on_folder_stable, args=[Path(event.src_path)])`. Add folder to pending set.
   - In `on_created` for `FileCreatedEvent`: if `event.src_path`'s parent has a pending folder timer → reset that timer → skip normal `_on_create` callback.
   - Add `_on_folder_stable(folder_path: Path)` method: remove from pending set → submit `asyncio.run(capture_folder(folder_path))` to `self._folder_executor`.
   - On `VaultWatcher.__init__`: create `self._folder_executor = ThreadPoolExecutor(max_workers=config.capture.folder_max_workers)`. Shut it down in `stop()`.
   - Add optional `on_folder_create: Callable[[Path], None] | None = None` param to `VaultWatcher.__init__` for tests.

9. Write tests:
   - `tests/test_storage/test_batches.py`: insert + update_status round-trip
   - `tests/test_pipelines/test_capture_folder.py`:
     - Inbox drop, auto confidence → folder moved, batches row COMPLETE, all files captured
     - Inbox drop, CLUELESS → folder not moved, batches row CLUELESS, per-file markers written
     - Project drop → Stage 1 skipped, batches row written with confidence=1.0
     - One file fails in Stage 2 → batch PARTIAL, other files captured
     - Empty folder → no batches row, no pipeline run
   - `tests/test_vault/test_watcher.py`:
     - `DirCreatedEvent` → pending registry populated, timer started
     - `FileCreatedEvent` inside pending folder → timer reset, `_on_create` suppressed
     - Timer fires → `on_folder_create` callback called with folder path

**Files to modify**:
- `src/storage/migrations/002_batches.sql` — new file
- `src/storage/batches.py` — new file
- `src/storage/documents.py` — add `batch_id` kwarg to `upsert()`
- `src/core/pipeline.py` — add `batch_id` field to `PipelineContext`
- `src/core/config.py` — add `folder_cooldown_seconds`, `folder_max_workers` to `CaptureConfig`
- `src/prompts/classify_folder.yaml` — new file
- `src/pipelines/capture.py` — add `capture_folder()`
- `src/vault/watcher.py` — pending-folder registry, `ThreadPoolExecutor`, `DirCreatedEvent` handling
- `config/config.yaml` — add new fields under `capture:`

**Test criteria**:
- [ ] `batches` table created by migration; FK pragma enforced
- [ ] Inbox drop → folder moved to correct destination, `batches.status=COMPLETE`
- [ ] Inbox drop CLUELESS → folder not moved, per-file markers written, `batches.status=CLUELESS`
- [ ] Project/Domain drop → LLM NOT called, `batches.confidence=1.0`
- [ ] Partial failure → `batches.status=PARTIAL`, successful files captured
- [ ] Empty folder → discarded, no `batches` row
- [ ] `documents.batch_id` set for all files in batch
- [ ] `updated_by_human: true` file in batch → skipped by `write_note`, batch continues
- [ ] `DirCreatedEvent` in watcher → pending registry entry created
- [ ] `FileCreatedEvent` inside pending folder → timer reset, `_on_create` suppressed
- [ ] `ThreadPoolExecutor` caps concurrent folder pipelines (mock `max_workers=1`, drop 2 folders, second queues)
- [ ] C-10: `asyncio.run()` only called from worker thread, never from watchdog observer thread

**Status**: [ ] pending

---

### Phase 5 — Handlers Extension
**Goal**: Support 8 additional file types: `.xlsx`, `.csv`, `.pptx`, `.html`, `.eml`, `.msg`, `.png` (stub), `.jpg` (stub).

**Reference**: Full implementation spec and test strategy in `docs/research/phase1.5_redesign/handlers_extended.md`.
Cancelled plan skeleton at `docs/plans/phase1.5_redesign/handlers_extended.md` (status `cancel`) — ignore it; research doc is authoritative.

**Summary of changes**:
- New files: `src/handlers/xlsx_handler.py`, `csv_handler.py`, `pptx_handler.py`, `html_handler.py`, `eml_handler.py`, `msg_handler.py`, `image_handler.py`
- Update `src/handlers/__init__.py` registration order
- Add deps to `pyproject.toml`: `openpyxl>=3.1`, `python-pptx>=1.0`, `extract-msg>=0.28`
- Tests: `tests/test_handlers/` — one test file per handler

**Status**: [ ] pending

---

---

### Phase 6 — Idempotent Capture
**Goal**: Prevent re-running the LLM pipeline on unchanged files. Skip silently with a `SKIPPED` audit entry; re-run only when file content has changed.

**Design**:

```
# Phase 6 — Idempotent Capture: Entry Hash Guards
# Scope: Hash checks inserted at capture_file() entry, before run_pipeline().
#        Left = .md path. Right = binary path.
#        Does NOT cover pipeline stage internals.

            capture_file(path, ctx)  (exists)
                        │
                        ▼
            ┌───────────────────────────┐
            │  FILE_LOST guard  (Ph 1)  │  FileNotFoundError → Failure
            └────────────┬──────────────┘
                         │ file exists
                         ▼
               ┌──────────────────┐
               │ path.suffix      │
               │  == ".md"?       │
               └─────┬────────┬───┘
                    YES       NO
                     │         │
       ┌─────────────┘         └──────────────────────┐
       ▼                                              ▼
┌──────────────────────────┐     ┌──────────────────────────────────┐
│  MD HASH CHECK  [NEW]    │     │  BINARY HASH CHECK  [NEW]        │
│                          │     │                                  │
│  sha256(path.read_       │     │  sibling = parent /              │
│    bytes())              │     │    .summaries / {path.name}.md   │
│  documents.get_by_       │     │                                  │
│    path(vault_path)      │     │  no sibling → first capture,     │
│  compare content_hash    │     │    fall through                  │
└────┬──────────────┬───────┘     │  sibling exists →               │
   MATCH         DIFFER /         │    read source_hash frontmatter  │
     │           no row           │    sha256(binary bytes)          │
     │              │             └────────────┬────────────┬────────┘
     ▼              ▼                        MATCH       DIFFER /
  write SKIPPED  fall through                │           no sibling
  audit entry    to pipeline              write SKIPPED  fall through
  return Success                          audit entry    to pipeline
                                          return Success

                    ↓ DIFFER / no-row paths ↓
         run_pipeline([extract → enrich_urls → summarize
                     → metadata → apply_location_tags → store])
                              │
                              │ non-md path only
                              ▼
             ┌─────────────────────────────────────────┐
             │  _store_nonmd  [MODIFIED]                │
             │                                         │
             │  source_hash =                          │
             │    sha256(binary.read_bytes())          │
             │  written into sibling NoteMetadata      │
             └─────────────────────────────────────────┘

NoteMetadata (vault/frontmatter.py)  [one field added]
┌────────────────────────────────────────────────────────┐
│  ...all existing fields unchanged...    (exists)       │
│  source_hash: str | None = None         [NEW]          │
│    SHA256 of binary bytes at last capture.             │
│    Only set on type=attachment-summary notes.          │
│  "source_hash" also added to _KNOWN_KEYS.              │
└────────────────────────────────────────────────────────┘
```

**Key facts verified in code:**
- `documents.DocumentRow.content_hash: str | None` exists at `storage/documents.py:38` — no migration needed for `.md` path.
- `NoteMetadata` defined in `vault/frontmatter.py` (not `schema.py` as spec draft said). `_KNOWN_KEYS` set at line 39 — `source_hash` must be added there too or frontmatter parser will strip it on round-trip.
- `documents.get_by_path(vault_path, db_path)` already exists — use it for the lookup.
- `vault/writer.py` already computes `sha256` and returns it on `WriteOutcome` — no new hashing infrastructure needed.
- Audit `outcome` is a plain `str` — `"SKIPPED"` is a new string constant, no enum change.

**Steps**:

1. Add `source_hash: str | None = None` to `NoteMetadata` in `src/vault/frontmatter.py`. Add `"source_hash"` to `_KNOWN_KEYS` (same block as `"attachment_path"`). Default `None` keeps all existing callers unbroken.

2. Add MD idempotent check at `capture_file` entry in `src/pipelines/capture.py` — after the existing cooldown guard, before `run_pipeline()`. Exact position: after line that calls `_check_cooldown` (or equivalent), before `run_pipeline(stages, ...)`:
   ```python
   current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
   existing = documents.get_by_path(to_vault_path(path, ctx.config.vault), db_path=ctx.db_path)
   if existing.is_success() and existing.value.content_hash == current_hash:
       _audit_skipped(path, ctx)
       return Success(WriteOutcome(..., outcome="SKIPPED"))
   ```
   Only applies when `path.suffix.lower() == ".md"`.

3. Add binary idempotent check at `capture_file` entry — after the existing CLUELESS-inbox guard, before `run_pipeline()`. Only applies to non-`.md` files:
   ```python
   sibling = path.parent / ctx.config.vault.summaries_subdir / f"{path.name}.md"
   if sibling.exists():
       sibling_note = read_note(sibling)
       if sibling_note.is_success() and sibling_note.value.metadata.source_hash:
           current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
           if sibling_note.value.metadata.source_hash == current_hash:
               _audit_skipped(path, ctx)
               return Success(WriteOutcome(..., outcome="SKIPPED"))
   ```

4. Add `_audit_skipped(path, ctx)` best-effort helper in `src/pipelines/capture.py` following `_audit_rename_gate` pattern at line ~274. Writes `outcome="SKIPPED"` audit entry. Never raises — swallow any `Failure` from `audit.write`.

5. Update `_store_nonmd` in `src/pipelines/capture.py` to compute `source_hash` and inject it into `sibling_meta`:
   ```python
   source_hash = hashlib.sha256(src.read_bytes()).hexdigest()
   # then include source_hash=source_hash in NoteMetadata construction for sibling
   ```
   This runs at capture time so subsequent re-triggers find the hash.

6. Write tests in `tests/test_pipelines/test_capture.py`:
   - `.md` file unchanged (hash matches `content_hash` in DB) → `SKIPPED` audit written, `run_pipeline` NOT called, `Success` returned
   - `.md` file edited (hash differs) → pipeline runs normally
   - `.md` file not in DB (first capture) → pipeline runs normally
   - Binary with matching sibling `source_hash` → `SKIPPED`, pipeline not called
   - Binary with differing sibling `source_hash` → pipeline runs, sibling regenerated
   - Binary with no sibling → first capture, pipeline runs
   - `_audit_skipped` fails silently — `Success` still returned

7. Write a test in `tests/test_vault/test_frontmatter.py`:
   - `source_hash` round-trips through write/parse correctly
   - `source_hash=None` (default) round-trips without writing the key to YAML

**Files to modify**:
- `src/vault/frontmatter.py` — add `source_hash` field + `_KNOWN_KEYS` entry
- `src/pipelines/capture.py` — add `_audit_skipped()` helper; MD hash check at entry; binary hash check at entry; `_store_nonmd` writes `source_hash`

**Test criteria**:
- [ ] `.md` unchanged → `SKIPPED` audit, no LLM call, `Success` returned
- [ ] `.md` edited → pipeline runs, frontmatter overwritten with new AI output
- [ ] `.md` not in DB → pipeline runs (first capture)
- [ ] Binary unchanged (sibling `source_hash` matches) → `SKIPPED`, no LLM call
- [ ] Binary updated (hash differs) → pipeline runs, sibling regenerated with new `source_hash`
- [ ] Binary no sibling → pipeline runs, sibling created with `source_hash`
- [ ] `_audit_skipped` write failure does NOT suppress `Success` return
- [ ] `source_hash` field survives frontmatter round-trip; `None` default not written to YAML
- [ ] Phase 8 invariant: `SKIPPED` outcome not counted as new knowledge (test Phase 8 filter separately, but document the string here)
- [ ] All 650+ existing tests still pass

**Status**: [ ] pending

---

### Phase 7 — `reconcile_stale_batch_refs` 
**Goal**: Null out `batch_id` on `documents` rows where the file has moved away from its original batch destination. Eventual-consistency cleanup for TD-036.

**Prerequisite**: Phase 4 complete (`batches` table + `documents.batch_id` column exist).

**Design**:

```
# Phase 7 — reconcile_stale_batch_refs (Reconcile Stage 6)
# Scope: One SQL JOIN + UPDATE pass per reconcile run.
#        Does NOT clear batches.status — only nulls documents.batch_id.
#        Runs after Stage 5 (reconcile_stale_tags) in the explicit await-chain.

  reconcile() await-chain (Phase 3 existing)
          │
          ▼  (after Stage 5)
  ┌────────────────────────────────────────────────────────┐
  │  reconcile_stale_batch_refs  [NEW Stage 6]             │
  │                                                        │
  │  SELECT d.vault_path,                                  │
  │         b.destination_type, b.destination_name         │
  │  FROM documents d                                      │
  │  JOIN batches b ON d.batch_id = b.batch_id             │
  │  WHERE d.batch_id IS NOT NULL                          │
  └───────────────────────────┬────────────────────────────┘
                              │ for each row
                              ▼
              ┌───────────────────────────────┐
              │  expected_prefix:             │
              │  "Projects/<A>/"  or          │
              │  "Domain/<D>/"                │
              │  (from destination_type/name) │
              └────────────────┬──────────────┘
                               │
               ┌───────────────┼────────────────────────┐
               │                                        │
   vault_path starts          vault_path does NOT
   with expected_prefix       start with expected_prefix
               │                                        │
               ▼                                        ▼
            no-op              UPDATE documents SET batch_id = NULL
                               WHERE vault_path = ?
                                         │
                                         ▼
                                 batch_refs_cleared += 1
                                 (on ReconcileResult)
```

**Steps**:

1. Add `batch_refs_cleared: int = 0` to `ReconcileResult` dataclass in `src/pipelines/reconcile.py`. Default `0` keeps existing callers unbroken.

2. Add `reconcile_stale_batch_refs(result: ReconcileResult, ctx: PipelineContext) -> Result[ReconcileResult]` in `src/pipelines/reconcile.py`:
   - Open DB connection via `_connect(ctx.db_path)`
   - Run JOIN query: `SELECT d.vault_path, b.destination_type, b.destination_name FROM documents d JOIN batches b ON d.batch_id = b.batch_id WHERE d.batch_id IS NOT NULL`
   - For each row: compute expected prefix — `"Projects/<A>/"` if `destination_type == "project"` else `"Domain/<D>/"`
   - If `vault_path` does not start with prefix → `UPDATE documents SET batch_id = NULL WHERE vault_path = ?`; increment counter
   - Return `Success(replace(result, batch_refs_cleared=result.batch_refs_cleared + counter))`
   - Return `Success(result)` unchanged if `batches` table does not exist (guard: check schema version or catch `OperationalError`) — makes Stage 6 safe to add before Phase 4 is complete

3. Wire Stage 6 into the `reconcile()` explicit await-chain (Phase 3 added this chain):
   ```python
   match await reconcile_stale_batch_refs(result, ctx):
       case Failure() as f: return f
       case Success(value=r): result = r
   ```
   Insert after the Stage 5 block.

4. Write tests in `tests/test_pipelines/test_reconcile.py`:
   - Document with `batch_id` pointing to `Projects/Alpha/` destination, `vault_path` still under `Projects/Alpha/` → `batch_id` preserved, `batch_refs_cleared=0`
   - Document with `batch_id` pointing to `Projects/Alpha/`, `vault_path` moved to `Projects/Beta/` → `batch_id` nulled, `batch_refs_cleared=1`
   - Document with `batch_id` pointing to `Domain/Engineering/`, `vault_path` moved to inbox → `batch_id` nulled
   - Document with `batch_id=NULL` → skipped (not in JOIN result)
   - `batches` table absent → stage returns `Success` without crashing (graceful no-op)

**Files to modify**:
- `src/pipelines/reconcile.py` — `ReconcileResult` new field; new `reconcile_stale_batch_refs` stage; wire into await-chain

**Test criteria**:
- [ ] File still under batch destination → `batch_id` preserved
- [ ] File moved out of batch destination → `batch_id` nulled
- [ ] Files without `batch_id` → untouched
- [ ] `batch_refs_cleared` counter accurate
- [ ] `batches` table absent → stage is a no-op, `Success` returned (safe to deploy before Phase 4)
- [ ] Full 7-stage reconcile pipeline completes without regression
- [ ] `ReconcileResult.batch_refs_cleared` included in CLI output (update `kms reconcile` log line)

**Status**: [ ] pending

---

## Open Questions

- Should `capture_folder` be exposed as `kms capture --folder <path>` CLI command for manual re-processing? (Non-blocking — watcher-triggered path works without it)
- Should Stage 5 write a `TAG_CLEANUP` audit entry per dirty note? Not required by C-13 but useful for Phase 8 observability.
- Should `apply_location_tags` write a `LOCATION_OVERRIDE` audit entry when it adds/changes tags? Same observability question.
- TD-034 (project-to-domain mapping registry) blocks `apply_location_tags` from setting a `domain/<D>` tag for project-located files. Current behavior: domain tag left to AI inference for project files. Acceptable for now.

## Out of Scope

- Rename gate rework — no spec yet (TD-029)
- TD-035 reconcile mismatch alert for human-locked notes with location drift
- TD-034 project-to-domain registry
- PENDING_REVIEW review UI for folder batches — Phase 3+ concern
- `kms migrate-attachments` for legacy layout (TD-032)
