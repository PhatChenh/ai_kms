# Classify retries a failed document with self-correcting feedback, bounded by a config cap, then parks it for human review

_Created: 2026-06-15_

Phase 8 Slice B extracts structured facts from a captured document via per-dimension AI calls. A call can fail (bad JSON, invalid tag, hallucinated id) or produce one bad fact inside an otherwise-valid reply. **Decision: when any fact is bad or any per-dimension audit write fails, write the good facts but do NOT stamp the document classified, so the work-discovery scan re-finds it and retries (D9); on each retry inject a machine-generated "previous attempt feedback" string into the prompt so the AI can self-correct (D11); cap total attempts at a config value K, after which the document is parked for human review (status = `needs-review`, which the work scan skips) with an audit entry explaining why (D10).** Attempt count and last-failure reason persist on new `documents` columns so they survive container restarts (retries span reboots).

**Status:** accepted

## Context

- Slice A's design (ADR-0017) deliberately deferred retry-bounding: a failed classify simply leaves `classify_content_hash` empty/stale and is re-queued by the next catch-up scan. There is no brake today.
- Without a bound, a deterministically-malformed AI reply (same bad output every time) produces an infinite re-classify loop, burning DeepSeek tokens on every restart forever.
- The project's audit rule is non-negotiable (C-13): every AI decision must be on record before the work is considered done. The stamp is therefore the last step, gated on the audit write succeeding.
- A simpler "skip the bad fact and stamp anyway" was a genuine alternative — but it loses that fact forever unless the document's content later changes (e.g. a deadline the AI fumbled once is never re-attempted). The user's whole point is that no fact should be silently dropped.
- Retries cross container restarts (a doc can fail, the container can reboot via Litestream, and the doc is retried on the next boot's catch-up scan). So the attempt count and the last-failure note cannot live only in memory — they need durable storage.

## Decision

1. **Withhold the stamp on any imperfection (D4 + D9).** Per document, the write order is facts → per-dimension audit → stamp. If any fact fails validation (invalid tag, unknown action, missing field, hallucinated id) or any dimension's audit write fails, the good facts are committed but `classify_content_hash` is left unstamped — the document stays discoverable and is retried. Duplicate facts written on a prior partial attempt are healed by the exact-entity dedup backstop (ADR-0019) on retry.
2. **Self-correcting feedback (D11).** On a retry, the extraction prompt includes a `previous_attempt_feedback` slot — a machine-generated description of why the last attempt failed (e.g. "you used tag `urgent`, which is not allowed for dimension people; valid tags: [role, other]"; "id 999 does not exist"). Empty on the first attempt. This is distinct from the banned Phase-10 user-corrections few-shot slot (broad-grill decision #17): it carries machine-generated validation errors, not human corrections.
3. **Bounded by a config cap (D10).** A `classify.max_retries` config value (K) bounds total attempts. The work-discovery scan and the live-enqueue path both skip a document once it is parked.
4. **Park for human review, never lose the fact (D10).** After K failed attempts, the document's `status` is set to `needs-review` (work scan filters it out), and an audit entry records why it was parked. A future web UI surfaces parked documents. The document is never auto-deleted and its partial facts are never auto-retired.
5. **Durable retry state on `documents` (new migration).** Two new columns — `classify_attempts` (INTEGER) and `classify_last_error` (TEXT) — persist across restarts. They are cleared on a successful classify (so a later content change starts fresh).

## Considered options

- **Withhold-stamp + feedback + cap, state on `documents` columns (chosen).** Smallest durable surface: the work-discovery query already reads `documents`, so adding two columns keeps work discovery a single-table query and avoids a join. The cap converts an infinite loop into a bounded, observable park.
- **Skip-the-bad-fact and stamp anyway (no retry).** Rejected: silently loses any fact that fails once, with no second chance — exactly the data-loss the user rejected. Simpler, but defeats the purpose.
- **Separate `classify_retries` table.** Rejected: a second table keyed by doc id duplicates the lifecycle the `documents` row already owns and forces the hot work-discovery query into a join, for data that is 1:1 with the document. No second consumer needs it as its own entity.
- **Reconstruct attempt count from the audit log.** Rejected: the audit log can record attempts, but the *parked* state and the *last-failure feedback string* have no home there without scanning and parsing history on every attempt — and the parked state must be a cheap filter in the work-discovery query, which an audit scan cannot provide.

## Consequences

- **The work-discovery query gains a status filter.** `find_unclassified` must exclude `status = 'needs-review'` so parked documents are not re-queued. This is a behavior change to a Slice A function (Slice A's query had no such filter).
- **A new migration (011) adds two `documents` columns.** Per C-05 this is a versioned `.sql` delta, never an in-code ALTER. The standard version-pin test cascade applies (bump prior migrations' pinned version to 11).
- **`status = 'needs-review'` is now overloaded.** Capture already uses `needs-review` for binary/needs-summary rows; classify reuses the same value for parked docs. The two are distinguishable by whether `classify_attempts` reached the cap, but a future UI must not assume `needs-review` means only one thing.
- **Repeated partial failures can accumulate then heal.** Across attempts before the cap, good facts are written each time; the dedup backstop (ADR-0019) prevents duplicates, and updates are idempotent. A doc that finally succeeds on attempt 3 has clean state; a doc that never succeeds is parked with whatever partial facts the good attempts wrote (acceptable — better than losing them).
- **K is a tuning knob, not a guarantee of success.** A document parked after K attempts needs human attention; the audit entry is the only automatic signal until the review UI exists.
