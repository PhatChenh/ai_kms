"""
tests/test_core/test_rename_gate.py

Unit tests for core/rename_gate.py — the rename gate decision engine.

All tests are pure Python (no vault on disk, no CONFIG import).
RenameGateConfig is constructed directly with defaults or test-specific values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.rename_gate import RenameAction, RenameDecision, decide_rename
from core.config import RenameGateConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(**overrides) -> RenameGateConfig:
    """Build a RenameGateConfig with defaults, optionally overriding fields."""
    return RenameGateConfig(**overrides)


def _decide(
    filename: str,
    ai_title: str = "AI Generated Title",
    is_existing_doc: bool = False,
    **cfg_overrides,
) -> RenameDecision:
    """Convenience wrapper: decide_rename from a filename string."""
    src = Path(f"/tmp/inbox/{filename}")
    return decide_rename(src, ai_title, is_existing_doc, config=_cfg(**cfg_overrides))


# ---------------------------------------------------------------------------
# Rule 1: existing document always SKIP
# ---------------------------------------------------------------------------

class TestRule1ExistingDoc:
    def test_existing_doc_legible_name_skips(self):
        decision = _decide("Q2 Strategy.md", ai_title="Quarterly Review", is_existing_doc=True)
        assert decision.action == RenameAction.SKIP

    def test_existing_doc_illegible_name_still_skips(self):
        """Even keyboard mash is SKIP if file was captured before."""
        decision = _decide("xkjdhfs83.pdf", ai_title="Q2 Movies Deck", is_existing_doc=True)
        assert decision.action == RenameAction.SKIP

    def test_existing_doc_generic_name_still_skips(self):
        """'untitled.md' already in DB → SKIP (user may be editing it)."""
        decision = _decide("untitled.md", ai_title="Board Meeting Notes", is_existing_doc=True)
        assert decision.action == RenameAction.SKIP

    def test_existing_doc_final_stem_is_original(self):
        """SKIP final_stem must equal the original filename stem."""
        decision = _decide("My Report.md", ai_title="Renamed Title", is_existing_doc=True)
        assert decision.final_stem == "My Report"


# ---------------------------------------------------------------------------
# Rule 2: legible office doc → SKIP (user named it on purpose)
# ---------------------------------------------------------------------------

class TestRule2LegibleOfficeDoc:
    def test_two_word_md_skips(self):
        decision = _decide("Q2 Strategy.md")
        assert decision.action == RenameAction.SKIP

    def test_three_word_docx_skips(self):
        decision = _decide("Annual Budget Plan.docx")
        assert decision.action == RenameAction.SKIP

    def test_two_word_xlsx_skips(self):
        decision = _decide("Sales Report.xlsx")
        assert decision.action == RenameAction.SKIP

    def test_two_word_pptx_skips(self):
        decision = _decide("Board Deck.pptx")
        assert decision.action == RenameAction.SKIP

    def test_two_word_txt_skips(self):
        decision = _decide("Meeting Notes.txt")
        assert decision.action == RenameAction.SKIP

    def test_single_word_md_falls_through_rule2(self):
        """Single word office doc — Rule 2 does NOT fire (only ≥2 words qualify)."""
        decision = _decide("notes.md")
        # Should fall through to Rule 3 or 4 — not SKIP via Rule 2
        # "notes" is generic → Rule 3 → AUGMENT
        assert decision.action == RenameAction.AUGMENT

    def test_rule2_final_stem_is_original(self):
        decision = _decide("Project Alpha.md")
        assert decision.final_stem == "Project Alpha"

    def test_non_office_extension_does_not_trigger_rule2(self):
        """PDF is not in office_extensions — a legible PDF name falls through to Rule 4 check."""
        decision = _decide("My Report.pdf")
        # "My Report" is 2 words, legible — but .pdf not in office_extensions
        # Falls to Rule 3 (not generic) → Rule 4 (not illegible) → SKIP (default)
        assert decision.action == RenameAction.SKIP


# ---------------------------------------------------------------------------
# Rule 3: generic placeholder → AUGMENT
# ---------------------------------------------------------------------------

class TestRule3GenericPlaceholder:
    def test_untitled_augments(self):
        decision = _decide("untitled.md", ai_title="Board Strategy Review")
        assert decision.action == RenameAction.AUGMENT

    def test_notes_augments(self):
        decision = _decide("notes.md", ai_title="Q2 Planning Session")
        assert decision.action == RenameAction.AUGMENT

    def test_meeting_augments(self):
        decision = _decide("meeting.md", ai_title="Project Sync Notes")
        assert decision.action == RenameAction.AUGMENT

    def test_a_meeting_augments(self):
        """'a meeting' — generic AND has 2 tokens. Rule 3 fires, NOT Rule 4."""
        decision = _decide("a meeting.md", ai_title="Q2 Strategy Review")
        assert decision.action == RenameAction.AUGMENT

    def test_augment_final_stem_contains_original(self):
        decision = _decide("a meeting.md", ai_title="Q2 Strategy Review")
        assert "a meeting" in decision.final_stem

    def test_augment_final_stem_contains_ai_title(self):
        decision = _decide("a meeting.md", ai_title="Q2 Strategy Review")
        assert "Q2 Strategy Review" in decision.final_stem

    def test_augment_format_original_dash_title(self):
        """Format: 'original - AI title'."""
        decision = _decide("a meeting.md", ai_title="Phong Q2 Sync")
        assert decision.final_stem == "a meeting - Phong Q2 Sync"

    def test_augment_near_max_stem_truncates_to_original(self):
        """When budget ≤ 0, _build_augmented_stem falls back to original_stem only."""
        # max_stem_length=20; "a meeting" is 9 chars → budget = 20 - 9 - 3 (" - ") = 8
        # AI title "Q2 Strategy Review" is longer than 8 → fallback = original stem
        decision = _decide(
            "a meeting.md",
            ai_title="Q2 Strategy Review",
            max_stem_length=20,
        )
        assert decision.final_stem == "a meeting"

    def test_augment_exact_fit(self):
        """AI title fits exactly in budget — no truncation needed."""
        decision = _decide(
            "notes.md",
            ai_title="Q2",       # 2 chars, budget with max=15: 15 - 5 - 3 = 7 ≥ 2 → fits
            max_stem_length=15,
        )
        assert decision.final_stem == "notes - Q2"


# ---------------------------------------------------------------------------
# Rule 4: illegible filename → FULL_RENAME
# ---------------------------------------------------------------------------

class TestRule4Illegible:
    def test_keyboard_mash_full_renames(self):
        decision = _decide("xkdhgksjfs.pdf", ai_title="Q2 Movies Deck")
        assert decision.action == RenameAction.FULL_RENAME

    def test_hex_hash_full_renames(self):
        decision = _decide("a3f9c2b1.pdf", ai_title="Contract Draft")
        assert decision.action == RenameAction.FULL_RENAME

    def test_uuid_full_renames(self):
        decision = _decide(
            "550e8400-e29b-41d4-a716-446655440000.pdf",
            ai_title="Invoice Q1",
        )
        assert decision.action == RenameAction.FULL_RENAME

    def test_very_short_token_full_renames(self):
        """Filename stem of 1-2 chars is illegible junk."""
        decision = _decide("ab.pdf", ai_title="Board Minutes")
        assert decision.action == RenameAction.FULL_RENAME

    def test_full_rename_final_stem_is_sanitized_ai_title(self):
        decision = _decide("xkdhgksjfs.pdf", ai_title="Q2 Movies Deck")
        assert decision.final_stem == "Q2 Movies Deck"

    def test_full_rename_sanitizes_special_chars(self):
        """AI title with special chars is sanitized for filesystem safety."""
        decision = _decide("xkdhgksjfs.pdf", ai_title="Q2/Movies: Deck*Review")
        # Slashes, colons, asterisks should be removed or replaced
        assert "/" not in decision.final_stem
        assert ":" not in decision.final_stem
        assert "*" not in decision.final_stem


# ---------------------------------------------------------------------------
# Default (safe fallback): ambiguous non-office → SKIP
# ---------------------------------------------------------------------------

class TestDefaultSafeFallback:
    def test_single_legible_word_non_office_skips(self):
        """Single legible word, non-office extension — can't clearly classify → SKIP."""
        decision = _decide("proposal.pdf")
        assert decision.action == RenameAction.SKIP

    def test_two_word_non_office_not_generic_not_illegible_skips(self):
        """2-word .pdf, not generic, not illegible — safe default SKIP."""
        decision = _decide("My Report.pdf", ai_title="Strategic Initiative")
        assert decision.action == RenameAction.SKIP


# ---------------------------------------------------------------------------
# Rule ordering regression guard
# ---------------------------------------------------------------------------

class TestRuleOrdering:
    def test_a_meeting_is_rule3_not_rule4(self):
        """'a meeting' has 2 tokens → Rule 2 doesn't fire (generic).
        Rule 3 checks generic → yes → AUGMENT. Rule 4 never reached."""
        decision = _decide("a meeting.md", ai_title="Q2 Strategy Review")
        assert decision.action == RenameAction.AUGMENT
        # Verify Rule 4 (FULL_RENAME) was NOT chosen
        assert decision.action != RenameAction.FULL_RENAME

    def test_existing_doc_beats_all_other_rules(self):
        """Rule 1 (existing doc) short-circuits Rules 2-4 regardless of filename."""
        for filename in ["xkdhgksjfs.pdf", "untitled.md", "My Report.docx"]:
            decision = _decide(filename, is_existing_doc=True)
            assert decision.action == RenameAction.SKIP, (
                f"Expected SKIP for existing doc '{filename}', got {decision.action}"
            )


# ---------------------------------------------------------------------------
# RenameDecision structure
# ---------------------------------------------------------------------------

class TestRenameDecisionStructure:
    def test_decision_has_reason(self):
        decision = _decide("Q2 Strategy.md")
        assert decision.reason
        assert isinstance(decision.reason, str)

    def test_decision_has_confidence(self):
        decision = _decide("xkdhgksjfs.pdf", ai_title="Deck")
        assert 0.0 <= decision.confidence <= 1.0

    def test_decision_is_frozen(self):
        """RenameDecision must be immutable (frozen dataclass)."""
        decision = _decide("notes.md", ai_title="Q2 Review")
        with pytest.raises((AttributeError, TypeError)):
            decision.action = RenameAction.SKIP  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Importability without vault
# ---------------------------------------------------------------------------

class TestImportability:
    def test_decide_rename_importable_no_vault(self):
        """decide_rename must be importable with no CONFIG / no vault on disk."""
        from core.rename_gate import decide_rename as dr  # noqa: F401
        assert callable(dr)

    def test_rename_gate_config_importable_no_vault(self):
        from core.config import RenameGateConfig as RGC  # noqa: F401
        assert issubclass(RGC, object)
