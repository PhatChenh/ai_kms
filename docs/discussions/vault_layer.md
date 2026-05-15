# Discussions: vault_layer

## Phase 0 — Planning
_Completed: 2026-05-14_

### Exchanges

**Q:** In OQ-V2, what is the `documents` table? Is it the vault's documentation folder or a SQL table? What does "FS" mean? What is `storage/documents.upsert()`? And what are all the options for keeping them in sync?

**A:** `documents` is a SQLite table defined in `storage/schema.sql` — not the vault folder. It is an index: one row per note in the vault, holding only metadata needed for fast lookups. The note body never lives there.

```sql
CREATE TABLE documents (
    id               INTEGER PRIMARY KEY,
    vault_path       TEXT UNIQUE,        -- "inbox/foo.md"
    updated_by_human INTEGER DEFAULT 0,  -- 0 or 1
    content_hash     TEXT,
    ...
);
```

FS = filesystem. The actual `.md` file on disk inside the Obsidian vault. Every note exists in two places simultaneously: the `.md` file (source of truth for content) and a row in `documents` (an index that mirrors a few frontmatter fields for cheap queries).

`storage/documents.upsert(...)` does not exist yet. It is a placeholder name for "a function that does `INSERT OR REPLACE` into the `documents` table." It would live in `storage/` alongside the existing `storage/audit_log.py`.

**Why a mirror at all:** A pipeline asking "is `inbox/foo.md` off-limits because the human edited it?" has two options — (A) open the file, parse YAML, read `updated_by_human` (slow: disk read + YAML parse), or (B) `SELECT updated_by_human FROM documents WHERE vault_path = ?` (fast: one indexed lookup). The project chose B.

**Option (a) — writer writes both FS and SQLite:**
```
writer.write_note(...)
  ├─ atomic write to .md file on disk
  └─ storage.documents.upsert(...)  ← writer calls this too
```
Pro: mirror always fresh. Con: `vault/writer.py` imports from `storage/` — vault layer is coupled to the database. A test needs a real SQLite file.

**Option (b) — writer touches only the FS; indexer syncs SQLite later:**
```
writer.write_note(...)
  └─ atomic write to .md file — done

later: indexer.detect_changes() scans vault, upserts changed rows
```
Pro: writer is pure-FS, testable with just `tmp_path`. Con: drift window between write and next `detect_changes()` call; every caller must remember to run the indexer.

**Option (c) — hybrid (what was chosen):** writer returns a `WriteOutcome` dataclass containing `vault_path`, `content_hash`, `updated_by_human`, etc. The writer imports nothing from storage. The pipeline calls `storage.documents.upsert(outcome)` as the next step after the write.
```
writer.write_note(...) → Success(WriteOutcome(...))

pipeline then calls:
storage.documents.upsert(outcome)  ← one extra line in the pipeline
```
This keeps the writer decoupled (testable with `tmp_path` only) while avoiding the full-vault rescan cost of option (b). The coupling moves up to the pipeline layer, which is allowed to know about both vault and storage.

_Key concept: vault-storage sync strategies — FS-only writer vs direct mirror vs WriteOutcome handoff_

---

## Phase 2 — frontmatter.py
_Completed: 2026-05-15_

### Exchanges

**Q:** What happens when a human creates a note in Obsidian, fills in some (but not all) frontmatter fields — some in the NoteMetadata known set, some not — and then the AI pipeline runs? Does filling in frontmatter cause the system to flag the note as edited by human?

**A:** `parse()` splits frontmatter into known keys (on `NoteMetadata`) and unknown keys (everything else). Known fields like `project: Zalopay` land directly on the model; unknown fields like `my_custom_tag: important` land in `extra`. Both survive a round-trip through `dumps()` unchanged.

Filling in frontmatter does NOT set `updated_by_human=True`. There is no watcher in Phase 0/1 — the vault layer cannot detect an Obsidian save. `updated_by_human` only becomes `True` when `write_note(..., actor="human")` is explicitly called. Until then, `updated_by_human` defaults to `False` and AI writes proceed normally.

The real gap is different: when the AI pipeline writes a fresh `NoteMetadata` (e.g. `project=None`, `tags=[]`), `dumps()` uses `exclude_none=True` — which only drops `None`, not empty lists. So `tags: []` gets written to the file, silently overwriting the human's `tags: [meeting, Q2]`.

```python
# model_dump(exclude_none=True) on default NoteMetadata:
# {'tags': [], 'updated_by_human': False}
# tags=[] is NOT None → it IS written → human tags are lost
```

Fix applied (Option B in Phase 4 plan): in `write_note` merge, if caller's value is `None` or `[]`, fall back to the existing note's value instead of overwriting.

_Key concept: updated_by_human is not auto-set; known-field overwrite gap from exclude_none_

---

**Q:** What is a sentinel value, and why is it needed?

**A:** Option B rule: `tags=[]` from the caller means "I didn't provide tags — keep existing." But what if a pipeline genuinely wants to clear all tags? It would also pass `tags=[]`, which is indistinguishable from "not provided."

A sentinel is a special magic value that breaks this ambiguity — a value that can only mean "I explicitly want this to be empty":

```python
NoteMetadata(tags=["__clear__"])
# writer sees: non-empty → "explicitly provided"
# writer strips sentinel → writes tags: []
```

Without a sentinel, there is no way to tell "forgot to set" from "wants empty." The proper fix (TD-014) is a `NoteMetadataUpdate` dataclass where every field has three states: a value, `None` (not provided), and `UNSET` (explicitly cleared). This is deferred because Phase 1 pipelines only add data, never clear fields.

_Key concept: sentinel value — distinguishing "not provided" from "explicitly empty"_

---

**Q:** Should design gaps like the sentinel issue be tracked somewhere so they are not forgotten?

**A:** Yes — in STATE.md under Technical Debt. TD entries record: what the gap is, why it is deferred, what the known fix is, and which phase owns it. A `⚠️ USER FLAG` annotation in the TD entry tells Claude Code to surface the issue loudly before any future phase extends the workaround silently.

_Key concept: TD entries as cross-session design memory with escalation flags_
