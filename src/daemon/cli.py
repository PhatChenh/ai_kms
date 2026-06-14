"""
daemon/cli.py

Click CLI for the sync daemon: ``start``, ``scan``, ``status``.

All three commands load ``DaemonConfig`` from a YAML file (default:
``~/.kms-daemon/config.yaml``) and use the ``KMS_DAEMON_API_KEY``
environment variable for authentication.

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

from core.result import Failure, Success
from daemon.cache import DaemonSyncState, LocalCache
from daemon.config import DaemonConfig, load_daemon_config
from daemon.event_reporter import report_deleted, report_moved
from daemon.extractor import BinaryContent, TextContent, extract
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

    async def _run() -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            # ── 1. Startup scan ──────────────────────────────────────────
            result: ScanResult = await scan(cfg, client)
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
                                        cache.set_after_ack(vp, content_hash, st.st_size, st.st_mtime)
                                    _log.debug("skipped (stat changed, content same)", vault_path=vp)
                                    return

                    # ── Extract + upload ─────────────────────────────
                    match extract(disk_path, cfg.vault_root, cfg.max_file_size_bytes):
                        case Success(value=TextContent() as tc):
                            match await upload_text(client, cfg, tc):
                                case Success(value=doc_id):
                                    _log.info("uploaded text", vault_path=vp, doc_id=doc_id)
                                    # Cache-on-ack
                                    if cache is not None and st is not None:
                                        cache.set_after_ack(vp, tc.content_hash, st.st_size, st.st_mtime)
                                case Failure() as f:
                                    _log.warning("upload_text failed", vault_path=vp, error=f.error)
                        case Success(value=BinaryContent() as bc):
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
                """Schedule a delete event report."""
                async def _handle() -> None:
                    match await report_deleted(client, cfg, vp):
                        case Success():
                            _log.info("reported deleted", vault_path=vp)
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

            # ── 4. Main loop ─────────────────────────────────────────────
            try:
                while True:
                    await asyncio.sleep(1)
            finally:
                watcher.stop()
                watcher.join()
                _log.info("watcher stopped")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        click.echo("")  # newline after ^C
        click.echo("Daemon stopped.")
