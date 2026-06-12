# Research: Classify Inline in Single-File Capture (P2-CIC)

_Last updated: 2026-06-08_
_Spec verified: `docs/2_specs/phase2/classify-inline-capture.md` (P2-CIC, both phases)._
_Verified against commits up to: `a28b33c` (P2-CL Phase 2 — classify() pure function)._

---

## Spec Verification Table

15 assumptions verified. 13 validated. 2 resolved (previously invalidated). 0 unverifiable.

| ID | Claim | Verdict | Evidence |
|----|-------|---------|----------|
| A1 | Stage list is `[extract, enrich_urls, summarize, metadata, apply_location_tags, store]` and a new stage can be inserted after `apply_location_tags` before `store` using the same `(value, ctx) -> Result` contract. | **Validated** | `capture.py:975-980` — stage list matches exactly. `run_pipeline` calls each stage with `(current_value, context)`. `apply_location_tags` returns `Result[MetadataResult]`; `store` takes `MetadataResult`. A new classify stage taking `(MetadataResult, ctx) -> Result[MetadataResult]` slots in cleanly. |
| A2 | `classify()` takes `(title, summary, tags, valid_destinations, config)` and returns `Success(ClassifyResult)` with `target_type`/`target_name`/`confidence`/`reasoning`; all failures `recoverable=True` except render. No production caller. | **Validated** | `pipelines/classify.py:31-36` — signature is exactly `(title: str, summary: str, tags: str, valid_destinations: str, config: MainConfig)`. `ClassifyResult` has `target_type`, `target_name`, `confidence`, `reasoning`. Template-render error is `recoverable=False` (line 66); all others are `recoverable=True`. No production caller found in `src/` (grep confirmed). |
| A3 | `_location_context(path, vault_cfg)` returns a `(type, name)` tuple where type is `"project"`, `"domain"`, `"inbox"`, or `None`. | **Validated** | `vault/paths.py:266-306` — returns `("domain", "<D>")`, `("project", "<A>")`, `("inbox", None)`, or `(None, None)`. Matches exactly. |
| A4 | `format_for_prompt(ProjectRegistry(groups=live.get_groups()))` produces the AI destination menu; `LiveRegistry.get_groups()` returns a thread-safe copy. | **Validated (with nuance)** | `vault/registry.py:151-178` — `format_for_prompt(registry: ProjectRegistry) -> str` works as described. `LiveRegistry.get_groups()` (line 268-271) returns `dict(self._registry.groups)` under lock — a shallow dict copy. The `ProjectGroup` objects inside are shared mutable references, but `format_for_prompt` only reads them. Thread-safe for read purposes. Caveat: a concurrent watcher mutation to a `ProjectGroup.projects` list while `format_for_prompt` iterates it could theoretically race, but the watcher always replaces the list (`group.projects = [...]`), it never mutates in-place, so in practice this is safe. |
| A5 | `CONFIG.thresholds.for_pipeline("classify")` falls back to `global` band (auto 0.85 / suggest 0.60) because `pipelines: {}` is empty; `.route(score)` returns AUTO/SUGGEST/CLUELESS. | **Validated** | `config.py:444-455` — `for_pipeline` does `self.pipelines.get(name, self.global_)`. `thresholds.yaml:19` — `pipelines: {}`. So "classify" falls back to global (auto 0.85, suggest 0.60). `ConfidenceBand.route()` (line 409-433) returns `RouteDecision.AUTO`, `.SUGGEST`, or `.CLUELESS`. |
| A6 | The binary LOCATED branch in `_store_nonmd` can be driven by passing an externally-derived destination instead of the path-derived one. | **Resolved** | Originally invalidated: `capture.py:562-563` signature is `_store_nonmd(mr, note_meta, ctx)` with no destination params; lines 577-589 hard-derive `target_type`/`target_name` as local vars. Spec patched: A6 row now explicitly marked INVALIDATED (type-b). Component 5 calls out: (1) add optional `target_type`/`target_name` params to `_store_nonmd`; (2) `.md` AUTO cross-folder move done by classify step itself via `move_note` + `replace_path` before handing to `store`. Both refactors accurately describe what the code needs. |
| A7 | The `.md` rename/move path uses `move_note` + `documents.replace_path` with rollback, no orphan row. | **Validated** | `capture.py:498-541` — the rename path does: `move_note(src, dst, actor="ai")`, then `write_note(dst, ...)`, then `documents.replace_path(old_vault_path, outcome, ...)`. On write failure: rolls back via `move_note(dst, src)`. On DB failure: rolls back via `move_note(dst, src)`. `replace_path` (in `documents.py:225-284`) does `DELETE` + `INSERT OR REPLACE` in one connection context (single transaction), so no orphan row. However, note: the current `_store_md` only renames within the SAME directory (line 496: `_find_rename_dst(src.parent, sanitized_stem)` — always uses `src.parent`). For a cross-folder move (inbox to project), the classify step would need to do its own move-then-replace-path, not reuse `_store_md`'s internal rename. |
| A8 | `move_note(src, dst, actor="ai")` returns `Failure(recoverable=False)` when the note has `updated_by_human=true`. | **Validated** | `vault/writer.py:196-207` — reads note, checks `current.metadata.updated_by_human and actor == "ai"`, returns `Failure(error="note locked by human edit", recoverable=False, ...)`. |
| A9 | `move_guard` registration before a binary move makes the watcher's cross-folder re-home return early; the binary g2 batch-stamp runs AFTER the guard check. | **Validated** | `vault/watcher.py:674-688` — sub-step a: `move_guard.check_and_consume(dst)` is the first check in the cross-folder branch. If it matches, the watcher returns immediately (skip re-home). Sub-step g2 (lines 904-954): batch-stamp runs much later, after location detection, placement, binary move, sibling write, and DB rename. Order confirmed: guard first, batch-stamp last (before audit). |
| A10 | An AUTO move to a project/domain ROOT leaves `batch_id` NULL because `is_batch_subfolder` is False for roots. | **Validated** | `vault/paths.py:316-360` — for a project root like `Projects/Alpha/`: `_location_context` returns `("project", "Alpha")`, then `rel = path.relative_to(projects_path)` = `("Alpha",)`, `len(rel.parts) >= 2` = `1 >= 2` = `False`. So `is_batch_subfolder(project_root, ...)` returns `False`. The batch-stamp pre-step in `capture_file` (line 945) only fires when `is_batch_subfolder(path.parent, ...)` is True. For a file at inbox root, `path.parent` = inbox dir, and `is_batch_subfolder(inbox_dir, ...)` returns `False` because `path != vault_cfg.inbox_path` fails (it IS inbox_path). So inbox-root files also get no batch_id. After an AUTO move to `Projects/Alpha/note.md`, `path.parent` would be `Projects/Alpha/`, which returns False. Batch_id stays NULL. |
| A11 | The idempotency guards run before the pipeline and short-circuit unchanged re-run; the "pending-routing" early-exit is the only special-case for already-parked binaries. | **Validated** | `capture.py:860-872` — pending-routing early-exit checks `marker.exists()` and `status == "pending-routing"`. Lines 877-940: content-hash / source_hash idempotency guards. Both run BEFORE the `run_pipeline` call at line 975. No other early-exit guards exist between the cooldown check (line 849) and the pipeline call. The pending-routing exit is the only special case for parked binaries. |
| A12 | `capture_folder` writes `batches.folder_path` WITHOUT NFC normalization while `capture_file`'s batch-stamp lookup normalizes NFC. | **Validated** | `capture.py:1456,1510,1534,1556` — all four `_insert_batch` calls in `capture_folder` pass `folder_path=str(folder_path.relative_to(vault_cfg.root).as_posix())` with NO `unicodedata.normalize("NFC", ...)`. `capture.py:948-950` — the batch-stamp lookup uses `_ud.normalize("NFC", str(path.parent.relative_to(...).as_posix()))`. Mismatch confirmed. Non-ASCII decomposed folder names will produce different strings on write vs read. |
| A13 | `PipelineContext` is a mutable `@dataclass` that can gain a new optional field without breaking callers. | **Validated** | `core/pipeline.py:47-63` — `@dataclass` (NOT `frozen=True`). Fields: `config`, `correlation_id`, `db_path=None`, `taxonomy=None`, `batch_id=None`. All use keyword defaults. Callers construct with keyword args (`PipelineContext(config=..., correlation_id=..., ...)`). Adding `skip_classify: bool = False` at the end would not break any existing caller. |
| A14 | `NoteMetadata` accepts new typed optional fields and `_KNOWN_KEYS` controls round-trip through `parse`/`dumps`. Additionally, new string fields must be added to `_coerce_bool_to_str` validator. | **Resolved** | Originally partially invalidated: spec understated the `_coerce_bool_to_str` validator requirement at `frontmatter.py:71`. Spec patched: A14 row now explicitly mentions the `_coerce_bool_to_str` validator requirement. Component 2 build steps explicitly say: "Add the four string/float fields to the `_coerce_bool_to_str` validator list as appropriate (the str ones only — `suggested_project`, `suggested_primary_domain`, `classify_reasoning`)." Code confirms validator at line 71: `@field_validator("type", "project", "summary", "source", "source_file", "attachment_path", "status", mode="before")` — the three new str fields need adding here. Spec now accurately describes the requirement. |

---

---

## Open Question Resolutions

### OQ-CIC-A — Project Registry wiring into `capture_file`

**Resolution: Add a `registry` field to `PipelineContext`.**

The watcher already holds a `LiveRegistry` reference (passed via `_VaultEventHandler.__init__` `registry` parameter, line 129 of `watcher.py`). But `capture_file` only receives a `PipelineContext`, which currently has no registry field.

Three options exist:
1. **Add `registry: LiveRegistry | None = None` to `PipelineContext`** — cleanest. The watcher builds the context with the registry. CLI callers build the registry from `build_registry(vault_cfg)` at the entry point. Tests pass `None` or a mock.
2. **Import a module-level `LiveRegistry`** — fragile, breaks test isolation.
3. **Call `build_registry(vault_cfg)` fresh each time in the classify step** — expensive (reads every project's CLAUDE.md on every classify call).

**Recommended: Option 1.** Add `registry: "LiveRegistry | None" = field(default=None)` to `PipelineContext`. The classify step checks `ctx.registry`; if None, it falls back to `build_registry(ctx.config.vault)` (one-shot for CLI). Watcher-driven captures pass the live registry. This aligns with how `batch_id` is already carried on the context.

**Impact:** A13 is already validated — adding an optional field to `PipelineContext` does not break callers. `_build_default_context()` (line 799-818) would need to build a registry from `build_registry(CONFIG.main.vault)` and pass it. Or it can remain `None` and the classify step falls back.

### OQ-CIC-B — Retry-loop numbers (TD-048)

**Resolution: 3 attempts, 1-second base backoff with exponential growth, on exhaustion fall back to CLUELESS-style recorded-candidate.**

This matches TD-048's suggestion and is consistent with the existing `classify()` failure modes (all `recoverable=True` except template-render). The retry loop lives inside the classify step (component 4), not in `classify()` itself. On exhaustion: record the failure as a CLUELESS candidate with `classify_reasoning: "classify failed after 3 attempts: <last error>"`.

### OQ-CIC-C — Status vocabulary

**Resolution: Use `"needs-review"` for both SUGGEST and CLUELESS outcomes.**

The existing `"pending-routing"` status is used only by the CLUELESS binary marker path (`capture.py:750`). That path is being retired by this feature. The new `"needs-review"` status replaces it for all recorded-candidate outcomes. No DB CHECK constraint exists (deferred in Phase Pre-2). The value is future-proof: when the CHECK constraint is added, `"needs-review"` is a clean, self-documenting value. No need for a separate `"needs-review-stuck"` for CLUELESS — the absence of `suggested_project`/`suggested_primary_domain` distinguishes CLUELESS from SUGGEST.

### OQ-CIC-D — Destination tags on AUTO-filed notes (TD-019)

**Resolution: The classify step must stamp `ai_project` and ensure domain tags on `ai_tags` in the `MetadataResult` before handing off to `store`.**

The `store` function (line 446-458) builds `NoteMetadata` from `mr.ai_project` and `mr.ai_tags`. If the classify step sets these correctly before returning the `MetadataResult`, then `store` writes them to disk. After the move, the file would be in `Projects/<project>/` with `project: <project>` and appropriate domain tags — matching what `apply_location_tags` would produce for a file already in that location. No separate "destination tag stamping" step is needed. The classify step IS the stamp.

Note: `apply_location_tags` only runs once (before classify), based on the file's current location. For inbox drops, it leaves `ai_project = None`. The classify step then sets it. On a re-capture of the now-located file, `apply_location_tags` would derive the same values from the new location. Structural consistency is maintained.

### OQ-CIC-E — `_store_nonmd` LOCATED branch external destination override

**Resolution: Refactor `_store_nonmd` to accept optional `target_type`/`target_name` parameters.**

See A6 invalidation detail above. The refactor is mechanical: add two optional parameters, default to `None`, fall through to existing path-derivation when `None`. The LOCATED branch internals are fully reusable. This must be called out as an explicit build step in the plan.

Similarly, `_store_md` needs attention for the `.md` AUTO case: it currently only renames within the same directory (`src.parent`). For cross-folder moves, the classify step must do its own `move_note(src, dst)` + `documents.replace_path(old, outcome)` sequence, reusing the existing chokepoints but not the `_store_md` internal rename path. The spec's component 5 already implies this but should be explicit.

---

## Must-Verify Checklist

| Item | Verdict | Notes |
|------|---------|-------|
| R1: `move_guard` skips pipeline move | Confirmed | `watcher.py:677` — `check_and_consume(dst)` is first in cross-folder branch. Returns early on match. |
| R2: DB-row consistency via existing chokepoints | Confirmed | `move_note` + `replace_path` (atomic DELETE+INSERT in one connection) leaves no orphan row. Binary sibling-first write ensures sibling row exists before binary moves. |
| R3: `batch_id` NULL for root destinations | Confirmed | `is_batch_subfolder` returns False for project/domain roots (depth check: `len(rel.parts) >= 2` fails for roots). Batch-stamp pre-step only fires when True. |
| R4: Idempotency guards + retire "pending-routing" | Confirmed | Content-hash and source_hash guards run before pipeline (lines 877-940). Pending-routing exit (lines 860-872) is the only parked-binary special case. Retiring it is safe — the classify step replaces its function. |
| R6/TD-049: NFC-normalize `folder_path` | Confirmed as a real bug | Four `_insert_batch` call sites in `capture_folder` lack NFC normalization. The `capture_file` read path normalizes. Fix: shared helper or inline `unicodedata.normalize("NFC", ...)` at all write sites. |
| classify() reshape mechanics | Confirmed feasible | `classify()` signature and `ClassifyResult` are the current shapes (P2-CL already implemented). The reshape (subject-shaped input, derive-from-tags output) is a clean change to the existing function. No production callers — only unit tests need updating. |
| routing-derivation helpers in `vault/paths.py` | Confirmed sufficient | `_location_context` provides the gate. `resolve_placement` provides the binary destination path. Project root = `vault_cfg.projects_path / name`. Domain root = `vault_cfg.domain_path / name`. No new helper needed for routing derivation — it is simple path math. |

---

## Additional Findings

### P2-CL is already implemented

STATE.md lists P2-CL as "PENDING implementation," but commits `b2d33fa` (ClassifyResult) and `a28b33c` (classify() function) are already on `main`. The code in `pipelines/classify.py` matches the P2-CL spec. The spec's component 6 (reshape) will modify this already-existing code, not build it from scratch. The planner should note this — the reshape is a modification, not a greenfield build.

### `_store_md` cross-folder move gap

The spec's component 5 says "route through the `_store_md` rename/move chokepoints." But `_store_md` only renames within `src.parent` (line 496: `_find_rename_dst(src.parent, sanitized_stem)`). For the `.md` AUTO case, the classify step needs to perform a cross-folder move that `_store_md` does not currently support. The classify step should either:
- (a) Do the move itself before calling store (set the MetadataResult's source path to the new location), or
- (b) Add an optional `destination_dir: Path | None` parameter to `_store_md`, analogous to the `_store_nonmd` refactor.

Option (a) is simpler: the classify step does `move_note(src, dst, actor="ai")`, updates `mr.raw.source_path` to `dst` (since MetadataResult is frozen, it needs `dataclasses.replace`), then lets `store` handle the in-place write + upsert. The `documents.replace_path` call in `_store_md` only fires on rename — for an in-place write, `documents.upsert` is used. So after the move, `store` sees the file at its new location and does an in-place write + upsert. This works.

### `capture_folder` CLUELESS loop calls `capture_file` without SUPPRESS

The spec's component 3 says the CLUELESS loop in `capture_folder` must set `skip_classify` on the per-file context. Looking at `capture.py:1559-1563`:

```python
for f in files:
    match await capture_file(f, ctx_with_batch):
```

This calls `capture_file` with `ctx_with_batch` which does NOT have `skip_classify=True`. Once the classify step exists, these files (already in inbox under CLUELESS folder) would each trigger an individual classify call — which is the correct SUPPRESS scenario the spec identifies. Confirming: component 3 (SUPPRESS signal) is a required build step, and the CLUELESS loop is one of the places that must set it.

### `_capture_folder_files` also calls `capture_file` without SUPPRESS

`capture.py:1323-1335` — the ROUTING case (project/domain drop) also calls `capture_file` without SUPPRESS. These files ARE located (they are in a project/domain folder after the folder move), so the classify step would see them as LOCATED and skip. But the inbox CLUELESS case (lines 1559-1563) is different — those files are in inbox with no location context, so the classify step would try to classify them individually. The SUPPRESS signal is needed here.

---

## Summary

**Counts:** 13 validated, 2 resolved (A6, A14 — previously invalidated), 0 unverifiable.

**All assumptions now validated or resolved.** No remaining invalidations. No Q4 diagram needed.

**Previously invalidated, now resolved:**
- A6: Spec patched — Component 5 now explicitly calls out the `_store_nonmd` refactor (add optional `target_type`/`target_name` params) and the `.md` cross-folder move strategy (classify step does its own `move_note` before handing to `store`). Both accurately describe the code.
- A14: Spec patched — A14 row and Component 2 build steps now explicitly require adding new string fields to `_coerce_bool_to_str` validator.

**Open questions resolved:**
- OQ-CIC-A: Add `registry: LiveRegistry | None` to `PipelineContext`.
- OQ-CIC-B: 3 attempts, exponential backoff, fall back to CLUELESS on exhaustion.
- OQ-CIC-C: `"needs-review"` for both SUGGEST and CLUELESS.
- OQ-CIC-D: Classify step stamps `ai_project`/`ai_tags` on MetadataResult; `store` writes them.
- OQ-CIC-E: Refactor `_store_nonmd` to accept optional destination parameters.

Ready for: /plan classify-inline-capture

---

## Re-check (cycle 1) — 2026-06-08

### Re-check: all assumptions resolved

The spec was patched to address two invalidated assumptions (A6, A14) and fold in five open-question resolutions (OQ-CIC-A through OQ-CIC-E). This re-check verified the patches against live code and scanned all 15 assumptions for regressions.

| ID | Was | Now | Evidence |
|----|-----|-----|----------|
| A6 | Invalidated — `_store_nonmd` hard-derives destination from path; no external param | Resolved — spec now explicitly marks A6 as INVALIDATED (type-b); Component 5 calls out both refactors: optional params on `_store_nonmd`, and `.md` cross-folder move via classify step's own `move_note` + `replace_path` | `capture.py:562-563` (signature), `capture.py:577-589` (local vars), `capture.py:495-496` (`_store_md` uses `src.parent` only) — all match spec's corrected description |
| A14 | Invalidated (partially) — spec understated `_coerce_bool_to_str` validator requirement | Resolved — A14 row now mentions validator; Component 2 build steps explicitly list the three string fields to add | `frontmatter.py:71` — validator decorator covers existing string fields; spec correctly identifies the three new ones (`suggested_project`, `suggested_primary_domain`, `classify_reasoning`) that must be added |

**OQ-CIC-A resolution verified:** `PipelineContext` (`pipeline.py:47-62`) is a mutable `@dataclass` with keyword-default fields. Adding `registry: "LiveRegistry | None" = field(default=None)` is mechanically feasible. `_VaultEventHandler.__init__` (`watcher.py:129`) already holds `registry: "LiveRegistry | None"` — passing it through to `PipelineContext` is straightforward wiring.

**Regression check:** All 13 previously-validated assumptions (A1-A5, A7-A13, A15) re-scanned against the patched spec. No new contradictions introduced. Key confirmations:
- A1: Stage list at `capture.py:977` unchanged.
- A7: `_store_md` cross-folder gap is now acknowledged in Component 5 (spec does not claim `_store_md` handles cross-folder moves).
- A13: Adding `registry` field is consistent with the validated claim that `PipelineContext` accepts new optional fields.
- A15: `MetadataResult` is `frozen=True` (`capture.py:68`); spec correctly says to use `dataclasses.replace` for the `.md` AUTO path.

**Counts:** 0 still-invalidated, 2 resolved, 0 new invalidations, 0 regressions.

All assumptions validated. Ready for /plan classify-inline-capture.
