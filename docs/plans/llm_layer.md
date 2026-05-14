# Plan: LLM Layer
_Last updated: 2026-05-14_
_Status: [x] done_

## Approach

Build the LLM layer in five small phases: bootstrap deps and test prompt first, then
`prompt_loader.py`, then revise the `LLMProvider` ABC and config models, then the new
`openai_provider.py`, and finally migrate the existing sync providers to the async interface.
Each phase is independently testable. The existing `claude_provider.py` and
`ollama_provider.py` are migrated in Phase 5 — not deferred — because Phase 1 (Capture)
routes to `claude` immediately and a sync provider blocks the event loop (DECISION-010).

**Open questions resolved in this plan:**
- OQ-L1: Migrate existing providers to async `complete()` now (Phase 5).
- OQ-L2: `Prompt.render()` returns `(str, str)` — both rendered system and user strings.
- OQ-L3: Module-level `PROMPTS: dict[str, Prompt]` eagerly loaded at import time.
- OQ-L4: Separate `integration` marker; distinct from `smoke`.
- OQ-L5: `OpenAICompatConfig.api_key_env: str` field; default `"FIREWORKS_API_KEY"`.

---

## Phases

### Phase 1 — Bootstrap: deps + test prompt
**Goal**: Get `openai` package installed, pytest markers in place, and `prompts/test.yaml`
on disk — everything needed for later phases to import and test without missing deps.

**Steps**:
1. Add `openai>=1.0` to `dependencies` in `pyproject.toml`.
2. Add `integration: tests that call a real external API and require a live key` to the
   `markers` list in `[tool.pytest.ini_options]`.
3. Create `prompts/test.yaml`:
   ```yaml
   name: test
   system: "You are a test assistant."
   user: "Echo this back: {{ message }}"
   variables: [message]
   ```
4. Run `uv sync` to install the new dep.

**Files to modify**:
- `pyproject.toml` — add `openai>=1.0` dep; add `integration` marker
- `prompts/test.yaml` — create (new file)

**Test criteria**:
- [ ] `uv run python -c "import openai; print(openai.__version__)"` prints a version number
- [ ] `uv run pytest --co -q` shows no "Unknown mark" warnings for `integration`
- [ ] `prompts/test.yaml` parses as valid YAML (`python -c "import yaml; yaml.safe_load(open('prompts/test.yaml'))"`)

**Status**: [x] done
**Completed**: 2026-05-14
**Notes**: openai 2.36.0 installed (plan specified >=1.0). All 3 criteria passed immediately.

---

### Phase 2 — `llm/prompt_loader.py`
**Goal**: Load all `prompts/*.yaml` files into a module-level `PROMPTS` dict at import
time. Each prompt renders via Jinja2 with `StrictUndefined` — missing variables raise loudly.

**Steps**:
1. Create `llm/prompt_loader.py` with:
   - `Prompt(BaseModel)` — fields: `name: str`, `system: str`, `user: str`,
     `variables: list[str]`, `model: str | None = None`, `temperature: float | None = None`.
   - `Prompt.render(self, **vars: object) -> tuple[str, str]` — renders both `system` and
     `user` templates via Jinja2 `StrictUndefined`; returns `(rendered_system, rendered_user)`.
     Raises `jinja2.UndefinedError` if any template variable is missing.
   - `load_prompts(directory: Path) -> dict[str, Prompt]` — reads all `*.yaml` in
     `directory`; keys by `prompt.name`; returns empty dict if directory is empty.
   - `_PROMPTS_DIR: Path` — resolved relative to this file's parent parent (project root),
     same pattern as `_CONFIG_DIR` in `core/config.py`.
   - `PROMPTS: dict[str, Prompt]` — module-level singleton, populated by
     `load_prompts(_PROMPTS_DIR)` at module load time.

   ```python
   # llm/prompt_loader.py (skeleton — not the full implementation)
   from pathlib import Path
   import yaml
   from jinja2 import Environment, StrictUndefined
   from pydantic import BaseModel

   _PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

   class Prompt(BaseModel):
       name: str
       system: str
       user: str
       variables: list[str]
       model: str | None = None
       temperature: float | None = None

       def render(self, **vars: object) -> tuple[str, str]:
           env = Environment(undefined=StrictUndefined)
           return (
               env.from_string(self.system).render(**vars),
               env.from_string(self.user).render(**vars),
           )

   def load_prompts(directory: Path) -> dict[str, Prompt]:
       prompts = {}
       for path in sorted(directory.glob("*.yaml")):
           data = yaml.safe_load(path.read_text(encoding="utf-8"))
           prompt = Prompt.model_validate(data)
           prompts[prompt.name] = prompt
       return prompts

   PROMPTS: dict[str, Prompt] = load_prompts(_PROMPTS_DIR)
   ```

2. Create `tests/test_llm/` directory and `tests/test_llm/__init__.py`.
3. Write `tests/test_llm/test_prompt_loader.py` — see test criteria below.

**Files to modify**:
- `llm/prompt_loader.py` — create
- `tests/test_llm/__init__.py` — create (empty)
- `tests/test_llm/test_prompt_loader.py` — create

**Test criteria**:
- [ ] `PROMPTS["test"]` exists after import (loads `prompts/test.yaml` eagerly)
- [ ] `PROMPTS["test"].render(message="hello")` returns `("You are a test assistant.", "Echo this back: hello")`
- [ ] `PROMPTS["test"].render()` (missing `message`) raises `jinja2.UndefinedError`
- [ ] `load_prompts(tmp_path)` on an empty directory returns `{}`
- [ ] `PROMPTS["nonexistent"]` raises `KeyError` (standard dict behaviour — no custom handling needed)
- [ ] `uv run pytest tests/test_llm/test_prompt_loader.py -v` all green

**Status**: [x] done
**Completed**: 2026-05-14
**Notes**: All 5 tests pass. Pre-existing test_logging.py errors (missing `correlation_id` fixture) unrelated to this phase. mypy not installed; syntax verified via ast.parse.

---

### Phase 3 — Revise `llm/provider.py` + config
**Goal**: Replace sync `AIProvider` ABC with async `LLMProvider` ABC. Add `LLMResponse`
dataclass. Extend `core/config.py` with `OpenAICompatConfig`. Update `Provider` type alias
to include `"openai"`. Update factory to dispatch `"openai"` case (stub import only — the
actual class is built in Phase 4).

**Steps**:
1. In `core/config.py`:
   - Add `"openai"` to `Provider` type alias:
     ```python
     type Provider = Literal["claude", "ollama", "openai"]
     ```
   - Add `OpenAICompatConfig(BaseModel)` after `OllamaConfig`:
     ```python
     class OpenAICompatConfig(BaseModel):
         base_url:    str = "https://api.fireworks.ai/inference/v1"
         model:       str = "accounts/fireworks/models/gpt-oss-20b"
         max_tokens:  int = 1024
         timeout:     int = 60
         api_key_env: str = "FIREWORKS_API_KEY"  # name of the env var holding the key
     ```
   - Add `openai_compat: OpenAICompatConfig` field to `MainConfig` with
     `Field(default_factory=OpenAICompatConfig)`.

2. Add `openai_compat:` section to `config/config.yaml` with explicit values (mirrors defaults
   above — explicit is better than relying on defaults for the active provider):
   ```yaml
   openai_compat:
     base_url: https://api.fireworks.ai/inference/v1
     model: accounts/fireworks/models/gpt-oss-20b
     max_tokens: 1024
     timeout: 60
     api_key_env: FIREWORKS_API_KEY
   ```

3. Rewrite `llm/provider.py`:
   - Replace `AIProvider` ABC with `LLMProvider` ABC.
   - Add `LLMResponse` as a `@dataclass(frozen=True)` (consistent with DECISION-005 —
     dataclass for internal DTOs, not Pydantic):
     ```python
     @dataclass(frozen=True)
     class LLMResponse:
         content: str
         model: str
         usage: dict
     ```
   - `LLMProvider` has one abstract method:
     ```python
     class LLMProvider(ABC):
         @abstractmethod
         async def complete(self, system: str, user: str) -> Result[LLMResponse]: ...
     ```
   - No `embed()` on `LLMProvider` — embeddings are `sentence-transformers` responsibility
     in Phase 3 (retrieval), not the LLM provider.
   - Update `get_provider(task, config) -> LLMProvider`:
     - `case "claude":` imports `ClaudeProvider` (updated in Phase 5)
     - `case "ollama":` imports `OllamaProvider` (updated in Phase 5)
     - `case "openai":` imports `OpenAIProvider` (built in Phase 4)
     - The return type annotation changes from `AIProvider` to `LLMProvider`.

**Files to modify**:
- `core/config.py` — add `"openai"` to `Provider`; add `OpenAICompatConfig`; add field to `MainConfig`
- `config/config.yaml` — add `openai_compat:` section
- `llm/provider.py` — full rewrite: `LLMProvider` ABC + `LLMResponse` dataclass + updated factory

**Test criteria**:
- [ ] `from llm.provider import LLMProvider, LLMResponse, get_provider` imports cleanly
- [ ] `LLMResponse(content="x", model="y", usage={})` is frozen (attempting `.content = "z"` raises `FrozenInstanceError`)
- [ ] `from core.config import OpenAICompatConfig` imports and instantiates with defaults
- [ ] `uv run pytest tests/test_core/test_config.py -v` still all green (existing config tests pass)
- [ ] `uv run pytest -m "not smoke and not integration" -v` full suite still green

**Status**: [x] done
**Completed**: 2026-05-14
**Notes**: Added OpenAICompatConfig, "openai" to Provider type. Rewrote provider.py to LLMProvider ABC + LLMResponse frozen dataclass. Updated test_config.py::test_rejects_unknown_provider_value — was checking "openai" was invalid, now openai is valid so updated to use "unknown_provider" instead.

---

### Phase 4 — `llm/openai_provider.py`
**Goal**: One working async `complete()` call to any OpenAI-compatible endpoint, returning
`Result[LLMResponse]`. Defaults to Fireworks. Switchable to any vendor by changing
`config/config.yaml` `openai_compat` section.

**Steps**:
1. Create `llm/openai_provider.py`:
   - Local `_OpenAICompatSettings(BaseSettings)` that reads the env var named by
     `config.api_key_env`:
     ```python
     class _OpenAICompatSettings(BaseSettings):
         model_config = SettingsConfigDict(env_file=".env", extra="ignore")
         api_key: str  # required — no default; startup fails if missing
     ```
     Instantiated as `_OpenAICompatSettings(_env_file=".env",
     **{config.api_key_env.lower(): ...})` — use `model_fields_set` trick or simpler:
     read via `os.environ.get(config.api_key_env)` directly. **Prefer the simpler
     `os.environ` approach here** — `BaseSettings` field mapping is not flexible enough
     for a runtime-configurable env var name. Raise `ConfigError` at `__init__` if missing.
   - `OpenAIProvider(LLMProvider)`:
     ```python
     class OpenAIProvider(LLMProvider):
         def __init__(self, config: OpenAICompatConfig) -> None:
             api_key = os.environ.get(config.api_key_env)
             if not api_key:
                 raise ConfigError(
                     f"{config.api_key_env} not set. Add it to .env."
                 )
             self._client = openai.AsyncOpenAI(
                 api_key=api_key,
                 base_url=config.base_url,
                 timeout=config.timeout,
             )
             self._model     = config.model
             self._max_tokens = config.max_tokens

         async def complete(self, system: str, user: str) -> Result[LLMResponse]:
             try:
                 resp = await self._client.chat.completions.create(
                     model=self._model,
                     max_tokens=self._max_tokens,
                     messages=[
                         {"role": "system", "content": system},
                         {"role": "user",   "content": user},
                     ],
                 )
                 usage = resp.usage.model_dump() if resp.usage else {}
                 return Success(LLMResponse(
                     content=resp.choices[0].message.content or "",
                     model=resp.model,
                     usage=usage,
                 ))
             except openai.APIError as exc:
                 return Failure(
                     error=str(exc),
                     recoverable=True,
                     context={"provider": "openai_compat", "model": self._model},
                 )
     ```
   - Note: `resp.usage.model_dump()` converts `CompletionUsage` to plain dict —
     required for JSON serialisation downstream (audit log). Never store the object.

2. Wire into the factory: add `case "openai": from llm.openai_provider import OpenAIProvider; return OpenAIProvider(config.openai_compat)` in `get_provider()`.

3. Write `tests/test_llm/test_openai_provider.py`:
   - Unit test: mock `openai.AsyncOpenAI`, assert `complete()` returns `Success(LLMResponse)`
     with correct fields; assert `APIError` maps to `Failure(recoverable=True)`.
   - Integration test (marked `@pytest.mark.integration @pytest.mark.asyncio`): calls real
     Fireworks endpoint with `system="respond with one word"`, `user="ping"`, asserts
     `isinstance(result, Success)` and `result.value.content` is non-empty.

**Files to modify**:
- `llm/openai_provider.py` — create
- `llm/provider.py` — add `case "openai":` to factory (minimal change)
- `tests/test_llm/test_openai_provider.py` — create

**Test criteria**:
- [ ] Unit test: `complete()` with mocked client returns `Success(LLMResponse(content=..., model=..., usage={...}))`
- [ ] Unit test: `openai.APIError` in mocked client returns `Failure(recoverable=True)`
- [ ] Unit test: missing env var at `__init__` raises `ConfigError`
- [ ] `uv run pytest tests/test_llm/test_openai_provider.py -v -m "not integration"` all green
- [ ] Integration (manual, needs `FIREWORKS_API_KEY`): `uv run pytest tests/test_llm/test_openai_provider.py -v -m integration` passes

**Status**: [x] done
**Completed**: 2026-05-14
**Notes**: Created llm/openai_provider.py with OpenAIProvider(LLMProvider). Reads API key via os.environ.get(config.api_key_env) at __init__; raises ConfigError if missing. complete() wraps openai.APIError → Failure(recoverable=True). All 7 unit tests pass. Full suite 199 passed.

---

### Phase 5 — Migrate `claude_provider.py` + `ollama_provider.py`
**Goal**: Both existing providers implement the new `LLMProvider` ABC — async `complete()`
returning `Result[LLMResponse]`. This unblocks Phase 1 (Capture), which routes to `claude`.

**Steps — `claude_provider.py`**:
1. Replace `Anthropic()` with `AsyncAnthropic()`.
2. Rename `chat()` to `complete(system, user)` — make it `async def`.
3. Change return type to `Result[LLMResponse]`.
4. Remove `json_mode` parameter — JSON formatting belongs in prompt YAML system field.
5. Remove `embed()` — not in `LLMProvider` ABC.
6. Wrap API call in try/except; catch `anthropic.APIError` (and subclasses); return
   `Failure(recoverable=True, ...)` for network errors, `Failure(recoverable=False, ...)` for
   auth errors (`anthropic.AuthenticationError`).
7. `LLMResponse.usage` — populate from `response.usage.model_dump()`.

   ```python
   # llm/claude_provider.py (key changes — not full file)
   from anthropic import AsyncAnthropic, APIError, AuthenticationError

   class ClaudeProvider(LLMProvider):
       def __init__(self, config: ClaudeConfig, task: Task = "capture") -> None:
           api_key = os.environ.get("ANTHROPIC_API_KEY")
           if not api_key:
               raise ConfigError("ANTHROPIC_API_KEY not set.")
           self._client    = AsyncAnthropic(api_key=api_key)
           self._model     = config.synthesis_model if task in _SYNTHESIS_TASKS else config.model
           self._max_tokens = config.max_tokens

       async def complete(self, system: str, user: str) -> Result[LLMResponse]:
           try:
               resp = await self._client.messages.create(
                   model=self._model,
                   max_tokens=self._max_tokens,
                   system=system,
                   messages=[{"role": "user", "content": user}],
               )
               usage = resp.usage.model_dump() if resp.usage else {}
               return Success(LLMResponse(
                   content=resp.content[0].text,
                   model=resp.model,
                   usage=usage,
               ))
           except AuthenticationError as exc:
               return Failure(error=str(exc), recoverable=False,
                              context={"provider": "claude"})
           except APIError as exc:
               return Failure(error=str(exc), recoverable=True,
                              context={"provider": "claude"})
   ```

**Steps — `ollama_provider.py`**:
1. Rename `chat()` to `complete(system, user)` — make it `async def`.
2. Wrap the existing sync `requests.post()` calls in `asyncio.to_thread()` — no new dep
   (`httpx` is NOT added; `requests` is already installed; `asyncio.to_thread` is stdlib).
3. Remove `embed()` — out of scope for `LLMProvider`.
4. Wrap in try/except; return `Failure(recoverable=True)` on `ConnectionError`.
5. Change return type to `Result[LLMResponse]`.

   ```python
   # llm/ollama_provider.py — complete() sketch
   async def complete(self, system: str, user: str) -> Result[LLMResponse]:
       try:
           payload = {"model": self.chat_model, "prompt": user,
                      "system": system, "stream": False}
           raw = await asyncio.to_thread(self._post, "/api/generate", payload)
           return Success(LLMResponse(
               content=raw.get("response", ""),
               model=self.chat_model,
               usage={},
           ))
       except ConnectionError as exc:
           return Failure(error=str(exc), recoverable=True,
                          context={"provider": "ollama"})
   ```

6. Write `tests/test_llm/test_claude_provider.py` and
   `tests/test_llm/test_ollama_provider.py` — unit tests only (mock the HTTP client).

**Files to modify**:
- `llm/claude_provider.py` — full rewrite to async `complete()` + `Result`
- `llm/ollama_provider.py` — update `complete()` async via `asyncio.to_thread()`
- `tests/test_llm/test_claude_provider.py` — create
- `tests/test_llm/test_ollama_provider.py` — create

**Test criteria**:
- [ ] `ClaudeProvider` is a subclass of `LLMProvider` (ABC conformance check)
- [ ] `OllamaProvider` is a subclass of `LLMProvider` (ABC conformance check)
- [ ] Unit test: Claude `complete()` with mocked `AsyncAnthropic` returns `Success(LLMResponse)`
- [ ] Unit test: Claude `AuthenticationError` returns `Failure(recoverable=False)`
- [ ] Unit test: Claude `APIError` returns `Failure(recoverable=True)`
- [ ] Unit test: Ollama `complete()` with mocked `_post` returns `Success(LLMResponse)`
- [ ] Unit test: Ollama `ConnectionError` returns `Failure(recoverable=True)`
- [ ] `uv run pytest tests/test_llm/ -v -m "not integration"` all green
- [ ] `uv run pytest -m "not smoke and not integration" -v` full suite still green

**Status**: [x] done
**Completed**: 2026-05-14
**Notes**: Rewrote ClaudeProvider to LLMProvider ABC with async complete() + Result[LLMResponse]. Replaced Anthropic() with AsyncAnthropic(). AuthenticationError → Failure(recoverable=False); other APIError → Failure(recoverable=True). Added load_dotenv() at __init__ (same pattern as OpenAIProvider). Rewrote OllamaProvider to LLMProvider ABC; complete() wraps sync _post() in asyncio.to_thread(). embed() removed from both. 213 tests pass.

---

## Open Questions

All research OQs resolved in this plan (see Approach section). No blocking open questions remain.

---

## Out of Scope

- `llm/ollama_provider.py` embedding support — embeddings are `sentence-transformers` in Phase 3.
- `json_mode` equivalent — JSON formatting moves to prompt YAML `system` field in Phase 1.
- HTTP transport / streaming — `complete()` is single-turn request/response only.
- Prompt caching, tool use, multi-turn — Phase 1+ concerns.
- Ollama async rewrite via `httpx` — `asyncio.to_thread()` is sufficient for Phase 0; defer httpx if Ollama becomes performance-critical.
- `llm/__init__.py` — Python 3 implicit namespace packages work without it; add only if a Phase 1 import fails.