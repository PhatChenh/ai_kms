# Project Registry — Spec

**Feature name:** Project Registry
**Date:** 2026-06-07
**Design doc:** [docs/1_design/project-registry.md](../1_design/project-registry.md)
**ADR:** [ADR-0009 — One domain per project](../architecture/system_adr/0009-one-domain-per-project.md)
**Behavior inventory prefix:** P2-REG (entries P2-REG-01 through P2-REG-06)

---

## Feature overview (plain English)

The Project Registry is a live, in-memory map of where things live in the vault. At startup it scans the `Projects/` and `Domain/` folders and builds a grouped list: each domain folder gets its own section, and each active project is listed under the domain it declared in its index file. Projects that have not declared a domain (or whose declared domain no longer exists) appear under a special "Uncategorized" group.

The Classify pipeline queries this map to know where to route inbox items. The Search and Briefing pipelines will query it later for the same reason.

While the vault watcher is running, the map stays current: when a new project folder appears, the map grows; when a project is archived or renamed, the map updates — all without restarting the system.

---

## Q1 Diagram — What happens inside (from design doc)

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

## Q2 Diagram — How it connects to others

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

## Components

### 1. `build_registry(vault_cfg)` — startup scan

**What it does:** Scans `Projects/` and `Domain/` folders once and returns a `ProjectRegistry` object — a grouped, immutable snapshot of the vault's destination structure.

**Steps:**
1. Call `load_valid_domains(vault_cfg.root)` to get the set of existing domain folder names.
2. Walk every direct subfolder of `Projects/`. For each:
   - Skip system folders: `attachment/`, `.summaries/`, any folder starting with `.`
   - Attempt to read `Projects/<A>/CLAUDE.md` via `read_note()`
   - If CLAUDE.md exists: extract first `domain/<D>` tag from `note.metadata.tags` where `D` is in the valid-domains set. That is the project's domain.
   - If no CLAUDE.md, no matching domain tag, or `read_note` returns `Failure`: project goes to `Uncategorized`.
3. Enumerate all `Domain/<D>/` folders (from step 1) as standalone destination groups — even those with no projects claiming them.
4. Exclude `Domain/<D>/Archive/` entries entirely.
5. Return `Success(ProjectRegistry)`.

**Return type:** `Result[ProjectRegistry]` — never raises, never returns `None`.

**Constraints:** Uses `read_note()` only (C-01). Accepts `vault_cfg: VaultConfig` as parameter — never imports `CONFIG` at module scope (C-17).

---

### 2. `ProjectRegistry` — data structure

**What it is:** An immutable, queryable snapshot of project-to-domain relationships.

**Fields:**
- `groups: dict[str, ProjectGroup]` — keyed by domain name (e.g. `"Finance"`, `"Movies"`) plus a special key `"Uncategorized"`
- `all_project_names: frozenset[str]` — flat set for fast membership checks

**`ProjectGroup`:**
- `domain_name: str` — the domain label (e.g. `"Finance"` or `"Uncategorized"`)
- `domain_path: Path | None` — absolute path to `Domain/<D>/` folder; `None` for `Uncategorized`
- `projects: list[ProjectEntry]` — ordered list of projects in this group

**`ProjectEntry`:**
- `name: str` — the project folder name as it appears in `Projects/`
- `path: Path` — absolute path to `Projects/<A>/`
- `domain_unknown: bool` — `True` if domain tag was missing, stale, or CLAUDE.md absent

---

### 3. `LiveRegistry` — live-refresh wrapper

**What it is:** A mutable wrapper around `ProjectRegistry`. Holds the current snapshot and applies incremental updates when the watcher fires events.

**Constructor:** `LiveRegistry(vault_cfg: VaultConfig)` — calls `build_registry` internally at init time.

**Mutation methods (called by `VaultWatcher`):**

| Method | When called | What it does |
|---|---|---|
| `add_project(name: str)` | New `Projects/<A>/` folder detected | Reads CLAUDE.md (if present), inserts into correct group |
| `remove_project(name: str)` | Project folder moved to `Archive/` or deleted | Removes from all groups |
| `rename_project(old: str, new: str)` | Project folder renamed | Updates entry name; domain mapping unchanged |
| `refresh_domain(project_name: str)` | `Projects/<A>/CLAUDE.md` created or modified | Re-reads domain tag, moves project to new group if changed |
| `invalidate_domain(domain_name: str)` | `Domain/<D>/` folder deleted or renamed | Moves all projects whose domain was `D` to `Uncategorized`; removes `D` group |

**Query method:** `get_groups() -> dict[str, ProjectGroup]` — returns current snapshot. Thread-safe read.

**Thread safety:** All mutations acquire an internal `threading.Lock`. `get_groups()` returns a shallow copy to avoid concurrent modification.

---

### 4. `VaultWatcher` extension — registry hookup

**What changes:** `VaultWatcher.__init__` gains one new optional parameter:

```
registry: LiveRegistry | None = None
```

When `registry` is not `None`, the watcher calls the appropriate `LiveRegistry` mutation method after its existing debounce logic fires.

**Event conditions for each mutation:**

| Watcher event | Condition | Registry call |
|---|---|---|
| `DirCreatedEvent` | Path is direct child of `Projects/` | `registry.add_project(folder_name)` |
| `DirDeletedEvent` or move-to-Archive | Path is direct child of `Projects/` | `registry.remove_project(folder_name)` |
| `DirMovedEvent` within `Projects/` | Both src and dst are direct children of `Projects/` | `registry.rename_project(old, new)` |
| `FileCreatedEvent` or `FileModifiedEvent` | Path matches `Projects/<A>/CLAUDE.md` exactly | `registry.refresh_domain(project_name)` |
| `DirDeletedEvent` or `DirMovedEvent` | Path is direct child of `Domain/` | `registry.invalidate_domain(domain_name)` |

The watcher must NOT call registry methods inside the debounce timer callback — the existing debounce fires on the main watchdog thread. Registry mutation must happen after debounce resolves (same pattern as existing `on_create`/`on_modify` callbacks).

---

### 5. `format_for_prompt(registry)` — prompt serialization

**What it does:** Converts a `ProjectRegistry` into a plain-text block for injection into the classify YAML prompt template. The output is the `{destinations}` variable that `prompts/classify.yaml` will reference.

**Format:**

```
Domains and projects:

Finance:
  - Projects: Movies Q2 Strategy, Budget Review 2026

Movies:
  - Projects: Summer Campaign, Awards Season

Uncategorized (no domain assigned yet — use the project name and
semantic reasoning to infer which domain it most likely belongs to):
  - Projects: New Initiative, Unnamed Project
```

**Rules:**
- Only non-empty groups appear (empty domain groups have no projects but still appear if the Domain/ folder exists — show them with "No active projects")
- Uncategorized group always appears last
- Each group's project list is sorted alphabetically

---

## Constraints checklist

| Constraint | How this spec satisfies it |
|---|---|
| C-01 — vault writes only via writer.py | Registry is read-only; `read_note()` used for CLAUDE.md reads |
| C-07 — prompts as YAML only | `format_for_prompt()` output injected as a variable into `prompts/classify.yaml`; not hardcoded in Python |
| C-12 — Result returns from pipeline functions | `build_registry()` returns `Result[ProjectRegistry]` |
| C-17 — no CONFIG at module scope in tests | All functions accept `vault_cfg: VaultConfig` as parameter |

---

## Files to create or modify

| File | Change |
|---|---|
| `src/vault/registry.py` | New file — `build_registry`, `ProjectRegistry`, `ProjectGroup`, `ProjectEntry`, `LiveRegistry`, `format_for_prompt` |
| `src/vault/watcher.py` | Extend `VaultWatcher.__init__` with `registry: LiveRegistry | None = None`; add 5 event-condition checks |
| `tests/test_vault/test_registry.py` | New test file — all P2-REG-01 through P2-REG-06 behaviors |
| `tests/test_vault/test_watcher.py` | Extend with registry hookup tests |

---

## Success criteria

| ID | Behavior |
|---|---|
| P2-REG-01 | Registry returns all `Projects/<A>/` folders grouped under their declared domain |
| P2-REG-02 | Project with stale domain tag (domain folder deleted) moves to Uncategorized |
| P2-REG-03 | Project with no CLAUDE.md appears in Uncategorized |
| P2-REG-04 | Only the first `domain/<D>` tag is used when CLAUDE.md has multiple domain tags |
| P2-REG-05 | Live update: new project folder added to registry without restart |
| P2-REG-06 | Live update: project CLAUDE.md created or updated changes domain grouping |

---

## Open questions

None — all design questions resolved before spec was written.
