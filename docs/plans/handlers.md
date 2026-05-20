# Plan: Handlers
_Last updated: 2026-05-20_
_Status: [x] done_

## Architecture

```
Component view
──────────────

                         handlers/ (this plan)
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                           │
│  ┌────────────────────────────┐       ┌─────────────────────────────┐   │
│  │ HandlerRegistry             │       │ BaseHandler (ABC)            │   │
│  │ Class-level handler lookup  │       │ Extraction contract          │   │
│  │ · register(cls) → cls       │       │ · can_handle(Path) → bool   │   │
│  │ · resolve(Path)             │       │ · extract(Path)             │   │
│  │   → Result[BaseHandler]     │       │   → Result[RawContent]      │   │
│  └────────────┬───────────────┘       └──────────────┬──────────────┘   │
│               │ first match                           │ ABC              │
│               │                    ┌──────────────────┼──────────────┐  │
│               │                    ▼                  ▼              ▼  │
│               │         ┌──────────────────┐  ┌────────────┐  ┌──────┐ │
│               │         │ MarkdownHandler   │  │ PdfHandler │  │Docx  │ │
│               │         │ .md → body text   │  │ .pdf→text  │  │Hdlr  │ │
│               │         └────────┬─────────┘  └─────┬──────┘  └──┬───┘ │
│               │                  │                   │             │     │
│               ▼                  │                   │             │     │
│  ┌────────────────────┐          │                   │             │     │
│  │ RawContent          │◀────────┴───────────────────┴─────────────┘    │
│  │ Frozen dataclass    │                                                  │
│  │ text, source_path   │                                                  │
│  │ is_md               │                                                  │
│  └────────────────────┘                                                  │
│                                                                           │
│  handlers/__init__.py  ← imports all three handlers → triggers @register │
└──────────────────────────────────────────────────────────────────────────┘

Existing deps (not modified):
  vault/reader.py ─────────────────────────▶ used by MarkdownHandler only
  core/result.py  ─────────────────────────▶ used by all

Missing deps (add to pyproject.toml):
  pypdf ───────────────────────────────────▶ PdfHandler
  python-docx ─────────────────────────────▶ DocxHandler

Future consumer (dashed — Phase 1 capture pipeline):
┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  pipelines/capture.py
│  import handlers → triggers boot  │
   HandlerRegistry.resolve(path)
└ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

```
Data flow through the handler subsystem
──────────────────────────────────────

Path (dropped file)
     │
     ▼
HandlerRegistry.resolve(path) ──────────── no handler ──▶ Failure(recoverable=False)
     │
     │ Success(handler)
     ▼
handler.extract(path) ──────────────────── parse/IO error ──▶ Failure(recoverable=False)
     │                                     image-only PDF  ──▶ Failure(recoverable=False)
     │ Success(RawContent)
     ▼
 ┌─────────────────────────────────────────────────┐
 │ RawContent                                       │
 │   text:        str   → LLM summarise stage      │
 │   source_path: Path  → store stage (write-back) │
 │   is_md:       bool  → branch: in-place vs sibling│
 └─────────────────────────────────────────────────┘
```

```
Bootstrap sequence (import-time registration)
─────────────────────────────────────────────

pipelines/capture.py
   import handlers            ← triggers handlers/__init__.py
         │
         ▼
   handlers/__init__.py
   ┌─────────────────────────────────────────────┐
   │ from handlers.markdown_handler import ...   │ ← @register fires → _handlers[0]
   │ from handlers.pdf_handler import ...        │ ← @register fires → _handlers[1]
   │ from handlers.docx_handler import ...       │ ← @register fires → _handlers[2]
   └─────────────────────────────────────────────┘

Resolution order = [MarkdownHandler, PdfHandler, DocxHandler]
First match wins. Each handler claims exactly one suffix → no ambiguity.
```

---

## Approach

Build bottom-up: shared types first (`base.py`), dispatch mechanism second (`registry.py`), then concrete handlers one at a time. One handler must be working end-to-end before the next is added (roadmap rule). Bootstrap (`__init__.py`) is wired last — after all three handlers pass their individual tests — so early phases remain independently testable without any import magic.

`HandlerRegistry._handlers` is a class-level mutable list. Tests that register dummy handlers use a `clean_registry` fixture in `conftest.py` that saves and restores the list around each test, preventing cross-test pollution.

---

## Phases

### Phase 1 — base.py: RawContent + BaseHandler ABC

**Goal**: Define the shared types that all handlers implement and the pipeline receives.

**Steps**:
1. Create `handlers/base.py`:
   - `RawContent`: `@dataclass(frozen=True)` with `text: str`, `source_path: Path`, `is_md: bool`
   - `BaseHandler`: ABC with two abstract methods `can_handle(self, path: Path) -> bool` and `extract(self, path: Path) -> Result[RawContent]`
   - Import `Result` from `core.result`, `Path` from `pathlib`, `ABC`/`abstractmethod` from `abc`
   - Export both via `__all__`
2. Create `tests/test_handlers/__init__.py` (empty — makes pytest discover the package)
3. Create `tests/test_handlers/test_base.py`:
   - Write a minimal concrete subclass stub (two implementations that just `return True` / `return Success(...)`)
   - Assert `isinstance(stub, BaseHandler)` passes
   - Assert `extract` return annotation is `Result[RawContent]` (inspect via `__annotations__` or type hints)

**Files to create**:
- `handlers/base.py` — new
- `tests/test_handlers/__init__.py` — new (empty)
- `tests/test_handlers/test_base.py` — new

**Test criteria**:
- [x] `uv run pytest tests/test_handlers/test_base.py -v` passes
- [x] A stub subclass satisfies `isinstance(stub, BaseHandler)`
- [x] Attempting to instantiate `BaseHandler` directly raises `TypeError`

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: Delivered as planned. 7 tests, all green. `--explicit-package-bases` resolves mypy package ambiguity without premature `__init__.py` (consistent with `core/` which also has no `__init__.py`). No deviations. No surprises.

---

### Phase 2 — registry.py: HandlerRegistry

**Goal**: Class-level dispatch — register handlers by type, resolve a path to the first matching handler.

**Steps**:
1. Create `handlers/registry.py`:
   - `HandlerRegistry` class with `_handlers: list[BaseHandler] = []` class variable
   - `@classmethod register(cls, handler_class: type[BaseHandler]) -> type[BaseHandler]`: instantiates and appends, returns class (decorator pattern)
   - `@classmethod resolve(cls, path: Path) -> Result[BaseHandler]`: iterates `_handlers`, returns `Success(first match)` or `Failure(recoverable=False, error=f"no handler for extension '{path.suffix}'")`
   - Document resolution order in module docstring: "first-registered wins; markdown is registered first"
2. Create `tests/test_handlers/conftest.py`:
   - `clean_registry` fixture (function scope): saves `HandlerRegistry._handlers[:]`, yields, restores. All tests that register dummy handlers must use this fixture.
3. Create `tests/test_handlers/test_registry.py`:
   - Test: register a dummy handler (`can_handle` → `.xyz`), resolve `.xyz` path → `Success`
   - Test: resolve unknown extension → `Failure(recoverable=False)`
   - Test: two handlers registered, path matches second → returns second (first-match by registration order)
   - All tests use `clean_registry` fixture

**Files to create**:
- `handlers/registry.py` — new
- `tests/test_handlers/conftest.py` — new
- `tests/test_handlers/test_registry.py` — new

**Test criteria**:
- [x] `uv run pytest tests/test_handlers/test_registry.py -v` passes
- [x] Dummy handler registered → `resolve(matching_path)` returns `Success(handler)`
- [x] Unknown extension → `resolve` returns `Failure` with `recoverable=False`
- [x] Clean-registry fixture prevents test-to-test pollution (verify: second test does not see dummy from first test)

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: Delivered as planned. 4 tests, all green. `ClassVar[list[BaseHandler]]` for `_handlers` keeps mypy happy. `clean_registry` fixture uses slice assignment (`[:]`) to mutate in-place so conftest reference stays stable. No deviations. No surprises.

---

### Phase 3 — markdown_handler.py: First end-to-end handler

**Goal**: Extract body text from a `.md` drop, using `vault.reader.read_note` to strip frontmatter.

**Steps**:
1. Create `handlers/markdown_handler.py`:
   - `MarkdownHandler(BaseHandler)` decorated with `@HandlerRegistry.register`
   - `can_handle`: `return path.suffix.lower() == ".md"`
   - `extract`: call `vault.reader.read_note(path)`, on `Success` return `Success(RawContent(text=note.content, source_path=path, is_md=True))`, on `Failure` propagate as-is
   - Import `HandlerRegistry` from `handlers.registry`, `read_note` from `vault.reader`
2. Create `tests/test_handlers/test_markdown_handler.py`:
   - Fixture: write a `.md` file with YAML frontmatter + known body text to `tmp_path` using `pathlib.Path.write_text`
   - Test: `MarkdownHandler().extract(path)` → `Success(RawContent)` with `text == known_body`, `is_md is True`, `source_path == path`
   - Test: body is stripped of frontmatter (text does not contain `---` or frontmatter keys)
   - Test: `MarkdownHandler().can_handle(Path("note.md"))` → `True`
   - Test: `MarkdownHandler().can_handle(Path("note.PDF"))` → `False` (case check)
   - Test: non-existent path → `extract` returns `Failure`

**Files to create**:
- `handlers/markdown_handler.py` — new
- `tests/test_handlers/test_markdown_handler.py` — new

**Note on test isolation**: `MarkdownHandler().extract(path)` creates a fresh instance per test — no registry interaction needed for these unit tests. Registry is not involved.

**Test criteria**:
- [x] `uv run pytest tests/test_handlers/test_markdown_handler.py -v` passes
- [x] `text` in `RawContent` is body-only, no frontmatter block
- [x] `is_md` is `True`
- [x] `.PDF` suffix returns `False` from `can_handle`
- [x] Missing file → `Failure`

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: Delivered as planned. 9 tests, all green. `@HandlerRegistry.register` fires at import time — `clean_registry` fixture in conftest.py correctly handles this. Pre-existing mypy stubs issue in `vault/frontmatter.py` (python-frontmatter / yaml no stubs) is not introduced by this phase. No deviations. No surprises.

---

### Phase 4 — pdf_handler.py: PDF extraction + pypdf dependency

**Goal**: Extract text from a `.pdf` drop; return `Failure` for image-only or unreadable PDFs.

**Steps**:
1. Add `pypdf` to `pyproject.toml` dependencies list. Run `uv sync` to install.
2. Create `handlers/pdf_handler.py`:
   - `PdfHandler(BaseHandler)` decorated with `@HandlerRegistry.register`
   - `can_handle`: `return path.suffix.lower() == ".pdf"`
   - `extract`: inside `try/except Exception`:
     - `pypdf.PdfReader(str(path))`, iterate pages with `page.extract_text() or ""`
     - Join with `"\n"`, strip
     - After joining: `if not text: return Failure(error="PDF contains no extractable text (image-only or empty)", recoverable=False, context={"path": str(path)})`
     - On any exception: `return Failure(error=f"PDF read failed: {exc}", recoverable=False, context={"path": str(path)})`
     - On success: `return Success(RawContent(text=text, source_path=path, is_md=False))`
3. Create `tests/test_handlers/test_pdf_handler.py`:
   - Fixture for a text-bearing PDF: create programmatically with `pypdf.PdfWriter` + `fpdf2` or include a committed tiny PDF in `tests/fixtures/sample_text.pdf` (see decision below)
   - Fixture for an image-only (no text layer) PDF: use `pypdf.PdfWriter().add_blank_page(width=200, height=200)` — blank page has no text layer
   - Test: text PDF → `Success(RawContent)` with non-empty `text`, `is_md=False`
   - Test: blank-page PDF → `Failure(recoverable=False)`
   - Test: non-existent path → `Failure`
   - Test: `can_handle(Path("doc.pdf"))` → `True`; `can_handle(Path("doc.md"))` → `False`

**Note on PDF fixture**: Creating a text-bearing PDF programmatically with `pypdf` alone is not possible (pypdf reads, not creates text layers). Options:
  - A) Commit a tiny fixture PDF (`tests/fixtures/sample_text.pdf`) — simplest, ~10 KB binary. **(Recommended)**
  - B) Add `fpdf2` as a dev-only dependency to generate PDFs in tests — extra dep for test-only.

The committed fixture is simplest. Create it once with any PDF tool and commit it.

**Files to modify / create**:
- `pyproject.toml` — add `pypdf` to dependencies
- `handlers/pdf_handler.py` — new
- `tests/fixtures/sample_text.pdf` — new (committed binary fixture)
- `tests/test_handlers/test_pdf_handler.py` — new

**Test criteria**:
- [x] `uv run pytest tests/test_handlers/test_pdf_handler.py -v` passes
- [x] Text PDF → `Success` with non-empty `text`, `is_md=False`
- [x] Blank-page PDF (no text layer) → `Failure(recoverable=False)`
- [x] Non-existent path → `Failure`

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: Used `fpdf2` as dev dep (user chose this over hand-crafted binary). `tests/fixtures/sample_text.pdf` generated with fpdf2 and committed. `pypdf>=4.0` added to main deps. Blank-page fixture generated at test time via `pypdf.PdfWriter().add_blank_page()`. 9 tests, all green. No deviations. No surprises.

---

### Phase 5 — docx_handler.py: DOCX extraction + python-docx dependency

**Goal**: Extract paragraph text from a `.docx` drop.

**Steps**:
1. Add `python-docx` to `pyproject.toml` dependencies list. Run `uv sync` to install.
2. Create `handlers/docx_handler.py`:
   - `DocxHandler(BaseHandler)` decorated with `@HandlerRegistry.register`
   - `can_handle`: `return path.suffix.lower() == ".docx"`
   - `extract`: inside `try/except Exception`:
     - `from docx import Document; doc = Document(str(path))`
     - `text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())`
     - `return Success(RawContent(text=text, source_path=path, is_md=False))`
     - On exception: `return Failure(error=f"DOCX read failed: {exc}", recoverable=False, context={"path": str(path)})`
   - Empty DOCX (all paragraphs empty) returns `Success(RawContent(text=""))` — empty text is not an error; pipeline LLM stage handles it
3. Create `tests/test_handlers/test_docx_handler.py`:
   - Fixture: create a `.docx` in `tmp_path` using `python-docx` itself: `Document()`, `add_paragraph("Hello World")`, `save(str(path))`
   - Fixture for empty DOCX: `Document()`, save with no paragraphs added
   - Test: `.docx` with text → `Success(RawContent)` with `text` containing "Hello World", `is_md=False`
   - Test: empty `.docx` → `Success(RawContent(text=""))` (not Failure)
   - Test: non-existent path → `Failure`
   - Test: `can_handle(Path("doc.docx"))` → `True`; `can_handle(Path("doc.pdf"))` → `False`

**Files to modify / create**:
- `pyproject.toml` — add `python-docx` to dependencies
- `handlers/docx_handler.py` — new
- `tests/test_handlers/test_docx_handler.py` — new

**Test criteria**:
- [x] `uv run pytest tests/test_handlers/test_docx_handler.py -v` passes
- [x] DOCX with paragraphs → `Success` with correct text, `is_md=False`
- [x] Empty DOCX → `Success` with `text=""` (not `Failure`)
- [x] Non-existent path → `Failure`

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: Delivered as planned. `python-docx>=1.1` added to main deps. 10 tests, all green. TD-H1 (table cell text not extracted) documented in module docstring. No deviations. No surprises.

---

### Phase 6 — handlers/__init__.py: bootstrap + integration

**Goal**: Wire all three handlers into a single importable package; verify end-to-end registration and dispatch.

**Steps**:
1. Create `handlers/__init__.py`:
   ```python
   # Import order = registration order. MarkdownHandler is registered first.
   from handlers.markdown_handler import MarkdownHandler
   from handlers.pdf_handler import PdfHandler
   from handlers.docx_handler import DocxHandler

   __all__ = ["MarkdownHandler", "PdfHandler", "DocxHandler"]
   ```
2. Add integration tests to `tests/test_handlers/test_registry.py` (new section, not replacing Phase 2 unit tests):
   - Test: `import handlers` → `len(HandlerRegistry._handlers) == 3`
   - Test: resolve `Path("note.md")` → returned handler is `MarkdownHandler` instance
   - Test: resolve `Path("report.pdf")` → returned handler is `PdfHandler` instance
   - Test: resolve `Path("document.docx")` → returned handler is `DocxHandler` instance
   - Test: resolve `Path("file.unknown")` → `Failure(recoverable=False)`
   - Test: resolve `Path("NOTE.MD")` (uppercase) → `Success` (case-insensitive suffix)
   - **Important**: these integration tests do NOT use the `clean_registry` fixture — they rely on the real registered handlers. Run in a separate test class or file to avoid fixture interference.

**Files to create / modify**:
- `handlers/__init__.py` — new
- `tests/test_handlers/test_registry.py` — add integration test class

**Test criteria**:
- [ ] `uv run pytest tests/test_handlers/ -v` — all tests pass (full suite)
- [ ] `import handlers; HandlerRegistry._handlers` has exactly 3 entries
- [ ] Each extension resolves to the correct handler type
- [ ] Uppercase suffix resolves correctly
- [ ] Unknown extension returns `Failure`
- [ ] `uv run pytest tests/ -m "not smoke" -v` — no regressions in existing test suite

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: Delivered as planned. 7 new integration tests + 4 existing unit tests = 11 total in test_registry.py, all green. Full suite: 331 passed. `test_handler_types_exported_from_package` correctly fails without `__init__.py` (handlers seen as namespace package, `__file__=None`). **Surprise S-H1**: editable install (`__editable___ai_kms_0_1_0_finder.py`) cached `handlers` in `NAMESPACES` stale from before `__init__.py` was created. Fix: `uv pip install -e .` regenerates the finder. Future: whenever adding `__init__.py` to a previously namespace package directory, run `uv pip install -e .`. Integration tests do NOT use `clean_registry` — they verify real registered handlers. `len==3` assertion replaced with type membership checks (extensible when new handlers are added).

---

### Phase 7 — url_fetcher.py: URL detection + content fetching

**Goal**: Deliver the utility functions that let the capture pipeline enrich any extracted text (from `.md`, PDF, or DOCX) with the actual content of linked URLs — including YouTube transcripts and web pages.

**Why here and not in the pipeline**: Handlers own all content-extraction concerns. URL fetching is extraction from a remote source, not orchestration logic. The pipeline's `enrich_urls` stage will be a thin caller of these functions. Keeping the fetching logic in `handlers/` makes it independently testable and reusable across future pipelines.

**Design note — async boundary**: `fetch_url_content` is synchronous (blocking I/O). The async pipeline stage wraps it with `asyncio.to_thread(fetch_url_content, url)`. This matches the Ollama provider pattern (TD-010). Do not make `fetch_url_content` async.

**Steps**:
1. Add new dependencies to `pyproject.toml`:
   - `beautifulsoup4` — HTML parsing for web scraping
   - `youtube-transcript-api` — YouTube transcript fetching
   - `requests` already present — no change needed
   Run `uv sync` after editing.
2. Create `handlers/url_fetcher.py`:
   - `detect_urls(text: str) -> list[str]`: regex `https?://[^\s\)\]\>"]+`, returns all unique HTTP/HTTPS URLs found in text
   - `_is_youtube(url: str) -> bool`: checks `urlparse(url).netloc in ("www.youtube.com", "youtube.com", "youtu.be")`
   - `_extract_video_id(url: str) -> str | None`: parses `?v=` query param for `youtube.com` URLs, last path segment for `youtu.be` URLs
   - `_fetch_youtube(url: str) -> Result[str]`: calls `YouTubeTranscriptApi.get_transcript(video_id)`, joins snippet texts. Returns `Failure(recoverable=False)` if no transcript available (not a network error — content simply doesn't exist). Returns `Failure(recoverable=True)` on network errors.
   - `_fetch_web(url: str) -> Result[str]`: `requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})`, parse with `BeautifulSoup(resp.text, "html.parser")`, remove `<script>`/`<style>` tags, return `soup.get_text(separator="\n", strip=True)`. Returns `Failure(recoverable=True)` on network/HTTP errors.
   - `fetch_url_content(url: str) -> Result[str]`: dispatches to `_fetch_youtube` or `_fetch_web`
   - Export only `detect_urls` and `fetch_url_content` in `__all__`
3. Update `handlers/__init__.py` (Phase 6) to also export `detect_urls` and `fetch_url_content`:
   ```python
   from handlers.url_fetcher import detect_urls, fetch_url_content
   ```
4. Create `tests/test_handlers/test_url_fetcher.py`:
   - `detect_urls` unit tests (no network):
     - Text with one URL → returns `[url]`
     - Text with no URLs → returns `[]`
     - Text with mixed content + multiple URLs → all found
     - Markdown link syntax `[label](https://...)` → URL extracted (regex matches inside parens)
     - Duplicate URLs → returned once (deduplicate)
   - `_is_youtube` unit tests (no network):
     - `https://www.youtube.com/watch?v=abc` → `True`
     - `https://youtu.be/abc` → `True`
     - `https://example.com` → `False`
   - `_extract_video_id` unit tests (no network):
     - `?v=dQw4w9WgXcQ` → `"dQw4w9WgXcQ"`
     - `youtu.be/dQw4w9WgXcQ` → `"dQw4w9WgXcQ"`
     - Malformed URL → `None`
   - Integration tests (marked `@pytest.mark.integration` — require real network + API keys):
     - `fetch_url_content("https://youtu.be/...")` → `Success` with non-empty transcript text
     - `fetch_url_content("https://example.com")` → `Success` with non-empty web text
     - Private/no-transcript YouTube → `Failure(recoverable=False)`

**Note on the capture pipeline's `enrich_urls` stage** (implemented in the capture pipeline plan, not here): The stage calls `detect_urls(raw_content.text)`, then for each URL calls `await asyncio.to_thread(fetch_url_content, url)`. Successful fetches are appended to `raw_content.text` as a `[Referenced content]` section. Failed fetches are logged at WARNING but do not abort the pipeline — enrichment is best-effort.

**Files to modify / create**:
- `pyproject.toml` — add `beautifulsoup4`, `youtube-transcript-api`
- `handlers/url_fetcher.py` — new
- `handlers/__init__.py` — add url_fetcher exports (Phase 6 file, minor update)
- `tests/test_handlers/test_url_fetcher.py` — new

**Test criteria**:
- [ ] `uv run pytest tests/test_handlers/test_url_fetcher.py -v -m "not integration"` passes (unit tests, no network)
- [ ] `detect_urls` finds all HTTP/HTTPS URLs in markdown text, deduplicates
- [ ] `detect_urls` returns `[]` for text with no URLs
- [ ] `_is_youtube` correctly classifies `youtube.com` and `youtu.be` URLs
- [ ] `_extract_video_id` extracts correct ID from standard YouTube URL shapes
- [ ] `fetch_url_content` on unreachable URL → `Failure(recoverable=True)`

**Status**: [x] done

**Completed**: 2026-05-20
**Notes**: Delivered as planned. 16 unit tests, all green. Full suite: 345 passed. **Surprise S-H2**: `youtube-transcript-api` v1.x broke the planned API — `get_transcript()` classmethod replaced by instance-based `api.fetch(video_id)` returning `FetchedTranscript` iterable of `FetchedTranscriptSnippet` objects (`.text` attribute, not `["text"]` dict). Errors now in `youtube_transcript_api._errors`. Fixed inline. Added `types-requests` to dev deps for mypy. Integration tests marked `@pytest.mark.integration` (skipped by default). This is the last phase — plan complete.

---

## Open Questions

None — all design decisions resolved during research and planning.

Resolved during planning:
- **OQ-H4 (bootstrap)**: `handlers/__init__.py` imports all handlers in registration order. Pipeline does `import handlers` to trigger it.
- **OQ-H1 (empty DOCX)**: Returns `Success(RawContent(text=""))`. Empty text is valid input for the LLM summarise stage.
- **PDF fixture**: Committed binary in `tests/fixtures/sample_text.pdf` (Phase 4 decision point).
- **URL fetching scope**: Pulled forward from "post-Phase 1" into this plan (user decision 2026-05-20). Delivered as `handlers/url_fetcher.py` utility; capture pipeline adds `enrich_urls` stage separately.
- **URL fetching location**: Utility in `handlers/`, not in `MarkdownHandler.extract()` — network I/O stays out of filesystem extraction methods. Pipeline stage orchestrates the two steps.

---

## Out of Scope

- Email and chat handlers — added post-Phase 1 pipeline validation (roadmap rule)
- OCR for image-only PDFs — explicitly out of scope in spec ("No OCR in Phase 1")
- Table cell text in DOCX — documented as TD-H1, Phase 3+ enhancement
- `pipelines/capture.py` — this plan delivers only the extraction layer; capture pipeline is a separate plan
- Watcher (`vault/watcher.py`) — Phase 1 item 13, separate plan
- `kms capture` CLI wiring — follows capture pipeline, not handlers

---

## Technical Debt Inherited

These items are known from research and must be tracked:

| ID | What | Action in this plan |
|---|---|---|
| TD-H1 | DOCX table cell text not extracted | Documented in `docx_handler.py` module docstring |
| TD-H2 | No OCR fallback for image-only PDFs | `Failure` message explicitly says "no OCR in this version" |
| TD-H3 | Registry `_handlers` test pollution | Resolved: `clean_registry` fixture in Phase 2 `conftest.py` |
| TD-H4 | `pypdf` + `python-docx` missing from `pyproject.toml` | Fixed in Phase 4 and Phase 5 respectively |
| TD-H5 | `beautifulsoup4` + `youtube-transcript-api` missing from `pyproject.toml` | Fixed in Phase 7 |
| TD-H6 | `fetch_url_content` ignores robots.txt | Acceptable for local demo; revisit if KMS runs as a server | Phase 4+ |
| TD-H7 | YouTube videos without transcripts (auto-generated disabled) return Failure | No fallback for transcript-less videos. Could fall back to video metadata/description via YouTube Data API. Deferred. | Phase 3+ |
