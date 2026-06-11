# MCP tools embed domain/project context (CLAUDE.md, context.yaml) directly in tool responses with two-stage injection, not as a separate discovery tool

Phase 4 MCP tools automatically inject relevant CLAUDE.md and context.yaml files into search/read responses so the AI client always has domain background before interpreting results. Context is scoped by result-frequency threshold, hash-deduplicated per session, and delivered in two stages: top-N with search cards, minority with full content. We choose this over a separate context-fetching tool because embedded injection is deterministic (AI cannot forget to call it), over single-stage because the AI needs context both when choosing cards and when reading full content, and over always-inject because broad queries gain nothing from context and waste tokens.

**Status:** accepted (2026-06-11 — design + research verified the FastMCP process-lifespan mechanism = per-conversation under stdio; Phase 4 plan built on it)

## Context

- After Phases 1-3, the system captures, classifies, and searches notes. Phase 4 exposes the vault via MCP to any AI client (Claude Desktop, web, phone).
- Unlike Claude Code (which auto-discovers CLAUDE.md by walking the filesystem), MCP clients receive only what tool responses contain. Without explicit context injection, AI has no knowledge of domain vocabulary, stakeholders, project status, or organizational structure.
- Each Domain folder has `CLAUDE.md` (instructions/background) and `context.yaml` (people, metrics, vocabulary). Each Project folder has `CLAUDE.md`. These files are the user's primary mechanism for giving AI background knowledge.
- MCP server runs as stdio subprocess — one process per conversation in Claude Desktop (verified via GitHub issue #28860). In-memory state naturally scoped to conversation lifetime.
- Research (2024-2026) shows: the two-tool pattern (search + read) is industry standard (AWS Knowledge MCP, Obsidian MCP, OpenAI guidance); progressive disclosure achieves 85x token savings vs load-everything; Context Portal MCP server does per-workspace context injection alongside results.

## Decision

### 1. Context is embedded in tool responses, not fetched separately

All read-path tools (`kms_search`, `kms_read`, `kms_inspect`) include relevant context files in their response. No separate `kms_get_context` tool. The AI never needs to remember an extra step — context arrives automatically.

### 2. Two-stage injection with strict ordering: context BEFORE content

```
kms_search response:
  Block 1-N: CLAUDE.md / context.yaml (top-N from result distribution)
  Block N+1: Result cards (structured JSON)

kms_read response:
  Block 1-N: CLAUDE.md / context.yaml (minority domains/projects not yet sent)
  Block N+1: Full note content
```

Context always precedes content in the response. AI reads background before viewing results (stage 1) and before reading full notes (stage 2). This ordering is non-negotiable — it ensures the AI can make informed card selections and correctly interpret note content.

### 3. Frequency-based scoping with threshold and cap

After search, count which projects/domains appear in top results:
- Inject context for any domain/project with >= `frequency_threshold` (default 0.3) share of results
- Hard cap: `max_context_files` (default 3) per response
- Below threshold: zero context injected (broad query — context adds noise)
- All values in `config.yaml` under `mcp.context_injection`

### 4. Hash-based deduplication, in-memory, per-session

Server maintains `{content_hash: sent_timestamp}` dict in process memory. Already-sent context replaced with short "context for X already provided" note. New process (new conversation) = clean slate. If CLAUDE.md edited mid-session, hash changes, full content re-sent (correct behavior).

Escape hatch: `include_context=true` parameter forces full re-injection regardless of hash state.

### 5. Domain context bundles CLAUDE.md + context.yaml

For domain folders, both files are injected together (same dedup logic per file). Graceful fallback chain: both exist -> both injected; one missing -> inject the other; both missing -> no context for that domain.

## Considered Options

- **Separate `kms_get_context` tool** — AI must remember to call it before every search. Different clients may not. Fragile, non-deterministic. Rejected.
- **MCP Resources (read-once at session start)** — Architecturally correct but poor client support as of 2026. Cannot scope to query-relevant domains (would need to send ALL domains upfront). Rejected for now; revisit when client support improves.
- **Always inject all context** — Vault with 15 domains and 30 projects would dump megabytes of CLAUDE.md on every query. Token-wasteful. Rejected.
- **Single-stage injection (only with search results)** — AI reads full notes from minority domains without background context. Misses the case where 1/10 results is from a different domain and AI selects it for full read. Rejected.
- **`on_initialize` session hook for context** — Fires at connection start when we don't know which domains user will ask about. Same "dump everything" problem. Rejected.
- **Prompt caching alone (no dedup)** — Reduces API cost for repeated prefixes but does NOT reduce context window consumption. Tokens still count. Complementary optimization, not a replacement for scoping. Not rejected, but insufficient alone.

## Consequences

- **Positive:** AI always has domain background before interpreting results. Works identically across Claude Desktop, web, and phone (any MCP client). Token-efficient via threshold gating and hash dedup. No extra tool calls needed.
- **Positive:** `context.yaml` vocabulary injection means AI understands domain jargon (e.g., "GTV" = "Gross Transaction Value") without the user explaining it every conversation.
- **Negative:** Response structure is more complex than a simple result list. Tool implementation must maintain session state dict and compute frequency distribution.
- **Negative:** Threshold tuning (0.3 default) may need adjustment per vault. Too low = noisy context on broad queries. Too high = missing context on multi-domain queries.
- **Risk:** If CLAUDE.md files grow very large (user writes extensive project docs), even capped injection could consume significant context window. Future mitigation: `structuredContent` MCP field (zero-token client rendering) when client support arrives.
