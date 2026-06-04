# Discussions: storage_level

Conceptual exchanges during implementation. Source material for flashcards.

---

## Phase 6 — Smoke test wiring
_Completed: 2026-05-10_

### Exchanges

**Q:** Why does `test.py` hardcode `./data/kb.db` instead of reading the path from CONFIG?

**A:** `CONFIG` validates that the vault root exists at import time. The vault path in `config/config.yaml` points to a directory that doesn't exist on this machine — so importing CONFIG raises `ConfigError` before the code even reaches the database path. Hardcoding `./data/kb.db` skips the import entirely. The value matches `config.yaml`'s `database.path` field exactly — it's not a divergence from config, just a way to read one value without triggering the vault check.

```python
@model_validator(mode="after")
def validate_vault_root_exists(self) -> Self:
    if not self.vault.root.exists():   # raises if vault missing
        raise ConfigError(...)
```
_Key concept: Pydantic model_validator runs on every load — can't partially load a model to skip one field_

---

**Q:** Could we read the DB path from `config.yaml` directly instead of hardcoding it?

**A:** Yes, with raw YAML — but it's worse than the hardcode. `config.yaml` is only usable without the vault check if you bypass Pydantic entirely:

```python
import yaml
db_path = Path(yaml.safe_load(open("config/config.yaml"))["database"]["path"])
```

That's an unvalidated raw dict access with no type safety, no defaults, and no error message if the key is missing. The hardcoded value is simpler, matches the config exactly, and is clearly commented explaining why.
_Key concept: raw YAML vs Pydantic loading — bypassing validation loses type safety and defaults_

---

**Q:** Does `test.py` leave anything behind after it exits?

**A:** Yes. `./data/kb.db` persists on disk and accumulates 10 more rows per run (each run's rows are isolated by a unique `correlation_id`, so correctness is unaffected). `data/` is git-ignored so the file won't be committed. Convention for smoke tests is to leave artifacts — they're useful for inspecting written data in a SQLite browser after a failed run. Cleanup can be added with `DB_PATH.unlink(missing_ok=True)` at the end if desired.
_Key concept: smoke test artifacts — intentionally left for post-failure inspection_

---

**Q:** Is `tests/test_storage/test_smoke.py` the same as `test.py`?

**A:** Same intent, different execution context. `test.py` is a manual developer script — uses a persistent DB at `./data/kb.db`, leaves files behind, run with `uv run python test.py`. `test_smoke.py` is the CI-safe version — uses pytest's `tmp_path` fixture (auto-deleted after test), run with `pytest -m smoke`, produces no disk artifacts. Both run the same write-10-rows-then-query flow.
_Key concept: smoke test vs pytest smoke marker — same logic, different isolation and cleanup guarantees_

---

## Phase 5 — `core/audit.py`
_Completed: 2026-05-10_

### Exchanges

**Q:** The `append()` INSERT doesn't include a `timestamp` value — how does timestamp-based querying work?

**A:** `timestamp` has `DEFAULT (datetime('now'))` in the schema, so SQLite fills it automatically at insert time. `query()` filters on it with `date(timestamp) = ?`, which strips the time portion so `"2026-05-10 14:32:00"` matches the filter string `"2026-05-10"`. Tests that need to control the exact timestamp value insert rows directly via SQL with a hardcoded string — production code never sets the field.

```sql
timestamp TEXT NOT NULL DEFAULT (datetime('now'))
```
_Key concept: SQL column defaults — let the DB fill values rather than the application_

---

**Q:** Should `write()` be wrapped in a `@audited(pipeline, stage)` decorator?

**A:** No. `outcome` (AUTO/SUGGEST/CLUELESS) comes from the routing gate, which runs *after* the stage returns. A decorator wrapping the stage sees the `AIDecision` but not the outcome — the gate hasn't run yet. To fix that, the decorator would also have to call the gate, which means it owns routing logic — two responsibilities in one annotation. The explicit `audit.write()` call at the pipeline level is correct because that's the only place where both `decision` and `outcome` exist simultaneously.
_Key concept: decorator scope — a decorator can only see what the function returns, not what happens after_

---

**Q:** Which architecture scales better as the project grows — the current pipeline-owns-sequencing model, or a stage-owns-gate model where each stage calls its own routing gate and audit?

**A:** Current model scales better for this project. Three reasons: (1) Routing is policy, not stage logic — the same `classify` function might use strict thresholds from `capture` and looser thresholds from a re-classification sweep; if the stage owns the gate, thresholds get baked in. (2) Pipelines need to branch on outcome — AUTO goes to vault, SUGGEST to review queue, CLUELESS to inbox; if the stage buries the outcome in a decorator, the pipeline can't branch cleanly. (3) Pure stage functions are independently testable with no DB or gate setup.

Stage-owns-gate is a microservices pattern suited to event-driven architectures (Lambda, Kafka consumers) where each unit publishes its result and has no upstream caller. A sequential pipeline that branches on outcome is the wrong shape for it.
_Key concept: pipeline vs event-driven architecture — where routing logic belongs_

---

**Q:** What is a "stage"?

**A:** A stage is one pure function in a pipeline chain. It does exactly one thing: takes input, returns `Result[T]`. No side effects beyond its one responsibility. Stages are independently testable — you can call `classify(note)` with a test note and assert the `AIDecision` without touching the gate, the DB, or the vault.

```python
raw_content | extract | summarize | classify | store
#             stage     stage       stage      stage
```
_Key concept: pipeline stage — single-responsibility pure function, independently testable_

---

## Phase 4 — `storage/audit_log.py`
_Completed: 2026-05-10_

### Exchanges

**Q:** What does `= ?` mean in a SQL string, and how does the `?` get filled in?

**A:** `?` is SQLite's parameterized query placeholder. Values are never interpolated directly into SQL strings (that opens SQL injection). Instead you pass a separate list — SQLite substitutes each `?` left-to-right with the corresponding value, properly escaped.

```python
conn.execute("SELECT * FROM t WHERE pipeline = ?", ("capture",))
# SQLite replaces ? with 'capture', safely quoted
```
_Key concept: SQL parameterized queries — placeholders prevent injection_

---

**Q:** What do lines 86–87 in `query()` produce?

```python
where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
```

**A:** `" AND ".join(clauses)` concatenates the clause strings with ` AND ` between each. Then `"WHERE "` is prepended. If `clauses` is empty, `where` is an empty string so the SELECT has no WHERE clause at all.

```python
# No filters:
clauses = [] → where = ""

# date only:
clauses = ["date(timestamp) = ?"] → where = "WHERE date(timestamp) = ?"

# date + pipeline:
clauses = ["date(timestamp) = ?", "pipeline = ?"]
→ where = "WHERE date(timestamp) = ? AND pipeline = ?"
```
_Key concept: dynamic WHERE clause construction via list join_

---

**Q:** How do `clauses` and `params` work together throughout `query()`?

**A:** They are built in parallel — every time a clause string with `?` is appended to `clauses`, the matching value is appended to `params` at the same position. `params.append(limit)` runs last, matching the `LIMIT ?` at the end of the SQL string. At `conn.execute(sql, params)`, SQLite pairs each `?` left-to-right with each value in `params`.

```python
# query(date="2026-05-10", correlation_id="abc-123"):
clauses = ["date(timestamp) = ?", "correlation_id = ?"]
params  = ["2026-05-10",          "abc-123",       1000]
#                                                   ↑ limit appended last
```
_Key concept: parallel list construction — clauses and params stay in sync_

---

**Q:** What is the `limit` parameter in `query()` for?

**A:** Cap on rows returned, defaulting to 1000. Without it, `query(date="...")` on a large audit log pulls every row ever written into memory. The 1000 default is safe for normal use; callers can override with a higher value if needed (e.g. Phase 8 briefing needing a full day's entries).
_Key concept: default LIMIT — defensive memory bound on unbounded queries_

---

**Q:** Why does `defaults` have type `dict[str, object]` but is constructed with `dict(...)`? Is `dict()` a function?

**A:** `dict[str, object]` is the type hint — keys are strings, values are `object` (Python's base type, covers any value). The mixed types in `defaults` (`str`, `float`, `list[str]`) have no single common type, so `object` is the catch-all. `dict(key=value, ...)` is the constructor — identical to `{"key": value, ...}` literal syntax, just a style choice. The object has a `.update()` method because all Python dicts have it.

```python
{"pipeline": "test_pipe"}          # literal
dict(pipeline="test_pipe")         # constructor — same result
```
_Key concept: `dict()` constructor vs literal syntax; `object` as catch-all type_

---

**Q:** What does `**kwargs` in a function definition mean?

**A:** `**kwargs` collects all keyword arguments passed by the caller into a dict named `kwargs`. Single `*args` collects positional args into a list; double `**kwargs` collects keyword args into a dict.

`**` also works in reverse at a call site — unpacking a dict into keyword args:

```python
def _entry(**kwargs): ...          # packs: caller's keywords → kwargs dict
AuditEntry(**defaults)             # unpacks: defaults dict → keyword args
```
_Key concept: `**kwargs` — pack keyword args into dict; `**dict` — unpack dict into keyword args_

---

**Q:** Why rename bare `append`/`query` imports to `audit_log.append`/`audit_log.query`?

**A:** `append` and `query` are too generic — mentally collide with list methods and any other module that exports a `query`. Importing the module instead of the functions lets the namespace carry the context: `audit_log.append(entry)` reads clearly without needing a rename. This is the same pattern `core/audit.py` uses when calling into this module.
_Key concept: module-level import for namespace clarity vs bare function import_

---

**Q:** How does `test_append_pulls_correlation_id_from_contextvars` verify the correlation_id flows automatically?

```python
cid = new_correlation_id()
result = audit_log.append(_entry(), db_path=db)
with get_connection(db) as conn:
    row = conn.execute("SELECT correlation_id FROM audit_log WHERE id=?", (result.value,)).fetchone()
assert row[0] == cid
```

**A:** `new_correlation_id()` does two things: generates a UUID and binds it into structlog's contextvars. `append()` never receives the ID explicitly — it reads `get_contextvars().get("correlation_id")` at write time. The test verifies the contract end-to-end by reading the stored row directly via SQL and comparing to what `new_correlation_id()` returned.
_Key concept: contextvars implicit threading — value set once, read anywhere in the same call stack_

---

**Q:** Why does `audit_log.py` have no logger?

**A:** `audit_log.py` is pure storage — INSERT and SELECT only. Logging belongs one level up in `core/audit.py` (the domain façade) or in the pipelines that call it. Adding a logger here would also create a circular concern: the audit log IS the record of what happened; it shouldn't also write to the structured log about writing to itself.
_Key concept: layer separation — storage layer silent, logging at domain/pipeline layer_

---

## Phase 1 — `storage/schema.sql`
_Completed: 2026-05-09_

### Exchanges

**Q:** The Phase 1 surprise note says triggers raise `sqlite3.IntegrityError` instead of `sqlite3.OperationalError` — why does the exception type differ from what the plan expected?

**A:** SQLite's `RAISE(ABORT, 'msg')` inside a trigger maps to `sqlite3.IntegrityError` in Python's `sqlite3` module, not `OperationalError`. Both inherit from `sqlite3.DatabaseError`, so catching the parent class handles either. The plan's test criteria named `OperationalError` but the actual runtime behaviour is `IntegrityError` — Phase 4's trigger test must catch `sqlite3.IntegrityError` or `sqlite3.DatabaseError`.

```python
try:
    conn.execute("UPDATE audit_log SET pipeline='x'")
except sqlite3.IntegrityError as e:   # not OperationalError
    assert "append-only" in str(e)
```
_Key concept: SQLite RAISE(ABORT) → Python IntegrityError, not OperationalError_

---

## Phase 3 — `storage/db.py`
_Completed: 2026-05-10_

### Exchanges

**Q:** What does "inside a single transaction" mean in the migration runner description?

**A:** A transaction is a group of SQL operations that either all succeed or all fail together — atomic. In `_run_migrations`, "inside a single transaction" means the migration DDL and the `UPDATE schema_version SET version = ?` are committed together. Without this, a crash between the migration succeeding and the version update would leave the DB with the new schema but the old version number — the runner would try to re-apply the same migration on next boot. With a transaction, both land or neither does.

Practical caveat: SQLite's `executescript()` auto-commits any pending transaction before running, which breaks naive transaction wrapping. The current implementation works for DDL-only migrations because a failed `executescript` raises before the version UPDATE runs. Destructive migrations in later phases would need `conn.execute()` line-by-line inside an explicit `BEGIN/COMMIT`.
_Key concept: database transaction atomicity — all-or-nothing commit_

---

**Q:** What does "open/close per-context is correct for a single-writer CLI; no thread-local pooling needed" mean?

**A:** Connection pooling keeps a set of already-open DB connections ready so multiple concurrent callers can grab one instantly instead of paying the open cost each time. It matters for web servers handling 100 requests/second simultaneously. A CLI tool runs one command at a time — open, do work, close — and the cost is negligible. `get_connection` opens a fresh connection on entry and closes it on exit. No pool needed until the system becomes a long-running daemon handling concurrent requests.
_Key concept: connection pooling — when it's necessary vs overkill_

---

**Q:** Why does `init_db` not use the `get_connection` context manager?

**A:** `get_connection` commits or rolls back **once** at the end. `init_db` needs to commit **after each migration individually**:

```
migration 001 → commit → version=1
migration 002 → commit → version=2
migration 003 → FAIL   → rollback, version stays 2
```

Using `get_connection` would give one commit at the very end covering all migrations — all-or-nothing. That breaks incremental version-tracking: if migration 003 fails, migrations 001 and 002 would also be rolled back even though they succeeded. `init_db` owns its connection lifecycle because schema boot sequencing can't be expressed as a single transactional block.
_Key concept: per-migration commit vs single end-of-function commit_

---

**Q:** What does `::` mean in a pytest test path like `tests/test_storage/test_db.py::test_init_db_creates_file`?

**A:** It is pytest's addressing syntax. The part before `::` is the file path; the part after is the specific test function inside that file. Running the full path with `::` executes only that one function instead of the entire file.
_Key concept: pytest node ID syntax — file::function_

---

**Q:** The return type of `init_db` is `Result[None]`. Does `Failure[None]` exist?

```python
def init_db(db_path: Path | None = None) -> Result[None]:
```

**A:** `Result[None]` expands to `Success[None] | Failure`. `Failure` has no type parameter — it carries error information (`error`, `recoverable`, `context`), not a value. The `[None]` only describes what `Success` wraps. There is no `Failure[None]` — just `Failure`. `Result[None]` means "succeeds with no useful return value, or fails with error details."

```python
type Result[T] = Success[T] | Failure   # Failure is unparameterised
```
_Key concept: Result type — Success is parameterised, Failure is not_

---

**Q:** Why does `test_init_db_is_idempotent` assert that the version is the same after two calls rather than asserting a specific value?

**A:** The test proves the migration runner doesn't re-apply already-run migrations. The risk is a bug where the second `init_db` call re-applies files that already ran, bumping the version again. Asserting `version_after_second == version_after_first` catches that. The specific value doesn't matter — what matters is it didn't change. (The version is 1 after the first call because `001_initial.sql` is comment-only but still gets processed; asserting `== 1` would also be correct but ties the test to the current state of the migrations folder.)
_Key concept: idempotency testing — assert no change, not a specific value_

---

**Q:** The `test_migration_runner_advances_version` test uses a synthetic `002_test.sql`. Should it also call `init_db` a second time to verify the runner doesn't re-apply 002?

**A:** It could, but that's already covered by `test_init_db_is_idempotent`. Each test proves one thing: `test_migration_runner_advances_version` proves the runner picks up new files and advances the version; `test_init_db_is_idempotent` proves already-applied migrations are skipped on a second call. Adding the idempotency check to the migration test is harmless but redundant.
_Key concept: test isolation — one assertion per test, cross-cutting concerns tested separately_
