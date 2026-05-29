# STATE.md — Cross-Session Project State
_Created: 2026-05-09_
_Last updated: 2026-05-24 (Brief #4 — post-review bugfix pass: sibling naming, reconcile guards, watcher refactor)_

## Current Position
**Phase**: Phase 1 — Capture ✅ **Complete as of 2026-05-21**

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

**Phase 1 — Capture Checklist** _(CLOSED — complete 2026-05-21)_:
- [x] handlers/base.py (BaseHandler ABC, registry pattern)
- [x] handlers/__init__.py (HandlerRegistry export + auto-discovery)
- [x] handlers/markdown_handler.py (extract summary + metadata from .md)
- [x] handlers/pdf_handler.py (PDF text extraction → summary)
- [x] handlers/docx_handler.py (DOCX text extraction → summary)
- [x] handlers/url_fetcher.py (fetch web content; integrated into pipeline stages)
- [x] pipelines/capture.py (5-stage pipeline: extract → enrich_urls → summarize → metadata → store)
- [x] prompts/summarize.yaml + prompts/extract_metadata.yaml
- [x] core/tags.py + config/tags.yaml (tag taxonomy + validate_tags)
- [x] CLI: `kms capture <file>` + `kms capture --scan` + `kms watch`
- [x] vault/watcher.py (VaultWatcher + debounce via threading.Timer)
- [x] vault/indexer.py scan_non_md_drops + scan_capture modified/deleted/moved loops
- [x] 487 tests pass (all capture pipeline phases verified)
- [x] audit_log wired: every capture writes CAPTURED + TAG_VIOLATION entries

**Phase 1.5 — Revise Attachment Layout Checklist** _(PENDING; not in roadmap — design-change rework)_:
- [x] `core/config.py` — added `summaries_subdir: str = ".summaries"` Field; removed `attachment_path` @property; temporary callers in `capture.py:456`, `capture.py:627`, `cli/main.py:127` use `.root / .attachment_dir` with `# COUPLING:` comments marking Brief #2/#3 work. 576 tests pass.
- [x] `vault/paths.py` — added `project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries` helpers reading `attachment_dir` + `summaries_subdir` from VaultConfig. No hardcoded subdir names. 8 new tests; 594 tests pass.
- [x] `vault/indexer.py` — added `_DOT_ALLOWLIST: frozenset[str] = frozenset({".summaries"})`; updated both `dirnames[:] = [...]` prune expressions in `scan_non_md_drops` + `scan_vault` with scoped condition `(not d.startswith(".") or (d in _DOT_ALLOWLIST and dirpath.name == "attachment"))`. 99 vault tests pass, no regressions.
- [x] `vault/frontmatter.py` — added `"attachment_path"` to `_KNOWN_KEYS`; added `attachment_path: str | None = None` field to `NoteMetadata` after `source_file`. 15 frontmatter tests pass.
- [x] 4 architecture decisions recorded (DECISION-021 through -024); 5 TD items recorded (TD-020 through TD-024).
- [ ] Claude CLI provider: metadata JSON parse fails on short DOCX extracts (~29 chars). Prompt hardening or empty-metadata fallback needed. (**TD-028**)
- [ ] Rename gate logic mis-calibrated (too liberal / too conservative). Needs research on competitor approaches + confidence-scored suggestion model. (**TD-029**)

Other in-flight notes:
- Handlers extension: XLSX done; others pending sibling approach finalization.
- Sibling md file handling: DONE.

<!-- Original Claude CLI provider error log (kept for TD-028 reproduction):
    tested with real vault and file, receive this kms capture /Users/lap14806/ai_kms_test_vault/attachment/finance.docx
    2026-05-22T10:07:00.590605Z [warning  ] para_context_path set but not found: /Users/phatchenh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Claude Brain/para-context.yaml — classify pipeline will skip PARA context. [core.config]
    2026-05-22T10:07:00.617190Z [info     ] docx.extract.ok                [handlers.docx_handler] bytes=24045 chars=29 correlation_id=3b1f2067-3b2f-4edf-a190-1e745c566e7e path=/Users/lap14806/ai_kms_test_vault/attachment/finance.docx
    2026-05-22T10:07:22.601077Z [error    ] stage_failed                   [core.pipeline] context={'content_preview': 'Need full note content. Headings alone ("Q1 performance", "Q2 Performance") insufficient for metadata extraction.\n\nProvide:\n- Body text / data / findings\n- Context (meeting? report? personal reflectio'} correlation_id=3b1f2067-3b2f-4edf-a190-1e745c566e7e error='metadata JSON parse error: Expecting value: line 1 column 1 (char 0)' pipeline=capture recoverable=False stage=metadata traceback='Traceback (most recent call last):\n  File "/Users/lap14806/Library/CloudStorage/OneDrive-VNGGroupJSC/Documents/Zalopay 2026/01. Improve productivity/ai_kms/pipelines/capture.py", line 100, in _parse_metadata_json\n    parsed = json.loads(cleaned)\n             ^^^^^^^^^^^^^^^^^^^\n  File "/Users/lap14806/.local/share/uv/python/cpython-3.12.11-macos-aarch64-none/lib/python3.12/json/__init__.py", line 346, in loads\n    return _default_decoder.decode(s)\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/Users/lap14806/.local/share/uv/python/cpython-3.12.11-macos-aarch64-none/lib/python3.12/json/decoder.py", line 338, in decode\n    obj, end = self.raw_decode(s, idx=_w(s, 0).end())\n               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/Users/lap14806/.local/share/uv/python/cpython-3.12.11-macos-aarch64-none/lib/python3.12/json/decoder.py", line 356, in raw_decode\n    raise JSONDecodeError("Expecting value", s, err.value) from None\njson.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)\n'
    FAILED: metadata JSON parse error: Expecting value: line 1 column 1 (char 0)
-->



**Brief #2 — attachment_capture_pipeline** _(complete — 2026-05-24)_:
- [x] Phase 1 (Taxonomy): `attachment-summary` added to `config/tags.yaml`; count tests updated to 9
- [x] Phase 2 (Prompt): `prompts/summarize_attachment.yaml` created — 3-section system prompt; variables: file_type, short_summary, text
- [x] Phase 3 (Rewrite `_store_nonmd()`): per-project paths, inline destination resolution, sibling-first write, CLUELESS inbox handling _(complete 2026-05-24)_
- [x] Phase 4 (Fix `scan_non_md_drops` + `scan_capture`): rule-based skip, extended `.summaries/` allowlist for inbox/ _(complete 2026-05-24)_

**Brief #3 — attachment_sync_and_archive** _(complete — 2026-05-24)_:
- [x] Phase 1 (Prerequisite Fixes): TD-023 watcher VaultConfig, TD-AS-1 .summaries/ skip, false-success logging — complete
- [x] Phase 2 (Archive Layout Helpers): domain_archive(paths.py), archive_path @property removed (config.py) — complete
- [x] Phase 3 (Watcher Sync Callbacks): _is_binary, _sibling_for helpers; on_delete → SIBLING_ORPHANED; on_move → ATTACHMENT_MOVED (same folder) or SIBLING_ORPHANED (different folder) — complete
- [x] Phase 4 (kms reconcile): 4-stage reconcile command — reconcile_paths, reconcile_orphan_binaries, reconcile_stale_binaries, reconcile_orphan_siblings; ReconcileResult dataclass; CLI wired; TD-026 retired

**Brief #4 — Review Fixes (post Phase 1.5+Briefs #1/#2/#3 review)** _(2026-05-24)_:
Triggered by `/superpowers:requesting-code-review`. Applied subset of review findings.
- [x] Sibling marker naming convention changed to `<binary.name>.md` (e.g. `report.pdf.md`) to prevent collisions between `report.pdf` and `report.docx` — see DECISION-028. Touched: `vault/watcher.py::_sibling_for`, `vault/indexer.py::_has_inbox_sibling`, `pipelines/capture.py` (3 sites: LOCATED sibling, CLUELESS marker, early-exit guard), `pipelines/reconcile.py` (Stages 2+3 sibling lookups), + tests.
- [x] Added `_is_managed_summaries_area(path, vault_cfg)` in `vault/paths.py`. Returns True when path lives under any `attachment/` subtree OR under `inbox/`. Used by reconcile Stage 4 to scope `.summaries/` `rglob`. Distinct from `_is_in_managed_attachment` (kept) which is the binary-pipeline area predicate.
- [x] Reconcile Stage 4 dual guards (DECISION-029): scope guard (`_is_managed_summaries_area`) + type guard (`note.metadata.type == "attachment-summary"`). Prevents accidental deletion of user-placed `.md` inside `.summaries/`.
- [x] `vault/watcher.py` refactor: hoisted lazy imports (logging, unicodedata, audit_write, AIDecision, Failure, Success, delete_by_path, rename_doc, read_note, move_note, write_note) to module top; moved `TYPE_CHECKING` block above `_sibling_for` definition.
- [x] `pipelines/reconcile.py`: top-level `from pipelines.capture import capture_file` (replaces inline lazy import). Test monkeypatch target updated from `pipelines.capture.capture_file` → `pipelines.reconcile.capture_file`. Reordered `__all__` so entry point `reconcile` is last (composition order).
- [x] CLUELESS marker body: replaced empty string with single-line placeholder (`_Pending classification — binary at: <path>_` + handoff note) so markers are FTS-searchable and self-explanatory in Obsidian preview.
- [x] STATE.md label fix: Brief #2 header `in progress` → `complete`. "Re-make work" prose collapsed into TD-028 + TD-029 with full error log preserved in HTML comment.
- [x] Tests updated: `test_watcher.py` monkeypatch targets retargeted to `vault.watcher.<name>` (top-level imports broke source-module patching — same gotcha as Q13); `_sibling_for` tests now assert `<filename>.md`; 1 new test for stem-collision distinctness. Phase-3, phase-9, phase-12, phase-rename, reconcile, indexer tests updated to new sibling pattern. 650 tests pass.

- [x] **Critical #1** (TD-030) resolved 2026-05-24: `on_deleted` reorder — binary sync now runs before `_should_skip`. Regression test added.

**Not applied (deferred to user decision)**:
- Issue #8 — `move_attachment` TOCTOU window (existence check then `os.replace`). Tracked as **TD-031**.
- Issue #9 — `kms migrate-attachments` for legacy `Vault/attachment/`+`Vault/Archive/` layout. Deferred greenfield — no production users. Tracked as **TD-032**.

**Next planned work**: Phase 2 — Classify pipeline.

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

### [DECISION-020] Pydantic v2 `model_validator(mode="after")` side-effects must live in `load_config()`, not in nested model validators
- **Source**: `core/config.py` — bug fix 2026-05-22; Pydantic v2 re-runs nested `model_validator(mode="after")` validators when a nested model instance is passed to a parent model constructor.
- **Decision**: Logging side-effects (warnings, info) that should fire exactly once at config load MUST be placed in `load_config()` after the fully-built `Config` object is available — not inside `MainConfig` or other nested model validators.
- **Alternatives considered**: (a) `model_config = ConfigDict(revalidate_instances='never')` on outer model — tested, does not suppress re-validation of nested validators; (b) class-level `_warned` flag on `MainConfig` — invasive, breaks immutability expectations.
- **Rationale**: Pydantic v2 re-runs `after` validators on a nested model instance whenever that instance is passed to the parent model's constructor. A validator in `MainConfig` thus fires once during `MainConfig(**raw)` and again during `Config(main=main_instance, ...)` — producing duplicate log output. `load_config()` runs exactly once per singleton load; it is the correct location for once-only side-effects.
- **Constraint for future phases**: Any new `model_validator(mode="after")` on `MainConfig` or other nested config models MUST NOT produce logging side-effects. All startup warnings and info logs that depend on the fully-validated config belong in `load_config()`, after `Config(...)` construction.

### [DECISION-021] Per-project/Domain attachment layout replaces single global `Vault/attachment/`
- **Source**: `docs/plans/revise_attachment_layout.md` — header decisions + Phases 1–4 (complete 2026-05-23)
- **Decision**: Each `Projects/<A>/` and `Domain/<D>/` has its own `attachment/` subfolder. The global `Vault/attachment/` folder is removed. Non-md binaries live at `Projects/<A>/attachment/<file>`. Sibling `.md` summaries live at `Projects/<A>/attachment/.summaries/<file>.md` (dot-prefix hides folder from Obsidian and the user). Domain follows the same pattern.
- **Alternatives considered**: Global `Vault/attachment/` — rejected: boss expects all project files in one place. Sibling next to source (old Phase 1 design) — rejected: floods vault with near-empty notes.
- **Rationale**: Boss's navigation model. All project-related files (notes, binaries, summaries) sit under one project folder. `.summaries/` dot-prefix keeps them invisible unless explicitly sought.
- **Constraint for future phases**: `pipelines/capture.py` MUST use `vault/paths.py::project_attachment(name)` / `project_summaries(name)` to compute target paths — never hardcode `"attachment"` or `".summaries"` in pipeline code. `vault/watcher.py` must generalize its per-project attachment-skip logic (Brief #3 scope — TD-023). `scan_non_md_drops` single `attachment_path` skip must generalize (Brief #2/#3 — TD-024).

### [DECISION-022] `documents.vault_path` for attachment siblings = sibling `.md` path; `NoteMetadata.attachment_path` points to binary
- **Source**: `docs/plans/revise_attachment_layout.md` — OQ-AL1 resolved (Option C / hybrid)
- **Decision**: `documents.vault_path` for a sibling row = `"Projects/<A>/attachment/.summaries/report.md"` (the indexed `.md` file). `NoteMetadata.attachment_path: str | None` frontmatter field carries the vault-relative path to the binary (`"Projects/<A>/attachment/report.pdf"`). Both pieces are maintained by the capture pipeline (Brief #2).
- **Alternatives considered**: (A) sibling-only (no pointer) — search hit opens summary; attachment rename breaks link silently; (B) attachment-only — requires weakening DECISION-018 and path rewrite in indexer; highest cost; (C) hybrid — chosen. Survives binary rename if sync updates frontmatter (Brief #3).
- **Rationale**: Option B conflicts with DECISION-018 (indexer indexes `.md` only; `vault_path` pointing at `.pdf` is incoherent). Option A is weaker — no structured way for tooling to find the binary. Option C is clean, schema-unchanged, and lets Phase 4 MCP follow the pointer.
- **Constraint for future phases**: Every search hit in Phase 3/4 resolves `documents.vault_path` to the sibling `.md`. To open the actual binary, consumers read `metadata.attachment_path` from the sibling's frontmatter. Phase 3 embeddings are computed from sibling body (coherent — body contains the AI-generated summary of the binary). Brief #3 sync must update `attachment_path` in frontmatter when the binary is renamed/moved.

### [DECISION-023] `VaultConfig`: `attachment_path` property removed; `summaries_subdir: str = ".summaries"` Field added
- **Source**: `docs/plans/revise_attachment_layout.md` — Phase 1 (OQ-AL2 resolved: Option-VC-A — remove the property)
- **Decision**: `VaultConfig.attachment_path` @property deleted. `attachment_dir: str = "attachment"` kept (used by path helpers). New `summaries_subdir: str = ".summaries"` Field added. Callers in `capture.py:456`, `capture.py:627`, `cli/main.py:127` temporarily use `.root / .attachment_dir` with `# COUPLING:` comments until Brief #2/#3 fixes them properly.
- **Alternatives considered**: (VC-B) repurpose as low-confidence staging area — depends on Brief #2 OQ-AC3 resolution, deferred; (VC-C) deprecated alias — creates confusion about which global folder still exists.
- **Rationale**: Global `Vault/attachment/` no longer exists. Keeping the property implies a global folder that is being deleted. Clean removal forces Brief #2 to use the per-project helpers.
- **Constraint for future phases**: `CONFIG.main.vault.attachment_path` no longer exists. Brief #2 must replace all 3 `# COUPLING:` callers with `project_attachment(name)` / `domain_attachment(name)` calls. Test `tests/test_core/test_config.py:353-355` (asserted `vault.attachment_path`) was deleted when the property was removed.

### [DECISION-025] Sibling-first write ordering in `_store_nonmd()` — binary move is step 2, never step 1
- **Source**: `docs/plans/attachment_capture_pipeline.md` — Phase 3 diagram + OQ-AC6 decision
- **Decision**: In the LOCATED path, the sibling `.md` is written **before** the binary is moved. Move only happens if `needs_move=True`. If move fails after sibling write, the broken `attachment_path` pointer is the accepted failure mode.
- **Alternatives considered**: Move-first (write sibling after move) — rejected: if sibling write fails after move, binary is displaced with no index record; harder to reconcile.
- **Rationale**: A sibling with a broken pointer is detectable and reconcilable (Brief #3 orphan pass). A moved binary with no sibling is invisible to search and harder to recover. Sibling-first guarantees the index always has a record even if the move step fails.
- **Constraint for future phases**: Brief #3 reconciliation pass must handle the case where `attachment_path` in sibling frontmatter points to a file that no longer exists (binary move failed or binary was manually deleted). TD-026 tracks this.

### [DECISION-026] Destination resolution in `_store_nonmd()` is inline path math — no AI call, no new pipeline stage
- **Source**: `docs/plans/attachment_capture_pipeline.md` — Approach section + Phase 3 Step 1
- **Decision**: Whether a binary is LOCATED or CLUELESS is determined by pure path inspection at the top of `_store_nonmd()`. No AI triage. No new stage. Projects/<A>/ or Domain/<D>/ → LOCATED; everything else → CLUELESS.
- **Alternatives considered**: AI-assisted routing (infer project from content/filename) — deferred to Phase 2 Classify. A new pipeline stage — rejected: stage count stays at 5; routing is not an extractable concern here.
- **Rationale**: Path context is deterministic when available; adding an AI call adds latency and cost for a decision that is already made structurally. Phase 2 Classify owns the harder case (inbox drops with no path context).
- **Constraint for future phases**: Phase 2 Classify resolves CLUELESS markers — it must NOT re-invoke `_store_nonmd()`. It reads the pending-routing sibling, classifies to project/domain, calls `project_attachment()` / `domain_attachment()` helpers, writes the full sibling, and clears `status=pending-routing`. No changes to `_store_nonmd()` at that point.

### [DECISION-027] CLUELESS binaries → inbox pending-routing markers; Phase 2 Classify resolves them
- **Source**: `docs/plans/attachment_capture_pipeline.md` — Diagram 4 (CLUELESS path)
- **Decision**: When `_store_nonmd()` cannot determine a project/domain from the source path, it writes a minimal sibling at `inbox/.summaries/<stem>.md` with `status=pending-routing`, `type=attachment-summary`, `attachment_path` → binary location. Binary stays in inbox (or is moved there from a stray location). The capture pipeline does NOT re-process these files on subsequent scans (early-exit guard checks for `status=pending-routing`).
- **Alternatives considered**: Block capture and surface to user for manual routing — rejected: non-technical user should not have to route files. Classify inline in capture — rejected: Phase 2 Classify is the dedicated routing pipeline; bundling it here violates stage separation.
- **Rationale**: Capture remains a thin ingest pipeline. CLUELESS files are safely parked with enough metadata (summary, type, attachment_path) for Phase 2 to act on. The pending-routing status field is the handoff contract between capture and classify.
- **Constraint for future phases**: Phase 2 Classify MUST check `status=pending-routing` on `.md` files in `inbox/.summaries/` as its input scan. The `scan_non_md_drops` Rule 2 skip (`_has_inbox_sibling`) prevents capture from re-triggering on already-parked CLUELESS binaries. Any pipeline that writes to `inbox/.summaries/` must set `status=pending-routing` (or clear it — Classify's job). _(See also DECISION-025 for sibling-first ordering.)_

### [DECISION-028] Sibling marker filename uses `<binary.name>.md` (full filename incl. extension), not `<binary.stem>.md`
- **Source**: Code review 2026-05-24 (issue #4 + #5)
- **Decision**: Capture pipeline writes sibling at `<parent>/<summaries_subdir>/<binary.name>.md` — e.g. `report.pdf` → `.summaries/report.pdf.md`. Replaces earlier `<stem>.md` pattern from Brief #2.
- **Alternatives considered**: `<stem>-<ext>.md` (e.g. `report-pdf.md`) — ugly. `<stem>-<hash6>.md` — unique but unreadable. `<stem>.md` (original) — broken when two binaries share stem.
- **Rationale**: With `<stem>.md`, dropping both `report.pdf` and `report.docx` in inbox produces a single sibling `report.md` whose `attachment_path` gets clobbered by the second binary, losing the first binary's classify handoff. `<binary.name>.md` is bijective with the binary, FTS-indexable, and trivially round-trips via `Path.with_suffix("")`.
- **Constraint for future phases**: All sibling lookups MUST use `<binary.name>.md`. Phase 2 Classify resolves markers by reading `attachment_path` from frontmatter, not by stem-matching sibling filename. Any new helper that maps binary → sibling must call `_sibling_for(binary, vault_config)` from `vault/watcher.py` (do not duplicate the path math).

### [DECISION-029] Reconcile Stage 4 unlinks only with two guards: managed-summaries scope + `attachment-summary` type
- **Source**: Code review 2026-05-24 (issues #2 + #3)
- **Decision**: `reconcile_orphan_siblings` (Stage 4) filters `summaries_dir` via `_is_managed_summaries_area(summaries_dir, vault_cfg)` AND filters individual sibling entries via `note.metadata.type == "attachment-summary"` before considering unlink. Both guards must pass.
- **Alternatives considered**: Scope guard only (still unlinks user-placed `.md` if scope happens to match). Type guard only (unscoped `rglob` finds stray `.summaries/` folders anywhere in vault). Trust the layout (unsafe; data-loss potential is autonomous).
- **Rationale**: Reconcile runs unattended (manual `kms reconcile` or scheduler). Stage 4 deletes by design. Defense in depth: scope rules out non-managed `.summaries/` directories (`Projects/<A>/.summaries/` without an `attachment/` parent, stray user folders); type rules out user-placed siblings of a different kind within managed dirs.
- **Constraint for future phases**: Any new pipeline that writes to `.summaries/` MUST set `type=attachment-summary` in frontmatter, otherwise reconcile Stage 4 will leave its output alone. Phase 2 Classify, when it resolves CLUELESS markers, must preserve the `type` field. New stages that create different sibling kinds inside `.summaries/` require either (a) their own type string + Stage 4 exclusion clause or (b) a separate parallel folder.

### [DECISION-024] Indexer `.summaries/` dotfolder allowlist is scoped — parent folder must be named `"attachment"`
- **Source**: `docs/plans/revise_attachment_layout.md` — Phase 3 (OQ-AL4 resolved: scoped allowlist)
- **Decision**: `_DOT_ALLOWLIST = frozenset({".summaries"})` in `vault/indexer.py`. Prune condition: `(not d.startswith(".") or (d in _DOT_ALLOWLIST and dirpath.name == "attachment"))`. A `.summaries/` folder is only traversed when its immediate parent is named `"attachment"`. All other dotfolders remain skipped.
- **Alternatives considered**: Global allowlist (traverse any `.summaries/` anywhere in vault) — simpler, but any user-created `.summaries/` in `inbox/` or elsewhere would be unexpectedly indexed.
- **Rationale**: Prevents accidental indexing. The `.summaries/` convention is a per-attachment-folder internal structure; it has no meaning outside an `attachment/` subtree.
- **Constraint for future phases**: If a new hidden-but-indexable convention emerges (e.g. `.archive-index/`), add it to `_DOT_ALLOWLIST` and add the appropriate parent-folder guard in the same condition. Do not loosen the global dotfolder skip without an explicit scoping guard.

---