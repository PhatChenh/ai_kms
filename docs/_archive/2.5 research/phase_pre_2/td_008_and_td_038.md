# Research: TD-008 + TD-038 — Pre-Phase 2 Database and Frontmatter Cleanup

_Last updated: 2026-06-03 (A4/A5 resolved after design-doc correction pass)_

---

## Overview

Two small pre-Phase 2 changes prepare the system for the Classify pipeline. TD-008 adds three columns (`project`, `status`, `key_topics`) to the `documents` SQL table so the classifier can read and write classification state cheaply. TD-038 removes a redundant `domain:` frontmatter field that drifts out of sync with the canonical `domain/<D>` tag.

This research verified both designs against the current code. All assumptions are now validated. Two invalidated assumptions (A4, A5) were found in TD-008 during the initial pass and resolved via a design-doc correction: `from_row()` → `_row_from_sqlite()` and `rename()` → `replace_path()`. One stale TECH_DEBT.md entry (B14) was corrected. Both TDs are unblocked for `/writing-detailed-specs`.

---

## Key Components

These changes touch three layers: the SQL schema, the documents data-access module, and the frontmatter/pipeline modules.

| File | Role |
|------|------|
| `src/storage/migrations/` | Migration runner auto-discovers `[0-9][0-9][0-9]_*.sql` in lex order; currently has `001_initial.sql` and `002_batches.sql` |
| `src/storage/schema.sql` | Baseline schema (version 0); no `project`, `status`, `key_topics` columns in `documents` |
| `src/storage/db.py` | `init_db()` → `_run_migrations()`; comment at line 26 requires "single atomic DDL statement" per file |
| `src/storage/documents.py` | `DocumentRow` dataclass + `upsert()`, `replace_path()`, `rename()`, `_row_from_sqlite()` |
| `src/vault/frontmatter.py` | `_KNOWN_KEYS`, `NoteMetadata` dataclass, `field_validator`, `dumps()` |
| `src/pipelines/capture.py` | `MetadataResult` (line 55), `store()` (line 423), `_store_nonmd()` (line 542), `apply_location_tags()` (line 277) |
| `src/vault/writer.py` | `_merge_metadata()` (line 48) — passes `domain=incoming.domain` at line 78 |
| `src/pipelines/reconcile.py` | Stage 5 `reconcile_stale_tags()` (line 294) — only touches `tags` and `project`, never `domain` scalar |

---

## How It Works

### TD-008 — Documents column additions

When `capture_file` finishes, `store()` calls `documents.upsert(outcome, batch_id=...)`. `upsert()` does an `INSERT OR REPLACE` that writes one row per note. Adding `project`, `status`, `key_topics` columns means extending that INSERT statement to include three more bound parameters sourced from `outcome.metadata`. The migration runner applies new `.sql` files automatically on the next `init_db()` call.

There is also a second INSERT path: `replace_path()` (line 197–251). This is called during `.md` renames (atomic delete-old + insert-new) and has its own `INSERT OR REPLACE` with the same column list as `upsert()`. Both must be extended.

`rename()` (line 254–282) does a simple `UPDATE ... SET vault_path = ?` — it does not re-insert columns. It does not need changing.

### TD-038 — Domain scalar removal

Currently every note gets `domain: finance` in frontmatter AND `domain/finance` in `tags:`. The scalar drifts because Stage 5 `reconcile_stale_tags` only touches `tags` and `project` — it never re-syncs `NoteMetadata.domain`. Removing the scalar means:

1. `dumps()` gains a `_DEPRECATED_KEYS` filter that prevents `domain:` from being re-emitted from `extra` during lazy migration.
2. `store()` stops passing `domain=mr.ai_domain` to `NoteMetadata`.
3. `_store_nonmd()` derives domain tags from `note_meta.tags` (already populated by `apply_location_tags`) instead of from `note_meta.domain`.
4. `writer.py::_merge_metadata()` stops passing `domain=incoming.domain`.

`MetadataResult.ai_domain` is kept — it is internal pipeline state used by `apply_location_tags` to append `domain/<D>` to `ai_tags`, which then flows into `note_meta.tags` in `store()`.

---

## Spec Verification

### TD-008

| ID | Assumption | Verdict | Evidence |
|----|-----------|---------|----------|
| A1 | Next migration numbers are 003, 004, 005 | ✅ Validated | `src/storage/migrations/` contains only `001_initial.sql` and `002_batches.sql` |
| A2 | `documents` table has no `project`, `status`, `key_topics` columns | ✅ Validated | `src/storage/schema.sql` — `documents` DDL has no such columns |
| A3 | `DocumentRow` has no `project`, `status`, `key_topics` fields | ✅ Validated | `src/storage/documents.py:26–40` — dataclass ends at `batch_id` |
| A4 | Function to update for reading new columns is "`from_row()` class method" | ✅ Resolved | Design corrected to `_row_from_sqlite()` module function (line 42). `DocumentRow` has no class methods. |
| A5 | "`upsert()` and `rename()`" are the two write sites to update | ✅ Resolved | Design corrected to `upsert()` and `replace_path()`. `rename()` is path-only UPDATE; `replace_path()` (line 197) has the second `INSERT OR REPLACE`. |
| A6 | `NoteMetadata.project` exists at frontmatter.py:54 | ✅ Validated | `src/vault/frontmatter.py:54` — `project: str | None = None` |
| A7 | `NoteMetadata.status` exists at frontmatter.py:64 | ✅ Validated | `src/vault/frontmatter.py:64` — `status: str | None = None` |
| A8 | `INSERT OR REPLACE` in `upsert()` replaces the whole row | ✅ Validated | `src/storage/documents.py:84–101` — `INSERT OR REPLACE` with all columns |
| A9 | C-05 satisfied by separate migration files (one DDL per file) | ✅ Validated | `src/storage/db.py:26` comment; `CONSTRAINTS.md` C-05 |
| A10 | `key_topics` extraction filter: exclude `domain/` and `type/` prefixes | ✅ Validated | Consistent with tag taxonomy; `domain/` and `type/` are queryable elsewhere |

### TD-038

| ID | Assumption | Verdict | Evidence |
|----|-----------|---------|----------|
| B1 | `_KNOWN_KEYS` contains `"domain"` | ✅ Validated | `src/vault/frontmatter.py:32` |
| B2 | `NoteMetadata.domain: str | None = None` at line 55 | ✅ Validated | `src/vault/frontmatter.py:55` exact match |
| B3 | `field_validator` includes `"domain"` at line 68 | ✅ Validated | `src/vault/frontmatter.py:68` exact match |
| B4 | `store()` passes `domain=mr.ai_domain` at capture.py:428 | ✅ Validated | `src/pipelines/capture.py:428` exact match |
| B5 | `_store_nonmd()` uses `([f"domain/{note_meta.domain}"] ...)` at lines 646–647 | ✅ Validated | `src/pipelines/capture.py:646–647` exact match |
| B6 | `writer.py::_merge_metadata()` passes `domain=incoming.domain` at line 78 | ✅ Validated | `src/vault/writer.py:78` exact match |
| B7 | Stage 5 `reconcile_stale_tags` does NOT touch the `domain:` scalar | ✅ Validated | `src/pipelines/reconcile.py:361` — `model_copy(update={"tags": ..., "project": ...})` only |
| B8 | `MetadataResult.ai_domain` at capture.py:60 | ✅ Validated | `src/pipelines/capture.py:60` exact match |
| B9 | `documents` table has no `domain` column | ✅ Validated | `src/storage/schema.sql` — no `domain` column |
| B10 | `apply_location_tags` sets `ai_domain=location_name` | ✅ Validated | `src/pipelines/capture.py:310` |
| B11 | Test assertion at `test_capture.py:657` is `metadata.domain == "finance"` | ✅ Validated | `tests/test_pipelines/test_capture.py:657` exact match — needs changing to `"domain/finance" in result.value.metadata.tags` |
| B12 | Test at `test_frontmatter.py:106` uses `domain="Y"` kwarg | ✅ Validated | `tests/test_vault/test_frontmatter.py:106` exact match |
| B13 | Test at `test_frontmatter.py:125` asserts `meta2.domain == meta.domain` | ✅ Validated | `tests/test_vault/test_frontmatter.py:125` exact match |
| B14 | TECH_DEBT.md description says drop `ai_domain` from `MetadataResult` | ⚠️ Stale | `TECH_DEBT.md` TD-038 says "drop `ai_domain` from `MetadataResult`" — design doc overrides this to keep it. TECH_DEBT.md needs a note that the design resolved to keep `ai_domain`. Not a code issue. |

---

## Edge Cases & Silent Failure Modes

### TD-008

**`replace_path()` gap (critical).** If `replace_path()` is not updated alongside `upsert()`, any `.md` rename during capture will silently clear `project`, `status`, and `key_topics` from the documents row. The columns exist after migration but the INSERT in `replace_path()` won't include them, so SQLite sets them to NULL on every rename. Notes that were classified (status set) and then moved would lose their status silently.

**NULL on existing rows.** `ALTER TABLE ADD COLUMN` initialises all existing rows to NULL. This is documented and acceptable — Phase 3 search must handle NULL gracefully ("not yet indexed"). No backfill path.

**`key_topics` on CLUELESS binaries.** CLUELESS binary markers (inbox binaries without project/domain context) get minimal metadata. `note_meta.tags` would be `["type/attachment-summary"]` only. After filtering, `key_topics_json = "[]"`. This is correct — NULL would be wrong because the column is explicitly set.

### TD-038

**Lazy migration leaves `domain:` in old notes.** Old notes keep `domain: finance` in their YAML until any pipeline re-writes them. In Obsidian these appear as an unknown frontmatter field. Cosmetically visible but functionally harmless — no code reads `NoteMetadata.domain` after the change.

**`dumps()` must filter before `d.update(metadata.extra)`.** If `_DEPRECATED_KEYS` filter is applied after `d.update(metadata.extra)`, it catches keys from `extra`. If applied before, it filters nothing (nothing is in `d` at that point for the deprecated key). The correct placement is filtering `metadata.extra` before calling `d.update(...)` or filtering `d` after `d.update(...)`. The design says "filter in `dumps()`" without specifying order — implementer must filter `d` after the `d.update(metadata.extra)` call.

---

## Dependencies & Coupling

### TD-008

- Both `upsert()` and `replace_path()` must be updated atomically — updating only one creates a silent data-loss path during renames.
- `DocumentRow` dataclass change is additive (defaults provided) — existing tests constructing `DocumentRow` directly won't break.
- Phase 2 Classify will write `status` via `documents.upsert()`. The column must exist before classify ships.
- Phase 3 search will filter on `project` and `key_topics`. NULL-handling must be explicit in Phase 3 queries.

### TD-038

- `apply_location_tags` (line 310) still sets `ai_domain=location_name` on `MetadataResult`. `store()` must stop consuming `ai_domain` as a scalar field. The field still drives `ai_tags` construction (adds `domain/<D>` to the tags list) — that part is unchanged.
- `_store_nonmd()` switch from `note_meta.domain` to `note_meta.tags` filter is safe only because `apply_location_tags` runs before `store()` in the pipeline and has already appended the `domain/<D>` tag to `ai_tags` → `note_meta.tags`. If pipeline order ever changes, this breaks silently.

---

## Extension Points

### TD-008

- **Open for extension:** Adding more columns later is another `ALTER TABLE ADD COLUMN` + extending `upsert()` and `replace_path()`.
- **Coupling risk:** `key_topics` extraction is inline in `upsert()` (one-liner, no helper). If Phase 3 needs different filtering logic, it's one-line to change. No abstraction needed.

### TD-038

- **`_DEPRECATED_KEYS` is a general mechanism.** Any future redundant frontmatter field can be added to the frozenset for lazy migration.
- **Coupling risk:** TD-038 assumes `apply_location_tags` always runs before `store()`. This is enforced by the pipeline stage order in `capture()`. It should be documented with a `# COUPLING:` comment where `_store_nonmd()` does the tag filter.

---

## Open Questions

None — all claims were verifiable from code.

---

## Technical Debt Spotted

- **TECH_DEBT.md TD-038 description** ~~says "drop `ai_domain` from `MetadataResult`"~~ — **resolved**: TECH_DEBT.md updated to reflect design decision to keep `ai_domain` as internal pipeline state only.
- **`replace_path()` is undocumented in the design** as a write site — **resolved**: design doc corrected to name `replace_path()` as second INSERT site and explains why `rename()` does not need changing.

---


## Update — 2026-06-03

### Resolution check: A4 and A5

Following the `/codebase-design-analysis` correction pass, both invalidated assumptions were resolved in `docs/design/phase_pre_2/td_008_documents_columns.md`.

| ID | Was | Now | Verdict |
|----|-----|-----|---------|
| A4 | "`from_row()` class method" | "`_row_from_sqlite()` function (line 42)" | ✅ Resolved — design doc corrected |
| A5 | "`upsert()` and `rename()`" | "`upsert()` and `replace_path()`" | ✅ Resolved — design doc corrected |

Code spot-check confirmed: `_row_from_sqlite()` at `src/storage/documents.py:42` and `replace_path()` at `src/storage/documents.py:197` still match the corrected design.

All TD-008 assumptions now validated. TD-038 assumptions were all validated in the original pass. Both TDs are unblocked for `/writing-detailed-specs`.

---

## Update — 2026-06-03 (spec verification pass)

### Exact line numbers — `store()` and `_store_nonmd()`

| Symbol | Line | Notes |
|--------|------|-------|
| `store()` defined | 423 | `async def store(mr: MetadataResult, ctx: PipelineContext) -> Result[WriteOutcome]:` |
| `domain=mr.ai_domain` kwarg in `NoteMetadata(...)` | 428 | Inside the constructor call at lines 425–432; the `domain=` kwarg is the third positional field |
| `_store_nonmd()` defined | 541 | `async def _store_nonmd(mr: MetadataResult, note_meta: NoteMetadata, ctx: PipelineContext) -> Result[WriteOutcome]:` |
| `note_meta.domain` reference (domain tag construction) | 646 | `([f"domain/{note_meta.domain}"] if note_meta.domain else [])` — inside `sibling_meta = NoteMetadata(...)` at lines 641–650 |

These confirm spec step 7 targets. B4 (`store()` at capture.py:428) and B5 (`_store_nonmd()` at lines 646–647) remain valid — the line numbers are unchanged since the initial research pass.

### Pipeline stage order

Stage list found at **line 912** inside `capture_file()`:

```python
return await run_pipeline(
    "capture",
    [extract, enrich_urls, summarize, metadata, apply_location_tags, store],
    path,
    context=context,
)
```

`apply_location_tags` is position 5 (index 4); `store` is position 6 (index 5). The list is a plain Python literal — not assembled conditionally, not sorted dynamically, not modified at runtime. There is only one call to `run_pipeline` for the main capture path. Order is guaranteed.

**Verdict: confirmed safe.**

### New assumption row added — TD-038

| ID | Assumption | Verdict | Evidence |
|----|-----------|---------|----------|
| B15 | `apply_location_tags` runs before `store()` in the capture pipeline stage list | ✅ Validated | `src/pipelines/capture.py:912` — literal list `[..., apply_location_tags, store]`; order is not conditional or dynamic |

### Surprises / new findings

None. No conditional stage skipping, no dynamic reordering, no second call to `run_pipeline` for the main capture path. The stage list in `scan_capture` delegates to `capture_file` (which runs the same list), so there is only one authoritative stage order. The existing `# COUPLING:` note in the Dependencies section of this document accurately describes the risk.
