# Plan: Phase 2-CL — Classify Component (pure function)
_Last updated: 2026-06-08_
_Status: [x] complete — merged 2026-06-08_
_ID prefix: P2-CL_
_Behavior IDs: P2-CL-01 through P2-CL-06_

---

## Architecture

### Q1 — What happens inside
_(from `docs/1_design/phase2/classify.md`)_

```
# Classify Component — What Happens Inside
Scope: Shows what the function does from receiving inputs to returning a result.
       Does NOT cover who calls it or what happens after (see Q2 below).

How to read this:
  Boxes  = steps the function takes, in order
  Arrows = what happens next
  Forks  = a decision with two outcomes

        title, summary, tags,
        valid_destinations
                │
                ▼
     ┌──────────────────────┐
     │ Load prompt template  │
     │ from classify.yaml    │
     │ via Prompt Loader     │
     └──────────┬───────────┘
                │
                ▼
     ┌──────────────────────┐
     │ Fill in the four      │
     │ placeholders: title,  │
     │ summary, tags, and    │
     │ destinations list     │
     └──────────┬───────────┘
                │
                ▼
     ┌──────────────────────┐
     │ Call the AI via the   │
     │ configured provider   │
     └──────────┬───────────┘
                │
         ┌──────┴──────┐
         │             │
       FAILED        ANSWERED
         │             │
         ▼             ▼
     Failure       ┌──────────────────────┐
     (retryable)   │ Parse JSON response   │
                   └──────────┬───────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
               INVALID JSON        VALID JSON
                    │                   │
                    ▼                   ▼
                Failure          Validate fields:
                (retryable)      target_type must be
                                 "project" or "domain"
                                      │
                             ┌────────┴────────┐
                             │                 │
                         INVALID           VALID
                             │                 │
                             ▼                 ▼
                         Failure           Success
                         (retryable)       (ClassifyResult)

ClassifyResult: target_type, target_name, confidence, reasoning
```

### Q2 — How it connects
_(from `docs/2_specs/phase2/classify.md`)_

```
# Classify Step — How It Connects
Scope: Shows what Classify Step touches and what passes between them.
       Does NOT show internal steps (see Q1 for that).

How to read this:
  Center box     = the component being built (this spec)
  Solid boxes    = already built, ready to use
  Dashed boxes   = planned, not built yet
  Arrow labels   = what passes between them


          ┌──────────────────────┐
          │  Prompt Template     │
          │  The question to     │
          │  ask the AI          │
          └──────────┬───────────┘
                     │ fills in title,
                     │ summary, tags,
                     │ and destinations
                     ▼
   ┌─────────────────────────────────────────────────┐
   │               CLASSIFY STEP                     │
   │  Asks the AI which vault folder this note       │
   │  belongs in. Returns the answer + confidence.   │
   └──────┬──────────────────────────────────┬───────┘
          │                                  │
          │ sends the filled          receives answer
          │ question                  (destination,
          ▼                           confidence, reason)
  ┌───────────────────┐       ┌────────────────────────┐
  │   AI Service      │       │  Destinations List     │
  │   Picks the       │       │  The full list of      │
  │   destination     │       │  vault folders to       │
  │   and explains    │       │  choose from            │
  └───────────────────┘       └────────────────────────┘

                     │
                     │ Classification Answer
                     │ or Retry Signal
                     ▼
          ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
          │     Classify Pipeline        │
          │     (future spec)            │
          │  Acts on the answer: logs    │
          │  the decision, checks        │
          │  confidence, moves the note  │
          └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

### Q3 — Why build it this way

```
# Classify Component — Why Build It This Way
Scope: Rules and constraints that shaped every step of this design.
       Shows which existing rules each step must follow, and why.
       Does NOT show internal logic (see Q1) or connections (see Q2).

How to read this:
  Center column    = the steps from Q1, in order
  Side boxes       = rules that apply to that step
  Lines            = which rule applies where
  ┄┄┄ borders      = rules that apply to the whole function


  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
  ┊  WHOLE FUNCTION RULE: Pure function — no file writes,      ┊
  ┊  no audit log calls, no global config object.             ┊
  ┊  The pipeline that calls this handles all of those.       ┊
  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄

        title, summary, tags,
        valid_destinations (plain string — built by pipeline)
                │
                ▼
┌───────────────────────────────┐    ┌──────────────────────────────┐
│  Load and fill in the         │◄───│ RULE: Prompts live in config  │
│  question template            │    │ files, not in code.          │
│                               │    │ Any prompt text written       │
│                               │    │ directly in Python triggers   │
│                               │    │ a hard warning. Use the       │
└──────────────┬────────────────┘    │ template loader instead.      │
               │                     │                               │
               │            also ───►│ SAFETY NET: If a variable     │
               │                     │ name is misspelled at the     │
               │                     │ call site, the template engine│
               │                     │ raises an error immediately.  │
               │                     │ Catch it and return a         │
               │                     │ non-retryable failure — never │
               │                     │ let it crash the caller.      │
               │                     └───────────────────────────────┘
               │
               ▼
┌───────────────────────────────┐    ┌──────────────────────────────┐
│  Ask the AI which folder      │◄───│ RULE: AI calls go through    │
│  the note belongs in          │    │ the provider factory.        │
│                               │    │ Never call the AI library     │
│                               │    │ directly — routing, model     │
│                               │    │ selection, and provider swap  │
└──────────────┬────────────────┘    │ all happen in the factory.   │
               │                    │                               │
               │           also ───►│ RULE: Config is passed in,    │
               │                    │ not read from a global object. │
               │                    │ This keeps the function        │
               │                    │ testable without a real vault  │
               │                    │ or environment on disk.        │
               │                    └──────────────────────────────┘
               │
        ┌──────┴──────┐
        │             │
      FAILED       ANSWERED
        │             │
        ▼             ▼
  ┌──────────┐   Parse JSON, then
  │ Failure  │   check answer shape
  │(retryable│        │
  │— all 3   │   ┌────┴────┐
  │ failure  │   │         │
  │ paths    │ INVALID   VALID
  │ work     │   │         │
  │ the same │   ▼         ▼
  │ way)     │ Failure  ┌────────────────────┐
  └──────────┘(retry.)  │ Wrap answer in     │
                        │ a typed result     │
                        └────────────────────┘
        │                        │
        ▼                        ▼
┌──────────────────────────────────────────────────────────────┐
│  RULE (all failure paths): Every failure must carry a reason  │
│  and a context dictionary — these are required fields, not    │
│  optional. Tests that omit the context dictionary will fail   │
│  at construction time, not at assertion time.                 │
│                                                              │
│  The "retryable" flag is meaningful — it tells the pipeline   │
│  "try again with the same inputs." But the retry loop itself  │
│  lives in the pipeline, not here. (TD-048)                   │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│  RULE (result shape): The answer container uses the same      │
│  frozen data record pattern as every other pipeline result    │
│  in this codebase — four fields, immutable, no validation     │
│  logic inside the record itself. Validation happens in the    │
│  step above; the record just holds what passed.              │
│                                                              │
│  Not a Pydantic model — plain Python data record. Consistent  │
│  with how Capture Result and Reconcile Result are built.      │
└──────────────────────────────────────────────────────────────┘

┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
┊  PIPELINE HANDOFF RULES (not this function's job):          ┊
┊  • Build the destinations string before calling this         ┊
┊    (format the project list, pass it in as plain text)       ┊
┊  • Log the AI decision after this returns                    ┊
┊  • Check the confidence score and route accordingly          ┊
┊  • Implement retry loop for retryable failures               ┊
┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
```

---

## Approach

This plan builds two things in strict order: first the typed result container (`ClassifyResult`), then the function that produces it (`classify()`). Both live in the same new file (`src/pipelines/classify.py`). All six behavior checks (P2-CL-01 through P2-CL-06) are verified by unit tests before the phase closes — tests use a mock AI provider, so no real API key or vault is needed.

The implementation follows TDD: write a failing test for each behavior, then write the minimum code to make it pass. No phase moves forward until its tests are green.

---

## Phases

### Phase 1 — ClassifyResult dataclass

**Goal:** Create a typed container that holds the four fields the AI returns. This is just a data structure — no logic, no validation. The function that uses it (Phase 2) is where validation happens.

**Why first?** Phase 2 needs this type to exist before `classify()` can reference it. It is also the simplest thing to test independently — construct it, check that fields are readable, verify that changing a field after construction raises an error (frozen).

**Extension point:** `[closed]` — adding a fifth field (e.g. `alternative_targets`) would need spec alignment with the pipeline. Acceptable; the spec locks four fields.

**Design:**

```
NEW FILE: src/pipelines/classify.py

@dataclass(frozen=True)
class ClassifyResult:
    target_type: str        # "project" or "domain" — validated by classify(), not here
    target_name: str        # exact folder name the AI chose
    confidence: float       # 0.0 – 1.0
    reasoning: str          # one-sentence explanation from the AI

Pattern matches:
  capture.py   lines 62–70  →  SummarizeResult, MetadataResult
  reconcile.py line 39      →  ReconcileResult
```

**Steps:**

1. Create `src/pipelines/classify.py`. Add the standard module-level imports: `from __future__ import annotations`, `import json`, `from dataclasses import dataclass`. Do not import `CONFIG` — it must not appear at module scope in this file.
2. Define `ClassifyResult` using `@dataclass(frozen=True)` with the four fields listed above. Add type annotations. No `__post_init__`, no property, no method.
3. Write a test file at `tests/test_pipelines/test_classify.py`. Add two test cases:
   - `test_classify_result_constructs` — confirms all four fields are readable after construction.
   - `test_classify_result_is_frozen` — confirms that assigning to any field after construction raises `FrozenInstanceError`.

**Files to modify:**

- `src/pipelines/classify.py` — create new file; add `ClassifyResult`
- `tests/test_pipelines/test_classify.py` — create new test file; two test cases

**Test criteria:**

- [ ] `ClassifyResult(target_type="project", target_name="Alpha", confidence=0.9, reasoning="Meeting notes.")` constructs without error and all four fields are accessible.
- [ ] `result.target_type = "domain"` raises `FrozenInstanceError` (or `dataclasses.FrozenInstanceError`).
- [ ] Construction succeeds even with an invalid `target_type` value (e.g. `target_type="inbox"`) — the dataclass does NOT validate; that is `classify()`'s job.
- [ ] `uv run pytest tests/test_pipelines/test_classify.py` passes.

**Status:** [x] complete — 2026-06-08

---

### Phase 2 — classify() async function

**Goal:** Build the single public entry point for the AI classification decision. It takes four string inputs plus the active config, calls the AI, parses and validates the response, and returns either `Success(ClassifyResult)` or `Failure(recoverable=True, ...)`. It never raises.

**Extension point:** `[extensible: config]` — switching AI providers requires only a `config.yaml` change (`providers.classify`). No code change in this file.

**Design:**

```
FUNCTION SIGNATURE:
  async def classify(
      title: str,
      summary: str,
      tags: str,           # caller serializes list[str] before calling
      valid_destinations: str,   # caller calls format_for_prompt() before calling
      config: MainConfig,  # passed explicitly — NOT the CONFIG singleton
  ) -> Result[ClassifyResult]:

IMPORTS NEEDED AT MODULE TOP:
  from llm.prompt_loader import PROMPTS
  from llm.provider import get_provider
  from core.result import Success, Failure, Result
  from core.config import MainConfig

BODY — 7 steps matching Q1 exactly:

  Step 1: Render the prompt
    try:
        system, user = PROMPTS["classify"].render(
            title=title, summary=summary,
            tags=tags, valid_destinations=valid_destinations,
        )
    except Exception as exc:
        return Failure(
            error=f"classify render error: {exc}",
            recoverable=False,        # code bug, not a transient failure
            context={"stage": "classify", "title": title},
        )

  Step 2: Get AI provider
    provider = get_provider("classify", config)

  Step 3: Call AI (async)
    response = await provider.complete(system, user)

  Step 4: Handle provider failure
    if isinstance(response, Failure):
        return Failure(
            error=response.error,
            recoverable=True,
            context={"stage": "classify", "title": title},
        )

  Step 5: Parse JSON
    try:
        data = json.loads(response.value.content)
    except json.JSONDecodeError as exc:
        return Failure(
            error=f"classify JSON parse error: {exc}",
            recoverable=True,
            context={
                "stage": "classify",
                "title": title,
                "raw": response.value.content[:200],
            },
        )

  Step 6: Validate target_type
    if data.get("target_type") not in {"project", "domain"}:
        return Failure(
            error=f"classify invalid target_type: {data.get('target_type')!r}",
            recoverable=True,
            context={"target_type": data.get("target_type"), "title": title},
        )

  Step 7: Return success
    return Success(ClassifyResult(
        target_type=data["target_type"],
        target_name=data["target_name"],
        confidence=float(data["confidence"]),
        reasoning=data["reasoning"],
    ))
```

**Planner note — render() try/except:** The Jinja2 engine used by `prompt_loader.py` is `StrictUndefined`. A typo in any variable name passed to `.render()` raises `UndefinedError` at call time. This violates C-12 if uncaught. The try/except in Step 1 catches this. `recoverable=False` because a render error is a code bug (wrong variable name), not a transient AI failure. Retrying with the same inputs will not fix it. (Research doc: "Jinja2 render exception not caught".)

**Steps:**

1. Add the four imports to the top of `src/pipelines/classify.py` (below the `from __future__` line). Do not import `CONFIG` — import `MainConfig` as a type hint only.
2. Write `async def classify(...)` with the exact signature shown above. Implement all 7 steps in order. No shortcuts — do not combine steps.
3. Before implementing, write the test cases listed below (TDD: RED first). Each test mocks the AI provider so no real API key is needed.
4. Run tests — they should fail (RED). Implement the function. Run tests again — they should pass (GREEN).

**How to mock the AI provider in tests:**

```python
# Pattern from capture pipeline tests — mock get_provider to return a
# controlled object whose .complete() returns what the test needs.

from unittest.mock import AsyncMock, patch, MagicMock
from core.result import Success, Failure, LLMResponse

# A mock provider that returns valid JSON:
mock_provider = MagicMock()
mock_provider.complete = AsyncMock(return_value=Success(LLMResponse(
    content='{"target_type": "project", "target_name": "Alpha", '
            '"confidence": 0.9, "reasoning": "Meeting notes."}',
)))

# Patch get_provider so classify() gets the mock:
with patch("pipelines.classify.get_provider", return_value=mock_provider):
    result = await classify(
        title="Q1 Review",
        summary="Financial overview",
        tags="finance, quarterly",
        valid_destinations="Projects:\n  - Alpha\nDomains:\n  - Finance",
        config=some_main_config,
    )
```

Build a minimal `MainConfig` fixture using `MainConfig.model_construct(...)` or by creating a tiny `config.yaml` in `tmp_path`. Check how existing pipeline tests build config fixtures — mirror that pattern exactly.

**Test cases to write (one test function per scenario):**

| Test function name | Scenario | Expected result | Behavior ID |
|---|---|---|---|
| `test_classify_valid_json_returns_success` | Mock returns well-formed JSON with `target_type="project"` | `Success(ClassifyResult)` with all four fields matching JSON values | P2-CL-01 |
| `test_classify_provider_failure_returns_retryable` | Mock `.complete()` returns `Failure(...)` | `Failure(recoverable=True)` | P2-CL-02 partial |
| `test_classify_bad_json_returns_retryable` | Mock `.complete()` returns `Success` with content `"Sorry, cannot help"` | `Failure(recoverable=True)` | P2-CL-02 |
| `test_classify_invalid_target_type_returns_retryable` | Valid JSON but `target_type="inbox"` | `Failure(recoverable=True)` | P2-CL-03 |
| `test_classify_never_raises` | All failure scenarios above | No exception raised in any case | P2-CL-04 |
| `test_classify_no_prompt_in_source` | Static check of `src/pipelines/classify.py` source text | No f-string or string literal containing "classify" prompt text | P2-CL-05 |
| `test_classify_no_direct_ai_import` | Static check of `src/pipelines/classify.py` imports | No import of `ClaudeProvider`, `anthropic`, or any provider class directly | P2-CL-06 |

**Important — Failure context field is required:** `Failure` has three required fields: `error: str`, `recoverable: bool`, `context: dict`. Tests that construct `Failure(error="...", recoverable=True)` without `context` will raise `TypeError` at construction time, not at assertion time. Always pass `context={}` at minimum. (Research doc: "Failure requires all three positional fields.")

**Files to modify:**

- `src/pipelines/classify.py` — add `classify()` function (Phase 1 created this file)
- `tests/test_pipelines/test_classify.py` — add 7 test functions (Phase 1 created this file)

**Test criteria:**

- [ ] `test_classify_valid_json_returns_success` passes — `Success(ClassifyResult)` with correct field values (P2-CL-01).
- [ ] `test_classify_provider_failure_returns_retryable` passes — `Failure(recoverable=True)` (P2-CL-02 partial).
- [ ] `test_classify_bad_json_returns_retryable` passes — `Failure(recoverable=True)` (P2-CL-02).
- [ ] `test_classify_invalid_target_type_returns_retryable` passes — `Failure(recoverable=True)` (P2-CL-03).
- [ ] `test_classify_never_raises` — all scenarios run without an exception being raised (P2-CL-04).
- [ ] `test_classify_no_prompt_in_source` — source file contains no prompt string (P2-CL-05).
- [ ] `test_classify_no_direct_ai_import` — source file imports only from `llm.prompt_loader`, `llm.provider`, `core.result`, `core.config` (P2-CL-06).
- [ ] `uv run pytest tests/test_pipelines/test_classify.py` — all tests pass, no warnings other than the pre-existing `RuntimeWarning` in `test_claude_cli_provider.py`.
- [ ] `uv run pytest tests/` — full suite still green (956+ tests).
- [ ] `uv run ruff check src/pipelines/classify.py` — no lint errors.

**Status:** [x] complete — 2026-06-08

---

## Handoff Notes (for the Classify Pipeline spec author)

These are things this plan deliberately leaves out — the classify pipeline spec must address them.

**1. Building `valid_destinations` (one line, thread-safe):**
The pipeline calls `format_for_prompt(ProjectRegistry(groups=live_registry.get_groups()))` to build the string before calling `classify()`. This is a one-liner. `format_for_prompt` is in `src/vault/registry.py:151`. `LiveRegistry.get_groups()` returns a `dict` — wrapping it in `ProjectRegistry(groups=...)` gives the object `format_for_prompt` expects. Thread-safe because `get_groups()` returns a snapshot copy. Do not call `format_for_prompt` inside `classify()` — the function signature takes a plain string by design.

**2. Audit log (C-13, non-negotiable):**
`classify()` is a pure function and does NOT call `core.audit.write()`. The pipeline wrapper must call `audit.write(...)` after `classify()` returns. Every pipeline stage that makes an AI decision must produce an audit entry. Missing this means Phase 8 (Daily Briefing) will have silent gaps.

**3. Retry loop (TD-048):**
`classify()` returns `Failure(recoverable=True)` for all three transient failure modes (provider error, bad JSON, invalid `target_type`). The `recoverable=True` flag signals "retry with the same inputs is valid." But no retry infrastructure exists in `pipelines/` yet. The pipeline spec must implement retry count + backoff. Until that is built, `recoverable=True` is informational only. TD-048 tracks this.

**4. `tags` serialization format:**
`classify()` accepts `tags: str`. The pipeline must convert `NoteMetadata.tags: list[str]` to a string before calling. Suggested format: `", ".join(sorted(note.metadata.tags))` — deterministic and human-readable. The prompt template accepts any string, but consistency matters for AI performance. Commit to one format in the pipeline spec.

**5. Verify config entries before the pipeline spec:**
Confirm `config/config.yaml` has both:
- `providers.classify` entry (should default to `"claude"` — verify at `config.py:179`)
- `thresholds.pipelines.classify` entry with `auto` and `suggest` values (if absent, `for_pipeline("classify")` falls back to `global_` defaults — decide whether to rely on the fallback or require an explicit entry).

---

## Open Questions

None — all six assumptions are validated by the research doc. The spec is ready for implementation.

---

## Out of Scope

- Full classify pipeline (`pipelines/classify.py` orchestration: classify → audit → confidence gate → route → move) — separate spec, next component.
- Confidence Gate — reads thresholds, routes to auto/review/inbox. Separate component.
- Route — verifies `target_name` folder exists on disk. Separate component.
- Move — physically moves the note. Separate component.
- Audit log entry — belongs to the pipeline wrapper, not `classify()`.
- Retry loop — pipeline's responsibility. (TD-048)
- `kms classify` CLI command — requires the full pipeline first.
- `target_name` existence validation — Route's job. `classify()` returns whatever the AI says; Route checks if the folder actually exists.
