"""Integration tests for the ``kms search`` CLI command.

Each test monkeypatches the search pipeline or indexer functions at the
system boundary so we exercise Click argument parsing, option plumbing,
and output formatting without touching the real search internals.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from click.testing import CliRunner

from cli.main import cli
from core.result import Failure, Success
from retrieval.reranker import SearchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_result(
    vault_path: str = "Projects/Alpha/note.md",
    title: str = "Q3 Budget Report",
    snippet: str = "budget analysis shows <mark>overspend</mark>",
    score: float = 0.87,
) -> SearchResult:
    """Build a ``SearchResult`` with realistic field values."""
    return SearchResult(
        vault_path=vault_path,
        summary="A detailed budget analysis for Q3.",
        snippet=snippet,
        score=score,
        metadata={
            "title": title,
            "project": "Alpha",
            "note_type": "meeting-notes",
            "updated_at": "2026-06-01 12:00:00",
            "key_topics": "budget,finance",
            "tags": "budget,finance",
        },
    )


def _patch_retrieval_search(monkeypatch, mock_fn):
    """Patch ``retrieval.search.search`` via the module object in sys.modules.

    ``retrieval/__init__.py`` re-exports ``search`` at the package level,
    shadowing the module name.  Accessing ``sys.modules["retrieval.search"]``
    gives us the real module object so ``setattr`` works.
    """
    import sys

    _search_mod = sys.modules["retrieval.search"]
    monkeypatch.setattr(_search_mod, "search", mock_fn)


# ---------------------------------------------------------------------------
# Query tests
# ---------------------------------------------------------------------------


def test_search_query_invokes_search_function(monkeypatch):
    """``kms search "budget"`` calls ``search()`` and prints the result title."""
    mock_result = _make_mock_result()

    def _mock_search(*args, **kwargs):
        return Success([mock_result])

    _patch_retrieval_search(monkeypatch, _mock_search)

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "budget"])

    assert result.exit_code == 0, f"stderr: {result.output}"
    assert "Q3 Budget Report" in result.output
    assert "0.87" in result.output


def test_search_project_option(monkeypatch):
    """``kms search --project Alpha "budget"`` passes ``project="Alpha"``."""
    captured = {}

    def _mock_search(*args, **kwargs):
        captured["kwargs"] = kwargs
        return Success([_make_mock_result()])

    _patch_retrieval_search(monkeypatch, _mock_search)

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "--project", "Alpha", "budget"])

    assert result.exit_code == 0, f"stderr: {result.output}"
    assert captured["kwargs"]["project"] == "Alpha"


def test_search_since_7d_parses(monkeypatch):
    """``kms search --since 7d "budget"`` sends a date_range ~7 days ago."""
    captured = {}

    def _mock_search(*args, **kwargs):
        captured["kwargs"] = kwargs
        return Success([_make_mock_result()])

    _patch_retrieval_search(monkeypatch, _mock_search)

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "--since", "7d", "budget"])

    assert result.exit_code == 0, f"stderr: {result.output}"
    date_range = captured["kwargs"]["date_range"]
    assert date_range is not None
    assert date_range[1] is None  # open-ended upper bound
    lower = date_range[0]
    expected_lower = datetime.now() - timedelta(days=7)
    # Allow a few seconds of slop for test execution time
    delta = abs((lower - expected_lower).total_seconds())
    assert delta < 10, f"Expected ~{expected_lower}, got {lower} (delta={delta}s)"


def test_search_since_date_parses(monkeypatch):
    """``kms search --since 2026-06-01 "budget"`` parses to exact datetime."""
    captured = {}

    def _mock_search(*args, **kwargs):
        captured["kwargs"] = kwargs
        return Success([_make_mock_result()])

    _patch_retrieval_search(monkeypatch, _mock_search)

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "--since", "2026-06-01", "budget"])

    assert result.exit_code == 0, f"stderr: {result.output}"
    date_range = captured["kwargs"]["date_range"]
    assert date_range is not None
    assert date_range[0] == datetime(2026, 6, 1)
    assert date_range[1] is None


def test_search_no_query_filter_only(monkeypatch):
    """``kms search --project Alpha`` (no query) calls ``search(query=None)``."""
    captured = {}

    def _mock_search(*args, **kwargs):
        captured["kwargs"] = kwargs
        return Success([_make_mock_result()])

    _patch_retrieval_search(monkeypatch, _mock_search)

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "--project", "Alpha"])

    assert result.exit_code == 0, f"stderr: {result.output}"
    assert captured["kwargs"]["query"] is None
    assert captured["kwargs"]["project"] == "Alpha"


def test_search_max_option(monkeypatch):
    """``kms search --max 5 "budget"`` passes ``max_results=5``."""
    captured = {}

    def _mock_search(*args, **kwargs):
        captured["kwargs"] = kwargs
        return Success([_make_mock_result()])

    _patch_retrieval_search(monkeypatch, _mock_search)

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "--max", "5", "budget"])

    assert result.exit_code == 0, f"stderr: {result.output}"
    assert captured["kwargs"]["max_results"] == 5


def test_search_invalid_since_rejected(monkeypatch):
    """An unparseable ``--since`` value is rejected with exit code 2.

    Covers both ``_parse_since`` failure branches: the ``int()`` path
    (``"xd"``) and the ``strptime`` path (``"garbage"``).  ``search()``
    must never run.
    """

    def _mock_search(*args, **kwargs):
        raise AssertionError("search() must not run when --since is invalid")

    _patch_retrieval_search(monkeypatch, _mock_search)

    runner = CliRunner()
    for bad in ["garbage", "xd"]:
        result = runner.invoke(cli, ["search", "--since", bad, "budget"])
        assert result.exit_code == 2, f"--since {bad}: {result.output}"
        assert "since" in result.output.lower()


def test_search_max_rejects_zero_and_negative(monkeypatch):
    """``--max 0`` and ``--max -1`` are rejected (must be >= 1)."""

    def _mock_search(*args, **kwargs):
        raise AssertionError("search() must not run for an invalid --max")

    _patch_retrieval_search(monkeypatch, _mock_search)

    runner = CliRunner()
    for bad in ["0", "-1"]:
        result = runner.invoke(cli, ["search", "--max", bad, "budget"])
        assert result.exit_code == 2, f"--max {bad}: {result.output}"


def test_search_failure_prints_error(monkeypatch):
    """A ``Failure`` from ``search()`` prints an error and exits 1."""

    def _mock_search(*args, **kwargs):
        return Failure("index corruption detected", recoverable=False, context={})

    _patch_retrieval_search(monkeypatch, _mock_search)

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "budget"])

    assert result.exit_code == 1
    assert "index corruption detected" in result.output


# ---------------------------------------------------------------------------
# --reindex tests
# ---------------------------------------------------------------------------


def test_search_reindex_flag(monkeypatch, tmp_path):
    """``kms search --reindex`` enumerates notes and calls both indexers."""
    # Prepare fake note content and monkeypatched infrastructure
    called_embedding: list[tuple] = []
    called_keywords: list[tuple] = []

    # Fake note returned by read_note
    from dataclasses import dataclass, field

    @dataclass
    class FakeMetadata:
        title: str | None = "Reindex Test"
        type: str | None = "meeting-notes"
        tags: list[str] | None = field(default_factory=list)
        summary: str | None = "A test summary."

    @dataclass
    class FakeNote:
        content: str = "This is the full body of the test note."
        metadata: FakeMetadata = field(default_factory=FakeMetadata)

    # Monkeypatch all_paths to return two vault paths
    monkeypatch.setattr(
        "storage.documents.all_paths",
        lambda db_path=None: Success(
            [
                ("Projects/Alpha/note1.md", "abc123"),
                ("Projects/Alpha/note2.md", "def456"),
            ]
        ),
    )

    # Monkeypatch read_note to return a fake note
    monkeypatch.setattr(
        "vault.reader.read_note",
        lambda path: Success(FakeNote()),
    )

    # Monkeypatch indexers
    def _fake_index_embedding(
        vault_path, title, note_type, tags, summary, db_path=None
    ):
        called_embedding.append((vault_path, title, note_type, tags, summary))
        return Success(None)

    def _fake_index_keywords(vault_path, title, summary, body, db_path=None):
        called_keywords.append((vault_path, title, summary, body))
        return Success(None)

    monkeypatch.setattr("retrieval.embeddings.index_embedding", _fake_index_embedding)
    monkeypatch.setattr("retrieval.keyword.index_keywords", _fake_index_keywords)

    # We also need to mock CONFIG.main.vault.root since the reindex
    # loop calls read_note(CONFIG.main.vault.root / vault_path).
    # Simulate a vault root at tmp_path.
    import core.config

    class FakeVault:
        root: Path = tmp_path

    class FakeDatabase:
        path: Path = tmp_path / "test.db"

    class FakeMain:
        vault = FakeVault()
        database = FakeDatabase()

    class FakeConfig:
        main = FakeMain()

    # Patch the private _CONFIG holder, NOT the public CONFIG name.  CONFIG is
    # served by core.config.__getattr__ (lazy); patching the public name makes
    # monkeypatch materialize a concrete CONFIG attribute on revert, which
    # permanently shadows __getattr__ and leaks the real config (root) into
    # every later test that calls to_vault_path.  Patching _CONFIG matches the
    # pipeline_ctx fixture and keeps __getattr__ intact.
    monkeypatch.setattr(core.config, "_CONFIG", FakeConfig())

    runner = CliRunner()
    result = runner.invoke(cli, ["search", "--reindex"])

    assert result.exit_code == 0, f"stderr: {result.output}"
    assert "2" in result.output  # count of successes
    assert len(called_embedding) == 2
    assert len(called_keywords) == 2

    # Verify both indexers received the correct data
    for call in called_embedding:
        assert call[1] == "Reindex Test"  # title
        assert call[2] == "meeting-notes"  # note_type
    for call in called_keywords:
        assert call[1] == "Reindex Test"  # title
        assert call[2] == "A test summary."  # summary
        assert call[3] == "This is the full body of the test note."  # body


def test_search_reindex_is_standalone():
    """``kms search --reindex "budget"`` is rejected (standalone only)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["search", "--reindex", "budget"])

    assert result.exit_code != 0
    assert "reindex" in result.output.lower()
