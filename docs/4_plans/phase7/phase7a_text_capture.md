# Plan: Phase 7A — Text Capture (cloud capture refactor, text path only)
_Last updated: 2026-06-14_
_Status: [ ] pending_

_Spec: `docs/2_specs/phase7/phase7a_text_capture.md` (components 1–6, behavior IDs P7-CAP-01…09 — source of truth for WHAT)._
_Research: `docs/3_research/phase7/phase7a_text_capture.md` (all assumptions verified; A9 resolved)._
_Design: `docs/1_design/phase7/phase7_capture_refactor.md` (Option A — Store-Raw-First; ADR-0014)._
_Reader mode: non-coder-readable. Plain English leads; code references are parenthetical anchors._

> This plan owns the HOW (build order, TDD red→green, exact files/lines, commit boundaries).
> For the WHAT (Build steps, Files-to-modify, Done-when of each component), open the spec and read the named component. The plan does not restate it.

---

## Architecture

### Q1 — What happens inside
_Source: design doc `phase7_capture_refactor.md` Q1 (the TEXT branch only: steps 1 → 2 → 3 → 4 → 5; the binary/visual branch 2b/3b is Phase 7B and omitted)._ In one line: validate the upload and check "seen this exact content before?" → if new, **store the raw text first** (safe immediately) → **ask the AI for a structured summary** → on success **attach the summary** to the same row, index it, and audit success → on failure, audit the failure but **still report success** → log one "ready for facts" line.

### Q2 — How it connects
_Source: spec `phase7a_text_capture.md` Q2._ The laptop daemon uploads extracted text through the cloud Upload Endpoint, which hands it to the **Text Capture Pipeline**. The pipeline talks to: the **Housekeeping AI Summarizer** (asks for a summary + title), the **Document Store** (saves raw text, then attaches the summary — two writes to the same row), the **Audit Log** (success + failure entries), the **Search Indexer** (keyword + meaning, once the summary attaches), and — dashed, inert until Phase 8 — **Knowledge Facts** (consulted for context, empty today) and **Classify** (a log line).

### Q3 — Why build it this way

```
# Text Capture — Why This Way
Scope: The rules and existing patterns that shaped Phase 7A text capture.
       Does NOT show the internal step order (see Q1) or the component
       connections (see Q2). Center box and component names match Q1/Q2.

How to read this:
  Center box        = the feature being built
  Surrounding boxes = a rule it must follow, OR an existing pattern it reuses
  Lines             = which rule/pattern applies to the feature
  [REUSE]           = an existing piece used as-is or nearly as-is
  [RULE]            = a non-negotiable project rule the build must honour

  ┌──────────────────────────┐      ┌──────────────────────────┐
  │ [REUSE] Front-load the    │      │ [RULE] Content is sacred  │
  │ existing duplicate-skip:   │      │ — store the raw text      │
  │ peek the fingerprint       │      │ FIRST, before the AI.     │
  │ BEFORE the AI, so a re-    │      │ It must survive even if   │
  │ upload never costs a paid  │      │ the AI is down.           │
  │ AI call                    │      │ (store-raw-first contract)│
  └────────────┬──────────────┘      └────────────┬─────────────┘
               │                                   │
               │       ┌─────────────────────┐     │
               └──────►│  TEXT CAPTURE         │◄────┘
                       │  PIPELINE             │
  ┌──────────────┐     │  Saves text, gets a   │     ┌──────────────────────┐
  │ [REUSE] Ask   │     │  summary, records it  │     │ [RULE] Every AI       │
  │ the AI through ├────►│  — and writes NOTHING│◄────┤ outcome is logged:    │
  │ the existing   │     │  back to the user's   │     │ a success entry AND   │
  │ provider       │     │  folders              │     │ a failure entry, both │
  │ factory; load  │     │                       │     │ to the Audit Log      │
  │ the prompt from│     └──┬────────┬────────┬──┘     │ (set a fresh trace id │
  │ a new prompt   │        │        │        │        │ first, or it silently │
  │ file, not code │        │        │        │        │ drops)                │
  └──────────────┘         │        │        │        └──────────────────────┘
                            │        │        │
            ┌───────────────┘        │        └────────────────┐
            ▼                        ▼                         ▼
  ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
  │ [REUSE] Empty         │ │ [RULE] Attach the     │ │ [RULE] The hand-off   │
  │ knowledge base is     │ │ summary in a SECOND   │ │ to fact-extraction is │
  │ normal — consult the  │ │ small write that does │ │ just ONE log line —   │
  │ facts store, get      │ │ not disturb the saved │ │ no queue, no flag, no │
  │ nothing back, and     │ │ text/fingerprint;     │ │ chat tool. Phase 8    │
  │ summarize on the text │ │ then make it findable │ │ finds its own work    │
  │ alone (degrade to     │ │ in keyword + meaning  │ │ (no tool before its   │
  │ "no context")         │ │ search, best-effort   │ │ pipeline)             │
  └──────────────────────┘ └──────────────────────┘ └──────────────────────┘

  ┌──────────────────────────────────────────────────────────────────────┐
  │ [RULE] On AI failure: keep the stored text, log the failure, and STILL │
  │ report success — so the laptop helper does not retry in a loop. The    │
  │ meaning-search entry waits for a later retry (keyword search covers    │
  │ the gap). The retry runner itself is NOT built in this phase.          │
  └──────────────────────────────────────────────────────────────────────┘

Simplified: The two writes to the Document Store (save raw, then attach
            summary) are drawn here as the single "store FIRST / attach
            SECOND" rule pair, matching the one Document Store box in Q2.
```

**Foundational rule under everything (the reason this phase exists):** This is the moment the project's oldest rule flips. Until now the user's folders were the source of truth and the database was just an index; from here on the **database is the source of truth for AI content**, and capture makes **zero** writes to the user's folders. The cleanup that comes with the flip is surgical: the orphaned "save-from-a-vault-write" routine retires (`documents.upsert(WriteOutcome)`), but the shared routine the chat move tool depends on (`documents.replace_path` → `_derive_title`, reached by `kms_move` via `mcp_server/_move.py:100`) is deliberately kept alive — its retirement is Phase 9, not here.

---

## Approach

Build **bottom-up, leaf-first**, so the test suite stays green at every phase boundary and the old vault-writing capture is deleted **only after** its DB-only replacement exists and passes its own tests. Order: the new prompt (no dependents) → the new DB writer (`attach_summary`, a standalone sibling) → the summarizer stage → the orchestrating entry function → the classify-trigger log line → and **last**, the retirement of the old path plus the CLI re-point and the constraint rewrite. Every new piece returns a `Success`/`Failure` Result and is testable in-process with an explicit `db_path` and an injected stub AI provider. No new schema, no new dependency, no MCP tool.

The new capture entry lives in `src/pipelines/capture.py` alongside the old code during Phases 1–6, then the old code is removed in Phase 7. This keeps each phase independently testable and never leaves the suite red.

---

## Phases

### Phase 1 — New structured-summary prompt (YAML)

**Goal**: Give the Housekeeping AI a single instruction that emits a fixed-header Markdown summary + a clean descriptive title.

Implements **spec Component 1**. `[extensible: config]` — a new prompt is data dropped into the prompts folder; the loader auto-discovers it with no code change.

**Design**:

```
# Phase 1 — New prompt file (folder view)

  src/prompts/
    ├── summarize.yaml            ← EXISTING: plain text, FORBIDS Markdown
    │                               (pattern reference ONLY — do not reuse)
    └── capture_summary.yaml      ← NEW (this phase)
          system: "...emit EXACTLY these headers, in order:
                   ## Overview / ## Key points / ## Decisions /
                   ## Action items / ## People mentioned ...
                   also return a short descriptive title..."
          user:   "{text}"           (bare document text — do NOT over-wrap)
          variables: [text]

  RESULT when rendered + sent to the AI on a sample meeting note:
    a Markdown block containing all five fixed headers + a descriptive title
```

**Steps**:
1. (RED) Add a test that loads the new prompt by name from the prompt registry (`PROMPTS["capture_summary"]`, `llm/prompt_loader.py:54`) and asserts: it exists, `render(text=...)` returns a `(system, user)` tuple (`prompt_loader.py:19`), and the system text contains all five literal header strings (`## Overview`, `## Key points`, `## Decisions`, `## Action items`, `## People mentioned`) and asks for a title. Run — fails (no file).
2. (GREEN) Add `src/prompts/capture_summary.yaml` with `system`/`user`/`variables` keys, the five fixed headers in order, the title instruction, and a `{text}` placeholder in `user`. It is a **generic** content frame — NOT aligned to project/domain dimensions (spec Component 1).
3. Confirm the existing `prompts/summarize.yaml` is left untouched (it is the opposite shape and stays for any legacy reader).

**Files to modify**:
- `src/prompts/capture_summary.yaml` — new file (the prompt)
- `tests/test_llm/test_prompt_loader.py` (or a new `tests/test_prompts/test_capture_summary.py`) — the loader/header test

**Test criteria**:
- [ ] `PROMPTS["capture_summary"].render(text="...")` returns a `(system, user)` tuple
- [ ] The rendered system prompt contains all five fixed headers in order and a title instruction
- [ ] No inline prompt f-string is introduced anywhere (C-07)

**Notes**: Coupling — the five header strings are duplicated between the prompt and the test. That is intentional: the test is the guard that the prompt keeps its contract. No `# COUPLING:` marker needed (it is a test asserting a config file).

**Status**: [ ] pending

---

### Phase 2 — New DB writer: `attach_summary` (the second beat)

**Goal**: Write the AI summary + descriptive title onto the row that already holds the raw text, touching nothing the raw-store beat saved.

Implements **spec Component 3**. `[closed]` — one real adapter (the new pipeline); it exists to honour the two-beat contract, not to abstract. Closed is correct here (research: "not a speculative seam").

**Design**:

```
# Phase 2 — attach_summary: UPDATE only these three columns

  BEFORE (row after Store-Raw-First beat):
    vault_path        = "Projects/Alpha/notes.md"
    full_body         = "<full extracted text>"   ◄─ keep
    content_hash      = "abc123"                    ◄─ keep
    original_filename = "notes.md"                  ◄─ keep
    file_size_bytes   = 8472                         ◄─ keep
    title             = "notes"   (filename stem)   ◄─ overwrite
    summary           = NULL                          ◄─ fill
    updated_at        = 2026-06-14 09:00:00          ◄─ advance

  attach_summary(vault_path, summary, title, db_path)
        │  one get_connection(db_path) transaction (PRAGMA foreign_keys=ON, C-04)
        ▼
  AFTER:
    full_body / content_hash / original_filename / file_size_bytes  ── UNCHANGED
    title      = "Alpha kickoff meeting notes"   (AI title)
    summary    = "## Overview\n...## People mentioned\n..."
    updated_at = 2026-06-14 09:00:42
```

**Steps**:
1. (RED) In `tests/test_storage/test_documents.py`, add tests with an explicit `db_path` (no module-scope CONFIG, C-17): (a) seed a row via `upsert_from_upload(...)`; call `attach_summary(...)`; assert `summary` + `title` updated, `updated_at` advanced, and `full_body`/`content_hash`/`original_filename`/`file_size_bytes` **unchanged**; (b) call `attach_summary` on a non-existent path → returns a `Failure` (or `Success(0)` rowcount) per the convention `delete_by_path`/`rename` use — **match the existing rowcount convention** (`documents.py` — those return `Result[int]`, 0 = not found). Run — fails (no function).
2. (GREEN) Add `def attach_summary(vault_path, summary, title, db_path=None) -> Result[int]` to `src/storage/documents.py`, as a sibling of `upsert_from_upload` (documents.py:156). Use `get_connection(db_path)` (storage/db.py); `UPDATE documents SET summary=?, title=?, updated_at=datetime('now') WHERE vault_path=?`; return `Success(rowcount)` / `Failure` on `sqlite3.Error` (C-12). Do **not** touch any other column.
3. Confirm `title` is always passed a real string (schema `title` is NOT NULL — research edge case); the caller (Phase 4) only calls `attach_summary` on the AI-success path where a title exists.

**Files to modify**:
- `src/storage/documents.py` — add `attach_summary`
- `tests/test_storage/test_documents.py` — preserve/update + not-found tests

**Test criteria**:
- [ ] After `attach_summary`, `summary`+`title`+`updated_at` change; `full_body`+`content_hash`+`original_filename`+`file_size_bytes` are byte-for-byte unchanged
- [ ] `attach_summary` on a missing path returns a `Result` (not an exception), matching the rowcount-0 convention of `delete_by_path`/`rename`
- [ ] Full suite green (no new connection factory; reuses `get_connection`, C-04)

**Notes**: Coupling — `attach_summary` and `upsert_from_upload` both know the `documents` column set. Acceptable: they live in the same deep module whose job is exactly that table.

**Status**: [ ] pending

---

### Phase 3 — Summarizer stage (Housekeeping AI + dormant context)

**Goal**: Turn stored text into a structured Markdown summary + title via the provider factory, briefed by known facts when any exist, and never fail just because the knowledge base is empty.

Implements **spec Component 2**. The AI provider is `[extensible: config]` behind the factory; the stage itself is `[closed]` (one pipeline owns it).

**Design**:

```
# Phase 3 — summarize stage (sequence, with the dormant pre-step)

  document text in
        │
        ▼
  (a) Gather context (DORMANT)
      query Knowledge Facts (query_by_entity / get_confident_and_pending)
        │  empty table → Success([])  → assemble EMPTY brief, no error
        │  (mirrors the existing degrade-to-"no block" pattern)
        ▼
  (b) Ask the AI
      get_provider("capture", CONFIG.main).complete(system, user)   ◄─ async
        │  prompt = PROMPTS["capture_summary"].render(text=<bare text>)
        ▼
  (c) Parse the reply
      split summary text + descriptive title off LLMResponse.content
        │
        ├── AI ok   → Success((summary, title))
        └── AI fail → Failure(...)        (NOT an exception — C-12)
```

**Steps**:
1. (RED) Add `tests/test_pipelines/test_capture_summarize.py` (new file; lazy/explicit CONFIG, C-17). Inject a **stub provider** (a fake `get_provider` returning an object whose async `complete` yields a canned `LLMResponse` with the five-header summary, or a forced `Failure`). Tests: (i) with **zero** knowledge-facts rows, the stage returns a normal `Success((summary, title))` and the empty-context path ran without error (P7-CAP-06); (ii) forced AI failure → the stage returns a `Failure`, not an exception; (iii) on success the returned summary contains the five headers and the title differs from the filename stem. Run — fails.
2. (GREEN) Add an **async** summarize stage function to `src/pipelines/capture.py` (e.g. `async def _summarize_upload(text, ...) -> Result[tuple[str, str]]`). (a) optional context: call the knowledge-facts helpers (`storage/knowledge_entries.py::query_by_entity`, `get_confident_and_pending`); on empty return an empty brief gracefully (pattern ref `mcp_server/context.py`); (b) `provider = get_provider("capture", CONFIG.main)`; `result = await provider.complete(system, user)` (provider.py:40, async, returns `Result[LLMResponse]`); read text off `LLMResponse.content` (provider.py:25-30); (c) parse into `(summary, title)`. Return `Success`/`Failure` (C-12).
3. Pass the **bare** document text to the prompt — do not wrap it in any context-builder (research: bare text gives cleaner search separation).
4. Keep the function free of module-scope `vault.` imports (BUILD RULE — see Open Questions OQ-A; not test-enforced for this file).

**Files to modify**:
- `src/pipelines/capture.py` — add the async summarize stage (lives beside old code for now)
- `tests/test_pipelines/test_capture_summarize.py` — new test file with stub provider

**Test criteria**:
- [ ] Zero knowledge-facts rows → summarize still returns `Success` with the structured summary (P7-CAP-06)
- [ ] Forced AI failure → `Failure` returned, no exception raised (C-12)
- [ ] Provider reached only via `get_provider("capture", CONFIG.main)` — no direct provider instantiation (C-08); prompt only via `PROMPTS` (C-07)

**Notes**: Coupling — the summarize stage hardcodes the task name `"capture"`. That is the config-driven task key (already in the `Task` literal, config.py:44), not a provider — so it is config, not code coupling. No marker needed.

**Status**: [ ] pending

---

### Phase 4 — Capture entry: front-loaded dedup → store → summarize → attach → trigger

**Goal**: One new entry function that orchestrates the whole text path with the dedup check provably ahead of the AI, honouring the store-raw-first contract.

Implements **spec Component 4** (and wires in Components 2, 3, and the Phase 5 trigger). `[closed]` — the single capture entry; callers (endpoint, dev CLI) see one verb.

**Design**:

```
# Phase 4 — capture_upload(...) orchestration (numbered steps + outcomes)

  capture_upload(vault_path, extracted_text, content_hash,
                 original_filename, file_size_bytes, db_path=None)
        │  FIRST LINE: new_correlation_id()   ◄─ or every audit write silently drops
        ▼
  1. Front-loaded dedup (P7-CAP-01)
     row = get_by_path(vault_path)
     if row exists AND row.content_hash == content_hash:
            → return Success   ── AI NEVER called, summary/updated_at untouched
        │  (else continue)
        ▼
  2. Store raw first (P7-CAP-02 / P7-CAP-05)
     upsert_from_upload(..., title=stem)   → full_body saved, summary NULL,
                                              project NULL, vault_path verbatim
        ▼
  3. Summarize  (await the Phase 3 stage)
        │
        ├── 4. AI SUCCESS:
        │       attach_summary(vault_path, summary, title)   (Phase 2)
        │       index_keywords(vault_path, title, summary, body)   best-effort
        │       index_embedding(vault_path, title, …, summary)     best-effort
        │       audit.write(CAPTURED)        ◄─ AFTER the physical writes
        │       classify trigger log line    (Phase 5)
        │       → return Success (P7-CAP-03)
        │
        └── 5. AI FAILURE:
                audit.write(<failure outcome>)   (C-13)
                (NO meaning-index — no summary yet)
                → return Success  ◄─ store-anyway, daemon does not loop (P7-CAP-04)
```

**Steps**:
1. (RED) Add `tests/test_pipelines/test_capture_upload.py` (new; explicit `db_path`, stub provider, C-17). Assert all seven Done-when cases the spec lists for Component 4: P7-CAP-01 (re-upload identical → `get_provider` **never invoked**, summary+`updated_at` unchanged), P7-CAP-02 (new upload → row has `full_body`, `summary` NULL at the store-raw point), P7-CAP-03 (success → summary+title present, findable by keyword+meaning search), P7-CAP-04 (forced failure → row stored, summary NULL, **failure audit entry written**, returns `Success`), P7-CAP-05 (`project`/domain NULL, vault_path stored verbatim), P7-CAP-06 (empty knowledge base completes), P7-CAP-07 (same content under two paths → two rows). Spy on the stub provider to prove the dedup short-circuit is **ahead of** the AI. Run — fails.
2. (GREEN) Add `async def capture_upload(vault_path, extracted_text, content_hash, original_filename=None, file_size_bytes=None, db_path=None) -> Result[int]` to `src/pipelines/capture.py`. Take the upload fields — **not** a `Path`, and **no** `vault.` types at module scope (use plain str/int/`Any`). Order exactly: `new_correlation_id()` first (core/logging_setup.py:55) → `get_by_path` dedup peek (documents.py:253) → `upsert_from_upload(..., title=stem)` (documents.py:156) → `await _summarize_upload(...)` → success/failure branches as in the design box.
3. Wire the audit entries via `core.audit.write(decision, pipeline="capture", stage=..., outcome=..., db_path=...)` (audit.py:11) with an `AIDecision` (core/confidence.py:36 — `action`+`reasoning` non-empty, `confidence` 0.0–1.0, `source_ids`=`[vault_path]` per OQ-7C). **`CAPTURED` audit fires only after** `attach_summary` succeeds (the codebase "audit after the action" rule). The failure audit fires on the failure branch.
4. Best-effort indexing **after** attach, keyed on `vault_path`: `index_keywords(vault_path, title, summary, body)` (keyword.py:10) and `index_embedding(vault_path, title, …, summary)` (embeddings.py:42), each in its own try/except so an index error never sinks the capture. Pass `summary or ""` defensively to `index_keywords` (research edge case: param typed `str`).
5. Do **not** call `index_embedding` on the AI-failure branch (no summary → weak embedding; deferred to retry per OQ-7B).

**Files to modify**:
- `src/pipelines/capture.py` — add `capture_upload` (still beside old code)
- `tests/test_pipelines/test_capture_upload.py` — new orchestration tests

**Test criteria**:
- [ ] Re-upload identical → provider never called; `summary`+`updated_at` unchanged (P7-CAP-01)
- [ ] New upload → `full_body` set, `summary` NULL at store-raw point; `project`/domain NULL; vault_path verbatim (P7-CAP-02, P7-CAP-05)
- [ ] Success → structured summary + descriptive title, findable by keyword AND meaning search (P7-CAP-03)
- [ ] Forced AI failure → row stored, `summary` NULL, **failure audit entry present**, returns `Success` (P7-CAP-04, C-13)
- [ ] Empty knowledge base completes (P7-CAP-06); identical content under two paths → two rows (P7-CAP-07)
- [ ] `new_correlation_id()` is the first action (audit writes do not silently drop)

**Notes**: This phase touches `capture.py` + one test file + (transitively) the audit/confidence types — within the 3–4 file limit. The live `/api/upload` endpoint is wired to this new entry in **Phase 4b** (decided 2026-06-14 — OQ-B resolved: wire it in 7A so capture is end-to-end testable via the real entry point, not just the dev CLI).

**Status**: [ ] pending

---

### Phase 4b — Wire the live `/api/upload` endpoint to `capture_upload` (OQ-B resolved)

**Goal**: Make the cloud's real entry point run the new capture. The daemon (Phase 6) and `curl` then exercise the full pipeline end-to-end, not just the dev CLI.

**Steps**:
1. In `mcp_server/api.py::upload_handler`, replace the direct `upsert_from_upload(...)` call with `await capture_upload(...)` (the Phase 4 entry), passing the validated upload fields (`vault_path`, `extracted_text`, `content_hash`, `original_filename`, `file_size_bytes`). The handler stays logic-free (validation + dispatch only); `capture_upload` owns the store-raw → summarize → attach → index → audit flow internally (so the raw store still happens first via `upsert_from_upload`, now called *inside* `capture_upload`).
2. Response contract: return `{"status": "ok", "document_id": ...}` on `Success` (unchanged shape); on the AI-failure-store-anyway path `capture_upload` returns `Success` too, so the endpoint still reports `ok` with a stored document (summary fills in later). Keep the existing 400/401/500 handling.
3. Latency note: the request now spans the summarizer LLM call (~5–12s, within the rearchitecture budget). Acceptable for the daemon's per-file POST; revisit a background-task split only if it becomes a problem (out of scope here).
4. The dedup short-circuit (P7-CAP-01) lives inside `capture_upload`, so a re-upload of identical content returns fast (no LLM) through the same endpoint.

**Files to modify**:
- `src/mcp_server/api.py` — `upload_handler` calls `capture_upload` instead of `upsert_from_upload` directly.
- `tests/test_mcp_server/test_api.py` — update the upload tests: a real upload now produces a summary in the DB; a duplicate re-upload calls no provider; a forced AI failure still returns `ok` with the row stored.

**Test criteria**:
- [ ] `POST /api/upload` with new text → document row has `full_body` + structured `summary` + descriptive title; findable by search (P7-CAP-02, P7-CAP-03).
- [ ] `POST /api/upload` with identical `content_hash` → provider never called, fast return (P7-CAP-01).
- [ ] Forced AI failure on the endpoint path → `200 ok`, row stored, `summary` NULL, failure audit entry present (P7-CAP-04).
- [ ] Handler remains logic-free (validation + dispatch); no business logic added to `api.py`.

**Depends on**: Phase 4 (`capture_upload` must exist).

**Status**: [ ] pending

---

### Phase 5 — Classify trigger (no-op log stub)

**Goal**: Mark in the log only that a captured document is ready for later fact-extraction — no queue, no flag, no MCP tool.

Implements **spec Component 5**. `[closed]` (a single log line). C-15: no MCP tool added.

**Design**:

```
# Phase 5 — classify trigger

  (end of a SUCCESSFUL capture_upload)
        │
        ▼
  logger.info("capture.classify_ready vault_path=%s", vault_path)
        │   (one line; %s-style, NOT kwargs — stdlib logging, see CLAUDE.md)
        ▼
  NO queue row · NO flag column · NO marker · NO MCP tool
```

**Steps**:
1. (RED) Add a test (extend `test_capture_upload.py`): on a successful capture, exactly one classify-ready log line is emitted (capture logs via `caplog`), and **no** new DB row/flag is created beyond the document row (e.g. assert no `batches`/queue side-effect). Run — fails.
2. (GREEN) Add one `logger.info("capture.classify_ready vault_path=%s", vault_path)` at the tail of the success branch of `capture_upload`. Use `%s`-style formatting (stdlib `logging` does not take kwargs — CLAUDE.md gotcha). No table, no column, no tool.

**Files to modify**:
- `src/pipelines/capture.py` — one log line in `capture_upload`'s success tail
- `tests/test_pipelines/test_capture_upload.py` — log-line + no-side-effect assertion

**Test criteria**:
- [ ] A successful capture emits exactly one classify-ready log line (P7-CAP-08)
- [ ] No queue row, flag column, or marker is created; the document is searchable before any classify runs

**Notes**: Folds naturally into Phase 4; kept as a labelled step so the spec component maps 1:1 and the C-15 "no tool before its pipeline" rule is visibly honoured.

**Status**: [ ] pending

---

### Phase 6 — Retire the old path-based capture + dead `WriteOutcome` couplings + re-point the dev CLI

**Goal**: Delete the old vault-writing capture so the only capture path is the new DB-only one; re-point `kms capture`; remove the orphaned `upsert(WriteOutcome)` — **without** touching modules other live code still needs.

Implements **spec Component 6** (A9 resolved). This is the **last** phase so the suite is green at every prior boundary and the replacement (Phases 1–5) already exists.

**Design**:

```
# Phase 6 — retirement boundary (KEEP vs DELETE, research-verified)

  DELETE from pipelines/capture.py:
    capture_file(Path) · scan_capture · capture_folder · classify_step
    _store_md · _store_nonmd · _classify_auto_md_move · apply_location_tags
    all write_note()/move_note() calls · all WriteOutcome usage
    sibling .md creation · build_registry/ProjectRegistry use

  DELETE from storage/documents.py:
    upsert(WriteOutcome)            ◄─ callers ONLY old capture (1062/1268/1416)

  RE-POINT cli/main.py:
    kms capture <file>:  extract text locally → capture_upload(...)  (in-process)
    kms watch:           (see Notes — its capture_file/scan_capture calls retire too)

  ── KEEP (live non-capture consumers — DO NOT DELETE) ──────────────────
    documents.replace_path(WriteOutcome)   ◄─ kms_move via _move.py:100
    the WriteOutcome import in documents.py ◄─ replace_path signature
    _derive_title(outcome)                  ◄─ called by replace_path
    upsert_from_upload · get_by_path · all_paths · delete_by_path ·
    rename · filter_paths · update_batch_id   ◄─ api.py / retrieval / indexer
    vault/writer.py · watcher · indexer       ◄─ Phase 6-daemon / shared (ADR-0012)
```

**Steps**:
1. **Re-point the dev CLI first** (so a working entry exists before deletion). In `cli/main.py`, change `kms capture <file>` (main.py:65, :83): import the file handler/extractor + `capture_upload`; extract text from the local file, compute/obtain a content hash, then `asyncio.run(capture_upload(vault_path=..., extracted_text=..., content_hash=..., original_filename=..., file_size_bytes=...))`. It writes **no** vault file, frontmatter, or sidecar `.md` (P7-CAP-09). Keep the `asyncio.run` wrapper (C-10).
2. **Delete `kms watch` and `pipelines/reconcile.py`** (OQ-C resolved 2026-06-14 — delete both now). `kms watch` (main.py:341, :360, :388 call `capture_file`/`scan_capture`) is the old local-vault watcher, superseded by the Phase 6 daemon; `pipelines/reconcile.py` (imports `capture_file` at top level + :138, :199) is the old 7-stage reconcile, superseded by the Phase 6 startup scanner (ADR-0013 §A3). Both are dead in the cloud model and nothing shipped to users (clean slate). Remove the `kms watch` Click command from `cli/main.py`, delete `src/pipelines/reconcile.py` entirely, and delete their tests (`tests/test_pipelines/test_reconcile.py`, `tests/test_vault/test_watcher.py` as applicable). This removes the `capture_file`/`scan_capture` import coupling so those functions can retire cleanly in step 3.
3. **Delete the old capture functions** listed in the design box from `pipelines/capture.py`. Remove now-dead imports (`write_note`, `move_note`, `WriteOutcome`, `build_registry`/`ProjectRegistry`, frontmatter/sibling helpers) from that file only.
4. **Retire `documents.upsert(WriteOutcome)`** (documents.py:100) — its three callers (capture.py:1062/1268/1416) are gone. **KEEP** `replace_path`, the `WriteOutcome` import (documents.py:24), and `_derive_title` (documents.py:80) — all reached by the live `kms_move` tool (verified: `_move.py:100`).
5. **Rewrite/delete the broken tests** (research blast radius): `tests/test_pipelines/test_capture.py`, `test_capture_phase3.py`, `test_capture_phase9.py`, `test_capture_phase10.py`, `test_capture_phase12.py`, `test_capture_folder.py`, `test_capture_rename.py`, `test_capture_search.py`, `test_capture_integration.py`, `test_reconcile.py`, `tests/test_pipelines/conftest.py`, `tests/test_vault/test_watcher.py`, `tests/test_vault/test_paths.py`. Also in `tests/test_storage/test_documents.py` / `test_documents_search.py`: **keep** the `replace_path` tests (function stays); **delete** the `upsert(WriteOutcome)` tests (function retires). Delete tests that exercised vault-writing behavior outright; rewrite any that assert a capability still relevant (none are expected — capture no longer writes vault files). `test_reconcile.py`: `pipelines/reconcile.py` imports `capture_file` (reconcile.py top-level) — reconcile is **not** Phase 7's owner (its successor is the Phase 6 startup scanner, ADR-0013 §A3), so removing `capture_file` breaks reconcile's import. See Open Questions OQ-C for reconcile's fate.
6. Run the full suite green; confirm `test_pipeline_has_no_heavy_imports` (test_pipeline_phase1.py) still passes (it greps `core/pipeline.py`, not the new capture — but no new `vault.` import should appear at `core/pipeline.py` scope); confirm `kms_move` and other live callers of the retained functions still work (run their existing tests).

**Files to modify** (this phase necessarily spans more than 3–4 files because it is a deletion sweep; the *source* edits are 3: `pipelines/capture.py`, `storage/documents.py`, `cli/main.py` — the rest are test deletions/rewrites):
- `src/pipelines/capture.py` — delete old functions + dead imports
- `src/storage/documents.py` — delete `upsert(WriteOutcome)` only
- `src/cli/main.py` — re-point `kms capture`; **delete the `kms watch` command** (OQ-C)
- `src/pipelines/reconcile.py` — **delete entirely** (OQ-C; successor is the Phase 6 scanner, ADR-0013)
- ~13 test files — rewrite or delete per the blast-radius list

**Test criteria**:
- [ ] The old vault-writing functions no longer exist in `pipelines/capture.py`
- [ ] `kms capture <file>` runs `capture_upload` in-process and writes **no** vault file/frontmatter/sidecar `.md` (P7-CAP-09)
- [ ] `documents.upsert(WriteOutcome)` is gone; `replace_path` + `WriteOutcome` import + `_derive_title` remain
- [ ] `kms_move` and all other live callers of retained functions still pass their tests
- [ ] Full suite green; `test_pipeline_has_no_heavy_imports` still passes

**Notes**: Known coupling — this phase deliberately leaves `replace_path`/`WriteOutcome`/`_derive_title` alive despite them being capture-flavoured, because `kms_move` needs them. Their retirement is Phase 9 (MCP adaptation). This is the A9 resolution, not tech debt to fix here.

**Status**: [ ] pending

---

### Phase 7 — Rewrite C-01 / C-03 in CONSTRAINTS.md (the DB-as-source-of-truth flip)

**Goal**: Make the constraint file reflect reality after the flip — capture writes zero vault files; the database is the source of truth for AI content.

This is the spec's and design's explicit "C-01/C-03 must be rewritten as part of this phase" requirement. Not code — a docs/guardrail step, run via `/guardrail-check`.

**Design**:

```
# Phase 7 — constraint flip (before → after)

  C-01 BEFORE: "Vault is source of truth; documents table is index only"
       (currently carries a ⚠️ Rearchitecture note: 'flip happens in Phase 6/7')
  C-01 AFTER:  "Database is source of truth for AI content; the user's vault is
               read-only user input. Capture makes ZERO vault writes."

  C-03 BEFORE: "write_note is a pure writer — pipeline owns the merge"
       (currently carries a ⚠️ Rearchitecture note)
  C-03 AFTER:  scope to the RETAINED writer consumers (kms_move, Phase 6 daemon);
               capture no longer calls write_note at all.

  Keep CLAUDE.md "## Constraint Index" + CONSTRAINTS.md in sync.
```

**Steps**:
1. Run `/guardrail-check` to confirm exactly which constraints this phase changes (expect C-01, C-03; verify C-02 wording given `write_note` retires from capture but stays for the move tool / daemon).
2. Rewrite C-01 and C-03 in `CONSTRAINTS.md` to state the flip — preserve the section structure (Severity/Domain/Rule/Why/Danger signal/Source). Note in Source that the flip shipped in Phase 7A. Update the `⚠️ Rearchitecture` notes from "will flip" to "flipped 2026-06 in Phase 7A."
3. Keep the `## Constraint Index` in `CLAUDE.md` consistent (the one-line summaries).
4. If `/guardrail-check` surfaces a new undocumented constraint or tech debt (e.g. the heavy-imports-test gap, OQ-A), record it via `/guardrail-check Write` (TECH_DEBT.md / OPEN_QUESTIONS.md).

**Files to modify**:
- `CONSTRAINTS.md` — rewrite C-01, C-03 (and reconcile C-02 wording)
- `CLAUDE.md` — keep `## Constraint Index` in sync (if wording changes)

**Test criteria**:
- [ ] C-01 reads "database is source of truth; vault read-only; capture makes zero vault writes"
- [ ] C-03 scoped to retained writer consumers (move tool / daemon), not capture
- [ ] `/guardrail-check` reports no stale "vault is source of truth" rule still claiming capture writes the vault
- [ ] CLAUDE.md Constraint Index matches CONSTRAINTS.md

**Notes**: Ordered last because the flip is only *true* once Phase 6 has removed the last vault-writing capture path. Rewriting the constraint before the code would make the rule lie.

**Status**: [ ] pending

---

## Open Questions

These are flags for the human/orchestrator before or during implementation. **OQ-B and OQ-C are now RESOLVED (2026-06-14)** and folded into the plan (Phase 4b + Phase 6). OQ-A remains as non-blocking tech debt.

- **OQ-A — Extend the heavy-imports test to cover the new capture? (tech debt, non-blocking).** Research found `test_pipeline_has_no_heavy_imports` greps **only** `core/pipeline.py`, not `pipelines/capture.py` — so "no module-scope `vault.` import in the new capture" is a BUILD RULE with no test guard. Recommendation: keep it a build rule for 7A; log a tech-debt item to optionally add a source-grep test on `pipelines/capture.py` later. (Out of 7A's stated scope.)
- **OQ-B — RESOLVED (2026-06-14): wire `/api/upload` in 7A.** Added as **Phase 4b** — `upload_handler` calls `capture_upload` so capture is end-to-end testable via the real entry point (curl/daemon), not just the dev CLI. The endpoint stays logic-free; latency now spans the LLM call (~5–12s, within budget); the dedup short-circuit keeps re-uploads fast.
- **OQ-C — RESOLVED (2026-06-14): delete both now (option a).** `kms watch` and `pipelines/reconcile.py` are deleted in Phase 6 — both are the old local-vault model, superseded by the Phase 6 daemon + startup scanner (ADR-0013 §A3), and nothing shipped to users (clean slate). This removes the `capture_file`/`scan_capture` import coupling so those functions retire cleanly. Their tests are deleted with them.
- **OQ-7A / OQ-7B / OQ-7C (carried from spec/design, all non-blocking).** Summary storage shape (re-parse Markdown if section access ever needed); AI-failure embedding timing (wait for retry — keyword search covers the gap); audit `source_ids` identifier (use vault_path for 7A). All confirmed non-blocking by research; recommendations adopted in the plan above.

## Out of Scope

Per spec "Out of scope" — not restated, summarized:
- **Binary/visual capture** (blob storage, vision call, migration 009, `"vision"` task) — **Phase 7B**.
- **Opening `/api/upload` to raw bytes** — Phase 6/7B.
- **The summary retry runner** — deferred, no phase assigned (the contract `summary IS NULL` = "needs summary" + the failure audit trail ship here; the job that re-summarizes does not).
- **Provisional body-only embedding on AI failure** — deferred (OQ-7B).
- **Project/domain classification** — Phase 8 (capture sets none, feeds no folder name to the AI).
- **Section-level addressing of the summary** — deferred (OQ-7A).
- **The vault↔cloud reconcile** — successor is the Phase 6 startup scanner (ADR-0013). 7A **deletes** `pipelines/reconcile.py` (OQ-C) rather than porting it; the scanner is built in Phase 6.
- **Deleting `vault/writer.py`, the watcher, the indexer** — shared with Phase 6 (ADR-0012); 7A stops *importing* `WriteOutcome` from capture but deletes none of those modules.
- **Retiring `replace_path` / `WriteOutcome` / `_derive_title`** — kept alive for `kms_move`; their retirement is Phase 9 (A9 resolution).
