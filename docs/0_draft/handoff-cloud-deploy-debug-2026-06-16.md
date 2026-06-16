# Handoff: Cloud Deployment Debug Session — 2026-06-16

## Session Summary

Debugged why MCP queries (`kms_search`, `kms_vault_info`) returned empty results on the AgentBase cloud deployment despite successful file uploads from the daemon.

## What Was Done

### Bug 1: Prompts not found in container (FIXED, deployed)
- **Root cause:** `src/prompts/` is a data directory (YAML files, no `__init__.py`), not a Python package. `uv sync --no-editable` doesn't install it to site-packages. `prompt_loader.py` resolves `_PROMPTS_DIR` via `Path(__file__).parent.parent / "prompts"` which points to nonexistent path in site-packages.
- **Fix:** Added `COPY src/prompts/ /usr/local/lib/python3.12/site-packages/prompts/` to Dockerfile line 42.
- **Status:** Deployed and verified working in current container.

### Bug 2: Docker layer caching (FIXED, deployed)
- Split Dockerfile builder stage: copy only `pyproject.toml` + `README.md` first, `uv sync` deps, then copy source and re-sync. Source-only changes skip ~2GB dep install.
- **Status:** Deployed. Was briefly suspected of causing container ERROR but AgentBase team confirmed platform-side issue.

### Bug 3: MCP queries return empty (ROOT-CAUSED, partial fix applied)
- **Root cause chain:**
  1. Previous container (pre-prompts-fix) received 5 uploads at ~09:20 UTC
  2. All hit `capture.summarize_failed` (`'capture_summary'` KeyError)
  3. `_best_effort_index()` (FTS + embeddings) only runs on summarize SUCCESS path (`capture.py:268`), skipped on FAILURE path (`capture.py:289`)
  4. Documents stored in DB but with NO summary, NO FTS entries, NO embeddings
  5. Litestream replicated this broken state to S3
  6. Current container restored it; catch_up_scan only re-runs classify, not capture
  7. `kms_vault_info` empty: no `knowledge_entries` (classify never succeeded), no `inbox/%` vault_paths
  8. `kms_search` empty: no FTS/embedding entries exist
- **Fix applied:** Deleted all 5 docs from cloud DB via `/api/event` delete endpoint. Daemon's next sync cycle will re-upload, and capture will run with working prompts this time.
- **Status:** Deletes confirmed (`/api/state` returns empty). **Waiting for daemon to re-sync.**

### Classify pipeline 429 rate limiting (ONGOING)
- Classify orchestrate hits 429 Too Many Requests from `maas-llm-aiplatform-hcm.api.vngcloud.vn` on every AI call
- Docs retry (attempts 1, 2, ...) up to `classify.max_retries` then park
- This is separate from the empty-query bug — even after re-upload, classify will need rate limits to clear before `knowledge_entries` populate

## What Next Session Should Do

### Immediate: Verify re-upload worked
1. Check if daemon has re-synced (look at runtime logs for new upload events after ~10:30 UTC / 17:30 local)
2. Verify `/api/state` shows documents again
3. Check logs for `capture.classify_ready` (means summarize succeeded)
4. Test `kms_search` and `kms_vault_info` via MCP

### Code fix: Add indexing on summarize failure path
In `src/pipelines/capture.py` around line 289 (AI FAILURE branch), `_best_effort_index` should still be called with raw `extracted_text` so search works even when summarization fails. Currently only runs on success path (line 268). This is a resilience improvement.

### Rate limit: Classify pipeline
- Monitor if classify eventually succeeds after rate limits clear
- If rate limits persist, may need to add backoff/delay between classify attempts
- Or reduce concurrent classify calls

## Key Infrastructure Details

| Item | Value |
|------|-------|
| Runtime ID | `runtime-c6507401-2dff-42a3-b295-965940cbf195` |
| Endpoint ID | `endpoint-1cd2a3b9-8d82-461b-abfd-df9b6973721f` |
| Endpoint URL | `https://endpoint-1cd2a3b9-8d82-461b-abfd-df9b6973721f.agentbase-runtime.aiplatform.vngcloud.vn` |
| Image | `vcr.vngcloud.vn/111480-abp111749/kms-image:latest` |
| Flavor | `runtime-s2-general-2x4` |
| Env file | `.agentbase/runtime.env` |
| DB path (container) | `/data/kb.db` (set via `KMS_DB_PATH` env var, matches Litestream) |
| Litestream config | `/etc/litestream.yml` → S3 replica |
| User timezone | UTC+7 (Vietnam) |

## Files Modified This Session

- `Dockerfile` — prompts COPY line + layer caching optimization (both committed on `cloud-native` branch)
- `src/mcp_server/server.py` — staged but NOT committed (check `git diff` for what changed)

## Suggested Skills

- `/agentbase-deploy` — for any further runtime updates, log checks, endpoint management
- `/tdd-implement` — if implementing the `_best_effort_index` fix on failure path
- `/capture_discussion_v2` — if decisions made here need to be preserved as design rationale

## Reference Docs

- `STATE.md` — overall build progress
- `CONSTRAINTS.md` — hard constraints
- `docs/architecture/system_adr/` — ADRs
- `scripts/start.sh` — container startup (Litestream restore → replicate → app)
- `src/mcp_server/cloud_entry.py` — app factory + composed lifespan
- `src/pipelines/capture.py:146-311` — capture_upload text branch (the bug site)
- `src/mcp_server/context.py` — ContextInjectionEngine (what MCP tools query)
