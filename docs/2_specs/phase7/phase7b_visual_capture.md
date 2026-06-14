# Phase 7B — Visual / Binary Capture

_Spec. Output of `/writing-detailed-specs`. Feeds `/research` then `/plan-from-specs`._
_Design input: `docs/1_design/phase7/phase7b_visual_capture.md` (7 decisions resolved; Guardrail Checklist; Q1 diagram)._
_Requirements: `docs/0_draft/phase7/phase7b_visual_capture_requirements.md`._
_Converge point: `docs/2_specs/phase7/phase7a_text_capture.md` + the live `capture_upload` (`src/pipelines/capture.py`). 7B extends 7A's store-raw-first contract; the two paths fork cleanly and share only the converge point._
_7A artifacts: spec at `docs/2_specs/phase7/phase7a_text_capture.md`; research at `docs/3_research/phase7/phase7a_text_capture.md`; plan at `docs/4_plans/phase7/phase7a_text_capture.md`._
_Behavior IDs: **P7-CAP-10…13** in `docs/system_behavior/behavior_inventory.yaml`._
_ADRs: ADR-0014 (store-raw-first / AI-failure = store-anyway), ADR-0015 (visual/binary: cloud stores blob + vision-describes), ADR-0013 (raw-byte hash), ADR-0012 (additive rearchitecture)._
_LOCKED DECISIONS (human, 2026-06-14): storage = `boto3` + separate `KMS_BLOB_*` bucket in VNG Object Storage (OQ-7H resolved); vision route = AgentBase MaaS OpenAI-compatible `image_url` shape, NOT Anthropic block (OQ-7G route decided; capability verification still build-blocking)._
_Reader mode: non-coder-readable (default). Plain English leads every section; code references are parenthetical anchors._

---

## Purpose

Today the cloud knows how to capture **text** — save it, summarize it, index it. But some files have no useful text: a photo, a scanned page, a chart dropped into a folder. For those, the laptop helper sends the **actual file bytes** instead. Phase 7B teaches the cloud to handle those bytes: **keep the file in cloud storage, remember where it lives, and ask a picture-reading AI to describe it in words** so the file becomes searchable like any text note. The safety promise mirrors 7A exactly: **save the file first (it is safe the moment it lands), describe it second (the description may arrive late, or not at all if the file is too big, the wrong type, or the AI is down).** When the user later deletes a file, the cloud throws the file bytes away **only if no other captured file points at the same bytes**. The text path (7A) is untouched.

---

## Already built (reuse, do not rebuild)

| Function / Module | Location | What it does (plain English) | How this spec uses it | Depth |
|---|---|---|---|---|
| Upload Endpoint | `mcp_server/api.py::upload_handler` | Cloud front door; validates uploads; the multipart binary path currently calls `upsert_from_upload` but **discards the file bytes** (`api.py:141-142`) | 7B re-points the multipart branch to pass bytes to the binary capture entry instead of discarding them | deep |
| Event Endpoint (delete) | `mcp_server/api.py` (POST `/api/event` type=deleted) | Receives a delete report from the daemon; calls `delete_by_path` to hard-delete the document row + search entries in one transaction | 7B extends this path with a reference-counted blob delete after the row is gone | deep |
| Raw-store routine | `storage/documents.py::upsert_from_upload` | Save-or-update by vault_path: new path = INSERT; same `content_hash` = **skip (return existing id)**; changed hash = UPDATE. Already accepts `extracted_text=None` for binaries (`documents.py:100`) | Used unchanged for the store-raw beat; 7B extends it (or adds a sibling) to also write `blob_ref` + `mime_type` | deep |
| Summary attach routine | `storage/documents.py::attach_summary` | Updates only `summary`, `title`, and `updated_at` on an existing row, preserving everything else | Used unchanged — the vision description attaches the same way as a text summary | deep |
| Delete by path | `storage/documents.py::delete_by_path` | Hard-deletes the document row + search entries in one transaction, returns `Success(rowcount)` | Extended (or wrapped) to also check reference count and conditionally delete the blob | deep |
| Lookup by path | `storage/documents.py::get_by_path` | Fetch the existing document row for a vault_path (returns the row, or "none found") | Used for the front-loaded dedup peek (same as 7A) | deep |
| Document row shape | `storage/documents.py::DocumentRow` | The in-memory shape of one document row | Gains two new fields (`blob_ref`, `mime_type`) from migration 009 | deep |
| AI provider factory | `llm/provider.py::get_provider` | Returns the right AI provider for a named task, config-driven | 7B adds `"vision"` to the task list and calls `get_provider("vision", CONFIG.main)` | deep |
| AI provider base | `llm/provider.py::LLMProvider` | Abstract base with `complete(system, user)` — text-only today | 7B adds `describe_image(...)` method with a default that returns Failure | deep |
| OpenAI-compatible provider | `llm/openai_provider.py::OpenAIProvider` | Uses `openai.AsyncOpenAI` with `messages=[{role:system,...},{role:user,...}]` | 7B overrides `describe_image` to send an `image_url` content block (MaaS shape) | deep |
| Prompt loader | `llm/prompt_loader.py` (`PROMPTS`) | Loads every prompt from YAML files in `prompts/` | Loads the new vision-describe prompt | shallow |
| Keyword indexer | `retrieval/keyword.py::index_keywords` | Adds a document to the full-text search index | Called best-effort after the description attaches | deep |
| Meaning indexer | `retrieval/embeddings.py::index_embedding` | Adds a document to the semantic search index | Called best-effort after the description attaches | deep |
| Audit writer | `core/audit.py::write` | Records one AI decision/outcome in the tamper-evident audit log | Writes `DESCRIBED` on success, and skip/failure entries for too-big / unsupported-type / vision-failed | deep |
| AI decision envelope | `core/confidence.py::AIDecision` | The "show your work" record every audit entry needs | Built for describe-success, skip, and failure entries | deep |
| DB connection | `storage/db.py::get_connection` | The one connection factory (enforces `PRAGMA foreign_keys=ON`) | All new DB writes use it; no new connection factory | deep |
| Task literal | `core/config.py::Task` | The fixed list of LLM task names (`"capture"`, etc.) | Gains `"vision"` | shallow |
| Providers config | `core/config.py::ProvidersConfig` | Maps each task to a provider | Gains a `vision` field | shallow |
| Capture pipeline entry | `pipelines/capture.py::capture_upload` | The converge point — orchestrates text capture (7A). Currently handles text-only | 7B extends with a binary branch that forks when `extracted_text is None` and raw bytes are present | deep |

---

## Q1 Diagram — what happens inside (from the design doc)

_The binary branch of the two-input fork. The text branch (7A) is unchanged and omitted._

```
# Phase 7B Visual/Binary Capture — What Happens Inside (binary branch zoom-in)
Scope: Shows what happens when the daemon uploads ONE file as RAW BYTES with
       no extracted text. This is the binary/visual branch of the two-input
       fork — the text branch (store text → summarize → attach) is unchanged.
       Does NOT cover daemon-side extraction, blob-serving retrieval, or
       fact-extraction.

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

   Daemon uploads ONE binary item:
   raw file bytes + folder path + filename + size + file type
   + content fingerprint (over RAW FILE BYTES)
                    │
                    ▼
      ┌────────────────────────────┐
      │ 1. Capture Receiver        │
      │ Validate; compare raw-byte │
      │ fingerprint to stored rows │
      └────────────┬───────────────┘
                   │
          "Seen this exact content,
           or is the file too big?"
            ┌──────┴───────────────┐
       YES / TOO BIG               NO, within limit
            │                       │
            ▼                       ▼
   ┌────────────────────┐  ┌────────────────────────────┐
   │ Stop or store-only │  │ 2. Store Blob First        │
   │ Duplicate → no      │  │ Save raw bytes to object   │
   │ store, no AI.       │  │ storage by content key     │
   │ Too big → store     │  │ (identical bytes stored    │
   │ blob, skip AI,      │  │ once); save a row that     │
   │ audit why, succeed  │  │ REFERENCES the blob; EMPTY │
   └────────────────────┘  │ summary (file safe now)    │
                           └─────────────┬──────────────┘
                                         │
                              "Can this file type be
                               described? (image / text-
                               less PDF = yes)"
                            ┌────────────┴───────────────┐
                          NO (zip/video/etc.)         YES
                            │                           │
                            ▼                           ▼
                  ┌────────────────────┐  ┌────────────────────────────┐
                  │ Store-only         │  │ 3. Vision Describer        │
                  │ Leave description  │  │ One picture-reading AI call│
                  │ empty; audit       │  │ → searchable description   │
                  │ "unsupported";     │  │ + descriptive title        │
                  │ return success     │  └─────────────┬──────────────┘
                  └────────────────────┘                │
                                                "Did the vision AI succeed?"
                                              ┌─────────┴──────────┐
                                             YES                   NO
                                              │                     │
                                              ▼                     ▼
                            ┌────────────────────────────┐  ┌────────────────────────────┐
                            │ 4. Attach Description      │  │ Record failure in Audit    │
                            │ Update SAME row: put       │  │ Log; leave description     │
                            │ description into summary + │  │ empty; STILL return        │
                            │ full text + title; index   │  │ success. Blob safe in      │
                            │ for keyword + meaning       │  │ storage but not searchable │
                            │ search; audit success      │  │ until a later retry        │
                            └────────────┬───────────────┘  └────────────────────────────┘
                                         │
                                         ▼
                            ┌────────────────────────────┐
                            │ 5. Classify Trigger        │
                            │ Log one line: ready for    │
                            │ later fact-extraction      │
                            │ (no-op stub today)         │
                            └────────────────────────────┘
```

```
Simplified: The duplicate-skip and the size-cap skip share one terminal box
            (both stop before the Vision Describer — one stores nothing, the
            other stores the blob + audits why). The "unsupported type" skip
            and the "vision failed" branch both end the same way as the text
            path's AI-failure branch: blob/file safe, description empty, audit
            written, success returned — convergence with 7A's contract.
```

## Q2 Diagram — How it connects to others

```
# Visual/Binary Capture — How It Connects
Scope: Shows what the Binary Capture branch touches. Does NOT show
       internal steps (see Q1 for that). Does NOT show the text branch
       (7A) internals — only shows it as an unchanged sibling.

How to read this:
  Center box      = the feature being built (Phase 7B)
  Solid boxes     = components that already exist
  ══ double-line  = NEW components this phase creates
  Dashed boxes    = planned, not built yet
  Arrow labels    = what passes between them

                     ┌──────────────┐
                     │ Daemon       │
                     │ (laptop      │
                     │  helper)     │
                     └──────┬───────┘
                 sends raw  │
                 bytes +    │
                 metadata   │
                 via        │
                 multipart  │
                            ▼
                     ┌──────────────┐
                     │ Upload       │
                     │ Endpoint     │    hands text to
                     │ Cloud front  ├──────────────────────┐
                     │ door         │                      │
                     └──────┬───────┘                      ▼
              hands bytes   │                     ┌──────────────┐
              to binary     │                     │ Text Capture │
              branch        │                     │ Pipeline     │
                            │                     │ (7A, as-is)  │
                            ▼                     └──────────────┘
  ╔══════════════════════════════════════════════════════════════╗
  ║                BINARY CAPTURE BRANCH                        ║
  ║   Stores blob, describes if possible, records everything    ║
  ╚══╤══════════╤══════════╤══════════╤═══════════╤═════════════╝
     │          │          │          │           │
     │ puts     │ saves    │ sends    │ writes    │ indexes
     │ blob     │ row w/   │ image    │ success   │ after
     │          │ blob ref │ for      │ + failure │ description
     │          │ + type   │ describe │ + skip    │ attaches
     │          │          │          │ entries   │
     ▼          ▼          ▼          ▼           ▼
  ╔════════╗ ┌────────┐ ╔════════╗ ┌────────┐ ┌──────────────┐
  ║ Blob   ║ │Document║ ║ Vision ║ │ Audit  │ │ Search       │
  ║ Store  ║ │ Store  ║ ║Describ.║ │ Log    │ │ Indexer      │
  ║ (NEW)  ║ │        ║ ║ (NEW)  ║ │        │ │ keyword +    │
  ╚════╤═══╝ └────┬───╝ ╚════════╝ └────────┘ │ meaning      │
       │          │                            └──────────────┘
       │ writes   │ on delete:
       │ content- │ count remaining
       │ addressed│ references;
       │ key      │ if last → delete blob
       ▼          │
  ╔════════════╗  │     ┌ ─ ─ ─ ─ ─ ─ ─ ┐
  ║ Object     ║  │     │ Classify        │
  ║ Storage    ║◄─┘     │ (Phase 8)       │
  ║ blob       ║        └ ─ ─ ─ ─ ─ ─ ─ ┘
  ║ bucket     ║                │
  ║ (NEW)      ║          future: fact-
  ╚════════════╝          extraction
         │
         │ separate from
         ▼
  ┌──────────────┐    ┌ ─ ─ ─ ─ ─ ─ ─ ┐
  │ DB Backup    │    │ Blob Serving    │
  │ Helper       │    │ (Phase 9)       │
  │ (Litestream, │    └ ─ ─ ─ ─ ─ ─ ─ ┘
  │  as-is)      │
  └──────────────┘

  Configuration that drives the binary branch:

  ╔══════════════╗     ╔════════════════╗     ╔═══════════════╗
  ║ Vision       ║     ║ Vision         ║     ║ Migration 009 ║
  ║ Config       ║     ║ Prompt         ║     ║ blob_ref +    ║
  ║ describable  ║     ║ describe_image ║     ║ mime_type     ║
  ║ types + cap  ║     ║ .yaml          ║     ║ columns       ║
  ╚══════════════╝     ╚════════════════╝     ╚═══════════════╝
```

```
Simplified: "Document Store (raw save)" and "Document Store (description attach)"
            are shown as one "Document Store" box — they are two writes to the
            SAME document row (save with blob ref first, attach description second).
            The delete flow (Event Endpoint → Document Store → Blob Store) is shown
            as annotation on the Document Store spoke, not a separate spoke.
            The 6 spokes off the center box are: Blob Store, Document Store, Vision
            Describer, Audit Log, Search Indexer, and the delete-flow annotation.
```

---

## Feature overview

**Happy path (binary upload, describable type, within size cap, AI healthy):**

1. **The front door receives a binary upload.** The daemon sends raw file bytes via multipart, plus a folder path, filename, size, file type (MIME), and a content fingerprint computed over the raw bytes. The Upload Endpoint today discards those bytes; 7B stops discarding and passes them to the binary capture branch.
2. **The receiver checks "have we seen this exact content before?"** It compares the supplied fingerprint to what is already stored for that folder path. If a row with the **same** fingerprint exists, the upload is a duplicate — stop early, report success, and **never store the blob or call the vision AI** (P7-CAP-10).
3. **Store the blob first.** The raw bytes are saved to cloud object storage under a content-addressed key (the fingerprint itself). Because the key is the fingerprint, identical bytes are stored only once and a file move never re-uploads. A document row is saved with the blob reference (the object key) and the file type, plus an **empty description**. The file is safe now (P7-CAP-10).
4. **Check "can this file type be described?"** A config list of describable types (e.g. `image/*`, possibly `application/pdf`) and a config size cap decide. If the type is not in the list or the file exceeds the cap, skip to step 6.
5. **Ask the Vision Describer for a description.** One picture-reading AI call (via AgentBase MaaS, OpenAI-compatible `image_url` shape) produces a searchable description + descriptive title. The description is attached to the same row, indexed for keyword + meaning search, and a `DESCRIBED` audit entry is written (P7-CAP-11).
6. **Store-only path (skip or failure).** If the type is unsupported or the file is too big, the blob is stored but no AI is called; an audit entry records the skip reason and the pipeline returns success (P7-CAP-12). If the Vision AI fails, the blob is stored but the description stays empty; a failure audit entry is written and the pipeline **still returns success** (P7-CAP-11) — the description can fill in on a later retry.
7. **Classify trigger.** Same no-op stub log line as 7A (P7-CAP-10).

**Delete path:** When the daemon reports a file deletion, the existing Event Endpoint calls `delete_by_path`, which hard-deletes the document row. 7B extends this: the event handler pre-reads the row via `get_by_path` to capture `blob_ref` before the delete. After the row is gone, if a blob was referenced, check if any other row still references the same blob key. If none do, delete the blob from object storage (best-effort — a failed blob delete is logged but never fails the user's delete). If other rows still reference it, leave the blob alone (P7-CAP-13).

**Edge cases:**

- **AI fails:** The blob is safe from step 3. The pipeline writes a failure audit entry, leaves the description empty, and returns success. The file is not searchable until a retry succeeds.
- **Unsupported type (zip, video, etc.):** Blob is stored, description stays empty, audit says "unsupported type," success returned. The file is stored but not searchable by content.
- **File too big for vision:** Blob is stored, description stays empty, audit says "too big," success returned.
- **Same bytes in two folders:** Two independent document rows are created, each pointing at the same blob (content-addressed). Each gets its own description. Deleting one row does not remove the blob because the other row still references it (P7-CAP-13).
- **Identical re-upload:** The front-loaded dedup short-circuits before the blob store AND before the vision call — no re-upload, no re-describe, no cost (P7-CAP-10).

---

## Out of scope

- **The text capture path (7A).** Completely unchanged. No modifications to 7A's flow, prompt, or contract. **Handled by Phase 7A** (already spec'd + researched + planned).
- **Blob-serving retrieval (letting a user view or download the stored blob).** The blob is stored but no read endpoint exists to serve it back. **Handled by Phase 9.**
- **Fact-extraction / classify.** The classify trigger is a log line. No queue, no flag, no MCP tool. **Handled by Phase 8.**
- **The description retry runner.** 7B ships the contract (empty description = "needs description," same `summary IS NULL` signal as 7A) and the failure audit trail, but the job that later re-describes is **deferred — no phase assigned yet.**
- **Multi-blob per document.** Two columns model a strict one-file = one-blob = one-row relationship. A future multi-page-scan feature would need a `blobs` table migration. **Deferred — OQ-7D.**
- **Daemon-side changes.** 7B assumes the daemon already sends raw bytes + MIME type via multipart. How the daemon decides what to extract vs send raw is **Phase 6 / daemon scope.**
- **PDF text extraction.** If a PDF has extractable text, the daemon extracts it and sends it as text (7A path). 7B only handles text-less / image-only PDFs where the daemon sends raw bytes.
- **Orphan-blob sweep.** A reconcile job to find blobs with no remaining document references. Harmless (an orphaned blob wastes storage, never corrupts data). **Deferred.**
- **Per-vault opt-out of blob storage.** The privacy/data-residency shift (file bytes leaving the laptop) is accepted for single-tenant personal deployment. **Deferred.**

---

## Constraints

Non-negotiable rules the build must respect.

- **Store-blob-first / description-nullable / derived "needs-description" / AI-failure = store-anyway-and-audit-and-succeed** — source: ADR-0014, extended to blobs by ADR-0015.
- **`content_hash` is over RAW FILE BYTES, supplied by the daemon; the cloud compares it as-is** — source: ADR-0013.
- **C-01 — DB is source of truth; vault is read-only** — the blob store is a new external write surface (object storage), NOT the vault, NOT the DB. The DB holds a *reference* to the blob. No vault write.
- **C-04 — `PRAGMA foreign_keys=ON` on every connection** — all new DB writes use `get_connection`. No new connection factory.
- **C-05 — Schema changes via versioned `.sql` only** — migration 009 adds `blob_ref TEXT` + `mime_type TEXT` nullable columns on `documents`. Bump the version-pin test (`test_migration_007.py:41,56`) from current to new version.
- **C-06 — Thresholds in config, not code** — the vision size cap and the describable-type set are config values, not float/string literals in `pipelines/`.
- **C-07 — Prompts as YAML** — the vision instruction lives in `prompts/describe_image.yaml`, loaded via `PROMPTS`.
- **C-08 — AI via the provider factory** — the vision call routes through `get_provider("vision", CONFIG.main)`; never instantiate a provider directly.
- **C-09 — Providers carry model fields** — adding `vision_model` is a same-shape addition; note C-09 field-list growth (OQ-7E recommendation: dedicated field).
- **C-12 — Result types at module boundaries** — the binary branch, blob-store put/delete, reference-count check, and describe call all return `Success`/`Failure`.
- **C-13 — Audit log non-negotiable** — `DESCRIBED` on success; skip/failure entries (too-big / unsupported / vision-failed) all via `core.audit.write` with `source_ids = vault_path`. `new_correlation_id()` must fire first.
- **C-15 — No MCP tool before its pipeline** — 7B adds no MCP tool. Blob-serving retrieval is Phase 9.
- **C-17 — No module-scope CONFIG in tests** — new 7B tests lazy-import CONFIG or pass explicit `db_path`. The blob store is tested via a local-filesystem stub.
- **C-18 — Daemon cache is advisory; cloud is authority** — the cache-on-ack corollary: daemon writes the hash to its cache only AFTER the cloud returns 200/ok from `/api/upload`.
- **The multipart handler must stop discarding file bytes** — `api.py:141-142` currently discards them. 7B must pass them through.

---

## Assumptions

Claims about existing code this spec depends on. `/research` verifies each. Each is falsifiable.

| ID | Assumption | Source implication | What would prove it wrong |
|----|-----------|-------------------|---------------------------|
| A1 | `upsert_from_upload` already accepts `extracted_text=None` for binary uploads and its skip-identical branch still fires when `content_hash` matches | design Decision 1; `documents.py:100` | The function rejects or mishandles `extracted_text=None`; or the skip branch does not fire when extracted_text is None |
| A2 | `upsert_from_upload` can be extended (or a sibling added) to write `blob_ref` and `mime_type` into the new columns without breaking existing text-path callers | design Decision 1 implication | Adding the two column writes changes return shape or breaks callers that pass `extracted_text` (not None) |
| A3 | `attach_summary(vault_path, summary, title, db_path)` works identically for a vision description as for a text summary — it writes `summary` + `title` + `updated_at` without caring about `blob_ref` or `mime_type` | design Decision 3 | `attach_summary` reads or clobbers `blob_ref` / `mime_type` when it updates |
| A4 | ~~`delete_by_path` returns the deleted row's data~~ **RESOLVED (research):** `delete_by_path` does NOT return `blob_ref`; use `get_by_path` to pre-read `blob_ref` before calling `delete_by_path`. The event handler (not `delete_by_path` itself) owns the pre-read | design Decision 5 | n/a — resolved |
| A5 | `OpenAIProvider` (in `llm/openai_provider.py`) uses `openai.AsyncOpenAI` and sends `messages` as a list of dicts; the OpenAI SDK's `image_url` content-block shape can be sent by replacing the `content` string with a list of content blocks | design Decision 3 (MaaS route) | `OpenAIProvider.complete()` does not use the `openai` SDK, or the SDK version cannot send `image_url` blocks in the messages content |
| A6 | The `/api/upload` multipart handler (`api.py:141-142`) receives the raw file bytes in memory (as `await request.form()` or similar); the bytes are available to pass downstream without a second read | design risk about discarded bytes | The handler streams bytes to disk and they are not available in-memory; or the handler never receives raw bytes at all |
| A7 | `index_keywords` and `index_embedding` can be called keyed on `vault_path` after the description attaches, exactly as 7A does after a text summary attaches | 7A spec A5 (verified in 7A research) | The indexers require a type or argument that a binary row cannot provide |
| A8 | The `content_hash` value (hex digest) is a valid and unique object-storage key; S3-compatible stores accept it as a key name without escaping | design Decision 2 | The hex digest contains characters invalid for S3 keys, or is too short/long for VNG Object Storage |
| A9 | AgentBase MaaS (OpenAI-compatible endpoint) accepts image input via the `image_url` content-block shape (`{"type":"image_url","image_url":{"url":"data:image/png;base64,...}}`). **PARTIALLY VERIFIED (research):** Vision models exist (Qwen3-VL, Skylark-vision) and API is "OpenAI-compatible," but `image_url` acceptance not directly confirmed in docs. **50 req/day rate limit** on vision models. Proceed with standard OpenAI `image_url` format for images; drop `application/pdf` from default describable set until verified at integration time | OQ-7G (locked route decision) | MaaS rejects image input entirely — HARD STOP, escalate to human |
| A10 | The `"vision"` task can be added to the `Task` literal and `ProvidersConfig` the same way `"capture"` exists today, without breaking existing task routing | design Decision 3 | Adding a new literal value to `Task` breaks runtime validation or existing provider resolution |

---

## Component dependency order

_Documents what must exist before each component can work — not the order a developer writes code. Execution order is owned by `/plan-from-specs`._

### 1. Migration 009 — blob reference columns

**Goal.** Give the Document Store two new nullable columns so a binary row can say "my bytes live at this object key, and my type is this."

**Build.** Add `storage/migrations/009_add_blob_ref.sql` (or similar). The migration adds two nullable columns to `documents`: `blob_ref TEXT` (the content-addressed object key in cloud storage) and `mime_type TEXT` (the file's MIME type, e.g. `image/png`). Both are NULL for all existing rows and for all text-only rows going forward. Update `DocumentRow` to include the two fields, and update `_row_from_sqlite` to read them (using the same `if "col" in row.keys()` guard pattern the late-added columns already use). Bump the schema version-pin test.

**Depends on.** None.

**Assumes.** —

**Done when.** After running `init_db`, the `documents` table has `blob_ref` and `mime_type` columns. Existing rows and new text-only rows have both as NULL. `DocumentRow` carries the fields and `_row_from_sqlite` populates them. The version-pin test passes at the new version number.

---

### 2. Blob Store module

**Goal.** Give the cloud its first ability to write, read, check, and delete an arbitrary file in object storage — hidden behind a small helper so no other module speaks S3 directly.

**Build.** Create `storage/blobs.py` with a `BlobStore` class (or a set of module-level functions) exposing four operations:
- `put(key, data, mime_type)` — write bytes to object storage under the given key. Idempotent (re-putting identical bytes to the same key is harmless). Returns `Success(key)` or `Failure(...)`.
- `get(key)` — read bytes back. Returns `Success(bytes)` or `Failure(...)`. (Used by Phase 9 blob-serving; built now for completeness and testing.)
- `delete(key)` — delete the object. Returns `Success` or `Failure(...)`. A failure is logged but never fatal (best-effort).
- `exists(key)` — check if the object exists. Returns `Success(bool)` or `Failure(...)`.

Under the hood, use `boto3` (sync) with `asyncio.to_thread()` wrapping for use from async code (matching the project's existing sync-in-async pattern). Configuration: `KMS_BLOB_ENDPOINT`, `KMS_BLOB_BUCKET`, `KMS_BLOB_ACCESS_KEY_ID`, `KMS_BLOB_SECRET_ACCESS_KEY` (env vars, separate from the Litestream `LITESTREAM_*` set). The object key is namespaced (e.g. `blobs/<content_hash>`).

For tests, provide a local-filesystem stub (`LocalBlobStore` or similar) that writes to a temp directory instead of S3.

**Depends on.** None (independent of migration 009).

**Assumes.** A8.

**Interface shape.** Callers see four verbs: put, get, delete, exists. The S3 client, credentials, endpoint, and bucket name are hidden. One real adapter (the S3 client) + one test adapter (the local-filesystem stub) — a real seam with 2 adapters, not speculative.

**Dependency category.** remote-owned (define port, test with in-memory/local adapter).

**Decisions.**
- Q: Should the `BlobStore` be a class instantiated with config, or module-level functions reading from env? Options: class (injectable, testable) / functions (simpler). Leaning **class** because it makes test-stub injection clean and matches the provider pattern.

**Done when.** `put` writes bytes to the configured store and `get` reads them back. `delete` removes them. `exists` returns True/False correctly. The local-filesystem stub passes the same test suite. All operations return `Result`, never raise.

---

### 3. Vision config — describable types + size cap + vision_model field

**Goal.** Make the "which files get described" decision and the "how big is too big for vision" threshold config-driven, so adding a new format or tuning the cap is a config edit, not a code change.

**Build.** Extend `core/config.py`:
- Add a `VisionConfig` (or extend `CaptureConfig`) with:
  - `describable_mime_prefixes: list[str]` — default `["image/"]` (images only). `application/pdf` is excluded from the default set per research verification: MaaS PDF support is unconfirmed. The list is config-editable — add `"application/pdf"` once MaaS PDF capability is verified at integration time. A file whose MIME type starts with any prefix in this list is describable.
  - `max_vision_bytes: int` — the size cap for vision. Files larger than this skip the Vision Describer. Separate from `handlers.max_file_size_bytes` (50 MB extraction cap).
- Add `"vision"` to the `Task` literal.
- Add a `vision` field to `ProvidersConfig` (maps the `"vision"` task to a provider).
- Add a `vision_model` field to `OpenAICompatConfig` (and other provider configs per C-09 — default-raise providers can leave it empty or set a placeholder).

Update `config/config.yaml` with default values under a `capture.vision` section.

**Depends on.** None.

**Assumes.** A10.

**Done when.** `CONFIG.main.capture.vision.describable_mime_prefixes` returns the configured list. `CONFIG.main.capture.vision.max_vision_bytes` returns the configured cap. `get_provider("vision", CONFIG.main)` resolves a provider. Each provider config has a `vision_model` field (can be empty string for providers that do not support vision).

---

### 4. Vision provider extension — `describe_image` method

**Goal.** Give the AI provider the ability to describe an image, routed through the standard factory, without changing the existing text path.

**Build.** Extend `llm/provider.py`:
- Add `async def describe_image(self, system: str, image_bytes: bytes, mime_type: str) -> Result` to `LLMProvider` with a default body: `return Failure("vision not supported by this provider", recoverable=False)`.
- In `llm/openai_provider.py`, override `describe_image` on `OpenAIProvider`:
  - Base64-encode the image bytes.
  - Build the OpenAI-compatible `image_url` content block: `{"type": "image_url", "image_url": {"url": "data:<mime_type>;base64,<encoded>"}}`.
  - Send `messages=[{"role":"system","content": system}, {"role":"user","content": [<image_url_block>, {"type":"text","text": user_text}]}]`.
  - Parse the response and return `Success(LLMResponse)` or `Failure(...)`.

The system prompt comes from the new vision prompt YAML (Component 5). The user text is a brief instruction to describe the image (also from the prompt YAML).

**Depends on.** Component 3 (the `"vision"` task must exist so `get_provider("vision", ...)` resolves).

**Assumes.** A5, A9.

**Interface shape.** Callers see one verb: `provider.describe_image(system, image_bytes, mime_type)`. The base64 encoding, content-block construction, and SDK call are hidden. Default-raise base + OpenAI override = 2 adapters, a real seam.

**Dependency category.** true-external (inject port, test with mock/stub provider). The actual MaaS call is tested by research verification (A9), not by unit tests.

**Done when.** `get_provider("vision", CONFIG.main).describe_image(system, bytes, "image/png")` returns a `Result` with a text description. A provider that does not override it returns `Failure("vision not supported")`. No change to `complete()` — existing text calls are byte-for-byte unchanged.

---

### 5. Vision prompt (YAML)

**Goal.** Give the Vision Describer a single instruction that produces a searchable text description + descriptive title from an image.

**Build.** Add `prompts/describe_image.yaml` loaded via `PROMPTS`. The instruction must direct the AI to:
- Describe the visual content of the image in searchable plain English.
- Extract any visible text (OCR-like behavior).
- If the image is a chart/graph/diagram, describe what it shows (axes, trends, key data points).
- Produce a short descriptive title.
- Output format: a Markdown block with `## Description` and any visible text, plus a title line.

**Depends on.** None.

**Assumes.** —

**Done when.** A prompt file exists in `prompts/`. When loaded and rendered, it contains clear instructions for describing an image. The output format is parseable for a description body + title.

---

### 6. Binary capture branch (extends `capture_upload`)

**Goal.** Extend the pipeline entry with a binary branch that handles uploads with raw bytes and no extracted text — storing the blob first, describing if possible, and recording everything.

**Build.** Extend `pipelines/capture.py::capture_upload` (the converge point shared with 7A). When `extracted_text is None` and raw bytes + MIME type are present, fork into the binary branch:

1. **Front-loaded dedup (P7-CAP-10).** Peek via `get_by_path`. If existing row has same `content_hash`, short-circuit: return success. Neither the blob store nor the vision AI is called.
2. **Store blob first.** Call `blob_store.put(content_hash, raw_bytes, mime_type)`. On failure, return `Failure` (cannot proceed without the blob safe).
3. **Store raw row.** Call `upsert_from_upload(...)` with the new `blob_ref` and `mime_type` values, `extracted_text=None`. The row now references the blob. Description stays empty. File is safe.
4. **Describable check.** Compare `mime_type` against `CONFIG.main.capture.vision.describable_mime_prefixes` and `file_size_bytes` against `max_vision_bytes`:
   - Not describable → audit "unsupported type," return success (P7-CAP-12).
   - Too big → audit "too big," return success (P7-CAP-12).
5. **Vision describe.** Call `get_provider("vision", CONFIG.main).describe_image(system, raw_bytes, mime_type)` using the prompt from Component 5.
   - **On success:** Call `attach_summary(vault_path, description, title)`. Best-effort `index_keywords` + `index_embedding`. Write `DESCRIBED` audit entry. Log classify trigger. Return success (P7-CAP-11).
   - **On failure:** Leave description empty. Write failure audit entry. Return success (P7-CAP-11).

The function signature gains optional parameters: `raw_bytes: bytes | None = None`, `mime_type: str | None = None`. When `extracted_text` is present, the existing 7A text path runs unchanged. When `extracted_text` is None and `raw_bytes` is present, the binary branch runs. When both are None, return `Failure`.

**Depends on.** Components 1 (migration), 2 (blob store), 3 (vision config), 4 (vision provider), 5 (vision prompt).

**Assumes.** A1, A2, A3, A7.

**Decisions.**
- Q: Should the blob store instance be passed as a parameter or resolved from a module-level factory? Options: parameter (explicit, testable) / factory (matches CONFIG pattern). Leaning **parameter** because it makes test-stub injection clean and the blob store is remote-owned.
- Q: Should `upsert_from_upload` be extended with `blob_ref`/`mime_type` params, or should a separate `upsert_blob_from_upload` be added? Options: extend (fewer functions) / new function (clearer separation). Leaning **extend** because the existing function already handles the binary case (`extracted_text=None`) and adding two optional params is minimal.

**Done when.** (1) Re-uploading identical content for the same path returns success and neither the blob store nor the vision AI is called (P7-CAP-10). (2) A describable binary upload leaves a row with `blob_ref` and `mime_type` populated, a description in `summary`, and the file findable by keyword + meaning search (P7-CAP-11). (3) A forced vision failure leaves the blob stored, description empty, failure audit entry written, and success returned (P7-CAP-11). (4) An unsupported-type binary leaves the blob stored, description empty, audit says "unsupported type," success returned (P7-CAP-12). (5) A too-big binary leaves the blob stored, description empty, audit says "too big," success returned (P7-CAP-12). (6) The text path (7A) is completely unchanged — passing `extracted_text` with no `raw_bytes` runs the old path identically.

---

### 7. Reference-counted blob delete

**Goal.** When a document row is deleted, check if any other row still references the same blob; if not, delete the blob from object storage. A failed blob delete is harmless (kept, never corrupts data).

**Build.** Extend the delete path (either in `storage/documents.py::delete_by_path` or in a thin wrapper the event handler calls):

1. Before deleting the row, call `get_by_path` to pre-read the row's `blob_ref` (the object key). This is necessary because `delete_by_path` hard-deletes without returning the deleted row's data.
2. Delete the document row via `delete_by_path` (existing behavior — hard-delete + search cleanup in one transaction).
3. After the row is gone, if `blob_ref` was not NULL: run `SELECT COUNT(*) FROM documents WHERE blob_ref = ?` — if zero remaining references, call `blob_store.delete(key)` (best-effort). If the blob delete fails, log the failure and audit it, but **do not fail the event** — an orphaned blob is harmless.
4. If other rows still reference the blob, do nothing.

The blob delete is **outside** the DB transaction (the DB delete is the source of truth; the blob delete is best-effort). This matches the "cache advisory / cloud authority" spirit — the DB says what exists; the blob store follows.

**Depends on.** Components 1 (migration — `blob_ref` column must exist), 2 (blob store — `delete` and the reference-count query).

**Assumes.** A4.

**Decisions.**
- Q: Should the reference-count query run inside or outside the delete transaction? Options: inside (consistent snapshot) / outside (simpler, no extended lock). Leaning **inside** for the count query (consistent read), then blob delete outside (S3 call should not hold a DB lock).

**Done when.** (1) Deleting the last row referencing a blob also deletes the blob from object storage. (2) Deleting a row when another row still references the same blob does NOT delete the blob. (3) A failed blob delete logs the error but does NOT fail the document delete — the row is gone, success is returned (P7-CAP-13). (4) Deleting a text-only row (blob_ref is NULL) works exactly as before — no blob logic fires.

---

### 8. Upload Endpoint re-point — stop discarding bytes

**Goal.** Make the multipart upload handler pass the file bytes to the binary capture branch instead of throwing them away.

**Build.** In `mcp_server/api.py`, modify the multipart branch of `upload_handler`:
- Stop discarding the file bytes (`api.py:141-142`).
- Extract `raw_bytes` and `mime_type` from the multipart form data.
- Call `capture_upload(vault_path=..., extracted_text=None, content_hash=..., raw_bytes=raw_bytes, mime_type=mime_type, ...)` — the new binary branch from Component 6.
- Return the pipeline's result as the HTTP response.

The JSON text path (7A) remains unchanged.

**Depends on.** Component 6 (the binary capture branch must exist to call).

**Assumes.** A6.

**Done when.** A multipart upload with raw bytes hits the binary capture branch (not the text path). The bytes are not discarded. The response reflects the pipeline's result (success with blob stored, or appropriate error).

---

## Handoff notes

- **Contract with Phase 8 (Classify):** Phase 7B delivers binary document rows where `project`/domain are NULL, the folder path is stored verbatim in `vault_path`, and the description (if present) is in `summary`. Phase 8 finds its work the same way as with text captures — by querying the database.
- **Contract with Phase 9 (Blob Serving):** Phase 7B delivers blobs in object storage keyed by `content_hash`, referenced from `documents.blob_ref`. Phase 9 builds the read endpoint that serves those bytes back to a requesting client. The `blob_store.get(key)` method is built now (Component 2) so Phase 9 can use it directly.
- **Contract with the retry runner (deferred):** "Needs description" = `summary IS NULL` (same signal as 7A's "needs summary"). The audit log records *why* a description is missing. The retry runner (not built here) re-describes those rows.
- **OQ-7G is BUILD-BLOCKING for vision code.** Research must verify AgentBase MaaS accepts image input before Components 4 and 6 (the vision parts) are implemented. If MaaS cannot do vision at all, HARD STOP — escalate to human. If MaaS does images only, drop `application/pdf` from the describable set (config edit). Components 1, 2, 3 (migration, blob store, config) can proceed independently of the MaaS verification.
- **OQ-7H is RESOLVED.** `boto3` + separate `KMS_BLOB_*` bucket. The actual `pip`/`pyproject.toml` install happens at implementation time.
- **`boto3` is a new dependency.** Approved in principle by the human. Install at implementation time, not before.
- **Research topics:**
  1. Verify `upsert_from_upload` handles `extracted_text=None` correctly and its skip-identical branch fires in that case (A1).
  2. ~~Verify `delete_by_path` returns or preserves `blob_ref`~~ **A4 RESOLVED:** `delete_by_path` does not return row data; spec patched to use `get_by_path` pre-read in the event handler.
  3. Verify `OpenAIProvider` SDK version supports `image_url` content blocks in messages (A5).
  4. Verify the multipart handler has raw bytes available in memory (A6).
  5. **BUILD-BLOCKING:** Verify AgentBase MaaS accepts image input + check PDF support (A9 / OQ-7G).
  6. Verify VNG Object Storage access details — endpoint URL, bucket creation method, env var names for blob credentials (OQ-7H follow-up).
  7. Verify `content_hash` hex string is a valid S3 object key (A8).

---

## Open questions

_Deferred per instruction — do not block on these; carry into research/planning._

- **OQ-7D — Multi-blob per document.** Carried from design. Two columns now; migrate to a `blobs` table only if a real multi-blob need appears. Not a blocker.
- **OQ-7E — Vision model field placement.** Recommendation: dedicated `vision_model` field per provider config. Note C-09 field-list growth. Not a blocker.
- **OQ-7G — MaaS vision capability + PDF.** Route decided (MaaS). Research found vision models exist (Qwen3-VL, Skylark-vision) with 50 req/day rate limit, but `image_url` acceptance not directly documented. Proceeding with standard OpenAI `image_url` format for images; `application/pdf` dropped from default describable set. Verify at integration time. Non-vision components (1, 2, 3, 5, 7, 8) can proceed independently.
- **OQ-7I — Audit outcome name.** Recommendation: distinct `DESCRIBED` for observability. Not a blocker.

---

## Next step

Spec written. Run `/research` to verify spec assumptions against real code before planning. Pay special attention to **A9 / OQ-7G** (MaaS vision capability — build-blocking) and **A4** (delete_by_path blob_ref preservation) before any implementation.
