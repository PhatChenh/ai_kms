"""Unit tests for Phase 10 overwrite guard and trust preservation.

Tests the _should_overwrite() function directly, avoiding DB
or vault I/O.
"""

import pytest

from pipelines.classify_writer import _should_overwrite


# Minimal stub for KnowledgeEntry — only the fields _should_overwrite reads
class _FakeEntry:
    def __init__(self, trust_score: float):
        self.trust_score = trust_score


class TestShouldOverwrite:
    def test_trust_above_threshold_returns_false(self):
        entry = _FakeEntry(0.6)
        assert _should_overwrite(entry, threshold=0.5) is False

    def test_trust_equal_to_threshold_returns_true(self):
        entry = _FakeEntry(0.5)
        assert _should_overwrite(entry, threshold=0.5) is True

    def test_trust_below_threshold_returns_true(self):
        entry = _FakeEntry(0.3)
        assert _should_overwrite(entry, threshold=0.5) is True

    def test_trust_zero_returns_true(self):
        entry = _FakeEntry(0.0)
        assert _should_overwrite(entry, threshold=0.5) is True

    def test_trust_one_returns_false(self):
        entry = _FakeEntry(1.0)
        assert _should_overwrite(entry, threshold=0.5) is False

    def test_custom_threshold(self):
        entry = _FakeEntry(0.7)
        assert _should_overwrite(entry, threshold=0.8) is True
        assert _should_overwrite(entry, threshold=0.6) is False
