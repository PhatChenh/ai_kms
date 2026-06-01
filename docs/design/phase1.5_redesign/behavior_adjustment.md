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





# Folder handling in the capture pipeline?
## Intent & Rational:
**Intent**:
**Goal**:
**Anti-goal**:
## AI clarifying:


# Rename logic rework - deferred
## Intent & Rational:
**Intent**:
**Goal**:
**Anti-goal**:
## AI clarifying:


# Handle missing file while AI summarize (file drop, AI summrizing, but when done, file moved)
## Intent & Rational:
**Intent**:
**Goal**:
**Anti-goal**:
## AI clarifying:



# Handlers extension
just need to implement

# Idempotent for capturing flow
## Intent & Rational:
**Intent**:
**Goal**:
**Anti-goal**:
## AI clarifying:

**Diagnosis (2026-05-27):**

The architecture overview (`docs/architecture/phase1_capture/_OVERVIEW.md`) documents a deduplication gate inside the `store` stage:
> "Has this exact content been captured before? (check content hash in documents table) → YES → write SKIPPED audit entry, done"

This gate **does not exist in code**. `pipelines/capture.py::_store_md` (line ~344) has no content-hash check before writing. It goes straight to rename-gate → `write_note` → `documents.upsert`. Re-running `kms capture` on an unchanged file therefore:
1. Re-calls the LLM (summarize + metadata stages) — wastes tokens
2. Overwrites frontmatter (summary, title, tags) with potentially different AI output
3. Writes a new `CAPTURED` audit entry each time — inflates audit log

The only dedup signal that exists is `is_existing_doc` (line ~357): a flag read from `documents.get_by_path` used exclusively to influence rename-gate logic (Rule 1 SKIP = don't rename an already-indexed doc). It does **not** short-circuit the pipeline.

**Root cause:** spec-first doc written before implementation; content-hash exit path was never built.

**What needs to happen:**
- In `_store_md` and `_store_nonmd`, after reading the file, compute `content_hash` and check `documents.get_by_path`.
- If row exists AND `content_hash` matches → write `SKIPPED` audit entry, return `Success(existing_outcome)`, exit early (no LLM calls).
- If row exists but hash differs → re-capture is legitimate (file was edited).
- `SKIPPED` audit outcome string must be added; Phase 8 briefing should not count SKIPPED entries as new knowledge.

**Recapture logic in case of row exists but hash differs**:
...

**Files to change:** `src/pipelines/capture.py` (`_store_md`, `_store_nonmd`, and the `store` dispatch function that calls them). The content hash is already computed by `vault/writer.py::write_note` and stored via `documents.upsert` — it just needs to be read back early.


Schedule policing:
- summary review be taken with the position of the file, and the AI should judge if this file belong to the right place