# P5 Slice 2 — Deployment Foundation (AgentBase) — Design

_Created: 2026-06-13_
_Tier: MEDIUM (design-lite). Purely additive infrastructure — no pipeline changes, no config split._
_Locked requirements: `docs/0_draft/P5_slice2_deployment_foundation.md` (decisions D1–D25). This doc resolves only the HOW left open below that line._
_Behavior-inventory ID prefix: **`P5-DEPLOY`** (entries `P5-DEPLOY-01` … `P5-DEPLOY-12`)._
_Architecture context: `docs/0_draft/cloud_native_rearchitecture.md` (system direction) + `docs/0_draft/agentbase_research.md` (platform)._
_**Post-design sign-off (2026-06-13):** OQ-1 RESOLVED → add a `VAULT_ROOT` env binding to `core/config.py` (amends D14; `config.py` is now an in-scope MODIFIED file). OQ-3 RESOLVED → the startup script owns the Litestream final flush. OQ-2 parked for Phase 6._

---

## In plain terms (read this first)

This slice makes the knowledge system we already built able to **run as a container in the cloud** (on VNG's AgentBase) and gives the future daemon — the little program that will watch the user's laptop files (Phase 6) — **a cloud address to send files to**. Nothing about how the system thinks, summarizes, or classifies changes. We are only building the plumbing: a container recipe, a web front door on one port, a "still alive?" health check, two not-yet-smart endpoints for receiving files and file-move/delete notices, and a way to keep the database alive across container restarts.

Everything here is **additive**. The existing local setup (the version a developer runs on their own laptop for Claude Desktop) keeps working exactly as before. We are adding a second way to start the same system, not replacing the first.

---

## Cast of characters (referenced 3+ times)

| Name | One-line role |
|------|---------------|
| Cloud entry point | The new container startup module that serves everything on one web port |
| MCP server | The existing knowledge-assistant interface a chat client connects to (built in Phase 4) |
| Save-or-update routine | The new database function that stores or refreshes one uploaded file's record |
| Litestream | Background helper that streams the database to cloud storage and restores it on restart |
| Object Storage | Cloud file store (S3-compatible) that holds the database backup files |
| Health check | The open `/health` web address the platform pings to confirm readiness |
| Secret-key gate | The check that an incoming sync request carries the right shared key |

A fuller glossary of new terms is at the end of this doc, and in `CONTEXT.md`.

---

## Decision

**Mount the new web endpoints directly onto the existing knowledge-assistant's web application, in a new container-mode startup module, and serve all of them on one port — with the file-receiving and event-receiving handlers living in their own dedicated module behind a single secret-key gate.**

In plain terms: we take the web app that the existing knowledge-assistant interface already produces, bolt three extra web addresses onto it (`/health`, `/api/upload`, `/api/event`), wrap a secret-key check around the two sync addresses, and start the whole thing as one web server on port 8080. The local laptop startup is untouched.

- The MCP framework already produces a mountable web application and exposes a way to add custom routes (`FastMCP.streamable_http_app()` returns a Starlette app; `FastMCP.custom_route(...)` adds extra routes). Both confirmed present in the installed `mcp` package.
- The file-save logic goes through a **new** database function `upsert_from_upload()` in `storage/documents.py` (per D19) — never raw SQL in the web handler, never the existing `upsert()`.
- Why this over the alternatives: it reuses the framework's own web app and route mechanism (least new code, one process, one port per D17), while keeping the authenticated business endpoints in a real module where they can carry middleware and be tested — instead of squeezing them through the framework's auth-bypassing convenience decorator. See **Options explored**.

---

## Q1 Diagram — what happens inside the running container

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

## Guardrail Checklist (from `/guardrail-check Review`)

Required input for `/writing-detailed-specs`. Eight constraints apply across the three touched domains (DB Integrity, Architecture, Async & CLI). Each option below is checked against these.

- **C-04 · PRAGMA foreign_keys=ON on every new connection** — `upsert_from_upload()` must go through `storage/db.py::get_connection()` (which calls `_connect()`, which sets the pragma). Never open a raw `sqlite3.connect()`.
- **C-05 · All schema changes via versioned .sql deltas** — Slice 2 adds **no** new columns or tables. `full_body`, `original_filename`, `file_size_bytes` already exist (migration 008). `upsert_from_upload()` is INSERT/UPDATE only — no DDL.
- **C-12 · Public functions return Success/Failure** — by the letter this binds only `handlers/` and `pipelines/`; `upsert_from_upload()` lives in `storage/`. But the storage layer's existing convention IS `Result`-returning (`upsert`, `delete_by_path`, `rename` all return `Result`). Match it: `upsert_from_upload()` returns `Result[int]`.
- **C-13 · Audit log non-negotiable from Phase 1** — triggers on AI decisions only. The stubs make NO AI decision (D7: pure storage, no summarize/classify). No `provider.complete()` is added, so no audit write is required. Confirm no LLM call sneaks in.
- **C-14 · `mcp_server/tools.py` is logic-free** — the REST handlers and the auth branch MUST NOT be added to `tools.py`. They live in the new module. `tools.py` stays byte-for-byte unchanged.
- **C-15 · Never add an MCP tool before its pipeline exists** — the two sync endpoints are plain web routes, NOT MCP tools. They do not register via `mcp.tool()`. C-15 is not triggered.
- **C-10 · CLI commands wrap async with asyncio.run()** — the cloud entry point is not a Click command; uvicorn owns its own event loop. Do not nest `asyncio.run()` inside the running loop.
- **C-11 · load_dotenv called exactly once** — `mcp_server/server.py` already calls `load_dotenv` at module top. The cloud entry point imports `server.py`, so the call already happens. The cloud entry point must NOT call `load_dotenv` again.

---

## Implications — what this change actually means

- **The system gains a second way to start: as a cloud web server, without losing the laptop way.**
  - A new module `mcp_server/cloud_entry.py` imports the existing `mcp` FastMCP object from `mcp_server/server.py`, obtains its Starlette web app via `mcp.streamable_http_app()`, attaches the extra routes, and runs uvicorn on `0.0.0.0:8080`. `mcp_server/server.py::main()` (stdio, `mcp.run()`) is unchanged (D6). Verified: `server.py` constructs `mcp = FastMCP("kms", lifespan=_lifespan)` at module scope and is already importable.

- **The existing chat-assistant interface keeps working, just reachable over the web instead of only over a local pipe.**
  - FastMCP serves the MCP protocol on its `streamable_http_path` (default `/mcp`) inside the same Starlette app. No tool code changes; the five tools register exactly as today (`mcp_server/tools.py::register_tools`). The platform's Resource Gateway handles MCP-level auth (D18) — the container does not gate the MCP path itself.

- **One container = one web server = one port. No process supervisor, no second port.**
  - REST routes and the MCP protocol share the single Starlette app and the single uvicorn process (D5/D17). Confirmed the `mcp` dependency already pulls in `uvicorn` (0.49.0) and `starlette` (1.3.0) transitively — no new Python web dependency is strictly required, though listing `uvicorn` explicitly in `pyproject.toml` is advisable since `cloud_entry.py` imports it directly (`mcp` requires: anyio, httpx, …, **starlette**, **uvicorn**).

- **Receiving a file stores a database record and nothing more (for now).**
  - `/api/upload` hands its parsed body to a NEW `upsert_from_upload()` in `storage/documents.py` (D19). This function does NOT take a `WriteOutcome` (the existing `upsert()` does, at `documents.py:100`); it accepts the upload fields directly and runs `INSERT`/`UPDATE` within one `get_connection()` transaction. The existing `upsert()` is left byte-for-byte unchanged (D19, draft "Not modified").

- **Re-sending the same file is safe; sending a changed file overwrites; both decided by a content fingerprint.**
  - `upsert_from_upload()` reads the existing row for the same `vault_path` (via the existing `get_by_path()` pattern or an inline `SELECT`), compares `content_hash`: same → no-op (return existing id); absent → INSERT; different → UPDATE. This is the idempotency model in D23. No request-ID dedup needed.

- **Move and delete notices reuse logic we already wrote and tested — we are not re-implementing cleanup.**
  - `/api/event` `moved` calls the existing `documents.rename(old, new)` (`documents.py:316`) — already copies the keyword index (`notes_fts`) and meaning index (`embeddings_vec`) rows old→new within one transaction. `deleted` calls the existing `documents.delete_by_path(path)` (`documents.py:213`) — already deletes from `embeddings_vec`, `notes_fts`, and `documents` in one transaction (D20). Both return `Result[int]` where the int is rowcount; rowcount 0 → reply `not_found` (draft §5, D-not-an-error).

- **A first-ever boot builds the database from scratch; a restart restores it from cloud storage.**
  - On startup the cloud entry point calls `init_db()` (`storage/db.py:49`), which runs `schema.sql` then every migration 001–008 in order, leaving a fully-migrated empty DB if none exists (D12). Verified: `init_db` is idempotent (CREATE TABLE IF NOT EXISTS + version-gated migrations), so calling it after a Litestream restore is harmless. The restore itself happens in the startup script BEFORE the app starts (Litestream, D11/D12).

- **The database has to live outside the container because the container keeps no permanent disk.**
  - Litestream streams `/data/kb.db` (D13) to VNG Object Storage roughly once per second; on restart it downloads the latest copy first. Credentials come from env vars (`LITESTREAM_*`), never baked into the image (D11). A crash can lose ≤~1s of writes (D25), reconciled later by the Phase 6 startup scanner.

- **The database path moves to a dedicated `/data/` directory — achievable with no code change.**
  - D13 wants `/data/kb.db`. `DatabaseConfig.path` (`core/config.py:159`) defaults to `./data/kb.db` but is overridable via the `KMS_DB_PATH` env var (`ApiKeys.kms_db_path`, `core/config.py:522`, applied in `load_config()` at `config.py:596`). Set `KMS_DB_PATH=/data/kb.db` in the env-file — no code edit. **[VERIFIED]**

- **Module-depth note: the new code earns its keep; it is not a pass-through.**
  - `upsert_from_upload()` — apply the deletion test: if removed, its three-way idempotency decision (new / skip-same / overwrite) plus the C-04/C-05-safe transaction handling would have to be re-implemented inside the web handler, which would then carry SQL and branching — worse for testing and for C-14-style "keep logic out of the thin layer" discipline. It earns its keep.
  - `cloud_entry.py` — a real seam: it is the only adapter that turns "container start" into "serving MCP + REST on 8080". Deleting it removes the cloud deployment entirely; complexity does not vanish, it just becomes impossible. Keep.
  - The REST-handler module (whether `cloud_entry.py`-inline or a separate `mcp_server/api.py`) is the **open sub-decision** below.

---

## Options explored

The locked decisions (D1–D25) fix the WHAT. Three HOW sub-decisions were left open by the draft. Each is resolved below; the primary axis (where the REST handlers live + how they mount) gets full option treatment.

### Axis 1 — Where the REST handlers live + how they mount on the FastMCP app

#### Option A — Mount Starlette routes via a dedicated handler module, attached to the framework's web app (Recommended)

**What this means:** We put the two business endpoints (`/api/upload`, `/api/event`) and their secret-key gate in their own small module, then attach them — plus a `/health` route — onto the web app the knowledge-assistant framework already produces. One server, one port. The handlers live where they can carry a real authentication wrapper and be unit-tested.

**Approach:** `cloud_entry.py` calls `mcp.streamable_http_app()` to get the Starlette app, then appends `Route("/api/upload", upload_handler, methods=["POST"])` and `Route("/api/event", event_handler, methods=["POST"])` to its `.routes`, wraps the `/api/*` paths with an auth check (Starlette `Middleware` keyed on path prefix, or an auth check inside each handler), adds `/health` (either as another Route or via `mcp.custom_route`), and runs uvicorn on `0.0.0.0:8080`. Handlers import `upsert_from_upload`, `rename`, `delete_by_path` from `storage/documents.py`.

**Files touched:** new `mcp_server/cloud_entry.py` (startup + mount), new `mcp_server/api.py` (the two handlers + auth gate), `storage/documents.py` (+`upsert_from_upload`), `mcp_server/server.py` (already exposes `mcp` at module scope — likely zero change; a comment confirming it is a public export is enough), `pyproject.toml` (list `uvicorn` explicitly), `core/config.py` (+`VAULT_ROOT` env binding mirroring `KMS_DB_PATH`, + unit test — throwaway per TD-059, see OQ-1).

**Cost:** Dev effort: low–medium. Runtime cost: none beyond serving HTTP (no LLM, no vault scan). Maintenance: one new module to keep; auth is one small wrapper.

**Risk:** Mounting onto `streamable_http_app()` assumes the returned Starlette app's `.routes` list is appendable before `uvicorn.run` — true for Starlette, but the FastMCP lifespan must still run (it builds the context engine). Research must confirm the app returned by `streamable_http_app()` carries FastMCP's lifespan so the MCP path keeps working when we run uvicorn ourselves.

**Module depth:**
- New boundaries: `mcp_server/api.py` (handlers) — passes deletion test (its idempotency + auth + Result-handling would otherwise smear into `cloud_entry.py`). `upsert_from_upload()` — passes (see Implications).
- New interfaces: the daemon→cloud HTTP contract is a real seam — it has 2 real future adapters (the Phase 6 daemon as caller; `curl`/tests as the other), not speculative.
- Existing modules affected: `storage/documents.py` is a deep module already; we extend it with one sibling function, consistent with its existing `upsert`/`rename`/`delete_by_path` family. `mcp_server/server.py` is deep; we only read its `mcp` export.

**Constraints check:**
- [x] C-04 — satisfies (handler → `upsert_from_upload` → `get_connection`).
- [x] C-05 — satisfies (no DDL).
- [x] C-12 — satisfies (`upsert_from_upload` returns `Result`; handlers return HTTP responses, outside the handlers/pipelines letter).
- [x] C-13 — satisfies (no AI decision, no audit needed).
- [x] C-14 — satisfies (`tools.py` untouched).
- [x] C-15 — satisfies (REST routes, not MCP tools).
- [x] C-10 — satisfies (uvicorn owns the loop).
- [x] C-11 — satisfies (no second `load_dotenv`).

#### Option B — All routes inline in `cloud_entry.py` via the framework's `custom_route` decorator

**What this means:** Skip the separate handler module; register every route (including `/api/upload` and `/api/event`) using the framework's built-in "add a custom route" decorator, all inside the startup module.

**Approach:** Use `mcp.custom_route("/api/upload", methods=["POST"])` etc. directly in `cloud_entry.py`. Fewer files.

**Files touched:** `mcp_server/cloud_entry.py` (everything), `storage/documents.py` (+`upsert_from_upload`), `pyproject.toml`.

**Cost:** Dev effort: low. Maintenance: everything in one file; harder to unit-test handlers in isolation.

**Risk — disqualifying for `/api/*`:** the framework's `custom_route` docstring explicitly states *"Routes using this decorator will not require authorization … intended for uses that are … public such as health check endpoints."* D18 requires API-key auth on `/api/*`. Using `custom_route` for the authenticated endpoints fights the framework's stated contract (it is designed to *bypass* auth), so the secret-key gate would have to be re-implemented inside each handler with no shared middleware seam. `custom_route` is, however, the *right* tool for the open `/health` route.

**Module depth:** no new module boundary — but that is the problem here: the auth logic and SQL-adjacent handler bodies concentrate in the startup file, making the startup module shallow-but-overloaded.

**Constraints check:** same as Option A except the auth implementation is weaker (per-handler, not a shared wrapper) — still satisfies C-14/C-15 but increases the chance of an inconsistent gate across the two endpoints.

> **Recommended: Option A.** It uses the framework's own web app and route mechanism (so MCP + REST genuinely share one port and one process per D17) while keeping the *authenticated* endpoints in a real, testable module behind a single secret-key gate — instead of routing them through a convenience decorator the framework explicitly built to *skip* authentication. Use `custom_route` only for the open `/health` route.

**Rejected alternatives (one line each):**
- *Separate uvicorn process / second port for REST* — violates D5/D17 (one container, one port); adds a supervisor for zero benefit.
- *Sub-application mount (`Starlette().mount("/", mcp_app)`)* — re-parents the MCP app under a wrapper; viable but adds an extra app layer and risks lifespan/path quirks for no gain over appending routes to the existing app. Folded into Option A's simpler "append routes" form.
- *Put REST handlers in `mcp_server/tools.py`* — hard-blocked by C-14 (no logic in `tools.py`) and conceptually wrong (these are not MCP tools, C-15).

### Axis 2 — How the upload stores the file (the D19 tension)

**Resolved: a new `upsert_from_upload()` in `storage/documents.py`.** The draft's prose at ~line 261 ("stub uses direct INSERT") is **superseded by D19** (locked in the later grill). Direct SQL in the web handler is rejected: it would put SQL + branching in the thin web layer (against the project's keep-logic-in-the-data-layer discipline and harder to make C-04-safe), and would duplicate the idempotency decision the data layer should own. Reusing the existing `upsert()` is also rejected — it takes a `WriteOutcome` (`documents.py:100`), a vault-write artifact that does not exist in the cloud upload path, and D19 explicitly says leave `upsert()` for Phase 7. The new function accepts the upload fields directly, returns `Result[int]`, and does its own three-way idempotency check.

### Axis 3 — What to do with the open-ended `metadata` field in the stub

**Resolved: store-and-ignore (do NOT add a JSON column this slice).** D8 says Phase 7 decides usage; the lightest stub-correct option is to accept the `metadata` field in the request body and **not persist it** in Slice 2 — adding a column would be a schema change (C-05: a new migration) for data nothing reads yet, and Phase 7 owns that decision. The pipe is "wide enough" the moment the endpoint accepts the field without rejecting it. (If a future reviewer wants it persisted now, that is a one-line migration + column — but it is out of scope for the additive stub.)

---

## Known tradeoffs (what we give up by choosing Option A)

- **A little more structure than the absolute minimum.** Option B (everything inline) is fewer files. We accept one extra small module (`api.py`) to get a single, testable, consistent auth gate and to keep the startup file thin. For a stub this is a deliberate, cheap investment that Phase 7 inherits cleanly.
- **We depend on the framework's web app carrying its own lifespan when we run uvicorn ourselves.** If `streamable_http_app()` does not wire the lifespan the way `mcp.run()` does, the context engine that the MCP tools rely on might not initialize under the cloud entry point. This is the single biggest thing research must verify. (It does not affect the REST stubs — only the MCP path inside the same server.)
- **The `metadata` field is accepted but discarded this slice.** A daemon author testing against the stub will see their metadata "vanish." That is correct for the stub (D8) but must be documented in the contract so it is not mistaken for a bug.

---

## Risks (for research / planning / implementation to verify or watch)

- **R1 — FastMCP lifespan under self-hosted uvicorn.** Confirm that running `uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=8080)` enters the FastMCP `_lifespan` (which builds the `ContextInjectionEngine`). If not, the MCP tools break in cloud mode even though REST works. Research should also confirm whether appending to `app.routes` after `streamable_http_app()` is the supported way vs. sub-mounting. (Acceptance P5-DEPLOY-02.)
- **R2 — RESOLVED (was a blocker): add a `VAULT_ROOT` env binding to `core/config.py`.** No such binding exists today (only `KMS_DB_PATH` is overridable). The plan adds one (~3 lines) + a unit test so `VAULT_ROOT=/data/vault` satisfies `VaultConfig.root`. **Research correction:** it is NOT a `KMS_DB_PATH` mirror — the env value must be injected into the raw config dict *before* `MainConfig` is constructed (the existence check is a construction-time validator; `VaultConfig` has no `validate_assignment`). See OQ-1 + spec C2-5. Throwaway — removed at config-split (TD-059).
- **R3 — `init_db()` after a Litestream restore.** Verify that calling `init_db()` on a restored, already-populated DB is a safe no-op (it should be: CREATE TABLE IF NOT EXISTS + version-gated migrations). Confirm the startup ordering: restore → `init_db()` → serve. (Acceptance P5-DEPLOY-10/-11.)
- **R4 — Shutdown ordering (D24). Owner RESOLVED: the startup script.** Per OQ-3 sign-off (2026-06-13), `scripts/start.sh` traps SIGTERM → signals uvicorn to drain in-flight requests → runs the Litestream final flush → exits; the Python app stays unaware of Litestream. Research/plan still verify the trap+drain+flush mechanics sequence correctly.
- **R5 — Litestream + WAL interaction.** The DB runs in WAL mode (`db.py:18`) with `wal_autocheckpoint=100`. Litestream relies on WAL; confirm the autocheckpoint setting and Litestream's checkpointing do not fight (Litestream documents a recommended checkpoint posture). Low risk but worth a research note.
- **R6 — `uvicorn` listed transitively only.** `cloud_entry.py` will `import uvicorn` directly. It works today (transitive), but a future `mcp` version could drop it. Add `uvicorn` to `pyproject.toml` explicitly (low effort, removes a latent break).
- **R7 — Auth gate scope.** Ensure the secret-key gate covers exactly `/api/*` and never `/health` or the MCP path (D18). A path-prefix middleware is the clean way; verify it does not accidentally shadow the MCP route.

---

## Open questions

**OQ-1 — RESOLVED (user sign-off 2026-06-13): add a `VAULT_ROOT` env binding to `core/config.py`.**

The cloud container has no notes folder, but the system crashes at startup unless the vault root (`VaultConfig.root`) exists on disk. D14/TD-059 named an env var (`VAULT_ROOT=/data/vault`) as the fix but assumed that binding already existed — it does **not**. The config code reads the vault path only from `config.yaml`; only the database path (`KMS_DB_PATH`) has an env override, none for the vault root. So D14 as literally written (env var **and** `config.py` untouched) is impossible — one clause had to yield.

**Decision:** add a `VAULT_ROOT` environment override to `core/config.py`. Each tester's env-file sets `VAULT_ROOT=/data/vault`; the Dockerfile runs `mkdir -p /data/vault` so the existence check passes.

**Mechanism correction (research, 2026-06-13):** the override is NOT a literal mirror of `KMS_DB_PATH`. `KMS_DB_PATH` is applied *after* the config model is built; but the vault-root existence check is a `MainConfig` model_validator (`config.py:372-382`) that fires *at construction* against the YAML value, and `VaultConfig` has no `validate_assignment` — so a post-construction assignment would crash at import before it could help. The correct mechanism is to **inject `VAULT_ROOT` into the raw config dict before `MainConfig(**raw_main)` is constructed**, inside `load_config()` (read the env var via a `Field(alias="VAULT_ROOT")` on `ApiKeys`). See spec C2-5.

**Why this over writing the path into `config.yaml`:** it keeps **one identical image** for every tester (no cloud-specific `config.yaml`, no startup substitution step) and matches the env-driven container pattern already locked for Litestream (D11). Cost: a ~3-line edit to `core/config.py` plus a unit test.

**This amends D14:** `core/config.py` is now a MODIFIED file this slice (the draft's "Not modified" note rested on the false assumption that `VAULT_ROOT` was already wired). The binding is throwaway — removed when the config split lands (Phases 6/7/9), tracked under TD-059.

**OQ-2 — Should the daemon use the same AgentBase IAM service account as the container, or its own?**

Right now, no daemon exists; this slice only builds the cloud side. The daemon's own authentication to AgentBase (separate from the simple shared `KMS_DAEMON_API_KEY` it uses on `/api/*`) is noted as undecided in the platform research (§11.3).

The question: when Phase 6 builds the daemon, does it reuse the container's IAM credentials (simpler, less isolation) or get a dedicated account (more setup, better blast-radius control)?

**If shared:** one fewer credential to provision; a leak exposes both sides.
**If dedicated:** cleaner revocation per device; more onboarding steps.

Recommendation: dedicated account, decided in Phase 6 (out of scope for Slice 2). Logged here only so the API-key gate in this slice is not mistaken for the daemon's *platform* auth — it is not (D4/D18).

**OQ-3 — RESOLVED (user sign-off 2026-06-13): the startup script owns the final flush.**

D24 fixed the order (drain app → flush Litestream → exit); the owner is now decided. The shell startup script (`scripts/start.sh`) traps SIGTERM and sequences it — signal uvicorn to drain in-flight requests, then run the Litestream final flush, then exit — keeping the Python app unaware of Litestream (Litestream is already a script-managed background process per D12/§11.2). The plan specifies the exact trap/signal mechanics.

---

## ADR references

No new ADR is warranted for this slice. The additive-only cloud strategy is already covered by **ADR-0012** (Slice-1; the "purely additive, DB-as-source-of-truth flip happens later" decision). Every choice here is either locked upstream (D1–D25) or a low-stakes, reversible HOW (which module a handler lives in; store-vs-ignore a stub field). Checking the chosen option against the three ADR gates:

1. **Hard to reverse?** No — moving a handler between `cloud_entry.py` and `api.py`, or adding a `metadata` column later, is cheap.
2. **Surprising without context?** No — mounting REST on the existing web app and reusing `rename`/`delete_by_path` is the obvious, documented path.
3. **Result of a real trade-off?** Mildly (Option A vs B), but the trade-off is small and fully captured above.

Two of three gates are "no," so no ADR. (The genuinely consequential, possibly-surprising item — the vault-root contradiction — is captured as **OQ-1** for user decision, which is the correct vehicle, not an ADR.)

---

## Glossary (new terms this slice introduces)

| Term | Plain meaning |
|------|---------------|
| Cloud entry point | The new container-mode startup that serves the assistant + web endpoints on one port; the laptop "stdio" startup is separate and unchanged |
| `/health` endpoint | An open "still alive?" web address the cloud platform pings; the only path with no secret-key check |
| `/api/upload` | Web endpoint the future daemon calls to send one file's extracted text + details into the database (a storage-only STUB this slice) |
| `/api/event` | Web endpoint the future daemon calls to report a file move/rename or delete so the database stays in sync |
| Content fingerprint | A short hash of file content used to decide new / unchanged / changed without a separate request ID |
| Save-or-update routine | The new database function (`upsert_from_upload`) that stores or refreshes one uploaded file's record |
| Litestream | Background helper that streams the database to cloud storage ~every second and restores it on restart; not a database itself |
| Object Storage | Cloud file store (S3-compatible) that holds the database backup files; cannot run queries |
| Secret-key gate | The check that a sync request carries the right shared `KMS_DAEMON_API_KEY` |
| Dummy vault path | An empty placeholder folder created only to satisfy a startup validation; removed when the config split lands (TD-059) |

---

## Next step

Design doc finalized — OQ-1 + OQ-3 resolved with user sign-off (2026-06-13); OQ-2 parked for Phase 6. Next: `/writing-detailed-specs` to structure Option A into build steps (now including the `core/config.py` `VAULT_ROOT` binding and the startup-script-owned shutdown flush). Architecture-docs fold-in is optional and deferrable.
