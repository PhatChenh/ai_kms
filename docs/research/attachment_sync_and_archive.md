# Research: attachment_sync_and_archive
_Last updated: 2026-05-24_

> **Depends on**: [revise_attachment_layout.md](revise_attachment_layout.md) (Brief #1) — per-project
> attachment layout, `.summaries/` traversal, `NoteMetadata.attachment_path` field — all already shipped.
>
> **Depends on**: [attachment_capture_pipeline.md](attachment_capture_pipeline.md) (Brief #2) — sibling
> write ordering (DECISION-025), CLUELESS pending-routing markers (DECISION-027), `LOCATED`/`CLUELESS`
> audit outcomes — all shipped 2026-05-24.
>
> **Scope**: (i) Sync mechanics between `attachment/` binaries and `.summaries/` siblings after initial
> capture (delete/move/update propagation). (ii) Per-Domain Archive layout replacing global `Vault/Archive/`.
> Excludes: capture-time initial drop handling (Brief #2), layout primitives (Brief #1), CLAUDE.md writer
> (TD-015), scheduler (Phase 8+).

---

## Overview

Two coupled concerns: sync keeps sibling `.md` summaries consistent with their binary sources after
user actions (delete, move, rename, edit); archive places completed projects under `Domain/<D>/Archive/`
rather than a global `Vault/Archive/`.

They are coupled because archiving moves an entire project directory — attachment binaries and sibling
`.md` files move as a unit — and the indexer/watcher must reconcile both. Currently neither sync nor
archive routing is implemented; both depend on the per-project attachment layout shipped in Brief #1/2.

---

## Key Components

| File | Role |
|---|---|
| [vault/watcher.py](../../vault/watcher.py) | `_VaultEventHandler` + `VaultWatcher`. Dispatches `on_create`, `on_modify`, `on_delete`, `on_move` via debounced `threading.Timer`. Single `attachment_path` arg — broken for per-project layout (TD-023). |
| [vault/indexer.py](../../vault/indexer.py) | `detect_changes` — diffs `scan_vault()` against `documents.all_paths()`. `scan_non_md_drops` — finds loose binaries for capture. `_is_in_managed_attachment` helper (Brief #2 Phase 4). |
| [pipelines/capture.py](../../pipelines/capture.py) | `scan_capture` — calls `detect_changes` then handles added/modified/deleted/moved entries. Non-md loop via `scan_non_md_drops`. |
| [vault/writer.py](../../vault/writer.py) | `move_note(src, dst, actor)` — atomic single-note move. `move_attachment(src, dst)` — binary move, no frontmatter gate. No `move_project` function. |
| [core/config.py](../../core/config.py) `VaultConfig` | `archive_dir = "Archive"`, `archive_path → root / archive_dir`. Unused in pipeline code. No per-domain archive helper. |
| [vault/paths.py](../../vault/paths.py) | `load_valid_domains`, `project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries`. No `domain_archive(name)`. |
| [storage/documents.py](../../storage/documents.py) | `rename(old, new)` — per-row UPDATE of `vault_path` preserving integer id. `delete_by_path` — removes row. `all_paths()` — for `detect_changes`. No bulk prefix-rename. |
| [storage/audit_log.py](../../storage/audit_log.py) | TEXT `outcome` column, no CHECK constraint — any string valid. Append-only trigger. |
| [cli/main.py](../../cli/main.py) | `watch` command instantiates `VaultWatcher` with broken global `attachment_path` (l. 127–128 COUPLING). |

---

## How It Works — Current State

### Watcher event map (Q1)

`_VaultEventHandler` handles four event types. Key: `_should_skip(path)` runs first.

**`_should_skip` (watcher.py:67–81)**:
```python
def _should_skip(self, path: Path) -> bool:
    if self._attachment_path in path.parents:  # ← single global path — broken
        return True
    if path.name.startswith("."):              # ← checks filename, not parent folder
        return True
    if ".sync-conflict-" in path.name:
        return True
    for part in path.parts:
        if part in IGNORE_DIRS:               # ← IGNORE_DIRS has no ".summaries"
            return True
    return False
```

`self._attachment_path` is set from `cli/main.py:128`:
```python
attachment_path = CONFIG.main.vault.root / CONFIG.main.vault.attachment_dir
# = Vault/attachment/ — A FOLDER THAT NO LONGER EXISTS
```

Because `Vault/attachment/` is not a real path, `path.parents` never contains it. **The attachment skip
is a no-op for all per-project paths.**

| Event | `on_*` handler | What fires | Binary paths (e.g. `Projects/A/attachment/report.pdf`) |
|---|---|---|---|
| Create | `on_created` | `on_create(path)` | Fires (skip broken) → `capture_file(pdf)` → creates sibling (correct behavior, but wasteful if pipeline already moved the binary) |
| Modify | `on_modified` | skips non-md explicitly at l. 115–117 | Never fires. Comment: "Binary modify deferred — TD-C6" |
| Delete | `on_deleted` | `on_delete(path)` | Fires → `delete_by_path(binary_vault_path)` → 0 rows (binary not in documents — DECISION-022) |
| Move | `on_moved` | `on_move(src, dst)` | Fires → `rename_doc(old_binary, new_binary)` → 0 rows |

| Event | `.summaries/report.md` sibling path |
|---|---|
| Delete | `path.name = "report.md"` — does NOT start with `.`. `.summaries` not in IGNORE_DIRS. Skip: False. `on_delete` fires → `delete_by_path(sibling_vault_path)` → row deleted. Binary untouched. ✅ Correct. |
| Modify | `on_modified` passes md-only filter → fires `on_modify` → `capture_file(sibling_path)`. ⚠️ Latent bug (see below). |

**Directory moves are completely ignored**:
```python
def on_moved(self, event: DirMovedEvent | FileMovedEvent) -> None:
    if event.is_directory:
        return  # ← directory moves are silently dropped
```

When user drags `Projects/<A>/` to `Domain/<D>/Archive/<A>/`, watchdog fires one `DirMovedEvent`.
The handler returns immediately. Zero DB updates. All rows retain `Projects/<A>/...` vault_paths.

### `detect_changes` and sibling entries (scan_capture)

`scan_vault` now traverses `.summaries/` (Phase 1.5). Siblings appear in `current` entries.
`detect_changes` is path-agnostic — it treats siblings like any other `.md`.

**Consequence**: when a sibling body changes (e.g., Brief #3 sync re-writes a sibling with updated
content), `detect_changes` puts it in `modified`. `scan_capture` then calls `capture_file(sibling_path)`.

Inside `capture_file`, the sibling is `.md` — no early-exit guard applies. The full pipeline runs:
extract (reads sibling body) → summarize (AI re-summarizes the summary text) → metadata (extracts
title/tags from the summary) → store (`_store_md`).

In `_store_md`, `note_meta` is constructed without `attachment_path`:
```python
note_meta = NoteMetadata(
    summary=mr.summary,
    type=mr.ai_type,     # AI may return something other than "attachment-summary"
    domain=mr.ai_domain,
    tags=mr.ai_tags,
    confidence=mr.decision.confidence,
    # attachment_path NOT SET — will be None
)
```

`write_note` writes this metadata back to disk. **The `attachment_path` frontmatter field is wiped.**
The sibling loses its pointer to the binary. The `documents` row is intact but the on-disk frontmatter
is now broken. Phase 4 MCP tools following the `attachment_path` pointer will find `None`.

This is a **latent bug** that has no impact yet (Brief #3 sync not implemented), but will silently corrupt
sibling frontmatter as soon as any sync mechanism re-writes sibling bodies. Must be fixed before Brief #3
implements content-update sync.

### `documents.rename` and `documents.delete_by_path`

`rename(old, new)`:
```python
cur = conn.execute(
    "UPDATE documents SET vault_path = ? WHERE vault_path = ?",
    (new, old),
)
return Success(cur.rowcount)
```
Preserves integer id (DECISION-001). FK references in `audit_log` and `corrections` stable.
Returns `Success(0)` when `old` not found — **silent no-op, not a Failure**.

`delete_by_path(vault_path)`:
```python
cur = conn.execute("DELETE FROM documents WHERE vault_path = ?", (vault_path,))
return Success(cur.rowcount)
```
Also `Success(0)` when not found. Cascade: `corrections.document_id ON DELETE CASCADE` fires.
`audit_log` rows are NOT cascaded — they remain (correct, they are permanent record).

**Critical implication**: When watcher fires `rename_doc(old_binary_vault_path, new_binary_vault_path)`,
`rowcount = 0` and `Success(0)` is returned. The caller in `on_move` logs success:
```python
case Success():
    _wlog.info("watcher.renamed", old=old_rel, new=new_rel)
```
This logs a false success. No error is surfaced. The sibling's `attachment_path` frontmatter remains stale.

### `vault/writer.py` — no `move_project`

`write_note`, `move_note`, `move_attachment` are all single-file operations. No function moves a directory.
For archiving a project, the options are:
1. User drag-drops directory in OS → watcher fires `DirMovedEvent` → silently dropped → `scan_capture` reconciles on next run via content_hash move detection
2. A new `move_project(src_dir, dst_dir, db_path)` in `vault/writer.py` that uses `shutil.move` and bulk-renames DB rows atomically

Without option 2, archive moves are only visible to the DB on next `scan_capture`. DB is inconsistent between user drag-drop and next scan.

### `scan_capture` move detection for directory archives (Q9)

After a project directory move (`Projects/<A>/` → `Domain/<D>/Archive/<A>/`), next `scan_capture`:
1. `scan_vault` walks `Domain/<D>/Archive/<A>/` — returns VaultEntries for all `.md` and siblings
2. `all_paths()` returns old `Projects/<A>/...` vault_paths — all appear as "deleted"
3. `detect_changes` move detection: for each added entry, checks if any deleted entry has the same `content_hash`. If exactly one match, collapses as "moved".
4. `scan_capture` calls `rename_doc(old, new)` for each moved pair → rows updated, integer ids preserved.

**This works correctly for archive if content is unchanged during the move.** Limitation: only fires on
next `scan_capture` call, not real-time. For manual-only archiving (current design), this is acceptable.

**Edge case (Q-001 cross-cut)**: if a file is edited AND moved simultaneously (e.g., user edits a note
then immediately drags the whole project to archive), the content_hash changes. `detect_changes` cannot
match old→new as a move. The old row is deleted (cascade: `corrections` gone), new row inserted with a
new integer id. Audit trail for that document loses continuity. Same risk as Q-001 for regular notes.

### `VaultConfig.archive_path` — current state (Q6)

```python
archive_dir: str = "Archive"
@property
def archive_path(self) -> Path: return self.root / self.archive_dir
```

Points to `Vault/Archive/` (global). **Grep shows zero callers in `vault/`, `pipelines/`, `handlers/`,
`storage/`, `mcp_server/`**. The property is defined but nothing uses it. Unlike `attachment_path`
(which had 3 COUPLING callers in capture.py + cli/main.py), `archive_path` is safe to repurpose.

`load_valid_domains(vault_root)` reads `vault_root / "Domain"` subfolders:
```python
return frozenset(
    p.name for p in domain_dir.iterdir()
    if p.is_dir() and not p.name.startswith(".")
)
```
If `Domain/Uncategorized/` exists, it returns `"Uncategorized"` as a valid domain. No code change needed
for Uncategorized support — it's automatically handled by folder creation.

---

## Edge Cases & Silent Failure Modes

### 1. Watcher false-success on binary rename/delete

`on_delete` and `on_move` for binaries call `delete_by_path` / `rename_doc` with paths not in
`documents`. Both return `Success(0)`. Watcher logs success. Sibling is orphaned silently. No
error is surfaced to the user. This will persist until Brief #3 adds sibling-aware logic.

### 2. Sibling re-capture wipes `attachment_path` (latent bug)

Detailed above. Triggered whenever: (a) Brief #3 sync re-writes sibling body after binary content
update, (b) any future mechanism modifies a sibling `.md`, AND (c) `scan_capture` runs afterward.

**Affected code path**: `scan_capture` modified loop → `capture_file(sibling_path)` → `_store_md` →
`write_note(sibling_path, body, note_meta, "ai")` where `note_meta.attachment_path = None`.

**Fix options** (surfaced; plan stage picks):
- (A) In `capture_file` or `scan_capture`: skip files whose `detect_changes` entry has `metadata.type == "attachment-summary"` in the modified loop.
- (B) In `_store_md`: if `existing_note.metadata.type == "attachment-summary"`, preserve `attachment_path` from existing.
- (C) In `scan_capture` modified loop: filter `summary.modified` to exclude paths under `.summaries/`.

Option (C) is the most surgical (3-line change in `scan_capture`, no pipeline stage logic change). Option (B) is safer (handles any path re-write, not just scan-capture). Either works; plan must pick.

### 3. Double-capture when pipeline moves binary to `attachment/`

When pipeline calls `move_attachment(src, attachment_dst)`, watchdog fires `FileCreatedEvent` for
`attachment_dst`. Watcher `on_create` runs; `_should_skip` returns False (broken global path).
`get_by_path(binary_vault_path)` returns `Success(None)` (binary not in documents). `capture_file(binary_path)` is called. This triggers a second full capture (including LLM calls) for the same binary.

The second capture is idempotent (sibling already exists → `write_note` overwrites with same content;
`updated_by_human = False` → AI write allowed). But it's wasteful: two extra LLM calls per binary.
The debounce window (3s) vs pipeline completion time determines whether both fire. Fix: TD-023
(generalize watcher skip to per-project managed attachment).

### 4. Directory move leaves DB inconsistent until `scan_capture`

Described above. Time window between user archive move and next `scan_capture` = inconsistent DB.
If MCP search runs in that window, it returns results with old vault_paths that no longer exist on disk.
Phase 4 MCP tools opening those paths would fail. Mitigated by running `kms capture --scan` after archive.

### 5. Simultaneous edit + archive move (Q-001 edge case)

Sibling (or regular note) edited AND moved in same beat: `detect_changes` sees deleted(old) + added(new)
with different hashes. No move collapse. Old row deleted (cascade removes corrections). New row inserted
with new id. Audit trail for that document loses continuity. This is the known Q-001 open question —
attachment archive does not worsen it, but it does cross-cut.

### 6. `Domain/Uncategorized/` scope creep

If Phase 2 classify routes active notes to `Domain/Uncategorized/` (treating it as a regular domain),
the Uncategorized folder would accumulate active notes alongside archived projects. The convention
("Uncategorized is for archived-only") is not enforced by code — it's a naming convention.
Phase 2 classify MUST exclude `Uncategorized` from auto-routing targets for active notes.

### 7. Sibling `.summaries/` path check in `_should_skip`

For `Projects/A/attachment/.summaries/report.md`:
- `path.name = "report.md"` — does NOT start with `.`
- `.summaries` in `path.parts` — `.summaries` is NOT in `IGNORE_DIRS`
- Global `attachment_path` check: False (broken)

Result: watcher fires events for sibling `.md` files. `on_delete` correctly removes the DB row.
`on_create` for a new sibling: `get_by_path(sibling_vault_path)` finds the row (just upserted by
pipeline) → skip. Correct.

`on_modify` for sibling: passes through. Triggers latent bug #2.

---

## Dependencies & Coupling

```
vault/watcher.py
├── cli/main.py (instantiates VaultWatcher — provides attachment_path arg)
│   ├── storage/documents.py (delete_by_path, rename, get_by_path)
│   ├── pipelines/capture.py (capture_file, scan_capture)
│   └── vault/paths.py (to_vault_path, load_valid_domains)
├── vault/indexer.py (imports IGNORE_DIRS only — no CONFIG import)
└── threading.Timer (debounce)

vault/indexer.py::detect_changes
├── storage/documents.py::all_paths()
└── vault/indexer.py::scan_vault (produces VaultEntry list)
    └── vault/reader.py (read_note)

pipelines/capture.py::scan_capture
├── vault/indexer.py (detect_changes, scan_vault, scan_non_md_drops)
├── storage/documents.py (rename, delete_by_path, upsert)
└── pipelines/capture.py::capture_file (loop)

core/config.py::VaultConfig
├── archive_dir = "Archive", archive_path → unused
└── attachment_dir, summaries_subdir → used by vault/paths.py helpers
```

**Coupling risk (TD-023)**: `VaultWatcher` constructor signature takes `attachment_path: Path`.
Changing to a `vault_config` arg would require updating:
- `cli/main.py:204` (VaultWatcher instantiation)
- `tests/test_vault/test_watcher.py` — all tests use `attachment_path = root / "attachment"` fixture
  and `_make_handler` helper constructs handler with that arg

---

## Extension Points

| Component | How extended | What blocks extension today |
|---|---|---|
| `_should_skip` per-project skip | Replace `attachment_path: Path` with `vault_config: VaultConfig` + call `_is_in_managed_attachment(path, vault_cfg)` for non-md files | `_VaultEventHandler.__init__` takes `attachment_path: Path`; changing requires signature update + test changes |
| Sibling-aware sync in `on_delete`/`on_move` | Add new callbacks or inline logic: detect binary delete/move → derive sibling path → call existing `move_note`/`delete_by_path` | No extension point exists today; would be new inline logic |
| `vault/writer.py::move_project` | New function: `shutil.move(src_dir, dst_dir)` + `documents.bulk_rename_prefix(old, new, db_path)` | Neither function exists yet |
| `storage/documents.py::bulk_rename_prefix` | New SQL: `UPDATE documents SET vault_path = REPLACE(vault_path, ?, ?) WHERE vault_path LIKE ?` | Not exposed; `rename` is per-row only |
| Per-domain archive helper | `domain_archive(name: str) -> Path` in `vault/paths.py` — same shape as `domain_attachment` | Missing; straightforward to add |
| `VaultConfig.archive_path` repurpose | Remove `archive_path` @property (zero callers in pipeline code); add `archive_dir` usage in new per-domain helper | Safe to remove; no downstream breaks |

---

## Open Questions

| ID | Question | What I checked before marking open |
|---|---|---|
| OQ-AS1 | Real-time watcher-driven sync (on_delete/on_move extend to update sibling) vs periodic reconciliation CLI (`kms reconcile` walks attachment/ folders, fixes orphans). Trade-off: real-time requires watcher signature change + sync logic in watcher callbacks; periodic is a new CLI command with no watcher changes. | Confirmed watcher `on_delete`/`on_move` are the natural hooks. Confirmed `scan_capture` can serve as periodic reconciliation point. The "schedulers come last" rule favors CLI-first. |
| OQ-AS2 | Binary content-update sync: `on_modify` explicitly skips non-md (watcher.py:115-117). Options: (a) mark sibling `stale=true` when binary is modified (requires un-skipping binary mod + new `stale` frontmatter semantics); (b) auto-re-summarize on binary modify (adds LLM cost per binary edit); (c) accept — user triggers `kms capture <file>` manually. | Confirmed `on_modify` skips all non-md with comment "Binary modify deferred — TD-C6". No stale field in NoteMetadata today. |
| OQ-AS3 | `move_project(src_dir, dst_dir, db_path)` in `vault/writer.py`: needed for immediate archive moves (transactional, atomic). Without it, archive consistency depends on next `scan_capture`. Is the `scan_capture` reconciliation window acceptable for manual archiving? | Confirmed existing `detect_changes` move detection works correctly after directory move (content_hash matching). Window = time between user action and next `kms capture --scan`. Acceptable for Phase 1 manual use if documented. `move_project` defers to post-Phase 2. |
| OQ-AS4 | Phase 2 classify archive-aware routing: `load_valid_domains` returns `"Uncategorized"` as a valid domain if that folder exists. Phase 2 must NOT auto-route active notes to `Uncategorized`. Needs an exclusion list or naming convention check. | Checked `load_valid_domains` — no filtering for special names. `Uncategorized` would be returned as a valid routing target. Phase 2 scope. |
| OQ-AS5 | User-deleted sibling (`.summaries/report.md`): watcher `on_delete` removes DB row. Binary is orphaned from search. Was this intentional opt-out or accidental? No way to distinguish. Options: (a) accept — row removed, binary orphaned until next `scan_capture` or manual `kms capture`; (b) add a "deleted sibling" audit entry; (c) add a recovery CLI (`kms capture Projects/A/attachment/report.pdf` re-creates sibling). | Confirmed on_delete fires for sibling (filename `report.md` passes all skip checks). Binary NOT affected. |

---

## Reference Project Patterns

The reference project (`knowledge-base-server`) has no attachment sync concept — it handles structured
documents without binary file tracking. No patterns applicable for sync mechanics.

For archive layout: reference uses no domain/archive separation. Not applicable.

---

## Technical Debt Spotted

| ID | What | Status | Owned by |
|---|---|---|---|
| TD-023 (existing) | `vault/watcher.py` + `cli/main.py:128` take single `attachment_path: Path` — broken for per-project layout. `_should_skip` skip is a no-op for all per-project paths. | Open | Brief #3 |
| TD-AS-1 (new) | Sibling re-capture wipes `attachment_path`: when any sync mechanism re-writes sibling bodies, next `scan_capture` calls `capture_file(sibling)` → `_store_md` constructs `note_meta` without `attachment_path` → pointer wiped on write. Must be fixed before Brief #3 implements content-update sync. | Open — latent; no impact yet | Brief #3 (blocker for content-update sync) |
| TD-AS-2 (new) | `VaultConfig.archive_path` property (points to `Vault/Archive/`) has zero pipeline callers but IS tested at `tests/test_core/test_config.py:350-351` (`assert vault.archive_path == tmp_path / "Archive"`). Should be removed or repurposed when per-domain Archive is implemented — test must be deleted or updated (same pattern as TD-RAL-5 for `attachment_path`). | Open | Brief #3 / Archive layout plan |
| TD-AS-3 (new) | `vault/paths.py` missing `domain_archive(name: str) -> Path` helper. Needed to route archive operations. Pattern identical to `domain_attachment`. | Open | Brief #3 |
| TD-AS-4 (new) | `storage/documents.py` has no `bulk_rename_prefix(old, new, db_path)`. Per-file `rename` is sufficient for watcher-driven renames, but a project-tree archive move generates O(N) sequential UPDATEs. If `move_project` is added to writer.py, a bulk UPDATE (`REPLACE(vault_path, ?, ?)`) is more efficient and atomic. | Open — low priority if `scan_capture` reconciliation is accepted as archive mechanism | Post-Phase 2 |
| TD-C6 (existing) | `on_modify` skips non-md with comment "Binary modify deferred — TD-C6". Binary content updates are not detected by watcher. | Open | Brief #3 / OQ-AS2 |

---

## Downstream Phase Impact

- **Phase 2 (Classify)**: Must exclude `Uncategorized` from active-note routing targets (OQ-AS4).
  Phase 2 classify resolves CLUELESS markers in `inbox/.summaries/` — that loop is not affected by
  archive layout changes (inbox is unchanged).

- **Phase 3 (Retrieval)**: Embeddings computed from sibling body. If sibling `attachment_path` is wiped
  by TD-AS-1 bug, Phase 3 MCP tools cannot follow the pointer to the binary. TD-AS-1 must be fixed
  before Phase 3 builds any tool that reads `attachment_path`.

- **Phase 4 (MCP MVP)**: `kms_search` returns `documents` rows; tool follows `attachment_path` to binary.
  If DB has rows with old vault_paths (archive move not yet reconciled), tool returns stale paths.
  Brief #3 archive reconciliation must happen before or alongside Phase 4 shipping.

- **Phase 8 (Briefing)**: Reads `audit_log`. New Brief #3 audit types (`SIBLING_ORPHANED`,
  `SIBLING_STALE`, `ATTACHMENT_MOVED` or similar — plan stage names) must be chosen consistently
  with existing types (`ROUTED`, `CLUELESS`, `CAPTURED`). Brief #2 already established `ROUTED`/`CLUELESS`
  audit outcomes; Brief #3 adds new ones. No schema change needed (outcome is free-form TEXT).

---

## Self-Review Notes

- **Unsupported claims**: All watcher behavior verified against line numbers in `watcher.py`. The
  directory-move gap (`if event.is_directory: return`) is confirmed at `watcher.py:129`. The
  `attach_path` broken skip is confirmed at `watcher.py:72` and `cli/main.py:128`. `archive_path`
  "zero pipeline callers" verified by grep on `vault/`, `pipelines/`, `handlers/`, `storage/`, `mcp_server/`
  — test caller at `tests/test_core/test_config.py:350-351` found and noted in TD-AS-2.
- **Gaps disguised as confidence**: `mcp_server/` not read (confirmed skip: it's a logic-free wrapper
  per CLAUDE.md constraint; no attachment-path assumptions there). `briefings/` not read (Phase 8, not
  built yet). Neither affects this research scope.
- **Missing downstream impact**: Section added above. TD-AS-1 bug impact on Phase 3/4 flagged explicitly.
- **Contradictions with existing research**: None found. This research extends Brief #1/2 findings; does
  not contradict them.
- **Cargo-culted patterns**: No reference project patterns applicable; explicitly noted.
