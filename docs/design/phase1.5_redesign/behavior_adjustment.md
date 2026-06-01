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