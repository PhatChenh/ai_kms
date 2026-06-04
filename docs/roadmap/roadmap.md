---
created: 2026-04-26
updated: 2026-06-04
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

AI reads a captured note's summary + tags → decides which `Projects/<A>/` or `Domain/<D>/` subfolder it belongs in → moves it there. Confidence gates control automation level.

### What it does
- `pipelines/classify.py` — classify → confidence-gate → route → move (four pure stages)
- `prompts/classify.yaml` — classification prompt
- Confidence gates from `config/thresholds.yaml`: ≥ auto-move | mid-range → suggest + flag | low → flag as clueless
- Every decision → `audit_log` with timestamp, source note, decision, confidence, reasoning
- CLI: `kms classify <file>` + `kms classify --scan` (batch)

### Tech debt to resolve DURING this phase
- **TD-019** — Wire `validate_tags` in classify pipeline (shared infra from Phase 1, just needs wiring)
- **TD-034** — Project-to-domain mapping registry. Classify needs to know which domain a project belongs to. Design a `projects` DB table or `Projects/<A>/meta.yaml`. Without this, domain defaults to `Uncategorized`
- **TD-012** (partial) — Add `kms classify` CLI command (currently a stub)

### Tech debt to consider (not blocking)
- **TD-029** — Rename gate logic. Current rename heuristic is mis-calibrated. Can ship classify without it — rename is a capture concern, not classify. But classify could trigger a rename-suggestion. Decide during `/grill`
- **TD-038** — Domain scalar already deprecated in Pre-2. Classify should use `domain/<D>` tags only, never the scalar. Verify no consumer reads the scalar

### Open questions to resolve before starting
- None blocking. OQ-002 (per-section authorship) is Phase 7+

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

Make the vault queryable by meaning. Combines keyword search (FTS5) with semantic search (embeddings). Three tiers control cost: summary-only (hot), snippet (warm), full content (cold).

### What it does
- `storage/embeddings.py` — write/read vectors using `sentence-transformers` (`all-MiniLM-L6-v2`)
- `retrieval/keyword.py` — FTS5 full-text search
- `retrieval/semantic.py` — embedding cosine similarity
- `retrieval/hybrid.py` — merge + re-rank both result sets
- `retrieval/tiers.py` — dispatcher: hot (summary field) → warm (matching snippet) → cold (full content)
- CLI: `kms search "<query>"` — starts at hot tier, escalates on demand
- Index built from documents table + vault scan; incremental updates on each capture

### Tech debt to resolve DURING this phase
- **TD-004** — Create `embeddings` table + FTS5 virtual table (SQL migrations)
- **TD-013** — Route `embedding_model` config field to `sentence-transformers`
- **TD-012** (partial) — Add `kms search` CLI command (currently a stub)

### Tech debt to consider (not blocking)
- **TD-010** — Ollama httpx async rewrite. Only relevant if Ollama becomes the embedding provider. Skip unless needed

### Open questions to resolve before starting
- None blocking

### Tier rule
Callers never decide the tier — they ask for a query and a `max_cost`. `tiers.py` decides where to start and whether to escalate.

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

Thin wrapper over existing pipelines. The boss uses Claude Desktop to talk to the vault. Zero logic in `mcp_server/tools.py`.

### What it does
- `mcp_server/server.py` — MCP entrypoint, stdio transport (Claude Desktop compatible)
- `mcp_server/tools.py` — expose exactly three tools:
    - `kms_search(query, tier)` → calls `retrieval/tiers.py`
    - `kms_capture(content, source_type)` → calls `pipelines/capture.py`
    - `kms_classify(note_path)` → calls `pipelines/classify.py`
- `mcp_server/transport.py` — stdio first; HTTP transport deferred

### Tech debt to resolve DURING this phase
- **TD-007 / OQ-003** — `wal_autocheckpoint` tuning. MCP is a long-running daemon; unchecked WAL growth causes read latency. Set `wal_autocheckpoint=100` in `_connect()`
- **TD-012** (partial) — Any remaining CLI stubs

### Open questions to resolve BEFORE starting
- **OQ-004** — Concurrent `run_pipeline` contextvar bleed. `clear_contextvars()` in `new_correlation_id()` wipes concurrent calls. Fix: per-run `copy_context().run(...)`. MUST resolve before MCP ships concurrent tool handling

### Scope discipline
Do NOT add `kms_promote`, `kms_synthesize`, `kms_documentation_update`, or `kms_briefing` until those pipelines exist and are tested (C-15). An MCP tool that calls a stub is worse than no tool.

Later phases add their own MCP tools when their pipeline is ready.

### Actor rule for external AI (Claude Cowork, Claude Desktop)
Edits through our MCP tools → go through `write_note(..., actor="ai")` → tracked, AI-owned. Edits outside our pipeline (direct filesystem writes by Claude Cowork, human, or any other tool) → watcher treats as human edit → `updated_by_human = true`. The distinction is **pipeline vs not-pipeline**, not human vs AI. This means Claude Cowork MUST use MCP tools to make tracked edits — direct filesystem access is treated as human.

### Acceptance criteria (behavior test)
- [ ] Configure Claude Desktop to use the MCP server
- [ ] Ask Claude: "What do I know about Q3 marketing?" — returns relevant notes
- [ ] Ask Claude: "Capture this: <paste text>" — creates a new note in inbox
- [ ] Ask Claude: "Classify the notes in my inbox" — notes move to correct folders
- [ ] All three tools return structured results, not errors

---

## Phase 5 — Note Promotion

**`INDEPENDENT (depends on Phase 0+1 only) · WEIGHT: medium`**

Extract structured knowledge from raw captures — research notes, workflow templates, lessons learned. Turns ore into refined metal.

### What it does
- `pipelines/promotion.py` — detect promotable notes → extract structure → write to `Domain/<D>/`
- `prompts/promote.yaml` — promotion prompt
- Note types: research note | lesson learned | workflow template
- Confidence gate: < threshold → proposal goes to human review, never auto-promotes without high confidence
- Every promotion → `audit_log`
- CLI: `kms promote <file>` or `kms promote --scan`
- When pipeline is tested, add `kms_promote` tool to `mcp_server/tools.py`

### Tech debt: none blocking

### Open questions: none blocking

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

One living page per active project in `Documentation/`. AI proposes updates based on new captures. Human approves.

### What it does
- `pipelines/documentation.py` — read project materials → diff against current doc → propose update
- `prompts/documentation_update.yaml` — update prompt
- **Hard rule:** never overwrite a field where `updated_by_human = true`. Propose only; write only on explicit approval
- Conflict resolution: newest `updated_at` wins unless `updated_by_human = true` (human always wins)
- Update cycle: triggered on new capture to a project folder (event-driven, not timer)
- Output → `Vault/Documentation/<project>.md` (capture-excluded folder — watcher skips it)
- CLI: `kms update-docs <project>` or `kms update-docs --all`
- When pipeline is tested, add `kms_documentation_update` tool to `mcp_server/tools.py`

### Tech debt: none blocking

### Open questions to be aware of (not blocking start)
- **OQ-008** — Detecting human edits to capture-excluded AI-output folders. Documentation/ is capture-excluded, so human edits there won't trigger `updated_by_human`. Decide during `/grill` whether to add a lightweight edit-detection path for these folders

### Co-authoring: deferred past 17 June
Per-section AI/human merge (TD-006, OQ-002) is NOT in scope. Ship with the blunt whole-note gate: if human (or external AI via filesystem) edits this page, `updated_by_human = true` and AI stops updating it. V2 enhancement when real user feedback exists on what they actually want to edit. See Phase 4 "Actor rule" for how external AI edits are classified.

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

Track human corrections to AI classifications. Feed them back as few-shot examples to improve future accuracy. No model fine-tuning — just prompt augmentation.

### What it does
- Hook: detect when user manually moves a classified note → log correction with original classification + confidence + human-chosen destination
- `corrections` table enrichment (schema exists from Phase 0, fields need extending)
- Feed most recent N corrections as few-shot examples into `prompts/classify.yaml` at load time
- Accuracy metric: track classification confidence pre/post corrections in `audit_log`
- CLI: `kms corrections` (view correction history), `kms accuracy` (show improvement metrics)

### Tech debt to resolve DURING this phase
- **TD-005** — Enrich `corrections` table with classifier-specific fields (feedback type, correction delta, confidence)

### Open questions to be aware of
- **OQ-002** — Fine-grained AI vs human authorship. Not blocking for Phase 7 (whole-note gate is sufficient for correction detection), but inform the `/grill` discussion

### Why this is blocked on Phase 2
Self-learning hooks into classify corrections. Without a working classify pipeline, there's nothing to correct. The watcher needs to compare "AI classified to X" vs "human moved to Y" — both sides need to exist.

### Acceptance criteria (behavior test)
- [ ] Classify a note (auto-moves to `Projects/Alpha/`)
- [ ] Manually move it to `Projects/Beta/` (human correction)
- [ ] Run `kms corrections` — shows the correction entry
- [ ] Classify a similar note — the correction influences the new classification
- [ ] `audit_log` shows confidence improvement signal

---

## Phase 8 — Daily Briefing

**`INDEPENDENT (depends on Phase 0+1 only — reads audit_log) · WEIGHT: medium`**

Read-only consumer of audit_log. Generates a daily report: what got captured, what got classified, what needs human review, what patterns emerged.

### What it does
- `briefings/daily.py` — compose the full daily report from audit_log entries
- `briefings/classification_report.py` — what got moved where today, what needs review
- `prompts/briefing.yaml` — briefing prompt
- Output → `Vault/Briefings/YYYY/MM_DD.md` (capture-excluded folder)
- `scheduler/runner.py` + `jobs.yaml` — daily trigger at configured time (scheduler comes LAST — C-16)
- CLI: `kms briefing` (generate today's briefing manually)
- When pipeline is tested, add `kms_briefing` tool to `mcp_server/tools.py`

### Tech debt: none blocking

### Open questions: none blocking

### Co-authoring: deferred past 17 June
Same rule as Phase 6. Blunt whole-note gate. See Phase 6 "Co-authoring" note and Phase 4 "Actor rule".

### Note on scheduler
Build and verify `kms briefing` CLI first. Only add scheduler automation after CLI is verified (C-16).

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

Connect dots across the week's notes. Surface recurring themes, contradictions, and action items. Similar pattern to Daily Briefing but broader scope.

### What it does
- `pipelines/synthesis_weekly.py` — read week's audit_log + notes → synthesize
- `prompts/synthesize_weekly.yaml` — synthesis prompt
- Output → `Vault/Synthesis/YYYY/week_WW.md` (capture-excluded folder)
- Scheduler: weekly trigger via `jobs.yaml` (scheduler comes LAST — C-16)
- CLI: `kms synthesize` (generate this week's synthesis manually)
- When pipeline is tested, add `kms_synthesize` tool to `mcp_server/tools.py`

### Tech debt: none blocking

### Open questions: none blocking

### Co-authoring: deferred past 17 June
Same rule as Phase 6. Blunt whole-note gate. See Phase 6 "Co-authoring" note and Phase 4 "Actor rule".

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
