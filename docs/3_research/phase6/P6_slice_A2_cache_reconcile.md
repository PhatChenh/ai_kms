# Research: Phase 6 Slice A2 — Local Cache + Smart Reconcile
_Last updated: 2026-06-14_

## Overview

This subsystem gives the daemon persistent memory of what it already sent to the cloud. Without it (A1 behaviour), every restart and every file touch re-reads, re-hashes, and re-checks against the cloud. A2 adds a small local cache (JSON manifest), move detection for delete-then-create pairs, a 3-way reconcile (disk vs cache vs cloud), and a periodic audit timer.

This research verified all 8 spec assumptions and 13 key structural claims against the actual daemon code in `src/daemon/`. **7 of 8 assumptions validated. 1 assumption (A8) partially invalidated** -- the spec's claim about macOS FSEvents delete-then-create behaviour is misleading for the primary use case. The remaining structural claims (line numbers, return types, signatures, threading model) are all accurate.

---

## Key Components

The daemon subsystem is a standalone package (`src/daemon/`) with zero imports from `core/config`. These are the files A2 will touch or depend on.

| File | Role | A2 interaction |
|------|------|----------------|
| `daemon/config.py` | Pydantic settings model (`DaemonConfig`), YAML loader | A2 adds 4 new fields |
| `daemon/scanner.py` | Startup disk-vs-cloud reconcile (`scan()`) | A2 upgrades to 3-way compare |
| `daemon/extractor.py` | Reads files, computes SHA-256 hash, extracts text | A2 reuses the hash for cache comparison |
| `daemon/uploader.py` | HTTP upload with retry | A2 wires cache-on-ack into Success path |
| `daemon/event_reporter.py` | HTTP event reporting (moved/deleted) | A2 wires cache update into Success path |
| `daemon/watcher.py` | Watchdog FSEvents wrapper + debounce | A2 does NOT modify (move buffer sits after debounce) |
| `daemon/cli.py` | Click CLI with `start`, `scan`, `status` commands | A2 modifies `start`: adds cache load/save, move buffer, periodic timer, bail-early |
| `daemon/_http_retry.py` | Exponential backoff retry helper | Unchanged; used indirectly via uploader + event_reporter |

New files A2 will create: `daemon/cache.py` (cache module), `daemon/move_buffer.py` (move-correlation buffer).

---

## How It Works

When the daemon starts today (A1 behaviour), it runs `scan()` which fetches the cloud's document manifest and walks the vault to build a disk snapshot. It compares these two and uploads new/changed files, reports deleted files, and skips matching files. Then it starts a watchdog observer that fires callbacks on file create, modify, move, and delete events. The callbacks bridge from the watchdog thread to the asyncio event loop via `run_coroutine_threadsafe`.

After A2, the startup scan gains a third input (the cache), the live callbacks gain a bail-early check (skip if cache matches), deletes are buffered for move detection, and a periodic timer re-runs the scan to catch missed events.

---

## Spec Verification

All line numbers, signatures, return types, and behavioural claims from the spec's "Already built" table were verified against the actual source files.

| ID | Spec Claim | Verdict | Evidence |
|---|---|---|---|
| A1 | `_build_disk_state()` returns `dict[str, str]` mapping vault_path to content_hash, compatible with cache shape | **Validated** | `scanner.py:210` -- returns `tuple[dict[str, str], set[str]]`. The dict component IS `dict[str, str]` (vault_path to sha256_hex). The tuple wrapper (second element = unreadable set) is a detail the spec omits but does not contradict -- the cache would match the dict component. |
| A2 | `_fetch_cloud_state()` returns `dict[str, str or None]` where None means "cloud stored no fingerprint" | **Validated** | `scanner.py:160-162` -- type annotation is `dict[str, str \| None]`. Comment at line 203: `# content_hash can be None / NULL -> treat as "always re-upload"`. |
| A3 | `extract().content_hash` produces the same SHA-256 as `_build_disk_state()` -- both hash raw bytes | **Validated** | `extractor.py:129`: `hashlib.sha256(raw_bytes).hexdigest()`. `scanner.py:250`: `hashlib.sha256(raw).hexdigest()`. Both read raw bytes and hash identically. |
| A4 | `threading.Timer` callback fires in a thread-pool thread, not in the asyncio event loop | **Validated** | `watcher.py:142-159` -- `threading.Timer` spawns an OS thread per fire. The callback (e.g., `_on_create`) runs in that timer thread and uses `run_coroutine_threadsafe` (cli.py:202) to bridge to the event loop. No asyncio involvement in the timer callback itself. |
| A5 | `run_coroutine_threadsafe()` in `_on_create_or_modify` correctly bridges the watcher thread to the asyncio loop | **Validated** | `cli.py:182-202` -- `_on_create_or_modify` is a sync function. Inner async `_handle()` is scheduled via `asyncio.run_coroutine_threadsafe(_handle(), loop)` at line 202. A `threading.Lock` is correct for the cache since it must be acquirable from both the timer thread and the event loop thread. |
| A6 | `GET /api/state` returns ALL documents (no pagination) | **Validated (daemon-side)** | `scanner.py:160-207` -- single GET request, parses `body["documents"]` as a list, no pagination loop. Whether the cloud endpoint actually returns everything is an external contract (unverifiable from daemon code alone), but the daemon code expects and processes everything in one response. |
| A7 | `os.rename()` is atomic on macOS APFS/HFS+ for same-volume files | **Validated** | Standard POSIX guarantee. `os.rename()` / `pathlib.Path.rename()` is atomic on APFS (the standard macOS filesystem) when source and destination are on the same volume. The spec's crash-safe temp-file-then-rename pattern is sound. |
| A8 | macOS FSEvents delivers delete-then-create events for drag-moves within ~2 seconds | **Partially Invalidated** | See detailed analysis below in Invalidated Assumptions section. |

### Additional structural claims verified

| Claim | Verdict | Evidence |
|---|---|---|
| `DaemonConfig` is a flat Pydantic model at `config.py:26` with `extra="forbid"` | **Validated** | Line 26: `class DaemonConfig(BaseModel)`, line 33: `model_config = {"extra": "forbid"}`. Fields: vault_root, cloud_endpoint, api_key, debounce_seconds (1.0), ignore_patterns, upload_concurrency (4), retry_max (3), scan_batch_size (50), max_file_size_bytes (50MB). |
| `scan()` at `scanner.py:66` builds disk_state (line 90) and cloud_state (line 87) and does a 2-way set comparison (lines 96-98) | **Validated** | All line numbers correct. `cloud_state` at 87, `disk_state, unreadable` at 90, set operations at 96-98. |
| `ScanResult` at `scanner.py:39` has 4 int fields (uploaded, re_uploaded, deleted, skipped) | **Validated** | Lines 39-53. No `moved` field yet (A2 would add it). |
| `upload_text()` and `upload_binary()` return `Result[int]` | **Validated** | `uploader.py:70-74` and `uploader.py:112-116`. The int is `document_id`. |
| `report_moved()` and `report_deleted()` return `Result[None]` | **Validated** | `event_reporter.py:31-36` (moved) and `event_reporter.py:70-74` (deleted). Both return `Result[None]`, not `Result[int]`. |
| `_on_create_or_modify()` at cli.py:182, `_on_move()` at 204, `_on_delete()` at 215 | **Validated** | All line numbers exact. All are sync functions bridging to async via `run_coroutine_threadsafe`. |
| `should_skip_path()` at `watcher.py:36` is a standalone helper | **Validated** | Lines 36-81. Takes `path`, `ignore_patterns`, optional `root`. Used by both watcher and scanner. |
| `_DaemonEventHandler` at `watcher.py:87` debounces via `threading.Timer` | **Validated** | Lines 87-214. Debounce dict at line 122, Timer at line 157. Key detail: creates and modifies ARE debounced; **moves and deletes are NOT debounced** (lines 203-204 and 213-214 call callbacks directly). |
| Deletes are reported immediately in A1 (cli.py:215-224) | **Validated** | `_on_delete` schedules `report_deleted` immediately via `run_coroutine_threadsafe`. |
| `extract()` does `stat()` for size check but does NOT do a stat-then-hash pre-filter | **Validated** | `extractor.py:104-106` checks `path.stat().st_size` for the size limit. But it always reads all bytes and hashes. A new pre-filter path is needed for the bail-early optimization. |
| Observer is `FSEventsObserver` on macOS | **Validated** | `watchdog.observers.Observer` resolves to `watchdog.observers.fsevents.FSEventsObserver` on this machine. |

---

## Edge Cases & Silent Failure Modes

These are situations that could cause surprising behaviour during A2 implementation.

1. **Debounce asymmetry.** Creates and modifies are debounced (via `threading.Timer` with `debounce_seconds` delay); moves and deletes are NOT debounced (dispatched immediately). This means a delete event arrives at the move buffer immediately, but the corresponding create event arrives after the debounce delay (1.0s default). The Move Detective's window (2.0s) must account for this 1s debounce delay. Current defaults (2.0 > 1.0) satisfy this, but barely -- a tight margin for cross-volume moves.

2. **`_build_disk_state()` returns a tuple, not just a dict.** The second element (`unreadable: set[str]`) is used by `scan()` to exclude unreadable files from the cloud-only delete set (line 98: `cloud_only = cloud_paths - disk_paths - unreadable`). The 3-way reconcile must carry the unreadable set through to the conservative-delete logic.

3. **`_fetch_cloud_state()` returns empty dict on any error** (lines 174-177). If the cloud is unreachable at startup, the daemon treats every file as disk-only and re-uploads everything. After A2 with the cache, this behaviour needs careful handling: an empty cloud response with a populated cache means "cloud unreachable" not "cloud was wiped."

4. **DaemonConfig has `extra="forbid"`.** Adding new fields to the YAML config is safe, but any typo or unknown key in a user's YAML will cause a validation error. This is intentional (fail-fast) but means the A2 config upgrade must be documented for existing users.

5. **`_on_move` in the watcher is NOT debounced** (line 203-204). It calls `self._on_move(old_vp, new_vp)` directly, bypassing debounce. This means native move events fire immediately. For the Move Detective, this is fine -- native moves go straight to `report_moved`, no buffering needed. But it means the move callback and a debounced create callback could race if a move coincides with a rapid create.

---

## Dependencies & Coupling

The daemon package is intentionally isolated from the main codebase.

- **`core/result.py`** -- the only cross-boundary import. `Success` and `Failure` types. Stable.
- **`handlers/registry.py`** -- imported lazily inside `extractor.py:144` for handler dispatch. This is the only coupling to the main handler system.
- **`watchdog>=4.0`** -- external dependency for filesystem watching. On macOS, uses FSEventsObserver.
- **`httpx`** -- async HTTP client used by uploader, event_reporter, scanner.
- **`pydantic`** -- config validation.
- **`PyYAML`** -- config file loading.

A2 adds no new external dependencies. The new `daemon/cache.py` and `daemon/move_buffer.py` are daemon-internal with no cross-boundary imports beyond `core/result`.

---

## Extension Points

The cache module's public interface (7 methods) is designed as a seam: JSON today, SQLite later. This is a genuine extension point -- the interface is simple enough that swapping the backend is a localised change.

The move buffer is a pure in-memory data structure with no persistence. Its 3-method interface (`park_delete`, `match_create`, `expire`) is clean but not a planned extension point.

`DaemonConfig` with `extra="forbid"` means new fields require a code change (no dynamic config). This is appropriate for a Pydantic model but means config additions are code changes, not data changes.

---

## Open Questions

1. **Cloud-unreachable vs cloud-wiped ambiguity.** When `_fetch_cloud_state()` returns `{}` (empty dict), the current code treats every disk file as new and re-uploads. After A2, an empty cloud response + a populated cache is ambiguous: is the cloud unreachable (should skip uploads), or was it genuinely wiped (should re-upload)? The spec does not address this. Checked `scanner.py:174-177` -- the error paths all return `{}` with a log warning. Resolution direction: distinguish HTTP error from successful-but-empty response.

2. **Stat-then-hash cache entry shape.** The spec's Component 5 identifies that stat-then-hash requires storing `(size, mtime)` alongside the content hash, potentially changing the cache shape from `dict[str, str]` to `dict[str, dict]`. This is a planner decision but must be resolved before the cache module is built.

3. **Lock contention measurement.** The spec mentions per-file lock acquisition during periodic scan. Confirmed this is needed: the current `scan()` processes files concurrently via `asyncio.gather` with a semaphore. The cache lock must not block these concurrent tasks. Checked: no measurement exists -- recommend a simple benchmark during implementation.

4. **`scan_batch_size` field on DaemonConfig (line 60, default 50).** This field exists but is NEVER USED in `scanner.py` -- the scan processes all files via `asyncio.gather` with `upload_concurrency` as the only throttle. A2's periodic reconcile should either use `scan_batch_size` or the field should be documented as reserved.

---

## Technical Debt Spotted

1. **TD-SCAN-BATCH: `scan_batch_size` is dead config.** `DaemonConfig.scan_batch_size` (config.py:60, default 50) is defined but never read by `scanner.py`. Either wire it into the scan logic (e.g., batch the `asyncio.gather` calls) or remove it to avoid confusing users who try to tune it. Low priority -- does not affect correctness.

2. **TD-CLOUD-EMPTY: Empty cloud state is ambiguous.** `_fetch_cloud_state()` returns `{}` for both "cloud unreachable" and "cloud has zero documents." After A2, this ambiguity could cause unnecessary re-uploads when the cloud is temporarily down. Low priority for demo (cloud is local), higher priority for production.

---

## Invalidated Assumptions

One assumption was partially invalidated. The invalidation does NOT require a redesign -- it is a documentation/framing correction, not a structural change. The Move Detective is still useful as a safety net for cross-volume moves and OS-level edge cases.

### A8 — macOS FSEvents delete-then-create latency for drag-moves

**Spec claimed:** "macOS FSEvents delivers delete-then-create events for a drag-move within ~2 seconds" (Assumption A8). The spec presents the Move Detective's 2s correlation window as the primary mechanism for handling user drag-moves within the vault.

**Code shows:** The daemon uses `watchdog.observers.Observer` which resolves to `FSEventsObserver` on macOS (verified on this machine). Reading the actual watchdog FSEvents implementation at `.venv/lib/python3.12/site-packages/watchdog/observers/fsevents.py`, lines 225-270:

- **Same-volume moves within the watched tree** produce paired `is_renamed` native events with matching inodes. Watchdog correlates these in the same `queue_events()` callback batch (no timing gap) and emits a single `FileMovedEvent`. The daemon's `on_moved` handler receives this as `(old_vp, new_vp)` -- **no delete-then-create split occurs.**
- **Cross-volume moves** (e.g., drag from internal SSD to USB drive) cannot be tracked by inode across filesystem boundaries. These genuinely produce separate delete + create events and would benefit from the Move Detective's correlation window.
- **Moves out-of or into the watched tree** (if the vault root does not contain both source and destination) also produce separate events, but this is not relevant for intra-vault moves.

For the daemon's primary use case -- a user's vault on a single volume, watched recursively from the root -- **drag-moves within the vault will always produce a single `FileMovedEvent` via the native `on_moved` callback, not a delete-then-create pair.** The 2s `move_window_seconds` is therefore a safety net for edge cases (cross-volume, OneDrive sync artefacts, unusual filesystem behaviours), not the primary move-detection mechanism.

**Why this matters:** The spec and design doc frame the Move Detective as the primary solution for handling user drag-moves. In reality, the watcher's native move detection already handles the common case. This does not break the design -- the Move Detective is still valuable as a fallback -- but it changes the framing: the Move Detective is a safety net, not the main path. This means:
- The 2s default for `move_window_seconds` is adequate (it only needs to catch rare cross-volume scenarios, not common drag-moves).
- Testing should verify that native `FileMovedEvent` events correctly update the cache via `_on_move`, since this is the primary path.
- The spec's "Done when" criteria for Component 6 should include: "A native OS move event updates the cache correctly" as the primary test case, not just "A file dragged... results in one report_moved call."

**Suggested resolution directions:**
1. **Reframe the spec narrative** (recommended): Keep the Move Detective as-is but update the spec's Feature Overview and Component 6 to clarify that native `FileMovedEvent` is the primary move path, and the Move Detective handles the cross-volume/edge-case fallback. No code change needed.
2. **Add explicit documentation** of when the Move Detective fires vs when native move detection handles it, so implementers test both paths.

---

_Research complete. No structural redesign needed. The single partially-invalidated assumption (A8) requires a narrative correction in the spec, not a code change._
