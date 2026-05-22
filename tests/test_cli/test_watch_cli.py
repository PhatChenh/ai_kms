"""CLI tests for kms watch command."""

from __future__ import annotations

from click.testing import CliRunner


def test_watch_help_exits_zero():
    """`kms watch --help` exits 0 — smoke test without starting observer."""
    from cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["watch", "--help"])

    assert result.exit_code == 0
    assert "watch" in result.output.lower()
