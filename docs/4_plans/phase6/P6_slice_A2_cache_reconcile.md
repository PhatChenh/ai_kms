# Plan: Phase 6 Slice A2 — Local Cache + Smart Reconcile
_Last updated: 2026-06-14_
_Status: [~] in progress_

---

## Architecture

### Q1 — What happens inside
See design doc: `docs/1_design/phase6/P6_slice_A2_cache_reconcile.md` under "## Q1 Diagram".
Shows JSON manifest internal flow: daemon starts, reads cache file, forks on good/missing, loads into memory or discards and rebuilds, watcher and reconcile share one in-memory map behind one lock, cloud confirms, saves via temp-file-then-atomic-rename.

### Q2 — How it connects
See spec doc: `docs/2_specs/phase6/P6_slice_A2_cache_reconcile.md` under "## Q2 Diagram".
Shows hub-and-spoke: User's Filing Cabinet feeds File Watcher, which connects to Content Reader (fingerprints files), Local Notebook (center, new), and Move Detective (new). Startup Reconciler (upgraded to 3-way) reads the notebook and talks to Cloud Office. Scheduled Audit periodically re-triggers the Reconciler.

### Q3 — Why build it this way

```
# Local Cache + Smart Reconcile — Why This Way
Scope: Rules, constraints, and existing patterns that shaped this design.
       Does NOT show internal steps (see Q1) or connections (see Q2).

How to read this:
  Center box        = the feature being built (A2)
  Surrounding boxes = rules it must follow and why
  Lines             = which rule applies where

  ┌─────────────────────────────────┐
  │ C-18: Cache is advisory         │
  │ Cloud always wins disagreements │
  │ Cache loss = full reconcile,    │
  │ never data loss (ADR-0013)      │
  └──────────────┬──────────────────┘
                 │ governs every
                 │ cache read/write
                 │
  ┌──────────────┴──────────────────────────────────────────────┐
  │                                                             │
  │  ┌──────────────────┐    ┌───────────────────────────────┐  │
  │  │ JSON manifest    │    │ One threading.Lock             │  │
  │  │ (not SQLite)     │    │ (not asyncio.Lock)             │  │
  │  │                  │    │                                │  │
  │  │ Why: <5K files,  │    │ Why: watcher thread +          │  │
  │  │ disposable by    │    │ asyncio loop both access       │  │
  │  │ design, 2-adapter│    │ the cache; threading.Timer     │  │
  │  │ seam for future  │    │ callbacks can't take           │  │
  │  │ SQLite upgrade   │    │ asyncio.Lock                   │  │
  │  └────────┬─────────┘    └──────────────┬────────────────┘  │
  │           │                             │                   │
  │           │   ┌──────────────────────┐  │                   │
  │           └──►│  LOCAL NOTEBOOK +    │◄─┘                   │
  │               │  SMART RECONCILE    │                       │
  │           ┌──►│                     │◄─┐                    │
  │           │   └──────────┬──────────┘  │                    │
  │           │              │             │                    │
  │  ┌────────┴─────────┐   │   ┌─────────┴──────────────┐    │
  │  │ Cache-on-ack     │   │   │ Stat-then-hash         │    │
  │  │                  │   │   │ pre-filter              │    │
  │  │ Why: C-18        │   │   │                         │    │
  │  │ corollary —      │   │   │ Why: cheap size/mtime   │    │
  │  │ failed upload    │   │   │ glance avoids re-reading│    │
  │  │ must leave file  │   │   │ large files; hash still │    │
  │  │ uncached for     │   │   │ decides (OQ-A2-1)       │    │
  │  │ retry            │   │   │                         │    │
  │  └──────────────────┘   │   └─────────────────────────┘    │
  │                         │                                   │
  └─────────────────────────┼───────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
  ┌─────────┴──────────┐    │    ┌──────────┴──────────────┐
  │ 3-way reconcile    │    │    │ Move Detective is       │
  │                    │    │    │ a safety net             │
  │ Why: cloud         │    │    │                          │
  │ rollbacks from     │    │    │ Why: same-volume moves   │
  │ Litestream silently│    │    │ produce one native event │
  │ desync — cache is  │    │    │ via OS inode tracking;   │
  │ third witness;     │    │    │ buffer catches only      │
  │ 9-row rulebook     │    │    │ cross-volume/edge cases  │
  │ resolves every     │    │    │ (research A8)            │
  │ combination        │    │    │                          │
  └────────────────────┘    │    └──────────────────────────┘
                            │
            ┌───────────────┴───────────────┐
            │                               │
  ┌─────────┴──────────┐    ┌───────────────┴──────────────┐
  │ Conservative       │    │ Periodic timer inside        │
  │ deletes            │    │ existing event loop           │
  │                    │    │                               │
  │ Why: sweep-found   │    │ Why: C-10 — no second        │
  │ absences are low-  │    │ event loop; simple asyncio    │
  │ signal (transient  │    │ task that sleeps + re-runs    │
  │ glitch, placeholder│    │ the same reconcile            │
  │ ); live deletes    │    │                               │
  │ use move window    │    │                               │
  │ (high-signal)      │    │                               │
  │ (OQ-A2-3)          │    │                               │
  └────────────────────┘    └──────────────────────────────┘
```

Diagram Notes:
- **C-18 (top):** The cache is advisory; cloud is authority; cache loss is non-fatal. This is the single most important constraint — every cache design decision derives from it.
- **JSON manifest:** Personal-vault scale makes SQLite overhead unjustified. The cache interface is a real 2-adapter seam, so SQLite upgrade is a localized module swap later.
- **One threading.Lock:** The watcher runs in a watchdog thread and bridges to the asyncio loop via `run_coroutine_threadsafe`. Both sides need the cache lock. `asyncio.Lock` cannot be taken from `threading.Timer` callbacks — `threading.Lock` is correct.
- **Cache-on-ack:** Write fingerprint only after cloud returns 200. A failed upload leaves the file uncached so the next reconcile re-detects it. Optimistic caching was rejected.
- **Stat-then-hash:** Cheap size/mtime glance avoids re-reading large files on every save. The hash still decides — stat is a pre-filter, not a replacement.
- **3-way reconcile:** Cloud rollbacks (Litestream restore) silently desync without the cache as a third witness. The 9-row Decision Rulebook resolves every disk/cache/cloud combination.
- **Move Detective as safety net:** Same-volume intra-vault moves produce a single `FileMovedEvent` via watchdog FSEvents inode correlation. The Move Detective's 2s correlation window catches only cross-volume moves and edge cases where watchdog cannot correlate the inode.
- **Conservative deletes:** Sweep-found absences are low-signal. Live deletes use the move-correlation window (high-signal — user just did it).
- **Periodic timer inside existing loop:** C-10 says no second event loop. The timer is a simple asyncio task inside the existing `asyncio.run(_run())`.

---

## Approach

A2 gives the daemon a persistent memory (the "Local Notebook") so it skips unchanged files, detects moved files, and recovers gracefully from cache loss. The build is bottom-up: standalone cache module first (no dependencies), then config, then the move buffer (needs the cache lock), then the 3-way reconcile upgrade in the scanner, then the live-path wiring in the CLI, and finally integration verification. Each phase is independently testable. The cache interface is a real seam — JSON today, SQLite later — so the plumbing never needs to change when the backend upgrades.

**Lock ownership decision (resolved from spec's open question):** The `threading.Lock` is owned by a lightweight shared holder object (`DaemonSyncState`) that the cache and move buffer both reference. This avoids the cache module knowing about the buffer, and avoids the buffer knowing about the cache's internals. The holder is created once in `cli.py::start` and passed to both.

**Stat-then-hash cache shape (resolved from spec's open question):** The cache entry shape is `dict[str, dict]` where each value is `{"hash": str, "size": int, "mtime": float}`. This supports the stat-then-hash pre-filter and is forward-compatible with TD-061's future expansion (adding placeholder metadata). The `snapshot()` method returns the full dict for the 3-way compare; the bail-early check reads `size`/`mtime` first, then falls through to hash comparison only when stat changed.

---

## Phases

### Phase 1 — Cache module (the Local Notebook)

**Goal**: Create the standalone cache module with its 7-method public interface, thread-safe locking, atomic saves, and corruption-discard behavior.

Implements spec components 1 (cache module). Refer to the spec for the full Build description and Done-when criteria.

**Design**:

```
# Cache Module — File Layout + Key Behaviors

  daemon/cache.py (NEW)
  ├── DaemonSyncState       ← lightweight holder: one threading.Lock
  ├── LocalCache             ← in-memory dict + 7 public methods
  │     load(path)           ← read JSON, discard on any error
  │     save(path)           ← temp file + atomic rename
  │     get(vp)              ← return cached entry or None
  │     set_after_ack(vp, h, sz, mt) ← write entry (post-cloud-ack only)
  │     forget(vp)           ← remove entry
  │     snapshot()           ← frozen copy for 3-way compare
  │     rebuild(entries)     ← replace entire map (post-reconcile)
  └── All methods take the shared lock

  RESULT on load error:
  ┌──────────────────────────────────────┐
  │ Input:  garbled JSON / missing file  │
  │ Output: empty dict + warning log     │
  │ Why:    cache is disposable (C-18)   │
  └──────────────────────────────────────┘
```

**Steps**:
1. Create `src/daemon/cache.py`. Define `DaemonSyncState` as a dataclass holding a single `threading.Lock`. Define `LocalCache` class that takes a `DaemonSyncState` at construction.
2. Implement `load(path)`: read JSON, validate root is a dict, validate each entry has the expected shape (`{"hash": str, "size": int, "mtime": float}`). On ANY error (missing file, `json.JSONDecodeError`, non-dict root, malformed entry), reset to empty dict and log warning. Never raise.
3. Implement `save(path)`: write to a temp file in the same directory (using `tempfile.NamedTemporaryFile` with `dir=path.parent`), then `os.replace()` over the real file. On write failure, log warning and continue — cache is advisory. Ensure parent directory exists (create if needed).
4. Implement `get(vp)`, `set_after_ack(vp, hash, size, mtime)`, `forget(vp)`: each acquires the lock, reads/writes the in-memory dict. `get` returns the entry dict or `None`. `set_after_ack` writes `{"hash": hash, "size": size, "mtime": mtime}`.
5. Implement `snapshot()`: acquire lock, return a `dict(self._entries)` shallow copy (values are immutable dicts).
6. Implement `rebuild(entries)`: acquire lock, replace `self._entries` with a new dict from the provided entries.
7. Write tests in `tests/test_daemon/test_cache.py`.

**Files to modify**:
- `src/daemon/cache.py` — NEW (cache module + `DaemonSyncState`)

**Test criteria**:
- [ ] Loading a valid JSON cache file populates memory correctly; `get()` returns the right entry
- [ ] Loading garbled JSON starts with empty dict and logs a warning
- [ ] Loading a file with non-dict root starts with empty dict
- [ ] Loading a file with malformed entries (wrong shape) starts with empty dict
- [ ] Missing cache file starts with empty dict (no exception)
- [ ] `save()` writes a file that `load()` can read back identically
- [ ] `save()` uses atomic rename — a simulated crash (kill mid-save) leaves either old or new file, never partial
- [ ] Concurrent `get` + `set_after_ack` from multiple threads do not corrupt the map (thread-safety test)
- [ ] `snapshot()` returns a frozen copy that is not affected by subsequent `set_after_ack` calls
- [ ] `rebuild()` replaces the entire map; old entries are gone
- [ ] `save()` creates parent directory if missing

**Extension point**: `[extensible: protocol]` — The 7-method interface is a real seam. JSON backend today; SQLite documented as upgrade path. Callers depend on the interface, not the JSON serialization.

**Status**: [x] done
**Completed**: 2026-06-14
**Notes**: Created `src/daemon/cache.py` (DaemonSyncState + LocalCache with 7 methods) and `tests/test_daemon/test_cache.py` (11 tests). All test criteria verified — valid JSON load, garbled/absent/malformed handling, save roundtrip, atomic rename, parent dir creation, thread safety, snapshot isolation, rebuild. No surprises.

---

### Phase 2 — Config additions (new timing and path settings)

**Goal**: Add 4 new fields to `DaemonConfig` so admins can tune cache location and timing defaults without touching code.

Implements spec component 2. Refer to the spec for the full Build description and Done-when criteria.

**Design**:

```
# Config Changes — 4 New Fields on DaemonConfig

  daemon/config.py (MODIFY)
  ├── cache_path: str          default "~/.kms-daemon/cache.json"
  ├── move_window_seconds: float   default 2.0 (must > debounce_seconds)
  ├── periodic_interval_seconds: int   default 21600 (6h); 0 disables
  └── sweep_delete_confirmations: int  default 2 (must >= 1)

  VALIDATION:
  ┌────────────────────────────────────────────────────┐
  │ move_window_seconds <= debounce_seconds → ERROR    │
  │ sweep_delete_confirmations < 1 → ERROR             │
  │ periodic_interval_seconds < 0 → ERROR              │
  └────────────────────────────────────────────────────┘
```

**Steps**:
1. Add 4 new fields to `DaemonConfig` in `src/daemon/config.py` with defaults. All fields need defaults because `DaemonConfig` has `extra="forbid"` — existing YAML files without these fields must still parse.
2. Add a `@model_validator(mode="after")` to validate `move_window_seconds > debounce_seconds`. Use a model validator (not field validator) because it needs to compare two fields.
3. Add `@field_validator` for `sweep_delete_confirmations` (>= 1) and `periodic_interval_seconds` (>= 0).
4. Write tests in `tests/test_daemon/test_config.py` (extend existing file).

**Files to modify**:
- `src/daemon/config.py` — add 4 fields + validators

**Test criteria**:
- [ ] A config YAML with custom values for all 4 fields loads correctly
- [ ] A config YAML omitting these fields uses documented defaults
- [ ] Setting `move_window_seconds` <= `debounce_seconds` raises a validation error
- [ ] Setting `sweep_delete_confirmations` < 1 raises a validation error
- [ ] Setting `periodic_interval_seconds` < 0 raises a validation error
- [ ] `periodic_interval_seconds = 0` loads successfully (disables periodic reconcile)

**Extension point**: `[extensible: config]` — behavior changes through config fields.

**Status**: [ ] pending

---

### Phase 3 — Move Detective (the move-correlation buffer)

**Goal**: Create the move buffer that holds pending deletes and matches them with creates by fingerprint, so a delete-then-create pair becomes a single move event.

Implements spec component 3. Refer to the spec for Done-when criteria. Note the research A8 correction: this buffer is a safety net for cross-volume moves and edge cases, not the primary move-detection path (native `FileMovedEvent` handles same-volume moves directly).

**Design**:

```
# Move Buffer — 3-Method Interface

  daemon/move_buffer.py (NEW)
  ├── MoveBuffer(sync_state: DaemonSyncState)
  │     park_delete(fingerprint, vault_path)
  │       ← records delete candidate with timestamp
  │     match_create(fingerprint) → old_vault_path | None
  │       ← if match within window, removes + returns old path
  │     expire(move_window_seconds) → list[(fingerprint, vault_path)]
  │       ← returns all expired entries for confirmed deletion
  └── Shares the same threading.Lock via DaemonSyncState

  SEQUENCE:
  delete event → park_delete(hash, old_path)
       │
       ├── create event within window → match_create(hash)
       │   returns old_path → caller emits report_moved
       │
       └── window expires → expire(2.0)
           returns [(hash, old_path)] → caller emits report_deleted
```

**Steps**:
1. Create `src/daemon/move_buffer.py`. Define `MoveBuffer` class taking `DaemonSyncState` at construction.
2. Implement `park_delete(fingerprint, vault_path)`: acquire lock, store `{fingerprint: (vault_path, time.monotonic())}`. If a fingerprint collision exists (two deletes with same hash), keep the latest.
3. Implement `match_create(fingerprint)`: acquire lock, check if fingerprint exists in the buffer. If yes and NOT expired (caller checks by comparing timestamps), remove and return the old `vault_path`. If no match, return `None`. Note: expiry is time-based but match_create does NOT check the window — the `expire()` method handles cleanup. This keeps match_create simple: if it's still in the buffer, it's a valid match.
4. Implement `expire(move_window_seconds)`: acquire lock, iterate the buffer, remove and collect all entries where `time.monotonic() - timestamp > move_window_seconds`. Return list of `(fingerprint, vault_path)` tuples representing confirmed deletes.
5. Write tests in `tests/test_daemon/test_move_buffer.py`.

**Files to modify**:
- `src/daemon/move_buffer.py` — NEW

**Test criteria**:
- [ ] A parked delete matched by a create with the same fingerprint returns the old path
- [ ] A parked delete that expires (window passes) is returned by `expire()` as a confirmed delete
- [ ] A create with a fingerprint that does not match any parked delete returns `None`
- [ ] Two concurrent `park_delete` + `match_create` calls from different threads do not corrupt the buffer
- [ ] A fingerprint collision (two deletes with same hash) keeps the latest entry
- [ ] After `match_create` consumes an entry, a second `match_create` with the same fingerprint returns `None`

**Extension point**: `[closed]` — pure in-memory buffer, no persistence needed, no planned variants.

**Status**: [x] done
**Completed**: 2026-06-14
**Notes**: Created `src/daemon/move_buffer.py` (MoveBuffer class with park_delete, match_create, expire) and `tests/test_daemon/test_move_buffer.py` (7 tests). All 6 test criteria verified: match returns old path, expiry returns confirmed deletes, non-match returns None, thread safety, fingerprint collision keeps latest, second match after consumption returns None. No surprises. Shares DaemonSyncState lock from daemon.cache.

---

### Phase 4 — 3-way reconcile upgrade (the Decision Rulebook)

**Goal**: Upgrade the startup scan from a 2-way diff (disk vs cloud) to a 3-way referee (disk vs cache vs cloud) that resolves every combination correctly, including cloud rollbacks, stale cache entries, and conservative deletes.

Implements spec component 4. Refer to the spec for the 9-row resolution table and Done-when criteria.

**Design**:

```
# 3-Way Reconcile — Before and After

  BEFORE (A1 — scanner.py:96-98):
  disk_only = disk_paths - cloud_paths
  cloud_only = cloud_paths - disk_paths - unreadable
  both = disk_paths & cloud_paths
  → 3 cases: upload / delete / skip-or-reupload

  AFTER (A2):
  all_paths = disk_paths | cache_paths | cloud_paths
  for each path → look up (disk_hash, cache_entry, cloud_hash)
  → 9-row Decision Rulebook resolves every combination
  → conservative deletes: track "seen-missing" candidates
  → after scan: rebuild cache to mirror resolved truth

  ScanResult gains: moved: int = 0
```

**Steps**:
1. Extend `ScanResult` in `scanner.py` with `moved: int = 0` field.
2. Add a `cache` parameter to `scan()`: `cache: LocalCache | None = None`. When `None`, behave as A1 (backward compatible for existing callers including `scan_cmd`).
3. Add a `sweep_delete_confirmations` parameter to `scan()` (default 1 for A1 backward compat).
4. When `cache` is provided, take a snapshot at scan start (`cache_state = cache.snapshot()`). Build the union of all paths (`disk_paths | set(cache_state.keys()) | cloud_paths`).
5. Replace the 2-set comparison logic (`scanner.py:96-98`) with a per-path resolution loop implementing the 9-row table from the spec. Key rules: cloud always wins disagreements (C-18); `None` cloud hash = always re-upload; unreadable files excluded from candidate-delete set (existing A1 carve-out).
6. Implement conservative deletes: maintain a `_candidate_deletes: dict[str, int]` mapping `vault_path → consecutive_miss_count`. A cloud-known path absent on disk increments its count; reaching `sweep_delete_confirmations` confirms the delete. A reappearing file clears the candidate. This state must persist between periodic scan calls — pass it as a parameter or store it on a scan-state object.
7. After the scan completes, call `cache.rebuild(resolved_entries)` where `resolved_entries` is the post-reconcile truth (what the cloud now holds + what was just uploaded).
8. For each successful upload, call `cache.set_after_ack(vp, hash, size, mtime)` within `_upload_one`.
9. Wire cache-on-ack into `_upload_one`: on `Success`, call `cache.set_after_ack()`. On `Failure`, do NOT touch the cache.
10. Write tests in `tests/test_daemon/test_scanner.py` (extend existing file).

**Files to modify**:
- `src/daemon/scanner.py` — upgrade `scan()`, extend `ScanResult`, add cache-on-ack to `_upload_one`

**Test criteria**:
- [ ] Brand-new file (disk only, not in cache or cloud) is uploaded and cached on ack
- [ ] Unchanged file (all three match) is skipped — count appears in `result.skipped`
- [ ] File on disk + cache but missing from cloud (rollback) is re-uploaded
- [ ] File on disk + cloud (same hash) but not in cache (cache was lost) is skipped and cached
- [ ] File on disk with different hash from cloud is re-uploaded
- [ ] File missing from disk, present in cache + cloud becomes a candidate delete (not immediate)
- [ ] After `sweep_delete_confirmations` consecutive sweeps with file still missing, delete is reported
- [ ] A reappearing file clears the candidate delete
- [ ] Stale cache entry (gone from disk and cloud) is silently dropped
- [ ] Cloud hash = `None` triggers re-upload
- [ ] Unreadable files are excluded from candidate-delete set
- [ ] After scan, cache is rebuilt to mirror resolved truth
- [ ] `scan()` with `cache=None` behaves exactly like A1 (backward compat)
- [ ] Failed upload does NOT write to cache

**Extension point**: `[closed]` — the resolution table is a fixed rulebook, not a pluggable strategy.

**Status**: [ ] pending

---

### Phase 5 — Bail-early check + live-path cache wiring

**Goal**: On the live path (file created or modified), skip extraction and upload when the file is unchanged according to the cache. Also wire cache-on-ack for successful live uploads and cache updates for native moves.

Implements spec components 5 (bail-early) and partial 6 (native move cache bookkeeping) and partial 8 (cache-on-ack for live path). Refer to the spec for Done-when criteria.

**Design**:

```
# Live Path — Bail-Early + Cache-on-Ack

  _on_create_or_modify(vp):
    1. stat the file (size, mtime)
    2. cache.get(vp) → cached entry
    3. if cached AND size == cached.size AND mtime == cached.mtime:
         → SKIP (bail-early, debug log)        ← fast path
    4. else: hash the file
    5. if cached AND hash == cached.hash:
         → SKIP + update stat in cache          ← stat changed, content same
    6. else: extract + upload
    7. on Success: cache.set_after_ack(vp, hash, size, mtime)
    8. on Failure: do NOT touch cache

  _on_move(old_vp, new_vp):
    (native move — already works)
    on report_moved Success:
      cache.forget(old_vp)
      cache.set_after_ack(new_vp, fingerprint, size, mtime)
```

**Steps**:
1. Modify `_on_create_or_modify` in `cli.py::start`. Before calling `extract()`, do the stat-then-hash pre-filter: (a) stat the file for size + mtime, (b) check `cache.get(vp)`, (c) if cached entry matches stat, skip entirely, (d) if stat differs, hash the file (read raw bytes + SHA-256), (e) if hash matches cached hash, skip but update stat in cache, (f) otherwise proceed with extract + upload.
2. After a successful `upload_text()` or `upload_binary()`, call `cache.set_after_ack(vp, content_hash, size, mtime)`.
3. After a failed upload, do NOT touch the cache.
4. Modify `_on_move` in `cli.py::start`. After a successful `report_moved()`, call `cache.forget(old_vp)` and `cache.set_after_ack(new_vp, fingerprint, size, mtime)`. The fingerprint for the new path can be obtained by stat+hash of the destination file.
5. Add `cache.save()` calls after each ack (or batch — see Phase 7 for save batching if needed).
6. Write/extend tests in `tests/test_daemon/test_cli.py`.

**Files to modify**:
- `src/daemon/cli.py` — modify `_on_create_or_modify`, `_on_move`, add cache construction in `start`

**Test criteria**:
- [ ] When a file is saved but content is unchanged, daemon logs "skipped (unchanged)" and makes no cloud call
- [ ] When a file is genuinely modified, daemon extracts, uploads, and updates cache
- [ ] The stat pre-filter avoids hashing when size+mtime are unchanged
- [ ] When stat changed but hash is unchanged, file is skipped and stat is updated in cache
- [ ] After a successful upload, the file's fingerprint appears in the cache
- [ ] After a failed upload (simulated 500), the file does NOT appear in the cache
- [ ] Native move event updates cache: old path removed, new path added with correct fingerprint

**Extension point**: `[closed]` — the bail-early logic is specific to the stat-then-hash strategy.

**Status**: [ ] pending

---

### Phase 6 — Move detection wiring (connecting Move Detective to live path)

**Goal**: Wire the Move Detective into the live watcher callbacks so that a delete-then-create pair becomes a single move event instead of a delete + re-upload.

Implements spec component 6 (move detection wiring). Refer to the spec for Done-when criteria. Key research finding: native same-volume moves already work via `_on_move` — this phase wires the fallback path for cross-volume moves and edge cases.

**Design**:

```
# Move Detection — Delete Buffering + Create Matching

  _on_delete(vp):                           (CHANGED: no longer reports immediately)
    1. fingerprint = cache.get(vp)          ← file is gone; cache is only hash source
    2. if fingerprint: move_buffer.park_delete(fingerprint.hash, vp)
    3. else: report_deleted(vp) immediately ← not in cache, can't match
    4. start/refresh expiry timer

  _on_create_or_modify(vp):                 (CHANGED: check buffer before upload)
    [bail-early check from Phase 5]
    5. after extraction, before upload:
       old_vp = move_buffer.match_create(content_hash)
    6. if old_vp: report_moved(old_vp, vp) instead of upload
       on ack: cache.forget(old_vp) + cache.set_after_ack(vp, ...)
    7. else: normal upload + cache-on-ack

  Timer expiry handler:
    expired = move_buffer.expire(move_window_seconds)
    for each (fingerprint, vp) in expired:
      report_deleted(vp) + cache.forget(vp) on ack
```

**Steps**:
1. Modify `_on_delete` in `cli.py::start`: instead of calling `report_deleted()` immediately, look up the file's fingerprint in the cache via `cache.get(vp)`. If found, call `move_buffer.park_delete(fingerprint["hash"], vp)` and start/refresh a timer for `cfg.move_window_seconds`. If not found in cache, report deleted immediately (can't match without a fingerprint).
2. Modify `_on_create_or_modify` in `cli.py::start`: after extraction (which gives us the `content_hash`), check `move_buffer.match_create(content_hash)`. If matched (returns `old_vp`), call `report_moved(old_vp, new_vp)` instead of uploading. On ack, `cache.forget(old_vp)` + `cache.set_after_ack(new_vp, hash, size, mtime)`. If not matched, proceed with normal upload.
3. Implement the expiry timer: use `threading.Timer` (consistent with watcher's existing pattern). On expiry, call `move_buffer.expire(cfg.move_window_seconds)`, then for each expired entry, schedule `report_deleted(vp)` via `run_coroutine_threadsafe`. On ack, call `cache.forget(vp)`.
4. On `cache.save()` calls: batch saves after each group of ack callbacks, not after every individual ack.
5. Write/extend tests in `tests/test_daemon/test_cli.py`.

**Files to modify**:
- `src/daemon/cli.py` — modify `_on_delete`, extend `_on_create_or_modify`, add expiry timer

**Test criteria**:
- [ ] A file dragged between folders (OS reports as delete + create with same hash) results in one `report_moved` call, not a delete + re-upload
- [ ] The cache updates: old path removed, new path present with correct fingerprint
- [ ] A genuine delete (no matching create within the window) results in one `report_deleted` call after window expires
- [ ] A native OS move event (`FileMovedEvent`) still works and updates the cache (not broken by the buffer)
- [ ] A delete of a file NOT in the cache is reported immediately (no buffering)
- [ ] The move window timer is refreshed on subsequent deletes (not accumulated)

**Extension point**: `[closed]` — the wiring is specific to the daemon's callback model.

**Status**: [ ] pending

---

### Phase 7 — Periodic reconcile timer (the Scheduled Audit)

**Goal**: Re-run the 3-way reconcile periodically while the daemon is running, catching files stranded by dropped watcher events.

Implements spec component 7. Refer to the spec for Done-when criteria.

**Design**:

```
# Periodic Timer — Inside Existing Event Loop

  start command's _run() async function:

    [existing: startup scan]
    [existing: watcher callbacks]
    [existing: watcher.start()]

    if cfg.periodic_interval_seconds > 0:      ← NEW
      periodic_task = asyncio.create_task(
        _periodic_reconcile(cfg, client, cache, candidate_deletes)
      )

    [existing: main loop — while True: await asyncio.sleep(1)]

    finally:
      periodic_task.cancel()                    ← NEW
      watcher.stop()
      watcher.join()

  async def _periodic_reconcile(cfg, client, cache, candidate_deletes):
    while True:
      await asyncio.sleep(cfg.periodic_interval_seconds)
      result = await scan(cfg, client, cache=cache, ...)
      cache.save(cfg.cache_path)
      log result
```

**Steps**:
1. Define `_periodic_reconcile` as an async function inside `cli.py` (or as a nested function inside `_run()`). It loops: sleep for `cfg.periodic_interval_seconds`, then call `scan(cfg, client, cache=cache, sweep_delete_confirmations=cfg.sweep_delete_confirmations)`, then `cache.save()`, then log the result.
2. In the `start` command's `_run()`, after the watcher starts, create the periodic task via `asyncio.create_task()` if `cfg.periodic_interval_seconds > 0`.
3. In the `finally` block, cancel the periodic task before stopping the watcher.
4. The periodic scan shares the same `cache` and `candidate_deletes` state as the startup scan — pass them through.
5. Write/extend tests in `tests/test_daemon/test_cli.py`.

**Files to modify**:
- `src/daemon/cli.py` — add `_periodic_reconcile`, modify `start` command's `_run()`

**Test criteria**:
- [ ] With `periodic_interval_seconds=1` (short for testing), the daemon re-runs the reconcile every ~1 second
- [ ] A file created while the watcher was temporarily overwhelmed is picked up by the periodic reconcile
- [ ] Setting `periodic_interval_seconds=0` disables the periodic reconcile — no task is created
- [ ] The periodic task is cancelled on daemon shutdown (no asyncio warnings)
- [ ] The periodic reconcile uses the same cache instance as the live path (shared state)

**Extension point**: `[extensible: config]` — interval controlled by `periodic_interval_seconds` field.

**Status**: [ ] pending

---

### Phase 8 — Integration wiring + startup orchestration

**Goal**: Wire everything together in the `start` command: cache load on startup, cache save on ack, move buffer construction, and end-to-end verification that all components interact correctly.

Implements spec component 8 (cache-on-ack wiring) and the startup orchestration. This is integration verification, not new logic. Refer to the spec for Done-when criteria.

**Design**:

```
# Startup Orchestration — Full Sequence

  start command's _run():
    1. Load config
    2. Create DaemonSyncState (shared lock)
    3. Create LocalCache(sync_state) → cache.load(cfg.cache_path)
    4. Create MoveBuffer(sync_state)
    5. Run startup scan with cache
    6. cache.save(cfg.cache_path)
    7. Define watcher callbacks (with bail-early, move buffer, cache-on-ack)
    8. Start watcher
    9. Start periodic reconcile (if enabled)
    10. Main loop
    11. On shutdown: cancel periodic, stop watcher, cache.save()

  VERIFY (integration checklist):
  ├── Every upload_text/upload_binary Success → cache.set_after_ack
  ├── Every report_moved Success → cache.forget(old) + cache.set_after_ack(new)
  ├── Every report_deleted Success → cache.forget
  ├── Every Failure → cache NOT touched
  └── cache.save() after each batch of acks + at shutdown
```

**Steps**:
1. Modify the `start` command in `cli.py` to create `DaemonSyncState`, `LocalCache`, and `MoveBuffer` before the `_run()` async function.
2. Call `cache.load(Path(cfg.cache_path).expanduser())` at startup.
3. Pass the cache to `scan()` for the startup reconcile.
4. Call `cache.save()` after startup scan completes.
5. Verify all cache-on-ack paths are wired (from Phases 4, 5, 6):
   - Every `upload_text()`/`upload_binary()` `Success` calls `cache.set_after_ack()`
   - Every `report_moved()` `Success` calls `cache.forget(old) + cache.set_after_ack(new)`
   - Every `report_deleted()` `Success` calls `cache.forget()`
   - Every `Failure` does NOT touch the cache
6. Add `cache.save()` at daemon shutdown (in the `finally` block).
7. Verify `cache.save()` frequency: after startup scan, after periodic scan, at shutdown. NOT after every individual live ack — live acks are batched by the cache's in-memory nature (saves happen at shutdown and periodic boundaries). If needed, add a periodic "save dirty cache" timer (e.g., every 30 seconds if the cache has changed).
8. Write integration tests that exercise the full startup→live-event→shutdown sequence.

**Files to modify**:
- `src/daemon/cli.py` — orchestration in `start` command

**Test criteria**:
- [ ] After a successful upload, the file's fingerprint appears in the cache on disk (end-to-end)
- [ ] After a failed upload (simulated 500), the file does NOT appear in the cache
- [ ] After a successful move report, old path is gone from cache, new path is present
- [ ] After a successful delete report, the path is gone from the cache
- [ ] Cache is saved to disk at shutdown
- [ ] Cache is loaded from disk on startup — entries survive a daemon restart
- [ ] A full startup→scan→watch→modify→move→delete→shutdown cycle completes without errors

**Extension point**: `[closed]` — integration wiring is specific to the daemon's architecture.

**Status**: [ ] pending

---

## Open Questions

1. **Save frequency for live acks.** The spec says "not after every single ack — batch the saves." Current plan: save at startup-scan-end, periodic-scan-end, and shutdown. For live events, add a 30-second "dirty cache save" timer if needed. This might lose up to 30 seconds of cache state on a crash — acceptable because the cache is advisory and the next boot rebuilds from the cloud. Confirm this tradeoff is acceptable, or prefer save-after-every-ack (simpler but more I/O).

2. **`scan_batch_size` dead config (TD-SCAN-BATCH from research).** `DaemonConfig.scan_batch_size` is defined but never used. The plan does not change this — it remains dead config. Flag for cleanup in a separate TD pass.

3. **Cloud-unreachable vs cloud-wiped (research OQ-1).** When `_fetch_cloud_state()` returns `{}`, A1 re-uploads everything. After A2 with a populated cache, this is ambiguous: empty cloud + populated cache could mean "cloud unreachable" or "cloud was wiped." The plan does NOT address this — it keeps A1 behavior (re-upload everything on empty cloud response). Flag for a separate TD if this becomes a real problem (unlikely for demo; relevant for production).

---

## Out of Scope

- **OneDrive Files-On-Demand placeholder handling** — TD-061. A2 keeps the "unreadable file excluded from delete set" carve-out as the future seam.
- **PyInstaller .app / first-run wizard / tray icon** — Phase 6 Slice B.
- **Vision-describe + blob persistence** — Phase 7.
- **Any cloud-side change** — A2 touches `src/daemon/` only.
- **SQLite cache backend** — documented as upgrade path behind the cache interface, not built.
- **Multi-device cache coordination** — each device has its own independent notebook.
- **`scan_batch_size` cleanup** — existing dead config, not addressed by this plan.
