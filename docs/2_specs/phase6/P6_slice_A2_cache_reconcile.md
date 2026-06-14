# Phase 6 Slice A2 — Local Cache + Smart Reconcile

_Spec written: 2026-06-14_
_Input: `docs/1_design/phase6/P6_slice_A2_cache_reconcile.md`_
_Behavior IDs: P6-A2-01 through P6-A2-09 (9 entries, `behavior_inventory.yaml`)_
_Tier: MEDIUM — local-daemon-only, layers onto A1_

---

## Purpose

Today the daemon is forgetful: every time it boots or a file is touched, it re-reads the file from scratch and re-checks it against the cloud. A2 gives the daemon a small private "notebook" (a local cache) so it can skip unchanged files instantly, recognise a moved file instead of deleting and re-uploading it, and recover gracefully if the notebook is ever lost. After A2, the daemon is smarter (skips known-unchanged work), cheaper (moves are one event, not delete + re-capture), and safer (a damaged cache degrades to a full reconcile, never to data loss).

---

## Already built (reuse, do not rebuild)

| Function/Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `DaemonConfig` | `daemon/config.py:26` | Holds daemon settings (debounce, concurrency, retry, ignore patterns) | A2 adds 4 new fields here (`cache_path`, `move_window_seconds`, `periodic_interval_seconds`, `sweep_delete_confirmations`) | shallow — flat Pydantic model, no hidden logic |
| `scan()` | `daemon/scanner.py:66` | Startup reconcile — walks disk, fetches cloud state, uploads/deletes differences | A2 upgrades this from 2-way (disk vs cloud) to 3-way (disk vs cache vs cloud) using the resolution table | deep — hides walk/compare/upload behind one call |
| `ScanResult` | `daemon/scanner.py:39` | Return dataclass: uploaded, re_uploaded, deleted, skipped counts | A2 may extend with `moved` count | shallow |
| `_build_disk_state()` | `daemon/scanner.py` | Walks the vault, hashes files, returns state dict | A2 reuses as-is for the "disk" column of the 3-way compare | deep |
| `_fetch_cloud_state()` | `daemon/scanner.py` | Calls `GET /api/state`, returns cloud state dict | A2 reuses as-is for the "cloud" column of the 3-way compare | deep |
| `extract()` | `daemon/extractor.py:79` | Reads a file from disk, computes SHA-256 `content_hash` | A2 reuses the hash for cache comparison and move-fingerprint matching | deep |
| `TextContent` / `BinaryContent` | `daemon/extractor.py:37,56` | Frozen dataclasses carrying extracted content + hash | A2 reads `.content_hash` from these for cache lookups | shallow |
| `upload_text()` / `upload_binary()` | `daemon/uploader.py:70,112` | Upload content to cloud with retry; returns `Result[int]` | A2 wires cache-on-ack: on `Success`, write fingerprint to cache | deep |
| `report_moved()` | `daemon/event_reporter.py:31` | Reports a move event to the cloud with retry | A2 calls this when the Move Detective matches a delete to a create | deep |
| `report_deleted()` | `daemon/event_reporter.py:70` | Reports a delete event to the cloud with retry | A2 calls this after the move-correlation window expires with no match, or after conservative-delete confirmation | deep |
| `should_skip_path()` | `daemon/watcher.py:36` | Shared ignore-pattern filter (dotfiles, patterns from config) | A2 reuses as-is; no changes | deep |
| `_DaemonEventHandler` | `daemon/watcher.py:87` | Debounces filesystem events via `threading.Timer` | A2 does not modify; the move buffer sits after debounce | deep |
| `DaemonWatcher` | `daemon/watcher.py:220` | Wraps watchdog Observer; delegates to event handler | A2 does not modify | deep |
| `retry_with_backoff()` | `daemon/_http_retry.py:33` | Exponential backoff with transient-status retry | A2 uses indirectly via uploader + event_reporter | deep |
| `start` command | `daemon/cli.py:148` | Click command: runs scan, creates watcher, enters main loop | A2 modifies: adds cache load/save, move buffer, periodic timer, bail-early check | deep |
| `_on_create_or_modify()` | `daemon/cli.py:182` | Live handler: extract + upload on file change | A2 adds bail-early (check cache before extract) and cache-on-ack after upload | shallow — callback, logic inline |
| `_on_move()` | `daemon/cli.py:204` | Live handler: reports a native move | A2 adds cache update (forget old path, write new path on ack) | shallow |
| `_on_delete()` | `daemon/cli.py:215` | Live handler: reports a delete immediately | A2 replaces with: buffer the delete in the Move Detective instead of reporting immediately | shallow |

---

## Q1 Diagram (from design)

```
# Local Cache (JSON Manifest) — What Happens Inside
Scope: Shows how the daemon's local "notebook" loads, stays in sync while
       running, and saves itself crash-safely. Does NOT show the 3-way
       compare logic (that reads/writes this cache; see the reconcile flow).

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

              Daemon starts
                   │
                   ▼
        ┌────────────────────────┐
        │ Read the cache file     │
        │ (one map: each note's   │
        │  location → fingerprint)│
        └───────────┬────────────┘
                    │
         ┌──────────┴───────────┐
         │                      │
   File is good           Missing / garbled
         │                      │
         ▼                      ▼
  ┌──────────────┐      ┌──────────────────┐
  │ Load it into │      │ Start empty —    │
  │ memory       │      │ discard & rebuild│
  └──────┬───────┘      │ from the cloud   │
         │              └────────┬─────────┘
         └────────┬──────────────┘
                  ▼
        ┌──────────────────────────┐
        │ While running: watcher &  │
        │ reconcile share ONE in-   │
        │ memory map, behind one    │
        │ lock (no corruption)      │
        └───────────┬──────────────┘
                    │ cloud confirms
                    │ it got the file
                    ▼
        ┌──────────────────────────┐
        │ Save: write a temp file,  │
        │ then rename it over the   │
        │ real file (crash-safe)    │
        └──────────────────────────┘
```

---

## Q2 Diagram — How it connects to others

```
# Local Cache + Smart Reconcile — How It Connects to Others
Scope: Shows what the Local Notebook feature touches — both live-path
       (File Watcher) and batch-path (Startup Reconciler).
       Does NOT show internal cache steps (see Q1 for that).

How to read this:
  Center box     = the feature being built (A2)
  Solid boxes    = components that already exist (built in A1)
  Dashed boxes   = new in A2
  Arrow labels   = what passes between them

                  ┌────────────────────┐
                  │ User's Filing      │
                  │ Cabinet            │
                  │ Notes on the Mac   │
                  └────────┬───────────┘
                           │ file changes
                           │ (create/modify/
                           │  delete/move)
                           ▼
  ┌────────────────┐    ┌──────────────────┐    ┌────────────────┐
  │ Content Reader │◄───┤  File Watcher    ├───►│ Move Detective │
  │ Reads and      │    │  Monitors the    │    │ Buffers deletes│
  │ fingerprints   │    │  filing cabinet  │    │ and matches    │
  │ files          │    │  for changes     │    │ with creates   │
  └───────┬────────┘    └──────────────────┘    └───────┬────────┘
          │ fingerprint                                 │ move or
          │ to compare                                  │ confirmed
          ▼                                             │ delete
  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┤
  │                                                     │
  │          ┌─────────────────────────┐                │
  │          │    LOCAL NOTEBOOK       │                │
  │          │    Remembers what the   │◄───────────────┘
  │          │    daemon already sent  │
  │          │    to the cloud         │
  │          └─────────┬──┬────────────┘
  │                    │  │
  │    reads cached ───┘  └─── writes fingerprint
  │    fingerprints           after cloud confirms
  │          │
  └ ─ ─ ─ ─ ┼ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
             │
             ▼
  ┌──────────────────┐                  ┌──────────────────┐
  │ Startup          │  uploads and     │ Cloud Office     │
  │ Reconciler       │  events via      │ The single       │
  │ 3-way compare:   ├─────────────────►│ source of truth  │
  │ disk vs notebook │  Cloud Sender    │ (cloud database) │
  │ vs cloud         │  + Event         │                  │
  │                  │◄─────────────────┤ Returns current  │
  │                  │  Messenger       │ state on request │
  └────────┬─────────┘                  └──────────────────┘
           │
           │ re-triggered periodically
           │
  ┌ ─ ─ ─ ┴ ─ ─ ─ ─ ┐
  │ Scheduled Audit   │
  │ Periodic timer    │
  │ catches missed    │
  │ files             │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─┘
```

Diagram Notes:
- **Local Notebook** (center, new in A2): In-memory map of location-to-fingerprint, backed by a crash-safe JSON file. Both File Watcher and Startup Reconciler read/write it behind one shared lock.
- **File Watcher** (existing A1): Monitors the filing cabinet. Now checks the Local Notebook before uploading (bail-early). Passes deletes to Move Detective instead of reporting immediately.
- **Move Detective** (new in A2): Holds a deleted file's fingerprint from the Local Notebook; if a matching create arrives within a short window, emits one move event instead of a delete + re-upload.
- **Content Reader** (existing A1): Fingerprints files with SHA-256. Unchanged; reused by both live path and Startup Reconciler.
- **Startup Reconciler** (existing A1, upgraded): Was 2-way (disk vs cloud). Now 3-way (disk vs notebook vs cloud) using a 9-row Decision Rulebook. Rebuilds the notebook after every run.
- **Cloud Office** (existing): The cloud database — always wins disagreements. The notebook is rebuilt from it on any doubt.
- **Scheduled Audit** (new in A2): Periodic timer that re-runs the Startup Reconciler to catch files the File Watcher missed.
- Cloud Sender, Event Messenger, and Retry Helper collapsed into arrow labels to stay within spoke limits.

---

## Feature overview

### The happy path

When the daemon starts, it loads its notebook (a small JSON file mapping each note's location to its fingerprint). If the notebook is missing or damaged, it starts with an empty memory and rebuilds from the cloud — this is safe because the cloud is always the authority.

On boot, the Startup Reconciler runs a 3-way compare: for every file, it checks what is on disk, what the notebook remembers, and what the cloud knows. A 9-row Decision Rulebook resolves every combination (see Resolution Table below). After the reconcile finishes, the notebook is rebuilt to exactly mirror the resolved truth.

While running, the File Watcher monitors the user's filing cabinet. When a file is created or modified, the daemon first checks the notebook: if the file's fingerprint matches what was already sent, it bails early — no extraction, no upload. If the fingerprint differs (or is not in the notebook), it extracts, uploads, and on cloud confirmation writes the new fingerprint to the notebook (cache-on-ack).

When a file is deleted, the daemon does NOT report it immediately. Instead, the Move Detective looks up the deleted file's fingerprint in the notebook (the file is gone from disk, so the notebook is the only surviving source of its hash) and parks it in a buffer. If a new file with the same fingerprint appears within a short window (default 2 seconds), the pair is reported as a single move — the cloud keeps the file's summary and knowledge, only updating its location. If the window expires with no match, the delete is reported normally.

Periodically (default every 6 hours), the Scheduled Audit re-runs the Startup Reconciler to catch files the File Watcher may have missed (OS file-watchers genuinely drop events under rapid activity).

### The resolution table (9 cases)

For each path, the reconciler knows three facts: is it on disk (and its hash), is it in the cache (and its hash), is it in the cloud (and its hash).

| Disk | Cache | Cloud | Meaning | Action |
|---|---|---|---|---|
| Present | -- | -- | Brand-new file | **Upload**, then cache-on-ack |
| Present | Present | -- | On disk + cache but cloud lost it (rollback) | **Re-upload**, then cache-on-ack (P6-A2-07) |
| Present | -- | Same hash | Cloud has it, cache was lost/rebuilding | **Skip upload**; write fingerprint into cache |
| Present | Present | All equal | Steady state, unchanged | **Skip** (the common fast path) |
| Present | * | Different hash | Content changed on disk | **Re-upload**, then cache-on-ack |
| -- | Present | Present | File gone from disk; cloud + cache still have it | **Candidate delete** — confirm across sweeps (P6-A2-06) |
| -- | Present | -- | Gone from disk + cloud; only stale cache entry | **Drop stale cache entry**, no cloud call |
| -- | -- | Present | Cloud-only, never in this cache | **Candidate delete** — same sweep confirmation (P6-A2-06) |
| Present | Present | Cloud=None | Cloud stored no fingerprint (pre-P5 data) | **Re-upload**, then cache-on-ack |

### Edge cases

- **Damaged notebook**: any parse error, missing file, non-dict root, or malformed entry resets the in-memory map to empty. The daemon degrades to a full disk-vs-cloud reconcile (the A1 behaviour). No partial parse, no per-entry salvage.
- **Crash mid-save**: the notebook is saved via temp-file-then-atomic-rename. A crash mid-save lands back at the "missing/garbled" branch on next boot — safe.
- **Conservative deletes (sweep path)**: a file missing in one sweep is recorded as a candidate. Only after it stays gone across `sweep_delete_confirmations` (default 2) consecutive sweeps is the delete reported. A reappearing file clears the candidate.
- **Lock contention**: one `threading.Lock` guards the in-memory map and the move buffer. The periodic reconcile acquires the lock per-file (not for the whole scan) so live uploads are not blocked during a large reconcile.

### Default timings

All settings live on `DaemonConfig` — none are hardcoded in flow logic.

| Setting | Default | Rationale |
|---|---|---|
| `debounce_seconds` (existing) | 1.0 | Keep A1's value. Coalesces editor save-storms. |
| `move_window_seconds` (new) | 2.0 | Long enough for drag-move split events, short enough for responsive deletes. Must exceed `debounce_seconds`. |
| `upload_concurrency` (existing) | 4 | Keep A1's value. Caps parallel uploads. |
| `periodic_interval_seconds` (new) | 21600 (6h) | Defense-in-depth against dropped watcher events. `0` disables. |
| `sweep_delete_confirmations` (new) | 2 | Minimum to distinguish transient blip from real delete. |

---

## Out of scope

- **OneDrive Files-On-Demand placeholder handling** — deferred as TD-061. A2 keeps the "unreadable file excluded from delete set" carve-out as the future seam, but does not detect or handle online-only placeholders.
- **PyInstaller .app / first-run wizard / tray icon** — Phase 6 Slice B.
- **Vision-describe + blob persistence** — Phase 7.
- **Any cloud-side change** — A2 touches `src/daemon/` only. `GET /api/state` and `/api/event` already shipped in A1.
- **SQLite cache backend** — documented as the upgrade path behind the cache interface, but not built in A2 (JSON is sufficient at personal-vault scale).
- **Multi-device cache coordination** — out of scope; each device has its own independent notebook.

---

## Constraints

- **C-18 (Daemon Sync)** — Cache is advisory; cloud is authority; cache loss/corruption MUST be non-fatal (degrade to full reconcile, never data loss); cache-on-ack corollary (write fingerprint only after cloud 200). Source: `CONSTRAINTS.md`, ADR-0013.
- **C-10 (Async & CLI)** — The periodic reconcile timer MUST live inside the existing `asyncio.run(_run())` loop in `daemon/cli.py::start` — no second event loop. Source: `CONSTRAINTS.md`.
- **C-12 (Result convention)** — New public functions in the cache module SHOULD return `Result` for consistency with the daemon's existing convention (not hook-enforced since daemon is not in `handlers/` or `pipelines/`). Source: `CONSTRAINTS.md`.
- **C-11 (load_dotenv)** — NOT applicable to new cache code; the daemon reads `KMS_DAEMON_API_KEY` via `os.environ.get` directly. Source: `CONSTRAINTS.md`.
- **C-13 (Audit log)** — NOT applicable; the daemon makes zero AI decisions. Source: design doc guardrail checklist.
- **threading.Lock, not asyncio.Lock** — The watcher runs in a watchdog thread and bridges to the asyncio loop via `run_coroutine_threadsafe`. The cache lock must be a `threading.Lock` (taken from both the watcher thread and async tasks). Source: design doc risk analysis.

---

## Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|---|---|---|---|
| A1 | `_build_disk_state()` in `scanner.py` returns a `dict[str, str]` mapping `vault_path` to `content_hash`, compatible with the cache's `dict[str, str]` shape | Implication 2 (3-way compare) | If `_build_disk_state` returns a different shape (e.g., `set` of paths, or hash includes metadata beyond raw bytes) |
| A2 | `_fetch_cloud_state()` returns `dict[str, str or None]` where `None` means "cloud stored no fingerprint" | Implication 2 (3-way compare) | If `None` has a different meaning, or if the cloud never returns `None` for hash |
| A3 | `extract().content_hash` produces the same SHA-256 as `_build_disk_state()` for the same file — both hash raw bytes | Implication 1 (bail-early) | If `extract()` hashes a transformed/cleaned version of the content rather than raw bytes |
| A4 | `threading.Timer` in `_DaemonEventHandler` fires the callback in a thread-pool thread, not in the asyncio event loop | Implication 4 (move buffer interaction with debounce) | If the Timer callback runs inside the event loop (would deadlock if taking a `threading.Lock` that the loop also holds) |
| A5 | `run_coroutine_threadsafe()` in `_on_create_or_modify` (`cli.py:202`) correctly bridges the watcher thread to the asyncio loop, and the cache lock can be taken from either side | Implication (concurrency) | If the bridge mechanism changes or if taking a `threading.Lock` from within `run_coroutine_threadsafe` causes a deadlock |
| A6 | The `GET /api/state` endpoint returns ALL documents, not just a page — needed for the full 3-way compare | Implication 2 (3-way compare) | If the endpoint paginates and `scan()` only fetches page 1 |
| A7 | `os.rename()` (or `pathlib.Path.rename()`) is atomic on macOS APFS/HFS+ for files within the same filesystem | Implication (crash-safe save) | If the vault is on a non-POSIX filesystem (e.g., FAT32 external drive) where rename is not atomic |
| A8 | ~~macOS FSEvents delivers delete-then-create events for a drag-move within ~2 seconds~~ **CORRECTED by research:** Same-volume intra-vault moves produce a single `FileMovedEvent` via watchdog's FSEvents inode correlation — the existing `_on_move` handler catches these directly. The Move Detective's 2s correlation window is a **safety net** for cross-volume moves and edge cases where watchdog cannot correlate the inode, not the primary move-detection path. | Implication 7 (verified by research) | N/A — research resolved this; Move Detective role reframed as safety net |

---

## Component dependency order

### 1. Cache module — the Local Notebook

**Goal.** Give the daemon persistent memory of what it already sent to the cloud, so it can skip unchanged files and remember deleted files' fingerprints for move detection.

**Build.** Create `daemon/cache.py`. The module holds an in-memory `dict[str, str]` (`vault_path` to `content_hash`) guarded by a `threading.Lock`. Public interface:

- `load(path)` — read JSON from disk into memory. On any error (missing file, parse error, non-dict root), reset to empty dict and log a warning. Never raise.
- `save(path)` — write the current map to a temp file, then atomic-rename over the real file. Never raise on write failure (log + continue — the cache is advisory).
- `get(vault_path)` — return the cached fingerprint, or `None` if not present. Takes the lock.
- `set_after_ack(vault_path, content_hash)` — write a fingerprint. Called only after cloud confirms. Takes the lock.
- `forget(vault_path)` — remove an entry. Takes the lock.
- `snapshot()` — return a frozen copy of the entire map (for the 3-way compare to iterate without holding the lock for the whole scan).
- `rebuild(entries)` — replace the entire map with a new dict (post-reconcile rebuild). Takes the lock.

The module is a deep module: a tiny interface hiding atomic-write + corruption-discard + locking implementation. The interface is a real seam (2 adapters: JSON today, SQLite as documented upgrade path).

**Depends on.** None (standalone module).

**Assumes.** A7 (atomic rename on macOS).

**Interface shape.** Callers see 7 methods above. Hidden: JSON serialization, temp-file mechanics, lock acquisition, corruption handling. 2-adapter seam (JSON now, SQLite later).

**Dependency category.** In-process (test directly with temp files).

**Done when.** Loading a valid JSON cache file populates memory correctly. Loading a garbled file starts the daemon with an empty memory and a warning log. Saving writes a file that survives a simulated crash (process kill mid-save leaves either the old file or the new file, never a partial file). Concurrent `get` and `set_after_ack` calls from multiple threads do not corrupt the map.

---

### 2. Config additions — new timing and path settings

**Goal.** Let an admin tune cache location and timing defaults without touching code.

**Build.** Add 4 fields to `DaemonConfig` in `daemon/config.py`:

- `cache_path: str` — default `~/.kms-daemon/cache.json`. Where the notebook file lives.
- `move_window_seconds: float` — default `2.0`. How long to wait for a matching create after a delete.
- `periodic_interval_seconds: int` — default `21600` (6 hours). How often to re-run the reconcile. `0` disables.
- `sweep_delete_confirmations: int` — default `2`. How many consecutive sweeps a file must be absent before reporting a delete.

Add validation: `move_window_seconds` must be > `debounce_seconds`. `sweep_delete_confirmations` must be >= 1.

**Depends on.** None (standalone config change).

**Done when.** A config YAML with custom values for all 4 fields loads without error. A config YAML omitting these fields uses the documented defaults. Setting `move_window_seconds` <= `debounce_seconds` raises a validation error.

---

### 3. Move Detective — the move-correlation buffer

**Goal.** Detect when a delete-then-create is actually a file move, so the cloud keeps the file's existing summary instead of paying for a fresh AI pass. _Research note (A8 correction): same-volume intra-vault moves produce a single `FileMovedEvent` caught by `_on_move` directly. The Move Detective is a **safety net** for cross-volume moves and edge cases where watchdog cannot correlate the inode — not the primary move-detection path._

**Build.** Create `daemon/move_buffer.py`. The module holds an in-memory buffer of pending deletes: `dict[str, (vault_path, timestamp)]` keyed by content fingerprint. Public interface:

- `park_delete(fingerprint, vault_path)` — record a delete candidate with the current timestamp.
- `match_create(fingerprint)` — if a parked delete with this fingerprint exists and the window has not expired, remove it from the buffer and return the old `vault_path`. Otherwise return `None`.
- `expire(move_window_seconds)` — return and remove all entries older than the window. Each expired entry becomes a confirmed delete.

The buffer does NOT manage its own timer — the caller (cli.py wiring) manages the timer. The buffer is a pure data structure with time-aware expiry.

The buffer shares the same `threading.Lock` as the cache module. The lock instance is passed in at construction or shared via a common holder.

**Depends on.** Component 1 (cache module — needs the shared lock; needs `cache.get()` to look up the deleted file's fingerprint, since the file is gone from disk).

**Assumes.** A4 (Timer thread compatibility), A8 (FSEvents latency within window).

**Interface shape.** 3 methods above. Hidden: buffer dict, timestamp comparison. Single adapter (in-memory only — no persistence needed; pending deletes are short-lived).

**Dependency category.** In-process (test directly).

**Decisions.**
- Q: Should the lock be owned by the cache module and borrowed by the buffer, or should both borrow from a shared lock holder? Leaning shared holder — avoids the cache module knowing about the buffer. Defer to planner.

**Done when.** A parked delete is matched by a create with the same fingerprint within the window — returns the old path. A parked delete that expires (window passes) is returned by `expire()` as a confirmed delete. A create with a fingerprint that does not match any parked delete returns `None`. Two concurrent `park_delete` + `match_create` calls from different threads do not corrupt the buffer.

---

### 4. 3-way reconcile upgrade — the Decision Rulebook

**Goal.** Upgrade the startup scan from a 2-way diff (disk vs cloud) to a 3-way referee (disk vs cache vs cloud) that resolves every combination correctly, including cloud rollbacks and stale cache entries.

**Build.** Modify `daemon/scanner.py::scan()`:

- Accept the cache (a snapshot from Component 1) as a third input alongside disk state and cloud state.
- Replace the 2-set comparison logic (`scanner.py:96-98`) with the 9-row resolution table from the Feature Overview.
- Implement conservative deletes: track a "seen-missing" set; a cloud-known path absent on disk is a candidate on first sweep, confirmed only after `sweep_delete_confirmations` consecutive sweeps.
- After the scan completes, rebuild the cache to exactly mirror the resolved truth (what the cloud now holds + what was just uploaded).
- Unreadable files on disk remain excluded from the cloud-only delete set (existing A1 carve-out at `scanner.py:98,247`).

The `ScanResult` dataclass gains a `moved: int` count field.

**Depends on.** Component 1 (cache module — provides the cache snapshot), Component 2 (config — provides `sweep_delete_confirmations`).

**Assumes.** A1, A2, A3, A6.

**Done when.** A brand-new file (on disk, not in cache, not in cloud) is uploaded and cached on ack. An unchanged file (all three match) is skipped. A file present on disk + cache but missing from cloud (rollback scenario) is re-uploaded. A file missing from disk but present in cache + cloud becomes a candidate delete, not an immediate delete. After two consecutive sweeps with the file still missing, the delete is reported. A stale cache entry (file gone from disk and cloud) is silently dropped.

---

### 5. Bail-early check — live path optimization

**Goal.** On the live path (file created or modified), skip extraction and upload when the file's fingerprint matches what the cache already holds — the most common case when the daemon is running and files are being saved.

**Build.** Modify `_on_create_or_modify()` in `daemon/cli.py`:

- Before calling `extract()`, do a pre-check: compute the file's fingerprint (or use stat-then-hash pre-filter — see Decisions below) and compare it to `cache.get(vault_path)`.
- If the fingerprint matches the cache: skip the file entirely (log at debug level). This is the bail-early path (P6-A2-01).
- If the fingerprint differs or is not in the cache: proceed with extract + upload as today, then `cache.set_after_ack()` on success.

**Depends on.** Component 1 (cache module — provides `get` and `set_after_ack`), Component 2 (config).

**Assumes.** A3 (extract hash matches disk-state hash), A5 (thread-bridge compatibility).

**Decisions.**
- Q: Use stat-then-hash pre-filter or hash-always for the bail-early check? **Locked: stat-then-hash as pre-filter.** Cheap size/mtime glance first; only hash when stat changed. The hash still decides — stat is a pre-filter, not a replacement. (OQ-A2-1 locked.) Cache entry shape may need to store `(content_hash, size, mtime)` instead of just `content_hash` — defer exact shape to planner.
- [RESEARCH MUST VERIFY] The stat-then-hash pre-filter requires storing `(size, mtime)` alongside the content hash in the cache entry. Confirm this does not break the JSON manifest's `dict[str, str]` shape — may need `dict[str, dict]` or `dict[str, str]` with a separate stat cache.

**Done when.** When a file is saved but its content is unchanged, the daemon logs "skipped (unchanged)" and makes no cloud call. When a file is genuinely modified, the daemon extracts, uploads, and updates the cache. The pre-filter (stat check) correctly avoids hashing a 50MB file that was only touched (mtime changed, content unchanged).

---

### 6. Move detection wiring — connecting the Move Detective to the live path

**Goal.** Wire the Move Detective into the live watcher callbacks so that a delete-then-create pair becomes a single move event.

**Build.** Modify `daemon/cli.py`:

- `_on_delete(vp)`: instead of calling `report_deleted()` immediately, look up the file's fingerprint in the cache (`cache.get(vp)`), then call `move_buffer.park_delete(fingerprint, vp)`. Start or refresh a timer for `move_window_seconds`.
- `_on_create_or_modify(vp)`: after extraction, before uploading, check `move_buffer.match_create(fingerprint)`. If matched (returns `old_vp`): call `report_moved(old_vp, vp)` instead of uploading; on ack, `cache.forget(old_vp)` + `cache.set_after_ack(vp, fingerprint)`. If not matched: proceed with normal upload + cache-on-ack.
- `_on_move(old_vp, new_vp)`: add `cache.forget(old_vp)` + `cache.set_after_ack(new_vp, fingerprint)` after `report_moved` succeeds. (Native moves already work; this adds cache bookkeeping.)
- Timer expiry handler: when the move window expires, call `move_buffer.expire()`, then for each expired entry call `report_deleted(vp)` + `cache.forget(vp)` on ack.

**Depends on.** Component 1 (cache), Component 3 (move buffer), Component 5 (bail-early check — the create-path must check the buffer before deciding to upload).

**Assumes.** A4, A5, A8.

**Done when.** A file dragged from `Projects/Alpha/` to `Projects/Beta/` (OS reports as delete + create) results in one `report_moved` call to the cloud, not a delete + re-upload. The cache updates from old path to new path. A genuine delete (no matching create within 2 seconds) results in one `report_deleted` call. A native OS move event (single `moved` event) still works and updates the cache.

---

### 7. Periodic reconcile timer — the Scheduled Audit

**Goal.** Re-run the 3-way reconcile periodically while the daemon is running, catching files stranded by dropped watcher events.

**Build.** Add an `asyncio` task inside the existing `start` command's `_run()` async function in `daemon/cli.py`:

- If `config.periodic_interval_seconds > 0`: create an asyncio task that sleeps for the interval, then calls `scan()` (the upgraded 3-way version from Component 4). Loop until cancelled.
- The periodic scan takes the cache lock per-file (not for the whole scan) so live events are not blocked.
- Cancel the task on daemon shutdown (existing signal handling in `cli.py`).

This task MUST live inside the existing `asyncio.run(_run())` loop (C-10 compliance). No second event loop.

**Depends on.** Component 2 (config — provides `periodic_interval_seconds`), Component 4 (3-way reconcile — the scan logic being re-run).

**Done when.** With `periodic_interval_seconds=10` (for testing), the daemon re-runs the reconcile every 10 seconds while running. A file created while the watcher was temporarily overwhelmed is picked up by the periodic reconcile. Setting `periodic_interval_seconds=0` disables the periodic reconcile entirely.

---

### 8. Cache-on-ack wiring — tying it all together

**Goal.** Ensure every successful cloud interaction (upload, move report, delete report) updates the cache, and every failed interaction leaves the file uncached for retry.

**Build.** This is integration wiring across Components 5, 6, and 7 — not a new module. Verify that:

- Every `upload_text()` / `upload_binary()` Success path calls `cache.set_after_ack(vp, hash)`.
- Every `report_moved()` Success path calls `cache.forget(old_vp)` + `cache.set_after_ack(new_vp, hash)`.
- Every `report_deleted()` Success path calls `cache.forget(vp)`.
- Every Failure path does NOT touch the cache — the file stays uncached so the next reconcile re-detects it.
- `cache.save()` is called after each batch of acks (not after every single ack — batch the saves to avoid JSON rewrite storms during a large reconcile).

**Depends on.** Components 1, 4, 5, 6, 7 (all must be wired).

**Done when.** After a successful upload, the file's fingerprint appears in the cache on disk. After a failed upload (simulated 500), the file does NOT appear in the cache. After a successful move report, the old path is gone from the cache and the new path is present. After a successful delete report, the path is gone from the cache.

---

## Handoff notes

- **Contract with Phase 6 Slice B (packaging):** A2 delivers a working cache + reconcile that Slice B packages into the `.app`. No new CLI flags or user-facing config changes are needed for Slice B — the defaults work out of the box.

- **Contract with Phase 7 (vision-describe + blob):** A2's cache stores `vault_path → content_hash` for files. Phase 7 may add blob-reference metadata alongside the hash. The JSON manifest's value shape can grow from a string to a small object without a migration (noted in design doc "Not painting A2 into a corner for TD-061").

- **Open uncertainty — stat-then-hash cache shape:** The bail-early pre-filter (OQ-A2-1 resolution) requires storing `(size, mtime)` alongside the content hash. This may change the cache entry shape from `str` to `dict`. Research should decide the exact shape and confirm it does not break the "grow from string to object" upgrade path for TD-061.

- **Open uncertainty — lock holder pattern:** Whether the `threading.Lock` is owned by the cache module, the move buffer, or a shared holder is left to the planner. All three access the same lock; the ownership pattern is an implementation detail.

- **Suggested research:**
  1. **[MUST VERIFY] macOS FSEvents delete-then-create latency for drag-moves vs 2s `move_window_seconds` default.** Measure on the demo Mac. If latency exceeds ~2s, raise the default. (Source: design doc `[UNVERIFIED]` flag, Assumption A8.)
  2. **Stat-then-hash pre-filter shape.** Decide whether the cache stores `(hash, size, mtime)` as a dict value or keeps the flat `str` and stores stat data separately. Confirm the chosen shape works for TD-061's future expansion.
  3. **Per-file lock contention under load.** Confirm that per-file lock acquisition during periodic scan does not serialise so aggressively that live uploads stall during a large-vault reconcile. Consider yielding patterns.
  4. **`threading.Lock` across `run_coroutine_threadsafe` boundary.** Confirm no deadlock when the cache lock is taken from both the watcher thread (via `threading.Timer` callbacks) and from within `run_coroutine_threadsafe` coroutines on the asyncio loop.

---

## Locked open questions (from design)

| OQ | Decision | Rationale |
|---|---|---|
| OQ-A2-1 | **Stat-then-hash as pre-filter** for bail-early | Standard speed win; stays correct because the fingerprint still decides. Stat is a pre-filter, not a replacement. |
| OQ-A2-2 | **`cache_path` field on DaemonConfig**, defaulting to `~/.kms-daemon/cache.json` | Invisible default, tinkering layer underneath — matches the project's config philosophy. |
| OQ-A2-3 | **Window-only for live deletes; two-sweep for sweep path** | Live deletes are high-signal (user just did it); sweep-found absences are low-signal (could be transient). Different risk profiles warrant different guards. |
| OQ-A2-4 | **Per-file lock acquisition** during periodic scan | Avoids blocking live uploads during a large-vault reconcile. Scan reconciles against a start-of-scan snapshot and writes results back per file. |

---

_Spec written. Run `/research` to verify spec assumptions against real code before planning._
