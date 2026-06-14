# Phase 7 Slice 7B — Visual / Binary Capture: Design

_Status: Design (codebase-design-analysis output). Feeds `/writing-detailed-specs`._
_Requirements (locked, signed off 2026-06-14): `docs/0_draft/phase7/phase7b_visual_capture_requirements.md`._
_Design seed: `docs/1_design/phase7/phase7_capture_refactor.md` (Amendment §A1–§A7) — re-grounded against live code below._
_Converge point: `docs/2_specs/phase7/phase7a_text_capture.md` + the live `capture_upload` (`src/pipelines/capture.py`). 7B extends 7A's store-raw-first contract; the two paths fork cleanly and share only the converge point._
_Upstream inputs: ADR-0013 (raw-byte hash + reconcile ownership), ADR-0014 (store-raw-first / AI-failure store-anyway), ADR-0015 (visual/binary: cloud stores blob + vision-describes); `docs/0_draft/cloud_native_rearchitecture.md`; `docs/0_draft/agentbase_research.md` §11.2._
_**Behavior-inventory ID prefix: `P7-CAP`** (entries `P7-CAP-10…13`, `origin: design`, `granularity: outcome`, in `docs/system_behavior/behavior_inventory.yaml`). These are design-intent outcomes — implementation cannot override them._
_Reader mode: non-coder-readable (default). Every section leads with plain English; code references are parenthetical anchors. The doc makes sense if every code token were deleted._

---

## In plain terms (the whole thing in a paragraph)

Today the cloud only knows how to handle **text** that the laptop helper pulled out of a file. But some files have no useful text — a photo, a scanned page, and especially a **chart someone drops into a folder**. The laptop helper has no AI, so for those files it sends the **actual file** up to the cloud instead. Slice 7B teaches the cloud to do three things with that file: **keep the file in cloud file storage, remember where it lives, and ask a picture-reading AI to describe it once in words** so the chart becomes findable by search like any text note. The safety promise is exactly the text path's: **save the file first (it is safe the moment it lands), describe it second (the description may arrive late, or not at all if the file is too big, the wrong type, or the AI is down).** When the user later deletes a file, the cloud throws the file's bytes away **only if no other captured file points at the same bytes**. The text path (7A) is untouched.

This design resolves the seven decisions the requirements deferred. The two genuinely "bring-it-back-to-the-human" items — **which storage library + which bucket**, and the **vision-model wiring** — are presented as options with a recommendation and left as open questions; nothing is installed and nothing is marked decided.

---

## Cast of characters (symbols used 3+ times across this doc)

| Name | Plain-English role | Code anchor |
|---|---|---|
| Capture Receiver | Front door of the pipeline: validates an upload and asks "seen this exact content before?" | `pipelines/capture.py::capture_upload` (extended in 7B) |
| Document Store | The central table of document records (text/description, fingerprint, and — new — a blob reference) | `storage/documents.py` (`documents` table) |
| blob | The raw bytes of a user's binary/visual file (the file itself, not any text from it) | stored in object storage, never in the DB |
| blob store | The app's own write path into object storage for blobs (a small helper wrapping an S3-compatible client) | NEW module, e.g. `storage/blobs.py` |
| blob reference | The pointer on a document row to where its blob lives (object key) + the file type | NEW nullable columns on `documents` (migration 009) |
| content-addressed key | The blob's object-storage filename, derived from the raw-byte fingerprint, so identical bytes are stored once | the `content_hash` value |
| Vision Describer | The picture-reading mode of the cloud AI; describes an image/page in words | NEW vision call via `get_provider("vision", CONFIG.main)` |
| Audit Log | The tamper-evident record of every AI decision, skip, and failure (with the reason) | `core/audit.py` → `storage/audit_log.py` |
| Object Storage | S3-compatible cloud file store (VNG); already holds the database backups | `LITESTREAM_*` env vars today |

Glossary of one-off terms is inline where they appear. A full glossary table is at the end.

---

## Decision (the chosen shape, in one paragraph)

**Chosen:** extend the live text-capture entry (`capture_upload`) with a **binary branch** that (1) checks for a duplicate over the raw-byte fingerprint **before** anything, (2) **stores the blob first** to object storage under a **content-addressed key**, (3) saves a document row carrying a **blob reference + file type** in **two new nullable columns** added by **migration 009** (no new table), (4) routes the file to the **Vision Describer** only if it is within a config size cap **and** its type is in a config "send-to-vision" set, (5) writes the description into `summary` + `full_body` and audits success, and (6) on any skip or failure stores the blob anyway, audits the reason, and returns success. Delete is **reference-counted**: the existing hard-delete of the document row stays, and the blob is removed only when the last row referencing it is gone. The storage-client library + bucket choice and the vision-provider wiring are recommended but **left open for the human to confirm before install**.

This shape is chosen because it is the smallest surface that honours the locked "content is sacred / description may arrive late" contract, reuses every existing document reader (search, audit, delete) unchanged, and survives file moves.

---

## Q1 Diagram — what happens inside (the chosen option)

_This is a zoom-in of the binary branch of the two-input-fork Q1 in `phase7_capture_refactor.md`. Same names, box style, and arrow conventions. The text branch (store text → summarize → attach) is unchanged and omitted here._

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

---

## Guardrail Checklist (from `/guardrail-check Review`)

Domains touched: **Write Safety, DB Integrity, LLM & Providers, Architecture, Testing.** Skipped: **Async & CLI** (no new event-loop entry; the binary branch reuses 7A's `capture_upload` async entry and the existing `/api/upload` handler).

- [ ] **C-01 · DB is source of truth; vault is read-only** — 7B stores blob bytes to **object storage** (a new external write target, not the vault, not the DB); the description goes to `documents.summary`/`full_body`. No vault write. Satisfies — flag that the blob store is a new write surface, governed by ADR-0015's stated privacy shift.
- [ ] **C-04 · `PRAGMA foreign_keys=ON` on every connection** — the reference-count query, the blob-reference write, and the delete all reuse `storage/db.py::get_connection`. Add no new connection factory.
- [ ] **C-05 · Schema changes via versioned `.sql` only** — blob-reference columns land **only** as `009_*.sql`; bump the version-pin test from 8 → 9 (`tests/test_storage/test_migration_007.py:41,56` per the CLAUDE.md convention).
- [ ] **C-06 · Thresholds in config, never in code** — the **size cap** is a config value read from config, not a float literal in the binary branch. (It is a size limit, not a confidence band, but the "no magic number in `pipelines/`" spirit applies.)
- [ ] **C-07 · Prompts as YAML** — the vision instruction lives in `prompts/describe_image.yaml`, loaded via `PROMPTS[...]`. No inline prompt string.
- [ ] **C-08 · `get_provider(task, CONFIG)` factory** — the vision call routes through `get_provider("vision", CONFIG.main)`; the new image-capable method is invoked on the factory-returned provider, never a direct provider instantiation.
- [ ] **C-09 · Providers carry model/synthesis_model/embedding_model** — adding a `vision_model` field is a same-shape addition; confirm it does not make the three-field rule provider-specific (OQ-7E). Routing stays task-driven.
- [ ] **C-12 · Result types at module boundaries** — the binary branch, the blob-store put, the reference-count delete, and the describe call all return `Success`/`Failure`; the vision-failure / size-skip / unsupported-type paths return `Success` (store-anyway), never raise.
- [ ] **C-13 · Audit log non-negotiable** — a successful describe writes a `DESCRIBED` (or reused `CAPTURED`) entry; every skip/failure (too-big / unsupported-type / vision-failed) writes an audit entry **stating why**, all via `core.audit.write` with `source_ids` set. `new_correlation_id()` must fire first (the 7A audit caveat).
- [ ] **C-15 · No MCP tool before its pipeline** — 7B adds **no** MCP tool. Blob-serving retrieval and the `kms_inspect` rework are Phase 9. The classify trigger stays a log line.
- [ ] **C-17 · No module-scope CONFIG in tests** — new 7B tests lazy-import CONFIG / pass explicit `db_path`; the blob store is tested via a local-filesystem stub (the dependency is remote-owned → use a substitutable local adapter).

---

## The seven deferred decisions (options grids)

Each decision below leads in plain English, then gives every viable option full treatment, then a recommendation. Rejected alternatives get a one-line dismissal. Per the skill, every viable option gets a Q1 description; the chosen option's Q1 is the one above (the decisions compose into that single flow), and per-option Q1 descriptions are given inline where an option's internal flow differs materially.

### Decision 1 — Blob-reference shape: two columns on `documents` vs a separate `blobs` table

**In plain terms:** Every captured file already gets one row in the document table. A binary file additionally needs to record *where its bytes live* and *what type it is*. The question is whether that goes on the existing row (two extra columns) or into a new side-table linked back to the row.

**Option A — Two nullable columns on `documents` (Recommended).**
- **What this means:** A binary row is just a normal document row that also says "my bytes live at this object key, and my type is this." Search, audit, and delete already work off the document row, so a binary is simply a document whose body is a description and whose bytes live elsewhere.
- **Approach:** Migration 009 adds two nullable columns — a blob reference (the object key) and `mime_type`. NULL on every existing row and every text row; populated only on binary rows. `DocumentRow` gains the two fields read with the same `if "col" in row.keys()` guard the late-added columns already use (`documents.py:62-76`). A small sibling write (`upsert_blob_from_upload`, or a `blob_ref`/`mime_type` extension of `upsert_from_upload`) writes them on the store-blob-first beat.
- **Files touched:** `storage/migrations/009_*.sql` (new), `storage/documents.py` (`DocumentRow` + `_row_from_sqlite` + a blob-aware upsert), `tests/test_storage/test_documents.py`, the version-pin test.
- **Cost:** Dev effort low. Runtime cost: none beyond the existing single-row write. Maintenance: two columns, no join.
- **Risk:** If a document ever legitimately needs *several* blobs (a multi-page scan kept as separate images), two columns cannot model it — but nothing in 7B or Phase 9 asks for that (captured as **OQ-7D**, carried from the seed).
- **Module depth:** Deepens the already-deep `storage/documents.py` (small interface over real SQL) by two columns and at most one sibling write; no new module, no join. **Deletion test:** removing the columns would re-scatter "where is this file" into callers — they earn their keep.
- **What it defers:** A multi-blob future migrates to Option B then (OQ-7D).
- **Constraints check:** C-05 satisfies (migration 009); C-04 satisfies (reuse `get_connection`); C-01 satisfies (DB row holds a *reference*, bytes go to object storage); all others n/a.

**Option B — A separate `blobs` table keyed by document id (Not recommended).**
- **What this means:** Binary file locations live in their own table, linked to the document row by id.
- **Approach:** Migration 009 creates a `blobs` table (`document_id` FK, `object_key`, `mime_type`, `size`); the binary branch inserts there after creating the document row. Every reader that wants the blob does a join.
- **Per-option Q1 difference:** the "Store Blob First" box becomes two writes (document row, then a `blobs` insert) and the delete + reference-count logic queries the `blobs` table instead of a column — otherwise identical flow.
- **Cost:** Dev effort medium (new table, new reader, join in delete/retrieve). Maintenance: a table and a join for a one-to-one relationship.
- **Risk:** A new seam with only **one adapter today** (the binary branch) — speculative unless multi-blob is foreseen (it is not). Adds a join to the Phase 9 blob-serving reader for no present gain.
- **Module depth:** Introduces a new table + a join for a strict one-file = one-blob = one-row relationship two columns model directly. **Deletion test fails:** deleting the `blobs` table would just collapse back into two columns — it is a pass-through abstraction.
- **What it defers:** Nothing extra; it pre-pays for a multi-blob future that may never arrive.
- **Constraints check:** Same as A on C-04/C-05/C-01; loses on the seam-discipline lens.

> **Recommended: Option A.** It is the only shape that adds no join, reuses every existing document reader unchanged, and survives file moves — at the cost of one additive migration. The trade-off the reader can weigh: pick A and a future multi-page-scan feature needs a one-time migration to a table; pick B and pay a join on every blob read forever for a flexibility nothing yet needs.

### Decision 2 — Blob key scheme: content-addressed vs path-based

**In plain terms:** Object storage needs a filename for each stored file. We can name it after the file's *content fingerprint* (so identical bytes are stored once and a move never re-uploads), or after its *folder path* (so the key reads like the file's location).

**Option A — Content-addressed key, derived from the raw-byte fingerprint (Recommended).**
- **What this means:** The blob's object name is its content fingerprint. Two files with identical bytes map to one stored object; moving a file (which changes its path) never orphans or re-uploads the blob, because the bytes — and therefore the key — did not change.
- **Approach:** The object key is the `content_hash` value the daemon already supplies (over raw bytes, ADR-0013), optionally namespaced (e.g. `blobs/<hash>`). The document row's blob reference stores this key. A move is reported as a path rename (`rename`, `documents.py:359`) and touches no blob.
- **Cost:** Dev effort low. Runtime: a put that is idempotent (re-putting identical bytes to the same key is a no-op or harmless overwrite). Maintenance: none extra.
- **Risk:** Because identical bytes share one object, **deleting one row must not delete the shared blob** — this is exactly why delete is reference-counted (Decision 5). A cosmetic binary change flips the hash → a new key → a re-upload (rare, accepted per ADR-0013).
- **Module depth:** No new module; the key is a derived value, not an abstraction.
- **Constraints check:** C-01 satisfies; aligns with the front-loaded dedup (the same fingerprint gates both the document dedup and the blob de-dup).

**Option B — Path-based key (Not recommended).**
- **What this means:** The blob's object name mirrors the file's vault path.
- **Approach:** Object key = the vault path (e.g. `Projects/Alpha/attachment/chart.png`).
- **Risk:** Every file move re-keys (and re-uploads) the blob; two files that end up at the same path after a rename collide; identical bytes in two folders are stored twice. The per-path identity the requirements want (locked decision 7) is already preserved by the **document row** — the blob layer does not need to carry it.
- **Constraints check:** Functionally works, but loses move-stability and de-duplication.

> **Recommended: Option A (content-addressed).** The fingerprint is stable across moves, unique per content, and matches the dedup the pipeline already does — so a move never costs a re-upload and identical bytes never store twice. Per-path identity stays on the document row (each path still gets its own row and its own description); only the *bytes* are de-duplicated at the object level. This is **not** the rejected "share one summary across two paths" optimization — content-addressing the blob does not share descriptions.

### Decision 3 — Vision-provider extension shape

**In plain terms:** The cloud AI today can only take text in and give text out. To describe a picture, we need to add a way to send an image to a picture-capable model. The question is how to add that cleanly without breaking the existing text path.

**Verified facts (read in code 2026-06-13/14):** Every provider implements `complete(system: str, user: str)` — both strings (`llm/provider.py:40`; `claude_provider.py:51` sends `messages=[{"role":"user","content": user}]`, a plain string). There is **no** image-input path. The factory routes by task (`get_provider(task, config)`, `provider.py:54`); tasks are a fixed list (`Task` literal, `config.py:43-45`); each task maps to a provider via `ProvidersConfig.for_task` (`config.py:185`); model names live on `ClaudeConfig` (`config.py:196`).

**Option A — New `describe_image` method on the provider interface that default-raises (Recommended).**
- **What this means:** The picture-reading ability is added as a clearly-separate skill on the AI provider. Text providers that cannot see images say so honestly instead of pretending.
- **Approach:** Add `async def describe_image(self, system, image_bytes, mime_type) -> Result[LLMResponse]` to `LLMProvider` with a default body that returns `Failure("vision not supported")` (or raises a typed not-supported error caught into a `Failure`). `ClaudeProvider` overrides it to send an image content block (the Anthropic SDK accepts an image block in the `content` list alongside text). Add `"vision"` to the `Task` literal, a `vision: Provider` field to `ProvidersConfig`, and a `vision_model` field to `ClaudeConfig`. The Vision Describer calls `get_provider("vision", CONFIG.main).describe_image(...)`. `complete()` is untouched.
- **Cost:** Dev effort medium (interface method + one real implementation + 3 config fields + 1 task). Runtime: one vision call per describable binary. Maintenance: a second provider method to keep alive across providers.
- **Risk:** Other providers (`ollama`, `openai`, `claude_cli`) inherit the default-raise — fine, since the `"vision"` task routes to `claude` in config. If the deployment routes LLM through AgentBase MaaS (OpenAI-compatible, agentbase §8) instead of the Anthropic SDK, the image block shape differs (`image_url` vs Anthropic image block) — a real wiring risk (OQ-7G / OQ-7E note).
- **Module depth:** Deepens the provider interface by one capability with **2 adapters** (a real seam: the default-raise base + the Claude override) — not speculative. The text method stays untouched, so the change is additive.
- **Constraints check:** C-07 satisfies (`prompts/describe_image.yaml`); C-08 satisfies (factory dispatch); C-09 — adding `vision_model` is same-shape but grows the "required fields" list (OQ-7E); C-13 satisfies (describe audit).

**Option B — Overload `complete()` with optional image parameters (Not recommended).**
- **What this means:** Add optional `image_bytes`/`mime_type` arguments to the existing text call.
- **Risk:** Every provider's `complete()` signature changes; the text path now carries vision-shaped parameters it ignores; the "text-only is honest" property is lost. Larger blast radius (all four providers + all `complete()` callers) for no gain.
- **Constraints check:** Touches every provider unnecessarily.

> **Recommended: Option A (a separate `describe_image` method, default-raising).** It adds vision as a clearly-separate capability rather than overloading the text method, keeps non-vision providers honest, and leaves the text path byte-for-byte unchanged. The exact method signature is an implementation detail for the spec; the design fixes only that a new image-capable call is required and that it routes through the factory.

### Decision 4 — Storage-client dependency + bucket topology (NEW DEPENDENCY — human confirms before install)

**In plain terms:** The cloud already talks to object storage, but only the database-backup helper does, and only to copy the database file. The app itself has **no** way to write an arbitrary file. We must add one S3-compatible client (the requirements pre-approved adding *one* in principle) and decide whether blobs share the existing backup bucket or get their own. **This is the item that must come back to you before anything is installed.**

**Verified facts:** No `boto3`/`botocore`/`aioboto3`/`minio`/`s3fs` anywhere in `src/` or `pyproject.toml` (grep confirmed). The only object-storage consumer is **Litestream** (a separate binary, `litestream.yml` + `scripts/start.sh`), using `LITESTREAM_BUCKET` / `LITESTREAM_ENDPOINT` / `LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY`. Litestream cannot store an arbitrary blob — it only mirrors `/data/kb.db`. The rest of the cloud is async (Starlette/uvicorn, `mcp_server/api.py`).

**Library options:**

**Option A — `boto3` (sync), wrapped behind a blob-store helper + run in a thread (Recommended).**
- **What this means:** Use the most widely-used, best-documented S3 client; call its blocking put/get from the async handler the same way Ollama already wraps a blocking call.
- **Approach:** Add `boto3`; build a tiny `storage/blobs.py` with `put(key, bytes, mime)` / `get(key)` / `delete(key)` / `exists(key)` returning `Result`. Call the blocking client via `asyncio.to_thread(...)` inside the async capture branch (the exact pattern `OllamaProvider` uses today, per TD-010). A local-filesystem stub stands in for tests (the dependency is remote-owned).
- **Cost:** Dev effort low–medium. Runtime: a blocking S3 call wrapped in a thread per binary capture. Maintenance: one well-known dependency.
- **Risk:** Sync-in-async via a thread is slightly less elegant than native async, but it is the project's established pattern (TD-010) and avoids a second async-S3 dependency footprint.

**Option B — `aioboto3` (async, native) (Viable, heavier).**
- **What this means:** A natively-async S3 client matching the cloud's async style.
- **Approach:** Same blob-store helper, but `await client.put_object(...)` directly — no thread wrapper.
- **Cost:** Dev effort medium. Maintenance: `aioboto3` pulls `aiobotocore` + pins `botocore` versions tightly — a heavier, more fragile dependency tree than `boto3`.
- **Risk:** Version-pin churn against `botocore`; smaller community than `boto3`.

**Option C — `minio` (lightweight S3 client) (Viable, smallest).**
- **What this means:** A small, focused S3 client (sync) popular for self-hosted/S3-compatible stores.
- **Cost:** Smallest dependency footprint; sync (same thread-wrap as Option A).
- **Risk:** Less battle-tested against VNG's specific S3 dialect than `boto3`; the project would be betting on minio's compatibility with VNG Object Storage, which is unverified.

**Bucket topology options:**

**Topology 1 — Separate `KMS_BLOB_*` bucket + credentials (Recommended).**
- **What this means:** Blobs go in their own bucket, configured by their own env vars, distinct from the database-backup bucket.
- **Why:** The backup bucket is Litestream's territory — mixing arbitrary blobs into it risks confusing Litestream's restore scan and couples two very different lifecycles (DB snapshots churn every second; blobs are write-once). Separate buckets keep failure domains and retention policies independent.
- **Cost:** One more bucket + one more credential set to provision.

**Topology 2 — Reuse the Litestream backup bucket under a `blobs/` prefix (Viable, fewer creds).**
- **What this means:** Blobs live in the same bucket as the DB backups, under a key prefix.
- **Why considered:** One fewer bucket and credential set to provision.
- **Risk:** Litestream's restore reads the bucket; a large blob population alongside DB snapshots is operationally muddier and risks accidental retention/lifecycle rules hitting the wrong objects.

> **Recommended: `boto3` (Option A) + a separate `KMS_BLOB_*` bucket (Topology 1)**, both wrapped behind a small in-house blob-store helper with a local-filesystem test stub. `boto3` is the most reliable bet against VNG's S3 dialect and matches the project's existing sync-in-async-via-thread pattern; a separate bucket keeps blobs from entangling with Litestream's backup lifecycle. **This is a recommendation, not a decision — the library + bucket choice is `OQ-7H` and must be confirmed by the human before install. Do NOT install `boto3` (or any client) or add it to `pyproject.toml` as part of this design.**

### Decision 5 — Reference-counted delete (first verify a cloud delete path exists)

**In plain terms:** When the user deletes a file, the cloud removes its record. If we also kept the file's bytes in object storage, we should throw those bytes away too — **unless another captured file points at the same bytes** (which happens because identical bytes share one stored object). So "delete the blob only when the last record pointing at it is gone."

**Verified — a cloud delete path EXISTS (not a Phase-6 dependency).** `POST /api/event` with `{"type":"deleted","path":...}` already calls `delete_by_path(path)` (`mcp_server/api.py:334` → `documents.py:256`), which hard-deletes the document row and its search-index entries in one transaction, returning `Success(rowcount)` (0 = not found → `{"status":"not_found"}`). **The daemon's delete report has a real hook today.** The seed's "assumption to verify" (requirements decision 5) is resolved: 7B extends this existing path; it does **not** invent one and does not depend on unbuilt Phase 6 work.

**Option A — Count-then-delete inside the same delete transaction (Recommended).**
- **What this means:** When a row is deleted, the system checks "does any other row still reference this blob's key?" If none do, it deletes the blob from object storage; if some do, it leaves the blob alone.
- **Approach:** In `delete_by_path` (or a thin wrapper the event handler calls), before/after deleting the document row, read the row's blob reference; after the row is gone, run `SELECT COUNT(*) FROM documents WHERE blob_ref = ?` — if zero, call `blob_store.delete(key)`. The DB delete stays the source of truth; the blob delete is best-effort (a failed blob delete logs + audits but never fails the event — an orphaned blob is harmless and cheap, ADR-0015 cost note).
- **Cost:** Dev effort low–medium. Runtime: one extra count query + one conditional S3 delete per binary delete. Maintenance: contained in the data layer.
- **Risk:** A race between two concurrent deletes of two rows sharing one blob could, in theory, leave the blob (both see the other's row still present) — acceptable: an orphaned blob wastes a little storage, never corrupts data. Single-replica MVP (`--max-replicas 1`, agentbase §11.4) makes this near-impossible today.
- **Module depth:** Deepens the data layer; the count + conditional delete live next to the row delete in one transaction (mirrors how 7A keeps search-index cleanup in the same `with get_connection`). **Deletion test:** removing this logic would re-scatter "is the blob still needed?" into the event handler — it earns its keep.
- **Constraints check:** C-04 satisfies (same connection); C-12 satisfies (Result); C-13 satisfies (audit the blob delete/keep decision).

**Option B — Never delete blobs (store-forever) (Not recommended as default; viable fallback).**
- **What this means:** Document rows are deleted but blobs are kept forever.
- **Cost:** Zero delete logic; simplest.
- **Risk:** Object-storage cost grows unbounded; violates the requirements' explicit "delete now, reference-counted" decision. Kept only as the degraded behaviour if the blob-delete call fails.

**Option C — Per-row blob delete (no reference count) (Rejected — corrupts shared blobs).**
- Deleting one row deletes the shared blob, breaking every surviving row that points at the same content-addressed object. Directly contradicts content-addressing (Decision 2).

> **Recommended: Option A (count-then-delete, best-effort blob removal).** It honours the locked "delete-when-last-reference-gone" rule and is safe with content-addressed sharing; the only accepted cost is a rare orphaned blob under concurrent deletes, which wastes storage but never corrupts data. A failed blob delete degrades to Option B (keep the blob) rather than failing the user's delete.

### Decision 6 — Vision routing: where the "describe vs store-only" decision is made + is the type set config-driven

**In plain terms:** The pipeline must decide, per file, whether to send it to the picture-reading AI or just store it. Images and text-less PDFs get described; zips, videos, and other binaries are stored only. We want that "which types get described" list to live in config so new formats can be added without code changes.

**Decision: the routing lives as a fork inside the binary branch of `capture_upload`, reading a config "send-to-vision" set (Recommended).**
- **What this means:** Right after the blob is stored, one check asks "is this file's type in the describable set, and is it within the size cap?" — yes routes to the Vision Describer, no goes store-only with an audit reason.
- **Approach:** A config list (e.g. `capture.vision.describable_mime_prefixes: ["image/", "application/pdf"]` and a size cap) read via `CONFIG`. The binary branch checks `mime_type` against the set and the size against the cap; both checks are config-driven (C-06 spirit — no hardcoded type list or size literal in `pipelines/`). The decision is **not** an AI/confidence decision, so no `ConfidenceGate` is involved.
- **Files touched:** `core/config.py` (a small `VisionConfig`/`CaptureConfig` extension), `config/config.yaml`, the binary branch in `pipelines/capture.py`.
- **Constraints check:** C-06 satisfies (config, not literals); C-12 satisfies; C-13 satisfies (store-only path audits the reason).

**PDF caveat (must be verified before build — OQ-7G):** the requirements ask whether the configured vision model can accept **PDF** input or only images. The Anthropic SDK *does* accept a PDF document block on vision-capable models — but whether the project's configured vision model (and route — direct Anthropic vs AgentBase MaaS) accepts it is unverified here. **If the configured model accepts only images, text-less PDFs cannot be described and must fall to store-only** (and the "describable set" must drop `application/pdf`). This is a build-time verification, flagged as OQ-7G; the design keeps PDF in the *default* describable set but makes the set config-editable so it can be removed without code change.

**Rejected alternative:** putting the routing decision in the API handler (`api.py`) — violates the handler-stays-logic-free pattern (the handler validates + dispatches; the pipeline owns the fork).

### Decision 7 — Size cap → store + skip vision + audit reason

**In plain terms:** Very large files should still be saved (never lost) but should not be sent to the picture-reading AI — to avoid surprise vision-API cost and model rejection on giant files. The size limit is a config value.

**Decision (Recommended): a config size cap; over-cap files store the blob, skip the Vision Describer, and write an audit entry saying "too big."**
- **What this means:** The size check sits in the same fork as the type check (Decision 6). A file over the cap takes the store-only path: blob stored, description empty (the same "needs-description" derived state), audit reason = too-big.
- **Approach:** A `max_vision_bytes` (or similar) config value (C-06). The binary branch compares `file_size_bytes` (already supplied on the upload) to the cap **before** calling the Vision Describer. There is already a `handlers.max_file_size_bytes` (50 MB) for filesystem extraction (`config.py:286`) — the vision cap is a *separate, smaller* threshold (vision API limits and cost differ from extraction limits); do not reuse the extraction cap.
- **Constraints check:** C-06 satisfies (config); C-13 satisfies (audit reason); consistent with the no-new-flag-column rule (decision 9 / ADR-0014).

---

## Rejected alternatives (one line each)

- **Blob reference as a separate `blobs` table (Decision 1 Option B)** — adds a join + table for a one-to-one relationship two columns model directly; folded into OQ-7D as a future multi-blob option.
- **Path-based blob key (Decision 2 Option B)** — re-uploads the blob on every move and collides on post-rename path clashes; content-addressing avoids both.
- **Overload `complete()` with image args (Decision 3 Option B)** — changes every provider's signature and pollutes the text path for no gain.
- **Per-row blob delete with no reference count (Decision 5 Option C)** — deletes a shared content-addressed blob and corrupts surviving rows.
- **Never-delete blobs as the default (Decision 5 Option B)** — unbounded storage cost; kept only as the failed-delete fallback.
- **Routing the describe/store decision in the API handler** — breaks the logic-free-handler pattern.
- **Image stays local; cloud stores only the description (ADR-0015 option 1)** — rejected upstream; user cannot view a dropped graph from phone/web and the cloud cannot re-describe later.
- **Daemon OCRs/describes locally (ADR-0015 option 3)** — rejected upstream; pushes heavy AI into the thin daemon and OCR cannot interpret a graph.

---

## Implications

- **The user's actual file bytes now leave the laptop and live in cloud object storage.** This is a real privacy / data-residency shift from 7A (which sent only text). Accepted for the single-tenant personal deployment; a per-vault opt-out is deferred.
  - Governed by ADR-0015's stated "privacy / data-residency shift." The blob store is a new external write surface alongside the vault and the DB; C-01 is satisfied (the DB still holds only a reference, not the bytes) but the new surface must be a stated property.
- **The cloud gains its first ability to write an arbitrary file to object storage.** Until now only Litestream wrote there, and only the database file.
  - A new dependency (one S3-compatible client) and a new module (`storage/blobs.py`) are required; this is the riskiest finding and the human-gated install (OQ-7H). The client is wrapped so the rest of the pipeline never speaks S3 directly and tests use a local stub.
- **A binary file becomes findable by search only after its description lands.** The bytes are safe immediately, but keyword/meaning search has nothing to match until the Vision Describer writes the description.
  - The description populates `documents.summary` (flows into both search indexes exactly as the text summary does) and `full_body` (so keyword search over body matches; raw bytes never enter `full_body`). On skip/failure both stay empty and "needs-description" is derived from `summary IS NULL` — the same signal as 7A's "needs-summary." No new flag column.
- **Deleting a file may or may not delete its bytes.** Because identical bytes are stored once and shared, removing one file's record must not remove a blob another file still points at.
  - Realized by a count-then-delete in the existing `/api/event` delete path (`delete_by_path`); the blob delete is best-effort and a failure degrades to keep-the-blob.
- **Capture still guesses no project or domain, and adds no MCP tool.** The binary branch converges on the same classify-trigger log line as 7A and ships no chat tool.
  - Blob-serving retrieval and the `kms_inspect` rework are explicitly Phase 9 (ADR-0015).
- **A new migration (009) is required and will trip the version-pin test.** Any schema change lands as a versioned `.sql` file.
  - Migration 009 adds the two blob-reference columns; per the CLAUDE.md convention, bump `test_migration_007.py`'s `version == 8` assertions to `9` — this is the expected update, not a regression.
- **Module-depth check.** `storage/documents.py` is a **deep** module; 7B deepens it (two columns + a blob-aware upsert + the reference-count delete) without adding a new table or join. The new `storage/blobs.py` is a **real seam** (2 adapters: the real S3 client + a local-filesystem test stub) — it earns its keep by hiding S3 from the whole pipeline and making the remote dependency substitutable in tests. The vision capability on the provider is a real seam (default-raise base + Claude override).

---

## Known tradeoffs (what we give up by choosing this shape)

- **A binary row can sit undescribed for a while (or forever).** Between store-blob and a successful describe, or after a skip/failure with no retry runner, the file has bytes but no searchable description. Accepted because the bytes are safe and the alternative (reject-on-failure) loses the user's file on a transient outage. The retry runner is deferred (same as 7A).
- **Identical bytes share one blob, so delete must be conditional.** Content-addressing buys move-stability and de-duplication at the price of reference-counted delete logic and a rare orphaned blob under concurrent deletes. Accepted: a stray blob wastes a little storage, never corrupts data.
- **Two columns cannot hold more than one blob per document.** A future multi-page-scan feature needs a one-time migration to a `blobs` table (OQ-7D). Accepted: nothing in 7B or Phase 9 needs multi-blob.
- **Sync S3 client in an async pipeline (if `boto3` is chosen).** A blocking put/get wrapped in a thread is slightly less elegant than native async, but matches the project's established pattern (TD-010). Accepted unless the human picks `aioboto3`.

---

## Risks (for research / planning / implementation to watch)

- **The dedup short-circuit must move ahead of BOTH the blob store and the vision call.** For the binary path the front-loaded fingerprint check must short-circuit before storing the blob and before the vision call — or a re-uploaded identical image re-stores the blob and re-pays for vision. Verify with a test that neither the blob `put` nor the vision call fires on a duplicate. (Extends 7A's P7-CAP-01 ordering to P7-CAP-10.)
- **The vision call shape depends on the LLM route.** The Anthropic SDK takes an image content block; AgentBase MaaS is OpenAI-compatible (`image_url` shape, agentbase §8). If the deployment routes the `"vision"` task through MaaS instead of direct Anthropic, the `describe_image` implementation differs. Research must confirm the actual vision route + model and that it accepts the chosen input shape (OQ-7G).
- **PDF describability is unverified.** Whether the configured vision model accepts a text-less PDF (vs only raster images) decides whether `application/pdf` stays in the describable set or text-less PDFs fall to store-only (OQ-7G). Keep the set config-editable so this is a config change, not a code change.
- **New external dependency + bucket — must be confirmed before install.** No S3 client exists; one must be added (`boto3` recommended) plus a bucket choice (separate `KMS_BLOB_*` recommended). Do not install until the human confirms (OQ-7H).
- **`upsert_from_upload` already accepts `extracted_text=None` for binaries** (`documents.py:100`) — but it does **not** write a blob reference or `mime_type` today (no such columns). The binary store beat needs either an extended `upsert_from_upload` or a sibling `upsert_blob_from_upload`. Confirm which at spec time; reuse the existing skip-identical dedup branch either way.
- **The `/api/upload` multipart handler currently DISCARDS the file bytes and mime_type** (`api.py:141`, a P5 Slice 2 stub) and calls `upsert_from_upload` with `extracted_text=None`. 7B must re-point the multipart branch at the new binary capture entry (mirroring how 7A re-pointed the JSON branch at `capture_upload`) AND stop discarding the bytes. Note: actually *receiving* the raw bytes over the wire is the daemon/Phase-6 contract — 7B handles bytes once present; confirm the multipart field carries them.
- **Audit `source_ids` + the describe outcome name.** Use `vault_path` for `source_ids` (consistent with 7A / OQ-7C). Decide whether the success outcome is `DESCRIBED` or reuses `CAPTURED` (the seed left this open as part of §A5) — `audit.write` accepts any outcome string, so either works; recommend a distinct `DESCRIBED` for observability. Flagged as OQ-7I.
- **Best-effort indexing on binary rows.** The keyword/meaning indexers fold `summary`+`title` — on a binary row the description is the summary, so indexing works the same as text once the description lands; on skip/failure skip the meaning index (no description), exactly like 7A's failure branch.
- **`new_correlation_id()` must fire first** in any new binary entry path, or every audit write silently drops (the 7A caveat — already handled inside `capture_upload`; preserve it if a separate binary entry is added).
- **Concurrent-delete orphan blob** — under multi-replica this could leave a blob; MVP is single-replica (`--max-replicas 1`). A future sweep/reconcile could reclaim orphans if it ever matters (not built now).

---

## Open questions

_Deferred per instruction — do NOT block on these; carry into research/planning. OQ-7D and OQ-7E are carried from the seed; OQ-7G/7H/7I are new._

**OQ-7D — Blob-reference shape if a document ever needs more than one blob.** (carried from seed §A4)

Right now the chosen shape is two nullable columns on the document row (one blob reference, one file type), modelling a strict one-file = one-blob = one-row relationship.

The question: if a future feature needs several blobs per document (e.g. a multi-page scan kept as separate page images), do we migrate to a dedicated `blobs`/`attachments` table?

**If keep columns:** zero extra join, simplest reader path; cannot represent more than one blob per row.
**If add a `blobs` table:** clean one-to-many; adds a join for every reader and a table to maintain — complexity nothing in 7B or Phase 9 currently needs.

Recommendation: keep the two columns now; migrate to a table only if a real multi-blob need appears. Not a blocker.

**OQ-7E — Where the vision model name lives in provider config, and the C-09 interaction.** (carried from seed §A5)

Right now each provider config carries `model` and `synthesis_model`; C-09 lists `model`/`synthesis_model`/`embedding_model` as the required provider fields.

The question: does the vision model get its own `vision_model` field, or is it folded into an existing one (e.g. routing the `"vision"` task to `synthesis_model` if that model is multimodal)?

**If a dedicated `vision_model` field:** explicit, lets the vision model differ from the text models; a small same-shape addition to each provider config — but it grows the C-09 "required fields" list, which should be noted in the constraint.
**If reuse `synthesis_model`:** no new field, but couples vision capability to whatever the synthesis model happens to be, and breaks if that model is text-only.

Recommendation: add a dedicated `vision_model` field — explicit and decoupled, consistent with per-task model routing. Note the C-09 field-list growth in the spec. Not a blocker.

**OQ-7G — Vision route + PDF describability (BUILD-BLOCKING for the describable set).**

> **PARTIALLY RESOLVED (human decision, 2026-06-14, build-pipeline review gate):** The vision route is **AgentBase MaaS (platform LLM, OpenAI-compatible)** — owner's choice, for cost control, OVER the design's Anthropic-SDK recommendation. Consequences now LOCKED as inputs to research/spec:
> - `describe_image` must target the **MaaS OpenAI-compatible `image_url` content-block shape**, not the Anthropic image block.
> - **Research MUST verify, before any vision code is written, that AgentBase MaaS (a) accepts image input at all, and (b) accepts text-less PDF input.** This is the build-blocking open part.
>   - If MaaS **cannot do vision at all** → HARD STOP, escalate to the human (the route choice must be revisited).
>   - If MaaS does **images only** → drop `application/pdf` from the describable set (config edit); text-less PDFs fall to store-only.
>   - If MaaS does **images + PDF** → keep `application/pdf` in the set.
> - Keep the describable set config-editable so the PDF outcome is a config change, not code.
>
> _Original deferred text retained below for context._

Right now the design assumes a Claude vision model via the Anthropic SDK and keeps `application/pdf` in the default describable set.

The question: what is the actual vision route (direct Anthropic SDK vs AgentBase MaaS OpenAI-compatible) and does the configured vision model accept a text-less PDF, or only raster images?

**If direct Anthropic, PDF-capable model:** the `describe_image` Claude override sends an image/PDF block; text-less PDFs are describable; keep `application/pdf` in the set.
**If MaaS / image-only model:** the call shape is `image_url` not an Anthropic block (different `describe_image` implementation), and/or text-less PDFs cannot be described and must fall to store-only — drop `application/pdf` from the describable set (a config change, no code change).

Recommendation: verify the route + model at the research step before implementing `describe_image`; keep the describable set config-editable so the PDF decision is a config edit. Blocks the vision implementation, not the blob-store work.

**OQ-7H — Storage-client library + bucket topology (HUMAN-GATED, before any install).**

> **RESOLVED (human decision, 2026-06-14, build-pipeline review gate):** **`boto3` (sync, thread-wrapped) + a separate `KMS_BLOB_*` bucket inside VNG Object Storage** (the same VNG service Litestream already uses — no new vendor). Matches the design recommendation. `boto3` is the one new dependency, approved in principle — **the actual `pip`/`pyproject.toml` install still happens at implementation time, not before.** Research must verify VNG Object Storage access details (endpoint, bucket creation, access-key env vars).
>
> _Original deferred text retained below for context._

Right now no S3 client exists; the design recommends `boto3` + a separate `KMS_BLOB_*` bucket, both behind a blob-store helper.

The question: which client (`boto3` sync / `aioboto3` async / `minio`) and which bucket (reuse the Litestream backup bucket under a prefix vs a separate `KMS_BLOB_*` bucket)?

**If `boto3` + separate bucket (recommended):** most reliable against VNG's S3 dialect, matches the project's sync-in-async-via-thread pattern, keeps blobs out of Litestream's lifecycle — at the cost of one more bucket + credential set.
**If reuse the backup bucket / `aioboto3` / `minio`:** fewer credentials or native async or smaller footprint — but entangles blob lifecycle with DB backups, or adds a heavier/less-tested dependency.

Recommendation: `boto3` + separate `KMS_BLOB_*` bucket. **This must be confirmed by the human before anything is installed (reversibility rule / global contract §4). Nothing is installed in this design.** Blocks the blob-store implementation.

**OQ-7I — Audit outcome name for a successful describe.** (carried from seed §A5)

Right now the success outcome string is undecided (`DESCRIBED` vs reuse `CAPTURED`); `core.audit.write` accepts any string.

The question: distinct `DESCRIBED` outcome or reuse the text path's `CAPTURED`?

**If `DESCRIBED`:** lets the briefing / future UI distinguish a vision describe from a text capture in the audit log.
**If `CAPTURED`:** one fewer outcome vocabulary entry; binary and text look identical in the log.

Recommendation: use a distinct `DESCRIBED` for observability; the skip/failure entries already need their own reason strings (too-big / unsupported-type / vision-failed). Not a blocker.

---

## ADR decision (gate check)

The **blob lifecycle + content-addressed key + reference-counted delete** contract passes all three ADR gates:
1. **Hard to reverse** — the content-addressed key scheme and the "delete-when-last-reference-gone" rule become part of the storage contract and the daemon↔cloud delete semantics; changing them later means re-coordinating the key scheme, the delete path, and every blob reader (Phase 9).
2. **Surprising without context** — a future reader sees one stored object shared by two document rows, and a delete that sometimes keeps the blob, and reasonably asks "is that a bug?"
3. **Real trade-off** — genuine alternatives existed (path-keyed blobs; never-delete; per-row delete) and were chosen against for specific reasons.

**Recommendation: extend ADR-0015 with an amendment rather than write a new ADR.** ADR-0015 already owns "the cloud stores the blob + vision-describes it" and explicitly **assigns the blob storage shape + key scheme to the Phase 7 design step** (its Consequences: "The Phase 7 design step decides the blob storage shape and key scheme"). The three resolved items here — (a) two-columns-on-`documents` reference shape, (b) content-addressed key, (c) reference-counted delete — are the concrete answers ADR-0015 deferred to this step, not a new architectural direction. Recording them as an **ADR-0015 amendment** (mirroring how ADR-0013/0015 were folded into the 7_capture_refactor design as an Amendment) keeps the blob contract in one place and avoids a near-duplicate ADR. If the project prefers one ADR per concrete contract, ADR-0016 is the alternative — but the amendment is recommended because the lifecycle is a *consequence* of ADR-0015's decision, not an independent one.

_This is a recommendation; the actual amendment/ADR write is a follow-up step (the skill's ADR offer), not done inline here._

---

## ADR references

- **ADR-0014** — store-raw-first / AI-failure = store-anyway. 7B extends it to blobs: store blob + reference first, vision-describe second, failure = store-anyway-and-audit.
- **ADR-0015** — visual/binary content: cloud stores the blob + vision-describes. This design resolves the blob storage shape + key scheme ADR-0015 assigned to Phase 7, and adds the reference-counted-delete lifecycle (recommended as an ADR-0015 amendment).
- **ADR-0013** — `content_hash` over raw file bytes; the content-addressed key reuses that fingerprint. Reconcile ownership (Phase 6 scanner) is unaffected by 7B.
- **ADR-0012** — additive rearchitecture: migration 009 is additive; 7B touches only capture's binary branch and adds `storage/blobs.py` — it deletes no shared module.

---

## Options explored (summary)

- **Decision 1:** chosen two columns on `documents` (Option A); `blobs` table (Option B) deferred to OQ-7D.
- **Decision 2:** chosen content-addressed key (Option A); path-based (Option B) rejected for move-instability.
- **Decision 3:** chosen `describe_image` default-raising method (Option A); `complete()` overload (Option B) rejected for blast radius.
- **Decision 4:** recommended `boto3` + separate bucket; `aioboto3`/`minio` and bucket-reuse remain on the table (OQ-7H, human-gated).
- **Decision 5:** chosen count-then-delete best-effort (Option A); never-delete (B) is the failed-delete fallback; per-row delete (C) rejected.
- **Decision 6:** chosen config-driven describable set + fork inside the binary branch; handler-side routing rejected.
- **Decision 7:** chosen config size cap → store + skip vision + audit, separate from the extraction cap.

---

## Next step

Design doc written. Run `/architecture-docs` (or `/update-arch-story`) to fold the 7B binary branch into the main architecture designs, then run `/writing-detailed-specs` to structure the chosen options into build steps. Before any install or vision code: resolve **OQ-7H** (storage client + bucket — human sign-off) and **OQ-7G** (vision route + PDF describability) at the research step.
