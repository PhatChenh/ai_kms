# Phase 4 — MCP Server: Context Injection & Tool Design (Spec)

**Upstream design (authoritative):** `docs/1_design/P4_mcp_context_injection.md` — read it for the verified decision rationale, the per-tool C-14 split, the options grid, and the resolved open questions. This spec does **not** restate the design; it turns the design into buildable components, each with its own assumptions list for `/research` to verify.

**Behavior inventory:** acceptance criteria already exist as `P4-MCP-01 … P4-MCP-09` (origin: design) in `docs/system_behavior/behavior_inventory.yaml`. This spec references them by ID; it does **not** re-author or override them.

**Reader note (non-coder default).** Every section leads with a plain-English sentence. Code references live in parentheses or sub-bullets. The spec reads correctly even if every `code`-formatted token is deleted. A glossary is at the end.

---

## Purpose

This phase gives the AI chat assistant (Claude Desktop) a set of tools to look inside the user's knowledge vault during a conversation — and, crucially, makes every search or read response arrive already explaining the user's projects and domains, so the assistant understands the results without anyone having to ask. After this phase, a non-technical user can open Claude Desktop, ask "what do I know about Project Alpha?", and get back the right notes *plus* the background that makes those notes make sense — and can ask the assistant to file a stray inbox note into the right folder.

Concretely, the system gains a second long-running front door (a standalone MCP server, alongside the existing command-line tool) exposing five tools: discover the vault, search it, read notes, re-extract raw text from a binary file, and move a note. The amount of background context attached to each response is decided automatically (and tunably) so broad questions stay lean and focused questions arrive fully briefed.

---

## Already built (reuse, do not rebuild)

Everything the five tools *do* already exists and is tested — this phase wraps it, it does not re-implement it (constraint C-15). The new code is thin wrappers plus one decision engine.

| Function / Module | Location | What it does (plain English) | How this spec uses it | Depth |
|---|---|---|---|---|
| Search Coordinator (`search()`) | `retrieval/search.py:50` | Runs one search and returns cheap result cards (path, summary, snippet, score, metadata). The single search entry point. | `kms_search` calls it unchanged, except a new `location` filter is threaded through. | deep |
| Result card (`SearchResult`) | `retrieval/reranker.py:34` | A small summary of one matching note. Metadata bag holds `title`, `project`, `note_type`, `updated_at`, `key_topics`, `tags`. **No** `attachment_path`. **No** full body. | The Context Engine counts concentration off `project`/`note_type` and ships these cards after the context blocks. | deep |
| Candidate Filter (`filter_paths()`) | `storage/documents.py:393` | Narrows notes by project and/or date before ranking. Returns `Success(None)` = "search everything"; `Success([])` = "filtered, nothing matched". | Extended with a `location` (folder-prefix) filter for `kms_search`. | deep |
| Note Reader (`read_note()`) | `vault/reader.py:35` | Loads one note from disk → frontmatter + body + content hash. | `kms_read` calls it per path; helpers call it to resolve sibling frontmatter and pre-move metadata. | deep |
| Note Mover (`move_note()`) | `vault/writer.py:181` | The only safe way to move a `.md` note; honours the human-edit lock (blocks an AI move when `updated_by_human` is set). | `kms_move` calls it with `actor="ai"`. | deep |
| Index updater (`replace_path()`) | `storage/documents.py:232` | Re-points the search index from an old path to a new path in one transaction (clears old search rows). | `kms_move` calls it after the physical move. | deep |
| Note Catalog row reader (`get_by_path()`, `all_paths()`) | `storage/documents.py:146,178` | Fetch one document row by path / list all paths. | `kms_vault_info` derives inbox count + last-capture time; helpers re-read rows. | shallow |
| Move guard (`MoveGuard`, `get_active`, `set_active`) | `vault/move_guard.py` | A short-lived note telling the watcher "the pipeline moved this on purpose — don't undo it." | The server bootstrap publishes one; `kms_move` registers the destination before moving. | deep |
| Project Registry (`build_registry()`, `ProjectRegistry`, `LiveRegistry`, `format_for_prompt()`) | `vault/registry.py` | The live project→domain map, built by scanning vault folders. `groups` keyed by domain; `all_project_names` lists projects. **The real source of project/domain lists — there is no `meta.yaml`.** | `kms_vault_info` reads it; the Context Engine uses it for the project→domain frequency lookup. | deep |
| Handler registry (`HandlerRegistry.resolve()`) + extractors (`handler.extract()`) | `handlers/registry.py:48`, `handlers/base.py:81` | Picks the right text-extractor for a binary by extension and returns its raw text. | `kms_inspect` re-runs the extractor on the original binary — no AI call. | deep |
| Sibling→binary link (`NoteMetadata.attachment_path`) | `vault/frontmatter.py:72` | A sibling summary note's frontmatter field pointing at the original binary's vault path (written at capture). | The Binary Resolver Helper reads it to find the binary from a sibling `.md` path. | shallow |
| DB connection factory (`_connect()`, `get_connection()`) | `storage/db.py:16,74` | Opens a SQLite connection with WAL + foreign-keys ON. | A one-line `wal_autocheckpoint` add lands here (prerequisite). | deep |
| Correlation-id setup (`new_correlation_id()`) | `core/logging_setup.py:55` | Stamps a trace id on every log line of a run. **Calls `clear_contextvars()` first** — the contextvar-bleed risk. | The dispatcher wraps each tool call in its own context copy so concurrent calls don't wipe each other's id. | deep |
| MCP config (`MCPConfig`) | `core/config.py:244` | Holds MCP server settings (`port`, `host`, `enable_http`). | Extended with a nested `context_injection` block (threshold, cap, include-context toggle). | shallow |
| Search config (`SearchConfig`) | `core/config.py:303` | Holds `max_results`, `max_candidates`, model names. | Read unchanged by the Search Coordinator. | shallow |
| CLI bootstrap pattern | `cli/main.py:16,32,483` | `load_dotenv` once at top, `setup_logging` once, then `set_active(MoveGuard())` for the watcher. | The MCP server's own entry point mirrors this exact bootstrap. | deep |

**Partially built (extend):**
- **Candidate Filter** (`filter_paths()`) — exists with `project`/`since`/`until`; needs a `location` folder-prefix filter added (and the Search Coordinator must pass it through).
- **MCP config** (`MCPConfig`) — exists; needs a nested `context_injection` settings block.
- **DB connection factory** (`_connect()`) — exists; needs one `PRAGMA wal_autocheckpoint=100` line (must keep `PRAGMA foreign_keys=ON`).

**Not built (create):**
- The MCP Server Shell, the Tool Shim Layer, the Context Injection Engine, the Binary Resolver Helper, and the Note Mover Helper — all new, all under a new `mcp_server/` package (which does not yet exist).
- The `mcp>=1.27,<2` dependency (approved; not yet in `pyproject.toml`).

---

## Q1 Diagram (from design)

The chosen internal flow is **Option A** — the per-conversation dedup memory lives on the conversation's own FastMCP lifespan object. Reproduced verbatim from the design doc for grounding.

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

The companion Q1 in the design (the `kms_search` internal flow: search → count concentration → threshold fork → inject-or-not → context blocks first, cards second) is the per-search picture the Context Injection Engine implements.

---

## Q2 Diagram — How it connects to others

Plain-English: this shows where the new MCP feature touches the rest of the system. The chat client talks to the new server shell; the shell routes calls to thin tool wrappers; the wrappers hand work to one decision engine (and two small helpers); the engine fans out to the already-built search, registry, reader, extractor, mover, filter, and index. Names and arrow conventions match the Q1 above.

```
# MCP Context Injection — How It Connects
Scope: Shows what the Phase 4 MCP feature touches across the system.
       Does NOT show the internal inject math (threshold/cap/dedup) —
       see the Q1 diagrams for that.

How to read this:
  Center column  = the new feature being built (NEW)
  Solid boxes    = components that already exist and are reused
  Dashed boxes   = the per-conversation holder the Server Shell creates
  Arrow labels   = what passes between them
  Arrows         = "calls / hands work to"

  ┌─────────────────────┐
  │ Claude Desktop      │   the external chat client
  │ (AI client)         │
  └──────────┬──────────┘
             │ connects over stdio,
             │ calls the five tools
             ▼
  ┌─────────────────────┐        creates & holds one
  │ MCP Server Shell    │ - - - ►┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  │ (NEW) front door;   │        │ Conversation         │
  │ startup + per-call  │        │ Lifespan Object      │
  │ isolation           │        │ holds the one engine │
  └──────────┬──────────┘        └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
             │ routes each call
             ▼
  ┌─────────────────────┐
  │ Tool Shim Layer     │   the 5 thin, logic-free tools:
  │ (NEW) one-line      │   vault_info · search · read ·
  │ pass-throughs       │   inspect · move
  └──────────┬──────────┘
             │ hands the request to
             ▼
  ┌─────────────────────┐     reads tuning   ┌──────────────────┐
  │ Context Injection   │ ◄───── from ───────│ Config           │
  │ Engine (NEW)        │                    │ threshold, cap,  │
  │ counts, gates,      │                    │ include-context  │
  │ dedups, assembles   │                    └──────────────────┘
  │ response blocks     │
  └──────────┬──────────┘
             │ delegates inspect / move to
             ▼
  ┌─────────────────────┐   ┌─────────────────────┐
  │ Binary Resolver     │   │ Note Mover Helper   │
  │ Helper (NEW)        │   │ (NEW)               │
  │ sibling↔binary for  │   │ resolves dest,      │
  │ inspect             │   │ moves + reindexes   │
  └──────────┬──────────┘   └──────────┬──────────┘
             │                         │
             │   both the Engine and these helpers
             │   fan out to ALREADY-BUILT components
             ▼                         ▼
  ════════════════ already-built (reused as-is) ════════════════

  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
  │ Search        │  │ Project       │  │ Note Reader   │
  │ Coordinator   │  │ Registry      │  │ loads one     │
  │ runs search,  │  │ project→      │  │ note from     │
  │ returns cards │  │ domain lists  │  │ disk          │
  └───────────────┘  └───────────────┘  └───────────────┘

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

```
Who calls which already-built component (read each as "X calls Y"):
  - Context Injection Engine → Search Coordinator   (run the search, get cards)
  - Context Injection Engine → Project Registry      (project/domain lists; project→domain lookup for frequency count)
  - Context Injection Engine → Note Reader           (load full note bodies for kms_read)
  - Context Injection Engine → Note Catalog / Index  (inbox count, last-capture time for kms_vault_info)
  - Binary Resolver Helper   → Note Reader + Binary Extractor (find binary, re-extract raw text)
  - Note Mover Helper        → Note Reader + Note Mover + Note Catalog / Index (read, move, reindex)
  - Search Coordinator       → Candidate Filter      (existing wiring; new "location" filter added here)
```

Simplified: the seven already-built components are drawn as one shared tier instead of seven separate spokes (the feature touches more than the 6-spoke limit). The "who calls which" key preserves each exact connection. The two NEW helpers are shown as a sub-tier under the engine because the Tool Shim Layer reaches them only through the engine-dispatch path, never directly (C-14: shims hold no logic).

---

## Feature overview

**Happy path — a focused search.** The user asks the assistant a question about one project. The assistant calls the search tool. The thin wrapper hands the query and any filters to the existing Search Coordinator, which returns a handful of cheap result cards. The Context Injection Engine then counts how concentrated those cards are — what share belong to a single project or domain. If one project/domain crosses the configured share (the *frequency threshold*), the engine pulls that project's or domain's background file(s) (its `CLAUDE.md` and, for a domain, its `context.yaml`), caps the number attached (the *cap*), and places those context blocks **first** in the response, followed by the result cards. The assistant reads the background, then the cards.

**Happy path — discovery.** Before searching, the assistant calls the discovery tool. The engine reads the live Project Registry (project and domain names — scanned fresh, not from any file), counts inbox notes, finds the last-capture time, and reads the vault-root `CLAUDE.md` (global user context). All of that comes back in one response so the assistant knows the user's world.

**Happy path — read and inspect.** From the cards, the assistant asks to read several notes at once (one batched call). The engine loads each note's full body, and if any note belongs to a domain whose context has not yet been sent this conversation, it injects that minority-domain context first. If a card is a binary-backed summary (its note type signals `attachment-summary`), the assistant can ask to *inspect* the original — the Binary Resolver Helper finds the binary from the sibling note's frontmatter, re-runs the text extractor, and returns the raw extracted text. No AI call, no re-summarizing.

**Happy path — move.** When the user asks to file a stray inbox note, the assistant reads its stamped classify-reasoning, presents it, asks the user where it should go, then calls the move tool. The Note Mover Helper resolves the destination name to a folder, builds the note's new metadata (so its project/domain label matches the new home), registers the move guard so the file-watcher won't undo the move, moves the file, and re-points the index.

**Edge cases.**
- **Broad query → no context.** If no single project/domain crosses the threshold, zero context blocks are attached — just cards (`P4-MCP-04`).
- **Already-sent context → dedup.** If the engine already sent a context file this conversation (same content fingerprint), it replaces the full file with a short "context for X already provided" note (`P4-MCP-08`). A brand-new conversation (a new server process) starts with a clean slate.
- **Force re-injection.** A tool flag (`include_context=true`) forces the full context back in even if it was already sent (`P4-MCP-09`).
- **Edited context file mid-conversation.** Its fingerprint changes, so the new content is sent again (correct — the dedup is content-keyed).
- **Missing context files.** A project/domain with no `CLAUDE.md` and no `context.yaml` contributes no context block; the search still returns cards. The engine degrades gracefully (TD-054).
- **Human-locked note move.** If the note carries the human-edit lock, the move tool surfaces a clear failure to the assistant rather than crashing or overwriting (constraint C-02).
- **Stray inbox note in `Uncategorized`.** A result whose project has no domain contributes only to its project's frequency count, not any domain's (per the design recommendation; confirm at research).

---

## Out of scope

- **Creating new notes from chat (`kms_write`)** — deferred. The field-level metadata guard that protects user-set fields through capture re-processing is undecided. Handled later; tracked as **TD-056**. Listed in the tool-surface table as deferred; not specced as a buildable component here.
- **Re-running classify from chat (`kms_classify` as a tool)** — out of scope by design (ADR-0011). The conversation use case is handled by `kms_move` (the assistant acts on human judgment, not a re-classify).
- **HTTP / network transport** — stdio only for the MVP (Claude Desktop compatible). HTTP deferred (`MCPConfig.enable_http` stays false).
- **Auto-generating `CLAUDE.md` / `context.yaml`** — the MVP reads whatever files exist and degrades when they're absent. Auto-authoring is **TD-054**, post-Phase-4.
- **AI-facing usage instructions / skill** — ships *alongside* Phase 4 but the delivery format (tool descriptions vs skill file vs preferences) is **TD-055**; not a code component in this spec.
- **A faster filter-only global search** — the bare "list everything" path stays row-by-row (O(N)); acceptable on a small pre-launch vault. Tracked as **TD-053**.
- **Scheduling / always-on automation** — the MVP is a manually-launched server. Schedulers come last (constraint C-16).
- **A `location` index optimization** — `location` is a `WHERE`-clause filter, not a new index; query-plan tuning is deferred (Risk R2). No migration (constraint C-05).

---

## Constraints

Non-negotiable rules the build must respect. The first two are hook-enforced hard blocks — treat them as design boundaries, not afterthoughts.

- **C-14 · The tools file is logic-free** (HARD BLOCK) — `mcp_server/tools.py` may contain no `if/elif/for/while` at statement level. Every branch, loop, count, threshold compare, fallback chain, and dedup check lives in the Context Injection Engine or the helper modules. Each tool body is one expression that hands the request to the engine/helper and returns the result. — source: CLAUDE.md, CONSTRAINTS.md C-14, hook in `.claude/settings.json`.
- **C-06 · Thresholds in config, never in code** (HARD BLOCK) — the frequency threshold (0.3) and the cap (3) are read from config; no float literal in an `if`/`elif` comparison. — source: CONSTRAINTS.md C-06, hook.
- **C-15 · No MCP tool before its pipeline is tested** — each tool wraps an already-tested function (search, read, move+replace_path, extract, build_registry). No stub tools. — source: CONSTRAINTS.md C-15.
- **C-13 · Audit every AI decision** — the five MVP tools make **no new AI decisions** (all retrieval/move/extract). No `audit.write` is required for them; state this explicitly. If any future tool adds an LLM call, C-13 re-applies. — source: CONSTRAINTS.md C-13.
- **C-07 · Prompts as YAML** — `kms_inspect` uses the text extractor, not an LLM; no new prompt. — source: CONSTRAINTS.md C-07.
- **C-08 · Provider factory** — no direct LLM provider instantiation in MVP tools (no LLM calls). — source: CONSTRAINTS.md C-08.
- **C-04 · Foreign-keys pragma on every connection** (CRITICAL) — the `wal_autocheckpoint` edit must not remove `PRAGMA foreign_keys=ON`. — source: CONSTRAINTS.md C-04.
- **C-05 · Schema changes via migrations only** — `location` is a query clause, not DDL; no migration. — source: CONSTRAINTS.md C-05.
- **C-10 · Async wrapped with the established entry contract** — the server's async dispatch must follow the project's async entry pattern; no ad-hoc event-loop nesting. — source: CONSTRAINTS.md C-10.
- **C-11 · `load_dotenv` once, in the entry point** (HIGH) — the MCP server's own entry point owns `load_dotenv`; never inside `mcp_server/` library modules. — source: CONSTRAINTS.md C-11.
- **C-03 · `write_note`/`move_note` is a pure writer — caller owns the merge** (CRITICAL) — `kms_move` must read the note and re-pass fields explicitly (with the new project/domain); never call the mover with empty metadata. — source: CONSTRAINTS.md C-03.
- **C-12 · Public functions in guarded dirs return Result** — any new helper that lands in a guarded directory returns `Success`/`Failure`. — source: CONSTRAINTS.md C-12.
- **C-17 · No `CONFIG` import at module scope in tests** — MCP tests use lazy `CONFIG` or explicit paths. — source: CONSTRAINTS.md C-17, hook.

(Full guardrail checklist: design doc §Guardrail Checklist. Every component below was cross-checked against it in Step 4.)

---

## MVP tool surface

Five tools ship; `kms_write` is deferred. Each tool is a thin shim (C-14); the "where the logic lives" column names the engine/helper that holds it.

| Tool | What the AI uses it for | Wraps (already tested) | Where the logic lives | Status |
|---|---|---|---|---|
| `kms_vault_info` | Discover projects, domains, inbox count, last-capture time, global context | `build_registry()` + catalog reads + vault-root `CLAUDE.md` | Context Engine: registry view, counts, file read, dedup | MVP |
| `kms_search` | Search the vault, get context + cards | `search()` (+ new `location` filter) | Search Coordinator (ranking) + Context Engine (frequency → threshold → cap → dedup → block assembly) | MVP |
| `kms_read` | Read full bodies of chosen notes | `read_note()` per path | Context Engine: loop paths, minority-context inject, dedup | MVP |
| `kms_inspect` | Get raw extracted text from a binary source | `HandlerRegistry.resolve()` + `handler.extract()` | Binary Resolver Helper: sibling↔binary fallback, extractor dispatch | MVP |
| `kms_move` | File a note into a named project/domain | `move_note()` + `replace_path()` + move guard | Note Mover Helper: dest resolve, metadata build, move, reindex, lock-failure handling | MVP (TD-057) |
| `kms_write` | Create a note from chat | (capture pipeline + field-level guard) | — | **Deferred (TD-056)** |

---

## Assumptions

Each is a falsifiable claim about existing code or an external library that `/research` must verify. The two highest-priority are **A1** (the FastMCP lifespan API, Risk R1 — the whole Option A choice rests on it) and **A8** (the project→domain frequency lookup, OQ-P4-DOMAIN).

| ID | Assumption | Source (design) | What would prove it wrong |
|---|---|---|---|
| **A1** ✅ RESOLVED (research 2026-06-11) | CORRECTED: FastMCP (`mcp.server.fastmcp.FastMCP`) exposes a **process-scoped lifespan** entered once per process (`Server.run`), reachable inside each tool via the auto-injected `ctx.request_context.lifespan_context` (the `ctx` param is excluded from the public tool schema → a one-line tool body stays C-14-clean). Under the **stdio** transport one process = one conversation, so this process-lifespan **is** the per-conversation holder Option A needs — Option A is buildable exactly as designed, **no Option B fallback.** | Risk R1, OQ-P4-STATE (Option A) | Verified via ephemeral `uv run --with mcp` introspection + official SDK docs. Original "per-connection" wording was a mis-statement of a confirmed-feasible mechanism; corrected to "process-lifespan (= per-conversation under stdio)". |
| **A2** | FastMCP's tool-registration signature lets a tool body be a one-line pass-through (no required branching), and lets the dispatch be wrapped so each call runs in its own context copy. | Risk R1, §C-14 mechanism | Tool registration forces inline branching or a signature that can't be reduced to a pass-through → C-14 split needs rework. |
| **A3** | `mcp>=1.27,<2` installs cleanly on Python 3.12 with the project's `uv` toolchain and does not conflict with existing pinned deps (`anthropic`, `sentence-transformers`, `sqlite-vec`, etc.). | OQ-P4-DEP (approved) | Dependency resolution fails or forces a downgrade of an existing pinned package. |
| **A4** | The Search Coordinator (`search()`) returns result cards whose metadata bag carries `project` and `note_type` and **does not** carry `attachment_path`, so frequency counting and the binary signal both work off the card alone. | Flag 2 (D8), `reranker.py:154-161`, `search.py:34-42` | A card path exists where `project`/`note_type` is absent, or where `attachment_path` *is* present and load-bearing. |
| **A5** | The Project Registry's shape (`groups` keyed by domain name; each group has `domain_name`, `domain_path`, and a list of `ProjectEntry` with `name`+`path`; `all_project_names`) is exactly what `kms_vault_info` needs and is cheap (folder scan, no DB). | Flag 1 (D10), `registry.py:51-60,143-148` | `build_registry()` is expensive enough to need caching, or its shape omits domain or project names. |
| **A6** | A binary-backed sibling note's `attachment_path` frontmatter field holds the binary's vault-relative path and is reliably present for captured binaries; resolving it under the vault root yields the real binary. | D7, §sibling↔binary, `frontmatter.py:72`, `capture.py:1214` | A captured binary's sibling lacks `attachment_path`, or the stored value isn't resolvable to the binary. |
| **A7** ✅ RESOLVED (research 2026-06-11) | CORRECTED: `move_note(src, dst, actor="ai")` takes **NO incoming-metadata parameter** — it re-reads `src` from disk and merges that, then writes to `dst`; it DOES block the move with a `Failure` when the note is human-locked. So `kms_move` cannot set the new project/domain through `move_note` alone; it must write them with a separate `write_note(dst, new_meta, actor="ai")` AFTER the move. Proven sequence: `move_note` → `write_note(dst, new_meta)` → `replace_path` (`capture.py:962-968`). | Nuance (D12), `capture.py:962-968`, `writer.py:181-244,208-213` | Human-lock-block half confirmed correct; the metadata half was wrong (move_note carries no metadata). Component 9 recipe updated. |
| **A8** | The Context Engine can derive each result's **domain** by looking up the result's `project` in the Project Registry's project→domain mapping (cheap, no per-result note read); a result whose project is `Uncategorized` contributes only to its project's count. | Risk R4, OQ-P4-DOMAIN | The registry can't map a project to its domain without a per-note read, or the design's `Uncategorized` handling produces wrong frequency counts. |
| **A9** | `filter_paths()` can be extended with a `location` folder-prefix filter as an added `WHERE` clause on `vault_path` (e.g. matching `inbox/...`), with no new table and no migration. The `Success(None)` ("all notes") vs `Success([])` ("none matched") sentinel contract is preserved. | D9, Risk R2, `documents.py:393-448` | A `location` filter needs a schema/index change to be correct or usable, or it breaks the `None`-vs-`[]` sentinel contract. |
| **A10** | Adding `PRAGMA wal_autocheckpoint=100` to `_connect()` is a safe one-line change that does not disturb `journal_mode=WAL`, the `foreign_keys=ON` pragma, or the `sqlite-vec` extension load. | Prerequisite (TD-007/OQ-003), `db.py:16-25` | The pragma ordering interacts badly with the extension load, or `foreign_keys=ON` gets dropped. |
| **A11** | Wrapping each dispatched tool call in `copy_context().run(pipeline_fn)` isolates `new_correlation_id()`'s `clear_contextvars()` so concurrent calls don't wipe each other's correlation id, and this can be located in the server dispatcher. | Prerequisite (OQ-004), `logging_setup.py:71-74` | `copy_context().run(...)` does not isolate the contextvar in FastMCP's async dispatch model, or the dispatch hook isn't reachable. |
| **A12** | The MCP server can bootstrap exactly like the CLI: `load_dotenv` once → `setup_logging` once → importing `CONFIG` validates the vault root → `set_active(MoveGuard())` so `kms_move` suppresses watcher re-home. | Implications §server, `cli/main.py:16,32,483-485`, `config.py:561-633` | The server context can't reuse the CLI bootstrap sequence (e.g. FastMCP owns the event loop in a way that conflicts with `load_dotenv`/`setup_logging` ordering). |
| **A13** | A domain's context bundle is plain files read directly: a `CLAUDE.md` and (domain only) a `context.yaml`, with a simple file-exists fallback chain; `context.yaml` can be treated as opaque text (no schema assumed). | D5, R3, TD-054 | `context.yaml` requires structured parsing for the MVP, or the file locations differ from `Domain/<D>/` / `Projects/<A>/` roots. |

---

## Component dependency order

This documents **what must exist before each component can work** — not the coding order (that belongs to `/plan-from-specs`). Prerequisites first, then the new package from the outside in.

### 1. WAL-autocheckpoint prerequisite
**Goal.** Keep the long-running server's database write-ahead log from growing unbounded across many short tool calls.
**Build.** Add one pragma to the connection factory (`storage/db.py::_connect()`) setting `wal_autocheckpoint=100`. Leave `journal_mode=WAL`, `foreign_keys=ON`, and the `sqlite-vec` load untouched.
**Depends on.** None.
**Assumes.** A10.
**Decisions.** Q: keep the value at 100 (reference-project value) or tune? Leaning **100** — it's a starting point; revisit only if real daemon runs show latency.
**Done when.** Every new database connection opened by the system uses the checkpoint setting, and the foreign-key safety is still on (an existing connection still cascades deletes correctly). Closes TD-007 / OQ-003.

### 2. Config block for context injection
**Goal.** Make the two tuning numbers (how concentrated results must be before context is attached, and how many context files at most) editable without touching code.
**Build.** Add a nested `context_injection` settings group to the MCP config model (`core/config.py::MCPConfig`) with `frequency_threshold` (default 0.3), `max_context_files` (default 3), and `include_context_yaml` (default true). Add the matching block under `mcp:` in `config/config.yaml`. The Context Engine reads these; no float literal lives in any `if`/`elif`.
**Depends on.** None.
**Assumes.** —
**Interface shape.** Callers see `CONFIG.main.mcp.context_injection.<field>`. The defaults live in the Pydantic model so an absent YAML block still validates.
**Done when.** Changing the threshold or cap in the config file changes how much context a search response carries, with no code edit; a missing `context_injection` block still loads with the documented defaults. Satisfies C-06.

### 3. The `mcp` dependency
**Goal.** Bring in the official MCP framework so the server can speak the protocol Claude Desktop expects.
**Build.** Add `mcp>=1.27,<2` to `pyproject.toml` dependencies; refresh the lockfile.
**Depends on.** None.
**Assumes.** A3.
**Decisions.** Q: pin upper bound at `<2`? Leaning **yes** — guards against a breaking major.
**Done when.** A fresh environment install brings in the MCP framework and the high-level FastMCP API imports successfully. (Approved: OQ-P4-DEP.)

### 4. `location` filter on the Candidate Filter
**Goal.** Let the assistant scope a search to a physical vault area (e.g. "only the inbox"), distinct from the metadata `project` filter.
**Build.** Extend the Candidate Filter (`storage/documents.py::filter_paths()`) with an optional `location` argument that adds a folder-prefix `WHERE` clause on `vault_path`. Thread the same `location` argument through the Search Coordinator (`retrieval/search.py::search()`) so `kms_search` can pass it. Preserve the `Success(None)` (all-notes) vs `Success([])` (filtered, none matched) contract.
**Depends on.** None (extends existing functions).
**Assumes.** A9.
**Interface shape.** One new optional parameter on two existing functions; no new module. The filter logic keeps its single home (the deletion test still passes).
**Decisions.** Q: case-sensitivity / index use of the prefix match — `LIKE 'inbox%'` vs `GLOB 'inbox/*'` vs range bounds? Leaning **defer to research** (Risk R2) — correctness first, index tuning only if it matters on a real vault.
**Done when.** A search scoped to a folder area returns only notes physically under that area; an unscoped search behaves exactly as before. (Supports `P4-MCP-03`'s filter story.)

### 5. MCP Server Shell (bootstrap + dispatch)
**Goal.** Stand up the long-running front door that Claude Desktop connects to, set it up correctly once, and make every tool call run in its own isolated context.
**Build.** Create the server entry point (new `mcp_server/server.py` / `__main__`). Mirror the CLI bootstrap (`cli/main.py`): `load_dotenv` once at the top (C-11), `setup_logging` once, import `CONFIG` (which validates the vault root), and publish a move guard via `set_active(MoveGuard())` so `kms_move` can suppress watcher re-home. Register the five tools (Component 7) with the FastMCP app. At connection start, create **one** Context Injection Engine and store it on the conversation lifespan object (Option A). Wrap each dispatched tool call in its own context copy (`copy_context().run(...)`) to prevent correlation-id bleed (OQ-004). Follow the project's async entry contract (C-10).
**Depends on.** Components 1, 2, 3; and Component 6 (the engine it instantiates).
**Assumes.** A1, A2, A11, A12.
**Interface shape.** Callers (the MCP framework) see a configured app exposing five tools and a connection-lifespan hook. Hidden: the engine instantiation, the context-copy wrapping, the bootstrap order.
**Dependency category.** true-external (the MCP framework is an external library) — downstream planner should test the shell with the framework's in-memory/test transport and a mock engine; the bootstrap sequence is tested in-process.
**Decisions.**
- Q: where exactly does `copy_context().run(...)` wrap the call — inside the per-tool dispatch, or one layer up in the framework's handler? Options: per-tool / framework hook. Leaning **research-confirm** against the real FastMCP dispatch (A11/R1).
- A1 ✅ RESOLVED (research): use FastMCP's process-scoped lifespan via `ctx.request_context.lifespan_context` (per-conversation under stdio). No Option B switch needed.
**Done when.** Claude Desktop connects over stdio and lists exactly the five tools with no connection error on a no-op (`P4-MCP-01`); two tool calls in flight do not scramble each other's log trace id.

### 6. Context Injection Engine
**Goal.** The one brain that decides how much background to attach, remembers what it already sent this conversation, and assembles every tool's response in the right order. This is where all the branching lives (so the tools file stays logic-free).
**Build.** Create the engine module (new `mcp_server/context.py`). It holds the per-conversation dedup memory (content-hash table of context files already sent). It exposes a small set of "build response" methods, one per tool that needs context (`kms_vault_info`, `kms_search`, `kms_read`), each returning ordered response blocks (context blocks first, content second — D2). Inside, it:
- counts how often each project / domain appears across result cards, derives each result's domain via a **project→domain Project Registry lookup** (explicit step — A8/OQ-P4-DOMAIN), compares the top share against the configured threshold, caps the number of context files at the configured cap, drops files already in the dedup memory (replacing them with a short "already provided" note), honours an `include_context` override, and reads each chosen project/domain's `CLAUDE.md` (+ domain `context.yaml`) with a file-exists fallback chain;
- for `kms_vault_info`: builds the registry view (loops groups/projects), counts inbox notes and finds last-capture time from the catalog, reads the vault-root `CLAUDE.md`, runs the same dedup;
- for `kms_read`: loops the requested paths, loads each note body via the Note Reader, injects any not-yet-sent minority-domain context first.
All public functions return `Success`/`Failure` (C-12). No new AI decision → no audit write (C-13, stated explicitly).
**Depends on.** Components 2 (config), 4 (so `kms_search` can pass `location`); reuses Search Coordinator, Project Registry, Note Reader, Note Catalog reads.
**Assumes.** A4, A5, A8, A13.
**Interface shape.** Small interface (a few "build response" calls), large hidden implementation (counting, dedup, file reads, fallback chains). One real seam serving all five tools — a deep module that earns its keep.
**Dependency category.** in-process (test directly with a temp vault + temp DB and a stubbed registry/search).
**Decisions.**
- Q: how is a result's domain derived — registry project→domain lookup, or per-result note-tag read? Options: registry lookup (cheap) / read tags (accurate). Leaning **registry lookup**, `Uncategorized` results count only toward their project (design recommendation; confirm at research, A8).
- Q: dedup fingerprint — hash the file content, or the (path, mtime)? Leaning **content hash** (an edit mid-conversation should re-send; CONTEXT.md "hash-dedup session state").
**Done when.**
- A focused search response places the matching project/domain context **before** the cards (`P4-MCP-03`); a broad query attaches zero context (`P4-MCP-04`).
- A second focused search on the same domain in one conversation replaces the full context with a short "already provided" note (`P4-MCP-08`); a brand-new conversation sends it in full again.
- `include_context=true` forces full re-injection even after dedup (`P4-MCP-09`).
- `kms_vault_info` returns project + domain names (from the live registry), inbox count, last-capture time, and vault-root context once (`P4-MCP-02`).
- `kms_read` returns not-yet-sent minority-domain context before each note's full body; a binary-backed note returns its summary body, not bytes (`P4-MCP-05`).

### 7. Tool Shim Layer
**Goal.** Present the five tools to the assistant as thin, logic-free wrappers that satisfy the framework's registration contract and nothing more.
**Build.** Create the tools file (new `mcp_server/tools.py`). Each of the five tool bodies is a single expression: pull the engine off the conversation context (or call the relevant helper) and return its result. No `if/elif/for/while` at statement level (C-14). `kms_vault_info`, `kms_search`, `kms_read` route to the Context Engine; `kms_inspect` routes to the Binary Resolver Helper; `kms_move` routes to the Note Mover Helper.
**Depends on.** Components 5 (shell registers them), 6 (engine), 8 (binary helper), 9 (mover helper).
**Assumes.** A1, A2.
**Interface shape.** Intentionally shallow — its shallowness is *mandated* by C-14, not a smell. Callers (the framework + the AI) see five named tools; behind each is a one-line pass-through.
**Decisions.** Q: do the search/read tools expose `location` and `include_context` as parameters here? Leaning **yes** — they're passed straight through to the engine; the parameter list is declaration, not logic.
**Done when.** The tools file contains no statement-level branch or loop (the C-14 hook accepts the write), and each tool returns exactly what its engine/helper produces.

### 8. Binary Resolver Helper (`kms_inspect`)
**Goal.** Given either a summary note's path or a binary's own path, find the real binary and return its raw extracted text — no AI, no re-summarizing.
**Build.** Create a small resolver helper (new `mcp_server/_resolve.py` or equivalent). Fallback chain: if the path ends in `.md` and the note's frontmatter has an `attachment_path`, resolve that to the binary under the vault root; otherwise treat the given path as the binary itself. Then pick the extractor by extension (`HandlerRegistry.resolve()`) and run it (`handler.extract()`), returning the raw text. No prompt, no LLM (so C-07 and C-13 are not triggered). Public function returns `Success`/`Failure` (C-12).
**Depends on.** Reuses Note Reader, sibling `attachment_path`, Handler registry + extractors.
**Assumes.** A6.
**Dependency category.** in-process (test with a real fixture binary + its sibling note).
**Done when.** Inspecting a binary-backed result returns the raw extracted text resolved from the sibling's `attachment_path`; passing the binary path directly yields the same text; no AI call is made (`P4-MCP-06`).

### 9. Note Mover Helper (`kms_move`)
**Goal.** File a note into a named project or domain so that its on-disk location, its frontmatter label, and the search index all agree — and the watcher doesn't undo it.
**Build.** Create a move helper. Steps: resolve the destination *name* (project or domain) to a folder path (via the registry / `vault/paths`); `read_note(src)` and build new metadata with the destination's `project` (for a project move) or the `domain/<D>` tag + cleared `project` and a designated primary domain (for a domain move) — caller owns the merge (C-03); register the destination with the move guard **before** moving (`get_active().register(dst)`); call `move_note(src, dst, actor="ai")` to physically relocate the file (**A7: `move_note` carries NO metadata — it re-reads `src` from disk, so it alone will NOT update the project/domain label**); capture the pre-move vault-path string FIRST (`old_vault_path = to_vault_path(src)`, before the move — `src` is gone afterward); then `outcome = write_note(dst, new_meta, actor="ai")` to write the corrected frontmatter at the new path; then `replace_path(old_vault_path, outcome)` to re-point the index — **A7b: `replace_path`'s second arg is the `WriteOutcome` returned by `write_note` (it reads `outcome.metadata`/`vault_path`/`content_hash`), NOT the destination path** (`documents.py:232`). This `move_note` → `write_note` → `replace_path` sequence is the proven pattern (`capture.py:961-988`). If the note is human-locked, `move_note` returns a `Failure` — surface it to the assistant as a clear result. Public function returns `Success`/`Failure` (C-12).
**Depends on.** Component 5 (the shell must have published a move guard); reuses Note Mover, index updater, registry/paths.
**Assumes.** A7.
**Dependency category.** in-process (test with a temp vault: move a note, assert location + frontmatter + index + move-guard registration).
**Decisions.**
- Q: domain-destination consistency — set the `domain/<D>` tag, clear `project`, land under `Domain/<D>/`, and honour derive-from-tags so frontmatter and location never disagree? Leaning **yes** (Risk R5, CONTEXT.md "derived routing"); confirm the exact field set at research.
- Q: order of move-guard register vs move — register **before** the move (the proven capture pattern, `capture.py:1230-1232`).
**Done when.** Moving a CLUELESS inbox note to a named project relocates the file, updates its project/primary-domain frontmatter, updates the index (old search rows cleared), and the watcher does not re-home it (`P4-MCP-07`); a human-locked note returns a clear failure instead of being overwritten.

---

## Handoff notes

For `/research` and `/plan-from-specs`.

- **Top-priority research (Risk R1 / A1, A2, A11):** verify the FastMCP API before anything else. Specifically: (a) does FastMCP expose a per-connection lifespan/context object that a tool function can read at call time (the Option A foundation)? (b) what is the tool-registration signature, and can a tool body be a one-line pass-through with no inline branching (C-14)? (c) where in the dispatch can each call be wrapped in `copy_context().run(...)`? If (a) fails, the documented fallback is **Option B** (one process-global engine, safe because one stdio process is one conversation) — route back through the build-pipeline loop-back to re-decide; do **not** silently substitute it.
- **Second-priority research (OQ-P4-DOMAIN / A8):** confirm the project→domain frequency lookup. The card carries `project` and `note_type` but **not** domain; the engine must derive domain from the registry. Confirm the `Uncategorized` handling (a project with no domain contributes only to its project's count) produces sensible frequency counts. This is the subtlest correctness gap in the engine.
- **`context.yaml` is opaque (A13 / R3):** no schema exists (TD-054 deferred). The engine must read it as text and degrade gracefully when absent. Confirm the read path makes no structural assumption.
- **`location` query plan (A9 / R2):** `documents.vault_path` is `UNIQUE` (implicit index), but a default case-insensitive `LIKE 'inbox%'` may not use it. Research should check the query plan or switch to `GLOB 'inbox/*'` / range bounds if it matters. Low urgency on a small vault; flagged so the design's "has implicit index → fast" claim isn't taken on faith.
- **Two design code-corrections carried into this spec (do not revert):** (1) project/domain names come from the **live Project Registry**, not a `meta.yaml` (no such file exists — Flag 1 / A5); (2) result cards do **not** carry `attachment_path` — the binary signal is `note_type == "attachment-summary"`, and `kms_inspect` resolves the binary itself from sibling frontmatter (Flag 2 / A4, A6).
- **STATE.md correction needed:** STATE.md (line ~163) says the Project Registry is "PENDING implementation" — it is **shipped** (`vault/registry.py`, used by capture's classify step). Update when STATE.md is next touched.
- **Contract with Phase 4+ (`kms_write`, TD-056):** this phase delivers the read/move path. `kms_write` waits on the field-level metadata guard design. The Tool Shim Layer and Context Engine are built so a sixth tool can be added without touching the existing five (C-14 split keeps the engine the single home for logic).
- **AI usage instructions (TD-055):** the tools need accompanying guidance (start with `kms_vault_info`; two-step search→read; context-before-content; use `kms_inspect` for binary source; `include_context=true` escape hatch). Delivery format is undecided — ships alongside Phase 4 but is not a code component here.
- **Tech-debt intersections touched by this spec:** TD-007 (resolved by Component 1), TD-053 (filter-only O(N) path gets more traffic — monitor, not fixed), TD-054 (context files read as-is, degrade when absent), TD-055 (usage instructions), TD-056 (`kms_write` deferred), TD-057 (`kms_move` shipped here).

---

## Open questions (deferred — resolve at research/planning, do not block the spec)

- **OQ-P4-STATE — ✅ RESOLVED (research 2026-06-11).** Option A confirmed buildable: the dedup memory lives on FastMCP's process-scoped lifespan, reachable via `ctx.request_context.lifespan_context`; under stdio one process = one conversation, so it is per-conversation as intended. **No Option B fallback needed** (A1 was corrected, not failed).
- **OQ-P4-DOMAIN (A8).** Registry project→domain lookup vs per-result tag read for the frequency count. Spec assumes registry lookup; confirm at research.
- **`location` filter shape (R2 / A9).** `LIKE` vs `GLOB` vs range bounds for the folder-prefix match, and whether index use matters. Deferred to research.
- **Domain-move frontmatter field set (R5 / Component 9).** Exact fields to set/clear for a domain destination (tag, project, primary domain) so derive-from-tags consistency holds. Confirm at research.
- **`copy_context().run(...)` placement (A11 / Component 5).** Per-tool vs framework-hook. Confirm against the real FastMCP dispatch.
- **Threshold/cap starting values.** 0.3 / 3 are guesses until real vaults run (ADR-0010 Consequences). Config-driven so they can be tuned without code; no spec change needed to retune.

---

## Glossary

- **MCP Server Shell** — the long-running front-door process Claude Desktop connects to; owns startup and per-call isolation. (`mcp_server/server.py`)
- **Tool Shim Layer** — the five thin, logic-free tool wrappers the assistant sees. (`mcp_server/tools.py`)
- **Context Injection Engine** — the brain that counts concentration, applies the threshold + cap, remembers what was already sent, and assembles response blocks. (`mcp_server/context.py`)
- **Binary Resolver Helper** — finds the real binary (from a sibling note or a direct path) and picks the extractor for `kms_inspect`.
- **Note Mover Helper** — resolves a destination name, builds new metadata, moves, reindexes, and registers the move guard for `kms_move`.
- **Search Coordinator** — the existing one-call search that returns cheap result cards. (`retrieval/search.py`)
- **Result card** — a small per-result summary (path, summary, snippet, score, metadata); no full body, no `attachment_path`.
- **Candidate Filter** — narrows notes by project/date/(new) location before ranking. (`storage/documents.py::filter_paths`)
- **Project Registry** — the live project→domain map built by scanning vault folders; the real source of project/domain lists. (`vault/registry.py`)
- **Note Reader** — loads one note (frontmatter + body) from disk. (`vault/reader.py`)
- **Binary Extractor** — the existing handlers that extract raw text from a binary by file type. (`handlers/`)
- **Note Mover** — the existing safe move function + the move guard. (`vault/writer.py` + `vault/move_guard.py`)
- **Note Catalog / Index** — the SQLite documents table + the keyword/vector search indexes. (`storage/documents.py`)
- **Frequency threshold** — the share a single project/domain must reach across results before its context is attached (default 0.3, config).
- **Cap** — the max number of context files attached to any one response (default 3, config).
- **Hash-dedup session state** — the per-conversation, content-fingerprint record of context files already sent.
- **Conversation lifespan object** — the per-conversation FastMCP object that holds the one engine for that chat (Option A).
- **CLUELESS note** — an inbox note the capture pipeline could not confidently file; carries stamped classify-reasoning the assistant can present before a `kms_move`.

---

**Next step:** Spec written. Run `/research` to verify the assumptions above against real code and the installed `mcp` package — starting with A1 (FastMCP lifespan API, Risk R1) and A8 (project→domain frequency lookup) — before planning.
