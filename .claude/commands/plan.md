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

**5. `repomix-output.xml` — only if needed.** If resolving an annotation requires verifying an actual interface, function signature, or file that exists in the codebase, grep it surgically:
```
grep -n "<file path" repomix-output.xml | grep -i <relevant-keyword>
```
Do not read this file in full. Only pull the specific blocks you need.

## Step 2 — Check for human annotations

If `docs/plans/$FEATURE.md` already exists, scan it for any lines starting with `# QUESTION:` or `# COMMENT:`. These are annotations left by the human during review.

For each annotation found:
- Address it explicitly in your revision
- Mark it as resolved by changing the prefix to `# RESOLVED:`

Do not proceed to output a new plan if there are unresolved `# QUESTION:` annotations. Instead, output:

```
⚠️ Unresolved questions found in docs/plans/$FEATURE.md:
- [list each question]

Please answer these in the file and run /plan $FEATURE again.
```

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