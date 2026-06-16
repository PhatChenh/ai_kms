# Handoff — Public Demo Drop-Zone (file upload + size/quota limits)

**Date:** 2026-06-16
**Branch:** `cloud-native` (uncommitted changes pre-existing on branch: `Dockerfile`, `README.md`, `docs/0_draft/ui-astro/HANDOFF.md`, `src/mcp_server/server.py`, untracked `docs/guides/` — unrelated to this thread, do not touch as part of this work unless asked).
**Status:** Design decisions partially locked. No code written yet. Blocked on one user answer.

## Goal

User has a deployed AI-KMS instance (MCP + vault query already working). Wants a **public demo**: visitor lands on a website, drops a file into a drop-zone, system captures→classifies→signals "ready", visitor then connects their own Claude to the MCP server and queries the doc. Needs this exposed safely to anonymous internet visitors with:
1. A way to drop files in (not local machine — needs to hit the deployed cloud instance).
2. Per-file size limit.
3. Total trial-quota limit (shared across all visitors was the explicit choice — see Decisions).

Constraint from user: near deadline, wants minimal/no change to existing codebase if possible.

## What's already true (confirmed by reading code — do not re-derive)

- Storage question is already answered: uploads go to **cloud DB + S3-compatible blob store**, never the local machine. Existing path: `POST /api/upload` → [`upload_handler`](../../src/mcp_server/api.py:135) → [`capture_upload()`](../../src/pipelines/capture.py:146) → DB row + blob → pushed onto live classify queue (`_push_to_classify_queue`, [api.py:39](../../src/mcp_server/api.py:39)). This already gives capture + classify + queueing, for both JSON-text and multipart-binary uploads.
- **No per-file size cap is enforced anywhere on this endpoint today.** `await file_upload.read()` reads the whole multipart body unbounded into memory ([api.py:191](../../src/mcp_server/api.py:191)). `CONFIG.main.handlers.max_file_size_bytes` (50MB, [config.yaml:26](../../src/config/config.yaml:26)) only guards local-disk extractors (PDF/DOCX/etc handlers) — never checked in `api.py`.
- **No quota/counter concept exists anywhere** — no counter table, no per-visitor tracking, nothing.
- **Auth is the wrong shape for a public demo.** The route is gated by one shared `KMS_DAEMON_API_KEY` bearer token (`require_key`, [api.py:76](../../src/mcp_server/api.py:76)) meant for the daemon. That same key also unlocks `POST /api/event` (delete any doc by path) and `GET /api/state` (dump every vault_path + content_hash). Putting this key in browser JS for visitors hands out delete-everything access.
- **No "ready" signal endpoint exists.** `documents.status` column exists and DOES flip after classify completes (written by `classify_writer`/orchestrator, see [`storage/documents_classify.py`](../../src/storage/documents_classify.py)) — but `GET /api/state` ([api.py:330](../../src/mcp_server/api.py:330)) only returns `vault_path` + `content_hash`, not `status`. Nothing today lets a frontend poll "is doc X done classifying yet?"
- `kms_write` / [`_write.py`](../../src/mcp_server/_write.py) is the wrong tool — it's shaped for saving chat-insight text snippets, not raw file drops. Confirmed by reading it; do not repurpose.

## Decisions already locked by the user (do not re-ask)

- **Visitor quota model: shared global cap only.** No per-visitor split. (User explicitly picked this over client-UUID-based per-visitor quota and IP-bucketing.)
- **Quota storage: query the `documents` table directly.** `SELECT SUM(file_size_bytes) FROM documents WHERE vault_path LIKE 'demo/%'` before allowing a new upload. Zero schema change, no new migration, correct across restarts/replicas since DB is the shared source of truth. (User explicitly rejected a new counter table — extra migration this close to deadline — and rejected an in-memory counter — wrong under >1 replica and resets on redeploy.)

## Open — blocked on user, ask this first

**Where should upload + size/quota enforcement logic live?** This was being decided via a 3-way choice when the user dismissed the follow-up question. Re-ask or resume:

> "What's the demo website's hosting setup — fully static with no backend (and you don't want to add one), already has/will have a small backend or serverless function, or not built yet / no preference?"

This determines which of these two paths to take:

- **Option A — Proxy on the demo website** (zero ai_kms code change). The demo site's own backend/serverless function (Vercel/Netlify/Cloudflare Worker, or even a second tiny AgentBase Custom Agent runtime) holds the real `KMS_DAEMON_API_KEY`, enforces per-file size + the global quota check itself, then forwards the multipart POST to the existing `/api/upload` unmodified. Requires the demo site to have *some* execution capability — not pure static HTML/JS with zero willingness to add a function.
- **Option B — New restricted route inside ai_kms.** Add `POST /api/demo-upload` in [`src/mcp_server/api.py`](../../src/mcp_server/api.py) (~60–80 line addition), no `KMS_DAEMON_API_KEY` exposed to the browser (route is either fully public or has its own light-weight key), enforces:
  - per-file size via `Content-Length` header check + a hard cap (new config key, e.g. `demo.max_upload_bytes`)
  - global quota via the `SUM(file_size_bytes) WHERE vault_path LIKE 'demo/%'` query (decision above) — reject with 413/429 before calling `capture_upload`
  - force/sanitize `vault_path` to always land under a `demo/` prefix regardless of client input, for cleanup/visibility
  - still pushes to the classify queue exactly like `upload_handler` does today

If the site truly has zero backend and the user won't add one, Option B is the smaller total change (logic has to live somewhere; ai_kms is the only backend that already exists). Recommend defaulting to **Option B** if the user is undecided or says "no preference" — fewer new moving parts to stand up before deadline.

Either option still needs the **"ready" signal gap closed**: either extend `GET /api/state` to include `status`, or add a small new read endpoint (e.g. `GET /api/document-status?vault_path=...`) so the demo frontend can poll readiness. This is needed regardless of A vs B, since the status data only lives in ai_kms's DB.

## Constraints to respect when implementing (from project CLAUDE.md)

- No hardcoded thresholds — size/quota caps go in `config/config.yaml` + a Pydantic field in `core/config.py`, following the existing `max_file_size_bytes` pattern.
- `mcp_server/tools.py` must stay logic-free (hook-enforced) — this work is in `api.py`/REST routes, not that file, so it's fine, just don't drift logic into `tools.py`.
- Audit trail: any new write path that results in a captured/classified doc should still go through `capture_upload`, which already handles audit logging — don't bypass it.
- Per global CLAUDE.md (`~/.claude/CLAUDE.md`): this is a §2 decision checkpoint (touches auth, is a multi-file-ish architectural choice) — confirm the full plan with the user and get an explicit "go ahead" before writing any code. Do not start editing on resuming this thread without that confirmation.

## Suggested skills for next session

- Resume with `AskUserQuestion` directly (just need the one hosting-setup answer) — don't need a new skill just for that.
- Once Option A/B is picked: `codebase-design-analysis` is arguably already done above; could go straight to `writing-detailed-specs` to turn the chosen option into a buildable spec (file list, exact diff shape, config keys, route signature).
- After spec: `research` (verify assumptions against current `api.py`/`capture.py` — small enough this may be skippable) → `plan-from-specs` → `tdd-implement` for the actual ~60–100 line change, since project convention is TDD per `CLAUDE.md` Result-type/pipeline patterns.
- If the work ships: `update-state` / `update-project-docs` afterward per project convention (STATE.md tracks phase progress; this would likely be logged as a small slice, not a new numbered phase).

## Session notes

- This thread started via the `/agentbase-deploy` command, but the actual content was app-feature design (upload endpoint + quota), not a runtime/registry deploy action — that skill's instructions don't really apply here; treat as a normal design/research task per project CLAUDE.md.
- The session has a "caveman mode" hook active (terse fragment-style prose, full technical substance, code/commits written normally). This is a session/harness hook, not a saved memory — a fresh session will not inherit it unless the user has it configured globally. Don't assume it carries over.
