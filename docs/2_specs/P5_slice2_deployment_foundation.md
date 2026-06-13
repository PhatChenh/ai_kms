# P5 Slice 2 — Deployment Foundation (AgentBase) — Spec

_Created: 2026-06-13_
_Spec for: Option A (REST handlers + auth gate in a dedicated module, mounted onto the existing MCP web app, one port)._
_Upstream design: `docs/1_design/P5_slice2_deployment_foundation.md` (Option A chosen, OQ-1 + OQ-3 resolved, OQ-2 parked)._
_Locked requirements: `docs/0_draft/P5_slice2_deployment_foundation.md` (D1–D25; D14 + "Not modified" list AMENDED at design — see C-CONFIG below)._
_Behavior-inventory IDs: `P5-DEPLOY-01` … `P5-DEPLOY-12` (`docs/system_behavior/behavior_inventory.yaml`). Do not add new IDs._
_Tier: MEDIUM — purely additive infrastructure. No pipeline changes, no schema changes, no config split._

---

## Purpose

This phase lets the knowledge system we already built **run as a container in the cloud** (on VNG's AgentBase) and gives the future laptop daemon (Phase 6) **a cloud address to send files to**. After this phase, the same system that runs locally for Claude Desktop can also boot inside a container, serve one web port, accept uploaded files and file-move/delete notices over HTTP behind a shared secret key, answer the existing assistant (MCP) interface on that same port, and keep its database alive across restarts by streaming it to cloud storage.

Nothing about how the system thinks, summarizes, or classifies changes. The two new web endpoints are deliberately "dumb" — they store records and reuse cleanup logic we already wrote; they do not summarize, classify, index for search, or write to the audit log. The existing laptop (stdio) way of starting the system is untouched; this phase adds a second way to start the same system, not a replacement.

---

## Already built (reuse, do not rebuild)

Every item below was verified against the source on 2026-06-13. Build steps reference these by ID; do not re-implement them.

| Function / Module | Location | What it does | How this spec uses it | Depth |
|-------------------|----------|--------------|----------------------|-------|
| `mcp` (FastMCP object) | `mcp_server/server.py:123` | The assistant interface application, constructed at module scope with a lifespan, five tools already registered | `cloud_entry.py` imports it and asks it for its web app | deep |
| `register_tools(mcp)` | `mcp_server/server.py:128` (defn in `tools.py`) | Registers the five existing KMS tools onto `mcp` at import time | Happens automatically when `server.py` is imported — no call needed in cloud entry | deep |
| `main()` (stdio entry) | `mcp_server/server.py:167` | Runs `mcp.run()` over stdio for local Claude Desktop | Left byte-for-byte unchanged (D6); proves P5-DEPLOY-12 | deep |
| `_lifespan(app)` | `mcp_server/server.py:101` | Builds the Context Injection Engine the MCP tools depend on | Must still run when uvicorn serves the app — the #1 research risk (R1) | deep |
| `FastMCP.streamable_http_app()` | installed `mcp` package | Returns the framework's Starlette web app for the MCP protocol | Source of the web app `cloud_entry.py` mounts routes onto | shallow (interface trusted; internals verified by research R1) |
| `FastMCP.custom_route(path, methods)` | installed `mcp` package | Adds an extra route to the app, explicitly auth-bypassing | Used only for the open `/health` route | shallow |
| `upsert(outcome, db_path, batch_id)` | `storage/documents.py:100` | Inserts/replaces a documents row from a `WriteOutcome` | NOT used (takes a `WriteOutcome` that does not exist in the upload path); left unchanged (D19) | deep |
| `get_by_path(vault_path, db_path)` | `storage/documents.py:156` | Returns `Result[DocumentRow \| None]` for one path | Pattern the new save-or-update routine follows to read the existing row | deep |
| `delete_by_path(vault_path, db_path)` | `storage/documents.py:213` | Hard-deletes the row + its keyword-index + meaning-index rows in one transaction; returns `Result[int]` (rowcount) | Backs the `deleted` event; rowcount 0 → `not_found` (D20) | deep |
| `rename(old, new, db_path)` | `storage/documents.py:316` | Updates `vault_path` and carries the keyword + meaning index rows old→new in one transaction; returns `Result[int]` (rowcount) | Backs the `moved` event; rowcount 0 → `not_found` (D20) | deep |
| `get_connection(db_path=None, *, readonly=False)` | `storage/db.py:76` | Context manager that opens a connection with `PRAGMA foreign_keys=ON` set (via `_connect`), commits/rolls back | The save-or-update routine MUST use this, never raw `sqlite3.connect` (C-04) | deep |
| `init_db(db_path=None)` | `storage/db.py:49` | Runs `schema.sql` then migrations 001–008 in order; idempotent (CREATE TABLE IF NOT EXISTS + version-gated migrations) | Called on startup; safe no-op after a restore (R3) | deep |
| migration 008 | `storage/migrations/008_knowledge_entries_and_document_columns.sql` | Already added `full_body`, `original_filename`, `file_size_bytes` columns to `documents` | The upload routine writes these existing columns — NO new migration (C-05) | n/a |
| `ApiKeys.kms_db_path` | `core/config.py:522` | `Field(default=None, alias="KMS_DB_PATH")` — env override for the DB path | Exact pattern the new `VAULT_ROOT` binding mirrors | deep |
| `load_config()` DB-path application | `core/config.py:596-597` | `if keys.kms_db_path is not None: cfg.main.database.path = keys.kms_db_path` | Exact pattern the new `VAULT_ROOT` application mirrors | deep |
| `VaultConfig.root` | `core/config.py:84` | Required `Path`; no env override exists today | The thing `VAULT_ROOT` must be able to set so the container's startup validation passes | deep |
| WAL pragmas | `storage/db.py:18-19` | `journal_mode=WAL`, `wal_autocheckpoint=100` on every connection | Litestream relies on WAL — confirm no checkpoint conflict (R5) | n/a |

**Partially built (extend):**

- `storage/documents.py` — a deep module with an `upsert`/`get_by_path`/`delete_by_path`/`rename` family. We add ONE sibling function (`upsert_from_upload`) consistent with that family. We do not touch the existing functions.
- `core/config.py` — has an env-override mechanism for the DB path only. We add a parallel `VAULT_ROOT` override (throwaway, TD-059).
- `mcp_server/server.py` — already exposes `mcp` at module scope; likely zero change (a comment confirming `mcp` is a public export is the most that is needed).
- `pyproject.toml` — `uvicorn` and `starlette` arrive transitively via `mcp`. We add `uvicorn` explicitly because `cloud_entry.py` imports it directly (R6), and add a container entry point.

**Not built (create):** `Dockerfile`, `scripts/start.sh`, `litestream.yml`, `mcp_server/cloud_entry.py`, `mcp_server/api.py`, `storage/documents.py::upsert_from_upload`, the `core/config.py` `VAULT_ROOT` binding.

---

## Q1 Diagram (from design) — what happens inside the running container

```
# Cloud Deployment Foundation — What Happens Inside
Scope: Shows what happens inside the running cloud container — startup,
       the four request paths it serves on one port, and shutdown.
       Does NOT cover daemon-side extraction or the actual AgentBase deploy.

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

        Container starts
               │
               ▼
   ┌──────────────────────────┐
   │ Startup: restore latest   │
   │ backup if one exists,     │
   │ start background backup   │
   │ streamer, ensure database │
   │ exists and is up to date  │
   └────────────┬─────────────┘
                │
                ▼
   ┌──────────────────────────┐
   │ One web server listens on │
   │ one port, routes by path  │
   └────────────┬─────────────┘
                │
     ┌──────────┼───────────┬─────────────┐
     ▼          ▼           ▼             ▼
┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐
│ Health  │ │Assistant│ │ Upload   │ │ Events   │
│ check — │ │interface│ │ path     │ │ path     │
│ open,   │ │untouched│ │(needs    │ │(needs    │
│ "alive" │ │as before│ │ secret   │ │ secret   │
└─────────┘ └─────────┘ │ key)     │ │ key)     │
                        └────┬─────┘ └────┬─────┘
                             │            │
                             ▼            ▼
                    ┌─────────────┐ ┌──────────────┐
                    │ Save-or-    │ │ Move updates  │
                    │ update: new │ │ location;     │
                    │ / skip same │ │ delete removes│
                    │ / overwrite │ │ record; miss  │
                    │ changed     │ │ → "not found" │
                    └──────┬──────┘ └──────┬───────┘
                           │               │
                           └───────┬───────┘
                                   ▼
                        ┌────────────────────┐
                        │ One shared database │
                        │ (mirrored to cloud  │
                        │ backup ~every sec)  │
                        └────────────────────┘

Shutdown: stop taking new requests → finish in-flight ones →
          push one final backup → exit.

Simplified: The 3-step startup sequence (restore / start streamer /
            ensure schema) is grouped into one box. The upload secret-key
            gate and the events secret-key gate are the same shared
            gatekeeper, shown inline on each path rather than as a
            separate box.
```

---

## Q2 Diagram — how it connects to others

```
# Cloud Deployment Foundation — How It Connects
Scope: Shows how the running container connects to its outside neighbors —
       who sends it work and where its data is backed up.
       Does NOT show the internal request steps (see Q1 for that).

How to read this:
  Center box     = the container we are building (one web server, one port)
  Solid boxes    = neighbors / pieces that exist now
  Dashed boxes   = planned, not built yet (built in Phase 6)
  Arrow labels   = what passes between them


  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ┐                        ┌──────────────────┐
    Laptop Daemon                             │ Chat client      │
  │ (future caller)  │                        │ Talks the        │
    Watches laptop                            │ assistant        │
  │ files, POSTs them│                        │ protocol         │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ┘                        └────────┬─────────┘
         │     │                                       │
         │     │ file moves /                          │ asks for tools,
  uploads│     │ deletes                               │ search, reads
  a file │     │ (with secret key)                     │ (platform handles
  (with  │     │                                        │  its own auth)
  secret │     │                                        │
   key)  ▼     ▼                                        ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │                  CLOUD ENTRY POINT (the container)                 │
  │                  One web server, one port (8080)                   │
  │                                                                    │
  │   ┌─────────────┐   ┌─────────────┐   ┌──────────────────────┐    │
  │   │ Secret-key  │   │ Health check│   │ MCP server           │    │
  │   │ gate        │   │ (open, no   │   │ (assistant interface,│    │
  │   │ guards      │   │  key)       │   │  untouched)          │    │
  │   │ /api/* only │   └─────────────┘   └──────────────────────┘    │
  │   └──────┬──────┘                                                  │
  │          │ passes valid                                           │
  │          ▼ requests to                                            │
  │   ┌─────────────────┐        ┌─────────────────┐                  │
  │   │ Sync Endpoints  │──────► │ Save-or-update  │                  │
  │   │ Upload + Events │ store/ │ routine +       │                  │
  │   │                 │ move/  │ Document Store  │                  │
  │   └─────────────────┘ delete └────────┬────────┘                  │
  │                                        │ reads / writes           │
  │                                        ▼                          │
  │                              ┌──────────────────┐                 │
  │                              │ One shared        │                 │
  │                              │ database (SQLite) │                 │
  │                              └────────┬─────────┘                 │
  └────────────────────────────────────── │ ──────────────────────────┘
                                           │ mirrored / restored by
                                           ▼
                                  ┌──────────────────┐
                                  │ Litestream       │
                                  │ Streams DB out,  │
                                  │ restores on boot │
                                  └────────┬─────────┘
                                           │ backup files
                                           │ (~every second)
                                           ▼
                                  ┌──────────────────┐
                                  │ Object Storage   │
                                  │ Cloud file store │
                                  │ (S3-compatible)  │
                                  └──────────────────┘

Simplified: The Startup Script is not shown as a box — it is the wrapper that
            launches the Cloud Entry Point and drives Litestream restore-on-boot
            and final-flush-on-shutdown. Litestream and the database live inside
            the container; Object Storage is the only truly external data target.
            The three outside callers (Laptop Daemon, Chat client, Object Storage)
            are the "others" this slice connects to.
```

**Diagram consistency note:** Q2 reuses Q1's names verbatim — "Health check (open)", "Assistant interface / MCP server (untouched)", "Upload path / Events path (need secret key)", "Save-or-update", and "One shared database (mirrored to cloud backup ~every sec)". Q1 shows the inside-the-request flow on one port; Q2 zooms out to the three outside neighbors (Laptop Daemon caller, Chat client, Object Storage) and the Litestream backup path.

---

## Feature overview

Plain-English walk-through, happy path first.

**Startup.** When the container boots, a small startup script runs before anything serves traffic. If cloud-backup settings are present, it first asks Litestream to restore the latest database backup onto the local disk; if there is no backup (first-ever boot) it simply skips that and lets the app build a fresh database. It then starts Litestream in the background so that, from now on, every database change streams up to cloud storage roughly once a second. Finally it launches the cloud entry point. The cloud entry point ensures the database exists and is fully up to date (running the standard "create-if-missing + apply migrations" routine, which is safe to run whether the database was just restored or is brand new), then starts one web server on one port.

**Serving on one port.** The single web server routes by URL path. The existing assistant (MCP) interface answers on its own path exactly as it does today — the chat platform handles its own authentication, so the container does not gate that path. A `/health` path answers "alive" to anyone, with no key, because the cloud platform pings it to know the container is ready. The two new sync paths — upload and events — are the only ones guarded by the shared secret key.

**Upload (happy path).** The future daemon sends one file's extracted text plus details (path, content fingerprint, original filename, size, and an open-ended metadata blob) with the secret key attached. The upload handler hands those fields to a new save-or-update routine in the data layer. That routine looks up whether a record already exists for the same path: if there is no record, it inserts a new one and returns the new record id; the response says "ok" with that id.

**Upload (idempotency edge cases).** If a record already exists for that path and the content fingerprint matches, the routine changes nothing and returns the existing id — re-sending the identical file is a safe no-op. If a record exists but the fingerprint differs, the routine updates that one record in place (new text, new fingerprint) — there is never a second row for the same path. The open-ended metadata blob is accepted but not stored this phase (no column for it yet); a future phase decides what to do with it. This is correct for the stub and must be noted in the contract so a daemon author does not mistake the "vanishing" metadata for a bug.

**Events (happy path + edges).** The daemon reports file changes on the events path. A "moved" event (which also covers renames — a rename is just a move where only the filename changes) updates the record's location and carries its search-index entries along, by reusing the existing rename routine. A "deleted" event removes the record and all its search-index entries in one transaction, by reusing the existing delete routine. If either event names a path that was never captured, the response is "not found" — and this is explicitly NOT an error (the file may simply not have been captured yet), so the response is still a normal 200.

**Auth edge case.** Any request to a sync path with a missing or wrong secret key is rejected as unauthorized (401) and changes nothing in the database. The health path stays reachable with no key, and the assistant path is never touched by this gate.

**Shutdown.** When the platform sends a stop signal, the startup script catches it, tells the web server to stop taking new requests and finish the ones in flight, then runs one final Litestream flush so the last writes reach cloud storage, then exits. The app itself stays unaware of Litestream — the script orchestrates the order.

---

## Out of scope

- **Actual AgentBase deployment** (IAM account, container-registry push, gateway config, runtime create) — manual ops task, not a code deliverable (D1). This slice ships a Dockerfile that builds and runs locally and is verified with `curl`.
- **Summarization, classification, search indexing, or audit inside `/api/upload`** — the upload is pure storage (D7). Handled by Phase 7 (Capture Refactor).
- **Persisting the open-ended `metadata` field** — accepted and ignored this slice (D8); no new column. A future phase (likely Phase 7) decides usage. Adding a column now would be a schema change for data nothing reads.
- **The laptop daemon itself** — Phase 6. This slice only builds the cloud side it will call.
- **The daemon's own platform authentication (IAM)** — parked as OQ-2 for Phase 6. The secret-key gate here is the daemon→`/api/*` shared key (D4/D18), NOT the daemon's platform auth.
- **Cloud→daemon command endpoints** (`/pending-commands`, `/command-ack`) — dead per the rearchitecture doc §6.
- **Config split** (removing the vault root from cloud config) — Phases 6/7/9. The `VAULT_ROOT` binding added here is a throwaway bridge (TD-059).
- **Periodic reconcile / drift detection** — startup-only per D16; the Phase 6 startup scanner reconciles drift. Not built here.
- **Multi-tenancy / tenant isolation** — separate runtime per tester (D2/D3); no isolation code.
- **PostgreSQL backend** — SQLite + Litestream for MVP (D15); Postgres is a later phase.

---

## Constraints

Each is a hard stop carried from the design's Guardrail Checklist (`/guardrail-check Review`) and `CONSTRAINTS.md`. Build steps must not introduce a violation.

- **C-04 · `PRAGMA foreign_keys=ON` on every connection** — `upsert_from_upload()` MUST acquire its connection via `storage/db.py::get_connection()` (which calls `_connect()`, which sets the pragma). Never open a raw `sqlite3.connect()`. Source: DECISION-008 / `CONSTRAINTS.md` C-04.
- **C-05 · All schema changes via versioned `.sql` deltas** — Slice 2 adds NO column or table. `full_body`, `original_filename`, `file_size_bytes` already exist (migration 008). `upsert_from_upload()` is INSERT/UPDATE only — no `ALTER TABLE`, no `CREATE TABLE` in `.py`. The accepted-and-ignored `metadata` field must NOT trigger a new migration. Source: DECISION-007 / C-05.
- **C-12 · Public functions return `Success`/`Failure`** — `upsert_from_upload()` returns `Result[int]` to match the storage family (`upsert`/`delete_by_path`/`rename` all return `Result`). The web handlers translate that Result into an HTTP response. Source: C-12.
- **C-13 · Audit log non-negotiable from Phase 1 (triggers on AI decisions only)** — the stubs make NO AI decision; no `provider.complete()` is added, so no audit write is required. Confirm no LLM call sneaks into the handlers or the save-or-update routine. Source: DECISION-003/004 / C-13.
- **C-14 · `mcp_server/tools.py` is logic-free** — the REST handlers, the auth gate, and any branching MUST NOT be added to `tools.py`. They live in `mcp_server/api.py`. `tools.py` stays byte-for-byte unchanged. Source: C-14 / hook hard-block.
- **C-15 · Never add an MCP tool before its pipeline exists** — the two sync endpoints are plain web routes, NOT MCP tools; they do not register via `mcp.tool()`. C-15 is satisfied by construction. Source: C-15.
- **C-10 · async wrapped with `asyncio.run()` in CLI only** — the cloud entry point is NOT a Click command; uvicorn owns the event loop. Do NOT nest `asyncio.run()` inside the running loop. Source: DECISION-010 / C-10.
- **C-11 · `load_dotenv` called exactly once** — `mcp_server/server.py` already calls `load_dotenv` at module top (`server.py:27`). `cloud_entry.py` imports `server.py`, so the call already happens. `cloud_entry.py` MUST NOT call `load_dotenv` again. Source: DECISION-014 / C-11.
- **C-CONFIG (amended D14) · `core/config.py` IS modified this slice** — the design (OQ-1) supersedes the draft's "Not modified: core/config.py". The `VAULT_ROOT` binding is the only change, mirrors `KMS_DB_PATH`, and is throwaway (TD-059). It must not introduce any other behavior change to config loading.

---

## Assumptions

Each is a falsifiable claim about existing code or libraries that research must verify before planning. The "Risks" IDs (R1, R3, R5, R6, R7) from the design map onto these.

| ID | Assumption | Source implication | What would prove it wrong |
|----|-----------|-------------------|--------------------------|
| A1 (**R1 — top priority**) — **VALIDATED by research** | Running uvicorn directly on `mcp.streamable_http_app()` still enters FastMCP's `_lifespan` (`server.py:101`), so the Context Injection Engine the MCP tools rely on initializes in cloud mode. **Research nuance: the lifespan fires per MCP session (on the first `/mcp` request), NOT at uvicorn boot — so P5-DEPLOY-02 verification must issue a real tool-list/session request, not just `curl /health`.** | design Implications "existing chat-assistant interface keeps working" + Risk R1 | A self-hosted uvicorn run of `streamable_http_app()` serves `/health` and `/api/*` but the MCP tool-list path errors or returns no engine (P5-DEPLOY-02 fails). |
| A2 (R1 cont.) | Appending `Route(...)` objects to the Starlette app's `.routes` list (returned by `streamable_http_app()`) before `uvicorn.run` is a supported way to add routes, OR `mcp.custom_route` is the supported path for at least `/health`. | design Option A "Approach" | Appending to `.routes` has no effect, or raises, or the MCP route stops resolving after routes are appended; sub-mounting is the only working form. |
| A3 (R3) | `init_db()` (`db.py:49`) called on a restored, already-populated DB is a safe no-op (CREATE TABLE IF NOT EXISTS + version-gated migrations), so startup ordering restore → `init_db()` → serve is harmless. | design Implications "first-ever boot builds … restart restores" + Risk R3 | Running `init_db()` on a populated DB drops/recreates data, re-runs a migration, or errors on existing tables (P5-DEPLOY-10/-11 fail). |
| A4 (R5) | The DB's WAL mode + `wal_autocheckpoint=100` (`db.py:18-19`) does not conflict with Litestream's own checkpointing (Litestream relies on WAL). | design Risk R5 | Litestream documents a required checkpoint posture incompatible with `wal_autocheckpoint=100`, causing backup corruption or missed WAL frames. |
| A5 (R6) | `uvicorn` and `starlette` are available transitively today via `mcp>=1.27,<2`; `cloud_entry.py` can `import uvicorn` and `import starlette` directly, but listing `uvicorn` explicitly in `pyproject.toml` removes a latent break. | design Implications "one container = one server" + Risk R6 | `import uvicorn` fails in the built image, or a pinned `mcp` version ships without uvicorn/starlette. |
| A6 (R7) | A path-prefix auth gate can cover exactly `/api/*` without shadowing `/health` or the MCP protocol path. | design Risk R7 + D18 | The middleware or per-route check either lets an unauthenticated `/api/*` request through, or accidentally gates `/health` or the MCP path. |
| A7 | `mcp` is importable at module scope from `mcp_server/server.py` (`server.py:123`) with its five tools already registered, and importing `server.py` does not start the stdio server (it only runs on `main()` / `mcp.run()`). | design Implications "second way to start" | Importing `server.py` blocks, starts serving, or fails because tool registration has side effects that require a running loop. |
| A8 | Migration 008 columns `full_body`, `original_filename`, `file_size_bytes` exist on `documents` after `init_db`, so `upsert_from_upload` writes existing columns with no DDL. | design C-05 note ("[VERIFIED]") | A fresh `init_db` produces a `documents` table without one of those three columns. |
| A9 **(CORRECTED post-research — see C2-5)** | `VaultConfig.root` is required with no env override today. The override **cannot** mirror `KMS_DB_PATH`'s post-construction-assignment approach: the vault-root "must exist on disk" check is a `MainConfig` model_validator (`config.py:372-382`) that fires **at construction** against the YAML value (before any post-construction override), and `VaultConfig` has no `validate_assignment`. Correct mechanism: inject `VAULT_ROOT` into the **raw config dict before `MainConfig(**raw_main)` is constructed**, inside `load_config()`. | design OQ-1 / amended D14 | Injecting into the raw dict pre-construction does NOT satisfy the field, OR the existence validator lives somewhere a pre-construction raw-dict write cannot precede. |
| A10 | `rename()` (`documents.py:316`) and `delete_by_path()` (`documents.py:213`) each return `Result[int]` where the int is rowcount, and rowcount 0 means "path not in index". | design Implications "move and delete reuse logic" | Either returns something other than a rowcount int, or returns a non-zero value when the path is absent (breaking the `not_found` mapping, P5-DEPLOY-08). |

---

## Component dependency order

This documents what must exist before each component can work — not the order a developer types code. `/plan-from-specs` owns execution order. Components carry stable IDs (`C2-1` … `C2-8`) that research and the plan reference.

---

### C2-1. Save-or-update routine — `upsert_from_upload()` in `storage/documents.py`

**Goal.** A new data-layer function that stores one uploaded file's record, deciding by content fingerprint whether to insert a new record, skip an identical one, or update a changed one — so the web handler carries no SQL and no branching.

**Build.** Add a new function `upsert_from_upload(...)` in `storage/documents.py` as a sibling to `upsert`/`get_by_path`/`delete_by_path`/`rename`. It accepts the upload fields directly (vault path, extracted text → `full_body`, content fingerprint → `content_hash`, original filename → `original_filename`, file size → `file_size_bytes`; the `metadata` blob is NOT a parameter or is accepted and discarded). Inside one `get_connection()` transaction it reads the existing row for the same vault path (following the `get_by_path` pattern or an inline `SELECT`), then: same fingerprint → no write, return the existing record id; no existing row → INSERT, return the new id; different fingerprint → UPDATE in place, return the (same) id. Returns `Result[int]` (the record id). Writes only existing columns (no DDL). Does NOT call `upsert()` and does NOT take a `WriteOutcome`.

**Depends on.** None (extends existing module). Uses `get_connection` (`db.py:76`) and the migration-008 columns.

**Assumes.** A8 (columns exist), A4 (WAL/Litestream — only relevant at runtime, not to the function logic), C-04 (connection via `get_connection`).

**Interface shape.** Callers (the upload handler) see one function in → `Result[int]` out; the three-way idempotency decision and the SQL are hidden behind it. This is a deep boundary — passes the deletion test: removing it would smear SQL + branching into the web handler.

**Dependency category.** in-process (test directly against a temp DB path).

**Decisions.** Q: does `upsert_from_upload` accept the `metadata` field as a parameter (then ignore it) or not accept it at all? Options: A (accept-and-discard at the handler, keep the function signature clean) / B (accept it in the function and ignore). Leaning A — keep the discard at the handler boundary so the data-layer function only takes columns it actually writes. Resolve in planning.

**Done when.** Calling the routine with a brand-new path stores a `documents` row whose `full_body` equals the uploaded text, with `original_filename`, `file_size_bytes`, and `content_hash` set, and returns its id (P5-DEPLOY-03). Calling it again with the same path and same fingerprint leaves exactly one row unchanged and returns the same id (P5-DEPLOY-04). Calling it with the same path and a different fingerprint leaves exactly one row, now holding the new text and fingerprint (P5-DEPLOY-05).

---

### C2-2. REST handlers + secret-key gate + health route — `mcp_server/api.py`

**Goal.** A small, testable module holding the two business endpoints, the open health check, and a single shared secret-key gate that protects exactly the two sync endpoints.

**Build.** Create `mcp_server/api.py` containing: (a) an upload handler that parses the upload JSON body, hands the fields to `upsert_from_upload` (C2-1), and translates its `Result` into the JSON response — 200 with the record id on success; (b) an event handler that branches on `type`: `moved` calls the existing `rename(old_path, new_path)` (`documents.py:316`), `deleted` calls the existing `delete_by_path(path)` (`documents.py:213`), maps rowcount 0 → `{"status":"not_found"}` (still HTTP 200, not an error), and returns `{"status":"ok"}` otherwise; (c) the open `/health` route returning 200 with a small ok body; (d) a single secret-key gate that checks `Authorization: Bearer <key>` against the `KMS_DAEMON_API_KEY` env var and rejects missing/wrong keys with 401, scoped to apply to `/api/upload` and `/api/event` only. Provide the gate as a path-prefix mechanism the entry point can wrap `/api/*` with (Starlette `Middleware` keyed on path prefix, or an explicit per-handler check shared by both handlers). All branching lives here, NOT in `tools.py` (C-14).

**Depends on.** C2-1 (upload handler calls it).

**Assumes.** A6 (gate covers exactly `/api/*`), A10 (rename/delete return rowcount with 0 = not found), C-13 (no AI/audit), C-14 (logic stays out of `tools.py`).

**Interface shape.** Exposes the handler callables and a way to apply the gate; the entry point (C2-3) mounts them. The auth decision is a single shared seam, not duplicated per handler.

**Dependency category.** in-process / local-substitutable (handlers test directly against a temp DB; the gate tests with a fabricated request carrying right/wrong/missing keys).

**Decisions.** Q: gate as Starlette path-prefix middleware vs. a shared check called at the top of each handler? Options: A (middleware — one place, cleanest, but must verify it does not shadow `/health` or the MCP path, R7/A6) / B (per-handler shared function — trivially scoped but two call sites). Leaning A (middleware) for a single consistent gate; fall back to B if R7 shows path-prefix middleware shadows other routes.

**Done when.** A POST to the upload path with a valid key stores a record and returns its id; a move event relocates the record and carries its search-index entries; a delete event removes the record and its search-index entries in one transaction; an event for an unknown path returns `not_found` at HTTP 200 (P5-DEPLOY-06/-07/-08); a sync request with a missing or wrong key returns 401 and changes nothing, while `/health` answers with no key (P5-DEPLOY-09 / P5-DEPLOY-01).

---

### C2-3. Cloud entry point — `mcp_server/cloud_entry.py`

**Goal.** The single container-mode startup module that turns "container start" into "serving the assistant interface + the three new web addresses on one port", without touching the laptop (stdio) startup.

**Build.** Create `mcp_server/cloud_entry.py`. It imports the existing `mcp` object from `mcp_server/server.py` (which, by import, already registers the five tools and already called `load_dotenv` — do NOT call `load_dotenv` again, C-11). It ensures the database is ready by calling `init_db()` (C2-4 ordering). It obtains the framework's Starlette web app via `mcp.streamable_http_app()`, attaches the two business routes from C2-2 plus the `/health` route, wraps the `/api/*` paths with the C2-2 secret-key gate (and only those paths), and runs uvicorn on `0.0.0.0:8080`. uvicorn owns the event loop — no nested `asyncio.run()` (C-10). Provide a `python -m mcp_server.cloud_entry` runnable entry (a `__main__` guard).

**Depends on.** C2-1, C2-2 (the handlers/gate it mounts), C2-4 (DB-ready ordering it calls), C2-5 (the `VAULT_ROOT` binding so config loads in the container), C2-7 (uvicorn dependency listed).

**Assumes.** A1 (lifespan runs under self-hosted uvicorn — TOP research item), A2 (route-append vs custom_route), A5 (uvicorn importable), A7 (importing `server.py` is side-effect-safe), C-10, C-11.

**Interface shape.** A real seam: the only adapter that produces the cloud deployment. Deleting it removes cloud mode entirely. The stdio `main()` (`server.py:167`) is a separate, untouched seam.

**Dependency category.** local-substitutable for the route wiring (testable by starting the app in-process and issuing requests); the lifespan/uvicorn interaction is the part research must confirm (A1).

**Decisions.** Q: attach the business routes by appending to `app.routes` vs. sub-mounting the MCP app under a wrapper Starlette app? Options: A (append routes — simplest, design's preferred form) / B (sub-mount — extra app layer, lifespan/path risk). Leaning A; research (A2) confirms whether appending is supported before planning commits. Q: `/health` via `mcp.custom_route` (the framework's auth-bypassing decorator, correct for a public route) vs. a plain `Route`? Leaning `custom_route` for `/health` per the design.

**Done when.** Starting the module serves, on one port (8080): the assistant interface answering a tool-list request (P5-DEPLOY-02), the open `/health` returning 200 with no key (P5-DEPLOY-01), and the two guarded sync endpoints behaving per C2-2. The stdio entry point (`python -m mcp_server.server`) still works unchanged (P5-DEPLOY-12).

---

### C2-4. Startup DB ordering — restore → `init_db()` → serve (in the entry point)

**Goal.** Guarantee the container always serves against a correct, fully-migrated database whether it is a first-ever boot or a restart restored from cloud storage.

**Build.** In `cloud_entry.py` (C2-3), call `init_db()` (`db.py:49`) after Litestream restore (which the startup script C2-6 performs before the app launches) and before the web server starts serving. `init_db()` runs `schema.sql` then migrations 001–008 in order and is idempotent, so it is a safe no-op on a restored, populated DB and builds a fresh fully-migrated empty DB when none exists. The DB path is `/data/kb.db`, set via `KMS_DB_PATH=/data/kb.db` in the env-file (no code change for the path — `ApiKeys.kms_db_path` already wires it, `config.py:522/596`).

**Depends on.** C2-3 (lives in the entry point), C2-6 (the script does the restore before the app runs).

**Assumes.** A3 (`init_db` safe no-op after restore — research item R3), A8 (migration-008 columns present after migrate).

**Interface shape.** No new interface — sequencing of existing calls inside the entry point.

**Dependency category.** in-process (testable by pointing `KMS_DB_PATH` at a temp path and asserting schema + idempotency).

**Done when.** A first start with no backup creates a fresh DB at the data path whose `schema_version` is the latest and whose tables include `documents` (with `full_body`) and `knowledge_entries` (P5-DEPLOY-10). A restart with a backup present serves against the restored DB with prior rows intact — `init_db()` does not wipe or duplicate them (P5-DEPLOY-11).

---

### C2-5. `VAULT_ROOT` env binding — `core/config.py` (throwaway, TD-059)

**Goal.** Let the container satisfy the required vault-root config field with an env var, so the system boots in a container that has no notes folder — without a cloud-specific config file.

**Build.** Add a `VAULT_ROOT` env override to `core/config.py`. **Mechanism (corrected after research — this is NOT a literal mirror of `KMS_DB_PATH`):** the vault-root "must exist on disk" check is a `MainConfig` model_validator (`config.py:372-382`) that fires *at construction time* against the YAML value, *before* any post-construction override line runs; and `VaultConfig` has no `validate_assignment` (unlike `DatabaseConfig`), so assigning `cfg.main.vault.root` after the model is built would neither prevent the construction-time crash nor re-validate. Therefore the override must be applied to the **raw config dict before the model is constructed**: in `load_config()`, after the YAML is loaded into the raw mapping and before `MainConfig(**raw_main)` is called, when the `VAULT_ROOT` env var is set, write its value into the raw `vault.root` slot. Read the env var via a `Field(default=None, alias="VAULT_ROOT")` on `ApiKeys` (next to `kms_db_path`, `config.py:522`). Add a unit test. Mark the binding throwaway (TD-059) with a comment — removed at the config split. This is the ONLY change to `core/config.py` (C-CONFIG); do not alter other config behavior.

**Depends on.** None.

**Assumes.** A9 corrected (pre-construction raw-dict injection satisfies the field; the existence validator runs at construction), C-CONFIG (config.py is in scope this slice).

**Interface shape.** One env alias on `ApiKeys` + one pre-construction injection in `load_config()`. This deliberately **differs** from the `KMS_DB_PATH` path (which applies post-construction) precisely because vault-root validates at construction, not on assignment.

**Dependency category.** in-process (unit test sets `VAULT_ROOT`, points at a temp dir, asserts `cfg.main.vault.root` and that loading does not raise the existence error).

**Done when.** With `VAULT_ROOT=/data/vault` set and that directory present on disk, `load_config()` returns a config whose vault root is `/data/vault` and does NOT raise the vault-root existence error. With `VAULT_ROOT` unset, the YAML value is used unchanged. A unit test proves the env var overrides the YAML value and that the existing local/stdio load path is unaffected.

---

### C2-6. Startup script — `scripts/start.sh` (restore, replicate, launch, shutdown-flush)

**Goal.** The wrapper the container runs: it drives Litestream restore-on-boot and final-flush-on-shutdown around the app, keeping the Python app unaware of Litestream.

**Build.** Create `scripts/start.sh` that: (1) if Litestream env vars are set, tries to restore `/data/kb.db` from Object Storage; (2) if restore fails (no backup) or env vars are unset, skips (the app then creates a fresh DB via C2-4); (3) if env vars are set, starts Litestream replication in the background; (4) launches the app with `python -m mcp_server.cloud_entry`; (5) traps SIGTERM and runs the locked shutdown order (D24, OQ-3): signal uvicorn to drain (finish in-flight requests, stop accepting new ones) → run the Litestream final flush → exit. The app stays unaware of Litestream.

**Depends on.** C2-3 (the app it launches), C2-8 (the Litestream config it points at), the Dockerfile (C2-8) for the `/data` directory and Litestream binary.

**Assumes.** A4 (WAL/Litestream coexist — R5), and the shutdown trap/drain/flush sequence (R4, owner resolved = this script).

**Interface shape.** A shell orchestrator; no Python interface.

**Dependency category.** local-substitutable (verified by `docker run` + `curl`, and by a stop signal observing the flush-then-exit order).

**Decisions.** Q: exact mechanic to "drain uvicorn then flush" — forward SIGTERM to the uvicorn child and wait, then run the Litestream flush, vs. an explicit graceful-stop signal? Leaning: trap SIGTERM in the script, send it to the uvicorn process, `wait` for it to drain, then run the final Litestream flush, then exit. Verify the trap/drain/flush mechanics sequence correctly (R4) during research/planning.

**Done when.** `docker run -p 8080:8080` starts the container and `/health` returns 200 (P5-DEPLOY-01). With backup configured, a stop-and-restart restores the DB from Object Storage and prior rows survive (P5-DEPLOY-11). With no backup, first start creates a fresh DB (P5-DEPLOY-10). A stop signal drains in-flight requests, then pushes a final backup, then exits — in that order.

---

### C2-7. `pyproject.toml` — explicit `uvicorn` dependency + container entry point

**Goal.** Make the cloud entry point's direct dependency on uvicorn explicit so a future framework version cannot silently break the build, and provide a clean way to launch the container app.

**Build.** Add `uvicorn` to the `dependencies` list in `pyproject.toml` (it arrives transitively via `mcp` today, but `cloud_entry.py` imports it directly — R6/A5). Optionally add `starlette` if `cloud_entry.py`/`api.py` import it directly. Confirm the container launches the app via `python -m mcp_server.cloud_entry` (the `__main__` guard from C2-3); no new `[project.scripts]` console entry is required for the cloud path. Leave the existing `kms = "cli.main:cli"` entry untouched.

**Depends on.** C2-3 (the module that imports uvicorn).

**Assumes.** A5 (uvicorn/starlette transitively available today).

**Interface shape.** Dependency manifest change only.

**Dependency category.** local-substitutable (verified by a clean install + `import uvicorn` succeeding in the built image).

**Done when.** A fresh `uv sync` install resolves `uvicorn` explicitly, and `python -m mcp_server.cloud_entry` starts the server in the built image (P5-DEPLOY-01/-02).

---

### C2-8. Dockerfile + `litestream.yml` template

**Goal.** Package the system as a runnable container image for the cloud target, including the Litestream binary, the `/data` directory, the dummy vault directory, and a Litestream config driven entirely by env vars.

**Build.** Create a `Dockerfile`: platform `linux/amd64`, base `python:3.12-slim`; install `src/` dependencies plus the Litestream binary; create `/data/` and `/data/vault/` (the dummy vault dir so the `VAULT_ROOT=/data/vault` existence check passes, TD-059); expose port 8080; entry = the startup script (C2-6). Create `litestream.yml` as a template that watches `/data/kb.db` and replicates to an S3-compatible target with bucket/endpoint/keys read from `LITESTREAM_BUCKET`/`LITESTREAM_ENDPOINT`/`LITESTREAM_ACCESS_KEY_ID`/`LITESTREAM_SECRET_ACCESS_KEY` env vars — no secrets baked into the image (D11). The env-file (per tester) also sets `KMS_DB_PATH=/data/kb.db`, `VAULT_ROOT=/data/vault`, and `KMS_DAEMON_API_KEY`.

**Depends on.** C2-6 (the script it runs as entry), C2-7 (the dependency manifest the image installs).

**Assumes.** A4 (WAL/Litestream coexist), and that the `mkdir -p /data/vault` in the image satisfies the vault-root existence check before the app loads config (A9).

**Interface shape.** Container image + config template; no Python interface.

**Dependency category.** true-external-ish (verified end-to-end with `docker build` + `docker run` + `curl`).

**Done when.** `docker build --platform linux/amd64 .` completes without error; `docker run -p 8080:8080 <image>` starts the container and `/health` returns 200 (P5-DEPLOY-01); the MCP tool-list answers on the same port (P5-DEPLOY-02); the two sync endpoints behave per C2-2 against the containerized DB; with Litestream env vars set, the DB streams up and is restored on restart (P5-DEPLOY-11).

---

## Handoff notes

- **Contract with Phase 6 (daemon):** This phase promises a stable HTTP contract the daemon will call. The request/response shapes are fixed below. The `metadata` field is accepted but discarded this slice (D8) — document this so a daemon author does not read the vanished metadata as a bug. The secret-key gate here is the daemon→`/api/*` shared key (`KMS_DAEMON_API_KEY`), NOT the daemon's platform auth (OQ-2, Phase 6).
- **Contract with Phase 7 (capture refactor):** `/api/upload` is a pure-storage stub. Phase 7 adds summarize/classify/index/audit and decides what the stored `metadata` becomes (likely a new column + migration at that time). `upsert()` (`documents.py:100`) is left for Phase 7; this slice does not touch it.
- **Top research item (A1 / R1):** Whether FastMCP's lifespan runs when we self-host uvicorn on `streamable_http_app()` is the single biggest unknown. If it does not, the MCP path breaks in cloud mode even though REST works. Research this first; it gates C2-3.
- **Open uncertainty (A2):** Confirm appending to `app.routes` (vs. sub-mounting) is the supported way to add the business routes; this affects C2-2's gate wiring and C2-3's mount.
- **Research outcome (resolved):** (1) FastMCP `streamable_http_app()` lifespan under uvicorn (A1) — **VALIDATED**; lifespan runs, per-session not at boot (see A1 note). (2) `init_db()` on a populated DB (A3) — **VALIDATED** safe no-op. (4) `VaultConfig.root` override (A9) — **CORRECTED**: not a `KMS_DB_PATH` mirror; the existence check is a construction-time `MainConfig` validator (`config.py:372-382`) and `VaultConfig` has no `validate_assignment`, so the override is injected into the raw config dict pre-construction in `load_config()` (see C2-5). **Still verify at deploy/plan time:** (3) Litestream + `wal_autocheckpoint=100` interaction (A4) — unverifiable from code, deploy-time check. (5) The SIGTERM trap/drain/flush mechanics in `scripts/start.sh` (R4) — confirm uvicorn drains before the Litestream flush runs.

---

## Request / response contracts (copied from draft §4/§5)

### `POST /api/upload`

Requires `Authorization: Bearer <KMS_DAEMON_API_KEY>`.

Request:
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

Response (200):
```json
{"status": "ok", "document_id": 42}
```

Behavior (stub): check `content_hash` against the existing row for the same `vault_path` → skip if match (idempotent, return existing id); new `vault_path` → INSERT into `documents` with `full_body`, `original_filename`, `file_size_bytes`, `content_hash`; same path, different hash → UPDATE in place. The `metadata` field is accepted and NOT persisted this slice (Phase 7 decides usage). Returns the document id.

### `POST /api/event`

Requires `Authorization: Bearer <KMS_DAEMON_API_KEY>`.

Request (move/rename — one event type covers both, D21):
```json
{
  "type": "moved",
  "old_path": "Projects/Alpha/note.md",
  "new_path": "Projects/Beta/note.md"
}
```
or (delete):
```json
{
  "type": "deleted",
  "path": "Projects/Alpha/note.md"
}
```

Response (200):
```json
{"status": "ok"}
```

Behavior (stub): `moved` → reuse `rename(old_path, new_path)` (updates `vault_path` and carries the keyword + meaning index rows along, one transaction). `deleted` → reuse `delete_by_path(path)` (hard-delete from `documents` + `embeddings_vec` + `notes_fts`, one transaction). Unknown path (rowcount 0) → return `{"status": "not_found"}` — HTTP 200, NOT an error (the file may not have been captured yet).

### `GET /health`

No auth. Response (200):
```json
{"status": "ok"}
```

---

## Litestream config template (from draft §6)

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

## Success / verification (acceptance criteria, cross-referenced to behavior IDs)

Each row is a draft acceptance criterion mapped to a behavior-inventory ID (`docs/system_behavior/behavior_inventory.yaml`) and the component(s) that satisfy it.

| Behavior ID | Acceptance criterion | Components |
|-------------|----------------------|-----------|
| P5-DEPLOY-01 | `docker build --platform linux/amd64` succeeds; `docker run -p 8080:8080` starts the container; `/health` returns 200 with a small ok body and needs no key | C2-2, C2-3, C2-6, C2-8 |
| P5-DEPLOY-02 | The MCP server answers a tool-list request (the five existing tools) on the same single port | C2-3 (gated by A1) |
| P5-DEPLOY-03 | `POST /api/upload` with a valid key and a new path stores a `documents` row with `full_body` = uploaded text, plus `original_filename`/`file_size_bytes`/`content_hash`, and returns the id | C2-1, C2-2 |
| P5-DEPLOY-04 | Re-`POST` the identical payload → idempotent: exactly one row, no rewrite, same id | C2-1 |
| P5-DEPLOY-05 | `POST` same path with a different hash → row updated in place (new text + hash), still one row | C2-1 |
| P5-DEPLOY-06 | `POST /api/event` move → `vault_path` updated and search-index entries carried along | C2-2 (reuses `rename`) |
| P5-DEPLOY-07 | `POST /api/event` delete → row + keyword + meaning index entries removed in one transaction (hard delete) | C2-2 (reuses `delete_by_path`) |
| P5-DEPLOY-08 | Event for an uncaptured path → `{"status":"not_found"}` at HTTP 200, not an error | C2-2 (rowcount-0 mapping) |
| P5-DEPLOY-09 | Sync request with wrong/missing key → 401, no DB change; `/health` still open | C2-2 (gate), C2-3 (scoping) |
| P5-DEPLOY-10 | First start with no backup → fresh DB at the data path, fully-migrated schema (latest `schema_version`, `documents` with `full_body`, `knowledge_entries`) | C2-4, C2-6, C2-8 |
| P5-DEPLOY-11 | Restart with backup configured → DB restored from Object Storage before serving; prior rows present (modulo ~1s crash window) | C2-4, C2-6, C2-8 |
| P5-DEPLOY-12 | Existing stdio entry point (`python -m mcp_server.server`) still works unchanged | C2-3 (does not modify `server.py::main`) |

---

## Open questions / deferred

- **OQ-2 (parked, Phase 6):** Whether the daemon uses the container's AgentBase IAM service account or its own. Out of scope for this slice; logged so the `/api/*` secret-key gate is not mistaken for the daemon's platform auth (D4/D18). Recommendation (non-binding): dedicated account, decided in Phase 6.
- **TD-059 (tracked debt):** The `VAULT_ROOT` binding (C2-5) and the dummy `/data/vault` directory are throwaway bridges, removed when the config split lands (Phases 6/7/9). Touch them only as a unit; do not build anything else on the vault root cloud-side.
- **Deferred to research (not blocking the spec):** A1/R1 lifespan-under-uvicorn, A3/R3 `init_db` idempotency after restore, A4/R5 WAL vs. Litestream checkpointing, A9 `VaultConfig` assignment mechanism, R4 shutdown trap/drain/flush mechanics. These are research items, not unresolved design decisions.

---

## Next step

Spec written. Run `/research` to verify spec assumptions against real code before planning — start with A1/R1 (FastMCP lifespan under self-hosted uvicorn), which gates component C2-3.
