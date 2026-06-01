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

### Vault Layout

**sibling `.md`:**
A markdown summary file created alongside a non-md binary, named `<binary.name>.md` (e.g. `report.pdf.md`), stored under `attachment/.summaries/`. The `documents` row for the binary points to this sibling as `vault_path`.
_Avoid_: "shadow file", "proxy note"

**CLUELESS marker:**
The specific sibling `.md` written for a CLUELESS binary — parked at `inbox/.summaries/<filename>.md` with `status: pending-routing`. Body is a one-line placeholder. Phase 2 Classify overwrites body and routes binary.
_Avoid_: "pending note", "inbox marker"
