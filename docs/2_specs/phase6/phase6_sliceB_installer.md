# Phase 6 Slice B — Installable Daemon App (Spec)

_Created: 2026-06-14_
_Stage: `/writing-detailed-specs` output → input to `/research` then `/plan-from-specs`_
_Design source: `docs/1_design/phase6/phase6_sliceB_installer.md` (Option A, 12 locked decisions)_
_Grill: `docs/0_draft/phase6/phase6_sliceB_grill.md` · ADR: `docs/architecture/system_adr/0016-daemon-ships-as-native-app-not-docker.md`_
_Success criteria: `docs/system_behavior/behavior_inventory.yaml` prefix **`P6-SLICEB-*`** (see Handoff — entries NOT yet present in the inventory)_

> **Reader note.** Plain English leads every section. Code references (`file:line`, symbol names) live in parentheses or sub-bullets — the doc reads correctly if every `code`-formatted token is deleted.

---

## Purpose

Today the sync daemon only a programmer can run: you set a secret key in an environment variable, hand-write a config file, and type a terminal command. This phase wraps that **exact same daemon** in a double-click desktop app (Mac + Windows) that a non-technical manager installs and sets up in about two minutes — pick a notes folder, paste one key, click "test & save," done. From then on it runs quietly in the background, restarts at every login, and shows a small menu-bar icon as its only visible surface.

After this phase, a non-technical tester can install, set up, and run the sync daemon with no terminal, no environment variables, and no hand-written config — and the daemon's secret key lives in the operating system's encrypted vault instead of a plaintext environment variable. **Nothing about how the daemon syncs changes.**

---

## Glossary (plain-English names — used throughout)

| Plain-English name | What it is | Code anchor |
|---|---|---|
| App Supervisor | The single packaged app's brain — decides setup-vs-run on launch, runs the engine on a background thread, owns clean shutdown | new module in `src/daemon/` (e.g. `app.py`) |
| Setup Wizard | The one-time setup window (folder picker, cloud address, key field, "test & save") | new module (Tkinter, stdlib) |
| Status Tray | The menu-bar / system-tray icon showing alive/error state with a Quit item | new module (`pystray`, new dep) |
| Secret Vault | The operating system's encrypted key store | new dep `keyring` |
| Cloud Connection Check | The authenticated cloud endpoint the Wizard calls to prove key + address work | `mcp_server/api.py` `GET /api/state` (gated) |
| Sync Engine | The existing, unchanged daemon core — watch, extract, upload, reconcile | `src/daemon/` (`watcher.py`, `extractor.py`, `uploader.py`, `event_reporter.py`, `scanner.py`) |
| OS-Glue Seam | The one boundary hiding the only two per-OS behaviours (start-at-login + tray registration) behind a shared interface | new module |
| Auto-Start Registrar | The start-at-login hook — Mac login agent vs. Windows scheduled task / Run key | OS-Glue Seam's two adapters |
| Packager | The build tool that bundles everything into a Mac DMG or Windows installer and bakes in the default cloud address | PyInstaller + DMG/Inno spec files |
| Uninstall Cleanup | The command that wipes the key from the Secret Vault, removes config, and unregisters auto-start | new `daemon uninstall` CLI subcommand |

---

## Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `DaemonConfig` | `daemon/config.py:26` | Validated settings object: folder path, cloud address, key, tuning knobs; `extra:"forbid"`, `api_key` excluded from serialization | The Wizard writes the YAML this loads; the Supervisor never bypasses it | deep |
| `load_daemon_config` | `daemon/config.py:88` | Reads `~/.kms-daemon/config.yaml`, pulls the key from `KMS_DAEMON_API_KEY` env, validates | The Supervisor reads the key from the Secret Vault, sets the env var, then calls this **unchanged** | deep |
| `start` command body (`_run`) | `daemon/cli.py:148-251` | Startup scan, then watcher loop on `asyncio.run`, `Ctrl+C` → graceful `watcher.stop()/join()` | The Supervisor runs this same loop on a background thread; replaces `Ctrl+C` with a thread-safe stop signal | deep |
| `DaemonWatcher` + extract/upload/report stages | `daemon/watcher.py`, `extractor.py`, `uploader.py`, `event_reporter.py`, `scanner.py` | The whole sync pipeline, OS-neutral (watchdog, httpx, pydantic) | Packaged as-is; **not modified** | deep |
| Click CLI group | `daemon/cli.py:70` (`cli`) + `__main__.py` | `start` / `scan` / `status` subcommands under one group | `daemon uninstall` is added as a fourth subcommand to this group | deep |
| `GET /api/state` (gated) | `mcp_server/api.py:243`, gated by `require_key` at `api.py:46`/`263`; 401 on bad/missing key | Lists cloud documents; requires `Authorization: Bearer <key>` | The Wizard's live connection test hits **this** (authed), proving the key works — not `/health` | deep |
| `GET /health` (open) | `mcp_server/api.py:74` | Never gated; returns `{"status":"ok"}` for any caller | Explicitly **not** used by the Wizard test (a bad key would pass) | shallow |
| `Result` type (`Success`/`Failure`) | `core/result.py` | Typed success/error return | New connection-test, keyring, and uninstall functions return `Result` for consistency (C-12 advisory here — see Constraints) | deep |
| `[project.scripts] kms` | `pyproject.toml:40-41` | Console-script entry for the main CLI | The packaging entry point is the daemon app, not `kms`; the `daemon` group already runs via `python -m daemon` (`__main__.py`) | shallow |

---

## Q1 Diagram — Supervisor app, what happens inside (from design)

```
# Installable Daemon App — What Happens Inside
Scope: Shows what happens from launching the app to a running, syncing daemon.
       Does NOT show how the daemon syncs internally (that is the existing
       daemon core, unchanged) or the build/uninstall steps.

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

            App launched
                 │
                 ▼
        ┌─────────────────────┐
        │ Is this set up yet? │
        │ (config + key       │
        │  present & valid?)  │
        └──────────┬──────────┘
            ┌──────┴───────┐
           NO              YES
            │               │
            ▼               │
   ┌──────────────────┐     │
   │ Show setup       │     │
   │ wizard: folder,  │     │
   │ endpoint, key    │     │
   └────────┬─────────┘     │
            │               │
            ▼               │
   ┌──────────────────┐     │
   │ Live test the    │     │
   │ key + endpoint   │     │
   │ (authed call)    │     │
   └────────┬─────────┘     │
       ┌────┴────┐          │
     FAIL       PASS        │
       │         │          │
       ▼         ▼          │
  ┌────────┐ ┌──────────────┴───┐
  │ Show   │ │ Save settings +  │
  │ error, │ │ store key in OS  │
  │ retry  │ │ secure vault     │
  └────────┘ └────────┬─────────┘
                      ▼
            ┌───────────────────┐
            │ Start daemon core │
            │ + show tray;      │
            │ register at login │
            └───────────────────┘

Simplified: "register at login" only happens once (first successful setup), not
            every launch. The daemon-core internals (watch/extract/upload) are one
            box here — unchanged from Slices A1/A2.
```

### Q1 Diagram — How the build bakes the default endpoint (supporting view, from design)

```
# Build Step — Where the Default Endpoint Comes From
Scope: Shows how one generic per-OS app ends up pre-filled with a default
       cloud address. Does NOT show signing (the app is unsigned this slice).

   Build starts (Mac box, or Windows box)
                 │
                 ▼
   ┌──────────────────────────┐
   │ Read default endpoint     │
   │ value (a constant in the  │
   │ packaged code)            │
   └────────────┬─────────────┘
                ▼
   ┌──────────────────────────┐
   │ PyInstaller bundles the   │
   │ daemon core + wizard +    │
   │ tray + the constant       │
   └────────────┬─────────────┘
                ▼
   ┌──────────────────────────┐
   │ Wrap into DMG (Mac) or    │
   │ installer (Windows)       │
   └────────────┬─────────────┘
                ▼
   One generic build per OS — same file for every tester;
   the wizard pre-fills the default into an EDITABLE field
```

## Q2 Diagram — How it connects to others (new)

```
# Installable Daemon App — How It Connects
Scope: Shows what the App Supervisor touches to set up, start, and keep the
       sync engine running. Does NOT show the setup-vs-run decision flow
       (see Q1 for that) or how the Sync Engine syncs internally.

How to read this:
  Center box     = the feature being built (the App Supervisor)
  Solid boxes    = components used at runtime
  Dashed box     = build-time only (not a running connection)
  Arrow labels   = what passes between them

                          ┌──────────────────┐
                          │ Setup Wizard     │
                          │ One-time setup   │
                          │ window           │
                          └───┬──────────┬───┘
            opens on first    │          │  tests key + address
            run; hands back   │          ▼
            settings          │   ┌──────────────────┐
                              │   │ Cloud Connection │
                              │   │ Check            │
                              │   │ Proves key works │
                              │   └──────────────────┘
                              │          ▲
                              │          │ stores key
                              ▼          │
  ┌──────────────────┐   ┌────┴──────────┴────┐   ┌──────────────────┐
  │ Secret Vault     │   │   APP SUPERVISOR    │   │ Status Tray      │
  │ OS encrypted     │◄──┤   Decides setup vs  ├──►│ Menu-bar icon;   │
  │ key store        │   │   run; runs engine  │   │ alive / error    │
  └──────────────────┘   │   on a background   │◄──┤ state + Quit     │
       reads key ─────►  │   thread            │   └──────────────────┘
       at every launch   └────┬──────────┬─────┘    shows status ─►
                              │          │          ◄─ Quit
            starts on a       │          │  registers once
            background        │          │  at first setup
            thread /          ▼          ▼
            stop signal  ┌──────────┐ ┌──────────────────┐
                         │ Sync     │ │ Auto-Start       │
                         │ Engine   │ │ Registrar        │
                         │ Watch +  │ │ Relaunch every   │
                         │ upload   │ │ login            │
                         └──────────┘ └──────────────────┘

  Build-time only (not a running connection):
              ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
              │ Packager            │
              │ Bundles everything  │
              │ into a Mac or       │
              │ Windows installer;  │
              │ bakes in the        │
              │ default cloud       │
              │ address             │
              └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
                       ╎
                       ╎ produces the installable app
                       ▼
                  (the App Supervisor)

Simplified: The Wizard's "save settings" and the Supervisor's first-run "register
            at login" both happen once, at first setup. The Packager is drawn
            separately because it runs on the developer's machine at build time,
            not on the user's machine at runtime.
```

---

## Feature overview

**Happy path (first install).** The tester downloads one file per OS (a Mac DMG or a Windows installer), runs it, and double-clicks the app. The **App Supervisor** boots and asks one question: *is this already set up?* On a fresh machine the answer is no, so it opens the **Setup Wizard** — a small window with a folder picker (the notes folder), a cloud-address field pre-filled with the baked-in default (editable), and a field to paste the key. The tester clicks "test & save." The Wizard runs a **live connection test** against the authenticated cloud endpoint: a good key + reachable address passes; anything wrong fails with a clear error and the tester can retry. On pass, the Wizard writes the settings to the daemon's config file, stores the key in the OS **Secret Vault**, and the Supervisor registers the app with the **Auto-Start Registrar** so it relaunches at every login. The Supervisor then reads the key back from the Secret Vault, hands it to the existing config loader through the environment variable the daemon already expects, starts the **Sync Engine** on a background thread, and shows the **Status Tray** icon. From then on the only visible surface is that icon.

**Happy path (every later launch).** The Supervisor's setup check passes (config file + a key in the vault both present), so it skips the Wizard, reads the key, starts the Sync Engine on a background thread, and shows the tray. No window appears.

**Stopping.** The tester clicks Quit in the tray. The Supervisor signals the Sync Engine to stop cleanly (replacing the old `Ctrl+C` path), waits for the watcher to wind down, and exits.

**Uninstalling.** The native uninstaller removes the app files and invokes the **Uninstall Cleanup** command, which wipes the key from the Secret Vault, removes the config file, and unregisters auto-start — so no secret is left stranded in the OS vault.

**Edge cases.**
- *Bad or missing key at setup* — the live test fails; the Wizard shows the error and stays open for a retry. The user can never finish setup with a non-working key.
- *Key revoked or endpoint moved after setup* — the setup check is presence-only, so the app enters running mode and the Sync Engine's uploads start failing; this surfaces through the **tray error state**, not a re-opened blocking Wizard (see OQ-SB2).
- *Half-written config* — the setup check must define "valid enough to skip the Wizard" precisely so a partial config doesn't strand the user in neither state (see OQ-SB2 / A6).
- *Crash in wrapper code* — because everything is one process, a hard crash in the tray or wizard can take down the sync loop; accepted tradeoff (the wrapper is thin, the engine is mature).
- *Unsigned-app quarantine (Mac)* — the first launch needs a manual "Open Anyway"; auto-launch at login is claimed not to re-trigger Gatekeeper (must be confirmed — see Risks).

---

## Out of scope

- **Changing how the daemon syncs** — watch, extract, upload, reconcile, cache are untouched. Slice B adds **zero** sync/cache logic (behavior `P6-SLICEB-09`; C-18). Handled by Slices A1/A2.
- **Code signing / notarization** — the app ships unsigned this slice (ADR-0016). A future hardening phase handles signing.
- **Linux desktop** — only Mac + Windows. A third OS would add one branch to the OS-Glue Seam later (function-dispatch, reversible). Deferred — no phase assigned.
- **Multi-process supervisor (crash-respawn resilience)** — Option B; rejected for the tester phase. Deferred — revisit only if the daemon proves crash-prone in the field.
- **Self-registering OS-glue registry** — Option C; rejected as a 1-pattern-for-2-cases abstraction. Not planned.
- **Changing the default endpoint without a rebuild** — the default is a compiled constant; a new default means a new release. The field is editable so testers are never blocked. Out of scope by design (decision #7).
- **Auto-update / in-app upgrade** — not in this slice. Deferred — no phase assigned.
- **Re-running the live connection test on every launch** — rejected (OQ-SB2); presence-only entry, failures surface via the tray.

---

## Constraints

- **C-10 · CLI/async wrapping** — the Supervisor must start the Sync Engine through the existing `asyncio.run(_run())` path (`daemon/cli.py:248`), running it on a background thread; it must NOT hand-roll a new bare event loop in the tray thread. _Source: CLAUDE.md / CONSTRAINTS.md C-10; design Guardrail Checklist._
- **C-11 · `load_dotenv` once, in `cli/main.py` only** — the Secret Vault read must inject the key by setting `os.environ["KMS_DAEMON_API_KEY"]` directly; it must NOT add a `load_dotenv()` call anywhere in `daemon/`. _Source: CONSTRAINTS.md C-11._
- **C-12 · Result returns (advisory here)** — wizard/tray/keyring/uninstall code lives in `daemon/`, not `handlers/`/`pipelines/`, so C-12 is advisory; follow the `Result` pattern for the connection-test, keyring, and uninstall functions for consistency. _Source: CONSTRAINTS.md C-12; design Guardrail Checklist._
- **C-18 · Daemon cache is advisory; cloud is authority** — Slice B adds no sync/cache logic; the Supervisor only *starts* the daemon and never reaches into its sync path. _Source: CONSTRAINTS.md C-18; behavior `P6-SLICEB-09`._
- **Secret never in plaintext** — the key lives only in the OS Secret Vault, never in YAML, never in a shipped env file (decision #4; rearchitecture secret-handling). `DaemonConfig.api_key` is already `exclude=True` (`daemon/config.py:40`).
- **Config loader untouched** — `load_daemon_config` still reads the key from `KMS_DAEMON_API_KEY` (`daemon/config.py:125`); the Supervisor satisfies it via the env var rather than editing `config.py`. _Source: design Implications._
- **ADR-0016 is settled** — native PyInstaller app, not Docker; this spec implements it and does not re-litigate it.

_Domains not touched by packaging: Write Safety, DB Integrity, LLM & Providers, Testing._

---

## Assumptions

| ID | Assumption | Source implication | What would prove it wrong |
|----|-----------|-------------------|--------------------------|
| A1 | The authed `GET /api/state` endpoint exists today and returns 401 on a bad/missing key, so the Wizard can use it as the live connection test. | Implication #2 | `/api/state` is removed, ungated, or returns 200 without a valid key. **(Verified in this spec: `api.py:243`/`263`, 401 on `require_key` None.)** |
| A2 | `load_daemon_config` reads the key only from `KMS_DAEMON_API_KEY`, so setting that env var before calling it injects the vault key with no `config.py` change. | Implication #1 | `config.py` reads the key from YAML or another source, or the loader caches before the env var is set. **(Verified: `config.py:125`.)** |
| A3 | The existing `start` loop (`asyncio.run(_run())` + `watcher.stop()/join()`) can run on a background thread and be stopped by a thread-safe signal instead of `Ctrl+C`. | Implication #4 | The watcher requires the main thread, or `asyncio.run` on a worker thread conflicts with the tray's UI loop on either OS (see OQ-SB1). |
| A4 | The daemon imports **zero** AI-model packages (`sentence_transformers`, `torch`, `retrieval.*`), so the bundle stays small. | Implication #5 (**[VERIFIED]** in design) | A runtime import of `daemon.cli` + handler chain pulls any of those into `sys.modules`. |
| A5 | The text extractors (`pypdf`, `python-docx`, `openpyxl`, `bs4`, `requests`, `youtube_transcript_api`) are real packages PyInstaller can collect via hidden-imports/hooks. | Implication #6 | A frozen build silently falls back to raw-bytes upload because an extractor module is missing. |
| A6 | "Valid enough to skip the Wizard" can be defined as: config file present AND a key entry exists in the Secret Vault (presence-only). | OQ-SB2 / Risks | A presence-only check strands users on a half-written config, or product requires a live re-test on every launch. |
| A7 | Exactly two per-OS behaviours differ (start-at-login + tray registration); everything else is portable. | Implication #3 / module-depth check | A third per-OS difference appears (e.g. notifications, quarantine handling) that the 2-adapter seam can't absorb cleanly. |
| A8 | The `keyring` backend for each OS loads correctly inside a frozen PyInstaller bundle (not just under `uv run`). | Risks | The frozen app can't find/load the macOS or Windows keyring backend without extra hidden-imports. |

---

## Component dependency order

> Documents what must exist before each component can work — not the order a developer writes code. Execution order is owned by `/plan-from-specs`. Build order will likely be bottom-up (seam + keyring + connection-test first, Supervisor and packaging last), mirroring the codebase's "logic-free shims built last" pattern.

### 1. Secret Vault read/write (keyring wrapper)

**Goal.** Store the daemon's key in the OS encrypted vault and read it back — replacing the hand-set environment variable.

**Build.** A small module that writes the key to the Secret Vault under a fixed service name and reads it back, both returning a `Result`. Add the `keyring` dependency (`pyproject.toml`). The read path must set `os.environ["KMS_DAEMON_API_KEY"]` directly — never call `load_dotenv` (C-11).

**Depends on.** None.

**Assumes.** A2, A8.

**Dependency category.** local-substitutable — test with a stand-in/in-memory keyring backend; do not require the real OS vault in unit tests.

**Done when.** A key written by this module is readable back in a fresh process; after the read, the daemon's existing config loader picks up the key with no edits to `config.py`; nothing in `daemon/` calls `load_dotenv`.

---

### 2. Cloud Connection Check (live key test)

**Goal.** Prove a key + cloud address actually work before the Wizard lets the user finish.

**Build.** A function that makes an authenticated request to the cloud's `GET /api/state` (`mcp_server/api.py:243`) with the given key and address, returning a `Result` — pass on `200`, fail (with a readable reason) on `401`, unreachable, or other error. Must hit the **gated** `/api/state`, never the open `/health`.

**Depends on.** None (the endpoint already exists).

**Assumes.** A1.

**Dependency category.** true-external — inject the cloud address; test with a mock HTTP client (mirror the existing `status` command's `httpx.AsyncClient` usage at `daemon/cli.py:87`).

**Done when.** A correct key + reachable address returns success; a wrong key returns a failure that names "authentication"; an unreachable address returns a failure that names "cannot reach." No call path can pass with a key that `/api/state` rejects.

---

### 3. OS-Glue Seam + two adapters (Auto-Start Registrar + tray registration)

**Goal.** Concentrate the only two per-OS behaviours — start-at-login and tray registration — behind one shared interface with exactly two real implementations (Mac, Windows).

**Build.** Define one shared interface for "register/unregister start-at-login" and "show the tray." Provide two adapters: macOS (LaunchAgent for login; `pystray` macOS tray, main-thread) and Windows (Task Scheduler / registry Run key for login; `pystray` Windows tray). Select the adapter at startup with a plain `platform.system()` check — no registry, no self-registration (decisions #6, design tradeoff). Add the `pystray` dependency.

**Interface shape.** Callers (the Supervisor) see two operations: register-at-login and show-tray. Hidden behind the interface: the OS-specific LaunchAgent/Task-Scheduler mechanics and tray wiring. Adapter count: **2** (real, not speculative).

**Depends on.** None.

**Assumes.** A3 (tray vs. asyncio thread ownership), A7.

**Dependency category.** local-substitutable — test the dispatch (`platform.system()` → adapter) with stand-ins; the real registration is verified manually per OS in research/QA.

**Decisions.**
- Q: Which thread owns the main thread — the tray or the sync loop, and is it the same on both OSes? Options: tray-on-main (sync on worker) / sync-on-main (tray on worker). Leaning **tray-on-main / sync-on-worker** because most tray libraries (esp. macOS) require the UI on the main thread. Confirm per OS in research (OQ-SB1).

**Done when.** On each OS the correct adapter is selected automatically; registering at login makes the app relaunch after a logout/login cycle; the tray icon appears and its Quit item is wired; deleting the seam would scatter `if macOS / if Windows` branches across the Supervisor, tray, and uninstall code (the seam earns its keep).

---

### 4. Setup Wizard (Tkinter)

**Goal.** A one-time window that collects the notes folder, cloud address (pre-filled, editable), and key, runs the live test, and on pass saves settings + stores the key.

**Build.** A Tkinter window (stdlib — decisions #10/#11) with: a native folder picker, a cloud-address field pre-filled from the baked-in default constant, a key field, and a "test & save" action. On action it calls the Cloud Connection Check (component 2); on fail it shows the error and stays open; on pass it writes the daemon config YAML (the shape `load_daemon_config` reads) and stores the key via the Secret Vault wrapper (component 1).

**Depends on.** Components 1 (keyring) and 2 (connection check).

**Assumes.** A1, A2.

**Dependency category.** in-process (Tkinter is stdlib) — test the save/test orchestration with stubbed components 1 and 2.

**Done when.** A non-technical user can complete setup with no terminal; a wrong key keeps the window open with a clear error and never writes settings; on success the config file exists in the shape the loader reads and the key is in the Secret Vault; the address field shows the baked default but accepts an edit.

---

### 5. App Supervisor (setup-vs-run brain + threaded engine + clean stop)

**Goal.** The single entry point that decides setup-vs-run, runs the Sync Engine on a background thread, shows the tray, and shuts down cleanly on Quit.

**Build.** A module that, on launch: (a) runs the setup check — config file present AND key in the Secret Vault (presence-only, A6); (b) if not set up, opens the Wizard (component 4) and, on success, registers at login once (component 3); (c) reads the key from the Secret Vault into `KMS_DAEMON_API_KEY`, then starts the existing `start` loop (`daemon/cli.py:_run`) via `asyncio.run` on a background thread (C-10); (d) shows the tray (component 3) and wires Quit to a thread-safe stop signal that replaces the old `Ctrl+C` path (`daemon/cli.py:247-251`). On a later launch where setup passes, it skips the Wizard. Connection failures after setup surface through the tray error state, not a re-opened Wizard (OQ-SB2).

**Depends on.** Components 1, 2, 3, 4.

**Assumes.** A2, A3, A6, A7.

**Interface shape.** The Supervisor is the app's single public entry; it hides the thread management and the setup-vs-run branch behind one "run the app" surface.

**Dependency category.** in-process — test the branch logic and stop signal with stubbed engine/tray; the real threaded run is verified manually per OS.

**Done when.** A fresh machine shows the Wizard then runs; an already-set-up machine starts running with no window; Quit stops the engine cleanly and the process exits; the engine runs through the existing `asyncio.run` path (no new bare event loop); the key reaches the engine via the env var with no `config.py` edit.

---

### 6. Uninstall Cleanup (`daemon uninstall` CLI subcommand)

**Goal.** Remove the one thing a native uninstaller can't safely remove — the key in the Secret Vault — plus the config and the auto-start registration.

**Build.** A fourth subcommand on the existing Click group (`daemon/cli.py:70`), wrapping a `Result`-returning function that: deletes the key from the Secret Vault (component 1), removes the config file, and unregisters auto-start (component 3). The native uninstaller removes app files and invokes this command (decision #10).

**Depends on.** Components 1 (keyring) and 3 (seam — unregister).

**Assumes.** A7, A8.

**Dependency category.** local-substitutable — test against a stand-in keyring and a temp config path.

**Decisions.**
- Q: CLI subcommand vs. internal helper? Options: visible `daemon uninstall` / hidden internal function. Leaning **CLI subcommand** (matches the existing command pattern, testable in isolation, gives a manual recovery path if the native uninstaller misfires) — OQ-SB3.

**Done when.** Running the command on a set-up machine leaves no key in the Secret Vault, no config file, and no auto-start registration; running it twice is safe (idempotent); it returns a `Result` describing what it removed.

---

### 7. Packager (PyInstaller + DMG/Inno + baked default endpoint)

**Goal.** Produce one generic installable file per OS (Mac DMG, Windows installer) that bundles the engine + wizard + tray and bakes in the default cloud address.

**Build.** PyInstaller spec(s) that bundle the daemon core + Supervisor + Wizard + tray, with hidden-imports/hooks for `pystray`, `keyring` (correct per-OS backend — A8), watchdog's Windows backend, and the text extractors (`pypdf`, `python-docx`, `openpyxl`, `bs4`, `requests`, `youtube_transcript_api` — A5). The default endpoint is a compiled constant read at build time (decision #7); the Wizard pre-fills it into an editable field. Wrap the Mac build into a DMG and the Windows build into an installer (e.g. Inno Setup) whose uninstaller calls `daemon uninstall` (component 6). No cross-compile and no signing (ADR-0016).

**Depends on.** Components 1–6 (everything it bundles).

**Assumes.** A4, A5, A8.

**Dependency category.** true-external (build tooling) — verified by building and launching the frozen app on a real machine per OS, not by unit tests.

**Decisions.**
- Q: Windows installer tool — Inno Setup vs. alternative? Leaning **Inno Setup** (common, scriptable uninstaller hook). Confirm in research.

**Done when.** A single DMG installs and runs on a clean Mac; a single Windows installer installs and runs on a clean Windows box; the frozen app extracts PDFs/DOCX/XLSX (no raw-bytes fallback); the keyring backend loads in the frozen app; the address field shows the baked default; the Windows uninstaller invokes `daemon uninstall` and leaves no stranded secret.

---

## Handoff notes

- **Behavior-inventory gap (action for the planner / behavior-guide step):** The design and task brief reference success criteria `P6-SLICEB-01 … 10` in `docs/system_behavior/behavior_inventory.yaml`, but a grep of the inventory found **no `P6-SLICEB-*` entries** (only `P6-A2-02 … 09` exist). The design doc states they should exist as `status: planned, origin: design, granularity: outcome`. This spec **references them by ID and does not duplicate them** per the brief — but they must actually be created (run `/update-behavior-guide`, or confirm they live elsewhere) before research/QA can trace done-criteria to them. Until then, the "Done when" lines in each component above are the working acceptance criteria.

- **Contract with Slices A1/A2:** This phase promises to package and launch the existing daemon core **unmodified**. If A2's cache/reconcile work changes the `start` entry point or the watcher's stop sequence, components 5 and 7 must re-verify against the new shape. Slice B contributes zero sync/cache logic (C-18).

- **Open uncertainties carried from design (resolve in research, not blockers for this spec):**
  - **OQ-SB1** — thread ownership (tray-on-main vs. sync-on-main) per OS. Affects components 3 and 5. Recommendation: tray-on-main / sync-on-worker.
  - **OQ-SB2** — exact "already set up" check. Affects component 5. Recommendation: presence-only entry; surface later failures via tray error state.
  - **OQ-SB3** — `daemon uninstall` as CLI subcommand vs. internal helper. Affects component 6. Recommendation: CLI subcommand.

- **Suggested research (before planning):**
  1. Resolve OQ-SB1 on real Mac + Windows — which loop owns the main thread, and whether `pystray` + `asyncio.run`-on-worker coexist (Risk: tray/asyncio interaction).
  2. Confirm `keyring`'s per-OS backend loads inside a frozen PyInstaller bundle, not just under `uv run` (A8).
  3. Confirm watchdog's Windows backend and `pystray` survive a frozen Windows build (hidden imports/hooks).
  4. Confirm the macOS unsigned-app quarantine flag clears for a LaunchAgent-launched binary after the first manual "Open Anyway" (decision #12).
  5. Pin the exact text-extractor hidden-imports/hooks so a frozen build doesn't silently fall back to raw-bytes upload (A5).
  6. Confirm `python -m daemon` vs. a new packaged entry point is the right PyInstaller entry, and whether a `[project.scripts]` daemon entry is needed.

---

## Open / deferred questions (this spec)

- **DQ-1 — Where do the new modules live?** The glossary suggests `src/daemon/app.py` (Supervisor) plus sibling modules for wizard/tray/seam/keyring. Confirm the exact module layout during planning; the daemon package (`src/daemon/`) is the natural home since it already owns config, CLI, and the sync core.
- **DQ-2 — Default-endpoint constant location.** A compiled constant (decision #7) — where it lives (e.g. a `defaults.py` in `daemon/` or a build-injected value) is a planning detail. The constraint is only that it is read at build time and surfaced as an editable Wizard field.
- **DQ-3 — Tray "error state" content.** OQ-SB2's recommendation surfaces post-setup connection failures via the tray, but the exact states (alive / syncing / error) and how the tray learns of an upload failure from the engine thread are not yet specified. Flag for design refinement before the tray is built — note this is the one place Slice B reads engine health, and it must do so without adding sync logic (C-18).
- **DQ-4 — `P6-SLICEB-*` provenance.** See Handoff: the referenced behavior IDs don't yet exist in the inventory. Resolve whether they are to be authored now or already live in a branch/file not on disk.
```
