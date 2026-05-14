---
name: llm_layer
description: Phase 0 LLM layer research — prompt_loader.py, provider.py (ABC + factory), fireworks_provider.py — interface contracts, existing code conflicts, what to build, what to migrate.
metadata:
  type: project
---

# Research: llm_layer
_Last updated: 2026-05-14_

## Overview

The LLM layer provides the project's interface to AI inference. Three deliverables remain in
Phase 0: `llm/prompt_loader.py` (missing), a revised `llm/provider.py` interface (exists but
has critical mismatches with the new plan), and a new OpenAI-compatible provider (new). Existing
`claude_provider.py` and `ollama_provider.py` also exist — both may need interface updates
depending on whether the new `complete()` contract replaces `chat()` or coexists with it.

`prompts/` is empty. `llm/prompt_loader.py` does not exist.

**Architectural note (2026-05-14):** The originally planned `fireworks_provider.py` should be
replaced by a generic `llm/openai_provider.py`. Fireworks exposes an OpenAI-compatible REST
API — and so do Groq, Together AI, Anyscale, LM Studio, local Ollama, and OpenAI itself. A
single provider implementation parameterised by `base_url` + `api_key` + `model` covers all of
them. This is strictly more future-proof than a Fireworks-specific file.

---

## Key Components

### What exists on disk

| File | Status | Notes |
|---|---|---|
| `llm/provider.py` | ✅ Exists | Sync `AIProvider` ABC; `chat()` + `embed()`; factory `get_provider()` |
| `llm/claude_provider.py` | ✅ Exists | Sync; Anthropic SDK `Anthropic()` (not async); raises exceptions |
| `llm/ollama_provider.py` | ✅ Exists | Sync; `requests` HTTP; raises exceptions |
| `llm/prompt_loader.py` | ❌ Missing | TD-002 |
| `llm/__init__.py` | ❌ Missing | Package works via implicit namespace package in Python 3 |
| `prompts/` | ❌ Empty | No YAML files yet |

### What the new plan calls for

| File | Action | Key interface |
|---|---|---|
| `llm/prompt_loader.py` | Create | `load_prompts(dir) -> dict[str, Prompt]`; `Prompt.render(**vars) -> str` |
| `llm/provider.py` | Revise | `async def complete(self, system, user) -> Result[str]`; `LLMResponse` dataclass |
| `llm/openai_provider.py` | Create | `OpenAIProvider(LLMProvider)` — generic OpenAI-compat; `base_url` + `api_key` + `model` from config |

---

## How It Works (Current vs Target)

### Current `provider.py`

```python
class AIProvider(ABC):
    @abstractmethod
    def chat(self, prompt: str, system_prompt: str = "", json_mode: bool = False) -> str: ...
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

def get_provider(task: Task, config: MainConfig) -> AIProvider:
    match config.providers.for_task(task):
        case "claude": return ClaudeProvider(config.claude, task=task)
        case "ollama": return OllamaProvider(config.ollama)
        case _: raise ValueError(...)
```

### Target `provider.py` (per revised Phase 0 plan)

```python
from dataclasses import dataclass
from core.result import Result

@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict

class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str) -> Result[LLMResponse]: ...
```

**Critical gap**: the class is renamed from `AIProvider` → `LLMProvider`, and the method from
`chat()` → `complete()`. This is not backward-compatible with existing `claude_provider.py` and
`ollama_provider.py`.

---

## Critical Findings

### F-001: Sync Providers in Async Pipeline — Blocking Event Loop

**Severity: HIGH.** `core/pipeline.py` (`run_pipeline`) is `async def` (DECISION-010). Every
stage is `await`-ed. If a pipeline stage calls `provider.chat()` (which calls
`anthropic.Anthropic().messages.create()` or `requests.post()`) synchronously, it blocks the
event loop for the entire duration of the network round-trip. In Phase 4 (MCP daemon), this
freezes all concurrent requests while one LLM call is in-flight.

For Phase 0 the blocking is harmless (single-process CLI), but the interface set here is
permanent. Setting sync methods now means either a painful async retrofit in Phase 4 or
thread-pool workarounds (`asyncio.to_thread`).

**Resolution options:**
1. Rewrite `claude_provider.py` to use `AsyncAnthropic` (Anthropic SDK has native async support)
2. Keep `claude_provider.py` sync; wrap in `asyncio.to_thread()` inside pipeline stages
3. Rewrite to match new async `complete()` interface (cleanest, aligns with plan)

Option 3 is recommended. The Anthropic SDK already supports `AsyncAnthropic`.

### F-002: No Result Type on Existing Providers

The existing `claude_provider.py.chat()` and `ollama_provider.py.chat()` raise exceptions
instead of returning `Result`. The plan specifies `complete()` returns `Result[LLMResponse]`.

The new `fireworks_provider.py` should implement this correctly. The existing providers either
need matching refactors or must remain separate for backward compatibility (callers decide).

Cross-phase constraint from `STATE.md`: "Every public function in `handlers/` and `pipelines/`
returns `Success` or `Failure`." Strictly, `llm/` is neither — but the plan explicitly
specifies `Result[str]`, so the intent is clear.

### F-003: Method Signature Change — `chat()` vs `complete()`

The new plan changes the interface:
- Old: `chat(prompt, system_prompt, json_mode) -> str`
- New: `complete(system, user) -> Result[LLMResponse]`

`json_mode` has no equivalent in the new interface. It was used in `claude_provider.py` to
append `"\n\nReturn ONLY valid JSON..."` to the system prompt. Under the new interface, JSON
formatting should be handled in the prompt YAML itself (the system message in the YAML can
include JSON formatting instructions). This is the correct place for it.

### F-004: `openai_provider.py` Needs `openai` Package (Not in Dependencies)

`pyproject.toml` lists no `openai` dependency. `openai_provider.py` uses:
```python
import openai
client = openai.AsyncOpenAI(api_key=..., base_url=config.base_url)
```
`openai` must be added to `pyproject.toml` before `openai_provider.py` can be installed.

### F-005: `OpenAICompatConfig` Needed in `core/config.py`

`core/config.py` has `ClaudeConfig` and `OllamaConfig`. The generic OpenAI-compatible provider
needs its own config section — `OpenAICompatConfig` — because `base_url` and `model` differ
per vendor. Minimum fields:

```python
class OpenAICompatConfig(BaseModel):
    base_url: str = "https://api.fireworks.ai/inference/v1"
    model: str = "accounts/fireworks/models/gpt-oss-20b"
    max_tokens: int = 1024
    timeout: int = 60
```

API key must NOT go in `config.yaml`. For Fireworks specifically, `FIREWORKS_API_KEY` is in
`.env`. The provider reads it via a local `BaseSettings` subclass or directly from
`os.environ` — same pattern as `claude_provider.py` uses for `ANTHROPIC_API_KEY`. The local
`BaseSettings` approach (`_OpenAICompatSettings`) is cleaner as it gets Pydantic validation.

`ApiKeys` in `core/config.py` already has `openai_api_key`. For Fireworks, a separate
`FIREWORKS_API_KEY` env var must be added. The cleanest approach is a local
`_OpenAICompatSettings(BaseSettings)` inside `openai_provider.py` that reads a configurable
env var name — e.g. `api_key_env: str = "FIREWORKS_API_KEY"` in `OpenAICompatConfig`, and
the provider resolves `os.environ[config.api_key_env]` at init time. This makes the same
class work for any vendor key name without touching `core/config.py` for each new vendor.

### F-006: `Provider` Type Alias Doesn't Know About `"openai"`

`core/config.py` line 39:
```python
type Provider = Literal["claude", "ollama"]
```
Adding `openai_provider.py` requires updating this to:
```python
type Provider = Literal["claude", "ollama", "openai"]
```
And adding a `case "openai":` branch to `get_provider()` in `provider.py`. Both are
minimal changes, but they touch `core/config.py` — which means any test that imports `Config`
must be re-run.

**Naming note:** `"openai"` as the provider name means "any OpenAI-compatible API", not
specifically OpenAI's own API. This must be documented in config comments so future
contributors don't confuse it for an OpenAI-exclusive provider.

### F-007: `pytest-asyncio` Mode Not Configured

`pyproject.toml` has no `asyncio_mode` in `[tool.pytest.ini_options]`. Existing pipeline tests
use explicit `@pytest.mark.asyncio` decorators. The integration test for `fireworks_provider`
(marked `@pytest.mark.integration`) also needs `@pytest.mark.asyncio` since `complete()` is
async. The simplest fix is to add `asyncio_mode = "auto"` to `pyproject.toml`, which removes
the need for per-test marks — but this changes behavior for all existing tests. Safest default:
keep explicit marks, add them to new tests.

### F-008: `integration` Marker Not Defined in `pyproject.toml`

The plan specifies `@pytest.mark.integration` for the live-API test. `pyproject.toml` only
defines `smoke`. Running with `--strict-markers` will fail. The `integration` marker must be
added to `pyproject.toml` markers list.

---

## `prompt_loader.py` Design

### Prompt YAML Schema

Each file in `prompts/*.yaml` maps to a `Prompt` Pydantic model:

```python
class Prompt(BaseModel):
    name: str
    system: str
    user: str                       # Jinja2 template string
    variables: list[str]            # expected variable names; render raises if any missing
    model: str | None = None        # optional per-prompt model override
    temperature: float | None = None

    def render(self, **vars: str) -> tuple[str, str]:
        """Returns (rendered_system, rendered_user). Raises on missing vars."""
        env = Environment(undefined=StrictUndefined)
        rendered_system = env.from_string(self.system).render(**vars)
        rendered_user = env.from_string(self.user).render(**vars)
        return rendered_system, rendered_user
```

`StrictUndefined` makes Jinja2 raise `UndefinedError` if any `{{ variable }}` in the template
is not passed to `render()`. This is the only safe default for production prompts.

### `load_prompts()` Contract

```python
def load_prompts(directory: Path) -> dict[str, Prompt]:
    """Load all *.yaml files in directory. Key = prompt name (from `name` field)."""
```

Called once at startup. Should return the dict; callers (pipelines) keep the reference.
The `prompts/` directory is `{project_root}/prompts/` — same resolution pattern as
`_CONFIG_DIR` in `core/config.py`.

### Test Prompt (smoke test gate)

`prompts/test.yaml` must exist for the smoke test:
```yaml
name: test
system: "You are a test assistant."
user: "Echo this back: {{ message }}"
variables: [message]
```

---

## Dependencies & Coupling

| Depends on | Used for |
|---|---|
| `core/result.py` | `Result[LLMResponse]` return type |
| `core/exceptions.py` | `LLMError`, `ConfigError` |
| `core/config.py` | `Provider` type alias, `ApiKeys`, `ClaudeConfig`, `OllamaConfig` |
| `jinja2` | Prompt rendering (already in `pyproject.toml`) |
| `anthropic` | `claude_provider.py` (already in `pyproject.toml`) |
| `openai` | `fireworks_provider.py` (NOT yet in `pyproject.toml` — **add it**) |
| `pydantic-settings` | `FireworksSettings` in provider (already in `pyproject.toml`) |

**What depends on `llm/`:**
- Phase 1 `pipelines/capture.py` — calls `provider.complete(system, user)`
- Phase 2 `pipelines/classify.py` — same
- Every pipeline that calls the LLM consumes `prompt_loader.PROMPTS["<name>"].render(**vars)`

---

## Edge Cases & Silent Failure Modes

1. **Empty `prompts/` directory**: `load_prompts()` must return an empty dict (not raise). A
   missing key at render time is a `KeyError` — the pipeline stage should catch it and return
   `Failure(recoverable=False)`.

2. **Jinja2 `StrictUndefined` raises `UndefinedError`, not `KeyError`**: Caller must catch
   `jinja2.UndefinedError` explicitly, not just `Exception`, and wrap it in `Failure`.

3. **Fireworks API key in `.env` but `pydantic-settings` reads it wrong**: `FIREWORKS_API_KEY`
   maps to `fireworks_api_key` by lowercasing. Field name must match exactly. If the mapping
   fails silently (wrong field name), the provider starts with a `None` key and the first API
   call raises an auth error at runtime, not startup.

4. **OpenAI-compat model string format**: Fireworks namespaces models as
   `accounts/{owner}/models/{name}`. Short names silently fail or return a 404. The default
   (`accounts/fireworks/models/gpt-oss-20b`) must be verified against the Fireworks model list
   before use. For other vendors (Groq, Together, etc.) model strings follow their own scheme —
   all live in `config/config.yaml` under `openai_compat.model`, never hardcoded.

5. **Async + `StrictUndefined` in Jinja2**: Jinja2 itself is synchronous. `render()` doesn't
   need to be async. Do not wrap it in `asyncio.to_thread()` — it's CPU-only string
   interpolation, not IO.

6. **`LLMResponse.usage` is `dict`**: Fireworks (via `openai` SDK) returns usage as
   `CompletionUsage` object. Must call `.model_dump()` or `dict(usage)` before storing. If
   omitted, `LLMResponse.usage` holds an object that fails `json.dumps()` downstream (audit
   log serialisation).

---

## Open Questions

| ID | Question | What was checked |
|---|---|---|
| OQ-L1 | Should the existing `claude_provider.py` and `ollama_provider.py` be migrated to the new async `complete()` interface now, or deferred? Both are currently sync and return `str` (not `Result`). Phase 1 pipeline stages will call the provider — if they call `chat()`, they block the event loop. | Read `provider.py`, `claude_provider.py`, `ollama_provider.py`. Neither Anthropic SDK (`AsyncAnthropic`) nor `httpx` (for async Ollama) is a problem to add, but touching existing providers requires test coverage update. |
| OQ-L2 | Does `Prompt.render()` return `(system, user)` tuple, or just the user string? The plan says `render(**vars) -> str`, but a prompt has both system AND user templates. If only `user` is returned, the system prompt must be read separately. | Plan spec says `render()` returns a string; but system vs user split must be preserved for `complete(system, user)`. The spec may be incomplete. |
| OQ-L3 | Where is `PROMPTS` dict accessed from? Should `prompt_loader` expose a module-level `PROMPTS` singleton (like `CONFIG`), or should each pipeline call `load_prompts()` at startup? | No existing usage to check — `prompts/` is empty. `CONFIG` uses lazy `__getattr__` singleton; same pattern may apply here. |
| OQ-L4 | Is `@pytest.mark.integration` the right marker for live-API tests, or should it be part of `smoke`? | Checked `pyproject.toml` — only `smoke` is defined. `integration` is not. Either add a new marker or fold into `smoke`. |
| OQ-L5 | Should `OpenAICompatConfig.api_key_env` be a string field naming the env var to read, or should `openai_provider.py` always use a fixed env var name (e.g. `OPENAI_COMPAT_API_KEY`)? The flexible approach works for any vendor; the fixed name is simpler but forces every vendor to use the same env var name. | No existing precedent in codebase. `claude_provider.py` reads `ANTHROPIC_API_KEY` by hardcoded name. The flexible approach requires one more config field. |

---

## Reference Project Patterns

The reference project (`repomix-reference.xml`) has no entries — the file appears to be a
placeholder with no content. No patterns can be extracted.

---

## Technical Debt Spotted

| ID | What | When |
|---|---|---|
| TD-LLM-1 | Existing `chat()` interface on `claude_provider.py` + `ollama_provider.py` is sync and returns `str`, not `Result`. Will block event loop if called from async pipeline stages. | Fix when building Phase 1 capture pipeline, or during this Phase 0 step. |
| TD-LLM-2 | `json_mode` flag has no equivalent in new `complete(system, user)` interface. JSON formatting must move into prompt YAML `system` field. | No consumer yet; clean up when writing Phase 1 prompts. |
| TD-LLM-3 | `openai` package missing from `pyproject.toml`. Will silently fail until `uv sync` is re-run after adding it. | Add before implementing `openai_provider.py`. |
| TD-LLM-4 | `integration` pytest marker not defined. `--strict-markers` will fail. | Add to `pyproject.toml` before writing integration test. |

---

## Implementation Order

Based on dependencies between components:

1. **`pyproject.toml`** — add `openai` dep + `integration` marker
2. **`prompts/test.yaml`** — create smoke test prompt (no code deps)
3. **`llm/prompt_loader.py`** — pure Jinja2 + Pydantic; no provider dependency
4. **Revise `llm/provider.py`** — rename `AIProvider` → `LLMProvider`, add `LLMResponse`
   dataclass, change `chat()` → `complete()` (async, Result return), update factory to support
   `"openai"`, update `Provider` type alias in `core/config.py`
5. **Add `OpenAICompatConfig` to `core/config.py`** — `base_url`, `model`, `max_tokens`,
   `timeout`, `api_key_env` (or resolve OQ-L5 first); add to `MainConfig`
6. **`llm/openai_provider.py`** — generic async OpenAI-compat implementation; configured for
   Fireworks by default in `config/config.yaml` but switchable to any vendor by changing YAML
7. **Migrate `claude_provider.py`** — if OQ-L1 is resolved: rewrite to `AsyncAnthropic` +
   async `complete()` + `Result` return; otherwise defer to Phase 1

Steps 2 and 3 are independent; 4 and 5 depend on 3 being done.
