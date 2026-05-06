"""
tests/test_core/test_confidence.py
===================================

Full test coverage for ``core/confidence.py``.

Test map
--------
  Section 1 — AIDecision         construction, Pydantic validation, frozen contract
  Section 2 — route() branches   AUTO / SUGGEST / CLUELESS with default thresholds
  Section 3 — Boundary values    exact threshold values (0.85, 0.60) and one step either side
  Section 4 — Custom thresholds  overriding defaults proves no hardcoding in route()
  Section 5 — Pure function      idempotency, no mutation, return type, all outcomes reachable

Implementation notes
---------------------
- ``AIDecision`` is a Pydantic ``BaseModel`` with ``frozen=True``. Validation
  errors are ``pydantic.ValidationError``, not bare ``ValueError``. Tests
  import and assert on ``ValidationError`` directly.
- ``ConfidenceBand`` is constructed with keyword args — no YAML I/O anywhere
  in this file. Zero filesystem dependency.
- All tests are sync and in-process. ``route()`` is a pure function; no
  mocking needed.
- Bug reference: the repo's ``route()`` currently calls
  ``thresholds.route(AIDecision.confidence)`` (class attribute) instead of
  ``thresholds.route(decision.confidence)`` (instance value). Section 2's
  first test will catch this immediately with a ``TypeError``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import ConfidenceBand, RouteDecision
from core.confidence import AIDecision, RoutingOutcome, route


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _band(auto: float = 0.85, suggest: float = 0.60) -> ConfidenceBand:
    """Construct a ConfidenceBand — no file I/O."""
    return ConfidenceBand(auto=auto, suggest=suggest)


def _decision(
    confidence: float,
    action: str = "classify:Domain/Movies",
    reasoning: str = "Matches Q2 OKR keywords.",
    source_ids: list[str] | None = None,
) -> AIDecision:
    """Construct a minimal valid AIDecision."""
    return AIDecision(
        action=action,
        confidence=confidence,
        reasoning=reasoning,
        source_ids=source_ids or ["inbox/note.md"],
    )


# ===========================================================================
# Section 1 — AIDecision: Pydantic model construction and validation
# ===========================================================================

class TestAIDecision:
    """
    Unit tests for AIDecision as a Pydantic BaseModel.

    Key contracts:
    - Validation errors are pydantic.ValidationError (not bare ValueError).
    - frozen=True: the model is immutable after construction.
    - source_ids defaults to an empty list.
    - confidence is range-validated by Field(ge=0.0, le=1.0).
    """

    # ── Happy-path construction ───────────────────────────────────────────

    def test_creates_with_all_fields(self):
        d = AIDecision(
            action="classify:Projects/Movies Q2",
            confidence=0.91,
            reasoning="Strong keyword match with Q2 OKR language.",
            source_ids=["inbox/2026-05-04.md", "inbox/2026-05-03.md"],
        )
        assert d.action == "classify:Projects/Movies Q2"
        assert d.confidence == pytest.approx(0.91)
        assert "keyword" in d.reasoning
        assert len(d.source_ids) == 2

    def test_source_ids_defaults_to_empty_list(self):
        d = AIDecision(
            action="fallback:inbox",
            confidence=0.40,
            reasoning="No keywords matched any known domain.",
        )
        assert d.source_ids == []

    def test_confidence_at_zero_is_valid(self):
        d = _decision(confidence=0.0)
        assert d.confidence == pytest.approx(0.0)

    def test_confidence_at_one_is_valid(self):
        d = _decision(confidence=1.0)
        assert d.confidence == pytest.approx(1.0)

    # ── Pydantic validation errors ────────────────────────────────────────

    def test_raises_validation_error_on_empty_action(self):
        """model_validator catches empty action — error is pydantic.ValidationError."""
        with pytest.raises(ValidationError, match="action"):
            AIDecision(action="", confidence=0.80, reasoning="Some reasoning.")

    def test_raises_validation_error_on_whitespace_only_action(self):
        with pytest.raises(ValidationError, match="action"):
            AIDecision(action="   ", confidence=0.80, reasoning="Some reasoning.")

    def test_raises_validation_error_on_empty_reasoning(self):
        with pytest.raises(ValidationError, match="reasoning"):
            AIDecision(action="classify:x", confidence=0.80, reasoning="")

    def test_raises_validation_error_on_whitespace_only_reasoning(self):
        with pytest.raises(ValidationError, match="reasoning"):
            AIDecision(action="classify:x", confidence=0.80, reasoning="\t\n")

    def test_raises_validation_error_on_confidence_above_one(self):
        """Field(le=1.0) enforces the upper bound."""
        with pytest.raises(ValidationError):
            _decision(confidence=1.001)

    def test_raises_validation_error_on_confidence_below_zero(self):
        """Field(ge=0.0) enforces the lower bound."""
        with pytest.raises(ValidationError):
            _decision(confidence=-0.001)

    def test_raises_validation_error_on_percentage_instead_of_ratio(self):
        """Guards against passing 91.0 instead of 0.91 — a common LLM output bug."""
        with pytest.raises(ValidationError):
            _decision(confidence=91.0)

    # ── Frozen model contract ─────────────────────────────────────────────

    def test_model_is_immutable(self):
        """
        frozen=True means AIDecision is immutable after construction.
        A decision, once made, is a fact — it should not be mutated.
        This also protects the audit trail from accidental post-hoc edits.
        """
        d = _decision(0.90)
        with pytest.raises((ValidationError, TypeError)):
            d.confidence = 0.50  # type: ignore[misc]

    def test_source_ids_list_cannot_be_replaced(self):
        """frozen=True prevents replacing the list reference."""
        d = _decision(0.90)
        with pytest.raises((ValidationError, TypeError)):
            d.source_ids = ["new/path.md"]  # type: ignore[misc]


# ===========================================================================
# Section 2 — route(): branch coverage (three outcomes)
# ===========================================================================

class TestRouteBranches:
    """
    Confirm all three routing outcomes are reachable.
    Mid-range values — well away from boundaries — make intent clear.

    NOTE: If these tests raise TypeError, the bug in route() is present:
          ``thresholds.route(AIDecision.confidence)`` must be
          ``thresholds.route(decision.confidence)``. Fix confidence.py first.
    """

    def test_high_confidence_routes_to_auto(self):
        outcome = route(_decision(0.95), _band())
        assert outcome == RoutingOutcome.AUTO

    def test_medium_confidence_routes_to_suggest(self):
        outcome = route(_decision(0.72), _band())
        assert outcome == RoutingOutcome.SUGGEST

    def test_low_confidence_routes_to_clueless(self):
        outcome = route(_decision(0.30), _band())
        assert outcome == RoutingOutcome.CLUELESS

    @pytest.mark.parametrize("score,expected", [
        (1.00, RoutingOutcome.AUTO),
        (0.95, RoutingOutcome.AUTO),
        (0.90, RoutingOutcome.AUTO),
        (0.72, RoutingOutcome.SUGGEST),
        (0.65, RoutingOutcome.SUGGEST),
        (0.30, RoutingOutcome.CLUELESS),
        (0.10, RoutingOutcome.CLUELESS),
        (0.00, RoutingOutcome.CLUELESS),
    ])
    def test_representative_values(self, score: float, expected: RoutingOutcome):
        assert route(_decision(score), _band()) == expected

    def test_routing_outcome_is_route_decision(self):
        """
        RoutingOutcome is a re-export of RouteDecision — the same enum object.
        If this fails, the alias in confidence.py has been broken and pipeline
        code importing RoutingOutcome will silently use a different type.
        """
        assert RoutingOutcome is RouteDecision


# ===========================================================================
# Section 3 — Boundary values
# ===========================================================================

class TestBoundaryValues:
    """
    Tests at and immediately around the threshold boundaries.

    Default thresholds: auto=0.85, suggest=0.60

    Boundary semantics (from ConfidenceBand.route in config.py):
        score >= 0.85 → AUTO
        score >= 0.60 → SUGGEST   (and score < 0.85)
        score <  0.60 → CLUELESS

    These tests document inclusivity unambiguously.
    """

    def test_exactly_085_routes_to_auto(self):
        assert route(_decision(0.85), _band()) == RoutingOutcome.AUTO

    def test_just_below_085_routes_to_suggest(self):
        assert route(_decision(0.84), _band()) == RoutingOutcome.SUGGEST

    def test_just_above_085_routes_to_auto(self):
        assert route(_decision(0.86), _band()) == RoutingOutcome.AUTO

    def test_exactly_060_routes_to_suggest(self):
        assert route(_decision(0.60), _band()) == RoutingOutcome.SUGGEST

    def test_just_below_060_routes_to_clueless(self):
        assert route(_decision(0.59), _band()) == RoutingOutcome.CLUELESS

    def test_just_above_060_routes_to_suggest(self):
        assert route(_decision(0.61), _band()) == RoutingOutcome.SUGGEST

    @pytest.mark.parametrize("score,expected", [
        (0.85, RoutingOutcome.AUTO),      # exact auto — inclusive
        (0.84, RoutingOutcome.SUGGEST),   # just below auto
        (0.86, RoutingOutcome.AUTO),      # just above auto
        (0.60, RoutingOutcome.SUGGEST),   # exact suggest — inclusive
        (0.59, RoutingOutcome.CLUELESS),  # just below suggest
        (0.61, RoutingOutcome.SUGGEST),   # just above suggest
    ], ids=[
        "auto_exact",
        "auto_below",
        "auto_above",
        "suggest_exact",
        "suggest_below",
        "suggest_above",
    ])
    def test_boundary_parametrized(self, score: float, expected: RoutingOutcome):
        assert route(_decision(score), _band()) == expected


# ===========================================================================
# Section 4 — Custom thresholds
# ===========================================================================

class TestCustomThresholds:
    """
    Prove nothing is hardcoded in route().
    If these pass, editing thresholds.yaml changes behavior without any code change.
    """

    def test_tighter_auto_threshold(self):
        """auto=0.95: a score of 0.90 that normally auto-executes now only suggests."""
        tight = _band(auto=0.95, suggest=0.60)
        assert route(_decision(0.90), tight) == RoutingOutcome.SUGGEST

    def test_looser_auto_threshold(self):
        """auto=0.70: a score of 0.75 that would normally suggest now auto-executes."""
        loose = _band(auto=0.70, suggest=0.40)
        assert route(_decision(0.75), loose) == RoutingOutcome.AUTO

    def test_tighter_suggest_threshold(self):
        """suggest=0.80: a score of 0.70 that normally suggests is now clueless."""
        tight = _band(auto=0.90, suggest=0.80)
        assert route(_decision(0.70), tight) == RoutingOutcome.CLUELESS

    def test_conservative_pipeline_thresholds(self):
        """
        Simulate a strict pipeline (e.g. 'promote'): auto=0.92, suggest=0.75.
        Scores that auto-execute globally get flagged; mid-range becomes clueless.
        """
        strict = _band(auto=0.92, suggest=0.75)
        assert route(_decision(0.88), strict) == RoutingOutcome.SUGGEST
        assert route(_decision(0.70), strict) == RoutingOutcome.CLUELESS
        assert route(_decision(0.93), strict) == RoutingOutcome.AUTO

    def test_exact_custom_boundary_auto(self):
        """Boundary holds at non-default threshold: score == auto → AUTO."""
        assert route(_decision(0.92), _band(auto=0.92, suggest=0.70)) == RoutingOutcome.AUTO

    def test_exact_custom_boundary_suggest(self):
        """Boundary holds at non-default threshold: score == suggest → SUGGEST."""
        assert route(_decision(0.70), _band(auto=0.92, suggest=0.70)) == RoutingOutcome.SUGGEST

    def test_just_below_custom_boundary_suggest(self):
        """One step below non-default suggest threshold → CLUELESS."""
        assert route(_decision(0.69), _band(auto=0.92, suggest=0.70)) == RoutingOutcome.CLUELESS


# ===========================================================================
# Section 5 — Pure function contract
# ===========================================================================

class TestPureFunctionContract:
    """
    Verify route() has no side effects and is idempotent.
    A regression here means someone added state or a write inside route().
    """

    def test_same_inputs_same_output(self):
        """Calling route() twice with identical inputs returns the same outcome."""
        d = _decision(0.90)
        band = _band()
        assert route(d, band) == route(d, band)

    def test_route_does_not_mutate_thresholds(self):
        """
        route() must not modify the ConfidenceBand it receives.
        ConfidenceBand is a Pydantic model — frozen=False by default, so
        mutation is possible if someone accidentally assigns to it inside route().
        """
        band = _band(auto=0.85, suggest=0.60)
        original_auto = band.auto
        original_suggest = band.suggest

        route(_decision(0.90), band)

        assert band.auto == pytest.approx(original_auto)
        assert band.suggest == pytest.approx(original_suggest)

    def test_frozen_decision_cannot_be_mutated_by_route(self):
        """
        AIDecision.frozen=True guarantees route() cannot mutate it even if it
        tried. This test documents the contract and catches a future removal
        of frozen=True from the model config.
        """
        d = _decision(0.90)
        route(d, _band())
        assert d.confidence == pytest.approx(0.90)
        assert d.action == "classify:Domain/Movies"

    def test_return_type_is_routing_outcome(self):
        """route() always returns a RoutingOutcome instance."""
        for score in [0.10, 0.60, 0.85, 1.00]:
            result = route(_decision(score), _band())
            assert isinstance(result, RoutingOutcome)

    def test_all_three_outcomes_are_reachable(self):
        """
        Structural completeness: every enum member must be reachable.
        If a fourth outcome is added to RouteDecision, this test forces
        someone to handle it — preventing silent dead code.
        """
        band = _band()
        outcomes = {
            route(_decision(0.90), band),
            route(_decision(0.70), band),
            route(_decision(0.30), band),
        }
        assert outcomes == set(RoutingOutcome)
