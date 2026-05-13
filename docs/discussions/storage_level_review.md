## Code Review — Storage Level
_Completed: 2026-05-13_

### Exchanges

---

**Q:** What is a connection leak? And if `_run_migrations` raises, isn't the error caught by the `except` block — so why is there still a leak?

```python
conn = _connect(resolved)
conn.executescript(_SCHEMA_FILE.read_text())
_run_migrations(conn)
conn.close()           # ← skipped if _run_migrations raises
return Success(None)
except StorageError as exc:
    return Failure(...)  # ← conn.close() never called here
```

**A:** A connection leak is an open database connection that is never explicitly closed. Opening a connection allocates OS resources: a file handle, a WAL lock, a file descriptor. The OS holds these until the connection is closed. In CPython, the garbage collector will eventually close it via reference counting, but you don't control when — so the lock is held in the meantime.

The `except` block does catch the exception, but it only returns `Failure`. It never calls `conn.close()`. Execution jumped from `_run_migrations` to the `except` block, skipping `conn.close()` on the line above. The fix is `try/finally` — `finally` always runs whether the block succeeded, raised, or returned:

```python
conn = _connect(resolved)
try:
    conn.executescript(_SCHEMA_FILE.read_text())
    _run_migrations(conn)
    return Success(None)
finally:
    conn.close()   # runs on success AND exception
```

_Key concept: connection leak / try-finally resource cleanup_

---

**Q:** Why does `list[str]` in a `frozen=True` dataclass allow mutation? I thought `frozen` prevents mutability.

```python
@dataclass(frozen=True)
class AuditEntry:
    source_ids: list[str]
```

**A:** `frozen=True` blocks **reassignment** of the field — `entry.source_ids = ["new"]` raises `FrozenInstanceError`. But it does nothing to stop **mutating the object the field points to** — `entry.source_ids.append("evil")` works silently. `frozen` only protects the reference (the pointer to the list), not the list's contents.

```python
f = Foo(items=["a"])
f.items = ["b"]        # FrozenInstanceError — blocked
f.items.append("b")    # works — not blocked
```

`tuple` is immutable at the data level — no `append`, no `__setitem__`. Use `tuple[str, ...]` when you want the contents frozen too.

_Key concept: Python dataclass frozen vs. deep immutability_

---

**Q:** What does `...` mean in `tuple[str, ...]`?

**A:** `...` is the `Ellipsis` literal. In type hints, `tuple[str, ...]` means "tuple of any length where all elements are `str`" — it's the variable-length tuple annotation.

```python
tuple[str, str]      # exactly 2 strings
tuple[str, int]      # exactly 2 elements: str then int
tuple[str, ...]      # 0 or more strings (variable length)
```

`list[str]` has no length info in its type. `tuple[str, ...]` is its typed fixed-element equivalent.

_Key concept: Python type hint — variable-length tuple_

---

**Q:** For the `executescript` issue — are you saying that migrations 1, 2, 3 stay after migration 4 fails? That's intended, right?

**A:** Yes, that's intended — migrations are cumulative and that's correct behavior. The narrower concern is about a migration file that partially fails mid-way. `executescript()` issues an implicit `COMMIT` before running the script, so any DDL statements it executes are committed immediately. If a later statement in the same file fails, the already-committed DDL can't be rolled back. If `UPDATE schema_version` then also fails, the schema is ahead of what `schema_version` records — so the next boot skips the migration but the DB is in an inconsistent state.

With simple single-statement migrations this rarely triggers. The fix is to document the limitation and keep migration files to a single atomic DDL statement until a proper line-by-line executor is written in Phase 3.

_Key concept: sqlite3 executescript implicit COMMIT — DDL not transactional_

---

**Q:** How does `query()` open a read-write connection? Show me the code that causes it.

```python
with get_connection(db_path) as conn:   # line 95
    rows = conn.execute(sql, params).fetchall()
```

**A:** `get_connection` is a context manager defined in `db.py`. After yielding the connection, it always calls `conn.commit()` — even when the caller only did a `SELECT`:

```python
try:
    yield conn
    conn.commit()   # runs after every yield, even SELECT-only callers
```

The connection itself is opened read-write (SQLite has no read-only mode in this wrapper). The spurious `conn.commit()` is a no-op functionally but triggers a WAL checkpoint. In a tight loop (e.g. the daily briefing iterating audit rows) this accumulates unnecessary overhead. The fix is a `readonly=True` flag that skips the commit.

_Key concept: context manager post-yield side effects / WAL checkpoint pressure_

---

**Q:** Is allowing multiple rows in `schema_version` actually a bad thing?

**A:** Two things break silently:

1. `SELECT version FROM schema_version` with `fetchone()` returns the first row, but "first" is undefined without `ORDER BY`. You could get a stale version, causing migrations to re-run or be skipped.

2. `UPDATE schema_version SET version = ?` with no `WHERE` clause updates **all rows**. After migration 2 runs, every row in the table gets `version = 2` — including hypothetical earlier rows. Data is corrupt but no error is raised.

At Phase 0 with one migration and one row it causes no practical problem. It's a silent landmine that triggers when a second row is accidentally inserted. The fix is a `CHECK (id = 1)` constraint on a primary key column, making a second row a hard error:

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
);
```

_Key concept: single-row table enforcement via CHECK constraint_
