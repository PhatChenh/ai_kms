# Plan: Pipeline
_Last updated: 2026-05-14_
_Status: [x] done_

## Approach

Build `core/pipeline.py` as a single file with three exports: `PipelineContext` (dataclass), `Stage` (Protocol), and `run_pipeline` (async function). The runner is purely an orchestrator — no AI calls, no vault writes, no domain logic. All AI decisions and audit writes belong to individual stages. Two phases: implementation first, then the "done-when" test suite.

---

## Open Question Resolutions (from research OQ-P1 through OQ-P5)

**OQ-P1 — Defensive exception catching**: Yes. `run_pipeline` wraps each `await stage(...)` in `try/except BaseException` and returns `Failure(error=str(exc), recoverable=False, context={...})` if a stage raises instead of returning `Failure`. Stages are responsible for returning `Failure`, but the runner never lets an uncaught exception propagate to the caller.

**OQ-P2 — Stage name in logs**: Use `stage.__name__`. Stages must be top-level `async def` functions with meaningful names. Bound methods and lambdas must not be used as stages directly — wrap them in a named async function if needed.

**OQ-P3 — Concurrent runs / contextvars bleed**: Deferred to Phase 4 (MCP daemon). Single-user CLI runs sequentially; no issue for Phase 0–3.

**OQ-P4 — CLI calling async `run_pipeline`**: `asyncio.run()` in each Click command body. Pattern:
```python
@click.command()
def capture(file): asyncio.run(_async_capture(file))
```
No new dependencies (no `click-anyio`). Documented here so Phase 1 CLI wiring uses this pattern.

**OQ-P5 — `PipelineContext.config` type**: Full `Config` object. Tests that don't touch the vault pass `config=MagicMock()` — no vault needed for trivial-stage tests. Tests that need real config are marked `@pytest.mark.smoke`.

---

## Phases

### Phase 1 — Implementation (`core/pipeline.py`)

**Goal**: Ship the complete `core/pipeline.py` — context dataclass, Stage protocol, and async runner — ready for all downstream pipeline files to import.

**Steps**:
1. Create `core/pipeline.py` with module docstring explaining the three exports and their usage.
2. Define `PipelineContext` as a `@dataclass`:
   - `config: Config` — full validated config (type annotation only; not imported at module scope — use `TYPE_CHECKING` guard)
   - `correlation_id: str` — UUID set by `run_pipeline` at start of each run
   - `db_path: Path | None = None` — optional SQLite path override for tests; `None` = each stage reads from `context.config.main.database.path`
3. Define `Stage` as a `typing.Protocol`:
   ```python
   class Stage(Protocol):
       async def __call__(self, input: Any, context: PipelineContext) -> Result[Any]: ...
   ```
   Note: Python does not enforce Protocol signatures at runtime. The definition is for type-checker enforcement and documentation only.
4. Implement `run_pipeline`:
   ```python
   async def run_pipeline(
       name: str,
       stages: list[Stage],
       initial_input: Any,
       context: PipelineContext | None = None,
   ) -> Result[Any]:
   ```
   - If `context` is None: call `new_correlation_id()` to generate a fresh UUID (this also clears contextvars and binds the new ID), then build `PipelineContext(config=CONFIG, correlation_id=cid)`. If `context` is provided (e.g. from tests), call `bind_contextvars(correlation_id=context.correlation_id)` to ensure it's in structlog context — do NOT call `new_correlation_id()` (that would overwrite the caller's ID).
   - Loop over `stages`:
     - `logger.debug("stage_start", pipeline=name, stage=stage.__name__)`
     - `try: result = await stage(current_value, context)`
     - `except BaseException as exc: logger.error("stage_exception", pipeline=name, stage=stage.__name__, error=str(exc)); return Failure(error=str(exc), recoverable=False, context={"pipeline": name, "stage": stage.__name__})`
     - `match result: case Success(value=v): current_value = v; logger.debug("stage_ok", ...)`
     - `case Failure() as f: logger.error("stage_failed", pipeline=name, stage=stage.__name__, **f.to_log_dict()); return f`
   - Return `Success(current_value)` after all stages complete.
5. Add `__all__ = ["PipelineContext", "Stage", "run_pipeline"]`.

**Files to modify**:
- `core/pipeline.py` — create (new file)

**Test criteria**:
- [ ] `from core.pipeline import PipelineContext, Stage, run_pipeline` imports without error
- [ ] `core/pipeline.py` imports no module from `llm/`, `vault/`, `handlers/`, `storage/`, or `core/audit.py` (importable without those modules present)
- [ ] `run_pipeline` is an async function (verify with `asyncio.iscoroutinefunction`)

**Status**: [x] done

**Completed**: 2026-05-14
**Notes**: Created `core/pipeline.py` with `PipelineContext`, `Stage` (Protocol), and `run_pipeline` (async). No deviations. Three phase-1 criteria tests pass. Pre-existing test failures in `test_result_type.py` (stdin at module scope) and `test_logging.py` (missing fixture) are unrelated.

---

### Phase 2 — Tests (`tests/test_core/test_pipeline.py`)

**Goal**: Verify the "done-when" criteria: three trivial async stages run end-to-end; failure in stage 2 halts stage 3; every stage writes an audit entry; all entries share the same `correlation_id`.

**Steps**:
1. Create `tests/test_core/test_pipeline.py`.
2. Define three trivial stages that call `audit.write()` with a synthetic `AIDecision` before returning their result. Each stage must use the `db_path` from `context.db_path` when calling `audit.write()`. Example:
   ```python
   async def add_one(value: int, ctx: PipelineContext) -> Result[int]:
       decision = AIDecision(action="add_one", confidence=1.0, reasoning="test", source_ids=[])
       audit.write(decision, pipeline="test", stage="add_one", outcome="AUTO", db_path=ctx.db_path)
       return Success(value + 1)
   ```
3. **Test: happy path end-to-end**
   - `add_one → double → to_string` on input `1` → final value `"4"` (1+1=2, 2*2=4, str(4)="4")
   - Assert result is `Success` with `.value == "4"`
4. **Test: failure halts pipeline**
   - Stage 1 succeeds. Stage 2 returns `Failure(error="boom", recoverable=False, context={})`. Stage 3 must NOT run (use a sentinel flag to verify).
   - Assert result is `Failure` with `.error == "boom"`.
5. **Test: same `correlation_id` on all audit entries**
   - Run `add_one → double → to_string` with an explicit `PipelineContext(config=MagicMock(), correlation_id="test-cid-123", db_path=db)`
   - Query audit log: `audit_log.query(correlation_id="test-cid-123", db_path=db)`
   - Assert 3 entries returned, all with `correlation_id == "test-cid-123"`, stage names are `"add_one"`, `"double"`, `"to_string"` in order.
6. **Test: empty stages list**
   - `run_pipeline("empty", [], 42, context=ctx)` → `Success(42)`
7. **Test: stage that raises instead of returning `Failure`**
   - Define a stage that raises `RuntimeError("unexpected")` instead of returning `Failure`.
   - Assert `run_pipeline` returns `Failure` (not raises), `.recoverable == False`, `.error` contains `"unexpected"`.
8. **Test: `run_pipeline` without explicit context generates a fresh correlation_id**
   - Call `run_pipeline("test", [add_one], 0)` without passing `context`.
   - Assert result is `Success`.
   - Assert structlog contextvars contain a non-empty `correlation_id` after the call.

**Fixtures needed**:
- `db` fixture (reuse pattern from `tests/test_core/test_audit.py`): `tmp_path / "kb.db"` + `init_db(path)`.
- `ctx` fixture: `PipelineContext(config=MagicMock(), correlation_id="test-cid-123", db_path=<db fixture>)`.

**Files to modify**:
- `tests/test_core/test_pipeline.py` — create (new file)

**Test criteria**:
- [ ] `uv run pytest tests/test_core/test_pipeline.py -v` — all tests pass
- [ ] `uv run pytest tests/test_core/test_pipeline.py -m "not smoke" -v` — all tests pass (none should be smoke-marked)
- [ ] `uv run pytest tests/ -m "not smoke"` — full suite green (no regressions)

**Status**: [x] done

**Completed**: 2026-05-14
**Notes**: All 6 behavioral tests pass. One surprise: `test_no_explicit_context_generates_correlation_id` can't use real CONFIG (vault root missing on this machine). Fixed by seeding `core.config._CONFIG` with MagicMock via monkeypatch — bypasses `load_config()` without mocking the import itself. This pattern matches the guidance in CLAUDE.md ("pass explicit paths to bypass CONFIG"). No smoke marker needed.

---

## Open Questions

| # | Question | Status |
|---|---|---|
| OQ-P3 | Concurrent `run_pipeline` calls in Phase 4 MCP daemon — `clear_contextvars()` in `new_correlation_id()` will bleed across concurrent runs. Needs scoped contextvars or per-run copies. | 🔴 Deferred to Phase 4 planning |

---

## Out of Scope

- `cli/main.py` integration — CLI is a Phase 1 deliverable. Phase 0 pipeline is wired to tests only.
- `pipelines/capture.py`, `pipelines/classify.py` — these consume `run_pipeline`; built in Phases 1 and 2.
- `handlers/` — consumers of the pipeline runner; built in Phase 1.
- MCP server wiring — Phase 4.
- Async concurrency fix for context bleed — Phase 4.
- `asyncio.run()` wrapper in Click commands — documented here (OQ-P4), implemented in Phase 1 CLI.
