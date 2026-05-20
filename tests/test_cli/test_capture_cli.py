"""CLI tests for kms capture command."""

from __future__ import annotations

from click.testing import CliRunner


def test_capture_no_args_exits_nonzero():
    """kms capture without args exits non-zero (Click enforces required argument)."""
    from cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["capture"])

    assert result.exit_code != 0
