# Phase 8 Slice A — Classify Infrastructure (no LLM calls)

_Created: 2026-06-14_
_Status: Spec — ready for `/research`_
_Source design: `docs/1_design/phase8_sliceA_classify_infra.md` (Option A chosen)_
_Locked requirements: `docs/0_draft/phase8/phase8_classify_redesign_grill.md` (Slice A)_
_Direction source of truth: `docs/0_draft/cloud_native_rearchitecture.md` §7_
_Behavior IDs: **P8-CLS-A-01 … P8-CLS-A-07** (already written in `docs/system_behavior/behavior_inventory.yaml` — reference, do not duplicate)_
_Related ADR: ADR-0017 (in-memory asyncio queue + `classify_content_hash` work discovery)_

---

## Purpose

Build the **plumbing** for the new classify pipeline — the machinery that finds which captured documents still need their facts extracted, loads the right inputs for that extraction (the document's text, the knowledge categories, and the facts already known), and runs a simple in-memory queue that hands one document at a time to a background worker. After this phase the system can *discover, queue, and prepare* a document for classification — but it deliberately makes **no AI calls** (fact extraction is Slice B). Everything Slice A adds is testable with no AI, no network, and no cost.

What the system can do after Slice A that it couldn't before: at container startup it scans for unclassified (or content-changed) documents, enqueues them, and a single worker pulls each one and assembles the exact inputs an extractor will later need — stopping cleanly right before the AI step.

---

## Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| Dimension loader | `core/tags.py::load_dimensions` (line 147) | Loads `dimensions.yaml` into a dict | **Extended** to carry the new nested `{tags, guidance}` shape | deep |
| Dimension validator | `core/tags.py::validate_dimension_tag` (line 159) | Checks a (dimension, tag) pair against the rulebook | **Extended** — must read tags from `rulebook[dimension]["tags"]`, not `rulebook[dimension]` | deep |
| Confidence→status mapper | `core/tags.py::confidence_to_status` (line 189) | Maps a confidence score to `confident`/`pending` via a band | Reused unchanged by the ranker's status semantics (no change needed) | deep |
| Knowledge-entry store | `storage/knowledge_entries.py` | CRUD for `knowledge_entries` (5-function contract) | `KnowledgeEntry` dataclass + `_row_to_entry` **extended** for two new columns; **new ranked query added** | deep |
| Live-facts query | `storage/knowledge_entries.py::get_confident_and_pending` (line 171) | Returns non-retired entries, optionally filtered, unranked/uncapped | **Reference only** — Context Loader adds a *new* ranked+capped query alongside it (does NOT extend it; OQ-P8A-02) | deep |
| Documents store | `storage/documents.py` | Primary store for captured files (`upsert_from_upload`, `get_by_path`, `DocumentRow`) | Add work-discovery query + classify-stamp function; `DocumentRow` gains `classify_content_hash` | deep |
| `DocumentRow` dataclass | `storage/documents.py:34` | Mirrors one `documents` row | Gains a `classify_content_hash` field + `_row_from_sqlite` reads it | deep |
| Connection factory | `storage/db.py::get_connection` / `init_db` | SQLite connection (FK pragma on) + migration runner | New queries reuse it; migration 010 runs through `init_db` | deep |
| Container entry / app factory | `mcp_server/cloud_entry.py::build_app` (line 43) | Builds the Starlette app at container boot; runs under uvicorn's event loop | **Hosts** the queue + consumer + catch-up scan via a **composed outer lifespan** that wraps the framework's session-manager lifespan (NOT a Starlette `on_startup` handler — proven no-op; NOT the per-chat MCP lifespan) | deep |
| Capture "ready" log line | `pipelines/capture.py:267` and `:482` | Emits `capture.classify_ready` after a file is captured | **Documented seam only** — left as-is in Slice A; Slice B replaces with `queue.put(doc_id)` | shallow |
| CaptureConfig sub-model | `core/config.py::CaptureConfig` (line 315) | Typed `capture:` config block | New `classify:` config block follows the same Pydantic-sub-model pattern | deep |
| Result type | `core/result.py` (`Success` / `Failure`) | No-silent-failure return type | Every new public function returns `Result` (C-12) | deep |
| Existing dimensions config | `config/dimensions.yaml` | Flat `dimension → [tags]` provisional starter | **Rewritten** to nested `{tags, guidance}` + richer tags | shallow |

---

## Q1 Diagram (from design) — what happens inside

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

## Q2 Diagram — how it connects to others

```
# Phase 8 Slice A — Classify Infrastructure: How It Connects
Scope: Shows how the new classify plumbing connects to the rest of the system.
       Does NOT show the internal step-by-step flow (see Q1 for that).

How to read this:
  Solid boxes    = built in Slice A (this phase)
  Dashed boxes   = deferred to Slice B (not built yet)
  Arrow labels   = what passes between components
  ──────►        = built now
  - - - ►        = future seam, wired in Slice B

                          ┌─────────────────────┐
                          │ Container Startup    │
                          │ Boots the cloud app, │
                          │ starts the machinery │
                          └─────┬───────────┬────┘
                  starts Worker │           │ triggers
                                │           ▼
                                │   ┌──────────────────┐      asks
                                │   │ Catch-up Scan    │──────────────┐
                                │   │ Finds docs still │  "which docs  │
                                │   │ needing classify │   need work?" │
                                │   └────────┬─────────┘               │
                                │            │ uses                    │
                                │            ▼                         │
                                │   ┌──────────────────┐               │
                                │   │ Work Finder      │               │
                                │   │ Query: which     │               │
                                │   │ docs need work   │               │
                                │   └────────┬─────────┘               │
                                │            │ fills queue with ids    │
                                │            ▼                         │
                                │   ┌──────────────────┐               │
                                └──►│ Work Queue       │               │
   ┌ ─ ─ ─ ─ ─ ─ ─ ┐  - - - ►      │ Waiting doc ids, │               │
   │ Capture        │  future seam  │ one at a time    │               │
   │ Emits "ready   │ "ready to     └────────┬─────────┘               │
   │ to classify"   │  classify"             │ one id at a time        │
   └ ─ ─ ─ ─ ─ ─ ─ ┘                         ▼                         │
                                    ┌──────────────────┐               │
                  ┌─────────────────┤ Worker           │               │
                  │   ┌─────────────┤ Pulls one doc,   ├──────────┐    │
                  │   │             │ prepares inputs  │          │    │
                  │   │             └────────┬─────────┘          │    │
        calls     │   │ calls               │ calls              │    │
                  ▼   │                      ▼                    ▼    │
      ┌──────────────┐│           ┌──────────────────┐  ┌──────────────┐
      │ Content      ││           │ Dimension Loader │  │ Context      │
      │ Reader       ││           │ Reads categories │  │ Loader       │
      │ Full text or ││           │ + guidance       │  │ Ranks & caps │
      │ summary      ││           └────────┬─────────┘  │ known facts  │
      └──────┬───────┘│                    │ reads      └──────┬───────┘
             │ reads  │                    ▼                   │ reads
             │        │           ┌──────────────────┐         │
             │        │           │ Knowledge        │         │
             │        │           │ Categories config│         │
             │        │           └──────────────────┘         │
             │        │ hands prepared inputs to               │
             │        ▼                                        │
             │   ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐                         │
             │   │ Housekeeping AI    │  (Slice B —             │
             │   │ (Slice B)          │   extracts facts)       │
             │   │ Extracts facts     │                         │
             │   └ ─ ─ ─ ┬ ─ ─ ─ ─ ─ ┘                         │
             │           │ on success (Slice B)                 │
             │           ▼                                      │
             │   ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐                         │
             │   │ Classified-Stamp   │ marks doc done          │
             │   │ (Slice B happy     │                         │
             │   │  path)             │                         │
             │   └ ─ ─ ─ ┬ ─ ─ ─ ─ ─ ┘                         │
             │           │ stamps                               │
             ▼           ▼                                      ▼
      ┌──────────────────────────────┐          ┌──────────────────────┐
      │ Document Store               │          │ Fact Store           │
      │ Captured files, text,        │          │ Structured facts     │
      │ summaries, fingerprints,     │          │ (now with trust-     │
      │ + new classify-fingerprint   │          │  score & retrieval)  │
      └──────────────┬───────────────┘          └───────────┬──────────┘
                     │                                       │
                     │        ┌──────────────────┐           │
                     └───────►│ Schema Update    │◄──────────┘
                  adds new    │ Adds new columns │  adds new
                  column      │ + indexes        │  columns
                              └──────────────────┘
```

```
Simplified: The Worker's three preparation helpers (Content Reader, Dimension
            Loader, Context Loader) are shown as separate boxes here to make
            their data sources visible — Q1 grouped them into one "Prepare
            inputs" step. The Schema Update is foundational: it underpins both
            stores rather than being called at runtime.
```

**Glossary (plain English → code home, for the planner only):**
Schema Update → `storage/migrations/010_*.sql`; Dimension Loader → `core/tags.py`; Knowledge Categories config → `config/dimensions.yaml`; Content Reader / Context Loader / Work Finder / Classified-Stamp → new functions (classify-infra in `pipelines/classify.py` + two in `storage/documents.py`); Fact Store → `storage/knowledge_entries.py`; Document Store → `storage/documents.py`; Container Startup → `mcp_server/cloud_entry.py::build_app`; Work Queue + Worker → `asyncio.Queue` + consumer coroutine.

---

## Feature overview

**Happy path (Slice A).** When the container boots, a startup hook starts a single background worker and runs a one-time **catch-up scan**. The scan runs the **Work Finder** query — "which documents have never been classified, or have changed since they were?" — and puts each matching document id onto an in-memory **Work Queue**. The single **Worker** loop pulls one id at a time (never two at once) and prepares that document's inputs:

1. **Content Reader** decides whether to use the document's full extracted text or its short summary, based on an approximate size check against a configured token budget.
2. **Dimension Loader** loads the knowledge categories — each with its allowed tags and its per-category guidance text.
3. **Context Loader** loads the facts already known in each category, drops retired ones, ranks them (most-trusted → most-confident → most-recent), and caps the count per category.

In Slice A the worker stops right there — it has assembled everything an extractor would need, but makes no AI call. The **Classified-Stamp** function (which marks a document done so the next scan skips it) exists and is tested, but is only *called on the happy path* once Slice B's AI extraction lands. Because nothing is stamped in Slice A, a re-run of the scan re-discovers the same documents — which is correct and harmless.

**Edge cases.**
- **Document changed after a prior classify** — its classify-fingerprint no longer matches its content fingerprint, so the Work Finder re-discovers it.
- **Big document** — Content Reader falls back to the summary so a later prompt never blows the token budget.
- **Category with more facts than the cap** — Context Loader returns only the top-ranked N.
- **No facts yet in a category** — Context Loader returns an empty list (not an error).
- **Container restart mid-backlog** — the in-memory queue is lost, but the catch-up scan rebuilds the work-list from the durable classify-fingerprint markers; nothing is permanently lost.
- **Failure during preparation** — the worker leaves the fingerprint untouched, so the document is retried on the next scan (no dead-letter queue).

---

## Out of scope

- **Any LLM / AI call** — fact extraction, prompt rendering, JSON parsing of AI output. _Slice B._
- **Entity-extraction prompt** (`prompts/entity_extract.yaml`) and the per-dimension extractor. _Slice B._
- **Entry Writer** — routing new/update/retire actions, validating entry ids, writing facts. _Slice B._
- **Calling Classified-Stamp on the happy path** — the function is built and tested in Slice A, but only *invoked after a successful classify* in Slice B. _Slice B._
- **Wiring capture's push seam** — replacing the `capture.classify_ready` log line with `queue.put(doc_id)`. Slice A leaves the seam as-is, documented. _Slice B._
- **Audit logging of classify decisions** — no AI decisions happen in Slice A. _Slice B (per-dimension audit)._
- **Document-deletion source cleanup** — removing doc ids from `knowledge_entries.sources`. _Slice B (`/api/event` delete handler)._
- **Deleting the old folder-routing classify code** (`classify`, `ClassifyResult`, `build_subject`, `build_folder_subject`, `_destination_names`). Slice A *adds new functions alongside* this dead code; Slice B guts it. _Slice B._
- **Populating `trust_score` / `retrieval_count`** — both columns ship inert in Slice A. `trust_score` corrections arrive in Phase 10; `retrieval_count` increments arrive in Phase 9.
- **Paging / batching the catch-up scan's enqueue** — Slice A enqueues in one burst (OQ-P8A-03). _Deferred — tech-debt note to page if backlog grows._
- **Back-pressure / retry-count bounding on the queue** — no retry counter, no dead-letter queue. _Deferred._
- **A real tokenizer** — Content Reader uses a `chars / 4` estimate, not a token counter. _Deferred — intentional heuristic._

---

## Constraints

- **C-05 · All schema changes via versioned `.sql` deltas** — migration 010 must be a `.sql` file; never an in-code `ALTER`. Source: CLAUDE.md / CONSTRAINTS.md / hook.
- **C-06 spirit · No hardcoded tunables in pipelines** — the 10000-token content cap and the 50-entry per-dimension cap must come from config, never a literal in code. (They are config ints, not confidence floats, so the hook does not fire — but the rule still binds.) Source: CLAUDE.md / hook.
- **C-12 · Every public function in `pipelines/` and store layers returns `Success`/`Failure`** — Content Reader, Context Loader, Work Finder, Classified-Stamp, and the consumer must return `Result`. Source: CLAUDE.md / CONSTRAINTS.md.
- **C-17 · Never import `CONFIG` at module scope in tests** — new Slice A tests pass explicit `db_path` / config; no module-scope `CONFIG`. Source: CLAUDE.md / hook.
- **Migration version-pin cascade** — adding migration 010 bumps the schema version; the prior version-pin assertions must move `9` → `10` (see Assumptions A8). Source: CLAUDE.md "What Claude gets wrong → migrations".
- **In-process, same container as capture; single sequential consumer** — no separate worker process, no external broker. Source: ADR-0017 / rearch §7 / grill §2.
- **Worker starts at container build/startup, NOT in the MCP lifespan** — the lifespan fires per-chat-session; a background housekeeper must not wait for a chat client. Source: ADR-0017 (consequence #1) / CLAUDE.md Phase 5 gotcha.
- **C-04 · FK pragma on every new connection** — N/A: Slice A adds no new connection factory (reuses `get_connection`). Source: CLAUDE.md.
- **C-07 / C-08 / C-13 (prompts-as-YAML / provider factory / audit log)** — N/A in Slice A: no AI calls, no AI decisions. Source: design Guardrail Checklist.

---

## Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|-----------|-------------------|--------------------------|
| A1 | `documents` has a `content_hash` column the Work Finder can compare against | design §work-discovery; `DocumentRow.content_hash` exists | `content_hash` is missing/NULL on captured rows, so the comparison can't run |
| A2 | `documents.full_body` and `documents.summary` both exist and are readable for the Content Reader | design §content reader; migration 008 added `full_body`, `summary` predates it | Either column is absent or never populated by capture |
| A3 | `validate_dimension_tag` currently does `rulebook[dimension]` and treats it as the tag list (line 178) — the nested shape breaks this | design §dimensions; `core/tags.py:178` | The function already reads `["tags"]`, so no change is needed |
| A4 | The only tests pinning the flat `dimensions.yaml` shape live in `tests/test_core/test_dimensions.py` (P5-DATA-07/08), with an inline `RULEBOOK = {dim: [tags]}` fixture | design §blast radius; grep showed one file | Other tests/modules read `rulebook[dim]` as a flat list and break unflagged |
| A5 | `get_confident_and_pending` excludes retired (`status != 'retired'`) and is **not** ranked/capped — the new query genuinely adds ranking, not duplicates it | design §context loader; `knowledge_entries.py:181,191` | The existing function already ranks by trust/confidence and caps |
| A6 | `KnowledgeEntry` dataclass + `_row_to_entry` do **not** carry `trust_score`/`retrieval_count` today, so a ranked `SELECT *` cannot sort/return them until the dataclass learns them | design §ranking signals; `knowledge_entries.py:16,33` | The dataclass already has these fields |
| A7 | **[Resolved via research]** `build_app` is the correct place to start the worker at container boot (NOT the per-chat MCP lifespan) — but the mechanism is a **composed outer lifespan**, NOT a Starlette `on_startup` handler. `build_app` wraps the framework app's existing session-manager lifespan in an `@asynccontextmanager` that starts the worker + catch-up scan on entry and cancels the worker on exit, under uvicorn's loop. The original `on_startup` recommendation was disproven: the FastMCP-returned app already sets a custom lifespan (`fastmcp/server.py:1044`), and Starlette ignores `on_startup` whenever a lifespan is set (`starlette/routing.py:582-599`) — so `on_startup` would silently never run. | design §worker start + OQ-P8A-01; verified at `cloud_entry.py:43,78`; `fastmcp/server.py:1044`; `starlette/routing.py:582-599` | Now verified — the only residual unknown (composition shape: task-group vs `create_task` inside the lifespan body) is an implementation choice, not an open assumption |
| A8 | The version-pin assertions to bump `9`→`10` are: `test_migration_007.py:41,56`, `test_migration_008.py:47`, `test_migration_009.py:38` | CLAUDE.md migration gotcha; grep of `schema_version` | Other migration tests also assert a fixed version and break unflagged |
| A9 | `capture.classify_ready` is logged at `pipelines/capture.py:267` and `:482` with `vault_path` (not `doc_id`); `row_id` is in scope at those points | design §capture seam; capture.py grep | The log line is elsewhere, or `row_id` is not available at that point (affects Slice B only) |
| A10 | `init_db` runs numbered migrations in order through `get_connection`, so dropping `010_*.sql` in `storage/migrations/` is sufficient to apply it | design §migration; `cloud_entry.py` step 2 + `db.py` | Migrations are registered in an index that must also be edited |
| A11 | Confidence band for status semantics is reachable via `CONFIG.thresholds.for_pipeline("classify")` and unchanged by Slice A | `core/config.py:478`; design (status inert in ranker) | The ranker needs live status re-gating in Slice A (it does not — that's Slice B) |

---

## Component dependency order

_Documents what must exist before each component can work — not the order code is written. Execution order is owned by `/plan-from-specs`._

### 1. Schema Update (migration 010)

**Goal.** Add the durable database fields the rest of Slice A depends on: a per-document "have I been classified?" fingerprint, and two inert ranking signals on facts, plus two supporting indexes.

**Build.** A new versioned SQL delta (`storage/migrations/010_*.sql`):
- `documents.classify_content_hash` — TEXT, nullable (the classify-fingerprint).
- `knowledge_entries.trust_score` — REAL, DEFAULT 0.5 (inert in P8; Phase 10 populates).
- `knowledge_entries.retrieval_count` — INTEGER, DEFAULT 0 (inert in P8; Phase 9 populates).
- `CREATE INDEX IF NOT EXISTS idx_ke_trust ON knowledge_entries(trust_score DESC);`
- `CREATE INDEX IF NOT EXISTS idx_docs_classify_hash ON documents(classify_content_hash);`
- A comment in the file noting the two columns are intentionally inert in Phase 8.

**Depends on.** None (runs first).

**Assumes.** A1, A10.

**Decisions.**
- Q: Does `init_db` auto-discover numbered `.sql` files, or is there a registry/index to update too? Options: drop-file-only / edit-registry. Leaning drop-file-only because that's how 008/009 appear to work — confirm in research (A10).

**Done when.** Applying migrations to a database that already holds documents and knowledge entries leaves all existing rows intact and readable; afterward `documents` has a nullable classify-fingerprint column, `knowledge_entries` shows `trust_score` defaulting to 0.5 and `retrieval_count` defaulting to 0, and both new indexes exist. _(Behavior P8-CLS-A-07.)_

---

### 2. Expanded knowledge-categories config (`dimensions.yaml`) + loader/validator extension

**Goal.** Give each knowledge category a richer tag set **and** a per-category guidance text the extractor will later use, and teach the existing loader/validator to carry the new shape — without splitting the concept across two files.

**Build.**
- Rewrite `config/dimensions.yaml` from flat `dimension → [tags]` to nested `dimension → {tags: [...], guidance: "..."}`, using the richer defaults from the grill (§4) — people / projects / domains with their tag sets, every tag set keeping a mandatory `other`.
- Extend `core/tags.py::load_dimensions` to load the nested shape.
- Extend `core/tags.py::validate_dimension_tag` to read the allowed tags from `rulebook[dimension]["tags"]` instead of treating `rulebook[dimension]` as the list itself.
- Update the flat-shape tests in `tests/test_core/test_dimensions.py` (P5-DATA-07/08): the inline `RULEBOOK` fixture and assertions move to the nested shape. This is the **one backward-incompatible config-shape change** in Slice A (known, mechanical cascade).

**Depends on.** None (config + pure loader; independent of the migration).

**Assumes.** A3, A4.

**Interface shape.** Callers see: a rulebook dict where each dimension maps to `{tags, guidance}`. Hidden: YAML parsing. This deepens the existing `core/tags.py` module (the dimension authority) rather than adding a new seam — no new adapters.

**Decisions.**
- Q: Should the validator/loader fail loudly if a dimension is missing the mandatory `other` tag or its `guidance` block, or accept silently? Options: validate-on-load / accept-silently. Leaning validate-on-load because P8-CLS-A-04 expects a malformed config to be *rejected with a clear failure*, not silently accepted. Confirm exact validation surface (loader vs a separate check) in research.
- Q: Is `guidance` required per dimension, or optional with a default? Leaning required (it's the whole point of the change) — but planner should confirm against the grill's "prompt injection seam from day one."

**Done when.** Loading the expanded config exposes, for each category, its allowed tags and its guidance text; every tag set still includes the mandatory catch-all tag; and a config with a dimension missing its catch-all (or otherwise malformed) is rejected with a clear failure rather than silently accepted. _(Behavior P8-CLS-A-04.)_

---

### 3. Knowledge-entry ranking support (`KnowledgeEntry` + new ranked query)

**Goal.** Make facts sortable by the new ranking signals and give the Context Loader a query that returns ranked, capped, non-retired facts per category — each carrying its database id.

**Build.**
- Add `trust_score: float` and `retrieval_count: int` fields to the `KnowledgeEntry` dataclass (`storage/knowledge_entries.py:16`), and have `_row_to_entry` read both columns.
- Add a **new** ranked+capped query function (do NOT extend `get_confident_and_pending` — OQ-P8A-02). Query shape:
  `SELECT ... FROM knowledge_entries WHERE status != 'retired' AND dimension = ? ORDER BY trust_score DESC, confidence DESC, updated_at DESC LIMIT ?`
- The function returns `Result[list[KnowledgeEntry]]`; the cap (`LIMIT`) value comes from config (`classify.max_entries_per_dimension`), never a literal.
- `[UNVERIFIED — research]` Whether `knowledge_entries.upsert` INSERT/UPDATE column lists need the two new columns added so future writes set explicit values; DB defaults cover omitted inserts, but a `SELECT *`-based round-trip must still populate the dataclass.

**Depends on.** Component 1 (the columns + index must exist).

**Assumes.** A5, A6.

**Interface shape.** Callers (the Context Loader, component 5) see one new query function returning ranked `KnowledgeEntry` objects. The 5-function CRUD contract of the store stays stable; ranking is the only new concern. One caller only → internal query, not a new public seam.

**Dependency category.** in-process (test directly against a temp SQLite DB with seeded rows).

**Done when.** Each `KnowledgeEntry` round-trips its trust-score and retrieval-count from the DB, and the new query, run against a category holding more facts than the cap, returns no retired facts, returns them ordered by trust then confidence then recency, returns no more than the configured cap, and carries each fact's database id. _(Supports behavior P8-CLS-A-05.)_

---

### 4. Classify config block (`classify.max_content_tokens`, `classify.max_entries_per_dimension`)

**Goal.** Make the two Slice A tunables configurable, per C-06 spirit.

**Build.**
- Add a `ClassifyConfig` Pydantic sub-model in `core/config.py` (mirroring `CaptureConfig`'s pattern) with:
  - `max_content_tokens: int` (default 10000)
  - `max_entries_per_dimension: int` (default 50)
- Wire it into `MainConfig` as `classify: ClassifyConfig = Field(default_factory=ClassifyConfig)`.
- Add the `classify:` block to `config/config.yaml`.

**Depends on.** None (pure config). Components 3, 5, 6 read its values.

**Assumes.** A11 (band reachable separately for status semantics; not needed by Slice A reads).

**Decisions.**
- Q: Should `classify` reuse the existing `thresholds.yaml` band wiring, or stay a plain `config.yaml` block? Leaning plain block — these are sizing ints, not confidence floats, so they don't belong in `thresholds.yaml`.

**Done when.** Both caps are readable from config (defaulting to 10000 tokens and 50 entries) and no Slice A code compares against a literal cap value.

---

### 5. Content Reader + Context Loader (classify-infra functions in `pipelines/classify.py`)

**Goal.** Build the two preparation helpers that turn a document id into the inputs an extractor will later consume — choosing text-vs-summary by size, and loading ranked+capped facts per category.

**Build.** Add these as **new functions** in `pipelines/classify.py`, alongside the soon-to-die folder-routing code (kept out of that dead code; not a new module — see design module-depth note):
- **Content Reader** — given a document id (or `DocumentRow`), read `full_body` and `summary`; if `len(full_body) // 4 < CONFIG.classify.max_content_tokens` use `full_body`, else use `summary`. The `// 4` is the chars→tokens estimate. Returns `Result` carrying the chosen text. The comparison reads the config int (component 4), never a literal.
- **Context Loader** — for each configured dimension, call the new ranked query (component 3) with the per-dimension cap (component 4), returning the ranked+capped non-retired facts, each with its id. Returns `Result`.

**Depends on.** Components 1, 2, 3, 4.

**Assumes.** A2, A5, A6.

**Interface shape.** Each has exactly **one** caller (the consumer, component 6) — internal pipeline stages, NOT public interfaces with multiple adapters. Per the design lens: plain functions returning `Result`, no protocols.

**Dependency category.** in-process (test directly: seed a temp DB + config, call the function, assert on the returned text / fact list).

**Done when.**
- Content Reader: a document whose full text fits under the configured budget yields its full text; a document whose full text exceeds the budget yields its summary instead. _(Behavior P8-CLS-A-03.)_
- Context Loader: for a category holding more facts than the cap, it returns no retired facts, ordered by trust → confidence → recency, capped at the configured maximum, each fact carrying its database id. _(Behavior P8-CLS-A-05.)_

---

### 6. Work Finder + Classified-Stamp (functions in `storage/documents.py`)

**Goal.** Give classify a way to discover its own work and a way to mark a document done — the durable backbone of the queue's retry/skip behavior.

**Build.** Add two new functions to `storage/documents.py`:
- **Work Finder** — `SELECT id FROM documents WHERE classify_content_hash IS NULL OR classify_content_hash != content_hash`. Returns `Result[list[int]]` of document ids needing classification.
- **Classified-Stamp** — set `classify_content_hash = content_hash` for one document id. Returns `Result` (rowcount or id). Built and unit-tested in Slice A; **not called on the happy path until Slice B** (no successful classify happens without the AI).
- Add `classify_content_hash` to `DocumentRow` and have `_row_from_sqlite` read it (defensively, `if "classify_content_hash" in row.keys()`).

**Depends on.** Component 1 (the column + index must exist).

**Assumes.** A1.

**Dependency category.** in-process (test directly: seed documents in mixed states, assert which ids are returned / skipped).

**Done when.**
- Work Finder, run against a DB with one NULL-fingerprint document, one fingerprint-mismatch document, and one fingerprint-match document, returns the first two and not the third. _(Behavior P8-CLS-A-01.)_
- After Classified-Stamp runs on a document, work discovery no longer returns it; a document left unstamped is still returned (the retry path). _(Behavior P8-CLS-A-02.)_

---

### 7. Work Queue + Worker + catch-up scan (started from `build_app`)

**Goal.** Wire the in-memory queue, the single sequential consumer, and the startup catch-up scan into the container so classification work flows automatically from boot — with the consumer body a skeleton that prepares inputs and stops before the (Slice B) AI call.

**Build.**
- An `asyncio.Queue` of document ids.
- A single **consumer coroutine** with an `await queue.get()` loop: pull one id, load its document, run Content Reader (component 5), Dimension Loader (component 2), and Context Loader (component 5) — then **stop** (skeleton boundary; no AI call, no stamp on happy path). The consumer returns/propagates `Result` per stage and logs failures, leaving the fingerprint untouched on failure (retry).
- A **catch-up scan**: run Work Finder (component 6) once at startup and `put` each id onto the queue **in one burst** (OQ-P8A-03; tech-debt note to page later).
- Start both via a **composed outer ASGI lifespan** installed in `mcp_server/cloud_entry.py::build_app`, under uvicorn's existing event loop. The app returned by `mcp.streamable_http_app()` already carries a custom lifespan (FastMCP hardcodes `lifespan=lambda app: self.session_manager.run()` at `fastmcp/server.py:1044`), so a Starlette `on_startup` handler is a **silent no-op** — Starlette only honours `on_startup` when no lifespan is set (`starlette/routing.py:582-599`). **`on_startup` is FORBIDDEN here.** Instead `build_app` composes an `@asynccontextmanager` outer lifespan that:
  1. **On startup:** create the consumer coroutine as a background `asyncio.create_task(...)` AND run the catch-up scan (Work Finder → enqueue discoverable ids), THEN `async with` enters the framework's existing session-manager lifespan and yields through it — so the MCP server still initialises.
  2. **On shutdown:** cancel the worker task, then exit the inner FastMCP lifespan cleanly.
  Composition shape (b): inject the combined lifespan onto the framework app `build_app` already owns and mutates (it captures the app's existing `lifespan` callable, wraps it, and reassigns the composed one in place) — rather than constructing a separate outer Starlette wrapper. Evidence: `build_app` already holds the returned app and extends `app.routes` (`cloud_entry.py:78,81`), so swapping its lifespan in place is the minimal change consistent with the existing code. **NOT** in the per-chat MCP lifespan. **NOT** wrapped in `asyncio.run` (C-10/C-11 do not apply — runs under the container loop).

**Depends on.** Components 1, 2, 5, 6 (and 3, 4 transitively).

**Assumes.** A7.

**Interface shape.** Callers see a startup function that, once called, runs the background worker. Hidden: the queue, the consumer loop, the scan. One integration point (`build_app`).

**Dependency category.** in-process — start-mechanism resolved by research: the worker is launched from a **composed outer lifespan** in `build_app` (not `on_startup`), run under uvicorn's event loop. Test the consumer + scan logic directly with an injected queue and temp DB; verify the *composed lifespan* separately (assert the worker task is created AND the inner FastMCP session-manager lifespan still runs on app entry, and the worker is cancelled on exit).

**Decisions.**
- Q: Where exactly does the coroutine get scheduled so it runs at container boot? **Resolved via research (A7, now verified):** a **composed outer lifespan** installed in `build_app` — wrap the framework app's existing session-manager lifespan in an `@asynccontextmanager` that starts the worker + catch-up scan on entry and cancels the worker on exit. A Starlette `on_startup` handler is a proven no-op because the FastMCP-returned app already has a custom lifespan (`fastmcp/server.py:1044`; `starlette/routing.py:582-599`). This was the single most important wiring decision in Slice A.
- Q: Should the catch-up scan run inside the consumer's first iteration or as a separate startup step before the consumer starts? Leaning separate step (scan → enqueue → start consumer) for testability.

**Done when.** When several discoverable documents are pre-seeded and the consumer starts, the catch-up scan enqueues all currently-discoverable documents, the consumer processes ids one at a time (never two concurrently), and the queue drains to empty once all enqueued ids are processed — with the worker stopping after preparing each document's inputs (no AI call). _(Behavior P8-CLS-A-06.)_

---

## Handoff notes

- **Contract with Slice B:** Slice A delivers (a) a Work Finder + Classified-Stamp pair that together form the durable retry/skip mechanism; (b) Content Reader + Context Loader that produce the exact inputs the per-dimension extractor consumes; (c) a running queue + consumer whose body has a clearly marked seam where Slice B inserts the AI extraction → Entry Writer → audit → `Classified-Stamp` happy-path call. Slice B also wires capture's push seam (`capture.classify_ready` → `queue.put(doc_id)`) and guts the old folder-routing classify code.

- **Resolved — worker start hook (was the most important uncertainty):** ADR-0017 mandates the worker start at container boot, NOT in the per-chat MCP lifespan. Research resolved the mechanism: a **composed outer lifespan** in `build_app` that wraps the framework's session-manager lifespan and starts the worker + catch-up scan on entry (cancels on exit). A Starlette `on_startup` handler is a proven **silent no-op** — the FastMCP-returned app already sets a custom lifespan (`fastmcp/server.py:1044`; `starlette/routing.py:582-599`), so `on_startup` is FORBIDDEN here. See OQ-P8A-01 / A7. Only the composition shape is left to implementation.

- **Open uncertainty — migration auto-discovery:** whether dropping `010_*.sql` is sufficient or a registry/index also needs editing (A10). Cheap to confirm by reading `db.py` / how 008/009 are wired.

- **Suggested research:**
  1. Read `storage/db.py` to confirm how numbered migrations are discovered/applied (A10) and list the exact version-pin assertions to bump `9`→`10` (A8).
  2. Read `mcp_server/cloud_entry.py` + `mcp_server/server.py` startup to confirm whether `build_app` runs under a live event loop and the correct hook for the worker coroutine (A7, OQ-P8A-01).
  3. Grep every caller of `load_dimensions` and `validate_dimension_tag` to confirm the flat-shape blast radius is limited to `test_dimensions.py` (A4).
  4. Confirm whether `knowledge_entries.upsert` needs the two new columns added to its INSERT/UPDATE lists, or whether DB defaults + `_row_to_entry` are sufficient (component 3 `[UNVERIFIED]`).
  5. Confirm `documents.content_hash` is reliably populated on captured rows so the Work Finder comparison is meaningful (A1).

- **Tech-debt to log (for `/plan` or `/guardrail-check`):** catch-up scan enqueues in one burst — page/batch if a large vault floods the queue at startup (OQ-P8A-03; matches the grill's "watch vault size" entry).

---

## Open questions (deferred — do not block planning)

- **OQ-P8A-01 — Where exactly does the classify worker get started?** **Resolved via research (mechanism now known).** Start it via a **composed outer lifespan** installed in `build_app`: wrap the framework app's existing session-manager lifespan in an `@asynccontextmanager` that starts the worker + catch-up scan on entry and cancels the worker on exit, under uvicorn's event loop. A Starlette `on_startup` handler — the original recommendation — is a **silent no-op**: the FastMCP-returned app already sets a custom lifespan (`fastmcp/server.py:1044`), and Starlette only honours `on_startup` when no lifespan is set (`starlette/routing.py:582-599`). _(No longer UNVERIFIED. Only the composition shape — `create_task` inside the lifespan body vs an anyio task group — is left to implementation.)_
- **OQ-P8A-02 — One ranked query function, or extend the existing fact-loader?** **Resolved:** new ranked query function (keeps the 5-function CRUD contract stable; isolates ranking). Reflected in component 3.
- **OQ-P8A-03 — Catch-up scan: one burst or paged?** **Resolved for Slice A:** one burst, with a tech-debt note to page if backlog size becomes a problem. Reflected in component 7 + Handoff.
- **OQ-P8A-05 (new) — Is `guidance` mandatory per dimension, and should missing `other`/`guidance` fail loudly at load?** Leaning: validate-on-load and reject (P8-CLS-A-04 expects rejection of malformed config). Confirm exact validation surface in research. _(Deferred — not a blocker.)_ (Renumbered from a draft OQ-P8A-04 to avoid collision with OQ-P8A-04 = periodic-synthesis in OPEN_QUESTIONS.md.)

---

## Next step

Spec written. Run `/research` to verify the spec assumptions (especially A7 — the worker start hook — and A8/A10 — the migration cascade and auto-discovery) against real code before planning.
