# Gotchas Archive

Entries removed from `CLAUDE.md ## What Claude gets wrong` during prune passes.
Reason codes: **absorbed** (info folded into another entry), **derivable** (readable from code), **detail** (implementation detail, not a mistake pattern).

---

## Archived 2026-06-04

- **`_is_in_managed_attachment` lives in `vault/paths.py`.** Moved from `vault/indexer.py` in Brief #3 Phase 1. Import from `vault.paths`, not `vault.indexer`.
  _Reason: absorbed — import location now noted in the "two near-twin predicates" entry._

- **`scan_capture` modified loop skips `.summaries/` paths.** Prevents re-capturing sibling .md files which would wipe `attachment_path` from frontmatter (TD-AS-1).
  _Reason: derivable — skip logic is commented in `src/pipelines/capture.py` with TD-AS-1 reference._

- **Reconcile now has 7 stages, not 6.** Stage 7 = `reconcile_editable_migration` — moves editable files out of `attachment/` to project/domain root. Update stage counts if referencing.
  _Reason: derivable — stages are named functions in `src/pipelines/reconcile.py`; Stage 7 is visible._

- **Settle window in watcher coalesces multi-hop moves.** Binary move A→B followed quickly by B→C produces one re-home event (A→C), not two. Default settle window is configurable. Without this, intermediate destinations trigger spurious sibling creation/deletion.
  _Reason: detail — implementation behavior, not a mistake pattern; documented in `vault/watcher.py` (T7)._
