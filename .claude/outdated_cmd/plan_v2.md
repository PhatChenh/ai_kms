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

## Step 3 — Write the plan

Write the plan to `docs/plans/$FEATURE.md` using this structure:

```
# Plan: <Feature>
_Last updated: <date>_
_Status: [ ] pending | [~] in progress | [x] done_

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

## Step 4 — Confirm

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
