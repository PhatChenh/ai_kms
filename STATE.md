# STATE.md — Cross-Session Project State
_Created: 2026-05-09_
_Last updated: 2026-05-20 (Phase 0 complete; Phase 1 checklist created + handlers done)_

## Current Position
**Phase**: Phase 1 — Capture ✅ **Phase 0 complete as of 2026-05-20**

**Phase 0 Final Checklist** _(CLOSED)_:
- [x] core/exceptions.py
- [x] core/result.py
- [x] core/logging_setup.py
- [x] core/config.py
- [x] core/confidence.py
- [x] core/pipeline.py
- [x] core/audit.py
- [x] llm/ (all providers + prompt_loader.py)
- [x] prompts/ (scaffolding + test.yaml)
- [x] storage/schema.sql, migrations/, db.py, audit_log.py
- [x] vault/ (paths.py, frontmatter.py, reader.py, writer.py — complete 2026-05-20)
- [x] smoke test

**Phase 1 — Capture Checklist** _(In progress — targeting M1 ~15 May 2026)_:
- [x] handlers/base.py (BaseHandler ABC, registry pattern)
- [x] handlers/__init__.py (HandlerRegistry export + auto-discovery)
- [x] handlers/markdown_handler.py (extract summary + metadata from .md)
- [x] handlers/pdf_handler.py (PDF text extraction → summary)
- [x] handlers/docx_handler.py (DOCX text extraction → summary)
- [x] handlers/url_fetcher.py (fetch web content; integrated into pipeline stages)
- [ ] pipelines/capture.py (extract → summarize → extract_metadata → store; 4 pure stages)
- [ ] prompts/summarize.yaml (AI summarization prompt)
- [ ] prompts/extract_metadata.yaml (AI metadata extraction prompt)
- [ ] CLI: `kms capture <file>` (Click command + asyncio.run wrapper)
- [ ] Phase 1 integration test (end-to-end: .md drop → parsed → upsert → audit)
- [ ] audit_log wired: every capture writes decision + confidence + source note IDs

**Next planned work**: Wire handlers → capture pipeline → audit log. Then classify (Phase 2).

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

### [DECISION-017] Vietnamese filename normalization via Unicode NFC
- **Source**: `docs/plans/vault_layer.md` — Phase 4 (writer.py) OQ-V6 resolution
- **Decision**: Apply `unicodedata.normalize("NFC", ...)` to `vault_path` strings before storing in SQLite and before comparing paths from filesystem scans.
- **Rationale**: macOS stores filenames in NFD (decomposed form); Python may read them as NFD strings. Vietnamese filenames use tonal diacritics (ă, â, đ, ê, ô, ơ, ư + combining tone marks). Without NFC, the same filename can produce two different Python strings depending on how it was read, causing indexer to report spurious "deleted + added" for the same note.
- **Constraint for future phases**: `vault/writer.py` applies NFC in `_to_vault_path()` before returning `vault_path` in `WriteOutcome`. `vault/indexer.py` applies NFC in `scan_vault()` when computing `VaultEntry.vault_path`. This ensures storage and retrieval use the same normalized form.

### [DECISION-018] Indexer scans only .md files, not binary attachments
- **Source**: `docs/plans/vault_layer.md` — Phase 6 (indexer.py) design decision
- **Decision**: `scan_vault()` skips any file that is not `.md` (case-insensitive). Binary files in the vault (PDFs, images, etc.) are not indexed.
- **Rationale**: (1) The vault contains attachments alongside notes, but attachments are not knowledge artifacts — they have no frontmatter, no body to classify, no hash to track. (2) The `documents` table schema is designed for markdown: vault_path, content_hash, frontmatter fields. Indexing a binary file there is incoherent. (3) PDFs/emails/web articles enter the system as INPUT via handlers (e.g. PDFHandler), not as vault notes. They get captured, summarized, and written as .md. The original attachment is not indexed — its derived note is.
- **Constraint for future phases**: If a future phase needs to track attachments (e.g. for de-duplication), that would require a separate `attachments` table and a different indexer — not an extension of the markdown indexer. Attachment moves are handled separately by `vault/writer.move_attachment()`, which takes no `updated_by_human` gate and returns `Result[Path]` (not `WriteOutcome`).

### [DECISION-019] Tag taxonomy enforced in pipeline, not in NoteMetadata validators
- **Source**: `docs/plans/capture_pipeline.md` — Phases 5-7 (tag taxonomy enforcement, decided 2026-05-20)
- **Decision**: Tag validation (`validate_tags` in `core/tags.py`) runs in the pipeline's `metadata` stage, not in `NoteMetadata` Pydantic field validators. `NoteMetadata.type` and `NoteMetadata.domain` fields are kept as convenience fields, derived from `type/<name>` and `domain/<name>` tags respectively.
- **Alternatives considered**: (a) Pydantic field_validator on NoteMetadata.tags — rejected: NoteMetadata reads existing notes with old vocabulary; strict validator would break backward compat; (b) Remove type/domain fields entirely — rejected: user chose to keep both for Obsidian property queries.
- **Rationale**: NoteMetadata is used to read all existing vault notes (old and new vocabulary). Adding a strict validator breaks reads of notes captured before the taxonomy change. The pipeline is the right enforcement point — it validates AI output at creation time, not at read time.
- **Constraint for future phases**: All pipelines that generate tags (capture, classify, synthesis) MUST call `validate_tags` from `core/tags.py`. Tag violations are logged as `TAG_VIOLATION` audit entries (separate from `CAPTURED`). `NoteMetadata.type` is always derived from `type/<name>` tag by stripping prefix; `NoteMetadata.domain` is derived from the first `domain/<name>` tag.

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
| TD-014 | ~~`write_note` cannot explicitly clear a known field~~ | **RESOLVED 2026-05-20**: Removed Option B merge from `vault/writer.py`. `write_note` now writes exactly what the caller passes. Pipelines must `read_note` first if they want to preserve existing fields. Only `created` is preserved automatically as a factual timestamp invariant. See cross-phase constraint below. | ✅ Resolved | `plans/vault_layer.md` Phase 4 Option B note |
| TD-015 | `CLAUDE.md` co-authoring needs body section-merge, not just a watcher | `CLAUDE.md` (project/domain index, formerly `project_index.md` / `domain_index.md`) holds an AI-maintained index section AND a human-authored context section in one body. `write_note` replaces the *whole* body — no section merge — and `updated_by_human` is a whole-note gate (DECISION-002). So the Phase 9 watcher cannot just flip `updated_by_human`; it needs a section-aware body merge (e.g. `<!-- AI-INDEX -->...<!-- /AI-INDEX -->` delimiters) so AI index updates do not clobber human context edits. **Interim rule (Option A, decided 2026-05-18):** AI always writes `CLAUDE.md` with `actor="ai"`; `updated_by_human` stays `False` (do NOT set it `True` — that hard-blocks all AI writes); accept that human context edits to `CLAUDE.md` CAN be overwritten by AI index writes until the section-merge lands. The capture/classify pipelines never index `CLAUDE.md` (`indexer.IGNORE_FILES`). | Phase 9 (watcher / co-author) | `plans/vault_layer.md` OQ-V8; review session 2026-05-18 |
| TD-016 | User explicit URL flagging in `enrich_urls` | User marks URLs as crucial via frontmatter `fetch_urls: [url1, url2]` list or inline `#fetch` tag on a URL line. These bypass the structural gate and are always fetched regardless of body length or url count. Implementation: `enrich_urls` reads `fetch_urls` from `RawContent` frontmatter (requires extending `RawContent` or passing note metadata separately) and merges with gate-selected URLs. No changes to stages 3-5. Isolated in `_build_gate` extension point in `enrich_urls`. | Phase 1+ (post-watcher) | `docs/research/capture_pipeline.md` Wishlist A |
| TD-017 | AI URL triage replacing structural heuristic gate | Before fetching, LLM classifies each URL as `primary \| citation \| skip` using `prompts/url_triage.yaml`. Only `primary` URLs are fetched. Replaces `_should_enrich` structural heuristic with `_ai_triage_urls()` call inside `_build_gate`. Requires: new `prompts/url_triage.yaml`, `_ai_triage_urls(urls, body) → list[str]` helper, LLM call inside `enrich_urls` (adds latency). Gate isolation in `_build_gate` means stages 3-5 need no changes. | Phase 2+ | `docs/research/capture_pipeline.md` Wishlist B |
| TD-018 | Domain list refresh in `kms watch` | Taxonomy loaded once at watcher startup; new Domain/ folders added while watcher runs are invisible until restart. Acceptable for Phase 9; dynamic refresh deferred. | Post-Phase 9 | OQ-C6, plans/capture_pipeline.md |
| TD-019 | Tag taxonomy enforcement in classify pipeline | `core/tags.py` validate_tags is shared infrastructure; classify (roadmap Phase 2) must wire it in. Not in capture plan scope. | Roadmap Phase 2 | Out of Scope, plans/capture_pipeline.md |

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
- **`write_note` is a pure writer — pipeline owns the merge.** `write_note` writes exactly what the caller passes. To preserve any existing field (tags, project, summary, etc.), the calling pipeline MUST call `read_note` first and re-pass existing values explicitly. The only exception: `created` is always preserved as a factual timestamp invariant. A pipeline that calls `write_note` with `NoteMetadata()` (all defaults) will wipe all existing metadata. _(Source: TD-014 resolved 2026-05-20)_
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
| Q-005 | Phase 9 watcher design for `updated_by_human` locking strategy. DECISION-002 specifies whole-note lock: if `updated_by_human=True`, no AI writes allowed at all (including metadata-only edits). When Phase 9 watcher detects a human body edit and sets `updated_by_human=True`, should the AI still be allowed to update frontmatter (metadata-only writes) if the body is unchanged, or keep the whole-note lock? | Phase 9 (watcher) | 🔴 Open |
