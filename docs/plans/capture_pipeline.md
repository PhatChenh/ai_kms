# Plan: Capture Pipeline
_Last updated: 2026-05-20_
_Status: [~] in progress — Phase 4 done; Phases 5-7 (tag taxonomy) pending_

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
**Goal**: Define the taxonomy vocabulary in config, build the `TagTaxonomy` dataclass and `validate_tags` function, and write tests against pure logic — no vault or pipeline dependency yet.

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
2. Create `core/tags.py`:
   ```python
   from __future__ import annotations
   from dataclasses import dataclass
   from pathlib import Path
   import yaml

   @dataclass(frozen=True)
   class TagTaxonomy:
       allowed_types: frozenset[str]   # from config/tags.yaml
       valid_domains: frozenset[str]   # from vault Domain/ folder scan

   def validate_tags(
       tags: list[str],
       taxonomy: TagTaxonomy,
   ) -> tuple[list[str], list[str]]:
       """Return (valid_tags, violations). Violations are human-readable strings."""
       valid: list[str] = []
       violations: list[str] = []
       type_tags_seen = 0

       for tag in tags:
           if tag.startswith("type/"):
               value = tag[len("type/"):]
               if value in taxonomy.allowed_types:
                   valid.append(tag)
                   type_tags_seen += 1
               else:
                   violations.append(f"unknown type tag: {tag!r}")
           elif tag.startswith("domain/"):
               value = tag[len("domain/"):]
               if value in taxonomy.valid_domains:
                   valid.append(tag)
               else:
                   violations.append(f"unknown domain tag: {tag!r} — not in Domain/ folders")
           elif "/" in tag:
               violations.append(f"free tag has namespace prefix: {tag!r} — stripped")
           else:
               valid.append(tag)

       if type_tags_seen == 0:
           violations.append("no type/ tag found — AI must assign exactly one")
       elif type_tags_seen > 1:
           violations.append(f"multiple type/ tags found ({type_tags_seen}) — only first kept")
           # De-duplicate: keep first type/ tag, drop the rest
           seen_type = False
           deduped: list[str] = []
           for tag in valid:
               if tag.startswith("type/"):
                   if not seen_type:
                       deduped.append(tag)
                       seen_type = True
               else:
                   deduped.append(tag)
           valid = deduped

       return valid, violations


   def load_taxonomy(tags_yaml_path: Path, valid_domains: frozenset[str]) -> TagTaxonomy:
       """Load static vocabulary from tags.yaml; accept pre-scanned domain set."""
       raw = yaml.safe_load(tags_yaml_path.read_text())
       return TagTaxonomy(
           allowed_types=frozenset(raw.get("allowed_types", [])),
           valid_domains=valid_domains,
       )
   ```
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

**Status**: [ ] pending

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

**Status**: [ ] pending

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

**Status**: [ ] pending

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

**Status**: [ ] pending

---

### Phase 9 — Watcher
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
       from core.tags import load_taxonomy
       from core.pipeline import PipelineContext
       from core.logging_setup import new_correlation_id
       from vault.paths import load_valid_domains
       from vault.watcher import VaultWatcher
       from pipelines.capture import capture_file, scan_capture

       root = CONFIG.main.vault.root
       db_path = CONFIG.main.db.path

       # Load taxonomy once at watcher startup
       valid_domains = load_valid_domains(root)
       taxonomy = load_taxonomy(
           Path(__file__).parent.parent / "config" / "tags.yaml",
           valid_domains,
       )

       def on_drop(path: Path) -> None:
           ctx = PipelineContext(
               config=CONFIG.main,
               db_path=db_path,
               correlation_id=new_correlation_id(),
               taxonomy=taxonomy,    # pre-loaded taxonomy
           )
           asyncio.run(capture_file(path, context=ctx))

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
   **Note on domain refresh**: The taxonomy is loaded once at `kms watch` startup. New Domain/ folders added while the watcher runs are NOT detected until the watcher is restarted. This is acceptable for Phase 9 — document in `kms watch --help` text.

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
| OQ-C2 | `documents.upsert` changes integer id on rename. Safe for Phase 1 (no FK from audit_log). Roadmap Phase 7 (self-learning) corrections FK may orphan if note was renamed post-capture. | Roadmap Phase 7 | Document in TD-C2; defer. |
| OQ-C4 | `scan_capture` processes `added` only; modified notes not re-captured. | Phase 1 scope | Accept for Phase 1. Note in CLI `--scan` help text. |
| OQ-C5 | **⚠️ TD-014 flag** — Phase 2 calls `write_note` with AI-filled `NoteMetadata`. Option B merge in `_merge_metadata` treats empty/None caller values as "keep existing." For capture: `tags=[]` (JSON parse fallback) correctly preserves existing tags. But the user has flagged this: _"STOP and surface to user BEFORE implementing."_ Recommended resolution: for Phase 1, `_parse_metadata_json` returns `tags=["unclassified"]` as fallback instead of `tags=[]` — avoids the Option B ambiguity entirely. Confirm before Phase 2 implementation begins. | Phase 2 impl | 🔴 Must confirm before /implement |
| OQ-C6 | `kms watch` loads taxonomy at startup; new Domain/ folders added while the watcher runs are invisible until restart. Is this acceptable for Phase 9, or does the watcher need periodic taxonomy refresh (e.g. re-scan Domain/ every N minutes)? | Phase 9 | 🟡 Accept for Phase 9. Document in watch help text. Revisit if users report stale domain lists. |
| OQ-C7 | When `validate_tags` finds no `type/` tag (zero-type violation), the pipeline continues with `ai_type=None` and writes a note with `type=None` in frontmatter. Is that acceptable, or should zero-type be a recoverable Failure that re-prompts the LLM once? | Phase 7 | 🟡 Accept None for Phase 7 — retrying LLM adds latency and complexity. FLAG in audit log (violation entry covers it). |

## Out of Scope

- **AI rename disambiguation** (TD-C5): when `.md` rename collides, Phase 1 falls back to suffix. The full strategy — AI reads summaries of both conflicting notes, proposes disambiguated names, may rename both — is deferred. **Hard precondition: must not resume until TD-015 (co-authoring section-merge) replaces the `updated_by_human` whole-note lock.** The reason: renaming an existing note is a write to an already-indexed note; if that note has `updated_by_human=True`, the write is blocked, and silently renaming only the new note while leaving the existing one untouched produces an incoherent result. Once co-authoring lands and per-section ownership replaces the blunt gate, this feature becomes viable.
- Re-capture of modified (previously-indexed) notes — Phase 2+ feature
- AI URL triage (Wishlist B: LLM classifies URLs as primary/citation/skip) — TD-017, Phase 2+
- User explicit URL flagging via frontmatter `fetch_urls:` list — TD-016, Phase 1+ (post-watcher)
- FTS5 indexing — Phase 3 (retrieval layer)
- MCP tools for capture — no tool before pipeline is tested (cross-phase constraint)
- Per-section authorship tracking — TD-006, roadmap Phase 7+
- **Taxonomy enforcement in classify pipeline** — Phase 2 (classify) will also generate tags; it must apply the same `validate_tags` logic from `core/tags.py`. That wiring belongs in the classify plan, not here. `core/tags.py` is shared infrastructure.
- **Periodic taxonomy refresh in watcher** — watcher loads domains once at startup; Domain/ folder changes while watcher runs are not detected (OQ-C6). Dynamic refresh deferred post-Phase 9.
- **LLM retry on zero-type violation** — if AI returns no `type/` tag, pipeline continues with `ai_type=None` and logs TAG_VIOLATION. Retry logic (re-prompt once) is deferred (OQ-C7).
