---
description: Generate a phased implementation plan from research findings. Writes to plans/<feature>.md. Never writes code.
allowed-tools: Read, Bash(find:*), Bash(cat:*)
argument-hint: <feature>
---

# Planning Task

Your ONLY job in this session is to produce a written plan. Do NOT write or modify any code.

## Input

Feature to plan: $ARGUMENTS

The feature name is used as the filename slug.

## Step 1 — Load all inputs

Your plan must synthesize two sources. Read both before writing anything.

**Source 1 — The spec (from this conversation):**
The human has pasted implementation details into the conversation before running this command. This is your primary source of truth for what to build — the exact files, function signatures, and design decisions already made. Do not invent or assume anything the spec has already decided.

**Source 2 — Research findings:**
Check if `research/$FEATURE.md` exists.

- **If it exists**: Read it fully. It tells you what already exists in the codebase, what interfaces to conform to, and what the reference project does. Your plan must connect the spec to these real interfaces — not imagined ones.
- **If it does not exist**: This is a green-field step with no existing dependencies to research. Proceed using the spec alone, but note this clearly at the top of the plan.

**Source 3 — Existing plan:**
Also check if `plans/$FEATURE.md` already exists. If it does, read it — you will update it, not overwrite it from scratch. Preserve completed phases and existing annotations.

## Step 2 — Check for human annotations

If `plans/$FEATURE.md` already exists, scan it for any lines starting with `# QUESTION:` or `# COMMENT:`. These are annotations left by the human during review.

For each annotation found:
- Address it explicitly in your revision
- Mark it as resolved by changing the prefix to `# RESOLVED:`

Do not proceed to output a new plan if there are unresolved `# QUESTION:` annotations. Instead, output:

```
⚠️ Unresolved questions found in plans/$FEATURE.md:
- [list each question]

Please answer these in the file and run /plan $FEATURE again.
```

## Step 3 — Write the plan

Write the plan to `plans/$FEATURE.md` using this structure:

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
✅ Plan written → plans/$FEATURE.md

Phases: <count>
Open questions: <count>

NEXT STEP — Review the plan:
  - Add "# QUESTION: ..." for anything unclear
  - Add "# COMMENT: ..." to redirect the approach
  - When satisfied, run: /implement $FEATURE
```

Do NOT proceed to implementation. Your job ends here.
