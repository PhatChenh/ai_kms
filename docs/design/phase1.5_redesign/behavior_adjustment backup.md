# Domain and project tagging rules:
Desired outcome: If a file sit inside a project/domain, it must have a domain tag matching the project's domain
Explored option to create rule-based tagging like inference through file_path, etc. but they are not very satisfactory, and too complex
Option selected: Let AI guess, but couple the tagging with the AI confidence rating about the location of the file (Add to Phase 2: what AI should behave when the file already in the domain/project folder, but confidence rating of that location is not high)
**Implication**:
- Metadata prompt must include file's vault-relative path as a new variable — AI cannot assess location fit without knowing where the file lives
- AI returns a `location_confidence` score alongside tags; low score → write `location_review: true` to frontmatter (not auto-move — surfaces to human via Phase 2 batch or daily briefing)
- `location_confidence_min` threshold must live in `config/thresholds.yaml` (hook-enforced; no float literals in pipelines)
- Files outside Projects/ or Domain/ (inbox, Documentation, Briefings) have no expected location — AI returns null confidence; pipeline must handle that without error
- This confidence field is a Phase 2 input contract: Phase 2 reads `location_review: true` files and decides surface-to-user vs ignore

## Detailed specs

→ [docs/specs/phase1.5_domain_tagging_location_confidence.md](../../specs/phase1.5_domain_tagging_location_confidence.md)

# Adding tags clean up in reconcile pipeline
Desired outcome: Outdated domain/project tags will be cleaned up in the reconcile pipeline.
**Implication**:
- "Outdated" = tag references a `domain/X` where `Domain/X/` folder no longer exists in vault
- Reconcile must scan all indexed `.md` files, validate their `domain/` tags against current `valid_domains`, and flag or remove stale ones
- Must NOT auto-remove if `updated_by_human = true` — surface as conflict instead
- Touches `NoteMetadata` rewrite → same `updated_by_human` guard as any AI write
- `valid_domains` is runtime-derived (vault scan), so reconcile must re-scan Domain/ folders at start

**Potential option**:
- **A (flag only):** reconcile writes a `tags_stale: true` frontmatter field + audit entry. Human or Phase 2 decides what to replace stale tag with. Safest — no data loss.
- **B (remove + notify):** reconcile strips the stale `domain/X` tag, writes `TAGS_CLEANED` audit entry, logs affected files to stdout. Simple; loses the tag permanently with no replacement.
- **C (remove + re-infer):** reconcile strips stale tag, then calls metadata stage (LLM) to re-suggest domain. Expensive; risks churn on files where no good domain match exists.
- **Recommended: A for now.** Flag only; let Phase 2 / daily briefing surface stale-tag files to user for manual re-routing. Avoids silent data mutation in reconcile which already does destructive ops (sibling deletion).



- summary of files in the moment of adding them will not take any other inputs - pure summary

# Folder handling in the capture pipeline?


# Rename logic rework - deferred


# Handlers extension
just need to implement

# Idempotent for capturing flow

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