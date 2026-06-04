# docs/_archive/

Historical docs for **completed** phases. Moved here 2026-06-04 to de-noise `docs/`
without losing the trail. **Nothing deleted** — `git mv` preserved full history.

These were process artifacts (scratch drafts, executed plans, consumed research),
not the decision/intent record. The live, maintained docs stay in `docs/`:

- **Decisions** → `docs/architecture/` (ADRs — superseded, never deleted)
- **Intent** → `docs/design/`, `docs/specs/`, `docs/roadmap/`
- **Current state** → `STATE.md` (repo root)
- **Testing** → `docs/testing/`

## What's here

| Folder | Was | Status |
|---|---|---|
| `draft/` | pre-skill scratch (phase 0/1 capture, handlers, llm, storage, vault) | superseded by design/specs |
| `plans/` | executed implementation plans (phase0, phase1, phase1.5_redesign, phase_pre_2) | built + merged |
| `research/` | consumed research for the same phases | folded into the build |

Not archived (still active in `docs/draft/`): `vault-restructure-editable-noedit-split.md`
(in-development phase) and `ssot-and-noncoder-docs-brief.md` (governs the deferred
anti-drift / layered-SSOT work).

## Deferred work

Per `docs/draft/ssot-and-noncoder-docs-brief.md`: after the in-development
vault-restructure phase ships → wrap up Phase 1 → run the brief's anti-drift
reconcile (STATE.md becomes the "what it does now" source of truth) → Phase 2.
