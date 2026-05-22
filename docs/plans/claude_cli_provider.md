# Plan: claude_cli_provider
_Last updated: 2026-05-22_
_Status: [x] done_

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          LLM Layer (llm/)                           │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  LLMProvider (Abstract Base — exists)                        │   │
│  │  The contract all providers must follow                      │   │
│  │  · complete(system_instructions, user_content)               │   │
│  │    → either Success(response) or Failure(error)              │   │
│  └────────────┬──────────────────┬──────────────────────────────┘   │
│               │                  │                                  │
│   ┌───────────▼──────┐   ┌───────▼────────────────────────────┐    │
│   │  ClaudeProvider  │   │  ClaudeCliProvider  ← NEW           │    │
│   │  (exists)        │   │  Calls Claude via CLI subprocess     │    │
│   │  Uses HTTPS API  │   │  No API key needed                  │    │
│   │  Needs API key   │   │  · complete(system, user)           │    │
│   └──────────────────┘   └───────────────┬─────────────────────┘    │
│                                          │ spawns process           │
│   ┌──────────────────┐                   ▼                          │
│   │  OllamaProvider  │         ┌──────────────────┐                 │
│   │  (exists)        │         │  "claude" binary │                 │
│   └──────────────────┘         │  on your machine │                 │
│                                └──────────────────┘                 │
│   ┌──────────────────┐                                              │
│   │  OpenAIProvider  │  [extensible: protocol — all providers       │
│   │  (exists)        │   implement the same interface]              │
│   └──────────────────┘                                              │
└─────────────────────────────────────────────────────────────────────┘

         ┌───────────────────────────────────────────────────────┐
         │  get_provider() factory  (llm/provider.py — edit)     │
         │  "Which provider should handle this task?"            │
         │                                                       │
         │  "claude_cli" ──────────────▶ ClaudeCliProvider       │
         │  "claude"     ──────────────▶ ClaudeProvider          │
         │  "ollama"     ──────────────▶ OllamaProvider          │
         │  "openai"     ──────────────▶ OpenAIProvider          │
         └───────────────────────────────────────────────────────┘
```

**Call flow:**

```
Pipeline stage                ClaudeCliProvider             "claude" binary
(e.g. summarize)                  (new)                     (on machine)
      │                              │                              │
      │  complete(                   │                              │
      │    system="You are...",      │                              │
      │    user="Summarise: ..."     │                              │
      │  )                           │                              │
      │─────────────────────────────▶│                              │
      │                              │  spawns subprocess           │
      │                              │  claude -p                   │
      │                              │  --system-prompt "You are…"  │
      │                              │  --model haiku               │
      │                              │  --output-format json        │
      │                              │  --max-turns 1               │
      │                              │─────────────────────────────▶│
      │                              │  "Summarise: ..." → stdin    │
      │                              │─────────────────────────────▶│
      │                              │                              │
      │                              │  ◀──── JSON on stdout ───────│
      │                              │  { "result": "This note…",   │
      │                              │    "cost_usd": 0.0004,       │
      │                              │    "is_error": false }       │
      │                              │                              │
      │  ◀── Success(response) ──────│                              │
      │      content = "This note…"  │                              │
      │      model  = "claude-haiku" │                              │
      │      usage  = {cost_usd:...} │                              │
```

---

## Approach

Add `ClaudeCliProvider` as a fourth `LLMProvider` implementation. It spawns the `claude`
binary as an async subprocess using `asyncio.create_subprocess_exec` (fits DECISION-010 —
no blocking). System prompt is passed via `--system-prompt` flag; user content via stdin.
No new package dependencies — stdlib only. Three files get new content
(`core/config.py`, `llm/claude_cli_provider.py`, `llm/provider.py`) and one YAML gets a
new section (`config/config.yaml`). Nothing else changes.

---

## Phases

### Phase 1 — Config additions
**Goal**: Add `ClaudeCliConfig` to `core/config.py`, update the `Provider` type alias, wire into `MainConfig`, and add the `claude_cli:` section to `config/config.yaml`.

**Steps**:
1. In `core/config.py`, add `ClaudeCliConfig` model after `OpenAICompatConfig`:
   ```python
   class ClaudeCliConfig(BaseModel):
       """Claude CLI subprocess provider settings."""
       cli_path:        str = "claude"
       model:           str = "claude-haiku-4-5-20251001"
       synthesis_model: str = "claude-sonnet-4-20250514"
       embedding_model: str = "voyage-3"   # interface parity; not used by CLI
       max_tokens:      int = 1024          # interface parity; CLI has no --max-tokens flag
       timeout:         int = 60            # seconds passed to asyncio.wait_for
   ```
2. Update `Provider` type alias (line ~39):
   ```python
   type Provider = Literal["claude", "claude_cli", "ollama", "openai"]
   ```
3. Add `claude_cli: ClaudeCliConfig` field to `MainConfig`:
   ```python
   claude_cli: ClaudeCliConfig = Field(default_factory=ClaudeCliConfig)
   ```
4. In `config/config.yaml`, add `claude_cli:` section with defaults.

**Files to modify**:
- `core/config.py` — add `ClaudeCliConfig`, update `Provider` type alias, add field to `MainConfig`
- `config/config.yaml` — add `claude_cli:` section

**Test criteria**:
- [ ] `uv run pytest tests/test_core/test_config.py -m "not smoke"` passes with no new failures
- [ ] `from core.config import CONFIG; CONFIG.main.claude_cli.cli_path` returns `"claude"` in a Python REPL (with vault on disk)
- [ ] Setting `providers.capture: claude_cli` in `config.yaml` does not raise a validation error on load

**Status**: [x] done

**Completed**: 2026-05-22
**Notes**: Added `ClaudeCliConfig` model, updated `Provider` type alias to include `"claude_cli"`, added `claude_cli` field to `MainConfig`, added `claude_cli:` section to `config/config.yaml`. Added 8 unit tests (7 in `TestClaudeCliConfig`, 1 in `TestProvidersConfig`). Pre-existing mypy `import-untyped` warning for `yaml` not introduced by this change.

---

### Phase 2 — Provider implementation
**Goal**: Implement `ClaudeCliProvider` in `llm/claude_cli_provider.py` — subprocess spawn, JSON parsing, `Result` return, proper timeout/cleanup.

**Steps**:
1. Create `llm/claude_cli_provider.py` with class `ClaudeCliProvider(LLMProvider)`.
2. In `__init__`:
   - Resolve model: `synthesis_model` if `task in SYNTHESIS_TASKS`, else `model`
   - Store `cli_path`, `timeout`, `_model`
   - Call `shutil.which(cli_path)` — raise `ConfigError` immediately if binary not found (fail-fast, same pattern as `ClaudeProvider` checking `ANTHROPIC_API_KEY`)
3. In `async def complete(system, user)`:
   - Build env: `{**os.environ, "CLAUDE_CODE_ENTRYPOINT": "cli"}`
   - Spawn with `asyncio.create_subprocess_exec`:
     ```
     cli_path, "-p",
     "--system-prompt", system,
     "--model", self._model,
     "--output-format", "json",
     "--max-turns", "1"
     ```
   - Pipe `user.encode()` to stdin via `asyncio.wait_for(proc.communicate(...), timeout=self._timeout)`
   - On `asyncio.TimeoutError`: kill process (`proc.kill()`), return `Failure(recoverable=True)`
   - Parse stdout as JSON; on `json.JSONDecodeError`: return `Failure(recoverable=True, context={"stderr": stderr[:200]})`
   - If `data.get("is_error")` or `proc.returncode != 0`: return `Failure(recoverable=True)`
   - Strip markdown fencing from `data["result"]` if present
   - Return `Success(LLMResponse(content=text, model=self._model, usage={"cost_usd": ..., "duration_ms": ...}))`
4. Handle `OSError` from `create_subprocess_exec` (binary disappeared after init): return `Failure(recoverable=False)`

**Files to modify**:
- `llm/claude_cli_provider.py` — create new file

**Test criteria**:
- [ ] File imports without error: `from llm.claude_cli_provider import ClaudeCliProvider`
- [ ] `ClaudeCliProvider` satisfies the `LLMProvider` ABC (no `TypeError` on instantiation with a mock config)

**Status**: [x] done

**Completed**: 2026-05-22
**Notes**: Created `llm/claude_cli_provider.py` with `ClaudeCliProvider(LLMProvider)`. `__init__` does eager `shutil.which` check (raises `ConfigError` if binary absent). `complete()` uses `asyncio.create_subprocess_exec` with `--output-format json --max-turns 1`, timeout via `asyncio.wait_for`, handles: `OSError` (binary vanished), `TimeoutError` (kills proc), `JSONDecodeError`, `is_error` flag, non-zero exit. `_strip_fence()` helper removes outer markdown fencing. Phase 2 tests confirm import and ABC compliance. Full test failure paths deferred to Phase 4.

---

### Phase 3 — Factory wiring
**Goal**: Register `"claude_cli"` in `get_provider()` so pipelines can use the new provider via config.

**Steps**:
1. In `llm/provider.py`, add branch inside `get_provider()` match statement:
   ```python
   case "claude_cli":
       from llm.claude_cli_provider import ClaudeCliProvider
       return ClaudeCliProvider(config.claude_cli, task=task)
   ```
   Place after the `"claude"` branch, before `"ollama"`.
2. Update the `ValueError` message in the `case _:` branch to include `"claude_cli"` in the valid options list.

**Files to modify**:
- `llm/provider.py` — add `case "claude_cli":` branch, update error message

**Test criteria**:
- [ ] `get_provider("capture", mock_config_with_claude_cli)` returns a `ClaudeCliProvider` instance
- [ ] `get_provider("synthesis", mock_config_with_claude_cli)` returns a `ClaudeCliProvider` using `synthesis_model`
- [ ] `get_provider("capture", config_with_unknown_provider)` still raises `ValueError` with correct message

**Status**: [x] done

**Completed**: 2026-05-22
**Notes**: Added `case "claude_cli":` branch in `get_provider()` after the `"claude"` branch. Updated `ValueError` message in `case _:` to include `"claude_cli"` in the valid options list. Added 4 unit tests in `TestGetProviderClaudeCli` to `tests/test_llm/test_provider.py`: instance type check, default model routing, synthesis model routing, and unknown provider error message. All 561 non-smoke tests pass.

---

### Phase 4 — Tests
**Goal**: Cover all failure paths with unit tests (no real binary needed) and one smoke test that calls the real `claude` binary.

**Steps**:
1. Create `tests/test_llm/test_claude_cli_provider.py`.
2. Unit test: mock `asyncio.create_subprocess_exec` to return clean JSON stdout → assert `Success(LLMResponse)` with correct `content`, `model`, `usage` keys.
3. Unit test: mock returns `{"is_error": true, "result": "auth failed"}` → assert `Failure(recoverable=True)`.
4. Unit test: mock raises `asyncio.TimeoutError` → assert `Failure(recoverable=True)` AND process is killed (verify `proc.kill()` called).
5. Unit test: mock stdout is not valid JSON → assert `Failure(recoverable=True)`.
6. Unit test: `shutil.which` returns `None` in `__init__` → assert `ConfigError` raised at construction time, not at `complete()` call.
7. Unit test: mock returns result with markdown fencing (`` ```json\n{...}\n``` ``) → assert stripped content in `Success`.
8. Smoke test (`@pytest.mark.smoke`): construct `ClaudeCliProvider` with real config, call `complete("You are a test assistant.", "Reply with the single word: OK")`, assert `Success` and `"OK"` in content. Skipped on machines without `claude` binary.
9. Update `tests/test_llm/test_claude_provider.py` pattern check: verify no regressions in existing provider tests.

**Files to modify**:
- `tests/test_llm/test_claude_cli_provider.py` — create new file

**Test criteria**:
- [ ] `uv run pytest tests/test_llm/test_claude_cli_provider.py -m "not smoke"` — all 7 unit tests pass
- [ ] `uv run pytest tests/ -m "not smoke"` — full suite still green (no regressions)
- [ ] Smoke test passes on a machine with `claude` binary installed and authenticated

**Status**: [x] done

**Completed**: 2026-05-22
**Notes**: Added 7 unit tests + 1 smoke test to `tests/test_llm/test_claude_cli_provider.py`. Test classes: `TestClaudeCliProviderInit` (binary not found → ConfigError), `TestClaudeCliProviderComplete` (happy path, is_error flag, timeout + kill, invalid JSON, nonzero exit code, markdown fence stripping). Smoke test skips automatically when `claude` binary absent. Fixed pre-existing mypy error in `_make_config` by replacing `**overrides` dict pattern with explicit typed kwargs. One RuntimeWarning from CPython 3.12 AsyncMock + asyncio.wait_for interaction in `test_invalid_json_stdout` — from mock internals, not production code; test passes correctly. Full suite: 568 passed (was 561), 0 failures.

---

## Open Questions

None. All research open questions resolved by design decisions above:
- **OQ-CLI-1** (`--system-prompt` flag): Option B chosen; flag is supported in all current Claude CLI versions. If binary is old and rejects the flag, the `OSError` / non-zero exit path in Phase 2 handles it.
- **OQ-CLI-2** (`CLAUDE_PATH` env override): Not implemented. `cli_path` in YAML is the single config point. Env var override is a future TD item if operators need it.
- **OQ-CLI-3** (usage dict format): `usage` stores `{"cost_usd": float, "duration_ms": int}` instead of token counts. Documented in `LLMResponse`. Phase 8 briefing must read `usage` defensively (`usage.get("input_tokens", 0)`).
- **OQ-CLI-4** (eager binary check): Resolved — `shutil.which` runs in `__init__`, raises `ConfigError` immediately.

---

## Out of Scope

- Streaming responses (CLI subprocess mode has no streaming equivalent)
- `max_tokens` enforcement (no `--max-tokens` flag in `claude -p` mode; field kept for interface parity only)
- `CLAUDE_PATH` environment variable override (deferred; YAML config is sufficient)
- Embeddings via CLI (no embedding mode in `claude -p`)
- Connection pooling / persistent subprocess session (single process per call; Phase 4 MCP daemon load revisit is TD-CLI-2)
