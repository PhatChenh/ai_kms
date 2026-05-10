# STATE.md — Cross-Session Project State
_Created: 2026-05-09_
_Last updated: 2026-05-09_

## Current Position
**Phase**: Phase 0 — Foundations

**Checklist**:
- [x] core/exceptions.py
- [x] core/result.py
- [x] core/logging_setup.py
- [x] core/config.py
- [x] core/confidence.py _(exists on disk; not in CLAUDE.md checklist but listed in roadmap Phase 0)_
- [x] llm/ _(provider.py, claude_provider.py, ollama_provider.py exist on disk; CLAUDE.md marks unchecked — prompts/ still empty)_
- [ ] core/audit.py
- [ ] core/pipeline.py
- [ ] storage/schema.sql
- [ ] storage/migrations/
- [ ] storage/db.py
- [ ] storage/audit_log.py
- [ ] prompts/ (empty)
- [ ] vault/
- [ ] smoke test

**Next planned work**: `docs/plans/storage_level.md` — 6-phase plan covering schema.sql → migrations scaffold → db.py → audit_log.py → core/audit.py → smoke test. All phases pending.

---

## Architecture Decisions

### [DECISION-001] `documents.id` is INTEGER AUTOINCREMENT; `vault_path` is separate UNIQUE column
- **Source**: `docs/plans/storage_level.md` — `# RESOLVED` in Phase 1 DDL section
- **Decision**: Synthetic integer PK for `documents`; vault path stored as a UNIQUE but mutable column.
- **Alternatives considered**: (a) UUID PK — same stability benefit, harder to debug; (b) frontmatter `doc_id` field — most robust but requires vault write capability not available in Phase 0; (c) path-as-PK — cheap but broken on first rename.
- **Rationale**: Notes are renamed and moved constantly. If `vault_path` were the PK, a renamed note would orphan all FK references in `audit_log`, `corrections`, and future `embeddings`. Integer PK is stable across renames; Phase 1 vault indexer detects moves via `content_hash` and UPDATEs `vault_path` in place.
- **Constraint for future phases**: Phase 1 vault indexer MUST run `SELECT id FROM documents WHERE content_hash = ? AND vault_path != ?` before inserting to detect moves. All FKs (`corrections.document_id`, future `embeddings.document_id`) reference `documents.id` (integer), not the path string. _(Note: `docs/research/storage_level.md` described path-as-PK — the plan supersedes this.)_

### [DECISION-002] `updated_by_human` is a whole-note boolean safety gate, not per-field authorship
- **Source**: `docs/plans/storage_level.md` — `# RESOLVED` in Phase 1 DDL section
- **Decision**: A single `INTEGER NOT NULL DEFAULT 0` column on `documents`. If 1, the AI skips the write (or surfaces a conflict) for the entire note.
- **Alternatives considered**: Per-section authorship tracking (HTML-style comments or a separate `edits` table).
- **Rationale**: Intentionally blunt — one human edit anywhere in the note makes the whole note off-limits to AI writes. Fine-grained tracking is a harder problem, explicitly out of scope.
- **Constraint for future phases**: `vault/writer.py` MUST check `updated_by_human` before every AI write. Per-section authorship is deferred to Phase 7 or later (Open Question Q-002). Any future implementation requires a separate design — do not extend this column.

### [DECISION-003] `audit_log` columns: `pipeline` = named workflow, `stage` = pure-function name, `source_ids` = JSON list
- **Source**: `docs/plans/storage_level.md` — `# RESOLVED` in `audit_log` DDL section
- **Decision**: Three columns (`pipeline`, `stage`, `source_ids`) are the AI's "what did you look at and at which step" record.
- **Rationale**: Phase 8 (daily briefing) needs to reconstruct exactly what the AI saw and why it decided what it did. `source_ids` is a JSON list because synthesis pipelines can combine multiple notes in a single decision.
- **Constraint for future phases**: Every pipeline stage that makes an AI decision MUST populate `pipeline`, `stage`, and `source_ids`. Phase 8 briefing reads these columns as its primary input. `json.dumps(list)` — never `str(list)` — to ensure round-trip safety with `json.loads`.

### [DECISION-004] `storage/audit_log.py` = dumb SQL only; `core/audit.py` = domain façade
- **Source**: `docs/plans/storage_level.md` — `# RESOLVED` in Phase 4 section
- **Decision**: `storage/audit_log.py` owns `AuditEntry` dataclass, `append()`, and `query()`. `core/audit.py` translates `AIDecision` + pipeline metadata into an `AuditEntry` and calls `append()`.
- **Rationale**: `storage/` must be free of domain knowledge; `core/` must be free of SQL. The split keeps both layers independently testable.
- **Constraint for future phases**: Pipelines call `core.audit.write(...)`, NEVER `storage.audit_log.append(...)` directly. `storage/audit_log.py` exposes no `update_*` or `delete_*` symbols — absence is the enforcement mechanism, backed by DB triggers.

### [DECISION-005] `AuditEntry` is `@dataclass(frozen=True)`, not Pydantic `BaseModel`
- **Source**: `docs/plans/storage_level.md` — `# RESOLVED` in Phase 4 `AuditEntry` definition
- **Decision**: `@dataclass(frozen=True)` for internal DTO immutability; Pydantic stays in `core/config.py`.
- **Rationale**: `AuditEntry` is an internal DTO between storage layers. DB schema + triggers provide the validation that matters. Pydantic is for user-configurable values (`Field`) or computed properties (`@property`).
- **Constraint for future phases**: The rule from CLAUDE.md holds: `Field` = human-configurable values; `@property` = code-computed values; `@dataclass` = internal DTOs. Do not use Pydantic for storage-layer data objects.

### [DECISION-006] `correlation_id` on `AuditEntry`: explicit > contextvars > hard Failure (no NULL inserts)
- **Source**: `docs/plans/storage_level.md` — `# RESOLVED` in Phase 4 `append()` section
- **Decision**: Precedence: (1) `entry.correlation_id` if set; (2) `structlog.contextvars.get_contextvars().get("correlation_id")`; (3) `Failure(recoverable=False)` — never insert a NULL.
- **Rationale**: NULL `correlation_id` makes Phase 8 briefing unable to group events by pipeline run, breaking the daily digest.
- **Constraint for future phases**: Every pipeline entry point MUST call `new_correlation_id()` from `core/logging_setup.py`. The contextvars fallback means callers don't have to thread the ID explicitly — but they must set it at the top.

### [DECISION-007] Raw `sqlite3` over ORM; versioned `.sql` deltas in `migrations/`
- **Source**: `docs/plans/storage_level.md` — Approach section
- **Decision**: Stdlib `sqlite3` (no SQLAlchemy). Schema changes via numbered `.sql` files (`001_initial.sql`, `002_...`, etc.) applied by `db.py`'s migration runner.
- **Alternatives considered**: Reference project's ad-hoc `PRAGMA table_info` + `ALTER TABLE` at boot; `aiosqlite` for async.
- **Rationale**: Versioned deltas allow rollback; reference's approach doesn't scale past 1-2 migrations and makes rollback impossible. SQLite serialises writes natively — `aiosqlite` buys nothing.
- **Constraint for future phases**: ALL schema changes land as new `.sql` files in `storage/migrations/`. No in-code `ALTER TABLE`. Phase 3 adds `002_add_fts5.sql`; Phase 7 adds `003_enrich_corrections.sql`. Never `DROP TRIGGER` mid-migration unless the migration also re-creates the trigger in the same file.

### [DECISION-008] `corrections.document_id` FK uses `ON DELETE CASCADE`
- **Source**: `docs/plans/storage_level.md` — `# RESOLVED` in `corrections` DDL section
- **Decision**: `document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE`
- **Rationale**: Without CASCADE, deleting a `documents` row raises a FK constraint error (orphaned `corrections`). WITH CASCADE, the DB stays consistent with no application code. `PRAGMA foreign_keys=ON` must be set on every connection for this to fire.
- **Constraint for future phases**: Phase 7 self-learning MUST rely on CASCADE for corrections cleanup — do not implement manual deletion in application code. `PRAGMA foreign_keys=ON` is set in every `_connect()` call (not just once at boot).

### [DECISION-009] `get_connection()` is a context manager; open/close per-context (no connection pool)
- **Source**: `docs/plans/storage_level.md` — Phase 3 `get_connection()` design
- **Decision**: Each `with get_connection() as conn:` opens, uses, commits or rolls back, and closes. No thread-local singleton or pool.
- **Rationale**: Single-writer CLI. Simplicity trumps pooling overhead at this scale.
- **Constraint for future phases**: Phase 4 (MCP server, long-running process) should revisit this. A daemon with many short-lived tool calls will pay per-call connection overhead. At that point, a thread-local singleton or connection pool becomes relevant. Flag in Phase 4 planning.

---

## Technical Debt

| ID | What | Why deferred | Owned by phase | Source |
|---|---|---|---|---|
| TD-001 | `core/pipeline.py` | Unrelated to storage layer; parallel Phase 0 deliverable | Phase 0 | Out of Scope, `plans/storage_level.md` |
| TD-002 | `llm/prompt_loader.py` and `prompts/` (empty) | Outside storage scope; llm/ providers exist but no prompt loading | Phase 0 | Out of Scope, `plans/storage_level.md` |
| TD-003 | `vault/` (paths, frontmatter, reader, writer) | Outside storage scope | Phase 0 | Out of Scope, `plans/storage_level.md` |
| TD-004 | `embeddings` table + FTS5 virtual table | No consumer until retrieval layer exists | Phase 3 | Out of Scope, `plans/storage_level.md` |
| TD-005 | `corrections` enrichment with classifier-specific fields | Placeholder table exists; fields added when self-learning is built | Phase 7 | Out of Scope, `plans/storage_level.md` |
| TD-006 | Per-section AI vs human authorship tracking | Hard problem; `updated_by_human` blunt gate is sufficient now | Phase 7+ | Open Question Q-002, `plans/storage_level.md` |
| TD-007 | Daemon-mode WAL checkpoint (`wal_autocheckpoint`) | CLI exits cleanly; WAL truncates on close; irrelevant until MCP daemon | Phase 4 | Out of Scope + Open Question Q-003, `plans/storage_level.md` |
| TD-008 | `documents` columns: `project`, `status`, `key_topics` | Add via migrations when pipelines demand them; not pre-emptively | Phase 2+ | Out of Scope, `plans/storage_level.md` |
| TD-009 | `updated_by_human` sync between frontmatter and SQLite | SQLite mirror exists for cheap queries; sync logic is vault/writer.py concern | Phase 1 | research/storage_level.md edge cases |

---

## Cross-Phase Constraints

- **Vault is source of truth for note content.** `documents` table is an index only — it never stores note body or serves as a content cache. `vault/writer.py` is the only code that writes to the vault.
- **`updated_by_human = 1` means hands off.** `vault/writer.py` MUST check this before every AI write. If true: skip write or surface a conflict. No exceptions.
- **Audit log is non-negotiable from Phase 1.** Phase 8 briefing reads from it. A pipeline that skips audit writes produces a silent gap in the daily digest.
- **Every public function in `handlers/` and `pipelines/` returns `Success` or `Failure`.** Raw values and `None` returns are forbidden at module boundaries.
- **All thresholds live in `config/thresholds.yaml`, never in code.** Float literals in `if`/`elif` comparisons inside `pipelines/` are a hard-block hook violation.
- **All prompts are YAML files in `prompts/`.** f-strings containing prompt-like keywords in `.py` files trigger a hook warning.
- **`mcp_server/tools.py` is logic-free.** `if`, `elif`, `for`, `while` at statement level is a hard-block hook violation. Tools call pipelines; pipelines do the work.
- **Never add an MCP tool before its pipeline exists and is tested.** A stub tool that calls nothing misleads the demo.
- **Schedulers come last in each phase.** Build manual CLI first, automate second.
- **`PRAGMA foreign_keys=ON` on every new connection.** The pragma is connection-scoped; forgetting it silently disables FK enforcement including `ON DELETE CASCADE` on `corrections`.
- **All schema changes via versioned `.sql` deltas.** No in-code `ALTER TABLE`. Migration runner applies in lexical order and records version in `schema_version`.

---

## Open Questions

| ID | Question | Blocks | Status |
|---|---|---|---|
| Q-001 | Move/rename detection: integer PK + `content_hash` vs frontmatter `doc_id`. If a note is edited AND moved simultaneously, content_hash–based detection fails. Is this sufficient for Phase 1, or does Phase 1 need to write `doc_id` to frontmatter? | Phase 1 vault indexer | 🔴 Open |
| Q-002 | Fine-grained AI vs human authorship per section. `updated_by_human` is whole-note only. If MCP tool needs to show "AI wrote this summary, you wrote this conclusion," a separate design (HTML comments or `edits` table) is required. | Phase 7+ | 🔴 Open |
| Q-003 | `wal_autocheckpoint` tuning. Reference sets to 100 pages; SQLite default is 1000. Worth adding to `_connect()` before Phase 4 MCP (long-running daemon), or accept default for CLI? | Phase 4 | 🔴 Open |
