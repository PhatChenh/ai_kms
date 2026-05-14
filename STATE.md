# STATE.md — Cross-Session Project State
_Created: 2026-05-09_
_Last updated: 2026-05-14 (llm_layer session)_

## Current Position
**Phase**: Phase 0 — Foundations

**Checklist**:
- [x] core/exceptions.py
- [x] core/result.py
- [x] core/logging_setup.py
- [x] core/config.py
- [x] core/confidence.py _(exists on disk; not in CLAUDE.md checklist but listed in roadmap Phase 0)_
- [x] llm/ _(provider.py, claude_provider.py, ollama_provider.py, openai_provider.py, prompt_loader.py — all async LLMProvider ABC — complete 2026-05-14)_
- [x] core/audit.py
- [x] core/pipeline.py
- [x] storage/schema.sql
- [x] storage/migrations/
- [x] storage/db.py
- [x] storage/audit_log.py
- [x] prompts/ _(prompts/test.yaml exists; prompt_loader.py loads eagerly — complete 2026-05-14)_
- [ ] vault/
- [x] smoke test

**Next planned work**: Phase 0 remaining: `vault/`. After that, Phase 1 (Capture + Classify + Search, targeting M1 ~15 May 2026).

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

### [DECISION-010] `run_pipeline` is async; stages are `async def` top-level functions
- **Source**: `docs/plans/pipeline.md` — Approach + OQ-P2 resolution
- **Decision**: `run_pipeline` is an `async def`. Every stage must be a top-level `async def` with a meaningful `__name__`. Bound methods and lambdas must be wrapped.
- **Alternatives considered**: Sync runner (simpler now, requires rewrite later); `asyncio.gather` for parallel stages (breaks sequential dependency between stages).
- **Rationale**: Phase 4 MCP daemon serves concurrent requests. With async, while pipeline A awaits an LLM call, the event loop can run pipeline B's CPU-bound stage. Retrofitting async later touches every call site. The async skeleton is free infrastructure investment.
- **Constraint for future phases**: Phase 1 CLI wraps each Click command with `asyncio.run(_async_fn())` — no `click-anyio` dependency. Phase 4 must address concurrent-run contextvars bleed (OQ-P3 / Q-004).

### [DECISION-011] `run_pipeline` catches `Exception`, not `BaseException`
- **Source**: `docs/plans/pipeline.md` — OQ-P1 resolution (amended during code review 2026-05-14)
- **Decision**: `except Exception as exc` — stage bugs are caught and returned as `Failure`; `SystemExit`, `KeyboardInterrupt`, `GeneratorExit` propagate normally.
- **Alternatives considered**: `BaseException` (plan originally specified this; rejected — swallows signals); `PipelineError` only (too narrow — stages can throw any exception type).
- **Rationale**: `run_pipeline` guarantees callers always receive a `Result`, never a raw exception. But process-level signals must escape — catching `BaseException` would make Ctrl-C unresponsive.
- **Constraint for future phases**: Stages must return `Failure` for expected errors. The `except Exception` catch is a safety net for stage *bugs*, not a substitute for proper error handling.

### [DECISION-013] `LLMProvider` ABC: single async `complete(system, user) -> Result[LLMResponse]` method
- **Source**: `docs/plans/llm_layer.md` — Approach + Phase 3 steps
- **Decision**: All providers implement one abstract async method. `LLMResponse` is a frozen dataclass (`content: str`, `model: str`, `usage: dict`). Factory is `get_provider(task, config) -> LLMProvider` in `llm/provider.py`.
- **Alternatives considered**: `embed()` on the ABC (rejected — embeddings are `sentence-transformers` responsibility); sync interface (rejected — blocks event loop, violates DECISION-010); `json_mode` parameter (rejected — JSON formatting belongs in prompt YAML system field).
- **Rationale**: One method, one contract. Pipelines are decoupled from provider internals. The frozen DTO guarantees `usage` is always a plain dict (JSON-serializable for audit log) — never a raw SDK object.
- **Constraint for future phases**: Phase 1 pipelines must `await provider.complete(system, user)`. `usage` must be populated via `resp.usage.model_dump()` — never store SDK usage objects directly. Per-prompt `model` / `temperature` overrides are NOT supported by the current ABC signature; extend it when needed.

### [DECISION-014] API keys resolved via `os.environ.get()` only; `cli/main.py` owns `load_dotenv`
- **Source**: `docs/plans/llm_layer.md` — Phase 4 approach note + post-review fix (2026-05-14)
- **Decision**: Providers call `os.environ.get(key_name)` and raise `ConfigError` if absent. `load_dotenv` is called once in `cli/main.py` before any imports. `tests/test_llm/conftest.py` loads `.env` once for the test session.
- **Alternatives considered**: `load_dotenv` inside each provider `__init__` with a hardcoded `Path(__file__).parent.parent / ".env"` — rejected: breaks if provider code is installed as a wheel (path resolves to site-packages).
- **Rationale**: Library code must not assume where `.env` lives. Application entrypoints (CLI, test session) own environment bootstrap. Providers are portable.
- **Constraint for future phases**: Any new provider MUST NOT call `load_dotenv`. New CLI subcommands are added to `cli/main.py` which already calls `load_dotenv` at module level. Test files that need API keys must either use `monkeypatch.setenv` or rely on `tests/test_llm/conftest.py`.

### [DECISION-015] All three providers carry `model`, `synthesis_model`, `embedding_model` fields; `SYNTHESIS_TASKS` is shared constant in `llm/provider.py`
- **Source**: `docs/plans/llm_layer.md` — post-plan extension session (2026-05-14); review finding #5
- **Decision**: `ClaudeConfig`, `OllamaConfig`, and `OpenAICompatConfig` all have three model fields. `get_provider(task, config)` passes `task` to all three providers. Each provider selects `synthesis_model` if `task in SYNTHESIS_TASKS`, else `model`. `SYNTHESIS_TASKS = frozenset({"synthesis", "documentation"})` lives in `llm/provider.py`.
- **Alternatives considered**: Only Claude having per-task model selection (original plan); per-provider task routing config.
- **Rationale**: Single-provider operation: if all tasks are routed to one provider (e.g. full-Ollama mode), the provider must still serve synthesis tasks with a smarter model. Config-only switch — no code change needed.
- **Constraint for future phases**: New pipeline tasks added to the `Task` type alias must be evaluated for whether they belong in `SYNTHESIS_TASKS`. New providers must accept `task: Task` and apply the same selection logic. `_embedding_model` is stored but not yet routed — Phase 3 retrieval will wire it.

### [DECISION-016] `PROMPTS` dict and Jinja2 `Environment` are module-level singletons; `StrictUndefined` always
- **Source**: `docs/plans/llm_layer.md` — OQ-L3 resolution + Phase 2 steps; post-review fix #4 (2026-05-14)
- **Decision**: `PROMPTS: dict[str, Prompt]` loaded eagerly at `llm/prompt_loader.py` import time. `_JINJA_ENV = Environment(undefined=StrictUndefined)` is a module-level singleton (not reconstructed per `render()` call). `Prompt` has no `model` or `temperature` fields — those are deferred until `LLMProvider.complete()` accepts them.
- **Alternatives considered**: Lazy loading (rejected — OQ-L3 chose eager for fail-fast at startup); per-call `Environment()` construction (rejected — expensive on hot paths); `DebugUndefined` or default `Undefined` (rejected — silent rendering of missing vars sends garbled strings to the LLM).
- **Rationale**: Fail at startup if a prompt file is missing or malformed, not mid-pipeline. `StrictUndefined` makes missing template variables loud. Singleton `Environment` avoids repeated object construction on hot pipeline paths.
- **Constraint for future phases**: All AI prompts are YAML files in `prompts/` — never inline f-strings (hook enforced). Phase 1 pipelines call `PROMPTS["name"].render(**vars)` to get `(system_str, user_str)`, then pass those to `provider.complete()`. Per-prompt model/temperature override requires extending `LLMProvider.complete()` signature first.

### [DECISION-012] `PipelineContext.config` uses `TYPE_CHECKING` guard; CONFIG loaded lazily inside `run_pipeline`
- **Source**: `docs/plans/pipeline.md` — Phase 1 steps + OQ-P5 resolution
- **Decision**: `from core.config import Config` is gated under `if TYPE_CHECKING`. `CONFIG` singleton is imported inside `run_pipeline` body only when `context=None`. Tests pass `config=MagicMock()` via explicit `PipelineContext`.
- **Rationale**: `CONFIG` validates vault root at import time. Importing it at module scope in `core/pipeline.py` would make the pipeline unimportable on machines without the vault. Lazy import keeps the module importable in test environments.
- **Constraint for future phases**: This pattern (lazy CONFIG import inside function body, not module scope) applies to any module that is imported by tests but doesn't require real vault config at import time.

---

## Technical Debt

| ID | What | Why deferred | Owned by phase | Source |
|---|---|---|---|---|
| TD-001 | `core/pipeline.py` | _(delivered 2026-05-14)_ | Phase 0 ✅ | Out of Scope, `plans/storage_level.md` |
| TD-002 | `llm/prompt_loader.py` and `prompts/` (empty) | _(delivered 2026-05-14)_ | Phase 0 ✅ | Out of Scope, `plans/storage_level.md` |
| TD-003 | `vault/` (paths, frontmatter, reader, writer) | Outside storage scope | Phase 0 | Out of Scope, `plans/storage_level.md` |
| TD-004 | `embeddings` table + FTS5 virtual table | No consumer until retrieval layer exists | Phase 3 | Out of Scope, `plans/storage_level.md` |
| TD-005 | `corrections` enrichment with classifier-specific fields | Placeholder table exists; fields added when self-learning is built | Phase 7 | Out of Scope, `plans/storage_level.md` |
| TD-006 | Per-section AI vs human authorship tracking | Hard problem; `updated_by_human` blunt gate is sufficient now | Phase 7+ | Open Question Q-002, `plans/storage_level.md` |
| TD-007 | Daemon-mode WAL checkpoint (`wal_autocheckpoint`) | CLI exits cleanly; WAL truncates on close; irrelevant until MCP daemon | Phase 4 | Out of Scope + Open Question Q-003, `plans/storage_level.md` |
| TD-008 | `documents` columns: `project`, `status`, `key_topics` | Add via migrations when pipelines demand them; not pre-emptively | Phase 2+ | Out of Scope, `plans/storage_level.md` |
| TD-009 | `updated_by_human` sync between frontmatter and SQLite | SQLite mirror exists for cheap queries; sync logic is vault/writer.py concern | Phase 1 | research/storage_level.md edge cases |
| TD-010 | Ollama `httpx` async rewrite | `asyncio.to_thread(requests.post)` is sufficient for Phase 0; only worth revisiting if Ollama becomes performance-critical | Phase 3+ | Out of Scope, `plans/llm_layer.md` |
| TD-011 | Per-prompt `model` and `temperature` overrides | `Prompt` model has no `model`/`temperature` fields — removed as dead weight. Requires extending `LLMProvider.complete()` signature when needed | Phase 1+ | DECISION-016 + review finding #3 |
| TD-012 | `cli/main.py` commands: capture, classify, search, briefing | Placeholder stubs raise `NotImplementedError`; wired to pipelines as each phase delivers | Phase 1+ | `cli/main.py` created as dotenv owner (DECISION-014) |
| TD-013 | `_embedding_model` stored on all providers but not yet routed | Field exists for single-provider portability; Phase 3 retrieval wires it to `sentence-transformers` or provider embedding endpoint | Phase 3 | DECISION-015; Out of Scope, `plans/llm_layer.md` |

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
- **`CONFIG` validates vault root at import time.** Any code or test that imports `CONFIG` at module level fails if the vault path doesn't exist on disk. Tests must pass explicit paths (e.g. `db_path=tmp_path/...`) to bypass CONFIG, or lazy-import CONFIG inside functions. Do not import CONFIG at module scope in test files. _(Source: plans/storage_level.md Phase 6 Surprises S-001)_
- **`PRAGMA foreign_keys=ON` on every new connection.** The pragma is connection-scoped; forgetting it silently disables FK enforcement including `ON DELETE CASCADE` on `corrections`.
- **All schema changes via versioned `.sql` deltas.** No in-code `ALTER TABLE`. Migration runner applies in lexical order and records version in `schema_version`.
- **CLI commands wrap async pipelines with `asyncio.run()`.** Pattern: `@click.command() def capture(file): asyncio.run(_async_capture(file))`. No `click-anyio` or other async Click adapter. _(Source: plans/pipeline.md OQ-P4)_
- **`load_dotenv` is called exactly once, in `cli/main.py`, before any other imports.** Provider `__init__` methods call only `os.environ.get()`. Test files use `monkeypatch.setenv` for unit tests and `tests/test_llm/conftest.py` for integration tests. _(Source: DECISION-014)_
- **All three providers (Claude, Ollama, OpenAI-compat) must support `model`, `synthesis_model`, and `embedding_model` config fields.** New providers follow this pattern. `get_provider(task, config)` passes `task` to all providers. _(Source: DECISION-015)_
- **Pipelines never call provider `complete()` directly — always `get_provider(task, CONFIG.main).complete(system, user)`.** Factory in `llm/provider.py` is the single dispatch point. _(Source: DECISION-013)_

---

## Open Questions

| ID | Question | Blocks | Status |
|---|---|---|---|
| Q-001 | Move/rename detection: integer PK + `content_hash` vs frontmatter `doc_id`. If a note is edited AND moved simultaneously, content_hash–based detection fails. Is this sufficient for Phase 1, or does Phase 1 need to write `doc_id` to frontmatter? | Phase 1 vault indexer | 🔴 Open |
| Q-002 | Fine-grained AI vs human authorship per section. `updated_by_human` is whole-note only. If MCP tool needs to show "AI wrote this summary, you wrote this conclusion," a separate design (HTML comments or `edits` table) is required. | Phase 7+ | 🔴 Open |
| Q-003 | `wal_autocheckpoint` tuning. Reference sets to 100 pages; SQLite default is 1000. Worth adding to `_connect()` before Phase 4 MCP (long-running daemon), or accept default for CLI? | Phase 4 | 🔴 Open |
| Q-004 | Concurrent `run_pipeline` calls in Phase 4 MCP daemon — `clear_contextvars()` in `new_correlation_id()` will bleed across concurrent runs. Needs scoped contextvars or per-run copies. | Phase 4 | 🔴 Open |
