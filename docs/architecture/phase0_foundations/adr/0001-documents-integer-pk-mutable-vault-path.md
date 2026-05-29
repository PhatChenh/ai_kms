# documents.id is INTEGER AUTOINCREMENT; vault_path is a separate UNIQUE column

Synthetic integer PK for the `documents` table; vault path stored as a UNIQUE but mutable column. Notes are renamed and moved constantly — if `vault_path` were the PK, a rename would orphan every FK reference in `audit_log`, `corrections`, and future `embeddings`.

**Status:** accepted

**Considered Options**

- UUID PK — same stability benefit, harder to debug interactively.
- Frontmatter `doc_id` field — most robust but requires vault write capability not available in Phase 0.
- Path-as-PK — cheap but broken on first rename.

**Consequences**

- Phase 1 vault indexer must run `SELECT id FROM documents WHERE content_hash = ? AND vault_path != ?` before inserting, to detect moves and UPDATE `vault_path` in place.
- All FKs (`corrections.document_id`, future `embeddings.document_id`) reference `documents.id` (integer), not the path string.

_Note: `docs/research/storage_level.md` described path-as-PK — the plan supersedes this._
