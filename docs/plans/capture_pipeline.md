# Plan: Capture Pipeline
_Last updated: 2026-05-20_
_Status: [~] in progress — Phase 4 done_

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
  ▼ Stage 4: metadata      [PROMPTS["extract_metadata"].render(text,summary)]
  │          + audit.write(decision, pipeline="capture", stage="metadata",
  │                        outcome="CAPTURED", db_path)
Result[MetadataResult(raw, summary, ai_title, ai_type, ai_tags, decision)]
  │
  ▼ Stage 5: store
  ├─ is_md=True  ─▶ read_note(source_path)  [original body, not RawContent.text]
  │                 NoteMetadata(summary, type, tags, confidence)
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
config values. The watcher (Phase 6) is the automation layer added last per
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
**Goal**: `kms capture <file>` runs the full pipeline; an end-to-end integration test confirms audit_log and documents rows are written. No `--scan` yet (added in Phase 5).

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

### Phase 5 — scan_capture + --scan flag
**Goal**: `scan_capture` is implemented and `kms capture --scan` is wired; un-indexed `.md` files detected by `detect_changes` are processed.

**Steps**:
1. Add `scan_capture(root: Path | None = None, db_path: Path | None = None) -> Result[list[WriteOutcome]]` to `pipelines/capture.py`:
   ```python
   async def scan_capture(root=None, db_path=None):
       from core.config import CONFIG  # lazy
       root = root or CONFIG.main.vault.root
       db_path = db_path or CONFIG.main.db.path
       match scan_vault(root):
           case Failure() as f: return f
           case Success(value=entries):
               summary = detect_changes(entries, db_path=db_path)
               outcomes = []
               for entry in summary.added:
                   path = root / entry.vault_path
                   match await capture_file(path):
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

**Status**: [ ] pending

---

### Phase 6 — Watcher
**Goal**: `kms watch` monitors the **entire vault root** (not just inbox) and calls `capture_file` on any new drop in any non-ignored folder, with debounce to coalesce rapid filesystem events.

**Steps**:
1. Add `watchdog` to `pyproject.toml` dependencies:
   ```toml
   "watchdog>=4.0",
   ```
2. Create `vault/watcher.py`:
   ```python
   # vault/watcher.py — emits paths only; NO pipeline/llm imports
   from pathlib import Path
   from typing import Callable
   import threading
   from watchdog.observers import Observer
   from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent
   ```
   - `VaultWatcher` class wraps `Observer` + `FileSystemEventHandler`.
   - `__init__(root: Path, callback: Callable[[Path], None], debounce_seconds: float = 3.0)`.
   - Ignore list: same as `vault/indexer.py` — `IGNORE_DIRS`, dotfiles, `.sync-conflict-*`.
   - Debounce: `threading.Timer` per path; each event resets the timer; on fire, call `callback(path)`.
   - Handle `FileCreatedEvent` and `FileMovedEvent` (dest_path for moved).
   - `start() / stop()` methods (delegate to `Observer`).
   - `watcher.py` must NOT import `pipelines/`, `llm/`, or `core.config` at module scope.
3. Add `kms watch` command to `cli/main.py`:
   ```python
   @cli.command()
   def watch() -> None:
       """Watch vault root; capture new drops from any folder automatically."""
       import asyncio
       from pathlib import Path
       from core.config import CONFIG  # lazy — cli/main.py already called load_dotenv
       from vault.watcher import VaultWatcher
       from pipelines.capture import capture_file, scan_capture

       def on_drop(path: Path):
           asyncio.run(capture_file(path))  # each drop runs its own event loop

       root = CONFIG.main.vault.root
       asyncio.run(scan_capture())  # reconcile files that landed while watcher was down
       watcher = VaultWatcher(root, callback=on_drop)
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
- [ ] `VaultWatcher` fires callback once after debounce window for rapid create events on same path
- [ ] `VaultWatcher` fires callback for file created in `inbox/` — inbox drop still works
- [ ] `VaultWatcher` fires callback for file created in `Projects/foo/` — non-inbox drop detected
- [ ] `VaultWatcher` fires callback for `FileMovedEvent` using `dest_path`
- [ ] `VaultWatcher` ignores dotfiles and `.sync-conflict-*` files
- [ ] `VaultWatcher` ignores files inside `IGNORE_DIRS` (e.g. `.git/`, `.obsidian/`)
- [ ] `VaultWatcher` does NOT import any module from `pipelines/` or `llm/`
- [ ] `kms watch --help` exits 0 (smoke test without starting observer)

**Status**: [ ] pending

---

## Open Questions

| # | Question | Blocks | Status |
|---|---|---|---|
| OQ-C1 | Where to expose `to_vault_path` | Phase 1 | ✅ Resolved: move to `vault/paths.py` as public function |
| OQ-C2 | `documents.upsert` changes integer id on rename. Safe for Phase 1 (no FK from audit_log). Phase 7 corrections FK may orphan if note was renamed post-capture. | Phase 7 | Document in TD-C2; defer. |
| OQ-C4 | `scan_capture` processes `added` only; modified notes not re-captured. | Phase 1 scope | Accept for Phase 1. Note in CLI `--scan` help text. |
| OQ-C5 | **⚠️ TD-014 flag** — Phase 2 calls `write_note` with AI-filled `NoteMetadata`. Option B merge in `_merge_metadata` treats empty/None caller values as "keep existing." For capture: `tags=[]` (JSON parse fallback) correctly preserves existing tags. But the user has flagged this: _"STOP and surface to user BEFORE implementing."_ Recommended resolution: for Phase 1, `_parse_metadata_json` returns `tags=["unclassified"]` as fallback instead of `tags=[]` — avoids the Option B ambiguity entirely. Confirm before Phase 2 implementation begins. | Phase 2 impl | 🔴 Must confirm before /implement |

## Out of Scope

- **AI rename disambiguation** (TD-C5): when `.md` rename collides, Phase 1 falls back to suffix. The full strategy — AI reads summaries of both conflicting notes, proposes disambiguated names, may rename both — is deferred. **Hard precondition: must not resume until TD-015 (co-authoring section-merge) replaces the `updated_by_human` whole-note lock.** The reason: renaming an existing note is a write to an already-indexed note; if that note has `updated_by_human=True`, the write is blocked, and silently renaming only the new note while leaving the existing one untouched produces an incoherent result. Once co-authoring lands and per-section ownership replaces the blunt gate, this feature becomes viable.
- Re-capture of modified (previously-indexed) notes — Phase 2+ feature
- AI URL triage (Wishlist B: LLM classifies URLs as primary/citation/skip) — TD-017, Phase 2+
- User explicit URL flagging via frontmatter `fetch_urls:` list — TD-016, Phase 1+ (post-watcher)
- FTS5 indexing — Phase 3 (retrieval layer)
- MCP tools for capture — no tool before pipeline is tested (cross-phase constraint)
- Per-section authorship tracking — TD-006, Phase 7+
