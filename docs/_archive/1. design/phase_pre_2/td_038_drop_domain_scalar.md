# Design: TD-038 — Drop Redundant Scalar `domain:` Frontmatter Field

_Date: 2026-06-03_
_Status: Design complete — awaiting spec_
_Phase: Pre-Phase 2 cleanup_

---

## Decision

**Chosen: Option A** — Drop `domain:` from `NoteMetadata`, auto-strip via `dumps()` blocklist (lazy migration), keep `ai_domain` in `MetadataResult` as internal pipeline state.

Reason: eliminates drift at the source with zero new CLI commands, no bypass of write safety constraints, and no disruption to the pipeline's internal routing logic.

---

## Implications

### What "drop the domain scalar" means in this codebase

Right now every captured note gets a `domain:` frontmatter key (e.g. `domain: finance`) AND a `domain/finance` tag inside `tags:`. These are redundant. The problem: `reconcile_stale_tags` (Stage 5) only fixes the tag when a domain folder is renamed — it never re-syncs the scalar. So the scalar silently holds a stale value long after the tag is corrected.

The fix: stop writing `domain:` as a separate field. Domain lives **only** as a `domain/<D>` tag inside `tags:`.

### What changes in code

**Directly modified:**

- `vault/frontmatter.py` — three removals:
  1. `"domain"` from `_KNOWN_KEYS` (line 34) — parse() will route existing `domain:` keys to `extra` rather than `NoteMetadata.domain`
  2. `domain: str | None = None` field from `NoteMetadata` (line 55)
  3. `"domain"` from `field_validator` (line 68)
  4. Add `_DEPRECATED_KEYS = frozenset({"domain"})` and filter in `dumps()` so any `domain:` key surviving in `extra` is silently dropped on next write

- `pipelines/capture.py` — two removals:
  1. `store()` (line 428): remove `domain=mr.ai_domain` from NoteMetadata constructor — that kwarg no longer exists
  2. `_store_nonmd()` (lines 646–647): replace `([f"domain/{note_meta.domain}"] if note_meta.domain else [])` with tag-filter: `[t for t in note_meta.tags if t.startswith("domain/")]` — domain tags are already in `note_meta.tags` (set by `apply_location_tags` stage)

- `vault/writer.py` — one removal:
  1. `_merge_metadata()` (line 78): remove `domain=incoming.domain` from NoteMetadata constructor

**Not changed:**

- `MetadataResult.ai_domain` (capture.py:60) — internal pipeline state, not frontmatter. `apply_location_tags` sets it; it is used to append `domain/<D>` to `ai_tags`. Removing it would require cascading changes across the metadata and apply_location_tags stages with no net benefit. Kept.
- `pipelines/reconcile.py` — Stage 5 already does NOT touch `domain:` scalar. No change needed.
- `storage/documents.py` — `domain:` was never stored in the documents table. No DB migration needed for TD-038.

**Indirectly affected:**

- `vault/frontmatter.py::dumps()` — adding `_DEPRECATED_KEYS` filter is the lazy migration mechanism. Any note re-written by any pipeline stage will have `domain:` stripped from frontmatter automatically over time.
- Notes on disk that are never touched again will keep `domain:` in their YAML forever — this is acceptable; the field is an unknown key (goes to `extra`) and has no consumers after the code change.

### Guards and constraints that apply

- **C-03 (write safety)**: all vault writes go through `write_note`. The lazy migration respects this — `domain:` is stripped inside `dumps()` (the serializer), not via direct file writes. No bypass needed.
- **updated_by_human gate**: human-locked notes are protected from AI writes. After the code change, human-locked notes that are never re-written will keep `domain:` in YAML — this is intentional. The field is harmless when orphaned.
- **C-14 (audit log)**: the scalar removal is not an AI decision — no audit entry required. `apply_location_tags` already logs domain tag decisions.

### Downstream effects

- Phase 2 classify pipeline: will read notes via `vault/reader.py → NoteMetadata`. With `domain` field removed, classify must derive domain from `tags` (filter for `domain/` prefix). This is straightforward — the same pattern used in `reconcile_stale_tags`.
- Phase 3 search: if a FTS index is built on frontmatter fields, `domain:` won't be there. Domain filtering uses the `domain/<D>` tag in `tags:` instead. More correct, less redundant.
- Phase 8 briefing: reads audit_log, not frontmatter directly. Not affected.

---

## Guardrail Checklist

- [x] **C-03 (vault writes)** — satisfies: dumps() filter is serialization-side, not a bypass
- [x] **C-12 (idempotent writes)** — satisfies: stripping an absent key is a no-op
- [x] **C-14 (audit log)** — not applicable: schema cleanup, not AI decision
- [x] **DB integrity** — satisfies: no DB schema change needed for TD-038
- [x] **updated_by_human gate** — satisfies: human-locked notes not forcibly rewritten; field orphaned harmlessly

_Success criteria moved to `docs/usability_test/phase_pre_2/td_038_drop_domain_scalar.md`._

---

## Options Explored

### Option A — Drop from NoteMetadata + lazy strip via dumps() _(Chosen)_

Add `_DEPRECATED_KEYS` filter to `dumps()`. Domain stripped from frontmatter whenever note is next written by any pipeline. No new CLI, no write-safety bypass.

**Rejects:** nothing novel — straightforward removal.

**Defers:** notes that are never re-touched will keep `domain:` in YAML. Acceptable — the field is harmless when no code reads it.

---

### Option B — Drop from NoteMetadata + one-shot migration command

Add `kms migrate-domain-scalar` CLI command that walks all vault `.md` files and strips `domain:` via direct YAML manipulation (bypassing `write_note`).

**Rejected because:** bypasses the write-safety constraint (C-03). Requires a documented exception and a new `_atomic_write` exposure. The lazy-strip approach achieves the same cleanup over time without the bypass.

---

### Option C — Drop from NoteMetadata AND MetadataResult

Remove `ai_domain` from `MetadataResult` entirely. Derive domain from tags wherever needed inside the pipeline.

**Rejected because:** `apply_location_tags` currently sets `ai_domain` as a convenient handle for the domain name (separate from constructing the tag string). Removing it requires cascading changes to how `apply_location_tags` communicates domain to downstream stages. No user-visible benefit over Option A — `ai_domain` is internal pipeline state, not frontmatter, and creates no drift.

---

## Known Tradeoffs

- Old notes that are never re-touched keep `domain:` as an orphaned YAML key. They will be visible in Obsidian as a frontmatter field with no semantic meaning. This is a cosmetic issue only — `kms reconcile` will strip it the next time it processes each note.
- `MetadataResult.ai_domain` remains as an internal field. A future dev might wonder why `ai_domain` exists but `NoteMetadata.domain` doesn't. A `# TD-038: internal pipeline state only — domain written as domain/<D> tag, not scalar` comment is sufficient.

---

## Open Questions

None blocking implementation.

---

## ADR References

- DECISION-019: tag taxonomy enforcement lives in pipeline (not model validators) — reconcile_stale_tags owns tag repair
- TD-038 source: Code review 2026-06-03 (I3); user decision to override AI domain via location tag
