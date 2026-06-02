# Discussions: Pipeline

---

## Phase 1 — Implementation (core/pipeline.py)
_Completed: 2026-05-14_

### Exchanges

**Q:** What does `await` do in the stage loop — doesn't it just block the whole call stack?

```python
result = await stage(current_value, context)
```

**A:** `await` blocks the current coroutine (`run_pipeline`) but not the event loop. When the stage hits its own I/O wait (e.g. an LLM API call), it suspends and hands control back to the event loop, which can run other coroutines. The call stack for *this pipeline run* waits, but the thread isn't frozen.

Without `await`, `stage(...)` returns a coroutine object immediately — the function body never executes, and `result` is `<coroutine object>` instead of `Success`/`Failure`.

_Key concept: Python async/await — coroutine suspension vs thread blocking_

---

**Q:** If the event loop has nothing else queued during a single CLI run, does `await` provide any benefit at all?

**A:** No, not in Phase 0–3 single-CLI-run context. The event loop suspends `run_pipeline`, finds nothing else to schedule, and immediately resumes it. Net benefit is zero.

The value appears in Phase 4 (MCP daemon) where multiple client requests run concurrently. While pipeline A awaits the LLM network round-trip, the event loop can execute pipeline B's CPU-bound stage:

```
With async (Phase 4):
  Pipeline A: [stage1]→[await LLM]...........[stage2]→done
  Pipeline B:           [stage1]→[await LLM]...[stage2]→done

Without async:
  Pipeline A: [stage1]→[await LLM]→[stage2]→done
  Pipeline B:                                [stage1]→...
```

The async skeleton is infrastructure investment — retrofitting sync→async later would touch every call site. The plan acknowledges this explicitly (OQ-P3: concurrent runs deferred to Phase 4).

_Key concept: async as infrastructure vs immediate value — when concurrency pays off_
