# Phase 10 — Self-Learning & Reports Backend: Implementation Plan

_Created: 2026-06-16_
_Input: Spec (`docs/2_specs/phase10_self_learning.md`) + Research corrections (4 items incorporated)_
_Build order: Prereq -> Migration -> Retire fix -> Config -> Trust calc -> Trust preservation + Overwrite guard -> Correction aliases -> Few-shot -> Comments -> Full correction recording -> Trust-floor -> Volatility -> Comment inspect -> Reports pipeline -> Report tool_

---

## Architecture Overview

Phase 10 closes the user feedback loop on AI-extracted knowledge. Users confirm, reject, or revise facts through MCP tools; the system records corrections, adjusts per-fact trust scores, and protects validated facts from silent AI overwrites. Past AI errors are fed back as few-shot teaching examples in future extraction prompts. On-demand reports synthesize knowledge health via the configured synthesis LLM. Five new tables (fact_corrections, reports, entry_comments) and two new MCP tools (kms_comment, kms_reports) are added. All thresholds and deltas are config-driven (C-06). The implementation is split into two slices: Slice 1 is P9-independent (trust calculator, overwrite guard, few-shot, comments, correction aliases), Slice 2 depends on P9's context engine (trust-floor filtering, volatility flags, full correction recording, reports).

---

## Research Corrections Applied

| Correction | What changed from spec | Where in plan |
|---|---|---|
| **A5 (INVALIDATED)** — `retire()` does NOT contain inline DELETE statements at lines 269-277. It calls `_delete_search_indexes(conn, entry_id)` helper at line 291, guarded by `if old:` at line 290. Fix = remove lines 290-291 only, not 8 lines of inline SQL. | Phase 2, retire search fix |
| **A6 (INVALIDATED)** — Method is `_build_orientation_facts()` at line 276, NOT `_build_orientation_block()`. Neither context.py method contains SQL — both delegate to `query_ranked_for_orientation()` in `knowledge_entries.py:366`. Trust-floor filter goes in `query_ranked_for_orientation()` as optional `min_trust` parameter, not inline in context.py. | Phase 9, trust-floor filtering |
| **SR-1 (CONFIRMED GAP)** — `upsert()` at `knowledge_entries.py:141` does NOT include `trust_score` in INSERT SQL (line 204-207: 8 columns, no trust_score) or UPDATE SQL (lines 169-172: 8 SET clauses, no trust_score). Must be fixed as prerequisite before trust scores can flow. | Phase 1, prerequisite fix |
| **`_should_overwrite` pass bodies** — Lines 213-214 and 278-279 in `classify_writer.py` have `pass` that falls through to `ke_upsert()`. The guard returns False but the overwrite executes anyway. Must replace `pass` with actual "insert competing entry" logic. | Phase 5, overwrite guard activation |

---

## Q3 Dependency Diagram

```
                     Phase 10 — Build Order
                     ======================

  ┌─────────────────────┐
  │ Ph1: upsert()       │ Prereq — trust_score in SQL
  │  trust_score fix    │
  └─────────┬───────────┘
            │
  ┌─────────▼───────────┐   ┌──────────────────────┐
  │ Ph2: Migration 013  │   │ Ph2b: Retire search  │
  │  3 new tables       │   │  fix (independent)   │
  └─────────┬───────────┘   └──────────────────────┘
            │
  ┌─────────▼───────────┐
  │ Ph3: Config ext     │ 7 new SelfLearningConfig fields
  └─────────┬───────────┘
            │
  ┌─────────▼───────────┐
  │ Ph4: Trust calc     │ adjust_trust() pure function
  │  src/pipelines/     │
  │  trust.py (NEW)     │
  └────┬────────────┬───┘
       │            │
  ┌────▼────┐  ┌────▼──────────────┐
  │ Ph5:    │  │ Ph6: Correction   │
  │ Overwr. │  │ aliases + trust   │
  │ Guard + │  │ in _correct.py    │
  │ Trust   │  └────────┬──────────┘
  │ Preserv │           │
  └────┬────┘    ┌──────▼──────────┐
       │         │ Ph8: Full       │
       │         │ correction      │  Slice 2
       │         │ recording       │  ──────
       │         └────────┬────────┘
  ┌────▼────────────┐     │
  │ Ph7: Few-shot   │     │
  │ system          │     │
  │ few_shot.py(NEW)│     │
  │ + prompt wiring │     │
  │ + orchestrator  │     │
  └─────────────────┘     │
                          │
  ┌──────────────────┐    │
  │ Ph7b: Comment    │    │
  │ system           │    │
  │ _comment.py(NEW) │    │
  │ + kms_comment    │    │
  │ + context loader │    │
  └────────┬─────────┘    │
           │              │
  ┌────────▼──────────────▼─┐
  │ Ph9: Context engine ext │
  │  trust-floor filter     │
  │  volatility flag        │
  │  comment in inspect     │
  └────────┬────────────────┘
           │
  ┌────────▼────────────────┐
  │ Ph10: Report synthesis  │
  │  reports.yaml (NEW)     │
  │  reports.py (NEW)       │
  │  kms_reports tool       │
  └─────────────────────────┘
```

**Legend:** Each box is one implementation phase. Arrows show "must complete before." Phases at the same horizontal level can be done in parallel if no arrow connects them. Ph2b (retire fix) is independent of all others. Slice 2 boundary is marked — components below it require P9 context engine to be complete.

---

## Implementation Phases

### Phase 1: Prerequisite — Add trust_score to upsert() SQL

_Spec components: SR-1 (research gap). Prerequisite for P10-SL-03, P10-SL-05, P10-SL-06._
_Exit criteria: `upsert()` writes trust_score to DB in both INSERT and UPDATE paths._

#### P10-PREREQ-01: Fix upsert() SQL

**File:** `src/storage/knowledge_entries.py`
**Behavior IDs:** SR-1

**What to do:**

1. In `upsert()` UPDATE path (line 168-185), add `trust_score=?` to the SET clause. Change:
   ```python
   cursor = conn.execute(
       """UPDATE knowledge_entries
          SET dimension=?, entity=?, tag=?, fact=?, status=?,
              confidence=?, sources=?, reasoning=?,
              updated_at=datetime('now')
          WHERE id=?""",
       (
           entry.dimension,
           entry.entity,
           entry.tag,
           entry.fact,
           status,
           entry.confidence,
           sources_json,
           entry.reasoning,
           entry.id,
       ),
   )
   ```
   To:
   ```python
   cursor = conn.execute(
       """UPDATE knowledge_entries
          SET dimension=?, entity=?, tag=?, fact=?, status=?,
              confidence=?, sources=?, reasoning=?,
              trust_score=?,
              updated_at=datetime('now')
          WHERE id=?""",
       (
           entry.dimension,
           entry.entity,
           entry.tag,
           entry.fact,
           status,
           entry.confidence,
           sources_json,
           entry.reasoning,
           entry.trust_score,
           entry.id,
       ),
   )
   ```

2. In `upsert()` INSERT path (line 203-218), add `trust_score` to both the column list and VALUES. Change:
   ```python
   cursor = conn.execute(
       """INSERT INTO knowledge_entries
          (dimension, entity, tag, fact, status, confidence,
           sources, reasoning)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
       (
           entry.dimension,
           entry.entity,
           entry.tag,
           entry.fact,
           status,
           entry.confidence,
           sources_json,
           entry.reasoning,
       ),
   )
   ```
   To:
   ```python
   cursor = conn.execute(
       """INSERT INTO knowledge_entries
          (dimension, entity, tag, fact, status, confidence,
           sources, reasoning, trust_score)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
       (
           entry.dimension,
           entry.entity,
           entry.tag,
           entry.fact,
           status,
           entry.confidence,
           sources_json,
           entry.reasoning,
           entry.trust_score,
       ),
   )
   ```

**Tests:**
- Unit: `upsert(KnowledgeEntry(trust_score=0.8, ...))` INSERT path -> `SELECT trust_score FROM knowledge_entries WHERE id=?` returns 0.8, not 0.5.
- Unit: `upsert(KnowledgeEntry(id=existing_id, trust_score=0.9, ...))` UPDATE path -> `SELECT trust_score` returns 0.9.
- Unit: `upsert(KnowledgeEntry(...))` with default trust_score -> `SELECT trust_score` returns 0.5 (default preserved).

---

### Phase 2: Migration 013 + Retire Search Fix

_Spec components: 1 (migration), 2 (retire fix)_
_Exit criteria: Three new tables exist. Schema version = 13. FK CASCADE works. Retired entries stay in search indexes._

#### P10-SL-04: Migration 013 — Self-Learning Tables

**File:** `src/storage/migrations/013_self_learning_tables.sql` (NEW)
**Behavior IDs:** P10-SL-04

**What to do:**

1. Create the migration file:
   ```sql
   -- Migration 013: Self-learning tables for Phase 10
   -- fact_corrections: records every correction operation with metadata
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

   CREATE INDEX IF NOT EXISTS idx_fc_entry ON fact_corrections(entry_id);
   CREATE INDEX IF NOT EXISTS idx_fc_reason ON fact_corrections(reason_category);

   -- reports: stores on-demand synthesized reports
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

   CREATE INDEX IF NOT EXISTS idx_reports_type ON reports(report_type, created_at DESC);

   -- entry_comments: additive annotations on knowledge entries
   CREATE TABLE IF NOT EXISTS entry_comments (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
       comment_text TEXT NOT NULL,
       created_at TEXT DEFAULT (datetime('now'))
   );

   CREATE INDEX IF NOT EXISTS idx_ec_entry ON entry_comments(entry_id);

   UPDATE schema_version SET version = 13;
   ```

2. Bump version-pin assertions in ALL migration test files from `12` to `13`. Files to update:
   - `tests/test_storage/test_migration_007.py` (lines 41, 56)
   - `tests/test_storage/test_migration_008.py` (line 49)
   - `tests/test_storage/test_migration_009.py` (line 38)
   - `tests/test_storage/test_migration_010.py` (line 16)
   - `tests/test_storage/test_migration_011.py` (line 15)
   - `tests/test_storage/test_migration_012.py` (line 15)

**Tests:**
- Migration test: after `init_db`, `fact_corrections`, `reports`, `entry_comments` tables exist (query `sqlite_master`).
- Schema version test: `SELECT version FROM schema_version` returns 13.
- FK cascade test: insert a knowledge entry, insert a fact_correction referencing it, delete the knowledge entry, verify the fact_correction row is also deleted.
- FK cascade test: same for entry_comments.

---

#### P10-SL-20: Retire Search Fix

**File:** `src/storage/knowledge_entries.py`
**Behavior IDs:** P10-SL-20

**What to do — RESEARCH CORRECTION A5 APPLIED: remove helper call, not inline SQL.**

1. In `retire()` (line 268), remove lines 290-291:
   ```python
   # REMOVE these two lines:
   if old:
       _delete_search_indexes(conn, entry_id)
   ```
   The `old = conn.execute(...)` SELECT at lines 277-280 can also be removed since it is only used by the deleted `_delete_search_indexes` call. However, keeping it is harmless and avoids changing line numbers of the return statement.

   After the fix, `retire()` only does:
   - UPDATE status='retired', reasoning=reason
   - Return rowcount

   The `_delete_search_indexes` helper function itself (lines 68-71) should NOT be deleted — it is still used by `_sync_search_indexes()` (line 54, `delete_old=True` path).

**Tests:**
- Unit: after `retire(entry_id)`, `SELECT status FROM knowledge_entries WHERE id=?` returns 'retired'.
- Unit: after `retire(entry_id)`, `SELECT * FROM facts_fts WHERE rowid=?` still returns a row (search index NOT deleted).
- Unit: after `retire(entry_id)`, `SELECT * FROM facts_vec WHERE entry_id=?` still returns a row.
- Unit: `search_facts(query)` does NOT return the retired entry (because `_join_entries` filters `AND status != 'retired'` at `fact_search.py:273`).

---

### Phase 3: Self-Learning Config Extension

_Spec components: 3_
_Exit criteria: 7 new config fields load from YAML with defaults. No float literals in pipeline code for these values._

#### P10-SL-05 (partial): Config Extension

**File:** `src/core/config.py`
**Behavior IDs:** P10-SL-05, P10-SL-12 (partial — max_corrections_per_prompt)

**What to do:**

1. Add 7 new fields to `SelfLearningConfig` (line 298-305). After the existing 5 fields, add:
   ```python
   class SelfLearningConfig(BaseModel):
       """Controls how the self-learning pipeline adjusts prompts (Roadmap 8)."""

       enabled: bool = True
       min_evaluations: int = 20
       confidence_threshold: float = Field(0.8, ge=0.0, le=1.0)
       include_examples_in_prompt: bool = True
       max_examples: int = 5
       # Phase 10 additions:
       trust_confirm_delta: float = Field(0.05, ge=0.0, le=1.0)
       trust_reject_delta: float = Field(-0.10, ge=-1.0, le=0.0)
       trust_revise_base: float = Field(0.6, ge=0.0, le=1.0)
       overwrite_trust_threshold: float = Field(0.5, ge=0.0, le=1.0)
       min_trust_for_context: float = Field(0.3, ge=0.0, le=1.0)
       volatility_correction_count: int = Field(3, ge=1)
       max_corrections_per_prompt: int = Field(5, ge=1)
   ```

2. Update `src/config/config.yaml` — add 7 new keys under `self_learning:` (after line 79):
   ```yaml
   self_learning:
     confidence_threshold: 0.8
     enabled: true
     include_examples_in_prompt: true
     max_examples: 5
     min_evaluations: 20
     trust_confirm_delta: 0.05
     trust_reject_delta: -0.10
     trust_revise_base: 0.6
     overwrite_trust_threshold: 0.5
     min_trust_for_context: 0.3
     volatility_correction_count: 3
     max_corrections_per_prompt: 5
   ```

**Tests:**
- Unit: `CONFIG.main.self_learning.trust_confirm_delta` returns 0.05.
- Unit: `CONFIG.main.self_learning.trust_reject_delta` returns -0.10.
- Unit: `CONFIG.main.self_learning.overwrite_trust_threshold` returns 0.5.
- Unit: `CONFIG.main.self_learning.min_trust_for_context` returns 0.3.
- Unit: `CONFIG.main.self_learning.volatility_correction_count` returns 3.
- Unit: `CONFIG.main.self_learning.max_corrections_per_prompt` returns 5.
- Unit: loading config.yaml without the new keys still works (defaults apply).

---

### Phase 4: Trust Calculator

_Spec components: 4_
_Exit criteria: Pure function computes trust deltas from config. No float literals. No DB access._

#### P10-SL-03, P10-SL-05: Trust Calculator

**File:** `src/pipelines/trust.py` (NEW)
**Behavior IDs:** P10-SL-03, P10-SL-05

**What to do:**

1. Create `src/pipelines/trust.py`:
   ```python
   """Trust Calculator — pure function for trust score adjustment.

   No DB access, no side effects. Reads deltas from config only.
   """

   from __future__ import annotations

   from core.config import SelfLearningConfig
   from core.result import Result, Success


   def adjust_trust(
       current_score: float,
       operation: str,
       config: SelfLearningConfig,
   ) -> Result[float]:
       """Compute the new trust score after a correction operation.

       Args:
           current_score: The entry's current trust_score (0.0-1.0).
           operation: One of "confirm", "reject", "revise".
           config: SelfLearningConfig with trust deltas.

       Returns:
           Success(new_score) clamped to [0.0, 1.0].
           Unknown operations return the current score unchanged.
       """
       if operation == "confirm":
           new = min(1.0, current_score + config.trust_confirm_delta)
       elif operation == "reject":
           new = max(0.0, current_score + config.trust_reject_delta)
       elif operation == "revise":
           new = config.trust_revise_base
       else:
           new = current_score
       return Success(new)
   ```

**Tests:**
- Unit: `adjust_trust(0.5, "confirm", config)` returns `Success(0.55)`.
- Unit: `adjust_trust(0.5, "reject", config)` returns `Success(0.40)`.
- Unit: `adjust_trust(0.5, "revise", config)` returns `Success(0.6)`.
- Unit: `adjust_trust(1.0, "confirm", config)` returns `Success(1.0)` (capped at 1.0).
- Unit: `adjust_trust(0.0, "reject", config)` returns `Success(0.0)` (floored at 0.0).
- Unit: `adjust_trust(0.5, "unknown_op", config)` returns `Success(0.5)` (unchanged).
- Unit: no float literals in function body — all values read from `config`.

---

### Phase 5: Trust Preservation + Overwrite Guard Activation

_Spec components: 5 (overwrite guard), 6 (trust preservation)_
_Exit criteria: Trust not reset on update. High-trust entries blocked from overwrite. Competing entry created when blocked._

#### P10-SL-06, P10-SL-07, OQ-P10-01: Trust Preservation and Overwrite Guard

**File:** `src/pipelines/classify_writer.py`
**Behavior IDs:** P10-SL-06, P10-SL-07

**What to do:**

1. **Trust preservation — UPDATE path** (line 163-200). The existing SELECT at line 163 fetches `entity, fact` from the row. Extend it to also fetch `trust_score`:
   ```python
   old = conn.execute(
       "SELECT entity, fact, trust_score FROM knowledge_entries WHERE id = ?",
       (entry.id,),
   ).fetchone()
   ```
   Then at line 198 where `KnowledgeEntry` is constructed, add `trust_score`:
   ```python
   entry = KnowledgeEntry(
       id=ref_id,
       dimension=dimension,
       entity=fact.get("entity", ""),
       tag=fact.get("tag", ""),
       fact=fact.get("fact", ""),
       confidence=float(fact.get("confidence", 0.5)),
       sources=_merge_sources(existing_sources, doc_id),
       reasoning=_merge_reasoning(existing_reasoning, fact.get("reason", "")),
       trust_score=float(old["trust_score"]) if old and "trust_score" in old.keys() else 0.5,
   )
   ```

2. **Trust preservation — twin-fold path** (lines 245-272). The twin SELECT at lines 247-250 currently fetches `sources, reasoning`. Extend to also fetch `trust_score`:
   ```python
   twin_row = conn.execute(
       "SELECT sources, reasoning, trust_score FROM knowledge_entries WHERE id = ?",
       (twin_id,),
   ).fetchone()
   ```
   Then at line 261 where `twin_entry = KnowledgeEntry(...)` is constructed, add:
   ```python
   trust_score=float(twin_row["trust_score"]) if twin_row and "trust_score" in twin_row.keys() else 0.5,
   ```

3. **Activate `_should_overwrite()`** (line 49). Change signature and logic:
   ```python
   def _should_overwrite(existing_entry, *, threshold: float) -> bool:
       """Return True if classify may overwrite the existing entry.

       Entries with trust_score > threshold are protected from silent overwrite.
       The default threshold (0.5) means only user-confirmed entries are protected.
       """
       return existing_entry.trust_score <= threshold
   ```

4. **Update call site 1 — update path** (line 212). Replace:
   ```python
   if not _should_overwrite(entry):
       # Phase 10: would write conflicting new entry instead
       pass  # Phase 9: always overwrites
   ```
   With:
   ```python
   if not _should_overwrite(entry, threshold=CONFIG.main.self_learning.overwrite_trust_threshold):
       # Trust exceeds threshold — insert competing entry instead of overwriting
       competing = KnowledgeEntry(
           dimension=dimension,
           entity=entry.entity,
           tag=entry.tag,
           fact=entry.fact,
           confidence=entry.confidence,
           sources=entry.sources,
           reasoning=entry.reasoning,
           trust_score=0.5,  # new entry starts at default trust
       )
       comp_result = ke_upsert(competing, status="pending", band=band, db_path=db_path)
       if isinstance(comp_result, Failure):
           summary.clean = False
           _writer_log.warning(
               "write_entries overwrite_blocked competing insert failed entry_id=%s error=%s",
               ref_id, comp_result.error,
           )
       else:
           _writer_log.info(
               "overwrite_blocked entry_id=%s trust=%.2f threshold=%.2f new_pending_id=%s",
               ref_id, entry.trust_score,
               CONFIG.main.self_learning.overwrite_trust_threshold,
               comp_result.value,
           )
       continue  # skip the original ke_upsert below
   ```
   Add `from core.config import CONFIG` import at top of file if not already present.

5. **Update call site 2 — twin-fold path** (line 277). Same pattern as call site 1:
   ```python
   if not _should_overwrite(twin_entry, threshold=CONFIG.main.self_learning.overwrite_trust_threshold):
       competing = KnowledgeEntry(
           dimension=dimension,
           entity=entity,
           tag=tag,
           fact=fact.get("fact", ""),
           confidence=confidence,
           sources=_merge_sources([], doc_id),
           reasoning=fact.get("reason", ""),
           trust_score=0.5,
       )
       comp_result = ke_upsert(competing, status="pending", band=band, db_path=db_path)
       if isinstance(comp_result, Failure):
           summary.clean = False
           _writer_log.warning(
               "write_entries twin overwrite_blocked competing insert failed twin_id=%s error=%s",
               twin_id, comp_result.error,
           )
       else:
           _writer_log.info(
               "overwrite_blocked twin_id=%s trust=%.2f threshold=%.2f new_pending_id=%s",
               twin_id, twin_entry.trust_score,
               CONFIG.main.self_learning.overwrite_trust_threshold,
               comp_result.value,
           )
       continue
   ```

**Tests:**
- Unit: `_should_overwrite(entry_with_trust_0_6, threshold=0.5)` returns False.
- Unit: `_should_overwrite(entry_with_trust_0_5, threshold=0.5)` returns True (equal is NOT protected).
- Unit: `_should_overwrite(entry_with_trust_0_3, threshold=0.5)` returns True.
- Integration: `write_entries([update_fact], ...)` where existing entry has trust 0.8 -> original entry unchanged, new pending entry created with same entity/tag.
- Integration: `write_entries([update_fact], ...)` where existing entry has trust 0.5 -> entry is updated normally.
- Integration: trust_score 0.8 on existing entry -> after `write_entries` update path, trust_score still 0.8 (not reset to 0.5).
- Integration: twin-fold path with trust 0.7 -> competing entry created, twin unchanged.

---

### Phase 6: Correction Operation Aliases + Trust Adjustment

_Spec components: 10 (Slice 1 aliases)_
_Exit criteria: confirm/reject/revise work as operations. Trust scores adjust correctly. Old P9 operations unchanged._

#### P10-SL-01, P10-SL-03: Correction Aliases with Trust

**File:** `src/mcp_server/_correct.py`
**Behavior IDs:** P10-SL-01, P10-SL-03

**What to do:**

1. Add imports at top:
   ```python
   from core.config import CONFIG
   from pipelines.trust import adjust_trust
   ```

2. After the `match get_entry_by_id(...)` block (line 38-45), capture the entry's trust_score:
   ```python
   old_trust = entry.trust_score
   ```

3. Add `confirm` alias after the existing operation dispatch (insert before line 88 `else:` block):
   ```python
   elif operation == "confirm":
       # Confirm = promote + trust bump
       fields = asdict(entry)
       fields["status"] = "confident"
       trust_result = adjust_trust(old_trust, "confirm", CONFIG.main.self_learning)
       fields["trust_score"] = trust_result.unwrap()
       updated = type(entry)(**fields)
       match upsert(updated, db_path=db_path):
           case Failure() as f:
               return f

   elif operation == "reject":
       # Reject = retire + trust drop
       trust_result = adjust_trust(old_trust, "reject", CONFIG.main.self_learning)
       new_trust = trust_result.unwrap()
       # Write trust before retiring (retire does not call upsert)
       fields = asdict(entry)
       fields["trust_score"] = new_trust
       updated = type(entry)(**fields)
       match upsert(updated, db_path=db_path):
           case Failure() as f:
               return f
       if reason is None:
           reason = "Rejected by user"
       match retire(entry_id, reason, db_path=db_path):
           case Failure() as f:
               return f

   elif operation == "revise":
       if new_fact is None:
           return Failure("new_fact is required for revise", recoverable=False)
       # Retire old entry
       match retire(entry_id, reason or "Revised by user", db_path=db_path):
           case Failure() as f:
               return f
       # Create new entry with revised fact at trust_revise_base
       trust_result = adjust_trust(old_trust, "revise", CONFIG.main.self_learning)
       new_entry = type(entry)(
           id=None,  # new row
           dimension=entry.dimension,
           entity=entry.entity,
           tag=entry.tag,
           fact=new_fact,
           confidence=entry.confidence,
           sources=entry.sources,
           reasoning=reason or f"Revised from entry {entry_id}",
           trust_score=trust_result.unwrap(),
       )
       match upsert(new_entry, db_path=db_path):
           case Failure() as f:
               return f
           case Success(value=new_id):
               pass
   ```

4. Update the existing `promote` handler (line 78-79) to also bump trust:
   ```python
   elif operation == "promote":
       fields["status"] = "confident"
       trust_result = adjust_trust(old_trust, "confirm", CONFIG.main.self_learning)
       fields["trust_score"] = trust_result.unwrap()
   ```

5. Update the return value for `revise` to include the new entry id:
   ```python
   # At the end, for revise operations:
   if operation == "revise":
       return Success({"entry_id": entry_id, "operation": operation, "result": "applied", "new_entry_id": new_id})
   ```

**Tests:**
- Unit: `correct_entry(5, "confirm")` -> entry trust_score is old_trust + 0.05. Status = "confident".
- Unit: `correct_entry(5, "reject", reason="wrong")` -> entry trust_score drops by 0.10. Status = "retired".
- Unit: `correct_entry(5, "revise", new_fact="corrected text")` -> old entry retired, new entry created with trust 0.6 and the corrected fact text.
- Unit: `correct_entry(5, "revise")` without new_fact -> Failure("new_fact is required for revise").
- Unit: `correct_entry(5, "promote")` still works and now bumps trust.
- Unit: `correct_entry(5, "edit_fact", new_fact="x")` still works unchanged (no trust adjustment for P9 ops).
- Unit: Old operations (retire, change_tag, change_entity, un_retire) still work unchanged.
- Unit: `revise` preserves sources list from old entry on new entry.

---

### Phase 7: Few-Shot System

_Spec components: 7, 8_
_Exit criteria: Past ai_error corrections injected into extraction prompt. Selection is relevance-ranked. Cap from config._

#### P10-SL-10, P10-SL-11, P10-SL-12: Few-Shot Selector + Prompt Wiring

**Files:** `src/pipelines/few_shot.py` (NEW), `src/prompts/entity_extract.yaml`, `src/pipelines/classify_extract.py`, `src/pipelines/classify_orchestrator.py`
**Behavior IDs:** P10-SL-10, P10-SL-11, P10-SL-12

**What to do:**

1. **Create `src/pipelines/few_shot.py`:**
   ```python
   """Few-shot Correction Selector — picks relevant past AI errors as teaching examples."""

   from __future__ import annotations

   from pathlib import Path

   from core.result import Failure, Result, Success
   from storage.db import get_connection


   def select_corrections(
       dimension: str,
       doc_entities: list[str],
       *,
       cap: int,
       db_path: Path | None = None,
   ) -> Result[list[dict]]:
       """Select the most relevant ai_error corrections for a dimension.

       Selection algorithm:
       1. Query fact_corrections WHERE reason_category = 'ai_error',
          joined with knowledge_entries for dimension/entity.
       2. Score: +3 dimension match, +2 entity overlap, +1 recency.
       3. Sort descending, take top cap.

       Returns list of dicts: {old_fact, new_fact, feedback, dimension, entity}.
       """
       try:
           import sqlite3

           with get_connection(db_path, readonly=True) as conn:
               conn.row_factory = sqlite3.Row
               rows = conn.execute(
                   """SELECT fc.old_fact, fc.new_fact, fc.feedback,
                          ke.dimension, ke.entity, fc.created_at
                   FROM fact_corrections fc
                   JOIN knowledge_entries ke ON fc.entry_id = ke.id
                   WHERE fc.reason_category = 'ai_error'
                   ORDER BY fc.created_at DESC""",
               ).fetchall()

           if not rows:
               return Success([])

           entity_set = set(e.lower() for e in doc_entities)

           scored: list[tuple[float, dict]] = []
           for idx, row in enumerate(rows):
               score = 0.0
               if row["dimension"] == dimension:
                   score += 3.0
               if row["entity"] and row["entity"].lower() in entity_set:
                   score += 2.0
               # Recency: position penalty (first = most recent)
               score += max(0, 1.0 - idx * 0.1)

               scored.append((score, {
                   "old_fact": row["old_fact"] or "",
                   "new_fact": row["new_fact"] or "",
                   "feedback": row["feedback"] or "",
                   "dimension": row["dimension"] or "",
                   "entity": row["entity"] or "",
               }))

           scored.sort(key=lambda x: x[0], reverse=True)
           return Success([item[1] for item in scored[:cap]])

       except Exception as exc:
           return Failure(str(exc), recoverable=True, context={"dimension": dimension})


   def format_few_shot(corrections: list[dict]) -> str:
       """Format corrections as teaching text for the extraction prompt.

       Returns empty string if corrections list is empty.
       """
       if not corrections:
           return ""

       lines = ["Previous extraction mistakes to avoid:"]
       for c in corrections:
           line = f'- For [{c["entity"]}] in [{c["dimension"]}]: The AI incorrectly extracted "{c["old_fact"]}".'
           if c["new_fact"]:
               line += f' The correct fact is "{c["new_fact"]}".'
           if c["feedback"]:
               line += f' {c["feedback"]}'
           lines.append(line)

       return "\n".join(lines)
   ```

2. **Update `src/prompts/entity_extract.yaml`** — add `few_shot_corrections` variable. Insert BEFORE the dimension guidance section (before line 51 `Knowledge category`). Add after line 49 (before `user: |` content):
   ```yaml
   user: |
     {% if few_shot_corrections %}
     {{ few_shot_corrections }}

     {% endif %}
     Knowledge category (dimension):
     {{ dimension_guidance }}
   ```
   And update line 74 variables list:
   ```yaml
   variables: [document_text, dimension_guidance, existing_facts, previous_attempt_feedback, few_shot_corrections]
   ```

3. **Update `src/pipelines/classify_extract.py`** — add `few_shot_corrections` parameter to `extract()` (line 81):
   ```python
   async def extract(
       dimension: str,
       text: str,
       existing_facts: list,
       guidance: str,
       feedback: str,
       config: MainConfig,
       few_shot_corrections: str = "",
   ) -> Result[list[dict]]:
   ```
   Pass `few_shot_corrections` to the prompt renderer alongside the other variables. Find where the prompt variables are assembled (the dict passed to the template renderer) and add `"few_shot_corrections": few_shot_corrections`.

4. **Update `src/pipelines/classify_orchestrator.py`** — wire few-shot before each `extract()` call. In the per-dimension loop (line 249), BEFORE the `extract()` call at line 255:
   ```python
   # Few-shot correction injection (Phase 10)
   few_shot_text = ""
   if CONFIG.main.self_learning.enabled:
       from pipelines.few_shot import select_corrections, format_few_shot

       doc_entities = [e.entity for e in existing]
       sel_result = select_corrections(
           dim_name,
           doc_entities,
           cap=CONFIG.main.self_learning.max_corrections_per_prompt,
           db_path=db_path,
       )
       if isinstance(sel_result, Success) and sel_result.value:
           few_shot_text = format_few_shot(sel_result.value)
   ```
   Then update the `extract()` call at line 255 to pass it:
   ```python
   extracted = await extract(
       dim_name,
       text,
       existing,
       guidance_text,
       feedback=last_error or "",
       config=config,
       few_shot_corrections=few_shot_text,
   )
   ```

**Tests:**
- Unit: `select_corrections("people", ["Anthony"], cap=5, db_path=...)` with 10 ai_error corrections -> returns 5, people-dimension + Anthony-entity corrections ranked highest.
- Unit: `select_corrections(...)` with no ai_error corrections -> returns empty list.
- Unit: `select_corrections(...)` never returns `stale_source` corrections (only `ai_error`).
- Unit: `format_few_shot([])` returns empty string.
- Unit: `format_few_shot([{"old_fact": "X", "new_fact": "Y", "feedback": "Z", "dimension": "people", "entity": "A"}])` returns text containing "Previous extraction mistakes to avoid:".
- Integration: with corrections in DB, a classify run's prompt includes the few-shot section.
- Integration: with no corrections, prompt is unchanged (empty string injected, Jinja `{% if %}` skips it).
- Unit: cap from config is respected — with 10 corrections and cap=3, only 3 returned.

---

### Phase 7b: Comment System

_Spec components: 9_
_Exit criteria: Comments stored in DB. kms_comment tool works. Context loader includes comments._

#### P10-SL-17, P10-SL-18: Comment Backing + Tool + Context Integration

**Files:** `src/mcp_server/_comment.py` (NEW), `src/mcp_server/tools.py`, `src/pipelines/classify.py`
**Behavior IDs:** P10-SL-17, P10-SL-18

**What to do:**

1. **Create `src/mcp_server/_comment.py`:**
   ```python
   """kms_comment backing — add comments to knowledge entries."""

   from __future__ import annotations

   from pathlib import Path

   from core.result import Failure, Result, Success


   def add_comment(
       entry_id: int,
       text: str,
       *,
       db_path: Path | None = None,
   ) -> Result[dict]:
       """Add a comment to a knowledge entry.

       Validates entry exists, inserts into entry_comments table.
       Returns Success({"comment_id": ..., "entry_id": ...}).
       """
       import sqlite3
       from storage.knowledge_entries import get_entry_by_id
       from storage.db import get_connection

       match get_entry_by_id(entry_id, db_path=db_path):
           case Success(value=None):
               return Failure(f"Entry {entry_id} not found", recoverable=False)
           case Failure() as f:
               return f
           case Success():
               pass

       try:
           with get_connection(db_path) as conn:
               cursor = conn.execute(
                   "INSERT INTO entry_comments (entry_id, comment_text) VALUES (?, ?)",
                   (entry_id, text),
               )
               return Success({"comment_id": cursor.lastrowid, "entry_id": entry_id})
       except sqlite3.Error as exc:
           return Failure(str(exc), recoverable=False, context={"entry_id": entry_id})
   ```

2. **Register `kms_comment` in `tools.py`** — add after `kms_correct` registration (line 114). Add import at top:
   ```python
   from mcp_server import _comment
   ```
   Then in `register_tools()`:
   ```python
   mcp.tool(
       description="Add a comment to a knowledge entry. Comments are additive annotations visible in future extraction context."
   )(kms_comment)
   ```
   And define the tool function (alongside other tool functions, before `register_tools`):
   ```python
   def kms_comment(entry_id: int, text: str) -> dict:
       return _comment.add_comment(entry_id=entry_id, text=text, db_path=_db_path()).unwrap()
   ```
   Note: check how `_db_path()` is obtained in existing tools (e.g., `kms_correct`) and follow the same pattern.

3. **Context loader integration** — In `src/pipelines/classify.py:context_loader()` (line 92-149), after loading ranked facts per dimension, also load comments for entities in those facts. After the `for dim_name in rulebook:` loop that builds `result`, add a post-processing step:
   ```python
   # Load comments for entries in context
   import sqlite3 as _sqlite3
   from storage.db import get_connection as _get_conn

   try:
       with _get_conn(db_path, readonly=True) as conn:
           conn.row_factory = _sqlite3.Row
           for dim_name, entries in result.items():
               for entry in entries:
                   if entry.id is not None:
                       comments = conn.execute(
                           "SELECT comment_text FROM entry_comments WHERE entry_id = ? ORDER BY created_at",
                           (entry.id,),
                       ).fetchall()
                       if comments:
                           comment_texts = [r["comment_text"] for r in comments]
                           # Append comments to reasoning field for context
                           entry.reasoning = (entry.reasoning or "") + "\nComments: " + "; ".join(comment_texts)
   except _sqlite3.Error:
       pass  # Best-effort — comments are supplementary context
   ```

   Note: The exact integration mechanism may need adjustment based on how `existing_facts` are formatted in the orchestrator. The key requirement is that comments appear in the extraction context for their entity's dimension.

**Tests:**
- Unit: `add_comment(entry_id=5, text="This person left")` returns `Success({"comment_id": ..., "entry_id": 5})`.
- Unit: `add_comment(entry_id=99999, text="x")` returns `Failure("Entry 99999 not found")`.
- Unit: After adding a comment, `SELECT * FROM entry_comments WHERE entry_id=5` returns the comment.
- Unit: `kms_comment` tool is registered and callable.
- Integration: After adding a comment to entry 5, `context_loader()` returns entries where the commented entry's context includes the comment text.

4. **Update `src/mcp_server/AI_INSTRUCTIONS.md`** — Add `kms_comment` to the tool inventory table and add a comment workflow section:
   - Tool inventory row: `| kms_comment | Add a comment to a knowledge entry. Additive annotation visible in future extraction context. |`
   - Update "Five tools" → "Six tools" in the intro line.
   - Add a "Comments" section after "Correct vs write routing":
     ```markdown
     ## Comments

     `kms_comment` adds a lightweight annotation to a knowledge entry by integer `entry_id`. Comments are additive-only (no edit, no delete). They appear in the extraction context for future classify runs, influencing how the AI interprets that entity. Use when the user wants to add context without changing the fact itself.
     ```

---

### Phase 8: Full Correction Recording

_Spec components: 11 (Slice 2)_
_Exit criteria: Every correction writes a row to fact_corrections with full metadata._

#### P10-SL-01, P10-SL-02: Correction Recording

**File:** `src/mcp_server/_correct.py`, `src/mcp_server/tools.py`
**Behavior IDs:** P10-SL-01, P10-SL-02

**What to do:**

1. Add `reason_category` and `feedback` keyword parameters to `correct_entry()` (line 14):
   ```python
   def correct_entry(
       entry_id: int,
       operation: str,
       *,
       new_fact: str | None = None,
       new_tag: str | None = None,
       new_entity: str | None = None,
       reason: str | None = None,
       reason_category: str | None = None,
       feedback: str | None = None,
       db_path: Path | None = None,
   ) -> Result[dict]:
   ```

2. After applying the operation and adjusting trust, before the audit section, INSERT into `fact_corrections`:
   ```python
   # Record correction in fact_corrections table
   import sqlite3 as _sqlite3
   from storage.db import get_connection

   try:
       # Determine new trust and new fact text for snapshot
       match get_entry_by_id(entry_id, db_path=db_path):
           case Success(value=updated_entry) if updated_entry is not None:
               new_trust = updated_entry.trust_score
               snapshot_new_fact = updated_entry.fact
           case _:
               new_trust = old_trust  # entry may be retired/gone
               snapshot_new_fact = new_fact

       with get_connection(db_path) as conn:
           conn.execute(
               """INSERT INTO fact_corrections
                  (entry_id, operation, reason_category, feedback,
                   old_fact, new_fact, old_trust_score, new_trust_score)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
               (
                   entry_id,
                   operation,
                   reason_category,
                   feedback,
                   entry.fact,  # old fact snapshot (from original entry loaded at top)
                   snapshot_new_fact if operation in ("edit_fact", "revise") else None,
                   old_trust,
                   new_trust,
               ),
           )
   except _sqlite3.Error:
       pass  # Best-effort recording — do not fail the correction itself
   ```

3. Update `kms_correct` in `tools.py` to accept and pass through `reason_category` and `feedback`. Find the `kms_correct` function definition and add the parameters:
   ```python
   def kms_correct(
       entry_id: int,
       operation: str,
       new_fact: str | None = None,
       new_tag: str | None = None,
       new_entity: str | None = None,
       reason: str | None = None,
       reason_category: str | None = None,
       feedback: str | None = None,
   ) -> dict:
   ```
   Pass `reason_category=reason_category, feedback=feedback` to `correct_entry()`.

**Tests:**
- Unit: After `correct_entry(5, "reject", reason_category="ai_error", feedback="confused two people")`, `SELECT * FROM fact_corrections WHERE entry_id=5` returns a row with operation="reject", reason_category="ai_error", feedback="confused two people", old_fact populated, old_trust_score and new_trust_score populated.
- Unit: After `correct_entry(5, "confirm")`, fact_corrections row has operation="confirm", new_fact=NULL, old_trust and new_trust differ by confirm_delta.
- Unit: After `correct_entry(5, "revise", new_fact="corrected")`, fact_corrections row has old_fact=original, new_fact="corrected".
- Unit: `kms_correct` tool accepts reason_category and feedback parameters.
- Unit: Correction recording failure does not cause the correction itself to fail (best-effort).

---

### Phase 9: Context Engine Extensions

_Spec components: 12 (trust-floor), 13 (volatility), 14 (comment inspect)_
_Exit criteria: Low-trust entries excluded from orientation. Frequently-corrected entries flagged. Comments visible in inspect._

#### P10-SL-08: Trust-Floor Filtering

**File:** `src/storage/knowledge_entries.py`, `src/mcp_server/context.py`
**Behavior IDs:** P10-SL-08

**What to do — RESEARCH CORRECTION A6 APPLIED: filter in `query_ranked_for_orientation()`, not in context.py.**

1. Add optional `min_trust` parameter to `query_ranked_for_orientation()` (line 365):
   ```python
   def query_ranked_for_orientation(
       *,
       dimension: str | None = None,
       entity: str | None = None,
       limit: int = 5,
       min_trust: float | None = None,
       db_path: Path | None = None,
   ) -> Result[list[KnowledgeEntry]]:
   ```

2. In the SQL construction (line 387-401), add trust filter when provided:
   ```python
   query = "SELECT * FROM knowledge_entries WHERE status != 'retired'"
   params: list[str] = []

   if min_trust is not None:
       query += " AND trust_score >= ?"
       params.append(str(min_trust))

   if dimension is not None:
       query += " AND dimension = ?"
       params.append(dimension)
   if entity is not None:
       query += " AND entity = ?"
       params.append(entity)
   ```

3. In `context.py:_build_orientation_facts()` (line 276), pass `min_trust` when calling `query_ranked_for_orientation()` at line 302:
   ```python
   match query_ranked_for_orientation(
       dimension=dim,
       limit=max_per_dim,
       min_trust=CONFIG.main.self_learning.min_trust_for_context,
       db_path=db_path,
   ):
   ```

4. In `context.py:_build_orientation_for_entities()` (line 328), same change at line 355:
   ```python
   match query_ranked_for_orientation(
       entity=entity,
       limit=max_per_dim,
       min_trust=CONFIG.main.self_learning.min_trust_for_context,
       db_path=db_path,
   ):
   ```

**Tests:**
- Unit: `query_ranked_for_orientation(dimension="people", min_trust=0.3)` excludes entries with trust_score < 0.3.
- Unit: `query_ranked_for_orientation(dimension="people", min_trust=None)` returns all non-retired entries (backward compatible).
- Integration: Entry with trust_score 0.2 does NOT appear in `kms_vault_info` orientation blocks.
- Integration: Same entry IS returned by `search_facts()` (fact_search has no trust filter).

---

#### P10-SL-09: Volatility Flag

**File:** `src/mcp_server/context.py`
**Behavior IDs:** P10-SL-09

**What to do:**

1. In `_build_orientation_facts()` (line 276), after fetching entries for a dimension, batch-query correction counts and annotate. Insert BEFORE the fact-formatting loop (line 311):
   ```python
   # Batch query correction counts for volatility flag
   import sqlite3 as _sqlite3
   entry_ids = [e.id for e in entries if e.id is not None]
   correction_counts: dict[int, int] = {}
   if entry_ids:
       try:
           with get_connection(db_path, readonly=True) as vol_conn:
               placeholders = ", ".join("?" for _ in entry_ids)
               vol_rows = vol_conn.execute(
                   f"SELECT entry_id, COUNT(*) as cnt FROM fact_corrections WHERE entry_id IN ({placeholders}) GROUP BY entry_id",
                   entry_ids,
               ).fetchall()
               correction_counts = {r[0]: r[1] for r in vol_rows}
       except Exception:
           pass  # Best-effort
   volatility_threshold = CONFIG.main.self_learning.volatility_correction_count
   ```

2. Modify the fact bullet formatting (line 313-315). Change:
   ```python
   lines.append(
       f"- [{entry.entity}] {entry.fact} "
       f"(confidence: {entry.confidence})"
   )
   ```
   To:
   ```python
   bullet = (
       f"- [{entry.entity}] {entry.fact} "
       f"(confidence: {entry.confidence})"
   )
   if correction_counts.get(entry.id, 0) >= volatility_threshold:
       bullet += " [frequently corrected]"
   lines.append(bullet)
   ```

3. Apply same pattern in `_build_orientation_for_entities()` (line 328). Add batch correction count query before the entity loop, and append `[frequently corrected]` flag to the bullet at line 372-374.

**Tests:**
- Unit: Entry with 4 corrections (threshold=3) appears in orientation as `- [Entity] Fact (confidence: 0.85) [frequently corrected]`.
- Unit: Entry with 2 corrections appears without the flag.
- Unit: Entry with 0 corrections appears without the flag.
- Unit: Volatility threshold read from config (not hardcoded).

---

#### P10-SL-19: Comment Visibility in Inspect

**File:** `src/mcp_server/_resolve.py`
**Behavior IDs:** P10-SL-19

**What to do:**

1. Note: `kms_inspect` inspects documents (via `_resolve.py`), not knowledge entries directly. Comment visibility on knowledge entries requires either: (a) extending `kms_inspect` to also accept entry IDs, or (b) adding comments to the `kms_search` fact results when an entry is returned.

   The spec says "modify kms_inspect response assembly" — but inspect currently resolves documents, not entries. The most natural fit is to add a `get_entry_comments()` query function in `_comment.py` and expose it through a new field in fact search results or via the kms_correct/kms_inspect response.

   Practical approach: Add a `get_comments(entry_id, db_path)` function to `_comment.py` that returns comments for an entry. Then modify the `kms_correct` response to include recent comments on the corrected entry, so the consumer AI sees them in context.

2. Add to `src/mcp_server/_comment.py`:
   ```python
   def get_comments(
       entry_id: int,
       *,
       db_path: Path | None = None,
   ) -> Result[list[dict]]:
       """Retrieve all comments for a knowledge entry."""
       import sqlite3
       from storage.db import get_connection

       try:
           with get_connection(db_path, readonly=True) as conn:
               conn.row_factory = sqlite3.Row
               rows = conn.execute(
                   "SELECT id, comment_text, created_at FROM entry_comments WHERE entry_id = ? ORDER BY created_at",
                   (entry_id,),
               ).fetchall()
               return Success([
                   {"comment_id": r["id"], "text": r["comment_text"], "created_at": r["created_at"]}
                   for r in rows
               ])
       except sqlite3.Error as exc:
           return Failure(str(exc), recoverable=False, context={"entry_id": entry_id})
   ```

3. In `_correct.py`, after applying a correction, include comments in the response:
   ```python
   # Include comments in response
   from mcp_server._comment import get_comments
   comments_result = get_comments(entry_id, db_path=db_path)
   comments = comments_result.value if isinstance(comments_result, Success) else []

   return Success({
       "entry_id": entry_id,
       "operation": operation,
       "result": "applied",
       "comments": comments,
   })
   ```

**Tests:**
- Unit: `get_comments(entry_id=5)` returns list of comment dicts with comment_id, text, created_at.
- Unit: `get_comments(entry_id=5)` with no comments returns empty list.
- Unit: After `correct_entry(5, "confirm")`, response includes `"comments"` key with any existing comments.

---

### Phase 10: Report Synthesis

_Spec components: 15 (pipeline), 16 (tool)_
_Exit criteria: On-demand reports synthesized by LLM. Stored in reports table. kms_reports tool works. New report type = YAML only._

#### P10-SL-13, P10-SL-14, P10-SL-15, P10-SL-16: Report Pipeline + Tool

**Files:** `src/config/reports.yaml` (NEW), `src/pipelines/reports.py` (NEW), `src/mcp_server/tools.py`
**Behavior IDs:** P10-SL-13, P10-SL-14, P10-SL-15, P10-SL-16

**What to do:**

1. **Create `src/config/reports.yaml`** with 5 default report types:
   ```yaml
   report_types:
     correction_summary:
       title: "Correction Summary"
       prompt: |
         Analyze the following correction data and produce a concise report covering:
         - Total corrections by type (confirm, reject, revise)
         - Most-corrected entities
         - Accuracy trend (are corrections decreasing over time?)
         Provide specific numbers and actionable insights.
       sources: [corrections]
       filters: {}

     knowledge_health:
       title: "Knowledge Health Report"
       prompt: |
         Analyze the following knowledge base data and produce a health report covering:
         - Total facts by dimension
         - Count of pending vs confident vs retired entries
         - Coverage metrics (dimensions with few facts)
         - Overall knowledge base quality assessment
       sources: [facts]
       filters: {}

     volatile_entries:
       title: "Volatile Entries Report"
       prompt: |
         Analyze the following entries that have been corrected multiple times.
         For each volatile entry, explain:
         - How many times it was corrected and why
         - Whether the current value seems stable
         - Whether the entry should be reviewed by a human
       sources: [corrections, facts]
       filters: {}

     coverage_gaps:
       title: "Coverage Gaps Report"
       prompt: |
         Analyze the following data about documents and extracted knowledge.
         Identify:
         - Documents with zero knowledge entries extracted
         - Dimensions with significantly fewer facts than others
         - Entities mentioned in documents but absent from the knowledge base
       sources: [facts, summaries]
       filters: {}

     conflicts:
       title: "Conflicts Report"
       prompt: |
         Analyze the following pairs of knowledge entries where conflicts exist
         (same entity + dimension + tag with both trusted and pending status).
         For each conflict:
         - Show both the trusted and pending versions
         - Explain the likely source of the conflict
         - Recommend which version to keep
       sources: [facts]
       filters: { detect_conflicts: true }
   ```

2. **Create `src/pipelines/reports.py`:**
   ```python
   """Report Synthesis Pipeline — on-demand knowledge health reports.

   Report definitions are data (YAML). Adding a new report type
   requires zero code changes (extension point rule).
   """

   from __future__ import annotations

   import json
   import sqlite3
   from pathlib import Path

   import yaml

   from core.config import MainConfig
   from core.result import Failure, Result, Success
   from storage.db import get_connection


   def synthesize_report(
       report_type: str,
       *,
       config: MainConfig,
       db_path: Path | None = None,
   ) -> Result[dict]:
       """Generate a report by type.

       Steps:
       1. Load report definition from config/reports.yaml.
       2. Gather data per sources config.
       3. Render prompt with gathered data as context.
       4. Call synthesis LLM.
       5. Store result in reports table.
       6. Return report dict.
       """
       # 1. Load report definition
       reports_path = Path(__file__).resolve().parent.parent / "config" / "reports.yaml"
       try:
           with open(reports_path) as f:
               report_defs = yaml.safe_load(f)
       except Exception as exc:
           return Failure(f"Cannot load reports.yaml: {exc}", recoverable=False)

       if report_type not in report_defs.get("report_types", {}):
           return Failure(
               f"Unknown report type: {report_type}. Available: {list(report_defs['report_types'].keys())}",
               recoverable=False,
           )

       definition = report_defs["report_types"][report_type]
       title = definition["title"]
       prompt_template = definition["prompt"]
       sources = definition.get("sources", [])
       filters = definition.get("filters", {})

       # 2. Gather data
       context_parts: list[str] = []
       source_ids: list[int] = []

       try:
           with get_connection(db_path, readonly=True) as conn:
               conn.row_factory = sqlite3.Row

               if "facts" in sources:
                   rows = conn.execute(
                       "SELECT id, dimension, entity, tag, fact, status, confidence, trust_score "
                       "FROM knowledge_entries ORDER BY dimension, entity"
                   ).fetchall()
                   facts_text = _format_facts(rows)
                   context_parts.append(facts_text)
                   source_ids.extend(r["id"] for r in rows)

                   # Conflict detection
                   if filters.get("detect_conflicts"):
                       conflicts = _detect_conflicts(conn)
                       if conflicts:
                           context_parts.append(_format_conflicts(conflicts))

               if "corrections" in sources:
                   rows = conn.execute(
                       "SELECT fc.*, ke.dimension, ke.entity "
                       "FROM fact_corrections fc "
                       "JOIN knowledge_entries ke ON fc.entry_id = ke.id "
                       "ORDER BY fc.created_at DESC"
                   ).fetchall()
                   context_parts.append(_format_corrections(rows))

               if "summaries" in sources:
                   rows = conn.execute(
                       "SELECT id, title, summary FROM documents ORDER BY id"
                   ).fetchall()
                   context_parts.append(_format_summaries(rows))

       except sqlite3.Error as exc:
           return Failure(str(exc), recoverable=False, context={"report_type": report_type})

       # 3. Render prompt
       full_prompt = prompt_template + "\n\n--- DATA ---\n\n" + "\n\n".join(context_parts)

       # 4. Call synthesis LLM
       try:
           from llm.provider import get_provider

           provider = get_provider("synthesis", config)
           response = provider.chat(
               system="You are a knowledge base analyst. Produce clear, data-driven reports.",
               user=full_prompt,
           )
           body = response if isinstance(response, str) else str(response)
       except Exception as exc:
           return Failure(f"LLM synthesis failed: {exc}", recoverable=True, context={"report_type": report_type})

       # 5. Store in reports table
       try:
           with get_connection(db_path) as conn:
               cursor = conn.execute(
                   """INSERT INTO reports (report_type, title, body, prompt_used, filters_used, sources_used)
                      VALUES (?, ?, ?, ?, ?, ?)""",
                   (report_type, title, body, full_prompt, json.dumps(filters), json.dumps(source_ids)),
               )
               report_id = cursor.lastrowid
       except sqlite3.Error as exc:
           return Failure(f"Failed to store report: {exc}", recoverable=False)

       # 6. Audit
       from core.audit import write as audit_write
       from core.confidence import AIDecision
       from core.logging_setup import new_correlation_id

       new_correlation_id()
       decision = AIDecision(
           action=f"report:{report_type}",
           confidence=1.0,
           reasoning=f"On-demand report generation: {title}",
           source_ids=[str(report_id)],
       )
       audit_write(decision, pipeline="reports", stage="synthesize", outcome="GENERATED")

       return Success({"report_id": report_id, "title": title, "body": body})


   def _format_facts(rows) -> str:
       lines = ["KNOWLEDGE ENTRIES:"]
       for r in rows:
           lines.append(
               f"  [{r['id']}] {r['dimension']}/{r['entity']}/{r['tag']}: "
               f"{r['fact']} (status={r['status']}, conf={r['confidence']}, trust={r['trust_score']})"
           )
       return "\n".join(lines)


   def _format_corrections(rows) -> str:
       lines = ["CORRECTIONS:"]
       for r in rows:
           lines.append(
               f"  [{r['id']}] {r['dimension']}/{r['entity']} op={r['operation']} "
               f"category={r['reason_category']} trust:{r['old_trust_score']}->{r['new_trust_score']} "
               f"at {r['created_at']}"
           )
       return "\n".join(lines)


   def _format_summaries(rows) -> str:
       lines = ["DOCUMENTS:"]
       for r in rows:
           lines.append(f"  [{r['id']}] {r['title']}: {(r['summary'] or '')[:200]}")
       return "\n".join(lines)


   def _detect_conflicts(conn) -> list[tuple]:
       """Find same entity+dimension+tag with both confident and pending entries."""
       return conn.execute(
           """SELECT e1.id as trusted_id, e1.entity, e1.dimension, e1.tag,
                     e1.fact as trusted_fact, e1.trust_score,
                     e2.id as pending_id, e2.fact as pending_fact
              FROM knowledge_entries e1
              JOIN knowledge_entries e2
                ON e1.entity = e2.entity AND e1.dimension = e2.dimension AND e1.tag = e2.tag
              WHERE e1.status = 'confident' AND e1.trust_score > 0.5
                AND e2.status = 'pending'
                AND e1.id != e2.id"""
       ).fetchall()


   def _format_conflicts(conflicts) -> str:
       lines = ["CONFLICTS (trusted entry vs pending entry):"]
       for c in conflicts:
           lines.append(
               f"  Trusted [{c[0]}] {c[1]}/{c[2]}/{c[3]}: \"{c[4]}\" (trust={c[5]}) "
               f"vs Pending [{c[6]}]: \"{c[7]}\""
           )
       return "\n".join(lines)
   ```

3. **Register `kms_reports` in `tools.py`** — add after `kms_comment` registration. C-15: pipeline exists and is tested before tool is added. Add import:
   ```python
   from pipelines import reports
   ```
   Tool function:
   ```python
   def kms_reports(report_type: str) -> dict:
       from core.config import CONFIG
       return reports.synthesize_report(report_type=report_type, config=CONFIG.main, db_path=_db_path()).unwrap()
   ```
   Registration:
   ```python
   mcp.tool(
       description="Generate a knowledge report on demand. Types: correction_summary, knowledge_health, volatile_entries, coverage_gaps, conflicts."
   )(kms_reports)
   ```

**Tests:**
- Unit: `synthesize_report("knowledge_health", config=config)` with mock LLM returns Success with report body.
- Unit: `synthesize_report("nonexistent", config=config)` returns Failure with available types listed.
- Unit: Report is stored in `reports` table with all fields.
- Unit: Adding a new entry to `reports.yaml` (e.g., `custom_report`) produces a different report without code changes.
- Unit: `conflicts` report correctly identifies entries where overwrite guard created a competing pending entry alongside a trusted entry.
- Unit: `kms_reports("knowledge_health")` tool returns the synthesized report.
- Unit: Audit trail contains `report:knowledge_health` entry after generation.
- Integration: With mock LLM provider, full pipeline from tool call to stored report to response works.

4. **Update `src/mcp_server/AI_INSTRUCTIONS.md`** — Add `kms_reports` to the tool inventory table and add a reports section:
   - Tool inventory row: `| kms_reports | Generate an on-demand knowledge health report by type. |`
   - Update "Six tools" → "Seven tools" in the intro line.
   - Add a "Reports" section after "Comments":
     ```markdown
     ## Reports

     `kms_reports` generates an on-demand knowledge health report. Pass `report_type` — one of: `correction_summary`, `knowledge_health`, `volatile_entries`, `coverage_gaps`, `conflicts`. Reports are synthesized by the LLM using current knowledge base data and stored for history. Use when the user asks about knowledge quality, correction trends, or wants a health check.
     ```
   - Update the "Correct vs write routing" section to add `confirm`, `reject`, `revise` to `kms_correct` operations list, and mention `reason_category` and `feedback` parameters.

---

## Open Questions

None remaining. All OQs from the spec (OQ-P10-01 through OQ-P10-05) have been resolved:
- OQ-P10-01: Trust preserved on update (Phase 5, trust preservation).
- OQ-P10-02: No correction inheritance on revise (Phase 6, revise creates clean entry).
- OQ-P10-03: Always regenerate reports (Phase 10, no caching).
- OQ-P10-04: Same few-shot on retry (Phase 7, few_shot_text computed before extract loop).
- OQ-P10-05: No comment length cap (Phase 7b, no cap).

---

## Out of Scope

- Frontend / web UI (Phase 11)
- Trust score decay over time
- Entity resolution / semantic contradiction detection
- Correction history inheritance on revise
- Report caching / freshness (always regenerate)
- Comment editing or deletion
- Scheduling / automation of reports
- Multiple learning signal types