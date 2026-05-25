---
description: Execute the next pending phase from docs/plans/<feature>.md. Updates plan progress after each phase. Waits for human verification before continuing.
allowed-tools: Read, Edit, Bash(python:*), Bash(pytest:*), Bash(uv:*), Bash(find:*), Bash(cat:*)
argument-hint: <feature>
---

# Implementation Task

> The global HITL behavioral contract in CLAUDE.md applies in full.
> The specific rules below govern the implementation workflow only.

You implement ONE phase at a time. You stop after each phase and wait for human verification.

## Input

Feature to implement: $ARGUMENTS

---

## Step 1 — Load and validate the plan

Read these in order:

**1. `CLAUDE.md`** — Read in full. Current build progress and project conventions — what commands to use, what patterns are established, what has already been decided. Your implementation must conform to these conventions.

**2. `STATE.md`** — If it exists, read it in full.  
- Any "Architecture Decisions" are hard constraints on how you write code — not suggestions.
- Any "Cross-Phase Constraints" must be respected even if the plan file doesn't mention them.
- Any relevant "Technical Debt" entries should be noted — don't introduce more of the same.

**3. `docs/plans/$FEATURE.md`** — Read in full.

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

---

## Step 2 — Identify the current phase

Find the first phase with status `[ ] pending`. That is your target phase.
Do not skip ahead, do not implement multiple phases at once.

---

## Step 2b — Pre-implementation confirmation gate  

Before writing a single line of code or test, call `AskUserQuestion` with:

```
▶ Ready to implement: Phase <N> — <Phase name>
  Goal: <phase goal>

  Files I will create or modify:
  - <file> — <what changes>
  - <file> — <what changes>

  Test criteria I will implement against:
  - <criterion 1>
  - <criterion 2>
```

Options:
- A) Proceed with implementation (Recommended)
- B) I want to adjust the scope before starting — describe adjustment
- C) Skip this phase and move to the next

Do not write any code until the human selects option A.

This gate exists because writing code is irreversible in ways that planning
is not. One confirmation saves a phase of wrong-direction work.

If the test criteria in the plan are ambiguous — where two reasonable
implementations would both satisfy them — surface the ambiguity here via
an additional `AskUserQuestion` call before proceeding. Do not silently
pick an interpretation.

---

## Step 3 — Implement with TDD (Red-Green-Refactor)

**Iron law: No production code without a failing test first.**

Wrote code before the test? Delete it. Start over. No exceptions.

For each behavior the phase requires, follow this cycle:

### 3a. RED — Write one failing test

Write a single test that describes the next behavior to implement. Write the test from the spec's required behavior (inputs → expected outputs), not from your intended implementation. If you already know what the implementation will look like before finishing the test, stop — you're writing tests after.

Requirements:
- Tests one behavior only. If the name contains "and", split it.
- Clear name that describes the expected behavior, not implementation.
- Uses real code, not mocks, unless external I/O makes it unavoidable.
- Follows the project's test conventions (see CLAUDE.md).
- Assertions must check specific expected values, not just type, presence, or non-None. A test that would pass for any non-crashing implementation is not a test.

If the plan's **Test criteria** section names specific test files/functions,
use those exact names. If a named test file already exists with placeholder
`pass` bodies, fill them in now — do not proceed with placeholders.

### 3b. Verify RED — Watch it fail

Run the test. This step is mandatory. Never skip it.

```bash
uv run pytest path/to/test_file.py::test_name -x
```

Confirm:
- The test **fails** (not errors from import/syntax problems).
- The failure message matches your expectation (feature missing, not typo).
- It fails because the production code doesn't exist yet.

Paste the actual pytest output in full. Do not paraphrase, summarize, or truncate.

If the test **passes** immediately → you're testing existing behavior.
Rewrite the test.

If the test **errors** (import failure, syntax) → fix the error,
re-run until it fails correctly for the right reason.

If the implementation you're about to write would only work for the specific inputs in this test, write a second test with different inputs first. Both must fail. Then implement the general logic.

### 3c. GREEN — Write minimal production code

Write the simplest code that makes the failing test pass. Nothing more.

- Do not add features the test doesn't require.
- Do not refactor other code.
- Do not "improve" beyond what the test demands.
- Do not anticipate future tests.
- Never hardcode return values to satisfy a test. If the implementation only works for the exact inputs in the test, it's not an implementation — it's a cheat. Production code must express the general logic, not memorize the test's expected output.

### 3d. Verify GREEN — Watch it pass

Run the test again, plus the full test suite for affected files:

```bash
uv run pytest path/to/test_file.py -x
```

Confirm:
- The new test passes.
- All existing tests still pass.
- No warnings or errors in output.

For any test that fails, paste the actual pytest output in full. Do not paraphrase or summarize.

If the new test fails → fix the production code, not the test.
If other tests broke → fix now before continuing.

### 3e. REFACTOR — Clean up (tests must stay green)

After green, and only after green:
- Remove duplication in production code.
- Improve names and structure.
- Extract helpers if warranted.
- Run the full suite after every refactor change — tests must stay green.
- Do not add new behavior during refactor.

### 3f. Repeat

Go back to 3a for the next behavior in this phase.

---

## Testing rules — non-negotiable

These apply to every test written during implementation.

### Never test mock behavior
If your assertion checks that a mock exists or a mock was called, you're
testing the mock, not the code. Delete the assertion. Test real behavior
or remove the mock.

Gate: Before asserting on any mock element, ask: "Am I verifying real
component behavior, or just that my mock is wired up?" If the latter, stop.

### Never add test-only methods to production classes
If a method exists solely for test cleanup or inspection, it doesn't belong
in the production class. Put it in test utilities (`tests/helpers/`,
`conftest.py`, or a test fixture).

Gate: Before adding any method to a production class, ask: "Is this only
called by tests?" If yes, it goes in test utilities.

### Mock with understanding, not fear
Before mocking any dependency:
1. Know what side effects the real method has.
2. Know whether your test depends on any of those side effects.
3. Mock at the lowest level that removes the slow/external part while
   preserving the behavior the test needs.

Never mock "to be safe." Never mock without tracing the dependency chain.
If mock setup exceeds 50% of the test, consider an integration test instead.

### Use complete mocks
When mocking a data structure (API response, config object, database row),
mirror the full real structure — not just the fields your immediate test
uses. Partial mocks hide structural assumptions and break silently when
downstream code accesses omitted fields.

### Recognize the red flags
Any of these mean you've gone wrong — stop and course-correct:
- Assertion checks for `*-mock` test IDs
- Methods only called in test files
- Mock setup is >50% of the test
- Test fails when you remove the mock
- Can't explain why the mock is needed
- Test passes on first run (never saw it fail)
- You're keeping deleted code "as reference"
- You're rationalizing "just this once"

---

## Code quality rules — non-negotiable

- Strict typing — no `Any` or untyped returns where avoidable
- No silent failures — use the project's `Result` type pattern where it applies
- Functions do one thing — if you're bundling two concerns, split them
- Run typecheck after each file edit: `uv run mypy <file>` or the project's typecheck command
- Every public function gets a docstring: one-line summary, `Args:`,
  `Returns:`, and `Raises:` if relevant. Describe the contract, not the
  implementation.
- Inline comments explain **why**, not what. Comment when a decision was
  non-obvious, a constraint forced this shape, or a future reader would
  ask "why not just...". Never restate what the next line does.

---

## When you encounter something the plan did not anticipate 

If you discover something mid-phase that the plan did not account for
(a missing interface, an unexpected dependency, a conflicting constraint):

1. **Stop immediately.** Do not implement a workaround.
2. Document the surprise in the plan under a `## Surprises` section:
   ```
   ## Surprises
   - Phase <N>: [what was found, why it wasn't in the plan, what it blocks]
   ```
3. Call `AskUserQuestion` with:
   - A description of what you found
   - Options: A) implement a minimal workaround and continue, B) stop and rethink this phase, C) revise the plan before continuing
   - Your recommendation labeled "(Recommended)"
4. Wait for the human's decision before writing any code for the surprise.

"Implement a minimal solution and flag it for review" is not acceptable —
it still means writing unplanned code. The human decides what happens
when the plan is wrong, not you.

---

## Step 4 — Final verification

Run the full test criteria from the plan. Every criterion must pass.

```bash
uv run pytest tests/ -x
```

If a test fails and the fix is **within this phase's scope**: fix it and re-run.

If a test fails and the fix would require **touching files outside this phase,
or reveals a flaw in the plan itself**: do not improvise. Call `AskUserQuestion`:  
- Describe the test failure and why it can't be fixed within scope
- Options: A) revise the plan before continuing, B) document as known issue and proceed, C) expand scope of this phase to include the fix
- Wait for the human's decision

Do not proceed until all test criteria pass or the human has explicitly chosen option B.

### Verification checklist

Before marking the phase done, confirm all of these:

- [ ] Every new function/method has at least one test
- [ ] Watched each test fail before writing its production code
- [ ] Each test failed for the expected reason (feature missing, not typo/import)
- [ ] Wrote minimal code to pass each test — no speculative features
- [ ] All tests pass, output clean (no warnings, no errors)
- [ ] Tests use real code — mocks only where external I/O forced it
- [ ] Edge cases and error paths are covered
- [ ] No test-only methods in production classes
- [ ] Typecheck passes on all modified files (including test files) 
- [ ] No surprises were resolved unilaterally — all were surfaced and approved
- [ ] Every new processing class implements a Protocol or ABC — no concrete-only classes
- [ ] No behavior is hardcoded to a specific source type, provider, or format without
      a # COUPLING: comment explaining why and what would be needed to generalize it

Cannot check all boxes? Something was skipped. Fix it before proceeding.

---

## Step 5 — Update the plan file

After all test criteria pass, update `docs/plans/$FEATURE.md`:

1. Change the phase status from `[ ] pending` to `[x] done`
2. Add an implementation note directly under the phase:

```
**Completed**: <date>
**Notes**: [What was actually done — any deviations from the plan, any surprises, any tech debt introduced]
```

3. Change the top-level `_Status_` field to `[~] in progress` (or `[x] done` if this was the last phase)

4. Call /update-state $ARGUMENTS

---

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
