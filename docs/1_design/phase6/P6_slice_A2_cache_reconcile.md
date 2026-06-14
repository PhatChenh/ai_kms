# Phase 6 Slice A2 — Local Cache + Smart Reconcile: Design Document

_Created: 2026-06-14_
_Status: DESIGN — locked decisions from ADR-0013 + the A2 grill (`phase6_A2_grill.md`). Elaborates HOW (cache format, timings, 3-way compare, move reconstruction), not WHETHER._
_Behavior ID prefix: **P6-A2-** (9 entries in `behavior_inventory.yaml`, origin: design)_
_Tier: MEDIUM (design-lite) — local-daemon-only, layers onto A1; no new public cloud contract (`GET /api/state`, `/api/event` already shipped in A1)._
_Audience: Next AI session doing spec/research/plan. Non-coder readable — plain-English leads; code refs in sub-bullets._

---

## In plain terms

A2 gives the local daemon a small private "notebook" on the user's Mac. Today (after A1) the daemon is forgetful: every time it boots, and every time a file is touched, it re-reads and re-checks the file from scratch against the cloud. A2 lets it remember what it already sent — so it can skip unchanged files instantly, recognise a *moved* file instead of throwing away the cloud's summary and paying for a fresh AI pass, and recover gracefully if the notebook is ever lost or disagrees with the cloud. The cloud always wins; the notebook is only a speed shortcut, and losing it is never fatal.

This doc decides the four mechanical things the grill handed to the design step: **what the notebook is made of and where it lives**, **the default timings**, **the exact rule for every disk-vs-notebook-vs-cloud disagreement**, and **how a delete becomes a move**.

---

## Cast of characters (symbols referenced 3+ times)

| Name | One-line role |
|------|---------------|
| **the cache** / notebook | The local manifest mapping each note's location to its raw-byte fingerprint (`vault_path → content_hash`). New in A2. |
| `scan()` | The startup reconcile in `daemon/scanner.py` — A1 made it disk↔cloud; A2 upgrades it to disk↔cache↔cloud. |
| `DaemonConfig` | The standalone daemon settings model (`daemon/config.py`); A2 adds cache + timing fields. |
| `cache-on-ack` | The rule: write a fingerprint to the cache only AFTER the cloud returns 200. |
| `GET /api/state` | The existing cloud endpoint returning `[{vault_path, content_hash}]` — the authority baseline. |
| move-correlation window | The short wait after a delete during which a matching create turns the pair into one move. |
| `should_skip_path()` | The shared ignore-pattern filter in `daemon/watcher.py`, reused by the scan walk. |

---

## Scope reminder — A2 only

**In scope:** the local cache module (storage format + location + crash-safe writes), cache-on-ack wiring, live-path bail-early, the 3-way compare in `scan()`, hash-based move-vs-delete reconstruction (buffer-delete + correlation window), periodic reconcile on a timer, conservative sweep-driven deletes (D3), discard-and-rebuild on a damaged cache (D2).

**Out of A2:** OneDrive Files-On-Demand placeholder handling (deferred — TD-061; see "Not painting A2 into a corner" below). PyInstaller `.app` / first-run wizard / tray (Slice B). Vision-describe + blob persistence (Phase 7). Any cloud-side change — A2 touches `src/daemon/` only.

---

## Decision

**Chosen: the cache is a single JSON manifest file, loaded once into an in-memory map guarded by one lock, saved back with atomic temp-file-then-rename writes (Option A).** It is the smallest thing that satisfies cache-on-ack (one atomic save per ack), the disposable-rebuild rule (a bad parse just resets the in-memory map to empty), and the watcher↔reconcile concurrency need (one lock around one in-memory dict). A tiny SQLite database (Option B) was the close runner-up and is the right upgrade if the vault ever grows past tens of thousands of files — but it adds a schema, a migration story, and connection-lifecycle handling for a personal vault that does not need them yet.

One sentence why: a JSON manifest keeps the notebook genuinely disposable and trivially crash-safe at personal-vault scale, where the database's only real advantage (no full-file rewrite per change) does not yet pay for its extra machinery.

---

## Q1 Diagram — the chosen option (JSON manifest)

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

Simplified: The 3-way disk/cache/cloud comparison is shown as the single
            "watcher & reconcile share one map" box. A crash mid-save lands
            back at the "Missing / garbled → discard & rebuild" branch.
```

---

## Guardrail Checklist

(From `/guardrail-check Review` — domains: Architecture, Async & CLI. Required input for `/writing-detailed-specs`.)

- [ ] **C-12 · Public functions in handlers/ & pipelines/ return Result.** `daemon/` is neither, so the hook does not fire — but the daemon already follows the Result convention (uploader/event_reporter return `Success`/`Failure`). New cache + reconcile public functions SHOULD return `Result` for consistency. _Applies by convention._
- [ ] **C-13 · Audit log for AI decisions.** NOT APPLICABLE — the daemon makes zero AI decisions (no `provider.complete()` on the daemon). No audit obligation.
- [ ] **C-10 · CLI wraps async with asyncio.run().** The periodic-reconcile timer MUST live inside the existing `asyncio.run(_run())` loop in `daemon/cli.py::start` — no second event loop, no async Click adapter.
- [ ] **C-11 · load_dotenv once, in cli/main.py.** NOT APPLICABLE to new cache code; the daemon reads `KMS_DAEMON_API_KEY` via `os.environ.get` directly and must keep doing so.

Domains checked: Architecture, Async & CLI.
Domains skipped: Write Safety (daemon writes nothing to the vault), DB Integrity (no cloud schema change), LLM & Providers (no AI on daemon), Testing (standard patterns).

**Recommended new constraint (not yet recorded).** ADR-0013 said a daemon constraint should be added during the Phase 6 design step. Recommend logging it (`/guardrail-check Write`): *"The daemon cache is advisory; the cloud DB is authority; cache loss/corruption MUST be non-fatal (degrade to full reconcile, never delete cloud rows because the cache was missing)."* New domain group: **Daemon Sync**. Deferred to the doc-sweep step rather than written here (flagged in Open Questions OQ-A2-5).

---

## Implications

- The daemon stops being forgetful: an unchanged file is now recognised instantly and skipped, so re-saving a file or restarting the daemon no longer burns extraction + upload work the cloud already has.
  - New cache module (proposed `daemon/cache.py`) holds an in-memory `dict[str, str]` (`vault_path → content_hash`). The live `modified` handler in `daemon/cli.py::_on_create_or_modify` gains a pre-extract check: hash-or-stat the file, compare to the cache, bail if equal. `scan()` in `daemon/scanner.py:66` gains the cache as a third input.

- The startup scanner grows from a two-way diff into a three-way referee, so a cloud that quietly rolled back to an older backup repairs itself on the next boot instead of drifting forever.
  - `scan()` currently builds `disk_state` (`scanner.py:90`) and `cloud_state` (`scanner.py:87`) and compares the two sets (`scanner.py:96-98`). A2 adds `cache_state` and replaces the two-set comparison with the resolution table below. The cache is rebuilt from the post-reconcile truth at the end of every scan.

- Moving a file between folders stops being destructive: instead of the cloud deleting the file's row (dropping its summary + search entries) and paying for a fresh AI pass, the daemon recognises the same content under a new name and sends one cheap "moved" event.
  - Today `daemon/cli.py::_on_delete` reports every delete immediately via `report_deleted` (`event_reporter.py:70`). A2 buffers deletes for a short window; a `created` whose fingerprint matches a buffered delete's fingerprint is rewritten into a single `report_moved` (`event_reporter.py:31`). The cache supplies the deleted file's fingerprint (the file is already gone from disk, so the cache is the only place that hash survives).

- A damaged notebook can never silently poison the sync: any doubt about the cache means it is thrown away whole and rebuilt from the cloud, so the worst case is a slower-but-correct full reconcile, never wrong data.
  - On load, a `json.JSONDecodeError`, a missing file, a non-dict root, or any malformed entry resets the in-memory map to `{}` and lets `scan()` run its full disk↔cloud reconcile (the A1 degraded behaviour). No partial parse, no per-entry salvage (grill D2).

- Deletes become deliberately cautious so a transient filesystem hiccup never destroys real work: a file missing in one sweep is not enough to scrub the cloud — it must stay gone across more than one check.
  - The periodic/startup sweep tracks a small "seen-missing" set; a cloud-known path absent on disk is recorded as a candidate on its first sweep and only reported deleted when a subsequent sweep confirms it still absent (grill D3). A reappearing file clears the candidate.

- The daemon keeps watching itself even without a restart: a timer re-runs the same reconcile periodically, catching files stranded when the OS file-watcher drops events (which it genuinely does under rapid or synced-folder activity).
  - New `periodic_interval_seconds` field on `DaemonConfig`; an `asyncio` task inside the existing `start` loop (`cli.py:160-245`) calls `scan()` on the interval. `0`/unset disables it.

- **Module depth / deletion test.** The new cache module is a genuinely deep module: a tiny interface (`get` / `set_after_ack` / `forget` / `snapshot` / `load` / `save`) hiding the atomic-write + corruption-discard + locking implementation. Delete it and the complexity does not vanish — it reappears scattered across `scanner.py` (load/save/lock), `cli.py` (live bail + ack writes), and the move buffer (fingerprint lookup). It earns its keep. The cache interface is a *real seam* (2+ adapters: the JSON file today, a SQLite store as the documented upgrade path — Option B), so introducing the interface is not speculative. `scan()` is being deepened, not widened: it stays one entry point but its body absorbs the 3-way logic.

- `[UNVERIFIED: watchdog delete→create ordering and timing on macOS FSEvents]` — the move-correlation window default assumes a `created` arrives within ~2s of the matching `deleted` for a drag-move that the OS reports as two events. Research must measure actual FSEvents delete→create latency on the demo Mac before locking the default.

---

## Default timings (recommended, with rationale)

All live on `DaemonConfig` (`daemon/config.py`) so a technical admin can tune them; none are hardcoded in flow logic.

| Setting | Default | Rationale |
|---|---|---|
| `debounce_seconds` (existing) | **1.0** | Keep A1's value. Coalesces editor save-storms (multiple `modified` events) into one upload. |
| `move_window_seconds` (new) | **2.0** | Long enough to catch a drag-move that the OS splits into delete-then-create (D1: bias toward catching moves), short enough that a genuine delete reaches the cloud within ~2s. Must exceed `debounce_seconds` so a debounced create can still land inside the window. |
| `upload_concurrency` (existing) | **4** | Keep A1's value. Caps parallel uploads so a big startup scan does not flood the cloud or saturate the uplink. |
| `periodic_interval_seconds` (new) | **21600** (6h) | Generous per the grill — defense-in-depth against dropped watcher events, not a primary path. Cheap (reuses `scan()`). `0` disables. |
| `sweep_delete_confirmations` (new) | **2** | D3: a cloud-known file must be absent across this many consecutive sweeps before a delete is reported. `2` = "missing twice in a row," the minimum that distinguishes a transient blip from a real delete. |

---

## The 3-way compare resolution table

For each path, the scanner now knows three facts: is it on **disk** (and its hash), is it in the **cache** (and its hash), is it in the **cloud** (and its hash, possibly `None`). The action for every combination, consistent with cloud-authority + D2 + D3:

| Disk | Cache | Cloud | Meaning | Action |
|---|---|---|---|---|
| ✔ | — | — | Brand-new file | **Upload**, then cache-on-ack. |
| ✔ | ✔ | — | On disk + cache but cloud lost it (rollback) | **Re-upload** (cloud authority drives the heal), then cache-on-ack. (P6-A2-07) |
| ✔ | — | ✔ same hash | Cloud has it, cache was lost/rebuilding | **Skip upload**; write the fingerprint into the cache (cache catches up to cloud truth). |
| ✔ | ✔ | ✔ all equal | Steady state, unchanged | **Skip** (the common case; the whole point of the cache). |
| ✔ | * | ✔ differ | Content changed on disk | **Re-upload** (disk wins for content), then cache-on-ack. |
| — | ✔ | ✔ | File gone from disk; cloud + cache still have it | **Candidate delete** — record in seen-missing; report deleted only after `sweep_delete_confirmations` sweeps (D3); on confirm, send `report_deleted` and forget from cache. (P6-A2-06) |
| — | ✔ | — | Gone from disk + cloud; only stale cache entry | **Drop the stale cache entry**, no cloud call (nothing to delete). |
| — | — | ✔ | Cloud-only, never in this cache | **Candidate delete** (same D3 path as row 6) — most likely a real offline deletion; still confirmed across sweeps. |
| ✔ | ✔ | ✔ cloud=`None` | Cloud stored no fingerprint (pre-P5 data) | **Re-upload** (treat `None` as "always re-upload", matching A1 `scanner.py:119`), then cache-on-ack. |

Notes:
- "all-three-differ" collapses into row 5 (disk content differs from cloud): **disk wins, upload**. The cache hash is irrelevant once disk≠cloud — the cache is advisory.
- Unreadable files on disk are excluded from the cloud-only delete set (A1 already does this — `scanner.py:98,247`); A2 keeps that carve-out, which is also the seam TD-061 will later widen for OneDrive placeholders.
- After the scan completes, the cache is rebuilt to exactly mirror what the cloud now holds + what was just uploaded — never the pre-scan cache.

---

## Move reconstruction mechanism

The OS reports a drag-move two ways: sometimes one native `moved(old, new)` event, sometimes a `deleted(old)` followed by a `created(new)`. A1 passed both straight through, so the split form churned the cloud (delete row → re-upload). A2 reconstructs the move:

1. **Native move** — `daemon/cli.py::_on_move` already maps to one `report_moved`. Unchanged; no buffering needed.
2. **Split form (delete + create)** — on `deleted(vp)`, do NOT report immediately. Look up the file's fingerprint in the cache (the file is gone from disk, so the cache is the only surviving source of its hash — this is *why* move reconstruction needs A2). Park `(fingerprint → old_vp)` in an in-memory **move buffer** with a timestamp, and start/refresh a `move_window_seconds` timer.
3. On a `created(new_vp)` within the window, extract the new file's fingerprint and check the buffer: a fingerprint match → cancel the buffered delete and emit one `report_moved(old_vp, new_vp)`; cache the fingerprint under `new_vp` on ack, forget `old_vp`.
4. If the window expires with no match → emit `report_deleted(old_vp)`, forget from cache on ack.

**Interaction with debounce + the live watcher:** the move buffer sits *after* the watcher's existing debounce (`watcher.py:142`) — debounce coalesces the create's `modified` storm first, then the buffer correlates the settled create against the parked delete. Because `move_window_seconds` (2.0) > `debounce_seconds` (1.0), a debounced create still arrives inside the window. The buffer is a separate structure from the watcher's debounce timers; both are guarded so the periodic reconcile and the live watcher cannot mutate the cache or the buffer at the same time (single lock — P6-A2-09).

---

## Landmines addressed in the design

- **Cache vs live-watcher race.** One lock guards the in-memory map and the move buffer; reads (bail-early check) and writes (cache-on-ack, forget) all take it. The periodic reconcile takes the same lock for its load/compare/rebuild. No second copy of the map exists. (P6-A2-09)
- **Partial-write / crash mid-reconcile.** Saves are temp-file-then-atomic-rename, so the on-disk cache is always a complete previous-or-new state, never half-written. A crash mid-reconcile leaves the cloud as authority and the next boot rebuilds the cache from it — no durable corruption. (P6-A2-05)
- **Cache going stale vs cloud after offline edits.** The startup 3-way compare is exactly the heal: disk vs cloud decides truth; the cache is only a hint and is rebuilt to match the resolved truth every scan. A stale cache can at worst cost one redundant compare, never wrong data. (P6-A2-07)
- **Cache-on-ack ordering bug class (mirrors capture.py audit-after-action).** Write the fingerprint only after `upload_*`/`report_*` returns `Success` — never optimistically before the request. A failed upload must leave the file uncached so the next reconcile re-detects it. (P6-A2-02)

---

## Not painting A2 into a corner for TD-061 (OneDrive)

TD-061 (Files-On-Demand placeholders) is explicitly out of scope, but two A2 choices keep the door open: (1) the cache key is `vault_path → fingerprint`, and TD-061 will add cheap metadata (size/mtime) alongside the fingerprint — a JSON object value can grow from a string to a small object without a migration (a SQLite store would need an `ALTER TABLE`); (2) the "unreadable → exclude from delete" carve-out (`scanner.py:247`) is the same seam where "online-only placeholder → hash-only-if-local" will later branch. A2 does not assume every disk file is byte-readable in a way that forbids that branch.

---

## Known tradeoffs

- **JSON rewrites the whole file on every ack.** At personal-vault scale (1–5K files, a few hundred KB) this is trivial; at 100K+ files it would become a per-change cost SQLite avoids. We accept the rewrite now and document SQLite as the upgrade path. We give up: cheap incremental single-row writes.
- **In-memory map = whole cache in RAM.** A few hundred KB for a personal vault — negligible. We give up: the streaming/low-memory profile a database query-on-demand would give a huge vault.
- **Move reconstruction is best-effort, time-bounded.** A drag-move whose create lands *after* the window degrades to delete+re-upload (the A1 behaviour) — correct, just costly. We bias toward catching moves (D1) but cannot guarantee it for arbitrarily slow filesystems.
- **Conservative deletes lag.** A real delete takes up to `sweep_delete_confirmations × interval` to clear via the periodic path (live deletes still clear within `move_window_seconds`). We accept a lingering cloud row over ever destroying real work (D3).

---

## Risks (for research / planning to verify)

- **FSEvents delete→create latency** — measure on the demo Mac; if it exceeds ~2s for drag-moves, raise `move_window_seconds`. (See `[UNVERIFIED]` above.)
- **Bail-early hash cost** — the live path must hash the file to compare to the cache. For large files this re-reads bytes on every save. Research should decide whether to gate the hash behind a cheap `stat()` (size + mtime) pre-check before hashing — a fast-path that avoids hashing unchanged large files. Note: a mtime-only check is unsafe alone (mtime can be preserved across content changes); it is a pre-filter, not a replacement for the hash.
- **Periodic reconcile overlapping a live event storm** — confirm the single lock does not serialise so aggressively that live uploads stall during a 6h-interval scan of a large vault. Consider yielding the lock per-file rather than holding it for the whole scan.
- **Concurrency model** — A1's watcher runs in a watchdog thread and bridges to the asyncio loop via `run_coroutine_threadsafe` (`cli.py:202`). The cache lock must be a `threading.Lock` (taken from both the watcher thread and async tasks), not an `asyncio.Lock`. Research must confirm the lock type against the actual thread/loop boundary.

---

## Open questions

**OQ-A2-1 — Bail-early: hash every save, or stat-then-hash?**

Right now the design says the live path hashes a changed file to compare against the notebook. For a big file, that means reading all its bytes on every save, even tiny edits.

The question: should the daemon first do a cheap size/timestamp glance and only do the full fingerprint when that glance changed?

**If hash-always:** simplest and always correct, but re-reads large files fully on every save.
**If stat-then-hash:** much faster for large unchanged files, but adds a fast-path that must never be trusted alone (timestamps can lie), so it is a pre-filter before the real fingerprint, not a replacement.

Recommendation: stat-then-hash as a pre-filter. It is the standard speed win and stays correct because the fingerprint still decides. Defer the exact pre-filter rule to spec.

**OQ-A2-2 — Where does the cache file live?**

Right now A1 puts the daemon's config at `~/.kms-daemon/config.yaml`. The cache has no home yet.

The question: same folder (`~/.kms-daemon/cache.json`), or a config-set path?

**If fixed `~/.kms-daemon/`:** matches the config location, zero new config, easy to find. 
**If config-set `cache_path`:** lets an admin point it at a faster disk or a per-vault location, at the cost of one more setting.

Recommendation: add a `cache_path` field on `DaemonConfig` defaulting to `~/.kms-daemon/cache.json`. Invisible default, tinkering layer underneath — matches the project's config philosophy. Low cost.

**OQ-A2-3 — Does the live watcher also need the conservative two-sweep delete rule, or only the periodic sweep?**

Right now D3 (don't delete on a single missing-sighting) is described for the *sweep* path. Live deletes go through the move-window buffer instead.

The question: is the move-correlation window a sufficient guard for live deletes, or should live deletes also require a second confirmation?

**If window-only for live:** a live delete clears within ~2s — responsive, and the window already absorbs the move case. A genuine fast delete is reported quickly.
**If two-sweep for live too:** maximally safe but makes every live delete lag by a full sweep interval — likely too sluggish and contradicts "responsive live path."

Recommendation: window-only for live deletes; two-sweep confirmation for the sweep/offline path. The two triggers have different risk profiles — a live delete the user just performed is high-signal; a file merely absent during a background sweep is low-signal. Not a blocker.

**OQ-A2-4 — Lock granularity during a periodic scan of a large vault.**

Right now the design says one lock guards the cache and move buffer.

The question: hold the lock for the whole scan, or take/release it per file?

**If whole-scan hold:** simplest, but a 6h-interval scan of a large vault could block live uploads for the scan's duration.
**If per-file:** live events interleave cleanly, but the scan must tolerate the cache changing under it mid-pass.

Recommendation: per-file lock acquisition, with the scan reconciling against a snapshot taken at the start and writing results back per file. Defer the snapshot-vs-live-merge detail to research. Not a blocker for the demo (small vault), but the right shape.

**OQ-A2-5 — Record the daemon-sync constraint now or at doc-sweep?**

Right now ADR-0013 says a daemon constraint ("cache advisory / cloud authority / cache-loss non-fatal") should be added during the Phase 6 design step, but `CONSTRAINTS.md` has no Daemon Sync group yet.

The question: write the constraint as part of A2, or batch it into the deferred Phase-6 doc-sweep (CONSTRAINTS.md / CLAUDE.md / STATE.md)?

**If now:** the guardrail is enforceable while A2 is being built. 
**If doc-sweep:** keeps all Phase-6 doc edits in one pass, but A2 ships before the constraint is on the books.

Recommendation: write it now via `/guardrail-check Write` (new "Daemon Sync" group) — the constraint is load-bearing for A2's correctness and the cost is one card. Not a code blocker.

---

## ADR references

- **ADR-0013** (`docs/architecture/system_adr/0013-...`) — hybrid cache, cloud authority, scanner-is-reconcile, hash = raw bytes. A2 implements this ADR; it adds no new system-wide decision. The cache *format* (JSON vs SQLite) was explicitly left to this design step by ADR-0013's consequences section, so no new ADR is required (it fails the "hard to reverse" gate — swapping JSON for SQLite later is a localised module change behind the cache interface).

---

## Options explored

### Option A — JSON manifest, in-memory map, atomic rename (CHOSEN)

Already detailed above (Decision + Q1). Deep cache module, real 2-adapter seam, satisfies cache-on-ack / D2 / concurrency with the least machinery. Trade: rewrites the whole file per ack (fine at personal scale).

### Option B — Tiny SQLite database (viable; documented upgrade path)

**What this means:** the notebook is a small database file instead of a text file; the daemon queries rows on demand and the database engine handles crash-safety and concurrent writes.

**Approach:** one table `(vault_path PRIMARY KEY, content_hash)`; per-ack write is a single-row `INSERT OR REPLACE` committed as its own transaction; corruption → delete-and-recreate the file (discard & rebuild). No bulk in-memory map.

**Files touched:** new `daemon/cache.py` (SQLite-backed), same call sites in `scanner.py` / `cli.py`.

**Cost:** Dev medium (schema + connection lifecycle + thread-safety mode for SQLite across the watcher thread and async tasks). Runtime: cheap incremental writes, low memory. Maintenance: a schema to version, even if A2 never migrates it.

**Risk:** SQLite default thread-checking and write-locking across the watchdog thread ↔ asyncio boundary is a real footgun (the project already hit thread/loop bridging in `cli.py:202`). Over-built for a few-thousand-file vault.

**Module depth:** identical interface to Option A behind the same seam — which is exactly why it is the painless future upgrade rather than the choice now.

**What it defers:** nothing extra; it is strictly more scalable and strictly more machinery.

**Constraints check:** C-12 satisfies (Result-returning), C-10/C-11 not applicable, C-13 not applicable. Same as Option A.

Q1 diagram (drawn during Step 4):

```
# Local Cache (SQLite) — What Happens Inside
              Daemon starts
                   │
                   ▼
        ┌────────────────────────┐
        │ Open the cache database │
        │ file (one table: note's │
        │ location → fingerprint) │
        └───────────┬────────────┘
         ┌──────────┴───────────┐
   File opens OK          Missing / corrupt
         │                      │
         ▼                      ▼
  ┌──────────────┐      ┌──────────────────┐
  │ Use it as-is │      │ Recreate empty — │
  │ (rows queried│      │ discard & rebuild│
  │  on demand)  │      │ from the cloud   │
  └──────┬───────┘      └────────┬─────────┘
         └──────────┬────────────┘
                    ▼
        ┌──────────────────────────┐
        │ While running: watcher &  │
        │ reconcile read/write rows │
        │ directly; the database    │
        │ serialises concurrent     │
        │ writes itself             │
        └───────────┬──────────────┘
                    │ cloud confirms it got the file
                    ▼
        ┌──────────────────────────┐
        │ Write the fingerprint row │
        │ as its own transaction    │
        │ (a crash loses one row,   │
        │  never the whole cache)   │
        └──────────────────────────┘
```

Reason not selected: equivalent correctness, more machinery (schema, connection lifecycle, cross-thread SQLite mode) for scale a personal vault does not need. Kept as the documented upgrade behind the cache interface.

### Rejected alternatives (one line each)

- **Local cache as authority (full offline sync-client).** Rejected by ADR-0013 — reintroduces silent drift against a cloud that genuinely rolls back; breaks single-source-of-truth.
- **Stay stateless (no cache, A1 behaviour).** Rejected by ADR-0013 — no live bail-early, whole-vault state pull each boot, and move reconstruction is impossible without a stored hash.
- **Repair/partially-trust a damaged cache.** Rejected by grill D2 — partial salvage is exactly how silent drift creeps in; the notebook is disposable.
- **Report sweep-driven deletes on first sighting.** Rejected by grill D3 — a single transient miss would scrub real work invisibly, the worst failure mode.
- **Optimistic caching (write fingerprint before the cloud acks).** Rejected — violates cache-on-ack; a failed upload would be wrongly remembered as synced and never retried.

---

## Glossary

| Term | Meaning |
|------|---------|
| **the cache / notebook** | The daemon's local manifest mapping each note's vault location to its raw-byte fingerprint. Advisory speed layer; the cloud is authority; losing it is non-fatal. |
| **content fingerprint** | SHA-256 over a file's raw bytes — the same value the daemon already computes (ADR-0013). Used to detect change and to match a deleted file to its re-created twin. |
| **cache-on-ack** | The rule: a file's fingerprint is written to the cache only AFTER the cloud returns a success response, so a failed upload is never remembered as synced. |
| **3-way compare** | The startup/periodic reconcile comparing disk ↔ cache ↔ cloud and acting per the resolution table; cloud wins every disagreement. |
| **discard & rebuild** | On any cache damage, throw the whole notebook away and refill it from cloud truth — never repair (grill D2). |
| **move-correlation window** | The short wait after a delete during which a matching re-creation (same fingerprint) turns the delete+create pair into one move event. |
| **move buffer** | The in-memory holding area for delete events awaiting a possible matching create within the window. |
| **conservative delete** | A sweep-found missing file is reported deleted only after it stays gone across more than one sweep (grill D3). |
| **periodic reconcile** | The same scan re-run on a timer while the daemon runs, catching files stranded by dropped OS watcher events. |
| **bail-early** | Skipping extraction + upload on the live path when a file's fingerprint matches the cache (unchanged). |

---

## Next step

Design doc written. Run `/architecture-docs` (or `/update-arch-story`) to fold the cache + 3-way reconcile into the main architecture designs, then `/writing-detailed-specs` to structure Option A into build steps. Before spec, log the recommended **Daemon Sync** constraint (OQ-A2-5) via `/guardrail-check Write`.
