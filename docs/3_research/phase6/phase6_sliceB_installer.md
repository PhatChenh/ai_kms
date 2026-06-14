# Research: Phase 6 Slice B — Installable Daemon App
_Last updated: 2026-06-14_
_Spec verified: `docs/2_specs/phase6/phase6_sliceB_installer.md`_
_Method: every code-side claim was opened and read at the depth the claim required; A4 was additionally confirmed by actually importing the daemon and inspecting loaded modules. Packaging/platform claims that cannot be run in this environment are marked **external assumption** with cited reasoning._

## Overview

In plain English: this phase wraps the **existing, unchanged** sync daemon in a double-click desktop app for Mac and Windows. The spec makes a set of promises about what the current code already does — that an authenticated cloud endpoint exists for the setup wizard to test against, that the daemon reads its secret from an environment variable (so the app can inject it from the OS keychain without editing config code), that the daemon's start loop can run on a background thread, and that the daemon does NOT drag in the heavy AI/embedding libraries (keeping the installer small).

This research checked each of those promises against the real code. **All six code-side assumptions hold.** The endpoint exists and is authenticated; the daemon reads its key only from the environment; the start path has no main-thread or signal-handler entanglements that would block a worker thread; and the daemon's import graph — verified empirically — pulls in **none** of `sentence_transformers`, `torch`, or `retrieval`. Two findings refine the spec rather than break it: (1) the text-extractor library list in the spec is **incomplete** — the daemon transitively loads MORE extractor libraries than the spec names (and the task brief's mention of "pdfplumber" is wrong — the code uses `pypdf`), which matters for PyInstaller hidden-imports; (2) the behavior-inventory IDs the spec said were missing **now exist**. Neither requires a redesign.

The packaging risks (pystray's main-thread requirement, freezing keyring/watchdog/pystray, macOS Gatekeeper quarantine) are genuinely external — they depend on library and OS behavior that cannot be exercised in this repo. They are documented as external assumptions with the best available evidence, and they remain the real open questions for this phase.

---

## Key Components

Plain English: these are the existing code pieces the spec says it will reuse, plus where the spec's new modules will plug in.

| Component | Location | Role |
|---|---|---|
| `DaemonConfig` | `src/daemon/config.py:26` | Pydantic settings; `extra:"forbid"` (line 33); `api_key` is `Field(exclude=True, repr=False)` (line 40) — never serialized. |
| `load_daemon_config` | `src/daemon/config.py:88` | Reads `~/.kms-daemon/config.yaml`, pulls `api_key` ONLY from `os.environ["KMS_DAEMON_API_KEY"]` (line 125), validates. |
| `start` command + `_run` | `src/daemon/cli.py:146-251` | `asyncio.run(_run())` (line 248); inside: startup scan → watcher with 4 callbacks → `while True: await asyncio.sleep(1)` → `finally: watcher.stop()/join()`. |
| `DaemonWatcher` | `src/daemon/watcher.py:220` | watchdog `Observer` (line 248), `start()`/`stop()`/`join()` (lines 255-270); `stop()` cancels debounce timers then stops the observer. |
| Click group `cli` | `src/daemon/cli.py:70` | `@click.group()`; three `@cli.command()` subcommands (`status`, `scan`, `start`). |
| `python -m daemon` entry | `src/daemon/__main__.py` | `from daemon.cli import cli` → `cli()`. |
| `extract()` | `src/daemon/extractor.py:79` | Hash + text-extract; **lazily imports `handlers.registry`** at line 144 and calls `HandlerRegistry.resolve(path)`. This is the bridge from `daemon/` into `handlers/`. |
| `GET /api/state` | `src/mcp_server/api.py:243`, route at `api.py:450` | Gated by `require_key` (line 263-265): 401 on bad/missing key. Lists `{vault_path, content_hash}`. |
| `GET /health` | `src/mcp_server/api.py:74`, route at `api.py:455` | In a separate `health_route` list — NOT gated; returns `{"status":"ok"}` for any caller. |
| `require_key` | `src/mcp_server/api.py:46` | Reads expected key from `os.environ["KMS_DAEMON_API_KEY"]`; returns `None` on missing header, non-Bearer prefix, missing env key, or mismatch. |

---

## How It Works

Plain English: when a tester eventually launches the packaged app, the new Supervisor will decide setup-vs-run, then hand the existing daemon's start loop to a background thread. This research only verifies the **existing** pieces that handoff depends on — it does not build them.

The relevant existing flow (the `start` command) is:

1. `cli.py:158` loads `DaemonConfig` (which requires `KMS_DAEMON_API_KEY` to already be in the environment — `config.py:125-130` raises `ValueError` if it is missing).
2. `cli.py:248` calls `asyncio.run(_run())`.
3. Inside `_run`: opens an `httpx.AsyncClient`, runs a startup `scan`, captures `loop = asyncio.get_running_loop()` (line 178), defines three watcher callbacks that marshal work back onto the loop via `asyncio.run_coroutine_threadsafe` (lines 202, 213, 224), starts `DaemonWatcher` (line 234), then idles on `while True: await asyncio.sleep(1)` (line 241).
4. On `KeyboardInterrupt` the `while` loop is interrupted, the `finally` block runs `watcher.stop()` + `watcher.join()` (lines 243-244), and `asyncio.run` returns.

For the Supervisor (new), the seam the spec relies on is: set `os.environ["KMS_DAEMON_API_KEY"]` from the OS keychain BEFORE calling the loader, then run `asyncio.run(_run())` on a worker thread, and replace the `Ctrl+C`/`KeyboardInterrupt` exit with a thread-safe stop signal that breaks the `while True` loop. Nothing in the read path installs an OS signal handler or asserts the main thread (verified — see A3), so this is mechanically possible.

---

## Spec Verification

Plain English: every code-side assumption the spec made was checked against the actual files. All held. The external packaging assumptions could not be run here and are marked unverifiable-in-repo with cited reasoning.

| ID | Spec Claim | Verdict | Evidence |
|----|-----------|---------|----------|
| A1 | `GET /api/state` exists, is authed, returns 401 on bad/missing key; `/health` is NOT gated. | ✅ Validated (code) | `api.py:450` route; `state_handler` calls `require_key` → 401 (`api.py:263-265`). `/health` is in a separate `health_route` list, no gate (`api.py:74,455`). `require_key` returns None (→401) on missing header, non-Bearer prefix, missing env key, or token mismatch (`api.py:54-66`). |
| A2 | `load_daemon_config` reads the key ONLY from `KMS_DAEMON_API_KEY`; setting that env var injects the key with no `config.py` edit. | ✅ Validated (code) | `config.py:125` `os.environ.get("KMS_DAEMON_API_KEY")`; YAML never supplies `api_key` (assigned at line 131 from env only); `api_key` is `exclude=True` (`config.py:40`). No caching — loader re-reads env on every call. |
| A3 | The `start` loop can run on a background thread, stopped by a thread-safe signal instead of Ctrl+C. | ✅ Validated (code) | `cli.py:248` `asyncio.run(_run())`; no `signal.`/`add_signal_handler`/`SIGINT` anywhere in `daemon/` (grep: none); no `main_thread`/`current_thread` assertions in `daemon/` (grep: none). `watcher.stop()/join()` (`watcher.py:259-270`) is a clean, thread-agnostic shutdown. `asyncio.run` on a non-main thread is allowed in CPython; the only main-thread-only feature (`loop.add_signal_handler`) is NOT used. |
| A4 | The daemon imports ZERO AI-model packages (`sentence_transformers`/`torch`/`retrieval.*`). | ✅ Validated (code + empirical) | Static: only `retrieval/embeddings.py` and `retrieval/reranker.py` import the heavy stack; importers of `retrieval` are `cli/main.py`, `pipelines/capture.py`, `mcp_server/context.py`, `retrieval/*` — none in the daemon graph. Empirical: `import daemon.cli` + full handler registration (11 handlers) left `sentence_transformers`/`torch`/`retrieval`/`sklearn`/`transformers` ALL absent from `sys.modules`. (`numpy` IS pulled in — minor bundle note, not the embedding stack.) |
| A5 | Extractors are `pypdf`, `python-docx`, `openpyxl`, `bs4`, `requests`, `youtube_transcript_api` — real packages PyInstaller can collect. | ⚠️ Validated-but-INCOMPLETE (code) | All six named libs are real and imported (`pdf_handler.py:12` `pypdf`; `docx_handler.py:17` `docx`; `xlsx_handler.py:17` `openpyxl`; `url_fetcher.py:43-46` `requests`/`bs4`/`youtube_transcript_api`). BUT the daemon's `extract()` imports `handlers.registry`, which runs `handlers/__init__.py`, which imports ALL handlers — adding `python-pptx` (`pptx_handler.py:54`) and `extract-msg` (`msg_handler.py:54`) to the runtime graph. The PyInstaller hidden-import list must include these two extra libs. See "Invalidated/Refined Assumptions". |
| A6 | "Valid enough to skip the Wizard" = config file present AND a key in the Secret Vault (presence-only). | ✅ Validated (code, as a design choice) | `config.py:104-122`: a present-but-partial YAML is read as a dict; missing required `vault_root`/`cloud_endpoint` raise `pydantic.ValidationError` only at construction (`config.py:133`). So "config file present" is NOT the same as "config valid" — a half-written YAML passes a presence check but fails the loader. The presence-only entry decision is internally consistent with OQ-SB2's "surface failures via tray," but the Supervisor must treat a loader `ValidationError` at run-time as a tray-error state, not a crash. |
| A7 | Exactly two per-OS behaviours differ (start-at-login + tray registration). | ⚠️ Unverifiable-in-repo | No OS-glue code exists yet (new modules). This is a forward-looking design claim about the new seam, not a claim about existing code. Plausible from the daemon being OS-neutral (watchdog/httpx/pydantic only), but the third potential difference (Gatekeeper/quarantine handling on macOS) is a real external risk — see Open Questions. |
| A8 | The `keyring` backend loads correctly inside a frozen PyInstaller bundle. | ⚠️ Unverifiable-in-repo (external) | `keyring` is not yet a dependency (`pyproject.toml:7-38` has no `keyring`/`pystray`/`pyinstaller`). Cannot be run here. External evidence below. |

---

## Edge Cases & Silent Failure Modes

Plain English: things that can go wrong quietly when this is built.

- **Half-written config passes the presence check but fails the loader (A6).** `load_daemon_config` only raises `ValidationError` at `DaemonConfig(**data)` construction (`config.py:133`). A Supervisor that checks "file exists + key in vault" then calls the loader can still get a `ValidationError` at runtime. This must route to a tray-error state, not an uncaught crash. The loader raises `ValueError`/`pydantic.ValidationError`/`yaml.YAMLError` (documented `config.py:99-102`) — the Supervisor must catch all three.
- **Missing env key raises, it does not return a Result (A2).** `load_daemon_config` raises `ValueError` (`config.py:126-130`) when `KMS_DAEMON_API_KEY` is absent. The Supervisor's "read key from vault → set env → load" sequence must guarantee the env var is set first, or this raises. This is the exact seam the spec depends on — it works, but it is exception-based, not Result-based.
- **The wizard MUST NOT use `/health` (A1).** The existing `status` command (`cli.py:85`) hits `/health` with a Bearer header — but `/health` is ungated, so a wrong key still returns 200. The wizard's live test must hit `/api/state` (gated) to actually prove the key. The spec is correct to call this out; the existing `status` command is the WRONG template for the connection test (it would false-pass a bad key). Reuse `status`'s `httpx.AsyncClient` *shape*, not its endpoint.
- **Extractor fallback is silent by design (A5).** If a frozen build is missing `python-pptx` or `extract-msg`, `handler.extract()` fails and `extract()` silently falls through to `BinaryContent` (`extractor.py:159-176`) — the file uploads as raw bytes instead of extracted text. No error surfaces. This is the "silent raw-bytes fallback" the spec's A5 worried about, and it now applies to `.pptx`/`.msg` too, not just the six named libs.
- **`numpy` is in the bundle.** Empirically present after daemon import (transitively via `openpyxl`/`pandas`-style deps or pydantic). Not the embedding stack, but PyInstaller will bundle it — a size note, not a correctness issue.

---

## Dependencies & Coupling

Plain English: what the daemon touches and what touches it.

- **`daemon/` → `handlers/` is a real runtime edge.** `daemon/extractor.py:144` lazily imports `handlers.registry`. Because Python runs a package's `__init__.py` when any submodule is imported, this transitively imports `handlers/__init__.py` → all 11 handlers → their libs (`pypdf`, `docx`, `openpyxl`, `pptx`, `extract_msg`, `requests`, `bs4`, `youtube_transcript_api`) → `vault.reader` → `vault.frontmatter`. PyInstaller must follow this whole chain.
- **`daemon/` does NOT touch `core.config`.** Confirmed: zero imports of `core.config`/`CONFIG`/`load_config` in `daemon/` (grep). C-19 isolation holds — the daemon starts without triggering cloud config validation. This is what keeps A4 true (the embedding stack lives downstream of `core.config`/`cli.main`/`retrieval`).
- **`api_key` env-var coupling.** Both the daemon (`config.py:125`) and the cloud server (`api.py:61`) read the same `KMS_DAEMON_API_KEY`. The Supervisor injects it on the client side; the cloud reads its own copy server-side. Consistent.
- **Declared deps already cover the extractor libs** (`pyproject.toml:21-31`: `pypdf`, `python-docx`, `openpyxl`, `python-pptx`, `extract-msg`, `beautifulsoup4`, `youtube-transcript-api`, `requests`, `watchdog`, `httpx`, `click`, `pydantic`, `pyyaml`). Missing for this phase (expected new deps the spec calls for): `keyring`, `pystray`, `pyinstaller`. No `pillow` is needed — the image handlers are stubs that return `Failure` (`image_handler.py:21-26,34-39`).

---

## Extension Points

Plain English: where the new code plugs in cleanly, and where it does not.

- **Click group is trivially extensible (A: `daemon uninstall`).** `cli.py:70` `@click.group()` with sibling `@cli.command()` decorators. A fourth `@cli.command()` adds the uninstall subcommand with zero edits to existing commands. ✅
- **Config injection seam is clean.** The Supervisor sets `os.environ["KMS_DAEMON_API_KEY"]` before `load_daemon_config()` — no `config.py` edit, satisfying C-11 (no `load_dotenv` in `daemon/`) and the "config loader untouched" constraint. ✅
- **Start loop is wrappable but exit is Ctrl+C-shaped.** The `while True: await asyncio.sleep(1)` + `finally` pattern (`cli.py:240-245`) is clean to wrap, but the only exit today is `KeyboardInterrupt` (`cli.py:249`). The Supervisor needs a thread-safe stop — e.g. an `asyncio.Event` set from the tray thread via `loop.call_soon_threadsafe`, breaking the `while`. This is a small modification to (or wrapper around) `_run`, NOT a rewrite. Note the spec says the daemon core is "unmodified" — strictly, the stop mechanism either wraps `_run` or adds an optional stop-event param; flag for planning whether that counts as "modifying the core."
- **`extract()`'s handler import is the only daemon→handlers coupling** — it is already lazy (inside the function), so it does not bloat import time of the CLI itself, but PyInstaller's static analysis must still be told about the handler libs via hidden imports.

---

## Open Questions

Plain English: the genuine unknowns that code alone cannot answer — all are external library/platform behavior.

- **OQ-SB1 — pystray main-thread requirement (external assumption).** On macOS, GUI event loops (AppKit/`NSApplication`, which `pystray`'s macOS backend uses) conventionally require the **main** thread; `pystray`'s own docs state the icon's `run()` blocks and recommend running other work on a separate thread. This means the spec's orientation — **tray-on-main, sync-on-worker** — is the correct one for macOS. The spec did NOT get this backwards; A3 confirms the sync loop has no main-thread dependency, so putting it on a worker is sound. On Windows `pystray` uses a Win32 message loop that also conventionally owns its thread; tray-on-main still works. *Evidence: pystray documentation (Icon.run blocks; "run_detached" exists for non-main scenarios) + AppKit main-thread convention. Cannot be exercised in this repo — external.*
- **OQ — keyring in a frozen app (A8, external assumption).** `keyring` uses **runtime backend discovery** via entry points, which PyInstaller's static analysis commonly misses, causing "no backend available" in a frozen build. The standard mitigation is explicit hidden imports: `keyring.backends.macOS` (macOS Keychain) and `keyring.backends.Windows` (Credential Manager), plus collecting `keyring`'s metadata. *Evidence: well-documented PyInstaller↔keyring interaction; keyring backend-discovery design. External — not runnable here.*
- **OQ — watchdog + pystray freezing (external assumption).** watchdog's macOS FSEvents backend and Windows `ReadDirectoryChangesW` backend, and pystray's per-OS backends, are loaded dynamically; PyInstaller may need hidden imports (`watchdog.observers.fsevents` / `...read_directory_changes`, pystray's `_darwin`/`_win32`). The daemon already runs watchdog correctly under `uv run`; the risk is purely the frozen build. *External.*
- **OQ — macOS Gatekeeper quarantine on LaunchAgent relaunch (decision #12, external assumption).** For an **unsigned** app, the first launch triggers quarantine ("Open Anyway"). After the user approves, macOS clears the `com.apple.quarantine` attribute on that specific bundle, so a subsequent LaunchAgent (non-interactive) launch of the *same* binary generally does NOT re-prompt. Caveat: this holds only if the bundle is not replaced/re-downloaded (an update re-quarantines), and behavior has tightened across recent macOS releases. *Evidence: documented Gatekeeper quarantine-attribute behavior. Must be confirmed on a real target macOS version — external.*
- **OQ-SB2 / A6 — "valid enough to skip wizard."** Code-side this is resolved (presence-only is implementable), but the product decision (presence-only vs. re-test) is a design call, not a code fact. The loader's runtime `ValidationError` path (above) must be handled.

---

## Technical Debt Spotted

- **Spec's extractor library list is incomplete (A5).** The spec/task brief names six libs and mentions "pdfplumber"; the code uses `pypdf` (not pdfplumber) and ALSO loads `python-pptx` + `extract-msg` transitively. The PyInstaller spec must include hidden imports for all eight extractor libs, or `.pptx`/`.msg` files silently degrade to raw-bytes upload. (Refinement, not a blocker.)
- **Stale spec Handoff note: P6-SLICEB IDs now exist.** The spec's Handoff (line 405) says a grep found no `P6-SLICEB-*` entries. As of this research, `docs/system_behavior/behavior_inventory.yaml` contains `P6-SLICEB-01 … 10` (10 entries). DQ-4 is effectively resolved — the IDs are authored. The planner should stop treating their absence as a blocker.
- **Stop-signal vs. "unmodified core."** The spec promises the daemon core is unmodified, but the only exit path today is `KeyboardInterrupt` (`cli.py:249`). Adding a thread-safe stop (event param or wrapper) is the one place the "unmodified" promise needs an explicit ruling at planning time.

---

## Invalidated Assumptions

**None.** No assumption was outright invalidated. A5 was **refined** (the named library list is incomplete — the daemon loads two additional extractor libs, and the brief's "pdfplumber" is actually `pypdf`), and A6 was confirmed with a caveat (presence ≠ valid; the loader can still raise at runtime). Neither requires a redesign — both are absorbed by adding hidden imports (A5) and a try/except → tray-error path (A6). A7 and A8 are forward-looking/external and could not be verified against existing code.

Because no code-side assumption requires a redesign (no type-c escalation), **no Q4 conflict diagram is drawn** — per the research skill, text is sufficient for the mechanical/refinement-level findings above.
