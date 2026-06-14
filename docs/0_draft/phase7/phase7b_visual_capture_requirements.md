# Phase 7 Slice 7B — Visual/Binary Capture: Locked Requirements

_Source: `/grill` requirement interview (Phase -1 of build-pipeline), 2026-06-14_
_Status: SIGNED OFF by project owner. Feeds the design step._
_Slice 7A (text capture) is DONE through the full pipeline (spec + research + plan exist; see `docs/{2_specs,3_research,4_plans}/phase7/phase7a_text_capture.md`)._
_Single source of truth for direction: `docs/0_draft/cloud_native_rearchitecture.md` (§3, §5, §7, §15.1) + the Amendment §A1–§A7 in `docs/1_design/phase7/phase7_capture_refactor.md` (the 7B design seed). Roadmap: `docs/roadmap/roadmap.md` "Phase 7 — Capture Refactor"._

---

## What Phase 7B is

Teach the cloud capture pipeline a **second input shape**: a binary/visual file that arrives at `POST /api/upload` as **raw bytes + mime type + raw-byte fingerprint + size + vault_path, with NO extracted text**. The cloud:

1. Stores the raw bytes in cloud object storage (VNG Object Storage, S3-compatible — the "blob store").
2. Keeps only a **reference** to those bytes on the `documents` row — never the bytes in the DB.
3. Runs a **vision model once** to produce a searchable text description of the image/page.
4. Writes that description to the document's `summary` **and** `full_body` so search finds it.

The binary branch shares the 7A converge point (index → audit → classify-trigger stub) and the same store-raw-first safety contract (ADR-0014, extended): **store the blob first, describe second; if the description never lands, keep the document anyway.**

The text path (7A) is unchanged. The fork is clean — text never touches the blob store or the vision model.

## Grounding facts (from the design seed §A1–§A7, verified against live code 2026-06-13)

- `POST /api/upload` exists (`mcp_server/api.py`); the binary **wire format** that carries bytes is **Phase 6 (daemon) work**, not 7B. 7B handles the bytes once they arrive.
- `documents` has **no** column for a blob reference and **no** column for mime type today. Migration 009 is required (additive; C-05 / ADR-0012-clean).
- **No** S3/object-storage write client exists in `src/` or `pyproject.toml`. Only Litestream touches object storage, and it only mirrors the database file — it cannot store an arbitrary blob. A new client must be built/added.
- The LLM provider interface is **text-only** (`complete(system: str, user: str)` — both strings). There is no image-input path. A vision-capable call must be added.
- `content_hash` is computed over **raw file bytes** by the daemon (ADR-0013). The cloud compares what it is sent — it must never recompute a hash from extracted text.

---

## The 10 locked decisions

1. **Two input shapes at the front door.** `/api/upload` accepts either the text shape (7A, done) or the binary/visual shape (raw bytes + mime + raw-byte hash + size + vault_path, no text). 7B owns what the cloud does with the binary shape.

2. **Store-blob-first, describe-second.** Persist the raw bytes to the blob store, write a reference on the `documents` row, **then** call the vision model. The user's file is safe the moment it is stored, before any AI runs. (ADR-0014 store-raw-first, extended to blobs.)

3. **Description populates `summary` AND `full_body`.** The vision description is the image's searchable text — written to both fields exactly as 7A's structured summary / extracted text are. The raw bytes never enter `full_body`; only the words.

4. **Privacy / data-residency line — ACCEPTED.** With visual capture, the user's actual image/document **bytes leave the laptop** and live in cloud object storage (a change from 7A, which sent only text). Accepted for the single-tenant personal deployment. A per-vault opt-out is **deferred** to a future sensitive/multi-tenant scenario — not built now. (ADR-0015 privacy shift.)

5. **Delete now, reference-counted.** When the daemon reports a file delete and the `documents` row goes away, the stored blob is deleted **only when no other row still references it** (delete-when-last-reference-gone). Because blobs are content-addressed, two rows (same bytes, two paths) share one blob; deleting one row must not break the survivor. _Assumption to verify in design: a cloud-side delete path exists for the daemon's delete report to hook into._

6. **Vision routing by file type.** Images (`image/*`) **and** text-less PDFs (`application/pdf` that arrived with no extracted text) → vision model. All other binaries (zip, video, spreadsheet-with-no-text, etc.) → **stored but not described** (empty description, keyword-searchable by filename). The set of "send-to-vision" types is config-driven so formats can be added without code changes. _The "local text extraction failed/empty → send raw bytes" trigger is the **daemon's job (Phase 6)**; 7B assumes that contract and the design verifies it._

7. **New storage-client dependency — APPROVED in principle.** 7B may add **one** S3-compatible storage client (none exists today). The exact library (e.g. `boto3` sync vs `aioboto3` async vs `minio`) **and** whether the picture-bucket reuses the existing Litestream backup bucket or gets its own (`KMS_BLOB_*` env vars) = the design step brings back a concrete recommendation **before anything is installed** (reversibility rule / global contract §4).

8. **Size cap (config) → store, skip vision.** Files above a configurable size limit are **stored** (never lost) but **skipped for vision** (empty description, same needs-description state) to avoid surprise vision-API cost and model rejection on giant files. Threshold in config (C-06).

9. **"Needs-description" is derived; the *reason* lives in the audit log.** Empty description = needs-description, derived from the same empty-summary signal as 7A's "needs-summary" — **no new flag column** (consistent with 7A locked decision 6 / ADR-0014). Every skip/failure writes an **audit entry stating why** (too big / vision failed / unsupported type) so a future UI can explain it. (See TD below.)

10. **Idempotency on re-upload.** The front-loaded dedup over **raw bytes** runs **before** blob-store and **before** the vision call — a re-uploaded identical image is detected and skipped (no re-store, no re-pay). A *changed* image (new bytes → new hash) is treated as new content (rare, accepted/self-correcting per ADR-0013).

---

## Deferred to the design phase (flagged, NOT silently decided)

- **Storage-client library choice + bucket topology.** `boto3` vs `aioboto3` vs `minio`; reuse backup bucket vs separate `KMS_BLOB_*` bucket. Design recommends; owner confirms before install. (Decision 7.)
- **Blob-reference shape.** Two nullable columns on `documents` (blob reference + `mime_type`) — design seed Option A (Recommended) — vs a separate `blobs` table (Option B). Migration 009 either way. (Design seed §A4; OQ-7D for the future multi-blob case.)
- **Blob key scheme.** Content-addressed (keyed by raw-byte fingerprint) recommended over path-based, so moving a file never orphans/re-uploads its blob and identical bytes de-duplicate at the object level. (Design seed §A4.) Confirm in design.
- **Vision provider extension shape.** How the image-capable call is added to the provider contract (e.g. a `describe_image` method that default-raises on non-vision providers), the new `"vision"` task, a `vision: Provider` field, and a vision model name in config. C-09 lists required provider fields — adding a vision model field is a same-shape addition (OQ-7E). (Design seed §A5.)
- **What the vision description's audit decision is called** (`DESCRIBED` vs reuse `CAPTURED`). (Design seed §A5.)

---

## Constraints in play

- **ADR-0012** — additive rearchitecture. Migration 009 is additive; 7B may break capture's own dependencies but must NOT unilaterally delete shared modules still consumed by Phase 6 (watcher) or the unassigned reconcile transition.
- **ADR-0013** — `content_hash` is over **raw file bytes** (daemon-supplied); cloud never recomputes from text. Cosmetic binary change can flip the hash → re-capture (rare, accepted).
- **ADR-0014** — store-raw-first / AI-failure = store-anyway-and-audit. 7B extends it: store blob+reference first, vision-describe second.
- **ADR-0015** — visual/binary content: cloud stores blob + vision-describes. Privacy/data-residency shift stated, not hidden.
- **C-05** migration-only schema changes (migration 009).
- **C-06** thresholds in config (size cap).
- **C-07** prompts as YAML (`prompts/describe_image.yaml`, never inline).
- **C-08** LLM via provider factory (`get_provider("vision", ...)`, never instantiate directly).
- **C-12** Result types — every public function returns `Success`/`Failure`, never raises.
- **C-13** audit log — the describe decision and any vision failure/skip are audited (with reason).

## Behavior IDs

`P7-CAP-10 … P7-CAP-13` (visual/binary capture). Design Step 3.5 derives the exact per-ID success criteria.

## Tech debt logged this interview

- **TD — "why no description" not surfaced to user / consuming AI.** When a blob is stored but left without a description (too big / vision failed / unsupported type), the reason is recorded in the audit log only. Surface it to the user and the consuming AI once a UI exists. (Owner-requested at grill, 2026-06-14.)

## ADR candidate (for the design step to formalize if gates pass)

The **blob lifecycle + content-addressed key + reference-counted delete** contract (store-blob-first, content-addressed object key, delete-when-last-reference-gone, orphan-on-crash tolerated via idempotent put) is hard-to-reverse + surprising-without-context + a real trade-off (vs path-keyed blobs, vs never-delete, vs per-row delete). Likely warrants an ADR (or an extension of ADR-0015). Defer the offer to the design step. _(Note: ADR-0013/0014/0015 already cover much of the surrounding contract — design decides whether a new ADR or an amendment is warranted.)_

## Tier classification

**HEAVY.** Evidence from the interview: new external dependency (object-storage write client), new provider capability (image-input vision call), new migration (009, blob-reference columns), new prompt + audit pair, reference-counted delete semantics, and a privacy/data-residency boundary crossing. Hard to reverse, spans multiple components. Full chain runs: **design → spec → research → plan.**
