# Success Criteria — Phase 1.5 Pay-Debt
_Generated: 2026-06-03_
_Source plan: `docs/plans/phase1.5_redesign/pay_debt.md`_

Each phase has two tiers:
- **"You can verify"** — observable in the vault without terminal access (file presence, frontmatter fields)
- **"Developer must verify"** — requires terminal, logs, or DB access

---

## Phase 1 — FILE_LOST Guard

### You can verify

- **Given** `report.pdf` in `inbox/`, deleted before capture starts
  **When** watcher fires capture
  **Then** no `.summaries/report.pdf.md` appears anywhere in vault; `inbox/` is empty for that file

- **Given** `report.pdf` exists when capture starts, deleted mid-pipeline (after extraction, before store)
  **When** store stage runs
  **Then** no `.summaries/report.pdf.md` appears; no orphaned sibling anywhere under vault

### Developer must verify

- Audit log row written: `outcome="FILE_LOST"`, `stage="entry"` when entry guard fires; `stage="store"` when store guard fires
- `capture_file()` returns `Failure(recoverable=True)` from entry guard; `Failure(recoverable=False)` from store guard
- No `documents` row inserted in either guard path
- `_audit_file_lost()` write failure does NOT suppress `Failure` return — `capture_file` still returns `Failure`

---

## Phase 2 — `_location_context` + `apply_location_tags`

### You can verify

- **Given** `note.md` sits in `Domain/Engineering/`
  **When** capture pipeline runs
  **Then** `note.md` frontmatter `tags:` contains `domain/Engineering`

- **Given** `note.md` sits in `Projects/Alpha/`
  **When** capture pipeline runs
  **Then** `note.md` frontmatter contains `project: Alpha`

- **Given** `note.md` already has `domain/Engineering` in tags, sits in `Domain/Engineering/`
  **When** capture pipeline runs again
  **Then** frontmatter contains exactly one `domain/Engineering` entry — no duplicate

- **Given** `note.md` sits in `inbox/`
  **When** capture runs
  **Then** frontmatter has no new `domain/` tag; `project:` field absent or unchanged

### Developer must verify

- `_location_context()` returns `("domain", "Engineering")` for path under `Domain/Engineering/`; `("project", "Alpha")` under `Projects/Alpha/`; `("inbox", None)` under `inbox/`; `(None, None)` elsewhere
- Invalid domain (folder not in `load_valid_domains()` result) → warning logged, tag not written, `apply_location_tags` still returns `Success`
- `apply_location_tags` returns `Success(MetadataResult)` with correct `ai_tags` / `ai_project` set
- `updated_by_human: true` note → `write_note(actor="ai")` blocks write; frontmatter on disk unchanged

---

## Phase 3 — `reconcile_stale_tags` (Stage 5)

### You can verify

- **Given** `note.md` frontmatter contains `domain/OldDomain`; `Domain/OldDomain/` folder deleted from vault
  **When** `kms reconcile` runs
  **Then** `note.md` frontmatter no longer contains `domain/OldDomain`

- **Given** `note.md` sits under `Domain/Engineering/` but frontmatter lacks `domain/Engineering` tag
  **When** `kms reconcile` runs
  **Then** `note.md` frontmatter now contains `domain/Engineering` in tags

- **Given** `note.md` sits under `Projects/Alpha/` but frontmatter has `project: Beta`
  **When** `kms reconcile` runs
  **Then** `note.md` frontmatter now has `project: Alpha`

- **Given** `note.md` has `updated_by_human: true` and a stale `domain/OldDomain` tag
  **When** `kms reconcile` runs
  **Then** `note.md` frontmatter is completely unchanged — human lock respected

### Developer must verify

- `ReconcileResult.tags_updated` equals count of notes written during Stage 5
- `load_valid_domains()` called exactly once per `reconcile()` invocation (verify via mock call count), not once per note
- `read_note()` called before every `write_note()` — all existing frontmatter fields preserved; only `tags` and `project` replaced
- `scan_vault()` called once at `reconcile()` entry, result passed into Stage 1 and Stage 5
- Full await-chain (Stages 1–6) completes without regression against prior reconcile tests

---

## Phase 4 — Folder Handling (`capture_folder` + watcher)

### You can verify

- **Given** folder `ProjectX/` containing 3 files dropped in `inbox/`; LLM routes to `Projects/Alpha/` with high confidence
  **When** watcher debounce timer fires (5 s after last file event)
  **Then** `inbox/ProjectX/` no longer exists
         AND `Projects/Alpha/ProjectX/` exists with all 3 original files
         AND each file has a sibling `.md` under the appropriate `.summaries/` path

- **Given** folder `ProjectX/` dropped in `inbox/`; LLM confidence is CLUELESS (below review threshold)
  **When** capture runs
  **Then** `inbox/ProjectX/` remains in place (not moved)
         AND each file has a CLUELESS placeholder `.md` in the vault

- **Given** folder dropped directly in `Projects/Alpha/` (not inbox)
  **When** watcher timer fires
  **Then** folder stays in `Projects/Alpha/`, all files captured normally (LLM not called for routing)

- **Given** one of 3 files in batch fails (e.g. handler not found)
  **When** batch completes
  **Then** other 2 files captured normally; their sibling `.md` notes exist in vault

### Developer must verify

- `batches` table row inserted with correct `status` (`COMPLETE | PARTIAL | CLUELESS`), `destination_type`, `destination_name`, `file_count`
- `documents.batch_id` column populated for every successfully captured file in the batch
- `FOLDER_CLASSIFIED` audit entry written for inbox drops (auto and CLUELESS paths)
- LLM `classify_folder.yaml` NOT called for Project/Domain drops; `batches.confidence=1.0` in that row
- `DirCreatedEvent` → `_pending_folders` registry populated, timer started
- `FileCreatedEvent` inside pending folder → timer reset; normal `_on_create` callback suppressed for that file
- Timer fires → `_on_folder_stable()` called; `capture_folder()` submitted to `ThreadPoolExecutor`
- `asyncio.run()` called only from `ThreadPoolExecutor` worker thread, never from watchdog observer thread
- Empty folder → no `batches` row created, no pipeline run

---

## Phase 5 — Handlers Extension (8 new types)

### You can verify

- **Given** `budget.xlsx` dropped in inbox
  **When** capture runs
  **Then** `.summaries/budget.xlsx.md` appears in vault with non-empty body (spreadsheet content extracted)

- **Given** `deck.pptx` dropped in inbox
  **When** capture runs
  **Then** `.summaries/deck.pptx.md` appears with slide text in body

- **Given** `email.eml` dropped in inbox
  **When** capture runs
  **Then** `.summaries/email.eml.md` appears with subject and body content extracted

- **Given** `photo.png` dropped in inbox (stub handler)
  **When** capture runs
  **Then** `.summaries/photo.png.md` appears with stub body — no crash, no silent skip

### Developer must verify

- Each handler's `can_handle()` returns `True` only for its own extensions; `False` for all others
- `extract()` returns `Success(RawContent)` for valid files of each type
- No `elif` chain added to pipeline code — all 8 handlers self-register via `HandlerRegistry`
- Existing `MarkdownHandler`, `PdfHandler`, `DocxHandler` still match their types (registration order preserved)
- `openpyxl`, `python-pptx`, `extract-msg` present in installed deps (`pyproject.toml` + `uv.lock`)

---

## Phase 6 — Idempotent Capture

### You can verify

- **Given** `note.md` already captured; content not changed; frontmatter has AI-written summary
  **When** capture triggers again
  **Then** frontmatter content is identical before and after; summary not overwritten

- **Given** `note.md` content edited by user since last capture
  **When** capture runs
  **Then** frontmatter updated with new AI-generated summary and tags

- **Given** `report.pdf` already captured; sibling `report.pdf.md` has `source_hash` matching binary; PDF content unchanged
  **When** capture triggers again
  **Then** `report.pdf.md` frontmatter is identical; `source_hash` unchanged

- **Given** `report.pdf` replaced with new version (same filename, different bytes)
  **When** capture runs
  **Then** `report.pdf.md` regenerated with updated summary
         AND `source_hash` in `report.pdf.md` frontmatter updated to new value

### Developer must verify

- `SKIPPED` audit entry written: `outcome="SKIPPED"`, correct source path
- `run_pipeline()` NOT invoked when hash matches (verify via mock or spy)
- `_audit_skipped()` write failure does NOT prevent `Success` return from `capture_file()`
- `source_hash` field survives frontmatter round-trip: write `source_hash="abc123"` → parse → `metadata.source_hash == "abc123"`
- `source_hash=None` (default) not written as a key to YAML output
- No `documents` row update on SKIPPED path

---

## Phase 7 — `reconcile_stale_batch_refs`

### You can verify

- No vault-visible change — this stage modifies only `documents.batch_id` in SQLite. All outcomes require DB or log access.

### Developer must verify

- `documents.batch_id` set to `NULL` for any row where `vault_path` no longer starts with `Projects/<A>/` or `Domain/<D>/` matching `batches.destination_type/name`
- `documents.batch_id` preserved for rows where path still matches destination prefix
- Rows where `documents.batch_id IS NULL` → untouched, not in JOIN result
- `ReconcileResult.batch_refs_cleared` = exact count of nulled rows
- `batches` table absent (pre-Phase-4 DB) → `reconcile_stale_batch_refs()` returns `Success`, `batch_refs_cleared=0`, no crash
- `kms reconcile` CLI output includes `batch_refs_cleared` count
