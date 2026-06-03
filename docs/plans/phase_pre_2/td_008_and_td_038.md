# Plan: Phase Pre-2 — DB Schema Prep + Domain Scalar Cleanup
_Last updated: 2026-06-03_
_Status: [x] complete — 2026-06-03, 5 commits, 797 tests pass_

Covers: TD-008 (add `project`, `status`, `key_topics` columns to `documents` table) +
        TD-038 (remove redundant `domain:` frontmatter scalar; lazy migration via `dumps()`).

---

## Architecture

```
# Phase Pre-2 — Implementation Architecture
Scope: Two independent build tracks (TD-008 and TD-038) with no cross-track
       dependencies until Step 9. Does NOT cover Phase 2 Classify, Phase 3
       Search, or backfill of existing rows.

How to read this
  ─ [NEW]    = file created fresh
  ─ [EXTEND] = existing file modified; existing interface preserved
  ─ Downward arrows within a track = "must be done before"
  ─ No arrows cross between Track A and Track B until Step 9

         TRACK A — TD-008 (Add DB Columns)       TRACK B — TD-038 (Remove Domain Scalar)
         ────────────────────────────────         ────────────────────────────────────────

  ┌──────────────────────────────────┐    ┌──────────────────────────────────────────┐
  │ [NEW] 3 Migration SQL Files      │    │ [EXTEND] frontmatter.py — dumps()        │
  │ storage/migrations/              │    │ Add _DEPRECATED_KEYS = frozenset({"domain"│
  │   003_add_project.sql            │    │ Filter d after d.update(metadata.extra)   │
  │   004_add_status.sql             │    │ Strips domain: on every re-write          │
  │   005_add_key_topics.sql         │    └──────────────────┬────────────────────────┘
  │ Applied automatically by init_db │                       │
  └──────────────────┬───────────────┘          domain: now stripped on next write
                     │ columns exist in schema               ▼
                     ▼                          ┌──────────────────────────────────────────┐
  ┌──────────────────────────────────┐          │ [EXTEND] frontmatter.py — NoteMetadata   │
  │ [EXTEND] DocumentRow dataclass   │          │ Remove domain: str | None = None field   │
  │ storage/documents.py             │          │ Remove "domain" from _KNOWN_KEYS         │
  │ + project:    str | None = None  │          │ Remove "domain" from field_validator     │
  │ + status:     str | None = None  │          │ domain: in YAML now routes to extra      │
  │ + key_topics: list[str] = []     │          └──────────┬───────────────────────────────┘
  └──────────────────┬───────────────┘                     │ domain field gone from Python
                     │ typed fields define read shape  ┌────┴────────────────────────┐
                     ▼                                 ▼                             ▼
  ┌──────────────────────────────────┐  ┌─────────────────────────────┐  ┌──────────────────────┐
  │ [EXTEND] _row_from_sqlite()      │  │ [EXTEND] capture.py         │  │ [EXTEND] writer.py   │
  │ storage/documents.py:42          │  │ store() line 428:           │  │ _merge_metadata()    │
  │ Read project → pass through      │  │   remove domain=mr.ai_domain│  │ line 78:             │
  │ Read status  → pass through      │  │ _store_nonmd() line 646:    │  │   remove             │
  │ Read key_topics → json.loads()   │  │   replace note_meta.domain  │  │   domain=incoming.   │
  │ NULL key_topics → empty list     │  │   with tag-filter on .tags  │  │   domain kwarg       │
  └──────────────────┬───────────────┘  └─────────────┬───────────────┘  └──────────┬───────────┘
                     │ reader ready for write tests    │                              │
                     ▼                                 │                              │
  ┌──────────────────────────────────┐                │                              │
  │ [EXTEND] upsert()                │                │                              │
  │ [EXTEND] replace_path()          │                │                              │
  │ storage/documents.py             │                │                              │
  │ Both INSERT OR REPLACE paths:    │                │                              │
  │   + project  ← meta.project      │                │                              │
  │   + status   ← meta.status       │                │                              │
  │   + key_topics ← json.dumps(     │                │                              │
  │       filtered tags)             │                │                              │
  │ ⚠ BOTH must be updated together  │                │                              │
  └──────────────────┬───────────────┘                │                              │
                     │                                 └──────────────┬───────────────┘
                     └────────────────────────────────────────────────┘
                                                       │ all steps complete
                                                       ▼
                        ┌────────────────────────────────────────────────────────────────┐
                        │ [Step 9] Tests — both tracks converge here                     │
                        │                                                                │
                        │  TD-038 — fix existing (these break during Phase 3):          │
                        │    • test_frontmatter.py:106 — remove domain="Y" kwarg       │
                        │    • test_frontmatter.py:125 — remove .domain assertion       │
                        │    • test_capture.py:657   — domain → tag membership check   │
                        └────────────────────────────────────────────────────────────────┘
```

---

## Approach

Build Track A (TD-008) and Track B (TD-038) in sequence. Track A first because it is purely additive (new files + new fields) and carries no breakage risk. Track B second because it removes code that existing tests depend on — the old tests must be fixed in the same phase that removes the code. Each phase ends with a passing test run before moving on.

---

## Phases

### Phase 1 — TD-008: Migration files
**Goal**: Create the three SQL migration files that extend the `documents` table schema. After this phase, running `kms` against a fresh DB will produce a `documents` table with all three new columns.

**Design**:
```
TDD cycle:

  Write test                    Create files               sqlite3 .schema output
  ────────────────────────────────────────────────────────────────────────────────
  test_documents_have_new_cols  003_add_project.sql        CREATE TABLE documents (
  calls init_db(tmp_db)         004_add_status.sql           ...
  asserts 3 cols present        005_add_key_topics.sql       project    TEXT,
                                                             status     TEXT,
  RED (cols absent)             GREEN (cols present)         key_topics TEXT,
                                                             ...
                                                           )
```

Extension: [extensible: config] — adding more columns later follows the identical pattern: new `.sql` file + extend `documents.py`. No changes to migration runner.

**Steps**:
1. Add a test `test_documents_table_has_project_status_key_topics` to `tests/test_storage/test_documents.py`. The test calls `init_db(db_path=tmp_path / "kb.db")` and then uses `sqlite3.connect(tmp_path / "kb.db")` to run `PRAGMA table_info(documents)` and assert that `project`, `status`, and `key_topics` appear in the column names. Run it — expect RED (`OperationalError` or assertion failure).
2. Create `src/storage/migrations/003_add_project.sql` containing exactly one line: `ALTER TABLE documents ADD COLUMN project TEXT;`
3. Create `src/storage/migrations/004_add_status.sql` containing exactly one line: `ALTER TABLE documents ADD COLUMN status TEXT;`
4. Create `src/storage/migrations/005_add_key_topics.sql` containing exactly one line: `ALTER TABLE documents ADD COLUMN key_topics TEXT;`
5. Run the test — expect GREEN. Run `uv run pytest tests/test_storage/test_documents.py -m "not smoke"` to confirm no regressions.
6. Commit.

**Files to modify**:
- `src/storage/migrations/003_add_project.sql` — [NEW] one-line ALTER TABLE
- `src/storage/migrations/004_add_status.sql` — [NEW] one-line ALTER TABLE
- `src/storage/migrations/005_add_key_topics.sql` — [NEW] one-line ALTER TABLE
- `tests/test_storage/test_documents.py` — add schema-presence test

**Test criteria**:
- [ ] `test_documents_table_has_project_status_key_topics` passes (RED → GREEN)
- [ ] No other test in `tests/test_storage/` breaks
- [ ] `PRAGMA table_info(documents)` shows `project TEXT`, `status TEXT`, `key_topics TEXT`

**Notes**:
- Do NOT use `CREATE TABLE` or modify `schema.sql`. The migration runner in `db.py` applies `.sql` files in lex order. Files 003–005 come after 001 and 002, so they apply cleanly.
- `init_db()` is the entry point; it calls `_run_migrations()` internally. Tests should call `init_db(db_path=...)` — never import `CONFIG` at module scope (C-17).

**Status**: [x] complete — commit e83c7cd

---

### Phase 2 — TD-008: Extend documents.py
**Goal**: Teach `DocumentRow`, `_row_from_sqlite()`, `upsert()`, and `replace_path()` about the three new columns. After this phase, capturing any note will write `project`, `status`, and `key_topics` to the DB; reading back will return a typed Python object.

**Design**:
```
  Before (documents.py)              After (documents.py)
  ──────────────────────             ───────────────────────────────────────────
  @dataclass                         @dataclass
  class DocumentRow:                 class DocumentRow:
    vault_path: str                    vault_path: str
    ...                                ...
    batch_id: str | None = None        batch_id: str | None = None
    # no project/status/key_topics     project:   str | None = None          ← NEW
                                       status:    str | None = None          ← NEW
                                       key_topics: list[str] = field(        ← NEW
                                           default_factory=list)

  _row_from_sqlite(row):             _row_from_sqlite(row):
    return DocumentRow(                return DocumentRow(
      vault_path=row[0],               vault_path=row[0],
      ...                              ...
      batch_id=row[N],                 batch_id=row[N],
    )                                  project=row[N+1],                     ← NEW
                                       status=row[N+2],                      ← NEW
                                       key_topics=(                          ← NEW
                                         json.loads(row[N+3])
                                         if row[N+3] else []),
                                     )

  INSERT OR REPLACE INTO documents   INSERT OR REPLACE INTO documents
    (vault_path, ..., batch_id)        (vault_path, ..., batch_id,
    VALUES (?, ..., ?)                  project, status, key_topics)
                                       VALUES (?, ..., ?, ?, ?, ?)
                                       -- bound: meta.project, meta.status,
                                       --   json.dumps([t for t in meta.tags
                                       --     if not t.startswith("domain/")
                                       --     and not t.startswith("type/")])
```

`replace_path()` gets the **identical** INSERT OR REPLACE extension. Both functions must be updated in the same commit — see the `replace_path()` gap note in the architecture diagram.

**Steps**:
1. Add tests to `tests/test_storage/test_documents.py` (all should be RED initially):
   - `test_document_row_defaults`: construct `DocumentRow(vault_path="x", title="y", summary="s", note_type="note", confidence=0.9, updated_by_human=False, content_hash="h", batch_id=None)` — assert `project is None`, `status is None`, `key_topics == []`.
   - `test_row_from_sqlite_reads_key_topics_json`: insert a raw row into a temp DB with `key_topics='["quarterly-review","stakeholder-management"]'`, call `get_by_path(path)`, assert `key_topics == ["quarterly-review", "stakeholder-management"]`.
   - `test_row_from_sqlite_handles_null_key_topics`: insert a raw row with `key_topics=NULL`, call `get_by_path(path)`, assert `key_topics == []`.
   - `test_upsert_writes_and_reads_back_new_columns`: build a `NoteMetadata` with `project="Alpha"`, `status=None`, `tags=["domain/finance", "type/note", "quarterly-review"]`, call `upsert(outcome, batch_id="b1")`, call `get_by_path(...)`, assert `project=="Alpha"`, `status is None`, `key_topics==["quarterly-review"]`.
   - `test_upsert_clueless_binary_key_topics_empty`: `tags=["type/attachment-summary"]` → `key_topics==[]`.
   - `test_replace_path_preserves_new_columns`: upsert a row with `project="Alpha"`, `key_topics=["x"]`, call `replace_path(old_path, new_path, meta, batch_id)`, call `get_by_path(new_path)`, assert columns preserved.
2. Run tests — expect RED (`AttributeError` on `DocumentRow.project`).
3. In `src/storage/documents.py`, extend `DocumentRow` dataclass: add three fields after `batch_id`:
   ```python
   project: str | None = None
   status: str | None = None
   key_topics: list[str] = field(default_factory=list)
   ```
4. In `_row_from_sqlite()` (line 42): extend the `DocumentRow(...)` constructor call to read three more columns from the row. Add `import json` at top of file if not already present. Handle NULL `key_topics` with `json.loads(row[N]) if row[N] else []`. The column index N+1/N+2/N+3 corresponds to the order in the SELECT — check what `get_by_path` selects and add `project`, `status`, `key_topics` to the SELECT list.
5. In `upsert()` (around line 84): extend the `INSERT OR REPLACE` SQL to add `project`, `status`, `key_topics` to the column list. Add three bound params:
   - `meta.project`
   - `meta.status`
   - `json.dumps([t for t in meta.tags if not t.startswith("domain/") and not t.startswith("type/")])`
6. In `replace_path()` (around line 197): apply the **identical** column and bound-param extension as step 5. Do not skip this — the architecture diagram's `⚠` note explains why.
7. Run all tests — expect GREEN. Run `uv run pytest tests/test_storage/ -m "not smoke"`.
8. Commit.

**Files to modify**:
- `src/storage/documents.py` — extend `DocumentRow`, `_row_from_sqlite()`, `upsert()`, `replace_path()`
- `tests/test_storage/test_documents.py` — add 6 new tests

**Test criteria**:
- [ ] All 6 new tests pass (RED → GREEN)
- [ ] `test_document_row_defaults` passes with no new kwargs
- [ ] Existing `DocumentRow(...)` calls in other tests still work (defaults kick in)
- [ ] `uv run pytest tests/test_storage/ -m "not smoke"` — no failures

**Notes**:
- `rename()` (around line 254) does `UPDATE ... SET vault_path = ?` only — it does NOT have an `INSERT OR REPLACE`. Do not touch it.
- Existing tests that construct `DocumentRow` without the new fields will still pass because all three fields have Python defaults.
- Do not import CONFIG at module scope in tests. Use `db_path=tmp_path / "kb.db"` pattern matching existing test fixtures.

**Status**: [x] complete — commit e3a52ff

---

### Phase 3 — TD-038: frontmatter.py changes
**Goal**: Remove the `domain:` frontmatter scalar from the Python model. After this phase: (1) `dumps()` strips `domain:` from any note it serializes — lazy migration of old notes; (2) `NoteMetadata` has no `domain` field; existing tests that use `domain=` are fixed.

**Design**:
```
Step 3A — dumps() filter (do first):

  BEFORE dumps():                      AFTER dumps():
  d = {}                               d = {}
  # ... fill known fields ...          # ... fill known fields ...
  d.update(metadata.extra)             d.update(metadata.extra)
                                       for key in _DEPRECATED_KEYS:  ← NEW (after update!)
                                           d.pop(key, None)
  return yaml.dump(d)                  return yaml.dump(d)

  Test: NoteMetadata(extra={"domain": "finance"})
        → dumps() → assert "domain" not in output YAML

Step 3B — remove domain field (do after 3A):

  BEFORE NoteMetadata:                 AFTER NoteMetadata:
  _KNOWN_KEYS = frozenset({            _KNOWN_KEYS = frozenset({
    ..., "domain", ...                   ...  ← "domain" removed
  })                                   })

  class NoteMetadata:                  class NoteMetadata:
    ...                                  ...
    domain: str | None = None   ←GONE
    ...                                  ...

  @field_validator("domain", ...)      @field_validator(...)  ← "domain" removed
  def validate_...(cls, v): ...        def validate_...(cls, v): ...

  Fix test_frontmatter.py:106: NoteMetadata(domain="Y", ...) → remove domain="Y"
  Fix test_frontmatter.py:125: meta2.domain == meta.domain → remove or rewrite assertion
```

**Steps**:

**3A — dumps() filter:**

1. Add two tests to `tests/test_vault/test_frontmatter.py`:
   - `test_dumps_strips_deprecated_domain_key`: build `NoteMetadata(extra={"domain": "finance"})`, call `dumps(meta)`, assert `"domain:" not in result` (or parse the YAML and check key absence).
   - `test_dumps_preserves_non_deprecated_extra_keys`: build `NoteMetadata(extra={"custom_field": "value"})`, call `dumps(meta)`, assert `"custom_field:" in result`.
2. Run — expect RED (domain: currently appears in dumps output because it's in extra and nothing strips it yet).
3. In `src/vault/frontmatter.py`, add near `_KNOWN_KEYS` at module level:
   ```python
   _DEPRECATED_KEYS = frozenset({"domain"})
   ```
4. In `dumps()`, locate `d.update(metadata.extra)`. Immediately **after** that line, add:
   ```python
   for key in _DEPRECATED_KEYS:
       d.pop(key, None)
   ```
   CRITICAL: this must be AFTER `d.update(metadata.extra)`, not before.
5. Run — expect GREEN. Run `uv run pytest tests/test_vault/test_frontmatter.py`.
6. Commit: "feat(frontmatter): add _DEPRECATED_KEYS lazy migration filter in dumps()"

**3B — remove domain field:**

7. Add test to `tests/test_vault/test_frontmatter.py`:
   - `test_parse_yaml_with_domain_produces_no_domain_attr`: build a YAML string with `domain: finance` in it, call `parse(yaml_str)` (or however frontmatter is parsed), assert `hasattr(result, "domain") is False` or that accessing `.domain` raises `AttributeError`.
8. Run — expect RED (`meta.domain` still exists).
9. Also fix the two existing tests that will break:
   - `test_frontmatter.py:106`: find the `NoteMetadata(domain="Y", ...)` call and remove the `domain="Y"` kwarg.
   - `test_frontmatter.py:125`: find `meta2.domain == meta.domain` and remove that assertion (or replace with a tag-based check if the test intent requires it).
10. In `src/vault/frontmatter.py`:
    - Remove `"domain"` from `_KNOWN_KEYS` (line 34)
    - Remove `domain: str | None = None` from `NoteMetadata` dataclass (line 55)
    - Remove `"domain"` from the `field_validator` decorator (line 68)
11. Run — expect GREEN. Run `uv run pytest tests/test_vault/ -m "not smoke"`.
12. Commit: "feat(frontmatter): remove domain scalar field; route domain: in YAML to extra"

**Files to modify**:
- `src/vault/frontmatter.py` — add `_DEPRECATED_KEYS`, add filter in `dumps()`, remove `domain` from `_KNOWN_KEYS` + `NoteMetadata` + `field_validator`
- `tests/test_vault/test_frontmatter.py` — add 3 new tests, fix 2 existing tests (lines 106 and 125)

**Test criteria**:
- [ ] `test_dumps_strips_deprecated_domain_key` passes (RED → GREEN after step 6)
- [ ] `test_dumps_preserves_non_deprecated_extra_keys` passes
- [ ] `test_parse_yaml_with_domain_produces_no_domain_attr` passes (RED → GREEN after step 11)
- [ ] `test_frontmatter.py:106` and `:125` no longer fail
- [ ] `uv run pytest tests/test_vault/ -m "not smoke"` — no failures after each commit

**Notes**:
- Two commits in this phase: one for the filter, one for the field removal. Keep them separate — it makes the lazy migration mechanism easy to reason about in git history.
- `MetadataResult.ai_domain` in `capture.py:60` is NOT touched in this phase.
- After step 10, any YAML with `domain:` in it will parse to `extra["domain"]`, which step 4 already strips via `_DEPRECATED_KEYS`. The round-trip test in step 7 verifies this end-to-end.

**Status**: [x] complete — commits f25d64a (3A) + f8cd23e (3B)

---

### Phase 4 — TD-038: Fix pipeline consumers (capture.py + writer.py)
**Goal**: Remove all references to the now-deleted `NoteMetadata.domain` field from the capture pipeline and write layer. After this phase, `kms capture` runs without `TypeError` and domain tags continue to appear in captured notes (sourced from `note_meta.tags` instead of `note_meta.domain`).

**Design**:
```
store() — line 423 in capture.py:

  BEFORE:                              AFTER:
  NoteMetadata(                        NoteMetadata(
    ...                                  ...
    domain=mr.ai_domain,    ← REMOVE
    ...                                  ...
  )                                    )

_store_nonmd() — line 646 in capture.py:

  BEFORE:                              AFTER:
  [f"domain/{note_meta.domain}"]       [t for t in note_meta.tags
  if note_meta.domain else []            if t.startswith("domain/")]
                                       # COUPLING: apply_location_tags must run
                                       # before _store_nonmd; domain/<D> tag
                                       # must already be in note_meta.tags

_merge_metadata() — line 78 in writer.py:

  BEFORE:                              AFTER:
  NoteMetadata(                        NoteMetadata(
    ...                                  ...
    domain=incoming.domain, ← REMOVE
    ...                                  ...
  )                                    )
```

**Steps**:
1. Fix `tests/test_pipelines/test_capture.py:657`. The current assertion is `metadata.domain == "finance"`. Change it to `assert "domain/finance" in result.value.metadata.tags`. Run this test — expect it to remain RED for now (the domain kwarg is still in code, but `metadata.domain` no longer exists as an attribute → `AttributeError`). The test now fails with `AttributeError` → that's expected at this point.
2. In `src/pipelines/capture.py`, find `store()` at line 423. Locate the `NoteMetadata(...)` constructor call at line 428. Remove the `domain=mr.ai_domain` keyword argument. Do not touch any other kwarg.
3. In `src/pipelines/capture.py`, find `_store_nonmd()` at line 541. Locate the domain tag construction at line 646. Replace:
   ```python
   ([f"domain/{note_meta.domain}"] if note_meta.domain else [])
   ```
   with:
   ```python
   # COUPLING: apply_location_tags must run before _store_nonmd; domain/<D> tag must be in note_meta.tags
   [t for t in note_meta.tags if t.startswith("domain/")]
   ```
4. In `src/vault/writer.py`, find `_merge_metadata()` at line 48. Locate the `NoteMetadata(...)` constructor at ~line 78. Remove the `domain=incoming.domain` keyword argument.
5. Run `uv run pytest tests/test_pipelines/ tests/test_vault/ -m "not smoke"` — expect GREEN.
6. Commit: "feat(capture, writer): remove NoteMetadata.domain consumption; derive domain tag from tags list"

**Files to modify**:
- `src/pipelines/capture.py` — line 428: remove `domain=mr.ai_domain`; line 646: replace domain tag construction with tag-filter + COUPLING comment
- `src/vault/writer.py` — line ~78: remove `domain=incoming.domain`
- `tests/test_pipelines/test_capture.py` — line 657: fix domain assertion to tag membership check

**Test criteria**:
- [ ] `test_capture.py:657` passes — `"domain/finance" in result.value.metadata.tags`
- [ ] No `TypeError: __init__() got unexpected keyword argument 'domain'` anywhere in test output
- [ ] No `AttributeError: 'NoteMetadata' object has no attribute 'domain'`
- [ ] `uv run pytest tests/test_pipelines/ tests/test_vault/ -m "not smoke"` — no failures

**Notes**:
- `MetadataResult.ai_domain` at `capture.py:60` is NOT removed. `apply_location_tags` still sets it and uses it internally to construct the `domain/<D>` tag string before appending to `ai_tags`. Only the downstream consumption of `ai_domain` as `NoteMetadata.domain` (the scalar) is being removed here.
- The COUPLING comment in step 3 is mandatory. The tag-filter is only safe because `apply_location_tags` runs at pipeline stage index 4 and `store()` runs at index 5 (confirmed at `capture.py:912`). The comment makes this ordering assumption explicit for future maintainers.
- If a test uses `write_note(path, NoteMetadata(...), actor="ai")` anywhere and constructed `NoteMetadata` with `domain=...`, it will now fail. Search for this pattern: `grep -rn "domain=" tests/` and fix any remaining call sites.

**Status**: [x] complete — commit e87364a

---

### Phase 5 — Full suite green
**Goal**: Confirm no regressions across the full test suite. Both tracks are now complete. Run the full suite, investigate any failures, fix.

**Design**:
```
  uv run pytest tests/ -m "not smoke"
          │
          ▼
     All 787+ tests pass?
          │
     YES ─┤─ NO: diagnose and fix
          │         │
          │         ▼
          │    Likely failure sites:
          │    • Any test that constructs NoteMetadata with domain= kwarg (grep missed it)
          │    • Any test that accesses .domain on a NoteMetadata (grep missed it)
          │    • Any test asserting metadata.domain == "..."
          │    • documents.py tests if SELECT query doesn't include new columns
          ▼
       Commit
```

**Steps**:
1. Run `uv run pytest tests/ -m "not smoke"` from the repo root.
2. If any failures: read the error. Common patterns:
   - `TypeError: __init__() got unexpected keyword argument 'domain'` → grep the failing test file for `domain=` and remove the kwarg.
   - `AttributeError: 'NoteMetadata' object has no attribute 'domain'` → grep for `.domain` attribute access and replace with tag-based check.
   - `AssertionError` in documents tests → likely `_row_from_sqlite` column index mismatch; check the SELECT column order matches the `DocumentRow` field order.
3. Fix each failure. Re-run after each fix.
4. When suite is green: `uv run pytest tests/ -m "not smoke"` — confirm pass count ≥ 787 (pre-phase count).
5. Commit: "test: bring full suite to green after Phase Pre-2 implementation"

**Files to modify**:
- Any test file with residual `domain=` kwarg or `.domain` attribute access

**Test criteria**:
- [ ] `uv run pytest tests/ -m "not smoke"` — 0 failures, 0 errors
- [ ] Pass count ≥ 787 (the pre-phase-pre-2 baseline from STATE.md)
- [x] ruff not installed in env — formatting auto-applied by hook
- [x] 797 passed, 0 failures, 1 skipped, 16 deselected (10 more than pre-phase baseline of 787)

**Status**: [x] complete — full suite green, 797 tests pass

---

## Open Questions

None. All spec assumptions validated by research. All line numbers confirmed exact.

---

## Out of Scope

- **Backfilling existing rows** — `ALTER TABLE ADD COLUMN` initialises all existing rows to NULL. No backfill. Phase 3 Search must handle NULL gracefully.
- **`status` vocabulary enforcement at DB level** — no CHECK constraint. Phase 2 Classify enforces vocabulary in code.
- **One-shot vault migration** — stripping `domain:` from all existing notes at once requires bypassing `write_note` (C-03 violation). Lazy migration via `dumps()` is the chosen approach.
- **`MetadataResult.ai_domain` removal** — kept as internal pipeline state. No phase assigned.
- **`rename()` in documents.py** — path-only UPDATE, no INSERT OR REPLACE. Not touched.
- **Phase 2 Classify pipeline** — this phase only prepares the infrastructure Classify will use.
