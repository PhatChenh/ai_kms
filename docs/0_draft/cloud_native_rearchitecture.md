# Cloud-Native Rearchitecture — AgentBase Deployment

_Created: 2026-06-11_
_Updated: 2026-06-12 (Session 2 — file-moving dropped, classify redesigned, knowledge_entries introduced)_
_Status: SINGLE SOURCE OF TRUTH for system direction. All other project docs (roadmap.md, CLAUDE.md, STATE.md, onboarding.md) describe the current codebase, not the target architecture. When they conflict with this document, this document wins._
_Audience: Next AI session doing design/spec/plan work_

---

## Session start instructions

**Read this document first. It is the single source of truth for system direction.**

**Reading order:**
1. This document (`docs/0_draft/cloud_native_rearchitecture.md`) — full system direction, all architectural decisions, what stays/moves/retires
2. `CLAUDE.md` — coding conventions and stable interfaces for existing code (Phases 0-4). Has a stale notice at top — heed it. Patterns referencing vault writes, frontmatter, file moves, .summaries/, inbox/, attachment/ routing are dead.
3. `docs/roadmap/roadmap.md` — completed phases (0-4) and stable interfaces only. **Phases 5-9 are scrapped. Do not implement anything from those sections.**
4. `STATE.md` — completed phase history only.

**Rules:**
- If any doc contradicts this document, this document wins.
- As you implement each piece, update the corresponding sections in CLAUDE.md, CONSTRAINTS.md, STATE.md, CONTEXT.md, onboarding.md, and architecture docs to match the new reality. Don't batch — update when you ship. Remove stale notices from files once their content is fully current.
- Do not read or act on: Phases 5-9 in roadmap, CLAUDE.md sections about vault/writer.py, frontmatter, .summaries/, move_guard, inbox/, attachment/ routing, kms_move, or CLAUDE.md per-project files. All dead.

**Things that will trip you up:**
- **Phase 4 MCP server just shipped (2026-06-12).** 5 working tools, 1258 tests. `_move.py` dies entirely, `context.py` needs major rework (knowledge_entries replaces CLAUDE.md), `_resolve.py` changes. But `tools.py` shim pattern and `server.py` bootstrap are adaptable. Don't delete — adapt.
- **~1370 tests will mostly break.** Many depend on `write_note`, frontmatter, `WriteOutcome`, vault file creation. You need a test strategy upfront — which to keep, rewrite, or delete. Don't try to keep all green while refactoring.
- **Hook enforcement in `.claude/settings.json` needs auditing.** Current hooks block direct vault writes, hardcoded thresholds, etc. Some become irrelevant when vault/writer.py dies. Others stay (thresholds in config, prompts as YAML). Audit hooks early.
- **Existing ADRs (0001-0011) — some invalidated.** ADR-0006 (editable/no-edit split) → dead. ADR-0007 (sibling naming) → dead. ADR-0009 (search RRF+rerank) → survives. ADR-0010/0011 (MCP context injection, write-path) → partially survives. Check which are still valid before designing.
- **Investigation guide (§15) line numbers may drift.** If any work touches existing code before rearchitecture starts, line numbers go stale. Use function names as primary anchors, line numbers as hints.
- **Project owner is non-technical.** Steers Claude Code, doesn't write code. Keep communication plain-English, behavior-focused.
- **Nothing has shipped to end users.** No migration concerns. Clean slate for all changed parts. Decide early: fresh rewrite vs incremental adaptation for each module.

---

## How to read this document

This document has 18 sections. They are cross-referencing — reading one section in isolation will give you an incomplete picture. **Read in this order:**

1. **§1-2** first — understand what this doc is and the two-AI model. (2 min)
2. **§3-5** — the architectural split, daemon spec, and source-of-truth shift. Core decisions. (8 min)
3. **§6** — file-moving dropped and why. This is the biggest divergence from the current codebase. (3 min)
4. **§7** — new classify pipeline (entity extraction into knowledge_entries). The new core feature. (5 min)
5. **§8** — three-tier retrieval model. (2 min)
6. **§9** — graceful degradation table. Product behavior the architecture must deliver. (1 min)
7. **§11-12** — what stays, what moves, what retires, constraint updates. Module-level impact. (5 min)
8. **§15** — investigation guide with exact file paths and line numbers. Read WHEN you start inspecting code, not before. (reference)
9. **§10, §13, §14, §16, §17** — supporting detail (web UI, open questions, Phase 4 impact, AgentBase deployment, starting checklist). Read as needed.

**Do NOT start designing from §15 alone.** The investigation guide tells you WHERE to look but not WHY things are changing. §3-7 give the WHY. §11-12 give the WHAT. §15 gives the WHERE.

### Warnings for the design phase

- **This is NOT a simple port.** The source-of-truth shifts from vault files to DB (§5). This changes the data flow direction, not just where code runs. Any design that treats this as "same code, different server" will miss the point.
- **File-moving is DEAD.** The system never moves, renames, or reorganizes the user's files. This kills inbox/, attachment/ routing, resolve_placement(), move_guard, sibling .summaries/, frontmatter writes, and the classify-as-routing concept. See §6.
- **Classify is completely redesigned.** Old classify = "pick a folder." New classify = "extract structured knowledge into multi-dimensional DB tables." See §7. Any design that treats classify as a routing decision is building the wrong thing.
- **`WriteOutcome` is dead.** It connected `vault/writer.py` → `storage/documents.py`. Both sides of that coupling change. See §15.1.
- **The daemon is simpler than originally planned.** No command execution from cloud. No file moves. Watch + extract + upload + report events. See §4.
- **The retrieval stack is safe.** `search.py`, `ranker.py`, `reranker.py`, `embeddings.py`, `keyword.py` — all DB-only, zero filesystem dependency. Don't redesign these unless the DB schema changes force column renames.
- **~1370 existing tests.** Many assume local vault files exist. Cloud-side tests will need mocks for daemon uploads. Daemon-side tests reuse the temp-vault pattern. Plan the test split early.
- **No migration concern.** Nothing has shipped to end users. Ignore all backward-compatibility references in the existing codebase.
- **Constraint C-01 must be rewritten before implementation starts.** Current C-01 says "vault is source of truth" — the refactor inverts this. Any implementer reading `CONSTRAINTS.md` without the update will build the wrong thing.

---

## 1. What this document is

A record of decisions made during discussions between the project owner and AI sessions on 2026-06-11 and 2026-06-12. It captures the **agreed direction, constraints, and open design questions** for rearchitecting AI-kms from a local-only system to a hybrid cloud-native deployment on VNG's AgentBase platform.

**Session 1 (2026-06-11):** Established the two-AI model, daemon/cloud split, DB-as-source-of-truth, AgentBase deployment model.

**Session 2 (2026-06-12):** Dropped file-moving entirely. Redesigned classify from folder-routing to multi-dimensional entity extraction. Introduced `knowledge_entries` table. Dropped inbox/, .summaries/, attachment/ routing, frontmatter, CLAUDE.md files. Added web UI scope. Simplified daemon (no command execution).

This document does NOT prescribe function signatures, class hierarchies, or module interfaces. Those belong to the design and spec phases. It DOES pin down the architectural split, the deployment model, data ownership, the product behavior model, and the knowledge extraction design — so the design phase starts from settled ground, not from relitigating these decisions.

---

## 2. Context: Two AIs, one knowledge base

The system has two distinct AI actors:

### Housekeeping AI (cloud, on AgentBase)
- Runs the capture pipeline (extract → summarize → store)
- Runs the classify pipeline (read content → extract structured knowledge into dimension tables)
- Runs reconcile passes (DB-only)
- No direct user interaction
- Deployed as a **Custom Agent** on AgentBase Runtime (Docker container, autoscaling, named endpoints)
- Triggered when daemon uploads new/changed content

### User-facing AI (any Claude client)
- The human's conversational AI (Claude Desktop, claude.ai web, Claude mobile)
- Consumes the knowledge base via MCP tools (`kms_search`, `kms_read`, `kms_vault_info`, `kms_inspect`)
- Does NOT run capture or classify — it queries and retrieves
- Connects to the MCP server hosted on AgentBase

### Why two AIs
The housekeeping AI is a system agent — it runs automatically when files change, extracts knowledge, writes summaries. The user-facing AI is the human's thinking partner — it searches, reads, and presents context. Separating them means the housekeeping AI can be improved/redeployed without touching the user's conversational experience, and the user's AI can access context from any device without the housekeeping system running.

---

## 3. Architectural split — what lives where

### On AgentBase (cloud)
| Component | Current location | Notes |
|---|---|---|
| Database | `data/kb.db` on local disk | **Becomes source of truth.** Summaries, tags, metadata, embeddings, FTS5, audit log, knowledge entries — all first-class DB. |
| Capture pipeline | `pipelines/capture.py` | Runs on AgentBase. Receives extracted content from daemon. Outputs structured summary + metadata to DB. Does NOT write to vault. |
| Classify pipeline | `pipelines/classify.py` | **Complete redesign.** Reads document content from DB → extracts structured knowledge into `knowledge_entries` table. Runs as separate async process after capture. |
| Knowledge entries | NEW | `knowledge_entries` table — multi-dimensional structured knowledge (people, projects, domains, etc.). Replaces CLAUDE.md files. See §7. |
| Search coordinator | `retrieval/search.py` | Reads from DB. Fully self-contained on cloud. |
| Ranker + reranker | `retrieval/ranker.py`, `retrieval/reranker.py` | Read from DB. No filesystem dependency. |
| Index layer | `retrieval/embeddings.py`, `retrieval/keyword.py` | Write to DB on capture. |
| LLM provider | `llm/provider.py` | Configurable provider. Can use AgentBase platform LLM, Anthropic API, or any OpenAI-compatible endpoint. |
| Prompt loader | `llm/prompt_loader.py` | YAML files ship inside the Docker image. |
| Config | `core/config.py`, `config/config.yaml` | Ships inside Docker image. Cloud config only (no vault root). |
| MCP server | `mcp_server/` | Hosted on AgentBase. Serves user-facing AI from anywhere. |
| Audit log | `storage/audit_log.py` | Writes to cloud DB. |
| Web UI | NEW | Browse knowledge entries + documents. Correct/comment for self-learning. Details deferred. See §10. |

### On user's machine (local)
| Component | Notes |
|---|---|
| Vault (raw files) | User's files in any folder structure they choose. No imposed organization. User owns these files completely. |
| Daemon | Thin Python process. Watches entire vault, extracts text, uploads to AgentBase, reports file events (move/rename/delete). |
| Handlers (extraction) | `handlers/*.py` — PDF, DOCX, XLSX text extraction runs locally for speed. Extracted text uploaded to AgentBase. If extraction fails, raw bytes uploaded as fallback. |

### On neither (removed from both)
| Component | Reason |
|---|---|
| `vault/writer.py` | System never writes to vault. All AI output goes to DB. |
| `vault/move_guard.py` | No file moves by system. Dead. |
| `vault/indexer.py` | Replaced by daemon watch + scan. |
| `vault/frontmatter.py` | No frontmatter reads or writes. Metadata is DB-only. |
| `vault/reader.py` | Cloud reads from DB. Daemon doesn't need to read note content (just extracts and uploads). |
| `vault/paths.py` (placement logic) | `resolve_placement()`, `project_attachment()`, `domain_attachment()` — all dead. No file routing. Daemon only needs vault-relative path computation. |
| Sibling `.md` files under `.summaries/` | Summaries are DB records. No vault files generated by AI. |
| `inbox/` as drop zone | No file moves → no staging area → no inbox/. |
| `attachment/` folders | No binary routing. Files stay where user puts them. |
| CLAUDE.md files (per-project/domain) | Replaced by `knowledge_entries` table. Living context is in DB, not in files. |
| `kms_move` MCP tool | System doesn't move files. Dead. |

---

## 4. Daemon specification

### Role
The daemon is a **thin bridge** between the user's local filesystem and the AgentBase cloud. It has no AI, no DB, no classification logic. It does two things:

1. **Watch → Extract → Upload**: detect file changes in the vault, extract text content locally (using handlers), upload extracted content + file metadata to AgentBase via HTTPS. If local extraction fails, upload raw bytes as fallback.
2. **Report events**: when user moves, renames, or deletes files, report the event to AgentBase so DB can update `vault_path` accordingly. Pure bookkeeping.

### Communication pattern
- **Daemon → AgentBase**: HTTPS REST calls. Daemon initiates all outbound connections (NAT-friendly).
  - File created/modified: extract text → POST content + metadata
  - File moved/renamed: POST old path + new path
  - File deleted: POST deleted path
- **AgentBase → Daemon**: No commands flow in this direction. System never moves or modifies user files. (WebSocket/SSE channel reserved for future features if needed.)
- **Connection loss**: When daemon disconnects (laptop closed), AgentBase detects lost connection. All read/search/context MCP tools continue working (DB is self-sufficient). Capture pauses until daemon reconnects.

### Watch scope
Daemon watches the **entire vault directory tree**, not just a drop zone. Every file anywhere in the vault is a capture candidate.

### Scan on startup
On daemon start, full vault walk to catch changes that happened while daemon was offline. Diff vault state against DB state (by path + content hash), report deltas to AgentBase. Equivalent of current `kms reconcile` but simplified — just "what files are new/changed/moved/deleted since last sync."

### Batch behavior
When multiple files drop at once, daemon uploads them in parallel (capped at N concurrent uploads). AgentBase handles concurrent capture requests.

### What daemon does NOT do
- No AI calls
- No DB access
- No classification
- No file moves or renames (system never reorganizes user's files)
- No frontmatter reading/writing
- No reconcile logic (scan is just file-diff, not the 7-stage reconcile)
- No summary generation
- No command execution from cloud

### Extraction runs locally (speed decision)
Text extraction (PDF → text, DOCX → text, etc.) runs on the daemon, not on AgentBase. Rationale:

- Extracted text is ~50-100x smaller than raw bytes (5MB PDF → ~50KB text)
- Upload of text is near-instant vs 2-10s for raw bytes on home internet
- Total capture latency: ~5-12s (local extract + text upload + LLM call) vs ~7-18s (raw upload + cloud extract + LLM call)
- LLM call (~3-8s) dominates either way — extraction location affects the upload segment
- **Fallback**: if local extraction fails (unsupported format, corrupted file, handler crash), daemon uploads raw bytes. Cloud extracts on its end. Capture always works.
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
- **DB is the source of truth** for all AI-generated content
- Vault is the user's file storage — system reads from it, never writes to it
- Summaries, tags, metadata, confidence, reasoning — all first-class DB columns
- Full extracted text stored in DB (`full_body`) — enables content access without daemon
- Structured knowledge stored in `knowledge_entries` table — replaces CLAUDE.md
- `kms_read` reads from DB. Always available.
- `kms_inspect` has three tiers (see §8)

### What this changes about constraint C-01
C-01 currently says: "Vault is source of truth; documents table is index only."

**C-01 must be rewritten.** In the new model:
- DB is source of truth for AI-generated content (summaries, classifications, knowledge entries)
- Vault is source of truth for raw user files (the originals the user created/dropped)
- System never writes to vault — no `vault/writer.py`, no frontmatter, no sibling files
- The `updated_by_human` concept shifts to DB: user corrections via web UI mark entries as human-overridden

### What this preserves
- C-02 (human edits respected) — concept survives via web UI corrections on DB records
- C-04 (FK pragma) — unchanged
- C-05 (migration-only schema) — unchanged
- C-06 (thresholds in config) — unchanged
- C-07 (prompts as YAML) — unchanged
- C-12 (Result types) — unchanged
- C-13 (audit log) — unchanged, writes to cloud DB
- C-14 (tools.py logic-free) — unchanged
- C-17 (no module-scope CONFIG in tests) — unchanged

---

## 6. File-moving is dead — rationale and consequences

### The decision
The system will **never** move, rename, organize, or restructure the user's files. Files stay exactly where the user puts them. The folder structure is entirely user-controlled.

### Why
The old model imposed a folder structure (`inbox/` → `Projects/<A>/` or `Domain/<D>/`) which:
- Assumes users want a specific organization scheme — risky assumption for a product
- Creates complexity: placement logic, move_guard, confidence-gated routing, sibling file sync, binary sync callbacks, 7-stage reconcile
- Breaks when users have their own organization preferences
- Makes the daemon complex (command execution, WebSocket channel for move instructions)

The new model: system is a **knowledge extractor**, not a **file organizer**. All intelligence lives in DB. Users organize files however they want. System just watches, reads, and extracts.

### What dies
| Component | Lines | Why dead |
|---|---|---|
| `inbox/` as concept | — | No staging area needed when system doesn't move files |
| `resolve_placement()` | `vault/paths.py` | No placement decisions |
| `project_attachment()`, `domain_attachment()` | `vault/paths.py` | No attachment routing |
| `_classify_auto_md_move()` | `capture.py:407` | No auto-move on classify |
| `move_note()` | `vault/writer.py:181` | No file moves |
| `WriteOutcome` | `vault/writer.py:39` | No vault writes produce outcomes |
| `write_note()` | `vault/writer.py:114` | No AI writes to vault |
| `move_guard.py` | entire module | No moves to guard |
| `.summaries/` folders | vault structure | No sibling files |
| `_handle_binary_delete`, `_handle_binary_move` | `vault/watcher.py` | No binary sync needed |
| `_sibling_for()` | `vault/watcher.py` | No sibling computation |
| `no_edit_extensions` config | `config.yaml` | No binary placement routing |
| `attachment/` folder structure | vault layout | No hidden binary folders |
| Reconcile stages 2-4 | orphan/stale binary/sibling stages | No binaries or siblings to reconcile |
| `kms_move` MCP tool | `mcp_server/tools.py` | No file moves |
| CLAUDE.md per-project files | vault files | Replaced by knowledge_entries |
| Confidence-gated move routing | `core/confidence.py` usage in capture | No AUTO/SUGGEST/CLUELESS move paths |

### What simplifies
- **Daemon**: watch + extract + upload + report events. No command execution. No WebSocket needed.
- **Capture pipeline**: extract → summarize → store to DB. No vault writes, no move logic.
- **Watcher**: no binary sync callbacks, no sibling management, no _should_skip for .summaries/. Just detect file events and report.
- **Classify**: standalone async process. Pure DB-to-DB. No file system interaction at all.

### What classify becomes
See §7. Classification transforms from "pick a destination folder" to "extract structured knowledge into dimension tables."

---

## 7. New classify pipeline — multi-dimensional entity extraction

### Overview
Classify reads document content from DB (written by capture) and extracts structured knowledge into a `knowledge_entries` table. Each entry is one atomic fact about one entity in one dimension.

This replaces CLAUDE.md files as the system's living context. Instead of writing context into files, all structured knowledge lives in DB — queryable, updatable, version-tracked.

### The knowledge_entries table

Single universal table. Adding a new dimension = config change + prompt update, zero schema change.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `dimension` | TEXT NOT NULL | Config-enforced. e.g. `people`, `projects`, `domains` |
| `entity` | TEXT NOT NULL | The thing this fact is about. e.g. `Anthony`, `Movie Q2`, `Finance` |
| `tag` | TEXT NOT NULL | Config-enforced sub-category within dimension. e.g. `role`, `deadline`, `policy`. Every tag set has mandatory `other` catch-all. |
| `fact` | TEXT NOT NULL | The atomic piece of knowledge. e.g. "Product Lead for Movie Q2" |
| `status` | TEXT NOT NULL | One of: `confident`, `pending`, `retired` |
| `confidence` | REAL | AI confidence score for this extraction |
| `sources` | TEXT (JSON array) | Which document(s) this fact was extracted from. Vault paths or document IDs. |
| `reasoning` | TEXT | Why this status/confidence. For retired entries: why retired. |
| `created_at` | TEXT | When first extracted |
| `updated_at` | TEXT | When last modified |

### Entry lifecycle: confident → pending → retired

- **Confident**: high-confidence extraction. Direct statement, confirmed decision. Treated as current truth.
- **Pending**: medium/low confidence. Hedging language, speculation, secondhand info. Surfaced to user for confirmation on web UI. Can be promoted to confident when new evidence arrives.
- **Retired**: superseded by newer information. Not deleted — stays for history, revert, searching old state. Has explanation for why retired.

Confidence drives initial status — same gating pattern as before, but gates entry status instead of move action. Thresholds from config.

### Extraction flow

When classify processes a new document:
1. Read document content from `documents.full_body`
2. For each dimension, query existing `confident` + `pending` entries for entities mentioned in the document
3. Call LLM with: document content + existing entries → extract new facts, update existing facts, retire superseded facts
4. Write new/updated entries to `knowledge_entries` table
5. Audit log entry for every extraction

**AI sees confident + pending entries only.** Retired entries stay in DB for user browsing but are NOT fed into the extraction prompt (reduces noise and token cost).

### Extraction principles (prompt-enforced)

- **Concise**: keep only crucial context per entity. Same discipline as CLAUDE.md: short, actionable, no noise. Not a dumping ground.
- **Incremental update with retirement**: when new info supersedes old, AI replaces — doesn't accumulate. Old decisions/status that got overridden get retired with explanation.
- **Source-traced**: every entry links back to the document(s) it came from. Enables user investigation and correction.
- **Evidence accumulation**: multiple weak signals (pending entries) from different documents can combine to promote an entry to confident.

### Dimensions and tags — config-enforced

```yaml
# Example config structure (exact format TBD in design phase)
dimensions:
  people:
    tags: [role, relationship, contact, preference, other]
  projects:
    tags: [status, deadline, decision, blocker, milestone, other]
  domains:
    tags: [policy, principle, fact, trend, other]
```

- **Dimensions**: finite list in config. Adding a dimension = config change + prompt update.
- **Tags**: finite list per dimension in config. AI must pick from allowed set. Validation rejects unknown values.
- **Mandatory `other` tag**: every tag set must include `other` as catch-all for facts that don't fit existing tags.
- **Tag set is mutable**: user can add/remove/replace tags via web UI. On change:
  - New tag added → house AI re-scans `other` entries to find facts that belong to the new tag
  - Tag removed → entries under that tag reassigned (house AI decides where they go)
  - Tag replaced → entries re-tagged

### Extensibility

Adding a new dimension (e.g. `interests`, `labels`):
1. Add to config YAML with its tag set
2. Update extraction prompt to include the new dimension
3. Zero schema change, zero code change
4. House AI starts extracting into the new dimension on next classify run

### Capture vs classify separation

**Capture** and **classify** are separate async processes:
- **Capture**: daemon uploads content → cloud generates structured summary → stores in `documents` table. Fast. File is searchable immediately after capture.
- **Classify**: reads document content from DB → extracts facts into `knowledge_entries`. Heavier (reads existing entries, compares, decides). Runs asynchronously after capture completes.

Classify may benefit from batching — processing multiple new documents together for better cross-document context.

### Self-learning loop

User corrections via web UI create learning signal:
- User promotes pending → confident: validates AI extraction
- User retires a confident entry: corrects AI mistake
- User edits a fact: provides ground truth

These corrections are recorded and fed back as few-shot examples to improve future extractions. Same concept as Phase 7 (Self-Learning) but for knowledge entries instead of file moves.

---

## 8. Three-tier retrieval model

### Tier 1 — Summary cards (cheapest, always available)
Structured summary from `documents` table + relevant `knowledge_entries` for matched entities. Answers "what is this about?" without touching full content.

Summary is now **structured** (not the old 2-4 sentence frontmatter blurb):
- Named sections: overview, key points, decisions, action items, people mentioned
- Rich enough that reading only the summary gives 80% of the value
- Generated by capture pipeline (one LLM call, stored in `documents.summary`)

### Tier 2 — Full extracted text (medium, always available)
`full_body` column from `documents` table. The complete extracted text that was sent to the LLM for summarization. Always available from DB, any device, laptop open or closed.

### Tier 3 — Raw file access (expensive, laptop-dependent)
Returns the vault path to the actual file on disk. Consuming AI (Claude Desktop) reads the file directly from local filesystem. Only works when daemon is connected (laptop open). Gives access to original binary — images, formatting, charts, things text extraction loses.

### Context injection
Context injection uses **both** knowledge_entries and search results:
- **Knowledge entries first**: distilled, up-to-date facts. "What we know about X." Structured, concise, immediately useful.
- **Search results second**: raw source material. "Where we learned it." Supporting evidence on demand.

Pattern: knowledge entries = fast structured answer. Search = supporting evidence for deep dives.

---

## 9. Graceful degradation — laptop open vs closed

| Capability | Laptop open (daemon connected) | Laptop closed (daemon offline) |
|---|---|---|
| `kms_search` | Full — reads from DB | Full — reads from DB |
| `kms_read` | Full — reads from DB | Full — reads from DB |
| `kms_vault_info` | Full — knowledge_entries from DB | Full — knowledge_entries from DB |
| `kms_inspect` (tier 1-2) | Full — reads from DB | Full — reads from DB |
| `kms_inspect` (tier 3) | Returns path, local AI reads file | Unavailable — file on closed laptop |
| Capture (new file) | Full — daemon detects, uploads, cloud processes | **Paused** — no watcher running |
| Classify | Full — triggered after capture | **Paused** — no new captures to classify |
| Web UI | Full | Full — reads from DB |

**Key insight:** all read/search/context operations work 24/7. Only capture (new file ingestion) pauses when laptop is closed. The user's AI assistant can always answer "what do I know about X?" from any device.

**Compared to old model:** `kms_move` is gone (system doesn't move files). No write-to-vault operations exist at all. The only degradation is "no new files captured while laptop is closed."

---

## 10. Web UI — replaces CLAUDE.md and Obsidian as user's window

### Why needed
With no frontmatter, no sibling .md files, and no CLAUDE.md, the user has no way to see AI output by browsing their vault in Obsidian. The vault is now just their raw files. All AI-generated knowledge lives in DB.

The web UI becomes the user's window into their knowledge base.

### Minimum viable capabilities

1. **Browse** — view knowledge entries grouped by dimension/entity. View document summaries. Filter by dimension, entity, tag, status.
2. **Correct** — change entry status (promote pending → confident, retire wrong entries, edit facts). Change document metadata (project/domain assignments). Intuitive and system-readable — corrections feed self-learning loop for house AI.
3. **Comment** — add notes/context to entries that house AI should consider in future extractions.

### Design details deferred
Exact UI design, tech stack, hosting model — all deferred to design phase. The three capabilities above are the requirement.

---

## 11. What stays, what moves, what's new

### Stays (reusable as-is or with minor adaptation)
| Module | Why it stays |
|---|---|
| `core/result.py` | Pure data type. No filesystem dependency. |
| `core/audit.py` | Writes to DB. Works as-is. |
| `core/tags.py` | Pure validation. Extends to validate dimensions and tags for knowledge_entries. |
| `core/confidence.py` | Pure routing logic. Now gates entry status instead of move action. |
| `core/pipeline.py` | Pipeline executor. No filesystem dependency. |
| `llm/provider.py` | Provider factory. Config-driven. Works anywhere. |
| `llm/prompt_loader.py` | Loads YAML from package. Ships in Docker image. |
| `retrieval/ranker.py` | Reads from DB. No filesystem dependency. |
| `retrieval/reranker.py` | Reads from DB. No filesystem dependency. |
| `retrieval/search.py` | Reads from DB. No filesystem dependency. |
| `retrieval/embeddings.py` | Writes to DB. No filesystem dependency. |
| `retrieval/keyword.py` | Writes to DB. No filesystem dependency. |
| `storage/db.py` | SQLite connection factory. Works as-is. |
| `storage/audit_log.py` | CRUD on audit table. Works as-is. |
| `handlers/*.py` | Text extraction logic. Code reused on daemon side. |
| `config/tags.yaml` | Tag taxonomy. Extends with dimension/tag definitions. Ships in Docker image. |
| `prompts/*.yaml` | All prompts. Ship in Docker image. New prompts for entity extraction. |
| `mcp_server/tools.py` | Tool shims. Logic-free. Remove `kms_move`, adapt others for new engine. |
| `mcp_server/context.py` | Context injection engine. Needs redesign to pull from knowledge_entries instead of CLAUDE.md. |

### Moves (changes deployment location)
| Module | From | To | Adaptation needed |
|---|---|---|---|
| `vault/watcher.py` | Main system | Daemon | Dramatically simplified. No binary sync callbacks, no sibling management. On file change: extract + upload. On file move/delete: report event. |
| `handlers/*.py` | Main system | Daemon | Same extraction code, output goes to HTTPS upload, not pipeline stage input. |

### Changes significantly (same module, different behavior)
| Module | What changes |
|---|---|
| `pipelines/capture.py` | Input: extracted text + file metadata from daemon HTTPS upload (not a `Path`). Output: structured summary + metadata to DB (not vault files). No classify inline. No file moves. No sibling writes. Dramatically simpler. |
| `pipelines/classify.py` | **Complete redesign.** No longer "pick a folder." Now: read document content from DB → extract structured knowledge into `knowledge_entries` across dimensions. Read-modify-write on existing entries. Separate async process from capture. |
| `storage/documents.py` | New columns: `full_body`, `original_filename`, `file_size_bytes`, `tags` (full list). `summary` populated from LLM output directly. `upsert()` redesigned — no longer takes `WriteOutcome`. No longer a secondary index — IS the primary store. |
| `core/config.py` | Splits into cloud config (DB path, LLM, MCP, dimensions/tags) and daemon config (vault root, AgentBase endpoint, auth, watch settings). |
| `mcp_server/server.py` | Runs as AgentBase Custom Agent (port 8080, health endpoint). Bootstrap changes from CLI-like to container-like. |
| `mcp_server/_resolve.py` | Tier 2: reads `full_body` from DB. Tier 3: returns vault path. No longer calls handler registry. |
| `mcp_server/context.py` | Context injection reads from `knowledge_entries` table instead of CLAUDE.md files. |

### New (does not exist today)
| Component | Purpose |
|---|---|
| `knowledge_entries` table | Multi-dimensional structured knowledge. Replaces CLAUDE.md. See §7. |
| Daemon process | Watch vault, extract text, upload to AgentBase, report file events. New Python package/entry point. |
| Daemon ↔ AgentBase API | REST endpoints for content upload and event reporting. New HTTP contract. |
| DB persistence layer | SQLite backup/restore to S3-compatible storage. Or migration to PostgreSQL. |
| Container entry point | `Dockerfile`, health endpoint, AgentBase runtime contract compliance (port 8080, `/health`). |
| AgentBase Gateway config | Resource Gateway definition pointing to MCP server inside container. Inbound auth config. |
| Web UI | Browse knowledge entries + documents. Correct/comment. Self-learning input. |
| Dimension/tag config | Config file defining allowed dimensions and their tag sets. |
| Entity extraction prompts | New YAML prompts for multi-dimensional knowledge extraction. |

### Retires (no longer needed)
| Component | Why |
|---|---|
| `vault/writer.py` | System never writes to vault. All AI output goes to DB. |
| `vault/move_guard.py` | No file moves by system. |
| `vault/indexer.py` | Replaced by daemon watch + scan. |
| `vault/frontmatter.py` | No frontmatter reads or writes. Metadata is DB-only. |
| `vault/reader.py` | Cloud reads from DB. Daemon extracts raw text, doesn't parse frontmatter. |
| `vault/paths.py` (most of it) | Placement logic dead. Only vault-relative path computation survives (for daemon). |
| `inbox/` concept | No drop zone. Entire vault is watched. |
| `.summaries/` folders | No sibling files. Summaries in DB. |
| `attachment/` folders | No binary routing. |
| CLAUDE.md per-project files | Replaced by knowledge_entries. |
| `kms_move` MCP tool | No file moves. |
| `_classify_auto_md_move()` | No auto-move on classify. |
| `WriteOutcome` class | No vault writes produce outcomes. |
| `NoteMetadata` (as frontmatter model) | Fields survive as DB columns; Pydantic model for frontmatter parsing retires. |
| Reconcile 7-stage pipeline | Replaced by daemon scan (file-diff on startup). DB-only reconcile may survive in simplified form. |
| `cli/main.py` (as primary entry) | CLI becomes dev/admin tool. Primary entry is container + daemon. |
| Confidence-gated move routing | No AUTO/SUGGEST/CLUELESS move paths. Confidence now gates entry status. |

---

## 12. Constraint updates needed

| Constraint | Current | New |
|---|---|---|
| **C-01** | "Vault is source of truth; documents table is index only" | **DB is source of truth for AI content. Vault is user's file storage — read-only input to the system.** |
| **C-02** | "updated_by_human=1 means hands off" | Concept survives via web UI. User corrections on DB records mark entries as human-overridden. |
| **C-03** | "write_note is a pure writer" | `write_note` retires entirely. System never writes to vault. |
| **C-10** | "CLI commands wrap async with asyncio.run()" | MCP server on AgentBase owns the event loop. CLI becomes secondary. |
| **C-11** | "load_dotenv called exactly once in cli/main.py" | Container entry point replaces CLI. AgentBase auto-injects env vars. |
| **C-14** | "mcp_server/tools.py is logic-free" | **Unchanged.** |
| **C-15** | "Never add MCP tool before pipeline exists" | **Unchanged.** |
| **C-16** | "Schedulers come last" | **Unchanged.** |
| NEW | — | **System never writes to vault.** No file moves, no frontmatter, no sibling files. Vault is read-only input. |
| NEW | — | **Daemon is stateless.** No DB, no cache, no AI state. Pure bridge. Crash → restart → no data loss. |
| NEW | — | **Dimensions and tags are config-enforced.** AI cannot invent dimensions or tags. Validation rejects unknown values. |
| NEW | — | **Every knowledge entry has sources.** No entry without traceability to the document(s) it was extracted from. |
| NEW | — | **DB writes must persist to durable storage.** SQLite + S3 backup, or managed DB. Container restart must not lose data. |

---

## 13. Impact on Phase 4 (MCP, built for local-only model)

Phase 4 was completed 2026-06-12 for the local-only model. Key impacts for the rearchitecture:

| Phase 4 component | Impact |
|---|---|
| `server.py` | Bootstrap changes: container entry instead of CLI-like startup. Port 8080. Health endpoint. |
| `context.py` (ContextInjectionEngine) | **Major change.** Currently reads CLAUDE.md from disk + builds context from search. New: reads from `knowledge_entries` table. Project→domain lookup from knowledge_entries dimension, not filesystem registry. |
| `_resolve.py` (binary resolver) | Tier 2: reads `full_body` from DB. Tier 3: returns vault path. No longer calls handler registry. |
| `_move.py` (note mover) | **Dead.** System doesn't move files. Remove entirely. |
| `tools.py` (tool shims) | Remove `kms_move`. Adapt remaining 4 tools for new engine methods. `kms_vault_info` returns knowledge_entries summary instead of CLAUDE.md content. |
| `AI_INSTRUCTIONS.md` | Rewrite for new capabilities. Remove move instructions. Add knowledge entry context. |

---

## 14. Open design questions (for next session)

These are explicitly NOT decided. They are the design phase's job.

1. **Daemon ↔ AgentBase API shape.** REST endpoints for content upload + event reporting. Auth model. (Likely: IAM service account credentials.)

2. **DB schema for knowledge_entries.** Exact column types, indexes, FK relationships to documents table. Should `sources` be a JSON array column or a junction table?

3. **Classify batching.** Process documents one at a time or in batches? Batching gives better cross-document context but adds complexity. Batch size? Trigger (time-based, count-based, or both)?

4. **Entity resolution.** "Anthony" in one note and "Anthony Nguyen" in another — same entity? How does the AI handle name variants? Should there be an entity normalization step?

5. **SQLite persistence on AgentBase.** S3 backup on every write? Periodic snapshots? Litestream? Managed Postgres instead?

6. **Daemon packaging and distribution.** PyInstaller single binary? Homebrew formula? Auto-update?

7. **Web UI tech stack.** Server-side rendered? SPA? Hosted on AgentBase alongside the MCP server? Separate service?

8. **knowledge_entries query API.** What MCP tool exposes knowledge entries? New `kms_knowledge` tool? Or extend `kms_search` to search across both documents and knowledge entries?

9. **Tag change propagation.** When user adds/removes a tag, how does the re-scan work? Full re-scan of all `other` entries? Scoped to the changed dimension? Async job?

10. **Capture idempotency in cloud model.** Daemon might upload the same file twice (network retry, restart). Cloud must handle duplicate uploads gracefully. Content hash as dedup key?

11. **Multi-device vault.** If user has vault synced across devices, do they run one daemon or two? Conflict resolution?

---

## 15. Investigation guide — where to look for the refactor

This section maps each refactor area to the exact files, line numbers, data classes, and coupling points the next agent must inspect before designing. Read these files; do NOT design from memory or from the summaries in §11.

### 15.1 Capture pipeline refactor (biggest change — 2241 lines)

**File:** `src/pipelines/capture.py`

**Key functions to inspect:**
- `capture_file()` (line 1495) — the main entry point. Currently takes a `Path`, calls handler → summarize → classify → store. Needs to accept extracted text + metadata from daemon HTTPS upload instead. Classify moves out to separate process.
- `_store_md()` (line 924) — writes AI output to vault via `write_note()` + indexes via `documents.upsert()`. In new model: skip `write_note()`, write structured summary directly to DB.
- `_store_nonmd()` (line 1113) — writes sibling `.md` under `.summaries/` + calls `write_note()`. In new model: no sibling file; summary goes to DB. Same as `_store_md()` effectively.
- `_classify_auto_md_move()` (line 407) — **DEAD.** Calls `move_note()` + `write_note()` + `replace_path()`. Entire function retires.
- `capture_folder()` (line 2060) — batch capture. Same simplification as `capture_file()`.

**Coupling points that break:**
- `_store_md` and `_store_nonmd` both call `vault/writer.py::write_note()` — this call is dead.
- Both call `documents.upsert(outcome)` where `outcome` is a `WriteOutcome` from `write_note()`. `upsert()` must be redesigned to accept AI output directly.
- `_classify_auto_md_move` calls `vault/move_guard.py::get_active().register()` — dead.
- Inline classify call — moves to separate async process.

**Data classes to inspect:**
- `WriteOutcome` (`vault/writer.py:39`) — **DEAD.** Currently the input to `documents.upsert()`. Needs replacement.
- `RawContent` (`handlers/base.py:47`) — the output of handlers. **STAYS.** Daemon extracts → produces `RawContent` → uploads to AgentBase.
- `NoteMetadata` (`vault/frontmatter.py:56`) — **RETIRES** as frontmatter model. Fields survive as DB columns. May survive as internal data transfer object for capture output.

### 15.2 Documents table expansion

**File:** `src/storage/documents.py` (447 lines)

**Current `DocumentRow` (line 28):** `id, vault_path, title, summary, note_type, confidence, created_at, updated_at, updated_by_human, content_hash, batch_id, project, status, key_topics`.

**What's missing for source-of-truth:**
- `full_body` — complete extracted text (currently only in vault files)
- `original_filename` — the raw filename as user dropped it
- `file_size_bytes` — for UI display and storage management
- `tags` — full tag list in DB (currently only `key_topics` which is derived)
- `summary` — already exists but must become the structured summary from LLM

**`upsert()` (line 90):** Takes `WriteOutcome` as input. Dead coupling. Must be redesigned.

**`replace_path()` (line 232):** Also takes `WriteOutcome`. Same dead coupling. Simplifies to just updating `vault_path` on user file move (reported by daemon).

**Migration:** New migration file(s) in `storage/migrations/` per C-05. Plus new `knowledge_entries` table.

### 15.3 Everything that retires in vault/

| File | Lines | Status |
|---|---|---|
| `vault/writer.py` | 345 | **Entire module dead.** `write_note()`, `move_note()`, `WriteOutcome` — all retire. |
| `vault/move_guard.py` | ~60 | **Entire module dead.** No file moves to guard. |
| `vault/indexer.py` | 322 | **Entire module dead.** Replaced by daemon watch + scan. |
| `vault/frontmatter.py` | 193 | **Entire module dead.** No frontmatter reads or writes. |
| `vault/reader.py` | 51 | **Dead on cloud side.** Daemon doesn't need it either (extracts raw text, doesn't parse frontmatter). |
| `vault/paths.py` | 555 | **Mostly dead.** Placement logic (`resolve_placement`, `project_attachment`, `domain_attachment`, `_is_in_managed_attachment`) all dead. Only vault-relative path computation might survive for daemon. |
| `vault/watcher.py` | 1129 | **Moves to daemon, dramatically simplified.** Binary sync callbacks dead. Sibling management dead. Core watch + debounce + skip logic reusable. |

### 15.4 Classify pipeline — complete redesign

**File:** `src/pipelines/classify.py` (current: pure function, ~200 lines)

Current `classify()` takes a subject string + valid destinations → returns `ClassifyResult(project, domain, confidence, reasoning)`. This is a folder-routing function.

**New classify is fundamentally different:**
- Input: document ID (reads content from DB)
- Process: for each configured dimension, query existing entries → call LLM with content + existing entries → extract/update/retire facts
- Output: new/updated rows in `knowledge_entries` table
- Separate async process from capture

The current `classify.py` code is not salvageable for the new purpose. It's a clean rewrite.

### 15.5 Project registry — replaced by knowledge_entries

**File:** `src/vault/registry.py`

`build_registry()` scans `Projects/` and `Domain/` directories on disk. **Dead.** No filesystem scanning on cloud.

In the new model, "projects" is a dimension in `knowledge_entries`. The list of known projects = `SELECT DISTINCT entity FROM knowledge_entries WHERE dimension = 'projects'`. No separate registry needed.

**Consumers that must change:**
- `pipelines/capture.py` (lines 620-630) — built registry for classify prompt. Classify is now separate.
- `pipelines/classify.py` — references `ProjectRegistry` type. Complete rewrite.
- `mcp_server/context.py` — uses registry for project→domain lookup. Reads from knowledge_entries instead.

### 15.6 Config split

**File:** `src/core/config.py` — `VaultConfig` and `MainConfig`

Currently one config for everything. Needs split:
- **Cloud config:** DB path, LLM providers, MCP settings, AgentBase endpoint, thresholds, dimension/tag definitions
- **Daemon config:** vault root path, AgentBase endpoint (where to upload), auth credentials, watch settings

```bash
# Run this to see every CONFIG usage:
grep -rn "CONFIG\." src/ --include="*.py" | grep -v "__pycache__"
```

### 15.7 Test suite impact

**Current:** ~1370 tests. Many use temp vaults with real files.

Tests that create temp vault files and call capture/classify/write_note will need adaptation:
- Cloud-side tests mock the daemon upload (no real files)
- Daemon-side tests use real temp files (as today)
- Integration tests need both sides running
- All tests referencing `WriteOutcome`, `write_note`, `move_note`, `read_note`, frontmatter — need rewrite or deletion

```bash
# Estimate test impact:
grep -rn "write_note\|read_note\|move_note\|write_text\|vault_path\|WriteOutcome\|NoteMetadata\|frontmatter" tests/ --include="*.py" -l | wc -l
```

---

## 16. AgentBase deployment model

Based on AgentBase documentation (crawled 2026-06-11).

### Deployment type: Custom Agent
- Docker image built from the project
- Deployed via `runtime.sh create` with autoscaling configuration
- Container gets auto-injected env vars: `GREENNODE_CLIENT_ID`, `GREENNODE_CLIENT_SECRET`, `GREENNODE_AGENT_IDENTITY`, `GREENNODE_ENDPOINT_URL`
- Container listens on port 8080 (runtime contract)
- Health check at `/health`

### MCP exposure: Resource Gateway
- AgentBase Resource Gateway acts as a managed proxy for MCP servers
- Gateway speaks MCP JSON-RPC
- Supports inbound auth (NONE / IAM / JWT)
- The MCP server runs inside the Custom Agent container
- Gateway endpoint is the URL user-facing Claude connects to

### LLM provider
- Configurable via existing `config.yaml` provider abstraction
- Options: AgentBase platform LLM (OpenAI-compatible), Anthropic API, any OpenAI-compatible provider

### DB persistence
- AgentBase containers are stateless (no persistent volume)
- SQLite file must be persisted externally: backup to S3-compatible object store on writes, restore on container start
- Alternative: migrate to managed PostgreSQL
- For MVP: SQLite + S3 backup. Personal vault DB is small (<100MB).

---

## 17. Summary: next agent's starting checklist

1. Read this document end-to-end (it is the single source of truth for system direction)
2. Read `CONSTRAINTS.md` (understand which constraints survive, change, or retire — see §12)
3. Inspect the files listed in §15 at the line numbers given
4. Run the `grep` commands in §15.6 and §15.7 to map CONFIG usage and test impact
5. Start design work — likely `/codebase-design-analysis` on the refactor scope
6. Build order recommendation: daemon first (simplest, unblocks everything), then capture refactor, then knowledge_entries table + new classify, then MCP adaptation, then web UI

**Do NOT:**
- Read `roadmap.md` phases 5-9 as current plans — they are stale; all unimplemented phases are scrapped in favor of this rearchitecture
- Read `onboarding.md` as current contributor guide — it describes the old local-only model
- Treat CLAUDE.md coding patterns as immutable — many patterns reference vault writes and frontmatter that no longer apply

### Progressive documentation update policy

As each piece of the rearchitecture is implemented, update the corresponding sections in these files to match the new reality:
- `CLAUDE.md` — coding conventions, test patterns, hook enforcement
- `CONSTRAINTS.md` — hard constraints (see §12 for what changes)
- `STATE.md` — current position, phase history
- `CONTEXT.md` — domain vocabulary
- `docs/onboarding.md` — contributor guide
- `docs/architecture/` — system diagrams, ADRs, design docs

**Don't batch — update when you ship.** By the time rearchitecture is complete, all docs should be current. Each file has a stale notice pointing to this document; remove the notice once that file's content is fully updated.

---

## 18. AgentBase platform references

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
- No persistent volume — containers are stateless
- SDK: `greennode-agentbase` (Python) for Identity, Memory clients
- IAM auth via service account (`client_id` / `client_secret` → bearer token)
