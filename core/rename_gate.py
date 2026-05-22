"""
core/rename_gate.py

Rename gate: classifies each captured file into SKIP / AUGMENT / FULL_RENAME
using deterministic Python rules. Zero new AI calls.

Entry point: decide_rename(src, ai_title, is_existing_doc, config)

Rule flowchart (evaluated top-down):
  1. Already in DB (is_existing_doc=True)  → SKIP
  2. Office-type file with ≥2-word stem     → SKIP  (user named intentionally)
  3. Generic placeholder name               → AUGMENT (keep original + append AI topic)
  4. Illegible / gibberish stem             → FULL_RENAME (replace with AI title)
  default                                   → SKIP  (safe conservative fallback)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.config import RenameGateConfig


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class RenameAction(str, Enum):
    SKIP        = "SKIP"
    AUGMENT     = "AUGMENT"
    FULL_RENAME = "FULL_RENAME"


@dataclass(frozen=True)
class RenameDecision:
    """
    Immutable result of the rename gate.

    Attributes:
        action:     What the pipeline should do with the file.
        final_stem: The filename stem (no extension) to use after the decision.
                    For SKIP: equals the original stem.
                    For AUGMENT: "original - AI topic".
                    For FULL_RENAME: sanitized AI title.
        reason:     Human-readable explanation — written to audit log.
        confidence: Gate confidence in its decision (0.0–1.0).
    """

    action:     RenameAction
    final_stem: str
    reason:     str
    confidence: float


# ---------------------------------------------------------------------------
# Module-level constant
# ---------------------------------------------------------------------------

# TODO: migrate to RenameGateConfig.generic_names: list[str] (TD-GATE-1)
_GENERIC_NAMES: frozenset[str] = frozenset({
    "untitled", "notes", "note", "meeting", "a meeting",
    "new note", "draft", "scratch", "temp", "tmp",
    "document", "new document", "file", "new file",
    "inbox", "todo", "wip",
})

# Characters that are unsafe in filenames across macOS / Windows / Linux.
_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|]')

# A stem that looks like gibberish: all hex chars or UUID pattern.
_HEX_ONLY_RE   = re.compile(r'^[0-9a-fA-F]+$')
_UUID_RE        = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)
# A stem whose tokens are all non-word (no recognizable letters between separators).
_WORD_TOKEN_RE  = re.compile(r'[a-zA-Z]{3,}')

# Looks like a real word: has at least one vowel among its letters.
_VOWEL_RE = re.compile(r'[aeiouAEIOU]')


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def decide_rename(
    src: Path,
    ai_title: str,
    is_existing_doc: bool,
    config: RenameGateConfig,
) -> RenameDecision:
    """
    Classify a file into SKIP / AUGMENT / FULL_RENAME using deterministic rules.

    Args:
        src:             Full path to the source file (inbox drop).
        ai_title:        Title produced by the metadata stage (Step 3).
                         Already available — no new AI call is made here.
        is_existing_doc: True if this path already exists in the documents table.
        config:          Tunable parameters from config/config.yaml capture.rename_gate.

    Returns:
        RenameDecision with action, final_stem, reason, and confidence.
    """
    stem      = src.stem
    extension = src.suffix.lower()

    # -- Rule 1: already captured before ─────────────────────────────────────
    if is_existing_doc:
        return RenameDecision(
            action=RenameAction.SKIP,
            final_stem=stem,
            reason="File already in documents table — user may be actively editing it.",
            confidence=1.0,
        )

    # -- Rule 2: office type with legible multi-word stem ─────────────────────
    # Generic check runs first: "a meeting.md" is legible (2 words) but also
    # generic, and Rule 3 must fire for it — not Rule 2.
    if extension in config.office_extensions and _is_legible(stem) and not _is_generic(stem):
        return RenameDecision(
            action=RenameAction.SKIP,
            final_stem=stem,
            reason="Office file with descriptive multi-word name — user named it intentionally.",
            confidence=0.9,
        )

    # -- Rule 3: generic placeholder ──────────────────────────────────────────
    if _is_generic(stem):
        final_stem = _build_augmented_stem(stem, ai_title, config.max_stem_length)
        return RenameDecision(
            action=RenameAction.AUGMENT,
            final_stem=final_stem,
            reason=f"Generic placeholder name '{stem}' — augmented with AI topic.",
            confidence=0.85,
        )

    # -- Rule 4: illegible / gibberish ────────────────────────────────────────
    if _is_illegible(stem):
        sanitized = _sanitize_stem(ai_title)
        return RenameDecision(
            action=RenameAction.FULL_RENAME,
            final_stem=sanitized,
            reason=f"Illegible filename '{stem}' — replaced with AI title.",
            confidence=0.9,
        )

    # -- Default: safe fallback ───────────────────────────────────────────────
    return RenameDecision(
        action=RenameAction.SKIP,
        final_stem=stem,
        reason="Cannot clearly classify filename — leaving unchanged (conservative default).",
        confidence=0.5,
    )


# ---------------------------------------------------------------------------
# Private classifiers
# ---------------------------------------------------------------------------

def _is_legible(stem: str) -> bool:
    """
    True if the stem looks like a deliberate human name: ≥2 whitespace-separated
    tokens, each containing at least one letter.

    Single-word stems do NOT qualify — they fall through to Rule 3/4 check.
    """
    tokens = stem.strip().split()
    if len(tokens) < 2:
        return False
    return all(re.search(r'[a-zA-Z]', t) for t in tokens)


def _is_generic(stem: str) -> bool:
    """
    True if the normalized lowercase stem is in the generic names set.

    Normalization strips leading/trailing whitespace only — internal whitespace
    is preserved so "a meeting" matches the frozenset entry exactly.
    """
    normalized = stem.strip().lower()
    return normalized in _GENERIC_NAMES


def _is_illegible(stem: str) -> bool:
    """
    True if the stem is keyboard mash, a hash code, UUID, or very short junk.

    Criteria (any one is sufficient):
    - ≤ 2 characters (too short to be meaningful)
    - Matches UUID pattern
    - All characters are hex digits (likely a hash code)
    - No 3-letter word token found anywhere in the stem
    """
    if len(stem) <= 2:
        return True
    if _UUID_RE.match(stem):
        return True
    # Strip hyphens/underscores to check raw hex.
    plain = stem.replace("-", "").replace("_", "")
    if _HEX_ONLY_RE.match(plain) and len(plain) >= 6:
        return True
    # No recognizable word token (3+ consecutive letters) → mash
    if not _WORD_TOKEN_RE.search(stem):
        return True
    # All letter tokens lack vowels → keyboard mash (e.g. "xkdhgksjfs")
    letter_tokens = _WORD_TOKEN_RE.findall(stem)
    if letter_tokens and not any(_VOWEL_RE.search(t) for t in letter_tokens):
        return True
    return False


# ---------------------------------------------------------------------------
# Private stem builders
# ---------------------------------------------------------------------------

def _sanitize_stem(title: str) -> str:
    """
    Remove characters unsafe in filenames and collapse whitespace.

    Args:
        title: Raw string (AI title or original stem).

    Returns:
        Cleaned stem safe for use as a filename component.
    """
    # NFC normalize first (Vietnamese diacritics, etc.)
    normalized = unicodedata.normalize("NFC", title)
    # Strip unsafe filesystem chars
    cleaned = _UNSAFE_CHARS.sub("", normalized)
    # Collapse internal whitespace
    return " ".join(cleaned.split())


def _build_augmented_stem(
    original_stem: str,
    ai_title: str,
    max_stem_length: int,
) -> str:
    """
    Build "original - AI topic" within max_stem_length characters.

    The AUGMENT action always preserves the original name. If the AI title
    does not fit in the remaining budget, the original stem is returned as-is.

    Args:
        original_stem:   The file's existing stem (kept verbatim).
        ai_title:        The AI's suggested title for the content.
        max_stem_length: Maximum total characters for the combined stem.

    Returns:
        Combined stem, or original_stem alone if the AI title doesn't fit.
    """
    separator  = " - "
    sanitized_title = _sanitize_stem(ai_title)
    budget = max_stem_length - len(original_stem) - len(separator)
    if budget <= 0 or len(sanitized_title) > budget:
        # AI title doesn't fit — AUGMENT always keeps original name
        return original_stem
    return f"{original_stem}{separator}{sanitized_title}"
