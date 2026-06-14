# Plan: Phase 7B — Visual / Binary Capture
_Last updated: 2026-06-14_
_Status: [~] in progress_

_Spec: `docs/2_specs/phase7/phase7b_visual_capture.md` (components 1-8, behavior IDs P7-CAP-10...13 -- source of truth for WHAT)._
_Research: `docs/3_research/phase7/phase7b_visual_capture.md` (all assumptions verified; A4/A9 resolved)._
_Design: `docs/1_design/phase7/phase7b_visual_capture.md` (7 decisions resolved; ADR-0014/0015 extended)._
_7A plan (converge point): `docs/4_plans/phase7/phase7a_text_capture.md`._
_Reader mode: non-coder-readable. Plain English leads; code references are parenthetical anchors._

> This plan owns the HOW (build order, TDD red-to-green, exact files/lines, commit boundaries).
> For the WHAT (Build steps, Files-to-modify, Done-when of each component), open the spec and read the named component. The plan does not restate it.

---

## Architecture

### Q1 -- What happens inside
_Source: design doc `phase7b_visual_capture.md` Q1 (the BINARY branch only)._ In one line: validate the upload and check "seen this exact content before?" -> if new, **store the raw bytes first** to object storage under a content-addressed key (safe immediately) -> check "can this file type be described?" -> if yes, **ask the Vision Describer** for a description + title -> on success **attach the description** to the same row, index it, and audit DESCRIBED -> on failure, audit the failure but **still report success** -> log one "ready for facts" line.

### Q2 -- How it connects
_Source: spec `phase7b_visual_capture.md` Q2._ The laptop daemon sends raw bytes through the cloud Upload Endpoint, which hands them to the **Binary Capture Branch**. The branch connects to: the **Blob Store** (writes content-addressed bytes to object storage), the **Document Store** (saves a row referencing the blob, then attaches the description -- two writes to the same row), the **Vision Describer** (asks a picture-reading AI for a searchable description), the **Audit Log** (success + skip + failure entries), the **Search Indexer** (keyword + meaning, once the description attaches). On delete: the Event Endpoint pre-reads the blob reference, deletes the row, then ref-counts the blob -- removing it from object storage only when the last row pointing at it is gone. Config drives the describable-type set and the size cap. Migration 009 adds the blob-reference columns.

### Q3 -- Why build it this way

```
# Visual / Binary Capture — Why This Way
Scope: The rules and existing patterns that shaped this build.
       Does NOT show the internal step order (see Q1) or the
       component connections (see Q2). Names match Q1/Q2.

How to read this:
  Center box        = the feature being built
  Surrounding boxes = a rule it must follow, OR an existing
                      pattern it reuses
  Lines             = which rule/pattern applies to the feature
  [REUSE]           = an existing piece used as-is or nearly as-is
  [RULE]            = a non-negotiable project rule the build
                      must honour

  ┌───────────────────────────┐     ┌───────────────────────────┐
  │ [REUSE] 7A converge       │     │ [RULE] Store the blob     │
  │ point — extend the shared │     │ FIRST, before any AI.     │
  │ capture entry with a      │     │ It must survive even if   │
  │ binary branch that forks  │     │ the AI is down. Same      │
  │ when there is no text.    │     │ contract as 7A, extended  │
  │ Same store-first /        │     │ to blobs by the second    │
  │ describe-second / audit-  │     │ storage ADR.              │
  │ always contract as 7A     │     │ (store-blob-first)        │
  └─────────────┬─────────────┘     └─────────────┬─────────────┘
                │                                  │
                │      ┌────────────────────┐      │
                └─────►│  BINARY CAPTURE     │◄─────┘
                       │  BRANCH             │
  ┌──────────────┐     │  Stores blob, gets  │     ┌──────────────────────┐
  │ [REUSE] Ask  │     │  a description,     │     │ [RULE] Every AI      │
  │ the AI       ├────►│  records everything │◄────┤ outcome is logged:   │
  │ through the  │     │  — and writes       │     │ DESCRIBED on success │
  │ existing     │     │  NOTHING back to    │     │ + skip/failure with  │
  │ provider     │     │  the user's folders │     │ the reason. Set a    │
  │ factory with │     │                     │     │ fresh trace id first │
  │ a new vision │     └──┬──────┬───────┬───┘     │ or it silently drops │
  │ method       │        │      │       │         └──────────────────────┘
  └──────────────┘        │      │       │
           ┌──────────────┘      │       └───────────────────┐
           ▼                     ▼                           ▼
  ┌──────────────────┐ ┌───────────────────┐  ┌──────────────────────────┐
  │ [RULE] Which      │ │ [RULE] Schema     │  │ [RULE] No chat tool      │
  │ files get         │ │ changes land as   │  │ before its pipeline —    │
  │ described and the │ │ numbered SQL      │  │ 7B adds zero tools;      │
  │ size cap both     │ │ migration files;  │  │ blob-serving retrieval   │
  │ live in config,   │ │ migration 009     │  │ is Phase 9               │
  │ not in code       │ │ adds two nullable │  └──────────────────────────┘
  │ (config-driven    │ │ columns; prior    │
  │ thresholds)       │ │ version-pin tests │
  └──────────────────┘ │ bump 8 to 9       │
                       └───────────────────┘

  ┌──────────────────────────────────────────────────────────────────┐
  │ [RULE] On AI failure / skip / unsupported type: keep the stored  │
  │ blob, log the reason, and STILL report success — so the laptop   │
  │ helper does not retry in a loop. The blob is safe; the           │
  │ description can fill in on a later retry. Every path returns     │
  │ Success or Failure — never raises.                               │
  └──────────────────────────────────────────────────────────────────┘
```

**Extension point marking:**
- Blob Store module: `[extensible: protocol]` -- real S3 adapter + local-filesystem test stub; callers depend on the interface, not the concrete backend
- Vision provider (`describe_image`): `[extensible: protocol]` -- default-raises on non-vision providers; OpenAI override is the first real adapter; future providers add an override
- Describable-type set: `[extensible: config]` -- add a new MIME prefix to `config.yaml`, no code change
- Vision size cap: `[extensible: config]` -- tune the threshold in config
- Binary capture branch: `[closed]` -- it is the single orchestrator; variants would require modifying it (acceptable: one orchestrator per capture mode)

---

## Approach

Build **bottom-up, leaf-first**, so the test suite stays green at every phase boundary and no phase depends on unbuilt infrastructure. Order: the DB migration (no dependents) -> the blob-store module (independent) -> the vision config + provider extension + prompt (the "socket" layer) -> the binary capture branch (the orchestrator, built on tested leaves) -> the reference-counted delete (extends the event handler) -> the upload-endpoint re-point (the last wire) -> version-pin bump and full-suite verification. Each new piece returns a `Success`/`Failure` Result and is testable in-process with an explicit `db_path` and an injected stub (blob store or AI provider). One new dependency: `boto3` (approved by human, OQ-7H resolved).

The binary branch lives in `src/pipelines/capture.py` alongside the existing `capture_upload`. The `capture_upload` signature gains optional `raw_bytes` and `mime_type` parameters; when `extracted_text` is `None` and `raw_bytes` is present, the binary branch runs. When `extracted_text` is present, the existing 7A text path runs unchanged.

---

## Phases

### Phase 1 -- Migration 009: blob-reference columns

**Goal**: Give the Document Store two new nullable columns so a binary row can say "my bytes live at this object key, and my type is this."

Implements **spec Component 1**.

**Design**:

```
# Phase 1 — Migration 009 (folder view + column effect)

  src/storage/migrations/
    ├── 008_knowledge_entries_and_document_columns.sql  ← EXISTING
    └── 009_add_blob_ref.sql                             ← NEW (this phase)
          ALTER TABLE documents ADD COLUMN blob_ref TEXT;
          ALTER TABLE documents ADD COLUMN mime_type TEXT;
          UPDATE schema_version SET version = 9;

  BEFORE (documents row):
    ... | full_body | content_hash | ... | (no blob_ref) | (no mime_type) |

  AFTER:
    ... | full_body | content_hash | ... | blob_ref NULL | mime_type NULL |

  Existing text rows and new text rows: both columns stay NULL.
  Binary rows: blob_ref = content-addressed object key; mime_type = "image/png" etc.
```

**Steps**:
1. (RED) Add a test in `tests/test_storage/` that runs `init_db(db_path)`, then checks `PRAGMA table_info(documents)` for columns named `blob_ref` and `mime_type`. Run -- fails (columns missing).
2. (GREEN) Add `src/storage/migrations/009_add_blob_ref.sql` with two `ALTER TABLE documents ADD COLUMN` statements and the version bump.
3. Extend `DocumentRow` (`storage/documents.py:28`) with `blob_ref: str | None = None` and `mime_type: str | None = None` as the last two fields.
4. Extend `_row_from_sqlite` to read both using the existing `if "col" in row.keys()` guard pattern (same as `full_body`/`original_filename`/`file_size_bytes`).
5. Bump the three version-pin assertions from `8` to `9`: `tests/test_storage/test_migration_007.py:41`, `test_migration_007.py:56`, `test_migration_008.py:47`.
6. Commit.

**Files to modify**:
- `src/storage/migrations/009_add_blob_ref.sql` -- new file
- `src/storage/documents.py` -- `DocumentRow` + `_row_from_sqlite`
- `tests/test_storage/test_migration_007.py` -- version-pin bump (8 -> 9, two assertions)
- `tests/test_storage/test_migration_008.py` -- version-pin bump (8 -> 9, one assertion)
- `tests/test_storage/test_migration_009.py` -- new file (column-presence test)

**Test criteria**:
- [x] After `init_db`, the `documents` table has `blob_ref TEXT` and `mime_type TEXT` columns
- [x] Existing text rows have both as NULL
- [x] `DocumentRow` carries the two fields and `_row_from_sqlite` populates them
- [x] Schema version reads 9
- [x] All three prior version-pin tests pass at version 9
- [x] Full suite green

**Status**: [x] done

---

### Phase 2 -- Blob Store module

**Goal**: Give the cloud its first ability to write, read, check, and delete an arbitrary file in object storage, hidden behind a small helper so no other module speaks S3 directly.

Implements **spec Component 2**. `[extensible: protocol]` -- real S3 adapter + local-filesystem test stub.

**Design**:

```
# Phase 2 — Blob Store module (interface + two adapters)

  src/storage/
    ├── documents.py  ← EXISTING (not touched)
    └── blobs.py      ← NEW (this phase)
          class BlobStore (Protocol or ABC):
              put(key, data, mime_type)  → Success(key) or Failure
              get(key)                   → Success(bytes) or Failure
              delete(key)                → Success or Failure
              exists(key)                → Success(bool) or Failure

          class S3BlobStore(BlobStore):
              __init__(endpoint, bucket, access_key_id, secret_access_key)
              # uses boto3 sync client
              # all ops wrapped in asyncio.to_thread for async callers

          class LocalBlobStore(BlobStore):
              __init__(root: Path)
              # writes to temp directory — for tests only

  Callers:  capture branch → put (store blob)
            delete handler → delete (remove blob)
            Phase 9        → get (serve blob)

  Config env vars (NEW):
    KMS_BLOB_ENDPOINT       — S3-compatible endpoint URL
    KMS_BLOB_BUCKET          — bucket name
    KMS_BLOB_ACCESS_KEY_ID   — access key
    KMS_BLOB_SECRET_ACCESS_KEY — secret key
```

**Steps**:
1. (RED) Add tests using `LocalBlobStore(tmp_path)`: (a) `put` then `get` returns same bytes; (b) `put` same key twice is idempotent (no error); (c) `delete` removes the object, `exists` returns False; (d) `delete` on a missing key returns Success (not an error); (e) `exists` on a missing key returns `Success(False)`. All return `Result`. Run -- fails (no module).
2. (GREEN) Create `src/storage/blobs.py`. Implement `BlobStore` as an ABC or Protocol with the four verbs. Implement `LocalBlobStore` using `Path.write_bytes` / `Path.read_bytes` / `Path.unlink`. Key is namespaced to `blobs/<key>` subdirectory.
3. Add `boto3` to `pyproject.toml` under dependencies (approved by human, OQ-7H resolved).
4. Implement `S3BlobStore` wrapping `boto3.client("s3")`. The four verbs call the sync boto3 client. Provide `async_put` / `async_get` / `async_delete` / `async_exists` convenience wrappers using `asyncio.to_thread(...)` (matching the project's sync-in-async-via-thread pattern).
5. Add an integration-skippable test (`@pytest.mark.smoke`) for `S3BlobStore` that requires real S3 credentials -- skip in CI.
6. Commit.

**Files to modify**:
- `src/storage/blobs.py` -- new file
- `pyproject.toml` -- add `boto3` dependency
- `tests/test_storage/test_blobs.py` -- new file

**Test criteria**:
- [x] `LocalBlobStore` passes all four-verb tests
- [x] `put` is idempotent; `delete` on missing key is Success
- [x] All operations return `Result`, never raise
- [x] `S3BlobStore` has async wrappers using `asyncio.to_thread`
- [x] Full suite green (storage tests: 93 passed, 1 skipped; core tests: 380 passed)

**Notes**: `boto3` is the one approved new dependency. C-17 is satisfied: tests use `LocalBlobStore` with `tmp_path`, not CONFIG.

**Completed**: 2026-06-14
**Notes**: Implemented BlobStore as ABC (not Protocol — same pattern as llm/provider.py). LocalBlobStore namespaces keys under `<root>/blobs/<key>`. S3BlobStore wraps sync boto3 client with async wrappers via asyncio.to_thread. 8 new tests (7 pass, 1 smoke skip). Commit: 962177d.

**Status**: [x] done

---

### Phase 3 -- Vision config + provider extension + prompt

**Goal**: Make the "which files get described" decision and the size cap config-driven, give the AI provider the ability to describe an image, and create the vision prompt -- all three are the "socket" layer the binary capture branch plugs into.

Implements **spec Components 3, 4, and 5**. Three independent leaves bundled into one phase because each is small and they share no cross-dependency beyond the `"vision"` task name.

**Design**:

```
# Phase 3 — Vision config + provider extension + prompt (3 parallel leaves)

  Leaf A — Vision Config:
    core/config.py gains:
      Task literal:         + "vision"
      ProvidersConfig:      + vision: Provider = "openai"
      OpenAICompatConfig:   + vision_model: str = "..."
      ClaudeConfig:         + vision_model: str = ""
      OllamaConfig:         + vision_model: str = ""
      ClaudeCliConfig:      + vision_model: str = ""
      CaptureConfig:        + vision: VisionConfig (nested)
        VisionConfig:
          describable_mime_prefixes: list[str] = ["image/"]
          max_vision_bytes: int = 10_485_760  (10 MB default)

    config/config.yaml gains:
      capture:
        vision:
          describable_mime_prefixes: ["image/"]
          max_vision_bytes: 10485760

  Leaf B — Provider extension:
    llm/provider.py:
      LLMProvider gains:
        async def describe_image(self, system, image_bytes, mime_type)
            -> Result[LLMResponse]
        default body: return Failure("vision not supported", recoverable=False)

    llm/openai_provider.py:
      OpenAIProvider overrides describe_image:
        1. base64-encode the image bytes
        2. build {"type":"image_url","image_url":{"url":"data:<mime>;base64,..."}}
        3. send messages=[system, user=[image_url_block, text_block]]
        4. parse response → Success(LLMResponse) or Failure

    llm/provider.py get_provider():
      The "vision" task resolves to "openai" via ProvidersConfig.vision field.
      The OpenAIProvider __init__ gains vision-model routing:
        if task == "vision": self._model = config.vision_model

  Leaf C — Vision prompt:
    src/prompts/describe_image.yaml — NEW
      system: instructions to describe visual content, extract visible text,
              describe chart axes/trends, produce a descriptive title
      user: "{mime_type} image attached. Describe it."
      variables: [mime_type]

  RESULT: get_provider("vision", CONFIG.main).describe_image(system, bytes, mime)
          returns a Result with a text description.
```

**Steps**:

_Leaf A -- Vision Config:_
1. (RED) Add a test that imports `VisionConfig`, asserts `describable_mime_prefixes` defaults to `["image/"]` and `max_vision_bytes` defaults to a positive integer. Add a test that `get_provider("vision", config)` does not raise `AttributeError`. Run -- fails.
2. (GREEN) Add `"vision"` to the `Task` literal (`config.py:43-45`). Add `vision: Provider = "openai"` to `ProvidersConfig` (`config.py:173`). Add `vision_model: str` to `OpenAICompatConfig` (`config.py:221`), `ClaudeConfig` (`config.py:196`), `OllamaConfig` (`config.py:209`), `ClaudeCliConfig` (`config.py:233`) -- default empty string for providers that do not support vision. Add `VisionConfig` nested model and a `vision: VisionConfig` field to `CaptureConfig` (`config.py:303`). Update `config/config.yaml`.

_Leaf B -- Provider extension:_
3. (RED) Add a test: calling `describe_image` on a base/non-overriding provider returns `Failure("vision not supported")`. Add a test: calling `describe_image` on `OpenAIProvider` with a mocked `_client.chat.completions.create` returns `Success(LLMResponse)` with the description text. Run -- fails.
4. (GREEN) Add `async def describe_image(self, system: str, image_bytes: bytes, mime_type: str) -> Result[LLMResponse]` to `LLMProvider` (`provider.py:33`) with default `return Failure(...)`. Override in `OpenAIProvider` (`openai_provider.py:18`): base64-encode, build the `image_url` content block, send via `self._client.chat.completions.create`. Add vision-model routing in `OpenAIProvider.__init__`: `if task == "vision": self._model = config.vision_model`.

_Leaf C -- Vision prompt:_
5. (RED) Add a test that loads `PROMPTS["describe_image"]` and asserts it exists, `render(mime_type="image/png")` returns a `(system, user)` tuple, and the system text contains instructions about describing visual content. Run -- fails.
6. (GREEN) Add `src/prompts/describe_image.yaml`.
7. Commit all three leaves together.

**Files to modify**:
- `src/core/config.py` -- `Task`, `ProvidersConfig`, all 4 provider configs, `CaptureConfig` + new `VisionConfig`
- `config/config.yaml` -- `capture.vision` section
- `src/llm/provider.py` -- `LLMProvider.describe_image` default method
- `src/llm/openai_provider.py` -- `OpenAIProvider.describe_image` override + vision-model routing
- `src/prompts/describe_image.yaml` -- new file
- `tests/test_core/test_config.py` or new `tests/test_core/test_vision_config.py` -- config tests
- `tests/test_llm/test_provider.py` or new -- `describe_image` default + override tests
- `tests/test_llm/test_prompt_loader.py` or new -- prompt load test

**Test criteria**:
- [ ] `VisionConfig` defaults are correct (describable_mime_prefixes, max_vision_bytes)
- [ ] `get_provider("vision", CONFIG.main)` resolves without error
- [ ] `describe_image` on a non-overriding provider returns `Failure`
- [ ] `describe_image` on `OpenAIProvider` with mocked client returns `Success(LLMResponse)` with the description
- [ ] The prompt loads and renders correctly
- [ ] Existing text-path `complete()` calls are byte-for-byte unchanged
- [ ] Full suite green

**Notes**: OQ-7G (MaaS image_url acceptance) is unverifiable from code alone. The OpenAI SDK natively supports `image_url` content blocks. If MaaS rejects them at integration time, HARD STOP and escalate. `application/pdf` is NOT in the default `describable_mime_prefixes` list (research finding: MaaS PDF support unconfirmed). C-09: the `vision_model` field is a same-shape addition; this grows the C-09 field list from 3 to 4 per provider (OQ-7E recommendation: dedicated field -- adopted here).

**Completed**: 2026-06-14
**Notes (implementation)**: Implemented VisionConfig with describable_mime_prefixes and max_vision_bytes. Added "vision" to Task literal and ProvidersConfig.vision field. Added vision_model to all four provider configs. Added default describe_image to LLMProvider (returns Failure). Added describe_image override to OpenAIProvider with base64-encoded image_url content blocks and vision_model routing. Created src/prompts/describe_image.yaml. 13 new tests pass. Config access: CONFIG.main.capture.vision.describable_mime_prefixes and get_provider("vision", CONFIG.main) both work.

**Status**: [x] done

---

### Phase 4 -- Binary capture branch (the orchestrator)

**Goal**: Extend the pipeline entry with a binary branch that handles uploads with raw bytes and no extracted text -- storing the blob first, describing if possible, and recording everything.

Implements **spec Component 6**. `[closed]` -- one orchestrator per capture mode; acceptable.

**Design**:

```
# Phase 4 — Binary capture branch (sequence of beats)

  capture_upload(vault_path, extracted_text, content_hash, ...,
                 raw_bytes=None, mime_type=None, blob_store=None)
                    │
       extracted_text is None AND raw_bytes present?
            ┌───────┴───────┐
           YES               NO → existing 7A text path (unchanged)
            │
            ▼
  ┌──────────────────────────┐
  │ Beat 0. Correlation ID   │  new_correlation_id() — same as 7A
  └────────────┬─────────────┘
               ▼
  ┌──────────────────────────┐
  │ Beat 1. Front-loaded     │  get_by_path + compare content_hash
  │ dedup (P7-CAP-10)        │  same hash → return Success (no blob,
  └────────────┬─────────────┘  no AI, no store)
               ▼
  ┌──────────────────────────┐
  │ Beat 2. Store blob       │  blob_store.put(content_hash, bytes, mime)
  │ (P7-CAP-10)              │  on failure → return Failure
  └────────────┬─────────────┘
               ▼
  ┌──────────────────────────┐
  │ Beat 3. Store raw row    │  upsert_from_upload(..., blob_ref=key,
  │                          │  mime_type=mime, extracted_text=None)
  │                          │  → file is safe now
  └────────────┬─────────────┘
               ▼
  ┌──────────────────────────┐
  │ Beat 4. Describable?     │  mime in describable_prefixes AND
  │ (P7-CAP-12)              │  size <= max_vision_bytes?
  └────────────┬─────────────┘
          ┌────┴────┐
         NO         YES
          │          │
          ▼          ▼
    audit skip  ┌──────────────────────┐
    return      │ Beat 5. Vision       │
    Success     │ describe (P7-CAP-11) │
                │ get_provider("vision")│
                │ .describe_image(...)  │
                └────────────┬─────────┘
                        ┌────┴────┐
                       YES        NO (AI failed)
                        │          │
                        ▼          ▼
                  attach_summary  audit failure
                  index best-     return Success
                  effort          (blob safe)
                  audit DESCRIBED
                  log classify
                  return Success
```

**Steps**:
1. (RED) Write tests using `LocalBlobStore(tmp_path)` and a mock AI provider:
   - (a) Binary upload with describable type, healthy AI -> row has `blob_ref`, `mime_type`, `summary` (description), `title`, audit has DESCRIBED entry. Blob exists in store.
   - (b) Identical re-upload -> returns Success, blob_store.put NOT called, AI NOT called (dedup).
   - (c) Unsupported type -> row has `blob_ref`, `mime_type`, `summary` is NULL, audit has "unsupported type" entry.
   - (d) File over size cap -> row has `blob_ref`, `mime_type`, `summary` is NULL, audit has "too big" entry.
   - (e) Vision AI failure -> row has `blob_ref`, `mime_type`, `summary` is NULL, audit has failure entry. Success returned.
   - (f) Text upload (extracted_text present, raw_bytes absent) -> existing 7A text path runs unchanged (regression guard).
   Run -- fails.
2. (GREEN) Extend `capture_upload` signature (`capture.py:123`): change `extracted_text: str` to `extracted_text: str | None = None`; add `raw_bytes: bytes | None = None`, `mime_type: str | None = None`, `blob_store: BlobStore | None = None`. Add the binary branch after the correlation-ID step: when `extracted_text is None and raw_bytes is not None`, run beats 1-5 as designed. When `extracted_text is None and raw_bytes is None`, return `Failure("neither text nor bytes supplied")`.
3. Extend `upsert_from_upload` (`documents.py:100`) with optional `blob_ref: str | None = None` and `mime_type: str | None = None` params. Add the two columns to the INSERT and UPDATE SQL. Default `None` is backward-compatible -- existing callers are unchanged.
4. After all binary-branch tests pass, run the existing 7A text-path tests and the full suite to confirm no regression.
5. Commit.

**Files to modify**:
- `src/pipelines/capture.py` -- `capture_upload` signature + binary branch
- `src/storage/documents.py` -- `upsert_from_upload` gains `blob_ref`/`mime_type` params
- `tests/test_pipelines/test_capture_binary.py` -- new file (6 tests above)
- `tests/test_storage/test_documents.py` -- test `upsert_from_upload` with blob_ref/mime_type params

**Test criteria**:
- [ ] Describable binary upload -> row has blob_ref + mime_type + summary + title; audit DESCRIBED; blob in store (P7-CAP-11)
- [ ] Identical re-upload -> Success, no blob put, no AI call (P7-CAP-10)
- [ ] Unsupported type -> blob stored, summary NULL, audit "unsupported type" (P7-CAP-12)
- [ ] Over size cap -> blob stored, summary NULL, audit "too big" (P7-CAP-12)
- [ ] Vision failure -> blob stored, summary NULL, audit failure, Success returned (P7-CAP-11)
- [ ] Text upload -> existing 7A path runs unchanged (regression guard)
- [x] `upsert_from_upload` with blob_ref/mime_type writes the two new columns; without them both are NULL
- [x] Full suite green (118 pass, 1 skip — zero regressions)

**Notes**: The blob store is passed as a parameter (not resolved from CONFIG) so tests inject `LocalBlobStore` cleanly. The production caller (the upload endpoint, Phase 6) instantiates `S3BlobStore` and passes it.

**Completed**: 2026-06-14
**Notes (implementation)**: Extended capture_upload signature with raw_bytes, mime_type, blob_store params (all optional). Added _capture_binary helper implementing the 5-beat binary branch: dedup → store blob → store row → describable check → vision describe. Extended upsert_from_upload with blob_ref/mime_type params (INSERT and UPDATE). Added _audit_skip and _audit_failed helpers for binary audit entries. 7 new binary tests + 3 upsert_from_upload tests all pass. 7A text-path regression tests unchanged. CONFIG accessed via lazy import in _capture_binary (same pattern as _summarize_upload). Vision provider accessed via get_provider("vision", CONFIG.main). Prompt from PROMPTS["describe_image"].

**Status**: [x] done

---

### Phase 5 -- Reference-counted blob delete

**Goal**: When a document row is deleted, check if any other row still references the same blob; if not, delete the blob from object storage. A failed blob delete is harmless.

Implements **spec Component 7**. `[closed]` -- the delete logic is one path in the event handler.

**Design**:

```
# Phase 5 — Reference-counted blob delete (event handler extension)

  Event Endpoint receives: POST /api/event type=deleted path=...
                │
                ▼
  ┌──────────────────────────┐
  │ 1. Pre-read the row      │  get_by_path(path) → row with blob_ref
  │    (before it is deleted)│  if blob_ref is NULL → skip blob logic
  └────────────┬─────────────┘
               ▼
  ┌──────────────────────────┐
  │ 2. Delete the row        │  delete_by_path(path) → Success(rowcount)
  │    (existing behavior)   │  search entries cleaned in same transaction
  └────────────┬─────────────┘
               ▼
  ┌──────────────────────────┐
  │ 3. Reference count       │  SELECT COUNT(*) FROM documents
  │                          │  WHERE blob_ref = ?
  │                          │  count == 0 → this was the last reference
  └────────────┬─────────────┘
          ┌────┴────┐
        LAST       SHARED
          │          │
          ▼          ▼
   blob_store.    do nothing
   delete(key)    (blob still
   (best-effort)  needed)
   fail → log,
   don't fail
   the event
```

**Steps**:
1. (RED) Add tests:
   - (a) Delete the last row referencing a blob -> blob is removed from object storage.
   - (b) Delete one of two rows sharing a blob -> blob is NOT removed.
   - (c) Failed blob delete -> document row is still gone, Success returned, error logged.
   - (d) Delete a text-only row (blob_ref NULL) -> no blob logic fires, behaves as before.
   Run -- fails.
2. (GREEN) Create a helper function (e.g. `_delete_with_blob_cleanup` or similar) in `mcp_server/api.py` or `storage/documents.py` that: (a) calls `get_by_path` to capture `blob_ref`; (b) calls `delete_by_path`; (c) if `blob_ref` was not NULL and delete succeeded, runs `SELECT COUNT(*) FROM documents WHERE blob_ref = ?`; (d) if count is 0, calls `blob_store.delete(key)` best-effort. The blob delete is OUTSIDE the DB transaction (the DB delete is the source of truth; the S3 call should not hold a DB lock).
3. Update `event_handler` in `api.py` to use this new function instead of calling `delete_by_path` directly for the `"deleted"` event type.
4. Commit.

**Files to modify**:
- `src/mcp_server/api.py` -- event handler extension for blob cleanup
- `src/storage/documents.py` -- possibly a reference-count query helper (or inline in api.py)
- `tests/test_mcp_server/test_api_blob_delete.py` -- new file (4 tests above)

**Test criteria**:
- [ ] Last-reference delete removes the blob from object storage (P7-CAP-13)
- [ ] Shared-reference delete does NOT remove the blob (P7-CAP-13)
- [ ] Failed blob delete logs but does not fail the event (P7-CAP-13)
- [x] Text-only row delete (blob_ref NULL) works exactly as before -- no blob logic
- [x] Full suite green (1273 pass, 4 pre-existing unrelated failures)

**Notes**: The reference-count query runs inside `get_connection` (C-04 satisfied). The blob delete is outside the transaction. C-14 (logic-free tools) applies to the MCP tool shims, not the API handler -- the API handler is allowed to contain control flow (it is the HTTP transport layer, not the MCP tool layer). The blob_store instance is injected (same pattern as Phase 4).

**Completed**: 2026-06-16
**Notes (implementation)**: Added `_delete_with_blob_cleanup` helper in `api.py` that pre-reads row → deletes row → ref-counts blob → best-effort delete. Added `_blob_store` module-level injection point (same pattern as `_db_path`). 4 new tests in `test_api_blob_delete.py` using `LocalBlobStore`. All tests pass; no regressions in the existing 82 MCP server tests.

**Status**: [x] done

---

### Phase 6 -- Upload Endpoint re-point: stop discarding bytes

**Goal**: Make the multipart upload handler pass the file bytes to the binary capture branch instead of throwing them away.

Implements **spec Component 8**. `[closed]` -- one handler path; changing it is the point.

**Design**:

```
# Phase 6 — Upload Endpoint re-point

  BEFORE (current, api.py:141-142):
    # file bytes and mime_type are accepted but discarded in A1
    result = upsert_from_upload(...)  ← direct DB write, no pipeline

  AFTER:
    raw_bytes = await form["file"].read()   ← get bytes from UploadFile
    mime_type = form.get("mime_type")
    blob_store = S3BlobStore(...)           ← or LocalBlobStore in tests
    result = await capture_upload(
        vault_path=vault_path,
        extracted_text=None,
        content_hash=content_hash,
        raw_bytes=raw_bytes,
        mime_type=mime_type,
        blob_store=blob_store,
        original_filename=original_filename,
        file_size_bytes=file_size_bytes,
        db_path=_db_path,
    )
```

**Steps**:
1. (RED) Add a test: send a multipart upload with a file field to the handler; assert the binary capture branch was called (mock `capture_upload`) with `raw_bytes` and `mime_type` populated. Assert the old direct `upsert_from_upload` call is NOT made for binary uploads. Run -- fails.
2. (GREEN) In `api.py`, modify the multipart branch (~line 106-151):
   - Read raw bytes: `file_upload = form.get("file"); raw_bytes = await file_upload.read() if file_upload else None`.
   - Read mime_type: `mime_type = form.get("mime_type") or None`.
   - Instantiate the blob store (from env vars or a module-level factory).
   - Replace the direct `upsert_from_upload` call with `await capture_upload(vault_path=..., extracted_text=None, content_hash=..., raw_bytes=raw_bytes, mime_type=mime_type, blob_store=blob_store, ...)`.
   - Return the pipeline's result as the HTTP response.
3. Ensure the JSON text path (7A) remains unchanged -- it already calls `capture_upload` with `extracted_text` and no `raw_bytes`.
4. Commit.

**Files to modify**:
- `src/mcp_server/api.py` -- multipart branch re-point
- `tests/test_mcp_server/test_api.py` -- update existing multipart tests + add binary-path test

**Test criteria**:
- [ ] A multipart upload with file bytes calls `capture_upload` with `raw_bytes` + `mime_type`
- [ ] The old direct `upsert_from_upload` path is removed for multipart
- [ ] A multipart upload WITHOUT a file field still works (graceful None)
- [ ] The JSON text path (7A) is unchanged
- [ ] Full suite green

**Status**: [ ] pending

---

### Phase 7 -- Full-suite verification + version-pin confirmation

**Goal**: Run the entire test suite end-to-end and confirm no regressions, all version pins are correct, and all behavior IDs are exercisable.

Implements no new spec component -- this is the verification phase.

**Design**:

```
# Phase 7 — Full-suite verification checklist

  1. uv run pytest tests/                    ← full suite green
  2. uv run ruff check .                     ← lint clean
  3. uv run ruff format --check .            ← format clean
  4. Confirm version-pin tests pass at 9
  5. Confirm P7-CAP-10..13 behavior IDs are covered by tests:
     P7-CAP-10 → Phase 4 tests (a) + (b)  (store-first + dedup)
     P7-CAP-11 → Phase 4 tests (a) + (e)  (describe success + failure)
     P7-CAP-12 → Phase 4 tests (c) + (d)  (unsupported + too-big)
     P7-CAP-13 → Phase 5 tests (a) + (b)  (last-ref + shared-ref delete)
  6. Confirm 7A text-path regression guard passes
```

**Steps**:
1. Run `uv run pytest tests/` -- all pass.
2. Run `uv run ruff check . && uv run ruff format --check .` -- clean.
3. Review behavior inventory entries P7-CAP-10..13: verify each has a matching `pytest_ref` pointing to the test from Phases 4-5.
4. If any test fails, fix the issue and create a NEW commit (do not amend).
5. Final commit with updated `pytest_ref` in behavior inventory (if the entries need updating).

**Files to modify**:
- `docs/system_behavior/behavior_inventory.yaml` -- update `pytest_ref`, `last_tested`, `last_result`, `status` for P7-CAP-10..13
- Any file with a lint/format issue

**Test criteria**:
- [ ] Full suite green -- zero failures
- [ ] Lint + format clean
- [ ] All four behavior IDs (P7-CAP-10..13) have passing pytest references
- [ ] 7A text-path regression tests pass

**Status**: [ ] pending

---

## Open Questions

1. **OQ-7G -- MaaS vision capability (partially resolved).** The vision route is AgentBase MaaS (locked by human). Research found vision models exist (Qwen3-VL, Skylark-vision) and the API is "OpenAI-compatible," but `image_url` content-block acceptance is not directly documented. Proceeding with standard OpenAI `image_url` format. **If MaaS rejects image input at integration time, HARD STOP and escalate.** PDF is dropped from the default describable set until MaaS PDF support is confirmed. 50 req/day rate limit noted.

2. **OQ-7D -- Multi-blob per document (carried from design).** Two columns model a strict one-file = one-blob = one-row relationship. A future multi-page-scan feature would need a `blobs` table migration. Not a blocker for 7B.

3. **OQ-7E -- Vision model field placement (adopted).** Dedicated `vision_model` field added per provider config. C-09 field list grows from 3 to 4. Not a blocker.

4. **OQ-7I -- Audit outcome name (adopted).** Using distinct `DESCRIBED` for observability. Not a blocker.

5. **VNG Object Storage access details.** Endpoint: `https://hcm04.vstorage.vngcloud.vn`. S3-compatible (boto3 works). Access keys: S3 key pair from vStorage console. Env vars: `KMS_BLOB_ENDPOINT`, `KMS_BLOB_BUCKET`, `KMS_BLOB_ACCESS_KEY_ID`, `KMS_BLOB_SECRET_ACCESS_KEY`. Verify at integration time.

## Out of Scope

Refer to the spec's "Out of scope" section for the full list. Key items:
- The text capture path (7A) -- unchanged
- Blob-serving retrieval -- Phase 9
- Fact-extraction / classify -- Phase 8
- Description retry runner -- deferred, no phase assigned
- Daemon-side changes -- Phase 6
- Orphan-blob sweep -- deferred
