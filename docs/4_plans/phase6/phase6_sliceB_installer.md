# Plan: Phase 6 Slice B — Installable Daemon App

_Last updated: 2026-06-15_
_Status: [~] in progress_

_Spec: `docs/2_specs/phase6/phase6_sliceB_installer.md` — the source of truth for WHAT to build (7 components, "Build" descriptions, "Done when" criteria). This plan owns HOW: ordering, TDD RED→GREEN, exact line numbers, commit boundaries._
_Research: `docs/3_research/phase6/phase6_sliceB_installer.md` — Invalidated Assumptions: **NONE**. Two mechanical refinements folded into the phases below (eight extractor libs, not six; loader raises rather than returning a Result)._
_Behavior IDs: `P6-SLICEB-01 … 10` — confirmed present in `docs/system_behavior/behavior_inventory.yaml` (lines 3772–4006). Not missing; do not author._

> **Reader note.** Plain English leads every section. Code references (`file:line`, symbol names) live in parentheses or sub-bullets — the plan reads correctly if every `code`-formatted token is deleted.

> **Gating preamble — read before starting Phase 1.** This whole plan is **blocked on Slice A2 landing.** A2 is docs-only right now (the cache/reconcile slice). Slice B promises to package and launch the **existing daemon core unmodified**. If A2 changes the daemon's `start` entry point (`daemon/cli.py:148`) or the watcher's stop sequence (`watcher.stop()/join()`), then Phase 5 (Supervisor) and Phase 7 (Packager) must re-verify against the new shape before they are built. Do not start Phase 5 until A2's daemon-core shape is frozen. Phases 1–4 and 6 touch only new modules + the Click group and can proceed independently of A2.

---

## Architecture

### Q1 — What happens inside
_Source: design doc `docs/1_design/phase6/phase6_sliceB_installer.md` (the "Supervisor app, what happens inside" diagram + the "build bakes the default endpoint" supporting view). Not re-drawn here — open the design doc. In one line: launch → "is this set up?" → NO opens the wizard (folder + endpoint + key → live authed test → save + store key) / YES skips it → start the daemon core on a background thread + show the tray; register at login once._

### Q2 — How it connects
_Source: spec doc `docs/2_specs/phase6/phase6_sliceB_installer.md` (the "How It Connects" hub-and-spoke). Not re-drawn here — open the spec. In one line: the **App Supervisor** is the hub; it reads the key from the **Secret Vault**, runs the **Sync Engine** on a worker thread, shows the **Status Tray**, registers via the **Auto-Start Registrar**, and the **Setup Wizard** (one-time) calls the **Cloud Connection Check**. The **Packager** is build-time only._

### Q3 — Why build it this way

```
# Installable Daemon App — Why This Way
Scope: The existing interfaces and patterns the new App Supervisor must conform
       to, and why each one is a hard rail (not a choice). Same names and centre
       box as Q1/Q2 — this is a zoom-out, not a new picture.

How to read this:
  Centre box        = the feature being built (the App Supervisor)
  Surrounding boxes = existing rails the build must respect
  Lines             = which rail constrains the Supervisor

  ┌────────────────────────┐        ┌────────────────────────┐
  │ Existing start path     │        │ Secret-via-environment  │
  │ Run the engine through  │        │ contract                │
  │ the same run-once-then- │        │ The engine only reads   │
  │ watch loop on a worker  │        │ its key from one env    │
  │ thread; stop it with a  │        │ slot — set it from the  │
  │ thread-safe signal, not │        │ vault BEFORE the loader  │
  │ a keyboard interrupt    │        │ runs; never hand-write   │
  │ (C-10: no new loop)     │        │ a key file (C-11)        │
  └───────────┬────────────┘        └───────────┬────────────┘
              │                                  │
              │      ┌──────────────────────┐    │
              └─────►│   APP SUPERVISOR      │◄───┘
                     │   Decides setup vs    │
              ┌─────►│   run; runs engine    │◄───┐
              │      │   on a worker thread  │    │
              │      └──────────────────────┘    │
              │                                   │
  ┌───────────┴────────────┐        ┌─────────────┴──────────┐
  │ Authed test endpoint    │        │ Existing command group  │
  │ The wizard proves the   │        │ Add the uninstall step  │
  │ key against the LOGGED- │        │ as one more command     │
  │ IN cloud check, not the │        │ alongside the existing  │
  │ open health ping (which │        │ ones — no edits to the  │
  │ would pass a bad key)   │        │ commands already there  │
  └────────────────────────┘        └────────────────────────┘

           ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
           │ Zero sync/cache logic (C-18)         │
           │ The Supervisor only STARTS the       │
           │ engine and reads its alive/error     │
           │ state for the tray — it never        │
           │ reaches into how the engine syncs    │
           └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘

Why these four rails, in plain terms:
  - The engine is mature and tested. We wrap it, we do not re-open it. So we
    must enter it the way it already starts, and leave it the way it already
    stops (just triggered by a click instead of a keypress).
  - The secret must never touch a file. The engine already reads it from one
    place; we fill that place from the OS vault, so nothing in the engine
    changes.
  - A setup that "succeeds" with a dead key is worse than no setup. Only the
    logged-in check can tell a good key from a bad one.
  - Uninstall is just one more command in a group that already takes commands —
    no new machinery, and it is testable and hand-runnable for recovery.

Simplified: the dashed box is a "do-not-cross" boundary, not a connection —
            it is drawn to make the C-18 line explicit, since the tray DOES
            read engine health and a reader might mistake that for sync logic.
```

**Extension-point marking for the components this plan introduces:**

- Secret Vault wrapper — `[extensible: protocol]` Result-returning read/write/delete over `keyring`; callers depend on the three functions, not on `keyring`.
- Cloud Connection Check — `[closed]` single function against one endpoint; adding a second probe endpoint would edit this file. Acceptable — there is exactly one authed endpoint and no second on the horizon (mirrors the design's "honest cardinality" stance). Flagged, not a violation.
- OS-Glue Seam — `[extensible: protocol]` one shared interface, two adapters selected by `platform.system()`. A third OS = one new adapter + one dispatch branch (design tradeoff, reversible). Deliberately **not** a self-registering registry (Option C rejected — 1-pattern-for-2-cases).
- Setup Wizard — `[closed]` one window; not designed for variants.
- App Supervisor — `[closed]` the single app entry point; intentionally one surface.
- Uninstall command — `[extensible: registry]` a fourth `@cli.command()` on the existing Click group — self-registers by decoration, zero edits to existing commands.
- Packager — `[extensible: config]` PyInstaller spec(s) + installer scripts; per-OS behaviour is data in the spec files.

---

## Approach

Build **bottom-up**, exactly as the spec's dependency order and the codebase's "logic-free shims built last" pattern dictate: the leaf utilities the Supervisor leans on (Secret Vault, Connection Check, OS-Glue Seam) come first and are independently green; the Wizard composes the first two; the Supervisor composes all of them; uninstall is a thin command over the leaves; the Packager bundles everything and is verified only on real hardware. This keeps every phase testable at its own boundary with stubbed dependencies, and isolates the two genuinely un-unit-testable concerns — real OS registration and the frozen build — into their own phases where "verification" means a manual checklist on a real machine, not a pytest assertion.

Why bottom-up rather than top-down: the Supervisor is the one piece that cannot be tested without its dependencies existing, and the dependencies (keyring, connection-check) are pure and easy to test in isolation. Inverting the order would force heavy mocking of not-yet-designed leaves.

---

## Phases

### Phase 1 — Secret Vault wrapper (keyring)

**Goal**: Store the daemon's key in the OS encrypted vault and read it back, replacing the hand-set environment variable — with the read path setting the env slot the engine already expects.

**Implements**: spec component **1** (Secret Vault read/write). Behavior `P6-SLICEB-04` (key in OS vault, not plaintext). See spec §"Component dependency order → 1" for Build + Done-when.

**Design**:
```
# Secret Vault wrapper — folder/flow
src/daemon/secret_vault.py   (NEW)
   store_key(key)   ──►  keyring sets entry under fixed service name
   read_key()       ──►  keyring gets entry  ──► returns Result[str]
   load_key_into_env() ──► read_key() ──► os.environ["KMS_DAEMON_API_KEY"]=key
   delete_key()     ──►  keyring deletes entry  (used by Phase 6 uninstall)

   RULE: load_key_into_env sets the env slot DIRECTLY — never load_dotenv (C-11)
```

**Steps** (TDD RED→GREEN):
1. Add `keyring` to `pyproject.toml` dependencies (`[project] dependencies`, near the existing `httpx`/`click` block, `pyproject.toml:21-31`). **New dependency — see Open Questions; do not install silently if the orchestrator requires sign-off.** `uv sync`.
2. RED: write `tests/test_daemon/test_secret_vault.py`. Use `keyring.set_keyring(...)` with an in-memory/stand-in backend in a fixture (dependency category: local-substitutable — do NOT touch the real OS vault). Tests: store-then-read round-trips; read-when-absent returns `Failure`; `load_key_into_env` puts the value in `os.environ["KMS_DAEMON_API_KEY"]`; `delete_key` then read returns `Failure`.
3. GREEN: implement `src/daemon/secret_vault.py` with `store_key`, `read_key`, `delete_key`, `load_key_into_env`, all returning `Result` (C-12 advisory, follow it). Fixed service name constant (e.g. `KMS_DAEMON`).
4. Add a grep-guard test (or extend an existing daemon import-discipline test) asserting `secret_vault.py` contains no `load_dotenv` (C-11).
5. Commit.

**Files to modify**:
- `pyproject.toml` — add `keyring` dependency.
- `src/daemon/secret_vault.py` (NEW) — the wrapper.
- `tests/test_daemon/test_secret_vault.py` (NEW) — round-trip + env-injection + absence tests.

**Test criteria**:
- [ ] `uv run pytest tests/test_daemon/test_secret_vault.py` green with a stand-in backend.
- [ ] A key written by the module is readable in a fresh `read_key()` call.
- [ ] After `load_key_into_env()`, `os.environ["KMS_DAEMON_API_KEY"]` holds the key (this is the exact slot `load_daemon_config` reads at `config.py:125`).
- [ ] No `load_dotenv` anywhere under `daemon/` (grep-guard).

**Notes / coupling**: `# COUPLING:` the env-var key name `KMS_DAEMON_API_KEY` is shared with the engine (`config.py:125`) and the cloud server (`api.py:61`). It is a hard contract; do not parameterize it.

**Status**: [x] done
**Completed**: 2026-06-15
**Notes**: Implemented `src/daemon/secret_vault.py` with `store_key`, `read_key`, `load_key_into_env`, `delete_key` — all returning `Result`. Tests use in-memory dict via monkeypatch on keyring functions (no real OS vault touched). Grep-guard test confirms no `load_dotenv` in the module. Full daemon test suite: 261 passed (5 new + 256 baseline). No deviations from plan. keyring was already in pyproject.toml (no dependency change needed).

---

### Phase 2 — Cloud Connection Check (live authed key test)

**Goal**: Prove a key + cloud address actually work before the Wizard lets the user finish — against the logged-in endpoint, never the open health ping.

**Implements**: spec component **2** (Cloud Connection Check). Behavior `P6-SLICEB-02` (live key test gates setup). See spec §"Component dependency order → 2".

**Design**:
```
# Connection check — request shape
src/daemon/connection_check.py   (NEW)
   check_connection(endpoint, key)  async
        │  GET {endpoint}/api/state    ◄── GATED endpoint (api.py:450), NOT /health
        │  Authorization: Bearer {key}
        ▼
   200 ──────────────► Success
   401 ──────────────► Failure("authentication ...")   ◄── names "authentication"
   unreachable ──────► Failure("cannot reach ...")      ◄── names "cannot reach"
   other ────────────► Failure(status + body snippet)

   Reuse the httpx.AsyncClient SHAPE from the status command (cli.py:87-108),
   NOT its endpoint — status hits /health (ungated) and would pass a bad key.
```

**Steps** (TDD RED→GREEN):
1. RED: `tests/test_daemon/test_connection_check.py`. Inject a mock httpx client (dependency category: true-external — mock the transport, do not hit a network). Cases: 200 → `Success`; 401 → `Failure` whose message contains "authentication"; connection error → `Failure` whose message contains "cannot reach"; 500 → `Failure` with status + body snippet.
2. GREEN: implement `check_connection(endpoint, key, client)` (async, takes/opens an `httpx.AsyncClient`, returns `Result`). Target path `{endpoint}/api/state` (the gated route, spec-verified `api.py:450`/`263`). Mirror the timeout + error-branch structure of the `status` command (`cli.py:87-108`).
3. Add a guard test asserting the request path ends in `/api/state` and NOT `/health` (this is the single most important correctness rail in the slice — research §"Edge Cases": `/health` false-passes a bad key).
4. Commit.

**Files to modify**:
- `src/daemon/connection_check.py` (NEW) — the authed probe.
- `tests/test_daemon/test_connection_check.py` (NEW) — pass/auth-fail/unreachable/other + the `/api/state`-not-`/health` guard.

**Test criteria**:
- [ ] `uv run pytest tests/test_daemon/test_connection_check.py` green.
- [ ] A correct key + reachable address returns `Success`.
- [ ] A wrong key (401) returns a `Failure` naming "authentication".
- [ ] An unreachable address returns a `Failure` naming "cannot reach".
- [ ] No code path probes `/health`; the only authed call is to `/api/state`.

**Notes / coupling**: `# COUPLING:` the endpoint suffix `/api/state` is the single authed surface today (`api.py:450`). `[closed]` — a second probe would edit this file; acceptable per the design's honest-cardinality stance.

**Status**: [ ] pending

---

### Phase 3 — OS-Glue Seam + two adapters (Auto-Start Registrar + tray registration)

**Goal**: Concentrate the only two per-OS behaviours — start-at-login and tray registration — behind one shared interface with exactly two real implementations (Mac, Windows), selected at startup by a plain `platform.system()` check.

**Implements**: spec component **3** (OS-Glue Seam + adapters). Behaviors `P6-SLICEB-05` (start at login), `P6-SLICEB-06` (tray icon alive/error + Quit), `P6-SLICEB-07` (cross-OS, two adapters). See spec §"Component dependency order → 3".

**Design**:
```
# OS-Glue Seam — interface + dispatch
src/daemon/os_glue/__init__.py     get_os_adapter()  ── platform.system() ──┐
                                                                            │
   OsAdapter (Protocol):                                                    │
     register_at_login()      unregister_at_login()                         │
     show_tray(on_quit, state_provider)                                     │
                                          ┌──────────────┴──────────────┐
                                          ▼                             ▼
   src/daemon/os_glue/macos.py     LaunchAgent + pystray (MAIN thread)  │
   src/daemon/os_glue/windows.py   Task Scheduler/Run key + pystray ────┘

   Dispatch is a plain platform.system() branch — NOT a registry (Option C
   rejected). Tray runs on the MAIN thread (OQ-SB1 resolved: tray-on-main).
```

**Steps** (TDD RED→GREEN):
1. Add `pystray` to `pyproject.toml`. **New dependency — see Open Questions.** `uv sync`.
2. RED: `tests/test_daemon/test_os_glue.py`. Test the **dispatch** only (local-substitutable): monkeypatch `platform.system()` → assert `get_os_adapter()` returns the macOS adapter on "Darwin", the Windows adapter on "Windows", and raises a clear error on anything else. Assert both adapters satisfy the `OsAdapter` Protocol (have all methods). Do NOT assert real registration in unit tests.
3. GREEN: define `OsAdapter` Protocol in `src/daemon/os_glue/__init__.py` with `register_at_login`, `unregister_at_login`, `show_tray` — **Protocol is the deliverable of this step, before any adapter** (per the skill's interface-first rule). All methods return `Result` where they can fail.
4. GREEN: implement `macos.py` (LaunchAgent plist write under `~/Library/LaunchAgents/`; `pystray` macOS tray on the main thread) and `windows.py` (Task Scheduler or registry Run key; `pystray` Windows tray). Tray exposes a Quit item wired to an injected `on_quit` callback and reads alive/error from an injected `state_provider` callable (this keeps the tray free of sync logic — C-18; it only *reads* a state value).
5. **MANUAL VERIFICATION (real hardware — not unit-testable, surfaced as an explicit step per the task brief):**
   - macOS: register at login → log out/in → confirm the app relaunches → confirm the tray icon appears and Quit is wired.
   - Windows: same cycle via Task Scheduler/Run key.
   - Confirm `pystray` runs on the **main** thread on both OSes (OQ-SB1 / research external assumption — verify, do not assume).
6. Commit.

**Files to modify**:
- `pyproject.toml` — add `pystray`.
- `src/daemon/os_glue/__init__.py` (NEW) — `OsAdapter` Protocol + `get_os_adapter()` dispatch.
- `src/daemon/os_glue/macos.py` (NEW) — LaunchAgent + macOS tray adapter.
- `src/daemon/os_glue/windows.py` (NEW) — Task Scheduler/Run key + Windows tray adapter.
- `tests/test_daemon/test_os_glue.py` (NEW) — dispatch + Protocol-conformance tests.

**Test criteria**:
- [ ] `uv run pytest tests/test_daemon/test_os_glue.py` green (dispatch + Protocol conformance, stubbed).
- [ ] On each OS the correct adapter is auto-selected; unsupported OS errors clearly.
- [ ] (Manual) register-at-login survives a logout/login cycle on each OS.
- [ ] (Manual) tray icon appears, shows alive/error, Quit is wired — `pystray` on the main thread on both OSes.

**Notes / coupling**: `# COUPLING:` the `platform.system()` branch is the deliberate, honest 2-case dispatch (Option C registry rejected). A third OS = one new adapter file + one branch — flag in the file with a `# COUPLING:` comment noting the Linux-deferred decision.

**Status**: [ ] pending

---

### Phase 4 — Setup Wizard (Tkinter)

**Goal**: A one-time window that collects the notes folder, cloud address (pre-filled, editable), and key; runs the live test; and on pass saves settings + stores the key.

**Implements**: spec component **4** (Setup Wizard). Behaviors `P6-SLICEB-01` (2-minute no-terminal setup), `P6-SLICEB-02` (bad key keeps window open), `P6-SLICEB-03` (editable baked default). See spec §"Component dependency order → 4".

**Design**:
```
# Setup Wizard — save/test orchestration (the testable core)
src/daemon/wizard.py   (NEW)
   on "test & save":
     check_connection(endpoint, key)   ◄── Phase 2
       FAIL ──► show error, window stays open, NOTHING written
       PASS ──► write_daemon_config_yaml(folder, endpoint)   (shape config.py reads)
            └─► store_key(key)                                ◄── Phase 1
   endpoint field pre-filled from DEFAULT_ENDPOINT constant (editable)

   Tkinter is stdlib (no new dep). Test the orchestration with Phase 1+2 STUBBED;
   the Tk widgets themselves are not unit-tested.
```

**Steps** (TDD RED→GREEN):
1. Decide the default-endpoint constant location (DQ-2). RED first on the orchestration, not the widgets.
2. RED: `tests/test_daemon/test_wizard.py`. Test the **save/test orchestration** with stubbed Phase 1 (`store_key`) and Phase 2 (`check_connection`) — dependency category in-process. Cases: connection-check FAIL → no config written, no key stored, error surfaced; connection-check PASS → config YAML written in the exact shape `load_daemon_config` reads (folder/`vault_root` + `cloud_endpoint`), key stored via the wrapper; default endpoint constant pre-fills but an edited value flows through.
3. GREEN: implement `src/daemon/wizard.py` — a thin orchestration function (`run_wizard()` builds the Tk window) plus a separately-callable `attempt_save(folder, endpoint, key, *, check, store)` that the test drives directly. `attempt_save` calls the connection check; on `Success` writes the config YAML (via the daemon's own config-writing path — reuse `daemon/config.py` write helpers if present, else write the dict `load_daemon_config` parses) and stores the key. The config write must produce the shape the loader reads — **and the loader can still raise at runtime on a half-written file (research A6); the Wizard's job is to always write a complete file so it never does.**
4. Manual smoke: launch `run_wizard()` under `uv run`, complete a fake setup against a local test server, confirm the folder picker + editable endpoint + retry-on-bad-key UX.
5. Commit.

**Files to modify**:
- `src/daemon/wizard.py` (NEW) — Tkinter window + `attempt_save` orchestration.
- `src/daemon/defaults.py` (NEW, or a constant in `wizard.py` per DQ-2) — `DEFAULT_ENDPOINT` baked constant.
- `tests/test_daemon/test_wizard.py` (NEW) — orchestration tests with stubbed check + store.

**Test criteria**:
- [ ] `uv run pytest tests/test_daemon/test_wizard.py` green.
- [ ] A failing connection check writes NO config and stores NO key, and surfaces the error.
- [ ] A passing check writes a config in the shape `load_daemon_config` reads AND stores the key in the vault.
- [ ] The endpoint field pre-fills the baked default but accepts an edit (the edited value is what gets written).

**Notes / coupling**: `# COUPLING:` the config YAML shape is dictated by `DaemonConfig`/`load_daemon_config` (`config.py:88`). Write the exact keys the loader expects — do not invent a new schema.

**Status**: [ ] pending

---

### Phase 5 — App Supervisor (setup-vs-run brain + threaded engine + clean stop)

**Goal**: The single entry point that decides setup-vs-run, runs the Sync Engine on a background thread via the existing start path, shows the tray, and shuts down cleanly on Quit.

**Implements**: spec component **5** (App Supervisor). Behaviors `P6-SLICEB-08` (skip wizard when set up), `P6-SLICEB-09` (zero sync/cache logic added), `P6-SLICEB-10` (clean Quit stops engine). See spec §"Component dependency order → 5".

> **Blocked on Slice A2** (see preamble). Verify the `start`/`_run` shape (`cli.py:148-251`) and `watcher.stop()/join()` (`watcher.py:259-270`) are unchanged by A2 before starting.

**Design**:
```
# App Supervisor — setup-vs-run + threaded engine + stop
src/daemon/app.py   (NEW)
   run_app():
     setup_ok = config_file_present() AND read_key() is Success   (presence-only, A6)
        NO  ──► run_wizard()  ─► on success: get_os_adapter().register_at_login()  (once)
        YES ──► skip wizard
     load_key_into_env()                       ◄── Phase 1 (sets KMS_DAEMON_API_KEY)
     try: validate config loads                ◄── catch ValueError/ValidationError/YAMLError
          (half-written config ─► tray error state, NOT a crash — research A6)
     start engine on a WORKER thread:
          asyncio.run(_run_with_stop(stop_event))   ◄── existing start path (C-10)
     get_os_adapter().show_tray(on_quit, state_provider)   ◄── MAIN thread (Phase 3)
     on Quit: set thread-safe stop_event ─► breaks `while True` ─► watcher.stop()/join()

   Stop signal replaces Ctrl+C (cli.py:249). NO signal handler (worker thread
   can't add one). NO new bare event loop (C-10).
```

**Steps** (TDD RED→GREEN):
1. **Resolve the "unmodified core" ruling first** (research Tech-Debt / Extension-Points): the only exit path today is `KeyboardInterrupt` (`cli.py:249`). Add a thread-safe stop **without rewriting the engine** — preferred: factor the watch-loop body so it accepts an optional `stop_event: asyncio.Event` and the tray sets it via `loop.call_soon_threadsafe(stop_event.set)`. This is a small additive change to (or thin wrapper around) `_run`, not a rewrite. **See Open Questions — confirm this counts as "core unmodified" before editing `cli.py`.**
2. RED: `tests/test_daemon/test_app.py`. Stub Phase 1 (vault), Phase 3 (adapter + tray), and the engine runner (dependency category: in-process). Cases: fresh machine (no config / no key) → wizard invoked → register-at-login called once on success; already-set-up machine (config present + key in vault) → wizard NOT invoked, engine started; Quit → stop_event set → engine runner's stop path invoked → process-exit path reached; **half-written config** (loader raises) → tray-error state, no crash.
3. GREEN: implement `src/daemon/app.py` — `run_app()` doing the branch, the env injection (`load_key_into_env`), the worker-thread `asyncio.run`, the tray-on-main, and the stop wiring. Catch `ValueError`/`pydantic.ValidationError`/`yaml.YAMLError` from `load_daemon_config` and route to the tray-error state (research A6).
4. GREEN: the minimal `_run` change from step 1 (stop-event param/wrapper) in `daemon/cli.py`, with its own RED test asserting the loop exits when the event is set (no `KeyboardInterrupt` needed).
5. **MANUAL VERIFICATION (real hardware):** fresh machine → wizard → runs; already-set-up machine → runs with no window; Quit → engine stops cleanly, process exits; confirm the key reaches the engine via the env var with no `config.py` edit; confirm `pystray`-on-main + `asyncio.run`-on-worker coexist on both OSes (OQ-SB1).
6. Commit.

**Files to modify**:
- `src/daemon/app.py` (NEW) — the Supervisor.
- `src/daemon/cli.py` — additive stop-event seam in `_run` (the only edit to existing daemon code; gated on the step-1 ruling).
- `tests/test_daemon/test_app.py` (NEW) — branch logic + stop-signal + half-written-config tests.
- `tests/test_daemon/test_cli.py` (existing, if present) — add the stop-event-exits-loop test.

**Test criteria**:
- [ ] `uv run pytest tests/test_daemon/test_app.py` green.
- [ ] Fresh machine path invokes the wizard and registers at login once on success.
- [ ] Already-set-up path skips the wizard and starts the engine.
- [ ] Quit sets the stop event and the watch loop exits via `watcher.stop()/join()` — no `KeyboardInterrupt`.
- [ ] A half-written config routes to a tray-error state, not an uncaught crash.
- [ ] (Manual) the engine runs through the existing `asyncio.run` path on a worker thread; tray on main; key via env var; no `config.py` edit.

**Notes / coupling**: `# COUPLING:` the env-injection-before-loader ordering is load-bearing — `load_daemon_config` raises `ValueError` if `KMS_DAEMON_API_KEY` is absent (`config.py:126-130`). `load_key_into_env()` MUST run before any `load_daemon_config()` call. C-18: the tray reads engine alive/error via an injected `state_provider`; it must NOT call into the sync path.

**Status**: [ ] pending

---

### Phase 6 — Uninstall Cleanup (`daemon uninstall` CLI subcommand)

**Goal**: Remove the one thing a native uninstaller can't safely remove — the key in the Secret Vault — plus the config file and the auto-start registration.

**Implements**: spec component **6** (Uninstall Cleanup). Resolves OQ-SB3 (CLI subcommand, not internal helper). See spec §"Component dependency order → 6".

**Design**:
```
# daemon uninstall — fourth command on the existing group
src/daemon/cli.py     @cli.command()  def uninstall(): ...   ◄── self-registers (A: registry)
   wraps run_uninstall()  ─► Result
        delete_key()                 ◄── Phase 1 (vault)
        remove config file           (temp-path-safe in tests)
        unregister_at_login()        ◄── Phase 3 (seam)
   idempotent: running twice is safe (missing key/file/registration = OK)
```

**Steps** (TDD RED→GREEN):
1. RED: `tests/test_daemon/test_uninstall.py`. Stub Phase 1 (`delete_key`) + Phase 3 (`unregister_at_login`) and use a temp config path (local-substitutable). Cases: on a set-up machine, removes key + config + registration and returns a `Result` describing what it removed; running twice is safe (second run reports already-clean, no error).
2. GREEN: implement `run_uninstall()` (Result-returning) and add a fourth `@cli.command()` named `uninstall` to the existing Click group (`cli.py:70`) — zero edits to `start`/`scan`/`status` (extension-point: registry-by-decoration).
3. Commit.

**Files to modify**:
- `src/daemon/cli.py` — add `run_uninstall()` + the `@cli.command()` `uninstall` wrapper.
- `tests/test_daemon/test_uninstall.py` (NEW) — removes-everything + idempotency tests.

**Test criteria**:
- [ ] `uv run pytest tests/test_daemon/test_uninstall.py` green.
- [ ] After running on a set-up machine: no key in the vault, no config file, no auto-start registration.
- [ ] Running it twice is safe (idempotent) and returns a `Result` describing what was removed.

**Notes / coupling**: none beyond reuse of Phases 1 and 3.

**Status**: [ ] pending

---

### Phase 7 — Packager (PyInstaller + DMG/Inno + baked default endpoint)

**Goal**: Produce one generic installable file per OS (Mac DMG, Windows installer) that bundles the engine + Supervisor + Wizard + tray and bakes in the default cloud address — with all eight extractor libs frozen so extraction never silently degrades to raw bytes.

**Implements**: spec component **7** (Packager). See spec §"Component dependency order → 7". ADR-0016 (native app, not Docker) — settled, not re-litigated.

> **Blocked on Slice A2** (see preamble) and on Phases 1–6 (everything it bundles). Verified only by building + launching on real hardware — no unit tests.

**Design**:
```
# Packager — what the frozen graph MUST include
PyInstaller spec(s) + DMG (Mac) + Inno Setup (Windows)
   entry point:  the App Supervisor (app.py), launched via the daemon entry
   hidden imports / hooks — REQUIRED (frozen build misses dynamic loads):
     pystray backends            pystray._darwin / pystray._win32
     keyring backends            keyring.backends.macOS / keyring.backends.Windows
     watchdog backends           watchdog.observers.fsevents / ...read_directory_changes
     EIGHT extractor libs (research A5 refinement — six is WRONG):
       pypdf            (PDF — note: NOT pdfplumber)
       python-docx      python-pptx     openpyxl
       extract-msg      beautifulsoup4  requests
       youtube-transcript-api
   baked default endpoint = compiled constant read at build time (Phase 4 DEFAULT_ENDPOINT)
   Windows uninstaller calls `daemon uninstall`  ◄── Phase 6
   NO cross-compile, NO signing (ADR-0016)
```

**Steps**:
1. Confirm the PyInstaller entry point (DQ-1 / research suggested-research #6): `python -m daemon` vs. a new packaged entry; the entry must boot the **Supervisor** (`app.py`), not the bare `kms` CLI. Decide whether a `[project.scripts]` daemon entry is needed.
2. Add `pyinstaller` as a dev/build dependency. **New dependency — see Open Questions.**
3. Write the PyInstaller spec file(s) with the hidden-import list above. **Critical: include all EIGHT extractor libs** — `pypdf`, `python-docx`, `python-pptx`, `openpyxl`, `extract-msg`, `beautifulsoup4`, `requests`, `youtube-transcript-api`. The daemon's `extract()` lazily loads `handlers.registry` (`extractor.py:144`), which runs `handlers/__init__.py` and imports ALL handlers — so `python-pptx` (`pptx_handler.py:54`) and `extract-msg` (`msg_handler.py:54`) enter the frozen graph even though the spec's prose named only six. Missing either → `.pptx`/`.msg` silently upload as raw bytes (research §"Edge Cases").
4. Bake the default endpoint constant into the build (Phase 4 `DEFAULT_ENDPOINT`).
5. Wrap the Mac build into a DMG; wrap the Windows build into an Inno Setup installer whose uninstaller invokes `daemon uninstall` (Phase 6).
6. **MANUAL VERIFICATION ON REAL HARDWARE (the only verification — surfaced as explicit steps per the task brief; these are field-verify items, not blockers):**
   - **Extractor freeze (A5):** in the frozen app, drop a `.pdf`, `.docx`, `.xlsx`, `.pptx`, AND `.msg` into the watched folder → confirm each uploads as extracted text, NOT raw bytes.
   - **Keyring freeze (A8):** confirm the macOS Keychain / Windows Credential Manager backend loads in the frozen app (not just under `uv run`) — store + read a key from inside the bundle.
   - **watchdog + pystray freeze:** confirm the FSEvents/`ReadDirectoryChangesW` backend and the tray render in the frozen build.
   - **macOS Gatekeeper quarantine (decision #12):** confirm that after the first manual "Open Anyway", a LaunchAgent-launched relaunch of the SAME bundle does NOT re-prompt (re-quarantines only on a replaced/re-downloaded bundle).
   - DMG installs + runs on a clean Mac; Windows installer installs + runs on a clean Windows box; endpoint field shows the baked default; Windows uninstaller leaves no stranded secret.
7. Commit the spec/installer scripts (not the built binaries).

**Files to modify**:
- `daemon.spec` (or `packaging/daemon-mac.spec` + `packaging/daemon-win.spec`) (NEW) — PyInstaller spec(s) with the full hidden-import list.
- `packaging/installer.iss` (NEW) — Inno Setup script invoking `daemon uninstall`.
- `packaging/dmg/` (NEW) — DMG build config/script.
- `pyproject.toml` — add `pyinstaller` (build dep).
- `pyproject.toml` `[project.scripts]` — daemon entry, if DQ-1 resolves that way.

**Test criteria** (manual, per OS — no pytest):
- [ ] A single DMG installs and runs on a clean Mac; a single Windows installer installs and runs on a clean Windows box.
- [ ] The frozen app extracts PDF/DOCX/XLSX **and PPTX/MSG** — no raw-bytes fallback (all eight libs frozen).
- [ ] The keyring backend loads in the frozen app on each OS.
- [ ] watchdog + pystray backends survive the frozen build.
- [ ] (macOS) LaunchAgent relaunch of the approved bundle does not re-trigger Gatekeeper.
- [ ] The endpoint field shows the baked default; the Windows uninstaller invokes `daemon uninstall` and leaves no stranded secret.

**Notes / coupling**: `# COUPLING:` the eight-lib hidden-import list is coupled to the handler set in `handlers/__init__.py`. If a new file-handler/extractor lib is added later, this list must grow — add a `# COUPLING:` comment in the spec file pointing at `handlers/__init__.py` so the link isn't lost.

**Status**: [ ] pending

---

## Open Questions

- **New dependencies require sign-off (global contract §4).** This plan adds three packages not yet in `pyproject.toml` (research confirms none are present): `keyring` (Phase 1), `pystray` (Phase 3), `pyinstaller` (Phase 7, build-time). List them and wait for explicit approval before `uv sync`. All three are spec-mandated (decisions #3, #4, #10; ADR-0016), so this is a confirm-not-decide gate.
- **"Unmodified core" ruling for the stop signal (Phase 5, step 1).** The spec promises the daemon core is unmodified, but the only exit today is `KeyboardInterrupt` (`cli.py:249`). The plan proposes a small additive stop-event seam in `_run` (not a rewrite). Confirm this additive change counts as "core unmodified," or instruct a wrapper-only approach that does not touch `cli.py`. (Research Tech-Debt: "the one place the unmodified promise needs an explicit ruling.")
- **Module layout (DQ-1).** Plan assumes new modules live under `src/daemon/` (`app.py`, `wizard.py`, `secret_vault.py`, `connection_check.py`, `os_glue/`, `defaults.py`) — the natural home since `daemon/` already owns config, CLI, and the sync core. Confirm.
- **Default-endpoint constant location (DQ-2).** Plan assumes `src/daemon/defaults.py` (`DEFAULT_ENDPOINT`), baked at build time. Confirm vs. an inline `wizard.py` constant.
- **Tray "error state" content (DQ-3).** The tray reads engine alive/error via an injected `state_provider`, but the exact states (alive / syncing / error) and how the tray learns of an upload failure from the engine thread are under-specified. This is the one place Slice B reads engine health — flag for a brief design refinement before Phase 3's tray is fully wired, ensuring it stays read-only (C-18).
- **PyInstaller entry point (DQ-1 / Phase 7 step 1).** `python -m daemon` vs. a new `[project.scripts]` daemon entry — the entry must boot the Supervisor (`app.py`), not the `kms` CLI. Confirm during Phase 7.

## Out of Scope

_All carried verbatim from the spec's "Out of scope" — not re-derived. See spec §"Out of scope" for rationale._
- Changing how the daemon syncs (watch/extract/upload/reconcile/cache) — zero sync/cache logic added (C-18, `P6-SLICEB-09`).
- Code signing / notarization — the app ships unsigned this slice (ADR-0016).
- Linux desktop — Mac + Windows only; a third OS = one adapter + one branch later.
- Multi-process supervisor (crash-respawn) — Option B, rejected for the tester phase.
- Self-registering OS-glue registry — Option C, rejected (1-pattern-for-2-cases).
- Changing the default endpoint without a rebuild — it is a compiled constant.
- Auto-update / in-app upgrade.
- Re-running the live connection test on every launch — presence-only entry; failures surface via the tray (OQ-SB2).
