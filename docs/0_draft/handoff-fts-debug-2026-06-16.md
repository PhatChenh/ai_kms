# Handoff: FTS / BM25 Debug — 2026-06-16

## Status

**Container is up** (runtime-c6507401-2dff-42a3-b295-965940cbf195, endpoint-1cd2a3b9-8d82-461b-abfd-df9b6973721f). MCP server responds, Claude Desktop connected.

**Embeddings API still 429** — rate limit from earlier retry storm hasn't cleared (hours). LLM chat completions (Gemma 4) work fine, it's only `/v1/embeddings` that's throttled.

## What works

- **kms_vault_info** — returns entity map and facts (classify pipeline working, doc_id=1 being retried)
- **kms_search with empty query** (filter-only mode) — finds document in `documents` table, returns cards with title/summary
- **BM25 fallback** in `rank()` — when embeddings fail, falls back to BM25-only ranking and returns `Success` (not `Failure`). Confirmed via log: `rank: embed/KNN failed (BM25-only fallback)`
- **kms_write** — saves chat insights as documents

## What's broken

**kms_search with a query always returns `{"result": []}`.** BM25 finds nothing — `snippet: ""`, `score: 0.0` on all result cards.

## Root cause hypothesis

`index_keywords()` (in `retrieval/keyword.py`) is called from `_best_effort_index()` in `pipelines/capture.py:268` on the SUCCESS path. No `capture.index_keywords_failed` error appears in logs. Yet `notes_fts` FTS5 table has no entries matching any query — not "pineapple" (from `kms_write` test), not "claw" or "training" (from actual email document), not "product launch" (from AI-generated summary).

The most likely cause: **`index_keywords` writes to a different DB file than search reads from**, or the FTS table `notes_fts` wasn't created by migration 007.

## Recent changes (committed, NOT yet deployed)

1. **`src/retrieval/keyword.py`** — added debug logging: prints DB path, vault_path, title, summary/body lengths on write, confirms FTS insert OK
2. **`src/retrieval/ranker.py`** — added debug logging: prints DB path and query on `_bm25_search`, prints result count
3. **`src/retrieval/embeddings.py`** — `max_retries` wired from config to sync `openai.OpenAI()` client
4. **`config/config.yaml` + `src/config/config.yaml`** — `vault.root: /data/vault` (safe fallback), Gemma 4 model, `max_retries: 2`

All committed on `cloud-native` branch. Need to build + push + update runtime to deploy.

## Next steps (in order)

### 1. Deploy the debug logging
Build, push, update runtime to get the `index_keywords` and `_bm25_search` debug logs live.

### 2. Run the test
From Claude Desktop: `kms_write "a unique test word xylophone"` then `kms_search "xylophone"`.

### 3. Check logs
```
bash .claude/skills/agentbase/scripts/runtime.sh logs runtime-c6507401-2dff-42a3-b295-965940cbf195 --from 0 --limit 100 --query "index_keywords"
bash .claude/skills/agentbase/scripts/runtime.sh logs runtime-c6507401-2dff-42a3-b295-965940cbf195 --from 0 --limit 100 --query "_bm25_search"
```

The logs will show:
- What DB path `index_keywords` writes to
- What DB path `_bm25_search` reads from
- What values are being inserted (title, summary length, body length)
- How many results BM25 finds

If DB paths differ → fix the `_db_path` wiring (likely `api._db_path` is None while search resolves from CONFIG differently).
If DB paths match but BM25 finds 0 results → `notes_fts` table doesn't exist or migration 007 failed. Run `sqlite3 <db_path> ".tables"` in the container.
If BM25 finds results but snippet is empty → FTS tokenizer issue.

### 4. Container keeps restarting
`ERROR: ASGI callable returned without completing response.` then `SIGTERM` → shutdown → HuggingFace cross-encoder model download → restart. This happens every few minutes. The cross-encoder model (`ms-marco-MiniLM-L-6-v2`) is downloaded fresh on every startup (no cache). Not urgent but wastes time and bandwidth. Could be fixed by baking the model into the Docker image or setting `HF_HOME` to a persistent volume.

## Key infrastructure

| Item | Value |
|------|-------|
| Runtime ID | `runtime-c6507401-2dff-42a3-b295-965940cbf195` |
| Endpoint ID | `endpoint-1cd2a3b9-8d82-461b-abfd-df9b6973721f` |
| Endpoint URL | `https://endpoint-1cd2a3b9-8d82-461b-abfd-df9b6973721f.agentbase-runtime.aiplatform.vngcloud.vn` |
| Image | `vcr.vngcloud.vn/111480-abp111749/kms-image:latest` |
| Flavor | `runtime-s2-general-2x4` |
| Env file | `.agentbase/runtime.env` |
| DB path (container) | `/data/kb.db` (via `KMS_DB_PATH`) |
| Branch | `cloud-native` |

## Deploy commands

```
docker build --platform linux/amd64 -t vcr.vngcloud.vn/111480-abp111749/kms-image:v20260616180000 -t vcr.vngcloud.vn/111480-abp111749/kms-image:latest .
docker push vcr.vngcloud.vn/111480-abp111749/kms-image:v20260616180000
docker push vcr.vngcloud.vn/111480-abp111749/kms-image:latest
bash .claude/skills/agentbase/scripts/runtime.sh update runtime-c6507401-2dff-42a3-b295-965940cbf195 --image vcr.vngcloud.vn/111480-abp111749/kms-image:latest --from-cr --flavor runtime-s2-general-2x4 --env-file .agentbase/runtime.env
```
