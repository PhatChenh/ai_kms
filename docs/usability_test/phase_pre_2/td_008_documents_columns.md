# Usability Tests: TD-008 — documents columns (project, status, key_topics)

_Design doc: `docs/design/phase_pre_2/td_008_documents_columns.md`_

---

## You can verify (in the vault / CLI)

- **Given** a `.md` note captured after this change  
  **When** you run `kms capture <note.md>`  
  **Then** the audit log shows `CAPTURED`  
  AND (DB check) `SELECT project, status, key_topics FROM documents WHERE vault_path = ?` returns non-NULL `project` (if note is under `Projects/<A>/`) and a JSON array in `key_topics`

- **Given** a note already in the documents table before this migration runs  
  **When** `kms capture --scan` re-processes it  
  **Then** `project`, `status`, `key_topics` are populated in the DB row

- **Given** a note with tags `["domain/finance", "type/report", "q2-results", "stakeholder-comms"]`  
  **When** upsert runs  
  **Then** `key_topics` column contains `["q2-results", "stakeholder-comms"]` (only non-prefixed tags — `domain/` and `type/` stripped)

---

## Developer must verify

- `DocumentRow.project`, `.status`, `.key_topics` exist and have correct types (`str | None`, `str | None`, `list[str]`)
- `upsert()` returns `Success(rowid)` — no regression on existing captures
- `get_by_path()` returns `DocumentRow` with `key_topics` as `list[str]` (deserialized from JSON), not raw string
- `rename()` also propagates the three new columns (not just `upsert()`)
- Migration files `003_add_project.sql`, `004_add_status.sql`, `005_add_key_topics.sql` run clean on a fresh DB (`init_db()` applies all three in order)
- Existing test suite passes — all three new `DocumentRow` fields have defaults so no forced test updates
- After migration on existing DB, old rows have `NULL` in all three columns (no corruption)
