---
description: Generate a phased implementation plan from research findings. Writes to docs/plans/<feature>.md. Never writes code.
allowed-tools: Read, Bash(find:*), Bash(cat:*)
argument-hint: <feature>
---

# Planning Task

Your ONLY job in this session is to produce a written plan. Do NOT write or modify any code.

## Input

Feature to plan: $ARGUMENTS

The feature name is used as the filename slug.

## Step 1 — Load all inputs

Read these in order before writing anything. Every source serves a different purpose.

**1. `CLAUDE.md`** — Read in full. Current build progress and project conventions. Tells you what is already done and what patterns must be followed.

**2. `docs/research/$FEATURE.md`** — Check if it exists.
- **If it exists**: Read it fully. It contains the spec details, codebase findings, and reference project patterns from a prior session. This is your primary source of truth when no spec has been pasted in the current conversation.
- **If it does not exist**: This is a green-field step. The human should have pasted the spec into the conversation. If neither exists, stop and output:
  ```
  ❌ No research file and no spec found for "$FEATURE".
  Either paste the spec into the conversation or run /research $FEATURE first.
  ```

**3. `docs/plans/$FEATURE.md`** — Check if it exists.
- **If it exists**: Read it fully. Look for `# QUESTION:` and `# COMMENT:` annotations left by the human. These are your primary task for this run — address every one of them. Preserve all completed phases.
- **If it does not exist**: You are writing the plan for the first time.

**4. The current conversation** — Check if the human has pasted a spec. If yes, it takes priority over the research file for design decisions — it reflects the human's latest intent. If no spec is pasted, rely on the research file.

**5. `STATE.md`** — If it exists, read it in full before writing anything.
- Any "Architecture Decision" relevant to this feature is a hard constraint — the plan must conform to it, not re-litigate it
- Any "Open Question" tagged as blocking this feature must be resolved in the plan's "Open Questions" section or escalated to the human
- Any "Technical Debt" that this feature is responsible for retiring should appear as an explicit phase step

**6. `repomix-output.xml` — only if needed.** If resolving an annotation requires verifying an actual interface, function signature, or file that exists in the codebase, grep it surgically:
```
grep -n "<file path" repomix-output.xml | grep -i <relevant-keyword>
```
Do not read this file in full. Only pull the specific blocks you need.

## Step 2 — Handle human annotations

If `docs/plans/$FEATURE.md` already exists, scan it for any lines starting
with `# QUESTION:` or `# COMMENT:`.

### 2a. Read all annotations before acting

Read every annotation in the file first. Do not start addressing the first
one before reading the last one. Annotations may be related — partial
understanding leads to wrong revisions.

### 2b. Clarify anything unclear before revising

If any annotation is ambiguous or you cannot determine the human's intent:

- Do NOT revise the plan yet — not even the items you do understand.
- Output a clarification request listing every unclear item:

```
⚠️ Need clarification before revising:

Items I understand:
- #COMMENT on Phase 2: [restate what you think they want]
- #QUESTION on Phase 3: [restate the question and your answer]

Items I need clarified:
- #COMMENT on Phase 1: "[quote the annotation]" — do you mean [interpretation A] or [interpretation B]?
- #QUESTION on Phase 4: "[quote the annotation]" — I can't determine this without knowing [X]. What's your preference?

Please clarify and run /plan $FEATURE again.
```

Then STOP. Do not partially revise.

### 2c. Verify before adopting

For each `# COMMENT:` that suggests a design change:

1. Check it against `CLAUDE.md` and `STATE.md` constraints. Does it
   conflict with an existing architecture decision?
2. If verifiable against the codebase, grep `repomix-output.xml` to
   confirm the suggestion is technically sound.
3. If it contradicts a prior architecture decision in `STATE.md`, do not
   silently adopt it — flag the conflict:

```
⚠️ Conflict: Your comment on Phase 2 suggests [X], but STATE.md
records an architecture decision for [Y] (decided on <date>).

Options:
A) Revise the plan to follow [X] — this overrides the prior decision
B) Keep [Y] — I'll note why in the plan

Which do you prefer?
```

Then STOP and wait.

### 2d. Push back when warranted

The human's annotations are trusted input, but trust does not mean blind
compliance. Push back with technical reasoning when:

- The suggestion would break an existing interface or contract
- It contradicts a `STATE.md` architecture decision
- It adds scope that violates YAGNI — verify actual usage before including
- It would make a phase untestable or too large to verify independently
- You can see a better approach the human may not have considered

How to push back:
- State the technical concern, not an opinion
- Offer an alternative
- Let the human decide

```
# RESOLVED: [original annotation]
Pushback: [technical reasoning]. Suggest [alternative] instead.
Waiting for your call — add a new # COMMENT if you disagree.
```

### 2e. Respond without performance

When addressing annotations, never write:
- "Great point!" / "Excellent suggestion!"
- "You're absolutely right!"
- "Thanks for catching that!"

Instead:
- Restate the requirement in your own words to confirm understanding
- State what changed in the plan and why
- If the annotation was correct, just fix it — the revision speaks for itself

### 2f. Mark resolved

After addressing each annotation, change its prefix:
- `# QUESTION:` → `# RESOLVED:` with your answer on the next line
- `# COMMENT:` → `# RESOLVED:` with a note on what changed

## Step 3 — Draw the architecture

Before writing the plan, draw ASCII diagrams that show how the feature
works and where it fits. The human cannot validate a plan they cannot
visualize. Diagrams make the mental model inspectable — flawed logic,
missing connections, and unintended coupling become visible in a picture
when they hide in prose.

### 3a. Choose the right diagram type

Pick the diagram type that fits the feature. Use one or more as needed.
Do not draw diagrams that don't serve understanding — pick the ones that
expose the decisions the human needs to validate.

Keep structural diagrams (component, layer) separate from behavioral
diagrams (sequence, activity, state). Mixing "what exists" with "what
happens when" makes both views unclear. If you find yourself wanting
to label structural arrows with multiple decision conditions, that's
the signal to add a separate activity diagram, not to overload the
component one.

**Component diagram** — What are the pieces, what's each one for, and
how do they connect? Use when the feature introduces new modules,
classes, or services and the human needs to see what talks to what.

Each focal component box (the components this plan is building) should
include three things, in order:
1. The component name
2. A 1-line responsibility — what this component is for
3. 2–3 key public methods or operations — never full signatures, never
   private helpers, never every method on the class

The point of this rule: an architecture diagram is for reasoning about
structure, not replacing a class diagram. Full signatures and exhaustive
method lists add noise at this level — IDEs already generate that view
better than you can draw it. Stop at "enough to know what to call into
this component for."

```
┌──────────────────┐     ┌────────────────────┐     ┌──────────────┐
│  Watcher         │────▶│  Pipeline          │────▶│  Storage     │
│  Detects new     │     │  Orchestrates      │     │  Persists    │
│  files in inbox  │     │  capture stages    │     │  audit log   │
│  · watch(path)   │     │  · run(capture)    │     │  · upsert()  │
│  · on_drop(fn)   │     │  · register(stage) │     │  · query()   │
└──────────────────┘     └─────────┬──────────┘     └──────────────┘
                                   │ confidence < 0.60
                                   ▼
                          ┌──────────────────┐
                          │  Inbox           │
                          │  Holds notes for │
                          │  human review    │
                          └──────────────────┘
```

Exceptions to the method-list rule:
- Adjacent context components (existing modules shown for orientation,
  future components shown for forward-compatibility) may omit the
  method list — the focus is what's being built, not what's pointed at.
- Single-condition arrow labels like `confidence < 0.60` above are
  fine. Multiple decision points belong in an activity diagram.

**Data flow diagram** — How does data move through the system?
Use when the feature is a pipeline or transformation chain and the
human needs to see inputs, outputs, and intermediate states.

Label every arrow with the **domain type** that flows through it — the
Pydantic model name, dataclass, or `Result[T, E]` — not a Python
primitive. `RawCapture` is correct; `dict` is not. `Result[Summary,
ExtractionError]` is correct; `tuple` is not. The whole point of
typing arrows is to catch shape mismatches in the picture before they
become bugs in the code, and a label of "dict" catches nothing.

```
raw email ──▶ extract() ──▶ RawCapture ──▶ summarize() ──▶ Summary
                                                             │
                              AuditEntry ◀── classify() ◀────┘
                                  │              │
                                  ▼              ▼
                             audit_log      Vault/Projects/
```

**Layer diagram** — What sits on top of what?
Use when the feature touches multiple architectural layers and the
human needs to see the dependency direction.

```
┌─────────────────────────────────┐
│        MCP Server / CLI         │  ← user-facing
├─────────────────────────────────┤
│        Pipeline / Handlers      │  ← business logic
├─────────────────────────────────┤
│        Storage / Config         │  ← infrastructure
└─────────────────────────────────┘
```

**Sequence diagram** — What happens in what order?
Use when the feature involves multiple actors or async steps and the
human needs to see the timeline.

```
Human          Watcher        Pipeline        Storage
  │               │               │               │
  │  drop file    │               │               │
  │──────────────▶│               │               │
  │               │  new capture  │               │
  │               │──────────────▶│               │
  │               │               │  store()      │
  │               │               │──────────────▶│
  │               │               │  ◀── Ok ──────│
  │               │  ◀── done ────│               │
  │  ◀── notify ──│               │               │
```

**Activity / decision-flow diagram** — What's the decision logic at
this point? Use when the feature has branching, confidence-gated
routing, or multiple conditional paths and the human needs to see
what happens under what condition.

This is distinct from a sequence diagram (which shows order across
actors) and a state diagram (which shows an entity's lifecycle).
Reach for an activity diagram whenever a single operation's outcome
depends on multiple thresholds, rules, or conditions. The moment you
catch yourself wanting to stack multiple `if X then Y else Z` labels
onto a component diagram's arrows, draw this instead.

```
            ┌─────────────────┐
            │  classify(note) │
            └────────┬────────┘
                     │ Decision(label, confidence)
                     ▼
              ┌─────────────┐
              │ confidence  │
              │   ≥ 0.85 ?  │
              └──┬──────┬───┘
            yes  │      │  no
                 ▼      ▼
        ┌──────────┐   ┌─────────────┐
        │ auto-file│   │ confidence  │
        │ to folder│   │  ≥ 0.60 ?   │
        └──────────┘   └──┬──────┬───┘
                       yes│      │no
                          ▼      ▼
                   ┌──────────┐  ┌──────────┐
                   │ flag for │  │  hold    │
                   │  review  │  │  inbox   │
                   └──────────┘  └──────────┘
```

**State diagram** — What states can an entity be in?
Use when the feature manages a lifecycle (e.g., a note moving through
inbox → classified → promoted → archived).

```
                 ┌──────────┐
    drop ───────▶│  inbox   │
                 └────┬─────┘
                      │ classify (≥0.85)
                      ▼
                 ┌──────────┐  human edit   ┌──────────┐
                 │ classified│─────────────▶│  locked   │
                 └────┬─────┘              └──────────┘
                      │ promote
                      ▼
                 ┌──────────┐
                 │ promoted  │
                 └────┬─────┘
                      │ 90 days inactive
                      ▼
                 ┌──────────┐
                 │ archived  │
                 └──────────┘
```

### 3b. Show existing context, not just the new feature

The diagram must show how the new feature connects to what already exists.
Draw existing components as boxes with a note like `(exists)`. The human
needs to see the integration surface, not just the new parts in isolation.

If the roadmap shows future features that will depend on what's being
built now, include them as dashed or annotated boxes so the human can
verify the current design won't block them:

```
┌─────────────┐     ┌──────────────┐     ┌ ─ ─ ─ ─ ─ ─ ┐
│  Config      │────▶│  Storage     │─ ─ ▶  Embeddings    
│  (exists)    │     │  (this plan) │     │  (Phase 4)    │
└─────────────┘     └──────────────┘     └ ─ ─ ─ ─ ─ ─ ┘
```

### 3c. Label every arrow

Unlabeled arrows are ambiguous. Every connection must say what flows
through it — a function call, a data type, an event, a file path.
An arrow without a label forces the reader to guess, and guessing
is where wrong mental models start.

### 3d. Scale to complexity

Simple features (one new module, clear data path) → one diagram, 5–10
lines. Complex features (multiple modules, branching logic, integration
with existing systems) → 2–3 diagrams of different types — pair a
component diagram with an activity diagram when routing matters, or
with a sequence diagram when ordering matters.

Do not draw diagrams for decoration. If a feature is a single function
with no integration surface, a one-line description is enough:
`config.yaml → load_config() → Config object (Pydantic)`. That is
itself a diagram.

### 3e. Present and wait

Output the diagram(s) BEFORE writing the plan. The human reviews the
architecture first. If the mental model is wrong, the plan built on it
will be wrong too.

```
📐 Architecture for "$FEATURE":

[diagram(s)]

Does this match your mental model? If anything looks wrong — missing
connections, wrong data flow, components in the wrong layer — say so
before I write the plan.
```

Wait for the human to confirm or correct. If they correct, redraw and
present again. Only proceed to Step 4 when the human confirms the
diagram is accurate.

On revision runs (when addressing `# COMMENT:` annotations that change
the architecture), update the diagram and present the diff: what changed
and why. Do not skip this step just because a diagram already exists.

## Step 4 — Write the plan

Write the plan to `docs/plans/$FEATURE.md` using this structure:

```
# Plan: <Feature>
_Last updated: <date>_
_Status: [ ] pending | [~] in progress | [x] done_

## Architecture

[Paste the confirmed diagram(s) from Step 3 here, inside a fenced
code block. This is the canonical visual reference for the feature.
Anyone reading the plan should be able to understand the structure
without reading the phases.]

## Approach
[2–3 sentences on the overall implementation strategy. Why this approach, not another.]

## Phases

### Phase 1 — <Short name>
**Goal**: [What this phase delivers, in one sentence]

**Steps**:
1. [Concrete step]
2. [Concrete step]
...

**Files to modify**:
- `path/to/file.py` — [what changes]

**Test criteria**:
- [ ] [Specific, runnable verification — not vague. E.g. "run X and assert Y"]
- [ ] [Another check]

**Status**: [ ] pending

---

### Phase 2 — <Short name>
[same structure]

---
[continue for all phases]

## Open Questions
[Any decisions not yet made — things the human needs to decide before implementation]

## Out of Scope
[Things explicitly NOT included in this plan]
```

## Rules for writing phases

- Each phase must be independently testable before moving on
- No phase should touch more than 3–4 files at once
- Tests come BEFORE the next phase starts — never at the end
- If a phase feels too large, split it

## Step 5 — Confirm

After writing the file, output exactly:

```
✅ Plan written → docs/plans/$FEATURE.md

Phases: <count>
Open questions: <count>

NEXT STEP — Review the plan:
  - Add "# QUESTION: ..." for anything unclear
  - Add "# COMMENT: ..." to redirect the approach
  - When satisfied, run: /implement $FEATURE
```

Do NOT proceed to implementation. Your job ends here.
