# Self-Learning Research: Hermes-Agent → AI-KMS Adaptation

> **Purpose:** Research output for the next AI session that will update the ai-kms roadmap (specifically Phases 8, 9, and 10) to incorporate self-learning capabilities. Based on deep analysis of the hermes-agent project at `/Users/phatchenh/hermes-agent`.
>
> **Date:** 2026-06-14
> **Source project:** hermes-agent (Claude Code's open-source agent framework)
> **Target project:** ai-kms at `/Users/phatchenh/ai_kms` (roadmap: `docs/roadmap/roadmap.md`)

---

## Part 1: How Hermes-Agent Implements Self-Learning

Hermes uses three layered mechanisms. None involve fine-tuning. All operate through prompt injection of learned context into future sessions.

### Mechanism 1: Background Review Loop (Primary Learning Path)

**File:** `agent/background_review.py`

After every conversation turn, a daemon thread forks the agent with a restricted tool whitelist (memory + skill tools only). The fork replays the conversation and asks: "should any skill/memory be saved or updated?"

Two persistent stores get updated:

- **MEMORY.md** (`~/.hermes/memories/MEMORY.md`) — facts about the user: preferences, expectations, persona, communication style. Injected as frozen snapshot into system prompt at session start.
- **SKILL.md files** (`~/.hermes/skills/<skill-name>/SKILL.md`) — class-level task knowledge: "how to do X for this user." Each skill is an umbrella with `references/`, `templates/`, `scripts/` subdirectories.

**Key design decisions:**

1. **Corrections are first-class signals.** Frustration phrases like "stop doing X", "too verbose", "don't format like this" trigger skill patches, not just memory writes. The review prompt (lines 54-61) explicitly lists these as skill signals.

2. **Preference order for updates:** (a) patch the skill that was active in the conversation, (b) patch an existing umbrella skill, (c) add a support file under an existing skill, (d) create a new class-level skill. Always prefer updating over creating.

3. **Anti-patterns explicitly blocked:** environment-dependent failures, negative claims about tools ("X is broken"), session-specific transient errors, one-off task narratives. These "harden into refusals the agent cites against itself for months."

4. **Memory vs skill distinction:** Memory = "who the user is"; Skills = "how to do this class of task for this user." User preference corrections go into BOTH (the fact in memory, the behavioral change in the governing skill).

**Review prompts (three variants, all in `background_review.py`):**
- `_MEMORY_REVIEW_PROMPT` (lines 34-43): Focus on user preferences/expectations
- `_SKILL_REVIEW_PROMPT` (lines 45-148): Detailed 147-line prompt with preference order, anti-patterns, protected skill rules
- `_COMBINED_REVIEW_PROMPT` (lines 150-232): Both checks simultaneously (used when both memory and skill review are enabled)

**Nudge system** (`agent/turn_context.py` lines 210-217, `agent/agent_init.py` lines 1110-1118):
- Memory nudge: every N turns (config: `agent.memory.nudge_interval`, default 10)
- Skill creation nudge: every N tool-calling iterations (config: `agent.skills.creation_nudge_interval`, default 10)
- Nudges inject a reminder into the turn context, not the system prompt

### Mechanism 2: Holographic Trust Scoring (Structured Facts with Feedback)

**Files:** `plugins/memory/holographic/__init__.py`, `plugins/memory/holographic/store.py`

SQLite-backed fact store with entity resolution and trust scoring. Optional plugin (not enabled by default).

**Schema (from `store.py`):**
```sql
CREATE TABLE facts (
    fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL,
    category        TEXT DEFAULT 'general',  -- user_pref, project, tool, general
    tags            TEXT DEFAULT '',
    trust_score     REAL DEFAULT 0.5,
    retrieval_count INTEGER DEFAULT 0,
    helpful_count   INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE entities (
    entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE fact_entities (
    fact_id     INTEGER NOT NULL,
    entity_id   INTEGER NOT NULL,
    PRIMARY KEY (fact_id, entity_id)
);
```

**Two tools exposed to the agent:**
1. `fact_store` — CRUD + compositional queries (add, search, probe, related, reason, contradict, update, remove, list)
2. `fact_feedback` — binary rating after using a fact: "helpful" or "unhelpful"

**Trust scoring mechanics (from `store.py` lines 353-392):**
- `helpful=True` → trust += 0.05, `helpful_count` += 1
- `helpful=False` → trust -= 0.10
- **Asymmetric by design:** bad facts sink 2x faster than good facts rise
- Trust clamped to [0.0, 1.0]
- Queries filter by `min_trust` (default 0.3) — facts below threshold stop appearing in results
- Result: self-correcting fact store. Good facts surface more, bad facts decay naturally.

**Entity resolution:** Facts linked to entities via junction table. `probe(entity="Anthony")` returns all facts about Anthony. `reason(entities=["Anthony", "Project Q2"])` returns facts connected to BOTH entities (compositional query using HRR-based retrieval).

### Mechanism 3: Skill Curator (Lifecycle Management)

**File:** `agent/curator.py`

Periodic background job that maintains the skill library. Runs when agent is idle and last run was > `interval_hours` ago (default: 7 days).

**Responsibilities:**
- Auto-transition lifecycle states based on activity timestamps
- `last_active_at` per skill (derived from usage)
- Stale threshold: 30 days (default) → flagged for review
- Archive threshold: 90 days (default) → auto-archived (recoverable, not deleted)
- Consolidation: overlapping skills merged by the curator agent
- Pinned skills bypass auto-transitions (but can still be patched)

**State tracking** (`~/.hermes/skills/.curator_state`):
```json
{
    "last_run_at": "2026-06-10T...",
    "last_run_duration_seconds": 45,
    "last_run_summary": "Archived 2 skills, consolidated 1",
    "run_count": 12,
    "paused": false
}
```

### Supplementary: Prefill Messages (Manual Few-Shot Injection)

**File:** `cli.py` lines 289-333

Config key `prefill_messages_file` points to a JSON file of `{role, content}` pairs. These are injected into every API call but never saved to conversation history. Allows manual few-shot examples for steering agent behavior. Not automatic — human-curated.

### Supplementary: Session Search (Cross-Session RAG)

**File:** `tools/session_search_tool.py`

FTS5 full-text search over past session transcripts with LLM summarization. Allows the agent to recall context from prior conversations. Not directly self-learning, but provides the retrieval layer that could feed a learning system.

---

## Part 2: How Self-Learning Maps to AI-KMS

### Architecture Comparison

| Aspect | Hermes | AI-KMS |
|--------|--------|--------|
| **Learning trigger** | Implicit (conversation corrections detected by background fork) | Explicit (user clicks promote/retire/edit in Web UI) |
| **Storage** | Markdown files (MEMORY.md, SKILL.md) + optional SQLite (holographic) | SQLite `knowledge_entries` table + new `corrections` table |
| **Feedback mechanism** | Binary (helpful/unhelpful) on facts | Structured corrections (promote/retire/edit with field+reason) |
| **Injection method** | System prompt snapshot (memory) + skill loading (skills) | Few-shot examples in extraction prompt + trust-weighted context injection |
| **Lifecycle** | Curator auto-archives stale skills | `status` field (confident/pending/retired) + trust score decay |
| **Learning loop** | Background fork → skill patch → next session loads patched skill | User correction → corrections table → few-shot injector → next classify run uses examples |

### What AI-KMS Should Adopt

1. **Trust scoring with asymmetric adjustment** — directly from Hermes holographic store. Proven pattern.
2. **Corrections as structured data** — Hermes stores corrections as prose in markdown; AI-KMS should use a structured `corrections` table (better for querying, analytics, few-shot formatting).
3. **Few-shot injection into prompts** — Hermes uses `prefill_messages_file`; AI-KMS should inject recent corrections into `entity_extract.yaml` as examples.
4. **Threshold-based filtering** — Hermes filters facts by `min_trust`; AI-KMS context injection should filter `knowledge_entries` by trust score.

### What AI-KMS Should Skip

1. **Background review fork** — Hermes needs this because it's a conversational agent that learns mid-chat. AI-KMS is batch-oriented: user corrects in Web UI → correction stored → next classify run reads corrections. Simpler loop.
2. **Skill system** — overkill. AI-KMS doesn't need self-modifying task procedures. Corrections + few-shot injection achieves the same learning effect.
3. **Curator** — AI-KMS already has `status=retired` lifecycle on `knowledge_entries`. No need for separate lifecycle management.
4. **Session search / cross-session RAG** — AI-KMS's MCP server is request/response, not conversational. Search already exists via Phase 3.

---

## Part 3: Specific Changes Per Phase

### Phase 8 — Classify Redesign (3 forward-compatible hooks)

These are small additions that prevent Phase 10 from rewriting Phase 8 code.

#### 1. `corrections` table — ship in Phase 8 migration

```sql
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_entry_id INTEGER NOT NULL,
    action TEXT NOT NULL,              -- 'promote', 'retire', 'edit'
    field_changed TEXT,                -- 'fact', 'tag', 'entity', 'dimension', 'status'
    old_value TEXT,
    new_value TEXT,
    reason TEXT,                       -- required for 'retire', optional for others
    corrected_by TEXT,                 -- user identifier
    corrected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (knowledge_entry_id) REFERENCES knowledge_entries(id)
);
CREATE INDEX IF NOT EXISTS idx_corrections_entry ON corrections(knowledge_entry_id);
CREATE INDEX IF NOT EXISTS idx_corrections_action ON corrections(action);
CREATE INDEX IF NOT EXISTS idx_corrections_at ON corrections(corrected_at DESC);
```

Plus a `storage/corrections.py` CRUD module following the same pattern as `storage/knowledge_entries.py`:
- `record(knowledge_entry_id, action, field_changed, old_value, new_value, reason, corrected_by) → Result[int]`
- `query_by_entry(knowledge_entry_id) → Result[list[CorrectionRow]]`
- `query_recent(limit, dimension=None) → Result[list[CorrectionRow]]` — for few-shot injection
- `correction_count(knowledge_entry_id) → Result[int]` — for analytics

All return `Result` types (C-12). Phase 10 Web UI calls `record()`. Phase 10 few-shot injector calls `query_recent()`.

#### 2. `trust_score` column on `knowledge_entries`

Add to Phase 8's migration (same migration file as `corrections` table):

```sql
ALTER TABLE knowledge_entries ADD COLUMN trust_score REAL DEFAULT 0.5;
CREATE INDEX IF NOT EXISTS idx_ke_trust ON knowledge_entries(trust_score DESC);
```

Phase 8 ENTRY WRITER sets initial trust from confidence band:
- `AUTO` (high confidence) → trust = 0.7
- `SUGGEST` (medium) → trust = 0.5
- `CLUELESS` (low) → trust = 0.3

Phase 10 adjusts trust via corrections:
- Promote (pending → confident): trust += 0.05
- Retire (confident → retired): trust -= 0.10
- Edit: trust = 0.6 (user-validated but modified)

Trust adjustment logic lives in a pure function (can be in `core/tags.py` or a new `core/trust.py`):
```python
HELPFUL_DELTA = 0.05
UNHELPFUL_DELTA = -0.10

def adjust_trust(current: float, action: str) -> float:
    """Asymmetric trust adjustment. Mirrors Hermes holographic store."""
    if action == "promote":
        return min(1.0, current + HELPFUL_DELTA)
    elif action == "retire":
        return max(0.0, current + UNHELPFUL_DELTA)
    elif action == "edit":
        return 0.6  # user-validated but modified
    return current
```

Ship this function in Phase 8 (it's pure, no dependencies). Phase 10 calls it.

#### 3. Few-shot injection slot in ENTITY EXTRACTOR

Phase 8's `entity_extract.yaml` prompt template includes an optional corrections block:

```yaml
# In entity_extract.yaml:
{% if corrections_block %}

## Recent user corrections (learn from these)
The user has previously corrected AI extractions. Use these as guidance:
{{ corrections_block }}

Apply these patterns: if the user corrected a similar extraction before, 
follow their correction. Do not repeat mistakes the user already fixed.
{% endif %}
```

Phase 8 ENTITY EXTRACTOR renders this with an empty string:

```python
# In Phase 8 entity extractor:
def _load_corrections_block(dimension: str) -> str:
    """Load recent corrections for few-shot injection. Stub until Phase 10."""
    return ""

# Called in extractor:
prompt = PROMPTS["entity_extract"].render(
    content=doc.full_body,
    existing_entries=context.entries_by_dimension,
    corrections_block=_load_corrections_block(dimension),
)
```

Phase 10 implements the real `_load_corrections_block()`:

```python
# Phase 10 replaces the stub:
def _load_corrections_block(dimension: str, max_examples: int = 5) -> str:
    """Load recent corrections as few-shot examples for extraction prompt."""
    result = corrections.query_recent(limit=max_examples, dimension=dimension)
    if result.is_failure() or not result.value:
        return ""
    
    lines = []
    for c in result.value:
        if c.action == "edit":
            lines.append(f"- Extracted: {c.old_value} → User corrected to: {c.new_value}")
        elif c.action == "retire":
            lines.append(f"- Extracted: {c.old_value} → User retired (reason: {c.reason})")
        elif c.action == "promote":
            lines.append(f"- Extracted: {c.old_value} → User confirmed as correct")
    return "\n".join(lines)
```

Config for max examples: `classify.max_corrections_in_prompt: 5` (C-06: thresholds in config).

### Phase 9 — MCP Adaptation (2 changes)

#### 1. Trust-aware context injection

`context.py` rewrite (already planned in roadmap) queries `knowledge_entries` for context blocks. Add trust-based filtering and sorting:

- Query: `WHERE status IN ('confident', 'pending') AND trust_score >= :min_trust ORDER BY trust_score DESC`
- Config: `mcp.context_injection.min_trust: 0.3` (same threshold as Hermes holographic store)
- High-trust entries appear first in context blocks, low-trust entries excluded entirely

This is a query-level change — no architectural change. Just reads the `trust_score` column that Phase 8 ships.

#### 2. Correction volatility signal in context blocks

When building context blocks for `kms_vault_info` or `kms_search`, entries with many corrections should be flagged:

```python
correction_count = corrections.correction_count(entry.id)
if correction_count.value and correction_count.value > 3:
    context_line += " [frequently corrected]"
```

This tells the user-facing Claude that this fact has been contested. Low cost, high signal.

### Phase 10 — Web UI + Self-Learning (3 additions to existing roadmap)

#### 1. Trust score visualization in BROWSE

Show trust score as visual indicator alongside status badge. High-trust entries (> 0.8) feel "solid" (green), mid-trust (0.3-0.8) feel "normal", low-trust (< 0.3) feel "tentative" (grey/faded). Users can sort by trust score.

#### 2. Correction analytics view

Simple dashboard showing:
- Most corrected entities (which entities does AI get wrong most?)
- Correction rate by dimension (is "people" extraction more accurate than "projects"?)
- Entries promoted vs retired this week
- Trend: is extraction accuracy improving over time?

This helps users spot systematic extraction errors and decide whether to adjust `config/dimensions.yaml` or refine extraction prompts.

#### 3. Implement real `_load_corrections_block()`

Replace the Phase 8 stub with the real implementation (see Phase 8 section above). This is the core self-learning mechanism: corrections → few-shot examples → better future extractions.

---

## Part 4: Design Decisions to Resolve

These should be discussed during `/grill` for the relevant phase:

### Phase 8 Grill Questions

1. **Should `corrections` table support correction of documents (not just knowledge entries)?** User might want to correct a document's title, tags, or summary. If yes, add optional `document_id` column (nullable FK to `documents`). If no, corrections are knowledge-entry-only.

2. **Natural-key dedup on `corrections`?** Multiple corrections to the same entry+field create multiple rows. Is that the desired behavior (full history) or should we deduplicate (latest correction wins)?
   - **Recommendation:** Keep full history. The few-shot injector reads recent corrections by timestamp. History enables trend analysis in Phase 10.

3. **Trust score decay over time?** Hermes doesn't implement this. Should AI-KMS decay trust scores for entries that haven't been accessed or validated in N days?
   - **Recommendation:** Skip for MVP. Add in Phase 10 if correction data shows stale entries are a problem. Premature optimization otherwise.

4. **Correction propagation:** When user corrects entity name (e.g., "Tony" → "Anthony"), should all entries for that entity be updated?
   - **Recommendation:** Yes, but only for entity name changes (not fact changes). Entity rename = global. Fact correction = per-entry.

### Phase 9 Grill Questions

1. **Trust threshold for context injection — fixed or per-dimension?** Some dimensions might have systematically lower trust (e.g., "projects" are volatile, "people/role" are stable).
   - **Recommendation:** Start with global threshold. Per-dimension thresholds are a Phase 10 optimization if correction data justifies it.

### Phase 10 Grill Questions

1. **Correction pattern detection — automatic or manual?** Should the system automatically detect "user always retires role extractions for people in dimension X" and adjust behavior? Or should it just surface the pattern in analytics and let the user decide?
   - **Recommendation:** Surface in analytics first. Automatic adjustment is risky (false patterns → wrong corrections applied). Let user decide whether to add explicit rules to `dimensions.yaml`.

2. **Max corrections in prompt — how to select?** By recency? By relevance to current document? By dimension match?
   - **Recommendation:** Dimension match + recency. Load corrections for the same dimension being extracted, ordered by `corrected_at DESC`, capped by config limit. Relevance-based selection (semantic similarity to current document) is a future optimization.

---

## Part 5: Summary Table of All Changes

| Phase | Change | Type | Cost | Purpose |
|-------|--------|------|------|---------|
| **8** | `corrections` table + CRUD module | Migration + new module | ~150 LOC | Store user corrections for Phase 10 |
| **8** | `trust_score` column on `knowledge_entries` | Migration | ~10 LOC | Gradient confidence for context injection |
| **8** | `adjust_trust()` pure function | New function in `core/trust.py` or `core/tags.py` | ~15 LOC | Trust adjustment logic (Hermes-inspired) |
| **8** | `corrections_block` slot in `entity_extract.yaml` | Prompt template change | ~10 LOC | Few-shot injection seam for Phase 10 |
| **8** | `_load_corrections_block()` stub | Stub function in extractor | ~5 LOC | Phase 10 replaces with real implementation |
| **9** | Trust-aware filtering in `context.py` | Query change | ~10 LOC | High-trust entries first, low-trust excluded |
| **9** | `min_trust` config key | Config addition | ~3 LOC | Configurable trust threshold |
| **9** | Correction volatility flag in context blocks | Display logic | ~10 LOC | Signal contested facts to user-facing Claude |
| **10** | Real `_load_corrections_block()` | Replace stub | ~30 LOC | Core self-learning mechanism |
| **10** | Trust score visualization | UI feature | TBD | User sees confidence gradient |
| **10** | Correction analytics dashboard | UI feature | TBD | User spots systematic errors |
| **10** | `max_corrections_in_prompt` config | Config addition | ~3 LOC | Control few-shot injection budget |

**Total forward-compatible cost in Phase 8:** ~190 LOC (migration + CRUD + stub + pure function + prompt slot)
**Total forward-compatible cost in Phase 9:** ~23 LOC (query change + config + display flag)

---

## Part 6: Key Files in Hermes-Agent (Reference)

For anyone who wants to read the source patterns directly:

| File | What it does | Relevant pattern |
|------|-------------|-----------------|
| `agent/background_review.py` | Background fork that learns from corrections | Review prompt design, correction signal detection |
| `agent/turn_context.py` (lines 210-217) | Nudge counter setup | Periodic review triggering |
| `agent/curator.py` | Skill lifecycle management | Auto-archive, consolidation, state tracking |
| `agent/memory_manager.py` | Memory provider orchestration | Plugin-based memory architecture |
| `tools/memory_tool.py` | Built-in memory (MEMORY.md / USER.md) | Bounded file-backed memory, injection into system prompt |
| `tools/skill_manager_tool.py` | Skill create/edit/patch/delete | Preference embedding in task knowledge |
| `plugins/memory/holographic/__init__.py` | Structured facts + feedback tools | `fact_feedback` schema, trust scoring tools |
| `plugins/memory/holographic/store.py` | SQLite fact store | Trust scoring SQL, asymmetric adjustment, entity resolution |
| `agent/agent_init.py` (lines 1110-1205) | Config loading for learning features | Config keys, defaults, validation |
| `cli.py` (lines 289-333) | Prefill messages loading | Manual few-shot injection mechanism |
