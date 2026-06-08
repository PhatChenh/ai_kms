# AI-KMS Domain Context

Key terms and concepts specific to this project. General programming terms omitted.

## Language

### Pipeline & Capture

**LOCATED:**
A non-md binary capture outcome where the file's vault path reveals project or domain context. Binary is moved to the appropriate `attachment/` folder; a rich sibling `.md` summary is written.
_Avoid_: "routed", "placed"

**CLUELESS:**
A non-md binary capture outcome where no project/domain context can be derived from path. Binary is parked in `inbox/`; a pending-routing marker `.md` is written for Phase 2 Classify to resolve.
_Avoid_: "unrouted", "unclassified"

**SKIPPED:**
A capture outcome where the file's content hash matches an existing `documents` row. Pipeline exits early — no LLM calls, no frontmatter overwrite. (Planned — not yet implemented; see behavior_adjustment.md § Idempotent.)
_Avoid_: "duplicate", "already captured"

**LOCATION_OVERRIDE:**
An audit entry written by the `apply_location_tags` stage when it adds or changes a `domain/<D>` tag or `project:` field based on file path, overriding or supplementing the AI-inferred value. Not required by C-13 (location is deterministic, not an AI decision) but useful for Phase 8 observability.
_Avoid_: "path correction", "location fix"

**location confidence:**
Certainty about domain/project tags derived purely from a file's vault path position — zero AI cost, deterministic. Distinct from AI confidence scores (which are probabilistic).
_Avoid_: "path-based tagging", "folder-inferred tag"

**apply_location_tags:**
A pipeline stage inserted between `metadata` and `store` in the capture pipeline. Inspects `raw.source_path`, adds `domain/<D>` to `tags` for domain-folder files, sets `project: <A>` for project-folder files. Does not call LLM.
_Avoid_: "location stage", "path tagger"

**_location_context(path, vault_cfg):**
A helper function in `vault/paths.py` that inspects a file path and returns `(location_type, location_name)` — e.g. `("domain", "Strategy")` or `("project", "Alpha")` or `(None, None)`. Extracted from inline logic in `_store_nonmd`. Shared by `apply_location_tags` (capture) and Phase 2 Classify.
_Avoid_: "path detector", "location resolver"

### Tags & Frontmatter

**domain tag:**
A `domain/<D>` string entry inside the `tags: list[str]` frontmatter field — Obsidian's special tag field. Multiple domain tags per note are allowed.
_Avoid_: `domain:` field (that is a separate legacy string field, not an Obsidian tag)

**project tag / project field:**
The `project: "<A>"` separate frontmatter string field. Not an Obsidian tag. One per note. Set by location only — AI does not infer it.
_Avoid_: "project tag in tags list"

**type tag:**
A `type/<name>` string entry in `tags` (e.g. `type/report`). Exactly one required per note. Validated against `config/tags.yaml::allowed_types`.

**free tag:**
A tag with no namespace prefix (e.g. `strategy`, `q1-review`). 5–10 required per note. Must not start with any `domain/`, `type/`, or other prefix.

### Phase 2 — Classify

**Project Registry:**
The shared, live in-memory lookup of all valid vault destinations — active projects (`Projects/<A>/`) and domain folders (`Domain/<D>/`) — that Classify, Search, and Briefing query. Populated at startup by scanning vault folder structure; kept live by the watcher (folder add/rename/archive triggers an update). Does not include archived projects (`Domain/<D>/Archive/`). Output is grouped by domain; projects with no or stale domain tag appear under `Uncategorized`.
_Avoid_: "project list", "destination table"

**valid destination:**
A project folder or domain folder that an inbox note may be filed into by the Classify pipeline. Specifically: any `Projects/<A>/` or `Domain/<D>/` entry in the Project Registry. Excludes `Domain/<D>/Archive/` entries and `inbox/` itself.
_Avoid_: "routing target", "output folder"

**Uncategorized (registry group):**
A catch-all group in the Project Registry output containing active projects whose `CLAUDE.md` has no domain tag, an unrecognised domain tag, or a stale domain tag (domain folder was deleted/renamed). Classify can still route to these projects; the AI prompt explains the gap and instructs semantic inference. Reconcile resolves the underlying CLAUDE.md issue.
_Avoid_: "unknown domain", "unassigned project"

### Vault Layout

**sibling `.md`:**
A markdown summary file created alongside a non-md binary, named `<binary.name>.md` (e.g. `report.pdf.md`), stored under `attachment/.summaries/`. The `documents` row for the binary points to this sibling as `vault_path`.
_Avoid_: "shadow file", "proxy note"

**CLUELESS marker:**
The specific sibling `.md` written for a CLUELESS binary — parked at `inbox/.summaries/<filename>.md` with `status: pending-routing`. Body is a one-line placeholder. Phase 2 Classify overwrites body and routes binary.
_Avoid_: "pending note", "inbox marker"

**no-edit file:**
A non-`.md` file whose extension is in `VaultConfig.no_edit_extensions` (default: pdf, png, jpg, jpeg, gif, webp). Routed to the hidden `attachment/` folder — not visible to the user in Obsidian. Contrast with editable file. Routing via `resolve_placement()` in `vault/paths.py`.
_Source_: ADR-0006 (accepted 2026-06-04)

**editable file:**
Any non-`.md` file NOT in `no_edit_extensions` (e.g. docx, xlsx, pptx). Lives in the project/domain root so the non-technical user can see and open it in place. NOT hidden in `attachment/`. Content changes detected by watcher and trigger re-summarization.
_Source_: ADR-0006 (accepted 2026-06-04)

**AI-output folder:**
One of `Briefings/`, `Synthesis/`, `Documentation/`. The AI writes here; users never drop source material here. Capture-excluded: watcher and scan_capture skip them entirely so AI outputs are never re-captured.
_Source_: ADR-0006 (accepted 2026-06-04)

**misplaced location:**
Any folder that is NOT one of {`inbox/`, a specific `Projects/<A>/`(+its `attachment/`), a specific `Domain/<D>/`(+its `attachment/`)} and is NOT an AI-output folder. Examples: bare `Projects/`, bare `Domain/`, `Domain/<D>/Archive/`, vault root. A file dropped in a misplaced location is swept to `inbox/` by the watcher.
_Source_: ADR-0006 (accepted 2026-06-04)

**batch-worthy subfolder:**
A folder whose location in the vault signals that a group of files belong together — specifically, any subfolder *inside* `inbox/`, `Projects/<A>/`, or `Domain/<D>/`, but NOT the root of those trees (`inbox/` itself, `Projects/<A>/` itself, or `Domain/<D>/` itself). Files captured from a batch-worthy subfolder are associated with a shared batch identifier so Phase 8 Briefing can group them. A file captured directly into `inbox/` root is NOT batch-worthy (no grouping signal).
_Avoid_: "subfolder drop", "grouped folder"

**live batch membership:**
The meaning of the `batch_id` foreign key on a `documents` row — it records which active batch the file *currently* belongs to based on its folder position, not when it was first captured. If a file moves from one subfolder to another, its batch membership updates. Distinct from a capture timestamp (which records when the file was first processed, never updates).
_Avoid_: "capture batch", "original batch"

**folder_path (on batches table):**
The vault-relative POSIX path of the subfolder that triggered the batch creation (e.g. `inbox/Q2-reports`). Used to look up whether a batch already exists for a given subfolder before creating a new one. NOT UNIQUE — multiple batch rows for the same folder_path are valid (e.g. re-drops after a cleanup); lookup always picks the most recent row.
_Avoid_: "batch folder", "source folder"
