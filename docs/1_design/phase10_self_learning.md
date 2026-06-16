# Phase 10 — Self-Learning & Reports Backend: Design

_Created: 2026-06-15_
_Status: DESIGN — produced by /codebase-design-analysis_
_Input: Phase 10 grill (docs/0_draft/phase10/phase10_self_learning_grill.md)_
_Audience: Next AI session running /writing-detailed-specs_
_Behavior ID prefix: **P10-SL** (Self-Learning)_

---

## Summary

Phase 10 closes the feedback loop between the user and the knowledge system. Today, the AI extracts facts and the user reads them — but has no way to say "this is wrong," "this is right," or "here is what I actually know." Phase 10 adds four backend capabilities: a corrections system that records user feedback and adjusts trust scores, trust mechanics that protect validated facts from being overwritten by the classify pipeline, a few-shot injector that teaches the AI from its past mistakes, and a reports system that synthesizes knowledge health summaries on demand. No frontend — the consumer AI (Claude Desktop) drives everything through MCP tools.

## Glossary

| Term | Plain English | Technical detail |
|---|---|---|
| **fact correction** | A user saying "this fact is right," "this fact is wrong," or "here is the fixed version." | A row in `fact_corrections` recording the operation (confirm/reject/revise), the old and new fact text, a reason category, and optional user feedback. One correction per row. |
| **trust score** | How much the user has validated a fact, independent of the AI's own confidence. | A float on `knowledge_entries.trust_score`. Starts at 0.5 (neutral). Rises with confirms (+0.05), falls with rejects (-0.10). Revise sets the new entry to 0.6. Deltas are in config, never code (C-06). |
| **overwrite guard** | The rule that protects user-validated facts from being silently replaced by the classify pipeline. | `_should_overwrite()` in `classify_writer.py` (P9 placed the seam, returns `True`). P10 activates it: when `trust_score > threshold` (default 0.5 from config), the classify pipeline writes a new pending entry instead of overwriting the existing one. |
| **reason category** | Whether the AI misread the document or the source document was wrong. | `ai_error` = the AI extracted incorrectly (fed to few-shot learning). `stale_source` = the source was outdated (NOT fed to few-shot — would teach the AI to ignore documents). |
| **few-shot injector** | Past AI mistakes fed back into the extraction prompt as teaching examples. | A selector reads `ai_error` corrections from `fact_corrections`, picks the most relevant ones (dimension match, entity overlap, recency), formats them as bad/good pairs, and injects them into the `entity_extract.yaml` prompt via a new Jinja variable. |
| **volatility flag** | A warning that a fact has been corrected many times, signaling it may be contested. | When an entry has more than 3 corrections (config-driven), MCP context injection appends `[frequently corrected]` to the fact bullet. P9-dependent (needs P9 context engine). |
| **report** | A synthesized summary of knowledge health, corrections, or coverage. | A row in the `reports` table. Generated on demand by the housekeeping AI using a YAML-configured filter + prompt. Stored for history; latest returned to the consumer AI. |
| **entry comment** | A human note attached to a knowledge entry. | A row in `entry_comments`. Additive only — no edit, no delete. Fed to the classify context loader so the AI considers human annotations during future extraction. |

---

## Current State

### What exists that Phase 10 builds on

**knowledge_entries table** (Phase 8, migration 008 + 010):
- `trust_score REAL DEFAULT 0.5` — inert column, shipped in migration 010. Currently unused — every entry sits at 0.5.
- `retrieval_count INTEGER DEFAULT 0` — P9 repurposes as `retrieval_score` (decaying float). Column stays, Python name changes.
- Status lifecycle: confident / pending / retired. `retire()` currently removes from search indexes (BUG — see below).

**classify_writer.py** (Phase 8 Slice B):
- `_should_overwrite()` decision point at line 49. Returns `True` unconditionally. Called in both the update path (line 212) and the twin-fold path (line 277). The seam is correctly placed — P10 activates it.
- `write_entries()` routes facts by action: new, update, retire. Each path calls `ke_upsert()` or `ke_retire()`.

**_correct.py** (Phase 9):
- `correct_entry()` supports: `edit_fact`, `change_tag`, `change_entity`, `promote`, `un_retire`, `retire`. Audit-logged. Returns `Result[dict]`.
- Does NOT record the correction in a corrections table (no table exists). Does NOT adjust trust. Does NOT categorize the reason.

**context.py** (Phase 9):
- `ContextInjectionEngine` assembles orientation facts from `knowledge_entries`, ranked by trust/retrieval/confidence/recency. Budget-capped per dimension. Identity-dedup per conversation.
- No trust-floor filtering (all non-retired entries are eligible). No volatility annotation.

**entity_extract.yaml** (Phase 8):
- Prompt has a `previous_attempt_feedback` variable for self-correction retry. No few-shot examples variable.

### Bug: `retire()` purges search indexes

`knowledge_entries.py:retire()` (line 246-281) removes the retired entry from `facts_fts` and `facts_vec`. This makes retired entries invisible to search — but per the rearchitecture doc section 7, retired entries should remain searchable (a user asking "what did we used to think about X?" should find them). They should only be excluded from context injection (which already filters `WHERE status != 'retired'`).

**Fix:** Remove the search-index cleanup from `retire()`. Search queries that should exclude retired entries already do so via the `WHERE status != 'retired'` clause in the join step of `fact_search.py:_join_entries()`.

---

## Design Decisions

### D1. `kms_comment` as a separate MCP tool (not an operation on `kms_correct`)

**Decision:** Separate `kms_comment` tool.

**Rationale:**
- `kms_correct` changes facts (mutative). `kms_comment` adds context (additive). Mixing them conflates "fix this" with "note this."
- C-14 (tools.py is logic-free) means the tool shim calls a backing function. A comment operation buried inside `correct_entry()` would need branching logic to skip trust adjustment, skip retirement, skip the correction record — a violation of the function's single responsibility.
- The consumer AI's behavioral stance differs: corrections require source checking first (AI_INSTRUCTIONS.md); comments are lightweight annotation.
- Separate tools let the AI_INSTRUCTIONS.md give distinct behavioral guidance for each.

**Rejected alternative:** Comment as a `kms_correct` operation. Simpler tool surface (one less tool to learn), but forces branching in the backing function and confuses the correction audit trail with annotation noise.

### D2. Few-shot injection cap: 5 examples per prompt

**Decision:** Default 5, config-driven via `self_learning.max_corrections_per_prompt`.

**Rationale:**
- Each few-shot example is approximately 3 lines (bad/good/reason), roughly 40-60 tokens. 5 examples = 200-300 tokens — well under the entity_extract prompt's token budget.
- 10 examples (600 tokens) risks crowding the existing_facts section when dimensions have many entries. 5 is conservative; the config knob lets users increase if extraction accuracy warrants it.
- Relevance selection (dimension match > entity overlap > recency) means even 5 well-chosen examples carry more signal than 10 random ones.

### D3. Conflict detection: structural heuristic, not semantic similarity

**Decision:** The "conflicts" report type detects contradictions using a structural heuristic — multiple active (non-retired) entries for the same entity + dimension + tag where at least one was blocked by the overwrite guard.

**Rationale:**
- Semantic similarity between short one-line facts is unreliable (Phase 9 research spike showed separation issues with short texts). Building a contradiction detector on top of unreliable similarity would produce false positives.
- The overwrite guard naturally surfaces conflicts: when trust > threshold blocks an overwrite, both the old (trusted) and new (pending) entries survive. Querying for this pattern is a simple SQL scan — no AI call, no embedding.
- Coverage: this catches the most important conflicts (user-validated fact vs. new AI extraction). It misses subtler contradictions (two AI-extracted facts that quietly disagree), but those are better handled by a future entity resolution phase, not a heuristic overlay.

**Rejected alternative:** Semantic similarity between facts for the same entity. Would catch more subtle contradictions but at the cost of high false positives and an embedding comparison per entity. Deferred to a future entity resolution phase.

### D4. Comment visibility in `kms_inspect`: yes, appended to entry detail

**Decision:** When `kms_inspect` returns a knowledge entry (through the summary or text tier), comments on that entry are appended as a "Comments" section.

**Rationale:**
- Comments exist to provide human context. If the consuming AI cannot see them when inspecting an entry, they serve no purpose in the MCP workflow.
- Comments are short and additive. Appending them does not risk blowing the response size.
- Implementation is a lightweight join: `SELECT * FROM entry_comments WHERE entry_id = ? ORDER BY created_at`.

### D5. Report synthesis reuses P9 context assembly for data gathering, not for the synthesis prompt itself

**Decision:** The report pipeline reuses P9's `context_loader` + `query_ranked_for_orientation` patterns to gather filtered data (which entries, which documents). The synthesis prompt is report-specific (from `config/reports.yaml`), not the extraction prompt.

**Rationale:**
- Report prompts vary wildly ("summarize project status" vs. "list coverage gaps" vs. "find conflicts"). They cannot share a single prompt template.
- But data gathering is the same problem: "give me relevant, ranked knowledge entries filtered by dimension/entity/status." That logic already exists in `context_loader` (dimension-ranked, capped facts) and `query_ranked_for_orientation` (4-key sort). Reuse avoids duplication.
- The synthesis prompt goes through `get_provider("synthesis")` — a different task/model than classify (C-08, C-09).

---

## Component Analysis

### Component 1: Corrections System

**What it does:** Records every correction in `fact_corrections`, applies the correction to `knowledge_entries`, adjusts trust, and writes an audit entry.

**New table: `fact_corrections`** (migration 013)

| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `entry_id` | INTEGER NOT NULL | FK → knowledge_entries(id) |
| `operation` | TEXT NOT NULL | confirm, reject, revise |
| `reason_category` | TEXT | ai_error, stale_source |
| `feedback` | TEXT | Optional user explanation |
| `old_fact` | TEXT | Snapshot before correction |
| `new_fact` | TEXT | Revised text (NULL for confirm/reject) |
| `old_trust_score` | REAL | Trust before adjustment |
| `new_trust_score` | REAL | Trust after adjustment |
| `created_at` | TEXT DEFAULT datetime('now') | When recorded |

**FK cascade:** `ON DELETE CASCADE` — if a knowledge entry is deleted (not retired, which is a status change), its corrections are cleaned up. `PRAGMA foreign_keys=ON` is already enforced (C-04).

**Changes to `_correct.py`:**
- `correct_entry()` gains two new parameters: `reason_category` (optional, default `None`), `feedback` (optional, default `None`).
- The function's internal operations are renamed to match the grill's terminology: `confirm` (was `promote`), `reject` (was `retire`), `revise` (was `edit_fact`). The old P9 operation names (`promote`, `retire`, `edit_fact`, `change_tag`, `change_entity`, `un_retire`) remain as aliases for backward compatibility — they call the same backing logic.
- After applying the operation to `knowledge_entries`, the function:
  1. Calls `adjust_trust()` to compute the new trust score.
  2. Writes the new trust score to the entry.
  3. Inserts a row into `fact_corrections`.
  4. Writes audit (already exists).
- For `revise`: retires the old entry, creates a new entry at trust 0.6 with the new fact text, then records the correction pointing at the OLD entry_id (the one that was revised).

**Changes to `tools.py`:**
- `kms_correct` gains `reason_category` and `feedback` parameters. Passes them through to `correct_entry()`.

### Component 2: Trust Mechanics

**Pure function: `adjust_trust()`**

Located in a new module `src/pipelines/trust.py` (not in `core/` — trust adjustment is a pipeline-stage concern, not a shared primitive).

```
adjust_trust(current_score: float, operation: str, config: SelfLearningConfig) -> float
```

- `confirm`: `min(1.0, current + config.trust_confirm_delta)` — default +0.05
- `reject`: `max(0.0, current + config.trust_reject_delta)` — default -0.10
- `revise`: returns `config.trust_revise_base` — default 0.6 (applied to the NEW entry)
- Unknown operation: returns `current` unchanged (defensive).

Deltas live in config (C-06):
```yaml
self_learning:
  trust_confirm_delta: 0.05
  trust_reject_delta: -0.10
  trust_revise_base: 0.6
  overwrite_trust_threshold: 0.5
  min_trust_for_context: 0.3
  volatility_correction_count: 3
  max_corrections_per_prompt: 5
```

**Overwrite guard activation:**

`_should_overwrite()` in `classify_writer.py` changes from:
```python
def _should_overwrite(existing_entry) -> bool:
    return True
```
to:
```python
def _should_overwrite(existing_entry, *, threshold: float) -> bool:
    return existing_entry.trust_score <= threshold
```

The `threshold` parameter is read from `CONFIG.main.self_learning.overwrite_trust_threshold` at the call site in `write_entries()`. When `_should_overwrite` returns `False`, the writer:
1. Skips the update/fold.
2. Inserts the new fact as a fresh `pending` entry (same dimension, entity, tag) — preserving both the trusted fact and the contradictory new extraction.
3. Logs a structured warning: `overwrite_blocked entry_id=X trust=Y threshold=Z`.

This creates the conflict pattern that the "conflicts" report type detects.

**Trust-floor filtering (P9-dependent):**

`context.py` orientation builders add `AND trust_score >= ?` to their queries. The threshold comes from `CONFIG.main.self_learning.min_trust_for_context`. Entries below this floor are excluded from MCP context blocks but remain searchable and visible in `kms_inspect`.

**Volatility flag (P9-dependent):**

When building orientation fact bullets, the context engine queries `SELECT COUNT(*) FROM fact_corrections WHERE entry_id = ?`. If count exceeds `CONFIG.main.self_learning.volatility_correction_count`, the fact bullet gets `[frequently corrected]` appended.

### Component 3: Few-Shot Injector

**New module: `src/pipelines/few_shot.py`**

```
select_corrections(
    dimension: str,
    doc_entities: list[str],
    *,
    cap: int,
    db_path: Path | None = None,
) -> Result[list[dict]]
```

Selection algorithm:
1. Query `fact_corrections` WHERE `reason_category = 'ai_error'`, joined with `knowledge_entries` for dimension/entity.
2. Score each correction:
   - +3 if dimension matches the current extraction dimension
   - +2 if the corrected entry's entity appears in `doc_entities` (entities mentioned in the document being classified)
   - +1 base for recency (ORDER BY `created_at DESC`, position penalty)
3. Sort by score descending, take top `cap`.
4. Return list of dicts: `{old_fact, new_fact, feedback, dimension, entity}`.

**Format function:**

```
format_few_shot(corrections: list[dict]) -> str
```

Produces a block of text suitable for Jinja injection:
```
Previous extraction mistakes to avoid:
- For [entity] in [dimension]: The AI incorrectly extracted "[old_fact]". The correct fact is "[new_fact]". [feedback if present]
```

**Injection into entity_extract.yaml:**

Add a new Jinja variable `few_shot_corrections` to the prompt:
```yaml
user: |
  {% if few_shot_corrections %}
  {{ few_shot_corrections }}
  {% endif %}

  Knowledge category (dimension):
  {{ dimension_guidance }}
  ...
variables: [document_text, dimension_guidance, existing_facts, previous_attempt_feedback, few_shot_corrections]
```

**Wiring in classify_orchestrator.py:**

Before calling `extract()`, the orchestrator calls `select_corrections()` and `format_few_shot()`, then passes the formatted string as a new `few_shot_corrections` keyword argument to `extract()`, which passes it to the prompt renderer.

### Component 4: Reports System

**New table: `reports`** (migration 013, same file as `fact_corrections`)

| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `report_type` | TEXT NOT NULL | Key from reports.yaml |
| `title` | TEXT NOT NULL | Human-readable title |
| `body` | TEXT NOT NULL | Synthesized report text |
| `prompt_used` | TEXT NOT NULL | Exact prompt sent to LLM |
| `filters_used` | TEXT (JSON) | Snapshot of filters |
| `sources_used` | TEXT (JSON) | Entry/doc IDs fed to synthesis |
| `created_at` | TEXT DEFAULT datetime('now') | When generated |

**New config file: `config/reports.yaml`**

Defines report types. Each report has:
- `title`: human-readable name
- `filters`: deterministic SQL constraints (dimensions, entities, status, tags)
- `prompt`: what the AI does with the filtered data (loaded via PROMPTS pattern)
- `sources`: which corpora to read — `facts`, `summaries`, `corrections`

Default report types ship in the config:
1. **correction_summary** — correction counts, most-corrected entities, accuracy trend
2. **knowledge_health** — total facts by dimension, pending count, coverage metrics
3. **volatile_entries** — entries with correction count above volatility threshold
4. **coverage_gaps** — documents with zero knowledge entries extracted
5. **conflicts** — entries where overwrite guard blocked, creating two active entries for same entity+tag

**New module: `src/pipelines/reports.py`**

```
synthesize_report(
    report_type: str,
    *,
    config: MainConfig,
    db_path: Path | None = None,
) -> Result[dict]
```

Steps:
1. Load report definition from `config/reports.yaml`.
2. Gather data per `sources` config: query `knowledge_entries` and/or `documents` and/or `fact_corrections` using the filter constraints.
3. Format gathered data as context text.
4. Render the report's prompt template with the context.
5. Call `get_provider("synthesis", config)` for the LLM synthesis.
6. Store the result in the `reports` table.
7. Return `Success({"report_id": id, "title": ..., "body": ...})`.

**New MCP tool: `kms_reports`** (P9-dependent — needs MCP tool registration pattern)

```python
def kms_reports(
    report_type: str,
    ctx: Context = None,
) -> dict:
    """Generate or retrieve a knowledge report."""
    from pipelines.reports import synthesize_report
    return synthesize_report(report_type, config=...).unwrap()
```

### Component 5: Comments

**New table: `entry_comments`** (migration 013)

| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `entry_id` | INTEGER NOT NULL | FK → knowledge_entries(id) ON DELETE CASCADE |
| `comment_text` | TEXT NOT NULL | The comment body |
| `created_at` | TEXT DEFAULT datetime('now') | When added |

**New module: `src/mcp_server/_comment.py`**

```
add_comment(entry_id: int, text: str, *, db_path: Path | None = None) -> Result[dict]
```

Validates entry exists, inserts row, returns `Success({"comment_id": ..., "entry_id": ...})`.

**New MCP tool: `kms_comment`**

```python
def kms_comment(
    entry_id: int,
    text: str,
    ctx: Context = None,
) -> dict:
    """Add a comment to a knowledge entry."""
    from mcp_server._comment import add_comment
    return add_comment(entry_id=entry_id, text=text).unwrap()
```

**Context loader integration:**

`classify.py:context_loader()` is augmented: after loading ranked facts per dimension, it also loads comments for entities present in those facts. Comments are appended to the fact's representation in the prompt context so the AI sees human annotations during extraction.

**kms_inspect integration:**

When `kms_inspect` returns entry details, comments for that entry are appended as a "Comments" section.

### Component 6: Retire Search Fix (Bug Fix)

**File:** `src/storage/knowledge_entries.py`

**Change:** Remove the `facts_fts` DELETE and `facts_vec` DELETE from `retire()`. The function should only update the status to 'retired' and set the reasoning — search index entries remain so retired facts are still findable.

**Verification:** `fact_search.py:_join_entries()` already has `AND status != 'retired'` in its WHERE clause, which filters retired entries from search results. Context injection queries also have this filter. Search consumers that WANT retired entries (e.g., a future "history" mode) can query without the filter.

---

## Options Grid

### Option A: Monolithic Phase (all components in one sequence)

Build all six components in strict dependency order. One branch, one merge.

**Pros:** Simple coordination. One set of migrations.
**Cons:** Long phase (~2 weeks). Blocks P9-dependent work until P9 ships. Cannot ship P9-independent improvements early.

### Option B: Two-Slice Split (P9-independent first, P9-dependent second) — CHOSEN

**Slice 1 (P9-independent, can start now):**
- Migration 013 (fact_corrections, reports, entry_comments tables)
- `adjust_trust()` pure function + config additions
- `_should_overwrite()` activation in classify_writer.py
- Few-shot injector (select + format + prompt wiring)
- `retire()` search fix (bug fix)
- Comments table + `_comment.py` + `kms_comment` tool
- P9 operation rename aliases in `_correct.py` (confirm/reject/revise)

**Slice 2 (P9-dependent, starts after P9 ships):**
- Report synthesis pipeline + `kms_reports` tool
- Trust-floor filtering in context engine
- Volatility flag in context engine
- Comment visibility in `kms_inspect`
- Correction recording wired into `correct_entry()`
- Full `kms_correct` extension with reason_category + feedback

**Pros:** ~60% of the work starts immediately. Trust protection (overwrite guard) and few-shot learning ship without waiting for P9. Bug fix ships now.
**Cons:** Two merges. Slice 2 needs careful integration testing with P9's rewritten context engine.

### Option C: Component-Per-Branch (6 parallel branches)

Each component is its own branch, merged independently.

**Pros:** Maximum parallelism. Smallest possible merge conflicts.
**Cons:** Migration coordination nightmare (6 branches touching the same migration file). Integration testing is deferred to the end. Overkill for a single developer.

**CHOSEN: Option B (Two-Slice Split).** This is the standard pattern in this project (Phase 7 and Phase 8 both used A/B slicing). It maximizes early delivery while respecting P9 dependencies.

---

## Migration Design (013)

All three new tables are in a single migration file: `src/storage/migrations/013_self_learning_tables.sql`.

```sql
CREATE TABLE IF NOT EXISTS fact_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    reason_category TEXT,
    feedback TEXT,
    old_fact TEXT,
    new_fact TEXT,
    old_trust_score REAL,
    new_trust_score REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    prompt_used TEXT NOT NULL,
    filters_used TEXT,
    sources_used TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entry_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
    comment_text TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_fc_entry ON fact_corrections(entry_id);
CREATE INDEX idx_fc_reason ON fact_corrections(reason_category);
CREATE INDEX idx_ec_entry ON entry_comments(entry_id);
CREATE INDEX idx_reports_type ON reports(report_type, created_at DESC);

UPDATE schema_version SET version = 13;
```

---

## Config Additions

**`config/config.yaml` — self_learning section expansion:**

```yaml
self_learning:
  enabled: true
  min_evaluations: 20
  confidence_threshold: 0.8
  include_examples_in_prompt: true
  max_examples: 5
  # Phase 10 additions:
  trust_confirm_delta: 0.05
  trust_reject_delta: -0.10
  trust_revise_base: 0.6
  overwrite_trust_threshold: 0.5
  min_trust_for_context: 0.3
  volatility_correction_count: 3
  max_corrections_per_prompt: 5
```

**New file: `config/reports.yaml`** — report type definitions (see Component 4).

---

## Files Touched

### New files
| File | Purpose |
|---|---|
| `src/pipelines/trust.py` | `adjust_trust()` pure function |
| `src/pipelines/few_shot.py` | Correction selector + formatter |
| `src/pipelines/reports.py` | Report synthesis pipeline |
| `src/mcp_server/_comment.py` | Comment backing function |
| `src/storage/migrations/013_self_learning_tables.sql` | Three new tables + indexes |
| `config/reports.yaml` | Report type definitions |

### Modified files
| File | Change |
|---|---|
| `src/storage/knowledge_entries.py` | Remove search-index cleanup from `retire()` |
| `src/pipelines/classify_writer.py` | Activate `_should_overwrite()` with trust threshold |
| `src/pipelines/classify_orchestrator.py` | Wire few-shot injector before `extract()` calls |
| `src/pipelines/classify_extract.py` | Add `few_shot_corrections` parameter to `extract()` |
| `src/mcp_server/_correct.py` | Add reason_category, feedback, trust adjustment, correction recording |
| `src/mcp_server/tools.py` | Add `kms_comment`, `kms_reports` tools; extend `kms_correct` parameters |
| `src/mcp_server/context.py` | Trust-floor filter, volatility flag (Slice 2) |
| `src/core/config.py` | Extend `SelfLearningConfig` with trust/few-shot fields |
| `config/config.yaml` | Add self_learning trust/few-shot values |
| `src/prompts/entity_extract.yaml` | Add `few_shot_corrections` variable |
| `src/mcp_server/AI_INSTRUCTIONS.md` | Document new tools and correction workflow |

---

## Constraint Compliance

| Constraint | How Phase 10 complies |
|---|---|
| C-01 (DB is source of truth) | All new data in DB tables. No vault writes. |
| C-04 (FK pragma) | `fact_corrections` and `entry_comments` use FK → knowledge_entries with ON DELETE CASCADE. Existing `PRAGMA foreign_keys=ON` enforces. |
| C-05 (Versioned migrations) | Single migration 013. No in-code schema changes. |
| C-06 (Config thresholds) | All trust deltas, caps, and thresholds in `config.yaml`. No floats in pipeline code. |
| C-07 (Prompts in YAML) | Report prompts in `config/reports.yaml`. Few-shot injection via YAML variable. |
| C-08 (Provider factory) | Report synthesis uses `get_provider("synthesis", config)`. |
| C-12 (Result returns) | All new public functions return `Result[T]`. |
| C-13 (Audit log) | Corrections audit-logged (existing). Report synthesis audit-logged (new). |
| C-14 (tools.py logic-free) | New tools are one-expression delegates. |
| C-15 (Pipeline before tool) | `kms_reports` added only after `reports.py` pipeline exists. `kms_comment` added only after `_comment.py` backing exists. |

---

## P9 Dependency Map

| P10 work item | P9 component needed | Can start before P9? |
|---|---|---|
| Migration 013 (3 tables) | None | YES |
| `adjust_trust()` | None | YES |
| `_should_overwrite()` activation | Seam exists (P9-F-01) | YES |
| Correction recording | `correct_entry()` exists (P9-A-02) | YES (extend it) |
| Few-shot injector | entity_extract.yaml exists (P8) | YES |
| `retire()` search fix | `facts_fts`/`facts_vec` exist (P9-B-01/02) | YES |
| Comments table + tool | None | YES |
| Report synthesis | `get_provider("synthesis")` + context assembly | NO (P9 Phase 4) |
| `kms_reports` tool | Tool registration pattern (P9-A-01) | NO (P9 Phase 5) |
| Trust-floor filtering | Context engine rewrite (P9-C-01) | NO |
| Volatility flag | Context engine rewrite (P9-C-01) | NO |
| Comment in `kms_inspect` | Inspect rewrite (P9-D-01) | NO |

---

## Open Questions (for spec/plan phases to resolve)

1. **OQ-P10-01: Trust score persistence on entry update.** When `write_entries()` updates an existing entry (action=update), should the trust score reset to 0.5 (default) or be preserved from the existing entry? Current design: preserved (the update only changes fact text/confidence, not trust). Confirm.

2. **OQ-P10-02: Correction history for revised entries.** When a `revise` creates a new entry and retires the old one, the new entry has no correction history. Should corrections on the old entry be "inherited" by the new one (e.g., via a `revised_from` FK)? Current design: no inheritance — the new entry starts clean. The old entry's corrections remain attached to the retired row for history.

3. **OQ-P10-03: Report cache/freshness.** Should `kms_reports` always regenerate, or serve the latest cached report if it is less than N hours old? Current design: always regenerate (simplest). Add a `max_age_hours` config per report type later if LLM cost becomes a concern.

4. **OQ-P10-04: Few-shot injection during self-correction retry.** When the orchestrator retries a failed extraction (withhold-stamp loop), should few-shot corrections be re-injected on retry? Current design: yes, same as the first attempt. The few-shot examples are static per dimension and do not change between retries.

5. **OQ-P10-05: Comment length limit.** Should comments have a character cap? Current design: no cap — comments are additive and the context loader already has a per-dimension budget. If a single comment is absurdly long, the context loader's cap naturally truncates the overall context. Add a column-level cap later if abuse is observed.

---

## Behavior Inventory

See `behavior_inventory.yaml` — entries P10-SL-01 through P10-SL-20 with `origin: design`, `granularity: outcome`.
