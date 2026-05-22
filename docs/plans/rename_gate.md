# Plan: Rename Gate
_Last updated: 2026-05-22_
_Status: [x] done_

---

## What this feature fixes

Today the capture pipeline renames almost every file it processes, because the AI's
suggested title almost always differs from the original filename. This breaks user
trust: files get renamed even when the user named them deliberately, or when the
user is actively editing them.

The rename gate is a pre-decision checkpoint inserted just before the rename happens.
It classifies each file into one of three lanes — SKIP / AUGMENT / FULL_RENAME —
using deterministic Python rules. No new AI call is needed.

---

## Architecture

### The pipeline step sequence

```
Step 1 — EXTRACT
  Read the file content.  No AI yet.

Step 2 — SUMMARIZE
  AI call #1: "Summarize this content."
  AI returns a summary.

Step 3 — METADATA       ← AI title is produced here
  AI call #2: "What's a good title for this?"
  AI returns: e.g. "Q2 Movies Strategy Review"
                │
                │  title is now known, passed forward
                ▼
Step 4 — STORE          ← Gate runs here, before any rename
  ┌──────────────────────────────────────────────────────────┐
  │  rename_gate receives:                                   │
  │    · the file path            (e.g. "xkjdhfs83.pdf")    │
  │    · the AI title from Step 3 (already produced, no new │
  │      AI call)                                           │
  │    · whether this file was captured before              │
  │    · config rules (from config/config.yaml)             │
  │                                                          │
  │  Gate runs 4-rule flowchart.  Zero new AI calls.        │
  │  Decision: SKIP / AUGMENT / FULL_RENAME                 │
  └──────────────────────────────────────────────────────────┘
                │
                ▼
  File renamed (or left alone) + decision written to audit log
```

### The 4-rule decision flowchart

```
A file arrives at the STORE step
          │
          ▼
 ┌────────────────────────────────────────────────────────┐
 │  Has this file been captured before?                   │
 │  (is it already in our database?)                      │
 └──────────────────────┬─────────────────────────────────┘
                        │
          ┌─────────────┴──────────────┐
          │ YES                        │ NO (brand new file)
          ▼                            ▼
   ┌─────────────┐          ┌──────────────────────────────────────┐
   │  SKIP       │          │  Is this a document type (.md, .docx,│
   │  Don't      │          │  .xlsx, .pptx, .txt) AND does its    │
   │  rename it. │          │  filename look like a real name      │
   │  User is    │          │  (at least 2 words)?                 │
   │  still      │          └──────────────┬───────────────────────┘
   │  editing it.│                         │
   └─────────────┘          ┌──────────────┴──────────────┐
                            │ YES                         │ NO
                            ▼                             ▼
                     ┌─────────────┐          ┌──────────────────────────────┐
                     │  SKIP       │          │  Is it a generic placeholder? │
                     │  Don't      │          │  ("untitled", "notes",        │
                     │  rename it. │          │  "meeting", "a meeting", etc.)│
                     │  User named │          └──────────────┬───────────────┘
                     │  it on      │                         │
                     │  purpose.   │          ┌──────────────┴──────────────┐
                     └─────────────┘          │ YES                         │ NO
                                              ▼                             ▼
                                       ┌────────────────┐    ┌──────────────────────────┐
                                       │  AUGMENT       │    │  Is the filename          │
                                       │  Keep original │    │  unreadable gibberish?    │
                                       │  name, append  │    │  (random letters, hash    │
                                       │  AI topic.     │    │  codes, very short junk)  │
                                       └────────────────┘    └────────────┬─────────────┘
                                                                          │
                                                             ┌────────────┴────────────┐
                                                             │ YES                     │ NO
                                                             ▼                         ▼
                                                           ┌──────────────┐  ┌─────────────────┐
                                                           │ FULL_RENAME  │  │ SKIP (safe      │
                                                           │ Replace name │  │ default)        │
                                                           │ entirely     │  │ Can't clearly   │
                                                           │ with AI title│  │ classify →      │
                                                           └──────────────┘  │ leave it alone. │
                                                                             └─────────────────┘
```

### What each outcome means in plain terms

```
  SKIP:
  ┌─────────────────────────────────────────┐
  │ Original name:   "Q2 Strategy.md"       │
  │ AI suggested:    "Q2 Movies Strategy"   │
  │ Final name:      "Q2 Strategy.md"  ← unchanged
  └─────────────────────────────────────────┘

  AUGMENT:
  ┌─────────────────────────────────────────┐
  │ Original name:   "a meeting.md"         │
  │ AI suggested:    "Q2 Strategy Review"   │
  │ Final name:      "a meeting - Q2 Strategy Review.md"
  │                   ↑ kept    ↑ AI adds topic info
  └─────────────────────────────────────────┘

  FULL_RENAME:
  ┌─────────────────────────────────────────┐
  │ Original name:   "xkjdhfs83.pdf"        │
  │ AI suggested:    "Q2 Movies Deck"       │
  │ Final name:      "Q2 Movies Deck.pdf"   │
  │                   ↑ fully replaced      │
  └─────────────────────────────────────────┘
```

### Component map

```
  config/config.yaml              ← tune behavior here, no coding needed
  ┌────────────────────────────────────────────────────────────────┐
  │  rename_gate:                                                  │
  │    office_extensions: [".md", ".docx", ".xlsx", ".pptx", ...] │
  │    max_stem_length: 120   ← max characters in a filename      │
  └────────────────────────────────────────────────────────────────┘
                    │ rules provided to
                    ▼
  core/rename_gate.py             ← NEW: the decision engine
  ┌────────────────────────────────────────────────────────────────┐
  │  Input:  file path, AI's suggested title (from Step 3),       │
  │          whether file was captured before, config rules       │
  │  Logic:  runs the 4-rule flowchart (no new AI calls)          │
  │  Output: SKIP / AUGMENT / FULL_RENAME + the final filename    │
  └────────────────────────────────────────────────────────────────┘
                    │ decision flows into
                    ▼
  pipelines/capture.py            ← EXISTING, small edit in STORE step
  ┌────────────────────────────────────────────────────────────────┐
  │  Before: renames almost every file automatically              │
  │  After:  asks the gate first, then acts on the answer         │
  │                                                               │
  │  Writes one audit log entry per capture so every rename       │
  │  decision is traceable.                                       │
  └────────────────────────────────────────────────────────────────┘
         │ acts on decision                  │ records decision in
         ▼                                   ▼
  vault/writer.py                      audit_log (SQLite)
  (file moved or left alone,           (Phase 8 briefing reads this
   existing mechanics unchanged)        to summarise AI activity)
```

---

## Approach

Insert a pure-function gate (`core/rename_gate.py`) into the `store` stage of the
capture pipeline. The gate runs after the metadata stage (so the AI title is already
known) and before any filesystem rename. Config-driven thresholds in `RenameGateConfig`
mean all tunable values live in `config/config.yaml` — no code change needed to
adjust them. The existing rename mechanics (collision loop, rollback on failure,
audit trail) are unchanged; the gate only decides which action to take and what
the final filename is.

This approach is preferred over prompt-based instructions ("don't rename legible
files") because Python rules are enforced deterministically — LLM instructions are
advice that can be ignored.

---

## Phases

### Phase 1 — Gate module + config schema + unit tests
**Goal**: `core/rename_gate.py` exists, is fully tested in isolation, and its
tunables are wired into `config/config.yaml`.

**Steps**:
1. Add `RenameGateConfig` to `core/config.py`:
   ```python
   class RenameGateConfig(BaseModel):
       office_extensions: list[str] = Field(
           default_factory=lambda: [".md", ".docx", ".xlsx", ".pptx", ".txt"]
       )
       max_stem_length: int = 120

   # In CaptureConfig, add:
   rename_gate: RenameGateConfig = Field(default_factory=RenameGateConfig)
   ```
2. Add `rename_gate:` block under `capture:` in `config/config.yaml`:
   ```yaml
   capture:
     cooldown_seconds: 0
     max_urls_per_note: 3
     rename_gate:
       office_extensions: [".md", ".docx", ".xlsx", ".pptx", ".txt"]
       max_stem_length: 120
   ```
3. Create `core/rename_gate.py` with:
   - `RenameAction` enum: `SKIP | AUGMENT | FULL_RENAME`
   - `RenameDecision` frozen dataclass: `action`, `final_stem`, `reason`, `confidence`
   - `decide_rename(src, ai_title, is_existing_doc, config: RenameGateConfig) -> RenameDecision`
     implementing all 4 rules + conservative default
   - Private classifiers: `_is_legible`, `_is_generic`, `_is_illegible`
   - Private stem builders: `_sanitize_stem`, `_build_augmented_stem`
   - `_GENERIC_NAMES` as module-level frozenset constant
     (marked `# TODO: migrate to RenameGateConfig.generic_names` per TD-GATE-1)
   - **Correction vs. consulting code**: `_build_augmented_stem` fallback when
     `budget <= 0` must return `original_stem` (not `sanitized_title`) — the
     AUGMENT action always preserves the original name.
   - **Correction vs. consulting code**: `OFFICE_EXTENSIONS` is NOT a module
     constant — values come from `config.office_extensions`.

4. Write `tests/test_core/test_rename_gate.py` covering:
   - Rule 1: existing doc always SKIP (even illegible name)
   - Rule 2: legible office doc → SKIP; single-word office doc → falls through (not Rule 2)
   - Rule 3: generic placeholders → AUGMENT; `final_stem` contains both original and AI parts
   - Rule 3 edge: AUGMENT with stem near max_stem_length → `final_stem` = original stem only
   - Rule 4: keyboard mash → FULL_RENAME; hex hash → FULL_RENAME; UUID → FULL_RENAME;
     very short token → FULL_RENAME
   - Default: single legible word, non-office ext → SKIP
   - `_is_generic` ordering: "a meeting" is generic AND has 2 tokens — Rule 3 fires,
     not Rule 4 (regression guard for rule-ordering invariant)

**Files to modify**:
- `core/config.py` — add `RenameGateConfig`, update `CaptureConfig`
- `config/config.yaml` — add `rename_gate:` block under `capture:`
- `core/rename_gate.py` — new file
- `tests/test_core/test_rename_gate.py` — new file

**Test criteria**:
- [ ] `uv run pytest tests/test_core/test_rename_gate.py -v` passes with 0 failures
- [ ] `uv run pytest tests/test_core/test_config.py -v` passes (config schema change)
- [ ] `from core.rename_gate import decide_rename` is importable with no vault on disk
- [ ] `decide_rename` with `is_existing_doc=True` always returns `SKIP` regardless of filename

**Status**: [x] done

**Completed**: 2026-05-22
**Notes**: All 4 rules implemented. Two deviations from plan caught during implementation: (1) Rule 2 must check `not _is_generic(stem)` first to prevent "a meeting.md" from short-circuiting at Rule 2 — plan didn't call this out explicitly; (2) `_is_illegible` needed vowel-absence heuristic to catch keyboard mash like "xkdhgksjfs" (pure letter strings that look like words but have no vowels). `_build_augmented_stem` falls back to original_stem when AI title length exceeds budget (not just when budget ≤ 0). `# type: ignore[arg-type]` added to `rename_gate` Field per existing pattern in file.

---

### Phase 2 — Wire gate into capture pipeline
**Goal**: `pipelines/capture.py` no longer renames unconditionally; every rename
decision flows through `decide_rename` first, and every decision is audited.

**Steps**:
1. Add import to `pipelines/capture.py`:
   ```python
   from core.rename_gate import RenameDecision, decide_rename
   ```
2. Add private helper `_audit_rename_gate(decision, src, ctx)` that writes one
   `audit_log` row per gate decision (deduplicates the identical audit block that
   would otherwise exist in both `_store_md` and `_store_nonmd`).
3. Modify `_store_md` (lines ~305–308):
   - Before the rename block, look up whether this vault path already exists in
     the `documents` table via `documents.get_by_path(to_vault_path(src), db_path=ctx.db_path)`.
   - Call `decide_rename(src, mr.ai_title, is_existing_doc, config=ctx.config.capture.rename_gate)`.
   - Call `_audit_rename_gate(decision, src, ctx)`.
   - Replace `sanitized_stem = _sanitize_title(mr.ai_title)` with `sanitized_stem = decision.final_stem`.
   - The downstream rename block (`if sanitized_stem != src.stem:`) is unchanged.
4. Modify `_store_nonmd` (line ~383):
   - Call `decide_rename(src, mr.ai_title, is_existing_doc=False, config=ctx.config.capture.rename_gate)`.
     (No DB lookup needed: `scan_non_md_drops` structurally cannot return a binary
     that has already been moved to `attachment/` — non-md files are never re-processed.
     `is_existing_doc=False` always.)
   - Call `_audit_rename_gate(decision, src, ctx)`.
   - Replace `sanitized_stem = _sanitize_title(mr.ai_title) or src.stem`
     with `sanitized_stem = decision.final_stem`.
   - Downstream attachment/sibling logic is unchanged.
5. Update `tests/test_pipelines/test_capture_phase3.py` line 75
   (`test_store_md_different_title_renames_note`): change fixture stem from
   `"old-name"` (legible, 2 tokens → Rule 2 → SKIP) to `"xkdhgksjfs"` (illegible
   keyboard mash → Rule 4 → FULL_RENAME), so the test still exercises the rename path.

**Files to modify**:
- `pipelines/capture.py` — import, helper, modify `_store_md`, modify `_store_nonmd`
- `tests/test_pipelines/test_capture_phase3.py` — fix one test stem

**Test criteria**:
- [ ] `uv run pytest tests/test_pipelines/test_capture_phase3.py -v` passes
- [ ] `uv run pytest tests/test_pipelines/ -v` passes with 0 failures
- [ ] `uv run pytest tests/ -v` — full suite green
- [ ] Capturing the same `.md` file twice does not rename it on the second pass
      (manual smoke test or assertion in existing integration tests)
- [ ] Every capture produces an `audit_log` row with `stage="rename_gate"` and a
      non-null `outcome` (SKIP / AUGMENT / FULL_RENAME)

**Status**: [x] done

**Completed**: 2026-05-22
**Notes**: Gate wired into `_store_md` (with DB lookup for `is_existing_doc`) and `_store_nonmd` (always `is_existing_doc=False` per DECISION-018). `_audit_rename_gate` helper deduplicates audit write. Test fixtures in phase3, phase12, phase9 and integration tests updated: stems changed from legible to illegible (`"old-name"` → `"xkdhgksjfs"`, `"report.pdf"` → `"kqzxvbn.pdf"`, etc.) so tests still exercise rename path. Integration test audit-count assertion relaxed from `== 1` to `>= 1` (gate now adds second audit row per capture).

---

### Phase 3 — Integration tests
**Goal**: Three end-to-end scenarios verify the gate works correctly from file drop
through to final filename and audit entry.

**Steps**:
1. Create `tests/test_pipelines/test_capture_rename.py` with three test cases
   (mocked LLM provider, real tmp filesystem):

   **Test A — Re-capture of active file (SKIP)**
   - Setup: insert `"Q2 Strategy.md"` into the `documents` table to simulate prior capture.
   - Action: call `capture_file("Q2 Strategy.md")` with AI title `"Different Title"`.
   - Assert: file on disk is still named `"Q2 Strategy.md"` (not renamed).
   - Assert: `audit_log` has one row with `stage="rename_gate"`, `outcome="SKIP"`.

   **Test B — Generic placeholder (AUGMENT)**
   - Setup: drop `"a meeting.md"` into inbox (not in documents table).
   - Action: call `capture_file("a meeting.md")` with AI title `"Phong Q2 Sync"`.
   - Assert: file renamed to `"a meeting - Phong Q2 Sync.md"`.
   - Assert: `audit_log` row with `outcome="AUGMENT"`.

   **Test C — Illegible binary drop (FULL_RENAME)**
   - Setup: drop `"xkdhgksjfs.pdf"` into inbox.
   - Action: call `capture_file("xkdhgksjfs.pdf")` with AI title `"Q2 Movies Deck"`.
   - Assert: attachment moved to `attachments/Q2 Movies Deck.pdf`.
   - Assert: sibling note created as `"Q2 Movies Deck.md"`.
   - Assert: `audit_log` row with `outcome="FULL_RENAME"`.

   **Test D — LLM call count guard**
   - Action: call `capture_file` on any file.
   - Assert: mocked LLM provider was called exactly twice (summarize + metadata).
     No extra call from the gate.

**Files to modify**:
- `tests/test_pipelines/test_capture_rename.py` — new file

**Test criteria**:
- [ ] `uv run pytest tests/test_pipelines/test_capture_rename.py -v` passes (4 tests)
- [ ] `uv run pytest tests/ -m "not smoke" -v` — full non-smoke suite green

**Status**: [x] done

**Completed**: 2026-05-22
**Notes**: Two surprises fixed during implementation: (1) `WriteOutcome` has no `updated_by_human` constructor arg — uses `NoteMetadata` instead; pre-insert for Test A built with proper `WriteOutcome(vault_path, absolute_path, content_hash, metadata)`. (2) Blank PDF rejected by handler with "no extractable text" — replaced with a hand-crafted PDF 1.3 with a Type1 font text stream; pypdf emits a benign `incorrect startxref pointer` warning but parses and extracts text successfully. Both surprises were test-fixture issues, not production code issues — no plan deviation needed.

---

## Technical Debt created

| ID | What | Action |
|---|---|---|
| TD-GATE-1 | `_GENERIC_NAMES` is a hardcoded frozenset in `core/rename_gate.py`. Users cannot add "scratch", "wip", etc. without a code change. | Migrate to `RenameGateConfig.generic_names: list[str]` in a follow-up. Marked with `# TODO` in code. |
| TD-GATE-2 | `test_store_md_different_title_renames_note` (Phase 2 step 5) needs stem update from "old-name" to illegible. | Fixed in Phase 2 step 5 — resolved inline. |

---

## Open Questions

None blocking — all design questions resolved in `docs/research/rename_gate.md`.

---

## Out of Scope

- Adding a second LLM call to semantically check if the filename is related to content.
- Per-user customisation of `_GENERIC_NAMES` list (deferred to TD-GATE-1).
- Changes to the metadata prompt (`prompts/extract_metadata.yaml`) — no edits needed.
- Any changes to downstream rename mechanics: `_find_rename_dst`, `move_note`,
  rollback logic, `documents.replace_path`. These are untouched.
