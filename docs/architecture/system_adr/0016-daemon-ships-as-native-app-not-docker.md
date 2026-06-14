# The daemon ships as a native packaged app (PyInstaller), not a Docker container

_Created: 2026-06-14_

Surfaced during the Phase 6 Slice B (installable daemon app) grill. The cloud side of AI-kms is a Docker container on AgentBase, so the obvious question is "why not ship the daemon as Docker too?" **Decision: the daemon ships as a native packaged desktop app (PyInstaller `.app` on macOS, `.exe` installer on Windows), NOT a Docker container.** Docker Desktop on Mac/Windows cannot deliver the daemon's core function — live filesystem watching — because host filesystem events do not cross the Linux-VM boundary, and a containerized daemon also breaks the host-path contract that retrieval Tier 3 depends on.

**Status:** accepted (direction). Implementation is Phase 6 Slice B (the last slice of Phase 6).

## Context

- **The daemon's core job is live FS watching.** It uses `watchdog` (FSEvents on macOS, ReadDirectoryChangesW on Windows, inotify on Linux) to detect file create/modify/move/delete the instant they happen — the "drop a PDF → in cloud within 10s" acceptance criterion.
- **Docker Desktop on Mac/Windows runs a Linux VM.** A bind-mounted host folder (`-v /Users/you/Vault:/vault`) makes the files *readable* in the container without copying — that part works. But **host filesystem events do not propagate into the container across the VM boundary.** inotify/FSEvents on a bind-mounted host directory under Docker Desktop is silently broken: the watcher never fires. The only fallback is polling (re-stat the whole tree on a timer) — slow, CPU-heavy on a large vault, and it degrades the 10s latency target to minutes.
- **Tier-3 retrieval needs host-native paths.** The three-tier model (rearch §8) has `kms_inspect` Tier 3 hand the user's local Claude Desktop a *vault path* to open the real file. A containerized daemon knows `/vault/report.pdf` (the container path), not the host path Claude Desktop must open. Path translation is possible but adds a fragile seam to a core contract.
- **The target user is a non-technical manager on a Mac** (Windows added in parallel per the Slice B grill). They will not run `docker run` with bind-mount flags. The deliverable must be a double-click install.
- **The two workloads are opposites.** The cloud side is event-free, stateless, server-shaped — Docker is right there. The daemon is local, event-driven, filesystem-native, single-user — Docker is the wrong shape for it.

## Decision

Ship the daemon as a **native packaged desktop app**:

- **macOS:** PyInstaller `.app`, delivered as a DMG.
- **Windows:** PyInstaller `.exe`, delivered via an installer (Inno Setup / NSIS).
- Cross-platform from the start; the portable Python core (watchdog, handlers, uploader, `DaemonConfig`) is shared, and only the OS-glue (startup registration, tray, packaging, the unsigned-warning override) is written per-OS behind an OS-abstraction seam.

## Considered options

- **(1) Docker container with a bind-mounted vault.** Rejected. Live FS events don't cross the Docker Desktop VM boundary on Mac/Windows → forced into polling (breaks the 10s latency target, CPU cost on a large vault). Also breaks the Tier-3 host-path contract (daemon would report container paths). Familiar toolchain, but it's the wrong tool for an event-driven local watcher.
- **(2) Native packaged app, PyInstaller (chosen).** Watching works natively on each OS; paths are host-native (Tier 3 intact); double-click install for a non-technical user. Cost: macOS Gatekeeper / Windows SmartScreen warnings on an unsigned app, and two per-OS build/packaging toolchains.
- **(3) Plain Python process (pip/pipx, run from terminal).** Rejected. Fails the "non-technical manager, 2-minute install" bar — requires a Python install and a terminal.

## Consequences

- **Unsigned-app friction (accepted, separate decision).** No Apple Developer account ($99/yr) and no Windows code-signing cert for the tester phase — the app is unsigned, so first launch triggers Gatekeeper "Open Anyway" / SmartScreen "Run anyway." Handled by a one-time illustrated walkthrough doc handed to testers. Revisit signing when the user pool grows.
- **Two build toolchains + a Windows machine.** PyInstaller can't cross-compile — the `.exe` must be built and tested on a real Windows box. Build matrix is per-OS.
- **Small bundle.** The daemon imports **no** `sentence-transformers` / embedding model (embeddings are cloud-side) — the bundle is just Python + watchdog + the text extractors (`pdfplumber`, `python-docx`, `openpyxl`), keeping PyInstaller output manageable on both OSes.
- **Overturns no prior ADR** — it resolves an open packaging question, not a recorded decision. It does refine the rearch doc's daemon spec (§4 describes the daemon but not its packaging) and the roadmap's "DAEMON INSTALLER" component, which named PyInstaller without ruling out Docker.
- **New runtime deps for the daemon package:** `keyring` (OS secure-store for the API key) and `pystray` (cross-platform tray). Tkinter (wizard UI) is stdlib.
- **Linux is unaffected by the VM-boundary problem** (inotify crosses bind mounts natively) — if a future Linux-server-side-vault scenario appears, Docker could be revisited *for that platform only*. Not in scope now.
