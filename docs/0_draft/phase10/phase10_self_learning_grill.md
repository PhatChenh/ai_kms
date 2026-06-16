# Phase 10 — Self-Learning & Reports Backend: Grill Output

_Created: 2026-06-15_
_Status: GRILL COMPLETE — ready for design phase_
_Audience: Next AI session running /codebase-design-analysis_
_Context: Read alongside `docs/0_draft/cloud_native_rearchitecture.md` §7 (self-learning loop) and §10 (web UI)_

---

## Scope Decision

**Phase 10 is backend-only.** No frontend. REST API + MCP tools + documentation for Phase 11 to build the interactive web UI against.

The roadmap acceptance criterion "Non-technical user completes browse + correct flow without instructions" **moves to Phase 11.**

---

## MVP Delivery Channel

No web UI, but insights are accessible NOW via MCP tools. Consumer AI (Claude Desktop/web) pulls pre-computed reports via `kms_reports` tool. Consumer AI can format conversationally or generate HTML artifacts from raw data. No housekeeping AI HTML generation pipeline — consumer AI handles presentation.

---

## Decided: Four Deliverable Areas

### 1. Corrections System

**Table design:** `fact_corrections` — one table for this signal type. Future learning signals (classify errors, summary corrections) get their own tables. No single "learning_signals" junk drawer.

**Three correction operations:**

| Operation | What happens | Trust delta | Entry status after |
|---|---|---|---|
| **Confirm** | User validates a fact is correct | +0.05 | `confident` |
| **Reject** | User says fact is wrong or stale | -0.10 | `retired` |
| **Revise** | User fixes fact text | Old entry: retired. New entry: trust 0.6 | Old: `retired`. New: `confident` |

**Trust deltas are the same regardless of correction reason.** Trust is about the fact's reliability, not the AI's performance. A stale fact is equally untrustworthy whether the AI misread or the source was outdated.

**Correction reason categories (affects few-shot injector, not trust):**

| Category | Meaning | Fed to few-shot injector? |
|---|---|---|
| `ai_error` | AI misread the document | YES — teaches extraction accuracy |
| `stale_source` | Source document was outdated/wrong, AI extracted correctly | NO — would teach AI to ignore documents |

**Consumer AI categorizes automatically:** checks source document(s) via `kms_inspect`, compares extracted fact against source text, determines if AI misread or source was wrong. Passes verdict as `reason_category` parameter on `kms_correct`.

**Feedback field:** Optional `feedback` TEXT on every correction. Consumer AI collects user's explanation naturally in conversation ("why is this wrong?" → user explains). Stored alongside old/new values. Enriches few-shot training signal.

**`kms_correct` parameters (extending P9's version):**
- `entry_id` (existing)
- `operation` (existing — renamed from P9: confirm/reject/revise instead of promote/retire/edit)
- `reason_category` (NEW — `ai_error` or `stale_source`)
- `feedback` (NEW — optional user explanation)
- `new_fact` / `new_tag` / `new_entity` (existing from P9)

**`AI_INSTRUCTIONS.md` update:** Instruct consumer AI to check sources before calling `kms_correct`. Categorize by comparing extracted fact against source document.

### 2. Trust Mechanics

**`adjust_trust(current_score, operation) → float`** — pure function, deltas in config (C-06).

| Operation | Delta | Notes |
|---|---|---|
| Confirm | +0.05 | Asymmetric — slow to build trust |
| Reject | -0.10 | Faster to lose trust (Hermes-inspired) |
| Revise | Set 0.6 on new entry | User-provided fact has higher baseline than AI-extracted (0.5) |

**`_should_overwrite` activation (Phase 9 placed the seam, Phase 10 activates):**
- `trust_score > 0.5` → do NOT overwrite. Write a new `pending` entry with the contradictory fact instead.
- Threshold in config: `self_learning.overwrite_trust_threshold: 0.5`
- Rationale: protects user-validated facts only. Unconfirmed entries (trust 0.5) remain freely updatable by classify.

**`min_trust` filtering:**
- Threshold 0.3 in context injection config
- Entries below 0.3 excluded from MCP context blocks
- P9-dependent: needs P9's rewritten context engine

**Volatility flag:**
- Entries with >3 corrections get `[frequently corrected]` appended in context blocks
- Signals contested facts to consumer AI
- P9-dependent: needs P9's context engine

### 3. Reports System

**Architecture:** Housekeeping AI synthesizes reports on-demand. Results stored in DB. Consumer AI pulls via MCP tool.

**Report definition (YAML config):**
```yaml
reports:
  project_a_status:
    filters:
      dimensions: [projects]
      entities: [Project A]
      status: [confident, pending]
    prompt: "Summarize current state of this project. Flag any conflicting facts or stale decisions."
    sources: [facts, summaries]  # what data to feed the prompt
```

- `filters` — deterministic SQL WHERE clause (dimensions, entities, status, tags)
- `prompt` — what the AI does with the filtered data
- `sources` — which corpora to read: `facts` (knowledge_entries), `summaries` (documents.summary), `full_text` (documents.full_body, opt-in, expensive)

**Adding a new report = copy a YAML block, change filters + prompt. No code.**

**`reports` table:**

| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `report_type` | TEXT NOT NULL | Key from YAML config |
| `title` | TEXT NOT NULL | Human-readable title |
| `body` | TEXT NOT NULL | Synthesized report text |
| `prompt` | TEXT NOT NULL | Exact prompt sent to LLM (for user evaluation) |
| `filters_used` | TEXT (JSON) | Snapshot of filters that produced this report |
| `sources_used` | TEXT (JSON array) | Doc IDs / entry IDs fed into synthesis |
| `created_at` | TEXT NOT NULL | When generated |

No versioning — each run creates a new row. Consumer pulls latest by `report_type`. History queryable.

**Default report types (ship in config):**

1. **Correction summary** — X entries corrected this period. Most-corrected entities. Accuracy trend.
2. **Knowledge health** — Total facts across entities. Pending entries awaiting review. Coverage by dimension.
3. **Volatile entries** — Entries with >3 corrections. Possible ambiguity or conflicting sources.
4. **Coverage gaps** — Documents captured but zero knowledge entries extracted. Possible extraction failures.
5. **Conflicts** — Entries blocked by `_should_overwrite` guard. Same entity+dimension with contradictory active facts.

**Context injection for synthesis:** Reuse P9's context assembly logic (4-key ranking: trust, retrieval, confidence, recency) to build the context window for report LLM calls. Same quality as consumer AI context. P9-dependent.

**MCP tool:** `kms_reports` — on-demand trigger. Consumer AI calls → housekeeping AI runs synthesis synchronously (3-8s LLM call, acceptable for single-user) → result stored in DB → returned in same response.

### 4. Comments

**Simple table:** `entry_comments` — `id`, `entry_id` (FK → knowledge_entries), `comment_text`, `created_at`.

**One REST endpoint** to add a comment. Comments are additive — no overwrite, no edit, no delete.

**Context loader integration:** When housekeeping AI runs classify, the context loader reads comments for entities being processed. Comments provide human context the AI should consider during extraction (e.g., "This person left the company").

**MCP delivery:** Consumer AI calls `kms_correct` with a `comment`-type operation, or a separate `kms_comment` endpoint. Design phase decides.

---

## Few-Shot Injector

**Selection strategy: relevance-first, not recency-first.**

Priority order:
1. **Dimension match** — if extracting `people` dimension, prefer corrections about `people` entries
2. **Entity overlap** — if document mentions "Anthony," prefer corrections about Anthony
3. **Recency** — among equally relevant corrections, prefer recent ones

**Filter:** `WHERE correction_reason = 'ai_error'` only. Stale-source corrections excluded.

**Cap:** `self_learning.max_corrections_per_prompt` in config. Default TBD in design (5-10 range).

**Injection point:** Prepended to `entity_extract.yaml` prompt as few-shot examples during classify runs.

---

## Bug Fix (Picked Up During Grill)

**`retire()` in `knowledge_entries.py` incorrectly purges entries from `facts_fts` and `facts_vec` search indexes.**

Current behavior: retired entries are unsearchable everywhere.

Correct behavior per rearchitecture doc §7:
- **Context injection:** retired entries excluded (correct as-is)
- **Search:** retired entries INCLUDED, flagged as retired in results. "What did we used to think about X?" must find retired entries.

Fix: `retire()` should NOT remove from search indexes. Filtering happens at query time — context injection adds `WHERE status != 'retired'`, search does not (or has opt-in `include_retired` flag).

---

## P9 Dependencies

| Phase 10 component | P9 dependency | Can start before P9? |
|---|---|---|
| `fact_corrections` table + migration | None | YES |
| `adjust_trust()` pure function | None | YES |
| `_should_overwrite` activation | P9 places seam (P9-F-01) | YES (function exists, just activate) |
| Correction operations (confirm/reject/revise) | `kms_correct` backing (P9-A-02) | PARTIALLY — P9 builds basic `kms_correct`, P10 extends |
| Few-shot injector | Classify pipeline (P8, done) | YES |
| Reports table + YAML config | None | YES |
| Report synthesis pipeline | P9 context engine rewrite (P9-C-01/C-02) for context injection reuse | NO — needs P9 Phase 4 |
| `kms_reports` MCP tool | P9 tool registration pattern (P9-A-01) | NO — needs P9 Phase 5 |
| `min_trust` filtering | P9 context engine (P9-C-01) | NO |
| Volatility flag | P9 context engine (P9-C-01) | NO |
| Comments table + endpoint | None | YES |
| Retire search fix | P9 fact_search.py (P9-B-06) | PARTIALLY — fix retire() now, search query adjustment after P9 |

**~60% of Phase 10 work is P9-independent.** Design all of it now, implement P9-independent pieces first.

---

## Upstream Decisions Carried Forward

From Phase 8 grill:
- Trust adjustment is asymmetric (Hermes-inspired)
- `min_trust: 0.3` filtering threshold
- Volatility flag at >3 corrections
- Token budget monitoring for dimension summaries
- Trust score decay over time — deferred, monitor first

From Phase 9 plan:
- `_should_overwrite` seam placed in `classify_writer.py`, always returns True
- Corrections table "exists but inert and wrong-shaped" — Phase 10 designs from scratch
- `retrieval_count` increment wired in P9

---

## Open Questions for Design Phase

1. **`kms_comment` vs extending `kms_correct`** — separate MCP tool for comments, or add a `comment` operation type to `kms_correct`?
2. **Report synthesis prompt design** — how much of the context assembly logic from P9 can be directly reused vs needs adaptation for report-specific queries?
3. **Few-shot injection cap** — default 5 or 10? Need to measure token budget impact during design.
4. **Conflict detection** — how does the "conflicts" report type detect contradictory facts? Semantic similarity between facts for same entity+dimension? Or simpler heuristic (multiple active entries for same entity+tag)?
5. **Comment visibility in MCP** — should comments on an entry be included when consumer AI reads that entry via `kms_inspect`?
