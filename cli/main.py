"""
cli/main.py

Entry point for the kms CLI. Loads environment variables once at startup
so all downstream modules can use os.environ.get() without knowing where
keys come from.
"""

from pathlib import Path

from dotenv import load_dotenv

# Load .env before importing anything that reads environment variables.
# override=False means shell-exported vars take precedence over the file.
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

import click  # noqa: E402 — must be after dotenv load

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
@click.argument("file", type=click.Path(exists=True))
def capture(file: str) -> None:
    """Run the capture pipeline on a single file."""
    import asyncio
    from pathlib import Path
    from core.result import Success, Failure
    from pipelines.capture import capture_file

    result = asyncio.run(capture_file(Path(file)))
    match result:
        case Success(value=v):
            click.echo(f"OK: {v}")
        case Failure(error=e):
            click.echo(f"FAILED: {e}", err=True)
            raise SystemExit(1)


@cli.command()
@click.argument("file", type=click.Path(exists=True))
def classify(file: str) -> None:
    """Run the classify pipeline on a single file."""
    raise NotImplementedError("Phase 2 — not yet built")


@cli.command()
@click.argument("query")
def search(query: str) -> None:
    """Semantic + keyword search, hot tier first."""
    raise NotImplementedError("Phase 3 — not yet built")


@cli.command()
def briefing() -> None:
    """Generate today's briefing."""
    raise NotImplementedError("Phase 7 — not yet built")


if __name__ == "__main__":
    cli()
