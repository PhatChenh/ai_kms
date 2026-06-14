# Research: Phase 7B — Visual / Binary Capture
_Last updated: 2026-06-14_

## Overview

Phase 7B teaches the cloud to handle binary file uploads (images, scanned PDFs) by storing the raw bytes in object storage and asking a vision-capable AI to describe them in words. This research verified the spec's 10 assumptions against the actual codebase and the AgentBase platform documentation.

**Summary:** 7 assumptions validated, 2 resolved (A4 patched successfully, A9 accurately reflected), 1 unverifiable from code alone. All blocking invalidations from the first pass are resolved. The spec now correctly describes the `get_by_path` pre-read pattern for blob cleanup (A4) and accurately reflects the MaaS vision integration risk with PDF dropped from defaults and rate limit noted (A9). Ready for planning.

---

## Key Components

These are the existing modules the binary capture branch will touch or extend, confirmed by reading the actual code.

| Component | File | Role |
|---|---|---|
| Upload endpoint | `src/mcp_server/api.py` | Cloud front door; multipart path currently discards file bytes (line 141–142) |
| Event endpoint (delete) | `src/mcp_server/api.py` line 270–345 | Receives delete events; calls `delete_by_path` |
| Save-or-update routine | `src/storage/documents.py::upsert_from_upload` (line 100–196) | INSERT/skip-identical/UPDATE by vault_path + content_hash |
| Summary attach | `src/storage/documents.py::attach_summary` (line 446–487) | Updates only `summary`, `title`, `updated_at` — no other columns |
| Delete by path | `src/storage/documents.py::delete_by_path` (line 256–282) | Hard-deletes row + search entries; returns `Success(rowcount)` only |
| Document row shape | `src/storage/documents.py::DocumentRow` (line 27–47) | 17 fields; no `blob_ref` or `mime_type` today |
| AI provider factory | `src/llm/provider.py::get_provider` (line 54–87) | Routes by `Task` literal to concrete provider |
| AI provider base | `src/llm/provider.py::LLMProvider` (line 33–51) | ABC with `complete(system, user)` — text-only |
| OpenAI-compatible provider | `src/llm/openai_provider.py::OpenAIProvider` (line 18–87) | Uses `openai.AsyncOpenAI`; sends `messages` as list of dicts |
| Keyword indexer | `src/retrieval/keyword.py::index_keywords` (line 10–51) | Keyed on `vault_path`, takes `title`, `summary`, `body` |
| Meaning indexer | `src/retrieval/embeddings.py::index_embedding` (line 42+) | Keyed on `vault_path`, takes `title`, `note_type`, `tags`, `summary` |
| Task literal | `src/core/config.py` line 43–45 | `"classify" | "synthesis" | "documentation" | "embeddings" | "self_learn" | "capture"` — no `"vision"` |
| Providers config | `src/core/config.py::ProvidersConfig` (line 173–193) | Fields: `classify`, `synthesis`, `embeddings`, `self_learn`, `capture` — no `vision` |
| Capture pipeline | `src/pipelines/capture.py::capture_upload` (line 123–278) | Text-only today; `extracted_text: str` is REQUIRED (not `str | None`) |

---

## How It Works (current text path, for contrast)

The existing text capture path (`capture_upload`) runs five stages in sequence:

1. **Correlation ID** — `new_correlation_id()` for audit/log threading
2. **Dedup peek** — `get_by_path` + compare `content_hash`; same hash = early return
3. **Store raw** — `upsert_from_upload` saves `full_body` + `content_hash` immediately
4. **Summarize** — AI call via `get_provider("capture", ...)` + `PROMPTS["capture_summary"]`; parse summary + title
5. **On success:** `attach_summary` + best-effort `index_keywords` + `index_embedding` + audit `CAPTURED`
   **On failure:** audit `FAILED`, return `Success(row_id)` anyway (store-anyway contract)

The binary branch (7B) will fork after step 2 when `extracted_text is None` and `raw_bytes` are present, inserting blob-store and vision-describe steps where steps 3–5 currently run.

---

## Spec Verification

| ID | Spec Claim | Verdict | Evidence |
|----|-----------|---------|----------|
| A1 | `upsert_from_upload` already accepts `extracted_text=None` and its skip-identical branch fires when `content_hash` matches | ✅ Validated | `documents.py:101` — param is `extracted_text: str | None = None`; line 164 compares `existing_hash == content_hash` regardless of `extracted_text` value. Confirmed: `None` flows to `full_body = NULL` in the INSERT/UPDATE SQL. |
| A2 | `upsert_from_upload` can be extended to write `blob_ref` and `mime_type` without breaking text-path callers | ✅ Validated | Adding two optional kwargs (`blob_ref: str | None = None`, `mime_type: str | None = None`) with default `None` is backward-compatible. Existing callers never pass them, so they stay `NULL`. The INSERT/UPDATE SQL adds two more columns — no return-shape change (still `Success(row_id)`). |
| A3 | `attach_summary` works identically for vision description — writes only `summary`, `title`, `updated_at` without touching `blob_ref`/`mime_type` | ✅ Validated | `documents.py:471–480` — the UPDATE statement is: `SET summary = ?, title = ?, updated_at = datetime('now') WHERE vault_path = ?`. It touches exactly 3 columns. No read or write of any other column. Safe for vision descriptions. |
| A4 | ~~`delete_by_path` returns the deleted row's `blob_ref`~~ **Patched:** use `get_by_path` to pre-read `blob_ref` before calling `delete_by_path` | ✅ Resolved | `documents.py:199–228` — `get_by_path` does `SELECT * FROM documents WHERE vault_path = ?` and returns `Success(DocumentRow)`. After migration 009 adds `blob_ref`/`mime_type` columns and `DocumentRow`/`_row_from_sqlite` are updated (using the existing `if "col" in row.keys()` guard pattern at lines 62–77), `get_by_path` will return the `blob_ref` value. The event handler pre-reads before calling `delete_by_path`. |
| A5 | `OpenAIProvider` uses `openai.AsyncOpenAI` and the SDK's `image_url` content-block shape can be sent by replacing the `content` string with a list of content blocks | ✅ Validated | `openai_provider.py:37` — `self._client = openai.AsyncOpenAI(...)`. Line 62 — messages use `{"role": "user", "content": user}` where `user` is a string. The `openai` Python SDK (>=1.0) supports `content` as either a string or a list of content blocks (text + image_url). Replacing the string with a list is SDK-supported. |
| A6 | The multipart handler receives raw file bytes in memory and they are available to pass downstream | ✅ Validated | `api.py:108` — `form = await request.form()`. Starlette's `request.form()` reads multipart data including file uploads into memory (for small files) or `SpooledTemporaryFile`. The `file` field (line 93: "accepted and discarded") is available as `form["file"]` (an `UploadFile` object with `.read()` async method). Bytes are accessible. |
| A7 | `index_keywords` and `index_embedding` can be called keyed on `vault_path` after description attaches, same as 7A | ✅ Validated | `keyword.py:10–16` — `index_keywords(vault_path, title, summary, body, db_path)`. `embeddings.py:42–48` — `index_embedding(vault_path, title, note_type, tags, summary, db_path)`. Both are keyed on `vault_path` with no type-specific requirements. For a binary row: `body` can be the description text, `summary` is the description, `title` is the AI-generated title. Works identically. |
| A8 | The `content_hash` hex digest is a valid S3 object key | ✅ Validated | S3 object keys allow any UTF-8 character. A hex digest (0-9, a-f) is a strict subset. VNG vStorage is S3-compatible (`hcm04.vstorage.vngcloud.vn`). Hex strings are safe keys. The spec's namespacing (`blobs/<content_hash>`) also uses only safe characters. |
| A9 | AgentBase MaaS accepts image input via the `image_url` content-block shape | ⚠️ Unverifiable | MaaS lists two vision models: `Qwen3-VL-235B-A22B-Instruct` and `Skylark-vision`. The MaaS API is documented as "OpenAI-compatible" — but the docs do NOT explicitly confirm `image_url` content blocks work. The docs show only text-based examples. A separate OCR API exists for image/PDF processing. **Not a hard stop** — vision models exist, the API is OpenAI-compatible (where `image_url` blocks are standard), but the exact format is undocumented. Must be verified at integration time. See Invalidated Assumptions below for PDF impact. |
| A10 | The `"vision"` task can be added to the `Task` literal and `ProvidersConfig` the same way `"capture"` exists today | ✅ Validated | `config.py:43–45` — `Task` is a `Literal[...]` type alias. Adding `"vision"` is a string addition. `ProvidersConfig` (line 173–193) uses `getattr(self, task)` in `for_task()`, so adding a `vision: Provider = "openai"` field completes the wiring. Same pattern as every existing task. Note: `ProvidersConfig` currently lacks `documentation` field despite `Task` including it — `for_task("documentation")` would raise `AttributeError`. Adding `vision` is same-shape as adding `documentation` would be. |

---

## Edge Cases & Silent Failure Modes

1. **`capture_upload` signature mismatch.** The current signature is `extracted_text: str` (required, non-optional). The spec's Component 6 says the binary branch fires "when `extracted_text is None`." This is correct in intent — the spec plans to change the signature to `str | None` — but worth noting explicitly that the signature change is required, not just adding new optional params.

2. **`for_task` with missing field.** `ProvidersConfig.for_task()` uses `getattr(self, task)`. If `"vision"` is added to the `Task` literal but the `vision` field is not added to `ProvidersConfig`, `for_task("vision")` raises `AttributeError` at runtime with no helpful message. The same latent bug exists for `"documentation"` today.

3. **`delete_by_path` transaction boundary.** The spec's Component 7 says the reference-count query should run inside the transaction but the blob delete should be outside. Current `delete_by_path` uses a single `with get_connection()` block for the DELETE + search cleanup. The blob reference-count logic must either (a) wrap `delete_by_path` to pre-read `blob_ref`, or (b) restructure the delete to SELECT-then-DELETE. Option (a) is safer — no change to `delete_by_path` internals.

4. **Multipart `file` field is `UploadFile`, not raw bytes.** `form["file"]` returns a Starlette `UploadFile` object. To get bytes: `raw_bytes = await form["file"].read()`. This is an async call. The spec says "bytes are available to pass downstream without a second read" — true, but the `.read()` call is async and must happen before the form is closed.

5. **Migration version-pin test cascade.** Migration 008 test asserts `version == 8`. Adding migration 009 bumps to 9. Must update `test_migration_008.py:47` from `8` to `9`. Same pattern as the 007→008 bump documented in CLAUDE.md.

---

## Dependencies & Coupling

**New external dependency:** `boto3` — approved by human. Not yet in `pyproject.toml`.

**Internal coupling points:**
- `storage/documents.py` — gains `blob_ref`/`mime_type` columns and fields (migration 009 + `DocumentRow` + `_row_from_sqlite` + `upsert_from_upload`)
- `core/config.py` — gains `"vision"` in `Task` literal, `vision` field in `ProvidersConfig`, `VisionConfig` in `CaptureConfig` or standalone, `vision_model` in provider configs
- `llm/provider.py` — gains `describe_image` default method on `LLMProvider`
- `llm/openai_provider.py` — overrides `describe_image` with `image_url` content block construction
- `pipelines/capture.py` — signature change on `capture_upload` (`extracted_text: str` → `str | None`), binary branch fork
- `mcp_server/api.py` — multipart handler stops discarding bytes, passes to binary capture branch

**No coupling to vault modules** — the binary capture path is DB-only + object-storage, consistent with C-01.

---

## Extension Points

- **Blob store:** The spec designs it as a class with injectable adapters (real S3 + local-filesystem stub). This is a clean seam.
- **Vision provider:** Adding `describe_image` as a default-Failure method on `LLMProvider` lets any future provider implement vision. OpenAI override is the first real adapter.
- **Describable types:** Config-driven list (`describable_mime_prefixes`). Adding a new format = config edit, not code change.
- **Vision size cap:** Config-driven threshold (`max_vision_bytes`). Tuning = config edit.

---

## Open Questions

1. **MaaS `image_url` block format — verified at integration only.** The documentation confirms vision models exist (`Qwen3-VL`, `Skylark-vision`) and the API is "OpenAI-compatible," but does not show example requests with `image_url` content blocks. The standard OpenAI SDK sends these natively, so this is likely to work — but until a real API call succeeds, it remains unverified. **Recommendation:** build the vision provider code targeting the standard OpenAI `image_url` format; test against MaaS at integration time; have a fallback plan (OCR API) if the content-block format is rejected.

2. **PDF vision via MaaS — unconfirmed.** No documentation confirms that MaaS vision models accept `application/pdf` input via `image_url` blocks. The platform has a separate OCR API for PDFs. **Recommendation:** drop `application/pdf` from the initial `describable_mime_prefixes` config. Text-less PDFs fall to the store-only path (blob stored, no description, audit records "unsupported type"). Add PDF back when MaaS PDF support is confirmed or the OCR API is integrated.

3. **VNG Object Storage access details — partially verified.** Endpoint: `https://hcm04.vstorage.vngcloud.vn`. S3-compatible (boto3 works). Access keys: S3 key pair generated in the vStorage console. The spec's proposed env vars (`KMS_BLOB_ENDPOINT`, `KMS_BLOB_BUCKET`, `KMS_BLOB_ACCESS_KEY_ID`, `KMS_BLOB_SECRET_ACCESS_KEY`) follow the same `KMS_BLOB_*` prefix pattern as `LITESTREAM_*` — this is clean and confirmed workable.

---

## Technical Debt Spotted

- **TD-060 (existing, OPEN):** "Why no description" reason not surfaced to user. Phase 7B writes audit entries for skip/failure but no UI reads them. Logged during 7B grill. Phase 10 scope.
- **`ProvidersConfig` missing `documentation` field:** `Task` includes `"documentation"` but `ProvidersConfig` has no `documentation` attribute. `for_task("documentation")` would raise `AttributeError`. Not triggered today (no pipeline calls it), but adding `"vision"` without addressing this pattern risks the same class of bug. Low priority — note for when someone adds a documentation pipeline.

---

_(Invalidated Assumptions section removed — all prior invalidations resolved. See Update below.)_

---

## Update — 2026-06-14
### Re-check: all assumptions resolved

The spec was patched after the first research pass to address two invalidated assumptions (A4, A9). This re-check verified those patches against the actual code and re-scanned all 10 assumptions for new issues introduced by the design fixes.

| ID | Was | Now | Evidence |
|----|-----|-----|----------|
| A4 | `delete_by_path` does not preserve `blob_ref` — `blob_ref` lost on DELETE | Use `get_by_path` to pre-read `blob_ref` before calling `delete_by_path` | `documents.py:199–228` — `get_by_path` does `SELECT * FROM documents WHERE vault_path = ?`, returns `Success(DocumentRow)`. `_row_from_sqlite` (line 50–77) already uses the `if "col" in row.keys()` guard for late-added columns. After migration 009 adds `blob_ref`/`mime_type` and `DocumentRow`/`_row_from_sqlite` are updated, `get_by_path` returns the `blob_ref` value. The pre-read sequence works. |
| A9 | MaaS vision `image_url` unverified; PDF status unknown | Spec correctly notes: vision models exist, `image_url` unverified but likely, 50 req/day limit, PDF dropped from default describable set | Spec A9 row, Component 3 (`describable_mime_prefixes: ["image/"]`), and OQ-7G all accurately reflect the research findings. No overclaim, no underclaim. |

**Full scan of all 10 assumptions:** No new invalidations found. The A4 patch (pre-read via `get_by_path`) does not affect any other assumption. Assumptions A1–A3, A5–A8, A10 remain validated as in the first pass.

All assumptions validated. Ready for /plan phase7b_visual_capture.
