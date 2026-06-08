# Phase 2-CL — Classify Component (pure function)

**ID prefix:** P2-CL
**Input:** `docs/1_design/phase2/classify.md`
**Date:** 2026-06-08

---

## Purpose

This phase delivers `classify()` — the pure Python function that takes a captured note's title, summary, tags, and the formatted list of available vault destinations, asks the AI to pick where the note belongs, and returns a typed answer.

After this phase, the codebase has a single, testable entry point for the AI classification decision. The function never moves files, writes to disk, or logs to the audit trail — those responsibilities belong to the pipeline that calls it (a separate, future spec). What it delivers is a typed `ClassifyResult` that tells the caller: "put this note in Project Alpha" or "put this note in the Finance domain," together with a confidence score and a one-sentence reason.

---

## Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `PROMPTS["classify"]` | `src/llm/prompt_loader.py` (loads `src/prompts/classify.yaml`) | Holds the classify prompt template. `.render(title, summary, tags, valid_destinations)` returns a `(system_str, user_str)` tuple ready for the AI. | `classify()` calls `.render()` with its four inputs to build the AI call. | shallow |
| `get_provider(task, config)` | `src/llm/provider.py:54` | Factory that returns the correct `LLMProvider` for a given task, as configured in `config.yaml` under `providers:`. | `classify()` calls `get_provider("classify", config)` then awaits `.complete(system, user)`. | shallow |
| `LLMProvider.complete(system, user)` | `src/llm/provider.py:40` | Async method — sends a system+user message pair to the AI, returns `Result[LLMResponse]`. | Called inside `classify()` to get the raw text response. | deep |
| `Success(value)` / `Failure(error, recoverable, context)` | `src/core/result.py` | Typed result envelope. Every public pipeline function must return one of these, never raise. | `classify()` returns `Success(ClassifyResult)` or `Failure(recoverable=True, ...)`. | deep |
| `prompts/classify.yaml` | `src/prompts/classify.yaml` | Ready-to-use prompt file. Variables: `title`, `summary`, `tags`, `valid_destinations`. Instructs the AI to respond with JSON: `target_type`, `target_name`, `confidence`, `reasoning`. | Used as-is — no changes needed. | deep |
| `format_for_prompt(registry)` | `src/vault/registry.py:151` | Converts a `ProjectRegistry` into a human-readable string (domain headers + project names). | The pipeline (not `classify()`) calls this before calling `classify()`, passing the resulting string as `valid_destinations`. | shallow |
| `"classify"` Task literal | `src/core/config.py:44` | Registers "classify" as a valid routing task. `get_provider("classify", ...)` will not raise. | Used implicitly by `get_provider`. No change needed. | shallow |

---

## Q1 Diagram (from design)

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

---

## Q2 Diagram — How it connects to others

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

Simplified: Destinations List is shown as a direct input into Classify Step (the pipeline
builds it via `format_for_prompt(registry)` and passes it in). AI retry signal feeds
back to Classify Pipeline — omitted from diagram to avoid crossing arrows.

---

## Feature overview

When a note arrives in the inbox after capture, it has a title, summary, and tags but no destination. `classify()` reads those three pieces of information — plus a formatted list of all known vault destinations — and asks the AI where the note belongs.

**Happy path:** The function loads the classify prompt template, fills in the four input values, sends the filled prompt to the configured AI provider, receives a JSON response, parses it, checks that `target_type` is either `"project"` or `"domain"` (the only two valid routing targets), and returns a `ClassifyResult` with all four fields populated.

**Error cases — all retryable:**
- AI call fails outright (network, timeout, provider error) → `Failure(recoverable=True)`
- AI responds but the response text is not valid JSON → `Failure(recoverable=True)`
- JSON is valid but `target_type` is something other than `"project"` or `"domain"` → `Failure(recoverable=True)`

All three failure modes are `recoverable=True` because they are transient — retrying with the same inputs is a valid strategy. The pipeline that calls `classify()` decides how many times to retry; `classify()` itself does not loop.

---

## Out of scope

- **Full classify pipeline** (`pipelines/classify.py` orchestration: classify → audit → confidence gate → route → move) — separate spec; next component to build.
- **Confidence Gate** — reads `CONFIG.thresholds.for_pipeline("classify")`, routes to auto/review/inbox. Separate component.
- **Route** — verifies `target_name` folder actually exists on disk; returns the resolved vault path. `classify()` does NOT validate this. Separate component.
- **Move** — physically moves the note from inbox to the chosen destination. Separate component.
- **Audit log entry** — required by C-13, but belongs to the pipeline wrapper, not to `classify()` itself (which is a pure function). The pipeline spec must call `core/audit.write(...)` after `classify()` returns.
- **Retry loop** — `classify()` returns `Failure(recoverable=True)` but never retries internally. The pipeline decides retry count and backoff. (TD-pending: retry loop not yet built in `pipelines/`.)
- **`kms classify` CLI command** — requires the full pipeline to exist first. Separate plan.
- **`tags` serialization** — the caller converts `NoteMetadata.tags: list[str]` to a string before calling `classify()`. Serialization format is a handoff decision.

---

## Constraints

- **C-07** — Prompt loaded via `PROMPTS["classify"].render(...)`. No prompt text in any Python source file. Source: CLAUDE.md hook enforcement.
- **C-08** — AI call goes through `get_provider("classify", config)`. No direct `ClaudeProvider(...)` or any other provider instantiation inside `classify()`. Source: DECISION-013.
- **C-12** — `classify()` must return `Success(ClassifyResult)` or `Failure(...)`. Never `None`, never raise. Source: CONSTRAINTS.md §Architecture.
- **C-13 (negative)** — `classify()` itself is a pure function and does NOT call `audit.write()`. The pipeline wrapper owns the audit entry. Spec must make this explicit so the planner does not add audit writes inside `classify()`. Source: DECISION-003.
- **C-17** — No `CONFIG` at module scope in test files. Tests pass `config: MainConfig` explicitly as a function argument. Source: DECISION-012.
- **Extension Point Rule** — Switching AI providers in the future requires only a `config.yaml` change. `classify()` never names a specific provider. The `get_provider()` factory already handles dispatch.

---

## Assumptions

| ID | Assumption | Source | What would prove it wrong |
|----|-----------|--------|--------------------------|
| A1 | `PROMPTS["classify"]` key exists — `src/prompts/classify.yaml` is present, valid YAML, and loads at import time | Design doc: "already designed and planned" | `PROMPTS["classify"]` raises `KeyError` at runtime; file missing or YAML malformed |
| A2 | `"classify"` is registered as a `Task` literal in `core/config.py` — `get_provider("classify", ...)` will not raise `ValueError` | `src/core/config.py:44` `type Task = Literal[..., "classify", ...]` | `get_provider("classify", ...)` raises `ValueError: Unknown provider` |
| A3 | `LLMProvider.complete()` is `async` — `classify()` must be declared `async def` | `src/llm/provider.py:40` `async def complete(...)` | `complete()` is synchronous; `await` call would fail |
| A4 | `classify.yaml` uses Jinja2 `{{ variable }}` placeholders for all four inputs | `src/prompts/classify.yaml` `variables: [title, summary, tags, valid_destinations]` | `.render(...)` raises `jinja2.UndefinedError` for any of the four variable names |
| A5 | `format_for_prompt(registry)` returns a plain string with no further transformation needed for injection into the prompt | `src/vault/registry.py:151` returns `"\n".join(lines)` | String contains markup or Python objects that produce unreadable prompt text |
| A6 | `LLMResponse.content` is the raw text string to parse as JSON | `src/llm/provider.py:27` `content: str` field on `LLMResponse` | `content` is pre-parsed or wrapped so `json.loads(result.value.content)` fails on a valid AI response |

---

## Component dependency order

### 1. `ClassifyResult` dataclass

**Goal.** A typed container for the AI's classification answer — the value `classify()` wraps in `Success(...)` on the happy path.

**Build.** Create a `@dataclass(frozen=True)` named `ClassifyResult` at the top of `src/pipelines/classify.py` with four fields:

- `target_type: str` — must be `"project"` or `"domain"` (validated by `classify()`, not by the dataclass itself)
- `target_name: str` — the exact name of the destination (a project name or a domain name)
- `confidence: float` — between 0.0 and 1.0
- `reasoning: str` — one sentence from the AI explaining the choice

No Pydantic, no validation logic inside the dataclass. Validation is `classify()`'s responsibility.

**Depends on.** Nothing — pure data structure.

**Done when.** `ClassifyResult(target_type="project", target_name="Alpha", confidence=0.9, reasoning="Meeting notes tied to active engagement.")` constructs without error and all four fields are accessible. Invalid `target_type` does NOT raise at construction time (that guard lives in `classify()`).

---

### 2. `classify()` async function

**Goal.** The single public entry point for the AI classification decision — takes four string inputs plus the active config, calls the AI, parses and validates the response, and returns a typed result or a retryable failure.

**Build.** Create `async def classify(title: str, summary: str, tags: str, valid_destinations: str, config: MainConfig) -> Result[ClassifyResult]` in `src/pipelines/classify.py`.

The function body follows the Q1 diagram exactly — no shortcuts, no bundled stages:

1. **Load and render prompt:** `system, user = PROMPTS["classify"].render(title=title, summary=summary, tags=tags, valid_destinations=valid_destinations)`
2. **Get provider:** `provider = get_provider("classify", config)`
3. **Call AI:** `response = await provider.complete(system, user)`
4. **Handle provider failure:** if `response` is `Failure`, return `Failure(error=response.error, recoverable=True, context={"stage": "classify", "title": title})`
5. **Parse JSON:** `json.loads(response.value.content)` — on `json.JSONDecodeError`, return `Failure(error="classify JSON parse error: <msg>", recoverable=True, context={"stage": "classify", "title": title, "raw": response.value.content[:200]})`
6. **Validate `target_type`:** if not in `{"project", "domain"}`, return `Failure(error="classify invalid target_type: <value>", recoverable=True, context={"target_type": <value>, "title": title})`
7. **Return success:** `return Success(ClassifyResult(target_type=..., target_name=..., confidence=..., reasoning=...))`

The function never raises. All code paths end in `return Success(...)` or `return Failure(...)`.

**Depends on.** Component 1 (`ClassifyResult` dataclass).

**Assumes.** A1, A2, A3, A4, A6.

**Interface shape.**
- Callers see: `classify(title, summary, tags, valid_destinations, config) → Result[ClassifyResult]`
- Hidden behind it: prompt loading, provider dispatch, JSON parsing, `target_type` validation
- `tags` is `str` — the caller serializes `NoteMetadata.tags: list[str]` before calling (see Handoff notes for recommended format)
- `config` is `MainConfig` (not the full `Config` wrapper) — consistent with the `get_provider(task, config: MainConfig)` factory signature at `provider.py:54`

**Dependency category.** In-process — test directly by passing a mock `LLMProvider` that returns controlled `Result[LLMResponse]` values. No filesystem, no network, no vault.

**Decisions.**
- Q: Should `tags` be `str` or `list[str]`? Leaning `str` — the Jinja2 template expects a plain string for injection, and passing a `list` would render as Python's `['tag1', 'tag2']` literal. Caller serializes before calling; keeps `classify()` free of any dependency on `NoteMetadata`.
- Q: Should `config` be passed explicitly or read from the `CONFIG` singleton? Leaning explicit parameter — makes the function testable without patching the global singleton (C-17 applies to tests). Consistent with how existing pipeline stages are constructed.

**Done when.**

- Given a mock provider that returns well-formed JSON (`target_type="project"`, `target_name="Alpha"`, `confidence=0.9`, `reasoning="..."`), `classify()` returns `Success(ClassifyResult)` with all four fields matching the JSON values. (P2-CL-01)
- Given a mock provider that returns `Failure(...)`, `classify()` returns `Failure(recoverable=True)`. (P2-CL-02 partial)
- Given a mock provider that returns `Success` with non-JSON content (e.g. `"Sorry, I cannot help"`), `classify()` returns `Failure(recoverable=True)`. (P2-CL-02)
- Given valid JSON but `target_type="inbox"` (not `"project"` or `"domain"`), `classify()` returns `Failure(recoverable=True)`. (P2-CL-03)
- `classify()` never raises an exception in any of the above cases. (P2-CL-04)
- No prompt string appears in `src/pipelines/classify.py` — only `PROMPTS["classify"].render(...)`. (P2-CL-05)
- The AI call is made via `get_provider("classify", config)` — no direct provider class name appears in the module. (P2-CL-06)

---

## Handoff notes

- **Contract with pipeline spec:** `classify()` is a pure AI call. The pipeline spec that wraps it must: (1) call `format_for_prompt(registry)` to build `valid_destinations` before calling `classify()`; (2) call `core/audit.write(...)` after `classify()` returns (C-13 — non-negotiable); (3) call `CONFIG.thresholds.for_pipeline("classify")` to get the `ConfidenceBand` and route on `result.value.confidence`; (4) implement retry count and backoff for `Failure(recoverable=True)` — no retry mechanism exists yet in `pipelines/`.

- **`tags` serialization contract:** The pipeline must decide how to convert `NoteMetadata.tags: list[str]` into the `tags: str` parameter. Suggested: `", ".join(sorted(note.metadata.tags))` — deterministic, human-readable. Research or prompt-testing recommended before the pipeline spec commits to a format; the prompt template accepts any string but performance may differ between comma-separated and newline-separated.

- **`target_name` validation is Route's job:** `classify()` validates that `target_type` is one of `{"project", "domain"}` but does NOT check that `target_name` exists as a real folder on disk. Route (next pipeline stage after confidence gate) owns that check. If Route cannot find the named destination, it returns its own `Failure` — the pipeline must handle that independently.

- **Thresholds config — verify before pipeline spec:** Confirm that `config/config.yaml` has a `thresholds.pipelines.classify` entry with `auto` and `suggest` values. If missing, `CONFIG.thresholds.for_pipeline("classify")` falls back to `global_` defaults (see `config.py:444`). The pipeline spec must decide whether to rely on the fallback or require an explicit entry.

- **TD-pending (retry loop):** `classify()` returns `Failure(recoverable=True)` but no retry infrastructure exists in `pipelines/`. Record as a new tech debt entry if none already exists covering this (no current TD matches this exactly). The pipeline spec must implement retry before `recoverable=True` is meaningful in production.

- **Suggested research before pipeline spec:** Verify `config/config.yaml` has a `providers.classify` entry (should default to `"claude"` — check `config.py:179`). Verify the `thresholds` section has a classify band. Both are needed by the pipeline spec but are outside this spec's scope.
