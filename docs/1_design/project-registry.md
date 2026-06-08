# Project Registry — Design Doc

**Feature name:** Project Registry
**Date:** 2026-06-07
**ID prefix:** P2-REG
**ADR:** [ADR-0009 — One domain per project](../architecture/system_adr/0009-one-domain-per-project.md)
**Behavior inventory prefix:** P2-REG (entries P2-REG-01 through P2-REG-06 in `docs/system_behavior/behavior_inventory.yaml`)

---

## What this is (plain English)

Right now the system can capture and summarise notes dropped into the inbox, but it has no memory of where projects live or what domain they belong to. When Phase 2 (Classify) tries to route an inbox item to the right project folder, it needs a map: "which projects exist, and which domain does each belong to?"

The Project Registry is that map. It reads the vault's `Projects/` folder once at startup, builds a grouped list of all projects sorted under their declared domain, and hands that list to the Classify pipeline so it can make routing decisions. If the vault is being watched live, the registry refreshes automatically when folders are created or project index files change.

---

## How it fits in (implications summary)

**Existing code reused — no new dependencies introduced:**

- `vault/paths.py` — `load_valid_domains(vault_root)` already returns the set of valid domain folder names. `_location_context(path, vault_cfg)` already detects `("project", name)` for any path under `Projects/`. Both are pure path arithmetic with no I/O side effects.
- `vault/reader.py` — `read_note(path)` can be called on any `.md` file including `Projects/<A>/CLAUDE.md`. Returns `Result[Note]` (Success or Failure). No special-casing needed.
- `vault/frontmatter.py` — `NoteMetadata.tags: list[str]` already parses the `tags:` YAML list from any note. The `domain/<D>` tags that identify a project's domain are already stored there.
- `core/result.py` — `Result[T]` / `Success` / `Failure` pattern applies to all public functions.

**New code introduced:**

- `vault/registry.py` — new module (see DQ-3). Contains `build_registry(vault_cfg)` and the `ProjectRegistry` / `ProjectGroup` data classes. Optionally a `LiveRegistry` wrapper for watcher integration.

**Cross-cutting constraints that apply:**

- **C-01 (read_note only, no direct file reads):** Registry reads `CLAUDE.md` via `read_note()`, never `path.read_text()` or direct `open()`.
- **C-12 (Result returns from pipeline functions):** `build_registry` returns `Result[ProjectRegistry]`, not a bare dict or a raised exception.
- **C-17 (no CONFIG at module scope in tests):** Registry functions accept `vault_cfg: VaultConfig` as a parameter rather than importing `CONFIG` at module scope.

---

## Design questions and options

### DQ-1: Domain mapping storage format in `Projects/<A>/CLAUDE.md`

**The question:** How does a project declare which domain it belongs to in its `CLAUDE.md` file?

---

**Option A — `domain/<D>` tag in `tags:` list (Recommended)**

Plain English: The project's index file uses the same `tags:` field that ordinary notes use. A project in the Finance domain has `domain/Finance` somewhere in its `tags:` list. The registry reads this list using the existing `NoteMetadata.tags` parser — no new fields, no new parsing logic.

Example `CLAUDE.md` frontmatter:
```yaml
---
tags:
- domain/Finance
- project/Alpha
---
```

Pros:
- Zero new schema. `NoteMetadata.tags` is already parsed by `frontmatter.parse()` and fully tested.
- `read_note()` already returns tags. The registry needs no new parsing code — just `[t for t in note.metadata.tags if t.startswith("domain/")]`.
- Consistent with how notes elsewhere in the vault declare domain membership.
- The behavior inventory fixtures (P2-REG-01, P2-REG-02, P2-REG-04) already assume this format — they use `fixture_frontmatter: tags: [domain/Finance]`.
- ADR-0009 first-wins rule maps directly: iterate `tags`, find first `domain/<D>` entry where `Domain/<D>/` folder exists.

Cons:
- A human editing `CLAUDE.md` must know to use the `domain/` prefix rather than a plain scalar. The asymmetry (CLAUDE.md uses tags, not a `domain:` key) requires one line of documentation.
- If someone adds a `domain/Finance` tag for a different reason, it is silently interpreted as a domain assignment.

Compatibility with ADR-0009: Full. The one-domain rule — "first tag whose domain folder exists" — is trivially implemented by scanning the tags list in order.

---

**Option B — Dedicated `domain:` frontmatter scalar**

Plain English: Add a new field `domain: Finance` (not a tag) directly to the CLAUDE.md frontmatter. The registry reads this field instead of the tags list.

Example:
```yaml
---
domain: Finance
tags:
- project/Alpha
---
```

Pros:
- Intent is explicit — a human reading the file sees `domain:` and understands immediately.
- No prefix parsing needed. The value is already the domain name.

Cons:
- `domain:` was removed from `NoteMetadata` in Phase Pre-2 (listed in `_DEPRECATED_KEYS` in `frontmatter.py`). Reintroducing it requires reversing that migration decision or adding a CLAUDE.md-specific parser that deliberately ignores the deprecation filter.
- Breaks the uniformity principle: CLAUDE.md would need a different schema from all other notes.
- Two parallel ways to declare domain membership (tags for notes, scalar for projects) creates confusion.

Compatibility with ADR-0009: Would work but requires new schema work that contradicts a recent migration.

---

**Option C — Separate `context.yaml` sidecar**

Plain English: Each project has a `Projects/<A>/context.yaml` file (no CLAUDE.md involvement). The registry reads this YAML file instead.

Example:
```yaml
domain: Finance
status: active
```

Pros:
- Completely separate from the note schema — no risk of YAML field collisions.
- Machine-written content stays out of CLAUDE.md.

Cons:
- Introduces a new file type that Obsidian users cannot see or edit naturally.
- No existing reader for YAML sidecars — requires new parsing code.
- Now there are two files to maintain per project (CLAUDE.md + context.yaml). Non-coders will miss the sidecar.
- Out of step with the vault-as-markdown-files design principle.

Compatibility with ADR-0009: Would work, but adds unnecessary complexity for no gain.

---

**DQ-1 domain-lookup flow (Option A — chosen):**

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

### DQ-2: Live refresh hookup to the watcher

**The question:** After startup, how does the registry stay current when projects are added, renamed, or reclassified while the system is running?

---

**Option A — Observer/callback pattern: registry registers with VaultWatcher (Recommended)**

Plain English: The registry is passed into `VaultWatcher` at construction time (like the other callbacks — `on_create`, `on_modify`, `on_delete`, `on_move`). The watcher calls the registry when it sees a directory creation, a CLAUDE.md creation or modification, or a project folder deletion.

Assessment from reading the watcher code: `VaultWatcher.__init__` takes all callbacks as constructor parameters — there is no `register_callback()` method. Adding registry callbacks requires either (a) extending the constructor with new parameters, or (b) wrapping the existing `on_create`/`on_modify` callbacks to also notify the registry. Neither is difficult, but both touch `VaultWatcher`'s constructor signature.

Pros:
- Registry is always current. No lag between vault change and registry update.
- Consistent with watcher's existing debounce and thread-safety model.

Cons:
- Couples registry refresh to watcher internals. Any watcher API change breaks registry hookup.
- Requires extending `VaultWatcher.__init__` or wrapping callbacks at the CLI layer.
- More complex to test in isolation — must wire up a watcher mock.

---

**Option B — Registry polls vault on a timer**

Plain English: A background timer re-scans `Projects/` and `Domain/` every N seconds and rebuilds the in-memory registry. No watcher coupling.

Pros:
- Zero coupling to watcher internals.
- Simple to implement and test.

Cons:
- Lag between vault change and registry update equals the poll interval.
- Background timer adds a new thread to manage.
- Per the CLAUDE.md constraint "Schedulers come last. Build manual CLI first, then automate" — timers are a scheduler variant; they belong after the manual version is proven.

---

**Option C — Rebuild on each Classify invocation**

Plain English: There is no persistent registry object. Every time the Classify pipeline needs the registry, it calls `build_registry(vault_cfg)` on the spot and reads current state from disk. The call is cheap — it scans directory entries and reads one small CLAUDE.md per project. No in-memory state, no background threads.

Pros:
- Simplest possible implementation. No state to invalidate.
- Always perfectly current — reads disk at call time.
- No watcher coupling needed now. Live refresh (Option A) can be layered on later once Classify is proven.
- Consistent with "Build manual CLI first, then automate."
- No new threads, no timers.

Cons:
- Disk reads on every Classify call. For vaults with hundreds of projects this is a noticeable but not prohibitive cost (one directory scan + N small file reads).
- If Classify is called in a tight loop, the repeated scans accumulate. In practice, Classify processes one inbox item at a time — this is not a hot path.

---

**DQ-2 flow (Option A — chosen: live refresh via VaultWatcher extension):**

```
  kms watch starts up
         │
         ▼
  ┌──────────────────────┐
  │ build_registry()     │
  │ scans Projects/      │
  │ + Domain/ at startup │
  └──────────┬───────────┘
             │
             ▼
  ┌──────────────────────┐
  │ LiveRegistry created │
  │ holds in-memory map  │
  │ (always current)     │
  └──────────┬───────────┘
             │
             ▼
  ┌──────────────────────┐
  │ VaultWatcher created │
  │ receives registry as │
  │ constructor argument │
  └──────────┬───────────┘
             │
  ┌──────────┴──────────────────────────────┐
  │  While watcher runs:                    │
  │                                         │
  │  New Projects/<A>/ folder               │
  │    → registry.add_project(name)         │
  │                                         │
  │  Projects/<A>/CLAUDE.md changed         │
  │    → registry.refresh_domain(name)      │
  │                                         │
  │  Project folder moved to Archive/       │
  │    → registry.remove_project(name)      │
  │                                         │
  │  Project folder renamed                 │
  │    → registry.rename_project(old, new)  │
  │                                         │
  │  Domain/<D>/ deleted or renamed         │
  │    → registry.invalidate_domain(D)      │
  └─────────────────────────────────────────┘
             │
             ▼
  Classify calls registry.get_groups()
  → always sees current vault state
```

Note: `VaultWatcher.__init__` gains one new optional parameter: `registry: LiveRegistry | None = None`. The mutation methods above are the full public interface of `LiveRegistry`. The core `build_registry()` function is unchanged — `LiveRegistry` wraps it and delegates to `build_registry` at startup.

---

### DQ-3: Where the registry module lives

**The question:** Which source layer owns `vault/registry.py`?

---

**Option A — `vault/registry.py` (Recommended)**

Plain English: The registry reads vault folder structure and CLAUDE.md files — both vault-layer operations. It belongs in the same layer as `paths.py` (path helpers), `reader.py` (note reader), and `frontmatter.py` (tag parser).

Pros:
- Dependency direction is correct: `vault/` → `core/` → nothing outside. Registry imports `reader.py`, `paths.py`, `core/result.py` — all legal downward imports.
- No new layer crossings.
- Search and Briefing pipelines (`pipelines/search.py`, `pipelines/briefing.py`) can both import `vault.registry` directly, same as they import `vault.reader`.

Cons:
- None identified. This is the natural home.

---

**Option B — `core/registry.py`**

Plain English: Put the registry in the `core/` layer (shared primitives) so it can be imported by any layer without circular imports.

Pros:
- Maximum reusability — any layer can import it.

Cons:
- `core/` is for primitives that have no vault dependency (`result.py`, `audit.py`, `confidence.py`). Putting vault-reading code in `core/` breaks the layer contract.
- Would require `core/registry.py` to import `vault/reader.py` — an upward dependency from core to vault. That is a design violation.

---

**Option C — Inline in `pipelines/classify.py`**

Plain English: The registry logic lives only inside the Classify pipeline — a local function, not a separate module.

Pros:
- Zero new files for a first-pass implementation.

Cons:
- Search and Briefing pipelines also need to know the project map. Inlining in classify.py requires copy-pasting into each pipeline — or importing from a pipeline into another pipeline, which creates a lateral dependency.
- Violates the extension point rule: adding a new pipeline consumer requires modifying classify.py.

---

## Chosen options (recommendation)

| Question | Choice | Rationale |
|---|---|---|
| DQ-1: Domain tag format | **Option A** — `domain/<D>` in `tags:` list | Reuses existing `NoteMetadata.tags` parsing; behavior inventory fixtures already assume this format; zero schema changes. |
| DQ-2: Live refresh | **Option A** — Observer/callback via VaultWatcher extension | Delivers live refresh as locked in requirements; watcher gains one optional `registry` constructor param; `LiveRegistry` wraps `build_registry` for startup scan. |
| DQ-3: Module location | **Option A** — `vault/registry.py` | Correct dependency direction; consistent with `reader.py`, `paths.py`; importable by Search and Briefing without layer violations. |

---

## Success criteria (reference only)

Behavior inventory entries P2-REG-01 through P2-REG-06 — see `docs/system_behavior/behavior_inventory.yaml`.

---

## Open questions

**OQ-1 (resolved 2026-06-07):** Yes — all `Domain/<D>/` folders appear as valid destination groups regardless of whether any project claims them. Grill locked: "Include all `Domain/<D>/` folders as direct destinations." `ProjectRegistry` must enumerate all existing domain folders, not only those referenced by a project tag.

**OQ-2 (resolved 2026-06-07):** Live refresh via VaultWatcher extension chosen. `LiveRegistry` wraps `build_registry`; `VaultWatcher.__init__` gains `registry: LiveRegistry | None = None`. Spec must define exact watcher event conditions for each mutation method.
