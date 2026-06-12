# Phase 4 — MCP Server: Context Injection & Tool Design

**Source:** Grilling session 2026-06-11
**Status:** Draft — ready for `/codebase-design-analysis`
**Scope:** MCP tool surface, context injection strategy, retrieval flow. Does NOT cover: `kms_write` field-level guard (TD-056), auto-generation of CLAUDE.md/context.yaml (TD-054).

> **NOTE:** The Phase 4 section in `docs/roadmap/roadmap.md` is STALE. It describes the original `kms_capture`/`kms_classify` design which has been superseded by this document. This draft + ADR-0010 + ADR-0011 are the authoritative Phase 4 design. Ignore the roadmap's Phase 4 components, tool registry, and acceptance criteria.

---

## Problem Statement

After Phases 1–3, the system can capture, classify, and search notes — but only via terminal. Phase 4 exposes the vault through MCP so any AI client (Claude Desktop, web, phone) can query it in natural conversation.

Two key problems beyond basic MCP wiring:
1. **Context gap:** AI querying the vault has no background knowledge about the user's domains/projects. Unlike Claude Code (which auto-discovers CLAUDE.md files), MCP clients receive only what the tool returns. Without context, AI misinterprets domain jargon, misunderstands project relationships, and gives shallow answers.
2. **Token waste:** Naively attaching all context files to every response wastes tokens. Need smart scoping and deduplication.

---

## Research Findings (what others do)

| Pattern | Source | Relevance |
|---------|--------|-----------|
| Metadata pre-filter scoping | Pinecone, Weaviate, our `filter_paths()` | Already implemented |
| Hierarchical parent-child retrieval | H-RAG, LlamaIndex TreeRAG | Maps to Domain→Project tree |
| Static context prepend | Context Portal MCP, LangChain RAG | Direct match — prepend project instructions before results |
| Structured MCP response (context + results blocks) | knowledge-mcp, context-portal | Convention, not spec requirement |
| Two-tool pattern (search + read) | AWS Knowledge MCP, Obsidian MCP, OpenAI guidance | Industry standard for progressive disclosure |
| Progressive disclosure (85x token savings) | SynapticLabs meta-tool pattern | Validates two-step approach |
| MCP Resources vs Tools | MCP spec | Resources = read-once at session start; poor client support today |
| Prompt caching | Anthropic API | Reduces cost but not context window; relevant for repeated context |
| `structuredContent` in tool results | MCP spec 2025-06-18 | Zero-token UI rendering; future optimization |

**Key references:**
- [context-portal](https://github.com/GreatScottyMac/context-portal) — per-workspace decisions/architecture in SQLite
- [smart-connections-mcp](https://github.com/msdanyg/smart-connections-mcp) — Obsidian vault semantic search via MCP
- [AWS Knowledge MCP Server](https://awslabs.github.io/mcp/servers/aws-knowledge-mcp-server/) — `search_documentation` + `read_documentation`
- [cyanheads/obsidian-mcp-server](https://github.com/cyanheads/obsidian-mcp-server) — 14 tools including search + read separation

**Session lifecycle (verified):**
- stdio transport: one MCP server process per conversation (Claude Desktop spawns subprocess per chat)
- Multiple simultaneous chats = separate processes (confirmed via GitHub issue #28860)
- New chat = new process = fresh in-memory state (correct behavior for context dedup)
- `Mcp-Session-Id` being deprecated in next MCP spec revision — do NOT depend on it
- In-memory session state is safe for stdio (process lifetime = conversation lifetime)

---

## Design Decisions

### D1: Context injection happens INSIDE tool responses, not as a separate tool

**Decision:** Context files (CLAUDE.md, context.yaml) are embedded in search/read tool responses, not fetched via a separate tool.
**Reason:** If context is a separate tool, AI must remember to call it first. Different clients may not. Baking it in guarantees context always arrives with results.

### D2: Two-stage context injection with strict ordering

**Decision:** Context is injected at two points, always BEFORE the content it relates to:

```
kms_search response:
  1. CLAUDE.md context blocks (top-N from result distribution, hash-deduped)
  2. Result cards (summary/snippet/metadata)

kms_read response:
  1. CLAUDE.md context blocks (minority domains/projects, hash-deduped)
  2. Full note content
```

**Reason:** AI must be context-aware BEFORE viewing results/content. Stage 1 ensures AI can make informed card selections. Stage 2 ensures AI has background for every domain/project it's about to read in full.

### D3: Context scoping via result frequency + threshold + cap

**Decision:**
- After search, count which projects/domains appear in top results
- Inject CLAUDE.md for any domain/project with ≥ 30% of results (configurable threshold)
- Hard cap: max 3 context files total (configurable)
- Below threshold = no context injected (broad query, context not useful)
- All thresholds in `config.yaml`, not hardcoded

**Examples:**
- "Movies Q2 performance" → 8/10 results from Movies → inject Domain/Movies/CLAUDE.md + Projects/Movies Q2/CLAUDE.md
- "what meetings this week" → 2 results each from 5 projects → nothing hits 30% → zero context
- "compare Movies and Game revenue" → 5 each → both hit 50% → both domain CLAUDE.md injected

### D4: Hash-based deduplication (in-memory, per-session)

**Decision:** Server tracks content hashes of all CLAUDE.md/context.yaml files sent in current session. If same hash already sent, replace with short note "context for X already provided." New session = new process = clean slate.
**Storage:** Python dict in MCP server process memory. No SQLite persistence.
**Edge case:** If CLAUDE.md is edited mid-session, hash changes → server re-sends full content (correct behavior).
**Escape hatch:** `include_context=true` parameter on read-path tools forces re-injection regardless of hash state.

### D5: Domain context.yaml bundled with CLAUDE.md

**Decision:** For domain folders, inject both `CLAUDE.md` and `context.yaml` (people, metrics, vocabulary). Same hash-dedup. Graceful fallback: missing context.yaml → CLAUDE.md only; missing CLAUDE.md → context.yaml only; both missing → no context for that domain.
**Reason:** `context.yaml` is small, structured, and carries critical vocabulary (jargon, KPIs, stakeholder names) the AI needs to correctly interpret notes.

### D6: Full CLAUDE.md content, no template enforcement

**Decision:** Return entire CLAUDE.md file content. No `<!-- context-end -->` markers or section splitting.
**Reason:** Cannot control how end users write their CLAUDE.md. Any template enforcement would be brittle and add user burden.

### D7: Separate `kms_read` and `kms_inspect` tools

**Decision:** Two tools for retrieving content:
- `kms_read(paths[])` — returns sibling `.md` summary body for binary-backed notes, full markdown body for regular notes. Metadata as structured dict, body as markdown text.
- `kms_inspect(path)` — re-runs text extractor on the original binary file (PDF, DOCX, etc.) and returns raw extracted text. Accepts either sibling `.md` path or binary path (resolves internally).

**Reason:** AI needs the summary for quick understanding, but sometimes needs exact text from the source binary (specific quotes, table data, page-level detail). Re-extraction is fast (no AI call, just text parsing). Separate tool avoids batch-parameter awkwardness.

### D8: Response format — multiple content blocks, structured cards

**Decision:** MCP tool results use content block arrays:
- Context blocks: `{type: "text", text: <CLAUDE.md content>}` — plain text, AI reads as prose
- Result cards: `{type: "text", text: <structured JSON>}` — path, title, summary, snippet, score, project, tags, note_type, attachment_path
- Full content (kms_read): `{type: "text", text: <JSON with metadata dict + body markdown>}`

### D9: `kms_search` parameters

**Decision:** `kms_search` accepts:
- `query` (string, optional) — free-text search query
- `project` (string, optional) — filter by project metadata field (semantic filter)
- `date_range` (string, optional) — filter by updated_at (indexed, efficient)
- `location` (string, optional) — filter by vault path prefix (physical filter, e.g., "inbox")
- `include_context` (bool, optional, default=true) — force/skip context injection
- `max_results` (int, optional) — cap on returned cards

`project` and `location` are different axes: `project` = semantic (from metadata), `location` = physical (from vault path). Both useful, not redundant.

### D10: `kms_vault_info` as session-start discovery tool

**Decision:** New tool that returns:
- List of active projects (names + folder paths)
- List of domains (names + folder paths)
- Inbox note count
- Last capture timestamp
- Vault-root CLAUDE.md content (if exists) — global user context, hash-deduped

**Reason:** AI needs to discover vault structure before querying. Provides exact project/domain names for `kms_search` `project` parameter. Natural place for one-time global context injection.

### D11: `kms_capture` and `kms_classify` are NOT MCP tools

**Decision:** These are watcher-internal operations, not AI-callable tools.

**`kms_capture` replaced by `kms_write` (TD-056):** AI creates notes by writing to vault with user-directed metadata. Watcher detects and runs capture pipeline. Requires field-level metadata guard so pipeline doesn't overwrite user-set tags/project.

**`kms_classify` replaced by `kms_move` (TD-057):** For CLUELESS notes, AI reads `classify_reasoning` from frontmatter, presents to user, asks for guidance, then moves directly with `kms_move`. Re-invoking classify on a CLUELESS note = same input, same CLUELESS output.

### D12: `kms_move` for AI-directed note relocation

**Decision:** Thin wrapper around `move_note()` + `documents.replace_path()`.
- Updates frontmatter `project`/`primary_domain` to match destination
- Uses `move_guard` to prevent watcher re-homing
- Not blocked by TD-056 (field-level guard) — move is physical, not metadata
- Ships in Phase 4 MVP

---

## MCP Tool Surface

### Phase 4 MVP (ships)

| Tool | Type | Params | Context injection? |
|------|------|--------|--------------------|
| `kms_vault_info` | Discovery | none | Yes (vault-root CLAUDE.md) |
| `kms_search` | Read | query, project, date_range, location, include_context, max_results | Yes (top-N from results) |
| `kms_read` | Read | paths[], include_context | Yes (minority domains/projects) |
| `kms_inspect` | Read | path | Yes (same dedup logic) |
| `kms_move` | Write | path, destination | No |

### Deferred (TD-056: field-level guard required)

| Tool | Type | Params | Blocked by |
|------|------|--------|------------|
| `kms_write` | Write | content, metadata{} | TD-056 (field-level metadata guard in capture pipeline) |

### NOT exposed as MCP tools

| Operation | Reason | Replacement |
|-----------|--------|-------------|
| `kms_capture` | Watcher-internal; AI doesn't process file drops | `kms_write` (TD-056) for conversational capture |
| `kms_classify` | Re-classifying CLUELESS notes gives same result | `kms_move` (TD-057) after human guidance |

---

## Retrieval Flow (end-to-end)

```
User: "What do I know about Movies performance Q2?"

1. AI calls kms_vault_info (if first query in session)
   → Returns: project list, domain list, inbox count, vault-root CLAUDE.md
   → AI now knows: Movies domain exists, Movies Q2 Strategy project exists

2. AI calls kms_search(query="Movies performance Q2", project="Movies Q2 Strategy")
   → Server runs search pipeline (filter_paths → rank → rerank)
   → Server counts result distribution: 7/10 from Movies domain, 6/10 from Movies Q2 project
   → Both exceed 30% threshold → inject Domain/Movies/CLAUDE.md + context.yaml + Projects/Movies Q2/CLAUDE.md
   → Hash-dedup check: first time → send full content with hashes
   → Response (ordered):
     Block 1: Domain/Movies/CLAUDE.md (hash: abc123)
     Block 2: Domain/Movies/context.yaml (hash: def456)
     Block 3: Projects/Movies Q2 Strategy/CLAUDE.md (hash: ghi789)
     Block 4: Result cards JSON [{vault_path, title, summary, snippet, score, metadata}, ...]

3. AI reads context blocks → understands Movies domain vocabulary, stakeholders, project status
   AI reads cards → selects 3 notes to read fully

4. AI calls kms_read(paths=["Projects/Movies Q2/revenue-analysis.md", "Projects/Movies Q2/meeting-may.md", "Domain/Movies/notes/industry-report.md"])
   → Server checks: all paths are Movies domain/project → CLAUDE.md already sent (hash match)
   → Response (ordered):
     Block 1: "Context for Domain/Movies and Projects/Movies Q2 Strategy already provided."
     Block 2: Full content of revenue-analysis.md {metadata: {...}, body: "..."}
     Block 3: Full content of meeting-may.md {metadata: {...}, body: "..."}
     Block 4: Full content of industry-report.md {metadata: {...}, body: "..."}

5. (Optional) AI sees a search result for "report.pdf" (type=attachment-summary)
   AI calls kms_inspect(path="Projects/Movies Q2/attachment/.summaries/report.pdf.md")
   → Server resolves: this is a sibling → finds binary at attachment_path → re-runs PdfHandler.extract()
   → Response:
     Block 1: (no new context — already sent)
     Block 2: Full extracted text from report.pdf
```

---

## Context Injection Architecture

```
                    ┌─────────────────────────────┐
                    │   In-Memory Session State    │
                    │   {hash → sent_timestamp}    │
                    └──────────┬──────────────────┘
                               │
            ┌──────────────────┼──────────────────┐
            │                  │                   │
            ▼                  ▼                   ▼
    ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
    │  kms_search   │  │   kms_read    │  │  kms_inspect  │
    │               │  │               │  │               │
    │ 1. Run search │  │ 1. Resolve    │  │ 1. Resolve    │
    │ 2. Count      │  │    paths      │  │    binary     │
    │    project/   │  │ 2. Find new   │  │ 2. Find new   │
    │    domain     │  │    domains/   │  │    domain/    │
    │    frequency  │  │    projects   │  │    project    │
    │ 3. Apply      │  │ 3. Inject     │  │ 3. Inject     │
    │    threshold  │  │    unsent     │  │    unsent     │
    │    + cap      │  │    CLAUDE.md  │  │    CLAUDE.md  │
    │ 4. Inject     │  │ 4. Return     │  │ 4. Extract    │
    │    unsent     │  │    full       │  │    binary     │
    │    CLAUDE.md  │  │    content    │  │    text       │
    │ 5. Return     │  │               │  │               │
    │    cards      │  │               │  │               │
    └───────────────┘  └───────────────┘  └───────────────┘

    Context files per location:
    - Domain/<D>/: CLAUDE.md + context.yaml
    - Projects/<A>/: CLAUDE.md only
    - Vault root: CLAUDE.md only (via kms_vault_info)

    Threshold: ≥ 30% of results from that domain/project (configurable)
    Hard cap: max 3 context files per response (configurable)
    Dedup: content hash, in-memory dict, per-process lifetime
```

---

## Config Surface (new keys in config.yaml)

```yaml
mcp:
  context_injection:
    frequency_threshold: 0.3    # domain/project must represent ≥ 30% of results
    max_context_files: 3        # hard cap per response
    include_context_yaml: true  # bundle context.yaml with domain CLAUDE.md
```

---

## Related Tech Debt

| TD | Summary | Status |
|----|---------|--------|
| TD-054 | Auto-generate CLAUDE.md + context.yaml for domains/projects | OPEN — post-Phase 4 |
| TD-055 | AI-facing skills/instructions for MCP tool usage | OPEN — ships with Phase 4 |
| TD-056 | `kms_write` + field-level metadata guard in capture pipeline | OPEN — blocks kms_write |
| TD-057 | `kms_move` MCP tool for note relocation | OPEN — ships with Phase 4 MVP |

---

## Open Questions — Resolved in Grill Session

1. **OQ-004 (contextvar bleed):** RESOLVED — wrap each tool call in `copy_context().run(pipeline_fn)`. One-pattern fix in MCP server dispatcher.
2. **OQ-003 / TD-007 (WAL autocheckpoint):** RESOLVED — add `wal_autocheckpoint=100` to `_connect()`. One-liner.
3. **Field-level metadata guard (TD-056):** DEFERRED — blocks `kms_write` only. MVP ships without it.
4. **`kms_vault_info` caching:** RESOLVED — read `meta.yaml` fresh every call. File is tiny (<1ms read). Caching adds staleness risk for zero gain.
5. **`location` filter:** RESOLVED — add `location` param to `filter_paths()` as `vault_path LIKE ?` prefix match. `vault_path` has implicit index (UNIQUE constraint).
6. **Binary-backed note signal in cards:** RESOLVED — `note_type: "attachment-summary"` already in SearchResult metadata. No need to add `attachment_path` to documents table. `kms_inspect` resolves binary path internally from sibling frontmatter.
7. **`kms_move` destination format:** RESOLVED — accept project/domain name (string), resolve to vault path internally via `vault/paths.py`. AI gets exact names from `kms_vault_info`.
8. **MCP library choice:** RESOLVED — use official `mcp` package (`from mcp.server.fastmcp import FastMCP`). Pin `mcp>=1.27,<2`. High-level API built-in, smaller dep footprint than separate `fastmcp` package. Anthropic-maintained.

## Open Questions — Still Open (for TD-056 session)

1. **Field-level metadata guard mechanism:** What marks a field as user-owned vs pipeline-owned? Options: `_locked_fields` list, hash-per-field, `set_by` provenance stamp. Blocks `kms_write`.
2. **`kms_write` destination:** Should it write to inbox (always) or directly to target folder (skipping classify)?

---

## Phase 4 Build Scope

### Prerequisites (resolve first, small)
| Item | Work | Size |
|------|------|------|
| OQ-003 / TD-007 | `wal_autocheckpoint=100` in `_connect()` | 1 line |
| OQ-004 | `copy_context().run(...)` in MCP dispatcher | ~10 lines |
| `mcp` dependency | Add `mcp>=1.27,<2` to pyproject.toml | 1 line |

### MVP Components (build order)
| # | Component | What | New files | Depends on |
|---|-----------|------|-----------|------------|
| 1 | MCP Server shell | stdio server via FastMCP, tool registration, contextvar isolation | `src/mcp_server/__init__.py`, `src/mcp_server/server.py` | Prerequisites |
| 2 | Context injection engine | Hash-dedup state, frequency threshold, CLAUDE.md/context.yaml reader, response builder | `src/mcp_server/context.py` | Server shell |
| 3 | `kms_vault_info` | Discovery tool: project/domain list from meta.yaml, inbox count from DB, vault-root CLAUDE.md | `src/mcp_server/tools.py` | Context engine |
| 4 | `kms_search` | Wraps `retrieval/search.py::search()` + `filter_paths()` location param + context injection stage 1 | `src/mcp_server/tools.py` (add), `storage/documents.py` (location param) | Context engine |
| 5 | `kms_read` | Batch path read via `vault/reader.py::read_note()` + context injection stage 2 | `src/mcp_server/tools.py` (add) | Context engine |
| 6 | `kms_inspect` | Binary re-extraction via handler `extract()` + path resolution (sibling↔binary) | `src/mcp_server/tools.py` (add) | Handlers |
| 7 | `kms_move` | `move_note()` + `replace_path()` + frontmatter update + move_guard | `src/mcp_server/tools.py` (add) | Server shell |
| 8 | Claude Desktop config | `claude_desktop_config.json` entry for stdio server | Config file | All tools |
| 9 | TD-055 delivery | AI instructions in tool descriptions + optional skill/preference file | Tool descriptions in tools.py | All tools |

### Estimated Size
- **New files:** ~4 (server.py, context.py, tools.py, __init__.py)
- **Modified files:** ~2 (documents.py for location param, pyproject.toml for mcp dep)
- **New lines:** ~800-1200 estimated
- **Tests:** ~200-300 lines (tool integration tests, context engine unit tests, hash-dedup tests)

### Build Plan

**One `/build-pipeline` run** to produce the implementation plan (design is already grilled; spec is writing up decisions from this session).

**Two `/tdd-implement` sessions:**

**Session A — Server + Context Engine + Search Path:**
- Prerequisites (OQ-003 WAL fix, OQ-004 contextvar fix, mcp dependency)
- MCP server shell (stdio, FastMCP, tool registration)
- Context injection engine (hash-dedup state, frequency threshold, CLAUDE.md/context.yaml reader, response builder)
- `kms_vault_info` (discovery + vault-root CLAUDE.md)
- `kms_search` (search wrapper + location param on `filter_paths` + context injection stage 1)
- Session A acceptance: Claude Desktop can connect, call `kms_vault_info`, and run a search query with context injection

**Session B — Read Path + Write Path + Instructions:**
- `kms_read` (batch path read + context injection stage 2)
- `kms_inspect` (binary re-extraction + sibling↔binary path resolution)
- `kms_move` (move_note + replace_path + move_guard + frontmatter update)
- TD-055 delivery (AI instructions in tool descriptions)
- Session B acceptance: full end-to-end behavior test — search → read → inspect → move, all with context injection working

### Acceptance Criteria (replaces stale roadmap criteria)
- [ ] Configure Claude Desktop to use the MCP server (stdio)
- [ ] Call `kms_vault_info` — returns project/domain list + vault-root CLAUDE.md
- [ ] Ask "what do I know about [project]?" — `kms_search` returns context blocks + result cards
- [ ] Read selected notes via `kms_read` — returns minority context + full content
- [ ] Inspect a binary-backed note via `kms_inspect` — returns raw extracted text
- [ ] Move a CLUELESS inbox note to a project folder via `kms_move`
- [ ] Second search on same domain — context hash-deduped (short note instead of full CLAUDE.md)
- [ ] All tools return structured results, not errors
- [ ] `include_context=true` forces context re-injection
