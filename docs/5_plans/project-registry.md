# Plan: Project Registry
_Last updated: 2026-06-07_
_Status: [ ] pending_

**Spec:** [docs/3_specs/project-registry.md](../3_specs/project-registry.md)
**Research:** [docs/4_research/project-registry.md](../4_research/project-registry.md)
**Design doc:** [docs/1_design/project-registry.md](../1_design/project-registry.md)
**Behavior IDs covered:** P2-REG-01 through P2-REG-06

---

## Architecture

### Q1 — What happens inside (from design doc)

```
        Vault folder structure
        (Projects/ + Domain/)
               │
               ▼
     ┌───────────────────┐
     │ Scan all project  │
     │ folders in        │
     │ Projects/         │
     └────────┬──────────┘
              │
              ▼
     ┌───────────────────┐
     │ For each project: │
     │ read its CLAUDE.md│
     │ tags list         │
     └────────┬──────────┘
              │
              ▼
     ┌───────────────────┐
     │ Find first tag    │
     │ starting with     │
     │ "domain/"         │
     └────────┬──────────┘
              │
       ┌──────┴──────┐
       │             │
    FOUND         NOT FOUND
       │             │
       ▼             ▼
  ┌──────────┐  ┌──────────┐
  │ Check if │  │ Place in │
  │ Domain/  │  │ Uncat-   │
  │ folder   │  │ egorized │
  │ exists   │  └──────────┘
  └────┬─────┘
       │
  ┌────┴────┐
  │         │
EXISTS  MISSING
  │         │
  ▼         ▼
┌──────┐ ┌──────────┐
│ Add  │ │ Place in │
│ to   │ │ Uncat-   │
│ that │ │ egorized │
│ group│ └──────────┘
└──────┘
```

---

### Q2 — How it connects (from spec)

```
                          ┌──────────────────────┐
                          │  Domain Folder List  │
                          │  Knows which Domain/ │
                          │  folders are valid   │
                          └──────────┬───────────┘
                                     │ valid domain
                                     │ names
                                     ▼
    ┌─────────────────┐    ┌─────────────────────┐    ┌──────────────────────┐
    │  Note Reader    │    │                     │    │  Tag Parser          │
    │  Opens any .md  │    │  PROJECT REGISTRY   │    │  Reads the tags:     │
    │  file from disk ├───►│                     │◄───┤  list from any note  │
    └─────────────────┘    │  Builds and keeps   │    │  frontmatter         │
      reads CLAUDE.md      │  the project-to-    │    └──────────────────────┘
      for each project     │  domain map         │      extracts domain tags
                           │                     │
                           └────────┬────────────┘
                                    │        ▲
                       current      │        │ vault events
                       project map  │        │ (add / remove /
                                    │        │  rename / domain change)
                                    │   ┌────┴──────────────────┐
                                    │   │  Vault Watcher        │
                                    │   │  Watches for folder   │
                                    │   │  and file changes     │
                                    │   └───────────────────────┘
                                    │
                    ┌───────────────┼────────────────────┐
                    ▼               ▼                     ▼
         ┌─────────────────┐  ┌ ─ ─ ─ ─ ─ ─ ─ ┐  ┌ ─ ─ ─ ─ ─ ─ ─ ─┐
         │  Classify       │    Search           │    Daily Briefing  │
         │  Decides which  │  │ Filters results │  │ Groups content  │
         │  project inbox  │    by domain        │    by domain       │
         │  items go to    │  └ ─ ─ ─ ─ ─ ─ ─ ┘  └ ─ ─ ─ ─ ─ ─ ─ ─┘
         └─────────────────┘
```

Dashed boxes = planned consumers, not built yet. Only Classify is in scope for Phase 2.

---

### Q3 — Why build it this way

```
# Project Registry — Why Build It This Way
Scope: Shows the rules and existing interfaces that shaped this design.
       Does NOT cover internal steps (see Q1) or connections (see Q2).

How to read this:
  Center box       = the feature being built (two parts: startup + live)
  Surrounding boxes = rules and existing patterns it must conform to
  Lines             = which rule applies where

  ┌─────────────────────────────┐      ┌────────────────────────────────┐
  │ Domain Folder Scanner       │      │ Note Reader                    │
  │ Domain/ list comes from one │      │ CLAUDE.md always opened via    │
  │ shared helper — never        │      │ the vault reader, never        │
  │ re-implemented inline        │      │ directly from disk             │
  └────────────┬────────────────┘      └───────────────┬────────────────┘
               │ Rule: paths.py is                     │ Rule: C-01 —
               │ the single source                     │ vault layer owns
               │ for Domain/ names                     │ all file reads
               │                                       │
               └──────────────────┬────────────────────┘
                                  │
                                  ▼
                       ┌──────────────────────────────────────┐
                       │           PROJECT REGISTRY            │
  ┌────────────────────┤                                       ├─────────────────────┐
  │                    │  Left half: Startup scan              │                     │
  │                    │  Right half: Live refresh             │                     │
  │                    └──────────────────────────────────────┘                     │
  │                                  │                                               │
  │                                  │                                               │
  ▼                                  ▼                                               ▼
┌────────────────────────┐  ┌─────────────────────────────┐  ┌──────────────────────────────┐
│ Tags List, Not Scalar  │  │ Result Wrapper Required     │  │ Watcher Hookup Rule          │
│ Domain declared as     │  │ build_registry() must       │  │ No post-construction         │
│ "domain/Finance" tag   │  │ return Success or Failure,  │  │ registration method exists   │
│ — "domain:" scalar     │  │ never raise or return None  │  │ — registry passed as a       │
│ is deprecated and      │  │ (C-12)                      │  │ constructor argument         │
│ ignored on read        │  │                             │  │ (on_create / on_modify       │
└────────────────────────┘  └─────────────────────────────┘  │ pattern)                     │
                                                              └──────────────────────────────┘
                                                                          │
                                                                          ▼
                                      ┌──────────────────────────────────────────────────────┐
                                      │ Directory Event Injection Rule                        │
                                      │ Watcher already has if event.is_directory: return     │
                                      │ blocks in on_created, on_deleted, on_moved.           │
                                      │ Registry calls go INSIDE those blocks —               │
                                      │ not as new parallel branches after them.              │
                                      └──────────────────────────────────────────────────────┘
                  │
                  ▼
  ┌───────────────────────────────────┐
  │ Thread Safety Pattern             │
  │ Use threading.Lock() — same as    │
  │ the three locks already in the    │
  │ watcher. Not RLock, not asyncio.  │
  └───────────────────────────────────┘
       │
       ▼
  ┌───────────────────────────────────┐
  │ No Config at Module Scope (C-17)  │
  │ vault/registry.py accepts         │
  │ vault_cfg as a parameter —        │
  │ never imports CONFIG directly     │
  └───────────────────────────────────┘
```

---

## Approach

Build the registry in three self-contained phases: first the pure data structures and startup scanner (no I/O complexity, fully testable in isolation), then the live-update wrapper that adds thread-safe mutation, and finally the watcher hookup that fires those mutations from real vault events. Each phase ends with a passing test suite before the next starts. The `format_for_prompt()` serializer is bundled into Phase 1 as it is pure and stateless.

---

## Phases

### Phase 1 — Core data model + startup scan

**Goal:** A working `build_registry(vault_cfg)` function and the three data classes it returns, fully tested against a fake vault on disk. Also includes `format_for_prompt()`. This is the foundation every later phase builds on.

**Implements:** Spec components 1, 2, and 5. Satisfies P2-REG-01, P2-REG-02, P2-REG-03, P2-REG-04.

**Extension points:**
- `build_registry()` — `[extensible: config]` — vault structure is read from `vault_cfg`, not hardcoded
- `ProjectRegistry`, `ProjectGroup`, `ProjectEntry` — `[closed]` — plain dataclasses; no variant needed
- `format_for_prompt()` — `[closed]` — formatting is a display concern; changing format means editing this function

**Design — startup scan flow (see Q1 above):**

```
Phase 1 produces:

  src/vault/registry.py
  ├── build_registry(vault_cfg)  → Result[ProjectRegistry]
  ├── ProjectRegistry            dataclass with .groups + .all_project_names
  ├── ProjectGroup               dataclass with .domain_name + .domain_path + .projects
  ├── ProjectEntry               dataclass with .name + .path + .domain_unknown
  └── format_for_prompt(reg)     → str  (pure, no I/O)

  tests/test_vault/test_registry.py
  └── tmp_path fixtures with Projects/ + Domain/ subdirs
      covering P2-REG-01 through P2-REG-04
```

**Steps (TDD — write the failing test first, then the implementation):**

1. Create `tests/test_vault/test_registry.py` with an empty test file. Import guard only — confirm it runs clean.

2. **RED** — Write `test_p2_reg_01_projects_grouped_by_domain`: build a `tmp_path` vault with `Projects/Alpha/` (CLAUDE.md with `tags: [domain/Finance]`) and `Domain/Finance/`. Call `build_registry(vault_cfg)`. Assert `result` is `Success`, registry has `"Finance"` group, and `"Alpha"` is in its projects list.
   - `vault_cfg` is a `VaultConfig` constructed directly in the test — never import `CONFIG` at module scope.

3. **GREEN** — Create `src/vault/registry.py`. Define `ProjectEntry`, `ProjectGroup`, `ProjectRegistry` as Python `dataclass`es. Write `build_registry(vault_cfg)` stub that returns `Failure("not implemented")`.

4. **GREEN (continued)** — Implement `build_registry`:
   - Call `load_valid_domains(vault_cfg.root)` (from `vault/paths.py:307`) to get `frozenset[str]` of valid domain names.
   - Iterate `vault_cfg.projects_path.iterdir()` — skip non-directories and skip names starting with `.` plus `attachment` and `.summaries`.
   - For each project folder: call `read_note(project_path / "CLAUDE.md")` (from `vault/reader.py:35`). On `Failure` or missing → `domain_unknown=True`. On `Success` → scan `note.metadata.tags` for first `t.startswith("domain/")` where `t[len("domain/"):]` is in `valid_domains`. If found, assign to that domain. If not, `domain_unknown=True`.
   - Also enumerate all valid domain folders as `ProjectGroup` entries (even those with zero projects).
   - Collect uncategorized projects into a `"Uncategorized"` group.
   - Return `Success(ProjectRegistry(...))`.

5. Run `uv run pytest tests/test_vault/test_registry.py -x` — P2-REG-01 passes.

6. **RED** — Write `test_p2_reg_02_stale_domain_tag_goes_to_uncategorized`: vault has `Projects/Beta/` (CLAUDE.md with `tags: [domain/OldDomain]`) but NO `Domain/OldDomain/` folder. Assert `Beta` lands in `Uncategorized`.

7. **GREEN** — Confirm the existing logic already handles this (the `t[len("domain/"):] in valid_domains` check). Test passes.

8. **RED** — Write `test_p2_reg_03_no_claude_md_goes_to_uncategorized`: `Projects/Gamma/` exists, no CLAUDE.md inside. Assert `Gamma` in `Uncategorized`, `domain_unknown=True`.

9. **GREEN** — `read_note` returns `Failure` for missing file; existing `domain_unknown=True` path handles it. Test passes.

10. **RED** — Write `test_p2_reg_04_first_domain_tag_wins`: CLAUDE.md has `tags: [domain/Finance, domain/Movies]`. Both domains exist. Assert project lands under `Finance`, not `Movies`.

11. **GREEN** — The "first matching tag" scan already handles this. Test passes.

12. **RED** — Write `test_domain_folders_appear_even_without_projects`: `Domain/Movies/` exists but no project claims it. Assert `Movies` group exists in registry with empty projects list (not absent entirely).

13. **GREEN** — Add the enumeration of all valid domains step to `build_registry` so each `Domain/<D>/` folder becomes a `ProjectGroup` regardless of whether any project claims it.

14. **RED** — Write `test_format_for_prompt_basic` and `test_format_for_prompt_uncategorized_last`: build a minimal registry with two domain groups and one uncategorized entry. Assert output is a string containing `"Finance:"`, `"Uncategorized"` appears after domain groups, and alphabetical ordering within each group.

15. **GREEN** — Implement `format_for_prompt(registry: ProjectRegistry) -> str`:
    - Sort non-Uncategorized groups alphabetically.
    - For each group: emit `"<DomainName>:"` then `"  - Projects: ..."` (alphabetically sorted project names); if group has no projects emit `"  - No active projects"`.
    - Append `Uncategorized` group last with its inline note about semantic reasoning.
    - Return the full string.

16. **Commit:** "feat: vault/registry.py — build_registry, data classes, format_for_prompt (P2-REG-01 through P2-REG-04)"

**Files to create or modify:**
- `src/vault/registry.py` — **New file**
- `tests/test_vault/test_registry.py` — **New file**

**Success criteria:**
- [ ] P2-REG-01: projects grouped under declared domain
- [ ] P2-REG-02: stale domain tag → Uncategorized
- [ ] P2-REG-03: no CLAUDE.md → Uncategorized
- [ ] P2-REG-04: first matching domain tag wins
- [ ] All domain folders appear in registry even with zero projects
- [ ] `format_for_prompt` produces correct string; Uncategorized always last; alphabetical within group
- [ ] `build_registry` returns `Success(ProjectRegistry)` — never raises, never returns `None`
- [ ] No module-scope `CONFIG` import anywhere in `registry.py` or its test file
- [ ] `uv run pytest tests/test_vault/test_registry.py` — all green

**Status:** [ ] pending

---

### Phase 2 — LiveRegistry (thread-safe live-update wrapper)

**Goal:** A `LiveRegistry` class that wraps the startup scan and exposes five mutation methods for watcher events. All mutations are thread-safe. `get_groups()` returns a shallow copy to avoid concurrent modification.

**Implements:** Spec component 3. Satisfies P2-REG-05 (live add) and P2-REG-06 (CLAUDE.md change triggers domain update).

**Extension points:**
- `LiveRegistry` mutation methods — `[extensible: protocol]` — adding new event types means adding a new method without changing callers
- `threading.Lock` — `[closed]` — same pattern as `watcher.py:140-157`; do not switch to `RLock` or `asyncio.Lock`

**Design — live update flow:**

```
LiveRegistry holds:
  _registry: ProjectRegistry  (current snapshot)
  _vault_cfg: VaultConfig
  _lock: threading.Lock       (guards all mutations)

Public interface:
  add_project(name)           → None   (reads CLAUDE.md if present)
  remove_project(name)        → None
  rename_project(old, new)    → None
  refresh_domain(project_name)→ None   (re-reads CLAUDE.md, may change group)
  invalidate_domain(domain)   → None   (all affected projects → Uncategorized)
  get_groups()                → dict   (shallow copy, thread-safe read)
```

**Steps:**

1. **RED** — Write `test_live_registry_add_project`: create a `LiveRegistry`, then add a new project folder to the tmp vault, call `live.add_project("NewProject")`. Assert it appears in `get_groups()` output.

2. **GREEN** — Add `LiveRegistry` to `src/vault/registry.py`:
   - Constructor calls `build_registry(vault_cfg)` and stores the result. If `build_registry` returns `Failure`, store an empty registry (log the error; do not raise).
   - `add_project(name: str)`: acquire lock → re-read `Projects/<name>/CLAUDE.md` via `read_note()` → determine domain → insert `ProjectEntry` into appropriate group → release lock.
   - `remove_project(name: str)`: acquire lock → remove from all groups → release lock.
   - `rename_project(old: str, new: str)`: acquire lock → find entry by `old` name → update `.name` field → release lock.
   - `refresh_domain(project_name: str)`: acquire lock → re-read CLAUDE.md → move project to new group if domain changed → release lock.
   - `invalidate_domain(domain_name: str)`: acquire lock → move all projects from that group to `Uncategorized` → remove the domain group → release lock.
   - `get_groups()`: acquire lock → return `dict(self._registry.groups)` (shallow copy) → release lock.

3. Run test — passes.

4. **RED** — Write `test_live_registry_remove_project`: add a project to the registry, call `remove_project()`, assert it no longer appears in `get_groups()`.

5. **GREEN** — Implementation covers it. Test passes.

6. **RED** — Write `test_p2_reg_06_refresh_domain_changes_group`: project starts in `Finance` group. Rewrite its CLAUDE.md to have `tags: [domain/Movies]`. Call `live.refresh_domain("Alpha")`. Assert `Alpha` now appears under `Movies`, not `Finance`.

7. **GREEN** — `refresh_domain` re-reads CLAUDE.md and moves the entry. Test passes.

8. **RED** — Write `test_live_registry_invalidate_domain`: project is under `Finance`. Call `invalidate_domain("Finance")`. Assert project moves to `Uncategorized` and `Finance` group is absent.

9. **GREEN** — `invalidate_domain` implementation covers it. Test passes.

10. **RED** — Write `test_live_registry_thread_safe_concurrent_adds`: spawn 10 threads each calling `add_project()` with distinct names. Assert all 10 appear in final `get_groups()` with no `KeyError` or duplicate.

11. **GREEN** — The `threading.Lock` in all mutations ensures this. Test passes.

12. **Commit:** "feat: vault/registry.py — LiveRegistry with thread-safe mutations (P2-REG-05, P2-REG-06)"

**Files to modify:**
- `src/vault/registry.py` — Add `LiveRegistry` class
- `tests/test_vault/test_registry.py` — Add Phase 2 test cases

**Success criteria:**
- [ ] P2-REG-05: `add_project()` adds without restart
- [ ] P2-REG-06: `refresh_domain()` moves project to correct group after CLAUDE.md changes
- [ ] `invalidate_domain()` moves all affected projects to Uncategorized
- [ ] Concurrent mutation does not cause `KeyError` or data loss
- [ ] `get_groups()` returns a copy — mutations after the call do not change the returned dict
- [ ] `uv run pytest tests/test_vault/test_registry.py` — all green

**Status:** [ ] pending

---

### Phase 3 — VaultWatcher hookup

**Goal:** `VaultWatcher` gains one new optional parameter `registry: LiveRegistry | None = None`. When a registry is provided, the watcher calls the appropriate mutation method when project folders or CLAUDE.md files change. All existing watcher tests must remain green — the registry parameter is opt-in and changes nothing when `None`.

**Implements:** Spec component 4. Satisfies P2-REG-05 (watcher-driven live add) and the full live-refresh contract.

**Extension points:**
- `VaultWatcher.__init__` new parameter — `[extensible: config]` — `registry=None` default preserves all existing callers
- Event routing inside directory branches — `[closed]` — must stay inside the existing `if event.is_directory: return` blocks; no new parallel dispatch paths

**Design — watcher hookup (per spec component 4 event table):**

```
on_created  → if is_directory AND path is direct child of Projects/:
                registry.add_project(folder_name)   [INSIDE existing dir branch, before return]

on_deleted  → if is_directory AND path is direct child of Projects/:
                registry.remove_project(folder_name)
              if is_directory AND path is direct child of Domain/:
                registry.invalidate_domain(folder_name)

on_moved    → if is_directory AND both src+dst are direct children of Projects/:
                registry.rename_project(old_name, new_name)
              if is_directory AND src is direct child of Domain/:
                registry.invalidate_domain(src_folder_name)
              if is_directory AND src is in Projects/ but dst is NOT in Projects/:
                (move to Archive/ counts as remove)
                registry.remove_project(src_folder_name)

on_modified → if path matches Projects/<A>/CLAUDE.md exactly:
                registry.refresh_domain(project_folder_name)

CRITICAL: All of these inject into the EXISTING if event.is_directory: return blocks.
  on_created dir path is lines 200-203: _register_pending_folder() then return.
  on_deleted dir path is lines 383-384: currently just `return`.
  on_moved dir path is lines 401-402: currently just `return`.
  on_modified has NO directory branch (already filters at line 352: if event.is_directory: return).
  The CLAUDE.md modify hookup goes in the file path (after the is_directory early return).
```

**Steps:**

1. **RED** — Write `test_watcher_registry_hookup_add_project`: create a `LiveRegistry` and a `VaultWatcher` with `registry=live`. Create a new directory inside `Projects/`. After debounce, assert the project appears in `live.get_groups()`.
   - Use `unittest.mock.patch` for debounce timer (or set `debounce_seconds=0` in the test constructor) to avoid real sleeps.
   - Patch target: `vault.watcher.<name>` — never the source module (per TD-033 / CLAUDE.md "What Claude gets wrong").

2. **GREEN** — Add `registry: LiveRegistry | None = None` to `_VaultEventHandler.__init__` signature (and store as `self._registry`). Also add it to `VaultWatcher.__init__` and pass through to the handler constructor. In `on_created` inside the `if event.is_directory:` block (lines 200-203): after `_register_pending_folder(folder_path)` and before `return`, add:
   ```python
   if self._registry is not None:
       parent = folder_path.parent
       if parent == self._vault_config.projects_path and not folder_path.name.startswith("."):
           self._debounce(
               f"reg:add:{folder_path}",
               self._registry.add_project,
               (folder_path.name,),
           )
   ```
   Note the unique debounce key prefix `reg:` — prevents collision with existing file-event keys.

3. Run test — passes.

4. **RED** — Write `test_watcher_registry_hookup_remove_project`: delete a project directory. Assert project removed from `live.get_groups()`.

5. **GREEN** — In `on_deleted` inside `if event.is_directory:` (line 383-384), before `return`:
   - If `folder_path.parent == projects_path`: `registry.remove_project(name)`
   - If `folder_path.parent == domain_path`: `registry.invalidate_domain(name)`

6. **RED** — Write `test_watcher_registry_hookup_rename_project`: rename a project folder inside `Projects/`. Assert new name appears, old name gone.

7. **GREEN** — In `on_moved` inside `if event.is_directory:` (line 401-402), before `return`:
   - Both `src.parent` and `dst.parent` are `projects_path` → `registry.rename_project(src.name, dst.name)`
   - `src.parent == projects_path` but `dst.parent` is not → `registry.remove_project(src.name)` (move to Archive)
   - `src.parent == domain_path` → `registry.invalidate_domain(src.name)`

8. **RED** — Write `test_watcher_registry_hookup_claude_md_modified`: write a CLAUDE.md inside `Projects/Alpha/` with a new domain tag. Assert `refresh_domain` is called and project moves to new group.

9. **GREEN** — In `on_modified` after the `if event.is_directory: return` guard: check if `path` matches `Projects/<A>/CLAUDE.md` exactly. If so and registry is not None:
   ```python
   if self._registry is not None:
       parts = path.relative_to(self._vault_config.projects_path).parts
       if len(parts) == 2 and parts[1] == "CLAUDE.md":
           self._debounce(
               f"reg:refresh:{parts[0]}",
               self._registry.refresh_domain,
               (parts[0],),
           )
   ```

10. **RED** — Write `test_watcher_registry_none_no_effect`: pass `registry=None` (default). Fire all event types. Assert existing `on_create`, `on_modify`, `on_delete`, `on_move` callbacks still fire correctly with no errors.

11. **GREEN** — All guards are `if self._registry is not None:` — existing behavior is unchanged.

12. Run full test suite: `uv run pytest tests/ -m "not smoke"`. All existing watcher tests must remain green.

13. **Commit:** "feat: vault/watcher.py — LiveRegistry hookup via constructor param (P2-REG-05, P2-REG-06)"

**Files to modify:**
- `src/vault/watcher.py` — `_VaultEventHandler.__init__` and `VaultWatcher.__init__` gain `registry` param; 4 event handler methods gain registry dispatch
- `tests/test_vault/test_watcher.py` — Add Phase 3 registry hookup tests

**CRITICAL implementation notes:**
- Debounce key prefix must be `reg:` — avoids collision with `bin:` and `binmod:` and plain `str(path)` keys.
- Directory branch injection locations (exact lines in current codebase):
  - `on_created` dir branch: after `self._register_pending_folder(folder_path)` at line 202, before `return` at line 203
  - `on_deleted` dir branch: at line 383-384 — currently just `return`; add dispatch before
  - `on_moved` dir branch: at line 401-402 — currently just `return`; add dispatch before
  - `on_modified` CLAUDE.md check: after `if event.is_directory: return` at line 352-353
- `on_created` for a `DirCreatedEvent` already calls `_register_pending_folder` for the folder-capture flow — the registry hookup is an additional side effect in the same branch, not a replacement.

**Success criteria:**
- [ ] `VaultWatcher(registry=None)` — all existing callers work unchanged, no new params required
- [ ] New project folder in `Projects/` → `add_project()` called after debounce
- [ ] Project folder deleted from `Projects/` → `remove_project()` called
- [ ] Project folder renamed within `Projects/` → `rename_project(old, new)` called
- [ ] Project folder moved outside `Projects/` → `remove_project()` called
- [ ] `Domain/<D>/` deleted or moved → `invalidate_domain()` called
- [ ] `Projects/<A>/CLAUDE.md` created or modified → `refresh_domain()` called
- [ ] `uv run pytest tests/ -m "not smoke"` — all green (956+ tests)

**Status:** [ ] pending

---

## Open Questions

None — all design questions resolved in the design doc and spec. Research validated all 10 assumptions with no invalidated ones.

---

## Out of Scope

Per spec and research:
- No database writes — the registry is entirely in-memory; no SQL migrations needed for this feature
- No `kms registry` CLI command — Classify will query `LiveRegistry` directly; a CLI display tool for the registry is not in scope
- Search and Daily Briefing consumers — dashed boxes in Q2; not wired until those pipelines are built
- Reconcile extensions for stale/missing/multi-domain CLAUDE.md tags — tracked as TD-044, TD-045, TD-046; not in this plan
- Image capture and other Phase 2 pipeline concerns
- `format_for_prompt()` is built here but the `prompts/classify.yaml` prompt that uses it is Phase 2 Classify scope

---

## Tech Debt Interactions

- **TD-034** — this plan retires TD-034. The project registry that was missing is now built; location-confidence tagging in the capture pipeline can look up domain from the registry instead of defaulting to `Uncategorized`. (Not wired in this plan — that is Phase 2 Classify work.)
- **TD-044, TD-045, TD-046** — logged during design; not addressed here. After this plan ships, reconcile can be extended to detect stale/missing/multi-domain CLAUDE.md tags and surface them to the Briefing.
- **TD-018** — Domain list refresh (live `Domain/` folder detection). Phase 3 of this plan adds `invalidate_domain` hookup which partially addresses TD-018 for domain deletion/rename. New `Domain/<D>/` folders created while the watcher runs will still require a watcher add-domain hookup (not in this plan's scope).
