# Plan: Phase 4 — MCP Server Context Injection & Tool Design
_Last updated: 2026-06-11_
_Status: [ ] pending_

**Reads with:** spec `docs/2_specs/P4_mcp_context_injection.md` (the authoritative WHAT — 9 components, behavior IDs P4-MCP-01…09, assumptions A1–A13), research `docs/3_research/P4_mcp_context_injection.md` (the verification — all assumptions Validated/Resolved, no live invalidations), design `docs/1_design/P4_mcp_context_injection.md` (Option A rationale + Q1).

**Reader note (non-coder default).** Every phase leads with a plain-English purpose. Code references live in parentheses and sub-bullets. A glossary is at the end. The plan owns the HOW (build order, test-first ordering, exact line numbers, commit boundaries); it does **not** restate the spec's Build steps or Done-when — open the spec for those, by component ID.

---

## Architecture

### Q1 — What happens inside
The chosen internal flow is **Option A**: the per-conversation dedup memory lives on the conversation's own long-running server object; a tool reads the one engine off that object, the engine checks whether a context file was already sent this conversation, and returns context-blocks-first / content-second. Full diagram in the design doc (`docs/1_design/P4_mcp_context_injection.md` §Q1 + §Option A) and reproduced in the spec (`docs/2_specs/P4_mcp_context_injection.md` §Q1 Diagram). Not duplicated here.

### Q2 — How it connects
The chat client talks to the new server front door; the front door routes to five thin tool wrappers; the wrappers hand work to one decision engine plus two small helpers; the engine and helpers fan out to seven already-built components (search, registry, reader, filter, extractor, mover, index). Full diagram in the spec (`docs/2_specs/P4_mcp_context_injection.md` §Q2 Diagram). Not duplicated here.

### Q3 — Why build it this way
This diagram is the planning-stage rationale: it maps the rules and existing patterns the build must conform to onto the same picture as Q2, with a one-box reason on each link.

```
# MCP Context Injection — Why Build It This Way
Scope: The rules and existing patterns that shaped the Phase 4 build, mapped
       onto the same picture as "How It Connects" (Q2). Does NOT show the
       inject math (threshold/cap/dedup) — see Q1 for that.

How to read this:
  Center column     = the new feature being built
  Solid boxes       = existing components reused as-is
  Dashed box        = the per-conversation holder the Shell creates
  Speech boxes (◇)  = the RULE or REASON that shapes the link it sits on
  Arrows            = "calls / hands work to"

  ┌─────────────────────┐
  │ Claude Desktop      │   the external chat client
  │ (AI client)         │
  └──────────┬──────────┘
             │ connects over stdio, calls the five tools
             ▼
  ┌─────────────────────┐      creates & holds one     ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  │ MCP Server Shell    │ - - - - - - - - - - - - - - ►│ Conversation         │
  │ front door;         │                              │ Lifespan Object      │
  │ startup + per-call  │                              │ holds the one engine │
  │ isolation           │                              └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
  └──────────┬──────────┘                                        ▲
             │                  ◇──────────────────────────────────────────◇
             │                  │ WHY held here: one stdio process = one     │
             │                  │ conversation, so the conversation's own    │
             │                  │ lifespan object IS the per-conversation    │
             │                  │ memory — no process-global needed          │
             │                  ◇──────────────────────────────────────────◇
             │
   ◇─────────────────────────────────────────◇   ◇──────────────────────────────────◇
   │ STARTUP rule: bootstrap exactly like the  │   │ ISOLATION rule: run each tool call │
   │ existing CLI front door — load env once,  │   │ in its own context copy so two     │
   │ set up logging once, validate vault root, │   │ in-flight calls don't wipe each    │
   │ publish one move-guard. Reuse a proven    │   │ other's trace id                   │
   │ sequence; don't invent one (C-11, C-10)   │   ◇──────────────────────────────────◇
   ◇─────────────────────────────────────────◇
             │
             │ routes each call
             ▼
  ┌─────────────────────┐        ◇──────────────────────────────────────────◇
  │ Tool Shim Layer     │◄───────│ C-14 rule: shims stay logic-free. A hard   │
  │ the 5 thin tools:   │        │ hook BLOCKS any branch/loop/count here —   │
  │ vault_info · search │        │ every decision must live downstream in the │
  │ read · inspect ·    │        │ Engine and the two helpers                 │
  │ move                │        ◇──────────────────────────────────────────◇
  └──────────┬──────────┘
             │ hands the request to
             ▼
  ┌─────────────────────┐   reads tuning   ┌──────────────────┐
  │ Context Injection   │ ◄─────from───────│ Config           │
  │ Engine              │                  │ threshold, cap,  │
  │ counts, gates,      │                  │ include-context  │
  │ dedups, assembles   │                  └──────────────────┘
  │ response blocks     │        ◇──────────────────────────────────────────◇
  └──────────┬──────────┘◄───────│ C-06 rule: the two tuning numbers (how     │
             │                   │ concentrated results must be, how many     │
             │                   │ files at most) are READ from config, never │
             │                   │ hardcoded — a hook blocks threshold        │
             │                   │ literals                                   │
             │                   ◇──────────────────────────────────────────◇
             │ delegates inspect / move to
             ▼
  ┌─────────────────────┐   ┌─────────────────────┐
  │ Binary Resolver     │   │ Note Mover Helper   │
  │ Helper              │   │ resolves dest,      │
  │ sibling↔binary for  │   │ moves + reindexes   │
  │ inspect             │   │                     │
  └──────────┬──────────┘   └──────────┬──────────┘
             │                         │
   ◇────────────────────────◇   ◇──────────────────────────────────────────◇
   │ NO-AI rule: re-runs the  │   │ MOVE-ORDER rule: move → write → reindex,   │
   │ existing text extractor  │   │ in that order, because the mover carries   │
   │ on the original binary.  │   │ NO metadata (it re-reads the source). A    │
   │ No AI call → no new      │   │ separate write sets the new project/domain │
   │ prompt, no audit entry   │   │ label; the reindex re-points the index     │
   ◇────────────────────────◇   │ using the write's outcome (C-03; A7/A7b)   │
             │                   ◇──────────────────────────────────────────◇
             │   both the Engine and these helpers      │
             │   fan out to ALREADY-BUILT components    │
             ▼                                          ▼
  ════════════════ already-built (reused as-is) ════════════════
                                                                 ◇──────────────────────◇
  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐        │ C-15 rule: every tool  │
  │ Search        │  │ Project       │  │ Note Reader   │ ◄──────│ wraps a pipeline that  │
  │ Coordinator   │  │ Registry      │  │ loads one     │        │ is ALREADY built and   │
  │ runs search,  │  │ project→      │  │ note from     │        │ tested — no tool is    │
  │ returns cards │  │ domain lists  │  │ disk          │        │ exposed before its     │
  └───────────────┘  └───────┬───────┘  └───────────────┘        │ backing path has tests │
                             ▲                                    ◇──────────────────────◇
                     ◇───────────────────────────────────◇
                     │ DOMAIN rule: the Engine derives     │
                     │ each result's domain by a CHEAP     │
                     │ project→domain registry lookup (no  │
                     │ per-note read); "Uncategorized" is  │
                     │ treated as not-a-real-domain        │
                     ◇───────────────────────────────────◇

  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
  │ Candidate     │  │ Binary        │  │ Note Mover    │
  │ Filter        │  │ Extractor     │  │ safe move +   │
  │ project/date/ │  │ raw text by   │  │ move guard    │
  │ location      │  │ file type     │  │               │
  └───────────────┘  └───────────────┘  └───────────────┘

  ┌──────────────────────────────────────────────────────┐
  │ Note Catalog / Index                                  │
  │ SQLite documents table + keyword/vector search index  │
  └──────────────────────────────────────────────────────┘
```

Simplified: the seven already-built components stay grouped as one tier (same as Q2) instead of seven spokes; the rationale boxes (◇) sit on the links they explain rather than adding new arrows. The inject math is still hidden in the single Engine box (see Q1).

---

## Approach

Build bottom-up so every layer rests on a tested layer below it. The three small prerequisites (database checkpoint, config block, the new dependency, the `location` filter) land first because they touch existing files and have no dependencies. Then the server shell stands up the long-running front door and the per-conversation isolation. Then the Context Injection Engine — the one place all branching lives — is built and tested directly, with no MCP framework in the loop. Only after the engine and the two helpers exist and are tested do the five thin tool shims get written (C-15: no tool before its backing path is tested; C-14: the shims must stay logic-free). This order also matches the C-14 split: the shim layer is the *last* thing built, on top of fully-tested engines, so there is never a temptation to put logic in it.

Why not top-down (shell + tools first): a tool shim that calls an engine method that does not exist yet is a stub (forbidden by C-15), and the C-14 hook would have nothing real to wrap. Building the logic first and the shim last keeps every commit shippable and testable.

**TDD throughout.** Each phase names its RED test (write first, watch it fail) and its GREEN checkpoint (implementation makes it pass). The project runs `/tdd-implement` against this plan, so phases are written test-first.

**Extension-point marking** (per component this plan introduces):
- MCP Server Shell — `[extensible: config]` (transport/host/port from config; HTTP toggle deferred).
- Tool Shim Layer — `[closed by design]` — adding a 6th tool is a new one-line shim + new engine method; the existing five are untouched. Its shallowness is *mandated* by C-14, not a smell (a sixth tool like `kms_write`/TD-056 slots in without modifying the five).
- Context Injection Engine — `[extensible: config]` (threshold/cap/toggle tunable without code) — the one deep module; all branching lives here.
- Binary Resolver Helper — reuses the existing handler **registry** (`[extensible: registry]` downstream — a new file-type extractor self-registers; the resolver is unchanged).
- Note Mover Helper — `[closed]` — single-purpose move recipe; no variants expected.
- `location` filter — extends two existing functions (deepens `filter_paths`, does not add a module).

---

## Phases

Build order (bottom-up). Spec component IDs in brackets.

| # | Phase | Spec component(s) | Behavior IDs it advances |
|---|-------|-------------------|--------------------------|
| 1 | Prerequisites: WAL checkpoint + config block + `mcp` dep + `location` filter | Components 1, 2, 3, 4 | (foundation for 02–09) |
| 2 | MCP Server Shell (bootstrap + per-call isolation) | Component 5 | P4-MCP-01 |
| 3 | Context Injection Engine | Component 6 | P4-MCP-02, 03, 04, 05, 08, 09 |
| 4 | Binary Resolver Helper | Component 8 | P4-MCP-06 |
| 5 | Note Mover Helper | Component 9 | P4-MCP-07 |
| 6 | Tool Shim Layer + register on shell + end-to-end | Component 7 | P4-MCP-01 (all five listed) |
| 7 | TD-055 AI usage instructions (delivery format decided here) | — (not a code component; ships alongside) | — |

---

### Phase 1 — Prerequisites: WAL checkpoint, config block, `mcp` dependency, `location` filter
**Goal**: Land the four no-dependency groundwork pieces so the server, engine, and search tool have what they need. Each is independently testable and touches existing files only.

**Design**:
This phase has four independent sub-steps. They share a phase because none depends on another and each is small. Ordered so the riskiest (the new dependency install) is bracketed by tests on both sides.

```
Sub-step          Touches                       Proves
1a WAL checkpoint  storage/db.py::_connect       a new connection has the checkpoint
                                                 setting AND still has foreign-keys ON
1b config block    core/config.py::MCPConfig     CONFIG.main.mcp.context_injection.*
                   config/config.yaml mcp:        reads the defaults; missing block
                                                 still validates
1c mcp dependency  pyproject.toml + uv.lock      `from mcp.server.fastmcp import
                                                 FastMCP, Context` imports
1d location filter documents.py::filter_paths    a folder-scoped search returns only
                   retrieval/search.py::search    notes under that folder; unscoped
                                                 behaves as before; None-vs-[] kept
```

**Steps** (TDD — RED before GREEN per sub-step):

1. **1a WAL checkpoint** — implements spec **Component 1** (Done-when + A10 in spec; do not restate).
   - RED: add a test that opens a fresh connection (`get_connection(db_path=tmp/"kb.db")`) and asserts `PRAGMA wal_autocheckpoint` returns `100` AND `PRAGMA foreign_keys` returns `1`. Put it in `tests/test_storage/test_db.py` (create if absent). It fails (default is 1000).
   - GREEN: add one line `conn.execute("PRAGMA wal_autocheckpoint=100")` to `_connect()` (`src/storage/db.py:16-25`), placed after the existing `journal_mode=WAL` line and leaving `foreign_keys=ON` and the sqlite-vec load untouched (verified absent today, research A10 / `db.py:18-24`).
   - Commit: `feat(P4): wal_autocheckpoint=100 in _connect (TD-007)`.

2. **1b config block** — implements spec **Component 2** (C-06).
   - RED: add a test that loads config from a YAML *without* a `context_injection` block and asserts the three documented defaults appear (`frequency_threshold == 0.3`, `max_context_files == 3`, `include_context_yaml is True`), and a second that a YAML *with* an override changes the value. Use an explicit config-load path, not module-scope `CONFIG` (C-17).
   - GREEN: add a nested Pydantic model `ContextInjectionConfig` (fields with the three defaults) and a `context_injection: ContextInjectionConfig = Field(default_factory=...)` field on `MCPConfig` (`src/core/config.py:244-249`). Add the matching block under `mcp:` in `src/config/config.yaml` (currently lines 35-38: `enable_http`, `host`, `port`). The default-factory means an absent YAML block still validates.
   - Commit: `feat(P4): mcp.context_injection config block (C-06)`.

3. **1c `mcp` dependency** — implements spec **Component 3** (A3, approved OQ-P4-DEP).
   - RED: add a test (marked so it is skippable pre-install) that does `from mcp.server.fastmcp import FastMCP, Context` and asserts import succeeds. It fails before install.
   - GREEN: add `mcp>=1.27,<2` to `[project].dependencies` in `pyproject.toml`; run `uv sync` to refresh `uv.lock`. (Research A3 verified co-resolution with pinned pydantic v2 / anthropic / sqlite-vec via ephemeral `--with`; the formal lockfile resolve is this step.)
   - **GATE — human confirmation already given.** The locked decision approves `mcp>=1.27,<2`. The implementer runs `uv sync` (a dependency install — flagged here so the implementer knows it is the one approved install; do not add any other package).
   - Commit: `build(P4): add mcp>=1.27,<2 dependency (OQ-P4-DEP)`.

4. **1d `location` filter** — implements spec **Component 4** (A9; Risk R2 shape deferred — see Open Questions).
   - RED: add tests in `tests/test_retrieval/` (or `tests/test_storage/test_documents.py` for the filter-level test): (i) `filter_paths(location="inbox", db_path=...)` returns only vault_paths physically under `inbox/`; (ii) `filter_paths()` with no args still returns `Success(None)` (global sentinel preserved); (iii) a `location` that matches nothing returns `Success([])` not `Success(None)`; (iv) `search(query=..., db_path=...)` threads a `location` through and scopes results. They fail (no `location` param yet).
   - GREEN:
     - Extend `filter_paths()` (`src/storage/documents.py:393-447`) with an optional `location: str | None = None` parameter. Add a folder-prefix `WHERE` clause on `vault_path` to the existing appended-clause list (lines 420-433 build `clauses`/`params`). Keep the `None`-vs-`[]` contract: the early `Success(None)` guard at line 417 must now also account for `location` being `None` (i.e. `if project is None and since is None and until is None and location is None: return Success(None)`).
     - Thread `location` through `search()` (`src/retrieval/search.py:50-114`): add `location: str | None = None` to the signature and pass it into the `filter_paths(...)` call at line 95.
   - **Decision deferred to research-confirm at implementation (R2):** the exact prefix-match shape — `LIKE 'inbox%'` vs `GLOB 'inbox/*'` vs range bounds. Correctness first (must match only the folder subtree, not a sibling like `inbox-archive/`); index-use tuning is low-urgency on a small vault. The plan does NOT lock the SQL operator — see Open Questions. Whatever shape is chosen, the test (i) above (only-under-`inbox/`) is the correctness gate.
   - Commit: `feat(P4): location folder filter on filter_paths + search (Component 4)`.

**Files to modify**:
- `src/storage/db.py` — one pragma line in `_connect()`.
- `src/core/config.py` — `ContextInjectionConfig` model + field on `MCPConfig`.
- `src/config/config.yaml` — `mcp.context_injection` block.
- `pyproject.toml` + `uv.lock` — `mcp>=1.27,<2`.
- `src/storage/documents.py` — `location` param on `filter_paths`.
- `src/retrieval/search.py` — `location` param threaded through `search`.
- Tests: `tests/test_storage/test_db.py`, `tests/test_core/test_config.py`, `tests/test_storage/test_documents.py` (or `tests/test_retrieval/`).

**Test criteria**:
- [ ] Fresh connection reports `wal_autocheckpoint=100` and `foreign_keys=1`.
- [ ] Absent `context_injection` YAML block loads with documented defaults; an override changes the value.
- [ ] `from mcp.server.fastmcp import FastMCP, Context` imports after `uv sync`.
- [ ] Folder-scoped `filter_paths`/`search` returns only notes under the folder; unscoped is unchanged; `None`-vs-`[]` sentinel preserved.
- [ ] Full suite stays green (`uv run pytest tests/`); existing `filter_paths`/`search` tests still pass (the deletion/single-home test for the filter still holds).

**Notes / coupling**: 1c is the one approved dependency install. C-04 (foreign-keys) must remain in `_connect` — the pragma add is additive. No migration (C-05): `location` is a query clause, not DDL.

**Status**: [ ] pending

---

### Phase 2 — MCP Server Shell (bootstrap + per-call isolation)
**Goal**: Stand up the long-running front door Claude Desktop connects to, bootstrap it once exactly like the CLI, create one Context Injection Engine per conversation on the framework's lifespan, and run each tool call in its own context copy so two in-flight calls don't scramble each other's trace id.

**Design**:
```
Server process starts (one stdio process = one conversation)
        │
        ▼
┌────────────────────────────────────────────────┐
│ Bootstrap (mirror cli/main.py exactly):          │
│  1. load_dotenv once at the top (C-11)           │
│  2. setup_logging once (C-10/C-11)               │
│  3. import CONFIG → validates vault root          │
│  4. set_active(MoveGuard())  (so kms_move works) │
└───────────────────────┬──────────────────────────┘
                        ▼
┌────────────────────────────────────────────────┐
│ FastMCP app created with a lifespan that yields  │
│ {"engine": ContextInjectionEngine(...)}          │
│  — entered ONCE per process = once per           │
│    conversation under stdio (research A1)         │
└───────────────────────┬──────────────────────────┘
                        ▼
┌────────────────────────────────────────────────┐
│ Each tool call wrapped in copy_context().run(...)│
│  so new_correlation_id()'s clear_contextvars()   │
│  cannot wipe a sibling call's trace id (A11)     │
└──────────────────────────────────────────────────┘
```

**Steps**: implements spec **Component 5** (read its Build + Done-when; A1, A2, A11, A12).

1. RED (bootstrap order): write a test that imports the server module's bootstrap function and asserts, with `load_dotenv`/`setup_logging`/`set_active` patched, that they are called in the order load_dotenv → setup_logging → (CONFIG access) → set_active, exactly once each. Mirror the CLI's proven sequence (`cli/main.py:16` load_dotenv, `:32` setup_logging, `:483-485` set_active_guard). It fails (no server module yet).
2. RED (lifespan holds one engine): write a test using the MCP framework's in-memory/test transport (research handoff: "test the shell with the framework's in-memory/test transport and a mock engine") that connects and asserts the lifespan context exposes exactly one engine instance reachable via `ctx.request_context.lifespan_context["engine"]`, and that the engine is the same object across two calls in one connection. It fails.
3. RED (context isolation): write a test that fires two concurrent dummy tool calls, each calling `new_correlation_id()`, and asserts each call's correlation id survives (no cross-wipe). This proves the `copy_context().run(...)` wrap. It fails without the wrap. (`core/logging_setup.py:71` `clear_contextvars()` is the bleed source — research A11.)
4. GREEN: create `src/mcp_server/__init__.py` and `src/mcp_server/server.py` (and a `__main__` entry). Implement:
   - the bootstrap (load_dotenv once at top of the entry, setup_logging once, import CONFIG, `set_active(MoveGuard())` from `vault/move_guard.py`);
   - an `@asynccontextmanager` lifespan passed to `FastMCP(lifespan=...)` that yields `{"engine": ContextInjectionEngine(...)}`;
   - a per-tool-call wrapper using `contextvars.copy_context().run(...)` around the engine dispatch.
   - Wire the async entry per the project's async contract (C-10): the FastMCP run is the event-loop owner; do not nest a second `asyncio.run`. (Component 5 is `dependency-category: true-external`.)
   - **The engine is a stub at this phase only insofar as it has no methods yet** — but Phase 3 builds it next and Phase 6 registers the tools. Crucially: **no tool is registered in this phase** (C-15 — tools come after the engine + helpers are tested). The shell stands up and lists *zero* tools until Phase 6. (P4-MCP-01 "lists exactly the five tools" completes in Phase 6; this phase proves the shell starts, bootstraps, and isolates calls.)

5. GREEN (isolation): place the `copy_context().run(...)` per dispatched tool call. **Exact placement deferred to research-confirm against the real FastMCP dispatch** (OQ-004 / A11): per-tool wrapper is the safe default; confirm whether FastMCP already runs each tool in its own task/context. See Open Questions.

**Files to modify**:
- `src/mcp_server/__init__.py` — new (package marker).
- `src/mcp_server/server.py` — new (bootstrap + lifespan + dispatch wrap + `__main__`).
- Tests: `tests/test_mcp_server/test_server.py` — new (bootstrap order, lifespan-holds-one-engine, context isolation). Use the framework's test transport + a mock/real engine; no module-scope CONFIG (C-17).

**Test criteria**:
- [ ] Bootstrap calls load_dotenv → setup_logging → CONFIG → set_active in order, once each.
- [ ] Connecting over the test transport exposes exactly one engine on the lifespan context; same object across two calls in one connection.
- [ ] Two concurrent dummy calls each keep their own correlation id (no cross-wipe).
- [ ] No `asyncio.run` nesting; the FastMCP run owns the loop (C-10).

**Notes / coupling**: `[extensible: config]`. **C-11 boundary:** `load_dotenv` lives ONLY at the top of the server entry (`server.py`) — never inside the library modules `context.py`, `_resolve.py`, or `_move.py`. Depends on Phase 1 (config block, the `mcp` dep). Instantiates the engine from Phase 3 — so Phase 3's engine constructor must exist before Phase 2's GREEN fully passes; in practice build Phase 3's engine skeleton (constructor + empty dedup memory) first if needed, or land Phase 2's lifespan against a tiny placeholder engine and swap to the real one when Phase 3 lands. Recommended: build the engine constructor in Phase 3 RED-first, then return to Phase 2 GREEN. (The plan keeps them as separate phases for testability; the implementer may interleave the engine constructor.)

**Status**: [ ] pending

---

### Phase 3 — Context Injection Engine
**Goal**: Build the one brain that counts how concentrated search results are, decides how much background to attach, remembers what it already sent this conversation, and assembles every tool's response with context blocks first and content second. This is where all the branching lives (so the tools file can stay logic-free).

**Design**:
```
Engine (one per conversation; holds a content-hash dedup memory)
  build_search_response(query, filters, include_context)
        │  1. call Search Coordinator → cards (project, note_type)
        │  2. for each card: derive its domain via project→domain
        │     registry lookup (Uncategorized = not a real domain)
        │  3. count concentration; compare top share to threshold (config)
        │            ┌── below ──► attach NO context, return cards only
        │            └── at/above ─► take top few (cap from config),
        │                            drop ones in dedup memory (replace
        │                            with "already provided" note unless
        │                            include_context forces full),
        │                            read each chosen CLAUDE.md (+ domain
        │                            context.yaml) with file-exists fallback
        ▼
  Response = [context blocks first] + [result cards second]

  build_vault_info_response()
        │  loop registry groups/projects → names; count inbox notes +
        │  last-capture time from catalog; read vault-root CLAUDE.md;
        │  same dedup
  build_read_response(paths, include_context)
        │  loop paths → read_note each; inject any not-yet-sent
        │  minority-domain context first; binary-backed note returns
        │  its summary body (not bytes)
```

**Steps**: implements spec **Component 6** (read its Build + the six Done-when bullets; A4, A5, A8, A13). The plan's job is the TDD order and the two decisions confirmed below.

1. RED (engine skeleton + dedup memory): test that a fresh engine has an empty dedup memory and that recording a context file's content hash makes a second send of the *same* content dedup, while *different* content (an edited file → new hash) re-sends. Drives the **content-hash** dedup decision (locked: hash file content, not path+mtime — an edit mid-conversation should re-send; CONTEXT.md "hash-dedup session state").
2. RED (frequency → threshold → cap, P4-MCP-03/04): with a stubbed search returning cards concentrated on one project, assert context blocks precede cards and are capped at the configured cap; with cards spread across many projects/domains (below threshold), assert **zero** context blocks. Threshold/cap read from `CONFIG.main.mcp.context_injection` (C-06 — no float literal in the engine's `if`).
3. RED (project→domain derivation, A8/OQ-P4-DOMAIN): with a stubbed registry, assert each card's domain is derived by **registry lookup** of the card's `project` (no per-note read), and that a card whose project is `Uncategorized` contributes only to its project's count, never to a domain's. (Locked decision: registry lookup; `Uncategorized` is not a real domain — research A8 confirms `registry.py:143-146` holds the pseudo-domain; the engine builds the reverse project→domain map in-memory since there is no built-in `domain_for_project()` helper.)
4. RED (dedup + force, P4-MCP-08/09): a second focused search on the same domain in one conversation replaces the full context with a short "already provided" note; `include_context=True` forces full re-injection even after dedup.
5. RED (vault_info, P4-MCP-02): `build_vault_info_response()` returns project + domain names from the live registry (`build_registry` → `ProjectRegistry.groups` / `all_project_names`, `registry.py:50-60`), inbox count + last-capture time from the catalog (`all_paths`/`get_by_path`, `documents.py:178,146`), and the vault-root CLAUDE.md once. Registry is a folder scan, cheap, no DB (research A5) — read fresh each call.
6. RED (read, P4-MCP-05): `build_read_response(paths)` loads each note body via `read_note` (`vault/reader.py:35`), injects not-yet-sent minority-domain context first, and returns a binary-backed note's *summary body* (its `note_type == "attachment-summary"` card), not bytes.
7. RED (missing context files, TD-054): a project/domain with no `CLAUDE.md` and no `context.yaml` contributes no context block; search still returns cards (graceful degrade). `context.yaml` read as **opaque text** with a file-exists fallback (A13 — no schema; read path makes no structural assumption).
8. GREEN: create `src/mcp_server/context.py`. Implement the three `build_*_response` methods + the private helpers (concentration count, project→domain reverse-map builder, dedup memory, file-read-with-fallback, block assembler). All public methods return `Success`/`Failure` (C-12). **No new AI decision → no `audit.write` (C-13, stated explicitly):** every step is retrieval/registry/file-read; there is no `provider.complete()` here.

**Files to modify**:
- `src/mcp_server/context.py` — new (the engine; the one deep module).
- Tests: `tests/test_mcp_server/test_context.py` — new (one test class per build_* method + dedup + threshold + domain-derivation + degrade). Test directly with a temp vault + temp DB + a stubbed registry/search (research: `dependency-category: in-process`). No module-scope CONFIG (C-17); pass `db_path` explicitly.

**Test criteria**:
- [ ] Focused search → context blocks before cards, capped at config cap (P4-MCP-03).
- [ ] Broad query → zero context blocks (P4-MCP-04).
- [ ] Same content re-sent → deduped to "already provided"; edited content (new hash) → re-sent (P4-MCP-08).
- [ ] `include_context=True` → full re-injection (P4-MCP-09).
- [ ] `kms_vault_info` payload: project + domain names from live registry, inbox count, last-capture time, vault-root context once (P4-MCP-02).
- [ ] `kms_read` payload: minority-domain context before each body; binary-backed note returns summary body (P4-MCP-05).
- [ ] Missing context files → no block, cards still returned (TD-054).
- [ ] No `audit.write` and no `provider.complete()` anywhere in `context.py` (C-13/C-08 — stated, verified by grep).
- [ ] No float literal in any engine `if`/`elif` (C-06 — threshold/cap from config).

**Notes / coupling**: `[extensible: config]`. Depends on Phase 1 (config block, `location` filter so `kms_search` can pass it) and reuses Search Coordinator, Project Registry, Note Reader, Note Catalog reads. The engine is the single home for logic — adding a 6th tool later adds an engine method, not a branch in any shim. **Known coupling (`# COUPLING:`):** the engine reads `note_type == "attachment-summary"` as the binary signal (the only place a literal type-string is matched) — generalizing to other binary markers would mean a config-driven type list; not done now because there is one binary signal today (research A4, `capture.py:1213`, `tags.yaml:12`).

**Status**: [ ] pending

---

### Phase 4 — Binary Resolver Helper (`kms_inspect` backing)
**Goal**: Given either a summary note's path or a binary's own path, find the real binary and return its raw extracted text — no AI, no re-summarizing.

**Design**:
```
inspect(path):
  path ends in ".md" AND its frontmatter has attachment_path?
       ├─ YES ─► resolve attachment_path under vault root → binary
       └─ NO  ─► treat the given path as the binary itself
  pick extractor by extension (HandlerRegistry.resolve)
  run extractor (handler.extract) → RawContent.text
  return Success(raw text)          (no prompt, no LLM, no audit)
```

**Steps**: implements spec **Component 8** (read its Build + Done-when; A6).

1. RED: with a real fixture binary (e.g. a small PDF) + its sibling `.md` carrying `attachment_path` frontmatter, assert `inspect(sibling_md_path)` returns the raw extracted text resolved from `attachment_path` (`vault/frontmatter.py:72`, written at `capture.py:1214`).
2. RED: assert `inspect(binary_path)` (the binary directly) returns the same text.
3. RED: assert no AI call is made (no `provider.complete`, no prompt load) — e.g. patch the provider factory and assert it is never called.
4. RED: a `.md` path whose frontmatter has *no* `attachment_path` and is not itself a binary returns a clear `Failure` (C-12), not a crash.
5. GREEN: create `src/mcp_server/_resolve.py` (or equivalent). Implement the fallback chain: `read_note` to inspect frontmatter (`vault/reader.py:35`); resolve `attachment_path` under vault root (`to_vault_path` inverse / path join); `HandlerRegistry.resolve(binary)` (`handlers/registry.py:48`) → `handler.extract(binary)` (`handlers/base.py:81`) → return `RawContent.text` (`base.py:47`). Public function returns `Success`/`Failure` (C-12). No prompt (C-07 not triggered), no audit (C-13 not triggered) — it is text parsing, not a decision.

**Files to modify**:
- `src/mcp_server/_resolve.py` — new.
- Tests: `tests/test_mcp_server/test_resolve.py` — new. Use a real fixture binary + sibling note (research: `dependency-category: in-process`).

**Test criteria**:
- [ ] Inspecting a binary-backed result returns raw extracted text resolved from the sibling's `attachment_path` (P4-MCP-06).
- [ ] Passing the binary path directly yields the same text (P4-MCP-06).
- [ ] No AI call is made (provider factory never invoked).
- [ ] A `.md` with no `attachment_path` and no binary at the path returns `Failure`, not a crash.

**Notes / coupling**: reuses the handler `[extensible: registry]` — a new file-type extractor self-registers; the resolver is unchanged. Independent of Phase 3 (the engine); both are reached only through the shim in Phase 6.

**Status**: [ ] pending

---

### Phase 5 — Note Mover Helper (`kms_move` backing)
**Goal**: File a note into a named project or domain so its on-disk location, its frontmatter label, and the search index all agree — and the watcher doesn't undo it. A human-locked note surfaces a clear failure instead of being overwritten.

**Design** (the proven move recipe — **exact order matters**; matches `capture.py:961-988`):
```
move(src, dst_name, dst_kind):                  dst_kind ∈ {project, domain}
  1. resolve dst_name → dst folder path
        project → project_dir(name)   (vault/paths.py:418)
        domain  → domain_dir(name)    (vault/paths.py:441)
     compute dst = <folder>/<src.name>
  2. read_note(src) → build new_meta:            (C-03: caller owns the merge)
        project move → set project = dst_name, carry all other fields
        domain move  → set domain/<D> tag, clear project, set primary domain
  3. old_vault_path = to_vault_path(src)         ← capture BEFORE the move
  4. get_active().register(dst)                  ← register BEFORE the move
  5. move_note(src, dst, actor="ai")             ← carries NO metadata; re-reads
        │                                          src; BLOCKS human-locked moves
        └─ Failure(human-locked) ─► return it as a clear result (C-02)
  6. outcome = write_note(dst, new_meta, actor="ai")   ← sets the new label
  7. replace_path(old_vault_path, outcome)       ← 2nd arg is the WriteOutcome,
                                                   NOT a path (documents.py:232)
  return Success
```

**Steps**: implements spec **Component 9** (read its Build + Done-when; A7/A7b — the corrected recipe). **Do NOT regress to `replace_path(old, dst)`** — research A7b: the second arg is the `WriteOutcome` from step 6, read for `.metadata`/`.vault_path`/`.content_hash` (`documents.py:254-291`); passing a `Path` fails at runtime and silently diverges index from disk.

1. RED (project move, P4-MCP-07): in a temp vault with a temp DB, move a note from `inbox/` to a named project. Assert: file is at the new path; frontmatter `project` equals the destination; the index row points at the new path and the old search rows are cleared; the move guard was registered for the destination before the move.
2. RED (the A7b trap — a guard test): assert the helper calls `replace_path` with the `WriteOutcome` from the post-move `write_note` (not the `dst` path) — e.g. by capturing the call and asserting the second arg has `.metadata`/`.vault_path` attributes. This is the regression test that prevents reverting to `replace_path(old, dst)`.
3. RED (the A7 trap — label actually changes): assert that after the move the on-disk frontmatter `project`/domain matches the *new* home, proving the separate `write_note(dst, new_meta)` ran (a move-only path would leave the old label — research edge case).
4. RED (human-locked, C-02): move a note whose `updated_by_human` is set; assert `move_note` returns `Failure` and the helper surfaces it as a clear `Failure` result (not a crash, not an overwrite). Build the locked note via `write_note(..., actor="human")` (CLAUDE.md gotcha: `updated_by_human` is set from actor, not incoming metadata).
5. RED (order — register before move): assert `get_active().register(dst)` is called before `move_note` (the proven capture pattern, `capture.py:459-462`).
6. GREEN: create the mover helper (e.g. `src/mcp_server/_move.py`). Implement the seven-step recipe above. Resolve destination via `vault/paths.py` (`project_dir`/`domain_dir`). Build `new_meta` from `read_note(src)` with project/domain overridden (C-03 — carry all fields to keep). Capture `old_vault_path = to_vault_path(src)` before the move. Register the guard, call `move_note` (`vault/writer.py:181`), then `write_note(dst, new_meta, actor="ai")` (`writer.py:114`), then `replace_path(old_vault_path, outcome)` (`documents.py:232`). Return `Success`/`Failure` (C-12).

**Decisions confirmed (locked, from research/spec) — do not re-litigate:**
- **Move-guard register order:** register **before** the move (`capture.py:459-462`).
- **`replace_path` second arg:** the `WriteOutcome` (A7b), never `dst`.

**Decision deferred (Open Questions):** the **exact domain-move field set** (which fields to set/clear so derive-from-tags consistency holds: `domain/<D>` tag + cleared `project` + a designated primary domain) — confirm at implementation against the derive-from-tags rule (Risk R5, CONTEXT.md "derived routing"). The project-move path is fully specified and is the primary P4-MCP-07 acceptance path; the domain-move path lands once the field set is confirmed.

**Files to modify**:
- `src/mcp_server/_move.py` — new.
- Tests: `tests/test_mcp_server/test_move.py` — new. Temp vault + temp DB (research: `dependency-category: in-process`).

**Test criteria**:
- [ ] Project move: file relocated, frontmatter `project` updated, index re-pointed (old rows cleared), guard registered, watcher does not re-home (P4-MCP-07).
- [ ] `replace_path` is called with the `WriteOutcome`, not the `dst` path (A7b regression guard).
- [ ] On-disk label matches the new home after the move (A7 — the separate write ran).
- [ ] Human-locked note → clear `Failure`, no overwrite (C-02).
- [ ] Guard registered before the move (order).

**Notes / coupling**: `[closed]`. Depends on Phase 2 (the shell must have published a move guard via `set_active(MoveGuard())`); reuses Note Mover, index updater, registry/paths. Closes TD-057.

**Status**: [ ] pending

---

### Phase 6 — Tool Shim Layer + register on shell + end-to-end
**Goal**: Present the five tools to the assistant as thin, logic-free wrappers; register them on the server shell; confirm Claude Desktop (or the test transport) lists exactly five tools and each returns what its engine/helper produces.

**Design**:
```
mcp_server/tools.py — each body is ONE expression (C-14 hard block):

  kms_vault_info(ctx)                  → engine.build_vault_info_response()
  kms_search(query, project, since,    → engine.build_search_response(...)
             until, location,
             include_context, ctx)
  kms_read(paths, include_context,     → engine.build_read_response(...)
           ctx)
  kms_inspect(path, ctx)               → resolver.inspect(path)
  kms_move(src, dest_name, dest_kind,  → mover.move(...)
           ctx)

  (ctx is auto-injected by the framework and EXCLUDED from the public
   tool schema — research A1/A2 — so the public params are exactly the
   user-facing ones; the one-line body stays C-14-clean.)
```

**Steps**: implements spec **Component 7** (read its Build + Done-when; A1, A2). C-15: every tool wraps a path that is now built and tested (Phases 3/4/5).

1. RED (C-14 cleanliness): a test that greps `src/mcp_server/tools.py` for `^\s+(if |elif |for |while )` and asserts none — mirrors the live hook (`.claude/settings.json` matches `*/mcp_server/tools.py` and hard-blocks on that pattern). (This documents the constraint as a test in addition to the hook.)
2. RED (lists five tools, P4-MCP-01): via the framework test transport, connect and assert exactly the five tool names are listed and a no-op call returns without a connection error. (This completes P4-MCP-01, which Phase 2 only partially proved.)
3. RED (pass-through): for each tool, assert the shim returns exactly what its engine/helper produces — with the engine/helper mocked, assert the shim does not transform the result.
4. RED (`ctx` excluded from schema): assert the public input schema of `kms_search` does NOT contain `ctx` (research A1 verified `kms_demo(query, ctx)` exposes only `query`).
5. GREEN: create `src/mcp_server/tools.py`. Each tool body is one expression that pulls the engine off `ctx.request_context.lifespan_context["engine"]` (or calls the resolver/mover) and returns the result. `kms_vault_info`/`kms_search`/`kms_read` → engine; `kms_inspect` → Phase 4 resolver; `kms_move` → Phase 5 mover. The search/read tools expose `location` and `include_context` as parameters (declaration, not logic — passed straight through).
6. GREEN: register the five tools on the FastMCP app in `server.py` (Phase 2 left this as "no tools registered"). Confirm the shell now lists five.

**Files to modify**:
- `src/mcp_server/tools.py` — new (five one-line shims).
- `src/mcp_server/server.py` — register the five tools.
- Tests: `tests/test_mcp_server/test_tools.py` — new (C-14 grep, lists-five, pass-through, ctx-excluded). `tests/test_mcp_server/test_server.py` — extend the lists-five assertion.

**Test criteria**:
- [ ] `tools.py` contains no statement-level `if/elif/for/while` (C-14 hook accepts the write; the grep test passes).
- [ ] The shell lists exactly the five tools; a no-op call returns without error (P4-MCP-01).
- [ ] Each shim returns exactly what its engine/helper produces (no transformation).
- [ ] `ctx` is excluded from each tool's public input schema.
- [ ] Full suite green.

**Notes / coupling**: `[closed by design]` — its shallowness is mandated by C-14. Depends on Phases 2 (shell), 3 (engine), 4 (resolver), 5 (mover). A sixth tool (`kms_write`/TD-056) later is a new shim + a new engine method; the five are untouched.

**Status**: [ ] pending

---

### Phase 7 — TD-055 AI usage instructions (ships alongside, not a code component)
**Goal**: Ship the AI-facing guidance so the assistant uses the vault correctly (start with `kms_vault_info`; two-step search→read; context-before-content; `kms_inspect` for binary source; `include_context=true` escape hatch). This is documentation/configuration, not a buildable code component.

**Design**: TD-055 already enumerates the 13 instruction points (start-with-vault-info, never-assume-structure, two-step retrieval, context-before-content, hash-dedup-is-automatic, batch reads, inspect-for-binary, structured filters, refinement-expected, broad-queries-skip-context, plus the deferred write-path notes). This phase only decides the **delivery format** and writes the content into it.

**Steps**:
1. **Decide the delivery format** (deferred — see Open Questions): options are (a) per-tool `description=` strings on each `@mcp.tool()`, (b) a standalone skill/instruction file shipped with the server, (c) a user personal-preferences block, or (d) a combination. Recommendation: per-tool descriptions for the always-visible basics (each tool says when to call it) + a short skill/instruction file for the cross-tool flow (the two-step search→read, context-before-content). The format is not load-bearing for the code — it can be revised without touching the five tools.
2. Write the chosen content (the 13 points distilled from TD-055, dropping points 11–12 which describe the not-yet-built `kms_write`/`kms_move`-write-path; keep point 12's `kms_move` for CLUELESS since it ships in Phase 5).
3. If delivered as tool descriptions: add `description=` to each `@mcp.tool()` decorator in Phase 6's `tools.py` (still logic-free — a string literal is not a branch). If delivered as a file: create it under the repo (no vault write — it is a repo artifact, not a vault note).

**Files to modify**:
- Either `src/mcp_server/tools.py` (descriptions) and/or a new instruction file (path decided at implementation).

**Test criteria**:
- [ ] The chosen delivery exists and covers the discovery + search/read flow + inspect + include_context escape hatch.
- [ ] If descriptions: `tools.py` still passes the C-14 grep (string literals are fine).

**Notes / coupling**: not a code component (spec Out-of-scope: "delivery format is TD-055"). No tests of behavior; this is the human/AI-facing layer. Closes the documentation half of TD-055.

**Status**: [ ] pending

---

## Open Questions
Deferred decisions — resolve at implementation (research-confirm against real FastMCP / a real vault). None blocks the build; each has a safe default the plan proceeds on.

1. **`location` filter SQL shape (Risk R2 / A9, Phase 1d).** `LIKE 'inbox%'` vs `GLOB 'inbox/*'` vs range bounds (`>= 'inbox/' AND < 'inbox0'`). Correctness gate: match only the folder subtree, not a sibling like `inbox-archive/` — so a bare `LIKE 'inbox%'` is wrong; use `LIKE 'inbox/%'` or `GLOB 'inbox/*'` or range bounds. Index-use on the implicit `vault_path UNIQUE` index is low-urgency on a small vault. **Default to proceed:** `GLOB 'inbox/*'` (case-sensitive, uses the index for a prefix) unless the query plan shows otherwise. Test (i) in Phase 1d is the gate.
2. **`copy_context().run(...)` placement (OQ-004 / A11, Phase 2 step 5).** Per-tool wrapper vs one layer up in the framework's request handler. Cannot be determined from our code (it depends on whether FastMCP already runs each tool in its own task/context). **Default to proceed:** wrap per dispatched tool call (the safe spot); confirm against the real FastMCP dispatch at implementation and drop the wrapper only if the framework already isolates.
3. **Domain-move frontmatter field set (Risk R5 / Component 9, Phase 5).** Exact fields to set/clear for a *domain* destination (set `domain/<D>` tag, clear `project`, set a designated primary domain) so derive-from-tags consistency holds. **Default to proceed:** ship the *project*-move path first (fully specified, the primary P4-MCP-07 acceptance path); land the domain-move path once the field set is confirmed against the derive-from-tags rule (CONTEXT.md "derived routing").
4. **TD-055 delivery format (Phase 7).** Tool descriptions vs skill file vs preferences vs combination. **Default to proceed:** per-tool descriptions for the basics + a short instruction file for the cross-tool flow. Revisable without touching the five tools.
5. **Threshold/cap starting values (config, not code).** 0.3 / 3 are guesses until real vaults run (ADR-0010). Config-driven (Phase 1b) so they retune without a code change — no plan change needed to adjust.

---

## Out of Scope
Carried verbatim from the spec's Out-of-scope (do not re-spec here — open the spec for rationale):
- **`kms_write`** (creating notes from chat) — deferred, **TD-056** (field-level metadata guard undecided).
- **`kms_classify` as a tool** — out of scope by design (ADR-0011); the conversation use case is `kms_move`.
- **HTTP / network transport** — stdio only for the MVP; `MCPConfig.enable_http` stays false. HTTP deferred.
- **Auto-generating `CLAUDE.md` / `context.yaml`** — the MVP reads what exists and degrades; auto-authoring is **TD-054**.
- **A faster filter-only global search** — the bare list-everything path stays O(N) (**TD-053**); MCP exercises it more — monitor, not fixed.
- **A `location` index optimization** — `location` is a `WHERE` filter, not a new index; query-plan tuning deferred (Risk R2). No migration (C-05).
- **Scheduling / always-on automation** — the MVP is a manually-launched server (C-16: schedulers last).

---

## Glossary
- **MCP Server Shell** — the long-running front-door process Claude Desktop connects to; owns startup and per-call isolation. (`mcp_server/server.py`)
- **Tool Shim Layer** — the five thin, logic-free tool wrappers the assistant sees. (`mcp_server/tools.py`)
- **Context Injection Engine** — the brain that counts concentration, applies the threshold + cap, remembers what was already sent, assembles response blocks. (`mcp_server/context.py`)
- **Binary Resolver Helper** — finds the real binary (from a sibling note or a direct path) and re-runs the extractor for `kms_inspect`. (`mcp_server/_resolve.py`)
- **Note Mover Helper** — resolves a destination name, builds new metadata, moves, reindexes, registers the move guard for `kms_move`. (`mcp_server/_move.py`)
- **Conversation lifespan object** — the per-conversation server object that holds the one engine for that chat (Option A); under stdio one process = one conversation.
- **Dedup memory** — the per-conversation, content-fingerprint record of context files already sent.
- **Frequency threshold** — the share a single project/domain must reach across results before its context is attached (default 0.3, config).
- **Cap** — the max number of context files attached to any one response (default 3, config).
- **CLUELESS note** — an inbox note the capture pipeline could not confidently file; carries stamped classify-reasoning the assistant can present before a `kms_move`.
- **Result card** — a small per-result summary (path, summary, snippet, score, metadata); no full body, no `attachment_path`.
- **Move guard** — a short-lived registry entry telling the watcher "the pipeline moved this on purpose — don't undo it."

---

**Next step:** Plan written. Review, then run `/tdd-implement P4_mcp_context_injection` (start at Phase 1).
