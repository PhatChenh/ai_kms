# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# AI-kms

AI-enhanced knowledge management system for busy managers. Watches an Obsidian vault, processes drops (notes, PDFs, emails, YouTube, web articles), summarises and classifies them, and surfaces patterns back to the user via a daily briefing and MCP server.

---

## Project context

**Target user:** a non-technical executive. Zero organisational effort is the baseline assumption. The AI does the work; the human does the judgment.

**Key constraint:** Hard delivery deadline 30 June 2026. Current phase: Cloud-native rearchitecture — P5 Slice 1 (Data/Config Foundation) complete (1275 tests, merged to cloud-native). Three milestones:
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
- `docs/2_specs/` — output of `/writing-detailed-specs`; input to `/research` and `/plan-from-specs`
- `docs/3_research/` — output of `/research`; consumed by `/plan-from-specs`
- `docs/4_plans/` — output of `/plan-from-specs`; executed by `/tdd-implement`
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
│   ├── 2_specs/              ← output of /writing-detailed-specs
│   ├── 3_research/          ← output of /research
│   ├── 4_plans/              ← output of /plan-from-specs
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
│   │   └── migrations/  ← numbered .sql migration files (001–007; no new migration in Session B)
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
kms search "<query>"           # semantic + keyword search (stub — Session B replaces)
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

**Overall:** Phase 1 of 8 complete + Brief #2/#3 done + Phase 1.5 Pay-Debt complete + Phase Pre-2 complete + Vault-Restructure complete (2026-06-04, 956 tests). TD-042 deprecated-key strip complete (2026-06-07, 959 tests). P2-CL classify() pure function implemented (2026-06-08). **P2-CIC Classify Inline in Capture — all 9 phases COMPLETE (2026-06-08, 1080 tests, merged to main).** **TD-040/TD-041 Batch-ID Fix COMPLETE (2026-06-09).** **P3 Session A Index Layer COMPLETE (2026-06-10, 1147 tests).** **P3 Session B Query Path COMPLETE (2026-06-11, ~1370 tests, merged to main). Phase 3 (Search) COMPLETE. M1 milestone (Capture + Classify + Search end-to-end) ACHIEVED.** **Phase 4 (MCP Server) — ALL 7 PHASES COMPLETE (2026-06-12, 1258 tests, merged to main).** **P5 Slice 1 (Data/Config Foundation) — COMPLETE (2026-06-13, 1275 tests, merged to cloud-native).** **P5 Slice 2 (Deployment Foundation / AgentBase) — COMPLETE (2026-06-13, 450+ tests, Docker verified).** Slice 2 makes cloud code deployable: `upsert_from_upload` save-or-update routine, `VAULT_ROOT` config binding, REST handlers + secret-key gate + `/health` in `mcp_server/api.py`, cloud entry point (`mcp_server/cloud_entry.py`), explicit uvicorn dependency, `scripts/start.sh` startup wrapper, `Dockerfile` + `litestream.yml` template. Docker container builds, runs on port 8080 with all endpoints verified. Behavior IDs `P5-DEPLOY-01…12` (12 entries, all `active`). **Next: Phase 6 (Daemon).**

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
- **Every new migration breaks the previous migration's version-pin test.** `tests/test_storage/test_migration_<N>.py` asserts `schema_version == N` after `init_db`. Adding migration `N+1` bumps the version, so those assertions fail on a normal `pytest` run (no skip marker). When you add a migration, bump the prior version-pin assertions to the new number — this is the expected update, NOT a regression. (Hit during P5 Slice 1: migration 008 → bump `test_migration_007.py:41,56` `7`→`8`.)
- **Using `@property` instead of Pydantic `Field` for user-configurable values.** Rule: `Field` = things a human configures. `@property` = things the code computes from other fields.

### vault/ — sibling files
- **Sibling `.md` files: location AND naming.** Non-md capture creates sibling at `Projects/<A>/attachment/.summaries/<binary.name>.md` — suffix is `.md` appended to the FULL filename (e.g. `report.pdf.md`), NOT `<binary.stem>.md`. Use `_sibling_for(binary, vault_config)` from `vault/watcher.py` — never recompute inline. `documents.vault_path` for a sibling row = the sibling path; `metadata.attachment_path` = the binary path. See ADR-0007.

### vault/ — watcher internals
- **`VaultWatcher` / `_VaultEventHandler` constructors take `vault_config: VaultConfig`, not `attachment_path: Path`.** `_should_skip` uses `_is_in_managed_attachment(path, vault_config)` for non-.md files. CLI: `VaultWatcher(root=root, vault_config=CONFIG.main.vault, ...)`.
- **`documents.delete_by_path` and `documents.rename` return `Result[int]`.** The int is rowcount — check for 0 to detect "not in index".
- **`vault/watcher.py::on_deleted` and `on_moved` run binary sync BEFORE `_should_skip`, using unique `bin:` debounce key prefix.** Binary delete/move in `Projects/<A>/attachment/` fires `_handle_binary_delete` / `_handle_binary_move` BEFORE `_should_skip` — `_should_skip` only filters the user callback (indexer), not the internal sync. Sync uses `bin:` key prefix to avoid colliding with user callbacks. Binary move INTO managed attachment dir is NOT skipped (needed to orphan the old sibling). Reordering breaks sibling cleanup silently. (TD-030 fix)
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
- **CLUELESS binary marker now gets real summary + `status: needs-review`.** P2-CIC Phase 7 replaced the old `_Pending classification — binary at: <vp>_` placeholder with a real AI-summarized body, `source_hash` for idempotent re-entry, and `status: needs-review`. The old `pending-routing` concept is fully retired. `_store_nonmd` CLUELESS branch now calls `summarize_attachment` to produce the sibling body.

### Phase 2 — classify pipeline gotchas
- **`core/pipeline.py` cannot import from `vault.`** — `tests/test_core/test_pipeline_phase1.py::test_pipeline_has_no_heavy_imports` greps source for `vault.` and fails on any match. PipelineContext fields typed with vault types must use `Any` instead of the actual type, even under `TYPE_CHECKING`. The test checks raw source text, not runtime imports.
- **Parallel subagent dispatch can absorb uncommitted changes.** If two subagents work on same repo simultaneously, one's `git commit -a` can pick up the other's uncommitted changes. Always commit each subagent's work immediately, or use worktree isolation per subagent.
- **Audit writes must happen AFTER the physical action succeeds, not before.** `_write_classify_audit(...)` called pre-move fires even when the move fails — failure is logged as `AUTO` in the audit log. Pattern: call `_write_classify_audit(..., "AUTO")` only after `move_note()` + `documents.replace_path()` both succeed; call `_write_classify_audit(..., "SUGGEST"/"CLUELESS")` in each fallback branch. Tests asserting `AUTO` outcome without a real file on disk silently no-op the move but fire the pre-move audit — masking the bug. Applies to `_classify_auto_md_move()` in `capture.py`. (P2-CIC review fix #1, 2026-06-08)

### Phase 3 — search index layer gotchas
- **`replace_path` cleans old search entries but does NOT create new ones.** The capture pipeline's best-effort indexing creates search entries for the new path. If someone adds `INSERT INTO embeddings_vec` or `INSERT INTO notes_fts` inside `replace_path`, the note gets duplicate search entries (one from `replace_path`, one from capture indexing). The asymmetry with `rename` (which DOES copy search entries) is intentional — `rename` moves an existing indexed note; `replace_path` creates a fresh row whose indexing is handled downstream.
- **Search-table cleanup must be inside the same `with get_connection` as the documents operation.** `delete_by_path`, `rename`, and `replace_path` all use single-transaction cleanup. Adding search-table SQL outside the `with` block (or in a separate connection) breaks atomicity — search entries can survive while documents rows are deleted, or vice versa.


### Phase 3 — search query path gotchas (Session B, implemented)
- **`embeddings_vec` filtered KNN requires `MATCH + k + IN (...)` form.** The no-`MATCH` form (`WHERE vault_path IN (...) ORDER BY distance`) executes but returns ALL NULL distances — silent correctness trap. Always use `embedding MATCH ? AND k = ? AND vault_path IN (...)`. Verified on sqlite-vec v0.1.9. See ADR-0009.
- **`notes_fts` body column is index 3 (0-based).** `snippet(notes_fts, 3, ...)` targets body. Column order: `vault_path UNINDEXED, title, summary, body`.
- **Bare-query embedding gives better separation than wrapped.** Do NOT wrap the query in `_build_context_text()` — research proved bare query has better match/distractor separation (0.266 vs 0.133).
- **`created_at` is NOT uniformly full-width.** It can be date-only (`YYYY-MM-DD`) from `meta.created`. Date filters must use `updated_at` (always `YYYY-MM-DD HH:MM:SS` from `datetime('now')`).
- **`_build_vault_context` returns `tuple[str, frozenset, frozenset]`, not `str`.** Phase 7 (TD-051) changed the return type. Callers must unpack: `text, project_names, domain_names = _build_vault_context(cfg)`. Tests that called this directly must be updated.
- **Registry-failure fallback must return `None, None`, not empty frozensets.** In `_build_vault_context` fallback path, returning `frozenset(), frozenset()` routes `classify()` into typed-set validation with no allowed names — silently rejects all AI assignments. Return `None, None` to trigger backward-compat pooled-set validation.
- **`filter_paths()` `None` sentinel ≠ empty list.** `Success(None)` means "no filters applied, search everything." `Success([])` means "filters applied, nothing matched." Mixing them up causes global search to return zero results or filtered search to scan everything.
- **`title` field reroutes from `extra` to typed `NoteMetadata.title`.** `"title"` is now in `_KNOWN_KEYS`. Notes written before Session B have `title` in `extra`; new captures have it as a typed field. `_derive_title()` prefers `metadata.title`, falls back to `extra["title"]`, then `Path.stem`.

### Phase 4 — MCP (planned, not yet implemented) gotchas
- **`move_note` carries NO metadata — relocating a note AND changing its project/domain is a 3-call sequence, not one.** `move_note(src, dst, actor="ai")` re-reads `src` from disk and merges only that; it does NOT accept incoming metadata, and it blocks human-locked AI moves. To update the label: capture `old_vault_path = to_vault_path(src)` BEFORE the move → `move_note(src, dst)` → `outcome = write_note(dst, new_meta, actor="ai")` (pure writer, C-03: build `new_meta` from `read_note(src)` with project/domain overridden — it carries every field to keep) → `replace_path(old_vault_path, outcome)`. **`replace_path`'s 2nd arg is the `WriteOutcome` from `write_note`, NOT a path** (`documents.py:232`; proven `capture.py:986-987`). Passing `dst` fails at runtime → silent index/disk divergence. (P4 research A7/A7b; `kms_move`.)

### Phase 5 — cloud deployment (built P5 Slice 2, 2026-06-13) gotchas
- **`VAULT_ROOT` must be injected BEFORE `MainConfig` construction — post-construction tricks fail silently.** The `KMS_DB_PATH` pattern (assign after `Config(...)` build) does NOT work for vault root because `validate_vault_root_exists` is a `MainConfig` `@model_validator(mode="after")` that fires AT CONSTRUCTION time. And `VaultConfig` has no `validate_assignment`. So the env override must inject into `raw_main["vault"]["root"]` between `keys = ApiKeys()` and `Config(main=MainConfig(**raw_main), ...)` (`config.py:589-591`). Verified against actual `load_config()` code.
- **`select_vault_by_env` overrides vault root when `env == "test"` — cloud env-files MUST set `env: prod`/`dev`.** If the config YAML has `env: test`, the validator redirects vault.root to `testing.vault_path`, bypassing the VAULT_ROOT injection. The Dockerfile's `config/config.yaml` must have `env: prod`.
- **Config and SQL files resolve relative to install path, not PYTHONPATH.** When the package is installed with `--no-editable` into site-packages, `_CONFIG_DIR` resolves to `site-packages/config/`, and `_SCHEMA_FILE` resolves to `site-packages/storage/schema.sql`. The Dockerfile must COPY config files AND schema.sql + migrations/ to the site-packages path, not just to `/app/`.
- **`mcp_server.server` module-level import triggers CONFIG validation.** Importing `mcp` from `server.py` at module scope immediately fires `load_config()`, which validates vault root. Any phase that imports `server.py` must have VAULT_ROOT properly set first.
- **`uvicorn.run(app, ..., factory=False)` is the correct call for a pre-built app.** If using `factory=True`, the parameter should be a string import path for the factory function, not the app object. The simpler approach: build the app inline, then `uvicorn.run(app, ...)`.
- **MCP lifespan fires per-chat-session, not at uvicorn boot.** The Context Injection Engine builds when a client first connects to the MCP path, not when uvicorn starts. Verifying the lifespan works requires a real MCP tool-list request, not just a `/health` curl.

### Hook-enforced — no longer needed here
The following were moved out of active guidance because hooks in `.claude/settings.json` now block or warn on them automatically:
- **`CONFIG` module-scope import in tests** — hook blocks `^from core.config import CONFIG` (unindented) in `tests/**/*.py`
- **Removed VaultConfig APIs** (`.attachment_path`, `.archive_path`) — hook blocks any `.py` file accessing these; use `project_attachment()`/`domain_attachment()`/`domain_archive()` from `vault/paths.py`
- **Patching `vault.writer.<name>` in tests** — hook warns; patch `vault.watcher.<name>` instead (TD-033)

## Constraint Index
<!-- guardrail-check skill writes here when new constraint groups are added -->
- [Write Safety](CONSTRAINTS.md#write-safety) (3 rules) — DB source of truth, updated_by_human gate, write_note scoped to retained consumers
- [DB Integrity](CONSTRAINTS.md#db-integrity) (2 rules) — FK pragma, migration-only schema changes
- [LLM & Providers](CONSTRAINTS.md#llm--providers) (4 rules) — factory dispatch, config thresholds, prompt YAML only, provider fields
- [Async & CLI](CONSTRAINTS.md#async--cli) (2 rules) — asyncio.run pattern, load_dotenv placement
- [Architecture](CONSTRAINTS.md#architecture) (5 rules) — Result returns, audit log, MCP logic-free, MCP pre-req, scheduler order
- [Testing](CONSTRAINTS.md#testing) (1 rule) — CONFIG import scope
- [Daemon Sync](CONSTRAINTS.md#daemon-sync) (1 rule) — cache advisory / cloud authority / cache-loss non-fatal / cache-on-ack