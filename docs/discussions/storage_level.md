# Discussions: storage_level

Conceptual exchanges during implementation. Source material for flashcards.

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
