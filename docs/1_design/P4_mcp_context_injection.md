# Phase 4 — MCP Server: Context Injection & Tool Design (Codebase Design Analysis)

**Status:** Design analysis — verification of locked decisions against real code + open-implementation options
**Source inputs (locked, NOT re-decided here):**
- `docs/0_draft/P4_mcp_context_injection.md` (grill output — decisions D1–D12, 8 resolved OQs, tool surface)
- `docs/architecture/system_adr/0010-mcp-context-injection-in-tool-responses.md` (proposed)
- `docs/architecture/system_adr/0011-mcp-write-path-kms-write-kms-move-not-kms-capture-kms-classify.md` (proposed)
- **Behavior inventory ID prefix for this phase: `P4-MCP`** (entries P4-MCP-01 … P4-MCP-09 written to `docs/system_behavior/behavior_inventory.yaml`)

**Reader note (non-coder default).** Every section leads with a plain-English sentence. Code references (file/symbol) live in parentheses or sub-bullets. The document reads correctly even if every `code`-formatted token is deleted.

---

## Cast of characters (symbols used 3+ times)

| Name | Plain-English role |
|------|--------------------|
| Search Coordinator (`retrieval/search.py::search`) | The one function that runs a search and returns cheap result cards. Already built and tested. |
| Result card (`retrieval/reranker.py::SearchResult`) | A small summary of one matching note — path, summary, snippet, score, and a metadata bag. No full note body. |
| Candidate Filter (`storage/documents.py::filter_paths`) | Narrows the note set down by project/date before any ranking runs. |
| Note reader (`vault/reader.py::read_note`) | Loads one note from disk and returns its frontmatter + body. |
| Note mover (`vault/writer.py::move_note`) | The only safe way to move a note file; respects the human-edit lock. |
| Index updater (`storage/documents.py::replace_path`) | Re-points the search index from an old path to a new path in one transaction. |
| Move guard (`vault/move_guard.py`) | A short-lived note that says "the pipeline moved this on purpose — watcher, don't undo it." |
| Project Registry (`vault/registry.py::build_registry`) | The live list of which projects belong to which domains, built by scanning vault folders. **This is the real source of the project/domain lists** — there is no `meta.yaml`. |
| Handler registry (`handlers/registry.py::HandlerRegistry.resolve`) | Picks the right text-extractor for a file by its extension. |
| Tool Shim (planned `mcp_server/tools.py`) | The thin, logic-free wrapper each MCP tool presents to the AI client. |
| Context Injection Engine (planned `mcp_server/context.py`) | The new module that decides which background files to attach and remembers what it already sent. |

---

## Decision (what this analysis recommends)

**Build the five MVP tools as thin, logic-free shims over already-built pipelines, put every branch and loop in a separate Context Injection Engine, and keep the per-conversation dedup memory on the connection's own lifespan object.** In one sentence: the locked design is feasible against the real code with two corrections (the project/domain list comes from the live Project Registry, not a `meta.yaml`; and result cards do not yet carry an `attachment_path` field), and the one genuinely-open choice — where the dedup memory lives — should be the conversation's lifespan object because the MCP server is one process per conversation, which makes that the simplest correct home.

The selected internal flow is shown in the Q1 diagram below.

---

## Q1 Diagram — chosen design (kms_search with context injection)

```
# Chosen Design — kms_search With Context Injection: What Happens Inside
Scope: One search call end-to-end. Context memory lives on the conversation.
       Does NOT show kms_read / kms_inspect / kms_move.

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Fork   = a decision with different outcomes

        Search request arrives at the thin Tool Shim
                   │
                   ▼
     ┌──────────────────────────────────┐
     │ Shim hands query + filters to the │
     │ Search Coordinator (existing)     │
     └───────────────┬──────────────────┘
                     │
                     ▼
     ┌──────────────────────────────────┐
     │ Coordinator returns ranked result │
     │ cards (path, summary, snippet,    │
     │ project, note type)               │
     └───────────────┬──────────────────┘
                     │
                     ▼
     ┌──────────────────────────────────┐
     │ Context Engine counts how often   │
     │ each project / domain appears in  │
     │ the cards                         │
     └───────────────┬──────────────────┘
              ┌───────┴────────┐
              │                │
   SHARE BELOW THRESHOLD   SHARE AT/ABOVE THRESHOLD
   (broad query)           (focused query)
              │                │
              ▼                ▼
     ┌────────────────┐  ┌───────────────────────────┐
     │ Inject no      │  │ Take top few (capped),     │
     │ context        │  │ drop ones already sent,    │
     │                │  │ load their context files   │
     └───────┬────────┘  └─────────────┬─────────────┘
             └──────────────┬──────────┘
                            ▼
        Response: context blocks first, then result cards
```

Simplified: the threshold / cap / hash-dedup substeps are grouped into the single "take top few, drop already-sent, load context" box on the focused branch.

The three competing homes for the dedup memory (Options A/B/C) are diagrammed in the **Options explored** section.

---

## Verification of locked decisions against real code

Each row states, in plain English, whether the decision is buildable as-is, then cites the code that proves it. **Two decisions hit a code-reality problem (D8 and D10) — flagged loudly.**

| Decision | Feasible? | Plain-English finding |
|----------|-----------|-----------------------|
| **D1** context inside responses, no separate tool | ✅ Yes | The tools already return whatever blocks we build; nothing in the code forces a separate context tool. |
| **D2** two-stage, context-before-content | ✅ Yes | The response is an ordered list of blocks; we control the order. |
| **D3** frequency + threshold + cap, from config | ✅ Yes, with config wiring | Result cards expose `project` and `note_type`, which is enough to count concentration (`retrieval/reranker.py:154-161`). Threshold/cap must be added to config (see Implications). |
| **D4** hash-dedup, in-memory, per session | ✅ Yes | One process per conversation is the verified lifecycle; an in-memory table is safe (draft "Session lifecycle (verified)"). |
| **D5** domain bundles CLAUDE.md + context.yaml | ✅ Yes | Both are plain files we read directly; the fallback chain is straightforward file-exists checks. |
| **D6** full CLAUDE.md, no template markers | ✅ Yes | We read and return the file text as-is. |
| **D7** separate `kms_read` and `kms_inspect` | ✅ Yes | `read_note` returns the sibling summary body for a binary-backed note; the binary path is recoverable from frontmatter; the extractor can be re-run standalone (see below). |
| **D8** card shape includes `attachment_path` | ⚠️ **Partial — code mismatch** | The `note_type` signal IS present, but cards do **not** carry `attachment_path` today. See flag below. |
| **D9** `kms_search` params incl. `location` | ✅ Yes, with one addition | `search()` exists with query/project/date_range/max_results; `location` needs adding to both `filter_paths()` and `search()`. |
| **D10** `kms_vault_info` reads `meta.yaml` | ❌ **Wrong source — code contradicts draft** | There is no `meta.yaml`. The real source is the live Project Registry. See flag below. |
| **D11** capture/classify NOT MCP tools | ✅ Yes (deferral) | `kms_write` is correctly deferred (TD-056); nothing in MVP requires it. |
| **D12** `kms_move` wraps move + index update | ✅ Yes, with a frontmatter nuance | `move_note` + `replace_path` + `move_guard` all exist as named; updating `project`/domain needs care (see nuance below). |

### 🚩 Flag 1 (D10) — `kms_vault_info` source-of-truth: Project Registry, NOT `meta.yaml`

**Plain English:** The draft says the project/domain list for `kms_vault_info` comes from a `meta.yaml` file. That file does not exist anywhere in the code. The list is actually produced live by scanning the vault's folders. Three project docs disagree with each other, and the code is the tie-breaker.

- The Project Registry exists and ships today (`vault/registry.py`): `build_registry(vault_cfg)`, `ProjectRegistry`, `format_for_prompt()`, and `LiveRegistry` are all implemented (file dated 2026-06-08). It is already used by capture's classify step (`pipelines/capture.py:1878-1900`, `_build_vault_context`).
- The registry's shape is exactly what `kms_vault_info` needs: `ProjectRegistry.groups` is a dict keyed by domain name; each group has a `domain_name`, a `domain_path`, and a list of `ProjectEntry` (each with a `name` and a folder `path`). Domain names = the group keys (minus `Uncategorized`); project names = `all_project_names` (`vault/registry.py:51-60, 143-148`).
- **No `meta.yaml` reference exists in `src/`** (verified by grep). The draft's resolved-OQ #4 ("read `meta.yaml` fresh every call") is built on a file that isn't there.
- **STATE.md is also stale here:** STATE.md line 163 says "Project Registry — Plan written 2026-06-07, PENDING implementation." That is wrong — the code shipped. TD-051 (RESOLVED) and CLAUDE.md both correctly treat the registry as existing.
- **Recommendation for the orchestrator:** `kms_vault_info` reads the live registry (cheap — folder scan, no DB). Correct the draft's D10 / resolved-OQ #4 wording and update STATE.md line 163 to "implemented." Because `build_registry()` is cheap and the draft's reason for "no caching" (freshness) still holds, the conclusion (read fresh each call) survives — only the *source* changes from `meta.yaml` to the registry.

### 🚩 Flag 2 (D8) — result cards do not carry `attachment_path`

**Plain English:** The draft says each search result card includes the path to the original binary file. The code does not put that on the card. This does not break `kms_inspect` (it finds the binary another way), but the draft's card shape is inaccurate.

- The card metadata bag contains `title`, `project`, `note_type`, `updated_at`, `key_topics`, `tags` — **not** `attachment_path` (`retrieval/reranker.py:154-161`; same shape in the filter-only path `retrieval/search.py:34-42`).
- This is fine for `kms_inspect`: the binary path is recoverable by reading the sibling note and taking its `attachment_path` frontmatter field (`vault/frontmatter.py:72`; written at capture in `pipelines/capture.py:1214`). The resolved-OQ #6 already chose exactly this ("`kms_inspect` resolves binary path internally from sibling frontmatter") — so D8's card field is redundant.
- **Recommendation:** Drop `attachment_path` from the documented card shape (it was never added to the index, by design). The AI distinguishes binary-backed notes by `note_type == "attachment-summary"` on the card, then calls `kms_inspect`, which does its own resolution.

### Nuance (D12) — `kms_move` must build new metadata before moving

**Plain English:** Moving a note does not automatically change which project it claims to belong to. To make the note's label match its new home, the mover must set the new project/domain explicitly before the move, or the old label sticks.

- `move_note(src, dst, actor)` re-merges using the **incoming** metadata as authoritative for every field except `created` (`vault/writer.py:48-92, 181-244`). It does not infer project from the destination folder.
- So `kms_move` must: read the note, construct metadata with the destination's `project` (or clear it and set the domain tag for a domain move), then call `move_note` — and pass `actor="ai"` (which will be blocked if the note is human-locked; that block is correct and should surface as a Result failure to the AI).
- After the physical move, call `replace_path(old_vault_path, outcome)` to fix the index (`storage/documents.py:232-303`), and register the destination with the move guard *before* the move (`get_active().register(dst)` — pattern proven at `pipelines/capture.py:1230-1232`).

### Prerequisites verified

- **WAL autocheckpoint (TD-007 / OQ-003):** Confirmed open. `_connect()` sets `journal_mode=WAL` and `foreign_keys=ON` but **not** `wal_autocheckpoint` (`storage/db.py:16-25`). One-line add. The FK pragma must remain (C-04).
- **Contextvar bleed (OQ-004):** Confirmed real. `new_correlation_id()` calls `clear_contextvars()` then `bind_contextvars(...)` (`core/logging_setup.py:71-74`). In a concurrent daemon, one tool call's `clear_contextvars()` wipes another's correlation id. The fix is to run each tool call inside its own context copy (`copy_context().run(...)`) — feasible and isolated to the dispatcher.
- **`mcp` dependency:** Not present in `pyproject.toml` (only `anthropic`, `openai`, etc.). Adding `mcp>=1.27,<2` is a **new dependency that requires explicit user sign-off** (see Open Questions OQ-P4-DEP). The draft's resolved-OQ #8 chose the official `mcp` package (`from mcp.server.fastmcp import FastMCP`).

---

## The load-bearing constraint: C-14 logic-free split (per tool)

**Plain English:** There is a hard rule (enforced automatically) that the MCP tools file may contain no `if`, `for`, `while`, or `elif` at the top level of its functions. The draft's build scope puts all five tools' logic in that file — that would be blocked. Every branch, loop, count, threshold, and fallback must live somewhere else. Below is exactly where each tool's "thinking" goes so the tools file stays a set of straight-line pass-throughs.

> Rule (`CONSTRAINTS.md` C-14, hard block in `.claude/settings.json`): no `if/elif/for/while` at statement level in `mcp_server/tools.py`. A tool body should be: build the call, hand it to the engine/pipeline, return the result.

| Tool | What the Tool Shim does (no branches) | Where ALL branching/looping/counting lives |
|------|----------------------------------------|---------------------------------------------|
| **kms_vault_info** | Calls one engine function and returns its blocks. | Context Engine builds the registry view (loops over groups/projects), counts the inbox, reads vault-root context, runs hash-dedup. |
| **kms_search** | Hands query + filters to one coordinator/engine call; returns blocks. | Search Coordinator does ranking. Context Engine does frequency-count → threshold compare → cap → hash-dedup → block assembly. |
| **kms_read** | Passes the path list to one engine call; returns blocks. | Engine loops the paths, resolves each, decides minority-context injection, reads each note, dedups. |
| **kms_inspect** | Passes one path to one engine/helper call; returns text. | A resolver helper decides sibling-vs-binary, picks the extractor, runs it. (See sibling↔binary section.) |
| **kms_move** | Passes path + destination to one move helper; returns the result. | A move helper resolves the destination name to a path, builds metadata, moves, updates the index, registers the move guard, handles the human-lock failure. |

**Mechanism that makes this real:** each Tool Shim is one expression — `return engine.do_thing(args)` — using Python's `match`/early-return only inside the engine, never in the shim. The engine functions live in `mcp_server/context.py` and a small `mcp_server/_resolve.py` (or equivalent), where branching is allowed. This is the single most important structural decision in Phase 4; if it is skipped, the hook blocks the build.

---

## How `kms_inspect` resolves sibling ↔ binary (genuinely-open sub-mechanic)

**Plain English:** When the AI wants the raw text of a PDF (not the AI summary), it may hand us either the summary note's path or the binary's own path. We must figure out which one we got and find the binary, then re-run the text extractor — no AI call.

The resolution is a short fallback chain (lives in a helper, not the shim, per C-14):
1. If the path ends in `.md` and the note's frontmatter has an `attachment_path`, that field is the binary's vault-relative path (`vault/frontmatter.py:72`, written at `pipelines/capture.py:1214`). Resolve it to an absolute path under the vault root.
2. Otherwise treat the given path as the binary itself.
3. Pick the extractor for that binary by extension (`HandlerRegistry.resolve(binary_path)` → `handlers/registry.py:48-66`) and run it (`handler.extract(binary_path)` returns `RawContent.text` — `handlers/base.py:61-90`).
4. Return the extracted text. **No prompt, no LLM** — so C-07 (prompts-as-YAML) is not triggered and C-13 (audit every AI decision) does not apply (this is text parsing, not a decision).

This is fully feasible: every cited symbol exists and is already tested in the capture path.

---

## Implications (what this change actually means)

- **The AI client always arrives pre-briefed about the user's world, without an extra step.** Context files ride inside the search/read responses, ordered before the content they explain.
  - Engine assembles `{type:"text"}` blocks; context blocks precede result/content blocks (D2; `retrieval/reranker.py::SearchResult` is the card payload).
- **The project and domain names the AI sees are computed live from the vault folders, every call.** No new file to maintain, no staleness, and the names line up exactly with what `kms_search`'s `project` filter expects.
  - `build_registry(vault_cfg)` → `ProjectRegistry.groups` / `all_project_names` (`vault/registry.py:63-148`). `kms_vault_info` and the `project` filter share this source.
- **Two small, tunable numbers control how much context gets attached, and they must live in config, not code.** Otherwise the hard block on hardcoded thresholds fires.
  - Add `mcp.context_injection` block (`frequency_threshold`, `max_context_files`, `include_context_yaml`) to `MCPConfig` (`core/config.py:244-249`) and `config.yaml` (`mcp:` section already exists at lines 35-38). The Context Engine reads them; the frequency-vs-threshold comparison happens in the engine (allowed), never in the tools file (C-6, C-14).
- **The server is a second, long-running front door to the system — it needs its own startup, separate from the CLI.** It must load environment + logging + config once, and (for `kms_move`) publish a move guard, the same way the CLI's `watch` command does.
  - New entry point (planned `mcp_server/server.py` / `__main__`) mirrors `cli/main.py`'s bootstrap: `load_dotenv` once at the top (C-11), `setup_logging` once, and `set_active(MoveGuard())` if `kms_move` is to suppress watcher re-home. Importing `CONFIG` triggers `load_config()`, which validates the vault root exists (`core/config.py:561-633`).
- **Concurrent tool calls can scramble each other's log-tracing id unless each call runs in its own context.** This is a correctness fix, not a nicety.
  - Wrap each dispatched tool call in `copy_context().run(pipeline_fn)` (OQ-004; mechanism at `core/logging_setup.py:71-74`).
- **The filter-only/global search path is row-by-row today; the MCP server will exercise it more.** It is not a bug, but it scales linearly with vault size.
  - `_search_filter_only` does `all_paths()` then one `get_by_path()` per note (`retrieval/search.py:122-155`). Already tracked as TD-053. MCP makes bare `kms_search` (no query, project/date only) reachable conversationally, so this path gets more traffic. Flagged, not fixed (small vault pre-launch).
- **`kms_move` is the only MVP tool that writes to the vault, and it inherits all the write-safety rules for free** because it goes through the existing mover.
  - Human-locked notes block an AI move (`vault/writer.py:208-213`); that failure should surface to the AI as a clear Result, not a crash.

### Module depth check

- **Context Injection Engine (new, `mcp_server/context.py`) earns its keep.** Delete it and the dedup counting, threshold/cap logic, and block assembly reappear smeared across five tools — which C-14 forbids anyway. It is a deep module: small interface (a few "build response" calls), large hidden implementation (counting, dedup, file reads, fallback chains). Real seam: it serves all five tools (5 adapters).
- **Tool Shim layer (new, `mcp_server/tools.py`) is intentionally shallow — and that is correct here.** It exists only to satisfy the MCP framework's tool-registration contract while keeping logic out (C-14). Its shallowness is mandated by a constraint, not a smell.
- **`filter_paths` is being deepened, not widened.** Adding a `location` prefix filter extends an existing function rather than adding a new module (`storage/documents.py:393-448`). The deletion test passes: the filtering logic has one home.
- **No new index seam:** `location` is a `WHERE vault_path LIKE ?` clause, not a new table — so C-05 (migrations-only schema changes) is not triggered.

---

## Success criteria

Written to `docs/system_behavior/behavior_inventory.yaml` as `P4-MCP-01` … `P4-MCP-09` (origin: design, granularity: outcome), derived from the draft's 9-checkbox acceptance criteria. Summary of what "working" looks like from the outside:

1. Claude Desktop connects over stdio and lists the five tools (`P4-MCP-01`).
2. `kms_vault_info` returns project/domain lists (from the live registry), inbox count, last-capture time, vault-root context (`P4-MCP-02`).
3. A focused `kms_search` returns context blocks **before** result cards (`P4-MCP-03`).
4. A broad query injects **zero** context (`P4-MCP-04`).
5. `kms_read` returns minority-domain context then full content; binary-backed notes return the summary body (`P4-MCP-05`).
6. `kms_inspect` re-extracts raw binary text via sibling→binary resolution, no AI call (`P4-MCP-06`).
7. `kms_move` relocates a CLUELESS note, updates frontmatter + index, watcher does not undo it (`P4-MCP-07`).
8. A repeat search on a seen domain deduplicates to a short note (`P4-MCP-08`).
9. `include_context=true` forces re-injection (`P4-MCP-09`).

---

## Guardrail Checklist (required input for `/writing-detailed-specs`)

Filtered to the domains this change touches. Each option below is checked against these.

- **C-14 · MCP tools.py logic-free** (HARD BLOCK) — no `if/elif/for/while` at statement level in `mcp_server/tools.py`. → Branching lives in the Context Engine + resolver helpers.
- **C-15 · No MCP tool before backing pipeline tested** — each tool wraps an already-tested function: `search()` ✓, `read_note()` ✓, `move_note()`+`replace_path()` ✓, `handler.extract()` ✓, `build_registry()` ✓. No stub tools.
- **C-12 · Public functions in pipelines/handlers return Result** — any new helper placed in a guarded dir returns `Success`/`Failure`.
- **C-13 · Audit every AI decision** — MVP tools make **no new AI decisions** (all retrieval/move). State this explicitly; no `audit.write` needed for the five tools. (If a future tool adds an LLM call, C-13 re-applies.)
- **C-06 · Thresholds in config, never in code** (HARD BLOCK) — `frequency_threshold` (0.3) and `max_context_files` (3) read from `mcp.context_injection`.
- **C-07 · Prompts as YAML** — `kms_inspect` uses the extractor, not an LLM; no new prompt.
- **C-08 · get_provider factory** — no direct provider instantiation in MVP tools (no LLM calls).
- **C-04 · FK pragma every connection** (CRITICAL) — WAL-autocheckpoint edit must not drop `PRAGMA foreign_keys=ON`.
- **C-05 · Schema changes via migrations only** — `location` is a query clause, not DDL; no migration.
- **C-10 · CLI/async wrap with asyncio.run** — the MCP server's async dispatch must follow the established async entry contract; no ad-hoc event-loop nesting.
- **C-11 · load_dotenv once, in the entry point** (HIGH) — the MCP server's own entry point owns `load_dotenv`; never inside `mcp_server/` library modules.
- **C-03 · write_note is a pure writer** (CRITICAL) — `kms_move` must read the note and re-pass fields explicitly; never call the mover with empty metadata.
- **C-17 · No CONFIG import at module scope in tests** — MCP tool tests use lazy CONFIG or explicit paths.

Domains checked: Architecture, DB Integrity, Async & CLI, LLM & Providers, Write Safety, Testing. Domains skipped: none material.

---

## Known tradeoffs (what we give up by this design)

- **The dedup memory is per-process, so it cannot survive a server restart mid-conversation.** Acceptable: the verified lifecycle is one process per conversation, and a restart legitimately means a fresh briefing.
- **Threshold tuning is a guess until real vaults run.** 0.3 / cap-3 are starting points; too low = noisy context on broad queries, too high = missing context on multi-domain queries (ADR-0010 Consequences).
- **MVP cannot create notes (`kms_write` deferred).** "Capture this discussion" — a core use case — waits on TD-056's field-level guard (ADR-0011 Negative).
- **The filter-only global path stays O(N).** Chosen consciously (TD-053) over a data-layer rewrite, because the vault is small pre-launch.

---

## Risks (for research / planning to watch)

- **R1 — `mcp` / FastMCP API shape unverified against code.** This analysis verified the *KMS* side; the FastMCP lifespan object, tool-registration signature, and how it threads a per-connection context into a tool handler are **not** verified against an installed package (the dep isn't installed). Research must confirm: (a) does FastMCP expose a per-connection lifespan/context object a tool can read (needed for Option A), and (b) where to wrap `copy_context().run(...)` in its dispatch.
- **R2 — `location` LIKE prefix and the UNIQUE index.** `documents.vault_path` is `UNIQUE` (`storage/schema.sql:3`), giving an implicit index. SQLite uses that index for a prefix `LIKE` **only** when `case_sensitive_like` is on or the column has the right collation; with default `LIKE` (case-insensitive) SQLite may not use the index for `col LIKE 'inbox%'`. Research should verify the query plan or use `vault_path GLOB 'inbox/*'` / `>=`/`<` range bounds if index use matters. Low urgency (small vault) but flagged so the draft's "has implicit index → fast" claim is not taken on faith.
- **R3 — context.yaml shape is unspecified.** D5 bundles `context.yaml` but no schema exists yet (TD-054 auto-generation is deferred). MVP must treat it as opaque text and degrade gracefully when absent. Confirm the read path makes no assumption about its structure.
- **R4 — frequency counting needs a domain signal on the card.** Cards carry `project` and `note_type` but a note's **domain** comes from its `domain/<D>` tags (in `key_topics`? — no, `_derive_key_topics` strips `domain/` and `type/` prefixes, `storage/documents.py:78-87`). So the card today does **not** expose the domain directly. The engine must derive domain per result (e.g. resolve the result's project → domain via the registry, or read the note's tags). Research must pin down how the engine knows each result's domain for the D3 frequency count — this is the subtlest gap.
- **R5 — `kms_move` domain destination.** Moving to a *domain* (not a project) must set the `domain/<D>` tag and clear `project`, and land the file under `Domain/<D>/` (path via `vault/paths.py::domain_dir`). Confirm the derive-from-tags consistency rule (CONTEXT.md "derived routing") is honoured so frontmatter and location never disagree.

---

## Open questions

**OQ-P4-DEP — Adding the `mcp` dependency needs your sign-off. → RESOLVED (user, 2026-06-11): APPROVED.**
Right now the project has no MCP library installed. To build the server we must add the official `mcp` package (pinned `mcp>=1.27,<2`).
**Resolution:** User approved adding `mcp>=1.27,<2` (official Anthropic-maintained package, high-level FastMCP API). Phase 4 proceeds as designed.

**OQ-P4-STATE — Where does the per-conversation dedup memory live? → RESOLVED (user, 2026-06-11): Option A (FastMCP connection lifespan).**
Right now nothing remembers which context files were already sent in a conversation.
**Resolution:** User chose **Option A** — the memory sits on the conversation's own FastMCP lifespan/connection object (clean per-conversation isolation, self-cleaning when the chat ends).
**HARD DEPENDENCY ON RESEARCH (Risk R1):** Option A assumes FastMCP exposes a per-connection context/lifespan object the tool shims can read the engine off of. **Research MUST verify this API exists** (the `mcp` package is not yet installed). If R1 fails at research, this routes back through the build-pipeline loop-back to re-decide — fallback is Option B (process-global, safe because one stdio process IS one conversation). The spec is built on Option A; do not silently substitute B.

**OQ-P4-DOMAIN — How does the context engine know each search result's domain for the frequency count? (carry-over of Risk R4)**
Right now a result card exposes its project and note-type, but not its domain.
The question: derive the domain by looking up the result's project in the registry, or by reading each result note's `domain/<D>` tags?
**If registry lookup:** cheap, no extra disk reads, but a project in `Uncategorized` has no domain.
**If read tags per result:** accurate per note, but adds a note read per card (cost on the hot path).
Recommendation: registry lookup for the project→domain mapping (cheap), treating `Uncategorized` results as contributing only to their project's count. Confirm at research.

**Carry-over open questions (record in root `OPEN_QUESTIONS.md` / already tracked):**
- **OQ-003 / TD-007** — add `wal_autocheckpoint=100` to `_connect()` (prerequisite; confirmed open).
- **OQ-004** — `copy_context().run(...)` contextvar isolation in the dispatcher (prerequisite; confirmed open, blocks Phase 4).
- **TD-056** — `kms_write` + field-level metadata guard (still open; blocks `kms_write`; the guard mechanism — `_locked_fields` vs hash-per-field vs `set_by` stamp — is undecided).
- **TD-057** — `kms_move` MCP tool (ships in MVP; design confirmed feasible here).
- **TD-054** — auto-generate CLAUDE.md / context.yaml (post-Phase-4; MVP degrades gracefully when files are absent).
- **TD-055** — AI-facing usage instructions (ships with Phase 4; delivery format TBD).

---

## ADR references

- **ADR-0010** (proposed) — context injection in tool responses. This analysis finds it feasible. **Recommendation: keep as proposed until the `mcp`/FastMCP API is verified (Risk R1), then move to accepted.** Do not change its status from this doc.
- **ADR-0011** (proposed) — write-path `kms_write`/`kms_move` not `kms_capture`/`kms_classify`. Feasible against code (`move_note`+`replace_path`+`move_guard` all exist; CLUELESS frontmatter fields confirmed). **Recommendation: move to accepted** for the `kms_move` half (no blockers); the `kms_write` half stays proposed pending TD-056.
- **No new ADR is warranted.** The one genuinely-open choice (OQ-P4-STATE) is reversible and low-stakes given the one-process-per-conversation lifecycle, so it does not meet the hard-to-reverse + surprising + real-tradeoff bar. The two code-reality corrections (registry vs `meta.yaml`; card shape) are documentation fixes, not architecture decisions.

---

## Options explored

The build approach for the five tools is essentially fixed by the constraints (C-14 forces the thin-shim + engine split; C-15 forces wrapping tested pipelines). The single open implementation choice is **where the dedup session memory lives** (OQ-P4-STATE). All three are viable; each gets a Q1 diagram.

### Option A — Engine instance held by the conversation's lifespan (Recommended)

**What this means:** Each chat gets its own briefing-memory that is created when the chat connects and thrown away when it ends. Clean and automatic.

**Constraints check:** C-14 satisfied (shims read the engine off the context, no branching); all others satisfied. Depends on Risk R1.

```
# Option A — Engine Held by the Conversation's Lifespan: What Happens Inside
Scope: Where the context-dedup memory lives and how a tool reaches it, for ONE
       conversation. Does NOT show the inject math (threshold/cap).

How to read this:
  Boxes = steps in order   Arrows = what happens next   Fork = a decision

        Conversation connection opens
                   │
                   ▼
     ┌──────────────────────────────┐
     │ Server Bootstrap creates ONE  │
     │ Context Injection Engine and  │
     │ stores it on the shared       │
     │ conversation lifespan object  │
     └───────────────┬──────────────┘
                     ▼
     ┌──────────────────────────────┐
     │ A Tool Shim is called; it     │
     │ receives the conversation     │
     │ context as a parameter        │
     └───────────────┬──────────────┘
                     ▼
     ┌──────────────────────────────┐
     │ Shim pulls the single engine  │
     │ off the context and hands it  │
     │ the request                   │
     └───────────────┬──────────────┘
                     ▼
     ┌──────────────────────────────┐
     │ Engine checks its Dedup       │
     │ Memory: already sent?         │
     └───────────────┬──────────────┘
              ┌───────┴────────┐
        NOT SENT YET       ALREADY SENT
              │                │
              ▼                ▼
     ┌────────────────┐  ┌────────────────┐
     │ Build full     │  │ Replace with   │
     │ context blocks │  │ short "already │
     │ + content      │  │ provided" note │
     └────────┬───────┘  └───────┬────────┘
              └────────┬─────────┘
                       ▼
        Tool response blocks returned
        (conversation ends → engine discarded)
```

### Option B — One process-wide engine shared by all tools (Fallback if R1 fails)

**What this means:** A single shared briefing-memory for the whole running server. Simplest to wire and *safe in this project* because the server is one process per conversation — but it would silently mix memories if that ever stopped being true.

**Constraints check:** C-14 satisfied. The only risk is the process model assumption.

```
# Option B — One Process-Wide Engine Shared by All Tools: What Happens Inside
Scope: Same goal as Option A, engine in one shared holder for the process.

How to read this:
  Boxes = steps in order   Arrows = what happens next

        Server process starts
                   │
                   ▼
     ┌──────────────────────────────┐
     │ Bootstrap creates ONE engine  │
     │ and parks it in a single      │
     │ process-wide shared holder    │
     └───────────────┬──────────────┘
                     ▼
     ┌──────────────────────────────┐
     │ A Tool Shim is called; it     │
     │ reaches into the shared       │
     │ holder to get the engine      │
     └───────────────┬──────────────┘
                     ▼
     ┌──────────────────────────────┐
     │ Engine checks its Dedup       │
     │ Memory and runs the inject    │
     │ decision                      │
     └───────────────┬──────────────┘
                     ▼
     ┌──────────────────────────────┐
     │  RISK: holder is process-wide.│
     │  If one process ever served   │
     │  two conversations, their     │
     │  memories would MIX           │
     └───────────────┬──────────────┘
                     ▼
        Tool response blocks returned
```

### Option C — Fresh engine per call, memory in an external store (Not recommended)

**What this means:** No long-lived briefing-memory; each tool call looks up the memory by conversation id, uses it, and writes it back. Most robust to any process model, but adds plumbing the simple stdio model doesn't need.

**Constraints check:** C-14 satisfied (the lookup/build/writeback runs inside a helper, not the shim). The cost is an external store and a conversation-id the stdio transport doesn't natively expose.

```
# Option C — Fresh Engine Per Call, Memory in External Store: What Happens Inside
Scope: Same goal; only an external Session Memory Store persists between calls.

How to read this:
  Boxes = steps in order   Arrows = what happens next

        A Tool Shim is called
                   │
                   ▼
     ┌──────────────────────────────┐
     │ Shim asks the Session Memory  │
     │ Store (by conversation id)    │
     │ for the current fingerprint   │
     │ table                         │
     └───────────────┬──────────────┘
                     ▼
     ┌──────────────────────────────┐
     │ Shim builds a throwaway       │
     │ engine around that table and  │
     │ runs the inject decision      │
     └───────────────┬──────────────┘
                     ▼
     ┌──────────────────────────────┐
     │ Shim writes the updated       │
     │ fingerprint table back to the │
     │ Session Memory Store          │
     │ (the only thing that persists)│
     └───────────────┬──────────────┘
                     ▼
        Tool response blocks returned
```

> **Recommended: Option A.** The verified one-process-per-conversation lifecycle makes the conversation's lifespan the natural, self-cleaning home for the memory — Option B is the safe fallback only if the MCP library does not expose that object (Risk R1).

### Rejected alternatives (one line each)

- **Put all five tools' logic directly in `tools.py`** (the draft's literal build scope) — rejected: violates C-14 hard block; the hook will refuse the write.
- **A separate `kms_get_context` tool** — rejected by ADR-0010 (AI may forget to call it).
- **MCP Resources for context** — rejected by ADR-0010 (poor client support in 2026; cannot scope to query-relevant domains).
- **Always inject all context** — rejected by ADR-0010 (token-wasteful).
- **Read project/domain names from a `meta.yaml`** — rejected: no such file exists; the live Project Registry is the real source (Flag 1).
- **Add `attachment_path` to the search index/card** — rejected: unnecessary; `kms_inspect` resolves the binary from sibling frontmatter (Flag 2, resolved-OQ #6).

---

## Next step

Design analysis complete. Recommended sequence:
1. Resolve **OQ-P4-DEP** (dependency sign-off) with the user.
2. Run `/architecture-docs` to fold the corrected source-of-truth (registry, not `meta.yaml`) into the main architecture story and move ADR-0011's `kms_move` half toward accepted.
3. Run `/writing-detailed-specs` using this doc's Guardrail Checklist + the per-tool C-14 split as the build skeleton.
