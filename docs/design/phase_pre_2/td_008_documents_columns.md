# Design: TD-008 — Add `project`, `status`, `key_topics` to documents Table

_Date: 2026-06-03_
_Status: Design complete — awaiting spec_
_Phase: Pre-Phase 2 (first migration step of Phase 2 setup)_

---

## Decision

**Chosen: Option A (revised)** — All three columns (`project`, `status`, `key_topics`) in **three separate migration files** (003, 004, 005), stored as `TEXT`, `key_topics` as a JSON array string. `status` column added as plain `TEXT` with no vocabulary constraint — Phase 2 classify defines and writes the values.

Reason: all three are derived directly from in-memory `NoteMetadata` at `upsert()` time — no new prompts, no new fields, no new pipeline stages. Three single-statement migration files (per `db.py` atomicity recommendation), one update to `documents.py`.

---

## Implications

### What these columns actually are

The `documents` table is a queryable mirror of the vault — it lets the search and MCP layers find notes cheaply via SQL without reading every frontmatter file. Currently it mirrors: `vault_path`, `title`, `summary`, `note_type`, `confidence`, `updated_by_human`, `content_hash`, `batch_id`. Three frontmatter fields are missing:

- **`project`** — mirrors `NoteMetadata.project` (frontmatter.py:54). A string like `"Alpha"` set when a note lives under `Projects/Alpha/`. Already populated by `apply_location_tags` and `reconcile_stale_tags`; just not written to the DB.
- **`status`** — mirrors `NoteMetadata.status` (frontmatter.py:64). Not yet written by any pipeline (always `None` at capture time). Phase 2 classify will write values like `"inbox"`, `"classified"`, `"flagged"`. The column must exist before classify can write it.
- **`key_topics`** — NOT a separate frontmatter field. These are the **Layer 3 free topic tags** (no prefix) from `NoteMetadata.tags`. The `extract_metadata.yaml` prompt generates 5–10 of these per note (e.g. `quarterly-review`, `stakeholder-management`). At `upsert()` time, filter `meta.tags` for tags without `domain/` or `type/` prefix → store as JSON array string.

### What changes in code

**Three migration files (one DDL statement each):**

- `storage/migrations/003_add_project.sql`: `ALTER TABLE documents ADD COLUMN project TEXT;`
- `storage/migrations/004_add_status.sql`: `ALTER TABLE documents ADD COLUMN status TEXT;`
- `storage/migrations/005_add_key_topics.sql`: `ALTER TABLE documents ADD COLUMN key_topics TEXT;`

`status` is plain `TEXT` with no CHECK constraint. Phase 2 classify owns the vocabulary (likely `"inbox"`, `"classified"`, `"flagged"`) — adding a CHECK now would require a migration to extend it later.

Note: `ALTER TABLE ADD COLUMN` in SQLite always produces NULLs in existing rows. No backfill. Existing documents rows will have `NULL` for all three until the next capture or reconcile re-upserts them.

**`storage/documents.py`** — three changes:

1. `DocumentRow` dataclass: add three new fields:
   - `project: str | None = None`
   - `status: str | None = None`
   - `key_topics: list[str] = field(default_factory=list)` (deserialized from JSON on read)

2. `_row_from_sqlite()` function (line 42): read three new columns — `key_topics` must `json.loads()` the TEXT value (handle NULL → empty list). Note: this is a module-level private function, not a class method on `DocumentRow`.

3. `upsert()` and `replace_path()` functions: add three new params to `INSERT OR REPLACE`:
   - `meta.project` → `project`
   - `meta.status` → `status`
   - `json.dumps([t for t in meta.tags if not t.startswith("domain/") and not t.startswith("type/")])` → `key_topics`

**Extraction logic for key_topics:**
```python
_topic_tags = [t for t in meta.tags if not t.startswith("domain/") and not t.startswith("type/")]
key_topics_json = json.dumps(_topic_tags)
```

This runs at upsert time, in `documents.py`, inline. No helper function needed (single-use logic).

### Guards and constraints that apply

- **DB integrity (C-06)**: schema changes only via migration files. Three `ALTER TABLE` statements in `003_documents_columns.sql`. Migration runner auto-discovers and applies on next `init_db()`.
- **Migration atomicity** (from `db.py:26`): "Keep migration files to a single atomic DDL statement." Three separate files (003, 004, 005) — each is one statement. Satisfies the constraint.
- **Idempotency**: `INSERT OR REPLACE` in upsert already replaces the whole row. Adding three columns doesn't change this contract.

### Downstream effects

- **Phase 2 classify pipeline**: can read `documents.status` to check if a note has already been classified. Can write `status="classified"` or `status="flagged"` via `documents.upsert()` after classify runs.
- **Phase 3 search**: `project` and `key_topics` enable SQL filtering (`WHERE project = 'Alpha'`, `WHERE key_topics LIKE '%quarterly-review%'`). Without these columns, search would require reading every frontmatter file.
- **Phase 4 MCP**: `kms_search()` tool can filter by project and topic without vault scans.
- **Existing rows**: NULL until next re-upsert. If Phase 3 search is built before all notes are re-processed, some rows will have NULL columns. Acceptable — NULL means "not yet indexed". Phase 3 should handle NULL gracefully.

### Module depth check

`storage/documents.py` is a deep module: its interface (`upsert`, `replace_path`, `get_by_path`, `delete_by_path`, `rename`, `get_all_paths`) hides all SQL from callers. Adding three columns extends the implementation without changing the interface signature (same functions, same return types). Both `upsert()` and `replace_path()` contain an `INSERT OR REPLACE` — both must be updated. `rename()` is a path-only `UPDATE SET vault_path` and does not need changing. Deletion test: removing documents.py would push SQL into every pipeline caller — it earns its keep.

---

## Guardrail Checklist

- [x] **DB integrity (migration-only schema changes)** — satisfies: `003_documents_columns.sql` migration file
- [x] **C-03 (vault writes)** — not applicable: documents.py is DB, not vault
- [x] **Result type returns** — satisfies: `upsert()` already returns `Result[int]`; no signature change
- [x] **CONFIG import scope in tests** — watch: any test that creates a `DocumentRow` directly needs the new fields with defaults. All three have defaults (`None` or `[]`) so existing tests won't break.
- [x] **Atomicity risk** — acceptable: ADD COLUMN is non-destructive; partial migration leaves unused NULLs, not corruption

_Success criteria moved to `docs/usability_test/phase_pre_2/td_008_documents_columns.md`._

---

## Options Explored

### Option A — All three columns, three migration files _(Chosen)_

`003_add_project.sql`, `004_add_status.sql`, `005_add_key_topics.sql`. One update to `documents.py`. All three columns available before Phase 2 starts.

**Defers:** backfilling existing rows. Existing documents rows have NULL until next re-process.

---

### Option B — `project` + `status` only; defer `key_topics`

Two `ALTER TABLE` instead of three. Simpler migration.

**Rejected because:** `key_topics` extraction is one `json.dumps([...])` line in `upsert()`. There is no complexity argument for deferring it. Deferring means a second migration and a second `upsert()` update later. No benefit.

---

### Option C — Store full `tags` column (JSON array of all tags) instead of pre-filtered `key_topics`

Store `json.dumps(meta.tags)` as a `tags TEXT` column. More flexible — Phase 3 search can filter however it wants client-side.

**Rejected because:** TD-008 specifically names `key_topics` as the column. Domain tags (`domain/<D>`) and type tags (`type/<X>`) are already queryable via taxonomy and `note_type` column respectively. Storing the full tags list adds redundancy. The pre-filter at write time is a one-liner with no runtime cost. If Phase 3 needs more tag flexibility, it can be extended then.

---

## Known Tradeoffs

- Existing rows have `NULL` in new columns until re-processed. Any Phase 3 query must handle NULL gracefully (treat as "not yet indexed").

- `status` has no CHECK constraint. Invalid values can be written without DB error. Phase 2 classify must enforce the vocabulary in code (not schema).

---

## Open Questions

None — migration strategy (three files) and status vocabulary deferral both resolved.

---

## ADR References

- TD-008 source: `plans/storage_level.md` Out of Scope
- DB migration pattern: `storage/db.py:23–40` (single atomic DDL per file recommendation)
