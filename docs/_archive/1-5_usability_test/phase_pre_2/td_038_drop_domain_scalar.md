# Usability Tests: TD-038 — Drop Redundant Scalar `domain:` Frontmatter Field

_Design doc: `docs/design/phase_pre_2/td_038_drop_domain_scalar.md`_

---

## You can verify (in the vault / Obsidian)

- **Given** a `.md` note captured after this change is deployed  
  **When** you open the file in Obsidian  
  **Then** there is NO `domain:` key in the frontmatter YAML  
  AND the note's `tags:` list contains `domain/finance` (or whichever domain applies)

- **Given** an old `.md` note that has `domain: finance` in frontmatter  
  **When** that note is re-written by any pipeline (capture, reconcile, classify)  
  **Then** the `domain:` key is gone from frontmatter afterward  
  AND `tags:` still contains the correct `domain/finance` tag (unchanged)

- **Given** an old `.md` note with `updated_by_human: true` and `domain: finance`  
  **When** no pipeline touches it  
  **Then** `domain: finance` remains in the file (orphaned harmlessly — human-lock prevents forced rewrite; no pipeline will overwrite it)

---

## Developer must verify

- `NoteMetadata` has no `domain` attribute — `note.metadata.domain` raises `AttributeError`
- `MetadataResult.ai_domain` still exists — internal pipeline state preserved, not removed
- No `domain` column in `documents` table — `upsert()` unchanged, no DB migration needed for this TD
- `test_capture.py:657` assertion updated: `metadata.domain == "finance"` → `"domain/finance" in result.value.metadata.tags`
- `test_frontmatter.py:106` updated: remove `domain="Y"` kwarg from `NoteMetadata` constructor
- `test_frontmatter.py:125` updated: remove `assert meta2.domain == meta.domain`
- `_store_nonmd()` sibling tag construction uses `[t for t in note_meta.tags if t.startswith("domain/")]` — not `note_meta.domain`
- `dumps()` with a note that has `domain:` in `extra`: output YAML must NOT contain `domain:` key (stripped by `_DEPRECATED_KEYS` filter)
- 787 tests pass after change (no new failures)
