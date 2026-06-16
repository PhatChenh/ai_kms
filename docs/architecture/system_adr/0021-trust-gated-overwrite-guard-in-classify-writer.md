# ADR-0021: Trust-Gated Overwrite Guard in Classify Writer

**Status:** Accepted
**Date:** 2026-06-15
**Decision makers:** Design session (Phase 10 Self-Learning & Reports)
**Context:** Phase 10 grill decision on trust mechanics; Phase 9 placed the `_should_overwrite()` seam.

## Context

The classify pipeline's entry writer (`classify_writer.py`) updates existing knowledge entries when new documents contain updated information. Phase 9 placed an explicit `_should_overwrite()` decision point that always returns `True`. Phase 10 must decide how to protect facts that the user has validated through corrections.

The core tension: the classify pipeline needs to update facts as new information arrives (documents are the source of truth), but once a user has validated a fact, silently overwriting it with a new extraction erases their judgment.

## Decision

Activate the overwrite guard with a trust-score threshold: when an existing entry's `trust_score` exceeds a configurable threshold (default 0.5), the classify writer does NOT overwrite it. Instead, the writer inserts a new `pending` entry with the same dimension, entity, and tag, preserving both the trusted fact and the contradictory new extraction.

The threshold (0.5) means only entries that have received at least one user confirmation are protected. Entries at the default trust score (0.5, no user interaction) remain freely updatable — the guard activates at `trust_score > threshold`, not `>=`.

## Consequences

### Positive

- User-validated facts are never silently lost. The user's judgment is preserved.
- The new contradictory fact is not discarded — it becomes a `pending` entry that can be reviewed.
- The "conflict" pattern (two active entries for the same entity+tag, one trusted, one pending) is naturally detectable by a SQL query, powering the "conflicts" report type.
- The threshold is config-driven (C-06), so it can be tuned without code changes.

### Negative

- Knowledge entries for the same entity+tag can accumulate over time if the user does not resolve conflicts. Mitigation: the "conflicts" report surfaces them for human review.
- The exact-entity dedup backstop (`_find_twin` in `classify_writer.py`) will find the trusted entry as a twin, trigger the overwrite path, and hit the guard — correctly redirecting to a fresh insert. But this means the dedup backstop no longer prevents all duplicates for trusted entities. This is the intended behavior: the duplicate IS the conflict signal.
- The `>` threshold comparison means an entry with exactly 0.5 trust (no user interaction) is still overwritable. An entry with 0.55 (one confirm) is protected. This asymmetry is deliberate — trust must be actively earned, not assumed.

### Alternatives considered

1. **Lock entries on first confirmation (boolean flag).** Simpler but binary — no ability to tune the protection level. A single accidental confirm would permanently lock an entry.
2. **Version history on knowledge_entries.** Keep every version of every fact. More complete history, but massively increases table complexity and query cost for a feature that may never be needed. The correction record already captures old/new values.
3. **Always overwrite, mark as "needs review" when trust is high.** Loses the original trusted fact. The user's validated version is gone — they would have to re-correct it.

## Related

- Phase 9 `_should_overwrite()` seam: P9-MCP-15 (behavior inventory)
- Phase 10 trust mechanics: P10-SL-05, P10-SL-06, P10-SL-07 (behavior inventory)
- Constraint C-06: thresholds in config, never in code
- Phase 10 design: `docs/1_design/phase10_self_learning.md` § Component 2
