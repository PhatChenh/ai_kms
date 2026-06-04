# Implementation Plan: Rename Gate for Capture Pipeline

## 1. Context & Goal

**Problem:** In `pipelines/capture.py:306`, the rename trigger is `if sanitized_stem != src.stem`. Because the LLM-generated title in `extract_metadata.yaml` almost always differs from the source filename, this currently fires on nearly every capture — including legible working files and recently edited notes. Result: filenames change unexpectedly, breaking user trust.

**Goal:** Insert a deterministic pre-decision gate that classifies each file into one of three rename lanes before the rename actually happens. The LLM still produces a single title in the metadata stage (no new LLM call), but the gate decides *how* — or *whether* — that title gets applied to the filename.

**Non-goal:** Adding a second LLM call. Adding semantic "is the filename related to content" checks (deferred — too expensive and fragile for v1).

---

## 2. Design Decisions (with rationale)

### 2.1 Deterministic gate, not prompt instructions
We learned the hard way that telling the LLM "don't rename legible files" doesn't work — that's advice, not a constraint. The gate is enforced in Python code so it cannot be ignored.

### 2.2 Three actions: SKIP / AUGMENT / FULL_RENAME
Maps directly to the user's four rules:
- Rule 1 (recently updated) → `SKIP`
- Rule 2 (office doc + legible) → `SKIP`
- Rule 3 (generic placeholder) → `AUGMENT`
- Rule 4 (illegible/gibberish) → `FULL_RENAME`

### 2.3 "Recently modified" signal: lookup in `documents` table, not mtime
mtime alone can't distinguish "freshly dropped into inbox" (recent mtime, valid to rename) from "user actively editing" (recent mtime, do NOT rename). Cleaner signal: **if `vault_path` already exists in the `documents` table, this is a re-capture of an active file → SKIP rename**. First-time captures fall through to legibility rules.

Bonus: this aligns with the existing `scan_capture` flow where `added` vs `modified` files both call `capture_file`, but with different semantic meaning.

### 2.4 AUGMENT strategy: `{original_stem} - {ai_title}`
Preserves the user's original word (e.g., "meeting", "notes") AND adds the AI-derived topical info. Simple, predictable, doesn't require a second prompt. Implementer can iterate later if needed.

### 2.5 Reuse the existing AI title — no second LLM call
The `metadata` stage already produces a title. The gate just decides whether to apply it, augment with it, or ignore it. Same cost, same latency as today.

### 2.6 Conservative default: when in doubt, SKIP
For files that don't clearly match any rule (e.g., a single legible word like "notes.pdf"), default to SKIP. Trust is harder to rebuild than to keep — users can rename manually if they really want a different name.

### 2.7 Office doc extensions are configurable
`.md`, `.docx`, `.xlsx`, `.pptx`, `.txt` — treat as "human-curated names". Other extensions (`.pdf`, `.png`, downloads) are typically machine-generated → less deference.

---

## 3. Change Set Summary

| File | Type | Description |
|---|---|---|
| `core/rename_gate.py` | **NEW** | Pure deterministic gate module |
| `core/config.py` | MODIFY | Add `RenameGateConfig` and wire into `CaptureConfig` |
| `config/main.yaml` (or equivalent) | MODIFY | Add defaults for new config fields |
| `pipelines/capture.py` | MODIFY | Call gate inside `_store_md` and `_store_nonmd`; add audit entry |
| `prompts/extract_metadata.yaml` | **NO CHANGE** | Title generation stays the same — gate handles enforcement downstream |
| `tests/core/test_rename_gate.py` | **NEW** | Unit tests for gate decisions |
| `tests/pipelines/test_capture_rename.py` | **NEW** | Integration tests for capture-pipeline rename behavior |

---

## 4. Detailed Implementation

### 4.1 NEW FILE: `core/rename_gate.py`

```python
"""core/rename_gate.py

Deterministic pre-decision gate for filename renaming during capture.

Classifies each captured file into one of three lanes:
  - SKIP:        keep the existing stem
  - AUGMENT:     keep stem + append AI-derived topical info
  - FULL_RENAME: replace stem entirely with AI title

The gate runs AFTER the metadata stage (so the AI title is known) but
BEFORE the store stage performs any rename. No LLM calls happen here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = [
    "RenameAction",
    "RenameDecision",
    "decide_rename",
    "OFFICE_EXTENSIONS",
]


class RenameAction(str, Enum):
    SKIP = "skip"
    AUGMENT = "augment"
    FULL_RENAME = "full_rename"


@dataclass(frozen=True)
class RenameDecision:
    """Result of the rename gate. Always carries a final_stem the caller can use directly."""
    action: RenameAction
    final_stem: str    # sanitized stem (no extension); for SKIP this equals src.stem
    reason: str        # human-readable; goes to audit_log
    confidence: float  # 1.0 = deterministic rule matched; <1.0 = heuristic


# File extensions treated as "human-curated names" — user typed these themselves.
OFFICE_EXTENSIONS: frozenset[str] = frozenset({".md", ".docx", ".xlsx", ".pptx", ".txt"})

# Common placeholder names the OS or user assigns when nothing better is at hand.
_GENERIC_NAMES: frozenset[str] = frozenset({
    "untitled", "new document", "document", "note", "notes",
    "meeting", "a meeting", "meeting notes", "new note",
    "copy", "draft", "temp", "test", "file", "summary",
})

# --- Regex patterns ---------------------------------------------------------
# Hex hash / fingerprint
_HEX_HASH_RE = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)
# UUID anywhere in the stem
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}", re.IGNORECASE)
# Long consonant run = keyboard mash (no English word has 5+ consecutive consonants)
_KEYBOARD_MASH_RE = re.compile(r"[bcdfghjklmnpqrstvwxz]{5,}", re.IGNORECASE)
# Sanitization for derived stems
_UNSAFE_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# --- Public API -------------------------------------------------------------


def decide_rename(
    src: Path,
    ai_title: str,
    is_existing_doc: bool,
) -> RenameDecision:
    """Classify the rename action for a captured file.

    Args:
        src:             Source path of the captured file.
        ai_title:        Title suggested by the metadata stage (already sanitized).
        is_existing_doc: True if src.vault_path already exists in the documents table.
                         Indicates an active working file — never rename.

    Returns:
        RenameDecision with final_stem always populated.
    """
    stem = src.stem
    ext = src.suffix.lower()

    # Rule 1: existing doc in DB — never rename, regardless of name quality.
    if is_existing_doc:
        return RenameDecision(
            action=RenameAction.SKIP,
            final_stem=stem,
            reason="already tracked in documents table — active working file",
            confidence=1.0,
        )

    # Rule 2: office doc with a legible name — trust the human.
    if ext in OFFICE_EXTENSIONS and _is_legible(stem):
        return RenameDecision(
            action=RenameAction.SKIP,
            final_stem=stem,
            reason=f"office document ({ext}) with legible human-assigned name",
            confidence=1.0,
        )

    # Rule 3: generic placeholder — augment with AI title to add discernible info.
    if _is_generic(stem):
        augmented = _build_augmented_stem(stem, ai_title)
        return RenameDecision(
            action=RenameAction.AUGMENT,
            final_stem=augmented,
            reason=f"generic placeholder name '{stem}' — appending topical info",
            confidence=0.95,
        )

    # Rule 4: illegible / machine-generated — full rename.
    if _is_illegible(stem):
        return RenameDecision(
            action=RenameAction.FULL_RENAME,
            final_stem=_sanitize_stem(ai_title),
            reason=f"illegible machine-generated name '{stem}'",
            confidence=0.9,
        )

    # Conservative default: legible-ish, not clearly anything → SKIP.
    return RenameDecision(
        action=RenameAction.SKIP,
        final_stem=stem,
        reason="name is ambiguous but not clearly illegible — conservative skip",
        confidence=0.7,
    )


# --- Classifiers (private) --------------------------------------------------


def _is_legible(stem: str) -> bool:
    """Legible if it has at least 2 word-like tokens and no hash/UUID patterns."""
    if _HEX_HASH_RE.match(stem) or _UUID_RE.search(stem):
        return False
    # Treat hyphens/underscores as word separators
    cleaned = stem.replace("-", " ").replace("_", " ").strip()
    words = [w for w in cleaned.split() if len(w) > 1]
    return len(words) >= 2


def _is_generic(stem: str) -> bool:
    """Generic if it matches a known placeholder (optionally with a trailing counter)."""
    # Strip trailing " 3" / "_3" / "-3"
    cleaned = re.sub(r"[\s_-]+\d+$", "", stem.lower().strip())
    return cleaned in _GENERIC_NAMES


def _is_illegible(stem: str) -> bool:
    """Illegible if it's a hash, UUID, keyboard mash, or single short token."""
    if _HEX_HASH_RE.match(stem):
        return True
    if _UUID_RE.search(stem):
        return True
    if _KEYBOARD_MASH_RE.search(stem):
        return True
    # Single short token with no separators ("xyz", "abc")
    compact = stem.replace("-", "").replace("_", "").replace(" ", "")
    if len(compact) < 5 and " " not in stem and "-" not in stem and "_" not in stem:
        return True
    return False


# --- Stem builders (private) ------------------------------------------------


def _sanitize_stem(raw: str, max_len: int = 120) -> str:
    """Strip path-unsafe chars and trim to max_len."""
    return _UNSAFE_CHARS_RE.sub("", raw)[:max_len].strip()


def _build_augmented_stem(original_stem: str, ai_title: str, max_len: int = 120) -> str:
    """Combine generic placeholder with AI title: '{stem} - {ai_title}'.

    Truncates the AI title so the combined result fits max_len.
    """
    sanitized_title = _sanitize_stem(ai_title, max_len=max_len)
    separator = " - "
    budget = max_len - len(original_stem) - len(separator)
    if budget <= 0:
        # Original stem alone already at limit; fall back to AI title.
        return sanitized_title
    truncated_title = sanitized_title[:budget].strip()
    if not truncated_title:
        return original_stem
    return f"{original_stem}{separator}{truncated_title}"
```

**Important notes for the implementer:**
- All thresholds and constants live in this module as named module-level constants. None of them are confidence thresholds for routing (which live in config) — they are deterministic rule values.
- The gate is a **pure function**. No I/O, no DB calls, no LLM calls. The caller is responsible for computing `is_existing_doc` and passing it in.
- `final_stem` is always set, even for SKIP (just equals `src.stem`). This eliminates `None`-handling at the call site.

---

### 4.2 MODIFY: `core/config.py`

Add a nested config block under `CaptureConfig` (or wherever capture settings live). **Do not hardcode these values in `rename_gate.py`** — they belong in config so the user can tune behavior without code changes.

```python
# Inside core/config.py — adjust to match existing pydantic-settings patterns.

class RenameGateConfig(BaseModel):
    """Tunables for the capture-time rename gate.

    These values are deterministic rule parameters, not confidence thresholds.
    The gate's confidence outputs are informational only (for audit).
    """
    # Office-doc extensions treated as human-curated names.
    # Override here to add e.g. ".odt" or remove ".txt".
    office_extensions: list[str] = Field(
        default_factory=lambda: [".md", ".docx", ".xlsx", ".pptx", ".txt"]
    )

    # Max length of the final stem produced by the gate (before extension).
    max_stem_length: int = 120


class CaptureConfig(BaseModel):
    cooldown_seconds: float
    max_urls_per_note: int
    rename_gate: RenameGateConfig = Field(default_factory=RenameGateConfig)
    # ... existing fields
```

If `office_extensions` and `max_stem_length` end up needing to be passed into `decide_rename()`, change the signature to `decide_rename(src, ai_title, is_existing_doc, config: RenameGateConfig)` and read the values from there. For the v1 cut, hardcoding them as module constants in `rename_gate.py` is acceptable as long as we add a `TODO` comment marking them for migration to config.

**Decision:** start with module constants for simplicity. Migrate to config in a follow-up if the user needs to tune.

---

### 4.3 NO CHANGE: `prompts/extract_metadata.yaml`

The prompt still produces a single `title` field. The gate decides downstream whether to apply it. No prompt edits needed.

**Why this is correct:** the LLM's job is "given this content, what's a good descriptive title?" That's a fine job. The bug was downstream — using that title unconditionally as the filename. By gating the application of the title, we keep the prompt's job clean and single-purpose.

---

### 4.4 MODIFY: `pipelines/capture.py`

Two surgical changes inside the `store` stage, plus an import and a new audit entry.

#### 4.4.1 New imports

```python
from core.rename_gate import RenameAction, RenameDecision, decide_rename
```

#### 4.4.2 Refactor `_store_md`

Replace the current rename block (lines ~298–322) with a gate-driven version. The diff:

**Before:**
```python
sanitized_stem = _sanitize_title(mr.ai_title)
src = mr.raw.source_path

if sanitized_stem != src.stem:
    dst = _find_rename_dst(src.parent, sanitized_stem)
    if dst is not None:
        # ... rename
```

**After:**
```python
src = mr.raw.source_path

# Check whether this vault_path is already tracked — signals an active working file.
existing_doc_result = documents.get_by_path(to_vault_path(src), db_path=ctx.db_path)
is_existing_doc = isinstance(existing_doc_result, Success) and existing_doc_result.value is not None

decision = decide_rename(
    src=src,
    ai_title=mr.ai_title,
    is_existing_doc=is_existing_doc,
)

# Audit the gate decision so every rename outcome is traceable.
gate_audit = AIDecision(
    action=f"capture:rename_gate:{decision.action.value}",
    confidence=decision.confidence,
    reasoning=decision.reason,
    source_ids=[to_vault_path(src)],
)
match audit.write(
    gate_audit,
    pipeline="capture",
    stage="rename_gate",
    outcome=decision.action.value.upper(),
    db_path=ctx.db_path,
):
    case Failure():
        logger.warning("rename_gate.audit_failed", src=str(src))
    case Success():
        pass

sanitized_stem = decision.final_stem

if sanitized_stem != src.stem:
    dst = _find_rename_dst(src.parent, sanitized_stem)
    if dst is not None:
        # ... existing rename branch unchanged
    else:
        logger.warning(
            "store.rename_collision_fallback",
            src=str(src),
            decision_action=decision.action.value,
            reason="all 10 rename slots taken",
        )

# In-place write (no rename or fallback) — unchanged
```

The downstream rename mechanics (`_find_rename_dst`, `move_note`, `write_note`, `documents.delete_by_path`, `documents.upsert`) are untouched.

#### 4.4.3 Refactor `_store_nonmd`

Same gate call, applied before constructing the sibling path:

**Before:**
```python
src = mr.raw.source_path
sanitized_stem = _sanitize_title(mr.ai_title) or src.stem
```

**After:**
```python
src = mr.raw.source_path

# For non-md files, the source is always being moved to attachments/, so the
# "existing doc" check is on the would-be sibling .md path, not the binary itself.
sibling_vault_path = to_vault_path(src.parent / f"{_sanitize_title(mr.ai_title)}.md")
existing_doc_result = documents.get_by_path(sibling_vault_path, db_path=ctx.db_path)
is_existing_doc = isinstance(existing_doc_result, Success) and existing_doc_result.value is not None

decision = decide_rename(
    src=src,
    ai_title=mr.ai_title,
    is_existing_doc=is_existing_doc,
)

# Same audit pattern as _store_md — extract to a helper if duplication grows.
gate_audit = AIDecision(
    action=f"capture:rename_gate:{decision.action.value}",
    confidence=decision.confidence,
    reasoning=decision.reason,
    source_ids=[to_vault_path(src)],
)
audit.write(
    gate_audit,
    pipeline="capture",
    stage="rename_gate",
    outcome=decision.action.value.upper(),
    db_path=ctx.db_path,
)

sanitized_stem = decision.final_stem or src.stem
suffix = src.suffix
# ... rest unchanged (sibling path, attachment_dst with collision loop, etc.)
```

**Implementer note:** If `documents.get_by_path` does not exist, add it. It should be a thin wrapper around `SELECT * FROM documents WHERE vault_path = ?` returning `Result[DocumentRow | None]`.

#### 4.4.4 Optional refactor: extract the audit block to a helper

The audit-writing block is duplicated between `_store_md` and `_store_nonmd`. If the implementer prefers, extract to:

```python
def _audit_rename_gate(decision: RenameDecision, src: Path, ctx: PipelineContext) -> None:
    """Write a rename-gate audit entry; log a warning on audit failure but never raise."""
    gate_audit = AIDecision(
        action=f"capture:rename_gate:{decision.action.value}",
        confidence=decision.confidence,
        reasoning=decision.reason,
        source_ids=[to_vault_path(src)],
    )
    match audit.write(
        gate_audit,
        pipeline="capture",
        stage="rename_gate",
        outcome=decision.action.value.upper(),
        db_path=ctx.db_path,
    ):
        case Failure():
            logger.warning("rename_gate.audit_failed", src=str(src))
        case Success():
            pass
```

Then call `_audit_rename_gate(decision, src, ctx)` in both branches.

---

## 5. Audit Trail

Every rename gate decision writes one row to `audit_log` with:

| Field | Value |
|---|---|
| `pipeline` | `"capture"` |
| `stage` | `"rename_gate"` |
| `action` | `"capture:rename_gate:skip"` / `"capture:rename_gate:augment"` / `"capture:rename_gate:full_rename"` |
| `outcome` | `"SKIP"` / `"AUGMENT"` / `"FULL_RENAME"` |
| `confidence` | the gate's confidence value |
| `reasoning` | the gate's `reason` string (human-readable explanation) |
| `source_ids` | `[vault_path of src]` |

This means the daily briefing can report things like:
> "Skipped 4 renames today: 3 office docs with legible names, 1 active working file. Augmented 2 generic placeholders. Full-renamed 1 file with illegible name 'xkdhgksjfs.pdf' → 'q2-movies-strategy-deck.pdf'."

---

## 6. Test Plan

### 6.1 Unit tests: `tests/core/test_rename_gate.py`

One test per rule branch, using `Path` fixtures (no real filesystem needed — only `Path.suffix` and `Path.stem` are read by the gate):

```python
import pytest
from pathlib import Path
from core.rename_gate import RenameAction, decide_rename

# Rule 1: existing doc
def test_existing_doc_always_skips():
    d = decide_rename(Path("inbox/xkdhgksjfs.pdf"), "q2 strategy", is_existing_doc=True)
    assert d.action == RenameAction.SKIP
    assert d.final_stem == "xkdhgksjfs"

# Rule 2: office doc + legible
@pytest.mark.parametrize("name,ext", [
    ("meeting notes q2", ".md"),
    ("Q2 Strategy Review", ".docx"),
    ("Movies Forecast 2026", ".xlsx"),
])
def test_legible_office_doc_skips(name, ext):
    d = decide_rename(Path(f"inbox/{name}{ext}"), "irrelevant ai title", is_existing_doc=False)
    assert d.action == RenameAction.SKIP
    assert d.final_stem == name

# Rule 3: generic placeholder → augment
@pytest.mark.parametrize("stem", ["a meeting", "untitled", "untitled 3", "new document", "notes"])
def test_generic_placeholder_augments(stem):
    d = decide_rename(Path(f"inbox/{stem}.md"), "Q2 Strategy Review", is_existing_doc=False)
    assert d.action == RenameAction.AUGMENT
    assert stem in d.final_stem
    assert "Q2 Strategy Review" in d.final_stem

# Rule 4: illegible → full rename
@pytest.mark.parametrize("stem", [
    "xkdhgksjfs",            # keyboard mash
    "a1b2c3d4e5f6",          # hex hash
    "550e8400-e29b-41d4",    # UUID-ish
    "abc",                   # too-short single token
])
def test_illegible_full_renames(stem):
    d = decide_rename(Path(f"inbox/{stem}.pdf"), "Q2 Movies Deck", is_existing_doc=False)
    assert d.action == RenameAction.FULL_RENAME
    assert d.final_stem == "Q2 Movies Deck"

# Conservative default
def test_ambiguous_skips():
    # Single legible word, non-office ext, not generic, not illegible
    d = decide_rename(Path("inbox/forecast.pdf"), "Q2 Forecast", is_existing_doc=False)
    assert d.action == RenameAction.SKIP

# Edge: AUGMENT stem stays within max_len
def test_augment_respects_max_len():
    long_title = "A" * 200
    d = decide_rename(Path("inbox/meeting.md"), long_title, is_existing_doc=False)
    assert len(d.final_stem) <= 120
```

### 6.2 Integration tests: `tests/pipelines/test_capture_rename.py`

Three scenarios that exercise the full capture pipeline with a mocked LLM:

1. **Legible .md re-capture** — file `Q2 Strategy.md` already in documents, AI suggests "Different Title". Assert: file is not renamed, content is updated, audit entry has `outcome=SKIP`.

2. **Generic placeholder fresh capture** — drop `a meeting.md`, AI suggests "Phong Q2 Sync". Assert: file is renamed to `a meeting - Phong Q2 Sync.md`, audit entry has `outcome=AUGMENT`.

3. **Illegible binary drop** — drop `xkdhgksjfs.pdf`, AI suggests "Q2 Movies Deck". Assert: sibling `.md` is created as `Q2 Movies Deck.md`, attachment moves to `attachments/Q2 Movies Deck.pdf`, audit entry has `outcome=FULL_RENAME`.

---

## 7. Open Questions / Tradeoffs Already Decided

| Question | Decision | Why |
|---|---|---|
| Should "legible but unrelated to content" trigger full rename? | **No** | Requires semantic check — brings back the original problem. User can rename manually. |
| Should `office_extensions` and `max_stem_length` be in config or hardcoded? | **Module constants for v1** | Defer config migration until user needs to tune. Marked with TODO. |
| Should we use mtime, ctime, or DB lookup for "active working file"? | **DB lookup** | mtime can't distinguish fresh-drop from active-edit. DB lookup is unambiguous. |
| Should AUGMENT generate name via second LLM call? | **No** | Reuse the title from metadata stage. No extra cost, no extra latency. |
| Should `_store_md` and `_store_nonmd` share audit logic? | **Extract helper if duplication bothers you** | Both call paths are short — judgment call. |
| What if the gate disagrees with itself across runs (rule changes)? | **Out of scope for v1** | Audit log preserves history. If rules change, old decisions stay valid. |

---

## 8. Acceptance Criteria

The implementation is complete when:

- [ ] `core/rename_gate.py` exists, is importable, and `decide_rename` returns a `RenameDecision` for all four rule branches plus the conservative default.
- [ ] All unit tests in `tests/core/test_rename_gate.py` pass.
- [ ] `pipelines/capture.py` no longer renames files unconditionally based on `sanitized_stem != src.stem`. The rename decision flows through `decide_rename` first.
- [ ] Every rename gate decision writes an audit_log row with the correct `outcome`.
- [ ] Integration test: capturing the same `Q2 Strategy.md` file twice does not rename it on the second pass.
- [ ] Integration test: capturing `a meeting.md` with content about Q2 strategy produces a filename containing both "a meeting" and a topical phrase.
- [ ] Integration test: capturing `xkdhgksjfs.pdf` produces an attachment with a legible, AI-derived name.
- [ ] No new LLM calls were added to the metadata stage (verify via test that mocks the LLM provider and asserts call count == 1 per capture).
