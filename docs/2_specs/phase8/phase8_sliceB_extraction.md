# Phase 8 Slice B — Classify Extraction Pipeline (spec)

_Date: 2026-06-15_
_Status: SPEC — ready for `/research`._
_Design (primary input): `docs/1_design/phase8/phase8_sliceB_extraction.md` (design COMPLETE; choices A1/B1/C/D locked; ADR-0017/0018/0019)._
_Slice A plan (built infrastructure this builds ON): `docs/4_plans/phase8_sliceA_classify_infra.md`._
_System direction: `docs/0_draft/cloud_native_rearchitecture.md`._
_Behavior IDs: **P8-CLS-B-01 … P8-CLS-B-12** — already in `docs/system_behavior/behavior_inventory.yaml`, pointing at the design doc as `spec_ref`. This spec references them; it does NOT duplicate or redefine them._

> **For the non-coder reader.** Slice A built the plumbing: it finds documents that still need classifying, prepares their inputs, and stops just before any AI step. **Slice B adds the AI brain.** For each captured document the system asks the AI — one focused question per knowledge category (people, projects, domains) — to pull out small structured facts ("Anthony leads Movie Q2"). It then safely writes those facts into the knowledge database (adding new ones, editing existing ones, retiring outdated ones), records each decision in the audit log, and marks the document done so it is never re-processed. Three guards make this more than a single AI call: it never silently loses a fact (a bad fact is logged and the document retried with a note about what failed, capped so it cannot loop forever); it never spawns duplicate facts (an exact-match check before every insert folds twins together); and it classifies the moment a document arrives (capture now puts the document's id straight onto the live work queue).

---

## Purpose

After this slice the system turns every captured document into structured, audited knowledge facts automatically — instead of routing notes to folders (the old behavior, deleted here). A document dropped while the container is running is classified within moments; a document already in the vault at boot is classified by the startup scan. Each document's facts are written safely (add / update / retire, with provenance accumulation and exact-duplicate folding), every AI decision is logged, and the document is stamped "done" only once everything succeeded. A malformed AI reply is retried with feedback and, after a configured number of tries, parked for a human rather than looping forever. Deleting a source document prunes its id from every fact it backed, and a fact left with no sources is flagged for a human, never silently deleted.

This is the AI layer that Slice A deliberately stopped short of. It is the last piece of Phase 8's classify redesign.

---

## Already built (reuse, do not rebuild)

Grouped by module. Line numbers are current as of this writing; research re-verifies.

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| **Work Finder** `find_unclassified` | `storage/documents.py:634` | Returns ids of documents whose `classify_content_hash` is NULL or stale. | The retry loop adds a `status != 'needs-review'` filter so parked docs are not re-queued. | deep |
| **Classified-Stamp** `stamp_classified` | `storage/documents.py:659` | Sets `classify_content_hash = content_hash` for one doc id; `Result[int]` rowcount. | The orchestrator calls it once per document, only on full success. | deep |
| **Content Reader** `content_reader` | `pipelines/classify.py:259` | Chooses `full_body` vs `summary` by token budget; `Result[str]`. | The orchestrator's "read the doc text" input. Unchanged. | deep |
| **Context Loader** `context_loader` | `pipelines/classify.py:320` | Loads ranked, capped, non-retired facts per dimension, each carrying its `id`; `Result[dict]`. | Supplies the existing-facts-with-ids block the Extraction Prompt renders. Unchanged behavior; known per-doc re-query inefficiency carried (Slice A TD). | deep |
| **Consumer** `consumer` | `pipelines/classify.py:388` | Single sequential worker; pulls a doc id, prepares inputs, **stops at the Slice B seam**. | Slice B fills the seam with the orchestrator call (extract → write → audit → stamp / retry). | deep |
| **Catch-up Scan** `catch_up_scan` | `pipelines/classify.py:446` | One-burst startup enqueue of all `find_unclassified` ids. | Reused as the boot-time net; unchanged (paging deferred — OQ-P8B-03). | shallow |
| **Fact Store CRUD** | `storage/knowledge_entries.py` | `upsert` (`:54`), `query_by_entity` (`:143`), `retire` (`:159`, never deletes), `query_ranked_by_dimension` (`:206`), `KnowledgeEntry` dataclass (`:16`, carries `sources`, `status`, `confidence`, `trust_score`). | Entry Writer routes facts through `upsert`/`retire`, dedups via `query_by_entity`. **`upsert` UPDATE overwrites `sources` wholesale** (`:71`) — Entry Writer must merge in Python. | deep |
| **Provider factory** `get_provider` | `llm/provider.py` | Returns the AI provider for a task (single dispatch point). | Entity Extractor calls `get_provider("classify", config).complete(...)`. | deep |
| **Prompt loader** `PROMPTS[name].render` | `llm/prompt_loader.py:13` | Loads a YAML prompt, returns `(system, user)`. | Entity Extractor renders `PROMPTS["entity_extract"]`. | deep |
| **Audit** `core.audit.write` | `core/audit.py:11` | Records one AI decision (`decision: AIDecision`, `pipeline`, `stage`, `outcome`); requires a correlation_id in contextvars. | Orchestrator writes one audit record per dimension + one on park. | deep |
| **Correlation id** `new_correlation_id` | `core/logging_setup.py:55` | Sets a per-run correlation id in contextvars. | Orchestrator calls it once per document before any audit, or `audit_log.append` fails "missing correlation_id" (`audit_log.py:28-34`). | deep |
| **Status helper** `confidence_to_status` | `core/tags.py:255` | Maps a confidence float → `"confident"`/`"pending"` via `band.route` (no float literal). | Entry Writer re-gates each written fact's status from its confidence — never a float compare (C-06). | deep |
| **Dimensions loader** `load_dimensions` | `core/tags.py` | Returns `Result[dict]` of `{dim: {tags, guidance}}` (nested shape, Slice A). | Source of per-dimension `guidance` for the Extraction Prompt; already consumed by `context_loader`. | deep |
| **Classify config** `ClassifyConfig` | `core/config.py:336` | `max_content_tokens`, `max_entries_per_dimension`. | Slice B adds `max_retries` (K) here. | shallow |
| **Task literal** `Task` | `core/config.py:43` | Includes `"classify"`; `providers.classify` maps it (`config.yaml:63`, currently `claude_cli`). | Reused as-is (Grid C); M2 re-points the mapping at DeepSeek — no new task literal. | deep |
| **Composed lifespan / queue wiring** `build_app`, `_wrap_lifespan` | `mcp_server/cloud_entry.py:43,96` | Builds the app; wraps the FastMCP lifespan; creates the `asyncio.Queue` and starts the consumer + catch-up scan. | Slice B stores the queue on `app.state.classify_queue` (currently a local var, `cloud_entry.py:118`). | deep |
| **Upload handler** `upload_handler` | `mcp_server/api.py:96` | Receives a capture, calls `capture_upload`, returns the doc id. | Slice B adds the live-enqueue push after a successful capture. `row_id` IS the doc id (verified `capture.py:216/406/484`). | deep |
| **Delete path** `_delete_with_blob_cleanup` | `mcp_server/api.py:330` | Deletes a document row by path + best-effort blob cleanup. | Slice B adds the source-prune (look up id by path BEFORE delete). | deep |
| **Old folder-routing classify** `build_subject` `:22`, `build_folder_subject` `:52`, `_destination_names` `:70`, `ClassifyResult` `:97`, `classify` `:113` | `pipelines/classify.py` | The retired "which folder?" logic. | **DELETED** in this slice (see component 10). | n/a |
| **Old classify tests** | `tests/test_pipelines/test_classify.py` (791 lines, 33 tests) | Test the old folder-routing symbols. | **DELETED** with the code. | n/a |
| **Dead stub block** | `tests/test_pipelines/conftest.py:79-96` | Imports `ClassifyResult`, defines `_stub_classify`, monkeypatches `pipelines.capture.classify` (a non-existent attribute — dead today). | **DELETED** (no fixture repair needed; verified `pipelines.capture` has no `classify` attr). | n/a |

---

## Q1 Diagram — chosen orchestrator flow (copied from design)

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
```

## Q2 Diagram — how it connects to others

```
# Classify Extraction — How It Connects
Scope: Shows what the Classify Module touches when it turns one captured
       document into structured facts. Does NOT show the internal
       step order (see Q1 for that).

How to read this:
  Center box     = the feature being built
  Solid boxes    = components that already exist
  Dashed boxes   = new in this slice (Entity Extractor, Entry Writer,
                   Extraction Prompt, and the two new handler hooks)
  Arrow labels   = what passes between them

                       ┌──────────────────┐         ┌──────────────────┐
                       │ Catch-up Scan    │         │ Work Queue       │
                       │ At startup, finds│──fills──►│ Doc ids waiting  │
                       │ unclassified docs│  with ids│ to be classified │
                       └──────────────────┘         └────────┬─────────┘
  ┌ ─ ─ ─ ─ ─ ─ ─ ┐                                          │ pulls one
  │ Upload Handler│ ── pushes new doc id ──────────────────► │ doc id at
  │ (new hook)    │                                          │ a time
  └ ─ ─ ─ ─ ─ ─ ─ ┘                                          ▼
                                                  ┌────────────────────┐
        ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐              │   CLASSIFY MODULE  │
        │ AI Call Cluster (new)     │  asks for    │   Orchestrator:    │
        │ Entity Extractor +        │◄──facts per──┤   per category,    │
        │ Extraction Prompt +       │   category   │   extract → write  │
        │ AI Provider Factory       │──returns────►│   → audit, then    │
        └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘   facts      │   stamp done       │
                                                   └──┬────┬────┬───┬───┘
                  ┌───────────────────────────────────┘    │    │   │
        reads     ▼                              hands facts│    │   │ writes
   ┌──────────────────┐                          to (new)   ▼    │   │ "done"
   │ Knowledge        │                      ┌ ─ ─ ─ ─ ─ ─ ─ ┐   │   │ marker +
   │ Categories config│                      │ Entry Writer  │   │   │ retry
   │ Names + guidance │                      │ Add / update /│   │   │ state
   └──────────────────┘                      │ retire facts, │   │   ▼
                                             │ fold dups,    │   │ ┌──────────────────┐
                                             │ prune sources │   │ │ Document Store   │
                                             └──────┬────────┘   │ │ Per-doc row +    │
                                          writes    │            │ │ done marker +    │
                                          facts to  ▼            │ │ retry columns    │
                                          ┌──────────────────┐   │ └────────┬─────────┘
                                          │ Fact Store       │   │          ▲
                                          │ One row per fact │   │ records  │ prunes doc
                                          └──────────────────┘   │ each AI  │ id from
                                                                 ▼ decision  │ each fact
                                          ┌──────────────────┐   │           │
                                          │ Audit Log        │◄──┘   ┌ ─ ─ ─ ┴ ─ ─ ─ ┐
                                          │ Records every    │       │ Delete Handler│
                                          │ AI decision      │       │ (new hook):   │
                                          └──────────────────┘       │ source-prune  │
                                                                     └ ─ ─ ─ ─ ─ ─ ─ ┘
```

```
Simplified: The three AI-call pieces (Entity Extractor, Extraction Prompt,
            AI Provider Factory) are grouped into one "AI Call Cluster" spoke.
            App Shared State is not drawn as its own box — it is just where the
            Work Queue lives so the handlers can reach it (the Upload Handler's
            arrow into the Work Queue stands in for it).
```

### Glossary (plain-English ↔ code)

| Plain-English name (in diagrams) | Code reference |
|---|---|
| Classify Module / orchestrator | `pipelines/classify.py` (new orchestrator function) |
| Entity Extractor | new function in `pipelines/classify.py` |
| Entry Writer | new function in `pipelines/classify.py` |
| AI Provider Factory | `get_provider` in `llm/provider.py` |
| Extraction Prompt | `prompts/entity_extract.yaml` (new) |
| Knowledge Categories config | `config/dimensions.yaml` via `core/tags.py::load_dimensions` |
| Fact Store | `knowledge_entries` table via `storage/knowledge_entries.py` |
| Document Store | `documents` table via `storage/documents.py` |
| Audit Log | `core/audit.py::write` → `storage/audit_log.py` |
| Work Queue | `asyncio.Queue[int]` created in `cloud_entry.py::_wrap_lifespan` |
| Upload Handler | `upload_handler` in `mcp_server/api.py` |
| Delete Handler | `_delete_with_blob_cleanup` in `mcp_server/api.py` |
| App Shared State | Starlette `app.state` |
| Catch-up Scan | `catch_up_scan` in `pipelines/classify.py` |

---

## Feature overview

**Happy path (one document).** A document id reaches the Work Queue — either pushed live by the Upload Handler the instant a capture finishes, or enqueued by the startup Catch-up Scan. The single sequential consumer pulls one id at a time and hands it to the orchestrator. The orchestrator tags the run with a fresh correlation id, reads the document's text (Content Reader: full body or summary by size), loads the known facts per category each carrying its database id (Context Loader), and loads the document's last-failure note and try count from its row. Then, **for each knowledge category** (people / projects / domains): the Entity Extractor makes one AI call — rendering the Extraction Prompt with that category's guidance, the existing-facts-with-ids block, and any previous-attempt feedback — and parses a structured-fact reply; the Entry Writer applies each fact (add a new one after an exact-duplicate check, update an existing one by id while accumulating its sources, or retire one by id), skipping and logging any fact that references a non-existent id; the orchestrator writes one audit record for that category. When every category fully succeeds (facts written and audit recorded), the orchestrator stamps the document classified once and clears its failure note and try count. The document is now done and the Work Finder will never return it again unless its content changes.

**Edge cases.**
- *A bad fact among good ones.* The good facts are still written; the bad fact (e.g. a hallucinated id, an unparseable reply, a failed audit) holds the whole document back: the orchestrator does NOT stamp it, saves the failure reason on the document row, and increments the try count. The Work Finder re-queues it (its `classify_content_hash` is still NULL/stale). On the retry the Extraction Prompt renders the saved failure reason as `previous_attempt_feedback` so the AI is told what it got wrong.
- *A runaway document.* When the try count reaches the configured cap K, the orchestrator parks the document (`status = 'needs-review'`) and writes a "parked" audit record. The Work Finder's new filter excludes `needs-review` rows, so a parked document is never re-queued — it waits for a human.
- *Duplicate facts.* The AI sees a capped list of existing facts and is told to reuse their ids, but the cap means it cannot see them all. So before any new insert the Entry Writer queries the Fact Store for an exact `dimension + entity + tag` non-retired match and folds into it (update + source accumulation) instead of creating a twin.
- *A captured document while the container runs.* The Upload Handler reaches the Work Queue on `app.state` and pushes the new id; if no queue is present (CLI runs, tests) it skips silently and the next Catch-up Scan covers it.
- *A deleted source document.* On delete, the system looks up the document's id by path BEFORE the row is removed, then for every non-retired fact whose `sources` contains that id it removes the id and dedupes; a fact left with no sources is set to `status = 'pending'` (never auto-deleted, never auto-retired).
- *A crash mid-document.* No permanent state is lost: the durable "done" marker lives on the document row, never in memory, so an un-stamped document is simply re-discovered and re-run; the retry-plus-dedup design makes re-running safe.

---

## Out of scope

- **Cross-document batching of AI calls** — each document still sends its full text once per dimension. Deferred — TD-066. (Per-call context re-send is an accepted tradeoff; design "Known tradeoffs".)
- **Prompt caching for the OpenAI-compatible endpoint** — wired only if research confirms endpoint support. Deferred — TD-067. (Design risk #1.)
- **Holistic cross-dimension extraction** (one prompt that sees all dimensions' facts at once) — focused per-dimension calls are intentional. A cross-dimension entity-name header is the upgrade path. Deferred — TD-068. (Design tradeoff D8.)
- **Entity resolution / name-variant merging** — "Anthony" and "Anthony Nguyen" stay separate. Exact-entity dedup only. Deferred — no phase assigned. (Design tradeoff D6.)
- **Paging / batching the Catch-up Scan's enqueue** — one burst, same as Slice A. Deferred — OQ-P8B-03 / OQ-P8A-03. (Design open question.)
- **Populating `trust_score` / `retrieval_count`** — both remain inert in Phase 8 (Phase 9 increments retrieval_count; Phase 10 populates trust_score). Slice A note.
- **A real tokenizer** — Content Reader keeps the `chars / 4` estimate. (Slice A out-of-scope, unchanged.)
- **Any new MCP tool** — Slice B adds none (C-15). The work runs on the background worker + the existing REST handlers.
- **Re-pointing `providers.classify` at DeepSeek in `config.yaml`** as a code change — the spec assumes the config mapping is set by deploy/research (Grid C, M2; design risk #2). The *code* always calls `get_provider("classify", config)`; which model that resolves to is a config concern, confirmed in research.

---

## Constraints

Each must hold for every component below. Sourced from the design's Guardrail Checklist and `CONSTRAINTS.md`.

- **C-06** — retry cap K read from config; status via `confidence_to_status`/`band.route`, never a float compare in `pipelines/`. Source: CONSTRAINTS.md C-06 / hook hard-block.
- **C-07** — `prompts/entity_extract.yaml` is the only prompt source; rendered via `PROMPTS["entity_extract"].render(...)`; no inline prompt f-strings. Source: CONSTRAINTS.md C-07 / hook warning.
- **C-08** — Entity Extractor calls `get_provider("classify", config).complete(...)`; never instantiate a provider directly. Source: CONSTRAINTS.md C-08.
- **C-04** — all DB access via `get_connection()` (sets `PRAGMA foreign_keys=ON`); no new connection factory. Source: CONSTRAINTS.md C-04.
- **C-05** — retry-state columns land as **migration 011** (`.sql` file); no in-code `ALTER TABLE`. Source: CONSTRAINTS.md C-05.
- **C-12** — Entity Extractor, Entry Writer, orchestrator all return `Success`/`Failure`. Source: CONSTRAINTS.md C-12.
- **C-13** — per-dimension `core.audit.write` (not `storage.audit_log.append`); `source_ids`/`pipeline`/`stage` populated; `new_correlation_id()` per document before any audit. Source: CONSTRAINTS.md C-13.
- **C-14** — Slice B touches `api.py` handlers + `cloud_entry.py`, never `mcp_server/tools.py`. Source: CONSTRAINTS.md C-14.
- **C-01** — classify writes to `knowledge_entries` / `documents` only — zero vault writes. Source: CONSTRAINTS.md C-01.
- **C-17** — new tests pass explicit `db_path`/config; no module-scope `CONFIG`. Source: CONSTRAINTS.md C-17 / hook block.
- **Stdlib logging** — worker/log lines use `%s`-style, not kwargs (`_log.warning("msg key=%s", v)`). Source: CLAUDE.md "General patterns".
- **Migration version-pin cascade** — adding migration 011 bumps prior pinned-version assertions to 11. Source: CLAUDE.md migration gotcha.

---

## Assumptions

Each is a falsifiable claim about existing code that research must verify.

| ID | Assumption | Source implication | What would prove it wrong |
|----|-----------|-------------------|--------------------------|
| A1 | The old folder-routing symbols deleted (`build_subject`, `build_folder_subject`, `_destination_names`, `ClassifyResult`, `classify`) are imported ONLY by `tests/test_pipelines/test_classify.py` and the dead conftest block. | "old code deleted entirely" | Any other source/test file imports one of these symbols. |
| A2 | `pipelines.capture` has no `classify` attribute; the conftest monkeypatch (`conftest.py:96`) is dead and no test requests the `pipeline_ctx` fixture. | "the monkeypatch is dead today" | `hasattr(pipelines.capture, "classify")` is True, or a test references `pipeline_ctx`. |
| A3 | `knowledge_entries.upsert` UPDATE path (`:71`) overwrites `sources` wholesale (`json.dumps(entry.sources)`); the DB does not merge sources. | "upsert overwrites sources" | UPDATE merges or appends sources at the SQL layer. |
| A4 | `query_by_entity` (`:143`) returns all non-... entries for an entity carrying `id`, `dimension`, `tag`, `status`, enough to detect an exact `dimension+entity+tag` non-retired match. | exact-entity dedup backstop | The function omits any of those fields, or filters out the rows needed. |
| A5 | `retire(id, reason)` (`:159`) never deletes — it sets `status='retired'`. | retire-not-delete | `retire` issues a DELETE. |
| A6 | `confidence_to_status(score, band)` (`tags.py:255`) returns the status string via `band.route` with no float literal in `pipelines/`. | status re-gate via helper | The helper is unavailable, or status must be computed with a float compare. |
| A7 | `core.audit.write(decision, pipeline=, stage=, outcome=)` (`audit.py:11`) requires a correlation_id present in contextvars (set by `new_correlation_id`), else `audit_log.append` fails "missing correlation_id" (`audit_log.py:28-34`). | per-doc correlation id (M1) | audit.write succeeds without a correlation id, or has a different signature. |
| A8 | `find_unclassified` (`documents.py:634`) is a single-table `documents` query that can gain a `status != 'needs-review'` clause without a join. | park = needs-review skipped by scan | Work discovery already joins another table, or `status` is not on `documents`. |
| A9 | The `documents` row carries `content_hash` and `status`; `status='needs-review'` is already used by capture (needs-summary). | overloaded needs-review | `documents` lacks a `status` column, or `needs-review` collides destructively with capture's use. |
| A10 | `row_id` returned by `capture_upload` (`api.py:201/259`) IS the `documents.id` (verified `capture.py:216/406/484`). | live-enqueue pushes the doc id | The returned id is a different identifier (e.g. a batch id). |
| A11 | The Upload Handler runs inside a Starlette request and can reach `request.app.state` at the point capture returns. | live-enqueue via app.state | `request.app.state` is unavailable in the handler, or the queue cannot be stored there. |
| A12 | `_wrap_lifespan` (`cloud_entry.py:96`) creates the `asyncio.Queue` (`:118`) inside the composed lifespan; storing it on `app.state` there is reachable by handlers at request time. | A1 chosen (queue on app.state) | The lifespan runs after requests can arrive, or `app.state` set in the lifespan is not visible to handlers. |
| A13 | `_delete_with_blob_cleanup` (`api.py:330`) receives the document by **path**; the document's id must be looked up (`get_by_path`) BEFORE `delete_by_path` removes the row. | source-prune by id | The delete signal already carries the id, or the row is still readable after delete. |
| A14 | There is no existing query that finds `knowledge_entries` whose JSON `sources` text contains a given id; SQLite JSON support in the deployed build is unconfirmed. | OQ-P8B-01 | A `json_each`/`LIKE` query already exists and is proven on the deployed SQLite. |
| A15 | `providers.classify` in `config.yaml` (`:63`) currently maps to `claude_cli`; M2 re-points it at the OpenAI-compatible (DeepSeek) provider via config, not code. | reuse classify task name (Grid C) | The mapping cannot be changed without a code edit, or the OpenAI-compat provider config lacks model/endpoint. |
| A16 | Migration auto-discovery (`db.py` glob) applies a dropped `011_*.sql` with no registry edit; current `schema_version` is 10. | migration 011 cascade | Migrations require a registry entry, or the current version is not 10. |

---

## Component dependency order

This documents what must exist before each component can work — not the order a developer writes code (that is `/plan-from-specs`' job). IDs continue the **P8-CLS-B** prefix and align to the existing `behavior_inventory.yaml` entries P8-CLS-B-01…12.

---

### 1. Migration 011 — retry-state columns (P8-CLS-B-07 schema support)

**Goal.** Give each document a place to remember how many times its classification has failed and what went wrong last time, so retries can be self-correcting and bounded.

**Build.** Add a versioned migration file `storage/migrations/011_*.sql` adding two columns to `documents`: `classify_attempts` (INTEGER, default 0) and `classify_last_error` (TEXT, nullable). Follow the format of `010_classify_content_hash_and_ranking.sql`. Bump the prior version-pin assertions in `tests/test_storage/test_migration_007.py`, `_008.py`, `_009.py`, `_010.py` from `10` → `11`.

**Depends on.** None (foundation — every retry-aware component reads these columns).

**Assumes.** A16.

**Done when.** After `init_db`, the schema version is 11; a `documents` row reports `classify_attempts == 0` by default and `classify_last_error` is null; pre-existing rows survive the migration intact.

---

### 2. Config — `classify.max_retries` (K) (P8-CLS-B-07 support)

**Goal.** Make the retry cap a tunable, not a number baked into the code.

**Build.** Add `max_retries: int` (with a sensible default, e.g. 3, `ge=1`) to `ClassifyConfig` in `core/config.py:336`, alongside `max_content_tokens` / `max_entries_per_dimension`; add the matching key to the `classify:` block in `config/config.yaml`. It is an int (not a confidence float) so it does not belong in `thresholds.yaml`, but the no-literal rule still binds downstream (C-06 spirit).

**Depends on.** None.

**Done when.** The cap is readable as `config.classify.max_retries`, defaults to the chosen K, and a YAML override takes effect; no Slice B code compares the try-count against a literal.

---

### 3. Extraction Prompt — `prompts/entity_extract.yaml` (P8-CLS-B-01 support)

**Goal.** Give the AI a single, version-controlled instruction set for pulling structured facts out of one document for one category — and a place to tell it what it got wrong last time.

**Build.** Create `prompts/entity_extract.yaml` (the ONLY prompt source for extraction; never an inline f-string — C-07). It must render: the document text; the per-dimension `guidance` from `dimensions.yaml`; the existing-facts-with-ids block (each known fact shown with its database id, so the AI can target updates/retirements by id and omit the id for new facts); and `previous_attempt_feedback` (empty on the first attempt, the saved last-error on a retry). The reply contract is a structured JSON list of facts, each carrying an action (new / update / retire), a referenced id where applicable, and the fact fields (entity, tag, fact text, confidence). Mirror the JSON-only, no-markdown style of `classify.yaml`.

**Depends on.** None (a YAML asset). Its render variables come from components 4 and 5 at call time.

**Decisions.**
- Q: Exact reply schema (field names, retire-reason field, how `new`/`update`/`retire` is expressed)? Options: explicit `action` field per fact / separate lists per action. Leaning per-fact `action` because it keeps one flat list the Entry Writer iterates. Resolve in planning/research against the Entry Writer's parse.

**Done when.** Rendering the prompt with a document, a dimension's guidance, a list of existing facts with ids, and a feedback string produces a `(system, user)` pair where the existing facts appear with their ids, the guidance appears, and the feedback appears (or is cleanly absent when empty).

---

### 4. Entity Extractor (P8-CLS-B-01)

**Goal.** Ask the AI one focused question per knowledge category and turn its reply into structured facts the writer can apply — failing loudly and recoverably when the reply is unusable.

**Build.** A new function in `pipelines/classify.py` that, for one dimension, renders `PROMPTS["entity_extract"]` (component 3), calls `get_provider("classify", config).complete(system, user)` (C-08), and JSON-parses the reply into structured facts. Mirror the old `classify()` error pattern (`classify.py:137-251`): a template-render error → `Failure(recoverable=False)`; a provider failure, JSON parse error, or field-validation error → `Failure(recoverable=True)`; truncate the raw reply to 200 chars in the failure context. Returns `Result` (C-12).

**Depends on.** Component 3 (prompt), Context Loader (existing — supplies the existing-facts-with-ids), Content Reader (existing — supplies the text).

**Assumes.** A6 (not directly), A15 (the `classify` task resolves to a working provider).

**Interface shape.** Caller sees `extract(dimension, text, existing_facts, guidance, feedback, config) -> Result[list[fact]]`; the prompt rendering + provider dispatch + parse are hidden. One caller (the orchestrator) — a plain `Result`-returning function, not a protocol (`[closed]`, matches Slice A seam policy).

**Done when.** For a dimension whose AI reply is valid JSON, the extractor returns the parsed facts; for an unparseable or field-incomplete reply it returns a recoverable `Failure` carrying a ≤200-char snippet of the raw reply; for a template error it returns a non-recoverable `Failure`.

---

### 5. Entry Writer (P8-CLS-B-02, P8-CLS-B-03, P8-CLS-B-09)

**Goal.** Apply the extracted facts to the Fact Store safely — adding, editing, or retiring — so that re-running after a partial failure does not corrupt or duplicate the table.

**Build.** A new function in `pipelines/classify.py` that, given the parsed facts and the current document id, routes each fact by action:
- **new** → before inserting, call `query_by_entity` (`knowledge_entries.py:143`) and fold an exact `dimension + entity + tag` non-retired match into an **update** instead of creating a twin (D6); otherwise `upsert` a fresh row with this document's id in `sources`.
- **update** → read the referenced entry's existing `sources`, append this document's id, dedupe, and `upsert` the merged list — because `upsert`'s UPDATE overwrites `sources` wholesale (`:71`); the DB does not merge (D2/A3).
- **retire** → `retire(id, reason)` (`:159`) — never deletes (A5).
- On every write, re-gate the fact's `status` from its confidence via `confidence_to_status` (`tags.py:255`) — never a float compare (C-06).
- A referenced id that does not exist (a hallucination) is **skipped and logged**, and reported up so the orchestrator withholds the stamp (D9).

Returns `Result` (C-12). The function must distinguish "all facts written cleanly" from "one or more facts skipped" so the orchestrator can decide whether to stamp.

**Depends on.** Component 1 (no — uses Fact Store only), Component 4 (the facts it applies), Fact Store CRUD (existing).

**Assumes.** A3, A4, A5, A6.

**Interface shape.** Caller sees `write_entries(facts, doc_id, dimension, band, db_path) -> Result[...]` where the result signals clean-vs-skipped; insert/update/retire routing, source merge, dedup, and status re-gate are hidden. One caller (the orchestrator) — plain `Result` function, not a protocol (`[closed]`). (Design note: the one component with real internal logic; kept a function in `classify.py` because it has a single caller — a 1-adapter seam would be speculative.)

**Decisions.**
- Q: On `update` that lowers confidence, does the fact demote to `pending`? Options: re-gate on every write (matches locked broad-grill) / only ever promote. Leaning re-gate on every write — see OQ-P8B-02. Not a blocker.

**Done when.** Applying a `new` fact that exactly matches an existing non-retired `dimension+entity+tag` adds NO second row and folds into the existing one with the new source id appended; applying an `update` to an existing id leaves its prior sources intact plus this document's id (deduped); applying a `retire` flips the entry to `retired` without deleting it; a fact referencing a non-existent id is skipped, logged, and surfaced so the document is not stamped; every written fact's status matches `confidence_to_status` for its confidence.

---

### 6. Orchestrator (P8-CLS-B-04, P8-CLS-B-05, M1 correlation id)

**Goal.** Run one document end-to-end — per category extract → write → audit — and mark it done only when everything succeeded; otherwise hand it to the retry loop.

**Build.** A new function in `pipelines/classify.py` invoked by the consumer at the Slice B seam (`classify.py:431`). Per document: call `new_correlation_id()` once (`logging_setup.py:55`) before any audit (M1 / A7); read the text (Content Reader), load facts per dimension (Context Loader), load the document's `classify_attempts` + `classify_last_error`. Then **for each dimension**: Entity Extractor → Entry Writer → one `core.audit.write(decision, pipeline="classify", stage=<dimension>, outcome=...)` (C-13). After all dimensions: if every dimension fully succeeded (facts written cleanly AND audit recorded), call `stamp_classified(doc_id)` (`documents.py:659`) exactly once and clear the retry state (component 7); otherwise hand off to the retry loop (component 7) — do NOT stamp (D4 + D9). Returns `Result` (C-12).

**Depends on.** Components 4, 5, 7 (retry hooks), Content Reader, Context Loader, Classified-Stamp, audit, correlation id (all existing or above).

**Assumes.** A6, A7, A10.

**Interface shape.** Caller (the consumer) sees `orchestrate(doc_id, config, db_path) -> Result[...]`; the per-dimension loop, audit, stamp/retry decision are hidden. `[closed]` — the natural completion of the consumer loop, one file, seam at the queue (design Grid D).

**Done when.** For a document whose every category succeeds, exactly one `stamp_classified` runs, its `classify_content_hash` now equals its `content_hash` (Work Finder no longer returns it), one audit record exists per dimension, and the run carried a single fresh correlation id; for a document where any category fails, no stamp runs and the document is handed to the retry loop.

---

### 7. Retry loop (P8-CLS-B-07, P8-CLS-B-08)

**Goal.** Make a malformed AI reply self-correcting and bounded — feed back what failed, cap the attempts, and park rather than loop forever — with durable state that survives a crash.

**Build.** On a failed orchestrator run: save `classify_last_error` (the failure reason) and increment `classify_attempts` on the document row; if `classify_attempts >= config.classify.max_retries` (K), set `status = 'needs-review'` and write a "parked" audit record (D10). On a successful run: clear both columns (set attempts to 0, last-error to null). Add a `status != 'needs-review'` filter to `find_unclassified` (`documents.py:634`) so parked documents are not re-queued (A8). The Extraction Prompt (component 3) renders `previous_attempt_feedback` from `classify_last_error` (empty on the first attempt) (D11). New helper(s) on `storage/documents.py` to read/update the retry columns; the orchestrator drives them.

**Depends on.** Component 1 (the columns), Component 2 (K), Component 6 (the orchestrator drives it).

**Assumes.** A8, A9.

**Decisions.**
- Q: Is `needs-review` for a parked classify safely distinguishable from capture's needs-summary use? Leaning yes (distinguishable by `classify_attempts > 0`) — accepted overload per design "Known tradeoffs". Verify in research that no capture path keys off `needs-review` in a way that collides.

**Done when.** A document that fails K times ends with `status = 'needs-review'`, `classify_attempts == K`, a saved `classify_last_error`, and a "parked" audit record — and the Work Finder no longer returns it; a document that fails fewer than K times keeps `status` unchanged, its attempts incremented, its last-error saved, and is still returned by the Work Finder; on the retry the AI receives the saved last-error as feedback; a document that succeeds has both retry columns cleared.

---

### 8. Live-enqueue seam (P8-CLS-B-06)

**Goal.** Classify a document the moment it is captured while the container runs — not at the next reboot.

**Build.** In the composed lifespan (`cloud_entry.py:_wrap_lifespan`), store the `asyncio.Queue` on `app.state.classify_queue` instead of a local variable (`:118`). In the Upload Handler (`mcp_server/api.py:upload_handler`), after a successful `capture_upload` returns the `row_id` (= doc id, A10), reach the queue via `request.app.state` and call `queue.put_nowait(row_id)`. If the queue is absent (CLI runs, tests) → skip silently; the Catch-up Scan remains the net (D1, A11/A12).

**Depends on.** Components 6/7 (so an enqueued id is actually processed), the existing queue + lifespan + Upload Handler.

**Assumes.** A10, A11, A12.

**Done when.** With the app running, a successful upload results in the new document id appearing on the Work Queue and being classified without a restart; running capture with no queue present (CLI/test) does NOT error and the document is still picked up by the next Catch-up Scan.

---

### 9. Source-prune on delete (P8-CLS-B-10)

**Goal.** When a source document is deleted, shrink provenance correctly instead of leaving facts pointing at a document that no longer exists — and never silently destroy a fact.

**Build.** In `_delete_with_blob_cleanup` (`api.py:330`): look up the document's id by path via `get_by_path` **before** `delete_by_path` removes the row (the delete signal arrives by path, but `sources` holds ids — A13). Then for every non-retired entry whose `sources` contains that id, remove the id, dedupe, and if the list becomes empty set `status = 'pending'` — never auto-delete, never auto-retire (D3). Finding entries by a contained id is a scan-and-filter in code for this slice (OQ-P8B-01).

**Depends on.** Fact Store CRUD, the existing delete path.

**Assumes.** A13, A14.

**Decisions.**
- Q: Scan-and-filter in Python vs a SQLite JSON query to find facts by contained source id? Leaning scan-and-filter — see OQ-P8B-01. Not a blocker.

**Done when.** Deleting a document that was a source for several facts leaves each of those facts without that id in its `sources` (and unchanged otherwise); a fact that had ONLY that document as a source ends with empty sources and `status = 'pending'` — not deleted, not retired; deleting a document that backs no facts changes no facts.

---

### 10. Code deletion — retire the old folder-routing classify (P8-CLS-B-11, P8-CLS-B-12)

**Goal.** Remove the dead "which folder?" classify so the module's single meaning is "extract knowledge facts" — and remove the tests and dead fixture that only exercised it.

**Build.** Delete from `pipelines/classify.py`: `build_subject` (`:22`), `build_folder_subject` (`:52`), `_destination_names` (`:70`), `ClassifyResult` (`:97`), `classify` (`:113`). Keep the Slice A functions in the same file (`content_reader` `:259`, `context_loader` `:320`, `consumer` `:388`, `catch_up_scan` `:446`) and the new Slice B functions. Delete `tests/test_pipelines/test_classify.py` (33 tests — imports exactly these symbols). Delete the dead `_stub_classify` block + the `ClassifyResult` import in `tests/test_pipelines/conftest.py:79-96` (verified dead — `pipelines.capture` has no `classify` attribute and no test requests the `pipeline_ctx` fixture; the monkeypatch would `AttributeError` if ever exercised). No separate fixture repair is needed.

**Depends on.** Components 4–7 should exist first (so the module is not left without a classify behavior between delete and rebuild — a planning/ordering concern, not a hard runtime dependency).

**Assumes.** A1, A2.

**Done when.** None of the five old symbols remain in `pipelines/classify.py`; `test_classify.py` is gone; the dead conftest block is gone; the full suite is green (the only expected breakage is the deleted file, and the version-pin cascade from component 1).

---

## Handoff notes

- **Contract with Phase 9 / 10:** `trust_score` and `retrieval_count` remain inert in this slice; Slice B writes facts whose ranking fields keep their Slice A defaults. Phase 9 increments `retrieval_count`; Phase 10 populates `trust_score`. Do not populate them here.
- **Contract with deploy (M2):** the code calls `get_provider("classify", config)`; which model that resolves to is set in `config/config.yaml` `providers.classify` (currently `claude_cli`). Research/plan must confirm the OpenAI-compatible (DeepSeek) provider config (model + endpoint) is set before the first real classify call, or it hits the wrong model. Not a code change in this slice.
- **Open uncertainty — finding facts by contained source id (A14/OQ-P8B-01):** no existing query does this; SQLite JSON support on the deployed build is unconfirmed. The recommendation is scan-and-filter in Python (zero dependency risk). Research should confirm the fact count is small enough at single-user scale and whether `json_each`/`LIKE` is worth it later.
- **Open uncertainty — status re-gate on update (OQ-P8B-02):** re-gating on every write can demote a confident fact when a later low-confidence mention updates it. The locked behavior is re-gate-on-every-write; flag for observation, not a blocker.
- **Suggested research topics:** (1) verify the Entity Extractor reply schema against the Entry Writer's parse before planning (component 3 decision); (2) confirm `request.app.state` reachability and that `put_nowait` on the unbounded queue cannot block/raise under load (design risk #4); (3) verify the `needs-review` overload does not collide with any capture path that keys off that status (component 7 decision); (4) confirm DeepSeek/OpenAI-compatible prompt-caching support before any caching work (TD-067; out of scope here but informs M2).
- **Catch-up backlog (OQ-P8B-03 / OQ-P8A-03):** in Slice B each enqueued item now costs an AI call, so a large unclassified vault at boot floods the queue with paid work. The sequential consumer rate-limits token spend naturally, so one-burst is kept; paging is a later optimization. A TD entry should be (re)confirmed during implementation.

---

## Open questions (carried from design — for research to verify, not blockers)

**OQ-P8B-01 — How to find facts by a contained source id.**
A fact's source documents are stored as a JSON list of ids in a single text column; no query finds "every fact whose source list contains id 9". When a document is deleted, do we scan every non-retired fact and filter in code, or use a SQLite JSON query? Scan-and-filter is simplest and dependency-free but reads every non-retired fact per delete; a JSON query is faster but depends on the deployed SQLite's JSON support. **Design recommendation: scan-and-filter in code for Slice B** — negligible at single-user fact counts, swappable later without behavior change. Not a blocker.

**OQ-P8B-02 — Does an update that lowers confidence demote a fact's status.**
Status is re-computed from confidence on every write, including updates. If a new lower-confidence mention updates a previously-confident fact, should it drop to `pending`? Re-gating matches the locked "confidence is a live signal" behavior but can demote an established fact from one hedged mention; only-ever-promote is stabler but never reflects genuine new uncertainty. **Design recommendation: re-gate on every write (locked broad-grill behavior); flag for observation** — a "max confidence seen" rule is a later refinement if demotion thrashes. Not a blocker.

**OQ-P8B-03 — Should the startup catch-up scan page its enqueues.**
The Catch-up Scan enqueues every unclassified id in one burst; in Slice B each item costs an AI call. Page/batch the enqueues, or keep one burst? One burst is simplest and the sequential consumer drains one at a time (memory is the only concern); paging smooths a huge backlog's startup memory but adds code. **Design recommendation: keep one burst for Slice B** (carries OQ-P8A-03); the sequential consumer naturally rate-limits token spend; paging is a later optimization. Not a blocker.

---

## Next step

Spec written. Run `/research` to verify the assumptions above (A1–A16) against real code before planning.
