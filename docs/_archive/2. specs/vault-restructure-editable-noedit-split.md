---
# Vault Restructure: Editable vs No-Edit File Split — Detailed Specs
Status: Spec (writing-detailed-specs output). Input for /plan-from-specs.
Source design: docs/design/vault-restructure-editable-noedit-split.md
---

## T1 — Config: `no_edit_extensions` + capture-excluded folders

### Purpose

This task adds two pieces of tunable data to `VaultConfig` so that all later restructure tasks can read them from one place instead of hardcoding names or re-deriving logic independently.

First: a list of "no-edit" file extensions (PDFs, images) — the small, stable set of file types the executive never opens and edits. Any captured non-`.md` file whose extension is **not** on this list is treated as editable and routed to the visible project root (T2/T3). Any file whose extension **is** on this list is hidden in `attachment/` as before.

Second: a way to enumerate the three AI-output folder names (`Briefings/`, `Synthesis/`, `Documentation/`) as a single grouped accessor. These are the folders the system writes to itself; later tasks (T4) will exclude them from capture so the AI never re-ingests its own output.

After this task, T2's placement helper and T4's capture-exclusion predicate have stable, config-sourced inputs to read. Nothing in this task changes runtime behaviour — it is schema and defaults only.

---

### Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `VaultConfig` | `src/core/config.py:69–99` | Pydantic model holding vault root + all sub-folder name `Field`s + derived `@property` path helpers | The new `no_edit_extensions` Field and `ai_output_dirs` property are added to this exact model | Deep |
| `VaultConfig.briefings_dir` | `src/core/config.py:81` | `str` Field, default `"Briefings"` | Included in `ai_output_dirs` tuple | Shallow |
| `VaultConfig.synthesis_dir` | `src/core/config.py:80` | `str` Field, default `"Synthesis"` | Included in `ai_output_dirs` tuple | Shallow |
| `VaultConfig.documentation_dir` | `src/core/config.py:79` | `str` Field, default `"Documentation"` | Included in `ai_output_dirs` tuple | Shallow |
| `VaultConfig.attachment_dir` | `src/core/config.py:83` | `str` Field, default `"attachment"` | Existing precedent for config-sourced folder names that consumers read by name | Shallow |
| `VaultConfig.summaries_subdir` | `src/core/config.py:84` | `str` Field, default `".summaries"` | Nearest existing precedent: `scan_capture` already reads this name from config rather than hardcoding it | Shallow |
| `RenameGateConfig.office_extensions` | `src/core/config.py:224–226` | `list[str]` Field with `default_factory` lambda returning dot-prefixed lowercased strings | Closest existing pattern for an extension list on a config model; `no_edit_extensions` follows the same conventions (dot-prefixed, lowercase, `default_factory` lambda) | Shallow |
| `field_validator` (Pydantic v2) | `src/core/config.py:25, 107` | Pydantic decorator for per-field validation | Used as the pattern for the optional dot-prefix validator on `no_edit_extensions` | Shallow |
| `config/config.yaml` — `vault:` block | `src/config/config.yaml:69–77` | YAML source of truth for all `VaultConfig` fields | New `no_edit_extensions:` key added here; folder name keys already present | Shallow |
| `TestVaultConfig` | `tests/test_core/test_config.py:317–379` | Existing test class for `VaultConfig` | New tests for `no_edit_extensions` and `ai_output_dirs` are added in this same class, following its `VaultConfig(root=tmp_path)` pattern | Shallow |

---

### Feature overview

The task makes two additions to `VaultConfig`, then reflects the first addition in `config.yaml`.

**Addition 1 — `no_edit_extensions` Field.**
A `list[str]` Field on `VaultConfig` that ships with a default covering PDFs and common image formats: `[".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]`. Every string is lowercase and dot-prefixed, matching the convention used by `RenameGateConfig.office_extensions`. Pydantic validates the list at config load; an optional `field_validator` lowercases each entry and asserts a leading dot, so a misconfigured YAML value (e.g. `pdf` without a dot) fails fast at startup rather than silently mis-routing files later. This is the canonical definition of "no-edit" — "editable" is defined as the complement (no second list).

**Addition 2 — `ai_output_dirs` computed property.**
A `@property` on `VaultConfig` that returns a `tuple[str, ...]` of the three AI-output folder names: `(self.briefings_dir, self.synthesis_dir, self.documentation_dir)`. This is a computed view of data the model already owns — the three `*_dir` Fields. Because it reads those Fields directly, it can never drift from the YAML configuration. Consumers (T4) iterate this tuple; they do not re-type the three names. An optional companion `ai_output_paths` property returns the three resolved `Path` objects, mirroring the existing `briefings_path` / `synthesis_path` / `documentation_path` pattern.

**YAML change.**
The `vault:` block in `src/config/config.yaml` gains one new key: `no_edit_extensions: [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]`. The three AI-output folder name keys are already present.

**What this task does NOT do.** It does not add any logic that reads these new fields. Consuming logic is owned by T2 (placement helper reads `no_edit_extensions`) and T4 (capture-exclusion predicate reads `ai_output_dirs`). No pipeline, handler, watcher, or indexer is touched.

---

### Out of scope

- **Placement logic (editable vs no-edit routing)** — T2 owns the `resolve_placement` helper that reads `no_edit_extensions`. Deferred to T2.
- **AI-output capture exclusion predicates** — T4 owns `_is_ai_output(path, vault_cfg)`. Deferred to T4.
- **`_collect_folder_files` attachment/summaries skip** — T5 owns that fix. Deferred to T5.
- **Any change to `IGNORE_DIRS` in `vault/indexer.py`** — explicitly out of scope per the design decision; adding AI-output folder names to `IGNORE_DIRS` would blind FTS/search and break reconcile's orphan-sibling stage (DECISION-029).
- **`.heic`, `.tiff`, `.svg`, `.bmp` extensions** — left out of the default list for now; widening later is a one-line YAML edit. Narrowing risks surprising users, so the conservative set is shipped.
- **DB migration** — `no_edit_extensions` is config, not schema. No migration needed (C-05 does not apply here).
- **Any consuming pipeline, handler, watcher, or indexer change** — this task is schema + defaults only.

---

### Constraints

- **C-17 · No module-scope CONFIG import in tests** — source: `CONSTRAINTS.md`. Tests must construct `VaultConfig(root=tmp_path)` directly; no `from core.config import CONFIG` at module top.
- **`Field` vs `@property` rule (CLAUDE.md)** — `no_edit_extensions` is human-tunable → `Field`. The AI-output folder name bundle is code-computed from existing Fields → `@property`. Do not make `ai_output_dirs` a `Field` (that would duplicate the three `*_dir` values and create drift risk).
- **Extension-Point Rule (CLAUDE.md) / behavior is data not logic** — the editable/no-edit boundary must be a config list, not a hardcoded literal in consumer code. This task supplies the list; consumers must read it.
- **One canonical list (design anti-goal)** — ship only `no_edit_extensions`. "Editable" is defined by absence. Never add a second `editable_extensions` list.
- **C-06 / C-07 / C-13 / C-01 / C-02 / C-03 / C-05 / C-08** — none apply to this task (no thresholds, no prompts, no AI decisions, no vault writes, no schema change, no LLM calls). Flagged explicitly so downstream tasks know T1 did not and must not smuggle any of these in.
- **Downstream consumer obligation (T2)** — when T2 reads `no_edit_extensions`, it must use `path.suffix.lower()` before the membership test. The config stores lowercase dot-prefixed strings; a raw `.suffix` on a `.PDF` file would miss the match. T1 stores the canonical form; enforcing the lowercase compare is T2's obligation.

---

### Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|---|---|---|---|
| A1 | `VaultConfig` lives at `src/core/config.py:69–99` and has `briefings_dir`, `synthesis_dir`, `documentation_dir` as `str` Fields with defaults `"Briefings"`, `"Synthesis"`, `"Documentation"` | Design doc T1 Implications §"What the key terms mean" | If those Fields are missing, renamed, or moved to a different model, the `ai_output_dirs` property cannot compile |
| A2 | `config/config.yaml`'s `vault:` block already contains the three AI-output folder name keys and does NOT yet contain a `no_edit_extensions:` key | Design doc T1 Implications §"Files touched" | If `no_edit_extensions:` already exists in YAML, adding it again creates a duplicate key error at load |
| A3 | `RenameGateConfig.office_extensions` is a `list[str]` `Field` with a `default_factory` returning dot-prefixed lowercase strings, and Pydantic v2 `field_validator` is already imported in `config.py` | Design doc T1 §"What the key terms mean"; `src/core/config.py:25,224` | If the import or the Field pattern differs, the new validator syntax would need adjustment |
| A4 | The existing `TestVaultConfig` class in `tests/test_core/test_config.py` constructs `VaultConfig(root=tmp_path)` directly (no module-scope CONFIG import) | Design doc T1 §Guardrail Checklist; `tests/test_core/test_config.py:326–328` | If the test class uses a module-scope `CONFIG` import, C-17 is already violated and must be fixed before adding new tests here |
| A5 | Pydantic v2 validates `VaultConfig` fields at parse time (`load_config()` call), so a malformed `no_edit_extensions` value in YAML causes an immediate startup error — not a silent pass | Design doc T1 Implications §"Downstream effects" | If validation is deferred or `model_config = {"strict": False}` bypasses it, the fast-fail guarantee does not hold |

---

### Component dependency order

#### 1. Add `no_edit_extensions` Field to `VaultConfig`

**Goal.** Give the config model a validated, config-sourced list of no-edit file extensions so that all downstream placement logic reads from one authoritative place.

**Build.**

In `src/core/config.py`, inside `VaultConfig` (after the existing `summaries_subdir` Field), add:

```
no_edit_extensions: list[str] = Field(
    default_factory=lambda: [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]
)
```

Then add a `field_validator` on `"no_edit_extensions"` (mode `"before"`) that:
- Lowercases each entry.
- Asserts each entry starts with `"."`, raising a `ValueError` with a clear message if not (e.g. `'pdf' must start with a dot — use ".pdf"`) so a misconfigured YAML value fails at startup.

The validator fires at Pydantic parse time, covering both YAML-sourced values and programmatic construction in tests.

**Depends on.** None — this is the foundation component.

**Assumes.** A3 (field_validator import already present), A5 (Pydantic validates at parse time).

**Interface shape.**
- Field name: `no_edit_extensions`
- Type: `list[str]`
- Default: `[".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]`
- Consumers call: `vault_cfg.no_edit_extensions`
- Consumers do: `path.suffix.lower() in vault_cfg.no_edit_extensions`
- This is an in-process dependency; callers receive the validated list directly.

**Done when.**
- The app starts successfully with the default config (no `no_edit_extensions:` key in YAML) — the default list is used.
- If `no_edit_extensions: [pdf, png]` (no dots) is present in YAML, startup raises a `ValidationError` naming the offending value.
- `VaultConfig(root=tmp_path).no_edit_extensions` returns `[".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]` in a unit test that never imports the CONFIG singleton.
- `VaultConfig(root=tmp_path, no_edit_extensions=[".pdf", ".heic"]).no_edit_extensions` returns `[".pdf", ".heic"]` (custom value round-trips).
- `VaultConfig(root=tmp_path, no_edit_extensions=[".PDF"]).no_edit_extensions` returns `[".pdf"]` (validator lowercases).

---

#### 2. Add `no_edit_extensions:` key to `config/config.yaml`

**Goal.** Make the default extension list explicit and human-editable in the YAML config file so an operator can widen or narrow the set without touching code.

**Build.**

In `src/config/config.yaml`, inside the `vault:` block (after the existing `synthesis_dir:` key), add:

```yaml
no_edit_extensions: [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]
```

This mirrors the default in the `Field` above. The YAML value and the Python default must be identical so that removing the YAML key does not change runtime behaviour (stable default).

**Depends on.** Component 1 (the Field must exist before the YAML key is meaningful).

**Assumes.** A2 (key does not yet exist in YAML).

**Done when.**
- `kms --help` (or any CLI command) runs without a `ValidationError` after this YAML edit.
- Reading `CONFIG.main.vault.no_edit_extensions` in a smoke test (vault on disk) returns a list containing `".pdf"` and `".png"`.
- Changing the YAML value to `[".pdf"]` and restarting produces `no_edit_extensions == [".pdf"]` — YAML overrides the Python default.

---

#### 3. Add `ai_output_dirs` computed property to `VaultConfig`

**Goal.** Give every consumer a single, drift-proof way to ask "which folder names are AI-output?" without re-listing the three `*_dir` Fields themselves.

**Build.**

In `src/core/config.py`, inside `VaultConfig` (after the last `@property` path helper, currently `briefings_path`), add:

```
@property
def ai_output_dirs(self) -> tuple[str, ...]:
    return (self.briefings_dir, self.synthesis_dir, self.documentation_dir)
```

Optionally add a companion:

```
@property
def ai_output_paths(self) -> tuple[Path, ...]:
    return (self.briefings_path, self.synthesis_path, self.documentation_path)
```

The `ai_output_paths` property mirrors the pattern of the three individual `*_path` properties already on the model and is the form T4 may prefer when it builds predicates against real `Path` objects. Whether to add both or just `ai_output_dirs` is a judgment call for the planner (see Decisions below).

**Depends on.** Component 1 (the `no_edit_extensions` Field must exist, and the three `*_dir` Fields are confirmed present — see A1). This component has no hard dep on Component 2 (YAML) but should land in the same commit for consistency.

**Assumes.** A1 (the three `*_dir` Fields exist on `VaultConfig`).

**Interface shape.**
- Property name: `ai_output_dirs`
- Return type: `tuple[str, ...]`
- Value: `(briefings_dir, synthesis_dir, documentation_dir)` — always exactly these three, in this order
- Consumers iterate: `for name in vault_cfg.ai_output_dirs: ...`
- Companion `ai_output_paths` property (optional): `tuple[Path, ...]` of the three resolved `Path` objects
- In-process dependency; no adapter needed.

**Decisions.**
- Q: Add `ai_output_paths` alongside `ai_output_dirs` now, or only when T4 needs it? Options: add both now (one extra line, avoids T4 touching `config.py`) / add `ai_output_dirs` only (YAGNI until T4). Leaning add both because T4 will almost certainly need resolved `Path` objects for predicate comparisons, and the cost is one `@property` line now vs a config.py edit inside T4's diff later.

**Done when.**
- `VaultConfig(root=tmp_path).ai_output_dirs` returns `("Briefings", "Synthesis", "Documentation")` in a unit test that never imports `CONFIG`.
- Overriding one dir: `VaultConfig(root=tmp_path, briefings_dir="Reports").ai_output_dirs` returns `("Reports", "Synthesis", "Documentation")` — the property is live, not cached.
- `len(vault_cfg.ai_output_dirs) == 3` — exactly three entries, no more, no less.
- (If `ai_output_paths` is added): `vault_cfg.ai_output_paths` returns `(tmp_path / "Briefings", tmp_path / "Synthesis", tmp_path / "Documentation")`.

---

#### 4. Add tests for the new Field and property

**Goal.** Confirm that the new additions work correctly, that the C-17 (no module-scope CONFIG import) rule is honoured, and that the default list is canonical when the YAML key is absent.

**Build.**

Inside `tests/test_core/test_config.py`, in the existing `TestVaultConfig` class, add the following test cases (all construct `VaultConfig(root=tmp_path)` directly — no module-scope `CONFIG` import):

1. `test_no_edit_extensions_default_is_pdf_and_images` — asserts the six default extensions are present and all are dot-prefixed lowercase strings.
2. `test_no_edit_extensions_custom_value_round_trips` — overrides the list, reads it back, asserts equality.
3. `test_no_edit_extensions_validator_lowercases_entries` — passes `[".PDF", ".PNG"]`, asserts stored values are `[".pdf", ".png"]`.
4. `test_no_edit_extensions_validator_rejects_missing_dot` — passes `["pdf"]`, asserts `ValidationError` is raised with a message referencing the missing dot.
5. `test_no_edit_extensions_absent_from_yaml_uses_default` — constructs `VaultConfig` without the key (i.e. `VaultConfig(root=tmp_path)` with no `no_edit_extensions` kwarg), asserts the default list is used (proves the Field default is canonical, not YAML-dependent).
6. `test_ai_output_dirs_returns_three_dir_names` — asserts `ai_output_dirs == ("Briefings", "Synthesis", "Documentation")` with defaults.
7. `test_ai_output_dirs_reflects_overridden_dir_name` — overrides one `*_dir` Field, asserts `ai_output_dirs` reflects the override (proves the property is live, not a cached literal).
8. (If `ai_output_paths` added) `test_ai_output_paths_returns_resolved_paths` — asserts the tuple contains `tmp_path / "Briefings"` etc.

**Depends on.** Components 1 and 3 (the Field and the property must exist for the tests to compile).

**Assumes.** A4 (existing `TestVaultConfig` does not use module-scope `CONFIG`).

**Done when.**
- All eight tests pass under `uv run pytest tests/test_core/test_config.py -m "not smoke"`.
- No module-scope `from core.config import CONFIG` appears in any new test.
- The full suite (`uv run pytest tests/`) still passes with no regressions (count ≥ 798, the current baseline from STATE.md).

---

### Handoff notes

- **Contract with T2:** T2's `resolve_placement` helper will call `vault_cfg.no_edit_extensions` and compare `path.suffix.lower()` against it. The T1 spec guarantees the Field exists, is validated (dot-prefixed, lowercased), and has a stable default. T2 must use `.lower()` on the candidate suffix before membership-testing — this is a T2 obligation, not enforced by T1.
- **Contract with T4:** T4's `_is_ai_output(path, vault_cfg)` predicate will iterate `vault_cfg.ai_output_dirs` (and/or `ai_output_paths`). T1 guarantees the property exists and returns exactly the three AI-output folder names in a stable tuple. T4 must not hard-code `"Briefings"` etc. anywhere — always read from the tuple.
- **Contract with T5:** T5's `_collect_folder_files` already reads `vault_cfg.attachment_dir` and `vault_cfg.summaries_subdir` from config. T1 adds no new obligation for T5; the two folder names T5 needs already exist on `VaultConfig`.
- **`IGNORE_DIRS` must not be touched:** Any attempt to add `"Briefings"`, `"Synthesis"`, or `"Documentation"` to `vault/indexer.py::IGNORE_DIRS` would blind FTS/search and break reconcile's orphan-sibling stage (DECISION-029). The capture-exclusion logic lives in T4's predicates in `vault/paths.py`, not in the indexer's global frozenset.
- **Open uncertainty:** The design recommends adding the optional dot-prefix `field_validator`. If the planner decides to skip the validator (to reduce scope), the "fail-fast on missing dot" done-when criterion becomes a developer-verify item only, not a runtime guarantee. The spec records both paths; the planner chooses.
- **Suggested research for /research:** Verify that `VaultConfig` at `src/core/config.py:69–99` has no existing `no_edit_extensions` field or `ai_output_dirs` property already added by a previous branch; verify that `config/config.yaml` `vault:` block does not yet contain `no_edit_extensions:`. Both are quick grep checks that ground the A1/A2 assumptions before planning.

---

## T2 — Single shared placement helper

### Purpose

This task creates the single, authoritative function that answers one question: "given a captured non-`.md` file and its resolved project or domain home, where should it physically live?" It produces a small data object — a `Placement` — that names the binary's destination directory, the `.summaries/` directory its sibling should live in, and a flag indicating whether the binary needs to be moved at all. Every consumer (the capture pipeline today, the Phase 2 Classify pipeline later) calls this function. None of them re-derive the rule. If the rule ever changes — say, a new no-edit extension is added — there is exactly one place to update.

After this task, the codebase has a single, tested, pure-function source of truth for the editable-vs-no-edit placement rule. The capture pipeline (T3) will be rewired to call it. The watcher re-home logic (T6) will call it. Phase 2 Classify will call it. None of those callers can silently disagree with each other.

---

### Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `VaultConfig` | `src/core/config.py:69–99` | Pydantic model for vault root + all sub-folder name Fields + derived path `@property` helpers | The new `resolve_placement` function takes a `VaultConfig` instance explicitly and reads `no_edit_extensions`, `attachment_dir`, `summaries_subdir`, `projects_path`, `domain_path` from it | Deep |
| `VaultConfig.no_edit_extensions` | `src/core/config.py` (added by T1) | `list[str]` Field; the canonical set of dot-prefixed lowercase extensions treated as no-edit (pdf, images) | T2's placement rule reads this list: `file_path.suffix.lower() in vault_cfg.no_edit_extensions` | Shallow |
| `VaultConfig.attachment_dir` | `src/core/config.py:83` | `str` Field, default `"attachment"` | Used to build the `attachment/` subdirectory path for no-edit files | Shallow |
| `VaultConfig.summaries_subdir` | `src/core/config.py:84` | `str` Field, default `".summaries"` | Used to build the sibling directory path as `final_dir / summaries_subdir` | Shallow |
| `VaultConfig.projects_path` | `src/core/config.py:91` | `@property` returning `root / projects_dir` | Used to build the project root dir (`projects_path / target_name`) for editable-file placement | Shallow |
| `VaultConfig.domain_path` | `src/core/config.py:93` | `@property` returning `root / domain_dir` | Used to build the domain root dir (`domain_path / target_name`) for editable-file placement | Shallow |
| `_is_in_managed_attachment` | `src/vault/paths.py:26` | Pure predicate: True if a path lives inside `Projects/<A>/attachment/` or `Domain/<D>/attachment/` | Called by `resolve_placement` to determine whether the binary is already in the attachment subtree | Shallow |
| `_location_context` | `src/vault/paths.py:87` | Pure function returning `(type, name)` for a path — `("project", "Alpha")`, `("domain", "Finance")`, `("inbox", None)`, `(None, None)` | NOT called by `resolve_placement` directly — `resolve_placement` accepts `target_type` and `target_name` as already-resolved inputs from the caller. Listed here to clarify that T2 does not re-implement location detection. | Shallow |
| `_sibling_for` | `src/vault/watcher.py:54–73` | Returns `<binary.parent>/<summaries_subdir>/<binary.name>.md` | T2 does NOT call this function (it anchors to the binary's *current* parent, not the *final* parent). T2's `sibling_dir` is `final_dir / vault_cfg.summaries_subdir`, consistent with the same naming rule. Callers compute the full sibling path as `sibling_dir / f"{attachment_dst.name}.md"`. | Shallow |
| `TestVaultConfig` | `tests/test_core/test_config.py` | Existing test class constructing `VaultConfig(root=tmp_path)` directly | New tests for `resolve_placement` construct `VaultConfig(root=tmp_path)` in the same style — no module-scope CONFIG import | Shallow |

---

### Feature overview

The placement helper is pure path arithmetic. It takes four inputs — the source file path, the resolved location type (`"project"` or `"domain"`), the location name (e.g. `"Alpha"` or `"Finance"`), and the `VaultConfig` — and applies two rules deterministically:

**Rule 1 — Is the file no-edit or editable?**
Lowercase the file's extension and check if it is in `vault_cfg.no_edit_extensions`. If yes, it belongs in the `attachment/` subdirectory (hidden from Obsidian). If no, it belongs in the project or domain root (visible in Obsidian).

**Rule 2 — Is the file already in the right place?**
Check whether the file's current parent directory is the same as the computed `final_dir`. If yes, `needs_move` is False (no filesystem operation needed). If no, `needs_move` is True.

The helper returns a `Placement` dataclass with three fields:
- `final_dir` — the directory where the binary should end up
- `sibling_dir` — the `.summaries/` directory where the AI summary card should live (always `final_dir / vault_cfg.summaries_subdir`)
- `needs_move` — whether the binary must be moved from its current location to `final_dir`

The helper performs no filesystem operations. It does not create directories, read file contents, call the LLM, or run the collision loop. Those are the caller's responsibilities (T3 for capture, T6 for watcher re-home). The helper's only job is pure destination arithmetic.

Four cases are covered:

| File class | Current location | `needs_move` | `final_dir` |
|---|---|---|---|
| No-edit (pdf/image) | NOT in `attachment/` | True | `<type>/<name>/attachment/` |
| No-edit (pdf/image) | Already in `attachment/` | False | `<type>/<name>/attachment/` |
| Editable (docx/xlsx/…) | NOT in `attachment/` | False | `<type>/<name>/` (root) |
| Editable (docx/xlsx/…) | IN `attachment/` | True | `<type>/<name>/` (root) |

In all four cases, `sibling_dir = final_dir / vault_cfg.summaries_subdir`.

---

### Out of scope

- **Rename-gate stem computation** — the sanitized filename stem (`decide_rename`) is T3's responsibility. T2 returns a directory, not a full file path.
- **Collision loop** — iterating `-1/-2/…` to find an unused filename is T3's responsibility. T2 has no concept of existing files.
- **`mkdir` / directory creation** — T2 does not create directories. Callers do that before writing.
- **The CLUELESS / inbox path** — files with no resolved `target_type`/`target_name` do not go through `resolve_placement`. The inbox-parking path is in T3/T4, not T2.
- **AI-output exclusion (`_is_ai_output`)** — T4 owns that predicate.
- **Misplaced-file sweep** — T4 owns that behavior.
- **Root-`.summaries/` recognition in reconcile predicates** — T10 must update `_is_managed_summaries_area` to recognize root-level `.summaries/` (the case when editable files land in the project root). T2 only *writes* siblings there; T10 teaches reconcile to find them. This is a [REQUIRES: T10] forward dependency.
- **Phase 2 Classify wiring** — Phase 2 is the second caller of `resolve_placement`, but it is not built in this task. The seam this task creates will be consumed by Phase 2 without any modification.
- **`_sibling_for` in `vault/watcher.py`** — not modified. T2 uses the same naming convention (`<binary.name>.md`) but computes the sibling directory from `final_dir`, not from the binary's current parent. The two are consistent; `_sibling_for` is not replaced.

---

### Constraints

- **Config-as-data (CLAUDE.md Extension-Point Rule)** — `no_edit_extensions` must be read from `vault_cfg`, never hardcoded as string literals in the helper. Source: CLAUDE.md "New threshold or rule → edit a config file."
- **C-12 exempt for `vault/paths.py` functions** — `resolve_placement` lives in `vault/paths.py`, which follows the local convention of returning plain values (Path, bool, dataclass), not `Result`. Every existing function in that module (`_is_in_managed_attachment`, `project_attachment`, etc.) returns a plain value. There is no recoverable failure mode in pure path arithmetic — bad input is a programmer error. Source: CONSTRAINTS.md C-12 (scoped to `handlers/` and `pipelines/`); design doc §Guardrail Checklist.
- **C-17 · No module-scope CONFIG import in tests** — `resolve_placement` takes `vault_cfg` explicitly so tests can construct `VaultConfig(root=tmp_path)` without importing the CONFIG singleton. The function itself must NOT import CONFIG at module scope. Source: CONSTRAINTS.md C-17.
- **Sibling naming rule (CLAUDE.md gotcha)** — sibling filename is `<binary.name>.md` (full filename including extension), NOT `<stem>.md`. E.g. `report.pdf` → `.summaries/report.pdf.md`. T2 returns `sibling_dir` only; the caller forms the full sibling path. The caller must follow this naming rule. Source: DECISION-028; CLAUDE.md "What Claude gets wrong."
- **DECISION-029 (type guard on `.summaries/` writes)** — T2 only computes the sibling directory path. Setting `type=attachment-summary` in the sibling's frontmatter is T3's obligation when calling `write_note`. T2 does not write and does not enforce this — but the spec records it here so the obligation is not lost.
- **No filesystem I/O in the helper** — T2 must not call `exists()`, `mkdir()`, `read_bytes()`, or any other filesystem operation. It is a total, pure function. Source: design doc Anti-goal "No LLM in this helper — pure path math"; design doc §Options "Option A" rationale.
- **TD-033 (monkeypatch the importing module)** — tests that stub out `_is_in_managed_attachment` when testing `resolve_placement` must patch `vault.paths._is_in_managed_attachment`, not `vault.paths._is_in_managed_attachment` via another module. Since both are in the same file this is straightforward, but any test for a *consumer* (T3's `_store_nonmd`) that patches `resolve_placement` must patch `pipelines.capture.resolve_placement`, not `vault.paths.resolve_placement`. Source: CLAUDE.md TD-033.
- **[REQUIRES: T1]** — `VaultConfig.no_edit_extensions` must exist before `resolve_placement` compiles. T1 must land before T2.

---

### Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|---|---|---|
| A1 | `_is_in_managed_attachment(path, vault_cfg)` exists in `src/vault/paths.py` and is importable from there; it returns `True` only for paths under `Projects/<A>/attachment/` or `Domain/<D>/attachment/` | Design doc T2 Implications §"What the key terms mean"; T1 spec already confirmed this from the code read | If the function is missing, renamed, or moved to a different module, `resolve_placement` will have an import error at build time |
| A2 | `VaultConfig.no_edit_extensions` (added by T1) is a `list[str]` field whose values are lowercase and dot-prefixed (e.g. `".pdf"`), validated at Pydantic parse time | T1 spec §Component 1 "Interface shape"; design doc T1 Implications §"What the key terms mean" | If the field is absent or stores un-dotted strings like `"pdf"`, the suffix membership test `path.suffix.lower() in vault_cfg.no_edit_extensions` will silently mis-route all files |
| A3 | `VaultConfig.projects_path` and `VaultConfig.domain_path` are `@property` accessors returning `Path` objects (`root / projects_dir` and `root / domain_dir` respectively) and exist on the current `VaultConfig` model at `src/core/config.py:91,93` | Design doc T2 Implications §"Runtime deps"; confirmed by direct code read | If either property is renamed or removed, the `final_dir` computation will fail with `AttributeError` |
| A4 | `VaultConfig.attachment_dir` is a `str` Field (default `"attachment"`) and `VaultConfig.summaries_subdir` is a `str` Field (default `".summaries"`), both on the current model | Design doc T2 Implications §"Runtime deps"; confirmed by direct code read of `src/core/config.py:83–84` | If either Field is renamed, the `final_dir / vault_cfg.attachment_dir` and `sibling_dir` path computations will produce wrong paths |
| A5 | The existing path helpers in `vault/paths.py` (`project_attachment`, `domain_attachment`, `project_summaries`, `domain_summaries`) call `CONFIG.main.vault.*` internally and use `mkdir`; `resolve_placement` must NOT call these helpers because they are not pure and they import CONFIG at the lazy-call site | Design doc T2 Implications §"Files touched" and §"What the helper is and is NOT"; confirmed by direct code read of `paths.py:207–246` | If this assumption is wrong (e.g. those helpers are refactored to accept `vault_cfg` explicitly), T2 could potentially delegate to them — but as of now they must be avoided |
| A6 | No `Placement` dataclass and no `resolve_placement` function exist anywhere in `src/` yet | Confirmed by grep of the codebase (no matches) | If either already exists from a prior branch, the spec would create a duplicate; grep would catch this during research |
| A7 | The caller (`_store_nonmd` in `capture.py`) already has `target_type` and `target_name` computed before it reaches the destination-resolution block (lines 562–575 of `capture.py`) — T2 receives these as inputs, it does not re-derive them | Design doc T2 Implications §"What the helper is and is NOT"; confirmed by direct code read of `capture.py:562–575` | If `target_type`/`target_name` are not available at the call site, the T3 rewiring would need to recompute them before calling `resolve_placement` |

---

### Component dependency order

#### 1. Define the `Placement` dataclass in `vault/paths.py`

**Goal.** Give callers a stable, named return type for the placement helper so that consuming code refers to `placement.final_dir`, `placement.sibling_dir`, and `placement.needs_move` by name rather than by positional tuple index.

**Build.**

In `src/vault/paths.py`, before the existing predicate functions, add a frozen dataclass:

```
@dataclasses.dataclass(frozen=True)
class Placement:
    final_dir:   Path   # directory where the binary should live
    sibling_dir: Path   # .summaries/ directory for the AI card
    needs_move:  bool   # True if binary must be moved from its current location
```

The class is frozen (immutable) because placement values are computed once per capture event and never mutated. Add `import dataclasses` to the file's imports (currently `vault/paths.py` does not import `dataclasses`).

**Depends on.** None — this is a pure data definition with no runtime dependencies.

**Assumes.** None.

**Interface shape.**
- Callers: `from vault.paths import Placement` (or receive it as a return value from `resolve_placement`).
- Fields accessed by name, not by index.
- Immutable — no setters.
- Dependency category: in-process (test directly, no stub needed).

**Done when.**
- `from vault.paths import Placement` succeeds in a Python REPL pointed at the project.
- `Placement(final_dir=Path("/a"), sibling_dir=Path("/a/.summaries"), needs_move=True)` constructs without error.
- Attempting to assign `p.needs_move = False` after construction raises `FrozenInstanceError`.
- No regressions in the existing test suite (`uv run pytest tests/ -m "not smoke"`).

---

#### 2. Implement `resolve_placement` in `vault/paths.py`

**Goal.** Provide the single, authoritative pure function that applies the editable-vs-no-edit routing rule and returns where a captured binary and its summary card should live.

**Build.**

In `src/vault/paths.py`, after the `Placement` dataclass and before `_is_in_managed_attachment`, add the function `resolve_placement`:

Signature:
```
def resolve_placement(
    file_path: Path,
    target_type: str,
    target_name: str,
    vault_cfg: VaultConfig,
) -> Placement:
```

Logic (in plain English, no code):

1. Determine whether the file is "no-edit" by checking if `file_path.suffix.lower()` is a member of `vault_cfg.no_edit_extensions`.

2. Determine whether the file is already inside a managed attachment subtree by calling the existing `_is_in_managed_attachment(file_path, vault_cfg)`.

3. Compute `base_dir`: the project or domain root directory.
   - If `target_type == "project"`: `base_dir = vault_cfg.projects_path / target_name`
   - If `target_type == "domain"`: `base_dir = vault_cfg.domain_path / target_name`

4. Compute `final_dir`:
   - If the file is no-edit: `final_dir = base_dir / vault_cfg.attachment_dir`
   - If the file is editable: `final_dir = base_dir`

5. Compute `sibling_dir = final_dir / vault_cfg.summaries_subdir`

6. Compute `needs_move = (file_path.parent != final_dir)`
   - Note: this comparison is between `Path` objects; both must be absolute and un-normalised in the same way. The existing code uses `Path` objects from `VaultConfig` which are absolute, so this comparison is safe.

7. Return `Placement(final_dir=final_dir, sibling_dir=sibling_dir, needs_move=needs_move)`.

The function is total — every possible combination of inputs yields a valid `Placement`. There are no early returns, no exceptions, and no `None` values in the output. If `target_type` is neither `"project"` nor `"domain"`, the `base_dir` computation would produce a path that is technically wrong — but this case should never occur (callers only call `resolve_placement` when `target_type is not None`). The function does not guard against it with an exception; if the caller passes an invalid type, the resulting `Placement` will be incorrect and the caller's own logic will surface the error.

The function must perform no filesystem operations. No `exists()`, no `mkdir()`, no `open()`, no `stat()`. It is pure path arithmetic over `Path` objects.

**Depends on.** Component 1 (`Placement` dataclass must be defined first).

**Assumes.** A1 (`_is_in_managed_attachment` exists), A2 (`no_edit_extensions` is a validated list on `VaultConfig`), A3 (`projects_path`/`domain_path` exist as properties), A4 (`attachment_dir`/`summaries_subdir` exist as Fields), A5 (the existing CONFIG-importing helpers must not be called).

**Interface shape.**
- `resolve_placement(file_path, target_type, target_name, vault_cfg) -> Placement`
- Callers receive directories, not full filenames. Callers combine `final_dir` with the sanitized stem and extension to form the full binary path. Callers combine `sibling_dir` with `f"{attachment_dst.name}.md"` to form the full sibling path (following DECISION-028 naming rule).
- Dependency category: in-process.

**Decisions.**
- Q: Should `resolve_placement` be exported in `__all__` for `vault/paths.py`? Options: yes (explicit public surface) / no (all functions in the module are implicitly public). Leaning yes — the function is a named seam with multiple callers; making it explicit prevents accidental underscores-as-private confusion. Low-cost addition.

**Done when.**
- `from vault.paths import resolve_placement` succeeds.
- For a `.pdf` file at `Projects/Alpha/report.pdf` with `target_type="project"`, `target_name="Alpha"`: `final_dir` equals `vault_cfg.projects_path / "Alpha" / vault_cfg.attachment_dir`, `needs_move` is `True`.
- For the same `.pdf` already at `Projects/Alpha/attachment/report.pdf`: `needs_move` is `False`.
- For a `.docx` file at `Projects/Alpha/budget.docx`: `final_dir` equals `vault_cfg.projects_path / "Alpha"`, `needs_move` is `False`.
- For a `.docx` file at `Projects/Alpha/attachment/budget.docx`: `final_dir` equals `vault_cfg.projects_path / "Alpha"`, `needs_move` is `True`.
- For a `.PDF` file (uppercase extension): `final_dir` is the attachment directory (the lowercase check routes it as no-edit). Unit test with a mixed-case suffix.
- For a `Domain/Finance/…` path with `target_type="domain"`, `target_name="Finance"`: the same four cases hold symmetrically.
- `sibling_dir` equals `final_dir / vault_cfg.summaries_subdir` in every case (no exception).
- No filesystem side effects: pointing the helper at a `tmp_path` with no directories inside does not create any directories or files.
- No `CONFIG` import occurs inside the function (test with a `VaultConfig(root=tmp_path)` that has no real vault on disk).

---

#### 3. Add unit tests for `Placement` and `resolve_placement`

**Goal.** Confirm the placement rule is correct across all four routing cases, both location types (project and domain), a mixed-case suffix, and the no-filesystem-side-effects guarantee — and confirm that the C-17 rule (no module-scope CONFIG import) is respected.

**Build.**

Add a new test file `tests/test_vault/test_paths_placement.py` (or add a class `TestResolvePlacement` to the existing `tests/test_vault/test_paths.py` if it exists). All tests must construct `VaultConfig(root=tmp_path)` directly — no `from core.config import CONFIG` at module scope.

The tests cover:

1. **`test_placement_dataclass_is_frozen`** — constructing a `Placement` and attempting to overwrite a field raises `FrozenInstanceError`.

2. **`test_no_edit_not_in_attachment_needs_move`** — `.pdf` at project root: `needs_move=True`, `final_dir` ends with `attachment/`.

3. **`test_no_edit_already_in_attachment_no_move`** — `.pdf` already inside `attachment/`: `needs_move=False`, `final_dir` still ends with `attachment/`.

4. **`test_editable_not_in_attachment_no_move`** — `.docx` at project root: `needs_move=False`, `final_dir` equals the project root dir.

5. **`test_editable_in_attachment_needs_move`** — `.docx` inside `attachment/`: `needs_move=True`, `final_dir` equals the project root dir.

6. **`test_domain_symmetry`** — repeat the four cases above with `target_type="domain"` to confirm project and domain paths are handled identically.

7. **`test_uppercase_extension_routed_as_no_edit`** — file with `.PDF` extension: routes the same as `.pdf` (sibling dir inside `attachment/.summaries/`).

8. **`test_sibling_dir_always_equals_final_dir_slash_summaries`** — for every case, assert `placement.sibling_dir == placement.final_dir / vault_cfg.summaries_subdir`.

9. **`test_no_filesystem_side_effects`** — call `resolve_placement` with a `tmp_path` that has no directories; assert that no new directories or files appear in `tmp_path` after the call. (This guards against accidental `mkdir` calls.)

10. **`test_custom_no_edit_extensions`** — construct `VaultConfig(root=tmp_path, no_edit_extensions=[".xlsx"])` and verify that `.xlsx` is routed to attachment (no-edit) while `.docx` is routed to the visible root (editable).

**Depends on.** Components 1 and 2 (`Placement` and `resolve_placement` must exist).

**Assumes.** A2, A3, A4, A6.

**Done when.**
- All tests pass under `uv run pytest tests/test_vault/test_paths_placement.py -m "not smoke"` (or equivalent test class path).
- No module-scope `from core.config import CONFIG` appears anywhere in the new test file.
- `uv run pytest tests/ -m "not smoke"` still passes with no regressions (count >= 798, current baseline).

---

### Handoff notes

- **[From T1 spec] Contract honored:** T2 uses `vault_cfg.no_edit_extensions` (the Field T1 adds) and compares `file_path.suffix.lower()` against it — exactly as the T1 spec specifies as a "T2 consumer obligation." This spec confirms that obligation is met.

- **Contract with T3:** T3 (`_store_nonmd` rewrite) calls `resolve_placement(src, target_type, target_name, vault_cfg)` after the existing `target_type`/`target_name` derivation block (capture.py:562–575). It receives a `Placement` and then:
  - Uses `placement.final_dir` as the directory for the collision loop (replacing the current `att_dir` variable).
  - Uses `placement.sibling_dir` as the directory for the sibling path (replacing `sum_dir`).
  - Uses `placement.needs_move` instead of the current `needs_move` boolean.
  - Deletes the `if target_type == "project": att_dir = ... else: att_dir = ...` block (capture.py:593–598).
  - T3 still owns: the rename-gate call, the collision loop, the LLM call, `write_note`, `move_attachment`, the audit row, and the `type=attachment-summary` frontmatter field.

- **Contract with T6 (watcher re-home):** T6's rewritten else-branch in `_handle_binary_move` calls `resolve_placement(dst, loc_type, loc_name, cfg)` where `loc_type` and `loc_name` come from `_location_context(dst, cfg)`. T6 must import `resolve_placement` from `vault.paths` at module top level (so that tests can patch `vault.watcher.resolve_placement`, per TD-033).

- **Contract with Phase 2 Classify:** Phase 2's placement call will follow the same signature. No modification to `resolve_placement` is expected — Phase 2 simply adds a second call site.

- **[REQUIRES: T10] root-`.summaries/` recognition:** When an editable file lands in the project root, its sibling lives at `Projects/<A>/.summaries/<name>.md`. The current `_is_managed_summaries_area` predicate in `vault/paths.py` only recognizes `.summaries/` under `attachment/` or `inbox/` — it does NOT recognize root-level `.summaries/`. T10 must update `_is_managed_summaries_area` to also cover `Projects/<A>/.summaries/` and `Domain/<D>/.summaries/`. This spec does not touch `_is_managed_summaries_area`; T10 owns that change. Until T10 lands, reconcile Stage 4 will leave root-level sibling cards alone (it will not orphan them, which is safe but means stale root siblings accumulate until T10).

- **Open uncertainty:** The design doc notes that `needs_move = (file_path.parent != final_dir)` is a `Path` equality comparison. On macOS, filesystem paths can have NFC vs NFD normalization differences. If `file_path` is NFD-normalized (watchdog produces NFD paths on macOS) and `vault_cfg.projects_path` is NFC-normalized, the comparison may produce a spurious `True`. The existing codebase normalizes watcher paths via `unicodedata.normalize("NFC", ...)` before vault-relative path computation (CLAUDE.md gotcha). The planner should verify whether `resolve_placement` needs a normalization step in the comparison, or whether callers always pass NFC-normalized paths. Recommend: research this in `/research` before coding.

- **Suggested research for /research:**
  1. Confirm that neither `Placement` nor `resolve_placement` exist in the codebase (grep for both names in `src/`).
  2. Verify that `_is_in_managed_attachment` is the correct predicate for "is the binary in attachment/" — specifically that it returns `True` for `Projects/<A>/attachment/report.pdf` and `False` for `Projects/<A>/report.pdf`.
  3. Check path normalization: does `vault_cfg.projects_path` return an NFC or NFD `Path` on macOS? Does `file_path` as passed by capture arrive NFC or NFD? This determines whether the `file_path.parent != final_dir` comparison is safe as-is.
  4. Verify that `tests/test_vault/test_paths.py` exists and follows the `VaultConfig(root=tmp_path)` pattern — to confirm where to add the new test class.

---

## T3 — `_store_nonmd`: symmetric, type-driven `needs_move`

### Purpose

Today the capture pipeline hides every non-`.md` file inside a hidden `attachment/` folder — even editable office documents (Word, Excel, PowerPoint) that the executive actively works with. Those files then disappear from Obsidian's view. This task fixes that asymmetry: no-edit files (PDFs, images) continue to go into `attachment/` as before, while editable files go to — or stay in — the visible project or domain root. The AI-written summary card ("sibling") always follows the binary to its correct `.summaries/` folder. No new AI call is introduced; the routing decision comes purely from the `Placement` dataclass that T2 already computes.

After this task, a `.docx` captured into `Projects/Alpha/` is visible in Obsidian at `Projects/Alpha/<name>.docx`. A `.pdf` in the same folder still moves into `Projects/Alpha/attachment/<name>.pdf`. The rule lives in exactly one place (`resolve_placement` in T2), so the capture pipeline and every future consumer (Phase 2 Classify, T6 watcher re-home) can never disagree.

---

### Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `_store_nonmd` | `src/pipelines/capture.py:540–697` | Async function that handles captured non-`.md` files — resolves destination (LOCATED or CLUELESS), writes the sibling summary card, moves the binary, audits, upserts the DB row | This spec rewires only the **LOCATED branch** of this function; the CLUELESS branch (lines 699–763) is left byte-for-byte unchanged | Deep |
| Inline destination block | `src/pipelines/capture.py:561–575` | Derives `target_type`, `target_name`, and `needs_move` from the source path using `vault_cfg.projects_path`/`domain_path` membership checks | **Deleted by this task** — replaced by a call to `resolve_placement` (T2) | Deep |
| Dir-selection block | `src/pipelines/capture.py:593–598` | Picks `att_dir` and `sum_dir` by calling `project_attachment`/`project_summaries` or `domain_attachment`/`domain_summaries` | **Deleted by this task** — T2's `Placement.final_dir` and `Placement.sibling_dir` supply these directly | Deep |
| Collision loop | `src/pipelines/capture.py:600–611` | Tries `<stem><suffix>`, then `<stem>-1<suffix>` … `<stem>-100<suffix>` against `att_dir` until a free slot is found; returns `Failure` if all 100 slots are taken | Kept intact — generalised to run against `placement.final_dir` instead of the hardwired `att_dir` | Deep |
| `resolve_placement` | `src/vault/paths.py` (added by T2) | Pure function returning `Placement(final_dir, sibling_dir, needs_move)` for a captured non-`.md` file given its resolved location type and name | T3 calls this immediately after deriving `target_type`/`target_name`, replacing the deleted inline blocks | Shallow |
| `Placement` dataclass | `src/vault/paths.py` (added by T2) | Frozen dataclass with `final_dir: Path`, `sibling_dir: Path`, `needs_move: bool` | T3 unpacks the returned `Placement` to drive the collision loop, sibling path, and move guard | Shallow |
| `decide_rename` | `src/pipelines/capture.py:587–591` | Applies the rename gate to produce `sanitized_stem` from the AI title | Untouched — still runs before the collision loop | Deep |
| Sibling write (`write_note`) | `src/pipelines/capture.py:640–654` | Writes the sibling `.md` card with `actor="ai"`, `type="attachment-summary"`, `attachment_path` pointing at the binary's final vault path, and `source_hash` | Kept intact — only the `sibling_path` argument changes (uses `placement.sibling_dir`) | Deep |
| `move_attachment` | `src/vault/writer.py:241` | Moves a binary file on disk through the vault writer; returns `Result` | Kept intact — only the destination path changes (uses `placement.final_dir`) | Deep |
| LOCATED audit row | `src/pipelines/capture.py:673–690` | Writes `audit_log` row with `pipeline="capture"`, `stage="store"`, `outcome="LOCATED"` | Kept — `reasoning` string enriched to record `editable→root` vs `no-edit→attachment` | Shallow |
| `documents.upsert` | `src/pipelines/capture.py:693–697` | Upserts the sibling's `DocumentRow` into the `documents` table with `batch_id` | Untouched | Shallow |
| `source_hash` computation | `src/pipelines/capture.py:636–639` | Hashes the binary at its pre-move path (`src` if `needs_move` else `attachment_dst`); used by T9 content-change detection | Kept byte-for-byte — logic still valid because binary bytes are identical before and after the move | Shallow |
| `_audit_file_lost` | `src/pipelines/capture.py:580–586` | Handles the case where the source file disappears during the pipeline | Untouched — runs before the new `resolve_placement` call, in the same position | Shallow |
| Lazy imports (now unused) | `src/pipelines/capture.py:550–555` | `from vault.paths import domain_attachment, domain_summaries, project_attachment, project_summaries` | **Deleted by this task** — these four helpers are no longer called once the dir-selection block is removed | Shallow |
| `project_attachment`, `domain_attachment`, `project_summaries`, `domain_summaries` | `src/vault/paths.py` | Parametrized path helpers that use the CONFIG singleton internally | **No longer called from `_store_nonmd`** after T3 — their path math is now inside `resolve_placement` (T2), which is pure and CONFIG-free | Shallow |
| `WriteOutcome` | `src/vault/writer.py` | Return type for vault write operations | Unchanged; `_store_nonmd` still returns `Result[WriteOutcome]` | Shallow |
| `to_vault_path` | Used at `capture.py:634, 678` | Converts an absolute `Path` to a vault-relative string for DB/audit rows | Kept; called with the final `attachment_dst` to produce the `attachment_vault_path` written into sibling frontmatter | Shallow |

---

### Feature overview

When `_store_nonmd` reaches the LOCATED branch (the file has a resolved project or domain home), it currently does two things in sequence that together embed the placement rule: it derives `needs_move` from a raw path-parts check, then it hardwires `att_dir` and `sum_dir` to the `attachment/` subtree regardless of file type. Both blocks are deleted.

After this task the LOCATED branch reads as follows (in plain English):

1. **Guard: file existence check.** Same as before — bail with `Failure` + `_audit_file_lost` if the source file has disappeared.

2. **Rename gate.** Same as before — `decide_rename` produces `sanitized_stem`.

3. **Placement resolution (new).** Call `resolve_placement(src, target_type, target_name, vault_cfg)` — the function T2 adds to `vault/paths.py`. Receive back a `Placement` with three fields: `final_dir` (where the binary belongs), `sibling_dir` (where its `.summaries/` card belongs), and `needs_move` (whether the binary is in the wrong place right now). This single call replaces both the deleted inline destination block and the deleted dir-selection block.

4. **Collision loop.** Same logic as before, but run against `placement.final_dir` instead of the old hardwired `att_dir`. If `placement.needs_move` is False, skip the loop — the binary is already at its destination.

5. **Sibling write.** Same as before, but the sibling path is now `placement.sibling_dir / f"{attachment_dst.name}.md"` instead of `sum_dir / f"..."`. The `type="attachment-summary"` frontmatter field, `attachment_path`, `source_hash`, and `actor="ai"` are all kept unchanged.

6. **Binary move.** Same as before — `move_attachment(src, attachment_dst)` — but `attachment_dst` is now resolved against `placement.final_dir`. Move only fires when `placement.needs_move` is True.

7. **Audit row.** Same as before, but the `reasoning` string is enriched: instead of `"Routed to {target_type}/{target_name}"` it becomes something like `"Routed to {target_type}/{target_name} (editable→root)"` or `"(no-edit→attachment)"` so the audit trail is legible in the daily briefing.

8. **DB upsert.** Unchanged.

The CLUELESS branch (inbox-parking for files with no project or domain context, lines 699–763) is **not touched by this task**. Every single line in it stays exactly as it is.

---

### Out of scope

- **CLUELESS / inbox-parking path** — lines 699–763 of `_store_nonmd` are unchanged. T4 owns the misplaced→inbox sweep; Phase 2 Classify owns the pending-routing resolution. Neither is touched here.
- **`resolve_placement` implementation** — owned entirely by T2. T3 only calls it; it does not modify or re-implement it.
- **Root-`.summaries/` recognition in reconcile predicates** — when editable files land in the project root their siblings live at `Projects/<A>/.summaries/<name>.md`. The current `_is_managed_summaries_area` predicate in `vault/paths.py` does not yet recognise this location. T10 must extend it. T3 creates the new sibling location but does not update the predicate. [REQUIRES: T10]
- **T8 MoveGuard wiring at the `move_attachment` call site** — T8 is responsible for registering the move destination with the suppression guard before `move_attachment` fires. T3's edit does not add that registration; T8 does. The call site at `capture.py:658` will be wrapped by T8 in a later task. (Build order: T3 before T8.)
- **Watcher re-home on user move** — owned by T6. T3 only changes where the pipeline puts the file; it does not change what the watcher does when a user subsequently moves it.
- **Phase 2 Classify wiring** — Phase 2 is a future caller of `resolve_placement`; T3 is the first caller. Phase 2 is out of scope here.
- **`_collect_folder_files` attachment/summaries exclusion** — T5.
- **`_is_ai_output` / `_is_misplaced` predicates** — T4.
- **Content-change re-capture on binary edit** — T9.

---

### Constraints

- **C-01 · Vault-only writes** — T3 keeps calling `write_note` (sibling) and `move_attachment` (binary) through `vault/writer.py`. The only change is the destination `Path` argument. No raw `.write_text()` or `open(..., 'w')`. Source: `CONSTRAINTS.md` C-01; hook hard-block.
- **C-03 · write_note is a pure writer** — The sibling is a new AI-authored file at each capture (`is_existing_doc=False`); T3 passes a full `NoteMetadata` with all required fields. No merge needed for a new sibling. Source: `CONSTRAINTS.md` C-03.
- **C-12 · Result return** — `_store_nonmd` already returns `Result[WriteOutcome]` on every path. T3 adds no new early returns beyond the existing collision-exhausted `Failure` (reused verbatim). Source: `CONSTRAINTS.md` C-12.
- **C-13 · Audit every AI decision** — The LOCATED audit row (lines 673–690) is preserved. T3 enriches its `reasoning` string but does not remove or skip it. Source: `CONSTRAINTS.md` C-13.
- **C-07 · Prompts as YAML** — The `summarize_attachment` LLM call (lines 617–628) is untouched. No new f-string prompts introduced. Source: `CONSTRAINTS.md` C-07.
- **C-17 · No module-scope CONFIG in tests** — New tests must construct `VaultConfig(root=tmp_path)` directly. `resolve_placement` takes `vault_cfg` explicitly and uses no CONFIG singleton. Source: `CONSTRAINTS.md` C-17.
- **DECISION-025 · Sibling-first ordering** — Sibling is written before the binary is moved. T3 preserves this ordering exactly. Source: `capture.py` DECISION-025 comment.
- **DECISION-029 · `type=attachment-summary` on every sibling** — The `type="attachment-summary"` frontmatter field is kept for root-placed siblings too. Reconcile Stage 4 (T10) needs this even outside `attachment/`. Source: CLAUDE.md DECISION-029; `capture.py:641`.
- **Sibling naming rule (CLAUDE.md gotcha)** — Sibling filename is `<binary.name>.md` (full filename including extension), e.g. `report.pdf.md`. T3 keeps the `f"{attachment_dst.name}.md"` formula at `capture.py:633` unchanged. Source: DECISION-028; CLAUDE.md "What Claude gets wrong."
- **TD-033 · Monkeypatch the importing module** — Tests that stub `resolve_placement` when testing `_store_nonmd` must patch `pipelines.capture.resolve_placement`, not `vault.paths.resolve_placement`. T3 adds `resolve_placement` to `capture.py`'s imports at module top (or lazy-imports inside the function at the call site — see Decisions below), and tests patch that name. Source: CLAUDE.md TD-033.
- **[REQUIRES: T2]** — `resolve_placement` and `Placement` must exist in `vault/paths.py` before T3 compiles. T2 must land first. Source: design doc build order.
- **[REQUIRES: T1]** — `vault_cfg.no_edit_extensions` must exist on `VaultConfig` before `resolve_placement` (called by T3) can run. T1 must land before T2 and therefore before T3. Source: T2 spec Constraints.

---

### Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|---|---|---|
| A1 | `_store_nonmd` is defined at `src/pipelines/capture.py:540` and its LOCATED branch spans lines ~561–697, with the inline destination block at lines 561–575 and the dir-selection block at lines 593–598 | Design doc T3 Implications §"The actual code being replaced" | If the function has been moved, split, or significantly renumbered since the design was written, the deletion targets would be at different line numbers |
| A2 | The four lazy imports at `capture.py:550–555` (`project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries`) are only called within the deleted dir-selection block (593–598) and nowhere else in `_store_nonmd` | Design doc T3 Implications §"Files touched" | If any of these four helpers are also called below line 599 in the same function (e.g. in the CLUELESS branch), deleting the lazy import block would cause a `NameError` at runtime |
| A3 | `resolve_placement` (added by T2) has the signature `resolve_placement(file_path: Path, target_type: str, target_name: str, vault_cfg: VaultConfig) -> Placement` and is importable from `vault.paths` | T2 spec §Component 2 "Interface shape"; design doc T2 Option A | If the function signature or module location differs, T3's call site would not compile |
| A4 | At the T3 call site, `target_type` and `target_name` have already been derived from the source path (lines 562–575 of the existing code); they are available as local variables before the placement call | Design doc T3 Implications §"The actual code being replaced" — "T3 still derives `target_type`/`target_name` from the path" | If the derivation block is moved inside the deleted region, T3 must keep the derivation logic while only deleting the `needs_move` and dir-selection sub-blocks |
| A5 | The collision loop at `capture.py:600–611` uses `att_dir` as the directory variable; after T3's change it is repointed to `placement.final_dir` with no other structural change needed | Design doc T3 Implications §"The actual code being replaced" — "generalised to run against `Placement.final_dir`" | If the loop uses other variables tied to the attachment subtree (e.g. an explicit `attachment/` string), additional changes would be needed |
| A6 | `sibling_path` is constructed at `capture.py:633` as `sum_dir / f"{attachment_dst.name}.md"`; after T3 `sum_dir` is replaced by `placement.sibling_dir`, with no other change to the sibling-write step | Design doc T3 Implications §"Guards/constraints that apply" — "only the `sibling_path` argument changes" | If `sum_dir` is used in additional places below line 633 within the LOCATED branch, each use must be updated |
| A7 | The CLUELESS branch starts at `capture.py:699` with a comment `# CLUELESS path:` and is completely independent of the inline destination block; removing lines 561–575 and 593–598 leaves the CLUELESS branch syntactically and semantically intact | Design doc T3 Implications §"The actual code being replaced" — "CLUELESS branch is out of scope, left byte-for-byte unchanged" | If the CLUELESS branch shares any local variable initialized in the deleted blocks (other than `target_type` / `target_name` / `needs_move` which are set before the block), deletion would cause `NameError` |
| A8 | `ctx.config.vault` is available as `vault_cfg` at the top of `_store_nonmd` and can be passed directly to `resolve_placement` | Design doc T3 Implications §"Runtime deps" and existing code at `capture.py:559` | If `vault_cfg` is a different type or not fully initialized at that point, the call would fail |
| A9 | The existing test file for `_store_nonmd` is in `tests/test_pipelines/` (likely `test_capture.py` or `test_capture_phase9.py`) and uses monkeypatching patterns compatible with TD-033 (patching `pipelines.capture.<name>` not the source module) | Design doc T3 Implications §"Files touched"; CLAUDE.md TD-033 | If the test file doesn't exist or uses a different import pattern, new tests would need to establish the patching convention from scratch |

---

### Component dependency order

#### 1. Derive `target_type` / `target_name` and call `resolve_placement` (replace the inline destination block)

**Goal.** Replace the two-block inline destination resolution (the `needs_move` derivation at lines 561–575 and the dir-selection at lines 593–598) with a single call to T2's `resolve_placement`, giving `_store_nonmd` a type-driven, config-sourced placement decision.

**Build.**

Inside `_store_nonmd`, in `src/pipelines/capture.py`:

1. Keep the `target_type` / `target_name` derivation as-is (lines 562–575). These variables are still needed as inputs to `resolve_placement`. The only sub-line to remove from this block is the `needs_move = rel.parts[1] != vault_cfg.attachment_dir` assignment (lines 570 and 575) — `needs_move` will come from the `Placement` instead.

2. Add an import of `resolve_placement` from `vault.paths`. Because the existing code uses a lazy import pattern for the four helpers at lines 550–555, maintain consistency: add `resolve_placement` (and `Placement`) to the same lazy import block if it is kept, or import at the top of the function if the lazy block is being removed (see Decisions below).

3. After the `target_type` / `target_name` derivation block and before the `if target_type is not None:` check, add nothing — `resolve_placement` is called inside the LOCATED branch where `target_type is not None` is already confirmed.

4. At the start of the LOCATED branch (currently line 577), immediately after the existing file-existence guard and rename-gate call, insert the `resolve_placement` call:

   ```
   placement = resolve_placement(src, target_type, target_name, vault_cfg)
   ```

   This produces `placement.final_dir`, `placement.sibling_dir`, and `placement.needs_move`.

5. Delete the dir-selection block (lines 593–598: the `if target_type == "project": att_dir = ... else: att_dir = ...` block). Delete the four lazy imports at lines 550–555 that fed it (`project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries`).

6. In all subsequent code within the LOCATED branch, replace every reference to `att_dir` with `placement.final_dir` and every reference to `sum_dir` with `placement.sibling_dir`. Replace the local `needs_move` boolean with `placement.needs_move`.

**Depends on.** T2 (`resolve_placement` and `Placement` must exist in `vault/paths.py`). T1 (`vault_cfg.no_edit_extensions` must exist on `VaultConfig` for `resolve_placement` to work at runtime).

**Assumes.** A1, A2, A3, A4, A5, A6, A7, A8.

**Interface shape.**
- This step modifies an existing internal function, not a public interface boundary.
- Callers of `_store_nonmd` are unchanged — it still takes `(mr, note_meta, ctx)` and returns `Result[WriteOutcome]`.
- The `resolve_placement` import must be at the top of `capture.py` (or inside the function body using a lazy import) so that tests can patch `pipelines.capture.resolve_placement` per TD-033.

**Decisions.**
- Q: Keep the lazy import pattern (inside the function) or move to a module-level import at the top of `capture.py`? Options: lazy import (consistent with existing pattern for the four helpers; slightly harder to patch in tests — must patch inside the function's local scope) / module-level import (standard; easier to patch as `pipelines.capture.resolve_placement`; breaks from the existing lazy-import precedent in this function). Leaning module-level import because TD-033 patching is cleaner and the lazy-import pattern existed to avoid a circular import risk that does not apply to `vault.paths` (which never imports from `pipelines`).

**Done when.**
- A `.docx` file at `Projects/Alpha/budget.docx` captured via `kms capture` stays at `Projects/Alpha/budget.docx` in the vault (visible in Obsidian) — it is not moved into `attachment/`.
- A `.pdf` file at `Projects/Alpha/report.pdf` captured via `kms capture` moves to `Projects/Alpha/attachment/report.pdf` (hidden from Obsidian).
- A `.docx` found at `Projects/Alpha/attachment/budget.docx` during capture is moved out to `Projects/Alpha/budget.docx`.
- A `.pdf` already at `Projects/Alpha/attachment/report.pdf` stays put.
- The same four outcomes hold for `Domain/<D>/` paths.
- No `NameError` occurs from removed imports or variables (CLUELESS branch still works for files that have no project/domain context).

---

#### 2. Enrich the LOCATED audit row `reasoning` string

**Goal.** Make the audit trail legible — so the daily briefing and any human reading the audit log can tell at a glance whether a captured file was treated as editable (visible root) or no-edit (hidden attachment).

**Build.**

In the `audit.write(...)` call at `src/pipelines/capture.py:673–690`, change the `reasoning` argument from:

```
reasoning=f"Routed to {target_type}/{target_name}"
```

to:

```
reasoning=f"Routed to {target_type}/{target_name} ({'editable→root' if not placement_is_no_edit else 'no-edit→attachment'})"
```

The cleanest way to compute the label: after the `resolve_placement` call (step 1), derive a boolean `_is_no_edit = placement.needs_move and placement.final_dir != src.parent or (not placement.needs_move and vault_cfg.attachment_dir in placement.final_dir.parts)`. Alternatively, re-use `src.suffix.lower() in vault_cfg.no_edit_extensions` directly at the audit site — this is a one-line read, not a new decision. Either approach is acceptable; the planner chooses the cleaner one.

This is a one-line change to a string value. No new audit row, no new `audit.write` call, no new AI decision point.

**Depends on.** Component 1 (the `placement` variable must exist in scope).

**Assumes.** A1 (the audit row exists at lines 673–690).

**Done when.**
- The `audit_log` table's `reasoning` column for a LOCATED capture of a `.docx` contains the substring `editable→root`.
- The `reasoning` column for a LOCATED capture of a `.pdf` contains `no-edit→attachment`.
- Existing LOCATED audit rows for files already in `attachment/` (no-edit, `needs_move=False`) also contain `no-edit→attachment`.

---

#### 3. Add unit tests for the rewritten LOCATED branch

**Goal.** Confirm that the four routing cases (editable/no-edit × already-placed/misplaced), both location types (project and domain), and the collision-exhaustion failure path all produce the correct outcomes — and confirm that C-17 (no module-scope CONFIG import) and TD-033 (patch the importing module) are respected.

**Build.**

In `tests/test_pipelines/` (the existing test directory for capture), add a new test class (or new test methods in the existing `test_capture.py`) named `TestStoreNonmdLocatedBranch`. All tests must:
- Construct `VaultConfig(root=tmp_path)` directly — no `from core.config import CONFIG` at module scope.
- Patch `resolve_placement` as `pipelines.capture.resolve_placement` (not `vault.paths.resolve_placement`) per TD-033.
- Patch `write_note` as `pipelines.capture.write_note`, `move_attachment` as `pipelines.capture.move_attachment`, and `audit.write` as `pipelines.capture.audit` per the same TD-033 rule.

The tests cover:

1. **`test_editable_not_in_attachment_no_move`** — `resolve_placement` returns `Placement(final_dir=<project root>, sibling_dir=<project root>/.summaries, needs_move=False)`. Assert `move_attachment` is NOT called. Assert sibling path argument to `write_note` is under `<project root>/.summaries/`. Assert `documents.upsert` is called. Assert function returns `Success`.

2. **`test_editable_in_attachment_moves_to_root`** — `resolve_placement` returns `Placement(final_dir=<project root>, sibling_dir=<project root>/.summaries, needs_move=True)`. Assert `move_attachment` IS called with `src` and a path inside `<project root>`. Assert sibling is written at `<project root>/.summaries/<name>.md`.

3. **`test_no_edit_not_in_attachment_moves_to_attachment`** — `resolve_placement` returns `Placement(final_dir=<project root>/attachment, sibling_dir=<project root>/attachment/.summaries, needs_move=True)`. Assert `move_attachment` IS called with destination inside `attachment/`. Assert sibling is at `attachment/.summaries/`.

4. **`test_no_edit_already_in_attachment_no_move`** — `resolve_placement` returns `Placement(final_dir=<project root>/attachment, sibling_dir=<project root>/attachment/.summaries, needs_move=False)`. Assert `move_attachment` is NOT called.

5. **`test_domain_symmetry`** — Repeat at least one of the above cases with a domain source path (`Domain/Finance/…`) to confirm project and domain paths are handled identically by the rewritten branch.

6. **`test_collision_loop_uses_final_dir`** — `resolve_placement` returns `needs_move=True` with `final_dir` set to a directory where `<stem><suffix>` already exists (mock `attachment_dst.exists()` to return True for the first two iterations, False on the third). Assert the final filename is `<stem>-2<suffix>`. Assert the path is inside `placement.final_dir`, not any hardwired `attachment/` path.

7. **`test_collision_exhaustion_returns_failure`** — `resolve_placement` returns `needs_move=True`; `attachment_dst.exists()` always returns True (all 100 slots taken). Assert `_store_nonmd` returns `Failure(recoverable=False)` with the stem and suffix in context.

8. **`test_audit_reasoning_includes_routing_class_editable`** — Capture an editable file; assert the `audit.write` call's `reasoning` argument contains `editable→root`.

9. **`test_audit_reasoning_includes_routing_class_no_edit`** — Capture a no-edit file; assert `reasoning` contains `no-edit→attachment`.

10. **`test_sibling_type_is_attachment_summary_for_root_placement`** — Editable file, sibling lands in project root `.summaries/`; assert the `NoteMetadata` passed to `write_note` has `type="attachment-summary"` (DECISION-029 guard).

11. **`test_clueless_branch_unaffected`** — Pass a source path with no project or domain context (so `target_type` stays `None`); assert the function routes to the CLUELESS branch (inbox-park marker written, no `resolve_placement` call). This is a non-regression check confirming the CLUELESS branch was not accidentally broken.

**Depends on.** Components 1 and 2 (the rewritten LOCATED branch and enriched audit reasoning must exist).

**Assumes.** A8, A9.

**Done when.**
- All tests pass under `uv run pytest tests/test_pipelines/ -m "not smoke"`.
- No `from core.config import CONFIG` appears at module scope in the new test code.
- No patch targets `vault.paths.resolve_placement` — all patches use `pipelines.capture.resolve_placement`.
- `uv run pytest tests/ -m "not smoke"` still passes with no regressions (count ≥ 798, current baseline).

---

### Handoff notes

- **[From T1 spec] Contract honored:** `vault_cfg.no_edit_extensions` is read by `resolve_placement` (T2), which T3 calls. T3 itself does not read `no_edit_extensions` directly — that obligation was T2's. T3 only receives the already-computed `Placement`.

- **[From T2 spec] Contract honored:** T3 calls `resolve_placement(src, target_type, target_name, vault_cfg)` after the existing `target_type`/`target_name` derivation (lines 562–575). It uses `placement.final_dir` as the collision-loop directory, `placement.sibling_dir` as the sibling directory, and `placement.needs_move` as the move guard. It deletes the inline destination block and the dir-selection block as the T2 spec specifies. T3 still owns: the rename-gate call, the collision loop, the LLM call, `write_note`, `move_attachment`, the audit row, and the `type=attachment-summary` frontmatter field.

- **Contract with T8 (MoveGuard):** T8 must wrap the `move_attachment(src, attachment_dst)` call at `capture.py:658` with a `guard.register(attachment_dst)` call before the move fires. T3's edit leaves this call site at its existing location; T8 adds the registration around it. The two tasks do not conflict as long as T3 lands before T8.

- **Contract with T6 (watcher re-home):** T6 consumes `resolve_placement` independently — it calls it from `vault/watcher.py` with `(dst, loc_type, loc_name, cfg)`. T3's change to `capture.py` does not affect T6's call site. The shared rule lives in T2 only.

- **Contract with T10 (reconcile migration):** Root-level `.summaries/` siblings (created by T3 for editable files) are not yet recognized by `_is_managed_summaries_area`. Until T10 extends that predicate, reconcile Stage 4 will leave root siblings alone (safe: no orphaning, just accumulation). T3 must set `type="attachment-summary"` on every root sibling so that once T10 lands, Stage 4's type guard correctly identifies them. This spec confirms that obligation.

- **Contract with Phase 2 Classify:** Phase 2 calls `resolve_placement` from its own code path — not from `_store_nonmd`. The CLUELESS branch that Phase 2 resolves (lines 699–763) is untouched by T3. Phase 2 will find the CLUELESS branch structurally identical to what it expects.

- **[REQUIRES: T10] root-`.summaries/` recognition:** Root-level siblings at `Projects/<A>/.summaries/<name>.md` are a new location this task creates. `_is_managed_summaries_area` in `vault/paths.py` must be extended by T10 to cover this location. Until T10, stale root siblings will not be cleaned up by `kms reconcile`. Safe but imperfect until T10 lands.

- **Open uncertainty — path normalization:** The `needs_move` check inside `resolve_placement` is `file_path.parent != final_dir` (a `Path` equality comparison). On macOS, watchdog delivers NFD-normalized paths, while `vault_cfg.projects_path` is NFC (loaded from YAML on disk). If `src` arrives NFD, the comparison may spuriously return `True` even when the file is already in the right place. The T2 spec flags this as an open question for research. T3 is the first consumer, so the planner for T3 should resolve this before coding: if `resolve_placement` normalizes its inputs, T3 is safe; if not, T3 must normalize `src` before passing it. The existing codebase normalizes watcher paths via `unicodedata.normalize("NFC", ...)` (CLAUDE.md gotcha) — confirm whether `capture.py` paths follow the same pattern.

- **Suggested research for /research:**
  1. Verify the exact line ranges of the two deleted blocks: does the inline destination block really end at line 575, and does the dir-selection block really span lines 593–598? Confirm by reading the live file — the design doc was written from a snapshot and line numbers may have shifted.
  2. Confirm that `project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries` are used ONLY in the dir-selection block within `_store_nonmd` — that no other line in the function calls them. If any other call site exists, deleting the lazy import block would cause a `NameError`.
  3. Verify that `sum_dir` is referenced only at `capture.py:633` and nowhere else in the LOCATED branch. Same for `att_dir`.
  4. Verify path normalization for `src` in `_store_nonmd`: how does the source path arrive — directly from `mr.raw.source_path`? Is it NFC or NFD on macOS? This determines whether `resolve_placement` needs a normalization step.
  5. Confirm the current test baseline count (the spec says ≥ 798; verify with `uv run pytest tests/ -m "not smoke" --co -q | tail -1` before coding begins).

---

## T4 — Misplaced-to-inbox (all types) + AI-output capture-exclusion

### Purpose

This task adds two new "leave this alone" / "move this to safety" rules that apply uniformly across both the live watcher and the batch scan, so the system never accidentally processes files it should not, and never silently ignores a mis-dropped file.

**Rule 1 — AI-output folders are off-limits to capture.** The three folders the system writes to itself (`Briefings/`, `Synthesis/`, `Documentation/`) must never be captured. Capturing a briefing would produce a new note, which would be captured, creating an infinite feedback loop. Both the live watcher and the batch scan must skip these folders entirely — without adding them to the indexer's `IGNORE_DIRS` (which would blind full-text search).

**Rule 2 — Mis-dropped files are swept to inbox (now including `.md` notes).** When a file lands somewhere that is not a real home — for example, directly in `Projects/` with no project subfolder — the system should move it to `inbox/` and treat it as a normal inbox drop. This already works by accident for non-`.md` files (they fall into the CLUELESS branch). This task makes that behaviour intentional, named, and extended to `.md` files, which today are written in place with no sweep.

After this task: both predicates live in `vault/paths.py` alongside the existing path-classification twins, so the watcher and the scanner both consult one definition — with no chance of drift.

---

### Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `VaultConfig.ai_output_dirs` | `src/core/config.py` (added by T1) | Computed `@property` returning `(briefings_dir, synthesis_dir, documentation_dir)` as a `tuple[str, ...]` | `_is_ai_output` iterates this tuple to test whether a path part is an AI-output folder name. T4 is the first consumer of this T1 property. | Shallow |
| `VaultConfig.briefings_dir`, `synthesis_dir`, `documentation_dir` | `src/core/config.py:79–81` | `str` Fields with defaults `"Briefings"`, `"Synthesis"`, `"Documentation"` | Backing data for `ai_output_dirs` — never read directly by T4; always accessed via the tuple property | Shallow |
| `VaultConfig.projects_path`, `domain_path`, `inbox_path` | `src/core/config.py:91,93,89` | `@property` accessors returning resolved `Path` objects | `_is_misplaced` computes path membership against `projects_path` and `domain_path`; the target inbox location comes from `inbox_path` | Shallow |
| `_is_in_managed_attachment` | `src/vault/paths.py:26` | Pure predicate: True if a path lives inside `Projects/<A>/attachment/` or `Domain/<D>/attachment/` | Existing twin predicate; T4's two new predicates follow the same module conventions (bool return, takes `(path, vault_cfg)`, no IO) | Shallow |
| `_is_managed_summaries_area` | `src/vault/paths.py:56` | Pure predicate: True if a path lives inside any attachment subtree or inbox — the scope guard used by reconcile Stage 4 | Second existing twin predicate that confirms the module's established pattern for T4's two new predicates | Shallow |
| `_location_context` | `src/vault/paths.py:87` | Returns `(type, name)` for a path — but treats `Projects/<file>.md` as `("project", "<file>")` (phantom-project quirk) | Explicitly NOT called by `_is_misplaced` — the predicate must do its own `len(rel.parts) >= 2` test to avoid the phantom-project bug. Listed here so the builder knows to avoid it. | Shallow |
| `_should_skip` | `src/vault/watcher.py:124` | Returns True if a watcher path should never trigger a callback (managed attachment, dotfiles, sync-conflict, IGNORE_DIRS) | T4 adds `_is_ai_output` as a new skip condition alongside the existing checks | Deep |
| `IGNORE_DIRS` | `src/vault/indexer.py:42` | Frozen set of directory names pruned during every vault walk (`".git"`, `".obsidian"`, etc.) | Deliberately NOT modified — AI-output exclusion is a capture skip, not an indexer prune | Shallow |
| `_DOT_ALLOWLIST` | `src/vault/indexer.py:56` | `frozenset({".summaries"})` — dotfolders permitted when parent is `attachment/` or `inbox_dir` | Not modified; T4's AI-output pruning is a separate, additive prune step in the `dirnames[:]` list comprehension | Shallow |
| `scan_non_md_drops` | `src/vault/indexer.py:104` | Walks the vault and returns non-`.md` drop candidates, applying Rules 1 + 2 to skip managed-attachment and pending-routing files | T4 adds a Rule 3 prune: skip dirnames that match AI-output folder names using `_is_ai_output` logic applied to dirnames, exactly like the existing IGNORE_DIRS prune | Deep |
| `scan_vault` | `src/vault/indexer.py:156` | Walks the vault and returns `VaultEntry` objects for every readable `.md` note | T4 adds AI-output dirname pruning to the same `dirnames[:]` list comprehension that already prunes `IGNORE_DIRS` and dotfiles | Deep |
| `scan_capture` | `src/pipelines/capture.py:918` | Drives the full scan: calls `scan_vault` for `.md` files, `scan_non_md_drops` for binaries, and dispatches each to `capture_file` | T4 adds a misplaced-md sweep loop over `summary.added` entries before dispatching them to `capture_file` — mis-placed `.md` files are moved to inbox first | Deep |
| `capture_file` | `src/pipelines/capture.py:793` | Runs the 6-stage capture pipeline on a single file | Unchanged. A swept `.md` file, now at its inbox path, is dispatched to `capture_file` like any other inbox drop — no new code path in `capture_file` itself | Deep |
| `move_note` | `src/vault/writer.py` (imported at `vault/watcher.py` top level) | Moves a `.md` file on disk through the vault writer, preserving on-disk metadata | Used by the misplaced-md sweep to move the note to inbox before pipeline dispatch. Must be called as `vault.watcher.move_note` (not `vault.writer.move_note`) when patched in watcher tests (TD-033). | Deep |
| `move_attachment` | `src/vault/writer.py:241` | Moves a binary file on disk through the vault writer | Used by the existing CLUELESS path for non-`.md` misplaced files — already handles the non-md sweep. No new call needed for non-md. | Deep |
| CLUELESS branch (`_store_nonmd`) | `src/pipelines/capture.py:699–763` | Parks a binary with no project/domain context in inbox and writes a pending-routing marker. Already writes an `audit_log` row with `outcome="CLUELESS"`. | Non-md misplaced→inbox is already handled here. T4 leaves this branch byte-for-byte unchanged; it adds only the intentional predicate and the md-sweep to make the behaviour explicit and symmetric. | Deep |
| `audit.write` | `src/core/audit.py` | Writes one row to `audit_log` with `pipeline`, `stage`, `outcome`, `source_ids`, and `reasoning` | T4's misplaced-md sweep must call `audit.write` to record the sweep decision, mirroring the CLUELESS non-md audit row | Deep |
| `AIDecision` | `src/core/confidence.py` | Small dataclass carrying `action`, `confidence`, `reasoning`, `source_ids` — the required wrapper for every `audit.write` call | Passed to `audit.write` for the new misplaced-md audit row | Shallow |
| `to_vault_path` | `src/vault/paths.py:149` | Converts an absolute path to an NFC-normalised vault-relative POSIX string for DB and audit rows | Used in the new misplaced-md audit row's `source_ids` field | Shallow |
| `documents.delete_by_path` | `src/storage/documents.py` | Removes a `documents` row by `vault_path` | Must be called for any swept `.md` that was already indexed at its old pre-sweep path, to prevent an orphan DB row | Shallow |
| `read_note` | `src/vault/reader.py` | Reads and parses a `.md` note from disk | Used to check whether a misplaced `.md` is already indexed (has a DB row) before the sweep, so the old row can be cleaned up | Shallow |
| `new_correlation_id` | `src/core/logging_setup.py` | Creates a fresh correlation ID for a pipeline run | T4's misplaced-md sweep helper is a new pipeline-level function; it must call this at entry and bind it to context | Shallow |

---

### Feature overview

T4 delivers two independent but related behaviours. Both are implemented through named path predicates in `vault/paths.py` that every call site queries — so the rules live in exactly one place.

**Behaviour 1 — AI-output exclusion.**

A new predicate `_is_ai_output(path, vault_cfg)` returns True if any part of the path's component list matches one of the AI-output folder names in `vault_cfg.ai_output_dirs`. The test is done by name (matching anywhere in the path parts), accepting the same documented limitation that T5 accepted for `attachment`/`.summaries`: a user folder coincidentally named `Briefings` inside a project would also be skipped. This is acceptable for the non-technical target user and is consistent with the project's established name-skip convention.

This predicate is wired into three places:
- `_should_skip` in `vault/watcher.py` — adds one condition so any watcher event for a file inside an AI-output folder is silently dropped (with a debug log line using `%s` formatting).
- `dirnames[:]` prune in `scan_non_md_drops` in `vault/indexer.py` — adds AI-output dir names to the in-place prune so the walk never descends into them.
- `dirnames[:]` prune in `scan_vault` in `vault/indexer.py` — same prune for the `.md` walk.

The implementation for the prune loops does NOT call `_is_ai_output` on the full absolute path (since `dirpath / d` has not been formed yet during the prune step). Instead, the prune checks whether `d` (the dirname being pruned) is in `vault_cfg.ai_output_dirs`. This is equivalent but avoids unnecessary path construction during the prune.

**Behaviour 2 — Misplaced-file sweep.**

A new predicate `_is_misplaced(path, vault_cfg)` returns True if a path is inside the vault but is not a real home: not inbox, not AI-output, and not a genuine project or domain location. "Genuine" is defined as: under `projects_path` or `domain_path` with `len(rel.parts) >= 2` (meaning at least one subfolder level beyond the bare root — a real `Projects/<A>/` exists). This test explicitly neutralises the `_location_context` phantom-project quirk by requiring the second path part, and it must NOT call `_location_context` for this check.

For **non-`.md` files**: the existing CLUELESS branch in `_store_nonmd` already produces the misplaced→inbox outcome for bare-`Projects/x.pdf` paths (because `len(rel.parts) < 2` leaves `target_type=None`). T4 makes this intentional by documenting `_is_misplaced` as the conceptual predicate, but no code change is needed in `_store_nonmd` for non-md files — the existing behaviour is preserved unchanged.

For **`.md` files**: this is genuinely new. The `scan_capture` function's `summary.added` loop currently dispatches every added `.md` to `capture_file` regardless of location. T4 adds a pre-dispatch check: before calling `capture_file`, test `_is_misplaced(path, vault_cfg)`. If True, sweep the note to inbox using `move_note`, write an audit row with `outcome="MISPLACED"`, clean up any stale DB row at the old path using `delete_by_path`, then dispatch to `capture_file` with the new inbox path. The pipeline then runs normally on the inbox path.

For the live **watcher**: a misplaced `.md` dropped and picked up by `on_created` passes through `_should_skip` (which only filters AI-output and other known skips). It then fires `_on_create → capture_file`. The misplaced-md check in `scan_capture` covers the batch-scan case; the watcher case is handled because `capture_file` receives the file at its original misplaced path — the pipeline's `store` stage (`_store_md`) writes it in place. This means the watcher path does NOT sweep a misplaced `.md` to inbox automatically; it captures it in place. The design doc acknowledges this: the watcher sweep is handled via `scan_capture` (which runs on startup and on demand), not via the live event handler. The watcher does fire on the swept-and-now-inbox `.md` file as well (the move triggers a `FileCreatedEvent` for the inbox path), which is correct — the inbox capture is a normal pipeline run.

---

### Out of scope

- **Routing onward from inbox** — Phase 2 Classify owns what happens to files after they land in inbox. T4 only deposits mis-dropped files there; it does not classify, tag, or route them further.
- **`IGNORE_DIRS` modification** — AI-output folder names must NOT be added to `IGNORE_DIRS`. That would hide `Briefings/` and `Synthesis/` from FTS search and break the indexer's `.summaries/` visibility (DECISION-029). The exclusion lives only in capture paths (watcher + scan).
- **Non-`.md` misplaced path changes** — the existing CLUELESS branch in `_store_nonmd` already handles non-md misplaced→inbox. T4 leaves it unchanged. `_is_misplaced` is defined as a named predicate for documentation and future callers, but it is not wired into `_store_nonmd`.
- **Watcher `on_created` / `on_moved` direct misplaced-md sweep** — the live watcher does not sweep misplaced `.md` files proactively. The sweep happens in `scan_capture`. The watcher fires `capture_file` in place; the in-place capture stores the file at the misplaced path. The batch `scan_capture` then sweeps it on the next run. This is a known one-scan-lag, accepted for MVP scope.
- **OQ-008 — human edits to excluded folders** — capture-excluding `Documentation/` means human edits there are invisible to the system until a future co-author phase builds a `Documentation/`-only modify listener. This is a known, logged trade-off (OPEN_QUESTIONS.md OQ-008). T4's `_is_ai_output` predicate is the natural future hook for that listener. No code action here.
- **T2/T3 placement logic** — T4 decides only "capture vs skip vs sweep-to-inbox". It never calls `resolve_placement` (that is the editable-vs-no-edit rule, a different concern). T4 is independent of T2 and T3.
- **Reconcile changes** — T10 owns vault migration. T4 adds no reconcile stage.
- **`~$` lock-file filter** — Office lock files (`~$*.tmp`) are T9's concern. T4's `_should_skip` addition is a single membership check against AI-output dir names; it does not touch the lock-file filter.

---

### Constraints

- **C-01 · Vault-only writes** — The misplaced-md sweep calls `move_note` (from `vault/writer.py`), never raw `write_text` or `open(..., 'w')`. Source: `CONSTRAINTS.md` C-01; hook hard-block.
- **C-02 · `updated_by_human` gate** — Moving a misplaced `.md` to inbox must NOT rewrite its body or strip frontmatter. `move_note` is a disk-level move that preserves on-disk content; the gate is only triggered on `write_note` calls with `actor="ai"`. Source: `CONSTRAINTS.md` C-02.
- **C-12 · Result return at pipeline boundaries** — The new `_sweep_misplaced_md` helper in `pipelines/capture.py` (if extracted as a function) must return `Result[WriteOutcome]`. The new predicates in `vault/paths.py` return plain `bool` (consistent with the existing `_is_in_managed_attachment` / `_is_managed_summaries_area` twins — those are not pipeline boundaries). Source: `CONSTRAINTS.md` C-12.
- **C-13 · Audit every decision** — The misplaced-md sweep writes an `audit_log` row mirroring the CLUELESS non-md row: `pipeline="capture"`, `stage="store"`, `outcome="MISPLACED"`, non-empty `source_ids`, a `reasoning` string. Source: `CONSTRAINTS.md` C-13; design doc §Guardrail Checklist.
- **C-06 / C-07 · No thresholds in code, prompts as YAML** — Not applicable. No LLM call and no threshold added in this task. Source: `CONSTRAINTS.md` C-06, C-07.
- **C-17 · No module-scope CONFIG import in tests** — All tests construct `VaultConfig(root=tmp_path)` directly. The new predicates in `vault/paths.py` take `vault_cfg` explicitly. Source: `CONSTRAINTS.md` C-17.
- **CLAUDE.md: monkeypatch the importing module (TD-033)** — Tests that stub `move_note` when testing `scan_capture`'s sweep must patch `pipelines.capture.move_note`, not `vault.writer.move_note`. Tests that stub `_is_ai_output` when testing `_should_skip` must patch `vault.watcher._is_ai_output` (if it is imported at module top). Source: CLAUDE.md TD-033.
- **CLAUDE.md: do NOT add AI-output names to `IGNORE_DIRS`** — AI-output exclusion is a capture-path skip only. Adding to `IGNORE_DIRS` would blind FTS search. Source: T1 consumer obligation; design doc §Guardrail Checklist.
- **CLAUDE.md: `logging` is `%s`-style** — Any new `_log.*` lines in `vault/watcher.py` must use `%s` format strings, not keyword arguments. Source: CLAUDE.md "What Claude gets wrong."
- **CLAUDE.md: vault-relative paths from `self._root` in watcher** — Any relpath computation in the watcher uses `path.relative_to(self._root)`, not CONFIG. Source: CLAUDE.md "What Claude gets wrong."
- **DECISION-029: `.summaries/` writes set `type=attachment-summary`** — The misplaced-md sweep creates no sibling; the later in-inbox `capture_file` run uses the normal pipeline which already sets the type via the CLUELESS path. No new obligation here beyond confirming this chain is preserved. Source: DECISION-029.
- **[REQUIRES: T1]** — `VaultConfig.ai_output_dirs` must exist before `_is_ai_output` compiles. T1 must land before T4. Source: design doc build order.

---

### Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|---|---|---|
| A1 | `VaultConfig.ai_output_dirs` is a `@property` on `VaultConfig` returning `(briefings_dir, synthesis_dir, documentation_dir)` as a `tuple[str, ...]`, added by T1 | Design doc T1 §"Recommended Option A"; T1 spec §Component 3 "Interface shape" | If the property is absent, named differently, or returns a list instead of a tuple, `_is_ai_output` will have an `AttributeError` at first call |
| A2 | `_should_skip` in `vault/watcher.py` currently returns True for: managed attachment non-`.md` files, dotfiles, `.sync-conflict-*` files, and paths whose parts intersect `IGNORE_DIRS` — and for nothing else | Direct code read of `src/vault/watcher.py:124–141` | If `_should_skip` has other conditions not listed here, the new `_is_ai_output` check must be placed at the correct position in the evaluation order |
| A3 | The `dirnames[:]` in-place prune in `scan_non_md_drops` (line ~124) and `scan_vault` (line ~183) currently prunes by: `IGNORE_DIRS` membership, dotfile rule with `_DOT_ALLOWLIST`, and symlink check — and no other names | Direct code read of `src/vault/indexer.py:104–153` and `156–210` | If additional name-based prunes already exist, the AI-output prune must be added consistently alongside them |
| A4 | `scan_capture`'s `summary.added` loop (lines ~964–980) dispatches every added `.md` entry directly to `capture_file` with no pre-dispatch location check | Direct code read of `src/pipelines/capture.py:964–980` | If a location check or misplaced filter already exists in the loop, T4 would create a duplicate and must instead extend the existing check |
| A5 | `move_note` is already imported at the top of `vault/watcher.py` (post Brief #4 refactor) and at module scope in `pipelines/capture.py` or accessible via lazy import — making it patchable as `vault.watcher.move_note` / `pipelines.capture.move_note` | STATE.md "Brief #4" notes top-level imports in `watcher.py`; `capture.py` imports confirmed at code read | If `move_note` is only lazy-imported inside a function in `capture.py`, the monkeypatch target changes — must patch inside the function's local namespace, which is not standard |
| A6 | `documents.delete_by_path` is importable from `storage.documents` and takes `(vault_path: str, db_path: Path)` returning `Result[int]` | Design doc T4 §Implications — "DB row: a swept md … the old row is removed/replaced"; confirmed by existing usage in `scan_capture` at line ~1050 | If the signature differs or the function is missing, the orphan-cleanup step would need a different DB call |
| A7 | A misplaced `.md` at `Projects/<file>.md` (bare root, no subfolder) is NOT yet in the `documents` table when `scan_capture` processes it — because it appears in `summary.added` (not `summary.modified`) | Design doc T4 §Implications — "a `.md` in bare `Projects/` becomes a `VaultEntry`, lands in `summary.added`" | If the path already appeared in a prior scan (pre-T4), it would be in `summary.modified`, not `summary.added`. T4's sweep must also cover the `modified` loop for this edge case, or the sweep is delayed by one scan cycle |
| A8 | `vault_cfg.inbox_path` is accessible from the `PipelineContext` via `ctx.config.vault.inbox_path` at the point in `scan_capture` where the sweep is added | Confirmed by existing usage at `capture.py:702` (`vault_cfg.inbox_path in src.parents`) | If `vault_cfg` is not in scope at that point in `scan_capture`, the sweep code cannot compute the inbox destination |
| A9 | The `watcher.py` module-level import of `_is_in_managed_attachment` from `vault.paths` (line ~39) establishes the precedent that new predicates added to `vault/paths.py` should also be imported at module top in `watcher.py` — making them patchable as `vault.watcher.<name>` in tests | STATE.md "Brief #4" import refactor; direct code read of `watcher.py:39` | If a new predicate is only lazily imported inside `_should_skip`, the TD-033 patch target changes and existing watcher tests that patch other names would not establish the pattern |

---

### Component dependency order

#### 1. Add `_is_ai_output` predicate to `vault/paths.py`

**Goal.** Give every call site a single named function to ask "is this path inside an AI-output folder the system writes to itself?" so the rule can never drift across the watcher and the scanner.

**Build.**

In `src/vault/paths.py`, after `_is_managed_summaries_area` and before `_location_context`, add a new predicate function `_is_ai_output`.

Signature: `_is_ai_output(path: Path, vault_cfg: VaultConfig) -> bool`

Logic (in plain English):
- Iterate over the string parts of `path` (i.e. `path.parts`).
- For each part, check if it is in `vault_cfg.ai_output_dirs` (the tuple T1 adds: `briefings_dir`, `synthesis_dir`, `documentation_dir`).
- Return True on the first match; return False if no part matches.

The match is by name anywhere in the path — consistent with how T5 handles `attachment`/`.summaries` name exclusion (same documented limitation: a user folder coincidentally named `Briefings` inside a project would also match). Document this limitation in the docstring.

The function is pure path arithmetic: no filesystem I/O, no `mkdir`, no `exists()`, no CONFIG import.

**Depends on.** T1 (`VaultConfig.ai_output_dirs` must exist on the config model).

**Assumes.** A1.

**Interface shape.**
- `_is_ai_output(path: Path, vault_cfg: VaultConfig) -> bool`
- Same signature convention as the existing `_is_in_managed_attachment` and `_is_managed_summaries_area` twins.
- In-process. No adapter needed; test directly with `VaultConfig(root=tmp_path)`.
- Export in `__all__` if `vault/paths.py` maintains one (follow the existing pattern).

**Done when.**
- `_is_ai_output(tmp_path / "Briefings" / "2026-06.md", vault_cfg)` returns True.
- `_is_ai_output(tmp_path / "Synthesis" / "week1.md", vault_cfg)` returns True.
- `_is_ai_output(tmp_path / "Documentation" / "Alpha.md", vault_cfg)` returns True.
- `_is_ai_output(tmp_path / "Projects" / "Alpha" / "note.md", vault_cfg)` returns False.
- `_is_ai_output(tmp_path / "inbox" / "drop.pdf", vault_cfg)` returns False.
- With a custom override `VaultConfig(root=tmp_path, briefings_dir="Reports")`: `_is_ai_output(tmp_path / "Reports" / "daily.md", vault_cfg)` returns True (property is live, not cached).
- No filesystem side effects: no directories are created in `tmp_path` after the call.
- No CONFIG import in the function body.

---

#### 2. Add `_is_misplaced` predicate to `vault/paths.py`

**Goal.** Give every call site a single named function to ask "is this path in a location that is not a real home for any file?" so the misplaced-vs-valid rule is defined once and cannot drift.

**Build.**

In `src/vault/paths.py`, after `_is_ai_output`, add a new predicate function `_is_misplaced`.

Signature: `_is_misplaced(path: Path, vault_cfg: VaultConfig) -> bool`

Logic (in plain English):
1. If the path is inside the inbox (`vault_cfg.inbox_path in path.parents` or `path.parent == vault_cfg.inbox_path`): return False. (Inbox is a valid home.)
2. If `_is_ai_output(path, vault_cfg)` is True: return False. (AI-output folders are valid locations for the system's own files.)
3. If `vault_cfg.projects_path in path.parents`:
   - Compute `rel = path.relative_to(vault_cfg.projects_path)`.
   - If `len(rel.parts) >= 2`: return False. (A real project subfolder exists — e.g. `Projects/Alpha/note.md` has `rel.parts == ("Alpha", "note.md")`, length 2.)
   - Else: return True. (Bare root drop — e.g. `Projects/note.md` has `rel.parts == ("note.md",)`, length 1.)
4. If `vault_cfg.domain_path in path.parents`:
   - Same logic: `len(rel.parts) >= 2` → False (real domain subfolder), else → True.
5. Return False. (Path is outside all managed locations — root-level or unknown; not "misplaced" in the sense the system handles.)

Critical design note: This function must NOT call `_location_context`. The `_location_context` function treats `Projects/<file>.md` as `("project", "<file>")` — creating a phantom project named after the file. `_is_misplaced` avoids this trap by directly testing `len(rel.parts) >= 2`.

The function is pure path arithmetic. No filesystem I/O, no CONFIG import.

**Depends on.** Component 1 (`_is_ai_output` must exist; `_is_misplaced` calls it in step 2).

**Assumes.** A1 (for the `_is_ai_output` call).

**Interface shape.**
- `_is_misplaced(path: Path, vault_cfg: VaultConfig) -> bool`
- Same signature convention as the existing path-predicate twins.
- In-process. Test directly with `VaultConfig(root=tmp_path)`.

**Done when.**
- `_is_misplaced(tmp_path / "Projects" / "note.md", vault_cfg)` returns True (bare root drop, length 1).
- `_is_misplaced(tmp_path / "Projects" / "Alpha" / "note.md", vault_cfg)` returns False (real project, length 2).
- `_is_misplaced(tmp_path / "Domain" / "Finance" / "note.md", vault_cfg)` returns False.
- `_is_misplaced(tmp_path / "Domain" / "note.md", vault_cfg)` returns True.
- `_is_misplaced(tmp_path / "inbox" / "drop.pdf", vault_cfg)` returns False (inbox is not misplaced).
- `_is_misplaced(tmp_path / "Briefings" / "daily.md", vault_cfg)` returns False (AI-output is not misplaced).
- `_is_misplaced(tmp_path / "Projects" / "Alpha" / "attachment" / "report.pdf", vault_cfg)` returns False (deep path, `len(rel.parts) >= 2`).
- No filesystem side effects. No CONFIG import.

---

#### 3. Wire `_is_ai_output` into `_should_skip` in `vault/watcher.py`

**Goal.** Make the live watcher silently drop any file event for a path inside an AI-output folder, so a briefing or synthesis note is never dispatched to the capture callback — and so the skip is observable via a log line.

**Build.**

In `src/vault/watcher.py`:

1. Add `_is_ai_output` to the module-level import from `vault.paths` (alongside the existing `_is_in_managed_attachment` import on line ~39). This makes it patchable as `vault.watcher._is_ai_output` in tests, following TD-033.

2. Inside `_should_skip(self, path: Path) -> bool`, add a new condition after the existing `_is_in_managed_attachment` check and before the dotfile check:

   ```
   if _is_ai_output(path, self._vault_config):
       _log.debug("watcher.skip.ai_output path=%s", path.name)
       return True
   ```

   The log line uses `%s` positional formatting (CLAUDE.md `logging` rule). The message key `watcher.skip.ai_output` follows the `module.action.detail` convention established in the file.

No other changes to `_should_skip` or any event handler.

**Depends on.** Component 1 (`_is_ai_output` must exist in `vault/paths.py` before the import compiles).

**Assumes.** A2 (structure of `_should_skip`), A9 (module-level import pattern established).

**Done when.**
- A `FileCreatedEvent` for a path inside `Briefings/` does NOT fire the `_on_create` callback (the watcher returns without dispatching).
- A `FileCreatedEvent` for a path inside `Projects/Alpha/note.md` still fires `_on_create` normally (no regression on valid paths).
- The watcher emits a `debug`-level log line containing `watcher.skip.ai_output` and the filename when an AI-output path is skipped.
- The log line uses `%s` formatting (no kwargs in the `_log.debug` call).

---

#### 4. Wire AI-output dirname prune into `scan_non_md_drops` in `vault/indexer.py`

**Goal.** Make the batch non-`.md` scanner skip AI-output folders during the walk, so a PDF dropped in `Synthesis/` is never added to the capture queue.

**Build.**

In `src/vault/indexer.py`, in the `scan_non_md_drops` function, extend the `dirnames[:]` in-place list comprehension to exclude dirnames that are in the AI-output folder name set.

The prune must:
- Retrieve the AI-output folder names: `ai_output_dirs = vault_config.ai_output_dirs` (the T1 tuple). This is a local variable set once before the `root.walk()` loop.
- In the `dirnames[:]` comprehension, add `and d not in ai_output_dirs` alongside the existing `d not in IGNORE_DIRS` condition.

Important: this test is against `d` (the raw dirname string), not against an absolute path — consistent with how `IGNORE_DIRS` is applied. This means the prune is name-based and applies at any depth (same accepted limitation as T5's `attachment`/`.summaries` exclusion and the existing `IGNORE_DIRS` pruning).

No other changes to `scan_non_md_drops`.

**Depends on.** T1 (`vault_config.ai_output_dirs` must exist on `VaultConfig`); Component 1 (conceptually defines the predicate, though the prune uses the dirname string directly, not `_is_ai_output`, for efficiency in the tight loop).

**Assumes.** A1, A3.

**Done when.**
- `scan_non_md_drops` with a vault containing `Synthesis/report.pdf` returns a list that does NOT contain the `Synthesis/report.pdf` path.
- `scan_non_md_drops` with a vault containing `inbox/doc.docx` still returns `inbox/doc.docx` (no regression on valid inbox drops).
- `scan_non_md_drops` with a vault containing `Projects/Alpha/attachment/report.pdf` still skips it via the existing `_is_in_managed_attachment` Rule 1 (no regression).
- No new `IGNORE_DIRS` member was added.

---

#### 5. Wire AI-output dirname prune into `scan_vault` in `vault/indexer.py`

**Goal.** Make the batch `.md` scanner skip AI-output folders during the walk, so a briefing `.md` is never added to `summary.added` and dispatched to capture.

**Build.**

In `src/vault/indexer.py`, in the `scan_vault` function, extend the `dirnames[:]` in-place list comprehension in the same way as Component 4.

Retrieve `ai_output_dirs` once before the `root.walk()` loop. The comprehension currently reads config for `_inbox_dir` already (line ~167-172); `ai_output_dirs` is retrieved similarly via `CONFIG.main.vault.ai_output_dirs` when `root is None`, or via a passed-in config object if the signature is extended.

Design decision — `scan_vault` signature: `scan_vault` currently takes only `root: Path | None`. To get `ai_output_dirs` without a module-scope CONFIG import, two options exist:
- Extend the signature to `scan_vault(root: Path | None = None, vault_cfg: VaultConfig | None = None)` — the preferred approach for testability (C-17).
- Lazy-import CONFIG inside the function (already the pattern for `root is None` — acceptable for production, but test-hostile).

Both are acceptable. Leaning toward option A (extend signature with `vault_cfg: VaultConfig | None = None`) because it matches how `scan_non_md_drops` already takes `vault_config` explicitly — making `scan_vault` symmetric and testable without CONFIG.

If the signature is extended, the one call site in `scan_capture` at `capture.py:954` must pass `vault_cfg=CONFIG.main.vault` (lazy-imported in the same block where CONFIG is already loaded).

**Depends on.** T1 (`vault_config.ai_output_dirs`); Component 4 (same pattern, implemented first for consistency reference).

**Assumes.** A1, A3.

**Decisions.**
- Q: Extend `scan_vault(root, vault_cfg=None)` or keep signature and lazy-import CONFIG for `ai_output_dirs`? Options: extend signature (symmetric with `scan_non_md_drops`, testable without real vault, 1 call-site update in `scan_capture`) / lazy CONFIG import (no signature change, test-hostile for C-17). Leaning extend signature — consistent, C-17-clean.

**Done when.**
- `scan_vault` with a vault containing `Briefings/2026-01.md` returns a `Success` whose `entries` list does NOT contain the briefing path.
- `scan_vault` with a vault containing `Projects/Alpha/note.md` still returns that entry (no regression).
- If signature is extended: `scan_vault(_root, vault_cfg=CONFIG.main.vault)` call in `scan_capture` compiles and passes the no-regression suite.
- No new member added to `IGNORE_DIRS`.

---

#### 6. Add misplaced-md sweep to `scan_capture` in `pipelines/capture.py`

**Goal.** Make the batch scanner detect `.md` notes dropped in genuinely misplaced locations and move them to inbox before running the capture pipeline — so every misplaced `.md` is treated as a normal inbox drop rather than being captured in place.

**Build.**

In `src/pipelines/capture.py`, in the `scan_capture` function:

1. Add `_is_misplaced` and `move_note` to the function's imports. `_is_misplaced` is imported from `vault.paths`. `move_note` is imported from `vault.writer`. Both are added at the top of `scan_capture`'s lazy-import block (where `scan_vault`, `detect_changes`, etc. are already imported), so they are patchable as `pipelines.capture._is_misplaced` and `pipelines.capture.move_note` in tests (TD-033).

2. In the `summary.added` loop (currently at lines ~964–980), before calling `capture_file(path, context=ctx)`, add a pre-dispatch misplaced-md sweep:
   - Compute `vault_cfg = CONFIG.main.vault` (already in scope in the function).
   - If `path.suffix.lower() == ".md"` AND `_is_misplaced(path, vault_cfg)`:
     - Compute the inbox destination: `inbox_dst = vault_cfg.inbox_path / path.name`. Handle collision (same `-1/-2` pattern used in the CLUELESS branch at `capture.py:707-710`).
     - Clean up any stale DB row at the old path: call `documents.delete_by_path(to_vault_path(path), db_path=_db_path)`.
     - Move the file to inbox: call `move_note(path, inbox_dst, actor="ai")`.
     - On `Failure` from `move_note`: log a warning and skip (do not attempt capture of a file that failed to move).
     - Write an audit row: `audit.write(AIDecision(action="capture:sweep", confidence=1.0, reasoning="Misplaced md swept to inbox", source_ids=[to_vault_path(path)]), pipeline="capture", stage="store", outcome="MISPLACED", db_path=_db_path)`. Use `%s`-style log formatting for any `_log.*` lines.
     - Reassign `path = inbox_dst` so the subsequent `capture_file(path, context=ctx)` runs on the new inbox location.

3. The `summary.modified` loop does NOT need the same sweep. Explanation: a misplaced `.md` that already appeared in a prior scan (pre-T4) would be in `summary.modified`, not `summary.added`. This is an accepted one-scan-lag edge case for the MVP. Document it with a `# NOTE:` comment.

**Depends on.** Components 1, 2 (`_is_misplaced` and `_is_ai_output` must exist in `vault/paths.py`); Component 5 (`scan_vault` must already prune AI-output dirs so they never appear in `summary.added`).

**Assumes.** A4 (no pre-existing misplaced check in the loop), A5 (`move_note` importable and patchable), A6 (`delete_by_path` available), A7 (misplaced `.md` appears in `summary.added`), A8 (`vault_cfg` accessible from context).

**Decisions.**
- Q: Should the sweep also cover `summary.modified`? Options: yes (handles the one-scan-lag edge case — costs one extra `_is_misplaced` call per modified entry per scan) / no (simplest, MVP-safe, document the lag). Leaning no for MVP; document the lag with a comment.

**Done when.**
- A `.md` file at `Projects/stray.md` (bare root) is moved to `inbox/stray.md` by `scan_capture` and then captured from inbox. The file no longer exists at `Projects/stray.md` after the scan.
- A `.md` file at `Projects/Alpha/note.md` (real project, `len(rel.parts) == 2`) is NOT swept — it is captured in place (no regression).
- An `audit_log` row exists with `pipeline="capture"`, `stage="store"`, `outcome="MISPLACED"`, and `source_ids` containing the vault-relative path of the swept file.
- No stale `documents` row exists for the old pre-sweep vault path after the scan completes (the `delete_by_path` call cleaned it up, or it was absent).
- `scan_capture` returns `Success` (a list of `WriteOutcome` objects), never raw `None` — the Result constraint is honoured on the new branch.
- `move_note` is patched as `pipelines.capture.move_note` in tests (not `vault.writer.move_note`).

---

#### 7. Add unit tests for the two new predicates (`vault/paths.py`)

**Goal.** Confirm that `_is_ai_output` and `_is_misplaced` produce the correct yes/no answers for all cases, respect the `ai_output_dirs` config override, and perform no filesystem side effects — and that C-17 (no module-scope CONFIG import) is honoured.

**Build.**

Add a new test class `TestIsAiOutputAndMisplaced` in `tests/test_vault/test_paths.py` (or `test_paths_predicates.py` if the file would grow too large). All tests construct `VaultConfig(root=tmp_path)` directly — no `from core.config import CONFIG` at module scope.

Tests for `_is_ai_output`:

1. `test_is_ai_output_briefings_returns_true` — path inside `Briefings/` returns True.
2. `test_is_ai_output_synthesis_returns_true` — path inside `Synthesis/` returns True.
3. `test_is_ai_output_documentation_returns_true` — path inside `Documentation/` returns True.
4. `test_is_ai_output_projects_returns_false` — path inside `Projects/Alpha/note.md` returns False.
5. `test_is_ai_output_inbox_returns_false` — path inside `inbox/` returns False.
6. `test_is_ai_output_custom_dir_override` — `VaultConfig(root=tmp_path, briefings_dir="Reports")`: path inside `Reports/` returns True; path inside `Briefings/` returns False.
7. `test_is_ai_output_no_filesystem_side_effects` — call on a path under `tmp_path` with no real dirs; assert no new files or dirs appear.

Tests for `_is_misplaced`:

8. `test_is_misplaced_bare_projects_root_returns_true` — `Projects/note.md` (no subfolder) returns True.
9. `test_is_misplaced_bare_domain_root_returns_true` — `Domain/note.md` returns True.
10. `test_is_misplaced_real_project_returns_false` — `Projects/Alpha/note.md` returns False.
11. `test_is_misplaced_real_domain_returns_false` — `Domain/Finance/note.md` returns False.
12. `test_is_misplaced_inbox_returns_false` — `inbox/drop.pdf` returns False.
13. `test_is_misplaced_ai_output_returns_false` — `Briefings/daily.md` returns False (AI-output is not misplaced).
14. `test_is_misplaced_deep_project_path_returns_false` — `Projects/Alpha/attachment/report.pdf` returns False (`len(rel.parts) >= 2`).
15. `test_is_misplaced_no_filesystem_side_effects` — same no-IO guard.

**Depends on.** Components 1 and 2.

**Assumes.** None (pure unit test of pure functions).

**Done when.**
- All 15 tests pass under `uv run pytest tests/test_vault/ -m "not smoke"`.
- No `from core.config import CONFIG` at module scope in the new test file.
- `uv run pytest tests/ -m "not smoke"` still passes with no regressions (count ≥ 798, current baseline per STATE.md).

---

#### 8. Add unit tests for `_should_skip` AI-output exclusion (watcher)

**Goal.** Confirm that the watcher's `_should_skip` now returns True for any AI-output path, and that the skip is observable via a debug log line — without regressing existing skip behaviour.

**Build.**

In `tests/test_vault/test_watcher.py`, add a new test class or extend the existing `_should_skip` test group. All tests construct `VaultConfig(root=tmp_path)` directly.

1. `test_should_skip_briefings_path_returns_true` — construct a `_VaultEventHandler` with a vault root; assert `_should_skip(tmp_path / "Briefings" / "daily.md")` returns True.
2. `test_should_skip_synthesis_path_returns_true` — same for `Synthesis/`.
3. `test_should_skip_documentation_path_returns_true` — same for `Documentation/`.
4. `test_should_skip_valid_project_path_returns_false` — `Projects/Alpha/note.md` still returns False.
5. `test_should_skip_ai_output_emits_debug_log` — use `caplog` (pytest logging fixture) to assert a debug record containing `watcher.skip.ai_output` is emitted when an AI-output path is tested. Confirms the log line exists and uses the correct key.
6. `test_should_skip_ai_output_patch_target` — patch `vault.watcher._is_ai_output` (not `vault.paths._is_ai_output`) and confirm the watcher uses the patched version. This is the TD-033 guard test.

**Depends on.** Component 3.

**Assumes.** A2, A9.

**Done when.**
- All tests pass under `uv run pytest tests/test_vault/test_watcher.py -m "not smoke"`.
- No patch target in the new tests uses `vault.paths._is_ai_output` — all use `vault.watcher._is_ai_output`.
- The full suite still passes (count ≥ 798).

---

#### 9. Add unit tests for AI-output prune in `scan_non_md_drops` and `scan_vault`

**Goal.** Confirm that the batch scanners do not return paths inside AI-output folders, and do not regress on valid paths — including the existing inbox and `.summaries/` allowlist behaviour.

**Build.**

In `tests/test_vault/test_indexer.py` (or a new `test_indexer_ai_output.py`), add tests using a fixture vault tree in `tmp_path`. All construct `VaultConfig(root=tmp_path)` directly.

For `scan_non_md_drops`:

1. `test_scan_non_md_drops_excludes_briefings` — create `Briefings/report.pdf` in `tmp_path`; assert the result list does not contain it.
2. `test_scan_non_md_drops_excludes_synthesis` — same for `Synthesis/data.xlsx`.
3. `test_scan_non_md_drops_includes_inbox_drop` — create `inbox/doc.docx`; assert it IS included (no regression on valid inbox drops).
4. `test_scan_non_md_drops_excludes_attachment_per_rule1` — create `Projects/Alpha/attachment/report.pdf`; assert it is excluded via the existing `_is_in_managed_attachment` Rule 1 (no regression).

For `scan_vault`:

5. `test_scan_vault_excludes_briefings_md` — create `Briefings/daily.md`; assert the returned `entries` list does not contain it.
6. `test_scan_vault_excludes_synthesis_md` — same for `Synthesis/week1.md`.
7. `test_scan_vault_includes_project_md` — create `Projects/Alpha/note.md`; assert it IS included (no regression).
8. `test_scan_vault_includes_inbox_summaries_sibling` — create `inbox/.summaries/doc.pdf.md`; assert it IS included (existing `_DOT_ALLOWLIST` + `inbox_dir` rule must not be broken).

**Depends on.** Components 4 and 5.

**Assumes.** A3.

**Done when.**
- All tests pass under `uv run pytest tests/test_vault/ -m "not smoke"`.
- Full suite still passes (count ≥ 798).

---

#### 10. Add integration tests for the misplaced-md sweep in `scan_capture`

**Goal.** Confirm the end-to-end sweep: a misplaced `.md` is detected, moved to inbox, audited, and then captured from its new inbox location — with no orphan DB row at the old path.

**Build.**

In `tests/test_pipelines/test_capture.py` (or a new `test_capture_misplaced.py`), add a test class `TestScanCaptureMisplacedMd`. All tests construct `VaultConfig(root=tmp_path)` directly and patch `pipelines.capture.move_note`, `pipelines.capture._is_misplaced`, `pipelines.capture.capture_file`, and `pipelines.capture.audit` as `pipelines.capture.*` (TD-033).

1. `test_scan_capture_sweeps_bare_projects_md_to_inbox` — create `Projects/stray.md` in a minimal vault fixture; run `scan_capture`; assert the file now exists at `inbox/stray.md` and not at `Projects/stray.md`. Assert `move_note` was called with `(projects_stray_path, inbox_stray_path)`.

2. `test_scan_capture_audit_row_written_for_sweep` — same setup; assert `audit.write` was called with `outcome="MISPLACED"` and `source_ids` containing the vault-relative path of the original location.

3. `test_scan_capture_deletes_stale_db_row_after_sweep` — pre-populate the `documents` table with a row for `Projects/stray.md`; run `scan_capture`; assert that row no longer exists (verify via `documents.get_by_path`).

4. `test_scan_capture_does_not_sweep_real_project_md` — create `Projects/Alpha/note.md`; run `scan_capture`; assert `move_note` was NOT called (file stays in place).

5. `test_scan_capture_sweep_failure_logged_not_raised` — patch `move_note` to return `Failure(error="disk error", recoverable=False)`; run `scan_capture`; assert the function returns `Success(...)` (sweep failure is logged, not propagated) and the original path is not passed to `capture_file`.

6. `test_scan_capture_captures_swept_file_from_inbox` — full sweep + capture: after the sweep, assert `capture_file` was called with the new inbox path (not the original misplaced path).

7. `test_scan_capture_result_type_is_success` — assert `scan_capture` returns a `Result` (specifically `Success`) on the misplaced-md path, never raw `None` (C-12 guard).

**Depends on.** Component 6 (the sweep must exist before these tests can pass).

**Assumes.** A4–A8.

**Done when.**
- All seven tests pass under `uv run pytest tests/test_pipelines/ -m "not smoke"`.
- No patch target uses `vault.writer.move_note` — all use `pipelines.capture.move_note`.
- No patch target uses `vault.paths._is_misplaced` — uses `pipelines.capture._is_misplaced`.
- Full suite passes (count ≥ 798).

---

### Handoff notes

- **[From T1 spec] Contract honored:** T4 iterates `vault_cfg.ai_output_dirs` (the tuple T1 adds) and never touches `IGNORE_DIRS`. The T1 consumer obligation is respected: the folder names are read from the computed property, never hardcoded as string literals in T4 code.

- **[Independent of T2/T3]:** T4 does not call `resolve_placement` and does not depend on the `Placement` dataclass. This task only decides "capture vs skip vs sweep-to-inbox" — never editable-vs-no-edit placement. The design doc build order confirms T4 depends only on T1, not on T2 or T3. T4 can be built in parallel with T2/T3 as long as T1 is complete.

- **Contract with T8 (MoveGuard):** T8 registers pipeline-initiated moves with a suppression registry to prevent the watcher from re-firing on them. The misplaced-md sweep in `scan_capture` calls `move_note` — this is a pipeline-initiated move. T8 must register the inbox destination before this `move_note` call fires, or the watcher will pick up the new file at `inbox/stray.md` as a fresh create event. Since `scan_capture` runs while the watcher is active in production, T8's guard must cover `move_note` calls from pipelines, not just from `_store_nonmd`. The planner for T8 should note this extension.

- **Contract with T10 (reconcile migration):** T4 does not create new sibling files. The misplaced-md sweep moves a `.md` note to inbox; no `attachment-summary` sibling is created by T4. T10 therefore has no new obligation from T4.

- **OQ-008 (human edits to excluded folders):** capture-excluding `Documentation/`, `Briefings/`, and `Synthesis/` makes future human edits there invisible. This is a known, logged trade-off (OPEN_QUESTIONS.md). The `_is_ai_output` predicate is the natural future hook: a future co-author phase can add a `Documentation/`-only modify listener that calls `_is_ai_output` to scope its subscription. No code action from T4 beyond defining the predicate.

- **One-scan-lag for already-indexed misplaced `.md` files:** If a `.md` was captured at `Projects/stray.md` before T4 lands, it will be in the `documents` table and `summary.modified` (not `summary.added`) on the first scan after T4. The misplaced sweep in Component 6 only covers `summary.added`. The file will not be swept until it appears as `added` again (e.g. after a delete + re-drop). This is an accepted MVP limitation documented with a `# NOTE:` comment in the code. A future pass can extend the sweep to `summary.modified`.

- **Name-match limitation for AI-output prune:** The dirname prune in Components 4 and 5 matches by name at any depth. A user folder coincidentally named `Briefings` inside a project (e.g. `Projects/Alpha/Briefings/`) would be skipped. This is the same accepted limitation T5 took for `attachment`/`.summaries`. Document in the docstring of `_is_ai_output` and in the inline comments at the prune site.

- **Suggested research for /research:**
  1. Verify that `VaultConfig.ai_output_dirs` (T1) exists on the config model at `src/core/config.py` before writing any code — grep for `ai_output_dirs` in the file. If T1 has not yet landed, T4 cannot compile.
  2. Confirm the exact structure of `_should_skip` at `src/vault/watcher.py:124–141` — specifically that no AI-output condition already exists — to determine the correct insertion point for the new check.
  3. Confirm the exact structure of the `dirnames[:]` list comprehension in `scan_non_md_drops` (lines ~124) and `scan_vault` (lines ~183) to determine where to add the `d not in ai_output_dirs` clause without breaking the existing `_DOT_ALLOWLIST` / `inbox_dir` logic.
  4. Confirm the exact structure of the `summary.added` loop in `scan_capture` (lines ~964–980) — verify no pre-existing location check exists — to determine the correct insertion point for the sweep.
  5. Confirm whether `move_note` is already imported at module scope in `capture.py` or only available via lazy import inside functions, to establish the correct TD-033 patch target.
  6. Confirm current test baseline count: `uv run pytest tests/ -m "not smoke" --co -q | tail -1` (must be ≥ 798 before any edits).

---

## T5 — capture_folder: exclude attachment/ + .summaries/ by name

### Purpose

When the system captures a dropped folder, it walks the folder and collects every file inside it. Today that walk picks up two kinds of files it should never touch: binaries already filed away by the system inside a hidden `attachment/` subfolder, and the AI-written summary cards that live in `.summaries/`. Re-capturing a `.summaries/` summary actively destroys data — it overwrites the summary's frontmatter and wipes the pointer back to the original binary file (`attachment_path`). This is the same class of bug that was already fixed for single-file scans (TD-AS-1); T5 closes the same hole for folder drops.

After this task, `_collect_folder_files` skips any path that passes through a folder named `attachment` or `.summaries`, reading those names from the config (exactly as the rest of the codebase does) rather than hardcoding them. As a side effect, the `file_count` written into the `batches` DB row becomes accurate — it counts only genuinely capturable files.

---

### Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `_collect_folder_files` | `src/pipelines/capture.py:1087–1104` | Private helper that rglobs a dropped folder and returns a filtered list of capturable `Path` objects, currently skipping dirs, dotfiles, and `IGNORE_DIRS`-named parts | This task modifies its signature and body — adds `vault_cfg: VaultConfig` parameter and extends the skip logic | Deep |
| `capture_folder` | `src/pipelines/capture.py:1230` | Async orchestrator that calls `_collect_folder_files` at two internal sites: line 1255 (initial collection before location detection) and line 1306 (re-collection after an LLM-routed folder move) | Both call sites receive `vault_cfg` from `ctx.config.vault`; both are updated to pass it as the new second argument | Deep |
| `VaultConfig.attachment_dir` | `src/core/config.py:83` | `str` Field, default `"attachment"` | Provides the folder name to skip for managed binary storage — read as `vault_cfg.attachment_dir` at the new skip check | Shallow |
| `VaultConfig.summaries_subdir` | `src/core/config.py:84` | `str` Field, default `".summaries"` | Provides the folder name to skip for AI summary cards — read as `vault_cfg.summaries_subdir` at the new skip check | Shallow |
| `IGNORE_DIRS` | `src/vault/indexer.py:42` | Frozen set of folder names always pruned by the vault walker (e.g. `.git`, `.obsidian`) | The existing membership check `any(part in IGNORE_DIRS for part in rel_parts)` stays unchanged; T5 extends it with a second `or` term, not a mutation of `IGNORE_DIRS` | Shallow |
| `scan_capture` | `src/pipelines/capture.py:918` | Batch scan function that calls `scan_vault` and `scan_non_md_drops`, not `_collect_folder_files` | Listed here to make explicit that `scan_capture` is NOT touched by T5 — it already has its own `.summaries/` skip via `_summaries_subdir` (capture.py:1009–1011) | Shallow |
| Existing `.summaries/` skip in `scan_capture` | `src/pipelines/capture.py:1009–1011` | Reads `CONFIG.main.vault.summaries_subdir` and skips any entry whose vault_path contains that name | The closest existing precedent for this task's config-sourced name-skip convention; T5 follows the same pattern inside `_collect_folder_files` | Shallow |
| `TestCaptureFolderFiles` / existing folder capture tests | `tests/test_pipelines/test_capture_phase9.py` (or equivalent) | Tests for `capture_folder` and `_collect_folder_files` | New tests are added as a new class (or new methods) in the same test file; existing tests must not regress | Shallow |

---

### Feature overview

The folder walker today applies one filter: skip any file whose path contains a part that belongs to `IGNORE_DIRS` (a module-level frozenset in the indexer). That filter was designed for system metadata folders like `.git` and `.obsidian`. It does not cover the two vault-managed folders — `attachment/` and `.summaries/` — because those are intentionally indexed by the search/reconcile pipeline and must not be globally suppressed.

T5 adds a second, local filter inside `_collect_folder_files` only. The filter reads the two folder names from `VaultConfig` (the same config object every other part of the system uses) and skips any file whose relative path contains either name as a component. The check is by string membership in `path.parts`, so a file at `subfolder/attachment/report.pdf` is skipped if `attachment` appears anywhere in its relative path — the same depth-agnostic behaviour the existing `IGNORE_DIRS` check already uses.

Because `_collect_folder_files` is a private helper with exactly two internal callers and no external callers, adding a `vault_cfg` parameter to its signature affects only those two lines. Both call sites are inside `capture_folder`, which already has access to `vault_cfg` via `ctx.config.vault` — making the threading cost zero beyond adding the argument.

The `batches.file_count` column is computed directly from `len(files)` and `len(new_files)` at the two call sites. Correcting the collector automatically corrects the count — no additional edit is needed.

Nothing about the indexer, the watcher, `IGNORE_DIRS`, `_DOT_ALLOWLIST`, or any reconcile stage is touched.

---

### Out of scope

- **Modifying `IGNORE_DIRS` or `_DOT_ALLOWLIST` in `vault/indexer.py`** — adding `attachment` or `.summaries` there would hide siblings from FTS search and break reconcile Stage 4 (DECISION-029). Explicitly out of scope per the design decision Anti-goal.
- **Any change to `scan_capture`, `scan_vault`, or `scan_non_md_drops`** — those already have their own skip logic for `.summaries/` and are not involved in folder drops.
- **Any change to `vault/watcher.py` or `vault/paths.py`** — T5 is deliberately scoped away from the path-predicate hotspot. All path-predicate work belongs to T2/T4.
- **Root-level `.summaries/` recognition in reconcile predicates** — T10 owns that. T5 does not write anything; it only prevents a destructive re-read.
- **AI-output folder exclusion (`Briefings/`, `Synthesis/`, `Documentation/`)** — T4 owns that. T5 covers `attachment/` and `.summaries/` only.
- **`.summaries/` skip in the watcher's `_should_skip`** — the watcher already handles binary-sync via dedicated `bin:` keys; the watcher-path exclusion is T4's concern.
- **Editable-vs-no-edit placement routing** — T2 and T3 own that. T5 only decides whether to collect a file at all.

---

### Constraints

- **Config-sourced names, never string literals (CLAUDE.md Extension-Point Rule)** — the two folder names must be read from `vault_cfg.attachment_dir` and `vault_cfg.summaries_subdir`, never hardcoded as `"attachment"` or `".summaries"`. Source: CLAUDE.md "New threshold or rule → edit a config file"; design doc T5 Guardrail Checklist row 1. The existing `scan_capture` precedent (capture.py:1009–1011) follows this exactly; T5 mirrors it.
- **C-12 · Result type exempt for private helpers** — `_collect_folder_files` is a private function returning `list[Path]`, not a public pipeline boundary. It is not subject to the `Success`/`Failure` return rule. The `capture_folder` orchestrator already returns `Result[list[WriteOutcome]]` and is unchanged. Source: CONSTRAINTS.md C-12 (scoped to public functions in `handlers/` and `pipelines/`); design doc T5 Guardrail Checklist row 2.
- **C-01 · Vault-only writes (protective)** — T5 performs no writes. It prevents harmful re-captures that would overwrite `.summaries/` siblings and wipe `attachment_path` frontmatter. Source: CONSTRAINTS.md C-01; design doc T5 Guardrail Checklist row 3.
- **C-13 · Audit (not applicable)** — T5 introduces no AI decision. Per-file audit rows are written downstream by `_capture_folder_files` / `capture_file` unchanged. Source: CONSTRAINTS.md C-13; design doc T5 Guardrail Checklist row 5.
- **C-17 · No module-scope CONFIG import in tests** — new tests must construct `VaultConfig(root=tmp_path)` directly and pass it to `_collect_folder_files`. The function receives `vault_cfg` explicitly, so no CONFIG singleton is imported either in the function body or in tests. Source: CONSTRAINTS.md C-17; design doc T5 Guardrail Checklist row 6.
- **Do not add names to `IGNORE_DIRS`** — the two folder names must NOT be added to `IGNORE_DIRS` in `vault/indexer.py`. That set is consumed by the indexer, `scan_vault`, and `scan_non_md_drops`; widening it would hide siblings from search and break reconcile Stage 4. Source: design doc T5 Options §"Option C" rejection; DECISION-029.
- **[REQUIRES: T1]** — `VaultConfig.attachment_dir` and `VaultConfig.summaries_subdir` already exist on the current model (they predate this spec — see STATE.md Phase 1.5 entry). T5 has no hard runtime dependency on T1's new fields; it reads only the two existing fields. T1's `no_edit_extensions` and `ai_output_dirs` additions are irrelevant to T5. The build-order note "T5 reads T1 VaultConfig fields" refers to the existing fields, confirming T5 is safe to implement without waiting for T1's new additions.

---

### Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|---|---|---|
| A1 | `_collect_folder_files` is defined at `src/pipelines/capture.py:1087` with signature `def _collect_folder_files(folder_path: Path) -> list[Path]` and has exactly two internal call sites: line 1255 (`files = _collect_folder_files(folder_path)`) and line 1306 (`new_files = _collect_folder_files(new_folder)`), with no other callers in the codebase | Design doc T5 Implications §"What files get touched" — "confirmed by grep: capture.py:1255, :1306; no test references the function directly" | A grep for `_collect_folder_files` in `src/` returns more than two matches, or a test file imports/calls it directly — requiring additional call-site updates or a different patching strategy |
| A2 | `VaultConfig.attachment_dir` is a `str` Field (default `"attachment"`) and `VaultConfig.summaries_subdir` is a `str` Field (default `".summaries"`) at `src/core/config.py:83–84`, and both fields exist on the current model without modification from T1 | Design doc T5 Implications §"What the key terms mean"; STATE.md Phase 1.5 entry confirming `summaries_subdir` was added there | If either field is renamed, has a different default, or does not exist on `VaultConfig`, the new skip check would silently compute wrong paths or fail with `AttributeError` |
| A3 | At the first call site (`capture.py:1255`), `vault_cfg` is NOT yet a local variable — the `vault_cfg = ctx.config.vault` assignment appears at line 1261, six lines later. However, `ctx` IS fully initialised at line 1252 (`context = await _build_default_context()` / `ctx = context`), so `ctx.config.vault` is available before line 1255 | Direct code read of `capture.py:1250–1262` | If `ctx` is not fully initialised at line 1252 (e.g. `_build_default_context()` is lazy), `ctx.config.vault` at line 1255 would raise `AttributeError`; or if `vault_cfg` is already assigned before line 1255 in some code path, the fix is simpler than described |
| A4 | At the second call site (`capture.py:1306`), `vault_cfg` IS already a local variable — it was assigned at line 1261, and line 1306 is inside the `case Success(value=new_folder)` branch which is unreachable before line 1261 | Direct code read of `capture.py:1261–1316` | If the code structure between lines 1261 and 1306 has changed (e.g. `vault_cfg` reassigned or deleted), the second call site may need a different approach |
| A5 | The existing skip check at `capture.py:1101` reads: `if any(part in IGNORE_DIRS for part in rel_parts): continue` — a single `any(...)` expression over a generator | Direct code read of `capture.py:1087–1104` | If the check has a different structure (e.g. a `for` loop with explicit `if`s, or an early-return), the extension pattern in Component 1 needs adjustment |
| A6 | `_collect_folder_files` is not imported or called anywhere in `tests/` — tests exercise it only through `capture_folder` end-to-end, so adding a required `vault_cfg` parameter does not break any existing test that calls `_collect_folder_files` directly | Design doc T5 Implications §"Module depth" — "no test references the function directly" | A grep for `_collect_folder_files` in `tests/` returns one or more matches — those test call sites would need updating |
| A7 | `IGNORE_DIRS` is imported at the top of `_collect_folder_files` via a lazy `from vault.indexer import IGNORE_DIRS` inside the function body (`capture.py:1092`) — it is not a module-level import in `capture.py` | Direct code read of `capture.py:1087–1104` | If `IGNORE_DIRS` is imported at module scope in `capture.py`, the lazy-import comment in Component 1 is wrong, and the change is simply an inline extension |

---

### Component dependency order

#### 1. Extend `_collect_folder_files` signature and skip logic

**Goal.** Make the folder walker skip any file that lives inside a folder named `attachment` or `.summaries`, reading those names from the config rather than hardcoding them — so the two vault-managed folder names can never drift out of sync between the walker and the config.

**Build.**

In `src/pipelines/capture.py`, modify `_collect_folder_files`:

1. Change the signature from `_collect_folder_files(folder_path: Path) -> list[Path]` to `_collect_folder_files(folder_path: Path, vault_cfg: VaultConfig) -> list[Path]`. Import `VaultConfig` at the top of the function (or at module scope — see Decisions below) if not already imported there.

2. At the top of the function body, before the `rglob` loop, derive a skip-name set from the two config fields:
   ```
   skip_names = {vault_cfg.attachment_dir, vault_cfg.summaries_subdir}
   ```
   This is a `set` of two strings (e.g. `{"attachment", ".summaries"}` with default config). It is computed once per call, not inside the loop.

3. In the existing `if any(part in IGNORE_DIRS for part in rel_parts): continue` check (currently at `capture.py:1101`), extend the condition to also skip paths that pass through a `skip_names` member:
   ```
   if any(part in IGNORE_DIRS or part in skip_names for part in rel_parts):
       continue
   ```
   This is an additive change to the single existing `any(...)` expression. The `IGNORE_DIRS` check remains first, as it is today.

4. Update the docstring to reflect the two new skip categories: "Skips: directories, dotfiles, IGNORE_DIRS parts, and any path passing through a folder named `vault_cfg.attachment_dir` or `vault_cfg.summaries_subdir`."

No other changes to the function body.

**Depends on.** None — this component has no code dependencies within T5. (VaultConfig fields `attachment_dir` and `summaries_subdir` already exist on the model; this component only reads them.)

**Assumes.** A2, A5, A7.

**Interface shape.**
- New signature: `_collect_folder_files(folder_path: Path, vault_cfg: VaultConfig) -> list[Path]`
- Private function; its only callers are inside `capture_folder` in the same file.
- The function is a total function (returns a list, possibly empty) — no change to its return contract.
- Dependency category: in-process (direct call; test directly with `VaultConfig(root=tmp_path)`).

**Decisions.**
- Q: Import `VaultConfig` at module scope in `capture.py` or inside the function as a lazy import? The existing import pattern for `VaultConfig` in `capture.py` should be checked by research. Options: module-scope import (standard; consistent with how other type hints are resolved at the top of the file) / lazy import inside the function body (consistent with the existing lazy `from vault.indexer import IGNORE_DIRS` inside this very function). Leaning module-scope because `VaultConfig` is likely already imported in `capture.py` for the `ctx.config.vault` usage, making a duplicate lazy import redundant. Research should confirm before coding.

**Done when.**
- `_collect_folder_files(Path("/vault/dropped"), vault_cfg)` called with a folder containing `subfolder/attachment/report.pdf` returns a list that does NOT include `report.pdf`.
- The same call with a folder containing `subfolder/.summaries/report.pdf.md` returns a list that does NOT include `report.pdf.md`.
- A folder containing `notes.docx` (at the root of the dropped folder, no `attachment/` or `.summaries/` in its relative path) IS still returned in the list.
- A folder containing only `attachment/report.pdf` returns an empty list (zero capturable files).
- With a custom `VaultConfig(root=tmp_path, attachment_dir="binaries", summaries_subdir=".cards")`: files under `binaries/` and `.cards/` are skipped; files under `attachment/` and `.summaries/` are NOT skipped (the config-sourced names are honored, not the hardcoded defaults).

---

#### 2. Update both call sites in `capture_folder` to pass `vault_cfg`

**Goal.** Thread the `vault_cfg` argument to both places inside `capture_folder` that call `_collect_folder_files`, so the new required parameter is satisfied without introducing any CONFIG singleton reference inside the helper.

**Build.**

In `src/pipelines/capture.py`, inside `capture_folder`:

**Call site 1 — line 1255 (initial collection, before the `vault_cfg` local variable is assigned).**

Currently: `files = _collect_folder_files(folder_path)` at line 1255.
The `vault_cfg` local variable is not yet assigned (it is assigned at line 1261). However, `ctx` is fully initialised at line 1252. The fix is to either:
- Move the `vault_cfg = ctx.config.vault` assignment from line 1261 to before line 1255 (preferred — makes the code read more naturally, and `vault_cfg` is used throughout the rest of the function anyway), then change the call to `_collect_folder_files(folder_path, vault_cfg)`, or
- Inline `ctx.config.vault` at the call site: `_collect_folder_files(folder_path, ctx.config.vault)` — acceptable if moving the assignment would disturb a meaningful structural comment.

Either approach is correct. Leaning toward moving the `vault_cfg` assignment earlier because it makes all downstream uses of `vault_cfg` in `capture_folder` structurally consistent (one place of definition, used everywhere from that point on).

**Call site 2 — line 1306 (re-collection after LLM-routed folder move).**

Currently: `new_files = _collect_folder_files(new_folder)` at line 1306.
`vault_cfg` is already a local variable at this point (defined at line 1261, and this call is inside the `case Success(value=new_folder)` branch which is after line 1261). Change to: `new_files = _collect_folder_files(new_folder, vault_cfg)`.

No other changes in `capture_folder`.

**Depends on.** Component 1 (the new signature must exist before the call sites can compile with the new argument).

**Assumes.** A1, A3, A4.

**Done when.**
- `capture_folder` compiles without `TypeError: _collect_folder_files() takes 1 positional argument but 2 were given` (obviously) and without `AttributeError` on `ctx.config.vault` at the first call site.
- A folder dropped into the vault that contains an `attachment/` subfolder produces a `batches` row whose `file_count` equals only the count of files NOT inside `attachment/` or `.summaries/` — confirmed by a DB-row check in the integration test.
- A folder dropped with zero capturable files (e.g. a folder containing only `attachment/report.pdf`) correctly returns `Success([])` with no `batches` row written (the empty guard at line 1256 fires before the batch insert).

---

#### 3. Add unit tests for the updated `_collect_folder_files`

**Goal.** Give the test suite a dedicated, CONFIG-free unit test class that verifies the new skip behaviour across the key scenarios — including the "no regression on plain folders" case — and confirms the config-sourced naming convention is honored.

**Build.**

In `tests/test_pipelines/` (the existing directory for capture tests), add a new test class `TestCollectFolderFiles` (either in the existing `test_capture_phase9.py` or a new `test_collect_folder_files.py` if that is cleaner). All tests must:
- Construct `VaultConfig(root=tmp_path)` directly — no `from core.config import CONFIG` at module scope (C-17).
- Build a minimal folder tree in `tmp_path` using `pathlib.Path.mkdir()` + `.write_text()` (test fixtures only — these are not vault writes, they are test scaffolding inside `tmp_path`).
- Call `_collect_folder_files(dropped_folder_path, vault_cfg)` directly.

The tests cover:

1. **`test_collect_folder_files_skips_attachment_subfolder`** — dropped folder contains `doc.docx` at root and `attachment/report.pdf` one level deep. Assert the returned list contains `doc.docx` and does NOT contain `report.pdf`. Assert list length is 1.

2. **`test_collect_folder_files_skips_summaries_subfolder`** — dropped folder contains `doc.docx` and `.summaries/doc.docx.md`. Assert `.summaries/doc.docx.md` is NOT in the returned list. Assert `doc.docx` IS in the list.

3. **`test_collect_folder_files_skips_nested_attachment`** — dropped folder contains `sub/attachment/binary.xlsx`. Assert `binary.xlsx` is NOT in the returned list (the skip applies at any depth, not just at the top level).

4. **`test_collect_folder_files_skips_nested_summaries`** — dropped folder contains `sub/attachment/.summaries/binary.xlsx.md`. Assert it is NOT in the returned list.

5. **`test_collect_folder_files_plain_folder_no_regression`** — dropped folder contains `notes.docx`, `budget.xlsx`, `slides.pptx` with no `attachment/` or `.summaries/` subtree. Assert all three paths are in the returned list (length 3). This is the regression guard.

6. **`test_collect_folder_files_empty_when_only_attachment`** — dropped folder contains only `attachment/report.pdf` and `attachment/.summaries/report.pdf.md`. Assert the returned list is empty.

7. **`test_collect_folder_files_config_sourced_names`** — construct `VaultConfig(root=tmp_path, attachment_dir="binaries", summaries_subdir=".cards")`. Dropped folder contains `binaries/report.pdf` and `attachment/doc.docx`. Assert `report.pdf` (inside `binaries/`) is skipped but `doc.docx` (inside `attachment/`, which is NOT the configured name) is NOT skipped. This is the proof that names are config-sourced, not hardcoded.

8. **`test_collect_folder_files_dotfiles_still_skipped`** — dropped folder contains `.hidden_file` (a dotfile by leaf name). Assert it is NOT in the returned list (existing dotfile guard must not regress).

9. **`test_collect_folder_files_ignore_dirs_still_skipped`** — dropped folder contains a subfolder in `IGNORE_DIRS` (e.g. `.git/config`). Assert `.git/config` is NOT in the returned list (existing `IGNORE_DIRS` guard must not regress).

**Depends on.** Components 1 and 2 (the updated function and call sites must compile first for the tests to import correctly).

**Assumes.** A2, A6 (no existing direct tests of `_collect_folder_files` to conflict with).

**Done when.**
- All nine tests pass under `uv run pytest tests/test_pipelines/test_collect_folder_files.py -m "not smoke"` (or equivalent path if added to an existing file).
- No `from core.config import CONFIG` appears at module scope in the new test file.
- `uv run pytest tests/ -m "not smoke"` still passes with no regressions (count ≥ 798, the current baseline per STATE.md).

---

### Handoff notes

- **Contract with T1 spec:** T5 reads `VaultConfig.attachment_dir` and `VaultConfig.summaries_subdir` — both of which exist on the current model BEFORE T1's changes land. T1 adds `no_edit_extensions` and `ai_output_dirs`, which T5 does not use. T5 and T1 are therefore fully independent and can be built in either order. The build-order note in the design doc places T5 first precisely because it has no dependency on any other T-task.

- **[Independent of T2/T3/T4]:** T5 makes no placement decision (that is T2/T3) and adds no capture-exclusion predicate in `vault/paths.py` (that is T4). This task only changes which files the folder walker hands to the pipeline. The downstream pipeline (which T3 will rewire) receives a corrected, cleaner input list.

- **`batches.file_count` accuracy is a free side effect:** The two `len(files)` / `len(new_files)` usages at `capture.py:1267` and `capture.py:1309` read directly from the corrected collector output. No additional edit is needed to fix the count.

- **Non-interference with the watcher's `bin:` binary sync:** The watcher owns `attachment/`-area binary sync via dedicated `bin:` debounce keys (CLAUDE.md "Two `_debounce` calls with same key cancel each other"). T5's folder-walker skip and the watcher's binary sync touch disjoint paths: the folder collector now skips the `attachment/`/`.summaries/` region, while the watcher's `bin:` keys track that same region. The two cannot collide on the same file. The design doc §Success criteria ("Non-interference (capture-vs-watcher)") asks the planner to confirm no shared-path test regressions in the existing phase-9 tests.

- **Over-exclusion accepted limitation:** A user folder literally named `attachment` or `.summaries` (at any depth inside a dropped folder) will be silently skipped. This is the same documented limitation that T4's `_is_ai_output` and the existing `IGNORE_DIRS` check accept. It is acceptable for the non-technical executive target user who does not name project folders this way.

- **Open uncertainty:** The design doc notes that `_collect_folder_files` is called at line 1255 before the `vault_cfg` local variable is assigned (at line 1261). The spec recommends moving the `vault_cfg = ctx.config.vault` assignment earlier. Research should confirm that no structural comment, log message, or other code block between lines 1252 and 1261 creates a reason NOT to hoist the assignment (e.g. a comment that explicitly scopes "Stage 2: determine location" as starting at line 1260). If hoisting is blocked for stylistic reasons, the inline `ctx.config.vault` at the call site is the fallback.

- **Suggested research for /research:**
  1. Confirm the exact body of `_collect_folder_files` at `src/pipelines/capture.py:1087–1104` has not changed since the design was written (specifically: verify the `any(part in IGNORE_DIRS for part in rel_parts)` check is still structured as a single `any()` expression and that `IGNORE_DIRS` is still lazy-imported inside the function body, not at module scope).
  2. Verify that `_collect_folder_files` has exactly two call sites in the codebase — grep for the name in `src/` and `tests/` to confirm A1 and A6.
  3. Confirm that `VaultConfig` is already imported (either at module scope or accessible) in `capture.py`, to determine whether a new import is needed for the `vault_cfg: VaultConfig` type annotation in the function signature.
  4. Read `capture.py:1250–1265` to confirm the exact point where `vault_cfg` is first assigned and verify A3 (that `ctx` is fully initialised before line 1255 in all code paths, including the `context is not None` fast path).
  5. Confirm current test baseline count: `uv run pytest tests/ -m "not smoke" --co -q | tail -1` (must be ≥ 798 before any edits).

---

## T6 — Watcher: re-home on user move (incl. T11 correlation_id verify)

### Purpose

Today, when the user drags a captured binary file from one project to another, the watcher tears down the AI-written summary card at the old location and walks away. The file's summary is gone, its project tags are stale, and its database record is orphaned. T6 fixes that: a cross-folder user move triggers a **re-home** — the summary card is written at the new location, the location tags are updated, the binary is placed in the right sub-folder for its type (visible root for editable office docs, hidden `attachment/` for PDFs and images), and the database record is updated — all without calling the AI. The content did not change, so re-summarizing would waste money and produce identical output.

After this task, dragging a file from `Projects/A/` to `Projects/B/` produces a correct, fully-wired summary card at `Projects/B/`, not a stale orphan at `Projects/A/`. Same-folder renames continue to work exactly as before. The `correlation_id` verification (T11) folds in as a test-only obligation — no code change is required beyond preserving the existing bind.

[REQUIRES: T2 + T8] — ship T6 in the same increment as T8; T6 is the sole consumer of `MoveGuard`.

---

### Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `_handle_binary_move` | `src/vault/watcher.py:344–449` | Handles watchdog `on_moved` events for binary files; currently splits into a same-folder rename branch (L357–419) and a cross-folder else branch (L420–449) that orphans the old sibling | T6 rewrites ONLY the else branch (L420–449); the same-folder rename branch is left byte-for-byte unchanged | Deep |
| `on_moved` dispatch | `src/vault/watcher.py:280–299` | Debounces `on_moved` events: fires `_handle_binary_move` via `bin:{dst}` key for binary moves, then fires the user `on_move` callback | T6 does NOT change `on_moved`; it only changes what `_handle_binary_move`'s else branch does | Deep |
| `_VaultEventHandler.__init__` | `src/vault/watcher.py:89–122` | Watcher event handler constructor; holds `self._root`, `self._vault_config`, `self._timers`, `self._lock`, `self._pending_folders`, `self._folder_tokens`, `self._folder_lock` | T6 adds `self._move_guard` attribute here (wired from T8's `MoveGuard`) | Deep |
| `_sibling_for` | `src/vault/watcher.py:54–73` | Returns `<binary.parent>/<summaries_subdir>/<binary.name>.md` — the sibling path anchored to the binary's **current** parent | Used in the re-home branch to compute `old_sibling_vp` for the DB lookup; also used by the MoveGuard-suppressed path. Note: the re-home branch must compute the **new** sibling path from `placement.sibling_dir / f"{dst.name}.md"` (anchored to the binary's FINAL parent), NOT from `_sibling_for(dst, cfg)` (which anchors to `dst.parent`, which equals `final_binary.parent` only for no-edit files, not editable ones) | Deep |
| `_vp()` local helper | `src/vault/watcher.py:352–355` | Defined inside `_handle_binary_move`; converts an absolute Path to a NFC-normalised vault-relative POSIX string without calling the CONFIG singleton | T6 must preserve and reuse this local helper in the rewritten else branch — never call the CONFIG-singleton `to_vault_path` from inside the watcher | Shallow |
| `bind_contextvars(correlation_id=...)` | `src/vault/watcher.py:346–347` | Binds a fresh `correlation_id` to the structlog context at the top of `_handle_binary_move`, before either branch runs | T6 preserves this bind verbatim; the re-home audit row inherits it in the same Timer thread. T11 verify-only: add no second bind | Shallow |
| `resolve_placement` | `src/vault/paths.py` (added by T2) | Pure function: `resolve_placement(file_path, target_type, target_name, vault_cfg) -> Placement(final_dir, sibling_dir, needs_move)` | T6 calls this with `dst` (the new binary location) to determine where the binary and its new sibling belong after the move | Shallow |
| `Placement` dataclass | `src/vault/paths.py` (added by T2) | Frozen dataclass: `final_dir: Path`, `sibling_dir: Path`, `needs_move: bool` | T6 reads `placement.final_dir`, `placement.sibling_dir`, and `placement.needs_move` to route the binary and build the new sibling path | Shallow |
| `_location_context` | `src/vault/paths.py:87–121` | Pure function returning `(type, name)` for a path: `("project", "Alpha")`, `("domain", "Finance")`, `("inbox", None)`, `(None, None)` | T6 calls `_location_context(dst, cfg)` to derive the new `loc_type` and `loc_name` for the `resolve_placement` call — mirrors what `apply_location_tags` does without going through the async pipeline stage | Shallow |
| `get_by_path` | `src/storage/documents.py:141–170` | Fetches the `DocumentRow` for a given `vault_path`; returns `Success(DocumentRow)`, `Success(None)`, or `Failure` | T6 uses this to look up the existing summary text and metadata from the DB row for the old sibling — never reads the on-disk source sibling, so a missing on-disk sibling (e.g. after a multi-hop move chain) does not block the re-home | Shallow |
| `DocumentRow` | `src/storage/documents.py:27–43` | Frozen dataclass mirroring one `documents` row: `id`, `vault_path`, `title`, `summary`, `note_type`, `confidence`, `content_hash`, `key_topics`, `batch_id`, `project`, `status`, `updated_by_human` | T6 reads `row.summary`, `row.note_type`, `row.confidence`, `row.content_hash`, `row.key_topics` to rebuild the new sibling card body | Shallow |
| `delete_by_path` | `src/storage/documents.py:198–...` | Deletes the `documents` row for a given `vault_path`; returns `Result[int]` (rowcount) | T6 uses this to remove the old sibling's DB row after the new sibling card has been written and the rename/upsert is complete. Alternative: use `rename_doc` to preserve the row id — see Decisions in Component 2 | Shallow |
| `rename as rename_doc` | `src/storage/documents.py` (imported in `watcher.py:37`) | Renames the `documents` row's `vault_path` in-place, preserving row id and FK links | If the spec uses `rename_doc` rather than delete+upsert, the old sibling row is updated to point at the new sibling path — preferred for id/FK continuity (DECISION-001) | Shallow |
| `move_attachment` | `src/vault/writer.py:241` | Moves a binary file on disk through the vault writer; returns `Result` | T6 calls `move_attachment(dst, final_binary)` when `placement.needs_move` is True (i.e. the file needs to move from `dst` to `placement.final_dir / dst.name`) | Shallow |
| `move_note` | `src/vault/writer.py:175` | Moves a `.md` file on disk and preserves its content; returns `Result` | T6 calls `move_note(old_sibling, new_sibling_path, actor="ai")` if the old sibling exists on disk — moves the card to the new location rather than deleting and rewriting | Shallow |
| `write_note` | `src/vault/writer.py:108` | Writes a `.md` note with metadata; enforces `updated_by_human` gate | T6 calls `write_note(new_sibling_path, body, meta, actor="ai")` when the old sibling does NOT exist on disk (the fallback rebuild path, sourcing content from the DB row) | Shallow |
| `audit_write` | `src/core/audit.py` | Writes one `audit_log` row | T6 writes exactly one row per re-home with `outcome="REHOMED"`, `action="watcher:binary_rehome"`, `pipeline="watcher"`, `stage="sync"` — replaces the current `SIBLING_ORPHANED` audit row on the else branch | Shallow |
| `AIDecision` | `src/core/confidence.py` | Data class for audit decisions | Unchanged; used to construct the re-home audit payload | Shallow |
| `new_correlation_id` | `src/core/logging_setup.py` | Generates a fresh `correlation_id` | Already called at line 347 (function top); T6 must NOT add a second call — the re-home audit row inherits the existing bind | Shallow |
| `MoveGuard.check_and_consume` | `src/vault/move_guard.py` (added by T8) | Returns True and consumes the guard entry if `path` was registered by the pipeline; returns False otherwise | T6 calls `self._move_guard.check_and_consume(final_binary)` at the top of the re-home branch to suppress pipeline-initiated moves; logs `watcher.rehome_skip` and returns if True | Shallow |
| `get_active` | `src/vault/move_guard.py` (added by T8) | Module-level accessor returning the shared `MoveGuard` instance (or None outside `kms watch`) | T6 does NOT call `get_active()` — it reads from `self._move_guard` (the instance injected into `_VaultEventHandler` by T8); `get_active()` is for pipeline-side callers only | Shallow |

---

### Feature overview

When a binary file moves to a different folder inside the vault, the watcher fires `on_moved`. The `bin:{dst}` debounce key routes the event to `_handle_binary_move(src, dst)`. The existing same-folder branch (rename in place) is already correct and stays unchanged.

The else branch — triggered when `dst.parent != src.parent` — is what T6 rewrites. After T6 it does the following in order:

**Step 1 — MoveGuard check.** Ask the injected `MoveGuard` whether this destination was registered by the pipeline. If yes, the move was pipeline-initiated (not a user drag), so log `watcher.rehome_skip` and return immediately. No re-home, no audit. This prevents the watcher from fighting the capture pipeline.

**Step 2 — Determine new location.** Call `_location_context(dst, cfg)` to get `(loc_type, loc_name)` for the binary's destination folder — e.g. `("project", "Beta")`. If `loc_type` is None (the file was moved to an unrecognized location), fall back to the orphan-only path: delete the old sibling DB row, log a warning, and write the `SIBLING_ORPHANED` audit row as before.

**Step 3 — Compute placement.** Call `resolve_placement(dst, loc_type, loc_name, cfg)` (from T2) to get `Placement(final_dir, sibling_dir, needs_move)`. `final_dir` is where the binary should ultimately live (project root for editable files like `.docx`, `attachment/` for no-edit files like `.pdf`). `sibling_dir` is where the new summary card should live. `needs_move` indicates whether the binary itself needs a second move (e.g. a `.docx` dropped in `Projects/B/attachment/` by watchdog needs to be pulled to `Projects/B/`).

**Step 4 — Look up existing summary.** Compute `old_sibling_vp` from `_vp(_sibling_for(src, cfg))`. Call `get_by_path(old_sibling_vp)` to fetch the `DocumentRow` carrying the summary text. If the row is missing (file was never captured or index is stale), log `not_in_index` and fall back to the orphan-only path — this matches today's posture and keeps the re-home graceful.

**Step 5 — Move the binary (if needed).** If `placement.needs_move` is True, call `move_attachment(dst, final_binary)` where `final_binary = placement.final_dir / dst.name`. On failure, log and bail — leave the card pointer state in the DECISION-025 broken-pointer posture.

**Step 6 — Write the new sibling card.** Compute `new_sibling_path = placement.sibling_dir / f"{final_binary.name}.md"`. If the old sibling exists on disk, call `move_note(old_sibling, new_sibling_path, actor="ai")` to relocate it, then call `write_note` to patch the `attachment_path` frontmatter field to the new binary's vault-relative path. If the old sibling does NOT exist on disk (already gone, e.g. after a multi-hop chain), rebuild the card from the `DocumentRow` using `write_note(new_sibling_path, row.summary, rebuilt_meta, actor="ai")`. In both cases the card keeps `type="attachment-summary"` (DECISION-029).

**Step 7 — Update the database.** Call `rename_doc(old_sibling_vp, new_sibling_vp)` to update the `vault_path` in the `documents` row in-place (preserves row id and FK links, per DECISION-001). If the row was already missing (step 4 fall-back was not triggered because `get_by_path` succeeded but `rename_doc` returns rowcount 0), log a warning — this is not a fatal error.

**Step 8 — Write the audit row.** Write one `audit_write` row: `outcome="REHOMED"`, `action="watcher:binary_rehome"`, `pipeline="watcher"`, `stage="sync"`, `reasoning` naming source→dest and the routing class (editable→root or no-edit→attachment), `source_ids=[new_sibling_vp]`. The `correlation_id` is already bound from the function-top bind at line 347 — no second bind.

**T11 correlation_id verify (folds in).** The `bind_contextvars(correlation_id=new_correlation_id())` call at `watcher.py:346–347` lexically encloses the entire `_handle_binary_move` function, including the rewritten else branch. The re-home `audit_write` runs in the same Timer thread, so `correlation_id` is in scope automatically. T6's only obligation is to preserve the existing bind verbatim and add no second bind. Verification is via the cross-folder move test: assert the audit row has a non-null `correlation_id` and that no "missing correlation_id" warning appears in the log.

---

### Out of scope

- **Same-folder rename branch (`watcher.py:357–419`)** — already correct. Not touched by T6.
- **`on_moved` dispatch code (`watcher.py:280–299`)** — not changed. The `bin:{dst}` debounce key and the `_should_skip` / user `on_move` callback dispatch stay byte-for-byte identical.
- **`cli/main.py::on_move` callback** — handles md/user-note DB renames. Binary moves never route through `on_move`; they go through `_handle_binary_move` via the `bin:` key. Not touched.
- **`MoveGuard` module implementation** — owned entirely by T8. T6 only consumes `check_and_consume` and holds a reference to the guard instance. Ship T6 + T8 in the same increment.
- **Move-chain convergence (settle window)** — owned by T7. T6 fires once per hop; T7 will coalesce multi-hop chains into a single re-home. T6 is built to compose with T7 without modification (re-home sources summary from DB, so a missing on-disk source sibling is not a blocker).
- **Root-`.summaries/` recognition in reconcile predicates** — owned by T10. T6 writes sibling cards at `Projects/<A>/.summaries/` for editable files; the `_is_managed_summaries_area` predicate in `vault/paths.py` does not yet recognise this location. T10 must extend it. Until T10 lands, Stage 4 orphan-sibling reconcile leaves root-level sibling cards alone (safe but means stale ones accumulate until T10).
- **Re-capture on content change** — owned by T9. T6 re-homes WITHOUT calling the LLM. If the file's content changed after the move, T9's `chg:` path handles re-capture. T6 reuses the stored summary unconditionally.
- **Misplaced→inbox sweep** — owned by T4. T6 only handles moves between known project/domain locations.
- **Phase 2 Classify wiring** — `resolve_placement` (T2) will also be consumed by Phase 2, but T6 is the second consumer; no modification to the helper is needed.

---

### Constraints

- **C-01 · Vault-only writes** — all disk mutations go through `vault/writer.py`: `move_attachment`, `move_note`, `write_note`. No raw `.write_text()` or `open(..., 'w')`. Source: `CONSTRAINTS.md` C-01; hook hard-block.
- **C-13 · Audit every AI/sync decision** — exactly one `audit_write` row per re-home with `outcome="REHOMED"`. The current orphan audit row (`outcome="SIBLING_ORPHANED"`) on the else branch is replaced. No audit row for a MoveGuard-suppressed move (suppression is the absence of an action, not a decision). Source: `CONSTRAINTS.md` C-13.
- **Result-type discipline** — every `Result`-returning call (`get_by_path`, `move_attachment`, `move_note`, `write_note`, `rename_doc`, `audit_write`) must be matched with a `case Success` / `case Failure` block. `Failure` branches log `%s`-style and either return early or continue the sequence per the broken-pointer fallback posture. Never swallow a `Failure` silently. Source: CLAUDE.md Architecture.
- **`updated_by_human` gate** — `write_note` and `move_note` set `updated_by_human` from the `actor` argument. T6 passes `actor="ai"` for all sibling writes, so a re-homed card is correctly AI-owned. The gate also means a human-locked sibling (`updated_by_human=True` on disk) will cause `move_note(actor="ai")` to return `Failure` — T6 must match that failure, log it, and bail without moving the binary (no half-move). Source: `CONSTRAINTS.md` C-02; `vault/writer.py:202–207`.
- **TD-033 · Monkeypatch the importing module** — `resolve_placement`, `_location_context`, `get_by_path`, `move_attachment`, `move_note`, `write_note`, `audit_write`, `AIDecision`, `rename_doc` are all imported at the top of `vault/watcher.py`. Tests that stub any of these must patch `vault.watcher.<name>`, not the source module (e.g. `vault.watcher.resolve_placement`, not `vault.paths.resolve_placement`). Source: CLAUDE.md TD-033.
- **TD-030 · on_moved ordering** — binary-sync dispatch (`bin:{dst}` → `_handle_binary_move`) stays BEFORE `_should_skip` and the user `on_move` callback dispatch. T6 does not touch `on_moved` at all, so this ordering is preserved. Source: CLAUDE.md TD-030.
- **T11 · correlation_id bind — no second bind** — `bind_contextvars(correlation_id=new_correlation_id())` is already called at `watcher.py:346–347`. T6 must NOT add a second call. The re-home audit row inherits the existing bind in the same Timer thread. Source: design doc T6 Implications §"T11 folds in".
- **DECISION-029 · `type=attachment-summary` on every sibling** — the rebuilt card must carry `type="attachment-summary"` whether it is placed in `attachment/.summaries/` or in the root `Projects/<A>/.summaries/`. Source: CLAUDE.md DECISION-029.
- **DECISION-025 · broken-pointer failure posture** — if `move_attachment` fails (e.g. destination collision), log the failure and leave the card pointing at the prior binary path. Do not crash, do not retry. Source: design doc T6 §Options "Option B" risk note; DECISION-025.
- **Stdlib logging, `%s`-style, vault-relative via `self._root`** — `_log` is `logging.getLogger(__name__)` at `watcher.py:46`. All new log lines use `%s` placeholders, not kwargs. Vault-relative path strings computed via the `_vp()` local helper (L352–355), NOT `to_vault_path` (which reads the CONFIG singleton). Source: CLAUDE.md "What Claude gets wrong" — `logging` is `%s`-style; vault-relative paths from `self._root`.
- **C-17 · No module-scope CONFIG import in tests** — new tests must build `VaultConfig(root=tmp_path)` directly. Source: `CONSTRAINTS.md` C-17.
- **[REQUIRES: T2]** — `resolve_placement` and `Placement` must exist in `vault/paths.py` before T6 compiles.
- **[REQUIRES: T8]** — `MoveGuard` and `get_active()` must exist in `vault/move_guard.py`; `_VaultEventHandler.__init__` must accept and store the `move_guard` parameter (wired by T8's VaultWatcher changes). Ship T6 and T8 in the same increment.
- **[REQUIRES: T3]** — T3's LOCATED `move_attachment` call site (`capture.py:658`) must be one of the MoveGuard register sites (wired by T8), so a pipeline capture-initiated move is suppressed and T6's re-home does not fight the pipeline.

---

### Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|---|---|---|
| A1 | `_handle_binary_move(src, dst)` is defined at `src/vault/watcher.py:344` with the same-folder branch spanning lines ~357–419 and the cross-folder else branch spanning lines ~420–449; the else branch is what T6 rewrites | Design doc T6 Implications §"Which terms map to what" | A grep / read shows the function at a different line range, or the else branch has already been partially rewritten by a prior commit |
| A2 | `bind_contextvars(correlation_id=new_correlation_id())` is called at `watcher.py:346–347`, lexically inside `_handle_binary_move` and before either branch runs; it is still present in the current working tree | Design doc T6 Implications §"T11 folds in"; direct code read at `watcher.py:346–347` (confirmed above) | The bind call has been moved, removed, or placed inside only one branch — if so, T6 must restore it to the function top before the branch split |
| A3 | `get_by_path(vault_path) -> Result[DocumentRow | None]` exists at `src/storage/documents.py:141–170` and is importable; it returns `Success(DocumentRow)` if found, `Success(None)` if not found, `Failure` on DB error. The `DocumentRow` dataclass has a `summary` field (`str | None`) | Design doc T6 Implications §"Sibling lookup via DB"; direct code read of `documents.py:27–43, 141–170` (confirmed above) | If `summary` is absent from `DocumentRow`, the rebuild step cannot source the card body from the DB row |
| A4 | The `documents` table has NO `attachment_path` column (schema.sql confirmed: columns are `id, vault_path, title, summary, note_type, confidence, created_at, updated_at, updated_by_human, content_hash` plus migration additions); the reverse lookup must be path-keyed, not attachment_path-keyed | Design doc T6 Implications §"Sibling lookup via DB"; confirmed by design doc's schema.sql note | If `attachment_path` IS a column in the current schema (e.g. added by a later migration), a direct attachment_path lookup would be possible but must not be used (path-keyed lookup is sufficient and already implemented) |
| A5 | `resolve_placement` and `Placement` (added by T2) are importable from `vault.paths` and have the signature `resolve_placement(file_path: Path, target_type: str, target_name: str, vault_cfg: VaultConfig) -> Placement(final_dir: Path, sibling_dir: Path, needs_move: bool)` | T2 spec §Component 2 "Interface shape"; design doc Appendix T2 settled decisions | If the function or dataclass is absent or has a different signature, T6 cannot call it; confirmed by grep in /research |
| A6 | `_location_context(dst, cfg)` is importable from `vault.paths` and returns `(None, None)` for paths outside any known project/domain/inbox location | Design doc T6 Implications §"Which terms map to what"; direct code read of `paths.py:87–121` (confirmed above) | If `_location_context` is renamed or moved, the import in `watcher.py` must be updated |
| A7 | `MoveGuard` (added by T8) is injected into `_VaultEventHandler` as `self._move_guard`; the method `check_and_consume(path: Path) -> bool` is callable from inside `_handle_binary_move` without going through `get_active()` | T8 spec Appendix §"Decisions for downstream" — "VaultWatcher creates MoveGuard, passes to _VaultEventHandler; store as self._move_guard" | If T8 stores the guard under a different attribute name or does not inject it into the handler, the check at the top of the re-home branch would be `None.check_and_consume` — an AttributeError at runtime |
| A8 | `rename as rename_doc` is already imported at the top of `watcher.py` at line 37 (`from storage.documents import delete_by_path, rename as rename_doc`) — confirmed by direct code read | Direct code read of `watcher.py:37` (confirmed above) | If the import is absent or aliased differently, T6's DB rename call must add or correct the import |
| A9 | `move_note` and `write_note` are already imported at `watcher.py:41` (`from vault.writer import move_note, write_note`); `move_attachment` is NOT currently imported at module scope in `watcher.py` | Direct code read of `watcher.py:40–41` | If `move_attachment` is already imported, no new import is needed; if `move_note`/`write_note` are absent, they must be added. Research must verify the exact import state. |
| A10 | `_sibling_for(src, cfg)` anchors the sibling path to the binary's **current** parent (`src.parent / summaries_subdir / f"{src.name}.md"`); for the re-home case, the new sibling path must be computed as `placement.sibling_dir / f"{final_binary.name}.md"`, NOT as `_sibling_for(dst, cfg)` | Design doc T6 Implications §"Sibling"; T2 spec §"Sibling naming rule" | If `_sibling_for` is changed by a prior task to anchor to a different path, the re-home sibling path computation may be correct after all — verify in /research |

---

### Component dependency order

#### 1. Add `move_attachment` import and `resolve_placement` / `_location_context` / `get_by_path` imports to `watcher.py`

**Goal.** Ensure that every new collaborator T6 needs is imported at the top of `watcher.py` — so that tests can patch `vault.watcher.<name>` per TD-033, and so the new else-branch code compiles.

**Build.**

In `src/vault/watcher.py`, extend the module-level imports:

1. Add `get_by_path` to the existing `storage.documents` import line (`watcher.py:37`). After the change it reads: `from storage.documents import delete_by_path, get_by_path, rename as rename_doc`.

2. Add `move_attachment` to the existing `vault.writer` import line (`watcher.py:41`). After the change: `from vault.writer import move_attachment, move_note, write_note`.

3. Add a new import line for `resolve_placement` and `_location_context` from `vault.paths` (these are not yet imported there). After the change: `from vault.paths import _is_in_managed_attachment, _location_context, resolve_placement`.

   Note: `Placement` is the return type of `resolve_placement`; it does not need to be imported separately unless used in a type annotation in the else branch. The planner should confirm whether a `Placement` import is needed for any `isinstance` check or type hint in the branch.

**Depends on.** T2 (`resolve_placement`, `Placement` must exist in `vault/paths.py`); T8 (`MoveGuard` must exist and be injected — but the import of `MoveGuard` is done inside T8's changes, not here; T6 accesses it as `self._move_guard`).

**Assumes.** A5 (T2's `resolve_placement` exists), A6 (`_location_context` is importable from `vault.paths`), A8 (existing `rename_doc` import confirmed), A9 (existing `move_note`/`write_note` imports confirmed; `move_attachment` is absent and must be added).

**Interface shape.** No new public interface. These are module-level name bindings that make the collaborators patchable as `vault.watcher.<name>`.

**Decisions.**
- Q: Should `Placement` be imported at module scope? Options: yes (makes type annotations possible) / no (unnecessary if the else branch only accesses `placement.final_dir` etc. by attribute, never needing the type name). Leaning no — the else branch uses attribute access, not isinstance checks or type annotations, so importing `Placement` at module scope adds no functional benefit and slightly widens the import surface.

**Done when.**
- `python -c "import vault.watcher"` succeeds with no `ImportError` after T2 and T8 are also merged.
- `grep "move_attachment" src/vault/watcher.py` shows the name in the import line, not only in `capture.py`.
- `grep "get_by_path" src/vault/watcher.py` shows the name in the import line.
- `grep "resolve_placement" src/vault/watcher.py` shows the name in the import line.

---

#### 2. Rewrite the cross-folder else branch of `_handle_binary_move`

**Goal.** Replace the current orphan-and-do-nothing else branch with a full re-home: move the binary to its type-correct final location, write the summary card at the new location from the DB-stored summary (no LLM call), update the database record, and audit — all while respecting the MoveGuard suppression check and every vault-safety constraint.

**Build.**

In `src/vault/watcher.py`, inside `_handle_binary_move`, replace the entire else block (currently lines ~420–449) with the following logic. The `_vp()` local helper defined at lines 352–355 is preserved unchanged and remains in scope.

The logic in plain English, step by step:

**Sub-step 2a — MoveGuard check.**
At the very top of the else block, check the injected move guard:
```
g = self._move_guard
if g is not None and g.check_and_consume(dst):
    _log.info("watcher.rehome_skip path=%s reason=pipeline_initiated", dst)
    return
```
If the guard fires, return immediately — no re-home, no audit row, no DB change.

**Sub-step 2b — Compute new location.**
Call `_location_context(dst, self._vault_config)` to get `(loc_type, loc_name)`. If `loc_type is None`, the file was moved to an unrecognised location. Fall back to the existing orphan path: delete the old sibling's DB row, log `watcher.binary_move_orphan_no_location`, and write `SIBLING_ORPHANED` audit row. This preserves the safety of today's behavior for moves to unknown locations.

**Sub-step 2c — Compute placement.**
Call `resolve_placement(dst, loc_type, loc_name, self._vault_config)` to get `placement`. Compute:
- `final_binary = placement.final_dir / dst.name`
- `new_sibling_path = placement.sibling_dir / f"{dst.name}.md"`
- `new_sibling_vp = _vp(new_sibling_path)`

**Sub-step 2d — Look up existing summary from DB.**
Compute `old_sibling = _sibling_for(src, self._vault_config)` and `old_sibling_vp = _vp(old_sibling)`.
Call `get_by_path(old_sibling_vp)`. On `Failure`, log warning and fall back to orphan-only path (delete DB row + `SIBLING_ORPHANED` audit). On `Success(None)` (row not found), log `watcher.binary_move_not_in_index` and fall back to orphan-only path. On `Success(value=row)`, proceed to the move steps.

**Sub-step 2e — Move binary if needed.**
If `placement.needs_move` is True and `final_binary != dst`:
- Call `move_attachment(dst, final_binary)`.
- On `Failure`: log warning (`watcher.binary_rehome_move_failed`) and return. Do not continue — the sibling would point at a path that does not exist on disk (DECISION-025 broken-pointer posture).
- On `Success`: proceed.

If `placement.needs_move` is False, `final_binary` is already in the correct location — no move needed.

**Sub-step 2f — Write the new sibling card.**

Determine whether the old sibling exists on disk:

_If the old sibling exists on disk_ (`old_sibling.exists()` is True):
1. Call `move_note(old_sibling, new_sibling_path, actor="ai")`.
   - On `Failure` (including `updated_by_human` refusal): log `watcher.binary_rehome_sibling_move_failed` and return (no DB change, no audit — half-move is unsafe).
   - On `Success`: the card is now at `new_sibling_path` with its existing content.
2. Call `read_note(new_sibling_path)` to get the current metadata.
   - On `Failure`: log warning. The sibling content is moved but the pointer is stale — continue to the DB step regardless (the broken-pointer posture accepts this).
   - On `Success(value=note)`: rewrite `note.metadata.attachment_path = _vp(final_binary)` and call `write_note(new_sibling_path, note.content, note.metadata, actor="ai")` to persist the pointer update.
     - On `Failure`: log warning and continue.

_If the old sibling does NOT exist on disk_ (`old_sibling.exists()` is False):
Rebuild the card entirely from the DB row. Construct `NoteMetadata` with: `title=row.title`, `note_type=row.note_type or "attachment-summary"`, `type="attachment-summary"`, `confidence=row.confidence`, `attachment_path=_vp(final_binary)`, `content_hash=row.content_hash`, `source_hash=row.content_hash` (best available — hash round-trip is acceptable when on-disk sibling is gone), `updated_by_human=False`, and the new location tag (editable→root means the domain/project tag must reflect `loc_type`/`loc_name`).
Call `write_note(new_sibling_path, row.summary or "", rebuilt_meta, actor="ai")`.
- On `Failure`: log warning and return.

**Sub-step 2g — Update the database.**
Call `rename_doc(old_sibling_vp, new_sibling_vp)`.
- On `Success(value=0)`: log `watcher.binary_rehome_db_row_not_found` (row was already absent — not fatal).
- On `Failure`: log warning.
- On `Success(value=N > 0)`: proceed.

**Sub-step 2h — Write audit row.**
```
audit_write(
    AIDecision(
        action="watcher:binary_rehome",
        confidence=1.0,
        reasoning=f"Re-homed {src.name} → {_vp(final_binary)} ({'editable→root' if not placement.needs_move or ... else 'no-edit→attachment'})",
        source_ids=[new_sibling_vp],
    ),
    pipeline="watcher",
    stage="sync",
    outcome="REHOMED",
)
```
On `Failure`: log `watcher.binary_rehome_audit_failed`.

The `reasoning` string should note the routing class (editable→root vs no-edit→attachment) using `dst.suffix.lower() in self._vault_config.no_edit_extensions` to derive the class — a one-line read, not a new decision.

**Depends on.** Component 1 (all required imports must exist in `watcher.py`). T2 (`resolve_placement`). T8 (`self._move_guard` injected by T8's constructor wiring, `check_and_consume` callable).

**Assumes.** A1, A2, A3, A4, A5, A6, A7, A8, A9, A10.

**Interface shape.** No new public interface. `_handle_binary_move` remains a private internal method; its signature `(self, src: Path, dst: Path) -> None` is unchanged. The method is called by `_debounce` from a `threading.Timer` thread.

**Decisions.**
- Q: Use `rename_doc(old_vp, new_vp)` to update the DB row, or use `delete_by_path(old_vp)` + `upsert(new_outcome)`? Options: `rename_doc` (preserves row id and FK links per DECISION-001; preferred) / delete + upsert (simpler but loses row id). Leaning `rename_doc` — DECISION-001 prefers id preservation; `rename_doc` is already imported in `watcher.py:37`.
- Q: For the `needs_move` check in the audit reasoning string, should the predicate be `dst.suffix.lower() in self._vault_config.no_edit_extensions` or derived from `placement.needs_move`? Options: `no_edit_extensions` membership (direct, config-sourced) / `placement.needs_move` + `final_dir` inspection (indirect). Leaning `no_edit_extensions` membership — single, readable, mirrors the T3 audit enrichment pattern.
- Q: What to do when the old sibling exists on disk AND `updated_by_human` lock causes `move_note` to fail? Options: return immediately (no binary move, no DB change — safest) / move the binary but leave the sibling orphaned. Leaning return immediately — a locked sibling means the human owns the card; pulling the binary out from under it would break the `attachment_path` pointer silently. Match the `Failure` and return.

**Done when.**
- Given `Projects/A/report.pdf` with sibling `Projects/A/attachment/.summaries/report.pdf.md`, when the user drags `report.pdf` to `Projects/B/`, then `Projects/B/attachment/report.pdf` exists on disk and `Projects/B/attachment/.summaries/report.pdf.md` appears with the same summary text. The old sibling card at `Projects/A/attachment/.summaries/report.pdf.md` is gone from disk and from the database.
- Given `Projects/A/budget.xlsx` with sibling `Projects/A/.summaries/budget.xlsx.md` (editable, after T3), when dragged to `Projects/B/`, then `Projects/B/budget.xlsx` exists (visible root) and `Projects/B/.summaries/budget.xlsx.md` carries the original summary.
- Given a file moved to an unrecognised folder (not a project/domain), the behavior is the existing orphan path — no crash, one `SIBLING_ORPHANED` audit row.
- Given a pipeline-initiated move (MoveGuard returns True), no re-home audit row is written and no sibling is moved.
- Given a binary move where the old sibling was hand-edited (`updated_by_human=True`), neither the binary nor the sibling is moved (the `move_note` failure causes an early return).
- The `correlation_id` in the `REHOMED` audit row is non-null (T11 verify — the function-top bind is preserved).
- No second `bind_contextvars(correlation_id=...)` call appears in the else branch.

---

#### 3. Add unit tests for the rewritten else branch

**Goal.** Confirm that the re-home logic is correct for the main cases (no-edit → attachment, editable → root, MoveGuard suppression, missing DB row, `updated_by_human` lock), that T11's `correlation_id` is present in every audit row, and that the TD-033 patching convention is enforced.

**Build.**

Add a new test class `TestHandleBinaryMoveRehome` in `tests/test_vault/test_watcher.py` (or a new `tests/test_vault/test_watcher_rehome.py` if the existing file would become unwieldy). All tests:
- Construct `VaultConfig(root=tmp_path)` directly — no module-scope `from core.config import CONFIG` (C-17).
- Construct `_VaultEventHandler` with an explicit `vault_config=VaultConfig(root=tmp_path)` and mock callbacks.
- Patch `vault.watcher.resolve_placement`, `vault.watcher.get_by_path`, `vault.watcher.move_attachment`, `vault.watcher.move_note`, `vault.watcher.write_note`, `vault.watcher.rename_doc`, `vault.watcher.audit_write` — always the `vault.watcher.*` name, never the source module (TD-033).
- Inject a mock `MoveGuard` via `handler._move_guard = mock_guard`.

Tests to write:

1. **`test_rehome_no_edit_pdf_cross_folder`** — mock `resolve_placement` returning `Placement(final_dir=attachment_dir, sibling_dir=attachment_summaries_dir, needs_move=True)`, mock `get_by_path` returning a `DocumentRow` with a summary. Assert `move_attachment` called with `(dst, attachment_dir / dst.name)`, `move_note` called with the old and new sibling paths, `rename_doc` called with `(old_sibling_vp, new_sibling_vp)`, `audit_write` called with `outcome="REHOMED"`.

2. **`test_rehome_editable_docx_cross_folder`** — mock `resolve_placement` returning `Placement(final_dir=project_root, sibling_dir=root_summaries_dir, needs_move=False)`. Assert `move_attachment` is NOT called, `move_note` called to the root-`.summaries/` path, `rename_doc` called, `audit_write` called with `outcome="REHOMED"`.

3. **`test_rehome_moveguard_suppression`** — configure mock `_move_guard.check_and_consume` to return True. Assert `move_attachment` is NOT called, `move_note` is NOT called, `audit_write` is NOT called, log contains `watcher.rehome_skip`.

4. **`test_rehome_fallback_when_no_db_row`** — mock `get_by_path` returning `Success(None)`. Assert `move_attachment` NOT called, `move_note` NOT called, `rename_doc` NOT called; the existing orphan path fires (assert `audit_write` called with `outcome="SIBLING_ORPHANED"` or equivalent orphan outcome).

5. **`test_rehome_fallback_when_unknown_location`** — mock `_location_context` returning `(None, None)`. Assert `resolve_placement` NOT called, orphan path fires.

6. **`test_rehome_updated_by_human_lock_aborts`** — mock `move_note` returning `Failure(error="human_locked", recoverable=False)`. Assert `move_attachment` NOT called (or if binary was already moved before sibling step, accept that constraint — check design sub-step 2e vs 2f ordering: binary move happens in 2e before sibling move in 2f; if 2f fails, the binary is already at its new location but the sibling is left orphaned — the test should assert that the audit row reflects a partial failure, or that the spec orders sibling move BEFORE binary move to allow a clean abort; confirm with Decisions in Component 2). Leaning: check the `updated_by_human` lock BEFORE moving the binary — read the old sibling, inspect `updated_by_human`, and if True, return before `move_attachment`. This avoids a half-move. The spec should note this ordering check.

7. **`test_rehome_fallback_sibling_rebuild_from_db_when_on_disk_absent`** — configure `old_sibling.exists()` to return False (monkeypatch `Path.exists` for the old sibling path); mock `get_by_path` returning a `DocumentRow`. Assert `write_note` called (rebuild path), NOT `move_note`. Assert `rename_doc` called with correct vp values.

8. **`test_rehome_audit_row_has_correlation_id`** — capture the `audit_write` call's `AIDecision` argument. Assert `audit_write` was called exactly once and the `correlation_id` contextvar is bound in that thread (use `structlog.contextvars.get_contextvars()` in the mock to read the contextvar and assert `correlation_id` is present and non-empty). This is the T11 verify.

9. **`test_rehome_domain_symmetry`** — repeat the no-edit and editable cases with `target_type="domain"` (mock `_location_context` returning `("domain", "Finance")`). Assert final paths use `vault_cfg.domain_path / "Finance" / ...`.

10. **`test_rehome_moveguard_none_does_not_raise`** — set `handler._move_guard = None`. Assert the code does not raise `AttributeError` (guard is optional; `None` means "not under kms watch — no suppression needed").

**Depends on.** Component 2 (the rewritten else branch must exist for the tests to exercise it). Component 1 (imports must resolve).

**Assumes.** A1–A10.

**Done when.**
- All ten tests pass under `uv run pytest tests/test_vault/test_watcher_rehome.py -m "not smoke"` (or equivalent path).
- No patch target in the new tests uses any source module name (e.g. `vault.paths.resolve_placement`, `vault.writer.move_note`, `storage.documents.get_by_path`) — all use `vault.watcher.*`.
- No `from core.config import CONFIG` appears at module scope in the new test file.
- `uv run pytest tests/ -m "not smoke"` still passes with no regressions (count ≥ 798, current baseline per STATE.md).

---

#### 4. Add integration smoke test for T11 correlation_id verify

**Goal.** Confirm that the `correlation_id` inherited by the re-home audit row is a valid, non-empty string — providing the T11 "verify-only" guarantee that the existing `bind_contextvars` at the function top covers the rewritten else branch.

**Build.**

In `tests/test_vault/test_watcher_rehome.py` (or the existing watcher test file), add one integration-style test:

**`test_t11_correlation_id_present_in_rehome_audit`** — run `_handle_binary_move(src, dst)` with a mocked else-branch setup (different-folder move, valid DB row). Capture the `AIDecision` object passed to the mocked `audit_write`. Assert that `structlog.contextvars.get_contextvars()` sampled from inside the `audit_write` mock contains a `correlation_id` key with a non-empty string value. Also assert the audit row's `action` is `"watcher:binary_rehome"` and `outcome` is `"REHOMED"`.

This test is deliberately narrow: it does not test the full watcher event loop, only that the re-home code path, when invoked from a Timer thread, inherits the bind established at line 347. No real filesystem, no real DB.

**Depends on.** Component 2 (the rewritten branch must exist), Component 3 (the test infrastructure patterns are established).

**Assumes.** A2 (the `bind_contextvars` call at line 347 is preserved verbatim).

**Done when.**
- The test passes under `uv run pytest tests/test_vault/ -m "not smoke"`.
- The test captures a `correlation_id` value from the contextvar at `audit_write` call time — not from a field on the `AIDecision` object (which would only prove the field was set, not that the contextvar was bound at that point in the thread).

---

### Handoff notes

- **Contract with T2 spec honored:** T6 calls `resolve_placement(dst, loc_type, loc_name, cfg)` — using `dst` (the watchdog event destination), not the pipeline `src` — because the re-home must place the binary at its new location, not re-derive from its old location. The T2 spec's "Contract with T6 (watcher re-home)" note confirms this is the expected call pattern. T6 imports `resolve_placement` at `watcher.py` module scope and tests patch `vault.watcher.resolve_placement` (TD-033).

- **Contract with T8 spec honored:** T6 accesses the guard as `self._move_guard` (injected by T8's `VaultWatcher.__init__` changes). T6 does NOT call `get_active()` — that is for pipeline-side callers. T6 checks `self._move_guard is not None` before calling `check_and_consume` to handle the case where the handler is used outside `kms watch` (e.g. in tests that do not wire a guard). The T8 spec note "T6 is the sole consumer of `check_and_consume`" is honored — no other caller exists in T6.

- **Contract with T3 spec:** T3's LOCATED `move_attachment` call site (`capture.py:658`) will be wrapped by T8's guard registration. T6 depends on T3 having landed before T8's register calls are added at that site. If T3 is not yet merged, T6 can still be built and tested (the MoveGuard will simply never suppress anything until T3+T8 land), but the end-to-end "pipeline move is not re-homed" scenario will only be verifiable once all three are merged.

- **Ordering note for Component 2, sub-step 2e vs 2f:** The design doc (Option B, step 4) says "move binary, then write card". However, if the old sibling is `updated_by_human`, an early-abort before the binary move is cleaner. The spec recommends reading `updated_by_human` from the old sibling (either from the DB row's `updated_by_human` field, or from the `read_note` result if checking the on-disk sibling) BEFORE calling `move_attachment`. If `updated_by_human` is True, return immediately — no binary move, no DB change, no audit. This is stricter than the design doc but safer: it avoids a binary that has moved but whose card is still at the old path. The planner should add this pre-check as the first read step before sub-step 2e.

- **`[REQUIRES: T10]` root-`.summaries/` recognition:** When T6 writes a sibling at `Projects/<A>/.summaries/<name>.md` (editable file re-homed to root), that path is NOT yet recognized as a managed summaries area by `_is_managed_summaries_area` in `vault/paths.py`. Until T10 extends that predicate, reconcile Stage 4 (`reconcile_orphan_siblings`) will not scope-protect those root-level sibling cards. T6 writes them there correctly; T10 teaches reconcile to find and protect them.

- **T7 composition:** T6 sources the summary from the DB row (not the on-disk source sibling). This is the load-bearing design choice that makes T7's move-chain coalescing safe: even if intermediate siblings at `Projects/B/` were never written (because T7 cancels the B-hop), the final re-home at `Projects/C/` can still find the summary via `get_by_path(_vp(_sibling_for(origin_src, cfg)))` — as long as the original `src` (the chain origin, preserved by T7's pending-move registry) is passed. T7's spec must ensure the chain-origin src is threaded through to T6's re-home call.

- **Open uncertainty — NFC normalisation in `file_path.parent != final_dir` comparison inside `resolve_placement`:** The T2 spec (Handoff notes) flags a potential NFC/NFD mismatch on macOS between watchdog event paths and `vault_cfg.projects_path`. If `dst` from the watchdog event is NFD and `placement.final_dir` is NFC, `needs_move` could be spuriously True. The planner should verify — or add NFC normalisation to the `resolve_placement` inputs — during the research phase. T6 passes `dst` as-is to `resolve_placement`; if normalisation is needed, it should happen inside `resolve_placement` (T2's responsibility) or be applied to `dst` before the call (T6's responsibility). Confirm in /research.

- **Suggested research for /research:**
  1. Read `src/vault/watcher.py:344–449` verbatim to confirm the else branch line range and that the same-folder branch is structurally distinct. Verify A1.
  2. Read `src/vault/watcher.py:346–347` verbatim to confirm the `bind_contextvars` call is present at the function top, not inside a branch. Verify A2.
  3. Grep `src/vault/watcher.py` for `move_attachment` — confirm it is NOT currently imported at module scope (so Component 1 must add it). Verify A9.
  4. Confirm that `resolve_placement` and `_location_context` are NOT already imported in `watcher.py` (would affect Component 1's import additions). Verify A5, A6.
  5. Read `src/storage/documents.py:27–43` to confirm `DocumentRow` has a `summary` field and an `updated_by_human` field — needed for the pre-check in sub-step 2e ordering decision. Verify A3.
  6. Check whether `NoteMetadata` (in `core/note.py` or similar) has an `attachment_path` field that `write_note` accepts — needed to confirm the rebuilt-sibling path in sub-step 2f.
  7. Confirm current test baseline: `uv run pytest tests/ -m "not smoke" --co -q | tail -1` (must be ≥ 798).

---

## T7 — Move-chain convergence (settle window)

### Purpose

When the executive drags a file to the wrong project and then quickly drags it again to the right one (`A → B → C`), the watcher today would — after T6 is in place — fire a full re-home on every hop: write a summary card at B, then orphan it and write another at C. This task adds a short "settle window" so the watcher waits a few seconds after each move before acting; if a second move arrives for the same file, the pending re-home is cancelled and the clock restarts. Only when the file stops moving does the system re-home once, to the final location.

After this task, a two-hop or three-hop move chain produces exactly one re-home, exactly one audit row, and no stale summary cards at intermediate locations. A single-hop move still re-homes correctly — the settle window adds latency only, not a behavior change.

[REQUIRES: T6 + T8] — ship T7 in the same increment as T6 and T8.

---

### Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `_VaultEventHandler` | `src/vault/watcher.py:89–122` | The watchdog event handler; holds all registries (`_timers`, `_pending_folders`, `_folder_tokens`, `_folder_lock`) and the `_move_guard` injected by T8 | T7 adds three new private helpers and two new dicts + a lock to this class, symmetric with the existing folder-cooldown members | Deep |
| `_register_pending_folder` / `_reset_folder_timer` / `_fire_folder_stable` | `src/vault/watcher.py:182–249` | The folder-cooldown helpers that debounce folder-drop events: register a pending folder, reset its timer on new events, and fire capture when stable; the token guard (`_folder_tokens`) makes stale fires no-ops | T7 copies this exact three-helper + token-guard pattern for binary moves; the settle-window helpers are structurally identical, operating on a different registry (`_pending_binary_moves`) and keyed on filename rather than folder path | Deep |
| `_folder_tokens` / `_pending_folders` / `_folder_lock` | `src/vault/watcher.py:113–122` | Two dicts and a `threading.Lock` that back the folder-cooldown registry | T7 adds parallel `_binary_move_tokens: dict[str, int]`, `_pending_binary_moves: dict[str, tuple[Path, Path, int]]`, and `_binary_move_lock: threading.Lock` with the same locking discipline | Shallow |
| `_debounce` | `src/vault/watcher.py:151–159` | Cancels-and-restarts a `threading.Timer` per key; used for all watcher debouncing | T7 does NOT use `_debounce` for the settle window — the settle helpers mirror `_register_pending_folder` / `_reset_folder_timer` directly (they manage the token guard that `_debounce` lacks). `_debounce` remains unchanged. | Shallow |
| `on_moved` / `_VaultEventHandler.on_moved` | `src/vault/watcher.py:280–299` | Dispatches watchdog `on_moved` events: fires the `bin:{dst}` sibling-sync via `_debounce` (TD-030 first), then the user `on_move` callback | T7 intercepts the cross-folder binary case AFTER the existing `bin:{dst}` sibling-sync dispatch (TD-030 ordering preserved) and routes it into the settle window instead of directly into T6's re-home | Deep |
| `_handle_binary_move` | `src/vault/watcher.py:344–449` | Handles binary move events: same-folder rename branch (unchanged) and cross-folder else branch (T6's re-home, now called by the settle timer) | T7 does NOT change `_handle_binary_move`'s logic. The settle timer fires T6's re-home by calling the else-branch code path. T7 only changes *when* and *with what arguments* that path is invoked. | Deep |
| `bind_contextvars(correlation_id=...)` | `src/vault/watcher.py:346–347` | Binds a fresh `correlation_id` at the top of `_handle_binary_move`, before either branch | Preserved verbatim. The settle-timer callback invokes `_handle_binary_move` with the chain-origin src and final dst; `bind_contextvars` is called at the function top as before — T7 must not add a second bind. | Shallow |
| `MoveGuard.check_and_consume` | `src/vault/move_guard.py` (added by T8) | Returns True and consumes the registration if `path` was registered by the pipeline | T7's settle-timer fires T6's re-home branch, which already calls `self._move_guard.check_and_consume(final_binary)` at its entry. T7 must NOT consume a guard token on intermediate hops — the guard check lives INSIDE T6's re-home, which T7 only calls once at the end. | Shallow |
| `CaptureConfig` | `src/core/config.py:231–238` | Pydantic model for capture tuning: cooldowns, workers, thresholds | T7 adds `binary_settle_seconds: float = Field(5.0, ge=0.0)` here — parallel to `folder_cooldown_seconds` which governs folder-drop debounce | Shallow |
| `folder_cooldown_seconds` | `src/core/config.py` (field on `CaptureConfig`) and `src/config/config.yaml` | Existing float Field controlling the folder-drop cooldown duration | T7 adds `binary_settle_seconds` alongside this field in both config.py and config.yaml — making the two timers independently tunable. Does NOT reuse `folder_cooldown_seconds` (design doc explicitly recommends a dedicated key). | Shallow |
| `VaultWatcher.__init__` | `src/vault/watcher.py:465–503` | Constructs the watcher: creates `_VaultEventHandler`, passes root/vault_cfg/callbacks, threads cooldown value from `CaptureConfig` | T7 threads `binary_settle_seconds` from the watcher constructor into `_VaultEventHandler` (same pattern as `folder_cooldown_seconds` → `self._folder_cooldown`) | Deep |
| `watch()` | `src/cli/main.py:253–260` | Wires the live watcher for `kms watch`: builds `VaultWatcher` — currently does NOT pass `folder_cooldown_seconds` (uses `VaultWatcher` default silently) | T7 must add **two** new kwargs: `folder_cooldown_seconds=CONFIG.main.capture.folder_cooldown_seconds` AND `binary_settle_seconds=CONFIG.main.capture.binary_settle_seconds` — both timers must be wired or both silently ignore YAML overrides | Shallow |
| `unicodedata.normalize` | `src/vault/watcher.py` (already imported) | NFC-normalises a string for safe Unicode path comparison | T7's identity key is `unicodedata.normalize("NFC", dst.name)` — ensuring the filename key matches watchdog event paths consistently across the move chain, same as the rest of the watcher | Shallow |
| `threading.Timer` / `threading.Lock` | stdlib, already used in `watcher.py` | Timer with callback; Lock for cross-thread state | The settle registry uses the same `threading.Timer` + `threading.Lock` discipline as the folder-cooldown registry. No new imports needed. | Shallow |

---

### Feature overview

The core problem is that the `bin:{dst}` debounce key the watcher already uses is keyed on the **destination path**, not on the **file identity**. So `A → B` debounces under `bin:B` and `B → C` debounces under `bin:C` — two separate keys that do not coalesce. T7 introduces a second layer of coalescing: a filename-keyed "settle window" that sits on top of the per-hop debounce.

When a cross-folder binary move fires in `on_moved`, the settle window works like this:

1. The existing `bin:{dst}` sibling-sync dispatch fires first (TD-030 ordering — this must not change).
2. For the re-home action (the new T6 logic), instead of firing immediately, the event is registered in a pending-binary-moves registry keyed on `normalize("NFC", dst.name)` — the stable file identity across the chain.
3. Each registration stores: the **chain-origin `src`** (the first hop's source path, preserved across all subsequent updates to the registry entry), the **latest `dst`** (updated on each new hop), and a **monotonically-increasing token** (the C2 stale-fire guard — copied exactly from `_fire_folder_stable`).
4. A `threading.Timer` is armed for `binary_settle_seconds`. If another move event arrives for the same filename before the timer fires, the timer is cancelled, the token is incremented, and the registry entry is updated with the new `dst` (while keeping the original chain-origin `src`). The old timer fires but is a no-op because its token no longer matches the stored token.
5. When the timer finally fires without being superseded, it calls T6's re-home with `(origin_src, final_dst)` — where `origin_src` is the chain origin preserved from step 3. This means T6 can look up the original sibling via `get_by_path(_vp(_sibling_for(origin_src, cfg)))` even if the intermediate sibling at B was never created.
6. Inside the settle-timer callback, T8's MoveGuard check runs as the first thing in T6's re-home branch (exactly as T6 specifies) — T7 does not move that check earlier. This ensures a pipeline-initiated final move still triggers the guard, and superseded intermediate hops never consume a guard token.

**What changes in `on_moved`:** The existing dispatch block for cross-folder binary moves — currently routing directly into T6's re-home branch via `_handle_binary_move` — is intercepted and routed through `_register_pending_binary_move` instead. The same-folder rename dispatch is NOT changed. The `bin:{dst}` sibling-sync dispatch is NOT changed. Only the cross-folder re-home firing path is intercepted.

**Edge cases:**
- Single-hop move (`A → B`, no follow-up): the settle timer fires once after `binary_settle_seconds`, re-home runs exactly as T6 specifies. The settle window adds latency only.
- Two files with the same name moving concurrently: they share a filename key and one re-home could be attributed to the wrong file. For a single sequential human user this is an accepted limitation (documented in design doc, OQ resolved).
- Pipeline-initiated move: T8's `check_and_consume` runs inside T6's re-home when the settle timer fires — the guard token is consumed once at the end, not N times per hop.

---

### Out of scope

- **T6 re-home mechanics** — the settle window only controls when T6's re-home fires and with what `(origin_src, final_dst)` arguments. What T6 does inside the re-home branch is unchanged. T7 does not modify `_handle_binary_move`.
- **Same-folder rename branch** (`watcher.py:357–419`) — not touched. The settle window applies only to the cross-folder re-home path.
- **Binary-delete path** (`on_deleted`) — not touched. The settle window does not affect delete events.
- **T8 MoveGuard internals** — T7 relies on T8's guard being in place but adds no logic to `move_guard.py`.
- **Content-hash identity key** — Option C from the design doc. The filename key is chosen (single-user requirement, no read-per-event cost). Content-hash is explicitly deferred.
- **Multi-user concurrency** — the known same-name collision edge case is accepted for the single sequential human executive target user.
- **Root-`.summaries/` recognition in reconcile** — T10 owns that. T7 writes nothing to the vault; the eventual re-home is entirely T6's responsibility.
- **`kms reconcile` or any pipeline outside `kms watch`** — the settle window is a watcher-internal mechanism. It does not apply to `scan_capture`, `kms capture`, or reconcile.

---

### Constraints

- **C-01 · Vault-only writes via writer** — T7 itself performs no vault writes. All disk effects of the eventual re-home go through T6's `move_attachment` / `move_note` / `write_note`. Satisfied trivially. Source: `CONSTRAINTS.md` C-01.
- **C-13 · Audit log** — The coalesced re-home emits T6's single `REHOMED` audit row. T7 must NOT emit an audit row for superseded (cancelled) hops — a cancelled pending re-home represents no AI decision. Net result: one audit row per settled chain. Source: `CONSTRAINTS.md` C-13; design doc T7 §Guardrail Checklist.
- **C-06 · No hardcoded thresholds in `pipelines/`** — C-06 is scoped to confidence floats in `if/elif` inside `pipelines/`. The settle duration lives in `vault/watcher.py` (not `pipelines/`) and is a duration, not a confidence threshold. Nonetheless, the duration must be config-sourced (`binary_settle_seconds`), never a bare literal. Source: design doc T7 §Guardrail Checklist; CLAUDE.md "New threshold or rule → edit a config file."
- **C-17 · No module-scope CONFIG import in tests** — new tests construct `_VaultEventHandler` or `VaultWatcher` with explicit `vault_config=VaultConfig(root=tmp_path)` and explicit settle-seconds. Never import `CONFIG` at module scope. Source: `CONSTRAINTS.md` C-17.
- **TD-033 · Monkeypatch the importing module** — tests that patch `_handle_binary_move` or any collaborator must patch `vault.watcher.<name>`, never the source module. Source: CLAUDE.md TD-033.
- **TD-030 · Binary-sync dispatch before re-home logic** — the `bin:{dst}` sibling-sync dispatch in `on_moved` (`watcher.py:288–291`) must remain before any settle-window registration. T7 intercepts the re-home path AFTER the sync dispatch, not before. Source: CLAUDE.md TD-030; design doc T7 §Guardrail Checklist.
- **C2 token guard (stale-fire prevention)** — T7 must copy the exact token pattern from `_fire_folder_stable` (`watcher.py:229–249`): each timer carries a token; a fired timer is a no-op unless its token still matches the stored token. This is the correctness guarantee for "single re-home at final location." Source: design doc T7 §Guardrail Checklist; CLAUDE.md "stale fire" rule.
- **`%s`-style stdlib logging** — `_log` is `logging.getLogger(__name__)` in watcher.py. All new log lines use `%s` placeholders, not kwargs. Source: CLAUDE.md "What Claude gets wrong — logging."
- **NFC path normalisation for identity key** — the filename-based identity key must be `unicodedata.normalize("NFC", dst.name)` to match watchdog event paths consistently on macOS. Source: CLAUDE.md "vault-relative paths from `self._root`"; design doc T7 §Guardrail Checklist.
- **No second `bind_contextvars` call** — T6's re-home fires from inside `_handle_binary_move`, which already has `bind_contextvars(correlation_id=new_correlation_id())` at the function top (`watcher.py:346–347`). T7 must NOT add a second bind in the settle-timer callback. The existing bind covers the re-home's Timer thread. Source: T6 spec §Constraints "T11 · correlation_id bind".
- **[REQUIRES: T6]** — T7's settle timer calls T6's re-home path. T6 must exist and be tested before T7 can be built. Ship all three (T8 + T6 + T7) in the same increment. Source: design doc build order L420.
- **[REQUIRES: T8]** — T8's `MoveGuard.check_and_consume` is called inside T6's re-home branch. T7's settle window must NOT move that check earlier (doing so would consume the guard token on a superseded intermediate hop). T8 must exist before T7. Source: design doc T7 §Implications "Critical composition with T8."
- **[REQUIRES: T2]** — transitively via T6: the settle-timer's final re-home call passes `(origin_src, final_dst)` to T6, which calls `resolve_placement(final_dst, ...)`. T2 must exist. Source: design doc T7 §"[REQUIRES:]" labels.

---

### Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|---|---|---|
| A1 | `_register_pending_folder`, `_reset_folder_timer`, and `_fire_folder_stable` are defined at `src/vault/watcher.py:182–249` and follow the pattern: register stores `(timer, token)` keyed by a string key in `_pending_folders` + `_folder_tokens`; reset cancels the existing timer, increments the token, and starts a new one; fire checks the token before acting | Design doc T7 §Implications "Settle window = a debounce timer … modelled on the existing folder cooldown" | A code read shows the helpers use a different registry shape (e.g. storing only a timer, no token), or the token increment/match logic differs — the T7 helpers cannot be a direct structural copy in that case |
| A2 | `_pending_folders` and `_folder_tokens` are `dict` attributes on `_VaultEventHandler`, initialised in `__init__` alongside a `threading.Lock` called `_folder_lock`; their types are `dict[str, threading.Timer]` and `dict[str, int]` respectively | Design doc T7 §Implications "The registry is in-process state on `_VaultEventHandler`, same as `_pending_folders`/`_folder_tokens`"; direct code read of `watcher.py:113–122` (confirmed above) | If the dicts have different types or the lock is shared with `_timers` (not a separate `_folder_lock`), the parallel registry pattern for T7 needs different initialisation |
| A3 | `on_moved` at `src/vault/watcher.py:280–299` dispatches cross-folder binary moves via `_debounce("bin:{dst}", ...)` which eventually calls `_handle_binary_move(src, dst)` — and this dispatch (the `bin:{dst}` key) fires BEFORE the user `on_move` callback dispatch (the `str(dst)` key); both are present in `on_moved` and must remain in this order | Design doc T7 §Implications "TD-030 ordering"; CLAUDE.md TD-030 | A code read shows `on_moved` has been refactored so the `bin:{dst}` dispatch no longer fires directly into `_handle_binary_move`, or the order has been swapped — T7's interception point would need to change |
| A4 | T6's re-home logic (the rewritten cross-folder else branch in `_handle_binary_move`) is callable with `(origin_src, final_dst)` where `origin_src` is the chain-origin path (not necessarily the immediate predecessor). Specifically, `_sibling_for(origin_src, cfg)` produces the DB lookup key for the original summary, and `_location_context(final_dst, cfg)` provides the destination project/domain context | T6 spec §Feature overview "Step 4 — Look up existing summary" — "compute `old_sibling_vp` from `_vp(_sibling_for(src, cfg))`"; design doc T7 §Implications "Critical composition with T6 (the consumer)" | If T6's re-home is hard-coded to assume `src` is the immediate move event source (e.g. it also calls `_location_context(src, cfg)` to decide where the file came FROM), passing an earlier origin src would produce incorrect location tags — the spec would need to thread a separate "origin" parameter |
| A5 | `VaultWatcher.__init__` currently accepts `folder_cooldown_seconds` (or equivalent) as a parameter and passes it to `_VaultEventHandler`; the existing pattern for threading a scalar config value from the watcher constructor to the handler is established in the working tree | Design doc T7 §Implications §"Files touched — Directly (wiring): `VaultWatcher.__init__`"; direct code read of `watcher.py:465–503` (confirmed above) | If `folder_cooldown_seconds` is read from a CONFIG singleton inside `_VaultEventHandler` rather than injected via the constructor, T7 must use the same singleton approach for `binary_settle_seconds` — the spec's constructor-injection approach would not match the pattern |
| A6 | `CaptureConfig` at `src/core/config.py:231–238` has a `folder_cooldown_seconds` Field (or equivalent float Field) that serves as the precedent for `binary_settle_seconds`; the `CaptureConfig` model is used to hold capture-pipeline timing parameters | Design doc T7 §Implications "Directly (config, if dedicated key chosen): `src/core/config.py` `CaptureConfig`"; direct code read of `config.py:231–238` (confirmed above) | If `folder_cooldown_seconds` lives on a different config model (e.g. `VaultConfig` or a new `WatcherConfig`), `binary_settle_seconds` must be added to the same model to stay parallel |
| A7 | ~~`watch()` in `src/cli/main.py:253–260` constructs `VaultWatcher` and passes `CONFIG.main.capture.folder_cooldown_seconds` to the constructor; adding `binary_settle_seconds` is a one-argument addition to the same call~~ **CORRECTED (research):** `watch()` does NOT currently pass `folder_cooldown_seconds` at all — `VaultWatcher` default is used silently. T7 must add **two** new keyword arguments: `folder_cooldown_seconds=CONFIG.main.capture.folder_cooldown_seconds` AND `binary_settle_seconds=CONFIG.main.capture.binary_settle_seconds`. | Design doc T7 §Implications; research verification of `cli/main.py:253–260` | N/A — corrected against actual code |
| A8 | `unicodedata` is already imported in `src/vault/watcher.py` (it is used for the NFC normalisation of vault-relative paths in the existing watcher code) | CLAUDE.md "vault-relative paths from `self._root`" note; existing code read of watcher.py | If `unicodedata` is not currently imported in `watcher.py`, T7 must add the import |
| A9 | The pending-binary-moves registry entry needs to store three values per key: the chain-origin `src` path (first hop source, never updated after initial registration), the latest `dst` path (updated on each hop), and the current token integer. A `tuple[Path, Path, int]` shape matches the way `_pending_folders` stores `(timer, ...)` alongside `_folder_tokens` storing the token separately — T7 may store these in one dict as `dict[str, tuple[Path, Path, int]]` or in two parallel dicts following the folder pattern | Design doc T7 §Options "Option B — Approach: The registry stores, per key, the timer + token + the original `src` of the first hop" | If the folder-cooldown pattern stores the timer in a `threading.Timer` wrapper that cannot be trivially copied, T7's registry may need a slightly different shape; research should confirm the exact structure before coding |

---

### Component dependency order

#### 1. Add `binary_settle_seconds` Field to `CaptureConfig` and `config.yaml`

**Goal.** Give the settle window a dedicated, independently tunable duration so that a user or developer can control how long the watcher waits after a binary file stops moving before running the re-home — without affecting folder-capture cooldown timing.

**Build.**

In `src/core/config.py`, inside `CaptureConfig` (after the existing `folder_cooldown_seconds` Field), add:

```
binary_settle_seconds: float = Field(5.0, ge=0.0)
```

The `ge=0.0` validator ensures a non-negative duration at startup (a zero value means fire immediately — effectively disabling coalescing, which is a valid test config). The default of 5.0 seconds is recommended in the design doc: long enough to outlive watchdog's event-delivery + debounce delay (default debounce is 3.0 s), but short enough to not feel sluggish for normal single-hop moves.

In `src/config/config.yaml`, inside the `capture:` block (near the existing `folder_cooldown_seconds` key), add:

```yaml
binary_settle_seconds: 5.0
```

The YAML default must match the Python `Field` default so that removing the YAML key does not change runtime behavior.

**Depends on.** None — this component is pure config schema with no code dependencies.

**Assumes.** A6 (`CaptureConfig` is the correct model; `folder_cooldown_seconds` is the precedent Field next to which this new Field is added).

**Interface shape.**
- Field name: `binary_settle_seconds`
- Type: `float`
- Default: `5.0`
- Consumers call: `CONFIG.main.capture.binary_settle_seconds`
- In-process dependency; no adapter needed.

**Done when.**
- `kms --help` (or any CLI command) starts without a `ValidationError` after this YAML edit.
- `CaptureConfig(binary_settle_seconds=0.5).binary_settle_seconds` returns `0.5` in a unit test that never imports `CONFIG` (C-17).
- `CaptureConfig(binary_settle_seconds=-1.0)` raises a `ValidationError` at construction time (the `ge=0.0` guard fires).
- `CaptureConfig().binary_settle_seconds` returns `5.0` when no value is provided (default is stable).
- Changing the YAML key to `binary_settle_seconds: 2.0` and restarting produces `CONFIG.main.capture.binary_settle_seconds == 2.0` (YAML overrides the Python default).

---

#### 2. Thread `binary_settle_seconds` into `_VaultEventHandler` via `VaultWatcher`

**Goal.** Make the settle duration available to the event handler that will arm the settle timers, following the same constructor-injection pattern the folder cooldown already uses — so tests can supply an explicit duration without loading real config.

**Build.**

In `src/vault/watcher.py`:

1. In `VaultWatcher.__init__` (lines ~465–503), add a `binary_settle_seconds: float` parameter (with default equal to `CaptureConfig`'s default, i.e. `5.0`). Store it and pass it to the `_VaultEventHandler` constructor.

2. In `_VaultEventHandler.__init__` (lines ~89–122), add a `binary_settle_seconds: float` parameter. Store it as `self._binary_settle_seconds: float = binary_settle_seconds`.

3. In `src/cli/main.py`, in the `watch()` function (lines ~253–260), add **two** keyword arguments to the `VaultWatcher(...)` call: `folder_cooldown_seconds=CONFIG.main.capture.folder_cooldown_seconds` AND `binary_settle_seconds=CONFIG.main.capture.binary_settle_seconds`. Neither is currently passed — both timers use the `VaultWatcher` default silently. Wire both so YAML overrides take effect. Do not read from CONFIG inside the handler.

**Depends on.** Component 1 (`binary_settle_seconds` must exist on `CaptureConfig` before `CONFIG.main.capture.binary_settle_seconds` compiles).

**Assumes.** A5 (`VaultWatcher.__init__` accepts `folder_cooldown_seconds` via constructor injection and passes it to the handler — T7 mirrors that pattern exactly), A7 (`watch()` passes individual named arguments to `VaultWatcher`).

**Interface shape.**
- `VaultWatcher.__init__(..., binary_settle_seconds: float = 5.0)`
- `_VaultEventHandler.__init__(..., binary_settle_seconds: float = 5.0)`
- In-process; tests pass an explicit duration directly.

**Done when.**
- Constructing `VaultWatcher(root=tmp_path, vault_config=VaultConfig(root=tmp_path), binary_settle_seconds=0.1)` succeeds in a unit test with `VaultConfig(root=tmp_path)` (C-17 — no real vault, no CONFIG import at module scope).
- `self._binary_settle_seconds` equals `0.1` on the resulting `_VaultEventHandler` instance.
- The existing full test suite (`uv run pytest tests/ -m "not smoke"`) still passes with no regressions — wiring the new parameter does not break any existing watcher construction.

---

#### 3. Add the pending-binary-move registry to `_VaultEventHandler`

**Goal.** Give the event handler three new private attributes that together form the settle-window registry: a dict storing pending move entries (keyed by NFC-normalised filename), a dict storing the current token per key (for the stale-fire guard), and a lock protecting both from concurrent read/write across the observer thread and the Timer thread.

**Build.**

In `src/vault/watcher.py`, inside `_VaultEventHandler.__init__` (lines ~89–122), after the existing `_pending_folders` / `_folder_tokens` / `_folder_lock` declarations, add:

```
self._pending_binary_moves: dict[str, tuple[Path, Path]] = {}
# Maps NFC-normalised filename → (origin_src, latest_dst)

self._binary_move_tokens: dict[str, int] = {}
# Maps NFC-normalised filename → current token (incremented on each new hop)

self._binary_move_lock: threading.Lock = threading.Lock()
```

The registry stores `(origin_src, latest_dst)` as the value — the timer object itself is not stored in the dict (it is self-cancelling via the token; letting it go out of scope is fine, matching the folder-cooldown pattern where `_pending_folders` stores the timer). If the folder-cooldown pattern stores the timer in the dict, T7 must mirror that shape exactly. Research should confirm the exact storage shape of `_pending_folders` before coding (see Assumptions A1, A2, A9).

The token dict (`_binary_move_tokens`) is initialised to `{}` and grows/shrinks alongside `_pending_binary_moves`.

**Depends on.** Component 2 (`self._binary_settle_seconds` must exist on the handler instance before the helpers in Component 4 can read it).

**Assumes.** A1 (folder-cooldown pattern shape), A2 (folder cooldown uses separate dicts for timer and token, not a combined dict), A9 (registry value shape — `(origin_src, latest_dst, token)` or equivalent).

**Done when.**
- A freshly constructed `_VaultEventHandler` instance has `_pending_binary_moves`, `_binary_move_tokens`, and `_binary_move_lock` as attributes (unit test asserting `hasattr`).
- Both dicts are empty at construction (`len == 0`).
- The lock is a `threading.Lock` instance (not `RLock`), matching the `_folder_lock` discipline.
- No regressions in the existing test suite.

---

#### 4. Add `_register_pending_binary_move`, `_reset_binary_move_timer`, and `_fire_binary_move_stable` helpers

**Goal.** Implement the three private helpers that drive the settle window — structurally identical to `_register_pending_folder`, `_reset_folder_timer`, and `_fire_folder_stable` — with the adaptations needed for binary move identity (filename key, origin-src preservation, two-Path payload).

**Build.**

In `src/vault/watcher.py`, in the `_VaultEventHandler` class body, after the existing `_fire_folder_stable` helper (line ~249), add three new private methods:

**`_register_pending_binary_move(self, key: str, origin_src: Path, dst: Path) -> None`**

Logic (in plain English, no code):
1. Acquire `self._binary_move_lock`.
2. Check whether an entry for `key` already exists in `_pending_binary_moves`.
   - If NO entry: this is the first hop. Store `(origin_src, dst)` as the value. Initialise `_binary_move_tokens[key] = 0`. Arm a `threading.Timer` for `self._binary_settle_seconds` that calls `_fire_binary_move_stable(key, origin_src, dst, token=0)`.
   - If an entry ALREADY EXISTS: this is a subsequent hop. The `origin_src` stored in the existing entry must be KEPT (do not overwrite it with the new hop's `src`). Update the entry's `latest_dst` to the new `dst`. Increment `_binary_move_tokens[key]`. Cancel the existing timer (it is stale — its token no longer matches). Arm a new `threading.Timer` for `self._binary_settle_seconds` calling `_fire_binary_move_stable(key, existing_origin_src, dst, token=new_token)`.
3. Release the lock.
4. Start the timer outside the lock (same pattern as `_register_pending_folder`).

Log a debug line: `"watcher.binary_settle_registered key=%s dst=%s token=%s"` — `%s`-style.

**`_reset_binary_move_timer(self, key: str, dst: Path) -> None`**

This is a convenience helper for re-entry into an existing pending slot. Logic: same as the "already exists" branch of `_register_pending_binary_move` above. In practice, the implementation may inline this or call it from `_register_pending_binary_move` — the planner decides the exact code split.

**`_fire_binary_move_stable(self, key: str, origin_src: Path, final_dst: Path, token: int) -> None`**

This is the timer callback. Logic (in plain English):
1. Acquire `self._binary_move_lock`.
2. Read the current token for `key` from `_binary_move_tokens`.
3. If the stored token != the passed `token`: this is a stale fire (a later hop superseded this one). Log a trace: `"watcher.binary_settle_superseded key=%s"`. Remove the registry entry if the stored token has already been cleaned up, or leave it for the live timer to clean. Release lock and return — do nothing else.
4. If the stored token matches: this is the real, final fire. Remove `key` from both `_pending_binary_moves` and `_binary_move_tokens`. Release lock.
5. Outside the lock, call T6's re-home with `(origin_src, final_dst)`: this means calling `_handle_binary_move(origin_src, final_dst)` directly (the function is on `self`, already defined). T6's logic — MoveGuard check, location resolution, placement, summary lookup, card write, DB update, audit — runs exactly as specified in the T6 spec. T7 passes `origin_src` as the `src` argument so T6 can look up the original summary via `_sibling_for(origin_src, cfg)`.

Log after the final fire: `"watcher.binary_settle_fired key=%s origin=%s dst=%s"` — `%s`-style.

**Depends on.** Component 3 (the registry dicts and lock must exist). Component 2 (`self._binary_settle_seconds` must exist). [REQUIRES: T6] — `_handle_binary_move` must have its rewritten cross-folder else branch before `_fire_binary_move_stable`'s call to it produces the re-home behavior (without T6, the call hits the old orphan path, which is safe but incorrect for the feature).

**Assumes.** A1 (folder-cooldown helper pattern shape confirmed), A4 (T6's re-home accepts an `origin_src` that is the chain origin, not necessarily the immediate predecessor — specifically that `_sibling_for(origin_src, cfg)` gives the correct DB lookup key).

**Interface shape.**
- All three are private methods on `_VaultEventHandler`.
- Not public; no external adapters. Tests call them directly on a constructed `_VaultEventHandler` instance (in-process).
- `_fire_binary_move_stable` is the timer callback — it runs on a `threading.Timer` thread, not the observer thread. Lock discipline is critical.

**Decisions.**
- Q: Should `_register_pending_binary_move` and `_reset_binary_move_timer` be two separate methods or inlined into one? Options: two methods (mirrors `_register_pending_folder` + `_reset_folder_timer` exactly — structural symmetry is the T7 rationale) / one method with an internal branch (slightly less code). Leaning two separate methods — the folder-cooldown pattern was designed to be readable and mirrors the T7 design intent; deviation from the pattern would need a justification. Planner confirms.
- Q: Where exactly does the timer get stored? Options: in `_pending_binary_moves` as a third element of the tuple (making it `dict[str, tuple[Path, Path, threading.Timer]]`) / not stored in the dict at all (let it run via closure; cancel by calling `timer.cancel()` on a reference held in the local scope before the new timer is started). The folder-cooldown pattern must be checked (A1, A2) to confirm which approach it uses — T7 mirrors it exactly.

**Done when.**
- Calling `_register_pending_binary_move("report.pdf", src_path, dst_path)` on a fresh handler creates an entry in `_pending_binary_moves` and `_binary_move_tokens` (unit test asserting dict membership).
- Calling it a second time with a new `dst_path` updates the `latest_dst` but keeps the original `origin_src` unchanged (unit test asserting `_pending_binary_moves["report.pdf"][0] == original_src`).
- Calling it a second time increments the token (assert `_binary_move_tokens["report.pdf"] == 1`).
- When the timer fires with the correct token, `_fire_binary_move_stable` removes the key from both dicts and calls `_handle_binary_move(origin_src, final_dst)`.
- When the timer fires with a stale token (superseded), `_fire_binary_move_stable` is a no-op — `_handle_binary_move` is NOT called. Log contains `watcher.binary_settle_superseded`.
- All log lines use `%s` formatting (no kwargs in `_log.*` calls).

---

#### 5. Intercept cross-folder binary re-home in `on_moved` to route through the settle window

**Goal.** Make the watcher's cross-folder binary move path go through the settle window instead of firing T6's re-home immediately — so multi-hop chains coalesce. The existing sibling-sync dispatch (TD-030) must remain first and unchanged.

**Build.**

In `src/vault/watcher.py`, in the `_VaultEventHandler.on_moved` method (lines ~280–299):

The existing logic in this block (approximate, to be verified by research):
1. If the path is a binary and internal: `_debounce("bin:{dst}", lambda: _handle_binary_move(src, dst), ...)` — sibling-sync dispatch (TD-030 first).
2. User `on_move` callback dispatch via `_debounce(str(dst), ...)`.

After T7, the cross-folder re-home path must be intercepted. The change is surgical:

1. Keep the `bin:{dst}` sibling-sync `_debounce` call EXACTLY as it is (TD-030, must not change).
2. After (or around) the existing `bin:{dst}` debounce, detect whether the move is cross-folder: `if src.parent != dst.parent` (the same condition that would route to the else branch in `_handle_binary_move`).
3. If cross-folder AND the path is a binary: compute the identity key `key = unicodedata.normalize("NFC", dst.name)` and call `self._register_pending_binary_move(key, origin_src=src, dst=dst)`. This replaces the re-home firing that would otherwise happen via `_handle_binary_move`'s else branch — but only for the re-home action. The sibling-sync (step 1) fires per-hop as before.
4. The `on_move` user callback dispatch (step 2 of the existing logic) is unchanged.

Important: `_handle_binary_move` is still called by the `bin:{dst}` `_debounce` in step 1 for the sibling-sync path. T7 does NOT prevent that call. T7 only prevents `_handle_binary_move`'s else branch (the re-home) from firing per-hop by ensuring that when `_fire_binary_move_stable` calls `_handle_binary_move(origin_src, final_dst)`, the function goes down the else branch for a cross-folder re-home.

The exact interception mechanism depends on how T6 structured its else-branch call and how the `bin:{dst}` debounce is constructed. Research (see Suggested research in Handoff notes) must confirm the exact `on_moved` code before the planner decides whether to:
- Replace the `bin:{dst}` debounce lambda with a two-action lambda (sibling-sync + register-settle), or
- Keep the `bin:{dst}` debounce for sibling-sync and add a separate register call in `on_moved` for the re-home settle.

The conservative implementation: add `self._register_pending_binary_move(key, src, dst)` after the `bin:{dst}` `_debounce` call when `src.parent != dst.parent` — so the sibling-sync fires normally via the debounce, and the re-home settle is registered separately. The fire method (`_fire_binary_move_stable`) calls `_handle_binary_move(origin_src, final_dst)` directly (bypassing the debounce) for the final re-home.

**Depends on.** Component 4 (`_register_pending_binary_move` must exist). [REQUIRES: T6] (T6's else branch must be the re-home; without T6, calling `_handle_binary_move` from the settle timer fires the old orphan path).

**Assumes.** A3 (`on_moved` structure: `bin:{dst}` dispatch fires first via `_debounce`; the else-branch re-home logic is what T7 coalesces).

**Decisions.**
- Q: Does the `bin:{dst}` sibling-sync debounce today directly invoke `_handle_binary_move`, which then internally decides same-folder vs cross-folder? Or does `on_moved` route to two different lambdas? Options: `on_moved` calls `_debounce("bin:{dst}", lambda: self._handle_binary_move(src, dst))` and the routing happens inside `_handle_binary_move` (one entry point) / `on_moved` already checks `src.parent != dst.parent` and routes to different lambdas. Research must confirm before coding (Suggested research item 1). The interception strategy differs depending on the answer.

**Done when.**
- When a binary is moved cross-folder while a `_VaultEventHandler` is running, `_register_pending_binary_move` is called (confirmed by a unit test that mocks it and asserts the call).
- The `bin:{dst}` sibling-sync `_debounce` still fires per-hop (sibling-sync is not suppressed). This is confirmed by the non-interference test: the watcher's sibling-sync path produces its sync events, while the re-home is coalesced.
- When a binary is moved within the same folder (rename), the settle window is NOT engaged — `_register_pending_binary_move` is NOT called (assert mock was not called for same-folder moves).
- The TD-030 ordering is preserved: the `bin:{dst}` dispatch fires before the settle registration for every hop.

---

#### 6. Add unit and integration tests for the settle window

**Goal.** Confirm the token guard works (stale fires are no-ops), the origin-src is preserved across hops, multi-hop chains coalesce into a single re-home call, and no regressions occur in the existing sibling-sync or single-hop paths.

**Build.**

Add a new test class `TestBinaryMoveSettleWindow` in `tests/test_vault/test_watcher_settle.py` (or a new section of `tests/test_vault/test_watcher.py`). All tests:
- Construct `_VaultEventHandler` with `vault_config=VaultConfig(root=tmp_path)` and `binary_settle_seconds=0.05` (50ms) so timers fire quickly in tests — no `CONFIG` import at module scope (C-17).
- Patch `vault.watcher._handle_binary_move` (not `vault.watcher._fire_binary_move_stable`, not `vault.watcher._handle_binary_move` via source) — TD-033.
- Use `threading.Event` or short `time.sleep` to synchronise timer firing in tests (same pattern as the existing folder-cooldown tests, if any; verify in research).

Tests to write:

1. **`test_single_hop_fires_once`** — call `_register_pending_binary_move` once for `"report.pdf"`. Wait for the timer. Assert `_handle_binary_move` called once with `(origin_src, dst)`. Assert registry is empty after fire.

2. **`test_two_hop_fires_once_at_final_dst`** — call `_register_pending_binary_move` with `("report.pdf", src_A, dst_B)`, then immediately with `("report.pdf", dst_B, dst_C)`. Wait for one timer cycle. Assert `_handle_binary_move` called exactly once — with `(src_A, dst_C)` (origin is `src_A`, final dst is `dst_C`). Assert `_handle_binary_move` was NOT called with `(src_A, dst_B)` or `(dst_B, dst_C)`.

3. **`test_token_guard_stale_fire_is_noop`** — call `_register_pending_binary_move` twice (two hops). Use a mock timer to fire the FIRST timer (stale token) before the second timer has a chance to fire. Assert `_handle_binary_move` is NOT called. Then let the second timer fire. Assert `_handle_binary_move` called once.

4. **`test_origin_src_preserved_across_three_hops`** — call `_register_pending_binary_move` three times with the same key but different `(src, dst)` pairs. After fire, assert `_handle_binary_move` receives the `src` from the FIRST registration as its first argument, not from the second or third.

5. **`test_registry_empty_after_stable_fire`** — after `_fire_binary_move_stable` completes, assert `_pending_binary_moves` and `_binary_move_tokens` are both empty (no leaked registry entries).

6. **`test_same_folder_move_does_not_engage_settle_window`** — trigger `on_moved` with `src.parent == dst.parent` (same-folder rename). Assert `_register_pending_binary_move` is NOT called. Assert the existing `bin:{dst}` sibling-sync fires normally (assert `_handle_binary_move` is called via the debounce path as before).

7. **`test_sibling_sync_fires_per_hop_not_suppressed`** — trigger `on_moved` twice (two hops). Assert the `bin:{dst}` sibling-sync fires TWICE (once per hop) — the settle window coalesces only the re-home, not the sync. Patch `vault.watcher._debounce` or the sync callback to count calls.

8. **`test_audit_row_count_for_chain`** — end-to-end with real `_handle_binary_move` (T6 already in place) and a real `audit_write` mock. Trigger a two-hop chain. Wait for settle. Assert `audit_write` was called exactly once with `outcome="REHOMED"`. Assert no audit row for the intermediate hop.

9. **`test_pipeline_moveguard_suppresses_settled_fire`** — configure `self._move_guard.check_and_consume` to return True for the final dst. Trigger one hop. After settle fires, assert `_handle_binary_move`'s re-home is suppressed (MoveGuard consumes once, at fire time, not at registration time). Assert `audit_write` is NOT called.

10. **`test_config_zero_settle_fires_immediately`** — use `binary_settle_seconds=0.0`. Register one hop. Assert `_handle_binary_move` is called (nearly) immediately without waiting. (This is the "disable coalescing" config, useful for tests that want instant settling.)

11. **`test_no_module_scope_config_import`** — assertion in the test file header: confirm that no `from core.config import CONFIG` appears at module scope (C-17 guard).

**Depends on.** Components 3, 4, and 5 (registry, helpers, and `on_moved` interception must all exist for these tests to exercise the real code paths).

**Assumes.** A1–A9.

**Done when.**
- All tests pass under `uv run pytest tests/test_vault/test_watcher_settle.py -m "not smoke"`.
- No patch target in the new tests uses any source-module name (e.g. `vault.watcher._handle_binary_move` is the correct target — NOT any source-of-origin path).
- No `from core.config import CONFIG` appears at module scope in the new test file.
- `uv run pytest tests/ -m "not smoke"` still passes with no regressions (count ≥ 798, current baseline per STATE.md).
- The existing watcher tests (`tests/test_vault/test_watcher.py`) still pass with no regressions — the `on_moved` interception does not break same-folder rename or other existing event paths.

---

### Handoff notes

- **[From T2 spec] Contract honored transitively:** T7's settle timer passes `(origin_src, final_dst)` to `_handle_binary_move`. T6 (inside `_handle_binary_move`) calls `resolve_placement(final_dst, loc_type, loc_name, cfg)` — using the `final_dst` T7 provides as the placement input. T7 does not call `resolve_placement` directly. The T2 spec's "Contract with T6" note confirms this is the expected usage pattern.

- **[From T6 spec] Contract honored:** T6 sources the sibling summary from the DB via `get_by_path(_vp(_sibling_for(src, cfg)))`. T7 passes `origin_src` (the first hop's source) as the `src` argument to `_handle_binary_move`, so the DB lookup key is the original sibling path — not a path at an intermediate location that may never have had a sibling written. This is the load-bearing correctness guarantee for multi-hop coalescing (T6 Handoff notes: "T7's spec must ensure the chain-origin src is threaded through to T6's re-home call"). T7 satisfies this by preserving `origin_src` in the registry and never overwriting it on subsequent hops.

- **[From T8 spec] Contract honored:** T8's MoveGuard check lives inside T6's re-home branch, AFTER the binary-sync dispatch. T7's settle window sits in front of T6's re-home call — the ordering is: `bin:{dst}` sync dispatch (TD-030, per hop) → settle window registration (per hop) → on settle fire: T6 re-home → MoveGuard check (once, at fire time). Intermediate hops never touch the MoveGuard. This matches the design doc requirement "T7 must place the settle window BEFORE [the T8 guard check] so a superseded hop never consumes a guard token."

- **Contract with Phase 2 Classify:** Phase 2 calls `resolve_placement` from its own pipeline path, not from the watcher. T7 adds no obligation for Phase 2.

- **Contract with T10 (reconcile migration):** T7 adds no vault writes and no new DB schema. T10 has no dependency on T7.

- **OQ resolved — identity key:** The design doc's open question "filename vs content-hash as identity key" is resolved in favour of filename (`unicodedata.normalize("NFC", dst.name)`). Rationale: single sequential human user; no file read per event; no TD-039 lock-file exposure. This decision is final (design doc §Cross-check: "The OQ 'identity key choice: filename vs content-hash' is **resolved** in favour of filename").

- **OQ resolved — dedicated config key:** The design doc's open question "reuse `folder_cooldown_seconds` or add a dedicated key" is resolved in favour of a dedicated `binary_settle_seconds` key. Rationale: two unrelated timings should be independently tunable; coupling them is a semantic smell the design doc explicitly flags. This decision is final (design doc §Cross-check).

- **Known limitation — same-name concurrent files:** Two files with the same filename moving concurrently (e.g. `Alpha/report.pdf` and `Beta/report.pdf` both moved at the same time) share the `"report.pdf"` identity key and one re-home may be attributed to the wrong chain. This is accepted for the single sequential human user target. Document this limitation in the `_register_pending_binary_move` docstring.

- **Open uncertainty — `on_moved` exact structure:** The spec's Component 5 describes the interception but defers the exact code shape to research (see Suggested research item 1). Two different interception strategies exist depending on whether `on_moved` routes to one `_debounce` call (and `_handle_binary_move` internally branches) or two separate lambdas. The planner must read `on_moved` before writing Component 5 code.

- **Open uncertainty — timer storage shape:** Component 3 and 4 note that the exact shape of `_pending_binary_moves` (whether the timer is stored in the dict or held in local scope only) depends on how `_pending_folders` is structured (A1, A2, A9). Research must confirm before coding the helpers.

- **Suggested research for /research:**
  1. Read `src/vault/watcher.py:280–299` verbatim to confirm the exact structure of `on_moved` — specifically: does the `bin:{dst}` debounce call `_handle_binary_move` as its lambda, and does `on_moved` itself detect `src.parent != dst.parent`, or does that detection happen only inside `_handle_binary_move`? This determines the T7 interception strategy for Component 5.
  2. Read `src/vault/watcher.py:182–249` verbatim to confirm the exact shape of `_register_pending_folder`, `_reset_folder_timer`, and `_fire_folder_stable` — specifically: (a) is the `threading.Timer` stored in `_pending_folders` or in local scope? (b) what is the exact type of `_pending_folders` values? (c) how is the token incremented (is it a module-level counter or a per-key counter in `_folder_tokens`)? T7's helpers must be structurally symmetric (A1, A2, A9).
  3. Read `src/vault/watcher.py:89–122` verbatim to confirm the exact `__init__` parameter names for `folder_cooldown_seconds` (or equivalent) and how it is threaded to `_VaultEventHandler`. Verify A5.
  4. Read `src/cli/main.py:253–260` verbatim to confirm how `VaultWatcher` is constructed in `watch()` — specifically whether named kwargs are used and which config paths are read. Verify A7.
  5. Read `src/core/config.py:231–238` verbatim to confirm `CaptureConfig` structure and the name of the `folder_cooldown_seconds` Field (exact name needed to know where to add `binary_settle_seconds`). Verify A6.
  6. Confirm that `unicodedata` is already imported in `src/vault/watcher.py` (grep: `import unicodedata`). Verify A8.
  7. Confirm current test baseline count: `uv run pytest tests/ -m "not smoke" --co -q | tail -1` (must be ≥ 798 before any edits).

---

## T8 — Sticky-note: suppress pipeline-initiated moves

### Purpose

When the capture pipeline relocates a binary file — for example, moving a newly-captured PDF into `attachment/` — that relocation looks identical to a human drag from the watcher's perspective. Once T6 adds the re-home behaviour (re-derive location, move the binary to its type-correct folder, rebuild the summary card), the watcher would fire a re-home on every pipeline move, undoing the pipeline's own work in an infinite loop.

T8 breaks that loop before it can start. It introduces a small "sticky note" registry: immediately before the pipeline performs a binary move, it drops a short-lived note naming the destination path. When the watcher's re-home branch fires, it checks whether that destination is on the sticky note. If yes, it skips the re-home and consumes the note. If no, it treats the event as a genuine user move and re-homes normally. The note expires on its own after a few seconds, so a crash mid-move can never permanently silence the watcher.

After this task the codebase has: a standalone `MoveGuard` module (the registry), three pipeline-side registration call sites, and one watcher-side check inside T6's re-home branch — all sharing one instance via a module-level accessor bound at `kms watch` startup.

[REQUIRES: T3 move sites] [SHIP SAME INCREMENT AS T6]

---

### Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `_VaultEventHandler.__init__` | `src/vault/watcher.py:89–122` | Watcher event handler constructor; stores `self._root`, `self._vault_config`, `self._timers`, `self._lock`, `self._pending_folders`, `self._folder_tokens`, `self._folder_lock` | T8 adds `self._move_guard: MoveGuard` injected from `VaultWatcher`; the handler calls `self._move_guard.check_and_consume(dst)` inside the T6 re-home branch | Deep |
| `VaultWatcher.__init__` | `src/vault/watcher.py:465–503` | Creates `_VaultEventHandler` and the watchdog `Observer`, threading config into the handler | T8 extends this to create a `MoveGuard` and pass it to the handler | Deep |
| `_debounce` | `src/vault/watcher.py:151–159` | Cancels any existing timer for a key and schedules a new one | Listed here to clarify that T8 does NOT add a new debounce timer — it uses a lock-guarded dict, not a timer registry, to track moves | Shallow |
| `on_moved` dispatch | `src/vault/watcher.py:280–299` | Debounces `on_moved` events; fires `_handle_binary_move` via `bin:{dst}` key for binary files, then fires the user `on_move` callback | T8 does NOT touch `on_moved`; the guard check lives inside `_handle_binary_move`'s else branch (which T6 writes) | Deep |
| `_handle_binary_move` else branch | `src/vault/watcher.py:420–449` | Currently orphans the old sibling when the binary moves to a different folder | T8 adds `self._move_guard.check_and_consume(final_binary)` at the entry of this else branch (the T6 re-home branch) — if True, log and return early | Deep |
| `threading.Lock` | stdlib | Used for the existing `self._lock` and `self._folder_lock` registries inside `_VaultEventHandler` | T8's `MoveGuard` uses its own `threading.Lock` internally; no shared lock with the handler | Shallow |
| `threading.Timer` | stdlib | Used by `_debounce` for existing debounce windows | T8 does NOT use `threading.Timer` — expiry is lazy (checked on `check_and_consume`, using monotonic timestamps), not a fired callback | Shallow |
| `move_attachment` | `src/vault/writer.py:241` | Moves a binary file through the vault writer | T8 wraps the three call sites that call `move_attachment` (or `move_folder`) in capture.py with a guard-registration call immediately before each move | Deep |
| `move_folder` | `src/vault/writer.py:304` | Moves a whole folder through the vault writer | Third registration site: `capture_folder` calls this at `capture.py:1297`; T8 registers the destination folder path before this call | Shallow |
| `watch` command | `src/cli/main.py:145–260` | Sets up the watcher loop, wires callbacks, creates `VaultWatcher` | T8 calls `vault.move_guard.set_active(guard)` here after creating the guard and before starting the watcher, so the pipeline can retrieve the same instance via `get_active()` | Deep |
| `asyncio.run_coroutine_threadsafe` | `src/cli/main.py:203–204, 222–223` | Dispatches `capture_file(...)` from the observer thread to the asyncio loop thread | The pipeline's move calls execute on the asyncio-loop thread or a pool worker thread; the guard check runs on the observer thread — confirms the registry must be thread-safe (lock-guarded) | Shallow |
| `_log = logging.getLogger(__name__)` | `src/vault/watcher.py:46` | Module-level stdlib logger | T8's skip log line in the watcher uses `_log.info("watcher.rehome_skip path=%s reason=pipeline_initiated", dst)` — `%s`-style formatting, no kwargs | Shallow |

---

### Feature overview

The feature has two cooperating sides: the pipeline (producer) and the watcher (consumer).

**Producer side — pipeline registration.**
In three places inside `src/pipelines/capture.py`, immediately before a binary or folder move is executed, the pipeline asks: "is there a live `MoveGuard` right now?" If yes (we are running under `kms watch`), it calls `guard.register(dst)` with the exact destination path the move will use. Outside `kms watch` (batch `kms capture`, reconcile, tests), `get_active()` returns None and the registration is silently skipped — no overhead, no import-time side effect.

The three registration sites are:
1. `capture.py:658` — `move_attachment(src, attachment_dst)` in the LOCATED branch of `_store_nonmd` (registered by T3).
2. `capture.py:711` — `move_attachment(src, inbox_dst)` in the CLUELESS/inbox-park branch of `_store_nonmd`.
3. `capture.py:1297` — `move_folder(folder_path, destination)` in `capture_folder`.

In each case the pattern is:
```
g = get_active()
if g:
    g.register(dst)
# then the existing move call unchanged
```

**Consumer side — watcher guard check.**
Inside `_handle_binary_move`'s cross-folder else branch (which T6 rewrites into the re-home logic), the very first thing the re-home branch does is ask the guard: `self._move_guard.check_and_consume(final_binary)`. If this returns True, the pipeline registered that destination — the move was not a human drag — so the watcher logs `watcher.rehome_skip path=<dst> reason=pipeline_initiated` and returns immediately with no re-home and no audit row. If it returns False, the re-home proceeds normally.

The `check_and_consume` operation is consume-once: it removes the entry and returns True only once per registered path. Expired entries (older than the TTL) are lazily dropped on every call.

**The registry itself (`MoveGuard`).**
A small class in `src/vault/move_guard.py`:
- Internal state: a `dict[str, float]` mapping NFC-normalised path strings to expiry timestamps (monotonic clock), plus a `threading.Lock`.
- `register(path, ttl=None)`: acquires the lock, inserts/updates the entry. The TTL defaults to approximately `debounce_seconds + margin` (around 5 s total) — long enough to outlast watchdog's event delivery and the existing 3 s debounce window, short enough that a genuine human move a few seconds later is NOT suppressed.
- `check_and_consume(path) -> bool`: acquires the lock, lazily drops expired entries, checks whether the NFC-normalised path string is present. If yes, removes the entry and returns True. If no, returns False.
- Path key normalisation: `unicodedata.normalize("NFC", str(path))` — must match the normalised paths watchdog delivers on macOS (CLAUDE.md gotcha: vault-relative paths from `self._root` use NFC normalisation).

**Module-level accessor (`set_active` / `get_active`).**
Two module-level functions in `move_guard.py`:
- `set_active(guard: MoveGuard) -> None`: stores the guard in a module-level variable.
- `get_active() -> MoveGuard | None`: returns the stored guard, or None if never set (i.e. outside `kms watch`).

`cli/main.py`'s `watch()` function calls `set_active(guard)` after creating the guard and before passing it to `VaultWatcher`. The pipeline's registration sites call `get_active()`.

**TTL and crash safety.**
The TTL ensures that if the pipeline crashes after calling `register` but before the move completes, the guard entry automatically expires and the watcher is not permanently silenced. The lazy-expiry design means no background thread is needed; expiry happens on the next `check_and_consume` call.

---

### Out of scope

- **T6 re-home branch implementation** — T8 only provides the registry and adds the `check_and_consume` call at the entry of the T6 branch. The full re-home logic (DB lookup, `resolve_placement`, sibling write, audit row) is owned by T6.
- **Suppressing the existing `_handle_binary_move` sibling-sync** — T8 suppresses ONLY the T6 re-home branch. The existing `_handle_binary_move` binary-sync logic (`bin:{dst}` debounce via `on_moved`, watcher.py:288–291) is NOT gated by the guard. The pipeline (T3) already writes the sibling at the final destination before moving the binary (DECISION-025 sibling-first), so when the watcher's sibling-sync fires for a pipeline move, it finds the sibling already in place and does nothing meaningful — but T8 does not add new logic to gate it. This decision is flagged for T6: if T6 decides the re-home subsumes sibling-sync, T6 may extend the guard. See Handoff notes.
- **T7 settle-window implementation** — T7's pending-binary-move registry is a separate data structure (keyed on filename) that coalesces multi-hop chains. It is built after T8. T7 places its settle window in front of the T6 re-home call; T8's `check_and_consume` is called inside the T6 re-home entry (i.e. after the settle window fires). This ordering ensures intermediate hops never consume a guard token.
- **Registration for `move_note` in pipeline sweeps** — T4's misplaced-md sweep calls `move_note` to push a `.md` to inbox. The watcher's re-home branch (`_handle_binary_move`) fires only on BINARY events (files that `_is_binary(path)` returns True). A `.md` file moved by T4's sweep does not go through `_handle_binary_move`, so no registration is needed for `move_note` calls in `scan_capture`. (If T10's reconcile runs while the watcher is live, T10 should register its `move_attachment` calls — see Handoff notes.)
- **Reconcile / T10 wiring** — T10's `reconcile_editable_migration` stage calls `move_attachment`. If the watcher is live during reconcile, registration is recommended (the design doc flags it as a SOFT dependency). T10's spec handles that wiring. T8 only adds the registration to the three capture call sites.
- **Persistence across restarts** — the registry is in-process memory only. A `kms watch` restart starts with an empty registry, which is correct: pipeline moves from a prior session are already complete and any watcher events for them have already been delivered and consumed.

---

### Constraints

- **C-01 · Vault-only writes** — T8 itself performs no vault writes. The pipeline's `move_attachment`/`move_folder` calls that T8 wraps with a registration continue to go through `vault/writer.py`. The registration call (`guard.register(dst)`) touches only in-memory state. Source: `CONSTRAINTS.md` C-01; hook hard-block.
- **C-12 · Result type** — `MoveGuard.register` and `MoveGuard.check_and_consume` are private infrastructure helpers (analogous to `_debounce`, `_register_pending_folder`), not public `handlers/` or `pipelines/` functions. They return `None` and `bool` respectively — consistent with the existing private helper convention. The pipeline registration sites (`g.register(dst)`) are one-liners added inside existing `Result`-returning functions; they do not change the Result contract. Source: `CONSTRAINTS.md` C-12 (scoped to public functions in `handlers/` and `pipelines/`).
- **C-13 · Audit log** — T8 makes no AI decision; suppression is the *absence* of an action, not a decision. No `audit_write` call is needed for a suppressed re-home. A `_log.info(...)` line is the correct artefact. Source: `CONSTRAINTS.md` C-13; design doc T8 §Guardrail Checklist.
- **C-17 · No module-scope CONFIG import in tests** — tests for `MoveGuard` construct it directly (`MoveGuard()`) without needing any vault config. Tests that cover the registration sites construct `VaultConfig(root=tmp_path)` and build a `PipelineContext` explicitly. Source: `CONSTRAINTS.md` C-17.
- **Thread-safety (CLAUDE.md)** — the registry is accessed from the observer thread (watchdog callback, `check_and_consume`) and from the asyncio-loop thread or pool worker thread (pipeline, `register`). The lock must be acquired for both operations. Source: design doc T8 §Implications "genuine cross-thread shared state → registry MUST be lock-guarded".
- **Debounce-key uniqueness (CLAUDE.md)** — T8 does not add new debounce keys. The guard check and registration are NOT debounced; they are synchronous lock-guarded dict operations. The existing `bin:{dst}` and `str(dst)` debounce keys are unchanged. Source: CLAUDE.md "Two `_debounce` calls with same key cancel each other."
- **TD-030 · on_moved ordering** — the existing `bin:{dst}` binary-sync dispatch in `on_moved` must remain before any re-home or guard logic. T8 does not touch `on_moved`; the guard check lives inside `_handle_binary_move`'s else branch. TD-030 ordering is preserved. Source: CLAUDE.md TD-030.
- **TD-033 · Monkeypatch the importing module** — tests that patch `get_active` must patch `vault.move_guard.get_active` (the function in the new module) OR `pipelines.capture.get_active` (the name imported into `capture.py`) depending on which module's behaviour is under test. Tests for `_handle_binary_move` guard behaviour must patch `vault.watcher.MoveGuard` or the `_move_guard` attribute directly. Source: CLAUDE.md TD-033.
- **`%s`-style stdlib logging** — the skip log line in `watcher.py` uses `%s` positional formatting: `_log.info("watcher.rehome_skip path=%s reason=pipeline_initiated", dst)`. No kwargs. Source: CLAUDE.md "What Claude gets wrong."
- **[REQUIRES: T3]** — T3's LOCATED move site at `capture.py:658` must exist before T8 can wrap it. T8 also wraps the CLUELESS inbox-park site at `capture.py:711` and the folder-move site at `capture.py:1297` — these exist today and do not require T3 to land first, but T3 and T8 must ship in the same increment so the LOCATED site is covered. Source: design doc T8 §Cross-check; build order.
- **[REQUIRES: T6]** — T6 is the sole consumer of `check_and_consume`. T8 and T6 must ship in the same increment. Source: design doc T8 §Decisions; build order L421.

---

### Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|---|---|---|
| A1 | `_VaultEventHandler.__init__` at `watcher.py:89–122` stores all per-handler config as `self.*` attributes; adding `self._move_guard` as a new attribute follows the same pattern as `self._timers`, `self._lock` etc. | Design doc T8 §Implications "same process" — confirmed constructor holds existing registries | If the constructor is significantly different from the described pattern (e.g. uses `__slots__` or a different attribute model), the injection approach must change |
| A2 | `VaultWatcher.__init__` at `watcher.py:465–503` creates `_VaultEventHandler` directly and can accept a `MoveGuard` instance to pass through; no external factory or DI framework is involved | Design doc T8 §Implications "same process" — watcher creates the handler, CLI creates the watcher | If `VaultWatcher` or `_VaultEventHandler` are constructed via a factory or have a fixed constructor signature that cannot accept new parameters, the injection wiring must change |
| A3 | The three pipeline move call sites exist at exactly these locations: `capture.py:658` (`move_attachment` in LOCATED branch), `capture.py:711` (`move_attachment` in CLUELESS/inbox branch), `capture.py:1297` (`move_folder` in `capture_folder`) | Design doc T8 §Implications §"Pipeline-initiated move" — all three sites named with line numbers | If any site has moved, been inlined, or been removed by prior work (e.g. T3 refactor shifted line numbers), the registration wraps must be placed at the correct new locations |
| A4 | `unicodedata` is already imported at module scope in `src/vault/watcher.py` (the existing NFC-normalisation pattern uses it) | CLAUDE.md "vault-relative paths" gotcha; design doc T8 §Implications "NFC-normalised" | If `unicodedata` is not yet imported in `watcher.py`, it must be added — no functional change but an import-list edit |
| A5 | `cli/main.py`'s `watch()` function (lines ~145–260) constructs `VaultWatcher` with a specific set of named kwargs and can accommodate a new `move_guard` kwarg (or the guard can be injected in a subsequent `watcher.set_guard(guard)` call if the constructor is already frozen) | Design doc T8 §Implications "bound at watch wiring (cli/main.py:253–260)" | If `VaultWatcher.__init__` uses `*args` or has some other pattern that prevents adding a kwarg without breaking existing tests, an alternative injection mechanism (e.g. a `set_move_guard` method) must be used |
| A6 | `move_attachment` at `capture.py:658` and `capture.py:711` and `move_folder` at `capture.py:1297` are called with a named `dst` (or positional second argument) that is a `Path` object — the same object the pipeline intends to move to and that watchdog will later report as the `dst` in the `on_moved` event | Design doc T8 §Decisions — "register the exact `attachment_dst`/`inbox_dst`/folder `destination` Path objects the move uses" | If the pipeline constructs an intermediate path object and renames it before the move (e.g. collision-loop produces `attachment_dst` but the actual disk call uses a different path), the registered path would not match the watchdog event path and the guard would never fire True |
| A7 | watchdog delivers paths to `on_moved` as absolute, NFC-normalised (on macOS) `Path` objects or strings that, when normalised to NFC with `unicodedata.normalize("NFC", str(path))`, exactly match the path key stored in the guard by the pipeline's `register(dst)` call | CLAUDE.md "vault-relative paths from `self._root`" gotcha; design doc T8 §Recommendation — "register the exact dst Path objects the move uses"; must match watchdog event path | If watchdog delivers NFD-normalised paths on macOS but the pipeline's `dst` Path is NFC, NFC-normalising both (as the guard does) should produce matching strings — but if the file system or watchdog introduces other normalization differences, the guard check would always return False |
| A8 | The TTL chosen (~5 s, derived as `debounce_seconds + margin`) outlasts the watchdog event delivery + the existing 3.0 s debounce window, so `check_and_consume` is called before the entry expires for any normal pipeline move | Design doc T8 §Implications "TTL must outlast watchdog's event-delivery + debounce delay (`debounce_seconds` default 3.0s)"; design doc §Decisions "Recommended TTL ≈ debounce_seconds + small margin (~5s)" | If the actual debounce window is larger than 3.0 s (e.g. the user changed `debounce_seconds` to 10 in config), the guard entry may expire before `check_and_consume` fires, causing a spurious re-home |
| A9 | `src/vault/move_guard.py` does not yet exist in the codebase | Design doc T8 §Implications "New (Option A): `src/vault/move_guard.py`" | If a `move_guard.py` already exists from a prior branch, the spec would create a duplicate; a grep for the filename in `src/vault/` would catch this |
| A10 | The `watch()` function in `cli/main.py` is the only call site that constructs `VaultWatcher` in production; tests that construct `VaultWatcher` directly can pass `None` (or a stub `MoveGuard`) for the guard parameter without breaking existing test behaviour | Design doc T8 §Implications "instantiated by VaultWatcher and injected into the handler"; existing tests do not exercise re-home | If other production call sites construct `VaultWatcher` (e.g. a scheduled job), those sites must also create and wire a `MoveGuard` |

---

### Component dependency order

#### 1. Create `src/vault/move_guard.py` — the `MoveGuard` class and module-level accessor

**Goal.** Provide a small, independently testable registry that lets the pipeline leave a short-lived note before a binary move, and lets the watcher consume that note to decide whether the event was pipeline-initiated.

**Build.**

Create `src/vault/move_guard.py` with the following contents (described behaviourally; exact code is for the planner):

The module has two parts:
1. A `MoveGuard` class with two public methods:
   - `register(path: Path, ttl: float | None = None) -> None` — acquires an internal `threading.Lock`, inserts or updates the entry `NFC_normalise(str(path)) → monotonic_expiry` in an internal dict. The TTL defaults to a module-level constant `_DEFAULT_TTL_SECONDS` (~5.0). After inserting, releases the lock.
   - `check_and_consume(path: Path) -> bool` — acquires the lock, iterates the dict and drops any entries whose expiry is in the past (lazy expiry), then checks whether the NFC-normalised path string is present. If yes, removes it and returns True. If no, returns False. Releases the lock regardless.
   - Path key normalisation for both methods: `unicodedata.normalize("NFC", str(path))`.

2. Two module-level accessor functions:
   - `set_active(guard: "MoveGuard") -> None` — stores `guard` in a module-level variable `_active: MoveGuard | None = None`.
   - `get_active() -> "MoveGuard | None"` — returns `_active`.

The module must import `threading`, `time`, `unicodedata`, and `pathlib.Path` at module scope. It must NOT import `CONFIG` or any project module at module scope (keeps it free of circular-import risk and test-hostile singletons).

`_DEFAULT_TTL_SECONDS` is a module-level constant (not a hardcoded literal buried in a branch), defaulting to `5.0`. The planner may make this configurable via `VaultConfig` or `CaptureConfig` in a follow-up; for this task a named constant is sufficient.

**Depends on.** None — this component has no project-code dependencies.

**Assumes.** A9 (file does not yet exist).

**Interface shape.**
- Public API: `register(path, ttl=None) -> None` and `check_and_consume(path) -> bool` on `MoveGuard`; `set_active(guard) -> None` and `get_active() -> MoveGuard | None` at module level.
- Two real adapters from day one: the pipeline (producer, calls `register`) and the watcher (consumer, calls `check_and_consume`). Not a speculative seam.
- Dependency category: in-process. No adapter needed; test by constructing `MoveGuard()` directly.
- The `threading.Lock` ensures this is safe under concurrent access from different threads; callers do not need their own synchronisation.

**Decisions.**
- Q: Should the TTL be read from `CaptureConfig` (a new field like `move_guard_ttl_seconds`) or left as a module constant? Options: module constant (no config plumbing required, easy to change in code, no test-config complexity) / config field (tunable without code change, consistent with project "behaviour is data" principle). Leaning module constant for MVP because TTL is an infrastructure detail the user would never tune, and adding it to config requires threading it through `VaultWatcher` — scope creep for this task. Planner notes this decision for T8.
- Q: Should `set_active`/`get_active` use a simple module-level variable or a thread-local? Options: module-level variable (simpler; `watch()` is the only setter; the value is set once and read many times) / thread-local (over-engineering; the guard is a process-level singleton, not a per-thread value). Leaning module-level variable.

**Done when.**
- `from vault.move_guard import MoveGuard, set_active, get_active` succeeds in a Python REPL.
- `guard = MoveGuard()`: calling `guard.register(Path("/vault/Projects/A/attachment/report.pdf"))` followed immediately by `guard.check_and_consume(Path("/vault/Projects/A/attachment/report.pdf"))` returns True; a second call to `check_and_consume` with the same path returns False (consume-once).
- After registering and advancing time past the TTL (mock `time.monotonic`), `check_and_consume` returns False (expired entry).
- `get_active()` returns None before `set_active` is called; returns the guard after `set_active(guard)`.
- No `from core.config import CONFIG` appears in `move_guard.py`.
- The module file exists at `src/vault/move_guard.py`.

---

#### 2. Wire `MoveGuard` into `VaultWatcher` and `_VaultEventHandler`

**Goal.** Make the watcher hold a reference to the shared `MoveGuard` instance so that the re-home branch can call `check_and_consume` without importing a global singleton.

**Build.**

In `src/vault/watcher.py`:

1. Add `from vault.move_guard import MoveGuard` to the module-level imports (alongside the existing imports from `vault.paths`, `vault.writer`, etc.).

2. In `VaultWatcher.__init__` (watcher.py:465–503): create a `MoveGuard` instance (or accept one as an optional parameter `move_guard: MoveGuard | None = None`, creating a new one if None). Store it as `self._move_guard`. Pass it to `_VaultEventHandler`.

3. In `_VaultEventHandler.__init__` (watcher.py:89–122): add a `move_guard: MoveGuard` parameter; store it as `self._move_guard`. This attribute is later read by the T6 re-home branch.

No other changes to `_VaultEventHandler.__init__` or `VaultWatcher.__init__` at this step.

**Depends on.** Component 1 (`MoveGuard` must exist and be importable from `vault.move_guard`).

**Assumes.** A1, A2, A4.

**Interface shape.**
- `VaultWatcher.__init__` gains an optional `move_guard: MoveGuard | None = None` parameter (creates one if not supplied, so existing call sites need no change unless they want to inject a specific guard).
- `_VaultEventHandler.__init__` gains a `move_guard: MoveGuard` parameter (required; supplied by `VaultWatcher`).
- Existing watcher tests that construct `_VaultEventHandler` or `VaultWatcher` directly must pass a `MoveGuard()` instance (or rely on the `None`-creates-new default on `VaultWatcher`).
- Dependency category: in-process.

**Decisions.**
- Q: Should `VaultWatcher` create its own `MoveGuard` unconditionally, or accept one via constructor injection? Options: create unconditionally (simpler; fewer constructor params) / accept optional injection (testable — tests can pass a pre-configured guard or a spy; the `cli/main.py::watch()` call passes the same guard it sets as active). Leaning optional injection so that `cli/main.py` can wire the single shared instance and pass it to `VaultWatcher` in one statement.

**Done when.**
- Constructing `VaultWatcher(root=tmp_path, vault_config=VaultConfig(root=tmp_path), on_create=..., on_move=..., on_delete=...)` does not raise `TypeError` (no required parameter added without a default, or the default creates a fresh `MoveGuard`).
- `VaultWatcher._event_handler._move_guard` is a `MoveGuard` instance (not None).
- `uv run pytest tests/test_vault/ -m "not smoke"` still passes with no regressions after this change.

---

#### 3. Wire `set_active(guard)` into `cli/main.py`'s `watch()` function

**Goal.** Ensure that the pipeline can reach the same `MoveGuard` instance that the watcher holds, by storing it in the module-level accessor before starting the watcher loop.

**Build.**

In `src/cli/main.py`, inside the `watch()` Click command (lines ~145–260):

1. Add `from vault.move_guard import set_active as set_active_guard` (or `from vault import move_guard`) to the lazy imports or to the top of `watch()` — consistent with the existing lazy-import pattern in that function.

2. After constructing the `VaultWatcher` (and before calling `watcher.start()`), call `set_active_guard(watcher._move_guard)` to publish the guard to the module-level accessor. This ensures the pipeline's `get_active()` calls return the same instance that `_VaultEventHandler._move_guard` holds.

No other changes to `watch()`.

**Depends on.** Components 1 and 2 (`MoveGuard` must exist; `VaultWatcher._move_guard` must be accessible).

**Assumes.** A5, A10.

**Done when.**
- After `watch()` starts, `vault.move_guard.get_active()` returns a non-None `MoveGuard` instance that is the same object as `watcher._event_handler._move_guard` (identity check: `get_active() is watcher._event_handler._move_guard`).
- `uv run pytest tests/ -m "not smoke"` still passes with no regressions.

---

#### 4. Add guard registration at the three pipeline move sites in `capture.py`

**Goal.** Ensure that every pipeline-initiated binary or folder move is pre-announced to the `MoveGuard`, so the watcher can recognize it and skip re-home.

**Build.**

In `src/pipelines/capture.py`, add `from vault.move_guard import get_active` to the module-level imports (so it is patchable as `pipelines.capture.get_active` per TD-033).

Then wrap each of the three move call sites with the registration guard:

**Site 1 — LOCATED branch, `_store_nonmd` (approximately line 658).**
Before the `move_attachment(src, attachment_dst)` call, insert:
```
_g = get_active()
if _g:
    _g.register(attachment_dst)
```

**Site 2 — CLUELESS/inbox-park branch, `_store_nonmd` (approximately line 711).**
Before the `move_attachment(src, inbox_dst)` call, insert the same pattern with `inbox_dst` as the argument to `_g.register`.

**Site 3 — `capture_folder` folder move (approximately line 1297).**
Before the `move_folder(folder_path, destination)` call, insert the same pattern with `destination` as the argument.

In all three cases the registered path must be the exact `Path` object passed to the move call (not a string, not a parent directory) — this is what watchdog will report as the `dst` in the subsequent `on_moved` event.

Important: these three lines do NOT change the Result return type, the `move_attachment`/`move_folder` call itself, the collision-loop logic, or any other behaviour. They are purely additive one-liner guards that fire only when `get_active()` is non-None.

**Depends on.** Components 1 and 3 (`MoveGuard` must exist and `set_active` must have been called by `watch()` before any capture runs under the watcher).

**Assumes.** A3, A6.

**Done when.**
- After a `kms watch`-driven capture of a no-edit PDF into `attachment/`, calling `vault.move_guard.get_active().check_and_consume(attachment_dst)` before the watcher's debounce fires returns True (the registration was made and the entry exists).
- `_store_nonmd` and `capture_folder` still return `Success` / `Failure` as before; the registration calls do not introduce new failure modes or change the Result contract.
- Outside `kms watch` (e.g. `kms capture <file>` run standalone), `get_active()` returns None and no registration occurs — no side effects, no errors.

---

#### 5. Add the `check_and_consume` call inside the T6 re-home branch of `_handle_binary_move`

**Goal.** Make the watcher's re-home branch silently exit without performing any re-home when the move destination was registered by the pipeline.

**Build.**

In `src/vault/watcher.py`, inside the else branch of `_handle_binary_move` (the cross-folder branch that T6 rewrites into the re-home logic):

At the very first line of the else branch, before any `_location_context` or `resolve_placement` call, insert:

```
if self._move_guard.check_and_consume(final_binary):
    _log.info("watcher.rehome_skip path=%s reason=pipeline_initiated", final_binary)
    return
```

Where `final_binary` is the computed final binary path (after `resolve_placement` determines `needs_move` — or, if the MoveGuard check must happen before `resolve_placement`, use `dst` since that is what the pipeline registered).

Note on ordering: the design doc states the guard check should use `final_binary` (the post-`resolve_placement` path), but the pipeline registers `dst` (the watchdog event `dst`, before `resolve_placement` may move it again). For no-edit files, `dst` and `final_binary` are the same (binary stays in `attachment/`). For editable files, `final_binary` may differ from `dst` (the binary is moved from `dst` to the project root). This means: for editable-file pipeline captures, the registered path is `dst` (= `attachment_dst` from the pipeline) but `final_binary` after `resolve_placement` would be the project root. The guard would be checked against `final_binary` but only `dst` was registered → guard returns False → spurious re-home.

Resolution (to be verified by research): the pipeline's LOCATED branch registers `attachment_dst` (the immediate destination of `move_attachment`). For editable files, `attachment_dst = placement.final_dir / final_name` = the project root path. For no-edit files, `attachment_dst = placement.final_dir / final_name` = the attachment path. So in both cases `attachment_dst` IS the final binary path. The guard should be checked against `dst` (the watchdog event path, which equals `attachment_dst` since that is what `move_attachment` places the file at) — unless `resolve_placement` determines `needs_move=True` for a second adjustment from the watcher perspective (which should not happen since the pipeline already put the file at the right location). The planner must verify this during research: confirm that `attachment_dst` in the pipeline (Component 4, Site 1) equals the watchdog `dst` and that `resolve_placement(dst, …)` returns `needs_move=False` for a file the pipeline already correctly placed.

For safety, the guard check should use `dst` (the watchdog event path, which is what the pipeline registered as `attachment_dst`). The `_log.info` call uses `%s` formatting (CLAUDE.md `logging` rule).

This component is explicitly co-dependent with T6: T8 provides the `_move_guard` attribute and the `check_and_consume` call; T6 provides the surrounding re-home branch that wraps the check. They must be written together in the same increment. If T6's branch does not yet exist, this component is a stub that can be placed at the top of the current orphan else branch; T6 will then build around it.

**Depends on.** Components 1 and 2 (`MoveGuard` and `self._move_guard` must exist).

**Assumes.** A1, A7.

**Done when.**
- A pipeline-initiated capture of a no-edit PDF (pipeline calls `move_attachment(src, attachment_dst)` and registers `attachment_dst`) produces ZERO `watcher:binary_rehome` audit rows for that destination.
- A log line `watcher.rehome_skip path=<dst> reason=pipeline_initiated` appears in the log exactly once for each suppressed pipeline move.
- A genuine user drag of the same file (without any pipeline registration) produces exactly ONE `watcher:binary_rehome` audit row (the re-home fires normally).
- The `_log.info` call uses `%s` formatting — no kwargs.

---

#### 6. Add unit tests for `MoveGuard` (register / check_and_consume / expiry / thread safety)

**Goal.** Confirm the registry's core contract: consume-once semantics, lazy expiry, thread-safe operation, and the cross-thread register-then-check pattern — all without needing a vault on disk.

**Build.**

Create `tests/test_vault/test_move_guard.py`. All tests construct `MoveGuard()` directly — no `VaultConfig`, no `CONFIG` import.

The tests cover:

1. **`test_register_and_consume_returns_true`** — `register(path)` then `check_and_consume(path)` returns True.

2. **`test_consume_once_second_call_returns_false`** — `register(path)` then two consecutive `check_and_consume(path)` calls: first returns True, second returns False.

3. **`test_unregistered_path_returns_false`** — `check_and_consume(path)` on a never-registered path returns False.

4. **`test_expired_entry_returns_false`** — `register(path, ttl=0.01)` then `time.sleep(0.05)` then `check_and_consume(path)` returns False. (Or mock `time.monotonic` to advance past TTL.)

5. **`test_nfc_normalisation_matches`** — register with an NFD-normalised path string (simulate a macOS filesystem path); `check_and_consume` with the NFC-normalised equivalent returns True. Confirms the guard handles macOS NFD/NFC path variants.

6. **`test_multiple_paths_independent`** — register `path_a` and `path_b`; `check_and_consume(path_a)` returns True; `check_and_consume(path_b)` also returns True; `check_and_consume(path_a)` again returns False.

7. **`test_cross_thread_register_check`** — register from a worker thread (via `threading.Thread`), check from the main thread; confirm True is returned. Confirms the lock holds under concurrent access. Optionally, run register and check concurrently (one thread each) 100 times and assert no `RuntimeError: dictionary changed size during iteration` is raised.

8. **`test_get_active_returns_none_by_default`** — immediately after module import, `get_active()` returns None (module-level variable starts as None).

9. **`test_set_and_get_active_roundtrip`** — `set_active(guard)` then `get_active()` returns the same guard object.

10. **`test_no_module_scope_config_import`** — a static assertion: confirm `move_guard.py` contains no `from core.config import CONFIG` (C-17 guard).

**Depends on.** Component 1 (`MoveGuard` must exist).

**Assumes.** A9.

**Done when.**
- All tests pass under `uv run pytest tests/test_vault/test_move_guard.py -m "not smoke"`.
- No `from core.config import CONFIG` at module scope in the new test file.
- `uv run pytest tests/ -m "not smoke"` still passes with no regressions (count ≥ 798, current baseline per STATE.md).

---

#### 7. Add integration tests for pipeline-side registration and watcher-side suppression

**Goal.** Confirm that a pipeline-initiated move results in a registered guard entry (producer side) and that the watcher's re-home branch consumes that entry and skips re-home (consumer side) — covering the full cross-thread interaction at the integration level.

**Build.**

In `tests/test_vault/` (or `tests/test_pipelines/`), create `tests/test_vault/test_move_guard_integration.py`. All tests construct `VaultConfig(root=tmp_path)` directly (C-17). Patch `vault.move_guard.get_active` to return a pre-seeded spy `MoveGuard` (TD-033: patch the importing module's name). Patch `vault.watcher.move_attachment`, `vault.watcher.write_note`, `vault.watcher.move_note`, `vault.watcher.get_by_path`, `vault.watcher.audit_write` as `vault.watcher.<name>` (TD-033).

The tests cover:

1. **`test_pipeline_registers_before_located_move`** — call `_store_nonmd` with a no-edit file (mocked `resolve_placement` returning `needs_move=True`) while a spy `MoveGuard` is active; assert `guard.register` was called with the `attachment_dst` path before `move_attachment` was called (ordering check: register precedes move).

2. **`test_pipeline_registers_before_inbox_move`** — same but for the CLUELESS/inbox branch (`target_type=None`); assert `guard.register(inbox_dst)` was called.

3. **`test_pipeline_skips_registration_when_no_active_guard`** — `get_active()` returns None; call `_store_nonmd`; assert no AttributeError and `move_attachment` still fires normally.

4. **`test_watcher_rehome_skipped_when_guard_fires`** — pre-seed `MoveGuard` with a registered path `dst`; call `_handle_binary_move(src, dst)` (the else-branch / cross-folder case); assert `audit_write` was NOT called (no `REHOMED` row), `write_note` was NOT called, and a log record containing `watcher.rehome_skip` exists.

5. **`test_watcher_rehome_fires_when_guard_not_registered`** — `MoveGuard` is empty (nothing registered); call `_handle_binary_move(src, dst)` cross-folder; assert `audit_write` WAS called with `outcome="REHOMED"` (T6's re-home ran). This is the regression guard confirming genuine user moves are not suppressed.

6. **`test_guard_check_uses_dst_not_final_binary`** — register `dst`; configure `resolve_placement` to return `needs_move=True` (editable file, `final_binary != dst`); call `_handle_binary_move`; assert guard suppression fires (True returned) even though `final_binary` differs from `dst`. This confirms the guard is checked against `dst` (the watchdog event path = what the pipeline registered), not against `final_binary` (the post-placement path).

   (If research discovers that the pipeline registers `final_binary` rather than `dst`, this test must be updated accordingly — see Handoff notes.)

7. **`test_no_module_scope_config_import`** — confirm no `from core.config import CONFIG` at module scope in the test file (C-17 guard).

**Depends on.** Components 1, 4, and 5 (guard, pipeline registration, and watcher check must all exist).

**Assumes.** A3, A6, A7.

**Done when.**
- All tests pass under `uv run pytest tests/test_vault/test_move_guard_integration.py -m "not smoke"`.
- Patch targets for `get_active` are `pipelines.capture.get_active` or `vault.move_guard.get_active` (never `vault.writer.*`).
- Patch targets for watcher collaborators are `vault.watcher.<name>` (never the source module).
- No `from core.config import CONFIG` at module scope.
- `uv run pytest tests/ -m "not smoke"` still passes with no regressions (count ≥ 798, current baseline).

---

### Handoff notes

- **[From T3 spec] Contract honored:** T3's LOCATED move site (`capture.py:658`, `move_attachment(src, attachment_dst)`) is identified as one of the three T8 registration sites. T3 rewires the destination variable from `att_dir`-derived to `placement.final_dir`-derived — T8 wraps the resulting `attachment_dst` (which is now `placement.final_dir / final_name`) with a `guard.register` call. T3 must land before T8 wraps that site; they ship in the same increment.

- **Contract with T6 (sole consumer):** T6's rewritten else branch calls `self._move_guard.check_and_consume(final_binary_or_dst)`. T8 delivers: (1) the `MoveGuard` class with `check_and_consume`, (2) the `self._move_guard` attribute on `_VaultEventHandler`, and (3) Component 5's guard check stub at the top of the else branch. T6 builds the full re-home logic around that stub. The two must ship in the same increment — the spec note `[REQUIRES: T6]` confirms this.

- **Contract with T7:** T7 adds a settle window that sits in FRONT of the T6 re-home call. The T8 guard check is INSIDE the T6 re-home call. So the ordering is: per-hop binary-sync dispatch (TD-030) → per-hop settle-window registration (T7) → on-settle-fire: T6 re-home entry → T8 guard check → re-home logic. T7 must never consume a guard token on an intermediate hop (because intermediate hops do not enter the T6 re-home branch — they are caught by the settle-window coalescing). T8 has no obligation to T7 beyond shipping before T7.

- **Contract with T10 (reconcile migration, SOFT):** T10's `reconcile_editable_migration` stage calls `move_attachment` to relocate editable files from `attachment/` to the project root. If `kms watch` is running concurrently during `kms reconcile`, those moves would look like user moves to the watcher and trigger re-home. T10 should register the destinations with `get_active()` before each `move_attachment` call (the same pattern as the three capture sites in Component 4). This is a SOFT dependency: T10's spec must decide whether to add the registration or document "run reconcile with the watcher stopped." T8 provides the mechanism; T10 decides whether to use it.

- **Open uncertainty — registered path vs final binary path:** Component 5's note flags a potential mismatch: the pipeline registers `attachment_dst` (the path passed to `move_attachment`), but the watcher's guard check fires after watchdog delivers the `on_moved` event whose `dst` equals that same `attachment_dst`. For no-edit files (pipeline moves binary to `attachment/`) this is a direct match. For editable files (T3 moves binary to the project root), `attachment_dst = placement.final_dir / final_name` = the project root path — so both the registered path and the watchdog `dst` are the project root path. This should be a clean match. Research must confirm: after T3 ships, does the pipeline ever call `move_attachment(src, path_X)` and then separately call `move_attachment(path_X, path_Y)` in the same capture (which would cause the watcher to fire on `path_Y`, which was never registered)? If yes, Site 1 must register both `path_X` and `path_Y`.

- **Open uncertainty — `check_and_consume` path argument:** Component 5 recommends the guard check use `dst` (the watchdog event `dst`). Research should confirm whether `_handle_binary_move`'s else branch in T6 computes `final_binary = placement.final_dir / dst.name` (which may differ from `dst` if `placement.needs_move=True` for a watcher-side secondary move). If `final_binary != dst` after a pipeline capture, the guard registered `dst` but the check would be against `final_binary` → mismatch. Planner must read T6's exact code and confirm the guard check argument.

- **Suggested research for /research:**
  1. Verify that `src/vault/move_guard.py` does not yet exist (`grep -r "move_guard" src/vault/`).
  2. Read `src/vault/watcher.py:89–122` verbatim to confirm `_VaultEventHandler.__init__` constructor parameter list and all `self.*` attribute assignments — determines the injection wiring in Component 2.
  3. Read `src/vault/watcher.py:465–503` verbatim to confirm `VaultWatcher.__init__` constructor parameter list and how it constructs `_VaultEventHandler` — determines where to add the `move_guard` pass-through.
  4. Read `src/cli/main.py:145–260` verbatim to confirm how `VaultWatcher` is constructed in `watch()` and identify the exact insertion point for `set_active_guard(watcher._move_guard)` (Component 3).
  5. Read `src/pipelines/capture.py` at the three move sites (~L658, ~L711, ~L1297) verbatim to confirm the exact variable names for the destination paths (`attachment_dst`, `inbox_dst`, `destination`) and confirm that these are the same path objects that watchdog will later report as `on_moved.dst` (A6 verification — addresses the open uncertainty about registered path vs final binary).
  6. Confirm that `unicodedata` is already imported in `src/vault/watcher.py` (grep for `import unicodedata`). If not, it must be added to the import block (A4).
  7. Confirm current test baseline count: `uv run pytest tests/ -m "not smoke" --co -q | tail -1` (must be ≥ 798 before any edits).

---

## T9 — Content-change detection on binary edit (atomic-save aware)

### Purpose

After T3 ships editable files to the visible project root, users will open and edit those files (Word, Excel, PowerPoint) directly in their vault. Without this task, saving a change in any Office app produces no update to the AI-written summary card — the card stays stale, describing the file as it was when first captured. This task makes the watcher react to binary saves: it recognizes that Office apps use "atomic save" sequences that fire a burst of MODIFY / DELETE / CREATE events on the real filename, determines whether the file content actually changed (by comparing a stored SHA-256 hash), and triggers a re-capture only when the bytes are new. It also patches two guard gaps that macOS probing revealed: Office lock files (`~$<name>.ext`) were not being skipped, and the `_handle_binary_delete` handler was missing a guard that would have caused it to orphan a sibling during the mid-burst DELETE that Excel fires on every save.

After this task: an editable binary edited and saved in `Projects/<A>/` causes its sibling summary card to refresh within one debounce window (~3 s). Unchanged saves (Cmd+S with no edit) produce no LLM call. Files inside `attachment/` (no-edit files) are unaffected.

---

### Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `_VaultEventHandler` | `src/vault/watcher.py:76` | Private class; filters and debounces watchdog filesystem events | All five edits live inside this class's methods | Deep |
| `_debounce(key, fn, args)` | `src/vault/watcher.py:151` | Cancels any running timer for `key`, starts a new one; reusable for any debounce key string | Used to schedule `_handle_binary_content_change` under the new `chg:{path}` key | Shallow |
| `_should_skip(path)` | `src/vault/watcher.py:124` | Returns True for managed-attachment non-md, dotfiles, sync-conflict, IGNORE_DIRS | Extended to also skip `~$` Office lock files by adding `"~$"` to the `startswith` check | Shallow |
| `on_modified` | `src/vault/watcher.py:251` | Handles watchdog MODIFY events; currently returns early for all non-`.md` files (TD-C6) | The early-return is replaced with a `chg:` debounce for internal binaries | Shallow |
| `on_deleted` | `src/vault/watcher.py:262` | Handles watchdog DELETE events; already fires `bin:{path}` debounce for internal binaries | Extended to also fire `chg:{path}` debounce for internal binaries, so the Excel last-event-DELETE case converges | Shallow |
| `_handle_binary_delete` | `src/vault/watcher.py:303` | Orphans sibling DB row + audit when a binary is deleted | A `path.exists()` guard is prepended so it exits early during atomic-save burst DELETEs | Shallow |
| `_sibling_for(binary, vault_config)` | `src/vault/watcher.py:54` | Returns `<binary.parent>/<summaries_subdir>/<binary.name>.md` — the expected sibling path | Used in `_handle_binary_content_change` to locate the indexed sibling before reading its `source_hash` | Shallow |
| `read_note(path)` | `src/vault/reader.py` (imported `watcher.py:40`) | Reads a vault `.md` file, returns `Success(Note)` or `Failure` | Used inside `_handle_binary_content_change` to read the sibling's `source_hash` frontmatter field | Shallow |
| `NoteMetadata.source_hash` | `src/vault/frontmatter.py` | `str \| None` field on the note metadata model; stores the SHA-256 of the binary at last capture | Compared against the current binary hash to determine whether content changed | Shallow |
| `capture_file(path, context)` | `src/pipelines/capture.py:793` | Full capture pipeline entry point; re-summarises and re-indexes a single file | Called via `self._on_create(path)` when the hash comparison confirms content has changed | Deep |
| `capture_file` idempotency guard | `src/pipelines/capture.py:868–908` | For binaries: reads sibling, reads binary bytes, compares `source_hash`; returns early if hashes match | Prevents a redundant LLM call if `_handle_binary_content_change` fires concurrently with the `str(path)` → `_on_create` path (Word/PPT double-dispatch) | Deep |
| `_is_in_managed_attachment(path, vault_cfg)` | `src/vault/paths.py:26` | Returns True iff path is inside a `Projects/<A>/attachment/` or `Domain/<D>/attachment/` subtree | Already called in `_should_skip`; ensures no-edit files in `attachment/` never reach the new `chg:` debounce | Shallow |
| `_is_binary(path)` | `src/vault/watcher.py:49` | Returns True iff the file's suffix is not `.md` | Used in `on_modified` and `on_deleted` to decide whether to schedule `chg:` | Shallow |
| `new_correlation_id()` | `src/core/logging_setup.py` (imported `watcher.py:35`) | Creates a UUID4 and binds it to structlog context vars | Called at the top of `_handle_binary_content_change` so all downstream log and audit entries share a single correlation ID | Shallow |
| `_is_internal(path)` | `src/vault/watcher.py:143` | Returns True iff `path` is inside the vault root | Gate used in `on_modified` and `on_deleted` before scheduling `chg:` — ensures external paths are never debounced | Shallow |

---

### Feature overview

The fix has five localized parts, all inside `_VaultEventHandler` in `vault/watcher.py`. Together they form a closed loop: events are filtered → burst is collapsed → when the burst settles, content is compared → re-capture fires only if bytes changed.

**Part 1 — Lock-file filter.** Office apps create a lock file named `~$<original name>.<ext>` in the same folder when the user opens a file. This lock file generates its own CREATE and MODIFY events, which today flow through to callbacks unfiltered. The fix is a one-character addition to the existing `_should_skip` dotfile guard: change `path.name.startswith(".")` to `path.name.startswith((".", "~$"))` so all `~$` files are silently discarded.

**Part 2 — `on_modified` now handles binaries.** Today, `on_modified` drops all non-`.md` events immediately after the `_should_skip` check (the `TD-C6` deferred comment). The fix replaces that early return with a conditional: if the path is a binary and is inside the vault, schedule a `chg:{path}` debounce pointing at the new `_handle_binary_content_change` callback, then return. The debounce uses the same `_debounce()` infrastructure as every other key — if another MODIFY fires within the window, the timer resets.

**Part 3 — `on_deleted` also resets `chg:`.** Excel's save sequence ends with a DELETE event on the real file path, even though the file is still alive after the burst. The existing `on_deleted` code already schedules a `bin:{path}` debounce for binary deletes. The fix adds a second `chg:{path}` debounce for the same path (same `_handle_binary_content_change` target). The two keys are independent — `chg:` and `bin:` never cancel each other. When the debounce fires, `_handle_binary_content_change`'s `path.exists()` guard is the first check: if the file is gone (genuine delete), the method returns immediately and the `bin:` handler takes over. If the file is still alive (atomic-save DELETE), the method proceeds to the hash comparison.

**Part 4 — `_handle_binary_delete` gets an existence guard.** During the burst, the `bin:{path}` debounce fires after the debounce window. Without a guard, `_handle_binary_delete` orphans the sibling DB row even though the file survived. The fix adds `if path.exists(): return` at the very top of the function body (before the correlation ID bind and all other logic). A single return exits cleanly; the `chg:` handler takes responsibility for triggering re-capture.

**Part 5 — `_handle_binary_content_change` (new method).** This is the core of T9. It runs after the debounce window. It performs, in order:
1. `if not path.exists(): return` — genuine delete; `bin:` handler covers sibling orphan.
2. Find the sibling using `_sibling_for(path, self._vault_config)`. `if not sibling.exists(): return` — the file was never captured; the normal `str(path)` → `_on_create` path handles first capture.
3. `read_note(sibling)` — on failure, return without re-capturing (the `on_create` path will re-try).
4. If `source_hash` is set in the sibling's metadata, compute `hashlib.sha256(path.read_bytes()).hexdigest()`. On `OSError` (file locked mid-read), return.
5. If hashes match: log `watcher.binary_content_unchanged path=<path>` at DEBUG and return — no re-capture.
6. If hashes differ (or no stored hash): call `self._on_create(path)`, which routes to `asyncio.run_coroutine_threadsafe(capture_file(...), loop)` — the full re-capture pipeline.

**Double-dispatch for Word and PowerPoint.** For these apps, the save burst's last event is CREATE. That means both `str(path)` → `_on_create` (from `on_created`) AND `chg:{path}` → `_handle_binary_content_change` → `_on_create` will fire after the debounce window. Two `capture_file` calls enter the asyncio loop. The `capture_file` idempotency guard (`capture.py:868–908`) exits the second call immediately (hash now matches the freshly-written sibling). Net effect: one LLM call, one no-op. This is acceptable — no correctness issue, just one redundant function call that is stopped before it reaches the LLM.

**Import addition.** `hashlib` is not currently imported at module scope in `watcher.py`. It must be added to the existing import block (lines 12–15 area). Do not import it inside the method — that would mask the missing module-level declaration.

---

### Out of scope

- **Windows atomic-save patterns (TD-039).** Windows Office apps use temp files named `~WRD*.tmp` (not `~$*.tmp`) and may hold an exclusive lock on the binary during write. The `~$` filter added in Part 1 covers macOS patterns only. Windows requires a separate probe and a broader filter. Explicitly deferred to TD-039.

- **No-edit files in `attachment/` (PDFs, images).** The existing `_should_skip` check already returns True for files inside `attachment/` — they never reach `on_modified` or the `chg:` debounce. No change needed. If a user somehow manually triggers a binary modify inside `attachment/`, it is silently ignored, which is the correct behavior.

- **First-capture of a brand-new binary.** When a binary drops into the vault for the first time and has no sibling yet, `_handle_binary_content_change`'s `if not sibling.exists(): return` guard stops it. The normal `str(path)` → `_on_create` path handles first capture. No change to `on_created` is needed.

- **Re-summarisation strategy / prompt tuning.** T9 routes changed binaries into the existing `capture_file` pipeline unchanged. Any improvements to how summaries are generated belong to Phase 2 Classify or a separate prompt-tuning task.

- **Audit log for the "hash unchanged → skip" no-op.** The design doc (C-13 row in the guardrail checklist) explicitly classifies this as "no AI decision — DEBUG log only, not audit_write." The `watcher.binary_content_unchanged` log line is the correct artifact.

- **`capture_file`'s sibling-lookup scan.** After T3 ships (editable files in project root, sibling in root `.summaries/`), `capture_file`'s existing sibling scan (`capture.py:877–884`, which walks `path.parents`) will find `Projects/<A>/.summaries/<name>.md` correctly with no code change. This is a [REQUIRES: T3] soft note, not a T9 code change.

---

### Constraints

- **C-01 · All vault writes via `vault/writer.py`** — source: `CONSTRAINTS.md`. `_handle_binary_content_change` performs no vault write. It calls `self._on_create(path)` which routes to `capture_file`, which calls `write_note` in `vault/writer.py`. No raw `write_text` anywhere in T9.
- **C-02 · `updated_by_human` gate** — source: `CONSTRAINTS.md`. Re-capture enters via `capture_file` → `write_note`; the gate is enforced there. `_handle_binary_content_change` does not call `write_note` itself and cannot bypass the gate.
- **C-03 · `write_note` is a pure writer; pipeline owns the merge** — source: `CONSTRAINTS.md`. `_handle_binary_content_change` calls `read_note` only to read `source_hash`. It does not call `write_note`. The re-capture pipeline (`capture_file`) owns the full read-then-write cycle for the sibling.
- **C-12 · Public `handlers/` and `pipelines/` functions return `Result`** — source: `CONSTRAINTS.md`. N/A: `_handle_binary_content_change` is a private method on a `vault/` class, not a public `handlers/` or `pipelines/` function. `capture_file`, which it dispatches to, already returns `Result`.
- **C-13 · Audit log non-negotiable** — source: `CONSTRAINTS.md`. The hash-match early exit is a watcher-internal optimization, not an AI decision — a DEBUG log line is the correct artifact. All AI decisions happen inside `capture_file`, which already writes `audit_log` rows.
- **C-17 · No module-scope CONFIG import in tests** — source: `CONSTRAINTS.md`. Tests construct `VaultConfig(root=tmp_path)` directly and inject a mock `_on_create` callable. No `from core.config import CONFIG` at module scope in any test file.
- **TD-033 · Monkeypatch the importing module, not the source** — source: `CLAUDE.md`. Tests that patch `read_note` must target `vault.watcher.read_note`, not `vault.reader.read_note`. Tests patching `_on_create` mock the callable injected at construction time, not the source.
- **`%s`-style stdlib logging** — source: `CLAUDE.md`. `_log` in `watcher.py` is `logging.getLogger(__name__)` (stdlib). All new `_log.*` lines use `%s` placeholders, never kwargs (e.g., `_log.debug("watcher.binary_content_unchanged path=%s", path)`).
- **Vault-relative paths via `self._root`** — source: `CLAUDE.md`. Any vault-path conversion inside `watcher.py` uses `relative_to(self._root)`, never the CONFIG singleton's `to_vault_path`.
- **`chg:` and `bin:` keys are independent** — source: design doc §6 Cross-check; `CLAUDE.md` "Two `_debounce` calls with same key cancel each other." `chg:{path}` and `bin:{path}` are different key strings; scheduling both for the same path does not cancel either.
- **`hashlib` module-level import** — source: design doc §6 Cross-check. Add `import hashlib` to the existing import block in `watcher.py` (lines 12–15). Do not import inside the method.
- **TD-037 retirement** — source: `CLAUDE.md` TECH_DEBT. T9 delivers the deferred binary-modify re-capture (the `# Binary modify deferred — TD-C6` comment at `watcher.py:258`). Remove that comment and the early-return in `on_modified` as part of this task.

---

### Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|---|---|---|---|
| A1 | `_should_skip` checks `path.name.startswith(".")` at `watcher.py:134` and this is the only dotfile guard in the method | Design doc T9 §1 "~$ lock file filter"; confirmed by reading `watcher.py:124–141` | If `_should_skip` already has a `~$` check, or if the check uses a different pattern such as `not path.name[0].isalnum()`, the patch in Component 1 would differ or be redundant |
| A2 | `on_modified` returns early for all non-`.md` files after the `_should_skip` check, with the comment `# Binary modify deferred — TD-C6` at approximately `watcher.py:257–259` | Design doc T9 §1 "on_modified returns early"; confirmed by reading `watcher.py:251–260` | If `on_modified` already has any handling for non-`.md` files (e.g., a partial TD-037 fix), Component 2 must merge rather than replace |
| A3 | `on_deleted` already schedules `_debounce(f"bin:{path}", self._handle_binary_delete, (path,))` for internal binaries, and does NOT yet schedule any `chg:` debounce for the same path | Design doc T9 §1 "on_deleted"; confirmed by reading `watcher.py:262–278` | If `on_deleted` already has a `chg:` line (e.g., from a prior partial implementation), Component 3 would be redundant |
| A4 | `_handle_binary_delete` does NOT have a `path.exists()` guard at its top; it unconditionally proceeds to orphan the sibling and write an audit row | Design doc T9 §1 "`_handle_binary_delete` missing guard"; confirmed by reading `watcher.py:303–342` | If the guard already exists, Component 4 is already done and can be skipped |
| A5 | `hashlib` is NOT currently imported at module scope in `watcher.py`; it is absent from the import block at lines 12–41 | Design doc T9 §1 "hashlib import"; confirmed by `grep "^import hashlib" src/vault/watcher.py` returning nothing | If `hashlib` is already imported at module scope, no import change is needed |
| A6 | `read_note` is imported at module scope in `watcher.py` (`from vault.reader import read_note` at approximately line 40) | Design doc T9 §1 "`read_note` already imported"; confirmed by reading `watcher.py:33–41` | If `read_note` is not imported at module scope, it must be added to the import block |
| A7 | `NoteMetadata.source_hash` is a `str \| None` field that `read_note` populates from the sibling's YAML frontmatter; `capture_file` writes this field at `capture.py:638–639` using `hashlib.sha256(path.read_bytes()).hexdigest()` | Design doc T9 §1 "existing `source_hash` infrastructure"; confirmed by reading `capture.py:635–648` | If `source_hash` is computed differently (e.g., partial bytes or a different algorithm), the hash comparison in `_handle_binary_content_change` would produce false mismatches |
| A8 | `self._on_create` is the `on_create` callable injected into `_VaultEventHandler.__init__` at construction time; calling `self._on_create(path)` from a `threading.Timer` thread is thread-safe because `on_create` in `cli/main.py` calls `asyncio.run_coroutine_threadsafe(capture_file(...), loop)` | Design doc T9 §1 "calling `_on_create` from timer thread"; confirmed by reading `watcher.py:89–104` and `cli/main.py:203–204` | If `_on_create` directly calls `asyncio.run()` (blocking) rather than `run_coroutine_threadsafe`, calling it from a timer thread would deadlock |
| A9 | The `capture_file` idempotency guard for binaries (`capture.py:868–908`) reads the sibling at the path returned by scanning `path.parents` for `<summaries_subdir>/<name>.md`; after T3 ships, this scan finds siblings in root `.summaries/` (`Projects/<A>/.summaries/<name>.md`) as well as in `attachment/.summaries/` — no changes to `capture_file` are needed for T9 | Design doc T9 §1 "sibling-lookup scan"; confirmed by reading `capture.py:870–884` | If the sibling scan hard-codes `attachment/` paths or only looks one directory deep, it would miss root-level siblings after T3 ships, causing double-dispatch to bypass idempotency |
| A10 | The test baseline is at least 798 tests passing (per `STATE.md` last update 2026-06-03) under `uv run pytest tests/ -m "not smoke"` | `STATE.md` "798 tests pass" | If the baseline is below 798, a pre-existing regression must be fixed before T9 tests are added |

---

### Component dependency order

#### 1. Add `~$` Office lock-file filter to `_should_skip`

**Goal.** Stop Office lock files (`~$<name>.docx`, `~$<name>.xlsx`, `~$<name>.pptx`) from reaching any watcher callback, so they are never captured, indexed, or confused with the real file.

**Build.**

In `src/vault/watcher.py`, in the `_should_skip` method (`watcher.py:134`), change the dotfile guard from:
```python
if path.name.startswith("."):
```
to:
```python
if path.name.startswith((".", "~$")):
```

No other change to `_should_skip`. The existing `_is_in_managed_attachment` check, sync-conflict check, and `IGNORE_DIRS` check are all untouched.

**Depends on.** None — this is a standalone one-line change.

**Assumes.** A1.

**Done when.**

- Given a file named `~$goal.docx` exists inside `Projects/A/`, when the watcher event handler processes a CREATE event for it, `_should_skip` returns True and no callback is invoked (observable by confirming no sibling `.md` is created for the lock file, and no `documents` row appears for it).
- Given a file named `.DS_Store` or `.hidden.md` exists, `_should_skip` still returns True (existing dotfile behaviour is not regressed).
- Given a file named `goal.docx` (no leading `~$`), `_should_skip` returns False and the normal create callback fires.

---

#### 2. Make `on_modified` schedule `chg:` debounce for internal binaries

**Goal.** Route MODIFY events on binary files to the content-change handler instead of silently discarding them, so that Word and PowerPoint saves (whose last event is CREATE but which also fire MODIFY) can converge on the `chg:` key.

**Build.**

In `src/vault/watcher.py`, in `on_modified` (`watcher.py:257–259`), replace the early-return block:
```python
if path.suffix.lower() != ".md":
    # Binary modify deferred — TD-C6 (requires reverse attachment lookup)
    return
```
with:
```python
if path.suffix.lower() != ".md":
    if self._is_internal(path):
        self._debounce(
            f"chg:{path}", self._handle_binary_content_change, (path,)
        )
    return
```

Remove the `# Binary modify deferred — TD-C6` comment entirely — TD-037 is delivered by this task.

The `.md` path below the replaced block is unchanged: `self._debounce(str(path), self._on_modify, (path,))` still fires for markdown files.

**Depends on.** Component 5 (`_handle_binary_content_change` must exist before this schedules it).

**Assumes.** A2.

**Done when.**

- Given `Projects/A/goal.docx` is indexed, when a MODIFY event fires for it, a `chg:goal.docx` (i.e., `chg:/absolute/path/to/goal.docx`) debounce timer is scheduled — observable by checking that `_handle_binary_content_change` is eventually called (verified in unit tests via mock).
- Given a MODIFY on a binary that is NOT internal to the vault (e.g., an event from a file outside the vault root), no debounce is scheduled (the `self._is_internal(path)` guard blocks it).
- Given a MODIFY on a `.md` file, the existing `_on_modify` callback path is unaffected.
- Given a MODIFY on a binary inside `attachment/` (e.g., `Projects/A/attachment/report.pdf`), `_should_skip` returns True before `on_modified`'s binary branch is reached, so no `chg:` debounce fires.

---

#### 3. Make `on_deleted` also reset the `chg:` timer for internal binaries

**Goal.** Ensure that Excel's save sequence — which ends with a DELETE event on the real file — converges on the `chg:` key just like MODIFY events do, so the content-change handler fires after the burst.

**Build.**

In `src/vault/watcher.py`, in `on_deleted` (`watcher.py:271–273`), extend the existing binary-sync block from:
```python
if _is_binary(path) and self._is_internal(path):
    self._debounce(
        f"bin:{path}", self._handle_binary_delete, (path,)
    )
```
to:
```python
if _is_binary(path) and self._is_internal(path):
    self._debounce(
        f"bin:{path}", self._handle_binary_delete, (path,)
    )
    self._debounce(
        f"chg:{path}", self._handle_binary_content_change, (path,)
    )
```

The `chg:` and `bin:` debounces are independent keys — scheduling both for the same path does not cancel either. The `_should_skip` check and `_on_delete` callback below are untouched.

**Depends on.** Component 5 (`_handle_binary_content_change` must exist).

**Assumes.** A3.

**Done when.**

- Given `Projects/A/Danh sách.xlsx` is indexed, when a DELETE event fires for it during a save burst and the file still exists after the debounce window, `_handle_binary_content_change` is called (observable: its DEBUG log line fires or a mock records the call).
- Given a DELETE on a binary that is a genuine deletion (file does not exist after the debounce window), `_handle_binary_content_change` runs, returns immediately at the `if not path.exists(): return` guard, and `_handle_binary_delete` proceeds to orphan the sibling (both fire independently).
- Given a DELETE on a `.md` file, only the existing `str(path)` → `_on_delete` path fires — the new block does not touch `.md` events.

---

#### 4. Add `path.exists()` guard to `_handle_binary_delete`

**Goal.** Prevent `_handle_binary_delete` from orphaning a live sibling when an atomic-save DELETE fires mid-burst and the binary survives.

**Build.**

In `src/vault/watcher.py`, at the very start of `_handle_binary_delete` (`watcher.py:303`), before the `import structlog` and `bind_contextvars` lines, add:
```python
if path.exists():
    return  # atomic-save mid-burst DELETE — file survived; chg: handler covers re-capture
```

All logic below the guard (the `import structlog`, `bind_contextvars`, sibling orphan, and audit write) is unchanged.

**Depends on.** None — this guard is independent of the new content-change machinery.

**Assumes.** A4.

**Done when.**

- Given `Projects/A/Danh sách.xlsx` is indexed and its sibling exists, when `_handle_binary_delete` fires (from the `bin:` debounce) and the file still exists on disk, the method returns immediately and no `watcher.binary_delete_sibling_removed` log line appears.
- Given the same file is genuinely deleted (file does not exist on disk), `_handle_binary_delete` proceeds and the sibling DB row is removed (`watcher.binary_delete_sibling_removed` is logged).
- No sibling orphan event fires during a Word, Excel, or PowerPoint Cmd+S save sequence (developer-verify criterion from the design doc).

---

#### 5. Add `import hashlib` to `watcher.py` module imports and implement `_handle_binary_content_change`

**Goal.** Provide the new private method that: confirms the binary survived the event burst, checks whether content actually changed by comparing the stored and current SHA-256 hashes, and triggers re-capture only on a real change.

**Build.**

**Import.** In `src/vault/watcher.py`, add `import hashlib` to the existing import block (after the current stdlib imports at lines 12–15: `asyncio`, `logging`, `threading`, `unicodedata`). This is a module-level addition — do not import inside the method.

**Method.** Add the following private method to `_VaultEventHandler`, placed after `_handle_binary_delete` and before `_handle_binary_move`:

```python
def _handle_binary_content_change(self, path: Path) -> None:
    """Re-capture a binary if its content changed since last indexing.

    Called after the chg:{path} debounce settles. Compares the binary's
    current SHA-256 against the source_hash stored in its sibling frontmatter.
    Calls _on_create only if hashes differ (or no stored hash).
    """
    import structlog
    structlog.contextvars.bind_contextvars(correlation_id=new_correlation_id())
    if not path.exists():
        return  # genuine delete — bin: handler covers sibling orphan
    sibling = _sibling_for(path, self._vault_config)
    if not sibling.exists():
        return  # not yet indexed; on_create handles first capture
    match read_note(sibling):
        case Failure():
            return  # unreadable sibling — let on_create path handle it
        case Success(value=note):
            if note.metadata.source_hash:
                try:
                    current = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    return  # file locked mid-read (rare on macOS); skip
                if current == note.metadata.source_hash:
                    _log.debug(
                        "watcher.binary_content_unchanged path=%s", path
                    )
                    return
    # Hash changed OR no stored hash — trigger re-capture via existing on_create path
    self._on_create(path)
```

The method body mirrors the pseudo-code in the design doc (§4 Option A, item 5). Note: the `structlog` import is inside the method for consistency with `_handle_binary_delete` and `_handle_binary_move`, which also import it inline — this is the established in-file convention, not a violation of the module-level import rule (the hashlib import, which is used only here, must be at module scope per A5 and the constraint in the design doc).

**Depends on.** Components 1, 2, 3, 4 (should be implemented together, but this method is the target of all three `chg:` debounce registrations and must exist before they fire).

**Assumes.** A5 (hashlib not yet imported), A6 (`read_note` already imported), A7 (`source_hash` field exists), A8 (`_on_create` is thread-safe), A9 (`capture_file` idempotency guard handles double-dispatch).

**Interface shape.**

Private method on `_VaultEventHandler` — no public interface. Callers are `_debounce`'s internal `threading.Timer`. Returns `None` in all branches. The only observable side effect is either a DEBUG log line (no-op) or a call to `self._on_create(path)` (re-capture trigger).

**Done when.**

- Given `Projects/A/goal.docx` with sibling `Projects/A/.summaries/goal.docx.md` containing `source_hash: <old_sha>`, when `_handle_binary_content_change` fires and the file's current SHA-256 differs from `<old_sha>`, `self._on_create` is called exactly once.
- Given the same setup with the current SHA-256 equalling `<old_sha>`, `self._on_create` is NOT called and a DEBUG log line `watcher.binary_content_unchanged path=<path>` is emitted.
- Given `Projects/A/goal.docx` has no sibling (never indexed), `_handle_binary_content_change` returns without calling `self._on_create` (the first-capture path uses `on_created` → `str(path)` → `_on_create`).
- Given a path that no longer exists when the method fires, it returns immediately without error.
- Given a `read_note` failure on the sibling, it returns without calling `self._on_create`.

---

#### 6. Add unit tests for `_handle_binary_content_change` and the patched methods

**Goal.** Confirm the six branch behaviors described in the design doc at the unit level, using `tmp_path` fixtures and mock callbacks — no real vault, no LLM, no CONFIG import.

**Build.**

Create `tests/test_vault/test_watcher_content_change.py`. All tests construct `_VaultEventHandler` directly with `VaultConfig(root=tmp_path)` and a mock `on_create` callable. Patch `vault.watcher.read_note` (not `vault.reader.read_note`) per TD-033.

The tests cover:

1. **`test_lock_file_skip`** — create a file named `~$goal.docx` inside `tmp_path`; assert `handler._should_skip(path)` returns True. Confirm a file named `goal.docx` in the same dir returns False from `_should_skip`.

2. **`test_on_modified_binary_schedules_chg_debounce`** — fire a synthetic `FileModifiedEvent` on a non-`.md` path inside `tmp_path`; assert that `_debounce` is called with a key matching `f"chg:{path}"` and the `_handle_binary_content_change` target. (Patch `_debounce` to record calls, or use a very short debounce_seconds and assert the callable fires.)

3. **`test_on_modified_md_unaffected`** — fire a MODIFY event on a `.md` path; assert the existing `_on_modify` callback is scheduled under `str(path)`, and NO `chg:` key is scheduled.

4. **`test_on_deleted_schedules_both_keys`** — fire a DELETE event on a binary path inside `tmp_path`; assert both `bin:{path}` (targeting `_handle_binary_delete`) and `chg:{path}` (targeting `_handle_binary_content_change`) debounces are scheduled, and that they are independent (scheduling one does not cancel the other).

5. **`test_handle_binary_delete_skips_when_file_exists`** — write a real file at `tmp_path/goal.docx`; call `_handle_binary_delete(path)` directly; assert the `delete_by_path` function (patched as `vault.watcher.delete_by_path`) is NOT called, and no `watcher.binary_delete_sibling_removed` log line is emitted.

6. **`test_handle_binary_delete_proceeds_when_file_gone`** — call `_handle_binary_delete(path)` where the file does NOT exist (never created in `tmp_path`); assert `delete_by_path` IS called (patch as `vault.watcher.delete_by_path`).

7. **`test_content_change_triggers_on_create_when_hash_differs`** — write a real binary file at `tmp_path/Projects/A/goal.docx`; create a sibling at `tmp_path/Projects/A/.summaries/goal.docx.md` with `source_hash: <old_sha>` in frontmatter (where `old_sha != sha256(actual file bytes)`); patch `vault.watcher.read_note` to return a `Success(Note)` with that `source_hash`; call `_handle_binary_content_change(path)`; assert the mock `on_create` was called once with `path`.

8. **`test_content_change_no_op_when_hash_matches`** — same setup but `source_hash` in frontmatter matches the actual file's SHA-256; assert `on_create` was NOT called and a DEBUG log record containing `watcher.binary_content_unchanged` was emitted.

9. **`test_content_change_skips_when_no_sibling`** — no sibling file exists; call `_handle_binary_content_change(path)`; assert `on_create` was NOT called (the `if not sibling.exists(): return` guard fires).

10. **`test_content_change_skips_when_file_gone`** — call `_handle_binary_content_change(path)` where the binary does not exist; assert `on_create` was NOT called.

11. **`test_content_change_skips_on_read_note_failure`** — patch `vault.watcher.read_note` to return `Failure(...)`; assert `on_create` was NOT called.

12. **`test_no_module_scope_config_import`** — static assertion: the test file contains no `from core.config import CONFIG` at module scope (C-17 guard).

**Depends on.** Component 5 (all methods under test must exist).

**Assumes.** A6 (TD-033: patch `vault.watcher.read_note`), A10 (baseline test count ≥ 798).

**Done when.**

- All 12 tests pass under `uv run pytest tests/test_vault/test_watcher_content_change.py -m "not smoke"`.
- No `from core.config import CONFIG` at module scope in the test file.
- `uv run pytest tests/ -m "not smoke"` still passes with no regressions (count ≥ 798 + 12 new tests = ≥ 810).

---

#### 7. Add an end-to-end integration smoke test for the content-change flow

**Goal.** Confirm the full event → debounce → hash-compare → re-capture chain works together, without requiring a real vault or real Office app — using a tmp_path fixture, a real debounce cycle (short `debounce_seconds`), and a patched `capture_file`.

**Build.**

In `tests/test_vault/test_watcher_content_change.py` (same file as Component 6), add an integration-level test class `TestContentChangeIntegration`. All tests use `VaultConfig(root=tmp_path)` (C-17). Patch `vault.watcher.read_note` and `pipelines.capture.capture_file` (or the `_on_create` mock). Use `debounce_seconds=0.05` (50 ms) so the timer fires quickly without adding meaningful latency to the test suite.

The tests cover:

1. **`test_excel_save_pattern_triggers_recapture`** — simulate Excel's save burst (DELETE then file still exists) on a binary with a known mismatched `source_hash`; fire synthetic `FileDeletedEvent`; wait for the `chg:` debounce to fire (`time.sleep(0.15)`); assert `on_create` was called once. Assert `delete_by_path` (patched as `vault.watcher.delete_by_path`) was NOT called (the `_handle_binary_delete` existence guard suppressed it).

2. **`test_word_save_pattern_no_double_llm`** — simulate Word's save burst (MODIFY, DELETE, MODIFY, CREATE, MODIFY, CREATE) by firing six synthetic events; wait for debounce; assert `on_create` is called at most twice (double-dispatch is acceptable — idempotency guard stops the second call internally in a real run); confirm the debounce collapsed all six events into a single timer.

3. **`test_unchanged_save_no_recapture`** — simulate a save burst on a binary whose `source_hash` matches the file bytes; wait for debounce; assert `on_create` was NOT called.

**Depends on.** Components 1–6 (all five watcher edits must be in place).

**Assumes.** A7, A8, A9, A10.

**Done when.**

- All integration tests pass under `uv run pytest tests/test_vault/test_watcher_content_change.py::TestContentChangeIntegration -m "not smoke"`.
- Full suite: `uv run pytest tests/ -m "not smoke"` passes with count ≥ 809 + integration tests.
- `watcher.binary_delete_sibling_removed` does NOT appear in logs during any of the three save-pattern tests (confirmed via `caplog` fixture or log capture).

---

### Handoff notes

- **[From T3 spec]: Sibling location after T3 ships.** After T3 delivers editable files to the project root, their siblings live at `Projects/<A>/.summaries/<name>.md` (root-level `.summaries/`), not `attachment/.summaries/`. `_handle_binary_content_change` calls `_sibling_for(path, self._vault_config)`, which returns `<binary.parent>/<summaries_subdir>/<binary.name>.md`. For a root-level editable file, `binary.parent = Projects/<A>/` — so the sibling is correctly found at `Projects/<A>/.summaries/<name>.md` with no changes needed to `_sibling_for`. T9 does NOT depend on T3 being shipped first, but in a combined run, T3 must be merged before T9 can produce correct sibling paths for editable files.

- **Contract with `capture_file` (idempotency guard).** T9 relies on `capture_file`'s binary idempotency guard (`capture.py:868–908`) to suppress the second of two concurrent `capture_file` calls for Word/PPT double-dispatch. The guard reads the sibling at the path found by scanning `path.parents` for `<summaries_subdir>/<name>.md`. After T3 ships, this scan must find root-level siblings — research should verify A9 before planning.

- **`chg:` key independence from other keys.** The design doc confirms `chg:{path}` and `bin:{path}` are different key strings and do not cancel each other. However, `chg:{path}` and `str(path)` (the user callback key in `on_created`) are also different strings — so a Word/PPT CREATE event schedules BOTH `str(path)` → `_on_create` AND, when MODIFY also fires, `chg:{path}` → `_handle_binary_content_change`. These fire independently after the debounce window. This is the known double-dispatch behaviour; the idempotency guard handles it. Research should confirm: does `on_created` for a binary ALSO call `_debounce(str(path), self._on_create, (path,))`? If yes, the double-dispatch is confirmed. Read `watcher.py:161–200` verbatim.

- **TD-037 retirement.** The `# Binary modify deferred — TD-C6 (requires reverse attachment lookup)` comment at `watcher.py:258` and the early-return it annotates are removed by Component 2. Research/plan should confirm the TD-037 entry in `TECH_DEBT.md` and either close it or update it to reference this task.

- **No config changes in this task.** T9 introduces no new config key, no new YAML file, no DB migration, no new CLI command, and no new MCP tool. All five edits are inside `vault/watcher.py`. This makes T9 maximally independent: it can be merged in any order relative to T1–T8 (no hard build-order dependency), though logically it slots after T3 (so editable root files actually exist to be edited).

- **Suggested research for /research:**
  1. Read `src/vault/watcher.py:124–141` verbatim to confirm the exact `_should_skip` body and that `startswith(".")` is the sole dotfile check (A1).
  2. Read `src/vault/watcher.py:251–260` verbatim to confirm the `on_modified` early-return structure matches the assumed form, and that the `TD-C6` comment is at line 258 (A2).
  3. Read `src/vault/watcher.py:262–278` verbatim to confirm `on_deleted` does not yet have a `chg:` line (A3).
  4. Read `src/vault/watcher.py:303–342` verbatim to confirm `_handle_binary_delete` has no `path.exists()` guard (A4).
  5. Run `grep "^import hashlib" src/vault/watcher.py` to confirm `hashlib` is absent from module-level imports (A5).
  6. Read `src/vault/watcher.py:161–200` to confirm how `on_created` handles binary files (verifies the double-dispatch path and whether `str(path)` → `_on_create` is scheduled for non-`.md` creates).
  7. Read `src/pipelines/capture.py:868–908` verbatim to confirm the sibling scan (`path.parents` loop at lines 877–884) will find root-level `.summaries/` siblings after T3 ships (A9).
  8. Confirm the `TECH_DEBT.md` entry for TD-037 and its current status so the plan can mark it retired.
  9. Confirm current test baseline count: `uv run pytest tests/ -m "not smoke" --co -q | tail -1` (must be ≥ 798 per A10).

---

## T10 — Reconcile migration stage (editable-in-attachment)

### Purpose

Tasks T2 and T3 fix the editable-vs-no-edit placement rule going forward — new captures route Word, Excel, and PowerPoint files to the visible project root instead of hiding them in `attachment/`. But files captured by the old pipeline are already sitting in `attachment/` and will never pass through capture again. This task adds a one-shot, on-demand migration sweep — `kms reconcile` — that finds editable files stranded in `attachment/`, pulls each one out to the visible project or domain root, moves the AI summary card alongside it, re-points the summary's `attachment_path` frontmatter to the new binary location, and fixes the database row — all without calling the AI.

After this task, a single `kms reconcile` run heals an existing vault: every `.docx`, `.xlsx`, and `.pptx` buried in `attachment/` becomes visible in Obsidian at its correct project or domain root, with its summary card in the root-level `.summaries/` directory and its database record pointing at the new paths. Future `kms reconcile` runs are idempotent — files already in the right place are skipped.

Additionally, this task extends two path predicates (`_is_in_managed_attachment` and `_is_managed_summaries_area`) to recognise root-level `.summaries/` directories (e.g. `Projects/<A>/.summaries/`). Without this extension, reconcile Stage 4's orphan-sibling guard would leave migrated siblings unprotected, and the near-twin predicates used by the watcher and reconcile would have a blind spot for the new sibling location created by T3.

---

### Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `reconcile()` | `src/pipelines/reconcile.py` | Async orchestrator that runs the existing 6-stage reconcile pipeline in sequence | T10 adds a new `reconcile_editable_migration` call between Stage 3 and Stage 4; no other change to the orchestrator | Deep |
| `ReconcileResult` | `src/pipelines/reconcile.py` | Dataclass accumulating per-stage counts and flags (e.g. `orphan_binaries_removed`, `stale_binaries_removed`) | T10 adds one new counter field `editables_migrated: int = 0` to this dataclass | Deep |
| `reconcile_orphan_binaries` (Stage 2) | `src/pipelines/reconcile.py` | Walks `attachment/` dirs and removes orphaned binary files; its walk shape (`root.rglob(attachment_dir)`) is the model T10 follows | T10's walk uses the same rglob + `_is_in_managed_attachment` filter pattern | Deep |
| `reconcile_stale_binaries` (Stage 3) | `src/pipelines/reconcile.py` | Walks `attachment/` dirs and removes binaries whose sibling DB row is stale; Stage 3 completes before T10 runs | T10 runs AFTER Stage 3 so it only encounters binaries that survived the stale/orphan purge | Deep |
| `reconcile_orphan_siblings` (Stage 4) | `src/pipelines/reconcile.py` | Removes sibling `.md` files whose `attachment_path` pointer has no corresponding binary; runs AFTER T10 | T10 must complete the full re-point (binary + sibling `attachment_path` + DB row) before Stage 4 runs — a correctly migrated sibling is protected from Stage 4 deletion only if its `attachment_path` points at the new root binary | Deep |
| `_is_in_managed_attachment` | `src/vault/paths.py:26` | Pure predicate: True if a path lives directly under `Projects/<A>/attachment/` or `Domain/<D>/attachment/` | T10 uses this to scope the walk to managed attachment dirs only, AND T10 extends it to cover root-level `.summaries/` as a managed area (see Component 1) | Deep |
| `_is_managed_summaries_area` | `src/vault/paths.py:56` | Pure predicate: True if a path is inside any `attachment/.summaries/` or `inbox/.summaries/` area — the scope guard used by Stage 4 | T10 extends this to also return True for `Projects/<A>/.summaries/` and `Domain/<D>/.summaries/` (root-level siblings), so Stage 4 correctly recognises and protects migrated sibling cards | Deep |
| `resolve_placement` | `src/vault/paths.py` (added by T2) | Pure function returning `Placement(final_dir, sibling_dir, needs_move)` for a captured binary given its resolved location and VaultConfig | T10 calls this to compute where an editable binary must move — the single source of placement truth, ensuring migration and capture agree on the same rule | Shallow |
| `Placement` | `src/vault/paths.py` (added by T2) | Frozen dataclass with `final_dir`, `sibling_dir`, `needs_move` | T10 unpacks the returned `Placement` to drive the migration move and sibling path computation | Shallow |
| `_location_context` | `src/vault/paths.py:87` | Returns `(type_str, name_str)` for a path — `("project", "Alpha")`, `("domain", "Finance")` etc. | T10 calls this to derive `target_type`/`target_name` before calling `resolve_placement`, exactly as T3 does for capture | Shallow |
| `move_attachment` | `src/vault/writer.py:241` | Moves a binary file through the vault writer; returns `Result` | T10 calls this to relocate the editable binary from `attachment/` to the project/domain root | Deep |
| `move_note` | `src/vault/writer.py:175` | Moves a `.md` file through the vault writer with `actor` parameter; returns `Result`; respects `updated_by_human` gate | T10 calls this to move the sibling `.md` from `attachment/.summaries/` to the root `.summaries/`; `actor="ai"` triggers the human-lock gate which returns `Failure(recoverable=False)` for human-edited siblings | Deep |
| `read_note` | `src/vault/reader.py` | Reads and parses a `.md` note from disk into a `Note` object with `metadata` | T10 calls this to read the existing sibling's `attachment_path` frontmatter before rewriting it | Shallow |
| `write_note` | `src/vault/writer.py` | Writes or overwrites a `.md` note with full `NoteMetadata`; respects `updated_by_human` gate | T10 calls this after `move_note` to rewrite the sibling's `attachment_path` frontmatter to the new binary path — a mandatory step; `rename` alone does not update frontmatter | Deep |
| `documents.rename` | `src/storage/documents.py:287` | Updates a `documents` row's `vault_path` in-place while preserving the row `id` and all FK links (DECISION-001) | T10 calls this after the sibling file is moved and its frontmatter is rewritten, to fix the DB row's `vault_path` from the old sibling path to the new sibling path | Deep |
| `documents.get_by_path` | `src/storage/documents.py:141` | Retrieves a `DocumentRow` by its `vault_path` string | T10 calls this to check whether the binary has an indexed sibling before attempting migration; also used in Developer-verify tests to confirm the row was renamed | Shallow |
| `audit_write` | `src/core/audit.py` | Writes one row to the `audit_log` table | T10 calls this once per migrated file with `action="reconcile:editable_migrated"`, `outcome="EDITABLE_MIGRATED"` | Shallow |
| `AIDecision` | `src/core/confidence.py` | Small dataclass wrapping `action`, `confidence`, `reasoning`, `source_ids` — required wrapper for every `audit_write` call | Passed to `audit_write` for each migrated file | Shallow |
| `to_vault_path` | `src/vault/paths.py:149` | Converts an absolute `Path` to an NFC-normalised vault-relative POSIX string | Used to build `vault_path` strings for `documents.rename`, `audit_write` `source_ids`, and sibling frontmatter `attachment_path` | Shallow |
| `PipelineContext` / `ctx` | `src/core/pipeline.py` | Carries config, db_path, and other runtime dependencies; passed to every pipeline stage | T10's new stage receives `ctx` as its second argument, consistent with all existing reconcile stages | Shallow |
| `_log` | `src/pipelines/reconcile.py` (module-level) | `logging.getLogger(__name__)` — stdlib logger for the reconcile pipeline | T10 uses `_log.warning(...)` / `_log.info(...)` with `%s` format strings for all log lines, consistent with the rest of the file | Shallow |
| `get_active` (MoveGuard) | `src/vault/move_guard.py` (added by T8) | Returns the active `MoveGuard` instance if `kms watch` is running, or `None` | T10 calls `g = get_active(); if g: g.register(root_dst)` before every `move_attachment` to avoid a concurrently-running watcher re-homing the migration's own move (soft dependency — see Constraints) | Shallow |
| Existing CLI echo in `kms reconcile` | `src/cli/main.py:129–136` | Prints a summary line per completed reconcile stage (e.g. "Removed N orphan binaries") | T10 extends this block with a new echo line for `editables_migrated` | Shallow |

---

### Feature overview

The migration sweep is a new Stage 3.5 — inserted between the existing Stage 3 (stale binary cleanup) and Stage 4 (orphan sibling cleanup) — in the `reconcile()` pipeline in `src/pipelines/reconcile.py`. It runs only when `kms reconcile` is called manually; there is no scheduler.

**Walk phase.** The stage walks every `attachment/` directory in the vault using the same `root.rglob(vault_cfg.attachment_dir)` pattern as Stages 2 and 3. For each file found inside a managed attachment area, it skips: dotfiles, `.md` files (siblings), symlinks, and no-edit files (those whose lowercased extension is in `vault_cfg.no_edit_extensions`). Only editable, non-`.md` binaries remain.

**Placement phase.** For each editable binary, the stage derives the project or domain name using `_location_context`, then calls `resolve_placement(entry, loc_type, loc_name, vault_cfg)` — the same function T3 uses for new captures. If `resolve_placement` returns `needs_move=False`, the binary is already at its correct home and the stage skips it. This makes the stage idempotent.

**Collision resolution.** If the destination filename already exists at `placement.final_dir`, the stage appends `-1`, `-2`, etc. up to `-100`, following the same policy as the capture pipeline (`capture.py:600–611`). If all 100 slots are taken, the binary is skipped with a warning.

**Move sequence (sibling-first, DECISION-025).** For each editable binary that needs moving:
1. Locate the existing sibling at `attachment/.summaries/<entry.name>.md`.
2. Read the sibling via `read_note` to get its current content.
3. Rewrite the sibling's `attachment_path` frontmatter field to the new binary's vault-relative path (`to_vault_path(root_dst)`), then call `move_note` to move it from `attachment/.summaries/` to `placement.sibling_dir/<root_dst.name>.md` with `actor="ai"`. The sibling's body, `type=attachment-summary`, and all other frontmatter fields are preserved.
4. Move the binary via `move_attachment(entry, root_dst)`.
5. Fix the DB row via `documents.rename(old_sibling_vp, new_sibling_vp)`.
6. Write one audit row per migrated file.
7. Increment the `editables_migrated` counter on `ReconcileResult`.

**Human-locked sibling gate.** If the sibling has `updated_by_human: true` in its frontmatter, `move_note(actor="ai")` returns `Failure(recoverable=False)`. The stage matches this Failure, logs a warning, and skips the entire file — neither the binary nor the sibling is moved. The binary stays in `attachment/` until the human releases the lock. No audit row is written for skipped files.

**Missing sibling fallback.** If `read_note` or `move_note` returns a Failure for any other reason, or if the sibling does not exist on disk, the stage logs a warning and skips the file. The binary is left where it is.

**Ordering guarantee.** Stage 3 removes stale binaries (those with no valid sibling) before T10 runs, so T10 never encounters a stale binary. Stage 4 (orphan siblings) runs after T10, so it sees fully re-pointed siblings and does not delete them.

---

### Out of scope

- **Live-capture changes** — T3 owns the change to `_store_nonmd` that routes new captures correctly. T10 only heals previously-captured files; it does not change the capture pipeline.
- **LLM / summary regeneration** — T10 reuses the stored summary from the existing sibling. No AI call is made. No prompt is loaded.
- **Watcher re-home on user move** — T6 owns watcher-driven re-home. T10 is a batch sweep with no event-driven component.
- **Phase 2 Classify wiring** — Phase 2 will call `resolve_placement` from its own code path. T10 does not touch Phase 2's pending-routing logic.
- **`kms reconcile --dry-run` flag** — not added in this task. The stage reports its count in the summary; a dry-run mode is a future enhancement. Deferred — no phase assigned.
- **Auto-scheduling** — T10 runs on-demand via `kms reconcile`. No scheduler, no cron. Consistent with CLAUDE.md "Schedulers come last." Deferred to a later automation phase.
- **T6 shared `rehome_binary` helper** — T10 intentionally does NOT share a helper with T6. The design doc (Option C analysis) deferred that extraction because T6 carries watcher-specific concerns (MoveGuard gate, settle-window origin, `REHOMED` audit outcome) that do not apply to a batch sweep. If the shared helper is needed in the future, promote T10's per-file logic at that point.
- **Content-change re-capture** — T9. T10 reuses the stored summary; it does not re-summarise.
- **Domain archive helper changes** — domain vault layout is already handled by `_location_context` and `resolve_placement`; T10 makes no changes to `domain_archive` or the archive path logic.

---

### Constraints

- **C-01 · Vault-only writes** — every file relocation must go through `move_attachment` (binary) and `move_note` (sibling). No raw `.write_text()` or `open(..., 'w')`. The sibling frontmatter rewrite goes through `write_note` (after `move_note` places it). Source: `CONSTRAINTS.md` C-01; hook hard-block.
- **C-02 · `updated_by_human` gate** — `move_note(actor="ai")` already refuses to move a human-locked sibling; it returns `Failure(recoverable=False)`. The stage must match that Failure and SKIP the whole file — do not move the binary without its sibling. Source: `CONSTRAINTS.md` C-02; design doc T10 Guardrail Checklist.
- **C-13 · Audit every AI/sync decision** — one `audit_write(AIDecision(...))` per migrated file with `action="reconcile:editable_migrated"`, `outcome="EDITABLE_MIGRATED"`, `pipeline="reconcile"`, `stage="reconcile_editable_migration"`. Zero audit rows for skipped files (locked / no-edit / no-row / already-placed). Source: `CONSTRAINTS.md` C-13; design doc T10 Guardrail Checklist.
- **C-12 · Result return at pipeline boundaries** — the stage function returns `Result[ReconcileResult]`; every inner `move_attachment`/`move_note`/`read_note`/`documents.rename`/`get_by_path` call is matched (no unhandled Failure). A per-file Failure logs a `%s`-style warning and skips the file; the stage continues. Source: `CONSTRAINTS.md` C-12; design doc T10 Guardrail Checklist.
- **C-06 · No hardcoded thresholds in pipelines** — the editable/no-edit decision is delegated to `resolve_placement` (config-driven via `no_edit_extensions`), never a float literal. No confidence comparison in the stage. Source: `CONSTRAINTS.md` C-06.
- **C-07 · Prompts as YAML only** — no LLM call, no prompt, no f-string prompt. Source: `CONSTRAINTS.md` C-07 (vacuously satisfied — included for completeness).
- **C-17 · No module-scope CONFIG import in tests** — all tests construct `VaultConfig(root=tmp_path)` and `PipelineContext` explicitly. No `from core.config import CONFIG` at test module scope. Source: `CONSTRAINTS.md` C-17; design doc T10 Guardrail Checklist.
- **Single placement rule (T2 anti-goal)** — the stage MUST call `resolve_placement`; it must NOT inline an editable/no-edit check. A second copy of the placement rule is the explicit anti-goal of T2. Source: T2 spec §Constraints "Config-as-data"; design doc T2 §Recommendation.
- **DECISION-025 · Sibling-first ordering** — the sibling is moved and re-pointed before the binary is relocated and before the DB row is renamed. A crash between sibling-move and binary-move leaves the old DB row pointing at a still-readable file (the old sibling path is still valid). Source: DECISION-025; design doc T10 §Cross-check.
- **DECISION-029 · `type=attachment-summary` on every sibling** — the migrated sibling keeps `type=attachment-summary` in its frontmatter. `move_note` preserves existing metadata; `write_note` for the frontmatter rewrite must re-pass the full `NoteMetadata` with `type="attachment-summary"` intact. Source: DECISION-029; design doc T10 Guardrail Checklist.
- **DECISION-001 · `documents.rename` preserves row id** — fixing the DB row uses `documents.rename(old_vp, new_vp)`, never a delete + insert. This preserves the row's `id` and any FK links. Source: DECISION-001; design doc T10 §Cross-check.
- **TD-033 · Monkeypatch the importing module** — tests that patch `move_attachment`, `move_note`, `read_note`, `write_note`, `audit_write`, `resolve_placement`, `get_active` must patch them as `pipelines.reconcile.<name>` (the importing module), not the source modules. Source: CLAUDE.md TD-033.
- **`%s`-style stdlib logging** — `_log` in `reconcile.py` is `logging.getLogger(__name__)`; all new `_log.*` calls use `%s` placeholders, not keyword arguments. Source: CLAUDE.md "What Claude gets wrong."
- **SOFT · T8 MoveGuard registration** — if `get_active()` is non-None, call `g.register(root_dst)` immediately before every `move_attachment` to prevent a concurrently-running watcher from re-homing the migration's own move. If `get_active()` is None (watcher not running), skip registration silently. This is a soft dependency: if T8 has not yet landed, the stage works without it but documentation must note "run `kms reconcile` while the watcher is stopped, or ensure T8 is merged first." Source: design doc T10 §3 Developer-must-verify #5.
- **[REQUIRES: T1]** — `vault_cfg.no_edit_extensions` must exist before the stage can compute "is this an editable file?" T1 must land before T10. Source: design doc T10 Settled decisions.
- **[REQUIRES: T2]** — `resolve_placement` must exist in `vault/paths.py` before the stage compiles. T2 must land before T10. Source: design doc T10 Settled decisions.

---

### Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|---|---|---|
| A1 | `_is_in_managed_attachment(path, vault_cfg)` is defined at `src/vault/paths.py:26` and returns True only for paths whose grandparent resolves to `vault_cfg.projects_path` or `vault_cfg.domain_path` AND whose parent is named `vault_cfg.attachment_dir` (i.e. directly under `Projects/<A>/attachment/` or `Domain/<D>/attachment/`) | Design doc T10 §1 Implications §"Files touched (indirectly)"; T2 spec §"Already built" table; direct prior code read | If the predicate also returns True for deeper paths (e.g. `Projects/A/attachment/subfolder/file`), the walk-and-filter logic still works but the fix-the-predicates step in Component 1 needs a different depth check |
| A2 | `_is_managed_summaries_area(path, vault_cfg)` is defined at `src/vault/paths.py:56` and currently returns True only for paths under `attachment/.summaries/` subtrees or `inbox/.summaries/` — it does NOT yet recognize `Projects/<A>/.summaries/` or `Domain/<D>/.summaries/` as managed | Design doc T2 §Cross-check "[REQUIRES: T10] root-`.summaries/` recognition"; T2 spec §Handoff notes "[REQUIRES: T10]"; T3 spec §Handoff notes "[REQUIRES: T10]" | If the predicate already recognises root-level `.summaries/` (e.g. from a prior branch), Component 1's predicate extension is a no-op and can be skipped |
| A3 | `reconcile()` in `src/pipelines/reconcile.py` calls exactly 6 stages in order; the stage after `reconcile_stale_binaries` (Stage 3) and before `reconcile_orphan_siblings` (Stage 4) is a direct function call with `await reconcile_stale_binaries(result, ctx)`; inserting a new `await reconcile_editable_migration(result, ctx)` between them requires adding exactly one line | Design doc T10 §1 Implications §"Files touched" — "add to `__all__`; call it from `reconcile()` between Stage 3 and Stage 4" | If the reconcile orchestrator uses a stage list (e.g. `STAGES = [fn1, fn2, ...]`) rather than sequential `await` calls, the insertion requires a different approach |
| A4 | `ReconcileResult` is a dataclass in `src/pipelines/reconcile.py` with integer counter fields (e.g. `orphan_binaries_removed: int = 0`); adding a new `editables_migrated: int = 0` field follows the same pattern | Direct prior code read of `reconcile.py` | If `ReconcileResult` is a Pydantic model or a named tuple rather than a `dataclasses.dataclass`, the field addition syntax differs |
| A5 | Each reconcile stage has the signature `async def stage_name(result: ReconcileResult, ctx: PipelineContext) -> Result[ReconcileResult]`, matches `result` in-place (mutates the counter on the passed-in object), and returns `Success(result)` on normal completion | Design doc T10 §1 Implications §"Module depth" — "new async stage function alongside six identical siblings"; direct prior code read | If stages return a new `ReconcileResult` instance rather than mutating the passed-in one, T10 must follow the same return contract to avoid losing prior-stage counts |
| A6 | The CLI echo block at `src/cli/main.py:129–136` iterates a list of `(label, count)` pairs derived from `ReconcileResult` fields, or contains one `print`/`click.echo` per field; adding a new echo for `editables_migrated` is a one- or two-line addition in the same block | Design doc T10 §1 Implications §"Files touched" — "extend the `kms reconcile` summary echo (main.py:129-136)"; direct prior code read | If the echo block uses a single f-string over the whole result object, the addition requires editing that f-string rather than adding a line |
| A7 | `resolve_placement(entry, loc_type, loc_name, vault_cfg)` is importable from `vault.paths` (T2), is a total function with no filesystem side effects, and returns `Placement(final_dir, sibling_dir, needs_move)` where `needs_move=False` means the binary is already at `final_dir` | T2 spec §Component 2 "Interface shape" and §"Done when"; T2 settled decisions | If `resolve_placement` raises an exception for a valid editable binary (rather than returning a Placement with `needs_move=False`), T10's skip logic must catch the exception rather than checking `needs_move` |
| A8 | The existing sibling for a binary in `attachment/` is located at `<binary.parent>/.summaries/<binary.name>.md` (e.g. for `Projects/A/attachment/plan.docx` the sibling is at `Projects/A/attachment/.summaries/plan.docx.md`) — i.e. the `_sibling_for` rule (`<binary.name>.md`, full filename including extension) applied to the binary's current parent | CLAUDE.md "Sibling marker filename = `<binary.name>.md`, NOT `<binary.stem>.md`"; DECISION-028; T2 spec §"What the key terms mean" | If the old pipeline wrote siblings using `<stem>.md` (without extension), the sibling lookup would fail and T10 would fall through to the missing-sibling fallback for every file |
| A9 | `documents.rename(old_vp: str, new_vp: str, db_path: Path) -> Result[int]` exists in `src/storage/documents.py:287`, updates the `vault_path` column of the matching row in-place, and returns `Success(1)` on success or `Success(0)` if no row matched | Design doc T10 §1 Implications — "documents.rename(old_vp, new_vp) preserves row id + FK links (DECISION-001)"; CLAUDE.md "documents.delete_by_path and documents.rename return `Result[int]`" | If `documents.rename` does not exist or has a different signature (e.g. takes a `DocumentRow` rather than path strings), the DB-fix step needs a different call pattern |
| A10 | `move_note(src: Path, dst: Path, actor: str, ...) -> Result[...]` in `src/vault/writer.py:175` checks the sibling's `updated_by_human` flag when `actor="ai"` and returns `Failure(recoverable=False)` if the flag is True — this is the human-lock gate relied on by T10 for its skip-the-whole-file behaviour | CLAUDE.md "write_note sets `updated_by_human` from `actor`"; design doc T10 §1 Guardrail Checklist — "C-02 · move_note(actor='ai') refuses locked siblings" | If `move_note` does not check `updated_by_human` (only `write_note` does), T10 would bypass the lock and must add its own read + check before calling `move_note` |
| A11 | `_location_context(path, vault_cfg)` returns `(type_str, name_str)` tuples like `("project", "Alpha")` for paths under `Projects/Alpha/attachment/` — the attachment subdirectory does NOT confuse it into returning `None`/`None` | T3 spec §Assumptions A7 — "`target_type`/`target_name` are available" from the derivation block that uses `_location_context`; design doc T10 §4 Option A approach — "loc via `_location_context(entry, vault_cfg)`" | If `_location_context` returns `(None, None)` for a path inside `Projects/<A>/attachment/` (because the path is not in the vault root's direct `projects_path`), T10's call to `resolve_placement` would have invalid inputs and need a fallback |
| A12 | The two predicates `_is_in_managed_attachment` and `_is_managed_summaries_area` are pure functions with no filesystem I/O; extending them to recognise root-level `.summaries/` requires only a new conditional branch using `vault_cfg.projects_path` / `vault_cfg.domain_path`, not any new module import or config key | Design doc T10 §6 Cross-check — "extend `_is_in_managed_attachment`/`_is_managed_summaries_area` (paths.py:26,56) to recognize root-level `Projects/<A>/.summaries/` and `Domain/<D>/.summaries/`"; T2 spec §Handoff notes "[REQUIRES: T10]" | If the predicates import or call CONFIG internally (rather than receiving `vault_cfg`), their extension would also require threading `vault_cfg` through calls that currently do not receive it |

---

### Component dependency order

#### 1. Extend `_is_in_managed_attachment` and `_is_managed_summaries_area` in `vault/paths.py`

**Goal.** Teach the two near-twin path predicates about root-level `.summaries/` directories — the new sibling location created by T3 for editable files — so that reconcile Stage 4, the watcher, and any other caller can correctly identify and protect migrated sibling cards. Without this extension, Stage 4 would leave migrated root siblings in a blind spot: it would not recognise them as "managed" and therefore not clean them up if their `attachment_path` pointer ever goes stale.

**Build.**

In `src/vault/paths.py`, modify the two existing predicates:

**`_is_in_managed_attachment(path, vault_cfg)`** — add a new True branch for root-level summaries areas. The predicate currently returns True only if the path's grandparent resolves to a known project/domain attachment dir. Extend it to also return True if the path lives inside `Projects/<A>/.summaries/` or `Domain/<D>/.summaries/` where `<A>` / `<D>` are direct subdirectories of `projects_path` / `domain_path`. The test is: `path.parent.name == vault_cfg.summaries_subdir` AND `path.parent.parent.parent in (vault_cfg.projects_path, vault_cfg.domain_path)` (grandgrandparent is `Projects/` or `Domain/`). This covers `Projects/<A>/.summaries/<file>`.

**`_is_managed_summaries_area(path, vault_cfg)`** — add a True branch for paths whose parent is a `.summaries/` directory that is a direct child of a project or domain root. The test: `path.parent.name == vault_cfg.summaries_subdir` AND `path.parent.parent.parent in (vault_cfg.projects_path, vault_cfg.domain_path)`. This is structurally identical to the addition above — both predicates gain the same new branch.

Both additions are pure path arithmetic: no filesystem I/O, no new imports, no CONFIG access (both predicates already receive `vault_cfg`).

**Depends on.** None — this component depends only on `vault_cfg.summaries_subdir`, `vault_cfg.projects_path`, and `vault_cfg.domain_path`, all of which exist on `VaultConfig` before T10 (confirmed by STATE.md Phase 1.5 and T2 spec A3/A4).

**Assumes.** A2 (the predicates do NOT yet recognise root-level `.summaries/`), A12 (the predicates are pure functions taking `vault_cfg`).

**Interface shape.**
- Both functions keep their existing signatures: `(path: Path, vault_cfg: VaultConfig) -> bool`. No signature change.
- Callers that already use `_is_in_managed_attachment` for the binary/sibling walk (watcher `_should_skip`, reconcile Stages 2/3) are not affected: the extension only adds True cases for root-level `.summaries/` paths, which were previously False and would not have been in the walk scope for those stages.
- The new True cases matter specifically to reconcile Stage 4 (`reconcile_orphan_siblings`), which uses `_is_managed_summaries_area` as its scope guard — migrated siblings are now in scope for orphan detection but are protected by their rewritten `attachment_path` pointer (if T10 sets it correctly).
- Dependency category: in-process (pure path arithmetic; test directly with `VaultConfig(root=tmp_path)`).

**Done when.**
- `_is_in_managed_attachment(tmp_path / "Projects" / "Alpha" / ".summaries" / "plan.docx.md", vault_cfg)` returns True.
- `_is_in_managed_attachment(tmp_path / "Domain" / "Finance" / ".summaries" / "report.pdf.md", vault_cfg)` returns True.
- `_is_in_managed_attachment(tmp_path / "Projects" / "Alpha" / "plan.docx", vault_cfg)` returns False (the binary at the project root is NOT "in managed attachment").
- `_is_managed_summaries_area(tmp_path / "Projects" / "Alpha" / ".summaries" / "plan.docx.md", vault_cfg)` returns True.
- `_is_managed_summaries_area(tmp_path / "Projects" / "Alpha" / "attachment" / ".summaries" / "report.pdf.md", vault_cfg)` still returns True (existing behaviour preserved).
- All existing tests for `_is_in_managed_attachment` and `_is_managed_summaries_area` continue to pass (no regressions).
- No filesystem I/O occurs during any call (pure path arithmetic).

---

#### 2. Add `editables_migrated` field to `ReconcileResult`

**Goal.** Give the reconcile result object a counter for migrated files so the stage and the CLI echo can both refer to it by name — consistent with every other per-stage counter already on this dataclass.

**Build.**

In `src/pipelines/reconcile.py`, in the `ReconcileResult` dataclass definition, add one new integer field:

```
editables_migrated: int = 0
```

Position it after the existing per-stage counter fields (e.g. after `stale_binaries_removed` and before any summary or flag fields), following the established field ordering convention in the file.

**Depends on.** None.

**Assumes.** A4 (`ReconcileResult` is a `dataclasses.dataclass` with integer counter fields).

**Done when.**
- `ReconcileResult()` constructs without error and `result.editables_migrated == 0` by default.
- `result.editables_migrated = 3` can be assigned on a `ReconcileResult` instance (the field is mutable, not frozen — consistent with how other counters are incremented in-place by each stage).

---

#### 3. Implement `reconcile_editable_migration` stage in `reconcile.py`

**Goal.** Provide the stage function that walks every `attachment/` directory, identifies editable files that need to move to the project/domain root, and performs the full migration (binary move, sibling move + frontmatter repoint, DB row rename, audit) for each one — reusing the single placement rule from T2 so capture and migration can never disagree.

**Build.**

In `src/pipelines/reconcile.py`, add a new async stage function:

```
async def reconcile_editable_migration(result: ReconcileResult, ctx: PipelineContext) -> Result[ReconcileResult]:
```

Add it to `__all__` alongside the other stage names.

The function body follows this logic (in plain English):

1. **Setup.** Retrieve `vault_cfg = ctx.config.vault`. Import `resolve_placement`, `_is_in_managed_attachment`, `_location_context` from `vault.paths`; import `move_attachment`, `move_note`, `write_note`, `read_note` from `vault.writer` / `vault.reader`; import `documents.rename`, `documents.get_by_path` from `storage.documents`; import `audit_write`, `AIDecision` from `core.audit` / `core.confidence`; import `to_vault_path` from `vault.paths`; import `get_active` from `vault.move_guard` (soft T8 dep — guard if import fails). All imports are at the top of the function as lazy imports (consistent with the existing reconcile stage pattern), so they are patchable as `pipelines.reconcile.<name>` in tests (TD-033).

2. **Walk.** Iterate `vault_cfg.root.rglob(vault_cfg.attachment_dir)` to find all `attachment/` directories. For each path `entry` found inside such a directory:
   - Skip if `entry.is_dir()`, `entry.is_symlink()`, or `entry.name.startswith(".")`.
   - Skip if `entry.suffix.lower() == ".md"` (siblings, not binaries).
   - Skip if not `_is_in_managed_attachment(entry, vault_cfg)` (extra guard — only operate on files in known managed attachment areas).
   - Skip if `entry.suffix.lower() in vault_cfg.no_edit_extensions` (no-edit file; stays in `attachment/`).
   - For the remaining files (editable binaries in managed attachment): proceed to the placement phase.

3. **Placement.** Derive `loc_type, loc_name = _location_context(entry, vault_cfg)`. If `loc_type is None`, log a warning and skip (should not happen for files in a managed attachment area, but guard against it). Call `placement = resolve_placement(entry, loc_type, loc_name, vault_cfg)`. If `not placement.needs_move`, skip (already at the correct location — idempotent).

4. **Collision resolution.** Compute `root_dst = placement.final_dir / entry.name`. If `root_dst.exists()`, try `placement.final_dir / f"{entry.stem}-1{entry.suffix}"`, then `-2`, etc. up to `-100`. If all 100 names are taken, log a warning and skip the file.

5. **Sibling location.** Compute `old_sibling = entry.parent / vault_cfg.summaries_subdir / f"{entry.name}.md"` (the sibling at the binary's current `attachment/.summaries/` location). Compute `new_sibling = placement.sibling_dir / f"{root_dst.name}.md"` (the sibling's destination, following the `_sibling_for` naming rule `<binary.name>.md`).

6. **Move sequence (DECISION-025 sibling-first):**

   a. **Read the old sibling.** Call `read_note(old_sibling)`. On Failure: log a warning and skip the whole file. On Success: proceed with the `Note` value.

   b. **Move and repoint the sibling.** Call `move_note(old_sibling, new_sibling, actor="ai")`.
      - On `Failure(recoverable=False)`: this means the sibling is human-locked (`updated_by_human=True`). Log a warning (`_log.warning("reconcile.editable_migration.skip_locked binary=%s", entry.name)`) and skip the file (do not move the binary). Continue to the next file.
      - On other Failure: log a warning and skip the file.
      - On Success: proceed.

   c. **Rewrite the `attachment_path` frontmatter.** After `move_note` places the sibling at `new_sibling`, the sibling's `attachment_path` field still points at the old binary path. Call `write_note(new_sibling, body=existing_note_body, meta=existing_metadata_with_attachment_path_updated, actor="ai")` where `attachment_path` is set to `to_vault_path(root_dst)`. This is the mandatory frontmatter repoint step (design doc §6 Cross-check ⚠️ Frontmatter re-point is mandatory). If `write_note` returns Failure: log a warning; the sibling is now at the new path but with a stale pointer — emit a `_log.error` noting the inconsistency so an operator can fix it manually.

   d. **Move the binary.** Register with MoveGuard if active: `g = get_active(); if g: g.register(root_dst)`. Call `move_attachment(entry, root_dst)`. On Failure: log a warning. Note: if the binary move fails after the sibling was already moved, the state is partially migrated — log both paths so an operator can reconcile manually. Do not attempt to undo the sibling move (reversal is not safe without a DB-consistent undo path).

   e. **Fix the DB row.** Compute `old_sibling_vp = to_vault_path(old_sibling)` and `new_sibling_vp = to_vault_path(new_sibling)`. Call `documents.rename(old_sibling_vp, new_sibling_vp, db_path=ctx.db_path)`. On `Success(0)` (no row matched): log a warning — the binary had no indexed sibling. On Failure: log a warning.

   f. **Audit.** Call `audit_write(AIDecision(action="reconcile:editable_migrated", confidence=1.0, reasoning=f"Migrated editable binary from {to_vault_path(entry)} to {to_vault_path(root_dst)}", source_ids=[to_vault_path(entry)]), pipeline="reconcile", stage="reconcile_editable_migration", outcome="EDITABLE_MIGRATED", db_path=ctx.db_path)`.

   g. **Counter.** `result.editables_migrated += 1`.

7. **Return.** `return Success(result)`.

**Depends on.** Component 1 (`_is_in_managed_attachment` / `_is_managed_summaries_area` extensions must exist before this stage runs, so Stage 4 later in the same reconcile run sees correctly scoped siblings). Component 2 (`editables_migrated` field must exist on `ReconcileResult`). T2's `resolve_placement` and T1's `no_edit_extensions` must be merged.

**Assumes.** A1, A3, A4, A5, A7, A8, A9, A10, A11, A12.

**Interface shape.**
- Signature: `async def reconcile_editable_migration(result: ReconcileResult, ctx: PipelineContext) -> Result[ReconcileResult]`
- Consistent with all six existing stages (same signature, same in-place mutation of `result`, same `Success(result)` return).
- All collaborators (writers, DB, audit) are lazy-imported at function top so tests can patch them as `pipelines.reconcile.<name>` per TD-033.
- Dependency category: in-process.

**Decisions.**
- Q: Should the stage also handle the case where `read_note` returns Success but the sibling's body is empty or the `source_hash` is missing? Options: yes (extra guard, emit a warning but proceed with the move) / no (move proceeds regardless of sibling body content — migration is purely path math). Leaning no: the stage only re-homes, not re-summarises; the sibling body and `source_hash` are irrelevant to the move decision. The planner decides.
- Q: If `write_note` fails (frontmatter repoint fails after sibling was moved), should the stage attempt to roll back the sibling move? Options: yes (safer but complex — requires a reverse `move_note` which may also fail) / no (log both paths, leave for operator — consistent with T6's accepted broken-pointer posture from DECISION-025). Leaning no — consistent with the established broken-pointer failure posture; the operator can re-run `kms reconcile` after fixing disk conditions.

**Done when.**
- `from pipelines.reconcile import reconcile_editable_migration` succeeds.
- Given `Projects/A/attachment/plan.docx` with sibling `Projects/A/attachment/.summaries/plan.docx.md` in a test vault, calling the stage directly produces `Projects/A/plan.docx` on disk with no file at `Projects/A/attachment/plan.docx`.
- The sibling is at `Projects/A/.summaries/plan.docx.md` and its `attachment_path` frontmatter equals the vault-relative path of `Projects/A/plan.docx`.
- `documents.get_by_path("Projects/A/.summaries/plan.docx.md")` returns a row; `documents.get_by_path("Projects/A/attachment/.summaries/plan.docx.md")` returns no row.
- Exactly one audit row with `outcome="EDITABLE_MIGRATED"` is written.
- A `.pdf` at `Projects/A/attachment/report.pdf` is NOT migrated (stays in place).
- A file whose sibling has `updated_by_human: true` is NOT migrated (logged, both files left in place).
- Calling the stage a second time on an already-migrated vault produces `result.editables_migrated == 0` (idempotent).

---

#### 4. Wire `reconcile_editable_migration` into the `reconcile()` orchestrator

**Goal.** Make `kms reconcile` actually run the new stage — inserted between Stage 3 (stale binaries) and Stage 4 (orphan siblings) — so migration happens before orphan detection sees the new root sibling locations.

**Build.**

In `src/pipelines/reconcile.py`, in the `reconcile()` async orchestrator, add one line immediately after the existing `await reconcile_stale_binaries(result, ctx)` call and before the `await reconcile_orphan_siblings(result, ctx)` call:

```
await reconcile_editable_migration(result, ctx)
```

No other changes to `reconcile()`.

**Stage ordering rationale (important):** Stages 2 and 3 remove orphan and stale binaries from `attachment/` first. T10 therefore only encounters binaries that are valid (have indexed siblings). Stage 4 then runs after T10 — it sees the migrated, re-pointed siblings at root `.summaries/` and correctly identifies them as managed (thanks to Component 1's predicate extension). If T10 were placed after Stage 4, migrated siblings might be treated as orphans mid-run. The design doc §6 Cross-check ⚠️ Stage ordering explicitly requires this position.

**Depends on.** Component 3 (`reconcile_editable_migration` must exist and be importable before this line compiles). Component 1 (predicates must recognise root `.summaries/` before Stage 4 runs in the same orchestrator call).

**Assumes.** A3 (orchestrator uses sequential `await` calls, not a stage list).

**Done when.**
- `kms reconcile` runs to completion without error on a vault with editable files in `attachment/`.
- The CLI output includes the new `editables_migrated` echo line (after Component 5 below).
- Stage order in the orchestrator is: Stage 1 (paths) → Stage 2 (orphan binaries) → Stage 3 (stale binaries) → Stage 3.5 T10 (editable migration) → Stage 4 (orphan siblings) → Stage 5 (stale batch refs) → Stage 6 (stale tags).

---

#### 5. Extend the `kms reconcile` CLI echo with the new counter

**Goal.** Make the human-visible reconcile summary line reflect the number of files migrated, so the user knows the command did something useful.

**Build.**

In `src/cli/main.py`, in the `reconcile` command's post-run echo block (lines 129–136), add a new echo line after the existing stale-binaries line and before the orphan-siblings line:

```
click.echo(f"  Migrated {result.editables_migrated} editable files from attachment/ to project root")
```

The label wording can be adjusted for clarity; the key requirement is that `result.editables_migrated` appears in the output. Use the same formatting convention (indented, plain English) as the existing lines.

**Depends on.** Component 2 (`editables_migrated` must exist on `ReconcileResult`). Component 4 (the stage must be wired into the orchestrator so the count is populated).

**Assumes.** A6 (the echo block at lines 129–136 uses one echo call per counter field or an equivalent iterable).

**Done when.**
- Running `kms reconcile` on a vault with 2 migrated files prints a line containing `2` and `editable` (or equivalent wording) in the output.
- Running on a vault with no editable files in attachment prints `0` (not absent — the line always appears).

---

#### 6. Add unit tests for the predicate extensions in `vault/paths.py`

**Goal.** Confirm that the two extended predicates correctly recognise root-level `.summaries/` paths, do not regress on the existing attachment-subtree cases, and perform no filesystem I/O — all without importing the CONFIG singleton.

**Build.**

In `tests/test_vault/test_paths.py` (or `test_paths_placement.py` if a separate file was created by T2), add a new test class `TestPredicateRootSummaries`. All tests construct `VaultConfig(root=tmp_path)` directly — no module-scope `CONFIG` import.

Tests for `_is_in_managed_attachment`:

1. `test_root_summaries_project_returns_true` — `Projects/Alpha/.summaries/plan.docx.md`: returns True (new T10 case).
2. `test_root_summaries_domain_returns_true` — `Domain/Finance/.summaries/report.xlsx.md`: returns True.
3. `test_attachment_summaries_still_returns_true` — `Projects/Alpha/attachment/.summaries/report.pdf.md`: still returns True (existing case, no regression).
4. `test_root_binary_returns_false` — `Projects/Alpha/plan.docx`: returns False (the binary at the root is NOT "in managed attachment").
5. `test_inbox_summaries_returns_false` — `inbox/.summaries/doc.pdf.md`: returns False (inbox area is not a managed attachment area).

Tests for `_is_managed_summaries_area`:

6. `test_root_summaries_project_is_managed` — `Projects/Alpha/.summaries/plan.docx.md`: returns True (new T10 case).
7. `test_root_summaries_domain_is_managed` — `Domain/Finance/.summaries/report.xlsx.md`: returns True.
8. `test_attachment_summaries_still_managed` — `Projects/Alpha/attachment/.summaries/report.pdf.md`: still True (no regression).
9. `test_inbox_summaries_still_managed` — `inbox/.summaries/doc.pdf.md`: still True (no regression).
10. `test_root_binary_not_managed_summaries` — `Projects/Alpha/plan.docx`: False.

**Depends on.** Component 1.

**Assumes.** A2, A12.

**Done when.**
- All ten tests pass under `uv run pytest tests/test_vault/ -m "not smoke"`.
- No `from core.config import CONFIG` at module scope in any new test.
- `uv run pytest tests/ -m "not smoke"` passes with no regressions (count ≥ 798, current baseline).

---

#### 7. Add unit tests for `reconcile_editable_migration`

**Goal.** Confirm the stage function's per-file behaviour: correct cases are migrated, no-edit files are skipped, human-locked siblings are skipped without moving the binary, the sibling frontmatter is rewritten, the DB row is renamed, and the audit row is written — across both project and domain paths, and under the idempotency guarantee.

**Build.**

In `tests/test_pipelines/test_reconcile.py` (or a new `test_reconcile_migration.py`), add a new test class `TestReconcileEditableMigration`. All tests construct `VaultConfig(root=tmp_path)` and a `PipelineContext` explicitly — no module-scope `CONFIG` import. All collaborators are patched as `pipelines.reconcile.<name>` (TD-033).

1. `test_editable_binary_moved_to_project_root` — editable `.docx` at `Projects/A/attachment/plan.docx`; assert after the stage: `Projects/A/plan.docx` exists, `Projects/A/attachment/plan.docx` does not, `move_attachment` called with the correct root_dst.

2. `test_sibling_moved_to_root_summaries` — same fixture; assert `Projects/A/.summaries/plan.docx.md` exists, `Projects/A/attachment/.summaries/plan.docx.md` does not, `move_note` called with `(old_sibling, new_sibling, actor="ai")`.

3. `test_attachment_path_frontmatter_rewritten` — assert that `write_note` was called with `NoteMetadata` whose `attachment_path` equals `to_vault_path(Projects/A/plan.docx)` (the new root binary path). This is the mandatory frontmatter repoint check.

4. `test_db_row_renamed` — assert `documents.rename` was called with `(old_sibling_vp, new_sibling_vp, db_path)`.

5. `test_audit_row_written` — assert `audit_write` was called with `outcome="EDITABLE_MIGRATED"` and `source_ids` containing the old binary's vault-relative path.

6. `test_counter_incremented` — assert `result.editables_migrated == 1` after the stage.

7. `test_no_edit_pdf_stays_in_attachment` — `.pdf` at `Projects/A/attachment/report.pdf`; assert `move_attachment` was NOT called and `result.editables_migrated == 0`.

8. `test_human_locked_sibling_skips_whole_file` — patch `move_note` to return `Failure(error="human locked", recoverable=False)`; assert `move_attachment` was NOT called and `result.editables_migrated == 0`.

9. `test_already_at_root_is_skipped` — patch `resolve_placement` to return `Placement(final_dir=entry.parent, sibling_dir=..., needs_move=False)`; assert `move_attachment` was NOT called.

10. `test_domain_symmetry` — editable `.xlsx` at `Domain/Finance/attachment/budget.xlsx`; assert it is moved to `Domain/Finance/budget.xlsx`.

11. `test_idempotent_second_run` — call the stage twice on the same vault; assert `result.editables_migrated == 0` on the second call (everything already at the correct location).

12. `test_move_guard_registers_dst` — patch `get_active()` to return a mock `MoveGuard`; assert `guard.register(root_dst)` was called before `move_attachment`.

13. `test_move_guard_absent_no_error` — patch `get_active()` to return None; assert the stage runs to `Success` without error (guard is optional).

**Depends on.** Components 1, 2, 3.

**Assumes.** A1, A4, A5, A7, A8, A9, A10.

**Done when.**
- All 13 tests pass under `uv run pytest tests/test_pipelines/ -m "not smoke"`.
- No patch target uses the source module form (e.g. no `vault.writer.move_note` — all use `pipelines.reconcile.move_note`).
- No `from core.config import CONFIG` at module scope.
- `uv run pytest tests/ -m "not smoke"` passes (count ≥ 798 + new tests).

---

#### 8. Add integration tests for stage ordering and CLI echo

**Goal.** Confirm that `reconcile_editable_migration` fires at the correct position in the `reconcile()` orchestrator (after Stage 3, before Stage 4), that Stage 4 does not delete migrated root siblings, and that the CLI echo includes the new counter line.

**Build.**

In `tests/test_pipelines/test_reconcile.py`, add a new test class `TestReconcileOrchestration` or extend the existing orchestration tests.

1. `test_stage_order_migration_before_orphan_siblings` — create a test vault with an editable `.docx` in `attachment/` and its sibling; spy on the call order of stage functions (patch each stage); run `reconcile(ctx)`; assert `reconcile_editable_migration` is called BEFORE `reconcile_orphan_siblings` in the call sequence.

2. `test_stage4_does_not_orphan_migrated_sibling` — a full end-to-end test: create an editable binary in `attachment/`; run `reconcile(ctx)` (with real stage functions and a real `tmp_path` vault); assert that after reconcile completes, the root sibling at `Projects/A/.summaries/plan.docx.md` still exists and was NOT deleted by Stage 4. This verifies the Component 1 predicate extension protects the migrated sibling.

3. `test_cli_echo_includes_editables_migrated` — using the `CliRunner`, call the `kms reconcile` command on a minimal vault; capture output; assert the string `"editable"` (or the exact echo label chosen in Component 5) appears in the output.

**Depends on.** Components 1–5 (all must exist for these tests to pass end-to-end).

**Assumes.** A3, A6.

**Done when.**
- All three tests pass under `uv run pytest tests/test_pipelines/ -m "not smoke"`.
- Test 2 passes without patching Stage 4 — it runs the real stage on a real `tmp_path` vault — confirming the predicate extension works end-to-end.
- Full suite passes (count ≥ 798 + all new tests).

---

### Handoff notes

- **[From T2 spec] Contract honored:** T10 calls `resolve_placement(entry, loc_type, loc_name, vault_cfg)` with the exact signature T2 defines. `loc_type` and `loc_name` are derived via `_location_context(entry, vault_cfg)` before the call. T10 does not inline an editable/no-edit rule — the single source of truth in T2 is respected.

- **[From T3 spec] Contract honored:** T3 ensures new captures write root siblings with `type=attachment-summary` (DECISION-029). T10 migrates old siblings that were written by the old pipeline (which also set this type, confirmed by the existing capture code). If any old sibling somehow lacks `type=attachment-summary`, Stage 4's type-guard in `reconcile_orphan_siblings` leaves it alone (intentional defense against user-placed `.md` files — DECISION-029). T10 makes no assumption about the sibling body type; it moves whatever is there.

- **[From T8 spec] Soft dependency:** T8 adds the `MoveGuard` registry and `get_active()`. T10's stage registers `root_dst` before `move_attachment` if `get_active()` is non-None. If T8 has not yet landed when T10 is built, T10 should guard the import with a try/except: `try: from vault.move_guard import get_active; except ImportError: get_active = lambda: None`. This allows T10 to ship independently of T8 without failing on import, and silently no-ops the guard registration. Once T8 lands, the try/except can be replaced with a direct import. The design doc marks this as "SOFT T8" — not blocking.

- **DECISION-025 broken-pointer failure posture:** If the binary move (`move_attachment`) fails after the sibling has already been moved and re-pointed, the state is: sibling at root `.summaries/` with the correct `attachment_path` pointer, but the binary still at the old `attachment/` path. This is an inconsistency that Stage 4 would detect on the NEXT reconcile run (the new sibling pointer points at a binary that is not yet at the root — Stage 4 would see the root pointer as stale and delete the sibling). To prevent this, T10 must write the sibling's `attachment_path` to point at the old binary path as a fallback if `move_attachment` fails — or accept the known DECISION-025 broken-pointer posture and document it. The design doc accepts the broken-pointer posture. The planner should confirm this with the user before coding.

- **Frontmatter repoint is the most likely bug:** The design doc §6 Cross-check ⚠️ calls out "Frontmatter re-point is mandatory" as the single most likely mistake. `documents.rename` fixes only the DB `vault_path`; it does NOT update the sibling's on-disk `attachment_path` field. A naive `move_note` + `rename` without the `write_note` repoint creates a sibling that passes the DB check but whose frontmatter still points at the old binary in `attachment/` — causing Stage 4 to delete it on the next run (binary "missing" at the stale pointer). Component 3's build instructions make this explicit; the planner must verify it is implemented.

- **Stage ordering cross-check:** The design doc §6 Cross-check ⚠️ Stage ordering note states: "place `reconcile_editable_migration` AFTER `reconcile_orphan_binaries`/`reconcile_stale_binaries` (so capture-of-missing/stale runs against files still in their old home) and BEFORE `reconcile_orphan_siblings` (so the orphan stage sees the migrated, re-pointed siblings)." This ordering is the exact rationale in Component 4.

- **Suggested research for /research:**
  1. Read `src/vault/paths.py:26–80` verbatim to confirm the exact body of `_is_in_managed_attachment` and `_is_managed_summaries_area` — specifically their current True/False conditions — so the extension in Component 1 adds the correct branch without breaking any existing True case.
  2. Read `src/pipelines/reconcile.py` (full file, or at least the `reconcile()` orchestrator and `ReconcileResult` definition) to confirm the stage call pattern (sequential `await` vs. stage list, A3) and the existing counter fields (A4) and the exact lines 129–136 of `cli/main.py` for the echo block (A6).
  3. Verify that `move_note` in `src/vault/writer.py:175` does check `updated_by_human` (via `actor`) and returns `Failure(recoverable=False)` for human-locked siblings — confirm A10 before coding Component 3.
  4. Verify that `_location_context(path, vault_cfg)` returns a non-None `type_str` for a path inside `Projects/<A>/attachment/` — confirm A11 to ensure T10's `loc_type is None` guard is sufficient.
  5. Confirm whether `documents.rename` signature is `(old_vp: str, new_vp: str, db_path: Path) -> Result[int]` — confirm A9 before coding the DB-fix step in Component 3.
  6. Confirm current test baseline: `uv run pytest tests/ -m "not smoke" --co -q | tail -1` (must be ≥ 798 before any edits).
  7. Grep for `reconcile_editable_migration` in `src/` to confirm it does not already exist from a prior branch.
  8. Grep for `editables_migrated` in `src/pipelines/reconcile.py` to confirm the field does not already exist on `ReconcileResult`.
