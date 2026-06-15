# Phase 8 Nuclear Review — Issue Diagnosis

> **Scope:** Phase 8 Slice A (Classify Infrastructure) + Slice B (Classify Extraction)
> **Branch:** `cloud-native`
> **Date:** 2026-06-15
> **Method:** 4 parallel review agents (classify pipeline, storage layer, server wiring, config/prompts) + manual cross-cutting analysis
> **Test status:** 59 Phase 8 tests passing, full suite ~1275 tests green

---

## Summary

| Severity | Count | Description |
|----------|-------|-------------|
| CRITICAL | 4 | Perf regression at scale, DRY violation, async lifecycle bug |
| HIGH | 6 | File decomposition needed, validation sprawl, fragile paths, repeated patterns |
| MEDIUM | 10 | Duplication, missing helpers, perf advisories, dead import |
| LOW | 5 | Unchecked results, loose types, missing bounds |

**Verdict:** Functionally correct — no logic bugs found. Structurally overdue for decomposition. `classify.py` at 984 lines is the primary concern. Two async lifecycle issues in worker shutdown. Major code-judo opportunity in `write_entries` (280-line function with 3 copy-pasted branches).

---

## CRITICAL (4)

### C1. Dimensions loaded TWICE per document

**Files:** `src/pipelines/classify.py:109` + `src/pipelines/classify.py:831`
**Impact:** 2N file reads + 2N YAML parses per N documents (same immutable config)

`context_loader()` loads `dimensions.yaml` at line 109 via `load_dimensions(dimensions_path)`. Then `orchestrate()` loads it AGAIN at line 831 via a separate `load_dimensions()` call to get guidance text and dimension names.

The two calls serve different purposes:
- `context_loader` loads dimensions to iterate and query ranked facts per dimension
- `orchestrate` loads dimensions for guidance text (passed to `extract()`) and dimension iteration

But both read+parse the same YAML file. For a queue of 100 documents, that's 200 redundant file reads.

**Fix:** `orchestrate()` should call `context_loader()` once and receive both the ranked facts AND the parsed rulebook. Options:
1. Have `context_loader` return `Success((rulebook, facts_by_dim))` as a tuple
2. Pass `rulebook` as a parameter to `context_loader` so it doesn't need to load it
3. Load dimensions once in `consumer()` before the `while True` loop, pass down

Option 3 is cleanest — dimensions are immutable during a consumer session.

---

### C2. Context loader re-queries DB per document

**File:** `src/pipelines/classify.py:122-126`
**Impact:** N documents × D dimensions = N×D redundant DB queries

```python
for dim_name in rulebook:
    ranked = query_ranked_by_dimension(
        dim_name,
        limit=cap,
        db_path=db_path,
    )
```

`context_loader()` is called once per document by `orchestrate()` (line 798). Each call issues D queries (one per dimension) to `knowledge_entries`. The ranked facts change only when `write_entries()` modifies them — within a single document's processing, the facts from OTHER dimensions are stable.

Between documents, facts DO change (the previous document's extraction may have added entries). So per-document reload is correct for dimensions that were just modified. But dimensions that weren't touched don't need re-querying.

**Fix options (increasing sophistication):**
1. **Simple cache per consumer session:** Load all facts once at consumer startup + after each catch-up scan. Stale-on-write is acceptable since the consumer processes sequentially — the next document gets the updated facts naturally.
2. **Invalidate per dimension:** After `write_entries()` for dimension X, mark X as dirty. Only re-query dirty dimensions on next `context_loader()` call.
3. **Accept current behavior with a comment:** For small knowledge bases (<1000 entries, <5 dimensions), the perf cost is negligible. Add `# PERF: O(N×D) queries — cache when this becomes a bottleneck` comment.

Recommendation: Option 1 for Phase 9, Option 3 as immediate fix.

---

### C3. `write_entries` is 280 lines with 3 copy-pasted source-merge blocks

**File:** `src/pipelines/classify.py:452-731`
**Impact:** Maintenance hazard — fixing source-merge logic requires 3 parallel edits

The function handles three actions (retire, update, new) in a single for-loop. The update path and new-twin-fold path contain nearly identical logic:

**Source-merge + dedupe (copy-pasted twice):**

Update path (lines 572-588):
```python
existing_sources: list[str] = (
    json.loads(row["sources"]) if row["sources"] else []
)
merged_sources = existing_sources + [str(doc_id)]
seen: set[str] = set()
deduped: list[str] = []
for s in merged_sources:
    if s not in seen:
        seen.add(s)
        deduped.append(s)
```

New-twin-fold path (lines 655-668):
```python
existing_sources: list[str] = []
# ... parse from twin_row ...
merged = existing_sources + [str(doc_id)]
seen = set()
deduped_sources = []
for s in merged:
    if s not in seen:
        seen.add(s)
        deduped_sources.append(s)
```

**Status re-gating (repeated 3 times):**
- Line 602-604: update path
- Line 685-687: new-twin-fold path
- Line 709-711: new-fresh path

Each instance: `if band is not None: status = _re_gate(confidence, band)`

**Reasoning merge (repeated twice):**
- Line 576-579: update path
- Line 670-672: new-twin-fold path

Each instance: `new_reasoning = fact.get("reason", ""); merged_reasoning = new_reasoning if new_reasoning else existing_reasoning`

**Fix — extract action handlers:**

```python
def _merge_sources(existing: list[str], doc_id: int) -> list[str]:
    merged = existing + [str(doc_id)]
    seen: set[str] = set()
    return [s for s in merged if not (s in seen or seen.add(s))]

def _compute_status(confidence: float, band: object | None) -> str | None:
    if band is None:
        return None
    return _re_gate(confidence, band)

def _merge_reasoning(existing: str, new: str) -> str:
    return new if new else existing

def _apply_retire(fact, doc_id, dimension, db_path) -> tuple[bool, list[int]]: ...
def _apply_update(fact, doc_id, dimension, band, db_path) -> tuple[bool, list[int]]: ...
def _apply_new(fact, doc_id, dimension, band, db_path) -> tuple[bool, list[int]]: ...
```

Then `write_entries` becomes:
```python
handlers = {"retire": _apply_retire, "update": _apply_update, "new": _apply_new}
for fact in facts:
    handler = handlers.get(fact.get("action"))
    if handler is None:
        summary.clean = False; continue
    clean, skipped = handler(fact, doc_id, dimension, band, db_path)
    if not clean: summary.clean = False
    summary.skipped_ids.extend(skipped)
```

Estimated savings: ~80 lines of duplication removed, each handler independently testable.

---

### C4. Worker task cancelled but never awaited

**File:** `src/mcp_server/cloud_entry.py:129`
**Impact:** Resource leak on shutdown — DB connections and file handles may not close

```python
finally:
    worker.cancel()
    _log.debug("classify_worker_task_cancelled")
```

`worker.cancel()` requests cancellation but does NOT wait for the task to finish. The standard asyncio pattern:

```python
finally:
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    _log.debug("classify_worker_task_cancelled")
```

Without awaiting, the task continues running until the event loop processes the cancellation. If the lifespan context exits before the task completes, any open resources (DB connections in `orchestrate()`, active AI API calls in `extract()`) are abandoned, not cleaned up.

**Note:** The server wiring agent incorrectly flagged `except Exception` in `consumer()` as catching `CancelledError`. This is **invalid** — Python 3.12 `CancelledError` inherits from `BaseException`, not `Exception`. Verified: `issubclass(asyncio.CancelledError, Exception) == False`. The bare `except Exception` at classify.py:179 does NOT catch cancellation, which is correct behavior.

---

## HIGH (6)

### H1. `classify.py` at 984 lines — 1k threshold

**File:** `src/pipelines/classify.py`
**Impact:** Cognitive load; four distinct concerns in one file

Current structure:
```
Lines 1-137:    Infrastructure helpers (content_reader, context_loader)     ~137L
Lines 139-211:  Worker + catch-up scan (consumer, catch_up_scan)            ~73L
Lines 213-437:  Entity extraction (extract + validation)                    ~225L
Lines 440-731:  Entry writing (WriteSummary, write_entries)                 ~292L
Lines 734-984:  Orchestrator (orchestrate)                                  ~251L
```

**Recommended decomposition:**

```
src/pipelines/
├── classify.py                  (~210L — kept, public API surface)
│   ├── content_reader()
│   ├── context_loader()
│   ├── consumer()
│   └── catch_up_scan()
├── classify_extract.py          (~180L — extraction + validation)
│   ├── extract()
│   └── _validate_item()
├── classify_writer.py           (~150L — entry writing + helpers)
│   ├── write_entries()
│   ├── _apply_retire()
│   ├── _apply_update()
│   ├── _apply_new()
│   ├── _merge_sources()
│   └── _compute_status()
└── classify_orchestrator.py     (~150L — orchestration + retry)
    ├── orchestrate()
    └── _fail_and_record()
```

Each file stays under 250 lines. Each concern is independently testable. Import surface remains clean — callers import from `classify.py` which re-exports if needed.

---

### H2. Entity validation: 114-line switch-on-action block

**File:** `src/pipelines/classify.py:322-436`
**Impact:** 30+ near-identical Failure constructions

Three action branches (retire, update, new) each validate required fields with the same pattern:
```python
if "field" not in item:
    return Failure(
        error=f"entity_extract item[{i}] '{action}' missing '{field}'",
        recoverable=True,
        context={"stage": "extract", "dimension": dimension, "raw": raw_text[:200]},
    )
```

The context dict is identical across ALL validation failures. The error message follows a fixed template.

**Fix:**
```python
def _validate_item(item: dict, action: str, index: int, dimension: str, raw_text: str) -> Failure | dict:
    """Validate one extracted fact. Returns Failure or cleaned dict."""
    def _fail(msg: str) -> Failure:
        return Failure(
            error=f"entity_extract item[{index}] {msg}",
            recoverable=True,
            context={"stage": "extract", "dimension": dimension, "raw": raw_text[:200]},
        )

    REQUIRED = {
        "retire": {"id", "reason"},
        "update": {"id", "entity", "tag", "fact", "confidence"},
        "new":    {"entity", "tag", "fact", "confidence"},
    }
    FORBIDDEN = {"new": {"id"}}

    for field in REQUIRED.get(action, set()):
        if field not in item:
            return _fail(f"'{action}' missing '{field}'")

    for field in FORBIDDEN.get(action, set()):
        if field in item:
            return _fail(f"'{action}' must not include '{field}'")

    # ... string-empty checks, confidence range, build parsed dict
```

Estimated savings: ~70 lines, validation logic becomes declarative.

---

### H3. Fragile `.parent.parent` path resolution (duplicated)

**Files:** `src/pipelines/classify.py:105-107` and `src/pipelines/classify.py:828-829`
**Impact:** Breaks on package install to site-packages; two locations to maintain

```python
# Line 105-107 (context_loader)
_classify_dir = Path(__file__).resolve().parent  # src/pipelines
_project_root = _classify_dir.parent.parent       # project root
dimensions_path = _project_root / "config" / "dimensions.yaml"

# Line 828-829 (orchestrate)
dimensions_path = (
    Path(__file__).resolve().parent.parent / "config" / "dimensions.yaml"
)
```

Both resolve to the same file but use slightly different computation patterns. Neither will work when the package is installed to site-packages (the `.parent.parent` chain resolves to `site-packages/` root, not the project root).

**Fix:** Import `CONFIG_DIR` from `core.config` (if it exists) or accept `dimensions_path` as a parameter. Check what `core.config` exports:

```python
# In core/config.py, if CONFIG_DIR exists:
from core.config import CONFIG_DIR
dimensions_path = CONFIG_DIR / "dimensions.yaml"

# Or: accept as parameter
def context_loader(*, config, db_path=None, dimensions_path=None):
    if dimensions_path is None:
        dimensions_path = Path(__file__).resolve().parent.parent / "config" / "dimensions.yaml"
```

---

### H4. `write_entries` twin-lookup is inline 20-line fragment

**File:** `src/pipelines/classify.py:620-641`
**Impact:** Twin-lookup logic not reusable, scattered in middle of action handler

```python
twins_result = query_by_entity(entity, db_path=db_path)
twin_id: int | None = None
if isinstance(twins_result, Success):
    for t in twins_result.value:
        if (
            t.status != "retired"
            and t.dimension == dimension
            and t.tag == tag
        ):
            twin_id = t.id
            break
```

This dedup check (find existing non-retired entry with same entity+dimension+tag) is a core classify concern but lives inline in the `new` action path only.

**Fix:**
```python
def _find_twin(entity: str, dimension: str, tag: str, db_path) -> Result[int | None]:
    """Find existing non-retired entry matching entity+dimension+tag."""
    twins_result = query_by_entity(entity, db_path=db_path)
    if isinstance(twins_result, Failure):
        return twins_result
    for t in twins_result.value:
        if t.status != "retired" and t.dimension == dimension and t.tag == tag:
            return Success(t.id)
    return Success(None)
```

---

### H5. `orchestrate` error-recording pattern repeated 4 times

**File:** `src/pipelines/classify.py:781-793, 800-812, 833-845, 947-952`
**Impact:** 4 identical error-handling blocks

Every early-return failure in `orchestrate()` does:
```python
rf_result = record_classify_failure(doc_id, error_msg, db_path=db_path)
if isinstance(rf_result, Failure):
    _worker_log.warning(
        "orchestrate record_classify_failure failed doc_id=%s error=%s",
        doc_id, rf_result.error,
    )
return Failure(
    error=f"<stage> failed: {text_result.error}",
    recoverable=True,
    context={"doc_id": doc_id, "stage": "orchestrate"},
)
```

**Fix:**
```python
def _fail_and_record(
    doc_id: int, stage: str, error: str, db_path: Path | None
) -> Failure:
    rf = record_classify_failure(doc_id, error, db_path=db_path)
    if isinstance(rf, Failure):
        _worker_log.warning(
            "orchestrate record_classify_failure failed doc_id=%s error=%s",
            doc_id, rf.error,
        )
    return Failure(
        error=f"{stage} failed: {error}",
        recoverable=True,
        context={"doc_id": doc_id, "stage": "orchestrate"},
    )
```

Saves ~30 lines, ensures consistent error recording.

---

### H6. `documents.py` classify helpers should extract

**File:** `src/storage/documents.py:642-810`
**Impact:** 169 classify-specific lines in an already-823-line general-purpose module

Functions added by Phase 8 Slice A+B:
- `find_unclassified()` — line 642
- `stamp_classified()` — line 668
- `record_classify_failure()` — line 699
- `clear_classify_retry_state()` — line 731
- `park_document()` — line 751
- `load_classify_retry_state()` — line 781

None of these are called by any non-classify code. They form a cohesive group.

**Fix:** Extract to `storage/documents_classify.py`. `documents.py` drops to ~654 lines. Classify pipeline imports from the new module. No other callers need to change.

---

## MEDIUM (10)

### M1. Source-merge dedup duplicated (update + twin-fold)

**Files:** `classify.py:572-588` and `classify.py:655-668`

Same 8-line block. Extract to `_merge_sources(existing: list[str], doc_id: int) -> list[str]`.

---

### M2. Status re-gating repeated 3 times

**Files:** `classify.py:602-604`, `classify.py:685-687`, `classify.py:709-711`

Same 2-line pattern: `if band is not None: status = _re_gate(confidence, band)`. Extract to `_compute_status(confidence, band) -> str | None`.

---

### M3. Update-path read-merge-regate-upsert could be helper

**File:** `classify.py:543-614`

The update path: read existing entry → merge sources → merge reasoning → compute status → upsert. Same sequence as twin-fold path (lines 643-696). Both are "merge new data into existing entry." A shared `_merge_and_upsert(ref_id, fact, doc_id, dimension, band, db_path)` helper would serve both.

---

### M4. Stamp-path fallthrough into retry-path has implicit state

**File:** `classify.py:918-984`

If `all_clean` is True, the stamp path runs (lines 918-943). If stamp fails, it records failure and returns. If `all_clean` is False, the retry path runs (lines 945-978). The `failure_reasons` list and `error_msg` are built inside the per-dimension loop and consumed in the retry path. The stamp path never reads them.

This works but requires tracking that `error_msg` is only defined in the `not all_clean` branch. A reader must hold both branches in their head. Clearer to split:

```python
if all_clean:
    return _handle_stamp(doc_id, db_path, cid)
else:
    return _handle_retry(doc_id, failure_reasons, config, db_path, cid)
```

---

### M5. Per-dimension audit verbose — extractable helper

**File:** `classify.py:897-915`

```python
audit_decision = AIDecision(
    action=f"extract:{dim_name}",
    confidence=0.8,
    reasoning=f"Extracted {len(extracted.value) if not isinstance(extracted, Failure) else 0} facts from doc {doc_id}",
    source_ids=[str(doc_id)],
)
audit_result = audit_write(
    audit_decision,
    pipeline="classify",
    stage=dim_name,
    outcome="classify" if dim_clean else "needs-retry",
    db_path=db_path,
)
if isinstance(audit_result, Failure):
    all_clean = False
    failure_reasons.append(f"{dim_name} audit: {audit_result.error}")
```

This 15-line block runs once per dimension. Extractable to `_audit_dimension(dim_name, dim_clean, facts_count, doc_id, db_path) -> Failure | None`.

---

### M6. `prune_sources` loads ALL non-retired entries into memory

**File:** `src/storage/knowledge_entries.py:262-265`

```sql
SELECT id, sources FROM knowledge_entries WHERE status != 'retired'
```

Fetches every non-retired entry, parses JSON in Python, filters by doc_id. For large knowledge bases (1000+ facts), this is O(N) memory. The docstring acknowledges this: "OQ-P8B-01: scan-and-filter in Python (swappable for a JSON1 query later)."

**Fix (future):** Use SQLite JSON1 extension:
```sql
SELECT id, sources FROM knowledge_entries
WHERE status != 'retired'
  AND json_each.value = ?
```
Or pre-filter with `WHERE sources LIKE '%"' || ? || '"%'` to reduce Python-side work.

---

### M7. Redundant dedupe in `prune_sources`

**File:** `src/storage/knowledge_entries.py:277-282`

After removing `sid` from sources, the code dedupes the result. But `write_entries` already dedupes sources on every write. The only way duplicates could exist is if they were written by a different code path (manual SQL, migration, etc.).

The dedupe is defensive but unnecessary for the normal code path. Either add a comment explaining why it's there or remove it.

---

### M8. `_row_from_sqlite` has 16 defensive column checks

**File:** `src/storage/documents.py:62-100`

```python
classify_content_hash=row["classify_content_hash"] if "classify_content_hash" in row.keys() else None,
trust_score=row["trust_score"] if "trust_score" in row.keys() else None,
# ... 14 more
```

This pattern handles schema evolution (columns added by later migrations). Could compress to:
```python
def _col(row, name, default=None):
    return row[name] if name in row.keys() else default
```

---

### M9. `find_unclassified` likely table-scans despite index

**File:** `src/storage/documents.py:642-665`

```sql
WHERE (classify_content_hash IS NULL OR classify_content_hash != content_hash)
  AND (status IS NULL OR status != 'needs-review')
```

Migration 010 creates `idx_docs_classify_hash ON documents(classify_content_hash)`. But the OR condition and cross-column comparison likely prevent SQLite from using the index effectively.

Not a correctness issue. For <10K documents, performance is fine. Add a comment: `-- NOTE: OR condition may table-scan; consider composite index if this becomes a bottleneck`.

---

### M10. Unused `import asyncio` in `_push_to_classify_queue`

**File:** `src/mcp_server/api.py:45`

```python
def _push_to_classify_queue(request: Request, document_id: int) -> None:
    import asyncio  # <-- unused
    queue = getattr(request.app.state, "classify_queue", None)
```

`asyncio` is not used in this function. `queue.put_nowait()` is a method on the queue object, doesn't require asyncio in the caller. Remove the import.

---

## LOW (5)

### L1. `clear_classify_retry_state()` result not checked

**File:** `classify.py:939`

```python
clear_classify_retry_state(doc_id, db_path=db_path)
```

No error handling. If this fails, next retry sees stale state (non-zero attempt count). Idempotent on success, but inconsistent with surrounding pattern (all other calls check results).

---

### L2. `audit_write()` in park path result not unwrapped

**File:** `classify.py:967-973`

Audit write result is discarded. Acceptable (audit is best-effort) but inconsistent with earlier audit calls that check and log failures.

---

### L3. `band: object | None` — loose type

**Files:** `classify.py:457` and `classify.py:744`

```python
def write_entries(facts, doc_id, dimension, *, band: object | None = None, ...):
def orchestrate(doc_id, *, config, db_path=None, band: object | None = None):
```

`band` is always a `ConfidenceBand` from `core.config`. Using `object` loses all type checking. Should be `ConfidenceBand | None`. The import is already available in `knowledge_entries.py` which uses the typed version.

---

### L4. `max_retries` has no upper bound

**File:** `src/core/config.py:345` (approximate)

`max_retries: int = Field(default=3, ge=1)` — no `le=` constraint. A config typo of `max_retries: 999999` is technically valid and would cause runaway retry loops. Recommend `le=20`.

---

### L5. `_row_to_entry` defensive key checks need lifecycle comment

**File:** `src/storage/knowledge_entries.py:49-50`

```python
trust_score=row["trust_score"] if "trust_score" in row.keys() else 0.5,
retrieval_count=row["retrieval_count"] if "retrieval_count" in row.keys() else 0,
```

These handle pre-migration-010 databases. Add comment: `# Backcompat for pre-migration-010 — remove when all deployments have migrated`.

---

## Rejected Findings

### Server wiring agent: `except Exception` catches `CancelledError`

**Claim:** `classify.py:179` bare `except Exception` catches `asyncio.CancelledError`, breaking graceful shutdown.

**Rejection reason:** Python 3.12 `CancelledError` inherits from `BaseException`, NOT `Exception`. Verified:
```python
>>> import asyncio
>>> issubclass(asyncio.CancelledError, Exception)
False
```

The bare `except Exception` at line 179 does NOT catch cancellation. This is correct behavior — `CancelledError` propagates up and triggers the `finally` block for `task_done()`, then propagates further to the task runner for proper cleanup.

---

## Recommended Fix Priority

### Phase 1 — Quick wins (no structural change)

| ID | Fix | Est. effort | Lines saved |
|----|-----|-------------|-------------|
| C4 | Await worker cancellation in cloud_entry.py | 5 min | +4 lines |
| M10 | Remove unused asyncio import in api.py | 1 min | -1 line |
| L1 | Check clear_classify_retry_state result | 2 min | +3 lines |
| L3 | Type `band` as `ConfidenceBand \| None` | 2 min | 0 lines |
| L4 | Add `le=20` to max_retries | 1 min | 0 lines |

### Phase 2 — Extract helpers (within classify.py)

| ID | Fix | Est. effort | Lines saved |
|----|-----|-------------|-------------|
| C3 | Extract _merge_sources, _compute_status, _merge_reasoning | 15 min | ~40 lines |
| H2 | Extract _validate_item | 20 min | ~70 lines |
| H4 | Extract _find_twin | 10 min | ~15 lines |
| H5 | Extract _fail_and_record | 10 min | ~30 lines |
| M5 | Extract _audit_dimension | 5 min | ~10 lines |

### Phase 3 — File decomposition

| ID | Fix | Est. effort | Lines saved |
|----|-----|-------------|-------------|
| H1 | Split classify.py → 3-4 files | 30 min | 0 (reorganize) |
| H6 | Extract documents_classify.py | 20 min | 0 (reorganize) |

### Phase 4 — Performance (defer to Phase 9+)

| ID | Fix | Est. effort | Lines saved |
|----|-----|-------------|-------------|
| C1 | Pass rulebook through instead of reloading | 15 min | ~15 lines |
| C2 | Cache ranked facts in consumer session | 30 min | ~10 lines |
| M6 | Use JSON1 or LIKE pre-filter in prune_sources | 20 min | ~10 lines |

---

## Cross-Cutting Observations

### What Phase 8 got right

1. **Sequential consumer model** — single worker, sequential processing. No concurrency bugs possible. Clean.
2. **Composed lifespan pattern** — correctly wraps FastMCP's inner lifespan per CLAUDE.md constraints. Well-documented.
3. **Result type discipline** — every function returns `Result[T]`. No silent failures. Callers handle both paths.
4. **Audit trail** — per-dimension audit writes, even on failure. Phase 8 briefing requirements met.
5. **Graceful degradation** — classify_queue absent in CLI/tests → skip silently. blob_store absent → text-only mode. Clean null-object patterns.
6. **Self-correcting retry** — bounded retry with park-at-cap. No infinite loops. Retry state in DB survives restarts.

### What needs attention

1. **classify.py is one refactor away from exceeding 1k lines.** Any Phase 9 addition pushes it over. Decompose now.
2. **The source-merge + dedupe + re-gate pattern is the core "entry application" logic** and it's scattered across 3 branches with no abstraction. This is the single biggest maintainability risk.
3. **Path resolution for dimensions.yaml is fragile** and will break the Docker deployment if site-packages layout changes.
4. **Worker shutdown is incomplete** — the await-after-cancel fix is trivial but important for production reliability.
