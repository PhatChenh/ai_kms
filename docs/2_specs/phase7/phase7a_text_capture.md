# Phase 7A — Text Capture (cloud capture refactor, text path only)

_Spec. Output of `/writing-detailed-specs`. Feeds `/research` then `/plan-from-specs`._
_Design input: `docs/1_design/phase7/phase7_capture_refactor.md` (Option A — Store-Raw-First; the three resolved decisions; implications/risks). From the ADR-0013/0015 Amendment, only the two text-path corrections are carried (see §A2 / §A3 below)._
_Requirements: `docs/0_draft/phase7/phase7_capture_refactor_requirements.md` (+ Amendment + Slice-split note)._
_Behavior IDs: **P7-CAP-01…09** in `docs/system_behavior/behavior_inventory.yaml`. (P7-CAP-10…13 are 7B — out of scope here.)_
_ADRs: ADR-0014 (capture data-safety contract), ADR-0013 (raw-byte hash + reconcile ownership), ADR-0012 (additive rearchitecture / consumer-refactor sequencing)._
_Reader mode: non-coder-readable (default). Plain English leads every section; code references are parenthetical anchors. The spec should make sense even if every code token were deleted._

---

## Purpose

Today "capture" reads a file off the user's disk, asks the AI to summarize it, and then **writes new files back into the user's folders** (a summary note, hidden sidecar files, sometimes moving the original). Phase 7A rewrites the cloud's text-capture so it does exactly three jobs and nothing else: **save the uploaded text safely, ask the AI for a structured summary, and record it all in the central database.** After this phase, capture never writes to the user's folders, never moves files, and never guesses which project a document belongs to.

The guiding rule (locked in the requirements, formalized in ADR-0014): **the user's content is sacred; the AI summary is a nice-to-have that may arrive late.** So we save the raw text the instant it arrives; if the AI is down we still keep the document, audit the failure, and report success — the summary can fill in later.

This spec covers the **text path only**. Uploads that carry raw file bytes with no extracted text (photos, scans, charts) are the **visual/binary path (Phase 7B)** and are explicitly out of scope here.

---

## Already built (reuse, do not rebuild)

| Function / Module | Location | What it does (plain English) | How this spec uses it | Depth |
|---|---|---|---|---|
| Upload endpoint | `mcp_server/api.py::upload_handler` | Cloud front door; validates a daemon upload (vault_path, extracted_text, content_hash all required) and calls the raw-store routine | The pipeline runs after this. Today the handler calls only the raw-store; Phase 7A re-points it (or wraps it) at the new pipeline entry so summarize + audit + index happen | deep |
| Raw-store routine | `storage/documents.py::upsert_from_upload` | Save-or-update by vault_path: new path → INSERT; same `content_hash` → **skip (return existing id)**; changed hash → UPDATE. Stores `full_body`, `original_filename`, `file_size_bytes`, `title`, `content_hash` | This **is** the store-raw-first beat AND the front-loaded dedup (its skip-identical branch). Used unchanged | deep |
| Lookup by path | `storage/documents.py::get_by_path` | Fetch the existing document row for a vault_path (returns the row, or "none found") | Used to peek for an existing identical-hash row **before** the AI call, so duplicates never reach the summarizer | deep |
| Document row shape | `storage/documents.py::DocumentRow` | The in-memory shape of one document (carries `full_body`, `summary`, `title`, `content_hash`, `project`, etc.) | Read to check existing `content_hash`; `project`/`summary` stay NULL at capture | deep |
| AI provider factory | `llm/provider.py::get_provider` | Returns the right AI provider for a named task (config-driven, never hardcoded) | The summarizer calls `get_provider("capture", CONFIG.main)`; `"capture"` already exists in the task list | deep |
| AI text call | `llm/provider.py::LLMProvider.complete(system, user)` | One text-in / text-out AI call, returns a Result | The single Housekeeping-AI summarize call | deep |
| Prompt loader | `llm/prompt_loader.py` (`PROMPTS`) | Loads every prompt from YAML files in `prompts/` | Loads the structured-summary prompt (new YAML); no inline prompt strings (C-07) | shallow |
| Existing summarize prompt | `prompts/summarize.yaml` | Today's plain-text 2–4 sentence summary prompt | **Pattern reference only** — Phase 7A needs a NEW prompt that emits fixed Markdown headers; do not silently reuse the plain-text one | shallow |
| Keyword indexer | `retrieval/keyword.py::index_keywords(vault_path, title, summary, body)` | Adds a document to the full-text (keyword) search index | Called best-effort after the summary attaches, keyed on vault_path | deep |
| Meaning indexer | `retrieval/embeddings.py::index_embedding(vault_path, title, …, summary)` | Adds a document to the meaning (semantic) search index; folds title + summary into the encoded text | Called best-effort after the summary attaches; on AI failure there is no summary so this is deferred to retry (see Out of scope / OQ-7B) | deep |
| Audit writer | `core/audit.py::write(decision, pipeline, stage, outcome)` | Records one AI decision/outcome in the tamper-evident audit log; takes an `AIDecision` (action, confidence, reasoning, source_ids) | Writes the success (`CAPTURED`) entry and the AI-failure entry (C-13) | deep |
| AI decision envelope | `core/confidence.py::AIDecision` | The "show your work" record (action, confidence, reasoning, source_ids) every audit entry needs | Built for both the success and failure audit entries | deep |
| Knowledge-facts queries | `storage/knowledge_entries.py::query_by_entity`, `get_confident_and_pending` | Look up known facts to optionally brief the AI before summarizing | The dormant context-injection pre-step calls these; must degrade to "no context" gracefully when empty | deep |
| Graceful-empty pattern | `mcp_server/context.py` | Reference for degrading missing context to "no block" rather than erroring | Pattern to copy for the empty-knowledge-base fallback | shallow |
| DB connection | `storage/db.py::get_connection` | The one connection factory (enforces `PRAGMA foreign_keys=ON`) | The new attach-summary routine uses it; no new connection factory (C-04) | deep |

---

## Q1 Diagram — what happens inside (text branch, from the design doc)

_Extracted from the design doc's two-input-fork Q1, showing ONLY the text branch (2a → 3a → 4 → 5). The binary/visual branch (2b/3b) is omitted — it is Phase 7B._

```
# Phase 7A Text Capture — What Happens Inside (text branch only)
Scope: What happens when the daemon uploads ONE item of extracted text.
       Does NOT cover daemon-side extraction, the binary/visual branch
       (Phase 7B), fact-extraction (Phase 8), or move/delete events.

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

   Daemon uploads ONE item:
   extracted text + folder path + filename + size
   + content fingerprint (over RAW FILE BYTES)
                    │
                    ▼
      ┌────────────────────────────┐
      │ 1. Capture Receiver        │
      │ Validate upload; compare   │
      │ raw-byte fingerprint to    │
      │ stored rows                │
      └────────────┬───────────────┘
                   │
          "Seen this exact
           content before?"
            ┌──────┴───────┐
           YES             NO
            │               │
            ▼               ▼
   ┌────────────────┐  ┌────────────────────┐
   │ Stop early —   │  │ 2. Store Raw First │
   │ already        │  │ Save full text +   │
   │ processed.     │  │ filename + size +  │
   │ No AI runs     │  │ fingerprint; EMPTY │
   └────────────────┘  │ summary (safe now) │
                       └─────────┬──────────┘
                                 │
                                 ▼
                       ┌────────────────────┐
                       │ 3. Housekeeping    │
                       │ AI Summarizer      │
                       │ One AI call →      │
                       │ structured summary │
                       │ + descriptive title│
                       └─────────┬──────────┘
                                 │
                         "Did the AI succeed?"
                       ┌─────────┴──────────┐
                      YES                   NO
                       │                     │
                       ▼                     ▼
     ┌────────────────────────────┐  ┌────────────────────────────┐
     │ 4. Attach Summary          │  │ Record failure in Audit    │
     │ Update SAME row with       │  │ Log; leave summary empty;  │
     │ summary + title; index for │  │ STILL return success.      │
     │ keyword + meaning search;  │  │ Text row searchable now;   │
     │ write success to Audit Log │  │ summary lands on retry     │
     └────────────┬───────────────┘  └────────────────────────────┘
                  │
                  ▼
     ┌────────────────────────────┐
     │ 5. Classify Trigger        │
     │ Log one line: ready for    │
     │ later fact-extraction      │
     │ (no-op stub today)         │
     └────────────────────────────┘

Simplified: "Gather Context (dormant)" is folded into the Housekeeping AI
            Summarizer box (an optional pre-step of the same stage). On AI
            failure the Classify Trigger adds nothing actionable, so it is
            omitted from the failure branch.
```

## Q2 Diagram — How it connects to others

```
# Text Capture — How It Connects
Scope: Shows what the Text Capture Pipeline touches. Does NOT show its
       internal steps (see Q1 for that) and does NOT cover the
       binary/visual path (object storage, vision) — that is Phase 7B.

How to read this:
  Center box     = the feature being built
  Solid boxes    = components that already exist
  Dashed boxes   = planned / not lit up yet
  Arrow labels   = what passes between them

        ┌──────────────┐         ┌──────────────────────────┐
        │ Daemon       │         │ Knowledge Facts          │
        │ (laptop      │         │ (empty until Phase 8)    │
        │  helper)     │         └ ─ ─ ─ ─ ─ ┬ ─ ─ ─ ─ ─ ─ ┘
        └──────┬───────┘                     ┆ consults for context
   uploads     │                             ┆ (empty today → no change)
   extracted   │ sends text +                ┆
   text        ▼ fingerprint                 ┆
        ┌──────────────┐                     ┆
        │ Upload       │                     ┆
        │ Endpoint     │                     ┆
        │ Cloud front  │                     ┆
        │ door         │                     ┆
        └──────┬───────┘                     ┆
   hands the   │                             ┆
   upload to   ▼                             ▼
        ┌─────────────────────────────────────────────┐
        │           TEXT CAPTURE PIPELINE             │
        │   Saves text, gets a summary, records it    │
        └──┬────────┬─────────────┬──────────┬────────┘
           │        │             │          │
   asks    │        │ saves raw   │ writes   │ hands off
   for a   │        │ text, then  │ success  │ "ready for
   summary │        │ attaches    │ + failure│ facts"
   + title │        │ summary     │ entries  │ (log line)
           ▼        ▼             ▼          ▼
   ┌────────────┐ ┌────────────┐ ┌────────┐ ┌ ─ ─ ─ ─ ─ ─ ┐
   │Housekeeping│ │ Document   │ │ Audit  │ │ Classify     │
   │AI          │ │ Store      │ │ Log    │ │ (Phase 8)    │
   │Summarizer  │ │ text+sumry │ │        │ └ ─ ─ ─ ─ ─ ─ ┘
   └────────────┘ └─────┬──────┘ └────────┘
                        │ once summary is attached,
                        │ make it findable
                        ▼
                 ┌──────────────┐
                 │ Search       │
                 │ Indexer      │
                 │ keyword +    │
                 │ meaning      │
                 └──────────────┘

Simplified: "Document Store (raw save)" and "Document Store (summary attach)"
            are shown as one "Document Store" box — they are two writes to the
            SAME document row (save first, attach summary second).
            Knowledge Facts and Classify (Phase 8) are dashed: wired now,
            but inert until Phase 8 fills the facts table / runs classify.
```

---

## Feature overview

**Happy path (text upload, AI healthy):**

1. **The front door receives an upload.** The laptop daemon sends extracted text plus a folder path, filename, size, and a content fingerprint (a short code computed over the file's *raw bytes* by the daemon). The cloud endpoint validates the request, as it does today.
2. **The receiver checks "have we seen this exact content before?"** It compares the supplied fingerprint to what is already stored for that folder path. If a row with the **same** fingerprint exists, the upload is a duplicate — the pipeline stops early, reports success, and **never calls the AI** (so a re-upload never costs a summary). This dedup check happens **before** the AI call — that ordering is the whole point (P7-CAP-01).
3. **Store the raw text first.** For genuinely new or changed content, the full extracted text, filename, size, and fingerprint are saved to the central document record immediately, with an **empty summary** and a placeholder title (the filename stem). The user's content is now safe and already findable by keyword search (P7-CAP-02). Capture sets **no project and no domain** — it only stores the folder path verbatim so a later phase can interpret it (P7-CAP-05).
4. **Ask the Housekeeping AI for a structured summary.** One AI call produces a summary with fixed named sections — **## Overview / ## Key points / ## Decisions / ## Action items / ## People mentioned** — as one block of human-readable Markdown, plus a clean descriptive title that overrides the filename stem. (Optionally, before the call, the pipeline consults known facts to brief the AI — but that knowledge store is empty today, so the consult finds nothing and the AI summarizes on the text alone.)
5. **Attach the summary to the same record.** The summary and the descriptive title are written onto the same document row, leaving the already-saved text and fingerprint untouched. The document is then added to both search indexes (keyword and meaning) best-effort, and a success entry (`CAPTURED`) is written to the audit log (P7-CAP-03).
6. **Trigger classify — a no-op.** The pipeline logs a single line saying this document is ready for later fact-extraction. No queue, no flag, no marker. The pipeline returns success (P7-CAP-08).

**Edge case — AI fails (model down, timeout, garbage response):** The document is **already saved** from step 3. The pipeline writes a **failure entry** to the audit log explaining why the summary is missing, leaves the summary empty, and **still returns success** so the daemon does not loop. The document remains keyword-searchable by its full text; the summary (and the meaning-search entry, which needs the summary) fills in on a later retry. The retry runner itself is **not** built in this phase (P7-CAP-04).

**Edge case — empty knowledge base:** Every brand-new user starts with zero known facts, so the "no context found → summarize on text alone" path is a permanent production path, not a temporary one. It is built and explicitly tested now even though it lights up fully only in Phase 8 (P7-CAP-06).

**Edge case — same content in two folders:** If identical content is uploaded under two different folder paths, two independent document rows are created, each with its own summary. Dedup is keyed on folder-path **plus** fingerprint, not on content alone (P7-CAP-07).

**Dev convenience:** A single thin developer command (`kms capture <file>`) still exists. It extracts text from a local file and calls the **new** pipeline directly, in-process — so a developer can test capture without standing up the daemon. It writes no vault files, no frontmatter, no sidecar `.md` (P7-CAP-09).

---

## Out of scope

- **Binary / visual capture (raw file bytes, no extracted text).** The whole second input shape — object/blob storage, the vision-describe AI call, the S3 write client, migration 009 (blob-reference columns + mime type), the `"vision"` task and `vision_model` config. **Handled by Phase 7B** (own spec → plan). Behavior IDs P7-CAP-10…13.
- **Opening the `/api/upload` gate to accept raw bytes.** Today the endpoint hard-rejects a body with no extracted text (HTTP 400). Loosening that wire format is **Phase 6 / 7B**, not 7A. 7A's pipeline assumes extracted text is present.
- **The summary retry runner.** Phase 7A ships the contract (empty summary = "needs summary") and the failure audit trail, but the job that later re-summarizes `summary IS NULL` documents is **deferred — no phase assigned yet** (ADR-0014).
- **Provisional meaning-search embedding on AI failure.** On AI failure there is no summary, so no meaning-search entry is created until retry; keyword search covers the interim. Whether to build a body-only provisional embedding is **deferred (OQ-7B).**
- **Project / domain classification.** Capture sets no project or domain and does not feed the folder name to the AI. **All** project/domain reasoning, including whether to trust the folder, moves to **Phase 8.**
- **Section-level addressing of the summary.** The summary is one Markdown block, not machine-addressable per section. Re-parsing or a structured column is **deferred (OQ-7A).**
- **The vault↔cloud reconcile.** The old 7-stage `pipelines/reconcile.py` is NOT ported or owned by Phase 7. Its successor is the **Phase 6 startup scanner** (ADR-0013 §A3). Not a Phase 7 worry.
- **Deleting `vault/writer.py`.** Phase 7A stops *importing* `WriteOutcome` from capture, but does NOT delete `vault/writer.py` — it is shared with Phase 6 (watcher) per ADR-0012.
- **Deleting the watcher / indexer.** Those die with Phase 6, not here.

---

## Constraints

Non-negotiable rules the build must respect. Sourced from CLAUDE.md, the design's Guardrail Checklist, and the ADRs.

- **Store-raw-first / summary-nullable / derived "needs-summary" / AI-failure = store-anyway-and-audit-and-succeed** — source: ADR-0014. The four-part data-safety contract is the spine of this phase.
- **`content_hash` is over RAW FILE BYTES, supplied by the daemon; the cloud compares it as-is and NEVER recomputes a hash from extracted text** — source: ADR-0013 §A2. (No cloud code change needed for the hash basis; only wording. A cosmetic binary change can flip the hash and trigger a re-capture — accepted as rare/self-correcting.)
- **C-01 flips here: the database becomes the source of truth for AI content; capture makes ZERO vault writes** — source: ADR-0012, design Guardrail Checklist. No `write_note`, no frontmatter, no sidecar `.md`, no file move. C-01/C-03 in `CONSTRAINTS.md` must be rewritten as part of this phase (flag — do not silently leave stale).
- **No new schema / no new column** — source: locked decision 6, C-05. `full_body` (migration 008) already exists; "needs-summary" is derived from `summary IS NULL`. (Migration 009 is **7B**, not here.)
- **No confidence threshold in code** — source: C-06. Capture is summarize-only; no gate, no float literal in `pipelines/`.
- **Prompts live in YAML, never inline** — source: C-07. The structured-summary prompt is a new `prompts/*.yaml` file loaded via `PROMPTS`.
- **AI via the provider factory** — source: C-08. The summarizer calls `get_provider("capture", CONFIG.main)`; never instantiates a provider directly.
- **Every public function returns `Success`/`Failure`, never raises** — source: C-12. The AI-failure-store-anyway path still returns `Success`.
- **Audit log is non-negotiable** — source: C-13, ADR-0014. A `CAPTURED` entry on success **and** a failure entry on AI failure, both via `core.audit.write`.
  - **Caveat (research-confirmed):** `audit_log.append` returns `Failure` if no correlation id is in contextvars. The new pipeline entry MUST call `new_correlation_id()` (from `core/logging_setup.py`) at its top before any audit write, or every `CAPTURED`/failure write silently fails.
- **Reuse `storage/db.py::get_connection`; add no new connection factory** — source: C-04 (`PRAGMA foreign_keys=ON`).
- **No MCP tool before its pipeline** — source: C-15. The classify trigger is a log line, not an MCP tool; no MCP tool is added in Phase 7A.
- **No module-scope CONFIG import in tests; lazy-import or pass explicit `db_path`** — source: C-17 (hook-enforced).
- **The new capture must not import `vault.` types at module scope** — use `Any` / DTOs for any field that would otherwise be a vault type. **Research caveat:** `test_pipeline_has_no_heavy_imports` greps **`src/core/pipeline.py` ONLY**, NOT `pipelines/capture.py` — so this is an explicit BUILD RULE here, not a test-enforced guarantee. Optionally extend that test to cover the new capture (logged as tech debt; intent is sound, named enforcement does not currently reach this file).
- **ADR-0012 retirement boundary:** Phase 7A MAY break capture's own dependencies (delete old capture, stop importing `WriteOutcome`) but MUST NOT delete shared modules still consumed by Phase 6 or other live callers.

---

## Assumptions

Claims about existing code this spec depends on. `/research` verifies each. Each is falsifiable.

| ID | Assumption | Source implication | What would prove it wrong |
|----|-----------|--------------------|---------------------------|
| A1 | `upsert_from_upload` already performs the three-way fingerprint dance (insert / skip-identical / update-changed) keyed on vault_path, and needs **no change** for the raw-store beat | design Decision 3; impl. "Duplicate uploads stop costing money" | The skip-identical branch is missing or keyed on something other than vault_path + content_hash (`documents.py:218`) |
| A2 | `upsert_from_upload`'s skip-identical branch runs only **after** the function is called; a separate pre-check (`get_by_path`) is required to short-circuit **before** the AI | design risk "dedup short-circuit must move ahead of the LLM" | Calling `upsert_from_upload` itself can skip the AI without a prior peek (it cannot — it does not know about the AI) |
| A3 | `documents.summary` is a plain TEXT column read straight into the keyword index's summary column and folded into the meaning embedding; a Markdown block flows through unchanged and stays searchable | design Decision 1 | The keyword index does not read `summary`, or the embedding does not fold it in (`keyword.py`, `embeddings.py::_build_context_text`) |
| A4 | `upsert_from_upload` accepts an optional `title` and falls back to the filename stem when none is given | design Decision 2 | The function has no `title` parameter or no stem fallback (`documents.py:196`) |
| A5 | `index_keywords(vault_path, title, summary, body)` and `index_embedding(vault_path, title, …, summary)` can be called keyed on **vault_path** (not on a `WriteOutcome`) after the attach | design Decision 3 ("keyed on vault_path instead of a WriteOutcome") | The indexers require a `WriteOutcome` and cannot be driven from vault_path + fields |
| A6 | `get_provider("capture", CONFIG.main)` resolves a working provider; `"capture"` is already a valid task | design C-08 line | `"capture"` is not in the `Task` literal (`config.py:43`) |
| A7 | `core.audit.write` accepts an `AIDecision` with an arbitrary `outcome` string and an empty/short `source_ids`, so a `CAPTURED` and a failure entry can both be written | design C-13 line | `audit.write` rejects the outcome string or requires non-empty source_ids (`audit.py`, `confidence.py`) |
| A8 | `knowledge_entries` query helpers (`query_by_entity`, `get_confident_and_pending`) return an empty list (not an error) when the table is empty | design "guaranteed-empty knowledge base" implication | An empty table makes them return `Failure` (`knowledge_entries.py`) |
| A9 | `documents.upsert(WriteOutcome)` and `documents.replace_path(WriteOutcome)` have **only old capture** as a live caller, so both retire cleanly | design Decision 3 ("verified only caller is old capture") | **❌ INVALIDATED → RESOLVED (research-confirmed, `docs/3_research/phase7/`).** `mcp_server/_move.py:100` (`kms_move`, Phase 4) calls `replace_path(WriteOutcome)`, and `replace_path` itself calls `_derive_title`. **Corrected scope:** retire `documents.upsert(WriteOutcome)` ONLY; **KEEP** `replace_path`, the `WriteOutcome` import in `documents.py`, AND `_derive_title` until `kms_move` is refactored (Phase 9) |
| A10 | The dev `kms capture <file>` command (`cli/main.py`) currently calls `capture_file`/`scan_capture`, both of which retire; re-pointing it at the new in-process pipeline is mechanical | design "scan_capture and capture_folder callers" risk | The CLI calls capture through some other indirection not enumerated by grepping `capture_file`/`scan_capture`/`capture_folder` |

---

## Component dependency order

_Documents what must exist before each component can work — not the order a developer writes code. Execution order is owned by `/plan-from-specs`._

### 1. Structured-summary prompt (YAML)

**Goal.** Give the Housekeeping AI a single instruction that produces a summary with fixed named sections and a clean descriptive title — in plain, searchable Markdown.

**Build.** Add a new prompt YAML file (`prompts/*.yaml`, e.g. `capture_summary.yaml`) loaded via the existing prompt loader (`PROMPTS`). The instruction must direct the AI to emit a **generic content frame** with these exact fixed headers, in this order: `## Overview`, `## Key points`, `## Decisions`, `## Action items`, `## People mentioned`. It is a generic frame, **NOT** dimension-aligned (this was explicitly decided — do not align the headers to project/domain dimensions). The prompt must also ask for a short descriptive title. Do **not** reuse `summarize.yaml` (which forbids Markdown and asks for 2–4 plain-text sentences) — that is the opposite shape.

**Depends on.** None.

**Assumes.** —

**Done when.** A prompt file exists in the prompts folder; when loaded, summarizing sample meeting-note text yields a Markdown block containing all five fixed headers and a descriptive title. (Supports P7-CAP-03.)

---

### 2. Summarizer (Housekeeping AI) with dormant context injection

**Goal.** Turn stored extracted text into a structured Markdown summary + descriptive title, optionally briefed by known facts — and never fail just because no facts exist.

**Build.** A stage (in the new `pipelines/capture.py`) that: (a) **optionally** gathers context by querying the knowledge-facts store (`knowledge_entries.query_by_entity` / `get_confident_and_pending`) and assembling a short brief; when the store is empty it returns an empty brief **gracefully** (mirror the degrade-to-"no block" pattern in `mcp_server/context.py`) and the summarizer proceeds on the document text alone; (b) calls the AI via `get_provider("capture", CONFIG.main).complete(system, user)` using the new prompt (Component 1); (c) parses the AI's response into the summary text + descriptive title. Returns `Success(summary, title)` or `Failure(...)` (C-12). The bare document text should be passed to the summarizer (do not over-wrap it — research showed bare query/text gives cleaner search separation).

**Depends on.** Component 1 (the prompt).

**Assumes.** A6, A8.

**Dependency category.** in-process (test directly). The AI provider is `true-external` behind the existing factory — test by injecting a stub provider that returns a canned summary or a forced failure.

**Done when.** With **zero** knowledge-facts rows, summarizing a document still returns a normal structured summary with no error (the empty-context path ran and found nothing — P7-CAP-06). When the AI is forced to fail, the stage returns a `Failure` (not an exception). When the AI succeeds, the returned summary contains the five fixed headers and a descriptive title distinct from the filename stem.

---

### 3. DB writer — `attach_summary` (the second beat)

**Goal.** Write the AI summary and descriptive title onto the document row that already holds the raw text, without disturbing anything the raw-store beat saved.

**Build.** A new small routine in `storage/documents.py`, e.g. `attach_summary(vault_path, summary, title, db_path)`, that UPDATEs only `summary`, `title`, and `updated_at` on the row identified by `vault_path`, using `storage/db.py::get_connection` (no new connection factory). It must **preserve** `full_body`, `content_hash`, `original_filename`, `file_size_bytes` (respects C-03's spirit — never blank a column the raw-store set). Returns `Result[int]` (the row id) per C-12.

**Depends on.** None (it is a sibling of the existing `upsert_from_upload`).

**Assumes.** A1, A3, A4.

**Interface shape.** Callers see one verb: "attach the summary + title to this path." The SQL UPDATE and column-preservation are hidden behind it. One real adapter (the new pipeline) — not a speculative seam; it exists to honour the two-beat data-safety contract.

**Dependency category.** in-process (test directly with an explicit `db_path`).

**Done when.** After `attach_summary` runs on a row that already has `full_body` and a content fingerprint, the row's `summary` and `title` are updated, `updated_at` advances, and `full_body` + `content_hash` + filename + size are **unchanged**. Calling it on a non-existent path returns a `Result` (not an exception). (Supports P7-CAP-03.)

---

### 4. Capture Receiver + pipeline entry (front-loaded dedup → store → summarize → attach → trigger)

**Goal.** One new entry function that orchestrates the whole text path with the dedup check provably ahead of the AI, honouring the data-safety contract.

**Build.** A new entry function in `pipelines/capture.py` (e.g. `capture_upload(...)`) that takes the upload fields (vault_path, extracted_text, content_hash, original_filename, file_size_bytes) — **not** a `Path`, and **no** `vault.` types at module scope (use `Any`/DTOs to keep `test_pipeline_has_no_heavy_imports` green). It performs, in order:
   1. **Front-loaded dedup (P7-CAP-01).** Peek for an existing row at this vault_path (`get_by_path`). If one exists with the **same** content_hash, short-circuit: return success **without** calling the summarizer. (The check must be ahead of the AI call — assert via a test that the provider is never invoked on a duplicate.)
   2. **Store raw first (P7-CAP-02).** Call `upsert_from_upload(...)` with `title = stem` (no AI yet). Content is now safe; `summary` stays empty; `project` stays NULL (P7-CAP-05).
   3. **Summarize (Component 2).**
   4. **On AI success:** call `attach_summary` (Component 3); then best-effort `index_keywords` + `index_embedding` keyed on **vault_path** (after attach); then write a `CAPTURED` audit entry; then **classify trigger** (Component 5). Return success (P7-CAP-03).
   5. **On AI failure:** leave summary empty, write a **failure** audit entry (C-13), and **return success** so the daemon does not loop (P7-CAP-04). Do **not** index meaning-search (no summary yet). Do not block.

Audit writes must happen **after** the physical action succeeds (the codebase's known "audit after the action, not before" rule). Returns `Success`/`Failure` (C-12); the AI-failure-store-anyway path returns `Success`.

**Depends on.** Components 2 and 3 (and 5 for the trigger).

**Assumes.** A1, A2, A5, A7.

**Interface shape.** Callers (the upload endpoint, the dev CLI) see one verb: "capture this uploaded text." The two-beat write, dedup, AI call, indexing, and audit are hidden behind it.

**Dependency category.** in-process (test directly with an explicit `db_path` and an injected stub provider).

**Decisions.**
- Q: Where does the dedup peek read the existing hash — `get_by_path` (returns the full row) or a narrower helper? Options: reuse `get_by_path` / add a tiny `content_hash_for(vault_path)`. Leaning **reuse `get_by_path`** because it already exists and the row is cheap; avoid a new function unless research shows a hot path. (Not a blocker.)

**Done when.** (1) Re-uploading identical content for the same path returns success and the summarizer is **never** invoked, and the row's summary + `updated_at` are unchanged (P7-CAP-01). (2) A brand-new upload leaves a row with `full_body` populated and `summary` empty at the store-raw point (P7-CAP-02). (3) A successful capture leaves a row with a structured summary + descriptive title, findable by both keyword and meaning search (P7-CAP-03). (4) A forced AI failure leaves the row stored with `summary` empty, writes a failure audit entry, and returns success (P7-CAP-04). (5) `project`/domain stay NULL and vault_path is stored verbatim (P7-CAP-05). (6) Empty knowledge base still completes capture (P7-CAP-06). (7) Identical content under two paths produces two rows (P7-CAP-07).

---

### 5. Classify trigger (no-op stub)

**Goal.** Mark, in the log only, that a captured document is ready for later fact-extraction — without building any queue, flag, or marker.

**Build.** One log line in the new pipeline keyed on the document id (one `logger.info(...)`). No table, no column, no MCP tool (C-15).

**Depends on.** Component 4 (it runs at the end of a successful capture).

**Assumes.** —

**Done when.** A successful capture emits exactly one classify-ready log line and creates **no** queue row, flag column, or marker; the document is searchable before any classify runs (P7-CAP-08).

---

### 6. Retire the old path-based capture + the dead `WriteOutcome` couplings

**Goal.** Delete the old vault-writing capture so the only capture path is the new DB-only one, and remove now-dead couplings — **without** touching modules other live code still needs.

**Build.** Remove from `pipelines/capture.py`: `capture_file(Path)`, `_store_md`, `_store_nonmd`, `_classify_auto_md_move`, `capture_folder`, `scan_capture`, `classify_step`, `apply_location_tags`, all `write_note()` / `move_note()` calls, all `WriteOutcome` usage, frontmatter writes, sibling `.md` creation, and `build_registry`/`ProjectRegistry` use. Re-point the dev `kms capture <file>` (`cli/main.py`) at the new in-process entry (Component 4): extract text locally, then call `capture_upload(...)`. **Keep** `upsert_from_upload`, `get_by_path`, `all_paths`, `delete_by_path`, `rename`, `filter_paths`, `update_batch_id` (other live consumers: api.py, retrieval, indexer). **Do NOT delete** `vault/writer.py` (shared with Phase 6 per ADR-0012).

Additionally — **research-confirmed scope (A9 resolved):** retire `documents.upsert(WriteOutcome)` **ONLY**. **KEEP** `documents.replace_path(WriteOutcome)`, the `WriteOutcome` import in `documents.py`, and `_derive_title(outcome)` — all three are still consumed by the live `kms_move` tool (`mcp_server/_move.py:100` → `replace_path` → `_derive_title`). Their retirement rides with Phase 9 (MCP adaptation), NOT this phase.

**Depends on.** Component 4 (the replacement must exist before the old path is removed).

**Assumes.** A9, A10.

**Decisions.**
- **RESOLVED (research-confirmed).** Option (a) is correct and verified: retire `documents.upsert(WriteOutcome)` only; **keep** `replace_path`, the `WriteOutcome` import, and `_derive_title` — the latter two are reached by the live `kms_move` tool via `mcp_server/_move.py:100`. (`_derive_title` is called by the retained `replace_path`, so it must NOT be deleted — this corrects the spec's original "retire `_derive_title`" lean.) `_move.py`/`kms_move` refactor is Phase 9 scope, not this phase.

**Done when.** The old vault-writing functions no longer exist in `pipelines/capture.py`; `kms capture <file>` runs the new pipeline in-process and writes **no** vault file, frontmatter, or sidecar `.md` (P7-CAP-09); the full test suite passes; `test_pipeline_has_no_heavy_imports` still passes (no module-scope `vault.` import in the new capture); and `kms_move` / any other live caller of the retained functions still works.

---

## Handoff notes

- **Contract with Phase 8 (Classify):** Phase 7A delivers document rows where `project`/domain are NULL, the folder path is stored verbatim in `vault_path`, and "no facts yet" is true by definition for every captured document. Phase 8 finds its own work by asking "which documents have produced no facts?" — Phase 7A builds **no** queue or flag for it (C-15).
- **Contract with the retry runner (deferred):** "Needs summary" = `summary IS NULL`. The audit log records *why* a summary is missing. The retry runner (not built here) is owed the job of re-summarizing those rows and building their meaning-search embedding (OQ-7B).
- **Contract with `CONSTRAINTS.md`:** C-01 and C-03 must be rewritten in this phase to reflect DB-as-source-of-truth (the design flags this; it is not done in the spec). Flag for `/guardrail-check` during planning.
- **RESOLVED — `replace_path`/`WriteOutcome` retirement (A9):** Research enumerated all callers. Retire `documents.upsert(WriteOutcome)` only; keep `replace_path` + `WriteOutcome` + `_derive_title` (live via `kms_move`/`_move.py:100`) until Phase 9. See OQ-7A-1 (now resolved).
- **Suggested research topics:**
  1. Confirm `upsert_from_upload`'s skip-identical branch keys on vault_path + content_hash exactly, and that a pre-peek via `get_by_path` is the right short-circuit (A1/A2).
  2. Enumerate all callers of `capture_file`, `capture_folder`, `scan_capture`, `classify_step`, `apply_location_tags` (tests + CLI) so the retirement is complete (A10).
  3. Confirm `index_keywords`/`index_embedding` can be driven from vault_path + fields after the attach, mirroring today's wiring but without a `WriteOutcome` (A5).
  4. Confirm the audit `source_ids` identifier choice for the `CAPTURED`/failure entries (OQ-7C — design recommends vault_path).
  5. Confirm `knowledge_entries` helpers return empty-list (not Failure) on an empty table (A8).

---

## Open questions

_Deferred per instruction — do not block on these; carry into research/planning._

- **OQ-7A-1 — RESOLVED (research-confirmed).** `mcp_server/_move.py:100` (`kms_move`, Phase 4) is a live caller of `replace_path(WriteOutcome)`, and `replace_path` calls `_derive_title`. **Final scope:** retire `documents.upsert(WriteOutcome)` ONLY; **keep** `replace_path`, the `WriteOutcome` import, and `_derive_title` until `kms_move` is refactored (Phase 9). No longer an open question.
- **OQ-7A (from design) — Summary storage shape if section-level access is later needed.** Re-parse the Markdown headers vs migrate to a JSON column. Recommendation: re-parse when/if the need is real; not a blocker.
- **OQ-7B (from design) — When does the meaning-search embedding get built on an AI-failed capture?** Provisional body-only embedding now vs wait for the summary retry. Recommendation: wait for retry; keyword search covers the interim. Not a blocker.
- **OQ-7C (from design) — Identifier used in audit `source_ids`: vault_path or document id?** Recommendation: keep `source_ids` = vault_path for Phase 7A to match existing audit rows; defer the firm choice to Phase 8 (which owns `sources`). Not a blocker.

---

## Next step

Spec written. Run `/research` to verify spec assumptions against real code before planning. Pay special attention to **A9 / OQ-7A-1** (the `replace_path`/`WriteOutcome` retirement contradiction) before any deletion.
