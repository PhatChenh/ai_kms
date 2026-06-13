# P5 Slice 2 — Deployment Foundation (AgentBase)

_Created: 2026-06-13_
_Status: GRILL COMPLETE (2 passes: initial 2026-06-13 D1–D16, build-pipeline 2026-06-13 D17–D25) — ready for design phase_
_Context: Read `docs/0_draft/cloud_native_rearchitecture.md` (architecture) + `docs/0_draft/agentbase_research.md` (platform reference) first._

---

## What this slice does

Make the existing cloud code deployable on AgentBase and give the daemon (Phase 6) something to POST to. No pipeline changes, no config split — just infrastructure.

**Deliverables:**
1. Dockerfile that builds and runs locally
2. New HTTP entry point for the MCP server (existing stdio entry point unchanged)
3. `/health` endpoint (AgentBase hard requirement)
4. Two stub REST endpoints for daemon→cloud communication
5. Litestream + VNG Object Storage for SQLite persistence

---

## Decisions locked during grill (2026-06-13)

### D1 — Local Docker only, no AgentBase deployment
Actual AgentBase deployment (IAM account, container registry push, gateway config) is a manual ops task, not a code deliverable. This slice ships a Dockerfile that builds and runs locally with `docker run -p 8080:8080`. Verify `/health` and REST endpoints via `curl`.

### D2 — Separate container per tester (not multi-tenant)
3-4 testers, each gets their own AgentBase runtime. One container = one SQLite DB = one Litestream backup. No tenant isolation code. Same Docker image, different env-files per tester.

### D3 — `--max-replicas 1` per runtime
SQLite is single-writer. One replica per runtime avoids write conflicts. Revisit only if query load requires horizontal scaling (→ PostgreSQL migration).

### D4 — Simple API key auth for daemon→cloud
NOT IAM (that's for AgentBase platform APIs). Container reads `KMS_DAEMON_API_KEY` from env var. Daemon sends `Authorization: Bearer <api-key>` on every request. One key per runtime instance. Sufficient for trusted testers.

**Future note for Phase 6 DAEMON INSTALLER:** Init script must guide user through: generate API key → set on container env → paste into daemon config. Already noted in roadmap.

### D5 — Same process for MCP + REST endpoints
FastMCP is built on Starlette/uvicorn. Add REST routes (`/api/upload`, `/api/event`, `/health`) to the same ASGI app. No process supervisor needed. One uvicorn process on port 8080.

### D6 — New entry point, keep existing stdio
Create `mcp_server/cloud_entry.py` (or similar). Imports existing FastMCP app, mounts REST routes, runs uvicorn on port 8080 with HTTP transport. `mcp_server/server.py` continues working in stdio mode for local Claude Desktop dev.

### D7 — `/api/upload` is pure storage (stub)
Accepts extracted text + metadata → INSERT into `documents` table with `full_body`. No summarization, no search indexing, no audit. Phase 7 (Capture Refactor) adds the real pipeline. Keep the stub dumb.

### D8 — `/api/upload` accepts open-ended `metadata` field
Payload includes a `metadata` JSON field for filesystem timestamps + format-specific metadata (PDF author, DOCX properties, etc.). Phase 6 decides what the daemon extracts. Phase 7 decides what capture does with it. Slice 2 just makes the pipe wide enough.

### D9 — `/api/event` delete = hard delete
No soft delete. Clean slate, no production users. `DELETE FROM documents WHERE vault_path = ?` + cleanup of search entries (embeddings_vec, notes_fts). Soft delete adds complexity for zero benefit at this scale.

### D10 — Upload vs delete race: eventual consistency
Upload arrives after delete event → just store it (next delete event cleans up). Delete arrives after upload → removes row. Both orderings produce correct final state. Startup scanner (Phase 6) reconciles any drift.

### D11 — Litestream credentials via env vars
`LITESTREAM_ACCESS_KEY_ID`, `LITESTREAM_SECRET_ACCESS_KEY`, `LITESTREAM_BUCKET`, `LITESTREAM_ENDPOINT`. Same image serves all testers — only the env-file differs. No secrets baked into image.

### D12 — First deployment: create fresh DB if no backup
Startup script: try restore from Object Storage → if no backup → run `init_db()` (applies all migrations, creates empty tables) → start Litestream replication → start app. No manual DB seeding required.

### D13 — DB path: `/data/kb.db`
Dedicated `/data/` directory. Not `/tmp/` (unpredictable clearing). Not `/app/` (keep code and data separate). Litestream watches `/data/kb.db`.

### D14 — Dummy vault path workaround (TD-059)
Current `VaultConfig` requires `vault.root` to exist on disk. Container has no vault. Set `VAULT_ROOT=/data/vault` env var + `mkdir -p /data/vault` in Dockerfile. No vault code runs cloud-side. Dies when config split happens (Phases 6/7/9). Tracked as TD-059.

**AMENDED during design (user sign-off 2026-06-13 — see design doc OQ-1):** the `VAULT_ROOT` env var has **no binding in `core/config.py`** today (only `KMS_DB_PATH` is overridable). The original "env var + config.py UNMODIFIED" combo is impossible. Resolution: ADD a `VAULT_ROOT` env override to `core/config.py` + a unit test. `core/config.py` is therefore now a MODIFIED file this slice (supersedes the "Not modified" note below). Chosen over a cloud `config.yaml` to keep one identical image per tester. Still throwaway — removed at config-split (TD-059). **Mechanism (research correction):** NOT a `KMS_DB_PATH` mirror — the value is injected into the raw config dict before `MainConfig` is constructed (vault-root validates at construction; `VaultConfig` has no `validate_assignment`). See spec C2-5.

### D15 — DB backend flexibility
SQLite + Litestream for MVP/demo. PostgreSQL support planned for later. Storage layer (`storage/db.py`, `get_connection()`) stays the single DB access point. When adding PostgreSQL: connection factory + migration runner are the two touch points.

### D16 — Startup-only reconcile
No periodic reconcile while daemon runs. Watcher catches real-time events. Phase 6 STARTUP SCANNER diffs vault against cloud DB on daemon start. Add periodic reconcile only if testers report drift.

### D17 — One server, one port (build-pipeline grill, 2026-06-13)
MCP + REST + future web UI all share one Starlette/ASGI server on port 8080. No separate internal ports. Different URL paths, same server. AgentBase expects one container → one port.

### D18 — MCP auth delegated to AgentBase Gateway (build-pipeline grill, 2026-06-13)
API key auth on `/api/*` routes only. `/health` is open. MCP endpoint has no container-level auth — AgentBase Resource Gateway handles MCP auth (IAM/JWT) at the platform level.

### D19 — New `upsert_from_upload()` function (build-pipeline grill, 2026-06-13)
Upload endpoint calls a new `upsert_from_upload()` in `storage/documents.py`. Not raw SQL in the handler, not touching the old `upsert()` (that's Phase 7). Clean separation.

### D20 — Reuse existing storage functions for events (build-pipeline grill, 2026-06-13)
`/api/event` delete reuses `delete_by_path()`, move reuses `rename()`. Both already handle multi-table cleanup atomically and are tested.

### D21 — `moved` event covers moves and renames (build-pipeline grill, 2026-06-13)
No separate `renamed` event type. A rename is a move where only the filename changes. One event type, simpler contract.

### D22 — Folder events decomposed by daemon (build-pipeline grill, 2026-06-13)
Folder-level move/delete broken into per-file events by the daemon. Cloud endpoint only handles individual files. Keeps the API contract simple.

### D23 — Content hash is the idempotency key (build-pipeline grill, 2026-06-13)
Same vault_path + same content_hash = skip. Same vault_path + different hash = update. No request-level dedup (request ID) needed. Network retries with identical payloads naturally dedup.

### D24 — Shutdown sequence: app first, then Litestream (build-pipeline grill, 2026-06-13)
SIGTERM → gracefully stop uvicorn (finish in-flight requests, no new ones) → flush Litestream (final WAL backup) → exit. Prevents race where app writes during Litestream flush.

### D25 — Crash data loss acceptable (build-pipeline grill, 2026-06-13)
Litestream replicates every ~1 second. Crash may lose up to ~1 second of writes. Acceptable for 3-4 testers. Daemon startup scanner re-uploads anything missing.

---

## What to build

### 1. Dockerfile

```
Platform: linux/amd64
Base: python:3.12-slim
Installs: src/ dependencies + Litestream binary
Creates: /data/ directory, /data/vault/ (dummy, TD-059)
Exposes: port 8080
Entry: startup script (see below)
```

### 2. Startup script (`scripts/start.sh` or similar)

Sequence:
1. If Litestream env vars set → try restore `/data/kb.db` from Object Storage
2. If restore fails (no backup) or env vars not set → skip (fresh DB created by app)
3. If Litestream env vars set → start Litestream replication in background
4. Start the app: `python -m mcp_server.cloud_entry`
5. On shutdown (SIGTERM): flush Litestream before exit

### 3. Cloud entry point (`mcp_server/cloud_entry.py`)

- Import existing FastMCP `mcp` app from `mcp_server/server.py`
- Mount additional routes on the underlying Starlette app:
  - `GET /health` → 200 `{"status": "ok"}`
  - `POST /api/upload` → stub upload handler
  - `POST /api/event` → stub event handler
- Auth middleware: check `Authorization: Bearer <key>` against `KMS_DAEMON_API_KEY` env var on `/api/*` routes. `/health` is unauthenticated.
- Run uvicorn on `0.0.0.0:8080`
- Initialize DB on startup: `init_db()` (creates tables if missing, runs migrations)

### 4. `POST /api/upload`

**Request:**
```json
{
  "vault_path": "Projects/Alpha/meeting.md",
  "extracted_text": "Full text content...",
  "content_hash": "sha256:abc123...",
  "original_filename": "meeting.md",
  "file_size_bytes": 4096,
  "metadata": {
    "created_time": "2026-06-13T10:00:00Z",
    "modified_time": "2026-06-13T14:30:00Z",
    "pdf_author": "Anthony",
    "pdf_title": "Q2 Report"
  }
}
```

**Response:**
```json
{"status": "ok", "document_id": 42}
```

**Behavior (stub):**
- Check content_hash against existing row for same vault_path → skip if match (idempotent)
- If new vault_path → INSERT into documents with `full_body`, `original_filename`, `file_size_bytes`, `content_hash`
- If vault_path exists but hash differs → UPDATE row (content changed)
- `metadata` field stored as-is (JSON column or ignored in stub — Phase 7 decides usage)
- Returns document ID

### 5. `POST /api/event`

**Request:**
```json
{
  "type": "moved",
  "old_path": "Projects/Alpha/note.md",
  "new_path": "Projects/Beta/note.md"
}
```
or
```json
{
  "type": "deleted",
  "path": "Projects/Alpha/note.md"
}
```

**Response:**
```json
{"status": "ok"}
```

**Behavior (stub):**
- `moved` / `renamed`: UPDATE `vault_path` in documents table. Also update `vault_path` in `embeddings_vec` and `notes_fts` if rows exist.
- `deleted`: DELETE from documents + embeddings_vec + notes_fts (hard delete, within same transaction).
- Unknown path → return `{"status": "not_found"}` (not an error — file may not have been captured yet)

### 6. Litestream config (`litestream.yml` template)

```yaml
dbs:
  - path: /data/kb.db
    replicas:
      - type: s3
        bucket: ${LITESTREAM_BUCKET}
        endpoint: ${LITESTREAM_ENDPOINT}
        access-key-id: ${LITESTREAM_ACCESS_KEY_ID}
        secret-access-key: ${LITESTREAM_SECRET_ACCESS_KEY}
```

---

## Out of scope

- Actual AgentBase deployment (IAM, container registry, gateway, runtime create)
- Config split (vault root removal from cloud config)
- Summarization, search indexing, or audit in `/api/upload` (Phase 7)
- Periodic reconcile (startup-only per D16)
- Multi-tenancy (separate runtimes per tester per D2)
- Daemon code (Phase 6)
- `/pending-commands` or `/command-ack` endpoints (cloud→daemon commands are dead per rearchitecture doc §6)

---

## Acceptance criteria

- [ ] `docker build --platform linux/amd64` succeeds
- [ ] `docker run -p 8080:8080` starts container, `/health` returns 200
- [ ] MCP server responds to tool-list request on port 8080
- [ ] `curl POST /api/upload` with test payload → document appears in DB with `full_body`
- [ ] `curl POST /api/upload` same payload again → idempotent (skipped)
- [ ] `curl POST /api/upload` same path, different hash → updated
- [ ] `curl POST /api/event` with move → `vault_path` updated in DB
- [ ] `curl POST /api/event` with delete → row removed from DB + search tables
- [ ] Unauthorized request (wrong/missing API key) → 401
- [ ] Container restart with Litestream configured → DB restored from Object Storage
- [ ] Container first start (no backup) → fresh DB created with correct schema
- [ ] Existing stdio MCP entry point still works (`python -m mcp_server.server`)

---

## Dependencies

- **Slice 1 migration (COMPLETE):** `knowledge_entries` table + `full_body`/`original_filename`/`file_size_bytes` columns on `documents`. Already merged to cloud-native branch.
- **No other dependencies.** This slice touches no existing pipeline code.

---

## Files expected to be created/modified

**New files:**
- `Dockerfile`
- `scripts/start.sh` (or similar startup script)
- `litestream.yml` (template, env-var substituted at runtime)
- `mcp_server/cloud_entry.py` (HTTP entry point)
- `mcp_server/api.py` (REST route handlers — or inline in cloud_entry.py, design decides)

**Modified files:**
- `mcp_server/server.py` — may need to export the FastMCP app object so `cloud_entry.py` can import it
- `pyproject.toml` — add uvicorn dependency if not already present

**Not modified:**
- ~~`core/config.py` — untouched~~ **SUPERSEDED (see amended D14):** `core/config.py` IS modified this slice — adds a throwaway `VAULT_ROOT` env binding (mirrors `KMS_DB_PATH`) + unit test. Removed at config-split (TD-059).
- `storage/documents.py` — `upsert()` unchanged (stub uses direct INSERT, not the existing `upsert()` which takes `WriteOutcome`)
- `pipelines/*` — no pipeline changes
