# Plan: TD-042 — Stage 5 strips deprecated frontmatter keys
_Generated 2026-06-07 from mini-spec docs/3_specs/td042-deprecated-key-strip-mini.md_

---

## Q1 — What is the problem? (where the gap is)

```
VAULT (disk)
  note.md
    frontmatter:
      domain: finance        ← old scalar, should be gone
      tags:
        - domain/Finance     ← already correct
      project: Alpha         ← already correct

         │
         ▼
   kms reconcile
   Stage 5: reconcile_stale_tags
         │
         │  checks tags → already correct   (no dirty)
         │  checks project → already correct (no dirty)
         │
         ▼
   dirty = False
   ─── SKIP ───►   note.md never written
                   "domain: finance" stays on disk forever
```

The gap: Stage 5 only sets dirty=True for tag/project mismatches.
A note whose ONLY problem is a deprecated key is never written,
so the lazy-migration strip in dumps() never fires.

---

## Q2 — How does Stage 5 connect to vault/writer and vault/frontmatter?

```
reconcile_stale_tags (Stage 5)
pipelines/reconcile.py
        │
        │  lazy import inside stage function body
        │  (consistent with all other Stage 5 imports)
        │
        ├──► from vault.frontmatter import _DEPRECATED_KEYS
        │         frozenset{"domain"}
        │         — the list of keys that must not survive on disk
        │
        ├──► from vault.reader import read_note
        │         reads note.metadata.extra
        │         extra = {"domain": "finance", ...}
        │         (unknown keys land in extra, not on typed fields)
        │
        │   NEW CHECK (one line added before "if not dirty: continue")
        │   if any key in note.metadata.extra is in _DEPRECATED_KEYS:
        │       dirty = True
        │
        ├──► model_copy(update={tags, project})
        │         preserves extra unchanged
        │         (verified: writer._merge_metadata line 85 copies extra as-is)
        │
        └──► write_note(path, content, new_meta, actor="ai")
                  vault/writer.py
                       │
                       ▼
                  _merge_metadata
                       │   updated_by_human gate:
                       │   if disk says updated_by_human=True
                       │   and actor="ai" → Failure(recoverable=False)
                       │   Stage 5 already handles this branch silently
                       │
                       ▼
                  dumps(new_meta, body)
                  vault/frontmatter.py
                       │
                       ▼
                  for key in _DEPRECATED_KEYS:
                      d.pop(key, None)     ← strips "domain"
                       │
                       ▼
                  atomic write to disk
                  "domain: finance" is gone
```

No new imports added to reconcile.py at module level.
No change to vault/writer.py or vault/frontmatter.py.
No new counter on ReconcileResult — reuse tags_updated.

---

## Q3 — Why build it this way?

```
Four design options considered:

Option A (chosen)
  Add ONE LINE to Stage 5.
  Reuse the existing dirty-flag → write_note → dumps() chain.
  No new module. No new counter. No Stage 7 change.
  ┌─ lazy import  → consistent with how Stage 5 already imports helpers
  ├─ reuse tags_updated → "a write happened" is the right signal; no new field
  ├─ no Stage 7 change → Stage 7 (editable_migration) has no dirty-flag pattern;
  │                       different domain; touching it would widen blast radius
  └─ strip in dumps() → strip logic already exists and is tested; we just need
                         to trigger the write

Option B (rejected)
  New reconcile stage "reconcile_deprecated_keys".
  Extra function, extra counter, extra test fixture.
  Warranted only if the logic grew complex. It is one line.

Option C (rejected)
  Strip in _merge_metadata inside writer.py.
  Moves migration knowledge into the wrong layer.
  writer.py would need to import _DEPRECATED_KEYS — a vault/frontmatter →
  vault/writer dependency that does not exist today.

Option D (rejected)
  One-shot migration script (kms migrate-deprecated-keys).
  Requires a separate CLI command, operator action, and coordination.
  Lazy migration via reconcile is zero-ops.
```

---

## Scope

**In:**
- `src/pipelines/reconcile.py` — Stage 5 only (one line + lazy import line)
- `tests/test_pipelines/test_reconcile.py` — three new test cases

**Out:**
- Stage 7 (`reconcile_editable_migration`) — no dirty-flag pattern; different domain
- `vault/frontmatter.py` — already correct; no change
- `vault/writer.py` — already correct; no change
- `ReconcileResult` dataclass — no new counter; reuse `tags_updated`
- `STATE.md` — not updated this session (per instruction)

---

## Done-when (acceptance criteria)

| ID | Scenario | Expected outcome |
|----|----------|-----------------|
| P2-REC-01 | Note has `domain: finance` in extra, already has valid `domain/Finance` tag, correct `project:` | After `kms reconcile`, `domain:` key absent from disk. `tags_updated` incremented by 1. |
| P2-REC-02 | Same note but `updated_by_human: true` on disk | Note untouched. Key stays. No write. `tags_updated` unchanged. |
| P2-REC-03 | Note has no deprecated keys and no other dirty reason | Skipped. No write. No regression. |

---

**Status:** [x] done
**Completed:** 2026-06-07
**Notes:** Tests use `shutil.copy()` from pre-written fixture `.md` files (not `write_note`) because `dumps()` strips `_DEPRECATED_KEYS` at write time. 3 pre-existing `write_text()` calls in `test_reconcile.py` also fixed as part of hook cleanup (lines 180/220/262 → `shutil.copy()` from new fixture files). Hook false-positive on `.attachment_path` in `reconcile.py` (catches `note.metadata.attachment_path` — pre-existing, not VaultConfig API). 1 unrelated flaky test (`test_no_edit_pdf_cross_folder_rehome`) failed in full-suite ordering — confirmed passes in isolation; pre-existing state pollution.

## Surprises

- **Hook false-positive:** `.attachment_path` hook in `settings.json` matches `note.metadata.attachment_path` (legitimate `NoteMetadata` field) in addition to the banned `VaultConfig.attachment_path`. Fires on every edit to `reconcile.py`. Pre-existing — not introduced by TD-042. Log as TD: narrow hook regex to `vault_cfg\.attachment_path` or `VaultConfig.*\.attachment_path`.
- **Hook: write_text in test files:** Three pre-existing `write_text()` calls in `test_reconcile.py` also violated the vault-write hook. Fixed as part of this session (new fixture files + `shutil.copy()`). No scope risk — mechanical replacement.
- **Fixture needed:** Test for deprecated-key strip could not use `write_note` to create the fixture because `dumps()` strips `_DEPRECATED_KEYS` at write time. Solution: pre-written `.md` fixture files + `shutil.copy()`.
- **Flaky test:** `test_watcher_rehome.py::test_no_edit_pdf_cross_folder_rehome` fails in full-suite ordering (passes in isolation). Pre-existing ordering issue, not caused by TD-042 changes.

## Implementation steps (TDD order)

### Step 1 — Write the three tests (RED)

File: `tests/test_pipelines/test_reconcile.py`

Add a fixture that builds a minimal in-memory note with:
- `metadata.extra = {"domain": "finance"}`
- `metadata.tags = ["domain/Finance"]` (already valid)
- `metadata.project = "Alpha"` (already correct)
- `updated_by_human = False`

**Test P2-REC-01** (happy path)
- Arrange: note with deprecated key in extra, valid tag, correct project.
  Patch `read_note` to return this note.
  Patch `write_note` to capture calls and return `Success()`.
  Patch `load_valid_domains` to return `{"Finance"}`.
- Act: call `reconcile_stale_tags(result, ctx, [entry])`.
- Assert: `write_note` was called once; `result.tags_updated == 1`.

**Test P2-REC-02** (human lock)
- Arrange: same note but `updated_by_human=True`.
  Patch `write_note` to return `Failure(recoverable=False, context={"vault_path": "..."})`
  (mirrors the human-lock branch already tested for other Stage 5 writes).
- Act: call `reconcile_stale_tags`.
- Assert: `write_note` was called (Stage 5 tried), but `tags_updated == 0`.

**Test P2-REC-03** (no-op regression)
- Arrange: note with empty extra `{}`, valid tags, correct project.
  Patch `write_note` to raise `AssertionError` if called (must not fire).
- Act: call `reconcile_stale_tags`.
- Assert: `write_note` never called; `tags_updated == 0`.

Run: `uv run pytest tests/test_pipelines/test_reconcile.py -k "deprecated"` → expect 3 FAIL.

---

### Step 2 — Add the deprecated-key check to Stage 5 (GREEN)

File: `src/pipelines/reconcile.py`

Location: inside `reconcile_stale_tags`, after the existing project check block
(currently around line 357), before `if not dirty: continue`.

**Lazy import** (add alongside existing lazy imports at top of function body,
lines 309-311):
```
from vault.frontmatter import _DEPRECATED_KEYS
```

**Dirty check** (insert before `if not dirty: continue`):
```
# Deprecated frontmatter keys still present in extra — trigger write so
# dumps() strips them (lazy migration, TD-042).
if any(k in note.metadata.extra for k in _DEPRECATED_KEYS):
    dirty = True
```

Run: `uv run pytest tests/test_pipelines/test_reconcile.py -k "deprecated"` → expect 3 PASS.

Verify no regressions: `uv run pytest tests/test_pipelines/test_reconcile.py` → all existing tests still PASS.

---

### Step 3 — Full suite (VERIFY)

```bash
uv run pytest tests/
```

Expected: 956 + 3 = 959 tests pass. Zero new failures.

---

### Step 4 — Lint

```bash
uv run ruff check src/pipelines/reconcile.py
```

Expected: no issues (auto-format hook handles formatting on write).

---

## Risk and edge cases

| Risk | Mitigation |
|------|-----------|
| `model_copy` drops `extra` before write | Verified: `writer._merge_metadata` line 85 copies `extra` as-is. P2-REC-01 test will catch any regression. |
| `updated_by_human` gate bypassed | Stage 5 already handles the `Failure(recoverable=False, context={"vault_path": ...})` branch silently. P2-REC-02 test locks this in. |
| `_DEPRECATED_KEYS` import creates a module-level import in reconcile.py | Import is lazy (inside function body), consistent with existing Stage 5 pattern. No circular-import risk. |
| Adding new keys to `_DEPRECATED_KEYS` later | No plan change needed — Stage 5 iterates the frozenset dynamically. |
| Counter inflation (tags_updated used for two purposes) | Acceptable: both cases mean "Stage 5 triggered a write." Phase 8 Briefing reads the count for informational display only, not routing. |

---

## Files touched

| File | Change |
|------|--------|
| `src/pipelines/reconcile.py` | +1 lazy import line, +3 lines dirty-check block |
| `tests/test_pipelines/test_reconcile.py` | +3 test cases |

Total: ~6 lines of production code. No other files.

---

## Open questions

None. All edge cases resolved in mini-spec (2026-06-07 grill session).
Tier: standard change — single stage, single dirty-flag, existing write path.
No escalation triggered.
