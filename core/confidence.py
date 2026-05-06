from pydantic import BaseModel, Field, model_validator
from core.config import ConfidenceBand, RouteDecision as RoutingOutcome
import structlog

logger = structlog.get_logger(__name__)

__all__ = ["AIDecision", "RoutingOutcome", "route"]


class AIDecision(BaseModel):
    """
    The standard envelope produced by every AI pipeline stage.
 
    Every field is mandatory: if you cannot populate ``reasoning`` or
    ``source_ids``, the stage is not ready for production. Stubs break the
    audit trail.
 
    Fields
    ------
    action : str
        A human-readable description of what the AI decided to do.
        Convention: ``"verb:target"`` (e.g. ``"classify:Projects/Movies"``).
    confidence : float
        A score in [0.0, 1.0]. Calibrated so that 0.85 means "very likely
        correct" and 0.60 means "plausible but uncertain".
    reasoning : str
        The AI's explanation for the decision. Feeds directly into the audit
        log — it is the only way a human reviewer can evaluate a SUGGEST or
        CLUELESS outcome without re-running the model.
    source_ids : list[str]
        Vault-relative paths (or external IDs) of the notes/documents that
        informed the decision. Required for "show your work" traceability.
        May be an empty list only for decisions that have no source material
        (e.g. a default fallback), but this should be rare.
    """
    action:     str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning:  str
    source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_empty_strings(self) -> "AIDecision":
        if not self.action.strip():
            raise ValueError("action must not be empty.")
        if not self.reasoning.strip():
            raise ValueError("reasoning must not be empty.")
        return self

    model_config = {"frozen": True}   # decisions are facts; they shouldn't change

def route(decision: AIDecision, thresholds: ConfidenceBand) -> RoutingOutcome:
    """
    Map an ``AIDecision`` to a ``RoutingOutcome`` using the supplied thresholds.
 
    This is a **pure function**: it reads ``decision.confidence``, delegates
    to ``ConfidenceBand.route()``, logs at DEBUG, and returns. No writes,
    no network, no side effects.
 
    The routing logic itself lives in ``ConfidenceBand.route()`` (core/config.py)
    so that threshold values and comparison logic stay co-located. This
    function's job is to:
      1. Accept the full ``AIDecision`` (not just a bare float).
      2. Emit a single structured DEBUG log with enough context to trace the
         decision without opening the audit log.
      3. Return the ``RoutingOutcome``.
 
    Parameters
    ----------
    decision :
        The AI's decision envelope, produced by a pipeline stage.
    thresholds :
        The ``ConfidenceBand`` for the calling pipeline, obtained via
        ``CONFIG.thresholds.for_pipeline("pipeline_name")``.
 
    Returns
    -------
    RoutingOutcome
        One of AUTO, SUGGEST, or CLUELESS.
 
    Examples
    --------
    >>> from core.config import ConfidenceBand
    >>> band = ConfidenceBand(auto=0.85, suggest=0.60)
    >>> d = AIDecision(action="classify:Domain/Movies", confidence=0.90,
    ...                reasoning="Strong keyword match.", source_ids=["inbox/note.md"])
    >>> route(d, band)
    <RouteDecision.AUTO: 'AUTO'>
    """
    outcome: RoutingOutcome = thresholds.route(decision.confidence)
    # DEBUG-only: rich context for developer traces; never emitted in production
    # INFO runs. Structured so log aggregation tools can filter/search.
    logger.debug(
        "confidence_gate",
        action=decision.action,
        confidence= decision.confidence,
        outcome=outcome.value,
        auto_thresh=thresholds.auto,
        suggest_thresh=thresholds.suggest,
        source_ids= decision.source_ids,
        # Truncate reasoning to 120 chars so debug logs stay scannable.
        reasoning_preview=decision.reasoning[:120],
    )
 
    return outcome