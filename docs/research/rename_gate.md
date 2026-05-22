# Research: Rename Gate
_Last updated: 2026-05-22_

## Overview

The capture pipeline (`pipelines/capture.py`) renames files unconditionally whenever
the AI-generated title differs from the source filename. Because the LLM almost always
produces a different title than the source stem, this fires on nearly every capture —
including actively-edited notes and intentionally-named files. A deterministic pre-decision
gate inserted into the `store` stage (before the rename) would classify each file into
one of three lanes (SKIP / AUGMENT / FULL_RENAME) using Python rules only, with no new
LLM calls and no changes to the metadata prompt.

---

## Key Components

### The bug (two locations)

**`pipelines/capture.py:305–308` — `_store_md`:**
```python
sanitized_stem = _sanitize_title(mr.ai_title)
src = mr.raw.source_path
if sanitized_stem != src.stem:          # fires on nearly every capture
    dst = _find_rename_dst(...)
```

**`pipelines/capture.py:383` — `_store_nonmd`:**
```python
sanitized_stem = _sanitize_title(mr.ai_title) or src.stem  # always AI title
```

Both paths use the AI title unconditionally. There is no check for whether the
source filename is already legible, human-curated, or being actively edited.

### Existing infrastructure the gate can use

| Component | Location | Role |
|---|---|---|
| `documents.get_by_path(vault_path, db_path)` | `storage/documents.py:105` | Returns `Result[DocumentRow \| None]`. Already exists — no need to add it. |
| `to_vault_path(path)` | `vault/paths.py` | Converts absolute path → vault-relative string for DB lookup. Already imported in `capture.py`. |
| `AIDecision` | `core/confidence.py` | Audit envelope. Gate uses this for audit.write(). |
| `core.audit.write(decision, pipeline, stage, outcome, db_path)` | `core/audit.py` | Already used in capture pipeline; gate adds a second call per capture. |
| `CaptureConfig` | `core/config.py:197` | Nested pydantic model under `MainConfig.capture`. Gate config goes here. |

### What `scan_capture` does with modified files

`scan_capture` calls `capture_file` for both `added` and `modified` entries (lines 537–593).
Modified files are already in the `documents` table. Without a gate, every save-and-re-capture
of an active working file triggers a rename attempt. The gate's `is_existing_doc=True` path
(Rule 1 → SKIP) is the primary fix for this scenario — the most common source of unexpected
renames in practice.

---

## How It Works (proposed gate design)

The gate runs inside `_store_md` and `_store_nonmd`, after metadata is known but before
any rename is attempted. It is a **pure function** — no I/O, no LLM calls, no DB calls.
The calling code is responsible for providing `is_existing_doc`.

```
decide_rename(src, ai_title, is_existing_doc, config)
    │
    ├─ Rule 1: is_existing_doc=True  → SKIP   (re-capture of active working file)
    ├─ Rule 2: office ext + legible  → SKIP   (human named this intentionally)
    ├─ Rule 3: generic placeholder   → AUGMENT ({stem} - {ai_title})
    ├─ Rule 4: illegible / hash      → FULL_RENAME (replace with ai_title)
    └─ Default: ambiguous            → SKIP   (conservative; trust is fragile)
```

Classifiers:
- `_is_legible(stem)` — True if at least 2 word-like tokens (split on `-/_`) and no hash/UUID pattern
- `_is_generic(stem)` — True if matches `_GENERIC_NAMES` set (optionally with trailing counter)
- `_is_illegible(stem)` — True if hex hash, UUID, keyboard mash, or very short single token

Output:
```python
@dataclass(frozen=True)
class RenameDecision:
    action: RenameAction     # SKIP | AUGMENT | FULL_RENAME
    final_stem: str          # always set; callers use directly
    reason: str              # human-readable; goes to audit
    confidence: float        # 1.0 = deterministic; <1.0 = heuristic
```

---

## Edge Cases & Silent Failure Modes

### `_is_legible` single-word boundary
Files like `forecast.pdf` or `notes.md` have one word-token → `_is_legible` returns False
(requires ≥ 2 tokens). They fall through to `_is_illegible` (likely False too) → conservative
SKIP. This is correct behavior, but implementers must be aware the Rule 2 path requires
two words — a single legible word does not activate the office-doc trust shortcut.

### `_is_generic` vs `_is_legible` ordering matters
Rule 3 (generic) must run before Rule 4 (illegible). `"a meeting"` is generic AND has
only two tokens. If Rule 4 ran first, `_is_illegible("a meeting")` would return False
(no hash, no mash) and it would fall to SKIP. Correct order: Rule 3 → Rule 4 → default.

### AUGMENT truncation with long stems
`_build_augmented_stem` truncates the AI title so total ≤ `max_stem_length`. If the
original stem is already near the limit, the AI title contribution may be zero bytes.
Fallback: return original stem only (not the sanitized AI title). This is correct.

### Rollback path still applies after gate
The gate decides `final_stem`. If `final_stem != src.stem` (rename proceeds), the
existing rollback logic (`move_note` reverse on write failure) still applies — unchanged.
The gate does not touch the rollback mechanics.

### Non-md: `is_existing_doc` is always False
`scan_non_md_drops` only returns binaries that are NOT in `attachment/` yet (DECISION-018
+ scan logic). A binary is moved to `attachment/` on first capture, so it cannot be
re-processed. `is_existing_doc` for non-md is structurally always False — no DB lookup
needed. The consultant's `_store_nonmd` logic that checks the sibling's vault path is
solving a non-existent problem. Pass `is_existing_doc=False` always for non-md.

### Existing tests that will break
`test_store_md_different_title_renames_note` (test_capture_phase3.py:75) drops
`old-name.md` and expects rename to `New Title.md`. Under the gate: `old-name` is a
legible office doc (two tokens, `.md` extension) → Rule 2 → SKIP. The rename will NOT
happen. This test must be updated to use an illegible stem (e.g. `xkdhgksjfs.md`) to
exercise the FULL_RENAME path.

---

## Dependencies & Coupling

- `core/rename_gate.py` — new module; imports only `re`, `dataclasses`, `enum`, `pathlib`
- `core/config.py` — adds `RenameGateConfig` nested in `CaptureConfig`
- `config/config.yaml` — adds `rename_gate:` block under `capture:`
- `pipelines/capture.py` — calls `decide_rename` in `_store_md` (line ~305) and `_store_nonmd` (line ~383)
- `storage/documents.py` — `get_by_path` already exists; no changes needed
- `prompts/extract_metadata.yaml` — NO CHANGE. Title generation unchanged.
- `vault/writer.py`, `storage/documents.py` — downstream rename mechanics untouched

---

## Extension Points

| Component | Extensible How | What Blocks Extension Today |
|---|---|---|
| `_GENERIC_NAMES` frozenset | Move to `RenameGateConfig.generic_names: list[str]` | Currently module constant; user can't add "scratch", "wip" without a code change. Mark as TD. |
| `OFFICE_EXTENSIONS` | Already in `RenameGateConfig.office_extensions` (when properly configured) | Module constant in consultant's design; must be in config per CLAUDE.md rule. |
| `max_stem_length` | `RenameGateConfig.max_stem_length: int` | Same — must be in config. |
| Rule set itself | Closed — new rules require code changes | No protocol/registry for rule injection. Acceptable at v1; gate is not a classification system. |

---

## Open Questions

None that are blocking — the code provides sufficient signal to answer all design questions.

---

## Reference Project Patterns

Not applicable — the reference project (`knowledge-base-server`) does not implement a
filename rename gate. The pattern here is entirely specific to the Obsidian vault rename
workflow.

---

## Technical Debt Spotted

| ID | What | Action |
|---|---|---|
| TD-GATE-1 | `_GENERIC_NAMES` is a hardcoded frozenset in `core/rename_gate.py` | Migrate to `RenameGateConfig.generic_names: list[str]` in a follow-up. Document with `# TODO: migrate to RenameGateConfig.generic_names` comment. |
| TD-GATE-2 | `test_store_md_different_title_renames_note` tests the unconditional rename path | Must update stem to illegible filename to keep the FULL_RENAME path covered. |
| TD-GATE-3 | `metadata` stage hardcodes `confidence=0.9` in `AIDecision` | Pre-existing; out of scope for rename gate. |

---

## Pushbacks on the Consultant's Plan

The consultant's design is architecturally sound. Four corrections against the actual codebase:

### 1. `documents.get_by_path` already exists (consultant said "add it if missing")
`storage/documents.py:105–134` has `get_by_path(vault_path, db_path) -> Result[DocumentRow | None]`
with `readonly=True` connection. Already imported-compatible. No add needed.

### 2. `is_existing_doc` for `_store_nonmd` is always False — skip the DB lookup
The consultant's non-md integration checks the sibling vault path of the AI-titled note.
This is solving a non-problem: `scan_non_md_drops` structurally cannot return a binary
that has already been moved to `attachment/`. Non-md files are never re-processed.
Simplified integration: `decide_rename(src, mr.ai_title, is_existing_doc=False, config=...)`.

### 3. Module constants for `office_extensions` / `max_stem_length` violate CLAUDE.md
CLAUDE.md states: **"New threshold or rule → edit a config file. Do not hardcode it."**
This is a hard rule. `RenameGateConfig` must be added to `CaptureConfig` from day one,
not as a follow-up. The gate's signature must accept `config: RenameGateConfig`:
```python
def decide_rename(src, ai_title, is_existing_doc, config: RenameGateConfig) -> RenameDecision
```

### 4. The audit helper should be extracted from day one
The consultant calls it "optional." In this codebase, duplicating a 12-line audit block
across `_store_md` and `_store_nonmd` is not acceptable. Extract `_audit_rename_gate`
as a module-private helper before the plan step closes.

---

## Recommended Implementation Delta vs Consultant's Plan

| Consultant's Spec | Actual Implementation |
|---|---|
| `decide_rename(src, ai_title, is_existing_doc)` — no config param | `decide_rename(src, ai_title, is_existing_doc, config: RenameGateConfig)` |
| Module constants for `office_extensions`, `max_stem_length` | `RenameGateConfig` fields in `CaptureConfig`; YAML defaults in `config.yaml` |
| "Add `get_by_path` if missing" | Already exists — skip |
| `_store_nonmd` DB lookup for sibling vault path | `is_existing_doc=False` always; skip lookup |
| Audit helper "optional" | Extract `_audit_rename_gate` helper; required to avoid duplication |
| `tests/pipelines/test_capture_rename.py` (new file) | Also update `test_store_md_different_title_renames_note` in existing test file to use illegible stem |

---

## Config Changes Required

**`core/config.py` — add `RenameGateConfig` and update `CaptureConfig`:**
```python
class RenameGateConfig(BaseModel):
    office_extensions: list[str] = Field(
        default_factory=lambda: [".md", ".docx", ".xlsx", ".pptx", ".txt"]
    )
    max_stem_length: int = 120

class CaptureConfig(BaseModel):
    cooldown_seconds: int = Field(60, ge=0)
    max_urls_per_note: int = Field(3, ge=0)
    rename_gate: RenameGateConfig = Field(default_factory=RenameGateConfig)
```

**`config/config.yaml` — add under `capture:`:**
```yaml
capture:
  cooldown_seconds: 0
  max_urls_per_note: 3
  rename_gate:
    office_extensions: [".md", ".docx", ".xlsx", ".pptx", ".txt"]
    max_stem_length: 120
```
