# Research: Phase 10 — Self-Learning & Reports Backend
_Last updated: 2026-06-16_

## Overview

Phase 10 adds a user feedback loop to the AI knowledge extraction system. Users can confirm, reject, or revise individual facts extracted by the classify pipeline, and the system adjusts a per-fact trust score accordingly. Trusted facts become protected from silent AI overwrites, past AI mistakes become teaching examples for future extraction runs, and on-demand reports synthesize knowledge health.

This research verified the spec's 9 assumptions (A1-A9) against the actual codebase after Phase 9 implementation. It also answered the 4 specific verification items from the spec's handoff notes.

**Summary:** 7 validated, 2 invalidated, 0 unverifiable. Both invalidated assumptions are mechanical mismatches (wrong method name, wrong line numbers for a helper call). A separate confirmed gap — `upsert()` SQL missing `trust_score` — was identified via the Suggested Research items (SR-1) and must be fixed as a prerequisite before trust scores can flow through the system.

---

## Key Components

The self-learning feature extends existing components rather than creating a new standalone pipeline. The key modules it touches are:

| Module | Role in Phase 10 |
|--------|-----------------|
| `storage/knowledge_entries.py` | Fact store: upsert, retire, ranked queries. Trust_score column exists (DEFAULT 0.5) but upsert() does not write it. |
| `pipelines/classify_writer.py` | Entry writer: `_should_overwrite()` seam exists, always returns True. Two call sites for overwrite guard. |
| `pipelines/classify_extract.py` | Entity extraction: `extract()` takes 6 positional+keyword args. Prompt has 4 variables. |
| `pipelines/classify_orchestrator.py` | Per-doc orchestration: calls extract() per dimension in a loop. No entity list available pre-extraction. |
| `mcp_server/_correct.py` | Correction handler: 6 operations, returns `Result[dict]`. No trust adjustment or correction recording. |
| `mcp_server/context.py` | Context injection engine: `_build_orientation_facts()` and `_build_orientation_for_entities()` query via `query_ranked_for_orientation()` which already filters `status != 'retired'` but has no trust-floor filter. |
| `mcp_server/tools.py` | Tool shim layer: 5 tools registered (vault_info, search, inspect, write, correct). Logic-free. |
| `retrieval/fact_search.py` | Fact search: `_join_entries()` correctly filters `AND status != 'retired'`. |
| `core/config.py` | `SelfLearningConfig` has exactly 5 fields. |
| `prompts/entity_extract.yaml` | 4 variables: document_text, dimension_guidance, existing_facts, previous_attempt_feedback. |

---

## How It Works

The self-learning feedback loop works as follows:

1. **User corrects a fact** via the consumer AI (Claude Desktop), which calls `kms_correct` with an operation (confirm/reject/revise) and optional reason.
2. **Trust Calculator** (new, pure function) computes the new trust score based on deltas from config.
3. **Correction Handler** (`_correct.py`) applies the operation, adjusts trust via upsert, and records the correction in the `fact_corrections` table.
4. **On next classify run**, the orchestrator calls the **Few-Shot Selector** (new) to find recent `ai_error` corrections relevant to the current dimension, formats them as teaching examples, and passes them to `extract()`.
5. **Overwrite Guard** (`_should_overwrite()` in `classify_writer.py`) checks trust before updating — if trust exceeds the threshold, a new pending entry is created instead of overwriting the trusted one.
6. **Context Engine** (`context.py`) applies trust-floor filtering to orientation queries, excluding low-trust facts from the consumer AI's context. Frequently-corrected facts get a volatility flag.

The **report synthesis** path is separate: the consumer AI calls `kms_reports` with a report type, the pipeline gathers data per the YAML-defined report definition, sends it to the synthesis LLM, stores the result, and returns it.

---

## Spec Verification

The spec makes 9 explicit assumptions. Here is each one verified against the actual code.

| ID | Spec Claim | Verdict | Evidence |
|----|-----------|---------|----------|
| A1 | `_should_overwrite()` exists at `classify_writer.py:49`, accepts one positional arg (`existing_entry`), and is called at lines 212 and 277 in `write_entries()`. | **Validated** | `classify_writer.py:49` — `def _should_overwrite(existing_entry) -> bool:` returns True. Line 212: `if not _should_overwrite(entry):`. Line 277: `if not _should_overwrite(twin_entry):`. All three line references confirmed exact. |
| A2 | `correct_entry()` supports `edit_fact`, `change_tag`, `change_entity`, `promote`, `un_retire`, `retire` and returns `Result[dict]`. | **Validated** | `_correct.py:14-101` — signature `correct_entry(entry_id, operation, *, new_fact, new_tag, new_entity, reason, db_path) -> Result[dict]`. Operations handled: `retire` (line 48), `edit_fact` / `change_tag` / `change_entity` / `promote` / `un_retire` (lines 55-83). Returns `Success({"entry_id": ..., "operation": ..., "result": "applied"})`. |
| A3 | `entity_extract.yaml` has exactly 4 variables: `document_text`, `dimension_guidance`, `existing_facts`, `previous_attempt_feedback`. | **Validated** | `prompts/entity_extract.yaml:74` — `variables: [document_text, dimension_guidance, existing_facts, previous_attempt_feedback]`. Exactly 4 variables. Adding `few_shot_corrections` as a 5th is safe — the Jinja2 template just needs the new variable referenced and listed. |
| A4 | `SelfLearningConfig` has 5 fields (enabled, min_evaluations, confidence_threshold, include_examples_in_prompt, max_examples). | **Validated** | `core/config.py:298-305` — exactly 5 fields: `enabled: bool = True`, `min_evaluations: int = 20`, `confidence_threshold: float = Field(0.8)`, `include_examples_in_prompt: bool = True`, `max_examples: int = 5`. All have defaults, so adding 7 new fields with defaults is backward-compatible. |
| A5 | `retire()` contains explicit `facts_fts DELETE` and `facts_vec DELETE` statements at lines 269-277. | **Invalidated** | `knowledge_entries.py:269-296` — `retire()` starts at line 269 but the search-index cleanup is done by calling `_delete_search_indexes(conn, entry_id)` at line 292, not by inline DELETE statements. The helper `_delete_search_indexes()` at lines 69-72 contains the actual DELETE SQL. The line numbers are wrong and the mechanism is a helper function call, not inline SQL. The fix target is still correct (remove the `_delete_search_indexes` call from `retire()`), but the spec's description of "remove the facts_fts DELETE and facts_vec DELETE statements at lines 269-277" is inaccurate. |
| A6 | `_build_orientation_block()` and `_build_orientation_for_entities()` both query `knowledge_entries` with `WHERE status != 'retired'` but no trust-floor filter. | **Invalidated** (naming mismatch) | The method is named `_build_orientation_facts()` (line 276), not `_build_orientation_block()`. Neither `_build_orientation_facts` nor `_build_orientation_for_entities` query the DB directly — they both delegate to `query_ranked_for_orientation()` from `storage/knowledge_entries.py:366`, which has `WHERE status != 'retired'` and no trust-floor filter. The spec's factual claim about behavior is correct (no trust filter exists), but the method name is wrong. The trust-floor filter would be added to `query_ranked_for_orientation()` or as a parameter to it, not by modifying SQL inline in `context.py`. |
| A7 | `tools.py:register_tools()` registers exactly 5 tools (kms_vault_info, kms_search, kms_inspect, kms_write, kms_correct). | **Validated** | `tools.py:98-114` — `register_tools(mcp)` registers exactly 5 tools: `kms_vault_info`, `kms_search`, `kms_inspect`, `kms_write`, `kms_correct`. Adding 1-2 more follows the same `mcp.tool(description=...)(fn)` pattern. |
| A8 | Migration 012 is the latest migration. Migration 013 is the next available number. | **Validated** | `ls src/storage/migrations/` shows `012_fact_search_index.sql` is the latest. Migration 013 is the next available number. |
| A9 | `KnowledgeEntry.trust_score` defaults to 0.5 and is currently inert — no code reads or writes it except the DB default. | **Validated** (with nuance) | `knowledge_entries.py:39` — `trust_score: float = 0.5`. The `upsert()` function does NOT include `trust_score` in either its INSERT or UPDATE SQL (confirmed at lines 169-185 and 204-218). The DB column has `DEFAULT 0.5` (migration 010). **However**, `trust_score` IS read by: (a) `_row_to_entry()` at line 116, (b) `query_ranked_for_orientation()` ORDER BY at line 399, (c) `query_ranked_by_dimension()` ORDER BY at line 348, (d) `_join_entries()` in `fact_search.py` at line 296. So it is not fully "inert" — it is used in ranking and returned in results. What IS inert is that no code actively writes a non-default value. The spec's claim is essentially correct for the purpose of Phase 10 planning. |

---

## Suggested Research Item Answers

These are the 4 specific verification items from the spec's handoff notes.

### SR-1: Does `upsert()` SQL include `trust_score` in both INSERT and UPDATE paths?

**No.** This is a confirmed gap.

The `upsert()` function at `knowledge_entries.py:141-234` does NOT include `trust_score` in either path:

- **INSERT** (line 205-218): `INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning) VALUES (?, ?, ?, ?, ?, ?, ?, ?)` — 8 columns, no `trust_score`.
- **UPDATE** (line 169-185): `UPDATE knowledge_entries SET dimension=?, entity=?, tag=?, fact=?, status=?, confidence=?, sources=?, reasoning=?, updated_at=datetime('now') WHERE id=?` — 8 SET clauses, no `trust_score`.

New inserts get `trust_score = 0.5` from the column DEFAULT. Updates leave the existing `trust_score` untouched (which happens to be the desired behavior for OQ-P10-01, but it is accidental, not intentional).

**Impact on Phase 10:** Before the Trust Calculator can write adjusted trust scores, `upsert()` must be modified to include `trust_score` in both the INSERT and UPDATE SQL. The `KnowledgeEntry` dataclass already carries `trust_score` (line 39), so the change is adding the column to the SQL and the parameter tuple. This is a prerequisite for Components 4, 5, 6, 10, and 11.

### SR-2: How does the orchestrator obtain `doc_entities` for the few-shot selector?

**It does not have them pre-extraction.** The orchestrator at `classify_orchestrator.py:164-309` processes dimensions in a loop (line 249). For each dimension, it calls `extract()` which returns parsed fact dicts. Entity names appear only in the extraction results, not before.

The orchestrator does have access to `facts_by_dim` (line 215) — the existing knowledge entries loaded by `context_loader()`. These entries have `.entity` attributes. So the entities from *already-known facts* are available before extraction, but entities *newly mentioned in the current document* are not.

**Options for Phase 10:**
1. **Use existing facts' entities** — `[e.entity for e in facts_by_dim.get(dim_name, [])]`. Available before `extract()` is called. Does not capture new entities from the current document, but for teaching examples ("don't make this mistake again"), prior-dimension entities are the most relevant match anyway.
2. **Two-pass extraction** — extract entities from text first, then select corrections, then extract facts. Adds complexity and an extra LLM call. Overkill.
3. **Empty list on first pass** — pass `doc_entities=[]` and rely on dimension-matching alone for few-shot selection. The +3 dimension-match bonus in the selection algorithm would still rank relevant corrections highly.

**Recommendation:** Option 1. Use entities from the existing knowledge base for the current dimension. The few-shot selector's +2 entity-overlap bonus is a tie-breaker, not the primary ranking signal. Dimension match (+3) is the dominant factor.

### SR-3: Does `_join_entries()` WHERE clause correctly exclude retired entries?

**Yes.** Confirmed correct.

`fact_search.py:268-275` — the SQL is:
```sql
SELECT id, dimension, entity, fact, confidence, trust_score, retrieval_count, sources
FROM knowledge_entries
WHERE id IN (...)
  AND status != 'retired'
```

The `AND status != 'retired'` filter is in the JOIN query, not in the search queries. This means:
- FTS5 and vec0 indexes may contain retired entries (if the retire-fix from Phase 10 Component 2 is applied).
- Those retired entries are filtered out at the JOIN stage before results are returned.

This is the correct architecture for the retire-fix: retired entries stay in the search indexes (so "what did we used to think?" works), but `search_facts()` never returns them because `_join_entries()` filters on status.

### SR-4: Does P9 change `_build_orientation_block()` query structure?

**The method name changed.** P9 rewrote `context.py` entirely. The old `_build_orientation_block()` no longer exists. The replacement methods are:

1. `_build_orientation_facts()` (line 276) — iterates over all dimensions, calls `query_ranked_for_orientation(dimension=dim, limit=max_per_dim)` from `knowledge_entries.py`.
2. `_build_orientation_for_entities()` (line 328) — iterates over specific entities, calls `query_ranked_for_orientation(entity=entity, limit=max_per_dim)`.

Neither method contains inline SQL. Both delegate to `query_ranked_for_orientation()` which uses:
```sql
SELECT * FROM knowledge_entries WHERE status != 'retired'
  [AND dimension = ?] [AND entity = ?]
ORDER BY trust_score DESC, retrieval_count DESC, confidence DESC, updated_at DESC
LIMIT ?
```

**Trust-floor filter insertion point:** Add `AND trust_score >= ?` to `query_ranked_for_orientation()` as an optional parameter. The two context.py methods would pass `CONFIG.main.self_learning.min_trust_for_context` to this parameter. This is cleaner than modifying context.py's SQL (which does not exist — the SQL is in knowledge_entries.py).

---

## Edge Cases & Silent Failure Modes

1. **Trust score silently stays at 0.5 after corrections.** Because `upsert()` does not write `trust_score`, calling `adjust_trust()` and then `upsert()` with the adjusted score on the `KnowledgeEntry` object will have NO EFFECT — the UPDATE SQL ignores the field. This would be a silent correctness failure if not caught before implementation.

2. **Revise operation creates orphaned search entries.** The `revise` flow retires the old entry (which currently deletes search indexes) and creates a new entry (which adds search indexes). After the retire-fix removes search cleanup from `retire()`, the old entry's search indexes will persist alongside the new entry's — correct behavior for the "what did we used to think?" use case, but could cause stale FTS hits until the entry's status is filtered out in `_join_entries()`.

3. **`_should_overwrite` returns True even when guard should block.** The current seam at lines 212 and 277 does `if not _should_overwrite(entry): pass`. The `pass` means even after Phase 10 activates the guard (returns False), no new entry is created — the code falls through to the existing `ke_upsert()` call. The spec's Component 5 must replace the `pass` with actual "insert competing entry" logic.

4. **Orchestrator does not have doc_entities pre-extraction.** The few-shot selector needs entity names, but the orchestrator only has entities from previously-known facts, not from the current document. This limits the entity-overlap bonus in the selection algorithm but does not break it — dimension matching is the primary ranking signal.

---

## Dependencies & Coupling

Phase 10 has tight coupling with these modules:

- **`storage/knowledge_entries.py:upsert()`** — must be modified to write `trust_score`. This is a cross-cutting change that affects every caller of upsert.
- **`storage/knowledge_entries.py:query_ranked_for_orientation()`** — must gain an optional trust-floor parameter. Called by `context.py` methods.
- **`pipelines/classify_writer.py:_should_overwrite()`** — must gain threshold parameter and replace `pass` bodies at both call sites.
- **`pipelines/classify_orchestrator.py:orchestrate()`** — must wire few-shot selector calls before `extract()`. The loop body grows by ~5 lines per dimension.
- **`mcp_server/_correct.py:correct_entry()`** — must gain `reason_category`, `feedback` params and trust adjustment + correction recording. The function grows substantially.
- **`mcp_server/context.py`** — methods delegate to `query_ranked_for_orientation()`, so trust-floor changes are in knowledge_entries.py, not here. Volatility annotation requires per-entry correction count query.
- **`config/config.yaml`** — gains 7 new fields under `self_learning:`. Backward-compatible (all have defaults).

---

## Extension Points

The design correctly identifies several extension points:

- **Report types** — Adding a new report type is YAML-only (`config/reports.yaml`), no code changes. This follows the Extension Point Rule.
- **Correction operations** — Adding a new operation (e.g., `merge_entities`) requires code changes in `_correct.py`. Not purely extensible, but the operation dispatch is a simple if/elif chain.
- **Trust deltas** — All trust adjustment values are config-driven (`SelfLearningConfig`). Tuning does not require code changes.
- **Few-shot selection algorithm** — The scoring weights (+3 dimension, +2 entity, +1 recency) would be hardcoded in `few_shot.py`. Moving these to config is a possible future enhancement.

---

## Open Questions

1. **Should `upsert()` always write `trust_score`, or only when explicitly provided?** The current behavior (never write it) means the DB DEFAULT controls the value. If we add `trust_score` to every UPDATE, a classify pipeline update that does not set `trust_score` on the `KnowledgeEntry` would write 0.5 (the dataclass default), overwriting any user-adjusted value. **Recommendation:** Add `trust_score` to the UPDATE SET clause, and ensure `write_entries()` copies the existing row's `trust_score` onto the `KnowledgeEntry` before upserting (Component 6 in the spec already calls for this).

2. **Should the trust-floor filter be in `query_ranked_for_orientation()` or as a wrapper in `context.py`?** The spec says modify the context.py SQL, but context.py has no SQL — it delegates. **Recommendation:** Add an optional `min_trust: float | None = None` parameter to `query_ranked_for_orientation()` and add `AND trust_score >= ?` when provided. The context.py methods pass the config value through.

3. **Few-shot scoring weights** — The spec hardcodes +3/+2/+1. Should these be configurable? For the initial implementation, hardcoded is fine (only one consumer). Consider config if a second consumer appears.

---

## Technical Debt Spotted

1. **TD-P9-PERF-01/02 (context_loader re-reads per doc)** — Still unfixed. Phase 10 Component 9 adds comment loading to the context loader, adding another per-doc query. The spec's handoff notes correctly flag this intersection.

2. **`_should_overwrite` pass bodies** — Lines 213-214 and 278-279 in `classify_writer.py` have placeholder `pass` statements. These must be replaced with actual "insert competing entry" logic in Phase 10 Component 5. If someone forgets, the guard blocks the overwrite but then falls through to execute it anyway — a silent correctness bug.

3. **`KnowledgeEntry.retrieval_score` vs DB `retrieval_count`** — The dataclass field is named `retrieval_score` (line 40) but the DB column is `retrieval_count` (migration 010). The mapping happens in `_row_to_entry()` at line 117-119. This naming mismatch could cause confusion when Phase 10 adds `trust_score` writes.

---

## Invalidated Assumptions

### A5 — retire() search-index cleanup mechanism

**Spec claimed:** `retire()` in `knowledge_entries.py` "contains explicit `facts_fts DELETE` and `facts_vec DELETE` statements at lines 269-277."

**Code shows:** `knowledge_entries.py:292` — `retire()` calls `_delete_search_indexes(conn, entry_id)` (a helper function defined at lines 69-72) rather than containing inline DELETE statements. The helper does the actual `DELETE FROM facts_fts` and `DELETE FROM facts_vec`.

**Why this matters:** The spec's Component 2 says "remove the `facts_fts` DELETE and `facts_vec` DELETE statements (currently at lines 269-277)." The actual fix is to remove the `_delete_search_indexes(conn, entry_id)` call at line 292 (and the associated `if old:` check at line 291). The two lines to remove are 291-292, not "lines 269-277." The fix is simpler than described (remove 2 lines instead of ~8), but targets a different location.

**Suggested resolution:** Update the spec's Component 2 to say: "Remove the `_delete_search_indexes(conn, entry_id)` call at line 292 of `retire()` (and the guarding `if old:` at line 291). The function should only update status to 'retired' and set reasoning."

### A6 — _build_orientation_block() method name

**Spec claimed:** `_build_orientation_block()` and `_build_orientation_for_entities()` in `context.py` both query `knowledge_entries` with `WHERE status != 'retired'` but no trust-floor filter. The spec references `_build_orientation_block()` at line 297.

**Code shows:** The method is named `_build_orientation_facts()` at line 276, not `_build_orientation_block()`. Neither method contains SQL — they both delegate to `query_ranked_for_orientation()` in `storage/knowledge_entries.py:366-415`, which has `WHERE status != 'retired'` in its SQL at line 388.

**Why this matters:** The spec's Components 12 and 13 say "modify `_build_orientation_block()` in `context.py`" — this method does not exist. The correct approach is to either: (a) modify `query_ranked_for_orientation()` in `knowledge_entries.py` to accept a `min_trust` parameter, or (b) add the filter directly in context.py by wrapping the query call. Option (a) is cleaner because it keeps SQL in the storage layer.

**Suggested resolution:** Update all references from `_build_orientation_block()` to `_build_orientation_facts()`. Update Components 12 and 13 to target `query_ranked_for_orientation()` in `knowledge_entries.py` for the trust-floor filter, not inline SQL in `context.py`.

### SR-1 (not an assumption but a confirmed gap) — upsert() missing trust_score

**Spec handoff notes asked:** "Verify `upsert()` SQL includes `trust_score` in both INSERT and UPDATE paths."

**Code shows:** `trust_score` is absent from both paths. See SR-1 section above for full details.

**Why this matters:** This is a prerequisite for the entire trust system. Without it, `adjust_trust()` computes a new score, but `upsert()` ignores it. The spec's Component 6 (Trust score preservation) partially addresses this for the classify writer path, but the upsert SQL itself must also be fixed.

**Suggested resolution:** Add `trust_score` to both the INSERT column list and the UPDATE SET clause in `upsert()`. This should be a Phase 10 prerequisite step (before Component 4), not a side effect of Component 6.
