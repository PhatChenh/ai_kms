"""
Phase 1 import/contract tests for core/pipeline.py.
These three tests verify the module exports and async contract only.
"""
from __future__ import annotations

import asyncio
import inspect


def test_import():
    """PipelineContext, Stage, run_pipeline importable from core.pipeline."""
    from core.pipeline import PipelineContext, Stage, run_pipeline  # noqa: F401


def test_run_pipeline_is_async():
    """run_pipeline must be an async function."""
    from core.pipeline import run_pipeline

    assert asyncio.iscoroutinefunction(run_pipeline)


def test_pipeline_has_no_heavy_imports():
    """
    core/pipeline.py must not import llm/, vault/, handlers/, storage/, or core/audit.
    Verified by inspecting the module's source — cheap and dependency-free.
    """
    import importlib
    import importlib.util
    from pathlib import Path

    src = (Path(__file__).parents[2] / "core" / "pipeline.py").read_text()
    forbidden = ["llm.", "vault.", "handlers.", "storage.", "core.audit"]
    for mod in forbidden:
        assert mod not in src, f"core/pipeline.py must not import {mod!r}"
