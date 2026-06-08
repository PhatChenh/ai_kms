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
async def test_classify_step_inbox_auto_project(
    vault_root: Path, pipeline_ctx, monkeypatch
):
    """Loose inbox .md, AI returns project=Alpha, confidence=0.9.

    Verify: ai_project set, domain tags include domain/finance, the file is
    actually moved to Projects/Alpha/, and exactly one AUTO audit row is
    written (only after the move + DB swap succeed).
    """
    from pipelines.capture import classify_step

    _setup_project_dirs(vault_root)
    note_path = vault_root / "inbox" / "note.md"
    _write_test_note(note_path, "## Hello\n\nBody.")

    classify_mock = _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.9)),
    )
    audit_mock = _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)
    ctx = pipeline_ctx

    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value
    assert updated_mr.ai_project == "Alpha"
    assert "domain/finance" in updated_mr.ai_tags
    classify_mock.assert_called_once()

    # File physically moved to the project root.
    assert (vault_root / "Projects" / "Alpha" / "note.md").exists()
    assert not note_path.exists()

    # Exactly one AUTO audit row, written only after the move succeeded.
    audit_mock.assert_called_once()
    call_kwargs = audit_mock.call_args.kwargs
    assert call_kwargs["outcome"] == "AUTO"
    assert call_kwargs["stage"] == "classify"
    assert call_kwargs["pipeline"] == "capture"


# ---------------------------------------------------------------------------
# Test 5: inbox AUTO domain only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_inbox_auto_domain_only(
    vault_root: Path, pipeline_ctx, monkeypatch
):
    """AI returns project=None, primary_domain=Finance, confidence=0.9 -> AUTO domain."""
    from pipelines.capture import classify_step

    _setup_domain_dirs(vault_root)
    note_path = vault_root / "inbox" / "note.md"
    _write_test_note(note_path, "## Hello\n\nBody.")

    _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project=None, confidence=0.9)),
    )
    audit_mock = _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)
    ctx = pipeline_ctx

    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value
    assert updated_mr.ai_project is None
    assert updated_mr.ai_domain == "Finance"
    assert "domain/finance" in updated_mr.ai_tags

    # File physically moved to the domain root.
    assert (vault_root / "Domain" / "Finance" / "note.md").exists()
    assert not note_path.exists()

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
async def test_classify_step_project_beats_domain(
    vault_root: Path, pipeline_ctx, monkeypatch
):
    """AI returns project=Alpha, primary_domain=Finance -> ai_project set, domain tags added.

    P2-CIC-11: project assignment takes priority over domain routing.
    """
    from pipelines.capture import classify_step

    _setup_project_dirs(vault_root)
    _setup_domain_dirs(vault_root)
    note_path = vault_root / "inbox" / "note.md"
    _write_test_note(note_path, "## Hello\n\nBody.")

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
    _patch_core_config(monkeypatch, vault_root)
    ctx = pipeline_ctx

    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value
    assert updated_mr.ai_project == "Alpha"
    assert "domain/finance" in updated_mr.ai_tags

    # Project wins over domain: file goes to Projects/Alpha/, not Domain/Finance/.
    assert (vault_root / "Projects" / "Alpha" / "note.md").exists()
    assert not (vault_root / "Domain" / "Finance" / "note.md").exists()

    audit_mock.assert_called_once()
    assert audit_mock.call_args.kwargs["outcome"] == "AUTO"


# ---------------------------------------------------------------------------
# Test 10: multi-domain primary routing (P2-CIC-12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_step_multi_domain_primary_routing(
    vault_root: Path, pipeline_ctx, monkeypatch
):
    """AI returns project=None, domains=["finance","legal"], primary_domain=Finance.

    P2-CIC-12: destination is Domain/Finance/, all domain tags kept.
    """
    from pipelines.capture import classify_step

    _setup_domain_dirs(vault_root)
    note_path = vault_root / "inbox" / "note.md"
    _write_test_note(note_path, "## Hello\n\nBody.")

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
    _patch_core_config(monkeypatch, vault_root)
    ctx = pipeline_ctx

    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value
    assert updated_mr.ai_project is None
    assert updated_mr.ai_domain == "Finance"
    assert "domain/finance" in updated_mr.ai_tags
    assert "domain/legal" in updated_mr.ai_tags

    # Primary domain drives routing: file goes to Domain/Finance/.
    assert (vault_root / "Domain" / "Finance" / "note.md").exists()
    assert not note_path.exists()

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


# ---------------------------------------------------------------------------
# Phase 6 — AUTO move handoff
# ---------------------------------------------------------------------------


def _write_test_note(path: Path, body: str, actor: str = "ai") -> None:
    """Write a real .md note file on disk via vault/writer.py."""
    from vault.writer import write_note

    result = write_note(path, body, NoteMetadata(), actor=actor)  # type: ignore[arg-type]
    assert isinstance(result, Success), f"write_note failed: {result}"


def _setup_project_dirs(vault_root: Path) -> Path:
    """Create Projects/Alpha/ directory, return the project path."""
    p = vault_root / "Projects" / "Alpha"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _setup_domain_dirs(vault_root: Path) -> Path:
    """Create Domain/Finance/ directory, return the domain path."""
    d = vault_root / "Domain" / "Finance"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.mark.asyncio
async def test_auto_md_moves_to_project_folder(
    vault_root: Path, pipeline_ctx, monkeypatch
):
    """Loose inbox .md, AUTO with project=Alpha.

    Verify: file physically exists at Projects/Alpha/<name>.md, not at inbox
    path.  documents.vault_path is the new path.  No orphan row at old path.
    """
    from pipelines.capture import classify_step
    from storage.documents import get_by_path

    _setup_project_dirs(vault_root)
    note_path = vault_root / "inbox" / "my-note.md"
    _write_test_note(note_path, "## Hello\n\nTest body.")

    _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.95)),
    )
    _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)
    ctx = pipeline_ctx

    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value

    # File moved to project root.
    expected_dst = vault_root / "Projects" / "Alpha" / "my-note.md"
    assert expected_dst.exists(), f"Expected {expected_dst} to exist"
    assert not note_path.exists(), "Original inbox file must be gone"

    # MetadataResult source_path updated.
    assert updated_mr.raw.source_path == expected_dst

    # DB: new path exists, old path gone.
    new_row = get_by_path("Projects/Alpha/my-note.md", db_path=ctx.db_path)
    assert isinstance(new_row, Success)
    assert new_row.value is not None, "New row must exist"

    old_row = get_by_path("inbox/my-note.md", db_path=ctx.db_path)
    assert isinstance(old_row, Success)
    assert old_row.value is None, "Old row must be gone"


@pytest.mark.asyncio
async def test_auto_md_moves_to_domain_folder(
    vault_root: Path, pipeline_ctx, monkeypatch
):
    """Loose inbox .md, AUTO with project=None, primary_domain=Finance.

    Verify: file at Domain/Finance/<name>.md.
    """
    from pipelines.capture import classify_step
    from storage.documents import get_by_path

    _setup_domain_dirs(vault_root)
    note_path = vault_root / "inbox" / "domain-note.md"
    _write_test_note(note_path, "## Domain note\n\nBody.")

    _mock_classify(
        monkeypatch,
        Success(
            _make_classify_result(
                project=None, primary_domain="Finance", confidence=0.95
            )
        ),
    )
    _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)
    ctx = pipeline_ctx

    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value

    expected_dst = vault_root / "Domain" / "Finance" / "domain-note.md"
    assert expected_dst.exists(), f"Expected {expected_dst} to exist"
    assert not note_path.exists()

    assert updated_mr.raw.source_path == expected_dst

    new_row = get_by_path("Domain/Finance/domain-note.md", db_path=ctx.db_path)
    assert isinstance(new_row, Success)
    assert new_row.value is not None


@pytest.mark.asyncio
async def test_auto_md_move_guard_registered(
    vault_root: Path, pipeline_ctx, monkeypatch
):
    """After an AUTO .md move, verify move_guard.register was called with the
    destination path before the move.
    """
    from pipelines.capture import classify_step
    from vault.move_guard import MoveGuard, set_active

    _setup_project_dirs(vault_root)
    note_path = vault_root / "inbox" / "guard-test.md"
    _write_test_note(note_path, "## Guard test\n\nBody.")

    # Install a real MoveGuard so classify_step can register the destination.
    guard = MoveGuard()
    set_active(guard)

    _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.95)),
    )
    _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)
    ctx = pipeline_ctx

    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    expected_dst = vault_root / "Projects" / "Alpha" / "guard-test.md"
    assert expected_dst.exists()

    # Verify move_guard registered the destination.
    # check_and_consume removes the entry on first match — it should still be
    # there since no watcher consumed it.
    registered = guard.check_and_consume(expected_dst)
    assert registered, "move_guard must have registered the destination path"

    set_active(None)  # cleanup


@pytest.mark.asyncio
async def test_auto_md_replace_path_atomic(vault_root: Path, pipeline_ctx, monkeypatch):
    """After the move, verify exactly 1 documents row exists for the note
    (no duplicate at old + new path).
    """
    from pipelines.capture import classify_step
    from storage.documents import get_by_path

    _setup_project_dirs(vault_root)
    note_path = vault_root / "inbox" / "atomic-test.md"
    _write_test_note(note_path, "## Atomic\n\nBody.")

    _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.95)),
    )
    _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)
    ctx = pipeline_ctx

    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)

    # Only 1 row: new path exists, old path does not.
    new_row = get_by_path("Projects/Alpha/atomic-test.md", db_path=ctx.db_path)
    assert isinstance(new_row, Success)
    assert new_row.value is not None

    old_row = get_by_path("inbox/atomic-test.md", db_path=ctx.db_path)
    assert isinstance(old_row, Success)
    assert old_row.value is None


@pytest.mark.asyncio
async def test_auto_binary_routes_through_store_nonmd(
    vault_root: Path, pipeline_ctx, monkeypatch
):
    """Loose inbox PDF, AUTO with project=Alpha.

    Verify classify_step sets target_type/target_name on MetadataResult.
    Then verify store() passes them through to _store_nonmd.
    """
    from pipelines.capture import classify_step
    from unittest.mock import patch

    _setup_project_dirs(vault_root)
    # No real binary needed — classify_step only checks mr.raw.is_md.
    note_path = vault_root / "inbox" / "report.pdf"

    _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.95)),
    )
    _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)
    ctx = pipeline_ctx

    # Step 1: classify_step sets target_type/target_name.
    mr = _make_mr(note_path, is_md=False)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value
    assert updated_mr.target_type == "project"
    assert updated_mr.target_name == "Alpha"
    assert updated_mr.ai_project == "Alpha"

    # Step 2: Verify store() passes target_type/target_name to _store_nonmd.
    async def _fake_store_nonmd(mr, note_meta, ctx, target_type=None, target_name=None):
        return Success(
            MagicMock(
                vault_path="Projects/Alpha/attachment/.summaries/report.pdf.md",
                absolute_path=MagicMock(),
                content_hash="abc",
                metadata=MagicMock(),
            )
        )

    with patch(
        "pipelines.capture._store_nonmd", side_effect=_fake_store_nonmd
    ) as mock_sn:
        from pipelines.capture import store

        store_result = await store(updated_mr, ctx)
        assert isinstance(store_result, Success)
        mock_sn.assert_called_once()
        call_kw = mock_sn.call_args.kwargs
        assert call_kw["target_type"] == "project"
        assert call_kw["target_name"] == "Alpha"


@pytest.mark.asyncio
async def test_auto_binary_end_to_end_placement(
    vault_root: Path, pipeline_ctx, monkeypatch
):
    """Real loose binary AUTO: classify_step → store → _store_nonmd places it.

    Drives a real PDF through classify_step (sets the AUTO target) then the
    real store() → _store_nonmd with NO mocks on the filer.  Asserts on-disk
    placement + the sibling DB row.  Closes the P2-CIC-05 gap left by the
    param-passthrough-only test (#3) — only the LLM summarize call is stubbed.
    """
    from llm.provider import LLMResponse
    from pipelines.capture import classify_step, store
    from storage.documents import get_by_path
    from vault.reader import read_note

    import shutil

    _setup_project_dirs(vault_root)
    binary_path = vault_root / "inbox" / "report.pdf"
    fixture_pdf = Path(__file__).parent.parent / "fixtures" / "sample_text.pdf"
    shutil.copy(fixture_pdf, binary_path)

    _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.95)),
    )
    _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)

    # Stub only the LLM (summarize_attachment) — the filer runs for real.
    class _StubProvider:
        async def complete(self, system, user):
            return Success(
                LLMResponse(content="Rich attachment summary.", model="test", usage={})
            )

    monkeypatch.setattr(
        "pipelines.capture.get_provider", lambda *a, **kw: _StubProvider()
    )

    ctx = pipeline_ctx
    mr = _make_mr(binary_path, is_md=False)

    # Stage 5.5: classify sets the AUTO target.
    classified = await classify_step(mr, ctx)
    assert isinstance(classified, Success)
    assert classified.value.target_type == "project"
    assert classified.value.target_name == "Alpha"

    # Stage 6: real store → real _store_nonmd places the binary.
    stored = await store(classified.value, ctx)
    assert isinstance(stored, Success), f"store failed: {stored}"

    # Binary physically lands in the project attachment dir; inbox copy gone.
    expected_binary = vault_root / "Projects" / "Alpha" / "attachment" / "report.pdf"
    assert expected_binary.exists(), f"Expected binary at {expected_binary}"
    assert not binary_path.exists(), "Binary must not remain in inbox"

    # Sibling .md lands in attachment/.summaries/ named <full filename>.md.
    expected_sibling = (
        vault_root
        / "Projects"
        / "Alpha"
        / "attachment"
        / ".summaries"
        / "report.pdf.md"
    )
    assert expected_sibling.exists(), f"Expected sibling at {expected_sibling}"

    # DB row is keyed on the sibling vault_path.
    sibling_vault_path = "Projects/Alpha/attachment/.summaries/report.pdf.md"
    row = get_by_path(sibling_vault_path, db_path=ctx.db_path)
    assert isinstance(row, Success)
    assert row.value is not None, "Sibling DB row must exist"

    # Sibling frontmatter: attachment_path → the binary, type preserved.
    match read_note(expected_sibling):
        case Success(note):
            assert (
                note.metadata.attachment_path == "Projects/Alpha/attachment/report.pdf"
            )
            assert note.metadata.type == "attachment-summary"
        case _:
            raise AssertionError("sibling note must be readable")


@pytest.mark.asyncio
async def test_auto_binary_batch_id_null(vault_root: Path, pipeline_ctx, monkeypatch):
    """After an AUTO move to project root, verify batch_id on the documents
    row is NULL.  (P2-CIC-07 / R3)

    For md files, classify_step calls replace_path(batch_id=None).
    Verify the DB row has NULL batch_id after the AUTO move.
    """
    from pipelines.capture import classify_step
    from storage.documents import get_by_path

    _setup_project_dirs(vault_root)
    note_path = vault_root / "inbox" / "batchless-md.md"
    _write_test_note(note_path, "## Batchless\n\nBody.")

    _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.95)),
    )
    _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)
    ctx = pipeline_ctx

    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)

    # Verify DB row has NULL batch_id.
    new_row = get_by_path("Projects/Alpha/batchless-md.md", db_path=ctx.db_path)
    assert isinstance(new_row, Success)
    assert new_row.value is not None
    assert new_row.value.batch_id is None, (
        f"Expected batch_id=NULL, got {new_row.value.batch_id}"
    )


@pytest.mark.asyncio
async def test_auto_human_locked_fallback(vault_root: Path, pipeline_ctx, monkeypatch):
    """Loose inbox .md with updated_by_human=true.

    AUTO triggered but move_note returns Failure(recoverable=False).
    Verify: file stays in inbox, candidate fields written (SUGGEST-style),
    no crash.  (R5)
    """
    from pipelines.capture import classify_step
    from storage.documents import get_by_path

    _setup_project_dirs(vault_root)
    note_path = vault_root / "inbox" / "locked-note.md"
    # Write with actor="human" so updated_by_human=true is set on disk.
    _write_test_note(note_path, "## Locked\n\nBody.", actor="human")

    _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.95)),
    )
    audit_mock = _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)

    # Mock write_note for candidate fallback (SUGGEST-style).
    write_note_mock = _mock_write_note(monkeypatch)

    ctx = pipeline_ctx
    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    # File must stay in inbox — move was blocked by human lock.
    assert note_path.exists(), "Locked file must stay in inbox"
    expected_dst = vault_root / "Projects" / "Alpha" / "locked-note.md"
    assert not expected_dst.exists(), "Locked file must NOT be moved"

    # Candidate fields written (SUGGEST-style fallback).
    write_note_mock.assert_called_once()
    written_meta = write_note_mock.call_args.args[2]
    assert written_meta.status == "needs-review"
    assert written_meta.suggested_project == "Alpha"
    assert written_meta.suggested_primary_domain == "Finance"
    # Reasoning includes the block reason.
    assert "blocked" in written_meta.classify_reasoning.lower()

    # Audit must record SUGGEST, NOT AUTO — the file stayed in inbox, so an
    # AUTO row would make the briefing report it as auto-filed (the #1 fix).
    audit_mock.assert_called_once()
    assert audit_mock.call_args.kwargs["outcome"] == "SUGGEST"

    # No DB row at destination.
    new_row = get_by_path("Projects/Alpha/locked-note.md", db_path=ctx.db_path)
    assert isinstance(new_row, Success)
    assert new_row.value is None


@pytest.mark.asyncio
async def test_auto_md_db_error_writes_suggest_audit(
    vault_root: Path, pipeline_ctx, monkeypatch
):
    """AUTO .md move succeeds on disk but documents.replace_path fails.

    Verify: the move is rolled back (file back in inbox), candidate fields
    written, and the audit records SUGGEST — never AUTO. (#1 fix)
    """
    from pipelines.capture import classify_step
    from storage.documents import get_by_path

    _setup_project_dirs(vault_root)
    note_path = vault_root / "inbox" / "db-error-note.md"
    _write_test_note(note_path, "## DB error\n\nBody.")

    _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.95)),
    )
    audit_mock = _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)

    # Force the DB swap to fail so the AUTO move rolls back.
    monkeypatch.setattr(
        "pipelines.capture.documents.replace_path",
        lambda *a, **kw: Failure(error="boom", recoverable=True, context={}),
    )

    ctx = pipeline_ctx
    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)

    # Move rolled back — file is back in inbox, not at the destination.
    assert note_path.exists(), "File must be rolled back to inbox on DB failure"
    assert not (vault_root / "Projects" / "Alpha" / "db-error-note.md").exists()

    # Audit must record SUGGEST, not AUTO.
    audit_mock.assert_called_once()
    assert audit_mock.call_args.kwargs["outcome"] == "SUGGEST"

    # No DB row at destination.
    new_row = get_by_path("Projects/Alpha/db-error-note.md", db_path=ctx.db_path)
    assert isinstance(new_row, Success)
    assert new_row.value is None


@pytest.mark.asyncio
async def test_auto_md_metadata_result_updated(
    vault_root: Path, pipeline_ctx, monkeypatch
):
    """After an AUTO .md move, verify the MetadataResult returned has
    raw.source_path pointing to the new location (via dataclasses.replace).
    """
    from pipelines.capture import classify_step

    _setup_project_dirs(vault_root)
    note_path = vault_root / "inbox" / "mr-update-test.md"
    _write_test_note(note_path, "## MR Update\n\nBody.")

    _mock_classify(
        monkeypatch,
        Success(_make_classify_result(project="Alpha", confidence=0.95)),
    )
    _mock_audit_write(monkeypatch)
    _patch_core_config(monkeypatch, vault_root)
    ctx = pipeline_ctx

    mr = _make_mr(note_path)
    result = await classify_step(mr, ctx)

    assert isinstance(result, Success)
    updated_mr = result.value

    # raw.source_path must point to the new location.
    expected_dst = vault_root / "Projects" / "Alpha" / "mr-update-test.md"
    assert updated_mr.raw.source_path == expected_dst

    # The file must exist at the new path.
    assert expected_dst.exists()

    # ai_project must be stamped.
    assert updated_mr.ai_project == "Alpha"

    # source_path in raw is a Path object (not a string).
    assert isinstance(updated_mr.raw.source_path, Path)
