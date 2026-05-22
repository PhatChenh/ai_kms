# Plan: Handlers Extended (New Types)
_Last updated: 2026-05-22_
_Status: [ ] pending_

## Architecture

```
New handlers — component view
──────────────────────────────────────────────────────────────────────────────

handlers/ (extends existing registry from handlers.md plan)

HandlerRegistry (exists)         BaseHandler ABC (exists)
  · register(cls) → cls            · can_handle(Path) → bool
  · resolve(Path)                  · extract(Path) → Result[RawContent]
       │ first match wins
       ▼
Existing (unchanged):             New (this plan):
┌────────────┐ ┌────────────┐    ┌────────────┐ ┌────────────┐ ┌────────────┐
│MarkdownHdlr│ │ PdfHandler │    │XlsxHandler │ │CsvHandler  │ │PptxHandler │
│ .md        │ │ .pdf       │    │ .xlsx      │ │ .csv       │ │ .pptx      │
│(exists)    │ │(exists)    │    │ openpyxl   │ │ stdlib csv │ │python-pptx │
└────────────┘ └────────────┘    └────────────┘ └────────────┘ └────────────┘
┌────────────┐ ┌────────────┐    ┌────────────┐ ┌────────────┐ ┌────────────┐
│ DocxHandler│ │UrlFetcher  │    │HtmlHandler │ │EmlHandler  │ │MsgHandler  │
│ .docx      │ │(util)      │    │ .html/.htm │ │ .eml       │ │ .msg       │
│(exists)    │ │(exists)    │    │beautifulsoup│ │stdlib email│ │extract-msg │
└────────────┘ └────────────┘    └────────────┘ └────────────┘ └────────────┘
                                 ┌──────────────────────────────┐
                                 │ image_handler.py (stubs)     │
                                 │ PngHandler .png → Failure    │
                                 │ JpgHandler .jpg/.jpeg→Failure│
                                 └──────────────────────────────┘

All new handlers → Result[RawContent] with is_md=False
PNG/JPG stubs → Failure(recoverable=False, "vision not implemented")
```

```
Updated handlers/__init__.py registration order
────────────────────────────────────────────────
from handlers.markdown_handler import MarkdownHandler   # position 0
from handlers.pdf_handler import PdfHandler             # position 1
from handlers.docx_handler import DocxHandler           # position 2
from handlers.xlsx_handler import XlsxHandler           # position 3 — new
from handlers.csv_handler import CsvHandler             # position 4 — new
from handlers.pptx_handler import PptxHandler           # position 5 — new
from handlers.html_handler import HtmlHandler           # position 6 — new
from handlers.eml_handler import EmlHandler             # position 7 — new
from handlers.msg_handler import MsgHandler             # position 8 — new
from handlers.image_handler import PngHandler, JpgHandler  # 9/10 — new stubs
```

```
RawContent.text shape per new type
────────────────────────────────────────────────────────────────────

XLSX:                            PPTX:
[Sheet: "Q1 Revenue"]            [Slide 1: "Q1 Results"]
Date | Product | Amount          Revenue grew 12% YoY
2026-01-01 | Widget A | 1200
[Sheet: "Summary"]               [Slide 2: "Outlook"]
Total | 1200                     ...

CSV:                             EML / MSG:
Date,Product,Amount              From: sender@example.com
2026-01-01,Widget A,1200         Subject: Q1 Results

                                 Body text here...

HTML:
Cleaned text — scripts/styles/nav/footer removed
```

---

## Approach

Eight new handlers, one phase each, all following the same `BaseHandler` ABC. Bottom-up order: add dependencies first, then handler + tests, then wire into `__init__.py`. Each phase is independently testable before moving to the next. `handlers/__init__.py` is updated in the final phase after all handlers pass individually.

The `clean_registry` fixture in `tests/test_handlers/conftest.py` already exists — all tests that interact with the registry use it. The `stub_config` autouse fixture is already present and covers new handlers automatically (they don't import CONFIG directly).

New dependencies (`openpyxl`, `python-pptx`, `extract-msg`) are added to `pyproject.toml` in Phase 1 before any handler code, so all subsequent phases can import them cleanly.

---

## Phases

### Phase 1 — Dependencies + XlsxHandler
**Goal**: Add new `pyproject.toml` deps and deliver the first new handler end-to-end.

**Steps**:
1. Add to `pyproject.toml` dependencies:
   - `openpyxl>=3.1`
   - `python-pptx>=1.0`
   - `extract-msg>=0.28`
   Run `uv sync` after editing.
2. Create `handlers/xlsx_handler.py`:
   - `XlsxHandler(BaseHandler)` with `@HandlerRegistry.register`
   - `can_handle`: `return path.suffix.lower() == ".xlsx"`
   - `extract`: `openpyxl.load_workbook(str(path), data_only=True)`, iterate sheets
     - Per sheet: `[Sheet: "name"]` header + first row as column headers + non-empty data rows
     - Rows joined with ` | ` (pipe-separated), sections joined with `\n\n`
     - Empty sheets (no rows) skipped entirely
     - Empty workbook (all sheets skipped) → `Success(RawContent(text=""))`
     - Any exception → `Failure(error=f"XLSX read failed: {exc}", recoverable=False, context={"path": str(path)})`
3. Create `tests/test_handlers/test_xlsx_handler.py`:
   - Fixtures via `openpyxl.Workbook` in `tmp_path`
   - Tests per standard list in research + type-specific tests below

**Files to modify/create**:
- `pyproject.toml` — add `openpyxl`, `python-pptx`, `extract-msg`
- `handlers/xlsx_handler.py` — new
- `tests/test_handlers/test_xlsx_handler.py` — new

**Test criteria**:
- [ ] Single-sheet `.xlsx` with header + data rows → `Success` with `[Sheet: "name"]` header in text
- [ ] Multi-sheet workbook → text contains both sheet headers and their data
- [ ] Empty sheet in multi-sheet workbook → that sheet's section absent from text
- [ ] All-empty workbook → `Success(RawContent(text=""))`
- [ ] `.xlsx` with formulas (`data_only=True`) → cell values (not formulas) in text
- [ ] Non-existent path → `Failure(recoverable=False)`
- [ ] `can_handle(Path("file.xlsx"))` → `True`
- [ ] `can_handle(Path("file.XLSX"))` → `True` (uppercase)
- [ ] `can_handle(Path("file.xls"))` → `False`
- [ ] `uv run pytest tests/test_handlers/test_xlsx_handler.py -v` passes

**Status**: [ ] pending

---

### Phase 2 — CsvHandler
**Goal**: Extract text from `.csv` files with BOM handling and header preservation.

**Steps**:
1. Create `handlers/csv_handler.py`:
   - `CsvHandler(BaseHandler)` with `@HandlerRegistry.register`
   - `can_handle`: `return path.suffix.lower() == ".csv"`
   - `extract`: open with `encoding="utf-8-sig", newline=""`, use `csv.reader`
     - Join each row with `","`, join rows with `"\n"`, strip
     - Empty CSV → `Success(RawContent(text=""))`
     - Any exception → `Failure(error=f"CSV read failed: {exc}", recoverable=False, context={"path": str(path)})`
   - No new dependency — `csv` is stdlib.
2. Create `tests/test_handlers/test_csv_handler.py`:
   - Fixtures written directly via `tmp_path / "file.csv"` `.write_text(...)`

**Files to create**:
- `handlers/csv_handler.py` — new
- `tests/test_handlers/test_csv_handler.py` — new

**Test criteria**:
- [ ] CSV with header + data rows → `Success` with all rows in text, `is_md=False`
- [ ] BOM-prefixed CSV (UTF-8-sig) → first cell has no `﻿` character
- [ ] Empty CSV → `Success(RawContent(text=""))`
- [ ] CSV with quoted fields containing commas → correctly joined (no extra quotes splitting)
- [ ] Non-existent path → `Failure(recoverable=False)`
- [ ] `can_handle(Path("file.csv"))` → `True`
- [ ] `can_handle(Path("file.CSV"))` → `True`
- [ ] `can_handle(Path("file.xlsx"))` → `False`
- [ ] `uv run pytest tests/test_handlers/test_csv_handler.py -v` passes

**Status**: [ ] pending

---

### Phase 3 — PptxHandler
**Goal**: Extract per-slide text with slide title as section header.

**Steps**:
1. Create `handlers/pptx_handler.py`:
   - `PptxHandler(BaseHandler)` with `@HandlerRegistry.register`
   - `can_handle`: `return path.suffix.lower() == ".pptx"`
   - `extract`: `pptx.Presentation(str(path))`, iterate slides with `enumerate(prs.slides, start=1)`
     - Per shape: check `shape.has_text_frame`, get `shape.text_frame.text.strip()`
     - Title detection: `shape.shape_type == 13` → title; all other shapes → body texts list
     - Section header: `[Slide N: "title"]` if title found, else `[Slide N]`
     - Slide appended only if has title or body texts
     - Sections joined with `\n\n`, empty presentation → `Success(RawContent(text=""))`
     - Any exception → `Failure(error=f"PPTX read failed: {exc}", recoverable=False, context={"path": str(path)})`
2. Create `tests/test_handlers/test_pptx_handler.py`:
   - Fixtures via `python_pptx.Presentation()` in `tmp_path`

**Files to create**:
- `handlers/pptx_handler.py` — new
- `tests/test_handlers/test_pptx_handler.py` — new

**Test criteria**:
- [ ] Slide with title + body text → `Success` with `[Slide 1: "title"]` followed by body
- [ ] Slide with body only (no title shape) → `[Slide 1]` header with body
- [ ] Multi-slide presentation → text has one section per non-empty slide
- [ ] Empty presentation (no slides) → `Success(RawContent(text=""))`
- [ ] Non-existent path → `Failure(recoverable=False)`
- [ ] `can_handle(Path("file.pptx"))` → `True`
- [ ] `can_handle(Path("file.PPTX"))` → `True`
- [ ] `can_handle(Path("file.ppt"))` → `False`
- [ ] `uv run pytest tests/test_handlers/test_pptx_handler.py -v` passes

**Status**: [ ] pending

---

### Phase 4 — HtmlHandler
**Goal**: Extract cleaned text from local `.html`/`.htm` files using BeautifulSoup.

**Steps**:
1. Create `handlers/html_handler.py`:
   - `HtmlHandler(BaseHandler)` with `@HandlerRegistry.register`
   - `can_handle`: `return path.suffix.lower() in (".html", ".htm")`
   - `extract`: `path.read_text(encoding="utf-8", errors="replace")`
     - `BeautifulSoup(html, "html.parser")`
     - Decompose `script`, `style`, `head`, `nav`, `footer` tags
     - `soup.get_text(separator="\n", strip=True)`
     - Empty result → `Failure(error="HTML contains no extractable text", recoverable=False, context={"path": str(path)})`
     - Any exception → `Failure(error=f"HTML read failed: {exc}", recoverable=False, ...)`
   - No new dep — `beautifulsoup4` already in `pyproject.toml`.
2. Create `tests/test_handlers/test_html_handler.py`:
   - Fixtures written directly via `tmp_path / "file.html"` `.write_text(...)`

**Files to create**:
- `handlers/html_handler.py` — new
- `tests/test_handlers/test_html_handler.py` — new

**Test criteria**:
- [ ] HTML with `<p>` body → `Success` with body text, `is_md=False`
- [ ] HTML with `<script>` tags → script content absent from output
- [ ] HTML with `<style>` tags → style content absent from output
- [ ] `.htm` extension → `can_handle` returns `True`
- [ ] Empty HTML (`<html><body></body></html>`) → `Failure(recoverable=False)`
- [ ] Non-existent path → `Failure(recoverable=False)`
- [ ] `can_handle(Path("file.html"))` → `True`
- [ ] `can_handle(Path("file.HTML"))` → `True`
- [ ] `can_handle(Path("file.md"))` → `False`
- [ ] `uv run pytest tests/test_handlers/test_html_handler.py -v` passes

**Status**: [ ] pending

---

### Phase 5 — EmlHandler
**Goal**: Extract headers + plain-text body from RFC 2822 `.eml` files.

**Steps**:
1. Create `handlers/eml_handler.py`:
   - `EmlHandler(BaseHandler)` with `@HandlerRegistry.register`
   - `can_handle`: `return path.suffix.lower() == ".eml"`
   - `extract`: `path.read_bytes()`, `email.message_from_bytes(raw, policy=email.policy.default)`
     - Build `headers` string: From, To, Subject, Date via `msg.get(key, "")`
     - Collect `text/plain` parts: `msg.walk()` for multipart, or `msg.get_content()` for single-part
     - `body = "\n".join(body_parts).strip()`
     - `text = f"{headers}\n\n{body}".strip()`
     - Returns `Success` even if body is empty (headers-only is acceptable for HTML-only emails)
     - Any exception → `Failure(error=f"EML read failed: {exc}", recoverable=False, ...)`
   - No new dep — `email` is stdlib.
2. Create `tests/test_handlers/test_eml_handler.py`:
   - Fixtures written directly as RFC 2822 strings via `tmp_path / "file.eml"` `.write_text(...)`

**Files to create**:
- `handlers/eml_handler.py` — new
- `tests/test_handlers/test_eml_handler.py` — new

**Test criteria**:
- [ ] Plain `.eml` with From/Subject/body → `Success` with all three in text
- [ ] Multipart email (plain + html parts) → only plain-text body in output (no HTML tags)
- [ ] Email with no plain-text part (HTML-only) → `Success` with headers only (no `Failure`)
- [ ] Non-existent path → `Failure(recoverable=False)`
- [ ] `can_handle(Path("file.eml"))` → `True`
- [ ] `can_handle(Path("file.EML"))` → `True`
- [ ] `can_handle(Path("file.msg"))` → `False`
- [ ] `uv run pytest tests/test_handlers/test_eml_handler.py -v` passes

**Status**: [ ] pending

---

### Phase 6 — MsgHandler
**Goal**: Extract headers + body from Outlook OLE2 `.msg` files using `extract-msg`.

**Steps**:
1. Create `handlers/msg_handler.py`:
   - `MsgHandler(BaseHandler)` with `@HandlerRegistry.register`
   - `can_handle`: `return path.suffix.lower() == ".msg"`
   - `extract`: use `with extract_msg.Message(str(path)) as msg:`
     - Build `headers` string: From (`msg.sender`), To (`msg.to`), Subject (`msg.subject`), Date (`msg.date`)
     - `body = (msg.body or "").strip()`
     - `text = f"{headers}\n\n{body}".strip()`
     - Any exception → `Failure(error=f"MSG read failed: {exc}", recoverable=False, ...)`
2. Create `tests/fixtures/sample.msg` — commit a minimal valid `.msg` binary fixture.
   - Generate via Python script using `extract-msg` test fixtures or a real Outlook export.
   - Must contain: From, Subject, and a plain-text body.
3. Create `tests/test_handlers/test_msg_handler.py`:
   - Uses `tests/fixtures/sample.msg` for the valid-file test
   - Non-existent path uses `tmp_path`

**Note on fixture**: `extract-msg` itself ships test `.msg` files in its own test suite. Use one from `site-packages/extract_msg/tests/` or generate a minimal one with `extract-msg`'s own test utilities if possible. If not, export a real `.msg` from Outlook.

**Files to create**:
- `handlers/msg_handler.py` — new
- `tests/fixtures/sample.msg` — new committed binary fixture
- `tests/test_handlers/test_msg_handler.py` — new

**Test criteria**:
- [ ] Valid `sample.msg` → `Success` with From/Subject and non-empty body in text
- [ ] Non-existent path → `Failure(recoverable=False)`
- [ ] `can_handle(Path("file.msg"))` → `True`
- [ ] `can_handle(Path("file.MSG"))` → `True`
- [ ] `can_handle(Path("file.eml"))` → `False`
- [ ] `uv run pytest tests/test_handlers/test_msg_handler.py -v` passes

**Status**: [ ] pending

---

### Phase 7 — Image stubs (PngHandler, JpgHandler)
**Goal**: Register PNG/JPG in the handler registry so drops return a clear, actionable `Failure` rather than "no handler found".

**Steps**:
1. Create `handlers/image_handler.py`:
   - `PngHandler(BaseHandler)` with `@HandlerRegistry.register`
     - `can_handle`: `return path.suffix.lower() == ".png"`
     - `extract`: `return Failure(error="image extraction requires a vision-capable LLM — not yet implemented", recoverable=False, context={"path": str(path)})`
   - `JpgHandler(BaseHandler)` with `@HandlerRegistry.register`
     - `can_handle`: `return path.suffix.lower() in (".jpg", ".jpeg")`
     - `extract`: same `Failure` as above
   - No dependency.
2. Create `tests/test_handlers/test_image_handler.py`:
   - Fixtures: write 1-byte dummy file in `tmp_path` (content irrelevant)

**Files to create**:
- `handlers/image_handler.py` — new
- `tests/test_handlers/test_image_handler.py` — new

**Test criteria**:
- [ ] `PngHandler().extract(any_path)` → `Failure(recoverable=False)` with "vision-capable" in error message
- [ ] `JpgHandler().extract(any_path)` → same `Failure` shape
- [ ] `PngHandler().can_handle(Path("photo.png"))` → `True`
- [ ] `PngHandler().can_handle(Path("photo.PNG"))` → `True`
- [ ] `JpgHandler().can_handle(Path("photo.jpg"))` → `True`
- [ ] `JpgHandler().can_handle(Path("photo.jpeg"))` → `True`
- [ ] `JpgHandler().can_handle(Path("photo.JPEG"))` → `True`
- [ ] `PngHandler().can_handle(Path("photo.jpg"))` → `False`
- [ ] `uv run pytest tests/test_handlers/test_image_handler.py -v` passes

**Status**: [ ] pending

---

### Phase 8 — Wire __init__.py + integration tests
**Goal**: Register all new handlers in `handlers/__init__.py`; verify end-to-end dispatch for all 8 new extensions.

**Steps**:
1. Update `handlers/__init__.py` — add imports for all new handlers after the existing three:
   ```python
   from handlers.xlsx_handler import XlsxHandler
   from handlers.csv_handler import CsvHandler
   from handlers.pptx_handler import PptxHandler
   from handlers.html_handler import HtmlHandler
   from handlers.eml_handler import EmlHandler
   from handlers.msg_handler import MsgHandler
   from handlers.image_handler import PngHandler, JpgHandler
   ```
   Update `__all__` to include all new names.
2. Add integration tests to `tests/test_handlers/test_registry.py` (new class `TestExtendedHandlerIntegration`):
   - Do NOT use `clean_registry` fixture — tests verify real registered handlers
   - Test: `resolve(Path("file.xlsx"))` → `Success` with `XlsxHandler` instance
   - Test: `resolve(Path("file.csv"))` → `Success` with `CsvHandler` instance
   - Test: `resolve(Path("file.pptx"))` → `Success` with `PptxHandler` instance
   - Test: `resolve(Path("file.html"))` → `Success` with `HtmlHandler` instance
   - Test: `resolve(Path("file.htm"))` → `Success` with `HtmlHandler` instance
   - Test: `resolve(Path("file.eml"))` → `Success` with `EmlHandler` instance
   - Test: `resolve(Path("file.msg"))` → `Success` with `MsgHandler` instance
   - Test: `resolve(Path("file.png"))` → `Success` with `PngHandler` instance
   - Test: `resolve(Path("file.jpg"))` → `Success` with `JpgHandler` instance
   - Test: `resolve(Path("file.jpeg"))` → `Success` with `JpgHandler` instance

**Files to modify/create**:
- `handlers/__init__.py` — add new handler imports + `__all__` entries
- `tests/test_handlers/test_registry.py` — add `TestExtendedHandlerIntegration` class

**Test criteria**:
- [ ] All 10 new extensions resolve to correct handler type
- [ ] `.htm` resolves to `HtmlHandler` (not "no handler")
- [ ] `.jpeg` resolves to `JpgHandler`
- [ ] `uv run pytest tests/test_handlers/ -v` — full handler suite passes
- [ ] `uv run pytest tests/ -m "not smoke" -v` — no regressions in full suite

**Status**: [ ] pending

---

## Open Questions

None — all design decisions resolved in `docs/research/handlers.md` Extended section (2026-05-22).

Resolved:
- **Image strategy**: Stubs with `Failure(recoverable=False)`. Vision requires `LLMProvider` ABC extension — deferred as TD-H17.
- **`.msg` library**: `extract-msg>=0.28` (context manager support).
- **Structured data depth**: Metadata headers included (sheet names, slide titles, CSV header row).
- **Image handler count**: Two separate classes (`PngHandler`, `JpgHandler`) in one file (`image_handler.py`), consistent with one-suffix-per-handler convention.

---

## Out of Scope

- `.xls` (Excel 97-2003) — requires separate `xlrd`-based handler; deferred TD-H9
- `.ppt` (PowerPoint 97-2003) — same limitation as `.xls`; no library support without conversion
- PPTX speaker notes — TD-H12, Phase 2 enhancement
- EML HTML-only email fallback — TD-H14, Phase 2 enhancement
- MSG RTF body fallback — TD-H16, Phase 2 enhancement
- PNG/JPG vision implementation — TD-H17, requires `LLMProvider.complete_vision()` ABC extension
- Attachment extraction from EML/MSG — recursive handler dispatch, Phase 3+
- CSV encoding auto-detection (`chardet`) — TD-H10
- CSV delimiter auto-detection (`csv.Sniffer`) — TD-H11

---

## Technical Debt Inherited

From `docs/research/handlers.md`:

| ID | What | Action in this plan |
|---|---|---|
| TD-H8 | XLSX merged cells → `None` fill-cells | Documented in `xlsx_handler.py` module docstring |
| TD-H9 | `.xls` not supported | `can_handle` only matches `.xlsx`; `.xls` returns "no handler" `Failure` |
| TD-H10 | CSV encoding auto-detection | `utf-8-sig` covers Excel BOM case; documented in module docstring |
| TD-H11 | CSV delimiter auto-detection | Comma-only; documented in module docstring |
| TD-H12 | PPTX speaker notes | Not extracted; `# COUPLING:` comment in `pptx_handler.py` pointing to enhancement |
| TD-H13 | PPTX non-standard title detection | `shape_type == 13` only; fallback deferred |
| TD-H14 | EML HTML-only emails → headers-only output | Returns `Success`; documented limitation |
| TD-H15 | EML attachment extraction | Not attempted; documented in module docstring |
| TD-H16 | MSG RTF body fallback | `msg.body or ""` only; RTF path deferred |
| TD-H17 | PNG/JPG vision | Stubs registered; full path documented in image_handler.py |
