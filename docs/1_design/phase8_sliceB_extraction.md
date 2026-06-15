# Phase 8 Slice B — Classify Extraction Pipeline: Design

_Date: 2026-06-15_
_Status: Design COMPLETE. Ready for `/writing-detailed-specs`._
_Behavior ID prefix: **P8-CLS-B** (Slice A used P8-CLS-A-01…07; Slice B continues at P8-CLS-B-01…12, already written to `behavior_inventory.yaml` by an earlier design pass — this doc is their `spec_ref`)._
_ADRs written this pass: **ADR-0018** (self-correcting bounded retry loop, D9–D11) + **ADR-0019** (live-enqueue seam + exact-entity dedup, D1 + D6)._
_Source of truth for system direction: `docs/0_draft/cloud_native_rearchitecture.md`. Locked requirements: `docs/0_draft/phase8/phase8_sliceB_extraction_grill.md` (D1–D11, M1–M3)._

---

## In plain terms (read this first)

Slice A built the plumbing: it finds documents that need classifying, prepares their inputs, and stops right before any AI step. **Slice B adds the AI brain.** For each captured document, the system asks the AI — one focused question per knowledge category (people, projects, domains) — to pull out small structured facts ("Anthony leads Movie Q2", "Movie Q2 deadline is August"). It then safely writes those facts into the knowledge database (adding new ones, editing existing ones, retiring outdated ones), records the decision in the audit log, and marks the document done so it is never re-processed.

Three things make this more than a single AI call:

1. **It never silently loses a fact.** If the AI returns one bad fact among good ones, the good facts are saved, the bad one is logged, and the document is held back for another try — with a note telling the AI what it got wrong last time. A safety cap stops this from looping forever: after a configured number of tries, the document is parked for a human to look at.
2. **It never spawns duplicate facts.** The AI sees a short list of existing facts and avoids repeating them, but that list is capped — so before adding any "new" fact the system double-checks the database for an exact match and folds into it instead of creating a twin.
3. **It classifies the moment a document arrives.** Today, classification only runs at container startup; Slice B wires capture so a document dropped while the system is running is queued immediately.

What could go wrong, and how this design guards it: a half-written document is healed by retry-plus-dedup; a crash loses no permanent state (the durable "done" marker lives in the database, not in memory); a runaway AI is stopped by the retry cap; deleting a source document does not orphan its facts (the document's id is pruned from each fact, and a fact left with no sources is flagged pending for a human, never auto-deleted).

---

## Cast of characters (symbols used 3+ times)

| Name | Role |
|---|---|
| `pipelines/classify.py` | The classify module — already holds Content Reader, Context Loader, the queue consumer, and the catch-up scan (Slice A). Slice B adds the Entity Extractor, Entry Writer, and orchestrator here. |
| `knowledge_entries` | The database table holding one row per fact (dimension, entity, tag, fact text, status, confidence, sources, etc.). |
| `documents` | The database table holding one row per captured document. Carries `classify_content_hash` (the "done" marker) and gains the retry-state columns. |
| `classify_content_hash` | The per-document "done" marker: empty or stale = needs classifying; matching the content fingerprint = done. (Slice A; the "classify fingerprint".) |
| Entity Extractor | The new step that makes one AI call per dimension and parses the structured-fact reply. |
| Entry Writer | The new step that applies parsed facts to `knowledge_entries` (insert / update / retire), accumulates sources, and folds exact duplicates. |
| dimension | A top-level knowledge category (people / projects / domains), fixed in `config/dimensions.yaml`. |
| guidance | The plain-English steering text per dimension in `config/dimensions.yaml`, fed into the extraction prompt. |
| `get_provider` | The factory in `llm/provider.py` that returns the right AI provider for a task; the single dispatch point for every LLM call. |
| `core.audit.write` | The one allowed way to record an AI decision in the audit log. |
| `app.state` | The running web app's shared object where the live work queue is stored so capture can reach it. |

---

## Decision

**Build Slice B as eight components inside the existing classify module and the existing web/api layer — no new package — choosing: (A1) reach the live work queue via the app's shared state; (B1) persist retry count + last-failure reason as two new columns on the `documents` row and represent "parked" as a `needs-review` status the work scan skips; (C) reuse the existing `classify` task name (re-pointed at DeepSeek in config) rather than adding a new task literal; (D) keep the orchestrator, queue, and worker in `pipelines/classify.py` rather than a new worker module.**

One sentence why: every choice keeps the new code inside modules that already own the relevant concern (classify owns the worker; `documents` owns per-document lifecycle state; the upload handler already owns the request/app), so the deletion test passes — removing any new boundary would scatter its logic back into those same callers, not collapse a pass-through.

---

## Q1 Diagram — chosen orchestrator flow

```
# Classify Slice B — Chosen Orchestrator Flow (one document)
Scope: What happens when the classify worker processes ONE captured document.
       Does NOT show how the id reached the queue (see Grid A) or how facts
       are stored row-by-row (see Grid B / Entry Writer).

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

        Document id arrives on the work queue
                       │
                       ▼
        ┌────────────────────────────────┐
        │ Prepare inputs:                 │
        │ tag this run, read the doc text,│
        │ load known facts per category,  │
        │ load last-failure note + tries  │
        └───────────────┬────────────────┘
                        │
                        ▼
        ┌────────────────────────────────┐
        │ For each knowledge category:    │
        │  • AI extracts proposed facts   │
        │  • Writer adds/updates/retires, │
        │    folds duplicates, skips bad  │
        │  • Write one audit record       │
        └───────────────┬────────────────┘
                        │
                        ▼
            "Did every category fully
             succeed (facts + audit)?"
                        │
              ┌─────────┴──────────┐
             YES                   NO
              │                    │
              ▼                    ▼
   ┌──────────────────┐  ┌──────────────────────┐
   │ Stamp classified;│  │ Don't stamp; save     │
   │ clear failure    │  │ failure reason; add 1 │
   │ note + tries     │  │ to the try count      │
   └──────────────────┘  └──────────┬───────────┘
                                     │
                                     ▼
                          "Tries reached the cap?"
                                     │
                            ┌────────┴────────┐
                           NO                YES
                            │                 │
                            ▼                 ▼
                    ┌──────────────┐  ┌──────────────────┐
                    │ Stays in     │  │ Park for human   │
                    │ queue; will  │  │ review; audit    │
                    │ retry later  │  │ why it stopped   │
                    └──────────────┘  └──────────────────┘

Simplified: "tag this run / read text / load facts / load failure note" grouped
            into one Prepare box. The per-category extract→write→audit loop is one
            box (it repeats once per dimension). The retry-cap check is a second
            fork off the NO branch.
```

---

## Guardrail Checklist

(From `/guardrail-check Review`, domains: LLM & Providers, DB Integrity, Architecture, Async & CLI. Required input for `/writing-detailed-specs`.)

| Constraint | Applies to Slice B as | Status |
|---|---|---|
| **C-06** · thresholds in config, never in code | Retry cap K read from config; status via `confidence_to_status`/`band.route`, never a float compare in classify. | satisfies |
| **C-07** · prompts are YAML, never inline f-strings | `prompts/entity_extract.yaml` is the only prompt source; rendered via `PROMPTS["entity_extract"]`. | satisfies |
| **C-08** · use `get_provider(task, CONFIG)`, never `provider.complete()` directly | Entity Extractor calls `get_provider("classify", config).complete(...)`. | satisfies |
| **C-09** · providers carry model/synthesis_model/embedding_model | Reuse existing `classify` task → no new provider class, no field change. | not applicable (no new provider) |
| **C-04** · `PRAGMA foreign_keys=ON` on every connection | All DB access via `get_connection()` (already sets pragma); no new connection factory. | satisfies |
| **C-05** · schema changes via versioned `.sql` deltas | Retry-state columns land as **migration 011** — never an in-code ALTER. | satisfies |
| **C-10** · CLI async wrapped in `asyncio.run()` | Worker runs under the composed lifespan (already async); no new CLI. | satisfies |
| **C-11** · `load_dotenv` once in `cli/main.py` | No `load_dotenv` in classify.py or the worker. | satisfies |
| **C-12** · public functions in `pipelines/` return `Success`/`Failure` | Entity Extractor, Entry Writer, orchestrator all return `Result`. | satisfies |
| **C-13** · audit log non-negotiable | Per-dimension `core.audit.write` (not `storage.audit_log.append`); `source_ids`/`pipeline`/`stage` populated; `new_correlation_id()` per doc (M1). | satisfies |
| **C-14** · `mcp_server/tools.py` is logic-free | Slice B touches `api.py` event handler + `cloud_entry.py`, not `tools.py`. | not applicable |
| **C-15** · no MCP tool before its pipeline | Slice B adds no MCP tool. | not applicable |
| **C-16** · schedulers come last | Worker is event-driven (queue), not a cron scheduler. | not applicable |
| **C-01** · DB is source of truth; capture makes zero vault writes | Classify writes to `knowledge_entries`/`documents` only — no vault writes. | satisfies |
| **C-17** · never import CONFIG at module scope in tests | New tests pass explicit `db_path`/config; no module-scope `CONFIG`. | satisfies (impl-time) |

---

## Implications

- **The system now produces structured facts, not folder moves — the old "classify decides which folder" code is deleted entirely.** Classify's whole meaning changes: it no longer routes notes to project/domain folders; it extracts knowledge facts into a database.
  - Delete from `pipelines/classify.py`: `build_subject` (`:22`), `build_folder_subject` (`:52`), `_destination_names` (`:70`), `ClassifyResult` (`:97`), `classify` (`:113`). Delete `tests/test_pipelines/test_classify.py` (33 tests — verified: imports exactly these symbols). Keep the Slice A functions in the same file (`content_reader` `:259`, `context_loader` `:320`, `consumer` `:388`, `catch_up_scan` `:446`).
  - The conftest fixture at `tests/test_pipelines/conftest.py:96` monkeypatches `pipelines.capture.classify` — **verified that `pipelines.capture` has no `classify` attribute** (grep returns nothing; `hasattr(pipelines.capture, "classify")` is `False` at runtime) and **verified that no test file actually requests the `pipeline_ctx` fixture** (it is unused — only `conftest.py` mentions it). So the monkeypatch is dead today (it would `AttributeError` with default `raising=True` if ever exercised). Delete the dead `_stub_classify` block + the `ClassifyResult` import with the old code; no separate fixture repair is needed. (Confirms grill M3.)

- **Each captured document triggers one AI call per knowledge category, on a background worker — this is where the LLM token bill lives.** For N documents and M dimensions that is N×M calls; the full document text is re-sent once per dimension and each dimension's existing facts are re-sent per document.
  - Entity Extractor: one `get_provider("classify", config).complete(system, user)` per dimension, JSON-parsed. Mirror the old `classify()` error pattern (`:137-251`): template render error → `recoverable=False`; provider/parse/validation error → `recoverable=True`; truncate raw reply to 200 chars in the failure context.
  - Cost is bounded by Focused per-dimension context (D8) and DeepSeek's low price; cross-doc batching is deferred (TD-066), prompt caching is conditional on research (TD-067).

- **The AI is fed existing facts with their database ids so it can target updates and retirements precisely.** The reply uses ids to say "edit fact 12" or "retire fact 7", and omits the id for brand-new facts.
  - Context Loader (Slice A, `context_loader` `:320`) already returns ranked, capped, non-retired entries per dimension, each carrying its `id`. The extraction prompt renders these as an existing-entries-with-ids block. Slice A's known inefficiency (re-reading dimensions + re-querying facts per document) is acceptable for MVP; caching per consumer session is TD (Slice A review note).

- **Writing a fact is safe to repeat: updates accumulate provenance, and exact duplicates fold instead of multiplying.** Re-running the same extraction after a partial failure does not corrupt the table.
  - Entry Writer routes by action: `new` → insert (after the dedup check), `update` → update the referenced id, `retire` → `retire(id, reason)` (`knowledge_entries.py:159`, never deletes). On `update`, it **reads the existing `sources`, appends this document's id, dedupes, and writes the merged list** — because `upsert`'s UPDATE path overwrites `sources` wholesale (`:71`, `json.dumps(entry.sources)`); the DB does not merge (D2). Before any `new` insert it calls `query_by_entity` (`:143`) and folds an exact dimension+entity+tag non-retired match into an update (D6). A referenced id that does not exist (hallucination) is skipped and logged (D9).
  - Status is re-gated on every write from the fact's confidence via `confidence_to_status` (`core/tags.py:255`) — never a float compare (C-06).

- **A document is marked "done" only after its facts and its audit records are all on record — and one bad fact holds the whole document back for a retry.** The "done" stamp is the last action and is gated on perfection.
  - Write order per document: per dimension { extract → write facts → `core.audit.write` for that dimension }, then `stamp_classified(doc_id)` (`documents.py:659`) once after all dimensions. Any dimension's bad fact or failed audit ⇒ withhold the stamp (D4 + D9). The worker MUST call `new_correlation_id()` (`core/logging_setup.py:55`) per document before auditing, or `audit_log.append` fails with "missing correlation_id" (`audit_log.py:28-34`) (M1).

- **Retries are self-correcting and bounded, with durable state — so a malformed AI reply cannot loop forever.** Each retry tells the AI what failed last time; after a config-set number of tries the document is parked for a human.
  - New migration **011** adds `documents.classify_attempts` (INTEGER) + `documents.classify_last_error` (TEXT). New config `classify.max_retries` (K). On failure: save the error string, increment attempts; if attempts ≥ K, set `status = 'needs-review'` and write a "parked" audit entry (D10). On success: clear both columns. The retry prompt renders `previous_attempt_feedback` from `classify_last_error` (empty first attempt) (D11). `find_unclassified` (`documents.py:634`) gains a `status != 'needs-review'` filter so parked docs are not re-queued. (ADR-0018.)
  - Migration 011 triggers the standard version-pin cascade (bump `test_migration_007/008/009/010` pinned version → 11).

- **A document captured while the container runs is classified immediately, not at the next reboot.** The capture path now puts the document's id straight onto the live work queue.
  - The composed lifespan (`cloud_entry.py:117`) stores the queue on `app.state.classify_queue`; the upload handler (`mcp_server/api.py`) calls `queue.put_nowait(row_id)` after capture returns the id (`row_id` IS the doc id — verified at `capture.py:287/484/406`). Absent queue (CLI/tests) → skip silently; the catch-up scan (`catch_up_scan` `:446`) remains the net (D1, ADR-0019).

- **Deleting a source document prunes its id from every fact it backed; a fact left with no sources is flagged for a human.** Provenance shrinks correctly instead of leaving dangling references.
  - `_delete_with_blob_cleanup` (`api.py:330`) currently never touches `knowledge_entries`. Slice B adds: look up the document's id by path **before** the row is deleted (the delete signal arrives by path, but `sources` holds ids), then for every non-retired entry whose `sources` contains that id, remove the id, dedupe, and if the list becomes empty set `status = 'pending'` (never auto-delete, never auto-retire) (D3). Finding entries by contained id is a scan or JSON query (research decides the exact SQL).

- **Module-depth check — no new package, the classify module deepens.** `pipelines/classify.py` is already a deep module (small public surface: queue functions + helpers; large internal behavior). Slice B adds the Entity Extractor / Entry Writer / orchestrator as functions in the same module rather than a new `mcp_server/classify_worker.py`.
  - Deletion test: removing a hypothetical new worker module would not collapse complexity — the consumer loop, content reader, and context loader already live in `classify.py`; splitting the orchestrator out would force it to re-import all of them and split one cohesive flow across two files. Keeping it in `classify.py` keeps the seam at the queue (one interface), not at an arbitrary file boundary. The Entry Writer is the one candidate for its own home (it has real internal logic) — but it is a single caller (the orchestrator) and would be a 1-adapter seam, so it stays a function in `classify.py` for now.

---

## Known tradeoffs

- **Per-call context re-send (no batching, no caching yet).** We pay N×M repeated context sends. We accept this because classify is off the hot path, DeepSeek is cheap with large context, and the vault is single-user; batching would damage the clean per-doc audit/stamp/source model. (D7; TD-066/067.)
- **Focused per-dimension context, not holistic.** Each dimension's call does not see other dimensions' facts, so a fact that links two dimensions ("Anthony, who leads Finance") may be missed on one side. We accept this for cheaper, more focused prompts; a hybrid cross-dimension entity-name header is the upgrade path. (D8; TD-068.)
- **Exact-entity dedup only.** "Anthony" and "Anthony Nguyen" stay separate until entity resolution ships. We accept duplicate-by-name-variant drift to avoid building entity resolution now. (D6.)
- **`needs-review` status is overloaded.** Capture already uses it for needs-summary rows; classify reuses it for parked docs. We accept the overload (distinguishable by `classify_attempts`) rather than introducing a second status vocabulary.
- **Retry state on `documents` couples per-document lifecycle to the hot work-discovery query.** We accept two extra columns to keep work discovery a single-table query, rather than a join against a separate retry table.

---

## Risks (for research / planning to verify)

- **DeepSeek/OpenAI-compatible endpoint prompt-caching support is unverified.** D7 wires caching only if research confirms it. Research must check the actual `openai_provider` + endpoint capabilities before any caching work (TD-067).
- **`config.yaml` currently routes `classify: claude_cli`** (verified `config/config.yaml:63`). M2's "re-point classify at DeepSeek" means changing this mapping to the OpenAI-compatible provider — research/plan must confirm the `openai_compat` provider config (`model`, endpoint) is set for DeepSeek V4 Pro, or the first real classify call hits the wrong model.
- **Finding `knowledge_entries` by a document id contained in the JSON `sources` text column** has no existing query — research must decide between a full scan + in-Python filter vs a SQL `json_each`/`LIKE` approach, and confirm SQLite JSON support is available in the deployed build.
- **The live-enqueue path runs inside the web upload handler** — research must confirm the handler has access to `app.state` (Starlette `request.app.state`) at that point and that `put_nowait` on the unbounded queue cannot block or raise under load.
- **Catch-up backlog flooding** (OQ-P8A-03, carried) — a large unclassified vault enqueues everything at boot; now each item costs an AI call. Paging/batching the scan's `put`s may matter more in Slice B than Slice A.
- **Status re-gating on update may demote a confident fact to pending** if a later low-confidence extraction updates it. Verify this is the intended lifecycle (broad grill says confidence re-gates on every write) and that it does not thrash.

---

## Open questions

**OQ-P8B-01 — How to find facts by a contained source id**

Right now, a fact's list of source documents is stored as a single text field inside the fact's row (a JSON list of id numbers), and there is no query that finds "every fact whose source list contains id 9".

The question: when a document is deleted, how do we locate the facts that name it as a source — scan every non-retired fact and filter in code, or use a database JSON query?

**If scan-and-filter in code:** simplest, always works, but reads every non-retired fact on each delete (fine at current scale, slower as facts grow).
**If a database JSON query:** faster and scoped, but depends on the deployed SQLite build supporting JSON functions and on getting the query right.

Recommendation: scan-and-filter in code for Slice B. At a single-user vault's fact count the cost is negligible, it has zero dependency risk, and it can be swapped for a JSON query later without changing behavior. Not a blocker.

**OQ-P8B-02 — Does an update that lowers confidence demote a fact's status**

Right now, the design re-computes a fact's confident/pending status from its confidence every time it is written, including on updates.

The question: if a new, lower-confidence mention updates a previously-confident fact, should the fact drop back to pending?

**If yes (re-gate on every write):** matches the broad grill's "confidence is a live signal", but a single hedged mention can demote an established fact, which may surface noise for human review.
**If no (only ever promote):** stabler, but a fact that becomes genuinely uncertain never reflects that.

Recommendation: re-gate on every write (the locked broad-grill behavior). Flag for observation — if demotion thrashes in practice, a "max confidence seen" rule is a later refinement. Not a blocker.

**OQ-P8B-03 — Should the startup catch-up scan page its enqueues**

Right now, the catch-up scan puts every unclassified document id onto the queue in one burst at startup; in Slice A that was free (no AI). In Slice B each item costs an AI call.

The question: should the scan enqueue in pages/batches rather than all at once?

**If one burst (today):** simplest; the sequential consumer drains them one at a time anyway, so memory is the only concern.
**If paged:** smoother startup memory profile for a huge backlog, more code.

Recommendation: keep one burst for Slice B (carries OQ-P8A-03). The consumer is sequential so token spend is naturally rate-limited; paging is a later optimization. Not a blocker.

---

## ADR references

- **ADR-0017** (Slice A) — in-memory `asyncio.Queue` + `classify_content_hash` work discovery. Slice B builds on it (does not duplicate): it fills the documented seam and adds the live-enqueue path.
- **ADR-0018** (this pass) — self-correcting bounded retry loop (D9–D11): withhold-stamp-to-retry, inject last-failure feedback, cap at config K, park as `needs-review`; retry state on new `documents` columns.
- **ADR-0019** (this pass) — live-enqueue seam via `app.state` (D1) + exact-entity write-time dedup backstop (D6).

---

## Options explored

### Grid A — Live capture→queue handle mechanism

**Chosen: A1 — queue on `app.state`.** The queue is created in the lifespan that owns the app; storing it on `app.state` is the framework's place for per-app singletons, and the upload handler already has the request (hence the app). Degrades cleanly when absent.

```
# Grid A · Option A1 (CHOSEN) — Queue handle on the app's shared state
   Capture finishes a document (knows its id)
                    │
                    ▼
        ┌──────────────────────────┐
        │ Look up the work queue on │
        │ the running app's shared  │
        │ state object              │
        └────────────┬─────────────┘
                     │
              ┌──────┴───────┐
         queue found     no queue (e.g. CLI run)
              │               │
              ▼               ▼
   ┌──────────────────┐  ┌──────────────────┐
   │ Put the doc id on │  │ Skip — startup    │
   │ the queue (live)  │  │ catch-up scan will│
   └──────────────────┘  │ pick it up later  │
                         └──────────────────┘
```

- **A2 — module-level singleton queue.** A module holds the one queue, imported by capture. Not selected: a module-global leaks across tests unless explicitly reset and hides the queue's true owner (the app lifespan) behind import side-effects.

```
# Grid A · Option A2 — Module-level shared queue variable
   Capture finishes a document (knows its id)
                    │
                    ▼
        ┌──────────────────────────┐
        │ Import a shared module    │
        │ that holds the one queue  │
        │ created at app startup    │
        └────────────┬─────────────┘
              ┌──────┴───────┐
        queue set        queue is empty (not started)
              │               │
              ▼               ▼
   ┌──────────────────┐  ┌──────────────────┐
   │ Put the doc id on │  │ Skip — catch-up   │
   │ the queue (live)  │  │ scan covers it    │
   └──────────────────┘  └──────────────────┘
```

- **A3 — dependency injection (pass the queue down every call).** Not selected: capture's signature would gain a queue parameter that almost every caller (CLI, tests, reconcile) passes as None — speculative plumbing through layers that don't care, for one caller that does.

```
# Grid A · Option A3 — Pass the queue down through every call
   App startup creates the queue
                    │
                    ▼
        ┌──────────────────────────┐
        │ Pass the queue into the   │
        │ web upload handler, which │
        │ passes it into capture    │
        └────────────┬─────────────┘
                     ▼
        ┌──────────────────────────┐
        │ Capture finishes a doc    │
        │ and puts its id on the    │
        │ queue it was handed       │
        └──────────────────────────┘
```

### Grid B — Where retry-count + last-failure-reason persist, and how "park" is represented

**Chosen: B1 — new columns on the `documents` row; park = `needs-review` status.** Keeps work discovery a single-table query (it already reads `documents`), and per-document lifecycle state belongs on the document.

```
# Grid B · Option B1 (CHOSEN) — New columns on the document record
        Classify attempt finishes
              ┌─────┴──────┐
          success       failure
              │             │
              ▼             ▼
   ┌──────────────┐  ┌──────────────────────────┐
   │ Clear the    │  │ Save failure reason +     │
   │ count + note │  │ add 1 to the count, all   │
   │ on the doc   │  │ on the document's own row │
   └──────────────┘  └────────────┬─────────────┘
                                  ▼
                        "Count past the cap?"
                          ┌───────┴────────┐
                         NO               YES
                          │                │
                          ▼                ▼
                  ┌──────────────┐ ┌──────────────────┐
                  │ Re-found by  │ │ Set status "needs │
                  │ work scan    │ │ review"; scan     │
                  │ (NULL stamp) │ │ skips that status │
                  └──────────────┘ └──────────────────┘
```

- **B2 — separate retry-tracking table.** Not selected: a second table keyed by doc id duplicates the lifecycle the `documents` row owns and forces the hot work-discovery query into a join, for data that is 1:1 with the document.

```
# Grid B · Option B2 — Separate retry-tracking table
        Classify attempt finishes
              ┌─────┴──────┐
          success       failure
              │             │
              ▼             ▼
   ┌──────────────┐  ┌──────────────────────────┐
   │ Delete the   │  │ Upsert a row in a retry   │
   │ doc's retry  │  │ table: count, reason,     │
   │ row          │  │ parked flag               │
   └──────────────┘  └────────────┬─────────────┘
                                  ▼
                  Work scan joins documents to the
                  retry table and skips parked rows
```

- **B3 — reconstruct from the audit log.** Not selected: the audit log can count attempts but has no home for the *parked* state or the *last-failure feedback string* without scanning/parsing history on every attempt, and the parked state must be a cheap filter in work discovery.

```
# Grid B · Option B3 — Reconstruct from the audit log
        Classify attempt finishes (audit row written either way)
                    │
                    ▼
        ┌──────────────────────────┐
        │ Before each attempt, count│
        │ this doc's prior failures │
        │ by scanning audit history │
        └────────────┬─────────────┘
                     ▼
              "Failures past cap?"
              ┌──────┴───────┐
             NO             YES
              │              │
              ▼              ▼
      ┌──────────────┐ ┌──────────────────┐
      │ Attempt again│ │ Park (but parked  │
      │              │ │ state has no home) │
      └──────────────┘ └──────────────────┘
```

### Grid C — LLM task name (M2)

**Chosen: reuse the existing `classify` task literal, re-pointed at DeepSeek in config.** The old folder-routing classify dies, freeing the `classify` task name; reusing it avoids editing the `Task` literal (`core/config.py:43-45`) and its `providers.for_task` mapping. The Extractor calls `get_provider("classify", config)`.
- Rejected: adding a distinct `entity_extract` task literal — requires extending the `Task` union and the `providers:` mapping for no behavioral gain, since exactly one consumer uses it and the old name is now free.

### Grid D — Orchestrator / queue / worker home

**Chosen: keep them in `pipelines/classify.py`.** The consumer, content reader, context loader, and catch-up scan already live there (Slice A); the orchestrator is the natural completion of the consumer loop. One cohesive flow, one file, seam at the queue.
- Rejected: a new `mcp_server/classify_worker.py` — would re-import all the Slice A helpers and split one flow across two files; a 1-file move that fails the deletion test (removing it scatters logic back, gains nothing).

### Rejected alternatives (across grids)

- **Unique DB index on (dimension, entity, tag)** instead of the write-time dedup backstop — too rigid (hard-fails legitimate near-duplicates; cannot express fold-and-accumulate); the broad grill chose no unique index. (D6.)
- **Skip-the-bad-fact and stamp anyway** instead of withhold-stamp-retry — silently loses a fact that fails once. (D9.)
- **All-or-nothing per-dimension transaction** — does not exist today; per-fact writes plus retry-heals is the existing pattern (every `knowledge_entries` function opens its own connection). (D5.)
- **Holistic single-call extraction (all dimensions one prompt)** — maximizes the repeated-context cost the design is bounding. (D8.)

---

## Next step

Design doc written. Run `/update-arch-story` to fold Slice B into the architecture designs, then `/writing-detailed-specs` to structure the chosen option into build steps. The behavior inventory entries (P8-CLS-B-01…12) already exist and point here as their `spec_ref`.
