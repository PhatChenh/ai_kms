# Research: Phase 8 Slice A — Classify Infrastructure (no LLM calls)
_Last updated: 2026-06-14_

## Overview

In plain terms: this research checks whether the build plan for "Phase 8 Slice A" matches what the code actually does today. Slice A builds the plumbing that finds documents needing classification, loads their inputs, and runs a single background worker — but makes no AI calls yet. The spec made 11 testable claims about the existing code ("assumptions A1–A11"). This pass opened every file each claim names and confirmed it against the real code, not the spec's labels or line numbers.

**Result (after the A7 re-check pass, 2026-06-14): all 11 assumptions are clean — 10 validated, 1 (A7) resolved.** The slice is ready to plan. A7 was previously invalidated because the spec recommended a Starlette `on_startup` handler that the framework's built-in lifespan silently suppresses. The spec has since been patched to use a **composed outer lifespan** instead, and this re-check confirmed against the actual framework code that the new mechanism is sound. Everything else — the database columns, the config shape, the fact-store dataclass, the migration cascade, the dimension-loader break, the capture seam — was validated on the first pass and is unchanged.

How the worker now starts (the resolved A7 mechanism, verified end-to-end in code): the web app the container builds already carries a built-in startup routine (the chat-session manager) that runs as an async context manager. The patched plan wraps that existing routine inside a new one that *also* starts the background worker and the catch-up scan, then enters the built-in routine and yields through it — cancelling the worker on shutdown. Because the framework reads the startup routine at boot time (not when the app is constructed), swapping it inside `build_app` after the app is built takes effect, and the chat-session manager still runs. No blocking issues remain; no new invalidations were introduced.

---

## Key Components

These are the files and functions the spec touches, with their real roles and locations as confirmed in this pass.

- **`core/tags.py`** — the dimension authority. `load_dimensions()` (line 147) reads `dimensions.yaml` into a plain dict. `validate_dimension_tag()` (line 159) checks a `(dimension, tag)` pair; line 178 reads `allowed_tags = rulebook[dimension]` and treats it directly as the tag list. `confidence_to_status()` (line 189) maps a score to `confident`/`pending` via a band — untouched by Slice A.
- **`storage/knowledge_entries.py`** — the fact store. `KnowledgeEntry` dataclass (lines 16–30) has no `trust_score`/`retrieval_count`. `_row_to_entry` (line 33) reads only the existing columns. `get_confident_and_pending` (line 171) returns non-retired entries ordered by `dimension, entity, tag` with no cap.
- **`storage/documents.py`** — the document store. `DocumentRow` (line 34) carries `content_hash`, `summary`, and `full_body`. `upsert_from_upload` (line 111) writes `content_hash` on every captured row. No Work-Finder / Classified-Stamp functions exist yet.
- **`storage/db.py`** — `init_db` + `_run_migrations` (line 29) auto-discover numbered `.sql` files via `glob("[0-9][0-9][0-9]_*.sql")` and apply any whose number exceeds the stored version. No registry.
- **`mcp_server/cloud_entry.py`** — `build_app()` (line 43) builds the container's web app at boot: imports the MCP object, runs `init_db`, wires the blob store, calls `mcp.streamable_http_app()`, mounts REST routes, returns the app. No event loop runs at this point; no startup hook is attached.
- **`mcp_server/server.py`** — defines the FastMCP `_lifespan` (line 101), which CLAUDE.md correctly flags as per-chat-session.
- **`pipelines/capture.py`** — emits `capture.classify_ready` with `vault_path` at lines 267 and 482; `row_id` is in scope at both (returned immediately after).
- **`core/config.py`** — `CaptureConfig` (line 315) is the sub-model pattern to mirror; `MainConfig` (line 346) is where a new `classify:` block slots in alongside `capture` (line 365) and `search` (line 366).
- **`config/dimensions.yaml`** — currently the flat shape `dimension → [tags]`.
- **Third-party: `mcp` (FastMCP) + `starlette`** — the decisive evidence for A7 lives here, not in project code.

---

## How It Works

When the container starts, `build_app()` assembles the web app and returns it; uvicorn then runs that app, and uvicorn's startup is where any background worker would need to be launched. The MCP framework's web app (`mcp.streamable_http_app()`) is a Starlette app whose lifespan is **pre-set inside the framework** to run the MCP session manager. The spec's plan was to *also* attach a startup handler to this same app so the classify worker boots alongside it.

The break: Starlette only consults `on_startup`/`on_shutdown` handlers when **no** custom lifespan is set. When a lifespan is set (as the framework does), those handlers are silently ignored.

```
Container boot
      │
      ▼
build_app() assembles the web app
      │
      ▼
mcp.streamable_http_app()  ← returns a Starlette app
                              with a lifespan ALREADY set
                              (runs the MCP session manager)
      │
      ▼
uvicorn runs the app
      │
      ├─ framework lifespan runs (MCP session manager)   ✅
      └─ any added "startup handler"  →  IGNORED          ❌
                                          (worker never starts)
```

Confirmed at `.venv/.../mcp/server/fastmcp/server.py:1040-1044` (the returned `Starlette(... lifespan=lambda app: self.session_manager.run())`) and `.venv/.../starlette/routing.py:582-599` (the `Router` only wires `on_startup` into the default lifespan when `lifespan is None`).

**Resolved (re-check, 2026-06-14):** the spec no longer uses `on_startup`. It now composes a new outer lifespan that wraps the framework's existing one. This works because Starlette reads the lifespan at the ASGI `lifespan` event — `async with self.lifespan_context(app)` at `.venv/.../starlette/routing.py:638`, which fires at uvicorn startup, *after* `build_app` has returned. So reassigning `app.router.lifespan_context` inside `build_app` is honoured. The inner (framework) lifespan is `session_manager.run()`, itself an `@asynccontextmanager` (`.venv/.../mcp/server/streamable_http_manager.py:101-102`); its docstring documents the exact wrap pattern `async with session_manager.run(): yield` (`:114-117`), and `run()` may be entered only once per instance (`:119-126`) — which the composed lifespan satisfies (one entry per app boot, same as today). See the Re-check Update at the bottom.

---

## Spec Verification

Plain English: ten of the eleven claims held exactly. The one that failed (A7) is about *where* to start the worker — the spec's specific recommended hook does not work and would fail silently.

| Assumption ID | Spec Claim | Verdict | Evidence |
|--------------|-----------|---------|----------|
| A1 | `documents` has a `content_hash` column the Work Finder compares against, populated on captured rows | ✅ Validated | `documents.py:47` (`DocumentRow.content_hash`); `schema.sql:11`; `upsert_from_upload` writes `content_hash` on INSERT (`documents.py:166-182`) and UPDATE; capture passes it (`capture.py:190,363`) |
| A2 | `documents.full_body` AND `documents.summary` both exist and are readable | ✅ Validated | `summary` in base `schema.sql:5`; `full_body` added by migration `008_*.sql`; both on `DocumentRow` (`documents.py:42,53`); both read in `_row_from_sqlite` |
| A3 | `validate_dimension_tag` does `rulebook[dimension]` as the tag list (the nested shape breaks it) | ✅ Validated | `core/tags.py:178` — `allowed_tags = rulebook[dimension]`; `dimensions.yaml` is flat today, so nesting `{tags, guidance}` would make this a dict and break `tag not in allowed_tags` |
| A4 | Only `tests/test_core/test_dimensions.py` pins the flat shape; no other module reads `rulebook[dim]` as a flat list | ✅ Validated | grep of `load_dimensions`/`validate_dimension_tag` across `src/` + `tests/` returns only `core/tags.py` (definitions) and `test_dimensions.py`. `knowledge_entries.py` imports only `confidence_to_status` from `core.tags`, not the dimension fns. `test_dimensions.py:60-63` asserts `"other" in loaded["people"]` (flat-list membership) — breaks under nesting |
| A5 | `get_confident_and_pending` excludes retired and is NOT ranked/capped | ✅ Validated | `knowledge_entries.py:181` `WHERE status != 'retired'`; `:191` `ORDER BY dimension, entity, tag`; no `LIMIT`, no trust/confidence ordering |
| A6 | `KnowledgeEntry` + `_row_to_entry` do NOT carry `trust_score`/`retrieval_count` today | ✅ Validated | dataclass fields end at `updated_at` (`knowledge_entries.py:20-30`); `_row_to_entry` (`:33-47`) reads no such columns |
| A7 | `build_app` starts the worker via a **composed outer lifespan** (shape b: wrap the framework app's existing session-manager lifespan in place; NOT `on_startup`, NOT the per-chat MCP lifespan) | ✅ Resolved | Spec patched to forbid `on_startup` and mandate a composed `@asynccontextmanager` lifespan. Code confirms it is sound: `build_app` already holds the app and mutates it in place (`cloud_entry.py:78,81`); the lifespan lives on `app.router.lifespan_context` and is read at the ASGI lifespan event — i.e. at uvicorn startup, *after* `build_app` returns (`starlette/routing.py:638` `async with self.lifespan_context(app)`), so reassigning it in `build_app` takes effect; the inner FastMCP lifespan is `session_manager.run()`, an `@asynccontextmanager` (`streamable_http_manager.py:101-102`) whose own docstring shows the exact `async with session_manager.run(): yield` wrap pattern (`:114-117`). See Re-check Update |
| A8 | Version-pin bumps `9`→`10` are `test_migration_007.py:41,56`, `008.py:47`, `009.py:38` — and no other migration test pins a version | ✅ Validated | Exact lines/values confirmed (all assert `9` today). A full `grep schema_version` across `tests/` returns only those three files |
| A9 | `capture.classify_ready` logged at `capture.py:267` and `:482` with `vault_path`; `row_id` is in scope | ✅ Validated | Both lines log `capture.classify_ready` with `vault_path=vault_path`; `Success(row_id)` returned at `:269` and `:484` — `row_id` in scope |
| A10 | `init_db` runs numbered migrations in order; dropping `010_*.sql` is sufficient (no registry) | ✅ Validated | `db.py:36` `sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))`, applies `file_version > version`. 008/009 are plain files, no index. Drop-file-only confirmed |
| A11 | Confidence band for status is reachable via `CONFIG.thresholds.for_pipeline("classify")` and unchanged by Slice A | ✅ Validated | `core/config.py:478` `for_pipeline(...)` exists and returns a `ConfidenceBand`; Slice A reads no band (status re-gating is Slice B), so it is genuinely unused here |

---

## Edge Cases & Silent Failure Modes

Plain English: the dangerous failure here is invisible — the worker simply never starts and nothing errors out.

- **A7 silent failure (resolved, but keep the guard test).** The original trap: an `on_startup` hook on the FastMCP-returned app boots cleanly, `/health` returns 200, the MCP path works for chat — and the catch-up scan + worker never run, with no exception and no log. The spec now forbids `on_startup` and uses a composed lifespan, so the trap is designed out. Because the failure mode is invisible, planning should still include the end-to-end guard test the spec's component 7 describes: assert the worker task is created AND the inner FastMCP session-manager lifespan still runs on app entry, AND the worker is cancelled on exit. A `/health` curl or MCP tool-list test alone would not catch a regression here.
- **Nested-shape break is loud, not silent (A3/A4).** Once `dimensions.yaml` nests, `test_dimensions.py:60-63` fails immediately (`"other" in {...}` checks dict keys) and `validate_dimension_tag` returns wrong results — caught at test time. This is the *known* mechanical cascade the spec budgeted for.
- **`get_confident_and_pending` vs new ranked query (A5).** If a future edit adds `LIMIT`/ranking to `get_confident_and_pending` instead of a new function, existing callers silently get capped result sets. The spec's "new function, do not extend" decision (OQ-P8A-02) prevents this — honor it.
- **`content_hash` NULL on legacy rows (A1).** `content_hash` is nullable in schema. The Work-Finder query `classify_content_hash IS NULL OR classify_content_hash != content_hash` correctly re-discovers a row even if `content_hash` is NULL (NULL `!=` NULL is NULL/false, but `classify_content_hash IS NULL` catches a never-classified row). No issue for Slice A; noted for completeness.

---

## Dependencies & Coupling

Plain English: what Slice A leans on, and what could shift under it.

- **Migration auto-discovery (`db.py`)** — Slice A's migration 010 rides the existing glob runner. No coupling to a registry. Safe.
- **`core/tags.py` is shared by capture + knowledge extraction.** Only `confidence_to_status` is used outside the dimension functions, and Slice A does not touch it. The nested-shape change is contained to `load_dimensions`/`validate_dimension_tag` + their one test file.
- **`storage/knowledge_entries.py` 5-function CRUD contract** — Slice A adds a *new* ranked query and two dataclass fields; the existing five functions stay stable. `upsert`'s INSERT column list (`:99-113`) omits `trust_score`/`retrieval_count`, relying on DB defaults — see Open Questions for the `SELECT *` round-trip note.
- **FastMCP version coupling (A7).** The break depends on FastMCP hardcoding the lifespan in `streamable_http_app()`. A future MCP upgrade could change this surface — the worker-start fix should not depend on FastMCP internals beyond "it sets a lifespan."

---

## Extension Points

- **Migrations** — adding `010_*.sql` is the clean, intended extension; the runner needs no edit.
- **Config** — `ClassifyConfig` slots into `MainConfig` exactly like `CaptureConfig`/`SearchConfig` (`config.py:365-366`); add a `classify:` block to `config.yaml` (no block exists today; top-level keys are alphabetical-ish but not enforced).
- **Dimension loader** — `core/tags.py` is the correct deep module to extend (the design's Option A); no new module needed.
- **Worker-start hook** — resolved. The FastMCP-returned app is closed to `on_startup` extension, but it is open to lifespan composition: capture `app.router.lifespan_context`, wrap it in an outer `@asynccontextmanager`, and reassign in place inside `build_app`. Verified sound (see How It Works + Re-check Update). This is the spec's component-7 mechanism.

---

## Open Questions

- **`knowledge_entries.upsert` column list** (carried from spec component 3, `[UNVERIFIED]`). Confirmed from code: `upsert`'s INSERT (`:99-113`) and UPDATE (`:73-89`) omit `trust_score`/`retrieval_count`. With migration 010 supplying `DEFAULT 0.5` / `DEFAULT 0`, omitted INSERTs get correct defaults — so the writes do not strictly need the columns added. The ranked query uses `SELECT *`, so as long as `_row_to_entry` learns to read the two columns (spec component 3), the round-trip populates them. **Conclusion: adding the columns to `upsert`'s SQL is optional for Slice A correctness; only the dataclass + `_row_to_entry` reads are required.** Examined: the full `upsert`, `_row_to_entry`, and `query_by_dimension` (`SELECT *`) bodies. Decision (cosmetic vs explicit-write) is a planning judgment, not a code blocker.
- **Does the worker run under uvicorn's loop at all once the hook is fixed?** Code shows `build_app` returns before any loop exists, and uvicorn drives the lifespan — so a *composed lifespan* (not `on_startup`) is the verified path. The exact composition shape (anyio task group vs `asyncio.create_task` inside the lifespan body) cannot be pinned from static reading alone; it is an implementation choice for the design update, bounded by "must enter the FastMCP session-manager lifespan AND start the worker, both under uvicorn's loop."

---

## Technical Debt Spotted

- **Catch-up scan one-burst enqueue** (already logged by spec as OQ-P8A-03) — a large vault floods the in-memory queue at startup. Real, low-impact for single-user; page/batch later. Worth a TD entry per the grill's "watch vault size."
- **Inert columns `trust_score`/`retrieval_count`** — ship doing nothing in P8; a future reader may wonder why. The migration comment (spec-mandated) covers it. No action.

---

## Update — 2026-06-14
### Re-check: all assumptions resolved

The spec was patched to fix the one invalidated assumption (A7). This re-check pass re-verified A7 against the actual framework code (not the patched spec wording) and re-confirmed the other ten assumptions are untouched. **All 11 assumptions are now clean — 10 validated, 1 resolved. No new invalidations. Ready for `/plan`.**

| ID | Was | Now | Evidence |
|----|-----|-----|----------|
| A7 | "Start the worker via a Starlette `on_startup` handler on the FastMCP-returned app" — proven a silent no-op (the app already has a custom lifespan; Starlette ignores `on_startup` when a lifespan is set). | ✅ Resolved — spec now uses a **composed outer lifespan** (shape b: wrap the framework app's session-manager lifespan in place, reassigned inside `build_app`; `on_startup` explicitly forbidden; NOT the per-chat MCP lifespan). Mechanism verified sound. | See "Why the patched mechanism is sound" below. |

**Why the patched mechanism is sound (verified at file:line):**

1. **The app is available to mutate in place.** `build_app` does `app = mcp.streamable_http_app()` then `app.routes.extend(api_routes + health_route)` — it already holds and mutates the app object (`mcp_server/cloud_entry.py:78,81`). Reassigning that app's lifespan in the same place is the minimal, consistent change.
2. **The lifespan is reassignable after construction and the swap takes effect.** Starlette stores the lifespan on `app.router.lifespan_context` (`starlette/applications.py:52` → `Router.__init__`, `starlette/routing.py:599`). It is *read* — not at construction — but at the ASGI `lifespan` event: `async with self.lifespan_context(app)` (`starlette/routing.py:638`). That event fires at uvicorn startup, **after** `build_app` returns. So capturing the existing `app.router.lifespan_context`, wrapping it, and reassigning a composed `@asynccontextmanager` in place is honoured. (The app's `middleware_stack` is built lazily on first call and wraps `self.router`, which reads `lifespan_context` dynamically — no early freeze; `starlette/applications.py:74,86-90`.)
3. **The inner (framework) lifespan composes cleanly.** The FastMCP-returned app's lifespan is `lambda app: self.session_manager.run()` (`mcp/server/fastmcp/server.py:1044`), and `StreamableHTTPSessionManager.run()` is an `@contextlib.asynccontextmanager` (`mcp/server/streamable_http_manager.py:101-102`). Its own docstring shows the exact composition pattern the spec adopts — `async with session_manager.run(): yield` (`:114-117`). The composed outer lifespan therefore: start worker + catch-up scan → `async with inner(app): yield` → cancel worker on exit.
4. **The original trap is avoided.** No `on_startup` (a proven no-op here; `starlette/routing.py:582-599`), and not the per-chat MCP `_lifespan` (`mcp_server/server.py:101`). The worker runs under uvicorn's single event loop at container boot, and the MCP session manager still initialises.
5. **No new problem introduced.** `run()` may be entered only once per instance (`streamable_http_manager.py:119-126`); the composed lifespan enters it exactly once per app boot — same call count as today — so the guard is satisfied. The only residual is the composition *shape* (`asyncio.create_task` inside the lifespan body vs an anyio task group), which is an implementation choice the spec correctly leaves open, not an unverified assumption.

**Regression check (other assumptions, re-spot-checked against code):** A1/A2 — `DocumentRow` still carries `content_hash` (`documents.py:47`), `summary` (`:41`), `full_body` (`:52`). A3 — `core/tags.py:178` still `allowed_tags = rulebook[dimension]`. A5 — `get_confident_and_pending` still `WHERE status != 'retired'` + `ORDER BY dimension, entity, tag`, no `LIMIT` (`knowledge_entries.py:181,191`). A8 — three version-pin files still assert `9` today (`test_migration_007.py:41`, `test_migration_008.py:47`, `test_migration_009.py:38`). The patch changed only spec prose around A7 and touched no source code; A1–A6 and A8–A11 remain validated, unchanged.
