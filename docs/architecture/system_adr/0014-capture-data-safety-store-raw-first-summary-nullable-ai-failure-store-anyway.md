# Capture data-safety contract: store raw text first, summary is nullable, "needs-summary" is derived, AI-failure = store-anyway-and-audit

_Created: 2026-06-13_

The Phase 7 capture refactor (cloud-native rearchitecture) rewrites the cloud capture pipeline to receive extracted text from the daemon and produce a structured summary into the `documents` table — no vault writes. A core question the rewrite forces: **what happens to the user's content when the summarizer AI is unavailable, slow, or returns garbage?** The Phase 7 requirement interview (signed off 2026-06-13) locked the answer as a data-safety contract. **Decision: capture stores the user's full extracted text to the database BEFORE calling the AI (store-raw-first); the `summary` column is left nullable and starts empty; a missing summary is the derived "needs-summary" signal (`summary IS NULL`) — no flag column, no schema change; and on any summarization failure the pipeline keeps the stored document, writes a failure entry to the audit log, and returns success to the daemon so it does not loop.** The summary may arrive late, on a retry.

**Status:** accepted

## Context — why this needed a decision

In the old (vault-writing) capture, summarization happened *before* anything was persisted: the AI summarized, then `write_note` + `documents.upsert` wrote the result. An AI failure simply aborted the capture — acceptable when the user's original file still sat untouched on disk as the real source of truth.

The cloud model inverts the source of truth (ADR-0012 sequencing; rearch §5): the database becomes authoritative for AI-generated content, and the daemon ships *extracted text* up to the cloud. Two new realities make "abort on AI failure" unsafe:

1. **The content has left the user's authority boundary.** The daemon uploaded extracted text; if the cloud aborts on an AI hiccup and stores nothing, that capture is lost until the daemon happens to re-send (and the daemon, on getting a failure, may loop or back off — wasting work or dropping the file).
2. **The expensive, flaky step (the LLM) and the cheap, reliable step (saving text) are now in the same request.** Coupling the document's survival to the AI's success means a transient model outage destroys user content.

The guiding product principle from the requirements: **the user's content is sacred; the AI summary is enrichment that may arrive late.** That principle only holds if persistence does not depend on summarization.

A second sub-question: once a summary can legitimately be missing, how does a later retry job find documents needing a summary? Adding a `summary_status` flag column is the obvious move — but the absence of a summary already *is* the signal, and the requirements forbid schema additions in this phase (locked decision 6).

## Decision — the contract, in four parts

1. **Store-raw-first.** The upload's full extracted text, filename, size, and content fingerprint are written to the `documents` row (via `upsert_from_upload`) **before** the summarizer is called. The user's content is durable the moment it arrives, independent of the AI.
2. **Summary is nullable and starts empty.** The raw-store step leaves `summary` empty; a second step attaches the structured summary after a successful AI call (a small `attach_summary` UPDATE on the same row).
3. **"Needs-summary" is derived, not flagged.** A document with no summary yet is exactly `summary IS NULL`. The eventual retry job asks "which documents have no summary?" — no new column, no schema change. The audit log records *why* a given summary is missing.
4. **AI-failure = store-anyway-and-audit-and-succeed.** On summarization failure (model down, timeout, unparseable response), the pipeline: keeps the already-stored document, writes a failure entry to the audit log (honest, not silent — C-13), and returns `Success` to the daemon (so it does not retry-loop). The document is immediately keyword-searchable by its full text; the summary fills in on a later retry. (The retry runner itself is deferred — Phase 7 ships the contract + audit trail, not the runner.)

## Considered options

- **Reject-on-failure (abort, store nothing — the old behaviour).** Simplest; one write after the AI succeeds. Rejected: in the cloud model this loses the user's content on a transient AI outage and makes the daemon loop on the failed upload. Violates "content is sacred."
- **Single combined write after the AI returns.** Store full text + summary together in one transaction once the AI completes. Rejected: still couples the document's survival to the AI call — a crash or failure mid-call persists nothing.
- **Explicit `summary_status` flag column.** Store the document, mark it `needs_summary`. Rejected: adds schema for a signal that already exists for free (`summary IS NULL`); violates locked decision 6 (no schema change this phase). Kept as a one-migration fallback if failure-*type* distinction is ever needed.
- **Store-raw-first, summary-nullable, derived flag, store-anyway-on-failure (chosen).** Decouples persistence from summarization; honours the data-safety principle; zero schema change.

## Consequences

- **A document can exist with full text but no summary**, transiently (between the two write beats) or until a retry (after an AI failure). Every reader of `documents.summary` must tolerate NULL: search by `summary` simply has less to match, meaning-search embedding (which folds in the summary) is deferred to the retry, and the briefing phase must skip or placeholder a null summary. Keyword search over `full_body` covers the interim.
- **Two DB writes per capture** (store-raw, then attach-summary) instead of one. The extra write is the price of the safety guarantee.
- **The daemon always sees success for a stored-but-unsummarized document.** "Success" to the daemon means "your content is safe," not "the summary is ready." Downstream/UI must not assume a captured document has a summary.
- **A retry job is now owed.** Something must eventually re-summarize `summary IS NULL` documents. Phase 7 ships only the contract + audit trail; the runner is deferred (tracked as future work).
- **Front-loaded dedup is part of the same contract.** Because the document is keyed by content fingerprint at the raw-store step, an unchanged re-upload is detected before the AI runs and never pays for a summary (locked decision 1).
- **This is the phase where C-01 flips.** Storing `full_body` + summary in the database as authoritative AI content is the concrete inversion of "vault is source of truth." C-01/C-03 are rewritten as part of Phase 7 (per ADR-0012, the flip rides with the consumer-refactor phase).

## Gate check (why this is an ADR)

- **Hard to reverse:** the two-beat write semantics and "success-means-stored-not-summarized" become part of the daemon↔cloud contract and every downstream reader's assumptions. Changing it later means re-coordinating the daemon, the API, and all summary readers.
- **Surprising without context:** a future reader sees the pipeline store an empty summary and return success on an AI failure and reasonably asks "is that a bug?" — this ADR is the answer.
- **Real trade-off:** genuine alternatives existed (reject-on-failure; explicit flag column) and were chosen against for specific reasons.
