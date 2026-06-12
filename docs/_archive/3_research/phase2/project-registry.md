# Research: Project Registry
_Last updated: 2026-06-07_

## Overview

The Project Registry is a new subsystem for Phase 2 that will keep a live, in-memory map of which project folders belong to which domain folders. Other pipelines — starting with Classify — will query this map to know where an inbox item should be routed, instead of scanning the vault on every request.

This research verified the ten assumptions the spec makes about existing code before any new code is written. All ten assumptions were confirmed accurate. There are no blocking issues — planning can proceed.

---

## Key Components

The spec proposes adding one new file (`src/vault/registry.py`) and extending one existing file (`src/vault/watcher.py`). Before writing those, the spec relies on four existing pieces of infrastructure:

- **`vault/paths.py`** — provides helper functions for working out where files live inside the vault
- **`vault/reader.py`** — opens any `.md` file from disk and returns its contents
- **`vault/frontmatter.py`** — defines the `NoteMetadata` type that holds a note's YAML metadata, including its tags
- **`core/config.py`** — defines `VaultConfig`, the configuration object that describes how the vault folders are named

---

## How It Works (existing infrastructure)

When the registry runs at startup, it will:

1. Call `load_valid_domains(vault_cfg.root)` to scan `Domain/` and get a list of folder names that actually exist.
2. Walk each direct subfolder of `Projects/`, open its `CLAUDE.md` file using `read_note()`, extract the first `domain/<D>` tag from `note.metadata.tags`, and cross-check it against the valid domains list.
3. Projects without a CLAUDE.md, without a matching domain tag, or whose `read_note()` returns a failure go into an "Uncategorized" group.
4. The watcher fires mutation callbacks after debounce timers fire — the same pattern already used for file creates and modifies.

---

## Spec Verification

Each row below corresponds to one of the ten assumptions the caller provided for verification.

| Assumption ID | Spec Claim | Verdict | Evidence |
|---|---|---|---|
| A1 | `load_valid_domains(vault_root)` exists in `vault/paths.py` and returns `frozenset[str]` of domain folder names | ✅ Validated | `paths.py:307–323` — function exists, takes `vault_root: Path`, returns `frozenset(p.name for p in domain_dir.iterdir() if p.is_dir() and not p.name.startswith("."))` |
| A2 | `_location_context(path, vault_cfg)` exists in `vault/paths.py` and returns `("project", name)` for paths under `Projects/` | ✅ Validated | `paths.py:264–304` — function exists, returns `("project", rel.parts[0])` for paths whose parent chain includes `vault_cfg.projects_path` |
| A3 | `read_note(path)` exists in `vault/reader.py` and accepts any `.md` path, returning `Result[Note]` | ✅ Validated | `reader.py:35–51` — function exists, takes `path: Path`, calls `parse(path)` which raises `FileNotFoundError` as a `Failure` — so any path is accepted; non-existent paths yield `Failure(recoverable=False)` |
| A4 | `NoteMetadata.tags` is `list[str]` in `vault/frontmatter.py`, parsing the `tags:` YAML list; domain tags `domain/<D>` are stored there | ✅ Validated | `frontmatter.py:57` — `tags: list[str] = Field(default_factory=list)`; domain is stored as a tag string (e.g. `"domain/Finance"`) not as a scalar field, per Phase Pre-2 cleanup |
| A5 | `domain:` scalar is in `_DEPRECATED_KEYS` and is filtered out on read (not accessible via `note.metadata.domain`) | ✅ Validated | `frontmatter.py:48` — `_DEPRECATED_KEYS: frozenset[str] = frozenset({"domain"})`; `NoteMetadata` has no `domain` field; `parse()` at line 114–115 splits keys into `known` (in `_KNOWN_KEYS`) and `unknown` (into `extra`), so `domain` would land in `extra` if present in YAML — but `dumps()` at line 144–145 strips it on write. On read, `note.metadata.domain` does not exist as an attribute. |
| A6 | `VaultWatcher.__init__` takes all callbacks as constructor parameters; there is NO post-construction `register_callback` method | ✅ Validated | `watcher.py:926–941` — constructor takes `on_create`, `on_modify`, `on_delete`, `on_move` as required positional parameters. No `register_callback` method exists anywhere in the class. |
| A7 | The watcher uses `threading.Timer` for debounce; mutation callbacks should fire after debounce, not inline in the watchdog event handler | ✅ Validated | `watcher.py:189–197` — `_debounce()` cancels any existing timer and starts a new `threading.Timer`; all event handlers (`on_created`, `on_modified`, `on_deleted`, `on_moved`) call `_debounce()` rather than invoking callbacks directly |
| A8 | `vault/registry.py` does NOT exist yet | ✅ Validated | `ls src/vault/` confirms: `frontmatter.py`, `indexer.py`, `move_guard.py`, `paths.py`, `reader.py`, `watcher.py`, `writer.py` — no `registry.py`. Note: `src/handlers/registry.py` exists but is the handler registry, a completely separate module. |
| A9 | `threading.Lock` pattern exists in watcher for thread safety — there is an existing pattern to follow | ✅ Validated | `watcher.py:140–157` — `_VaultEventHandler.__init__` creates `self._lock`, `self._folder_lock`, and `self._binary_move_lock` as `threading.Lock()` instances; each is used with `with self._lock:` / `with self._folder_lock:` / `with self._binary_move_lock:` guards around mutable shared state. The pattern is well-established and consistent. |
| A10 | `VaultConfig` has `projects_dir` (or `projects_path`) and `domain_dir` (or `domain_path`) fields — confirm exact field names | ✅ Validated | `config.py:86–87` — `projects_dir: str = "Projects"` and `domain_dir: str = "Domain"` are the stored field names (strings); `projects_path` (`config.py:128`) and `domain_path` (`config.py:132`) are `@property` accessors that compute `self.root / self.projects_dir` and `self.root / self.domain_dir`. The spec's component code uses `vault_cfg.projects_path` and `vault_cfg.domain_path` — both exist as properties and are the correct names to use. |

---

## Edge Cases and Silent Failure Modes

These are things that aren't immediately obvious but matter for implementation:

- **`domain:` scalar in YAML lands in `extra`, not rejected.** If an old CLAUDE.md still has `domain: Finance` in its frontmatter, `parse()` puts that key in `NoteMetadata.extra` (not a hard error). The registry must ignore `extra` entirely and only look at `tags` — otherwise it might accidentally read a stale scalar.

- **`load_valid_domains` ignores hidden folders.** Folders starting with `.` inside `Domain/` are excluded. This is consistent with the vault's `.summaries/` pattern and is the right behavior.

- **`load_valid_domains` only goes one level deep.** It calls `iterdir()` on `Domain/`, not `rglob()`. So `Domain/<D>/Archive/` subfolders do not appear as valid domain names — correct behavior for the spec.

- **`read_note` returns `Failure` for non-existent files, not `None`.** The registry's `add_project` and `build_registry` logic must pattern-match on `Failure` to handle the "no CLAUDE.md" case — it cannot use a `None` check.

- **The watcher's debounce key collision risk.** The existing `_debounce()` method uses `str(path)` as the key. The spec adds registry callbacks on directory events (`DirCreatedEvent`, etc.) which the current watcher silently drops at `on_created` line 201 (`if event.is_directory: ... return`). The new registry callbacks need to be dispatched from the directory-event branches, which currently return early. Implementation must hook in there, not inside the existing file-event paths.

---

## Dependencies and Coupling

- `registry.py` (new) will import from `vault/paths.py` (`load_valid_domains`, `_location_context`) and `vault/reader.py` (`read_note`).
- `registry.py` must NOT import `CONFIG` at module scope (constraint C-17). It will accept `vault_cfg: VaultConfig` as a parameter.
- `watcher.py` will import `LiveRegistry` from `registry.py` — a one-way dependency that does not create a cycle.
- No database writes are involved. The registry is entirely in-memory.

---

## Extension Points

- **`LiveRegistry` mutation methods** are the natural extension points. Adding new event types (e.g., domain rename) means adding a new method — existing callers are unaffected.
- **`format_for_prompt()`** is pure and stateless — no extension needed; prompt format can be changed by editing the function alone.
- **`VaultWatcher` registry hookup** is optional (`registry: LiveRegistry | None = None`). Tests that don't need live registry updates pass `None` and existing behavior is unchanged.

---

## Open Questions

None. All ten assumptions are verifiable from code alone and all verified.

---

## Technical Debt Spotted

None introduced by this feature. One existing note relevant to implementers: `handlers/registry.py` is the handler registry (for file-type dispatch) — it is a different module from the vault `registry.py` that this spec introduces. Naming is unambiguous because they live in different packages (`handlers.registry` vs `vault.registry`), but reviewers should be aware of the naming overlap.

---

## Invalidated Assumptions

_(This section is omitted — all assumptions validated. No blocking issues.)_
