# Mini-Spec: TD-042 — Stage 5 strips deprecated frontmatter keys
_From grill interview, 2026-06-07_

## Requirement (restated)
`reconcile_stale_tags` (Stage 5) must set `dirty=True` when a note's `metadata.extra`
contains any key from `_DEPRECATED_KEYS`, so `write_note` is called and `dumps()` strips
the deprecated key from disk. Currently, notes whose only issue is a deprecated key are
skipped by the `if not dirty: continue` guard.

## Scope
- In: `src/pipelines/reconcile.py` Stage 5 (`reconcile_stale_tags`) only
- In: test covering the new dirty path
- Out: Stage 7 (`reconcile_editable_migration`) — no dirty-flag pattern; different domain
- Out: new counter on `ReconcileResult` — reuse `tags_updated`

## Done when
- P2-REC-01: Note with deprecated key + already-valid tag + correct project → deprecated key absent from disk after `kms reconcile`
- P2-REC-02: Note with deprecated key + `updated_by_human=true` → left untouched (no write, key stays)
- P2-REC-03: Note with no deprecated keys + no other dirty reason → still skipped (no write, no regression)

## Edge cases discussed
- `updated_by_human=true` gate is enforced by `write_note` (returns `Failure recoverable=False`) — Stage 5 already handles this branch silently; no Stage 5 change needed
- `model_copy(update={tags, project})` preserves `extra` — verified in writer._merge_metadata line 85
- `dumps()` strips `_DEPRECATED_KEYS` before serialising — verified at frontmatter.py lines 143-145
- Import `_DEPRECATED_KEYS` from `vault.frontmatter` inside Stage 5 (lazy import, consistent with other Stage 5 imports)
