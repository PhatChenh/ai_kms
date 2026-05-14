---

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
