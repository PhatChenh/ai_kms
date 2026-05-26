---
name: storage_level
description: Phase 0 storage layer research — schema.sql, migrations/, db.py, audit_log.py — what to build, how it ties to existing core/, what to borrow from the JS reference.
type: project
---

# Research: storage_level
_Last updated: 2026-05-07_

## Overview

The storage layer is the persistence backbone for AI-kms. It is **not** the vault — the
Obsidian vault is the source of truth for note content. SQLite holds an *index* of the vault
plus operational state: the `documents` index (id, summary, classification metadata), the
append-only `audit_log` (every AI decision ever made), and a `corrections` table reserved for
Phase 7 self-learning. Phase 3 will add `embeddings`. The layer is built once in Phase 0; every
later phase reads/writes through it. Without an audit log from day one, Phase 8 (daily
briefing) has nothing to read.

The four artefacts to deliver in Phase 0:
1. `storage/schema.sql` — base DDL for version 0 of the schema.
2. `storage/migrations/` — versioned `.sql` deltas (placeholder `001_initial.sql` now;
   real deltas land in Phase 3 and Phase 7).
3. `storage/db.py` — connection manager + migration runner. PRAGMAs (WAL, foreign_keys),
   thread-local connections, parameterised queries only.
4. `storage/audit_log.py` — `append(entry)` + `query(...)`. Dumb storage, no business logic.

## Key Components

**To build (none exist yet):**

| File | Role |
|---|---|
| `storage/schema.sql` | Base schema applied at version 0. Tables: `documents`, `audit_log`, `corrections`, `schema_version`. Indexes on hot columns. CREATE TRIGGER on `audit_log` to block UPDATE/DELETE. **No `embeddings` table** until Phase 3. |
| `storage/migrations/001_initial.sql` | Placeholder delta (comment-only). Exists so `db.py`'s migration loop has something to iterate over and proves the mechanism works before Phase 3 needs it. |
| `storage/db.py` | `init_db(db_path)` creates file, runs `schema.sql`, sets `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`. Runs migrations in order, bumps `schema_version`. Exposes `get_connection()` / context manager. Thread-local. Sync stdlib `sqlite3`, **not** SQLAlchemy. |
| `storage/audit_log.py` | `AuditEntry` dataclass mirroring the columns. `append(entry)` — single parameterised INSERT. `query(date=None, pipeline=None, correlation_id=None)` — filtered SELECT. Module deliberately exposes no `update_*` / `delete_*`. |

**Existing code that constrains the design:**

- [core/config.py](core/config.py) — `DatabaseConfig.path` (default `./data/kb.db`) is the single
  source for the DB location. `db.py` must read `CONFIG.main.database.path`, not a hardcoded
  constant.
- [core/result.py](core/result.py) — every public function in `audit_log.py` and `db.py` should
  return `Success[T] | Failure`, per project rule. Internal helpers can raise.
- [core/exceptions.py](core/exceptions.py) — `StorageError` is already declared. Wrap
  `sqlite3.Error` and rethrow as `StorageError` at module boundaries.
- [core/logging_setup.py](core/logging_setup.py) — `correlation_id` lives in contextvars.
  `audit_log.append()` must read it from there (not require it as an argument); pipelines call
  `new_correlation_id()` at entry, every audit row inherits it for free.
- [core/confidence.py](core/confidence.py) — `AIDecision` (action, confidence, reasoning,
  source_ids) is the natural input for an audit row. The audit schema's columns should map
  one-to-one to `AIDecision` + (timestamp, pipeline, stage, outcome, correlation_id).

## How It Works

**Boot sequence (called once from `cli/main.py`).**
1. `core/config.py` is already imported as a module-level singleton, validating
   `CONFIG.main.database.path`.
2. `cli/main.py` calls `setup_logging(...)` then `db.init_db(CONFIG.main.database.path)`.
3. `init_db`:
   - `Path(db_path).parent.mkdir(parents=True, exist_ok=True)` (the `data/` dir is
     git-ignored).
   - `sqlite3.connect(db_path)` (the file is created on first connect — there is no separate
     "create" step).
   - `PRAGMA journal_mode=WAL` (better concurrent reads, sane defaults at our scale).
   - `PRAGMA foreign_keys=ON` (off by default in SQLite — easy footgun).
   - Execute `schema.sql` (idempotent — every CREATE has `IF NOT EXISTS`).
   - Run the migration loop: read `schema_version.version`; for every
     `migrations/00X_*.sql` whose number > current, execute in a transaction and bump
     `schema_version`.
4. Subsequent calls to `get_connection()` return a thread-local connection.

**Audit append flow.** A pipeline stage calls `audit_log.append(AuditEntry(...))`. The function:
- pulls `correlation_id` from `structlog.contextvars` (set by `new_correlation_id()` at the
  pipeline entrypoint),
- builds a single parameterised INSERT,
- returns `Success(rowid)` or `Failure(error="…", recoverable=False, context={...})` if the
  INSERT raises.

**Audit query flow.** `query(date=..., pipeline=..., correlation_id=...)` builds a SELECT with
the supplied filters and returns a list of `AuditEntry`. Phase 8 (briefing) is the primary
consumer — a daily filter on `WHERE date(timestamp)=date('now','localtime')` is the canonical
read path.

**Migration semantics.** `schema.sql` defines the world at version 0. Each delta in
`migrations/` is named `00N_<purpose>.sql`, applied in lexical order, raises the version by 1
each time. The Phase 0 placeholder is `001_initial.sql` containing only a comment, so:
- the mechanism is exercised on first run,
- Phase 3 drops in `002_add_embeddings.sql`,
- Phase 7 drops in `003_enrich_corrections.sql`,
- no manual SQL on the live DB, ever.

## Schema details — what the columns must carry

**`documents` (the index, NOT the content store).**
- `id TEXT PRIMARY KEY` — the relative vault path (e.g. `inbox/2026-05-07-meeting.md`).
  Using the vault path as the PK means upserts from `vault/writer.py` are O(1) and there is
  one canonical ID per note across the whole system.
- `title TEXT NOT NULL`
- `summary TEXT` — capture pipeline output.
- `note_type TEXT` — set by classify pipeline (e.g. `domain`, `project`, `archive`).
- `confidence REAL` — last AI confidence for the routing decision.
- `created_at TEXT NOT NULL DEFAULT (datetime('now'))`
- `updated_at TEXT NOT NULL DEFAULT (datetime('now'))`
- `updated_by_human INTEGER NOT NULL DEFAULT 0` — boolean. `vault/writer.py` checks this
  before any AI write. Mirrored from frontmatter so SQLite can answer the same question
  without filesystem I/O.
- `content_hash TEXT` — SHA-256 of the body. Drives "did this file change?" cheap diffs in
  the `vault/indexer.py` (Phase 1+).
- Indexes: `documents(note_type)`, `documents(updated_at)`.

**`audit_log` (append-only, one row per AI decision).**
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `timestamp TEXT NOT NULL DEFAULT (datetime('now'))`
- `pipeline TEXT NOT NULL` — `"capture"`, `"classify"`, `"promotion"`, etc.
- `stage TEXT NOT NULL` — name of the pure-function stage inside the pipeline.
- `source_ids TEXT NOT NULL` — JSON-encoded list of vault paths or external IDs.
- `decision TEXT NOT NULL` — mirrors `AIDecision.action`.
- `confidence REAL NOT NULL` — mirrors `AIDecision.confidence`.
- `reasoning TEXT NOT NULL` — mirrors `AIDecision.reasoning`.
- `outcome TEXT NOT NULL` — `"AUTO"`, `"SUGGEST"`, `"CLUELESS"` (matches
  `RouteDecision`), or a custom string for non-routing decisions.
- `correlation_id TEXT NOT NULL` — UUID set by `new_correlation_id()` at pipeline entry.
- Indexes: `audit_log(timestamp)`, `audit_log(correlation_id)`,
  `audit_log(pipeline, timestamp)` (briefing reads filter by both).
- Triggers (belt-and-braces with the application-layer rule):
  ```sql
  CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
  CREATE TRIGGER audit_log_no_delete
    BEFORE DELETE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
  ```

**`corrections` (Phase 7 placeholder — empty until self-learning lands).**
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `timestamp TEXT NOT NULL DEFAULT (datetime('now'))`
- `document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE`
- `field TEXT NOT NULL` — which AI-set field the human overrode.
- `ai_value TEXT`
- `human_value TEXT`
- Defining this now means Phase 7 needs zero migration work. The roadmap calls this out
  explicitly.

**`schema_version` (one row, one int).**
- `version INTEGER NOT NULL`
- Seed row: `INSERT OR IGNORE INTO schema_version (version) VALUES (0);`

**Skip until Phase 3:** `embeddings` table. The roadmap is explicit. Defining it now
without a consumer would be premature.

## Edge Cases & Silent Failure Modes

- **Forgetting `PRAGMA foreign_keys=ON`.** SQLite ships with FKs *off*; the FK on
  `corrections.document_id` is silently ignored unless the pragma runs at connection time.
  Set it on every new connection (the pragma is connection-scoped, not database-scoped).
- **Auto-format on save (ruff) and `.sql` files.** The PostToolUse hook runs `ruff format`
  on `.py` only — `.sql` is unaffected.
- **`./data/kb.db` is git-ignored** (`*.db` and `data/`). Do not check it in. If a
  developer manually creates `data/`, the path resolves; if not, `init_db` must create the
  parent dir.
- **Trigger-vs-application enforcement.** The append-only triggers will catch *any*
  UPDATE/DELETE — including from a future debugging script. That is the point. If a future
  contributor needs to migrate the column shape of `audit_log`, the migration must `DROP
  TRIGGER` then re-create it after, inside the same migration file.
- **Idempotency of `init_db`.** Every CREATE in `schema.sql` must use `IF NOT EXISTS`. The
  migration runner must be idempotent on partially-applied state — record the version *only
  after* the delta runs successfully, inside the same transaction.
- **`correlation_id` not set.** If a caller forgets to call `new_correlation_id()` at the
  top of their pipeline, `audit_log.append()` will read `None` from contextvars. Decide:
  fail loud (`Failure(...)` + log error) rather than insert a NULL. NULL audit entries are
  the briefing's worst nightmare.
- **JSON encoding for `source_ids`.** Use `json.dumps(...)`. Do not `str(list_)` — that
  serialises with single quotes which `json.loads` can't read back. Round-trip safety is
  required for Phase 8.
- **Thread-local connections + WAL mode.** Two threads writing simultaneously in WAL still
  serialises writes (only one writer at a time), but readers don't block. That is fine for
  our workload; a single CLI invocation is single-writer by design.
- **`updated_by_human` drift.** The vault frontmatter is the user-visible source of truth;
  the SQLite mirror exists only for cheap queries. The `vault/writer.py` rule must keep
  these in sync — but this is a Phase 1 concern, not Phase 0.
- **Empty migration runs.** With only `001_initial.sql` (comment-only) the runner must not
  crash on a file that contains no executable statements. SQLite's `executescript` handles
  this fine — but be sure to wrap in a try/except and surface a clear error if a future
  migration fails mid-flight.
- **`datetime('now')` is UTC.** That is what we want for audit consistency. Briefing reads
  must apply local-time conversion at query time, not at insert time.

## Dependencies & Coupling

```
                       ┌──────────────────┐
                       │ core/config.py   │  CONFIG.main.database.path
                       └────────┬─────────┘
                                │
                       ┌────────▼─────────┐
                       │ storage/db.py    │  init_db, get_connection
                       └────────┬─────────┘
                                │
       ┌────────────────────────┼────────────────────────┐
       │                        │                        │
┌──────▼────────┐      ┌────────▼──────────┐    ┌────────▼─────────┐
│ schema.sql    │      │ migrations/       │    │ audit_log.py     │
│ (version 0)   │      │ 001_initial.sql   │    │ append, query    │
└───────────────┘      └───────────────────┘    └────────┬─────────┘
                                                         │ reads
                                                         ▼
                                                ┌────────────────────┐
                                                │ contextvars        │
                                                │ correlation_id     │
                                                │ (logging_setup.py) │
                                                └────────────────────┘
```

**Downstream consumers (don't build them — just don't break them later):**
- `vault/writer.py` (Phase 0): upserts a `documents` row and writes the markdown body.
  Both writes happen, but the SQL upsert is what makes "is `updated_by_human` true?" cheap.
- `pipelines/capture.py` (Phase 1): writes summary/title to `documents`, then audit row.
- `pipelines/classify.py` (Phase 2): writes note_type/confidence to `documents`, audit row.
- `retrieval/keyword.py` (Phase 3): FTS5 over `documents` (will need a migration to add the
  FTS5 virtual table).
- `briefings/daily.py` (Phase 8): reads `audit_log` filtered by today's date.
- `mcp_server/tools.py` (Phase 4): never touches storage directly — only via pipelines.

## Open Questions

1. **FTS5 in Phase 0 or Phase 3?** The reference creates the FTS5 virtual table on day one
   (alongside `documents`). Our roadmap defers it to Phase 3. Question: do we want the
   `documents_fts` virtual table + sync triggers in `schema.sql` now and only wire the
   *retrieval code* in Phase 3 — or hold the table itself until Phase 3? Defaulting to the
   latter (matches the roadmap), but worth confirming.
2. **Should `audit_log.append()` return `Result` or just rowid?** Project rule says
   `Result` for public functions, but failure to write the audit log is arguably a
   non-recoverable system fault. Probably: return `Result[int]`, where `Failure` always
   carries `recoverable=False`.
3. **Connection management style.** Two viable patterns: a context manager
   (`with get_connection() as conn:`) or thread-local module-level singleton. Reference uses
   a singleton (`let db = null; function getDb()`). For tests we need to point at
   `:memory:` or a tmp file — easier with a context manager that takes an optional
   override. Likely: context-manager-first, with `init_db(path=...)` setting a module-level
   default.
4. **Periodic WAL checkpoint.** The reference runs a `wal_checkpoint(TRUNCATE)` every 5
   minutes via `setInterval` to keep the WAL file small. For a CLI tool that exits after
   each command, this is overkill — the `.shm` and `.wal` files truncate on clean
   close. Skip unless we adopt a long-running daemon (Phase 4 MCP). Document the decision.
5. **`AuditEntry` shape vs. `AIDecision` shape.** They overlap heavily — `action`/`decision`,
   `confidence`, `reasoning`, `source_ids`. Build `AuditEntry` as a thin wrapper that
   accepts an `AIDecision` plus extra fields (`pipeline`, `stage`, `outcome`,
   `correlation_id`)? Likely yes — saves boilerplate at every call site.
6. **Where does `init_db` get called?** The CLI is not built yet. For Phase 0, the smoke
   test (`uv run python test.py` per CLAUDE.md) will be the one place that calls it. After
   Phase 1, `cli/main.py` is the canonical entry. Confirm we are OK with this temporary
   shim.
7. **Should `documents.id` be the relative vault path (string) or a synthetic int?** The
   reference uses `INTEGER PRIMARY KEY AUTOINCREMENT` and stores `vault_path` as a
   separate UNIQUE column. Our docs argue for the path-as-PK approach. Trade-off: ints
   are denser as foreign keys (Phase 7 `corrections` will FK back), strings are
   human-readable in the audit log. Leaning toward path-as-PK because every other system
   in the project (vault writer, briefing, MCP tools) speaks paths natively, and FK
   density doesn't matter at this scale.

## Reference Project Patterns

The reference is a Node.js + `better-sqlite3` implementation, [src/db.js](docs/reference/knowledge-base-server/src/db.js). The architecture is similar but the
patterns diverge in places we should call out:

**What to adopt:**
- **WAL mode + foreign_keys ON at connection time.** Direct lift.
- **`IF NOT EXISTS` on every CREATE.** Idempotent boot. Direct lift.
- **`vault_files` table = our `documents` table.** Same job: file path, content hash,
  type, project, status, summary, key_topics. Their `vault_files` schema is a great
  shopping list for what columns we'll eventually need; we start narrower (Phase 0
  doesn't need `key_topics` or `project`) and add columns via migrations as pipelines
  demand them.
- **`PRAGMA wal_autocheckpoint`.** Cheap, prevents WAL bloat. Adopt: set to 100 pages.
- **Hash-based incremental indexing.** Their `getAllVaultPaths()` + content hash compare
  is exactly what `vault/indexer.py` will need. Plan our `documents.content_hash` column
  to support this from day one even though the indexer is Phase 1.

**What to adapt — diverge intentionally:**
- **No FTS5 in Phase 0.** Reference creates `documents_fts` + three sync triggers up
  front. We hold until Phase 3 and add via migration `002_add_fts5.sql`. The roadmap is
  explicit; don't pre-build.
- **No `embeddings` table in Phase 0.** Same reasoning — reference creates it on day one,
  we defer to Phase 3.
- **`documents.id` strategy.** Reference uses synthetic `INTEGER` PK + a separate
  `vault_files` table mapping path → id. We collapse the two: relative vault path *is*
  the ID. Simpler, no join needed, and we don't need the `documents`/`vault_files` split
  because we don't have multiple "files per logical document" yet.
- **Migration mechanism.** Reference does ad-hoc `PRAGMA table_info` + `ALTER TABLE` at
  boot ([src/db.js:88-95](docs/reference/knowledge-base-server/src/db.js#L88-L95)). That
  works for one project but doesn't scale and makes rollback impossible. Our versioned
  delta approach is intentionally heavier and pays off after Phase 3.
- **No `db.js`-style monolithic file.** Reference puts schema, queries, search,
  statistics — everything — in one file. We split: `db.py` is connection/migration only,
  `audit_log.py` owns audit queries, future `documents.py` (Phase 1) owns documents
  queries.
- **Sync, not async.** Reference uses synchronous `better-sqlite3` (which is the right
  call for SQLite). Python `sqlite3` is also sync. Don't reach for `aiosqlite` — SQLite
  itself serialises writes, so async buys nothing.

**What to skip:**
- The English stop-words search ranking (lines 150–234 of `db.js`). That belongs in
  Phase 3's retrieval layer, not in storage.
- The `setInterval(WAL checkpoint, 5min)` block. CLI lifecycle, not daemon.

## Technical Debt Spotted

- The reference's "migration" lives inside `initSchema` as `PRAGMA table_info` + `ALTER
  TABLE` checks ([src/db.js:88-95](docs/reference/knowledge-base-server/src/db.js#L88-L95)).
  We should not replicate this pattern. Versioned files only.
- The reference duplicates `documents` and `vault_files` rows for every note (one
  `INSERT` into each table per indexed file). Our path-as-PK model avoids the
  duplication; do not regress on this when we copy any of their indexer code in Phase 1.
- The CLAUDE.md "What Claude gets wrong" call-out about `Result` types applies directly:
  every public function in `audit_log.py` and `db.py` must return `Success`/`Failure`,
  not raw values. Internal helpers can raise as long as they're caught at the module
  boundary.
- `core/audit.py` is referenced in [docs/top-level_layout.md](docs/top-level_layout.md)
  but does not yet exist. Phase 0 deliverables imply that audit *writing* lives in
  `core/audit.py` while *storage* lives in `storage/audit_log.py`. Likely split:
  `core/audit.py` = high-level writer that takes an `AIDecision` + pipeline metadata,
  formats into an `AuditEntry`, calls `storage/audit_log.append()`. Confirm before
  building — or fold both into `storage/audit_log.py` and skip `core/audit.py`. The
  roadmap lists `core/audit.py` in Phase 0 ("`core/` — `result.py`, `audit.py`,
  `confidence.py`, `pipeline.py`, ..."), so the split is real.
