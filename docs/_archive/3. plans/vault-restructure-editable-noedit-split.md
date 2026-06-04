# Plan: Vault Restructure — Editable vs No-Edit File Split
_Generated: 2026-06-04. Source spec: `docs/2. specs/vault-restructure-editable-noedit-split.md`. Research: `docs/2.5 research/vault-restructure-editable-noedit-split.md`._

---

## Reading guide

Each phase maps to one or more spec tasks (T1–T10). Phases are ordered by dependency. Steps within each phase must be done in order; phases themselves can only begin after their "Requires" phases are complete. "Done when" criteria are the acceptance tests — if they all pass, the phase is complete.

Test baseline before any work: `uv run pytest tests/ -m "not smoke" --co -q` must report **≥ 798 collected**. Never let a phase reduce that count.

---

## Phase 1 — Config foundation (T1)

**Spec tasks:** T1  
**Requires:** nothing  
**Can proceed in parallel with:** Phase 2 (T5 is independent of T1), but T1 must complete before Phases 3–8.

### What this phase does

Adds two pieces of data to `VaultConfig` that all later phases read:
1. A validated list of "no-edit" file extensions (the types the executive never opens and edits — PDFs and common image formats).
2. A computed property that groups the three AI-output folder names into one accessor so no consumer ever hard-codes `"Briefings"`, `"Synthesis"`, or `"Documentation"`.

No runtime behavior changes — this is config schema only.

### Steps

**Step 1.1 — Add `no_edit_extensions` Field to `VaultConfig`** (`src/core/config.py`, inside `VaultConfig` after the `summaries_subdir` Field at line 84)

Add a `list[str]` Field with `default_factory` returning `[".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]`. Add a `field_validator` (mode `"before"`) that lowercases every entry and asserts a leading dot — raises `ValueError` with a message naming the offending value if not. This validator fires at Pydantic parse time, covering both YAML-sourced values and unit-test construction.

Constraint: `no_edit_extensions` is human-configurable → `Field`, not `@property`. Never add a second `editable_extensions` list — "editable" is defined by absence.

**Step 1.2 — Add `no_edit_extensions:` key to `src/config/config.yaml`** (inside the `vault:` block, after `synthesis_dir:`)

Value: `[".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]` — identical to the Python default so removing the YAML key leaves behaviour unchanged.

**Step 1.3 — Add `ai_output_dirs` property to `VaultConfig`** (after the `briefings_path` property, currently the last `@property` on `VaultConfig`)

```python
@property
def ai_output_dirs(self) -> tuple[str, ...]:
    return (self.briefings_dir, self.synthesis_dir, self.documentation_dir)

@property
def ai_output_paths(self) -> tuple[Path, ...]:
    return (self.briefings_path, self.synthesis_path, self.documentation_path)
```

Both are live-computed (not cached), so overriding a `*_dir` Field is immediately reflected. Add both now — T4 will almost certainly need resolved `Path` objects.

**Step 1.4 — Add tests to `TestVaultConfig` in `tests/test_core/test_config.py`**

Add eight test methods inside the existing `TestVaultConfig` class (all construct `VaultConfig(root=tmp_path)` directly — no module-scope `CONFIG` import):
1. Default extension list contains exactly the six expected strings, all dot-prefixed lowercase.
2. Custom value round-trips.
3. Validator lowercases entries (`.PDF` → `.pdf`).
4. Validator rejects missing dot (`"pdf"` raises `ValidationError` with a message).
5. Absent YAML key uses Python default (construct with no `no_edit_extensions` kwarg).
6. `ai_output_dirs` returns `("Briefings", "Synthesis", "Documentation")` with defaults.
7. `ai_output_dirs` reflects an overridden `*_dir` Field.
8. `ai_output_paths` returns `(tmp_path / "Briefings", tmp_path / "Synthesis", tmp_path / "Documentation")`.

### Done when

- `uv run pytest tests/test_core/test_config.py -m "not smoke"` — all eight new tests pass and nothing regresses.
- `kms --help` (or `uv run python -c "from core.config import load_config"`) runs without a `ValidationError`.
- `VaultConfig(root=tmp_path, no_edit_extensions=[".PDF"]).no_edit_extensions` returns `[".pdf"]` (validator lowercased).
- `VaultConfig(root=tmp_path, no_edit_extensions=["pdf"])` raises `ValidationError` (missing dot).
- No `from core.config import CONFIG` at module scope in any new test code.
- Full suite: `uv run pytest tests/ -m "not smoke"` ≥ 798 collected, no regressions.

---

## Phase 2 — Folder-capture attachment/summaries exclusion (T5)

**Spec tasks:** T5  
**Requires:** nothing (reads only pre-existing `VaultConfig` fields: `attachment_dir`, `summaries_subdir`)  
**Can proceed in parallel with:** Phase 1

### What this phase does

The folder-drop walker (`_collect_folder_files`) currently picks up binaries already filed in `attachment/` and AI-written summary cards in `.summaries/`. Re-capturing those overwrites frontmatter and wipes the `attachment_path` pointer — data loss. This phase adds a config-sourced skip for both folder names, reading the names from `VaultConfig` rather than hardcoding them.

### Steps

**Step 2.1 — Extend `_collect_folder_files` signature and skip logic** (`src/pipelines/capture.py:1087`)

Change signature from `_collect_folder_files(folder_path: Path) -> list[Path]` to `_collect_folder_files(folder_path: Path, vault_cfg: VaultConfig) -> list[Path]`.

Before the rglob loop, compute: `skip_names = {vault_cfg.attachment_dir, vault_cfg.summaries_subdir}`

Extend the existing IGNORE_DIRS check (at capture.py:1101) from:
```python
if any(part in IGNORE_DIRS for part in rel_parts):
```
to:
```python
if any(part in IGNORE_DIRS or part in skip_names for part in rel_parts):
```

Research confirmed `VaultConfig` is NOT currently imported at module scope in `capture.py` — add the import. Check whether a module-scope import or lazy-import-inside-function matches the project pattern; lean toward module-scope (consistent with how other type hints work and avoids redundant lazy imports).

**Step 2.2 — Update both call sites in `capture_folder`**

- Call site 1 (`capture.py:1255`): research confirmed `vault_cfg` is NOT yet assigned at this point (it is assigned at line 1261). Move the `vault_cfg = ctx.config.vault` assignment to before line 1255, then change the call to `_collect_folder_files(folder_path, vault_cfg)`.
- Call site 2 (`capture.py:1306`): `vault_cfg` is already defined here. Change to `_collect_folder_files(new_folder, vault_cfg)`.

**Step 2.3 — Add tests in `tests/test_pipelines/`** (new class `TestCollectFolderFiles` — either in `test_capture_phase9.py` or new `test_collect_folder_files.py`)

Nine tests, all construct `VaultConfig(root=tmp_path)` directly, all call `_collect_folder_files(dropped_folder_path, vault_cfg)` directly (research confirmed there are no existing direct test references to this function):
1. Skip `attachment/` subfolder — `doc.docx` included, `attachment/report.pdf` excluded.
2. Skip `.summaries/` subfolder — `doc.docx` included, `.summaries/doc.docx.md` excluded.
3. Skip nested `attachment` at depth > 1.
4. Skip nested `.summaries/` under attachment.
5. Plain folder (no managed dirs) — all three files returned (regression guard).
6. Empty result when folder contains only `attachment/` contents.
7. Config-sourced names: custom `attachment_dir="binaries"` → files under `binaries/` skipped, files under `attachment/` (now the wrong name) NOT skipped.
8. Dotfiles still skipped.
9. `IGNORE_DIRS` members still skipped.

### Done when

- `uv run pytest tests/test_pipelines/ -m "not smoke"` — all nine new tests pass, nothing regresses.
- A folder containing only `attachment/report.pdf` produces `Success([])` (no capturable files → empty-guard fires before batch insert).
- A folder drop that previously wrote a `batches` row with the wrong `file_count` now shows the correct count (only non-managed files).
- No `from core.config import CONFIG` at module scope in new tests.
- Full suite: ≥ 798 collected, no regressions.

---

## Phase 3 — Placement helper (T2)

**Spec tasks:** T2  
**Requires:** Phase 1 (`VaultConfig.no_edit_extensions` must exist)

### What this phase does

Creates the single authoritative function that answers "where should this captured binary live?" — attachment folder for no-edit files (PDFs, images), visible project/domain root for editable office files. Returns a frozen dataclass (`Placement`) with three fields: `final_dir`, `sibling_dir`, `needs_move`. All later consumers (capture pipeline, watcher re-home, Phase 2 Classify) call this one function. If the routing rule ever changes, there is exactly one place to update.

### Steps

**Step 3.1 — Add `import dataclasses` and define `Placement` dataclass** (`src/vault/paths.py`, before the existing predicate functions)

```python
@dataclasses.dataclass(frozen=True)
class Placement:
    final_dir:   Path
    sibling_dir: Path
    needs_move:  bool
```

Research confirmed neither `Placement` nor `resolve_placement` exist in `src/` yet.

**Step 3.2 — Implement `resolve_placement` in `vault/paths.py`** (after the `Placement` dataclass, before `_is_in_managed_attachment`)

Signature: `resolve_placement(file_path: Path, target_type: str, target_name: str, vault_cfg: VaultConfig) -> Placement`

Logic (pure path arithmetic — no filesystem calls, no CONFIG import, no side effects):
1. Is the file no-edit? `file_path.suffix.lower() in vault_cfg.no_edit_extensions`
2. Is it already in managed attachment? Call existing `_is_in_managed_attachment(file_path, vault_cfg)`.
3. Compute `base_dir`: `vault_cfg.projects_path / target_name` if `target_type == "project"`, else `vault_cfg.domain_path / target_name`.
4. Compute `final_dir`: `base_dir / vault_cfg.attachment_dir` if no-edit, else `base_dir`.
5. Compute `sibling_dir = final_dir / vault_cfg.summaries_subdir`.
6. Compute `needs_move = (file_path.parent != final_dir)`.
7. Return `Placement(final_dir=final_dir, sibling_dir=sibling_dir, needs_move=needs_move)`.

Constraints: no `mkdir`, no `exists()`, no `open()`. Do not call the existing CONFIG-importing helpers (`project_attachment`, etc.) — they use `mkdir` and are not pure.

Add `resolve_placement` to `__all__` if `vault/paths.py` maintains one.

**Step 3.3 — Add unit tests** (new file `tests/test_vault/test_paths_placement.py` or new class in `tests/test_vault/test_paths.py`)

Ten tests, all construct `VaultConfig(root=tmp_path)` directly:
1. `Placement` is frozen — assigning a field after construction raises `FrozenInstanceError`.
2. No-edit file not in attachment → `needs_move=True`, `final_dir` ends with `attachment/`.
3. No-edit file already in attachment → `needs_move=False`, same `final_dir`.
4. Editable file not in attachment → `needs_move=False`, `final_dir` is project root.
5. Editable file in attachment → `needs_move=True`, `final_dir` is project root (moved OUT).
6. Domain symmetry — repeat the four routing cases with `target_type="domain"`.
7. Uppercase extension (`.PDF`) routed as no-edit (lowercase suffix check).
8. `sibling_dir == final_dir / vault_cfg.summaries_subdir` in every case.
9. No filesystem side effects — calling against a `tmp_path` with no directories creates no directories.
10. Custom `no_edit_extensions`: `[".xlsx"]` routes `.xlsx` to attachment, `.docx` to root.

### Done when

- `from vault.paths import Placement, resolve_placement` succeeds.
- `uv run pytest tests/test_vault/test_paths_placement.py -m "not smoke"` — all ten tests pass.
- No `from core.config import CONFIG` at module scope in new tests.
- Full suite: ≥ 798 collected, no regressions.

---

## Phase 4 — Capture pipeline rewire (T3)

**Spec tasks:** T3  
**Requires:** Phase 3 (T2 — `resolve_placement` must exist); Phase 1 (T1 — `no_edit_extensions` must exist on `VaultConfig`)

### What this phase does

Rewrites the LOCATED branch of `_store_nonmd` in the capture pipeline so that the routing decision comes from `resolve_placement` (T2) instead of the two hard-wired blocks that always sent everything to `attachment/`. The CLUELESS branch (inbox-parking for unlocated files) is not touched — every line in it stays byte-for-byte identical.

After this phase: a `.docx` captured into `Projects/Alpha/` stays visible at `Projects/Alpha/<name>.docx`; a `.pdf` still moves into `Projects/Alpha/attachment/<name>.pdf`.

### Steps

**Step 4.1 — Add `resolve_placement` import to `capture.py`** (module-scope, not lazy-inside-function)

Add to the existing module-scope imports: `from vault.paths import resolve_placement`. Research confirmed `VaultConfig` is NOT currently imported in `capture.py` — also add `from core.config import VaultConfig` (or a `TYPE_CHECKING` guard if the type is only used in annotations). This makes both patchable as `pipelines.capture.resolve_placement` and `pipelines.capture.VaultConfig` per TD-033.

**Step 4.2 — Remove the two deleted blocks and wire `resolve_placement`** (`src/pipelines/capture.py`, inside the LOCATED branch of `_store_nonmd`)

Operations to perform (in order):
1. Delete the four lazy imports at lines 550–555 (`project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries`) — research confirmed these are only used within the blocks being deleted.
2. Keep the `target_type` / `target_name` derivation block (lines 562–575) exactly as-is, except remove the `needs_move = ...` assignment within it (lines ~570, 575) — `needs_move` will come from `Placement`.
3. After the file-existence guard and rename-gate call (inside the LOCATED branch where `target_type is not None`), insert: `placement = resolve_placement(src, target_type, target_name, vault_cfg)`
4. Delete the dir-selection block (lines 593–598 — the `if target_type == "project": att_dir = ... else: att_dir = ...` block).
5. Replace every reference to `att_dir` with `placement.final_dir`, every `sum_dir` with `placement.sibling_dir`, and the local `needs_move` boolean with `placement.needs_move` throughout the rest of the LOCATED branch.

CLUELESS branch (lines 699–763): do not touch a single character.

**Step 4.3 — Enrich the LOCATED audit row `reasoning` string** (`src/pipelines/capture.py:673–690`)

After the `resolve_placement` call, derive a label: `_is_no_edit = src.suffix.lower() in vault_cfg.no_edit_extensions`

Change the `reasoning` argument to: `f"Routed to {target_type}/{target_name} ({'no-edit→attachment' if _is_no_edit else 'editable→root'})"`

**Step 4.4 — Add tests in `tests/test_pipelines/`** (new class `TestStoreNonmdLocatedBranch`)

Eleven tests, all construct `VaultConfig(root=tmp_path)` directly, patch `resolve_placement` as `pipelines.capture.resolve_placement` (not `vault.paths.resolve_placement` — TD-033), and patch `write_note`, `move_attachment`, `audit` as `pipelines.capture.*`:
1. Editable file not in attachment → `move_attachment` NOT called, sibling at `<project root>/.summaries/`.
2. Editable file in attachment → `move_attachment` called to project root.
3. No-edit not in attachment → `move_attachment` called to `attachment/`, sibling in `attachment/.summaries/`.
4. No-edit already in attachment → `move_attachment` NOT called.
5. Domain symmetry — repeat one case with a domain source path.
6. Collision loop uses `placement.final_dir` not hardwired `attachment/`.
7. Collision exhaustion (100 slots taken) → `Failure(recoverable=False)`.
8. Audit `reasoning` contains `editable→root` for editable file.
9. Audit `reasoning` contains `no-edit→attachment` for no-edit file.
10. Root-placement sibling has `type="attachment-summary"` in `NoteMetadata` (DECISION-029).
11. CLUELESS branch unaffected — file with no project/domain context routes to inbox-park, `resolve_placement` NOT called.

### Done when

- `uv run pytest tests/test_pipelines/ -m "not smoke"` — all eleven new tests pass.
- Manual verification: `.docx` captured into `Projects/Alpha/` lands at `Projects/Alpha/<name>.docx` (visible in Obsidian). `.pdf` captured into same folder lands at `Projects/Alpha/attachment/<name>.pdf`.
- A `.docx` captured while already at `Projects/Alpha/attachment/<name>.docx` is moved OUT to the project root.
- No `NameError` from removed imports; CLUELESS branch still routes inbox-park correctly.
- Audit log `reasoning` column contains the routing-class label.
- No `from core.config import CONFIG` at module scope in new tests.
- Full suite: ≥ 798 collected, no regressions.

---

## Phase 5 — AI-output exclusion and misplaced-md sweep (T4)

**Spec tasks:** T4  
**Requires:** Phase 1 (T1 — `VaultConfig.ai_output_dirs` must exist)  
**Note:** T4 is independent of T2 and T3. Can be built in parallel with Phases 3 and 4 after Phase 1 completes.

### What this phase does

Adds two named predicates to `vault/paths.py` and wires them into the watcher and batch scanner:
- `_is_ai_output(path, vault_cfg)`: True if any part of the path matches a folder the system writes to itself. Prevents the system from capturing its own briefings and causing an infinite feedback loop.
- `_is_misplaced(path, vault_cfg)`: True if a file is inside `Projects/` or `Domain/` but without a real subfolder (e.g., `Projects/stray.md` — dropped directly in the root, not inside a named project).

The misplaced-md sweep moves stale `.md` files from bare project/domain roots to `inbox/` before capture runs, so they are treated as normal inbox drops.

### Steps

**Step 5.1 — Add `_is_ai_output` predicate to `vault/paths.py`** (after `_is_managed_summaries_area`, before `_location_context`)

Signature: `_is_ai_output(path: Path, vault_cfg: VaultConfig) -> bool`

Logic: iterate `path.parts`; return True on the first part that is in `vault_cfg.ai_output_dirs`. Return False if none match. Document in the docstring that name-match is depth-agnostic (accepted limitation — a user folder coincidentally named `Briefings` inside a project would also match). No filesystem I/O, no CONFIG import.

**Step 5.2 — Add `_is_misplaced` predicate to `vault/paths.py`** (after `_is_ai_output`)

Signature: `_is_misplaced(path: Path, vault_cfg: VaultConfig) -> bool`

Logic:
1. If `vault_cfg.inbox_path in path.parents` or `path.parent == vault_cfg.inbox_path`: return False (inbox is a valid home).
2. If `_is_ai_output(path, vault_cfg)`: return False (AI-output folders are valid).
3. If `vault_cfg.projects_path in path.parents`: compute `rel = path.relative_to(vault_cfg.projects_path)`; return `len(rel.parts) < 2` (bare root drop).
4. If `vault_cfg.domain_path in path.parents`: same check for domain.
5. Return False (outside all managed locations).

Critical: do NOT call `_location_context` — it treats `Projects/<file>.md` as `("project", "<file>")`, creating a phantom project. Use the `len(rel.parts) >= 2` check directly.

**Step 5.3 — Wire `_is_ai_output` into `_should_skip` in `vault/watcher.py`**

1. Add `_is_ai_output` to the module-level import from `vault.paths` (alongside `_is_in_managed_attachment` at line ~39) — makes it patchable as `vault.watcher._is_ai_output` per TD-033.
2. Inside `_should_skip`, add after the `_is_in_managed_attachment` check:
   ```python
   if _is_ai_output(path, self._vault_config):
       _log.debug("watcher.skip.ai_output path=%s", path.name)
       return True
   ```
   Use `%s`-style formatting (stdlib logger rule).

**Step 5.4 — Wire AI-output dirname prune into `scan_non_md_drops` in `vault/indexer.py`**

Before the `root.walk()` loop, derive: `ai_output_dirs = vault_config.ai_output_dirs`

In the `dirnames[:]` list comprehension (lines ~124), add `and d not in ai_output_dirs` alongside the existing `d not in IGNORE_DIRS` clause. This prunes by dirname string — same accepted depth-agnostic limitation. Do NOT add names to `IGNORE_DIRS`.

**Step 5.5 — Wire AI-output dirname prune into `scan_vault` in `vault/indexer.py`**

Extend `scan_vault` signature to `scan_vault(root: Path | None = None, vault_cfg: VaultConfig | None = None)`. Inside the function, derive `ai_output_dirs` from `vault_cfg.ai_output_dirs` if provided, else lazy-import `CONFIG.main.vault.ai_output_dirs`. Apply the same `d not in ai_output_dirs` prune in the `dirnames[:]` comprehension.

Update the one call site in `scan_capture` at `capture.py:954` to pass `vault_cfg=CONFIG.main.vault`. Research confirmed this is the only call site.

**Step 5.6 — Add misplaced-md sweep to `scan_capture` in `pipelines/capture.py`**

Import `_is_misplaced` from `vault.paths` and confirm `move_note` is already imported at module scope (research confirmed `capture.py:31` has `from vault.writer import ... move_note ...`). Also add `from vault.move_guard import get_active` at module scope for T8 compatibility.

In the `summary.added` loop (lines ~964–980), before calling `capture_file(path, context=ctx)`, add:
- If `path.suffix.lower() == ".md"` AND `_is_misplaced(path, vault_cfg)`:
  - Compute `inbox_dst = vault_cfg.inbox_path / path.name` with collision handling (same `-1/-2` pattern as CLUELESS branch at capture.py:707-710).
  - Call `documents.delete_by_path(to_vault_path(path), db_path=_db_path)` to clean up any stale DB row.
  - Call `move_note(path, inbox_dst, actor="ai")`.
  - On `Failure` from `move_note`: log warning with `%s` formatting, skip — do not call `capture_file` on a file that failed to move.
  - Call `audit.write(AIDecision(action="capture:sweep", confidence=1.0, reasoning="Misplaced md swept to inbox", source_ids=[to_vault_path(path)]), pipeline="capture", stage="store", outcome="MISPLACED", db_path=_db_path)`.
  - Reassign `path = inbox_dst`.
- Add a `# NOTE:` comment on the `summary.modified` loop explaining the one-scan-lag edge case (misplaced `.md` that was previously indexed appears in `modified`, not `added`, until the file is re-dropped).

**Step 5.7 — Add tests for predicates** (new class `TestIsAiOutputAndMisplaced` in `tests/test_vault/test_paths.py`)

15 unit tests — 7 for `_is_ai_output`, 8 for `_is_misplaced`. All construct `VaultConfig(root=tmp_path)` directly; all use direct predicate calls (no filesystem I/O needed).

**Step 5.8 — Add tests for `_should_skip` AI-output extension** (in `tests/test_vault/test_watcher.py`)

6 tests covering: three AI-output folder names all return True, a valid project path returns False, a debug log `watcher.skip.ai_output` is emitted, and patching `vault.watcher._is_ai_output` (not `vault.paths._is_ai_output`) is used — the TD-033 guard test.

**Step 5.9 — Add tests for scanner AI-output prune** (in `tests/test_vault/test_indexer.py`)

8 tests using `tmp_path` vault trees with real files: 4 for `scan_non_md_drops`, 4 for `scan_vault`. Confirm AI-output paths are excluded and valid project/inbox paths are not regressed.

**Step 5.10 — Add integration tests for misplaced-md sweep** (new class `TestScanCaptureMisplacedMd` in `tests/test_pipelines/`)

7 integration tests. Patch all collaborators as `pipelines.capture.*` (TD-033). Cover: sweep to inbox, audit row written, stale DB row deleted, real project `.md` not swept, sweep failure logged not raised, `capture_file` called with inbox path, `scan_capture` returns `Success`.

### Done when

- A file at `Projects/stray.md` is moved to `inbox/stray.md` by `scan_capture` and the original path no longer exists.
- A file at `Projects/Alpha/note.md` is NOT swept (real project).
- A `FileCreatedEvent` for `Briefings/daily.md` does NOT fire `_on_create`.
- `scan_non_md_drops` and `scan_vault` do not return paths inside `Briefings/`, `Synthesis/`, or `Documentation/`.
- Audit row with `outcome="MISPLACED"` exists after sweep.
- No `from core.config import CONFIG` at module scope in any new test.
- Full suite: ≥ 798 collected, no regressions.

---

## Phase 6 — MoveGuard registry (T8)

**Spec tasks:** T8  
**Requires:** nothing (pure infrastructure module)  
**Must ship in same increment as:** Phase 7 (T6) — T8 provides the registry; T6 is the sole consumer

### What this phase does

Creates a small, thread-safe "sticky note" registry (`MoveGuard`) that lets the pipeline pre-announce a binary move destination before it happens. When the watcher's re-home branch fires (T6), it checks whether the destination was registered — if yes, it skips the re-home (this was a pipeline move, not a user drag). Research confirmed `src/vault/move_guard.py` does not yet exist.

### Steps

**Step 6.1 — Create `src/vault/move_guard.py`**

Module contents (all pure stdlib — no project imports at module scope to avoid circular imports):
- `_DEFAULT_TTL_SECONDS: float = 5.0` (module constant — not configurable for MVP; explicit name avoids buried literal).
- `_active: MoveGuard | None = None` (module-level variable).
- `MoveGuard` class:
  - Internal state: `dict[str, float]` mapping NFC-normalised path strings to expiry timestamps (monotonic clock) + `threading.Lock`.
  - `register(path: Path, ttl: float | None = None) -> None`: acquires lock, inserts/updates `normalize("NFC", str(path)) → time.monotonic() + (ttl or _DEFAULT_TTL_SECONDS)`, releases lock.
  - `check_and_consume(path: Path) -> bool`: acquires lock, drops expired entries lazily (iterate and remove), checks if NFC-normalised path is present, removes and returns True if found, False otherwise. Releases lock in all cases.
- `set_active(guard: MoveGuard) -> None`: stores guard in `_active`.
- `get_active() -> MoveGuard | None`: returns `_active`.

Imports needed: `threading`, `time`, `unicodedata`, `pathlib.Path`. No project imports.

**Step 6.2 — Wire `MoveGuard` into `VaultWatcher` and `_VaultEventHandler`** (`src/vault/watcher.py`)

1. Add `from vault.move_guard import MoveGuard` to module-level imports in `watcher.py`.
2. In `VaultWatcher.__init__`: add optional parameter `move_guard: MoveGuard | None = None`. If None, create a new `MoveGuard()`. Store as `self._move_guard`. Pass to `_VaultEventHandler`.
3. In `_VaultEventHandler.__init__`: add `move_guard: MoveGuard` parameter, store as `self._move_guard`.

Existing watcher tests can pass `MoveGuard()` explicitly or rely on the None-creates-new default on `VaultWatcher`.

**Step 6.3 — Wire `set_active` into `cli/main.py::watch()`**

After constructing `VaultWatcher` and before calling `watcher.start()`, add:
```python
from vault.move_guard import set_active as set_active_guard
set_active_guard(watcher._move_guard)
```
This publishes the guard so pipeline `get_active()` calls return the same instance.

**Step 6.4 — Add `get_active` import and registration at three pipeline sites in `capture.py`**

Import: `from vault.move_guard import get_active` at module scope in `capture.py` (patchable as `pipelines.capture.get_active` per TD-033).

At each of the three move call sites, insert the registration pattern immediately before the move:
```python
_g = get_active()
if _g:
    _g.register(<dst>)
```
- Site 1: `capture.py:~658` — `move_attachment(src, attachment_dst)` in the LOCATED branch (T3 rewired this; use `attachment_dst`).
- Site 2: `capture.py:~711` — `move_attachment(src, inbox_dst)` in the CLUELESS branch (use `inbox_dst`).
- Site 3: `capture.py:~1297` — `move_folder(folder_path, destination)` in `capture_folder` (use `destination`).

These are purely additive one-liners that fire only when `get_active()` is non-None. They do not change any Result return type or existing logic.

**Step 6.5 — Add unit tests for `MoveGuard`** (new file `tests/test_vault/test_move_guard.py`)

10 tests, all construct `MoveGuard()` directly — no `VaultConfig` or `CONFIG` needed:
1. Register then consume returns True.
2. Consume twice — second call returns False (consume-once).
3. Unregistered path returns False.
4. Expired entry returns False (mock `time.monotonic` to advance past TTL).
5. NFC/NFD normalisation — register with NFD path, consume with NFC equivalent, returns True.
6. Multiple independent paths — consuming one does not affect the other.
7. Cross-thread register-then-check (thread safety).
8. `get_active()` returns None before `set_active` is called.
9. `set_active` / `get_active` roundtrip — same object returned.
10. No `from core.config import CONFIG` in `move_guard.py`.

**Step 6.6 — Add integration tests for pipeline registration + watcher suppression** (new file `tests/test_vault/test_move_guard_integration.py`)

7 integration tests covering: pipeline registers before move, CLUELESS branch registers, no-active-guard case produces no error, watcher re-home skipped when guard fires, watcher re-home runs when guard is empty, guard checked against `dst` (not `final_binary`).

### Done when

- `from vault.move_guard import MoveGuard, set_active, get_active` succeeds.
- `guard.register(path)` followed by `guard.check_and_consume(path)` returns True; second `check_and_consume` returns False.
- After advancing past TTL: `check_and_consume` returns False.
- `get_active()` returns None before `set_active`; returns guard after.
- `uv run pytest tests/test_vault/test_move_guard.py -m "not smoke"` all pass.
- Existing watcher tests still pass (no new required constructor parameters break them).
- Full suite: ≥ 798 collected, no regressions.

---

## Phase 7 — Watcher re-home on user move (T6)

**Spec tasks:** T6 + T11 (correlation_id verify folds in)  
**Requires:** Phase 3 (T2 — `resolve_placement`), Phase 6 (T8 — `MoveGuard` wired into `_VaultEventHandler`)  
**Must ship in same increment as:** Phase 6 (T8)

### What this phase does

Rewrites the cross-folder else branch of `_handle_binary_move`. Currently, when a user drags a binary to a different project, the watcher orphans the old summary card and does nothing else. After this phase, it re-homes: moves the binary to the type-correct location (attachment for PDFs, visible root for .docx), writes the summary card at the new location using the DB-stored summary text (no LLM call), updates the database record, and audits. The same-folder rename branch (lines 357–419) is not touched.

### Steps

**Step 7.1 — Add missing imports to `vault/watcher.py` module scope**

Research confirmed the following are absent and must be added:
- `get_by_path` to the `storage.documents` import: `from storage.documents import delete_by_path, get_by_path, rename as rename_doc`
- `move_attachment` to the `vault.writer` import: `from vault.writer import move_attachment, move_note, write_note`
- New import line: `from vault.paths import _is_in_managed_attachment, _location_context, resolve_placement`

All imports are at module scope (patchable as `vault.watcher.*` per TD-033).

**Step 7.2 — Rewrite the cross-folder else branch of `_handle_binary_move`** (`src/vault/watcher.py:420–449`)

Replace the current orphan-and-do-nothing else block with the following logic. The `_vp()` local helper defined at lines 352–355 is preserved unchanged.

**Sub-step a — MoveGuard check** (first line of else block):
```python
if self._move_guard is not None and self._move_guard.check_and_consume(dst):
    _log.info("watcher.rehome_skip path=%s reason=pipeline_initiated", dst)
    return
```

**Sub-step b — Determine new location**:
Call `_location_context(dst, self._vault_config)` to get `(loc_type, loc_name)`. If `loc_type is None`: fall back to the existing orphan path (delete old sibling DB row, log warning, write `SIBLING_ORPHANED` audit row). Return.

**Sub-step c — Pre-check `updated_by_human` on old sibling BEFORE moving the binary** (ordering change from the design doc's Option B — spec recommends this stricter approach):
Compute `old_sibling = _sibling_for(src, self._vault_config)` and `old_sibling_vp = _vp(old_sibling)`. Call `get_by_path(old_sibling_vp)`. On `Failure` or `Success(None)`: log and fall back to orphan path. On `Success(value=row)`: if `row.updated_by_human` is True: log `watcher.binary_rehome_human_lock` and return — no binary move, no sibling move, no DB change.

**Sub-step d — Compute placement**:
Call `resolve_placement(dst, loc_type, loc_name, self._vault_config)` to get `placement`. Compute:
- `final_binary = placement.final_dir / dst.name`
- `new_sibling_path = placement.sibling_dir / f"{dst.name}.md"`
- `new_sibling_vp = _vp(new_sibling_path)`

**Sub-step e — Move binary if needed**:
If `placement.needs_move` and `final_binary != dst`: call `move_attachment(dst, final_binary)`. On `Failure`: log `watcher.binary_rehome_move_failed` and return (DECISION-025 broken-pointer posture).

**Sub-step f — Write new sibling card**:
If `old_sibling.exists()`:
1. Call `move_note(old_sibling, new_sibling_path, actor="ai")`. On `Failure`: log and return.
2. Call `read_note(new_sibling_path)`. On `Success(value=note)`: update `note.metadata.attachment_path = _vp(final_binary)` and call `write_note(new_sibling_path, note.content, note.metadata, actor="ai")`. On `Failure`: log and continue.

If `old_sibling.exists()` is False: rebuild from `row`. Construct `NoteMetadata` with `title=row.title`, `note_type=row.note_type or "attachment-summary"`, `type="attachment-summary"`, `confidence=row.confidence`, `attachment_path=_vp(final_binary)`, `content_hash=row.content_hash`, `updated_by_human=False`. Call `write_note(new_sibling_path, row.summary or "", rebuilt_meta, actor="ai")`. On `Failure`: log and return.

**Sub-step g — Update the database**:
Call `rename_doc(old_sibling_vp, new_sibling_vp)`. On `Success(value=0)`: log `watcher.binary_rehome_db_row_not_found`. On `Failure`: log warning.

**Sub-step h — Write audit row** (inherits the `correlation_id` bound at function top line 347 — do NOT add a second `bind_contextvars` call):
```python
audit_write(
    AIDecision(
        action="watcher:binary_rehome",
        confidence=1.0,
        reasoning=f"Re-homed {src.name} → {_vp(final_binary)} ({'no-edit→attachment' if dst.suffix.lower() in self._vault_config.no_edit_extensions else 'editable→root'})",
        source_ids=[new_sibling_vp],
    ),
    pipeline="watcher",
    stage="sync",
    outcome="REHOMED",
)
```

**Step 7.3 — Add unit tests** (new class `TestHandleBinaryMoveRehome` in `tests/test_vault/test_watcher_rehome.py`)

10 unit tests + 1 integration smoke test for T11. Patch all collaborators as `vault.watcher.*` (TD-033). Inject a mock `MoveGuard` via `handler._move_guard = mock_guard`. Tests:
1. No-edit PDF cross-folder re-home — binary moved to `attachment/`, sibling moved, DB renamed, audit written.
2. Editable .docx cross-folder re-home — binary NOT moved further (`needs_move=False`), sibling to root `.summaries/`, DB renamed, audit written.
3. MoveGuard suppression — no calls to `move_attachment`, `move_note`, or `audit_write`.
4. Fallback when DB row missing — orphan path fires, no re-home.
5. Fallback when unknown location — `resolve_placement` NOT called, orphan path.
6. `updated_by_human` lock aborts — `move_attachment` NOT called (pre-check fires before binary move).
7. Sibling rebuild from DB when on-disk sibling is absent — `write_note` called (not `move_note`), `rename_doc` called.
8. Audit row has non-null `correlation_id` (T11 verify — assert `structlog.contextvars.get_contextvars()` contains `correlation_id` at `audit_write` call time).
9. Domain symmetry — repeat no-edit and editable cases with `target_type="domain"`.
10. `_move_guard = None` does not raise `AttributeError`.
11. (Integration) T11 correlation_id: run `_handle_binary_move` cross-folder, capture the `AIDecision` passed to `audit_write` mock, assert `correlation_id` in contextvar is a non-empty string.

### Done when

- Given `Projects/A/report.pdf` with sibling `Projects/A/attachment/.summaries/report.pdf.md`, dragging to `Projects/B/` results in `Projects/B/attachment/report.pdf` and `Projects/B/attachment/.summaries/report.pdf.md` with the same summary text. Old sibling gone from disk and DB.
- Given `Projects/A/budget.xlsx` with sibling `Projects/A/.summaries/budget.xlsx.md`, dragging to `Projects/B/` results in `Projects/B/budget.xlsx` (visible root) and `Projects/B/.summaries/budget.xlsx.md`.
- Move to unrecognised folder → existing orphan path fires, no crash.
- Pipeline-initiated move (MoveGuard True) → no `REHOMED` audit row.
- `updated_by_human=True` sibling → binary NOT moved (early return).
- `correlation_id` in `REHOMED` audit row is non-null.
- No second `bind_contextvars` in the else branch.
- `uv run pytest tests/test_vault/test_watcher_rehome.py -m "not smoke"` — all tests pass.
- Full suite: ≥ 798 collected, no regressions.

---

## Phase 8 — Move-chain settle window (T7)

**Spec tasks:** T7  
**Requires:** Phase 7 (T6 re-home must exist), Phase 6 (T8 MoveGuard must exist)  
**Must ship in same increment as:** Phases 6 and 7

### What this phase does

Adds a filename-keyed "settle window" on top of the per-hop binary-move debounce. When a user drags a file to the wrong project and then quickly to the right one (`A → B → C`), the watcher would — after Phase 7 — fire a full re-home on every hop. This phase coalesces multi-hop chains: each new hop resets a timer; only when the file stops moving does the system re-home once, at the final location.

### Steps

**Step 8.1 — Add `binary_settle_seconds` Field to `CaptureConfig` and `config.yaml`**

In `src/core/config.py`, inside `CaptureConfig` (after `folder_cooldown_seconds` Field): `binary_settle_seconds: float = Field(5.0, ge=0.0)`.

In `src/config/config.yaml`, inside the `capture:` block: `binary_settle_seconds: 5.0`.

**Step 8.2 — Thread `binary_settle_seconds` into `_VaultEventHandler` via `VaultWatcher`**

In `VaultWatcher.__init__`: add `binary_settle_seconds: float = 5.0` parameter; store and pass to `_VaultEventHandler`.
In `_VaultEventHandler.__init__`: add `binary_settle_seconds: float = 5.0` parameter; store as `self._binary_settle_seconds`.
In `cli/main.py::watch()`: add TWO new kwargs to the `VaultWatcher(...)` call:
- `folder_cooldown_seconds=CONFIG.main.capture.folder_cooldown_seconds` (research confirmed this is currently silently using the default — wiring both fixes the silent-ignore for both timers)
- `binary_settle_seconds=CONFIG.main.capture.binary_settle_seconds`

**Step 8.3 — Add pending-binary-move registry to `_VaultEventHandler.__init__`**

After `_folder_lock` declaration, add (mirroring the folder-cooldown pattern exactly — read `watcher.py:182–249` before coding to confirm exact shape of `_pending_folders` / `_folder_tokens`):
```python
self._pending_binary_moves: dict[str, tuple[Path, Path]] = {}
self._binary_move_tokens: dict[str, int] = {}
self._binary_move_lock: threading.Lock = threading.Lock()
```
`_pending_binary_moves` stores `(origin_src, latest_dst)` per NFC-normalised filename key.

**Step 8.4 — Add three settle-window helper methods to `_VaultEventHandler`**

Placed after `_fire_folder_stable` (line ~249), structurally symmetric with `_register_pending_folder` / `_reset_folder_timer` / `_fire_folder_stable`:

- `_register_pending_binary_move(key, origin_src, dst)`: acquires `_binary_move_lock`, checks for existing entry. If no entry: store `(origin_src, dst)`, init token=0, arm timer → `_fire_binary_move_stable(key, origin_src, dst, token=0)`. If existing entry: keep stored `origin_src` (do NOT overwrite), update `latest_dst`, increment token, cancel old timer, arm new timer. Release lock. Start timer outside lock.

- `_reset_binary_move_timer(key, dst)`: convenience for re-entering an existing slot (may be inlined into `_register_pending_binary_move` — planner decides based on folder-cooldown pattern).

- `_fire_binary_move_stable(key, origin_src, final_dst, token)`: timer callback. Acquires lock. If stored token ≠ passed token: log `watcher.binary_settle_superseded`, release lock, return (stale fire). If match: remove key from both dicts, release lock. Outside lock: call `self._handle_binary_move(origin_src, final_dst)` directly — T6's re-home runs once, with the chain-origin `origin_src` so `_sibling_for(origin_src, cfg)` gives the correct original DB lookup key.

All log lines use `%s` formatting. Key is `unicodedata.normalize("NFC", dst.name)`.

**Step 8.5 — Intercept cross-folder re-home in `on_moved` to route through settle window**

Read `watcher.py:280–299` verbatim before coding (research must confirm whether `on_moved` routes to one `_debounce` call and `_handle_binary_move` branches internally, or whether `on_moved` itself detects `src.parent != dst.parent`). The conservative implementation: keep the existing `bin:{dst}` sibling-sync `_debounce` call exactly as-is (TD-030 ordering preserved). After it, add: if `src.parent != dst.parent` and `_is_binary(path)`: compute `key = unicodedata.normalize("NFC", dst.name)` and call `self._register_pending_binary_move(key, origin_src=src, dst=dst)`. The fire method (`_fire_binary_move_stable`) calls `_handle_binary_move` directly (bypassing the debounce) for the final re-home.

**Step 8.6 — Add tests** (new class `TestBinaryMoveSettleWindow` in `tests/test_vault/test_watcher_settle.py`)

11 tests. Construct `_VaultEventHandler` with `binary_settle_seconds=0.05` (50ms) for fast timers. Patch `vault.watcher._handle_binary_move` (TD-033). Use `threading.Event` for synchronisation (same pattern as existing folder-cooldown tests if any).

Cover: single-hop fires once; two-hop fires once at final dst; stale token is no-op; origin-src preserved across three hops; registry empty after stable fire; same-folder move does NOT engage settle; sibling-sync fires per-hop (not suppressed); end-to-end audit row count == 1 for chain; MoveGuard suppresses settled fire; zero-settle-seconds fires immediately; no module-scope CONFIG import.

### Done when

- `A → B → C` drag produces exactly one `REHOMED` audit row and one `write_note` call (at C). No intermediate card at B.
- Single-hop still re-homes correctly (settle latency only).
- Same-folder rename does NOT engage settle window.
- `bin:{dst}` sibling-sync fires per-hop (not coalesced by settle).
- `CaptureConfig(binary_settle_seconds=-1.0)` raises `ValidationError`.
- `uv run pytest tests/test_vault/test_watcher_settle.py -m "not smoke"` — all tests pass.
- Existing `test_watcher.py` tests still pass.
- Full suite: ≥ 798 collected, no regressions.

---

## Phase 9 — Binary content-change detection (T9)

**Spec tasks:** T9  
**Requires:** Phase 7 (T6 — sibling is in place to read `source_hash` from), Phase 4 (T3 — editable files are now in the project root where `on_modified` can see them)

### What this phase does

After Phase 4 puts editable files in the visible project root, users will open and edit them directly. Without this phase, saving in Word/Excel produces no update to the AI summary card. This phase adds five targeted changes to `_VaultEventHandler` that together form a closed loop: lock-file events are filtered → atomic-save burst is collapsed into one `chg:` debounce → when the burst settles, the binary's SHA-256 is compared against the stored hash — re-capture fires only if bytes changed. Also fixes a bug where `_handle_binary_delete` would orphan a live sibling during the mid-burst DELETE that Excel fires on every save.

### Steps

**Step 9.1 — Add `~$` Office lock-file filter to `_should_skip`** (`src/vault/watcher.py:134`)

Change `if path.name.startswith(".")` to `if path.name.startswith((".", "~$"))`. One-character addition. No other change to `_should_skip`.

**Step 9.2 — Make `on_modified` schedule `chg:` debounce for internal binaries** (`src/vault/watcher.py:257–259`)

Replace the early-return block (the `# Binary modify deferred — TD-C6` comment and the `return`) with:
```python
if path.suffix.lower() != ".md":
    if self._is_internal(path):
        self._debounce(f"chg:{path}", self._handle_binary_content_change, (path,))
    return
```
Remove the `# Binary modify deferred — TD-C6` comment entirely (TD-037 retired).

**Step 9.3 — Make `on_deleted` also reset `chg:` timer for internal binaries** (`src/vault/watcher.py:271–273`)

After the existing `self._debounce(f"bin:{path}", self._handle_binary_delete, (path,))` line, add:
`self._debounce(f"chg:{path}", self._handle_binary_content_change, (path,))`
The two keys are independent and do not cancel each other.

**Step 9.4 — Add `path.exists()` guard to `_handle_binary_delete`** (`src/vault/watcher.py:303`)

At the very start of the method, before the `import structlog` and `bind_contextvars` lines:
```python
if path.exists():
    return  # atomic-save mid-burst DELETE — file survived; chg: handler covers re-capture
```

**Step 9.5 — Add `import hashlib` and implement `_handle_binary_content_change`**

Add `import hashlib` to the module-level import block in `watcher.py` (not inside the method — research must confirm `hashlib` is absent).

Add private method `_handle_binary_content_change(self, path: Path) -> None` after `_handle_binary_delete` and before `_handle_binary_move`:
1. Bind correlation ID at top.
2. `if not path.exists(): return` — genuine delete; `bin:` handler covers orphan.
3. `sibling = _sibling_for(path, self._vault_config); if not sibling.exists(): return` — not yet indexed.
4. `read_note(sibling)` — on `Failure`: return.
5. If `note.metadata.source_hash` is set: compute `hashlib.sha256(path.read_bytes()).hexdigest()`. On `OSError`: return.
6. If hashes match: `_log.debug("watcher.binary_content_unchanged path=%s", path)` — return (no LLM call).
7. If hashes differ (or no stored hash): call `self._on_create(path)` — routes to `asyncio.run_coroutine_threadsafe(capture_file(...), loop)`. The `capture_file` idempotency guard stops a second redundant call if Word/PPT also fires a CREATE event.

All log lines use `%s` formatting. Vault-relative paths via `self._root`, not CONFIG.

**Step 9.6 — Add tests**

In `tests/test_vault/test_watcher.py` (or `test_watcher_content_change.py`):
- `~$` lock file is skipped by `_should_skip` (1 test).
- `on_modified` on binary schedules `chg:` debounce (1 test).
- `on_modified` on binary outside vault does NOT schedule `chg:` (1 test).
- `on_deleted` schedules both `bin:` and `chg:` for internal binary (1 test).
- `_handle_binary_delete` returns early if `path.exists()` (1 test).
- `_handle_binary_content_change`: file gone → return (1 test), sibling absent → return (1 test), hashes match → `_on_create` NOT called (1 test), hashes differ → `_on_create` called (1 test), `OSError` on read → return (1 test).

### Done when

- A `~$budget.xlsx` watcher event does NOT invoke any callback.
- Saving `budget.xlsx` (MODIFY event) eventually calls `_handle_binary_content_change`.
- Excel save sequence (MODIFY + DELETE + CREATE) does NOT orphan the sibling (no `watcher.binary_delete_sibling_removed` log for a save sequence).
- If binary bytes are unchanged (hash match): `_on_create` is NOT called.
- If binary bytes change: `_on_create` IS called, which triggers `capture_file` and a fresh summary.
- `uv run pytest tests/test_vault/ -m "not smoke"` — all new tests pass.
- Full suite: ≥ 798 collected, no regressions.

---

## Phase 10 — Reconcile migration and predicate extensions (T10)

**Spec tasks:** T10  
**Requires:** Phase 3 (T2 — `resolve_placement`) and Phase 4 (T3 — editable files now land in project root, creating root-level `.summaries/` siblings that T10 must learn to recognize)

### What this phase does

Two things:
1. Extends `_is_managed_summaries_area` in `vault/paths.py` to recognize root-level `.summaries/` directories (e.g., `Projects/<A>/.summaries/`) — currently it only knows about `attachment/.summaries/` and `inbox/.summaries/`. Without this, reconcile Stage 4 leaves stale root-level sibling cards alone indefinitely.
2. Adds a `reconcile_editable_migration` reconcile stage that moves any existing editable files from `attachment/` to the project root — fixing the vault state for files captured before Phase 4 shipped.

### Steps

**Step 10.1 — Extend `_is_managed_summaries_area` in `vault/paths.py`**

Read the current implementation before editing (it recognizes `.summaries/` under `attachment/` or `inbox/`). Extend to also return True for paths under `Projects/<A>/.summaries/` and `Domain/<D>/.summaries/` (a `.summaries/` directory that is a direct child of a project or domain subfolder — same depth-and-name pattern as the existing attachment case).

Research the exact current implementation to determine the correct extension (do NOT modify from memory).

**Step 10.2 — Add `reconcile_editable_migration` reconcile stage**

This stage:
1. Scans all `Projects/<A>/attachment/<file>` paths whose suffix is NOT in `vault_cfg.no_edit_extensions` (i.e., editable files that were captured before T3 shipped and ended up in the wrong place).
2. For each such file: calls `resolve_placement` to compute `Placement(final_dir=<project root>, needs_move=True)`, calls `move_attachment(src, final_binary)`, and updates the sibling's `attachment_path` frontmatter via `write_note`. Registers with `get_active()` before each `move_attachment` call (T8 SOFT dependency — allows watcher to coexist with reconcile).
3. Writes an audit row with `outcome="MIGRATED_EDITABLE"` for each file moved.

Wire this stage into `kms reconcile` as the final stage.

**Step 10.3 — Add tests**

- `_is_managed_summaries_area` now returns True for `Projects/Alpha/.summaries/file.pdf.md` (2 tests: project root, domain root).
- `_is_managed_summaries_area` still returns True for existing cases (1 regression test).
- `reconcile_editable_migration` moves editable files from attachment to root (1 integration test with tmp_path vault tree).
- Audit row with `outcome="MIGRATED_EDITABLE"` is written (1 test).

### Done when

- `_is_managed_summaries_area(tmp_path / "Projects" / "Alpha" / ".summaries" / "budget.xlsx.md", vault_cfg)` returns True.
- `kms reconcile` moves any editable file from `attachment/` to project root in a test vault.
- Stale root-level sibling cards (created by Phase 4/7 for editable files) are now within the scope of reconcile Stage 4 orphan cleanup.
- `uv run pytest tests/ -m "not smoke"` ≥ 798 collected, no regressions.

---

## Shipping order and increment grouping

| Increment | Phases | Can start | Blocking |
|---|---|---|---|
| 1 | Phase 1 (T1) | Immediately | Phases 3–8 wait for this |
| 2 | Phase 2 (T5) | Immediately (parallel to 1) | Nothing blocked |
| 3 | Phase 3 (T2) | After Inc 1 | Phases 4, 7 wait for this |
| 4 | Phase 4 (T3) | After Inc 3 | Phase 9 waits for this |
| 5 | Phase 5 (T4) | After Inc 1 (parallel to 3/4) | Nothing blocked |
| 6+7 | Phases 6+7 (T8+T6) | After Inc 3 | Phase 8 waits |
| 8 | Phase 8 (T7) | After Inc 6+7 | — |
| 9 | Phase 9 (T9) | After Inc 4 and 6+7 | — |
| 10 | Phase 10 (T10) | After Inc 3 and 4 | — |

Phases 6 and 7 (T8 and T6) must ship together in a single increment — T6 is the sole consumer of T8's `MoveGuard`.
Phase 8 (T7) must ship in the same increment as 6 and 7 (or immediately after, in the same PR).

---

## Open questions to resolve before coding each phase

**Before Phase 3 (T2) / Phase 4 (T3):** Confirm that `resolve_placement`'s `file_path.parent != final_dir` Path comparison does not need explicit NFC normalisation on macOS. The watcher normalizes paths via `unicodedata.normalize("NFC", ...)` before passing them onward — confirm that paths arriving at `_store_nonmd` via `mr.raw.source_path` follow the same convention. If not, add NFC normalisation to `resolve_placement` inputs.

**Before Phase 4 (T3):** Read the existing `_store_nonmd` tests in `tests/test_pipelines/` to confirm they use `pipelines.capture.*` patch targets (not `vault.writer.*`). If they use the wrong target, fix those tests first — otherwise new tests will be inconsistent with existing ones.

**Before Phase 8 (T7), Step 8.4:** Read `watcher.py:182–249` verbatim to confirm the exact registry shape of `_pending_folders` / `_folder_tokens` (is the timer stored in the dict or held by closure?). Mirror T7's registry shape exactly.

**Before Phase 8 (T7), Step 8.5:** Read `watcher.py:280–299` verbatim to confirm whether `on_moved` routes to one `_debounce` call (and `_handle_binary_move` branches internally) or whether `on_moved` already detects `src.parent != dst.parent`. The interception strategy differs.

**Before Phase 10 (T10):** Read `_is_managed_summaries_area` in `vault/paths.py` verbatim before editing — do not modify from memory.
