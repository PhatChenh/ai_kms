# updated_by_human is a whole-note boolean safety gate, not per-field authorship

A single `updated_by_human INTEGER NOT NULL DEFAULT 0` column on `documents` protects entire notes from AI overwrites. If set to 1, the AI skips the write (or surfaces a conflict) for the whole note — any human edit anywhere in the note makes the whole note off-limits.

**Status:** accepted

**Considered Options**

- Per-section authorship tracking via HTML-style comments or a separate `edits` table — deferred; a harder problem explicitly out of scope.

**Consequences**

- `vault/writer.py` must check `updated_by_human` before every AI write. Per-section authorship is deferred to Phase 7 or later. Any future implementation requires a separate design — do not extend this column.
