# Plan: storage_level
_Last updated: 2026-05-07_
_Status: [ ] pending_

## Approach

Build the Phase 0 storage layer in five small, independently testable phases:
schema first (no code), then the migration scaffold, then the connection +
migration runner, then the audit log module, then a smoke test that ties the
above into the `Result`/`structlog`/config primitives that already exist. Raw
`sqlite3` over an ORM (per the spec) and versioned `.sql` deltas instead of the
reference project's ad-hoc `ALTER TABLE` boot logic — because Phase 3
(embeddings) and Phase 7 (corrections enrichment) will both need real
migrations and we want to scaffold the mechanism once. Each phase is gated by
a runnable test before the next phase starts.

## Phases

### Phase 1 — `storage/schema.sql` (DDL only, no Python)
**Goal**: Land the version-0 schema file. Tables, indexes, triggers, and the
`schema_version` seed row — all idempotent.

**Steps**:
1. Create the `storage/` package (`storage/__init__.py`, empty).
2. Write `storage/schema.sql` containing:
   - `documents` table — `id TEXT PRIMARY KEY` (relative vault path), `title`,
     `summary`, `note_type`, `confidence REAL`, `created_at`, `updated_at`,
     `updated_by_human INTEGER NOT NULL DEFAULT 0`, `content_hash TEXT`. No
     content body.
   - `audit_log` table — `id INTEGER PRIMARY KEY AUTOINCREMENT`, `timestamp`,
     `pipeline`, `stage`, `source_ids` (JSON text), `decision`, `confidence`,
     `reasoning`, `outcome`, `correlation_id`. All NOT NULL except `id` /
     `timestamp` (defaulted).
   - `corrections` table — `id`, `timestamp`, `document_id` FK to
     `documents(id) ON DELETE CASCADE`, `field`, `ai_value`, `human_value`.
   - `schema_version` table — single `version INTEGER NOT NULL` column.
   - Indexes: `audit_log(timestamp)`, `audit_log(correlation_id)`,
     `audit_log(pipeline, timestamp)`, `documents(note_type)`,
     `documents(updated_at)`.
   - Triggers `audit_log_no_update` and `audit_log_no_delete` that
     `RAISE(ABORT, 'audit_log is append-only')`.
   - `INSERT OR IGNORE INTO schema_version (version) VALUES (0);`
3. Use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` /
   `CREATE TRIGGER IF NOT EXISTS` everywhere so the file is safe to re-run.
4. **No** `embeddings` table (deferred to Phase 3 per roadmap). **No** FTS5
   virtual table (deferred to Phase 3).

**Files to modify**:
- `storage/__init__.py` — new, empty.
- `storage/schema.sql` — new, the full DDL above.

**Test criteria**:
- [ ] Running `sqlite3 :memory: < storage/schema.sql` (or via a one-shot
      pytest) exits 0 and creates exactly four tables: `documents`,
      `audit_log`, `corrections`, `schema_version`.
- [ ] `SELECT version FROM schema_version` returns `0`.
- [ ] An `UPDATE audit_log SET pipeline='x'` after inserting a row raises
      with message containing `append-only`.
- [ ] A `DELETE FROM audit_log` raises with the same message.
- [ ] Running the script twice does not error (idempotent CREATEs).

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
- [ ] `Path("storage/migrations").glob("*.sql")` finds exactly one file
      and its name starts with `001_`.
- [ ] `sqlite3.connect(":memory:").executescript(open(...).read())` on the
      file exits cleanly even though it is comment-only.

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
   - Open a fresh connection. Apply both PRAGMAs on every new connection:
     `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`. (`foreign_keys`
     is per-connection, not per-database — the default is OFF.)
   - Execute `schema.sql` via `conn.executescript(...)`.
   - Run the migration loop (see step 4).
   - Wrap any `sqlite3.Error` as `StorageError` and return
     `Failure(error=str(exc), recoverable=False, context={"db_path": ...})`.
3. `get_connection() -> sqlite3.Connection` — context-manager-first:
   ```python
   @contextmanager
   def get_connection() -> Iterator[sqlite3.Connection]:
       conn = _connect()
       try:
           yield conn
           conn.commit()
       except Exception:
           conn.rollback()
           raise
       finally:
           conn.close()
   ```
   `_connect()` is a private helper that opens the connection and applies
   the two PRAGMAs. Sync stdlib `sqlite3`, no thread-local pooling for
   Phase 0 — open/close per-context is fine for a single-writer CLI.
4. `_run_migrations(conn) -> None`:
   - `SELECT version FROM schema_version` → current version.
   - `sorted(Path("storage/migrations").glob("[0-9][0-9][0-9]_*.sql"))`.
   - For each file whose leading int > current version, in a single
     transaction: `executescript(file.read_text())` then
     `UPDATE schema_version SET version = ?`. Commit. On error, rollback
     and re-raise.
5. Module exports: `init_db`, `get_connection`, `StorageError` (re-export
   from `core.exceptions` for caller convenience).

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
- [ ] `test_migration_runner_advances_version` — drop a synthetic
      `002_test.sql` into a tmp migrations dir, point `init_db` at it,
      assert `schema_version.version == 2` after a single boot. (Use
      `monkeypatch` to swap the migrations dir.)
- [ ] `test_migration_failure_rolls_back` — synthetic `003_bad.sql`
      containing `THIS IS NOT SQL`. Assert `init_db` returns `Failure`,
      version is unchanged from the previous good state, and the table
      structure matches the pre-migration state.

**Status**: [ ] pending

---

### Phase 4 — `storage/audit_log.py` (append + query)
**Goal**: Dumb storage for the append-only log. No business logic. Pulls
`correlation_id` from contextvars so callers don't have to pass it.

**Steps**:
1. Define `AuditEntry` as a `@dataclass(frozen=True)` mirroring the
   schema columns:
   ```python
   @dataclass(frozen=True)
   class AuditEntry:
       pipeline: str
       stage: str
       source_ids: list[str]
       decision: str
       confidence: float
       reasoning: str
       outcome: str
       # Auto-populated if not supplied:
       timestamp: str | None = None       # defaults to datetime('now') in SQL
       correlation_id: str | None = None  # pulled from contextvars at append time
   ```
2. `from_decision(decision: AIDecision, *, pipeline: str, stage: str, outcome: str) -> AuditEntry`
   constructor that lifts an `AIDecision` (action → decision, confidence,
   reasoning, source_ids) into an `AuditEntry`. Saves boilerplate at
   call sites.
3. `append(entry: AuditEntry) -> Result[int]`:
   - Resolve `correlation_id`: prefer `entry.correlation_id`; otherwise
     read from `structlog.contextvars.get_contextvars()["correlation_id"]`.
   - If still missing, return
     `Failure(error="missing correlation_id", recoverable=False, context={...})`.
     A NULL correlation_id breaks Phase 8's briefing — fail loud.
   - Use `get_connection()` from `db.py`. Single parameterised INSERT
     with `json.dumps(entry.source_ids)` for the JSON column.
     Returns `Success(cursor.lastrowid)`.
   - On `sqlite3.Error` → `Failure(error=..., recoverable=False, ...)`.
4. `query(*, date: str | None = None, pipeline: str | None = None,
   correlation_id: str | None = None, limit: int = 1000)
   -> Result[list[AuditEntry]]`:
   - Build a parameterised SELECT with the supplied filters; for `date`
     use `WHERE date(timestamp) = ?` (UTC — the briefing applies
     local-time conversion at call site, not here).
   - Hydrate rows back into `AuditEntry` (with `json.loads(source_ids)`).
   - `Success([...])` or `Failure(...)`.
5. Module deliberately exports no `update_*` or `delete_*` functions.
   Append-only is enforced both by the trigger (Phase 1) and by the
   absence of these symbols.

**Files to modify**:
- `storage/audit_log.py` — new.

**Test criteria**:
- [ ] `tests/test_storage/test_audit_log.py::test_append_returns_rowid` —
      with `init_db(tmp_path/"kb.db")` and a manually bound
      `correlation_id`, `append(AuditEntry(...))` returns
      `Success(value=1)` then `Success(value=2)` on a second call.
- [ ] `test_append_pulls_correlation_id_from_contextvars` —
      `new_correlation_id()`, then `append(...)` without setting
      `entry.correlation_id`. Query the row directly via SQL and assert
      the stored UUID matches.
- [ ] `test_append_fails_when_correlation_id_missing` —
      `clear_contextvars()`, then `append(...)` returns
      `Failure(recoverable=False)` with `"missing correlation_id"` in the
      error.
- [ ] `test_query_filters_by_date` — append two entries with different
      `timestamp`, query with `date=` matching only one, assert one row
      back.
- [ ] `test_query_filters_by_correlation_id` — append two entries under
      different correlation_ids, query for one, assert exactly one
      `AuditEntry` returned with the right fields.
- [ ] `test_source_ids_round_trip` — append with
      `source_ids=["inbox/a.md", "inbox/b.md"]`, query back, assert the
      list (not a string) is recovered intact.
- [ ] `test_append_only_at_module_level` — `import storage.audit_log` and
      assert there are no public symbols matching `^(update|delete)`
      (introspect with `dir()`).
- [ ] `test_trigger_blocks_direct_update` (belt-and-braces) — using
      `get_connection()`, run a raw `UPDATE audit_log SET ...` and assert
      `sqlite3.IntegrityError` (or whichever error class the trigger
      raises) with `"append-only"` in the message.
- [ ] `test_from_decision_lifts_aidecision` — construct an
      `AIDecision`, call `AuditEntry.from_decision(...)`, assert all
      five mirrored fields match.

**Status**: [ ] pending

---

### Phase 5 — Smoke test wiring
**Goal**: Prove the layer holds together end-to-end with the existing
`Result` / `structlog` / config primitives. This is the Phase 0 exit
criterion called out in CLAUDE.md.

**Steps**:
1. Update the existing `test.py` (or replace it — it currently does an
   ad-hoc Failure demo) with a real smoke flow:
   - `setup_logging(log_level="DEBUG", dev_mode=True)`.
   - `init_db()` (default path = `CONFIG.main.database.path`).
   - `correlation_id = new_correlation_id()`.
   - Build an `AIDecision` (action="classify:Domain/Movies",
     confidence=0.92, reasoning="…", source_ids=["inbox/x.md"]).
   - `entry = AuditEntry.from_decision(decision, pipeline="smoke",
     stage="classify", outcome="AUTO")`.
   - `append(entry)` ten times in a loop.
   - `query(correlation_id=correlation_id)` — assert ten rows.
   - Print `Success` count + first audit row.
2. Add a pytest equivalent at `tests/test_storage/test_smoke.py` marked
   `@pytest.mark.smoke` so `uv run pytest -m "not smoke"` skips it. The
   marker already exists in `pyproject.toml`.
3. Update CLAUDE.md's Phase 0 checklist to tick `storage/schema.sql`,
   `storage/migrations/`, `storage/db.py`, and `smoke test`. Leave
   `core/pipeline.py`, `llm/ + prompts/`, `vault/` untouched — those
   are out of scope here.

**Files to modify**:
- `test.py` — replace existing content with the smoke flow above.
- `tests/test_storage/__init__.py` — new, empty.
- `tests/test_storage/test_smoke.py` — new, smoke-marked pytest.
- `CLAUDE.md` — tick four checklist items.

**Test criteria**:
- [ ] `uv run python test.py` exits 0 and prints "10 entries written".
- [ ] `uv run pytest tests/test_storage/ -m "not smoke"` passes (covers
      Phases 3 and 4 unit tests; smoke is excluded).
- [ ] `uv run pytest tests/test_storage/test_smoke.py -m smoke` passes
      against a tmp DB path (override via fixture, not the real
      `./data/kb.db`).
- [ ] `uv run pytest tests/test_core/` continues to pass (no regression
      to existing core tests).

**Status**: [ ] pending

---

## Open Questions

These are the items from `research/storage_level.md` that the plan has
*provisionally* resolved but the human should sanity-check before
implementation. Annotate with `# QUESTION:` if any of these are wrong.

1. **`documents.id` = TEXT (relative vault path)**, not a synthetic
   `INTEGER`. Plan picks path-as-PK because every other system in the
   project speaks paths natively. Accept?
2. **No FTS5 in Phase 0**, deferred to a Phase 3 migration
   (`002_add_fts5.sql`). Roadmap is explicit; plan obeys.
3. **No periodic `wal_checkpoint`** background task. Reference runs one
   every 5 minutes for the long-running daemon — we are CLI-first, so
   skip until Phase 4 (MCP) makes us a daemon.
4. **`audit_log.append()` returns `Result[int]`**, with `Failure` always
   carrying `recoverable=False`. Failed audit writes are system faults,
   not retryable input errors.
5. **Connection management = context manager** (`with get_connection():`),
   not a module-level thread-local singleton. Easier to override in tests.
6. **`AuditEntry.from_decision(...)` constructor** that wraps an
   `AIDecision` plus pipeline/stage/outcome. Saves boilerplate at every
   call site that already has an `AIDecision` in hand.
7. **Where does `core/audit.py` fit?** The roadmap lists it as a Phase 0
   deliverable distinct from `storage/audit_log.py`. This plan folds the
   "decision → entry → append" composition into
   `AuditEntry.from_decision` + `audit_log.append`, leaving `core/audit.py`
   unbuilt. If the human wants `core/audit.py` to exist as a thin façade
   (e.g. `audit.write(decision, pipeline, stage, outcome)` that calls the
   storage module), add a Phase 4.5 — but the current plan considers it
   redundant.

## Out of Scope

- `core/pipeline.py` — Phase 0 deliverable, but unrelated to storage.
- `core/audit.py` — see Open Question 7.
- `llm/prompt_loader.py`, `vault/` — Phase 0 deliverables outside the
  storage layer.
- The `embeddings` table and FTS5 virtual table — Phase 3.
- Enriching `corrections` with classifier-specific fields (e.g. the
  original AI confidence) — Phase 7.
- A daemon-mode WAL checkpoint task — Phase 4 (MCP) revisits.
- Promoting `documents` to also store the body or a content cache —
  intentionally not done; the vault is the source of truth.
- Adding columns to `documents` for `project`, `status`, `key_topics`
  (visible in the reference's `vault_files`) — add via migrations as
  pipelines start needing them, not pre-emptively.
