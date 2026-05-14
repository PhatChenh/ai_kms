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

## Step 1.5 — Capture the discussion log for the previous phase

Check `docs/plans/$FEATURE.md` for the most recently completed phase
(status `[x] done`). If one exists, scan back through the conversation from the moment this phase started
until now. Extract every exchange where the human asked a question and
you gave a conceptual explanation — code walkthroughs, "why does this
work this way", design rationale, terminology, anything that was new
knowledge to them.

Extract all conceptual exchanges and append them to
`docs/discussions/$FEATURE.md` (create if absent) before doing anything else.

Append a new section for this phase in this exact format:

---

## Phase <N> — <Phase name>
_Completed: <date>_

### Exchanges

**Q:** <question>
```python
# optional: the code the human pointed to
```

**A:** <explanation>
```python
# optional: minimum snippet illustrating the concept
```
_Key concept: <label>_

**Q:** ...

Rules:
- Include ALL conceptual exchanges, even ones that seem minor.
  Small confusions are often the richest flashcard material.
- Do NOT include exchanges that were purely procedural
  ("run this command", "the test passed") — only knowledge transfer.
- Paraphrase the human's question into a clean, standalone sentence.
  Remove "wait, so..." and "I don't get why..." — keep the core question.
- Keep answers complete but cut throat-clearing ("Great question —
  the reason is..."). Start directly with the explanation.
- The `Key concept:` tag is required on every exchange — it is the
  anchor the flashcard command uses to decide card type and framing.
- If the exchange was triggered by a specific piece of code, attach the
minimum snippet that illustrates the concept being explained — typically
3–10 lines. Trim aggressively: remove lines that aren't load-bearing for
the concept. Label it clearly:

  Do NOT attach a snippet when:
  - The exchange was pure theory with no code anchor
  - The snippet would require the full function to make sense
  - The concept is better stated in words alone

  The snippet is context for the flashcard command — not the thing being
  tested.

If no phase is marked done yet, skip this step.

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
- Every public function gets a docstring: one-line summary, `Args:`,
  `Returns:`, and `Raises:` if relevant. Keep it factual — describe the
  contract, not the implementation.
- Inline comments explain **why**, not what. Comment when a decision was
  non-obvious, a constraint forced this shape, or a future reader would
  ask "why not just...". Never restate what the next line does.

If you encounter something the plan did not anticipate:
- DO NOT improvise a large change
- Make a note in the plan under a `## Surprises` section
- Implement a minimal solution to unblock the phase, then flag it for human review

## Step 4 — Write tests (if not already written)

If the **Test criteria** section requires running test files, check whether the test files named in the section already exist.

- If they do not exist: **write them now**, using the test function names
  exactly as listed in the plan. Each function must be independently
  runnable — no shared state unless a fixture is explicitly warranted.
  Follow the project's test conventions (see CLAUDE.md).
- If they already exist: skip creation, move to Step 5.

Do not proceed to implementation if any named test file has a placeholder
or `pass` body — fill it in completely.

## Step 5 — Verify the phase

Run the test criteria from the plan. Each must pass before marking the
phase done.

If a test fails:
- Fix the issue within the scope of this phase
- Re-run the check
- Do not proceed until all criteria pass

## Step 6 — Update the plan file

After all test criteria pass, update `docs/plans/$FEATURE.md`:

1. Change the phase status from `[ ] pending` to `[x] done`
2. Add an implementation note directly under the phase:

```
**Completed**: <date>
**Notes**: [What was actually done — any deviations from the plan, any surprises, any tech debt introduced]
```

3. Change the top-level `_Status_` field to `[~] in progress` (or `[x] done` if this was the last phase)

## Step 7 — Hard stop

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
