# Research: claude_cli_provider
_Last updated: 2026-05-22_

## Overview

A `ClaudeCliProvider` lets the system call Claude without an Anthropic API key by spawning
the `claude` CLI binary as a subprocess. The binary handles auth via the user's Claude Code
session. This is the pattern the reference project uses as its **default inference method** —
a deliberate choice so users who already have Claude Code installed need zero extra credentials.

The new provider is a drop-in `LLMProvider` implementation. Pipelines call `get_provider(task,
CONFIG.main)` and get back a `ClaudeCliProvider` if the task's provider is configured to
`"claude_cli"`. No pipeline code changes are needed.

---

## Key Components

| File | Role | Action |
|---|---|---|
| `llm/claude_cli_provider.py` | New provider — subprocess approach | Create |
| `core/config.py` | Add `ClaudeCliConfig` + update `Provider` type alias | Edit |
| `config/config.yaml` | Add `claude_cli:` section | Edit |
| `llm/provider.py` | Add `"claude_cli"` branch in `get_provider()` | Edit |
| `tests/test_llm/test_claude_cli_provider.py` | Unit + integration tests | Create |

---

## How It Works

### Reference project (JavaScript) — `src/classify/classifier.js`

```javascript
const CLAUDE_PATH = process.env.CLAUDE_PATH || 'claude';
const CLASSIFY_MODEL = process.env.CLASSIFY_MODEL || 'claude-haiku-4-5-20251001';

const proc = spawn(CLAUDE_PATH, [
  '-p', '--model', CLASSIFY_MODEL,
  '--output-format', 'json',
  '--max-turns', '1',
], {
  env: { ...process.env, CLAUDE_CODE_ENTRYPOINT: 'cli' },
  timeout: 60000,
  stdio: ['pipe', 'pipe', 'pipe'],
});

proc.stdin.write(prompt);  // combined system + user content
proc.stdin.end();

// stdout is JSON: { result: "...", cost_usd: ..., duration_ms: ... }
const response = JSON.parse(stdout);
const text = response.result;
```

Three files in the reference use this identical pattern:
- `src/classify/classifier.js` — batch classification
- `src/classify/summarizer.js` — note summarization
- `src/safety/review.js` — destructive action review (multi-model)

### CLI flags decoded

| Flag | Meaning |
|---|---|
| `-p` | Print mode: non-interactive, accepts stdin prompt, exits after response |
| `--model <name>` | Model to use. Any `claude-*` model string. |
| `--output-format json` | stdout is JSON (not plain text). Structured response. |
| `--max-turns 1` | Single-turn exchange. Prevents the CLI from asking follow-up questions. |
| `CLAUDE_CODE_ENTRYPOINT=cli` | Tells the binary it's running as a headless subprocess, not an interactive session. Required for reliable non-interactive operation. |

### JSON output schema

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "result": "the AI response text here",
  "session_id": "sess_abc123",
  "cost_usd": 0.00042,
  "duration_ms": 1847
}
```

Key fields:
- `result` — the text response (may contain markdown fencing despite `--output-format json` — strip it)
- `cost_usd` — useful for audit/cost tracking in `LLMResponse.usage`
- `duration_ms` — latency
- `is_error` — boolean; if `true`, `result` contains the error message

**Important:** `cost_usd` and `duration_ms` are available but **not token counts**. The usage
dict will differ from `ClaudeProvider` (which provides `input_tokens`, `output_tokens`).

### System + user mapping

The `LLMProvider.complete(system, user)` interface has two separate strings. The CLI's `-p`
mode accepts the prompt via stdin as a single user message. Two mapping strategies:

**Option A — Combine into single stdin string (reference project approach):**
```
{system}

---

{user}
```

The entire string is treated as the user turn. Works but the LLM sees everything as user
content — no formal system/user role separation.

**Option B — Use `--system-prompt` flag (cleaner role separation):**
```bash
claude -p --system-prompt "{system}" --model haiku --output-format json --max-turns 1
# user content piped to stdin
```

Option B preserves the role contract and aligns better with the `complete(system, user)` ABC.
**Option B is recommended**, with Option A as a fallback for older CLI versions.

### Python async adaptation

Using `asyncio.create_subprocess_exec()` — non-blocking, fits DECISION-010 (async pipeline):

```python
import asyncio
import json
import os
import shutil

async def _run_claude(
    cli_path: str,
    model: str,
    system: str,
    user: str,
    timeout: int,
) -> tuple[str, str]:
    """Spawns claude CLI. Returns (stdout_text, stderr_text)."""
    env = {**os.environ, "CLAUDE_CODE_ENTRYPOINT": "cli"}
    proc = await asyncio.create_subprocess_exec(
        cli_path,
        "-p",
        "--system-prompt", system,
        "--model", model,
        "--output-format", "json",
        "--max-turns", "1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(
        proc.communicate(input=user.encode()),
        timeout=timeout,
    )
    return stdout_bytes.decode(), stderr_bytes.decode()
```

Then in `complete()`:

```python
raw_output, stderr = await _run_claude(...)
data = json.loads(raw_output)
if data.get("is_error") or proc.returncode != 0:
    return Failure(error=data.get("result", stderr), recoverable=True, ...)
text = data.get("result", "")
# Strip markdown fencing if present
text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
return Success(LLMResponse(
    content=text,
    model=model,
    usage={"cost_usd": data.get("cost_usd", 0.0), "duration_ms": data.get("duration_ms", 0)},
))
```

---

## Config Design

### New `ClaudeCliConfig` in `core/config.py`

```python
class ClaudeCliConfig(BaseModel):
    """Claude CLI subprocess provider settings."""
    cli_path:        str = "claude"                    # override with CLAUDE_PATH env or explicit path
    model:           str = "claude-haiku-4-5-20251001" # fast tasks
    synthesis_model: str = "claude-sonnet-4-20250514"  # synthesis/documentation tasks
    embedding_model: str = "voyage-3"                  # unused — interface parity with other providers
    max_tokens:      int = 1024                         # NOTE: CLI ignores this — only here for parity
    timeout:         int = 60                           # seconds; passed to asyncio.wait_for
```

**Important:** `max_tokens` has no CLI flag equivalent in `-p` mode as of Claude CLI current
version. Include the field for interface parity; document it as advisory only.

### `Provider` type alias update

```python
# core/config.py line 39
type Provider = Literal["claude", "claude_cli", "ollama", "openai"]
```

### `MainConfig` addition

```python
class MainConfig(BaseModel):
    ...
    claude_cli: ClaudeCliConfig = Field(default_factory=ClaudeCliConfig)
```

### `config/config.yaml` addition

```yaml
claude_cli:
  cli_path: claude          # 'claude' assumes it's on PATH; override if installed elsewhere
  model: claude-haiku-4-5-20251001
  synthesis_model: claude-sonnet-4-20250514
  timeout: 60
```

### `providers:` section example

```yaml
providers:
  capture: claude_cli        # uses Claude CLI, no API key
  classify: claude_cli
  synthesis: claude          # still use API for synthesis (Sonnet 4 via API is faster than CLI)
  embeddings: ollama
  self_learn: claude_cli
```

---

## Factory Wiring

In `llm/provider.py`, add one branch to `get_provider()`:

```python
case "claude_cli":
    from llm.claude_cli_provider import ClaudeCliProvider
    return ClaudeCliProvider(config.claude_cli, task=task)
```

---

## Differences from `ClaudeProvider` (API)

| Dimension | `ClaudeProvider` | `ClaudeCliProvider` |
|---|---|---|
| Auth | `ANTHROPIC_API_KEY` env var | Claude Code session (no API key) |
| Transport | HTTPS via `anthropic` SDK | Subprocess stdin/stdout |
| Latency | ~1–3s (direct API) | ~2–5s (CLI startup overhead) |
| Token counts | In `usage` dict (`input_tokens`, `output_tokens`) | Not available; `cost_usd` + `duration_ms` instead |
| `max_tokens` | Honoured | Not passable via `-p` flags |
| Streaming | SDK supports it (not used by us) | Not available in subprocess mode |
| Concurrency | Safe (async SDK) | Process-per-call; high concurrency = many spawned processes |
| Binary required | No | Yes (`claude` on PATH or configured path) |

---

## Edge Cases & Silent Failure Modes

1. **`claude` binary not on PATH.** `asyncio.create_subprocess_exec()` raises `FileNotFoundError`.
   Catch it in `__init__` via `shutil.which(cli_path)` and raise `ConfigError` at init time
   rather than at the first `complete()` call. This surfaces the misconfiguration early (fail-fast
   principle, same as `ClaudeProvider` checking the API key in `__init__`).

2. **Timeout: `asyncio.wait_for` raises `TimeoutError`.** Wrap in `except asyncio.TimeoutError`
   and return `Failure(recoverable=True)`. The spawned subprocess must also be killed: call
   `proc.kill()` in the except block or use an async context manager to ensure cleanup.

3. **Non-zero exit code with no JSON on stdout.** The CLI may fail before producing output (e.g.,
   auth error, model not found). `json.loads("")` raises `json.JSONDecodeError`. Catch it and
   return `Failure(error=f"claude CLI exited {code}: {stderr}", recoverable=False)`.

4. **`--system-prompt` flag availability.** The `--system-prompt` flag was added in a specific
   CLI version. Older installs may not support it, silently treating `-p` args differently.
   The provider should document the minimum required CLI version. As a safety net, if spawning
   fails with a usage error, fall back to combining system+user into a single stdin prompt
   (Option A). This fallback should be explicit, not silent — log a warning.

5. **Markdown fencing in `result`.** The reference strips ```` ```json ``` ```` fences from the result
   even with `--output-format json`. Our pipelines call `prompt_loader.PROMPTS["..."].render()`
   and pass the rendered prompt to `complete()`. If the prompt YAML's system message requests
   JSON output, the response may still be fenced. Strip it in the provider before returning.

6. **Concurrent subprocess spawning in MCP daemon (Phase 4).** Each `complete()` call spawns
   a new process. Under heavy MCP load (many tool calls in parallel), this could exhaust OS
   process limits. For Phase 1/2 CLI use, this is not a concern. Flag it for Phase 4 planning
   (same as TD-010 for Ollama thread overhead).

7. **`CLAUDE_CODE_ENTRYPOINT=cli` in env.** The reference project sets this. Without it, the
   CLI may behave interactively, hanging indefinitely instead of returning. This env var must
   be set on every subprocess call. Use `{**os.environ, "CLAUDE_CODE_ENTRYPOINT": "cli"}` —
   never `CLAUDE_CODE_ENTRYPOINT` alone (would lose `PATH` and other essential env vars).

---

## Dependencies & Coupling

| Depends on | Used for |
|---|---|
| `core/config.py` → `ClaudeCliConfig` | Config object |
| `core/config.py` → `Task` type alias | Task routing |
| `core/result.py` → `Success`, `Failure` | Return type |
| `llm/provider.py` → `LLMProvider`, `LLMResponse`, `SYNTHESIS_TASKS` | ABC + DTO |
| Python stdlib: `asyncio`, `json`, `os`, `shutil` | No new dependencies |

**Zero new package dependencies.** This is a key advantage over `ClaudeProvider` (needs
`anthropic` SDK) and `OpenAIProvider` (needs `openai` SDK). All stdlib.

---

## Extension Points

| Component | Extensible? | How |
|---|---|---|
| `cli_path` | Yes — config field | Set in `claude_cli.cli_path` in YAML or `CLAUDE_PATH` env |
| Model selection | Yes — config fields | `model` / `synthesis_model` in YAML, `SYNTHESIS_TASKS` constant |
| `--output-format` | No — hardcoded `json` | JSON is required for structured parsing; changing breaks `result` extraction |
| Prompt mapping strategy (Option A/B) | Flag-guarded | `_SUPPORTS_SYSTEM_PROMPT` bool in provider; detect on first call |
| Timeout | Yes — config field | `timeout` in YAML |

---

## Open Questions

| ID | Question | What was checked |
|---|---|---|
| OQ-CLI-1 | Does the current installed Claude CLI version support `--system-prompt` flag? If not, Option B fails silently. | Verified, Claude CLI does support `--system-prompt` |
| OQ-CLI-2 | Should `ClaudeCliConfig.cli_path` be overridable via env var `CLAUDE_PATH` (like the reference project), or only via YAML config? | Reference uses `process.env.CLAUDE_PATH || 'claude'`. Our config follows "env vars for secrets, YAML for behavior". `cli_path` is a behavior value (non-secret path), so YAML is the right home. But `CLAUDE_PATH` env override is a legitimate escape hatch for CI environments. Resolution: YAML wins; `ClaudeCliProvider.__init__` can also check `os.environ.get("CLAUDE_PATH")` as a fallback before raising `ConfigError`. |
| OQ-CLI-3 | How should cost/usage data be used in audit log? `ClaudeProvider` stores `input_tokens`/`output_tokens`. `ClaudeCliProvider` stores `cost_usd`/`duration_ms`. Downstream Phase 8 (briefing) reads `audit_log`. If it tries to sum token counts across providers, CLI-sourced entries will have zero. | Checked `storage/audit_log.py` — `usage` is stored as JSON blob. No schema enforces specific keys. Phase 8 reads `usage` free-form. As long as the briefing reads defensively (`usage.get("input_tokens", 0)`), mixed providers are safe. Document the difference in the provider docstring. |
| OQ-CLI-4 | Should `ClaudeCliProvider.__init__` call `shutil.which(cli_path)` eagerly and raise `ConfigError` if binary not found? Or defer the error to the first `complete()` call? | Checked pattern in `ClaudeProvider` — it checks `ANTHROPIC_API_KEY` in `__init__` and raises `ConfigError` immediately. Consistent approach: check `shutil.which()` in `__init__`. This surfaces misconfiguration at startup, not mid-pipeline. |

---

## Reference Project Patterns

### Pattern: subprocess spawn for headless LLM calls

**What it is:** Spawn `claude -p --model X --output-format json --max-turns 1`, pipe prompt to stdin, parse JSON stdout.

**Why it exists in the reference project:** The reference project prioritizes zero-credential setup. Developers who have Claude Code installed need no additional API keys. The CLI binary handles OAuth via the Claude Code session. This is the reference's *only* inference path — they have no direct API provider.

**Does the reason apply here?** Partially. Our primary provider is `ClaudeProvider` (direct API). But the CLI provider adds:
1. A fallback path for users without an API key (same reason as reference)
2. A way to use models not yet on the API (rare)
3. A local rate-limit bypass (CLI may queue differently than API)

**Adopt, adapt, or skip?** **Adapt.** The subprocess approach is directly portable to Python (`asyncio.create_subprocess_exec`). Key divergences from reference:
- Reference combines system+user into one string; we should use `--system-prompt` flag for clean role separation.
- Reference has no `Result` type; we wrap everything in `Success`/`Failure`.
- Reference has no async; we use `asyncio.create_subprocess_exec` + `asyncio.wait_for`.
- Reference hardcodes `CLAUDE_CODE_ENTRYPOINT: 'cli'`; we carry it forward as it's required.

### Pattern: `CLAUDE_PATH` env var override

**What it is:** `const CLAUDE_PATH = process.env.CLAUDE_PATH || 'claude'`

**Why it exists:** In CI/CD environments or when multiple Claude versions are installed, the binary may not be on `PATH`. The env var lets operators override the path without changing code.

**Adopt?** Yes — as a secondary fallback in `__init__` (YAML config wins; env var is the escape hatch).

---

## Technical Debt Spotted

| ID | What | When |
|---|---|---|
| TD-CLI-1 | `max_tokens` field exists in `ClaudeCliConfig` for interface parity but has no CLI flag equivalent. Future CLI versions may add `--max-tokens`. | Address when CLI adds the flag. Document as advisory-only for now. |
| TD-CLI-2 | Per-process subprocess overhead. Each `complete()` call starts a new `claude` process. Under Phase 4 MCP daemon load, this may be slow. Consider a persistent `claude` session if the CLI gains a server mode. | Phase 4. Same class as TD-010 (Ollama thread overhead). |
| TD-CLI-3 | `--system-prompt` flag availability is version-gated. No version check in provider. | Address if OQ-CLI-1 reveals the flag is missing on any supported CLI version. |

---

## Implementation Checklist

1. **`core/config.py`**
   - Add `ClaudeCliConfig` model (fields: `cli_path`, `model`, `synthesis_model`, `embedding_model`, `max_tokens`, `timeout`)
   - Update `Provider` type alias: add `"claude_cli"`
   - Add `claude_cli: ClaudeCliConfig` field to `MainConfig`

2. **`config/config.yaml`**
   - Add `claude_cli:` section with defaults

3. **`llm/claude_cli_provider.py`**
   - `ClaudeCliProvider(LLMProvider)` — `__init__` checks binary exists via `shutil.which`
   - `async def complete(system, user) -> Result[LLMResponse]`
   - Uses `asyncio.create_subprocess_exec` with `CLAUDE_CODE_ENTRYPOINT=cli`
   - Option B (`--system-prompt` flag) primary; Option A (combine) fallback
   - Timeout via `asyncio.wait_for`; kills process on timeout
   - Strips markdown fencing from `result` field
   - Returns `usage={"cost_usd": ..., "duration_ms": ...}` — no token counts

4. **`llm/provider.py`**
   - Add `case "claude_cli":` branch in `get_provider()`

5. **`tests/test_llm/test_claude_cli_provider.py`**
   - Unit test: mock `asyncio.create_subprocess_exec`, verify JSON parsing + `Success` return
   - Unit test: mock returns `is_error: true` → verify `Failure` return
   - Unit test: mock raises `TimeoutError` → verify `Failure(recoverable=True)`
   - Unit test: `shutil.which` returns `None` → verify `ConfigError` in `__init__`
   - Integration test (`@pytest.mark.smoke`): real `claude` binary call (skipped in CI without binary)
