# Research: Pipeline
_Last updated: 2026-05-13_

## Overview

`core/pipeline.py` is the **sequential stage runner** that every roadmap feature (capture, classify, search, briefing, etc.) will use. It accepts a list of async stage callables, threads the output of each stage as the input to the next, halts on the first `Failure`, and wraps the whole run in a fresh `correlation_id`. It is a pure orchestrator — no AI calls, no vault writes, no domain logic of its own.

---

## Key Components

All of these are to be created in `core/pipeline.py`. Nothing related to this module currently exists on disk.

### `PipelineContext` (dataclass)
Carries the two cross-cutting concerns every stage needs without global imports:
- `config: Config` — the full validated config singleton (access thresholds, db path, provider settings)
- `correlation_id: str` — the UUID generated at run start (for logging + audit grouping)
- `db_path: Path | None` — optional override for the SQLite path; `None` = use `config.main.database.path`. Required for tests (pass `tmp_path / "kb.db"`)

### `Stage` (Protocol)
```python
class Stage(Protocol):
    async def __call__(self, input: Any, context: PipelineContext) -> Result[Any]: ...
```
Two-argument async callable. `input` is the output of the previous stage (or `initial_input` for stage 0). `context` carries config + correlation_id. The Protocol definition enforces the signature without restricting the type of data flowing through.

### `run_pipeline(name, stages, initial_input, context?) -> Result[Any]`
The only public function. Flow:
1. Call `new_correlation_id()` (clears old contextvars, binds fresh UUID)
2. Build `PipelineContext` with the correlation_id + CONFIG
3. Iterate stages; pass `(current_value, context)` to each
4. On `Success`: carry `result.value` forward
5. On `Failure`: log the stage name + error via structlog; return the `Failure` immediately (no further stages run)
6. After all stages succeed: return `Success(final_value)`

---

## How It Works

### Stage threading (the core loop)

```python
current = initial_input
for stage in stages:
    logger.debug("stage_start", pipeline=name, stage=stage.__name__, correlation_id=context.correlation_id)
    result = await stage(current, context)
    match result:
        case Success(value=v):
            current = v
            logger.debug("stage_ok", pipeline=name, stage=stage.__name__)
        case Failure() as f:
            logger.error("stage_failed", pipeline=name, stage=stage.__name__, **f.to_log_dict())
            return f
return Success(current)
```

Key detail: `stage.__name__` is used for log lines. Every stage callable must have a meaningful `__name__` (functions do by default; lambdas would log as `<lambda>` — avoid lambdas as stages).

### Correlation ID lifecycle

`new_correlation_id()` from `core/logging_setup.py`:
- Calls `clear_contextvars()` first (prevents bleed from previous run)
- Generates `uuid.uuid4()`
- Calls `bind_contextvars(correlation_id=...)` — every subsequent structlog call in this async context inherits it
- Returns the string

`run_pipeline` stores this string in `PipelineContext.correlation_id` so stages can access it explicitly (e.g. to include in an `AuditEntry`). Both paths (contextvars + explicit context field) point to the same value.

### Audit entries

The pipeline runner does **not** write audit entries itself. That is each stage's responsibility per CLAUDE.md rule 6: "Every pipeline stage that makes an AI decision must call `audit.write(...)`." Non-AI stages (extract, store, format) do not write audit entries. Only stages that call the LLM and receive an `AIDecision` must write to the audit log.

For the "done when" test (trivial stages like `add_one`, `double`, `to_string`), the test stages themselves call `audit.write()` with a synthetic `AIDecision` to prove correlation_id consistency. The runner only logs to structlog at DEBUG level.

### Context as second arg (not folded into input)

Keeps stage signatures clean and data types unambiguous:
```python
# Clean — what the stage receives vs. what infrastructure it uses are distinct
async def classify(note: RawContent, ctx: PipelineContext) -> Result[ClassifiedNote]: ...

# Bad — would require all input types to carry context, polluting domain types
async def classify(note_with_context: tuple[RawContent, PipelineContext]) -> Result[...]: ...
```

---

## Edge Cases & Silent Failure Modes

### 1. Correlation ID bleed in async concurrency
`new_correlation_id()` calls `clear_contextvars()` globally. If two pipeline runs overlap in the same event loop, the second call to `run_pipeline` clears the first run's correlation_id from contextvars while it's still running stages. For Phase 0–2 (single-user CLI, sequential runs) this is not a problem. MCP Phase 4 (concurrent tool calls in a long-running daemon) will need scoped contextvars or per-run copies. Flag as open question.

### 2. Stages are async — caller must be async
`run_pipeline` must be `async def` and awaited. CLI commands calling it need an `asyncio.run()` wrapper (or Click's async support). The top-level `cli/main.py` entry point is currently sync — it will need a runner wrapper.

### 3. Stage name in logs
Stages are identified by `stage.__name__`. If a stage is defined as a method (e.g. `handler.extract`), `stage.__name__` returns `"extract"` which may not be unique across handlers. A longer key like `f"{type(stage).__name__}.{stage.__name__}"` may be better. Defer decision to implementation.

### 4. `PipelineContext.config` triggers vault validation
`Config` (from `core/config.py`) validates `vault.root` existence at construction time. Tests that construct `PipelineContext(config=CONFIG, ...)` will fail on machines without the vault. Two mitigations:
- Pass a `MockConfig` or patched `Config` in tests
- OR accept that pipeline tests are `@pytest.mark.smoke` and skip them on CI without vault

Tests for trivial arithmetic stages can avoid constructing a real `Config` by creating a minimal stub. The test should NOT import `CONFIG` at module scope (per CLAUDE.md warning).

### 5. Empty `stages` list
`run_pipeline` with zero stages should return `Success(initial_input)` unchanged. This is a valid degenerate case and should work naturally from the loop.

### 6. Stage raises an exception instead of returning `Failure`
Stages are required to catch exceptions and return `Failure`, not raise. But `run_pipeline` should defensively `try/except` each stage call and wrap unexpected exceptions in a `Failure(recoverable=False)` rather than letting them propagate and crash the caller. This makes the runner robust to stages that don't follow the contract.

### 7. `db_path` on `PipelineContext` vs stage-local override
Stages that write to the audit log call `audit.write(..., db_path=context.db_path)`. If `context.db_path` is `None`, they fall through to `CONFIG.main.database.path` (the default in `storage/db.py`). This works only if CONFIG is available. For tests: always pass an explicit `db_path` in `PipelineContext`.

---

## Dependencies & Coupling

### What `core/pipeline.py` imports
- `core/result.py` — `Success`, `Failure`, `Result`
- `core/logging_setup.py` — `new_correlation_id()`
- `core/config.py` — `Config` (type annotation only; construction happens in `cli/main.py`)
- `structlog` — for stage entry/exit logging
- `asyncio` — for `async def` (stdlib)
- `pathlib.Path` — for `db_path` field
- `typing.Protocol`, `typing.Any`, `dataclasses.dataclass` — structural types

### What does NOT import `core/pipeline.py` yet
- `handlers/` — will import to run the capture pipeline
- `pipelines/capture.py`, `pipelines/classify.py` — each defines stages, calls `run_pipeline`
- `cli/main.py` — wraps `run_pipeline` with `asyncio.run()`
- `mcp_server/tools.py` — thin wrappers that call pipeline functions (post Phase 4)

### What `core/pipeline.py` must NOT import
- `storage/audit_log.py` or `core/audit.py` — audit is the stage's job, not the runner's
- `llm/` — the runner is provider-agnostic
- `vault/` — no vault I/O in the runner
- Any handler — handlers are consumers of the runner, not dependencies

---

## Open Questions

| # | Question | Blocks | Decision needed by |
|---|---|---|---|
| OQ-P1 | Should `run_pipeline` defensively catch exceptions from stages? Or trust stages to return `Failure`? | Implementation | Before writing tests |
| OQ-P2 | Stage identification in logs: `stage.__name__` vs. a user-supplied `name` parameter on the `Stage` protocol? If stages are bound methods, `__name__` may be ambiguous. | Log clarity | Implementation |
| OQ-P3 | Concurrent pipeline runs in Phase 4 (MCP daemon): will `new_correlation_id()` / `clear_contextvars()` bleed across concurrent runs? | Phase 4 MCP server | Phase 4 planning |
| OQ-P4 | How do CLI entry points call async `run_pipeline`? `asyncio.run()`? Click's `@click.command` with async support? Must be decided before Phase 1 CLI wiring. | Phase 1 CLI | Phase 1 |
| OQ-P5 | Should `PipelineContext` hold the full `Config` object or just the sub-models a pipeline needs? Full Config is simpler now; per-pipeline sub-configs reduce test setup burden. | Test ergonomics | Can defer to implementation |

---

## Reference Project Patterns

The reference project (`docs/reference/knowledge-base-server/src/`) is JavaScript and has no equivalent to a pipeline runner. It uses flat async functions (`processNewClippings`, `classifyNote`) that bundle multiple concerns in one function body — the exact anti-pattern CLAUDE.md rule 1 forbids.

Key divergences from the reference that are intentional:
- **Reference bundles stages** (classify + write frontmatter in `processor.js` line 59–78). We split into separate pure functions.
- **Reference uses hardcoded prompt strings** (`CLASSIFY_PROMPT` in `classifier.js` line 6–27). We load from YAML.
- **Reference has no Result type** — it returns `{ success: true/false }` ad hoc. We use typed `Success | Failure`.
- **Reference has no correlation_id** — no way to group log lines from one run.

Nothing from the reference is worth adopting for the pipeline runner specifically. The reference confirms our approach is more robust.

---

## Technical Debt Spotted

- `core/exceptions.py` already defines `PipelineError(KMSError)`. This can be used when the runner itself fails (e.g. stage raises unexpectedly after the defensive catch). Should map to `Failure(error=str(exc), recoverable=False)` — not re-raise `PipelineError`.
- `core/logging_setup.py` comment warns about contextvars bleeding across async tasks. The comment is correct but the fix (task-scoped contextvars) is not yet implemented. Low risk for Phase 0–3; medium risk for Phase 4.
- `cli/main.py` is not yet written. When it is, it needs `asyncio.run()` to call the async pipeline runner. If Click's `@click.command` is used with async functions, it needs `click.anyio` or a simple `asyncio.run()` wrapper in each command body.
