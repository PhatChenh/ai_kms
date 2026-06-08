---
created: 2026-04-26
updated: 2026-06-07
---

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
COMPLETED                          REMAINING
─────────                          ─────────

Phase 0 (Foundations) ─┐
Phase 1 (Capture)      ├─ DONE ──→ Phase 2 (Classify) ──→ Phase 4 (MCP Server)
Phase 1.5 (Pay Debt)   │                                        ↑
Phase Pre-2 (DB Prep)  │           Phase 3 (Search) ────────────┘
Vault-Restructure      │               ↑
                       │               └── depends on Phase 0+1 only
                       │
                       ├─ DONE ──→ Phase 5 (Promotion)       INDEPENDENT
                       ├─ DONE ──→ Phase 6 (Documentation)   INDEPENDENT
                       ├─ DONE ──→ Phase 8 (Briefing)        INDEPENDENT
                       └─ DONE ──→ Phase 9 (Synthesis)       INDEPENDENT

                                   Phase 7 (Self-Learning) ──→ depends on Phase 2
```

**Key insight:** Phase 2 and Phase 3 are independent of each other — both depend on Phase 0+1 only. Phase 4 (MCP) depends on both 2 and 3. This means Phase 2 and Phase 3 can run in parallel.

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

---

## Stable Interfaces (Phase 0+1 — all independent tasks build against these)

These have been stable across 956 tests and multiple phases. Independent tasks import from these modules only:

| Module | Key functions | What it does |
|--------|--------------|--------------|
| `vault/reader.py` | `read_note(path) → Result[Note]` | Read note + frontmatter from vault |
| `vault/writer.py` | `write_note(path, content, metadata, actor)`, `move_note(src, dst)` | Write/move notes (respects `updated_by_human`) |
| `vault/paths.py` | `resolve_placement()`, `project_attachment()`, `domain_attachment()` | Vault path helpers |
| `storage/documents.py` | `upsert()`, `get_by_path()`, `all_paths()`, `delete_by_path()`, `rename()` | Document index CRUD |
| `storage/audit_log.py` | `append(AuditEntry) → Result[int]`, `query()` | Audit log read/write |
| `core/audit.py` | `write(decision, source_ids, pipeline, stage, ...)` | High-level audit writer |
| `core/tags.py` | `validate_tags(tags, taxonomy) → Result` | Tag taxonomy enforcement |
| `core/pipeline.py` | `run_pipeline(stages, input) → Result` | Pipeline executor |
| `core/result.py` | `Success(value)`, `Failure(error, recoverable, context)` | Result type |
| `llm/provider.py` | `get_provider(task, config) → LLMProvider` | LLM call factory |
| `llm/prompt_loader.py` | `PROMPTS["name"].render(**vars)` | Prompt loading from YAML |
| `core/config.py` | `CONFIG` singleton, `VaultConfig`, `MainConfig` | Validated config |

---

## Phase 2 — Classify + Route

**`BLOCKED BY: Phase 0+1 (done) · WEIGHT: medium`**

### Goal

After Phase 1, the system captures and summarizes every note dropped into the inbox. But every note stays in the inbox — the system has no idea where it belongs. As notes accumulate, the inbox becomes as chaotic as the problem it was meant to solve, and the user is back to sorting everything by hand.

Phase 2 closes that gap. After a note is captured, the AI reads its summary and tags, decides which project or domain folder it belongs in, and moves it there automatically. High-confidence decisions happen without the user's involvement. Borderline cases get flagged for review but are not moved. Truly ambiguous notes stay in the inbox with a review marker. The user only touches the edge cases — routine sorting becomes invisible.

### How the pieces fit together

```
# Phase 2 — Classify + Route: What Happens Inside
Scope: Takes a note already in inbox/ and moves it to the correct project or domain folder.
       Does NOT cover: how the note was captured (Phase 1), search (Phase 3), or
       how corrections are learned over time (Phase 7).

How to read this:
  Boxes      = steps the system takes, in order
  BOLD NAME  = component name — maps to the Components section below
  Arrows     = what flows to the next step
  Fork       = a decision with different outcomes

┌───────────────────────────────────┐
│ Captured note sitting in inbox/   │
└──────────────────┬────────────────┘
                   │
                   ▼
┌───────────────────────────────────┐
│ CLASSIFIER                        │
│ AI reads summary + tags, picks    │
│ target folder + confidence score  │
└──────────────────┬────────────────┘
                   │
                   ▼
┌───────────────────────────────────┐
│ TAG VALIDATOR                     │
│ Confirms all output tags exist    │
│ in the allowed taxonomy           │
└──────────────────┬────────────────┘
                   │
                   ▼
┌───────────────────────────────────┐
│ CONFIDENCE GATE                   │
│ Routes by score: high / mid / low │
└────┬──────────────┬───────────────┘
     │              │              │
   high            mid            low
     │              │              │
     ▼              ▼              ▼
┌─────────┐   [Flag for        [Leave in inbox,
│  NOTE   │    review —         add review marker,
│  MOVER  │    don't move]      don't move]
│ moves   │
│ the note│
└─────────┘

Simplified: TAG VALIDATOR and CLASSIFIER are shown as two steps, but both run inside
            the classify pipeline stage before the result reaches CONFIDENCE GATE.
Note: [Flag for review] and [Leave in inbox] are outcomes produced by CONFIDENCE GATE —
      they are not separate components.
```

### Components

---

**CLASSIFIER** *(new — build first; everything else depends on its output)*

Call the AI with a captured note's summary and tags, and get back a destination folder path, confidence score, and reasoning.

- **Input:** A captured `Note` (vault path + summary + tags) from `inbox/`; note must already exist in the document index
- **Output:** `Success(ClassifyResult(folder="Projects/Alpha", confidence=0.91, reasoning="..."))` or `Failure(error, recoverable, context)`
- **Rules:**
  - Load the prompt from `prompts/classify.yaml` via `PROMPTS["classify"].render(summary=..., tags=..., projects_registry=...)` — never write the prompt as an inline f-string (C-07)
  - Call the LLM via `get_provider("classify", CONFIG.main).complete(system, user)` — never instantiate a provider directly (C-08)
  - Must include a **projects registry** in the prompt so the AI knows which projects and domains exist (resolves TD-034). Implement the registry as a `Projects/<A>/meta.yaml` file per project (containing at minimum `domain: <D>` and `description: <one line>`); load all meta.yaml files at classify-time and pass the list to the prompt. If a project has no meta.yaml, domain defaults to `Uncategorized`
  - Return `Success` or `Failure` — never raise an exception (C-12)
  - Write one `CLASSIFIED` audit entry via `core.audit.write(decision="CLASSIFIED", source_ids=[note_path], pipeline="classify", stage="classify", confidence=..., reasoning=...)` (C-13)
- **Reuse:** `llm/provider.py::get_provider`, `llm/prompt_loader.py::PROMPTS`, `core/audit.py::write`, `vault/reader.py::read_note`

---

**TAG VALIDATOR** *(already exists — wire it in; zero new code needed)*

Confirm every tag produced by the CLASSIFIER appears in the allowed taxonomy before passing to routing.

- **Input:** List of tags from `ClassifyResult`
- **Output:** `Success(validated_tags)` or `Failure("TAG_VIOLATION", recoverable=False, context={...})`
- **Rules:**
  - Call `core.tags.validate_tags(tags, taxonomy)` — do not reimplement validation logic (resolves TD-019)
  - On violation: write a `TAG_VIOLATION` audit entry via `core.audit.write(...)` and return `Failure` — never silently accept tags that fall outside the taxonomy (C-13)
  - The taxonomy source is `config/tags.yaml`; do not hardcode tag strings in pipeline code
- **Reuse:** `core/tags.py::validate_tags`, `config/tags.yaml`

---

**CONFIDENCE GATE** *(new — depends on CLASSIFIER; runs after TAG VALIDATOR)*

Read the confidence score from `ClassifyResult` and decide what action to take: auto-move, flag for review, or leave in inbox.

- **Input:** `ClassifyResult` with a validated confidence score (float 0–1)
- **Output:** One of three typed `RouteDecision` values: `AUTO_MOVE(target_folder)`, `REVIEW(suggestion)`, or `CLUELESS`
- **Rules:**
  - Read all thresholds from `config/thresholds.yaml` via `ConfidenceGate.from_config(config)` — never write `if confidence > 0.85` or any float literal inside the pipeline (C-06)
  - `REVIEW` path: add `review: true` and `suggested_folder: <folder>` to the note's frontmatter — do not move the note
  - `CLUELESS` path: add `review: true` to frontmatter and leave a human-readable note explaining the ambiguity — do not move
  - Return a typed `RouteDecision`; the NOTE MOVER only runs when it receives `AUTO_MOVE`
- **Reuse:** `core/confidence.py::ConfidenceGate`, `config/thresholds.yaml`

---

**NOTE MOVER** *(new — depends on CONFIDENCE GATE; runs only on AUTO_MOVE)*

Move the note from inbox to its target folder, update its frontmatter with the classification tags, and update the document index.

- **Input:** `RouteDecision(AUTO_MOVE, target_folder)` + the note's current vault path
- **Output:** `Success(new_vault_path)` or `Failure`
- **Rules:**
  - Call `vault/writer.py::move_note(src, dst)` — never call `os.rename`, `shutil.move`, or `.write_text` directly (C-01)
  - Read the note first via `vault/reader.py::read_note`, merge existing metadata with the new classification fields, then write via `vault/writer.py::write_note(..., actor="ai")` — never overwrite with partial metadata (C-03)
  - Check `updated_by_human` on the existing note before moving; if `True`, skip the move and return `Failure` with a conflict message (C-02)
  - Call `vault/move_guard.py::get_active().register(src_path)` before calling `move_note` so the file watcher does not treat this pipeline move as a misplaced file and re-home it
  - After move, call `storage/documents.py::upsert(...)` with the new vault path to keep the document index consistent
- **Reuse:** `vault/writer.py::move_note`, `vault/writer.py::write_note`, `vault/reader.py::read_note`, `vault/move_guard.py::get_active`, `storage/documents.py::upsert`

---

**CLASSIFY CLI** *(new — build last, after pipeline has passing tests)*

Expose `kms classify <file>` and `kms classify --scan` that call the classify pipeline and print a decision summary.

- **Input:** A single file path, or `--scan` to process all unclassified notes in `inbox/`
- **Output:** Console summary of each decision (moved / flagged / left in inbox) with confidence scores
- **Rules:**
  - Wrap the async pipeline call with `asyncio.run(...)` — no async Click adapters (C-10)
  - Zero logic in the CLI layer; the CLI calls the pipeline and prints its result
  - `--scan` mode: iterate over every `.md` in `inbox/` where `classified` frontmatter field is absent or `False`; skip already-classified notes
  - Do not add `kms_classify` as an MCP tool until this CLI command is verified working end-to-end (C-15, C-16)
- **Reuse:** `cli/main.py` Click group, `core/pipeline.py::run_pipeline`, `vault/reader.py::read_note`

---

### Acceptance criteria (behavior test)
- [ ] Drop a test `.md` note about "Q3 marketing budget" into `inbox/`
- [ ] Run `kms capture <file>` then `kms classify <file>`
- [ ] Note moves to correct `Projects/` or `Domain/` subfolder
- [ ] Frontmatter has classification tags
- [ ] `audit_log` has a CLASSIFIED entry with confidence score and reasoning
- [ ] Low-confidence note stays in inbox with a review flag
- [ ] Run `kms classify --scan` — all inbox notes get classified

---

## Phase 3 — Search + Three-Tier Retrieval

**`INDEPENDENT (depends on Phase 0+1 only) · WEIGHT: heavy`**

### Goal

After Phase 1, every note is captured and summarized. But finding a specific note means remembering its exact title or tags — keyword search the way a file system works. The vault is accumulating knowledge that cannot be found.

Phase 3 makes the vault queryable by meaning. A user can ask "what do I know about stakeholder resistance?" and get back notes that talk about "managing pushback in meetings" — even if those exact words were never used. The system always starts with the cheapest answer (a quick summary) and only goes deeper if the user or Claude needs more detail. This keeps cost low and speed high for every search.

### How the pieces fit together

```
# Phase 3 — Search + Three-Tier Retrieval: What Happens Inside
Scope: The query-time search flow — what happens when a user or Claude asks a question.
       Index building (EMBEDDING INDEXER, KEYWORD INDEXER) runs automatically
       in the background when notes are captured — not shown here.
       Does NOT cover: classification (Phase 2) or MCP integration (Phase 4).

How to read this:
  Boxes      = steps the system takes, in order
  BOLD NAME  = component name — maps to the Components section below
  Arrows     = what flows to the next step
  Fork       = a decision with different outcomes

┌──────────────────────────────────────┐
│ Query string + max_cost from caller  │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ TIER DISPATCHER                      │
│ Starts at hot (summaries only);      │
│ escalates to warm/cold if budget     │
│ allows and caller needs more         │
└──────┬───────────────────────────────┘
       │                    │
   hot only           warm or cold
       │                    │
       ▼                    ▼
[Summary results       ┌──────────────────────────┐
 returned — fast,      │ HYBRID RANKER             │
 cheap, done]          │ Merges keyword (FTS5) +   │
                       │ semantic (embedding)       │
                       │ results and re-ranks them  │
                       └──────────────┬─────────────┘
                                      │
                                      ▼
                       [Snippets (warm) or full
                        note content (cold) returned]

Background (runs at capture time, not shown in query flow):
  EMBEDDING INDEXER — stores note vectors in the embeddings table
  KEYWORD INDEXER   — writes note content to the FTS5 virtual table

Note: [Summary results], [Snippets], and [Full content] are tier outcomes, not components.
```

### Components

Build order: EMBEDDING INDEXER and KEYWORD INDEXER first (they feed the indexes). HYBRID RANKER second (it queries those indexes). TIER DISPATCHER last (it orchestrates the others). SEARCH CLI after the pipeline has passing tests.

---

**EMBEDDING INDEXER** *(new — build first; HYBRID RANKER reads from it)*

For each captured note, compute a vector embedding of its summary and store it in the `embeddings` table so semantic search can work.

- **Input:** A note's vault path and summary text; triggered by the capture pipeline completing, or by a full re-index scan
- **Output:** Row written to `embeddings` table: `(vault_path, model, vector_blob, indexed_at)`; returns `Success(rowcount)` or `Failure`
- **Rules:**
  - Create the `embeddings` table via a new SQL migration file `storage/migrations/006_add_embeddings.sql` — never create tables in Python code (C-05, resolves TD-004)
  - Load the embedding model from `CONFIG.main.providers.claude.embedding_model` (or the active provider's `embedding_model` field) — never hardcode a model name (resolves TD-013); use `sentence-transformers` with `all-MiniLM-L6-v2`
  - Re-indexing must be idempotent: upsert by `vault_path` — running twice produces no duplicates
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `storage/db.py::_connect`, `core/config.py::CONFIG`

---

**KEYWORD INDEXER** *(new — build alongside EMBEDDING INDEXER)*

Write each note's full text into an FTS5 virtual table so keyword search works across the vault.

- **Input:** A note's vault path and full text content; triggered at capture time or by a re-index scan
- **Output:** Row upserted to the FTS5 virtual table `notes_fts`; returns `Success(rowcount)` or `Failure`
- **Rules:**
  - Create the FTS5 virtual table via the same migration file as the embeddings table (`006_add_embeddings.sql`) — one migration covers both (C-05, resolves TD-004)
  - Index both note body text AND the summary field separately so hot-tier summary search does not pull full content
  - Re-indexing must be idempotent: `INSERT OR REPLACE` into FTS5 by `vault_path`
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `storage/db.py::_connect`

---

**HYBRID RANKER** *(new — depends on EMBEDDING INDEXER and KEYWORD INDEXER)*

Given a query, search both the FTS5 keyword index and the semantic embedding index, then merge and re-rank the results into a single ordered list.

- **Input:** Query string + an optional `max_results` count
- **Output:** `Success(list[RankedResult(vault_path, score, snippet, summary)])` ordered by relevance, or `Failure`
- **Rules:**
  - Run keyword search via FTS5 (`retrieval/keyword.py`) and semantic search via cosine similarity (`retrieval/semantic.py`) independently, then merge and re-rank in `retrieval/hybrid.py`
  - Score merging: normalize both score ranges to 0–1 before combining — raw FTS5 rank and cosine similarity are not on the same scale
  - Each `RankedResult` carries: `vault_path`, merged `score`, the best matching `snippet` (surrounding context from FTS5), and the note's `summary` field
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `storage/db.py::_connect`, `vault/reader.py::read_note` (for summaries)

---

**TIER DISPATCHER** *(new — depends on HYBRID RANKER; this is the public interface for search)*

Given a query and a `max_cost` budget, decide which tier to start at and whether to escalate to deeper (more expensive) tiers.

- **Input:** Query string + `max_cost` value (a token/cost budget set by the caller); the caller never specifies which tier
- **Output:** `Success(SearchResult(tier, results))` where `tier` is `"hot"`, `"warm"`, or `"cold"` and `results` is a list from HYBRID RANKER; or `Failure`
- **Rules:**
  - Always start at hot tier (return summaries only from the FTS5 index) — never start at warm or cold
  - Escalate to warm (snippets) only if `max_cost` allows; escalate to cold (full content) only from warm, not directly from hot
  - Read tier cost thresholds from `config/thresholds.yaml` — never hardcode cost values in the dispatcher (C-06)
  - Callers (CLI, MCP) must never pass a tier name directly — they pass `max_cost` and the dispatcher decides
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `core/confidence.py` (cost gate pattern), `config/thresholds.yaml`, HYBRID RANKER

---

**SEARCH CLI** *(new — build last, after pipeline has passing tests)*

Expose `kms search "<query>"` that calls TIER DISPATCHER and prints results, starting at hot tier.

- **Input:** Query string as a CLI argument; optional `--tier cold` flag to force a specific tier for debugging
- **Output:** Console output showing results — summaries for hot tier, snippets for warm, full content for cold
- **Rules:**
  - Wrap the pipeline call with `asyncio.run(...)` — no async Click adapters (C-10)
  - Zero logic in the CLI layer; the CLI calls TIER DISPATCHER and formats its output
  - Default behavior with no `--tier` flag: let TIER DISPATCHER decide (pass `max_cost=CONFIG.main.search.default_max_cost`)
  - Do not add `kms_search` as an MCP tool until this CLI is verified working end-to-end (C-15, C-16)
- **Reuse:** `cli/main.py` Click group, TIER DISPATCHER

---

### Acceptance criteria (behavior test)
- [ ] Capture 5+ diverse notes (meeting notes, research, project update)
- [ ] Run `kms search "stakeholder resistance"` — finds a note about "managing pushback"
- [ ] Hot tier returns summaries only (fast, cheap)
- [ ] Warm tier returns matching snippets with surrounding context
- [ ] Cold tier returns full note content
- [ ] Search works on both `.md` notes and sibling summaries of binaries

---

## Phase 4 — MCP Server MVP

**`BLOCKED BY: Phase 2 + Phase 3 · WEIGHT: medium`**

### Goal

After Phases 2 and 3, the system can capture, classify, and search notes — but only via terminal commands. The user has to open a terminal, remember the right command, and deal with raw text output. That breaks the "zero organizational effort" promise for a non-technical manager.

Phase 4 makes the vault accessible through plain conversation. The user opens Claude Desktop, describes what they want in natural language ("what do I know about the Alpha project?", "capture this email I just received", "sort my inbox"), and Claude does it. The MCP server is the bridge between Claude's conversational interface and the pipelines already built.

**Prerequisite before starting:** Resolve OQ-004 (concurrent `run_pipeline` contextvar bleed). The `clear_contextvars()` call in `new_correlation_id()` wipes context for concurrent calls. Fix: wrap each pipeline call in `copy_context().run(...)` so concurrent tool invocations get isolated contextvars. This must be fixed before MCP ships concurrent tool handling.

### How the pieces fit together

```
# Phase 4 — MCP Server MVP: What Happens Inside
Scope: The path from a Claude Desktop request to a pipeline call and back.
       Does NOT cover: the pipelines themselves (Phases 1–3), HTTP transport (deferred),
       or tools for Promotion/Documentation/Briefing (added in their respective phases).

How to read this:
  Boxes      = steps the system takes, in order
  BOLD NAME  = component name — maps to the Components section below
  Arrows     = what flows to the next step
  Fork       = a decision with different outcomes

┌──────────────────────────────────────┐
│ Claude Desktop sends a tool call     │
│ via stdio                            │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ MCP SERVER                           │
│ Receives request, validates inputs,  │
│ routes to the right tool             │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ TOOL REGISTRY                        │
│ Three thin wrappers:                 │
│ kms_search / kms_capture /           │
│ kms_classify                         │
└───┬──────────────┬────────────────┬──┘
    │              │                │
kms_search    kms_capture      kms_classify
    │              │                │
    ▼              ▼                ▼
[calls          [calls           [calls
 TIER            capture          classify
 DISPATCHER]     pipeline]        pipeline]

Note: kms_search, kms_capture, and kms_classify are tools inside TOOL REGISTRY —
      they are not separate components. Each is one function that calls one pipeline.
```

### Components

---

**MCP SERVER** *(new — build first; TOOL REGISTRY runs inside it)*

Start an MCP server process that listens for tool calls from Claude Desktop over stdio and returns structured results.

- **Input:** JSON-RPC messages from Claude Desktop over stdin; server runs as a long-lived process
- **Output:** JSON-RPC responses sent to stdout; the server never terminates until killed
- **Rules:**
  - Use stdio transport only — HTTP transport is explicitly deferred past Phase 4
  - Set `wal_autocheckpoint=100` in `storage/db.py::_connect()` — the MCP server is a long-running daemon and unchecked WAL growth causes read latency (resolves TD-007); this is a one-line addition to `_connect()`
  - Wrap each incoming tool call in `copy_context().run(pipeline_fn)` so concurrent calls get isolated Python contextvars — prevents correlation ID bleed between simultaneous tool invocations (resolves OQ-004)
  - The server itself must contain no business logic; it receives, dispatches, and returns
- **Reuse:** `mcp` Python library (add to dependencies), `core/logging_setup.py::new_correlation_id`

---

**TOOL REGISTRY** *(new — depends on MCP SERVER; all three tools must be built together)*

Three MCP tool definitions — `kms_search`, `kms_capture`, `kms_classify` — each of which calls exactly one pipeline and returns its result.

- **Input (kms_search):** `{query: string, max_cost?: string}` → calls `retrieval/tiers.py::search(query, max_cost)`
- **Input (kms_capture):** `{content: string, source_type: string}` → calls `pipelines/capture.py::capture_text(content, source_type)`
- **Input (kms_classify):** `{note_path: string}` → calls `pipelines/classify.py::classify_note(note_path)`
- **Output:** Each tool returns a structured dict with the pipeline's `Success` value, or a structured error with the `Failure` reason
- **Rules:**
  - `mcp_server/tools.py` must contain no `if`, `elif`, `for`, or `while` statements at the module level — zero logic, zero branching (C-14). If you find yourself writing a conditional, it belongs in the pipeline, not the tool
  - Each tool is exactly one function: receive input → call the pipeline → return its result
  - Do not add `kms_promote`, `kms_classify_batch`, or any other tool until its pipeline exists and has passing tests (C-15)
  - Edits made through these MCP tools call `write_note(..., actor="ai")` — they are tracked as AI writes. Direct filesystem writes by any tool outside this pipeline are treated as human edits by the watcher (they set `updated_by_human=true`). This is intentional: pipeline vs not-pipeline is the distinction, not human vs AI
- **Reuse:** `retrieval/tiers.py::search`, `pipelines/capture.py::capture_text`, `pipelines/classify.py::classify_note`, `core/result.py`

---

### Acceptance criteria (behavior test)
- [ ] Configure Claude Desktop to use the MCP server
- [ ] Ask Claude: "What do I know about Q3 marketing?" — returns relevant notes
- [ ] Ask Claude: "Capture this: <paste text>" — creates a new note in inbox
- [ ] Ask Claude: "Classify the notes in my inbox" — notes move to correct folders
- [ ] All three tools return structured results, not errors

---

## Phase 5 — Note Promotion

**`INDEPENDENT (depends on Phase 0+1 only) · WEIGHT: medium`**

### Goal

After Phase 1, raw captured notes accumulate in the vault. A meeting note might contain a reusable workflow that the user follows every sprint. A research note might contain a lesson the user wants to refer back to in a year. These insights are buried in raw captures — hard to find, never reused.

Phase 5 extracts the valuable knowledge. When a note contains something reusable — a workflow template, a lesson learned, a research finding — the system detects it, extracts a structured version, and saves it to the appropriate domain folder. The original note is never touched. Only high-confidence promotions happen automatically; borderline cases become proposals for the user to approve.

### How the pieces fit together

```
# Phase 5 — Note Promotion: What Happens Inside
Scope: Reads a captured note and, if valuable, creates a structured knowledge artifact.
       Does NOT cover: how notes were captured (Phase 1), classification (Phase 2),
       or documentation pages (Phase 6).

How to read this:
  Boxes      = steps the system takes, in order
  BOLD NAME  = component name — maps to the Components section below
  Arrows     = what flows to the next step
  Fork       = a decision with different outcomes

┌──────────────────────────────────────┐
│ Captured note (anywhere in vault)    │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ PROMOTABILITY DETECTOR               │
│ AI reads note, decides if it         │
│ contains reusable knowledge          │
└──────┬───────────────────────────────┘
       │                    │
  promotable           not promotable
       │                    │
       ▼                    ▼
┌────────────────┐    [Return Success(skipped)
│ STRUCTURE      │     — no output written]
│ EXTRACTOR      │
│ AI pulls out   │
│ structured     │
│ knowledge form │
└───────┬────────┘
        │
        ▼
┌──────────────────────────────────────┐
│ PROMOTION GATE                       │
│ Routes by confidence: auto / propose │
└──────┬───────────────────────────────┘
       │                    │
    high                   mid/low
       │                    │
       ▼                    ▼
┌────────────────┐    [Write proposal
│ PROMOTION      │     to inbox as a
│ WRITER         │     draft — do not
│ writes to      │     auto-promote]
│ Domain/ folder │
└────────────────┘

Note: [Return Success(skipped)] and [Write proposal] are outcomes — not separate components.
```

### Components

---

**PROMOTABILITY DETECTOR** *(new — build first; STRUCTURE EXTRACTOR depends on its signal)*

Call the AI to decide whether a note contains knowledge worth promoting: a reusable workflow, a lesson learned, or a research finding worth keeping long-term.

- **Input:** A note's vault path + full text content
- **Output:** `Success(PromotabilityResult(is_promotable=True, type="workflow-template", confidence=0.88))` or `Success(PromotabilityResult(is_promotable=False))` or `Failure`
- **Rules:**
  - Load prompt from `prompts/promote_detect.yaml` via `PROMPTS["promote_detect"].render(...)` — never inline a prompt string (C-07)
  - Call via `get_provider("promote", CONFIG.main)` (C-08)
  - Valid `type` values: `"workflow-template"`, `"lesson-learned"`, `"research-finding"` — check against this list; reject unknown types
  - Return `Success` or `Failure` — never raise (C-12)
  - Write a `PROMOTION_EVALUATED` audit entry via `core.audit.write(...)` for every call — including not-promotable decisions (C-13)
- **Reuse:** `llm/provider.py::get_provider`, `llm/prompt_loader.py::PROMPTS`, `core/audit.py::write`, `vault/reader.py::read_note`

---

**STRUCTURE EXTRACTOR** *(new — depends on PROMOTABILITY DETECTOR)*

Call the AI to extract the reusable knowledge from the note into a structured format appropriate for its promotion type.

- **Input:** Note full text + `PromotabilityResult` (type and confidence from detector)
- **Output:** `Success(StructuredNote(title, type, body_markdown, tags, source_path))` or `Failure`
- **Rules:**
  - Load prompt from `prompts/promote_extract.yaml` and pass `note_type` as a template variable — the prompt instructs the AI to produce the right structure per type (C-07)
  - Validate extracted tags via `core.tags.validate_tags(...)` before returning — invalid tags must produce a `Failure`, not be silently included (TD-019)
  - The extracted `StructuredNote` is self-contained: reading it should not require the original note
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `llm/provider.py::get_provider`, `llm/prompt_loader.py::PROMPTS`, `core/tags.py::validate_tags`

---

**PROMOTION GATE** *(new — depends on STRUCTURE EXTRACTOR)*

Read the confidence score and decide: auto-promote (write directly to Domain folder) or write a proposal draft for human approval.

- **Input:** `StructuredNote` with a confidence score
- **Output:** One of two `PromotionDecision` values: `AUTO_PROMOTE(target_folder)` or `PROPOSE(draft_path)`
- **Rules:**
  - Read thresholds from `config/thresholds.yaml` via `ConfidenceGate.from_config(config)` — never hardcode float comparisons (C-06)
  - `PROPOSE` path: write the draft to `inbox/` with a `promotion_proposal: true` frontmatter flag; the user reviews and approves manually — do not auto-write to `Domain/`
  - Return a typed `PromotionDecision`; the PROMOTION WRITER only runs on `AUTO_PROMOTE`
- **Reuse:** `core/confidence.py::ConfidenceGate`, `config/thresholds.yaml`

---

**PROMOTION WRITER** *(new — depends on PROMOTION GATE; runs only on AUTO_PROMOTE)*

Write the structured note to the appropriate `Domain/<D>/` subfolder and log the promotion.

- **Input:** `PromotionDecision(AUTO_PROMOTE, target_folder)` + `StructuredNote`
- **Output:** `Success(promoted_path)` or `Failure`
- **Rules:**
  - The destination folder is under `Domain/<D>/` — use `vault/paths.py::domain_attachment(...)` to resolve the path; never hardcode vault folder names
  - Call `vault/writer.py::write_note(path, content, metadata, actor="ai")` — never write directly to the filesystem (C-01)
  - The original source note must NOT be modified or moved — promotion creates a new artifact, it does not consume the source
  - Call `storage/documents.py::upsert(...)` to index the new promoted note
  - Write a `PROMOTED` audit entry via `core.audit.write(...)` with `source_ids=[source_path]`, confidence, and the target folder (C-13)
- **Reuse:** `vault/writer.py::write_note`, `vault/paths.py::domain_attachment`, `storage/documents.py::upsert`, `core/audit.py::write`

---

**PROMOTE CLI** *(new — build last, after pipeline has passing tests)*

Expose `kms promote <file>` and `kms promote --scan` that call the promotion pipeline.

- **Input:** A single file path, or `--scan` to scan all notes in the vault for promotion candidates
- **Output:** Console summary of what was promoted, proposed, or skipped
- **Rules:**
  - Wrap async calls with `asyncio.run(...)` — no async Click adapters (C-10)
  - Zero logic in the CLI layer
  - Do not add `kms_promote` as an MCP tool until this CLI is verified (C-15, C-16)
- **Reuse:** `cli/main.py` Click group, `core/pipeline.py::run_pipeline`

---

### Acceptance criteria (behavior test)
- [ ] Capture a meeting-notes `.md` that contains a reusable workflow ("every sprint we do X, then Y, then Z")
- [ ] Run `kms promote <file>`
- [ ] A new structured note appears in the correct `Domain/` folder with type `workflow-template` (or similar)
- [ ] Original note unchanged (no overwrite)
- [ ] `audit_log` has a PROMOTED entry with confidence and reasoning
- [ ] Low-confidence promotion stays as a suggestion, not auto-executed

---

## Phase 6 — Documentation Auto-Update

**`INDEPENDENT (depends on Phase 0+1 only) · WEIGHT: medium`**

### Goal

After Phase 1, the vault captures everything that happens on a project. But there is no single place to look to understand where a project stands right now — no living summary page, no progress tracker. Getting a current picture means reading through all the individual notes manually.

Phase 6 creates and maintains one living documentation page per active project. When new notes are captured for a project, the AI proposes an update to the project's doc page. The human reviews and decides what to keep. If the human has edited a section, the AI never touches it again. The documentation page becomes a reliable current-state view of the project, maintained automatically with human oversight on every edit.

**Open design question (resolve during `/grill`):** Documentation pages live in `Documentation/` which is a capture-excluded folder — the watcher does not set `updated_by_human` for edits there. Before building, decide how to detect human edits to these pages: option A is a lightweight second watcher path just for `Documentation/`, option B is requiring humans to manually set `updated_by_human: true` in frontmatter, option C is a hash-comparison approach at update time. Record the decision as an ADR before implementing.

### How the pieces fit together

```
# Phase 6 — Documentation Auto-Update: What Happens Inside
Scope: Reads a project's notes and proposes or writes an update to its doc page.
       Does NOT cover: how notes were captured (Phase 1), per-section AI/human merge
       (deferred post-deadline), or scheduling (built last, after CLI works).

How to read this:
  Boxes      = steps the system takes, in order
  BOLD NAME  = component name — maps to the Components section below
  Arrows     = what flows to the next step
  Fork       = a decision with different outcomes

┌──────────────────────────────────────┐
│ Project name (from CLI or trigger)   │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ PROJECT READER                       │
│ Loads all notes for the project      │
│ and the current doc page (if any)    │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ UPDATE PROPOSER                      │
│ AI diffs new note content against    │
│ the current doc, drafts an update    │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ HUMAN GATE                           │
│ Checks updated_by_human before       │
│ writing anything                     │
└──────┬───────────────────────────────┘
       │                    │
   not locked            locked
       │                    │
       ▼                    ▼
┌────────────────┐    [Skip write,
│ DOC WRITER     │     surface conflict
│ writes update  │     — do not
│ to             │     overwrite]
│ Documentation/ │
└────────────────┘

Note: [Skip write] is an outcome, not a component.
```

### Components

---

**PROJECT READER** *(new — build first; UPDATE PROPOSER depends on it)*

Load all captured notes for a given project and the current documentation page for that project (if it exists).

- **Input:** Project name (string, e.g. `"Alpha"`)
- **Output:** `Success(ProjectBundle(notes=[Note, ...], current_doc=Note|None))` or `Failure`
- **Rules:**
  - Query `storage/documents.py` to find all notes where `project == project_name` — never walk the vault filesystem directly
  - Load each note's full content via `vault/reader.py::read_note` — do not use the cached summary from the documents table as source material for documentation; use the full note
  - The current doc page lives at `Vault/Documentation/<project_name>.md`; if it does not exist, `current_doc=None`
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `storage/documents.py::get_by_path`, `vault/reader.py::read_note`

---

**UPDATE PROPOSER** *(new — depends on PROJECT READER)*

Call the AI with the project's notes and current doc page, and get back a proposed update to the doc page.

- **Input:** `ProjectBundle(notes, current_doc)` from PROJECT READER
- **Output:** `Success(DocUpdate(proposed_content, confidence, reasoning, source_note_paths))` or `Failure`
- **Rules:**
  - Load prompt from `prompts/documentation_update.yaml` via `PROMPTS["documentation_update"].render(...)` — never inline a prompt (C-07)
  - Call via `get_provider("documentation", CONFIG.main)` (C-08)
  - The proposed content must reference source notes as Obsidian wikilinks (`[[note-title]]`) — traceability is non-negotiable
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `llm/provider.py::get_provider`, `llm/prompt_loader.py::PROMPTS`

---

**HUMAN GATE** *(new — depends on UPDATE PROPOSER)*

Before writing anything, check whether the doc page has been edited by a human. If it has, do not write — surface a conflict message instead.

- **Input:** `DocUpdate` + the current doc's `Note` (or None if it does not exist yet)
- **Output:** `Success(WRITE_APPROVED)` or `Success(CONFLICT_FLAGGED(reason))`
- **Rules:**
  - If the current doc has `updated_by_human=True` in frontmatter: return `CONFLICT_FLAGGED` — do not pass to DOC WRITER. This is a hard rule: human wins, AI stops (C-02)
  - If the current doc does not exist: return `WRITE_APPROVED` — first write is always allowed
  - Never return `Failure` for the conflict case — a conflict is an expected outcome, not an error. The caller should log it and exit cleanly
  - Per-section merge (AI updates only some sections while respecting human-edited ones) is explicitly out of scope until post-deadline; the whole-note gate is the contract for now
- **Reuse:** `vault/reader.py::read_note` (to check frontmatter), `core/result.py`

---

**DOC WRITER** *(new — depends on HUMAN GATE; runs only on WRITE_APPROVED)*

Write the proposed doc update to `Documentation/<project_name>.md` and update the document index.

- **Input:** `DocUpdate(proposed_content)` + target path `Documentation/<project_name>.md`
- **Output:** `Success(written_path)` or `Failure`
- **Rules:**
  - Call `vault/writer.py::write_note(path, content, metadata, actor="ai")` — never write directly to the filesystem (C-01)
  - `Documentation/` is a capture-excluded folder — the watcher skips it. This means DOC WRITER must explicitly call `storage/documents.py::upsert(...)` to keep the doc page indexed
  - Write a `DOC_UPDATED` audit entry via `core.audit.write(...)` with `source_ids=[source_note_paths]` and the confidence score (C-13)
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `vault/writer.py::write_note`, `storage/documents.py::upsert`, `core/audit.py::write`

---

**UPDATE-DOCS CLI** *(new — build last, after pipeline has passing tests)*

Expose `kms update-docs <project>` and `kms update-docs --all` that call the documentation pipeline.

- **Input:** Project name, or `--all` to update docs for every active project
- **Output:** Console summary of what was updated, conflicted, or skipped
- **Rules:**
  - Wrap async calls with `asyncio.run(...)` — no async Click adapters (C-10)
  - Zero logic in the CLI layer
  - Do not add `kms_documentation_update` as an MCP tool until this CLI is verified (C-15, C-16)
- **Reuse:** `cli/main.py` Click group, `core/pipeline.py::run_pipeline`

---

### Acceptance criteria (behavior test)
- [ ] Create project folder `Projects/Alpha/` with 3+ captured notes
- [ ] Run `kms update-docs Alpha`
- [ ] `Documentation/Alpha.md` appears with a synthesized project summary
- [ ] Capture a new note to `Projects/Alpha/`
- [ ] Run `kms update-docs Alpha` again — doc updates to include new content
- [ ] Manually edit `Documentation/Alpha.md` (set `updated_by_human: true`)
- [ ] Run `kms update-docs Alpha` — AI does NOT overwrite human sections

---

## Phase 7 — Self-Learning

**`BLOCKED BY: Phase 2 (Classify) · WEIGHT: light`**

### Goal

After Phase 2, the system classifies notes automatically. But classification accuracy starts at "good enough" and never improves — the AI keeps making the same kinds of mistakes because it has no memory of what the user corrected.

Phase 7 closes the learning loop. Every time the user manually moves a classified note to a different folder, the system records that as a correction: "AI said Projects/Alpha, human said Projects/Beta." The next time classify runs, those corrections are fed back as examples at the start of the prompt. Over time, the system learns the user's preferences and the corrections become rarer. No model fine-tuning required — just a growing library of examples.

### How the pieces fit together

```
# Phase 7 — Self-Learning: What Happens Inside
Scope: Detects human corrections to AI classifications and feeds them back
       into future classify runs as few-shot examples.
       Does NOT cover: the classify pipeline itself (Phase 2), or model fine-tuning
       (out of scope — few-shot prompt augmentation only).

How to read this:
  Boxes      = steps the system takes, in order
  BOLD NAME  = component name — maps to the Components section below
  Arrows     = what flows to the next step
  (No forks — this is a linear learning loop)

┌──────────────────────────────────────┐
│ Human moves a classified note to     │
│ a different folder than AI chose     │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ CORRECTION DETECTOR                  │
│ Watcher sees the move, checks if     │
│ the original classification disagrees│
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ CORRECTION LOGGER                    │
│ Records: original folder, human      │
│ folder, note summary, confidence     │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ FEW-SHOT INJECTOR                    │
│ At next classify run, loads recent   │
│ corrections and prepends them to     │
│ prompts/classify.yaml as examples    │
└──────────────────────────────────────┘
```

### Components

---

**CORRECTION DETECTOR** *(new — build first; depends on Phase 2 classify pipeline being complete)*

Hook into the vault watcher to detect when a user manually moves a note that was previously classified by the AI.

- **Input:** Watcher `on_moved` event for a note inside `Projects/` or `Domain/`
- **Output:** `Success(CorrectionCandidate(note_path, ai_folder, human_folder, note_summary))` if a disagreement is detected, or `Success(None)` if the move was a pipeline move (not a human correction)
- **Rules:**
  - Look up the note in `storage/documents.py` to get the AI's original classification from its `project` field; compare with the destination folder of the move event
  - If destination matches original AI classification: this is not a correction — return `Success(None)`
  - Check `vault/move_guard.py::get_active()` registry before treating any move as a correction; if the move was registered by the pipeline, skip it (it is a system move, not a human override)
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `storage/documents.py::get_by_path`, `vault/move_guard.py::get_active`, `vault/watcher.py` (hook into existing `on_moved`)

---

**CORRECTION LOGGER** *(new — depends on CORRECTION DETECTOR)*

Write the correction to the `corrections` table with classifier-specific fields, and write an audit entry.

- **Input:** `CorrectionCandidate(note_path, ai_folder, human_folder, note_summary, confidence)`
- **Output:** `Success(correction_id)` or `Failure`
- **Rules:**
  - Add three new columns to the `corrections` table via a new migration file `storage/migrations/007_enrich_corrections.sql`: `ai_folder`, `human_folder`, and `original_confidence` (resolves TD-005)
  - Insert via the updated `corrections` table CRUD — never raw SQL INSERT in pipeline code (C-05)
  - Write a `CORRECTION_LOGGED` audit entry via `core.audit.write(...)` with `source_ids=[note_path]` (C-13)
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `storage/db.py::_connect`, `core/audit.py::write`

---

**FEW-SHOT INJECTOR** *(new — depends on CORRECTION LOGGER; wired into the classify pipeline)*

At the start of each classify run, load the most recent N corrections from the `corrections` table and inject them as few-shot examples into the classify prompt.

- **Input:** The existing classify prompt template from `prompts/classify.yaml`
- **Output:** An augmented prompt string with up to N correction examples prepended as "AI said X, human corrected to Y — use this as a guide"
- **Rules:**
  - Load at most `CONFIG.main.classify.max_correction_examples` recent corrections — never hardcode the count (C-06)
  - Format corrections as concrete examples in the prompt, not as rules or instructions; the examples show the AI what the user prefers
  - This runs inside the CLASSIFIER component from Phase 2 — it augments the prompt before the LLM call, not as a separate pipeline stage
  - The classify pipeline must still work correctly when `corrections` table is empty (zero examples → no change to prompt behavior)
- **Reuse:** `storage/db.py::_connect` (to query corrections), `llm/prompt_loader.py::PROMPTS`

---

**CORRECTIONS CLI** *(new — build last, after pipeline has passing tests)*

Expose `kms corrections` (view correction history) and `kms accuracy` (show confidence improvement over time).

- **Input:** No arguments for `kms corrections`; optional `--days N` filter; no arguments for `kms accuracy`
- **Output:** Console table of corrections and confidence trend over time
- **Rules:**
  - Wrap async calls with `asyncio.run(...)` — no async Click adapters (C-10)
  - Zero logic in the CLI layer; read from corrections table and format output
- **Reuse:** `cli/main.py` Click group, `storage/db.py::_connect`

---

### Acceptance criteria (behavior test)
- [ ] Classify a note (auto-moves to `Projects/Alpha/`)
- [ ] Manually move it to `Projects/Beta/` (human correction)
- [ ] Run `kms corrections` — shows the correction entry
- [ ] Classify a similar note — the correction influences the new classification
- [ ] `audit_log` shows confidence improvement signal

---

## Phase 8 — Daily Briefing

**`INDEPENDENT (depends on Phase 0+1 only — reads audit_log) · WEIGHT: medium`**

### Goal

After Phase 1, everything that happens in the vault is logged in the audit log. But no one reads the audit log — it is a machine record, not a human report. The user starts each day not knowing what was captured yesterday, what still needs review, or what patterns emerged across the week.

Phase 8 turns the audit log into a daily report. Each morning, the system reads everything that happened yesterday, synthesizes it into a structured briefing — what was captured, what moved where, what still needs human review, what themes emerged — and saves it as a readable note. The user opens one file and gets caught up instantly.

### How the pieces fit together

```
# Phase 8 — Daily Briefing: What Happens Inside
Scope: Reads today's audit_log entries and writes a human-readable briefing note.
       Does NOT cover: how entries got into audit_log (Phases 1–7), scheduling
       (built last, after CLI works), or MCP integration (added after CLI is verified).

How to read this:
  Boxes      = steps the system takes, in order
  BOLD NAME  = component name — maps to the Components section below
  Arrows     = what flows to the next step
  (No forks — this is a linear read → synthesize → write flow)

┌──────────────────────────────────────┐
│ Today's date (from CLI call)         │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ AUDIT READER                         │
│ Queries audit_log for today's        │
│ entries, groups by decision type     │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ BRIEFING COMPOSER                    │
│ AI synthesizes entries into a        │
│ structured daily report              │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ BRIEFING WRITER                      │
│ Saves report to                      │
│ Briefings/YYYY/MM_DD.md              │
└──────────────────────────────────────┘
```

### Components

---

**AUDIT READER** *(new — build first; BRIEFING COMPOSER depends on it)*

Query `storage/audit_log.py` for all entries within a given date range, and group them by decision type.

- **Input:** A date range (default: today's entries, from midnight to now)
- **Output:** `Success(AuditBundle(captured=[...], classified=[...], flagged=[...], errors=[...]))` or `Failure`
- **Rules:**
  - Call `storage/audit_log.py::query(start_time=..., end_time=...)` — never query SQLite directly in the pipeline (C-04)
  - Group entries by their `decision` field: `CAPTURED`, `CLASSIFIED`, `TAG_VIOLATION`, `PROMOTED`, `DOC_UPDATED`, etc.
  - Include flagged notes (those with `review=True` in frontmatter) as a separate bucket — the user needs to know what still needs their attention
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `storage/audit_log.py::query`

---

**BRIEFING COMPOSER** *(new — depends on AUDIT READER)*

Call the AI with the day's grouped audit entries and produce a structured briefing document.

- **Input:** `AuditBundle` from AUDIT READER
- **Output:** `Success(BriefingContent(body_markdown, source_note_paths))` or `Failure`
- **Rules:**
  - Load prompt from `prompts/briefing.yaml` via `PROMPTS["briefing"].render(...)` — never inline a prompt (C-07)
  - Call via `get_provider("synthesis", CONFIG.main)` — briefing is synthesis-class work, so use `synthesis_model` routing (C-08)
  - The briefing must reference source notes as Obsidian wikilinks (`[[note-title]]`) — traceability is non-negotiable
  - The briefing must contain four named sections: "What was captured", "What was classified and where", "Items needing your review", "Patterns and themes"
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `llm/provider.py::get_provider`, `llm/prompt_loader.py::PROMPTS`

---

**BRIEFING WRITER** *(new — depends on BRIEFING COMPOSER)*

Save the composed briefing to `Briefings/YYYY/MM_DD.md` and index it.

- **Input:** `BriefingContent(body_markdown)` + the target date
- **Output:** `Success(briefing_path)` or `Failure`
- **Rules:**
  - Target path is `Vault/Briefings/<YYYY>/<MM_DD>.md` — use `CONFIG.main.vault.root` to resolve the full path; never hardcode it
  - Call `vault/writer.py::write_note(path, content, metadata, actor="ai")` — never write directly to the filesystem (C-01)
  - `Briefings/` is a capture-excluded folder — the watcher skips it. BRIEFING WRITER must explicitly call `storage/documents.py::upsert(...)` to keep the briefing indexed
  - Write a `BRIEFING_WRITTEN` audit entry via `core.audit.write(...)` (C-13)
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `vault/writer.py::write_note`, `storage/documents.py::upsert`, `core/audit.py::write`

---

**BRIEFING CLI** *(new — build last, after pipeline has passing tests)*

Expose `kms briefing` that generates today's briefing on demand.

- **Input:** No arguments; optional `--date YYYY-MM-DD` to generate a briefing for a past date
- **Output:** Console confirmation of where the briefing was written
- **Rules:**
  - Wrap async calls with `asyncio.run(...)` — no async Click adapters (C-10)
  - Zero logic in the CLI layer
  - Build and verify this CLI before adding any scheduler automation (C-16)
  - Do not add `kms_briefing` as an MCP tool until this CLI is verified (C-15)
- **Reuse:** `cli/main.py` Click group, `core/pipeline.py::run_pipeline`

---

### Acceptance criteria (behavior test)
- [ ] Capture + classify several notes throughout a session
- [ ] Run `kms briefing`
- [ ] `Briefings/2026/06_DD.md` appears with:
  - Summary of what was captured today
  - Classification decisions with confidence
  - Items flagged for human review
  - Patterns across today's notes (recurring themes, contradictions)
- [ ] Briefing references source notes via wikilinks

---

## Phase 9 — Weekly Synthesis

**`INDEPENDENT (depends on Phase 0+1 only — reads audit_log) · WEIGHT: light`**

### Goal

After Phase 8, the user gets a daily briefing that summarizes yesterday. But a single day rarely tells the full story. The interesting signals emerge across a week: a theme that appeared in Monday's meeting that connected to Thursday's research note, a contradiction between two project assumptions that only shows up when you look at everything together, an action item buried in a Wednesday capture that is still unresolved by Friday.

Phase 9 surfaces those weekly patterns. Once a week, the system reads all the notes and audit entries from the past seven days, connects the dots across projects and domains, and writes a synthesis journal. Where the daily briefing says "here is what happened today," the weekly synthesis says "here is what this week meant."

### How the pieces fit together

```
# Phase 9 — Weekly Synthesis: What Happens Inside
Scope: Reads the week's audit_log entries and notes, writes a synthesis report.
       Does NOT cover: individual daily briefings (Phase 8), scheduling (built last
       after CLI works), or MCP integration (added after CLI is verified).

How to read this:
  Boxes      = steps the system takes, in order
  BOLD NAME  = component name — maps to the Components section below
  Arrows     = what flows to the next step
  (No forks — this is a linear read → synthesize → write flow)

┌──────────────────────────────────────┐
│ Week number (from CLI call)          │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ WEEK READER                          │
│ Loads this week's audit entries      │
│ and a sample of the week's notes     │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ SYNTHESIS COMPOSER                   │
│ AI connects dots across projects,    │
│ surfaces themes and contradictions   │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│ SYNTHESIS WRITER                     │
│ Saves report to                      │
│ Synthesis/YYYY/week_WW.md            │
└──────────────────────────────────────┘
```

### Components

---

**WEEK READER** *(new — build first; SYNTHESIS COMPOSER depends on it)*

Load the past seven days' audit entries and a representative sample of the week's notes.

- **Input:** ISO week number (e.g. `2026-W24`) or a date range
- **Output:** `Success(WeekBundle(audit_entries=[...], notes=[Note, ...]))` or `Failure`
- **Rules:**
  - Query `storage/audit_log.py::query(start_time=..., end_time=...)` for the week's entries (C-04)
  - Load notes via `vault/reader.py::read_note` — do not use document index summaries as source material; use full note content
  - Cap the note sample at `CONFIG.main.synthesis.max_notes_per_week` to stay within LLM context limits — never hardcode the cap (C-06); if more notes exist than the cap, prefer notes from more recent days and notes with higher audit activity (more decisions logged)
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `storage/audit_log.py::query`, `vault/reader.py::read_note`, `storage/documents.py`

---

**SYNTHESIS COMPOSER** *(new — depends on WEEK READER)*

Call the AI with the week's bundle and produce a cross-project synthesis that surfaces themes, contradictions, and action items.

- **Input:** `WeekBundle(audit_entries, notes)` from WEEK READER
- **Output:** `Success(SynthesisContent(body_markdown, source_note_paths))` or `Failure`
- **Rules:**
  - Load prompt from `prompts/synthesize_weekly.yaml` via `PROMPTS["synthesize_weekly"].render(...)` — never inline a prompt (C-07)
  - Call via `get_provider("synthesis", CONFIG.main)` — weekly synthesis is the highest-value synthesis task; use `synthesis_model` routing (C-08)
  - The synthesis must contain four named sections: "Recurring themes", "Contradictions or tensions", "Action items", "Cross-project connections"
  - Source notes must be referenced as Obsidian wikilinks (`[[note-title]]`) — traceability is non-negotiable
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `llm/provider.py::get_provider`, `llm/prompt_loader.py::PROMPTS`

---

**SYNTHESIS WRITER** *(new — depends on SYNTHESIS COMPOSER)*

Save the composed synthesis to `Synthesis/YYYY/week_WW.md` and index it.

- **Input:** `SynthesisContent(body_markdown)` + week identifier
- **Output:** `Success(synthesis_path)` or `Failure`
- **Rules:**
  - Target path is `Vault/Synthesis/<YYYY>/week_<WW>.md` — use `CONFIG.main.vault.root` to resolve; never hardcode the path
  - Call `vault/writer.py::write_note(path, content, metadata, actor="ai")` — never write directly to the filesystem (C-01)
  - `Synthesis/` is a capture-excluded folder — explicitly call `storage/documents.py::upsert(...)` after writing (watcher skips this folder)
  - Write a `SYNTHESIS_WRITTEN` audit entry via `core.audit.write(...)` (C-13)
  - Return `Success` or `Failure` — never raise (C-12)
- **Reuse:** `vault/writer.py::write_note`, `storage/documents.py::upsert`, `core/audit.py::write`

---

**SYNTHESIZE CLI** *(new — build last, after pipeline has passing tests)*

Expose `kms synthesize` that generates this week's synthesis on demand.

- **Input:** No arguments; optional `--week YYYY-WXX` to generate synthesis for a past week
- **Output:** Console confirmation of where the synthesis was written
- **Rules:**
  - Wrap async calls with `asyncio.run(...)` — no async Click adapters (C-10)
  - Zero logic in the CLI layer
  - Build and verify this CLI before adding any scheduler automation (C-16)
  - Do not add `kms_synthesize` as an MCP tool until this CLI is verified (C-15)
- **Reuse:** `cli/main.py` Click group, `core/pipeline.py::run_pipeline`

---

### Acceptance criteria (behavior test)
- [ ] Accumulate 10+ notes across multiple projects/domains over several sessions
- [ ] Run `kms synthesize`
- [ ] `Synthesis/2026/week_WW.md` appears with:
  - Recurring themes across projects
  - Contradictions or tensions surfaced
  - Action items extracted
  - Cross-project connections the user might not have noticed
- [ ] Synthesis references source notes via wikilinks

---

## Rules of the Road

- **Never skip the pipeline.** Every task goes through `/grill` → `/tdd-implement`. No shortcuts.
- **One handler, one pipeline, end-to-end before adding breadth.** Don't write 7 handlers before classify works.
- **Audit log is non-negotiable.** Phase 8 (briefing) reads from it. No audit log = no briefing.
- **Schedulers come last in each phase.** Manual CLI first, then automate (C-16).
- **MCP tools are thin wrappers.** Zero logic in `mcp_server/tools.py` (C-14). Logic belongs in a pipeline.
- **Never add an MCP tool before its pipeline exists and is tested** (C-15). A stub tool is a lie.
- **Every tag-generating pipeline MUST call `validate_tags`** from `core/tags.py` (TD-019). Violations are logged as `TAG_VIOLATION` audit entries — never silently accepted.
- **Result types everywhere.** Every public function in `handlers/` and `pipelines/` returns `Success` or `Failure` (C-12).
- **Prompts are YAML.** Never hardcode prompts in code (C-07). Use `PROMPTS["name"].render(**vars)`.
- **Thresholds are config.** Never hardcode confidence thresholds in pipeline code (C-06).
- **Work in worktrees.** Each contributor works in their own git worktree to avoid conflicts.
- **Behavior test is the review gate.** No code review. Run the acceptance criteria. If it works, it ships.

---

## Tech Debt Not Assigned to Any Phase

These items are tracked but not blocking any phase. Pay them opportunistically or in a dedicated cleanup pass post-deadline:

| TD | What | When to pay |
|----|------|-------------|
| TD-009 | `updated_by_human` sync between frontmatter and SQLite | Post-Phase 7 audit |
| TD-011 | Per-prompt model/temperature overrides | When a caller needs it |
| TD-015 | CLAUDE.md section-merge for AI co-authoring | Post-deadline |
| TD-016 | User explicit URL flagging in `enrich_urls` | Wishlist — no demand |
| TD-017 | AI URL triage replacing structural heuristic | Adds latency — Phase 2+ |
| TD-018 | Domain list refresh in `kms watch` | Post-deadline |
| TD-020 | Research doc describes OLD attachment layout | Documentation debt |
| TD-021 | Roadmap describes OLD attachment layout | Fixing now |
| TD-029 | Rename gate logic mis-calibrated | Consider during Phase 2 `/grill` |
| TD-031 | `move_attachment` TOCTOU window | Watcher hardening pass |
| TD-032 | No `kms migrate-attachments` for legacy layout | Only if needed |
| TD-033 | Watcher monkeypatch target documentation | Documentation-only |
| TD-035 | Reconcile: location-tag mismatch on human override | Post-Phase 2 |
| TD-036 | Reconcile: stale `batch_id` after file moves | Low risk, post-MVP |
| TD-039 | Windows support for binary content-change detection | Post-deadline, Mac-first |
