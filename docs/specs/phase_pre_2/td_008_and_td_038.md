# Phase Pre-2 — DB Schema Prep + Domain Scalar Cleanup

_Date: 2026-06-03_
_Status: Spec complete — ready for `/research` then `/plan-from-specs`_
_Covers: TD-008 (documents columns) + TD-038 (drop domain scalar)_

---

## Purpose

This phase prepares the system for the Phase 2 Classify pipeline by completing two independent cleanup tasks. First (TD-008): it adds three columns — `project`, `status`, and `key_topics` — to the `documents` SQL table so the classifier can read and write classification state without scanning vault files. Second (TD-038): it removes the redundant `domain:` frontmatter scalar that drifts out of sync with the canonical `domain/<D>` tag; after this phase, domain lives only as a tag. Neither task introduces new AI calls, new pipeline stages, or new CLI commands.

---

## Already built (reuse, do not rebuild)

### TD-008 track

| Function/Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `init_db()` / `_run_migrations()` | `src/storage/db.py` | Auto-discovers `[0-9][0-9][0-9]_*.sql` in `storage/migrations/` and applies in lex order on startup | New migration files 003–005 are applied automatically on next `init_db()` call | deep |
| `DocumentRow` | `src/storage/documents.py:26–40` | Dataclass for one row in the `documents` table | Extend with three new optional fields | deep |
| `_row_from_sqlite()` | `src/storage/documents.py:42` | Module-level private function that builds `DocumentRow` from a SQLite row | Extend to read `project`, `status`, `key_topics` columns | shallow |
| `upsert()` | `src/storage/documents.py:84–101` | `INSERT OR REPLACE` writing one note's metadata row | Extend to include three new bound parameters | deep |
| `replace_path()` | `src/storage/documents.py:197–251` | Atomic delete-old + `INSERT OR REPLACE` for renamed notes | Must be extended alongside `upsert()` — both share the same column list | deep |
| `NoteMetadata.project` | `src/vault/frontmatter.py:54` | Existing `str \| None = None` field on `NoteMetadata` | Source value for the `project` column at upsert time | deep |
| `NoteMetadata.status` | `src/vault/frontmatter.py:64` | Existing `str \| None = None` field on `NoteMetadata` | Source value for the `status` column at upsert time | deep |
| `NoteMetadata.tags` | `src/vault/frontmatter.py` | List of all tags on a note | Filter non-`domain/` non-`type/` tags → JSON string for `key_topics` column | deep |

### TD-038 track

| Function/Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `_KNOWN_KEYS` | `src/vault/frontmatter.py:32` | Frozenset of recognized frontmatter keys; unrecognized keys go to `extra` | Remove `"domain"` so existing `domain:` YAML routes to `extra` instead of `NoteMetadata.domain` | deep |
| `NoteMetadata` | `src/vault/frontmatter.py` | Dataclass for note metadata | Remove `domain: str \| None = None` field entirely | deep |
| `field_validator` | `src/vault/frontmatter.py:68` | Pydantic v2 validator invoked for known keys | Remove `"domain"` from its key list | deep |
| `dumps()` | `src/vault/frontmatter.py` | Serializes `NoteMetadata` back to YAML frontmatter dict | Add `_DEPRECATED_KEYS` filter **after** `d.update(metadata.extra)` to strip surviving `domain:` from old notes on next write | deep |
| `store()` | `src/pipelines/capture.py` (~line 423) | Builds `NoteMetadata` from `MetadataResult` and calls `documents.upsert()` | Remove `domain=mr.ai_domain` kwarg from the `NoteMetadata(...)` constructor call | deep |
| `_store_nonmd()` | `src/pipelines/capture.py` (~line 542) | Stores non-.md binary capture results | Replace `note_meta.domain` reference with a tag-filter on `note_meta.tags` | deep |
| `_merge_metadata()` | `src/vault/writer.py:48` | Merges incoming metadata onto existing note metadata before write | Remove `domain=incoming.domain` from the `NoteMetadata(...)` constructor at ~line 78 | deep |
| `MetadataResult.ai_domain` | `src/pipelines/capture.py:60` | Internal pipeline field — the domain name string from `apply_location_tags` | NOT removed; still drives `domain/<D>` tag construction inside `apply_location_tags` | deep |
| `apply_location_tags()` | `src/pipelines/capture.py:277` | Pipeline stage that reads folder structure and appends `domain/<D>` to `ai_tags` | NOT modified; its output (`note_meta.tags` containing `domain/<D>`) is what `_store_nonmd()` must now filter instead of reading `note_meta.domain` | deep |

---

## Feature overview

### TD-008 — Adding DB columns for Phase 2

Today the `documents` table mirrors most note metadata into SQL but is missing three fields that Phase 2 Classify will need: `project` (which project folder a note belongs to), `status` (classifier state, initially NULL), and `key_topics` (the free-form topic tags generated during capture).

The change works in two layers. At the database level, three one-statement migration files add the three columns — one column per file, satisfying the atomicity rule in `db.py`. At the code level, the `DocumentRow` dataclass gains three typed fields, the row-reader gains three column reads (with JSON parsing for `key_topics`), and both write paths — `upsert` and `replace_path` — gain three new bound parameters.

`key_topics` extraction: at write time, filter `meta.tags` for tags that do NOT start with `domain/` or `type/`. These are the Layer-3 free topic tags (e.g., `quarterly-review`, `stakeholder-management`). Store as `json.dumps([...])`. Domain and type tags are already queryable via other columns and the tag taxonomy.

Existing rows remain NULL in all three columns until re-processed — this is intentional. Phase 3 search must handle NULL gracefully.

### TD-038 — Removing the domain scalar

Today a note's domain is stored twice: as `domain: finance` in YAML frontmatter (via `NoteMetadata.domain`, written by `store()`) AND as `domain/finance` inside `tags:` (written by `apply_location_tags`). The scalar drifts silently because `reconcile_stale_tags` only corrects `tags` and `project` — it never re-syncs `domain:`.

The fix removes `domain` as a first-class field everywhere in code. After this change:
- No pipeline writes `domain:` to frontmatter on new notes
- Old notes that already have `domain:` in their YAML gradually lose it as a lazy migration: the next time any pipeline re-writes a note, `dumps()` strips the key via a `_DEPRECATED_KEYS` filter applied after `d.update(metadata.extra)`
- Domain information survives intact as the `domain/<D>` tag (already present, already correct)

The `_DEPRECATED_KEYS` filter placement is critical: it must filter `d` **after** `d.update(metadata.extra)` so it catches any `domain:` key that survived in the note's `extra` dict from parsing.

---

## Out of scope

- **`MetadataResult.ai_domain` removal** — `ai_domain` is internal pipeline state used by `apply_location_tags` to append the `domain/<D>` tag. Kept as-is. (No phase assigned for removal; no frontmatter impact.)
- **One-shot vault migration** — stripping `domain:` from all existing notes in bulk requires bypassing `write_note`, violating C-03. Lazy migration via `dumps()` is the chosen approach. Notes never re-touched keep `domain:` as an orphaned YAML key. (Cosmetic only; no code reads it after this phase.)
- **`status` vocabulary enforcement at DB level** — no `CHECK` constraint on the `status` column. Phase 2 Classify owns the vocabulary (`"inbox"`, `"classified"`, `"flagged"`) and enforces it in code.
- **Backfilling `project`/`key_topics` for existing rows** — `ALTER TABLE ADD COLUMN` initializes all rows to NULL. No backfill path. Phase 3 search must handle NULL gracefully (treat as "not yet indexed").
- **Phase 2 Classify pipeline itself** — this phase only adds the infrastructure Classify will use.
- **`rename()` in documents.py** — path-only `UPDATE SET vault_path`, no `INSERT OR REPLACE`. Not touched.

---

## Constraints

| Constraint | Rule | Source |
|---|---|---|
| C-05 (DB Integrity) | All schema changes via versioned `.sql` files in `storage/migrations/`. No inline `ALTER TABLE` in `.py` files. | `CONSTRAINTS.md` C-05 |
| C-05 atomicity corollary | One DDL statement per migration file. Three columns = three files (003, 004, 005). | `src/storage/db.py:26` comment |
| C-03 (Write Safety) | `_DEPRECATED_KEYS` filter runs inside the serializer (`dumps()`), not via direct file writes. No new `write_note` calls or write-safety bypasses. | `CONSTRAINTS.md` C-03 |
| C-12 (Result returns) | `upsert()` and `replace_path()` already return `Result[int]`. New parameters must not change return type or signature in a breaking way. | `CONSTRAINTS.md` C-12 |
| C-17 (Test scope) | Tests must not import `CONFIG` at module scope. Pass explicit paths (`db_path=tmp_path / "kb.db"`) to bypass CONFIG. | `CONSTRAINTS.md` C-17 |
| updated_by_human gate | Human-locked notes that are never re-written keep `domain:` in their YAML forever. This is intentional — they must not be force-rewritten. | `CONSTRAINTS.md` C-02 |
| Stage order — `apply_location_tags` before `store()` | `apply_location_tags` must run before `store()` / `_store_nonmd()` in the capture pipeline stage list. The tag-filter in step 7 assumes `domain/<D>` is already in `note_meta.tags`. Reordering stages silently drops domain tags from stored notes. Mark the call site with `# COUPLING:` comment. | Coupling note — `src/pipelines/capture.py` stage list |

---

## Assumptions

All assumptions pre-validated by research (`docs/research/phase_pre_2/td_008_and_td_038.md`). Listed for plan-phase traceability.

| ID | Assumption | Source | What would prove it wrong |
|---|---|---|---|
| A1 | Only `001_initial.sql` and `002_batches.sql` exist in `storage/migrations/` — next numbers are 003, 004, 005 | Research A1 | A third `.sql` file already present |
| A2 | `documents` table has no `project`, `status`, `key_topics` columns in `schema.sql` | Research A2 | Any of those column names in the `documents` DDL |
| A3 | `DocumentRow` dataclass ends at `batch_id` — no `project`, `status`, `key_topics` fields | Research A3 | Any of those field names in the dataclass |
| A4 | `_row_from_sqlite()` at `documents.py:42` is a module-level private function, not a class method on `DocumentRow` | Research A4 (resolved) | `_row_from_sqlite` is a class method or doesn't exist at that line |
| A5 | `replace_path()` at `documents.py:197` has its own `INSERT OR REPLACE` with the same column list as `upsert()` | Research A5 (resolved) | `replace_path` uses `UPDATE` only |
| A6 | `_KNOWN_KEYS` at `frontmatter.py:32` contains `"domain"` | Research B1 | `"domain"` absent from `_KNOWN_KEYS` |
| A7 | `NoteMetadata.domain: str \| None = None` exists at `frontmatter.py:55` | Research B2 | No `domain` field on `NoteMetadata` |
| A8 | `field_validator` at `frontmatter.py:68` includes `"domain"` | Research B3 | `"domain"` absent from the validator |
| A9 | `store()` passes `domain=mr.ai_domain` to `NoteMetadata(...)` at `capture.py:428` | Research B4 | No such kwarg at that call site |
| A10 | `_store_nonmd()` references `note_meta.domain` in domain tag construction at `capture.py:646–647` | Research B5 | Reference already uses `note_meta.tags` filter |
| A11 | `_merge_metadata()` passes `domain=incoming.domain` to `NoteMetadata(...)` at `writer.py:78` | Research B6 | No such kwarg at that call site |
| A12 | `apply_location_tags` runs before `store()` in the pipeline stage list, so `note_meta.tags` already contains `domain/<D>` when `_store_nonmd()` runs | Research coupling note | `note_meta.tags` has no `domain/<D>` tag at `_store_nonmd` time |

---

## Component dependency order

### 1. Migration files — 003, 004, 005 (TD-008)

**Goal.** Create the three SQL files that add `project`, `status`, and `key_topics` columns to the `documents` table.

**Build.** Create three new files in `src/storage/migrations/`:
- `003_add_project.sql`: `ALTER TABLE documents ADD COLUMN project TEXT;`
- `004_add_status.sql`: `ALTER TABLE documents ADD COLUMN status TEXT;`
- `005_add_key_topics.sql`: `ALTER TABLE documents ADD COLUMN key_topics TEXT;`

All three are plain `TEXT` columns, no `DEFAULT`, no `CHECK` constraint. SQLite initializes existing rows to NULL. The migration runner applies them automatically on the next `init_db()` call.

**Depends on.** Nothing — files are standalone.

**Assumes.** A1, A2.

**Done when.** Running `sqlite3 data/kb.db ".schema documents"` after `kms capture <any-file>` shows all three new columns. No `OperationalError: table documents has no column named project` at runtime.

---

### 2. `DocumentRow` dataclass extension (TD-008)

**Goal.** Make the Python dataclass that represents a documents row aware of the three new columns, with typed fields and safe defaults.

**Build.** In `src/storage/documents.py`, extend the `DocumentRow` dataclass with three new fields after `batch_id`:
- `project: str | None = None`
- `status: str | None = None`
- `key_topics: list[str] = field(default_factory=list)`

`key_topics` is stored as a JSON string in SQLite but exposed as a Python list to callers. Deserialization (JSON → list) happens in step 3.

**Depends on.** Step 1 (migration files define the schema the dataclass mirrors).

**Assumes.** A3.

**Done when.** `DocumentRow(vault_path="x", title="y", batch_id="b", project="Alpha", key_topics=["quarterly-review"])` constructs without error. Existing call sites that do not pass the new kwargs still work (defaults apply).

---

### 3. `_row_from_sqlite()` extension (TD-008)

**Goal.** Teach the row-reading function to populate the three new fields when reading from the database.

**Build.** In `src/storage/documents.py::_row_from_sqlite()` (line 42), read the three new columns from the SQLite row and assign to `DocumentRow`:
- `project`: pass through as-is (NULL → `None`)
- `status`: pass through as-is (NULL → `None`)
- `key_topics`: if column value is `None` or empty string, yield `[]`; otherwise `json.loads(value)`

**Depends on.** Step 2 (`DocumentRow` must have the new fields).

**Assumes.** A4.

**Done when.** A test that inserts a row with `key_topics='["quarterly-review","stakeholder-management"]'` directly via raw SQLite and then calls `get_by_path()` returns a `DocumentRow` with `key_topics == ["quarterly-review", "stakeholder-management"]`. A row with `NULL` in `key_topics` returns `key_topics == []`.

---

### 4. `upsert()` + `replace_path()` extension (TD-008)

**Goal.** Wire the three new columns into both write paths so every captured or renamed note writes `project`, `status`, and `key_topics`.

**Build.** In `src/storage/documents.py`:

**In `upsert()`:** extend the `INSERT OR REPLACE` SQL to include `project`, `status`, `key_topics` in the column list with three new bound parameters:
- `meta.project` → `project`
- `meta.status` → `status`
- `json.dumps([t for t in meta.tags if not t.startswith("domain/") and not t.startswith("type/")])` → `key_topics`

**In `replace_path()`:** identical extension — same three columns and same bound parameter expressions.

The `key_topics` extraction is a one-line inline expression in each function. No helper function.

**Depends on.** Steps 2–3 (dataclass and reader in place for test round-trips).

**Assumes.** A5.

**Interface shape.** `upsert(outcome, batch_id)` and `replace_path(old_path, new_path, meta, batch_id)` signatures unchanged. Callers see no interface change.

**Done when.**
- After capturing a note from `Projects/Alpha/` with tags `["domain/finance", "type/note", "quarterly-review"]`: `get_by_path()` returns `project="Alpha"`, `status=None`, `key_topics=["quarterly-review"]`.
- A note with only `domain/` and `type/` tags gets `key_topics=[]` (not NULL — the column is explicitly set).
- After a `replace_path()` rename, the renamed row still carries the correct `project` and `key_topics`.
- A CLUELESS binary note (tags `["type/attachment-summary"]` only) gets `key_topics=[]`.

---

### 5. `_DEPRECATED_KEYS` + `dumps()` filter (TD-038)

**Goal.** Prevent `domain:` from ever being written back to frontmatter when a note is re-processed, lazily migrating old notes over time.

**Build.** In `src/vault/frontmatter.py`:

Add at module level near `_KNOWN_KEYS`:
```python
_DEPRECATED_KEYS = frozenset({"domain"})
```

In `dumps()`, locate the `d.update(metadata.extra)` call. Immediately **after** that call, add:
```python
for key in _DEPRECATED_KEYS:
    d.pop(key, None)
```

Placement is critical: must be after `d.update(metadata.extra)` so it catches `domain:` that survived in the note's `extra` dict. Placing it before is a silent no-op.

**Depends on.** Nothing — this step is standalone and safe to ship before step 6.

**Done when.**
- A `NoteMetadata` with `extra={"domain": "finance"}` serialized via `dumps()` produces YAML without a `domain:` key.
- A `NoteMetadata` with `extra={"other_key": "value"}` still includes `other_key:` in output (the filter is not a blanket wipe of `extra`).

---

### 6. Remove `domain` from `NoteMetadata` + `_KNOWN_KEYS` + `field_validator` (TD-038)

**Goal.** Stop `domain:` from being parsed as a first-class field. After this change, any `domain:` key in a note's YAML routes to `NoteMetadata.extra`, where step 5's filter strips it on next write.

**Build.** In `src/vault/frontmatter.py`, make three removals in one edit:
1. Remove `"domain"` from `_KNOWN_KEYS` (line 34)
2. Remove `domain: str | None = None` from the `NoteMetadata` dataclass (line 55)
3. Remove `"domain"` from the `field_validator` decorator (line 68)

**Depends on.** Step 5 must be in place first. Without step 5, removing `"domain"` from `_KNOWN_KEYS` causes it to land in `extra`, which would then be written back into frontmatter on the next `dumps()` call — exactly the drift the lazy migration must prevent.

**Assumes.** A6, A7, A8.

**Done when.**
- Parsing a YAML with `domain: finance` produces a `NoteMetadata` with no `domain` attribute (`AttributeError` if accessed).
- A round-trip parse → `dumps()` of that YAML produces output with no `domain:` key (step 5 erases it).

---

### 7. Update `store()` and `_store_nonmd()` in `capture.py` (TD-038)

**Goal.** Remove both usages of `NoteMetadata.domain` in the capture pipeline so captured notes no longer attempt to set the removed field.

**Build.** In `src/pipelines/capture.py`:

**`store()` (~line 423):** Remove the `domain=mr.ai_domain` keyword argument from the `NoteMetadata(...)` constructor call.

**`_store_nonmd()` (~lines 646–647):** The current domain-tag construction is:
```python
[f"domain/{note_meta.domain}"] if note_meta.domain else []
```
Replace with a tag-filter:
```python
[t for t in note_meta.tags if t.startswith("domain/")]
```
This is safe because `apply_location_tags` runs before `store()` in the pipeline stage list and has already appended the `domain/<D>` tag to `note_meta.tags`.

Add a coupling comment at this line:
```python
# COUPLING: apply_location_tags must run before _store_nonmd; domain/<D> tag must be in note_meta.tags
```

`MetadataResult.ai_domain` (`capture.py:60`) is NOT touched — `apply_location_tags` still sets and uses it.

**Depends on.** Step 6 (field no longer exists on `NoteMetadata`; passing `domain=...` raises `TypeError`).

**Assumes.** A9, A10, A12.

**Done when.** `kms capture <md-file-in-Projects/Alpha/>` completes without `TypeError`. The captured note's frontmatter `tags:` contains `domain/<D>`. `NoteMetadata` parsed from that note has no `domain` attribute.

---

### 8. Update `_merge_metadata()` in `writer.py` (TD-038)

**Goal.** Remove the last remaining consumer of `NoteMetadata.domain` in the write layer.

**Build.** In `src/vault/writer.py::_merge_metadata()` (line 48), remove `domain=incoming.domain` from the `NoteMetadata(...)` constructor call at ~line 78.

**Depends on.** Step 6 (field no longer exists).

**Assumes.** A11.

**Done when.** Calling `write_note(path, NoteMetadata(...), actor="ai")` on a note that previously had `domain: finance` in its YAML does not raise `TypeError` and does not write `domain:` back to the file.

---

### 9. Update tests (TD-008 + TD-038)

**Goal.** Bring the full test suite back to green after all code changes. Fix existing tests that referenced the removed `domain` field; add new tests for the new DB columns and lazy-migration behavior.

**Build.**

**TD-008 — new tests in `tests/test_storage/test_documents.py`:**
- `DocumentRow` with new fields and defaults — all three default to `None` / `[]`
- `_row_from_sqlite` reads `key_topics` JSON string and NULL correctly
- `upsert()` writes `project`, `status`, `key_topics` and round-trips via `get_by_path()`
- `replace_path()` preserves all three fields after a rename
- CLUELESS binary case: `upsert()` with tags `["type/attachment-summary"]` → `key_topics == []`

**TD-038 — fix existing tests:**
- `tests/test_vault/test_frontmatter.py:106` — remove `domain="Y"` kwarg from `NoteMetadata(...)` constructor
- `tests/test_vault/test_frontmatter.py:125` — remove or rewrite `meta2.domain == meta.domain` assertion; domain is now only in `tags`
- `tests/test_pipelines/test_capture.py:657` — change `metadata.domain == "finance"` → `"domain/finance" in result.value.metadata.tags`

**TD-038 — new tests:**
- `dumps()` strips `domain:` from `extra` on re-serialize (covers `_DEPRECATED_KEYS` placement)
- `dumps()` does NOT strip non-deprecated keys from `extra` (filter is not a blanket wipe)
- Parsing YAML with `domain: finance` produces no `domain` attribute on `NoteMetadata`

**Depends on.** Steps 1–8 all complete.

**Done when.** `uv run pytest tests/ -m "not smoke"` passes with no failures. No test references `NoteMetadata.domain`, `DocumentRow` missing new field defaults, or `metadata.domain`.

---

## Handoff notes

- **Two independent tracks.** Steps 1–4 (TD-008) and steps 5–8 (TD-038) have no cross-dependency. Both tracks can be built and tested independently. Step 9 (tests) waits for both.

- **Contract with Phase 2 Classify:** `documents.status` must exist and be writable via `documents.upsert()` before Classify ships. This phase delivers that.

- **Contract with Phase 3 Search:** `project` and `key_topics` columns must exist for SQL filtering. Phase 3 must handle NULL gracefully in all queries — rows captured before this phase will have NULL in all three columns until re-upserted.

- **`replace_path()` gap is critical:** If `replace_path()` is not updated alongside `upsert()` in step 4, any rename during capture silently clears `project`, `status`, and `key_topics`. Both functions must be updated atomically.

- **`dumps()` filter placement:** The filter must be after `d.update(metadata.extra)`. Placing it before produces a silent no-op. The step-5 test exists precisely to catch this implementation mistake.

- **Pipeline stage order assumption:** Step 7's tag-filter in `_store_nonmd()` is safe only because `apply_location_tags` always runs before `store()`. This is enforced by the pipeline stage list in `capture.py`. The `# COUPLING:` comment at that line marks this assumption for future maintainers.

- **Suggested research before planning:** Verify exact line numbers for `store()` and `_store_nonmd()` call sites in `capture.py` — the design doc cites approximate lines. The planner needs confirmed line numbers for surgical edit instructions.
