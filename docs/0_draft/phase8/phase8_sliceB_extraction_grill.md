# Phase 8 Slice B — Extraction Pipeline: Grill Output

_Date: 2026-06-15_
_Status: Grill COMPLETE (Slice-B-specific). Ready for design→spec→research→plan pipeline._
_Tier: HEAVY (LLM integration, multi-module, deletes + rewrites classify.py)._
_Scope: Slice B ONLY — the LLM extraction pipeline. Slice A infrastructure (migration 010, nested dimensions.yaml, ClassifyConfig, KnowledgeEntry ranking query, Work Finder + Classified-Stamp, Content Reader + Context Loader, asyncio queue + worker skeleton) is ALREADY COMPLETE and merged to cloud-native._

> **For the non-coder reader:** Slice A built the *plumbing* — it finds files needing classification and prepares their inputs, then stops right before any AI step. Slice B adds the **AI brain**: it reads the prepared document, asks the AI (one focused call per knowledge category) to pull out structured facts ("Anthony leads Movie Q2", "Movie Q2 deadline is Aug"), then safely writes/updates/retires those facts in the database, logs the decision, and marks the document done. This grill nailed down the failure-handling, deduplication, cost, and retry behavior that the broad Phase 8 grill left fuzzy.

---

## Backdrop (do NOT re-litigate — locked in the broad Phase 8 grill)

Source: `docs/0_draft/phase8/phase8_classify_redesign_grill.md` (20 decisions). The ones that bind Slice B:

- Structured **JSON** output from the LLM.
- **One LLM call per dimension** (focused, not one monolithic call) — chosen deliberately for extraction quality.
- **Prompt-level dedup** (no unique index) — the LLM avoids duplicates by seeing existing entries. **(Slice B adds a deterministic backstop — see D6.)**
- **Update/retire via entry IDs** — Context Loader passes existing entries WITH their DB `id`; the LLM references IDs for `update`/`retire`, omits `id` for `new`.
- **Partial failure** = commit what succeeded, leave `classify_content_hash` NULL, retry whole doc; dedup prevents re-write duplicates.
- **Confidence is a live signal** — re-gates status on every write (via `confidence_to_status`).
- **trust_score** flat `0.5`, inert in P8 (Phase 10 moves it).
- **sources** = document **IDs** (integers), not vault paths.
- **vault_path** = a soft contextual hint to the LLM ("content always wins on conflict").
- **Audit**: one entry per dimension per document (counts new/updated/retired, success/failure).
- **No user-corrections slot** in P8 (that's Phase 10) — decision #17.
- Housekeeping AI = **DeepSeek V4 Pro** via OpenAI-compatible endpoint through existing `llm/provider.py` factory.

---

## Slice B components (from broad grill)

1. Entity extraction prompt `prompts/entity_extract.yaml` — per-dimension; slots for guidance + existing entries + document content + vault_path hint + **(new, D11)** previous-attempt feedback.
2. Entity Extractor — one LLM call per dimension; parses JSON; validates dimension/tag/action.
3. Entry Writer — routes by action (new→insert, update→update, retire→retire); validates IDs exist; **accumulates sources (D2)**; **exact-entity dedup backstop (D6)**.
4. Classify orchestrator — wires Content Reader → Context Loader → (per dimension: Extractor → Writer) → audit → hash stamp.
5. Wire to capture — make the worker queue reachable from the capture path so a freshly-captured doc is enqueued **live (D1)**.
6. Delete old folder-routing classify code (`classify`, `ClassifyResult`, `build_subject`, `build_folder_subject`, `_destination_names`).
7. Per-dimension audit logging.
8. Document-deletion source cleanup in the `/api/event` delete handler (D3).

---

## Decisions locked THIS grill (Slice-B-specific landmines)

### D1 — Live enqueue at runtime (not catch-up-scan only)
A document captured **while the container is running** must be enqueued for classification **immediately**, without waiting for a container restart.
- **Why:** the only feed today is the startup catch-up scan. Catch-up-only means a file dropped during runtime never classifies until reboot — effectively broken for a live system.
- **Constraint surfaced:** the `asyncio.Queue` is currently created *local to the composed lifespan* in `mcp_server/cloud_entry.py:118` — `capture.py` has **no handle to it**. The seam at `capture.py:285` (text) and `:482` (binary) currently only writes a `capture.classify_ready` log line. Wiring requires making the queue handle reachable from the capture path (app state / module singleton — **design decides the mechanism**). The `row_id` at the seam IS the `doc_id` the queue needs.
- **Keep the catch-up scan** too — it covers anything captured while the worker was down.

### D2 — Sources accumulate on update
When a new document updates an existing fact (`action: update`), its doc ID is **added** to the fact's `sources` list (e.g. `[5] → [5, 9]`), deduped — **never overwritten**.
- **Why:** provenance must grow; "which docs back this fact" is the point. Also required for D3 to work.
- **Constraint surfaced:** `knowledge_entries.upsert` UPDATE path **overwrites** `sources` with whatever it's handed (survey: `knowledge_entries.py:71`, `json.dumps(entry.sources)`). So the Entry Writer must **read existing sources, append, dedupe, write the merged list** — the DB does not merge for you.

### D3 — Delete cleanup: remove only that doc's ID; empty → pending
On a document delete signal, remove **only the deleted doc's ID** from every fact's `sources`. If a fact's sources becomes **empty → set `status = pending`** (never auto-delete, never auto-retire — user decides via web UI).
- **Gap confirmed:** `_delete_with_blob_cleanup` (`mcp_server/api.py:330`) cleans the documents row + search + blob, but **never touches `knowledge_entries`**. This is unbuilt — Slice B component 8.
- **Constraint surfaced:** the delete signal arrives by **path**, but `sources` holds **IDs** — so the handler must look up the doc's ID via path *before* deleting the row, then prune that ID from entries. `sources` is a JSON-text column → finding entries by contained ID needs a scan or JSON query (design decides).

### D4 — Write order: facts → audit → stamp; audit fail = no stamp
Per document: write all extracted facts, then write the audit record(s), then stamp `classify_content_hash`. **If the audit write fails, do NOT stamp** → doc retries next run.
- **Why:** project rule "every AI decision must be audited — non-negotiable." The stamp is the last step and only lands once the decision is on record. Retry + dedup heals duplicate facts; a fresh audit attempt is logged.
- **Note:** audit is per-dimension (decision #16), so within a doc it's: per dimension { extract → write facts → audit that dimension }, then stamp once after all dimensions. Any dimension's audit failure ⇒ withhold stamp.

### D5 — Per-fact write granularity (retry heals)
Facts write one per transaction (matches the existing one-connection-per-call pattern; survey confirms every `knowledge_entries` function opens its own connection). A crash mid-dimension can leave it half-written; the retry + dedup loop heals it. **No all-or-nothing per-dimension batch** — that capability doesn't exist today and the failure self-heals.

### D6 — Exact-entity write-time dedup backstop
Before inserting a `new` fact, the Entry Writer does a **direct DB lookup** for an existing non-retired entry with the **same dimension + entity (+ same tag)**. If found → fold into the existing entry (treat as update), don't spawn a twin. Log the fold.
- **Why:** prompt-level dedup only sees the **top-N** the AI was fed. A duplicate ranked **out of** the top-N (old, low-trust, beyond the cap) would be invisible → silent duplicate drift. The top-N is a recency/quality window, NOT an existence check.
- **Scope:** catches **exact-entity** dupes only (cheap, deterministic — `query_by_entity` already exists). **Fuzzy / name-variant** dupes (`"Anthony"` vs `"Anthony Nguyen"`) stay the LLM's job — that's **entity resolution**, explicitly deferred (rearchitecture open question #4). No unique index added.

### D7 — Accept per-call context re-send for MVP
Keep one stateless LLM call per (doc × dimension); context is re-sent each call (LLM API is stateless — no persistent "session" exists). 
- If research confirms the DeepSeek/OpenAI-compatible endpoint supports **prompt caching**, wire it via config (no architecture change). **Unverified — research must confirm.**
- Cross-doc batching → **tech debt** (see TD list). Reasons to defer: classify is async/off-hot-path, DeepSeek cheap + large-context, single-user vault, and batching would damage the clean per-doc audit/stamp/source model.

### D8 — Focused per-dimension context
Each per-dimension extraction call sees the **full document** + that **one dimension's** guidance + that **one dimension's** top-N existing facts. It does **NOT** see other dimensions' existing facts.
- **Why:** cheapest per-call payload; focused prompt = better extraction (deliberate, decision #6). Holistic (all dimensions every call) was rejected — it maximizes the exact repeated-context cost we're trying to bound.
- **Upgrade path if needed:** Hybrid (own facts + a lightweight list of *known entity names* from other dimensions) — add later as tech debt only if extractions visibly miss cross-dimension links.

### D9 — One bad fact in a valid reply: keep good, skip bad, withhold stamp
If the LLM returns valid JSON but **one fact object** fails validation (invalid tag, unknown action, missing field, hallucinated ID), **write the good facts, skip + log the bad one, but do NOT stamp the doc** → it retries.
- **Why (user-driven correction):** skip-and-stamp would lose that fact forever (e.g. a deadline) unless the doc content changes. Withholding the stamp keeps the doc in the queue for another extraction attempt; good facts dedup away on retry.
- A dimension whose **entire** call fails (network, unparseable JSON) also fails as a unit and leaves the doc unstamped (consistent with D4 / partial-failure #10).

### D10 — Bounded retries → human-review fallback
Retries are **capped at K** (K from config, not hardcoded). After K failed attempts to fully classify a doc, **stop auto-retrying** and **park it for human review** (web UI) with an audit entry explaining why.
- **Why:** D9's "withhold stamp → retry" plus a deterministically-malformed LLM output = infinite re-classify loop, burning tokens forever. Slice A explicitly deferred retry-bounding, so there's no brake today.
- Exact mechanism (retry-count column vs audit-flag vs a `needs_review` status) = design decision. Lock = "bounded, not infinite; park for human, don't lose the fact."

### D11 — Inject last-failure reason on retry (self-correction)
On a retry, the prompt includes a **"previous attempt feedback"** slot describing why the last attempt failed (e.g. "you used tag `urgent`, not allowed; valid tags: [...]"; "id 999 doesn't exist"). Empty on first attempt.
- **Why:** turns blind retry into self-correction → higher odds of success within the K budget → fewer docs hit the human-review fallback.
- **Implications:** (1) the last attempt's validation errors must be **persisted per-doc** (survives container restart, since retries span reboots) — design decides shape; (2) `entity_extract.yaml` gains a new prompt variable.
- **No conflict with decision #17:** #17 banned a *user-corrections* few-shot slot (Phase 10). This is a *machine-generated validation-error* slot — different purpose, fine for P8. Record so future readers don't think #17 was violated.

---

## Mechanical items (no decision needed — implementation notes for design/spec)

- **M1 — correlation_id:** the queue consumer currently sets **no correlation ID**, so `audit.write` will fail with `Failure("missing correlation_id")` (survey: `storage/audit_log.py:28-34`). The Slice B consumer **must call `new_correlation_id()` (from `core/logging_setup`) per doc** before auditing. One correlation ID per document = one traceable classify unit.
- **M2 — LLM task name:** `get_provider(task, config)` is keyed on a `Task` literal (`core.config`) + `providers.for_task` mapping (survey: `llm/provider.py:58`). Recommend **reuse the existing `"classify"` task name** (re-point it at DeepSeek in config) since the old classify dies — avoids editing the `Task` literal. If a distinct `"entity_extract"` task is preferred, the literal + mapping must be extended. Design confirms.
- **M3 — old-code deletion blast radius:** deleting the old folder-routing code breaks `tests/test_pipelines/test_classify.py` (~33 tests — delete with the code) and `tests/test_pipelines/conftest.py` (imports `ClassifyResult` at `:83`, monkeypatches `pipelines.capture.classify` at `:96`). **Survey finding:** `capture.py` does **not** import or reference `classify` — so that `monkeypatch.setattr("pipelines.capture.classify", ...)` targets a **non-existent attribute** and would raise `AttributeError` unless `raising=False`. **Verify this fixture's current state before deletion** — it may already be dead/unused. New-function tests survive: `test_classify_infra.py`, `test_classify_worker.py`, `test_cloud_entry.py`.

---

## Tech debt to log

- **TD — classify batching:** cross-doc batching and/or one-call-extracts-all-dimensions-per-doc, to cut the N×M repeated-context cost. Deferred from Slice B (D7).
- **TD — prompt caching:** wire provider prompt-caching for the stable context prefix *if* the endpoint supports it (verify in research). (D7)
- **TD — hybrid cross-dimension context:** add a lightweight cross-dimension entity-name header if focused extraction visibly misses links. (D8)
- **TD (existing, broad grill):** watch dimension-summary token growth as facts accumulate; no unique index (prompt+backstop dedup only); no time-based trust decay.

---

## ADR candidates (offer at design step — all three gates pass)

- **Retry/feedback/cap loop (D9–D11):** hard-to-reverse (shapes the queue + persistence + prompt schema), surprising (withhold-stamp-to-retry + self-correction feedback is non-obvious), real tradeoff (vs simple skip-and-stamp). 
- **Live-enqueue seam (D1):** hard-to-reverse (couples capture ↔ worker via a shared queue handle), surprising (queue is lifespan-local today), real tradeoff (live wiring vs catch-up-scan-only).

---

## Cost model (reference for design)

N docs × M dimensions = **N×M** stateless LLM calls. Per call ships: full doc text (so doc text sent M× per doc) + one dimension's guidance + one dimension's top-N facts (so each dimension's background sent ~N×). DB reads are cheap/local; the LLM **token bill** is the cost driver. Focused context (D8) keeps each of the N×M sends minimal; batching (TD) would cut the repetition.

---

## Survey ground-truth anchors (verified read-only, 2026-06-15)

For the designer — confirmed file:line facts (re-verify if code drifted):
- Capture seam: `pipelines/capture.py:285` (text), `:482` (binary) — `capture.classify_ready` log; `row_id` = doc_id available; both in `async def`.
- Queue: created at `mcp_server/cloud_entry.py:118`, local to the composed lifespan `_composed`.
- Slice A classify functions (keep): `content_reader` `classify.py:259`, `context_loader` `:320`, `consumer` `:388` (Slice B seam at `:431-435`), `catch_up_scan` `:446`.
- Old classify (delete): `build_subject` `:22`, `build_folder_subject` `:52`, `_destination_names` `:70`, `ClassifyResult` `:97`, `classify` `:113`.
- knowledge_entries: `upsert` `:54` (status gating `:66-69` via `confidence_to_status`; sources `json.dumps` `:71`); `retire` `:159`; `query_by_entity` `:143`; `query_ranked_by_dimension` `:206`; `_row_to_entry` sources read `:45`.
- `confidence_to_status` lives `core/tags.py:255` → only emits `confident`/`pending`.
- Provider: `get_provider` `llm/provider.py:58`; `complete(system,user)->Result[LLMResponse]` `:40`. JSON-parse pattern to mirror = old `classify()` `:137-251` (render err → recoverable=False; provider/parse/validation err → recoverable=True; truncate raw to 200 chars).
- Prompt: Jinja2 `{{ var }}`, `StrictUndefined`; YAML keys `name`/`system`/`user`/`variables`; `PROMPTS["<name>"]` keyed on `name` field.
- Audit: `core/audit.py::write(...)->Result[int]` `:11`; `storage/audit_log.py::append` `:27`; fails on missing correlation_id `:28-34`.
- Delete handler: `event_handler` `mcp_server/api.py:415`; `_delete_with_blob_cleanup` `:330` — does NOT touch knowledge_entries.

---

## Next step

Tier = **HEAVY**. Pipeline: `codebase-design-analysis` (design) → `writing-detailed-specs` (spec) → `research` → `plan-from-specs` (plan). Artifacts land in `docs/1_design/` → `docs/2_specs/` → `docs/3_research/` → `docs/4_plans/` (project uses underscore-numbered folders). Suggested slug: `phase8_sliceB_extraction`.
