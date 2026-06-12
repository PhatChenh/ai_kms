# Classify Component — Design Doc

**Feature name:** Classify (Python function)
**Date:** 2026-06-08
**ID prefix:** P2-CL
**Behavior inventory prefix:** P2-CL (entries P2-CL-01 through P2-CL-06 in `docs/system_behavior/behavior_inventory.yaml`)

---

## What this is (plain English)

After Phase 1 captures a note, the note sits in the inbox with a summary and tags. The Classify component is the AI call that reads those fields and picks a destination folder — "this note belongs in Project Alpha" or "this note belongs in the Finance domain."

It takes four pieces of information as input, asks the AI to choose a destination, and returns the answer together with a confidence score and a one-sentence reason. If the AI response is garbled or structurally invalid, it returns a retryable error instead of crashing.

Classify is a pure function — it does not move files, write to disk, or call the audit log. It only reads the prompt, calls the AI, and returns a result. Everything else (moving the note, logging the decision) is the pipeline's job.

---

## How it fits in

**Existing code reused — no new dependencies introduced:**

- `llm/prompt_loader.py` — `PROMPTS["classify"]` loads `src/prompts/classify.yaml` and exposes a `render()` method. Already used by the capture pipeline for `summarize` and `extract_metadata` prompts.
- `llm/provider.py` — `get_provider("classify", config)` returns the configured AI provider. The `complete(system, user)` method returns `Result[Response]`. Already the standard call pattern for every AI stage.
- `core/result.py` — `Success(value)` and `Failure(error, recoverable, context)` are the return types. Every public function in this codebase follows this contract.
- `src/prompts/classify.yaml` — already designed and planned (`docs/4_plans/classify-prompt.md`). Returns JSON with four fields: `target_type`, `target_name`, `confidence`, `reasoning`.
- `vault/registry.py` — `format_for_prompt(registry)` already implemented. Formats the project/domain map as a readable string the prompt can use. The pipeline (not Classify) calls this and passes the result string.

**New code introduced:**

- `ClassifyResult` dataclass — four fields matching the prompt's JSON output: `target_type: str`, `target_name: str`, `confidence: float`, `reasoning: str`.
- `classify()` function — the single public entry point for this component.

---

## Decisions locked (from grill interview 2026-06-08)

| Decision | What was chosen | Why |
|----------|----------------|-----|
| Return fields | All four: `target_type`, `target_name`, `confidence`, `reasoning` | Route needs `target_type` to build the vault path — dropping it forces Route to re-derive it |
| Failure on bad JSON | `Failure(recoverable=True)` | Transient LLM glitch — retry makes sense; pipeline decides retry count |
| Failure on invalid `target_type` | `Failure(recoverable=True)` | Structural error from the AI; retrying with same inputs is valid |
| Input signature | Individual fields: `title`, `summary`, `tags`, `valid_destinations` | Keeps Classify decoupled from the Note model; pure function, no fixture needed in tests |
| `valid_destinations` formatting | Pipeline calls `format_for_prompt(registry)` and passes the result string | Classify doesn't know the registry's structure; pipeline bridges them |
| `title` source | `path.stem` (actual filename on disk after capture) | Rename gate may reject AI's suggested name; `path.stem` is always ground truth |

**Tech debt logged:**
- TD-pending: Classify returns `Failure(recoverable=True)` but the pipeline has no retry loop yet — pipeline caller must implement retry count/backoff before relying on the recoverable flag.

---

## What happens inside (Q1 diagram)

```
# Classify Component — What Happens Inside
Scope: Shows what the function does from receiving inputs to returning a result.
       Does NOT cover who calls it or what happens after (see Q2 in the spec).

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

---

## What Classify does NOT do

- Does not move any file (that is Route + Move)
- Does not write to the audit log (that is Decision Log)
- Does not validate whether `target_name` exists in the vault (that is Route's job — Route checks the folder exists and returns Failure if not)
- Does not format the destinations string (pipeline calls `format_for_prompt(registry)` first)
- Does not retry on Failure (pipeline's responsibility)

---

## Success criteria (behavior IDs: P2-CL-01 through P2-CL-06)

- **P2-CL-01:** When the AI returns valid JSON, `classify()` returns `Success(ClassifyResult)` with all four fields populated.
- **P2-CL-02:** When the AI returns malformed JSON (not parseable), `classify()` returns `Failure(recoverable=True)`.
- **P2-CL-03:** When the JSON is parseable but `target_type` is not `"project"` or `"domain"`, `classify()` returns `Failure(recoverable=True)`.
- **P2-CL-04:** `classify()` never raises an exception and never returns `None` — only `Success` or `Failure`.
- **P2-CL-05:** The prompt is loaded from `prompts/classify.yaml` — no prompt string exists anywhere in the Python code.
- **P2-CL-06:** The AI call goes through `llm/provider.py` `get_provider()` — no direct import of the AI library.

---

## Out of scope

- The pipeline that calls `classify()` — that is `pipelines/classify.py`, a separate component
- Confidence Gate, Route, Move, Decision Log — separate components
- Retry logic — caller's responsibility (TD logged)
- `kms classify` CLI command — separate plan
