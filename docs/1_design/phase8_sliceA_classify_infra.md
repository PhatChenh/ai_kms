# Phase 8 Slice A — Classify Infrastructure (no LLM calls)

_Created: 2026-06-14_
_Status: Design — ready for `/writing-detailed-specs`_
_Tier: design-lite (precision read; requirements locked in `docs/0_draft/phase8/phase8_classify_redesign_grill.md`)_
_Behavior ID prefix: **P8-CLS-A** (P8-CLS-A-01 … P8-CLS-A-07 in `docs/system_behavior/behavior_inventory.yaml`)_
_Source of truth for direction: `docs/0_draft/cloud_native_rearchitecture.md` §7_

---

## In plain terms

Today the system can capture a file and summarize it, but it has no machinery to turn that file into structured "facts we know" — the part that reads a document and files away things like "Anthony leads Movie Q2." Phase 8 builds that machinery. **Slice A (this doc) builds only the plumbing**: a way to find which documents still need this treatment, a way to load the right inputs (the document's text, the list of knowledge categories, and the facts we already know), and a simple in-memory work queue that processes one document at a time. **It deliberately does NOT call the AI yet** — the actual fact-extraction is Slice B. The point of splitting it this way is that every piece of Slice A can be tested with no AI, no network, and no cost.

What could go wrong if we get the plumbing wrong: documents could be classified twice (wasted cost), or silently skipped forever (missing knowledge), or the inputs handed to the housekeeping AI (the Slice B extractor LLM — DeepSeek; not yet wired in Slice A) could be too big (token blow-ups) or stale. Slice A's job is to make those failure modes impossible before any AI is wired in.

---

## Cast of characters

| Name | Plain-English role |
|---|---|
| `knowledge_entries` table | The one shared table holding every structured fact (one fact per row) |
| `documents` table | The primary store for each captured file: its text, summary, and fingerprints |
| `content_hash` | A captured file's content fingerprint (already exists) |
| `classify_content_hash` | **New.** The fingerprint a document had when it was last classified — drives work discovery |
| dimension | A top-level knowledge category (people, projects, domains); fixed in config |
| `guidance` | **New.** User-written prompt text per dimension telling the AI what to look for |
| `trust_score` | **New.** User-validated quality axis on a fact (inert in P8); top ranking key |
| `dimensions.yaml` | The config file listing dimensions, their allowed tags, and (new) guidance |
| `core/tags.py` | Existing module that already loads + validates dimensions (Phase 5 Slice 1) |
| Content Reader | New step: picks full text vs summary by size |
| Context Loader | New step: loads + ranks + caps existing facts per dimension |
| work queue | New in-memory queue + single consumer loop that processes documents one at a time |
| consumer | The worker loop that drains the queue and prepares inputs — plain code, no AI |
| housekeeping AI | The Slice B extractor LLM (DeepSeek) that turns prepared inputs into facts; **not** wired in Slice A |

---

## Decision

**Build Slice A as four small additions wired into the existing cloud container, reusing the Phase 5 Slice 1 dimension loader rather than building a new one, and placing the queue + consumer + catch-up scan inside the existing container startup (`build_app`) so it shares capture's process and event loop.** This keeps the new surface area minimal, satisfies the rearchitecture's "in-process, same container as capture" decision, and lets every new function be a pure-or-DB-only unit testable without the LLM.

Concretely, the four additions are:
1. **Migration 010** — `documents.classify_content_hash`, `knowledge_entries.trust_score` (default 0.5) and `retrieval_count` (default 0), plus two indexes. (`storage/migrations/010_*.sql`.)
2. **Expanded `config/dimensions.yaml`** — richer tags + a `guidance` field per dimension; loaded through the existing `core/tags.py` loader, which is **extended** (not duplicated) to carry guidance.
3. **A new classify-infra module** holding the Content Reader, Context Loader/ranker, and the work-discovery query (`pipelines/classify.py` is gutted and rewritten in Slice B; Slice A adds these as new functions, keeping them out of the dead folder-routing code).
4. **A queue skeleton** (`asyncio.Queue` + single consumer coroutine + startup catch-up scan) started from `mcp_server/cloud_entry.py::build_app`, with the consumer body a skeleton that prepares inputs and stops before the (Slice B) AI call.

---

## Q1 Diagram — what happens inside

```
# Phase 8 Slice A — Classify Infrastructure: What Happens Inside
Scope: Shows how documents get found, queued, and prepared for classification.
       Does NOT show the AI extraction step itself (that is Slice B).

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

   Container starts up                  A new document
          │                             finishes capture
          ▼                                   │
 ┌──────────────────────┐                     │ (Slice B wires
 │ Catch-up scan: find  │                     │  this push;
 │ documents never      │                     │  noted seam in A)
 │ classified or        │                     │
 │ changed since last   │                     │
 └──────────┬───────────┘                     │
            │ document ids                     │ document id
            └─────────────┬────────────────────┘
                          ▼
              ┌────────────────────────┐
              │ Work queue (in memory, │
              │ same container)        │
              └───────────┬────────────┘
                          │ one id at a time
                          ▼
              ┌────────────────────────┐
              │ Single worker pulls    │
              │ one document, in order │
              │ (never two at once)    │
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │ Prepare inputs:        │
              │ • pick full text or    │
              │   summary by size      │
              │ • load dimensions +    │
              │   guidance             │
              │ • rank & cap existing  │
              │   facts per dimension  │
              └───────────┬────────────┘
                          │
                  Did everything
                  prepare AND
                  classify cleanly?
                          │
                 ┌────────┴────────┐
                YES                NO
                 │                 │
                 ▼                 ▼
        ┌────────────────┐  ┌────────────────┐
        │ Stamp document │  │ Leave unstamped│
        │ as classified  │  │ — retried on   │
        │ (skip next time)│ │ next scan      │
        └────────────────┘  └────────────────┘
```

```
Simplified: The three preparation steps (Content Reader, Dimension Config Loader,
            Context Loader) are grouped into one "Prepare inputs" box. In Slice A
            the worker body stops after preparing inputs — the AI extraction and
            the actual fact-writing that would precede the stamp are Slice B.
```

---

## Implications

- **Documents gain a "have I been classified yet?" marker, and classify finds its own work with one query — no separate queue table.** A document is work if its classify-marker is empty or no longer matches its content fingerprint.
  - `documents.classify_content_hash` (TEXT, nullable) added in migration 010. Work-discovery query: `SELECT id FROM documents WHERE classify_content_hash IS NULL OR classify_content_hash != content_hash`. Lives as a new function in the classify-infra module, reads via `storage.documents` / `get_connection`.

- **Knowledge facts get two new ranking signals that do nothing yet but unblock later phases.** Trust (user quality) and retrieval-count (demand) are stored now so later phases can populate them without another migration.
  - `knowledge_entries.trust_score REAL DEFAULT 0.5`, `knowledge_entries.retrieval_count INTEGER DEFAULT 0`. Both inert in P8. `trust_score` is the top key in the Context Loader's `ORDER BY`. `KnowledgeEntry` dataclass (`storage/knowledge_entries.py:16`) gains `trust_score` and `retrieval_count` fields, and `_row_to_entry` reads them — otherwise the ranker cannot sort by a column the dataclass does not carry.
    - `[UNVERIFIED: the exact INSERT in knowledge_entries.upsert() may need the two new columns added to its column list so future inserts get explicit defaults]` — verify in research; DB defaults cover it if the INSERT omits them.

- **The list of knowledge categories becomes richer and gains user-steerable instructions, but the way it is loaded barely changes.** The dimension config grows a `guidance` block per category; the existing loader is taught to carry that block through.
  - `config/dimensions.yaml` today is `dimension → [tags]` (a flat list). It becomes `dimension → {tags: [...], guidance: "..."}`. `core/tags.py::load_dimensions()` (line 147) and `validate_dimension_tag()` (line 159) must handle the nested shape. **This is the one backward-incompatible config-shape change in Slice A** — `validate_dimension_tag` currently does `rulebook[dimension]` and treats it as the tag list (line 178); after the change the tag list is `rulebook[dimension]["tags"]`.
    - Callers of `load_dimensions` / `validate_dimension_tag` must be found in research (grep showed `core/tags.py`, `storage/knowledge_entries.py` indirectly, tests). Phase 5 Slice 1 shipped a `test_dimensions.py` suite (P5-DATA-07/08) that pins the old flat shape — those tests will need updating, same cascade pattern as migrations.

- **The system can decide how much of a document to feed the housekeeping AI, by size.** Big documents fall back to their summary so the prompt never blows the token budget.
  - Content Reader: reads `documents.full_body` and `documents.summary` (both exist; `full_body` from Phase 7A). Rule: `len(full_body) // 4 < CONFIG.classify.max_content_tokens` → use `full_body`, else `summary`. The `// 4` is a chars→tokens estimate. The comparison is against a **config int**, not a confidence float — it does not trip the C-06 hook, but the threshold MUST come from config, never a literal.

- **Existing facts are loaded, ranked, and capped before they reach the housekeeping AI, so the prompt stays small and current.** Only confident + pending facts are loaded (retired ones are excluded), ranked by trust then confidence then recency, top-N per dimension.
  - Context Loader builds on `knowledge_entries.get_confident_and_pending(dimension=...)` (line 171) which already excludes retired. But that function sorts `ORDER BY dimension, entity, tag` and does not cap — Slice A needs a new ranked+capped query: `... WHERE status != 'retired' AND dimension = ? ORDER BY trust_score DESC, confidence DESC, updated_at DESC LIMIT ?`. Each returned entry carries its `id` (the dataclass already has it).
    - Decision: add a new ranked query function rather than overloading `get_confident_and_pending` with sort/limit params — keeps the existing 5-tool CRUD contract stable and the new ranking concern isolated.

- **Classification runs on its own schedule, one document at a time, inside the same container as capture.** A queue holds document ids; a single consumer drains it sequentially; a startup scan seeds the backlog.
  - `asyncio.Queue` + a consumer coroutine started in `build_app` (or a small `start_classify_worker()` it calls). Sequential by construction (single consumer, `await queue.get()` loop). Catch-up scan = run the work-discovery query at startup, `put` each id. This is **not** a Click command and **not** wrapped in `asyncio.run` — it lives under uvicorn's existing event loop, so C-10/C-11 (CLI async rules) do not apply.
    - `[UNVERIFIED: the MCP lifespan vs uvicorn-boot timing — CLAUDE.md notes MCP lifespan fires per-chat-session, not at uvicorn boot. The classify worker must start at app build/startup, NOT inside the MCP lifespan, or it won't run until a client connects. Verify in research where to hook the startup coroutine.]`

- **Capture's existing "ready to classify" log line is the queue-push seam — but Slice A only notes it.** Capture already emits a stub at the right place; Slice B replaces it with the actual `queue.put(doc_id)`.
  - `pipelines/capture.py:266` and `:482` — `logger.info("capture.classify_ready", vault_path=vault_path)`. Slice A leaves these as-is (documented seam). Wiring is Slice B (grill §"Wire to capture"). Note: the stub logs `vault_path`; the queue needs `doc_id` (the function has `row_id` in scope) — Slice B's concern, flagged here.

- **A successful classify stamps the marker; a failure leaves it empty so the document is retried.** This is the retry mechanism — no dead-letter queue needed in Slice A.
  - New function: set `classify_content_hash = content_hash` for a doc id, in `storage.documents`. In Slice A the skeleton consumer never reaches a "fully successful classify" (no AI), so the stamp function exists and is unit-tested but is not called on the happy path until Slice B. Partial-failure semantics (grill §10) are a Slice B concern.

- **Module-depth note: the gutted `classify.py` is the right home, not a new module.** Today `classify.py` is a deep-ish pure function (folder routing) that Slice B deletes. Adding the Slice A infra functions here (rather than a brand-new file) means the deletion test passes: removing the new functions would scatter the work-discovery/content-reader/ranker logic into the queue consumer and the API layer. They earn their keep as named, separately-tested units.
  - **Seam check:** the Content Reader and Context Loader each have exactly **one** caller (the consumer). They are NOT new public interfaces with multiple adapters — they are internal pipeline stages. Per the design lens, do not over-abstract them into protocols; plain functions returning `Result` are correct.

---

## Known tradeoffs

- **We changed the `dimensions.yaml` shape now (flat list → nested with guidance), which breaks the Phase 5 Slice 1 dimension tests.** We accept a known test-update cascade in exchange for shipping the guidance seam from day one (the grill's explicit decision §4). The alternative — a parallel `guidance.yaml` — would split one concept across two files and is rejected below.

- **The queue is in-memory, so a container restart loses any un-stamped backlog in flight.** We accept this because the catch-up scan rebuilds the backlog on every startup from the durable `classify_content_hash` markers — nothing is permanently lost, only re-discovered. The alternative (a durable DB-backed queue table) is more machinery than the grill's "no queue table" decision wants.

- **We add two columns (`trust_score`, `retrieval_count`) that do nothing in Phase 8.** We accept carrying inert columns to avoid a second migration in Phase 9/10. The cost is two columns that a reader might wonder about — documented here and in the migration comment.

- **The Content Reader's token estimate is `chars / 4`, a rough heuristic, not a real tokenizer.** We accept imprecision because the threshold is a coarse "is this document huge?" gate, not a billing-accurate count, and a real tokenizer would add a dependency for no decision-relevant gain.

---

## Risks (for research / planning to verify)

- **Where to start the classify worker so it actually runs.** CLAUDE.md warns the MCP lifespan fires per-chat-session, not at uvicorn boot. The worker must start at container startup. Research must confirm the exact hook in `build_app` / uvicorn startup, not the MCP lifespan. (Maps to the `[UNVERIFIED]` above.)
- **`dimensions.yaml` shape change blast radius.** Grep every caller of `load_dimensions` and `validate_dimension_tag` and every test asserting the flat shape (P5-DATA-07/08 at minimum). Confirm `storage/knowledge_entries.py` does not assume the flat shape indirectly.
- **Migration version-pin cascade.** Per CLAUDE.md, adding migration 010 bumps the prior version-pin tests. Research must list which `test_migration_00N.py` assertions move from `9` → `10`.
- **`KnowledgeEntry` dataclass + `_row_to_entry` must learn the two new columns** or `SELECT *` ranking queries will fail to populate them. Verify the dataclass round-trips trust_score/retrieval_count.
- **Sequential-consumer back-pressure under a large catch-up backlog.** A vault with thousands of unclassified docs floods the queue at startup. Slice A has no AI cost, but planning should note whether the catch-up scan should page/batch its `put`s.

---

## Open questions

**OQ-P8A-01 — Where exactly does the classify worker get started?**

Right now the cloud container builds its web app in one place at startup, but the system that handles live chat connections starts up separately, later, only when someone connects.

The question: do we start the classify worker at container build/startup, or piggyback on the chat-connection startup?

**If we start it at container build/startup:** the worker and its catch-up scan run as soon as the container is healthy, even with no chat clients connected — correct for a background housekeeper.
**If we piggyback on chat-connection startup:** classification would not run until a human first connects a chat client — wrong; capture would pile up unclassified.

Recommendation: start at container build/startup. A background housekeeper must not wait for a human to open a chat. (Not a blocker — resolvable in research by reading the startup code.)

**OQ-P8A-02 — One ranked query function, or extend the existing fact-loader?**

Right now there is a function that loads non-retired facts but does not rank or cap them.

The question: add a new ranked+capped query, or add sort/limit parameters to the existing one?

**If new function:** the existing 5-function fact-store contract stays stable; ranking is isolated and separately testable.
**If extend existing:** one fewer function, but the existing callers and tests now carry optional ranking params they don't use.

Recommendation: new function. The ranking concern is distinct from "give me the live facts" and isolating it keeps both simple. (Not a blocker.)

**OQ-P8A-03 — Does the catch-up scan enqueue ids in one burst, or paged?**

Right now there is no scan; Slice A introduces it.

The question: on startup, `put` every discoverable id at once, or page through them?

**If one burst:** simplest; fine for a personal vault of hundreds of docs.
**If paged:** safer for a huge backlog, but more code for a case that may never occur in the target (single-user) deployment.

Recommendation: one burst for Slice A, with a tech-debt note to page if backlog size becomes a problem. Matches the grill's "watch vault size" tech-debt entry. (Not a blocker.)

---

## Guardrail Checklist

_(from `/guardrail-check Review`; required input for `/writing-detailed-specs`)_

```
Domains touched: DB Integrity, Architecture, LLM & Providers (config only), Testing

[ ] C-04 · PRAGMA foreign_keys=ON on every new connection
    Slice A adds NO new connection factory — reuses get_connection. Satisfies (N/A).

[ ] C-05 · All schema changes via versioned .sql deltas
    APPLIES. Migration 010 must be a .sql file; columns/indexes never in-code ALTER.

[ ] C-06 · Confidence thresholds in config/thresholds.yaml, never in code
    APPLIES (watch). The 10000-token and 50-entry caps are CONFIG ints, not if/elif
    confidence floats — content-reader comparison + ranker LIMIT must read config,
    never a literal. Not a confidence float, so the hook does not fire, but the rule
    (no hardcoded tunables in pipelines) still binds.

[ ] C-07 · All AI prompts are YAML, never inline f-strings
    Satisfies (N/A) — Slice A makes no LLM calls.

[ ] C-08 · Pipelines use get_provider(task, CONFIG) factory
    Satisfies (N/A) — deferred to Slice B.

[ ] C-12 · Every public function in handlers/ and pipelines/ returns Success/Failure
    APPLIES. Content Reader, Context Loader, work-discovery, stamp function, and the
    consumer must return Result, not raw values.

[ ] C-13 · Audit log non-negotiable for AI decisions
    Satisfies (N/A) — Slice A makes no AI decisions; audit lands in Slice B.

[ ] C-17 · Never import CONFIG at module scope in tests
    APPLIES. New Slice A tests pass explicit db_path / config; no module-scope CONFIG.

Domains skipped: Write Safety (no vault writes — capture seam is a queue push),
                 Async & CLI (no new CLI command; worker runs under the container
                 event loop, not asyncio.run), Daemon Sync (daemon untouched).
```

---

## Options explored

### Option A — Reuse `core/tags.py` loader + classify-infra functions in `classify.py` + queue in `build_app` (Recommended)

**What this means:** Extend the dimension loader the previous phase already shipped, add the new prep functions to the classify file that Slice B rewrites anyway, and start the queue where the container already starts up. Smallest new surface; everything testable without AI.

**Files touched:** `storage/migrations/010_*.sql` (new); `config/dimensions.yaml` (expanded shape + guidance); `core/tags.py` (loader/validator learn nested shape); `storage/knowledge_entries.py` (dataclass + new ranked query); `storage/documents.py` (work-discovery + stamp functions); `pipelines/classify.py` (Content Reader, Context Loader, consumer skeleton — added alongside soon-to-die folder code); `mcp_server/cloud_entry.py` (start worker + catch-up scan).

**Cost:** Dev: medium. Runtime: zero LLM, light DB reads. Maintenance: one config-shape migration + inert columns.

**Risk:** `dimensions.yaml` shape change breaks P5-DATA-07/08 tests (known cascade). Worker-start hook must avoid the MCP-lifespan trap.

**Module depth:** No new module boundary (functions land in existing files). Deletion test: removing them scatters logic into the consumer/API — they earn their keep. New interfaces: none with 2+ adapters → no speculative protocols. Existing modules deepened: `core/tags.py` (already the dimension authority) and `storage/documents.py`.

**What it defers:** All LLM extraction, audit, capture-push wiring, source cleanup → Slice B.

**Constraints check:** C-05 satisfies (010 is .sql). C-06 satisfies (caps from config). C-12 satisfies (Result everywhere). C-17 satisfies (explicit db_path in tests). Others N/A.

### Option B — New `core/dimensions.py` module for the loader (Rejected → see below)

A dedicated module for dimension loading/validation/guidance instead of extending `core/tags.py`.

### Option C — Standalone classify worker process / separate container (Rejected → see below)

Run the queue + consumer as its own process or container rather than in-process with capture.

### Rejected alternatives

- **Option B (new dimensions module):** `core/tags.py` already owns `load_dimensions` + `validate_dimension_tag` (Phase 5 Slice 1). A new module would be a shallow pass-through duplicating that ownership — fails the deletion test (its complexity just reappears as imports). Extend the existing deep module instead.
- **Option C (separate worker process):** Directly contradicts the rearchitecture's grill decision §2 ("in-process, same container as capture; no separate worker") and adds deployment + IPC machinery for no Slice A benefit. Would also reintroduce concurrency on shared entities the sequential single-consumer design exists to prevent.
- **Separate `guidance.yaml` file:** Splits one concept (a dimension and its steering text) across two files that must be kept in lockstep. The grill (§4) explicitly puts guidance inside `dimensions.yaml`.
- **DB-backed durable queue table:** The grill (§1) explicitly rejects a queue/flag table — `classify_content_hash` + catch-up scan already give durable, restart-safe work discovery. A queue table would duplicate that.

> **Recommended: Option A.** It adds the least new code, reuses the dimension loader the previous phase already built, and keeps the queue where the container already boots — so the only genuinely new risk is a known, mechanical test cascade rather than new architecture.

---

## ADR offer

Two decisions clear all three ADR gates (hard to reverse + surprising + real trade-off):

1. **`dimensions.yaml` shape change (flat list → nested with guidance).** Hard-ish to reverse (config + loader + tests move together), surprising to a future reader expecting a flat list, and a real trade-off vs a separate guidance file.
2. **In-memory queue + `classify_content_hash` as the durable work record (no queue table).** Surprising (most systems reach for a job table), a real trade-off (durability via re-scan vs persisted queue), and shapes how every later classify enhancement finds work.

Recommend writing **one ADR** covering decision #2 (the work-discovery + in-memory-queue model) — it is the load-bearing architectural choice the rest of Phase 8 builds on. Decision #1 is config-shape evolution, better captured in this design doc than an ADR. _Deferred to the user per HITL contract — not written without sign-off._

---

## Escalation note

Slice A coupling stayed within the bounded scope the grill anticipated. The one item worth flagging: the **`dimensions.yaml` shape change is a cross-module config-contract change** (config + `core/tags.py` + Phase 5 tests), slightly broader than a pure "add columns + add functions" slice. It does NOT touch the public MCP API or the DB schema beyond the listed migration, and the loader is already owned by `core/tags.py`, so this is a contained, mechanical cascade — **not** a recommendation for full HEAVY design. Surfaced here so the spec phase budgets for the test updates.

---

## Next step

Design doc written. Run `/architecture-docs` to update the main architecture designs (note the new `classify_content_hash` work-discovery model and the in-process classify worker), then run `/writing-detailed-specs` to structure Option A into build steps.
