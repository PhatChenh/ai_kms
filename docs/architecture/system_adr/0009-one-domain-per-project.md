# One domain per project in the Project Registry

A project in the vault belongs to exactly one domain for routing and classification purposes. When `Projects/<A>/CLAUDE.md` carries multiple `domain/<D>` tags, only the first is used. Projects with no domain tag, an unrecognised domain tag, or a missing `CLAUDE.md` go to Uncategorized.

**Status:** accepted

**Context**

Notes in `Projects/<A>/` can and do carry multiple `domain/<D>` tags — they are genuinely multi-domain: a finance note might also carry `domain/Operations`. This is correct for notes, because a note is a leaf artifact that a human classified into one or more domains.

A project folder (`Projects/<A>/`) is different. It is a *destination* — a bucket that Phase 2 Classify must route inbox items into. A destination must be unambiguous: if the AI is deciding where to file a note, it needs to pick one folder. Presenting Alpha in both the Finance group and the Operations group would require the Classify pipeline to resolve that ambiguity on every call, duplicating decision logic and producing inconsistent audit trails.

**Decision**

The Project Registry enforces a one-domain constraint at the project level only. The constraint is read from `Projects/<A>/CLAUDE.md`:

- If CLAUDE.md has one or more `domain/<D>` tags, the first tag whose domain folder exists is used.
- If CLAUDE.md has no `domain/<D>` tag, or the CLAUDE.md file is absent, or no matching `Domain/<D>/` folder exists, the project goes to Uncategorized.

This rule does NOT affect note files — notes continue to allow multiple domain tags without restriction.

**Considered options**

- Allow multi-domain projects — rejected. Classify must choose a single destination folder. Forcing the Classify pipeline to break ties on every routing decision pushes policy into pipeline code instead of config. The registry is the right place to resolve this ambiguity once at build time.
- Use the last domain tag instead of the first — rejected. Arbitrary, unpredictable. The first tag is the one the human placed deliberately at the top; the first-wins rule is stable and easy to override (just reorder the list).
- Raise an error on multi-domain CLAUDE.md — rejected. Silent first-wins degrades more gracefully than an error that blocks the whole registry build.

**Consequences**

- The asymmetry between notes (multi-domain) and projects (single-domain) must be explained to anyone editing `Projects/<A>/CLAUDE.md`. The storage format choice (ADR-0010, TBD) must make the one-domain expectation visible.
- If a project legitimately belongs to two domains, the human must choose one as the primary (move to Uncategorized is also acceptable — Classify will use semantic reasoning). Splitting a project is not a registry concern.
- Phase 2 Classify reads the registry output. The prompt note for the Uncategorized group ("these projects have no domain assignment yet; use semantic reasoning to infer connections") covers projects where the single-domain constraint produced Uncategorized due to a stale tag.
