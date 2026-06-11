# Cloud-Native Rearchitecture — AgentBase Deployment

_Created: 2026-06-11_
_Status: DRAFT — discussion capture, not a build plan_
_Audience: Next AI session doing design/spec/plan work_

---

## How to read this document

This document has 15 sections. They are cross-referencing — reading one section in isolation will give you an incomplete picture. **Read in this order:**

1. **§1-2** first — understand what this doc is and the two-AI model. (2 min)
2. **§3-4** — the architectural split and daemon spec. This is the core decision. (5 min)
3. **§5** — the source-of-truth shift (DB replaces vault). Read this before looking at any code-level section. (3 min)
4. **§7** — graceful degradation table. Confirms the product behavior the architecture must deliver. (1 min)
5. **§9-10** — what stays, what moves, what retires, constraint updates. The module-level impact. (5 min)
6. **§13** — investigation guide with exact file paths and line numbers. Read this WHEN you start inspecting code, not before. (reference)
7. **§11** — Phase 4 impact. Read only if you need to decide whether to finish Phase 4 first or refactor first. (2 min)
8. **§6, §8, §12, §14** — supporting detail (inspect tiers, AgentBase deployment, open questions, starting checklist). Read as needed.

**Do NOT start designing from §13 alone.** The investigation guide tells you WHERE to look but not WHY things are changing. §3-5 give the WHY. §9-10 give the WHAT. §13 gives the WHERE.

### Warnings for the design phase

- **This is NOT a simple port.** The source-of-truth shifts from vault files to DB (§5). This changes the data flow direction, not just where code runs. Any design that treats this as "same code, different server" will miss the point.
- **`WriteOutcome` is the linchpin coupling.** It connects `vault/writer.py` → `storage/documents.py`. When `write_note()` retires for AI output, every call site that produces a `WriteOutcome` and feeds it to `upsert()` or `replace_path()` must be redesigned. See §13.1 and §13.2 for exact locations.
- **The daemon is NOT a thin wrapper.** It carries the watcher (1129 lines of battle-tested edge-case handling), the handler extraction stack, and a WebSocket command executor. It's a real deployable with its own test suite.
- **The retrieval stack is safe.** `search.py`, `ranker.py`, `reranker.py`, `embeddings.py`, `keyword.py` — all DB-only, zero filesystem dependency. Don't redesign these unless the DB schema changes force column renames.
- **Coordination complexity > code complexity.** The hard part is the API contract between daemon and AgentBase, the WebSocket protocol, and DB persistence on a stateless container. The code changes themselves are bounded — most files change shallowly (swap output target or input source), not deeply (redesign algorithms).
- **~1370 existing tests.** Many assume local vault files exist. Cloud-side tests will need mocks for daemon uploads. Daemon-side tests reuse the temp-vault pattern. Plan the test split early.
- **Constraint C-01 must be rewritten before implementation starts.** Current C-01 says "vault is source of truth" — the refactor inverts this. Any implementer reading `CONSTRAINTS.md` without the update will build the wrong thing.

---

## 1. What this document is

A record of decisions made during a discussion between the project owner and an AI session on 2026-06-11. It captures the **agreed direction, constraints, and open design questions** for rearchitecting AI-kms from a local-only system to a hybrid cloud-native deployment on VNG's AgentBase platform.

This document does NOT prescribe function signatures, class hierarchies, or module interfaces. Those belong to the design and spec phases. It DOES pin down the architectural split, the deployment model, data ownership, and the communication pattern — so the design phase starts from settled ground, not from relitigating these decisions.

---

## 2. Context: Two AIs, one knowledge base

The system has two distinct AI actors:

### Housekeeping AI (cloud, on AgentBase)
- Runs the capture pipeline (extract → summarize → classify → index)
- Runs reconcile passes
- No direct user interaction
- Deployed as a **Custom Agent** on AgentBase Runtime (Docker container, autoscaling, named endpoints)
- Called by the daemon when files change

### User-facing AI (any Claude client)
- The human's conversational AI (Claude Desktop, claude.ai web, Claude mobile)
- Consumes the knowledge base via MCP tools (`kms_search`, `kms_read`, `kms_vault_info`, `kms_inspect`, `kms_move`)
- Does NOT run capture or classify — it queries and retrieves
- Connects to the MCP server hosted on AgentBase

### Why two AIs
The housekeeping AI is a system agent — it runs automatically when files change, makes classification decisions, writes summaries. The user-facing AI is the human's thinking partner — it searches, reads, and presents context. Separating them means the housekeeping AI can be improved/redeployed without touching the user's conversational experience, and the user's AI can access context from any device without the housekeeping system running.

---

## 3. Architectural split — what lives where

### On AgentBase (cloud)
| Component | Current location | Notes |
|---|---|---|
| SQLite database | `data/kb.db` on local disk | **Becomes source of truth.** Summaries, tags, metadata, embeddings, FTS5, audit log — all first-class DB fields. |
| Capture pipeline | `pipelines/capture.py` | Runs on AgentBase. Receives extracted content from daemon. Outputs to DB, not to vault files. |
| Classify pipeline | `pipelines/classify.py` | Pure function, already side-effect-free. Runs on AgentBase. |
| Search coordinator | `retrieval/search.py` | Reads from DB. Fully self-contained on cloud. |
| Ranker + reranker | `retrieval/ranker.py`, `retrieval/reranker.py` | Read from DB. No filesystem dependency. |
| Index layer | `retrieval/embeddings.py`, `retrieval/keyword.py` | Write to DB on capture. |
| LLM provider | `llm/provider.py` | Configurable provider (`config.yaml`). Can use AgentBase platform LLM, Anthropic API, or any OpenAI-compatible endpoint. |
| Prompt loader | `llm/prompt_loader.py` | YAML files ship inside the Docker image. |
| Config | `core/config.py`, `config/config.yaml` | Ships inside Docker image. Vault-specific config (root path, etc.) adapts to cloud model. |
| MCP server | `mcp_server/` (Phase 4, in progress) | Hosted on AgentBase. Serves user-facing AI from anywhere. |
| Audit log | `storage/audit_log.py` | Writes to cloud DB. |
| Project registry | Scans vault folders | **Needs redesign.** Currently scans filesystem. In cloud model, registry data must come from DB or from daemon-reported folder structure. |
| Reconcile | `pipelines/reconcile.py` (7 stages) | Some stages touch filesystem (orphan detection, stale binaries). **Needs split**: DB-only stages run on cloud; file-touching stages dispatch commands to daemon. |
| Handlers (extraction) | `handlers/*.py` | Run on user's machine (daemon-side). See §4. |

### On user's machine (local)
| Component | Notes |
|---|---|
| Vault (raw files) | `inbox/`, `Projects/`, `Domain/` — the file drop zone. User owns these files. |
| CLAUDE.md files | Per-project and per-domain context files. Daemon syncs their content to DB on change. |
| Daemon | Thin Python process. Watches vault, uploads content, executes move commands from AgentBase. |
| Handlers (extraction) | `handlers/*.py` — PDF, DOCX, XLSX text extraction runs locally for speed. Extracted text (not raw bytes) is uploaded to AgentBase. |

### On neither (removed from both)
| Component | Reason |
|---|---|
| `vault/writer.py` (AI writes to vault) | Capture output goes to DB, not to `.md` files. Daemon handles file moves only. |
| Sibling `.md` files under `.summaries/` | Summaries are DB records, not vault files. The `.summaries/` folder concept retires. |
| Frontmatter as metadata store | Metadata lives in DB. Files in vault have no frontmatter requirements. Existing frontmatter on files is ignored. |
| `vault/indexer.py` (scan loops) | Replaced by daemon's watch-and-upload. |
| `vault/watcher.py` (local watchdog) | Moves to daemon. Watcher logic reused but runs as part of the daemon, not the main system. |

---

## 4. Daemon specification

### Role
The daemon is a **thin bridge** between the user's local filesystem and the AgentBase cloud. It has no AI, no DB, no classification logic. It does two things:

1. **Watch → Extract → Upload**: detect file changes in the vault, extract text content locally (using handlers), upload extracted content + file metadata to AgentBase via HTTPS.
2. **Execute commands**: receive commands from AgentBase (file moves, file deletes) via a persistent connection and execute them on the local filesystem.

### Communication pattern
- **Daemon → AgentBase**: HTTPS REST calls. Daemon initiates all outbound connections (NAT-friendly).
- **AgentBase → Daemon**: Push via WebSocket or SSE. Daemon opens an outbound persistent connection to AgentBase. AgentBase pushes commands through it (e.g., "move `inbox/report.pdf` to `Projects/Alpha/attachment/`").
- **Connection loss**: When daemon disconnects (laptop closed), AgentBase detects the lost connection. All read/search MCP tools continue working (DB is self-sufficient). Write-side tools (`kms_move`) return a clear "vault offline" failure. Pending commands queue on AgentBase until daemon reconnects.

### Batch behavior
When multiple files drop at once, daemon uploads them in parallel (capped at N concurrent uploads). AgentBase handles concurrent capture requests.

### CLAUDE.md sync
Daemon watches `CLAUDE.md` files in project and domain folders. On change, uploads their content to DB as a special record. The MCP server reads CLAUDE.md content from DB — always available regardless of daemon status.

### What daemon does NOT do
- No AI calls
- No DB access
- No classification
- No frontmatter reading/writing (exception: none — frontmatter is retired)
- No reconcile logic
- No summary generation

### Extraction runs locally (speed decision)
Text extraction (PDF → text, DOCX → text, etc.) runs on the daemon, not on AgentBase. Rationale:

- Extracted text is ~50-100x smaller than raw bytes (5MB PDF → ~50KB text)
- Upload of text is near-instant vs 2-10s for raw bytes on home internet
- Total capture latency: ~5-12s (local extract + text upload + LLM call) vs ~7-18s (raw upload + cloud extract + LLM call)
- LLM call (~3-8s) dominates either way — extraction location affects the upload segment
- Trade-off: daemon needs Python dependencies (`pdfplumber`, `python-docx`, `openpyxl`). Acceptable — daemon is already Python. Package with PyInstaller for one-click install.

---

## 5. Source-of-truth shift: DB replaces vault

### Before (current)
- Vault files are the source of truth
- DB (`documents` table) is a secondary index
- Summaries live as `.md` file content
- Metadata lives in YAML frontmatter
- `read_note()` reads from disk; DB mirrors it

### After (new model)
- **DB is the source of truth**
- Vault is the input zone (raw file drops) and reference archive
- Summaries, tags, title, key_topics, confidence, reasoning — all first-class DB columns
- Full extracted text stored in DB (not just summary) — enables `kms_read` to return content without daemon
- `kms_read` reads from DB. Always available.
- `kms_inspect` has two tiers (see §6)

### What this changes about constraint C-01
C-01 currently says: "Vault is source of truth; documents table is index only."

**C-01 must be rewritten.** In the new model:
- DB is source of truth for AI-generated content (summaries, classifications, metadata)
- Vault is source of truth for raw user files (the originals the user dropped)
- `vault/writer.py` as the sole write gate retires for AI output — AI output goes to DB
- The `updated_by_human` concept shifts: instead of "did the human edit this vault file," it becomes "did the human override this DB record" (if we keep human-override capability)

### What this preserves
- C-02 (human edits respected) — concept survives, implementation changes
- C-04 (FK pragma) — unchanged
- C-05 (migration-only schema) — unchanged
- C-06 (thresholds in config) — unchanged
- C-07 (prompts as YAML) — unchanged
- C-12 (Result types) — unchanged
- C-13 (audit log) — unchanged, writes to cloud DB
- C-14 (tools.py logic-free) — unchanged
- C-17 (no module-scope CONFIG in tests) — unchanged

---

## 6. kms_inspect two-tier model

`kms_inspect` returns raw extracted text for a file (not the AI summary — the actual content the AI was shown).

**Tier 1 — local path reference (cheap, laptop-dependent):**
Returns the vault path to the raw file. The consuming AI (e.g., Claude Desktop) can read it directly from the local filesystem. Free. Fast. Only works when user is on the same machine as the vault.

**Tier 2 — full extracted text in DB (costs storage, always available):**
The full extracted text (from handlers) is stored in DB at capture time alongside the summary. `kms_inspect` returns it from DB. Works from any device, laptop open or closed.

Design decision: both tiers should exist. Tier 2 is the mobility story. Tier 1 is a free optimization when local. Could be a per-request parameter, a per-user config, or automatic (try tier 2 first, fall back to tier 1 if not stored).

---

## 7. Graceful degradation — laptop open vs closed

| Capability | Laptop open (daemon connected) | Laptop closed (daemon offline) |
|---|---|---|
| `kms_search` | Full — reads from DB | Full — reads from DB |
| `kms_read` | Full — reads from DB | Full — reads from DB |
| `kms_vault_info` | Full — CLAUDE.md synced to DB | Full — last-synced CLAUDE.md from DB |
| `kms_inspect` (tier 2) | Full — reads from DB | Full — reads from DB |
| `kms_inspect` (tier 1) | Returns path, local AI reads file | Unavailable — file on closed laptop |
| `kms_move` | Full — daemon executes move | **Queued** — AgentBase stores command, daemon executes on reconnect |
| Capture (new file drop) | Full — daemon detects, uploads, AgentBase processes | **Paused** — no watcher running |
| Classify (inline) | Full — triggered by capture | **Paused** — no new captures |
| Reconcile | Partial — DB-only stages run; file-touching stages queued | DB-only stages only |

**Key insight:** all read/search/context operations work 24/7. Only write-to-vault operations degrade. The user's AI assistant can always answer "what do I know about X?" from any device.

---

## 8. AgentBase deployment model

Based on AgentBase documentation (crawled 2026-06-11 from `github.com/vngcloud/greennode-agentbase-skills` and skill files for `/agentbase`, `/agentbase-deploy`, `/agentbase-gateway`).

### Deployment type: Custom Agent
- Docker image built from the project
- Deployed via `runtime.sh create` with autoscaling configuration
- Container gets auto-injected env vars: `GREENNODE_CLIENT_ID`, `GREENNODE_CLIENT_SECRET`, `GREENNODE_AGENT_IDENTITY`, `GREENNODE_ENDPOINT_URL`
- Container listens on port 8080 (runtime contract)
- Health check at `/health`

### MCP exposure: Resource Gateway
- AgentBase Resource Gateway acts as a managed proxy for MCP servers
- Gateway speaks MCP JSON-RPC
- Supports inbound auth (NONE / IAM / JWT) — secures who can call the MCP tools
- The MCP server runs inside the Custom Agent container
- Gateway endpoint is the URL user-facing Claude connects to
- Gateway handles: auth, rate limiting, policy enforcement

### LLM provider
- Configurable via existing `config.yaml` provider abstraction
- Options: AgentBase platform LLM (OpenAI-compatible endpoint, integrated billing), Anthropic API (current), any OpenAI-compatible provider
- AgentBase auto-injects IAM credentials; LLM API keys go in container env vars

### DB persistence
- AgentBase containers are stateless (no persistent volume in docs)
- SQLite file must be persisted externally: backup to S3-compatible object store on writes, restore on container start
- Alternative: migrate to managed PostgreSQL (bigger effort, better durability)
- For MVP: SQLite + S3 backup. Personal vault DB is small (<100MB). Backup on each write batch is feasible.

### Memory service (optional, future)
- AgentBase offers a Memory Service (`memory/` API) for conversation history and semantic fact extraction
- Could be used for the user-facing AI's conversation memory across sessions
- Not needed for MVP — the knowledge base itself is the memory

---

## 9. What stays, what moves, what's new

### Stays (reusable as-is or with minor adaptation)
| Module | Why it stays |
|---|---|
| `core/result.py` | Pure data type. No filesystem dependency. |
| `core/audit.py` | Writes to DB. Works as-is. |
| `core/tags.py` | Pure validation. No filesystem dependency. |
| `core/confidence.py` | Pure routing logic. No filesystem dependency. |
| `core/pipeline.py` | Pipeline executor. No filesystem dependency. |
| `llm/provider.py` | Provider factory. Config-driven. Works anywhere. |
| `llm/prompt_loader.py` | Loads YAML from package. Ships in Docker image. |
| `pipelines/classify.py` | Pure function. No side effects. No filesystem dependency. |
| `retrieval/ranker.py` | Reads from DB. No filesystem dependency. |
| `retrieval/reranker.py` | Reads from DB. No filesystem dependency. |
| `retrieval/search.py` | Reads from DB. No filesystem dependency. |
| `retrieval/embeddings.py` | Writes to DB. No filesystem dependency. |
| `retrieval/keyword.py` | Writes to DB. No filesystem dependency. |
| `storage/db.py` | SQLite connection factory. Works as-is. |
| `storage/audit_log.py` | CRUD on audit table. Works as-is. |
| `storage/documents.py` | CRUD on documents table. Needs new columns (see below) but structure stays. |
| `handlers/*.py` | Text extraction logic. Moves to daemon but code is reusable. |
| `config/tags.yaml` | Tag taxonomy. Ships in Docker image. |
| `prompts/*.yaml` | All prompts. Ship in Docker image. |
| `mcp_server/tools.py` | Tool shims. Logic-free. Minor adaptation for new engine methods. |
| `mcp_server/context.py` | Context injection engine. Reads from DB + registry. Needs CLAUDE.md source change (DB not disk). |

### Moves (changes deployment location, may need interface adaptation)
| Module | From | To | Adaptation needed |
|---|---|---|---|
| `vault/watcher.py` | Main system | Daemon | Same watchdog logic, but on file change: extract + upload to AgentBase instead of calling capture pipeline directly. |
| `handlers/*.py` | Main system | Daemon | Same extraction code, but output goes to HTTPS upload, not to pipeline stage input. |
| `vault/paths.py` | Main system | Shared (both) | Daemon needs path resolution for move commands. Cloud needs it for registry. May need to split or make filesystem-independent. |
| `vault/move_guard.py` | Main system | Daemon | Move guard prevents watcher re-homing. Still needed on daemon side when daemon executes a move command. |

### Changes significantly (same module, different behavior)
| Module | What changes |
|---|---|
| `pipelines/capture.py` | Input changes: receives extracted text + file metadata over HTTPS, not a `Path`. Output changes: writes to DB (first-class fields), not to vault files via `write_note`. Still calls LLM summarize + metadata stages. |
| `storage/documents.py` | New columns: `full_body` (extracted text), `summary` (first-class, not read from file), `source_hash`, `original_filename`, `file_size_bytes`. `upsert()` writes these. No longer a secondary index — IS the primary store. |
| `core/config.py` | `VaultConfig` adapts: cloud doesn't have a local vault root. Daemon has it. Config splits into cloud config (DB path, LLM, MCP) and daemon config (vault root, AgentBase endpoint, auth). |
| `mcp_server/server.py` | Runs as AgentBase Custom Agent (port 8080, health endpoint). Bootstrap changes from CLI-like to container-like. |
| `mcp_server/_move.py` | Instead of calling `move_note()` directly, dispatches a move command to daemon via WebSocket/push channel. Async — command may queue if daemon offline. |
| `mcp_server/_resolve.py` | Tier 2: reads `full_body` from DB. Tier 1: returns vault path for local AI. No longer calls handler registry directly (extraction happened at capture time on daemon). |

### New (does not exist today)
| Component | Purpose |
|---|---|
| Daemon process | Watch vault, extract text, upload to AgentBase, execute commands. New Python package/entry point. |
| Daemon ↔ AgentBase API | REST endpoints for content upload; WebSocket for command push. New HTTP contract between daemon and cloud. |
| DB persistence layer | SQLite backup/restore to S3-compatible storage. Or migration to PostgreSQL. |
| Container entry point | `Dockerfile`, health endpoint, AgentBase runtime contract compliance (port 8080, `/health`). |
| AgentBase Gateway config | Resource Gateway definition pointing to the MCP server inside the container. Inbound auth config. |
| Project registry (cloud version) | Currently scans vault folders. Needs a DB-backed or daemon-reported alternative. Daemon sends folder structure on connect + on folder add/rename/delete. |

### Retires (no longer needed)
| Component | Why |
|---|---|
| `vault/writer.py` (AI writes) | AI output goes to DB. Daemon handles file moves only (no content writes to vault). |
| `vault/indexer.py` | Replaced by daemon watch-and-upload. |
| `vault/frontmatter.py` (write path) | No frontmatter writes. Read path may survive temporarily if existing vault files have frontmatter to migrate. |
| Sibling `.md` files under `.summaries/` | Summaries are DB records. No vault files generated by AI. |
| `vault/reader.py` (cloud side) | Cloud reads from DB, not from vault files. Daemon may still use reader for migration/backcompat. |
| `cli/main.py` (as primary entry point) | CLI becomes a dev/admin tool. Primary entry is the AgentBase container + daemon. |

---

## 10. Constraint updates needed

| Constraint | Current | New |
|---|---|---|
| **C-01** | "Vault is source of truth; documents table is index only" | **DB is source of truth for AI content. Vault is source of truth for raw user files.** |
| **C-02** | "updated_by_human=1 means hands off" | Concept survives. Implementation shifts to DB records. If human overrides an AI classification in the UI, that record is locked. |
| **C-03** | "write_note is a pure writer" | `write_note` retires for AI output. Daemon file moves are the only vault writes. New constraint needed for DB writes. |
| **C-10** | "CLI commands wrap async with asyncio.run()" | MCP server on AgentBase owns the event loop (FastMCP). CLI becomes secondary. |
| **C-11** | "load_dotenv called exactly once in cli/main.py" | Container entry point replaces CLI. `load_dotenv` in container entry or AgentBase auto-injects env vars. |
| **C-14** | "mcp_server/tools.py is logic-free" | **Unchanged.** |
| **C-15** | "Never add MCP tool before pipeline exists" | **Unchanged.** |
| **C-16** | "Schedulers come last" | **Unchanged.** Daemon is not a scheduler — it's an event-driven bridge. |
| NEW | — | **Daemon is stateless.** No DB, no cache, no AI state. Pure bridge. If daemon crashes, restart it — no data loss. |
| NEW | — | **All write-to-vault commands are idempotent.** Daemon may receive the same move command twice (reconnect replay). Must handle gracefully. |
| NEW | — | **DB writes must persist to durable storage.** SQLite + S3 backup, or managed DB. Container restart must not lose data. |

---

## 11. Impact on Phase 4 (MCP, currently being built)

Phase 4 plan (`docs/4_plans/P4_mcp_context_injection.md`) was written for the local-only model. Key impacts:

| Phase 4 component | Impact |
|---|---|
| Phase 1 (prereqs) | WAL checkpoint — still needed. Config block — needs cloud/daemon split. `mcp` dep — unchanged. `location` filter — unchanged (DB query). |
| Phase 2 (server shell) | Bootstrap changes: container entry instead of CLI-like startup. Port 8080. Health endpoint. AgentBase runtime contract. |
| Phase 3 (context engine) | `build_vault_info_response` reads CLAUDE.md from DB (daemon-synced), not from disk. `build_search_response` — unchanged (reads DB). `build_read_response` — reads from DB, not `read_note()`. |
| Phase 4 (binary resolver) | Tier 2: reads `full_body` from DB. Tier 1: returns vault path. No longer calls handler registry. |
| Phase 5 (note mover) | Dispatches move command to daemon instead of calling `move_note()` directly. Must handle daemon-offline case. |
| Phase 6 (tool shims) | Unchanged — still logic-free wrappers. |
| Phase 7 (AI instructions) | Add guidance about vault-offline degradation. |

**Recommendation:** Finish Phase 4 for the local-only model first (it's well-planned, tests exist, interfaces are stable). Then rearchitect as a separate phase. The local Phase 4 MCP server can be lifted into AgentBase with the adaptations above. Building it now gives a working MCP server to test against; rearchitecting later replaces the filesystem reads with DB reads.

OR: pause Phase 4, do the rearchitecture first, then build MCP directly for the cloud model. Trade-off: more upfront design work, but no throwaway code.

**This is a decision for the project owner.**

---

## 12. Open design questions (for next session)

These are explicitly NOT decided. They are the design phase's job.

1. **Daemon ↔ AgentBase API shape.** REST? gRPC? What endpoints? What auth? How does the daemon authenticate to AgentBase? (Likely: IAM service account credentials, same as any AgentBase client.)

2. **WebSocket command protocol.** What messages flow from AgentBase to daemon? Just "move file" or also "delete file", "create folder", etc.? Message format? Acknowledgment? Retry semantics?

3. **DB schema for first-class content.** What columns on `documents`? `full_body TEXT`, `summary TEXT`, `original_filename TEXT`, `file_size_bytes INTEGER`, `source_hash TEXT`? Or a separate `content` table?

4. **Project registry without filesystem.** Daemon reports folder structure on connect. Does AgentBase maintain a `folders` table? Or does daemon send a full tree snapshot and cloud rebuilds the registry in-memory?

5. **SQLite persistence on AgentBase.** S3 backup on every write? Periodic snapshots? Litestream? Managed Postgres instead? What's the recovery story when container restarts?

6. **Daemon packaging and distribution.** PyInstaller single binary? Homebrew formula? MSI installer for Windows? Auto-update mechanism?

7. **Existing vault migration.** Users with existing vaults have `.md` files with frontmatter, `.summaries/` folders, etc. Migration path: daemon does a one-time full scan + upload? Or old local system runs alongside new cloud system during transition?

8. **Reconcile split.** Which reconcile stages are DB-only (run on cloud) and which need the daemon? Stage-by-stage analysis needed.

9. **`kms_move` queuing.** When daemon is offline and user asks AI to move a file — does the AI get immediate "will be done when vault comes online" or does it wait? Timeout? Queue expiry?

10. **Multi-device vault.** If user has vault synced across laptop + desktop (via iCloud/OneDrive), do they run one daemon or two? Conflict resolution?

---

## 13. Investigation guide — where to look for the refactor

This section maps each refactor area to the exact files, line numbers, data classes, and coupling points the next agent must inspect before designing. Read these files; do NOT design from memory or from the summaries in §9.

### 13.1 Capture pipeline refactor (biggest change — 2241 lines)

**File:** `src/pipelines/capture.py`

**Key functions to inspect:**
- `capture_file()` (line 1495) — the main entry point. Currently takes a `Path`, calls handler → summarize → classify → store. Needs to accept extracted text + metadata from daemon HTTPS upload instead.
- `_store_md()` (line 924) — writes AI output to vault via `write_note()` + indexes via `documents.upsert()`. In new model: skip `write_note()`, write directly to DB with new first-class columns.
- `_store_nonmd()` (line 1113) — writes sibling `.md` under `.summaries/` + calls `write_note()`. In new model: no sibling file; summary goes to DB.
- `_classify_auto_md_move()` (line 407) — calls `move_note()` + `write_note()` + `replace_path()`. In new model: dispatches move command to daemon + updates DB.
- `capture_folder()` (line 2060) — batch capture. Same changes as `capture_file()` but multiplied.

**Coupling points:**
- `_store_md` and `_store_nonmd` both call `vault/writer.py::write_note()` — this call retires for AI output.
- Both call `documents.upsert(outcome)` where `outcome` is a `WriteOutcome` from `write_note()`. The `upsert()` signature takes `WriteOutcome` — this must change since there's no `write_note()` producing one.
- `_classify_auto_md_move` calls `vault/move_guard.py::get_active().register()` — move guard concept moves to daemon.

**Data classes to inspect:**
- `WriteOutcome` (`vault/writer.py:39`) — currently the input to `documents.upsert()`. This class is produced by `write_note()` which retires. Need a new input type for `upsert()` that doesn't depend on a vault write.
- `RawContent` (`handlers/base.py:47`) — the output of handlers. This stays. Daemon extracts → produces `RawContent` → uploads to AgentBase.
- `NoteMetadata` (`vault/frontmatter.py:56`) — frontmatter model. Retires as a vault concept but its fields (tags, project, summary, confidence, etc.) become first-class DB columns.

### 13.2 Documents table expansion

**File:** `src/storage/documents.py` (447 lines)

**Current `DocumentRow` (line 28):** `id, vault_path, title, summary, note_type, confidence, created_at, updated_at, updated_by_human, content_hash, batch_id, project, status, key_topics`.

**What's missing for source-of-truth:**
- `full_body` — complete extracted text (currently only in vault files, never in DB)
- `original_filename` — the raw filename as user dropped it
- `file_size_bytes` — for UI display and storage management
- `source_hash` — exists but verify it's populated consistently
- `tags` — currently `key_topics` is derived from tags minus prefixes; need full `tags` list in DB
- `summary` — already exists as a column but is populated from `meta.summary` which comes from frontmatter. In new model, populated directly from LLM output.

**`upsert()` (line 90):** Takes `WriteOutcome` as input. This couples DB writes to vault writes. Must be redesigned to accept AI output directly (e.g., a new dataclass or keyword args).

**`replace_path()` (line 232):** Also takes `WriteOutcome`. Same coupling.

**Migration path:** New migration file(s) in `storage/migrations/` per C-05.

### 13.3 Vault writer — what retires vs what stays

**File:** `src/vault/writer.py` (345 lines)

- `write_note()` (line 114) — **retires for AI output.** No AI-generated content writes to vault anymore.
- `move_note()` (line 181) — **moves to daemon.** Same logic but executed locally by daemon on command from AgentBase. Inspect: it calls `read_note()` internally, checks `updated_by_human`, uses `move_guard`. All of this happens on daemon side.

**`WriteOutcome` (line 39):** This is the return type of `write_note()` and the input to `documents.upsert()`. When `write_note()` retires, `WriteOutcome` either retires or is replaced by a DB-oriented equivalent.

### 13.4 Vault watcher → daemon watcher

**File:** `src/vault/watcher.py` (1129 lines)

**Key classes:**
- `_VaultEventHandler` (line 103) — the `watchdog.FileSystemEventHandler` subclass. Handles `on_created` (207), `on_modified` (367), `on_deleted` (407), `on_moved` (440).
- `VaultWatcher` (line 1056) — wraps `Observer` + debounce + callbacks.

**What changes:**
- Currently, `on_created` triggers capture pipeline locally. In new model: extract text locally → upload to AgentBase via HTTPS.
- Binary sync callbacks (`_handle_binary_delete`, `_handle_binary_move`) — these deal with `.summaries/` sibling files. If `.summaries/` retires, these callbacks simplify dramatically.
- Debounce logic (`_debounce` with `threading.Timer`) — stays. Still needed to avoid uploading mid-write.
- `_should_skip` — some skip rules change (no `.summaries/` to skip) but AI-output folder skip remains.

**Reuse opportunity:** The watcher's event handling, debounce, and skip logic are battle-tested (1129 lines, many edge cases handled). The daemon should reuse this module, replacing the callback targets (from "call capture pipeline" to "upload to AgentBase").

### 13.5 Project registry — filesystem dependency

**File:** `src/vault/registry.py` (see lines 63-148)

`build_registry(vault_cfg)` (line 63) scans `Projects/` and `Domain/` directories on disk. Reads each project's `CLAUDE.md` to find domain tags. Returns `ProjectRegistry` with `ProjectGroup` and `ProjectEntry` dataclasses.

**Problem:** On AgentBase there's no vault filesystem. Options for the next agent to evaluate:
- Daemon reports folder structure on connect (snapshot of `Projects/` and `Domain/` tree + CLAUDE.md contents)
- DB stores a `folders` or `registry` table that daemon keeps updated
- Registry rebuilt from `documents` table contents (each row has `project` and domain tags)

**Consumers of registry:**
- `pipelines/capture.py` (lines 620-630, 1881-1896) — builds registry for classify prompt
- `pipelines/classify.py` (line 79) — references `ProjectRegistry` type
- Phase 4 `mcp_server/context.py` (planned) — uses registry for project→domain lookup

### 13.6 Vault paths — split needed

**File:** `src/vault/paths.py` (555 lines)

Contains path resolution helpers (`resolve_placement()`, `project_attachment()`, `domain_attachment()`, `_location_context()`, `project_dir()`, `domain_dir()`, etc.). These compute vault-relative paths.

**Split analysis:**
- Path resolution for move commands → daemon side (daemon needs to know WHERE to move a file)
- `_location_context()` — used by capture to infer project/domain from file path. In new model, daemon sends the vault-relative path with the upload; cloud uses `_location_context()` logic to infer location. Could run on either side.
- `resolve_placement()` — determines `attachment/` vs root placement. Needed by daemon for move commands.

### 13.7 Frontmatter — retirement scope

**File:** `src/vault/frontmatter.py` (193 lines)

- `NoteMetadata` (line 56) — Pydantic model with tags, project, summary, confidence, etc. Fields survive as DB columns; the class itself may survive as an internal data transfer object.
- `parse()` / `dumps()` — YAML frontmatter serialization. Retires (no more frontmatter writes).
- `_DEPRECATED_KEYS`, `_KNOWN_KEYS` — retire with frontmatter.

### 13.8 Indexer — retirement

**File:** `src/vault/indexer.py` (322 lines)

`scan_non_md_drops()` and `scan_vault()` walk the filesystem to find unindexed files. Replaced entirely by daemon's watch-and-upload model. The daemon detects new files in real-time; no periodic scan needed.

**Exception:** on daemon startup, a one-time full scan may be needed to catch files that changed while daemon was offline. Similar to current `--scan` CLI command.

### 13.9 Reader — cloud vs daemon split

**File:** `src/vault/reader.py` (51 lines — very small)

`read_note(path) → Result[Note]` reads a file from disk and parses frontmatter.

- **Cloud side:** retires. Cloud reads from DB.
- **Daemon side:** may be used by `move_note()` (which reads the source before moving). Or daemon can simplify — just move the file without reading its content.
- **Migration:** if existing vaults need to be imported, the reader is needed for the one-time ingest.

### 13.10 MCP server adaptations (Phase 4, in progress)

**Files:** `src/mcp_server/` (planned — `server.py`, `context.py`, `tools.py`, `_resolve.py`, `_move.py`)

**Plan file:** `docs/4_plans/P4_mcp_context_injection.md`

Key adaptations already noted in §11. The next agent should inspect the Phase 4 plan's assumptions about filesystem access. Every `read_note()` call in the plan becomes a DB read. Every `move_note()` becomes a daemon command dispatch.

### 13.11 Config split

**File:** `src/core/config.py` — `VaultConfig` and `MainConfig`

Currently one config for everything. Needs split:
- **Cloud config:** DB path, LLM providers, MCP settings, AgentBase endpoint, thresholds
- **Daemon config:** vault root path, AgentBase endpoint (where to upload), auth credentials, watch settings

Inspect how `CONFIG` singleton is used across the codebase. Cloud modules should not reference `VaultConfig.root` (there's no vault on cloud). Daemon modules should not reference LLM or MCP settings.

```bash
# Run this to see every CONFIG usage:
grep -rn "CONFIG\." src/ --include="*.py" | grep -v "__pycache__"
```

### 13.12 Reconcile split

**File:** `src/pipelines/reconcile.py` (if exists) or stages scattered in `cli/main.py`

```bash
# Find reconcile stages:
grep -rn "def reconcile" src/ --include="*.py"
```

Each reconcile stage needs classification: DB-only (runs on cloud) vs file-touching (dispatches to daemon). The next agent must inspect each stage's filesystem dependencies.

### 13.13 Test suite impact

**Current:** ~1370 tests. Many use temp vaults with real files.

Tests that create temp vault files and call capture/classify/write_note will need adaptation:
- Cloud-side tests mock the daemon upload (no real files)
- Daemon-side tests use real temp files (as today)
- Integration tests need both sides running

```bash
# Estimate test impact — count tests that touch vault files:
grep -rn "write_note\|read_note\|move_note\|write_text\|vault_path" tests/ --include="*.py" -l | wc -l
```

---

## 14. Summary: next agent's starting checklist

1. Read this document end-to-end
2. Read `docs/4_plans/P4_mcp_context_injection.md` (Phase 4 plan — understand what was about to be built)
3. Read `CONSTRAINTS.md` (understand which constraints survive, change, or retire)
4. Inspect every file listed in §13 at the line numbers given
5. Run the `grep` commands in §13.11 and §13.12 to map CONFIG usage and reconcile stages
6. Decide: finish Phase 4 local-first then refactor, OR refactor first then build MCP for cloud
7. Start `/codebase-design-analysis` on the refactor scope

---

## 15. AgentBase platform references

All information about AgentBase was sourced from the following on 2026-06-11:

- **GitHub repo:** `github.com/vngcloud/greennode-agentbase-skills` — README + skill files
- **Platform reference skill:** `.claude/skills/agentbase/SKILL.md` — architecture, auth, SDK, runtime contract
- **Deploy skill:** `.claude/skills/agentbase-deploy/SKILL.md` — Custom Agent deployment, Container Registry, Docker workflow
- **Gateway skill:** `.claude/skills/agentbase-gateway/SKILL.md` — Resource Gateway (MCP proxy), inbound/outbound auth, target management

### Key AgentBase facts used in this document
- Custom Agent = user Docker image, autoscaling, named endpoints, VPC option
- Container port 8080, health at `/health`
- Auto-injected env vars: `GREENNODE_CLIENT_ID`, `GREENNODE_CLIENT_SECRET`, `GREENNODE_AGENT_IDENTITY`, `GREENNODE_ENDPOINT_URL`
- Resource Gateway proxies MCP JSON-RPC with auth (NONE/IAM/JWT) and policy enforcement
- No persistent volume evident in deploy docs — containers are stateless
- Memory Service available but optional (conversation history, semantic memory)
- SDK: `greennode-agentbase` (Python) for Identity, Memory clients
- IAM auth via service account (`client_id` / `client_secret` → bearer token)
- Container Registry: one pre-provisioned repo per user, managed credentials
