# Capture pushes freshly-classified work onto the live queue via the app's shared state, and the entry writer dedups exact-entity twins at write time

_Created: 2026-06-15_

Phase 8 Slice A built an in-memory `asyncio.Queue` fed only by a startup catch-up scan, leaving a documented seam in capture. **Decision (two coupled choices): (1) wire capture to enqueue a document id the moment capture finishes, by reaching the single queue via the running app's shared state object (`app.state`), keeping the startup catch-up scan as the restart safety net (D1); (2) before inserting a brand-new fact, the entry writer does a direct exact-match lookup (same dimension + entity + tag, non-retired) and folds into the existing fact instead of creating a twin (D6) — a deterministic backstop behind the prompt-level dedup, because the prompt only ever sees the top-N existing facts and a duplicate ranked out of that window is otherwise invisible.**

**Status:** accepted

## Context

- ADR-0017 made the queue local to the composed lifespan in `mcp_server/cloud_entry.py` — capture has no handle to it. The capture seam (`pipelines/capture.py`, text and binary branches) only writes a "ready to classify" log line. Catch-up-only means a file dropped while the container runs never classifies until the next reboot — broken for a live system.
- The document id capture needs to enqueue is already in hand at the seam (`row_id` returned by the upload store is the document id).
- Capture runs both cloud-side (under uvicorn, where the queue exists) and potentially in contexts with no queue (local CLI, tests). The enqueue must degrade gracefully when no queue is present.
- Prompt-level dedup (broad grill) relies on the AI seeing existing facts and not duplicating them — but the context loader feeds only the top-N ranked facts per dimension (a recency/quality window, capped by config). A duplicate that is old, low-trust, or simply beyond the cap is invisible to the AI, so it would spawn a silent twin. The top-N is not an existence check.
- An exact-entity lookup is cheap and deterministic — `query_by_entity` already exists. Fuzzy / name-variant dedup ("Anthony" vs "Anthony Nguyen") is a different, harder problem (entity resolution) explicitly deferred by the rearchitecture (open question #4); this ADR does not attempt it and adds no unique index.

## Decision

1. **Live enqueue via `app.state` (D1).** At app startup, the composed lifespan stores the single `asyncio.Queue` on the Starlette app's shared `app.state` (e.g. `app.state.classify_queue`). The web upload handler — which already has the request, and through it the app — reaches that queue after capture returns the document id and calls `queue.put_nowait(doc_id)`. When no queue is present (CLI, tests, text-only local runs), the enqueue is skipped silently; the startup catch-up scan remains the durable net.
2. **Catch-up scan stays (D1).** The startup scan is unchanged — it covers anything captured while the worker was down and anything the live push missed.
3. **Exact-entity write-time dedup backstop (D6).** Before inserting a `new` fact, the entry writer queries for an existing non-retired entry with the same dimension + entity + tag. If found, it folds the new fact into that entry (treated as an update — accumulates the source id, re-gates status) and logs the fold, instead of inserting a twin. Scope: exact-entity only. No unique DB index is added.

## Considered options

### Queue-handle mechanism (choice 1)

- **`app.state` shared object (chosen).** The queue is created in the same lifespan that owns `app`; storing it on `app.state` is the framework-blessed place for per-app singletons. The upload handler already receives the request (hence the app), so no new parameter threading and no global. Degrades cleanly (handler checks for the attribute).
- **Module-level singleton variable.** A module holding the one queue, imported by capture. Rejected: a module-global is harder to isolate in tests (leaks across test cases unless explicitly reset), and it hides the queue's true owner (the app lifespan) behind import side-effects. `app.state` ties the queue's lifetime to the app's lifetime, which is correct.
- **Dependency injection (pass the queue down every call).** Threading the queue from lifespan → upload handler → capture → store as an explicit parameter. Rejected: capture's signature would gain a queue parameter that 90% of callers (CLI, tests, reconcile) pass as None — speculative plumbing through layers that don't care, for one caller that does. `app.state` confines the coupling to the one handler that needs it.

### Dedup strategy (choice 2)

- **Exact-entity write-time backstop (chosen).** Deterministic, cheap, reuses an existing query, catches the invisible-duplicate case the prompt window misses. Bounded scope keeps it from straying into entity resolution.
- **Unique DB index on (dimension, entity, tag).** Rejected: too rigid — it would hard-fail on legitimate near-duplicates the system intends to keep as separate pending facts, and it cannot express the "fold and accumulate source" behavior. The broad grill explicitly chose no unique index.
- **Prompt-level dedup only (no backstop).** Rejected: silent duplicate drift whenever a true duplicate sits outside the top-N window the AI sees.

## Consequences

- **Capture ↔ worker are now coupled through a shared queue handle.** This is the seam ADR-0017 anticipated. The coupling is confined to `app.state` + the upload handler; capture's core functions stay queue-agnostic (they return the document id; the handler enqueues).
- **The enqueue is best-effort and non-blocking.** `put_nowait` on an unbounded in-memory queue does not block capture; if the queue is absent the push is skipped and the catch-up scan recovers the document. Capture never fails because classify is unavailable.
- **The dedup backstop reads before every new-fact insert.** One extra indexed lookup per new fact. Cheap and local, but it means a "new" action can resolve to an update — callers and audit counts must treat a folded new fact as an update, not an insert.
- **Fuzzy duplicates still accumulate.** "Anthony" and "Anthony Nguyen" remain separate entries until entity resolution ships. Accepted and explicitly out of scope.
- **No new schema for either choice.** The queue is in-memory (ADR-0017); the dedup uses existing tables and queries.
