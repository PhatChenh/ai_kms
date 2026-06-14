"""
daemon/cli.py

Click CLI for the sync daemon: ``start``, ``scan``, ``status``, ``uninstall``.

All four commands load ``DaemonConfig`` from a YAML file (default:
``~/.kms-daemon/config.yaml``) and use the ``KMS_DAEMON_API_KEY``
environment variable for authentication.  The ``uninstall`` command
does not require the API key — it removes the key, config file, and
auto-start registration.

Structlog is configured independently — this module never imports from
``core/logging_setup.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import click
import httpx
import structlog

import threading

from core.result import Failure, Result, Success
from daemon.cache import DaemonSyncState, LocalCache
from daemon.config import DaemonConfig, load_daemon_config
from daemon.event_reporter import report_deleted, report_moved
from daemon.extractor import BinaryContent, TextContent, extract
from daemon.move_buffer import MoveBuffer
from daemon.scanner import ScanResult, scan
from daemon.uploader import upload_binary, upload_text
from daemon.watcher import DaemonWatcher

# ── structlog configuration (standalone — no core/logging_setup) ────────────

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

_log = structlog.get_logger("daemon.cli")

# ── common helpers ───────────────────────────────────────────────────────────

_CONFIG_OPTION = click.option(
    "--config",
    "config_path",
    default=str(Path.home() / ".kms-daemon" / "config.yaml"),
    show_default=True,
    help="Path to the daemon configuration YAML file.",
)


def _load_config(config_path: str) -> DaemonConfig:
    """Load daemon config from *config_path*, reporting errors cleanly."""
    try:
        return load_daemon_config(Path(config_path))
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc))
    except ValueError as exc:
        raise click.ClickException(str(exc))


# ── CLI group ────────────────────────────────────────────────────────────────


@click.group()
def cli() -> None:
    """KMS Sync Daemon — watch, scan, and reconcile vault with cloud."""


# ── status command ───────────────────────────────────────────────────────────


@cli.command()
@_CONFIG_OPTION
def status(config_path: str) -> None:
    """Check connectivity to the cloud endpoint."""

    async def _run() -> None:
        cfg = _load_config(config_path)
        url = f"{cfg.cloud_endpoint}/health"
        _log.info("checking cloud endpoint", url=url)
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {cfg.api_key}"},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _log.error(
                    "health check failed",
                    status_code=exc.response.status_code,
                    body=exc.response.text[:200],
                )
                raise click.ClickException(
                    f"Cloud endpoint returned {exc.response.status_code}: "
                    f"{exc.response.text[:200]}"
                )
            except httpx.RequestError as exc:
                _log.error("health check unreachable", error=str(exc))
                raise click.ClickException(
                    f"Cannot reach cloud endpoint: {exc}"
                )
            else:
                _log.info("cloud endpoint is healthy", status_code=resp.status_code)
                click.echo(f"✓ Cloud endpoint reachable ({url})")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        raise click.Abort()


# ── uninstall cleanup ────────────────────────────────────────────────────────


def run_uninstall(config_path: Path | None = None) -> "Result[dict]":
    """Remove key, config file, and auto-start registration.

    Idempotent — safe to call multiple times.  Each step handles its
    own absence: ``delete_key`` returns ``Success`` when the key is
    already gone, ``unregister_at_login`` does the same for the
    registration, and missing config files are simply skipped.

    Args:
        config_path: Path to the config file.  Defaults to
            ``~/.kms-daemon/config.yaml``.

    Returns:
        ``Success({"removed": [...]})`` listing what was actually removed,
        or ``Failure(...)`` if a step returned an unrecoverable error.
    """
    from daemon.os_glue import get_os_adapter  # lazy import (Phase 3)
    from daemon.secret_vault import delete_key, read_key  # lazy import (Phase 1)

    if config_path is None:
        config_path = Path.home() / ".kms-daemon" / "config.yaml"

    removed: list[str] = []

    # 1. Delete the key from the OS vault (only if it exists)
    match read_key():
        case Success():
            match delete_key():
                case Success():
                    removed.append("key")
                case Failure() as f:
                    return f
        case Failure():
            pass  # key already absent — nothing to remove

    # 2. Remove the config file (skip if absent)
    if config_path.exists():
        try:
            config_path.unlink()
        except OSError as exc:
            return Failure(
                error=f"Failed to remove config file: {exc}",
                recoverable=False,
                context={"config_path": str(config_path)},
            )
        removed.append("config")

    # 3. Unregister from auto-start at login
    match get_os_adapter().unregister_at_login():
        case Success():
            removed.append("registration")
        case Failure() as f:
            return f

    return Success({"removed": removed})


@cli.command()
@_CONFIG_OPTION
def uninstall(config_path: str) -> None:
    """Remove the daemon key, config, and auto-start registration.

    Safe to run on a machine that is already partly or fully
    uninstalled — every step is idempotent.
    """
    match run_uninstall(Path(config_path)):
        case Success(value=result):
            removed = result["removed"]
            if removed:
                click.echo(f"Uninstall complete. Removed: {', '.join(removed)}")
            else:
                click.echo("Nothing to remove — already clean.")
        case Failure() as f:
            raise click.ClickException(f.error)


# ── scan command ─────────────────────────────────────────────────────────────


@cli.command(name="scan")
@_CONFIG_OPTION
def scan_cmd(config_path: str) -> None:
    """Run a one-shot reconcile and print the summary."""

    async def _run() -> None:
        cfg = _load_config(config_path)
        async with httpx.AsyncClient(timeout=30) as client:
            result: ScanResult = await scan(cfg, client)
        click.echo(f"Scan complete — vault: {cfg.vault_root}")
        click.echo(f"  Uploaded:    {result.uploaded}")
        click.echo(f"  Re-uploaded: {result.re_uploaded}")
        click.echo(f"  Deleted:     {result.deleted}")
        click.echo(f"  Skipped:     {result.skipped}")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        raise click.Abort()


# ── periodic reconcile ───────────────────────────────────────────────────────


async def _periodic_reconcile(
    cfg: DaemonConfig,
    client: httpx.AsyncClient,
    cache: LocalCache,
    candidate_deletes: dict[str, int],
    sweep_confirmations: int,
) -> None:
    """Re-run the 3-way reconcile on a timer while the daemon is running."""
    _log.info("periodic reconcile started", interval_seconds=cfg.periodic_interval_seconds)
    while True:
        await asyncio.sleep(cfg.periodic_interval_seconds)
        _log.debug("periodic reconcile running")
        try:
            result = await scan(
                cfg,
                client,
                cache=cache,
                candidate_deletes=candidate_deletes,
                sweep_delete_confirmations=sweep_confirmations,
            )
            _log.info(
                "periodic reconcile complete",
                uploaded=result.uploaded,
                re_uploaded=result.re_uploaded,
                deleted=result.deleted,
                skipped=result.skipped,
                moved=result.moved,
            )
            cache.save(Path(cfg.cache_path).expanduser())
        except Exception:
            _log.exception("periodic reconcile failed, will retry on next interval")


# ── start command ────────────────────────────────────────────────────────────


@cli.command()
@_CONFIG_OPTION
def start(config_path: str) -> None:
    """Start the sync daemon — scan then watch for live changes.

    Runs a startup reconciliation scan, then watches the vault for
    filesystem events (create, modify, move, delete) and uploads or
    reports changes to the cloud endpoint in real time.

    Press Ctrl+C to stop the daemon gracefully.
    """

    cfg = _load_config(config_path)

    # ── Cache wiring ─────────────────────────────────────────────────────
    sync_state = DaemonSyncState()
    cache = LocalCache(sync_state)
    cache.load(Path(cfg.cache_path).expanduser())
    move_buffer = MoveBuffer(sync_state)  # shares the same lock via sync_state

    _move_timer: threading.Timer | None = None
    _move_timer_lock = threading.Lock()
    periodic_task: asyncio.Task[None] | None = None

    async def _run() -> None:
        nonlocal _move_timer, periodic_task
        async with httpx.AsyncClient(timeout=30) as client:
            # ── 1. Startup scan ──────────────────────────────────────────
            candidate_deletes: dict[str, int] = {}
            sweep_confirmations = cfg.sweep_delete_confirmations

            result: ScanResult = await scan(
                cfg, client,
                cache=cache,
                candidate_deletes=candidate_deletes,
                sweep_delete_confirmations=sweep_confirmations,
            )
            _log.info(
                "startup scan complete",
                uploaded=result.uploaded,
                re_uploaded=result.re_uploaded,
                deleted=result.deleted,
                skipped=result.skipped,
            )
            click.echo(
                f"Scan complete: {result.uploaded} uploaded, "
                f"{result.re_uploaded} re-uploaded, "
                f"{result.deleted} deleted, "
                f"{result.skipped} skipped"
            )

            # ── Persist cache after startup scan ─────────────────────────
            cache.save(Path(cfg.cache_path).expanduser())

            loop = asyncio.get_running_loop()

            # ── 2. Watcher callbacks ─────────────────────────────────────

            def _refresh_move_timer() -> None:
                """Start or refresh the move-correlation window timer."""
                nonlocal _move_timer
                with _move_timer_lock:
                    if _move_timer is not None:
                        _move_timer.cancel()
                    _move_timer = threading.Timer(
                        cfg.move_window_seconds,
                        _on_move_window_expired,
                    )
                    _move_timer.daemon = True
                    _move_timer.start()

            def _on_move_window_expired() -> None:
                """Called when the move-correlation window expires."""
                expired = move_buffer.expire(cfg.move_window_seconds)
                if not expired:
                    return
                async def _handle_expired() -> None:
                    for fingerprint, old_vp in expired:
                        match await report_deleted(client, cfg, old_vp):
                            case Success():
                                _log.info("reported deleted (move window expired)", vault_path=old_vp)
                                cache.forget(old_vp)
                            case Failure() as f:
                                _log.warning("report_deleted failed (expired)", vault_path=old_vp, error=f.error)
                asyncio.run_coroutine_threadsafe(_handle_expired(), loop)

            def _on_create_or_modify(vp: str) -> None:
                """Schedule an extract + upload for a vault-relative path."""
                async def _handle() -> None:
                    disk_path = cfg.vault_root / vp
                    st = None

                    # ── Bail-early: stat-then-hash pre-filter ──
                    try:
                        st = disk_path.stat()
                    except OSError:
                        pass  # can't stat, proceed with extract
                    else:
                        cached = cache.get(vp) if cache is not None else None
                        if cached is not None:
                            if st.st_size == cached["size"] and st.st_mtime == cached["mtime"]:
                                _log.debug("skipped (unchanged)", vault_path=vp)
                                return  # bail-early — stat matches
                            # Stat differs → hash the file
                            try:
                                raw = disk_path.read_bytes()
                            except OSError:
                                pass  # can't read, fall through to extract
                            else:
                                content_hash = hashlib.sha256(raw).hexdigest()
                                if content_hash == cached["hash"]:
                                    # Content same, just update stat in cache
                                    if cache is not None:
                                        cache.touch(vp, st.st_size, st.st_mtime)
                                    _log.debug("skipped (stat changed, content same)", vault_path=vp)
                                    return

                    # ── Extract ───────────────────────────────────────
                    match extract(disk_path, cfg.vault_root, cfg.max_file_size_bytes):
                        case Success() as extracted:
                            # ── After extract, before upload: check Move Detective ──
                            content_hash = extracted.value.content_hash

                            old_vp = move_buffer.match_create(content_hash)
                            if old_vp is not None:
                                # This is a move! Report instead of uploading.
                                match await report_moved(client, cfg, old_vp, vp):
                                    case Success():
                                        _log.info("detected move via buffer", old_path=old_vp, new_path=vp)
                                        cache.forget(old_vp)
                                        if st is not None:
                                            cache.set_after_ack(vp, content_hash, st.st_size, st.st_mtime)
                                    case Failure() as f:
                                        _log.warning("report_moved failed (via buffer)", old_path=old_vp, new_path=vp, error=f.error)
                                return  # skip upload

                            # ── Normal upload ─────────────────────────
                            if isinstance(extracted.value, TextContent):
                                tc = extracted.value
                                match await upload_text(client, cfg, tc):
                                    case Success(value=doc_id):
                                        _log.info("uploaded text", vault_path=vp, doc_id=doc_id)
                                        # Cache-on-ack
                                        if cache is not None and st is not None:
                                            cache.set_after_ack(vp, tc.content_hash, st.st_size, st.st_mtime)
                                    case Failure() as f:
                                        _log.warning("upload_text failed", vault_path=vp, error=f.error)
                            else:
                                bc = extracted.value
                                match await upload_binary(client, cfg, bc):
                                    case Success(value=doc_id):
                                        _log.info("uploaded binary", vault_path=vp, doc_id=doc_id)
                                        if cache is not None and st is not None:
                                            cache.set_after_ack(vp, bc.content_hash, st.st_size, st.st_mtime)
                                    case Failure() as f:
                                        _log.warning("upload_binary failed", vault_path=vp, error=f.error)
                        case Failure() as f:
                            _log.warning("extract failed", vault_path=vp, error=f.error)

                asyncio.run_coroutine_threadsafe(_handle(), loop)

            def _on_move(old_vp: str, new_vp: str) -> None:
                """Schedule a move event report."""
                async def _handle() -> None:
                    match await report_moved(client, cfg, old_vp, new_vp):
                        case Success():
                            _log.info("reported moved", old_path=old_vp, new_path=new_vp)
                            # Update cache
                            if cache is not None:
                                new_disk_path = cfg.vault_root / new_vp
                                try:
                                    st = new_disk_path.stat()
                                    raw = new_disk_path.read_bytes()
                                    content_hash = hashlib.sha256(raw).hexdigest()
                                    cache.forget(old_vp)
                                    cache.set_after_ack(new_vp, content_hash, st.st_size, st.st_mtime)
                                except OSError:
                                    _log.warning("cannot fingerprint moved file for cache", new_path=new_vp)
                        case Failure() as f:
                            _log.warning("report_moved failed", old_path=old_vp, new_path=new_vp, error=f.error)

                asyncio.run_coroutine_threadsafe(_handle(), loop)

            def _on_delete(vp: str) -> None:
                """Buffer the delete in Move Detective; report only after window expires with no match."""
                fingerprint_entry = cache.get(vp)  # file is gone; cache is only hash source
                if fingerprint_entry is not None:
                    move_buffer.park_delete(fingerprint_entry["hash"], vp)
                    # Start/refresh expiry timer
                    _refresh_move_timer()
                else:
                    # Not in cache — can't match, report immediately
                    async def _handle() -> None:
                        match await report_deleted(client, cfg, vp):
                            case Success():
                                _log.info("reported deleted (no cache entry)", vault_path=vp)
                            case Failure() as f:
                                _log.warning("report_deleted failed", vault_path=vp, error=f.error)
                    asyncio.run_coroutine_threadsafe(_handle(), loop)

            # ── 3. Start watcher ─────────────────────────────────────────
            watcher = DaemonWatcher(
                cfg,
                on_create=_on_create_or_modify,
                on_modify=_on_create_or_modify,
                on_move=_on_move,
                on_delete=_on_delete,
            )
            watcher.start()
            _log.info("watcher started", vault_root=str(cfg.vault_root))
            click.echo(f"Watching {cfg.vault_root} — Ctrl+C to stop")

            # ── Periodic reconcile timer ─────────────────────────────────
            if cfg.periodic_interval_seconds > 0:
                periodic_task = asyncio.create_task(
                    _periodic_reconcile(cfg, client, cache, candidate_deletes, sweep_confirmations)
                )
            else:
                periodic_task = None

            # ── 4. Main loop ─────────────────────────────────────────────
            try:
                while True:
                    await asyncio.sleep(1)
            finally:
                # Cancel periodic reconcile
                if periodic_task is not None:
                    periodic_task.cancel()
                    try:
                        await periodic_task
                    except asyncio.CancelledError:
                        pass

                with _move_timer_lock:
                    if _move_timer is not None:
                        _move_timer.cancel()
                cache.save(Path(cfg.cache_path).expanduser())
                watcher.stop()
                watcher.join()
                _log.info("watcher stopped")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        click.echo("")  # newline after ^C
        click.echo("Daemon stopped.")
