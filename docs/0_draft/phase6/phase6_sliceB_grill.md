# Phase 6 Slice B — Installable Daemon App: Grill Outcome (locked decisions + rationale)

_Created: 2026-06-14_
_Source: build-pipeline Phase -1 grill (interactive, signed off 2026-06-14)_
_Status: INPUT to the design step. These decisions are locked; the design step elaborates HOW, not WHETHER._
_Related: rearch doc §4 (daemon spec), §8/§9 (three-tier retrieval), phase6_daemon_grill.md (slice cut), phase6_A2_grill.md, roadmap Phase 6 "DAEMON INSTALLER". ADR-0013 (hybrid cache), ADR-0016 (native, not Docker — written from this grill)._

> **Reader note.** Plain English leads. Slice B is the **last** slice of Phase 6: it wraps the working daemon (Slices A1 + A2) into a single app a non-technical manager can install on their own computer and set up in about two minutes. It does **not** change what the daemon does — it changes how it's delivered, installed, configured, and kept alive. Everything below is what we decided about *how the app should be packaged and behave*, and why.

---

## 1. What Slice B is

Package the existing Phase 6 daemon (watch vault → extract text → upload to the cloud; A1 built, A2 cache/reconcile planned) as a **one-click installable desktop app**. A busy manager downloads it, opens it, points it at their notes folder, pastes one key, and it runs quietly in the background from then on — including after every restart.

Tier: **heavy** (new packaging/distribution surface, touches where a secret is stored, two operating systems, OS startup integration, and the unsigned-app security story).

**Sequencing note:** Slice B *wraps* A1 + A2 — it has nothing to ship until A2 lands. A2 is currently docs-only (spec/research/plan written, not implemented). So design → spec → research → plan for B can proceed now in parallel; **implementing** B waits for A2. (Build-pipeline stops at plan anyway.)

---

## 2. The big decision — native app, NOT Docker (ADR-0016)

The owner initially leaned toward shipping the daemon in Docker (reuse the cloud-side toolchain). The grill **overturned** this.

- **Docker *can* read local files** without moving the vault inside — a bind mount (`-v /host/vault:/vault`) exposes the host folder in place. That part works.
- **But Docker Desktop on Mac/Windows runs a Linux VM, and host filesystem events do not cross that VM boundary.** The daemon's whole job is *live* file-watching (watchdog → FSEvents); inside a container watching a bind-mounted host folder, those events silently never fire. The only fallback is polling — slow, CPU-heavy, and it breaks the "drop a file → in cloud within 10s" target (minutes instead of seconds).
- **Tier-3 retrieval also breaks under Docker:** it hands the user's Claude Desktop a *vault path* to open the real file; a container only knows the container path (`/vault/...`), not the host path Claude Desktop needs.
- **Decision: native packaged app** (PyInstaller `.app` on Mac, `.exe` installer on Windows). Docker is right for the *cloud* side (event-free, stateless server); it is the wrong shape for the *daemon* (local, event-driven, filesystem-native).

Recorded in **ADR-0016**.

---

## 3. Unsigned app + one-time guided override (locked)

- Building a `.app`/`.exe` needs **no Apple/Microsoft account** — PyInstaller produces it, it runs.
- The friction is on *other people's* machines: an **unsigned** app trips macOS **Gatekeeper** ("can't verify developer") and Windows **SmartScreen** ("Windows protected your PC"). Clean, warning-free install would need a paid **Apple Developer Program ($99/yr)** + notarization (and a separate Windows code-signing cert).
- **Decision: ship unsigned for the tester phase.** The manager does a **one-time** guided override (Gatekeeper "Open Anyway" / SmartScreen "More info → Run anyway"), walked through with screenshots in a short doc. Annoying once, then never again. Revisit paid signing when the user pool grows.
- Rationale: single/small internal tester pool, not public distribution; $99/yr recurring for a ~30-second one-time click isn't worth it yet.

---

## 4. Cross-platform from the start — Mac + Windows (locked)

- Owner's call (overrode the "Mac-first" recommendation): build **both** Mac and Windows in this slice.
- **The Python core is already portable** (watchdog, handlers, uploader, `DaemonConfig`) — zero extra effort there.
- **The cost is in the wrapper glue**, ~30–40% per-OS, ~60–70% shareable if cross-platform libs are chosen from day one:

| Piece | macOS | Windows | Shared? |
|---|---|---|---|
| Launch-on-startup | LaunchAgent (launchd plist) | Task Scheduler / registry Run key | ❌ per-OS |
| Packaging | PyInstaller `.app` → DMG | PyInstaller `.exe` → installer (**build on a real Windows box — no cross-compile**) | ❌ two builds |
| Unsigned-warning story | Gatekeeper "Open Anyway" | SmartScreen "Run anyway" | ❌ per-OS doc |
| Tray icon | `pystray` (NOT rumps — Mac-only) | `pystray` | ✅ shared |
| Wizard UI | Tkinter | Tkinter | ✅ shared |

- **Design requirement:** an **OS-abstraction seam** so startup-registration and tray are swappable per-OS pieces; everything else shared.

---

## 5. API key storage — OS secure store via `keyring` (locked)

- The earlier slice-cut grill said "key in launchd environment, not YAML" — but launchd is Mac-only, so that mechanism doesn't cross-platform.
- **Decision: store `KMS_DAEMON_API_KEY` in the OS secure store via the cross-platform `keyring` library** — macOS Keychain + Windows Credential Manager, one API. Secret never sits in a plaintext file or an OS-specific env hack.
- **Non-secret config stays in a YAML file** (vault root, endpoint URL, debounce, ignore patterns, concurrency cap, cache path, periodic interval, etc. — as already planned in the A1/A2 grills).
- **New dependency:** `keyring` (flagged + accepted).

---

## 6. First-run wizard hard-blocks on a live connection test (locked)

- The #1 silent-failure risk: a non-technical user pastes a wrong/expired key or a typo'd endpoint, the wizard saves it, the daemon "runs" but every upload 401s — looks fine, syncs nothing.
- **Decision: the wizard makes a live, authenticated test call to the cloud before saving, and only completes on success.** A bad key/URL fails loudly at setup ("Couldn't connect — check your key") instead of silently later.
- **Must hit an authed endpoint** (e.g. `GET /api/state`), **not** the open `/health` (which can't verify the key).

---

## 7. One generic build per OS + baked *default* endpoint, editable (locked — option b)

- Testers each get their **own cloud runtime** (own endpoint URL + own key), so endpoints differ per person.
- **Rejected (a):** hardcoded-hidden endpoint per-user build → forces a rebuild + redistribute *per tester, per OS, on every release*. Treadmill.
- **Chosen (b):** **one generic build per OS** with a **baked *default* endpoint** that the wizard pre-fills into an **editable** field. Common case = one click past it; a tester whose endpoint differs edits it once.
- **Supports multiple testers + separate vaults out of one build:** the *only* shared thing is the binary file. Vault path (YAML), API key (keyring), and endpoint (editable) are all per-machine, set at each install's wizard.
- The endpoint is not secret, so baking a default is safe.

---

## 8. Updates — manual re-download, auto-update deferred (locked)

- **Decision: no auto-update this slice.** Owner sends testers the new build; they replace the app + relaunch.
- **Config survives a reinstall** (key in keyring + YAML untouched) → updating is "download, drag, reopen," not a re-setup.
- Rationale: tiny tester pool, fast-changing system, and auto-update infra is heavy *and* fights unsigned apps (self-replacing an unsigned app re-triggers Gatekeeper). Build it later if the pool grows and the app stabilizes.

---

## 9. Minimal tray icon (locked)

- The daemon is a windowless background app — the tray icon is the **only** local proof-of-life a non-technical user has ("is it running? stuck? did it sync?"). More essential-minimal than "nice-to-have."
- **Decision: minimal tray via `pystray` (cross-platform):** status states (running / syncing / error) + menu with **Quit** and **Open setup**.
- **NOT a dashboard.** The rich knowledge view (browse entries, correct facts) is the separate **web UI** (rearch §10). Tray = liveness + escape hatch only.
- **New dependency:** `pystray` (flagged + accepted).

---

## 10. Installer per OS with clean uninstall (locked)

- **Decision: friendly installer per OS** — macOS **DMG** (drag-to-Applications); Windows **installer** (Inno Setup / NSIS) with a Start-menu entry and a **registered, clean uninstall**.
- **Uninstall must remove all traces:** the app, the startup registration (LaunchAgent / Task Scheduler), the YAML config, **and the keyring entry** — no leftover secret. A plain zip + manual delete would strand the key in Keychain/Credential Manager.
- The one-time unsigned-warning override (§3) is separate from the installer — it's how you first *open* the app, handled by the walkthrough doc.

---

## 11. Wizard built in Tkinter (locked)

- **Decision: Tkinter** for the ~4-field one-time wizard (folder picker, endpoint, key, "test & save").
- Rationale: **stdlib — no new dependency**, bundles painlessly in PyInstaller on both OSes, native folder dialog (`tkinter.filedialog`). The dated look is irrelevant for a window seen once for ~90 seconds — not worth a localhost web server or a heavy Qt/Toga dependency.

---

## 12. Launch-on-login default ON (locked)

- **Decision: register to start at login by default** (LaunchAgent on Mac / Task Scheduler or registry Run on Windows). Tray **Quit** is the escape hatch.
- Rationale: a sync daemon that doesn't auto-start defeats its purpose — a non-technical user would close the laptop, reopen, and nothing syncs with no idea why. After the one-time Gatekeeper/SmartScreen approval, auto-launches don't re-warn (quarantine clears on first open).

---

## 13. Captured facts (for design / research)

- **Daemon bundles NO embedding model.** Embeddings run cloud-side (capture pipeline); the daemon only extracts text + uploads. → small PyInstaller bundle (Python + watchdog + `pdfplumber`/`python-docx`/`openpyxl`). **Research must confirm** the daemon imports no `sentence-transformers`.
- **New runtime deps:** `keyring`, `pystray`. (Tkinter is stdlib.) **Build tools:** PyInstaller + Inno Setup/NSIS. **Hardware:** a Windows machine to build/test the `.exe` (no cross-compile).
- **OS-abstraction seam** is the load-bearing design idea: startup-registration + tray as swappable per-OS implementations behind a shared interface.

---

## 14. Deferred / not-yet-pinned (for the design step)

- Tray status state machine (exact states, transitions, how sync state is wired in) + icon assets.
- Wizard field layout/validation UX; the source of the baked **default endpoint** value (build-time constant / env at build).
- Build & CI matrix — how the two per-OS builds are produced and versioned; how testers receive the file (download channel).
- The one-time unsigned-warning **override walkthrough doc** (screenshots, per OS).
- Exact uninstall mechanics per OS (what the installer registers; how the keyring entry is cleared on uninstall).
- macOS/Windows minimum-version targets.

---

## 15. Coordination flag

- **Implementation gated on A2.** A2 (cache + smart reconcile) is docs-only as of 2026-06-14. Slice B design/spec/research/plan can run now; implementation waits for A2 to land.
- **ADR numbering:** this grill produced **ADR-0016** (native, not Docker). Phase 7B concurrently produced ADR-0014/0015. Watch for further collisions if Phase 7 work is active.
