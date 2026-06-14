# Phase 7 — Capture Refactor: Locked Requirements

_Source: `/grill` requirement interview (Phase -1 of build-pipeline), 2026-06-13_
_Status: SIGNED OFF by project owner. Feeds the design step._
_Single source of truth for direction: `docs/0_draft/cloud_native_rearchitecture.md` (§3, §5, §7, §15.1). Roadmap: `docs/roadmap/roadmap.md` "Phase 7 — Capture Refactor"._

---

## What Phase 7 is

Rewrite the cloud capture pipeline. Input changes from a local filesystem `Path` to **extracted text arriving via daemon HTTPS upload** (`POST /api/upload`, already built in P5 Slice 2). Output changes from vault file writes (`write_note`, frontmatter, sibling `.md` under `.summaries/`) to a **structured summary stored in the `documents` table** in the DB. No vault writes. No folder-moving. No inline classify (that is Phase 8, a separate async process).

The expensive step is the AI summarization call (the **Housekeeping AI** — the cloud, behind-the-scenes AI; see CONTEXT.md). Everything is shaped around treating the user's content as sacred and the AI summary as enrichment that may arrive late.

## Grounding facts (verified against live code, 2026-06-13)

- `POST /api/upload` exists (`mcp_server/api.py::upload_handler`). It validates the body and calls `storage/documents.py::upsert_from_upload(...)`.
- `upsert_from_upload` already does the **idempotency dance** keyed on `vault_path`: no row → INSERT; same `content_hash` → skip (return existing id); different `content_hash` → UPDATE in place. It stores `full_body`, `original_filename`, `file_size_bytes`, `title`, `content_hash`. It does **NOT** summarize, index for search, write audit, or trigger classify — that gap is exactly Phase 7's job.
- `full_body` column already exists (migration 008, nullable, currently NULL on every row). **Storing extracted text needs no schema change.**
- The old `upsert(WriteOutcome)` and `replace_path(WriteOutcome)` paths in `documents.py` have `capture.py` as their only live caller — they lose their last caller when old capture dies in this phase.

---

## The 7 (8) locked decisions

1. **Dedup at the front.** The duplicate check (by `content_hash`) runs *before* the AI is called — an unchanged re-upload never pays for a summary. (Today the check lives inside the DB-store step, i.e. after the work; it must move earlier so the LLM call is skipped on duplicates.)

2. **Capture summarizes only.** No project/domain guessing. It stores `vault_path` (so the folder-intent signal — e.g. `Projects/Alpha/` — is never lost), but does **not** set a project and does **not** feed the folder name to the AI. All project/domain reasoning, including whether to trust the folder, is decided in one place: Phase 8.

3. **Summarization context injection — built now, dormant.** Before summarizing, the Housekeeping AI is given optional context: (i) existing `knowledge_entries` + (ii) semantically-similar already-captured document summaries. When none is found, it proceeds on the document text alone with no degradation. **Rationale for building it now though the knowledge table is empty until Phase 8:** an empty knowledge base is *guaranteed* on every new-user / new-vault setup, so the "no context found" fallback is a permanent production path, not a Phase-7 transient — it must be built and explicitly tested now. Lights up automatically once Phase 8 fills the table.

4. **Old path-based capture dies.** Remove `capture_file(Path)`, `_store_md`, `_store_nonmd`, `_classify_auto_md_move`, `capture_folder`, all `write_note()` calls, all `WriteOutcome` usage, frontmatter writes, sibling `.md` creation, the old vault-writing `kms capture` behavior. Keep **one thin dev entry** (`kms capture <file>`) that extracts text locally and calls the *new* pipeline function directly, in-process — so devs can test capture without standing up the daemon. **Do not** delete the watcher or indexer here — those die with Phase 6 / the unassigned reconcile transition; only delete a shared module if Phase 7 is provably its last consumer (ADR-0012).

5. **AI failure = store anyway.** On summarization failure (model down, timeout, garbage response), save the document with its full text (`full_body` — keyword-searchable immediately), leave `summary` empty, write a failure entry to the audit log (honest, not silent — C-13), and return success to the daemon so it does not loop. The summary can arrive on a later retry. The retry job itself is deferred (Phase 7 ships the flag + audit trail, not the retry runner).

6. **"Needs summary" is derived, not flagged.** "Summary is empty" *is* the signal — a later retry asks "which documents have no summary yet?". **No new column, no schema change.** The audit log records *why* a summary is missing. (If failure-type distinction is ever needed, an explicit `summary_status` column is a one-migration addition then.)

7. **Same content, two locations = two rows.** Per-path identity (the user deliberately put the file in two places; the daemon reports moves/deletes per-path). Accept the rare double-summary on a manually copied file. Skip the content-reuse-by-hash optimization in Phase 7; flag it as a future optimization.

8. **Classify trigger is a no-op stub.** Just a log line — no flag, no queue, no marker. Phase 8 finds its own work ("documents that have produced no knowledge facts yet"); every Phase-7-era document has zero facts by definition, so Phase 8 backfills them all naturally. Building a queue/flag now would be scaffolding for a consumer that does not exist (C-15).

---

## Deferred to the design phase (flagged, NOT silently decided)

- **Structured summary storage shape.** *How* the structured summary (overview / key points / decisions / action items / people mentioned) is physically stored in the single `summary` TEXT column — JSON blob vs rendered markdown vs other. Design decides.
- **Descriptive title generation.** Whether the summarizer also produces a descriptive title (overriding the filename stem) for better search quality. Likely yes (Phase 3 Session B established descriptive-title-at-capture); confirm in design.
- **`upsert_from_upload` extension + old-path retirement.** *How* `upsert_from_upload` is extended for the two-step "store content, then attach summary" flow (the two-step naturally matches decision 5), and whether/how the old `upsert(WriteOutcome)` / `replace_path(WriteOutcome)` paths are retired once their last caller (old capture) is gone.

---

## Constraints in play

- **ADR-0012** — additive rearchitecture; defer breaking changes to the consumer-refactor phase. Phase 7 **is** capture's consumer-refactor phase, so it MAY break capture's dependencies (delete old capture, retire `WriteOutcome` path), but must NOT unilaterally delete shared modules still consumed by Phase 6 (watcher) or the unassigned reconcile transition.
- **C-07** prompts as YAML (the summarize prompt lives in `prompts/`, never inline).
- **C-08** LLM via provider factory (`get_provider("capture", ...)`).
- **C-12** Result types — every public function returns `Success`/`Failure`, never raises.
- **C-13** audit log — the `CAPTURED` decision and any summarization failure are audited.

## CONTEXT.md terms added during this interview

- **Housekeeping AI** (Cloud-Native — Two-AI Model)
- **summarization context injection** (Cloud-Native — Two-AI Model)

## ADR candidate (for the design step to formalize if gates pass)

The capture **data-safety contract** — store-raw-first / summarize-second, `summary` nullable, "needs-summary" derived (no flag column), AI-failure = store-anyway-and-audit — is hard-to-reverse + surprising-without-context + a real trade-off (vs reject-on-failure, vs explicit flag column). Likely warrants an ADR. Defer the offer to the design step.

---

## Amendment — new upstream inputs (ADR-0013, ADR-0015), added 2026-06-13

After this interview, two ADRs from the Phase 6 (Daemon) grill landed that expand Phase 7's scope. They are accepted decisions and are inputs to the design step (not re-litigated here):

- **ADR-0013 (daemon hybrid cache / cloud authority / scanner-is-reconcile).** For Phase 7: `content_hash` is computed over **raw file bytes**, not extracted text — the cloud stores/compares whatever the daemon sends, so cloud-side code must NOT assume the hash is over extracted text. Also resolves the `pipelines/reconcile.py` ownership the design risk-list flagged: its successor is the Phase 6 startup scanner, not Phase 7.
- **ADR-0015 (visual/binary content — cloud stores blob + vision-describes).** Adds a **second capture input shape** to Phase 7: a binary/visual upload (raw bytes + mime + raw-byte hash + size + vault_path, NO extracted_text). The cloud persists the blob in VNG Object Storage, stores a **reference** (not the bytes) in `documents`, and runs a **vision model** once to produce a searchable text description stored as the document's summary / `full_body`. The text path is unchanged. ADR-0015 explicitly assigns the **blob storage shape + key scheme** to the Phase 7 design step. Store-raw-first (ADR-0014) extends: store blob+reference first, vision-describe second (failure → store-anyway, description arrives late). The `/api/upload` *wire format* accepting bytes is Phase 6 (slice A1); what the cloud *does* with the bytes (store + describe) is Phase 7.

The design doc (`docs/1_design/phase7/`) is adjusted to fold both in.

**SLICE SPLIT (decided 2026-06-13, post-amendment): Phase 7 splits into 7A and 7B.**
- **7A — Text capture (THIS pipeline run):** receiver → summarize → store, the dependency-free text path. Behavior IDs `P7-CAP-01…09`. Carries the ADR-0013 corrections that apply to both paths (`content_hash` = raw bytes; reconcile.py ownership is Phase 6's). Excludes the visual branch.
- **7B — Visual/binary capture (own spec→plan later):** blob store + vision-describe + migration 009 + new S3 client dependency + vision provider extension. Behavior IDs `P7-CAP-10…13`. Design seed = the amendment §A1–§A7 in the design doc. Not built in this run; new-dependency (S3 client) approval deferred to 7B start.
Rationale: the two paths share only the converge point; 7A is solid and demoable without object storage or vision. Matches the P5 slicing precedent.

## Tier classification

**HEAVY.** Evidence from the interview: this is a rewrite (not a tweak) of a 2241-line module; it inverts the source-of-truth (vault → DB); it spans multiple components (receiver, summarizer, DB writer, classify-trigger stub) and touches the daemon-facing API contract; it retires modules and the `WriteOutcome` coupling; it is hard to reverse. Full chain runs: **design → spec → research → plan.**
