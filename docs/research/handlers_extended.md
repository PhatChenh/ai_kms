
---

## Extended Handler Research: New Types

_Added: 2026-05-22_

This section covers the eight additional handler types requested after Phase 1 delivery: `.xlsx`, `.csv`, `.pptx`, `.png`, `.jpg`, `.html`, `.eml`, `.msg`.

All follow the same `BaseHandler` ABC contract and self-register via `@HandlerRegistry.register`. All return `Result[RawContent]` with `is_md=False`. The `handlers/__init__.py` import order is the registration order — new handlers append after the Phase 1 trio.

---

### Design Decisions for New Types

**D1 — Structured data: include metadata headers**
XLSX, CSV, and PPTX extract structured text with metadata context (sheet names, slide titles, column headers). This gives the LLM summariser enough structural context to produce meaningful summaries. Plain text concatenation without context would conflate values from different sheets or slides.

**D2 — Images: stub with clear Failure**
PNG/JPG require either OCR (Tesseract binary dep on host) or a vision LLM (requires extending `LLMProvider` ABC with a `complete_vision()` method). Neither is in scope now. Both handlers register, `can_handle` returns `True`, `extract` always returns `Failure(recoverable=False)`. The error message explicitly names what is needed so the debt is visible.

**D3 — HTML: reuse BeautifulSoup already present**
`beautifulsoup4` is already in `pyproject.toml` (added for `url_fetcher.py`). `HtmlHandler.extract` reads the file from disk and applies the same text-cleaning logic as `_fetch_web` in `url_fetcher.py`. No new dependency.

**D4 — Email: split `.eml` (stdlib) from `.msg` (Outlook binary)**
`.eml` is RFC 2822 MIME — Python's `email` stdlib parses it directly. `.msg` is Microsoft OLE2 compound binary — requires `extract-msg` (purpose-built, actively maintained, simple API). Two separate handlers keep each one simple.

**D5 — `.msg` library choice: `extract-msg` over `compoundfiles`**
`extract-msg` provides `msg.subject`, `msg.body`, `msg.sender` directly. `compoundfiles` is lower-level OLE2 reader requiring manual CFBE stream navigation. `extract-msg` is the standard choice for `.msg` in the Python ecosystem.

**D6 — Image handler: one class per suffix, not a shared `ImageHandler`**
Each handler claims exactly one suffix. `PngHandler` and `JpgHandler` are separate classes. `JpgHandler.can_handle` also matches `.jpeg`. This is consistent with all existing handlers and avoids special-casing in `can_handle`.

---

### New Dependencies

| Handler | Library | `pyproject.toml` status |
|---|---|---|
| `XlsxHandler` | `openpyxl` | ❌ must add |
| `CsvHandler` | stdlib `csv` | ✅ stdlib |
| `PptxHandler` | `python-pptx` | ❌ must add |
| `HtmlHandler` | `beautifulsoup4` | ✅ already present |
| `EmlHandler` | stdlib `email` | ✅ stdlib |
| `MsgHandler` | `extract-msg` | ❌ must add |
| `PngHandler` | (none — stub) | ✅ no dep |
| `JpgHandler` | (none — stub) | ✅ no dep |

---

### Updated `handlers/__init__.py` Registration Order

```python
# Import order = registration order. First match wins per suffix.
from handlers.markdown_handler import MarkdownHandler   # exists
from handlers.pdf_handler import PdfHandler             # exists
from handlers.docx_handler import DocxHandler           # exists
from handlers.xlsx_handler import XlsxHandler           # new
from handlers.csv_handler import CsvHandler             # new
from handlers.pptx_handler import PptxHandler           # new
from handlers.html_handler import HtmlHandler           # new
from handlers.eml_handler import EmlHandler             # new
from handlers.msg_handler import MsgHandler             # new
from handlers.image_handler import PngHandler, JpgHandler  # new (stubs)

__all__ = [
    "MarkdownHandler", "PdfHandler", "DocxHandler",
    "XlsxHandler", "CsvHandler", "PptxHandler",
    "HtmlHandler", "EmlHandler", "MsgHandler",
    "PngHandler", "JpgHandler",
]
```

No suffix ambiguity — each handler claims exactly one suffix (or two for `JpgHandler`: `.jpg` and `.jpeg`).

---

### XlsxHandler

```python
@HandlerRegistry.register
class XlsxHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".xlsx"

    def extract(self, path: Path) -> Result[RawContent]:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(path), data_only=True)
            sections: list[str] = []
            for sheet in wb.worksheets:
                rows = list(sheet.iter_rows(values_only=True))
                if not rows:
                    continue
                header = " | ".join(str(c) if c is not None else "" for c in rows[0])
                body_rows = [
                    " | ".join(str(c) if c is not None else "" for c in row)
                    for row in rows[1:]
                    if any(c is not None for c in row)
                ]
                section = f"[Sheet: \"{sheet.title}\"]\n{header}"
                if body_rows:
                    section += "\n" + "\n".join(body_rows)
                sections.append(section)
            text = "\n\n".join(sections).strip()
        except Exception as exc:
            return Failure(
                error=f"XLSX read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))
```

**Key notes:**
- `data_only=True` reads computed cell values, not formulas. Without this, cells containing `=SUM(...)` return `None`.
- Sheet name included as `[Sheet: "name"]` section header so the LLM knows which spreadsheet a value belongs to.
- First row treated as column headers. Empty rows (all `None`) are skipped.
- Empty workbook (all sheets empty) returns `Success(RawContent(text=""))` — same policy as DOCX. The LLM handles empty input.
- `openpyxl` does not support `.xls` (Excel 97-2003 binary). `.xls` would return `Failure` from `openpyxl.load_workbook`. A separate `XlsHandler` using `xlrd` is out of scope.

**`pyproject.toml` addition**: `openpyxl>=3.1`

---

### CsvHandler

```python
@HandlerRegistry.register
class CsvHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".csv"

    def extract(self, path: Path) -> Result[RawContent]:
        try:
            import csv
            lines: list[str] = []
            with path.open(newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                for row in reader:
                    lines.append(",".join(row))
            text = "\n".join(lines).strip()
        except Exception as exc:
            return Failure(
                error=f"CSV read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))
```

**Key notes:**
- `encoding="utf-8-sig"` strips the UTF-8 BOM that Excel adds when saving as CSV. Without this, the first cell of row 1 starts with `﻿`.
- `csv.reader` handles quoted fields and embedded commas correctly. Raw `str.split(",")` does not.
- Header row is included as the first line — the LLM uses it as context for what the columns mean.
- No sheet metadata (single-sheet format by definition).
- Empty CSV returns `Success(RawContent(text=""))`.
- No new dependency — `csv` is stdlib.

---

### PptxHandler

```python
@HandlerRegistry.register
class PptxHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pptx"

    def extract(self, path: Path) -> Result[RawContent]:
        try:
            from pptx import Presentation
            from pptx.util import Pt
            prs = Presentation(str(path))
            slides: list[str] = []
            for i, slide in enumerate(prs.slides, start=1):
                title = ""
                texts: list[str] = []
                for shape in slide.shapes:
                    if not shape.has_text_frame:
                        continue
                    shape_text = shape.text_frame.text.strip()
                    if not shape_text:
                        continue
                    if shape.shape_type == 13:  # MSO_SHAPE_TYPE.TITLE not needed
                        title = shape_text
                    else:
                        texts.append(shape_text)
                header = f"[Slide {i}: \"{title}\"]" if title else f"[Slide {i}]"
                if texts:
                    slides.append(header + "\n" + "\n".join(texts))
                elif title:
                    slides.append(header)
            text = "\n\n".join(slides).strip()
        except Exception as exc:
            return Failure(
                error=f"PPTX read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))
```

**Key notes:**
- Slide title extracted by checking `shape.shape_type` against the title placeholder type. In practice, the title shape is `shape_type == 13` (TITLE) or identified via `shape.is_title`. Use `shape.name.lower().startswith("title")` as a fallback if `shape_type` check misses non-standard templates.
- Each slide becomes a `[Slide N: "Title"]` section. The LLM can reason about slide sequence.
- Speaker notes are **not** extracted in Phase 1 — they live in `slide.notes_slide.notes_text_frame.text`. Adding notes is a TD item.
- Empty presentation returns `Success(RawContent(text=""))`.
- `python-pptx` handles `.pptx` only. `.ppt` (binary PowerPoint 97-2003) is unsupported — same limitation as openpyxl/xls.

**`pyproject.toml` addition**: `python-pptx>=1.0`

---

### HtmlHandler

```python
@HandlerRegistry.register
class HtmlHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in (".html", ".htm")

    def extract(self, path: Path) -> Result[RawContent]:
        try:
            from bs4 import BeautifulSoup
            html = path.read_text(encoding="utf-8", errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "head", "nav", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
        except Exception as exc:
            return Failure(
                error=f"HTML read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        if not text:
            return Failure(
                error="HTML contains no extractable text",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))
```

**Key notes:**
- `beautifulsoup4` already in `pyproject.toml` — no new dependency.
- Same cleaning approach as `url_fetcher._fetch_web`: remove `<script>`, `<style>`, `<head>`, `<nav>`, `<footer>` before text extraction.
- `errors="replace"` handles HTML files saved in legacy encodings (Windows-1252 is common in exported reports).
- `can_handle` matches both `.html` and `.htm` — both are common on Windows.
- Unlike `url_fetcher._fetch_web` (which fetches from a URL), `HtmlHandler` reads from disk. No `requests` call.
- Empty-text failure check: an HTML file of `<html><body></body></html>` would produce empty text → `Failure`. This differs from DOCX/XLSX (which return `Success(text="")`). Rationale: an empty HTML file is malformed input; an empty spreadsheet is a valid blank template.

---

### EmlHandler

```python
@HandlerRegistry.register
class EmlHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".eml"

    def extract(self, path: Path) -> Result[RawContent]:
        try:
            import email
            from email import policy as email_policy
            raw = path.read_bytes()
            msg = email.message_from_bytes(raw, policy=email_policy.default)

            headers = "\n".join([
                f"From: {msg.get('From', '')}",
                f"To: {msg.get('To', '')}",
                f"Subject: {msg.get('Subject', '')}",
                f"Date: {msg.get('Date', '')}",
            ])

            body_parts: list[str] = []
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body_parts.append(part.get_content())
            else:
                if msg.get_content_type() == "text/plain":
                    body_parts.append(msg.get_content())

            body = "\n".join(body_parts).strip()
            text = f"{headers}\n\n{body}".strip()
        except Exception as exc:
            return Failure(
                error=f"EML read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))
```

**Key notes:**
- `policy=email_policy.default` enables the modern `EmailMessage` API (Python 3.6+). Required for `msg.get_content()` and correct MIME decoding.
- Only `text/plain` parts extracted. `text/html` parts are skipped (HTML body in emails is usually a styled version of the plain-text part; extracting both produces duplicates). If no `text/plain` part exists (HTML-only email), body is empty — this is a known limitation (see TD below).
- `msg.walk()` handles nested `multipart/alternative` and `multipart/mixed` structures correctly.
- Headers included in output: From, To, Subject, Date. These are high-signal context for the LLM.
- No new dependency — `email` is stdlib.

**Edge case — HTML-only emails**: Many modern emails have no `text/plain` part. In that case `body` is empty and the output is headers only. This is a Phase 1 limitation. Enhancement: if `body` is empty after plain-text extraction, fall back to extracting `text/html` part through `BeautifulSoup` (same as `HtmlHandler`). Deferred as TD.

---

### MsgHandler

```python
@HandlerRegistry.register
class MsgHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".msg"

    def extract(self, path: Path) -> Result[RawContent]:
        try:
            import extract_msg
            msg = extract_msg.Message(str(path))
            headers = "\n".join([
                f"From: {msg.sender or ''}",
                f"To: {msg.to or ''}",
                f"Subject: {msg.subject or ''}",
                f"Date: {msg.date or ''}",
            ])
            body = (msg.body or "").strip()
            text = f"{headers}\n\n{body}".strip()
            msg.close()
        except Exception as exc:
            return Failure(
                error=f"MSG read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))
```

**Key notes:**
- `extract_msg.Message` opens the OLE2 compound file and exposes `.sender`, `.to`, `.subject`, `.date`, `.body` as decoded strings.
- `msg.close()` must be called to release the file handle. Wrap in `try/finally` in production — the broad `except Exception` does not guarantee cleanup. Consider using `with extract_msg.Message(str(path)) as msg:` if the library supports context manager (it does as of v0.28).
- `msg.body` returns the plain-text body. `msg.htmlBody` is available for HTML body — not used here for same reasons as `EmlHandler`.
- Attachments are accessible via `msg.attachments` — not extracted in Phase 1 (out of scope).
- `extract_msg` import name is `extract_msg` (underscore), pypi name is `extract-msg` (hyphen).

**`pyproject.toml` addition**: `extract-msg>=0.28`

---

### PngHandler and JpgHandler (Stubs)

```python
@HandlerRegistry.register
class PngHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".png"

    def extract(self, path: Path) -> Result[RawContent]:
        return Failure(
            error="image extraction requires a vision-capable LLM — not yet implemented",
            recoverable=False,
            context={"path": str(path)},
        )


@HandlerRegistry.register
class JpgHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in (".jpg", ".jpeg")

    def extract(self, path: Path) -> Result[RawContent]:
        return Failure(
            error="image extraction requires a vision-capable LLM — not yet implemented",
            recoverable=False,
            context={"path": str(path)},
        )
```

**Why stubs:**
PNG/JPG extraction requires either:
1. **OCR** (`pytesseract`) — extracts text from images but requires the Tesseract binary installed on the host machine (`brew install tesseract`). Fails silently on photos/diagrams with no text.
2. **Vision LLM** — passes image bytes to an LLM vision API. Requires extending `LLMProvider` ABC with a `complete_vision(system, user, image_bytes) -> Result[LLMResponse]` method. All providers (Claude, Ollama, OpenAI-compat) would need an implementation or a stub. This is a meaningful interface change to a stable, tested contract.

Neither is appropriate to implement without first extending the `LLMProvider` ABC contract and deciding on the vision model routing strategy. The stubs register both suffixes in the registry so the pipeline routes them correctly (returns `Failure`) instead of producing a confusing "no handler" error.

**Both classes live in `handlers/image_handler.py`** — one file for both stubs. Registration order: PngHandler before JpgHandler.

**Future implementation path:**
1. Add `complete_vision(system: str, user: str, image_bytes: bytes, mime_type: str) -> Result[LLMResponse]` to `LLMProvider` ABC
2. Implement in `ClaudeProvider` using Anthropic's vision API (base64 image content block)
3. Add stub returning `Failure(recoverable=False, "vision not supported by this provider")` to `OllamaProvider` and `OpenAICompatProvider`
4. Replace `PngHandler.extract` / `JpgHandler.extract` stub with actual vision call

---

### Edge Cases for New Types

**XLSX — merged cells**
`openpyxl` reports merged cells as `None` for all but the top-left cell. A merged header spanning columns A–D would show as `"Header | None | None | None"`. This is a known limitation. Enhancement: use `openpyxl`'s `merged_cells` attribute to detect and skip fill-cells. Deferred.

**XLSX — `.xls` vs `.xlsx`**
`openpyxl` only reads `.xlsx` (OOXML). Legacy `.xls` files raise `InvalidFileException`. `XlsxHandler.can_handle` only matches `.xlsx` — `.xls` falls through to `Failure("no handler")`. A separate `XlsHandler` using `xlrd` is out of scope.

**CSV — encoding detection**
`utf-8-sig` handles the most common case (Excel BOM). Non-UTF-8 files (Windows-1252, latin-1) will raise `UnicodeDecodeError` inside the `try/except` → `Failure`. Enhancement: use `chardet` to auto-detect encoding. Deferred.

**CSV — delimiter detection**
`csv.reader` defaults to comma delimiter. TSV files (`.csv` saved with tabs) parse incorrectly. Enhancement: use `csv.Sniffer().sniff()` to auto-detect delimiter. Deferred.

**PPTX — title shape detection**
Not all PPTX templates mark the title shape with `shape_type == 13`. Some use custom layouts where the title is a regular text box. Enhancement: also check `shape.name.lower().startswith("title")` as a fallback. Low priority — wrong title detection produces `[Slide N]` instead of `[Slide N: "Title"]`, not a pipeline failure.

**PPTX — speaker notes**
`slide.notes_slide.notes_text_frame.text` contains speaker notes. These are often the most information-dense part of a slide deck. Not extracted in Phase 1. Enhancement: append `[Speaker notes]\n{notes}` after each slide's body text. High value, low effort — prioritize in next enhancement round.

**EML — HTML-only emails**
Many modern emails have no `text/plain` part. Body will be empty; only headers are extracted. The LLM receives "From/To/Subject/Date" with no content — likely produces a poor summary. Enhancement: fall back to `text/html` part via `BeautifulSoup` if no plain text found. Same code path as `HtmlHandler`.

**EML — attachments**
Attachments inside `.eml` (PDFs, images, DOCX) are not extracted. Inline attachments (`Content-Disposition: inline`) are ignored. Out of scope — would require recursive handler dispatch.

**MSG — RTF body**
Some `.msg` files have `msg.body = None` but `msg.rtfBody` is set (RTF format). `extract-msg` can decode RTF via `compressed_rtf` dependency (installed automatically). If `msg.body` is `None`, attempt `msg.rtfBody` — `extract_msg` will auto-decode it to plain text if the dependency is present. Enhancement: explicit fallback to `msg.rtfBody` when `msg.body` is `None`.

---

### Test Strategy for New Handlers

**Fixture approach per type:**

| Handler | Fixture strategy |
|---|---|
| `XlsxHandler` | Create `.xlsx` in `tmp_path` using `openpyxl` itself: `wb = Workbook(); ws = wb.active; ws.append(["Name", "Value"]); ws.append(["A", 1]); wb.save(path)` |
| `CsvHandler` | Write `.csv` string directly: `path.write_text("Name,Value\nA,1\n")` |
| `PptxHandler` | Create `.pptx` using `python-pptx`: `prs = Presentation(); slide = prs.slides.add_slide(...); prs.save(path)` |
| `HtmlHandler` | Write `.html` string directly: `path.write_text("<html><body><p>Hello</p></body></html>")` |
| `EmlHandler` | Write `.eml` string directly using RFC 2822 format: `path.write_text("From: a@b.com\nSubject: Test\n\nBody text")` |
| `MsgHandler` | Commit a small `.msg` fixture binary in `tests/fixtures/sample.msg` (cannot be generated in-memory without OLE2 writer) |
| `PngHandler` / `JpgHandler` | Write any minimal binary or use `tests/fixtures/sample.png` — content irrelevant since extract always returns Failure |

**Standard tests per handler (apply to all eight):**
1. Valid file → `Success(RawContent)` with expected `text`, `is_md=False`, `source_path == path`
2. Non-existent path → `Failure(recoverable=False)`
3. `can_handle(Path("file.EXT"))` → `True` (correct extension, lowercase)
4. `can_handle(Path("file.EXT".upper()))` → `True` (uppercase suffix, case-insensitive)
5. `can_handle(Path("file.other"))` → `False`

**Type-specific tests:**
- `XlsxHandler`: multi-sheet workbook → text contains both `[Sheet: ...]` headers; empty sheet → skipped
- `CsvHandler`: BOM-prefixed CSV → first cell has no `﻿`
- `PptxHandler`: slide with title and body → text contains `[Slide 1: "..."]` and body
- `HtmlHandler`: HTML with `<script>` → script content not in output; empty HTML → `Failure`
- `EmlHandler`: multipart email with plain + HTML parts → only plain text in body
- `MsgHandler`: valid `.msg` → subject, sender, body all present
- `PngHandler` / `JpgHandler`: any path → `Failure` with message containing "vision-capable"

---

### New Technical Debt

| ID | What | Why deferred | Owned by phase |
|---|---|---|---|
| TD-H8 | XLSX merged cells produce `None` fill-cells in output | Minor display issue; LLM handles gracefully | Phase 3+ |
| TD-H9 | XLSX `.xls` files not supported | Requires separate `xlrd`-based `XlsHandler` | Phase 3+ |
| TD-H10 | CSV encoding auto-detection (`chardet`) | `utf-8-sig` covers the common Excel case | Phase 3+ |
| TD-H11 | CSV delimiter auto-detection (`csv.Sniffer`) | Comma-only for Phase 1 | Phase 3+ |
| TD-H12 | PPTX speaker notes not extracted | High value — `slide.notes_slide.notes_text_frame.text` | Phase 2 enhancement |
| TD-H13 | PPTX non-standard title shape detection | Fallback to `shape.name.lower().startswith("title")` | Phase 3+ |
| TD-H14 | EML HTML-only emails produce body-less output | Fall back to BeautifulSoup on `text/html` part | Phase 2 enhancement |
| TD-H15 | EML attachments not extracted | Recursive handler dispatch needed | Phase 3+ |
| TD-H16 | MSG RTF body fallback when `msg.body is None` | Use `msg.rtfBody` as fallback | Phase 2 enhancement |
| TD-H17 | PNG/JPG vision support | Requires extending `LLMProvider` ABC with `complete_vision()` | Phase 3 |
