# Plan: Revise Attachment Layout (Brief #1 — Vault-Layer Primitives)
_Last updated: 2026-05-23_
_Status: [~] in progress_

> **Scope**: vault-layer primitives only. Capture pipeline branching (Brief #2: `attachment_capture_pipeline`) and sync/archive (Brief #3: `attachment_sync_and_archive`) are out of scope.
>
> **Decisions locked before plan written**:
> - OQ-AL1 → Option C: `vault_path` = sibling `.md` + frontmatter `attachment_path` pointer to binary
> - OQ-AL2 → Remove `attachment_path` property from `VaultConfig` (callers in `capture.py` / `cli/main.py` are Brief #2/#3 scope)
> - OQ-AL3 → Confirmed non-issue: no code assumes global basename uniqueness outside the capture collision loop
> - OQ-AL4 → Scoped allowlist: `.summaries/` traversed only when parent folder is named `attachment/`
> - OQ-AL5 → Out of scope for Brief #1 (Obsidian wikilink path shape is Brief #2 concern)

---

## Architecture

### Vault layout — before and after

```
BEFORE                              AFTER
──────────────────────────────────  ──────────────────────────────────
Vault/                              Vault/
├── inbox/                          ├── inbox/
│   └── report.pdf  ← dropped here  │   └── report.pdf  ← dropped here
├── attachment/    ← ONE global     ├── Projects/
│   └── report.pdf   folder for     │   └── Strategy/
│       everything                  │       ├── my-notes.md
│                                   │       └── attachment/
└── Projects/                       │           ├── report.pdf  ← binary
    └── Strategy/                   │           └── .summaries/
        └── my-notes.md             │               └── report.md  ← sibling
                                    └── Domain/
                                        └── Finance/
                                            └── attachment/
                                                ├── q1-data.xlsx
                                                └── .summaries/
                                                    └── q1-data.md

Sibling report.md frontmatter (written by Brief #2):
  type: attachment-summary
  attachment_path: Projects/Strategy/attachment/report.pdf
  summary: "Q1 strategy review, 12 pages, covers OKRs and risk register."
```

### Components this plan changes

```
┌─────────────────────────────────────────────────────────────────┐
│  vault/paths.py          [extensible: config]                    │
│  Adds 4 parametrized path builders — no existing helpers change  │
│  · project_attachment("Strategy")                                │
│      → Vault/Projects/Strategy/attachment/                       │
│  · project_summaries("Strategy")                                 │
│      → Vault/Projects/Strategy/attachment/.summaries/            │
│  · domain_attachment("Finance")                                  │
│  · domain_summaries("Finance")                                   │
│  All read attachment_dir + summaries_subdir from VaultConfig.    │
└────────────────────────┬────────────────────────────────────────┘
                         │ reads subdir names from
┌────────────────────────▼────────────────────────────────────────┐
│  core/config.py :: VaultConfig    [extensible: config]           │
│  CHANGE 1: add  summaries_subdir: str = ".summaries"  (Field)   │
│  CHANGE 2: remove attachment_path @property                      │
│            → callers in capture.py / cli/main.py break at       │
│              runtime (not tested — fixed in Brief #2/#3)         │
│  attachment_dir: str = "attachment" — KEPT (used by helpers)     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  vault/indexer.py         [extensible: _DOT_ALLOWLIST]          │
│  Scoped .summaries/ allowlist — 2 prune loops updated            │
│                                                                  │
│  projects/Strategy/attachment/.summaries/  → TRAVERSE  ✓        │
│    (parent folder name == "attachment")                          │
│  inbox/.summaries/                         → SKIP      ✗        │
│    (parent folder name != "attachment")                          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  vault/frontmatter.py     [extensible: _KNOWN_KEYS]             │
│  Adds attachment_path as a typed NoteMetadata field              │
│  · NoteMetadata.attachment_path: str | None = None               │
│  · "_KNOWN_KEYS" gains "attachment_path"                         │
│  Brief #2 capture pipeline sets this field when writing siblings │
└─────────────────────────────────────────────────────────────────┘

(no change)
┌─────────────────────────────────────────────────────────────────┐
│  vault/writer.py, vault/reader.py, storage/documents            │
│  All path-agnostic. write_note accepts any body string.          │
│  documents.vault_path accepts any vault-relative POSIX string.   │
└─────────────────────────────────────────────────────────────────┘

Out of scope — Brief #2:
┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  pipelines/capture.py  — fix attachment_path callers (lines
  456, 461, 627); use new paths.py helpers; write sibling with
  extended summary body + attachment_path frontmatter field
└ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
Out of scope — Brief #3:
┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  cli/main.py:127  — fix attachment_path caller
  vault/watcher.py — per-project attachment skip logic
└ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

### Indexer dotfolder pruning — after change

```
vault walker reaches any folder starting with "."
        │
        ▼
  Is name in _DOT_ALLOWLIST?
  (_DOT_ALLOWLIST = frozenset({".summaries"}))
        │
        NO  ──▶ SKIP (as before)
        │
        YES
        │
        ▼
  Is parent folder named "attachment/"?
        │
        YES ──▶ TRAVERSE (index .md files inside)
        NO  ──▶ SKIP

Examples:
  Projects/Strategy/attachment/.summaries/  → TRAVERSE ✓
  Domain/Finance/attachment/.summaries/     → TRAVERSE ✓
  inbox/.summaries/                         → SKIP     ✗
  Projects/Strategy/.summaries/             → SKIP     ✗
```

---

## Approach

Four independent layers of the vault primitive stack are updated in dependency order: config fields first (paths.py reads them), then paths.py helpers (indexer tests will use them), then indexer traversal (requires paths helpers to be stable), then frontmatter field (independent of indexer, added last as it is the lightest change). Each phase is tested in isolation before the next starts. No pipeline code is touched.

---

## Phases

### Phase 1 — `core/config.py`: add `summaries_subdir`, remove `attachment_path` _(was Phase 2 — swapped due to dependency order)_
**Goal**: Add `summaries_subdir` field to `VaultConfig`; remove `attachment_path` property; fix 2 callers in `capture.py` and `cli/main.py` that used the removed property.

**Completed**: 2026-05-23
**Notes**: Phase order swapped from plan (config must precede paths.py because helpers read `summaries_subdir`). Plan was wrong about `capture.py` tests using MagicMock — `scan_capture` and `_store_nonmd` tests use real `VaultConfig` via `vault_root` fixture. Fixed `capture.py:456`, `capture.py:627`, and `cli/main.py:127` to use `.root / .attachment_dir` directly (equivalent; `# COUPLING:` comments mark Brief #2/#3 work). 576 tests pass (excluding pre-existing Ollama integration test).

**Status**: [x] done

---

### Phase 2 — `vault/paths.py`: 4 new path helpers _(was Phase 1 — swapped due to dependency order)_
**Goal**: Add `project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries` helpers in the same shape as existing helpers.

**Steps**:
1. Open [vault/paths.py](../../vault/paths.py) and read existing helper shape (lazy CONFIG import, mkdir, return Path).
2. Add the 4 new helpers after the existing `domain_index` helper. Each reads `CONFIG.main.vault.attachment_dir` and `CONFIG.main.vault.summaries_subdir` — **do not hardcode** the subdir names `"attachment"` or `".summaries"` in the function bodies.
3. Open [tests/test_vault/test_paths.py](../../tests/test_vault/test_paths.py) and add tests for all 4 helpers. Use `tmp_path`-based config (do not import CONFIG at module scope — cross-phase constraint).

**Files to modify**:
- `vault/paths.py` — add 4 functions
- `tests/test_vault/test_paths.py` — add tests for the 4 functions

**Test criteria**:
- [ ] `project_attachment("Strategy")` returns `vault_root / "Projects" / "Strategy" / "attachment"` and the directory exists after the call
- [ ] `project_summaries("Strategy")` returns `vault_root / "Projects" / "Strategy" / "attachment" / ".summaries"` and the directory exists
- [ ] `domain_attachment("Finance")` and `domain_summaries("Finance")` analogous
- [ ] All 4 helpers respect config overrides (e.g. `attachment_dir = "files"` → path uses `"files"` not `"attachment"`)
- [ ] `uv run pytest tests/test_vault/test_paths.py` passes

**Completed**: 2026-05-23
**Notes**: Added `project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries` to `vault/paths.py`. All 4 read `attachment_dir` and `summaries_subdir` from VaultConfig — no hardcoded subdir names. 8 new tests added (4 default-config, 4 config-override). 594 tests pass.

**Status**: [x] done

### Phase 3 — `vault/indexer.py`: scoped `.summaries/` allowlist
**Goal**: Teach both prune loops in `scan_vault` and `scan_non_md_drops` to traverse `.summaries/` directories when — and only when — their parent folder is named `attachment/`.

**Steps**:
1. Open [vault/indexer.py](../../vault/indexer.py) and read the full file. Identify both `dirnames[:] = [...]` pruning expressions: in `scan_non_md_drops` (lines ~91-97) and `scan_vault` (lines ~119-190).
2. Add module-level constant: `_DOT_ALLOWLIST: frozenset[str] = frozenset({".summaries"})`.
3. Replace the dotfolder prune in both loops. The new condition for keeping a directory `d` in `dirnames` is:

   ```python
   (not d.startswith(".") or (d in _DOT_ALLOWLIST and dirpath.name == "attachment"))
   ```

   This replaces `not d.startswith(".")` in both loops. All other conditions (`d not in IGNORE_DIRS`, symlink check) are unchanged.
4. Verify `scan_vault`'s file-level filter (`if name.startswith("."): continue` at line ~153) — this is fine as-is because `report.md` inside `.summaries/` does not start with `.`. No change needed.
5. Add/extend tests in [tests/test_vault/test_indexer.py](../../tests/test_vault/test_indexer.py).

**Files to modify**:
- `vault/indexer.py` — add `_DOT_ALLOWLIST` constant, update 2 prune expressions
- `tests/test_vault/test_indexer.py` — add traversal tests

**Test criteria**:
- [ ] `scan_vault` on a vault with `Projects/A/attachment/.summaries/report.md` returns a `VaultEntry` for `Projects/A/attachment/.summaries/report.md`
- [ ] `scan_vault` does NOT index `.summaries/` placed directly under `inbox/` (scoping guard works)
- [ ] `scan_vault` does NOT index `.summaries/` placed directly under `Projects/A/` (must be inside `attachment/`)
- [ ] A `.hidden_other/` directory (not in `_DOT_ALLOWLIST`) is still skipped
- [ ] `scan_non_md_drops` — binary `report.pdf` inside `Projects/A/attachment/` is NOT returned (Brief #2's generalization of the skip is flagged but NOT implemented here — `scan_non_md_drops` still takes a single `attachment_path` arg; this is the known coupling TD-RAL-4 precursor)
- [ ] `uv run pytest tests/test_vault/test_indexer.py` passes

**Notes — known remaining coupling**:
- `scan_non_md_drops(root, attachment_path: Path)` signature is unchanged. The single-path skip at line ~111 stops working correctly once the new per-project layout is live (Brief #2/#3 scope). This is the precursor to TD-RAL-4. The function still compiles and its existing tests pass; the semantic gap is documented.

**Completed**: 2026-05-23
**Notes**: Added `_DOT_ALLOWLIST: frozenset[str] = frozenset({".summaries"})` at module level. Updated both `dirnames[:] = [...]` prune expressions in `scan_non_md_drops` and `scan_vault` with the scoped condition `(not d.startswith(".") or (d in _DOT_ALLOWLIST and dirpath.name == "attachment"))`. All 5 test criteria pass. 99 vault tests pass, no regressions.

**Status**: [x] done

---

### Phase 4 — `vault/frontmatter.py`: add `attachment_path` typed field
**Goal**: Make `attachment_path` a first-class typed field on `NoteMetadata` so Brief #2 can set it structurally and tooling (linters, type-checkers) can verify usage.

**Steps**:
1. Open [vault/frontmatter.py](../../vault/frontmatter.py). Read `_KNOWN_KEYS` (lines ~27-42) and `NoteMetadata` model (lines ~45-72).
2. Add `"attachment_path"` to `_KNOWN_KEYS`.
3. Add `attachment_path: str | None = None` to `NoteMetadata` as a `Field` — after `source_file` (they are related: `source_file` = original source before move; `attachment_path` = the binary this sibling proxies).
4. Update [tests/test_vault/test_frontmatter.py](../../tests/test_vault/test_frontmatter.py): add round-trip test and verify unknown-key handling is not broken.

**Files to modify**:
- `vault/frontmatter.py` — add to `_KNOWN_KEYS`, add field to `NoteMetadata`
- `tests/test_vault/test_frontmatter.py` — add tests

**Test criteria**:
- [ ] `parse()` on frontmatter with `attachment_path: Projects/A/attachment/report.pdf` sets `metadata.attachment_path` correctly (not in `extra`)
- [ ] `dumps()` → `parse()` round-trip preserves `attachment_path`
- [ ] `parse()` on frontmatter without `attachment_path` sets `metadata.attachment_path = None`
- [ ] Notes with other unknown keys still collect them in `extra` (existing behavior unchanged)
- [ ] `uv run pytest tests/test_vault/test_frontmatter.py` passes

**Completed**: 2026-05-23
**Notes**: Added `"attachment_path"` to `_KNOWN_KEYS`. Added `attachment_path: str | None = None` field to `NoteMetadata` after `source_file`. Added `attachment_path` to `_coerce_bool_to_str` validator (consistent with other string fields). 4 new tests; all 15 frontmatter tests pass.

**Status**: [x] done

---

## Open Questions

None blocking — all 5 OQs from the research file resolved or deferred:
- OQ-AL1, AL2, AL4: resolved before plan written (see header)
- OQ-AL3: confirmed non-issue by grep
- OQ-AL5: deferred to Brief #2 (Obsidian wikilink path shape)

---

## Out of Scope

- **Brief #2**: `pipelines/capture.py` — fix `attachment_path` callers, use new path helpers, write sibling with extended summary body + frontmatter `attachment_path` field set. OQ-AL5 (wikilink path shape) resolved here.
- **Brief #3**: `vault/watcher.py` per-project attachment skip, `scan_non_md_drops` generalization, archive path per-domain.
- Documentation pass: annotate `docs/research/capture_pipeline.md`, `docs/roadmap.md` Phase 1, `docs/phase_1_detailed_specs.md` with "superseded by revise_attachment_layout" notes (TD-RAL-1 through TD-RAL-3).
- Sibling body extended summary (recorded in `docs/research/revise_attachment_layout.md` § "Brief #2 Forward Requirements").
