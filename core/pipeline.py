"""
core/pipeline.py
────────────────
Three exports used by every pipeline file in this project:

    PipelineContext  — carries config + correlation_id through a run
    Stage            — typing.Protocol for a single async pipeline step
    run_pipeline     — async orchestrator; chains stages, halts on first Failure

Usage:

    from core.pipeline import PipelineContext, Stage, run_pipeline

    async def my_stage(value: Any, ctx: PipelineContext) -> Result[Any]:
        ...
        return Success(transformed_value)

    result = await run_pipeline("my_pipeline", [stage_a, stage_b], initial_input)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from structlog.contextvars import bind_contextvars

from core.logging_setup import new_correlation_id
from core.result import Failure, Result, Success

if TYPE_CHECKING:
    from core.config import MainConfig
    from core.tags import TagTaxonomy

logger = structlog.get_logger(__name__)

__all__ = ["PipelineContext", "Stage", "run_pipeline"]


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class PipelineContext:
    """Carries shared state through one pipeline run.

    Attributes:
        config:         Validated project config. Tests may pass MagicMock().
        correlation_id: UUID stamped on every log line and audit entry in this run.
        db_path:        SQLite path override for tests. None → stages read from config.
        taxonomy:       Tag taxonomy for validation. None = skip validation.
    """

    config: "MainConfig"
    correlation_id: str
    db_path: Path | None = field(default=None)
    taxonomy: "TagTaxonomy | None" = field(default=None)


# ---------------------------------------------------------------------------
# Stage protocol
# ---------------------------------------------------------------------------


class Stage(Protocol):
    """A single async pipeline step.

    Stages receive the current value and the run context; they return a Result.
    A stage that raises instead of returning Failure is caught by run_pipeline.
    """

    async def __call__(self, input: Any, context: PipelineContext) -> Result[Any]:
        ...


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_pipeline(
    name: str,
    stages: list[Stage],
    initial_input: Any,
    context: PipelineContext | None = None,
) -> Result[Any]:
    """Chain stages sequentially, halting on the first Failure.

    Args:
        name:          Human-readable pipeline name (appears in every log line).
        stages:        Ordered list of async callables matching the Stage protocol.
        initial_input: Value passed to the first stage.
        context:       If None, a fresh PipelineContext is built using CONFIG and a
                       new correlation_id. If provided (e.g. from tests), its
                       correlation_id is bound into structlog contextvars without
                       clearing the existing context.

    Returns:
        Success(final_value) if all stages complete, or the first Failure returned
        (or caused by an uncaught exception) in any stage.
    """
    if context is None:
        from core.config import CONFIG

        cid = new_correlation_id()
        context = PipelineContext(config=CONFIG.main, correlation_id=cid)
    else:
        bind_contextvars(correlation_id=context.correlation_id)

    current_value: Any = initial_input

    for stage in stages:
        stage_name = getattr(stage, "__name__", repr(stage))
        logger.debug("stage_start", pipeline=name, stage=stage_name)

        try:
            result = await stage(current_value, context)
        except Exception as exc:
            logger.error(
                "stage_exception",
                pipeline=name,
                stage=stage_name,
                error=str(exc),
            )
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"pipeline": name, "stage": stage_name},
            )

        match result:
            case Success(value=v):
                current_value = v
                logger.debug("stage_ok", pipeline=name, stage=stage_name)
            case Failure() as f:
                logger.error(
                    "stage_failed",
                    pipeline=name,
                    stage=stage_name,
                    **f.to_log_dict(),
                )
                return f

    return Success(current_value)
