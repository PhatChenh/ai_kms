# Phase 6 Slice A2 — Session Capture + Grill Outcome (cache + smart reconcile)

_Created: 2026-06-14_
_Source: build-pipeline session — A1 recovery/merge + Phase -1 focused grill (signed off 2026-06-14)_
_Status: INPUT to the A2 design step. Locked decisions below are settled; design elaborates HOW (storage format, exact timings), not WHETHER._
_Related: `phase6_daemon_grill.md` (whole-phase grill, A2 decisions pre-locked there), ADR-0013 (hybrid cache / scanner-is-reconcile), roadmap Phase 6, A1 docs `docs/*/phase6/P6_slice_A1_daemon_core.md`._

> **Reader note.** Plain English leads. A2 gives the local daemon a small private "notebook" on the user's Mac so it stops re-checking the whole vault from scratch: skip unchanged files instantly, recognise *moved* files instead of re-uploading them, and recover gracefully if the notebook is lost or disagrees with the cloud (the cloud always wins). This doc records what this session did and what the grill locked.

---

## Part 1 — What this session did (A1 recovery + merge)

The session opened intending to build-pipeline A2, but discovered A1's state was not what it appeared.

### 1.1 A1 was built, then orphaned by a deleted worktree
- A1 (daemon core sync pipe) had been **fully built + tested in a git worktree** under `.worktrees/` (gitignored, commit `4c68271`).
- The worktree's branch ref was deleted (GitHub Desktop operation), orphaning the 9-commit chain (`971f0b3`..`723fd80`) as **dangling objects** — invisible to `git branch` / `git log`, but recoverable via `git fsck --no-reflogs --lost-found`.
- A1's plan doc still reads `_Status: [ ] pending_` — the doc was never flipped, which is why A1 looked unbuilt.

### 1.2 Recovery + merge
- Recovered: `git branch recover-p6-a1 723fd80` (the fsck tip). Branch kept as a safety ref.
- Merged into `cloud-native` at **`9b4a28c`** (`merge --no-ff`).
- A1 now lives at `src/daemon/`: `config, watcher, extractor, uploader, event_reporter, _http_retry, scanner, cli, __main__` + `tests/test_daemon/` (171 tests). Plus `handlers/*` `max_file_size_bytes` param (11 files) and cloud `/api/state` (GET) + multipart binary upload path.

### 1.3 Merge-conflict resolution — the `/api/upload` seam
The only conflict was `src/mcp_server/api.py`, where two parallel streams diverged on the same endpoint:
- **A1** (built earlier, off `e7790b6`) used the P5 stub `upsert_from_upload` for the JSON text path and *added* the multipart binary path, `state_handler`, `event_handler`, and routes.
- **cloud-native HEAD** (`f4f035e`, Phase 7A done) had rewired the JSON text path to `await capture_upload` (the real capture pipeline).

**Owner decision — Hybrid (forward-correct):**
- JSON text upload → `await capture_upload` (Phase 7A real pipeline: store-raw-first, summarize, store-anyway on AI failure, idempotent on content_hash).
- Multipart binary → `upsert_from_upload` (A1 stub; bytes discarded — blob storage deferred to Phase 7 per A1 design).
- Keep A1's `/api/state`, `/api/event`, routes. Import both functions.
- Net A1 test churn: ~zero. **Known minor gap:** a client-supplied `title` on the JSON path is dropped — `capture_upload` derives title from the AI summary, falling back to the filename stem. Accepted.

### 1.4 Pre-existing failure fixed
- `test_classify_ready_log_emitted` (P7-CAP-08) was failing on cloud-native *before* the merge. Cause: `capture.py:253` used stdlib `%s`-style positional args on a **structlog** logger, which renders `vault_path=%s positional_args=(...)` instead of interpolating.
- Fix (`71b56af`): `logger.info("capture.classify_ready", vault_path=vault_path)`. Affected suites now green (387 passed in the affected areas; full-suite baseline not yet run).

### 1.5 Housekeeping notes
- The merge commit used `--no-verify`; later confirmed **moot** — there is no git pre-commit hook (enforcement is via Claude Code `.claude/settings.json` hooks, which act on tool calls, not on `git commit`).
- Recovery recorded to auto-memory (`project_p6a1_worktree_recovery.md`).

---

## Part 2 — A2 grill outcome (focused Phase -1)

A whole-phase grill (`phase6_daemon_grill.md`) already locked A2's architecture (ADR-0013: hybrid cache, cloud-authority, scanner-is-reconcile, raw-byte hash, three triggers, cache-on-ack, move reconstruction, watch-whole-tree-except-ignore-list). This focused grill resolved the items that grill explicitly deferred.

### 2.1 Locked decisions — IN A2 (demo) scope

**D1 — Move-vs-delete race: lean toward reliably catching moves.**
When a file vanishes from one spot and appears in another (sometimes as two separate events), bias toward recognising it as a *move*, accepting that deletes may take a few seconds longer to reach the cloud.
_Why:_ a wrongly-recaptured move is expensive and user-visible — the cloud drops the file's summary + its place in the knowledge base, then pays for a fresh AI pass. A slightly delayed delete is harmless — search/read keep working off the cloud; the file just lingers a few extra seconds.

**D2 — Damaged/untrustworthy notebook: discard and rebuild, never repair.**
On *any* doubt about the cache's integrity (crash mid-write, garbled entries, unreadable), throw the entire notebook away and rebuild from the cloud. Never partially trust or salvage it.
_Why:_ matches cloud-authority + cache-loss-non-fatal. A full rebuild is cheap (re-read local files + one cloud request). Partial salvage is exactly how silent drift creeps in. The notebook is disposable, not repairable.

**D3 — Sweep-driven deletes: conservative, not eager.**
A file missing during a single sweep must not instantly scrub cloud knowledge. Require it to stay gone across more than one check before reporting a delete.
_Why:_ a false delete destroys real work invisibly — the worst failure mode. A genuinely-deleted file taking a little longer to clear costs nothing. (Refines the locked "report offline deletions" → "report them carefully.")

**D4 — A2 assumes a normal local vault (files readable) for the demo.**

### 2.2 Deferred to Tech Debt — real-world, post-demo

**TD — OneDrive "Files On-Demand" placeholder support.**
The intended end user runs **OneDrive**, which offloads rarely-touched files to its cloud, leaving an **online-only placeholder** on disk: the file still *appears* in the folder, but reading its bytes **forces OneDrive to download it**. This collides head-on with two locked decisions — "content hash = raw bytes" and "startup + periodic full sweep" — because naive hashing/walking would drag the *entire vault* back down from OneDrive (bandwidth, disk, defeats the offload).

Required capability (deferred, not built for the demo):
- Detect online-only placeholders; **never download a file just to fingerprint it**.
- Track offloaded files via the cache + cheap metadata (size / modified-time) that OneDrive exposes without triggering a download.
- Byte-hash **only** files whose content is actually local (carve-out to the locked "hash = raw bytes" rule).
- For a never-before-seen offloaded placeholder (e.g. daemon installed onto an already-offloaded vault): capture a name/path/size **stub** now so it's findable by name, and capture real content later when the file becomes local. **Do not force-download the whole vault on install.**

_Owner call:_ demo does not need this; real end-user deployment on OneDrive does. Save as TD, build later. (The "no-hydrate / hash-only-local" principle is ADR-worthy when the capability is built.)

### 2.3 Open — handed to the design step (mechanics, not decisions)
- Notebook **storage format + location** — JSON manifest file vs tiny SQLite; where it lives.
- Default **timings** — move-wait window, debounce, upload concurrency cap N, periodic-sweep interval.
- A2 **3-way compare internals** — how disk ↔ cache ↔ cloud disagreements resolve per case (disk-only → upload; cache-only → drop stale entry; cloud-only → cautious delete per D3; all-three-differ → disk wins, upload; cache↔cloud drift from a Litestream rollback → disk present + cloud absent = re-upload, self-heals).

---

## Part 3 — Build-pipeline status

- **Phase -1 (grill): COMPLETE** (this doc).
- **Next: tier classification → design step.** Tier likely **medium** (local-daemon-only, no new public contract — `/api/state` already shipped in A1; cache + reconcile layer on existing daemon modules). To be stated before dispatching the design subagent.
- Cross-cutting still to log this session: **TD entry** (§2.2) via guardrail-check.
