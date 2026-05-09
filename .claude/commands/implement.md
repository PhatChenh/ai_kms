---
description: Execute the next pending phase from docs/plans/<feature>.md. Updates plan progress after each phase. Waits for human verification before continuing.
allowed-tools: Read, Edit, Bash(python:*), Bash(pytest:*), Bash(uv:*), Bash(find:*), Bash(cat:*)
argument-hint: <feature>
---

# Implementation Task

You implement ONE phase at a time. You stop after each phase and wait for human verification.

## Input

Feature to implement: $ARGUMENTS

## Step 1 — Load and validate the plan

First, read `CLAUDE.md`. It contains current build progress and project conventions — what commands to use, what patterns are established, what has already been decided. Your implementation must conform to these conventions.

Then read `docs/plans/$FEATURE.md`.

- **If it does not exist**: Stop and output:
  ```
  ❌ No plan found for "$FEATURE".
  Run /plan $FEATURE first.
  ```

- **If any `# QUESTION:` annotations exist** (unresolved): Stop and output:
  ```
  ⚠️ Plan has unresolved questions. Resolve them before implementing.
  Run /plan $FEATURE to address them.
  ```

- **If all phases are `[x] done`**: Stop and output:
  ```
  ✅ All phases complete for "$FEATURE". Nothing left to implement.
  ```

## Step 2 — Identify the current phase

Find the first phase with status `[ ] pending`. That is your target phase. Do not skip ahead, do not implement multiple phases at once.

Output before starting:
```
▶ Implementing: Phase <N> — <Phase name>
  Goal: <phase goal>
  Files: <list of files to modify>
```

## Step 3 — Implement the phase

Execute only the steps listed under the current phase. Follow the plan exactly.

Code quality rules — these are non-negotiable:
- No unnecessary comments or docstrings that restate what the code does
- Strict typing — no `Any` or untyped returns where avoidable
- No silent failures — use the project's `Result` type pattern where it applies
- Functions do one thing — if you find yourself bundling two concerns, split them
- Run typecheck after each file edit: `uv run mypy <file>` or whatever the project's typecheck command is

If you encounter something the plan did not anticipate:
- DO NOT improvise a large change
- Make a note in the plan under a `## Surprises` section
- Implement a minimal solution to unblock the phase, then flag it for human review

## Step 4 — Verify the phase

Run the test criteria listed in the plan for this phase. Each criterion must pass before marking the phase done.

If a test fails:
- Fix the issue within the scope of this phase
- Re-run the check
- Do not proceed until all criteria pass

## Step 5 — Update the plan

After all test criteria pass, update `docs/plans/$FEATURE.md`:

1. Change the phase status from `[ ] pending` to `[x] done`
2. Add an implementation note directly under the phase:

```
**Completed**: <date>
**Notes**: [What was actually done — any deviations from the plan, any surprises, any tech debt introduced]
```

3. Change the top-level `_Status_` field to `[~] in progress` (or `[x] done` if this was the last phase)

## Step 6 — Hard stop

After updating the plan, output:

```
✅ Phase <N> complete — docs/plans/$FEATURE.md updated

What was done:
- [bullet summary of changes made]

Files modified:
- [list]

Please verify before continuing:
- [restate the test criteria from the plan]

When verified, run: /implement $FEATURE to continue with Phase <N+1>.
```

Then STOP. Do not continue to the next phase automatically. Wait for the human to run the command again.
