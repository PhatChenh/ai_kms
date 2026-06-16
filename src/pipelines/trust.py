"""Trust Score Adjustment — pure function for Phase 10 self-learning.

Trust deltas are config-driven (C-06). No floats in code.
"""

from __future__ import annotations

from core.result import Failure, Result, Success


def adjust_trust(
    current_score: float,
    operation: str,
    config,  # SelfLearningConfig
) -> Result[float]:
    """Compute the new trust score after a user correction.

    Args:
        current_score: The entry's current trust_score (0.0–1.0).
        operation: One of ``confirm``, ``reject``, ``revise``.
        config: ``SelfLearningConfig`` with trust deltas.

    Returns:
        Success(new_score) clamped to [0.0, 1.0].
        Failure for unknown operations.
    """
    match operation:
        case "confirm":
            new = min(1.0, current_score + config.trust_confirm_delta)
        case "reject":
            new = max(0.0, current_score + config.trust_reject_delta)
        case "revise":
            new = config.trust_revise_base
        case _:
            return Failure(
                f"Unknown trust adjustment operation: {operation}",
                recoverable=False,
                context={"operation": operation, "current_score": current_score},
            )

    return Success(new)
