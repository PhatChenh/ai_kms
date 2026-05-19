---
created: 2026-05-19
status: spec — ready for code planning
---

# Phase 1 — Capture: Detailed Specs

## Purpose

Drop a file anywhere in the vault → AI writes a summary + metadata into
frontmatter. **Capture only. No classification, no folder routing** — that is
Phase 2. A `.md` dropped in `inbox/` gets metadata stamped *in place*; it does
not move.

## Disagreement with the roadmap

The roadmap predates the code. Corrections applied here:

1. **Roadmap "Phase 1" bundled too much.** Roadmap M1 = Capture + Classify +
   Search. This spec is *capture only*. Moving notes into `Domain/`/`Projects/`
   is Phase 2.
2. **STATE.md is stale.** It lists `vault/` as pending. `vault/` is fully built
   and tested, as is `core/pipeline.py`. STATE.md should be refreshed.
3. **Roadmap `scheduler/` is not the capture trigger.** `scheduler/` +
   `jobs.yaml` schedule *feature* jobs (daily briefing, weekly synthesis). The
   "schedulers come last" rule applies to those, not to capture triggering. The
   capture trigger — a filesystem watcher — is built in this phase.

## Already built (reuse, do not rebuild)

- `core/pipeline.py` — `PipelineContext`, `Stage`, `run_pipeline`
- `core/confidence.py` — `AIDecision`, `route`
- `core/audit.py` — `audit.write(decision, pipeline=, stage=, outcome=)`
- `vault/` — `reader.read_note`, `writer.write_note` / `move_note` /
  `move_attachment`, `frontmatter` (`NoteMetadata`, `parse`, `dumps`),
  `indexer.scan_vault` / `detect_changes`, `paths.*`
- `storage/documents.py` — `upsert`, `get_by_path`, `all_paths`, `rename`,
  `delete_by_path`
- `llm/` — `get_provider(task, config)`, `provider.complete(system, user)`,
  `prompt_loader.PROMPTS["name"].render(**vars)`

## Capture-from-everywhere rules

Capture runs on a file in **any** folder, not just `inbox/`.

- **`.md` drop** → stamp summary + metadata into the note's own frontmatter,
  in place. Body preserved **byte-identical** — `write_note` replaces the whole
  body, so the pipeline MUST pass the original body unchanged. Summary goes in
  the `summary` frontmatter field, never the body.
- **non-md drop (PDF, DOCX)** → create a sibling `.md` summary note **in the
  same folder the file was dropped in** (inbox or elsewhere), body carrying an
  Obsidian `[[wikilink]]` + a `source_file` frontmatter field. The binary
  **always** moves to the central `attachment/` folder.
- **Rename if appropriate** — if the AI proposes a title differing from the
  current filename stem: `.md` → `move_note`; non-md → rename sibling `.md` +
  attachment to a matching convention so the pair stays linkable.

## Out of scope (Phase 1)

Classification + routing (Phase 2) · YouTube / web / email / chat handlers ·
embeddings + search (Phase 3) · feature schedulers `scheduler/` + `jobs.yaml`
(Phase 8/9).

---

## Build order

Build in this order. Each item depends on the ones above. Do not skip ahead.

### 1. `handlers/base.py` — Handler ABC + `RawContent`

**Goal.** Define the contract every input handler implements, so adding a new
source type later is one new file (Handler Registry pattern).

**Build.**
- `RawContent` — frozen dataclass: `text: str`, `source_path: Path`,
  `is_md: bool`. Holds extracted plain text plus origin info.
- `BaseHandler` ABC with two abstract methods:
  - `can_handle(self, path: Path) -> bool`
  - `extract(self, path: Path) -> Result[RawContent]`
- No registry logic here — that is item 2.

**Done when.** A subclass stub passes `isinstance(stub, BaseHandler)` and
`extract` is annotated `-> Result[RawContent]`.

---

### 2. `handlers/registry.py` — self-registering lookup

**Goal.** Map a dropped file to the one handler that can process it, with no
central `if/elif` dispatch.

**Build.**
- `HandlerRegistry` with a `register` decorator (class-level) and a
  `resolve(path: Path) -> Result[BaseHandler]` lookup that returns the first
  handler whose `can_handle` is true.
- `resolve` returns `Failure(recoverable=False)` when no handler matches.

**Decisions.**
- Resolution order when two handlers could match. Recommend: registration
  order; markdown registered first. Document the choice.

**Done when.** Registering a dummy handler and calling `resolve` on a matching
path returns `Success(handler)`; an unknown extension returns `Failure`.

---

### 3. `handlers/markdown_handler.py`

**Goal.** Extract text from a `.md` drop. First handler end-to-end.

**Build.**
- `MarkdownHandler(BaseHandler)`, registered.
- `can_handle` → `.md` suffix.
- `extract` → read file, return `RawContent(text=body, is_md=True)`. Use
  `vault.reader.read_note` so frontmatter is parsed, not treated as body.

**Done when.** `extract` on a `.md` file returns `Success(RawContent)` with the
body text and `is_md=True`.

---

### 4. `handlers/pdf_handler.py`

**Goal.** Extract text from a PDF. Second handler — only after markdown works.

**Build.**
- `PdfHandler(BaseHandler)`, registered. `can_handle` → `.pdf`.
- `extract` → pull text per page, join. `RawContent(is_md=False)`.

**Decisions.**
- PDF library: `pypdf` vs `pdfplumber`. Recommend `pypdf` (lighter); switch to
  `pdfplumber` only if layout-heavy PDFs garble. Pick one, add to `pyproject`.
- Empty / image-only PDF (no extractable text) → `Failure(recoverable=False)`
  with a clear message. No OCR in Phase 1.

**Done when.** `extract` on a text PDF returns `Success` with joined text; an
image-only PDF returns `Failure`.

---

### 5. `handlers/docx_handler.py`

**Goal.** Extract text from a DOCX. Third handler.

**Build.**
- `DocxHandler(BaseHandler)`, registered. `can_handle` → `.docx`.
- `extract` → `python-docx`, join paragraph text. `RawContent(is_md=False)`.

**Done when.** `extract` on a `.docx` returns `Success` with the paragraph text.

---

### 6. `prompts/summarize.yaml` + `prompts/extract_metadata.yaml`

**Goal.** The two AI prompts the capture pipeline calls. Prompts as config —
never inline f-strings.

**Build.**
- `summarize.yaml` — input: note text. Output: a concise summary string.
- `extract_metadata.yaml` — input: note text + summary. Output: structured
  fields the AI fills — `type`, `tags`, `title`. JSON-shaped output so the
  pipeline can parse it.
- Both follow the existing `prompts/test.yaml` schema (`name`, `system`,
  `user`, `variables`); they load via `PROMPTS[...]` at startup.

**Decisions.**
- Exact field set `extract_metadata` returns. `NoteMetadata` has many fields;
  Phase 1 fills only `type`, `tags`, `title`, `summary`. `project`, `domain`,
  `confidence`, `status` are left for Phase 2 — do not have the AI guess them.
- How `title` is returned and validated (filesystem-safe string).

**Done when.** `PROMPTS["summarize"]` and `PROMPTS["extract_metadata"]` load
without error and `.render(...)` produces `(system, user)` strings.

---

### 7. `pipelines/capture.py` — `.md` branch

**Goal.** The capture pipeline for a single `.md` drop, in place. Pure async
stages, no bundling: `extract → summarize → metadata → store`.

**Build.**
- Four top-level `async def` stages, each `Result`-returning, each with a
  meaningful `__name__`:
  - `extract` — resolve handler via registry, call `extract`.
  - `summarize` — `get_provider("capture", ctx.config)` → `complete` with the
    rendered `summarize` prompt.
  - `metadata` — render `extract_metadata`, call provider, parse fields. Build
    an `AIDecision` (action, confidence, reasoning, source_ids) and call
    `core.audit.write(decision, pipeline="capture", stage="metadata",
    outcome=...)`.
  - `store` — `write_note(path, original_body, NoteMetadata(...), actor="ai")`
    then `documents.upsert(outcome)`. Body passed unchanged.
- A `capture_file(path)` entry point composing the stages via `run_pipeline`,
  calling `new_correlation_id()` first.

**Decisions.**
- What `confidence` value capture assigns. Capture does not classify — suggest
  a fixed high confidence or the AI's self-reported summary confidence.
  Document it.
- `updated_by_human` is never set by capture (`actor="ai"`).

**Done when.** Acceptance test: drop a `.md` with a known body → run capture →
body byte-identical, `summary` frontmatter populated, one `audit_log` row, one
`documents` row.

---

### 8. CLI — `kms capture <file>`

**Goal.** Manual single-file trigger.

**Build.**
- Replace the `NotImplementedError` stub in `cli/main.py`'s `capture` command.
- `asyncio.run(capture_file(Path(file)))`; print the `Success`/`Failure`.

**Done when.** `kms capture path/to/note.md` runs the pipeline and reports the
outcome.

---

### 9. `pipelines/capture.py` — non-md branch

**Goal.** Handle PDF/DOCX drops: sibling `.md` + attachment relocation.

**Build.**
- In `store` (or a branch off it), when `RawContent.is_md` is false:
  - Build sibling `.md` body with an Obsidian `[[wikilink]]` to the source and
    a `source_file` frontmatter field.
  - `write_note` the sibling **in the same folder the binary was dropped in**.
  - `move_attachment(src, attachment/<name>)` — binary always to `attachment/`.
  - `documents.upsert` the sibling.
- `move_attachment` refuses to overwrite — pick a non-colliding name on
  collision.

**Decisions.**
- Naming convention linking sibling `.md` ↔ attachment (exact-stem match
  recommended). Collision strategy for `attachment/` (suffix `-1`, `-2`).

**Done when.** Drop a PDF outside `inbox/` → sibling `.md` appears in that
folder with a working wikilink + `source_file`, binary is in `attachment/`,
both rows in `documents`.

---

### 10. Rename support

**Goal.** Apply an AI-proposed title as a real filename change.

**Build.**
- In `store`: if the AI `title` differs from the current stem —
  - `.md` drop → `move_note(src, renamed_dst, actor="ai")`.
  - non-md → rename sibling `.md` + attachment to the matching convention.
- Re-index: `documents.rename(old, new)` for moves, or `upsert` the new path.

**Decisions.**
- Sanitise the AI title to a filesystem-safe stem; cap length.
- Destination collision (a note of that name already exists) → keep the
  original name, log it. Do not overwrite.

**Done when.** Capture a note the AI wants renamed → file is on disk under the
new name, `documents` row tracks the new `vault_path`, no orphan row.

---

### 11. Stability gate + config

**Goal.** Never capture a file the user is still editing.

**Build.**
- Add `capture.cooldown_seconds` (default 60) to `config.yaml` + its
  Pydantic model in `core/config.py`.
- A pre-flight check: skip a file whose `mtime` is within `cooldown_seconds`
  of now. Skipped files are retried on the next scan / watcher pass.

**Done when.** A file touched within the cooldown is skipped; the same file is
processed once it goes quiet.

---

### 12. `kms capture --scan` — whole-vault sweep

**Goal.** Process every un-captured note anywhere in the vault in one command.
Also the watcher's startup reconcile.

**Build.**
- `--scan` flag on the `capture` command.
- Run `indexer.scan_vault` → `indexer.detect_changes`; process `added` entries
  only (un-indexed notes). `modified` / `moved` are index-sync concerns, not
  re-capture — leave them.
- Apply the stability gate per file. Aggregate and report counts.

**Done when.** `kms capture --scan` on a vault with N new notes captures all N,
skips already-indexed notes, prints a summary.

---

### 13. `vault/watcher.py` + `kms watch`

**Goal.** Automatic, near-real-time capture on any drop.

**Build.**
- `vault/watcher.py` — a `watchdog` observer over the vault root. On a
  create/move event: debounce (wait for edits to settle), then **emit the
  path** to a callback. The watcher imports neither `pipelines/` nor `llm/` —
  it only emits paths.
- `kms watch` CLI command — wires the watcher callback to `capture_file`;
  applies the stability gate; on startup runs a `--scan` reconcile to catch
  drops that landed while the watcher was down.
- Ignore the same paths the indexer ignores (dotfiles, `.sync-conflict-`,
  `CLAUDE.md`, `IGNORE_DIRS`).

**Decisions.**
- Debounce window vs `cooldown_seconds` — debounce collapses event bursts;
  cooldown is the final mtime guard. Document how they interact (debounce
  short, e.g. 2–5s; cooldown is the real edit-safety gate).
- Process supervision / restart is out of scope — `kms watch` is run in the
  foreground for Phase 1.

**Done when.** With `kms watch` running, dropping a PDF or `.md` into any vault
folder triggers capture after the file goes quiet, with no manual command.

---

## Open items for code planning

- `RawContent` exact shape (above is the proposal — confirm).
- `extract_metadata.yaml` output schema + parsing/validation of AI JSON.
- Capture's `confidence` value convention (item 7).
- Whether `vault/watcher.py` is the right home for the watcher (it does pure FS
  work, emits paths only — no layer inversion — but confirm during planning).
