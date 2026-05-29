# Destination resolution in _store_nonmd() is inline path math — no AI call, no new pipeline stage

Whether a binary is LOCATED or CLUELESS is determined by pure path inspection at the top of `_store_nonmd()`. No AI triage. No new stage. `Projects/<A>/` or `Domain/<D>/` → LOCATED; everything else → CLUELESS.

**Status:** accepted

**Considered Options**

- AI-assisted routing (infer project from content/filename) — deferred to Phase 2 Classify.
- New pipeline stage — rejected: stage count stays at 5; routing is not an extractable concern here.

**Consequences**

- Phase 2 Classify resolves CLUELESS markers — it must NOT re-invoke `_store_nonmd()`. It reads the pending-routing sibling, classifies to project/domain, calls path helpers, writes the full sibling, and clears `status=pending-routing`.
