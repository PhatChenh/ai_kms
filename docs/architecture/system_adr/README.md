# Architecture Decision Records

ADRs live here. Sequential numbering: `NNNN-slug.md`.

## Status values

- `accepted` — decided and implemented
- `proposed` — under consideration, not yet built
- `superseded by ADR-NNNN` — replaced by a newer decision; link both ways
- `deprecated` — no longer relevant but not replaced

## Superseded discipline (non-negotiable)

**Never edit or delete a decided ADR.**

When a decision changes:
1. Write a new ADR (`NNNN+1-slug.md`) with the new decision
2. Set the old ADR's Status line to `**Status:** superseded by ADR-NNNN`
3. Add a link to the new ADR at the bottom of the old one
4. Add a "supersedes ADR-NNNN" note in the new one

This is the decision-layer anti-drift mechanism. Editing history makes it impossible to understand why something changed.

## Format

```md
# Short title of the decision

One to three sentences: context, decision, and why.

**Status:** accepted

**Considered Options** (optional — only when rejected alternatives are worth remembering)

- Alternative A — why rejected

**Consequences** (optional — only when non-obvious downstream effects need calling out)

- What this means for future work
```

## When to write one

All three must be true:
1. **Hard to reverse** — changing your mind later has real cost
2. **Surprising without context** — a future reader would wonder "why did they do it this way?"
3. **Real trade-off** — genuine alternatives existed; you picked one for specific reasons
