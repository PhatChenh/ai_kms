"""
cli/main.py

Entry point for the kms CLI. Loads environment variables once at startup
so all downstream modules can use os.environ.get() without knowing where
keys come from.
"""

from pathlib import Path

import yaml
from dotenv import load_dotenv

# Load .env before importing anything that reads environment variables.
# override=False means shell-exported vars take precedence over the file.
load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)

import click  # noqa: E402 — must be after dotenv load
from core.logging_setup import setup_logging  # noqa: E402

# Read logging settings directly from config.yaml — avoids full CONFIG load
# (which validates vault root) before logging is ready.
_log_cfg: dict = {}
try:
    with open(
        Path(__file__).parent.parent / "config" / "config.yaml", encoding="utf-8"
    ) as _f:
        _log_cfg = (yaml.safe_load(_f) or {}).get("logging", {})
except Exception:
    pass

setup_logging(
    log_level=str(_log_cfg.get("level", "INFO")),
    dev_mode=bool(_log_cfg.get("console", True)),
)

# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """AI-KMS: AI-enhanced knowledge management system."""


# ---------------------------------------------------------------------------
# Placeholder commands — filled in as pipelines are built
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("file", type=str, required=False)
@click.option(
    "--scan",
    is_flag=True,
    default=False,
    help="Capture all un-indexed notes in vault. Modified notes are not re-captured.",
)
def capture(file: str | None, scan: bool) -> None:
    """Run the capture pipeline on a single file, or scan the vault with --scan."""
    import asyncio
    from pathlib import Path
    from core.result import Success, Failure
    from pipelines.capture import capture_file, scan_capture

    if scan:
        scan_result = asyncio.run(scan_capture())
        match scan_result:
            case Success(value=outcomes):
                click.echo(f"OK: captured {len(outcomes)} file(s)")
            case Failure(error=e):
                click.echo(f"FAILED: {e}", err=True)
                raise SystemExit(1)
    elif file:
        from core.config import CONFIG

        _path = Path(file)
        if not _path.is_absolute():
            _path = CONFIG.main.vault.root / _path
        if not _path.exists():
            raise click.BadParameter(f"File not found: {_path}", param_hint="FILE")
        capture_result = asyncio.run(capture_file(_path))
        match capture_result:
            case Success(value=outcome):
                click.echo(f"OK: captured → {outcome.vault_path}")
            case Failure(error=e):
                click.echo(f"FAILED: {e}", err=True)
                raise SystemExit(1)
    else:
        raise click.UsageError("Provide a FILE argument or use --scan.")


@cli.command()
@click.argument("file", type=str)
def classify(file: str) -> None:
    """Run the classify pipeline on a single file."""
    from core.config import CONFIG

    _path = Path(file)
    if not _path.is_absolute():
        _path = CONFIG.main.vault.root / _path
    if not _path.exists():
        raise click.BadParameter(f"File not found: {_path}", param_hint="FILE")
    raise NotImplementedError("Phase 2 — not yet built")


@cli.command()
@click.argument("query", required=False, default=None)
@click.option("--project", default=None, help="Filter by project name")
@click.option(
    "--since",
    default=None,
    help="Filter by date: 7d, 30d, or YYYY-MM-DD",
)
@click.option("--max", "max_results", default=None, type=int, help="Max results")
@click.option(
    "--reindex",
    is_flag=True,
    default=False,
    help="Rebuild search indexes (standalone — no query or options)",
)
def search(
    query: str | None,
    project: str | None,
    since: str | None,
    max_results: int | None,
    reindex: bool,
) -> None:
    """Semantic + keyword search.  Use --reindex to rebuild search indexes."""
    # ------------------------------------------------------------------
    # --reindex mode: standalone maintenance, no query allowed
    # ------------------------------------------------------------------
    if reindex:
        if query is not None:
            raise click.UsageError("--reindex cannot be combined with a query.")
        _run_reindex()
        return

    # ------------------------------------------------------------------
    # Parse --since into a date_range tuple
    # ------------------------------------------------------------------
    date_range: tuple | None = None
    if since is not None:
        date_range = _parse_since(since)

    # ------------------------------------------------------------------
    # Run search
    # ------------------------------------------------------------------
    from core.result import Failure, Success
    from retrieval.search import search as _search

    match _search(
        query=query,
        project=project,
        date_range=date_range,
        max_results=max_results,
    ):
        case Success(cards):
            if not cards:
                click.echo("(no results)")
            else:
                for card in cards:
                    _print_result_card(card)
        case Failure(error=e):
            click.echo(f"FAILED: {e}", err=True)
            raise SystemExit(1)


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------


def _parse_since(since: str) -> tuple:
    """Parse a ``--since`` value into a ``(lower, None)`` date_range tuple.

    Supported formats: ``"7d"``, ``"30d"``, ``"YYYY-MM-DD"``.
    """
    from datetime import datetime, timedelta

    since = since.strip()
    if since.endswith("d"):
        try:
            days = int(since[:-1])
        except ValueError:
            raise click.BadParameter(
                f"Invalid --since format: {since!r} (expected 7d, 30d, or YYYY-MM-DD)",
                param_hint="--since",
            )
        return (datetime.now() - timedelta(days=days), None)

    try:
        return (datetime.strptime(since, "%Y-%m-%d"), None)
    except ValueError:
        raise click.BadParameter(
            f"Invalid --since format: {since!r} (expected 7d, 30d, or YYYY-MM-DD)",
            param_hint="--since",
        )


def _print_result_card(card) -> None:
    """Print a single ``SearchResult`` card to stdout."""
    meta = card.metadata or {}
    title = meta.get("title", card.vault_path)
    project = meta.get("project", "")
    note_type = meta.get("note_type", "")
    tags = meta.get("tags", "")

    click.echo(f"{title}")
    click.echo(f"  score={card.score:.4f}  project={project}  type={note_type}")
    if tags:
        click.echo(f"  tags: {tags}")
    if card.snippet:
        click.echo(f"  {card.snippet}")
    if card.summary:
        click.echo(f"  {card.summary[:200]}")
    click.echo()


def _run_reindex() -> None:
    """Rebuild search indexes from every note in the document catalog."""
    from pathlib import Path

    from core.config import CONFIG
    from core.result import Failure, Success
    from retrieval.embeddings import index_embedding
    from retrieval.keyword import index_keywords
    from storage.documents import all_paths
    from vault.reader import read_note

    db_path = CONFIG.main.database.path
    vault_root = CONFIG.main.vault.root

    match all_paths(db_path):
        case Failure(error=e):
            click.echo(f"FAILED: cannot enumerate notes: {e}", err=True)
            raise SystemExit(1)
        case Success(rows):
            pass

    success_count = 0
    total = len(rows)

    for vault_path, _hash in rows:
        note_path = vault_root / vault_path
        match read_note(note_path):
            case Failure(error=e):
                click.echo(f"SKIP {vault_path}: read failed — {e}", err=True)
                continue
            case Success(note):
                pass

        meta = note.metadata
        title = meta.title or Path(vault_path).stem
        note_type = meta.type or ""
        tags = meta.tags or []
        summary = meta.summary or ""
        body = note.content or ""

        match index_embedding(
            vault_path, title, note_type, tags, summary, db_path=db_path
        ):
            case Failure(error=e):
                click.echo(f"SKIP {vault_path}: embedding index failed — {e}", err=True)
                continue

        match index_keywords(vault_path, title, summary, body, db_path=db_path):
            case Failure(error=e):
                click.echo(f"SKIP {vault_path}: keyword index failed — {e}", err=True)
                continue

        success_count += 1

    click.echo(f"Reindexed {success_count}/{total} note(s)")


@cli.command()
def briefing() -> None:
    """Generate today's briefing."""
    raise NotImplementedError("Phase 7 — not yet built")


@cli.command()
def reconcile() -> None:
    """Sync search index to vault — fix paths, capture missing, re-summarize stale, clean orphans."""
    import asyncio
    from core.config import CONFIG
    from core.logging_setup import new_correlation_id
    from core.pipeline import PipelineContext
    from core.result import Failure, Success
    from pipelines.reconcile import reconcile as run_reconcile

    async def _run() -> None:
        ctx = PipelineContext(
            config=CONFIG.main,
            db_path=CONFIG.main.database.path,
            correlation_id=new_correlation_id(),
        )
        match await run_reconcile(ctx):
            case Success(value=r):
                click.echo(
                    f"OK: {r.paths_reconciled} paths reconciled, "
                    f"{r.new_captures} new binary captured, "
                    f"{r.restale_count} stale binaries re-summarized, "
                    f"{r.orphans_cleaned} orphans cleaned, "
                    f"{r.tags_updated} tags updated, "
                    f"{r.batch_refs_cleared} stale batch refs cleared"
                )
            case Failure(error=e):
                click.echo(f"FAILED: {e}", err=True)
                raise SystemExit(1)

    asyncio.run(_run())


@cli.command()
def watch() -> None:
    """Watch vault root; capture new drops from any folder automatically.

    Taxonomy (Domain/ folders) is loaded once at startup.
    New Domain/ folders added while the watcher runs require a restart.
    """
    import asyncio
    import hashlib
    import threading
    from pathlib import Path

    import structlog
    from core.config import CONFIG
    from core.logging_setup import new_correlation_id
    from core.pipeline import PipelineContext
    from core.result import Failure, Success
    from core.tags import load_taxonomy
    from pipelines.capture import capture_file, scan_capture
    from storage.documents import delete_by_path, get_by_path
    from storage.documents import rename as rename_doc
    from vault.paths import load_valid_domains, to_vault_path
    from vault.reader import read_note
    from vault.watcher import VaultWatcher

    _wlog = structlog.get_logger("cli.watch")

    root = CONFIG.main.vault.root
    db_path = CONFIG.main.database.path

    valid_domains = load_valid_domains(root)
    taxonomy = load_taxonomy(
        Path(__file__).parent.parent / "config" / "tags.yaml",
        valid_domains,
    )

    async def _run() -> None:
        await scan_capture()  # reconcile drops that arrived while watcher was down

        loop = asyncio.get_running_loop()

        def _make_ctx() -> PipelineContext:
            return PipelineContext(
                config=CONFIG.main,
                db_path=db_path,
                correlation_id=new_correlation_id(),
                taxonomy=taxonomy,
            )

        # Tracks paths whose capture pipeline is currently running.
        # Prevents a second pipeline launch when on_modified fires before
        # on_created's pipeline writes to DB (the DB-based guards can't help
        # until the first pipeline completes).
        _in_flight: set[str] = set()
        _in_flight_lock = threading.Lock()

        def _dispatch(path: Path) -> None:
            key = str(to_vault_path(path))
            with _in_flight_lock:
                if key in _in_flight:
                    _wlog.debug("watcher.skip_in_flight", vault_path=key)
                    return
                _in_flight.add(key)
            try:
                future = asyncio.run_coroutine_threadsafe(
                    capture_file(path, context=_make_ctx()), loop
                )
                future.add_done_callback(lambda _: _in_flight.discard(key))
            except Exception:
                _in_flight.discard(key)
                raise

        def on_create(path: Path) -> None:
            vault_rel = to_vault_path(path)
            # Skip if pipeline just wrote this file — prevents re-capture after AI rename.
            # By the time this debounced callback fires (≥3s after the event), replace_path
            # has already inserted the new vault_path into the DB.
            match get_by_path(vault_rel, db_path=db_path):
                case Success(value=row) if row is not None:
                    _wlog.debug(
                        "watcher.create_skip",
                        vault_path=vault_rel,
                        reason="already_in_db",
                    )
                    return
                case _:
                    pass
            _dispatch(path)

        def on_modify(path: Path) -> None:
            vault_rel = to_vault_path(path)
            # Skip if body is unchanged — this is an AI frontmatter-only write, not a user edit.
            # content_hash in DB is SHA256(body), same formula used by write_note.
            match get_by_path(vault_rel, db_path=db_path):
                case Success(value=row) if row is not None and row.content_hash:
                    match read_note(path):
                        case Success(value=note):
                            if (
                                hashlib.sha256(note.content.encode()).hexdigest()
                                == row.content_hash
                            ):
                                _wlog.debug(
                                    "watcher.modify_skip",
                                    vault_path=vault_rel,
                                    reason="hash_unchanged",
                                )
                                return
                        case _:
                            pass
                case _:
                    pass
            _dispatch(path)

        def on_delete(path: Path) -> None:
            if path.exists():
                # Spurious delete fired by macOS FSEvents when os.replace(tmp, dst) atomically
                # overwrites an existing dst — the old inode is "deleted" but the path remains.
                return
            vault_rel = to_vault_path(path)
            match delete_by_path(vault_rel, db_path=db_path):
                case Success(value=rowcount):
                    if rowcount == 0:
                        _wlog.warning(
                            "watcher.binary_not_in_index", vault_path=vault_rel
                        )
                    else:
                        _wlog.info("watcher.deleted", vault_path=vault_rel)
                case Failure(error=e):
                    _wlog.warning(
                        "watcher.delete_failed", vault_path=vault_rel, error=e
                    )

        def on_move(src: Path, dst: Path) -> None:
            if src.name.startswith("."):
                return
            old_rel = to_vault_path(src)
            new_rel = to_vault_path(dst)
            match rename_doc(old_rel, new_rel, db_path=db_path):
                case Success(value=rowcount):
                    if rowcount == 0:
                        _wlog.warning(
                            "watcher.binary_not_in_index", old=old_rel, new=new_rel
                        )
                    else:
                        _wlog.info("watcher.renamed", old=old_rel, new=new_rel)
                case Failure(error=e):
                    _wlog.warning(
                        "watcher.rename_failed", old=old_rel, new=new_rel, error=e
                    )

        watcher = VaultWatcher(
            root=root,
            vault_config=CONFIG.main.vault,
            on_create=on_create,
            on_modify=on_modify,
            on_delete=on_delete,
            on_move=on_move,
            folder_cooldown_seconds=CONFIG.main.capture.folder_cooldown_seconds,
            binary_settle_seconds=CONFIG.main.capture.binary_settle_seconds,
        )
        from vault.move_guard import set_active as set_active_guard

        set_active_guard(watcher._move_guard)
        watcher.start()
        click.echo(f"Watching {root} — Ctrl-C to stop")
        try:
            while True:
                await asyncio.sleep(1)
        finally:
            watcher.stop()
            watcher.join()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
