# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# AI-kms

AI-enhanced knowledge management system for busy managers. Watches an Obsidian vault, processes drops (notes, PDFs, emails, YouTube, web articles), summarises and classifies them, and surfaces patterns back to the user via a daily briefing and MCP server.

---

## Project context

**Target user:** a non-technical executive. Zero organisational effort is the baseline assumption. The AI does the work; the human does the judgment.

**Key constraint:** Hard delivery deadline 30 June 2026. Current phase: Vault-Restructure complete (ADR-0006 editable/no-edit split, 956 tests). Next: Phase 2 — Classify pipeline. Three milestones:
- M1 ~15 May — Capture + Classify + Search end-to-end
- M2 ~30 May — MCP MVP live for boss demo
- M3 30 June — Full feature set (Promotion, Documentation, Self-learning, Briefing)

**Reference docs** (read before changing architecture):

_Session orientation — read these first:_
- `STATE.md` — current position, architecture decisions, open questions, and tech debt; read at session start
- `CONTEXT.md` — key domain concepts and vocabulary
- `CONSTRAINTS.md` — hard constraints; check before any design or code change
- `OPEN_QUESTIONS.md` — unresolved decisions; check before making a new design call
- `TECH_DEBT.md` — deferred tasks; check when touching related code

_Architecture and design:_
- `docs/roadmap/roadmap.md` — phase-by-phase build order and rules of the road
- `docs/roadmap/design_artifacts/top-level_layout.md` — every folder explained, pattern-to-folder mapping
- `docs/architecture/overall_design.md` — container-level architecture overview
- `docs/architecture/system_diagram.md` — context diagram of the whole system
- `docs/architecture/system_adr/` — system-wide Architecture Decision Records

_Skill output folders (where skills read/write — numbered by pipeline stage):_
- `docs/0_draft/` — raw input drafts (pipeline input)
- `docs/1_design/` — output of `/codebase-design-analysis`; input to `/writing-detailed-specs`
- `docs/2_usability_test/` — success criteria from `/codebase-design-analysis`
- `docs/3_specs/` — output of `/writing-detailed-specs`; input to `/research` and `/plan-from-specs`
- `docs/4_research/` — output of `/research`; consumed by `/plan-from-specs`
- `docs/5_plans/` — output of `/plan-from-specs`; executed by `/tdd-implement`
- `docs/discussions/` — output of `/capture_discussion_v2`; historical design rationale
- `/build-pipeline` orchestrates `design→spec→research→plan` as isolated subagents (lean main session); the four skills above still run standalone.
---

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.12+ |
| Config validation | Pydantic v2 |
| Logging | structlog (stdlib interop via `logging_setup.py`) |
| AI provider | Anthropic Claude (via `llm/claude_provider.py`); Ollama as fallback |
| Database | SQLite with FTS5 (migrations in `storage/migrations/`) |
| Embeddings | `sentence-transformers` — `all-MiniLM-L6-v2` |
| Vault format | Obsidian markdown with YAML frontmatter |
| CLI | Click (`cli/main.py`, entry point `kms`) |
| Packaging | `uv` + setuptools with `[tool.setuptools.packages.find]` |
| MCP transport | stdio first (Claude Desktop compatible); HTTP deferred |

---

## Set up layout
```
/
├── CLAUDE.md          ← behavioral contract: rules, conventions, preferences
├── CONTEXT.md         ← key domain concepts and vocabulary
├── STATE.md           ← current implementation progress
├── CONSTRAINTS.md     ← hard constraints; check before design or code changes
├── TECH_DEBT.md       ← deferred tasks to revisit
├── OPEN_QUESTIONS.md  ← unresolved decisions
├── docs/
│   ├── 0_draft/              ← raw input drafts (pipeline input)
│   ├── 1_design/             ← output of /codebase-design-analysis
│   ├── 2_usability_test/    ← success criteria from /codebase-design-analysis
│   ├── 3_specs/              ← output of /writing-detailed-specs
│   ├── 4_research/          ← output of /research
│   ├── 5_plans/              ← output of /plan-from-specs
│   ├── discussions/           ← output of /capture_discussion_v2
│   ├── _archive/              ← completed phase artifacts (mirrors numbered structure)
│   ├── architecture/
│   │   ├── system_adr/            ← system-wide ADRs
│   │   ├── system_diagram.md      ← context diagram of whole system
│   │   ├── overall_design.md      ← container-level architecture overview
│   │   ├── phase0_foundations/    ← Phase 0 component map + domain ADRs
│   │   │   ├── _OVERVIEW.md
│   │   │   └── adr/
│   │   └── phase1_capture/        ← Phase 1 component map + domain ADRs
│   │       ├── _OVERVIEW.md
│   │       └── adr/
│   ├── reference/                 ← cloned reference implementation
│   │   └── knowledge-base-server/
│   └── roadmap/
│       ├── roadmap.md
│       └── design_artifacts/      ← designs produced while making roadmap
└── src/                           ← source code
```

## Repository layout

```
AI-kms/
├── src/
│   ├── cli/             ← Click commands; each command just calls a pipeline
│   ├── config/          ← tunable behavior ONLY (thresholds, routing, providers)
│   ├── core/            ← shared primitives: result, audit, confidence, pipeline, config, logging, tags
│   ├── handlers/        ← one class per input type; self-register at startup
│   ├── llm/             ← provider abstraction + prompt loader
│   ├── pipelines/       ← one file per roadmap feature; pure-function stages (capture, reconcile)
│   ├── prompts/         ← all AI prompts as YAML — edit here, never in code
│   ├── storage/         ← SQLite state (audit log, batches, document index)
│   │   └── migrations/  ← numbered .sql migration files (001–005; 006 pending TD-040/TD-041)
│   └── vault/           ← ALL Obsidian filesystem I/O; nothing else touches the vault directly
│       ├── move_guard.py ← suppresses watcher re-home for pipeline-initiated moves
│       └── (reader, writer, watcher, indexer, paths, frontmatter)
├── tests/               ← mirrors src/ layout; fixtures/ for test vault files
├── data/                ← runtime data (SQLite db)
└── logs/                ← runtime logs
```

**Vault layout** (the Obsidian folder, separate from the repo):
```
Vault/
├── inbox/                ← single drop zone
│   └── .summaries/       ← sibling .md files for inbox binaries
├── Projects/
│   └── <A>/
│       ├── CLAUDE.md           ← human-facing index (TD-015, out of scope)
│       ├── <user notes>.md
│       ├── <editable non-md>   ← csv, docx, xlsx etc. (visible in Obsidian)
│       └── attachment/         ← no-edit binaries only (pdf, png, jpg, etc.)
│           ├── report.pdf
│           └── .summaries/     ← hidden from Obsidian; sibling .md files indexed here
│               └── report.pdf.md  ← vault_path for this row; attachment_path → binary
├── Domain/
│   └── <D>/
│       ├── attachment/         ← per-domain no-edit binaries, same structure
│       │   └── .summaries/
│       └── Archive/            ← archived projects under this domain
├── Documentation/        ← one living page per active project (capture-excluded)
├── Briefings/            ← daily AI reports (capture-excluded)
├── Synthesis/            ← weekly AI journals (capture-excluded)
└── (no global Archive/ or attachment/ — both are per-Domain/Project)
```

---

## Extension Point Rule

Every component that processes, classifies, routes, or stores data must be
open for extension without modification. Concretely:

- New handler → add a class, register it. Do not touch the pipeline.
- New behavior → implement the Protocol. Do not add a branch to existing code.
- New threshold or rule → edit a config file. Do not hardcode it.

**What counts as an extension point:**
- `Protocol` or `ABC` — callers depend on the interface, not the concrete class
- Handler registry — new variants self-register at startup
- Config/YAML key — behavior is data, not logic

**What is a design violation:**
Adding a new source type, AI provider, output format, or classification rule
requires touching existing pipeline code. If that is true, the component is
closed — flag it before implementing, not after.

**How to mark coupling when it's unavoidable:**
Add a `# COUPLING:` inline comment explaining what would be needed to
generalize it and why it was not done now.

## Coding patterns — follow these exactly

**1. Pipeline Pattern**
Every feature is a sequential pipeline. Each stage is a pure function. Never bundle stages. Never skip stages.
```python
# Good
result = extract(raw) | summarize | classify | store

# Bad — bundled stages
def process(raw):
    summary = llm.call(raw)          # extract + summarize bundled
    db.save(summary, classify(raw))  # classify + store bundled
```

**2. Result Type — no silent failures**
Every public function returns `Success(value)` or `Failure(error, recoverable, context)`. Callers must handle both.
```python
# Good
match capture(file):
    case Success(note): route(note)
    case Failure(err, recoverable=True): queue_for_retry(err)
    case Failure(err): alert(err)

# Bad
note = capture(file)   # caller can ignore errors
```

**3. Confidence-Gated Routing — thresholds in config, never in code**
```python
# Good — thresholds read from config/thresholds.yaml
gate = ConfidenceGate.from_config(config)
action = gate.route(confidence_score)  # auto | review | inbox

# Bad
if confidence > 0.85:   # hardcoded threshold — hook will block this
```

**4. Handler Registry — new source = new file only**

`BaseHandler` + `HandlerRegistry` are scoped to **filesystem drops only** — the
interface takes `Path` and dispatches by file extension. URLs / YouTube /
Slack / email are NOT registry handlers; they are inline pipeline stages
that augment a filesystem drop (see `handlers/url_fetcher.py` for the
canonical pattern and rationale).

```python
# Good — drop a new file-format handler, register, done
@HandlerRegistry.register
class EpubHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".epub"
    def extract(self, path: Path) -> Result[RawContent]: ...

# Bad — adding a new elif to a central dispatch function
# Bad — registering a UrlHandler / YouTubeHandler (those are pipeline
#       stages, not registry handlers — would scope-conflict with
#       MarkdownHandler for notes containing both prose and links)
```

**5. Prompts as Config — never hardcode prompts**
```python
# Good
prompt = prompt_loader.get("summarize")   # from prompts/summarize.yaml

# Bad
prompt = f"Summarise this note: {content}"   # hook will warn on this
```

**6. Audit Trail — every AI decision is logged**
Every pipeline stage that makes an AI decision must call `audit.write(...)` with: timestamp, source note IDs, decision, confidence, reasoning, outcome. No exceptions.

**7. Idempotent Writes — upsert only; never overwrite human edits**
```python
# Good — vault/writer.py checks updated_by_human before every write
writer.upsert(note)   # safe to call twice

# Bad
vault_path.write_text(content)   # bypasses the guard — hook will block this
```

---

## Reference project

A cloned reference implementation lives at `../knowledge-base-server/`.
Read it to understand design patterns worth adapting — do not copy it wholesale.

Key areas to reference:
- `.docs/reference/knowledge-base-server/src/` — see how they structure handlers and pipelines
- `.docs/reference/knowledge-base-server/README.md` — overall architecture intent

When adapting from the reference: explain what you're borrowing and why,
and flag where our approach intentionally diverges.

---

## Environment

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # required; read by core/config.py via pydantic-settings
```

Vault root path is set in `config/config.yaml` under `vault.root`. It must exist on disk before startup.

---

## Commands

```bash
# Setup
uv sync                        # install dependencies

# Tests
uv run pytest tests/                                              # full suite
uv run pytest tests/test_core/test_config.py                     # single file
uv run pytest tests/test_core/test_config.py::TestVaultConfig    # single class
uv run pytest -m "not smoke"                                      # skip tests that need real vault on disk

# Lint/Format
uv run ruff check .                                                 # lint
uv run ruff format --check .                                        # check formatting (auto-applied on write by hook)

# CLI (after install)
kms capture <file>             # run capture pipeline on a single file
kms classify <file>            # run classify pipeline
kms search "<query>"           # semantic + keyword search, hot tier first
kms briefing                   # generate today's briefing

# Dev helpers
uv run python test.py          # quick smoke test (write → parse → upsert → audit)
```

The `smoke` pytest marker (defined in `pyproject.toml`) flags tests that hit real disk config or the real vault. Skip them with `-m "not smoke"` when the vault isn't set up locally.

---

## Automated enforcement

The following rules are enforced by hooks in `.claude/settings.json` — Claude Code will block or warn if they are violated. Do not attempt to work around them.

| Rule | Severity | What triggers it |
|---|---|---|
| No direct vault writes outside `vault/writer.py` | ⛔ Hard block | `.write_text()` or `open(..., 'w')` in any `.py` file except `vault/writer.py` |
| No hardcoded confidence thresholds in pipelines | ⛔ Hard block | Float literals in `if/elif` comparisons inside `pipelines/` |
| No logic in `mcp_server/tools.py` | ⛔ Hard block | `if`, `elif`, `for`, `while` at statement level in that file |
| No destructive bash commands | ⛔ Hard block | `rm -rf`, `DROP TABLE`, `TRUNCATE` |
| No hardcoded prompt f-strings | ⚠️ Warning | f-strings containing prompt-like keywords in `.py` files |
| Auto-format on every write | ℹ️ Silent | `ruff format` runs automatically on every `.py` file Claude touches |

---

## Critical rules — judgment required, not auto-enforced

- **Never add an MCP tool before its pipeline exists and is tested.** A stub tool that calls nothing is a lie.
- **`updated_by_human = true` means hands off.** If a frontmatter field carries this flag, do not propose overwriting it. Surface a conflict instead.
- **Audit log is non-negotiable from Phase 1.** Phase 8 (Daily Briefing) reads from it. No audit log means no briefing.
- **Schedulers come last.** Build manual CLI first, then automate.

---

## Build progress

**Overall:** Phase 1 of 8 complete + Brief #2/#3 done + Phase 1.5 Pay-Debt complete + Phase Pre-2 complete + Vault-Restructure complete (2026-06-04, 956 tests). TD-042 deprecated-key strip complete (2026-06-07, 959 tests). TD-040/TD-041 Batch-ID Fix plan complete (2026-06-07) — ready for TDD. Next: Phase 2 — Classify pipeline.

(Phase 0 + Phase 1 checklists closed — see STATE.md for full history.)

**Brief #3 — attachment_sync_and_archive** _(complete 2026-05-24)_:
- [x] Phase 1: watcher VaultConfig signature (TD-023), .summaries/ skip (TD-AS-1), false-success logging
- [x] Phase 2: domain_archive helper, archive_path property removal
- [x] Phase 3: _is_binary, _sibling_for, on_delete/on_move sync callbacks
- [x] Phase 4: kms reconcile — 7-stage reconcile command (paths, orphan binaries, stale binaries, orphan siblings, stale tags, stale batch refs, editable migration)

**Phase 1.5 Pay-Debt** _(complete + code-review clean 2026-06-03; see STATE.md)_:
- [x] All 7 phases (FILE_LOST guard, location tags, stale-tag reconcile, folder capture, handlers extension, idempotent capture, stale-batch-ref reconcile)
- [x] Code-review pass: 2 critical (batch_id wiring, folder timer race) + 4 important + 2 minor fixed; 797 tests pass (after Phase Pre-2). Branch `fix/phase1.5-codereview` NOT pushed.
- Deferred: rename gate rework (TD-029), binary-modify re-capture (TD-037)

**Vault-Restructure — Editable/No-Edit Split** _(complete 2026-06-04; merged from worktree branch, 956 tests)_:
- [x] Phases 1–7: `no_edit_extensions` config, `resolve_placement()`, editable→root / no-edit→attachment/ routing, AI-output folder skip, misplaced-file sweep
- [x] Phase 8: Binary content-change detection (SHA-256 compare, lock-file filter) — resolves TD-037
- [x] Phase 9: Settle window for multi-hop move coalescing
- [x] Phase 10: Root-level `.summaries/` support + `reconcile_editable_migration` (Stage 7)
- New module: `vault/move_guard.py` — suppresses watcher re-home for pipeline-initiated moves

**Phase Pre-2 — DB Schema Prep + Domain Scalar Cleanup** _(complete 2026-06-03; 5 commits, 797 tests at completion)_:
- [x] Phase 1: 3 new SQL migration files (003_add_project, 004_add_status, 005_add_key_topics) + schema-presence test
- [x] Phase 2: DocumentRow + project/status/key_topics fields; _row_from_sqlite, upsert, replace_path updated
- [x] Phase 3: `_DEPRECATED_KEYS = frozenset({"domain"})` in frontmatter.py; domain scalar removed from NoteMetadata
- [x] Phase 4: Removed domain kwarg from capture pipeline consumers; tag-based domain filter
- [x] Phase 5: Full suite green — 797 tests pass

---

## Key runtime patterns

**CONFIG singleton** — call `load_config()` once at startup (in `cli/main.py`). Every other module imports the validated singleton directly:
```python
from core.config import CONFIG
```

**LLM model routing** — per-task model selection is in `config/config.yaml` under `providers:`. Never hardcode model names in code; always read from config.

**Correlation IDs** — call `new_correlation_id()` from `core/logging_setup.py` at the top of every pipeline entry point. All downstream log lines and audit entries inherit it automatically via Python contextvars.

---

## What Claude gets wrong in this codebase

### Test patterns
- **Deprecated-key test fixtures cannot use `write_note` — `dumps()` strips `_DEPRECATED_KEYS` at write time.** Use `shutil.copy()` from pre-written `.md` files in `tests/fixtures/` to land a note on disk that still has the deprecated key. Applies to any key added to `_DEPRECATED_KEYS` in `vault/frontmatter.py`.
- **`RuntimeWarning` in `test_claude_cli_provider.py` is pre-existing — do not fix.** Every full `pytest tests/` run shows `coroutine 'AsyncMockMixin._execute_mock_call' was never awaited` from `test_invalid_json_stdout_returns_failure_recoverable`. Pre-dates Brief #2. Leave it.
- **Adding a type tag to `config/tags.yaml` breaks two count tests.** `tests/test_core/test_tags.py` has `test_tags_yaml_is_valid_and_has_nine_types` and `test_load_taxonomy_returns_correct_taxonomy` — both assert `len(allowed_types) == 9` (currently). Grep for the count integer and update when adding types. `SAMPLE_TAXONOMY` in the same file is a minimal logic-test fixture — do NOT update it when adding tags.
- **Using `@property` instead of Pydantic `Field` for user-configurable values.** Rule: `Field` = things a human configures. `@property` = things the code computes from other fields.

### vault/ — sibling files
- **Sibling `.md` files: location AND naming.** Non-md capture creates sibling at `Projects/<A>/attachment/.summaries/<binary.name>.md` — suffix is `.md` appended to the FULL filename (e.g. `report.pdf.md`), NOT `<binary.stem>.md`. Use `_sibling_for(binary, vault_config)` from `vault/watcher.py` — never recompute inline. `documents.vault_path` for a sibling row = the sibling path; `metadata.attachment_path` = the binary path. See ADR-0007.

### vault/ — watcher internals
- **`VaultWatcher` / `_VaultEventHandler` constructors take `vault_config: VaultConfig`, not `attachment_path: Path`.** `_should_skip` uses `_is_in_managed_attachment(path, vault_config)` for non-.md files. CLI: `VaultWatcher(root=root, vault_config=CONFIG.main.vault, ...)`.
- **`documents.delete_by_path` and `documents.rename` return `Result[int]`.** The int is rowcount — check for 0 to detect "not in index".
- **`vault/watcher.py::on_deleted` and `on_moved` run binary sync BEFORE `_should_skip`.** Binary delete/move in `Projects/<A>/attachment/` MUST fire `_handle_binary_delete` / `_handle_binary_move` so the sibling DB row + audit log stay consistent. `_should_skip` only filters the user callback (indexer), not the internal sync. Reordering breaks sibling cleanup silently. (TD-030 fix)
- **Watcher handles binary delete/rename sync internally.** `on_deleted` / `on_moved` call `_handle_binary_delete` / `_handle_binary_move`. Sync uses unique debounce key prefix `bin:` to avoid colliding with user callbacks. Binary move INTO managed attachment dir is NOT skipped — we need to orphan the old sibling.
- **Vault-relative paths in watcher computed from `self._root`, not `CONFIG`.** Use `unicodedata.normalize("NFC", str(path.relative_to(self._root).as_posix()))` — the `to_vault_path` helper uses CONFIG singleton which breaks in tests.
- **Two `_debounce` calls with same key cancel each other.** The second call overwrites the first timer. Use unique keys when debouncing multiple handlers for the same path.
- **`write_note` sets `updated_by_human` from `actor`, not from incoming metadata.** `_merge_metadata` computes `updated_by_human=(actor == "human")` — any `updated_by_human=True` on the incoming `NoteMetadata` is ignored when `actor="ai"`. Tests that need `updated_by_human=True` on disk must call `write_note(..., actor="human")`.

### vault/ — paths and routing
- **`vault/paths.py` has TWO near-twin predicates.** `_is_in_managed_attachment(path, cfg)` (in `vault/paths.py`, not `vault/indexer.py`) — True only under `Projects/<A>/attachment/` or `Domain/<D>/attachment/`. Used by watcher `_should_skip`, indexer Rule 1, reconcile Stages 2+3. `_is_managed_summaries_area(path, cfg)` — True under any `attachment/` subtree OR under `inbox/`. Used by reconcile Stage 4. Picking the wrong one is silent.
- **`VaultConfig.no_edit_extensions` controls binary placement; `_should_skip` also blocks AI-output folders.** `no_edit_extensions` (pdf, png, jpg, jpeg, gif, webp) → `attachment/` hidden; everything else non-md → project/domain root visible. Use `resolve_placement()` from `vault/paths.py` — never decide placement inline. `Briefings/`, `Synthesis/`, `Documentation/` are capture-excluded — adding content there requires `vault/writer.py`, not the capture pipeline.
- **`vault/move_guard.py` suppresses watcher re-home for pipeline moves.** Pipeline calls `get_active().register(path)` before moving. Watcher checks registry before re-homing. Thread-safe via `threading.Lock`. Without this, watcher sees the move as "misplaced file" and moves it back.
- **`is_batch_subfolder()` in `vault/paths.py` must unpack `_location_context()` as a tuple.** `_location_context(path, vault_cfg)` returns `tuple[str | None, str | None]` — e.g. `("project", "Alpha")`, `("inbox", None)`, or `(None, None)`. Never treat the return value as a single string. When implementing `is_batch_subfolder`, always unpack: `loc_type, loc_name = _location_context(path, vault_cfg)`. A tuple in a boolean context is always truthy — silent bug.

### General patterns
- **Standard `logging` module does not support keyword arguments.** Use `%s`-style: `_log.warning("msg key=%s", value)` not `_log.warning("msg", key=value)`. Structlog supports kwargs; `logging.getLogger(__name__)` does not.
- **Any code writing into `.summaries/` MUST set `type=attachment-summary` in frontmatter.** Missing → reconcile Stage 4 skips it silently. Phase 2 Classify must preserve it. See ADR-0008.

### Phase 2 specific
- **CLUELESS marker body is a one-line placeholder string, not `""`.** `pipelines/capture.py::_store_nonmd` CLUELESS path writes `_Pending classification — binary at: <vp>_` + handoff note. Phase 2 Classify overwrites the body when resolving the marker.


### Hook-enforced — no longer needed here
The following were moved out of active guidance because hooks in `.claude/settings.json` now block or warn on them automatically:
- **`CONFIG` module-scope import in tests** — hook blocks `^from core.config import CONFIG` (unindented) in `tests/**/*.py`
- **Removed VaultConfig APIs** (`.attachment_path`, `.archive_path`) — hook blocks any `.py` file accessing these; use `project_attachment()`/`domain_attachment()`/`domain_archive()` from `vault/paths.py`
- **Patching `vault.writer.<name>` in tests** — hook warns; patch `vault.watcher.<name>` instead (TD-033)

## Constraint Index
<!-- guardrail-check skill writes here when new constraint groups are added -->
- [Write Safety](CONSTRAINTS.md#write-safety) (3 rules) — vault-only writes, updated_by_human gate, write_note merge rule
- [DB Integrity](CONSTRAINTS.md#db-integrity) (2 rules) — FK pragma, migration-only schema changes
- [LLM & Providers](CONSTRAINTS.md#llm--providers) (4 rules) — factory dispatch, config thresholds, prompt YAML only, provider fields
- [Async & CLI](CONSTRAINTS.md#async--cli) (2 rules) — asyncio.run pattern, load_dotenv placement
- [Architecture](CONSTRAINTS.md#architecture) (5 rules) — Result returns, audit log, MCP logic-free, MCP pre-req, scheduler order
- [Testing](CONSTRAINTS.md#testing) (1 rule) — CONFIG import scope