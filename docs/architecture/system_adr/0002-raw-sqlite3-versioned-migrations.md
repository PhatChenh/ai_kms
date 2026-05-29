# Raw sqlite3 over ORM; versioned .sql deltas in migrations/

Stdlib `sqlite3` with no SQLAlchemy. Schema changes land as numbered `.sql` files (`001_initial.sql`, `002_...`, etc.) applied by `db.py`'s migration runner.

**Status:** accepted

**Considered Options**

- Reference project's ad-hoc `PRAGMA table_info` + `ALTER TABLE` at boot — rejected: doesn't scale past 1-2 migrations, makes rollback impossible.
- `aiosqlite` for async — rejected: SQLite serialises writes natively, async buys nothing here.

**Consequences**

- ALL schema changes must land as new `.sql` files in `storage/migrations/`. No in-code `ALTER TABLE`. Phase 3 adds `002_add_fts5.sql`; Phase 7 adds `003_enrich_corrections.sql`. Never `DROP TRIGGER` mid-migration without re-creating it in the same file.
