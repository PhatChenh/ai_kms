# Constraints

<!-- One line per group — keep in sync with ## Constraint Index in CLAUDE.md -->
<!-- Groups: Write Safety, DB Integrity, LLM & Providers, Async & CLI, Architecture, Testing -->

## Write Safety

### C-01 · Vault is source of truth; documents table is index only
**Severity:** CRITICAL
**Domain:** Write Safety
**Rule:** `vault/writer.py` is the only code that writes to the vault; `documents` table stores index metadata, never note body or content cache.
**Why:** Bypassing the writer skips the `updated_by_human` gate and idempotency checks, silently corrupting human notes.
**Danger signal:** Any `.write_text()` or `open(..., 'w')` in a `.py` file other than `vault/writer.py`; any code reading note body from `documents` instead of calling `read_note`.
**Source:** DEBTS_CONSTRAINTS.md Cross-Phase Constraints §1; hook enforcement in `.claude/settings.json`
**⚠️ Rearchitecture (2026-06-12):** This constraint **inverts** under the cloud-native rearchitecture — DB becomes source of truth for AI content; vault becomes read-only user input; `vault/writer.py` retires. See `docs/0_draft/cloud_native_rearchitecture.md` §5/§12 and ADR-0012. **Still in force for current code** — the flip happens in the phase that rewrites the last vault-writing consumer (Phases 6/7), NOT in Phase 5 Slice 1. Do not flip until that code ships.

---

### C-02 · updated_by_human=1 means hands off
**Severity:** CRITICAL
**Domain:** Write Safety
**Rule:** `vault/writer.py` MUST check `updated_by_human` before every AI write; if true, skip the write or surface a conflict — no exceptions.
**Why:** Overwriting a human-edited note with an AI write silently destroys the human's work with no recovery path.
**Danger signal:** Any `write_note(...)` call path that does not check `updated_by_human` first; any write that proceeds when the value is `1`.
**Source:** DECISION-002; DEBTS_CONSTRAINTS.md Cross-Phase Constraints §2

---

### C-03 · write_note is a pure writer — pipeline owns the merge
**Severity:** CRITICAL
**Domain:** Write Safety
**Rule:** `write_note` writes exactly what the caller passes; to preserve any existing field the pipeline MUST call `read_note` first and re-pass existing values explicitly. Only `created` is automatically preserved.
**Why:** Calling `write_note` with default `NoteMetadata()` wipes all existing metadata on the note — tags, project, summary, all gone.
**Danger signal:** Any pipeline calling `write_note` with `NoteMetadata()` or partial metadata without a preceding `read_note`; any assumption that `write_note` will merge or preserve existing fields other than `created`.
**Source:** TD-014 (resolved 2026-05-20); DEBTS_CONSTRAINTS.md Cross-Phase Constraints §10
**⚠️ Rearchitecture (2026-06-12):** `write_note` (and all vault writes) **retire** under the cloud-native rearchitecture — AI output goes to DB, not the vault. See `cloud_native_rearchitecture.md` §12 and ADR-0012. **Still in force for current code**; retires in the phase that rewrites its last consumer (Phases 6/7), NOT Phase 5 Slice 1.

---

## DB Integrity

### C-04 · PRAGMA foreign_keys=ON on every new connection
**Severity:** CRITICAL
**Domain:** DB Integrity
**Rule:** `PRAGMA foreign_keys=ON` must be executed in every `_connect()` call; the pragma is connection-scoped and does not persist across connections.
**Why:** Without the pragma, FK enforcement including `ON DELETE CASCADE` on `corrections` is silently disabled, allowing orphaned rows.
**Danger signal:** Any new `_connect()` implementation or connection factory that omits `cursor.execute("PRAGMA foreign_keys=ON")`; any test helper that creates a raw `sqlite3.connect()` without the pragma.
**Source:** DECISION-008; DEBTS_CONSTRAINTS.md Cross-Phase Constraints §12

---

### C-05 · All schema changes via versioned .sql deltas
**Severity:** HIGH
**Domain:** DB Integrity
**Rule:** All schema changes land as new numbered `.sql` files in `storage/migrations/`; migration runner applies in lexical order; no in-code `ALTER TABLE`.
**Why:** In-code schema mutations bypass migration ordering, make rollback impossible, and cause schema drift between environments.
**Danger signal:** Any `ALTER TABLE`, `CREATE TABLE`, or `DROP TABLE` statement inside `.py` files; any schema change not represented by a new file in `storage/migrations/`.
**Source:** DECISION-007; DEBTS_CONSTRAINTS.md Cross-Phase Constraints §13

---

## LLM & Providers

### C-06 · Confidence thresholds live in config/thresholds.yaml, never in code
**Severity:** HIGH
**Domain:** LLM & Providers
**Rule:** Float literals in `if`/`elif` comparisons inside `pipelines/` are forbidden; all routing thresholds are read from `config/thresholds.yaml` via `ConfidenceGate.from_config(config)`.
**Why:** Hardcoded thresholds require code changes and redeployment to tune routing; config-driven thresholds allow tuning without touching pipelines.
**Danger signal:** Any float literal in an `if`/`elif` condition inside `pipelines/`; any routing decision not delegated to `ConfidenceGate`.
**Source:** DEBTS_CONSTRAINTS.md Cross-Phase Constraints §5; hook hard-block in `.claude/settings.json`

---

### C-07 · All AI prompts are YAML files in prompts/, never inline f-strings
**Severity:** HIGH
**Domain:** LLM & Providers
**Rule:** All prompts are loaded via `PROMPTS["name"].render(**vars)` from `prompts/`; f-strings containing prompt-like keywords in `.py` files trigger a hook warning.
**Why:** Inline prompt f-strings bypass version control on prompt content and make prompt tuning a code change rather than a config edit.
**Danger signal:** Any f-string in a `.py` file containing words like "summarize", "classify", "extract", or instruction-style phrasing; any `provider.complete()` call passing a string literal rather than a rendered YAML prompt.
**Source:** DECISION-016; DEBTS_CONSTRAINTS.md Cross-Phase Constraints §6; hook warning in `.claude/settings.json`

---

### C-08 · Pipelines use get_provider(task, CONFIG) factory — never call provider.complete() directly
**Severity:** HIGH
**Domain:** LLM & Providers
**Rule:** Pipelines call `get_provider(task, CONFIG.main).complete(system, user)`; the factory in `llm/provider.py` is the single dispatch point for all LLM calls.
**Why:** Direct provider instantiation bypasses task-based model routing (`model` vs `synthesis_model`) and breaks provider swap at config level.
**Danger signal:** Any `ClaudeProvider(...)`, `OllamaProvider(...)`, or `OpenAICompatProvider(...)` instantiation outside `llm/provider.py`; any `.complete()` call not reached via `get_provider`.
**Source:** DECISION-013; DEBTS_CONSTRAINTS.md Cross-Phase Constraints §17

---

### C-09 · All providers carry model, synthesis_model, and embedding_model config fields
**Severity:** MEDIUM
**Domain:** LLM & Providers
**Rule:** `ClaudeConfig`, `OllamaConfig`, and `OpenAICompatConfig` must each carry `model`, `synthesis_model`, and `embedding_model` fields; `get_provider` passes `task` to all providers.
**Why:** Single-provider operation requires per-task routing; missing fields force code changes when switching providers.
**Danger signal:** Any new provider config class missing one of the three model fields; `get_provider` routing logic that is provider-specific rather than task-driven.
**Source:** DECISION-015; DEBTS_CONSTRAINTS.md Cross-Phase Constraints §16

---

## Async & CLI

### C-10 · CLI commands wrap async pipelines with asyncio.run()
**Severity:** HIGH
**Domain:** Async & CLI
**Rule:** Every Click command that calls an async pipeline uses `asyncio.run(_async_fn())`; no `click-anyio` or other async Click adapter.
**Why:** Mixing async adapters causes event-loop nesting; `asyncio.run` is the explicit single-threaded entry contract.
**Danger signal:** Any `import click_anyio` or `@coro` adapter in `cli/main.py`; any Click command declared as `async def` without a sync `asyncio.run` wrapper.
**Source:** DECISION-010; DEBTS_CONSTRAINTS.md Cross-Phase Constraints §14

---

### C-11 · load_dotenv called exactly once, in cli/main.py, before any other imports
**Severity:** HIGH
**Domain:** Async & CLI
**Rule:** `load_dotenv()` lives at the top of `cli/main.py` only; providers call `os.environ.get()` and raise `ConfigError` if a key is absent.
**Why:** Calling `load_dotenv` inside library code breaks portability (wheel install paths differ from source paths); multiple calls silently overwrite env vars set by the caller.
**Danger signal:** Any `load_dotenv()` call inside `llm/`, `handlers/`, `pipelines/`, or `storage/`; any provider `__init__` that calls `load_dotenv`.
**Source:** DECISION-014; DEBTS_CONSTRAINTS.md Cross-Phase Constraints §15

---

## Architecture

### C-12 · Every public function in handlers/ and pipelines/ returns Success or Failure
**Severity:** HIGH
**Domain:** Architecture
**Rule:** Every public function in `handlers/` and `pipelines/` must return `Success(value)` or `Failure(error, recoverable, context)`; raw values and `None` returns are forbidden at module boundaries.
**Why:** Silent failures propagate invisibly; callers cannot distinguish success from error without a typed Result.
**Danger signal:** Any `def` in `handlers/` or `pipelines/` with return type `None`, `str`, `dict`, or any non-`Result` type; any function that can return without wrapping in `Success(...)`.
**Source:** DEBTS_CONSTRAINTS.md Cross-Phase Constraints §4

---

### C-13 · Audit log is non-negotiable from Phase 1
**Severity:** HIGH
**Domain:** Architecture
**Rule:** Every pipeline stage that makes an AI decision MUST call `core.audit.write(...)`; never `storage.audit_log.append()` directly; `source_ids`, `pipeline`, and `stage` must be populated.
**Why:** Phase 8 Daily Briefing reads `audit_log` as its primary input; missing entries create silent gaps in the digest.
**Danger signal:** Any `provider.complete()` call not followed by `audit.write(...)`; any direct import of `storage.audit_log` in pipeline code; any `audit.write()` call missing `source_ids`, `pipeline`, or `stage`.
**Source:** DECISION-003; DECISION-004; DEBTS_CONSTRAINTS.md Cross-Phase Constraints §3

---

### C-14 · mcp_server/tools.py is logic-free
**Severity:** HIGH
**Domain:** Architecture
**Rule:** `mcp_server/tools.py` contains no `if`, `elif`, `for`, or `while` at statement level; tools call a pipeline and return its result.
**Why:** Logic in the MCP layer bypasses the pipeline's audit, confidence gating, and Result wrapping — producing untraceable decisions.
**Danger signal:** Any `if`, `elif`, `for`, or `while` statement at non-nested level in `mcp_server/tools.py`; any inline calculation or branching that belongs in a pipeline stage.
**Source:** DEBTS_CONSTRAINTS.md Cross-Phase Constraints §7; hook hard-block in `.claude/settings.json`

---

### C-15 · Never add an MCP tool before its pipeline exists and is tested
**Severity:** HIGH
**Domain:** Architecture
**Rule:** An MCP tool may only be added after its backing pipeline is implemented and has passing tests; stub tools that call nothing are forbidden.
**Why:** A stub tool that calls nothing misleads the demo and creates false confidence that the feature works.
**Danger signal:** Any `@tool` definition in `mcp_server/` whose body is `pass`, `return None`, or a hardcoded string; any tool added before the corresponding pipeline has tests.
**Source:** DEBTS_CONSTRAINTS.md Cross-Phase Constraints §8; CLAUDE.md critical rules

---

### C-16 · Schedulers come last in each phase
**Severity:** MEDIUM
**Domain:** Architecture
**Rule:** Within any phase, build and verify the manual CLI command first; only add scheduler/cron automation after the CLI is verified.
**Why:** Automating an untested pipeline multiplies failure surface; manual CLI smoke-tests the logic before handing to the scheduler.
**Danger signal:** Any `scheduler/jobs.yaml` entry for a pipeline with no corresponding `kms <command>` CLI test; any cron job added in the same commit as the pipeline it calls.
**Source:** DEBTS_CONSTRAINTS.md Cross-Phase Constraints §9

---

## Testing

### C-17 · Never import CONFIG at module scope in tests
**Severity:** MEDIUM
**Domain:** Testing
**Rule:** Tests must not import `CONFIG` at module level; pass explicit paths (e.g. `db_path=tmp_path / "kb.db"`) or lazy-import CONFIG inside the function under test.
**Why:** `CONFIG` validates vault root at import time; module-level import fails at pytest collection on machines without the vault, breaking CI.
**Danger signal:** Any `from core.config import CONFIG` or `import core.config` at the top of a test file outside an `if TYPE_CHECKING` block; any test fixture that depends on CONFIG being importable without a real vault on disk.
**Source:** DECISION-012; DEBTS_CONSTRAINTS.md Cross-Phase Constraints §11
