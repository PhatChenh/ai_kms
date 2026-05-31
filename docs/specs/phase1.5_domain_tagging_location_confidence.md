# Phase 1.5 — Domain Tagging with Location Confidence

## Purpose

Today the capture pipeline assigns domain tags to every note, but it has no way to know whether a note is in the right folder. A file sitting in `Projects/Finance/` should carry a `domain/finance` tag — but the AI can't verify that fit unless it knows where the file lives. This phase threads the file's vault-relative path into the metadata prompt so the AI can judge location fitness, returns a `location_confidence` score alongside the tags, and writes `location_review: true` to frontmatter when that score falls below a configurable threshold. No files are moved automatically — this phase only surfaces misfits for human review.

---

## Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `to_vault_path(path)` | `vault/paths.py` | Converts an absolute `Path` to a vault-relative POSIX string | Feed into metadata prompt as `vault_relative_path` variable | shallow |
| `PROMPTS["extract_metadata"].render(...)` | `llm/prompt_loader.py` + `prompts/extract_metadata.yaml` | Renders a YAML prompt template with named variables | Add `vault_relative_path` variable here | deep |
| `NoteMetadata` | `vault/frontmatter.py:46` | Pydantic model for all frontmatter fields | Add `location_confidence` and `location_review` fields | deep |
| `_KNOWN_KEYS` | `vault/frontmatter.py:27` | Frozenset that controls which YAML keys are parsed onto `NoteMetadata` | Add the two new keys so they round-trip through parse/dumps | shallow |
| `MetadataResult` | `pipelines/capture.py:53` | Frozen dataclass carrying all AI metadata stage outputs | Add `location_confidence: float | None` field | shallow |
| `metadata()` stage | `pipelines/capture.py:180` | Stage 4 of capture pipeline — calls LLM, parses JSON, returns `MetadataResult` | Extend to pass vault path to prompt and extract `location_confidence` from LLM response | deep |
| `store()` / `_store_md()` / `_store_nonmd()` | `pipelines/capture.py:328+` | Write AI metadata to vault and DB | Read `location_confidence` from `MetadataResult` and gate-write `location_review` to `note_meta` | deep |
| `ConfidenceBand` + `config/thresholds.yaml` | `core/config.py:268`, `config/thresholds.yaml` | Config-driven threshold model; `.route()` maps score to decision | Add `location_confidence_min` to `thresholds.yaml`; pipeline reads it from config — never hardcodes a float | deep |
| `audit.write(...)` | `core/audit.py` | Writes every AI decision to the audit log | Write a new audit entry when `location_review: true` is set | deep |
| `write_note(path, body, meta, actor)` | `vault/writer.py` | Single vault write gate; checks `updated_by_human` | No change needed — spec calls this with enriched `NoteMetadata` | deep |

---

## Feature overview

When a file is captured, the metadata stage already asks the LLM for tags and a title. This phase adds two things to that exchange:

1. **Input: file location** — the vault-relative path (e.g. `Projects/Finance/Q1 notes.md`) is passed into the prompt so the LLM knows where the file lives.

2. **Output: location confidence** — the LLM returns a new field `location_confidence` (0.0–1.0) alongside the existing fields. This score reflects how well the file's content matches its folder. A finance report in `Projects/Finance/` should score high; a personal journal entry in `Projects/Finance/` should score low.

After the LLM responds, the pipeline reads `location_confidence`:

- If the file is outside `Projects/` or `Domain/` folders (e.g. `inbox/`, `Documentation/`, `Briefings/`) — confidence is `null`. No flag written. This is expected; inbox files have no expected location.
- If confidence is at or above `location_confidence_min` — file is in the right place; no flag.
- If confidence is below `location_confidence_min` — pipeline writes `location_review: true` to frontmatter and logs an audit entry. No move is made. The flag surfaces the file for human review in Phase 2.

```
LLM response JSON
 ├── title             (existing)
 ├── tags              (existing)
 └── location_confidence  (NEW — float 0.0–1.0, or null if not applicable)
          │
          ▼
   file in Projects/ or Domain/?
          │
     NO   │   YES
          │
   null   │   score >= location_confidence_min?
          │
          ├── YES → no flag (file is fine)
          │
          └── NO  → write location_review: true to frontmatter
                     write LOCATION_REVIEW audit entry
```

---

## Out of scope

- **Auto-moving misfit files** — Phase 2 owns moves. This phase only flags. Never deferred to the user; deferred to Phase 2.
- **Phase 2 surface logic** — reading `location_review: true` files and deciding what to show the user. Phase 2 Classify pipeline.
- **Reconcile stale `location_review` flags** — once a user moves a file and re-captures it, the flag should clear. Deferred — no phase assigned yet. Future spec needed.
- **Recapture idempotency (content-hash gate)** — the behavior_adjustment.md doc describes this separately. This spec does not implement the `SKIPPED` gate. That is a separate Phase 1.5 item.
- **Schedule policing / summary review with file position** — the design note mentions "summary review taken with the position of the file." That is a Phase 2+ concern; this spec only pipes location into the metadata stage, not the summary stage.

---

## Constraints

- **C-06: Thresholds in config/thresholds.yaml, never in code** — `location_confidence_min` must be a key in `thresholds.yaml`. No float literal may appear in `if`/`elif` inside `pipelines/`. Source: `CONSTRAINTS.md#C-06`, hook hard-block.
- **C-07: Prompts are YAML files** — the updated `extract_metadata.yaml` is the only place the new prompt variable and JSON field specification live. No inline prompt strings. Source: `CONSTRAINTS.md#C-07`.
- **C-12: Result type on all public pipeline functions** — `metadata()` and `store()` already return `Result`; any new helper function introduced must also return `Result`. Source: `CONSTRAINTS.md#C-12`.
- **C-13: Audit log every AI decision** — setting `location_review: true` is an AI decision; it must produce an audit entry. Source: `CONSTRAINTS.md#C-13`.
- **C-02: updated_by_human gate** — the new `location_review` field is written by AI; `write_note` already guards against overwriting human edits. No special handling needed, but the spec must not bypass `write_note`. Source: `CONSTRAINTS.md#C-02`.
- **C-03: write_note is a pure writer — pipeline owns the merge** — when writing `location_review: true`, the pipeline must call `read_note` first to preserve all existing metadata. Source: `CONSTRAINTS.md#C-03`.
- **Files outside Projects/ or Domain/ return null confidence** — the pipeline must handle `null` without error. Never treat `null` as `0.0` (that would flag every inbox note). Source: design decision in `behavior_adjustment.md`.

---

## Build order

### 1. Add `location_confidence_min` to `config/thresholds.yaml`

**Goal.** Establish the single config-authoritative threshold that the pipeline will read to decide whether to flag a file for location review.

**Build.** Add a new key `location_confidence_min: 0.70` under the `global:` block in `config/thresholds.yaml`. Add a corresponding field `location_confidence_min: float = Field(0.70, ge=0.0, le=1.0)` to `ConfidenceBand` in `core/config.py`. The YAML default (`0.70`) ensures existing configs load without error; the Pydantic default is a backup for programmatic construction only. No Python pipeline code may reference the literal `0.70` — the value lives in `thresholds.yaml` exclusively.

**Depends on.** None.

**Interface shape.** `CONFIG.thresholds.global.location_confidence_min` (or `CONFIG.thresholds.for_pipeline("capture").location_confidence_min`). Callers never compare against a float literal — they read this field.

**Done when.** `uv run python -c "from core.config import load_config; c = load_config(); print(c.thresholds.global_.location_confidence_min)"` prints `0.7` without error. (Note: `global` is a Python keyword; field may need alias `global_` or stored under `pipelines.capture` — resolve at planning time.)

---

### 2. Add `location_confidence` and `location_review` to `NoteMetadata`

**Goal.** Give the vault layer typed fields for the two new frontmatter keys so they round-trip correctly through parse and dumps.

**Build.**
- Add `"location_confidence"` and `"location_review"` to `_KNOWN_KEYS` in `vault/frontmatter.py`.
- Add `location_confidence: float | None = Field(default=None, ge=0.0, le=1.0)` to `NoteMetadata`.
- Add `location_review: bool = False` to `NoteMetadata`.
- The existing `_coerce_bool_to_str` validator applies to string-coercion fields only; `location_review` is a `bool` and does not need it.

**Depends on.** None (independent of step 1).

**Done when.** A `.md` file with frontmatter `location_confidence: 0.45\nlocation_review: true` parses into a `NoteMetadata` with `location_confidence=0.45` and `location_review=True`. A `NoteMetadata(location_confidence=0.45, location_review=True)` round-trips through `dumps()` and back to the same values.

---

### 3. Update `extract_metadata.yaml` prompt

**Goal.** Tell the LLM about the file's location and ask it to return `location_confidence` as a new JSON field.

**Build.** Edit `prompts/extract_metadata.yaml`:

- Add `vault_relative_path` to the `variables:` list (alongside existing `text`, `summary`, `domain_list`).
- In the `system:` block, add a new output field specification:
  - `"location_confidence"`: a float 0.0–1.0 indicating how well the note's content fits its current folder path. If the path is in `inbox/`, `Documentation/`, or `Briefings/` (i.e. has no expected domain/project), return `null`. If the path is in `Projects/<name>/` or `Domain/<name>/`, return a score reflecting content-folder fit.
- In the `user:` block, add `File path: {{ vault_relative_path }}` before the note content.

**Depends on.** None (prompt change is isolated).

**Decisions.**
- Q: Should the system prompt include the list of valid domains alongside the file path so the LLM can cross-check the domain tag against the folder? Options: A) yes — higher quality location judgment / B) no — domain_list already included, AI can infer. Leaning A because the cross-check is the whole point of passing the path.
- Q: Should `null` be returned only for the three non-domain/project folders, or for any path the LLM can't classify? Options: A) null only for known non-scoped paths, score 0.0 for others / B) null for any uncertain case. Leaning A to keep the pipeline's null-handling path narrow.

**Done when.** Rendering the prompt with `vault_relative_path="inbox/meeting notes.md"` produces a system prompt instructing the LLM to return `"location_confidence": null`. Rendering with `vault_relative_path="Projects/Finance/Q1 report.md"` produces a system prompt instructing the LLM to return a float.

---

### 4. Extend `MetadataResult` and `metadata()` stage

**Goal.** Pass the vault-relative path into the prompt and extract `location_confidence` from the LLM response.

**Build.**

- In `pipelines/capture.py`, add `location_confidence: float | None` field to the `MetadataResult` dataclass.
- In `metadata()` stage:
  - Compute `vault_relative_path = to_vault_path(sr.raw.source_path)` (already called later for audit; move or duplicate earlier).
  - Pass `vault_relative_path=vault_relative_path` to `PROMPTS["extract_metadata"].render(...)`.
  - In `_parse_metadata_json` (or in `metadata()` after parsing): extract `location_confidence` from the parsed dict. Accept `float`, `int`, or `null`/`None`. Clamp to `[0.0, 1.0]`. If the value is missing or not a number, default to `None` (treat as unknown — do not flag).
  - Populate `MetadataResult(..., location_confidence=location_confidence)`.

**Depends on.** Steps 1, 3.

**Done when.** When the LLM returns `{"title": "...", "tags": [...], "location_confidence": 0.45}`, `MetadataResult.location_confidence` is `0.45`. When LLM returns `{"location_confidence": null}`, field is `None`. When `location_confidence` key is absent entirely, field is `None`.

---

### 5. Gate and write `location_review` in `store()` / `_store_md()` / `_store_nonmd()`

**Goal.** Write `location_review: true` to frontmatter when confidence is below threshold, and audit log the decision.

**Build.**

In `pipelines/capture.py`, add a helper `_apply_location_review(mr: MetadataResult, note_meta: NoteMetadata, ctx: PipelineContext) -> Result[NoteMetadata]`:

- If `mr.location_confidence is None` → return `Success(note_meta)` unchanged (null = not applicable).
- Read `location_confidence_min` from `ctx.config.thresholds` (the field is guaranteed present — see Step 1). No Python fallback float. If the field is somehow absent at runtime, return `Failure("location_confidence_min missing from thresholds config", recoverable=False)`.
- If `mr.location_confidence >= threshold` → return `Success(note_meta)` unchanged.
- If `mr.location_confidence < threshold`:
  - Build updated `note_meta` via `note_meta.model_copy(update={"location_review": True, "location_confidence": mr.location_confidence})`.
  - Write audit entry: `outcome="LOCATION_REVIEW"`, `context={"location_confidence": mr.location_confidence}`.
  - On audit `Failure` → return that `Failure` (propagate; do not swallow).
  - On audit `Success` → return `Success(updated_note_meta)`.

Call `_apply_location_review` inside both `_store_md` and `_store_nonmd` after `note_meta` is assembled and before `write_note`. Callers must match on `Success`/`Failure`.

**Depends on.** Steps 1, 2, 4.

**Interface shape.** `_apply_location_review` is a private helper; callers are `_store_md` and `_store_nonmd` only. Returns `Result[NoteMetadata]` so audit failures propagate rather than being swallowed.

**Decisions.**
- **RESOLVED — C-06 fix:** No Python fallback float. `location_confidence_min` must be present in `thresholds.yaml` (Step 1 adds it). Missing at runtime → hard `Failure`. This eliminates the float literal that would trigger the hook.
- **RESOLVED — C-12 fix:** `_apply_location_review` returns `Result[NoteMetadata]` (not bare `NoteMetadata`) so audit write failures surface to callers rather than being swallowed silently.
- Q: Should `location_confidence` score itself also be written to frontmatter (not just `location_review`)? Options: A) yes — useful for Phase 2 to surface the score / B) no — binary flag is enough. Leaning A because the behavior_adjustment.md says AI returns `location_confidence` score and it is a Phase 2 input contract; Phase 2 needs the score to decide how urgently to surface it.

**Done when.** A `.md` file in `Projects/Finance/` with a low-scoring location confidence has frontmatter `location_review: true` and `location_confidence: 0.45` after capture. The audit log contains a `LOCATION_REVIEW` entry with the source vault_path. A file in `inbox/` with `location_confidence: null` has neither field in frontmatter. A file scoring above threshold has no `location_review` field.

---

### 6. Tests

**Goal.** Cover all branches: null confidence, score above threshold, score below threshold, missing confidence key in LLM response, missing threshold in config.

**Build.** In `tests/test_pipelines/test_capture.py` (or a new `test_capture_location.py`):

- Unit test `_parse_metadata_json` with `location_confidence` present as float, as `null`, and as absent.
- Unit test `_apply_location_review` directly: null in → `Success(unchanged)`; above threshold → `Success(unchanged)`; below threshold → `Success` with `location_review=True` set and audit entry written; missing config key (simulate absent field) → `Failure(recoverable=False)`.
- Integration-style test for `metadata()` stage: mock LLM response with `location_confidence: 0.45`; assert `MetadataResult.location_confidence == 0.45`.
- Integration-style test for `_store_md()`: mock `_apply_location_review`'s effect; assert `write_note` receives a `NoteMetadata` with `location_review=True` and `location_confidence=0.45`; assert audit log contains `LOCATION_REVIEW` entry.
- Test `NoteMetadata` round-trip (parse → dumps → parse) for both new fields.
- Follow `CONSTRAINTS.md#C-17`: no module-scope CONFIG import in tests. Pass explicit `db_path=tmp_path/"kb.db"` where needed.

**Depends on.** Steps 2, 4, 5.

**Done when.** `uv run pytest tests/ -m "not smoke"` passes with the new tests included. No regression in existing 650 tests.

---

## Handoff notes

- **Contract with Phase 2 (Classify pipeline):** Phase 2 reads `location_review: true` files from the vault or DB and surfaces them to the user (or auto-routes based on a higher-confidence decision). This phase's promise: every file with `location_confidence < threshold` has `location_review: true` AND `location_confidence: <score>` in frontmatter, AND a `LOCATION_REVIEW` audit log entry. Phase 2 must not assume the score exists without `location_review: true` being set.
- **Null confidence contract:** Files in `inbox/`, `Documentation/`, `Briefings/` return `location_confidence: null` from LLM. The pipeline must NOT write `location_review` for null inputs. Phase 2 will never see inbox files flagged by this mechanism.
- **`thresholds.yaml` key naming:** The `global:` block in `thresholds.yaml` is a Python reserved word. Investigate at planning time whether `ConfidenceBand` uses `global_` alias or whether the key is nested under `pipelines.capture`. Resolve before implementing step 1.
- **LLM prompt quality:** `location_confidence` scoring is only as good as the prompt. The spec asks for a float but real LLMs may return borderline-absurd scores for ambiguous notes. Suggest adding a brief regression test with a real LLM call (smoke-marked) after initial integration. See OQ-007 for similar prompt quality concern.
- **Suggested research before planning:** Read `src/core/config.py` lines 329–360 to understand how `for_pipeline` merges global + pipeline-specific thresholds — the new `location_confidence_min` field must work the same way. Also check whether `ConfidenceBand.route()` needs extension or whether the new threshold is a separate comparison (it is a separate field, not a third band in `route()`).
