"""Phase 4 Component 7: best-effort search indexing after capture.

Tests that embedding + keyword indexers are called after successful
captures, that failures are swallowed, and that binary captures index
the sibling summary note.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.result import Success


# ---------------------------------------------------------------------------
# P3-IDX-01: Embedding indexed after MD capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_md_capture_indexes_embedding(vault_root, pipeline_ctx, monkeypatch):
    """After capturing a .md note, embeddings_vec has a row for its vault_path."""
    from pipelines.capture import capture_file
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse
    from storage.db import get_connection

    md_file = vault_root / "inbox" / "embedding-note.md"
    md_file.write_text(
        "# Embedding Note\n\nBody text for semantic search.", encoding="utf-8"
    )

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="AI-generated summary.", model="test", usage={})),
        Success(
            LLMResponse(
                content='{"title": "embedding-note", "type": "note", "tags": ["test"]}',
                model="test",
                usage={},
            )
        ),
    ]
    monkeypatch.setattr(
        "pipelines.capture.get_provider", lambda task, config: mock_provider
    )

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success)
    assert isinstance(result.value, WriteOutcome)
    vault_path = result.value.vault_path

    # Verify embedding row exists
    with get_connection(pipeline_ctx.db_path) as conn:
        row = conn.execute(
            "SELECT vault_path FROM embeddings_vec WHERE vault_path = ?",
            (vault_path,),
        ).fetchone()
    assert row is not None, f"embeddings_vec missing row for {vault_path}"


# ---------------------------------------------------------------------------
# P3-IDX-02: Keywords indexed after MD capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_md_capture_indexes_keywords(vault_root, pipeline_ctx, monkeypatch):
    """After capturing a .md note, notes_fts contains the distinctive body text."""
    from pipelines.capture import capture_file
    from llm.provider import LLMResponse
    from storage.db import get_connection

    md_file = vault_root / "inbox" / "keyword-note.md"
    md_file.write_text(
        "# Keyword Note\n\nThis note mentions zebra in the body.", encoding="utf-8"
    )

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Summary about zebras.", model="test", usage={})),
        Success(
            LLMResponse(
                content='{"title": "keyword-note", "type": "note", "tags": ["test"]}',
                model="test",
                usage={},
            )
        ),
    ]
    monkeypatch.setattr(
        "pipelines.capture.get_provider", lambda task, config: mock_provider
    )

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success)
    vault_path = result.value.vault_path

    # Verify FTS5 match for distinctive word
    with get_connection(pipeline_ctx.db_path) as conn:
        row = conn.execute(
            "SELECT vault_path FROM notes_fts WHERE notes_fts MATCH ?",
            ("zebra",),
        ).fetchone()
    assert row is not None, (
        f"notes_fts missing row for {vault_path} — 'zebra' not found"
    )
    assert row[0] == vault_path


# ---------------------------------------------------------------------------
# P3-IDX-03: Embedding failure doesn't block capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_failure_does_not_block_capture(
    vault_root, pipeline_ctx, monkeypatch
):
    """When index_embedding raises, capture still returns Success."""
    from pipelines.capture import capture_file
    from llm.provider import LLMResponse

    md_file = vault_root / "inbox" / "embed-fail-note.md"
    md_file.write_text(
        "# Embed Fail\n\nThis note still captures fine.", encoding="utf-8"
    )

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Summary.", model="test", usage={})),
        Success(
            LLMResponse(
                content='{"title": "embed-fail-note", "type": "note", "tags": ["test"]}',
                model="test",
                usage={},
            )
        ),
    ]
    monkeypatch.setattr(
        "pipelines.capture.get_provider", lambda task, config: mock_provider
    )

    # Patch index_embedding to simulate a crash
    monkeypatch.setattr(
        "retrieval.embeddings.index_embedding",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("model crashed")),
    )

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success), (
        f"capture should succeed even when embedding indexing fails, got {result}"
    )


# ---------------------------------------------------------------------------
# P3-IDX-04: Keyword failure doesn't block capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keyword_failure_does_not_block_capture(
    vault_root, pipeline_ctx, monkeypatch
):
    """When index_keywords raises, capture still returns Success."""
    from pipelines.capture import capture_file
    from llm.provider import LLMResponse

    md_file = vault_root / "inbox" / "keyword-fail-note.md"
    md_file.write_text("# Keyword Fail\n\nBody text here.", encoding="utf-8")

    mtime = md_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Summary.", model="test", usage={})),
        Success(
            LLMResponse(
                content='{"title": "keyword-fail-note", "type": "note", "tags": ["test"]}',
                model="test",
                usage={},
            )
        ),
    ]
    monkeypatch.setattr(
        "pipelines.capture.get_provider", lambda task, config: mock_provider
    )

    # Patch index_keywords to simulate a crash
    monkeypatch.setattr(
        "retrieval.keyword.index_keywords",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("FTS5 insert failed")),
    )

    result = await capture_file(md_file, context=pipeline_ctx)

    assert isinstance(result, Success), (
        f"capture should succeed even when keyword indexing fails, got {result}"
    )


# ---------------------------------------------------------------------------
# P3-IDX-05: Non-MD capture indexes sibling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_md_capture_indexes_sibling(vault_root, pipeline_ctx, monkeypatch):
    """After capturing a binary file, the sibling .md vault_path appears in both
    embeddings_vec and notes_fts."""
    from pipelines.capture import capture_file
    from vault.writer import WriteOutcome
    from llm.provider import LLMResponse
    from storage.db import get_connection

    # Create project attachment directory and copy a real PDF fixture
    att_dir = vault_root / "Projects" / "Alpha" / "attachment"
    att_dir.mkdir(parents=True, exist_ok=True)
    binary_file = att_dir / "report.pdf"
    import shutil

    _FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "sample_text.pdf"
    shutil.copy(_FIXTURE_PDF, binary_file)

    mtime = binary_file.stat().st_mtime
    monkeypatch.setattr("pipelines.capture.time", MagicMock(time=lambda: mtime + 120))

    mock_provider = AsyncMock()
    mock_provider.complete.side_effect = [
        Success(LLMResponse(content="Summary of report PDF.", model="test", usage={})),
        Success(
            LLMResponse(
                content='{"title": "Quarterly Report", "type": "report", "tags": ["test", "domain/alpha"]}',
                model="test",
                usage={},
            )
        ),
        Success(
            LLMResponse(
                content="Rich detailed summary of the quarterly report PDF document.",
                model="test",
                usage={},
            )
        ),
    ]
    monkeypatch.setattr(
        "pipelines.capture.get_provider", lambda task, config: mock_provider
    )

    result = await capture_file(binary_file, context=pipeline_ctx)

    assert isinstance(result, Success)
    assert isinstance(result.value, WriteOutcome)

    # The result vault_path is the sibling .md file
    sibling_vp = result.value.vault_path
    assert sibling_vp.startswith("Projects/Alpha/attachment/.summaries/")
    assert sibling_vp.endswith(".md")

    # Verify embedding row exists for sibling
    with get_connection(pipeline_ctx.db_path) as conn:
        emb_row = conn.execute(
            "SELECT vault_path FROM embeddings_vec WHERE vault_path = ?",
            (sibling_vp,),
        ).fetchone()
    assert emb_row is not None, f"embeddings_vec missing row for sibling {sibling_vp}"

    # Verify FTS5 row exists for sibling
    with get_connection(pipeline_ctx.db_path) as conn:
        fts_row = conn.execute(
            "SELECT vault_path FROM notes_fts WHERE vault_path = ?",
            (sibling_vp,),
        ).fetchone()
    assert fts_row is not None, f"notes_fts missing row for sibling {sibling_vp}"
