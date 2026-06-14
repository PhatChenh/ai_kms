# Phase 6 Slice A1 — Daemon Core Sync Pipe

_Created: 2026-06-13_
_Updated: 2026-06-13 (source-code-verified assumptions, A2 partially invalidated, A3/A4/A6/A7 verified)_
_Status: SPEC — ready for `/research` then `/plan-from-specs`_
_Behavior ID prefix: **P6-A1-** (22 entries in behavior_inventory.yaml, origin: design)_
_Upstream: design doc `docs/1_design/phase6/P6_slice_A1_daemon_core.md` (4 forks resolved)_
_Audience: Next AI session doing research and planning. Non-coder readable — plain English leads._

---

## Purpose

This phase builds the daemon — a thin local app on the user's Mac that watches their notes folder, extracts text from changed files, and uploads everything to the cloud knowledge base. It also adds one new cloud endpoint so the daemon can ask the cloud "what do you already know?"

After this phase: the user runs `python -m daemon`, points it at their vault folder, and text flows from their laptop to the cloud. New files are captured, modified files are re-captured, and moves/deletes keep the cloud database in sync. The daemon has no AI, no classification, and no local database — it is a pure bridge.

---

## Already built (reuse, do not rebuild)

| Component | Location | What it does | How this spec uses it | Depth |
|-----------|----------|--------------|----------------------|-------|
| Handler Registry | `handlers/registry.py` | Dispatches files by extension to the right text extractor (PDF, DOCX, etc.). First handler whose `can_handle()` returns True wins. | The daemon's Text Extractor calls `HandlerRegistry.resolve(path)` then `handler.extract(path)` to pull text from files. | deep |
| All concrete handlers | `handlers/` (markdown, pdf, docx, xlsx, csv, pptx, html, eml, msg, image) | Each handler reads one file format and returns extracted text. Image handlers return `Failure` (no vision model). | Reused as-is. The daemon imports `handlers/` and gets all registered handlers. | deep |
| `RawContent` dataclass | `handlers/base.py` | Immutable extraction result: `text`, `source_path`, `is_md`. | Text Extractor reads `.text` from successful extractions. | shallow |
| Result type | `core/result.py` | `Success(value)` / `Failure(error, recoverable, context)` pattern for every operation. | Every daemon component returns Result. The only import from `core/` the daemon uses. | deep |
| Upload endpoint | `mcp_server/api.py` `upload_handler` | Accepts JSON with `vault_path`, `extracted_text`, `content_hash` and upserts into the documents table. | Content Uploader sends text uploads here as JSON POST. | deep |
| Event endpoint | `mcp_server/api.py` `event_handler` | Accepts JSON with `type` (moved/deleted) and path(s), updates/removes the documents row. | Event Reporter sends move/delete events here as JSON POST. | deep |
| Secret-key gate | `mcp_server/api.py` `require_key` | Validates `Authorization: Bearer <KMS_DAEMON_API_KEY>` from env. | All daemon HTTP requests use this auth pattern. The same env var name is used on both sides. | shallow |
| `upsert_from_upload` | `storage/documents.py` | Three-way decision: no row = INSERT, same hash = SKIP, different hash = UPDATE. | Called by the upload handler when the daemon sends text. The binary upload handler (new) will use a similar pattern. | deep |
| `delete_by_path` | `storage/documents.py` | Removes a documents row plus its search index entries in one transaction. | Called by the event handler for delete events. | deep |
| `rename` | `storage/documents.py` | Updates vault_path on documents, copies search entries to new path. | Called by the event handler for move events. | deep |
| Starlette route wiring | `mcp_server/api.py` `api_routes`, `health_route` | Lists of Starlette `Route` objects mounted by cloud entry point. | The new GET /api/state route is added to `api_routes`. | shallow |
| watchdog library | `pyproject.toml` `watchdog>=4.0` | Cross-platform filesystem event watcher. | The daemon's File Watcher uses watchdog's `Observer` + `FileSystemEventHandler`, same as the existing vault watcher. Pinned at same version. | deep |
| Existing vault watcher patterns | `vault/watcher.py` | Debounce via `threading.Timer`, NFC Unicode normalization, vault-relative path computation, `_should_skip` pattern. | The daemon's File Watcher is **adapted** from this — keeping debounce/NFC/watchdog, stripping everything vault-specific (binary sync, sibling management, move_guard, frontmatter, indexer). Not a runtime dependency — a design source. | deep |

---

## Q1 Diagram (from design)

The daemon has two operating modes that share the same cloud endpoints and auth.

```
# Daemon Core — What Happens Inside (Q1)
Scope: Shows the two operating modes of the daemon.
       Does NOT cover local cache (Slice A2) or installer (Slice B).

How to read this:
  Boxes  = daemon components
  Arrows = data or event flow
  Left   = startup reconcile path (runs once on boot)
  Right  = live watcher path (runs continuously after boot)

    STARTUP RECONCILE                      LIVE WATCHER
    (runs on boot)                         (runs continuously)
         │                                      │
         ▼                                      ▼
  ┌──────────────┐                    ┌──────────────────┐
  │ Daemon       │                    │ File Watcher     │
  │ Settings     │                    │ Detects creates, │
  │ (load config)│                    │ modifies, moves, │
  └──────┬───────┘                    │ deletes          │
         │                            └────────┬─────────┘
         ▼                                     │
  ┌──────────────────┐                  ┌──────┴──────┐
  │ Startup Scanner  │                  │             │
  │ GET /api/state   │            create/modify   move/delete
  │ Walk vault+hash  │                  │             │
  │ Compare disk     │                  ▼             ▼
  │ vs cloud         │           ┌────────────┐ ┌────────────┐
  └──────┬───────────┘           │ Text       │ │ Event      │
         │                       │ Extractor  │ │ Reporter   │
    ┌────┴────┐                  │ (handlers) │ │ POST       │
    │         │                  └─────┬──────┘ │ /api/event │
new/changed  deleted                   │        └────────────┘
    │         │                        ▼
    ▼         ▼                 ┌────────────┐
┌────────┐ ┌────────┐          │ Content    │
│Content │ │Event   │          │ Uploader   │
│Uploader│ │Reporter│          │ POST       │
│POST    │ │POST    │          │ /api/upload│
│/upload │ │/event  │          └────────────┘
└────────┘ └────────┘
```

---

## Q2 Diagram — How it connects to others

```
# Daemon Core — How It Connects (Q2)
Scope: Shows what the daemon touches externally.
       Does NOT show internal daemon flow (see Q1 for that).

How to read this:
  Center box     = the daemon (being built)
  Solid boxes    = components that already exist
  Dashed boxes   = design heritage (not a runtime link)
  Arrow labels   = what passes between them

           ┌─────────────────────┐
           │ User's Vault Folder │
           │ (files on disk)     │
           └──────────┬──────────┘
                      │ reads file events
                      │ + raw file bytes
                      │
  ┌──────────────┐    │    ┌───────────────────┐
  │ Text         │    │    │ Cloud Web          │
  │ Extractors   │    │    │ Endpoints          │
  │ (shared)     │    │    │ /api/upload (text  │
  │ PDF, DOCX,   ├────┼────┤  + binary)         │
  │ XLSX, etc.   │    │    │ /api/event         │
  └──────────────┘    │    │ /api/state [NEW]   │
   imports handler    │    └─────────┬──────────┘
   registry           │             │
                      │             │ reads/writes
             ┌────────┴────────┐    │
             │    DAEMON       │    │
             │    Watches,     │    ▼
             │    extracts,    │  ┌─────────────────┐
             │    uploads      │  │ Document         │
             └────────┬────────┘  │ Database         │
                      │           │ (cloud-side,     │
                      │           │  daemon never    │
  ┌──────────────┐    │           │  touches it)     │
  │ Result Type  │    │           └──────────────────┘
  │ (shared)     ├────┘
  │ Success /    │
  │ Failure      │
  └──────────────┘
                      │
                      │ design heritage
                      │ (not a runtime link)
                      ▼
            ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
            │ Original File       │
            │ Watcher (adapted    │
            │ from, not imported) │
            └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

Diagram Notes:
- **Text Extractors (shared):** The daemon imports `handlers/` directly. Import boundary enforced by test — daemon must never import `core/config`, `storage/`, `mcp_server/`, `llm/`, or `pipelines/`.
- **Cloud Web Endpoints:** The daemon communicates exclusively over HTTPS. It never touches the database directly. All three endpoints (`/api/upload`, `/api/event`, `/api/state`) use the same Bearer token auth.
- **Result Type (shared):** `core/result.py` is the only module from `core/` that the daemon imports. It has no dependencies on config, database, or AI providers.
- **Original File Watcher:** The daemon's watcher is a simplified rewrite adapted from `vault/watcher.py`. Not imported at runtime — just the source of the design patterns (debounce, NFC normalization, watchdog Observer).

---

## Feature overview

The daemon is a thin Python app that runs on the user's Mac. It does one job: keep the cloud knowledge base fed with the user's vault content. It has no intelligence — no AI calls, no classification, no local database. It watches, extracts, and uploads.

**Happy path — live watching:** The user starts the daemon. It connects to the cloud, confirms it can reach the endpoint, then starts watching the vault folder. When the user creates or edits a file, the daemon extracts the text (using the same PDF/DOCX/XLSX handlers the cloud uses), computes a SHA-256 fingerprint of the raw file bytes, and sends the text plus metadata to the cloud's upload endpoint. When the user moves or deletes a file, the daemon sends a path-change event to the cloud so the database stays in sync.

**Happy path — startup reconcile:** When the daemon boots, it runs a one-time scan before starting the live watcher. It asks the cloud "what do you already know?" via `GET /api/state`, which returns every known document's path and content fingerprint. The daemon then walks the entire vault, hashes each file, and compares:
- File on disk but not in cloud: upload it (new file).
- File on disk with a different hash than the cloud: re-upload it (changed while daemon was off).
- File in cloud but not on disk: report it as deleted.
- File on disk with matching hash: skip it (unchanged).

**Binary fallback:** When the text extractors cannot handle a file (images, unknown formats), the daemon falls back to uploading the raw file bytes via a multipart upload. The cloud stores the metadata (path, hash, filename, size, mime type) but discards the bytes in A1 — actual blob storage is Phase 7's job.

**Failure handling:** Upload failures use exponential backoff with a configurable retry cap (default 3). If all retries fail, the daemon logs the failure and moves on. The file is NOT marked as synced, so the next startup scan (or the periodic reconcile in A2) will catch it. The daemon never crashes on a transient upload failure.

**Ignore patterns:** The daemon skips dotfolders (`.git`, `.obsidian`), OS junk (`.DS_Store`), editor temp files (`~$*`, `*.tmp`), and its own config. Patterns are configurable via the daemon's YAML config.

**A1 limitations (accepted):**
- No local cache — every modify event triggers extraction and upload (no bail-early). Cache comes in A2.
- No hash-based move reconstruction — some file moves may appear as delete+create to the cloud. Real move detection comes in A2.
- No periodic reconcile timer — only startup reconcile. Periodic comes in A2.

---

## Out of scope

- **Local cache and cache-on-ack** — The daemon keeps no state between runs in A1. Every boot does a full disk-vs-cloud comparison. Handled by Slice A2.
- **Live bail-early on modify events** — Without a cache, the daemon cannot compare against a stored hash on modify events. It extracts and uploads every time. Handled by Slice A2.
- **Hash-based move reconstruction** — Detecting that a delete+create pair is really a move requires a buffered-event window and stored hashes. Handled by Slice A2.
- **3-way reconcile (disk+cache+cloud)** — A1's reconcile is 2-way (disk vs cloud). The 3-way compare comes with the cache in A2.
- **Periodic reconcile timer** — A1 only reconciles on boot. A timer-driven periodic reconcile (e.g. every 6 hours) comes in A2.
- **PyInstaller packaging / installer / first-run wizard / tray icon** — Handled by Slice B.
- **Vision-describe for images** — The daemon uploads raw bytes; the cloud describes them with a vision model. Cloud-side vision is Phase 7.
- **Blob storage (binary bytes persistence)** — In A1, the cloud accepts binary uploads but discards the bytes. Blob persistence to Object Storage is Phase 7.
- **Cloud config split** — The cloud still requires a dummy vault root (TD-059). Config split that removes this requirement is deferred to Phase 7/9.

---

## Constraints

- **Import boundary** — The daemon (`src/daemon/`) must NOT import from `core/config`, `storage/`, `mcp_server/`, `llm/`, or `pipelines/`. Only `core/result.py` and `handlers/` are allowed. Enforced by a test that greps daemon source for forbidden imports. Source: design doc Fork 1, mirrors existing `core/pipeline.py` import boundary pattern.
- **Content hash = raw file bytes** — SHA-256 is computed over the file's raw bytes, not extracted text. Source: ADR-0013.
- **Cloud is authority** — The daemon has no local truth. On any disagreement, cloud wins. Source: ADR-0013.
- **Daemon is AI-free** — No LLM calls, no classification, no summarization. Source: rearch doc section 4, Phase 6 grill.
- **System never writes to vault** — The daemon reads files and reports events. It never creates, modifies, or moves user files. Source: rearch doc section 6, ADR-0012.
- **KMS_DAEMON_API_KEY from env** — The API key is read from an environment variable, never from a YAML file. Same env var name on both daemon and cloud sides. Source: Phase 6 grill section 10.
- **Result type for public functions** — Every public function in the daemon returns `Success(value)` or `Failure(error, recoverable, context)`. Source: C-12.
- **Prompts in YAML** — Not directly applicable (daemon has no prompts), but if any user-facing messages are needed, they follow C-07.
- **Same watchdog version** — Pin to `watchdog>=4.0`, matching `pyproject.toml`. Source: design doc open question 2.

---

## Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|-----------|-------------------|--------------------------|
| A1 | No handler in `handlers/` imports from `storage/`, `mcp_server/`, `llm/`, or `pipelines/` at module scope. | Design Fork 1 — daemon imports handlers directly. | `grep -rn "from storage\|from mcp_server\|from llm\|from pipelines" src/handlers/` returns any match at module scope (not inside a function body). |
| A2 | **PARTIALLY INVALIDATED.** `HandlerRegistry.resolve(path)` works without CONFIG (confirmed: `can_handle()` checks only file extension). But `handler.extract(path)` in every handler except MarkdownHandler lazily imports `from core.config import CONFIG` inside the method body to read `CONFIG.main.handlers.max_file_size_bytes`. This triggers the full CONFIG singleton validation (vault root must exist, etc.), which the daemon cannot satisfy. Resolution options: (a) the daemon's extractor wraps handler calls with a pre-set CONFIG stub/override, (b) handlers are refactored to accept `max_file_size_bytes` as a parameter instead of reading CONFIG, (c) the daemon sets up a minimal CONFIG before calling handlers. Research must determine which approach is least invasive. | Design Fork 1 — handlers are stateless. | Confirmed wrong: every handler's `extract()` calls `from core.config import CONFIG` for `max_file_size_bytes`. |
| A3 | **VERIFIED.** `content_hash TEXT` column exists in `storage/schema.sql` (line 10). `upsert_from_upload` writes to it on INSERT (line 210) and UPDATE (line 236). The `all_paths()` function already queries `SELECT vault_path, content_hash FROM documents` (line 299). GET /api/state can reuse this exact query or call `all_paths()` directly. | Design Fork 2 — GET /api/state queries this column. | Confirmed: column exists and is populated. |
| A4 | **PARTIALLY VERIFIED.** `upsert_from_upload` itself does NOT validate `extracted_text` -- it accepts whatever is passed and stores it in `full_body`. The 400 rejection happens in `upload_handler` in `api.py` (line 110-115: `if not extracted_text: return 400`). This means: the function is fine, but the HTTP handler needs to be changed to skip the `extracted_text` check on the binary path (Content-Type dispatch). The function can store NULL `full_body` without issue. | Design Fork 3 — binary upload stores metadata only. | The function itself has a hard check (verified: it does NOT -- the check is in the handler, not the function). |
| A5 | The existing `upload_handler` checks `Content-Type` or can be extended to dispatch between JSON (text) and multipart (binary). | Design Fork 3 — same endpoint, two content types. | Starlette's `Request` object does not expose a clean Content-Type dispatch, or multipart parsing requires a middleware change. |
| A6 | **VERIFIED.** `core/result.py` imports only `traceback` (stdlib) and `core.exceptions.KMSError`. `core/exceptions.py` is 21 lines of pure exception classes with zero imports. No transitive chain to config/DB/AI. Safe for daemon import. | Design Fork 1 — daemon imports `core/result.py` only. | Confirmed safe: no transitive imports. |
| A7 | **VERIFIED.** The existing `event_handler` in `api.py` handles both `"moved"` and `"deleted"` event types. Moved: requires `old_path` + `new_path`, calls `rename(old=old_path, new=new_path)`. Deleted: requires `path`, calls `delete_by_path(vault_path=path)`. No caller-identity assumptions. Field names differ slightly from the daemon's design (`type` vs `event_type` in design doc — the handler uses `body.get("type")`, so daemon must send `"type"`, not `"event_type"`). | Event Reporter design — reuses existing endpoint. | Confirmed working. Note: daemon must send `"type"`, not `"event_type"`. |
| A8 | watchdog 4.0+ provides `FileCreatedEvent`, `FileModifiedEvent`, `FileMovedEvent`, `FileDeletedEvent` with `src_path` and `dest_path` attributes. | Watcher design — adapts from existing vault watcher. | watchdog API changed between the version in use and 4.0+, or macOS FSEvents backend reports events differently than expected. |
| A9 | `RawContent.text` contains the full extracted text suitable for upload to the cloud. | Text Extractor design — reads `.text` from handler output. | A handler returns `RawContent` with only a summary or truncated text, not the full extracted content. |
| A10 | Starlette supports multipart form-data parsing via `request.form()` and file uploads via `request.form()["file"]` without additional dependencies. | Binary upload handler design. | Starlette requires `python-multipart` as an explicit dependency for form parsing. |

---

## Component dependency order

### 1. GET /api/state — cloud state endpoint
**Goal.** Give the daemon a way to ask the cloud "what documents do you already have and what are their content fingerprints?" — the baseline for startup reconcile.

**Build.** Add a new async handler `state_handler` in `mcp_server/api.py`. It validates the bearer token (reusing the existing `require_key` function), queries `SELECT vault_path, content_hash FROM documents`, and returns `{"status": "ok", "documents": [{vault_path, content_hash}, ...]}`. Add the route to `api_routes` as `GET /api/state`. Empty database returns an empty list, not an error.

**Depends on.** None — this is the only cloud-side change and has no daemon dependency.

**Assumes.** A3.

**Interface shape.** Callers see: `GET /api/state` with Bearer auth, returns JSON list. Implementation hidden: single SELECT query, same `_db_path` injection pattern as other handlers.

**Dependency category.** remote-owned (daemon tests will mock the HTTP response; cloud tests hit the handler directly).

**Done when.** A valid bearer token to `GET /api/state` returns 200 with a JSON list of `{vault_path, content_hash}` objects matching every row in the documents table. An invalid/missing token returns 401. An empty database returns an empty list, not an error. (P6-A1-01, P6-A1-02, P6-A1-03)

---

### 2. Binary upload path — multipart on /api/upload
**Goal.** Let the daemon upload raw file bytes for files it cannot extract text from (images, unknown formats), so the cloud knows about them even before Phase 7 adds vision-describe.

**Build.** Extend `upload_handler` in `mcp_server/api.py` to dispatch on `Content-Type`:
- `application/json` — existing text path (unchanged).
- `multipart/form-data` — new binary path. Reads the file bytes from the multipart form field and a JSON metadata part containing `vault_path`, `content_hash`, `original_filename`, `file_size_bytes`, `mime_type`. Calls a new or adapted `upsert_from_upload` variant that stores the metadata row WITHOUT requiring `extracted_text`. The raw bytes are accepted but discarded in A1 (blob persistence is Phase 7).

**Depends on.** None (can be built in parallel with Component 1).

**Assumes.** A4, A5, A10.

**Decisions.**
- Q: Should the binary upload reuse the same `upsert_from_upload` function (with `extracted_text` made optional) or have its own `upsert_from_binary_upload`? Options: A — make `extracted_text` optional in existing function / B — new function. Leaning A because the three-way decision (insert/skip/update) is identical; only the columns populated differ.
- Q: Does Starlette need `python-multipart` as an explicit dependency? Options: A — yes, add to pyproject.toml / B — it's already bundled. Research must verify (A10).

**Done when.** A multipart POST to `/api/upload` with file bytes and JSON metadata creates a documents row with `vault_path`, `content_hash`, `original_filename`, `file_size_bytes`, and NULL `full_body`. Re-uploading with the same hash is a no-op. Missing auth returns 401. (P6-A1-04, P6-A1-05, P6-A1-06)

---

### 3. Daemon Settings — standalone config
**Goal.** Give the daemon its own configuration that loads independently from the cloud config, so starting the daemon never triggers cloud config validation.

**Build.** Create `src/daemon/config.py` with a `DaemonConfig` Pydantic `BaseModel`. Fields: `vault_root` (Path, validated to exist on disk), `cloud_endpoint` (str, non-empty), `api_key` (str, from env `KMS_DAEMON_API_KEY`, not in YAML), `debounce_seconds` (float, default 1.0), `ignore_patterns` (list of str, sensible defaults: `.git`, `.obsidian`, `.trash`, `.stversions`, `.DS_Store`, `Thumbs.db`, `~$*`, `*.tmp`, `*.swp`, `.~lock*`), `upload_concurrency` (int, default 4), `retry_max` (int, default 3), `scan_batch_size` (int, default 50). Add a `load_daemon_config(path: Path | None = None)` function that reads a YAML file (default `~/.kms-daemon/config.yaml`), overrides `api_key` from env, and validates.

**Depends on.** None.

**Interface shape.** Callers see: `load_daemon_config()` returns a `DaemonConfig` object. All fields are validated. `api_key` is never in the YAML file.

**Dependency category.** in-process (test directly with tmp paths and env overrides).

**Done when.** Constructing `DaemonConfig` with a nonexistent `vault_root` raises `ValidationError`. Constructing with an empty `cloud_endpoint` raises `ValidationError`. A valid config with `KMS_DAEMON_API_KEY` set in env succeeds and populates `api_key` from env, not YAML. (P6-A1-07, P6-A1-08)

---

### 4. File Watcher — simplified vault watcher
**Goal.** Detect file creates, modifies, moves, and deletes in the vault folder and emit clean events for downstream processing.

**Build.** Create `src/daemon/watcher.py`. Adapt from `vault/watcher.py` — keep: watchdog `Observer` + `FileSystemEventHandler`, `threading.Timer` debounce, NFC Unicode normalization for vault-relative path computation. Strip: all binary-sync callbacks, sibling management, move_guard checks, `_should_skip` for `.summaries/` and managed attachment dirs, all imports from `vault/`, `storage/`, `core/config`. Add: config-driven ignore patterns (gitignore-style matching against `DaemonConfig.ignore_patterns`). Events emitted: `on_file_created(vault_relative_path)`, `on_file_modified(vault_relative_path)`, `on_file_moved(old_vault_path, new_vault_path)`, `on_file_deleted(vault_relative_path)`. All paths are vault-relative POSIX strings with NFC normalization.

**Depends on.** Component 3 (Daemon Settings — for ignore patterns and vault root).

**Assumes.** A8.

**Interface shape.** Callers see: `DaemonWatcher(config: DaemonConfig, on_create, on_modify, on_move, on_delete)` with `start()`, `stop()`, `join()`. Callbacks receive vault-relative string paths. Implementation hidden: watchdog observer, debounce timers, NFC normalization, ignore filtering.

**Dependency category.** in-process (test with real temp directory and watchdog events).

**Done when.** Creating a file in the vault triggers `on_file_created` with the correct vault-relative path. Modifying triggers `on_file_modified`. Moving triggers `on_file_moved` with old and new paths. Deleting triggers `on_file_deleted`. Files matching ignore patterns (e.g. `.DS_Store`, `.git/`) do NOT trigger any callback. Debounce coalesces rapid events on the same file. (P6-A1-09, P6-A1-10, P6-A1-11, P6-A1-12, P6-A1-13)

---

### 5. Text Extractor — extraction + hashing
**Goal.** Given a file path, extract its text content and compute a SHA-256 fingerprint of the raw bytes. If text extraction fails, prepare raw bytes for binary upload.

**Build.** Create `src/daemon/extractor.py`. The main function takes an absolute file path and returns either `TextContent(text, content_hash, vault_path, original_filename, file_size_bytes)` or `BinaryContent(raw_bytes, content_hash, vault_path, original_filename, file_size_bytes, mime_type)`. Logic: read the file's raw bytes once, compute SHA-256 hash. Then try `HandlerRegistry.resolve(path)` — if a handler is found and `handler.extract(path)` returns `Success(raw_content)`, return `TextContent` with the extracted text. If no handler or extraction fails, return `BinaryContent` with the raw bytes. File metadata (size, original filename) comes from `os.stat` and `path.name`. MIME type comes from Python's `mimetypes` module.

**Depends on.** Component 3 (Daemon Settings — for vault root to compute relative path). Uses `handlers/` and `core/result.py` (already built).

**Assumes.** A1, A2 (PARTIALLY INVALIDATED — see Assumptions table), A6 (VERIFIED), A9.

**Critical: handler CONFIG dependency.** Every handler except MarkdownHandler lazily imports `from core.config import CONFIG` inside `extract()` to read `CONFIG.main.handlers.max_file_size_bytes`. This means calling `handler.extract(path)` from the daemon will trigger CONFIG singleton validation (vault root exists, provider keys, etc.), which the daemon cannot and should not satisfy. The extractor must solve this before handlers can be reused. Three resolution paths (research must pick one):
- **(a) Inject a minimal CONFIG before calling handlers.** Set up a lightweight CONFIG with only `handlers.max_file_size_bytes` populated and vault root pointing at the real vault. Risk: CONFIG validation may require other fields (provider keys, etc.).
- **(b) Refactor handlers to accept `max_file_size_bytes` as a parameter.** Clean but touches every handler file + existing callers. Higher scope.
- **(c) Daemon sets `max_file_size_bytes` as a DaemonConfig field and monkey-patches or wraps handler.extract().** Least invasive but fragile.

**Interface shape.** Callers see: `extract(path: Path, vault_root: Path) -> TextContent | BinaryContent`. Implementation hidden: handler dispatch, byte reading, hashing, mime detection.

**Dependency category.** in-process (test with real temp files and the handler registry).

**Done when.** A `.pdf` file produces `TextContent` with extracted text and a SHA-256 hash matching the raw bytes. A `.png` file produces `BinaryContent` with raw bytes and the correct mime type. The hash is over raw bytes, not extracted text. File metadata (size, filename) is correct. (P6-A1-14, P6-A1-15, P6-A1-20)

---

### 6. Content Uploader — HTTPS upload with retry
**Goal.** Send extracted text (as JSON) or raw file bytes (as multipart) to the cloud's upload endpoint, with retry logic for transient failures.

**Build.** Create `src/daemon/uploader.py`. Async module using `aiohttp`. Two upload functions:
- `upload_text(session, config, content: TextContent)` — POST JSON to `/api/upload` with `vault_path`, `extracted_text`, `content_hash`, `original_filename`, `file_size_bytes`, `title`. Bearer auth from `config.api_key`.
- `upload_binary(session, config, content: BinaryContent)` — POST multipart to `/api/upload` with file bytes in a form field and JSON metadata in another field. Bearer auth.
Both use exponential backoff retry (base 1s, max `config.retry_max` attempts). Concurrency capped by `asyncio.Semaphore(config.upload_concurrency)`. Returns `Success(document_id)` or `Failure(error)`. Failure is logged but never crashes the daemon.

**Depends on.** Component 3 (Daemon Settings — for endpoint URL, API key, retry/concurrency config). Component 5 (Text Extractor — produces the content objects).

**Assumes.** A7.

**Interface shape.** Callers see: `upload_text(session, config, content)` and `upload_binary(session, config, content)`. Returns `Result[int]` (document_id). Implementation hidden: retry loop, backoff timing, auth header construction, JSON/multipart encoding.

**Dependency category.** remote-owned (test with mock HTTP server or `aiohttp` test utilities).

**Decisions.**
- Q: Should `aiohttp` be a daemon-only dependency or added to the main `pyproject.toml`? Options: A — add to main pyproject.toml (simpler, one package) / B — separate daemon dependencies. Leaning A because the daemon lives in `src/daemon/` within the same package root.

**Done when.** A text upload sends correct JSON to `/api/upload` with Bearer auth and receives a document_id. A binary upload sends multipart with file bytes and metadata. A 500 response triggers retry with backoff up to `retry_max` times. After exhausting retries, the function returns `Failure` but does not raise. The semaphore limits concurrent uploads to `upload_concurrency`. (P6-A1-16, P6-A1-21)

---

### 7. Event Reporter — move/delete to cloud
**Goal.** Report file move and delete events to the cloud so the database's `vault_path` entries stay in sync with the user's actual file locations.

**Build.** Create `src/daemon/event_reporter.py`. Async module. Two functions:
- `report_moved(session, config, old_path, new_path)` — POST JSON `{"type": "moved", "old_path": old, "new_path": new}` to `/api/event` with Bearer auth.
- `report_deleted(session, config, path)` — POST JSON `{"type": "deleted", "path": path}` to `/api/event` with Bearer auth.
Same retry logic as Content Uploader (shared helper or base class). Returns `Result[None]`.

**Depends on.** Component 3 (Daemon Settings — for endpoint URL, API key, retry config).

**Interface shape.** Callers see: `report_moved(session, config, old, new)` and `report_deleted(session, config, path)`. Returns `Result[None]`. Implementation hidden: JSON encoding, auth, retry.

**Dependency category.** remote-owned (test with mock HTTP).

**Done when.** A file move produces a POST to `/api/event` with `{"type": "moved", "old_path": "...", "new_path": "..."}` using vault-relative paths. A delete produces `{"type": "deleted", "path": "..."}`. Both include Bearer auth. Retry logic handles transient failures. (P6-A1-17)

---

### 8. Startup Scanner — disk-vs-cloud reconcile
**Goal.** On daemon boot, compare the vault's current state against what the cloud knows, and upload/report any differences so the cloud is fully up to date.

**Build.** Create `src/daemon/scanner.py`. Async function `scan(config: DaemonConfig, session: aiohttp.ClientSession)`. Steps:
1. GET `/api/state` to fetch the cloud manifest: a dict of `{vault_path: content_hash}`.
2. Walk the vault directory tree, applying ignore patterns. For each file, compute SHA-256 of raw bytes.
3. Compare:
   - On disk, not in cloud: extract text (via Component 5) and upload (via Component 6).
   - On disk, in cloud, hash differs: re-extract and re-upload.
   - In cloud, not on disk: report deleted (via Component 7).
   - Both match: skip.
4. Batch uploads with concurrency cap from config (`scan_batch_size` controls how many files to process before yielding, `upload_concurrency` caps parallel uploads).
5. Returns a summary: counts of uploaded, re-uploaded, deleted, and skipped files.

**Depends on.** Component 1 (GET /api/state), Component 3 (Daemon Settings), Component 5 (Text Extractor), Component 6 (Content Uploader), Component 7 (Event Reporter).

**Interface shape.** Callers see: `scan(config, session) -> ScanResult(uploaded, re_uploaded, deleted, skipped)`. Implementation hidden: vault walking, hashing, batching, cloud state parsing.

**Dependency category.** Combination — in-process for vault walking, remote-owned for cloud calls (mock HTTP).

**Done when.** Starting the daemon with files on disk that the cloud does not know about results in those files being uploaded. Files that changed while the daemon was off are re-uploaded (different hash). Files the cloud has but disk does not are reported as deleted. Files with matching hashes are skipped. The scanner applies ignore patterns during the walk. (P6-A1-18, P6-A1-19)

---

### 9. Daemon Command Line — CLI entry points
**Goal.** Give the user three commands to control the daemon: start it, run a one-shot scan, or check connectivity.

**Build.** Create `src/daemon/cli.py` using Click (matching the existing `kms` CLI pattern). Three commands:
- `start` — Load config, run startup scanner, then start the live watcher. Runs in the foreground (Ctrl+C to stop). The async main loop runs the scanner first, then starts the watcher in a thread and enters an asyncio event loop for the uploader/reporter.
- `scan` — Load config, run startup scanner only (one-shot), print summary, exit.
- `status` — Load config, try GET `/health` on the cloud endpoint, report success/failure.

Create `src/daemon/__main__.py` as the entry point for `python -m daemon`.

**Depends on.** All previous components (scanner, watcher, uploader, event reporter, extractor, config).

**Interface shape.** User sees: `python -m daemon start`, `python -m daemon scan`, `python -m daemon status`. Implementation hidden: asyncio loop management, watcher thread lifecycle, graceful shutdown on SIGINT.

**Dependency category.** in-process (integration test with temp vault and mock HTTP).

**Decisions.**
- Q: Should the daemon use `structlog` (matching cloud code) or stdlib `logging`? Options: A — structlog (consistency, structured output for debugging sync issues) / B — stdlib logging (simpler, smaller dep). Leaning A for consistency with the rest of the codebase. Research should confirm structlog has no transitive imports that violate the daemon's import boundary.

**Done when.** Running `python -m daemon start` loads config, runs the startup scanner, starts watching for live changes, and uploads a newly dropped file to the cloud within the debounce window. `python -m daemon scan` runs a one-shot reconcile and exits. `python -m daemon status` reports whether the cloud endpoint is reachable. (P6-A1-22)

---

### 10. Import boundary test
**Goal.** Enforce that the daemon package never imports forbidden modules, preventing accidental coupling with cloud-only code.

**Build.** Create a test that greps all `.py` files under `src/daemon/` for imports from `core/config`, `storage/`, `mcp_server/`, `llm/`, `pipelines/`, and `vault/`. Only `core/result` and `handlers/` are allowed from outside `daemon/`. This mirrors the existing pattern where `core/pipeline.py` cannot import from `vault.` (enforced by `test_pipeline_has_no_heavy_imports`).

**Depends on.** All daemon modules must exist (Components 3-9).

**Done when.** The test passes with the daemon package as built. Adding a `from storage.documents import upsert` to any daemon module causes the test to fail.

---

## Behavior inventory reference

All 22 behaviors are pre-defined in `docs/system_behavior/behavior_inventory.yaml` with IDs P6-A1-01 through P6-A1-22. The mapping to components:

| Behavior ID | Component | Summary |
|-------------|-----------|---------|
| P6-A1-01 | 1. GET /api/state | Returns document list with vault_path + content_hash |
| P6-A1-02 | 1. GET /api/state | Rejects unauthenticated requests with 401 |
| P6-A1-03 | 1. GET /api/state | Empty database returns empty list, not error |
| P6-A1-04 | 2. Binary upload | Accepts raw bytes without requiring extracted_text |
| P6-A1-05 | 2. Binary upload | Idempotent — re-upload same hash is a no-op |
| P6-A1-06 | 2. Binary upload | Rejects unauthenticated requests with 401 |
| P6-A1-07 | 3. Daemon Settings | Validates vault root exists and endpoint URL is present |
| P6-A1-08 | 3. Daemon Settings | Reads API key from env, not YAML |
| P6-A1-09 | 4. File Watcher | New file triggers upload |
| P6-A1-10 | 4. File Watcher | Modified file triggers re-upload |
| P6-A1-11 | 4. File Watcher | Deleted file triggers delete event |
| P6-A1-12 | 4. File Watcher | Moved file triggers move event |
| P6-A1-13 | 4. File Watcher | Ignores dotfolders, OS junk, editor temps |
| P6-A1-14 | 5. Text Extractor | Reuses handler registry for supported formats |
| P6-A1-15 | 5. Text Extractor | Falls back to binary upload on extraction failure |
| P6-A1-16 | 6. Content Uploader | Authenticates with Bearer token |
| P6-A1-17 | 7. Event Reporter | Sends moved event with old_path and new_path |
| P6-A1-18 | 8. Startup Scanner | Uploads new/changed files after disk-vs-cloud compare |
| P6-A1-19 | 8. Startup Scanner | Reports cloud-has-but-disk-missing as deleted |
| P6-A1-20 | 5. Text Extractor | Hash is over raw bytes, not extracted text |
| P6-A1-21 | 6. Content Uploader | Transient failure does not crash daemon |
| P6-A1-22 | 9. Daemon CLI | End-to-end: start daemon, drop file, text flows to cloud |

---

## Handoff notes

- **Contract with Phase 6 Slice A2:** A1 delivers a working stateless pipe. A2 adds the local cache (manifest of `vault_path -> content_hash`), cache-on-ack (write to cache only after cloud returns 200), live bail-early (compare against cache on modify events), 3-way reconcile (disk + cache + cloud), hash-based move reconstruction, and periodic reconcile timer. A2 does NOT change the cloud endpoints or the daemon's external interface — it layers efficiency on top of A1's pipe.

- **Contract with Phase 7:** A1's binary upload sends raw bytes that the cloud accepts and discards. Phase 7 must: (a) persist the bytes to VNG Object Storage keyed by content_hash, (b) store a blob reference on the documents row, (c) run a vision model to produce a searchable description. The binary upload metadata shape (vault_path, content_hash, original_filename, file_size_bytes, mime_type) is the contract between A1 and Phase 7 — Phase 7 should not need to change the daemon's upload format.

- **Open uncertainty: `python-multipart` dependency.** Starlette's multipart form parsing may require `python-multipart` as an explicit dependency (not bundled). Research must verify and, if needed, add it to `pyproject.toml`. See assumption A10.

- **Open uncertainty: `aiohttp` as new dependency.** The daemon needs an async HTTP client for concurrent uploads. `aiohttp` is the natural choice but it is a new dependency. Research should confirm it has no conflicts with existing deps and no unwanted transitive imports. Alternative: `httpx` (already familiar from Ollama provider patterns, though that uses `requests`).

- **Open uncertainty: structlog transitive imports.** The daemon should use `structlog` for consistency, but research must confirm that importing `structlog` does not transitively pull in any module that violates the daemon's import boundary (no `core/config`, no `storage/`, etc.). `structlog` itself is lightweight, but the project's `core/logging_setup.py` wires structlog with the CONFIG singleton — the daemon must configure structlog independently.

- **CRITICAL research: handler CONFIG dependency (A2 invalidated).** Every handler except MarkdownHandler lazily imports `from core.config import CONFIG` inside `extract()` for `max_file_size_bytes`. Calling `handler.extract()` from the daemon triggers CONFIG validation (vault root, provider keys, etc.). Research must: (1) inventory exactly which CONFIG fields are accessed by each handler's `extract()` (confirmed: only `CONFIG.main.handlers.max_file_size_bytes` for all checked handlers — pdf, docx, xlsx, csv, html, eml, msg, pptx), (2) determine the least-invasive resolution path (see Component 5 Decisions), (3) confirm MarkdownHandler truly has no CONFIG dependency (preliminary check: clean — no CONFIG import found).

- **Suggested research: A4 is partially verified.** `upsert_from_upload` function accepts NULL `full_body` fine. The 400 check is in `upload_handler` (api.py line 110-115), not in the function. Research should confirm no downstream consumer (search indexing in `retrieval/embeddings.py`, `retrieval/keyword.py`) breaks on NULL `full_body`. Trace: does capture-pipeline search indexing run on upload-path rows, or only on vault-captured rows?

- **Suggested research:** Verify assumption A10 — does Starlette need `python-multipart`? Check the import chain and test a multipart upload in a minimal Starlette app.

- **Suggested research:** Check whether `aiohttp` or `httpx` is the better fit for the daemon's HTTP client. Consider: async support, retry library integration, dependency weight, existing project patterns.

- **Suggested research: event field naming.** The design doc uses `"event_type"` in some places but the existing `event_handler` in `api.py` reads `body.get("type")`. The daemon's Event Reporter MUST send `"type"`, not `"event_type"`. Verify no other field-name mismatches exist between the daemon's planned payloads and the cloud handlers' expectations.

- **Daemon package __init__.py note:** The `src/daemon/__init__.py` must be empty or minimal. The daemon is a standalone app, not a library consumed by other modules. No exports needed — the entry point is `__main__.py`.

- **Doc sweep (deferred to pipeline end):** After A1 ships, update CONSTRAINTS.md (new daemon import-boundary constraint), CLAUDE.md (daemon package layout, gotchas), STATE.md (Phase 6 A1 completion), and CONTEXT.md (daemon glossary entries).
