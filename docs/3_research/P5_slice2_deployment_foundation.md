# Research: P5 Slice 2 — Deployment Foundation (AgentBase)
_Last updated: 2026-06-13_

## Overview

This slice lets the existing knowledge system boot inside a cloud container: one web server on one port that answers the existing chat-assistant (MCP) interface, plus two new "dumb" file-sync endpoints (upload + move/delete) behind a shared secret key, plus an open health check, with the database streamed to cloud storage so it survives restarts. Nothing about how the system summarizes or classifies changes — the new endpoints only store records and reuse cleanup logic that already exists.

This research verified the spec's 10 assumptions (A1–A10) and the eight component claims (C2-1…C2-8) against the actual installed `mcp` package source, the real `storage/documents.py` / `storage/db.py` bodies, and the real `core/config.py` loader. The top-priority assumption A1 (does the MCP lifespan still run when we host the app under our own uvicorn) **VALIDATED** with one important nuance about *when* it runs.

**Re-check pass (2026-06-13):** A9 — the only previously-invalidated assumption — was patched in the spec with a corrected mechanism (inject `VAULT_ROOT` into the raw config dict before `MainConfig(**raw_main)` is constructed) and is now **RESOLVED**. The corrected mechanism verifies against the actual `load_config()` body: a pre-construction injection window exists, writing the raw `vault.root` slot satisfies the construction-time existence validator, and the `ApiKeys` env-alias approach reads `VAULT_ROOT` the same way `KMS_DB_PATH` is read and is available before `MainConfig` is built. No new invalidation was introduced. **All ten assumptions are now validated or resolved; the one remaining ⚠️ item (A4, Litestream `wal_autocheckpoint` interaction) is an external-tool deploy-time check, not a code claim. Planning is unblocked.**

Because this is a stub-deployment slice with no pipeline coupling, the storage and MCP-import assumptions all held exactly as the spec claimed. The single config-loading ordering trap the spec originally flagged (A9) has now been corrected in the spec and confirmed against code — resolvable entirely from code without any redesign of a public contract.

---

## Key Components

These are the existing pieces the slice reuses, and where the new pieces will attach.

- **The MCP application object** (`src/mcp_server/server.py:123`) — `mcp = FastMCP("kms", lifespan=_lifespan)`, built at module scope with the five tools registered at import. The cloud entry point imports this and asks it for its web app.
- **The MCP lifespan** (`src/mcp_server/server.py:101`, `_lifespan`) — builds the Context Injection Engine the tools read via `ctx.request_context.lifespan_context["engine"]`. Yields `{"engine": ContextInjectionEngine()}`.
- **The framework web-app builder** (`mcp` package, `mcp/server/fastmcp/server.py:950`, `streamable_http_app()`) — returns a Starlette app for the MCP protocol. This is the app the slice mounts routes onto.
- **The public route decorator** (`mcp/server/fastmcp/server.py:705`, `custom_route()`) — appends auth-bypassing routes; documented for health checks.
- **The storage family** (`src/storage/documents.py`) — `upsert` (`:100`), `get_by_path` (`:156`), `delete_by_path` (`:213`), `rename` (`:316`). The new `upsert_from_upload` joins this family.
- **The connection + schema layer** (`src/storage/db.py`) — `get_connection` (`:76`), `init_db` (`:49`), WAL pragmas (`:18-19`).
- **The config loader** (`src/core/config.py`) — `VaultConfig` (`:77`), `ApiKeys.kms_db_path` (`:522`), `load_config()` DB-path application (`:596-597`), and the vault-root existence check (`MainConfig.validate_vault_root_exists`, `:372-382`).

New files to create (all confirmed absent today): `mcp_server/cloud_entry.py`, `mcp_server/api.py`, `Dockerfile`, `scripts/start.sh`, `litestream.yml`, `storage/documents.py::upsert_from_upload`, the `core/config.py` `VAULT_ROOT` binding.

---

## How It Works

What actually happens when a request arrives, traced through the real library code.

**The MCP lifespan (A1) — the load-bearing flow.** When uvicorn serves the app returned by `streamable_http_app()`, Starlette runs that app's lifespan. But that app's lifespan is **not** the user's `_lifespan` — it is `lambda app: self.session_manager.run()` (`mcp/server/fastmcp/server.py:1044`). The user's `_lifespan` is wired one layer deeper: it is wrapped by `lifespan_wrapper(self, self.settings.lifespan)` and attached to the **lowlevel MCP server** (`fastmcp/server.py:212`), not to the Starlette app. The chain that finally runs it:

1. Starlette enters the app lifespan → `session_manager.run()` (`mcp/server/streamable_http_manager.py:102`) sets up its task group.
2. When an MCP client connects, the session manager calls `self.app.run(...)` per request/session (`streamable_http_manager.py:197` stateless, `:299` stateful), where `self.app` is the lowlevel `_mcp_server`.
3. The lowlevel `Server.run()` enters the user lifespan via `stack.enter_async_context(self.lifespan(self))` (`mcp/server/lowlevel/server.py:657`).

So the engine **is** built — but **per MCP session**, when a client first talks to the MCP path, not at uvicorn process startup. That is exactly where the tools need it (they read `lifespan_context` during a tool call). `_lifespan`'s own docstring agrees: "entered exactly once per `Server.run()` call" (`server.py:106`). Conclusion: hosting `streamable_http_app()` under our own `uvicorn.run(...)` keeps the MCP interface fully working.

**Adding routes (A2).** `streamable_http_app()` merges `self._custom_starlette_routes` into the route list at build time (`fastmcp/server.py:1038`) and returns a plain `Starlette(routes=...)`. Starlette stores routes as a mutable list (`starlette/routing.py:578`) and iterates it at request time (`routing.py:674`). So routes can be added two supported ways: (a) `mcp.custom_route(path, methods)` **before** calling `streamable_http_app()`, or (b) after building the app, mutate the route list — `app.routes.append(...)` (the `.routes` property returns the live underlying list) or `app.add_route(...)` / `app.mount(...)` (`starlette/applications.py:110-118, 92`). Both are seen at request time.

**Upload + events.** The upload handler hands fields to `upsert_from_upload` (new); events reuse `rename` (move) and `delete_by_path` (delete). Both existing functions run in one `get_connection` transaction and return `Result[int]` rowcount; rowcount 0 → `not_found` at HTTP 200.

**Startup DB ordering.** The script restores the DB (if a backup exists), then `init_db()` runs `schema.sql` (all `CREATE TABLE IF NOT EXISTS`) and version-gated migrations — a safe no-op on a populated, already-migrated DB.

```
# How the MCP lifespan reaches our engine under self-hosted uvicorn
# Plain English: our engine builder is NOT on the web app — it's two layers
# down, fired when a chat client first connects to the assistant path.

  uvicorn runs the Starlette app
            │
            ▼
  Starlette enters the app's own lifespan
  = the session manager's run()  ── NOT our engine yet
            │
            ▼
  A chat client connects to the assistant path
            │
            ▼
  Session manager calls the lowlevel server's run() for that session
            │
            ▼
  Lowlevel server enters OUR lifespan  ──►  builds the Context Engine
            │                                (tools read it during the call)
            ▼
  Tools answer using the engine
```

---

## Spec Verification

Plain-English summary: every storage and MCP-import assumption held exactly as written. The MCP lifespan (A1) works under self-hosted uvicorn — it just fires per chat session rather than at process boot, which is correct. The one broken assumption is A9: the proposed `VAULT_ROOT` override, copied from the DB-path pattern, will not actually satisfy the vault-root check because that check runs at a different point in the loader than the DB-path one.

| Assumption ID | Spec Claim | Verdict | Evidence |
|--------------|-----------|---------|----------|
| A1 (R1, top) | Self-hosted uvicorn on `streamable_http_app()` still enters FastMCP's `_lifespan`, so the Context Injection Engine initializes in cloud mode. | ✅ Validated | `mcp/server/fastmcp/server.py:1044, 212` + `mcp/server/streamable_http_manager.py:197/299` + `mcp/server/lowlevel/server.py:657` — user lifespan is on the lowlevel server, entered per MCP session via `session_manager.run()→app.run()→enter_async_context(self.lifespan(self))`. Runs per-session, not at uvicorn boot, but does run. |
| A2 (R1 cont.) | Appending `Route` to the app's `.routes`, OR `mcp.custom_route`, is a supported way to add routes. | ✅ Validated | `fastmcp/server.py:1038` (custom routes merged at build), `:705` (custom_route decorator), `starlette/routing.py:578/674` (routes are a live list iterated at request time), `starlette/applications.py:80-81/110-118` (`.routes` property + `add_route`). |
| A3 (R3) | `init_db()` on a restored, populated DB is a safe no-op (CREATE IF NOT EXISTS + version-gated migrations). | ✅ Validated | `storage/db.py:49-61` runs `schema.sql` then `_run_migrations`; `db.py:35-43` only applies files where `file_version > version`; `schema.sql` all 4 `CREATE TABLE IF NOT EXISTS` + `INSERT OR IGNORE` version seed (`schema.sql:44-48`). |
| A4 (R5) | WAL + `wal_autocheckpoint=100` does not conflict with Litestream. | ⚠️ Unverifiable (no in-repo conflict) | `storage/db.py:18-19` confirms `journal_mode=WAL` + `wal_autocheckpoint=100` set on every connection. WAL is present (Litestream's prerequisite). Whether `=100` specifically fights Litestream's checkpoint posture is an external-tool runtime property, not determinable from this repo. See Open Questions. |
| A5 (R6) | `uvicorn`/`starlette` available transitively via `mcp` today; not direct deps. | ✅ Validated | `pyproject.toml:7-34` lists only `mcp>=1.27,<2` (no uvicorn/starlette). Installed: uvicorn 0.49.0, starlette 1.3.0 (both transitive). C2-7's explicit-add is correct. |
| A6 (R7) | A path-prefix gate can cover exactly `/api/*` without shadowing `/health` or the MCP path. | ✅ Validated | MCP path default `/mcp` (`fastmcp/server.py:166`, not overridden by `FastMCP("kms", ...)`). `/api/`, `/health`, `/mcp` are non-overlapping prefixes; Starlette middleware/handler can branch on `request.url.path.startswith("/api/")`. Both spec options (middleware / per-handler) scope cleanly. |
| A7 | `mcp` importable at module scope with five tools registered; importing `server.py` does not start serving. | ✅ Validated | `mcp_server/server.py:123` (`mcp` at module scope), `:128` (`register_tools(mcp)` at import), `:167-173` (`main()`/`mcp.run()` only under `if __name__=="__main__"`). **Caveat:** import triggers `CONFIG` at `server.py:49` → fires vault-root validation → couples A7 to A9 (see below). |
| A8 | Migration 008 columns `full_body`, `original_filename`, `file_size_bytes` exist on `documents` after `init_db`. | ✅ Validated | `storage/migrations/008_knowledge_entries_and_document_columns.sql:19-21` (three `ALTER TABLE documents ADD COLUMN`), mirrored in `DocumentRow` fields (`documents.py:45-47`). No new migration needed (C-05 holds). |
| A9 (**CORRECTED → re-checked**) | Inject `VAULT_ROOT` into the raw config dict **before** `MainConfig(**raw_main)` is constructed (not a post-construction mirror of `KMS_DB_PATH`); read it via `ApiKeys` `Field(alias="VAULT_ROOT")`. | ✅ Resolved | The corrected mechanism verifies in the real `load_config()`. (1) **Pre-construction window exists:** `raw_main = _load_yaml("config.yaml")` builds a mutable dict at `config.py:582`; `MainConfig(**raw_main)` is constructed at `config.py:591` — a clear mutation window between them. (2) **Raw slot satisfies the validator:** `validate_vault_root_exists` (`config.py:372-382`) is `@model_validator(mode="after")` checking the **constructed value** `self.vault.root.exists()`; writing `raw_main["vault"]["root"] = "/data/vault"` (YAML shape `vault:`→`root:`, `src/config/config.yaml:81/88`) before `:591` makes it validate `/data/vault` (which the Dockerfile `mkdir -p /data/vault` creates). (3) **Env read mirrors `KMS_DB_PATH`:** `ApiKeys` is a pydantic-settings `BaseSettings` (`config.py:514-518`) where `kms_db_path` reads `KMS_DB_PATH` (`config.py:522`); a sibling `Field(default=None, alias="VAULT_ROOT")` reads `VAULT_ROOT` identically. (4) **Ordering allows it:** `keys = ApiKeys()` runs at `config.py:589`, **before** `MainConfig(**raw_main)` at `:591`, so `keys.vault_root` is available to inject — see Update §below for the single ordering wrinkle (the injection must precede the `Config(...)` call, not sit at `:596-597`). No new invalidation. |
| A10 | `rename()` and `delete_by_path()` return `Result[int]` rowcount; 0 = not in index. | ✅ Validated | `documents.py:213-239` (delete: vec→fts→documents in one txn, `Success(cur.rowcount)`); `:316-366` (rename: UPDATE documents + copy vec/fts old→new in one txn, `Success(cur.rowcount)`). Absent path → 0 rows matched → rowcount 0. `upsert_from_upload` confirmed absent; `upsert` takes a `WriteOutcome` (`:100-104`). |

**C2 component-claim checks:**

| Component | Spec Claim | Verdict | Evidence |
|-----------|-----------|---------|----------|
| C2-1 | New `upsert_from_upload` sibling; existing `upsert` takes `WriteOutcome` (don't reuse it); use `get_connection`. | ✅ Validated | `upsert` signature `documents.py:100-104` takes `WriteOutcome`; `get_connection` at `db.py:76`; `upsert_from_upload` absent (to create). |
| C2-2 | REST handlers + gate live in new `api.py`; `tools.py` stays logic-free; events reuse `rename`/`delete_by_path`. | ✅ Validated | `tools.py` is single-expression shims, no statement-level branching (`tools.py:18-103`); `api.py` absent; reuse targets confirmed (A10). |
| C2-3 | `cloud_entry.py` imports `mcp` from `server.py` (load_dotenv already done), calls `init_db()`, builds app via `streamable_http_app()`, mounts routes + gate, runs uvicorn; `__main__` guard. | ✅ Validated (gated by A9) | `mcp` import-safe (A7); `streamable_http_app()`/lifespan (A1); load_dotenv at `server.py:27` (C-11). Will crash at import-time CONFIG validation unless A9 fixed. |
| C2-4 | `init_db()` after restore, before serve; idempotent; DB path via `KMS_DB_PATH`. | ✅ Validated | A3 + `config.py:522/596-597` wire `KMS_DB_PATH`→`database.path` (re-validated via `DatabaseConfig.validate_assignment`). |
| C2-5 (re-checked) | `VAULT_ROOT` binding injected into the raw config dict **before** construction (corrected — deliberately NOT a post-construction `KMS_DB_PATH` mirror). | ✅ Resolved | Spec C2-5 now describes pre-construction raw-dict injection + `ApiKeys` env alias, which matches the code: mutable `raw_main` at `config.py:582`, construction at `:591`, construction-time existence validator at `:372-382`, `ApiKeys` env source at `:514-522`. The corrected spec explicitly notes this **differs** from the DB-path path — exactly right. |
| C2-6 | `scripts/start.sh` drives restore / replicate / launch / SIGTERM-drain-flush; app stays Litestream-unaware. | ⚠️ Unverifiable (not built; shell orchestration) | File absent; sequence is runtime shell behavior verified by `docker run` + stop signal, not from existing code. R4 mechanics are an implementation choice, not a code claim. |
| C2-7 | Add `uvicorn` to `dependencies`; keep `kms` entry point; `python -m mcp_server.cloud_entry` launch. | ✅ Validated | A5; `kms = "cli.main:cli"` present (`pyproject.toml:37`). |
| C2-8 (re-checked) | Dockerfile + `litestream.yml`; `mkdir -p /data/vault` satisfies vault-root check before config loads. | ✅ Resolved (was ⚠️ blocked by A9) | With A9's corrected pre-construction injection, the existence validator (`config.py:372-382`) now checks the injected `VAULT_ROOT=/data/vault` value, and the Dockerfile `mkdir -p /data/vault` makes that path exist — so the check passes. The dependency on A9 is satisfied. (A4 Litestream/WAL remains a separate deploy-time check, unchanged.) |

---

## Edge Cases & Silent Failure Modes

- **A9 trap (RESOLVED in spec, but keep the guard during implementation).** The original danger: copying the DB-path override *shape* (assign `cfg.main.vault.root` after `MainConfig` construction) appears correct in review but crashes the container with `Vault root does not exist: <YAML value>` because construction validates the YAML root first. The spec has been corrected to inject `VAULT_ROOT` into the raw dict **before** `MainConfig(**raw_main)` (`config.py:591`). The implementation guard that remains: the injection MUST sit before line 591 (between the `_load_yaml` at `:582` and the `Config(main=MainConfig(**raw_main), ...)` call at `:590-595`), NOT at the existing post-construction DB-path block at `:596-597`. Placing it at `:596-597` would re-introduce the exact bug. A unit test must point `VAULT_ROOT` at a temp dir AND assert that an unset `VAULT_ROOT` leaves the YAML value untouched.
- **A7↔A9 coupling.** `import mcp_server.server` evaluates `from core.config import CONFIG` at module scope (`server.py:49`), which fires vault-root validation. So `cloud_entry.py` merely *importing* `server.py` will crash if `VAULT_ROOT` isn't correctly wired — before uvicorn even starts. The lifespan question (A1) is moot if the import fails.
- **A1 timing nuance.** The engine builds per MCP session, not at boot. A health-check ping (`/health`) or a `/api/*` call will work with no engine yet; the engine only materializes when a chat client hits `/mcp`. This is correct, but a smoke test that only curls `/health` does NOT prove the MCP path works — P5-DEPLOY-02 must actually issue a tool-list request to exercise the lifespan.
- **`custom_route` ordering.** Routes added via `mcp.custom_route(...)` only appear if registered **before** `streamable_http_app()` is called (the merge happens at build, `server.py:1038`). Registering after build is a silent no-op for that path; use `app.routes.append`/`add_route` post-build instead.
- **`rename`/`delete` rowcount independence.** `rename`'s `not_found` signal is the **documents** UPDATE rowcount, independent of whether search rows existed (vec/fts copies are guarded by `if row:`). A path with a documents row but no search rows still returns rowcount 1 (ok), correctly.
- **Starlette `.routes` is a property.** `app.routes` returns the live underlying list, so `app.routes.append(...)` mutates it in place and works; but `app.routes = [...]` (rebind) would not (read-only property). Minor — note for planning.

---

## Dependencies & Coupling

- **Cloud entry → MCP server module.** `cloud_entry.py` imports `mcp` and (transitively) triggers `load_dotenv` + `CONFIG` validation. Coupled to A9 for boot.
- **Upload/event handlers → storage family.** `api.py` depends on `upsert_from_upload` (new), `rename`, `delete_by_path`. All in-process, testable against a temp DB.
- **`VAULT_ROOT` binding → config loader control flow.** The corrected fix (now in spec C2-5) is NOT a new field alone; it changes *where* in `load_config()` the override is applied — pre-construction raw-dict injection between `config.py:582` and `:591`, plus an `ApiKeys` `Field(alias="VAULT_ROOT")` at `:522`. Confirmed feasible: `keys = ApiKeys()` (`:589`) already runs before `MainConfig(**raw_main)` (`:591`). Touches only `core/config.py` (C-CONFIG scope holds).
- **Litestream → WAL.** External tool; relies on WAL mode being on (it is). The `wal_autocheckpoint=100` interaction is the only open runtime question.
- **No pipeline / LLM / audit coupling.** Confirmed: the stub endpoints make no AI decision, so C-13 (audit) is satisfied by construction; no `provider.complete()` is introduced.

---

## Extension Points

- **New file-format handling, AI behavior:** untouched — this slice adds no pipeline stage. Phase 7 owns the upload→capture refactor; `upsert()` (`documents.py:100`) is deliberately left for it.
- **Auth gate:** a single shared seam (middleware or shared check) scoped to `/api/*`. Extensible to new sync routes by adding to the prefix set, not by editing handlers.
- **MCP tools:** unchanged; the cloud path reuses the same `mcp` object, so any future tool added to `tools.py` is automatically served in cloud mode too.
- **Blocked extension:** the vault-root config field is a throwaway bridge (TD-059). Do not build anything else on cloud-side vault root; it is removed at the config split (Phases 6/7/9).

---

## Open Questions

- **A4 — Litestream vs `wal_autocheckpoint=100`.** Verified in-repo that WAL + `wal_autocheckpoint=100` are set (`db.py:18-19`) and WAL is Litestream's prerequisite. Whether `=100` specifically conflicts with Litestream's recommended checkpoint posture is a property of the external Litestream binary's behavior and cannot be determined from this codebase. What I checked: the pragma values, that WAL is unconditionally on, and that nothing in the repo forces a manual `wal_checkpoint(TRUNCATE)` that would race Litestream. Recommendation: confirm against Litestream docs during C2-8 (Litestream's own guidance is typically "let Litestream manage checkpoints; a small autocheckpoint is tolerated"). Non-blocking for the spec — it is a deploy-time verification, not a code claim.
- **C2-6 SIGTERM drain/flush mechanics (R4).** The script does not exist; the trap→drain→flush order is an implementation choice verified by a stop-signal test, not by existing code. Non-blocking.

---

## Technical Debt Spotted

- **TD-059 (already tracked)** — ~~the `VAULT_ROOT` binding + dummy `/data/vault` are throwaway.~~ resolved-direction: the A9 fix (pre-construction injection) is now in spec C2-5 and is *also* throwaway, removed at the config split. Keep the fix minimal (one `ApiKeys` field + one injection block before `config.py:591`) so it is cheap to delete.
- **Latent: the DB-path override pattern is itself fragile as a template.** It works for `database.path` only because `DatabaseConfig` carries `validate_assignment=True` and the path has no construction-time gate. Anyone copying it for a field that has a parent-level `model_validator` (like vault root) will hit the same trap. Worth a one-line comment near `load_config():596` warning that the post-construction-assign pattern is safe only for fields without construction-time cross-field validation. (Note for the author; not in this slice's scope to fix beyond A9.)

---

## Invalidated Assumptions

_None. The single previously-invalidated assumption (A9 — `VAULT_ROOT` override mechanism) was corrected in the spec (C2-5) and confirmed RESOLVED against code on the 2026-06-13 re-check (see ## Update below). No new invalidations were introduced by the fix._

---

## Update — 2026-06-13

### Re-check: all assumptions resolved

Re-check mode: the spec's `## Invalidated Assumptions` entry (A9) was patched with a corrected mechanism — inject `VAULT_ROOT` into the raw config dict **before** `MainConfig(**raw_main)` is constructed, reading the env var via an `ApiKeys` `Field(alias="VAULT_ROOT")`. I re-verified the corrected mechanism against the real `load_config()` body and the `MainConfig`/`VaultConfig`/`ApiKeys` models. All four required checks pass; no new invalidation introduced.

| ID | Was | Now | Evidence |
|----|-----|-----|----------|
| A9 | Mirror `KMS_DB_PATH`'s post-construction assignment (`cfg.main.vault.root = ...` at `config.py:596-597`) — **invalidated** (validator fires at construction, before the assignment; `VaultConfig` has no `validate_assignment`). | Inject `VAULT_ROOT` into `raw_main["vault"]["root"]` **before** `MainConfig(**raw_main)`; read via `ApiKeys` env alias — **resolved**. | `config.py:582` (`raw_main = _load_yaml("config.yaml")` → mutable dict), `:589` (`keys = ApiKeys()`), `:590-595` (`Config(main=MainConfig(**raw_main), ...)`), `:372-382` (construction-time `validate_vault_root_exists`), `:522` (`kms_db_path` env-alias pattern to copy), `src/config/config.yaml:81/88` (`vault:`→`root:` raw shape). |
| C2-5 | "mirrors `KMS_DB_PATH` exactly" — **invalidated**. | Pre-construction raw-dict injection + `ApiKeys` env alias (explicitly NOT a post-construction mirror) — **resolved**. | Same as A9. |
| C2-8 | `mkdir -p /data/vault` pointless because validator checked YAML root — **partially blocked**. | Validator now checks the injected `/data/vault`; the `mkdir` satisfies it — **resolved**. | `config.py:372-382` validates the constructed (injected) value. |

**Detailed verification of the four re-check questions:**

1. **Pre-construction injection point — YES.** `load_config()` loads YAML into a mutable dict: `raw_main = _load_yaml("config.yaml")` (`config.py:582`). `MainConfig` is constructed later as a nested arg to `Config(...)`: `cfg = Config(main=MainConfig(**raw_main), thresholds=..., routing=..., keys=keys)` (`config.py:590-595`). The window to write `raw_main["vault"]["root"]` is between `:582` and `:590`. (Wrinkle: because `MainConfig(**raw_main)` is *inline* inside the `Config(...)` call, the injection statement must be placed **before** line 590 — it cannot be a separate post-`cfg` line like the DB-path block at `:596-597`. This is a placement constraint, not a blocker.)

2. **Writing the raw `vault.root` slot satisfies the validator — YES.** `MainConfig.validate_vault_root_exists` (`config.py:372-382`) is `@model_validator(mode="after")` and checks `self.vault.root.exists()` — the **constructed field value**, not the env or any external source. Setting `raw_main["vault"]["root"] = "/data/vault"` before construction means the validator sees `/data/vault`. The raw key path is confirmed `raw_main["vault"]["root"]` (YAML block `vault:` → `root:` at `src/config/config.yaml:81/88`; `_load_yaml` returns that nested dict). `/data/vault` exists in the container via the Dockerfile `mkdir -p /data/vault` (C2-8), so the check passes.

3. **`ApiKeys` `Field(alias="VAULT_ROOT")` reads the env var like `KMS_DB_PATH` — YES.** `ApiKeys` is a pydantic-settings `BaseSettings` with `model_config = SettingsConfigDict(env_file=".env", extra="ignore")` (`config.py:514-518`). `kms_db_path: Path | None = Field(default=None, alias="KMS_DB_PATH")` (`config.py:522`) reads `KMS_DB_PATH` from the env / `.env` source. A sibling `vault_root: Path | None = Field(default=None, alias="VAULT_ROOT")` reads `VAULT_ROOT` by the identical mechanism. The value is then available as `keys.vault_root` inside `load_config()`.

4. **Ordering allows reading the env before construction — YES.** `keys = ApiKeys()` is constructed at `config.py:589`, **before** `MainConfig(**raw_main)` runs at `:591` (inside the `Config(...)` call at `:590-595`). So `keys.vault_root` is populated and available to inject into `raw_main` before the model that validates it is built. (Alternatively `os.environ.get("VAULT_ROOT")` could be read directly, but the `ApiKeys` alias is the cleaner, spec-named path and is already ordered correctly.)

**Conclusion:** A9 is RESOLVED — the corrected spec mechanism is feasible and correct against the real code, with one implementation placement constraint (inject before the `Config(...)` call at `config.py:590`, not at the post-construction DB-path block `:596-597`). No assumption that was previously ✅ Validated is newly broken by the fix. All ten assumptions are validated or resolved; the only non-validated item is A4 (⚠️ Unverifiable — Litestream `wal_autocheckpoint=100` interaction), which is an external-tool deploy-time check, unchanged by this pass. **Ready for /plan P5_slice2_deployment_foundation.**
