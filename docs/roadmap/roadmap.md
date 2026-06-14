---
created: 2026-04-26
updated: 2026-06-13
---

> **REARCHITECTURE IN PROGRESS (2026-06-12).** Old phases 5-9 scrapped — replaced by cloud-native rearchitecture phases 5-10 below. Read `docs/0_draft/cloud_native_rearchitecture.md` as the single source of truth for system direction. Completed phases (0-4) remain accurate as reference for existing code.

# PROJECT CONTEXT

## Problem Statement and Project Overview
Office workers in tech companies are struggling to keep up with the demanding knowledge and an ever-growing list of things they have to do. This get especially terrible when they become managers and take on multiple different responsibilities and business lines. They find that the simple traditional note-taking system does not work due to:
- Notes accumulate fast and hard to find, and often time written down and forgot
- Organization effort is needed to manage the notes, but there is no time for that
- There is synthesis part of note taking, which is productive, and the transcribing part, which is not productive but is essential for the synthesis part. However, the modern workflow requires so much transcribing - meeting notes, reports, etc. that makes the unproductive part is too overwhelming and leave little room for other work
- Ideas and thinking often get lost, and not revisited
This project is built to solve all those issues by building a knowledge management system with AI. The AI will help users with transcribing and organizational tasks, as well as becoming a thinking tool for users by surfacing patterns from the notes, or bringing up interesting ideas for users to revisit
## Key Rule
This project is aimed at professional office workers, like managers and executives, who is too busy to do anything else except using things out of the box. This means that we need to make:
- Aggressive simplification
- Zero organizational effort as the baseline assumption
- AI does work, human does judgment
- Defaults and invisibility layer as the main interface to the office workers, but have flexibility and tinkering layer underneath for customization done by the technical team to adapt better to the office workers' needs
## Key Design principles:
- **Progressive automation with human oversight** — high confidence = auto-file, medium = flag for review, low = stay in inbox
- **Show your work** — every AI decision should be traceable to source materials
- **Never silently overwrite human edits** — trust-breaking moment if it happens
- **Natural emptying mechanisms** — systems without them decay
- **One job per artifact** — if you can't describe a component's job in one sentence, it's too complex
- **Flexibility and Adaptability** - a knowledge system is a highly personalized product, and thus the code base need to be designed in modular, interchangeable patterns so the technical team could add more features as requested by the office worker

## Feature Roadmap
1. **Capture** — Listen for drops of web articles, email, chat sessions, notes, pdf, docs, YouTube video transcripts, and process them by summarize, and input metadata
2. **Classify** — Classify the notes based on their content, and move them to correct folder
3. **Semantic Search** — Find notes by meaning, not just keywords ("what do I know about stakeholder resistance" finds notes about "managing pushback in meetings")
4. **Three-Tier Retrieval** — Get quick summaries (hot), search snippets (warm), or full content (cold) — always starting cheap and going deep only when needed
5.  **MCP Server** — Let Claude (desktop app or web) directly search, classify, promote, and synthesize your notes in natural conversation
6.  **Note Promotion** — Extract structured knowledge (research notes, workflow templates, lessons) from raw captures, turning ore into refined metal
7. **Documentation** - Automatically keep track and write synthesis report (based on notes relating to projects) of current progress of active projects.
8. **Self-Learning** — Track human corrections to AI classifications and use them to improve future accuracy
9. **Weekly Synthesis & Daily Briefing** — Automatically connect dots across the week's notes to surface recurring themes, contradictions, and action items

---

# Build Order — AI-kms

## Deadline: 17 June 2026

## Collaboration Model

This roadmap supports multiple contributors working in parallel. Each task is labeled with its dependency chain and weight. Contributors pick tasks based on availability.

**How to work a task:**
1. Run `/grill` to pin down the design (what exactly does this feature do? edge cases? scope?)
2. Run `/codebase-design-analysis` to explore implementation options against the existing codebase
3. Run `/writing-detailed-specs` to turn the chosen option into a buildable spec
4. Run `/research` to validate the spec against codebase constraints and patterns — this is the quality gate
5. Run `/plan-from-specs` to produce the implementation plan
6. Run `/tdd-implement` to build it phase by phase
7. Run behavior tests to verify end-to-end (acceptance criteria listed per task)

**Review model:** No code review. No test review. Final verification is **behavior testing** — run the app, do the thing described in acceptance criteria, confirm it works.

**Work in worktrees.** Each contributor works in their own git worktree to avoid conflicts.

---

## Dependency Graph

```
COMPLETED (existing)                    REARCHITECTURE PHASES
────────────────────                    ─────────────────────

Phase 0 (Foundations)  ─┐
Phase 1 (Capture)       │
Phase 1.5 (Pay Debt)    ├─ DONE ──→ Phase 5 (Infrastructure) ──→ Phase 6 (Daemon) ✅
Phase Pre-2 (DB Prep)   │                    │
Vault-Restructure       │                    ▼
Phase 2 (Classify)      │           Phase 7 (Capture Refactor) ✅
Phase 3 (Search)        │                    │
Phase 4 (MCP Server)  ──┘                    ▼
                                    Phase 8 (Classify Redesign)
                                             │
                                             ▼
                                    Phase 9 (MCP Adaptation)
                                             │
                                             ▼
                                    Phase 10 (Web UI + Self-Learning)
```

**Key insight:** Phase 6 (Daemon) and Phase 7 (Capture Refactor) can run in parallel — both depend on Phase 5 only. Phase 6 Slice A1 + A2 COMPLETE; Slice B plan written. Phase 7A (Text Capture) + 7B (Visual/Binary Capture) both COMPLETE. **Phase 8 Slice A (Classify Infrastructure, no LLM) COMPLETE (2026-06-14).**

---

## Completed Phases (reference only)

### Phase 0 — Foundations ✅
Core primitives, config, LLM provider, vault layer, storage layer, audit log. 956 tests. See STATE.md for full checklist.

### Phase 1 — Capture ✅
Drop file into `inbox/` → AI writes summary + metadata. Handlers: markdown, PDF, DOCX, XLSX. Watcher, indexer, reconcile (7 stages). Sibling `.md` files for binaries under `.summaries/`. URL enrichment. Tag validation.

### Phase 1.5 — Pay Debt ✅
FILE_LOST guard, location tags, stale-tag reconcile, folder capture, handlers extension, idempotent capture, stale-batch-ref reconcile.

### Phase Pre-2 — DB Schema Prep ✅
`project`, `status`, `key_topics` columns added to documents table. Domain scalar deprecated (lazy migration via `_DEPRECATED_KEYS`).

### Vault-Restructure — Editable/No-Edit Split ✅
ADR-0006. `no_edit_extensions` config, `resolve_placement()`, binary content-change detection, settle window, move_guard, AI-output folder exclusion, reconcile Stage 7.

### Phase 2: Classify & Route ✅
`classify()` pure function, inline classify in capture pipeline, project registry (`meta.yaml`), confidence-gated routing (AUTO/SUGGEST/CLUELESS), candidate frontmatter fields (`suggested_project`, `suggested_primary_domain`, `classify_confidence`, `classify_reasoning`), `move_guard` integration. 1080 tests.

### Phase 3 Session A — Index Layer ✅
Migration 007 (`embeddings_vec` vec0 + `notes_fts` FTS5), `retrieval/` package (Meaning Indexer `index_embedding` + Word Indexer `index_keywords`), best-effort capture pipeline wiring (4 call sites), search-table cleanup in `documents.py` (`delete_by_path`, `rename`, `replace_path`). 1147 tests.

### Phase 3 Session B — Query Path ✅
Hybrid search end-to-end: descriptive title at capture (`title` typed field on `NoteMetadata`), candidate filter (`filter_paths()` in `documents.py`), hybrid ranker (BM25 word search + KNN meaning search + RRF fusion in `retrieval/ranker.py`), cross-encoder re-ranker (SearchResult cards in `retrieval/reranker.py`), search coordinator (`search()` in `retrieval/search.py`), CLI search command (`--project`, `--since`, `--reindex`, `--max`), TD-051 classify cross-type validation (`project_names`/`domain_names` params on `classify()`). ~180 new tests, ~3400 lines. M1 milestone (Capture + Classify + Search end-to-end) achieved.


---

## Stable Interfaces (Phase 0+1+2+3+4 — all independent tasks build against these)

> **⚠️ STABLE FOR EXISTING CODE ONLY — several entries are being RETIRED by the rearchitecture (flagged 2026-06-12).** Accurate for Phases 0–4 code as-is. But for **rearchitecture phases (5–10), do NOT build new code against these dead-or-dying modules:** `vault/reader.py`, `vault/writer.py` (incl. `write_note`/`move_note`/`WriteOutcome`), `vault/paths.py` placement helpers (`resolve_placement`/`project_attachment`/`domain_attachment`), `mcp_server/_move.py`. They are retired per ADR-0012 (deletion rides with each module's last-consumer refactor). `storage/documents.upsert()` signature changes in Phase 7. See `docs/0_draft/cloud_native_rearchitecture.md` §11.

These have been stable across ~1258 tests and multiple phases. Independent tasks import from these modules only:

| Module | Key functions | What it does |
|--------|--------------|--------------|
| `vault/reader.py` | `read_note(path) → Result[Note]` | Read note + frontmatter from vault |
| `vault/writer.py` | `write_note(path, content, metadata, actor)`, `move_note(src, dst)` | Write/move notes (respects `updated_by_human`) |
| `vault/paths.py` | `resolve_placement()`, `project_attachment()`, `domain_attachment()` | Vault path helpers |
| `storage/documents.py` | `upsert()`, `get_by_path()`, `all_paths()`, `delete_by_path()`, `rename()`, `upsert_from_upload()` | Document index CRUD; `upsert_from_upload` (P5 Slice 2) accepts upload fields directly |
| `storage/audit_log.py` | `append(AuditEntry) → Result[int]`, `query()` | Audit log read/write |
| `core/audit.py` | `write(decision, source_ids, pipeline, stage, ...)` | High-level audit writer |
| `core/tags.py` | `validate_tags(tags, taxonomy) → Result` | Tag taxonomy enforcement |
| `core/pipeline.py` | `run_pipeline(stages, input) → Result` | Pipeline executor |
| `core/result.py` | `Success(value)`, `Failure(error, recoverable, context)` | Result type |
| `llm/provider.py` | `get_provider(task, config) → LLMProvider` | LLM call factory |
| `llm/prompt_loader.py` | `PROMPTS["name"].render(**vars)` | Prompt loading from YAML |
| `core/config.py` | `CONFIG` singleton, `VaultConfig`, `MainConfig` | Validated config |
| `pipelines/classify.py` | `classify(subject, valid_destinations, config) → Result[ClassifyResult]`, `build_subject(title, summary, tags) → str`, `ClassifyResult` | AI classify engine (pure function, no side effects) |
| `core/confidence.py` | `route(decision, thresholds) → RoutingOutcome`, `AIDecision(action, confidence, reasoning, source_ids)` | Confidence-gated routing |
| `vault/move_guard.py` | `get_active() → MoveGuard | None` | Pipeline move registration (prevents watcher re-home) |
| `retrieval/embeddings.py` | `index_embedding(vault_path, title, note_type, tags, summary) → Result[None]` | Semantic embedding storage (best-effort) |
| `retrieval/keyword.py` | `index_keywords(vault_path, title, summary, body) → Result[None]` | FTS5 keyword indexing (best-effort) |
| `storage/db.py` | `get_connection(db_path, readonly=False) → Generator[Connection]` | DB connection factory (loads sqlite-vec, WAL=100checkpoint, FK) |
| `storage/documents.py` | `filter_paths(project, since, until, location, db_path) → Result[list[str] \| None]` | Candidate filter (project + date range + folder location → vault_paths; `location="inbox"` scopes to that folder) |
| `retrieval/ranker.py` | `rank(query, candidate_paths, max_candidates, db_path) → Result[list[RankedResult]]` | Hybrid ranker (BM25 word + KNN meaning + RRF fusion) |
| `retrieval/reranker.py` | `rerank(query, candidates, db_path) → Result[list[SearchResult]]` | Cross-encoder re-ranker (cheap result cards with metadata) |
| `retrieval/search.py` | `search(query, project, date_range, max_results, location, db_path) → Result[list[SearchResult]]` | Search coordinator (filter → rank → rerank → cards). `location` scopes to a vault folder. Stable contract for CLI + MCP. |
| `pipelines/classify.py` | `classify(..., project_names, domain_names, ...)` — two optional frozenset params | TD-051: validates project against project_names only, domain against domain_names only |
| `mcp_server/context.py` | `ContextInjectionEngine()` — `build_search_response(query, project, since, until, location, include_context)`, `build_vault_info_response()`, `build_read_response(paths, include_context)` | Per-conversation engine: frequency-threshold gating, project→domain registry lookup, content-hash dedup, context-block + card assembly. Instantiated once per conversation via FastMCP lifespan. |
| `mcp_server/_resolve.py` | `inspect(path: Path) → Result[str]` | Binary text extractor — resolves sibling `.md` → binary via `attachment_path` frontmatter, then runs handler registry extractor. No AI, no audit. |
| `mcp_server/_move.py` | `move(src: Path, dst_name: str, dst_kind: str, db_path=None) → Result[str]` | 7-step proven move recipe: resolve dest → read → capture old path → register guard → `move_note` → `write_note(dst, new_meta)` → `replace_path(old_vp, outcome)`. Blocks human-locked notes (C-02). |
| `mcp_server/api.py` (P5 Slice 2) | `require_key(request)`, `upload_handler`, `event_handler`, `health_handler` | REST endpoints for daemon sync: `/api/upload` (save-or-update), `/api/event` (move/delete), `/health` (open health check). Bearer-key gate scoped to `/api/*`. |
| `mcp_server/cloud_entry.py` (P5 Slice 2) | `build_app(db_path=None) → Starlette` | Container-mode entry point: calls `init_db()` (C2-4), builds FastMCP web app, mounts REST routes + health check, runs `uvicorn` on port 8080 under `__main__`. Phase 8 Slice A adds composed outer lifespan (worker + catch-up scan). |
| `storage/documents.py` (P8 Slice A) | `find_unclassified(*, db_path=None) → Result[list[int]]`, `stamp_classified(doc_id, *, db_path=None) → Result[int]` | Classify work discovery: returns doc ids where `classify_content_hash IS NULL OR classify_content_hash != content_hash`. Stamp copies `content_hash` → `classify_content_hash`. |
| `storage/knowledge_entries.py` (P8 Slice A) | `query_ranked_by_dimension(dimension, *, limit, db_path=None) → Result[list[KnowledgeEntry]]` | Ranked, capped, non-retired facts per dimension ordered by `trust_score DESC, confidence DESC, updated_at DESC`. |
| `pipelines/classify.py` (P8 Slice A) | `content_reader(doc_id, *, config, db_path=None) → Result[str]`, `context_loader(*, config, db_path=None) → Result[dict[str, list[KnowledgeEntry]]]`, `consumer(queue, db_path, config)`, `catch_up_scan(queue, db_path)` | Classify preparation helpers: pick text vs summary by token budget; load ranked facts per dimension from config. Consumer and catch-up scan drive the in-memory work queue. |
| `core/tags.py` (P8 Slice A) | `load_dimensions(path) → Result[dict]`, `validate_dimensions(rulebook) → Result[dict]` | Loads nested `dimensions.yaml` `{dim: {tags, guidance}}` with loud reject on malformed config. |
| `core/config.py` (P8 Slice A) | `ClassifyConfig` — `max_content_tokens` (10000), `max_entries_per_dimension` (50) | Classify tunables. |


## Phase 3 — Search ✅ (COMPLETE)

**Status: COMPLETE** — 2026-06-11. Built in two sessions:

**Session A — Index Layer:** Migration 007 (`embeddings_vec` vec0 + `notes_fts` FTS5), Meaning Indexer (`index_embedding`), Word Indexer (`index_keywords`), best-effort capture pipeline wiring, search-table cleanup in `documents.py`. 1147 tests.

**Session B — Query Path:** Descriptive title at capture (Component 0), Candidate Filter (`filter_paths`), Hybrid Ranker (BM25 + KNN + RRF fusion), Cross-encoder Re-ranker (SearchResult cards), Search Coordinator (`search()`), CLI search command (`--project`, `--since`, `--reindex`, `--max`), TD-051 classify cross-type validation. ~180 new tests, ~3400 LOC.

**Architecture:** ADR-0009 (RRF + rerank, no tier dispatcher). See `docs/architecture/system_adr/0009-phase3-search-rrf-rerank-not-tier-dispatcher.md`.

**What was built (supersedes the original tier-dispatcher design below):**

**What was built:** See Session A+B summary above. Original tier-dispatcher design superseded by ADR-0009. See `docs/4_plans/P3_session_b_query_path.md` for complete build plan and acceptance criteria.

---

### Phase 4 — MCP Server MVP ✅ (COMPLETE 2026-06-12)

**Status: COMPLETE** — 2026-06-12. All 7 phases shipped in 6 commits. 1258 tests.

**What was built:** FastMCP stdio server (`server.py`) that mirrors the CLI bootstrap (load_dotenv → setup_logging → CONFIG → MoveGuard); per-conversation `ContextInjectionEngine` via FastMCP lifespan; `copy_context().run()` per-call isolation; Context Injection Engine (`context.py`) with frequency-threshold + project→domain registry lookup + content-hash dedup + context-block assembly; Binary Resolver Helper (`_resolve.py`) that re-extracts binary text via existing handler registry (no AI); Note Mover Helper (`_move.py`) implementing the proven 7-step move recipe; Tool Shim Layer (`tools.py`) — 5 logic-free shims: `kms_vault_info`, `kms_search`, `kms_read`, `kms_inspect`, `kms_move`; AI usage instructions (`AI_INSTRUCTIONS.md` + tool `description=` strings, closes TD-055). Phase 1 prerequisites: `wal_autocheckpoint=100` in `_connect()` (closes TD-007), `mcp.context_injection` config block, `mcp>=1.27,<2` dep, `location` folder filter on `filter_paths()` + `search()`.

**Architecture:** ADR-0010 (context injection in tool responses) + ADR-0011 (write-path via `kms_move`, not `kms_capture`/`kms_classify`). Option A — per-conversation lifespan = per-process under stdio. Context source = live ProjectRegistry (not `meta.yaml`). Build order: bottom-up (engine+helpers first, shims last — satisfies C-14+C-15).

**New package:** `src/mcp_server/` — `server.py`, `context.py`, `_resolve.py`, `_move.py`, `tools.py`, `AI_INSTRUCTIONS.md`. Entry: `python -m mcp_server.server`.

**Plan/Spec/Research/Design:** `docs/*/P4_mcp_context_injection.md`. ADRs: `0010`, `0011`.

---

## Phase 5 — Infrastructure Foundation ✅ (COMPLETE)

**`DEPENDS ON: Phase 0-4 · WEIGHT: medium · TYPE: infrastructure (no behavior change)`**

> **Rearchitecture phase.** Read `docs/0_draft/cloud_native_rearchitecture.md` for full architectural context. This phase laid the foundation — DB schema, repo structure, container scaffolding, API contract. No user-visible behavior changes.

> **🔪 SPLIT INTO TWO SLICES (decided 2026-06-12, build-pipeline grill). SLICE 1 COMPLETE (2026-06-13, merged to cloud-native). SLICE 2 COMPLETE (2026-06-13, merged to cloud-native).** Phase 5 ran as two independent, parallelizable slices:
>
> **SLICE 1 — Data/Config Foundation** *(pure local code, NO AgentBase, additive-only — zero existing-test breakage):*
> - DB MIGRATIONS (new `knowledge_entries` table + 3 **nullable** `documents` columns)
> - `storage/knowledge_entries.py` CRUD
> - DIMENSION/TAG CONFIG (`config/dimensions.yaml` + `validate_dimension_tag()`)
> - **Does NOT** touch `documents.upsert()`, **does NOT** split config, **does NOT** retire modules (all deferred — see per-component warnings + ADR-0012).
>
> **SLICE 2 — Deployment Foundation** *(COMPLETE 2026-06-13)*:
> - CONTAINER SCAFFOLDING (Dockerfile, `/health`, port 8080, **Litestream + VNG Object Storage** for SQLite persistence per agentbase_research §11.2, `--max-replicas 1` per §11.4)
> - REST API SKELETON (`/api/upload`, `/api/event`)
>
> **Parallelism:** the two slices touch disjoint files and can run in parallel worktrees. **One integration seam:** Slice 2's `/api/upload` writes `full_body`, which needs Slice 1's migration landed first (Slice 1's first build step). Everything else is independent. `CONTAINER SCAFFOLDING`'s old "depends on CONFIG SPLIT" dependency is **stale** (config split deferred) — it now depends on nothing in Slice 1.

### Goal

Everything in the rearchitecture depends on three things existing first: (1) the new DB schema (`knowledge_entries` table, `full_body` column on documents), (2) the repo split into cloud code vs daemon code, and (3) a deployable container on AgentBase with REST endpoints for the daemon to upload to.

Phase 5 built this foundation. After this phase: the DB can store everything the new pipeline needs, the repo has clear cloud/daemon boundaries, the container runs on AgentBase with a health endpoint, and REST endpoints exist (stubbed) for daemon uploads. No pipelines change yet — this is pure infrastructure.

### Repo structure after this phase

```
AI-kms/
├── src/              ← cloud code (Docker image)
│   ├── pipelines/    ← capture, classify (refactored in Phase 7-8)
│   ├── storage/      ← documents.py, audit_log.py, NEW knowledge_entries.py
│   ├── retrieval/    ← search, ranker, reranker, embeddings, keyword
│   ├── mcp_server/   ← server.py, tools.py, context.py
│   ├── handlers/     ← SHARED: text extraction, bundled in both cloud and daemon
│   ├── core/         ← result, audit, tags, confidence, config
│   ├── llm/          ← provider, prompt_loader
│   ├── config/       ← config.yaml (cloud), tags.yaml, thresholds.yaml
│   └── api/          ← NEW: REST endpoints for daemon uploads
├── daemon/           ← NEW: local daemon package
│   ├── __main__.py   ← entry point
│   └── (built in Phase 6)
├── Dockerfile        ← NEW: builds cloud image from src/
├── installer/        ← NEW: PyInstaller spec (built in Phase 6)
└── tests/

```

### Components

---

**DB MIGRATIONS** *(build first — everything else depends on schema)*

New migration files in `storage/migrations/`. Expand the DB to support the full cloud-native model.

- **New table: `knowledge_entries`** — see rearchitecture doc §7 for schema. Columns: `id`, `dimension`, `entity`, `tag`, `fact`, `status`, `confidence`, `sources` (JSON array), `reasoning`, `created_at`, `updated_at`.
- **New columns on `documents`:** `full_body` (TEXT — complete extracted text), `original_filename` (TEXT), `file_size_bytes` (INTEGER).
- **New CRUD module: `storage/knowledge_entries.py`** — `upsert()`, `query_by_dimension()`, `query_by_entity()`, `retire()`, `get_confident_and_pending()`. All return `Result` types (C-12).
- **`documents.py` changes:** `upsert()` must accept structured summary + full_body directly (not `WriteOutcome`). `replace_path()` simplified to just path update (daemon reports moves).

> **⚠️ ADDITIVE-ONLY IN SLICE 1 (added 2026-06-12, build-pipeline grill).** Slice 1 ships the schema + `knowledge_entries` CRUD + config/dimensions ONLY. It does **NOT** touch `documents.upsert()` / `replace_path()` — those have one live caller (`capture.py`) not rewritten until Phase 7, and no new caller exists yet (C-15: don't build an interface before its consumer). **DEFERRED to Phase 7:** (a) redesign `documents.upsert()` to take structured summary + `full_body` directly and drop the `WriteOutcome` path; (b) populate the three new columns on capture. **NULL-column resolution — DEFERRED, must be solved:** the three new columns ship **nullable** and stay NULL on every existing row (nothing populates them in Slice 1). No backfill needed (nothing shipped to users — clean slate, rearch doc §32/§61), but downstream readers MUST handle NULL: Phase 9 `_resolve.py` tier-2 reads `full_body` — when NULL (row not yet captured by the new Phase 7 path), it must degrade gracefully to tier-3 (vault path) rather than return empty. Flag this when building Phase 7 and Phase 9.

- **Rules:**
  - Migration-only schema changes (C-05)
  - FK pragma enforced (C-04)
  - Every new CRUD function returns `Success` or `Failure` (C-12)
  - **Slice 1 is additive — new columns nullable, no existing path altered, repo stays green.**
- **Acceptance:** `uv run pytest` passes with new schema (no existing tests rewritten). `knowledge_entries` table exists. `documents.full_body`/`original_filename`/`file_size_bytes` columns exist (nullable). `knowledge_entries` CRUD operations work. `documents.upsert()` unchanged — still accepts `WriteOutcome`.

---

**CONFIG SPLIT** *(depends on DB MIGRATIONS)*

> **⚠️ DEFERRED OUT OF SLICE 1 (added 2026-06-12, build-pipeline grill).** This component does NOT run in Slice 1. Vault-root config has **49 live usages across 7 files** (`core/config.py`, `pipelines/capture.py`, `pipelines/reconcile.py`, `cli/main.py`, `mcp_server/_resolve.py`, `mcp_server/server.py`, `mcp_server/context.py`) — all owned by Phases 6/7/9. The acceptance "cloud config loads **without** vault root" breaks all 7 in Slice 1 for zero benefit, and the daemon (consumer of daemon-config) doesn't exist until Phase 6, so a `DaemonConfig` model now would be unused scaffolding (C-15).
> **RESOLUTION (decided):** Defer the whole split. **Vault-root removal from cloud config rides with Phases 6/7/9** as each consumer sheds its vault dependency (cloud config is vault-root-free only once the LAST vault consumer is gone). **Daemon config is built in Phase 6** alongside the daemon. Slice 1 leaves `core/config.py` / `VaultConfig` fully intact and adds ONLY the dimension/tag config below.

Split `core/config.py` into cloud config and daemon config. Both validated by Pydantic.

- **Cloud config (`config/config.yaml`):** DB path, LLM providers, MCP settings, thresholds, dimension/tag definitions, AgentBase endpoint. No vault root.
- **Daemon config (`daemon/config.yaml`):** vault root path, AgentBase upload endpoint, auth credentials (client_id/client_secret), watch settings (debounce, ignored patterns).
- **Shared:** Result types, handler interfaces, tag taxonomy.
- **Rules:**
  - Thresholds in config, never in code (C-06)
  - `Field` for user-configurable, `@property` for computed
- **Acceptance:** Cloud code starts without vault root. Daemon config validates vault root exists on disk.

---

**DIMENSION/TAG CONFIG** *(Slice 1 deliverable — NO LONGER depends on CONFIG SPLIT, which is deferred)*

> **✅ SHIPS IN SLICE 1 (added 2026-06-12).** This is additive (new `config/dimensions.yaml` + new `validate_dimension_tag()` in `core/tags.py`), breaks nothing, and is the foundation Phase 8 classify needs. It does NOT require the config split — load `dimensions.yaml` independently. Starter taxonomy = the doc's example (`people`, `projects`, `domains` with their tag sets, each including mandatory `other`); treat the taxonomy as **provisional**, refinable in Phase 8.

Define allowed dimensions and their tag sets in config. Extend `core/tags.py` to validate dimension/tag pairs.

- **Config file:** `config/dimensions.yaml` — dimensions with their allowed tag sets. Every tag set must have mandatory `other` catch-all.
- **Validation:** `validate_dimension_tag(dimension, tag, config) → Result` — rejects unknown dimensions or tags.
- **Rules:**
  - Dimensions and tags are config-enforced. AI cannot invent them.
  - Adding a dimension = config + prompt change, zero schema change.
- **Acceptance:** `validate_dimension_tag("people", "role", config)` → `Success`. `validate_dimension_tag("people", "invented_tag", config)` → `Failure`.

---

**CONTAINER SCAFFOLDING** *(SLICE 2 · ~~depends on CONFIG SPLIT~~ — that dep is STALE, config split is deferred)*

> **⚠️ DEPENDENCY CORRECTED + AGENTBASE DECISIONS FOLDED IN (2026-06-12).** Config split was deferred (ADR-0012), so this no longer depends on it — it depends on **nothing in Slice 1** and runs in parallel. Caveat: the entry point's goal "cloud config starts **without** vault root" is blocked by the deferred config split (the current `mcp_server/server.py` still imports `move_guard`/vault), so the container boots the **existing vault-requiring config** for now — fine for this slice's own acceptance (`docker build` + `/health` 200 + tool-list). The no-vault-root cleanup rides with Phases 6/7/9.
> **AgentBase decisions now settled** (`docs/0_draft/agentbase_research.md`): **(a)** SQLite persistence = **Litestream + VNG Object Storage** (§11.2) — Dockerfile needs the Litestream binary + a startup script that restores the DB from object storage → starts replication → starts the app; shutdown flushes Litestream. Supersedes the vague "S3 or mounted volume" rule below. **(b)** `--max-replicas 1` for MVP (§11.4). **(c)** Build `--platform linux/amd64` (§5.2). **(d)** Container Registry push to `vcr.vngcloud.vn/{repo}/{name}:{tag}` (§5.1). **(e)** LLM can point at MaaS OpenAI-compatible endpoint via config only — no code change (§8).

Dockerfile, health endpoint, container entry point. Make the cloud side deployable on AgentBase.

- **Dockerfile:** builds from `src/`, installs dependencies, runs MCP server on port 8080.
- **Health endpoint:** `GET /health` returns 200. AgentBase runtime contract.
- **Entry point:** replaces CLI-like bootstrap. Loads cloud config, initializes DB, starts MCP server.
- **Auto-injected env vars:** `GREENNODE_CLIENT_ID`, `GREENNODE_CLIENT_SECRET`, `GREENNODE_AGENT_IDENTITY`, `GREENNODE_ENDPOINT_URL`.
- **Rules:**
  - Container is stateless — DB must be backed up externally (S3 or Litestream). For MVP: SQLite file in a mounted volume or S3 restore on start.
  - **DB backend flexibility (decided P5 Slice 2 grill, 2026-06-13):** SQLite + Litestream is for demo/MVP. PostgreSQL support planned for later. Storage layer (`storage/db.py`, `get_connection()`) should remain the single DB access point so swapping backends is a contained change — no raw SQL scattered across modules. Note for future: when adding PostgreSQL, the connection factory and migration runner are the two touch points.
  - **DB path:** `/data/kb.db` inside container. Startup script creates `/data/` if missing. Litestream watches this path.
- **Acceptance:** `docker build` succeeds. Container starts, `/health` returns 200. MCP server responds to tool list request.

---

**REST API SKELETON** *(SLICE 2 · depends on CONTAINER SCAFFOLDING + Slice 1 migration)*

> **⚠️ DEPENDENCY + CROSS-DOC CONFLICT FLAGGED (2026-06-12).** `/api/upload`'s stub "store with `full_body`" needs **Slice 1's migration landed first** (the `full_body` column). That is the single integration seam between the two slices — sequence it, nothing else.
> **CONFLICT to resolve before Phase 6:** `agentbase_research.md` §11.1 designs daemon command-push endpoints (`GET /pending-commands` + `POST /command-ack`) so the cloud can tell the daemon to **move files**. But `cloud_native_rearchitecture.md` Session 2 (the declared single source of truth) **dropped file-moving and ALL cloud→daemon commands** (§4, §6). Under the current architecture those polling endpoints are **moot** — do NOT build them. If a future need for cloud→daemon push appears, reopen as a design question. (Rearchitecture doc wins per its own precedence rule.)

Endpoints for daemon → cloud communication. Stubbed — real pipeline wiring happens in Phase 7.

- **`POST /api/upload`** — accepts extracted text + file metadata (vault_path, content_hash, filename, size). Returns document ID. Stub: stores to documents table with `full_body`.
- **`POST /api/event`** — accepts file events (moved, renamed, deleted) with old/new paths. Stub: updates `vault_path` in documents.
- **Auth:** Simple shared API key per runtime (NOT IAM — IAM is for platform APIs). Container reads `KMS_DAEMON_API_KEY` from env var; daemon sends `Authorization: Bearer <api-key>` on every request. One key per runtime instance — each tester gets their own. Decided during P5 Slice 2 grill (2026-06-13).
- **Rules:**
  - API contract is the interface between daemon and cloud. Changing it requires updating both sides.
  - Every endpoint returns JSON with `status` and `error` fields.
- **Acceptance:** `curl POST /api/upload` with test payload → document appears in DB. `curl POST /api/event` with move event → `vault_path` updated.

---

**RETIRE DEAD MODULES** *(can run parallel with other components)*

> **⚠️ SEQUENCING WARNING (added 2026-06-12, build-pipeline Slice 1 grill).** This component CANNOT run in Phase 5 as written. Every "dead" module still has a **live consumer owned by a later phase**, so deleting it now either breaks the repo or drags later-phase work forward:
> - `vault/writer.py`, `frontmatter.py`, `reader.py`, `indexer.py`, `move_guard.py` → imported by `pipelines/capture.py` (**Phase 7** rewrites it), `vault/watcher.py` (**Phase 6** moves it to daemon), `pipelines/reconcile.py` (**unassigned** — see below), `cli/main.py`, `handlers/markdown_handler.py`, `vault/registry.py`.
> - `mcp_server/_move.py` + `kms_move` shim → imported by `mcp_server/tools.py`, `server.py` (**Phase 9** adapts MCP).
> - `WriteOutcome` (`vault/writer.py`) → imported by `storage/documents.py`, which Phase 5 itself redesigns — handle **additively** (keep old `upsert(WriteOutcome)` path alive, add new path) so the repo stays green until Phase 7 swaps `capture.py` over.
> - `vault/paths.py` placement helpers (`resolve_placement`, `project_attachment`, `domain_attachment`, `_is_in_managed_attachment`) → also live-consumed by capture/watcher/reconcile; gutting breaks them too.
>
> **RESOLUTION (decided):** Each module's deletion **rides with the phase that rewrites its last consumer**, not Phase 5. Mapping: `writer`/`frontmatter`/`indexer` die across **Phase 6** (watcher→daemon) + **Phase 7** (capture rewrite); `reader` + `move_guard` die once their last consumer across Phases 6/7/9 is gone; `_move.py` + `kms_move` die in **Phase 9**. `paths.py` gutting waits until capture/watcher/reconcile no longer call placement helpers. **`pipelines/reconcile.py` is UNASSIGNED** — rearchitecture doc §11 says the 7-stage reconcile is replaced by daemon scan and "DB-only reconcile may survive in simplified form," but no phase owns this transition. Resolve before Phase 6. Phase 5 Slice 1 ships only the additive DB/config foundation; it deletes nothing.

Remove or gut modules that the rearchitecture kills. Clean up imports.

- **Remove entirely:** `vault/writer.py`, `vault/move_guard.py`, `vault/frontmatter.py`, `vault/reader.py`, `vault/indexer.py`, `mcp_server/_move.py`.
- **Gut:** `vault/paths.py` — keep only vault-relative path computation. Remove `resolve_placement()`, `project_attachment()`, `domain_attachment()`, `_is_in_managed_attachment()`.
- **Remove from tools.py:** `kms_move` tool shim.
- **Update imports:** grep all files that import from retired modules, fix or remove.
- **Rules:**
  - Tests that depend on retired modules: delete or rewrite. Don't keep broken tests.
  - Hook enforcement in `.claude/settings.json`: audit and remove hooks that reference dead modules.
- **Acceptance:** `uv run ruff check .` passes. No imports reference deleted modules. Remaining tests pass.

---

### Acceptance criteria (behavior test)

> **Regrouped 2026-06-12 to match the two-slice split + deferrals (ADR-0012).** The original flat list mixed Slice 1, Slice 2, and deferred-to-later-phase items.

**Slice 1 — Data/Config Foundation (additive, no existing-test breakage):**
- [x] `knowledge_entries` table exists with correct schema
- [x] `documents.full_body`, `original_filename`, `file_size_bytes` columns exist (**nullable**)
- [x] `storage/knowledge_entries.py` CRUD operations work (upsert, query_by_dimension, query_by_entity, retire, get_confident_and_pending)
- [x] `config/dimensions.yaml` defines dimensions with tag sets (each has `other`)
- [x] `validate_dimension_tag()` accepts valid pairs, rejects invalid
- [x] `documents.upsert()` **unchanged** — still accepts `WriteOutcome` (redesign deferred to Phase 7)
- [x] `uv run pytest` passes with **no existing tests rewritten or deleted** (additive-only)

**Slice 2 — Deployment Foundation (AgentBase):**
- [ ] `docker build --platform linux/amd64` succeeds, container starts on port 8080
- [ ] `/health` returns 200; MCP server responds to tool-list request
- [ ] Litestream restores DB from VNG Object Storage on start, replicates on write (§11.2)
- [ ] `POST /api/upload` stores document with `full_body` in DB *(needs Slice 1 migration landed — the integration seam)*
- [ ] `POST /api/event` updates vault_path on move

**Slice 1 implementation notes (2026-06-13 — merged to cloud-native, 1275 tests, +18 new):**

> What Slice 2 and downstream phases should know. Plan: `docs/4_plans/P5_slice1_data_foundation.md`. Source-of-truth for this slice is the **code**, not this roadmap summary. Read the plan before touching any of these modules.

**What shipped exactly as the roadmap said:**
- Migration 008: `knowledge_entries` (11 columns) + 3 nullable `documents` columns (`full_body`, `original_filename`, `file_size_bytes`). Single-file migration — the runner auto-applies it.
- `storage/knowledge_entries.py`: 5 CRUD ops (`upsert`, `query_by_dimension`, `query_by_entity`, `retire`, `get_confident_and_pending`). All return `Result`. Sources JSON round-trip. retire never deletes.
- `config/dimensions.yaml`: provisional starter taxonomy (people/projects/domains, each with `other`).
- `validate_dimension_tag()` + `confidence_to_status()` in `core/tags.py`. Pure functions, no float literal in if/elif.
- `documents.upsert()` untouched — still takes `WriteOutcome`.
- Zero existing tests rewritten (except the expected `test_migration_007.py` version pin 7→8).

**Where the implementation diverged from the roadmap (all intentional, resolved in research/plan):**

1. **`DocumentRow` +3 fields shipped in Slice 1, not Slice 2.** Phase 1 Step 3 tests exercise `_row_from_sqlite` with the new columns, so the guarded reads (`"key" in row.keys() else None`) and trailing `= None` defaults on `DocumentRow` were needed immediately. Purely additive — no existing path altered.

2. **`confidence_to_status()` takes an explicit `ConfidenceBand`, not config.** Callers pass the band object (testable without `CONFIG` at module scope). The mapping is `AUTO → "confident"`, `SUGGEST + CLUELESS → "pending"`. This is the one place the slice translates the existing 3-value gate into a 2-value status. If the gates ever diverge, a dedicated config block is a one-line addition.

3. **`validate_dimension_tag()` takes a pre-loaded rulebook `dict`, not a config path.** The standalone `load_dimensions(path)` loader is exposed so the future Phase 8 extractor loads once and passes the dict. Mirror of the existing `load_taxonomy`/`validate_tags` split.

4. **`KnowledgeEntry` dataclass is mutable** (not `frozen=True` like `DocumentRow`). Required for the `upsert` UPDATE path (mutate `entry.id` after insert, then re-upsert). If this object ever gets passed between pipeline stages in Phase 8, freeze it and use `dataclasses.replace()`.

5. **`upsert` uniqueness is id-only.** No natural-key index on (dimension, entity, tag, fact). Inserting the same logical fact twice creates two rows. Natural-key dedup is deferred to Phase 8 (DQ-2 in the plan). Phase 8's entry writer must handle duplicate detection itself or add a unique index.

6. **`upsert` UPDATE path checks `cursor.rowcount`** — returns `Failure` for nonexistent IDs (unlike the original audit_log `append` pattern which returns success unconditionally). Matches `retire`'s contract. Phase 8 callers should check `Result.is_success()` before assuming the update worked.

**What's deferred (Phase 7/8/9 must handle):**
- `full_body` / `original_filename` / `file_size_bytes` are **NULL** on all rows. Phase 7 populates them. Phase 9 `_resolve.py` tier-2 must gracefully degrade to tier-3 when `full_body` is NULL.
- `documents.upsert()` redesign → Phase 7 (drops `WriteOutcome` path, accepts structured summary directly).
- Config split (vault root removal) → Phases 6/7/9 as each consumer sheds its vault dependency.
- No modules retired in Slice 1 → `writer`, `frontmatter`, `reader`, `indexer`, `move_guard`, `_move.py` all still alive. Each dies with its last consumer per ADR-0012.
- Dimension/tag taxonomy is **provisional** — Phase 8 refines the tag sets.
- No `knowledge_entries` indexes on `dimension`, `entity`, or `status`. Queries will full-scan. Acceptable for MVP; add indexes in Phase 8 if performance degrades.

**DEFERRED out of Phase 5 (do NOT attempt here — see warnings + ADR-0012):**
- [ ] ~~Cloud config loads without vault root~~ → rides with Phases 6/7/9 (config split deferred)
- [ ] ~~Daemon config validates vault root path~~ → built in Phase 6 (daemon doesn't exist yet)
- [ ] ~~No imports reference retired modules~~ → each module dies with its last consumer's refactor phase
- [ ] ~~`uv run pytest` passes with deleted/rewritten tests for retired modules~~ → no modules retired in Phase 5

---

## Phase 6 — Daemon

**Status: ✅ Slice A1 (core sync pipe) + Slice A2 (cache + smart reconcile) COMPLETE (2026-06-14). Slice B (installable desktop app) — plan written, NOT implemented.**

**`DEPENDS ON: Phase 5 · WEIGHT: medium · TYPE: new local package`**

> **Rearchitecture phase.** Read `docs/0_draft/cloud_native_rearchitecture.md` §4 for full daemon spec. The daemon is a thin bridge — no AI, no DB, no classification. Watch + extract + upload + report events.

### Goal

The daemon runs on the user's laptop. It watches the entire vault directory, detects file changes, extracts text content using handlers (PDF, DOCX, etc.), and uploads the extracted text to AgentBase via HTTPS. When the user moves, renames, or deletes files, the daemon reports those events so the cloud DB stays in sync.

After this phase: the user installs a single app on their Mac. It watches their vault folder and keeps the cloud knowledge base fed with content. No configuration beyond "point at your vault folder" and "paste your auth token."

### How the pieces fit together

```
# Phase 6 — Daemon: What Happens Inside

User's laptop                              AgentBase (cloud)
─────────────                              ──────────────────

┌──────────────────────────────┐
│ VAULT WATCHER                │
│ Watches entire vault tree    │
│ Detects: create, modify,    │
│ move, rename, delete         │
└──────────┬───────────────────┘
           │ file event
           ▼
┌──────────────────────────────┐
│ TEXT EXTRACTOR               │
│ Uses handlers/ to extract    │
│ text from file               │
│ (PDF→text, DOCX→text, etc.) │
│ Falls back to raw bytes if   │
│ extraction fails             │
└──────────┬───────────────────┘
           │ extracted text + metadata
           ▼
┌──────────────────────────────┐        ┌──────────────────────┐
│ UPLOADER                     │──POST──│ /api/upload          │
│ HTTPS to AgentBase           │        │ (from Phase 5)       │
│ Auth via service account     │        └──────────────────────┘
└──────────────────────────────┘

File move/rename/delete:
┌──────────────────────────────┐        ┌──────────────────────┐
│ EVENT REPORTER               │──POST──│ /api/event           │
│ Reports path changes         │        │ (from Phase 5)       │
└──────────────────────────────┘        └──────────────────────┘

Startup:
┌──────────────────────────────┐
│ STARTUP SCANNER              │
│ Full vault walk              │
│ Diff against cloud DB state  │
│ Upload new/changed files     │
│ Report deleted files         │
└──────────────────────────────┘
```

### Components

---

**VAULT WATCHER** *(adapt from `vault/watcher.py` — dramatically simplified)*

> **⚠️ MODULE DELETION RIDES HERE (added 2026-06-12).** Phase 5's "retire dead modules" was deferred (see Phase 5 sequencing warning). The current `vault/watcher.py` imports `vault/writer.py`, `frontmatter.py`, `reader.py`, `indexer.py`, `move_guard.py`. When watcher is rewritten/moved to the daemon here, drop those imports. A shared module dies only when its LAST live consumer is gone — coordinate with Phase 7 (capture) and the unassigned `reconcile.py` before deleting any shared file.

Watch the entire vault directory tree for file changes. No binary sync callbacks, no sibling management, no `_should_skip` for `.summaries/`. Just detect events and dispatch.

- **Input:** Vault root path from daemon config
- **Output:** File events: `created(path)`, `modified(path)`, `moved(old_path, new_path)`, `deleted(path)`
- **Reuse from current `vault/watcher.py`:** debounce logic, `watchdog` integration, NFC normalization. Strip: binary sync, sibling management, `_should_skip` for managed dirs, `move_guard` checks.
- **Rules:**
  - Watch entire vault — no drop zone, no excluded folders (except maybe `.git`, `.obsidian`)
  - Debounce rapid changes (reuse existing debounce pattern)
  - On `modified`: check content hash against last known hash → skip if unchanged
- **Acceptance:** Drop a file in any vault subfolder → watcher fires `created` event. Edit file → `modified`. Move file → `moved(old, new)`. Delete → `deleted`.

---

**TEXT EXTRACTOR** *(reuse `handlers/*.py`)*

Extract text content from files using existing handler registry.

- **Input:** File path from watcher event
- **Output:** `Success(ExtractedContent(text, content_hash, filename, size_bytes, metadata))` or raw bytes fallback
  - `metadata` dict includes: (a) **filesystem metadata** — created time, modified time from `os.stat()`; (b) **format-specific metadata** — PDF author/title/creation date, DOCX author/properties, XLSX sheet names, image EXIF (extracted by handlers alongside text). Decided during P5 Slice 2 grill (2026-06-13).
- **Rules:**
  - Try handler extraction first (fast, produces clean text)
  - If handler fails (unsupported format, corrupted file): read raw bytes as fallback, upload those
  - Content hash (SHA-256) computed on extracted text for dedup
- **Reuse:** `handlers/base.py::HandlerRegistry`, all existing handlers (PDF, DOCX, XLSX, CSV, markdown, etc.)
- **Acceptance:** PDF → text extracted. Unknown format → raw bytes uploaded. Content hash matches for identical files.

---

**UPLOADER** *(new)*

Upload extracted content to AgentBase via HTTPS.

- **Input:** `ExtractedContent` + vault-relative path
- **Output:** `Success(document_id)` or `Failure` (with retry logic)
- **Rules:**
  - POST to `/api/upload` endpoint (from Phase 5)
  - Auth: bearer token from IAM service account (daemon config)
  - Retry on network failure (exponential backoff, max 3 retries)
  - Parallel uploads capped at N concurrent (configurable)
  - If upload fails after retries: log locally, retry on next startup scan
- **Acceptance:** Upload succeeds → document appears in cloud DB. Network down → retries → logs failure. Daemon restart → scanner catches missed uploads.

---

**EVENT REPORTER** *(new)*

Report file move/rename/delete events to AgentBase.

- **Input:** File event (moved, renamed, deleted) with old/new paths
- **Output:** `Success` or `Failure` (with retry)
- **Rules:**
  - POST to `/api/event` endpoint (from Phase 5)
  - Same auth and retry logic as UPLOADER
  - Move = old_path + new_path. Delete = path only.
- **Acceptance:** Move file → cloud DB `vault_path` updated. Delete file → cloud DB marks document accordingly.

---

**STARTUP SCANNER** *(new)*

On daemon start, walk entire vault and diff against cloud DB state to catch changes that happened while daemon was offline.

- **Input:** Vault root path
- **Output:** List of actions: upload new, re-upload changed, report deleted
- **Rules:**
  - GET current state from cloud (list of vault_path + content_hash pairs)
  - Walk vault: for each file, compute content_hash → compare against cloud state
  - New files → upload. Changed hash → re-upload. Missing from vault → report deleted.
  - Batch uploads for efficiency
- **Acceptance:** Stop daemon → add/edit/delete files → restart daemon → cloud DB matches vault state.

---

**DAEMON INSTALLER** *(build last)*

> **🔪 SCOPED AS "SLICE B" + build-pipeline COMPLETE (2026-06-14).** This component is the Phase 6 **Slice B** (installable app), planned end-to-end via build-pipeline: grill → design → spec → research → plan. **Plan-only, no code; implementation gated on Slice A2 landing.** Key decisions overriding/refining the bullets below: ships as a **NATIVE app, NOT Docker** (ADR-0016 — Docker Desktop can't deliver live FS-watching across the Mac/Win VM boundary); **cross-platform Mac + Windows** (not Mac-only); **unsigned** + one-time guided Gatekeeper/SmartScreen override (no paid signing this slice); API key in **OS secure store via `keyring`** (not launchd env — that's Mac-only); **one generic build per OS + baked editable default endpoint** (supports multiple testers + separate vaults); first-run **Tkinter** wizard hard-blocks on a live **authed** test (`GET /api/state`, not `/health`); **manual re-download** updates (auto-update deferred); **minimal `pystray` tray**; **installer per OS** (DMG + Inno/NSIS) with clean uninstall (removes app + startup reg + config + keyring entry); **launch-on-login default ON**. 7 implementation phases, behavior IDs `P6-SLICEB-01…10`. New deps planned: `keyring`, `pystray`, `pyinstaller`. Artifacts: `docs/{0_draft,1_design,2_specs,3_research,4_plans}/phase6/phase6_sliceB_*` + ADR-0016.

Package daemon as a single installable app for Mac.

- **Input:** Daemon source code + handlers + dependencies
- **Output:** `.app` bundle (PyInstaller) or Homebrew formula
- **Rules:**
  - Single binary — no Python install required for user
  - First-run setup: prompt for vault path + AgentBase endpoint URL + API key (the `KMS_DAEMON_API_KEY` set on their container env) → write daemon config. Guide user: "Ask your admin for the endpoint URL and API key for your runtime instance."
  - Launches on system startup (optional, launchd plist)
  - Tray icon showing sync status (optional, nice-to-have)
- **Acceptance:** Non-technical user can install and configure in under 2 minutes. Daemon starts watching vault after setup.

---

### Acceptance criteria (behavior test)
- [ ] Install daemon on a Mac
- [ ] Point at vault folder, paste auth token
- [ ] Drop a PDF into any vault subfolder → text appears in cloud DB within 10 seconds
- [ ] Move a file → cloud DB `vault_path` updates
- [ ] Delete a file → cloud DB reflects deletion
- [ ] Close laptop → reopen → daemon scanner catches any missed changes
- [ ] Network disconnection → daemon retries → eventually syncs
- [ ] Unsupported file format → raw bytes uploaded as fallback → capture still works

---

## Phase 7 — Capture Refactor

**Status: ✅ COMPLETE (2026-06-14). Phase 7A (Text Capture) + Phase 7B (Visual/Binary Capture) — both merged to cloud-native.**

**`DEPENDS ON: Phase 5 (DB schema + API) · WEIGHT: heavy · TYPE: rewrite`**

> **Rearchitecture phase.** Read `docs/0_draft/cloud_native_rearchitecture.md` §3 and §15.1 for full context. This is the biggest code change — `capture.py` goes from 2241 lines of vault-writing logic to a clean extract→summarize→store-to-DB pipeline.

### Goal

Rewrite the capture pipeline to work in the cloud model. Input changes from a local `Path` to extracted text arriving via daemon HTTPS upload. Output changes from vault file writes (`write_note`, frontmatter, sibling `.md`) to structured summary stored in DB. No vault writes. No classify inline (that's Phase 8, separate async process).

After this phase: when daemon uploads extracted text, the cloud generates a structured summary (overview, key points, decisions, action items, people mentioned) and stores everything in the `documents` table. The file is searchable immediately.

### How the pieces fit together

```
# Phase 7 — Capture Refactor: What Happens Inside

  Daemon uploads via /api/upload
           │
           │ extracted_text + vault_path + content_hash + filename + size
           ▼
  ┌──────────────────────────────────┐
  │ CAPTURE RECEIVER                 │
  │ Validates upload, checks         │
  │ idempotency (content_hash)       │
  └──────────┬───────────────────────┘
             │
             ▼
  ┌──────────────────────────────────┐
  │ SUMMARIZER                       │
  │ LLM generates structured summary│
  │ (overview, key points, decisions,│
  │ action items, people mentioned)  │
  └──────────┬───────────────────────┘
             │
             ▼
  ┌──────────────────────────────────┐
  │ DB WRITER                        │
  │ Stores summary + full_body +     │
  │ metadata to documents table      │
  │ Indexes embeddings + keywords    │
  └──────────┬───────────────────────┘
             │
             ▼
  ┌──────────────────────────────────┐
  │ CLASSIFY TRIGGER                 │
  │ Queues document for async        │
  │ classify (Phase 8)               │
  └──────────────────────────────────┘
```

### Components

---

**CAPTURE RECEIVER** *(rewrite of top of `capture_file()`)*

Accept extracted text from daemon upload API. Validate input. Check idempotency.

- **Input:** Extracted text, vault_path, content_hash, original_filename, file_size_bytes, metadata (open-ended JSON — filesystem timestamps + format-specific metadata from daemon; see Phase 6 TEXT EXTRACTOR)
- **Output:** `Success(CaptureInput)` or `Success(ALREADY_PROCESSED)` if content_hash matches existing record
- **Rules:**
  - Content hash is the dedup key. Same hash = already processed = skip (idempotent).
  - If hash differs from existing record for same vault_path: re-capture (content changed).
  - If vault_path is new: fresh capture.
  - Return `Success` or `Failure` — never raise (C-12).
- **Acceptance:** Upload same file twice → second is skipped. Upload changed file → re-captured. New file → captured.

---

**SUMMARIZER** *(rewrite of summarize stage in capture.py)*

Generate structured summary from extracted text using LLM.

- **Input:** Extracted text + filename
- **Output:** `Success(StructuredSummary(overview, key_points, decisions, action_items, people_mentioned, tags))` or `Failure`
- **Rules:**
  - Load prompt from `prompts/summarize.yaml` — never inline (C-07)
  - Call via `get_provider("capture", CONFIG.main)` (C-08)
  - Summary is structured with named sections — richer than old 2-4 sentence frontmatter blurb
  - Validate tags via `core.tags.validate_tags()` (TD-019)
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `llm/provider.py`, `llm/prompt_loader.py`, `core/tags.py`
- **Acceptance:** Upload meeting notes → structured summary has all named sections. Tags validated.

---

**DB WRITER** *(rewrite of `_store_md()` and `_store_nonmd()`)*

Store structured summary + full body + metadata to documents table. Index for search.

- **Input:** `StructuredSummary` + `CaptureInput` (vault_path, full_body, content_hash, filename, size)
- **Output:** `Success(document_id)` or `Failure`
- **Rules:**
  - Call `documents.upsert()` with new signature (no `WriteOutcome` — direct fields)
  - Store `full_body` in documents table (always available from DB)
  - Index embeddings via `retrieval/embeddings.py::index_embedding()` (best-effort)
  - Index keywords via `retrieval/keyword.py::index_keywords()` (best-effort)
  - Write `CAPTURED` audit entry via `core.audit.write()` (C-13)
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `storage/documents.py`, `retrieval/embeddings.py`, `retrieval/keyword.py`, `core/audit.py`
- **Acceptance:** After capture, document has `full_body`, `summary`, `title`, `tags`, `content_hash` in DB. Search finds it by keyword and meaning.

---

**CLASSIFY TRIGGER** *(new — lightweight)*

After capture, queue the document for async classification (Phase 8).

- **Input:** `document_id` from DB WRITER
- **Output:** Queued for classify. If Phase 8 not yet built, this is a no-op stub.
- **Rules:**
  - Classify is a separate async process — capture does NOT wait for it
  - For MVP: direct function call. Later: message queue.
  - Stub is acceptable — Phase 8 fills in the real classify.
- **Acceptance:** Capture completes without waiting for classify. Document is searchable before classify runs.

---

> **📌 DESIGN NOTE — folder structure as user-intent signal (decided P5 Slice 2 grill, 2026-06-13).** Current system infers project/domain from folder path (`Projects/Alpha/` → project=Alpha). In the new model, classify (Phase 8) extracts project/domain from document *content* via LLM. But the user's folder structure is still a meaningful signal of intent — putting a file in `Projects/Alpha/` expresses "this belongs to Alpha." Design question for Phase 7 (capture) + Phase 8 (classify): should `vault_path` be passed to the LLM as an input signal alongside content? If so, how much weight vs content-derived classification? This needs explicit design during those phases — do not silently ignore folder structure, and do not silently treat it as authoritative. Also: folder-level move/delete events are broken into per-file events by the daemon (decided same grill) — the cloud endpoint only handles individual files.

> **📌 DESIGN NOTE — knowledge_entries source cleanup on document deletion (decided P5 Slice 2 grill, 2026-06-13).** When a document is deleted (via `/api/event`), its ID must be removed from the `sources` JSON array of every `knowledge_entries` row that references it. If a knowledge entry's `sources` list becomes empty after removal, set its `status` to `pending` (flag for review) — do NOT auto-delete it. Rationale: a fact like "Anthony is Product Lead" may have been extracted from multiple documents; deleting one source doesn't make the fact untrue. Only when ALL sources are gone is the fact unverified. User may have manually promoted the entry to `confident` — auto-deleting would destroy human judgment. This cleanup logic lives in the `/api/event` delete handler, but only activates once Phase 8 (Classify) is populating `knowledge_entries`. Until then, the stub delete in Phase 5 Slice 2 only cleans `documents` + search tables.

### What retires in this phase
- `_store_md()` — replaced by DB WRITER
- `_store_nonmd()` — replaced by DB WRITER (no sibling files)
- `_classify_auto_md_move()` — dead (no inline classify, no file moves)
- `capture_folder()` — replaced by daemon batch upload
- All `write_note()` calls in capture — dead
- All `WriteOutcome` usage — dead
- All frontmatter writes — dead
- All sibling `.md` creation — dead

> **⚠️ MODULE DELETION RIDES HERE (added 2026-06-12).** Phase 5's "retire dead modules" was deferred (see Phase 5 sequencing warning). Once `capture.py` no longer imports them, this phase deletes capture's share of the dead modules: `vault/writer.py`, `vault/frontmatter.py`, `vault/indexer.py`, and the `vault/paths.py` placement helpers — **but only if no other live consumer remains** (watcher → Phase 6, reconcile → unassigned). Coordinate with Phase 6: a module dies only when its LAST consumer is gone. Also: swap `documents.upsert()` from the additive old-path (kept alive in Phase 5) to the new structured-summary signature here, then drop the dead `WriteOutcome` path.

### Acceptance criteria (behavior test)
- [ ] Daemon uploads extracted text for a markdown note → cloud produces structured summary in DB
- [ ] Daemon uploads extracted text for a PDF → same pipeline, same result
- [ ] Search finds the captured document by keyword and meaning
- [ ] `documents.full_body` contains complete extracted text
- [ ] Summary has named sections (overview, key points, decisions, action items, people mentioned)
- [ ] Audit log has `CAPTURED` entry with confidence and reasoning
- [ ] Upload same content twice → second upload is idempotent (skipped)
- [ ] Upload changed content for same path → re-captured with new summary
- [ ] No vault files written by capture (no `.summaries/`, no frontmatter, no moves)

---

## Phase 8 — Classify Redesign

**`DEPENDS ON: Phase 7 (Capture must populate documents.full_body) · WEIGHT: heavy · TYPE: complete rewrite`**

> **Rearchitecture phase.** Read `docs/0_draft/cloud_native_rearchitecture.md` §7 for the full knowledge_entries design. This is a clean rewrite — old classify (pick a folder) is completely replaced by entity extraction into dimension tables.

### Goal

Build the new classify pipeline: read document content from DB, extract structured knowledge across dimensions (people, projects, domains), store entries in `knowledge_entries` table with lifecycle management (confident/pending/retired). This replaces CLAUDE.md files as the system's living context.

After this phase: when a document is captured, classify runs async and extracts facts like "Anthony is Product Lead for Movie Q2" → stored as a `knowledge_entry` with dimension=`people`, entity=`Anthony`, tag=`role`, fact=`Product Lead for Movie Q2`, status=`confident`. All structured, queryable, source-traced.

### How the pieces fit together

```
# Phase 8 — Classify Redesign: What Happens Inside

  Document captured (from Phase 7)
           │
           │ document_id
           ▼
  ┌──────────────────────────────────┐
  │ CONTENT READER                   │
  │ Reads full_body from documents   │
  │ table in DB                      │
  └──────────┬───────────────────────┘
             │
             ▼
  ┌──────────────────────────────────┐
  │ CONTEXT LOADER                   │
  │ For each dimension, loads        │
  │ existing confident + pending     │
  │ entries for entities mentioned   │
  └──────────┬───────────────────────┘
             │
             ▼
  ┌──────────────────────────────────┐
  │ ENTITY EXTRACTOR                 │
  │ LLM reads document + existing    │
  │ entries → extracts new facts,    │
  │ updates existing, retires stale  │
  └──────────┬───────────────────────┘
             │
             ▼
  ┌──────────────────────────────────┐
  │ ENTRY WRITER                     │
  │ Writes new/updated entries to    │
  │ knowledge_entries table          │
  │ Retires superseded entries       │
  │ Audit logs every extraction      │
  └──────────────────────────────────┘
```

### Components

---

**CONTENT READER** *(new — simple)*

Read document content from DB for classify processing.

- **Input:** `document_id`
- **Output:** `Success(DocumentContent(vault_path, title, summary, full_body, tags))` or `Failure`
- **Rules:**
  - Read from `documents` table — never from vault filesystem
  - Return `Failure` if document not found or `full_body` is empty
- **Reuse:** `storage/documents.py::get_by_id()` or similar
- **Acceptance:** Document captured in Phase 7 → CONTENT READER retrieves full text from DB.

---

**CONTEXT LOADER** *(new)*

Load existing knowledge entries for entities that appear in the document. Gives the LLM context to update rather than re-extract.

- **Input:** `DocumentContent` + dimension config
- **Output:** `Success(ExistingContext(entries_by_dimension={...}))` — map of dimension → list of existing entries
- **Rules:**
  - Query `knowledge_entries` for `confident` + `pending` entries only. Do NOT load `retired` entries (reduces noise and tokens).
  - Initial version: load ALL confident + pending entries (vault is small). Optimize later if token budget is a problem.
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `storage/knowledge_entries.py::get_confident_and_pending()`
- **Acceptance:** Existing entries for relevant entities loaded. Retired entries excluded.

---

**ENTITY EXTRACTOR** *(new — core of the new classify)*

Call LLM with document content + existing entries → extract new facts, update existing facts, retire superseded facts.

- **Input:** `DocumentContent` + `ExistingContext`
- **Output:** `Success(ExtractionResult(new_entries=[...], updated_entries=[...], retired_entries=[...]))` or `Failure`
- **Rules:**
  - Load prompt from `prompts/entity_extract.yaml` — never inline (C-07)
  - Call via `get_provider("classify", CONFIG.main)` (C-08)
  - Every extracted entry must have: `dimension`, `entity`, `tag`, `fact`, `confidence`, `sources` (document IDs/paths)
  - Validate dimension and tag against config: `validate_dimension_tag(dim, tag, config)`. Reject unknown values.
  - Confidence drives initial status via config thresholds (C-06): high → `confident`, medium → `pending`, low → `pending`
  - Concise facts — same discipline as old CLAUDE.md. Not a dumping ground.
  - **Folder structure as input signal:** `vault_path` carries user intent (e.g., `Projects/Alpha/` → user considers this part of Alpha). Design must decide how to weight folder-path signal vs content-derived classification. See Phase 7 design note (P5 Slice 2 grill, 2026-06-13).
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `llm/provider.py`, `llm/prompt_loader.py`, `core/confidence.py`, `core/tags.py`
- **Acceptance:** Meeting notes about "Anthony, Product Lead for Movie Q2" → extracts entity `Anthony`, dimension `people`, tag `role`, fact `Product Lead for Movie Q2`.

---

**ENTRY WRITER** *(new)*

Write extracted entries to `knowledge_entries` table. Handle updates and retirements.

- **Input:** `ExtractionResult` from ENTITY EXTRACTOR
- **Output:** `Success(write_count)` or `Failure`
- **Rules:**
  - New entries: `upsert()` to knowledge_entries
  - Updated entries: update fact, confidence, sources, reasoning, updated_at
  - Retired entries: set `status='retired'`, add retirement reasoning
  - Every entry must have `sources` — no entry without traceability (new constraint)
  - Audit log entry for every extraction run via `core.audit.write()` (C-13)
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `storage/knowledge_entries.py`, `core/audit.py`
- **Acceptance:** After classify: new entries in DB with correct dimension/entity/tag/fact/status. Superseded entries retired. Audit log has `CLASSIFIED` entry.

---

### What retires in this phase
- `pipelines/classify.py` old code — complete rewrite. `classify(subject, valid_destinations)` → replaced by entity extraction.
- `ClassifyResult(project, domain, confidence, reasoning)` — dead. Replaced by `ExtractionResult`.
- `build_subject()` — dead. Content comes from DB, not frontmatter.
- Project registry → replaced by `SELECT DISTINCT entity FROM knowledge_entries WHERE dimension='projects'`

### Acceptance criteria (behavior test)
- [ ] Capture a meeting note about "Q2 progress meeting with Anthony from Finance"
- [ ] Classify runs async after capture
- [ ] `knowledge_entries` table has entries:
  - dimension=`people`, entity=`Anthony`, tag=`role`, fact contains role info
  - dimension=`projects`, entity=`Q2`, tag=`status` or `other`, fact contains progress info
  - dimension=`domains`, entity=`Finance`, tag=`other`, fact contains relevant info
- [ ] Every entry has `sources` pointing to the captured document
- [ ] Every entry has `status` (`confident` or `pending`) based on confidence thresholds
- [ ] Capture a second note mentioning Anthony with new info → existing entries updated, old info retired
- [ ] Audit log has `CLASSIFIED` entry for each extraction run
- [ ] Invalid dimension or tag in LLM output → rejected by validation

---

## Phase 9 — MCP Adaptation

**`DEPENDS ON: Phase 8 (knowledge_entries must be populated) · WEIGHT: medium · TYPE: adaptation`**

> **Rearchitecture phase.** Read `docs/0_draft/cloud_native_rearchitecture.md` §13 for Phase 4 impact analysis. Adapt the existing MCP server to the cloud-native model — remove dead tools, rewire context injection to use knowledge_entries, deploy on AgentBase.

### Goal

Adapt the Phase 4 MCP server to the cloud-native architecture. Remove `kms_move`. Rewrite context injection to pull from `knowledge_entries` instead of CLAUDE.md files. Make `kms_inspect` use the three-tier model (summary → full_body from DB → vault path). Deploy as AgentBase Resource Gateway.

After this phase: user-facing AI (Claude Desktop, web, mobile) connects to the MCP server on AgentBase. `kms_search` finds documents, `kms_vault_info` shows knowledge entries summary, `kms_read` reads from DB, `kms_inspect` shows three tiers. All work 24/7 — no laptop needed for read/search operations.

### Pre-existing fixes to pick up

- **C1 (from Phase 7.5 nuclear review, 2026-06-14):** `api.py:62` re-reads `KMS_DAEMON_API_KEY` from `os.environ` on every request. Read once at app startup instead. LOW severity — correct behavior, just wasteful.

### Components

---

**REMOVE `kms_move`** *(simple — delete)*

> **⚠️ MODULE DELETION RIDES HERE (added 2026-06-12).** Phase 5's "retire dead modules" was deferred (see Phase 5 sequencing warning). `mcp_server/_move.py` (+ `kms_move` shim) and the `move_guard`/`reader` imports inside `mcp_server/` are alive until this phase. Their deletion belongs here, not Phase 5.

- Delete `mcp_server/_move.py` entirely
- Remove `kms_move` tool from `tools.py`
- Remove move-related instructions from `AI_INSTRUCTIONS.md`
- **Acceptance:** `kms_move` no longer appears in tool list.

---

**REWRITE `context.py`** *(major change)*

Context injection engine currently reads CLAUDE.md from disk + builds context from search. Rewrite to pull from `knowledge_entries` table.

- **Context source:** `knowledge_entries` (distilled facts, primary) + search results (supporting evidence)
- **Project→domain lookup:** `SELECT DISTINCT entity FROM knowledge_entries WHERE dimension='projects'` replaces filesystem registry
- **Knowledge block:** For matched entities, include relevant `confident` entries grouped by dimension
- **Rules:**
  - Knowledge entries first, search results second (see rearchitecture doc §8)
  - `kms_vault_info` returns knowledge entries summary instead of CLAUDE.md content
  - Frequency-threshold gating pattern survives (reuse)
  - Content-hash dedup survives (reuse)
  - **Trust-aware sorting:** order entries by `trust_score DESC` within each dimension block. No `min_trust` filtering yet — all entries start at 0.5 (Phase 8 default), so filtering would exclude nothing. `min_trust` filtering activates in Phase 10 when corrections start moving scores.
  - **`retrieval_count` increment (from Phase 8 grill, 2026-06-14):** every time a knowledge entry is surfaced in an MCP tool response (context injection), increment `knowledge_entries.retrieval_count` for that entry. This provides a demand signal — facts users ask about rank higher in the Phase 8 Context Loader's budget-capped ranker. The column ships in Phase 8 (default 0); Phase 9 populates it.
  - **Add `retrieval_count` to ranker (from Phase 8 grill, 2026-06-14):** Phase 8's Context Loader ranks by `trust_score DESC, confidence DESC, updated_at DESC`. Phase 9 adds `retrieval_count DESC` to the formula for context injection sorting: `ORDER BY trust_score DESC, retrieval_count DESC, updated_at DESC`.
- **Reuse:** `storage/knowledge_entries.py`, `retrieval/search.py`
- **Acceptance:** `kms_vault_info` returns knowledge entries grouped by dimension, sorted by trust score. Context injection includes relevant entries for query entities.

---

**REWRITE `_resolve.py`** *(medium change)*

Three-tier retrieval:
- Tier 1: structured summary from `documents.summary` + relevant `knowledge_entries`
- Tier 2: `documents.full_body` from DB (always available)
- Tier 3: vault path for raw file access (laptop-dependent)

- **Rules:**
  - Tier 1-2 always work (DB). Tier 3 only when daemon is connected.
  - No handler registry calls — text already in DB.
- **Acceptance:** `kms_inspect` returns summary (tier 1), full text (tier 2), or vault path (tier 3).

---

**AGENTBASE DEPLOYMENT** *(depends on container from Phase 5)*

Deploy MCP server on AgentBase via Resource Gateway.

- **Resource Gateway config:** proxy MCP JSON-RPC to container port 8080. Inbound auth (IAM or JWT).
- **Gateway endpoint:** the URL user-facing Claude connects to as MCP server.
- **Update `AI_INSTRUCTIONS.md`:** remove move instructions, add knowledge entry context, explain three-tier retrieval.
- **Acceptance:** Claude Desktop connects to AgentBase MCP gateway. `kms_search`, `kms_read`, `kms_vault_info`, `kms_inspect` all work remotely.

---

### Acceptance criteria (behavior test)
- [ ] `kms_move` removed — not in tool list
- [ ] `kms_vault_info` returns knowledge entries grouped by dimension (not CLAUDE.md content)
- [ ] `kms_search` works from cloud DB (no vault needed)
- [ ] `kms_read` reads from DB (no vault needed)
- [ ] `kms_inspect` tier 1: summary + knowledge entries. Tier 2: full body from DB. Tier 3: vault path (laptop-dependent)
- [ ] Context injection includes relevant knowledge entries for query entities
- [ ] Claude Desktop connects to AgentBase MCP gateway
- [ ] All MCP tools work with laptop closed (tiers 1-2 only)

---

## Phase 10 — Web UI + Self-Learning

**`DEPENDS ON: Phase 9 (MCP tools and knowledge_entries must be live) · WEIGHT: heavy · TYPE: new feature`**

> **Rearchitecture phase.** Read `docs/0_draft/cloud_native_rearchitecture.md` §10 for web UI requirements and §7 for self-learning loop design. Tech stack, hosting, exact UI — all decided during design phase for this phase.

### Goal

Build a web interface that replaces CLAUDE.md and Obsidian as the user's window into their knowledge base. Users browse knowledge entries, view document summaries, correct AI mistakes, and add comments. Corrections feed a self-learning loop that improves future extractions.

After this phase: the user opens a web page and sees all their structured knowledge — people, projects, domains — grouped and filterable. They can promote pending entries, retire wrong ones, edit facts, and comment. The house AI learns from every correction.

### Components

---

**BROWSE** *(new)*

View knowledge entries grouped by dimension/entity. View document summaries. Filter by dimension, entity, tag, status.

- **Requirements:**
  - Group entries by dimension, then by entity within each dimension
  - Show entry status (confident/pending/retired) with visual indicator
  - Filter by: dimension, entity name, tag, status
  - Click entity → see all entries for that entity across dimensions
  - Click source → see the document summary
  - Paginate if entries are many

---

**CORRECT** *(new)*

Change entry status and edit facts. Corrections are system-readable — they feed self-learning.

- **Requirements:**
  - Promote: pending → confident (one click)
  - Retire: confident → retired (with required reason)
  - Edit: change fact text, tag, or entity name
  - Every correction records: who corrected, when, what changed, what it was before
  - Corrections stored in DB (new `corrections` table or audit log extension)
  - Intuitive — non-technical user must be able to correct without instructions

---

**COMMENT** *(new)*

Add notes/context to entries that house AI should consider in future extractions.

- **Requirements:**
  - Free-text comment on any entry
  - Comments visible to house AI during next classify run
  - Timestamp + author on each comment
  - Comments are additive — no overwrite

---

**SELF-LEARNING LOOP** *(new)*

User corrections feed back as learning signal to improve future extractions.

- **Requirements:**
  - User promotes pending → confident: validates AI extraction
  - User retires confident: corrects AI mistake
  - User edits fact: provides ground truth
  - Corrections recorded and available as few-shot examples for future `entity_extract.yaml` prompts
  - FEW-SHOT INJECTOR: at next classify run, loads recent corrections and prepends as examples to extraction prompt
  - Max corrections in prompt controlled by config (C-06)

---

### Forward items from Phase 8 grill (2026-06-14)

> These decisions were made during Phase 8's grill session and affect Phase 10. Full context: `docs/0_draft/phase8/phase8_classify_redesign_grill.md`.

- **Trust score decay over time** — monitor if stale entries become a problem. Not in MVP; add decay logic if correction data shows need.
- **Token budget monitoring for dimension summaries** — Phase 8 ships dynamic assembly with budget cap. As facts accumulate over time, may need optimization to pre-computed summaries. Watch and optimize.
- **Correction volatility flag** — entries with > 3 corrections get `[frequently corrected]` appended in context blocks (signals contested facts to user-facing Claude).
- **Trust adjustment pure function** — `adjust_trust(current, action) → float`. Promote: +0.05, retire: -0.10, edit: set 0.6. Asymmetric (Hermes-inspired). Deltas in config (C-06).
- **`min_trust` filtering activation** — config `mcp.context_injection.min_trust: 0.3` starts excluding entries once corrections move trust scores. Until then, all entries pass (flat 0.5 default).

### Design decisions deferred to this phase's `/grill`
- Web UI tech stack (SPA vs server-rendered)
- Hosting (same container as MCP server, or separate)
- Auth for web UI (same IAM as MCP, or separate)
- Exact UI layout and interaction patterns

### Acceptance criteria (behavior test)
- [ ] Open web UI → see knowledge entries grouped by dimension with trust score indicators
- [ ] Filter by dimension "people" → see only people entries
- [ ] Click entity "Anthony" → see all entries about Anthony
- [ ] Promote a pending entry → status changes to confident, trust score increases by 0.05
- [ ] Retire a confident entry → status changes to retired with reason, trust score decreases by 0.10
- [ ] Edit a fact → fact text updated, old value recorded in corrections table, trust score set to 0.6
- [ ] Add comment to entry → comment visible, timestamped
- [ ] After corrections: next classify run includes corrections as few-shot examples and produces better extractions for similar content
- [ ] Low-trust entries (< 0.3) excluded from MCP context injection
- [ ] Frequently corrected entries flagged in context blocks
- [ ] Correction analytics dashboard shows trends by dimension
- [ ] Non-technical user completes browse + correct flow without instructions

---

## Rearchitecture Dependency Graph

```
COMPLETED (existing)                    REARCHITECTURE PHASES
────────────────────                    ─────────────────────

Phase 0 (Foundations)  ─┐
Phase 1 (Capture)       │
Phase 1.5 (Pay Debt)    ├─ DONE ──→ Phase 5 (Infrastructure) ──→ Phase 6 (Daemon)
Phase Pre-2 (DB Prep)   │                    │
Vault-Restructure       │                    ▼
Phase 2 (Classify)      │           Phase 7 (Capture Refactor)
Phase 3 (Search)        │                    │
Phase 4 (MCP Server)  ──┘                    ▼
                                    Phase 8 (Classify Redesign)
                                             │
                                             ▼
                                    Phase 9 (MCP Adaptation)
                                             │
                                             ▼
                                    Phase 10 (Web UI + Self-Learning)
```

**Phase 6 (Daemon) and Phase 7 (Capture Refactor) can run in parallel** — both depend on Phase 5 only. Daemon is local code, Capture is cloud code. Different test strategies, no conflicts.

---

## Rules of the Road (updated for rearchitecture)

- **Never skip the pipeline.** Every task goes through `/grill` → `/tdd-implement`. No shortcuts.
- **Read the rearchitecture doc first.** `docs/0_draft/cloud_native_rearchitecture.md` is the single source of truth for system direction.
- **System never writes to vault.** All AI output goes to DB. Vault is read-only input.
- **Daemon is stateless.** No DB, no cache, no AI state. Pure bridge.
- **Dimensions and tags are config-enforced.** AI cannot invent dimensions or tags. Validation rejects unknown values.
- **Every knowledge entry has sources.** No entry without traceability.
- **Audit log is non-negotiable.** Every AI decision is logged. (C-13)
- **MCP tools are thin wrappers.** Zero logic in `mcp_server/tools.py` (C-14).
- **Never add an MCP tool before its pipeline exists and is tested** (C-15).
- **Result types everywhere.** Every public function returns `Success` or `Failure` (C-12).
- **Prompts are YAML.** Never hardcode prompts in code (C-07).
- **Thresholds are config.** Never hardcode confidence thresholds in pipeline code (C-06).
- **Work in worktrees.** Each contributor works in their own git worktree to avoid conflicts.
- **Behavior test is the review gate.** No code review. Run the acceptance criteria. If it works, it ships.
- **Update docs when you ship.** Don't batch — update CLAUDE.md, CONSTRAINTS.md, STATE.md as each phase completes.
