# Daemon keeps a local cache as a speed layer; the cloud DB stays authority; the startup scanner IS the reconcile

_Created: 2026-06-13_

The cloud-native rearchitecture originally declared the daemon **stateless** — "no DB, no cache, no AI state. Pure bridge. Crash → restart → no data loss" (rearch doc §12, roadmap Phase 5 NEW-constraint row). The Phase 6 grill (2026-06-13) re-examined this and **overturned it**. **Decision: the daemon keeps a local manifest (a cache of `vault_path → raw-file-hash`) as a performance layer for the live-edit path, but the cloud DB remains the single source of truth. The startup scanner is promoted from a one-way file-diff into a three-way reconcile (disk ↔ local cache ↔ cloud) that heals any cache↔cloud drift on every boot. This same scanner absorbs the orphaned `pipelines/reconcile.py` transition that ADR-0012 left unassigned.**

**Status:** accepted

## Context — why stateless was reconsidered

The stateless rule was chosen to protect the "cloud DB is the single source of truth" principle: a daemon that keeps its own state record creates a second source that can silently drift from the cloud (e.g., the cloud restores from a Litestream backup that lost the last few writes, while the daemon's record still says "synced").

But stateless has two real costs the grill surfaced:

1. **The live-edit path cannot bail early.** With no stored prior hash, on a `modified` event the daemon has nothing to compare against, so it must either extract+upload blindly (and let the cloud dedup) or make a per-path network round-trip. Both waste work a local hash would save.
2. **The startup baseline must come from the cloud anyway.** Detecting changes that happened while the laptop was closed requires comparing the vault to *some* baseline. Stateless forces a `GET /api/state` pull (which we add regardless — see below).

The decisive realization: the drift risk that motivated "stateless" is fully mitigated by making the cloud **authority** (not by forbidding a cache), and the drift-healing mechanism that a cache needs is *exactly* the startup scanner the roadmap already requires. So a hybrid adds almost no new machinery — it makes the already-planned scanner cache-aware.

## Decision — the daemon state model

1. **Local cache (advisory, not authority).** The daemon stores a manifest of `vault_path → content_hash`. Used only to make the live path fast and the startup scan cheap. It is never trusted over the cloud.
2. **Cloud DB is authority.** On any disagreement, cloud wins. Cache loss/corruption is non-fatal: a missing cache degrades to a full reconcile against the cloud (the stateless behaviour), never to data loss or a wrong diff.
3. **Content hash is over RAW FILE BYTES, not extracted text.** Lets the scanner decide "skip unchanged" by hashing bytes without running extraction on every file. Overturns the rearch doc / roadmap "hash on extracted text" line. Cost — re-capture on a cosmetic binary change — is rare and self-correcting.
4. **`GET /api/state` is a new cloud endpoint** returning `[{vault_path, content_hash}]`. Required by *both* the stateless and hybrid designs (baseline for the scan / drift-heal). It is a small, additive, read-only endpoint built as the first step of Phase 6. (The Phase 6 "pure local, runs parallel to cloud work" claim now carries this one-endpoint asterisk.)
5. **The startup scanner IS the reconcile.** It performs a three-way compare:
   - **disk ↔ cache** → fast detection of what changed locally,
   - **cache ↔ cloud (`GET /api/state`)** → drift healing; cloud rollbacks self-heal on next boot.
   It uploads new/changed files, reports offline deletions (cloud-has, disk-missing), and rebuilds the cache from cloud truth. This resolves the `pipelines/reconcile.py` ownership question ADR-0012 left open (its consequences §37): the vault↔cloud reconcile lives here, in Phase 6.
6. **Live path stays simple.** On a `modified` event the daemon compares to its cache; unchanged → bail (no extract, no upload). Changed → extract + upload, then update the cache. (This supersedes the Q4 "upload blindly, cloud dedups" lean, which was only necessary under the stateless assumption.)

## Considered options

- **Stateless + ask cloud (the prior decision).** Daemon holds nothing; every baseline comes from `GET /api/state`. Rejected as the MVP target: no live-path bail, whole-vault state pull each boot, no obvious win once the cache's drift risk is removed by making cloud authoritative — but kept as the *degraded fallback* when the cache is missing.
- **Local cache as authority (full offline sync-client model).** Daemon manifest is the truth; reconcile with cloud rarely. Rejected: reintroduces the silent-drift footgun against a cloud that genuinely rolls back from backups, and breaks the "single source of truth" principle and future multi-device.
- **Hybrid: local cache as speed layer, cloud as authority, scanner = reconcile (chosen).** Captures the live-path and startup efficiency of a cache while keeping correctness anchored to the cloud, and reuses the already-planned scanner as the drift-healer rather than adding a new mechanism.

## Consequences

- **Overturns the "daemon is stateless" direction** (rearch doc §12 NEW-constraint; roadmap Phase 5/6 text). The replacement rule: *the daemon cache is advisory; the cloud DB is authority; cache loss must be non-fatal (degrade to full reconcile).* To be recorded in `CONSTRAINTS.md` (new daemon constraint) during the Phase 6 design step, and the rearch doc / roadmap stale lines corrected.
- **Resolves ADR-0012's open question** — `pipelines/reconcile.py`'s successor is the Phase 6 startup scanner. The old 7-stage reconcile is not ported; its vault↔index role is replaced by the three-way scan.
- **Adds one cloud-side endpoint** (`GET /api/state`) to Phase 6, gated by the same `KMS_DAEMON_API_KEY` bearer as `/api/upload` and `/api/event`. Phase 6 is therefore not 100% local — it adds this one read-only endpoint to the cloud first.
- **Hash basis changes to raw bytes** everywhere the daemon computes `content_hash`; the cloud stores whatever the daemon sends, so the two stay consistent.
- **Cache design is deferred to the Phase 6 design step** (storage format, location, corruption handling, whether it is a JSON file or a tiny SQLite) — this ADR fixes the model and the authority direction, not the file format.
- **Distinct from the cloud-side "DB-only reconcile"** (knowledge_entries source-cleanup, rearch §85 / Phase 7 design note) — that is cloud-internal integrity, unrelated to this vault↔cloud sync, and is not in scope here.
