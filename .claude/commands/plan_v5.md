---
description: Generate a phased implementation plan from research findings. Writes to docs/plans/<feature>.md. Never writes code.
allowed-tools: Read, Bash(find:*), Bash(cat:*)
argument-hint: <feature>
---

# Planning Task

> The global HITL behavioral contract in CLAUDE.md applies in full.
> The specific rules below govern the planning workflow only.

Your ONLY job in this session is to produce a written plan. Do NOT write or modify any code.

## Input

Feature to plan: $ARGUMENTS

The feature name is used as the filename slug.

---

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

---

## Step 1b — Surface ambiguities before doing any work  

After reading all inputs, before any annotation work or architecture drawing:

Identify every open question or ambiguity in the spec/research. For each one that has 2 or more reasonable answers, call `AskUserQuestion` with:
- A one-line description of what the decision is
- 2–4 options, each with a one-line tradeoff
- Your recommendation first, labeled "(Recommended)"

**Do not proceed to Step 2 until every blocking ambiguity is resolved by the human.**

If no blocking ambiguities exist, state: "No blocking ambiguities found — proceeding to annotations."

Example triggers for Step 1b:
- The spec mentions two storage strategies without picking one
- The research file found 2 valid patterns in the codebase that are mutually exclusive
- STATE.md has an open question tagged as blocking this feature
- The scope boundary is unclear (e.g., does this feature include the UI layer or not?)

---

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
- Call `AskUserQuestion` for each unclear annotation with:  
  - The quoted annotation text
  - 2–3 interpretations as options
  - Your best-guess interpretation labeled "(Recommended)"

Then STOP. Do not partially revise.

### 2c. Verify before adopting

For each `# COMMENT:` that suggests a design change:

1. Check it against `CLAUDE.md` and `STATE.md` constraints. Does it
   conflict with an existing architecture decision?
2. If verifiable against the codebase, grep `repomix-output.xml` to
   confirm the suggestion is technically sound.
3. If it contradicts a prior architecture decision in `STATE.md`, do not
   silently adopt it — call `AskUserQuestion` with:  

```
⚠️ Conflict: Your comment on Phase 2 suggests [X], but STATE.md
records an architecture decision for [Y] (decided on <date>).
```

Options to present:
- A) Revise the plan to follow [X] — this overrides the prior decision
- B) Keep [Y] — I'll note why in the plan

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
- Offer an alternative via `AskUserQuestion`  
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

---

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

**Data flow diagram** — How does data move through the system?
Use when the feature is a pipeline or transformation chain and the
human needs to see inputs, outputs, and intermediate states.

Label every arrow with the **domain type** that flows through it — the
Pydantic model name, dataclass, or `Result[T, E]` — not a Python
primitive. `RawCapture` is correct; `dict` is not.

```
raw email ──▶ extract() ──▶ RawCapture ──▶ summarize() ──▶ Summary
                                                             │
                              AuditEntry ◀── classify() ◀────┘
                                  │              │
                                  ▼              ▼
                             audit_log      Vault/Projects/
```

**Layer diagram** — What sits on top of what?

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

**Activity / decision-flow diagram** — What's the decision logic?

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

### 3b. Show existing context, not just the new feature

Draw existing components as boxes with a note like `(exists)`. Include
future features that will depend on what's being built as dashed boxes.

```
┌─────────────┐     ┌──────────────┐     ┌ ─ ─ ─ ─ ─ ─ ┐
│  Config      │────▶│  Storage     │─ ─ ▶  Embeddings    
│  (exists)    │     │  (this plan) │     │  (Phase 4)    │
└─────────────┘     └──────────────┘     └ ─ ─ ─ ─ ─ ─ ┘
```

### 3c. Label every arrow

Unlabeled arrows are ambiguous. Every connection must say what flows
through it — a function call, a data type, an event, a file path.

### 3d. Scale to complexity

Simple features → one diagram, 5–10 lines.
Complex features → 2–3 diagrams of different types.
Do not draw diagrams for decoration.

### 3e. Present and wait — using AskUserQuestion  

Output the diagram(s), then call `AskUserQuestion` with:  

```
📐 Architecture for "$FEATURE" — does this match your mental model?
```

Options:
- A) Yes, this is correct — proceed to writing the plan (Recommended)
- B) [specific thing looks wrong] — describe the correction
- C) I need to see a different diagram type first

Do not proceed to Step 4 until the human selects option A or provides
a correction. If they provide a correction: redraw, present again,
repeat the `AskUserQuestion` call. Do not skip this gate on revision
runs — if a `# COMMENT:` annotation changed the architecture, update
the diagram and gate again.

### 3f. Mark extension points on the diagram

For every component this plan introduces, mark whether it is open or closed to extension:
- `[extensible: registry]` — new variants can be added by registering a new class
- `[extensible: config]` — behavior changes through config/yaml, no code needed
- `[extensible: protocol]` — implements a Protocol; callers don't depend on the concrete class
- `[closed]` — adding a variant requires modifying this file

Any component marked `[closed]` that the spec implies will need variants must be
flagged as a design question before the plan is written.

---

## Step 4 — Write the plan

Write the plan to `docs/plans/$FEATURE.md` using this structure:

```
# Plan: <Feature>
_Last updated: <date>_
_Status: [ ] pending | [~] in progress | [x] done_

## Architecture

[Paste the confirmed diagram(s) from Step 3 here, inside a fenced
code block.]

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
- [ ] [Specific, runnable verification]
- [ ] [Another check]

**Status**: [ ] pending

---

### Phase 2 — <Short name>
[same structure]

---

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
- If a phase introduces a new handler, classifier, or processing step, Phase 1
  of that feature must define the Protocol (the socket) as a standalone step —
  before any concrete class. The interface is the deliverable, not the first
  working implementation.
- No phase may implement behavior hardcoded to a specific source type, AI
  provider, or output format without flagging it explicitly as known coupling
  in the phase's Notes.

---

## Step 5 — Verification

After writing the plan, critically review it:

1. Identify potential problems (missing steps, untestable phases, scope creep, dependency gaps), check if edge cases outlined in `docs/research/$FEATURE.md` have been accounted for, and if there is any unintentional violation of cross-phase constraints listed in `STATE.md`
2. Verify whether each problem is real or a false alarm
3. For each real problem that requires a design decision, call `AskUserQuestion` with the options — do not silently resolve it
4. Verify that every file, method, or dependency referenced in the plan actually exists. If something critical is missing, flag it before writing
5. Review all the Technical Debts recorded in `STATE.md` and see if the current plan should address any of them. If there is a potential task emerge, call `AskUserQuestion` with the options to also include that in the plan. If user confirm, adjust the plan accordingly

Only revise the plan after the human responds to any open questions raised in this step.

---

## Step 6 — Confirm

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
