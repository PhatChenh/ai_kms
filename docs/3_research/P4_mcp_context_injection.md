# Research: Phase 4 — MCP Server Context Injection & Tool Design
_Last updated: 2026-06-11 (re-check pass)_

## Overview

Plain English: Phase 4 wraps already-built search/read/move/extract machinery in a long-running MCP server so Claude Desktop can browse the user's vault during a chat, with background context auto-attached to each response. This research opened every file the spec names and checked each of the 13 assumptions against what the code actually does — not what the spec claims. **Re-check status (2026-06-11):** the two previously-invalidated assumptions (A1, A7) were patched by the orchestrator and re-verified against the real FastMCP API and the real move/write/index code. **A1's concept is RESOLVED** — the patched wording ("process-scoped lifespan = per-conversation under stdio, no Option B fallback") is code-accurate. **A7's concept is RESOLVED** — the patched recipe correctly states `move_note` carries no metadata and a separate `write_note(dst, new_meta)` is needed. **But the patch introduced one NEW transcription error (A7b):** the spec's concrete call `replace_path(old, dst)` is wrong — the real signature is `replace_path(old_vault_path: str, outcome: WriteOutcome)`; the second argument is the `WriteOutcome` from the post-move `write_note`, NOT the destination `Path`. **(A7b ✅ RESOLVED 2026-06-11 — Component 9 corrected to `replace_path(old_vault_path, outcome)`.)**

The single load-bearing question — A1 — was: does FastMCP expose state a tool can read at call time? Re-confirmed via ephemeral `uv run --with 'mcp>=1.27,<2'` introspection: the lifespan is entered **once per process** (`Server.run`: `lifespan_context = await stack.enter_async_context(self.lifespan(self))`), reachable from a tool via `ctx.request_context.lifespan_context` (a real `RequestContext` field), and the `ctx` parameter is **excluded from the public tool schema** (a `kms_demo(query, ctx)` exposes only `query`), so a one-line tool body stays C-14-clean. Under stdio one process = one conversation, so this process-lifespan IS the per-conversation holder Option A needs. Patched A1 is accurate.

Counts after re-check + A7b fix (2026-06-11): **10 Validated · 3 Resolved (A1, A7, A7b) · 1 Validated-by-design/partially-unverifiable (A13)**. A7b (a one-token `replace_path` argument error the A7 patch introduced) was corrected in Component 9 to the exact call verified here (`replace_path(old_vault_path, outcome)`), user-accepted without a confirmation re-check. **No live invalidations remain — spec is plan-ready.** No Q4 diagram (all fixes are mechanical text changes).

---

## Key Components

Plain English: these are the real, shipped pieces the five tools reuse, plus the one external library the server stands on.

- **Search Coordinator** (`src/retrieval/search.py::search`, line 50) — one public `search()` returning `Result[list[SearchResult]]`. Already wires filter → rank → rerank. Has `query/project/date_range/max_results/db_path` params; **no `location` param yet** (spec adds it).
- **Result card** (`src/retrieval/reranker.py::SearchResult`, line 34) — `vault_path, summary, snippet, score, metadata`. The `metadata` dict carries `title, project, note_type, updated_at, key_topics, tags`. Built identically in the re-rank path (`reranker.py:154-161`) and the filter-only path (`search.py:34-41`).
- **Candidate Filter** (`src/storage/documents.py::filter_paths`, line 393) — returns `Result[list[str] | None]`. `Success(None)` = no filters (global); `Success([])` = filtered, nothing matched; `Success([paths])` = matched set.
- **Note Catalog** (`src/storage/documents.py`) — `DocumentRow` (line 27) has `project`, `note_type`, `title`, `status`, `key_topics` (columns added by migrations 003/004/005; `note_type` is base schema). `get_by_path` (146), `all_paths` (178), `replace_path` (232), `rename` (306).
- **Project Registry** (`src/vault/registry.py`) — `build_registry()` (63, returns `Result[ProjectRegistry]`), `ProjectRegistry.groups` (dict keyed by domain), `ProjectGroup` (`domain_name`, `domain_path`, `projects: list[ProjectEntry]`), `ProjectEntry` (`name`, `path`, `domain_unknown`), `all_project_names` (property, 56), `format_for_prompt()` (151), `LiveRegistry` (214). Pure folder scan, no DB.
- **Note Reader** (`src/vault/reader.py::read_note`, line 35) — returns `Result[Note(path, metadata, content, content_hash)]`.
- **Note Mover** (`src/vault/writer.py::move_note`, line 181) and **Index updater** (`replace_path`, documents.py:232).
- **Move guard** (`src/vault/move_guard.py`) — `MoveGuard`, `get_active`, `set_active`, `register(path)`.
- **Handlers** (`src/handlers/registry.py::HandlerRegistry.resolve`, line 47; `src/handlers/base.py::BaseHandler.extract`, line 80) — `resolve(path)→Result[BaseHandler]`, `extract(path)→Result[RawContent(text,...)]`.
- **Correlation id** (`src/core/logging_setup.py::new_correlation_id`, line 55) — calls `clear_contextvars()` (line 71) then `bind_contextvars`.
- **DB factory** (`src/storage/db.py::_connect`, line 16) — `journal_mode=WAL`, `foreign_keys=ON`, sqlite-vec load. No `wal_autocheckpoint`.
- **Config** — `MCPConfig` (`src/core/config.py:244`: `port/host/enable_http`), `SearchConfig` (line 303). `CONFIG` is lazy via module `__getattr__` (line 627).
- **External: `mcp` / FastMCP** (`mcp.server.fastmcp.FastMCP`, `Context`) — **not installed in the repo** (verified absent from `pyproject.toml`). Verified by ephemeral `uv run --with 'mcp>=1.27,<2'` introspection + official SDK docs.

---

## How It Works (the A1 mechanism, in detail)

Plain English: the server starts once, creates one "briefing memory" engine inside a startup/shutdown block, and the framework automatically hands every tool a small `Context` handle that can reach that engine. The chat ends → the process ends → the engine is discarded. No global variable needed; the framework holds it.

The FastMCP mechanism, pinned to real symbols (verified against `mcp>=1.27,<2`):

1. **Create the engine in a lifespan.** Pass an `@asynccontextmanager` to `FastMCP(lifespan=...)`. FastMCP's `__init__` wires it via `lifespan_wrapper(self, self.settings.lifespan)`.
2. **It runs once per process.** `Server.run()` does `lifespan_context = await stack.enter_async_context(self.lifespan(self))` — entered **once per `run()`**, and `run()` is one stdio process. The lifespan callable takes the `FastMCP` app (one per process). Confirmed by the official SDK docs: "the lifespan runs **once per server process**."
3. **Tools read it via injected Context.** A tool declares a `ctx: Context` parameter; FastMCP auto-detects it (`tool.context_kwarg == 'ctx'`) and **excludes it from the public tool schema** (verified: a `kms_demo(query, ctx)` exposes only `query`). Inside the tool: `ctx.request_context.lifespan_context` returns the yielded engine state. `RequestContext` has a real `lifespan_context` field (verified on the dataclass).
4. **The tool body is a one-line pass-through** — `return ctx.request_context.lifespan_context['engine'].do_thing(query)` — no `if/for/while`. C-14 compatible (verified by building such a tool).

Minimal verified sketch:
```python
@asynccontextmanager
async def lifespan(app):
    yield {"engine": ContextInjectionEngine(...)}   # created ONCE at startup

mcp = FastMCP("kms", lifespan=lifespan)

@mcp.tool()
def kms_search(query: str, ctx: Context) -> ...:
    return ctx.request_context.lifespan_context["engine"].search(query)  # one line
```

**Scope precision (the A1 correction):** `lifespan_context` is **process-scoped, not per-connection**. For stdio (the MVP), one process = one client = one conversation, so process-scope == per-conversation in practice — exactly the lifecycle the design relied on (design doc §Option A "one process per conversation"; spec edge case "a new server process starts with a clean slate"). Practically, the FastMCP lifespan IS the same physical holder as Option B's "process-global dict", just reached cleanly through framework injection instead of a module global. So Option A is buildable; it is not a fall-back to B.

---

## Spec Verification

Plain English: most assumptions held outright. A1 and A7 are marked Invalidated because their literal wording is false against the library/code — but each maps to a documented/proven alternative, so neither blocks the build (details in the Invalidated section).

| ID | Spec Claim (paraphrased) | Verdict | Evidence |
|----|--------------------------|---------|----------|
| **A1** | (PATCHED) FastMCP exposes a **process-scoped** lifespan entered once per `Server.run`, reachable via auto-injected `ctx.request_context.lifespan_context`; `ctx` is excluded from the public tool schema; under stdio one process = one conversation, so this is the per-conversation holder — Option A buildable, no Option B fallback. | ✅ Resolved (re-check 2026-06-11) | Re-verified `mcp>=1.27,<2`: `Server.run` source line `lifespan_context = await stack.enter_async_context(self.lifespan(self))`; `FastMCP.__init__` takes `lifespan` kwarg; `RequestContext.lifespan_context` is a real field; `kms_demo(query, ctx)` exposes only `['query']` in inputSchema (ctx auto-detected as `context_kwarg`). Patched wording matches code exactly. |
| **A2** | Tool body can be a one-line pass-through (no branching); dispatch can be wrapped per call. | ✅ Validated | Built a `@mcp.tool()` with `ctx: Context`; FastMCP auto-injects via `context_kwarg`, excludes `ctx` from schema. One-line `return ctx.request_context.lifespan_context[...]` works. (copy_context placement = A11.) |
| **A3** | `mcp>=1.27,<2` installs on 3.12, no conflict with pinned deps. | ✅ Validated | `uv run --with 'mcp>=1.27,<2'` + pydantic 2.13.4 + anthropic 0.109.1 + click 8.4.1 + sqlite_vec co-resolve and import together. Python 3.12.12. (Full lockfile resolve is for the install step; the high-risk pydantic-v2 co-resolution passes.) |
| **A4** | Cards carry `project` + `note_type`, do **not** carry `attachment_path`. | ✅ Validated | `reranker.py:154-161` and `search.py:34-41` build identical metadata: `title, project, note_type, updated_at, key_topics, tags`. No `attachment_path`. `DocumentRow` (documents.py:27) has `project`+`note_type`. `note_type=="attachment-summary"` is real (capture.py:1213, tags.yaml:12) — the binary signal. |
| **A5** | Registry shape (`groups` keyed by domain; `domain_name/domain_path/projects[name,path]`; `all_project_names`) is what `kms_vault_info` needs; cheap folder scan, no DB. | ✅ Validated | `registry.py:50-60` (ProjectRegistry/all_project_names), `41-47` (ProjectGroup), `32-38` (ProjectEntry). `build_registry` (63) does `iterdir()` + per-project `read_note` of CLAUDE.md — filesystem only, no SQL. Returns `Result`. |
| **A6** | Sibling note's `attachment_path` frontmatter holds the binary's vault-relative path, reliably present for captured binaries; resolvable under vault root. | ✅ Validated | `frontmatter.py:72` typed field + in `_KNOWN_KEYS` (40). Written at capture: `attachment_path=to_vault_path(attachment_dst)` (capture.py:1214). `read_note` returns it in `metadata`. |
| **A7** | (PATCHED) `move_note(src,dst,actor="ai")` takes **NO incoming-metadata param** — it re-reads `src` and writes that to `dst`, and blocks human-locked AI moves; so `kms_move` must write the new project/domain with a separate `write_note(dst, new_meta, actor="ai")` AFTER the move. Proven sequence: `move_note` → `write_note(dst, new_meta)` → `replace_path` (capture.py:962-968). | ✅ Resolved (re-check 2026-06-11) — concept correct | `writer.py:181-244`: `move_note` reads `src` (202), merges `incoming=current.metadata` (216) — no caller metadata; blocks human-lock at 208-213 ✓. `write_note` (114) is a pure writer, gate at 147-152. Proven order confirmed at capture.py:961-988. **Caveat: the spec's literal `replace_path(old, dst)` call is wrong — see A7b.** |
| **A7b** | (was: A7 patch wrote `replace_path(old, dst)` — wrong second arg) → **✅ RESOLVED (orchestrator fix 2026-06-11):** Component 9 corrected to `replace_path(old_vault_path, outcome)`, user-accepted without re-check. | ✅ Resolved (orchestrator fix 2026-06-11) | `documents.py:232` real signature is `replace_path(old_vault_path: str, outcome: WriteOutcome, db_path=None, batch_id=None)`. The second arg is the `WriteOutcome` returned by the post-move `write_note(dst, new_meta)`, NOT the `dst` path. Proven call: `replace_path(old_vault_path, outcome, ...)` (capture.py:986-987, 515-516). Passing `dst` (a `Path`) would fail — the function reads `outcome.metadata`, `outcome.vault_path`, `outcome.content_hash`. |
| **A8** | Engine derives each result's domain by project→domain registry lookup (no per-note read); `Uncategorized` project counts only toward its project. | ✅ Validated | `registry.py` `groups` gives the mapping: iterate `groups.values()` → `group.domain_name` per `ProjectEntry.name`. **No built-in `domain_for_project()` helper** — engine must build the reverse map (cheap, in-memory). `Uncategorized` group (143-146) holds domain-less projects; engine treats `"Uncategorized"` as not-a-real-domain so those count only toward their project. Supported. |
| **A9** | `filter_paths` can gain a `location` folder-prefix `WHERE` on `vault_path` (no table, no migration); `None`-vs-`[]` sentinel preserved; `vault_path` has implicit UNIQUE index. | ✅ Validated | `filter_paths` (documents.py:393-447): `None` sentinel at 417-418, `[]` at empty match. Clauses are appended `WHERE` strings — a `location` clause fits the same pattern. `schema.sql:3` `vault_path TEXT NOT NULL UNIQUE` (implicit index). Query-plan/index-use for prefix `LIKE` vs `GLOB` is the deferred R2/TD-053 nuance — feasible regardless. |
| **A10** | `PRAGMA wal_autocheckpoint=100` is a safe one-line add to `_connect()`, keeps WAL + `foreign_keys=ON` + sqlite-vec load. | ✅ Validated | `db.py:16-25`: `journal_mode=WAL`, `foreign_keys=ON`, sqlite-vec load present; `wal_autocheckpoint` absent (grep: NONE FOUND). One-line add after the WAL pragma does not disturb FK or extension load. |
| **A11** | Wrapping each tool call in `copy_context().run(...)` isolates `new_correlation_id`'s `clear_contextvars()`; locatable in dispatcher. | ✅ Validated (the bleed risk is confirmed in our code; FastMCP-side placement is the open detail) | `logging_setup.py:71` `clear_contextvars()` then `bind_contextvars` (74) — bleed risk real. `copy_context().run()` isolation of contextvars is standard CPython behaviour; exact placement in FastMCP's async dispatch is OQ-004 (per-tool wrapper is the safe spot). |
| **A12** | Server bootstraps like CLI: `load_dotenv` once → `setup_logging` once → CONFIG validates vault root → `set_active(MoveGuard())`. | ✅ Validated (minor: CONFIG validates on first **access**, not import — lazy `__getattr__`) | `cli/main.py:16` load_dotenv once, `32` setup_logging once, `484` `set_active_guard(...)`. `config.py:627` CONFIG is lazy (`__getattr__`); the eager-load block is commented (607-620). Vault-root validation runs inside `load_config()` on first CONFIG access. Sequence reusable. |
| **A13** | Domain bundle is plain files (`CLAUDE.md` + domain `context.yaml`), file-exists fallback, `context.yaml` opaque text. | ⚠️ Partially unverifiable from code (no read site exists yet) — but consistent with what code supports | No code reads a domain `context.yaml` today (greenfield for Phase 4). Registry reads project `CLAUDE.md` from `Projects/<A>/CLAUDE.md` (registry.py:102) and domains live at `Domain/<D>/` (registry.py:90). Reading those files as opaque text with a file-exists fallback is feasible; TD-054 confirms no schema is assumed. Treat as Validated-by-design, not contradicted. |

---

## Edge Cases & Silent Failure Modes

Plain English: things that work but can bite a careless implementation.

- **`move_note` silently keeps the old project (A7 trap).** A `kms_move` that only calls `move_note(src,dst)` + `replace_path(old, outcome)` relocates the file and reindexes but leaves `project`/`domain` frontmatter pointing at the OLD home — because `move_note` re-reads `src`'s on-disk metadata and `replace_path` writes that same (old) metadata. The label only changes if the helper also writes the new metadata (the capture.py:962-968 `move_note` + `write_note(dst, new_meta)` pattern). The spec's Component-9 recipe omits this write step.
- **`None` vs `[]` sentinel (A9).** `Success(None)` = global; `Success([])` = filtered-nothing. The Search Coordinator already branches correctly (`search.py:98-103`). A `location` filter must keep this: an empty match returns `Success([])`, not `Success(None)`.
- **Filter-only / global path is O(N) (TD-053).** `_search_filter_only` (search.py:122-155) does `all_paths()` then one `get_by_path()` per note. `kms_vault_info` inbox-count derivation and bare `kms_search` ride this path — more traffic in the MCP server. Not a bug; monitor.
- **`created_at` can be date-only.** Per CLAUDE.md, date filters must use `updated_at` (always full-width). `filter_paths` already filters on `updated_at` (documents.py:428-433) — keep `location`/date filters on `updated_at`.
- **Contextvar bleed under concurrency (A11).** Two concurrent tool calls each calling `new_correlation_id()` would `clear_contextvars()` on each other without per-call `copy_context()` isolation.
- **Registry `Uncategorized` is a pseudo-domain.** It appears as a `groups` key (registry.py:143). The frequency engine must NOT count it as a real domain, or a vault full of unclassified projects would spuriously cross the threshold.
- **Missing context files degrade gracefully (TD-054).** A project/domain with no `CLAUDE.md`/`context.yaml` contributes no context block; search still returns cards. No code asserts these files exist.

---

## Dependencies & Coupling

Plain English: what the new server leans on, and what it must not break.

- **New external dep `mcp>=1.27,<2`** — co-resolves with pinned pydantic v2 / anthropic / sqlite-vec (A3). Formal `uv add` + lockfile refresh is an implementation step (this research used ephemeral `--with`, did not touch `pyproject.toml`/`uv.lock`).
- **`_connect()` is shared by ALL DB access** — the `wal_autocheckpoint` add lands in one chokepoint (`db.py:16`). C-04 (`foreign_keys=ON`) must stay (it does in the one-line add).
- **C-14 hook is live and will block.** `.claude/settings.json` matches `*/mcp_server/tools.py` and greps `^\s+(if |elif |for |while )` → `exit 2` (hard block). The shim/engine split is mandatory, not stylistic. Verified the matcher targets the exact path the spec uses.
- **`move_note` + `replace_path` + `MoveGuard.register` are the proven move triad** (capture.py:459-462 register-before-move; 466 move; 515 replace_path). `kms_move` must reuse this exact ordering.
- **Registry has no DB coupling** — `build_registry` is a folder scan; calling it per `kms_vault_info` is cheap (A5).

---

## Extension Points

Plain English: where Phase 4 can grow without touching the five tools.

- **Tool Shim Layer (`mcp_server/tools.py`)** — adding a sixth tool (e.g. `kms_write`, TD-056) is a new one-line shim + a new engine method; the existing five are untouched. C-14 keeps all logic in the engine.
- **`filter_paths` location filter** — one optional param on `filter_paths` + threaded through `search()`. The filter logic stays single-homed (the deletion test still passes).
- **Context Injection Engine** — the one deep module; threshold/cap/dedup/block-assembly all live here, read from `MCPConfig.context_injection` (to be added). Tunable via config, no code edits (C-06).
- **Blocked-by-design:** the C-14 hook prevents pushing any branching back into `tools.py`. Any future tool that needs an LLM call re-triggers C-07/C-08/C-13 — the five MVP tools make no AI decision (extract/move/read/registry only), so no audit write is required for them.

---

## Open Questions

Plain English: things code alone cannot fully answer; each notes what was checked.

- **OQ-004 / A11 — exact `copy_context().run(...)` placement in FastMCP dispatch.** Checked: our `new_correlation_id` bleed is real; `copy_context` isolation is standard CPython. What's not determinable from our code is whether FastMCP already runs each tool in an isolated task/context or whether a per-tool wrapper is needed — that's a behaviour of the framework's async dispatch. Safe answer: wrap the engine-dispatch call per tool. Confirm against FastMCP's request handler at implementation.
- **A13 — domain `context.yaml` read path.** Checked: no code reads it today (greenfield). Treating it as opaque text with a file-exists fallback is feasible and matches TD-054's "no schema". Cannot be code-verified until the engine is built; flagged as design-consistent, not contradicted.
- **A3 full-lockfile resolve.** Checked the high-risk co-resolution (pydantic v2 + anthropic + mcp) passes via `--with`. The complete `uv sync` with the full pin set is the implementation step; no contradiction found.

---

## Technical Debt Spotted

- **TD-053 (existing, confirmed)** — filter-only/global search is O(N) (`search.py:122-155`); the MCP server exercises it more (`kms_vault_info`, bare `kms_search`). Monitor.
- **TD-007 (existing, resolved by Component 1)** — `wal_autocheckpoint` absent; the one-line add closes it.
- ~~**A7 recipe gap (for the planner)** — Component 9's literal "call `move_note` to change a note's project" was incomplete.~~ **resolved (re-check 2026-06-11):** the patched Component 9 now adds the explicit `write_note(dst, new_meta)` step. A NEW spec-text error (A7b: `replace_path(old, dst)` passed the wrong second argument) was then **✅ RESOLVED (2026-06-11)** — Component 9 corrected to `replace_path(old_vault_path, outcome)`. No tech debt remains. See Invalidated Assumptions / Update below.

---

## Invalidated Assumptions

**Re-check verdict (2026-06-11):** A1 and A7 (their concepts) are now **Resolved** — the orchestrator's patch is code-accurate (see Update section). **One NEW invalidation surfaced during re-check (A7b)** — the patched Component 9 recipe wrote the wrong second argument to `replace_path`. **A7b is now ✅ RESOLVED (orchestrator fix, 2026-06-11, user-accepted without a confirmation re-check):** Component 9 (spec line 387) was corrected to `replace_path(old_vault_path, outcome)` — the exact call this research already verified against `documents.py:232` + `capture.py:986-987`. No Q4 conflict diagram (mechanical text fix). **No live blocking item remains — all three (A1, A7, A7b) are Resolved; the spec is plan-ready.** Entries are kept below (struck through where superseded) so the record of what was wrong survives.

### A1 — ✅ RESOLVED — "process-lifespan (= per-conversation under stdio)"
~~**Spec claimed:** FastMCP exposes a **per-connection** lifespan/context object a tool reads at call time.~~
**Re-check:** ✅ RESOLVED. The patched spec (A1 row + Component 5 + OQ-P4-STATE) now says "process-scoped lifespan, reachable via `ctx.request_context.lifespan_context`, `ctx` excluded from schema, = per-conversation under stdio, no Option B fallback." Re-verified against `mcp>=1.27,<2`: `Server.run` source line `lifespan_context = await stack.enter_async_context(self.lifespan(self))` (once per process); `FastMCP.__init__` takes `lifespan`; `RequestContext.lifespan_context` is a real dataclass field; a `kms_demo(query, ctx)` tool exposes only `['query']` (ctx auto-detected as `context_kwarg`, excluded from the public schema). All three patched claims are code-accurate. Option A stands.

### A7 — ✅ RESOLVED (concept) — `move_note` carries no metadata; helper writes new metadata around the move
~~**Spec claimed:** `move_note(src,dst,actor="ai")` uses the **incoming** metadata as authoritative.~~
**Re-check:** ✅ RESOLVED for the concept. The patched A7 row + Component 9 now correctly state `move_note` takes no metadata param, re-reads `src` (writer.py:202), merges only its own on-disk metadata (`incoming=current.metadata`, writer.py:216), and blocks human-locked AI moves (writer.py:208-213). The corrected recipe's order — register guard → `move_note(src, dst, actor="ai")` → `write_note(dst, new_meta, actor="ai")` → `replace_path(...)` — matches the proven capture pattern at capture.py:961-988. The "caller owns the merge (C-03)" note is correct: `write_note` is a pure writer (writer.py docstring line 11; `_merge_metadata` only auto-preserves `created`), so `new_meta` must carry every field the note should keep, built from `read_note(src)` with project/domain overridden. **One detail is still wrong — see A7b.**

### A7b — ✅ RESOLVED (orchestrator fix 2026-06-11) — was: wrong second argument to `replace_path` in the Component 9 recipe
**Spec claims (patched):** Component 9 recipe writes `then call replace_path(old, dst) to fix the index` (also the A7 spec-table row: "Proven sequence: `move_note` → `write_note(dst, new_meta)` → `replace_path`" with the body recipe spelling it `replace_path(old, dst)`).
**Code shows:** `documents.py:232` — `replace_path(old_vault_path: str, outcome: WriteOutcome, db_path=None, batch_id=None)`. The second argument is the **`WriteOutcome`** returned by the post-move `write_note(dst, new_meta, actor="ai")`, **not** the destination `Path`. Internally `replace_path` reads `outcome.metadata`, `outcome.vault_path`, and `outcome.content_hash` to build the new documents row (documents.py:254-291). The proven call passes the outcome: `replace_path(old_vault_path, outcome, db_path=..., batch_id=...)` (capture.py:986-987; same shape at capture.py:515-516). Note also the first arg is the **old vault_path string** (`to_vault_path(src)` captured *before* the move, capture.py:961), not `dst`.
**Why this matters:** A `kms_move` built to the literal `replace_path(old, dst)` would pass a `Path` where a `WriteOutcome` is expected — the index update would fail at runtime (attribute access on a `Path`), leaving the file moved and re-labelled on disk but the search index still pointing at the old path. The note would be findable under its old location and invisible under its new one — a silent index/disk divergence.
**Suggested resolution directions:** (1) Change the Component 9 recipe (and the A7 row) from `replace_path(old, dst)` to `replace_path(old_vault_path, outcome, ...)`, where `old_vault_path = to_vault_path(src)` captured before the move and `outcome` is the `WriteOutcome` returned by the post-move `write_note`. (2) Make explicit in the recipe that the first argument is the pre-move vault path *string*, captured before `move_note` runs (mirrors capture.py:961).
**✅ Applied (2026-06-11):** Component 9 (spec line 387) now reads `capture old_vault_path = to_vault_path(src)` before the move → `outcome = write_note(dst, new_meta, actor="ai")` → `replace_path(old_vault_path, outcome)` — matching `documents.py:232` + the proven `capture.py:986-987`. User accepted the fix without a confirmation re-check. **A7b closed.**

### Re-check new-contradiction scan — no other contradiction found
- **C-02 (`updated_by_human`) does NOT interfere with the post-move `write_note(dst, ...)`.** `move_note(..., actor="ai")` writes `dst` with `updated_by_human=False` (`_merge_metadata`, writer.py:76). The subsequent `write_note(dst, new_meta, actor="ai")` reads `dst`, sees `updated_by_human=False`, so the gate (writer.py:147-152) does not block, and it re-writes `updated_by_human=False`. No wrongful lock. If the *original* `src` was human-locked, `move_note` itself returns `Failure` (writer.py:208-213) before any move — the correct, intended surfacing for `kms_move`.
- **`move_note` does NOT update the index — `replace_path` is required, not redundant.** `writer.py` has zero `storage`/`documents` imports (FS-only; docstring line 10). Neither `move_note` nor `write_note` touches the documents/FTS/vec tables. So the separate `replace_path` step is necessary and is not double-counting.

---

## Two design code-corrections (already in the spec — confirmed, do NOT revert)

Both spec corrections carried from the design doc are **confirmed accurate by code**:
1. **Project/domain names come from the live Project Registry, not a `meta.yaml`** — confirmed: `registry.py` ships and is used by capture; no `meta.yaml` read exists. (A5.)
2. **Cards do NOT carry `attachment_path`; the binary signal is `note_type=="attachment-summary"`** — confirmed: `reranker.py:154-161` / `search.py:34-41` metadata bag; `attachment-summary` is a real type (capture.py:1213). `kms_inspect` resolves the binary from sibling `attachment_path` frontmatter (A6). (A4.)

**STATE.md correction (still needed):** STATE.md line ~163 still says the Project Registry is "PENDING implementation" — it is **shipped** (`vault/registry.py`). Update when STATE.md is next touched.

---
## Update — 2026-06-11
### Re-check findings (scope: A1, A7 + new-contradiction scan)

This pass re-verified the two previously-invalidated assumptions against the orchestrator's patched spec AND the real code, then scanned the two patches for new contradictions.

**Resolved:**

| ID | Was (original wrong claim) | Now (patched + code-confirmed) | Evidence |
|----|----------------------------|--------------------------------|----------|
| A1 | FastMCP exposes a **per-connection** lifespan/context a tool reads at call time. | Process-scoped lifespan entered once per `Server.run`; reachable via auto-injected `ctx.request_context.lifespan_context`; `ctx` excluded from public schema; = per-conversation under stdio; no Option B fallback. | `mcp>=1.27,<2`: `Server.run` source `lifespan_context = await stack.enter_async_context(self.lifespan(self))`; `RequestContext.lifespan_context` real field; `kms_demo(query, ctx)` schema = `['query']` only (`context_kwarg='ctx'`). All patched claims match. |
| A7 (concept) | `move_note` uses **incoming** metadata as authoritative, so build new project/domain into metadata before calling it. | `move_note` takes NO metadata param (re-reads `src`); set new project/domain via a separate `write_note(dst, new_meta, actor="ai")` AFTER the move; `move_note` blocks human-locked AI moves. | writer.py:181-244 (no metadata param, reads src @202, merges own meta @216, human-lock block @208-213); `write_note` pure writer @114-178; proven order capture.py:961-988. |

**New invalidated assumptions:**

**A7b — wrong second argument to `replace_path` in the Component 9 recipe (introduced by the A7 patch).**
- **Spec (patched) claims:** Component 9 — "then call `replace_path(old, dst)` to fix the index."
- **Code shows:** `documents.py:232` → `replace_path(old_vault_path: str, outcome: WriteOutcome, db_path=None, batch_id=None)`. Second arg is the `WriteOutcome` from the post-move `write_note(dst, new_meta)`, NOT the `dst` Path; it reads `outcome.metadata/vault_path/content_hash` (documents.py:254-291). Proven call: `replace_path(old_vault_path, outcome, ...)` (capture.py:986-987, 515-516). First arg is the pre-move vault-path string (`to_vault_path(src)`, capture.py:961), not `dst`.
- **Why it matters:** the literal recipe passes a `Path` where a `WriteOutcome` is expected → index update fails at runtime; file moves + re-labels on disk but the search index keeps pointing at the old path (silent index/disk divergence; defeats P4-MCP-07).
- **Fix:** rewrite to `replace_path(old_vault_path, outcome, db_path=..., batch_id=...)` where `old_vault_path = to_vault_path(src)` captured before `move_note`, and `outcome` is the post-move `write_note` result.

**New-contradiction scan — clean on the two patch questions:**
- C-02 `updated_by_human` gate does **not** block the post-move `write_note(dst, ...)`: `move_note(actor="ai")` writes `dst` with `updated_by_human=False`, so the gate (writer.py:147-152) passes and re-writes `False` — no wrongful lock. (If `src` was human-locked, `move_note` itself fails first — intended.)
- `move_note` does **not** update the index (writer.py is FS-only, zero `storage` imports) — so the separate `replace_path` step is required, not redundant or double-counting.

**Verdict:** of {A1, A7} → **2 Resolved, 0 still-invalidated**. A7b (the one-token `replace_path` argument error the A7 patch introduced) was **✅ RESOLVED (2026-06-11)** — Component 9 (spec line 387) corrected to `replace_path(old_vault_path, outcome, ...)`, the exact call verified here against `documents.py:232` + `capture.py:986-987`; user accepted the fix without a confirmation re-check. Everything else (the A1 mechanism, the A7 move→write→reindex order, the C-02 and index-double-count scans) is clean. **The spec is plan-ready — no live invalidations remain.**
