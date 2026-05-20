# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# AI-kms

AI-enhanced knowledge management system for busy managers. Watches an Obsidian vault, processes drops (notes, PDFs, emails, YouTube, web articles), summarises and classifies them, and surfaces patterns back to the user via a daily briefing and MCP server.

---

## Project context

**Target user:** a non-technical executive. Zero organisational effort is the baseline assumption. The AI does the work; the human does the judgment.

**Key constraint:** Hard delivery deadline 30 June 2026. Current phase is 0 (foundations). Three milestones:
- M1 ~15 May — Capture + Classify + Search end-to-end
- M2 ~30 May — MCP MVP live for boss demo
- M3 30 June — Full feature set (Promotion, Documentation, Self-learning, Briefing)

**Reference docs** (read before changing architecture):
- `docs/roadmap.md` — phase-by-phase build order and rules of the road
- `docs/top-level_layout.md` — every folder explained, pattern-to-folder mapping
- `STATE.md` — current position, architecture decisions, open questions, and technical debt; read at session start
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

## Repository layout

```
AI-kms/
├── config/          ← tunable behavior ONLY (thresholds, routing, providers)
├── prompts/         ← all AI prompts as YAML — edit here, never in code
├── llm/             ← provider abstraction + prompt loader
├── core/            ← shared primitives: result, audit, confidence, pipeline, config, logging
├── handlers/        ← one class per input type; self-register at startup
├── pipelines/       ← one file per roadmap feature; pure-function stages
├── vault/           ← ALL Obsidian filesystem I/O; nothing else touches the vault directly
├── storage/         ← SQLite state (audit log, embeddings, document index)
├── retrieval/       ← keyword + semantic + hybrid + hot/warm/cold tiers
├── briefings/       ← reads audit_log, writes to Vault/Briefings/
├── mcp_server/      ← thin wrappers over pipelines; no logic here
├── cli/             ← Click commands; each command just calls a pipeline
├── scheduler/       ← cron-like runner; jobs.yaml defines triggers
└── tests/           ← mirrors source layout; fixtures/ for test vault files
```

**Vault layout** (the Obsidian folder, separate from the repo):
```
Vault/
├── inbox/           ← single drop zone
├── Projects/        ← active work; AI manages entirely
├── Domain/          ← durable knowledge; AI and user co-author
├── Documentation/   ← one living page per active project
├── Briefings/       ← daily AI reports
├── Synthesis/       ← weekly AI journals
└── Archive/         ← auto-archived; invisible to user
```

---

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

**Overall:** Phase 0 of 8 — targeting M1 (Capture + Classify + Search) by ~15 May 2026. See `docs/roadmap.md` for the full phase breakdown.

**Phase 0 checklist:**
- [x] core/exceptions.py
- [x] core/result.py
- [x] core/logging_setup.py
- [x] core/config.py
- [ ] core/pipeline.py
- [x] storage/schema.sql
- [x] storage/migrations/
- [x] storage/db.py
- [x] storage/audit_log.py
- [x] core/audit.py
- [ ] llm/ + prompts/
- [ ] vault/
- [x] smoke test

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

- **Skipping `Result` type on helper functions.** Every public function in `handlers/` and `pipelines/` must return `Success` or `Failure`, not raw values or `None`.
- **Using `@property` instead of Pydantic `Field` for user-configurable values.** Rule: `Field` = things a human configures. `@property` = things the code computes from other fields.
- **Importing `CONFIG` at module scope in tests.** `CONFIG` validates vault root at import time; tests on machines without the vault fail immediately. Pass explicit paths (e.g. `db_path=tmp_path / "kb.db"`) to bypass CONFIG, or lazy-import inside the function under test.
