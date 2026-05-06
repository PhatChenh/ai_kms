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
- `docs/phase0_guide.md` — Phase 0 checklist and smoke-test

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
if confidence > 0.85:   # hardcoded threshold
```

**4. Handler Registry — new source = new file only**
```python
# Good — drop a new file, register, done
@HandlerRegistry.register
class YouTubeHandler(BaseHandler):
    def can_handle(self, source: str) -> bool: ...
    def extract(self, source: str) -> Result[RawContent]: ...

# Bad — adding a new elif to a central dispatch function
```

**5. Prompts as Config — never hardcode prompts**
```python
# Good
prompt = prompt_loader.get("summarize")   # from prompts/summarize.yaml

# Bad
prompt = f"Summarise this note: {content}"
```

**6. Audit Trail — every AI decision is logged**
Every pipeline stage that makes an AI decision must call `audit.write(...)` with: timestamp, source note IDs, decision, confidence, reasoning, outcome. No exceptions.

**7. Idempotent Writes — upsert only; never overwrite human edits**
```python
# Good — vault/writer.py checks updated_by_human before every write
writer.upsert(note)   # safe to call twice

# Bad
vault_path.write_text(content)   # bypasses the guard
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

## Commands

```bash
# Setup
uv sync                        # install dependencies
uv run python -m pytest tests/ # run test suite

# CLI (after install)
kms capture <file>             # run capture pipeline on a single file
kms classify <file>            # run classify pipeline
kms search "<query>"           # semantic + keyword search, hot tier first
kms briefing                   # generate today's briefing

# Dev helpers
uv run python test.py          # quick smoke test (write → parse → upsert → audit)
```

---

## Critical rules — do not violate

- **`vault/writer.py` is the only entry point for vault writes.** Never call `open()` or `pathlib.write_text()` against vault files anywhere else.
- **Never add an MCP tool before its pipeline exists and is tested.** A stub tool that calls nothing is a lie.
- **Never hardcode a confidence threshold.** All thresholds live in `config/thresholds.yaml` and are loaded via `core/config.py`.
- **Never write a prompt string in Python code.** All prompts live in `prompts/*.yaml`, loaded once at startup by `llm/prompt_loader.py`.
- **`updated_by_human = true` means hands off.** If a frontmatter field carries this flag, do not propose overwriting it. Surface a conflict instead.
- **MCP tools contain zero logic.** If you find yourself writing conditional branches inside `mcp_server/tools.py`, stop — move it to the pipeline.
- **Audit log is non-negotiable from Phase 1.** Phase 8 (Daily Briefing) reads from it. No audit log means no briefing.
- **Schedulers come last.** Build manual CLI first, then automate.

---

## Phase 0 smoke test (must pass before any other phase)

```bash
uv run python test.py
```
Checks: write a markdown file with frontmatter → parse → upsert to SQLite → write audit row. All green = Phase 0 done.

---

## What Claude gets wrong in this codebase

- **Putting thresholds in pipeline code.** Always move them to `config/thresholds.yaml`.
- **Writing logic inside `mcp_server/tools.py`.** Tools are thin; logic belongs in pipelines.
- **Calling `pathlib` directly on vault files** instead of going through `vault/writer.py`.
- **Skipping `Result` type on helper functions.** Every public function in `handlers/` and `pipelines/` must return `Success` or `Failure`, not raw values or `None`.
- **Using `@property` instead of Pydantic `Field` for user-configurable values.** Rule: `Field` = things a human configures. `@property` = things the code computes from other fields.
