# Plan: storage_level
_Last updated: 2026-05-09_
_Status: [ ] pending_

## Approach

Build the Phase 0 storage layer in six small, independently testable phases:
schema first (no code), then the migration scaffold, then the connection +
migration runner, then the raw audit storage module, then a thin domain façade
(`core/audit.py`) that bridges `AIDecision` → SQL, then a smoke test that ties
everything together. Raw `sqlite3` over an ORM (per the spec) and versioned
`.sql` deltas instead of the reference project's ad-hoc `ALTER TABLE` boot
logic. `documents.id` is a synthetic `INTEGER AUTOINCREMENT` PK — vault paths
are mutable (renames, moves) but integer PKs are stable, preserving all FK
references and audit history across note lifecycle events. Each phase is gated
by a runnable test before the next phase starts.

---

## Phases

### Phase 1 — `storage/schema.sql` (DDL only, no Python)
**Goal**: Land the version-0 schema file. Tables, indexes, triggers, and the
`schema_version` seed row — all idempotent.

**Steps**:
1. Create the `storage/` package (`storage/__init__.py`, empty).

   > `__init__.py` does two things: (1) marks the directory as a Python
   > package so `import storage.db` works at runtime, and (2) lets
   > `setuptools.packages.find` discover the package during `uv build`.
   > Both matter — omit it and imports fail regardless of packaging.

2. Write `storage/schema.sql` with the following design choices:

   **`documents` — the vault index (not the content store)**

   ```sql
   CREATE TABLE IF NOT EXISTS documents (
       id               INTEGER PRIMARY KEY AUTOINCREMENT,
       vault_path       TEXT NOT NULL UNIQUE,
       title            TEXT NOT NULL,
       summary          TEXT,
       note_type        TEXT,
       confidence       REAL,
       created_at       TEXT NOT NULL DEFAULT (datetime('now')),
       updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
       updated_by_human INTEGER NOT NULL DEFAULT 0,
       content_hash     TEXT
   );
   ```

   # RESOLVED: `documents.id` is now an INTEGER AUTOINCREMENT PK. `vault_path` is a separate UNIQUE column. Rationale: notes get renamed and moved constantly; if vault_path were the PK, a renamed note would create a new row and lose all audit history, corrections FKs, and future embedding links that reference the old row. Integer PK is stable across renames — Phase 1's vault indexer will UPDATE vault_path in place when the same content_hash appears at a new path, preserving the row's id and all associated history. Three other alternatives were considered: (a) UUID PK — same benefits as int, but harder to debug; (b) frontmatter `doc_id` field — most robust, survives even content changes, but requires vault writing capability not available in Phase 0; (c) path-as-PK — cheap but breaks on first rename. We default to integer PK + content_hash–based move detection for now, with a note in Open Questions to revisit frontmatter-based identity in Phase 1 when vault writing is available.

   # RESOLVED: `updated_by_human` is a whole-note safety gate, not a fine-grained authorship tracker. Its single purpose: before the AI writes anything to a note, `vault/writer.py` reads this flag — if `updated_by_human = 1`, the AI skips the write (or surfaces a conflict to the user) rather than overwriting human edits. It is set to 1 when the vault indexer detects that a note's frontmatter was last modified outside the system. This is intentionally blunt: one human edit anywhere in the note makes the whole note off-limits to AI writes. Fine-grained tracking of "which sentences or sections are AI-written vs human-written" is a much harder problem and is explicitly out of scope for now — see "Out of Scope" section.

   **`audit_log` — append-only record of every AI decision**

   Each row answers: "At time T, during pipeline P at stage S, the AI looked
   at document(s) X, made decision D with confidence C for reason R, and the
   outcome was O."

   # RESOLVED: `pipeline` = the named workflow that was running, e.g. `"capture"`, `"classify"`, `"promotion"`. Each pipeline is a chain of pure-function stages. `stage` = the specific function within that pipeline that made the AI call, e.g. `"summarize"` or `"route_decision"`. `source_ids` = JSON list of the vault paths (or external IDs) that were the *input* to this AI decision — stored as JSON because a synthesis pipeline might combine 3 notes at once. Together, pipeline+stage+source_ids let you replay exactly what the AI saw and why it decided what it did, which is what Phase 8's daily briefing needs.

   ```sql
   CREATE TABLE IF NOT EXISTS audit_log (
       id             INTEGER PRIMARY KEY AUTOINCREMENT,
       timestamp      TEXT NOT NULL DEFAULT (datetime('now')),
       pipeline       TEXT NOT NULL,
       stage          TEXT NOT NULL,
       source_ids     TEXT NOT NULL,
       decision       TEXT NOT NULL,
       confidence     REAL NOT NULL,
       reasoning      TEXT NOT NULL,
       outcome        TEXT NOT NULL,
       correlation_id TEXT NOT NULL
   );
   ```

   Triggers (belt-and-braces enforcement of append-only):
   ```sql
   CREATE TRIGGER IF NOT EXISTS audit_log_no_update
     BEFORE UPDATE ON audit_log
     BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

   CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
     BEFORE DELETE ON audit_log
     BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
   ```

   **`corrections` — Phase 7 placeholder**

   # RESOLVED: `ON DELETE CASCADE` means: if a `documents` row is deleted, SQLite automatically deletes every `corrections` row whose `document_id` FK points to that deleted document. Without CASCADE, you would get a FK constraint error on document deletion (orphaned rows aren't allowed). With CASCADE, the database stays consistent without any application code.

   ```sql
   CREATE TABLE IF NOT EXISTS corrections (
       id          INTEGER PRIMARY KEY AUTOINCREMENT,
       timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
       document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
       field       TEXT NOT NULL,
       ai_value    TEXT,
       human_value TEXT
   );
   ```

   **`schema_version` — one row, one integer**
   ```sql
   CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
   INSERT OR IGNORE INTO schema_version (version) VALUES (0);
   ```

   **Indexes**:
   ```sql
   CREATE INDEX IF NOT EXISTS idx_documents_note_type  ON documents(note_type);
   CREATE INDEX IF NOT EXISTS idx_documents_updated_at ON documents(updated_at);
   CREATE INDEX IF NOT EXISTS idx_audit_timestamp       ON audit_log(timestamp);
   CREATE INDEX IF NOT EXISTS idx_audit_correlation     ON audit_log(correlation_id);
   CREATE INDEX IF NOT EXISTS idx_audit_pipeline_ts     ON audit_log(pipeline, timestamp);
   ```

3. Use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` /
   `CREATE TRIGGER IF NOT EXISTS` everywhere — safe to re-run.
4. **No** `embeddings` table (Phase 3). **No** FTS5 virtual table (Phase 3).

**Files to modify**:
- `storage/__init__.py` — new, empty.
- `storage/schema.sql` — new, full DDL.

**Test criteria**:

> **What is `sqlite3 :memory: < storage/schema.sql`?**
> # RESOLVED: `sqlite3 :memory:` opens the SQLite3 CLI connected to an in-memory database (`:memory:` is a special SQLite filename meaning "no file on disk — live only while the process runs"). The `<` operator redirects the content of `storage/schema.sql` as input to that CLI, so every SQL statement in the file executes and the result is discarded when the process exits. It is used for testing because: (a) it leaves no files behind, (b) it is fast, (c) it proves the SQL is valid syntax and creates the expected tables. A "one-shot pytest" is just a pytest test function that performs a complete action inline — no shared state, no fixtures needed beyond a database handle — and is designed to run once and be done, not be composed with other tests.

- [ ] `sqlite3 :memory: < storage/schema.sql` exits 0 and creates exactly
      four tables: `documents`, `audit_log`, `corrections`, `schema_version`.
- [ ] `SELECT version FROM schema_version` returns `0`.
- [ ] An `UPDATE audit_log SET pipeline='x'` after inserting a row raises
      with message containing `append-only`.
- [ ] A `DELETE FROM audit_log` raises with the same message.
- [ ] Running the script twice on the same `:memory:` handle does not error
      (idempotent `IF NOT EXISTS` on all DDL).

**Status**: [ ] pending

---

### Phase 2 — `storage/migrations/` scaffold
**Goal**: Prove the migration mechanism exists by adding a comment-only
delta. Phase 3 (embeddings) and Phase 7 (corrections enrichment) drop new
files here without a code change.

**Steps**:
1. Create `storage/migrations/` directory.
2. Add `storage/migrations/001_initial.sql` containing only a SQL comment
   describing the file's role as the post-`schema.sql` placeholder. No
   executable statements — the schema is at version 0; this delta is a
   no-op that exists to exercise the runner.
3. Add `storage/migrations/__init__.py` (empty) so the directory is a
   package and ships with the `setuptools.packages.find` glob.

**Files to modify**:
- `storage/migrations/__init__.py` — new, empty.
- `storage/migrations/001_initial.sql` — new, comment-only.

**Test criteria**:

> **What is `sqlite3.connect(":memory:").executescript(...)`?**
> # RESOLVED: `sqlite3.connect(":memory:")` is Python's stdlib `sqlite3` module opening an in-memory database — same idea as the CLI command above, but from Python code. The returned object is a `Connection` instance (conventionally called `conn`). Calling `.executescript(text)` on it runs a string of SQL statements. Because the database is in-memory, it exists only while the Python process runs and is garbage-collected when the variable goes out of scope — perfect for throwaway tests that shouldn't leave files on disk.

- [ ] `Path("storage/migrations").glob("*.sql")` finds exactly one file
      and its name starts with `001_`.
- [ ] `sqlite3.connect(":memory:").executescript(open("storage/migrations/001_initial.sql").read())`
      exits cleanly even though the file is comment-only.

**Status**: [ ] pending

---

### Phase 3 — `storage/db.py` (connection + migration runner)
**Goal**: Single entrypoint for opening a connection. Boots the schema and
applies any pending migrations. Wraps `sqlite3` errors as `StorageError`
and returns `Result` at module boundaries.

**Steps**:
1. Module-level: read DB path from `CONFIG.main.database.path`. Resolve
   `_PROJECT_ROOT / "storage"` for the schema/migrations files.
2. `init_db(db_path: Path | None = None) -> Result[None]`:
   - Default `db_path` to `CONFIG.main.database.path`.
   - `db_path.parent.mkdir(parents=True, exist_ok=True)` — `data/` is
     git-ignored.
   - Open a fresh connection via `sqlite3.connect(str(db_path))`. Apply
     both PRAGMAs on every new connection: `PRAGMA journal_mode=WAL`,
     `PRAGMA foreign_keys=ON`.

   > **What is `conn.`?**
   > # RESOLVED: `conn` is the variable holding the connection object returned by `sqlite3.connect(...)`. It is a `sqlite3.Connection` instance. Calling `conn.executescript(...)` runs SQL on that connection. Calling `conn.execute(...)` runs a single parameterised query. Calling `conn.commit()` saves pending writes. This is standard Python stdlib object-method syntax — `conn.method()` means "call `method` on the `conn` object".

   - Execute `schema.sql` via `conn.executescript(schema_text)`.
   - Run the migration loop (step 4).
   - Wrap any `sqlite3.Error` as `StorageError` and return
     `Failure(error=str(exc), recoverable=False, context={"db_path": str(db_path)})`.
   - On success: `Success(None)`.
3. `get_connection() -> Iterator[sqlite3.Connection]` — context manager:
   ```python
   @contextmanager
   def get_connection() -> Iterator[sqlite3.Connection]:
       conn = _connect()      # opens connection, applies both PRAGMAs
       try:
           yield conn
           conn.commit()
       except Exception:
           conn.rollback()
           raise
       finally:
           conn.close()
   ```
   Open/close per-context is correct for a single-writer CLI; no thread-local
   pooling needed in Phase 0.
4. `_run_migrations(conn) -> None` (private, raises on failure):
   - `SELECT version FROM schema_version` → current version int.

   > **What is `.glob(...)`?**
   > # RESOLVED: `Path.glob(pattern)` is a method on Python's `pathlib.Path` object. It searches the directory for files matching a shell-style wildcard pattern and returns an iterator of matching `Path` objects. `Path("storage/migrations").glob("[0-9][0-9][0-9]_*.sql")` finds every `.sql` file in that directory whose name starts with exactly three digits — `001_initial.sql`, `002_add_embeddings.sql`, etc. Wrapping in `sorted(...)` guarantees they run in numeric order regardless of filesystem ordering.

   - `sorted(Path(_MIGRATIONS_DIR).glob("[0-9][0-9][0-9]_*.sql"))`.
   - For each file whose leading int > current version: inside a single
     transaction, `executescript(file.read_text())` then
     `UPDATE schema_version SET version = ?`. Commit. On error, rollback
     and re-raise as `StorageError`.
5. Module exports: `init_db`, `get_connection`. Re-export `StorageError`
   from `core.exceptions` for caller convenience.

**Files to modify**:
- `storage/db.py` — new.

**Test criteria**:
- [ ] `tests/test_storage/test_db.py::test_init_db_creates_file` —
      `init_db(tmp_path / "kb.db")` returns `Success`, file exists,
      `SELECT name FROM sqlite_master WHERE type='table'` includes the
      four tables.
- [ ] `test_init_db_is_idempotent` — calling `init_db` twice on the same
      path is a no-op (no error, version unchanged).
- [ ] `test_pragma_foreign_keys_on` — open via `get_connection()`,
      `PRAGMA foreign_keys` returns `1`.
- [ ] `test_pragma_journal_mode_wal` — same, returns `'wal'`.
- [ ] `test_migration_runner_advances_version` — monkeypatch `_MIGRATIONS_DIR`
      to a tmp dir with a synthetic `002_test.sql` (valid SQL: a no-op
      `CREATE TABLE IF NOT EXISTS _test (x INT)`). Assert
      `schema_version.version == 2` after a single boot.
- [ ] `test_migration_failure_rolls_back` — synthetic `003_bad.sql`
      containing `THIS IS NOT SQL`. Assert `init_db` returns `Failure`,
      version is unchanged from 2, table structure is intact.

**Status**: [ ] pending

---

### Phase 4 — `storage/audit_log.py` (raw INSERT + SELECT only)
**Goal**: Dumb SQL storage for the append-only log. No domain logic. Pulls
`correlation_id` from contextvars. Deliberately exposes no `update_*` or
`delete_*` symbols.

# RESOLVED: This module is ONLY responsible for INSERT and SELECT. The domain logic of "take an AIDecision and format it into an AuditEntry" lives in `core/audit.py` (Phase 5). This split keeps storage/ free of domain knowledge and keeps core/ free of SQL. `storage/audit_log.py` knows about rows and connections; `core/audit.py` knows about AIDecision and pipeline metadata. The plan now builds both, in order.

**Steps**:
1. Define `AuditEntry` as a `@dataclass(frozen=True)` mirroring the schema
   columns:
   ```python
   @dataclass(frozen=True)
   class AuditEntry:
       pipeline:       str
       stage:          str
       source_ids:     list[str]
       decision:       str
       confidence:     float
       reasoning:      str
       outcome:        str
       timestamp:      str | None = None       # SQL default if None
       correlation_id: str | None = None       # resolved at append time
   ```

   > **Why `@dataclass` and not Pydantic `BaseModel`?**
   > # RESOLVED: Good instinct. The rule in CLAUDE.md is `Field` = things a human configures, `@property` = things the code computes. `AuditEntry` is neither — it is a structural DTO (data transfer object) between layers. `@dataclass(frozen=True)` is the right choice here because: (a) we want immutability (`frozen=True` prevents accidental mutation after construction), (b) the schema (NOT NULL constraints + triggers) provides the validation that matters, (c) Pydantic adds a dependency and runtime cost for validation we don't need at this boundary. Pydantic stays in `core/config.py` for user-configurable values. `AuditEntry` is an internal DTO — dataclass is appropriate.

2. `append(entry: AuditEntry) -> Result[int]`:
   - Resolve `correlation_id`:

     # RESOLVED: Precedence: (1) use `entry.correlation_id` if explicitly set; (2) fall back to `structlog.contextvars.get_contextvars().get("correlation_id")`; (3) if still None, return `Failure(error="missing correlation_id", recoverable=False, context={"entry": ...})` — fail loud, do not insert a NULL. A NULL correlation_id makes Phase 8's briefing unable to group events by run, breaking the daily digest. The fallback to contextvars is intentional — it means callers don't need to explicitly thread the ID through every function call as long as `new_correlation_id()` was called at the pipeline entry point.

   - `with get_connection() as conn:` — single parameterised INSERT with
     `json.dumps(entry.source_ids)`. Return `Success(cursor.lastrowid)`.
   - On `sqlite3.Error` → `Failure(error=str(exc), recoverable=False, context={...})`.
3. `query(*, date: str | None = None, pipeline: str | None = None,
   correlation_id: str | None = None, limit: int = 1000) -> Result[list[AuditEntry]]`:

   > **Who calls `query` and why?**
   > # RESOLVED: `query` is called by the system on behalf of the user, not by the AI for its own reasoning. Primary consumer: `briefings/daily.py` (Phase 8), which runs `query(date=today)` to get all AI decisions from the past day and format them into the manager's daily briefing. Secondary consumers: (a) `cli/main.py` can expose a `kms audit --date` debug command; (b) Phase 7 self-learning reads corrections alongside audit rows to see which AI decisions the human overrode. The AI never reads its own audit log mid-pipeline.

   - Build a parameterised SELECT with the supplied filters. For `date`:
     `WHERE date(timestamp) = ?` (UTC storage; briefing applies local-time
     conversion at call site).
   - Hydrate rows into `AuditEntry` with `json.loads(source_ids)`.
   - `Success([...])` or `Failure(...)`.
4. Module exports only: `AuditEntry`, `append`, `query`. No `update_*` or
   `delete_*` — enforcement by absence, backed by DB triggers.

**Files to modify**:
- `storage/audit_log.py` — new.

**Test criteria**:
- [ ] `tests/test_storage/test_audit_log.py::test_append_returns_rowid` —
      with `init_db(tmp_path/"kb.db")` and manually bound `correlation_id`,
      `append(AuditEntry(...))` returns `Success(value=1)`, second call
      returns `Success(value=2)`.
- [ ] `test_append_pulls_correlation_id_from_contextvars` —
      call `new_correlation_id()`, then `append(...)` without setting
      `entry.correlation_id`. Query the row via SQL and assert the stored
      UUID matches.
- [ ] `test_append_fails_when_correlation_id_missing` —
      `structlog.contextvars.clear_contextvars()`, then `append(...)`
      returns `Failure(recoverable=False)` with `"missing correlation_id"` in
      the error string.
- [ ] `test_query_filters_by_date` — two entries with different timestamps;
      query with `date=` matching only one; assert one row back.
- [ ] `test_query_filters_by_correlation_id` — two entries under different
      correlation_ids; query for one; assert exactly one `AuditEntry` with
      correct fields.
- [ ] `test_source_ids_round_trip` — append with
      `source_ids=["inbox/a.md", "inbox/b.md"]`; query back; assert a
      `list`, not a string, with both paths intact.
- [ ] `test_append_only_at_module_level` — `import storage.audit_log`,
      `dir()` contains no public symbol matching `^(update|delete)`.
- [ ] `test_trigger_blocks_direct_update` — via `get_connection()`, run raw
      `UPDATE audit_log SET pipeline='x' WHERE id=1` and assert
      `sqlite3.OperationalError` with `"append-only"` in message.

**Status**: [ ] pending

---

### Phase 5 — `core/audit.py` (domain façade)
**Goal**: Thin bridge between `AIDecision` (domain object) and
`storage/audit_log.py` (SQL layer). This is the module every pipeline calls.
`storage/audit_log.py` is never called directly from pipelines.

**Steps**:
1. `write(decision: AIDecision, *, pipeline: str, stage: str, outcome: str) -> Result[int]`:
   - Construct `AuditEntry` from the `AIDecision` fields:
     - `decision` → `entry.decision` (maps `AIDecision.action`)
     - `confidence` → `entry.confidence`
     - `reasoning` → `entry.reasoning`
     - `source_ids` → `entry.source_ids`
     - plus `pipeline`, `stage`, `outcome` from keyword args.
   - Call `storage.audit_log.append(entry)`.
   - Return `Success(rowid)` or propagate `Failure`.
2. No other public functions. No `query` wrapper — callers that need to read
   the log (Phase 8 briefing) use `storage.audit_log.query(...)` directly.
3. Module lives at `core/audit.py`. Import cost is zero — no heavyweight
   deps beyond the modules already in core and storage.

**Files to modify**:
- `core/audit.py` — new.

**Test criteria**:
- [ ] `tests/test_core/test_audit.py::test_write_lifts_aidecision` —
      construct an `AIDecision`, call `audit.write(...)`, assert the
      resulting `AuditEntry` stored in the DB has all five mirrored fields
      (`decision`, `confidence`, `reasoning`, `source_ids`, plus
      `pipeline`, `stage`, `outcome`).
- [ ] `test_write_propagates_failure` — if `append` returns `Failure`,
      `write` propagates it unchanged (no swallowing).
- [ ] `test_write_pulls_correlation_id_via_contextvars` — `new_correlation_id()`,
      then `write(...)`, assert the stored row has a matching
      `correlation_id`.

**Status**: [ ] pending

---

### Phase 6 — Smoke test wiring
**Goal**: Prove the layer holds together end-to-end with the existing
`Result` / `structlog` / config primitives. This is the Phase 0 exit
criterion called out in CLAUDE.md.

**Steps**:
1. Update the existing `test.py` with a real smoke flow:
   - `setup_logging(log_level="DEBUG", dev_mode=True)`.
   - `init_db()` (default path = `CONFIG.main.database.path`).
   - `correlation_id = new_correlation_id()`.
   - Build an `AIDecision(action="classify:Domain/Movies",
     confidence=0.92, reasoning="Strong title match", source_ids=["inbox/x.md"])`.
   - Call `core.audit.write(decision, pipeline="smoke", stage="classify", outcome="AUTO")`
     ten times in a loop.
   - `storage.audit_log.query(correlation_id=correlation_id)` — assert ten
     rows.
   - Print `Success` count + first audit row.
2. Add `tests/test_storage/test_smoke.py` marked `@pytest.mark.smoke` so
   `uv run pytest -m "not smoke"` skips it. Smoke test uses a tmp DB path
   (fixture), not the real `./data/kb.db`.
3. Update CLAUDE.md Phase 0 checklist to tick: `storage/schema.sql`,
   `storage/migrations/`, `storage/db.py`, `storage/audit_log.py`,
   `core/audit.py`, `smoke test`.

**Files to modify**:
- `test.py` — replace existing content.
- `tests/test_storage/__init__.py` — new, empty.
- `tests/test_storage/test_smoke.py` — new, smoke-marked pytest.
- `CLAUDE.md` — tick six checklist items.

**Test criteria**:
- [ ] `uv run python test.py` exits 0 and prints "10 entries written".
- [ ] `uv run pytest tests/test_storage/ -m "not smoke"` passes (Phases 3–4
      unit tests; smoke excluded).
- [ ] `uv run pytest tests/test_storage/test_smoke.py -m smoke` passes
      against tmp DB (no real vault needed).
- [ ] `uv run pytest tests/test_core/` continues to pass (no regression).

**Status**: [ ] pending

---

## Open Questions

1. **Move/rename detection strategy.** The plan uses integer PK +
   `content_hash` for detecting that a file was moved rather than deleted
   and re-created. This requires the Phase 1 vault indexer to run a
   `SELECT id FROM documents WHERE content_hash = ? AND vault_path != ?`
   check before inserting. Decide in Phase 1 whether this is sufficient or
   whether a frontmatter `doc_id` field (written on first capture, survives
   any move or rename) is worth implementing. If notes are frequently edited
   *and* moved simultaneously, content_hash–based detection will fail.
   Frontmatter `doc_id` is the more robust long-term solution but requires
   vault write capability.

2. **Fine-grained AI vs human authorship.** `updated_by_human` is a
   whole-note boolean safety gate. It does NOT track which sentences or
   sections were AI-written vs human-written. If per-section authorship
   becomes a requirement (e.g. for the MCP tool showing humans "the AI
   wrote this summary, you wrote this conclusion"), that is a separate
   design problem — likely involving HTML-style comments or a separate
   `edits` table. Defer to Phase 7 or later.

3. **`wal_autocheckpoint` tuning.** Reference sets `PRAGMA
   wal_autocheckpoint=100`. This is cheap and prevents WAL file bloat on
   long-running sessions. Consider adding it to `_connect()` before Phase 4
   (MCP) — or accept SQLite's default of 1000 pages for now.

## Out of Scope

- `core/pipeline.py` — Phase 0 deliverable, but unrelated to storage.
- `llm/prompt_loader.py`, `vault/` — Phase 0 deliverables outside the storage
  layer.
- The `embeddings` table and FTS5 virtual table — Phase 3.
- Enriching `corrections` with classifier-specific fields — Phase 7.
- Per-section AI vs human authorship tracking — see Open Question 2.
- A daemon-mode WAL checkpoint task — Phase 4 (MCP) revisits.
- Promoting `documents` to store note body or a content cache — the vault is
  the source of truth.
- Adding `project`, `status`, `key_topics` columns to `documents` — add via
  migrations as pipelines demand them, not pre-emptively.
