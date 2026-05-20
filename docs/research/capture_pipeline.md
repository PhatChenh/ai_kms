# Research: Capture Pipeline
_Last updated: 2026-05-20_

## Overview

`pipelines/capture.py` orchestrates the end-to-end processing of any file dropped into the vault (inbox or any folder). It runs 5 pure async stages: `extract → enrich_urls → summarize → metadata → store`. For `.md` drops the note is updated in-place; for non-md drops (PDF, DOCX) a sibling `.md` is created and the binary moved to `attachment/`. Every pipeline run writes one audit_log row and one documents row.

**User decision 2026-05-20:** URL fetching pulled forward from post-Phase 1 into Phase 1. `handlers/url_fetcher.py` is the utility. The capture pipeline adds `enrich_urls` as stage 2. Spec (`docs/phase_1_detailed_specs.md` items 7-13) was not updated; this document supersedes it where they diverge.

---

## Key Components (all already built — do not rebuild)

| File | Role in capture |
|---|---|
| `handlers/base.py` | `RawContent` frozen dataclass (text, source_path, is_md); `BaseHandler` ABC |
| `handlers/registry.py` | `HandlerRegistry.resolve(path) → Result[BaseHandler]` |
| `handlers/__init__.py` | Registration order: Markdown → PDF → DOCX |
| `handlers/markdown_handler.py` | Returns `RawContent(text=note.content, is_md=True)` |
| `handlers/pdf_handler.py` | Returns `RawContent(text=joined_text, is_md=False)` |
| `handlers/docx_handler.py` | Returns `RawContent(text=paragraph_text, is_md=False)` |
| `handlers/url_fetcher.py` | `detect_urls(text) → list[str]`; `fetch_url_content(url) → Result[str]` |
| `core/pipeline.py` | `run_pipeline(name, stages, initial_input, context)` chains stages |
| `core/audit.py` | `write(decision, pipeline, stage, outcome, db_path) → Result[int]` |
| `core/confidence.py` | `AIDecision(action, confidence, reasoning, source_ids)` |
| `vault/reader.py` | `read_note(path) → Result[Note]`; Note has `.content` and `.metadata` |
| `vault/writer.py` | `write_note`, `move_note`, `move_attachment` — all return `Result[WriteOutcome]` |
| `vault/frontmatter.py` | `NoteMetadata` Pydantic model; `parse`, `dumps` |
| `vault/indexer.py` | `scan_vault() → Result[list[VaultEntry]]`; `detect_changes(current)` |
| `storage/documents.py` | `upsert(outcome)`, `rename(old, new)`, `delete_by_path(path)` |
| `llm/provider.py` | `get_provider(task, config) → LLMProvider`; provider has `async complete(system, user)` |
| `llm/prompt_loader.py` | `PROMPTS: dict[str, Prompt]`; `PROMPTS["name"].render(**vars) → (system, user)` |
| `cli/main.py` | `capture` command stub raises `NotImplementedError` — replace in Phase 1 |

---

## How It Works

### Pipeline entry point

```python
async def capture_file(path: Path, context: PipelineContext | None = None) -> Result[WriteOutcome]:
    return await run_pipeline(
        "capture",
        [extract, enrich_urls, summarize, metadata, store],
        path,
        context=context,
    )
```

`context=None` → `run_pipeline` lazy-imports `CONFIG` and calls `new_correlation_id()` internally (DECISION-012). `capture_file` must NOT import `CONFIG` at module scope — that would break tests on machines without the vault. Tests pass an explicit `PipelineContext(config=mock_config, correlation_id="test-id", db_path=tmp_path/"kb.db")` to bypass CONFIG validation.

**Stability gate** runs at the top of `capture_file` before `run_pipeline` — this is one exception where `CONFIG` is lazy-imported inside the function body:

```python
async def capture_file(path: Path, context: PipelineContext | None = None) -> Result[WriteOutcome]:
    import time
    from core.config import CONFIG  # lazy import — not at module scope
    
    cooldown = CONFIG.main.capture.cooldown_seconds
    age = time.time() - path.stat().st_mtime
    if age < cooldown:
        return Failure(
            error=f"file modified {age:.0f}s ago, cooldown={cooldown}s — skipping",
            recoverable=True,
            context={"path": str(path), "age_seconds": age},
        )
    
    return await run_pipeline("capture", [extract, enrich_urls, summarize, metadata, store], path, context=context)
```

### Stage 1 — `extract`

Input: `Path`  
Output: `Result[RawContent]`

```python
async def extract(path: Path, ctx: PipelineContext) -> Result[RawContent]:
    match HandlerRegistry.resolve(path):
        case Failure() as f: return f
        case Success(value=handler):
            return handler.extract(path)
```

`HandlerRegistry` must be populated before call — import `handlers` at the top of `capture.py`. The registry is a class-level list populated at import time.

### Stage 2 — `enrich_urls`

Input: `RawContent`  
Output: `Result[RawContent]`

#### Decision gate — is this note worth enriching?

Not every URL in a note is worth fetching. The critical distinction:

| Note shape | URL role | Fetch? |
|---|---|---|
| Sparse body (< 500 chars ex-URLs), 1–3 URLs | URL IS the content ("link note") | ✅ Yes |
| Substantial body (> 500 chars ex-URLs) or > 3 URLs | URLs are citations or references inside rich text | ❌ No |

**Structural heuristic** (no LLM cost, no domain-list maintenance):

```python
def _should_enrich(text: str, urls: list[str], max_urls: int) -> bool:
    body_only = re.sub(r'https?://\S+', '', text).strip()
    return len(urls) <= max_urls and len(body_only) < 500
```

Rationale: A note with lots of prose alongside many URLs is reference-heavy — the text IS the content; fetching citations adds noise and endless link chains. A sparse note with 1-3 URLs is a "link drop" — the URLs ARE the content worth summarizing.

`max_urls` comes from `CONFIG.main.capture.max_urls_per_note` (default 3). If a note has 4+ URLs AND rich body text, skip all fetching.

#### Depth cap — already 1 hop by design

`enrich_urls` reads URLs from `raw.text` ONLY. Fetched URL content is passed to the LLM but is NEVER parsed for further URLs to fetch. Depth = 1, hard-coded by architecture. Redirect-following inside `_fetch_web` (up to `max_redirects`) is the same resource at a different URL — not content discovery.

#### Fetch flow (when gate passes)

```python
async def enrich_urls(raw: RawContent, ctx: PipelineContext) -> Result[RawContent]:
    from core.config import CONFIG  # lazy import
    
    urls = detect_urls(raw.text)
    if not urls:
        return Success(raw)
    
    max_urls = CONFIG.main.capture.max_urls_per_note
    if not _should_enrich(raw.text, urls, max_urls):
        logger.info("enrich_urls.skip", reason="reference-heavy", url_count=len(urls))
        return Success(raw)
    
    fetched: list[str] = []
    for url in urls[:max_urls]:
        match await asyncio.to_thread(fetch_url_content, url):
            case Failure() as f:
                logger.warning("enrich_urls.fetch_failed", url=url, error=f.error)
                # non-fatal — skip this URL, continue with others
            case Success(value=content):
                fetched.append(f"## {url}\n{content[:5000]}")  # truncate per URL
    
    if not fetched:
        return Success(raw)  # all fetches failed — original text is sufficient
    
    augmented = raw.text + "\n\n---\n[Referenced URL Content]\n\n" + "\n\n".join(fetched)
    return Success(RawContent(text=augmented, source_path=raw.source_path, is_md=raw.is_md))
```

Key constraints:
- `RawContent` is `frozen=True` — return a **new** instance, never mutate.
- `source_path` preserved unchanged — `store` stage reads this path for original body write-back.
- Each fetched URL truncated at 5000 chars (guard against massive pages bloating LLM context).
- Stage NEVER returns `Failure` — worst case is `Success(raw)` with no enrichment.
- Augmented text flows to `summarize` and `metadata` LLM stages only — NEVER written to disk.

**Why enrich_urls runs on all content types (not just `.md`):**  
The structural heuristic handles the PDF/DOCX case correctly. A PDF with dense text + many reference URLs fails the gate → no fetch. A short DOCX that is essentially a pointer to a web resource passes the gate → enriched. No special-casing by `is_md` needed.

#### Gate as an extension point

The heuristic is intentionally isolated in `_should_enrich`. Future gate strategies slot in without touching fetch logic:

```python
def _build_gate(raw: RawContent, urls: list[str], config) -> tuple[list[str], str]:
    """Return (urls_to_fetch, skip_reason). Empty list = skip all."""
    # Phase 1: structural heuristic only
    body_only = re.sub(r'https?://\S+', '', raw.text).strip()
    max_urls = config.capture.max_urls_per_note
    if len(urls) > max_urls or len(body_only) >= 500:
        return [], "reference-heavy"
    return urls[:max_urls], ""
```

Future strategies (wishlist — do NOT implement in Phase 1):

**Wishlist A — User explicit flagging**: User marks URLs as crucial in frontmatter or inline.
```yaml
# frontmatter
fetch_urls:
  - https://example.com/doc   # always fetched, bypasses gate
```
Or inline Obsidian convention: `[read this](https://example.com) #fetch`. The gate checks for flagged URLs first; flagged URLs bypass the structural check and are always included (up to a higher per-note cap).

**Wishlist B — AI triage**: Before fetching, ask LLM to classify each URL as `primary|citation|skip`. Only `primary` URLs get fetched. Adds one cheap LLM call before fetch I/O.
```python
# Rough shape — NOT Phase 1
async def _ai_triage_urls(urls: list[str], context: str, provider) -> list[str]:
    system, user = PROMPTS["url_triage"].render(urls=urls, context=context)
    resp = await provider.complete(system, user)
    # parse JSON list of {"url": ..., "role": "primary|citation|skip"}
    return [u["url"] for u in parsed if u["role"] == "primary"]
```
This would require a new `prompts/url_triage.yaml`. Slot it in by replacing `_build_gate` with `_ai_triage_urls` in `enrich_urls` — no other stage changes needed.

**Design constraint**: Both wishlist items require NO changes to stages 3–5. All enrichment decisions live inside `enrich_urls`. The fetch logic and the gate logic stay decoupled so either can be swapped independently.

### Stage 3 — `summarize`

Input: `RawContent` (possibly enriched text)  
Output: `Result[SummarizeResult]`

```python
@dataclass(frozen=True)
class SummarizeResult:
    raw: RawContent    # the enriched RawContent (source_path preserved)
    summary: str

async def summarize(raw: RawContent, ctx: PipelineContext) -> Result[SummarizeResult]:
    provider = get_provider("capture", ctx.config)
    system, user = PROMPTS["summarize"].render(text=raw.text)
    match await provider.complete(system, user):
        case Failure() as f: return f
        case Success(value=resp):
            return Success(SummarizeResult(raw=raw, summary=resp.content.strip()))
```

`SummarizeResult` is a private dataclass in `pipelines/capture.py`. Not exported.

### Stage 4 — `metadata`

Input: `SummarizeResult`  
Output: `Result[MetadataResult]`

```python
@dataclass(frozen=True)
class MetadataResult:
    raw: RawContent
    summary: str
    ai_title: str        # AI-proposed title (may differ from current filename stem)
    ai_type: str | None  # e.g. "note", "report", "meeting", "document"
    ai_tags: list[str]
    decision: AIDecision  # carried for store stage audit (already written here)

async def metadata(sr: SummarizeResult, ctx: PipelineContext) -> Result[MetadataResult]:
    provider = get_provider("capture", ctx.config)
    system, user = PROMPTS["extract_metadata"].render(
        text=sr.raw.text, summary=sr.summary
    )
    match await provider.complete(system, user):
        case Failure() as f: return f
        case Success(value=resp):
            parsed = _parse_metadata_json(resp.content)  # see JSON parsing section
            if isinstance(parsed, Failure): return parsed
            
            source_id = _to_vault_path(sr.raw.source_path)  # NFC vault-relative
            decision = AIDecision(
                action="capture:metadata",
                confidence=0.9,
                reasoning=f"Summarized and extracted metadata. Title: {parsed['title']}",
                source_ids=[source_id],
            )
            match audit.write(
                decision,
                pipeline="capture",
                stage="metadata",
                outcome="CAPTURED",
                db_path=ctx.db_path,
            ):
                case Failure() as f: return f
                case Success():
                    return Success(MetadataResult(
                        raw=sr.raw,
                        summary=sr.summary,
                        ai_title=parsed["title"],
                        ai_type=parsed.get("type"),
                        ai_tags=parsed.get("tags", []),
                        decision=decision,
                    ))
```

**Confidence convention — fixed 0.9**:  
Capture does not classify or route. A fixed `0.9` communicates "capture succeeded with high confidence" to Phase 8 briefing. AI-reported summary quality varies and requires extra parsing. Fixed value is simpler and stable. The value is high enough to appear in Phase 8 capture reports without triggering SUGGEST or CLUELESS routing gates (which are Phase 2's concern).

**`extract_metadata` JSON schema** (prompt must instruct the LLM to return this):
```json
{
  "title": "Concise title (max 120 chars, no path separators or special chars)",
  "type": "note|report|meeting|document|reference|other",
  "tags": ["tag1", "tag2"]
}
```

**`_parse_metadata_json(content: str) → dict | Failure`**:
1. Strip markdown code fences: `content = re.sub(r"^```json?\n?|^```\n?", "", content, flags=re.MULTILINE).strip()`
2. `json.loads(content)` → `Failure(recoverable=False)` on `json.JSONDecodeError`
3. Validate: `title` must be a non-empty string; `tags` must be a list. If either is wrong, fall back to defaults (title = source stem, tags = []), log warning, do NOT fail the pipeline.
4. Strip `title` of path-unsafe chars: `re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title)[:120].strip()`
5. Return `{"title": clean_title, "type": validated_type, "tags": clean_tags}`

**Audit outcome string**: `"CAPTURED"` — not a `RouteDecision`. The audit_log stores `outcome` as a TEXT column with no constraint. Capture doesn't route; `"CAPTURED"` signals phase 8 briefing that this was a successful capture run.

### Stage 5 — `store`

Input: `MetadataResult`  
Output: `Result[WriteOutcome]`

This stage branches on `raw.is_md`:

#### `.md` branch

```
1. Re-read original body: read_note(raw.source_path) → original_body
   (body in RawContent.text may be augmented by enrich_urls — re-read for byte-identical write)
2. Build NoteMetadata with AI fields:
   NoteMetadata(
       summary=mr.summary,
       type=mr.ai_type,
       tags=mr.ai_tags,
       confidence=mr.decision.confidence,
   )
   (created/updated/updated_by_human handled by _merge_metadata inside write_note)
3. Check rename needed: sanitized_stem = _sanitize_title(mr.ai_title)
   if sanitized_stem != raw.source_path.stem:
       dst = raw.source_path.parent / (sanitized_stem + ".md")
       if dst.exists():
           log warning "rename collision, keeping original name"
           goto write_in_place
       old_vault_path = _to_vault_path(raw.source_path)
       match move_note(raw.source_path, dst, actor="ai"):
           case Failure() as f: return f
           case Success(value=outcome):
               # Clean up old documents row, insert new one
               documents.delete_by_path(old_vault_path, db_path=ctx.db_path)
               return documents.upsert(outcome, db_path=ctx.db_path) and return Success(outcome)
4. write_in_place:
   match write_note(raw.source_path, original_body, note_meta, actor="ai"):
       case Failure() as f: return f
       case Success(value=outcome):
           return documents.upsert(outcome, db_path=ctx.db_path) and return Success(outcome)
```

**Body preservation invariant**: `write_note(path, original_body, ...)` where `original_body` comes from `read_note(raw.source_path).content`, never from `RawContent.text`. This is the byte-identical guarantee.

**Rename collision strategy**: If `dst.exists()`, keep the original name, log a WARNING with both paths, proceed with in-place write. Never overwrite an existing note.

**documents sequence for rename**: `delete_by_path(old)` + `upsert(new)`. This creates a new integer id for the renamed row. In Phase 1, `audit_log` has no FK to `documents.id`, so this is safe. Phase 7 (corrections) does have FK — but corrections are logged post-capture; the note's new id is the one that matters. Revisit if this causes issues in Phase 7.

#### Non-md branch (PDF, DOCX)

```
1. Build sibling .md path: sibling = raw.source_path.parent / (raw.source_path.stem + ".md")
2. Build sibling body:
   wikilink = f"![[{raw.source_path.name}]]"  # Obsidian wikilink
   body = wikilink
3. Build sibling NoteMetadata:
   NoteMetadata(
       summary=mr.summary,
       type=mr.ai_type,
       tags=mr.ai_tags,
       confidence=mr.decision.confidence,
       source_file=str(raw.source_path.name),  # original filename for reference
   )
4. Check sibling rename: sanitized_stem = _sanitize_title(mr.ai_title)
   if sanitized_stem != raw.source_path.stem:
       sibling = raw.source_path.parent / (sanitized_stem + ".md")
       attachment_dst = CONFIG.main.vault.attachment_path / (sanitized_stem + raw.source_path.suffix)
   else:
       attachment_dst = CONFIG.main.vault.attachment_path / raw.source_path.name

5. Collision avoidance for attachment_dst:
   while attachment_dst.exists():
       attachment_dst = attachment_dst.parent / (stem + f"-{n}" + suffix)  # n=1,2,...
   
6. write_note(sibling, body, sibling_meta, actor="ai") → Result[WriteOutcome]
7. documents.upsert(sibling_outcome, db_path=ctx.db_path)
8. move_attachment(raw.source_path, attachment_dst) → Result[Path]
   (move_attachment returns Failure if dst exists — the while loop above prevents this)
9. Return Success(sibling_outcome)
```

**Naming convention**: sibling `.md` and attachment share the same stem. The Obsidian `[[wikilink]]` uses the original filename (before rename) to ensure the link resolves to the file in `attachment/`. After rename, the wikilink should use the new name: `![[{attachment_dst.name}]]`. So the body must be built AFTER resolving `attachment_dst`.

**`source_file` field**: stores `attachment_dst.name` (the filename as it will exist in `attachment/`), not the original source_path.

---

## Edge Cases & Silent Failure Modes

### 1. `enrich_urls` augments text but `store` re-reads original body
- **Risk**: If the note is modified on disk between `extract` and `store`, the re-read in `store` will get the newer version, not what was summarized.
- **Mitigation**: Stability gate (cooldown_seconds, item 11) prevents processing files still being edited. If the gap is too small, the worse case is an incorrect summary — recoverable by re-running capture.
- **No fix needed in Phase 1**.

### 2. `updated_by_human = True` blocks the write
- `write_note` returns `Failure(recoverable=False, error="note locked by human edit")`.
- `store` stage propagates this Failure upstream.
- `run_pipeline` logs it and returns Failure to the caller.
- `capture_file` returns Failure. CLI prints the error. **No retry** — user must clear `updated_by_human` manually.

### 3. JSON parse failure in `_parse_metadata_json`
- If LLM returns malformed JSON: fall back to defaults (`title=source_stem`, `type=None`, `tags=[]`), log WARNING. Pipeline continues.
- If LLM returns valid JSON but wrong types (e.g., `tags` is a string): coerce — `tags = [str(v) for v in parsed["tags"]] if isinstance(parsed["tags"], list) else []`.
- Never fail the pipeline on LLM format errors. Capture with defaults is better than no capture.

### 4. `write_note` on a NEW note (no frontmatter yet)
- `write_note` checks `path.exists()`. If False, `existing_note = None`.
- `_merge_metadata(incoming=meta, existing=None, actor="ai")` uses `date.today()` for `created`, sets `updated_by_human=False`.
- ✓ No issue.

### 5. Non-md sibling `.md` already exists (re-capture)
- `write_note(sibling, ...)` checks `updated_by_human`. If it was set by a prior human edit: Failure.
- Otherwise: `write_note` merges metadata (Option B rules) — existing fields preserved if not in incoming.
- If the PDF was already moved to `attachment/` on first capture: `move_attachment` is called on `raw.source_path` which no longer exists → `Failure("attachment source not found")`.
- **Fix**: In `store` stage, check if `raw.source_path.exists()` before calling `move_attachment`. If binary is gone (already moved), skip `move_attachment` and proceed. Log a WARNING.

### 6. Attachment collision despite while-loop
- `move_attachment` refuses to overwrite (`Failure("attachment already exists at dst")`).
- The while-loop in `store` increments suffix until it finds a free slot.
- Infinite loop risk: not possible in practice since the suffix counter eventually reaches a unique value. But cap the loop at 100 iterations and return Failure if exceeded.

### 7. `audit.write` fails (missing correlation_id)
- `storage/audit_log.py:append` returns `Failure` if `correlation_id` is None in contextvars.
- `capture_file` calls `new_correlation_id()` (or `run_pipeline` does it) before stages run.
- Risk: If `capture_file` is called from a thread or task that doesn't inherit the contextvars (Phase 4 MCP daemon), the correlation_id may be missing.
- **Phase 1 CLI**: not a risk — synchronous `asyncio.run()` context.
- **Phase 4**: see Q-004 in STATE.md.

### 8. `move_note` atomicity with Syncthing / iCloud
- `_atomic_write` uses `os.replace()` (atomic on POSIX). Works correctly with Syncthing and iCloud if both are not writing simultaneously.
- Stability gate (cooldown_seconds) is the primary guard against this.

---

## Dependencies & Coupling

- `pipelines/capture.py` imports: `handlers`, `core.pipeline`, `core.audit`, `core.confidence`, `core.logging_setup`, `vault.reader`, `vault.writer`, `vault.frontmatter`, `vault.indexer`, `storage.documents`, `llm.provider`, `llm.prompt_loader`
- `cli/main.py` imports `pipelines.capture.capture_file` and `scan_capture` (for --scan)
- `vault/watcher.py` imports NOTHING from pipelines — it only emits paths via callback

**Layer violations to avoid**:
- `capture.py` must NOT import `CONFIG` at module scope (DECISION-012, cross-phase constraint)
- `vault/watcher.py` must NOT import `pipelines/` or `llm/` (spec constraint)

---

## Config Changes Required

### `core/config.py` — add `CaptureConfig` model

```python
class CaptureConfig(BaseModel):
    cooldown_seconds: int = Field(60, ge=0)
    max_urls_per_note: int = Field(3, ge=0)
```

Add to `MainConfig`:
```python
capture: CaptureConfig = Field(default_factory=CaptureConfig)
```

### `config/config.yaml` — add capture section

```yaml
capture:
  cooldown_seconds: 60
  max_urls_per_note: 3
```

`cooldown_seconds`: file must have `mtime` older than this before capture runs.
`max_urls_per_note`: cap URLs fetched per note in `enrich_urls`. Beyond 3, LLM context grows faster than quality improves.

---

## Prompts to Build

### `prompts/summarize.yaml`

```yaml
name: summarize
system: |
  You are a knowledge management assistant. Your job is to produce a concise,
  factual summary of the provided note content. Focus on the main topic, key
  points, and any decisions or actions mentioned. Write 2-4 sentences. Do not
  add opinions or inferences beyond what is stated.
user: |
  Summarize the following note content:

  {{ text }}
variables: [text]
```

### `prompts/extract_metadata.yaml`

```yaml
name: extract_metadata
system: |
  You are a knowledge management assistant. Extract structured metadata from
  a note. Return a single JSON object with exactly these fields:
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

**Note on JSON output**: The system prompt says "no markdown fences" but LLMs often return them anyway. `_parse_metadata_json` strips fences defensively.

---

## CLI Changes Required

### `cli/main.py` — replace `capture` stub

```python
@cli.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--scan", is_flag=True, default=False, help="Capture all un-indexed notes in vault")
def capture(file: str | None, scan: bool) -> None:
    """Run the capture pipeline on a single file, or --scan the vault."""
    import asyncio
    from pipelines.capture import capture_file, scan_capture

    if scan:
        result = asyncio.run(scan_capture())
    else:
        if not file:
            raise click.UsageError("Provide a file path or use --scan")
        result = asyncio.run(capture_file(Path(file)))
    
    match result:
        case Success(value=v): click.echo(f"OK: {v}")
        case Failure(error=e): click.echo(f"FAILED: {e}", err=True)
```

`scan_capture` is an async function in `pipelines/capture.py` that runs `scan_vault()` → `detect_changes()` → process `added` entries only.

---

## Build Order Recommendation

The spec's build order (items 7-13) is sound. Adjustments for the `enrich_urls` decision:

1. **Items 7+8** (pipeline `.md` branch + CLI): Add `enrich_urls` as stage 2 from the start. Build `summarize.yaml` and `extract_metadata.yaml` first (formerly item 6, but prompts are needed before pipeline runs).

2. **Items 9+10** (non-md branch + rename): Add to `store` stage. No new stages needed — branching is internal to `store`.

3. **Item 11** (stability gate): Add `CaptureConfig` to `core/config.py` and `config.yaml`. Add pre-flight check in `capture_file` before calling `run_pipeline`.

4. **Item 12** (`--scan`): `scan_capture()` function + `--scan` CLI flag. Calls `scan_vault()` → `detect_changes()` → stability gate per file → `capture_file()` per added entry.

5. **Item 13** (watcher): `vault/watcher.py` with `watchdog`. `kms watch` CLI command. Startup `--scan` reconcile. Debounce (2-5s) collapses event bursts; `cooldown_seconds` is the final mtime guard before the pipeline runs.

---

## Watcher Design (`vault/watcher.py`)

```python
# vault/watcher.py — emits paths, NO pipeline/llm imports
from pathlib import Path
from typing import Callable
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent
```

**Debounce**: Use a `threading.Timer` per path. Each create/move event resets the timer for that path. When the timer fires (after debounce_seconds, e.g., 3s), call the callback.

**Ignore list**: Same as `vault/indexer.py` — `IGNORE_DIRS`, `IGNORE_FILES`, dotfiles, `.sync-conflict-`.

**Callback**: `callback(path: Path) -> None` — the watcher is callback-agnostic. `kms watch` CLI wires the callback to an async wrapper that calls `capture_file(path)`.

**kms watch startup**: Call `scan_capture()` (the `--scan` logic) at startup to process any files that landed while the watcher was down.

---

## Reference Project Patterns

The reference project (`knowledge-base-server/src/ingest.js`) uses a flat `ingestFile()` function: one function handles extraction, storage, and file copy. No staged pipeline, no Result type, no audit trail.

**Why we diverge**: The reference design works for a simple file copy + DB insert. It has no recovery semantics, no audit trail, and no LLM enrichment. Our design is more complex but justified: (1) each stage can fail independently with retry semantics, (2) the audit log feeds Phase 8 briefing, (3) the LLM stages are async and benefit from the staged Result pattern. No reference patterns adopted for capture_pipeline.

---

## Open Questions

| # | Question | Blocks | Status |
|---|---|---|---|
| OQ-C1 | `_to_vault_path` is a private function in `vault/writer.py`. The `metadata` stage needs it to compute `source_ids` for `AIDecision`. Options: (a) expose it from `vault.writer`, (b) compute `str(path.relative_to(CONFIG.main.vault.root))` inline in the pipeline stage, (c) use `str(path)` as the source_id (absolute path). | Stage 4 source_ids | Recommend (a): expose `_to_vault_path` or add `to_vault_path(path: Path) -> str` as a public function in `vault/writer.py` or `vault/paths.py`. |
| OQ-C2 | `documents.upsert` after `write_note` on an already-indexed note does `INSERT OR REPLACE` — this changes the integer id. For Phase 1 this is safe (no FK from audit_log). For Phase 7 (corrections), the id change would orphan corrections logged against the pre-capture id. | Phase 7 | Document and defer. Phase 7 must handle id changes or we add update-metadata-only path before then. |
| OQ-C3 | `max_urls_per_note` lives in `capture:` config section (separate from `handlers.max_redirects` which is per-HTTP-fetch). Decided: separate fields, separate purposes. Resolved. | — | ✅ Resolved |
| OQ-C4 | `scan_capture` processes `added` entries only. `modified` entries (re-edited notes that were previously indexed) are NOT re-captured. This means if a note is edited after capture, its summary is stale. Is that acceptable for Phase 1? | Phase 1 scope | Accept for Phase 1. Note in CLI help text. Phase 2+ can add re-capture on modified. |

---

## Technical Debt Spotted

| ID | What | Why deferred |
|---|---|---|
| TD-C1 | `_to_vault_path` is private in `vault/writer.py` but needed by `capture.py` metadata stage for source_ids | Expose as public or move to `vault/paths.py`. Do in Phase 1 plan. |
| TD-C2 | `documents.upsert` changes integer id on replace. OK for Phase 1, risk for Phase 7 corrections FK. | Flag for Phase 7 planning. |
| TD-C3 | ~~`enrich_urls` appends URL text without truncation~~ — resolved in design: each fetched URL truncated at 5000 chars inline. | Resolved in research. |
| TD-C4 | `capture_file` stability gate reads `path.stat().st_mtime`. If vault is on iCloud or Syncthing, mtime may be unreliable (synced files get reset mtime). | Accept for Phase 1. Flag for watcher design (item 13). |
