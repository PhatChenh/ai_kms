# Plan: Phase 6 Slice A1 — Daemon Core Sync Pipe

_Last updated: 2026-06-13_
_Status: [ ] pending_

**Spec:** `docs/2_specs/phase6/P6_slice_A1_daemon_core.md`
**Research:** `docs/3_research/phase6/P6_slice_A1_daemon_core.md`
**Design:** `docs/1_design/phase6/P6_slice_A1_daemon_core.md`
**Behavior IDs:** P6-A1-01 through P6-A1-22

---

## Architecture

### Q1 — What happens inside

See design doc `docs/1_design/phase6/P6_slice_A1_daemon_core.md` — Q1 diagram showing two operating modes (startup reconcile + live watcher) sharing uploader, extractor, and event reporter.

### Q2 — How it connects

See spec doc `docs/2_specs/phase6/P6_slice_A1_daemon_core.md` — Q2 diagram showing daemon's external touchpoints: vault folder (reads), cloud endpoints (HTTP), handler registry (import), Result type (import), original watcher (design heritage only).

### Q3 — Why build it this way

```
# Daemon Core — Why Build It This Way (Q3)
Scope: Shows which existing code/patterns each new component must conform to.
       Rationale for key design choices annotated on Q1/Q2 boundaries.

How to read this:
  Solid boxes   = new code this plan builds
  Dashed boxes  = existing code reused or adapted
  Annotations   = constraints / why this shape was chosen

                             EXISTING (reuse)
                    ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
                    │  handlers/registry.py            │
                    │  + all concrete handlers          │
                    │  (PDF, DOCX, XLSX, CSV, PPTX,    │
                    │   HTML, EML, MSG, Markdown, Image)│
                    │                                   │
                    │  WHY reuse: handlers already      │
                    │  extract text from every format.  │
                    │  Duplication = drift.              │
                    │                                   │
                    │  CONSTRAINT: extract() lazily     │
                    │  imports CONFIG for max_file_size. │
                    │  FIX: add max_file_size_bytes     │
                    │  param to extract() so daemon     │
                    │  can bypass CONFIG entirely.       │
                    └ ─ ─ ─ ─ ─ ─ ┬ ─ ─ ─ ─ ─ ─ ─ ─ ┘
                                  │ handler.extract(path,
                                  │   max_file_size_bytes=50MB)
                                  │
  NEW: src/daemon/                │           EXISTING: mcp_server/api.py
  ┌───────────────────────────────┼──────────────────────────────────────┐
  │                               ▼                                      │
  │  ┌──────────────┐    ┌────────────────┐    ┌─────────────────────┐  │
  │  │ DaemonConfig │    │ Text Extractor │    │ Content Uploader    │  │
  │  │              │    │ (extractor.py) │    │ (uploader.py)       │  │
  │  │ WHY standalone:│   │                │───▶│                     │  │
  │  │ importing     │    │ WHY wraps      │    │ WHY httpx not       │──┼──▶ POST /api/upload
  │  │ core/config   │    │ handlers/:     │    │ aiohttp: already    │  │    (JSON or multipart)
  │  │ triggers full │    │ extract text   │    │ installed, lighter   │  │
  │  │ CONFIG valid- │    │ + SHA-256 hash │    │                     │  │    Auth: Bearer
  │  │ ation (vault  │    │ in one read    │    │ WHY retry+backoff:  │  │    KMS_DAEMON_API_KEY
  │  │ root, API     │    │                │    │ transient cloud     │  │
  │  │ keys) which   │    │ WHY binary     │    │ failures must not   │  │
  │  │ daemon cannot │    │ fallback: not  │    │ crash the daemon    │  │
  │  │ satisfy.      │    │ all files have │    │ (P6-A1-21)          │  │
  │  │ (Design F4)   │    │ text (images)  │    └─────────────────────┘  │
  │  └──────┬────────┘    └────────────────┘                             │
  │         │                                                             │
  │         │    ┌──────────────────┐    ┌───────────────────────┐       │
  │         │    │ File Watcher     │    │ Event Reporter        │       │
  │         │    │ (watcher.py)     │    │ (event_reporter.py)   │       │
  │         │    │                  │    │                       │───────┼──▶ POST /api/event
  │         │    │ WHY adapt from   │    │ WHY shares retry      │       │    {"type": "moved/deleted"}
  │         │    │ vault/watcher:   │    │ with uploader:        │       │
  │         │    │ proven debounce  │    │ same transient-error  │       │    Auth: Bearer
  │         │    │ + NFC + watchdog │    │ resilience needed     │       │    KMS_DAEMON_API_KEY
  │         │    │ patterns. Strip  │    │                       │       │
  │         │    │ vault-specific   │    └───────────────────────┘       │
  │         │    │ logic (sibling   │                                    │
  │         │    │ sync, move_guard,│    ┌───────────────────────┐       │
  │         │    │ frontmatter).    │    │ Startup Scanner       │       │
  │         │    │ (Design F1)      │    │ (scanner.py)          │───────┼──▶ GET /api/state [NEW]
  │         │    └──────────────────┘    │                       │       │
  │         │                            │ WHY disk-vs-cloud     │       │
  │         │    ┌──────────────────┐    │ only (no cache):      │       │
  │         │    │ CLI (cli.py)     │    │ A1 is stateless.      │       │
  │         └───▶│ + __main__.py    │    │ Cache comes in A2.    │       │
  │              │                  │    │ (Design OQ-1)         │       │
  │              │ WHY Click:       │    └───────────────────────┘       │
  │              │ matches existing │                                    │
  │              │ kms CLI pattern  │                                    │
  │              └──────────────────┘                                    │
  └──────────────────────────────────────────────────────────────────────┘

  EXISTING (import boundary)
  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  │  ALLOWED imports from daemon:                                       │
  │    ✅ core/result.py (Success/Failure — zero deps)                  │
  │    ✅ handlers/ (text extractors — safe at import time)             │
  │                                                                     │
  │  FORBIDDEN imports (enforced by test, Phase 9):                     │
  │    ❌ core/config   (triggers full CONFIG validation)               │
  │    ❌ storage/      (daemon never touches DB)                       │
  │    ❌ mcp_server/   (cloud-only)                                    │
  │    ❌ llm/          (daemon is AI-free)                             │
  │    ❌ pipelines/    (cloud-only processing)                         │
  │    ❌ vault/        (daemon adapts patterns, does not import)       │
  │                                                                     │
  │  NOTE: handlers/markdown_handler.py imports vault.reader at module  │
  │  scope. This is a TRANSITIVE import through handlers/, not a direct │
  │  daemon import. The boundary test checks daemon source files only.  │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

### Extension points

| Component | Extensibility | Mechanism |
|-----------|--------------|-----------|
| DaemonConfig | `[extensible: config]` | New fields added to Pydantic model + YAML |
| File Watcher | `[extensible: config]` | Ignore patterns via YAML list |
| Text Extractor | `[extensible: registry]` | New file formats via handler registry (add handler file, register, done) |
| Content Uploader | `[closed]` | Two upload paths (text JSON, binary multipart) — new content types would require code change. Acceptable: only two content types exist. |
| Event Reporter | `[closed]` | Two event types (moved, deleted) — new events would require code change. Acceptable: these are the only filesystem events that matter. |
| Startup Scanner | `[closed]` | Reconcile logic is fixed (4-way compare). Cache-aware reconcile replaces this in A2, not extends it. |
| CLI | `[extensible: config]` | New Click commands added as functions — follows existing `kms` CLI pattern. |
| GET /api/state | `[closed]` | Single query, single response shape. Pagination would require structural change (deferred — personal vault is small). |

---

## Approach

Build cloud-side changes first (they have zero daemon dependency and unblock all daemon testing), then refactor handlers to accept `max_file_size_bytes` as a parameter (unblocks daemon's text extractor without CONFIG), then build daemon components bottom-up: config, watcher, extractor, uploader/reporter, scanner, CLI. Each phase is independently testable with its own commit. The daemon communicates exclusively over HTTPS — it never touches the database.

Research resolved all open dependency questions: use `httpx` (already installed) not `aiohttp` (new dep), add `python-multipart` explicitly to `pyproject.toml`, change `upsert_from_upload` signature to `extracted_text: str | None = None`, and use `"type"` not `"event_type"` in event payloads.

---

## Phases

### Phase 1 — Cloud endpoints: GET /api/state + binary upload + upsert signature fix

**Goal**: Give the daemon two cloud capabilities it needs — asking "what do you know?" and uploading binary files — plus fix the `upsert_from_upload` signature to accept NULL extracted_text.

**Behavior IDs**: P6-A1-01, P6-A1-02, P6-A1-03, P6-A1-04, P6-A1-05, P6-A1-06

**Design**:

This phase adds one new endpoint and extends one existing endpoint, all in `mcp_server/api.py`. The pattern mirrors the existing `upload_handler` and `event_handler`: async Starlette handler, `require_key` auth gate, JSON response.

```
BEFORE (api.py has 2 routes):
  POST /api/upload  → JSON text only, requires extracted_text
  POST /api/event   → JSON move/delete

AFTER (api.py has 3 routes):
  POST /api/upload  → JSON text (unchanged) OR multipart binary (NEW)
  POST /api/event   → JSON move/delete (unchanged)
  GET  /api/state   → returns [{vault_path, content_hash}, ...] (NEW)

  upsert_from_upload signature: extracted_text: str | None = None (FIX)
```

**Steps**:

1. **Fix `upsert_from_upload` signature** (`storage/documents.py:156`). Change `extracted_text: str` to `extracted_text: str | None = None`. The function already passes the value through to SQL without validation — `full_body` column is nullable. No downstream consumers break (search indexing is separate, called by capture pipeline not by this function).

2. **Add `python-multipart` to `pyproject.toml`**. Add `"python-multipart>=0.0.20"` to dependencies. Currently installed as transitive dep of `mcp` (v0.0.32), but must be explicit for binary upload multipart parsing.

3. **Add `state_handler`** in `api.py`. New async handler: validate bearer token via `require_key`, call `all_paths(db_path=_db_path)`, return `{"status": "ok", "documents": [{"vault_path": vp, "content_hash": ch} for vp, ch in rows]}`. Empty database returns empty list. Add route to `api_routes`: `Route("/api/state", endpoint=state_handler, methods=["GET"])`.

4. **Extend `upload_handler` for binary path**. Check `Content-Type` header:
   - `application/json` — existing text path (unchanged, still requires `extracted_text`).
   - `multipart/form-data` — new binary path. Parse form via `request.form()`. Read metadata fields: `vault_path`, `content_hash`, `original_filename`, `file_size_bytes`, `mime_type`. Call `upsert_from_upload(vault_path=..., extracted_text=None, content_hash=..., original_filename=..., file_size_bytes=...)`. Accept file bytes from form but discard them in A1 (blob storage is Phase 7). Return same `{"status": "ok", "document_id": ...}` shape.

**Files to modify**:
- `src/storage/documents.py` — `upsert_from_upload` signature: `extracted_text: str | None = None`
- `src/mcp_server/api.py` — add `state_handler`, extend `upload_handler` for multipart, add `/api/state` to `api_routes`
- `pyproject.toml` — add `python-multipart>=0.0.20`

**Test criteria**:
- [ ] `GET /api/state` with valid bearer token returns 200 + JSON list of `{vault_path, content_hash}` matching all documents rows
- [ ] `GET /api/state` with no/bad token returns 401
- [ ] `GET /api/state` on empty database returns `{"status": "ok", "documents": []}`
- [ ] Existing JSON `POST /api/upload` still works identically (no regression)
- [ ] Multipart `POST /api/upload` with file bytes + metadata creates a documents row with NULL `full_body`
- [ ] Multipart re-upload with same `content_hash` is a no-op (returns existing id)
- [ ] Multipart upload with no/bad token returns 401
- [ ] `upsert_from_upload(extracted_text=None)` inserts a row with NULL `full_body`

**Status**: [ ] pending

---

### Phase 2 — Handler CONFIG refactor: add `max_file_size_bytes` parameter to `extract()`

**Goal**: Let the daemon call `handler.extract(path, max_file_size_bytes=50_000_000)` without triggering the CONFIG singleton, which requires vault root and provider keys that the daemon does not have.

**Behavior IDs**: P6-A1-14 (enables handler reuse from daemon)

**Design**:

Every handler except MarkdownHandler and image handlers lazily imports `from core.config import CONFIG` inside `extract()` to read exactly one field: `CONFIG.main.handlers.max_file_size_bytes`. The fix adds an optional parameter to `extract()`. When the parameter is provided, skip the CONFIG import. When it is `None` (default), fall back to the existing CONFIG import — existing callers are unaffected.

```
BEFORE (8 handlers):
  def extract(self, path: Path) -> Result[RawContent]:
      from core.config import CONFIG
      max_bytes = CONFIG.main.handlers.max_file_size_bytes
      ...

AFTER (8 handlers):
  def extract(self, path: Path, *, max_file_size_bytes: int | None = None) -> Result[RawContent]:
      if max_file_size_bytes is None:
          from core.config import CONFIG
          max_bytes = CONFIG.main.handlers.max_file_size_bytes
      else:
          max_bytes = max_file_size_bytes
      ...
```

**Steps**:

1. **Update `BaseHandler.extract()` ABC signature** (`handlers/base.py:81`). Add `*, max_file_size_bytes: int | None = None` keyword-only parameter after `path`.

2. **Update each concrete handler's `extract()` method** to accept the new parameter and use the sentinel pattern. Touch each of these files once with the same mechanical change:
   - `handlers/pdf_handler.py`
   - `handlers/docx_handler.py`
   - `handlers/xlsx_handler.py`
   - `handlers/csv_handler.py`
   - `handlers/pptx_handler.py`
   - `handlers/html_handler.py`
   - `handlers/eml_handler.py`
   - `handlers/msg_handler.py`

3. **Update MarkdownHandler** (`handlers/markdown_handler.py`). MarkdownHandler has no CONFIG import, but its `extract()` signature must match the ABC. Add the parameter (unused — MarkdownHandler does not check file size).

4. **Update image handlers** (`handlers/image_handler.py`). Add the parameter to PngHandler and JpgHandler (unused — they return Failure immediately). Signature must match ABC.

5. **Verify existing callers are unaffected**. All existing calls are `handler.extract(path)` — the new parameter defaults to `None`, which triggers the CONFIG import as before. No existing call sites need changes.

**Files to modify**:
- `src/handlers/base.py` — ABC signature
- `src/handlers/pdf_handler.py` — sentinel pattern
- `src/handlers/docx_handler.py` — sentinel pattern
- `src/handlers/xlsx_handler.py` — sentinel pattern
- `src/handlers/csv_handler.py` — sentinel pattern
- `src/handlers/pptx_handler.py` — sentinel pattern
- `src/handlers/html_handler.py` — sentinel pattern
- `src/handlers/eml_handler.py` — sentinel pattern
- `src/handlers/msg_handler.py` — sentinel pattern
- `src/handlers/markdown_handler.py` — signature match only
- `src/handlers/image_handler.py` — signature match only

**Test criteria**:
- [ ] `handler.extract(path)` (no param) still works — existing tests pass with no changes
- [ ] `handler.extract(path, max_file_size_bytes=50_000_000)` works without importing CONFIG
- [ ] A file larger than the passed `max_file_size_bytes` is rejected with `Failure`
- [ ] Full existing test suite passes (no regressions)

**Status**: [ ] pending

---

### Phase 3 — DaemonConfig: standalone daemon configuration

**Goal**: Give the daemon its own configuration that loads independently from the cloud config, so starting the daemon never triggers cloud config validation.

**Behavior IDs**: P6-A1-07, P6-A1-08

**Design**:

A standalone Pydantic model in `src/daemon/config.py`. Zero imports from `core/config`. The API key comes from the environment variable `KMS_DAEMON_API_KEY`, never from YAML.

```
DaemonConfig fields:
  vault_root: Path          # @field_validator: must exist on disk
  cloud_endpoint: str       # must be non-empty
  api_key: str              # from env KMS_DAEMON_API_KEY (not in YAML)
  debounce_seconds: float   # default 1.0
  ignore_patterns: list[str]  # default: [".git", ".obsidian", ...]
  upload_concurrency: int   # default 4
  retry_max: int            # default 3
  scan_batch_size: int      # default 50
  max_file_size_bytes: int  # default 50_000_000 (50 MB, matches cloud default)

load_daemon_config(path: Path | None = None) -> DaemonConfig
  1. Read YAML (default: ~/.kms-daemon/config.yaml)
  2. Override api_key from env KMS_DAEMON_API_KEY
  3. Validate
```

**Steps**:

1. **Create `src/daemon/` package**. Create `src/daemon/__init__.py` (empty).

2. **Create `src/daemon/config.py`**. Define `DaemonConfig(BaseModel)` with all fields listed above. Add `@field_validator("vault_root")` that checks `path.exists()`. Add `load_daemon_config(path: Path | None = None) -> DaemonConfig` that reads YAML, injects `api_key` from `os.environ["KMS_DAEMON_API_KEY"]`, and constructs the model. Raise `ValueError` if `KMS_DAEMON_API_KEY` is not set in env.

3. **Add default ignore patterns**. The default list: `[".git", ".obsidian", ".trash", ".stversions", ".DS_Store", "Thumbs.db", "~$*", "*.tmp", "*.swp", ".~lock*"]`. Glob-style patterns for matching against path components.

**Files to modify**:
- `src/daemon/__init__.py` — create (empty)
- `src/daemon/config.py` — create

**Test criteria**:
- [ ] `DaemonConfig(vault_root=Path("/nonexistent"), ...)` raises `ValidationError`
- [ ] `DaemonConfig(cloud_endpoint="", ...)` raises `ValidationError`
- [ ] With `KMS_DAEMON_API_KEY` set in env, `load_daemon_config(path)` populates `api_key` from env, not from YAML
- [ ] Without `KMS_DAEMON_API_KEY` in env, `load_daemon_config` raises
- [ ] Default `ignore_patterns` contains `.git`, `.obsidian`, `.DS_Store`
- [ ] `max_file_size_bytes` defaults to 50_000_000

**Status**: [ ] pending

---

### Phase 4 — File Watcher: simplified vault watcher for daemon

**Goal**: Detect file creates, modifies, moves, and deletes in the vault folder and emit clean events for downstream processing. No vault-specific logic — pure filesystem events with ignore patterns and debounce.

**Behavior IDs**: P6-A1-09, P6-A1-10, P6-A1-11, P6-A1-12, P6-A1-13

**Design**:

Adapted from `vault/watcher.py` — keeping the battle-tested watchdog + debounce + NFC patterns, stripping all vault-specific logic (binary sync, sibling management, move_guard, frontmatter, indexer). The daemon's watcher is NOT a runtime dependency on `vault/watcher.py` — it is a design-heritage rewrite.

```
DaemonWatcher(config: DaemonConfig,
              on_create: Callable[[str], None],
              on_modify: Callable[[str], None],
              on_move: Callable[[str, str], None],
              on_delete: Callable[[str], None])

  .start()  → starts watchdog Observer in a thread
  .stop()   → stops Observer
  .join()   → waits for Observer thread

Internal:
  _DaemonEventHandler(FileSystemEventHandler)
    - _should_skip(path) → matches against config.ignore_patterns
    - _to_vault_path(abs_path) → NFC-normalized POSIX relative to vault_root
    - _debounce(key, callback, delay) → threading.Timer pattern

Events:
  on_created  → skip dirs, skip ignored → debounce → on_create(vault_path)
  on_modified → skip dirs, skip ignored → debounce → on_modify(vault_path)
  on_moved    → skip dirs, skip both paths → on_move(old_vp, new_vp)
  on_deleted  → skip dirs, skip ignored → on_delete(vault_path)
```

**Steps**:

1. **Create `src/daemon/watcher.py`**. Implement `DaemonWatcher` class and `_DaemonEventHandler` inner class. Use `watchdog.observers.Observer` and `watchdog.events.FileSystemEventHandler`. Import only from `daemon.config` and stdlib.

2. **Implement ignore pattern matching**. For each file event, check `_should_skip(path)` against `config.ignore_patterns`. Match against individual path components (not full path) for dotfolder patterns like `.git`. Use `fnmatch.fnmatch` for glob patterns like `~$*` and `*.tmp`.

3. **Implement debounce**. Use `threading.Timer` with a `threading.Lock`-guarded dict of pending timers, keyed by vault-relative path. Cancel existing timer before scheduling a new one. Debounce delay from `config.debounce_seconds`.

4. **Implement NFC normalization**. Use `unicodedata.normalize("NFC", str(path.relative_to(vault_root).as_posix()))` for all vault-relative path computation. This matches the existing vault watcher pattern.

5. **Skip directory events**. All four handlers check `event.is_directory` and return early if True. The daemon cares only about file events.

**Files to modify**:
- `src/daemon/watcher.py` — create

**Test criteria**:
- [ ] Creating a file in the vault triggers `on_create` with correct vault-relative path
- [ ] Modifying a file triggers `on_modify`
- [ ] Moving a file triggers `on_move` with old and new vault-relative paths
- [ ] Deleting a file triggers `on_delete`
- [ ] Files matching `.DS_Store` do NOT trigger any callback
- [ ] Files inside `.git/` do NOT trigger any callback
- [ ] Files matching `~$*` pattern do NOT trigger any callback
- [ ] Rapid edits to the same file produce only one callback (debounce)
- [ ] Directory create/modify/delete/move events are ignored
- [ ] Vault-relative paths are NFC-normalized POSIX strings

**Status**: [ ] pending

---

### Phase 5 — Text Extractor: extraction + hashing

**Goal**: Given a file path, extract its text content using the handler registry and compute a SHA-256 fingerprint of the raw file bytes. When extraction fails, prepare binary content for fallback upload.

**Behavior IDs**: P6-A1-14, P6-A1-15, P6-A1-20

**Design**:

The extractor reads the file's raw bytes once, computes the SHA-256 hash, then tries text extraction via the handler registry. This ensures the hash is always over raw bytes (not extracted text) per ADR-0013.

```
extract(path: Path, vault_root: Path, max_file_size_bytes: int)
  → Result[TextContent | BinaryContent]

  1. Read raw bytes → compute SHA-256
  2. Compute vault_path = NFC relative POSIX path
  3. Try HandlerRegistry.resolve(path)
     3a. Found + extract(path, max_file_size_bytes=N) → Success
         → TextContent(text, content_hash, vault_path, filename, size)
     3b. Not found OR extract fails
         → BinaryContent(content_hash, vault_path, filename, size, mime_type)

@dataclass(frozen=True)
class TextContent:
    text: str
    content_hash: str
    vault_path: str
    original_filename: str
    file_size_bytes: int

@dataclass(frozen=True)
class BinaryContent:
    raw_bytes: bytes
    content_hash: str
    vault_path: str
    original_filename: str
    file_size_bytes: int
    mime_type: str
```

**Steps**:

1. **Create `src/daemon/extractor.py`**. Define `TextContent` and `BinaryContent` frozen dataclasses. Define `extract(path: Path, vault_root: Path, max_file_size_bytes: int) -> Result[TextContent | BinaryContent]`.

2. **Implement the extraction logic**. Read raw bytes once via `path.read_bytes()`. Compute `hashlib.sha256(raw_bytes).hexdigest()`. Get `vault_path` via NFC-normalized POSIX relative to `vault_root`. Get `original_filename` from `path.name`, `file_size_bytes` from `len(raw_bytes)`.

3. **Try handler registry dispatch**. Import `from handlers.registry import HandlerRegistry`. Call `HandlerRegistry.resolve(path)`. If `Success(handler)`, call `handler.extract(path, max_file_size_bytes=max_file_size_bytes)`. If that returns `Success(raw_content)`, return `Success(TextContent(..., text=raw_content.text))`.

4. **Binary fallback**. If no handler found OR extraction fails, get `mime_type` from `mimetypes.guess_type(str(path))[0] or "application/octet-stream"`. Return `Success(BinaryContent(...))`.

5. **Error handling**. If `path.read_bytes()` fails (file vanished, permission denied), return `Failure(recoverable=True)`.

**Files to modify**:
- `src/daemon/extractor.py` — create

**Test criteria**:
- [ ] A `.pdf` file returns `TextContent` with extracted text and SHA-256 hash of raw bytes
- [ ] A `.png` file returns `BinaryContent` with correct mime type `image/png`
- [ ] The content hash is over raw bytes (not extracted text) — verify by comparing `hashlib.sha256(raw_bytes).hexdigest()` against `result.content_hash`
- [ ] `vault_path` is a NFC-normalized POSIX string relative to `vault_root`
- [ ] `original_filename` matches `path.name`
- [ ] `file_size_bytes` matches actual file size
- [ ] A vanished file returns `Failure(recoverable=True)`
- [ ] Unknown file extension returns `BinaryContent` with `application/octet-stream`

**Status**: [ ] pending

---

### Phase 6 — Content Uploader + Event Reporter: HTTP uploads with retry

**Goal**: Send extracted text or binary content to the cloud upload endpoint, and report move/delete events to the cloud event endpoint. Both with exponential backoff retry.

**Behavior IDs**: P6-A1-16, P6-A1-17, P6-A1-21

**Design**:

Two async modules sharing a retry helper. Uses `httpx.AsyncClient` (already installed as transitive dep). The uploader has two paths: JSON for text, multipart for binary. The event reporter sends JSON for moved/deleted events. The field name for event type is `"type"` (not `"event_type"`) — verified against `api.py:182`.

```
# uploader.py
async upload_text(client, config, content: TextContent) → Result[int]
  POST /api/upload (JSON):
    {"vault_path": ..., "extracted_text": ..., "content_hash": ...,
     "original_filename": ..., "file_size_bytes": ..., "title": ...}

async upload_binary(client, config, content: BinaryContent) → Result[int]
  POST /api/upload (multipart):
    file: raw_bytes
    metadata: {vault_path, content_hash, original_filename, file_size_bytes, mime_type}

# event_reporter.py
async report_moved(client, config, old_path, new_path) → Result[None]
  POST /api/event: {"type": "moved", "old_path": ..., "new_path": ...}

async report_deleted(client, config, path) → Result[None]
  POST /api/event: {"type": "deleted", "path": ...}

# Shared retry: exponential backoff, base 1s, max config.retry_max attempts
# Concurrency: asyncio.Semaphore(config.upload_concurrency)
```

**Steps**:

1. **Create `src/daemon/uploader.py`**. Define `upload_text(client: httpx.AsyncClient, config: DaemonConfig, content: TextContent) -> Result[int]` and `upload_binary(client: httpx.AsyncClient, config: DaemonConfig, content: BinaryContent) -> Result[int]`. Each constructs the appropriate request body, adds `Authorization: Bearer {config.api_key}` header, and POSTs to `{config.cloud_endpoint}/api/upload`.

2. **Implement retry with exponential backoff**. Create a shared async retry helper (private function or shared module). Base delay 1 second, multiplied by 2 on each retry. Max `config.retry_max` attempts. On transient failures (HTTP 500, 502, 503, connection error), retry. On 4xx errors, fail immediately (bad request, not transient). After exhausting retries, return `Failure(recoverable=True)`.

3. **Create `src/daemon/event_reporter.py`**. Define `report_moved(client, config, old_path: str, new_path: str) -> Result[None]` and `report_deleted(client, config, path: str) -> Result[None]`. Send JSON with `"type"` field (not `"event_type"`). Same retry logic.

4. **Add `httpx` to `pyproject.toml`**. Add `"httpx>=0.27"` to dependencies. Currently installed as transitive dep of `anthropic`/`mcp`/`openai` but must be explicit for daemon use.

**Files to modify**:
- `src/daemon/uploader.py` — create
- `src/daemon/event_reporter.py` — create
- `pyproject.toml` — add `httpx>=0.27`

**Test criteria**:
- [ ] `upload_text` sends correct JSON body to `/api/upload` with Bearer auth header
- [ ] `upload_binary` sends multipart with file bytes and metadata fields
- [ ] A 500 response triggers retry up to `retry_max` times with exponential backoff
- [ ] A 401 response fails immediately (not retried — auth error is not transient)
- [ ] After exhausting retries, function returns `Failure` (does not raise)
- [ ] `report_moved` sends `{"type": "moved", "old_path": "...", "new_path": "..."}`
- [ ] `report_deleted` sends `{"type": "deleted", "path": "..."}`
- [ ] Both reporter functions include Bearer auth
- [ ] Test with `httpx` mock transport or `respx` library

**Status**: [ ] pending

---

### Phase 7 — Startup Scanner: disk-vs-cloud reconcile

**Goal**: On daemon boot, compare the vault's current state against what the cloud knows, and upload/report any differences so the cloud is fully up to date.

**Behavior IDs**: P6-A1-18, P6-A1-19

**Design**:

The scanner fetches the cloud manifest via `GET /api/state`, walks the vault directory, hashes each file, and executes a 4-way compare. It orchestrates the extractor, uploader, and event reporter.

```
async scan(config: DaemonConfig, client: httpx.AsyncClient) → ScanResult

  1. GET /api/state → cloud_manifest: dict[vault_path, content_hash]
  2. Walk vault (applying ignore patterns)
  3. For each file:
     - Hash raw bytes
     - vault_path = NFC relative POSIX
  4. Compare:
     disk-only          → extract + upload  (new file)
     disk+cloud, diff   → extract + upload  (changed file)
     cloud-only         → report_deleted    (offline deletion)
     disk+cloud, match  → skip              (unchanged)
  5. Batch with asyncio.Semaphore(config.upload_concurrency)

@dataclass
class ScanResult:
    uploaded: int
    re_uploaded: int
    deleted: int
    skipped: int
```

**Steps**:

1. **Create `src/daemon/scanner.py`**. Define `ScanResult` dataclass and `scan(config: DaemonConfig, client: httpx.AsyncClient) -> ScanResult`.

2. **Fetch cloud state**. GET `{config.cloud_endpoint}/api/state` with Bearer auth. Parse response as `{"documents": [{"vault_path": ..., "content_hash": ...}, ...]}`. Build a dict: `cloud_state = {doc["vault_path"]: doc["content_hash"] for doc in response["documents"]}`. Handle NULL `content_hash` in cloud (treat as "always re-upload").

3. **Walk vault**. Use `os.walk` with ignore-pattern filtering (reuse same `_should_skip` logic from watcher, or factor into a shared helper in `daemon/config.py` or a small `daemon/_ignore.py`). For each file, compute SHA-256 hash, compute vault-relative path with NFC normalization.

4. **Compare and act**. Build `disk_state = {vault_path: content_hash}` from the walk. Then:
   - For each `vault_path` in `disk_state` but not in `cloud_state`: extract + upload (count `uploaded`).
   - For each `vault_path` in both but `hash` differs: extract + upload (count `re_uploaded`).
   - For each `vault_path` in `cloud_state` but not in `disk_state`: `report_deleted` (count `deleted`).
   - All others: count `skipped`.

5. **Concurrency control**. Use `asyncio.Semaphore(config.upload_concurrency)` and `asyncio.gather` (or batched gather in groups of `config.scan_batch_size`) for parallel uploads.

**Files to modify**:
- `src/daemon/scanner.py` — create

**Test criteria**:
- [ ] A file on disk but not in cloud state is uploaded (counted as `uploaded`)
- [ ] A file on disk with different hash than cloud is re-uploaded (counted as `re_uploaded`)
- [ ] A file in cloud but not on disk is reported as deleted (counted as `deleted`)
- [ ] A file with matching hash is skipped (counted as `skipped`)
- [ ] Ignore patterns are applied during the walk (e.g., `.git/` files not uploaded)
- [ ] NULL `content_hash` from cloud is treated as "always re-upload"
- [ ] Scanner respects `upload_concurrency` (semaphore limits parallel uploads)
- [ ] Test with mock HTTP responses for `/api/state` and `/api/upload`

**Status**: [ ] pending

---

### Phase 8 — CLI + integration: daemon entry points

**Goal**: Give the user three commands to run the daemon: start (watch + upload), scan (one-shot reconcile), and status (connectivity check). Wire everything together into an end-to-end flow.

**Behavior IDs**: P6-A1-22

**Design**:

Click CLI matching the existing `kms` pattern. The daemon uses `structlog` for logging (research confirmed no transitive import boundary violations). The async main loop runs the scanner first, then starts the watcher in a thread and enters an asyncio event loop for uploads/reports.

```
python -m daemon start [--config PATH]
  → load config → startup scanner → live watcher → Ctrl+C to stop

python -m daemon scan [--config PATH]
  → load config → startup scanner → print summary → exit

python -m daemon status [--config PATH]
  → load config → GET /health → report success/failure
```

**Steps**:

1. **Create `src/daemon/__main__.py`**. Entry point: `from daemon.cli import cli; cli()`.

2. **Create `src/daemon/cli.py`**. Use Click. Define group `cli` with three commands: `start`, `scan`, `status`. All share a `--config` option (default: `~/.kms-daemon/config.yaml`).

3. **Implement `start` command**. Load config, create `httpx.AsyncClient`, run `scan()`, then create `DaemonWatcher` with callbacks that schedule async upload/report tasks. The watcher runs in its own thread; the main thread runs `asyncio.run()` for the async event loop. Handle `KeyboardInterrupt` (Ctrl+C) for graceful shutdown: stop watcher, close HTTP client.

4. **Implement `scan` command**. Load config, create `httpx.AsyncClient`, run `scan()` via `asyncio.run()`, print `ScanResult` summary, exit.

5. **Implement `status` command**. Load config, GET `{config.cloud_endpoint}/health`, report success/failure.

6. **Configure structlog independently**. Do NOT import `core/logging_setup.py` (it wires with CONFIG singleton). Set up structlog directly with `structlog.configure(...)` using a simple processor chain. Import `structlog` only (no core/ imports).

**Files to modify**:
- `src/daemon/__main__.py` — create
- `src/daemon/cli.py` — create

**Test criteria**:
- [ ] `python -m daemon status --config <path>` reports cloud endpoint reachability
- [ ] `python -m daemon scan --config <path>` runs one-shot reconcile and prints summary
- [ ] `python -m daemon start --config <path>` runs scanner then starts live watcher
- [ ] Dropping a file during `start` triggers upload within debounce window
- [ ] Ctrl+C (SIGINT) during `start` performs graceful shutdown (watcher stopped, client closed)
- [ ] The daemon does NOT import `core/config` (no CONFIG singleton triggered)
- [ ] structlog is configured independently (not via `core/logging_setup.py`)

**Status**: [ ] pending

---

### Phase 9 — Import boundary test

**Goal**: Enforce that the daemon package never directly imports forbidden modules, preventing accidental coupling with cloud-only code.

**Behavior IDs**: (enforcement, no behavior ID — cross-cutting constraint)

**Design**:

A test that greps all `.py` files under `src/daemon/` for direct imports from forbidden modules. Only `core/result` and `handlers/` are allowed from outside `daemon/`. Transitive imports through `handlers/` (like `vault.reader` via MarkdownHandler) are NOT checked — the test verifies daemon source files only, not the full import graph.

```
Test logic:
  For each .py file in src/daemon/:
    Read file text
    Check for:
      ❌ "from core.config" or "import core.config"
      ❌ "from storage" or "import storage"
      ❌ "from mcp_server" or "import mcp_server"
      ❌ "from llm" or "import llm"
      ❌ "from pipelines" or "import pipelines"
      ❌ "from vault" or "import vault"
    Allowed:
      ✅ "from core.result" or "from core.exceptions"
      ✅ "from handlers" or "import handlers"
      ✅ "from daemon" or "import daemon"
      ✅ stdlib, third-party
```

This mirrors the existing `test_pipeline_has_no_heavy_imports` pattern.

**Steps**:

1. **Create `tests/test_daemon/test_import_boundary.py`**. Write a test that globs `src/daemon/**/*.py`, reads each file, and asserts no forbidden imports exist.

2. **Define forbidden patterns**. Regex or string match for `from core.config`, `from storage`, `from mcp_server`, `from llm`, `from pipelines`, `from vault`. Exclude lines under `TYPE_CHECKING` blocks.

3. **Verify the test catches violations**. Add a docstring noting: to validate this test works, temporarily add `from storage.documents import upsert` to any daemon module and confirm the test fails.

**Files to modify**:
- `tests/test_daemon/__init__.py` — create (empty)
- `tests/test_daemon/test_import_boundary.py` — create

**Test criteria**:
- [ ] Test passes with daemon package as built (all phases 3-8)
- [ ] Adding `from storage.documents import upsert` to any daemon module causes the test to fail
- [ ] Adding `from vault.reader import read_note` to any daemon module causes the test to fail
- [ ] `from core.result import Success` in a daemon module does NOT cause the test to fail
- [ ] `from handlers.registry import HandlerRegistry` does NOT cause the test to fail

**Status**: [ ] pending

---

## Open Questions

1. **Should ignore-pattern logic be shared between watcher and scanner?** Both need to skip the same files. Options: (A) factor into a small `daemon/_ignore.py` helper, or (B) duplicate the check in both. Recommendation: (A) — factor into a shared helper to avoid drift. The implementer should decide the exact location.

2. **Watcher-to-async bridge pattern.** The watcher runs in a thread (watchdog requirement) but the uploader/reporter are async. The CLI `start` command needs to bridge: watcher callbacks → async task scheduling. Options: (A) use `asyncio.run_coroutine_threadsafe(coro, loop)` from the watcher thread, (B) use a `queue.Queue` that the async loop drains. Recommendation: (A) — simpler, and the pattern is well-documented.

3. **Should `structlog` be used for daemon logging?** Research confirmed no import boundary violations. Recommendation: yes, use structlog for consistency. Configure it independently in `daemon/cli.py`, not via `core/logging_setup.py`.

---

## Out of Scope

- **Local cache and cache-on-ack** — Every boot does a full disk-vs-cloud comparison. Handled by Slice A2.
- **Hash-based move reconstruction** — Some moves appear as delete+create. Handled by Slice A2.
- **Periodic reconcile timer** — Only startup reconcile. Timer comes in A2.
- **PyInstaller packaging / installer / tray icon** — Handled by Slice B.
- **Vision-describe for images** — Cloud-side, Phase 7.
- **Blob storage persistence** — Cloud accepts and discards binary bytes in A1. Phase 7.
- **Cloud config split** — Cloud still requires dummy vault root (TD-059). Deferred.
- **Test for `handlers/markdown_handler.py` importing `vault.reader`** — This is a transitive import through `handlers/`, not a direct daemon import. The import boundary test (Phase 9) checks daemon source files only. If this transitive import becomes problematic, it should be addressed as a separate handler refactor, not in A1.
