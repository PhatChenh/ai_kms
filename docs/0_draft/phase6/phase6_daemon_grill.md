# Phase 6 — Daemon: Grill Outcome (locked decisions + rationale)

_Created: 2026-06-13_
_Source: build-pipeline Phase -1 grill (interactive, signed off 2026-06-13)_
_Status: INPUT to the design step. These decisions are locked; the design step elaborates HOW, not WHETHER._
_Related: rearch doc §4, agentbase_research.md, roadmap Phase 6. ADR-0013 (hybrid cache), ADR-0015 (visual blobs)._

> **Reader note.** Plain English leads. The daemon is the small app that will run on the user's Mac, watch their notes folder, and keep the cloud knowledge base fed. It has no intelligence of its own — it watches, extracts text, and uploads. Everything below is what we decided about *how it should behave*, and why.

---

## 1. What Phase 6 is

A thin local app (the **daemon**) on the user's Mac. It watches the whole vault folder, pulls text out of changed files using the existing extractors, and pushes that text — plus file move/delete events — up to the already-built cloud endpoints. No AI, no classification on the daemon. The cloud database stays the single source of truth.

Tier: **heavy** (new package, touches auth + the daemon↔cloud API contract + new infra).

---

## 2. The slice cut (locked)

Phase 6 is too big for one pass. Three slices, built in order; each is green and shippable at its boundary:

- **Slice A1 — core sync pipe (stateless, fully working).** Run `python -m daemon`, point at a vault, text flows to the cloud. No local cache yet.
- **Slice A2 — cache + smart reconcile.** Adds the local cache and the optimizations that ride on it.
- **Slice B — installable app.** PyInstaller `.app`, first-run wizard, launch-on-startup, tray icon.

**The build pipeline runs on A1 first.**

Rationale: A1 proves the pipe with the least machinery; A2 layers efficiency/continuity (and holds most of the new complexity + risk); B is a different problem (macOS packaging). A1→A2→B; B wraps A, A doesn't depend on B.

---

## 3. The big decision — hybrid cache, cloud authority (ADR-0013)

The original architecture declared the daemon **stateless** (no cache). The grill **overturned** this.

- **Two kinds of "knowing what changed":** while the daemon runs, the OS watcher *pushes* events live (no stored state needed). While the daemon was *off*, it must compare the vault to a *baseline* — and that baseline was the open question.
- **Decision: hybrid.** The daemon keeps a **local cache** (a manifest of `vault_path → raw-file-hash`) as a *speed layer*, but the **cloud DB is authority**. On any disagreement, cloud wins. **Cache loss is non-fatal** — a missing cache degrades to a full reconcile against the cloud (the old stateless behaviour), never data loss.
- **Why not pure stateless:** no live-path bail-early, whole-vault state pull each boot, and the cache's only real risk (drift) is removed by making the cloud authoritative rather than by forbidding a cache.
- **Why not local-cache-as-authority:** reintroduces silent drift against a cloud that genuinely rolls back from Litestream backups; breaks "single source of truth" and future multi-device.

Recorded in **ADR-0013**. Overturns the rearch §12 "stateless" constraint (flagged inline in rearch + roadmap).

---

## 4. The startup scanner IS the reconcile (ADR-0013)

The roadmap left `pipelines/reconcile.py`'s successor "unassigned, resolve before Phase 6." **Resolved: the daemon's startup scanner is that reconcile.**

- It is a **3-way compare**: disk ↔ local cache ↔ cloud (`GET /api/state`).
- It uploads new/changed files, reports offline deletions (in-cloud, missing-on-disk), heals cache↔cloud drift (cloud rollbacks self-heal on next boot), and rebuilds the cache from cloud truth.
- The old 7-stage vault reconcile is **not ported** — it dies with its last consumer (ADR-0012).
- Distinct from the cloud-side "DB-only reconcile" (knowledge_entries source cleanup) — that is unrelated cloud-internal integrity.

**`GET /api/state`** is a NEW cloud endpoint (returns `[{vault_path, content_hash}]`, same `KMS_DAEMON_API_KEY` gate). Needed by both stateless and hybrid. **Built first**, as the only cloud touch in Phase 6 — so the "Phase 6 ⟂ Phase 7 parallel" claim carries this one-endpoint asterisk.

---

## 5. Content hash = RAW FILE BYTES (ADR-0013)

The dedup/change-detection hash is computed over **raw file bytes**, not extracted text.

- Lets the scanner decide "skip unchanged" by hashing bytes **without** running extraction on every file — the thing that makes the scanner cheap.
- Keeps the live path and scan path consistent.
- Cost: a cosmetic binary change (re-saved PDF, identical text) triggers a needless re-capture. Rare, self-correcting. The whole-vault-extraction cost of the alternative is paid on *every* boot.
- Overturns the rearch/roadmap "hash on extracted text" line.

---

## 6. Three reconcile triggers (locked)

- **Live** — watcher fires on each file event (normal path).
- **Startup** — one reconcile when the daemon boots.
- **Periodic** — the same reconcile re-run on a timer while running (config-gated, generous default e.g. 6h), even without restart.

Why periodic: filesystem watchers genuinely **drop events** (macOS under rapid changes, synced folders), and a long outage can strand files after retry-exhaustion until restart. Cheap defense-in-depth (reuses the scanner).

---

## 7. Failure / retry model (locked)

- **Invariant — cache-on-ack:** the daemon writes a file's hash into its cache **only after the cloud returns 200/ok** (the "ack" = the success response from `/api/upload` or `/api/event`). A failed/timed-out upload leaves the file *not* cached → automatically re-detected by the next reconcile.
- **Transient failure (running):** in-memory retry with exponential backoff, capped (~3 tries). Success → cache. Exhaustion → log, don't cache.
- **No persistent retry queue.** The reconcile (startup + periodic) is the durable safety net. Crash mid-upload → file never cached → next reconcile re-uploads. No data loss.

---

## 8. What the watcher watches / ignores (locked)

Watch the whole tree **except a config-driven ignore list**, defaulting to:
- dotfolders (`.git`, `.obsidian`, `.trash`, `.stversions`)
- OS junk (`.DS_Store`, `Thumbs.db`)
- editor temp/lock files (`~$*`, `*.tmp`, `*.swp`, `.~lock*`)
- the daemon's own cache/config

Gitignore-style patterns in daemon config (technical admin can extend) — matches the "invisible defaults, tinkering layer underneath" principle.

---

## 9. Moves vs deletes — hash-based reconstruction (locked; A2)

The OS watcher reports moves inconsistently (sometimes `moved(old,new)`, sometimes `deleted+created`). Mishandling churns the cloud: a move reported as delete+create makes the cloud delete the row (drops search entries, scrubs knowledge_entries sources) then re-capture (a fresh LLM call) — for a file the user merely dragged between folders.

- **The daemon reconstructs moves by content-hash:** briefly **buffer delete events**; a `created` with the same hash within a short window → emit one `moved`; no match → real `deleted`.
- Requires the cache (on a delete the file is gone from disk; you need the stored hash) → **this lands in A2**, not A1. In A1, watchdog's events pass through as-is (some moves degrade to delete+create — accepted for A1).
- Folder operations decompose to per-file events (cloud endpoint handles individual files only). Move *into* vault = upload; move *out* = delete.

---

## 10. Daemon config (locked)

- Standalone **`DaemonConfig`** Pydantic model in the new daemon package (the cloud config does not apply — no DB/LLM/MCP on the daemon).
- **API key (`KMS_DAEMON_API_KEY`) via environment variable** (same name both sides; keeps the secret out of files). NOT IAM — IAM is for platform APIs, not the daemon↔cloud channel.
- Everything non-secret in a **YAML file**: vault root (validated to exist on disk), endpoint URL, debounce, ignore patterns, upload concurrency cap, cache path, periodic interval, move-correlation window, retry cap.
- Slice B note: the installer's first-run wizard collects the key into the launchd environment, not the YAML.

---

## 11. Visual / binary content (ADR-0015; spans phases)

Files whose meaning is visual (images, **graphs/charts**) have no useful text. Decision: **cloud keeps the blob + vision-describes it.**

- **Phase 6 commitment (small, concrete):** the daemon uploads **raw bytes + metadata** for files with no usable text extraction; the cloud `/api/upload` is extended with a binary path to accept them. The daemon stays AI-free (no OCR, no description).
- **Deferred to Phase 7:** persist the blob to VNG Object Storage (reuse the Litestream dependency); store a reference + metadata in `documents` (blob storage shape decided at Phase 7 design); extend the summarizer with a vision model → searchable text description.
- **Deferred to Phase 9:** retrieval serves the blob to phone/web when the laptop is closed, or returns the local path when open — amending the three-tier "laptop-dependent" rule for stored blobs.
- Privacy shift noted: user images now leave the laptop into cloud object storage (acceptable for single-tenant personal deployment; future multi-tenant may need opt-out).

Recorded in **ADR-0015**.

---

## 12. Deferred / not-yet-pinned (for the design step)

- Debounce values, upload concurrency cap `N`, periodic interval, move-correlation window — config defaults to pick at design.
- What metadata the daemon sends with a *text* upload (the endpoint already accepts `title` + `metadata`).
- A1 startup scanner internals (batching, ordering).
- Cache storage format/location (JSON file vs tiny SQLite) — A2 design.
- **Doc sweep deferred to pipeline end:** CONSTRAINTS.md (new daemon constraint: cache advisory / cloud authority / cache-loss non-fatal), CLAUDE.md, STATE.md.

---

## 13. Coordination flag

**Phase 6 and Phase 7 are being worked in parallel** (roadmap permits it). A concurrent Phase 7 session created `ADR-0014` (capture data-safety) during this grill, colliding with a draft number — resolved by renumbering this work's visual ADR to **0015**. Watch for further ADR/doc number collisions; coordinate or let one land first.
