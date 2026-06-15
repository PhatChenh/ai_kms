# Research: Phase 8 Slice B — Classify Extraction Pipeline
_Last updated: 2026-06-15_

## Overview

**Plain-English.** Slice A built the classify plumbing: it finds documents that still
need classifying, prepares their inputs, and stops just before any AI step. Slice B
adds the AI brain — for each captured document it asks the AI (one focused question per
knowledge category) to pull out small structured facts, writes those facts safely into
the knowledge database (add / update / retire), logs every decision, and marks the
document done. Three guards make it robust: a bad fact is logged and retried with
feedback (capped so it cannot loop forever); an exact-match check folds duplicate facts
together; and capture pushes a new document straight onto the live work queue so it is
classified the moment it arrives.

This research independently verified all 16 falsifiable assumptions (A1–A16) in the spec
against the actual code — reading function bodies, not just signatures or labels. **All 16
assumptions are validated.** No assumption was invalidated. A handful of the spec's
inline `file:line` references have drifted by a few lines (the spec itself flags "line
numbers re-verified by research"); these are corrected in the evidence column and are
mechanical, not blocking. One open question (OQ-P8B-01, finding facts by a contained
source id) was sharpened: SQLite JSON1 **is** available in the deployed image, so both the
recommended scan-and-filter route and a JSON query are viable.

**Verdict: ready for `/plan phase8_sliceB_extraction`.** No invalidated assumptions, no
redesign needed, no Q4 conflict diagram required.

---

## Key Components

**Plain-English.** These are the existing building blocks Slice B reuses, and the seams
where the new code plugs in. The classify pipeline file already holds the worker loop and
the input-preparation helpers; Slice B fills the "seam" the worker deliberately stops at.
The fact store, audit log, provider factory, document store, and the cloud app's startup
wiring are all already in place.

| Component | Location (re-verified) | Role for Slice B |
|---|---|---|
| Worker loop `consumer` | `src/pipelines/classify.py:388` | Pulls a doc id, prepares inputs, stops at the seam (`:431`). Slice B fills the seam. |
| `content_reader` | `src/pipelines/classify.py:259` | Picks full_body vs summary by token budget. Reused unchanged. |
| `context_loader` | `src/pipelines/classify.py:320` | Loads ranked, capped, non-retired facts per dimension, each carrying its `id`. Reused unchanged. |
| `catch_up_scan` | `src/pipelines/classify.py:446` | One-burst startup enqueue. Reused unchanged. |
| Old folder-routing symbols (to delete) | `src/pipelines/classify.py` — `build_subject:22`, `build_folder_subject:52`, `_destination_names:70`, `ClassifyResult:97`, `classify:113` | Retired "which folder?" logic. Deleted in component 10. |
| Fact store CRUD | `src/storage/knowledge_entries.py` — `upsert:54` (UPDATE path `:77`), `query_by_entity:143`, `retire:159`, `query_ranked_by_dimension:206`, `KnowledgeEntry:16` | Entry Writer routes facts through these. |
| Work Finder `find_unclassified` | `src/storage/documents.py:634` | Single-table `documents` query; gains a `status != 'needs-review'` filter. |
| Classified-Stamp `stamp_classified` | `src/storage/documents.py:659` | Stamps one doc on full success. |
| `get_by_path` / `delete_by_path` | `src/storage/documents.py:251` / used in `api.py:377` | id lookup before delete (source-prune). |
| Provider factory `get_provider` | `src/llm/provider.py:58` | `get_provider("classify", config)` → `OpenAIProvider(config.openai_compat)` when re-pointed. |
| Audit `core.audit.write` | `src/core/audit.py:11` | One record per dimension + one on park. Requires a correlation id. |
| `new_correlation_id` | `src/core/logging_setup.py:55` | Set once per document before any audit. |
| `confidence_to_status` | `src/core/tags.py:255` | Re-gate fact status from confidence via `band.route` — no float literal. |
| `ConfidenceBand.route` | `src/core/config.py:455` (class `:421`) | The band the re-gate needs. |
| `ClassifyConfig` | `src/core/config.py:336` | `max_content_tokens`, `max_entries_per_dimension`; Slice B adds `max_retries`. |
| Composed lifespan / queue | `src/mcp_server/cloud_entry.py` — `build_app:43`, `_wrap_lifespan:96`, queue created `:118` | Store the queue on `app.state.classify_queue`. |
| Upload handler | `src/mcp_server/api.py:96` | Push new doc id after successful capture. |
| Delete path | `src/mcp_server/api.py:330` | Add source-prune (id lookup before delete). |

---

## How It Works

**Plain-English.** A document id reaches the work queue — either pushed live by the upload
handler the instant a capture finishes, or enqueued by the startup catch-up scan. The
single sequential worker pulls one id, reads the document text, loads the known facts per
category, and (Slice B) hands it to a new orchestrator. The orchestrator tags the run,
then for each category asks the AI to extract facts, writes them safely, and logs one
audit record. If everything succeeded it stamps the document done; if anything failed it
saves the failure reason, bumps the try count, and either leaves it for retry or parks it
for a human once the cap is hit.

The worker today (`consumer`, `classify.py:388-443`) already runs Content Reader →
Context Loader and stops at the seam comment at `classify.py:431-435`. Slice B inserts the
orchestrator call there. The composed lifespan (`cloud_entry.py:_wrap_lifespan`) already
creates the `asyncio.Queue` and starts the worker + catch-up scan; the only change is to
publish the queue on `app.state` so the upload handler can reach it.

---

## Spec Verification

**Plain-English.** Every falsifiable claim the spec makes about existing code was checked
by reading the actual function bodies. All sixteen held. The only discrepancies are a few
line numbers that drifted by a handful of lines — corrected below, and harmless because
the spec told research to re-verify them.

| ID | Spec Claim | Verdict | Evidence (re-confirmed) |
|----|-----------|---------|----------|
| A1 | Old folder-routing symbols imported ONLY by `test_classify.py` + the dead conftest block. | ✅ Validated | grep of `src/` + `tests/`: no `src/` file imports any of the five; `cloud_entry.py:112` imports only the *kept* `catch_up_scan, consumer`; importers are `tests/test_pipelines/test_classify.py:10-13` and `conftest.py:83` only. |
| A2 | `pipelines.capture` has no `classify` attr; the conftest monkeypatch is dead; no test requests `pipeline_ctx`. | ✅ Validated | `capture.py` "classify" hits are only the log string `"capture.classify_ready"` (`:285,:482`) — no `classify` symbol. `monkeypatch.setattr("pipelines.capture.classify", …)` (`conftest.py:96`) would raise AttributeError (pytest default `raising=True`). `pipeline_ctx` is referenced nowhere except its own def and a *comment* (`test_search_command.py:308`) — never requested. |
| A3 | `upsert` UPDATE overwrites `sources` wholesale via `json.dumps(entry.sources)`; DB does not merge. | ✅ Validated | `knowledge_entries.py:71` `sources_json = json.dumps(entry.sources)`; UPDATE sets `sources=?` to that value (`:77-93`). No SQL-side merge/append. (Spec cited `:71` for the UPDATE; the `SET sources=?` line is `:77` — `json.dumps` is at `:71`.) |
| A4 | `query_by_entity` returns non-… entries carrying `id`, `dimension`, `tag`, `status` — enough to detect an exact dimension+entity+tag non-retired match. | ✅ Validated | `knowledge_entries.py:143` `SELECT * FROM knowledge_entries WHERE entity=?`; `_row_to_entry` (`:35`) fills `id, dimension, entity, tag, status, …`. Caller must itself filter `status != 'retired'` and match dimension+tag (the query returns ALL statuses for the entity). |
| A5 | `retire(id, reason)` never deletes — sets `status='retired'`. | ✅ Validated | `knowledge_entries.py:159` issues `UPDATE … SET status='retired', reasoning=?, updated_at=… WHERE id=?`. No DELETE anywhere in the file. |
| A6 | `confidence_to_status(score, band)` returns status via `band.route`, no float literal. | ✅ Validated | `tags.py:255-270`: `decision = band.route(score)`; returns `"confident"` for AUTO else `"pending"`. No float comparison. `ConfidenceBand.route` at `config.py:455`. |
| A7 | `core.audit.write(decision, pipeline=, stage=, outcome=)` requires a correlation id in contextvars, else `audit_log.append` fails "missing correlation_id". | ✅ Validated | `audit.py:11-40` signature matches; calls `audit_log.append`. `audit_log.py:28-34`: `cid = entry.correlation_id or contextvars…("correlation_id"); if cid is None: return Failure("missing correlation_id")`. `new_correlation_id` at `logging_setup.py:55`. |
| A8 | `find_unclassified` is a single-table `documents` query; a `status != 'needs-review'` clause can be added without a join. | ✅ Validated | `documents.py:634-656`: `SELECT id FROM documents WHERE classify_content_hash IS NULL OR classify_content_hash != content_hash`. No join. A WHERE clause adds cleanly. |
| A9 | `documents` carries `content_hash` and `status`; `needs-review` already used by capture. | ✅ Validated | `status` column added by `migrations/004_add_status.sql`; `content_hash` present (used in `find_unclassified` / `upsert_from_upload`). **No src code reads `needs-review` back** (grep: zero non-test hits), so the overload cannot collide with a capture branch. |
| A10 | `row_id` returned by `capture_upload` IS `documents.id`. | ✅ Validated | `capture.py:216` returns `row.id` (dedup path); other returns come from `upsert_from_upload` (`documents.py:115`), which returns `cur.lastrowid` (INSERT, `:187`) or `existing_id = row["id"]` (`:189`) — both `documents.id`. `api.py` returns it as `document_id` (`:215,:270`). |
| A11 | The upload handler runs inside a Starlette request and can reach `request.app.state`. | ✅ Validated | `upload_handler(request: Request)` (`api.py:96`) is a Starlette route handler; `request.app.state` is the standard Starlette state object reachable in any handler. (Storage there happens in A12.) |
| A12 | `_wrap_lifespan` creates the queue inside the composed lifespan; storing it on `app.state` there is reachable by handlers. | ✅ Validated | `cloud_entry.py:96-130`: `_composed(app_ref)` creates `queue = asyncio.Queue()` (`:118`); `app_ref` is the Starlette app, so `app_ref.state.classify_queue = queue` is reachable from request handlers. Lifespan runs at ASGI startup before requests. |
| A13 | `_delete_with_blob_cleanup` receives the doc by path; the id must be looked up (`get_by_path`) BEFORE `delete_by_path`. | ✅ Validated | `api.py:330` takes `vault_path: str`. `get_by_path` is already called first (`:366`) and returns a `DocumentRow` with `.id`; `delete_by_path` runs after (`:377`). Currently it touches ONLY blob cleanup — never `knowledge_entries`. |
| A14 | No existing query finds `knowledge_entries` by a contained source id; deployed SQLite JSON support unconfirmed. | ✅ Validated | grep: no `json_each` / `json_extract` / `sources LIKE` anywhere in `src/`. **Refinement:** the deployed image is `python:3.12-slim` (Dockerfile `:17`); CPython 3.12 ships SQLite with JSON1 enabled — local check `json_extract(...)` returns a value. So JSON1 *is* available; scan-and-filter is still the recommended (dependency-free) route. |
| A15 | `providers.classify` maps to `claude_cli`; M2 re-points to the OpenAI-compatible provider via config, not code. | ✅ Validated | `config.yaml:63` `classify: claude_cli`. `provider.py:72-86`: `for_task` → `match`; `"openai"` → `OpenAIProvider(config.openai_compat)`. `openai_compat` block present with `base_url`, `model`, `api_key_env` (`config.yaml:43-50`). Re-pointing = change one YAML line. (Endpoint is currently Fireworks; deploy sets the actual DeepSeek model — code unchanged.) |
| A16 | A dropped `011_*.sql` auto-applies with no registry edit; current `schema_version` is 10. | ✅ Validated | `db.py:36` `sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))` — pure glob, no registry. Highest file is `010_*`; version-pin tests `test_migration_007/008/009/010.py` all assert `== 10`. Migration 011 bumps all four to 11. |

---

## Edge Cases & Silent Failure Modes

**Plain-English.** A few real traps the planner should keep in mind — none break the spec,
but each will cause a subtle bug if missed.

- **`query_by_entity` returns retired rows too.** It filters only by `entity` — the Entry
  Writer's exact-dedup check (`dimension + entity + tag` non-retired) must filter
  `status != 'retired'` and match dimension+tag itself in Python. `find_unclassified`'s
  `status != 'needs-review'` filter and the dedup's `status != 'retired'` are two different
  guards on two different tables — do not conflate.
- **The dead conftest monkeypatch would raise, not no-op, if ever exercised.**
  `monkeypatch.setattr` with a dotted string and the default `raising=True` raises
  AttributeError because `pipelines.capture.classify` does not exist. It is safe only
  because no test requests `pipeline_ctx`. Deleting the block (component 10) removes a
  latent landmine, not live behavior.
- **`needs-review` is write-only in the codebase today.** Capture writes it but nothing
  reads it back, so the classify-park overload is collision-free *now*. If any future
  capture path starts branching on `needs-review`, the overload would need a discriminator
  (the spec's `classify_attempts > 0` idea).
- **Audit fires "missing correlation_id" Failure, not an exception.** If the orchestrator
  forgets `new_correlation_id()`, every `audit.write` returns `Failure`, the dimension is
  counted as failed, and the doc is never stamped — it will retry forever until parked.
  The correlation-id call is load-bearing for the happy path, not just for logging.
- **`stamp_classified` returns rowcount 0 silently for a missing id** (`documents.py:676`).
  The orchestrator should treat 0 as a failure, not success, or a deleted-mid-run document
  would be marked done without being stamped.

---

## Dependencies & Coupling

**Plain-English.** What Slice B leans on and what leans back on it.

- **Worker → orchestrator (new) → extractor/writer (new):** all inside
  `pipelines/classify.py`, single file, single caller per function — matches the Slice A
  "closed seam" policy (plain `Result` functions, no protocol).
- **Orchestrator → fact store / document store / audit / provider:** existing modules,
  all `Result`-returning. The fact store's wholesale-overwrite `upsert` forces the Entry
  Writer to merge `sources` in Python before calling `upsert` on an update (A3).
- **Live-enqueue couples `cloud_entry.py` and `api.py` through `app.state`:** the queue is
  created in the lifespan and read in the upload handler. If the queue is absent (CLI, tests)
  the handler must skip silently — the catch-up scan is the net.
- **Migration 011 couples to four version-pin tests** (007/008/009/010) — the standard
  cascade. Missing the bump fails those tests on every `pytest` run.

---

## Extension Points

- **Provider is config-swappable** (`get_provider("classify", config)`) — re-point
  `providers.classify` in YAML to `openai` for DeepSeek; zero code change (A15).
- **Prompt is a YAML asset** (`prompts/entity_extract.yaml`, new) — rendered via
  `PROMPTS["entity_extract"].render(...)`; the reply schema can evolve without touching code.
- **Retry cap is config** (`classify.max_retries`) — tunable, no literal (C-06 spirit).
- **Source-prune dedup strategy is swappable** — scan-and-filter now; a JSON1 query later
  (JSON1 confirmed available) with no behavior change.
- **Blocked extension (acceptable):** the Entry Writer holds real branching logic in one
  function with one caller. Generalizing to a per-action strategy object would be
  speculative for a single caller — flagged, intentionally not done (matches design D-note).

---

## Open Questions

**OQ-P8B-01 — Finding facts by a contained source id (on delete).** No existing query does
this (verified). The deployed image (`python:3.12-slim`) **does** ship SQLite JSON1, so a
`json_each`/`json_extract` query is technically available — but scan-and-filter in Python
remains the recommended Slice B route (dependency-free, swappable). Evidence examined:
full `src/` grep for JSON SQL (none), local `json_extract` smoke test (works), Dockerfile
base image. *Resolution is a design preference, not a code blocker.*

**OQ-P8B-02 — Does an update that lowers confidence demote a fact's status?** Cannot be
settled from code — it is a product/behavior choice. The helper (`confidence_to_status`)
re-gates from whatever confidence it is handed; the question is whether the Entry Writer
should pass the new (possibly lower) confidence on every update. Locked design = re-gate on
every write. Flag for observation, not a blocker.

**OQ-P8B-03 — Should the catch-up scan page its enqueues?** Runtime/scale concern, not
determinable from code. One-burst is kept; the sequential consumer rate-limits AI spend.
Not a blocker.

---

## Technical Debt Spotted

- **`context_loader` re-reads `dimensions.yaml` and re-queries facts per document**
  (`classify.py:320`, carried Slice A TD) — N×D redundant work in the consumer loop. Slice B
  adds an AI call per dimension, so the relative cost shrinks, but caching dimensions+facts
  once per consumer session is the clean fix. Already logged as a Slice A TD; re-confirm
  during planning.
- **`context_loader` hardcodes the path to `dimensions.yaml`** via `Path(__file__)…`
  (`classify.py:344-346`) — fragile under package install. Known Slice A TD; not introduced
  here.
- **Catch-up backlog (OQ-P8B-03 / OQ-P8A-03):** in Slice B each enqueued item is a paid AI
  call, so a large unclassified vault at boot floods the queue with paid work. The sequential
  consumer rate-limits naturally; paging is the later optimization. Re-confirm the TD.

---

## Invalidated Assumptions

None. All 16 assumptions validated against actual code. No Q4 conflict diagram required
(no redesign, no type-c escalation, no mechanical invalidation that changes the design).

---

### Note on line-number drift (mechanical, non-blocking)

The spec flagged its own line numbers as "research re-verifies." Confirmed drifts:
`knowledge_entries.upsert` header is `:54` (spec) — the `json.dumps(sources)` is `:71` and
the UPDATE `SET sources=?` is `:77`. `api.py` Success returns are `:215`/`:270` (spec cited
`:201`/`:259`, which land on Failure/JSON-path lines). `capture.py:216` is correct;
`:406`/`:484` are nearby Success returns in the same function family. All `pipelines/classify.py`
and `core/*` references match exactly. None of these affect the design.
