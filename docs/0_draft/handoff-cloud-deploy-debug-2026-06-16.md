# Handoff: Cloud Deployment Debug Session — 2026-06-16

## Session Summary

Debugged cascading failures in AgentBase cloud deployment: empty MCP queries, AI model rate limiting, thinking-token incompatibility, env var wipe on runtime update. System now operational with Gemma 4 model.

## Key Infrastructure

| Item | Value |
|------|-------|
| Runtime ID | `runtime-c6507401-2dff-42a3-b295-965940cbf195` |
| Endpoint ID | `endpoint-1cd2a3b9-8d82-461b-abfd-df9b6973721f` |
| Endpoint URL | `https://endpoint-1cd2a3b9-8d82-461b-abfd-df9b6973721f.agentbase-runtime.aiplatform.vngcloud.vn` |
| Image | `vcr.vngcloud.vn/111480-abp111749/kms-image:latest` |
| Flavor | `runtime-s2-general-2x4` |
| Env file | `.agentbase/runtime.env` |
| DB path (container) | `/data/kb.db` (via `KMS_DB_PATH`) |
| User timezone | UTC+7 (Vietnam) |

---

## Bugs Found and Fixed

### Bug 1: Prompts not found in container (FIXED, prior session)

- **Root cause:** `src/prompts/` is a data directory (YAML files, no `__init__.py`). `uv sync --no-editable` doesn't install it to site-packages. `prompt_loader.py` resolves `_PROMPTS_DIR` via `Path(__file__).parent.parent / "prompts"` which points to nonexistent path.
- **Fix:** Added `COPY src/prompts/ /usr/local/lib/python3.12/site-packages/prompts/` to Dockerfile line 42.
- **Status:** Deployed and verified.

### Bug 2: MCP queries return empty (ROOT CAUSED, partial fix)

- **Root cause chain:**
  1. Previous container received 5 uploads at ~09:20 UTC
  2. All hit `capture.summarize_failed` (prompts KeyError from Bug 1)
  3. `_best_effort_index()` (FTS + embeddings) only runs on summarize SUCCESS path (`capture.py:268`), skipped on FAILURE path (`capture.py:289`)
  4. Documents stored in DB but with NO summary, NO FTS entries, NO embeddings
  5. `kms_vault_info` empty: no `knowledge_entries`, no `inbox/%` vault_paths
  6. `kms_search` empty: no FTS/embedding entries exist
- **Fix applied:** Deleted all 5 stale docs via `/api/event` delete endpoint. Daemon re-uploads with working prompts.
- **Code fix still needed:** Add `_best_effort_index` call to summarize FAILURE path in `src/pipelines/capture.py:289` for resilience.

### Bug 3: DeepSeek model permanently 429 (FIXED)

- `deepseek/deepseek-v4-pro` returning 429 for 4+ hours — shared platform rate limit.
- Direct curl confirmed: DeepSeek -> 429, other models -> 200.
- **Fix:** Changed `config/config.yaml` model (see Bug 4 for final choice).

### Bug 4: Qwen3 thinking tokens (DISCOVERED, avoided)

- Switched to `qwen/qwen3-5-27b` first, but discovered Qwen3 spends ALL `max_tokens` on internal reasoning and returns `content: null`.
- With `max_tokens: 1024`, model produces zero usable content.
- **Workaround exists:** Pass `extra_body: {"chat_template_kwargs": {"enable_thinking": false}}` — confirmed working via curl. Requires code change to `OpenAICompatConfig` + `openai_provider.py` (add `extra_body` field + forward to `chat.completions.create()`).
- **Actual fix:** Switched to `google/gemma-4-31b-it` — no thinking mode, produces content directly, confirmed 200 OK in production.
- Config change: `config/config.yaml` lines 54-55: `model: google/gemma-4-31b-it`, `synthesis_model: google/gemma-4-31b-it`.

### Bug 5: Runtime update drops env vars (FIXED)

- `runtime.sh update` without `--env-file` **WIPES existing env vars**.
- Container crashed: `Vault root does not exist: /Users/lap14806/demo_vault` — config.yaml default used instead of `VAULT_ROOT=/data/vault` from env.
- **Fix:** ALWAYS include `--env-file .agentbase/runtime.env` in runtime update commands.
- **Correct update command:**
  ```bash
  bash .claude/skills/agentbase/scripts/runtime.sh update \
    runtime-c6507401-2dff-42a3-b295-965940cbf195 \
    --image vcr.vngcloud.vn/111480-abp111749/kms-image:latest \
    --from-cr --flavor runtime-s2-general-2x4 \
    --env-file .agentbase/runtime.env
  ```

### Bug 6: Embeddings rate limit (ONGOING, self-healing)

- Embedding endpoint (`/v1/embeddings`) returning 429 from earlier retry storm.
- Chat completions recovered (Gemma works); embeddings still throttled.
- Embeddings client in `retrieval/embeddings.py:49` creates `openai.OpenAI()` without `max_retries` param (uses SDK default).
- FTS search works without embeddings; vector search won't until rate limit clears.
- **Will self-heal** once rate limit window passes.

### Bug 7: Docker build pattern (LEARNED)

- **Working build command** (user-verified):
  ```bash
  docker build --platform linux/amd64 \
    -t vcr.vngcloud.vn/111480-abp111749/kms-image:v$(date +%Y%m%d%H%M%S) \
    -t vcr.vngcloud.vn/111480-abp111749/kms-image:latest .
  docker push vcr.vngcloud.vn/111480-abp111749/kms-image:v$(date +%Y%m%d%H%M%S)
  docker push vcr.vngcloud.vn/111480-abp111749/kms-image:latest
  ```
- Must tag with BOTH timestamped AND latest. Building with only `latest` caused deploy errors.
- Platform is `linux/amd64` (confirmed — not arm64).

---

## Config Changes Made (NOT committed)

- `config/config.yaml` line 54: `model: google/gemma-4-31b-it` (was `deepseek/deepseek-v4-pro`)
- `config/config.yaml` line 55: `synthesis_model: google/gemma-4-31b-it` (was `deepseek/deepseek-v4-pro`)
- `config/config.yaml` line 58: `max_retries: 2` (was `5`)

---

## Current State (end of session, ~22:30 UTC+7)

- **Runtime:** ACTIVE, container running with Gemma 4 model
- **Capture pipeline:** Working. 2 docs uploaded + summarized (200 OK from Gemma chat completions)
- **Classify pipeline:** Running. Both docs passed `capture.classify_ready`, queued for classify. Entity extraction making successful API calls (200 OK).
- **MCP server:** Responding. ListToolsRequest, ListPromptsRequest, ListResourcesRequest all 200 OK.
- **Embeddings:** Still 429 (rate limit from earlier storm). Will self-heal.
- **DB:** 2 documents (`Movie Ticket Platform Research_demo.pptx`, `Re_ [Claw-a-thon] Official Training Workshop_demo.eml`)

---

## What Next Session Should Do

### Verify (check first, no code changes needed)
1. Check if embeddings rate limit cleared — `curl` the `/v1/embeddings` endpoint
2. Verify `kms_search` returns results via MCP (FTS should work now)
3. Verify `kms_vault_info` shows entity map (needs classify to have completed)
4. Check `knowledge_entries` table populated (classify pipeline output)
5. Run `git diff` on `cloud-native` branch to see uncommitted changes

### Code fixes (prioritized)
1. **`src/pipelines/capture.py:289`** — Add `_best_effort_index` to summarize FAILURE path. Currently only called on SUCCESS path (line 268). Documents captured without summaries get no FTS/embeddings, making them invisible to search.
2. **`src/retrieval/embeddings.py:49`** — Add `max_retries=2` to `openai.OpenAI()` constructor to match async client config.
3. **Optional:** Add `extra_body` config support to `OpenAICompatConfig` + `openai_provider.py` for future Qwen3/thinking-model compatibility.

### Operational
- Test with more file uploads through daemon
- Monitor classify pipeline completion
- Commit config changes on `cloud-native` branch

---

## Available Models on MaaS Platform (tested 2026-06-16)

| Model | Status | Notes |
|-------|--------|-------|
| `google/gemma-4-31b-it` | **WORKING** | Current choice. No thinking mode. Fast. |
| `qwen/qwen3-5-27b` | Works but broken | Thinking tokens consume all max_tokens. Needs `enable_thinking: false`. |
| `deepseek/deepseek-v4-pro` | **Permanently 429** | Shared platform rate limit. Do not use. |
| `deepseek/deepseek-v4-flash` | Untested | |
| `gemini/gemini-2.5-flash` | Untested | |
| `meta-llama/llama-4-maverick` | Untested | |
| `greennode/greennode-embedding-large-1007` | Works (when not rate-limited) | Embedding model. |

---

## Files Modified This Session

- `config/config.yaml` — model + max_retries changes (NOT committed)
- `docs/0_draft/handoff-cloud-deploy-debug-2026-06-16.md` — this file
