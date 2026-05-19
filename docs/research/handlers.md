# Research: Handlers
_Last updated: 2026-05-19_

## Overview

The `handlers/` subsystem is the extraction layer for Phase 1 Capture. It answers one question: "given a dropped file, produce plain text." Handlers know nothing about summarisation, classification, vault writes, or audit logging — they only extract text and wrap it in a typed `RawContent` dataclass. The pipeline's capture `extract` stage routes through the registry, calls the matched handler, and passes `RawContent` forward to the summarise stage.

Three handler types are required for Phase 1: markdown (`.md`), PDF (`.pdf`), and DOCX (`.docx`). All others (YouTube, web, email) are explicitly out of scope until the Phase 1 pipeline proves itself.

---

## Key Components

| File | Role |
|---|---|
| `handlers/base.py` | `RawContent` frozen dataclass + `BaseHandler` ABC |
| `handlers/registry.py` | Class-level `HandlerRegistry`: `register` decorator + `resolve` lookup |
| `handlers/markdown_handler.py` | `.md` extraction via `vault.reader.read_note` |
| `handlers/pdf_handler.py` | `.pdf` text extraction via `pypdf` |
| `handlers/docx_handler.py` | `.docx` extraction via `python-docx` |
| `handlers/__init__.py` | Bootstrap: imports all handlers so their `@register` decorators fire |

None of these files exist yet. `handlers/` directory exists but is empty.

---

## How It Works

### RawContent shape

`RawContent` is a `@dataclass(frozen=True)` with three fields:

```python
@dataclass(frozen=True)
class RawContent:
    text: str          # extracted body — no frontmatter for .md
    source_path: Path  # absolute path to the source file
    is_md: bool        # True = .md drop (in-place update); False = binary drop (create sibling)
```

`is_md` is the branch selector for the capture pipeline's `store` stage. When `True`, the pipeline updates the note in place and must pass `text` back unchanged as the `content` argument to `write_note` (body-preservation discipline). When `False`, the pipeline creates a sibling `.md` and moves the binary to `attachment/`.

`source_path` is carried through the pipeline so the `store` stage knows where to write back without re-discovering the path.

### BaseHandler ABC

```python
from abc import ABC, abstractmethod
from pathlib import Path
from core.result import Result

class BaseHandler(ABC):
    @abstractmethod
    def can_handle(self, path: Path) -> bool: ...

    @abstractmethod
    def extract(self, path: Path) -> Result[RawContent]: ...
```

Two abstract instance methods. `can_handle` is a pure predicate — no I/O, just suffix check. `extract` does the actual work and returns `Result[RawContent]`. Every public function in `handlers/` returns `Success` or `Failure` — raw values and `None` are forbidden (cross-phase constraint from STATE.md).

Note: CLAUDE.md's example shows `can_handle(self, source: str)` — that example is illustrative only. The spec and this codebase use `Path` throughout (vault/reader, vault/writer, vault/indexer all pass `Path`).

### HandlerRegistry

```python
class HandlerRegistry:
    _handlers: list[BaseHandler] = []  # class-level; populated at import time

    @classmethod
    def register(cls, handler_class: type[BaseHandler]) -> type[BaseHandler]:
        cls._handlers.append(handler_class())  # instantiate on register
        return handler_class                    # return class for decorator pattern

    @classmethod
    def resolve(cls, path: Path) -> Result[BaseHandler]:
        for handler in cls._handlers:
            if handler.can_handle(path):
                return Success(handler)
        return Failure(
            error=f"no handler for extension '{path.suffix}'",
            recoverable=False,
            context={"path": str(path)},
        )
```

**Resolution order = registration order. First match wins.** `MarkdownHandler` is registered first (via `__init__.py` import order). Since each handler only claims its own suffix, ambiguity is unlikely — but the "first match" rule is the tie-breaker if it ever arises. This must be documented in `registry.py`.

**Instantiation strategy:** Handlers are instantiated once on registration (not on every `resolve` call). Handlers are stateless, so a single shared instance is safe.

### Bootstrap — the import problem

For `resolve` to find any handlers, the handler modules must be imported before the first call to `resolve`. The cleanest solution is `handlers/__init__.py`:

```python
# handlers/__init__.py — import order is registration order
from handlers.markdown_handler import MarkdownHandler  # registered first
from handlers.pdf_handler import PdfHandler
from handlers.docx_handler import DocxHandler
```

`pipelines/capture.py` does `import handlers` (or `from handlers import ...`), which triggers `__init__.py`, which registers all three handlers in order. No magic, no fragile import side-effects buried in individual files.

### MarkdownHandler

```python
@HandlerRegistry.register
class MarkdownHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".md"

    def extract(self, path: Path) -> Result[RawContent]:
        match read_note(path):
            case Failure() as f:
                return f
            case Success(value=note):
                return Success(RawContent(
                    text=note.content,
                    source_path=path,
                    is_md=True,
                ))
```

`vault.reader.read_note` is the only legitimate path to reading a vault `.md` file — it parses frontmatter, normalises the body with `rstrip("\n")`, and computes `content_hash`. `note.content` is the body without the frontmatter block. This is precisely what the pipeline's `store` stage must pass back to `write_note` as `content` to preserve the body byte-for-byte (body-preservation discipline from `docs/roadmap.md` Phase 1).

Callers must never read `.md` files with raw `Path.read_text()` — that would include frontmatter as body text, which the LLM would then try to summarise.

### PdfHandler

```python
@HandlerRegistry.register
class PdfHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def extract(self, path: Path) -> Result[RawContent]:
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(pages).strip()
        except Exception as exc:
            return Failure(
                error=f"PDF read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        if not text:
            return Failure(
                error="PDF contains no extractable text (image-only or empty)",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))
```

`pypdf` is not in `pyproject.toml` yet — must be added as a dependency.

Empty-text check (`if not text`) must run after joining all pages. A PDF where every page returns `""` from `extract_text()` is image-only and cannot be processed without OCR (out of scope for Phase 1).

### DocxHandler

```python
@HandlerRegistry.register
class DocxHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".docx"

    def extract(self, path: Path) -> Result[RawContent]:
        try:
            from docx import Document
            doc = Document(str(path))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as exc:
            return Failure(
                error=f"DOCX read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))
```

`python-docx` (import: `from docx import Document`) is not in `pyproject.toml` yet — must be added.

A completely empty DOCX (all paragraphs empty) returns `Success(RawContent(text="", ...))`. The pipeline's `summarise` stage will receive empty text and should handle it gracefully (the LLM will likely produce a short "empty document" summary — that is acceptable). A `Failure` here would block legitimate blank-template documents.

---

## Edge Cases & Silent Failure Modes

### 1. Handler not registered — registry is empty at resolve time

If `handlers/__init__.py` is never imported (e.g. the pipeline does `from handlers.registry import HandlerRegistry` without importing the full `handlers` package), `resolve` will always return `Failure`. This is silent: no error at boot, only at runtime when `capture_file` first runs.

**Mitigation:** `handlers/__init__.py` imports all concrete handlers. The pipeline imports `handlers` (the package), not just `handlers.registry`. Test: assert `len(HandlerRegistry._handlers) == 3` after importing the package.

### 2. pypdf `extract_text()` returns None on some pages

`pypdf.PageObject.extract_text()` can return `None` for pages with no text layer (scanned images). The code uses `page.extract_text() or ""` to guard this. Joining may yield `"\n\n\n"` (all empty) — caught by `if not text.strip()`.

### 3. pypdf password-protected PDF

`pypdf.PdfReader` raises `pypdf.errors.PdfReadError` or `pypdf.errors.PdfStreamError` for password-protected files. The broad `except Exception` catch handles this and returns `Failure(recoverable=False)`.

### 4. python-docx — only paragraph text extracted

`python-docx` paragraphs do not include table cell text, text boxes, or header/footer content. A DOCX with all content in tables will extract as empty text → `Success` with `text=""`. This is a Phase 1 limitation, not a bug. A Phase 1+ enhancement: also iterate `table.rows[x].cells[y].paragraphs`. Deferred.

### 5. Case sensitivity in can_handle

`.PDF` (uppercase suffix) would not match `".pdf"`. All handlers use `path.suffix.lower()` — confirmed. Must be explicit in every `can_handle` implementation.

### 6. Symlinks

`vault/indexer.py` skips symlinks (`file_path.is_symlink()`). But handlers receive paths directly from the CLI or watcher, not through the indexer. A symlinked PDF passed to `kms capture` would be processed. This is likely fine but undocumented.

### 7. Two handlers claiming the same suffix

Nothing in the registry prevents two handlers registering for `.md`. The first registered wins silently — no warning. This is a known limitation of the "first match" approach. Document it; enforce via convention (one suffix per handler).

---

## Dependencies & Coupling

### What handlers import

| Module | Needed by |
|---|---|
| `core/result.py` | All handlers — `Result`, `Success`, `Failure` |
| `core/exceptions.py` | Available (`HandlerError`) but not raised directly — convert to `Failure` |
| `vault/reader.py` | `MarkdownHandler` only — `read_note` |
| `pypdf` (external) | `PdfHandler` — add to `pyproject.toml` |
| `python-docx` (external) | `DocxHandler` — add to `pyproject.toml` |

Handlers do NOT import: `core/config.py`, `storage/*`, `vault/writer.py`, `llm/*`, `core/audit.py`. They are deliberately narrow.

### What imports handlers

| Module | How |
|---|---|
| `pipelines/capture.py` | Imports `handlers` package to trigger registration; calls `HandlerRegistry.resolve(path)` in the `extract` stage |
| `tests/test_handlers/` | Direct imports of each handler class for unit testing |

### Missing dependencies (pyproject.toml gaps)

- `pypdf` — required for `PdfHandler`. Not currently listed.
- `python-docx` — required for `DocxHandler`. Not currently listed.

Both must be added before Phase 1 PDF/DOCX tests can run.

---

## Open Questions

| # | Question | Checked |
|---|---|---|
| OQ-H1 | Should an empty DOCX (`text=""` after extraction) return `Failure` or `Success`? Spec says "join paragraph text" with no empty-check. Current recommendation: `Success` (blank documents are valid; let the LLM handle it). | Checked spec — no guidance. Recommendation above. |
| OQ-H2 | Should `resolve` be a `@classmethod` or a module-level function? Either works. `@classmethod` on `HandlerRegistry` is consistent with CLAUDE.md's `@HandlerRegistry.register` example. | Confirmed: classmethod per spec example. No open issue. |
| OQ-H3 | `pypdf` vs `pdfplumber` — spec says use `pypdf`, switch only if layout-heavy PDFs garble. No layout-heavy PDF requirement in Phase 1. | Confirmed: `pypdf`. |
| OQ-H4 | Where does `handlers/__init__.py` live and what does it contain? Not explicitly specified. Recommended: import all concrete handlers in registration order. | Spec does not address. Solution above is the clean one. Flag to confirm during planning. |

---

## Reference Project Patterns

### What the reference does (ingest.js)

The reference uses a central `TYPE_MAP` dict (`{ '.pdf': 'pdf', '.md': 'markdown', ... }`) and a single `ingestFile()` function that branches on `type`:

```js
if (type === 'pdf') {
  content = await extractPdfContent(filePath, filename);
} else {
  content = extractContent(filePath, type, filename);
}
```

**Why it exists in the reference:** The reference is a Node.js knowledge-base server without an AI-first design philosophy. Simplicity over extensibility — the `TYPE_MAP` approach is fine for a small, stable set of types where someone will always modify `ingest.js` to add new ones.

**Why we do not adopt it:** CLAUDE.md explicitly names this as the "Bad" pattern. Every new type requires modifying `ingest.js`. Our Handler Registry pattern requires only a new file. Given the roadmap's intention to add YouTube, web, email, and chat handlers in later phases, the registry scales better.

### What the reference does (capture/*.js)

`src/capture/` has per-type modules (`web.js`, `youtube.js`, `terminal.js`, `x-bookmarks.js`) — each a standalone function, no shared ABC, no registry. They're called by routing code that knows their names.

**Useful signal:** Per-type files are the right approach. Our design goes further by adding a shared contract (ABC) and automatic discovery (registry). The reference confirms that per-type isolation is the right instinct, even if their mechanism is simpler.

### PDF extraction approach

The reference uses `pdf-parse` (Node.js). Our `pypdf` equivalent does the same thing: parse buffer, extract text per page. The reference silently returns a fallback string on failure (`Could not extract text: ${err.message}`) — we return `Failure` instead (no silent failures rule).

---

## Technical Debt Spotted

| ID | What | Why deferred | Owned by phase |
|---|---|---|---|
| TD-H1 | `python-docx` does not extract table cell text, text boxes, or header/footer content. | Phase 1 limitation — plain paragraph extraction is sufficient for most documents. Enhancement needed for layout-heavy reports. | Phase 3+ |
| TD-H2 | No OCR fallback for image-only PDFs. | Explicitly out of scope for Phase 1 (spec: "No OCR in Phase 1"). Would require `pytesseract` or cloud OCR. | Phase 3+ |
| TD-H3 | `HandlerRegistry._handlers` is a class-level mutable list — test pollution risk. One test registering a dummy handler affects all subsequent tests in the session. | Phase 1 scope: tests should either use `HandlerRegistry._handlers.clear()` in teardown, or instantiate a fresh registry per test. Design solution: make `_handlers` a module-level variable behind a function, or accept teardown convention. | Phase 1 tests |
| TD-H4 | `pypdf` and `python-docx` are missing from `pyproject.toml`. | Not yet added. Must be added before Phase 1 PDF/DOCX tests. | Phase 1 |
