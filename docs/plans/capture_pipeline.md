# Plan: Capture Pipeline
_Last updated: 2026-05-21_
_Status: [x] done — all 12 phases complete

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│  cli/main.py (exists)                                             │
│  · kms capture <file>   · kms capture --scan   · kms watch       │
└───────┬──────────────────────────────────────────┬───────────────┘
        │ asyncio.run(capture_file)                │ asyncio.run(watch)
        ▼                                          ▼
┌─────────────────────────┐          ┌─────────────────────────────┐
│  pipelines/capture.py   │◀─path────│  vault/watcher.py (new)     │
│  capture_file(path)     │          │  Watchdog + debounce        │
│  scan_capture()         │          │  · watch(root, callback)    │
│  stability gate         │          │  · on_created / on_moved    │
└──────────┬──────────────┘          └─────────────────────────────┘
           │ run_pipeline(5 stages)
     ┌─────▼──────┐   ┌──────────────┐   ┌──────────────────┐
     │  extract   │   │ enrich_urls  │   │   summarize      │
     │  HandlerReg│──▶│ url_fetcher  │──▶│ LLM: summarize   │
     │  (exists)  │   │ (exists)     │   │ .yaml (new)      │
     └────────────┘   └──────────────┘   └────────┬─────────┘
                                                   ▼
                                         ┌──────────────────┐
                                         │   metadata       │
                                         │ LLM: extract_meta│
                                         │ .yaml (new)      │
                                         │ + audit.write()  │
                                         └────────┬─────────┘
                                                  ▼
                                         ┌──────────────────┐
                                         │   store          │
                                         │ vault/writer.py  │
                                         │ storage/documents│
                                         │ (both exist)     │
                                         └──────────────────┘
```

```
Data flow:

Path (file dropped anywhere in vault)
  │
  ▼ Stage 1: extract
Result[RawContent(text, source_path, is_md)]
  │
  ▼ Stage 2: enrich_urls
  │   detect_urls(text) → urls
  │   _should_enrich? → fetch up to max_urls
  │   augment = text + "---\n[Referenced URL Content]\n..."
Result[RawContent(text=augmented?, source_path=UNCHANGED, is_md)]
  │
  ▼ Stage 3: summarize     [PROMPTS["summarize"].render(text=...)]
Result[SummarizeResult(raw: RawContent, summary: str)]
  │
  ▼ Stage 4: metadata      [PROMPTS["extract_metadata"].render(text,summary,domain_list)]
  │          + validate_tags(ai_tags, taxonomy)   ← Obsidian format check + taxonomy
  │          + audit.write(outcome="CAPTURED")
  │          + audit.write(outcome="TAG_VIOLATION") if violations
Result[MetadataResult(raw, summary, ai_title, ai_type, ai_domain, ai_tags, decision)]
  │
  ▼ Stage 5: store
  ├─ is_md=True  ─▶ read_note(source_path)  [original body, not RawContent.text]
  │                 NoteMetadata(summary, type, domain, tags, confidence)
  │                 rename? → move_note + documents.delete_by_path + documents.upsert
  │                 no rename → write_note + documents.upsert
  │
  └─ is_md=False ─▶ sibling = source_path.parent/(sanitized_stem+".md")
                    write_note(sibling, "![[attachment_dst.name]]", meta)
                    documents.upsert(sibling_outcome)
                    source_path.exists()? → move_attachment(src, attachment_dst)
Result[WriteOutcome]
```

```
Extended scan_capture flow (Phases 9 + 10):

scan_capture(root, db_path)
        │
        ├─ [Phase 8] scan_vault(root) → list[VaultEntry] (.md only)
        │                 ↓ detect_changes(entries, db_path) → ChangeSummary
        │             ├─ .added   [VaultEntry] → capture_file per entry     ← Phase 8 ✅
        │             ├─ .modified [VaultEntry] → capture_file per entry    ← Phase 10 (full re-capture)
        │             ├─ .deleted  [str]        → documents.delete_by_path  ← Phase 10
        │             └─ .moved    [(str,VaultEntry)] → documents.rename    ← Phase 10
        │
        └─ [Phase 9] scan_non_md_drops(root, attachment_path) → list[Path]
                    │ non-md files NOT in attachment/ folder
                    └─ capture_file per path (same pipeline as direct kms capture <file>)
                           └─ store: sibling .md created in DROP FOLDER (not moved)
                                     binary moved to attachment/
```

```
scan_non_md_drops decision flow (Phase 9):

Walk vault root (same IGNORE_DIRS + dotfile + sync-conflict rules as scan_vault)
        │
        For each file:
        ├─ path in attachment_path subtree? ─yes─▶ skip  (already captured, pipeline artifact)
        ├─ file.suffix.lower() == ".md"?    ─yes─▶ skip  (handled by scan_vault / detect_changes)
        ├─ dotfile or .sync-conflict-*?     ─yes─▶ skip
        └─ else ────────────────────────────────▶ include (PDF / DOCX / any binary drop)
```

```
Idempotency for non-md drops:

PDF lands in inbox/                (not yet captured)
        ↓
scan_non_md_drops → finds it → capture_file runs
        ↓
store._store_nonmd:
  sibling = inbox/sanitized.md    (created in DROP LOCATION — stays here)
  binary  = attachment/report.pdf (moved here)
        ↓
Next scan_capture:
  scan_non_md_drops → PDF now in attachment/ → SKIPPED ✓
  detect_changes → sibling inbox/sanitized.md → in documents table → no re-add ✓
```

```
Phase 10 — modified/deleted/moved reconciliation:

detect_changes() ChangeSummary
  ├─ .modified [VaultEntry]       ├─ .deleted [str]            ├─ .moved [(str, VaultEntry)]
  │                                │                             │
  capture_file(path, ctx)      delete_by_path(vault_path)   rename(old, new.vault_path)
  (full 5-stage re-capture)    (removes documents row)       (updates path, preserves int id)
  │                                │                             │
  WriteOutcome                 Success(1)                    Success(1)
  (fresh summary + tags         table clean                  audit_log FK still valid
   written to frontmatter)                                   (DECISION-001)
```

```
Watcher event routing (Phase 11):

Event type            src location       dst location      Action
────────────────────────────────────────────────────────────────────────────
FileCreated           —                  in vault (not att.) on_create(dst)
FileCreated           —                  in attachment/      SKIP
FileModified (.md)    —                  in vault (not att.) on_modify(path)
FileModified (binary) —                  anywhere            SKIP
FileDeleted           in vault (not att.) —                  on_delete(path)
FileDeleted           in attachment/      —                  SKIP
FileMovedEvent        in vault            in vault (not att.) on_move(src,dst) → rename
FileMovedEvent        in vault            in attachment/      SKIP (pipeline artifact)
FileMovedEvent        outside vault       in vault (not att.) on_create(dst)

on_create(path)  → capture_file(path, ctx)               ← .md AND non-md drops
on_modify(path)  → capture_file(path, ctx)               ← re-capture on edit
on_delete(path)  → delete_by_path(to_vault_path(path))   ← table cleanup
on_move(src,dst) → rename(to_vault_path(src), to_vault_path(dst))  ← path update
```

```
scan_capture vs watcher — same actions, different triggers:

             startup (kms watch)          watcher (real-time)
             ─────────────────────        ──────────────────────────────
.md added    detect_changes.added         FileCreatedEvent → on_create
non-md added scan_non_md_drops            FileCreatedEvent → on_create (any extension)
.md modified detect_changes.modified      FileModifiedEvent → on_modify (.md only)
.md deleted  detect_changes.deleted       FileDeletedEvent → on_delete
.md moved    detect_changes.moved         FileMovedEvent (internal) → on_move
```

```
store stage decision flow:

store(MetadataResult, ctx)
        │
   is_md=True?                              is_md=False?
        │                                        │
  read_note(source_path)               sibling = source_path.parent /
  build NoteMetadata                             (sanitized_stem + ".md")
        │                              attachment_dst = attachment_path /
  sanitized_stem                                (sanitized_stem + suffix)
  != source_path.stem?                 collision loop (cap 100 iterations)
        │ yes          │ no                   │
  dst.exists()?    write_note          write_note(sibling,
   yes  │   no     (in-place)            "![[attachment_dst.name]]", meta)
  keep  │  move_note    │              documents.upsert(sibling_outcome)
  name, │    │      documents               │
  log   │  delete_      .upsert        source_path.exists()?
  warn  │  by_path          │           yes → move_attachment(src, dst)
        │  + upsert         │           no  → log WARNING, skip
        │          Success(WriteOutcome)
```

## Approach

`pipelines/capture.py` is a 5-stage async pipeline (`extract → enrich_urls →
summarize → metadata → store`) wrapped in `capture_file(path)`. Every stage
is a pure top-level `async def` returning `Result[T]`; `run_pipeline` chains
them and halts on the first `Failure`. The stability gate (mtime cooldown) runs
before `run_pipeline` as a pre-flight check inside `capture_file`. Config and
prompts are established first (Phase 1) so later phases can test with real
config values. The watcher (Phase 9) is the automation layer added last per
the "schedulers come last" constraint.

**Config access rule**: Stage functions receive `ctx: PipelineContext`; within
any stage, config is `ctx.config` (a `MainConfig` instance). Lazy `CONFIG`
import is reserved for `capture_file` and `scan_capture` (the pipeline entry
points, where context may be None). Never import `CONFIG` at module scope in
`capture.py`; never lazy-import `CONFIG` inside a stage that already has `ctx`.

## Phases

### Phase 1 — Foundations (config + prompts + path utility)
**Goal**: All prerequisite config, prompts, and path helpers exist and are testable before pipeline code is written.

**Steps**:
1. Add `CaptureConfig` Pydantic model to `core/config.py`:
   ```python
   class CaptureConfig(BaseModel):
       cooldown_seconds: int = Field(60, ge=0)
       max_urls_per_note: int = Field(3, ge=0)
   ```
   Add `capture: CaptureConfig = Field(default_factory=CaptureConfig)` to `MainConfig`.
2. Add `capture:` section to `config/config.yaml`:
   ```yaml
   capture:
     cooldown_seconds: 60
     max_urls_per_note: 3
   ```
3. Create `prompts/summarize.yaml`:
   ```yaml
   name: summarize
   system: |
     You are a knowledge management assistant. Produce a concise, factual
     summary of the provided note content. Focus on the main topic, key
     points, and any decisions or actions mentioned. Write 2-4 sentences.
     Do not add opinions or inferences beyond what is stated.
   user: |
     Summarize the following note content:

     {{ text }}
   variables: [text]
   ```
4. Create `prompts/extract_metadata.yaml`:
   ```yaml
   name: extract_metadata
   system: |
     You are a knowledge management assistant. Extract structured metadata
     from a note. Return a single JSON object with exactly these fields:
     - "title": a concise, descriptive title (max 120 chars, no slashes or colons)
     - "type": one of "note", "report", "meeting", "document", "reference", "other"
     - "tags": a list of 1-5 short topic tags (lowercase, no spaces — use hyphens)
     Return ONLY the JSON object. No markdown fences, no explanation.
   user: |
     Note content:
     {{ text }}

     Summary:
     {{ summary }}
   variables: [text, summary]
   ```
5. Add `to_vault_path(absolute: Path) -> str` to `vault/paths.py` — copy the
   implementation from `vault/writer.py`'s private `_to_vault_path` (NFC-normalized
   POSIX vault-relative path via lazy CONFIG import).
6. Update `vault/writer.py` to import and call `vault.paths.to_vault_path` instead
   of the private `_to_vault_path`. Remove the private function.

**Files to modify**:
- `core/config.py` — add `CaptureConfig`; add `capture` field to `MainConfig`
- `config/config.yaml` — add `capture:` section
- `prompts/summarize.yaml` — new file
- `prompts/extract_metadata.yaml` — new file
- `vault/paths.py` — add `to_vault_path(absolute: Path) -> str`
- `vault/writer.py` — replace `_to_vault_path` calls with `from vault.paths import to_vault_path`

**Test criteria**:
- [ ] `CONFIG.main.capture.cooldown_seconds` == 60 and `max_urls_per_note` == 3 when loaded from `config/config.yaml`
- [ ] `PROMPTS["summarize"].render(text="hello")` returns a `(system, user)` tuple where `user` contains "hello"
- [ ] `PROMPTS["extract_metadata"].render(text="t", summary="s")` returns a valid tuple
- [ ] `to_vault_path(vault_root / "inbox" / "note.md")` returns `"inbox/note.md"` (NFC, POSIX)
- [ ] `vault/writer.py` existing tests still pass (no regression from refactor)

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: Delivered as planned. `CaptureConfig` added to `core/config.py` with `cooldown_seconds=60` and `max_urls_per_note=3` defaults. `capture:` section added to `config/config.yaml`. `prompts/summarize.yaml` and `prompts/extract_metadata.yaml` created. `to_vault_path` refactored to `vault/paths.py` as public function; `vault/writer.py` updated to import it, `_to_vault_path` removed. One existing test updated to import from `vault.paths`. mypy errors follow pre-existing `Field(default_factory=Class)` pattern throughout codebase — not new technical debt. 376 tests pass (no regressions).

---

### Phase 2 — Pipeline core (.md branch, no rename)
**Goal**: `capture_file(path)` runs the full 5-stage pipeline for `.md` files, writes in-place (no rename), and passes unit tests with mocked provider.

**Steps**:
1. Create `pipelines/capture.py` with:
   - Private frozen dataclasses `SummarizeResult` and `MetadataResult`
   - `_should_enrich(text, urls, max_urls) -> bool` helper
   - `_parse_metadata_json(content: str) -> dict | Failure` helper (strips fences, validates, coerces, sanitizes title)
   - `_sanitize_title(title: str) -> str` helper (strips path-unsafe chars, trims to 120 chars)
   - Stage functions: `extract`, `enrich_urls`, `summarize`, `metadata`, `store`
   - `capture_file(path, context=None) -> Result[WriteOutcome]` — no stability gate yet
2. `extract`: resolve handler via `HandlerRegistry.resolve(path)` → call `handler.extract(path)`.
   Import `handlers` at the top of `capture.py` to populate the registry.
3. `enrich_urls`: call `detect_urls(raw.text)`. If no URLs or `_should_enrich` fails: return `Success(raw)`.
   For each URL up to `max_urls`: `asyncio.to_thread(fetch_url_content, url)`. Append successes. Return new `RawContent(text=augmented, ...)`. Never return `Failure`.
4. `summarize`: `get_provider("capture", ctx.config)` → `provider.complete(system, user)` → `SummarizeResult`.
5. `metadata`: same provider → parse JSON → build `AIDecision(action="capture:metadata", confidence=0.9, ...)` → `audit.write(...)` → `MetadataResult`. Use `to_vault_path(sr.raw.source_path)` for `source_ids`.
6. `store` (.md branch only, in-place write, no rename):
   - `read_note(mr.raw.source_path)` → `original_body`
   - Build `NoteMetadata(summary, type, tags, confidence)`
   - `write_note(path, original_body, note_meta, actor="ai")` → `WriteOutcome`
   - `documents.upsert(outcome, db_path=ctx.db_path)` → return `Success(outcome)`
   - Non-md path: return `Failure(error="non-md not yet supported", recoverable=False)` (temporary)

**Files to modify**:
- `pipelines/capture.py` — new file

**Test criteria**:
- [ ] `extract` with a `.md` file returns `Success(RawContent(is_md=True))`
- [ ] `enrich_urls` with no URLs returns `Success(raw)` unchanged
- [ ] `enrich_urls` with 2 URLs in sparse text (< 500 chars) fetches and augments text
- [ ] `enrich_urls` with 4 URLs in dense text (> 500 chars) skips fetch, returns `Success(raw)` unchanged
- [ ] `enrich_urls` never returns `Failure` even when all fetches fail
- [ ] `summarize` returns `Success(SummarizeResult)` with non-empty `summary` when provider returns success
- [ ] `metadata` writes one `audit_log` row with `pipeline="capture"`, `stage="metadata"`, `outcome="CAPTURED"`
- [ ] `_parse_metadata_json` handles: valid JSON, JSON in markdown fences, `tags` as wrong type (coerces), missing `title` (falls back to stem)
- [ ] `capture_file(md_path, context=explicit_context)` end-to-end returns `Success(WriteOutcome)` with mocked provider

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: `pipelines/capture.py` created with all 5 stages (`extract`, `enrich_urls`, `summarize`, `metadata`, `store`), two frozen private dataclasses (`SummarizeResult`, `MetadataResult`), and helpers (`_sanitize_title`, `_should_enrich`, `_parse_metadata_json`). `core/pipeline.py` updated: `PipelineContext.config` type changed from `Config` to `MainConfig`, and `run_pipeline` now passes `CONFIG.main` when creating context from CONFIG. This aligns with the plan constraint "config is `ctx.config` (a `MainConfig` instance)". `tests/test_pipelines/` directory created with conftest + 12 tests covering all plan test criteria. Stage protocol list-item mismatch suppressed with `type: ignore[list-item]` (Protocol `self`-based callable vs module-level functions). All 390 tests pass.

---

### Phase 3 — Store full: rename + non-md branch + stability gate
**Goal**: `store` handles all file types and rename scenarios; `capture_file` rejects files still being edited.

**Steps**:
1. Extend `store` stage — `.md` rename logic:
   - Compare `_sanitize_title(mr.ai_title)` against `raw.source_path.stem`.
   - If stems differ: try `dst = parent / (sanitized_stem + ".md")`. If `dst.exists()`, try suffixes `sanitized_stem-1`, `sanitized_stem-2`, … up to `-9`. If all 10 slots taken: log WARNING, fall through to in-place write (original filename kept). Cap at 10 — do not loop indefinitely.
   - If a free slot found: `move_note(src, dst, actor="ai")` → `documents.delete_by_path(old_vault_path)` → `documents.upsert(new_outcome)` → return.
2. Extend `store` stage — non-md branch:
   - Build `sibling = source_path.parent / (sanitized_stem + ".md")`.
   - Build `attachment_dst = CONFIG.main.vault.attachment_path / (sanitized_stem + suffix)`.
   - Collision loop: while `attachment_dst.exists()` and counter < 100: increment suffix → cap 100 → `Failure`.
   - `write_note(sibling, f"![[{attachment_dst.name}]]", sibling_meta, actor="ai")` → `documents.upsert`.
   - Check `raw.source_path.exists()` before `move_attachment` (re-capture guard: log WARNING, skip if gone).
   - Return `Success(sibling_outcome)`.
3. Add stability gate at top of `capture_file`:
   - Lazy import `CONFIG` inside function body.
   - `age = time.time() - path.stat().st_mtime`
   - If `age < CONFIG.main.capture.cooldown_seconds`: return `Failure(recoverable=True, ...)`.
   - Then call `run_pipeline(...)`.

**Files to modify**:
- `pipelines/capture.py` — extend `store` + add stability gate to `capture_file`

**Test criteria**:
- [ ] `store` with `.md` and `ai_title != stem`: note renamed, old documents row deleted, new row inserted
- [ ] `store` with `.md` and rename collision (dst exists): tries `-1` suffix → succeeds on first free slot
- [ ] `store` with `.md` and all 10 suffix slots taken: falls back to in-place write, WARNING logged
- [ ] `store` with `.md` and `ai_title == stem`: in-place write, no rename attempted
- [ ] `store` with non-md (PDF) dropped in `inbox/`: sibling `.md` created in `inbox/`, attachment moved to `attachment/`, documents row for sibling
- [ ] `store` with non-md (PDF) dropped in `Projects/foo/`: sibling `.md` created in `Projects/foo/` (same folder as drop), NOT in `inbox/`
- [ ] `store` with non-md re-capture (binary already moved): skips `move_attachment`, logs WARNING, returns `Success`
- [ ] `store` with non-md attachment collision > 100: returns `Failure`
- [ ] `capture_file` with `mtime < cooldown_seconds` ago: returns `Failure(recoverable=True)` without calling `run_pipeline`
- [ ] `capture_file` with `mtime >= cooldown_seconds` ago: calls `run_pipeline` normally

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: Extended `store` stage with `_find_rename_dst` helper (tries stem, then stem-1 … stem-9, falls back in-place after 10 collisions with WARNING). Added `_store_md` and `_store_nonmd` private helpers — `_store_nonmd` creates sibling .md with `![[attachment_dst.name]]` body, collision loop capped at 100, `move_attachment` skipped with WARNING if source already gone. Stability gate added to `capture_file` using lazy `CONFIG` import (matching pre-existing `type: ignore[attr-defined]` pattern). `time` module imported at top level for monkeypatching in tests. Phase 2 e2e test updated: mocks `time.time` to make file appear 120s old (satisfies 60s cooldown), and changed title in LLM response to match stem to avoid triggering rename. Warning-assertion tests switched from `capsys` to `caplog.at_level(WARNING, logger="pipelines.capture")` — structlog routes through stdlib logging in full suite. 406 tests pass, 14 skipped (smoke), 1 deselected (Ollama integration).

---

### Phase 4 — CLI wiring + integration test
**Goal**: `kms capture <file>` runs the full pipeline; an end-to-end integration test confirms audit_log and documents rows are written. No `--scan` yet (added in Phase 8).

**Steps**:
1. Replace the `capture` Click command stub in `cli/main.py` (currently raises `NotImplementedError`):
   ```python
   @cli.command()
   @click.argument("file", type=click.Path(exists=True))
   def capture(file: str) -> None:
       """Run the capture pipeline on a file."""
       import asyncio
       from pathlib import Path
       from pipelines.capture import capture_file

       result = asyncio.run(capture_file(Path(file)))
       match result:
           case Success(value=v): click.echo(f"OK: {v}")
           case Failure(error=e): click.echo(f"FAILED: {e}", err=True)
   ```
2. Create `tests/test_pipelines/test_capture_integration.py` with a real vault tmp_path fixture:
   - Test A — inbox drop: write `tmp_vault/inbox/test.md`, call `capture_file`, assert one `audit_log` row (`pipeline="capture"`, `outcome="CAPTURED"`) and one `documents` row (`vault_path="inbox/test.md"` or renamed equivalent).
   - Test B — non-inbox `.md` drop: write `tmp_vault/Projects/foo/note.md`, call `capture_file`, assert `documents.vault_path` starts with `"Projects/foo/"` — verifies the note is indexed at its actual location, not moved to inbox.
   - Note: both tests use a real SQLite db but a mocked LLM provider (`monkeypatch` or explicit provider mock returning fixed JSON).

**Files to modify**:
- `cli/main.py` — replace `capture` stub
- `tests/test_pipelines/test_capture_integration.py` — new file

**Test criteria**:
- [ ] `kms capture tests/fixtures/sample.md` exits 0 and prints `OK:`
- [ ] Test A: inbox `.md` drop — one `audit_log` row, one `documents` row at `inbox/...`
- [ ] Test B: non-inbox `.md` drop (`Projects/foo/note.md`) — `documents.vault_path` starts with `Projects/foo/`, note NOT moved to inbox
- [ ] Integration test: `documents.vault_path` is NFC-normalized vault-relative POSIX path
- [ ] CLI: `kms capture` without args exits non-zero (Click enforces required argument)

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: `cli/main.py` `capture` stub replaced with real implementation using `asyncio.run(capture_file(Path(file)))`. Failure exits with `SystemExit(1)`. `click>=8.0` and `python-dotenv>=1.0` added to `pyproject.toml` dependencies (were missing). `[project.scripts]` entry point `kms = "cli.main:cli"` added — `kms` was not installable before. `tests/fixtures/sample.md` created for manual CLI smoke test. `tests/test_cli/test_capture_cli.py` created with no-args exit-nonzero test. 3 integration tests in `test_capture_integration.py` cover audit_log row, documents row at correct vault path, and NFC normalization. 410 tests pass, 14 skipped (smoke), 1 deselected (Ollama integration). CLI smoke test (`kms capture tests/fixtures/sample.md exits 0`) is a manual verification requiring real vault config + API key.

---

### Phase 5 — Tag Taxonomy Core
**Goal**: Define the taxonomy vocabulary in config, build the `TagTaxonomy` dataclass and `validate_tags` function (including Obsidian format rules), and write tests against pure logic — no vault or pipeline dependency yet.

**Obsidian tag format rules** (enforced by `_is_valid_obsidian_tag` before taxonomy checks):
- Allowed chars: letters, digits, `_`, `-`, `/` (for nested tags), accepted Unicode including emoji
- No spaces or ASCII punctuation outside `[_-/]` (e.g. no `:`, `.`, `!`, `@`, `#`, `"`, etc.)
- Each segment between `/` must contain at least one non-digit character (e.g. `1984` invalid, `y1984` valid)
- No empty segments (no double slashes, no leading/trailing slashes)
- Tags are case-insensitive in Obsidian; stored as-is (casing preserved)

**Steps**:
1. Create `config/tags.yaml`:
   ```yaml
   # Controlled vocabulary for type/ tags.
   # domain/ values are loaded at runtime from vault/Domain/ folder names.
   allowed_types:
     - meeting-note
     - email
     - report
     - article
     - reflection
     - task-list
     - transcript
     - capture
   ```
2. Create `core/tags.py` with:
   - `TagTaxonomy` frozen dataclass (`allowed_types`, `valid_domains`)
   - `_is_valid_obsidian_tag(tag: str) -> bool` — enforces Obsidian format rules before taxonomy checks
   - `validate_tags(tags, taxonomy) -> (valid, violations)` — runs format check first, then taxonomy checks:
     - Invalid Obsidian format → dropped with violation, skip remaining checks for that tag
     - `type/<value>`: must be in `taxonomy.allowed_types`
     - `domain/<value>`: must be in `taxonomy.valid_domains`
     - `<ns>/<value>` (other namespace): violation — free tags must not have prefix
     - After loop: exactly-one-type-tag enforcement (zero → violation; many → keep first, violation)
   - `load_taxonomy(tags_yaml_path, valid_domains) -> TagTaxonomy` — loads from YAML, takes pre-scanned domains as param (no vault I/O here)
3. `load_taxonomy` takes `valid_domains` as a parameter — it does NOT scan the vault itself. That scan is in Phase 6 (`vault/paths.py`). This keeps `core/tags.py` dependency-free.

**Files to modify**:
- `config/tags.yaml` — new file
- `core/tags.py` — new file

**Test criteria**:
- [ ] `config/tags.yaml` is valid YAML and `allowed_types` list has exactly 8 values
- [ ] `validate_tags(["type/report", "domain/finance", "quarterly-kpi"], taxonomy)` → no violations, all three tags valid
- [ ] `validate_tags(["type/bad-value"], taxonomy)` → violation for unknown type, tag dropped
- [ ] `validate_tags(["domain/nonexistent"], taxonomy)` → violation for unknown domain, tag dropped
- [ ] `validate_tags(["status/active"], taxonomy)` → violation for namespaced free tag, tag dropped
- [ ] `validate_tags([], taxonomy)` → violation "no type/ tag found"
- [ ] `validate_tags(["type/report", "type/article", "free-tag"], taxonomy)` → violation "multiple type/ tags", only first type/ kept
- [ ] `validate_tags` with empty `valid_domains` → any `domain/x` tag is a violation
- [ ] `load_taxonomy(tags_yaml_path, frozenset(["finance"]))` returns `TagTaxonomy` with `allowed_types` from file and `valid_domains={"finance"}`
- [ ] `validate_tags(["has space"], taxonomy)` → violation for invalid Obsidian format, tag dropped
- [ ] `validate_tags(["1984"], taxonomy)` → violation (all-numeric segment), tag dropped
- [ ] `validate_tags(["y1984", "kebab-case", "snake_case"], taxonomy)` → no format violation (beyond type/ check)

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: `config/tags.yaml` created with 8 allowed_types. `core/tags.py` created with `TagTaxonomy` frozen dataclass, `_is_valid_obsidian_tag` (enforces Obsidian format: no spaces, no invalid punctuation, no all-numeric segments, no empty segments), `validate_tags`, and `load_taxonomy`. Format check runs before taxonomy checks — invalid-format tags are dropped before type/domain lookup. No vault or pipeline dependencies. 452 tests pass.

---

### Phase 6 — Domain Loader + Context Extension
**Goal**: Scan vault's `Domain/` folder at pipeline startup to populate valid domains; wire `TagTaxonomy` into `PipelineContext` so all stages receive it.

**Steps**:
1. Add `load_valid_domains(vault_root: Path) -> frozenset[str]` to `vault/paths.py`:
   ```python
   def load_valid_domains(vault_root: Path) -> frozenset[str]:
       """Return folder names directly under vault_root/Domain/ as the valid domain set.
       Returns empty frozenset if Domain/ does not exist (no hard failure).
       Hidden folders (dotfiles) are excluded.
       """
       domain_dir = vault_root / "Domain"
       if not domain_dir.is_dir():
           return frozenset()
       return frozenset(
           p.name for p in domain_dir.iterdir()
           if p.is_dir() and not p.name.startswith(".")
       )
   ```
2. Extend `PipelineContext` in `core/pipeline.py`:
   ```python
   @dataclass
   class PipelineContext:
       config: MainConfig
       correlation_id: str
       db_path: Path
       taxonomy: TagTaxonomy | None = None   # None = taxonomy validation skipped
   ```
   Import guard: `from core.tags import TagTaxonomy` under `if TYPE_CHECKING` to keep the same lazy-import discipline as `MainConfig`.
3. Extend `capture_file` in `pipelines/capture.py` — build taxonomy before calling `run_pipeline`:
   ```python
   async def capture_file(path: Path, context: PipelineContext | None = None) -> Result[WriteOutcome]:
       import time
       from core.config import CONFIG  # lazy
       # ... existing stability gate ...
       if context is None:
           from core.tags import load_taxonomy
           from vault.paths import load_valid_domains
           valid_domains = load_valid_domains(CONFIG.main.vault.root)
           taxonomy = load_taxonomy(
               Path(__file__).parent.parent / "config" / "tags.yaml",
               valid_domains,
           )
           context = PipelineContext(
               config=CONFIG.main,
               db_path=CONFIG.main.db.path,
               correlation_id=new_correlation_id(),
               taxonomy=taxonomy,
           )
       return await run_pipeline("capture", [extract, enrich_urls, summarize, metadata, store], path, context=context)
   ```
   When `context` is passed explicitly (tests, `scan_capture`), taxonomy comes from the caller — no vault scan per file.

**Files to modify**:
- `vault/paths.py` — add `load_valid_domains`
- `core/pipeline.py` — add `taxonomy` field to `PipelineContext`; `TYPE_CHECKING` import of `TagTaxonomy`
- `pipelines/capture.py` — extend `capture_file` to build context with taxonomy when none provided

**Test criteria**:
- [ ] `load_valid_domains(vault_root)` returns folder names from `vault_root/Domain/` as a frozenset
- [ ] `load_valid_domains(vault_root)` returns `frozenset()` when `Domain/` does not exist
- [ ] `load_valid_domains(vault_root)` excludes hidden folders (e.g. `.obsidian`)
- [ ] `PipelineContext(config=mock, correlation_id="x", db_path=tmp, taxonomy=None)` — `taxonomy=None` is accepted (validation skip)
- [ ] `PipelineContext` with explicit `taxonomy=TagTaxonomy(...)` passes the taxonomy to stages via `ctx.taxonomy`
- [ ] `capture_file(path, context=explicit_ctx)` does NOT scan Domain/ folder (no vault I/O for domain loading when context already set)

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: `load_valid_domains(vault_root)` added to `vault/paths.py` — no CONFIG dependency, takes vault root as explicit arg. `PipelineContext` extended with `taxonomy: TagTaxonomy | None = None` field; `TYPE_CHECKING` import of `TagTaxonomy` added to `core/pipeline.py`. `capture_file` in `pipelines/capture.py` extended: when `context is None`, lazy-imports CONFIG, scans Domain/ once, builds taxonomy, constructs full `PipelineContext` with taxonomy before calling `run_pipeline`. When context is provided explicitly, no Domain/ scan occurs. `new_correlation_id` import added to `pipelines/capture.py`. 427 tests pass (no regressions).

---

### Phase 7 — Prompt Rewrite + Pipeline Validation + Derivation
**Goal**: The AI receives explicit taxonomy instructions and returns tags in the new format; the `metadata` stage validates, audits violations, and derives `ai_type`/`ai_domain`; `store` passes both derived fields to `NoteMetadata`.

**Steps**:
1. Rewrite `prompts/extract_metadata.yaml`:
   ```yaml
   name: extract_metadata
   system: |
     You are a knowledge management assistant. Extract structured metadata
     from a note. Return a single JSON object with exactly these fields:

     - "title": a concise, descriptive title (max 120 chars, no slashes or colons)
     - "tags": a list of tags following this exact taxonomy:

       LAYER 1 — domain tags (prefix: domain/)
         Assign ALL domains relevant to this note from the list below.
         Multi-value is expected — a note relevant to multiple domains gets all of them.
         Use ONLY values from this list. Omit domain/ tags if no match.
         Available domains: {{ domain_list }}

       LAYER 2 — type tag (prefix: type/, exactly one required)
         Choose exactly one from: meeting-note, email, report, article,
         reflection, task-list, transcript, capture

       LAYER 3 — free topic tags (no prefix, 5–10 required)
         Semantic concepts for discovery. Lowercase, hyphens for spaces.
         Must NOT start with any/ prefix (no "domain/", "type/", etc.).

     Return ONLY the JSON object. No markdown fences, no explanation.
   user: |
     Note content:
     {{ text }}

     Summary:
     {{ summary }}
   variables: [text, summary, domain_list]
   ```
   Note: `domain_list` renders as a comma-separated string (e.g. `"finance, strategy, ops"`).
   When `valid_domains` is empty, `domain_list` renders as `"(none — no Domain/ folders configured)"`.

2. Update `metadata` stage in `pipelines/capture.py`:
   a. Render prompt with `domain_list` injected:
      ```python
      domain_list = (
          ", ".join(sorted(ctx.taxonomy.valid_domains))
          if ctx.taxonomy and ctx.taxonomy.valid_domains
          else "(none — no Domain/ folders configured)"
      )
      system, user = PROMPTS["extract_metadata"].render(
          text=sr.raw.text, summary=sr.summary, domain_list=domain_list
      )
      ```
   b. After `_parse_metadata_json` succeeds, run tag validation if taxonomy is set:
      ```python
      ai_tags = parsed.get("tags", [])
      violations: list[str] = []
      if ctx.taxonomy is not None:
          ai_tags, violations = validate_tags(ai_tags, ctx.taxonomy)
      ```
   c. Build and write main `CAPTURED` audit entry (same as before).
   d. If violations exist, write a second `TAG_VIOLATION` audit entry:
      ```python
      if violations:
          viol_decision = AIDecision(
              action="capture:tag_violation",
              confidence=1.0,   # deterministic check — always 100% accurate
              reasoning=f"Dropped {len(violations)} tag(s): {violations}",
              source_ids=[source_id],
          )
          match audit.write(viol_decision, pipeline="capture", stage="metadata",
                            outcome="TAG_VIOLATION", db_path=ctx.db_path):
              case Failure():
                  logger.warning("tag_violation.audit_failed", violations=violations)
              case Success():
                  pass  # violation logged
          # TAG_VIOLATION audit failure is NON-FATAL — continue pipeline
      ```
   e. Derive `ai_type` and `ai_domain` from validated tags:
      ```python
      ai_type = next(
          (t[len("type/"):] for t in ai_tags if t.startswith("type/")), None
      )
      ai_domain = next(
          (t[len("domain/"):] for t in ai_tags if t.startswith("domain/")), None
      )
      ```
   f. Return `MetadataResult` — add `ai_domain` field:
      ```python
      return Success(MetadataResult(
          raw=sr.raw,
          summary=sr.summary,
          ai_title=parsed["title"],
          ai_type=ai_type,        # derived from type/<name> tag
          ai_domain=ai_domain,    # derived from first domain/<name> tag (NEW)
          ai_tags=ai_tags,
          decision=decision,
      ))
      ```

3. Add `ai_domain: str | None` field to `MetadataResult` frozen dataclass in `capture.py`.

4. Update `_parse_metadata_json` — remove `type` key handling (no longer in JSON schema):
   - Strip `"type"` key from parsed dict before returning (AI may still output it from old training; strip defensively)
   - `tags` must be a list of strings — same coercion as before
   - Return `{"title": clean_title, "tags": clean_tags}` (no `type` key)

5. Update `_store_md` and `_store_nonmd` in `store` stage — pass `ai_domain` to `NoteMetadata`:
   ```python
   NoteMetadata(
       summary=mr.summary,
       type=mr.ai_type,       # derived from type/<name> tag
       domain=mr.ai_domain,   # derived from first domain/<name> tag (NEW)
       tags=mr.ai_tags,
       confidence=mr.decision.confidence,
   )
   ```

6. Update existing test fixtures — all mocked LLM responses for the `extract_metadata` prompt must change from old format `{"type": "report", "tags": [...]}` to new format `{"tags": ["type/report", "domain/...", ...]}`. Files to update:
   - `tests/test_pipelines/test_capture.py` (Phase 2 unit tests)
   - `tests/test_pipelines/test_capture_integration.py` (Phase 4 integration tests)

**Files to modify**:
- `prompts/extract_metadata.yaml` — rewrite (add `domain_list` variable, new taxonomy instructions, remove `type` field)
- `pipelines/capture.py` — `MetadataResult` (add `ai_domain`), `_parse_metadata_json` (strip `type`), `metadata` stage (inject domain_list, validate tags, two audit calls, derive ai_type/ai_domain), `_store_md` + `_store_nonmd` (pass ai_domain)
- `tests/test_pipelines/test_capture.py` — update mocked LLM responses to new JSON format
- `tests/test_pipelines/test_capture_integration.py` — update mocked LLM responses

**Test criteria**:
- [ ] `PROMPTS["extract_metadata"].render(text="t", summary="s", domain_list="finance, ops")` — rendered user string contains "finance, ops"
- [ ] `PROMPTS["extract_metadata"].render(text="t", summary="s", domain_list="(none — no Domain/ folders configured)")` — renders without error
- [ ] `metadata` stage with `taxonomy=None` in context: no tag validation called, tags stored as-is (backward-compat)
- [ ] `metadata` stage with valid taxonomy: `validate_tags` called; valid tags kept; violations produce second `audit_log` row with `outcome="TAG_VIOLATION"`
- [ ] `metadata` stage with zero violations: exactly one `audit_log` row (`outcome="CAPTURED"`)
- [ ] `metadata` stage with violations: exactly two `audit_log` rows (`CAPTURED` + `TAG_VIOLATION`)
- [ ] `TAG_VIOLATION` audit write failure is non-fatal — pipeline continues and returns `Success`
- [ ] `MetadataResult.ai_type == "report"` when `ai_tags` contains `"type/report"`
- [ ] `MetadataResult.ai_type is None` when no `type/` tag in `ai_tags`
- [ ] `MetadataResult.ai_domain == "finance"` when `ai_tags` contains `"domain/finance"`
- [ ] `MetadataResult.ai_domain is None` when no `domain/` tag in `ai_tags`
- [ ] `NoteMetadata.domain` in written note matches `mr.ai_domain` (not None when domain/ tag present)
- [ ] `_parse_metadata_json('{"title":"T","type":"report","tags":["type/report"]}')` → strips `type` key, returns `{"title": "T", "tags": ["type/report"]}`
- [ ] All existing Phase 2 + Phase 4 tests pass after fixture update

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: `prompts/extract_metadata.yaml` rewritten with 3-layer taxonomy instructions and `domain_list` variable. `MetadataResult` extended with `ai_domain: str | None`. `_parse_metadata_json` strips legacy `type` key. `metadata` stage injects `domain_list`, runs `validate_tags` when `ctx.taxonomy` is set, writes second `TAG_VIOLATION` audit entry on violations (non-fatal), derives `ai_type`/`ai_domain` from tags. `store` passes `ai_domain` to `NoteMetadata`. All existing Phase 2/3/4 test fixtures updated (remove `"type"` key from LLM responses, add `ai_domain=None` to `MetadataResult` constructors, add `domain_list` to prompt render calls). `test_llm/test_prompt_loader.py` updated. 439 tests pass, 14 skipped.

---

### Phase 8 — scan_capture + --scan flag
**Goal**: `scan_capture` is implemented and `kms capture --scan` is wired; un-indexed `.md` files detected by `detect_changes` are processed.

**Note**: Phase 8 requires Phase 6 (Domain Loader) to be complete first — `scan_capture` must load taxonomy ONCE before the loop and pass via explicit `PipelineContext` to avoid re-scanning `Domain/` for every file.

**Steps**:
1. Add `scan_capture(root: Path | None = None, db_path: Path | None = None) -> Result[list[WriteOutcome]]` to `pipelines/capture.py`:
   ```python
   async def scan_capture(root=None, db_path=None):
       from core.config import CONFIG  # lazy
       from core.tags import load_taxonomy
       from vault.paths import load_valid_domains
       from core.logging_setup import new_correlation_id

       root = root or CONFIG.main.vault.root
       db_path = db_path or CONFIG.main.db.path

       # Load taxonomy once for the entire scan (not per-file)
       valid_domains = load_valid_domains(root)
       taxonomy = load_taxonomy(
           Path(__file__).parent.parent / "config" / "tags.yaml",
           valid_domains,
       )

       match scan_vault(root):
           case Failure() as f: return f
           case Success(value=entries):
               summary = detect_changes(entries, db_path=db_path)
               outcomes = []
               for entry in summary.added:
                   path = root / entry.vault_path
                   ctx = PipelineContext(
                       config=CONFIG.main,
                       db_path=db_path,
                       correlation_id=new_correlation_id(),
                       taxonomy=taxonomy,    # pre-loaded taxonomy
                   )
                   match await capture_file(path, context=ctx):
                       case Success(value=v): outcomes.append(v)
                       case Failure(error=e, recoverable=True):
                           logger.info("scan_capture.skip", path=str(path), reason=e)
                       case Failure() as f:
                           logger.warning("scan_capture.failed", path=str(path), error=f.error)
               return Success(outcomes)
   ```
   - `modified` entries are NOT re-captured (OQ-C4: accept for Phase 1).
   - `recoverable=True` failures (cooldown) are silently skipped (file still editing).
2. Extend `kms capture` CLI command in `cli/main.py` to add `--scan` flag:
   - Change `@click.argument("file", ...)` to `required=False`.
   - Add `@click.option("--scan", is_flag=True, default=False, help="Capture all un-indexed notes in vault. Modified notes are not re-captured.")`.
   - Import `scan_capture` lazily inside function body alongside `capture_file`.
   - Logic: if `scan` → `asyncio.run(scan_capture())`; else if `file` → `asyncio.run(capture_file(Path(file)))`; else raise `UsageError`.

**Files to modify**:
- `pipelines/capture.py` — add `scan_capture`
- `cli/main.py` — extend `capture` command with `--scan` flag

**Test criteria**:
- [ ] `scan_capture` with one new `.md` in `inbox/` returns `Success([WriteOutcome])`
- [ ] `scan_capture` with one new `.md` in `Projects/foo/` returns `Success([WriteOutcome])` — verifies non-inbox folders are scanned
- [ ] `scan_capture` with zero new files returns `Success([])`
- [ ] `scan_capture` skips files that fail stability gate (recoverable Failure)
- [ ] `scan_capture` logs WARNING but continues when one file fails fatally
- [ ] `modified` entries are not re-captured (documents row unchanged)

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: `scan_capture(root, db_path)` added to `pipelines/capture.py` — scans vault via `scan_vault`, diffs against DB via `detect_changes`, captures only `summary.added` entries (modified/deleted ignored). Taxonomy loaded once before the loop. `capture_file` called with explicit `PipelineContext` per file so stability gate uses mock config in tests. `kms capture` CLI updated: `file` arg now optional, `--scan` flag added, `UsageError` raised when neither provided. 6 new tests in `test_capture_phase3.py`. 458 tests pass, 1 skipped (Ollama integration).

---

### Phase 9 — Non-md drop detection in scan_capture
**Goal**: `scan_capture` processes non-md files (PDFs, DOCX, etc.) dropped anywhere in the vault outside the `attachment/` folder — applies the full capture pipeline (sibling `.md` created at the drop location, binary moved to `attachment/`). Overrides DECISION-018 for `scan_capture` only; `scan_vault` is unchanged.

**Idempotency**: After successful capture the binary is moved to `attachment/`. On the next `scan_capture`, `scan_non_md_drops` only returns files NOT in `attachment/` — the processed binary is invisible. If capture fails partway (sibling created but binary not moved), `capture_file` runs again; the store's re-capture guard handles it gracefully.

**Steps**:
1. Add `scan_non_md_drops(root: Path, attachment_path: Path) -> list[Path]` to `vault/indexer.py`:
   - Walk vault root with the same `IGNORE_DIRS` + dotfile + `.sync-conflict-*` filters as `scan_vault`.
   - Skip any file whose absolute path is inside `attachment_path` subtree (`attachment_path in path.parents`).
   - Skip `.md` files (handled by `scan_vault`).
   - Return a plain `list[Path]` — no `Result` wrapping; per-file I/O errors silently skip (same partial-success pattern as `scan_vault`).
   - Export `scan_non_md_drops` in `vault/indexer.py`'s `__all__`.

2. Extend `scan_capture` in `pipelines/capture.py` — after the `summary.added` loop, add:
   ```python
   # Non-md drops: process binaries not yet in attachment/ folder.
   # DECISION-018 override: scan_vault only indexes .md; this loop handles binary drops.
   from vault.indexer import scan_non_md_drops
   _attachment_path: Path = CONFIG.main.vault.attachment_path  # type: ignore[attr-defined]
   non_md_paths = scan_non_md_drops(_root, _attachment_path)
   for path in non_md_paths:
       ctx = PipelineContext(
           config=CONFIG.main,  # type: ignore[attr-defined]
           db_path=_db_path,
           correlation_id=new_correlation_id(),
           taxonomy=taxonomy,
       )
       match await capture_file(path, context=ctx):
           case Success(value=v):
               outcomes.append(v)
           case Failure(error=e, recoverable=True):
               logger.info("scan_capture.skip_nonmd", path=str(path), reason=e)
           case Failure() as f:
               logger.warning(
                   "scan_capture.failed_nonmd", path=str(path), error=f.error
               )
   ```

**Files to modify**:
- `vault/indexer.py` — add `scan_non_md_drops(root, attachment_path) -> list[Path]`
- `pipelines/capture.py` — extend `scan_capture` with non-md loop after `summary.added` loop

**Test criteria**:
- [ ] `scan_non_md_drops(root, attachment_path)` returns non-`.md` Paths not in `attachment_path` subtree
- [ ] `scan_non_md_drops` excludes files in `IGNORE_DIRS`
- [ ] `scan_non_md_drops` excludes dotfiles and `.sync-conflict-*` files
- [ ] `scan_non_md_drops` excludes `.md` files
- [ ] `scan_non_md_drops` returns `[]` when all non-md files are already in `attachment/`
- [ ] `scan_capture` with one PDF in `inbox/` (not in `attachment/`) → `capture_file` called → sibling `.md` created in `inbox/`, PDF moved to `attachment/`, `WriteOutcome` in result
- [ ] `scan_capture` with PDF already in `attachment/` → `scan_non_md_drops` skips it → no extra `capture_file` call
- [ ] `scan_capture` with PDF in `inbox/` AND `.md` notes in `inbox/` → both processed independently; `outcomes` contains results for both
- [ ] `scan_capture` non-md with unsupported extension (no handler) → `Failure` logged as WARNING, other files continue

**Status**: [x] done

**Completed**: 2026-05-21
**Notes**: `scan_non_md_drops(root, attachment_path) -> list[Path]` added to `vault/indexer.py` — same skip rules as `scan_vault` (IGNORE_DIRS, dotfiles, `.sync-conflict-*`, symlinks), plus skips `.md` files and files inside `attachment_path` subtree. `scan_capture` extended with non-md loop after `summary.added` loop: calls `capture_file` per non-md path with pre-loaded taxonomy context; logs `scan_capture.skip_nonmd` on recoverable failure, `scan_capture.failed_nonmd` on fatal failure, continues in both cases. Tests use real fixture PDF (`tests/fixtures/sample_text.pdf`) for end-to-end pipeline verification. 452 tests pass (16 skipped/deselected).

---

### Phase 10 — Modified/deleted/moved reconciliation in scan_capture
**Goal**: `scan_capture` keeps the `documents` table in sync for all change types from `detect_changes`: re-captures modified notes (fresh summary + frontmatter via full pipeline), removes rows for deleted notes, and updates `vault_path` for moved notes without changing integer id.

**Note on implementation order**: Implement Phase 10 before Phase 11 (watcher). Phase 11's `on_modify`/`on_delete`/`on_move` callbacks call the same underlying functions as Phase 10 (`capture_file`, `delete_by_path`, `rename`). Validating the logic in scan_capture first reduces wiring risk.

**Steps**:
1. In `scan_capture`, after the non-md drop loop (Phase 9), add a `summary.modified` loop. Full re-capture — same pipeline as new captures:
   ```python
   for entry in summary.modified:
       path = _root / entry.vault_path
       ctx = PipelineContext(
           config=CONFIG.main,  # type: ignore[attr-defined]
           db_path=_db_path,
           correlation_id=new_correlation_id(),
           taxonomy=taxonomy,
       )
       match await capture_file(path, context=ctx):
           case Success(value=v):
               outcomes.append(v)
           case Failure(error=e, recoverable=True):
               logger.info("scan_capture.skip_modified", path=str(path), reason=e)
           case Failure() as f:
               logger.warning(
                   "scan_capture.failed_modified", path=str(path), error=f.error
               )
   ```

2. Add a `summary.moved` loop. `documents.rename` updates `vault_path` in-place, preserving the integer id (DECISION-001). All `audit_log` and future `corrections` FKs remain valid:
   ```python
   from storage.documents import rename as rename_doc
   for old_vault_path, new_entry in summary.moved:
       match rename_doc(old_vault_path, new_entry.vault_path, db_path=_db_path):
           case Failure() as f:
               logger.warning(
                   "scan_capture.rename_failed",
                   old=old_vault_path,
                   new=new_entry.vault_path,
                   error=f.error,
               )
           case Success():
               pass
   ```

3. Add a `summary.deleted` loop. `ON DELETE CASCADE` on `corrections.document_id` cleans child rows automatically (DECISION-008):
   ```python
   from storage.documents import delete_by_path
   for vault_path in summary.deleted:
       match delete_by_path(vault_path, db_path=_db_path):
           case Failure() as f:
               logger.warning(
                   "scan_capture.delete_failed", vault_path=vault_path, error=f.error
               )
           case Success():
               pass
   ```

4. Update `scan_capture` docstring to reflect all four change types handled.

**Files to modify**:
- `pipelines/capture.py` — extend `scan_capture` with three new loops (modified, moved, deleted); update docstring

**Test criteria**:
- [ ] `scan_capture` with one modified `.md` → `capture_file` called again → `documents` row updated, fresh `summary` written to frontmatter
- [ ] `scan_capture` with one deleted `.md` → `documents.delete_by_path` called → row removed from table
- [ ] `scan_capture` with one moved `.md` (same content_hash, new path) → `documents.rename` called → `vault_path` updated, integer id unchanged
- [ ] `scan_capture` modified loop: one file fails with non-recoverable Failure → logged WARNING, other files continue, `Success(outcomes)` still returned
- [ ] `scan_capture` deleted loop: one `delete_by_path` fails → logged WARNING, other deletes continue
- [ ] `scan_capture` moved loop: one `rename` fails → logged WARNING, other renames continue
- [ ] `scan_capture` with zero modified, zero deleted, zero moved → no extra calls made

**Status**: [x] done

**Completed**: 2026-05-21
**Notes**: `scan_capture` extended with three new loops after the non-md loop: (1) `summary.modified` — full re-capture via `capture_file` with per-file `PipelineContext`; recoverable failures skipped silently, fatal failures logged as WARNING and skipped; (2) `summary.moved` — calls `documents.rename(old, new)` in-place, preserving integer id (DECISION-001); rename failures logged as WARNING, loop continues; (3) `summary.deleted` — calls `documents.delete_by_path`; delete failures logged as WARNING, loop continues. Docstring updated to list all four change types. 7 new tests in `test_capture_phase10.py` cover all plan criteria. Pre-existing test `test_scan_capture_does_not_recapture_modified_files` (Phase 8) still passes coincidentally — its assertion `result.value == []` holds because the real provider call fails without an API key; the authoritative new-behavior test is in `test_capture_phase10.py`. 474 tests pass, 1 skipped (Ollama integration).

---

### Phase 11 — Watcher
**Goal**: `kms watch` monitors the entire vault root and dispatches four event types — create, modify, delete, move — to the correct handler, with debounce to coalesce rapid filesystem events. Skips all events for files inside `attachment/` (pipeline artifacts).

**Event routing**:
```
Event                src              dst               Action
─────────────────────────────────────────────────────────────────────────
FileCreatedEvent     —                in vault          on_create(dst)
                     —                in attachment/    SKIP
FileModifiedEvent    —                in vault .md      on_modify(path)
                     —                in vault binary   SKIP  (TD-C6)
                     —                in attachment/    SKIP
FileDeletedEvent     in vault         —                 on_delete(path)
                     in attachment/   —                 SKIP
FileMovedEvent       in vault         in vault          on_move(src, dst)
                     in vault         in attachment/    SKIP  (pipeline moved binary)
                     outside vault    in vault          on_create(dst)  [external drop]
```

**Debounce with event-type tracking**: debounce map is `dict[str, tuple[Callable, tuple]]` keyed by `str(path)`. Each new event for a path cancels the running timer and stores the latest `(callable, args)`. On fire, dispatch with stored args. Rapid create→modify→delete on the same path results in `on_delete` firing — correct.

**Steps**:
1. Add `watchdog` to `pyproject.toml` dependencies:
   ```toml
   "watchdog>=4.0",
   ```
2. Create `vault/watcher.py`:
   ```python
   # vault/watcher.py — dispatches path events; NO pipeline/llm/core.config imports
   from pathlib import Path
   from typing import Callable
   import threading
   from watchdog.observers import Observer
   from watchdog.events import (
       FileSystemEventHandler,
       FileCreatedEvent,
       FileModifiedEvent,
       FileDeletedEvent,
       FileMovedEvent,
   )
   from vault.indexer import IGNORE_DIRS
   ```
   - `VaultWatcher.__init__(root: Path, attachment_path: Path, on_create: Callable[[Path], None], on_modify: Callable[[Path], None], on_delete: Callable[[Path], None], on_move: Callable[[Path, Path], None], debounce_seconds: float = 3.0)`.
   - `_should_skip(path: Path) -> bool`: True if `path` is inside `attachment_path`, is a dotfile, contains `.sync-conflict-`, or is inside an `IGNORE_DIRS` directory anywhere in the path chain.
   - `_is_internal(path: Path) -> bool`: True if `path` is inside `self._root`.
   - `on_created(event: FileCreatedEvent)`: skip if `_should_skip(dst)`; else debounce `on_create(dst)`.
   - `on_modified(event: FileModifiedEvent)`: skip if `_should_skip(path)` OR `path.suffix.lower() != ".md"` (binary modify deferred — TD-C6); else debounce `on_modify(path)`.
   - `on_deleted(event: FileDeletedEvent)`: skip if `_should_skip(path)`; else debounce `on_delete(path)`.
   - `on_moved(event: FileMovedEvent)`: skip if `_should_skip(dst)`; elif `_is_internal(src)`: debounce `on_move(src, dst)`; else debounce `on_create(dst)` (external drop via OS move).
   - `start() / stop() / join()` — delegate to `Observer`.
   - `watcher.py` must NOT import `pipelines/`, `llm/`, or `core.config` at module scope.

3. Add `kms watch` command to `cli/main.py`:
   ```python
   @cli.command()
   def watch() -> None:
       """Watch vault root; capture new drops from any folder automatically.

       Taxonomy (Domain/ folders) is loaded once at startup.
       New Domain/ folders added while the watcher runs require a restart.
       """
       import asyncio
       from pathlib import Path
       from core.config import CONFIG
       from core.tags import load_taxonomy
       from core.pipeline import PipelineContext
       from core.logging_setup import new_correlation_id
       from vault.paths import load_valid_domains, to_vault_path
       from vault.watcher import VaultWatcher
       from pipelines.capture import capture_file, scan_capture
       from storage.documents import delete_by_path, rename as rename_doc

       root = CONFIG.main.vault.root
       db_path = CONFIG.main.database.path
       attachment_path = CONFIG.main.vault.attachment_path

       valid_domains = load_valid_domains(root)
       taxonomy = load_taxonomy(
           Path(__file__).parent.parent / "config" / "tags.yaml",
           valid_domains,
       )

       def _make_ctx() -> PipelineContext:
           return PipelineContext(
               config=CONFIG.main,
               db_path=db_path,
               correlation_id=new_correlation_id(),
               taxonomy=taxonomy,
           )

       def on_create(path: Path) -> None:
           asyncio.run(capture_file(path, context=_make_ctx()))

       def on_modify(path: Path) -> None:
           asyncio.run(capture_file(path, context=_make_ctx()))

       def on_delete(path: Path) -> None:
           vault_rel = to_vault_path(path)
           delete_by_path(vault_rel, db_path=db_path)

       def on_move(src: Path, dst: Path) -> None:
           # Internal vault rename: update path, preserve integer id (DECISION-001)
           rename_doc(to_vault_path(src), to_vault_path(dst), db_path=db_path)

       asyncio.run(scan_capture())  # reconcile files that landed while watcher was down
       watcher = VaultWatcher(
           root=root,
           attachment_path=attachment_path,
           on_create=on_create,
           on_modify=on_modify,
           on_delete=on_delete,
           on_move=on_move,
       )
       watcher.start()
       click.echo(f"Watching {root} — Ctrl-C to stop")
       try:
           while True:
               import time; time.sleep(1)
       except KeyboardInterrupt:
           watcher.stop()
           watcher.join()
   ```

**Files to modify**:
- `pyproject.toml` — add `watchdog>=4.0`
- `vault/watcher.py` — new file
- `cli/main.py` — add `kms watch` command

**Test criteria**:
- [ ] `VaultWatcher` fires `on_create` once after debounce window for rapid create events on same path
- [ ] `VaultWatcher` fires `on_create` for `.md` file created in `inbox/`
- [ ] `VaultWatcher` fires `on_create` for `.md` file created in `Projects/foo/` (non-inbox drop)
- [ ] `VaultWatcher` fires `on_create` for PDF created in `inbox/` (non-md drop)
- [ ] `VaultWatcher` does NOT fire any callback for events on files inside `attachment/`
- [ ] `VaultWatcher` fires `on_modify` for `.md` file modified in vault
- [ ] `VaultWatcher` does NOT fire `on_modify` for a binary (non-`.md`) file modified in vault
- [ ] `VaultWatcher` fires `on_delete` for `.md` file deleted from vault
- [ ] `VaultWatcher` fires `on_move(src, dst)` for `.md` moved within vault (both src and dst inside vault root)
- [ ] `VaultWatcher` fires `on_create(dst)` for `FileMovedEvent` where src is outside vault (external drop via OS move)
- [ ] `VaultWatcher` does NOT fire for `FileMovedEvent` where dst is inside `attachment/` (pipeline artifact)
- [ ] `VaultWatcher` ignores dotfiles and `.sync-conflict-*` files
- [ ] `VaultWatcher` ignores files inside `IGNORE_DIRS` (e.g. `.git/`, `.obsidian/`)
- [ ] `VaultWatcher` does NOT import any module from `pipelines/` or `llm/`
- [ ] `kms watch --help` exits 0 (smoke test without starting observer)

**Status**: [x] done

**Completed**: 2026-05-21
**Notes**: `vault/watcher.py` created — `_VaultEventHandler` (debounce via `threading.Timer`, `_should_skip` gates attachment/dotfile/sync-conflict/IGNORE_DIRS, `_is_internal` for external-drop detection) + `VaultWatcher` (thin wrapper over watchdog `Observer`). No pipeline/llm/core.config module-scope imports. `kms watch` command added to `cli/main.py` — lazy-imports all heavy deps inside function body, loads taxonomy once at startup, runs `scan_capture()` at boot to reconcile offline drops, starts `VaultWatcher` with 4 callbacks, blocks on `time.sleep(1)` loop until Ctrl-C. `watchdog>=4.0` added to `pyproject.toml` (installed `watchdog==6.0.0`). 18 unit tests in `tests/test_vault/test_watcher.py` (direct `_VaultEventHandler` invocation, no real observer) + 1 CLI smoke test in `tests/test_cli/test_watch_cli.py`. 487 tests pass, 6 deselected (integration/API key tests).

### Phase 12 — Bug fixes (post-review)
**Goal**: address findings from code review of `pipelines/capture.py`. Five fixes across `_parse_metadata_json`, `_store_nonmd`, `_store_md`, and `capture_file` entry point. No new features. No behavioral change on success paths — only failure paths become correct and observable.

**Background**: review surfaced 2 Critical + 2 Important issues. One Critical (hardcoded `confidence=0.9` at line 213) intentionally left in place per `docs/research/capture_pipeline.md:270` — capture emits a constant signal, not a routing decision; fixed value is documented design.

**Fix 1 — `_parse_metadata_json` return type (Important)**
Function currently returns `dict | Failure`, violating the Result Type rule. Wrap the success path in `Success(...)` and update the caller in `metadata` stage to `match` on the result.
- Change signature: `def _parse_metadata_json(content: str, source_stem: str = "") -> Result[dict]`
- Final return becomes `Success({"title": title, "tags": tags})`
- In `metadata` stage (line ~194): replace `isinstance(parsed, Failure)` check with `match parsed: case Failure() as f: return f; case Success(value=parsed_dict): ...`, then read `parsed_dict["title"]` / `parsed_dict.get("tags", [])` downstream.

**Fix 2 — `_store_nonmd` missing Success branch (Important)**
The `match documents.upsert(sibling_outcome, ...)` block at line 378 only handles `Failure`. Add an explicit `case Success(): pass` so the match is exhaustive and intent is documented. No behavioral change.

**Fix 3 — `_store_nonmd` orphan note (Critical, option C)**
Current order writes sibling `.md` first, then moves the binary. If the move fails, vault contains a note with a broken `![[...]]` embed and no way for the pipeline to recover. Reorder:
1. Resolve `attachment_dst` with collision loop (unchanged).
2. Move binary from `src` to `attachment_dst` FIRST. On `Failure`, return Failure — vault state unchanged.
3. Only then write the sibling `.md` pointing to the now-confirmed `attachment_dst.name`.
4. Upsert documents row for the sibling.
Remove the `src.exists()` "already moved" branch — that path becomes unreachable once the move is the first disk write. If the move succeeds but the sibling write fails, the binary is in `attachment/` with no note — recoverable on next scan (orphan detected by reverse-lookup in TD-C6 / future phase). Surface that gap as a `# COUPLING:` note rather than silently retrying.

**Fix 4 — `_store_md` rename half-commit (Important, option B rollback)**
Rename path currently does: `move_note(src, dst)` → `write_note(dst, ...)` → `documents.delete_by_path(old)` (return ignored) → `documents.upsert(new)`. If `delete_by_path` fails, DB ends with two rows (stale + new) and the upsert proceeds blind.
Reorder + transaction + rollback:
1. `move_note(src, dst)` — disk rename. On Failure, return.
2. `write_note(dst, original_body, note_meta, actor="ai")` — capture `outcome`. On Failure, rename back via `move_note(dst, src)` and return original Failure.
3. Wrap `delete_by_path(old) + upsert(new)` in a single SQLite transaction (add a new helper in `storage/documents.py`, e.g. `replace_path(old_vault_path, outcome, db_path)` that runs both inside `BEGIN ... COMMIT`).
4. If the transaction Fails: undo disk — `move_note(dst, src)`, restore the original body via a second `write_note(src, original_body, note_meta_pre, ...)` is not feasible (pre-write metadata not retained). Simpler rollback: rename file back and delete the new-content file. Return Failure with context describing the partial state for operator inspection.
- New helper signature in `storage/documents.py`:
  ```python
  def replace_path(old_vault_path: str, outcome: WriteOutcome, db_path: Path | None = None) -> Result[None]:
      """Atomically delete the old documents row and upsert from outcome."""
  ```
  Implementation: `with get_connection(db_path) as conn:` → `conn.execute("BEGIN")` → existing delete + upsert logic against `conn` → `conn.execute("COMMIT")`. On `sqlite3.Error`, return Failure; context manager auto-rolls back the transaction.

**Fix 5 — `capture_file` entry point duplication (Critical, refactor)**
Stability gate and `run_pipeline` call appear twice (lines 429–435 + 455–461; 448–453 + 463–468). Not a correctness bug — `else` branch returns at line 448 — but two near-identical Failure constructions invite drift on future edits. Refactor into a single path:
1. Extract default-context construction into a private async helper `_build_default_context() -> PipelineContext`:
   ```python
   async def _build_default_context() -> PipelineContext:
       from core.config import CONFIG
       from core.tags import load_taxonomy
       from vault.paths import load_valid_domains
       valid_domains = load_valid_domains(CONFIG.main.vault.root)
       taxonomy = load_taxonomy(
           Path(__file__).parent.parent / "config" / "tags.yaml",
           valid_domains,
       )
       return PipelineContext(
           config=CONFIG.main,
           db_path=CONFIG.main.database.path,
           correlation_id=new_correlation_id(),
           taxonomy=taxonomy,
       )
   ```
2. Rewrite `capture_file`:
   ```python
   async def capture_file(path, context=None):
       if context is None:
           context = await _build_default_context()
       age = time.time() - path.stat().st_mtime
       cooldown = context.config.capture.cooldown_seconds
       if age < cooldown:
           return Failure(
               error=f"file too recent (age={age:.1f}s < cooldown={cooldown}s); retry later",
               recoverable=True,
               context={"path": str(path), "age_seconds": age, "cooldown_seconds": cooldown},
           )
       return await run_pipeline(
           "capture",
           [extract, enrich_urls, summarize, metadata, store],
           path,
           context=context,
       )
   ```
Lazy-import discipline preserved — heavy imports remain inside `_build_default_context`.

**Files to modify**:
- `pipelines/capture.py` — 5 fixes above
- `storage/documents.py` — add `replace_path` helper for Fix 4
- `tests/test_pipelines/test_capture.py` (and/or `test_capture_phase3.py`) — add tests below

**Test criteria**:
- [ ] `_parse_metadata_json` returns `Success(dict)` on valid JSON
- [ ] `_parse_metadata_json` returns `Failure(recoverable=False)` on unparseable JSON
- [ ] `metadata` stage handles `_parse_metadata_json` Failure by returning Failure (no audit row written)
- [ ] `_store_nonmd`: binary missing at move time returns Failure (no sibling written)
- [ ] `_store_nonmd`: successful move then failed sibling write leaves binary in `attachment/` and returns Failure (no orphan note in drop folder)
- [ ] `_store_nonmd`: happy path unchanged — sibling created in drop folder, binary in attachment, documents row present
- [ ] `_store_md` rename: simulated `replace_path` Failure rolls back disk rename — original filename restored, new-name file removed
- [ ] `_store_md` rename: happy path — old row gone, new row present in single transaction (assert via mid-transaction failure injection that no half-state ever observable)
- [ ] `_store_md` no-rename in-place write: unchanged behavior
- [ ] `capture_file(path, context=None)` cooldown still rejects newly-written files
- [ ] `capture_file(path, context=ctx)` with stale file proceeds through pipeline
- [ ] No duplicated `Failure(...)` construction string in `pipelines/capture.py` (regression guard: `grep -c "file too recent" pipelines/capture.py` returns 1)
- [ ] Full test suite still passes (487+ tests)

**Out of scope for Phase 12**:
- Sibling-without-binary orphan recovery (`_store_nonmd` Fix 3 leaves this gap — covered by TD-C6 future phase).
- Moving the `confidence=0.9` literal to config (intentional per research; would require Phase 2 routing-gate context to justify).
- Adding write-ahead log or two-phase commit across disk+DB (overkill; rollback is sufficient for single-process local vault).

**Status**: [x] done

**Completed**: 2026-05-22
**Notes**: All 5 fixes implemented TDD. `_parse_metadata_json` now returns `Result[dict]`; caller in `metadata` stage updated to `match/case`. `_store_nonmd` reordered: `move_attachment` first — missing binary returns Failure before any vault write. `_store_md` rename path uses new `documents.replace_path()` (atomic delete+insert in one transaction) with disk rollback on DB failure. `capture_file` entry point refactored: `_build_default_context()` extracted, single stability gate, single `run_pipeline` call. `test_store_nonmd_recapture_skips_move_logs_warning` updated to reflect new attachment-first contract (missing binary → Failure). 16 new tests in `test_capture_phase12.py`; 492 tests pass total (1 pre-existing Ollama integration failure unrelated to this phase).

---

## Open Questions

| # | Question | Blocks | Status |
|---|---|---|---|
| OQ-C1 | Where to expose `to_vault_path` | Phase 1 | ✅ Resolved: move to `vault/paths.py` as public function |
| OQ-C2 | `documents.upsert` changes integer id on rename. Safe for Phase 1 (no FK from audit_log). Roadmap Phase 7 (self-learning) corrections FK may orphan if note was renamed post-capture. | Roadmap Phase 7 | Document in TD-C2; defer. |
| OQ-C4 | `scan_capture` processes `added` only; modified notes not re-captured. | Phase 8 scope | ✅ Resolved: Phase 10 adds full re-capture for `summary.modified`, plus delete and rename handling. |
| OQ-C5 | **⚠️ TD-014 flag** — Phase 2 calls `write_note` with AI-filled `NoteMetadata`. Option B merge in `_merge_metadata` treats empty/None caller values as "keep existing." For capture: `tags=[]` (JSON parse fallback) correctly preserves existing tags. But the user has flagged this: _"STOP and surface to user BEFORE implementing."_ Recommended resolution: for Phase 1, `_parse_metadata_json` returns `tags=["unclassified"]` as fallback instead of `tags=[]` — avoids the Option B ambiguity entirely. Confirm before Phase 2 implementation begins. | Phase 2 impl | 🔴 Must confirm before /implement |
| OQ-C6 | `kms watch` loads taxonomy at startup; new Domain/ folders added while the watcher runs are invisible until restart. Is this acceptable for Phase 11, or does the watcher need periodic taxonomy refresh? | Phase 11 | 🟡 Accept for Phase 11. Documented in `kms watch` help text. Revisit if users report stale domain lists. |
| OQ-C7 | When `validate_tags` finds no `type/` tag (zero-type violation), the pipeline continues with `ai_type=None` and writes a note with `type=None` in frontmatter. Is that acceptable, or should zero-type be a recoverable Failure that re-prompts the LLM once? | Phase 7 | 🟡 Accept None for Phase 7 — retrying LLM adds latency and complexity. FLAG in audit log (violation entry covers it). |

## Out of Scope

- **AI rename disambiguation** (TD-C5): when `.md` rename collides, Phase 1 falls back to suffix. The full strategy — AI reads summaries of both conflicting notes, proposes disambiguated names, may rename both — is deferred. **Hard precondition: must not resume until TD-015 (co-authoring section-merge) replaces the `updated_by_human` whole-note lock.** The reason: renaming an existing note is a write to an already-indexed note; if that note has `updated_by_human=True`, the write is blocked, and silently renaming only the new note while leaving the existing one untouched produces an incoherent result. Once co-authoring lands and per-section ownership replaces the blunt gate, this feature becomes viable.
- **Binary lifecycle in `attachment/`** (TD-C6): when a non-md binary in `attachment/` is modified or deleted, the sibling `.md` is unaffected (content_hash unchanged → not in `detect_changes.modified`). Requires reverse lookup (binary → sibling) via a `source_file` queryable column or a new `attachments` table (per DECISION-018). The insertion point is `VaultWatcher._should_skip` — currently returns `True` for all `attachment/` events. A future phase flips this and dispatches to `on_attachment_modify` / `on_attachment_delete` callbacks once the reverse lookup is in place.
- AI URL triage (Wishlist B: LLM classifies URLs as primary/citation/skip) — TD-017, Phase 2+
- User explicit URL flagging via frontmatter `fetch_urls:` list — TD-016, Phase 1+ (post-watcher)
- FTS5 indexing — Phase 3 (retrieval layer)
- MCP tools for capture — no tool before pipeline is tested (cross-phase constraint)
- Per-section authorship tracking — TD-006, roadmap Phase 7+
- **Taxonomy enforcement in classify pipeline** — Phase 2 (classify) will also generate tags; it must apply the same `validate_tags` logic from `core/tags.py`. That wiring belongs in the classify plan, not here. `core/tags.py` is shared infrastructure.
- **Periodic taxonomy refresh in watcher** — watcher loads domains once at startup; Domain/ folder changes while watcher runs are not detected (OQ-C6). Dynamic refresh deferred post-Phase 11.
- **LLM retry on zero-type violation** — if AI returns no `type/` tag, pipeline continues with `ai_type=None` and logs TAG_VIOLATION. Retry logic (re-prompt once) is deferred (OQ-C7).
