# Plan: attachment_capture_pipeline Brief #2 (attachment_capture_pipeline)
_Last updated: 2026-05-23_
_Status: [x] done_

---

## Architecture

### Diagram 1 — Pipeline (data flow)

```
binary file dropped                          .md file dropped
(PDF, DOCX, XLSX…)                           (markdown note)
       │                                            │
       └──────────────────┬─────────────────────────┘
                          ▼
                   ┌────────────┐
                   │  extract() │  reads file content into text
                   └─────┬──────┘
                         │ RawContent(text, source_path, is_md)
                         ▼
                ┌──────────────────┐
                │  enrich_urls()   │  fetches linked web pages (if any)
                └──────┬───────────┘
                       │ RawContent (enriched)
                       ▼
                ┌──────────────────┐
                │  summarize()     │  AI: 2–4 sentence short summary
                └──────┬───────────┘
                       │ SummarizedContent(text, summary)
                       ▼
                ┌──────────────────┐
                │  metadata()      │  AI: title, tags, domain
                └──────┬───────────┘
                       │ MetadataResult(title, tags, ai_domain, decision)
                       ▼
                ┌──────────────────┐
                │  store()         │  writes to vault + database
                └──────────────────┘
                        │
              ┌─────────┴────────────┐
              ▼                      ▼
       LOCATED path              CLUELESS path
  (source path reveals           (no path context —
   project or domain)             binary stays/moves to inbox)
```

Note: pipeline stage count unchanged from current (5 stages). Destination resolution is
inline logic inside `store()/_store_nonmd()`, not a separate stage.

### Diagram 2 — Destination resolution inside `_store_nonmd()` (path-inference only)

```
  ┌───────────────────────────────────────────────────────────────────────────────────────┐
  │  _store_nonmd() — where does this binary go?  (pure path math, no AI, no thresholds) │
  └──────────────────────────────────────────┬────────────────────────────────────────────┘
                                             │ looks at: source file path only
                                             ▼
  ┌──────────────────────────────────────────────────────────────────────────────────────┐
  │  Is the file inside a SPECIFIC project subfolder?                                   │
  │  i.e. path starts with Projects/<A>/… where <A> is a named project folder           │
  │                                                                                      │
  │  ✓  Projects/Strategy/report.pdf           (loose file inside project)              │
  │  ✓  Projects/Strategy/attachment/data.xlsx  (already in project attachment/)        │
  │  ✗  Projects/report.pdf                    (dropped in Projects/ root — no project) │
  └────────────────────────┬──────────────────────────────────────────┬─────────────────┘
                          YES                                         NO
                           │                                           │
                           ▼                                           ▼
  ┌────────────────────────────────────┐   ┌──────────────────────────────────────────────────┐
  │ Already inside Projects/<A>/       │   │  Is the file inside a SPECIFIC domain subfolder? │
  │ attachment/ ?                      │   │  i.e. path starts with Domain/<D>/…              │
  └──────────┬─────────────────────────┘   │                                                  │
           YES            NO               │  ✓  Domain/Finance/report.pdf                    │
            │              │               │  ✓  Domain/Finance/attachment/deck.pptx          │
            ▼              ▼               │  ✗  Domain/report.pdf  (dropped in Domain/ root) │
  ┌─────────────┐  ┌────────────────────┐  └──────────────────────┬───────────────────────────┘
  │ No move.    │  │ Move binary to     │                        YES                  NO
  │ Write       │  │ Projects/<A>/      │                         │                    │
  │ sibling at  │  │ attachment/        │                         ▼                    ▼
  │ Projects/   │  │ Write sibling at   │             ┌──────────────────────┐  ┌───────────────────┐
  │ <A>/attach- │  │ Projects/<A>/      │             │ Already inside       │  │ CLUELESS          │
  │ ment/       │  │ attachment/        │             │ Domain/<D>/          │  │                   │
  │ .summaries/ │  │ .summaries/        │             │ attachment/ ?        │  │ Is source in      │
  └─────────────┘  └────────────────────┘             └────┬─────────────────┘  │ inbox/ ?          │
  → LOCATED                → LOCATED                     YES          NO         └───────┬───────────┘
                                                           │            │               YES         NO
                                                           ▼            ▼                │            │
                                                 ┌──────────────┐  ┌────────────────┐   │            │
                                                 │ No move.     │  │ Move to        │   ▼            ▼
                                                 │ Sibling at   │  │ Domain/<D>/    │ ┌──────────┐ ┌──────────────────┐
                                                 │ Domain/<D>/  │  │ attachment/    │ │ Binary   │ │ Binary moves to  │
                                                 │ attachment/  │  │ Sibling at     │ │ stays in │ │ inbox/ first.    │
                                                 │ .summaries/  │  │ .summaries/    │ │ inbox/.  │ │ Pending marker   │
                                                 └──────────────┘  └────────────────┘ │ Pending  │ │ at inbox/        │
                                                 → LOCATED              → LOCATED      │ marker   │ │ .summaries/      │
                                                                                       └──────────┘ └──────────────────┘
                                                                                       → CLUELESS    → CLUELESS

  CLUELESS triggers for:
    inbox/report.pdf          (no path context → Phase 2 resolves)
    Projects/report.pdf       (dropped in Projects/ root, no specific project)
    Domain/report.pdf         (dropped in Domain/ root, no specific domain)
    Briefings/notes.docx      (stray drop in unrecognised folder)
```

### Diagram 3 — `store()` LOCATED path (sibling-first ordering)

```
  Example A (needs_move=True):  Projects/Strategy/report.pdf
                                 → moves to Projects/Strategy/attachment/report.pdf
                                 → sibling at Projects/Strategy/attachment/.summaries/report.md

  Example B (needs_move=False): Projects/Strategy/attachment/report.pdf
                                 → already at destination, no move
                                 → sibling at Projects/Strategy/attachment/.summaries/report.md

  ────────────────────────────────────────────────────────────────────────────────────────
  _store_nonmd() — LOCATED path steps:

    Step 1 — Resolve destination via inline path check
             src = Projects/Strategy/report.pdf
             projects_path in src.parents → YES, len(rel.parts) >= 2 → YES
             target_type="project", target_name="Strategy"
             needs_move = (rel.parts[1] != "attachment") → True for Example A

    Step 2 — Decide final filename
             decide_rename(src, ai_title, config) → sanitized_stem
             (existing rename gate logic, unchanged)

    Step 3 — Call AI for rich structured body
             LLM call: prompts/summarize_attachment.yaml
             Input: file_type=".pdf", short_summary from summarize stage, text content
             Output: structured markdown with 3 sections (see Phase 2 prompt)

    Step 4 — WRITE SIBLING FIRST  ← (OQ-AC6 decision)
             Path: Projects/Strategy/attachment/.summaries/report.md
             Frontmatter:
               type: attachment-summary
               attachment_path: Projects/Strategy/attachment/report.pdf  ← DECISION-022
               summary: "2-sentence teaser from summarize stage"
               domain: strategy
               tags: [type/attachment-summary, domain/strategy]
             Body: structured markdown from Step 3

    Step 5 — MOVE BINARY (only if needs_move=True)
             Projects/Strategy/report.pdf → Projects/Strategy/attachment/report.pdf
             Skipped entirely if needs_move=False (binary already at destination).
             If move fails: sibling exists with broken attachment_path pointer (TD-C6).

    Step 6 — Write LOCATED audit entry
             pipeline=capture, stage=store, outcome="LOCATED"

    Step 7 — Upsert documents row
             vault_path = "Projects/Strategy/attachment/.summaries/report.md"
```

### Diagram 4 — `store()` CLUELESS path (two sub-cases)

```
  CASE A: binary dropped in inbox/ — no path context
  ──────────────────────────────────────────────────
  _store_nonmd() → target_type=None, source already in inbox/

    Step 1 — Binary STAYS at inbox/random.pdf        (no move)
    Step 2 — Write pending-marker at inbox/.summaries/random.md
               frontmatter: status=pending-routing,
                            attachment_path=inbox/random.pdf,
                            type=attachment-summary
    Step 3 — Write CLUELESS audit entry
    Step 4 — Upsert documents: vault_path = "inbox/.summaries/random.md"

  CASE B: binary dropped in wrong folder (e.g. Briefings/random.pdf)
  ──────────────────────────────────────────────────────────────────
  _store_nonmd() → target_type=None, source NOT in inbox/

    Step 1 — Move binary to inbox/: Briefings/random.pdf → inbox/random.pdf
             Collision handling: if inbox/random.pdf exists, use inbox/random-1.pdf etc.
    Step 2 — Write pending-marker at inbox/.summaries/random.md
    Step 3 — Write CLUELESS audit entry
    Step 4 — Upsert documents: vault_path = "inbox/.summaries/random.md"

  On NEXT scan: capture_file() sees status=pending-routing → skip.
  Phase 2 Classify will resolve CLUELESS files to project/domain.
```

### Diagram 5 — `scan_non_md_drops` skip rules

```
  TODAY (broken): skip rule = single global attachment_path check
  AFTER:          two rule-based skip checks

  ┌─────────────────────────────────────────────────────────────────┐
  │ SKIP Rule 1 — already in managed attachment folder             │
  │ File lives inside Projects/<A>/attachment/ or                  │
  │ Domain/<D>/attachment/                                         │
  │ → captured binary. Will never be a new drop.                   │
  ├─────────────────────────────────────────────────────────────────┤
  │ SKIP Rule 2 (NEW) — already-processed inbox file              │
  │ File is inside inbox/ AND                                      │
  │ inbox/.summaries/<stem>.md EXISTS                              │
  │ → already handled: LOCATED or pending-routing marker written.  │
  └─────────────────────────────────────────────────────────────────┘

  SCAN everything else:
    inbox/ files without a sibling → new drop
    files in other folders          → stray drop (capture moves to inbox)

  Indexer .summaries/ allowlist (amends DECISION-024):
    TODAY:  traverse .summaries/ only when parent folder = "attachment/"
    AFTER:  also traverse when parent folder = "inbox/"
            (to index pending-routing siblings for Phase 8 briefing)
```

### Diagram 6 — Components changed

```
  ┌──────────────────────────────────────────────────────────────┐
  │  pipelines/capture.py  [extensible: list]                    │
  │  Rewrites _store_nonmd() with per-project paths,             │
  │  inline destination resolution, rich sibling body.           │
  │  Adds early-exit guard for pending-routing binaries.         │
  │  Updates scan_capture() to use new indexer signature.        │
  ├──────────────────────────────────────────────────────────────┤
  │  vault/indexer.py  [extensible: config]                      │
  │  Changes scan_non_md_drops() signature.                      │
  │  Adds _is_in_managed_attachment() + _has_inbox_sibling().    │
  │  Extends .summaries/ allowlist to include inbox/ parent.     │
  ├──────────────────────────────────────────────────────────────┤
  │  prompts/summarize_attachment.yaml  [new file]               │
  │  Rich structured body prompt for attachment siblings.        │
  ├──────────────────────────────────────────────────────────────┤
  │  config/tags.yaml  [extensible: config]                      │
  │  Adds attachment-summary to type/ taxonomy.                  │
  └──────────────────────────────────────────────────────────────┘

  NOT CHANGED: handlers/, vault/paths.py, vault/frontmatter.py,
               vault/reader.py, vault/writer.py (signatures unchanged),
               config/thresholds.yaml, storage/schema.sql,
               storage/documents.py, mcp_server/,
               cli/main.py (l.127 COUPLING marker → Brief #3 / TD-023)
```

---

## Approach

Rewrite `_store_nonmd()` to use per-project attachment layout via `vault/paths.py` helpers. Destination resolution is inline path math (no AI, no stage): if source path is inside `Projects/<A>/` or `Domain/<D>/`, route there; otherwise CLUELESS (pending-routing marker, binary stays/moves to inbox). Phase 2 Classify handles domain- and project-inferred routing for inbox drops — that's its core job, not Brief #2's. The indexer's `scan_non_md_drops` is generalized from a single-path skip to rule-based skip logic. `summarize_attachment` prompt provides rich sibling body replacing the old bare wikilink.

Pipeline stage count stays at 5. No new stages. No new thresholds config.

---

## Phases

### Phase 1 — Taxonomy

**Goal**: Add `attachment-summary` type tag so sibling notes have a valid, searchable type.

**Steps**:
1. In `config/tags.yaml`, add `attachment-summary` to the `type:` list.

**Files to modify**:
- `config/tags.yaml` — add `attachment-summary` to `type/` taxonomy

**Test criteria**:
- [ ] `attachment-summary` appears in loaded tag taxonomy (test via `core/tags.py::load_taxonomy()`)
- [ ] `uv run pytest tests/test_core/ -m "not smoke"` passes with no new failures

**Status**: [x] done

**Completed**: 2026-05-24
**Notes**: Added `attachment-summary` to `config/tags.yaml`. Updated two count-based tests from 8→9 and added `test_attachment_summary_in_allowed_types`. No deviations from plan.

---

### Phase 2 — `summarize_attachment` prompt

**Goal**: New YAML prompt for generating rich sibling body content.

**Steps**:
1. Create `prompts/summarize_attachment.yaml` with system + user fields and variables `[file_type, short_summary, text]`.
   - System: instructs AI to return plain markdown with exactly 3 sections:
     `## What this file is`, `## Key content`, `## Key facts / findings`
   - User: passes `file_type`, `short_summary` (from summarize stage), and `text` (extracted content)
2. Verify `PROMPTS["summarize_attachment"]` loads without error at import time.

**Files to modify**:
- `prompts/summarize_attachment.yaml` — new file

**Test criteria**:
- [ ] `PROMPTS["summarize_attachment"].render(file_type=".pdf", short_summary="x", text="y")` returns `(system_str, user_str)` without error
- [ ] Rendered user string contains all three variable values
- [ ] Missing variable raises `UndefinedError` (StrictUndefined enforcement)

**Status**: [x] done

**Completed**: 2026-05-24
**Notes**: Created `prompts/summarize_attachment.yaml` with 3-section system prompt and `[file_type, short_summary, text]` variables. Added 4 tests to `test_prompt_loader.py`. No deviations from plan.

---

### Phase 3 — Rewrite `_store_nonmd()`

**Goal**: Replace old global-attachment logic with per-project paths, inline destination resolution, sibling-first write order, rich body, and CLUELESS inbox handling.

**Steps**:
1. **Inline destination resolution** at top of `_store_nonmd()` (no new stage, no AI call):
   ```python
   src = mr.raw.source_path
   vault_cfg = ctx.config.vault
   target_type, target_name, needs_move = None, None, False

   if vault_cfg.projects_path in src.parents:
       rel = src.relative_to(vault_cfg.projects_path)
       if len(rel.parts) >= 2:   # guard: must be Projects/<A>/file, not Projects/file
           target_type, target_name = "project", rel.parts[0]
           needs_move = rel.parts[1] != vault_cfg.attachment_dir
       # else: dropped directly into Projects/ root → CLUELESS
   elif vault_cfg.domain_path in src.parents:
       rel = src.relative_to(vault_cfg.domain_path)
       if len(rel.parts) >= 2:   # guard: must be Domain/<D>/file, not Domain/file
           target_type, target_name = "domain", rel.parts[0]
           needs_move = rel.parts[1] != vault_cfg.attachment_dir
       # else: dropped directly into Domain/ root → CLUELESS
   # else: inbox/, Briefings/, or other location → CLUELESS
   ```
   Files dropped into generic `Projects/` or `Domain/` root (no specific project/domain subfolder)
   fall through to CLUELESS — same as inbox drops. Phase 2 Classify resolves them.
2. **LOCATED path** (`target_type` is not None):
   a. Compute `attachment_dir` via `project_attachment(target_name)` or `domain_attachment(target_name)` from `vault/paths.py`
   b. Compute `summaries_dir` via `project_summaries(target_name)` or `domain_summaries(target_name)`
   c. Compute final `sanitized_stem` via existing `decide_rename()`
   d. Resolve collision on `attachment_dir / f"{sanitized_stem}{suffix}"` (existing collision loop)
   e. Call `summarize_attachment` prompt via LLM: `PROMPTS["summarize_attachment"].render(file_type=src.suffix.lower(), short_summary=mr.summary, text=mr.raw.text)`
   f. **Write sibling first**: `write_note(summaries_dir / f"{sanitized_stem}.md", rich_body, sibling_meta)` where `sibling_meta.attachment_path = to_vault_path(attachment_dst)` (NFC-normalized vault-relative path). Do NOT set `source_file` on sibling (see Notes).
   g. **Move binary second**: `move_attachment(src, attachment_dst)` — only if `needs_move=True`; skip if binary already at destination
   h. Write `LOCATED` audit entry: `pipeline="capture", stage="store", outcome="LOCATED"`
   i. `documents.upsert(sibling_outcome)` with `vault_path = sibling_vault_path`
   j. Remove `# COUPLING: capture.py:456` comment
3. **CLUELESS path** (`target_type` is None):
   a. If `src` inside `inbox_path`: binary stays, no move
   b. If `src` NOT inside `inbox_path`: move binary to `inbox_path / src.name` (collision-safe), update `src` reference
   c. Write pending-marker sibling at `inbox_path / vault_cfg.summaries_subdir / f"{src.stem}.md"` with `status="pending-routing"`, `type="attachment-summary"`, `attachment_path=to_vault_path(src_final_path)`. Do NOT set `source_file`.
   d. Create `inbox/.summaries/` dir if not exists (`mkdir(parents=True, exist_ok=True)`)
   e. Write `CLUELESS` audit entry: `pipeline="capture", stage="store", outcome="CLUELESS"`
   f. `documents.upsert()` with pending marker's `vault_path`
4. **Early-exit guard** at entry of `capture_file()` for non-md binaries: read sibling path (`inbox_path / summaries_subdir / f"{path.stem}.md"`); if exists and frontmatter `status == "pending-routing"`, return `Success` early — skip re-processing.

**Files to modify**:
- `pipelines/capture.py` — rewrite `_store_nonmd()`, add early-exit guard in `capture_file()`

**Test criteria**:
- [ ] LOCATED project (needs_move=True): sibling at `project_summaries(name)` path; `attachment_path` frontmatter = vault-relative binary path; binary moved to `project_attachment(name)`
- [ ] LOCATED project (needs_move=False): sibling written; `move_attachment` NOT called; binary unchanged
- [ ] LOCATED domain: same pattern, using `domain_attachment` / `domain_summaries`
- [ ] Sibling body contains all 3 sections (`## What this file is`, `## Key content`, `## Key facts / findings`)
- [ ] `source_file` NOT set on any sibling note (Q10 resolution)
- [ ] CLUELESS — dropped directly into `Projects/` root (no `<A>/` subfolder): treated as CLUELESS, pending-marker written
- [ ] CLUELESS — dropped directly into `Domain/` root (no `<D>/` subfolder): same
- [ ] CLUELESS in inbox: binary stays; pending-marker sibling at `inbox/.summaries/<stem>.md`; `status=pending-routing`
- [ ] CLUELESS outside inbox: binary moved to `inbox/`; pending-marker sibling at `inbox/.summaries/<stem>.md`
- [ ] Re-scan of CLUELESS binary (sibling with `status=pending-routing` exists): `capture_file()` returns `Success` early, no audit write, no re-move
- [ ] `attachment_path` frontmatter value is NFC-normalized vault-relative string
- [ ] No direct `.write_text()` or `open(..., 'w')` calls (hook enforcement)
- [ ] LOCATED audit row written for located path; CLUELESS audit row written for clueless path

**Notes**:
- `to_vault_path(path)` from `vault/paths.py` applies NFC normalization — use for all `attachment_path` values.
- `write_note` writes exactly what the caller passes (TD-014). Construct full `NoteMetadata` with all desired fields.
- **`source_file` must NOT be set on sibling notes** (Q10 resolution). `source_file` is reserved for `.md`-capture source tracking (external URL, email ID). Using `attachment_path` exclusively avoids ambiguous semantics for Phase 4 MCP consumers.
- If `move_attachment` raises after sibling is written: sibling exists with broken `attachment_path` pointer. Accepted failure mode (OQ-AC6 decision). TD-C6 tracks reconciliation pass in Brief #3.
- **Phase 2 Classify extension point.** CLUELESS files in inbox will be resolved by Phase 2. Phase 2 reads the pending-routing marker, classifies to project/domain, calls the same `project_attachment` / `domain_attachment` helpers, writes the full sibling, and clears `status=pending-routing`. No changes needed to `_store_nonmd()` at that point — Phase 2 is a separate pipeline operating on already-indexed sibling notes.

**Status**: [x] done

**Completed**: 2026-05-24
**Notes**: Rewrote `_store_nonmd()` with LOCATED (sibling-first, LLM rich body, binary move) and CLUELESS (pending-routing marker, binary to inbox) paths. Added early-exit guard in `capture_file()`. Fixed pre-existing bug: `vault/writer.py::_merge_metadata` was not forwarding `attachment_path` field (1-line fix, approved surprise). Removed COUPLING:456 comment. Updated 8 stale tests across test_capture_phase3.py, test_capture_phase12.py, test_capture_phase9.py, test_capture_rename.py. Added 1 test to test_writer.py. 613 tests pass.

---

### Phase 4 — Fix `scan_non_md_drops` and `scan_capture`

**Goal**: Replace single global attachment-path skip with rule-based skip. Update `scan_capture` caller. Extend `.summaries/` allowlist for `inbox/`.

**Steps**:
1. In `vault/indexer.py`, change `scan_non_md_drops` signature:
   ```python
   # OLD
   def scan_non_md_drops(root: Path, attachment_path: Path) -> list[Path]:
   # NEW
   def scan_non_md_drops(root: Path, vault_config) -> list[Path]:
   ```
2. Replace the single `if attachment_path in file_path.parents` guard with two rules:
   - Rule 1: add helper `_is_in_managed_attachment(file_path, vault_cfg) -> bool`
     - Returns True if `file_path` lives inside `Projects/<A>/attachment/` or `Domain/<D>/attachment/`
     - Checks: any parent named `attachment_dir` whose grandparent is `projects_path` or `domain_path`
   - Rule 2: add helper `_has_inbox_sibling(file_path, vault_cfg) -> bool`
     - Returns True if `file_path` is inside `inbox_path` AND `inbox_path / summaries_subdir / f"{file_path.stem}.md"` exists
3. Skip logic in scan loop:
   ```python
   if _is_in_managed_attachment(file_path, vault_config): continue
   if _has_inbox_sibling(file_path, vault_config): continue
   ```
4. Extend `_DOT_ALLOWLIST` prune condition in **both** `scan_non_md_drops` and `scan_vault`:
   ```python
   # OLD
   (not d.startswith(".") or (d in _DOT_ALLOWLIST and dirpath.name == "attachment"))
   # NEW
   (not d.startswith(".") or (d in _DOT_ALLOWLIST and dirpath.name in ("attachment", vault_config.inbox_dir)))
   ```
   `scan_vault` must also traverse `inbox/.summaries/` so pending-routing sibling `.md` files are indexed on subsequent scans.
5. In `pipelines/capture.py`, update `scan_capture` (l. 628–630):
   - Remove: `_attachment_path = CONFIG.main.vault.root / CONFIG.main.vault.attachment_dir`
   - Change: `scan_non_md_drops(_root, _attachment_path)` → `scan_non_md_drops(_root, ctx.config.vault)`
   - Remove `# COUPLING: capture.py:628` comment (TD-022 partially resolved)
6. Update `tests/test_vault/test_indexer.py` to pass `vault_config` mock instead of single `attachment_path: Path` (TD-C5).

**Files to modify**:
- `vault/indexer.py` — new signature, `_is_in_managed_attachment()`, `_has_inbox_sibling()`, extended allowlist condition
- `pipelines/capture.py` — update `scan_capture` caller, remove COUPLING:628 comment
- `tests/test_vault/test_indexer.py` — update existing tests for new signature

**Test criteria**:
- [ ] File at `Projects/A/attachment/x.pdf` → skipped (Rule 1)
- [ ] File at `Domain/D/attachment/x.pdf` → skipped (Rule 1)
- [ ] File at `inbox/x.pdf` with `inbox/.summaries/x.md` existing → skipped (Rule 2)
- [ ] File at `inbox/x.pdf` with NO sibling → scanned
- [ ] File at `Briefings/stray.docx` → scanned (stray drop)
- [ ] `scan_capture` with per-project vault: no files from `Projects/<A>/attachment/` returned
- [ ] `inbox/.summaries/*.md` files are traversed by `scan_vault` (indexed as `.md` notes)
- [ ] All existing `test_indexer.py` tests pass with updated signature

**Notes**:
- `vault_config` type hint can use `TYPE_CHECKING` guard to avoid module-scope import (same pattern as `PipelineContext.config` — DECISION-012).
- COUPLING marker at `cli/main.py:127` (watcher `attachment_path` arg) remains — Brief #3 scope (TD-023). This phase does NOT touch `cli/main.py`.

**Status**: [x] done

**Completed**: 2026-05-24
**Notes**: Changed `scan_non_md_drops` signature from `(root, attachment_path: Path)` to `(root, vault_config: VaultConfig)`. Added `_is_in_managed_attachment()` (Rule 1: skip per-project/domain attachment subtrees) and `_has_inbox_sibling()` (Rule 2: skip inbox binaries with existing .summaries sibling). Extended `_DOT_ALLOWLIST` prune in both `scan_non_md_drops` and `scan_vault` to traverse `inbox/.summaries/`. Updated `scan_capture` caller, removed COUPLING:628. Updated 6 existing test_indexer.py tests to new signature; updated 1 stale test in test_capture_phase9.py (global `attachment/` → per-project). TD-022 (capture.py:628) and TD-024 retired. 617 tests pass.

---

## Open Questions

| ID | Question | Blocks |
|---|---|---|
| OQ-Q006 | Wikilink path shape in sibling body: full vault-relative `[[Projects/A/attachment/report.pdf]]` is the recommendation (STATE.md Q-006). Must be tested on a real vault before shipping. | Phase 3 |
| OQ-AC4 | `summarize_attachment` prompt quality on real files not yet tested. May need iteration after first real vault run. | Phase 2 |

---

## Out of Scope

- Domain- and project-inferred routing for inbox drops — Phase 2 Classify scope. Phase 2 extends `_store_nonmd()` by resolving CLUELESS pending-routing markers to a specific project or domain.
- `cli/main.py:127` COUPLING marker (watcher `attachment_path` arg) — Brief #3 / TD-023
- Orphan reconciliation (binary in `attachment/` with no sibling) — Brief #3 / TD-C6
- `docs/research/capture_pipeline.md` non-md section annotation (old layout) — post-ship / TD-020
- `docs/roadmap.md` Phase 1 attachment layout description — post-ship / TD-021
- Watcher per-project attachment skip generalization — Brief #3 / TD-023

---

## Technical Debt Retired by This Plan

| ID | Retired in Phase |
|---|---|
| TD-022 (COUPLING capture.py:456 + capture.py:628) | Phase 3 (l.456), Phase 4 (l.628) |
| TD-024 (`scan_non_md_drops` single-path skip) | Phase 4 |
| TD-C5 (test_indexer.py old signature) | Phase 4 |
| TD-C7 (`attachment-summary` missing from tags.yaml) | Phase 1 |
| TD-C8 (`summarize_attachment.yaml` missing) | Phase 2 |
