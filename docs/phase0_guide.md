---
created: 2026-05-04
updated: 2026-05-06
---
# Phase 0 — Detailed Implementation Guide (Revised)

The goal of Phase 0 is to build the plumbing every later pipeline depends on. By the end of this
phase you will have: a Result type, a structured logger, a config layer, a confidence router, a
SQLite database with audit log, a prompt loader, a thin LLM provider wrapper that can make one
working API call, a vault read/write layer that respects `updated_by_human`, a vault indexer, and a
smoke test that exercises all of it end-to-end.

> **If `config.yaml` or `llm/` already exist from earlier work**, audit what is actually
> wired end-to-end vs. just scaffolded. Every file that exists only as a stub adds to your
> debt, not your progress. Phase 0 is done when the smoke test passes — not when the files exist.

---

## What Phase 0 does and does not do

| Does                                                        | Does not                                      |
| ----------------------------------------------------------- | --------------------------------------------- |
| Establishes all shared primitives every later phase imports | Classify or move notes                        |
| Makes one verified LLM call end-to-end                      | Call LLMs from pipelines                      |
| Loads and validates prompts at startup                      | Implement handler logic                       |
| Writes and reads vault notes with safety rules              | Run the briefing or scheduler                 |
| Initializes SQLite with migrations + audit log              | Index the entire vault (just the scaffolding) |
| Proves the whole stack cooperates (smoke test)              | Anything that requires Phase 1                |

---

## Pre-flight setup

Before touching `core/`, get the project skeleton right.

1. **Pin a dependency manager.** You already have `pyproject.toml` and `uv`. Stay with `uv`. Don't
   mix.
   - Resource: [astral.sh/uv docs → "Getting started"](https://docs.astral.sh/uv/) — 10 minutes.
2. **Create a virtualenv** isolated for this project. Add `.venv/` to `.gitignore`.
3. **Install Phase 0 dependencies:**
   - `pyyaml` — YAML loading
   - `python-frontmatter` — markdown + YAML frontmatter parsing
   - `pydantic` (v2) — config validation and prompt models
   - `pydantic-settings` — env-var overrides for secrets
   - `structlog` — structured logging
   - `jinja2` — prompt templating
   - `anthropic` — Claude API client (for `llm/claude_provider.py`)
   - `pytest` and `pytest-asyncio` — testing
   - SQLite ships with Python; no driver needed.
4. **Add `data/`, `logs/`, `.venv/`, `__pycache__/`, `*.db` to `.gitignore`.**
5. **Scaffold empty test directories** matching the source layout:
   `tests/test_core/`, `tests/test_vault/`, `tests/test_storage/`, `tests/test_handlers/`,
   `tests/test_pipelines/`, `tests/fixtures/`.

If you've never set up a Python package with `pyproject.toml` before, read the
[Python Packaging User Guide → "Packaging Python Projects"](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
once. You need editable installs (`uv pip install -e .`) so imports like
`from core.result import ...` work from anywhere in the project.

---

## Build order

Build in this order. Each item depends on the ones above. Do not skip ahead.

### 1. `core/exceptions.py`

**Goal.** One file defining the small exception hierarchy this codebase uses. Prevents every other
module from inventing its own `Exception` subclasses.

**Build.**
- One base class: `KMSError`.
- Empty subclasses: `ConfigError`, `VaultError`, `StorageError`, `LLMError`, `HandlerError`,
  `PipelineError`.
- Don't add fields yet. Enrich as real failure modes show up.

**Done when.** `from core.exceptions import KMSError` works from anywhere in the project.

**New knowledge.** None if you've subclassed `Exception` before. Otherwise:
[Real Python — "Python Exceptions: An Introduction"](https://realpython.com/python-exceptions/),
section on subclassing.

---

### 2. `core/result.py` — Pattern: Result Type

**Goal.** Replace `try/except` as the primary signal of failure between pipeline stages. Every
stage returns `Success(value)` or `Failure(error, recoverable, context)`. Callers cannot ignore
failures — the type forces them to handle both branches.

**Build.**
- A `@dataclass` `Success` generic over a type variable `T`.
- A `@dataclass` `Failure` with fields: `error: str`, `recoverable: bool`, `context: dict`. Add
  `traceback: str | None` (capture at construction time using `traceback.format_exc()`).
- A type alias `Result[T] = Success[T] | Failure`.
- Optional sugar: `is_success()`, `is_failure()`, `.unwrap()` (raises `KMSError(failure.error)`
  if called on a `Failure` — so callers can opt out of the discipline in scripts but keep it in
  production code).

**Decisions.**
- Whether to use `match/case` or `if isinstance` for branching at call sites. Either is fine —
  pick one and be consistent throughout the codebase.

**New knowledge.**
- **Generic typing in Python.** `TypeVar`, `Generic[T]`. Resource:
  [Real Python — "Python Type Checking"](https://realpython.com/python-type-checking/), generics
  section. Plus [PEP 484](https://peps.python.org/pep-0484/) for reference.
- **Why Result types over exceptions.** Scott Wlaschin's "Railway-Oriented Programming" talk on
  YouTube — it's F# but the idea translates 1:1. ~30 minutes. Watch it before writing this file.
- **Structural pattern matching.** Resource: [PEP 636 tutorial](https://peps.python.org/pep-0636/).

**Done when.** A function annotated `def foo(x) -> Result[int]:` passes `mypy --strict` and you
can write a caller that branches on `Success`/`Failure` cleanly.

---

### 3. `core/logging_setup.py`

**Goal.** One function — `setup_logging()` — called once at startup. Every other file gets a
logger with `structlog.get_logger(__name__)`. No `print()` in this codebase, ever.

**Build.**
- Configure `structlog` to emit JSON to file (`logs/kms.log`) and pretty-print to console in dev
  mode.
- Read log level from a parameter for now (you'll wire it to config in step 4).
- Set up a `correlation_id` context variable. Every log line inside a single pipeline run carries
  the same `correlation_id`. This is what lets the briefing trace one note's full journey later.

**New knowledge.**
- **structlog basics.** Resource:
  [structlog docs → "Getting Started" + "Configuration"](https://www.structlog.org/en/stable/getting-started.html).
  30 minutes.
- **Why structured logging matters for AI systems.** mCoding YouTube — "Modern Python logging"
  (~15 min). Also "The Twelve-Factor App: Logs" — [12factor.net/logs](https://12factor.net/logs).
- **Context variables.** Resource:
  [stdlib `contextvars`](https://docs.python.org/3/library/contextvars.html) docs.

**Done when.** Logging from a test file produces a JSON line in `logs/kms.log` with the
`correlation_id` field populated.

---

### 4. Config layer: `config/*.yaml` + `core/config.py`

**Goal.** Three YAML files of tunable values, loaded once by one Python module that exposes a
typed, validated `Config` object.

**Build the YAMLs.**
- `config/config.yaml` — vault root path, log level, default LLM provider, db path,
  env (dev/prod). Most of this already exists in your project; confirm the schema.
- `config/thresholds.yaml` — global confidence cutoffs (`auto: 0.85`, `suggest: 0.60`). Leave room
  for per-pipeline overrides.
- `config/routing.yaml` — empty for now. Phase 2 fills it. Create the file so imports don't break.

**Build `core/config.py`.**
- One Pydantic model per YAML file (`MainConfig`, `Thresholds`, `Routing`).
- One composing model `Config` containing all three.
- One function `load_config()` that reads all three files, validates, and returns `Config`.
- Use Pydantic's `BaseSettings` for any value that should be overridable by environment variable
  (API keys especially — never hardcode in YAML or commit to source control).

**Decisions.**
- **Singleton vs. dependency injection.** For your scale, a module-level singleton
  (`from core.config import CONFIG`) is fine and simpler. Pick one rule and apply it everywhere.
- **Validate vault path exists at load time?** Yes — fail fast. If the path is wrong, you want to
  know at startup, not at the first write.

**New knowledge.**
- **Pydantic v2.** Resource: [pydantic docs](https://docs.pydantic.dev/latest/), specifically
  "Models" and "Settings Management". Use **v2** — v1 examples float around Stack Overflow and
  look similar but break in v2.
- **PyYAML safe_load.** Resource: [PyYAML docs](https://pyyaml.org/wiki/PyYAMLDocumentation).
  Always `yaml.safe_load`, never `yaml.load`.

**Done when.** A unit test loads all three YAMLs, asserts thresholds parse to floats, asserts an
invalid YAML raises `ConfigError`.

---

### 5. `core/confidence.py` — Pattern: Confidence-Gated Routing

**Goal.** A pure function that takes an `AIDecision` plus thresholds and returns a routing outcome.
Used by every pipeline before any write. No side effects, no I/O.

**Build.**
- An `AIDecision` dataclass: `action: str`, `confidence: float`, `reasoning: str`,
  `source_ids: list[str]`.
- A `RoutingOutcome` enum: `AUTO_EXECUTE`, `FLAG_FOR_REVIEW`, `STAY_IN_INBOX`.
- One function `route(decision, thresholds) -> RoutingOutcome`.
- Pure function. No DB writes. Logs at debug level only.
- Thresholds are read from `Config` — never hardcoded here. The whole point of `thresholds.yaml`
  is that changing automation aggressiveness never requires a code change.

**New knowledge.**
- **Python Enums.** Resource: [stdlib `enum` docs](https://docs.python.org/3/library/enum.html),
  focus on `Enum` and `auto()`. 15 minutes.

**Done when.** Parametrized pytest covers all three branches and the boundary values (0.85, 0.60).

---

### 6. Storage layer

You will spend the most time here. Take it slow — the schema you define now carries all future
phases.

#### 6a. `storage/schema.sql`

**Goal.** A single `.sql` file the DB initializer reads at startup to create tables if they don't
exist.

**Tables for Phase 0.**
- **`documents`** — the index. Columns: `id` (text PK, use the relative vault path), `title`,
  `summary`, `note_type`, `confidence`, `created_at`, `updated_at`, `updated_by_human` (boolean),
  `content_hash`. **No content body** — that lives in the vault. This table is purely an index
  for retrieval.
- **`audit_log`** — append-only. Columns: `id` (autoincrement), `timestamp`, `pipeline`, `stage`,
  `source_ids` (JSON text), `decision`, `confidence`, `reasoning`, `outcome`, `correlation_id`.
  No UPDATE, no DELETE — ever.
- **`corrections`** — empty for now. Phase 7 fills it. Columns: `id`, `timestamp`, `document_id`,
  `field`, `ai_value`, `human_value`. Define the schema now so Phase 7 needs zero migration work.
- **`embeddings`** — skip until Phase 3. Do not create this table now.
- **`schema_version`** — single row, single integer column, used by the migration system.

**Indexes.** Add at minimum: `audit_log(timestamp)`, `audit_log(correlation_id)`,
`documents(note_type)`, `documents(updated_at)`.

**Append-only enforcement.** Add a SQLite trigger on `audit_log` that raises on UPDATE or DELETE.
Belt-and-braces with the application-layer rule.

**New knowledge.**
- **SQLite schema design.** Resource:
  [SQLite docs → "Datatypes In SQLite" + "CREATE TABLE"](https://www.sqlite.org/datatype3.html).
  SQLite's flexible typing is unusual if you've come from PostgreSQL or PySpark types.
- **CREATE TRIGGER.** Resource:
  [SQLite docs → CREATE TRIGGER](https://www.sqlite.org/lang_createtrigger.html).

#### 6b. `storage/migrations/`

**Goal.** Versioned SQL delta files that the migration system applies in order when the DB schema
version is behind. No code changes needed to evolve the schema — drop a new `.sql` file in this
folder.

**Build.**
- Naming convention: `001_initial.sql`, `002_add_embeddings.sql`, etc.
- For Phase 0, create a placeholder `001_initial.sql` that is effectively empty (or contains a
  comment) — the base schema is in `schema.sql`. This exists so `db.py`'s migration loop has
  something to iterate over and proves the mechanism works.
- The rule: `schema.sql` creates tables for version 0. Each `migrations/00X_*.sql` is a delta
  applied on top, bumping `schema_version` by 1 after each.

**Why scaffold this now?** Phase 3 adds an `embeddings` table. Phase 7 enriches the `corrections`
table. If you don't build the migration runner now, you will manually hack the live database
later and corrupt your audit trail. Scaffold once, use forever.

#### 6c. `storage/db.py`

**Goal.** Connection management plus the migration runner. Other modules call `get_connection()`
or use a context manager — they never touch `sqlite3.connect()` directly.

**Build.**
- `init_db(db_path)`: creates the file if absent, runs `schema.sql`, sets
  `PRAGMA journal_mode=WAL`, sets `PRAGMA foreign_keys=ON`.
- Migration runner: reads `schema_version`; scans `migrations/` for unapplied `00X_*.sql` files;
  runs them in order and bumps the version. For Phase 0 there is one file, so this is mostly
  plumbing — but build it correctly because Phase 3 (embeddings table) and Phase 7 (corrections
  enrichment) will both add migrations.
- Connection management: thread-local connection. Sync, not async. **Always parameterized queries
  — no string concatenation, ever.**

**Decisions.**
- **Raw `sqlite3` vs. ORM.** Use raw `sqlite3` for now. ORMs hide what's happening; you want to
  learn the mechanics. SQLAlchemy becomes the right answer at 10k+ documents — not before.
- **WAL mode.** Yes, always. Better concurrency, no real downsides at your scale.

**New knowledge.**
- **Python `sqlite3` stdlib.** Resource:
  [stdlib sqlite3 docs](https://docs.python.org/3/library/sqlite3.html), or
  [Real Python — "Data Management With Python, SQLite, and SQLAlchemy"](https://realpython.com/python-sqlite-sqlalchemy/)
  (just the SQLite section).
- **WAL mode.** Resource: [SQLite docs → Write-Ahead Logging](https://www.sqlite.org/wal.html).
  10 minutes.
- **Migrations without an ORM.** Read the `yoyo-migrations` library README to understand the
  pattern — then roll your own (~30 lines). You don't need a library dependency for something
  this simple.

#### 6d. `storage/audit_log.py`

**Goal.** Two functions: `append(entry)` and `query(...)`. That is it. No business logic. The
audit log module is dumb storage.

**Build.**
- An `AuditEntry` dataclass mirroring the schema columns.
- `append(entry)` — single INSERT, parameterized.
- `query(date=None, pipeline=None, correlation_id=None)` — filtered SELECT.
- Append-only enforced both at the table level (trigger from 6a) and here (no `update_*` or
  `delete_*` function exists in this module at all).

**Done when.** A script appends ten entries and `query(date=today)` returns ten rows.

---

### 7. `core/audit.py` — Pattern: Audit Trail (the writer face)

**Goal.** The module other code imports to record AI decisions. Wraps `storage/audit_log.py`
with conveniences pulled from the wider system: `correlation_id` from context, timestamps,
JSON serialization of `source_ids`.

**Build.**
- A function `record_decision(pipeline, stage, decision, outcome)` that constructs an `AuditEntry`
  (timestamp = now, `correlation_id` from `contextvars`, `source_ids` JSON-encoded) and appends it.
- A decorator `@audited(pipeline_name, stage_name)` that wraps a pipeline-stage function and
  records the decision and outcome automatically.

**Why split this from `storage/audit_log.py`?** Storage handles the table.
`core/audit.py` handles the *concept* of recording a decision. The split means `storage/` stays
a pure data layer with no knowledge of pipeline concepts like `correlation_id`.

**New knowledge.**
- **Decorators with arguments.** Resource:
  [Real Python — "Primer on Python Decorators"](https://realpython.com/primer-on-python-decorators/),
  section on decorators with arguments. 30 minutes.

**Done when.** A function decorated with `@audited(...)` produces an audit row when called, with
the `correlation_id` correctly populated from logging context.

---

### 8. `core/pipeline.py` — Pattern: Pipeline Runner

**Goal.** A small composer that takes a list of stages and runs them in sequence, threading the
output of each stage into the next. Halts on the first `Failure`. Logs each stage entry/exit.
Wraps the whole run in a fresh `correlation_id`.

**Build.**
- A `Stage` is a callable: `(input, context) -> Result[output]`. Define it as a `Protocol`.
- `run_pipeline(name, stages, initial_input) -> Result` sets a fresh `correlation_id`, iterates
  the stages, threads outputs, halts on `Failure`.
- Define a `PipelineContext` dataclass containing `config` and `correlation_id`. Pass it alongside
  the data input so stages can read config and write audit entries without globals.

**Decisions.**
- **Sync or async?** Go async from day one. Pipeline stages call LLMs (network I/O). Don't
  rewrite this later. Use `asyncio` and `async def` for all stages.
- **Pass context as a separate argument vs. fold it into the input?** Separate argument. Keeps
  the data flow clean and the stage signatures readable.

**New knowledge.**
- **asyncio basics.** Resource:
  [Real Python — "Async IO in Python: A Complete Walkthrough"](https://realpython.com/async-io-python/).
  Focus on `async def`, `await`, `asyncio.run`, `asyncio.gather`. Skip the advanced patterns.
  1 hour — worth every minute, since every pipeline stage will be async.
- **`typing.Protocol`** (for typing the Stage signature). Resource:
  [PEP 544](https://peps.python.org/pep-0544/) intro section.

**Done when.** A test pipeline of three trivial async stages (e.g., `add_one`, `double`,
`to_string`) runs end-to-end and produces one audit log entry per stage with the same
`correlation_id`.

---

### 9. LLM layer: `llm/prompt_loader.py` + `llm/provider.py` + `llm/claude_provider.py`

This is new in the revised Phase 0. The old plan deferred all LLM interaction to Phase 1. The
revised roadmap moves the LLM plumbing here because Phase 1 (Capture) will immediately call
`summarize` — if the provider is broken or untested, Phase 1 is blocked before it starts.

Build in this order within this step: `prompt_loader` first, `provider` second,
`claude_provider` third. Each builds on the previous.

#### 9a. `llm/prompt_loader.py`

**Goal.** Load all `prompts/*.yaml` files at startup into a dict. Each prompt file declares its
system message, user template, expected variables, model, and temperature. Templates render with
Jinja2.

**Build.**
- A `Prompt` Pydantic model: `name`, `system`, `user`, `variables` (list of expected variable
  names), `model`, `temperature`.
- `load_prompts(directory) -> dict[str, Prompt]`.
- A `Prompt.render(**vars)` method using Jinja2 `StrictUndefined` to substitute variables.
  Raise loudly if any expected variable is missing — silent variable defaults are how you ship a
  broken prompt to production.

**Decisions.**
- **Jinja2 vs. `str.format()`** — Jinja2. F-strings can't read from a YAML file. `str.format()`
  is fragile around braces. Jinja2 is the right choice now, and it will remain the right choice
  when you add few-shot examples in Phase 7 (Self-Learning).

**Create a test prompt for the smoke test.** Drop a `prompts/test.yaml` with one variable and a
trivial system prompt. You'll render this in the smoke test to confirm the chain works.

**New knowledge.**
- **Jinja2 basics.** Resource:
  [Jinja2 docs → "Template Designer Documentation"](https://jinja.palletsprojects.com/en/stable/templates/),
  variable substitution and `StrictUndefined` only. 15 minutes.

**Done when.** `PROMPTS['test'].render(x='hello')` returns a string. `.render()` without `x`
raises an error.

#### 9b. `llm/provider.py`

**Goal.** An abstract base class (ABC) that all LLM providers implement. This is the interface
the rest of the codebase uses — no pipeline ever imports `claude_provider` directly.

**Build.**
- An abstract `LLMProvider` class with one abstract method:
  `async def complete(self, system: str, user: str) -> Result[str]`.
- A `LLMResponse` dataclass: `content: str`, `model: str`, `usage: dict`. The provider always
  returns this — callers never parse raw API responses.
- Keep it minimal. Don't add streaming, tool use, or multi-turn here. That comes in Phase 1+.

**Why an ABC here?** The layout already shows `llm/ollama_provider.py` alongside
`claude_provider.py`. If you hardcode `anthropic.Anthropic()` in the pipeline, switching
providers requires touching every pipeline file. The ABC costs 15 lines today and saves hours
later.

#### 9c. `llm/claude_provider.py`

**Goal.** The concrete Anthropic implementation of `LLMProvider`. One working API call to Claude,
end-to-end, returning `Result[LLMResponse]`.

**Build.**
- `ClaudeProvider` implements `LLMProvider`.
- Read API key from environment variable via Pydantic `BaseSettings` — never from code, never
  from YAML. A startup check: if the key is missing, raise `ConfigError` immediately.
- The `complete()` method: call `anthropic.AsyncAnthropic().messages.create(...)`, wrap the
  response in `LLMResponse`, return `Success(response)`. Catch `anthropic.APIError` and return
  `Failure(recoverable=True, ...)` — never let a network error raise an unhandled exception
  through pipeline stages.
- Use `claude-haiku-*` as the default model for Phase 0 (cheapest, fastest — good for plumbing
  tests). Switch to Sonnet in Phase 1 prompts via `prompts/*.yaml`.

**Done when.** A single integration test (marked `@pytest.mark.integration` so it doesn't run
in CI without a key) calls `ClaudeProvider().complete(system="...", user="ping")` and gets a
non-empty string back.

> **Note:** Add `ANTHROPIC_API_KEY` to your `.env` file and load it with
> `python-dotenv` or Pydantic `BaseSettings`. Never commit the key.

---

### 10. Vault layer

#### 10a. `vault/paths.py`

**Goal.** One module that knows about every folder in the vault. Other modules ask for paths —
they never construct them.

**Build.**
- Read vault root from `Config`.
- Functions: `inbox()`, `briefings_today()`, `briefings_for(date)`, `project_dir(name)`,
  `domain_dir(name)`, `documentation(project)`, `synthesis_week(date)`, `archive()`.
- Each returns a `pathlib.Path`. Each ensures the directory exists
  (`Path.mkdir(parents=True, exist_ok=True)`).
- ISO date format only (`2026-04-25.md`), not `12_04` style.

**New knowledge.**
- **`pathlib`.** Resource: [stdlib pathlib docs](https://docs.python.org/3/library/pathlib.html).
  If you've used `os.path.join` from PySpark scripts, `pathlib` is the modern replacement —
  different API, much cleaner.

#### 10b. `vault/frontmatter.py`

**Goal.** Wraps `python-frontmatter` with project-specific helpers. Other modules call
`frontmatter.parse(path)` and `frontmatter.write(path, content, metadata)` — they never import
`python-frontmatter` directly.

**Build.**
- Wrap `python-frontmatter`'s `load()` and `dump()`.
- Define a `NoteMetadata` Pydantic model with all expected frontmatter fields: `type`, `tags`,
  `project`, `domain`, `created`, `updated`, `confidence`, `updated_by_human`, `summary`,
  `source`, `status`.
- Wrappers parse into / serialize from `NoteMetadata`. Unknown frontmatter fields are preserved
  (they might be user additions Claude should not strip) but logged at debug level.

**New knowledge.**
- **`python-frontmatter`.** Resource:
  [GitHub README — eyeseast/python-frontmatter](https://github.com/eyeseast/python-frontmatter).
- **PyYAML 1.1 boolean quirks.** Just know that `yes`/`no`/`on`/`off` parse as booleans by
  default. Always use `safe_load`. 5 minutes of awareness, not study.

#### 10c. `vault/reader.py`

**Goal.** One function: `read_note(path) -> Result[Note]`. Returns a `Note` dataclass with
`content`, `metadata`, `path`, `content_hash`. Computes the hash here so other code doesn't have
to.

**Build.**
- Use `vault/frontmatter.py` to parse.
- Compute SHA-256 of the body using `hashlib`.
- Return `Success(Note(...))` or `Failure(...)` if parsing fails.

#### 10d. `vault/writer.py` — load-bearing

**Goal.** All vault writes go through this one module. Enforces idempotency and the
`updated_by_human=true` rule. This file is non-negotiable for the trust model of your whole
system.

**Build.**
- `write_note(path, content, metadata, source) -> Result[None]`:
  - `source` is a literal: `'ai'` or `'human'`.
  - If the path exists, read existing frontmatter. If `updated_by_human=true` and `source='ai'`,
    abort with `Failure(recoverable=False, ...)` and an error message clear enough to surface in
    the daily briefing.
  - Otherwise upsert. **Atomic write**: write to a temp file in the same directory, then
    `os.replace()` to swap. Never leave a partial file.
  - Always update `updated_at`. Set `updated_by_human=False` when `source='ai'`, `True` when
    `source='human'`.
- `move_note(src, dst, source) -> Result[None]`: same atomicity, same `updated_by_human` check.

**Decisions.**
- **How does the writer know AI vs. human?** Pass the `source` argument explicitly at every call
  site. Don't infer it from anything. This is a deliberate friction point — it forces every
  caller to declare intent.

**New knowledge.**
- **Atomic file writes.** Search "atomic file write Python `os.replace`" — the
  temp-file-then-rename pattern. This is critical: a crash mid-write must never corrupt a note.
- **`hashlib` and `shutil`.** Resource:
  [stdlib docs](https://docs.python.org/3/library/hashlib.html). Standard, no surprises.

**Done when.** A test does: write note as `source='ai'` (succeeds) → mutate the file's
frontmatter to set `updated_by_human=true` → attempt a second write as `source='ai'` → assert
`Failure` is returned with a clear, useful error message.

#### 10e. `vault/indexer.py`

**Goal.** Scan the vault and detect which notes have changed since the last index run, using
content hash comparison. Other layers — especially the retrieval layer in Phase 3 — call this to
stay in sync without re-processing every note on every run.

**Build.**
- `scan_vault(vault_root) -> list[VaultEntry]` where `VaultEntry` is a dataclass:
  `path: Path`, `content_hash: str`, `metadata: NoteMetadata`.
- `detect_changes(current: list[VaultEntry], db_conn) -> ChangeSummary` where `ChangeSummary`
  contains three lists: `added`, `modified`, `deleted` (by vault path key).
- Uses `vault/reader.py` to parse each file — never opens files directly.
- Logs a summary at INFO level: `"Scan complete: X added, Y modified, Z deleted"`.

**Why build this in Phase 0?** Phase 3 (Search) and Phase 8 (Briefing) both need to know what
changed in the vault. Building the indexer now with no dependents means you can test it cleanly
against a fixture vault without entangled pipeline logic.

**Done when.** A test creates three markdown files in a `tmp_path` vault, runs
`scan_vault()`, deletes one file, adds a new one, mutates another, runs again, and asserts the
`ChangeSummary` correctly reports 1 added, 1 modified, 1 deleted.

---

### 11. Smoke test

**Goal.** A single script `scripts/smoke_phase0.py` (and a parallel `tests/test_smoke.py`) that
wires every Phase 0 piece together and proves they cooperate. **Phase 0 is not done until this
passes.**

**Steps.**
1. Load config.
2. Setup logging.
3. Initialize DB (creates tables from `schema.sql`, runs migration on `migrations/001_initial.sql`).
4. Load prompts from `prompts/`.
5. Construct a `NoteMetadata` and content body.
6. Call `vault/writer.py` with `source='ai'` to write to a temp directory.
7. Call `vault/reader.py` to read it back. Assert `content_hash` matches.
8. Upsert a row into the `documents` table via `storage/db.py`.
9. Render `prompts/test.yaml` with a dummy variable. Assert the string output contains the
   expected substitution.
10. Call `ClaudeProvider().complete(system="You are a test assistant.", user="Respond with: OK")`
    — assert `Success` is returned and the content is non-empty. (Mark this step
    `@pytest.mark.integration` so it skips in CI without an API key.)
11. Decorate a trivial async stage function with `@audited`, run a single-stage pipeline through
    `core/pipeline.py`.
12. Query `audit_log` — assert exactly one row with the matching `correlation_id`.
13. Run `vault/indexer.py` `scan_vault()` on the temp directory — assert the written note appears
    in the results.
14. Attempt to write the same note again with `source='ai'` after flipping
    `updated_by_human=true` — assert `Failure`.

If all assertions pass, Phase 0 is done.

---

## Testing strategy for Phase 0

- One test file per module: `tests/test_core/test_result.py`, `tests/test_vault/test_writer.py`,
  etc.
- Use pytest fixtures (`tmp_path`, custom `temp_vault`, `temp_db`) for isolation.
- Mark any test that makes a real network call with `@pytest.mark.integration`. These should not
  run in CI unless an API key is present.
- Don't write integration tests beyond the smoke test in this phase.
- Aim for ~80% coverage on `core/` and `vault/` — they're load-bearing. Storage layer can be
  lighter.

**New knowledge.**
- **Pytest fundamentals + fixtures.** Resource:
  [Real Python — "Effective Python Testing With Pytest"](https://realpython.com/pytest-python-testing/).
  One sitting.
- **`tmp_path` fixture.** Resource:
  [pytest docs → "How to use temporary directories and files"](https://docs.pytest.org/en/stable/how-to/tmp_path.html).
- **`pytest-asyncio`** for testing async code. Resource:
  [pytest-asyncio docs](https://pytest-asyncio.readthedocs.io/). 15 minutes.
- **`pytest.mark`** for custom markers (integration, slow). Resource:
  [pytest docs → "Working with custom markers"](https://docs.pytest.org/en/stable/how-to/mark.html).

---

## Recommended deeper reading (worth real investment)

- **"Architecture Patterns with Python"** — Percival & Gregory. Chapters 1–4 cover Result types,
  repositories, and the layered architecture you're building. Read while building, not before —
  abstractions click when they're solving a problem you've actually hit.
- **"A Philosophy of Software Design"** — Ousterhout. Short. The "deep modules" idea is exactly
  what `vault/writer.py` should be.
- **"The Pragmatic Programmer"** — Hunt & Thomas. Audiobook-friendly. Chapters on tracer code
  and reversibility apply directly to how you sequence Phase 0.
- **"Designing Data-Intensive Applications"** — Kleppmann. Chapter 7 (transactions) becomes
  valuable once you start worrying about partial pipeline failures in Phase 2+.
- **YouTube — ArjanCodes channel.** Search his videos on dataclasses, decorators, and Pydantic.
  ~15-20 min each, well-paced.

---

## Common traps that will eat days

1. **Building handlers before pipelines work.** The temptation to add YouTube + PDF + email
   handlers before `capture.py` runs end-to-end is enormous. Resist. Handlers are Phase 1.

2. **"I'll add the audit log later."** You won't. Adding traceability after the fact means
   re-instrumenting every function. Bake it in now.

3. **Confusing the SQLite `documents` table with the vault.** The vault is the source of truth.
   The DB is an index. If you ever feel tempted to delete a row from `documents` to "fix"
   something, stop and rethink.

4. **Async/sync mixing.** Go async for pipelines and LLM calls, sync for storage and config.
   Don't mix within a layer. If you must call sync from async, use `asyncio.to_thread`.

5. **Pydantic v1 syntax.** Use v2 only. v1 examples are everywhere on Stack Overflow and look
   almost identical but break in v2.

6. **Skipping atomic writes in `vault/writer.py`.** A crash mid-write corrupts a note. Users
   lose trust permanently. Use `os.replace()` after writing to a sibling temp file.

7. **Building `core/audit.py` before `core/logging_setup.py`.** Audit needs the `correlation_id`
   from logging context. Reverse the order and you'll rewrite both.

8. **Hardcoding the API key anywhere.** Not in code. Not in YAML. Not even as a default. In
   `.env`, loaded by `BaseSettings`. `.env` is gitignored.

9. **Skipping `vault/indexer.py` because "nothing uses it yet."** Phase 3 (Search) and Phase 8
   (Briefing) both depend on change detection. Building the indexer untested, under deadline
   pressure, inside Phase 3 will cost more time than building it clean now.

10. **Testing the LLM provider with a real API call in the main test suite.** Every run costs
    money and fails in CI without a key. Mark it `@pytest.mark.integration` from day one.

---

## When you finish

You should be able to demo:

- Drop a markdown file → the writer accepts it under `source='ai'`.
- Read it back, assert hash matches.
- Upsert into `documents`.
- Run the migration system, confirm `schema_version` is correct.
- Render a prompt template with a variable, confirm output.
- Make one real Claude API call, get a response back.
- Run a fake pipeline that writes one audit row.
- Query the audit log for today, see the row.
- Scan the temp vault with the indexer, see the note appear.
- Try to overwrite a human-edited note as AI → blocked with a clear error.

When that demo runs clean, move to Phase 1: Capture.
