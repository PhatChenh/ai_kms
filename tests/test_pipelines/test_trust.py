"""Unit tests for src/pipelines/trust.py — Phase 10 trust calculator."""

import pytest

from core.config import SelfLearningConfig
from core.result import Failure, Success
from pipelines.trust import adjust_trust


@pytest.fixture
def cfg() -> SelfLearningConfig:
    return SelfLearningConfig()


class TestAdjustTrust:
    def test_confirm_adds_delta(self, cfg: SelfLearningConfig):
        result = adjust_trust(0.5, "confirm", cfg)
        assert isinstance(result, Success)
        assert result.value == pytest.approx(0.55)

    def test_reject_subtracts_delta(self, cfg: SelfLearningConfig):
        result = adjust_trust(0.5, "reject", cfg)
        assert isinstance(result, Success)
        assert result.value == pytest.approx(0.4)

    def test_revise_returns_base(self, cfg: SelfLearningConfig):
        result = adjust_trust(0.5, "revise", cfg)
        assert isinstance(result, Success)
        assert result.value == pytest.approx(0.6)

    def test_confirm_clamped_at_one(self, cfg: SelfLearningConfig):
        result = adjust_trust(0.98, "confirm", cfg)
        assert isinstance(result, Success)
        assert result.value == 1.0

    def test_reject_clamped_at_zero(self, cfg: SelfLearningConfig):
        result = adjust_trust(0.03, "reject", cfg)
        assert isinstance(result, Success)
        assert result.value == 0.0

    def test_unknown_operation_returns_failure(self, cfg: SelfLearningConfig):
        result = adjust_trust(0.5, "nonexistent", cfg)
        assert isinstance(result, Failure)

    def test_custom_config_deltas(self):
        cfg = SelfLearningConfig(
            trust_confirm_delta=0.10,
            trust_reject_delta=-0.20,
            trust_revise_base=0.7,
        )
        assert adjust_trust(0.5, "confirm", cfg).value == pytest.approx(0.60)
        assert adjust_trust(0.5, "reject", cfg).value == pytest.approx(0.30)
        assert adjust_trust(0.5, "revise", cfg).value == pytest.approx(0.7)
