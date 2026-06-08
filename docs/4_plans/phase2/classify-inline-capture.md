# Plan: Classify Inline in Single-File Capture (P2-CIC)

_Date: 2026-06-08_
_Feature: P2-CIC -- Phase 2 Classify, inline in single-file capture_
_Spec: `docs/2_specs/phase2/classify-inline-capture.md` (9 components, C1--C9)_
_Research: `docs/3_research/phase2/classify-inline-capture.md` (15 assumptions validated/resolved)_
_Design: `docs/1_design/phase2/classify-inline-capture.md`_

**Q1, Q2, Q3 diagrams are in the design and spec docs -- see those for visual architecture.**

**Overall goal.** A note or attachment dropped loose in the inbox is captured, classified by the AI (project + primary domain), and either auto-filed to the right folder or left in inbox with a recorded candidate for human review. Files already in a project/domain folder are still summarized and stamped but never re-classified or moved. The whole-folder path is untouched in Phase 1; Phase 2 (component 9) migrates it onto the same engine.

**Key facts (from spec + research):**

- P2-CL (the classify engine) is already implemented on `main` -- component 6 is a reshape, not greenfield
- All 15 assumptions validated or resolved; no remaining invalidations
- All 5 OQs resolved (registry on context, retry numbers, status vocabulary, tag stamping, `_store_nonmd` refactor)
- The stage list is `[extract, enrich_urls, summarize, metadata, apply_location_tags, store]` at `capture.py:977`; the new classify step inserts after `apply_location_tags`, before `store`
- `MetadataResult` is `frozen=True` -- use `dataclasses.replace` when mutating for the AUTO path
- `_store_md` only renames within `src.parent`; cross-folder `.md` moves need the classify step to do its own `move_note` + `replace_path`
- `_store_nonmd` hard-derives destination as local vars (lines 577-589); needs optional params added

---

## Phase 1: Foundation -- candidate frontmatter fields + PipelineContext extensions

**Goal (plain English).** Give the system typed places to record the AI's classification guess on a note, and give the pipeline context the signals it needs (a suppress flag for folder-invoked capture, and access to the live project registry).

**Spec components:** C2 (candidate frontmatter), C3 (SUPPRESS signal + OQ-CIC-A registry field).

**Files to touch:**

| File | What changes |
|------|-------------|
| `src/vault/frontmatter.py` | Add 4 optional fields to `NoteMetadata`; update `_KNOWN_KEYS`; update `_coerce_bool_to_str` validator |
| `src/core/pipeline.py` | Add `skip_classify: bool = False` and `registry: "LiveRegistry | None" = None` to `PipelineContext` |

**Tests first (write these before the code):**

1. **`test_candidate_fields_round_trip`** -- Create a note with `suggested_project`, `suggested_primary_domain`, `classify_confidence`, `classify_reasoning` in frontmatter. Parse it with `parse()`, verify all 4 fields are on the `NoteMetadata` object. Then `dumps()` it back and verify all 4 keys survive the round-trip (proves `_KNOWN_KEYS` is updated).
2. **`test_candidate_fields_default_none`** -- Parse a note with no candidate fields. Verify all 4 default to `None`.
3. **`test_coerce_bool_to_str_on_candidate_strings`** -- Parse a note where `suggested_project` has YAML value `yes` (which PyYAML 1.1 reads as `True`). Verify the model coerces it to the string `"yes"`, not boolean `True`.
4. **`test_classify_confidence_bounds`** -- Verify `classify_confidence` accepts 0.0 and 1.0, rejects -0.1 and 1.1 (Pydantic `ge=0.0, le=1.0`).
5. **`test_pipeline_context_skip_classify_default_false`** -- Construct `PipelineContext(config=mock, correlation_id="x")`. Verify `skip_classify` is `False`.
6. **`test_pipeline_context_registry_default_none`** -- Same construction. Verify `registry` is `None`.
7. **`test_pipeline_context_accepts_registry`** -- Pass a mock `LiveRegistry` via `registry=mock_reg`. Verify it is stored.

**Success criteria:** P2-CIC-02 fields exist (suggested_project, suggested_primary_domain, classify_confidence, classify_reasoning), P2-CIC-03 fields exist (same set, with None for CLUELESS). Round-trip proven. Context extensions wired.

**Dependencies:** None -- this is the first phase.

**Risk/notes:**

- The `classify_confidence` field on `NoteMetadata` uses the same name as the existing `confidence` field. Distinguish: `confidence` is the capture-stage AI confidence (title/summary), `classify_confidence` is the classify-stage confidence. The spec chose separate names deliberately.
- Remember to add the three new string fields (`suggested_project`, `suggested_primary_domain`, `classify_reasoning`) to the `_coerce_bool_to_str` validator at `frontmatter.py:71`. Do NOT add `classify_confidence` (it is a float, not a string).
- Use `TYPE_CHECKING` import for `LiveRegistry` in `pipeline.py` to avoid circular imports; use string annotation `"LiveRegistry | None"`.

---

## Phase 2: Subject Builder -- normalize a note into one classify input block

**Goal (plain English).** Build a tiny helper that turns a note's title, summary, and tags into one short text block. This is what the AI prompt sees -- one shape, regardless of whether the input is a note or (later) a folder.

**Spec component:** C1.

**Files to touch:**

| File | What changes |
|------|-------------|
| `src/pipelines/classify.py` | Add `build_subject(title, summary, tags) -> str` pure function |

**Tests first:**

1. **`test_build_subject_all_fields`** -- Pass title="Report Q3", summary="Quarterly financials...", tags=["finance", "domain/treasury"]. Verify the output string contains all three pieces in a predictable format.
2. **`test_build_subject_empty_tags`** -- Pass tags=[]. Verify the output omits the tags line (no "Tags: " with nothing after it) or handles it gracefully.
3. **`test_build_subject_empty_summary`** -- Pass summary="". Verify the output omits the summary line.
4. **`test_build_subject_none_summary`** -- Pass summary=None. Verify no crash, no "None" literal in output.
5. **`test_build_subject_truncation`** -- Pass a very long summary (5000+ chars). Verify the output truncates to a reasonable limit (the prompt token budget should not be blown by one note).

**Success criteria:** The `subject` string feeds cleanly into the prompt template. Verifiable by checking `build_subject` output against the expected template structure.

**Dependencies:** None -- standalone pure function. But Phase 3 uses it.

**Risk/notes:**

- The spec says "if Phase 2 (folder migration) is deferred indefinitely, this is a speculative 1-adapter seam." Keep the function simple -- a formatted string, not an overengineered abstraction. It earns its keep when the folder caller lands in Phase 9.
- Location: put it in `pipelines/classify.py` alongside the engine, since the prompt shape and the subject shape must agree.
- Tags should be serialized as a comma-separated or newline-separated list. Match whatever the current `classify.yaml` prompt expects (the reshape in Phase 3 will update both together -- but establish the convention here).

---

## Phase 3: Classify Engine reshape -- subject-shaped input, derive-from-tags output

**Goal (plain English).** Change the existing classify engine so it takes a single `subject` text block (instead of separate title/summary/tags) and returns the AI's project assignment + domain tags + primary domain (instead of a free folder name). This is the engine all callers share.

**Spec component:** C6.

**Files to touch:**

| File | What changes |
|------|-------------|
| `src/pipelines/classify.py` | Reshape `classify()` signature: `(subject, valid_destinations, config)`. Reshape `ClassifyResult`: replace `target_type`/`target_name` with `project`/`domains`/`primary_domain`. Update validation logic. |
| `src/prompts/classify.yaml` | Replace `title`/`summary`/`tags` vars with single `subject` var. Change output JSON contract to `project`/`domains`/`primary_domain`/`confidence`/`reasoning`. |
| `tests/test_pipelines/test_classify.py` | Update all P2-CL unit tests (P2-CL-01 through P2-CL-06) to the new signature and result shape |

**Tests first (update existing + add new):**

1. **Update P2-CL-01** -- Template renders with a single `subject` var + `valid_destinations`.
2. **Update P2-CL-02** -- Provider failure returns `Failure(recoverable=True)`.
3. **Update P2-CL-03** -- Valid JSON with `project`, `domains`, `primary_domain`, `confidence`, `reasoning` returns `Success(ClassifyResult)` with new fields.
4. **Update P2-CL-04** -- Malformed JSON returns `Failure(recoverable=True)`.
5. **Update P2-CL-05** -- Missing required fields returns `Failure(recoverable=True)`.
6. **Update P2-CL-06** -- Template render error returns `Failure(recoverable=False)`.
7. **New: `test_classify_result_project_none`** -- AI returns `project: null`, `domains: ["finance"]`, `primary_domain: "Finance"`. Verify `ClassifyResult.project is None` and `primary_domain == "Finance"`.
8. **New: `test_classify_result_validates_project_against_destinations`** -- AI returns a project name NOT in `valid_destinations`. Verify `Failure(recoverable=True)`.
9. **New: `test_classify_result_validates_primary_domain`** -- AI returns a `primary_domain` NOT in `valid_destinations`. Verify `Failure(recoverable=True)`.
10. **New: `test_classify_result_no_project_no_domain`** -- AI returns both null. Verify the function still returns `Success(ClassifyResult)` -- the CLUELESS routing is the caller's job, not the engine's.

**Success criteria:** P2-CL unit tests pass with new signature. The engine is a pure function that knows nothing about files, moves, or audit -- only the AI call and parse. ClassifyResult carries `project`, `domains`, `primary_domain`, `confidence`, `reasoning`.

**Dependencies:** Phase 2 (Subject Builder -- the engine assumes `subject` is pre-built by the caller).

**Risk/notes:**

- P2-CL is already on `main` (commits `b2d33fa` + `a28b33c`). This phase modifies existing code. Back up the old test expectations mentally before changing them.
- The prompt change is the most delicate part. The new prompt must instruct the AI to: (a) pick a project from the valid_destinations list or say null; (b) confirm/reuse domain tags from the subject; (c) designate one primary domain; (d) return confidence 0.0--1.0 and reasoning. No `{% if %}` branches (constraint C-07).
- Validation: `project` when set must appear in `valid_destinations`. `primary_domain` when set must also appear. But `domains` items do NOT need to appear in destinations (they are tag-level, not folder-level). Be precise.
- The `domains` field on `ClassifyResult` is `list[str]` -- the AI confirms which domain tags apply. This replaces the old `target_type: "domain"` + `target_name: "<domain>"` pattern.

---

## Phase 4: Filer refactors -- `_store_nonmd` optional params + `.md` cross-folder move prep

**Goal (plain English).** Make the existing filing code accept an externally-derived destination, so the classify step can tell the filer "put this in Projects/Alpha" rather than having it guess from the source path.

**Spec component:** C5 (partial -- the refactors that enable the AUTO move, but not the classify step wiring).

**Files to touch:**

| File | What changes |
|------|-------------|
| `src/pipelines/capture.py` (`_store_nonmd`) | Add optional `target_type: str | None = None` and `target_name: str | None = None` params. When not None, skip the inline path-derivation (lines 577-589) and use the provided values. |
| No changes to `_store_md` | The `.md` cross-folder move is done by the classify step itself (Phase 5), not by modifying `_store_md`. |

**Tests first:**

1. **`test_store_nonmd_default_params_unchanged`** -- Call `_store_nonmd` without the new params (existing callers' behavior). Verify the path-derived destination is used. This is a regression guard -- every existing `_store_nonmd` test still passes.
2. **`test_store_nonmd_with_external_target`** -- Call `_store_nonmd` with `target_type="project"`, `target_name="Alpha"`. Verify the binary ends up in `Projects/Alpha/attachment/` and the sibling in `Projects/Alpha/attachment/.summaries/`. (Uses test vault fixture.)
3. **`test_store_nonmd_external_target_overrides_path`** -- Place a binary in `inbox/`, call with `target_type="domain"`, `target_name="Finance"`. Verify it goes to `Domain/Finance/attachment/`, NOT to inbox.
4. **`test_store_nonmd_external_target_sibling_db_row`** -- After an external-target store, verify `documents.vault_path` is the new sibling path and `documents.attachment_path` is the new binary path in the target folder.

**Success criteria:** All existing `_store_nonmd` tests pass unchanged (regression). New tests prove the optional params override the path-derived destination.

**Dependencies:** None (pure refactor of existing code). But Phase 6 depends on this.

**Risk/notes:**

- This is a surgical refactor. The only code change inside `_store_nonmd` is: if the optional params are not None, skip lines 577-589 and assign `target_type, target_name` from the params. The entire LOCATED branch (rename gate, sibling write, move_guard, move_attachment, upsert) runs exactly as before.
- The `_store_nonmd` function signature is private (single underscore). But it is called from `store()` and will be called from the classify step -- if the classify step needs to pass targets through `store`, the `store` function also needs to forward them. Decide: (a) classify step calls `_store_nonmd` directly for AUTO binary, or (b) `store` gets the params too. Recommendation: (a) is simpler -- the classify step can call `_store_nonmd` directly because it is in the same module. But this means the classify step must also handle the md/nonmd dispatch for AUTO. Weigh during implementation.
- Do NOT modify `_store_md` for cross-folder moves. The spec explicitly says the classify step does its own `move_note` + `replace_path` for `.md` AUTO, then hands the already-moved note to `store` for in-place write + upsert.

---

## Phase 5: Classify Step -- the new pipeline stage

**Goal (plain English).** This is the main event: a new step in the capture pipeline that, for loose inbox files only, asks the AI to classify the note, routes through the confidence gate, and either stamps tags + derives the destination (AUTO) or records the candidate and leaves the file (SUGGEST/CLUELESS). Files already in a folder or under SUPPRESS are passed through untouched.

**Spec component:** C4.

**Files to touch:**

| File | What changes |
|------|-------------|
| `src/pipelines/capture.py` | Add the `classify_step` async function. Insert it into the stage list at line 977 between `apply_location_tags` and `store`. |
| `src/core/audit.py` | No change -- reuse existing `write()` |
| `src/vault/registry.py` | No change -- reuse existing `build_registry`, `LiveRegistry`, `format_for_prompt` |

**Tests first:**

Write these in a new test file `tests/test_pipelines/test_classify_step.py`. All tests stub the AI provider -- no real LLM calls.

1. **`test_classify_step_suppress_passthrough`** -- `ctx.skip_classify = True`. Verify the step returns the input `MetadataResult` unchanged, no AI call, no audit row.
2. **`test_classify_step_located_project_passthrough`** -- File is in `Projects/Alpha/`. Verify the step returns unchanged, no AI call.
3. **`test_classify_step_located_domain_passthrough`** -- File is in `Domain/Finance/`. Same check.
4. **`test_classify_step_inbox_auto_project`** -- Loose inbox `.md`, AI returns project="Alpha", confidence=0.9, primary_domain="Finance". Verify: `ai_project` is set on the returned `MetadataResult`, domain tags include "domain/finance", no `suggested_*` fields written, one audit row with `outcome=AUTO`.
5. **`test_classify_step_inbox_auto_domain_only`** -- AI returns project=None, primary_domain="Finance", confidence=0.9. Verify: `ai_project` stays None, primary domain is used for routing, one AUTO audit row.
6. **`test_classify_step_inbox_suggest`** -- AI returns confidence=0.7 (SUGGEST band). Verify: file stays at inbox path, `suggested_project`/`suggested_primary_domain`/`classify_confidence`/`classify_reasoning` written to frontmatter, `status=needs-review`, one SUGGEST audit row.
7. **`test_classify_step_inbox_clueless`** -- AI returns confidence=0.4 (CLUELESS band). Verify: file stays, `suggested_project=None`, `suggested_primary_domain=None`, `status=needs-review`, `classify_reasoning` present, one CLUELESS audit row.
8. **`test_classify_step_no_project_no_domain_is_clueless`** -- AI returns project=None, domains=[], primary_domain=None, confidence=0.95. Verify: treated as CLUELESS regardless of confidence (locked OQ-CIC-4).
9. **`test_classify_step_project_beats_domain`** -- AI returns project="Alpha", primary_domain="Finance". Verify: destination is `Projects/Alpha/`, not `Domain/Finance/`. (P2-CIC-11)
10. **`test_classify_step_multi_domain_primary_routing`** -- AI returns project=None, domains=["finance", "legal"], primary_domain="Finance". Verify: destination is `Domain/Finance/`, all domain tags kept. (P2-CIC-12)
11. **`test_classify_step_retry_then_clueless`** -- Stub `classify()` to fail `recoverable=True` 3 times. Verify: falls back to CLUELESS-style candidate with reasoning mentioning "failed after 3 attempts". (OQ-CIC-B / TD-048)
12. **`test_classify_step_render_error_no_retry`** -- `classify()` returns `Failure(recoverable=False)`. Verify: no retry, immediate CLUELESS fallback.
13. **`test_classify_step_registry_from_context`** -- Pass `ctx.registry = mock_live_registry`. Verify: `mock_live_registry.get_groups()` is called instead of `build_registry`.
14. **`test_classify_step_registry_none_fallback`** -- Pass `ctx.registry = None`. Verify: `build_registry(vault_cfg)` is called as one-shot fallback.

**Success criteria:** P2-CIC-01 (AUTO project), P2-CIC-02 (SUGGEST), P2-CIC-03 (CLUELESS), P2-CIC-04 (LOCATED passthrough), P2-CIC-06 (SUPPRESS passthrough), P2-CIC-11 (project beats domain), P2-CIC-12 (multi-domain primary routing).

**Dependencies:** Phase 1 (context extensions + candidate fields), Phase 2 (Subject Builder), Phase 3 (reshaped classify engine).

**Risk/notes:**

- This is the largest phase. It wires together all the foundation pieces.
- The retry loop (3 attempts, 1s base exponential backoff) lives HERE, not in `classify()`. On exhaustion, fall back to CLUELESS-style recorded-candidate. Never fail the whole capture.
- The classify step reads `MetadataResult.ai_tags`, `ai_domain`, `ai_title`, `summary` to build the subject. For AUTO, it stamps `ai_project` and ensures domain tags are consistent on `MetadataResult` before returning -- `store` builds `NoteMetadata` from these fields.
- For SUGGEST/CLUELESS, the classify step does a read-before-write: `read_note(path)` to get existing frontmatter, then `write_note` with the candidate fields merged. This respects constraint C-03.
- The stage returns `Result[MetadataResult]` -- on AUTO it returns the modified `MetadataResult` (with `ai_project` set and potentially updated source path for `.md`), on SUGGEST/CLUELESS it returns the original `MetadataResult` so `store` handles the in-place write as today.

---

## Phase 6: AUTO move handoff -- route derived destination through existing filer

**Goal (plain English).** When the classify step says AUTO, actually move the file to the right folder and keep the database in sync. This phase wires the classify step's output to the existing move/file infrastructure.

**Spec component:** C5 (the integration part -- the refactors were Phase 4).

**Files to touch:**

| File | What changes |
|------|-------------|
| `src/pipelines/capture.py` (classify step) | AUTO branch: for `.md`, do `move_note` + `replace_path` + update `MetadataResult` source path; for binary, pass `target_type`/`target_name` to `_store_nonmd` |
| `src/vault/move_guard.py` | No change -- reuse `get_active().register(dst)` |

**Tests first:**

1. **`test_auto_md_moves_to_project_folder`** -- Loose inbox `.md`, AUTO with project="Alpha". Verify: file physically exists at `Projects/Alpha/<name>.md`, not at inbox path. `documents.vault_path` is the new path. No orphan row at old path.
2. **`test_auto_md_moves_to_domain_folder`** -- Loose inbox `.md`, AUTO with project=None, primary_domain="Finance". Verify: file at `Domain/Finance/<name>.md`.
3. **`test_auto_md_move_guard_registered`** -- After an AUTO `.md` move, verify `move_guard.register` was called with the destination path before the move.
4. **`test_auto_md_replace_path_atomic`** -- After the move, verify exactly 1 `documents` row exists for the note (no duplicate at old + new path).
5. **`test_auto_binary_routes_through_store_nonmd`** -- Loose inbox PDF, AUTO with project="Alpha". Verify: binary at `Projects/Alpha/attachment/report.pdf`, sibling at `Projects/Alpha/attachment/.summaries/report.pdf.md`, DB row `vault_path` = sibling, `attachment_path` = binary.
6. **`test_auto_binary_batch_id_null`** -- After an AUTO move to project root, verify `batch_id` on the documents row is NULL. (P2-CIC-07 / R3)
7. **`test_auto_human_locked_fallback`** -- Loose inbox `.md` with `updated_by_human=true`. AUTO triggered but `move_note` returns `Failure(recoverable=False)`. Verify: file stays in inbox, candidate fields written (SUGGEST-style), no crash. (R5)
8. **`test_auto_md_metadata_result_updated`** -- After an AUTO `.md` move, verify the `MetadataResult` returned to `store` has `raw.source_path` pointing to the new location (via `dataclasses.replace`).

**Success criteria:** P2-CIC-05 (binary AUTO), P2-CIC-07 (DB consistency + batch_id NULL), P2-CIC-13 (structural consistency -- folder matches tags). R5 (human-locked fallback).

**Dependencies:** Phase 4 (`_store_nonmd` optional params), Phase 5 (classify step exists to drive the AUTO branch).

**Risk/notes:**

- The `.md` AUTO move is the trickiest part. Sequence: (1) register destination with move_guard, (2) `move_note(src, dst, actor="ai")`, (3) `documents.replace_path(old_vault_path, new_vault_path)`, (4) `dataclasses.replace(mr, raw=dataclasses.replace(mr.raw, source_path=dst))` to update the MetadataResult, (5) return the updated MetadataResult so `store` does an in-place write + upsert at the new location.
- If `move_note` fails for any reason other than human-lock, it is an unexpected error. Log it, fall back to CLUELESS-style candidate, do NOT fail the capture.
- For binary AUTO, the classify step sets the target on `MetadataResult` or passes it to `_store_nonmd`. The simplest approach: the classify step adds two transient fields to a wrapper or passes them as function args. Since the phase list in `run_pipeline` chains stages, and `store` dispatches to `_store_md`/`_store_nonmd`, either (a) add optional attrs to `MetadataResult` for the target, or (b) have the classify step call `_store_nonmd` directly and return the result, bypassing `store`. Option (b) means the classify step handles the md/nonmd dispatch for AUTO only. Weigh at implementation time -- option (a) is cleaner if `MetadataResult` can carry optional routing hints.

---

## Phase 7: Idempotent re-entry -- retire pending-routing, guard re-classify

**Goal (plain English).** Remove the old "pending-routing" early-exit (which was a placeholder for Phase 2 classify) now that classify runs at drop time. Ensure a second capture of an unchanged file does not re-classify it.

**Spec component:** C7.

**Files to touch:**

| File | What changes |
|------|-------------|
| `src/pipelines/capture.py` (lines 860-872) | Remove the `pending-routing` early-exit guard |
| `src/pipelines/capture.py` (CLUELESS branch of `_store_nonmd`) | The CLUELESS marker body (`_Pending classification...`) is superseded -- the inbox branch now writes a real summary + recorded candidate. Verify/update the marker text. |
| `src/pipelines/capture.py` (`_capture_folder_files`, `capture_folder` CLUELESS loop) | Set `skip_classify=True` on the per-file context |

**Tests first:**

1. **`test_pending_routing_guard_removed`** -- Create an inbox binary with a sibling that has `status: pending-routing`. Run `capture_file`. Verify: the pipeline runs normally (no early-exit), the file gets classified. (Previously this would have short-circuited.)
2. **`test_unchanged_needs_review_note_idempotent`** -- Capture a loose `.md` note that gets SUGGEST (needs-review). Run `capture_file` again without changing the note. Verify: the content-hash guard short-circuits it, no second classify AI call, audit row count stays at 1.
3. **`test_unchanged_needs_review_binary_idempotent`** -- Same for a loose binary with a needs-review sibling. The `source_hash` guard short-circuits. No second classify call.
4. **`test_folder_clueless_loop_sets_suppress`** -- Drop a folder in inbox, folder classify returns CLUELESS. Verify: each file in the folder is captured with `ctx.skip_classify=True`, so no per-file classify call and no `suggested_*`/`needs-review` on individual files.
5. **`test_capture_folder_files_sets_suppress`** -- The `_capture_folder_files` helper (used by project/domain drop and AUTO folder) sets `skip_classify=True`. Verify: files captured under this helper have no classify step invocation.
6. **`test_clueless_binary_marker_superseded`** -- A loose binary that gets CLUELESS now has a real summary body (from the summarize step) plus `status: needs-review`, NOT the old `_Pending classification...` placeholder.

**Success criteria:** P2-CIC-08 (re-entry idempotency under `kms watch`), P2-CIC-06 (SUPPRESS -- folder files have no per-file classify). The old pending-routing concept is fully retired.

**Dependencies:** Phase 1 (SUPPRESS signal on context), Phase 5 (classify step must exist before its absence under SUPPRESS is testable).

**Risk/notes:**

- The CLUELESS loop in `capture_folder` (lines 1558-1564) calls `capture_file(f, ctx_with_batch)`. The fix is: `ctx_with_suppress = replace(ctx_with_batch, skip_classify=True)` and pass that instead.
- `_capture_folder_files` (used by project/domain drops AND AUTO folder moves) also calls `capture_file`. Those files are already LOCATED (they are in a project/domain folder after the move), so the classify step would see them as LOCATED and skip. Setting SUPPRESS is belt-and-suspenders but makes the intent explicit. Recommend setting it.
- Removing the `pending-routing` guard (lines 860-872) is safe because the classify step replaces its function. The idempotency guards at lines 877-940 are the real protection against re-processing unchanged files.

---

## Phase 8: TD-049 NFC fix -- normalize folder_path identically on both batch paths

**Goal (plain English).** Fix a latent bug where a folder with a non-ASCII name (like Vietnamese accented characters) could create duplicate batch rows because one code path normalizes the folder name and the other does not.

**Spec component:** C8.

**Files to touch:**

| File | What changes |
|------|-------------|
| `src/pipelines/capture.py` (`capture_folder`) | NFC-normalize `folder_path` at all 4 `_insert_batch` call sites (lines ~1456, ~1510, ~1534, ~1556) |
| Optionally `src/vault/paths.py` | Add a `to_folder_path(path, root) -> str` shared helper that does `unicodedata.normalize("NFC", str(path.relative_to(root).as_posix()))` |

**Tests first:**

1. **`test_folder_path_nfc_normalized_on_insert`** -- Create a folder with a decomposed Unicode name (e.g. `"Phât"` -- NFD form of "Phat"). Run `capture_folder`. Verify: the `batches.folder_path` column value is NFC-normalized (matches `unicodedata.normalize("NFC", ...)`).
2. **`test_folder_path_nfc_matches_batch_lookup`** -- After inserting a batch row with a decomposed folder name, run `capture_file` on a file inside that folder. Verify: the batch-stamp lookup finds the existing batch row (no duplicate created). This is the end-to-end proof the mismatch is fixed.
3. **`test_to_folder_path_helper`** (if shared helper created) -- Unit test: given a decomposed path, returns NFC string. Given an already-NFC path, returns the same string.

**Success criteria:** TD-049 resolved. A folder with decomposed non-ASCII characters creates exactly ONE batch row, and every file inside it gets the same `batch_id`.

**Dependencies:** None (independent bug fix). But it benefits SUPPRESS reliability (files in a folder batch must find the folder's batch row).

**Risk/notes:**

- The 4 `_insert_batch` call sites in `capture_folder` currently pass `folder_path=str(folder_path.relative_to(vault_cfg.root).as_posix())` with no NFC. The fix is mechanical: wrap each in `unicodedata.normalize("NFC", ...)` or call the shared helper.
- A shared helper (`to_folder_path`) is preferred over inline `normalize` at 4 sites -- it makes drift structurally impossible. But do not over-engineer it.

---

## Phase 9: [P2] Folder migration -- unified prompt for folder classify

**Goal (plain English).** Move the whole-folder classify path onto the same engine and prompt as single-file classify, then delete the separate folder prompt. One prompt, one engine. All 956+ folder tests must stay green.

**Spec component:** C9.

**Files to touch:**

| File | What changes |
|------|-------------|
| `src/pipelines/classify.py` (`build_subject`) | Add a folder-subject adapter: `build_folder_subject(folder_name, file_manifest) -> str` |
| `src/pipelines/capture.py` (`capture_folder` Case A) | Replace `PROMPTS["classify_folder"].render(...)` + `_parse_classify_json` with: build folder subject, call `classify(subject, valid_destinations, config)`, read `ClassifyResult` |
| `src/prompts/classify_folder.yaml` | Delete this file |
| `src/pipelines/capture.py` | Remove `_build_vault_context` and `_parse_classify_json` if no other callers remain |

**Tests first:**

1. **`test_folder_classify_uses_unified_engine`** -- Drop a folder in inbox. Verify: `classify()` is called (not the old `classify_folder` prompt), and the result is a `ClassifyResult` with `project`/`domains`/`primary_domain`.
2. **`test_folder_subject_contains_name_and_manifest`** -- `build_folder_subject("Q3 Reports", "report.pdf\nsummary.md")` contains both the folder name and the file list.
3. **`test_folder_classify_auto_routes_correctly`** -- Folder classify AUTO: folder moves to `Projects/<project>/` (same as today). Verify destination derivation matches the derive-from-tags model.
4. **`test_folder_classify_suggest_unchanged`** -- Folder classify SUGGEST: batch row created with PENDING_REVIEW status, no folder move. Same behavior as today.
5. **`test_folder_classify_clueless_unchanged`** -- Folder classify CLUELESS: per-file markers written through the CLUELESS loop (which sets SUPPRESS). Same behavior as today.
6. **`test_classify_folder_yaml_deleted`** -- Verify the file `prompts/classify_folder.yaml` does not exist after this phase.
7. **Regression: run the full 956+ folder test suite.** Every existing folder test must pass. This is the real guardrail.

**Success criteria:** One prompt, one engine. `classify_folder.yaml` deleted. All folder tests green. The folder path now uses the same `classify()` + `ClassifyResult` as single-file.

**Dependencies:** Phase 2 (Subject Builder), Phase 3 (reshaped engine). All of P1 must be complete.

**Risk/notes:**

- This is the highest-risk phase because 956+ folder tests are the guardrail. Run the full suite after every change.
- The folder prompt currently returns 3 fields (no `reasoning`). The unified engine returns 5 fields including `reasoning`. The folder path should accept and log `reasoning` -- it is new information that was not available before.
- `_build_vault_context` builds a string from `vault_cfg.projects_path` / `domain_path`. If `format_for_prompt` from the registry already covers this, `_build_vault_context` is redundant. Verify before deleting.
- `_parse_classify_json` parses the old 3-field response. The new `classify()` handles its own parsing. `_parse_classify_json` can be deleted if no other caller uses it.
- Mark this phase as **clearly separate** from P1 phases. It can be deferred without blocking P1 delivery.

---

## Execution order summary

```
Phase 1: Foundation (C2, C3)         -- no dependencies
Phase 2: Subject Builder (C1)        -- no dependencies
Phase 3: Engine reshape (C6)         -- depends on Phase 2
Phase 4: Filer refactors (C5 prep)   -- no dependencies
Phase 5: Classify Step (C4)          -- depends on Phases 1, 2, 3
Phase 6: AUTO move handoff (C5)      -- depends on Phases 4, 5
Phase 7: Idempotent re-entry (C7)    -- depends on Phases 1, 5
Phase 8: TD-049 NFC fix (C8)         -- no dependencies (can run anytime)
Phase 9: [P2] Folder migration (C9)  -- depends on Phases 2, 3 (all of P1)
```

Phases 1, 2, 4, and 8 are independent and can be built in parallel.
Phases 3 depends on 2.
Phase 5 is the convergence point -- it needs 1, 2, and 3.
Phase 6 is the integration phase -- needs 4 and 5.
Phase 7 ties up the loose ends -- needs 1 and 5.
Phase 9 is the P2 migration -- clearly separable from all of P1.

---

## Test count estimate

| Phase | New/updated tests | Key file |
|-------|------------------|----------|
| 1 | ~7 | `test_frontmatter.py`, `test_pipeline.py` |
| 2 | ~5 | `test_classify.py` (new section or `test_classify_step.py`) |
| 3 | ~10 (6 updated + 4 new) | `test_classify.py` |
| 4 | ~4 | `test_capture.py` (store_nonmd section) |
| 5 | ~14 | `test_classify_step.py` (new file) |
| 6 | ~8 | `test_classify_step.py` (AUTO section) |
| 7 | ~6 | `test_capture.py`, `test_classify_step.py` |
| 8 | ~3 | `test_capture.py` (batch section) |
| 9 | ~7 + full regression | `test_capture.py` (folder section) |
| **Total** | **~64 new/updated** | |

---

## Risk register

| Risk | Impact | Mitigation |
|------|--------|------------|
| Classify prompt quality -- AI may not reliably return the new JSON shape | Blocks Phases 3, 5 | Write the prompt carefully; test with real AI calls in a smoke test before relying on it |
| `_store_nonmd` refactor breaks existing LOCATED binary tests | Blocks Phase 4 | Regression guard: test_store_nonmd_default_params_unchanged runs first |
| `.md` cross-folder move leaves orphan DB row | Data integrity | The `replace_path` atomic DELETE+INSERT prevents this; test explicitly in Phase 6 |
| Phase 9 folder migration breaks 956+ tests | Large blast radius | Run full suite after every change; this phase is clearly separable and can be deferred |
| MetadataResult is frozen -- updating source_path requires `dataclasses.replace` chains | Code complexity | Straightforward with `replace(mr, raw=replace(mr.raw, source_path=new_path))`; test explicitly |
| LiveRegistry not yet wired in watcher | Blocks Phase 5 at runtime | Fallback: `ctx.registry is None` triggers one-shot `build_registry`. Always works for CLI. |
