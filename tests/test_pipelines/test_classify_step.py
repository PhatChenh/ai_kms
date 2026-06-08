"""Phase 5 — classify_step pipeline stage tests.

All tests stub the AI provider — no real LLM calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.confidence import AIDecision
from core.pipeline import PipelineContext
from core.result import Failure, Success
from handlers.base import RawContent
from pipelines.capture import MetadataResult
from pipelines.classify import ClassifyResult
from vault.frontmatter import NoteMetadata
from vault.reader import Note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mr(
    source_path: Path,
    is_md: bool = True,
    summary: str = "Test summary",
    ai_title: str = "Test Note",
    ai_type: str | None = None,
    ai_domain: str | None = None,
    ai_tags: list[str] | None = None,
    ai_project: str | None = None,
) -> MetadataResult:
    raw = RawContent(text="test content", source_path=source_path, is_md=is_md)
    return MetadataResult(
        raw=raw,
        summary=summary,
        ai_title=ai_title,
        ai_type=ai_type,
        ai_domain=ai_domain,
        ai_tags=ai_tags if ai_tags is not None else [],
        decision=AIDecision(action="test", confidence=0.9, reasoning="test"),
        ai_project=ai_project,
    )


def _make_config_mock(vault_root: Path) -> MagicMock:
    """Minimal config mock with a vault pointing at vault_root."""
    from core.config import VaultConfig

    cfg = MagicMock()
    cfg.vault = VaultConfig(root=vault_root)
    return cfg


def _patch_core_config(monkeypatch, vault_root: Path):
    """Patch core.config._CONFIG so to_vault_path + thresholds work with test paths."""
    import core.config as cfg_module

    from core.config import Thresholds, VaultConfig

    fake_vault = VaultConfig(root=vault_root)
    fake_main = MagicMock()
    fake_main.vault = fake_vault

    fake_full = MagicMock()
    fake_full.main = fake_main
    fake_full.thresholds = Thresholds()  # auto=0.85, suggest=0.60

    monkeypatch.setattr(cfg_module, "_CONFIG", fake_full)


def _make_classify_result(
    project: str | None = "Alpha",
    domains: list[str] | None = None,
    primary_domain: str | None = "Finance",
    confidence: float = 0.9,
    reasoning: str = "Test reasoning.",
) -> ClassifyResult:
    return ClassifyResult(
        project=project,
        domains=domains if domains is not None else ["finance"],
        primary_domain=primary_domain,
        confidence=confidence,
        reasoning=reasoning,
    )


def _make_read_note_stub(monkeypatch, metadata: NoteMetadata | None = None):
    """Patch read_note in capture module to return a Note with given metadata."""
    meta = metadata or NoteMetadata()
    note = Note(
        path=Path("dummy"),
        metadata=meta,
        content="test body",
        content_hash="abc123",
    )
    monkeypatch.setattr(
        "pipelines.capture.read_note",
        lambda path: Success(note),
    )


def _mock_classify(monkeypatch, return_value):
    """Stub classify() in capture namespace to return `return_value`."""
    mock = AsyncMock(return_value=return_value)
    monkeypatch.setattr("pipelines.capture.classify", mock)
    return mock


def _mock_audit_write(monkeypatch):
    """Stub audit.write in capture namespace; returns the mock for assertions."""
    mock = MagicMock(return_value=Success(1))
    monkeypatch.setattr("pipelines.capture.audit.write", mock)
    return mock


def _mock_write_note(monkeypatch):
    """Stub write_note in capture namespace; returns the mock for assertions."""
    from vault.writer import WriteOutcome

    outcome = WriteOutcome(
        vault_path="test/path.md",
        absolute_path=Path("/tmp/test.md"),
        content_hash="abc123",
        metadata=NoteMetadata(),
    )
    mock = MagicMock(return_value=Success(outcome))
    monkeypatch.setattr("pipelines.capture.write_note", mock)
    return mock


# ---------------------------------------------------------------------------
# Test 1: SUPPRESS passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_suppress_passthrough(tmp_path: Path, monkeypatch):
    """ctx.skip_classify=True -> return unchanged, no AI call, no audit row."""
    mr = _make_mr(tmp_path / "inbox" / "note.md")
    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
        skip_classify=True,
    )

    classify_mock = _mock_classify(monkeypatch, MagicMock())
    audit_mock = _mock_audit_write(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    assert result.value is mr
    classify_mock.assert_not_called()
    audit_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: LOCATED project passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_located_project_passthrough(tmp_path: Path, monkeypatch):
    """File in Projects/Alpha/ -> return unchanged, no AI call."""
    projects_dir = tmp_path / "Projects" / "Alpha"
    projects_dir.mkdir(parents=True)
    note_path = projects_dir / "note.md"

    mr = _make_mr(note_path)
    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
    )

    classify_mock = _mock_classify(monkeypatch, MagicMock())
    audit_mock = _mock_audit_write(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    assert result.value is mr
    classify_mock.assert_not_called()
    audit_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: LOCATED domain passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_located_domain_passthrough(tmp_path: Path, monkeypatch):
    """File in Domain/Finance/ -> return unchanged, no AI call."""
    domain_dir = tmp_path / "Domain" / "Finance"
    domain_dir.mkdir(parents=True)
    note_path = domain_dir / "note.md"

    mr = _make_mr(note_path)
    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
    )

    classify_mock = _mock_classify(monkeypatch, MagicMock())
    audit_mock = _mock_audit_write(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    assert result.value is mr
    classify_mock.assert_not_called()
    audit_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: inbox AUTO project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_inbox_auto_project(tmp_path: Path, monkeypatch):
    """Loose inbox .md, AI returns project=Alpha, confidence=0.9.

    Verify: ai_project set, domain tags include domain/finance, no
    suggested_* fields, one AUTO audit row.
    """
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    note_path = tmp_path / "inbox" / "note.md"
    mr = _make_mr(note_path)

    _patch_core_config(monkeypatch, tmp_path)

    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
    )
    ctx.db_path = tmp_path / "test.db"

    classify_mock = _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.9)),
    )
    audit_mock = _mock_audit_write(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value
    assert updated_mr.ai_project == "Alpha"
    assert "domain/finance" in updated_mr.ai_tags
    classify_mock.assert_called_once()

    # Verify one AUTO audit row
    audit_mock.assert_called_once()
    call_kwargs = audit_mock.call_args.kwargs
    assert call_kwargs["outcome"] == "AUTO"
    assert call_kwargs["stage"] == "classify"
    assert call_kwargs["pipeline"] == "capture"


# ---------------------------------------------------------------------------
# Test 5: inbox AUTO domain only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_inbox_auto_domain_only(tmp_path: Path, monkeypatch):
    """AI returns project=None, primary_domain=Finance, confidence=0.9 -> AUTO domain."""
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    note_path = tmp_path / "inbox" / "note.md"
    mr = _make_mr(note_path)

    _patch_core_config(monkeypatch, tmp_path)

    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
    )
    ctx.db_path = tmp_path / "test.db"

    _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project=None, confidence=0.9)),
    )
    audit_mock = _mock_audit_write(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value
    assert updated_mr.ai_project is None
    assert updated_mr.ai_domain == "Finance"
    assert "domain/finance" in updated_mr.ai_tags

    audit_mock.assert_called_once()
    assert audit_mock.call_args.kwargs["outcome"] == "AUTO"


# ---------------------------------------------------------------------------
# Test 6: inbox SUGGEST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_inbox_suggest(tmp_path: Path, monkeypatch):
    """AI returns confidence=0.7 (SUGGEST band) -> candidate fields written."""
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    note_path = tmp_path / "inbox" / "note.md"
    mr = _make_mr(note_path)

    _patch_core_config(monkeypatch, tmp_path)

    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
    )
    ctx.db_path = tmp_path / "test.db"

    _mock_classify(
        monkeypatch,
        Success(
            _make_classify_result(
                project="Alpha", confidence=0.7, reasoning="Plausible match."
            )
        ),
    )
    audit_mock = _mock_audit_write(monkeypatch)
    _make_read_note_stub(monkeypatch)
    write_note_mock = _mock_write_note(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    # Returns original mr (unchanged — no ai_project stamp)
    assert result.value is mr

    # Verify candidate fields written via write_note
    write_note_mock.assert_called_once()
    written_meta = write_note_mock.call_args.args[2]
    assert written_meta.status == "needs-review"
    assert written_meta.suggested_project == "Alpha"
    assert written_meta.suggested_primary_domain == "Finance"
    assert written_meta.classify_confidence == 0.7
    assert written_meta.classify_reasoning == "Plausible match."

    # Verify SUGGEST audit row
    audit_mock.assert_called_once()
    assert audit_mock.call_args.kwargs["outcome"] == "SUGGEST"


# ---------------------------------------------------------------------------
# Test 7: inbox CLUELESS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_inbox_clueless(tmp_path: Path, monkeypatch):
    """AI returns confidence=0.4 (CLUELESS band) -> candidate fields with null project/domain."""
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    note_path = tmp_path / "inbox" / "note.md"
    mr = _make_mr(note_path)

    _patch_core_config(monkeypatch, tmp_path)

    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
    )
    ctx.db_path = tmp_path / "test.db"

    _mock_classify(
        monkeypatch,
        Success(
            _make_classify_result(
                project=None,
                primary_domain=None,
                domains=["finance"],
                confidence=0.4,
                reasoning="Too vague to classify.",
            )
        ),
    )
    audit_mock = _mock_audit_write(monkeypatch)
    _make_read_note_stub(monkeypatch)
    write_note_mock = _mock_write_note(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    assert result.value is mr

    write_note_mock.assert_called_once()
    written_meta = write_note_mock.call_args.args[2]
    assert written_meta.status == "needs-review"
    assert written_meta.suggested_project is None
    assert written_meta.suggested_primary_domain is None
    assert written_meta.classify_confidence == 0.4
    # OQ-CIC-4 appends suffix when project + primary_domain are both None
    assert "Too vague to classify." in written_meta.classify_reasoning
    assert "no project or domain identified" in written_meta.classify_reasoning

    audit_mock.assert_called_once()
    assert audit_mock.call_args.kwargs["outcome"] == "CLUELESS"


# ---------------------------------------------------------------------------
# Test 8: no project no domain is CLUELESS regardless of confidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_no_project_no_domain_is_clueless(
    tmp_path: Path, monkeypatch
):
    """AI returns project=None, domains=[], primary_domain=None, confidence=0.95.

    Locked OQ-CIC-4: treated as CLUELESS regardless of confidence.
    """
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    note_path = tmp_path / "inbox" / "note.md"
    mr = _make_mr(note_path)

    _patch_core_config(monkeypatch, tmp_path)

    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
    )
    ctx.db_path = tmp_path / "test.db"

    _mock_classify(
        monkeypatch,
        Success(
            _make_classify_result(
                project=None,
                domains=[],
                primary_domain=None,
                confidence=0.95,
                reasoning="Cannot determine.",
            )
        ),
    )
    audit_mock = _mock_audit_write(monkeypatch)
    _make_read_note_stub(monkeypatch)
    write_note_mock = _mock_write_note(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    # Even with high confidence, OQ-CIC-4 forces CLUELESS
    audit_mock.assert_called_once()
    assert audit_mock.call_args.kwargs["outcome"] == "CLUELESS"

    write_note_mock.assert_called_once()
    written_meta = write_note_mock.call_args.args[2]
    assert written_meta.status == "needs-review"
    assert written_meta.suggested_project is None
    assert written_meta.suggested_primary_domain is None


# ---------------------------------------------------------------------------
# Test 9: project beats domain (P2-CIC-11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_project_beats_domain(tmp_path: Path, monkeypatch):
    """AI returns project=Alpha, primary_domain=Finance -> ai_project set, domain tags added.

    P2-CIC-11: project assignment takes priority over domain routing.
    """
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    note_path = tmp_path / "inbox" / "note.md"
    mr = _make_mr(note_path)

    _patch_core_config(monkeypatch, tmp_path)

    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
    )
    ctx.db_path = tmp_path / "test.db"

    _mock_classify(
        monkeypatch,
        Success(
            _make_classify_result(
                project="Alpha",
                domains=["finance"],
                primary_domain="Finance",
                confidence=0.9,
            )
        ),
    )
    audit_mock = _mock_audit_write(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value
    assert updated_mr.ai_project == "Alpha"
    assert "domain/finance" in updated_mr.ai_tags

    audit_mock.assert_called_once()
    assert audit_mock.call_args.kwargs["outcome"] == "AUTO"


# ---------------------------------------------------------------------------
# Test 10: multi-domain primary routing (P2-CIC-12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_multi_domain_primary_routing(tmp_path: Path, monkeypatch):
    """AI returns project=None, domains=["finance","legal"], primary_domain=Finance.

    P2-CIC-12: destination is Domain/Finance/, all domain tags kept.
    """
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    note_path = tmp_path / "inbox" / "note.md"
    mr = _make_mr(note_path)

    _patch_core_config(monkeypatch, tmp_path)

    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
    )
    ctx.db_path = tmp_path / "test.db"

    _mock_classify(
        monkeypatch,
        Success(
            _make_classify_result(
                project=None,
                domains=["finance", "legal"],
                primary_domain="Finance",
                confidence=0.9,
            )
        ),
    )
    audit_mock = _mock_audit_write(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value
    assert updated_mr.ai_project is None
    assert updated_mr.ai_domain == "Finance"
    assert "domain/finance" in updated_mr.ai_tags
    assert "domain/legal" in updated_mr.ai_tags

    audit_mock.assert_called_once()
    assert audit_mock.call_args.kwargs["outcome"] == "AUTO"


# ---------------------------------------------------------------------------
# Test 11: retry then CLUELESS (OQ-CIC-B / TD-048)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_retry_then_clueless(tmp_path: Path, monkeypatch):
    """Stub classify() to fail recoverable=True 3 times -> CLUELESS fallback."""
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    note_path = tmp_path / "inbox" / "note.md"
    mr = _make_mr(note_path)

    _patch_core_config(monkeypatch, tmp_path)

    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
    )
    ctx.db_path = tmp_path / "test.db"

    classify_mock = AsyncMock(
        return_value=Failure(error="Transient error", recoverable=True, context={})
    )
    monkeypatch.setattr("pipelines.capture.classify", classify_mock)

    audit_mock = _mock_audit_write(monkeypatch)
    _make_read_note_stub(monkeypatch)
    write_note_mock = _mock_write_note(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    # Should have tried 3 times
    assert classify_mock.call_count == 3

    # Falls back to CLUELESS
    audit_mock.assert_called_once()
    assert audit_mock.call_args.kwargs["outcome"] == "CLUELESS"

    # Reasoning mentions failed attempts (decision is first positional arg to audit.write)
    audit_decision = audit_mock.call_args[0][0]
    assert (
        "3" in audit_decision.reasoning
        or "retry" in audit_decision.reasoning.lower()
        or "failed" in audit_decision.reasoning.lower()
    )

    # Candidate fields written
    write_note_mock.assert_called_once()
    written_meta = write_note_mock.call_args.args[2]
    assert written_meta.status == "needs-review"


# ---------------------------------------------------------------------------
# Test 12: render error no retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_render_error_no_retry(tmp_path: Path, monkeypatch):
    """classify() returns Failure(recoverable=False) -> no retry, immediate CLUELESS."""
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    note_path = tmp_path / "inbox" / "note.md"
    mr = _make_mr(note_path)

    _patch_core_config(monkeypatch, tmp_path)

    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
    )
    ctx.db_path = tmp_path / "test.db"

    classify_mock = AsyncMock(
        return_value=Failure(error="Render error", recoverable=False, context={})
    )
    monkeypatch.setattr("pipelines.capture.classify", classify_mock)

    audit_mock = _mock_audit_write(monkeypatch)
    _make_read_note_stub(monkeypatch)
    _write_note_mock = _mock_write_note(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    # No retry — called exactly once
    assert classify_mock.call_count == 1

    audit_mock.assert_called_once()
    assert audit_mock.call_args.kwargs["outcome"] == "CLUELESS"


# ---------------------------------------------------------------------------
# Test 13: registry from context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_registry_from_context(tmp_path: Path, monkeypatch):
    """Pass ctx.registry = mock_live_registry -> get_groups() is called."""
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    note_path = tmp_path / "inbox" / "note.md"
    mr = _make_mr(note_path)

    _patch_core_config(monkeypatch, tmp_path)

    mock_registry = MagicMock()
    mock_registry.get_groups.return_value = {}

    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
        registry=mock_registry,
    )
    ctx.db_path = tmp_path / "test.db"

    classify_mock = _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.9)),
    )
    _audit_mock = _mock_audit_write(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    # LiveRegistry.get_groups() was called
    mock_registry.get_groups.assert_called_once()
    # classify was called with the destinations string
    classify_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Test 14: registry None fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_registry_none_fallback(tmp_path: Path, monkeypatch):
    """Pass ctx.registry=None -> build_registry(vault_cfg) is called as one-shot fallback."""
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Projects").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Domain").mkdir(parents=True, exist_ok=True)
    note_path = tmp_path / "inbox" / "note.md"
    mr = _make_mr(note_path)

    _patch_core_config(monkeypatch, tmp_path)

    ctx = PipelineContext(
        config=_make_config_mock(tmp_path),
        correlation_id="test-cid",
        registry=None,
    )
    ctx.db_path = tmp_path / "test.db"

    classify_mock = _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.9)),
    )
    _audit_mock = _mock_audit_write(monkeypatch)

    from pipelines.capture import classify_step

    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    # classify was called (which means destinations were built)
    classify_mock.assert_called_once()
    # Verify destinations argument includes expected structure
    call_args = classify_mock.call_args
    dest_arg = call_args[0][1]  # second positional arg = destinations string
    assert "Uncategorized" in dest_arg or len(dest_arg) > 0
