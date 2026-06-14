# Phase 7 — Capture Refactor: Design

_Status: Design (codebase-design-analysis output). Feeds `/writing-detailed-specs`._
_Requirements (locked, signed off): `docs/0_draft/phase7/phase7_capture_refactor_requirements.md`_
_Single source of truth for direction: `docs/0_draft/cloud_native_rearchitecture.md` §3, §5, §7, §15.1; `docs/roadmap/roadmap.md` "Phase 7 — Capture Refactor"._
_Behavior-inventory ID prefix: **P7-CAP** (entries `P7-CAP-01…13` in `docs/system_behavior/behavior_inventory.yaml`)._
_Reader mode: non-coder-readable (default). Plain English leads every section; code references are parenthetical anchors for engineers._
_**Amended 2026-06-13** to fold in two newly-accepted ADRs (ADR-0013, ADR-0015) — see "## Amendment — ADR-0013 + ADR-0015 (visual/binary capture)" near the end. The original text-path content below is preserved unchanged; the Q1 diagram has been replaced with a two-input fork version (the old text-only Q1 is superseded)._

---

## In plain terms (the whole thing in a paragraph)

Today, "capture" takes a file off the user's disk, asks the AI to summarize it, and then **writes new files back into the user's folders** (a summary note, hidden sidecar files, sometimes moving the original). In the cloud model, the user's files are sacred and untouched — a small program on the laptop (the **daemon**) reads each file, pulls out its text, and sends that text up to the cloud. Phase 7 rewrites the cloud's capture so it does three jobs and nothing else: **save the text safely, ask the AI for a structured summary, and record it all in the central database.** It never writes to the user's folders, never moves files, and never decides which project a file belongs to (that is a later phase's job). The guiding rule is "the user's content is sacred; the AI summary is a nice-to-have that may arrive late" — so we save the raw text the instant it arrives, and if the AI is down we still keep the document and just try the summary again later.

---

## Cast of characters (symbols used 3+ times)

| Name | Plain-English role | Code anchor |
|---|---|---|
| Capture Receiver | Front door: validates an upload and checks "have we seen this exact content?" | new entry function in `pipelines/capture.py` |
| Document Store | The central table of documents (text, summary, title, fingerprint) | `storage/documents.py` (`documents` table) |
| Housekeeping AI | The behind-the-scenes cloud AI that writes the summary | `llm/provider.py` via `get_provider("capture", ...)` |
| structured summary | A summary with named sections (overview / key points / decisions / action items / people) | stored in `documents.summary` |
| `full_body` | The complete extracted text of a file, kept in the database | `documents.full_body` column (migration 008) |
| content fingerprint | A short code computed over the file's **raw bytes** (ADR-0013), sent by the daemon; the cloud stores/compares it as-is to detect duplicates | `content_hash` |
| Audit Log | The tamper-evident record of every AI decision and failure | `core/audit.py` → `storage/audit_log.py` |
| `upsert_from_upload` | The save-or-update routine the upload endpoint already calls | `storage/documents.py::upsert_from_upload` |
| context injection (dormant) | Optionally briefing the AI with known facts before it summarizes | `storage/knowledge_entries.py` query helpers |
| Blob Store (new) | Cloud object storage holding the raw bytes of binary/visual files; the app needs a new write client | VNG Object Storage (S3-compatible) + new blob-store helper |
| Vision Describer (new) | The picture-reading AI mode that turns an image/graph into searchable words | new vision call via `get_provider("vision", ...)` (Amendment §A5) |
| blob reference (new) | A pointer on the document row to where the raw file lives in object storage (not the bytes) | new nullable column on `documents` (migration 009, Amendment §A4) |

Glossary of one-off terms is inline where they appear.

---

## Decision

**Chosen: Option A — Store-Raw-First two-step, extending `upsert_from_upload`, with a thin new pipeline entry.** The upload endpoint stores the raw text immediately (the user's content is safe the moment it arrives), then a single Housekeeping-AI call produces a structured summary that is attached to the *same* row; if the AI fails the document survives, the failure is audited, and success is still returned. This is chosen because it is the only option that honours the locked "content is sacred / summary may arrive late" contract without inventing new schema, and it reuses the save-or-update routine that the live upload endpoint already calls.

---

## Q1 Diagram — what happens inside (the chosen option, amended for the two-input fork)

_This replaces the original text-only Q1. The text branch (2a → 3a) is the original chosen-option flow, unchanged; the binary/visual branch (2b → 3b) is the ADR-0015 addition. Both share the store-raw-first safety contract and converge on the same "Did the AI succeed?" decision, attach point, and classify trigger. The amendment section near the end of this doc explains the binary branch in full._

```
# Phase 7 Cloud Capture — What Happens Inside (Two-Input Fork: Text vs Binary/Visual)
Scope: Shows what happens when the daemon uploads ONE item to the cloud — either
       extracted text, or raw file bytes it could not extract text from.
       Does NOT cover daemon-side extraction, fact-extraction (later phase),
       or move/delete events.

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

   Daemon uploads ONE item:
   (extracted text) OR (raw file bytes)
   + folder path + filename + size
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
            ▼          "Did this upload
   ┌────────────────┐  include extracted text?"
   │ Stop early —   │      ┌──────┴───────────┐
   │ already        │     TEXT              BINARY / VISUAL
   │ processed.     │   (has text)         (raw bytes, no text)
   │ No AI runs     │      │                    │
   └────────────────┘      ▼                    ▼
              ┌────────────────────┐  ┌────────────────────────────┐
              │ 2a. Store Raw First│  │ 2b. Store Blob First       │
              │ Save full text +   │  │ Save raw bytes to object   │
              │ filename + size +  │  │ storage; save a row that   │
              │ fingerprint; EMPTY │  │ holds a REFERENCE to the   │
              │ summary (safe now) │  │ blob + filename + size +   │
              └─────────┬──────────┘  │ fingerprint + file type;   │
                        │             │ EMPTY summary (safe now)   │
                        ▼             └─────────────┬──────────────┘
              ┌────────────────────┐                │
              │ 3a. Housekeeping   │                ▼
              │ AI Summarizer      │  ┌────────────────────────────┐
              │ One AI call →      │  │ 3b. Vision Describer       │
              │ structured summary │  │ One vision-AI call looks   │
              │ + descriptive title│  │ at the image/graph →       │
              └─────────┬──────────┘  │ searchable text description│
                        │             │ + descriptive title        │
                        │             └─────────────┬──────────────┘
                        └───────────┬───────────────┘
                                    │
                            "Did the AI succeed?"
                          ┌─────────┴──────────┐
                         YES                   NO
                          │                     │
                          ▼                     ▼
        ┌────────────────────────────┐  ┌────────────────────────────┐
        │ 4. Attach Description      │  │ Record failure in Audit    │
        │ Update SAME row with       │  │ Log; leave summary empty;  │
        │ summary/description +      │  │ STILL return success.      │
        │ title; index for keyword + │  │ Text row searchable now;   │
        │ meaning search; write      │  │ blob safe in storage but   │
        │ success to Audit Log       │  │ not searchable until its   │
        └────────────┬───────────────┘  │ description lands (retry)  │
                     │                   └────────────────────────────┘
                     ▼
        ┌────────────────────────────┐
        │ 5. Classify Trigger        │
        │ Log one line: ready for    │
        │ later fact-extraction      │
        │ (no-op stub today)         │
        └────────────────────────────┘
```

```
Simplified: "Gather Context (dormant)" is folded into the Housekeeping AI Summarizer box
            (an optional pre-step of the same stage; binary/visual uploads may skip it).
            On AI failure the Classify Trigger still logs but adds nothing actionable, so it
            is omitted from the failure branch.
```

---

## Guardrail Checklist (from `/guardrail-check Review`)

Domains touched: Write Safety, DB Integrity, LLM & Providers, Architecture, Testing. Skipped: Async & CLI (the one dev `kms capture` entry keeps the existing `asyncio.run` pattern; C-10/C-11 unaffected).

- [ ] **C-01 · Vault is source of truth; documents table is index only** — Phase 7 **flips** this: DB becomes source of truth, `full_body`/summary live in `documents`, no vault writes. This phase is where C-01 is rewritten (rearch §5/§12, ADR-0012). Must be explicit, not silent.
- [ ] **C-02 · `updated_by_human=1` means hands off** — old `write_note` path retires; the new DB attach-summary step must not clobber a human-overridden row (concept shifts to DB).
- [ ] **C-03 · `write_note` is a pure writer** — `write_note` retires here; the attach-summary UPDATE must not blank columns the raw-store step set.
- [ ] **C-04 · `PRAGMA foreign_keys=ON`** — reuse `storage/db.py::get_connection`; add no new connection factory.
- [ ] **C-05 · Schema changes via versioned .sql only** — locked decision 6: **no new schema**. Verify nothing adds a column.
- [ ] **C-06 · Thresholds in config, not code** — capture is summarize-only; no confidence gate, no float literal.
- [ ] **C-07 · Prompts as YAML** — the structured-summary prompt (and any context-injection prompt) live in `prompts/*.yaml`.
- [ ] **C-08 · `get_provider(task, CONFIG)` factory** — summarizer calls `get_provider("capture", CONFIG.main)`.
- [ ] **C-12 · Result types at module boundaries** — new entry + stages return `Success`/`Failure`; AI-failure-store-anyway returns `Success`.
- [ ] **C-13 · Audit log non-negotiable** — `CAPTURED` on success **and** a failure entry on AI failure, both via `core.audit.write`.
- [ ] **C-15 · No MCP tool before its pipeline** — classify-trigger is a log stub, not an MCP tool; no MCP tool added in Phase 7.
- [ ] **C-17 · No module-scope CONFIG in tests** — new tests lazy-import CONFIG / pass explicit `db_path`.

---

## The three deferred-to-design decisions (resolved)

### Decision 1 — Structured summary storage shape: **rendered Markdown** in `documents.summary`

**In plain terms:** The summary has named sections (overview, key points, decisions, action items, people mentioned). We store it as one human-readable block of Markdown text in the existing summary field — the same field search already reads — rather than as a machine-shaped data blob. This keeps the document instantly searchable by its own summary, keeps the later briefing phase able to read it as-is, and needs zero schema change.

- The summary column is plain TEXT today; the full-text search index pulls `summary` straight into its searchable "summary" column (`retrieval/keyword.py::index_keywords`, `notes_fts` column order `vault_path, title, summary, body`). A Markdown block flows in unchanged and stays searchable word-for-word.
- The meaning-search embedding also folds the summary into the text it encodes (`retrieval/embeddings.py::_build_context_text` appends `summary`). Markdown prose embeds well; a raw JSON blob (braces, quotes, field names) would pollute the embedding with structural noise.
- Phase 8 / briefing reads `documents.summary` as a string; Markdown is directly presentable with no parse step.

**Why not a JSON blob:** A JSON blob (`{"overview": "...", "key_points": [...]}`) would let downstream code address sections precisely, but (a) it degrades both search indexes (the index would store literal `{`, `"overview"`, etc.), (b) it forces every reader to parse, and (c) the requirements explicitly say no schema change and the column is already consumed as text. If section-level addressing is ever needed, the summary can be re-parsed from its Markdown headers, or a JSON-shaped column added in one migration then. Captured as **OQ-7A**.

- The Housekeeping AI is instructed (in the YAML prompt) to emit fixed Markdown headers (`## Overview`, `## Key points`, `## Decisions`, `## Action items`, `## People mentioned`) so the shape is predictable for human readers and for any future parser.

### Decision 2 — Descriptive title generation: **yes, the summarizer produces a descriptive title** that overrides the filename stem

**In plain terms:** A file called `IMG_2087.pdf` or `notes-final-v3.docx` is a bad title for search. The AI already produces a clean descriptive title during summarization; we keep that title as the document's title so search ranking and result cards are meaningful. This continues exactly what Phase 3 Session B established ("descriptive title at capture").

- The title flows into both search paths: it is the first field the embedding encodes (`_build_context_text` → `"title: {title} | ..."`) and the dedicated `title` column of the full-text index (`index_keywords(..., title=...)`, `notes_fts` column 1). It is also stored in `documents.title`.
- The save-or-update routine already accepts an optional title and falls back to the filename stem when none is given (`upsert_from_upload(..., title=...)` → `Path(vault_path).stem`). The two-step flow uses that fallback for the **raw-store** step (no AI yet, so stem) and overrides it with the AI title on the **attach-summary** step.
- On AI failure the title stays as the filename stem — acceptable, since the document is still findable by its full text and the title is corrected on a later summary retry.

### Decision 3 — `upsert_from_upload` extension + old-path retirement

**In plain terms:** Saving happens in two beats: first "store the raw text" (already exactly what the upload endpoint does today), then "attach the summary" once the AI returns. We add one small sibling routine for the second beat. The old save routine that the dead capture used (`upsert(WriteOutcome)`) loses its last caller in this phase, so it retires.

- **Raw-store beat** = the existing `upsert_from_upload(vault_path, extracted_text, content_hash, original_filename, file_size_bytes, title=stem)`. It already does the three-way fingerprint dance (insert / skip-identical / update-changed) — which is exactly the front-loaded dedup of locked decision 1. **No change needed to it.**
- **Attach-summary beat** = a new sibling, e.g. `attach_summary(vault_path, summary, title, db_path)`, that UPDATEs `summary` and `title` (and `updated_at`) on the row identified by `vault_path`, touching nothing else (preserves `full_body`, `content_hash`, etc. — respects C-03's spirit). It returns `Result[int]` (the row id) per C-12.
  - Search indexing (`index_embedding`, `index_keywords`) is called best-effort **after** the attach succeeds — mirroring today's capture wiring, but keyed on `vault_path` instead of a `WriteOutcome`.
- **Old-path retirement:** `documents.upsert(WriteOutcome)` and `documents.replace_path(WriteOutcome)` have `pipelines/capture.py` as their only live caller (verified: `upsert` called at `capture.py:1062,1268,1416`; `replace_path` at `capture.py:516,986`). When old capture dies in this phase they lose their last caller. Retire both, and drop the now-dead `WriteOutcome` import from `documents.py` and `_derive_title(outcome)` helper. **Keep** `upsert_from_upload`, `get_by_path`, `all_paths`, `delete_by_path`, `rename`, `filter_paths`, `update_batch_id` — all have other live consumers (api.py, retrieval, indexer). `WriteOutcome` itself lives in `vault/writer.py`; its full module deletion is shared with Phase 6 (watcher) per ADR-0012 — Phase 7 stops *importing* it but does not delete `vault/writer.py`.

---

## Implications

- **The user's folders become read-only to the system; everything the AI produces lives in the database.** This is the moment Phase 7 flips the project's oldest rule (C-01). After this phase, no part of capture writes a file, a frontmatter block, or a sidecar note.
  - Removes all `write_note`/`move_note`/`WriteOutcome`/`replace_path(WriteOutcome)`/sibling-`.md` usage from `pipelines/capture.py`; the new pipeline writes only DB rows + search-index rows. C-01/C-03 must be rewritten in `CONSTRAINTS.md` as part of this phase (flagged, not done here).
- **Duplicate uploads stop costing money.** Because the fingerprint check runs before the AI, re-uploading an unchanged file never triggers a paid summary call.
  - The dedup is the existing `upsert_from_upload` skip-identical branch (`documents.py:218`), but the new flow must check/short-circuit **before** invoking `get_provider("capture",...).complete(...)`, not after.
- **A document is searchable the instant its text arrives, even if the AI is slow or down.** Full text lands in the database first; keyword search works immediately; the summary (and meaning-search embedding, which needs the summary+title) fills in on success or on a later retry.
  - `full_body` is indexed into `notes_fts` body column. The embedding currently folds in `summary`+`title`; on AI-failure there is no summary yet, so the embedding step is deferred to the retry (acceptable — keyword search covers the gap). See OQ-7B.
- **Capture no longer guesses a project or domain.** It stores the folder path verbatim and stops there; all project/domain reasoning moves to Phase 8.
  - Drop the `classify_step`, `apply_location_tags`, `_classify_auto_md_move`, `capture_folder` routing, and the `build_registry`/`ProjectRegistry` usage from capture. `documents.project` stays NULL at capture.
- **A guaranteed-empty knowledge base is a permanent production path, not a temporary one.** Every brand-new user starts with zero known facts, so the "no context found → summarize on text alone" path must be built and tested now, even though it lights up fully only in Phase 8.
  - The context-injection pre-step queries `knowledge_entries` (`query_by_entity` / `get_confident_and_pending`) and returns an empty brief gracefully; it must never turn an empty result into a failure. Pattern reference: `mcp_server/context.py` already degrades missing context files to "no block" rather than erroring.
- **The classify hand-off is deliberately nothing.** Phase 8 will find its own work by asking "which documents have produced no facts yet?" — and every Phase-7 document qualifies by definition — so building a queue or flag now would be scaffolding for a consumer that does not exist (C-15).
  - Classify trigger = one `logger.info(...)` line keyed on the document id. No table, no column, no marker.
- **Module-depth check.** `storage/documents.py` is a **deep** module (small interface — a handful of functions — over real SQL/transaction logic); the change *deepens* it slightly (one new `attach_summary` function) and *shrinks* it (deletes `upsert`/`replace_path`/`_derive_title`/`WriteOutcome` coupling). `pipelines/capture.py` goes from a 2241-line module to a short orchestrator — the deletion test passes loudly: removing the old logic does not reappear anywhere (the vault-writing complexity genuinely disappears with the vault-writing responsibility). The new `attach_summary` is **not** a speculative seam: it has one real adapter (the new pipeline) and exists to honour the two-beat data-safety contract, not to abstract.

---

## Known tradeoffs (what we give up by choosing Option A)

- **A row can sit summary-less for a while.** Between the raw-store beat and a successful summary (or after an AI failure, until retry), the document has full text but no structured summary and no meaning-search embedding. We accept this because keyword search still finds it and the content is safe — the alternative (reject-on-failure) loses the user's content on a transient model outage.
- **Two DB writes per capture instead of one.** Store-raw-first then attach-summary is two transactions, not one combined write. We accept the extra write because it is what makes the content survive an AI failure — the safety is the whole point.
- **Markdown summary is not machine-addressable per-section.** We cannot cheaply ask "give me just the action items" without re-parsing. Accepted because search + briefing read the summary as text, and the requirement forbids schema changes.

---

## Risks (for research / planning / implementation to watch)

- **The dedup short-circuit must move ahead of the LLM call.** Today `upsert_from_upload` skips identical content *after* being called; the new pipeline must perform the fingerprint check (via `get_by_path` or a dedicated peek) **before** the summarizer (and, for the binary path, before the vision call **and** before storing the blob), or the "no summary/description for duplicates" guarantee (P7-CAP-01, extended by P7-CAP-13) silently fails. Verify with a test that asserts the provider is never invoked on a duplicate. **Note (ADR-0013):** the fingerprint compared here is over **raw file bytes**, supplied by the daemon — the cloud compares what it is sent and must never recompute a hash from extracted text. A cosmetic binary change can flip the hash and trigger a re-capture (accepted as rare/self-correcting per ADR-0013). See Amendment §A2.
- **[ADR-0015] Object-storage WRITE path does not exist in the app — it must be built.** Litestream (the only S3 consumer today) only mirrors the database file; the app has no blob-put client (no boto3/aioboto3/minio in `src/` or `pyproject.toml`). The binary path needs a new S3-compatible client against the VNG bucket, wrapped behind a small blob-store helper with a local-filesystem test stub. New dependency — confirm before installing. See Amendment §A6.
- **[ADR-0015] Vision/multimodal provider capability does not exist — it must be added.** `LLMProvider.complete(system, user)` is text-only (both params are strings); there is no image-input path. A new image-capable call (e.g. `describe_image`) plus a `"vision"` task, a `vision: Provider` field, and a vision model name in config are required. See Amendment §A5.
- **[ADR-0015] A new additive migration (009) is required** for the blob-reference shape (two nullable columns on `documents`: a blob reference + `mime_type`). Confirm it follows the version-pin test update convention (every new migration bumps the prior migration's version-pin assertion — see CLAUDE.md "Every new migration breaks the previous migration's version-pin test"). See Amendment §A4.
- **[ADR-0015] Privacy / data-residency shift.** User image/document bytes now leave the laptop and live in cloud object storage — a conscious change from "raw files stay local, only text goes up." Acceptable for the single-tenant personal deployment; a future sensitive/multi-tenant scenario may need a per-vault opt-out. Must be a stated property, not a silent one. See Amendment §A7.
- **Embedding depends on the summary.** `index_embedding` folds `summary`+`title` into the encoded text; on AI failure there is no summary, so meaning-search will be weak/absent until retry. Confirm the retry job (deferred) re-runs embedding, and that keyword search alone is acceptable in the interim (OQ-7B).
- **`apply_location_tags` previously added `domain/<D>` tags from the folder path.** Removing it means capture no longer derives any domain tag. Confirm Phase 8 is the sole owner of folder-signal interpretation (rearch §7 design note) and that nothing downstream still expects a capture-time domain tag.
- **Audit `source_ids` shape changes.** Old audit rows used the vault path as `source_id`. With per-path DB identity that still works, but confirm the `CAPTURED` and failure audit entries use a stable identifier (vault_path or document id) that Phase 8 briefing can resolve.
- **`scan_capture` and `capture_folder` callers.** `cli/main.py` and any tests call these; they retire with old capture. Research must enumerate every caller (grep `capture_file`, `capture_folder`, `scan_capture`) and the dev `kms capture` entry must be re-pointed at the new in-process pipeline function.
- **`tests/test_core/test_pipeline_phase1.py::test_pipeline_has_no_heavy_imports`** greps source for `vault.` — the new capture must not import `vault.` types at module scope (use `Any` / DTOs). Carry this constraint forward.

---

## Amendment — ADR-0013 + ADR-0015 (visual/binary capture)

_Added 2026-06-13. Two ADRs accepted after the original sign-off expand Phase 7's scope. They are locked upstream inputs — not re-opened here. The text-path content above stands as written; this section adds the second input shape and corrects two fingerprint/ownership notes. Reader mode stays non-coder-readable: plain English leads, code refs are parenthetical._

### In plain terms

The cloud used to assume every upload was **text** the laptop helper had already pulled out of a file. But some files have no useful text — a photo, a scanned document, and especially a **chart or graph** someone drops into a folder. The laptop helper cannot understand a picture (it has no AI), so for those files it now sends the **raw file itself** up to the cloud. Phase 7 teaches the cloud to handle this second shape: **keep the actual file in cloud file storage, remember where it lives, and ask a picture-reading AI to describe it once in words** — so the chart becomes findable by search just like a text note. The safety promise is identical to the text path: **save the file first (it is safe the moment it lands), describe it second (the description may arrive late if the AI is busy).** A second, smaller correction (ADR-0013): the duplicate-check fingerprint is now computed over the file's **raw bytes** by the laptop helper, and the cloud simply trusts and compares whatever it is sent — it must never recompute a fingerprint from the extracted text.

### A1 — Two input shapes at the front door (the fork)

The Capture Receiver now branches on a single question: **did this upload include extracted text?**

- **Text upload (unchanged):** extracted text is present → the existing Store-Raw-First two-step runs exactly as the original design describes (store text → summarize → attach). Nothing in the text path changes.
- **Binary/visual upload (new):** raw bytes + file type ("mime") + a raw-byte fingerprint + size + folder path arrive, with **no** extracted text → the new binary branch runs (store blob + reference → vision-describe → attach description).

Both branches reuse the **same store-raw-first data-safety contract** (ADR-0014): persist the user's content first (content is safe), enrich second (may fail → store-anyway + audit), and converge on the same "Did the AI succeed?" decision, the same attach step, and the same classify-trigger stub.

- **Where the fork lives in code (the seam):** the `/api/upload` wire format that *accepts* raw bytes is Phase 6's job (ADR-0015 slice A1). Today the endpoint hard-rejects a body with no extracted text (`mcp_server/api.py::upload_handler` returns HTTP 400 — "extracted_text is required"). Phase 7 does **not** open that gate; it builds what the cloud *does* once Phase 6 lets bytes through. The new pipeline entry function branches internally on "text present vs bytes present," so the daemon-facing seam stays a single endpoint.
- **Binary idempotency** is the same mechanism as text: the front-loaded fingerprint check (over raw bytes) runs **before** any AI. A re-uploaded identical image is detected and skipped — the vision model never runs, no blob is re-stored. (Consistent with locked decision 1 / P7-CAP-01, now extended to bytes.)

### A2 — content_hash is over RAW FILE BYTES, not extracted text (ADR-0013)

**In plain terms:** The duplicate-check fingerprint is produced by the laptop helper from the file's raw bytes. The cloud stores and compares exactly what it receives; it must never derive its own fingerprint from the text it was sent.

- The cloud-side dedup (`upsert_from_upload` skip-identical branch, `documents.py:218`) already compares the stored `content_hash` to the one supplied on the upload — it does **not** recompute anything. So **no cloud code change is required** for the hash-basis switch; the only adjustment is to descriptions/wording in this design and the dedup risk note (corrected below). This holds for both the text path and the binary path: in both, the daemon hashes raw bytes.
- **Consequence the design must state:** a cosmetic binary change (re-saved image, different timestamp metadata) can flip the raw-byte hash and trigger a re-capture (and a fresh vision call). ADR-0013 accepts this as rare and self-correcting.

### A3 — `pipelines/reconcile.py` ownership corrected (ADR-0013)

**In plain terms:** An earlier worry was that the old 7-stage reconcile had no home in the new world. That is now resolved upstream and is **not** a Phase 7 risk.

- ADR-0013 assigns the vault↔cloud reconcile to the **Phase 6 startup scanner** (a three-way disk ↔ local-cache ↔ cloud compare). Phase 7 neither ports nor owns `pipelines/reconcile.py`. Any earlier risk-note language treating reconcile as "unassigned" is retired by this clarification; it does not belong on Phase 7's risk list.

### A4 — Blob storage shape + key scheme (decision — ADR-0015 assigns this here)

The blob is the user's actual file bytes; it lives in cloud file storage (the same VNG Object Storage / S3-compatible bucket the database backups already use). The `documents` table must hold a **reference** to that blob — never the bytes themselves. Two questions: **(1) where does the reference live** (a column on `documents` vs a separate table), and **(2) what is the storage key** (the raw-byte fingerprint vs the folder path).

**Verified starting facts (read in code, 2026-06-13):**
- The `documents` row (`DocumentRow`, `storage/documents.py:28`) has **no** column for a blob reference and **no** column for file type ("mime"). Today it carries `full_body`, `original_filename`, `file_size_bytes`, `content_hash`, `summary`, `title` — all text/metadata.
- Therefore **any** reference shape needs a **new additive migration** (next number `009`), because schema changes must land as a versioned `.sql` file, never in-code (C-05; ADR-0002). This is additive and ADR-0012-clean.

**Option A — reference column(s) on `documents` (Recommended).**
- **What this means:** One row per captured item, text or binary. A binary row simply also carries "where its file lives" and "what type it is." Search, audit, and the classify-trigger all already key on the document row — a binary is just a document whose body is a description and whose bytes live elsewhere.
- **Approach:** Migration 009 adds two nullable columns: a blob reference (the object-storage key/locator) and a `mime_type`. NULL on every existing and every text row; populated only for binary rows. `DocumentRow` gains the two fields; `_row_from_sqlite` reads them with the same `if "col" in row.keys()` guard the other late-added columns use (`documents.py:62-76`). `upsert_from_upload` (or a sibling `upsert_blob_from_upload`) writes them on the store-blob-first beat.
- **Module depth:** Deepens the already-deep `storage/documents.py` by two columns and (at most) one sibling write function — no new module, no new table to join. Deletion test: removing the columns would re-scatter "where is this file" into callers — they earn their keep.
- **Why recommended:** Smallest surface that satisfies the contract; one identity (the document row) for text and binary alike; reuses every existing reader (search, audit, retrieval) unchanged; one additive migration.

**Option B — a separate `blobs` (or `attachments`) table keyed by document id.**
- **What this means:** Binary file locations live in their own table, linked back to the document row.
- **Approach:** Migration 009 creates a `blobs` table (`document_id` FK, `object_key`, `mime_type`, `size`); the binary branch inserts there after creating the `documents` row.
- **Module depth:** Introduces a new table and a join for every reader that wants the blob — a **new seam with only one adapter today** (the binary branch). Speculative unless multiple-blobs-per-document is foreseen (it is not — one file = one blob = one row).
- **Why not now:** Adds a join and a table for a one-to-one relationship that two columns model directly; pays complexity for flexibility nothing in Phase 7 or Phase 9 has asked for. Captured as a future option in OQ-7D.

**Key scheme — key the blob by the raw-byte fingerprint (Recommended), not the folder path.**
- **What this means:** The object-storage filename for a blob is derived from its content fingerprint, so identical bytes map to one object and moving the file (which changes its path) never orphans the blob.
- **Why:** The fingerprint is stable across moves (the daemon reports a move as a path rename — `rename`, `documents.py`), already unique per content, and matches the front-loaded dedup. A path-based key would re-key (and re-upload) the blob on every move and collide if two files share a path after a rename. The reference column still records *which* document points at the object, so per-path identity (locked decision 7) is preserved at the row level while the bytes are de-duplicated at the object level. (This is a content-addressed object key, distinct from row identity — it does not reintroduce the rejected "content-reuse-by-hash across paths" summary-sharing; each path still gets its own row and its own description.)

> **Recommended: Option A (two nullable columns on `documents`) + content-fingerprint object key.** It is the only shape that adds no join, reuses every existing document reader, and survives file moves — at the cost of one additive migration.

### A5 — Vision-describe: extend the summarizer to branch text-vs-vision (decision)

**In plain terms:** The same "Housekeeping AI" stage gains a second mode: for a picture it calls a picture-reading model that writes a few sentences describing what the image/graph shows. That description is what makes the visual findable by search.

**Decision: one summarizer stage that branches by input shape (text → summarize prompt; binary/visual → vision prompt), NOT a separate top-level pipeline.** Both modes do the identical downstream work — produce text, attach it to the row, index it, audit it — so a sibling "describer" pipeline would duplicate the attach/index/audit wiring for no gain. The branch is a single fork inside the enrichment stage; the deletion test passes (removing the branch would re-duplicate attach/index logic in two places).

**What exists vs what must be added (verified in code, 2026-06-13):**
- **The provider abstraction is text-only today.** Every provider implements `complete(system: str, user: str)` — both parameters are strings (`llm/provider.py:40`; `llm/claude_provider.py:51` sends `messages=[{"role":"user","content": user}]`, a plain string). **There is no way to pass image bytes through the current interface.** A vision call MUST be added.
  - **Smallest honest extension:** add one optional image input to the provider contract — e.g. a new `describe_image(system, image_bytes, mime_type)` method on `LLMProvider` (default-raises "not supported" so non-vision providers stay honest), implemented in `ClaudeProvider` by sending an image content block (the Anthropic SDK accepts an image block in the `content` list alongside text). This keeps `complete()` untouched and adds vision as a clearly-separate capability rather than overloading the text method. (Exact method shape is an implementation detail for the spec; the design fixes only that a new image-capable call is required.)
- **Per-task model selection is config-driven and ready to extend (C-08).** `get_provider(task, CONFIG.main)` (`llm/provider.py:54`) routes by task name; tasks are a fixed list (`Task` literal, `config.py:43-45`) and each maps to a provider via `ProvidersConfig.for_task` (`config.py:185`). Adding vision = add `"vision"` to the `Task` literal, add a `vision: Provider` field to `ProvidersConfig` (`config.py:179-183`), and add a vision-capable model name to `ClaudeConfig` (`config.py:202` — e.g. a `vision_model` field alongside `model`/`synthesis_model`). The describer then calls `get_provider("vision", CONFIG.main)` — never instantiating a provider directly (C-08 satisfied). _(Note: C-09 lists `model`/`synthesis_model`/`embedding_model` as required provider fields; adding `vision_model` is a natural same-shape addition — flag for the spec, see OQ-7E.)_
- **The vision prompt lives in YAML (C-07).** A new `prompts/describe_image.yaml` holds the system instruction ("describe this image/graph for search: what it shows, key figures, labels…") loaded via `PROMPTS["describe_image"]` exactly like every other prompt (`llm/prompt_loader.py`). No inline prompt strings.
- **The audit entry (C-13).** A successful describe writes a `DESCRIBED` (or reuse `CAPTURED`) audit entry; a vision failure writes a failure entry — both via `core.audit.write(...)`, mirroring the text path's `CAPTURED`/failure pair.

**What the description populates (resolved):** the vision description is written to **`summary`** (so it flows into both search indexes exactly as the structured Markdown summary does) **and** to **`full_body`** (so keyword search over body also matches it and `kms_inspect`-style readers have a textual body). The actual bytes never enter `full_body` — only the words. For a binary row there is no extracted text, so `full_body` = the description is the natural home; an empty description (vision failed) leaves both empty, and "needs-description" is derived from the **same** empty-summary signal as "needs-summary" (no new flag — consistent with locked decision 6 / ADR-0014).

### A6 — Object-storage WRITE path: must be BUILT (this is the riskiest finding)

**In plain terms:** The cloud already talks to cloud file storage — but only the **backup helper** does, and only to copy the database. The application itself has **no** way to write an arbitrary file (a blob) into that storage today. That write path must be built.

**Verified in code, 2026-06-13:**
- The only object-storage consumer is **Litestream**, a separate binary that replicates the SQLite database file to an S3-compatible bucket (`litestream.yml`; `scripts/start.sh`; credentials via `LITESTREAM_BUCKET` / `LITESTREAM_ENDPOINT` / `LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY`). Litestream **cannot** be used by the app to put a blob — it only mirrors the one database file.
- There is **no** `boto3`, `botocore`, `aioboto3`, `minio`, or any S3/object-storage client anywhere in `src/` or in `pyproject.toml` (grep verified; the only `blob` hits are sqlite-vec embedding byte-buffers in `retrieval/`, unrelated).
- **Therefore the app needs a new object-storage write client.** The cheap, consistent path: add an S3-compatible client (`boto3`, or `aioboto3` to stay async with the rest of the cloud) pointed at the **same** VNG bucket + credentials Litestream already uses (reuse the existing env vars, or add a parallel `KMS_BLOB_*` set so blob and backup buckets can differ). This is a new dependency — **list it and confirm before installing** (per the project's reversibility rule). The client is wrapped behind a tiny in-house "blob store" helper (one `put`/`get` interface) so the rest of the pipeline never speaks S3 directly — and so a local-filesystem stub can stand in for tests (the dependency is remote-owned; tests use a substitutable local adapter).
- **Migration need (confirmed):** YES — the chosen blob-reference shape (Option A) adds columns to `documents`, which requires **new migration `009`** (additive; C-05/ADR-0012-clean). If Option B were chosen it would instead be a new `blobs` table in the same migration. Either way, one new `.sql` file.

### A7 — Privacy / data-residency (ADR-0015 consequence — must be stated, not hidden)

**In plain terms:** Until now, raw files stayed on the laptop and only their text went to the cloud. With visual capture, the user's actual image/document **bytes** now leave the laptop and live in cloud storage. This is a deliberate, accepted property for the current single-tenant personal deployment — but it must be stated, and a future sensitive-vault or multi-tenant scenario may need a per-vault opt-out. (ADR-0015 "Privacy / data-residency shift.") Flagged here so the spec and the user own it consciously; captured as a risk row below.

### Slicing assessment (flag for the orchestrator — do NOT decide here)

Adding the visual path materially enlarges Phase 7: it introduces a **new external dependency** (an object-storage write client), a **new provider capability** (image-input LLM call), a **new migration** (blob-reference columns), and a **second prompt + audit pair** — on top of the already-HEAVY text rewrite. Each is independently testable, and the text path has **zero** dependency on the binary path (the fork is clean: text never touches the blob store or vision). That independence makes a split low-risk and natural.

**Recommendation (a flag, not a decision): split Phase 7 into 7A (text capture) and 7B (visual/binary capture).** 7A ships the text rewrite exactly as the original design specifies (no object storage, no vision, no new migration) and reaches a working, mergeable state — the M-milestone-relevant path. 7B adds the binary branch (migration 009, blob-store client, vision provider, describe prompt) on top. This keeps each slice within one context window, de-risks the new external dependency, and lets the text path ship and be demoed even if the object-storage/vision work slips. **This is surfaced as OQ-7F for the orchestrator/user to decide — it is not decided here.**

---

## Open questions

**OQ-7A — Summary storage shape if section-level access is later needed.**

Right now the structured summary is stored as one block of Markdown text in the summary field, which reads cleanly for humans and for search but cannot be sliced into individual sections without re-reading the text.

The question: if a future feature (e.g. "show me only the action items across all docs this week") needs to address summary sections individually, do we re-parse the Markdown headers, or migrate to a structured column?

**If re-parse Markdown:** zero schema change; a small parser keys off the fixed `## ` headers; brittle if the AI drifts from the header format.
**If add a JSON column:** one migration; precise section access; but two representations of the same summary to keep in sync, and the search-index pollution problem returns if anything indexes the JSON.

Recommendation: re-parse Markdown when/if the need is real. It avoids a schema change and a second copy of the data; the header format is prompt-enforced and testable. Not a blocker for Phase 7.

**OQ-7B — When does the meaning-search embedding get built on an AI-failed capture?**

Right now the meaning-search embedding is built from the summary + title, so a document whose summary failed has full-text (keyword) search only until its summary is retried.

The question: should the raw-store beat build a *provisional* embedding from `full_body` alone so meaning search works before the summary lands, or wait for the summary retry?

**If provisional embedding now:** meaning search works immediately even on AI failure; costs one embed per capture and a re-embed on retry; embedding of raw body may rank differently than the summary-based embedding.
**If wait for retry:** simpler, one embedding per document, consistent with today's "embedding includes summary" design; meaning search misses AI-failed docs until retry.

Recommendation: wait for retry (keyword search covers the interim, and the retry job is already the owner of "fill the missing summary"). Revisit if meaning-search coverage on failed captures proves important. Not a blocker; the retry runner is deferred anyway.

**OQ-7C — Identifier used in `sources` and audit `source_ids`: vault_path or document id?**

Right now audit rows key on the vault path. Phase 8 will record which documents a fact came from in the `sources` JSON of `knowledge_entries`.

The question: should Phase 7's audit `source_ids` (and the eventual `sources` linkage) use the vault path or the numeric document id?

**If vault_path:** human-readable, stable across re-summarize, but changes when the user moves the file (daemon reports a move → `rename` updates it).
**If document id:** immutable across moves, but opaque and requires a join to display.

Recommendation: defer the firm choice to Phase 8 (it owns `sources`); for Phase 7 keep audit `source_ids` = vault_path to match existing audit rows and avoid churn. Not a blocker.

**OQ-7D — Blob-reference shape if a document ever needs more than one blob.** (Amendment §A4)

Right now the chosen shape is two nullable columns on the document row (one blob reference, one file type), modelling a strict one-file = one-blob = one-row relationship.

The question: if a future feature needs several blobs per document (e.g. a multi-page scan kept as separate page images), do we migrate to a dedicated `blobs`/`attachments` table?

**If keep columns:** zero extra join, simplest reader path; cannot represent more than one blob per row.
**If add a `blobs` table:** clean one-to-many; adds a join for every reader and a table to maintain — complexity nothing in Phase 7 or Phase 9 currently needs.

Recommendation: keep the two columns now; migrate to a table only if a real multi-blob need appears. Not a blocker for Phase 7.

**OQ-7E — Where the vision model name lives in provider config.** (Amendment §A5)

Right now each provider config carries `model` (fast/cheap) and `synthesis_model` (smarter); C-09 lists `model`/`synthesis_model`/`embedding_model` as the required provider fields.

The question: does the vision-capable model get its own `vision_model` field, or is it folded into an existing one (e.g. routing the `"vision"` task to `synthesis_model` if that model is multimodal)?

**If a dedicated `vision_model` field:** explicit, lets the vision model differ from the text models; a small same-shape addition to each provider config (and a C-09 note that the field list grew).
**If reuse `synthesis_model`:** no new field, but couples vision capability to whatever the synthesis model happens to be, and breaks if that model is text-only.

Recommendation: add a dedicated `vision_model` field — explicit and decoupled, consistent with the per-task model-routing pattern. Not a blocker; confirm in the spec.

**OQ-7F — Should Phase 7 be split into 7A (text) and 7B (visual/binary)?** (Amendment §Slicing assessment — surfaced for the orchestrator/user, NOT decided here)

Right now Phase 7 is a single HEAVY slice (a 2241-line rewrite) and the amendment adds a new external dependency (object-storage write client), a new provider capability (vision call), a new migration, and a second prompt/audit pair on top.

The question: ship text capture and visual capture as one phase, or split them?

**If one phase:** one merge, one design lineage; but a large slice that may not fit one context window, and the text path cannot ship until the new external dependency (object storage) and vision provider are also done.
**If split 7A/7B:** 7A ships the text rewrite to a working, demoable, mergeable state with no new dependency, no migration, no vision; 7B adds the binary branch on top. Each slice fits a context window; the new external dependency is de-risked; the text path can ship even if visual work slips. The text and binary paths are cleanly independent (the fork shares only the converge point), so a split is low-risk.

Recommendation: **split into 7A + 7B.** This is a recommendation for the orchestrator/user to confirm — it is a phasing/scope call (§2 decision checkpoint), not decided unilaterally in this design.

---

## ADR references

This design creates **ADR-0014 — Capture data-safety contract: store-raw-first, summary-nullable, derived "needs-summary", AI-failure = store-anyway** (`docs/architecture/system_adr/0014-capture-data-safety-store-raw-first-summary-nullable-ai-failure-store-anyway.md`). The contract passed all three ADR gates (hard-to-reverse, surprising-without-context, real-tradeoff — see ADR). It is also constrained by **ADR-0012** (additive rearchitecture / consumer-refactor sequencing): Phase 7 is capture's consumer-refactor phase, so it MAY break capture's dependencies (delete old capture, stop importing `WriteOutcome`) but must NOT delete shared modules (`vault/writer.py`) still consumed by Phase 6.

**Amended (2026-06-13) — two newly-accepted upstream ADRs are now inputs to this design (see "## Amendment" above):**

- **ADR-0013 — Daemon hybrid cache / cloud authority / scanner-is-reconcile** (`docs/architecture/system_adr/0013-daemon-hybrid-cache-cloud-authority-scanner-is-reconcile.md`). For Phase 7: `content_hash` is computed over **raw file bytes** by the daemon; the cloud stores/compares it as-is and never recomputes from extracted text (Amendment §A2). Also resolves the old `pipelines/reconcile.py` ownership question — its successor is the **Phase 6 startup scanner**, not Phase 7 (Amendment §A3).
- **ADR-0015 — Visual/binary content: cloud stores the blob and vision-describes it** (`docs/architecture/system_adr/0015-visual-binary-content-cloud-stores-blob-and-vision-describes.md`). Adds a second capture input shape (binary/visual). ADR-0015 explicitly assigns the **blob storage shape + key scheme** to this design step (resolved in Amendment §A4: two nullable columns on `documents` + content-fingerprint object key). The store-raw-first contract (ADR-0014) extends to blobs: store blob + reference first, vision-describe second, failure = store-anyway. The blob-storage wiring + migration + vision-describe are Phase 7's owned scope; blob-serving retrieval is Phase 9.

---

## Options explored

### Option A — Store-Raw-First two-step (CHOSEN)
Store raw text first (reusing `upsert_from_upload`), then attach the summary in a second small UPDATE; AI failure keeps the document and audits the failure. Chosen — see Decision.

### Option B — Single combined write after the AI returns (Not recommended)
One transaction that writes full text + summary together once the AI completes. Simpler (one write, one routine), but **fails the data-safety contract**: on an AI failure or crash mid-call the user's content is never stored, defeating "content is sacred / summary may arrive late." Would also require holding the upload in memory across the slow LLM call before any persistence. Rejected: violates locked decision 5.

### Option C — JSON-blob summary in `documents.summary` (Not recommended)
Same two-step flow as A, but the summary is stored as a JSON object instead of Markdown. Gives precise section access, but pollutes both search indexes (the indexes would ingest JSON structure), forces every reader to parse, and adds no value Phase 7 needs. Rejected for storage-shape; folded into OQ-7A as a future option.

### Rejected alternatives (one line each)
- **Reject-on-AI-failure (conservative "flag only, don't store").** Returns failure to the daemon and stores nothing until the AI succeeds — loses content on transient outages and makes the daemon loop; violates locked decision 5.
- **New `summary_status` flag column.** Explicit "needs-summary" flag instead of deriving it from `summary IS NULL` — adds schema for a signal that already exists for free; violates locked decision 6.
- **Content-reuse-by-hash across paths (aggressive de-dup).** Reuse one summary for identical content in two folders — saves one AI call in a rare case but breaks per-path identity (locked decision 7); flagged as a future optimization.
- **Keep inline classify in capture.** Cheaper to leave it, but contradicts the locked split (classify is a separate Phase 8 async process) and re-introduces project/domain coupling.

### Amendment options explored (ADR-0015 visual/binary path — see "## Amendment")
- **Blob reference as a separate `blobs`/`attachments` table (Option B, not chosen).** Cleanly supports many blobs per document but adds a join and a table for a one-to-one relationship two columns model directly; folded into OQ-7D as a future option.
- **Image stays local; cloud stores only the description (ADR-0015 option 1, rejected upstream).** Cheapest, but the user cannot *see* a dropped graph from phone/web and the cloud cannot re-describe later without the laptop. Rejected in ADR-0015.
- **Daemon OCRs/describes locally (ADR-0015 option 3, rejected upstream).** Pushes heavy AI into the thin laptop helper and OCR alone can't interpret a graph. Rejected in ADR-0015 — visual understanding belongs cloud-side.
- **A separate top-level "describer" pipeline instead of branching the summarizer (not chosen, Amendment §A5).** Would duplicate the attach/index/audit wiring for no gain; the branch lives inside the one enrichment stage.

---

## Next step

Design doc written. Run `/architecture-docs` (or `/update-arch-story`) to fold the Phase 7 capture flow into the main architecture designs, then run `/writing-detailed-specs` to structure Option A into build steps.
