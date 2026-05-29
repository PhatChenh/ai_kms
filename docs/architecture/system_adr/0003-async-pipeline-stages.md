# run_pipeline is async; stages are async def top-level functions

`run_pipeline` is an `async def`. Every stage must be a top-level `async def` with a meaningful `__name__`. Bound methods and lambdas must be wrapped.

**Status:** accepted

**Considered Options**

- Sync runner — simpler now, requires full rewrite when Phase 4 MCP daemon needs concurrency.
- `asyncio.gather` for parallel stages — rejected: breaks sequential dependency between stages.

**Consequences**

- Phase 1 CLI wraps each Click command with `asyncio.run(_async_fn())` — no `click-anyio` dependency.
- Phase 4 must address concurrent-run contextvars bleed (OQ-P3 / Q-004).
- While pipeline A awaits an LLM call, the event loop can serve pipeline B's CPU-bound stage — the async skeleton is free infrastructure.
