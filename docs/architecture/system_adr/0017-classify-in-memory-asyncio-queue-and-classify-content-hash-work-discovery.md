# Classify triggers off an in-process asyncio queue and discovers its own work from a `classify_content_hash` column — no DB queue table, no external broker

_Created: 2026-06-14_

Phase 8 redesigns classify so that, after a file is captured, the system asynchronously extracts structured knowledge facts from it ("Anthony leads Movie Q2"). That redesign needs two pieces of plumbing: a way to **trigger** a classify run when capture finishes, and a way to **discover** which documents are unclassified or have changed since they were last classified. **Decision: trigger via a single in-process `asyncio.Queue` with one sequential consumer in the same container as capture (NOT a DB queue table, NOT an external broker); discover work via a new `classify_content_hash` column on `documents` (NULL = never classified, `!= content_hash` = changed, `== content_hash` = done). A startup catch-up scan re-derives the pending work-list from the hash, so the in-memory queue losing its state on restart is non-fatal.**

**Status:** accepted

## Context

- Phase 8 Slice A builds only the plumbing — it deliberately makes **no LLM calls** (fact extraction is Slice B). So both the trigger mechanism and the work-discovery mechanism must be testable with no AI, no network, and no cost.
- The cloud-native rearchitecture already fixed the deployment shape: classify runs **in-process, in the same container as capture**, one document at a time — there is no separate worker process and no second container (rearch §7; Phase 6 grill §2).
- Two failure modes the plumbing must make impossible: a document classified twice (wasted LLM cost), or a document silently skipped forever (missing knowledge). Both are properties of how work is triggered and discovered.
- Concurrency on shared entities is the underlying hazard. Multiple classify runs touching the same `knowledge_entries` rows race; a single sequential consumer removes that hazard by construction.
- The target deployment is a single-tenant, single-container personal vault. Any mechanism that assumes multiple workers, cross-container scaling, or a message-broker tier is paying ops/infra weight the deployment never spends.

## Decision

1. **Trigger — one in-process `asyncio.Queue`, one sequential consumer.** A single consumer coroutine drains the queue with an `await queue.get()` loop, processing one document id at a time. Sequential by construction: never two classify runs at once, so no race on shared `knowledge_entries` rows.
   - The queue + consumer + catch-up scan are started from `mcp_server/cloud_entry.py::build_app` (or a small `start_classify_worker()` it calls), under uvicorn's existing event loop. This is **not** a Click command and **not** wrapped in `asyncio.run` — CLI async rules (C-10/C-11) do not apply.
2. **Work discovery — a `classify_content_hash` column on `documents`, not a flag and not a queue table.** A document is unclassified work when its marker is empty or no longer matches its content fingerprint.
   - `documents.classify_content_hash` (TEXT, nullable) added in migration 010. States: `NULL` = never classified; `!= content_hash` = content changed since last classify; `== content_hash` = up to date. Work-discovery query: `SELECT id FROM documents WHERE classify_content_hash IS NULL OR classify_content_hash != content_hash`. Lives as a new function in the classify-infra module, reading via `storage.documents` / `get_connection`.
3. **Durability lives in the hash, not the queue.** A startup catch-up scan runs the work-discovery query and `put`s each id onto the queue. Because the durable `classify_content_hash` marker (not the queue) is the system of record for "what still needs classifying," an in-memory queue that loses its in-flight backlog on a container restart loses nothing permanently — the next boot's scan re-discovers it.
4. **Stamp on success, leave empty on failure.** A successful classify sets `classify_content_hash = content_hash` for that document (skip it next scan). A failed or partial run leaves the marker as-is (empty / stale), so the next catch-up scan re-queues it. This is the retry mechanism — no dead-letter queue is needed.

## Considered options

- **In-memory `asyncio.Queue` + `classify_content_hash` work record (chosen).** Smallest surface; runs in the process and event loop capture already uses; crash-safe **because** the durable hash plus the startup scan reconstruct the work-list. The only durable state is one column the migration adds anyway.
- **DB-backed durable queue table.** A `classify_queue` table persisting pending ids across restarts. Rejected: the grill (§1) explicitly rejects a queue/flag table — `classify_content_hash` + the catch-up scan already give durable, restart-safe work discovery, so a queue table would duplicate that durability with extra schema, extra writes, and a second source of "what's pending" that can drift from the hash.
- **External message broker (e.g. Redis / SQS / RabbitMQ).** Rejected: introduces an entire infra + ops tier (a service to deploy, secure, monitor, and pay for) for a single-container, single-user deployment that processes one document at a time. It would also reintroduce the multi-consumer concurrency on shared entities that the single sequential consumer exists to prevent. Unjustified weight for the target deployment.

## Consequences

- **The classify worker MUST start at container build/startup, NOT inside the MCP lifespan.** CLAUDE.md records that the MCP lifespan fires per-chat-session, not at uvicorn boot — if the worker were hooked there, classification would not run until a human first connected a chat client, and capture would pile up unclassified. The worker is a background housekeeper: it must start with the container. The exact hook in `build_app` / uvicorn startup is flagged for research (OQ-P8A-01). This is the single most important wiring constraint of this ADR.
- **No cross-container scaling of classify.** A single in-process consumer cannot be horizontally scaled to a second container. Accepted: the deployment is single-container and single-user, and sequential processing is a deliberate correctness choice (no races on shared `knowledge_entries`), not just a simplification.
- **Partial-failure retry relies on leaving `classify_content_hash` empty.** There is no dead-letter queue and no retry counter in Slice A — a document that fails (or partially fails) classify simply keeps its empty/stale marker and is re-queued by the next catch-up scan. A document that fails repeatedly will be retried on every boot until it succeeds or is fixed; bounding that is a later concern.
- **In-memory queue loses its in-flight backlog on restart — and that is fine.** Nothing is permanently lost; the catch-up scan rebuilds the backlog from the durable markers on every startup. The cost is re-discovery work at boot, not data loss.
- **Capture gains a push seam, wired in Slice B.** Capture already emits a "ready to classify" log line (`pipelines/capture.py`) at the right place; Slice A leaves it as a documented seam, and Slice B replaces it with the actual `queue.put(doc_id)`. (The current stub logs `vault_path`; the queue needs `doc_id` — flagged for Slice B.)
- **A large catch-up backlog floods the queue at startup.** A vault with thousands of unclassified documents enqueues them all at once. Slice A has no LLM cost so this is bounded, but whether the scan should page/batch its `put`s is left open (OQ-P8A-03).
