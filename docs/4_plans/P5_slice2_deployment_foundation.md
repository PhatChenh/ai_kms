# Plan: P5 Slice 2 — Deployment Foundation (AgentBase)
_Last updated: 2026-06-13_
_Status: [ ] pending_

_Spec: `docs/2_specs/P5_slice2_deployment_foundation.md` (components C2-1…C2-8 — the WHAT)._
_Research: `docs/3_research/P5_slice2_deployment_foundation.md` (assumptions A1–A10 all validated/resolved; Invalidated Assumptions = None)._
_Design: `docs/1_design/P5_slice2_deployment_foundation.md` (Option A; Q1 diagram)._

> This plan owns the HOW: build order, TDD step ordering, exact line numbers, commit boundaries, and the guardrails each phase must keep green. It does NOT restate the spec — open the spec for each component's Build description, request/response contracts, and Done-when wording. Phases below name the C2-* component IDs they implement and the P5-DEPLOY-* behavior IDs they satisfy.

---

## Architecture

This slice is a progressive zoom-out across three diagrams. **Q1** (in the design doc) shows what happens inside the running container. **Q2** (in the spec doc) shows how the container connects to its outside neighbors. **Q3** (below) annotates that same picture with *why* each piece is built the way it is — which existing interface or pattern the build must conform to, and the reason.

### Q1 — What happens inside
See `docs/1_design/P5_slice2_deployment_foundation.md`, block "Q1 Diagram — what happens inside the running container". (Container start → restore/stream/migrate startup → one web server on one port → four paths: health / assistant / upload / events → one shared DB mirrored to cloud → drain-and-flush shutdown.)

### Q2 — How it connects
See `docs/2_specs/P5_slice2_deployment_foundation.md`, block "Q2 Diagram — how it connects to others". (Laptop Daemon + Chat client → Cloud Entry Point on port 8080 [secret-key gate / health / MCP server] → Save-or-update + Document Store → one SQLite DB → Litestream → Object Storage.)

### Q3 — Why build it this way

```
# Cloud Deployment Foundation — Why This Way
Scope: Layers the "why" onto the Q2 connection picture — which EXISTING
       interface or pattern each piece must conform to, and the reason.
       Reuses Q1/Q2 names and positions. Does NOT re-show internal request
       steps (Q1) or the full neighbour map (Q2).

How to read this:
  Center stack      = the container we are building (same boxes as Q2)
  Surrounding boxes = the rule / existing pattern each piece must follow
  Lines             = which rule applies to which piece

  ┌─────────────────────────────┐        ┌─────────────────────────────┐
  │ WHY: inject the dummy notes- │        │ WHY: reuse the assistant     │
  │ folder location into config  │        │ framework's OWN web-app      │
  │ BEFORE the config object is  │        │ builder = one port, one      │
  │ built — the must-exist check │        │ process. No second server.   │
  │ runs at construction time;   │        │ (one address per the design's │
  │ injecting after crashes boot │        │  one-container rule)          │
  └──────────────┬──────────────┘        └───────────────┬─────────────┘
                 │ shapes startup                        │ shapes the whole
                 │ + config load                         │ entry point
                 ▼                                        ▼
  ╔══════════════════════════════════════════════════════════════════════╗
  ║                  CLOUD ENTRY POINT (the container)                     ║
  ║                  One web server, one port (8080)                       ║
  ║                                                                        ║
  ║   ┌─────────────┐   ┌─────────────┐   ┌──────────────────────┐        ║
  ║   │ Secret-key  │   │ Health check│   │ MCP server           │        ║
  ║   │ gate        │   │ (open, no   │   │ (assistant interface,│        ║
  ║   │ guards      │   │  key)       │   │  untouched)          │        ║
  ║   │ /api/* only │   └──────┬──────┘   └──────────┬───────────┘        ║
  ║   └──────┬──────┘          │                     │                    ║
  ║          │ passes valid    │                     │                    ║
  ║          ▼ requests to     │                     │                    ║
  ║   ┌─────────────────┐      │                     │                    ║
  ║   │ Sync Endpoints  │      │                     │                    ║
  ║   │ Upload + Events │      │                     │                    ║
  ║   └────────┬────────┘      │                     │                    ║
  ║            │ store/move/   │                     │                    ║
  ║            ▼ delete via    │                     │                    ║
  ║   ┌─────────────────┐      │                     │                    ║
  ║   │ Save-or-update  │      │                     │                    ║
  ║   │ + Document Store│      │                     │                    ║
  ║   └────────┬────────┘      │                     │                    ║
  ║            │ reads/writes  │                     │                    ║
  ║            ▼               │                     │                    ║
  ║   ┌──────────────────┐     │                     │                    ║
  ║   │ One shared        │    │                     │                    ║
  ║   │ database (SQLite) │    │                     │                    ║
  ║   └────────┬─────────┘     │                     │                    ║
  ╚════════════ │ ═════════════ │ ═════════════════ │ ════════════════════╝
        ▲       │         ▲     │            ▲       │
        │       │         │     │            │       │
  ┌─────┴─────┐ │  ┌──────┴───┐ │     ┌──────┴──────┐│
  │ WHY: Save-│ │  │ WHY: the │ │     │ WHY: the    ││
  │ or-update │ │  │ open path│ │     │ gate is one ││
  │ is a NEW  │ │  │ uses the │ │     │ shared seam ││
  │ data-layer│ │  │ framework│ │     │ scoped to   ││
  │ routine — │ │  │ 's own   │ │     │ /api/* only ││
  │ keeps SQL │ │  │ auth-    │ │     │ — never the ││
  │ + new/skip│ │  │ bypassing│ │     │ health path,││
  │ /overwrite│ │  │ route    │ │     │ never the   ││
  │ OUT of the│ │  │ helper   │ │     │ assistant   ││
  │ web layer │ │  │ (the     │ │     │ path. New   ││
  └───────────┘ │  │ right    │ │     │ sync route =││
                │  │ tool for │ │     │ extend the  ││
  ┌─────────────┴┐ │ a public │ │     │ prefix, not ││
  │ WHY: move +  │ │ no-key   │ │     │ edit handler││
  │ delete REUSE │ │ path)    │ │     └─────────────┘│
  │ the existing │ └──────────┘ │                    │
  │ rename and   │              │      ┌─────────────┴────────────┐
  │ delete-by-   │              │      │ WHY: import the EXISTING  │
  │ path routines│              │      │ assistant object as-is.   │
  │ — they do    │              │      │ Its engine-builder fires  │
  │ atomic multi-│              │      │ per chat session, so the  │
  │ table cleanup│              │      │ assistant path keeps      │
  │ (record +    │              │      │ working unchanged — the   │
  │ both search  │              │      │ container only reads it,  │
  │ indexes) in  │              │      │ never edits it            │
  │ one txn — do │              │      └──────────────────────────┘
  │ not re-build │              │
  └──────────────┘   ┌──────────┴──────────────┐
                     │ WHY: create-if-missing + │
  ┌──────────────────┤ apply-migrations is safe │
  │ shared DB        │ to run AFTER a restore — │
  │ persistence      │ never wipes/duplicates    │
  ▼                  │ restored rows. No new     │
  ┌──────────────┐   │ schema this slice; the    │
  │ Litestream   │   │ columns already exist     │
  │ → Object     │   └───────────────────────────┘
  │ Storage      │
  └──────┬───────┘
         │
         ▼
  ┌─────────────────────────────────────────────────┐
  │ WHY: the startup script OWNS the backup tool and  │
  │ the shutdown order (drain requests → final flush  │
  │ → exit). The app stays unaware of the backup tool │
  └───────────────────────────────────────────────────┘

Reuses Q1/Q2 verbatim: "Secret-key gate (guards /api/* only)", "Health check
(open, no key)", "MCP server (assistant interface, untouched)", "Sync Endpoints
Upload + Events", "Save-or-update + Document Store", "One shared database",
"Litestream → Object Storage". Q3 adds only the WHY callouts; the box skeleton
is identical to Q2.
```

---

## Approach

Build the dependency leaves first, then the assemblers. The data-layer save routine (C2-1) has no dependencies and is testable against a temp DB, so it goes first. The `VAULT_ROOT` config binding (C2-5) comes second because **importing `mcp_server/server.py` evaluates `from core.config import CONFIG` at module scope (`server.py:49`), which fires vault-root validation** — so the cloud entry point (C2-3) crashes at *import* time, before uvicorn ever starts, unless `VAULT_ROOT` is wired first. With the leaves in place, build the web handlers + gate (C2-2), then the entry point that mounts them and orders the DB-init (C2-3 + C2-4), then the packaging (C2-7 → C2-6 → C2-8), then an end-to-end docker verification. Every Python phase is test-first (RED → GREEN). The shell/Docker/shutdown phases are verified by `docker build` / `docker run` / `curl` + a stop-signal observation, not unit tests — they are marked as such.

**One identical image, additive only.** No pipeline change, no schema change (migration 008 columns already exist — C-05 stays green), no second `load_dotenv` (C-11), uvicorn owns the loop (C-10), `tools.py` byte-for-byte unchanged (C-14), the two sync endpoints are plain web routes not MCP tools (C-15).

---

## Phases

### Phase 1 — Save-or-update data routine (`upsert_from_upload`)

**Goal**: A new data-layer function that stores one uploaded file's record and decides — by content fingerprint — whether to insert, skip-identical, or overwrite-changed, so the web handler later carries no SQL and no branching.

**Implements**: spec **C2-1**. (Open the spec C2-1 block for the field list, the three-way idempotency rules, and the Done-when wording.)

**Design** (folder + family it joins):
```
storage/documents.py
  ├─ upsert(outcome: WriteOutcome, ...)        ← existing, UNTOUCHED (takes WriteOutcome)
  ├─ get_by_path(vault_path, db_path)          ← existing (read pattern to follow)
  ├─ delete_by_path(vault_path, db_path)       ← existing (Phase 4 reuses)
  ├─ rename(old, new, db_path)                 ← existing (Phase 4 reuses)
  └─ upsert_from_upload(...) -> Result[int]    ← NEW sibling (this phase)

  Decision inside one get_connection() transaction:
    no existing row for vault_path   → INSERT, return new id
    same content_hash                → no write, return existing id
    different content_hash           → UPDATE in place, return same id
```

**Steps** (TDD — RED first):
1. **RED** — Write `tests/test_storage/test_upsert_from_upload.py` covering the three Done-when cases from spec C2-1, each against a temp DB (`db_path=tmp_path / "kb.db"`, call `init_db(db_path)` first):
   - new path → one `documents` row with `full_body` == uploaded text, plus `original_filename`, `file_size_bytes`, `content_hash` set; returns an int id (P5-DEPLOY-03).
   - same path + same `content_hash` → exactly one row, unchanged, same id returned (P5-DEPLOY-04).
   - same path + different `content_hash` → exactly one row, now new text + new hash (P5-DEPLOY-05).
   - Do NOT import `CONFIG` at module scope (C-17 — hook-enforced); pass `db_path` explicitly.
2. **GREEN** — Add `upsert_from_upload(...)` in `storage/documents.py` as a sibling to `upsert` (after `documents.py:100`). Accept upload fields directly (vault_path, extracted_text→`full_body`, content_hash, original_filename, file_size_bytes). Per the C2-1 decision (lean A): the `metadata` blob is NOT a parameter — discard happens at the handler boundary (Phase 2). Inside ONE `get_connection(db_path)` block: read the existing row (inline `SELECT vault_path, content_hash, id ...` or follow the `get_by_path` pattern), then branch insert/skip/update. Return `Result[int]` (the row id). Writes only existing columns — no `ALTER TABLE`, no `CREATE TABLE`.
3. **VERIFY** — `uv run pytest tests/test_storage/test_upsert_from_upload.py` green; `uv run ruff check .` clean.
4. **COMMIT** — `feat(storage): add upsert_from_upload save-or-update routine (C2-1)`.

**Files to modify**:
- `src/storage/documents.py` — add `upsert_from_upload`; do NOT touch `upsert`/`rename`/`delete_by_path`.
- `tests/test_storage/test_upsert_from_upload.py` — new test file.

**Guardrails to keep green**:
- **C-04** — connection MUST come from `storage/db.py::get_connection()` (`db.py:76`), never raw `sqlite3.connect()`.
- **C-05** — INSERT/UPDATE only; the discarded `metadata` field must NOT trigger a migration.
- **C-12** — returns `Result[int]` (matches the storage family).
- **C-13** — no `provider.complete()`, no audit (no AI decision).
- **C-17** — no module-scope `CONFIG` import in the test.

**Done when**: P5-DEPLOY-03, P5-DEPLOY-04, P5-DEPLOY-05 pass against a temp DB (per spec C2-1 Done-when).

**Status**: [ ] pending

---

### Phase 2 — `VAULT_ROOT` config binding (throwaway, TD-059)

**Goal**: Let the container satisfy the required vault-root config field with an env var, so the system boots in a container that has no notes folder — without a cloud-specific config file. **This phase precedes any phase that imports `server.py`, because that import triggers vault-root validation.**

**Implements**: spec **C2-5**. (Open the spec C2-5 block for the corrected mechanism rationale and Done-when.)

**Design** (BEFORE / AFTER of the config load order — the load-bearing trap):
```
load_config() in core/config.py  (verified line numbers)

  :582   raw_main = _load_yaml("config.yaml")     ← mutable dict
  :589   keys = ApiKeys()                          ← VAULT_ROOT read HERE
  :590   cfg = Config(
  :591       main=MainConfig(**raw_main),          ← construction-time
  ...        ...                                       validator fires HERE
  :595   )
  :596   if keys.kms_db_path is not None:          ← DB-path applied HERE
  :597       cfg.main.database.path = keys.kms_db_path   (post-construction)

  ⛔ WRONG (re-introduces the A9 crash): mirror the DB-path block —
     assign cfg.main.vault.root after :595. VaultConfig has NO
     validate_assignment AND the existence check (validate_vault_root_exists,
     :372-382) already ran at :591 against the YAML value → crash before
     the assignment is reached.

  ✅ RIGHT: inject into the RAW dict between :589 and :590:
     if keys.vault_root is not None:
         raw_main["vault"]["root"] = str(keys.vault_root)
     ... then MainConfig(**raw_main) validates the injected /data/vault.
```

**Steps** (TDD — RED first):
1. **RED** — Write `tests/test_core/test_vault_root_env.py` asserting BOTH directions (do NOT import `CONFIG` at module scope — lazy-import `load_config` inside the test, point at temp dirs):
   - `VAULT_ROOT` set to a temp dir that exists + `env: prod`/`dev` → `load_config()` returns `cfg.main.vault.root == <temp dir>` and does NOT raise the vault-root existence error (P5-DEPLOY-10 boot path).
   - `VAULT_ROOT` unset → the YAML `vault.root` value is used unchanged (proves the local/stdio path is unaffected).
   - Use `monkeypatch.setenv("VAULT_ROOT", ...)` / `monkeypatch.delenv(..., raising=False)`. Set `env` to `prod` or `dev` in the test config so `select_vault_by_env` (`config.py:356-370`) does not redirect the root to the testing path (env wrinkle from research).
2. **GREEN** — Two edits to `src/core/config.py`, nothing else (C-CONFIG: this is the ONLY change to config behavior):
   - Add to `ApiKeys` (sibling of `kms_db_path` at `config.py:522`): `vault_root: Path | None = Field(default=None, alias="VAULT_ROOT")`.
   - Inject into the raw dict **between `keys = ApiKeys()` (`:589`) and the `Config(...)` constructor (`:590`)** — NOT at the post-construction DB-path block (`:596-597`):
     ```python
     # COUPLING / TD-059: throwaway cloud bridge — removed at config split (Phases 6/7/9).
     # MUST be pre-construction: validate_vault_root_exists (config.py:372-382) runs at
     # MainConfig construction and VaultConfig has no validate_assignment, so a
     # post-construction assign would crash. Do NOT move this to the KMS_DB_PATH block.
     if keys.vault_root is not None:
         raw_main["vault"]["root"] = str(keys.vault_root)
     ```
3. **VERIFY** — `uv run pytest tests/test_core/test_vault_root_env.py` green; run `uv run pytest -m "not smoke"` to confirm no existing config test regressed; `uv run ruff check .` clean.
4. **COMMIT** — `feat(config): add throwaway VAULT_ROOT env binding (C2-5, TD-059)`.

**Files to modify**:
- `src/core/config.py` — one `ApiKeys` field + one pre-construction injection block (with the TD-059 + placement-warning comment).
- `tests/test_core/test_vault_root_env.py` — new test file.

**Guardrails to keep green**:
- **C-CONFIG** — `VAULT_ROOT` binding is the ONLY change to config loading; no other behavior change.
- **A9 placement guard** — injection MUST sit before the `Config(...)` call at `config.py:590`, NEVER at `:596-597`.
- **env wrinkle** — test uses `env: prod`/`dev` so `select_vault_by_env` does not override the injected root.
- **C-17** — no module-scope `CONFIG` import in the test.

**Done when**: with `VAULT_ROOT=/data/vault` set and the dir present, `load_config()` returns that root with no existence error; with it unset, YAML is used unchanged; both directions asserted (spec C2-5 Done-when).

**Status**: [ ] pending

---

### Phase 3 — REST handlers + secret-key gate + health route (`mcp_server/api.py`)

**Goal**: A small, testable module holding the two business endpoints, the open health check, and a single shared secret-key gate that protects exactly the two sync endpoints — with all branching here, never in `tools.py`.

**Implements**: spec **C2-2**. (Open the spec C2-2 block + the "Request / response contracts" section for the exact JSON shapes and the 200/401/not_found rules.)

**Design** (request → handler → data layer → response):
```
POST /api/upload  (Bearer key)  ─► upload_handler
     parse body → drop `metadata` → upsert_from_upload(fields)  [Phase 1]
        → Result[int]  →  200 {"status":"ok","document_id": <id>}

POST /api/event   (Bearer key)  ─► event_handler
     type == "moved"   → rename(old_path, new_path)      [existing :316]
     type == "deleted" → delete_by_path(path)            [existing :213]
        rowcount 0 → 200 {"status":"not_found"}   (NOT an error)
        else       → 200 {"status":"ok"}

GET  /health      (no key)      ─► 200 {"status":"ok"}

Secret-key gate (single shared seam, scoped to /api/* ONLY):
     Authorization: Bearer <KMS_DAEMON_API_KEY>
     missing/wrong → 401, no DB change
     /health and the MCP path are NEVER gated
```

**Steps** (TDD — RED first):
1. **RED** — Write `tests/test_mcp_server/test_api.py`. Test the handler callables and the gate in-process against a temp DB (Starlette `TestClient`, or call the handlers with a fabricated `Request`; pass the temp `db_path` through). Cover:
   - upload with valid key + new path → 200 with a `document_id` (P5-DEPLOY-03 at the HTTP boundary).
   - event `moved` → `vault_path` updated, search-index entries carried (assert via `get_by_path` / a search-table read) (P5-DEPLOY-06).
   - event `deleted` → row + index entries gone in one transaction (P5-DEPLOY-07).
   - event for an unknown path → `{"status":"not_found"}` at **HTTP 200** (P5-DEPLOY-08).
   - missing/wrong key on `/api/upload` and `/api/event` → 401, DB unchanged (P5-DEPLOY-09).
   - `/health` with no key → 200 (P5-DEPLOY-01).
   - Decision (spec C2-2): implement the gate as **Starlette path-prefix middleware keyed on `request.url.path.startswith("/api/")`** (Option A); the test must additionally assert the gate does NOT fire on `/health`. If the middleware turns out to shadow `/health` or the MCP path, fall back to the per-handler shared check (Option B) — note which was used.
2. **GREEN** — Create `src/mcp_server/api.py` with: `upload_handler`, `event_handler`, the `/health` handler, and a gate factory (middleware class or a shared `require_key(request)` helper) reading `KMS_DAEMON_API_KEY` via `os.environ.get`. Handlers import `upsert_from_upload`, `rename`, `delete_by_path` from `storage/documents.py`. The `metadata` field is parsed-and-discarded at the handler (per C2-1 decision A). All branching lives here — NONE in `tools.py`.
3. **VERIFY** — `uv run pytest tests/test_mcp_server/test_api.py` green; `git diff --stat src/mcp_server/tools.py` shows ZERO changes (C-14); `uv run ruff check .` clean.
4. **COMMIT** — `feat(mcp): add REST handlers + secret-key gate + health route (C2-2)`.

**Files to modify**:
- `src/mcp_server/api.py` — new module (handlers + gate + health).
- `tests/test_mcp_server/test_api.py` — new test file.
- (NOT `tools.py` — must stay byte-for-byte unchanged.)

**Guardrails to keep green**:
- **C-14** — no branching in `tools.py`; it stays byte-for-byte unchanged (hook hard-block).
- **C-15** — endpoints are plain web routes, NOT `mcp.tool()` registrations.
- **C-13** — no LLM call sneaks into handlers; no audit.
- **C-12** — handlers translate `upsert_from_upload`/`rename`/`delete_by_path` `Result` into HTTP; the data-layer functions keep their `Result` contract.
- **A6 / A10** — gate covers exactly `/api/*`; rowcount-0 → `not_found` at HTTP 200.

**Done when**: P5-DEPLOY-03 (HTTP), -06, -07, -08, -09, -01 pass in-process (spec C2-2 Done-when).

**Status**: [ ] pending

---

### Phase 4 — Cloud entry point + startup DB ordering (`mcp_server/cloud_entry.py`)

**Goal**: The single container-mode startup module that turns "container start" into "serving the assistant interface + the three new web addresses on one port", with the DB ensured-ready before serving — without touching the laptop (stdio) startup.

**Implements**: spec **C2-3** (entry point) + **C2-4** (startup DB ordering inside it). (Open both spec blocks for the mount + ordering Done-when.)

**Design** (startup order inside the module):
```
import mcp from mcp_server.server   ← registers 5 tools + load_dotenv (C-11)
                                       ⚠️ this import triggers CONFIG validation
                                          → Phase 2 (VAULT_ROOT) MUST be done first
        │
        ▼
init_db()        ← C2-4: AFTER restore (script, Phase 6), BEFORE serve.
                   idempotent: safe no-op on a restored DB, builds fresh if none.
                   DB path = /data/kb.db via KMS_DB_PATH (no code change).
        │
        ▼
app = mcp.streamable_http_app()    ← framework's Starlette web app (A1: carries
                                      the lifespan; engine builds per chat session)
        │
        ▼
attach /api/upload, /api/event (Phase 3 handlers) → app.routes.append(...)   [A2]
attach /health via mcp.custom_route  (auth-bypassing helper — but register it
                                      BEFORE streamable_http_app() if using
                                      custom_route; see research note)
wrap /api/* with the Phase 3 secret-key gate (and ONLY /api/*)
        │
        ▼
uvicorn.run(app, host="0.0.0.0", port=8080)   ← uvicorn owns the loop (C-10)
                                                 NO nested asyncio.run()
        │
        ▼
if __name__ == "__main__":  → python -m mcp_server.cloud_entry
```

**Steps** (TDD — RED first):
1. **RED** — Write `tests/test_mcp_server/test_cloud_entry.py`:
   - **C2-4 ordering / idempotency** (in-process, temp DB via `KMS_DB_PATH`): first call with no DB → fresh DB at the data path, latest `schema_version`, `documents` has `full_body`, `knowledge_entries` exists (P5-DEPLOY-10). Second call on the populated DB → prior rows intact, no wipe/duplicate (P5-DEPLOY-11). Factor the "ensure DB + build app + mount" into a testable `build_app()`-style function so uvicorn is NOT started in the unit test.
   - **A1 lifespan** (the top research item): start the built app in-process (Starlette `TestClient` or `httpx.ASGITransport`) and issue a **real MCP tool-list/session request against `/mcp`** — assert the five tools come back. A `/health` curl alone does NOT prove this (the lifespan fires per MCP session, not at boot) — research nuance on A1. Assert `/health` returns 200 with no key on the mounted app (P5-DEPLOY-01) and the tool-list answers on the same app (P5-DEPLOY-02).
   - **P5-DEPLOY-12** — assert importing `mcp_server.cloud_entry` does NOT alter or break `mcp_server.server.main` (stdio path untouched).
2. **GREEN** — Create `src/mcp_server/cloud_entry.py`: import `mcp` from `mcp_server.server` (do NOT call `load_dotenv` again — C-11), call `init_db()` (C2-4), build the app via `mcp.streamable_http_app()`, append the Phase 3 routes, wrap `/api/*` with the gate, expose a `build_app()` helper for tests, and a `__main__` guard that runs `uvicorn.run(...)` on `0.0.0.0:8080`. Decision (spec C2-3): mount business routes via `app.routes.append(...)` (Option A, A2-confirmed) — NOT a sub-mount; `/health` via `mcp.custom_route` registered before `streamable_http_app()` (research: post-build `custom_route` is a silent no-op).
3. **VERIFY** — `uv run pytest tests/test_mcp_server/test_cloud_entry.py` green; `uv run python -m mcp_server.cloud_entry` boots locally (Ctrl-C to stop) and `curl localhost:8080/health` → 200; `uv run ruff check .` clean.
4. **COMMIT** — `feat(mcp): add cloud_entry with DB-init ordering + route mount (C2-3, C2-4)`.

**Files to modify**:
- `src/mcp_server/cloud_entry.py` — new module.
- `tests/test_mcp_server/test_cloud_entry.py` — new test file.
- `src/mcp_server/server.py` — at most a one-line comment confirming `mcp` is a public export (likely zero change).

**Guardrails to keep green**:
- **C-10** — uvicorn owns the loop; NO nested `asyncio.run()`.
- **C-11** — `cloud_entry.py` does NOT call `load_dotenv` (it happens via the `server.py` import at `server.py:27`).
- **A1** — verification issues a real `/mcp` tool-list request, not just `/health`.
- **A7↔A9** — depends on Phase 2 being done (import fires CONFIG validation).
- **C-15** — does not register any new MCP tool.

**Done when**: the module serves on port 8080 — assistant tool-list (P5-DEPLOY-02), open `/health` (P5-DEPLOY-01), the two guarded sync endpoints per Phase 3; stdio entry still works (P5-DEPLOY-12); DB ordering is idempotent (P5-DEPLOY-10/-11) (spec C2-3 + C2-4 Done-when).

**Status**: [ ] pending

---

### Phase 5 — Explicit `uvicorn` dependency (`pyproject.toml`)

**Goal**: Make the cloud entry point's direct dependency on uvicorn explicit so a future framework version cannot silently break the build.

**Implements**: spec **C2-7**. (Open the spec C2-7 block.)

**Design**:
```
pyproject.toml [project] dependencies
   mcp>=1.27,<2          ← already present (pulls uvicorn 0.49.0 transitively today)
 + uvicorn               ← ADD explicitly (cloud_entry.py imports it directly — A5/R6)
 (starlette only if api.py/cloud_entry.py import it directly)
 [project.scripts]
   kms = "cli.main:cli"  ← UNTOUCHED. Cloud launches via python -m mcp_server.cloud_entry.
```

**Steps**:
1. Add `uvicorn` to the `dependencies` array in `pyproject.toml` (and `starlette` only if Phase 3/4 import it directly — check the actual imports before adding). Leave `kms = "cli.main:cli"` untouched; no new console entry needed.
2. **VERIFY** — `uv sync` resolves cleanly with `uvicorn` listed explicitly; `uv run python -c "import uvicorn"` succeeds; `uv run python -m mcp_server.cloud_entry` still boots.
3. **COMMIT** — `build(deps): list uvicorn explicitly for cloud entry (C2-7)`.

**Files to modify**:
- `pyproject.toml` — add `uvicorn` (and maybe `starlette`) to `dependencies`.

**Guardrails to keep green**:
- Existing `kms` entry point untouched.

**Done when**: `uv sync` resolves `uvicorn` explicitly and `python -m mcp_server.cloud_entry` starts (spec C2-7 Done-when, supporting P5-DEPLOY-01/-02).

**Status**: [ ] pending

---

### Phase 6 — Startup script (`scripts/start.sh`) — restore / replicate / launch / shutdown-flush

**Goal**: The wrapper the container runs: it drives Litestream restore-on-boot and final-flush-on-shutdown around the app, keeping the Python app unaware of Litestream.

**Implements**: spec **C2-6**. (Open the spec C2-6 block for the five-step sequence and the locked shutdown order D24/OQ-3.)

> **Verification type: NOT a unit test.** This is a shell orchestrator verified by `docker run` + `curl` + a stop-signal observation (Phase 8). There is no Python unit test for `start.sh`.

**Design** (script lifecycle):
```
scripts/start.sh
 1. if LITESTREAM_* env vars set → litestream restore /data/kb.db   (best-effort)
 2. if restore fails (no backup) or vars unset → skip (app creates fresh DB via C2-4)
 3. if vars set → start litestream replicate in background
 4. launch app:  python -m mcp_server.cloud_entry   (Phase 4)
 5. trap SIGTERM → forward SIGTERM to uvicorn child → wait (drain in-flight) →
                   litestream final flush → exit          (D24 / OQ-3 order)
```

**Steps**:
1. Write `scripts/start.sh` implementing the five steps. Decision (spec C2-6): trap SIGTERM in the script, send it to the uvicorn process, `wait` for it to drain, then run the Litestream final flush, then exit (the leaning mechanic — verify the trap/drain/flush sequence at Phase 8).
2. Make it executable (`chmod +x`).
3. **VERIFY** — deferred to Phase 8 (`docker run` + stop signal). Local sanity: `bash -n scripts/start.sh` (syntax check) passes.
4. **COMMIT** — `feat(deploy): add start.sh restore/replicate/launch/flush wrapper (C2-6)`.

**Files to modify**:
- `scripts/start.sh` — new file.

**Guardrails to keep green**:
- App stays Litestream-unaware (orchestration in the shell only).
- Shutdown order: drain → flush → exit (D24).
- **A4** runtime check (Litestream vs `wal_autocheckpoint=100`) is a DEPLOY-TIME manual verification (Phase 8 / Open Questions), NOT a code task here.

**Done when**: verified in Phase 8 — `docker run` starts and `/health` → 200; a stop signal drains then flushes then exits in that order (spec C2-6 Done-when, P5-DEPLOY-01/-10/-11).

**Status**: [ ] pending

---

### Phase 7 — Dockerfile + `litestream.yml` template

**Goal**: Package the system as a runnable container image for the cloud target, including the Litestream binary, the `/data` directory, the dummy vault directory, and a Litestream config driven entirely by env vars.

**Implements**: spec **C2-8**. (Open the spec C2-8 block + the "Litestream config template" section.)

> **Verification type: NOT a unit test.** Verified by `docker build` + `docker run` + `curl` (Phase 8).

**Design** (image layout):
```
Dockerfile  (platform linux/amd64, base python:3.12-slim)
  ├─ install src/ deps (uv sync)         ← uvicorn now explicit (Phase 5)
  ├─ install the Litestream binary
  ├─ mkdir -p /data /data/vault          ← /data/vault satisfies VAULT_ROOT check (TD-059)
  ├─ EXPOSE 8080
  └─ ENTRYPOINT = scripts/start.sh       ← Phase 6

litestream.yml  (template, env-driven — NO secrets baked in, D11)
  dbs: - path: /data/kb.db
         replicas: - type: s3
                     bucket/endpoint/keys ← ${LITESTREAM_*} env vars

per-tester env-file also sets:
  KMS_DB_PATH=/data/kb.db   VAULT_ROOT=/data/vault   KMS_DAEMON_API_KEY=...
  env: prod  (or dev) — so select_vault_by_env does NOT redirect the root (env wrinkle)
```

**Steps**:
1. Write the `Dockerfile`: `--platform linux/amd64`, base `python:3.12-slim`; install deps; install the Litestream binary; `mkdir -p /data /data/vault`; `EXPOSE 8080`; entry = `scripts/start.sh`.
2. Write `litestream.yml` exactly as the spec's template (env-var-driven S3 target, no baked secrets).
3. Document the per-tester env-file keys (`KMS_DB_PATH`, `VAULT_ROOT`, `KMS_DAEMON_API_KEY`, `LITESTREAM_*`) and note that `env` must be `prod`/`dev` (env wrinkle — otherwise `select_vault_by_env` redirects the vault root and the `VAULT_ROOT` injection is bypassed).
4. **VERIFY** — deferred to Phase 8.
5. **COMMIT** — `feat(deploy): add Dockerfile + litestream.yml template (C2-8)`.

**Files to modify**:
- `Dockerfile` — new file.
- `litestream.yml` — new file.

**Guardrails to keep green**:
- **C-05** — no schema work; columns already exist via migration 008.
- **D11** — no secrets baked into the image; all from env.
- **env wrinkle** — env-file sets `env: prod`/`dev` so `VAULT_ROOT` injection is honored.
- **A9** — `mkdir -p /data/vault` makes the injected `VAULT_ROOT=/data/vault` path exist before config loads.

**Done when**: verified in Phase 8 (`docker build` + `docker run`) (spec C2-8 Done-when).

**Status**: [ ] pending

---

### Phase 8 — End-to-end container verification + deploy-time runtime checks

**Goal**: Prove the whole slice works as one container on one port, and run the runtime-only checks that cannot be unit-tested.

**Implements**: end-to-end acceptance for **C2-6 + C2-8** (and the cross-cutting behaviors of C2-1…C2-5 inside the container).

> **Verification type: NOT a unit test.** `docker build` / `docker run` / `curl` + a stop-signal observation.

**Steps** (manual / scripted verification — no RED/GREEN):
1. `docker build --platform linux/amd64 .` completes without error (P5-DEPLOY-01).
2. `docker run -p 8080:8080 <image>` starts; `curl localhost:8080/health` → 200 with no key (P5-DEPLOY-01).
3. Issue a real MCP tool-list request against `/mcp` on the running container → five tools answer on the same port (P5-DEPLOY-02). (Not just `/health` — A1 nuance.)
4. `curl` the two sync endpoints with a valid `Authorization: Bearer <KMS_DAEMON_API_KEY>` → upload stores + returns id (P5-DEPLOY-03), move/delete behave (P5-DEPLOY-06/-07), unknown path → `not_found` at 200 (P5-DEPLOY-08); wrong/missing key → 401 (P5-DEPLOY-09).
5. With no backup configured → first start creates a fresh `/data/kb.db`, fully-migrated (P5-DEPLOY-10).
6. With `LITESTREAM_*` configured against a test bucket → write a row, stop the container, restart → prior row survives the restore (P5-DEPLOY-11).
7. Send the container a stop signal → observe the order in logs: uvicorn drains in-flight requests → Litestream final flush → exit (C2-6 shutdown order).
8. **A4 runtime check (deploy-time, manual)** — confirm against Litestream's own docs/behavior that `wal_autocheckpoint=100` (`db.py:18-19`) does not fight Litestream's checkpoint posture. Research could not determine this from code; it is an external-tool runtime property. Record the finding in the Open Questions / TECH_DEBT if a posture change is needed (do NOT change the pragma speculatively).

**Files to modify**: none (verification only); may add a short `docs/` runbook note if useful (not required).

**Guardrails to keep green**: all of C-04, C-05, C-10, C-11, C-13, C-14, C-15 already proven by Phases 1–4; this phase confirms them end-to-end in the image.

**Done when**: P5-DEPLOY-01, -02, -03, -06, -07, -08, -09, -10, -11 all pass against the running container; the shutdown order is observed; the A4 runtime check is recorded.

**Status**: [ ] pending

---

## Open Questions

- **A4 — Litestream vs `wal_autocheckpoint=100` (deploy-time, non-blocking).** Verified in-repo that WAL + `wal_autocheckpoint=100` are set (`db.py:18-19`) and WAL is Litestream's prerequisite. Whether `=100` specifically conflicts with Litestream's recommended checkpoint posture is an external-tool runtime property — confirm against Litestream docs during Phase 8. Litestream's own guidance is typically "let Litestream manage checkpoints; a small autocheckpoint is tolerated." If a posture change is needed, record it; do not change the pragma speculatively.
- **OQ-2 (parked, Phase 6/daemon).** Whether the future daemon uses the container's AgentBase IAM account or its own. Out of scope here; logged so the `/api/*` secret-key gate is not mistaken for the daemon's platform auth.

## Out of Scope

(Per the spec's "Out of scope" — not restated in full.) Highlights: actual AgentBase deployment (manual ops), summarize/classify/index/audit inside `/api/upload` (Phase 7 capture refactor), persisting the `metadata` field (no new column this slice — C-05), the laptop daemon itself (Phase 6), the config split that removes the throwaway `VAULT_ROOT` bridge (Phases 6/7/9, TD-059), PostgreSQL, multi-tenancy, and any cloud→daemon command endpoints (dead per the rearchitecture doc).
