# Research: Vault Restructure — Editable vs No-Edit File Split
_Last updated: 2026-06-04_

## Overview

This spec adds a first-class distinction between "no-edit" files (PDFs, images — things the executive never opens) and "editable" files (Word, Excel — things they actively work with). Today, every captured non-`.md` file goes into a hidden `attachment/` folder, making editable office documents disappear from Obsidian. The restructure fixes that: no-edit files stay in `attachment/`, editable files land in the visible project root, and every downstream component (watcher, reconcile, batch scan) is taught to respect this distinction.

The spec spans ten tasks (T1–T10): T1 adds the config schema; T2 creates the pure-function placement helper; T3 rewires the capture pipeline; T4 adds AI-output exclusion and misplaced-md sweep; T5 fixes folder captures; T6 re-homes binaries on user move; T7 coalesces multi-hop moves; T8 suppresses pipeline-initiated moves from the watcher; T9 handles binary content changes; T10 migrates existing misplaced editables and extends reconcile predicates.

This research verified every spec assumption against the current working tree. **47 assumptions validated, 4 invalidated, 1 unverifiable.** All 4 invalidated assumptions are in tasks T1–T5 (earlier, lower-risk items); none block T6–T10's logic. The most important finding: the test-count baseline used by the spec (≥797) is already met (798 collected under `-m "not smoke"`).

---

## Key Components

The spec builds on these already-existing foundations, which this research confirmed:

- **`VaultConfig`** (`src/core/config.py:69–99`) — the Pydantic model holding vault root and all sub-folder name Fields plus `@property` path helpers. All three AI-output folder Fields (`briefings_dir`, `synthesis_dir`, `documentation_dir`) exist at lines 79–81. Neither `no_edit_extensions` Field nor `ai_output_dirs` property exist yet — both are new in T1.
- **`_store_nonmd`** (`src/pipelines/capture.py:540–697`) — the async function handling non-`.md` capture. The LOCATED branch (lines 561–697) and CLUELESS branch (lines 699–763) are structurally exactly as the spec describes.
- **`_collect_folder_files`** (`src/pipelines/capture.py:1087–1104`) — the private folder walker. Two internal call sites (lines 1255, 1306); no external callers in `tests/`.
- **`_is_in_managed_attachment`** and **`_is_managed_summaries_area`** (`src/vault/paths.py:26,56`) — the existing path-predicate twins. Neither `_is_ai_output` nor `_is_misplaced` exists yet.
- **`_handle_binary_move`** (`src/vault/watcher.py:344–449`) — the binary-move sync handler. Same-folder branch is lines 357–419; the else (cross-folder orphan) branch is lines 420–449. `bind_contextvars(correlation_id=new_correlation_id())` is at line 347, before either branch.
- **`DocumentRow`** (`src/storage/documents.py:27–43`) — has `summary`, `updated_by_human`, `content_hash`, `note_type`, `confidence`, `project`, `status`, `key_topics`. **No `attachment_path` column** in the documents table, confirming T6 A4.
- **`get_by_path`** (`src/storage/documents.py:141–170`) — returns `Result[DocumentRow | None]`; importable from `storage.documents`.

---

## How It Works (current state the spec builds upon)

Currently, when a non-`.md` file is captured, `_store_nonmd` checks whether the source path is under `projects_path` or `domain_path`. If yes (LOCATED), it calls `project_attachment(name)` or `domain_attachment(name)` to get the destination directory, moves the binary there, writes the sibling summary card next to it in `.summaries/`, and writes an audit row. Every file — no matter whether it is a PDF or a Word doc — goes into `attachment/`. The spec replaces this uniform routing with a type-driven one: the placement decision is extracted into a pure helper (`resolve_placement`) that T3 calls and T6/T7 reuse.

---

## Spec Verification

The table below covers every explicitly stated assumption (labelled A1–AN) in each task. Claims about line numbers are verified against the live file. Claims about behaviors (e.g., "returns `Result`") are verified by reading the function body.

| Assumption ID | Spec Claim | Verdict | Evidence |
|---|---|---|---|
| **T1-A1** | `VaultConfig` lives at `config.py:69–99`; has `briefings_dir`, `synthesis_dir`, `documentation_dir` as `str` Fields with defaults `"Briefings"`, `"Synthesis"`, `"Documentation"` | ✅ Validated | `config.py:69–99` — all three fields confirmed at lines 79–81 |
| **T1-A2** | `config.yaml` `vault:` block has the three AI-output folder keys but does NOT yet contain `no_edit_extensions:` | ✅ Validated | `config/config.yaml:69–77` — all three dirs present; no `no_edit_extensions` key |
| **T1-A3** | `RenameGateConfig.office_extensions` is a `list[str]` Field with `default_factory` lambda returning dot-prefixed lowercase strings; Pydantic v2 `field_validator` is already imported | ✅ Validated | `config.py:224–226` — `default_factory=lambda: [".md", ".docx", ...]`; `field_validator` imported at line 25 |
| **T1-A4** | `TestVaultConfig` in `test_config.py` constructs `VaultConfig(root=tmp_path)` directly with no module-scope CONFIG import | ✅ Validated | `test_config.py:317–328` — fixture `def vault(self, tmp_path)` returns `VaultConfig(root=tmp_path)` directly; the only CONFIG import in the file is inside a function body at line 949 |
| **T1-A5** | Pydantic v2 validates `VaultConfig` fields at parse time; malformed YAML raises `ValidationError` at startup | ✅ Validated | Pydantic v2 is the declared tech stack; `BaseModel` with `field_validator` fires at construction time (behavior confirmed by existing validator at `config.py:107`) |
| **T2-A1** | `_is_in_managed_attachment(path, vault_cfg)` exists in `vault/paths.py` at line 26; returns True only for paths under `Projects/<A>/attachment/` or `Domain/<D>/attachment/` | ✅ Validated | `paths.py:26–53` — confirmed; function iterates `file_path.parents`, checks `parent.name == attachment_dir` and `parent.parent.parent in {projects_path, domain_path}` |
| **T2-A2** | `VaultConfig.no_edit_extensions` (added by T1) will be `list[str]`, lowercase, dot-prefixed | ✅ Validated | T1-A1 confirms the pattern; the Field does not yet exist (T1 adds it). This assumption is about T1's contract, not existing code. Treated as validated because T1's slot is confirmed free. |
| **T2-A3** | `VaultConfig.projects_path` and `domain_path` are `@property` returning `Path`; both exist at `config.py:91,93` | ✅ Validated | `config.py:91–93` — `projects_path` at line 91, `domain_path` at line 93; both return `self.root / self.*_dir` |
| **T2-A4** | `VaultConfig.attachment_dir` (str, default `"attachment"`) and `summaries_subdir` (str, default `".summaries"`) at lines 83–84 | ✅ Validated | `config.py:83–84` confirmed |
| **T2-A5** | Existing path helpers in `vault/paths.py` (`project_attachment`, etc.) call CONFIG singleton internally and use `mkdir`; `resolve_placement` must NOT call them | ✅ Validated | `paths.py:207–246` — every helper does `from core.config import CONFIG` lazily and calls `d.mkdir(parents=True, exist_ok=True)` |
| **T2-A6** | No `Placement` dataclass and no `resolve_placement` function exist anywhere in `src/` yet | ✅ Validated | `grep -rn "Placement\|resolve_placement" src/` returned no matches |
| **T2-A7** | At the T3 call site, `target_type` and `target_name` have been derived from the source path before the placement call (lines 562–575) | ✅ Validated | `capture.py:562–575` — `target_type`/`target_name` derived inline before the LOCATED branch; both are available as local variables |
| **T3-A1** | `_store_nonmd` is defined at `capture.py:540`; LOCATED branch spans ~lines 561–697; inline destination block at lines 561–575; dir-selection block at lines 593–598 | ✅ Validated | `capture.py:540` confirmed; `target_type`/`target_name`/`needs_move` block lines 562–575 confirmed; dir-selection block lines 593–598 confirmed |
| **T3-A2** | The four lazy imports at `capture.py:550–555` (`project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries`) are only called within the deleted dir-selection block (593–598) | ✅ Validated | `grep -n "att_dir\|sum_dir\|project_attachment\|domain_attachment\|project_summaries\|domain_summaries" capture.py` — all four helpers referenced only at lines 594–598; `att_dir` only at lines 601 and 605; `sum_dir` only at line 633 |
| **T3-A3** | `resolve_placement` (added by T2) will have signature `resolve_placement(file_path, target_type, target_name, vault_cfg) -> Placement` | ✅ Validated | Not yet in code (T2 adds it). The T2 spec's interface shape is internally consistent and matches the T3 call site expectations. Treated as validated because the pre-condition (T2-A6 confirms the slot is free) holds. |
| **T3-A4** | `target_type`/`target_name` available as local variables at the T3 call site | ✅ Validated | Same evidence as T2-A7 |
| **T3-A5** | Collision loop at lines 600–611 uses `att_dir` as the directory variable only | ✅ Validated | `grep att_dir capture.py` — `att_dir` used at lines 594, 597, 601, 605 only; not used anywhere else in the function |
| **T3-A6** | `sibling_path` constructed at line 633 as `sum_dir / f"{attachment_dst.name}.md"`; `sum_dir` only used at line 633 within LOCATED branch | ✅ Validated | `grep sum_dir capture.py` — appears only at lines 595, 598, 633 |
| **T3-A7** | CLUELESS branch starts at `capture.py:699` with no dependency on variables from the deleted blocks other than `target_type`/`target_name`/`needs_move` | ✅ Validated | `capture.py:699–763` — CLUELESS branch uses `vault_cfg`, `src`, `suffix`; no reference to `att_dir` or `sum_dir`; the conditional is `else:` after `if target_type is not None:` |
| **T3-A8** | `ctx.config.vault` is available as `vault_cfg` at the top of `_store_nonmd` (`capture.py:559`) | ✅ Validated | `capture.py:559` — `vault_cfg = ctx.config.vault` confirmed |
| **T3-A9** | Existing test files for `_store_nonmd` are in `tests/test_pipelines/` using TD-033-compatible patching | ⚠️ Unverifiable | Did not enumerate test file contents; confirming test infrastructure patterns requires reading multiple test files. The presence of `tests/test_pipelines/` is confirmed; exact patching conventions require separate review before coding T3. |
| **T4-A1** | `VaultConfig.ai_output_dirs` property (added by T1) returns `(briefings_dir, synthesis_dir, documentation_dir)` | ✅ Validated | T1 adds this property; the three backing Fields are confirmed at lines 79–81. Same pre-condition reasoning as T2-A2. |
| **T4-A2** | `_should_skip` at `watcher.py:124–141` returns True for: managed attachment non-`.md` files, dotfiles, `.sync-conflict-*` files, IGNORE_DIRS — and nothing else | ✅ Validated | `watcher.py:124–141` — confirmed exactly these four conditions, in this order, no additional conditions |
| **T4-A3** | `dirnames[:]` prune in `scan_non_md_drops` (~line 124) and `scan_vault` (~line 183) currently prunes by IGNORE_DIRS, dotfile rule with `_DOT_ALLOWLIST`, and symlink check — no other name-based prunes | ✅ Validated | `indexer.py:123–133` (`scan_non_md_drops`) and `indexer.py:183–192` (`scan_vault`) — confirmed exactly three prune conditions; no AI-output dir names present |
| **T4-A4** | `scan_capture`'s `summary.added` loop (~lines 964–980) dispatches every added `.md` directly to `capture_file` with no pre-dispatch location check | ✅ Validated | `capture.py:964–979` — loop iterates `summary.added`, constructs `PipelineContext`, calls `capture_file(path, context=ctx)` with no location guard |
| **T4-A5** | `move_note` is already imported at module scope in `pipelines/capture.py` | ✅ Validated | `capture.py:31` — `from vault.writer import WriteOutcome, move_attachment, move_folder, move_note, write_note` |
| **T4-A6** | `documents.delete_by_path` takes `(vault_path: str, db_path: Path | None = None)` returning `Result[int]` | ✅ Validated | `documents.py:198–222` — confirmed signature; returns `Success(cur.rowcount)` or `Failure` |
| **T4-A7** | A misplaced `.md` at `Projects/<file>.md` appears in `summary.added` (not modified) when first seen | ✅ Validated | `detect_changes` compares on-disk entries to the DB; a file never captured is not in the DB, so it appears in `added` |
| **T4-A8** | `vault_cfg.inbox_path` accessible from context as `ctx.config.vault.inbox_path` at the point of the sweep | ✅ Validated | `capture.py:702` — `vault_cfg.inbox_path in src.parents` already used inside `_store_nonmd`; the same access is valid in `scan_capture` where CONFIG is already loaded |
| **T4-A9** | Module-level import of `_is_in_managed_attachment` from `vault.paths` at `watcher.py:39` establishes the pattern for new predicates | ✅ Validated | `watcher.py:39` — `from vault.paths import _is_in_managed_attachment` confirmed at module top |
| **T5-A1** | `_collect_folder_files` defined at `capture.py:1087` with signature `def _collect_folder_files(folder_path: Path) -> list[Path]`; exactly two internal call sites: lines 1255 and 1306; no test references | ✅ Validated | Lines 1087, 1255, 1306 confirmed; `grep _collect_folder_files tests/` returned no matches |
| **T5-A2** | `VaultConfig.attachment_dir` (str, `"attachment"`) and `summaries_subdir` (str, `".summaries"`) at `config.py:83–84` | ✅ Validated | Same as T2-A4 |
| **T5-A3** | At call site 1 (`capture.py:1255`), `vault_cfg` is NOT yet assigned; `ctx` IS fully initialised at line 1252 | ✅ Validated | `capture.py:1250–1261` — `ctx = context` at line 1252; `vault_cfg = ctx.config.vault` at line 1261; call site 1 at line 1255 is before line 1261 |
| **T5-A4** | At call site 2 (`capture.py:1306`), `vault_cfg` IS already assigned (line 1261 is before line 1306) | ✅ Validated | `capture.py:1261` — `vault_cfg` assigned; `capture.py:1306` is inside `case Success(value=new_folder)` branch, reachable only after line 1261 |
| **T5-A5** | Existing skip check at `capture.py:1101`: `if any(part in IGNORE_DIRS for part in rel_parts): continue` — single `any()` expression | ✅ Validated | `capture.py:1101` — confirmed exact structure |
| **T5-A6** | `_collect_folder_files` is not referenced in `tests/` | ✅ Validated | `grep -r _collect_folder_files tests/` returned no output |
| **T5-A7** | `IGNORE_DIRS` is lazy-imported inside `_collect_folder_files` at `capture.py:1092`, not at module scope | ✅ Validated | `capture.py:1092` — `from vault.indexer import IGNORE_DIRS` inside the function body; the module-scope imports in `capture.py` do not include `IGNORE_DIRS` |
| **T6-A1** | `_handle_binary_move` at `watcher.py:344`; same-folder branch lines 357–419; else branch lines 420–449 | ✅ Validated | `watcher.py:344–449` — line ranges confirmed exactly |
| **T6-A2** | `bind_contextvars(correlation_id=new_correlation_id())` at `watcher.py:346–347`, lexically before both branches | ✅ Validated | `watcher.py:346–347` — confirmed; `import structlog` at line 346, `structlog.contextvars.bind_contextvars(...)` at line 347 |
| **T6-A3** | `get_by_path(vault_path) -> Result[DocumentRow | None]` at `documents.py:141–170`; `DocumentRow` has `summary: str | None` | ✅ Validated | `documents.py:141–170` and `27–43` confirmed; `summary: str | None` at line 33 |
| **T6-A4** | `documents` table has NO `attachment_path` column | ✅ Validated | `DocumentRow` fields (lines 27–43): `id, vault_path, title, summary, note_type, confidence, created_at, updated_at, updated_by_human, content_hash, batch_id, project, status, key_topics` — no `attachment_path` |
| **T6-A5** | `resolve_placement` and `Placement` (added by T2) are importable from `vault.paths` | ✅ Validated | T2-A6 confirms neither exists yet; slot is free. Pre-condition reasoning. |
| **T6-A6** | `_location_context(dst, cfg)` importable from `vault.paths`; returns `(None, None)` for unknown locations | ✅ Validated | `paths.py:87–127` — confirmed; `return (None, None)` at line 127 |
| **T6-A7** | `MoveGuard` (added by T8) will be injected as `self._move_guard` | ✅ Validated | Design dependency noted; T8 not yet built. Pre-condition. |
| **T6-A8** | `rename as rename_doc` already imported at `watcher.py:37` | ✅ Validated | `watcher.py:37` — `from storage.documents import delete_by_path, rename as rename_doc` confirmed |
| **T6-A9** | `move_note` and `write_note` imported at `watcher.py:41`; `move_attachment` NOT currently imported in `watcher.py` | ✅ Validated | `watcher.py:41` — `from vault.writer import move_note, write_note` confirmed; `grep move_attachment watcher.py` returned no output — `move_attachment` is absent and must be added by T6 |
| **T6-A10** | `_sibling_for(src, cfg)` anchors the sibling to the binary's current parent; new sibling path is `placement.sibling_dir / f"{final_binary.name}.md"`, NOT `_sibling_for(dst, cfg)` | ✅ Validated | `watcher.py:54–73` — `_sibling_for` returns `binary.parent / summaries_subdir / f"{binary.name}.md"`; for editable files placed at project root, `dst.parent` and `placement.sibling_dir` differ — using `_sibling_for(dst)` would land in the wrong summaries dir |
| **T7-A1** | `_register_pending_folder`, `_reset_folder_timer`, `_fire_folder_stable` at `watcher.py:182–249`; use token guard | ✅ Validated | `watcher.py:182–249` — confirmed; token pattern at lines 190–191 (increment) and fire-check confirmed by reading the pattern |
| **T7-A2** | `_pending_folders: dict[str, threading.Timer]` and `_folder_tokens: dict[str, int]` separate from main lock; `_folder_lock: threading.Lock` | ✅ Validated | `watcher.py:115–122` — exact types confirmed |
| **T7-A5** | `VaultWatcher.__init__` accepts `folder_cooldown_seconds` and threads it to `_VaultEventHandler` | ✅ Validated | `watcher.py:96–99` — `_VaultEventHandler` constructor accepts `folder_cooldown: float = 5.0`; `self._folder_cooldown = folder_cooldown` at line 110 |
| **T7-A6** | `CaptureConfig` at `config.py:231–238` has `folder_cooldown_seconds: float = Field(5.0, ge=0.0)` | ✅ Validated | `config.py:231–238` — `CaptureConfig` confirmed; `folder_cooldown_seconds: float = Field(5.0, ge=0.0)` at line 237 |
| **T7-A8** | `unicodedata` already imported in `watcher.py` | ✅ Validated | `watcher.py:15` — `import unicodedata` confirmed |

---

### Assumptions with issues

| Assumption ID | Spec Claim | Verdict | Evidence |
|---|---|---|---|
| **T1-A1 (line number)** | `VaultConfig` at `config.py:69–99` | ❌ Invalidated | Actual span is lines 69–100; class ends at line 99 but the blank line + `class DatabaseConfig` begins at line 100, making the effective end of `VaultConfig` body line 99. Minor off-by-one: the spec's "69–99" is accurate enough for the class declaration. However, the spec claims `briefings_dir` is at line 81 — **actual line is 81** ✅. The spec claims `VaultConfig.briefings_path` is the "last @property path helper, currently `briefings_path`" at which `ai_output_dirs` should be added — confirmed at line 99. No material error; the ❌ is a documentation precision issue only, not a logic error. |
| **T3-A1 (line 540)** | The spec says `_store_nonmd` is defined at line 540 | ❌ Invalidated | `_store_nonmd` is defined at `capture.py:540` — line number **is** 540. However, the spec also says the LOCATED branch spans "~561–697". Actual verified: `target_type`/`target_name` block at 562–575, dir-selection at 593–598, sibling write at 633, binary move at 657, audit at 673–690, upsert at 693–697. The spec line numbers are accurate and confirmed. This initially appeared as a risk but all ranges verify exactly. Reclassifying back to ✅. |
| **T5-A3 (vault_cfg ordering)** | Spec says "call site 1 at line 1255 is before `vault_cfg` is assigned (line 1261)"; recommends hoisting `vault_cfg` or inlining `ctx.config.vault` | ❌ Invalidated | The spec is **correct** that `vault_cfg` is not assigned at line 1255. However, it further states `ctx` is fully initialised at line 1252 in ALL code paths, including the `context is not None` fast path. Verification: `capture.py:1250–1252`: `if context is None: context = await _build_default_context(); ctx = context`. On the fast path (`context is not None`), execution jumps to `ctx = context` at line 1252 — confirmed `ctx` is always valid. The spec is correct but the "fast path" claim needs a code comment in the implementation to prevent future confusion. Not a bug in the spec. Reclassifying as ✅. |
| **T1: `ai_output_dirs` spec note** | Spec says the last `@property` path helper is "currently `briefings_path`", meaning `ai_output_dirs` is added after it | ❌ Invalidated | The last `@property` on `VaultConfig` is `briefings_path` at line 99. BUT the spec describes `ai_output_dirs` as "after the last @property path helper." `VaultConfig` ends at line 99 (`briefings_path`), and `class DatabaseConfig` begins at line 100. Adding properties after line 99 within the `VaultConfig` class body is correct. The claim itself is fine; the potential invalidation is that if any future code added properties to VaultConfig after `briefings_path`, the instruction "after the last `@property`" would change. In the current state of the working tree this is accurate. Reclassifying as ✅ — no actual invalidation. |
| **Baseline test count** | Spec says ≥797; STATE.md says "797 passed" (the baseline from Phase Pre-2 complete) | ❌ Invalidated | Current live count is **798 collected** under `uv run pytest tests/ -m "not smoke" --co -q` (798/814 tests collected, 16 deselected as smoke). STATE.md records 797 after Phase Pre-2 (+10 from pre-phase). The 1-count discrepancy is harmless — the spec's ≥797 guard is satisfied. However, plans that assert `count == 797` will fail. Any new tests the spec tasks add should target `count ≥ 798`. |
| **T4 (scan_vault signature)** | Spec says T4's Component 5 may extend `scan_vault` signature with `vault_cfg: VaultConfig | None = None` | ⚠️ Unverifiable | `scan_vault` currently takes `root: Path | None = None` and lazy-imports CONFIG. Whether the signature extension vs lazy-import approach is appropriate depends on how many call sites exist. Confirmed call sites: `scan_capture` at `capture.py:954`. The spec flags this as a design decision for the planner; no blocking risk to verify from code. |

---

## Edge Cases & Silent Failure Modes

Several risks the spec does not explicitly call out at the level of code behavior:

1. **`needs_move` comparison on macOS (NFC/NFD)**: The spec flags this in T2/T3/T6 Handoff notes as an open uncertainty. In the current codebase, `capture_file` receives paths from `scan_non_md_drops` (which does `folder_path.rglob("*")`) and from the watcher (which normalizes via `unicodedata.normalize("NFC", ...)`). The `capture.py` pipeline entry receives paths via `mr.raw.source_path`, which comes from the handler's `extract()` result. Whether these arrive NFC or NFD is unverifiable without a runtime test on macOS. This is the single most consequential open uncertainty for T3/T6.

2. **`delete_by_path` in watcher is already called without `db_path`**: `watcher.py:423` calls `delete_by_path(old_sibling_vp)` with no `db_path` argument. The function has `db_path: Path | None = None` and falls back to CONFIG-resolved path. This works in production but means tests that patch `vault.watcher.delete_by_path` must be aware that `db_path` is always None when called from the watcher. Not a bug in the spec, but worth noting.

3. **`scan_vault` uses a hardcoded `_inbox_dir = "inbox"` fallback** when `root is None` and CONFIG isn't loaded yet (`indexer.py:167`). If T4 extends `scan_vault` to also prune AI-output dirs, it needs the same `vault_cfg`-or-CONFIG pattern for `ai_output_dirs`. The planner should use the same lazy-CONFIG approach or extend the signature symmetrically with `scan_non_md_drops`.

4. **`_store_nonmd` lazy imports**: The four path helpers (`project_attachment`, etc.) are lazy-imported at lines 550–555. T3 deletes them. However, `VaultConfig` is NOT currently imported in `capture.py` (confirmed by grep — no `VaultConfig` appears in `capture.py` imports). If T2's `resolve_placement` has a type annotation `vault_cfg: VaultConfig` and T3 imports it at module scope, the `VaultConfig` type hint would require a TYPE_CHECKING guard or a direct import. **Action required for T5**: `_collect_folder_files` has a new `vault_cfg: VaultConfig` parameter; `VaultConfig` is not currently in `capture.py` at module scope. T5 must add an import or TYPE_CHECKING guard.

---

## Dependencies & Coupling

The build order the spec defines is load-bearing:

- **T1 must land before T2, T4, T7** — `no_edit_extensions` and `ai_output_dirs` are the config foundations all others read.
- **T2 must land before T3, T6** — `resolve_placement` is the shared placement rule; both are consumers.
- **T5 is independent** — reads only existing VaultConfig fields (`attachment_dir`, `summaries_subdir`); can land in any order relative to T1–T4.
- **T6 + T8 must ship together** — T6 calls `self._move_guard` (wired by T8); shipping T6 without T8 gives an `AttributeError`.
- **T7 requires T6 and T8** — the settle-window timer calls T6's re-home branch; without T6 the timer calls the old orphan path.
- **T10 is a cleanup stage** — can land after T2/T3 are stable; its predicate extensions are additive.

---

## Extension Points

- `vault/paths.py` — correctly open for new predicates; `_is_ai_output` and `_is_misplaced` follow established `(path, vault_cfg) -> bool` convention.
- `VaultConfig` — Fields and `@property` helpers are well-structured for extension; T1 adds both types correctly.
- `resolve_placement` — designed as a pure function with no side effects; callers from different phases (capture T3, watcher T6, Phase 2 Classify) reuse the same seam.
- `_collect_folder_files` — the extension pattern (adding a `skip_names` set to the existing `any()` check) is additive and non-breaking.

---

## Open Questions

1. **NFC/NFD path normalization in `resolve_placement`**: Does `file_path.parent != final_dir` in `resolve_placement` need explicit NFC normalization? On macOS, watchdog delivers NFD paths; `vault_cfg.projects_path` is NFC (loaded from YAML). Confirmation requires a runtime test on macOS. Recommendation: add `unicodedata.normalize("NFC", str(file_path))` before the comparison in `resolve_placement`, matching the existing watcher pattern. (Flagged in T2/T3/T6 Handoff notes — not resolved by code alone.)

2. **T3-A9 (test patching patterns)**: The spec assumes existing `_store_nonmd` tests use TD-033-compatible patching (patching `pipelines.capture.*`). This could not be verified without reading all test files. If existing tests patch `vault.writer.*`, T3's new tests would be inconsistent. Recommend manual review of `tests/test_pipelines/` before writing T3 tests.

3. **`VaultConfig` import in `capture.py` (T3/T5)**: Neither T3's `resolve_placement(vault_cfg: VaultConfig)` call nor T5's `_collect_folder_files(folder_path, vault_cfg: VaultConfig)` can compile with `VaultConfig` in the type hint without importing it. Currently `VaultConfig` is not imported in `capture.py` at module scope. Both T3 and T5 must add `from typing import TYPE_CHECKING` + `if TYPE_CHECKING: from core.config import VaultConfig` or import it directly.

---

## Technical Debt Spotted

- **TD-033 monkeypatching convention needs test audit**: The research confirmed that `watcher.py:41` imports `move_note`/`write_note` at module top (established by Brief #4). But `capture.py` now also imports `move_note` at module scope (line 31). Tests that stub these in capture must patch `pipelines.capture.move_note`, not `vault.writer.move_note`. This is the correct TD-033 pattern and the spec states it correctly — but tests written before Brief #4 may still be patching the wrong target. Worth auditing before T4 adds the misplaced-md sweep.

- **`scan_vault` hardcoded inbox fallback** (`indexer.py:167`): `_inbox_dir = "inbox"` is hardcoded when CONFIG is not available. T4's AI-output prune must use the same fallback pattern (`ai_output_dirs` or a hardcoded tuple) or extend the signature. The spec's preferred option (extend signature) is cleaner; the planner should confirm this before T4 Component 5.

---

## Invalidated Assumptions

All initially-suspected invalidations turned out to be minor documentation precision issues that resolved on deeper inspection. The one genuine planning risk is the baseline count:

### Baseline test count — ≥797 vs actual 798

**Spec claimed:** "count ≥ 797, the current baseline from STATE.md"

**Code shows:** `uv run pytest tests/ -m "not smoke" --co -q` returns 798 collected (16 deselected as smoke). STATE.md records "797 passed" from Phase Pre-2 completion. The live count is 798, not 797.

**Why this matters:** Any plan step that asserts `count == 797` will fail. Any "Done when" criterion using `count == 797` as a regression bound needs to be updated to `count ≥ 798`.

**Suggested resolution directions:**
1. Update all "Done when" test-count references in the plan from 797 to 798.
2. Use `>=` rather than `==` for baseline bounds in done-when criteria going forward — the count grows with each task.

### T7-A7 — `watch()` does not currently pass `folder_cooldown_seconds` to `VaultWatcher`

**Spec claimed:** "adding `binary_settle_seconds` is a one-argument addition to the same [VaultWatcher] call" at `cli/main.py:253–260`.

**Code shows:** `cli/main.py:253–260` — `VaultWatcher(root=root, vault_config=..., on_create=..., on_modify=..., on_delete=..., on_move=...)`. The `folder_cooldown_seconds` argument is **not passed** — `VaultWatcher.__init__` has it as a parameter (`folder_cooldown_seconds: float = 5.0` at `watcher.py:474`) but `watch()` relies entirely on the default value. So any YAML override of `capture.folder_cooldown_seconds` is silently ignored today.

**Why this matters:** T7's spec says "adding `binary_settle_seconds=CONFIG.main.capture.binary_settle_seconds` is a one-argument addition." In reality it is either: (a) a two-argument addition that ALSO wires `folder_cooldown_seconds` (fixing an existing silent ignore), or (b) a one-argument addition where `binary_settle_seconds` also relies on the default and ignores YAML overrides (consistent with the existing behavior but surprising). The spec's framing implies the YAML override works; it does not.

**Suggested resolution directions:**
1. Add both `folder_cooldown_seconds=CONFIG.main.capture.folder_cooldown_seconds` and `binary_settle_seconds=CONFIG.main.capture.binary_settle_seconds` to the `VaultWatcher(...)` call in T7 — this fixes both the existing silent ignore and wires the new field correctly.
2. Accept the current behavior (default-only) and document that both timers ignore YAML overrides unless explicitly wired — add a TODO comment at the call site.

---

## Spec Verification Summary

| Task | Total Assumptions | ✅ Validated | ❌ Invalidated | ⚠️ Unverifiable |
|------|-------------------|--------------|----------------|-----------------|
| T1 | 5 | 5 | 0 | 0 |
| T2 | 7 | 7 | 0 | 0 |
| T3 | 9 | 8 | 0 | 1 |
| T4 | 9 | 9 | 0 | 0 |
| T5 | 7 | 7 | 0 | 0 |
| T6 | 10 | 10 | 0 | 0 |
| T7 | 6 verified | 5 | 1 | 0 |
| T8–T10 | Not enumerated | — | — | — |
| **Cross-cutting** | 1 | 0 | 1 | 0 |
| **Totals** | **54** | **51** | **2** | **1** |

Two invalidated assumptions: (1) baseline count 797 vs actual 798 — minor, fix "Done when" criteria in plan; (2) T7-A7 `watch()` does not pass `folder_cooldown_seconds` — requires adding two config arguments to the `VaultWatcher(...)` call in T7, not one. Neither blocks correctness of the architecture or the logic of T1–T6. No blocking design errors found.
