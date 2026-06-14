# Phase 8 — Classify Redesign: Grill Output

_Date: 2026-06-14_
_Status: Grill COMPLETE. Ready for design→spec→research→plan pipeline._
_Tier: HEAVY (complete rewrite, multi-module, LLM integration)_
_Slicing: 2 slices (A=infrastructure, B=extraction pipeline). Sequential — B depends on A._

---

## What Phase 8 Does (one sentence)

Rewrites `classify.py` from "pick a folder for a file" to "extract structured knowledge facts from document content into the `knowledge_entries` table across user-defined dimensions."

---

## All Decisions Made During Grill

### 1. Work Discovery Mechanism

**Decision:** `classify_content_hash` column on `documents` table.

- `NULL` = never classified (new file)
- Mismatches `content_hash` = content changed since last classification (needs re-classify)
- Matches `content_hash` = already done (skip)

Small migration. No queue, no flag table. Classify finds its own work with one query.

### 2. Trigger Mechanism

**Decision:** Capture finishes → pushes `doc_id` to an `asyncio.Queue` → single consumer loop processes documents sequentially.

- In-process (same container as capture). No separate worker.
- Sequential processing eliminates race conditions on shared entities.
- The queue also serves as retry mechanism for partial failures.
- Classify also runs a catch-up scan on container startup for anything missed.

### 3. LLM Output Format

**Decision:** Structured JSON.

LLM returns a JSON array of fact objects with actions. Validated before DB write. Matches existing `classify.py` pattern (`json.loads` on LLM response).

### 4. Dimensions Are User-Defined with Prompt Guidance

**Decision:** Each dimension in `dimensions.yaml` carries:
- A name
- A list of allowed tags (each set has mandatory `other`)
- A `guidance` field — user-injectable prompt text telling the AI what to look for in this dimension

```yaml
people:
  tags: [role, relationship, contact, preference, other]
  guidance: |
    Look for mentions of people — names, roles, relationships.
    Track who leads what, who reports to whom.

projects:
  tags: [status, deadline, decision, blocker, milestone, other]
  guidance: |
    Look for project names, status, deadlines, decisions, blockers.
    Track milestones and their dates.

domains:
  tags: [policy, principle, fact, trend, other]
  guidance: |
    Look for domain knowledge — policies, principles, industry facts.
```

No end-user setup flow yet. Dev manually edits for each user. Prompt injection seam exists from day one.

### 5. Dynamic Dimension Summary (Context for Extraction)

**Decision:** Context Loader dynamically assembles a summary of current state per dimension from existing entries. Fed to the LLM alongside the guidance and document content.

- Always current (reads live entries from DB)
- Budget-capped by the ranker (see #7) to prevent token bloat
- Tech debt logged: watch vault size as facts accumulate over time

### 6. One LLM Call Per Dimension

**Decision:** Extraction runs one focused LLM call per configured dimension. Not one monolithic call.

- Smaller focused prompts = better extraction quality
- Parallelizable (but we run sequentially in the queue — see #2)
- Adding a dimension = one more call, not a bloated combined prompt
- Cost acceptable — classify is async, not in the upload hot path

### 7. Fact Ranker (Budget Cap for Context Loader)

**Decision:** Per-dimension token budget with ranked selection.

```sql
ORDER BY trust_score DESC, confidence DESC, updated_at DESC
LIMIT :max_entries_per_dimension
```

Config: `classify.max_entries_per_dimension: 50` (or similar) in config.

Three ranking signals (arriving at different phases):
| Signal | Ships in | Populated by | Role |
|--------|----------|-------------|------|
| `trust_score` | P8 migration | P8 (inert 0.5), P10 (corrections) | User-validated quality |
| `confidence` | P5 migration | P8 extractor (live, updates with evidence) | AI evidence quality |
| `updated_at` | P5 migration | P8 extractor (on every write) | Recency |
| `retrieval_count` | P8 migration | P9 context injection | Demand signal |

Phase 8 ranker uses: trust_score + confidence + updated_at.
Phase 9 adds: retrieval_count.

### 8. Dedup Strategy — Prompt-Level

**Decision:** No unique index on knowledge_entries. The extractor sees existing entries in context → LLM avoids duplicates via prompt instruction. Fuzzy/semantic matching is the LLM's job, not SQL's.

### 9. Update/Retire via Entry IDs

**Decision:** Context Loader passes existing entries WITH their DB `id` to the LLM. LLM response references IDs for updates/retires, omits ID for new entries.

JSON response schema:
```json
[
  {"action": "new", "dimension": "people", "entity": "Anthony", "tag": "role", "fact": "VP of Product", "confidence": 0.9},
  {"action": "update", "id": 42, "fact": "VP of Product (promoted from PL)", "confidence": 0.95},
  {"action": "retire", "id": 37, "reasoning": "Q2 project completed"}
]
```

Entry Writer validates IDs exist before acting. Hallucinated IDs → skip + log warning.

### 10. Partial Failure Handling

**Decision:** Commit partial results. If dimension 1 succeeds but dimension 2 fails:
- Save dimension 1's entries to DB
- Leave `classify_content_hash` as NULL (so doc stays in "needs classify" queue)
- Log the failure
- Next run retries all dimensions; prompt-level dedup prevents duplicates from already-extracted dimensions

### 11. Content Input — Full Body vs Summary

**Decision:** If `len(full_body) / 4 < 10000` tokens (~40000 chars) → use full_body. Else → use summary.

Config: `classify.max_content_tokens: 10000`

DeepSeek V4 Pro as housekeeping AI — large context window makes 10k conservative.

### 12. Taxonomy Expansion

**Decision:** Expand the provisional `dimensions.yaml` in Phase 8 with richer defaults matching the rearchitecture doc examples + the new guidance field. Still user-configurable.

### 13. Sources — Document ID

**Decision:** `knowledge_entries.sources` stores document IDs (integers), not vault_paths.

- Stable — survives file moves without updating sources arrays
- Document deletion: remove ID from sources; if empty → status=pending (never delete entries, only retire)

### 14. Vault Path as Input Signal

**Decision:** Pass `vault_path` to the LLM as a contextual hint. Prompt instruction: "Consider file location as a signal of user intent, but content always wins on conflict."

### 15. Confidence is a Live Signal

**Decision:** Confidence is NOT frozen at extraction time. When the extractor updates an existing entry with new evidence, it reassesses confidence. The confidence band re-gates status on every write.

- Confidence moves based on AI evidence
- Trust moves based on user actions (Phase 10)
- Two independent feedback loops, both part of the ranker

### 16. Audit Logging — Per Dimension

**Decision:** One audit entry per dimension per document. Logs: dimension, counts (new/updated/retired), success/failure. Enough for Phase 10 analytics without bloating the audit log.

### 17. No Corrections Slot in Phase 8

**Decision:** Follow the roadmap — no stub, no forward-compat hook. Phase 10 adds the `corrections_block` variable to the prompt template when it ships the real implementation. C-15 spirit.

### 18. Trust Score — Flat 0.5 Initial

**Decision:** All entries start at 0.5 regardless of confidence band. Trust is a separate axis from confidence (user trust vs AI certainty). Phase 10 moves trust via corrections.

### 19. Rewrite classify.py in Place

**Decision:** Phase 7A lands first (confirmed). Old classify callers are dead by then. Phase 8 guts and rewrites `classify.py` — no orphan module, no parallel existence.

### 20. Document Deletion Source Cleanup

**Decision:** `/api/event` delete handler removes document ID from `sources` arrays. If sources becomes empty → status = `pending`. Never auto-delete, never auto-retire — user decides via web UI.

---

## Migration (Phase 8)

New columns:
- `documents.classify_content_hash` — TEXT, nullable (work discovery)
- `knowledge_entries.trust_score` — REAL, DEFAULT 0.5 (inert, Phase 10 activates)
- `knowledge_entries.retrieval_count` — INTEGER, DEFAULT 0 (inert, Phase 9 populates)

Index:
- `CREATE INDEX IF NOT EXISTS idx_ke_trust ON knowledge_entries(trust_score DESC);`
- `CREATE INDEX IF NOT EXISTS idx_docs_classify_hash ON documents(classify_content_hash);`

---

## Slice Breakdown

### Slice A — Infrastructure (no LLM calls)

**Components:**
1. **Migration** — 3 new columns + indexes
2. **Expanded `dimensions.yaml`** — richer tags + guidance field per dimension
3. **Dimension config loader** — loads and validates dimensions with guidance (extends `core/tags.py` or new module)
4. **Content Reader** — reads document full_body/summary from DB, applies token threshold
5. **Context Loader** — loads existing entries, assembles dynamic dimension summary, applies budget-cap ranker
6. **Async queue skeleton** — asyncio.Queue + single consumer loop + startup catch-up scan
7. **`classify_content_hash` update logic** — set hash on successful classify, check for work discovery

**Testable without LLM.** All pure functions + DB queries + config parsing.

### Slice B — Extraction Pipeline (LLM integration)

**Components:**
1. **Entity extraction prompt** — `prompts/entity_extract.yaml` (per-dimension, with guidance slot + existing entries slot + document content)
2. **Entity Extractor** — calls LLM per dimension, parses JSON response, validates dimension/tag/action
3. **Entry Writer** — routes by action (new→insert, update→update, retire→retire), validates IDs, updates sources, sets `classify_content_hash` on success
4. **Classify orchestrator** — wires Content Reader → Context Loader → (per dimension: Extractor → Writer) → audit → hash update
5. **Wire to capture** — replace Phase 7A log line with queue push
6. **Delete old classify code** — gut `classify.py`, remove `ClassifyResult`, `build_subject`, folder-routing logic
7. **Audit logging** — per-dimension entries with counts
8. **Source cleanup enhancement** — update `/api/event` delete handler to remove doc IDs from sources arrays

**Depends on Slice A** (ranker, context loader, queue, migration).

---

## Forward Items to Log in Roadmap

### Phase 9 additions:
- `retrieval_count` increment in context injection (context.py) — every time an entry is surfaced in MCP response, bump the count
- Add `retrieval_count` to ranker formula for context injection sorting
- Trust-aware filtering: `WHERE trust_score >= :min_trust` (config: `mcp.context_injection.min_trust: 0.3`)

### Phase 10 additions:
- Trust score decay over time (monitor if stale entries become a problem)
- Token budget monitoring for dimension summaries (as facts accumulate, may need optimization from dynamic to pre-computed)
- Correction volatility flag: entries with > 3 corrections get `[frequently corrected]` in context blocks

---

## Dependencies

- **Phase 7A MUST land first** — populates `documents.full_body` which classify reads
- **Phase 5 Slice 1 already shipped** — `knowledge_entries` table + CRUD, `dimensions.yaml`, `validate_dimension_tag()`
- **Phase 5 Slice 2 already shipped** — `/api/event` handler (source cleanup enhancement goes here)

---

## LLM Provider Note

Housekeeping AI = DeepSeek V4 Pro (OpenAI-compatible endpoint). Fits existing `llm/provider.py` factory with config-only change — `get_provider("classify", CONFIG.main)` points at DeepSeek endpoint. No code change needed.

---

## Tech Debt Logged

- **Watch dimension summary token growth** — as facts accumulate, dynamic assembly may hit budget. Monitor. Optimize to pre-computed if needed.
- **No unique index on knowledge_entries** — prompt-level dedup only. If duplicates become a problem, add index later.
- **Stale-fact decay** — no time-based trust decay in P8/P10 MVP. Add if correction data shows stale entries are a problem.
