# Research: Phase 6 Slice A1 — Daemon Core Sync Pipe

_Last updated: 2026-06-13_

## Overview

The daemon is a thin local app that watches the user's vault, extracts text from files, and uploads everything to the cloud knowledge base. This research verified the spec's 10 assumptions about reusing existing handlers, API endpoints, database operations, and dependencies against the actual source code.

Summary: 6 assumptions validated, 2 partially invalidated with clear resolution paths, 1 newly invalidated (missed by the spec), and 1 unverifiable from code alone. The two partially invalidated assumptions (A2 handler CONFIG dependency, A4 extracted_text validation) were already flagged by the spec; this research confirms them and adds detail. The newly invalidated assumption is A1 — the spec claimed no handler imports from `vault/`, but MarkdownHandler imports `vault.reader` at module scope. This is not a showstopper (vault.reader has no CONFIG or DB dependency), but the import boundary test in component 10 must account for it.

No critical redesign needed — all invalidations are mechanical (type-b fixes: wrong detail, fixable without structural change). Q4 diagram skipped.

---

## Key Components

These are the existing components the daemon reuses, verified against real code.

| Component | File | Role in Daemon |
|-----------|------|----------------|
| HandlerRegistry | `src/handlers/registry.py` | Dispatches file path to correct text extractor. `resolve(path)` iterates `_handlers` list, calls `can_handle()` (extension check only, no CONFIG). Returns `Success(handler)` or `Failure`. |
| BaseHandler / RawContent | `src/handlers/base.py` | ABC for handlers + immutable extraction result (`text`, `source_path`, `is_md`). Imports only `core.result`. |
| Concrete handlers | `src/handlers/*.py` (10 handlers) | PDF, DOCX, XLSX, CSV, PPTX, HTML, EML, MSG each lazily import CONFIG inside `extract()` for `max_file_size_bytes`. Image handlers (PNG, JPG) return Failure immediately (no CONFIG). MarkdownHandler imports `vault.reader` (no CONFIG). |
| Result type | `src/core/result.py` | `Success[T]` / `Failure` pattern. Imports only `traceback` (stdlib) and `core.exceptions.KMSError`. |
| Upload endpoint | `src/mcp_server/api.py:upload_handler` | Accepts JSON with `vault_path`, `extracted_text`, `content_hash`. Validates all three as required (400 if missing). Calls `upsert_from_upload()`. |
| Event endpoint | `src/mcp_server/api.py:event_handler` | Accepts JSON with `type` ("moved"/"deleted"). Moved requires `old_path` + `new_path`, calls `rename()`. Deleted requires `path`, calls `delete_by_path()`. Field is `body.get("type")`. |
| Secret-key gate | `src/mcp_server/api.py:require_key` | Reads `KMS_DAEMON_API_KEY` from env, validates `Authorization: Bearer <key>`. Returns key on match, None on mismatch. |
| `upsert_from_upload` | `src/storage/documents.py:156` | Three-way decision by `content_hash`: no row = INSERT, same hash = SKIP, different hash = UPDATE. Stores `extracted_text` as `full_body`. Does NOT trigger search indexing. |
| `all_paths` | `src/storage/documents.py:285` | Returns `Success([(vault_path, content_hash), ...])` from a single SELECT. Directly usable for GET /api/state. |
| Vault watcher patterns | `src/vault/watcher.py` | Debounce via `threading.Timer` + `threading.Lock`, NFC via `unicodedata.normalize("NFC", ...)`, watchdog `Observer` + `FileSystemEventHandler`. Heavy vault-specific logic to strip (binary sync, sibling management, move_guard, folder pending, re-homing). |

---

## How It Works

When the daemon boots, it loads its own standalone config (vault root, cloud endpoint, API key from env). It then runs the startup scanner: GET /api/state fetches all known `(vault_path, content_hash)` pairs from the cloud, the daemon walks the vault directory and hashes each file, and it compares. New or changed files go through the text extractor (handler registry dispatch), then upload to the cloud. Cloud-only files are reported as deleted. Matching files are skipped.

After the scan, the live watcher starts. It uses watchdog to detect filesystem events, debounces them with threading.Timer, normalizes paths with NFC, and dispatches to the extractor (for creates/modifies) or event reporter (for moves/deletes). All uploads use HTTP POST to `/api/upload` with Bearer auth; events go to `/api/event`.

---

## Spec Verification

Each assumption from the spec is verified against the actual code. The spec's line number references were generally accurate but are noted where they diverged.

| ID | Spec Claim | Verdict | Evidence |
|----|-----------|---------|----------|
| A1 | No handler in `handlers/` imports from `storage/`, `mcp_server/`, `llm/`, or `pipelines/` at module scope | ❌ Partially Invalidated | Correct for `storage/`, `mcp_server/`, `llm/`, `pipelines/`. But **MarkdownHandler imports `vault.reader` at module scope** (`markdown_handler.py:14`), and `url_fetcher.py` imports `core.config.CONFIG` lazily inside `_fetch_web()`. Also, `handlers/__init__.py` imports `url_fetcher` at module scope which pulls in `requests`, `bs4`, `youtube_transcript_api`. The `vault/` import matters because component 10's import boundary test lists `vault/` as forbidden. |
| A2 | HandlerRegistry.resolve() works without CONFIG. But handler.extract() lazily imports CONFIG for max_file_size_bytes (except MarkdownHandler and image handlers). | ✅ Validated | Confirmed exactly. `resolve()` (`registry.py:48-65`) checks `can_handle()` which only tests file extension. `extract()` in PDF, DOCX, XLSX, CSV, PPTX, HTML, EML, MSG all have `from core.config import CONFIG` inside the method body, accessing only `CONFIG.main.handlers.max_file_size_bytes`. MarkdownHandler: no CONFIG import. Image handlers: no CONFIG import (immediate Failure return). The CONFIG field default is 50 MB (`config.py:286`). |
| A3 | `content_hash TEXT` column exists in schema.sql. `upsert_from_upload` writes to it. `all_paths()` queries it. | ✅ Validated | `schema.sql:11` has `content_hash TEXT`. `upsert_from_upload` writes it on INSERT (`documents.py:210`) and UPDATE (`documents.py:238`). `all_paths()` (`documents.py:299`) queries `SELECT vault_path, content_hash FROM documents`. Directly reusable for GET /api/state. |
| A4 | `upsert_from_upload` does not validate `extracted_text`. The 400 rejection is in `upload_handler`. | ✅ Validated (with caveat) | `upload_handler` (`api.py:110-115`) checks `if not extracted_text: return 400`. `upsert_from_upload` itself stores whatever `extracted_text` is passed as `full_body`. **Caveat:** the function signature is `extracted_text: str` (not `str | None`) — for binary uploads the signature must change to `extracted_text: str | None = None`. The DB column `full_body` is nullable (no NOT NULL constraint). No downstream consumer breaks on NULL `full_body` — search indexing happens separately via `retrieval/keyword.py` and `retrieval/embeddings.py`, which are called by the capture pipeline, not by `upsert_from_upload`. |
| A5 | Starlette supports Content-Type dispatch and multipart parsing. | ✅ Validated | `request.headers.get("content-type")` is standard Starlette. `request.form()` for multipart requires `python-multipart`, which is installed (v0.0.32, required-by `mcp`). Starlette 1.3.0 multipart parser available. |
| A6 | `core/result.py` has no transitive chain to config/DB/AI. | ✅ Validated | `core/result.py` imports `traceback` (stdlib) and `core.exceptions.KMSError`. `core/exceptions.py` is 21 lines of pure exception classes with zero imports. Runtime test confirmed: importing `handlers` does not load `core.config` into `sys.modules`. |
| A7 | Event handler uses `body.get("type")`, not `"event_type"`. Handles "moved" and "deleted". | ✅ Validated | `api.py:182`: `event_type = body.get("type")`. Moved path requires `old_path` + `new_path` (`api.py:190-197`), calls `rename(old=old_path, new=new_path)`. Deleted requires `path` (`api.py:199-205`), calls `delete_by_path(vault_path=path)`. Daemon must send `"type"`, not `"event_type"`. |
| A8 | watchdog 4.0+ provides FileCreatedEvent, FileModifiedEvent, FileMovedEvent, FileDeletedEvent. | ⚠️ Unverifiable | `vault/watcher.py:21-30` imports all four event types plus Dir variants. `pyproject.toml` pins `watchdog>=4.0`. These imports work in the current codebase. Cannot verify macOS FSEvents backend behavior from code alone — requires runtime testing. |
| A9 | `RawContent.text` contains full extracted text suitable for upload. | ✅ Validated | `RawContent` (`base.py:47-58`) has `text: str`, `source_path: Path`, `is_md: bool`. Every handler's `extract()` stores the full extracted text in `text`. PDF concatenates all pages, DOCX all paragraphs, XLSX all sheets, etc. No truncation or summary — raw extraction output. |
| A10 | Starlette supports multipart form-data via `request.form()` without additional dependencies. | ❌ Invalidated | Starlette REQUIRES `python-multipart` for `request.form()` parsing — it is not bundled. Currently installed as transitive dep of `mcp` (v0.0.32). Must be added to `pyproject.toml` explicitly because daemon's multipart support should not depend on `mcp` being installed. |

---

## Edge Cases & Silent Failure Modes

These are scenarios where the daemon could fail silently or behave unexpectedly.

1. **MarkdownHandler uses vault.reader for parsing, not raw text.** This means .md files go through frontmatter stripping and content hashing via vault's parser. For the daemon's purposes (just extracting text to upload), this is actually fine — it gets clean body text without YAML frontmatter. But it creates a transitive import of `vault.reader` and `vault.frontmatter` via `handlers/__init__.py`. The import boundary test must either allow this or the daemon must skip MarkdownHandler and read .md files raw.

2. **url_fetcher is imported at module scope by handlers/__init__.py.** This pulls in `requests`, `bs4`, `youtube_transcript_api`, and a `ThreadPoolExecutor`. The daemon does not need URL fetching but gets these imports via `import handlers`. The daemon could `import handlers.registry` directly instead of `import handlers` to avoid this.

3. **`upsert_from_upload` return value on SKIP.** When `content_hash` matches, the function returns `Success(existing_id)` — same type as INSERT and UPDATE. The daemon's uploader must treat all three as success.

4. **`all_paths()` returns `content_hash` as potentially NULL.** The `content_hash TEXT` column has no NOT NULL constraint. Rows created by the capture pipeline's `upsert()` (not `upsert_from_upload`) may have NULL `content_hash`. The daemon's reconcile must handle NULL hashes in the cloud manifest (treat as "always re-upload").

5. **Large vault walks.** The startup scanner calls `all_paths()` which loads ALL document rows into memory. For very large vaults (10k+ files), this could be memory-heavy. Not a blocker for A1 but worth noting.

---

## Dependencies & Coupling

The daemon's external dependency situation:

| Dependency | Status | Notes |
|-----------|--------|-------|
| `watchdog>=4.0` | Already in pyproject.toml | Same version pin. Daemon reuses it. |
| `structlog` | Already in pyproject.toml | Runtime test confirmed: no transitive imports to core/config or storage. Safe for daemon use. Daemon must configure structlog independently (not via `core/logging_setup.py` which wires with CONFIG). |
| `httpx` | Installed (0.28.1), transitive dep of `anthropic`, `mcp`, `openai`, `huggingface-hub` | Already available. Supports `httpx.AsyncClient()` for async HTTP. **Recommended over aiohttp** because: (1) already installed, no new dep; (2) supports both sync and async; (3) familiar API; (4) lighter than aiohttp. |
| `aiohttp` | NOT installed | Would be a new dependency. Not recommended — httpx is already available. |
| `python-multipart` | Installed (0.0.32), transitive dep of `mcp` | Available but not in pyproject.toml. **Must add explicitly** for binary upload multipart parsing — daemon needs it even if mcp is not installed. |
| `core/result.py` | Safe | Only imports `traceback` + `core.exceptions`. No transitive chain. |
| `handlers/` | Safe with caveats | Importing `handlers` (the package) pulls in `vault.reader`, `vault.frontmatter` (via MarkdownHandler), and `url_fetcher` deps (requests, bs4, youtube_transcript_api). All CONFIG-free at import time. |

---

## Extension Points

The daemon design has clear extension boundaries for future slices.

- **Cache layer (A2):** The extractor returns `TextContent` / `BinaryContent` with `content_hash`. A2 can add a local cache keyed by `content_hash` between the extractor and uploader without changing either interface.
- **Move detection (A2):** The watcher emits raw events. A2 can buffer delete+create pairs and match by hash to reconstruct moves.
- **Blob storage (Phase 7):** The binary upload sends `content_hash`, `original_filename`, `file_size_bytes`, `mime_type`. Phase 7 adds blob persistence on the cloud side — daemon's upload format stays unchanged.
- **New handlers:** Adding a new file format handler requires only adding a handler file and registering it. The daemon's extractor uses `HandlerRegistry.resolve()` and automatically picks up new handlers.

---

## Open Questions

1. **Should the daemon import `handlers` (the full package) or cherry-pick `handlers.registry`?** Importing the package triggers MarkdownHandler registration (pulls in vault.reader) and url_fetcher imports (requests, bs4, youtube_transcript_api). Importing `handlers.registry` alone gets the registry but no handlers are registered. The daemon would need to explicitly import each handler it wants. Recommendation: import the full package — the transitive imports are safe and lightweight, and cherry-picking handlers creates maintenance burden.

2. **Import boundary test: should `vault.reader` and `vault.frontmatter` be allowed as transitive imports through `handlers/`?** These modules have no CONFIG or DB dependency. The test could whitelist them, or it could only check direct imports in daemon source files (not transitive). Recommendation: test only direct imports in `src/daemon/*.py` files, not transitive imports via handlers.

---

## Technical Debt Spotted

1. **TD-DAEMON-01: `upsert_from_upload` type signature needs `extracted_text: str | None = None`.** Current signature requires a string. Binary uploads need NULL `full_body`. Low risk — the function already passes the value through to SQL without validation.

2. **TD-DAEMON-02: `python-multipart` should be explicit in pyproject.toml.** Currently installed only as transitive dep of `mcp`. If mcp is removed or the daemon is deployed without mcp, multipart parsing breaks silently at runtime.

3. **TD-DAEMON-03: `all_paths()` loads entire documents table into memory.** For large vaults this could be expensive. Pagination or streaming would be better for the GET /api/state endpoint. Not a blocker for A1.

---

## Invalidated Assumptions

### A1 — Handler imports from vault/

**Spec claimed:** "No handler in handlers/ imports from storage/, mcp_server/, llm/, or pipelines/ at module scope."

**Code shows:** The claim is correct for storage/mcp_server/llm/pipelines. But MarkdownHandler (`handlers/markdown_handler.py:14`) imports `vault.reader.read_note` at module scope. Additionally, `handlers/__init__.py:14` imports `url_fetcher` which exposes `detect_urls` and `fetch_url_content` at package level. The url_fetcher itself lazily imports CONFIG inside `_fetch_web()` only (not at module scope).

**Why this matters:** Component 10 (import boundary test) lists `vault/` as a forbidden import for the daemon. Importing `handlers` transitively pulls in `vault.reader` and `vault.frontmatter`. However, these vault modules are safe — they import only `core.result` and stdlib. The real question is whether the import boundary test should grep daemon source files only (direct imports) or also scan transitive imports.

**Suggested resolution directions:**
1. **(Recommended) Test only direct imports in `src/daemon/*.py` files.** The import boundary prevents the daemon from coupling to vault logic. Transitive imports through handlers are safe because handlers is an allowed import. The test `grep -rn "from vault\|import vault" src/daemon/` would catch direct daemon-to-vault coupling without blocking the handlers' internal structure.
2. Import `handlers.registry` + individual handlers explicitly, skipping MarkdownHandler. This avoids the vault import but means the daemon can't extract .md files with frontmatter parsing.

### A10 — Starlette multipart requires python-multipart

**Spec claimed:** "Starlette supports multipart form-data parsing via request.form() without additional dependencies."

**Code shows:** Starlette requires `python-multipart` as an explicit dependency for `request.form()` and file uploads. It is currently installed (v0.0.32) as a transitive dependency of `mcp` — not declared in `pyproject.toml`.

**Why this matters:** If `mcp` is ever removed from dependencies, or the daemon is deployed in an environment without `mcp`, multipart form parsing will fail at runtime with an import error. Binary uploads would silently break.

**Suggested resolution directions:**
1. **(Recommended) Add `python-multipart>=0.0.20` to pyproject.toml.** Makes the dependency explicit. Low risk — it's already installed.
2. Accept the transitive dependency and document it. Higher risk of silent breakage.

---

## Handler CONFIG Dependency — Resolution Recommendation

Every handler except MarkdownHandler and image handlers lazily imports `from core.config import CONFIG` inside `extract()` to read a single field: `CONFIG.main.handlers.max_file_size_bytes` (default 50 MB). This triggers the full CONFIG validation chain (vault root must exist, provider keys must be set, etc.), which the daemon cannot satisfy.

**Inventory of CONFIG accesses in handlers:**

| Handler | CONFIG Field Accessed | Location |
|---------|----------------------|----------|
| PdfHandler | `CONFIG.main.handlers.max_file_size_bytes` | `pdf_handler.py:39-41` |
| DocxHandler | `CONFIG.main.handlers.max_file_size_bytes` | `docx_handler.py:41-43` |
| XlsxHandler | `CONFIG.main.handlers.max_file_size_bytes` | `xlsx_handler.py:46-48` |
| CsvHandler | `CONFIG.main.handlers.max_file_size_bytes` | `csv_handler.py:30-32` |
| PptxHandler | `CONFIG.main.handlers.max_file_size_bytes` | `pptx_handler.py:26-28` |
| HtmlHandler | `CONFIG.main.handlers.max_file_size_bytes` | `html_handler.py:26-28` |
| EmlHandler | `CONFIG.main.handlers.max_file_size_bytes` | `eml_handler.py:26-28` |
| MsgHandler | `CONFIG.main.handlers.max_file_size_bytes` | `msg_handler.py:26-28` |
| url_fetcher (_fetch_web) | `CONFIG.main.handlers.max_web_fetch_bytes`, `.web_fetch_timeout_seconds`, `.dns_resolve_timeout_seconds`, `.max_redirects` | `url_fetcher.py:275-282` |

All 8 extract() handlers access exactly ONE field. url_fetcher accesses 4 fields but is not used by the daemon (it's a pipeline stage, not a handler).

**Recommended resolution: Option (b) — refactor handlers to accept `max_file_size_bytes` as a parameter.**

Rationale:
- **Option (a) — inject minimal CONFIG before calling handlers.** Risky: CONFIG is a complex Pydantic model with validators. Building a stub that passes all validation without a real vault root and provider keys is fragile and will break when CONFIG grows new required fields. Rejected.
- **Option (b) — refactor handlers to accept `max_file_size_bytes` as a parameter.** Clean, explicit, testable. Changes `extract(path)` to `extract(path, max_file_size_bytes=None)` with a sentinel that triggers lazy CONFIG import only when the param is not provided. This preserves backward compatibility: the capture pipeline calls `extract(path)` (param is None, falls back to CONFIG). The daemon calls `extract(path, max_file_size_bytes=50_000_000)` and CONFIG is never triggered. Touches each handler file once (pattern change), and the base class ABC signature.
- **Option (c) — monkey-patch or wrap handler.extract().** Fragile, hard to test, violates explicit interface principle. Rejected.

**Implementation sketch for option (b):**
```python
# base.py — add optional parameter with sentinel
class BaseHandler(ABC):
    @abstractmethod
    def extract(self, path: Path, *, max_file_size_bytes: int | None = None) -> Result[RawContent]:
        ...

# Each handler — use parameter if provided, else lazy CONFIG
def extract(self, path: Path, *, max_file_size_bytes: int | None = None) -> Result[RawContent]:
    if max_file_size_bytes is None:
        from core.config import CONFIG
        max_file_size_bytes = CONFIG.main.handlers.max_file_size_bytes
    # ... rest of extract logic unchanged
```

This is a mechanical change across 8 files + base.py + 0 existing callers break (they all pass `path` only, getting the default None which triggers CONFIG as before).

---

## New Risks Not in Spec

1. **Daemon importing `handlers` pulls in heavy third-party deps at import time.** `handlers/__init__.py` imports `url_fetcher` which imports `requests`, `bs4`, `youtube_transcript_api`. These are not needed by the daemon. Impact: slightly slower daemon startup, slightly larger memory footprint. Not a blocker but worth noting — the daemon could import `handlers.registry` and individual handlers selectively to avoid this.

2. **`httpx` recommended over `aiohttp`.** The spec assumed `aiohttp` for the daemon's HTTP client. But `httpx` (v0.28.1) is already installed as a transitive dependency of multiple packages (anthropic, mcp, openai, huggingface-hub). It supports async via `httpx.AsyncClient()`, has a simpler API, and avoids adding a new dependency. `aiohttp` is NOT installed and would be a new dependency.

3. **`upsert_from_upload` extracted_text signature.** The function's type annotation is `extracted_text: str`, not `str | None`. For binary uploads (NULL full_body), the signature must be updated. This is a simple change but the spec did not call it out explicitly.
