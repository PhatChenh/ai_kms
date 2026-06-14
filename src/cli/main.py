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
    """Run the text capture pipeline on a single file. Writes ZERO vault files.

    The file's text content is extracted, hashed, and sent to the DB-only
    capture pipeline (Phase 7A).  No vault files, frontmatter, or sidecar
    .md files are created.
    """
    import asyncio
    import hashlib
    from pathlib import Path

    from core.result import Failure, Success
    from pipelines.capture import capture_upload

    if scan:
        click.echo("NOTICE: --scan is retired. Use the daemon startup scanner instead.")
        return

    if not file:
        raise click.UsageError("Provide a FILE argument.")

    _path = Path(file)
    if not _path.is_absolute():
        # Resolve relative to cwd — no vault writes, so no vault root needed
        _path = _path.resolve()
    if not _path.exists():
        raise click.BadParameter(f"File not found: {_path}", param_hint="FILE")
    if not _path.is_file():
        raise click.BadParameter(f"Not a file: {_path}", param_hint="FILE")

    # Extract text content
    raw_bytes = _path.read_bytes()
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    # Try to decode as UTF-8 text
    try:
        extracted_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        click.echo("FAILED: file is not UTF-8 text. Binary capture is Phase 7B.", err=True)
        raise SystemExit(1)

    # Use the filename stem as the vault_path
    vault_path = _path.name

    async def _run():
        return await capture_upload(
            vault_path=vault_path,
            extracted_text=extracted_text,
            content_hash=content_hash,
            original_filename=_path.name,
            file_size_bytes=_path.stat().st_size,
        )

    capture_result = asyncio.run(_run())
    match capture_result:
        case Success(value=row_id):
            click.echo(f"OK: captured → {vault_path} (row {row_id})")
        case Failure(error=e):
            click.echo(f"FAILED: {e}", err=True)
            raise SystemExit(1)


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
@click.option(
    "--max",
    "max_results",
    default=None,
    type=click.IntRange(min=1),
    help="Max results (must be >= 1)",
)
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


if __name__ == "__main__":
    cli()
