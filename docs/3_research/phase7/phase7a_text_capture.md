# Research: Phase 7A — Text Capture (cloud capture refactor, text path only)
_Last updated: 2026-06-14_

## Overview

Plain English: Phase 7A rewrites the cloud's "capture" so that when the laptop daemon
uploads a piece of extracted text, the cloud does exactly three safe things — **save the
raw text immediately, ask the AI for a structured summary, and record it in the central
database** — and never writes back into the user's folders. This research opened every
file the spec names and checked, claim by claim, whether the code actually behaves the way
the spec assumes.

**What it found:** 9 of 10 spec assumptions hold exactly as written. The one that does
not (A9) was *already flagged by the spec itself as likely-false* — and the code confirms
the spec's own corrected reading, not the original design's. This is a **scope correction,
not a redesign**: it narrows what Phase 7A is allowed to delete, nothing more. One extra
wrinkle surfaced outside the assumptions table: a constraint in the spec cites a test
(`test_pipeline_has_no_heavy_imports`) that does **not** actually guard the new capture
file. That is a minor documentation/enforcement gap, not a blocker.

**Bottom line for planning:** No invalidated assumption forces a design change. Phase 7A
can proceed. The one hard rule the plan must carry forward: **do NOT delete
`replace_path` or the `WriteOutcome` type** — they have a live, non-capture consumer
(`kms_move`). Retire only `documents.upsert(WriteOutcome)`, and keep `_derive_title`
because `replace_path` still uses it.

---

## Key Components

Plain English: these are the existing pieces Phase 7A reuses or retires. Each is a real
file/function confirmed on disk.

- **Upload front door** — `mcp_server/api.py::upload_handler` (api.py:68). Today it
  validates the upload (vault_path, extracted_text, content_hash all required → HTTP 400
  otherwise) and calls **only** `upsert_from_upload` (api.py:124). No summarize, no audit,
  no index happen today. 7A re-points it at the new pipeline entry.
- **Raw-store routine** — `storage/documents.py::upsert_from_upload` (documents.py:156).
  Three-way decision keyed on `vault_path` + `content_hash`: new path → INSERT
  (documents.py:194); same hash → **skip, return existing id** (documents.py:218); changed
  hash → UPDATE (documents.py:222). Stores `full_body`, `original_filename`,
  `file_size_bytes`, `title`, `content_hash`. Title defaults to `Path(vault_path).stem`
  (documents.py:196).
- **Lookup by path** — `storage/documents.py::get_by_path` (documents.py:253). Returns
  `Success(DocumentRow)` if found, `Success(None)` if not. Read-only connection. This is
  the front-loaded dedup peek.
- **Document row shape** — `storage/documents.py::DocumentRow` (documents.py:27). Carries
  `full_body`, `summary`, `title`, `content_hash`, `project`, `status` etc. `content_hash`
  is readable for the pre-AI peek.
- **AI provider factory** — `llm/provider.py::get_provider` (provider.py:54). `"capture"`
  is a valid `Task` literal (config.py:44) mapping to provider "claude" / model
  claude-haiku-4-5 by default.
- **AI text call** — `llm/provider.py::LLMProvider.complete(system, user)` (provider.py:40).
  **`async`**, returns `Result[LLMResponse]`; the text is on `LLMResponse.content`
  (provider.py:25-30). The summarizer stage must be async and read `.content`.
- **Prompt loader** — `llm/prompt_loader.py` (`PROMPTS`, prompt_loader.py:54). Loads every
  `prompts/*.yaml` at import; each prompt has `system`/`user`/`variables` and a
  `render(**vars)` returning `(system, user)`. No `capture`/`summary` prompt exists yet —
  Component 1 genuinely needs a new file.
- **Existing summarize prompt** — `prompts/summarize.yaml`. Plain-text 2–4 sentences,
  **explicitly forbids Markdown** ("no headers, no bullet points"). Confirms the spec:
  this is the opposite shape; a NEW prompt is required.
- **Keyword indexer** — `retrieval/keyword.py::index_keywords(vault_path, title, summary,
  body, db_path)` (keyword.py:10). Keyed on `vault_path`, plain fields, no `WriteOutcome`.
- **Meaning indexer** — `retrieval/embeddings.py::index_embedding(vault_path, title,
  note_type, tags, summary, db_path)` (embeddings.py:42). Keyed on `vault_path`; folds
  `summary` into the encoded text via `_build_context_text` and **omits the summary when
  it is None/empty** (embeddings.py:37-39) — so on AI-failure (NULL summary) the embedding
  is weak/deferred, exactly as the spec says.
- **Audit writer** — `core/audit.py::write(decision, *, pipeline, stage, outcome, db_path)`
  (audit.py:11). `outcome` is a free string. Underneath, `audit_log.append` returns
  `Failure("missing correlation_id")` if no correlation id is set (audit_log.py:28-34).
- **AI decision envelope** — `core/confidence.py::AIDecision` (confidence.py:36).
  `action` and `reasoning` must be non-empty (validator confidence.py:41-47); `confidence`
  must be 0.0–1.0; `source_ids` defaults to empty list — empty is allowed.
- **Knowledge-facts queries** — `storage/knowledge_entries.py::query_by_entity`
  (line 139) / `get_confident_and_pending` (line 171). Both return `Success([...])` from
  `fetchall()` — empty table → `Success([])`, never `Failure`.
- **Graceful-empty pattern** — `mcp_server/context.py` (graceful-degrade returns empty,
  e.g. context.py:594). Reference for the empty-knowledge fallback.
- **DB connection** — `storage/db.py::get_connection` (used everywhere above;
  `attach_summary` reuses it).

**Retire / keep boundary (the heart of A9):**
- `documents.upsert(WriteOutcome)` (documents.py:100) — live src callers: ONLY
  `pipelines/capture.py:1062, 1268, 1416`. **Genuinely orphaned → retire OK.**
- `documents.replace_path(WriteOutcome)` (documents.py:339) — live src callers:
  `pipelines/capture.py:515, 986` AND **`mcp_server/_move.py:100`** (backing `kms_move`,
  registered in `mcp_server/tools.py:14, 72, 103`). **Live non-capture consumer → MUST KEEP.**
- `WriteOutcome` type (`vault/writer.py:39`) — imported by `documents.py:24`,
  `capture.py:47`, `_move.py` (via the call). **MUST KEEP the import in documents.py**
  (replace_path's signature uses it).
- `_derive_title(outcome)` (documents.py:80) — used by BOTH `upsert` AND `replace_path`.
  Since `replace_path` stays, **`_derive_title` MUST stay.**

---

## How It Works (today's vs Phase 7A's flow)

Plain English: today an upload only gets *saved* — nothing summarizes or indexes it.
Phase 7A adds the summarize → attach → index → audit beats around the existing save, with
the duplicate check moved to run *before* the AI.

**Today (verified):**
1. `upload_handler` validates the body and calls `upsert_from_upload` (api.py:124).
2. `upsert_from_upload` INSERTs / skips-identical / UPDATEs by `vault_path`+`content_hash`
   (documents.py:186-244) and returns the row id.
3. That is the entire request. No AI, no audit, no search index.

**Phase 7A's intended flow (the spec, verified feasible against code):**
1. **Front-loaded dedup** — peek `get_by_path(vault_path)`; if a row exists with the same
   `content_hash`, return success **without** calling the AI. (Feasible: `get_by_path`
   exposes `content_hash` on the returned `DocumentRow`.)
2. **Store raw first** — `upsert_from_upload(..., title=stem)` saves `full_body`; `summary`
   stays NULL, `project` stays NULL.
3. **Summarize** — async `get_provider("capture", CONFIG.main).complete(system, user)`
   with a NEW Markdown-structured prompt; optional empty-knowledge consult degrades to "no
   context."
4. **On AI success** — `attach_summary(vault_path, summary, title)` (new UPDATE-only
   routine); best-effort `index_keywords` + `index_embedding` keyed on `vault_path`;
   `CAPTURED` audit entry; classify-trigger log line; return success.
5. **On AI failure** — leave summary NULL, write a failure audit entry, **return success**;
   skip meaning-index (no summary).

**Note for the plan (not a spec error, but easy to miss):** `audit.write` fails if no
`correlation_id` is in contextvars (audit_log.py:28). The new pipeline entry must call
`new_correlation_id()` (per CLAUDE.md runtime patterns) before any audit write, or both
the `CAPTURED` and failure entries silently `Failure`.

---

## Spec Verification

Plain English: every assumption the spec listed, checked against the real code. One row is
invalidated (A9) — and it is the row the spec already predicted would be invalidated.

| ID | Spec Claim | Verdict | Evidence |
|----|-----------|---------|----------|
| A1 | `upsert_from_upload` does the 3-way fingerprint dance (insert / skip-identical / update) keyed on vault_path + content_hash, needs no change for raw-store | ✅ Validated | `documents.py:186-244` — SELECT by vault_path, `existing_hash == content_hash` skip at :218, INSERT at :194, UPDATE at :222 |
| A2 | The skip-identical branch only runs *after* the function is called; a separate `get_by_path` pre-check is needed to short-circuit before the AI | ✅ Validated | `upsert_from_upload` has no knowledge of the AI; skip is internal (documents.py:218). `get_by_path` (documents.py:253) returns the row+`content_hash` for a clean pre-peek |
| A3 | `summary` is a plain TEXT column read into the keyword index and folded into the embedding; a Markdown block flows through unchanged | ✅ Validated | schema.sql:5 `summary TEXT` (nullable); `index_keywords` writes summary verbatim (keyword.py:29); `_build_context_text` folds summary (embeddings.py:37-39) |
| A4 | `upsert_from_upload` takes an optional `title` and falls back to the filename stem | ✅ Validated | `title: str \| None = None` (documents.py:163); `resolved_title = title or Path(vault_path).stem` (documents.py:196) |
| A5 | `index_keywords(...)` and `index_embedding(...)` can be called keyed on vault_path (not a WriteOutcome) after the attach | ✅ Validated | Both take `vault_path` + plain fields, no WriteOutcome (keyword.py:10, embeddings.py:42) |
| A6 | `get_provider("capture", CONFIG.main)` resolves; `"capture"` is a valid Task | ✅ Validated | `Task` literal includes `"capture"` (config.py:44); `get_provider` returns a provider (provider.py:54); default model claude-haiku-4-5 (config.py:202) |
| A7 | `core.audit.write` accepts an arbitrary `outcome` string and an empty/short `source_ids` | ✅ Validated | `outcome: str` free param (audit.py:25); `source_ids` defaults to `[]`, only `action`+`reasoning` must be non-empty (confidence.py:39-47). **Caveat:** needs correlation_id in contextvars (audit_log.py:28) |
| A8 | `query_by_entity` / `get_confident_and_pending` return empty list (not error) on empty table | ✅ Validated | Both `return Success([_row_to_entry(r) for r in rows])` from fetchall (knowledge_entries.py:150, 193); Failure only on sqlite3.Error |
| A9 | `documents.upsert(WriteOutcome)` AND `replace_path(WriteOutcome)` have ONLY old capture as a live caller, so both retire cleanly | ❌ Invalidated (spec self-predicted) | `upsert` callers: capture.py only (1062/1268/1416) → retire OK. `replace_path` callers: capture.py (515/986) **+ `mcp_server/_move.py:100`** (live `kms_move`, registered tools.py:14/72/103) → MUST KEEP. `WriteOutcome` import + `_derive_title` also must stay |
| A10 | The dev `kms capture <file>` calls `capture_file`/`scan_capture`, both retiring; re-pointing is mechanical | ✅ Validated | `kms capture` imports `capture_file, scan_capture` (main.py:65) and calls them (:68, :83); `kms watch` also uses them (:341, :360, :388). 13 test files reference the retiring functions — full blast radius below |

---

## Edge Cases & Silent Failure Modes

Plain English: things that can go wrong quietly if the plan isn't careful.

- **Missing correlation_id silently drops the audit row.** `audit_log.append` returns
  `Failure("missing correlation_id")` (audit_log.py:28-34) if contextvars has none. Since
  the audit log is non-negotiable (C-13) and the failure path *depends* on the audit entry
  being written, the new entry function MUST set `new_correlation_id()` first. The spec's
  flow does not call this out.
- **AI-failure embedding gap is real and intended.** `_build_context_text` drops the
  summary suffix when summary is None (embeddings.py:37-39). On AI failure the embedding
  would encode only `title/type/tags` — weak. The spec correctly defers the meaning-index
  to retry; the plan should NOT call `index_embedding` on the failure path.
- **`title` is NOT NULL in schema** (schema.sql:4). `attach_summary` must always pass a
  real title (the AI's descriptive title); never pass None. `summary` is nullable so it
  may stay NULL.
- **`index_keywords` types `summary` as `str`, not `str | None`** (keyword.py:14). On the
  success path summary is present so this is fine, but the plan should pass `summary or ""`
  defensively if it ever indexes a row with NULL summary.
- **Re-upload of changed content still UPDATEs full_body** (documents.py:222) — the
  store-raw beat is safe on re-capture; the dedup peek only short-circuits on *identical*
  hash, matching P7-CAP-01.

---

## Dependencies & Coupling

Plain English: what the new capture leans on, and what leans on the parts it wants to delete.

- **New capture entry → depends on:** `upsert_from_upload`, `get_by_path` (documents.py),
  the new `attach_summary` (documents.py), `get_provider` + `complete` (llm/provider.py),
  `PROMPTS` (prompt_loader.py), `index_keywords`/`index_embedding` (retrieval/),
  `audit.write` + `AIDecision` (core/), `knowledge_entries` helpers, `get_connection`
  (storage/db.py).
- **`replace_path` + `WriteOutcome` are coupled to `kms_move`**, NOT just capture.
  `mcp_server/_move.py` is the backing logic for the Phase 4 `kms_move` MCP tool
  (registered in `mcp_server/tools.py`). This is the single fact that narrows Phase 7A's
  retirement list.
- **`upsert(WriteOutcome)` is coupled only to old capture** — safe to retire.
- **Test blast radius (A10):** these test files reference the retiring capture functions
  and will break / need rewriting when old capture is removed:
  `tests/test_pipelines/test_capture.py`, `test_capture_phase3.py`, `test_capture_phase9.py`,
  `test_capture_phase10.py`, `test_capture_phase12.py`, `test_capture_folder.py`,
  `test_capture_rename.py`, `test_capture_search.py`, `test_capture_integration.py`,
  `test_reconcile.py`, `tests/test_pipelines/conftest.py`, `tests/test_vault/test_watcher.py`,
  `tests/test_vault/test_paths.py`. Plus the two CLI commands `kms capture` and `kms watch`
  (`cli/main.py:65, 83, 341, 360, 388`). `tests/test_storage/test_documents.py` and
  `test_documents_search.py` exercise `upsert(WriteOutcome)`/`replace_path` directly — the
  `replace_path` tests stay (function stays); the `upsert(WriteOutcome)` tests retire with
  the function.

---

## Extension Points

Plain English: where Phase 7A adds cleanly, and where it must not over-reach.

- **New prompt** — drop a new `prompts/*.yaml`; auto-loaded by `PROMPTS` at import. No code
  change to the loader. Clean extension point.
- **New AI task** — `"capture"` already exists; nothing to add.
- **`attach_summary`** — a new sibling of `upsert_from_upload` in `documents.py`, using the
  same `get_connection` pattern (C-04). UPDATE-only on `summary`, `title`, `updated_at`
  keyed by `vault_path`; preserves `full_body`/`content_hash`/`original_filename`/
  `file_size_bytes`. Feasible and isolated.
- **Hard boundary (ADR-0012 retirement rule):** Phase 7A may delete capture's own
  dependencies but must NOT delete shared modules. `replace_path`, `WriteOutcome`,
  `_derive_title`, and `vault/writer.py` all stay because `kms_move` / Phase 6 still need
  them.

---

## Open Questions

Plain English: things code alone can't settle — carry into planning, none blocking.

- **OQ-7A-1 (resolved by this research):** Can `replace_path`/`WriteOutcome` retire? **No.**
  `mcp_server/_move.py:100` is a live caller. Retire only `upsert(WriteOutcome)`. This is
  now a settled fact, not an open question — moved to Invalidated Assumptions as the
  corrected scope.
- **OQ-7A / OQ-7B / OQ-7C (from design, unchanged):** summary storage shape, AI-failure
  embedding timing, and audit `source_ids` identifier. All confirmed non-blocking by code:
  `summary` is plain TEXT (re-parse is possible later), embedding genuinely degrades on
  NULL summary (defer to retry is correct), and `source_ids` accepts vault_path strings
  (empty allowed). No code obstacle to any recommendation.
- **Correlation-id setup** (new, minor): the spec's flow omits `new_correlation_id()` at
  the entry. Code shows audit writes fail without it. Plan should add it explicitly. Not a
  design decision — a known runtime requirement.

---

## Technical Debt Spotted

- **Spec constraint misattribution (minor, worth fixing before plan).** The Constraints
  section says: *"The new capture must not import `vault.` types at module scope — source:
  `tests/test_core/test_pipeline_phase1.py::test_pipeline_has_no_heavy_imports`."* That
  test greps **`src/core/pipeline.py`** only (test_pipeline_phase1.py:29) — it does NOT
  scan `pipelines/capture.py`, and no other test enforces an import boundary on the new
  capture file. So the cited enforcement does not exist for this module. The *intent*
  (keep `vault.` out of the new capture's module scope, use `Any`/DTOs) is still sound
  guidance, but the plan should not assume a test will catch a violation. If the team wants
  it enforced, a new source-grep test on `pipelines/capture.py` would be needed (out of
  7A's stated scope).
- **`index_keywords` summary param typed `str` not `str | None`** (keyword.py:14) — works
  on the success path; defensive `summary or ""` recommended if ever indexing NULL-summary
  rows.

---

## Invalidated Assumptions

Only A9 is invalidated, and it is a **mechanical / scope correction** — it narrows what
Phase 7A may delete. It does NOT change the design's Q1 flow, the data-safety contract, or
any interface the spec defines. **No Q4 conflict diagram is needed** (per the research
skill: text suffices for mechanical/scope corrections; the design's two-beat store-then-
summarize flow stands unchanged).

### A9 — `replace_path` / `WriteOutcome` retirement scope

**Spec claimed (quoting the design it carried):** *"`documents.upsert(WriteOutcome)` and
`documents.replace_path(WriteOutcome)` have only old capture as a live caller, so both
retire cleanly."* (The spec itself flagged this as "LIKELY FALSE" and logged OQ-7A-1.)

**Code shows:**
- `documents.upsert(WriteOutcome)` (documents.py:100): live src callers are ONLY
  `pipelines/capture.py:1062, 1268, 1416`. → genuinely orphaned, **retire OK**.
- `documents.replace_path(WriteOutcome)` (documents.py:339): live src callers are
  `pipelines/capture.py:515, 986` **AND `mcp_server/_move.py:100`** — the backing logic for
  the Phase 4 `kms_move` MCP tool, which is registered live in `mcp_server/tools.py:14, 72,
  103`. → **MUST KEEP.**
- `WriteOutcome` (vault/writer.py:39) is imported by `documents.py:24` (used by
  `replace_path`'s signature) and by `_move.py`. → the import in `documents.py` **MUST
  STAY**.
- `_derive_title(outcome)` (documents.py:80) is called by BOTH `upsert` and `replace_path`.
  Since `replace_path` stays, `_derive_title` **MUST STAY**.

**Why this matters:** Deleting `replace_path` or the `WriteOutcome` import on the design's
original word would break `kms_move` at runtime (the MCP move tool would fail to import /
call). The blast would be silent until someone exercises `kms_move`.

**Corrected scope for Phase 7A (Component 6 retirement list):**
- ✅ Retire: `documents.upsert(WriteOutcome)` and its exclusive use of `_derive_title`
  *only if* `replace_path` were also gone — but it is not, so:
- ✅ Retire: `documents.upsert(WriteOutcome)` (the function) — its three callers all live
  in old capture and are being removed.
- ❌ KEEP: `documents.replace_path(WriteOutcome)` — live consumer `kms_move`.
- ❌ KEEP: the `WriteOutcome` import in `documents.py` — used by the retained `replace_path`.
- ❌ KEEP: `_derive_title` — used by the retained `replace_path`.
- ❌ KEEP (already in spec): `vault/writer.py`, the watcher, the indexer (Phase 6 / shared).

**Suggested resolution direction (for the spec author, not a decision):** Update
Component 6 / OQ-7A-1 to state plainly: retire `documents.upsert(WriteOutcome)` only;
explicitly KEEP `replace_path`, `WriteOutcome`, and `_derive_title` until `kms_move` is
refactored off `replace_path` in a later, separately-scoped change. This matches the
spec's own recommended Option (a).
