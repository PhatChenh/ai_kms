# Phase 7.5 — Issue Resolve (Post-Audit Daemon & Pipeline Fixes)

## Purpose

A thorough code audit of the cloud-native branch found 26 issues across the desktop sync daemon, the file-processing pipeline, and the AI prompt templates. Seven critical items were fixed immediately. This phase resolves the remaining 12 confirmed issues in three ordered waves. After this phase, the daemon no longer re-uploads the entire vault on a transient network glitch, ignores directories it should skip during scans, logs exceptions that were previously swallowed silently, and has a cleaner internal structure. The capture pipeline eliminates duplicated code blocks and checks for failures it previously ignored. No new features are added — only reliability, maintainability, and correctness improvements.

**Who cares:** The system does exactly the same things it did before, but does them more reliably and with fewer hidden failure modes. A non-technical reader can think of this as tightening loose screws found during an inspection.

---

## Already built (reuse, do not rebuild)

| Component | Location | What it does | How this spec uses it | Depth |
|-----------|----------|--------------|----------------------|-------|
| Retry Helper | `daemon/_http_retry.py` | Exponential-backoff retry for HTTP calls | Wave 2 wraps the cloud-state fetch call in it (Finding #2) | deep |
| Result type | `core/result.py` | `Success` / `Failure` return pattern | All fixes that check return values use this pattern | deep |
| Content Extractor | `daemon/extractor.py` | Reads files from disk and produces text or binary content objects | Called by the DaemonLoop callbacks — not modified, but the caller moves during Wave 1 | deep |
| Local Cache | `daemon/cache.py` | Advisory hash cache for the daemon | Called by DaemonLoop callbacks — unchanged, but callers restructure in Wave 1 | deep |
| Move Detective | `daemon/move_buffer.py` | Correlates delete+create pairs as moves using content hashes | Called by DaemonLoop callbacks — unchanged, but callers restructure in Wave 1 | deep |
| File Uploader | `daemon/uploader.py` | Sends extracted file content to the cloud endpoint | Wave 2 removes per-call auth headers (centralized to HTTP client) | deep |
| Event Reporter | `daemon/event_reporter.py` | Sends moved/deleted events to the cloud endpoint | Wave 2 removes per-call auth headers (centralized to HTTP client) | deep |
| File Watcher | `daemon/watcher.py` | Detects filesystem events (create, modify, move, delete) with debounce | Wave 2 fixes the move-event skip rule (Finding M5) | deep |
| Daemon Settings | `daemon/config.py` | Pydantic configuration model for the daemon | Wave 2 removes dead field and widens a type (Findings M1, M8) | deep |
| Vault Scanner | `daemon/scanner.py` | Disk-vs-cloud reconcile at startup and periodic intervals | Wave 2 adds retry, prunes ignored dirs, deletes dead twin function (Findings #2, #3, #7) | deep |
| Capture Pipeline | `pipelines/capture.py` | Processes text and binary files — summarize, classify, index | Wave 3 extracts shared indexing helper and checks attach_summary results (Findings #6, M11) | deep |
| Vision Prompt Template | `prompts/describe_image.yaml` | AI prompt for describing images/binaries | Wave 3 adds the declared-but-unused mime_type variable to the prompt text (Finding C5) | shallow |
| Keyword Indexer | `retrieval/keyword.py` | FTS5-based keyword indexing for search | Called by the new `_best_effort_index` helper — not modified | deep |
| Embedding Indexer | `retrieval/embeddings.py` | Vector embedding indexing for semantic search | Called by the new `_best_effort_index` helper — not modified | deep |
| Document Store | `storage/documents.py` | SQLite CRUD for the documents table (including `attach_summary`) | Wave 3 checks the return value of `attach_summary` — not modified | deep |

---

## Q1 Diagram — What happens inside (from design)

```
          12 verified issues
          from nuclear review
               │
               ▼
  ┌──────────────────────────────┐
  │  WAVE 1 — Daemon Structure  │
  │  Pull the 243-line god      │
  │  function into its own      │
  │  class; update the one      │
  │  external caller            │
  └──────────────┬───────────────┘
                 │  class exists,
                 │  imports updated
                 ▼
  ┌──────────────────────────────┐
  │  WAVE 2 — Daemon Fixes      │
  │  (8 items, independent)     │
  │                              │
  │  • Retry before aborting    │
  │    cloud fetch              │
  │  • Skip ignored folders     │
  │    during vault walk        │
  │  • Log exceptions from      │
  │    async callbacks          │
  │  • Centralize auth header   │
  │  • Delete dead twin builder │
  │  • Remove dead config field │
  │  • Fix move-event skip rule │
  │  • Widen periodic-interval  │
  │    number type              │
  └──────────────┬───────────────┘
                 │  all daemon
                 │  fixes landed
                 ▼
  ┌──────────────────────────────┐
  │  WAVE 3 — Capture + Prompt  │
  │  (3 items, independent)     │
  │                              │
  │  • Extract shared indexing  │
  │    helper (removes 2x       │
  │    26-line duplicate)       │
  │  • Check and log summary    │
  │    attachment failures      │
  │  • Add file type to the     │
  │    vision prompt template   │
  └──────────────────────────────┘
```

## Q2 Diagram — How it connects to others

```
# Phase 7.5 Issue Resolve — How It Connects
Scope: Shows which components each wave modifies and what they
       depend on. Does NOT show internal logic of each fix
       (see Q1 for that).

How to read this:
  Solid boxes    = components that already exist (no new modules)
  Wave labels    = which wave modifies each component
  Arrow labels   = what the modification does or what flows
  ══════►        = "modifies" (wave changes this component)
  ──────►        = "calls" or "depends on" (unchanged dependency)

                    WAVE 1 modifies
                    ═══════════════════════════════════════╗
                                                          ║
  ┌──────────────────────┐    restructures into    ┌──────╨──────────────┐
  │ WAVE 1               │    DaemonLoop class     │ Daemon Command     │
  │ Daemon Structure     │═════════════════════════►│ Center             │
  │ (1 fix)              │                         │ Sync engine core   │
  └──────────┬───────────┘    updates import       └────────────────────┘
             ║                ═══════════╗                  │
             ║                          ║                  │ calls
             ║                   ┌──────╨──────────────┐   │
             ║                   │ App Supervisor       │   │
             ║                   │ Desktop entry point  │   │
             ║                   └─────────────────────┘   │
             ║                                             │
             ▼                                             │
  ┌──────────────────────┐    adds retry     ┌─────────────┴────────────┐
  │ WAVE 2               │═══════════════════►│ Vault Scanner           │
  │ Daemon Fixes         │    deletes twin    │ Disk-vs-cloud reconcile │
  │ (8 fixes)            │    prunes dirs     └──────────┬──────────────┘
  └──────────┬───────────┘                               │ now uses
             ║                                           ▼
             ║    centralizes auth  ┌──────────────────────────────┐
             ║    on HTTP client    │ Retry Helper                 │
             ║    ═════════════════►│ Exponential backoff for HTTP │
             ║         │           └──────────────────────────────┘
             ║         │ removes per-call auth headers from:
             ║         ├───────► ┌──────────────────────┐
             ║         │         │ File Uploader        │
             ║         │         │ Sends files to cloud │
             ║         │         └──────────────────────┘
             ║         ├───────► ┌──────────────────────┐
             ║         │         │ Event Reporter       │
             ║         │         │ Sends move/delete    │
             ║         │         └──────────────────────┘
             ║         └───────► ┌──────────────────────┐
             ║                   │ Vault Scanner        │
             ║                   │ (same as above)      │
             ║                   └──────────────────────┘
             ║
             ║    removes dead field     ┌──────────────────────┐
             ║    widens number type     │ Daemon Settings      │
             ╠══════════════════════════►│ Configuration model  │
             ║                           └──────────────────────┘
             ║
             ║    fixes move-skip rule   ┌──────────────────────┐
             ╠══════════════════════════►│ File Watcher         │
             ║                           │ Filesystem events    │
             ║                           └──────────────────────┘
             ║
             ▼
  ┌──────────────────────┐    extracts shared    ┌──────────────────────┐
  │ WAVE 3               │    indexing helper +   │ Capture Pipeline     │
  │ Capture + Prompt     │    checks failures     │ Text & binary       │
  │ (3 fixes)            │═══════════════════════►│ file processing     │
  └──────────────────────┘                        └──────────────────────┘
             ║
             ║    adds file type        ┌──────────────────────┐
             ╚═════════════════════════►│ Vision Prompt        │
                                        │ Template             │
                                        │ AI image description │
                                        └──────────────────────┘
```

## Feature overview

This phase is purely internal cleanup — no new features, no API changes, no user-visible behavior changes except improved reliability.

**Happy path (nothing changes for the user):** The daemon starts, scans the vault, watches for file changes, and syncs everything to the cloud. The capture pipeline processes text and binary files into searchable summaries. All of this continues to work exactly as before.

**What improves under the hood:**

1. **Daemon structure (Wave 1):** The sync engine's core logic — currently a single 243-line function with 9 nested closures sharing state via `nonlocal` — is extracted into a proper class (`DaemonLoop`). Each callback becomes an independent method. The App Supervisor (the desktop app's entry point) updates its import to use the new class. Behavior is identical; testability and readability improve dramatically.

2. **Daemon reliability (Wave 2):** Eight independent fixes across four daemon modules:
   - The cloud-state fetch — the only HTTP call in the daemon that lacks retry protection — gets wrapped in the existing Retry Helper. If all retries fail, the scan aborts with a warning instead of treating the cloud as empty (which previously triggered a full vault re-upload).
   - The vault directory walk now prunes ignored directories (`.git`, `.obsidian`, `.trash`) instead of descending into them and filtering individual files. This avoids reading thousands of git objects on vaults with version control.
   - Fire-and-forget async callbacks (4 sites) get exception-logging callbacks so that unexpected errors are no longer silently swallowed.
   - The auth header (duplicated across 6 call sites in 4 files) is centralized on the HTTP client at construction time.
   - A dead twin function in the scanner is deleted (its replacement already exists).
   - A dead config field (`scan_batch_size`) that is defined but never read is removed.
   - The move-event skip rule is fixed: currently skips if either source or destination is in an ignored path; should skip only when the destination is ignored (otherwise a file moved out of `.git` into a watched folder is silently dropped).
   - The periodic-interval config field type is widened from `int` to `float` for consistency with other timing fields.

3. **Capture pipeline cleanup (Wave 3):** Three independent fixes:
   - Two identical 26-line indexing blocks (one in the text path, one in the binary path) are extracted into a shared helper function.
   - The return value of `attach_summary` (called at 2 sites) is checked; failures are now logged at warning level instead of silently discarded.
   - The vision prompt template declares a `mime_type` variable but never uses it in the prompt text. The variable is added so the AI knows the file type when describing an image.

---

## Out of scope

- **Hardcoded retry delays (Finding M6)** — The retry helper uses `1s base, 2x multiplier` as constants. Convention says these should be in config, but they are internal retry parameters and acceptable for MVP. Deferred — no phase assigned yet.
- **Event type string enum (Finding M7)** — `"deleted"` and `"moved"` are hardcoded strings in the Event Reporter. An enum would centralize them but the strings are used in only 2 places. Deferred — no phase assigned yet.
- **Cloud/API findings C1-C3, C6-C8** — These are separate from the daemon and capture pipeline. C2 (sync blob blocks event loop) is tracked for Phase 9. C4 (no UNIQUE constraint on knowledge_entries) is intentional — dedup is prompt-level by design (Phase 8 grill decision), not a SQL constraint. The rest are cosmetic or false positives per the research verification.
- **Local-only mode watcher tests** — `test_watcher.py` (48 vault/watcher tests) was deleted on the cloud-native branch. Restoring or deprecating the legacy vault watcher is a separate decision. Not in scope for this batch.
- **Auto-update mechanism** — The daemon installer (Phase 6 Slice B) defers auto-update. This batch does not add it.

---

## Constraints

- **Result type returns (C-12)** — Any new or modified public function in `pipelines/` must return `Success` or `Failure`. Source: CONSTRAINTS.md C-12.
- **Prompts as config (C-07)** — The vision prompt edit (Finding C5) modifies `prompts/describe_image.yaml`, not inline code. Source: CONSTRAINTS.md C-07.
- **No direct vault writes (CLAUDE.md hook)** — This batch does not write to the vault. All changes are in daemon code, pipeline code, and prompt YAML.
- **Audit log (C-13)** — The capture pipeline's audit writes are not modified. The new `_best_effort_index` helper is post-audit. Source: CONSTRAINTS.md C-13.
- **Daemon cache is advisory (C-18)** — The DaemonLoop extraction must preserve the cache-on-ack pattern. Source: CONSTRAINTS.md C-18.
- **Scope discipline (CLAUDE.md)** — Touch only what the 12 findings require. No adjacent improvements.

---

## Assumptions

| ID | Assumption | Source | What would prove it wrong |
|----|-----------|--------|--------------------------|
| A1 | `_run_with_stop` at `cli.py:268` is the only function `app.py` imports from `cli.py` for the sync engine (import at line 23). | Design implication #1, research finding #1 | `app.py` imports other functions or closures from `cli.py` beyond `_run_with_stop` |
| A2 | `_fetch_cloud_state` at `scanner.py:429` calls `client.get()` directly without `retry_with_backoff`. It is the only daemon HTTP call not using retry. | Design implication #2, research finding #2 | `_fetch_cloud_state` already uses retry, or another HTTP call also lacks retry |
| A3 | `retry_with_backoff` takes `(client, config, request_fn)` as its signature and returns `Result[httpx.Response]`. | Design implication #2, code at `_http_retry.py:33-37` | The signature has changed or takes different parameters |
| A4 | `_build_disk_state` at `scanner.py:478` and `_build_disk_entries` at `scanner.py:523` both use `_dirnames` (with leading underscore, explicitly unused) in their `os.walk` loops. | Design implication #3, research finding #3 | The variable is already named `dirnames` (without underscore) and is being pruned |
| A5 | There are exactly 4 sites in `cli.py` that call `asyncio.run_coroutine_threadsafe` and discard the returned Future. | Design implication #4, research finding #4 | The count is different (more or fewer sites), or some already have done callbacks |
| A6 | Auth header `{"Authorization": f"Bearer {config.api_key}"}` appears in 6 places across `uploader.py` (2), `event_reporter.py` (2), `scanner.py` (1), and `cli.py` (1). Note: `connection_check.py` has a 7th occurrence using `key` (not `config.api_key`) — it creates its own client, so it is outside the centralization scope. | Design implication #5, research finding #5, code verification | The count or distribution is different, or `connection_check.py` shares the main client |
| A7 | The duplicate indexing blocks in `capture.py` are at approximately lines 224-250 (text path) and 440-466 (binary path), with identical structure except the `body` argument. | Design implication #6, research finding #6 | The blocks have diverged, have different parameters, or are no longer at those locations |
| A8 | `_build_disk_state` is called only in the `cache is None` backward-compat path in `scan()`. | Design implication #7, research finding #7 | `_build_disk_state` is called from other locations |
| A9 | `scan_batch_size` at `config.py:60` is defined as a field but grep finds zero usages outside the config module. | Research finding M1 | The field is used somewhere (test fixtures that construct `DaemonConfig` with it still count as usage that must be updated) |
| A10 | `on_moved` at `watcher.py:194-199` uses `if self._should_skip(src_path) or self._should_skip(dst_path): return` — an `or` condition. | Research finding M5 | The condition has already been changed or uses different logic |
| A11 | `periodic_interval_seconds` at `config.py:66` is typed as `int`. | Research finding M8 | The type has already been changed to `float` |
| A12 | `attach_summary` returns a `Result` type and its return value is currently discarded at 2 call sites in `capture.py`. | Research finding M11 | `attach_summary` returns `None` or its return is already checked |
| A13 | `describe_image.yaml:15` declares `variables: [mime_type]` but the template text at lines 9-13 never contains `{{mime_type}}`. | Research finding C5 | The variable is already used in the template text |
| A14 | Phase 6 Slice B restructured `cli.py` with 324 insertions / 223 deletions. The line numbers from the nuclear review may have shifted. | Context from user | Line numbers in the findings are still accurate post-Slice-B merge |

---

## Component dependency order

### 1. DaemonLoop class extraction (Wave 1)

**Behavior ID:** P7H-FIX-07

**Goal.** Extract the 243-line `_run_with_stop` function and its 9 nested closures into a `DaemonLoop` class, making each callback independently testable.

**Build.** Modify the Daemon Command Center (`daemon/cli.py`):
- Create a `DaemonLoop` class that owns the sync engine lifecycle: config, HTTP client, cache, move buffer, move timer, and periodic reconcile task.
- Move each nested closure (`_on_create_or_modify`, `_on_move`, `_on_delete`, `_refresh_move_timer`, `_on_move_window_expired`) into a class method.
- Replace `nonlocal` captures with `self.` attribute access.
- The `start` Click command becomes ~10 lines calling `DaemonLoop`.
- Preserve `_run_with_stop` as a thin wrapper (or rename) so the App Supervisor's import continues to work — or update the import in `app.py` directly.

Modify the App Supervisor (`daemon/app.py`):
- Update the import at line 23 from `_run_with_stop` to the new class or wrapper.

**Depends on.** None.

**Assumes.** A1, A14.

**Done when.** The daemon starts, scans, watches, and stops identically to before. The `start` command body is under 15 lines. Each callback is a method on `DaemonLoop`, not a nested closure. All existing daemon tests pass (after updating test setup to construct the class instead of calling the function). The App Supervisor still launches the sync engine correctly.

---

### 2. Cloud fetch retry protection (Wave 2)

**Behavior ID:** P7H-FIX-01

**Goal.** Prevent the daemon from re-uploading the entire vault when the cloud is temporarily unreachable.

**Build.** Modify the Vault Scanner (`daemon/scanner.py`):
- Wrap the `_fetch_cloud_state` HTTP call in `retry_with_backoff`.
- On exhaustion (all retries failed), return `None` instead of `{}`.
- In the caller (`scan()`), check for `None` return. If `None`, log a warning and abort the scan early (return a zero-count `ScanResult`), preserving the cache state.

**Depends on.** Component 1 (DaemonLoop extraction) — because `_fetch_cloud_state` is in `scanner.py` which is independent of `cli.py`, this component does not strictly depend on Wave 1. However, it is sequenced in Wave 2 by convention.

**Assumes.** A2, A3.

**Done when.** When the cloud endpoint is unreachable, the scan retries with exponential backoff (using the existing Retry Helper). After exhausting retries, the scan aborts with a warning log instead of treating the cloud as empty. No files are re-uploaded. Existing cache state is preserved.

---

### 3. Ignored directory pruning (Wave 2)

**Behavior ID:** P7H-FIX-02

**Goal.** Stop the vault scanner from descending into `.git`, `.obsidian`, `.trash`, and other ignored directories during file walks.

**Build.** Modify the Vault Scanner (`daemon/scanner.py`):
- In both `_build_disk_state` and `_build_disk_entries` (until `_build_disk_state` is deleted in Component 8), rename `_dirnames` to `dirnames` and prune in-place: `dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]`.
- Derive `_SKIP_DIRS` from the non-glob patterns in `config.ignore_patterns` (the entries that are exact directory names like `.git`, `.obsidian`, `.trash`).

**Depends on.** None (independent within Wave 2).

**Assumes.** A4.

**Done when.** When scanning a vault that contains a `.git` directory with thousands of objects, the scanner does not descend into `.git` at all. Individual files in non-ignored directories are still filtered by `should_skip_path()` as before.

---

### 4. Async callback exception logging (Wave 2)

**Behavior ID:** P7H-FIX-03

**Goal.** Make unexpected exceptions in watcher callbacks visible in the daemon logs instead of silently vanishing.

**Build.** Modify the Daemon Command Center (`daemon/cli.py`, now inside `DaemonLoop` after Wave 1):
- Add a `_log_future_exception` helper that inspects `fut.exception()` and logs at error level.
- At all 4 sites where `asyncio.run_coroutine_threadsafe` is called, capture the returned Future and attach the done callback via `fut.add_done_callback(_log_future_exception)`.

**Depends on.** Component 1 (DaemonLoop extraction) — the 4 sites will have moved from nested closures to class methods.

**Assumes.** A5.

**Done when.** When a watcher callback raises an unexpected exception (not a `Failure` result, but a genuine `TypeError`, `AttributeError`, etc.), the exception appears in the daemon log at error level with the callback name and traceback summary. No behavior change on the success path.

---

### 5. Auth header centralization (Wave 2)

**Behavior ID:** P7H-FIX-08

**Goal.** Eliminate the 6 duplicated auth-header constructions across 4 files by setting default headers on the HTTP client at construction.

**Build.** Modify the Daemon Command Center (`daemon/cli.py`, inside `DaemonLoop` after Wave 1):
- When constructing `httpx.AsyncClient`, add `headers={"Authorization": f"Bearer {cfg.api_key}"}` as a default.

Modify the File Uploader (`daemon/uploader.py`):
- Remove `headers={"Authorization": f"Bearer {config.api_key}"}` from both `_request()` closures (text and binary upload). The client's default headers will be used automatically.

Modify the Event Reporter (`daemon/event_reporter.py`):
- Remove `headers={"Authorization": ...}` from both `_request()` closures (moved and deleted events).

Modify the Vault Scanner (`daemon/scanner.py`):
- Remove `headers={"Authorization": ...}` from `_fetch_cloud_state`.

Modify the Daemon Command Center (`daemon/cli.py`):
- Remove any remaining per-call `headers=` in the `status` command's health check (if it also uses the shared client — verify; the status command creates its own client, so its auth header stays).

**Depends on.** Component 1 (DaemonLoop extraction). The client construction point must be identified in the new class.

**Assumes.** A6.

**Decisions.**
- Q: Does the `status` command's health check share the same `httpx.AsyncClient`? Options: Yes (centralize there too) / No (it creates its own client, leave its header in place). Leaning No — the `status` command creates a separate short-lived client.

**Done when.** The auth header `{"Authorization": f"Bearer ..."}` appears in exactly one place in the daemon's long-lived client (the `httpx.AsyncClient` constructor in `DaemonLoop`). All HTTP calls that go through that client inherit the header automatically. The `status` command and the `connection_check` module, which each create their own short-lived clients, retain their own headers. All upload, event, and scanner calls still authenticate correctly.

---

### 6. Dead twin function deletion (Wave 2)

**Behavior ID:** P7H-FIX-10

**Goal.** Remove the dead `_build_disk_state` function (a near-twin of `_build_disk_entries`) to eliminate 40 lines of duplicated code.

**Build.** Modify the Vault Scanner (`daemon/scanner.py`):
- Delete the `_build_disk_state` function entirely.
- In `scan()`, update the `cache is None` backward-compat path to call `_build_disk_entries` instead and discard the extra `entries` dict: `_entries, disk_state, unreadable = _build_disk_entries(config)`.

**Depends on.** Component 3 (ignored directory pruning) — so that pruning is only applied to `_build_disk_entries`, not to the about-to-be-deleted twin.

**Assumes.** A8.

**Done when.** `_build_disk_state` no longer exists in `scanner.py`. The `cache=None` scan path calls `_build_disk_entries` and produces identical results. No behavioral change.

---

### 7. Dead config field removal (Wave 2)

**Behavior ID:** P7H-FIX-11

**Goal.** Remove the `scan_batch_size` config field that is defined but never used.

**Build.** Modify Daemon Settings (`daemon/config.py`):
- Remove the `scan_batch_size: int = Field(default=50, ge=1)` line.

Modify tests (`tests/test_daemon/test_config.py`):
- Remove `scan_batch_size` from any test fixtures that construct `DaemonConfig` with it explicitly. (If tests only use defaults, no change needed.)

**Depends on.** None (independent within Wave 2).

**Assumes.** A9.

**Done when.** `scan_batch_size` does not appear anywhere in the codebase. All config tests pass. Existing daemon config YAML files that include `scan_batch_size` will be rejected by Pydantic's `extra = "forbid"` — this is intentional and correct (the field was never used, so no deployed config should have it).

**Decisions.**
- Q: Should we allow `scan_batch_size` in config YAML as a no-op for backward compatibility? Options: Yes (add to an ignore list) / No (let `extra=forbid` reject it, forcing users to remove it). Leaning No — the field was never used, and `extra=forbid` is the project convention. No deployed daemon exists yet.

---

### 8. Move-event skip rule fix (Wave 2)

**Behavior ID:** P7H-FIX-04

**Goal.** Fix the move-event handler to process files moved from an ignored directory (like `.git`) into a watched directory, instead of silently dropping them.

**Build.** Modify the File Watcher (`daemon/watcher.py`):
- In `on_moved`, change the skip condition from `if self._should_skip(src_path) or self._should_skip(dst_path): return` to: if only the destination is ignored, skip (the file is moving into an ignored area). If only the source is ignored, process the event as a create at the destination (the file is appearing in a watched area).
- Specifically: `if self._should_skip(dst_path): return` — and if `self._should_skip(src_path)` but not `dst_path`, treat as a create event for `dst_path`.

**Depends on.** None (independent within Wave 2).

**Assumes.** A10.

**Done when.** When a file is moved from `.git/` into `Projects/Alpha/`, the watcher fires an `on_create` callback for the destination. When a file is moved from `Projects/Alpha/` into `.git/`, the watcher fires an `on_delete` callback for the source. When neither path is ignored, `on_move` fires as before. When both are ignored, the event is silently dropped.

---

### 9. Periodic interval type widening (Wave 2)

**Behavior ID:** P7H-FIX-12

**Goal.** Make `periodic_interval_seconds` accept float values for consistency with other timing fields (`debounce_seconds`, `move_window_seconds`).

**Build.** Modify Daemon Settings (`daemon/config.py`):
- Change `periodic_interval_seconds: int = Field(default=21600, ge=0)` to `periodic_interval_seconds: float = Field(default=21600, ge=0)`.

**Depends on.** None (independent within Wave 2).

**Assumes.** A11.

**Done when.** `periodic_interval_seconds` accepts float values (e.g., `3600.5`). Existing integer values continue to work. The daemon's `asyncio.sleep(cfg.periodic_interval_seconds)` call already accepts floats, so no caller changes are needed.

---

### 10. Shared indexing helper extraction (Wave 3)

**Behavior ID:** P7H-FIX-09

**Goal.** Eliminate the duplicated 26-line indexing blocks in the capture pipeline by extracting a shared helper.

**Build.** Modify the Capture Pipeline (`pipelines/capture.py`):
- Create a private helper function `_best_effort_index(vault_path, title, summary, body, db_path)` that contains the two try/except blocks for `index_keywords` and `index_embedding`.
- Replace both inline indexing blocks (text path and binary path) with calls to `_best_effort_index(...)`.
- The `body` parameter is the only argument that differs between the two call sites (`extracted_text` for text path, `summary or ""` for binary path).

**Depends on.** None (independent within Wave 3, and independent of Waves 1-2).

**Assumes.** A7.

**Done when.** The two indexing blocks are replaced by single-line calls to `_best_effort_index`. The helper contains the lazy imports, the try/except, and the logger calls. Behavior is identical — errors are caught and logged, never propagated.

---

### 11. Attach summary failure logging (Wave 3)

**Behavior ID:** P7H-FIX-06

**Goal.** Make `attach_summary` failures visible in the logs instead of silently discarding the return value.

**Build.** Modify the Capture Pipeline (`pipelines/capture.py`):
- At both call sites where `documents.attach_summary(...)` is called (text path and binary path), capture the return value.
- If the return is a `Failure`, log a warning with the vault path and error message.
- Capture still returns `Success` regardless — `attach_summary` failure is non-fatal.

**Depends on.** None (independent within Wave 3).

**Assumes.** A12.

**Done when.** When `attach_summary` returns a `Failure` (e.g., database error), the capture pipeline logs a warning like `"attach_summary failed for <vault_path>: <error>"`. The capture result is still `Success` — the document was stored, only the summary attachment failed.

---

### 12. Vision prompt mime_type usage (Wave 3)

**Behavior ID:** P7H-FIX-05

**Goal.** Use the already-declared `mime_type` variable in the vision prompt so the AI knows the file type when describing an image.

**Build.** Modify the Vision Prompt Template (`prompts/describe_image.yaml`):
- Add a line to the `user:` section: `"This file is a {{mime_type}}."` — placed before the description request.

**Depends on.** None (independent within Wave 3).

**Assumes.** A13.

**Done when.** The rendered prompt includes the file's MIME type (e.g., "This file is a image/png."). The `mime_type` variable in the `variables:` list is no longer orphaned. The AI receives file-type context to produce more specific descriptions.

---

## Handoff notes

- **Contract with downstream phases:** This phase produces no new APIs, no schema changes, no new config keys. All changes are internal. Phase 8 (Classify Redesign), Phase 9 (MCP Adaptation), and Phase 10 (Web UI) are unaffected.
- **Single PR recommended:** The design doc recommends a single PR with clear commit-per-wave structure. All 12 fixes are verified and approaches locked. The planner should structure implementation phases to match the 3 waves.
- **Test restructuring in Wave 1:** The DaemonLoop extraction will require significant test file updates. `tests/test_daemon/test_cli.py` is 1850 lines with 303 mock-related lines. The planner should budget time for this.
- **Open uncertainty:** Line numbers from the nuclear review may have shifted after Phase 6 Slice B merged (324 insertions, 223 deletions in `cli.py`). Research should re-verify exact locations before planning. This is captured as assumption A14.
- **`scan_batch_size` in deployed configs:** If any test or development config YAML files include `scan_batch_size`, they will fail validation after Component 7 lands (`extra=forbid`). The planner should grep for this field in all YAML files, not just Python.
- **Suggested research:** Verify that `attach_summary` in `storage/documents.py` returns a `Result` type (assumption A12). If it returns `None` or an integer, the failure-logging approach needs adjustment.
- **Suggested research:** Verify the exact `_run_with_stop` line number and structure post-Slice-B merge to confirm the extraction boundary (assumption A14).

---

## Source references

- Design doc: `docs/1_design/phase7_5_issue_resolve.md`
- Research verification: `docs/3_research/nuclear_review_deferred_findings_verification.md`
- Original findings: `docs/0_draft/nuclear_review_deferred_findings.md`
- Behavior inventory: `docs/system_behavior/behavior_inventory.yaml` (P7H-FIX-01 through P7H-FIX-12)
