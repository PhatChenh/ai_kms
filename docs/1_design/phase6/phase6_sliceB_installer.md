# Phase 6 Slice B — Installable Daemon App (Design)

_Created: 2026-06-14_
_Stage: `/codebase-design-analysis` output → input to `/writing-detailed-specs`_
_Behavior-inventory prefix: **`P6-SLICEB-*`** (entries `P6-SLICEB-01 … 10`, all `status: planned`, `origin: design`, `granularity: outcome`)_
_Source of truth for direction: `docs/0_draft/cloud_native_rearchitecture.md`. Decisions locked by the grill: `docs/0_draft/phase6/phase6_sliceB_grill.md`. Packaging ADR: `docs/architecture/system_adr/0016-daemon-ships-as-native-app-not-docker.md`._

> **Reader note.** Plain English leads every section. Code references (file:line, symbol names) live in parentheses or sub-bullets — the doc reads correctly if every `code`-formatted token is deleted. A glossary table sits at the top.

---

## In plain terms

Today the sync daemon works, but only a programmer can run it: you set an environment variable with a secret key, write a small config file by hand, and type a command in a terminal. Slice B wraps that exact same daemon in a **double-click desktop app** a busy, non-technical manager installs and sets up in about two minutes — pick your notes folder, paste one key, click "test & save", done. From then on it runs quietly in the background and restarts itself every time you log in. A tiny menu-bar icon is the only thing you see: it tells you it's alive and lets you quit.

Nothing about *how the daemon syncs* changes. We are not touching watch, extract, upload, or reconcile. We are adding a setup wizard, secure key storage, a tray icon, a startup hook, and an installer around the existing engine.

---

## Cast of characters (symbols used 3+ times)

| Name | Plain-English role |
|---|---|
| Daemon core | The existing sync engine — watch the folder, pull out text, upload to the cloud (`src/daemon/`: `watcher.py`, `extractor.py`, `uploader.py`, `event_reporter.py`, `scanner.py`) |
| `DaemonConfig` | The validated settings object the daemon runs on — folder path, endpoint, key, tuning knobs (`daemon/config.py:26`) |
| `load_daemon_config` | The function that reads the settings file and pulls the key from the environment (`daemon/config.py:88`) |
| `start` command | The existing entry point that scans once then watches forever (`daemon/cli.py:148`) |
| Cloud endpoint | The user's own AgentBase server the daemon uploads to; the wizard tests it (`mcp_server/api.py`) |
| OS-glue seam | The one boundary hiding the only two per-operating-system pieces (start-at-login + tray) behind a shared interface |
| Keyring | The cross-platform library that stores the key in the OS secure vault (new dependency) |
| Wizard | The one-time Tkinter setup window |
| Tray | The menu-bar status icon (`pystray`, new dependency) |

---

## Decision

**Chosen: Option A — Supervisor app, one process, function-dispatch OS seam, compiled-constant default endpoint, single PyInstaller entry with run-modes, uninstall delegates to a `daemon uninstall` cleanup command.**

In one sentence: a single packaged app boots into a small **supervisor** that decides whether to show the wizard or go straight to running the daemon-in-a-thread plus the tray, with the only two OS-specific behaviours (login-registration and tray) selected at startup by a plain `platform.system()` check behind one shared interface — and the installer's uninstaller calls back into our own code to wipe the secret from the OS vault.

Why this over the alternatives: it keeps **one process and one binary** (simplest possible thing a non-technical user can't break), adds **no speculative abstraction** (the OS seam has exactly two real implementations — that is the genuine 2-adapter seam, not a guess), and routes the one genuinely dangerous cleanup — removing the secret — through code we own and test rather than fragile installer scripting.

---

## Q1 Diagram — Supervisor app, what happens inside

```
# Installable Daemon App — What Happens Inside
Scope: Shows what happens from launching the app to a running, syncing daemon.
       Does NOT show how the daemon syncs internally (that is the existing
       daemon core, unchanged) or the build/uninstall steps (see Q1-build below).

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
            box here — they are unchanged from Slices A1/A2.
```

### Q1 Diagram — How the build bakes the default endpoint (supporting view)

```
# Build Step — Where the Default Endpoint Comes From
Scope: Shows how one generic per-OS app ends up pre-filled with a default
       cloud address. Does NOT show signing (the app is unsigned this slice).

How to read this:
  Boxes  = build steps in order
  Arrows = what feeds into the next step

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

---

## Guardrail Checklist

From `/guardrail-check Review` (domains: Async & CLI, Architecture, Daemon Sync). This is required input for `/writing-detailed-specs`.

- [ ] **C-10 · CLI commands wrap async pipelines with `asyncio.run()`** — _Danger:_ an async daemon-start path without a sync `asyncio.run` wrapper. _Check:_ the supervisor must start the daemon through the existing `asyncio.run(_run())` path (`daemon/cli.py:248`), not a new bare event loop hand-rolled in the tray thread.
- [ ] **C-11 · `load_dotenv` called exactly once, in `cli/main.py`** — _Danger:_ `load_dotenv()` inside library code. _Check:_ the keyring read injects the key into `KMS_DAEMON_API_KEY` directly; it must NOT add a `load_dotenv` call anywhere in `daemon/`.
- [ ] **C-12 · Every public function in `handlers/`/`pipelines/` returns a Result** — _Check:_ wizard/tray/keyring code lives in `daemon/` (not `handlers/`/`pipelines/`), so this is advisory; follow the `Result` pattern for the connection-test and keyring functions for consistency with the codebase.
- [ ] **C-18 · Daemon cache is advisory; cloud is authority** — _Danger:_ packaging code that touches cache/sync. _Check:_ Slice B adds **zero** sync/cache logic — confirmed by behavior `P6-SLICEB-09`. The supervisor only *starts* the daemon; it never reaches into its sync path.

Domains skipped: Write Safety, DB Integrity, LLM & Providers, Testing — not touched by packaging.

---

## Implications

- The daemon's secret key stops living in an environment variable you set by hand and starts living in the operating system's encrypted vault, read automatically at startup.
  - New dependency `keyring` reads/writes the key under a fixed service name; the supervisor reads it then sets `os.environ["KMS_DAEMON_API_KEY"]` **before** `load_daemon_config` runs, so the daemon's own key-read path is untouched (`daemon/config.py:125` still does `os.environ.get("KMS_DAEMON_API_KEY")`). This satisfies the rearchitecture's secret-handling without editing `config.py`.

- The setup window must prove the key actually works before it lets the user finish — a wrong key can no longer slip through and silently sync nothing.
  - The wizard's test call must hit the **authenticated** `GET /api/state` endpoint (`mcp_server/api.py:227`, gated by `require_key` at `api.py:37`), NOT the open `GET /health` (`api.py:65`, never gated). Confirmed: `/api/state` exists and is authed — this dependency for grill decision #5 is **satisfied today**, no new endpoint needed.

- The app must run on both macOS and Windows from day one, but only two pieces actually differ between them; everything else is the portable Python the daemon already is.
  - The two per-OS pieces are start-at-login (LaunchAgent vs. Task Scheduler / registry Run key) and the tray icon (`pystray` handles both but registration is OS-specific). The shared core — `watcher.py`, `extractor.py`, `uploader.py`, `event_reporter.py`, `scanner.py`, `config.py` — is already OS-neutral (watchdog, httpx, pydantic).

- The daemon already runs as an async loop you stop with Ctrl+C; the app version needs a way to stop it that isn't a keyboard interrupt in a terminal.
  - The existing `start` command runs `asyncio.run(_run())` and catches `KeyboardInterrupt` (`daemon/cli.py:247-251`). The supervisor must run that same loop on a background thread and expose a clean stop signal the tray's "Quit" can trigger, replacing the Ctrl+C escape.

- The bundle stays small because the daemon carries no AI model — verified, not assumed.
  - **[VERIFIED]** A runtime import of `daemon.cli` plus the full handler-registration chain (`handlers/__init__.py`, which pulls `markdown_handler` → `vault.reader`) loads **zero** of `sentence_transformers`, `torch`, or `retrieval.*` into `sys.modules`. Those live only in `src/retrieval/` (`embeddings.py`, `reranker.py`), which the daemon never imports. Grill decision #12 confirmed.

- The text extractors the daemon bundles are real Python packages PyInstaller must collect, or extraction silently falls back to raw-bytes upload.
  - Handlers import `pypdf` (`pdf_handler.py:12`), `python-docx` (`docx_handler.py:17` → `from docx import Document`), `openpyxl` (`xlsx_handler.py:17`), plus `bs4`, `requests`, `youtube_transcript_api` (`url_fetcher.py`). PyInstaller hidden-imports / hooks must include these. If missed, `extract` returns `BinaryContent` and the cloud re-extracts — degraded but not broken (`extractor.py:159-176`).

- Module-depth check: the new wrapper introduces exactly one new seam, and it earns its keep.
  - **Deletion test on the OS-glue seam:** delete it and the per-OS `if macOS / if Windows` branches reappear scattered across the supervisor, tray, and uninstall code — so the seam concentrates that complexity (good, earns keep). **Seam reality:** it has exactly **two** adapters (Mac + Windows) — a real 2-adapter seam, not speculative. The daemon core modules are already deep (small public surface, real implementation) and are **not** modified.

---

## Known tradeoffs

- **One process, not three.** We run the wizard, tray, and daemon in a single process (daemon on a background thread). This is the simplest thing for a non-technical user — there is one app to quit, one thing to crash or not. The cost: a hard crash in the tray or wizard code could take down the sync loop. We accept this because the sync loop is the mature, tested part and the wrapper code is thin; a multi-process design (a supervisor that respawns a separate daemon process) buys resilience the tester phase doesn't need yet and adds inter-process plumbing a 2-minute-install product shouldn't carry.

- **Function-dispatch OS seam, not a plugin registry.** We pick the Mac-or-Windows implementation with a plain `platform.system()` check behind one shared interface, rather than a self-registering handler registry like the codebase uses for file handlers. We give up the "drop a new file, it registers itself" elegance — but there are exactly two operating systems and no third on the horizon, so a registry would be a speculative 1-pattern-for-2-cases abstraction. Reversible: if Linux desktop ever lands, adding a third branch is a one-line change.

- **Compiled-constant default endpoint, not a bundled editable data file.** The default cloud address is a constant in the packaged code, read at build time. We give up the ability to change the default *without* a rebuild — but a new default already means a new release (the whole point of "one generic build per OS"), so a separate data file would be moving parts for no gain. The field is editable regardless, so a tester is never blocked by a stale default.

- **Uninstall delegates the secret-wipe to our own code, not the installer's native scripting.** The installer calls a small `daemon uninstall` cleanup command that removes the keyring entry, config, and startup registration; the native installer just removes the app files and invokes that command. We give up "pure native uninstaller" simplicity — but clearing an OS-vault entry from Inno Setup / a DMG is fragile and easy to get wrong, and a stranded secret is the one cleanup failure that actually matters (decision #10). Routing it through tested Python is the safer trade.

---

## Risks (for research / planning to verify)

- **PyInstaller + watchdog on Windows.** Watchdog's Windows backend (`ReadDirectoryChangesW`) and `pystray`'s Windows tray have known PyInstaller packaging quirks (hidden imports, hooks). Research must confirm both survive a frozen build on a real Windows box — the ADR already flags no cross-compile.
- **Tray event loop vs. asyncio loop interaction.** `pystray` runs its own native UI loop and the daemon runs an asyncio loop; these must coexist (pystray on the main thread, daemon's `asyncio.run` on a worker thread, or vice versa per OS). Research must pin which loop owns the main thread on each OS — `pystray` typically requires the main thread on macOS.
- **Keyring backend availability when frozen.** `keyring` auto-selects a backend; a PyInstaller bundle may not include the macOS/Windows backend modules unless hidden-imported. Research must confirm the correct backend loads in the frozen app, not just in `uv run`.
- **First-run detection.** The supervisor decides wizard-vs-run by checking config + key presence/validity. Define "valid enough to skip the wizard" precisely so a half-written config doesn't strand the user in neither state.
- **Unsigned-app quarantine clearing.** Decision #12 claims login auto-launch won't re-trigger Gatekeeper after the first manual "Open Anyway." Research should confirm the quarantine flag actually clears for a LaunchAgent-launched unsigned binary.

---

## Open questions

**OQ-SB1 — Which thread owns the main thread: the tray or the sync loop?**

Right now the daemon is a terminal program that owns its whole process and stops on Ctrl+C; the tray icon library wants to run its own window loop, and the two have to share one app without fighting.

The question: does the menu-bar icon run on the app's main thread with the sync engine on a background thread, or the other way around — and is the answer the same on Mac and Windows?

**If tray-on-main (sync on a worker thread):** matches what most tray libraries require (especially macOS, which insists UI runs on the main thread) — but the sync engine's clean shutdown has to be driven from the worker thread.
**If sync-on-main (tray on a worker thread):** keeps the existing `asyncio.run` shutdown path untouched — but may not work on macOS, where the tray may refuse to draw off the main thread.

Recommendation: tray-on-main, sync-on-worker — it is the arrangement the tray library is most likely to support on both systems, and the sync engine's stop signal is a small, well-understood change. Confirm in research per OS. _Not a blocker for spec — it's an internal wiring detail._

**OQ-SB2 — What exactly counts as "already set up" so the app skips the wizard?**

Right now there is no app — the daemon just fails to start if the key or config is missing.

The question: when the app launches, what minimal check decides "go straight to running" versus "show the wizard"?

**If presence-only (config file + a key in the vault both exist):** fast and simple — but a stale or wrong key sails past and the daemon starts, then every upload fails (the exact silent-failure the wizard exists to prevent, just relocated to relaunch).
**If presence-plus-live-test (also re-run the authed test on every launch):** catches an endpoint that moved or a key that was revoked since setup — but adds a network round-trip and a failure mode to every single launch, including offline launches.

Recommendation: presence-only to *enter* running mode, but surface a connection failure through the **tray error state** rather than re-running the blocking wizard — the wizard hard-blocks once at setup (decision #5); after that, the tray is the proof-of-life that a later failure shows up on (decision #8). _Not a blocker — it's a refinement of the first-run check._

**OQ-SB3 — Is the `daemon uninstall` cleanup command a new CLI subcommand or an internal helper the installer calls?**

Right now uninstall doesn't exist; there's nothing to remove because there's no installer.

The question: should the secret-wipe live as a visible `daemon uninstall` command (alongside `start`/`scan`/`status` in `daemon/cli.py`), or as an internal function the OS uninstaller invokes without exposing it to users?

**If a CLI subcommand:** testable in isolation, runnable by hand if an uninstall half-fails, consistent with the existing Click group (`daemon/cli.py:70`) — but it's a user-visible command that's really plumbing.
**If an internal helper:** hidden from users — but harder to invoke for recovery and slightly harder to test end-to-end.

Recommendation: a CLI subcommand. It matches the existing command pattern, is trivially testable (a Result-returning function the command wraps), and gives a manual recovery path if the native uninstaller misfires. _Not a blocker — a spec-level placement choice._

---

## ADR references

- **ADR-0016** (`docs/architecture/system_adr/0016-daemon-ships-as-native-app-not-docker.md`) — locks "native PyInstaller app, not Docker." This design implements that decision; it does not re-litigate it.
- **ADR-0013** (hybrid cache) and **C-18** (Daemon Sync) — constrain the wrapped daemon; this slice adds no sync/cache logic, so it neither extends nor violates them.
- **No new ADR proposed.** The open design choices here (one process, function-dispatch seam, compiled-constant endpoint, delegated uninstall cleanup) are reversible internal-structure calls, not hard-to-reverse architecture-shaping decisions that a future reader would be surprised by — ADR-0016 already covers the one surprising, hard-to-reverse decision (native vs. Docker).

---

## Options explored

### Option A — Supervisor app, one process, function-dispatch seam (CHOSEN)
One binary boots a small supervisor that branches wizard-vs-run, runs the daemon on a background thread plus the tray, selects the OS-specific login-registration and tray pieces by a `platform.system()` check behind one shared interface, bakes the default endpoint as a compiled constant, and delegates the secret-wipe on uninstall to a `daemon uninstall` cleanup command.
- **Why chosen:** simplest thing a non-technical user can't break (one app, one quit), no speculative abstraction (the seam has exactly two real adapters), and the one dangerous cleanup runs through tested code.

### Option B — Two-process supervisor (separate daemon subprocess)
A lightweight tray/supervisor process launches and monitors the daemon as a **separate** child process, respawning it if it dies.
- **Files touched:** same set plus inter-process control plumbing (a control socket or signal channel between supervisor and daemon).
- **Not selected because:** it buys crash-resilience the tester phase doesn't need and adds inter-process plumbing — a moving part that can itself fail and confuse a non-technical user. The deletion test fails: the second process is mostly a pass-through wrapper around the same `start` command. Revisit if the daemon proves crash-prone in the field.

### Option C — Self-registering OS-glue registry (mirror the file-handler pattern)
Per-OS startup + tray implementations self-register at import, like the codebase's `HandlerRegistry` for file types, and the supervisor resolves by current OS.
- **Not selected because:** it's a 1-pattern-for-2-cases abstraction — a speculative seam (the design lens flags introducing an interface with only the adapters you'll ever have). Two operating systems with no third on the horizon don't justify a registry; a plain `platform.system()` branch is honest about the real cardinality.

### Rejected alternatives (one line each)
- **Key in YAML / env file shipped with the app** — rejected: decision #4 + rearchitecture secret-handling forbid a plaintext secret; the OS vault is the locked choice.
- **Wizard tests against `/health`** — rejected: `/health` is never gated (`api.py:65`), so it can't detect a bad key — decision #5 requires the authed `/api/state`.
- **Localhost web-UI wizard instead of Tkinter** — rejected: decision #10/#11 lock Tkinter (stdlib, native folder picker, zero new dep) for a window seen once for ~90 seconds.
- **rumps for the tray (macOS-native)** — rejected: Mac-only; decision #3/#8 require the cross-platform `pystray`.
- **Plain zip + manual delete uninstall** — rejected: would strand the secret in the OS vault (decision #10 requires the keyring entry be removed).
- **Per-tester hardcoded-endpoint build** — rejected: decision #7 chose one generic build with an editable baked default to avoid the per-tester rebuild treadmill.

---

## Next step

Design doc written. Run `/writing-detailed-specs` to structure Option A into build steps (the supervisor, the OS-glue seam with its two adapters, the keyring read/write, the Tkinter wizard with the authed connection test, the tray, the build spec, and the `daemon uninstall` cleanup command). Research should resolve OQ-SB1 (thread ownership per OS) and the PyInstaller packaging risks before planning.
