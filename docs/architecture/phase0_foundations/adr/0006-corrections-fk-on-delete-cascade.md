# corrections.document_id FK uses ON DELETE CASCADE

`document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE`. Without CASCADE, deleting a `documents` row raises a FK constraint error (orphaned `corrections`). With CASCADE, the DB stays consistent with no application code.

**Status:** accepted

**Consequences**

- `PRAGMA foreign_keys=ON` must be set on every connection — not just once at boot — for CASCADE to fire. It is set in every `_connect()` call.
- Phase 7 self-learning must rely on CASCADE for corrections cleanup; do not implement manual deletion in application code.
