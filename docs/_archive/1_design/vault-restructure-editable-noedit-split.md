# Design — Vault Restructure: Editable vs No-Edit File Split

**Status:** Design (codebase-design-analysis output). Input for `/writing-detailed-specs`.
**Source draft:** `docs/draft/vault-restructure-editable-noedit-split.md`
**Generated:** 2026-06-03 via 9 sequential design agents (one per task). T9 added 2026-06-04 after macOS atomic-save probe (real-vault event capture, three Office apps).
**Method:** Each task designed in build-order; each agent read the real code, ran codebase-design-analysis (≥3 options, depth lens, guardrail checklist, success criteria), self-reported confidence, and passed its settled decisions to the next. **All 9 agents reported confident** — the recommended option for each was adopted as the chain's working decision. T9 designed inline (probe data available, single session).

---

## Build order (per draft L418–444)

Hard deps: `T1 → T2 → T3`; `T3 → T8 → T6 → T7`; `T2/T3 → T10`. T8-before-T6 mandatory (re-home must not fire on the pipeline's own moves). T11 folds into T6 (verify only). T9 independent of T3 (watcher-only changes) — slot after T3 when editable files are in the root and actually get edited.

1. **Foundation:** T5, T1, T4
2. **Routing (headline):** T2 → T3
3. **Move story:** T8 → T6 (T11 folds in) → T7
4. **Migration:** T10
5. **Edit refresh (probe complete):** T9 — independent, slots after T3

---

## Decision summary

| Task | Recommended option | Confident |
|---|---|---|
| **T5** — capture_folder: exclude attachment/ + .summaries/ by name | Option A — config-driven name exclusion in _collect_folder_files (add the two names to the existing rel_parts membership check, sourced from VaultConfig). One reason: it matches the codebase's established config-driven name-skip convention (scan_capture reads summaries_subdir the same way) at the lowest possible blast radius, trading a minor, accepted over-exclusion of any user folder literally named "attachment" for a one-line, deletion-testable fix. | ✅ |
| **T1** — Config: no_edit_extensions + capture-excluded folders | Option A — add `no_edit_extensions` as a field on `VaultConfig` and expose AI-output folders via a computed `@property` that reuses the three existing dir-name fields. Chosen because it puts both lists where every other consumer (watcher, capture, paths.py) already reads vault structure, with zero duplication of the folder names that already live on the model. | ✅ |
| **T4** — Misplaced→inbox (all types) + AI-output capture-exclusion | Option B — Two-predicate approach: a new `_is_ai_output(path, vault_cfg)` capture-exclusion predicate (wired into watcher `_should_skip`, `scan_non_md_drops`, and `scan_vault` pruning) plus a `_is_misplaced(path, vault_cfg)` predicate that routes any mis-dropped file (md and non-md) to inbox via the existing move-to-inbox machinery. Chosen because it puts the two new behaviors behind named, deletion-test-passing predicates in `vault/paths.py` (the existing home for these checks) and reuses the already-built CLUELESS inbox move — at the cost of one extra hop through the watcher for misplaced md, which today is a no-op. | ✅ |
| **T2** — Single shared placement helper | Option A — add a pure function `resolve_placement(...)` to `src/vault/paths.py` returning a small `Placement` dataclass `(final_binary_path, sibling_path, needs_move)`. Chosen because it puts the editable/no-edit rule in the one module that already owns parametrized path math, with zero LLM/IO and a clean deletion test, and gives the future Phase 2 Classify the exact same callable as capture. | ✅ |
| **T3** — _store_nonmd: symmetric, type-driven needs_move | Option A — Consume T2's `resolve_placement` and delete the inline destination logic; one collision loop generalised to the helper-returned `final_dir`. Reason: it eliminates the duplicate editable/no-edit rule (T2 anti-goal) while keeping the sibling-first ordering and audit/Result guards untouched, trading a small one-time refactor for a single source of truth. | ✅ |
| **T8** — Sticky-note: suppress pipeline-initiated moves | Option A — Thread-safe TTL suppression registry as a small standalone module (vault/move_guard.py), instantiated by VaultWatcher and injected into the handler; pipeline registers via a module-level singleton bound at watch wiring. Chosen because it gives one explicit, testable seam shared cleanly across the observer thread and the pipeline thread-pool, expires on its own (crash-safe), and adds no logic to the move-handler beyond a single membership check. | ✅ |
| **T6** — Watcher: re-home on user move (incl. T11 correlation_id verify) | Option B — DB-driven re-home built on T2's resolve_placement, reusing the cross-folder branch of _handle_binary_move. Chosen because it reuses the single placement source of truth (no second copy of the editable/no-edit rule) and survives a missing on-disk source sibling (the move-chain case T7 cares about), at the cost of one extra DB read versus the on-disk-copy approach. | ✅ |
| **T7** — Move-chain convergence (settle window) | Option B — Dedicated binary-settle cooldown registry mirroring the folder-cooldown token-guard pattern, keyed on the destination filename. Chosen because it reuses a battle-tested in-tree pattern (the C2 token guard) and converges A→B→C to a single re-home with no new identity infrastructure, at the cost of a known same-name-collision edge that is acceptable for a single sequential human user. | ✅ |
| **T10** — Reconcile migration stage (editable-in-attachment) | Option A — new Stage 7 `reconcile_editable_migration` appended to the existing 6-stage pipeline, reusing the T2 `resolve_placement` helper and the T6 re-home mechanics (move binary + move sibling + fix frontmatter pointer + fix DB path). Chosen because it heals existing vaults with one on-demand command, lives where every other drift fix lives, and shares the single placement rule so capture and migration can never disagree. | ✅ |
| **T9** — Content-change detection on binary edit (atomic-save aware) | Option A — `chg:` debounce key (third key alongside existing `str(path)` and `bin:{path}`) reset by MODIFY + DELETE events on binaries; fires `_handle_binary_content_change` which reads sibling `source_hash`, computes current hash, and calls `_on_create` only on mismatch. Plus `~$` lock-file filter in `_should_skip` and `path.exists()` guard in `_handle_binary_delete`. Probe-grounded: handles Word (last event = CREATE), Excel (last event = DELETE), and PowerPoint (last event = CREATE) save patterns. All changes in `vault/watcher.py` only. | ✅ |

---

# T5 — `capture_folder`: exclude `attachment/` + `.summaries/` by name

## Implications

**Plain English.** When the user drops a whole *folder* into the vault, the system walks it and captures every file inside. Today that walk picks up two kinds of files it should leave alone: (1) binaries the system itself filed away inside a hidden `attachment/` folder, and (2) the AI-written summary `.md` files that live in `.summaries/`. Re-capturing a `.summaries/` summary is actively harmful — it overwrites the summary's frontmatter and wipes the pointer back to the original binary (`attachment_path`). This is the exact bug class the system already fixed for single-file scans (TD-AS-1). T5 closes the same hole for folder drops, and as a side effect makes the "X of Y files captured" count honest.

**What the key terms mean in this codebase.**
- The walk happens in `_collect_folder_files` (`src/pipelines/capture.py:1087-1104`). It `rglob`s every file under the dropped folder and keeps a file unless it is a directory, a dotfile *by its own name*, or its relative path passes through a name in `IGNORE_DIRS`.
- `attachment/` and `.summaries/` are the two folder *names* defined in config as `VaultConfig.attachment_dir = "attachment"` and `VaultConfig.summaries_subdir = ".summaries"` (`src/core/config.py:83-84`).
- Crucially, neither name is in the indexer's `IGNORE_DIRS` set (`src/vault/indexer.py:42-52`), and `.summaries` is explicitly in `_DOT_ALLOWLIST` (`indexer.py:56`) — the indexer *deliberately* keeps `.summaries/` visible. So `_collect_folder_files` does not skip them today. The fix must add the two names to its own skip check; it must **not** add them to `IGNORE_DIRS` (that would change indexer and `scan_capture` behavior — out of scope).
- Why the existing dotfile guard (`p.name.startswith(".")`, `capture.py:1098`) does not already catch this: the offending file is `.summaries/report.pdf.md`. The *file* name (`report.pdf.md`) is not dotted; only its parent folder is. The guard checks the leaf name, not ancestors.

**Which guards/constraints apply.**
- **Prompts/thresholds-as-config:** This change reads two folder *names* from `VaultConfig` rather than hardcoding `"attachment"` / `".summaries"` string literals. This mirrors how `scan_capture` already does it — `_summaries_subdir = CONFIG.main.vault.summaries_subdir` then `if _summaries_subdir in Path(entry.vault_path).parts` (`capture.py:1009-1011`). Following that convention keeps the codebase consistent and avoids a magic-string lint smell.
- **Result type:** `_collect_folder_files` is a private helper returning `list[Path]`, not a public pipeline function — it does not need to return `Success`/`Failure`, and the surrounding orchestrator (`capture_folder`) already returns `Result`. No change to the contract.
- **Vault-only writes / idempotent writes / updated_by_human:** Not engaged — this task only changes which files are *read* for capture; it writes nothing. By *preventing* re-capture of `.summaries/` siblings it actually protects the idempotency guard and the `updated_by_human` / `attachment_path` frontmatter that re-capture would clobber.
- **Audit:** No new AI decision is made here, so no new `audit.write` call. (The downstream `_capture_folder_files` already audits per file.)

**What files get touched.**
- **Directly:** `src/pipelines/capture.py` — `_collect_folder_files` body + signature, and its two call sites inside `capture_folder` (`capture.py:1255` and `capture.py:1306`).
- **Indirectly (no edit, but behavior shifts):** the `file_count` written into the `batches` row at `capture.py:1267`, `:1309`, `:1333` is `len(files)` / `len(new_files)` straight from this collector. Correcting the collector automatically corrects the count and therefore the PARTIAL/COMPLETE status logic in `_capture_folder_files` (`capture.py:1164-1165`) — no separate edit needed.
- **Untouched:** `src/vault/indexer.py` (`IGNORE_DIRS`), `src/vault/watcher.py`, `src/vault/paths.py`. T5 is deliberately scoped away from the path-predicate hotspot.

**Runtime deps.** `_collect_folder_files` is pure filesystem + a config object. The two call sites both already have `vault_cfg` in scope (`vault_cfg = ctx.config.vault`, `capture.py:1261`), so threading config in is free — no new singleton import, no test-import-scope hazard.

**Module depth (deletion test).** Shallow and additive. The change adds one membership term to an existing `any(...)` check and one parameter to a private function with exactly two internal callers (confirmed by grep: `capture.py:1255`, `:1306`; no test references the function directly). No new module boundary, no new interface, no new abstraction. If you deleted the change, the only thing lost is the skip — nothing depends on a new seam. This is the right depth for a one-line bug fix.

## Guardrail Checklist

| Rule (source) | Applies? | How the recommended option satisfies it |
|---|---|---|
| Thresholds/names as config, not hardcoded (CLAUDE.md "Prompts as Config"; the project's own `scan_capture` precedent) | Yes | Skip names read from `vault_cfg.attachment_dir` / `vault_cfg.summaries_subdir`, not string literals. |
| Result type on public functions (CONSTRAINTS Architecture) | N/A | `_collect_folder_files` is a private helper returning `list[Path]`; orchestrator returns `Result` unchanged. |
| Vault-only writes outside `vault/writer.py` (hard block) | N/A (protective) | Task reads only; it prevents harmful re-writes of `.summaries/` siblings. |
| Idempotent writes / `updated_by_human` / `attachment_path` preservation | Yes (protective) | Excluding `.summaries/` stops the re-capture that wipes `attachment_path` (TD-AS-1 class). |
| Audit every AI decision (CONSTRAINTS Architecture) | N/A | No AI decision added; per-file audit downstream is unchanged. |
| CONFIG import-scope in tests (CONSTRAINTS Testing) | Yes | Config arrives via the already-resolved `ctx.config.vault` param — no module-scope `CONFIG` import introduced. |
| `type=attachment-summary` guard on `.summaries/` writes (DECISION-029) | N/A | We are excluding, not writing, `.summaries/` files. |

## Success criteria

**You can verify (Given/When/Then — vault-visible only):**
1. Given a folder containing `notes.docx` plus an `attachment/report.pdf` subfolder, When you drop the folder into the vault, Then `report.pdf` is not captured a second time (no new sibling churn; the original `attachment/.summaries/report.pdf.md` is unchanged).
2. Given a folder containing a `.summaries/report.pdf.md` summary file, When you drop the folder, Then that summary file is not re-captured and its frontmatter (including the link back to its PDF) is untouched.
3. Given a folder with 3 real documents plus a hidden `attachment/` of 2 binaries and their 2 summaries, When you drop it, Then the batch reports 3 files captured, not 7.
4. Given a normal folder of office documents with no `attachment/` or `.summaries/` inside, When you drop it, Then every document is captured exactly as before (no regression).

**Developer must verify:**
- The `batches` row written at `capture.py:1267/1309/1333` has `file_count` equal to the count of real capturables only (DB row check).
- `_collect_folder_files` returns a list excluding any path whose `relative_to(folder_path).parts` contains `vault_cfg.attachment_dir` or `vault_cfg.summaries_subdir` (unit test with a fixture folder containing both subtrees).
- Non-interference (capture-vs-watcher): a folder drop's collector and the watcher's `bin:`-keyed binary sync touch disjoint paths — the collector now skips exactly the `attachment/`/`.summaries/` region the watcher owns, so they cannot both act on the same sibling. (Confirm no shared-path test regressions in `tests/test_pipelines/test_capture_phase9.py`.)

## Options

### Option A — Config-driven name exclusion inside `_collect_folder_files` (Recommended)
- **What this means (plain English):** Tell the folder-walker to ignore anything sitting under a folder named `attachment` or `.summaries`, using the names the system already stores in its config.
- **Approach:** Change the signature to `_collect_folder_files(folder_path: Path, vault_cfg: VaultConfig)`. Build a small skip-name set `{vault_cfg.attachment_dir, vault_cfg.summaries_subdir}` and extend the existing check at `capture.py:1101` to `if any(part in IGNORE_DIRS or part in skip_names for part in rel_parts): continue`. Pass `vault_cfg` at both call sites (already in scope).
- **Files touched:** `src/pipelines/capture.py` only (`_collect_folder_files` + 2 call sites). New unit test in `tests/test_pipelines/`.
- **Cost:** Dev — minimal (≈3 lines + signature + 2 call-site args + 1 test). Runtime — negligible (set membership per path). Maintenance — low; one obvious place, config-sourced.
- **Risk:** Minor over-exclusion — a user folder literally named `attachment`/`.summaries` is skipped. Accepted in the draft Open-questions for the non-technical target user.
- **Module depth:** Adds no boundary or interface. Passes the deletion test (delete = lose only the skip). Not speculative.
- **Defers:** Nothing.
- **Constraints check:** Config-sourced names ✓; no vault write ✓; no CONFIG import-scope hazard (uses `ctx.config.vault`) ✓.

### Option B — Hardcode the two literal names in the existing check
- **What this means (plain English):** Same skip, but spell `"attachment"` and `".summaries"` directly in the code instead of reading them from config.
- **Approach:** `if any(part in IGNORE_DIRS or part in {"attachment", ".summaries"} for part in rel_parts)`. No signature change.
- **Files touched:** `src/pipelines/capture.py` (body only) + test.
- **Cost:** Dev — smallest (no signature change). Runtime — same. Maintenance — higher: two more magic strings to keep in sync with `VaultConfig`; diverges from the `scan_capture` precedent that reads `summaries_subdir` from config.
- **Risk:** Silent drift if anyone ever re-points `attachment_dir`/`summaries_subdir` in config; the collector would stop matching. Also a magic-string smell against the "names as config" convention.
- **Module depth:** Same shallow depth as A.
- **Defers:** Nothing.
- **Constraints check:** ⚠️ Weakly violates the config-over-literals convention the codebase already follows for this exact pair of names. Otherwise clean.

### Option C — Add the names to `IGNORE_DIRS` in the indexer
- **What this means (plain English):** Make the *global* ignore list (used by indexer and scan) also drop `attachment`/`.summaries`, so the folder-walker inherits it for free.
- **Approach:** Add `"attachment"` to `IGNORE_DIRS` and remove `.summaries` from `_DOT_ALLOWLIST` (`indexer.py:42,56`); no change to `_collect_folder_files`.
- **Files touched:** `src/vault/indexer.py` — a shared, central definition.
- **Cost:** Dev — small edit. Runtime — same. Maintenance — high blast radius.
- **Risk:** ⚠️ High and out of scope. `IGNORE_DIRS` and `_DOT_ALLOWLIST` are consumed by the indexer and `scan_capture`'s `.summaries/` handling; `.summaries` is in `_DOT_ALLOWLIST` *on purpose* so siblings stay indexed/visible. Removing it would hide siblings from search and break reconcile's orphan-sibling stage (DECISION-029). The draft's Anti-goal explicitly says to keep this exclusion local to the folder collector.
- **Module depth:** Touches a shared boundary used by ≥3 callers — would entangle T5 with indexer/scan/reconcile.
- **Defers:** Nothing, but creates rework risk elsewhere.
- **Constraints check:** ⚠️ Contradicts the task Anti-goal and risks the DECISION-029 reconcile guard. Reject.

## Recommendation

**Option A.** It is the only option that closes the bug at the correct, local altitude *and* honors the codebase's existing "folder names live in config" convention (the same convention `scan_capture` already uses for `summaries_subdir`). The tradeoff accepted is a minor, documented over-exclusion: a user folder literally named `attachment` or `.summaries` would be skipped — acceptable for a non-technical executive who never creates such folders by hand.

## Cross-check

- **Scope creep removed:** No edits to `indexer.py`, `watcher.py`, or `paths.py`; the cross-cutting "root-level `.summaries/`" predicate work (draft L391) belongs to T2/T3/reconcile, not here — left untouched.
- **Constraint violations flagged:** Option B carries a ⚠️ convention smell (magic strings); Option C carries ⚠️ scope/Anti-goal/DECISION-029 violations. Recommended Option A has none.
- **Tech-debt items touched:** Retires the folder-drop variant of the **TD-AS-1** re-capture hole (same bug class the draft Goals cite). No tech debt worsened; neutral on TD-037/TD-039 (those are T9).
- **DECISION conflicts:** None. Consistent with DECISION-029 (we never write into `.summaries/` here) and DECISION-028 (sibling filename scheme untouched).
- **[REQUIRES]:** None — T5 is dependency-free (draft build order L426 leads with it as the safe quick win). Note for downstream: once T2/T3 introduce a *root-level* `.summaries/` for editable files, the same name-based exclusion in Option A already covers it (it matches `summaries_subdir` anywhere in `rel_parts`), so no T5 rework is needed.

---

# T1 — Config: `no_edit_extensions` + capture-excluded folders

## Implications

**In plain English.** This task adds two pieces of tunable data to the config so the rest of the restructure can read them instead of hardcoding:

1. A list of "no-edit" file extensions (PDFs + images) — the small, stable set. Any non-`.md` file whose extension is *not* on this list is treated as editable. This is the single dial that decides whether a captured file is hidden in `attachment/` or left visible in the project root.
2. A list of "AI-output" folders the system writes to itself — `Briefings/`, `Synthesis/`, `Documentation/` — which later tasks (T4) will exclude from capture so the AI never re-ingests its own output.

This task is **schema + data only**. It does *not* contain the logic that uses the lists (that is T2/T4). The deletion test: if you delete this task's code, T2's placement helper and T4's exclusion check have nothing to read — so this is a genuine shallow-but-load-bearing foundation, not a speculative abstraction.

**What the key terms mean in THIS codebase.**
- "No-edit extension" must match the existing extension convention. Every extension comparison in the code uses `path.suffix.lower()` against a lowercased, dot-prefixed string — see the handlers (`pdf_handler.py:34`, `image_handler.py:19,32`, `docx_handler.py:37`) and the nearest existing config analog, `RenameGateConfig.office_extensions` (`core/config.py:224`), which ships as `[".md", ".docx", ".xlsx", ".pptx", ".txt"]` (config.yaml:5) and is consumed by `extension in config.office_extensions` (`core/rename_gate.py:124`). So `no_edit_extensions` must ship as lowercased dot-prefixed strings; the *consumer* (T2) lowercases the candidate suffix before membership-checking. T1 just stores the canonical strings.
- "AI-output folder" already exists as data: the three folder names live on `VaultConfig` as `documentation_dir`, `synthesis_dir`, `briefings_dir` (`core/config.py:79-81`), each with a matching `@property` path helper (`briefings_path` at L99, etc.; `documentation_path`/`synthesis_path` at L95-97). The draft's anti-goal "data, not code" is half-satisfied already — the names are configurable; what's missing is a single grouped accessor so T4 can iterate them without re-listing the three field names.

**Guards / constraints that apply.** This is config-schema work, so most runtime guards do not fire:
- No vault writes, no `write_note`, no `updated_by_human` gate, no audit, no LLM, no thresholds, no prompts — none are touched. C-01/C-02/C-03/C-06/C-07/C-08/C-13 are all N/A to *this* task.
- The one live constraint is **C-17 (no module-scope CONFIG import in tests)**: the "test reads both back from config" done-when criterion must construct a `VaultConfig`/`MainConfig` with an explicit `root=tmp_path` (and the new fields), never `from core.config import CONFIG` at module top. The existing config tests already follow this; mirror them.
- Project convention (CLAUDE.md): **`Field` = a human configures it; `@property` = the code computes it from other fields.** `no_edit_extensions` is human-tunable → `Field`. The AI-output folder *names* are already `Field`s; a grouped list of those names is *computed from existing fields* → `@property` (it adds no new tunable surface, it just bundles `briefings_dir`/`synthesis_dir`/`documentation_dir`).

**Files touched.**
- Directly: `src/core/config.py` (`VaultConfig` — one new `Field`, optionally one `@property`) and `src/config/config.yaml` (`vault:` block, L69-77 — one new key).
- Indirectly (consumers, NOT edited here): `src/vault/paths.py` (T2 placement helper reads `no_edit_extensions`), `src/pipelines/capture.py` (`_store_nonmd` via T2/T3), `src/vault/watcher.py` + `scan_capture` (T4 reads the AI-output list). `src/vault/indexer.py`'s `IGNORE_DIRS` (L42) is *related but untouched* — see Cross-check.

**Downstream effects / runtime deps.** Pydantic validates the new field at config load (`load_config()`); a malformed YAML value (e.g. extensions without a leading dot) would fail fast at startup — desirable. No new third-party dependency. No DB, no migration (C-05 N/A — this is config, not schema). Module depth: shallow and intentionally so.

## Guardrail Checklist (rules that apply to THIS task)

- **C-17 · No module-scope CONFIG import in tests** — APPLIES. The done-when test constructs `VaultConfig(root=tmp_path, no_edit_extensions=[...])` directly / lazy-imports inside the test body. Recommended option satisfies it: nothing forces a CONFIG import.
- **CLAUDE.md `Field` vs `@property` rule** — APPLIES. Recommended option: tunable extension list → `Field`; computed folder-name bundle → `@property` reusing existing `*_dir` fields. Satisfied.
- **Extension-Point Rule (CLAUDE.md) / "behavior is data, not logic"** — APPLIES (it is the goal). Recommended option makes the editable/no-edit boundary a single config list; adding a new no-edit type later = edit YAML, no code. Satisfied.
- **Anti-goal: one canonical list only** (draft L67) — APPLIES. Recommended option ships exactly `no_edit_extensions`; "editable" is defined by *absence*, never a second list. Satisfied.
- **C-05 / C-06 / C-07 / C-13 / C-01..03** — DO NOT APPLY (no schema, no thresholds, no prompts, no audit, no vault write in this task). Flagged here so downstream tasks know T1 did not — and must not — smuggle any of these in.

## Success criteria

**You can verify (vault-visible / config-visible only):**
- GIVEN `config.yaml` has `no_edit_extensions: [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]`, WHEN the app starts, THEN startup succeeds (no config-validation crash).
- GIVEN an extension value is missing its leading dot (e.g. `pdf`), WHEN the app starts, THEN startup fails fast with a clear config error rather than silently mis-routing files later. (Only if a validator is added — see Option A note; otherwise this becomes a Developer-verify item.)
- GIVEN the three AI-output folder names in `config.yaml` (`briefings_dir`, `synthesis_dir`, `documentation_dir`), WHEN you read the new grouped accessor, THEN it returns exactly those three names with no extras.

**Developer must verify:**
- A unit test constructs `VaultConfig` (or `MainConfig`) with an explicit `root` and reads back `no_edit_extensions` and the AI-output folder accessor — asserting types (`list[str]`) and the shipped defaults. Confirms C-17 (no CONFIG import) and the round-trip.
- A test asserts the default `no_edit_extensions` list when the YAML key is *absent*, proving the `Field` default is the canonical pdf+image set (so a stripped config never makes everything editable).
- Concurrent-actor non-interference: N/A for T1 — config is read-only at runtime, loaded once at startup (`load_config()` singleton). No two actors mutate it.

## Options

### Option A — `no_edit_extensions` Field on `VaultConfig`; AI-output folders as a computed `@property`
**What this means (plain English).** Put the no-edit list right next to the other vault-structure knobs, and add a tiny computed accessor that bundles the three folder names the model already stores — so later tasks ask the config "which folders are AI-output?" instead of re-typing the names.
**Approach.** In `VaultConfig` (`core/config.py:69-99`): add `no_edit_extensions: list[str] = Field(default_factory=lambda: [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"])`. Add `@property ai_output_dirs(self) -> tuple[str, ...]: return (self.briefings_dir, self.synthesis_dir, self.documentation_dir)` (and/or an `ai_output_paths` property mirroring `briefings_path`). In `config.yaml` `vault:` block add the `no_edit_extensions:` key (folder names already present, L70-77).
**Files touched.** `src/core/config.py`, `src/config/config.yaml`. Test in `tests/test_core/test_config.py`.
**Cost.** Dev: ~30 min incl. test. Runtime: zero (one extra list + a property). Maintenance: lowest — the folder list cannot drift from the dir-name fields because it *is* those fields.
**Risk.** Very low. Only risk is choosing the wrong image-extension set (see Recommendation); easily widened later by editing YAML.
**Module depth.** No new module, no new boundary. The `@property` is a thin computed view over existing fields — passes the deletion test (delete it and T4 has no clean accessor). Not a speculative seam.
**Defers.** All consuming logic (T2 helper, T4 exclusion). Correct — that is the scope line.
**Constraints check.** Satisfies C-17, the Field/property rule, the Extension-Point Rule, and the one-canonical-list anti-goal. Touches no other constraint.

### Option B — New top-level `capture:`-block fields (e.g. `CaptureConfig.no_edit_extensions` + `CaptureConfig.excluded_folders`)
**What this means.** Treat both lists as capture-pipeline tuning and hang them off the existing `CaptureConfig` (`core/config.py:231-238`) instead of `VaultConfig`.
**Approach.** Add two `Field`s to `CaptureConfig`; add a `capture:` sub-block in YAML. Excluded folders stored as plain strings (duplicating the names already on `VaultConfig`).
**Files touched.** `src/core/config.py`, `src/config/config.yaml`, test.
**Cost.** Dev: similar. Runtime: zero. Maintenance: *higher* — the excluded-folder names would now be duplicated (once as `VaultConfig.briefings_dir` etc., once as a `CaptureConfig` string list); a rename of `Briefings/` would require editing two places, and the consumer must reconcile two sources of truth.
**Risk.** Medium-low. The duplication is a latent drift bug (CLAUDE.md flags near-twin/duplicated structure as a silent-bug hotspot). Also conceptually muddier: `no_edit_extensions` is about *vault layout* (where files live), not capture *tuning* (cooldowns, workers) — it belongs with the folder names it routes against.
**Module depth.** No new boundary, but creates a second home for vault-structure data — a weak seam.
**Defers.** Same as A.
**Constraints check.** Same constraints satisfied, but violates the spirit of the one-canonical-source principle by duplicating folder names. Not recommended.

### Option C — Mirror AI-output folders into `indexer.IGNORE_DIRS`; keep only `no_edit_extensions` in config
**What this means.** Reuse the existing module-level `IGNORE_DIRS` frozenset (`vault/indexer.py:42`) as the home for AI-output folder names, and add only the extension list to config.
**Approach.** Add `"Briefings"`, `"Synthesis"`, `"Documentation"` to `IGNORE_DIRS`; add `no_edit_extensions` to `VaultConfig`.
**Files touched.** `src/vault/indexer.py`, `src/core/config.py`, `src/config/config.yaml`, tests for both.
**Cost.** Dev: similar. Runtime: zero. Maintenance: worse — folder names become hardcoded module data (not user-meaningful config, contradicting draft L82 "it's user-meaningful, so config is preferred"), AND `IGNORE_DIRS` has a different *semantic* (it prunes the indexer's `.md` scan; AI-output exclusion is about the *capture* path, T4). Conflating them changes indexer behavior — `Briefings/` `.md` files would vanish from the documents mirror/search, which is a scope-creep side effect the draft explicitly does not ask for.
**Risk.** Medium-high. Silent behavior change to the indexer / FTS5 search; out of T1 scope (the draft's T5 decisions already warn against changing `IGNORE_DIRS`). Hardcoded, not config-driven — directly against the goal.
**Module depth.** No new boundary; reuses an existing one but with the wrong semantic. Fails the intent.
**Defers.** Same.
**Constraints check.** Violates the draft's stated preference for config over module-frozenset; risks an unrequested indexer/search change. Not recommended.

## Recommendation

**Option A.** It is the only option that adds the no-edit list where every consumer already reads vault structure *and* expresses the AI-output folders as a computed view of the names the model already owns — so the tradeoff is: I accept one extra trivial `@property` (a few lines) in exchange for eliminating any chance the folder list drifts from the canonical `*_dir` fields. The sole judgment call is the image-extension set; I ship the draft's recommended candidates `[".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]` and leave `.heic .tiff .svg .bmp` out for now (widening later is a one-line YAML edit, narrowing risks surprising the user). Optional hardening: a Pydantic `field_validator` that lowercases each entry and asserts a leading dot — recommended (cheap, fails fast), but not strictly required for done-when.

## Cross-check

- **Scope creep removed.** No consumer logic added (stays in T2/T4). `IGNORE_DIRS` deliberately NOT modified (Option C rejected) — keeps indexer/scan_capture behavior unchanged, consistent with the T5 decision "Do not add either to IGNORE_DIRS (out of scope)".
- **Constraint violations:** none introduced. ⚠️ Note for downstream: the *consumer* of `no_edit_extensions` (T2) must lowercase the candidate suffix before the membership test — config stores lowercased dotted strings (matching `office_extensions`/handler convention). If T2 compares a raw `.suffix` (mixed-case) it will mis-route `.PDF`/`.PNG`. This is a T2 obligation, flagged here so T1's storage convention is honored.
- **Tech-debt items:** TD-039 (Windows content-change) is unrelated to T1 and untouched (neutral). No existing TD item is retired or worsened by T1.
- **DECISION-NNN:** Does not contradict any. DECISION-029 (type-guard on `.summaries/` writes) is downstream (T3) and unaffected by adding config keys.
- **[REQUIRES: T1]** labels for downstream: T2 placement helper and T4 capture-exclusion both consume the new config fields named below.

---

# T4 — Misplaced→inbox (all types) + AI-output capture-exclusion

## 1. Implications

**What this task does, in plain English.** Two new "the system should leave this alone / move this" rules:

1. **AI-output folders are off-limits to capture.** The folders the AI itself writes into — `Briefings/`, `Synthesis/`, `Documentation/` — must never be captured. If we capture a briefing, summarising it produces another note, which gets captured, and so on: a feedback loop. So both the live watcher and the batch scan must skip these three folders entirely.
2. **Mis-dropped files get swept into the inbox.** If the user drops a file somewhere that is *not* a real home — e.g. directly in the top-level `Projects/` folder with no project subfolder, or in some random folder — the system moves it to `inbox/` and treats it as a normal inbox drop. This must now work for **markdown notes too**, not just binaries (today only binaries get swept).

**What the key terms mean in THIS codebase.**

- An **AI-output folder** is one of the three names already on `VaultConfig` as Fields (`briefings_dir`, `synthesis_dir`, `documentation_dir`; `src/core/config.py:79-81`). T1 exposes them as a computed tuple `VaultConfig.ai_output_dirs`. This task is their first *consumer*.
- A **misplaced location** is, precisely: a path that is **not** inbox, **not** a real `Projects/<A>/…` (a project subfolder must exist — the name after `Projects/` is a directory, not the dropped file), **not** a real `Domain/<D>/…`, and **not** an AI-output folder. The draft (L179) flags the trap: `_location_context` (`src/vault/paths.py:117-121`) treats a bare `Projects/<file>.md` as `("project", "<file>")` — i.e. it *invents a project named after the file*. Any new misplaced predicate must not be fooled by that quirk.

**Today's partial behaviour (verified in code).**

- For **non-md** files, mis-drops already work — but by accident, not by design. `_store_nonmd` (`src/pipelines/capture.py:566-575`) only sets a target when `len(rel.parts) >= 2`; a bare `Projects/x.pdf` has `rel.parts == ("x.pdf",)`, length 1, so `target_type` stays `None` and the file falls into the CLUELESS branch (L699-715) which moves it to inbox and writes a pending-routing marker. So non-md misplaced→inbox is **already the behaviour**; T4 only needs to make it *intentional and named*, and to extend it to md.
- For **md** files, there is no sweep. `scan_vault` (`src/vault/indexer.py:194-224`) indexes every readable `.md` anywhere in the vault (except IGNORE_DIRS / dotfiles), so a `.md` in bare `Projects/` becomes a `VaultEntry`, lands in `summary.added`, and `_store_md` (`src/pipelines/capture.py:528-537`) writes it **in place** — it is never moved. T4's md half is genuinely new.
- AI-output folders are currently **fully captured**: `scan_vault` and `scan_non_md_drops` (`src/vault/indexer.py:104-153`) walk them, and the watcher's `_should_skip` (`src/vault/watcher.py:124-141`) does not mention them. So a briefing dropped in `Briefings/` is captured today. T4's exclusion half is also genuinely new.

**Which guards / constraints apply.**

- **Vault-only writes (C-01).** Sweeping a file to inbox is a move. Non-md uses `move_attachment` (capture.py:711); md must use `move_note` (already imported in `vault/watcher.py` and used in `_store_md`). No raw `write_text`. The pending-routing marker write goes through `write_note` (capture.py:734) — already compliant.
- **`updated_by_human` gate (C-02) + write_note merge (C-03).** Relevant only on the marker write, which is unchanged. Moving a misplaced md to inbox must **not** rewrite its body or strip frontmatter — a plain disk move (`move_note`) preserves on-disk metadata; the subsequent in-inbox capture is a normal pipeline run. No regression here as long as we move, not rewrite.
- **Audit (C-13).** The existing CLUELESS branch already writes an audit row (capture.py:747-757). A misplaced-md sweep should likewise leave an audit trail (see Developer-must-verify).
- **Result type (C-12).** Any new predicate added to `vault/paths.py` returns a plain `bool` — `paths.py` holds path predicates (`_is_in_managed_attachment`, `_is_managed_summaries_area`), not pipeline functions, so they are exempt from the Result rule, consistent with the existing twins. New pipeline-level routing functions in `pipelines/` must return `Result`.
- **Thresholds / prompts as config (C-06/C-07).** Not touched — no new LLM call, no new threshold.
- **CONFIG import scope in tests (C-17).** Tests must build `VaultConfig`/`MainConfig` with explicit `root=tmp_path` (per T1 decisions), never module-scope CONFIG.

**Files touched.**

- *Directly:* `src/vault/paths.py` (new predicate(s)); `src/vault/watcher.py` (`_should_skip` adds AI-output exclusion; misplaced-md handling on `on_created`/`on_moved`); `src/pipelines/capture.py` (`scan_capture` md loop + the `_store_md`/`_store_nonmd` misplaced routing); `src/vault/indexer.py` (`scan_vault` + `scan_non_md_drops` prune AI-output dirs).
- *Indirectly:* `src/cli/main.py` `watch` callbacks (`on_create`/`on_move`) inherit the new skip via the watcher; no edit needed if exclusion lives in `_should_skip`.

**Downstream effects / runtime deps.** This is **shallow and central**: the predicates are pure path math (deletion test passes — delete them and capture-exclusion + sweep simply revert to today's behaviour). No new module boundary, no new interface. The one cross-cutting risk is the **OQ-008 blind spot** (L178): capture-excluding `Documentation/` means a human edit to a Documentation page is invisible to the system. This is a *known, logged, deferred* trade-off (OPEN_QUESTIONS.md OQ-008), not a T4 blocker — co-authoring is a future phase.

## 2. Guardrail Checklist

| Rule | Applies? | How the recommended option satisfies it |
|---|---|---|
| **C-01** vault-only writes | Yes | Sweep uses `move_note`/`move_attachment`; marker via `write_note`. No raw writes. |
| **C-02** updated_by_human gate | Yes (indirect) | Misplaced md is *moved*, not AI-rewritten; gate untouched. |
| **C-03** write_note pure-writer | Yes (indirect) | No new metadata merge; marker write already re-passes full `NoteMetadata`. |
| **C-12** Result at pipeline boundaries | Yes | New routing in `_store_md`/`scan_capture` returns `Result`; new `paths.py` predicates are bool (consistent with existing twins, not a pipeline boundary). |
| **C-13** audit every decision | Yes | Misplaced-md sweep writes an audit row mirroring the CLUELESS non-md path (`outcome="MISPLACED"` or reuse `CLUELESS`). |
| **C-06 / C-07** thresholds & prompts in config | N/A | No LLM, no threshold added. |
| **C-17** no module-scope CONFIG in tests | Yes | Tests build `VaultConfig(root=tmp_path)`. |
| **CLAUDE.md: monkeypatch the importing module** | Yes | Tests that patch `move_note`/`audit_write` must target `vault.watcher.<name>` / `pipelines.capture.<name>`, not the source module (TD-033). |
| **CLAUDE.md: do NOT add names to `IGNORE_DIRS`** | Yes | AI-output exclusion is a *capture* skip, NOT an indexer/FTS skip; never added to `IGNORE_DIRS` (would blind search + co-author detection). Per T1 CONSUMER OBLIGATION. |
| **CLAUDE.md: `logging` is %s-style** | Yes | Any new `_log.*` lines in watcher use `%s`, not kwargs. |
| **CLAUDE.md: vault-relative paths from `self._root`** | Yes | Watcher relpaths via `relative_to(self._root)`, not CONFIG. |
| **DECISION-029: `.summaries/` writes set `type=attachment-summary`** | Yes | Sweep-to-inbox does not create a sibling; the later in-inbox capture re-uses the existing CLUELESS marker path which already sets the type. |

## 3. Success criteria

**You can verify (vault-visible):**
1. *Given* a `.md` file dropped in `Briefings/`, *When* `kms scan-capture` runs (or the watcher is live), *Then* no new note appears anywhere and the briefing stays untouched in `Briefings/`.
2. *Given* a `.pdf` dropped in `Synthesis/`, *When* scan runs, *Then* it is NOT moved and NO `.summaries/` marker is created for it.
3. *Given* a `.docx` dropped directly in `Projects/` (no project subfolder), *When* scan/watcher runs, *Then* the `.docx` appears in `inbox/`.
4. *Given* a `.md` dropped directly in `Projects/` (no subfolder), *When* scan/watcher runs, *Then* the `.md` appears in `inbox/` (this is the new behaviour).
5. *Given* a `.md` already living in a real `Projects/<A>/` root, *When* scan runs, *Then* it is left in place (NOT swept).

**Developer must verify:**
- *Audit row:* the misplaced-md sweep writes an `audit_log` row with `pipeline="capture"`, `stage="store"`, a non-empty `source_ids`, and an outcome marking it misplaced (mirrors the CLUELESS non-md row at capture.py:747-757).
- *DB row:* a swept md, after its in-inbox capture, has exactly one `documents` row whose `vault_path` is under `inbox/` (no stray row at the old bare-`Projects/` path; if it was indexed first, the old row is removed/replaced — verify no orphan).
- *Result type:* the new `scan_capture` misplaced-md branch and `_store_md` routing return `Success`/`Failure`, never raw `None`.
- *Log line:* watcher skip of an AI-output file emits a debug/info line (so the exclusion is observable), using `%s` formatting.
- *Non-interference (watcher vs scan):* dropping a briefing while both the live watcher and a manual `scan_capture` could race must yield the same result (ignored) from either path — the exclusion lives in a shared predicate, so both honour it identically.

## 4. Options

### Option A — Inline checks at each call site (minimal, no new predicate)
- **What this means (plain English):** Patch each place that walks or dispatches files, adding a small `if part in (briefings, synthesis, documentation): skip` and a bare-Projects length check, written out at every site.
- **Approach:** In `_should_skip` (watcher), `scan_vault` + `scan_non_md_drops` prune loops, and `scan_capture`'s md loop, inline the exclusion. For misplaced md, inline a `len(rel.parts) < 2` check in `scan_capture` before dispatching, moving the file to inbox in place.
- **Files touched:** `watcher.py`, `indexer.py` (two functions), `capture.py` (`scan_capture`).
- **Cost:** dev low; runtime negligible; maintenance **high** — the exclusion logic is duplicated in 4+ places, and the bare-`Projects/` quirk must be re-derived at each.
- **Risk:** the four copies drift; the `_location_context` quirk (paths.py:117) gets handled in one place and forgotten in another → silent mis-route. CLAUDE.md explicitly flags these near-twin path checks as a silent-bug hotspot.
- **Module depth:** shallow but **no seam** — pure copy-paste; fails the "single source of truth" smell test.
- **Defers:** nothing; but bakes in duplication.
- **Constraints check:** OK on C-01/C-13 if each site is careful; fragile by construction.

### Option B — Two named predicates in `vault/paths.py` (Recommended)
- **What this means (plain English):** Add two small, well-named yes/no helpers next to the existing path predicates — one says "is this an AI-output file we must never capture?", the other says "is this file in a not-a-real-home spot?". Every call site asks the helper; the rule lives in exactly one place.
- **Approach:**
  - `_is_ai_output(path, vault_cfg) -> bool`: True iff any path part equals one of `vault_cfg.ai_output_dirs` (the T1 tuple) *and* the part's parent chain roots at `vault_cfg.root` (i.e. it is the top-level `Briefings/` etc., not a user folder coincidentally named "Briefings" deep inside a project — match by `path.parts` against the dir names, accepting the same documented limitation T5 accepted for `attachment`).
  - `_is_misplaced(path, vault_cfg) -> bool`: True iff the path is internal, NOT inbox, NOT AI-output, and NOT a *real* project/domain location. "Real" = under `projects_path`/`domain_path` with `len(rel.parts) >= 2` (a subfolder exists) — this directly neutralises the `_location_context` quirk by requiring the second path part.
  - Wire `_is_ai_output` into `_should_skip` (watcher), and into the prune loops of `scan_vault` + `scan_non_md_drops`. Wire `_is_misplaced` into `scan_capture`'s md loop (sweep via `move_note` to inbox, then capture in place) and rely on the existing CLUELESS branch for non-md (which already produces the misplaced→inbox outcome). Watcher `on_created`/`on_moved` for a misplaced md routes through the same `on_create` callback → `capture_file` → which now sees an inbox file.
- **Files touched:** `vault/paths.py` (two predicates), `vault/watcher.py` (`_should_skip` one line), `vault/indexer.py` (two prune loops), `pipelines/capture.py` (`scan_capture` md loop + a small misplaced-md sweep helper returning `Result`).
- **Cost:** dev low-moderate; runtime negligible; maintenance **low** — one definition each.
- **Risk:** low. Main risk is the prune loops in `scan_vault`/`scan_non_md_drops` currently prune by *dirname* inside `root.walk()`; adding AI-output dirnames there means a user folder named `Briefings` inside a project is also skipped — same accepted limitation T5 took for `attachment`/`.summaries`. Acceptable for the non-technical target user; document it.
- **Module depth:** deep enough to pass the deletion test (remove both predicates → behaviour cleanly reverts to today). Predicates are a real seam: 4+ adapters already call the sibling predicates `_is_in_managed_attachment`/`_is_managed_summaries_area`, so adding two more in the same module is *consistent*, not speculative.
- **Defers:** OQ-008 (human edits to excluded folders) — explicitly deferred.
- **Constraints check:** C-01 (move_note), C-13 (audit on sweep), C-12 (Result on the new pipeline helper), C-17 (tmp_path tests) all satisfied; honours T1's "don't add to IGNORE_DIRS" obligation.

### Option C — Route misplaced files through a single new pipeline "triage" stage
- **What this means (plain English):** Add a first pipeline stage that classifies every incoming path as {ai-output→skip, misplaced→move-to-inbox, valid→continue} before any extract/summarise runs, so all routing is in one funnel.
- **Approach:** New `triage` stage prepended to the capture pipeline list (capture.py:912), plus the watcher/scan exclusion still needed up front (you cannot run a pipeline stage on a file you never dispatch) — so exclusion is partly duplicated anyway.
- **Files touched:** `capture.py` (new stage + pipeline wiring + `_store_md`/`_store_nonmd` interaction), `watcher.py`, `indexer.py`. Largest surface.
- **Cost:** dev high; runtime one extra stage per file; maintenance moderate — a real new stage to keep in step with the others.
- **Risk:** higher — a new pipeline stage touches the headline capture flow; the AI-output skip still must live in the watcher/scan (the pipeline never sees skipped files), so the "single funnel" promise is only half-true and you end up with logic in *two* layers.
- **Module depth:** introduces a new pipeline stage — a real boundary, but **speculative** for a task this small; the deletion test is awkward (removing the stage changes pipeline composition).
- **Defers:** OQ-008.
- **Constraints check:** C-12/C-13 fine; but over-builds relative to the settled scope (anti-goal: don't touch routing-onward, which Phase 2 owns).

## 5. Recommendation

**Option B.** It places both new behaviours behind two named predicates in the file that already hosts the project's path-classification logic (`vault/paths.py`, alongside `_is_in_managed_attachment` / `_is_managed_summaries_area`), so the misplaced-vs-valid and AI-output rules each have exactly one definition that the watcher and the scan both consult — eliminating the drift risk that CLAUDE.md explicitly warns about for these near-twin predicates. The trade-off: a misplaced markdown note now takes one extra hop (sweep to inbox, then capture), where today it was a silent no-op — but that hop is the *point* of the task, and it reuses the move-to-inbox machinery the non-md CLUELESS path already proves out.

## 6. Cross-check

- **Scope creep removed:** No "routing onward" from inbox (Phase 2 Classify owns that — anti-goal honoured). No edit to `IGNORE_DIRS` (T1 obligation; would change FTS/index scope). No new `editable_extensions` list. No reconcile changes (that is T10).
- **Constraint violations:** none introduced. ⚠️ **Watch item:** when wiring `_is_ai_output` into the `scan_vault`/`scan_non_md_drops` prune loops, prune by the AI-output *dirname* only at the vault root level if you want to avoid the "user folder named Briefings inside a project" false-skip; the simplest implementation prunes by name anywhere and accepts that limitation (consistent with T5). Pick the same posture as T5 for consistency and document it. ⚠️ The `_location_context` quirk (paths.py:117-121) is **not** reused for the misplaced check — `_is_misplaced` does its own `len(rel.parts) >= 2` test to avoid the phantom-project bug; do not call `_location_context` for this decision.
- **Tech-debt items:** OQ-008 — **worsened-by-design but already logged**: capture-excluding `Documentation/` (and `Briefings/`/`Synthesis/`) makes future human edits there invisible until a co-author edit-detection path exists. No code action this task; the predicate is the natural future hook (a `Documentation/`-only modify listener can reuse `_is_ai_output`). Neutral on TD-037/TD-039 (content-change is T9). 
- **DECISION conflicts:** none. DECISION-027 (CLUELESS inbox parking) is *reused* for the non-md misplaced path. DECISION-029 (`.summaries/` type guard) untouched — the sweep creates no sibling.
- **Composition with prior tasks:** Uses T1's `VaultConfig.ai_output_dirs` tuple verbatim (T1 CONSUMER OBLIGATION for T4: iterate `ai_output_dirs`, do not touch `IGNORE_DIRS` — honoured). Independent of T2/T3 placement helper (this task only decides *capture vs skip vs sweep-to-inbox*, never editable-vs-no-edit placement), matching the build-order note that "T4 only needs T1's AI-output list, no T2 dependency" (draft L428).
- **[REQUIRES: T1]** `VaultConfig.ai_output_dirs` property must exist before T4's predicates compile.

---

## T2 — Single shared placement helper

A single pure function that decides **where a captured file physically lands** — given the file and its resolved project/domain home — and returns both the binary's destination directory and its summary-sibling directory. It is the *only* copy of the editable-vs-no-edit routing rule, so the capture pipeline today and the Phase 2 Classify pipeline later cannot drift apart.

### 1. Implications

**Plain English — what this task actually adds.** Right now the rule "where does a captured file go?" is hand-written *inside* the capture pipeline (the block at `src/pipelines/capture.py:561-575` and again at `:593-615`). T1 introduced the concept of *no-edit* files (pdf/images) versus *editable* files (docx/xlsx/pptx). T2 lifts the placement decision into one named function so that when Phase 2 Classify is built it calls the exact same function — otherwise an editable file that arrives via Classify would silently get buried in the hidden `attachment/` folder, which is the whole bug this restructure exists to kill.

**What the key terms mean in THIS codebase.**
- *No-edit file*: a non-`.md` file whose lowercased suffix is in `vault_cfg.no_edit_extensions` (the new T1 field). It belongs in `attachment/` (hidden from Obsidian) — current behavior, unchanged.
- *Editable file*: any non-`.md` file NOT in `no_edit_extensions`. It belongs in the project/domain **root** (visible). There is deliberately no `editable_extensions` list (T1 decision) — editable is the complement.
- *Sibling*: the AI-written `.md` summary for a binary, named `<binary.name>.md` (full filename incl. extension), living in a `.summaries/` subfolder of the binary's parent. The naming rule is fixed and already implemented twice — `_sibling_for` at `src/vault/watcher.py:54` and the inline write at `src/pipelines/capture.py:633`. T2 reuses it, but anchored to the binary's *final* parent.
- *In-attachment*: detected by the existing pure predicate `_is_in_managed_attachment(path, vault_cfg)` at `src/vault/paths.py:26` — True only directly under `Projects/<A>/attachment/` or `Domain/<D>/attachment/`.

**What the helper is and is NOT.** It is pure path arithmetic (the task's own anti-goal: "No LLM in this helper — pure path math"). Concretely it must NOT: call the LLM, read file bytes, compute the rename-gate stem, run the collision loop, or `mkdir`. All of those stay in the consumer T3. The helper's single job: given the file and its `(target_type, target_name)`, decide the **final directory** for the binary and the **`.summaries/` directory** for its sibling, plus a `needs_move` boolean.

**Guards/constraints that apply.** Because the helper writes nothing and makes no AI decision, most heavyweight constraints (vault-only writes C-01, `updated_by_human` C-02, audit C-13, prompts-as-config C-07, thresholds-in-config C-06) are the **consumer's** responsibility (T3), not T2's. The two that *do* bear on T2:
- **Result type (C-12).** C-12 requires public functions in `handlers/` and `pipelines/` to return `Success`/`Failure`. This helper lives in `vault/paths.py`, which is exempt — and every existing function there returns a `Path` or `bool`, not a `Result` (`_is_in_managed_attachment`, `project_attachment`, etc.). T2 follows that local convention: it returns a plain dataclass, no Result. (Justification: there is no recoverable failure mode in pure path math — bad input is a programmer error, not a runtime condition.)
- **Config-as-data.** The no-edit set is data (`vault_cfg.no_edit_extensions`), never a hardcoded literal in the helper (CLAUDE.md "New threshold or rule → edit a config file"). The helper takes `vault_cfg` explicitly and reads the list from it.

**Files touched.**
- *Directly:* `src/vault/paths.py` — one new function + one small dataclass. (No edit to the four existing parametrized helpers; the new function builds its directories from the passed `vault_cfg`, not from the CONFIG singleton, to stay testable.)
- *Indirectly / downstream:* `src/pipelines/capture.py::_store_nonmd` will be rewired to call it in T3 (out of scope here). Phase 2 Classify (not built) is the second future caller.

**Runtime deps.** None new. The helper depends only on `pathlib` and the `VaultConfig` fields (`no_edit_extensions`, `attachment_dir`, `summaries_subdir`, `projects_path`, `domain_path`) plus the existing `_is_in_managed_attachment` predicate in the same module.

**Module depth (deep vs shallow; deletion test).** Shallow and clean. The function is a thin, total mapping from inputs to a path triple — a real seam, because it has **2+ adapters** (capture today, Classify later), so the abstraction is *not* speculative. Deletion test: if you delete this function, both callers must inline the identical rule and immediately drift — exactly the failure this task prevents. That is the signature of a justified boundary, not premature abstraction.

### 2. Guardrail Checklist

| Rule | Applies? | How the recommended option satisfies it |
|---|---|---|
| C-01 vault-only writes | Indirect | Helper writes nothing; it returns paths. T3 (consumer) performs the writes via `write_note`/`move_attachment`. |
| C-02 `updated_by_human` gate | No (consumer's) | No write here. |
| C-12 Result return in pipelines/handlers | Exempt (lives in `vault/`) | Follows the local `vault/paths.py` convention: returns a dataclass, matching `_is_in_managed_attachment`→bool, `project_attachment`→Path. |
| C-13 audit log | No (consumer's) | The LOCATED/CLUELESS audit rows are written by `_store_nonmd`/Classify, not the helper. |
| C-06 thresholds in config | N/A | No thresholds; the routing rule is a deterministic type test, not a confidence gate. |
| Config-as-data (CLAUDE.md) | Yes | No-edit set read from `vault_cfg.no_edit_extensions`; never a literal in the helper. |
| C-17 no module-scope CONFIG in tests | Yes (tests) | Helper takes `vault_cfg` explicitly; tests build `VaultConfig(root=tmp_path)`. Helper itself does NOT import the CONFIG singleton. |
| Sibling naming rule (CLAUDE.md gotcha) | Yes | Reuses `<binary.name>.md` (full filename incl ext), matching `_sibling_for` (`watcher.py:54`) — never `<stem>.md`. |
| DECISION-029 type guard | Downstream (T3) | Helper only computes the `.summaries/` *path*; T3 sets `type=attachment-summary` when writing, including root `.summaries/`. |

### 3. Success criteria

**You can verify (vault-visible only):**
1. *Given* a `.docx` dropped into `Projects/Alpha/` *when* capture runs *then* the file is at `Projects/Alpha/report.docx` (root, visible) and its summary at `Projects/Alpha/.summaries/report.docx.md`. (End-to-end through T3.)
2. *Given* a `.pdf` dropped into `Projects/Alpha/` *when* capture runs *then* the file is at `Projects/Alpha/attachment/report.pdf` (hidden) and its summary at `Projects/Alpha/attachment/.summaries/report.pdf.md`.
3. *Given* a `.docx` that already sits in `Projects/Alpha/attachment/` *when* capture runs *then* it is pulled out to `Projects/Alpha/report.docx` with its sibling in `Projects/Alpha/.summaries/`.
4. *Given* a `.pdf` already in `Projects/Alpha/attachment/` *when* capture runs *then* it stays put (no move), sibling in `attachment/.summaries/`.
5. The same four outcomes hold under `Domain/<D>/` (symmetry).

**Developer must verify:**
1. `resolve_placement` returns a `Placement` value (dataclass), never a `Result`, for every branch — and is a *total* function (no branch returns `None`).
2. For the editable-in-attachment case, `needs_move is True` and `final_dir` is the project/domain root; for no-edit-already-in-attachment, `needs_move is False` and directories are unchanged.
3. The sibling dir returned is exactly `final_dir / vault_cfg.summaries_subdir` in every branch (sibling follows the binary's *final* parent, not its source).
4. `path.suffix.lower()` is used for the no-edit membership test (uppercase `.PDF` routes as no-edit). Unit test with a mixed-case suffix.
5. Non-interference (helper × consumer): the helper performs no filesystem mutation, so two concurrent capture runs sharing the helper cannot collide *in the helper* — all mutation/collision handling is serialized in T3's collision loop. A unit test asserts no `mkdir`/`write`/`read_bytes` occurs (e.g. point the helper at a `tmp_path` and assert no new dirs were created).

### 4. Options

#### Option A — Pure function + `Placement` dataclass in `vault/paths.py`, directories only (Recommended)
- **What this means (plain English):** Add one function that answers "which folder, and which `.summaries/` folder" — it hands back directories and a move flag, and lets the caller pick the exact filename. The editable/no-edit rule lives here and nowhere else.
- **Approach:** `resolve_placement(file_path, target_type, target_name, vault_cfg) -> Placement(final_dir, sibling_dir, needs_move)`. Rule: `is_no_edit = file_path.suffix.lower() in vault_cfg.no_edit_extensions`; `in_attachment = _is_in_managed_attachment(file_path, vault_cfg)`. no-edit & not in_attachment → final_dir = `<type>/<name>/attachment`; editable & in_attachment → final_dir = `<type>/<name>` (root); else final_dir = current parent. `sibling_dir = final_dir / summaries_subdir`. `needs_move = final_dir != file_path.parent`. Built from `vault_cfg` (not the CONFIG singleton).
- **Files touched:** `src/vault/paths.py` only (one function, one frozen dataclass).
- **Cost:** Dev — small (≈30 LOC + 6 tests). Runtime — negligible (pure arithmetic). Maintenance — low; one place to change the rule.
- **Risk:** Low. Main subtlety: keeping the helper free of the rename-gate stem and collision loop so it stays pure — handled by returning *directories*, not full filenames.
- **Module depth:** Real seam (2+ adapters). Passes the deletion test. Not speculative.
- **Defers:** Rename-gate stem, collision loop, `mkdir`, writes — all to T3. Root-`.summaries/` recognition by reconcile predicates → T10.
- **Constraints check:** C-12 (exempt, follows local Path/bool convention); config-as-data ✓; C-17 ✓ (explicit `vault_cfg`); sibling naming ✓.

#### Option B — Pure function that returns full final filenames (binary + sibling)
- **What this means:** The helper also picks the final filename and resolves collisions, returning `(final_binary_path, sibling_path)` ready to write.
- **Approach:** Same routing rule, but the helper runs the `-1/-2/…/100` collision loop and applies the sanitized stem.
- **Files touched:** `src/vault/paths.py` — but now it needs the rename-gate decision (`decide_rename`, `capture.py:587`) and must touch the filesystem to test `attachment_dst.exists()`.
- **Cost:** Dev — medium. Runtime — does disk `exists()` checks. Maintenance — higher: pulls rename-gate + collision policy into `vault/paths.py`, a module that today never touches disk for *files*.
- **Risk:** Medium-high. Breaks the module's purity (it would now hit the filesystem and depend on capture's rename gate), and entangles the collision policy that T3's own done-when (`capture.py:600-611`) wants to own.
- **Module depth:** Deeper, but the extra depth is the *wrong* responsibility absorbed (collision + rename), violating "pure path math" anti-goal.
- **Defers:** Nothing — but takes on too much.
- **Constraints check:** Would force a filesystem read into a pure helper; conflicts with the task anti-goal "pure path math". ⚠️

#### Option C — Add `editable_placement`/`no_edit_placement` as two helpers + a thin dispatcher
- **What this means:** Split the rule into two named helpers (one per file class) plus a tiny chooser.
- **Approach:** `no_edit_placement(...)`, `editable_placement(...)`, and `resolve_placement(...)` that picks one by suffix.
- **Files touched:** `src/vault/paths.py` (three functions).
- **Cost:** Dev — medium (3 functions, more tests). Runtime — negligible. Maintenance — the rule is still "single source" via the dispatcher, but spread over three names.
- **Risk:** Low-medium. More surface for the symmetric rule to be stated inconsistently between the two halves (the exact drift T2 exists to prevent).
- **Module depth:** Three shallow functions where one suffices — speculative split for a single-call-shape rule.
- **Defers:** Same as A.
- **Constraints check:** Same as A, but weaker on the "one place" goal (rule physically lives in two helpers).

### 5. Recommendation

**Option A.** It is the only option that keeps the helper *pure path math* (the task's explicit anti-goal protects this) while still being the single source of truth: returning directories + a move flag leaves the rename-gate stem and the collision loop where they already live in T3 (`capture.py:587`, `:600-611`), so T2 absorbs exactly the editable/no-edit rule and nothing else. The tradeoff: T3 must do one extra step (combine `final_dir` with the sanitized filename) rather than receiving a ready-to-write path — a deliberate cost paid to keep `vault/paths.py` filesystem-free and trivially unit-testable.

### 6. Cross-check

- **Scope creep removed:** Rejected pulling the collision loop and rename-gate into the helper (Option B) — those stay in T3. Helper does no `mkdir`/IO.
- **Constraint violations flagged:** None for Option A. ⚠️ on Option B (filesystem read inside a pure path helper, against the task anti-goal).
- **Tech-debt items:** Neutral. Does not touch TD-029 (rename gate), TD-037 (binary-modify), TD-039 (Windows). Slightly *improves* the latent duplication risk that Phase 2 Classify would otherwise create.
- **DECISION-NNN contradictions:** None. Preserves DECISION-025 (sibling-first — ordering is T3's; helper only supplies paths), DECISION-029 (type guard — T3 sets `type=attachment-summary` for root `.summaries/` too), DECISION-026 (pure path math, no AI in destination resolution — reinforced), DECISION-018 (non-md never re-processed — unaffected). The sibling naming reuses the `<binary.name>.md` rule (DECISION-028).
- **Composes with prior tasks:** Uses T1's `no_edit_extensions` and the `path.suffix.lower() in vault_cfg.no_edit_extensions` membership form (T1 consumer obligation). Does not duplicate T4's `_is_ai_output`/`_is_misplaced` predicates (different concern — placement vs eviction). T3 is the consumer.
- **Labels:** [REQUIRES: T1] (`no_edit_extensions` field). [REQUIRES: T10] for reconcile Stage 4 to recognize root-level `.summaries/` as a managed summaries area — T2 writes siblings there but does not teach `_is_managed_summaries_area`/`_is_in_managed_attachment` about the root case (cross-cutting note, draft L391). [CONSUMED-BY: T3, Phase 2 Classify].

---

# T3 — `_store_nonmd`: symmetric, type-driven `needs_move`

## Implications

**Plain English.** Today, when the system captures a non-`.md` file (a PDF, a Word doc, a spreadsheet) that already sits inside a project or domain folder, it does one thing: shove it into the hidden `attachment/` folder. That is correct for read-only references (PDFs, images) but wrong for files the executive actually edits (Word/Excel/PowerPoint) — those vanish into a folder Obsidian hides. This task makes the placement rule *symmetric*: no-edit files go into `attachment/` (unchanged), editable files go to (or stay in) the visible project/domain root. The AI-written one-page summary (the "sibling") always follows the binary to its final home's `.summaries/` folder. No AI call decides this — it is pure path math.

**What the key terms mean in this codebase:**
- *No-edit file* = a non-`.md` file whose lowercased suffix is in `vault_cfg.no_edit_extensions` (new field from T1: pdf/png/jpg/jpeg/gif/webp). Routes into `attachment/`.
- *Editable file* = any non-`.md` file NOT in that list (docx/xlsx/pptx/…). Routes to the project/domain root.
- *Sibling* = the AI summary `.md`, named `<binary.name>.md` (full filename incl. extension, e.g. `report.pdf.md`), living under `<final binary parent>/.summaries/`. The naming rule is the existing `_sibling_for` convention (CLAUDE.md gotcha: `<binary.name>.md`, NOT `<stem>.md`).
- *`needs_move`* = today a single boolean computed from `rel.parts[1] != attachment_dir` (capture.py:570, 575). T3 replaces this with the type-driven `Placement.needs_move` from T2 (`final parent != current parent`).

**The actual code being replaced.** `_store_nonmd` (capture.py:540-763) has two branches. The LOCATED branch (when the file is already under `Projects/<A>/` or `Domain/<D>/`) is the one T3 changes:
- The inline destination resolution at **capture.py:561-575** sets `target_type`, `target_name`, and `needs_move`. The `needs_move` line is the bug: it moves *everything* not already in `attachment/` *into* `attachment/`, including editable files. This block is replaced by a call to T2's `resolve_placement`.
- The directory selection at **capture.py:593-598** (`project_attachment`/`project_summaries` vs `domain_attachment`/`domain_summaries`) hardwires the attachment subtree. T2's `Placement(final_dir, sibling_dir, needs_move)` supplies these directly, so this block is deleted.
- The collision loop at **capture.py:600-611** (suffix `-1`, `-2`, … up to 100) currently runs only against `att_dir`. It is generalised to run against `Placement.final_dir` (which may now be a root dir for editable files). Open-question OQ in the draft (L143) asks whether root collision policy mirrors attachment policy — **answer: yes**, same `-N` loop, no new policy.
- The CLUELESS branch (capture.py:699-763, files with no project/domain context → parked in inbox) is **out of scope for T3** — T4 owns the misplaced→inbox sweep. T3 leaves it byte-for-byte unchanged.

**Guards/constraints that apply (and stay satisfied automatically):**
- *Vault-only writes (C-01):* T3 keeps using `write_note` (capture.py:650) and `move_attachment` (capture.py:658) — both route through `vault/writer.py`. No raw `write_text`. The only change is the *destination path argument*, not the write mechanism. (The hook hard-blocks any direct write, so this is enforced.)
- *write_note merge rule (C-03) / `updated_by_human` (C-02):* unaffected — the sibling is AI-authored at `actor="ai"`; placement does not touch human-edited fields.
- *Result type (C-12):* `_store_nonmd` already returns `Result[WriteOutcome]` on every path; T3 adds no new early return except the existing collision-exhausted `Failure` (capture.py:606-611), which it reuses verbatim against the generalised `final_dir`.
- *Audit (C-13):* the LOCATED audit row (capture.py:673-690) is preserved unchanged. Its `reasoning` string (`f"Routed to {target_type}/{target_name}"`) should be enriched to note the class (editable→root vs no-edit→attachment) so the briefing/audit trail is legible — a one-line string change, not a new decision point.
- *Prompts as config (C-07):* the `summarize_attachment` prompt call (capture.py:618-628) is untouched.
- *Type guard (DECISION-029):* the sibling metadata `type="attachment-summary"` (capture.py:640-649) MUST be kept for editable-in-root siblings too — reconcile Stage 4 (T10) depends on this even outside `attachment/`. T3 changes only *where* the sibling is written (`Placement.sibling_dir`), never its `type`.
- *source_hash (capture.py:638-639):* kept verbatim — it is the anchor for T9 content-change detection. The "hash src before move, dst after" logic still holds because the bytes are identical across the move regardless of destination.

**Files touched:**
- *Directly:* `src/pipelines/capture.py` (`_store_nonmd` LOCATED branch only), and its test file `tests/test_pipelines/test_capture.py` (new branch tests).
- *Indirectly (consumed, not edited by T3):* `src/vault/paths.py` `resolve_placement` (authored by T2). T3 deletes the now-redundant lazy import of `project_attachment`/`project_summaries`/`domain_attachment`/`domain_summaries` at capture.py:550-555 (the helper supplies the dirs).

**Downstream effects / runtime deps:** Editable binaries now land in the project/domain root, so the `documents` DB row's `vault_path` for the sibling points at root `.summaries/`, and `attachment_path` frontmatter points at the root binary. Indexer/watcher already capture root-level binaries (a root-level binary is NOT in a managed attachment subtree, so `_is_in_managed_attachment` returns False → it is a drop target). [REQUIRES: T10] reconcile must learn to recognise root `.summaries/` (flagged by T2; the two near-twin predicates in paths.py:26 & L56 do not yet recognise it).

**Module depth.** Shallow change. T3 owns no new boundary — it consumes one new pure helper (T2). Deletion test: if T3's edit were reverted, the only loss is the editable-vs-no-edit routing; nothing else breaks. No new interface, no speculative seam.

## Guardrail Checklist

| Rule | Applies? | How the recommended option satisfies it |
|---|---|---|
| C-01 vault-only writes | Yes | Continues to call `write_note`/`move_attachment` (writer.py); only the destination Path changes. Hook-enforced. |
| C-03 write_note merge | Yes | Sibling written by AI with explicit `NoteMetadata`; no field-preservation issue (new file). |
| C-12 Result type | Yes | `_store_nonmd` returns `Result[WriteOutcome]` on all paths; reuses existing collision `Failure`. |
| C-13 audit | Yes | LOCATED audit row preserved; `reasoning` enriched with the routing class. |
| C-07 prompts as config | Yes | `summarize_attachment` prompt call untouched. |
| C-17 tests never import CONFIG at module scope | Yes | New tests build `VaultConfig(root=tmp_path)` explicitly; no module-scope CONFIG. |
| DECISION-029 type guard | Yes | `type="attachment-summary"` kept for root siblings. |
| DECISION-025 sibling-first ordering | Yes | Write-sibling-then-move-binary order preserved; only destination changes. |
| CLAUDE.md `_sibling_for` naming | Yes | Sibling name stays `<binary.name>.md` (full filename incl. ext) via `Placement.sibling_dir`. |

## Success criteria

**You can verify (vault-visible):**
1. *Given* a `.docx` dropped into `Projects/Acme/` (root), *when* capture runs, *then* the `.docx` stays at `Projects/Acme/<name>.docx` (visible in Obsidian) and its summary appears at `Projects/Acme/.summaries/<name>.docx.md`.
2. *Given* a `.pdf` dropped into `Projects/Acme/` (root), *when* capture runs, *then* the `.pdf` is moved to `Projects/Acme/attachment/<name>.pdf` and its summary appears at `Projects/Acme/attachment/.summaries/<name>.pdf.md`.
3. *Given* a `.xlsx` found inside `Projects/Acme/attachment/` during capture, *when* capture runs, *then* the `.xlsx` is pulled out to `Projects/Acme/<name>.xlsx` (root) with its sibling at `Projects/Acme/.summaries/`.
4. *Given* a `.pdf` already inside `Domain/Finance/attachment/`, *when* capture runs, *then* it stays put (no move) and its sibling is at `Domain/Finance/attachment/.summaries/`.
5. *Given* two editable files that would collide on the root stem, *when* both are captured, *then* the second lands as `<stem>-1.<ext>` in the root (same `-N` policy as attachment today).

**Developer must verify:**
- *Audit row:* a `capture:store` row with `outcome="LOCATED"` is written for both editable-root and no-edit-attachment captures (capture.py:680-690), `source_ids` populated.
- *DB row:* the `documents` upsert (capture.py:693) for an editable file has `vault_path` = the root `.summaries/<name>.md` and the sibling's `attachment_path` frontmatter = the root binary path.
- *Result type:* collision-exhaustion still returns `Failure(recoverable=False)` with `final_dir` context, now keyed to the (possibly root) destination.
- *Non-interference (watcher vs capture):* a watcher-driven capture of a root-level editable binary must not re-trigger on its own move — the binary's final root location is not in a managed attachment subtree, and the sibling write uses `actor="ai"`; verify the watcher's own move does not produce a duplicate capture (covered by T8 suppression for the move story, but T3's in-place root capture must not loop on itself).

## Options

### Option A (Recommended) — Consume T2's `resolve_placement`, delete inline logic, generalise the one collision loop
- **What this means (plain English):** The placement decision lives in exactly one place (T2's helper). T3 just asks "where does this file go?" and then does its existing dance — pick a collision-free name, write the summary, move the binary — against whatever directory the helper named.
- **Approach:** Replace capture.py:561-575 with a `resolve_placement(src, target_type, target_name, vault_cfg)` call (after computing `target_type`/`target_name`, which T3 still derives from the path since the helper needs them as input). Delete the dir-selection block (capture.py:593-598). Point the collision loop and sibling path at `placement.final_dir` / `placement.sibling_dir`. Set `needs_move = placement.needs_move`. Keep sibling-first ordering, source_hash, audit, upsert verbatim.
- **Files touched:** `capture.py` (`_store_nonmd` LOCATED branch); remove the now-unused lazy import (capture.py:550-555); tests.
- **Cost:** Dev — small, mostly deletion + rewiring. Runtime — identical (still one LLM call, one move, one write). Maintenance — *lower*: one routing rule.
- **Risk:** Low. Main risk is the T2 helper not yet existing — T3 has a hard dependency on T2 (build order L420: T1→T2→T3). If T2 returns directories (recommended T2 signature) T3 owns the stem/collision; aligned.
- **Module depth:** No new boundary. Passes deletion test (revert loses only routing). No speculative interface.
- **Defers:** Root `.summaries/` recognition in reconcile predicates → [REQUIRES: T10]. CLUELESS/inbox placement for editable inbox files → Phase 2 Classify (cross-cutting L389).
- **Constraints check:** Satisfies all rows above. No duplicate rule (T2 anti-goal honoured).

### Option B — Keep inline logic, only flip the `needs_move` formula to be type-aware
- **What this means:** Minimal surgery — leave the destination plumbing in `_store_nonmd`, just change the one boolean and add a root-dir branch beside the attachment-dir branch.
- **Approach:** Replace capture.py:570/575 with `is_no_edit = src.suffix.lower() in vault_cfg.no_edit_extensions`; compute `needs_move` and pick `att_dir`/`sum_dir` OR a new `root_dir`/`root_sum_dir` inline. Add root-summaries dir math inline.
- **Files touched:** `capture.py` only; tests.
- **Cost:** Dev — small. Runtime — identical. Maintenance — *higher*: the editable/no-edit rule now exists in BOTH `_store_nonmd` and (per T2) `resolve_placement`/Phase 2 — exactly the duplication T2's anti-goal forbids.
- **Risk:** Medium — silent drift. When the rule changes (e.g. add `.heic`), two places must change; one will be missed.
- **Module depth:** Shallow but *worsens* coupling. Fails the "single source of truth" intent of T2.
- **Defers:** Same as A, plus it defers the T2 consumption (which T3 is explicitly tasked to do — "T3 must call `resolve_placement` and DELETE its inline logic", T2 decision).
- **Constraints check:** Violates the T2 anti-goal (no second copy of the rule). ⚠️ Contradicts a settled prior-task decision.

### Option C — Move ALL placement (incl. CLUELESS/inbox) behind one resolver
- **What this means:** Unify both branches of `_store_nonmd` so even the inbox-parking path asks the same helper.
- **Approach:** Extend `resolve_placement` to also handle `target_type=None` (inbox), collapsing capture.py:699-763 into the same flow.
- **Files touched:** `capture.py` (both branches), `paths.py` (helper extended beyond T2's scope), tests.
- **Cost:** Dev — larger. Runtime — identical. Maintenance — mixed: fewer branches but a fatter helper.
- **Risk:** High for THIS task — it reaches into the CLUELESS branch (T4's territory) and Phase 2's pending-routing contract. Scope creep.
- **Module depth:** Adds responsibility to a helper that T2 deliberately scoped to LOCATED placement only. Speculative generality not needed now.
- **Defers:** Nothing — but pulls T4/Phase-2 work forward prematurely.
- **Constraints check:** ⚠️ Scope violation — draft T3 Scope explicitly lists the misplaced→inbox relocation as OUT (T4), and the shared-helper extraction as T2.

## Recommendation

**Option A.** It is the only option that honours the settled T2 decision ("T3 must call `resolve_placement` and DELETE its inline destination logic — no second copy of the rule"), keeps every guard (sibling-first, source_hash, audit, Result, type guard) byte-identical, and confines the change to rewiring three small blocks. The tradeoff: T3 cannot land before T2 exists (hard dependency, already fixed in the build order) — accepted, because the alternative (Option B) buys independence at the cost of a duplicated routing rule that the design explicitly forbids.

## Cross-check

- **Scope creep removed:** Option C (touching CLUELESS/inbox) rejected — that is T4 + Phase 2. T3 touches only the LOCATED branch.
- **Constraint violations flagged:** Option B ⚠️ violates the T2 anti-goal (duplicate rule); Option C ⚠️ violates the draft T3 Scope (misplaced→inbox is OUT). Recommended Option A violates none.
- **Tech-debt items:** T3 is *neutral-to-improving*. It does not worsen TD-037 (binary-modify re-capture, T9) — source_hash anchor is preserved so T9 still works. It indirectly *enables* the headline value (editable files visible). It creates one forward dependency note, not new debt.
- **DECISION-NNN check:** Honours DECISION-025 (sibling-first ordering), DECISION-029 (type=attachment-summary guard kept for root siblings). Does not contradict DECISION-026 (pure path math, no AI) or DECISION-027 (CLUELESS parking — left untouched).
- **Labels:** [REQUIRES: T2] (the `resolve_placement` helper must exist first — hard dependency). [REQUIRES: T10] (reconcile must recognise root-level `.summaries/`; flagged by T2, not solved here).

---

## T8 — Sticky-note: suppress pipeline-initiated moves

### 1. Implications

**Plain English.** When the capture pipeline relocates a binary on its own (e.g. a no-edit PDF dropped in a project root gets moved into `attachment/`), that move surfaces to the folder watcher as a "file moved" event — exactly the same event a human move produces. Today the watcher already reacts to binary moves by syncing the sibling summary (rename or orphan it). Once T6 adds *re-home* (relocate sibling + re-derive location tags + move the binary per the editable/no-edit rule), the watcher would try to re-home a binary the pipeline *just* moved and already fully bookkept — the watcher would be fighting the pipeline. T8 is the guardrail that prevents that fight: before the pipeline moves a binary it drops a short-lived "sticky note" naming the destination path; when the watcher's move handler sees that path on its note, it skips re-home. The note auto-expires after a few seconds so a crash mid-move can never permanently deafen the watcher.

**What the key terms mean in THIS codebase.**
- "Pipeline-initiated move" = a call to `move_attachment(src, dst)` from `_store_nonmd` (capture.py:658 the LOCATED no-edit/relocate case, capture.py:711 the inbox-park case) and, for folder drops, `move_folder(...)` (capture.py:1297). These all run through the writer chokepoint (vault/writer.py:241, :304) per C-01.
- "Watcher's move handler" = `_VaultEventHandler.on_moved` (watcher.py:280). It currently does two things: (1) binary sync via `_handle_binary_move` keyed `bin:{dst}` (watcher.py:288-291), and (2) the user `on_move` callback keyed `str(dst)` (watcher.py:296). T6's re-home is the *new* behavior that must be suppressed; the existing sibling-sync is a separate concern (see Cross-check).
- "Same process" — confirmed. Under `kms watch` (cli/main.py:145) the watcher observer runs on its own thread (`Observer()`, watcher.py:504). File-drop captures are dispatched with `asyncio.run_coroutine_threadsafe(capture_file(...), loop)` onto the asyncio loop thread (cli/main.py:203-204, 222-223). Folder captures run as `asyncio.run(capture_folder(fp))` inside `self._folder_executor` (a `ThreadPoolExecutor`, watcher.py:478, 488-490). So a pipeline move can execute on the asyncio-loop thread OR on a pool worker thread, while the registry is *checked* on the observer thread. **This is genuine cross-thread shared state → the registry MUST be lock-guarded** (the file already uses `threading.Lock`/`threading.Timer` for its two existing registries, watcher.py:113, 122).

**Which guards/constraints apply.**
- **C-01 vault-only writes** — T8 itself performs no writes; it only registers/checks paths. The actual move still goes through `move_attachment`/`move_folder` (writer.py). No `.write_text()`/`open(...,'w')`.
- **C-12 Result type** — the pipeline *call sites* are inside `_store_nonmd`/`capture_folder` which already return `Result`. The registry's own methods are infrastructure (like `_debounce`, `_register_pending_folder`) and return plain values/None — consistent with the existing private handler helpers, which are not `Result`-typed. No new public pipeline function is introduced.
- **C-13 audit** — T8 makes **no AI decision**, so no audit row is required for the suppression act. (When suppression fires we *suppress* an action — there is no decision to log. A debug/info log line is the right artefact, matching the existing `watcher.create_skip`/`watcher.modify_skip` skip-logging pattern at cli/main.py:199, 216.)
- **C-17 tests** — build `VaultConfig(root=tmp_path)` / no module-scope `CONFIG` import.
- **Logging style** — inside watcher.py use stdlib `logging` with `%s` formatting (`_log.info("...path=%s", p)`), not kwargs (CLAUDE.md gotcha).
- **Monkeypatch target** — tests that patch the registry must patch `vault.watcher.<name>` (the importing module), not the source, per TD-033.

**Files touched.**
- *Directly:* `src/vault/watcher.py` (new registry wiring in `_VaultEventHandler.__init__`; a guard check in the T6 re-home branch of `on_moved`), and the move call sites in `src/pipelines/capture.py` (register the dst before `move_attachment`/`move_folder`). `src/cli/main.py` `watch()` wiring binds the shared registry so the same instance is visible to both the watcher and the pipeline (cli/main.py:253-260).
- *New (Option A):* `src/vault/move_guard.py` — a ~40-line `MoveGuard` class.
- *Indirectly:* T6 is the only consumer of the guard's `check`; T8 must land **before** T6 (build order L420-421: "never ship T6 without T8 in the same increment").

**Downstream effects / runtime deps.** No new third-party deps; uses stdlib `threading` + `time`. The TTL must outlive watchdog's event-delivery + debounce delay (`debounce_seconds` default 3.0s, watcher.py:473) but be short enough that a human move seconds later is NOT swallowed. Recommended TTL ≈ `debounce_seconds + a small margin` (~5s), read from a config field or derived from the existing debounce value rather than hardcoded.

**Module depth.** The registry is a *shallow but real* seam: one tiny module hiding the lock + TTL bookkeeping behind `register(path)` / `check_and_consume(path) -> bool`. Deletion test: if you delete it, T6 re-home fires on the pipeline's own moves → the watcher-vs-pipeline fight the task exists to prevent. So it earns its boundary. It is NOT speculative: it has exactly two real adapters from day one — the pipeline (producer) and the watcher (consumer).

### 2. Guardrail Checklist
- **C-01 (vault-only writes)** — satisfied: registry performs no FS writes; moves still go through `move_attachment`/`move_folder`.
- **C-12 (Result)** — N/A to private infra helpers (consistent with existing `_debounce`/`_register_pending_folder` which return `None`); no new public pipeline boundary added.
- **C-13 (audit)** — N/A: suppression is the *absence* of an action, not an AI decision. Emit a `logging` skip-line instead (mirrors existing skip logs).
- **C-17 (no module-scope CONFIG in tests)** — satisfied: tests construct `MoveGuard` directly and `VaultConfig(root=tmp_path)`.
- **Logging style (`%s`, not kwargs)** — satisfied in watcher.py edits.
- **Debounce-key uniqueness (CLAUDE.md)** — unaffected: T8 does not add a debounce timer; it adds a pre-move registration + a check inside `on_moved`. The existing `bin:{dst}` vs `str(dst)` keys are untouched.
- **TD-033 (patch the importing module)** — tests patch `vault.watcher`/`pipelines.capture` names.

### 3. Success criteria

**You can verify (vault-visible):**
- Given a no-edit PDF placed in `Projects/A/` while `kms watch` runs, When capture relocates it to `Projects/A/attachment/`, Then the PDF stays at `Projects/A/attachment/<name>.pdf` and its sibling at `Projects/A/attachment/.summaries/<name>.pdf.md` — i.e. it does NOT bounce back out (no re-home).
- Given the same PDF now sitting in `attachment/`, When YOU later drag it into `Domain/D/`, Then it IS re-homed (binary + sibling follow per T6) — proving the guard expired and a genuine user move is still handled.
- Given an editable docx captured into `Projects/A/` (T3 leaves it in root), When the pipeline finishes, Then it stays visible in `Projects/A/` with no spurious move.

**Developer must verify:**
- After a pipeline move, the audit log contains the normal `capture:store / LOCATED` row (or folder `FOLDER_CLASSIFIED`) and **no** `watcher:rehome` row for that path (Option A registry consumed the event).
- Log line: a `watcher.rehome_skip path=<dst> reason=pipeline_initiated` (or equivalent) appears exactly once per suppressed move; the registry entry is gone afterward (consumed-once).
- The registry entry auto-expires: a unit test advancing the clock past TTL confirms `check_and_consume` returns False after expiry even if `register` was never consumed (crash-safety).
- **Non-interference (observer thread ↔ pipeline thread):** a test that registers from a pool/loop thread and checks from the observer thread under contention shows no race (lock held); registry never raises `RuntimeError: dictionary changed size during iteration`.
- **Non-interference (T8 guard ↔ existing binary sibling-sync):** the guard suppresses ONLY the T6 re-home branch; `_handle_binary_move` sibling sync (watcher.py:344) must still run for genuine user moves — verify a user move both re-homes AND syncs the sibling, while a pipeline move does neither re-home nor a *spurious* sibling orphan.

### 4. Options

#### Option A — Standalone `MoveGuard` module, injected into the watcher, bound as a shared singleton at `watch` wiring (Recommended)
- **What this means (plain English).** A tiny new file owns the "sticky notes": a dict of `path → expiry timestamp` behind a lock, with `register(path, ttl)` and `check_and_consume(path) -> bool` (consume-once, lazily dropping expired entries on every call). The watcher holds one instance; the same instance is reachable by the pipeline so a move it makes leaves a note the watcher reads.
- **Approach.** Add `src/vault/move_guard.py`. `VaultWatcher.__init__` creates a `MoveGuard` and passes it to `_VaultEventHandler`. The pipeline reaches the *same* instance via a module-level accessor set during `watch()` wiring (cli/main.py) — e.g. `vault.move_guard.set_active(guard)` / `get_active()`; the pipeline calls `get_active()` and, if non-None (i.e. running under `kms watch`), calls `register(dst)` immediately before `move_attachment`/`move_folder`. In the watcher, the T6 re-home branch calls `guard.check_and_consume(dst)`; True → skip re-home.
- **Files touched.** New `src/vault/move_guard.py`; `src/vault/watcher.py` (ctor wiring + one check inside `on_moved`'s T6 branch); `src/pipelines/capture.py` (3 register sites: L658, L711, L1297); `src/cli/main.py` (bind the active guard, watch:253-260).
- **Cost.** Dev: low (~40 lines + 3 one-line register calls + wiring). Runtime: negligible (dict + lock, no timers — expiry is lazy). Maintenance: low; one obvious place for the rule.
- **Risk.** Low. Main subtlety: the pipeline must register the *final* dst path (NFC-normalised, matching the watchdog event path) or the check misses. Mitigation: register the exact `attachment_dst`/`inbox_dst`/folder `destination` Path objects the move uses.
- **Module depth.** Real, shallow seam; passes deletion test (delete → watcher fights pipeline). Two real adapters now (producer pipeline, consumer watcher) — not speculative.
- **Defers.** Nothing T8 owns. Leaves T6's re-home branch to be the consumer.
- **Constraints check.** C-01 ✓ (no writes), C-13 N/A (log not audit), C-17 ✓, TD-033 ✓ (patchable as `vault.watcher.MoveGuard` / `vault.move_guard.get_active`). No threshold/prompt involvement (C-06/C-07 N/A).

#### Option B — Inline registry inside `_VaultEventHandler` (no new module), pipeline reaches it via a watcher reference
- **What this means.** No new file: add `self._move_guard: dict[str, float]` + a `threading.Lock` directly in `_VaultEventHandler.__init__`, alongside the existing `_timers` and `_pending_folders` registries. Add `register_pipeline_move(path)` and a private check used in `on_moved`. The pipeline reaches the handler instance the same way (a module-level accessor set at `watch` wiring).
- **Approach.** Mirror the existing pending-folder registry pattern (watcher.py:114-122, 182-249) — same lock discipline, same module.
- **Files touched.** `src/vault/watcher.py` (registry + methods + check), `src/pipelines/capture.py` (3 register sites), `src/cli/main.py` (expose the handler/registry to the pipeline).
- **Cost.** Dev: low. Runtime: negligible. Maintenance: medium — watcher.py already carries two registries; a third grows the file's surface and couples the TTL logic to the handler, making it harder to unit-test in isolation (must construct a full handler with 6 callbacks).
- **Risk.** Low-medium. Exposing the live handler to the pipeline is a wider coupling than exposing a small guard object; easy to accidentally let the pipeline call other handler internals.
- **Module depth.** No new boundary. Shallower than A but the registry is *not* independently testable; deletion test still passes (same behavior) but the seam is muddier.
- **Defers.** Same as A.
- **Constraints check.** Same as A (C-01 ✓, C-13 N/A, C-17 ✓). Slightly worse on testability/isolation.

#### Option C — No registry; distinguish AI-move from user-move by checking "is the sibling/DB already consistent" (state-derived)
- **What this means.** Skip a registry entirely. In the watcher's re-home branch, decide whether the pipeline already did the bookkeeping by inspecting state — e.g. the sibling already exists at the new location and the DB row already points there, or the binary's `source_hash` matches an indexed sibling at the destination.
- **Approach.** In `on_moved`'s T6 branch, query `documents` (`get_by_path`) for the destination sibling; if present and consistent, assume pipeline-initiated and skip.
- **Files touched.** `src/vault/watcher.py` only (no pipeline/CLI changes).
- **Cost.** Dev: medium (more query logic in the handler). Runtime: a DB read per move. Maintenance: medium-high.
- **Risk.** **High — this is explicitly an anti-goal** (draft L288: "Do not rely solely on 'already in DB' (racy; can't distinguish AI-just-moved from user-moved-an-indexed-file)"). A user moving an already-indexed file would look identical to a pipeline move, so legitimate user re-homes get silently dropped. Also racy against the debounce window.
- **Module depth.** No new boundary, but adds branching logic to the handler (closer to C-14's spirit of keeping handlers thin, though C-14 is MCP-specific).
- **Defers.** Nothing — but it fails the "user move still handled" Done-when.
- **Constraints check.** Violates the task anti-goal; ⚠️ not acceptable as the primary mechanism.

### 5. Recommendation
**Option A.** It is the only option that gives the suppression rule its *own* small, lock-guarded, independently unit-testable home shared cleanly across the observer thread and the pipeline thread-pool — at the cost of one new ~40-line file. That isolation is worth it precisely because this is concurrency code (the riskiest kind to bury inside an already-busy handler), and because T6 will be the lone consumer of a stable two-method interface (`register` / `check_and_consume`). Option B saves a file but tangles TTL concurrency logic into a handler that already juggles two registries and can't be tested without a full 6-callback construction; Option C is ruled out by the draft's own anti-goal.

### 6. Cross-check
- **Scope creep removed.** T8 does NOT implement re-home (that is T6) — it only adds the registry + the *call to* `check_and_consume` in the branch T6 will own. The register calls wrap exactly the three binary/folder move sites named in the diagnosis; no other move site touched.
- **Constraint violations.** None for Option A. ⚠️ Option C violates the draft anti-goal (DB-only distinction is racy) — flagged, not chosen.
- **Existing binary sibling-sync interaction (important nuance).** Today `on_moved` already runs `_handle_binary_move` (sibling sync) for *every* internal binary move, including the pipeline's own moves. The pipeline (capture.py:633-650) writes the sibling at the **final** destination *before* it moves the binary (DECISION-025 sibling-first), so when the pipeline move event later reaches the watcher, `_handle_binary_move`'s `same_folder`/`old_sibling.exists()` logic (watcher.py:350, 357) generally finds nothing to rename and orphans nothing meaningful — but this is fragile. **T8 should suppress ONLY the T6 re-home branch**, NOT the existing `_handle_binary_move`, to keep this task surgical. If, during T6, the re-home subsumes sibling-sync, revisit whether the guard should also gate `_handle_binary_move` for pipeline moves. Flag for T6: `[REQUIRES: T6]` decide whether the guard wraps re-home only, or re-home + sibling-sync.
- **Tech-debt items.** Neutral on TD-030 (on_deleted/on_moved ordering) — T8 adds a check *after* the binary-sync dispatch in the re-home branch, preserving the TD-030 ordering invariant; do not reorder. Touches TD-033 posture positively (guard is patchable at `vault.watcher`/`vault.move_guard`). Does not address TD-037 (that is T9).
- **DECISION conflicts.** None. Consistent with DECISION-025 (sibling-first), DECISION-029 (`type=attachment-summary` unaffected). Composes with T3 (editable→root): T3's `move_attachment` into root for editable files would, under T6, look like a re-home candidate; T8's register at that site is what prevents the bounce — confirm T3's move site (it reuses capture.py:658 `move_attachment` in the LOCATED branch) is wrapped by the register call. `[REQUIRES: T3]` the single LOCATED move site must register its dst regardless of editable/no-edit.
- **Labels.** `[REQUIRES: T6]` (sole consumer of `check_and_consume`); `[REQUIRES: T3]` (its move site must register); build order: T8 ships in the same increment as T6 (L421).

---

# T6 — Watcher: re-home on user move (T11 correlation_id verify folds in)

## Implications

**Plain English — what "re-home" must do.** Today, when the user drags a captured file from `Projects/A/` to `Projects/B/`, the watcher does the dumb thing: it deletes the old AI-written summary card and walks away. The file's summary, its project tag, and its database record are all left wrong or gone. T6 changes that one case: a user move should *carry the summary along* — recreate the summary card next to the file's new home, fix the project/domain tags, move the binary into the right sub-folder for its type (visible root for editable office docs, hidden `attachment/` for read-only PDFs/images), and update the database — **without ever calling the AI** (the content did not change, so re-summarizing would be wasted money and time). That is "re-home". It is distinct from "re-capture" (full AI re-run, which only T9 does, only on a content edit).

**Which terms map to what in this codebase.**
- The move handler is `_handle_binary_move(src, dst)` (`src/vault/watcher.py:344-449`). It already has two branches: a **same-folder** branch (L357-419, a pure rename — sibling rename + pointer update + DB `rename` + audit `ATTACHMENT_MOVED`) and a **cross-folder/else** branch (L420-449) which is the orphan-and-do-nothing path. **T6 rewrites the else branch into the re-home path.** The same-folder branch is already correct and should stay (it is a rename in place, not a re-home).
- "Sibling" = the AI summary card. Naming rule is `_sibling_for(binary, vault_config)` → `<binary parent>/<summaries_subdir>/<binary.name>.md` (`watcher.py:54-73`). Note it anchors to the binary's *current* parent — for re-home we must compute the **new** sibling from the binary's **final** parent (which T2's `resolve_placement` gives us), not from `dst.parent` directly, because an editable file's final parent is the project root while a no-edit file's final parent is `attachment/`.
- "Summary lookup via DB" (anti-goal: do NOT read the source sibling off disk): the summary text lives in two places — the sibling `.md` frontmatter (`summary:` field) and the `documents` row's `summary` column. There is **no `attachment_path` column** in the `documents` table (verified against `src/storage/schema.sql` — columns are `id, vault_path, title, summary, note_type, confidence, created_at, updated_at, updated_by_human, content_hash`, plus `batch_id/project/status/key_topics` added by migrations 002-005; see `DocumentRow` at `documents.py:27-43`). So the reverse lookup is **not** a query on `attachment_path` — it is deterministic: `old_sibling_vp = to_vault_path(_sibling_for(src, cfg))`, then `get_by_path(old_sibling_vp)` (`documents.py:141-170`) returns the `DocumentRow` carrying the summary. This resolves the draft's open question (L237): the schema supports a clean *path-keyed* reverse lookup, not an attachment_path one — and that is sufficient.

**Guards / constraints that apply.**
- **Vault-only writes (C-01).** Every disk mutation must go through `vault/writer.py`. Re-home needs: `move_attachment(src, final_binary_dst)` to relocate the binary, and to materialize the new sibling either `move_note(old_sibling, new_sibling)` (if it exists on disk) or `write_note(new_sibling, body, meta, actor="ai")` (rebuild from the DB row). All three already exist (`writer.py:108,175,241`). No raw `write_text`.
- **Idempotent / updated_by_human gate.** The binary is a blob (no gate — `move_attachment` docstring L245). The sibling is AI-authored; re-home writes it with `actor="ai"`. Per CLAUDE.md, `write_note` sets `updated_by_human` from the actor, so an AI re-home correctly leaves the row AI-owned. We must NOT re-home if the user has hand-edited the sibling — but a user editing a *card* is out of T6's trigger (T6 fires on a binary move, not a sibling edit); still, surfacing rather than overwriting a human-edited sibling is the safe posture (handled by reading the existing row and preserving its summary, which we do).
- **Audit (C-13).** The current orphan branch writes an audit row (L435-449). T6 must keep writing exactly one audit row per re-home, with `outcome="REHOMED"` (new outcome string) and a `reasoning` that states source→dest and editable-vs-no-edit, mirroring the LOCATED enrichment T3 added.
- **Result type.** `delete_by_path`, `rename`, `get_by_path`, `move_attachment`, `move_note`, `write_note` all return `Result[...]`; the rewritten branch must `match` every one and log `%s`-style on `Failure` (stdlib `logging`, per CLAUDE.md — no kwargs).
- **No thresholds, no prompts.** Re-home makes no AI decision and reads no threshold; C-06/prompt-as-config are N/A here.

**T11 folds in (correlation_id).** The working-tree edit already binds `correlation_id` at the top of `_handle_binary_move` (`watcher.py:346-347`: `bind_contextvars(correlation_id=new_correlation_id())`), which lexically encloses the else branch. Because the function runs in one `threading.Timer` thread and `audit_write` reads the contextvar synchronously in that same thread, the bind is in scope for the re-home `audit_write`. **T6 must preserve this bind verbatim and must not add a second bind.** The only T6 obligation for T11 is: the re-home audit row inherits the existing bind — verify with the cross-folder move test below (no `missing correlation_id` warning). No code change beyond preservation.

**Files touched.**
- *Directly:* `src/vault/watcher.py` — rewrite the else branch of `_handle_binary_move` (L420-449); add imports for `resolve_placement`/`Placement` (T2) and `get_by_path` (`storage.documents`), patched as `vault.watcher.<name>` per TD-033.
- *Indirectly (consumed, not edited):* `src/vault/paths.py::resolve_placement` (T2), `src/storage/documents.py::get_by_path` (existing), `src/vault/writer.py` (existing move/write fns), `src/pipelines/capture.py::apply_location_tags` logic (we reuse the *rule*, not the async stage — see Options).
- *Not touched:* `cli/main.py::on_move` (L241-251) — that callback only renames the **md/user-note** DB row for `_on_move`; the binary path goes through `_handle_binary_move` (the `bin:` debounce, L289-291), which is internal to the watcher and does not call back to the CLI. So re-home does NOT route through `on_move`. Confirmed by the dispatch at `watcher.py:288-296`.

**Downstream effects / runtime deps.** Re-home now performs file moves and a DB rebuild inside a debounce-timer thread. It depends on T2 (`resolve_placement`) being merged (hard dep) and on T8's `MoveGuard` to avoid firing on the pipeline's own moves (hard dep — see build order: never ship T6 without T8). It composes forward to T7 (move-chain): because the summary comes from the DB, not the on-disk source sibling, a coalesced `A→B→C` chain that has already deleted intermediate siblings still finds the summary via the original `documents` row key — provided T7 keys the settle window so only the final hop's `dst` triggers one re-home.

**Module depth.** Shallow change inside an existing function — no new module, no new public interface. It deepens `_handle_binary_move`'s else branch from ~30 lines to ~60. Deletion test: if you delete the re-home logic, you fall back to today's orphan behavior (a real regression for the user, so the code earns its place). No speculative seam is introduced; the only new boundary (`resolve_placement`) is owned by T2 and already has ≥2 adapters (capture T3 + watcher T6), so it is a real seam, not speculative.

## Guardrail Checklist

- **C-01 vault-only writes** — satisfied: re-home uses `move_attachment` + `move_note`/`write_note` only; no `write_text`. (Hard-block hook would catch a violation.)
- **C-13 audit every AI/sync decision** — satisfied: exactly one `audit_write` with `outcome="REHOMED"`, source→dest reasoning; replaces the orphan audit row.
- **Result-type discipline (CLAUDE.md / Architecture)** — satisfied: every `Result`-returning call is `match`ed; failures logged `%s`-style, never swallowed.
- **updated_by_human / idempotent write** — satisfied: sibling rewritten `actor="ai"`; binary blob exempt; we read-and-preserve the existing summary rather than regenerate.
- **TD-033 monkeypatch target** — satisfied: new collaborators imported at `watcher.py` top level and tests patch `vault.watcher.resolve_placement` / `vault.watcher.get_by_path`, not the source modules.
- **TD-030 on_moved ordering** — satisfied: the `bin:` binary-sync dispatch stays before any `_should_skip`; T6 does not reorder `on_moved` (L280-299); the MoveGuard check (T8) lives *inside* the re-home branch, after binary-sync dispatch.
- **stdlib logging, %s-style; vault-relative via `relative_to(self._root)`** — satisfied: reuse the existing `_vp()` local (L352-355) for all path-to-vault-string conversions; do not call the CONFIG-singleton `to_vault_path`.
- **C-06 thresholds-in-config** — N/A (no threshold; TTL lives on T8's guard, not here).
- **T11 correlation_id bind** — satisfied: function-top `bind_contextvars` preserved; re-home audit inherits it in the same Timer thread.

## Success criteria

**You can verify (vault-visible paths/fields):**
1. Given `Projects/A/report.pdf` (a no-edit file) with a summary, When you move it to `Projects/B/`, Then `Projects/B/attachment/report.pdf` exists and `Projects/B/attachment/.summaries/report.pdf.md` appears with the same summary text, and the old `Projects/A/...` sibling is gone.
2. Given `Projects/A/budget.xlsx` (an editable file), When you move it to `Projects/B/`, Then the file ends up at `Projects/B/budget.xlsx` (visible root, NOT inside `attachment/`) and its card appears at `Projects/B/.summaries/budget.xlsx.md` with the summary preserved.
3. Given a file under `Domain/Finance/`, When you move it to `Domain/Ops/`, Then the card's `domain/` tag reads `domain/Ops` (not `domain/Finance`) and the file is homed per its type rule.
4. Given any re-home, When it completes, Then no second/duplicate card exists at the old location and the file's summary card content is unchanged (no LLM rewrite — same wording as before the move).
5. Given a no-edit file moved within the same folder (pure rename), When the rename fires, Then behavior is the today's same-folder rename (card renamed in place) — T6 did not regress it.

**Developer must verify:**
- *DB row:* after re-home, `get_by_path(new_sibling_vp)` returns the row (same `id` preserved if `rename` is used, or a fresh upsert if rebuilt) and `get_by_path(old_sibling_vp)` returns no row; the `summary` column equals the pre-move value.
- *Audit row:* exactly one row, `pipeline="watcher"`, `stage="sync"`, `outcome="REHOMED"`, `action="watcher:binary_rehome"`, with a bound `correlation_id` (T11 verify — no `missing correlation_id` warning in the log).
- *Log line:* on a `MoveGuard`-suppressed move, `watcher.rehome_skip path=%s reason=pipeline_initiated` (T8 path) and NO re-home side effects.
- *Result type:* a `move_attachment` failure (e.g. dst collision) yields a logged `Failure` and leaves the sibling pointer state explainable (accepted DECISION-025 broken-pointer failure mode), not a crash.
- *Non-interference (pipeline ↔ watcher):* a capture-initiated no-edit root→attachment move (pipeline registers dst in MoveGuard) produces ZERO re-home audit rows; a user move of the same file (no registration) produces exactly one.

## Options

### Option A — Minimal: re-home by reusing the same-folder branch's three steps against a recomputed destination, with placement logic inlined
**What this means (plain English):** Copy the existing same-folder rename steps (rename card, fix pointer, rename DB row, audit) but point them at a destination folder we compute right here in the watcher using the editable/no-edit rule written inline.
**Approach:** In the else branch, compute `is_no_edit = dst.suffix.lower() in cfg.no_edit_extensions`, derive the final dir inline, `move_attachment` the binary, `move_note` the old sibling to the new sibling, fix the `attachment_path` pointer, re-derive tags inline, `rename` the DB row, audit.
**Files touched:** `vault/watcher.py` only.
**Cost:** dev low; runtime low (no extra DB read — uses on-disk sibling); maintenance HIGH — duplicates the editable/no-edit rule the project explicitly forbids having twice (T2 anti-goal).
**Risk:** Reads the summary from the on-disk source sibling, which the anti-goal (draft L227) forbids — breaks under T7 coalesced moves where the intermediate sibling is already deleted. Inline rule drifts from T2/T3.
**Module depth:** No new boundary; but it *violates* the single-source-of-truth seam by re-implementing placement.
**Defers:** Nothing; actively creates tech debt.
**Constraints check:** ⚠️ Violates T2 anti-goal (second copy of the rule) and the draft anti-goal of reading the source sibling off disk.

### Option B — DB-driven re-home on top of T2's `resolve_placement` (Recommended)
**What this means (plain English):** Look up the file's existing summary from the database (not from a disk file that might be gone), ask the shared placement helper where the file and its card belong now, move the binary there, write the card there from the DB summary, fix the tags, update the DB, and audit — all without the AI.
**Approach:** In the else branch:
1. `old_sibling_vp = _vp(_sibling_for(src, cfg))`; `row = get_by_path(old_sibling_vp)` → carries `summary`, `note_type`, `confidence`, `key_topics`.
2. `loc_type, loc_name = _location_context(dst, cfg)` (paths.py:87) → new project/domain.
3. `placement = resolve_placement(dst, loc_type, loc_name, cfg)` (T2) → `final_dir`, `sibling_dir`, `needs_move`. Final binary path = `final_dir / dst.name`; new sibling = `sibling_dir / f"{dst.name}.md"`.
4. `move_attachment(dst, final_binary)` only if `final_binary != dst` (T2 routes the binary to root or attachment per type).
5. Rebuild card body from the row's summary (reuse the same `type="attachment-summary"` + `domain/<loc>` tag + `attachment_path` + `source_hash` metadata shape T3 writes; source_hash copied from the existing sibling row or recomputed from bytes), `write_note(new_sibling, body, meta, actor="ai")`.
6. `delete_by_path(old_sibling_vp)` then upsert the new sibling outcome (or `rename` then patch — see open detail below), audit `REHOMED`.
7. Wrap with T8: `g = get_active(); if g and g.check_and_consume(final_binary): log rehome_skip; return` placed at branch entry, AFTER the binary-sync dispatch ordering is preserved.
**Files touched:** `vault/watcher.py` (rewrite else branch + 2 top-level imports).
**Cost:** dev medium; runtime +1 DB read (`get_by_path`) per cross-folder move — negligible for a single human user; maintenance LOW (one placement rule, shared).
**Risk:** Low. Edge: if the DB row is missing (file never captured / index drift) the re-home has no summary to carry — fall back to orphan-only behavior + a `not_in_index` warning (matches today's L426-429 posture). The `move_attachment` "dst must not exist" guard means a collision at the destination is a logged `Failure`, not a crash (acceptable; T3 owns the collision-loop, but re-home of a single move into a fresh folder rarely collides — if it does, log and leave the card pointing at the prior path, DECISION-025 broken-pointer posture).
**Module depth:** Shallow; reuses the T2 seam (real, 2+ adapters). Deletion test passes (removing it regresses to orphan).
**Defers:** Move-chain convergence (T7) and the root-`.summaries/` recognition in the two near-twin predicates ([REQUIRES: T10]).
**Constraints check:** Satisfies C-01, C-13, Result, TD-030, TD-033, T11. Honors T2 single-source rule and the "DB not disk" anti-goal.

### Option C — Re-home via a tiny reusable `rehome_binary()` helper extracted into `vault/` (new function), called by both watcher and a future reconcile (T10)
**What this means (plain English):** Same logic as Option B, but pulled out into its own named function so the watcher and the future migration command (T10) can both call one re-home routine.
**Approach:** Add `rehome_binary(src_or_indexed: Path, new_loc, cfg, *, actor="ai") -> Result[...]` to a vault module; `_handle_binary_move`'s else branch becomes a thin caller. T10 reconcile Stage 5 reuses it.
**Files touched:** new helper in `vault/` (e.g. `vault/rehome.py`), `vault/watcher.py` (call it), and prospectively `cli`/reconcile (T10).
**Cost:** dev medium-high; runtime same as B; maintenance LOW-to-MEDIUM (one routine, but a new module to own).
**Risk:** Premature abstraction risk — T10 is a *batch sweep* over existing-editable-in-attachment files and may want different inputs (it starts from an indexed binary already at rest, not a move event). Forcing both through one signature now is speculative until T10's exact shape is settled. The seam may be real (watcher + reconcile = 2 adapters) OR speculative if T10 ends up reusing `resolve_placement` directly instead.
**Module depth:** New boundary; deletion test is borderline — until T10 lands, only one caller exists, so the seam is speculative *now*.
**Defers:** Same as B, plus defers proving the seam until T10.
**Constraints check:** Same as B. ⚠️ Mild Extension-Point tension: a single-use abstraction (CLAUDE.md §3 "no abstractions for single-use code") until T10 actually consumes it.

## Recommendation
**Option B.** It reuses T2's `resolve_placement` as the single source of placement truth (no forbidden second copy of the editable/no-edit rule) and sources the summary from the `documents` row rather than a disk sibling that may already be gone — exactly what the T7 move-chain composition needs — at the cost of one extra `get_by_path` read per move, which is free for a single human user. Option C's shared helper is the right *eventual* shape but is single-use until T10, so extracting it now is speculative; if T10 confirms the need, promote B's branch body into a helper then.

## Cross-check

**Scope creep removed:** T6 does NOT touch the same-folder rename branch (already correct), does NOT touch `cli/main.py::on_move` (binary moves never route there), does NOT modify the two near-twin predicates (root-`.summaries/` recognition is T10), does NOT build the move-chain settle window (T7) or the suppression registry (T8 — T6 only *consumes* `get_active().check_and_consume`).

**Constraint violations flagged:** None in Option B. ⚠️ Option A violates the T2 anti-goal (duplicate rule) and the draft anti-goal (read source sibling off disk) — rejected. ⚠️ Option C carries a mild single-use-abstraction tension (CLAUDE.md §3) until T10.

**Tech-debt items touched:**
- TD-033 (monkeypatch target) — **neutral/honored**: new imports at module top, tests patch `vault.watcher.*`.
- TD-030 (on_moved ordering) — **neutral/honored**: binary-sync dispatch stays first.
- T11 / correlation_id-on-orphan-branch — **retired**: the rewritten branch inherits the function-top bind already present in the working tree; verified by the cross-folder move test. No new debt.
- No new TD introduced; the missing-row fallback is logged, not silently swallowed.

**Contradicts any DECISION-NNN?** No. Preserves DECISION-025 (sibling-first ordering + broken-pointer failure posture), DECISION-029 (`type=attachment-summary` on the rebuilt card, incl. root `.summaries/`). Consistent with T2/T3/T8 settled decisions.

**[REQUIRES:] labels:**
- [REQUIRES: T2] hard — `resolve_placement(file_path, target_type, target_name, vault_cfg) -> Placement(final_dir, sibling_dir, needs_move)` must exist and be importable.
- [REQUIRES: T8] hard — `vault.move_guard.get_active()` + `MoveGuard.check_and_consume(path)`; re-home must skip pipeline-initiated moves. Ship T6 + T8 in the same increment.
- [REQUIRES: T10] reconcile must recognize root-level `.summaries/` for the editable case (the two near-twin predicates do not yet); T6 only *writes* there.
- [REQUIRES: T3] T3's LOCATED move site must be a MoveGuard-registered site (so re-home doesn't fight capture).

---

# T7 — Move-chain convergence (settle window)

## 1. Implications

**In plain English.** When the executive drags a file to the wrong project and then drags it again to the right one a few seconds later (the "dropped wrong, fixed it" pattern, `A→B→C`), the watcher today would react to *each* hop. T6 makes each hop trigger a full re-home: relocate the AI summary, re-derive the project/domain tags, and move the binary. Doing that three times means the AI builds and then orphans summary files at B before finally settling at C — wasted work, a brief window where the summary lives in the wrong place, and a risk of a stray leftover `.md`. T7 adds a short "settle window": after a binary moves, the watcher waits a couple of seconds; if another move of the same file arrives, it cancels the pending re-home and restarts the clock. Only when the file stops moving does it re-home once, to the final location C.

**What the key terms mean in this codebase.**
- *Re-home* (T6) = relocate sibling + re-derive location tags + move the binary per the editable/no-edit rule, **reusing the existing DB-stored summary** (no LLM). This is the expensive operation T7 is coalescing.
- *Settle window* = a debounce timer scoped to a single binary's move-chain, modelled on the existing **folder cooldown** (`_register_pending_folder`/`_reset_folder_timer`/`_fire_folder_stable`, `vault/watcher.py:182-249`).
- *Stable identity key* = the value used to recognise "this is the same file moving again." The draft's recommended choice is the **filename** (`dst.name`); the alternative is a content hash.
- *Token guard* = the C2 stale-fire defense already in `_fire_folder_stable` (`watcher.py:229-249`): each timer carries a monotonically-increasing token; a fired timer is a no-op unless its token still matches the stored one. This is exactly the mechanism that makes "a later move supersedes an earlier pending re-home" safe.

**Why a plain `_debounce` is insufficient.** `_debounce` (`watcher.py:151-159`) already cancels-and-restarts per key, and the binary-move sync already debounces under the `bin:{dst}` key (`watcher.py:289-291`). But the debounce key today is keyed on the **destination** path (`bin:{dst}`), so `A→B` debounces under `bin:B` and `B→C` debounces under `bin:C` — two *different* keys, which do **not** coalesce (CLAUDE.md gotcha: "different keys do NOT coalesce"). So the current `bin:{dst}` debounce coalesces repeated events landing at the *same* destination, but does NOT coalesce a chain that lands at successively different destinations. T7's whole reason to exist is that the chain ends at different paths, so the identity key must be the **stable file identity** (filename), not the destination path.

**Critical composition with T6 (the consumer).** T6 rewrites the cross-folder branch of `_handle_binary_move` (`watcher.py:420-449`) to do the re-home. T7 must insert its settle window **between** the `on_moved` dispatch and the re-home firing — i.e. T7 wraps/gates the call into T6's re-home, it does not change re-home mechanics (explicit anti-goal: "OUT: the re-home mechanics themselves (T6)"). The two ship in the same chain (build order L420: `T8 → T6 → T7`).

**Critical composition with the DB-based summary lookup.** The headline correctness risk of coalescing is *losing the summary*. T6 already fixed this: re-home sources the summary from the **DB row** via `get_by_path(to_vault_path(_sibling_for(src, cfg)))`, "never the on-disk source sibling (composes with T7 coalesced moves)" (T6 decision). This is the load-bearing fact for T7: because the intermediate sibling at B may never be created (or may be deleted by an intermediate sync), the final re-home at C must look up the summary by the **original source identity**, not by reading a sibling next to the immediate `src`. T7's design must preserve enough state to do that DB lookup once at the end — see Options.

**Critical composition with T8 (the suppression guard).** T8's `MoveGuard.check_and_consume` suppresses re-homes that the *pipeline* initiated. T6 puts that gate "INSIDE the re-home branch entry … after binary-sync dispatch (TD-030 ordering preserved)." T7's settle window sits in front of that, so the ordering becomes: binary-sync dispatch (TD-030, must stay first) → settle/coalesce → on fire, T8 guard check → re-home. T7 must NOT move the T8 check earlier or it could consume a guard token on an intermediate hop that gets superseded.

**Files touched.**
- *Directly:* `src/vault/watcher.py` — new pending-binary-move registry helpers (mirroring `_register_pending_folder` et al.) and a rewrite of the `bin:{dst}` dispatch in `on_moved` (`watcher.py:288-291`) to register-into-the-settle-window instead of debouncing directly into `_handle_binary_move`'s cross-folder branch. The same-folder rename branch (`watcher.py:357-419`) and the binary-delete dispatch (`on_deleted`, `watcher.py:271-274`) are out of scope.
- *Directly (config, if dedicated key chosen):* `src/core/config.py` `CaptureConfig` (add `binary_settle_seconds`, `watcher.py:237` neighbourhood) and `src/config/config.yaml` (`capture.binary_settle_seconds`, near `folder_cooldown_seconds` L7).
- *Directly (wiring):* `src/vault/watcher.py` `VaultWatcher.__init__` (`watcher.py:465-503`) to thread the new setting through to `_VaultEventHandler`; `src/cli/main.py` `watch()` (`main.py:253-260`) to pass it from `CONFIG.main.capture`.
- *Indirectly:* T6's re-home path is the callback the settle timer fires; no code change to T6 mechanics, only to *who calls them and when*.

**Downstream effects.** None outside the watcher's move path. No DB schema change. No new public pipeline function. The audit log gains nothing new — T7 should produce **exactly one** `REHOMED` audit row (T6's new outcome), down from N rows for an N-hop chain; that is a fidelity improvement, not a new audit type.

**Runtime deps.** No new third-party dependency. Reuses `threading.Timer`, `threading.Lock`, `unicodedata.normalize` already imported in `watcher.py`. The settle registry is in-process state on `_VaultEventHandler`, same as `_pending_folders`/`_folder_tokens`.

**Module depth (deletion test).** The settle window is a *shallow* internal mechanism, not a new module or public interface. It is three private helpers + two dicts + a lock on an existing class — symmetric with the folder-cooldown helpers that already live there. It passes the deletion test in the sense that removing T7 reverts cleanly to T6's per-hop behavior (correct but churny); it does not introduce a speculative seam or a new adapter boundary. No new `Protocol`/`ABC` is warranted.

## 2. Guardrail Checklist

- **C-01 (vault-only writes via writer):** All disk effects of the eventual re-home go through T6's `move_attachment`/`move_note`/`write_note`. T7 itself writes nothing to the vault — it only schedules/cancels timers. ✔ satisfied trivially.
- **C-13 (audit log non-negotiable):** The coalesced re-home still emits T6's single `REHOMED` audit row. T7 must NOT emit an audit row for a *superseded* (cancelled) hop — a cancelled pending re-home represents no AI decision, so no audit. Net effect: one audit row per settled chain. ✔
- **C-06 (no hardcoded thresholds in `pipelines/`):** C-06 is scoped to confidence floats in `if/elif` inside `pipelines/`. The settle window lives in `vault/watcher.py`, not `pipelines/`, and is a *duration* not a *confidence threshold*, so C-06 does not bind. Nonetheless, mirror T8's posture: keep the duration on config (`binary_settle_seconds`) / the handler attribute, never a bare literal buried in a branch. ✔ (recommended option uses config).
- **C-17 (no module-scope CONFIG import in tests):** New tests construct `_VaultEventHandler`/`VaultWatcher` with an explicit `vault_config=VaultConfig(root=tmp_path)` and explicit settle-seconds; never import `CONFIG`. ✔
- **TD-033 (patch the importing module):** Tests monkeypatch `vault.watcher.<name>` (e.g. `vault.watcher.get_by_path`, `vault.watcher.resolve_placement`, `vault.watcher.move_attachment`), never the source modules. ✔
- **TD-030 ordering (binary-sync dispatch before re-home logic):** T7 must keep the `_is_binary(src) and self._is_internal(src)` dispatch in `on_moved` (`watcher.py:288-291`) ahead of any settle-window logic, and must not let the settle timer run *before* the sibling-sync side of `_handle_binary_move`. Concretely the settle window gates only the **cross-folder re-home** firing, not the sync dispatch. ✔ (see Option B mechanics).
- **C2 token-guard (CLAUDE.md "stale fire" rule):** T7 reuses the exact token pattern from `_fire_folder_stable` (`watcher.py:241-243`) so a stale settle-timer that fires after a newer move was registered is a no-op. ✔ This is the core correctness guarantee for "single re-home at final location."
- **`%s`-style stdlib logging:** `_log` is `logging.getLogger(__name__)` — all new log lines use `%s` placeholders, not kwargs (CLAUDE.md gotcha). ✔
- **NFC path normalisation:** Filename-keyed identity must normalise (`unicodedata.normalize("NFC", ...)`) so the key matches watchdog event paths across the chain (same rule the rest of the file follows). ✔

## 3. Success criteria

**You can verify (vault-visible only):**
1. *Given* a no-edit binary captured under `Projects/A/attachment/report.pdf` with its summary at `Projects/A/attachment/.summaries/report.pdf.md`, *when* I move `report.pdf` to `Projects/B/` and then within ~2s move it again to `Projects/C/`, *then* after the settle window exactly one summary file exists, at `Projects/C/attachment/.summaries/report.pdf.md`, and no `.summaries/report.pdf.md` remains under A or B.
2. *Given* the same chain, *when* it settles, *then* the binary itself ends at `Projects/C/attachment/report.pdf` (no-edit rule) — one copy, none left at A or B.
3. *Given* an editable binary `plan.xlsx` (root-visible), *when* I move it `Projects/A → Projects/B → Projects/C` quickly, *then* exactly one copy ends at `Projects/C/plan.xlsx` and its summary at `Projects/C/.summaries/plan.xlsx.md`.
4. *Given* a single move `Projects/A → Projects/B` (no follow-up), *when* the settle window elapses, *then* the binary and its summary appear under B exactly as T6 would produce them with no extra delay-induced loss (the window adds latency, not behavior change, for the single-hop case).
5. *Given* I move `report.pdf` from A to B, *then* its sibling summary content (the AI-written body) at B is byte-for-byte the original summary — no re-summarisation occurred (re-home reuses the DB summary).

**Developer must verify:**
- **Audit row:** Exactly one `REHOMED` audit row (action `watcher:binary_rehome`, pipeline=`watcher`, stage=`sync`) is written for the whole `A→B→C` chain — assert count == 1, not 3. Superseded hops write no audit row.
- **DB row:** Exactly one `documents` row for the summary after settle, with `vault_path` = the C-anchored `.summaries/<name>.md`; zero rows pointing at A or B siblings (assert `get_by_path` for the A/B sibling vp returns no row).
- **Result type:** The settle-timer callback path matches every `Result` from T6's `move_*`/`get_by_path` (no unhandled `Failure`); the missing-row fallback logs `not_in_index` and orphan-only without crashing.
- **Log line:** A superseded hop logs a stale-fire/no-op trace (e.g. `watcher.binary_settle_superseded key=%s`) and the final fire logs the single re-home; assert N-1 supersede lines + 1 fire line for an N-hop chain.
- **Non-interference (pipeline vs watcher):** When the pipeline itself moves a no-edit binary root→`attachment/` during capture (T8 registered the dst), the settle window fires once and T8's `check_and_consume(final_binary)` returns True → re-home skipped, `watcher.rehome_skip reason=pipeline_initiated` logged, no audit row. (One criterion for the pipeline↔watcher actor pair.)
- **Non-interference (user-move vs sibling-sync):** TD-030 — the `bin:` sibling-sync dispatch still fires per hop for delete/orphan bookkeeping; assert the same-folder rename branch and binary-delete path are unaffected by the settle window.

## 4. Options

### Option A — Reuse the existing `folder_cooldown_seconds` + a single new pending-binary registry, no dedicated config key
**What this means (plain English).** Add a settle window for moving files that behaves like the existing folder cooldown, but borrow the folder cooldown's *duration setting* rather than adding a new one. One fewer knob to explain to the user.
**Approach.** New `_register_pending_move(identity_key, src, dst)` / `_reset_move_timer` / `_fire_move_stable(identity_key, src, dst, token)` helpers on `_VaultEventHandler`, copied structurally from `_register_pending_folder` et al. (`watcher.py:182-249`) with the token guard. `on_moved` (`watcher.py:288-291`) registers into this instead of debouncing straight into the cross-folder re-home; the same-folder sibling rename and binary-delete dispatch stay as-is. Timer duration = `self._folder_cooldown`.
**Files touched.** `src/vault/watcher.py` only (new helpers + `on_moved` rewire); no config change, no `cli/main.py` change.
**Cost.** Dev: low (pattern copy). Runtime: one extra timer per move-chain; negligible memory. Maintenance: low code, but **conceptual coupling** — folder-capture cooldown and binary-settle now share one number; tuning one perturbs the other.
**Risk.** Medium-low. Overloading `folder_cooldown_seconds` is a semantic smell (the draft explicitly recommends *against* it: "Recommend a dedicated key"). If someone later tunes folder capture they silently change move convergence.
**Module depth.** Shallow; passes deletion test (revert → T6 per-hop). No new interface.
**Defers.** A dedicated tuning knob (would need a follow-up config add).
**Constraints check.** C-13/C-01/C-17/TD-030/C2-token all satisfiable. Mild violation of the draft's own recommendation (dedicated key), not a hard constraint.

### Option B — Dedicated `binary_settle_seconds` config + pending-binary registry keyed on filename (Recommended)
**What this means (plain English).** Same settle-window mechanism as A, but with its own labelled setting so the user/dev can tune "how long to wait after a file stops moving before filing it" independently of folder-drop cooldown. The file's *name* is the identity that ties the chain together.
**Approach.** As Option A's helpers, plus: add `CaptureConfig.binary_settle_seconds: float = Field(5.0, ge=0.0)` (`config.py:237` area) and `capture.binary_settle_seconds: 5.0` in `config.yaml` (near L7); thread it `cli/main.py watch()` → `VaultWatcher.__init__` → `_VaultEventHandler`. Identity key = `unicodedata.normalize("NFC", dst.name)`. The registry stores, per key, the timer + token + the **original `src` of the first hop** (so the final fire can do the DB summary lookup against the chain's true origin, not the immediate `src`). On fire, call T6's re-home with `(origin_src_or_db_identity, final_dst)`; T6 looks up the summary via `get_by_path(_sibling_for(origin, cfg))`. Token guard makes a superseded hop a no-op.
**Files touched.** `src/vault/watcher.py`, `src/core/config.py`, `src/config/config.yaml`, `src/cli/main.py`.
**Cost.** Dev: low-moderate (pattern copy + 4-file wiring). Runtime: one timer per chain; one DB lookup at the end (not per hop). Maintenance: low; a clearly-named knob is self-documenting.
**Risk.** Low. **Known edge (accepted):** if two *different* files with the *same name* move concurrently, the filename key coalesces them and one re-home could be attributed to the wrong file. For a single sequential human executive this is acceptable (draft OQ recommendation: "For a single sequential human user this is acceptable; confirm" — confirmed by settled requirement "single sequential human user"). Documented limitation, not a blocker.
**Module depth.** Shallow; symmetric with folder cooldown; deletion test passes. No speculative interface.
**Defers.** Content-hash identity (Option C) — left as a future hardening hook if multi-user lands.
**Constraints check.** C-13 (one audit row), C-01 (writes via T6/writer), C-06 (duration on config, not a pipeline literal), C-17/TD-033 (explicit VaultConfig in tests, patch `vault.watcher.*`), TD-030 (sync dispatch stays first), C2-token (stale-fire guard copied). All satisfied.

### Option C — Content-hash identity key (collision-proof settle window)
**What this means (plain English).** Instead of using the filename to recognise "same file moving again," compute a fingerprint of the file's *contents* so even two same-named files never get confused.
**Approach.** As Option B but the registry key = a hash of the file bytes (e.g. first-N-bytes + size, or full SHA over the binary). Requires reading the file on each move event to compute/confirm the key.
**Files touched.** Same four as B, plus a hashing helper (likely in `vault/paths.py` or a small util).
**Cost.** Dev: moderate (hashing + read-error handling — Office files may be locked mid-save on some platforms, TD-039). Runtime: **a file read per move event** (the draft flags this cost explicitly). Maintenance: higher — read failures, partial files, locked files all need handling.
**Risk.** Medium. Reading a binary that is mid-move/locked can fail or return partial bytes, producing an unstable key that *breaks* coalescing — the opposite of the goal. On macOS (the June target) this is mostly fine, but it adds a failure mode for zero benefit given the single-user requirement.
**Module depth.** Adds a real-ish helper but solves a problem we don't have (concurrent same-name moves). Borderline speculative; fails the "real seam / 2+ adapters" test — there is one consumer and one use.
**Defers.** Nothing; over-builds.
**Constraints check.** Same constraints satisfiable, but introduces a read path that fights TD-039 (Windows file-lock) prematurely. ⚠ Speculative complexity vs. settled single-user requirement.

## 5. Recommendation

**Option B — dedicated `binary_settle_seconds` + filename-keyed pending-binary registry with the C2 token guard.** The single tradeoff: it accepts a theoretical same-name-concurrent-move collision (irrelevant for one sequential human user) in exchange for reusing the exact, already-hardened folder-cooldown token-guard pattern with zero new read-per-event cost and a self-documenting tuning knob. Content-hashing (Option C) buys collision-immunity we don't need and adds a file-read failure mode that collides with TD-039; sharing `folder_cooldown_seconds` (Option A) saves one config line but couples two unrelated timings against the draft's own recommendation.

## 6. Cross-check

- **Scope creep removed.** T7 does NOT touch re-home mechanics (T6), the same-folder rename branch (`watcher.py:357-419`), binary-delete (`on_deleted`), the T8 guard internals, or any pipeline file. It only adds a settle window in front of T6's cross-folder re-home and a config knob. The `bin:{dst}` sibling-sync dispatch (TD-030) is preserved verbatim.
- **Constraint violations flagged.** None for Option B. ⚠ Option C would prematurely entangle with TD-039 (Windows file-lock on read) and adds speculative depth — flagged, not chosen. ⚠ Option A mildly violates the draft's "dedicated key" recommendation — flagged, not chosen.
- **Tech-debt items.** Neutral on TD-037 (binary-modify re-capture, T9), TD-039 (Windows), TD-029/TD-028. Does NOT worsen any. The OQ "identity key choice: filename vs content-hash" is **resolved** in favour of filename (single-user requirement), which retires that consolidated OQ (L402). The OQ "reuse `folder_cooldown_seconds` or add a dedicated key" (L269) is **resolved** in favour of a dedicated `binary_settle_seconds` key.
- **DECISION-NNN contradictions.** None. Composes with DECISION-025 (sibling-first ordering, owned by T3/T6), DECISION-029 (`type=attachment-summary` on root + attachment siblings, owned by T3/T6 — T7 writes nothing so it cannot violate it), TD-030 (sync-before-skip ordering, preserved), TD-033 (patch importing module, honored in tests).
- **[REQUIRES: ...] labels.**
  - **[REQUIRES: T6]** — sole consumer; T7's settle timer fires T6's re-home. Ship in the same increment (build order L420: `T8 → T6 → T7`).
  - **[REQUIRES: T8]** — T8's `check_and_consume` must run inside T6's re-home entry; T7 must place the settle window *before* that check so a superseded hop never consumes a guard token.
  - **[REQUIRES: T2]** (transitively, via T6) — final re-home calls `resolve_placement(final_dst, ...)`; T7 only supplies the settled `final_dst` and the chain-origin identity for the DB lookup.

---

# T10 — Reconcile migration stage (existing editable-in-attachment)

## 1. Implications

**Plain English.** Today every captured binary — including Word/Excel/PowerPoint files the user actually edits — is buried inside the hidden `attachment/` folder. Tasks T2/T3 fix this *going forward* (new captures route editable files to the visible project/domain root). But files captured by the *old* pipeline are already sitting in `attachment/` and will never pass through capture again. This task adds a one-shot, on-demand sweep — `kms reconcile` — that walks the vault, finds editable files stranded in `attachment/`, and pulls each one out to its project/domain root, dragging its AI summary and database record along so nothing is left dangling.

**What the key terms mean in THIS codebase:**
- *Editable file* = a non-`.md` file whose extension is NOT in `vault_cfg.no_edit_extensions` (T1). PDFs and images stay in `attachment/`; docx/xlsx/pptx do not.
- *Sibling* = the AI-written `.md` summary for a binary, named `<binary.name>.md` (full filename incl. extension — see `_sibling_for`, capture.py). For an editable file the sibling's correct home becomes the root-level `.summaries/` (T2/T3 decision: `Projects/<A>/.summaries/<name>.md`), not `attachment/.summaries/`.
- *Migration / re-home* = relocate binary per the type rule, move the sibling, re-point the sibling's `attachment_path` frontmatter, and fix the DB row — all WITHOUT calling the LLM (the summary is reused). This is exactly the T6 re-home shape, run as a batch sweep instead of on a watcher event.

**The non-obvious data dependency (the heart of this task).** A sibling note carries `attachment_path` in its frontmatter pointing at the binary (capture.py:642), and the DB `documents` row's `vault_path` is the *sibling's* path (not the binary's). So moving an editable file out of `attachment/` touches THREE artifacts that must stay consistent:
  1. the binary → `move_attachment(old, root_dst)` (writer.py:241),
  2. the sibling `.md` → `move_note(old_sibling, root_summaries/<name>.md, actor="ai")` (writer.py:175) — and its `attachment_path` frontmatter must be rewritten to the binary's new location, because `move_note` only merges metadata, it does not re-point the pointer,
  3. the DB row → fix `vault_path` from the old sibling path to the new sibling path via `documents.rename(old_vp, new_vp)` (documents.py:287), which preserves row id and FK links (DECISION-001).
A migration that moves the binary but forgets the frontmatter pointer creates exactly the orphan that reconcile Stage 4 (`reconcile_orphan_siblings`) hunts — so getting the pointer rewrite right is what "leaves no orphan" (Done-when) actually means.

**Guards/constraints that apply:**
- **Vault-only writes (C-01):** every relocation goes through `move_attachment` / `move_note`; never raw `write_text`. Hook-enforced.
- **updated_by_human gate (C-02):** `move_note(..., actor="ai")` already refuses to move a human-locked sibling (writer.py:202-207, returns `Failure(recoverable=False)`). The stage must match that Failure and SKIP (mirroring how `reconcile_orphan_siblings` skips `updated_by_human` siblings, reconcile.py:252-254, and how `reconcile_stale_tags` swallows the human-lock Failure, reconcile.py:365-371). A locked sibling means the binary stays put — do not force-move the binary out from under a locked summary.
- **Audit (C-13):** one audit row per migrated file (action e.g. `reconcile:editable_migrated`, outcome e.g. `EDITABLE_MIGRATED`), via `audit_write(AIDecision(...))` exactly like the other reconcile stages.
- **Result type:** stage returns `Result[ReconcileResult]`; every inner `move_*`/`rename`/`get_by_path`/`read_note` call is matched (no silent failures), same posture as the existing stages.
- **No thresholds/prompts:** none introduced — migration is pure path math (reuses T2) and reuses the stored summary; no LLM, so no prompt and no confidence threshold.
- **Type guard (DECISION-029):** the moved sibling keeps `type=attachment-summary`; `move_note` preserves it since it re-renders existing metadata. Root `.summaries/` siblings must remain recognized as attachment summaries (see cross-cut below).

**Files touched (directly):**
- `src/pipelines/reconcile.py` — add `reconcile_editable_migration(result, ctx)`; add to `__all__`; call it from `reconcile()` (after Stage 4 orphan cleanup, before stale-tags — see Recommendation for ordering); add a counter to `ReconcileResult`.
- `src/cli/main.py` — extend the `kms reconcile` summary echo (main.py:129-136) with the new counter.
- `src/vault/paths.py` — extend the two near-twin predicates to recognize root-level `.summaries/` (cross-cut, flagged `[REQUIRES: T10]` by T2/T3). See §6.

**Files touched (indirectly / runtime deps):**
- Depends on T2's `resolve_placement(file_path, target_type, target_name, vault_cfg) -> Placement(final_dir, sibling_dir, needs_move)` to compute destinations — the migration MUST use it (file-diagnosis line 366) so capture and migration agree on one rule.
- Reuses T6's re-home logic conceptually; if T6 ships a reusable helper for "move binary + move sibling + re-point frontmatter + fix DB", this stage should call it rather than re-implement (see §4 Option C).
- Reads `_is_in_managed_attachment` (paths.py:26) to scope the walk and `_location_context` (paths.py:87) to derive project/domain name for `resolve_placement`.

**Downstream effects.** After migration, an editable file's DB `vault_path` = `Projects/<A>/.summaries/<name>.md` and its `attachment_path` frontmatter = `Projects/<A>/<name>.<ext>`. Search/index (FTS) is unaffected — the sibling content is unchanged, only its path moved (Stage 1 `reconcile_paths` would otherwise catch a path move, but doing it inside this stage keeps the binary+sibling+row atomic per file).

**Module depth (deletion test).** Shallow and additive: a new async stage function alongside six identical siblings, plus one call line in `reconcile()`. No new module, no new interface, no new abstraction. If deleted, the only loss is the migration sweep; everything else compiles. It introduces no speculative seam — it composes the existing `move_attachment`/`move_note`/`rename`/`resolve_placement` primitives.

## 2. Guardrail Checklist

| Rule | Applies? | How the recommended option satisfies it |
|---|---|---|
| C-01 vault-only writes | Yes | All file relocation via `move_attachment` / `move_note`; no raw writes. Hook-clean. |
| C-02 updated_by_human gate | Yes | `move_note(actor="ai")` refuses locked siblings; stage matches that `Failure`, logs `%s`-style, and skips the binary too (no half-move). |
| C-13 audit log | Yes | One `audit_write(AIDecision(action="reconcile:editable_migrated", ...), pipeline="reconcile", stage="reconcile_editable_migration", outcome="EDITABLE_MIGRATED")` per migrated file. |
| Result type, no silent failures | Yes | Stage returns `Result[ReconcileResult]`; every inner Result matched; failures logged and the file skipped (sweep continues), mirroring `reconcile_orphan_binaries`. |
| C-06 no hardcoded thresholds in pipelines | Yes (vacuously) | No confidence comparison; the editable/no-edit decision is delegated to `resolve_placement` (config-driven `no_edit_extensions`, not a float literal). |
| C-07 prompts as YAML | Yes (vacuously) | No LLM call — summary reused from the DB/sibling. |
| Single placement rule (T2 anti-goal: no second copy) | Yes | Calls `resolve_placement`; the editable/no-edit + needs-move decision lives ONLY in T2. |
| C-17 CONFIG import scope (tests) | Yes | Tests build `VaultConfig(root=tmp_path)` + `PipelineContext` explicitly; never module-scope CONFIG. |
| Type-guard preservation (DECISION-029) | Yes | Moved sibling retains `type=attachment-summary`; root `.summaries/` recognized as managed. |
| %s-style stdlib logging | Yes | `_log = logging.getLogger(__name__)` already at top of reconcile.py; use `%s` placeholders. |

## 3. Success criteria

**You can verify (vault-visible):**
1. Given `Projects/A/attachment/plan.docx` with sibling `Projects/A/attachment/.summaries/plan.docx.md`, When I run `kms reconcile`, Then `plan.docx` appears at `Projects/A/plan.docx` (visible in Obsidian) and is gone from `attachment/`.
2. Given the same, When reconcile finishes, Then the sibling is at `Projects/A/.summaries/plan.docx.md` and `attachment/.summaries/` no longer holds it.
3. Given `Domain/Finance/attachment/budget.xlsx`, When I run reconcile, Then it lands at `Domain/Finance/budget.xlsx`.
4. Given a PDF `Projects/A/attachment/report.pdf`, When I run reconcile, Then it STAYS in `attachment/` (no-edit) and is untouched.
5. Given an editable file whose sibling was hand-edited (`updated_by_human: true`), When I run reconcile, Then both the binary and the sibling are left exactly where they are (no surprise move).

**Developer must verify:**
1. After migration, `documents.get_by_path("Projects/A/.summaries/plan.docx.md")` returns a row (id preserved) and `get_by_path` of the old sibling path returns `Success(None)` — `documents.rename` ran, no duplicate row.
2. The migrated sibling's `attachment_path` frontmatter equals `Projects/A/plan.docx` (re-pointed), so a subsequent `reconcile_orphan_siblings` run does NOT delete it.
3. Exactly one audit row per migrated file with `outcome="EDITABLE_MIGRATED"` and a bound `correlation_id` (set at the `reconcile` entry point); zero audit rows for skipped (locked / no-edit / no-row) files.
4. The CLI echo reports the new counter (`N editable files migrated`).
5. Non-interference (reconcile sweep vs live watcher): if `kms watch` is running concurrently, the migration's own `move_attachment` does not trigger a watcher re-home loop — verify via the T8 MoveGuard register-before-move (the stage should register the dst with `get_active()` if present, same as capture sites) OR accept that `kms reconcile` is run while the watcher is stopped. RECOMMENDED: register dst with the T8 guard to be safe; documented as a soft dependency, not blocking.

## 4. Options

### Option A — New Stage 7 `reconcile_editable_migration` (Recommended)
- **What this means (plain English):** Add one more stage to the existing reconcile pipeline. It walks every `attachment/` folder, and for each editable file, performs the full re-home (binary out to root, sibling to root `.summaries/`, frontmatter re-pointed, DB row renamed), reusing the same placement rule capture uses.
- **Approach:** New `async def reconcile_editable_migration(result, ctx)` in reconcile.py. Walk `vault_cfg.root.rglob(vault_cfg.attachment_dir)` (same loop shape as Stages 2/3, reconcile.py:111/158). For each file: skip dot/`.md`/symlink; require `_is_in_managed_attachment`; compute `is_no_edit = entry.suffix.lower() in vault_cfg.no_edit_extensions` → skip if no-edit; derive `(loc_type, loc_name)` via `_location_context(entry, vault_cfg)`; call `resolve_placement(entry, loc_type, loc_name, vault_cfg)`; if `not placement.needs_move` skip; else collision-loop the dst name in `placement.final_dir` (same `-N` up-to-100 policy as capture.py:602-611), `move_attachment(entry, root_dst)`, then locate the old sibling at `attachment/.summaries/<entry.name>.md`, `read_note` it, rewrite `attachment_path` to `to_vault_path(root_dst)`, `write_note`/`move_note` it to `placement.sibling_dir/<root_dst.name>.md` with `actor="ai"`, then `documents.rename(old_sibling_vp, new_sibling_vp)` (or `get_by_path`→`upsert` if a content-hash change is needed; rename suffices since content is unchanged). One audit row. Add counter to `ReconcileResult`; wire into `reconcile()` and the CLI echo.
- **Files touched:** reconcile.py (stage + entry + dataclass field), cli/main.py (echo), paths.py (root-`.summaries/` recognition — cross-cut).
- **Cost:** dev = moderate (one stage mirroring Stages 2/3 plus the sibling-repoint detail); runtime = O(files in attachment dirs), no LLM, fast; maintenance = low (additive, consistent with existing stages).
- **Risk:** the frontmatter re-point + DB rename ordering must be sibling-safe (write sibling to new location BEFORE renaming the DB row, so a crash leaves the old row pointing at a still-readable file). Medium-low; identical hazard already handled by capture's sibling-first ordering (DECISION-025).
- **Module depth:** shallow, additive; passes deletion test (removing it loses only migration). No new interface — composes existing primitives.
- **What it defers:** nothing new; OQ-008 (human edits in AI-output folders) untouched.
- **Constraints check:** C-01/C-02/C-13/Result/C-17 all satisfied; single placement rule preserved.

### Option B — Throwaway one-off migration script (CLI `kms migrate-editables`)
- **What this means:** A separate, run-once command outside the reconcile pipeline.
- **Approach:** New Click command duplicating the walk + re-home logic.
- **Files touched:** cli/main.py (new command), a new small module.
- **Cost:** dev similar; maintenance HIGHER (a second walk loop + a second place the editable/placement logic lives, risking drift); runtime same.
- **Risk:** the draft's own Goal says "no separate throwaway script" (line 348). Violates that goal. Also a second entry point users must remember.
- **Module depth:** a new command boundary that fails the deletion test poorly (it is single-use; the reconcile pipeline already is the home for drift correction).
- **What it defers:** future drift (the draft notes reconcile is the long-term home, line 362) — a script does not catch future drift on each `kms reconcile`.
- **Constraints check:** same guard satisfaction, but contradicts the stated anti-goal of "one on-demand command; no separate throwaway script."

### Option C — Extract a shared `_rehome_one(binary, loc, cfg, ctx)` helper used by BOTH T6 (watcher) and T10 (sweep)
- **What this means:** Factor the "move binary + move sibling + re-point frontmatter + fix DB + audit" into a single function; T6's watcher branch and T10's sweep both call it.
- **Approach:** New helper (in vault or pipelines) returning `Result`; T10's stage becomes a thin walk that calls it per file; T6 calls it per event.
- **Files touched:** reconcile.py, watcher.py (T6), a shared home, cli/main.py, paths.py.
- **Cost:** dev HIGHER now (must reconcile T6's event-context + MoveGuard/REHOMED-audit semantics with a batch sweep's per-file audit); maintenance LOWER long-term (one re-home implementation).
- **Risk:** couples T10 to T6's exact shape and timing; T6 carries watcher-specific concerns (MoveGuard `check_and_consume`, settle-window origin tracking from T7, `REHOMED` outcome string, `_vp()` vs CONFIG). Forcing a shared helper risks dragging watcher concerns into the batch path. Real seam only if both adapters genuinely need identical behavior — they differ on audit outcome and guard semantics.
- **Module depth:** a real interface IF 2+ adapters need it — they do (watcher + reconcile both re-home). But premature now: build T10 against the primitives, extract later if duplication proves painful.
- **What it defers:** the extraction itself; safe to defer.
- **Constraints check:** satisfiable, but higher coordination cost and scope beyond this one task.

## 5. Recommendation

**Option A — new Stage `reconcile_editable_migration` appended to the existing pipeline.** The tradeoff: it duplicates the "re-home one binary" steps that T6 also performs (a few lines of move+repoint+rename), which we accept in exchange for keeping T10 decoupled from T6's watcher-specific semantics (MoveGuard, settle window, REHOMED audit) and landing the migration in the one place users already run for drift correction. If the duplication later bites, Option C's shared helper is the clean refactor — but extracting it now would couple a batch sweep to event-driven watcher concerns prematurely.

## 6. Cross-check

- **Scope creep removed:** no live-capture changes (that is T3); no new config key (reuses T1's `no_edit_extensions`); no LLM/summary regeneration (reuse stored summary). The stage only moves what the old pipeline misplaced.
- **Constraint flags:** ⚠️ **Frontmatter re-point is mandatory** — `documents.rename` fixes only the DB `vault_path`; the sibling's `attachment_path` frontmatter still points at the OLD `attachment/` binary after a naive `move_note`. Failing to rewrite it makes the next `reconcile_orphan_siblings` run delete the freshly-migrated sibling (binary "missing" at the stale pointer). This is the single most likely bug — call it out in the build step. ⚠️ **Stage ordering:** place `reconcile_editable_migration` AFTER `reconcile_orphan_binaries`/`reconcile_stale_binaries` (so capture-of-missing/stale runs against files still in their old home) and BEFORE `reconcile_orphan_siblings` (so the orphan stage sees the migrated, re-pointed siblings — never the in-flight state). Recommended slot: between Stage 3 and Stage 4. (If placed after Stage 4, ensure the migration's own writes cannot be seen as orphans within the same run — they won't, since the stage repoints before completing.)
- **Tech-debt touched:** RETIRES the implicit "old vaults have editables stuck in attachment/" drift; touches the same `_is_in_managed_attachment`/`_is_managed_summaries_area` hotspot CLAUDE.md flags as silent-bug-prone — must extend BOTH to recognize root-level `.summaries/` (a `Projects/<A>/.summaries/` or `Domain/<D>/.summaries/` whose grandparent is projects_path/domain_path), or migrated siblings won't be protected by Stage 4's scope guard. Neutral on TD-037/TD-039 (Windows / content-change — unrelated).
- **DECISION-NNN:** consistent with DECISION-025 (sibling-first ordering — write/move sibling before flipping the DB row), DECISION-029 (preserve `type=attachment-summary`, including in root `.summaries/`), DECISION-001 (use `rename` to preserve row id + FK). Does not contradict any.
- **[REQUIRES: T2]** hard dependency on `resolve_placement` (single placement rule). **[REQUIRES: T1]** `no_edit_extensions`. **[REQUIRES: paths.py root-`.summaries/` recognition]** the cross-cut at draft L391 — this task is the one that needs it real, so extend `_is_in_managed_attachment`/`_is_managed_summaries_area` here (or confirm T2/T3 already shipped it; T2 explicitly deferred it to T10). **[SOFT: T8]** register migration dst with the MoveGuard if `get_active()` is non-None, so a concurrently-running watcher does not re-home the migration's own move; otherwise document "run reconcile with the watcher stopped."

---

# Appendix — Settled decisions passed down the chain

Each task's concrete decisions that later tasks were built to honor. Use this as the consistency contract when writing specs.

## T5 — capture_folder: exclude attachment/ + .summaries/ by name

**Recommendation:** Option A — config-driven name exclusion in _collect_folder_files (add the two names to the existing rel_parts membership check, sourced from VaultConfig). One reason: it matches the codebase's established config-driven name-skip convention (scan_capture reads summaries_subdir the same way) at the lowest possible blast radius, trading a minor, accepted over-exclusion of any user folder literally named "attachment" for a one-line, deletion-testable fix.

**Decisions for downstream:**

- `_collect_folder_files` signature changes to `(folder_path: Path, vault_cfg: VaultConfig) -> list[Path]` (was folder_path only). Both call sites in `capture_folder` (capture.py:1255, 1306) pass the already-bound `vault_cfg`/`ctx.config.vault`.
- Exclusion is BY NAME against `rel_parts`, reusing the existing `any(part in {...} for part in rel_parts)` check — NOT the grandparent `_is_in_managed_attachment` predicate (anti-goal in draft).
- Skip names come from config: `vault_cfg.attachment_dir` (default "attachment") and `vault_cfg.summaries_subdir` (default ".summaries"). Never hardcode the literals.
- `.summaries` is in indexer `_DOT_ALLOWLIST` and NOT in `IGNORE_DIRS`; `attachment` is in neither — that is why today's loop collects them. Do not add either to IGNORE_DIRS (would change indexer/scan_capture behavior — out of scope).
- file_count at capture.py:1267/1309/1333 is `len(files)`/`len(new_files)` from this collector, so the fix auto-corrects PARTIAL/COMPLETE accuracy with no separate change.
- Accepted limitation (documented, not blocking): a user folder literally named `attachment` or `.summaries` inside a drop is skipped. Fine for the non-technical target user.

## T1 — Config: no_edit_extensions + capture-excluded folders

**Recommendation:** Option A — add `no_edit_extensions` as a field on `VaultConfig` and expose AI-output folders via a computed `@property` that reuses the three existing dir-name fields. Chosen because it puts both lists where every other consumer (watcher, capture, paths.py) already reads vault structure, with zero duplication of the folder names that already live on the model.

**Decisions for downstream:**

- New config field: `VaultConfig.no_edit_extensions: list[str]`, default `[".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]`. Stored LOWERCASED + dot-prefixed (matches `office_extensions` convention). YAML key: `vault.no_edit_extensions`.
- AI-output folders exposed via computed `@property VaultConfig.ai_output_dirs -> tuple[str,...]` returning `(briefings_dir, synthesis_dir, documentation_dir)` — reuses existing `*_dir` Fields; no new YAML key for the folder NAMES (they already exist at config.yaml L70-77). Optionally add `ai_output_paths` property mirroring `briefings_path` etc.
- CONSUMER OBLIGATION (T2): lowercase the candidate file's `.suffix` before `in vault_cfg.no_edit_extensions`. Membership form: `path.suffix.lower() in vault_cfg.no_edit_extensions`.
- CONSUMER OBLIGATION (T4): iterate `vault_cfg.ai_output_dirs` for capture-exclusion; do NOT add these names to `indexer.IGNORE_DIRS` (would change indexer/FTS scope, out of scope).
- Do NOT introduce an `editable_extensions` list — editable = non-`.md` AND suffix NOT in `no_edit_extensions`.
- Image set shipped: pdf+png+jpg+jpeg+gif+webp. `.heic/.tiff/.svg/.bmp` deliberately excluded (widen via YAML if needed).
- Recommended (not required): Pydantic field_validator lowercasing entries + asserting leading dot.
- Tests must build VaultConfig/MainConfig with explicit `root=tmp_path` (C-17), never module-scope CONFIG import.

## T4 — Misplaced→inbox (all types) + AI-output capture-exclusion

**Recommendation:** Option B — Two-predicate approach: a new `_is_ai_output(path, vault_cfg)` capture-exclusion predicate (wired into watcher `_should_skip`, `scan_non_md_drops`, and `scan_vault` pruning) plus a `_is_misplaced(path, vault_cfg)` predicate that routes any mis-dropped file (md and non-md) to inbox via the existing move-to-inbox machinery. Chosen because it puts the two new behaviors behind named, deletion-test-passing predicates in `vault/paths.py` (the existing home for these checks) and reuses the already-built CLUELESS inbox move — at the cost of one extra hop through the watcher for misplaced md, which today is a no-op.

**Decisions for downstream:**

- Add two pure-bool predicates to `src/vault/paths.py`: `_is_ai_output(path, vault_cfg)` (True iff a path part matches one of `vault_cfg.ai_output_dirs`) and `_is_misplaced(path, vault_cfg)` (True iff internal AND not inbox AND not AI-output AND not a real project/domain location where "real" requires `len(rel.parts) >= 2` relative to projects_path/domain_path). These are bool, NOT Result (consistent with existing `_is_in_managed_attachment`).
- `_is_misplaced` must NOT call `_location_context` — it does its own `>=2 parts` test to dodge the phantom-project quirk (paths.py:117-121).
- Wire `_is_ai_output` into: watcher `_should_skip` (vault/watcher.py:124), `scan_vault` prune loop (indexer.py:183-192), `scan_non_md_drops` prune loop (indexer.py:124-133). Iterate `vault_cfg.ai_output_dirs`; do NOT add these names to `indexer.IGNORE_DIRS`.
- Misplaced NON-md already routes to inbox via the existing CLUELESS branch (capture.py:699-715) because `len(rel.parts) < 2` leaves target_type=None — keep relying on that; no new code for non-md beyond the audit/log naming.
- Misplaced MD is the only genuinely new sweep: in `scan_capture`'s md loop (capture.py:964) or a small `Result`-returning helper, detect `_is_misplaced`, `move_note(src, inbox/<name>)`, then let normal in-inbox capture run. Write an audit row (C-13).
- Use `move_note`/`move_attachment` for the sweep (C-01); never raw write_text. Watcher relpaths via `relative_to(self._root)`, logging `%s`-style. Tests patch `vault.watcher.<name>` / `pipelines.capture.<name>`, build `VaultConfig(root=tmp_path)`.
- Accepted documented limitation: a user folder literally named Briefings/Synthesis/Documentation anywhere is also skipped (same posture as T5's attachment/.summaries).
- OQ-008 (human edits to excluded AI-output folders) is explicitly deferred; the `_is_ai_output` predicate is the future hook.

## T2 — Single shared placement helper

**Recommendation:** Option A — add a pure function `resolve_placement(...)` to `src/vault/paths.py` returning a small `Placement` dataclass `(final_binary_path, sibling_path, needs_move)`. Chosen because it puts the editable/no-edit rule in the one module that already owns parametrized path math, with zero LLM/IO and a clean deletion test, and gives the future Phase 2 Classify the exact same callable as capture.

**Decisions for downstream:**

- New pure function lives in `src/vault/paths.py`: `resolve_placement(file_path: Path, target_type: Literal["project","domain"], target_name: str, vault_cfg: VaultConfig) -> Placement`. Returns a frozen dataclass `Placement(final_binary_path: Path, sibling_path: Path, needs_move: bool)`. NO Result wrapper (consistent with the other pure predicates in this module that return bool/Path, not Result).
- Decision rule (the single source of truth): `is_no_edit = file_path.suffix.lower() in vault_cfg.no_edit_extensions` (T1 obligation: candidate suffix lowercased before membership). `in_attachment = _is_in_managed_attachment(file_path, vault_cfg)`. no-edit & not in_attachment → move INTO attachment_dir; editable & in_attachment → move OUT to <type> root; else stay. needs_move = (final parent != current parent).
- Helper does NOT apply the rename gate, does NOT pick a collision-free filename, does NOT mkdir, does NOT read bytes, does NOT call the LLM. It computes the parent directory + the sibling path from a CALLER-SUPPLIED final filename. Signature therefore takes the already-sanitized final filename, or returns the target DIRECTORY and lets T3 do collision + name. RECOMMENDED concrete signature: `resolve_placement(file_path, target_type, target_name, vault_cfg)` returns `Placement(final_dir: Path, sibling_dir: Path, needs_move: bool)` — directories only; T3 owns rename-gate stem, collision loop, and final `final_dir/<stem><suffix>` + `sibling_dir/<finalname>.md`. This keeps the helper free of the rename-gate dependency.
- Sibling path is ALWAYS `<final binary parent>/<summaries_subdir>/<binary final name>.md` — i.e. reuse the `_sibling_for` naming rule (`<binary.name>.md`, full filename incl ext) but anchored to the binary's FINAL parent, not its source parent. For editable-in-root that means `Projects/<A>/.summaries/<name>.md`; for no-edit that means `Projects/<A>/attachment/.summaries/<name>.md`.
- The four existing path helpers (`project_attachment`/`project_summaries`/`domain_attachment`/`domain_summaries`, paths.py:207-246) MUST be supplemented with TWO new root-summaries helpers OR the directory math inlined in `resolve_placement`. RECOMMENDED: inline the root-summaries dir math inside `resolve_placement` (root case = `<type>_path / name / summaries_subdir`) to avoid two more near-identical CONFIG-singleton helpers; the existing four helpers stay for the no-edit (attachment) branch but should be replaced by config-driven path building inside the helper to avoid the CONFIG-singleton import (helper takes vault_cfg explicitly, so build paths from vault_cfg, NOT from the CONFIG singleton).
- T3 must call `resolve_placement` and DELETE its inline L561-575 + L593-615 destination logic. No second copy of the editable/no-edit rule may exist (T2 anti-goal).
- Cross-cutting: the two near-twin predicates (`_is_in_managed_attachment`, `_is_managed_summaries_area`) do NOT yet recognize root-level `.summaries/` — that recognition is a SEPARATE downstream concern (reconcile Stage 4, T10). T2 does not modify those predicates; it only WRITES paths there. Flag [REQUIRES: T10] for reconcile to recognize root `.summaries/`.
- Tests build `VaultConfig(root=tmp_path)` explicitly (C-17); never import CONFIG at module scope. Six branch tests minimum: {editable, no-edit} × {in root, in attachment, elsewhere/inbox} for both project and domain.

## T3 — _store_nonmd: symmetric, type-driven needs_move

**Recommendation:** Option A — Consume T2's `resolve_placement` and delete the inline destination logic; one collision loop generalised to the helper-returned `final_dir`. Reason: it eliminates the duplicate editable/no-edit rule (T2 anti-goal) while keeping the sibling-first ordering and audit/Result guards untouched, trading a small one-time refactor for a single source of truth.

**Decisions for downstream:**

- T3 edits ONLY the LOCATED branch of `_store_nonmd` (src/pipelines/capture.py:540-697); CLUELESS branch (L699-763) untouched (that is T4 + Phase 2).
- T3 calls T2's `resolve_placement(src, target_type, target_name, vault_cfg) -> Placement(final_dir, sibling_dir, needs_move)`; deletes inline destination logic at capture.py:561-575 and dir-selection at L593-598; removes the lazy import of project_attachment/project_summaries/domain_attachment/domain_summaries (L550-555).
- T3 still derives `target_type`/`target_name` from the path (helper needs them as input) and still owns the rename-gate stem + collision loop; the collision loop now runs against `placement.final_dir` (root dir for editable files), SAME `-N` up-to-100 policy as today (confirmed for root per OQ L143).
- Sibling path = `placement.sibling_dir / f"{final_binary_name}.md"` (full filename incl ext, `_sibling_for` rule); `needs_move = placement.needs_move`.
- KEEP verbatim: sibling-first ordering (DECISION-025), source_hash (capture.py:638-639), `type="attachment-summary"` sibling metadata (DECISION-029, incl. root siblings), LOCATED audit row (C-13) — enrich its `reasoning` string to note editable→root vs no-edit→attachment.
- Editable binaries now land in project/domain ROOT; sibling DB row `vault_path` = root `.summaries/<name>.md`, frontmatter `attachment_path` = root binary.
- [REQUIRES: T2] hard dependency. [REQUIRES: T10] reconcile must recognise root-level `.summaries/`.
- Tests: build `VaultConfig(root=tmp_path)` explicitly (C-17); minimum branches {editable, no-edit} × {root start, attachment start, stay} for project and domain.

## T8 — Sticky-note: suppress pipeline-initiated moves

**Recommendation:** Option A — Thread-safe TTL suppression registry as a small standalone module (vault/move_guard.py), instantiated by VaultWatcher and injected into the handler; pipeline registers via a module-level singleton bound at watch wiring. Chosen because it gives one explicit, testable seam shared cleanly across the observer thread and the pipeline thread-pool, expires on its own (crash-safe), and adds no logic to the move-handler beyond a single membership check.

**Decisions for downstream:**

- New module `src/vault/move_guard.py` with class `MoveGuard`: methods `register(path: Path, ttl: float | None = None) -> None` and `check_and_consume(path: Path) -> bool` (consume-once, lazy-expire under a `threading.Lock`); internal dict `path-str -> expiry-monotonic-timestamp`. Normalize the key via `unicodedata.normalize("NFC", str(path))` so it matches watchdog event paths.
- Module-level accessor in `vault/move_guard.py`: `set_active(guard)` / `get_active() -> MoveGuard | None`, bound during `cli/main.py::watch()` wiring (around lines 253-260) so the pipeline and watcher share ONE instance. Outside `kms watch`, `get_active()` returns None and the pipeline skips registration (no-op).
- TTL default ~5s, derived from / slightly above `VaultWatcher.debounce_seconds` (3.0). Do NOT hardcode a bare literal in `pipelines/` (C-06 is about confidence thresholds, but keep the TTL on the guard/config, not in a pipeline if/elif).
- Pipeline register sites (capture.py): immediately BEFORE `move_attachment(src, attachment_dst)` (L658), BEFORE `move_attachment(src, inbox_dst)` (L711), and BEFORE `move_folder(folder_path, destination)` (L1297) — register the exact dst Path used by the move. Guard via `g = get_active(); if g: g.register(dst)`.
- Watcher consumer (T6): inside the NEW re-home branch of `_VaultEventHandler.on_moved`, call `self._move_guard.check_and_consume(dst)`; True -> skip re-home, emit `_log.info("watcher.rehome_skip path=%s reason=pipeline_initiated", dst)`. Do NOT gate the existing `_handle_binary_move` sibling-sync (watcher.py:288-291) — T8 suppresses re-home only.
- `VaultWatcher.__init__` creates the `MoveGuard` (or accepts one) and passes it to `_VaultEventHandler`; store as `self._move_guard`.
- No audit row for suppression (no AI decision — C-13 N/A); log-line only, `%s`-style stdlib logging.
- Tests: construct `MoveGuard` directly + `VaultConfig(root=tmp_path)` (C-17); patch `vault.watcher.*` / `vault.move_guard.get_active` (TD-033); cover expiry-after-TTL and cross-thread register/check.
- Preserve TD-030 ordering in on_moved: binary-sync dispatch stays before any new guard/re-home logic.
- [REQUIRES: T6] sole consumer; [REQUIRES: T3] T3's LOCATED move site must be one of the registered sites. Ship T8 in the same increment as T6.

## T6 — Watcher: re-home on user move (incl. T11 correlation_id verify)

**Recommendation:** Option B — DB-driven re-home built on T2's resolve_placement, reusing the cross-folder branch of _handle_binary_move. Chosen because it reuses the single placement source of truth (no second copy of the editable/no-edit rule) and survives a missing on-disk source sibling (the move-chain case T7 cares about), at the cost of one extra DB read versus the on-disk-copy approach.

**Decisions for downstream:**

- T6 rewrites ONLY the cross-folder/else branch of `_handle_binary_move` (watcher.py:420-449); same-folder rename branch (L357-419) unchanged.
- Reverse lookup is path-keyed, NOT attachment_path: `documents` has no attachment_path column (verified schema.sql). Use `get_by_path(to_vault_path(_sibling_for(src, cfg)))` to fetch the summary-bearing DocumentRow.
- Re-home sources the summary from the DB row, never the on-disk source sibling (composes with T7 coalesced moves).
- Re-home calls T2 `resolve_placement(dst, loc_type, loc_name, cfg)` to pick final binary dir + sibling dir (editable→root, no-edit→attachment); new sibling = `placement.sibling_dir / f"{dst.name}.md"`.
- Location re-derivation uses `_location_context(dst, cfg)` (paths.py:87) to get the new project/domain, mirroring apply_location_tags' rule (not the async stage).
- New audit outcome string `REHOMED`, action `watcher:binary_rehome`, pipeline=watcher/stage=sync; replaces the orphan SIBLING_ORPHANED audit on this branch.
- T8 gate goes INSIDE the re-home branch entry: `g=get_active(); if g and g.check_and_consume(final_binary): log watcher.rehome_skip ...; return` — after binary-sync dispatch (TD-030 ordering preserved).
- T11: preserve the existing function-top `bind_contextvars(correlation_id=new_correlation_id())` (watcher.py:346-347); add NO second bind. Verify-only via cross-folder move test.
- Missing-row fallback: if get_by_path returns no row, log not_in_index warning + orphan-only (today's posture), no crash.
- New top-level imports in watcher.py: `resolve_placement`/`Placement` and `get_by_path`; tests patch `vault.watcher.<name>` (TD-033), build `VaultConfig(root=tmp_path)` (C-17).
- All disk writes via move_attachment/move_note/write_note (C-01); one audit row (C-13); every Result matched; %s-style stdlib logging; vault-relative via local `_vp()` not CONFIG-singleton to_vault_path.

## T7 — Move-chain convergence (settle window)

**Recommendation:** Option B — Dedicated binary-settle cooldown registry mirroring the folder-cooldown token-guard pattern, keyed on the destination filename. Chosen because it reuses a battle-tested in-tree pattern (the C2 token guard) and converges A→B→C to a single re-home with no new identity infrastructure, at the cost of a known same-name-collision edge that is acceptable for a single sequential human user.

**Decisions for downstream:**

- T7 adds a binary-move SETTLE WINDOW in src/vault/watcher.py, modelled on the folder-cooldown helpers (_register_pending_folder/_reset_folder_timer/_fire_folder_stable + token guard, watcher.py:182-249). New private helpers (e.g. _register_pending_move/_reset_move_timer/_fire_move_stable) + two dicts (pending timers, tokens) + a lock on _VaultEventHandler. COPY the C2 token-guard verbatim so a superseded hop is a no-op.
- Identity key = unicodedata.normalize("NFC", dst.name) (FILENAME, not destination path — because the chain ends at different paths and bin:{dst} debounce does not coalesce different keys). Accepted documented limitation: two different same-named files moving concurrently would coalesce; fine for the single sequential human user (resolves OQ L268 + L402).
- New config: CaptureConfig.binary_settle_seconds: float = Field(5.0, ge=0.0) in src/core/config.py (~L237, next to folder_cooldown_seconds); YAML key capture.binary_settle_seconds: 5.0 in src/config/config.yaml (~L7). Resolves OQ L269 in favour of a DEDICATED key (not reusing folder_cooldown_seconds). Thread it cli/main.py watch() (main.py:253-260) -> VaultWatcher.__init__ (watcher.py:465-503) -> _VaultEventHandler.
- on_moved rewire (watcher.py:288-296): KEEP the bin:{dst} sibling-sync dispatch FIRST (TD-030). Then register the move into the settle window instead of debouncing directly into the cross-folder re-home. The settle window gates ONLY the cross-folder RE-HOME firing (T6 branch), not the sibling-sync dispatch, not the same-folder rename branch.
- The pending-move registry must store the chain-ORIGIN src (first hop's src) per key so the final fire does the DB summary lookup against the true origin via T6's get_by_path(_sibling_for(origin, cfg)) — NOT against the immediate src (which may have no on-disk sibling after coalescing). Re-home reuses the DB-stored summary, no LLM.
- Ordering at fire time: binary-sync dispatch (per hop, TD-030) -> settle/coalesce -> on final fire, T8 check_and_consume(final_binary) -> T6 re-home. Do NOT move the T8 check earlier (would consume a guard token on a superseded hop).
- Exactly ONE REHOMED audit row per settled chain (C-13); superseded hops write NO audit row. Superseded hops log %s-style trace (e.g. watcher.binary_settle_superseded key=%s).
- Tests: build VaultConfig(root=tmp_path) + explicit binary_settle_seconds (C-17); patch vault.watcher.<name> (TD-033); cover A->B->C convergence (1 sibling/1 DB row/1 audit at C), single-hop unchanged, stale-fire token no-op.
- [REQUIRES: T6] sole consumer, ship same increment (order T8->T6->T7). [REQUIRES: T8] settle window precedes guard check. [REQUIRES: T2] via T6.

## T10 — Reconcile migration stage (editable-in-attachment)

**Recommendation:** Option A — new Stage 7 `reconcile_editable_migration` appended to the existing 6-stage pipeline, reusing the T2 `resolve_placement` helper and the T6 re-home mechanics (move binary + move sibling + fix frontmatter pointer + fix DB path). Chosen because it heals existing vaults with one on-demand command, lives where every other drift fix lives, and shares the single placement rule so capture and migration can never disagree.

**Decisions for downstream:**

- New stage `reconcile_editable_migration(result, ctx) -> Result[ReconcileResult]` added to src/pipelines/reconcile.py; added to __all__; called from reconcile() BETWEEN Stage 3 (stale_binaries) and Stage 4 (orphan_siblings).
- New counter field on ReconcileResult (e.g. `editables_migrated: int = 0`); CLI echo in cli/main.py:129-136 extended.
- Walk shape: `vault_cfg.root.rglob(vault_cfg.attachment_dir)`; per file skip dot/.md/symlink, require `_is_in_managed_attachment`, skip if `entry.suffix.lower() in vault_cfg.no_edit_extensions` (no-edit stays put).
- Destination via T2 `resolve_placement(entry, loc_type, loc_name, vault_cfg)`; loc via `_location_context(entry, vault_cfg)`; collision loop `-N` up to 100 in `placement.final_dir` (same policy as capture.py:602-611).
- Per migrated file: move_attachment(binary→root_dst); read old sibling at `attachment/.summaries/<entry.name>.md`; REWRITE its `attachment_path` frontmatter to `to_vault_path(root_dst)` (MANDATORY — rename alone is insufficient); move_note sibling→`placement.sibling_dir/<root_dst.name>.md` actor="ai"; documents.rename(old_sibling_vp, new_sibling_vp). Sibling-first ordering (DECISION-025): write/move+repoint sibling before flipping DB row.
- updated_by_human siblings: move_note(actor="ai") returns Failure(recoverable=False) — match and SKIP the whole file (do not move binary). No LLM call anywhere.
- One audit row per migrated file: action `reconcile:editable_migrated`, outcome `EDITABLE_MIGRATED`, pipeline=reconcile, stage=reconcile_editable_migration.
- Extend `_is_in_managed_attachment` / `_is_managed_summaries_area` (paths.py:26,56) to recognize root-level `Projects/<A>/.summaries/` and `Domain/<D>/.summaries/` (grandparent = projects_path/domain_path) — REQUIRED so Stage 4 protects migrated siblings.
- SOFT T8: if `get_active()` is non-None, register root_dst with the MoveGuard before move_attachment to avoid a concurrent watcher re-homing the migration's own move.
- Tests: VaultConfig(root=tmp_path) + PipelineContext explicit (C-17); branches {editable project, editable domain, no-edit stays, already-at-root no-op, human-locked skip}.

## T9 — Content-change detection on binary edit (atomic-save aware)

**Probe source:** Real-vault macOS event capture, 2026-06-04. Three Office apps, three save sequences logged. Design is probe-grounded — do not replace with assumptions.

## 1. Implications

**What the probe revealed — macOS atomic-save event sequences.**
All three apps fire a burst of events on the original filename within a window of 1–35 ms, then stop. The file survives at the original path. No MOVE events appear in any sequence.

```
Word (.docx) open:   CREATE ~$<name>.docx, MODIFY ~$<name>.docx, MODIFY <name>.docx
Word  Cmd+S burst:   MODIFY, DELETE, MODIFY, CREATE, MODIFY, CREATE  ← last = CREATE
Excel (.xlsx) open:  MODIFY <name>.xlsx, CREATE ~$<name>.xlsx, MODIFY ~$<name>.xlsx
Excel Cmd+S burst:   CREATE, MODIFY, DELETE                          ← last = DELETE
PPT  (.pptx) open:   CREATE ~$<name>.pptx, MODIFY ~$<name>.pptx, MODIFY <name>.pptx
PPT   Cmd+S burst:   MODIFY, CREATE, MODIFY, DELETE, MODIFY, CREATE  ← last = CREATE
All   on close:      MODIFY ~$<name>.ext, DELETE ~$<name>.ext
```

Critical implication: **Excel's save burst ends with DELETE.** A naive handler that routes on the last event type would interpret an Excel save as a file deletion. The handler must be burst-aware.

- The `~$<name>.ext` lock file is created by the Office process when the user opens the file, and deleted only when they close it — not during each save. It starts with `~$`, not `.`. Current `_should_skip` (`vault/watcher.py:134`) checks `path.name.startswith(".")`, which does **not** match `~$` files. Without a fix, every lock file create/modify fires `_debounce` and potentially `_on_create` → a spurious full capture of the lock file.

- All three save sequences fire MODIFY events on the real binary path. `on_modified` (`watcher.py:251–260`) currently returns early for non-`.md` files at line 257 ("Binary modify deferred — TD-C6"). This is the primary gap: MODIFY events are silently dropped for every binary.

- `_handle_binary_delete` (`watcher.py:303–342`) runs after the debounce window fires. It does NOT check whether `path.exists()` before orphaning the sibling. In Excel's save sequence, DELETE fires mid-burst but the file survives. After the 3 s debounce, `_handle_binary_delete` fires, finds the file exists (save completed), but without the guard it still attempts `delete_by_path` on the sibling DB row — silently orphaning a valid, living sibling. The main.py `on_delete` callback (cli/main.py:226) already contains `if path.exists(): return` for this exact reason (a comment on line 228 says "macOS FSEvents … old inode deleted but path remains"). `_handle_binary_delete` is missing the same guard.

- `capture_file` (`pipelines/capture.py:793`) has a binary idempotency guard (lines 868–908): for each binary it finds the sibling, reads `source_hash` from sibling frontmatter, computes `hashlib.sha256(path.read_bytes()).hexdigest()`, and returns early if hashes match. This existing infrastructure is the correct re-capture decision point — T9 must route changed binaries **into** `capture_file`, not implement a separate re-capture pipeline.

- The sibling-lookup logic in `capture_file` (lines 876–884) scans `path.parents` for the first `.summaries/<name>.md` candidate. After T3 ships (editable files in project root, sibling in root `.summaries/`), this scan will find root `.summaries/<name>.md` files correctly — no change needed to the lookup.

- `read_note` is already imported at module scope in `watcher.py:41`. `hashlib` is not — must be added to watcher.py imports.

- The MODIFY events during the burst all fire at the same `str(path)` debounce key if we route them there; the CREATE and DELETE events at the same key will overwrite each other's callback in `_debounce`. For Word/PPT (last event = CREATE), `str(path)` ends up routing to `_on_create`, which calls `capture_file` — already correct. For Excel (last event = DELETE), `str(path)` ends up routing to `_on_delete`, which in main.py returns early because `path.exists()`. So the `str(path)` key alone fails for Excel re-saves.

- A dedicated third debounce key `chg:{path}` — independent of `str(path)` (user callback routing) and `bin:{path}` (binary sync/sibling cleanup) — guarantees that ALL three event types (MODIFY, CREATE, DELETE) converge on a single "check and maybe re-capture" callback regardless of which event fires last. The `chg:` timer is always the last line of defense, not the only one.

- `_handle_binary_content_change` runs in a `threading.Timer` thread, same as `_handle_binary_delete`. Calling `self._on_create(path)` from it is safe: `_on_create` is a sync callable provided by main.py, and main.py's `on_create` calls `asyncio.run_coroutine_threadsafe(capture_file(...), loop)` — thread-safe by design (same pattern `_handle_binary_move` uses for no async calls, same timer-thread origin as other binary sync handlers).

- **Files touched (directly):** `src/vault/watcher.py` — five localized edits: `_should_skip`, `on_modified`, `on_deleted`, `_handle_binary_delete`, new `_handle_binary_content_change`. No other file requires a code change for T9. `capture_file` and `capture.py` are unchanged — they are the correct re-capture engine, called via the existing `_on_create` callback.

- **Downstream:** After T9, any binary edit in a project root (editable file) refreshes its sibling within ~3 s of save (one debounce window). No-edit files in `attachment/` are skipped by `_should_skip` (`_is_in_managed_attachment` check, watcher.py:130–133) before `chg:` debounce would fire — no spurious re-capture from `attachment/`.

- **Module depth:** all five edits are inside one private class `_VaultEventHandler`. The new method `_handle_binary_content_change` is a 15-line pure check: exists? → sibling exists? → hash compare → call existing `_on_create`. Deletion test: remove it and the Excel re-save case stops triggering re-capture; Word/PPT still work via `str(path)` → `_on_create`. So it earns its keep specifically for the "last event = DELETE" save pattern.

## 2. Guardrail Checklist

| Rule | Applies? | How the recommended option satisfies it |
|---|---|---|
| C-01 vault-only writes | Yes | `_handle_binary_content_change` calls `self._on_create(path)` → main.py `on_create` → `asyncio.run_coroutine_threadsafe(capture_file(...))` → `write_note` in `vault/writer.py`. No direct vault write in watcher.py. |
| C-02 updated_by_human gate | Yes | Re-capture enters via `capture_file` → `write_note`, which enforces the gate on the sibling. `_handle_binary_content_change` does not write anything itself — cannot bypass the gate. |
| C-03 write_note merge — pipeline owns read-then-write | Yes | `_handle_binary_content_change` calls `read_note(sibling)` only to read `source_hash`; it does not call `write_note`. The re-capture pipeline (`capture_file`) owns the full read→write cycle. |
| C-12 Result type on public pipeline functions | N/A | `_handle_binary_content_change` is a private watcher method in `vault/`, not a public `handlers/` or `pipelines/` function — constraint does not apply. `capture_file` it dispatches to does return `Result`. |
| C-13 Audit log non-negotiable | Yes (vacuously) | `_handle_binary_content_change` makes no AI decision — it is a routing check. The re-capture it triggers goes through `capture_file`, which already audits every stage. The "hash unchanged → skip" branch is a watcher-internal optimization logged at DEBUG; no `audit_write` needed. |
| C-17 Never import CONFIG in tests | Yes | Tests for `_handle_binary_content_change` use `VaultConfig(root=tmp_path)` directly. The `_on_create` callback is a mock. `read_note` call reads from `tmp_path` fixtures without touching CONFIG. |

## 3. Success criteria

**You can verify (vault-visible):**

1. Given `Projects/A/goal.docx` with sibling `Projects/A/.summaries/goal.docx.md` (after T3 ships editable-in-root), When I open `goal.docx` in Word, edit one word, and press Cmd+S, Then within ~5 s the sibling's `source_hash` frontmatter field changes to a new SHA-256 value matching the saved file's content — and the sibling body reflects the updated summary.

2. Given the same `goal.docx`, When I open it, press Cmd+S immediately without making any change, Then the sibling's `source_hash` is unchanged and the sibling body is unchanged (no re-capture triggered).

3. Given `Danh sách.xlsx` in `Projects/A/`, When I open it in Excel, edit one cell, and press Cmd+S, Then the sibling refreshes (same as criterion 1) — even though Excel's save burst ends with a DELETE event.

4. Given any `.docx` or `.xlsx` open in its Office app (lock file `~$<name>.ext` present in the project folder), When the watcher is running, Then no sibling is created for the `~$` lock file, and it does not appear in the vault index.

5. Given `Projects/A/attachment/report.pdf` (no-edit file), When report.pdf is opened in Preview and viewed (not edited), Then nothing changes — no re-capture event fires for files inside `attachment/`.

**Developer must verify:**

1. After a content-change re-capture, `documents.get_by_path("Projects/A/.summaries/goal.docx.md")` returns a row whose `content_hash` has changed from its pre-edit value — confirms the DB row was updated.
2. Log line `watcher.binary_content_unchanged path=<path>` at DEBUG level fires when hash matches; no downstream `capture_file` call is made (verified by checking no audit row with `outcome=LOCATED` appears for that file in that window).
3. No `watcher.binary_delete_sibling_removed` log fires during a Word, Excel, or PPT save sequence — confirms `_handle_binary_delete`'s `path.exists()` guard suppresses the mid-burst DELETE.
4. Non-interference: `_handle_binary_content_change` and `_handle_binary_delete` fire on separate debounce keys (`chg:{path}` vs `bin:{path}`); they do not cancel each other. Verify by checking both log lines fire independently after a single atomic save.
5. For Excel (last event = DELETE): exactly one re-capture audit row appears (from the `chg:` path), and zero re-captures from the `str(path)` path (since `_on_delete` returns early via `path.exists()` guard in main.py).

## 4. Options

### Option A — `chg:` debounce key + in-watcher hash pre-check (Recommended)

**What this means:** Add a third debounce timer key (`chg:{path}`) that all three event types (MODIFY, CREATE, DELETE) reset. When it fires, the watcher reads the sibling's stored hash, computes the binary's current hash, and only calls the existing re-capture callback if they differ. This is the minimal, burst-safe design that handles all three Office save patterns correctly.

**Approach:** Five localized changes to `_VaultEventHandler`:

1. `_should_skip`: `path.name.startswith((".", "~$"))` — add the `~$` prefix to the existing tuple check.

2. `on_modified` (currently returns early for non-`.md` at line 257): for binaries that are internal to the vault, schedule `chg:` debounce instead of returning:
   ```python
   if path.suffix.lower() != ".md":
       if self._is_internal(path):
           self._debounce(f"chg:{path}", self._handle_binary_content_change, (path,))
       return
   ```

3. `on_deleted`: for binary + internal paths, also reset the `chg:` timer (in addition to the existing `bin:` debounce):
   ```python
   if _is_binary(path) and self._is_internal(path):
       self._debounce(f"bin:{path}", self._handle_binary_delete, (path,))
       self._debounce(f"chg:{path}", self._handle_binary_content_change, (path,))
   ```

4. `_handle_binary_delete`: add existence check before orphaning the sibling:
   ```python
   def _handle_binary_delete(self, path: Path) -> None:
       if path.exists():
           return  # atomic-save DELETE half — file survived burst; chg: handles re-capture
       import structlog
       structlog.contextvars.bind_contextvars(correlation_id=new_correlation_id())
       # ... existing sibling orphan logic unchanged ...
   ```

5. New `_handle_binary_content_change`:
   ```python
   def _handle_binary_content_change(self, path: Path) -> None:
       import hashlib
       import structlog
       structlog.contextvars.bind_contextvars(correlation_id=new_correlation_id())
       if not path.exists():
           return  # genuine delete — bin: handler covers sibling orphan
       sibling = _sibling_for(path, self._vault_config)
       if not sibling.exists():
           return  # not yet indexed; on_create handles first capture
       match read_note(sibling):
           case Failure():
               return  # unreadable — let on_create path handle it
           case Success(value=note):
               if note.metadata.source_hash:
                   try:
                       current = hashlib.sha256(path.read_bytes()).hexdigest()
                   except OSError:
                       return
                   if current == note.metadata.source_hash:
                       _log.debug("watcher.binary_content_unchanged path=%s", path)
                       return
       # Hash changed (or no stored hash) — trigger re-capture via existing on_create path
       self._on_create(path)
   ```
   Add `import hashlib` to the top of `watcher.py` (module-level, alongside existing imports).

**Why `chg:` firing `_on_create` is correct:** `_on_create` in main.py calls `asyncio.run_coroutine_threadsafe(capture_file(path, ...), loop)`. `capture_file` runs the full pipeline including LLM summarization, writes a new sibling with an updated `source_hash`, and upserts the DB row — exactly re-capture.

**Double-dispatch for Word/PPT (last event = CREATE):** For Word and PPT, the `str(path)` key also ends up pointing at `_on_create` (last event is CREATE). Both `str(path)` → `_on_create` AND `chg:` → `_handle_binary_content_change` → `_on_create` fire after the debounce. Two `capture_file` calls enter the asyncio event loop. The `capture_file` idempotency guard (lines 868–908) ensures the second call exits immediately (hash now matches the freshly-written sibling). Net effect: one LLM call, one no-op. Acceptable.

**Files touched:** `src/vault/watcher.py` only — five targeted edits to one private class. `capture_file`, main.py, paths.py: unchanged.

**Cost:** dev = low (five localized edits, ~30 lines added); runtime = one `read_note` + one SHA-256 per save event per indexed binary (negligible — local I/O only, no LLM); maintenance = low (no new abstraction, no new config).

**Risk:**
- Double-dispatch for Word/PPT fires two `capture_file` calls; second is no-op via idempotency guard. Acceptable, but worth logging at DEBUG to confirm.
- Race: if `chg:` fires before the Word/PPT `str(path)` → `_on_create` capture completes (unlikely — both timers fire at the same wall-clock moment; the `asyncio.run_coroutine_threadsafe` returns immediately), `_handle_binary_content_change` may see the pre-save `source_hash` and trigger a second call. The idempotency guard stops it. Low risk.
- `on_created` is NOT modified: brand-new binaries (no existing sibling) still flow through `str(path)` → `_on_create` → `capture_file` as before. `_handle_binary_content_change`'s `if not sibling.exists(): return` guard ensures it skips them. No regression for first-capture.

**Module depth:** new method is a 20-line guard chain — shallow by design. Deletion test: remove it and Excel re-saves stop triggering re-capture (but Word/PPT still work via `str(path)` → `_on_create`). It earns its keep for the Excel pattern. New interface: none — `_on_create` is the existing callback, not a new seam. `chg:` key reuses `_debounce` infrastructure already shared by all other keys.

**What it defers:** TD-039 (Windows atomic-save — different event sequences, temp file names like `~WRD*.tmp`, exclusive lock during write). T9 on Windows requires a separate probe. Explicitly out of scope per the draft.

**Constraints check:**
- [x] C-01 vault-only writes — satisfied (all writes via `write_note` in vault/writer.py; watcher only routes)
- [x] C-02 updated_by_human — satisfied (re-capture path through `write_note`'s existing gate)
- [x] C-03 write_note merge — satisfied (`_handle_binary_content_change` does not call write_note)
- [x] C-12 Result type — N/A (private watcher method, not handlers/pipelines public function)
- [x] C-13 audit — satisfied (audit lives in capture_file; watcher skip = DEBUG log, not an AI decision)
- [x] C-17 CONFIG scope in tests — satisfied (tests use VaultConfig(root=tmp_path); mock _on_create)

---

### Option B — New `on_binary_change` callback injected into VaultWatcher

**What this means:** Add `on_binary_change: Callable[[Path], None] | None` as a constructor parameter on `VaultWatcher` (and `_VaultEventHandler`). Main.py provides a callback that does the hash-compare + `capture_file` dispatch. The watcher simply routes to it, with no hash logic of its own.

**Approach:** Add parameter to `VaultWatcher.__init__` (watcher.py:465) and `_VaultEventHandler.__init__` (watcher.py:89). `on_modified` and `on_deleted` debounce to `self._on_binary_change` (or its wrapper). Main.py defines the callback inline with access to `loop`, `_make_ctx`, and the hash-compare logic.

**Files touched:** `watcher.py` (constructor signature + two debounce calls), `cli/main.py` (new `on_binary_change` callback definition + wiring into `VaultWatcher()`).

**Cost:** dev = medium (two files, signature change propagates to all VaultWatcher call sites and tests); maintenance = lower conceptually (hash logic in main.py alongside other callbacks, not in the handler class).

**Risk:** Pushes the `_handle_binary_delete` guard change into a separate concern — the guard must still be added to watcher.py (it cannot live in main.py's callback). So this option does NOT eliminate watcher.py edits; it only relocates the hash-compare. The public API surface of `VaultWatcher` grows by one optional parameter — every test that constructs `VaultWatcher` directly may need updating.

**Module depth:** `VaultWatcher` becomes a real seam for `on_binary_change` only if 2+ call sites wire different callbacks. Currently one call site (main.py) and tests use `None`. Speculative seam — fails the seam-discipline test.

**What it defers:** nothing additional.

---

### Option C — Route binary MODIFY directly to `_on_create` without pre-check

**What this means:** When a binary MODIFY fires, skip the hash compare entirely and just call `capture_file` unconditionally. Let `capture_file`'s own idempotency guard decide. Simpler code — remove `_handle_binary_content_change` entirely.

**Approach:** `on_modified` for binaries: `self._debounce(str(path), self._on_create, (path,))`. No new method, no `read_note` in the watcher.

**Files touched:** `watcher.py` — `_should_skip` (1 line), `on_modified` (2 lines change), `on_deleted` (no change for Excel gap), `_handle_binary_delete` (1 line guard).

**Cost:** dev = very low; runtime = one `capture_file` call per binary modify event, which runs the full pipeline including LLM even when content is unchanged (the idempotency guard exits early, but the guard still reads the sibling, reads the binary, and computes a hash before exiting — ~10ms I/O per spurious event).

**Risk:**
- **Excel gap unresolved:** if the last event in the burst is DELETE (as in Excel), `str(path)` → `_on_delete` fires, not `_on_create`. Excel re-saves still fail to trigger re-capture. This option does NOT fix the Excel case unless combined with a `chg:` key — at which point it becomes Option A.
- No pre-check means every `on_modified` burst (even application autosaves that touch metadata without content change) fires the idempotency guard, which reads the sibling and binary. For an executive who has 20 open Office files, that's many redundant reads. Not a correctness bug, but a mild efficiency concern.

**What it defers:** the Excel case (last-event-DELETE saves skip re-capture permanently).

---

## 5. Recommendation

**Option A.** The tradeoff: Option A adds ~20 lines of hash-compare logic inside `_VaultEventHandler` (a mild widening of that class's responsibility), but in exchange it handles all three Office save patterns correctly including Excel's last-event-DELETE sequence, prevents spurious `capture_file` calls when content is unchanged, and does so without changing `VaultWatcher`'s public API or touching main.py. Option B's cleaner interface fails the seam-discipline test (one call site = speculative seam) and still requires watcher.py edits for the `_handle_binary_delete` guard. Option C misses the Excel case entirely without becoming Option A.

## 6. Cross-check

- **Scope check:** T9 touches only `vault/watcher.py`. No config changes, no DB migrations, no new CLI command, no prompt changes. Every suggestion in the options grid traces to the probe-grounded requirement.
- **Constraint flags:** ⚠️ `hashlib` import must be added at module scope in `watcher.py` — currently absent. Add to the existing import block (lines 10–41); do not import inside the method (that works but signals missing module-level declaration). ⚠️ The `chg:` key and `bin:` key debounce independently — verify that `_debounce` with `chg:{path}` does NOT accidentally cancel `bin:{path}` timer for the same file. They use different key strings so they do not interact. CLAUDE.md note: "Two `_debounce` calls with same key cancel each other" — confirmed that `chg:{path}` and `bin:{path}` are different keys; safe.
- **Tech-debt:** DELIVERS TD-037 (binary-modify re-capture — "currently deferred", watcher.py:258 comment "Binary modify deferred — TD-C6"). Remove that comment and the early-return in `on_modified` as part of this task. TD-039 (Windows) explicitly deferred — not touched. Neutral on all other TD items.
- **Decision checks:** DECISION-025 (sibling-first ordering) — T9 calls `_on_create` which calls `capture_file` which already enforces sibling-first. No violation. DECISION-029 (`type=attachment-summary` on siblings) — re-capture rewrites sibling via `write_note`, which re-renders existing metadata. If the sibling already has `type=attachment-summary`, re-capture preserves it (C-03's read-then-write rule). No violation.
- **Probe note for future Windows work (TD-039):** Windows Office temp files (`~WRD*.tmp`, `~$*.tmp`) and exclusive file locks during save will require a separate probe. Windows temp files do NOT start with `~$` uniformly — `~WRD0001.tmp` starts with `~W`. The `~$` filter in `_should_skip` covers macOS patterns only. Windows needs a broader `~*.tmp` filter and a retry-on-lock path in `capture_file`. Out of scope for this task; document in TD-039.
- **Tests:** `VaultConfig(root=tmp_path)` + mock `_on_create` callable (C-17). Branches to cover: (1) MODIFY on indexed binary with changed content → `_on_create` called; (2) MODIFY on indexed binary with unchanged content → `_on_create` NOT called, DEBUG log emitted; (3) MODIFY on brand-new binary (no sibling) → `_on_create` NOT called by `_handle_binary_content_change` (falls through; `str(path)` → `_on_create` handles it); (4) DELETE then EXISTS (`_handle_binary_delete` guard) → no sibling orphan; (5) `~$lock.docx` created → `_should_skip` returns True, no callback; (6) no-edit binary in `attachment/` modified → `_should_skip` returns True (`_is_in_managed_attachment`), no `chg:` fired.

