# Adding tags clean up in reconcile pipeline
## Intent & Rational:
**Intent**: Notes that are associated with a domain/project would have their tags point to those. Then, when those domain/project get renamed, or deleted, those tags will be stale. Therefore we need a mechanic to update the stale tags
**Goal**: Adding an update logic into `reconcile` pipeline that 1) delete the stale tags / project field 2) if that a domain tag: check if the field has a domain tag that matches the current note's location -> if yes then do nothing 3) add domain tag / project field based on the current location of that note
**Anti-goal**: Tag update logic should not touch anything outside of domain tag and project frontmatter field (this means not touching any other tags and frontmatter field, and other elements of the note)

## AI clarifying:

**Staleness definitions:**
- `domain/<D>` tag is stale when `Domain/<D>/` folder no longer exists in the vault (not when the note moves away from it).
- `project:` field is stale when its value doesn't match the note's current `Projects/<A>/` location — but only evaluated when the note is currently under `Projects/`. Notes outside `Projects/` have their `project:` field left untouched, even if the referenced project is deleted.

**Behavior per location (runs on ALL notes, every reconcile run):**
- `Domain/<D>/…` →
  1. Remove any `domain/<X>` tag where `Domain/<X>/` no longer exists.
  2. If `domain/<D>` is absent from the remaining tags → add it.
- `Projects/<A>/…` →
  1. Remove any `domain/<X>` tag where `Domain/<X>/` no longer exists.
  2. Set `project: <A>` (overwrite whatever value was there).
- `inbox/` or any other location →
  1. Remove any `domain/<X>` tag where `Domain/<X>/` no longer exists.
  2. `project:` field → leave alone.

**Invariants:**
- Multiple `domain/` tags are allowed. Reconcile only removes stale ones — AI-inferred tags that still point to valid folders are kept.
- `updated_by_human: true` → `write_note(actor="ai")` blocks the write automatically. No special-casing needed.
- Anti-goal: never touch any tag or frontmatter field outside of `domain/<D>` entries in `tags` list and the `project:` string field.

## Design

### Guardrail Checklist

- [x] C-01 · vault writes via `vault/writer.py` — satisfies: stage uses `write_note()` exclusively
- [x] C-02 · `updated_by_human` gate — auto-satisfies: `write_note(actor="ai")` blocks locked notes automatically
- [x] C-03 · pipeline owns merge — **CRITICAL**: stage must call `read_note` first and pass ALL existing fields; only `tags` and `project` change. Omitting any field silently wipes it.
- [x] C-12 · `Result` return type — satisfies: stage returns `Result[ReconcileResult]`
- [ ] C-13 · audit log — not applicable: deterministic non-AI cleanup; no `audit.write()` required

### Decision

`reconcile()` hoists `scan_vault()` to the entry point and passes `entries` explicitly to Stage 1 and Stage 5. `reconcile()` replaces `run_pipeline()` with an explicit await-chain so stages with different signatures can coexist. `_location_context(path, vault_cfg)` extracted to `vault/paths.py` as shared seam for both this stage and the companion `apply_location_tags` capture feature.

### Implications

- "Stale domain tag" means a `domain/<X>` string in `NoteMetadata.tags` where `Vault/Domain/<X>/` no longer exists as a folder. Staleness is about folder existence, not note location. `load_valid_domains()` already exists at `vault/paths.py:87`.
- `project:` field is `NoteMetadata.project: str | None` — a separate frontmatter field, NOT inside the `tags:` list. Both exist; this stage only touches `tags` list entries and `project:`.
- "ALL notes" includes `.summaries/*.md` sibling files. `scan_vault()` already includes these via `_DOT_ALLOWLIST`. A sibling at `Projects/<A>/attachment/.summaries/foo.pdf.md` counts as location `Projects/<A>` → gets `project: <A>`.
- `_location_context(path, vault_cfg) → tuple[str|None, str|None]` returns `("domain", "Engineering")` or `("project", "Alpha")` or `("inbox", None)` or `(None, None)`. Uses `vault_cfg.domain_dir`, `vault_cfg.projects_dir`, `vault_cfg.inbox_dir` — no hardcoded strings. Home: `vault/paths.py`.
- `scan_vault()` called **once** in `reconcile()` entry point. Stage 1 (`reconcile_paths`) signature changes from `(result, ctx)` to `(result, ctx, entries)` — receives pre-computed entries instead of calling `scan_vault()` internally. Stage 5 (`reconcile_stale_tags`) also receives `entries`.
- `reconcile()` drops `run_pipeline()` and uses an explicit await-chain. Stages 2–4 keep `(result, ctx)` signature and are called normally; Stages 1 and 5 receive the extra `entries` arg.
- `read_note(path)` called only for notes that actually need updating (2-pass: scan metadata from `entries`, read content only for dirty notes).
- `ReconcileResult` gets a new `tags_updated: int = 0` counter.

### Known tradeoffs

- **`reconcile_paths` signature change**: existing Stage 1 tests call `await reconcile_paths(initial, pipeline_ctx)` directly — must be updated to pass `entries`. All 2 Stage 1 tests break on the signature change (mechanical fix, not a logic change).
- **`reconcile()` no longer uses `run_pipeline`**: explicit await-chain is more verbose but the only clean way to pass different args to different stages. Stages 2–4 are called in a loop; Stages 1 and 5 are called individually.
- **Verbose NoteMetadata construction**: Pydantic model has no `.replace()`. Every `write_note` call requires constructing a new `NoteMetadata` copying all existing fields explicitly. Verbose but necessary for C-03.
- **`_location_context` extracted early**: second caller (`apply_location_tags` capture stage) expected but not yet built. Accepted because the companion feature's design explicitly anticipates this helper.

### Risks

- **C-03 violation at implementation time**: easy to accidentally pass partial `NoteMetadata` and silently wipe `type`, `summary`, `source`, `attachment_path`, etc. Implementation must always `read_note(path)` first and pass every existing field.
- **`load_valid_domains` inside note loop**: if accidentally placed per-note instead of once before the loop, causes O(N) filesystem scans.
- **`.summaries/` siblings get `project:` updated**: semantically correct but untested until Stage 5 tests are written.

### Open questions

- Should Stage 5 write a `TAG_CLEANUP` audit entry when it changes a note? Not required by C-13 (deterministic, not AI), but useful for Phase 8 observability. Non-blocking.
- TD-035 (mismatch alert for human-locked notes with location drift) remains open. Stage 5 silently skips locked notes per C-02. Separate concern, not in scope.

### ADR references

None yet. Offer: write ADR for extracting `_location_context` to `vault/paths.py` as shared seam (hard to reverse once both stages depend on it).

### Options explored

- **Option B (inline path detection)**: Stage 5 with location detection inlined, no helper extracted. Rejected: forces refactor when `apply_location_tags` capture stage is built.
- **Option C (second scan)**: Stage 5 calls `scan_vault()` independently. Rejected: wasteful second vault walk; Stage 1's scan result is already available and unused.


# Domain and project tagging rules:
## Intent & Rational:
**Context**: When user drop a file directly into specific project or domain folder, it implicates user intentionally want the file to be there. Therefore, if the tag does not match with the project folder, it will be confusing to users
**Goal**: Adjust domain and project tag behavior in the case of a file dropping directly into a specific folder. If the folder is a domain-type, tag will match that domain; If the folder is a project-type, tag will match that project and the domain of that project
**Anti-goal**: Do not touch other tag that is unrelated to domain or project.
## AI clarifying:

**Scope:** File under `Domain/<D>/` (any depth) OR `Projects/<A>/` (any depth). Inbox = no override.

**What "tag" means here:**
- Domain tag = `domain/<D>` entry inside `tags: list[str]` (Obsidian's special tag field)
- Project tag = `project: "<A>"` separate frontmatter string field

**Behavior per location:**
- `Domain/<D>/…` → add `domain/<D>` to `tags` list. Multiple domain tags allowed — add without removing AI-inferred domain tags.
- `Projects/<A>/…` → set `project: <A>` only. No domain tag override — AI infers domain from content. When TD-034 resolves (project registry exists), location stage will add the actual `domain/<D>` tag here.
- `inbox/` → no override; normal AI behavior.

**Conflict rules:**
- If location tag already matches AI output → skip (no-op).
- If `updated_by_human: true` guards the note → human wins, skip override. Mismatch tracked by **TD-035** (reconcile alert deferred).
- Multiple `domain/` tags are allowed. Only `project:` is one-per-note.

**Done when:** automated tests cover all cases + manual test script provided.

## Design

### Decision
Option A — new `apply_location_tags` pipeline stage inserted between `metadata` and `store`. Chosen because the location logic is reusable across capture and Phase 2 Classify, and a named stage is testable in isolation without duplicating path-detection code in both `_store_md` and `_store_nonmd`.

### Implications

- `domain/<D>` goes in the `tags` list (Obsidian's tag field), NOT in the separate `domain:` field. `project:` is a separate string field — always has been, not in tags list.
- `_store_nonmd` already detects location (`target_type`/`target_name` at `capture.py:461-470`) for **file routing** (where the binary moves on disk). That routing logic stays. `apply_location_tags` independently inspects the same path for **tag derivation** — these are separate concerns. The path inspection is duplicated, but it is cheap (pure path math) and the two uses cannot be merged without coupling tagging to file routing.
- `_store_md` currently has NO location detection — new stage adds it for the first time.
- `write_note(actor="ai")` already blocks writes to human-edited notes (`updated_by_human=True`). Human win is automatic — no special-casing needed.
- `MetadataResult` needs a new `ai_project: str | None` field so the stage can pass project context through to `store()`.
- `validate_tags` already checks `domain/<D>` against real `Domain/` folder names. Location stage must add only valid domain tags — validated against `ctx.taxonomy.valid_domains`.

### Known tradeoffs

- **Path inspection runs twice:** `apply_location_tags` inspects path for tagging; `_store_nonmd` inspects the same path again for file routing. Accepted in exchange for clean stage separation.
- **`MetadataResult` grows a field:** `ai_project` is added to carry location-derived project to `store()`. Alternative (re-inspect in `store()`) avoids the field but makes data flow implicit.
- **One more stage to own and test:** Pipeline grows from 5 to 6 stages. Accepted in exchange for reusability across capture and Phase 2 Classify.

### Risks

- `MetadataResult` is used in both `_store_md` and `_store_nonmd` — adding `ai_project` field requires updating those call sites and their tests.
- For project files, location stage sets `project:` only. No synthetic `domain/Uncategorized` — AI handles domain inference. Blocker resolved.
- Phase 2 Classify must import and call the shared `_location_context()` helper — otherwise it silently skips location tagging on CLUELESS resolutions.

### Open questions

- Should the location stage write a `LOCATION_OVERRIDE` audit entry when it adds/changes tags? Not required by C-13 (deterministic, not AI), but useful for Phase 8 observability.
- `_location_context(path, vault_cfg) → (type: str|None, name: str|None)` is a new helper extracted from the inline path inspection in `_store_nonmd` (`capture.py:461-470`). Recommended home: `vault/paths.py` — already owns all path-to-domain-or-project helpers. Non-blocking; location confirmed during spec writing.

### ADR references
None yet — pending open question resolution.

### Options explored
- **Option B (inline in store):** Location logic folded into `_store_md`/`_store_nonmd` as a private helper. Rejected: Phase 2 Classify cannot access private helpers without import coupling; `_store_nonmd` is already long.
- **Option C (prompt augmentation):** Inject `{{ location_hint }}` into AI prompt. Rejected: probabilistic guarantee, requires post-AI validation layer anyway, harder to test deterministically.





# Folder handling in the capture pipeline
## Intent & Rational:
**Intent**: When user drop a whole folder, or when they create a folder, the system will need to process the folder and all the files in them. The file will get standard treatments, plus extra for their association with their folder. The folder will also be processed as an entity. The folder and the whole files inside stay and travel together because once user drop them as a group, that signal strong intention for wanting the files to stay as group.
**Goal**: Handle folder drop by 1) move the folder from inbox to the right specific domain/project, 2) add metadata about the folder and files associated with it for the system to know (not human facing)
**Anti-goal**: Do not give solution that tear the dropped folder apart, with each file end up in a different folder.
## AI clarifying:

**Scope:**
- `inbox/` folder drop → full two-stage pipeline: Stage 1 classifies folder + routes as unit, Stage 2 processes files with batch context. `batches` row created.
- Folder dropped into `Projects/<A>/` or `Domain/<D>/` (any depth under that root) → **no Stage 1 classification** (routing already known from path), but still creates a `batches` row + links all files via `batch_id`. Stage 2 runs per-file capture with batch context injected. Grouping info preserved.
- Folder created at `Projects/` or `Domain/` root level (i.e. new top-level project/domain dir) → same as above: no classification, `batches` row created, per-file capture runs.
- Empty folder anywhere → stability cool-off fires, zero files → discard silently, no `batches` row.
- Nested sub-folders inside any dropped folder: files walked recursively, treated as part of the same batch.

**Detection — stability cool-off (applies to ALL vault locations):**
- `_VaultEventHandler.on_created` currently silently drops `DirCreatedEvent` (`watcher.py:146-147`). Change:
  1. `DirCreatedEvent` anywhere in vault (inbox, Projects, Domain) → register pending folder, start folder-level debounce timer (key: `f"dir:{folder_path}"`).
  2. `FileCreatedEvent` inside a pending folder → reset that folder's timer AND suppress the normal `_on_create` callback for that file.
  3. Timer fires with ≥1 file present → call `on_folder_create(folder_path)`.
- `on_folder_create` handler walks the folder on disk recursively (not from buffered event list).
- Cool-off window: `capture.folder_cooldown_seconds` in `config.yaml`. Default **5.0s** (vault is on OneDrive; network drive events arrive later than local FS).

**Two-stage pipeline — behavior differs by drop location:**

`capture_folder(folder_path)` detects location first, then branches:

- **Inbox drop** → Stage 1 runs:
  - LLM receives folder name + manifest of filenames → decides `target_type` + `target_name` + confidence. Prompt: `prompts/classify_folder.yaml` (C-07).
  - Confidence gate (thresholds from `config/thresholds.yaml`, C-06):
    - ≥ auto → move folder to `Domain/<D>/` or `Projects/<A>/`, run Stage 2.
    - review–auto → park in inbox with `PENDING_REVIEW` batch marker; human decides.
    - < review → CLUELESS; per-file CLUELESS markers written; Phase 2 Classify resolves later.

- **Project/Domain drop** → Stage 1 skipped:
  - Location derived from path (same `_location_context()` helper used elsewhere).
  - `batches` row written immediately with known `destination_type`/`destination_name`, confidence `1.0` (user placed it explicitly).
  - Stage 2 runs directly.

- **Stage 2 — Per-file processing (both paths):** each file runs standard capture pipeline (extract → enrich_urls → summarize → metadata → store). Folder name + sibling file list injected into prompts as batch context. `project:` / `domain:` tag set from batch decision (or from location), not re-derived per file. Every file gets `batch_id` on its `documents` row.

**SQLite — batches table (new migration, C-05):**
- New table: `batches(batch_id PK, folder_name, destination_type, destination_name, confidence, status, file_count, created_at)`.
- `documents` rows for batch files get a `batch_id` FK (new nullable column on `documents`).
- Status values: `PENDING_REVIEW` | `ROUTING` | `PARTIAL` | `COMPLETE` | `CLUELESS`.

**Partial failure:**
- If N of M files fail in Stage 2: log per-file, continue remaining files. Batch status → `PARTIAL`.
- Failed files stay in their post-move location with an error marker. No rollback of folder move on partial failure — reconcile handles cleanup.

**Invariants:**
- `updated_by_human: true` on any file → skip that file (C-02); batch continues.
- All folder and file moves use existing `move_attachment` / `move_note` helpers — no direct disk writes outside `vault/writer.py` (C-01).
- Stage 1 classification (inbox drops only) + per-file metadata both write audit entries (C-13).
- Empty folder at cool-off fire time → log and discard; no pipeline run.

**Anti-goals:**
- Do NOT run Stage 1 classification for folders dropped into `Domain/` or `Projects/` — location is already known, no LLM routing call needed.
- Do NOT route individual files within a batch to different destinations.
- Do NOT start Stage 2 before stability cool-off declares the drop complete.

## Design

### Guardrail Checklist

- [x] C-01 · vault writes via `vault/writer.py` — satisfies: all moves use `move_attachment`/`move_note`
- [x] C-02 · `updated_by_human` gate — satisfies: Stage 2 calls `capture_file` which calls `write_note(actor="ai")`, auto-blocking locked files
- [x] C-03 · pipeline owns merge — satisfies: Stage 2 reuses `capture_file` which calls `read_note` first
- [x] C-04 · PRAGMA foreign_keys=ON — satisfies: new migration uses existing `_connect()` which already sets pragma
- [x] C-05 · schema via versioned .sql deltas — satisfies: `batches` table + `batch_id` column via new migration file
- [x] C-06 · thresholds in `config/thresholds.yaml` — satisfies: Stage 1 gate uses `ConfidenceGate.from_config()`
- [x] C-07 · prompts in YAML — satisfies: Stage 1 prompt in `prompts/classify_folder.yaml`
- [x] C-08 · `get_provider` factory — satisfies: Stage 1 calls `get_provider("capture", ctx.config)`
- [ ] C-10 · `asyncio.run()` wrapper — **RISK**: `on_folder_create` fires on the watchdog thread (sync), not the async event loop. Must dispatch to async correctly (see Decision below).
- [x] C-12 · `Result` return type — satisfies: `capture_folder` returns `Result[list[WriteOutcome]]`
- [x] C-13 · audit log — satisfies: Stage 1 classification + each Stage 2 file write audit entries
- [ ] C-13 · audit log — **OPEN**: `batch_id` not propagated to `audit_log` table; Phase 8 Briefing cannot group entries by batch. Tracked as TD below.

### Decision

Option A — folder-level debounce in `vault/watcher.py` + dedicated `capture_folder()` entry point in `pipelines/capture.py`. Chosen over Option B (scan-based, double-processing risk) and Option C (individual file routing, violates user requirement).

### Implications

- **`_VaultEventHandler` gains pending-folder registry**: a `dict[str, set[Path]]` tracks folders whose timer is running; `on_created` adds folder to registry on ANY `DirCreatedEvent` in the vault (inbox, Projects, Domain); `FileCreatedEvent` inside a pending folder resets its timer AND suppresses the normal `_on_create` callback.
- **`VaultWatcher` gains `on_folder_create` parameter**: optional `Callable[[Path], None]`, defaults to `None` for backward compatibility. All existing call sites in `cli/main.py` and tests need no change unless they want folder handling.
- **Asyncio bridge — critical detail**: `on_folder_create` is called from the watchdog thread. `capture_folder` is async. The CLI's `kms watch` command currently runs a blocking `watcher.start() / join()` with no running event loop on that thread. Solution: the watcher callback wraps the async call with `asyncio.run(capture_folder(...))` in a new `threading.Thread` — same pattern as if the CLI spawned a separate worker. Prevents blocking the watchdog observer thread. `asyncio.run()` creates a new event loop per thread (safe; no loop nesting).
- **`capture_folder` two-stage logic**: detects location via `_location_context()` first, then branches. Inbox drop → Stage 1 renders `classify_folder.yaml`, calls LLM, confidence-gates routing. Project/Domain drop → Stage 1 skipped, `batches` row written immediately with `confidence=1.0`. Both paths run Stage 2: iterate files, call `capture_file(path, ctx_with_batch_id)`. `PipelineContext` gains optional `batch_id: int | None = None` passed through to `store()` to write `batch_id` on the `documents` row.
- **`batches` SQLite table**: written at pipeline entry (status `ROUTING`) before Stage 2 starts — for inbox drops this is after Stage 1 classification; for project/domain drops this is immediately. Updated to `COMPLETE`/`PARTIAL`/`CLUELESS` at end. `documents.upsert` gains an optional `batch_id` kwarg.
- **Folder move timing**: inbox drops only — entire folder moved to destination AFTER Stage 1 decides routing, BEFORE Stage 2 starts. Files already at destination when `capture_file` runs → `_store_nonmd` path detection resolves correctly without changes. Project/domain drops: folder already located, no move needed.
- **CLUELESS folder path**: folder not moved; each file gets individual CLUELESS marker. Phase 2 Classify must query `documents` by `batch_id` to route them as a group — this is a Phase 2 concern, not in scope now.

### Known Tradeoffs

- **Watchdog thread spawns threads**: each folder drop creates a `threading.Thread` to run the async pipeline. Concurrent drops are guaranteed at setup — user migrating existing projects/domains drops many folders simultaneously. Unbounded thread spawn is NOT acceptable. **Mitigation required**: use a `ThreadPoolExecutor` with a fixed `max_workers` cap (e.g. 4) shared across the watcher; excess folders queue and run as slots free. Cap should be configurable via `capture.folder_max_workers` in `config.yaml`.
- **`PipelineContext` grows `batch_id`**: all existing callers pass no `batch_id` (default `None`); no breakage. Field is visible on the context even for non-folder captures.
- **Folder moved before Stage 2**: if Stage 1 fails to move (e.g. destination exists), Stage 2 never runs. Accepted: move failure is unrecoverable without disk rollback.
- **Pending-folder registry is in-memory**: watcher restart loses pending state. If watcher dies mid-drop-debounce, the folder sits wherever it landed with no batch tracking — next `kms capture --scan` picks up the files individually, losing `batch_id` grouping. *(log TD in TECH_DEBT.md during spec — TD-folder-registry-persistence)*

### Risks

- **C-10 asyncio bridge**: `asyncio.run()` from a `threading.Thread` is safe but requires no existing running loop on that thread. Must verify the watchdog thread has no loop. If future refactor adds a loop to the watchdog thread, this breaks.
- **Thread storm on bulk setup**: user migrating existing vault drops many folders at once → N concurrent `DirCreatedEvent` → N threads without a cap. Implementation MUST use `ThreadPoolExecutor(max_workers=N)` on `VaultWatcher`; the executor submits `asyncio.run(capture_folder(...))` as a `Future` rather than spawning bare threads.
- **Suppressed file events race**: if `FileCreatedEvent` fires before the folder's `DirCreatedEvent` is processed (possible on some OS/FS combos), the file event fires `on_create` normally (not inside a pending folder). File gets captured individually; later `on_folder_create` processes the same folder minus that file. Mitigation: `on_folder_create` handler walks the actual folder on disk (not the buffered event list) — files already captured will hit the idempotent early-exit guard in `capture_file` (content-hash check, TD-idempotent).
- **`batch_id` missing from `audit_log`**: Phase 8 Briefing cannot trace a folder batch as a unit. Tracked as TD.
- **`PENDING_REVIEW` batches**: no human-facing review UI exists yet. Marker written to vault but user has no in-app way to approve. Out of scope; review UI is a Phase 3+ concern.

### Open Questions

- Should Stage 1 write a `FOLDER_CLASSIFIED` audit entry even for CLUELESS batches (confidence < review threshold)? Useful for briefing observability. Non-blocking.
- ~~`PENDING_REVIEW` vault marker location~~ — **closed**: no vault `.md` marker for MVP. `batches` row with `status=PENDING_REVIEW` is the record. Phase 3 review UI reads `batches` table and surfaces pending decisions there. Vault marker adds noise with no action mechanism until review UI exists.
- Should `capture_folder` be exposed as `kms capture --folder <path>` CLI command for manual re-processing? Useful for dev/debug. Non-blocking.
- ~~What would happen if a file that used to travel in a subfolder, then get moved out of the subfolder? Would the moved capture pipeline remove the file's batch status?~~ — **closed**: `documents.rename()` only updates `vault_path`; `batch_id` is preserved on any move. Semantically correct — `batch_id` = origin group (historical metadata), not current membership. `on_moved` does not clear it. Reconcile Stage 6 (`reconcile_stale_batch_refs`) will null out stale `batch_id` when a file's location no longer matches the batch destination — tracked as TD-036.

### ADR references

None yet. ADR warranted for: asyncio bridge pattern (watcher thread → threading.Thread → asyncio.run), hard to reverse once CLI watch loop design is locked. Offer: write ADR after spec is confirmed.

### Tech Debt

- **TD-folder-batch-audit**: `batch_id` not propagated to `audit_log` table. Phase 8 Briefing cannot group audit entries by folder batch. Deferred: `audit_log` schema is stable; adding `batch_id` is a separate migration. Acceptable for MVP.
- **TD-folder-partial-rollback** *(log in TECH_DEBT.md during spec)*: no rollback of folder move on partial Stage 2 failure. Failed files stay at destination with no cleanup path until reconcile runs. Accepted for MVP — reconcile handles orphan cleanup.
- **TD-folder-registry-persistence** *(log in TECH_DEBT.md during spec)*: pending-folder registry is in-memory only. Watcher crash mid-debounce → folder's files processed individually by next `scan_capture`, losing `batch_id` grouping. Fix: persist pending-folder state to SQLite (new `pending_folders` table) so watcher restart can resume. Deferred: crash during debounce window is very low probability; scan fallback is correct, just loses batch metadata.

### Options Explored

- **Option B (scan-based detection)**: periodic `scan_capture` extension detects stable new inbox sub-dirs. Rejected: double-processing risk (individual file events fire first, CLUELESS markers written, scan then re-processes same files). Also adds latency (folder processed on next scan cycle, not at drop completion).
- **Option C (folder context injection, no batch)**: watcher fires folder event but files route individually with folder name as prompt hint. Rejected: violates user requirement — "folder as unit, one destination."


# Rename logic rework - deferred
## Intent & Rational:
**Intent**:
**Goal**:
**Anti-goal**:
## AI clarifying:


# Handle missing file while AI summarize 
## Intent & Rational:
**Intent**: file drop in and trigger capture pipeline which invovles calling LLM for summrizing. But since the LLM call take time, it is possible for the file be gone (deleted/moved/renamed) when LLM call finished
**Goal**: Reconcile this behavior
**Anti-goal**:
## AI clarifying:

**Scope — what "missing" means:**
All three disappearance types are in scope: file deleted from vault, moved within vault, renamed. Pipeline treats them identically — abort + audit. No follow-the-file logic needed; watcher fires a fresh event for the new path, which starts a clean capture run.

**Detection point:**
Check file exists at `store` stage entry — before any vault write or DB upsert. Earlier stages (extract, summarize, metadata) do not need to know; they already hold content in memory and run to completion. The existence check gates only the write.

**Behavior on detection:**
1. Skip vault write — do not create partial `.md` in vault.
2. Skip DB upsert — do not create orphaned `documents` row.
3. Write `FILE_LOST` audit entry with: original path, stage where detected (`store`), reason if detectable (deleted / moved / renamed).
4. Return `Failure` from `store` stage — pipeline exits cleanly, no crash.

**Racing pipelines (move/rename):**
Watcher fires `on_moved` → new capture pipeline starts for new path. Old pipeline detects missing file at `store` and aborts with `FILE_LOST`. New pipeline runs clean against new path. No coordination needed between the two.

**Batch behavior:**
If one file in a folder batch disappears mid-pipeline: that file gets `FILE_LOST`, batch continues for remaining files. Batch status → `PARTIAL` if any file lost.

**Success criteria:**
- Pipeline never throws uncaught exception on missing file.
- No partial `.md` written to vault.
- No orphaned `documents` row in DB.
- `FILE_LOST` audit entry written for every lost file.
- Watcher's fresh event for moved/renamed file starts clean capture with no interference from aborted run.

## Design

### Guardrail Checklist

- [x] C-01 · vault writes via `vault/writer.py` — satisfies: abort paths never reach `write_note`
- [x] C-02 · `updated_by_human` gate — satisfies: abort paths never reach `write_note`
- [x] C-03 · pipeline owns merge — satisfies: abort paths never construct partial `NoteMetadata`
- [x] C-12 · `Result` return type — satisfies: both guard clauses return `Failure(...)`
- [ ] C-13 · audit log — not applicable: deterministic non-AI detection; `FILE_LOST` audit written best-effort for observability only, failure to write must not suppress the `Failure` return

### Decision

Option C — guard at `capture_file` entry (fixes pre-existing `path.stat()` crash) + guard at `store()` dispatcher (prevents orphaned sibling for binaries). Both use the `_audit_rename_gate` best-effort audit pattern. Chosen over Option A (leaves `path.stat()` crash) and Option B (leaves orphaned sibling race).

### Implications

- **Pre-existing crash at `capture_file:694`**: `path.stat().st_mtime` raises `FileNotFoundError` if file is deleted before pipeline starts. This is outside `run_pipeline`'s exception wrapper — the exception propagates uncaught up to the watcher callback. Guard at entry wraps this in try/except and returns clean `Failure(recoverable=True)`.
- **Orphaned sibling for binary files**: `_store_nonmd` writes the sibling `.md` FIRST (DECISION-025), then moves the binary. If binary disappears between sibling write and `move_attachment`, sibling is orphaned with broken `attachment_path`. Guard at `store()` entry (before sibling write) prevents this for the common case. Narrow race (file disappears after `store()` check, before `move_attachment`) is residual and already handled by reconcile Stage 4.
- **`.md` files already safe structurally**: `_store_md` calls `read_note(src)` as its first action. If file is gone, `read_note` returns `Failure` — no vault write, no DB write. Gap is audit labeling only (generic failure, not `FILE_LOST`). Guard at `store()` entry catches this before `read_note` and labels it properly.
- **Two FILE_LOST audit paths, never both in same run**: entry-time check fires if file is gone before pipeline starts; store-time check fires if file disappears during LLM calls. A single pipeline run hits at most one.
- **`scan_capture` handles both**: FILE_LOST from entry (`recoverable=True`) is logged + skipped at [capture.py:781](../../../src/pipelines/capture.py#L781). FILE_LOST from store (`recoverable=False`) is logged as warning. Both are correct behaviors.
- **Batch/folder captures (future)**: `capture_folder` calls `capture_file` per file. FILE_LOST on one file is a clean `Failure` → batch status becomes `PARTIAL`. No special-casing needed.

### Known tradeoffs

- **Two guard clauses instead of one**: slightly more code than Option A, but each guards a distinct bug. The entry guard fixes a crash; the store guard fixes data corruption. They are not redundant.
- **Residual binary sibling race**: file disappears after `store()` check but before `move_attachment`. Sibling gets written, binary move fails, `move_attachment` returns `Failure("attachment source not found")`. Sibling is orphaned. Accepted: reconcile Stage 4 already handles orphaned siblings; this race window is milliseconds wide.
- **`FILE_LOST` outcome string is new**: Phase 8 Briefing must not count `FILE_LOST` entries as new captured knowledge. Same requirement as `SKIPPED` (idempotent flow design).

### Risks

- **Audit write failure must not suppress Failure return**: the `FILE_LOST` guard must write audit best-effort and return `Failure` unconditionally, even if `audit.write` fails. Pattern: see `_audit_rename_gate` at [capture.py:274](../../../src/pipelines/capture.py#L274).
- **`recoverable` flag matters for `scan_capture`**: entry-time FILE_LOST → `recoverable=True` (file just gone, no retry will help, but scan should continue). Store-time FILE_LOST → `recoverable=False` (anomalous race, worth logging as warning). Inverting these would either silence real errors or spam logs.

### Open questions

- Should `FILE_LOST` entries be surfaced in Phase 8 Briefing as a "files lost during processing" count? Non-blocking — can be added when briefing is built.
- Should there be a `recoverable=True` vs `recoverable=False` distinction between entry-time and store-time? Recommended yes (see Risks above), but decision deferred to spec.

### ADR references

None. Change is a guard clause addition — not a design decision that's hard to reverse or surprising without context.

### Options explored

- **Option A (guard at `store()` only)**: fixes orphaned sibling, adds FILE_LOST audit at write time. Rejected: leaves pre-existing `path.stat()` crash at `capture_file` entry uncaught.
- **Option B (guard at `capture_file` entry only)**: fixes `path.stat()` crash, adds FILE_LOST audit at entry. Rejected: does not fix orphaned sibling race — binary can disappear during LLM calls and sibling still gets written.



# Idempotent for capturing flow
## Intent & Rational:
**Intent**: Current capture pipeline keep re-capturing the same file and changes its frontmatter
**Goal**: Make capture pipeline idempotent
**Anti-goal**: Do not touch anything outside of capture pipeline - ask user if must and provide reasoning

## AI Clarifying

**Diagnosis (2026-06-02):**

**Verified facts:**

The architecture overview (`docs/architecture/phase1_capture/_OVERVIEW.md:169-175`) documents a content-hash deduplication gate before the vault write:
> "Has this exact content been captured before? (check content hash in documents table) → YES → write SKIPPED audit entry, done"

This gate **does not exist in code**. `pipelines/capture.py::_store_md` (lines 356-362) calls `documents.get_by_path` by vault path, but the result only sets `is_existing_doc` — a flag used exclusively to influence rename-gate logic (Rule 1 SKIP = don't rename an already-indexed doc). It does **not** short-circuit the pipeline. Re-running `kms capture` on an unchanged file therefore:
1. Re-calls the LLM (summarize + metadata stages) — wastes tokens
2. Overwrites frontmatter (summary, title, tags) with potentially different AI output
3. Writes a new `CAPTURED` audit entry each time — inflates audit log

**Root cause:** spec-first doc written before implementation; content-hash exit path was never built.

**Why the fix must be at `capture_file` entry, not `_store_md`:**

The original diagnosis placed the check in `_store_md`/`_store_nonmd` and claimed it would prevent LLM calls. That is wrong. `store` is stage 5; LLM runs in stage 3 (`summarize`) and stage 4 (`metadata`). A check at stage 5 only prevents vault writes and duplicate audit entries — it does not save tokens.

To prevent LLM re-calls, the hash check must be inserted at `capture_file` entry (lines 675-721 of `capture.py`), after the existing cooldown and CLUELESS early-exit guards, before `run_pipeline()`.

**Why hash comparison works at entry (for `.md` files):**

After first capture, `write_note` overwrites the source `.md` file on disk with the AI-rendered version (frontmatter + body) and stores `SHA256(rendered_content)` via `documents.upsert`. On re-capture of an unchanged file, `path.read_bytes()` produces the same bytes as the stored hash → match → skip the whole pipeline.

`vault/writer.py` already computes `hashlib.sha256(body.encode("utf-8")).hexdigest()` and returns it on the `WriteOutcome`. `documents.DocumentRow.content_hash` (str | None) already stores it. Nothing new to build — just read it back early.

**What needs to happen:**
- At `capture_file` entry, after line 714, before `run_pipeline()`:
  1. Compute `current_hash = hashlib.sha256(path.read_bytes()).hexdigest()`
  2. Look up `documents.get_by_path(to_vault_path(path), db_path=ctx.db_path)`
  3. If row exists AND `row.content_hash == current_hash` → write `SKIPPED` audit entry, return `Success` early. No pipeline, no LLM.
  4. If row exists but hash differs → file was edited; re-capture is legitimate. Fall through to `run_pipeline()`.
- `SKIPPED` audit outcome string must be added; Phase 8 briefing must not count SKIPPED entries as new knowledge.

**Recapture logic when hash differs:**
`_store_md` already overwrites frontmatter unconditionally — correct for an edited `.md` file. `_store_nonmd` regenerates the sibling — correct for an updated binary. No special handling needed; existing pipeline runs normally in both cases.

**Non-md files — `source_hash` in sibling frontmatter:**

The existing CLUELESS guard (lines 702-714) only fires for `inbox/.summaries/<name>.md` with `status=pending-routing`. Located binaries (already in `Projects/<A>/attachment/`) have zero idempotency — pipeline runs unconditionally on every trigger.

A "sibling exists → skip" check would also be wrong: it cannot detect that an Excel or Docx was updated. The sibling would go stale silently.

Correct design: store SHA256 of binary bytes as `source_hash` in sibling frontmatter at capture time. At re-capture entry, compare current binary hash against stored `source_hash` — skip if match, re-run if differ.

- **At capture time** (`_store_nonmd`): compute `source_hash = hashlib.sha256(src.read_bytes()).hexdigest()`, add to `sibling_meta` as new `NoteMetadata` field.
- **At `capture_file` entry** (for non-md, after existing CLUELESS guard):
  1. Derive sibling path: `path.parent / vault_cfg.summaries_subdir / f"{path.name}.md"`
  2. If sibling exists: read `source_hash` from frontmatter, compute `hashlib.sha256(path.read_bytes()).hexdigest()`
  3. Match → write `SKIPPED` audit entry, return `Success` early. No pipeline, no LLM.
  4. Differ → binary was updated; re-run pipeline, regenerate sibling.
  5. No sibling → first capture, proceed normally.
- `source_hash` lives co-located with the sibling `.md` — no DB migration needed.
- `NoteMetadata` gains an optional `source_hash: str | None = None` field. Only set for `type=attachment-summary` notes.

**Files to change:**
- `src/pipelines/capture.py` — `capture_file` entry point (both `.md` and non-md hash checks); `_store_nonmd` (write `source_hash` into sibling_meta).
- `src/vault/schema.py` or wherever `NoteMetadata` is defined — add `source_hash: str | None = None` field.
- `_store_md` and `store` dispatch are NOT insertion points.




Schedule policing:
- summary review be taken with the position of the file, and the AI should judge if this file belong to the right place

# Handlers extension
just need to implement