# Phase 6 Slice A1 — Daemon Core Sync Pipe: Design Document

_Created: 2026-06-13_
_Status: DESIGN — locked decisions from Phase -1 grill + ADR-0013 + ADR-0015. Resolves 4 design forks._
_Behavior ID prefix: **P6-A1-** (22 entries in behavior_inventory.yaml, origin: design)_
_Audience: Next AI session doing spec/research/plan work. Non-coder readable — plain English leads._

---

## What this is

The daemon is a thin app on the user's Mac that watches their notes folder and keeps the cloud knowledge base fed. It has no intelligence — it watches files, pulls text out of them, and uploads the text (or raw bytes for images) to the cloud. This document decides **how** to build Slice A1: the stateless core pipe (no local cache yet).

**Locked inputs** (do not re-open):
- Phase -1 grill: `docs/0_draft/phase6/phase6_daemon_grill.md`
- ADR-0013: hybrid cache model, cloud authority, scanner = reconcile, hash = raw bytes
- ADR-0015: daemon uploads raw bytes for no-text files, cloud stores blob + vision-describes later

---

## Scope reminder — A1 only

**In scope:** GET /api/state cloud endpoint, binary-upload path on /api/upload, DaemonConfig, vault watcher (simplified), text extractor (reusing handlers/), uploader, event reporter, startup scanner (disk↔cloud, no cache).

**Out of A1:** local cache, cache-on-ack, live bail-early, 3-way/periodic reconcile (A2); installer/PyInstaller/wizard (B); vision-describe + blob storage (Phase 7/9).

---

## Design Fork 1: Daemon Package Layout

**The question:** The daemon needs the text extractors (PDF→text, DOCX→text, etc.) that already exist in `src/handlers/`. But it must NOT pull in cloud dependencies (database, AI providers, MCP server, capture pipeline). How do we share the handler code cleanly?

### Options considered

| Option | How it works | Pros | Cons |
|--------|-------------|------|------|
| **A — daemon/ inside src/, import handlers/ directly (Recommended)** | New `src/daemon/` package. Imports `from handlers.base import HandlerRegistry`. Strict rule: daemon/ never imports from core/config, storage/, mcp_server/, llm/, pipelines/. | Zero code duplication. Handlers already have minimal deps (just core/result.py). Same package root = clean imports. | Must enforce import boundary by convention + test. If a handler secretly imports a heavy module, daemon inherits it. |
| B — Copy handlers/ into daemon/ | Duplicate all handler files into a daemon-local copy. | Complete isolation. No risk of accidental coupling. | Two copies that drift. Bug fixes must be applied twice. Violates DRY. |
| C — Extract handlers/ to a shared package | Move handlers/ out of src/ into a separate `shared/` or `libs/handlers/` package. Both daemon and cloud import it. | Clean dependency tree on paper. | Restructures existing code for a problem that doesn't exist yet. Breaks current imports in capture pipeline. Over-engineering for A1. |

**Decision: Option A.** The daemon lives at `src/daemon/` and imports handlers via `from handlers.base import HandlerRegistry`. The import boundary (daemon must not import core/config, storage/, mcp_server/, llm/, pipelines/) is enforced by a test that greps daemon source for forbidden imports — same pattern the project already uses for other boundaries (e.g., `core/pipeline.py` cannot import from `vault.`).

**What the handlers need from core/:** Only `core/result.py` (the Result type). This is a lightweight, dependency-free module — no config loading, no DB, no AI. Safe to import.

**Research must verify:** That no handler in `src/handlers/` imports from storage/, mcp_server/, llm/, or pipelines/. If any does, that import must be refactored out before the daemon can reuse it.

### Proposed daemon package layout

```
src/daemon/
├── __init__.py
├── __main__.py          ← entry point: python -m daemon
├── config.py            ← DaemonConfig (standalone Pydantic)
├── watcher.py           ← simplified vault watcher (from vault/watcher.py)
├── extractor.py         ← text extraction orchestrator (calls handlers/)
├── uploader.py          ← HTTPS upload to cloud (text + binary)
├── event_reporter.py    ← reports moves/deletes to cloud
├── scanner.py           ← startup reconcile (disk↔cloud via GET /api/state)
└── cli.py               ← Click CLI (start, scan, status)
```

---

## Design Fork 2: GET /api/state — Cloud State Listing

**The question:** The startup scanner needs to know what the cloud already has. The new `GET /api/state` endpoint returns `[{vault_path, content_hash}]` for every document. How should this query work?

### Options considered

| Option | How it works | Pros | Cons |
|--------|-------------|------|------|
| **A — Single SELECT, full JSON list (Recommended)** | `SELECT vault_path, content_hash FROM documents` → return entire list as JSON. | Simplest. One round-trip. A personal vault of 1–5K files = 200KB–1MB JSON — trivial for HTTPS. | Won't scale to 100K+ files. Not needed for personal use. |
| B — Paginated with cursor | Return N items per page with a cursor token. Client pages through. | Handles arbitrarily large vaults. | Adds pagination logic to both client and server for a problem that doesn't exist (personal vault). A1 complexity for zero benefit. |
| C — Streaming NDJSON | Return one JSON object per line, streamed. | Memory-efficient for very large responses. | Complex client parsing. Overkill for <5K entries. |

**Decision: Option A.** One SELECT, full list. The `content_hash` column already exists in the documents table (added by P5 Slice 2's `upsert_from_upload`). The query is trivial:

- Implementation goes in `mcp_server/api.py` alongside existing `/api/upload`, `/api/event`, `/health`
- Same `KMS_DAEMON_API_KEY` bearer gate (existing `_check_secret` pattern)
- Returns `{"status": "ok", "documents": [{vault_path, content_hash}, ...]}`

**Research must verify:** That `content_hash` column exists in the documents table schema and is populated by `upsert_from_upload`.

---

## Design Fork 3: Binary-Upload Payload Shape

**The question:** ADR-0015 says the daemon uploads raw bytes for files with no usable text (images, graphs). The existing `/api/upload` accepts JSON with `extracted_text`. How should the binary path work?

### Options considered

| Option | How it works | Pros | Cons |
|--------|-------------|------|------|
| **A — Same /api/upload, multipart for binary (Recommended)** | Text uploads keep existing JSON body. Binary uploads use multipart/form-data with file bytes + JSON metadata part. Server dispatches based on Content-Type header. | Non-breaking for existing text path. One URL, one auth gate. Standard HTTP for file upload. | Server must handle two content types. Slightly more complex handler. |
| B — Base64-encoded JSON | Binary bytes encoded as base64 string in a JSON body alongside metadata. | Same Content-Type as text uploads. Simpler server dispatch. | ~33% payload overhead. A 5MB image becomes 6.7MB. Wasteful for binary that's already large. |
| C — Separate /api/upload-binary route | Dedicated endpoint for binary uploads. | Clean separation. Each endpoint handles one shape. | Route proliferation. Two auth gates to maintain. Two URLs to configure. |
| D — Multipart for BOTH text and binary | Standardize all uploads as multipart. Text goes in a text field, binary goes in a file field. | Uniform upload format. | Breaking change to existing text upload contract. Phase 7 capture pipeline uses the JSON path. |

**Decision: Option A.** The server checks the `Content-Type` header:
- `application/json` → existing text path (extracted_text + metadata)
- `multipart/form-data` → new binary path (file bytes + JSON metadata part)

**Binary upload metadata** (sent as a JSON part in multipart):
```
{
  "vault_path": "Projects/Alpha/attachment/chart.png",
  "content_hash": "<sha256-of-raw-bytes>",
  "original_filename": "chart.png",
  "file_size_bytes": 234567,
  "mime_type": "image/png"
}
```

**What the cloud does with binary in A1:** Accepts and stores the metadata in the documents table (vault_path, content_hash, filename, size). The raw bytes are held temporarily or passed to Phase 7's blob storage wiring. A1 ensures the plumbing works; the cloud's handling of the bytes is Phase 7's concern.

---

## Design Fork 4: DaemonConfig Validation

**The question:** The daemon needs its own config: vault root, API key, endpoint URL, ignore patterns, etc. Should it share anything with the existing cloud config system?

### Options considered

| Option | How it works | Pros | Cons |
|--------|-------------|------|------|
| **A — Fully standalone DaemonConfig (Recommended)** | New Pydantic model in `src/daemon/config.py`. Loads its own YAML file. Reads KMS_DAEMON_API_KEY from env. Zero imports from core/config. | Complete isolation. No risk of triggering CONFIG singleton validation. Simple to understand. Grill explicitly said "cloud config does not apply." | Tiny duplication of vault-root-exists validator (~3 lines). |
| B — Share base validators from core/ | Extract vault-root-exists check to core/validators.py. Both DaemonConfig and VaultConfig import it. | DRY for one validator. | Creates coupling between daemon and cloud config modules. core/validators.py becomes a shared dependency to coordinate. The shared code is 3 lines. |
| C — Inherit from VaultConfig | DaemonConfig extends VaultConfig, adds daemon-specific fields. | Reuses vault validation. | Imports core/config.py → triggers CONFIG singleton. The two configs have almost nothing in common beyond vault root. |

**Decision: Option A.** DaemonConfig is fully standalone. It is a simple Pydantic model:

```python
class DaemonConfig(BaseModel):
    vault_root: Path              # validated: must exist on disk
    cloud_endpoint: str           # e.g., "https://your-agent.agentbase.vngcloud.vn"
    api_key: str                  # from env KMS_DAEMON_API_KEY (not in YAML)
    debounce_seconds: float       # default 1.0
    ignore_patterns: list[str]    # default: [".git", ".obsidian", ".trash", ...]
    upload_concurrency: int       # default 4
    retry_max: int                # default 3
    scan_batch_size: int          # default 50
```

**Loading order:**
1. Read YAML file (default: `~/.kms-daemon/config.yaml`)
2. Override `api_key` from env `KMS_DAEMON_API_KEY` (required, fails if missing)
3. Validate vault_root exists on disk (Pydantic validator)

---

## Component Design Summary

### GET /api/state (cloud-side, build first)

- **Where:** `mcp_server/api.py` — add alongside existing routes
- **Auth:** Same `_check_secret` bearer gate as `/api/upload` and `/api/event`
- **Query:** `SELECT vault_path, content_hash FROM documents`
- **Response:** `{"status": "ok", "documents": [{"vault_path": "...", "content_hash": "..."}, ...]}`
- **Acceptance:** Valid key → 200 + full document list. No key → 401.

### Vault Watcher (daemon-side, adapted from vault/watcher.py)

- **Keep:** watchdog observer, debounce timer pattern, NFC Unicode normalization, vault-relative path computation
- **Strip:** binary-sync callbacks (`_handle_binary_delete`, `_handle_binary_move`), sibling management (`_sibling_for`), move_guard checks, `_should_skip` for `.summaries/`/managed dirs, all imports from vault/writer, vault/reader, vault/frontmatter, vault/indexer, vault/move_guard
- **Add:** config-driven ignore patterns (gitignore-style matching against `DaemonConfig.ignore_patterns`)
- **Events emitted:** `created(path)`, `modified(path)`, `moved(old, new)`, `deleted(path)`
- **No hash comparison in A1** (no cache for bail-early — that's A2). Every modify event triggers extraction + upload.

### Text Extractor (daemon-side, wraps handlers/)

- **Input:** absolute file path from watcher event
- **Output:** `ExtractedContent(text, content_hash, filename, size, metadata)` or `BinaryContent(raw_bytes, content_hash, filename, size, mime_type)`
- **Hash:** SHA-256 over raw file bytes (read once, hash, then extract)
- **Logic:** Try HandlerRegistry → handler found and extraction succeeds → ExtractedContent. Handler not found OR extraction fails → read raw bytes → BinaryContent.
- **Metadata:** filesystem (ctime, mtime from os.stat) + format-specific (PDF author/title, DOCX properties — whatever the handler already extracts)

### Uploader (daemon-side, new)

- **Input:** ExtractedContent (text path) or BinaryContent (binary path)
- **Endpoint:** POST `/api/upload` with Bearer KMS_DAEMON_API_KEY
- **Text path:** JSON body `{"vault_path": "...", "extracted_text": "...", "content_hash": "...", ...}`
- **Binary path:** multipart/form-data with file bytes + JSON metadata part
- **Retry:** exponential backoff, max `DaemonConfig.retry_max` attempts
- **Concurrency:** asyncio.Semaphore capped at `DaemonConfig.upload_concurrency`
- **Failure:** log locally, do not cache (reconcile catches it on next boot)

### Event Reporter (daemon-side, new)

- **Input:** move(old_path, new_path) or delete(path) from watcher
- **Endpoint:** POST `/api/event` with Bearer KMS_DAEMON_API_KEY
- **Body:** `{"event_type": "moved", "old_path": "...", "new_path": "..."}` or `{"event_type": "deleted", "path": "..."}`
- **Same retry logic as uploader**
- **A1 limitation:** no hash-based move reconstruction (A2). Watchdog's events pass through as-is. Some moves may degrade to delete+create — accepted for A1.

### Startup Scanner (daemon-side, new)

- **A1 mode:** disk↔cloud only (no local cache — that's A2's 3-way)
- **Steps:**
  1. GET /api/state → cloud manifest `{vault_path → content_hash}`
  2. Walk vault, compute SHA-256 of each file's raw bytes
  3. Compare:
     - On disk but not in cloud → upload (new file)
     - On disk AND in cloud but hash differs → re-upload (changed file)
     - In cloud but not on disk → report deleted
     - In cloud AND on disk, hash matches → skip (unchanged)
  4. Batch uploads for efficiency (capped at `scan_batch_size`)
- **Ignore patterns applied** during walk (same as watcher)
- **Acceptance:** Stop daemon → add/edit/delete files → restart → cloud matches vault.

---

## Open Questions (deferred to spec/plan)

1. **Binary bytes storage in A1:** When the cloud receives binary bytes via multipart, where does it put them before Phase 7 builds blob storage? Options: (a) store in a temp directory on the container filesystem, (b) store as a BLOB column in SQLite, (c) accept the upload and discard the bytes (keep only metadata). Recommend (c) for A1 — the metadata is what matters for the documents table; actual blob persistence is Phase 7's job.

2. **Watcher library:** Current code uses `watchdog`. The daemon reuses this. But should the daemon pin the same version, or can it float? Recommend: pin to same version as pyproject.toml for consistency.

3. **Async vs sync daemon:** Current watcher is sync (threading). Uploader benefits from async (concurrent uploads). Recommend: async main loop (asyncio), watcher runs in a thread, uploader uses aiohttp with Semaphore. Event reporter shares the async client.

4. **CLI entry points:** `python -m daemon start` (foreground), `python -m daemon scan` (one-shot reconcile), `python -m daemon status` (check connectivity). Recommend: Click CLI matching existing `kms` CLI pattern.

5. **Logging:** Use structlog (matching cloud code) or stdlib logging? Daemon is simpler — stdlib logging may suffice. But structlog gives structured output for debugging sync issues. Recommend: structlog for consistency.

---

## Glossary

| Term | Meaning |
|------|---------|
| **Daemon** | The thin local app on the user's Mac that watches files and uploads to the cloud |
| **Content hash** | SHA-256 fingerprint of a file's raw bytes — used to detect changes without reading the full content |
| **Startup scanner** | The reconcile that runs when the daemon boots — compares disk state against cloud state |
| **Cloud state** | The list of `{vault_path, content_hash}` the cloud knows about (from GET /api/state) |
| **Text extractor** | The component that pulls text out of files (PDF→text, DOCX→text) using existing handlers |
| **Binary upload** | When text extraction fails (images, graphs), the daemon uploads raw file bytes instead |
| **Event reporter** | Sends file move/rename/delete events to the cloud so it can update paths in the database |
| **Bail-early** | Skipping extraction+upload when a file hasn't changed (requires cache — A2, not A1) |
| **Ignore patterns** | Gitignore-style rules for files the daemon should not watch (e.g., .git, .DS_Store) |

---

## Diagrams

### Q1 — What Happens Inside (daemon core flow)

See the rendered Q1 diagram above in the conversation. The daemon has two operating modes:

1. **Startup reconcile (boot):** Load config → fetch cloud state via GET /api/state → walk entire vault and hash each file → compare against cloud → upload new/changed files → report deleted files → start watcher.

2. **Live watcher (running):** Watchdog detects file event → route by event type:
   - Create/modify → text extractor (handlers/) → uploader (text JSON or binary multipart) → cloud /api/upload
   - Move/delete → event reporter → cloud /api/event

Both paths share the same auth (Bearer KMS_DAEMON_API_KEY) and retry logic (exponential backoff, max 3).
