# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# AI-kms

AI-enhanced knowledge management system for busy managers. Watches an Obsidian vault, processes drops (notes, PDFs, emails, YouTube, web articles), summarises and classifies them, and surfaces patterns back to the user via a daily briefing and MCP server.

---

## Project context

**Target user:** a non-technical executive. Zero organisational effort is the baseline assumption. The AI does the work; the human does the judgment.

**Key constraint:** Hard delivery deadline 30 June 2026. Current phase: Brief #2 complete, Brief #3 Phase 1 done (attachment_sync_and_archive). Next: Phase 2 (Classify pipeline) + Brief #3 Phase 2. Three milestones:
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

_Skill output folders (where skills read/write):_
- `docs/design/` — output of `/codebase-design-analysis`; input to `/writing-detailed-specs`
- `docs/specs/` — output of `/writing-detailed-specs`; input to `/research` and `/plan-from-specs`
- `docs/research/` — output of `/research`; consumed by `/plan-from-specs`
- `docs/plan/` — output of `/plan-from-specs`; executed by `/tdd-implement`
- `docs/discussions/` — output of `/capture_discussion_v2`; historical design rationale
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
├── CONTEXT.md       ← context file with key concepts & domain language
├── CLAUDE.md        ← behavioral contract:rules, conventions, preferences
├── STATE.md         ← current implementation progress
├── CONSTRAINTS.md   ← constraints that need to be respected or the system will break. Check these contraints when designing new features or making changes
├── TECH_DEBT.md     ← deferred tasks that need to revisit and clear out in time
├── OPEN_QUESTIONS.md  ← unresolved decisions
└── docs/
    ├── architecture/
    │   ├── system_adr/              ← system-wide ADRs
    │   ├── system_diagram.md        ← context diagram of whole system
    │   ├── architecture_diagram.md  ← container diagram
    │   └── phase0_foundations/      ← one folder per container/phase
    │       ├── _OVERVIEW.md         ← component map for this phase
    │       └── adr/                 ← domain-specific ADRs
    ├── design/       ← design docs, contains output from /codebase-design-analysis, and required input for /writing-detailed-specs
    ├── specs/        ← specs docs, contains specs written by /writing-detailed-specs, and to be verified by /research
    ├── research/     ← research docs
    ├── plan/         ← plan docs
    └── roadmap/
        └── design_artifacts/ ← designs produced while making roadmap
└── src/                      ← source codes
```

## Repository layout

```
AI-kms/
├── src/
│   ├── config/          ← tunable behavior ONLY (thresholds, routing, providers)
│   ├── prompts/         ← all AI prompts as YAML — edit here, never in code
│   ├── llm/             ← provider abstraction + prompt loader
│   ├── core/            ← shared primitives: result, audit, confidence, pipeline, config, logging
│   ├── handlers/        ← one class per input type; self-register at startup
│   ├── pipelines/       ← one file per roadmap feature; pure-function stages
│   ├── vault/           ← ALL Obsidian filesystem I/O; nothing else touches the vault directly
│   ├── storage/         ← SQLite state (audit log, embeddings, document index)
│   └── cli/             ← Click commands; each command just calls a pipeline
├── tests/               ← mirrors src/ layout; fixtures/ for test vault files
├── data/                ← runtime data (SQLite db, embeddings)
└── logs/                ← runtime logs
```

**Vault layout** (the Obsidian folder, separate from the repo):
```
Vault/
├── inbox/                ← single drop zone
├── Projects/
│   └── <A>/
│       ├── CLAUDE.md           ← human-facing index (TD-015, out of scope)
│       ├── <user notes>.md
│       └── attachment/         ← per-project binaries (no global attachment/)
│           ├── report.pdf
│           └── .summaries/     ← hidden from Obsidian; sibling .md files indexed here
│               └── report.md   ← vault_path for this row; attachment_path frontmatter → binary
├── Domain/
│   └── <D>/
│       ├── attachment/         ← per-domain binaries, same structure
│       └── Archive/            ← archived projects under this domain
├── Documentation/        ← one living page per active project
├── Briefings/            ← daily AI reports
├── Synthesis/            ← weekly AI journals
└── (no global Archive/ or attachment/ — both are now per-Domain/Project)
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

**Overall:** Phase 1 of 8 complete + Brief #2/#3 done + Phase 1.5 Pay-Debt complete & code-review clean (commit b41caf1, 2026-06-03). Next: Phase 2 — Classify pipeline.

(Phase 0 + Phase 1 checklists closed — see STATE.md for full history.)

**Brief #3 — attachment_sync_and_archive** _(complete 2026-05-24)_:
- [x] Phase 1: watcher VaultConfig signature (TD-023), .summaries/ skip (TD-AS-1), false-success logging
- [x] Phase 2: domain_archive helper, archive_path property removal
- [x] Phase 3: _is_binary, _sibling_for, on_delete/on_move sync callbacks
- [x] Phase 4: kms reconcile — 4-stage reconcile command (paths, orphan binaries, stale binaries, orphan siblings)

**Phase 1.5 Pay-Debt** _(complete + code-review clean 2026-06-03; see STATE.md)_:
- [x] All 7 phases (FILE_LOST guard, location tags, stale-tag reconcile, folder capture, handlers extension, idempotent capture, stale-batch-ref reconcile)
- [x] Code-review pass: 2 critical (batch_id wiring, folder timer race) + 4 important + 2 minor fixed; 787 tests pass. Branch `fix/phase1.5-codereview` NOT pushed.
- Deferred: rename gate rework (TD-029), Claude CLI short-extract JSON fix (TD-028), binary-modify re-capture (TD-037), drop scalar `domain:` field (TD-038)

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

- **`RuntimeWarning` in `test_claude_cli_provider.py` is pre-existing — do not fix.** Every full `pytest tests/` run shows `coroutine 'AsyncMockMixin._execute_mock_call' was never awaited` from `test_invalid_json_stdout_returns_failure_recoverable`. Pre-dates Brief #2. Leave it.
- **Adding a type tag to `config/tags.yaml` breaks two count tests.** `tests/test_core/test_tags.py` has `test_tags_yaml_is_valid_and_has_nine_types` and `test_load_taxonomy_returns_correct_taxonomy` — both assert `len(allowed_types) == 9` (currently). Grep for the count integer and update when adding types. `SAMPLE_TAXONOMY` in the same file is a minimal logic-test fixture — do NOT update it when adding tags.
- **Skipping `Result` type on helper functions.** Every public function in `handlers/` and `pipelines/` must return `Success` or `Failure`, not raw values or `None`.
- **Using `@property` instead of Pydantic `Field` for user-configurable values.** Rule: `Field` = things a human configures. `@property` = things the code computes from other fields.
- **Importing `CONFIG` at module scope in tests.** `CONFIG` validates vault root at import time; tests on machines without the vault fail immediately. Pass explicit paths (e.g. `db_path=tmp_path / "kb.db"`) to bypass CONFIG, or lazy-import inside the function under test.
- **`CONFIG.main.vault.attachment_path` no longer exists.** Use `vault/paths.py::project_attachment(name)` or `domain_attachment(name)`. Property removed in Phase 1.5. Using it raises `AttributeError` at runtime.
- **`VaultConfig.archive_path` @property removed.** Use `domain_archive(name, vault_config)` from `vault/paths.py`. `archive_dir: str = "Archive"` Field kept for the helper. Property pointed to global `Vault/Archive/` which no longer exists.
- **Sibling `.md` files live at `.summaries/`, not next to source.** Non-md capture creates sibling at `Projects/<A>/attachment/.summaries/<binary.name>.md` (e.g. `report.pdf.md` — see next bullet for naming). `documents.vault_path` for a sibling row = the sibling path. `metadata.attachment_path` = the binary path. All `# COUPLING:` markers retired (Brief #2 + Brief #3 Phase 1).
- **`VaultWatcher` / `_VaultEventHandler` constructors take `vault_config: VaultConfig`, not `attachment_path: Path`.** Changed in Brief #3 Phase 1 (TD-023). `_should_skip` uses `_is_in_managed_attachment(path, vault_config)` for non-.md files. CLI: `VaultWatcher(root=root, vault_config=CONFIG.main.vault, ...)`.
- **`_is_in_managed_attachment` lives in `vault/paths.py`.** Moved from `vault/indexer.py` in Brief #3 Phase 1. Import from `vault.paths`, not `vault.indexer`.
- **`scan_capture` modified loop skips `.summaries/` paths.** Prevents re-capturing sibling .md files which would wipe `attachment_path` from frontmatter (TD-AS-1).
- **`documents.delete_by_path` and `documents.rename` return `Result[int]`.** The int is rowcount — check for 0 to detect "not in index" (false-success logging fix in Brief #3 Phase 1).
- **Watcher handles binary delete/rename sync internally.** `_VaultEventHandler.on_deleted` and `on_moved` call `_handle_binary_delete` / `_handle_binary_move` for non-.md files. Sync uses unique debounce key prefix `bin:` to avoid colliding with user callbacks. Binary move into managed attachment dir is NOT skipped — we need to orphan the old sibling.
- **Vault-relative paths in watcher computed from `self._root`, not `CONFIG`.** The `to_vault_path` helper uses CONFIG singleton which breaks in tests. Use `unicodedata.normalize("NFC", str(path.relative_to(self._root).as_posix()))` instead.
- **Standard `logging` module does not support keyword arguments.** Use `%s`-style formatting: `_log.warning("msg key=%s", value)` not `_log.warning("msg", key=value)`. Structlog supports kwargs but `logging.getLogger(__name__)` does not.
- **Two `_debounce` calls with same key cancel each other.** The second call overwrites the first timer. Use unique keys when debouncing multiple handlers for the same path.
- **`write_note` sets `updated_by_human` from `actor`, not from incoming metadata.** `_merge_metadata` computes `updated_by_human=(actor == "human")` — any `updated_by_human=True` on the incoming `NoteMetadata` is ignored when `actor="ai"`. Tests that need `updated_by_human=True` on disk must call `write_note(..., actor="human")`.
- **Sibling marker filename = `<binary.name>.md`, NOT `<binary.stem>.md`.** E.g. `report.pdf` → `.summaries/report.pdf.md`. Prevents collision when `report.pdf` and `report.docx` share an inbox. Use `_sibling_for(binary, vault_config)` from `vault/watcher.py` — don't recompute the path inline. (DECISION-028 / Brief #4)
- **`vault/paths.py` has TWO near-twin predicates.** `_is_in_managed_attachment(path, cfg)` — True only under `Projects/<A>/attachment/` or `Domain/<D>/attachment/`. Used by watcher `_should_skip`, indexer Rule 1, reconcile Stages 2+3 (binary-pipeline area). `_is_managed_summaries_area(path, cfg)` — True under any `attachment/` subtree OR under `inbox/`. Used by reconcile Stage 4 (where `.summaries/` siblings live). Picking the wrong one is silent.
- **Monkeypatching `vault.watcher` collaborators: target `vault.watcher.<name>`, NOT the source module.** `watcher.py` imports `move_note`, `write_note`, `delete_by_path`, `rename as rename_doc`, `read_note`, `audit_write`, `AIDecision`, `Failure`, `Success` at module top (Brief #4). Patching `vault.writer.move_note` updates the source module attribute but leaves `vault.watcher.move_note` pointing at the original. Same pattern for any module that does `from X import Y` at top level — patch the importing module, not X. (TD-033)
- **Any code writing into `.summaries/` MUST set `type=attachment-summary` in frontmatter.** `reconcile_orphan_siblings` (Stage 4) requires both scope (`_is_managed_summaries_area`) and type guard before unlinking. Missing `type` → reconcile leaves the sibling alone (intended defense against user-placed `.md`). Phase 2 Classify must preserve the type when resolving CLUELESS markers. (DECISION-029)
- **CLUELESS marker body is a one-line placeholder string, not `""`.** `pipelines/capture.py::_store_nonmd` CLUELESS path writes `_Pending classification — binary at: <vp>_` + handoff note. Phase 2 Classify is expected to overwrite the body when resolving the marker.
- **`vault/watcher.py::on_deleted` and `on_moved` run binary sync BEFORE `_should_skip`.** Binary delete/move in `Projects/<A>/attachment/` MUST fire `_handle_binary_delete` / `_handle_binary_move` so the sibling DB row + audit log stay consistent. `_should_skip` only filters the user callback (indexer), not the internal sync. If you reorder these handlers and `_should_skip` runs first, you silently break sibling cleanup for the headline Brief #3 scenario. (TD-030 fix)


## Constraint Index
<!-- guardrail-check skill writes here when new constraint groups are added -->
- [Write Safety](CONSTRAINTS.md#write-safety) (3 rules) — vault-only writes, updated_by_human gate, write_note merge rule
- [DB Integrity](CONSTRAINTS.md#db-integrity) (2 rules) — FK pragma, migration-only schema changes
- [LLM & Providers](CONSTRAINTS.md#llm--providers) (4 rules) — factory dispatch, config thresholds, prompt YAML only, provider fields
- [Async & CLI](CONSTRAINTS.md#async--cli) (2 rules) — asyncio.run pattern, load_dotenv placement
- [Architecture](CONSTRAINTS.md#architecture) (5 rules) — Result returns, audit log, MCP logic-free, MCP pre-req, scheduler order
- [Testing](CONSTRAINTS.md#testing) (1 rule) — CONFIG import scope