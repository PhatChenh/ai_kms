# Research: Phase 2 — Classify Component (pure function)
_Last updated: 2026-06-08_

---

## Overview

The Classify component is the AI call that reads a captured note's title, summary, and tags, then picks a destination folder — "put this in Project Alpha" or "put this in the Finance domain." It is a pure function: it does not move files, write to disk, or touch the audit log. Everything that comes before it (building the destinations list) and after it (acting on the answer) belongs to a separate pipeline.

This research verified six assumptions the spec makes about existing code — asking whether the building blocks it claims to reuse actually exist and behave as described. All six assumptions are confirmed. No build step is blocked. The spec is ready for planning.

---

## Key Components

The Classify function will be built in `src/pipelines/classify.py`. It assembles four building blocks that already exist:

| Component | File | Role |
|---|---|---|
| Prompt template | `src/prompts/classify.yaml` | Contains the question to ask the AI, with four fill-in slots |
| Prompt loader | `src/llm/prompt_loader.py` | Reads all `*.yaml` prompts at startup; exposes `PROMPTS["classify"]` |
| Provider factory | `src/llm/provider.py` | Returns the correct AI provider for a task; dispatches based on `config.yaml` |
| Registry formatter | `src/vault/registry.py` | Converts the project/domain map into a readable string for the prompt |

Supporting infrastructure (result types, config) already in place:

- `src/core/result.py` — `Success` / `Failure` typed envelopes every pipeline function must return
- `src/core/config.py` — `Task` type literal includes `"classify"`; `ProvidersConfig.classify` field defaults to `"claude"`

---

## How It Works

When `classify()` is called, it follows these steps in order — no shortcuts, no bundled stages:

1. **Load and render the prompt** — calls `PROMPTS["classify"].render(title=..., summary=..., tags=..., valid_destinations=...)`, which fills the four Jinja2 placeholders and returns a `(system_str, user_str)` tuple.
2. **Get the AI provider** — calls `get_provider("classify", config)`, which reads `config.yaml` and returns the configured provider (defaults to `ClaudeProvider`).
3. **Call the AI** — awaits `provider.complete(system, user)`, which returns `Result[LLMResponse]`.
4. **Handle provider failure** — if the AI call failed, returns `Failure(recoverable=True)`.
5. **Parse JSON** — calls `json.loads(response.value.content)`; if parsing fails, returns `Failure(recoverable=True)`.
6. **Validate `target_type`** — checks that the value is `"project"` or `"domain"`; if not, returns `Failure(recoverable=True)`.
7. **Return success** — wraps a `ClassifyResult` dataclass in `Success(...)`.

All three failure paths are retryable — the function itself never loops. The pipeline that calls it decides retry count and backoff.

---

## Spec Verification

The spec lists six assumptions about existing code. Each was verified by reading the actual source file at the location the spec names.

| Assumption ID | Spec Claim | Verdict | Evidence |
|---|---|---|---|
| A1 | `PROMPTS["classify"]` key exists — `src/prompts/classify.yaml` is present, valid YAML, and loads at import time | ✅ Validated | `src/prompts/classify.yaml` exists with `name: classify`. `PROMPTS` is built at module level by `load_prompts(_PROMPTS_DIR)` in `src/llm/prompt_loader.py:54`, scanning all `*.yaml` files on import. Key will be present. |
| A2 | `"classify"` is registered as a `Task` literal in `core/config.py` — `get_provider("classify", ...)` will not raise `ValueError` | ✅ Validated | `type Task = Literal["classify", ...]` at `config.py:43-45`. `ProvidersConfig` has `classify: Provider = "claude"` at `config.py:179`. `for_task("classify")` uses `getattr(self, "classify")` returning `"claude"`. In `provider.py:71`, `"claude"` matches the case branch returning `ClaudeProvider`. No `ValueError` raised. |
| A3 | `LLMProvider.complete()` is `async` — `classify()` must be declared `async def` | ✅ Validated | `provider.py:40`: `@abstractmethod async def complete(self, system: str, user: str) -> Result[LLMResponse]`. All concrete providers implement it as `async`. |
| A4 | `classify.yaml` uses Jinja2 `{{ variable }}` placeholders for all four inputs: `title`, `summary`, `tags`, `valid_destinations` | ✅ Validated | `src/prompts/classify.yaml` user section contains `{{ title }}`, `{{ summary }}`, `{{ tags }}`, `{{ valid_destinations }}`. `variables: [title, summary, tags, valid_destinations]` declared at bottom. `StrictUndefined` Jinja2 engine in `prompt_loader.py:10` means a missing variable raises `UndefinedError` — the spec's four names match exactly. |
| A5 | `format_for_prompt(registry)` returns a plain string with no further transformation needed for prompt injection | ✅ Validated | `registry.py:151-178`: function builds a `list[str]` of lines then returns `"\n".join(lines)` — a plain `str`. Output is human-readable text (domain headers + indented project names). No markup, no Python objects. Ready for direct injection into the Jinja2 template. |
| A6 | `LLMResponse.content` is the raw text string to parse as JSON | ✅ Validated | `provider.py:26-29`: `@dataclass(frozen=True) class LLMResponse` with `content: str` as first field. The AI's raw text response is stored here without pre-parsing. `json.loads(response.value.content)` will work on a valid JSON AI response. |

---

## "Done When" Conditions — Component Verification

The spec also states two "done when" conditions that depend on existing patterns in the codebase:

**`ClassifyResult` dataclass uses Python `@dataclass` (not Pydantic):**

Confirmed pattern. Both `SummarizeResult` and `MetadataResult` in `src/pipelines/capture.py` (lines 62-70) and `ReconcileResult` in `src/pipelines/reconcile.py` (line 39) use `@dataclass(frozen=True)`. No pipeline result container uses Pydantic. The spec's instruction to use `@dataclass(frozen=True)` matches the established convention.

**`classify()` uses `PROMPTS["classify"].render()` returning `(system_str, user_str)` tuple:**

Confirmed shape. `prompt_loader.py:19-34`: `def render(self, **vars: object) -> tuple[str, str]` returns `(rendered_system, rendered_user)`. The capture pipeline uses this exact unpack pattern at `capture.py:193`: `system, user = PROMPTS["summarize"].render(text=raw.text)`. The classify spec's build step 1 matches.

---

## Edge Cases and Silent Failure Modes

These are things that could go wrong that are not immediately obvious from reading the spec.

**`Failure` requires all three positional fields.** `core/result.py:37-51`: `Failure` is a plain `@dataclass` with `error: str`, `recoverable: bool`, and `context: dict` as required fields (no defaults). The spec's build steps pass all three — but a test that constructs `Failure(error="...", recoverable=True)` without `context` will raise `TypeError`. Tests must always supply `context`.

**`for_task("documentation")` would raise `AttributeError`, not `ValueError`.** `ProvidersConfig` has no `documentation` field even though `"documentation"` is in the `Task` literal. For "classify" specifically this is not a problem — `classify` IS a field. But this is a latent inconsistency worth knowing.

**`StrictUndefined` Jinja2 engine raises on any missing variable.** If `classify()` is ever called with a variable name that doesn't match the template (e.g., `PROMPTS["classify"].render(tile=...)` — a typo), Jinja2 raises `UndefinedError` at render time. The spec's function body does not wrap the `render()` call in a try/except, so a typo would propagate as an uncaught exception and violate C-12 (never raise). The planner should add a try/except around the render call or note this explicitly.

**`format_for_prompt` takes `ProjectRegistry`, not `LiveRegistry`.** The pipeline calling `classify()` must call `registry.get_groups()` (which returns the dict) and reconstruct, or call `format_for_prompt` on the inner `_registry` — actually `format_for_prompt` takes a `ProjectRegistry` object. `LiveRegistry` wraps a `ProjectRegistry` but does not expose it directly. The pipeline spec must decide how to extract the `ProjectRegistry` from `LiveRegistry` before calling `format_for_prompt`. (Likely: call `format_for_prompt(ProjectRegistry(groups=live_registry.get_groups()))` or expose a `snapshot()` method on `LiveRegistry`. This is a handoff note scope, not a classify.py scope — but good to flag for the pipeline spec.)

---

## Dependencies and Coupling

**`classify()` imports (at module level):**
- `from llm.prompt_loader import PROMPTS` — module-level singleton; loads all prompts at startup
- `from llm.provider import get_provider` — factory, pure function
- `from core.result import Success, Failure, Result`
- `from core.config import MainConfig` — type hint only, not the `CONFIG` singleton

**What classify() does NOT import:**
- `CONFIG` singleton — passes `config: MainConfig` explicitly (C-17 compliant)
- `vault.*` — pure AI call, no filesystem
- `storage.*` — no database
- `core.audit` — audit is the pipeline's responsibility, not classify's

**What depends on classify():**
- The future `pipelines/classify.py` orchestration (not yet built — separate spec)
- The `kms classify` CLI command (not yet built — requires the pipeline)

---

## Extension Points

**Switching AI providers:** Zero code change in `classify.py`. Change `providers.classify` in `config/config.yaml` from `"claude"` to `"ollama"` or `"openai"`. `get_provider()` dispatches automatically.

**Changing the prompt:** Edit `src/prompts/classify.yaml` only. No Python change required.

**Adding a third valid `target_type`:** Currently `classify()` only accepts `"project"` or `"domain"`. Adding `"inbox"` or another type requires changing the validation set in `classify.py` — a small, localized change. No extension point exists for this today; it's a direct edit.

**Changing the `ClassifyResult` fields:** Would break every caller. The spec locks four fields; adding a fifth (e.g. `alternative_targets`) would need spec alignment with the pipeline.

---

## Open Questions

None. All six assumptions are verifiable from code. No runtime behavior or external dependency is unresolvable at this stage.

---

## Technical Debt Spotted

**TD-pending (retry loop):** The spec notes this — `classify()` returns `Failure(recoverable=True)` but no retry infrastructure exists in `pipelines/`. This applies to the capture pipeline too (same pattern). The pipeline spec for `pipelines/classify.py` must implement retry count + backoff before `recoverable=True` is meaningful in production. Confirm whether a matching TD entry exists in `TECH_DEBT.md` before creating a duplicate.

**Jinja2 render exception not caught:** If `PROMPTS["classify"].render(...)` raises `UndefinedError` (wrong variable name at call site), the exception propagates uncaught and violates C-12. The planner should add a bare `except Exception` wrapper around the render call that converts it to `Failure(recoverable=False, ...)`. This is a small defensive pattern already implicit in C-12 but not enforced by a hook.

**`LiveRegistry` → `ProjectRegistry` extraction:** The pipeline spec needs to decide how the calling pipeline extracts a `ProjectRegistry` snapshot from `LiveRegistry` for `format_for_prompt`. `LiveRegistry.get_groups()` returns a dict, not a `ProjectRegistry`. Either add a `snapshot() -> ProjectRegistry` method to `LiveRegistry`, or construct inline. This is a coupling point between the registry plan and the classify pipeline spec.

---

## Invalidated Assumptions

_(This section is omitted — all assumptions validated.)_
