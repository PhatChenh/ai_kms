# Research: Phase 3 Session B — Query Path (Hybrid Search)
_Last updated: 2026-06-10_

## Overview

This research verifies the spec for the **hybrid search** feature — the system's first ability to find the right notes for a question. A caller asks with an optional question, an optional project, and an optional date window; the system narrows the note pile, runs a word search and a meaning search, blends the rankings, has a small "second-opinion" model re-score the top few, and returns cheap summary cards (never the full note). The eventual consumer is the Phase 4 MCP AI, not a human at a terminal.

I independently re-verified all 13 original assumptions (A1–A13) against the real code — and, for the behavior claims, by actually running queries against a freshly-migrated SQLite database with the project's own connection factory and real embedding model. I did **not** trust the design doc's "verified" labels; I re-ran every empirical probe from scratch.

**Re-check update (2026-06-10):** the team applied a root-cause fix at capture (design "Revision R1" / spec "Component 0 — Descriptive Title at Capture") to resolve the one invalidated assumption (A5). This pass re-verifies A5 against the *actual* code and verifies the six new assumptions the fix introduced (A14–A19). I did **not** trust the spec's "RESOLVED-BY-R1" marker — I re-proved the whole chain (binary path → metadata stage → frontmatter round-trip → `documents.title` → re-ranker card) at file:line and with in-process probes.

**Bottom line: A5 is GENUINELY RESOLVED by the proposed mechanism — but the fix introduces ONE new invalidated assumption, A15.** The chain holds: a binary's `documents.title` would carry the AI descriptive title (not `report.pdf`) once R1 ships, because the binary capture path provably reaches the metadata stage and `mr.ai_title` is always non-empty there. However, the spec's claim that "no existing test asserts an exact-dict on serialized frontmatter" is wrong: one test (`test_parse_minimal_note`) asserts `meta.extra == {"title": "T"}` — adding `title` to `_KNOWN_KEYS` reroutes that key onto the model field, so the test breaks. This is a known, one-test mechanical fix, not a redesign — but it must be flagged so the planner schedules the test update. **The invalidation set is therefore NOT empty (A15), but A15 is mechanical (a stale test, not a design flaw).**

The three original load-bearing rows are unchanged and still clear:
- **A1 (filtered KNN) — VALIDATED by execution.** The `MATCH + k + IN (...)` form returns real distances scoped to the candidate set; the no-`MATCH` form returns all-NULL distances exactly as the design warned. The candidate set acts as a partition before the k-nearest pick. Safe to build on.
- **A2 (bare-query embedding) — VALIDATED, low risk.** Embedding the bare query surfaces the right note above a distractor, and (measured) actually gives *cleaner* separation than wrapping the query in the stored template. No mitigation needed.
- **A3 (date format) — VALIDATED with one caveat.** `updated_at` is full-width `YYYY-MM-DD HH:MM:SS` and string comparison is chronological. The caveat: `created_at` can be date-only (`YYYY-MM-DD`), so the date filter must use `updated_at`, which the spec already does.

The two new load-bearing rows from R1 both hold:
- **A16 (binary reaches metadata) — VALIDATED.** The capture pipeline is a single uniform stage list (`extract → enrich_urls → summarize → metadata → apply_location_tags → classify_step → store`) applied to ALL files; the only `is_md` branch is *inside* `store()`, which runs AFTER `metadata` already set `mr.ai_title`. And `_parse_metadata_json` guarantees `ai_title` is never empty (falls back to the filename stem). So the keystone holds: binaries get a real title.
- **A18 (attachment-summary contract) — VALIDATED.** Reconcile Stage 4 keys purely off `note.metadata.type == "attachment-summary"`; adding a `title=` keyword to `sibling_meta`/`marker_meta` does not touch `type`, and the new `title:` frontmatter key round-trips onto the model without disturbing the type field.

No Q4 conflict diagram is warranted: A5 is resolved, and the sole remaining invalidation (A15) is a mechanical stale-test fix, not an architectural mismatch.

---

## Key Components

These are the existing pieces the spec builds on. All locations confirmed by reading the files.

- **Migration `007_search_indexes.sql`** (`src/storage/migrations/007_search_indexes.sql`) — creates the two search tables. `embeddings_vec` is `vec0(vault_path TEXT PRIMARY KEY, embedding FLOAT[384])`. `notes_fts` is `fts5(vault_path UNINDEXED, title, summary, body, tokenize='porter unicode61')`. Confirmed: `body` is the 4th declared column, i.e. **column index 3** (0-based) for `snippet()`.
- **Connection factory** (`src/storage/db.py::get_connection` / `_connect`) — every connection runs `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, and loads sqlite-vec. `readonly=True` skips the commit. This is the single DB door for `retrieval/` (C-04).
- **Meaning Indexer** (`src/retrieval/embeddings.py`) — `_get_model()` (lazy, module-cached `SentenceTransformer`), `_build_context_text(title, note_type, tags, summary)` (the composite string stored docs are embedded from), and `index_embedding(...) -> Result[None]`. All return `Result`.
- **Word Indexer** (`src/retrieval/keyword.py`) — `index_keywords(vault_path, title, summary, body, db_path=None) -> Result[None]`. Returns `Result`.
- **Note Catalog** (`src/storage/documents.py`) — `DocumentRow` (13 fields incl. `project`, `status`, `key_topics`, `created_at`, `updated_at`), `get_by_path(...) -> Result[DocumentRow | None]` (returns `Success(None)` when absent, never raises), `all_paths(...) -> Result[list[tuple[str,str]]]`. No filter-by-project/date function exists yet.
- **SearchConfig** (`src/core/config.py:303`) — `embedding_model=all-MiniLM-L6-v2`, `reranker_model=cross-encoder/ms-marco-MiniLM-L-6-v2`, `max_candidates=20`, `max_results=10`. Exposed at `CONFIG.main.search` (`MainConfig.search` field, line 342). Matches `config.yaml` (lines 69–73).
- **Full-Note Reader** (`src/vault/reader.py::read_note(path: Path) -> Result[Note]`) — takes an absolute path. Not called by `search()`; it is the caller's lazy-fetch path.
- **Project Registry** (`src/vault/registry.py`) — `ProjectRegistry.all_project_names` (a `@property` returning `frozenset[str]`), `ProjectRegistry.groups` (dict; keys = domain names incl. `Uncategorized`). `LiveRegistry.get_groups()` returns a copy of that dict. See A12 nuance below.
- **Classify engine** (`src/pipelines/classify.py`) — `classify(subject, valid_destinations: str, config) -> Result[ClassifyResult]`; validates `project` and `primary_domain` against ONE pooled set via `_destination_names(valid_destinations)`.
- **CLI search stub** (`src/cli/main.py:108`) — `@click.argument("query")` then `raise NotImplementedError`. The argument is **required and positional** today; the spec needs it optional.

---

## How It Works (verified flow)

When a search runs: the Search Coordinator asks the Note Catalog for candidate `vault_path`s (filter by project and/or `updated_at >= bound`, or "all" when nothing given). If there is no question, it sorts candidates by `updated_at` descending, caps, and returns cards (no model loaded). If there is a question, the Hybrid Ranker runs two searches scoped to those candidates — FTS5 BM25 over `notes_fts` and sqlite-vec KNN over `embeddings_vec` — blends them with Reciprocal Rank Fusion, and the Re-ranker scores the top few with a cross-encoder, attaches each note's summary + metadata from the Catalog, and returns capped cards.

The single hard question — scoping the meaning search to the filtered candidates — is answered by the `MATCH + k + IN (...)` SQL form, which I re-proved below.

---

## Spec Verification

Plain-English summary: the load-bearing trio (A1/A2/A3) all hold — A1 and A2 by live execution, A3 by format inspection plus a live date-filter query. **A5 was invalidated in the first pass and is now RESOLVED** by Revision R1's capture-time fix (re-proved below). The six new R1 assumptions (A14–A19) are re-checked in a dedicated sub-table further down: five Validated, one (A15) Invalidated — a single stale test that asserts `title` lives in `extra`, which the fix changes. A15 is mechanical, not a redesign.

| Assumption | Spec Claim (short) | Verdict | Evidence |
|---|---|---|---|
| **A1** | Filtered KNN via `MATCH + k + vault_path IN (...)` returns real distances scoped to the set; no-`MATCH` form returns NULL and must not be used | ✅ Validated | **Executed** against a freshly-migrated DB (sqlite-vec v0.1.9, the installed/pinned version). `MATCH+k+IN` returned real non-NULL distances; the in-set far candidate (dist 5.0) was returned and the out-of-set near candidate (dist 0.05) was excluded — partition-before-k confirmed. No-`MATCH` form returned all-NULL distances. `k=10` over 3 candidates returned 3, no error. Global (no-`IN`) KNN also works (A4). |
| **A2** | Embedding the bare query gives good-enough recall; re-ranker absorbs the doc/query asymmetry | ✅ Validated | **Executed** with real `all-MiniLM-L6-v2` + real `_build_context_text`. Bare query "stakeholder resistance" vs composite matching doc = 0.392, vs distractor = 0.126 → matching ranks first. Wrapping the query raised the absolute score (+0.07) but *collapsed* match-vs-distractor separation (0.266 → 0.133). Bare query is the better choice. Risk: LOW; no mitigation needed. |
| **A3** | `created_at`/`updated_at` are TEXT `YYYY-MM-DD HH:MM:SS` from `datetime('now')`; lexicographic `>=` is chronological if bound matches | ✅ Validated (caveat) | `datetime('now')` → `'2026-06-10 15:58:36'` (space sep, no T, no tz). `updated_at` always written via `datetime('now')` (`documents.py:113,270`). Live date-filter query selected the recent row, excluded the Jan row. **Caveat:** `created_at` uses `COALESCE(str(meta.created), datetime('now'))` and `meta.created` is a `date` → `str()` yields date-only `'2026-06-10'`. So `created_at` is NOT uniformly full-width. The spec's date filter uses `updated_at`, so this is harmless — but the A3 row's "both columns" wording is technically loose. |
| **A4** | No filter → omit `IN` (global); filter matched nothing → `Success([])` without touching indexes | ✅ Validated | Global KNN with no `IN` clause executed and returned globally-nearest rows (probe FORM 4). FTS5 `MATCH` without `IN` is standard. SQLite default variable limit is 32766 (no-filter avoids it). Empty-set short-circuit is caller logic — trivially correct. |
| **A5** | A binary sibling's `documents.title` holds the human title (e.g. "Q3 Budget Report"), not the filename — **resolved by Revision R1 (Component 0)** | ✅ **Resolved** | R1 adds `title: str \| None = None` to `NoteMetadata` + `"title"` to `_KNOWN_KEYS` (`frontmatter.py:27,55`), sets `title=mr.ai_title or None` at the three build sites (`store` `capture.py:889`, sibling_meta `:1176`, marker_meta `:1330`), and changes `_derive_title` (`documents.py:69`) to prefer `metadata.title`. Re-proved end to end: (a) the binary path reaches the `metadata` stage so `mr.ai_title` is non-empty (A16); (b) probe of the proposed `_derive_title` on a sibling outcome carrying `metadata.title="Q3 Budget Report"` returns `'Q3 Budget Report'` (was `'report.pdf'`); (c) both `upsert` and `replace_path` route through `_derive_title` (A17); (d) round-trip + attachment-summary contract intact (A14/A18). The re-ranker reading `documents.title` via `get_by_path` now gets the human title. **Gap-free** — see the A14–A19 sub-table for the one mechanical side-effect (A15, a stale test). |
| **A6** | Stale index row (note deleted out of band) → `get_by_path` returns `Success(None)`; re-ranker skips, no crash | ✅ Validated | `documents.py:162` — `if row is None: return Success(None)`. Never raises on a missing row (only on `sqlite3.Error`). The KNN/BM25 queries operate on the index tables independently, so an orphan index row does not error the search. Skip logic is straightforward. |
| **A7** | `Result` on `retrieval/` is convention (C-12 hook names only `pipelines/`/`handlers/`); Session A already returns `Result` | ✅ Validated | `index_embedding`/`index_keywords` both return `Result`. The settings.json C-12/C-06 hooks key off `*/pipelines/*.py` only — `retrieval/` is unenforced. Convention, not hard rule. |
| **A8** | Intended dataclasses/signatures buildable; no name collision in `retrieval/` | ✅ Validated | `retrieval/` contains only `embeddings.py`, `keyword.py`, empty `__init__.py` — no `search`/`rank`/`rerank`/`SearchResult`/`RankedResult` exist. `DocumentRow` supplies `summary`, `title`, `project`, `note_type`, `updated_at`, `key_topics` for the card. `read_note` confirmed. No collision. |
| **A9** | `notes_fts` cols `vault_path UNINDEXED, title, summary, body`; `body` = index 3; BM25 ascending | ✅ Validated | Migration confirms column order. **Executed**: `snippet(notes_fts, 3, ...)` returned body text; index 1 = title, index 2 = summary. `bm25(notes_fts)` returns a score (lower = better); `MATCH + IN` on the UNINDEXED column works for scoping. |
| **A10** | `CrossEncoder` ships in `sentence-transformers`; loadable as local CPU model with `.predict`; cacheable | ✅ Validated | `from sentence_transformers import CrossEncoder` imports; `CrossEncoder.predict` exists. `sentence-transformers` is in `pyproject.toml`. Offline loadability of the specific model is environment-dependent (see Open Questions). |
| **A11** | Catalog can be filtered by project/date via a new query using `get_connection(readonly=True)`; no such filter exists today | ✅ Validated | `documents.py` has only `get_by_path`/`all_paths`/`delete_by_path`/`replace_path`/`rename`/`update_batch_id` — no project/date filter. **Executed** a `WHERE project = ? AND updated_at >= ?` query through `get_connection(readonly=True)` — works. |
| **A12** | `ProjectRegistry.all_project_names` = frozenset of project names; `get_groups()` keys = domain names (Uncategorized excludable); call sites build the registry | ✅ Validated (nuance) | `all_project_names` is a `@property -> frozenset[str]`. `get_groups()` exists on **`LiveRegistry`**, not `ProjectRegistry` (the latter exposes `.groups`); both dicts key on domain names incl. `Uncategorized`. **Asymmetry:** call site 1 (`capture.py:622`) holds a registry object; call site 2 (`capture.py:2040` via `_build_vault_context`) builds a registry internally and returns only a **string**. The CLI `classify` command (`cli/main.py:96`) is still a stub. See note below. |
| **A13** | `search`/`rank`/`rerank` are sync; CPU work blocks the loop; acceptable for single-shot CLI | ✅ Validated (design intent) | These functions don't exist yet, so this is a design claim, not a code fact. The existing CLI pattern (`capture`/`reconcile`/`watch`) wraps with `asyncio.run`; there is no second concurrent caller in Session B. The reasoning holds; the Phase-4 daemon implication is correctly deferred (OQ-P3B-1). |

---

## Edge Cases & Silent Failure Modes

- **No-`MATCH` KNN returns NULL silently (A1).** If an implementer writes `WHERE vault_path IN (...) ORDER BY distance` (no `MATCH`), the query runs and returns rows — but every `distance` is NULL, so the "ranking" is arbitrary. The probe confirms this is a real, silent trap. The implementer test must assert real (non-NULL) distances AND the partition property (far-in-set returned, near-out-of-set excluded).
- **`created_at` is not full-width (A3 caveat).** Any future code that filters or sorts on `created_at` (not `updated_at`) lexicographically against a full `datetime('now')` bound will mis-compare for date-only rows. Session B is safe because it uses `updated_at`.
- **Sibling card title is the filename (A5).** Without a fix, a search hit on a PDF/image sibling shows `report.pdf` (or AI-title-plus-extension after FULL_RENAME), not a clean human title. The card is still usable, but P3-SRCH-05's literal promise ("a usable title, not `report.pdf.md`") is only half-met.
- **Variable ceiling on huge `IN` lists (A4).** The all-paths case must omit the `IN` clause; building a 30k+ element list would hit SQLite's 32766 default parameter limit.

---

## Dependencies & Coupling

What Session B depends on and what depends on it.

- **sqlite-vec capability bet.** The meaning search depends on `vec0` honoring `MATCH + k + IN (...)`. Verified on v0.1.9. **Risk the spec under-states:** `pyproject.toml` pins `sqlite-vec>=0.1.9` — a floor, **not a hard pin**. ADR-0009 and the spec both say to "pin" it. A `>=` floor allows a future `uv sync` to pull a newer version that could change filtered-KNN semantics. The implementer should add the regression test (asserting the partition property) AND consider tightening the pin to `==0.1.9` (a dependency decision for the human).
- **Capture-time best-effort indexing vs `--reindex`.** The capture pipeline already calls `index_embedding` + `index_keywords` at 4 sites (`capture.py:997,1047,1243,1378`), each as `mr.ai_title or stem`. `--reindex` re-runs the same idempotent (DELETE-then-INSERT) writers via `all_paths()` + `read_note()`. Coupling to know: **`--reindex` reads the note from disk via `read_note`, but the capture-time indexers are fed `mr.ai_title` and `mr.ai_tags`/`mr.ai_type` from the live pipeline.** A reindex loop that reconstructs title/type/tags from `read_note` + `documents` must mirror what capture passed, or the re-indexed embedding context string will differ from the original. The plan must specify exactly what `--reindex` passes to the indexers.
- **`replace_path` asymmetry (CLAUDE.md gotcha, confirmed).** `replace_path` cleans old search-table rows but does NOT create new ones; `rename` copies both tables old→new. Search is read-only and does not touch this, but `--reindex` relies on the capture indexers being the only creators.
- **CLI `search` stub signature collision.** `cli/main.py:110` declares `@click.argument("query")` — required positional. The spec needs `query` optional (filter-only mode) plus `--project`/`--since`/`--max`/`--reindex`. The command body must be rewritten, not patched. The `classify` command (line 96) remains a `NotImplementedError` stub — out of scope here but adjacent.
- **TD-051 call-site asymmetry (A12).** Two `classify()` call sites differ: `capture.py:622` has a registry object in scope (can pass two sets cheaply); `_build_vault_context` (`capture.py:2040`) returns only the formatted **string** and rebuilds the registry internally. To pass two sets to `classify()`, the second path must be refactored to surface the registry (or two sets) instead of a string — a slightly larger touch than the spec's "the call sites already build the registry, so they pass the two sets" implies. Still small, still cuttable.

---

## Extension Points

- **`search()` is the real seam** (CLI now, MCP tool Phase 4) — both adapters consume `Result[list[SearchResult]]`. `ranker.py`/`reranker.py` are single-caller depth boundaries, justified as readability, not speculative seams (design Module-depth check holds).
- **RRF constant `60`** lives in `retrieval/`, outside the C-06 hook's `pipelines/` scope (verified) — safe as a named module constant or config key without tripping enforcement.
- **Candidate Filter placement (OQ-P3B-3)** — a `documents.filter_paths(project, since, db_path)` helper is reusable by Phase 8/9 Synthesis and keeps SQL in the data layer; verified that no such function exists yet, so either placement is greenfield.

---

## Open Questions

- **CrossEncoder offline loadability (A10).** `CrossEncoder` imports and the class API is confirmed, but whether `cross-encoder/ms-marco-MiniLM-L-6-v2` loads without a network call depends on the local Hugging Face cache. I confirmed the import and `.predict` attribute; I did not download the reranker weights. The implementer should confirm the model is cached (or pre-fetch it) before relying on offline reranking. This cannot be settled from code alone.
- **`--reindex` title/type/tags reconstruction.** What exactly `--reindex` passes to `index_embedding`/`index_keywords` (reconstructed from `read_note` frontmatter vs `documents` columns) needs a plan-time decision so re-indexed context strings match capture-time ones. Evidence examined: the 4 capture call sites and the two index-writer signatures.

---

## Technical Debt Spotted

- **`sqlite-vec` is floor-pinned, not hard-pinned** (`pyproject.toml:32` → `>=0.1.9`). The whole meaning search rests on a v0.1.9 KNN-filter behavior. Worth a hard pin + the regression test ADR-0009 already calls for.
- ~~**A5 sibling-title gap is a pre-existing capture defect**~~ **resolved: Revision R1 (Component 0)** fixes this at the source in capture — adds a first-class `title` field to `NoteMetadata`, stamps `mr.ai_title` at all three build sites, and teaches `_derive_title` to prefer it. The sibling `documents.title` had been the filename since the attachment pipeline shipped; R1 makes it the AI descriptive title for every future capture (all consumers benefit, not just search). Confirmed gap-free in the 2026-06-10 re-check below.
- **Backfill remains tech debt (R1 Decision 5, confirmed by code):** `--reindex` re-runs only the index writers from existing frontmatter (`index_embedding`/`index_keywords`) — it does **not** re-run the `summarize → metadata` LLM stage, so it cannot regenerate an AI title for a pre-R1 sibling whose frontmatter never had one. Backfilling pre-R1 siblings requires full re-capture. Logged by the design step; out of scope for R1.

---

## Invalidated Assumptions

**A5 (the original invalidation) is now RESOLVED by Revision R1** and has been removed from this section. The R1 fix introduced six new assumptions (A14–A19); re-checking them surfaced **one new invalidation — A15** — which is a single stale test, not a design flaw. Per the research skill, this mechanical invalidation does not require a Q4 conflict diagram, so none is drawn. The planner must schedule the one-test fix before A15 clears.

### A15 — One existing frontmatter test asserts `title` lives in `extra`

**Spec claimed (A15 row + Component 0):** Adding `title: str | None = None` to `NoteMetadata` and `"title"` to `_KNOWN_KEYS` does not break existing `NoteMetadata`/frontmatter tests — "no test asserts a fixed field count or an exact-dict equality on the model or on serialized frontmatter."

**Code shows:** `tests/test_vault/test_frontmatter.py::test_parse_minimal_note` (lines 28–37) parses a note whose only frontmatter key is `title: T` and asserts **`meta.extra == {"title": "T"}`** — i.e. it relies on `title` being an *unknown* key that lands in `extra`. The test's own docstring says so: *"Unknown key 'title' goes into extra."* The moment `"title"` is added to `_KNOWN_KEYS` (`frontmatter.py:27`), `parse()` routes that key onto the new typed field, so `meta.extra` becomes `{}` and `meta.title == "T"`. I confirmed this with an in-process probe of the proposed patched model: the populated `title` round-trips onto the field and `extra == {}`, which is exactly what makes the old assertion fail.

**Why this matters:** It is not a runtime defect and not a redesign — the R1 mechanism is correct (A5 resolves). But the spec's A15 claim is factually wrong as written: there *is* an exact-dict frontmatter assertion on the `title` key, and it will turn red the instant R1 ships. The planner must include the one-line test update in the Component 0 build step, or CI breaks.

**Suggested resolution directions (not a decision):**
1. **Update the stale test (smallest, expected):** change `test_parse_minimal_note` to assert the new contract — `meta.title == "T"` and `meta.extra == {}` — and update its docstring ("`title` now routes to the typed field"). This is the intended consequence of the design; the test was encoding the old behavior. Bundle it into the Component 0 build step.
2. **Add a dedicated round-trip test alongside:** keep a positive test that a populated `title` survives `dumps()`→`parse()` and that a `None` title is omitted from the YAML — locks the A14 contract the fix depends on.

_No other test breaks were found:_ the many `"title": ...` hits across `tests/test_pipelines/` are LLM-response mock JSON (the title the AI returns), unaffected by the model change; the `title="report.pdf"` lines in `test_watcher_rehome.py`/`test_watcher_settle.py` construct a `DocumentRow` directly (its `title` field already exists) and are unaffected.

---
## Update — 2026-06-10 (Re-check pass: A5 resolution + Component 0 assumptions)

**Re-check mode.** The prior pass invalidated A5 (a binary sibling's catalog title was the filename, not a human title). The team applied a root-cause fix at capture — design "Revision R1" / spec "Component 0 — Descriptive Title at Capture." This pass re-verifies A5 against the *actual* code (not the spec's "RESOLVED-BY-R1" label) and verifies the six new assumptions the fix introduced (A14–A19). Everything below was read at file:line and, where behavior was at stake, confirmed with in-process probes.

### Resolved

| ID | Was (first pass) | Now | Evidence |
|----|------------------|-----|----------|
| **A5** | A binary sibling's `documents.title` is `Path("report.pdf.md").stem` = `report.pdf`, never the human title — re-ranker card shows the filename. | ✅ **Resolved by R1** | The proposed chain is correct end to end: `title` field added to `NoteMetadata` + `_KNOWN_KEYS` (`frontmatter.py:27,55`); `title=mr.ai_title or None` at the three build sites (`store` `capture.py:889`, `sibling_meta` `:1176`, `marker_meta` `:1330`); `_derive_title` (`documents.py:69`) preferring `metadata.title`. Probe of the proposed `_derive_title` on a sibling outcome carrying `metadata.title="Q3 Budget Report"` returns `'Q3 Budget Report'` (current code returns `'report.pdf'`). Both `upsert` (`documents.py:101`) and `replace_path` (`:249`) call `_derive_title`, so both write paths carry the human title. The re-ranker (not yet built) reads `documents.title` via `get_by_path` — that value is now correct. |

### New invalidated assumption

| ID | Spec claimed | Code shows | Verdict |
|----|--------------|-----------|---------|
| **A15** | No existing `NoteMetadata`/frontmatter test asserts an exact-dict on serialized frontmatter; adding the field is test-safe. | `test_frontmatter.py::test_parse_minimal_note` (lines 28–37) asserts `meta.extra == {"title": "T"}` — it relies on `title` being an *unknown* key. Adding `"title"` to `_KNOWN_KEYS` reroutes it onto the field, so `extra == {}` and the assertion fails. Confirmed by in-process probe of the patched model. | ❌ **Invalidated (mechanical)** — one stale test; fix is a one-line assertion update bundled into Component 0. Full entry in `## Invalidated Assumptions` above. |

### Component 0 (R1) assumptions — full re-check sub-table

Plain-English: these six assumptions back the A5 fix. Five are confirmed true against the code; one (A15) is the stale test above. The two load-bearing ones (A16, A18) both hold.

| ID | Assumption (short) | Verdict | Evidence |
|----|--------------------|---------|----------|
| A14 | Adding `title` to `_KNOWN_KEYS` + a `title: str \| None` Field round-trips cleanly; `dumps()` writes it when set, omits when `None`; no `_DEPRECATED_KEYS` interaction. | ✅ Validated | `_KNOWN_KEYS` (`frontmatter.py:27`) is a flat membership set; `parse()` (line 134) routes any member onto the model; `dumps()` (line 160) serialises via `model_dump(exclude_none=True, exclude={"extra"})` then strips only `_DEPRECATED_KEYS` (line 52 = `{"domain"}`). **In-process probe** of the patched model: populated `title` round-trips onto the field (`extra == {}`); `None` title produces no `title:` line; a note with `title="Keep Me"` + `extra={"domain":...}` keeps `title:` and strips `domain`. No deprecated-key interaction. |
| A15 | No existing frontmatter/`NoteMetadata` test breaks (no field-count / exact-dict assertion). | ❌ **Invalidated** | `test_parse_minimal_note` asserts `meta.extra == {"title": "T"}` and breaks when `title` becomes a known key. Sole breakage; mechanical one-test fix. (No field-count assertion exists; the break is the exact-dict on `extra`.) |
| **A16 (LOAD-BEARING)** | The binary capture path reaches the `metadata` stage so `mr.ai_title` is non-empty for binaries (and the CLUELESS-marker path). | ✅ Validated | `capture_file` runs ONE uniform stage list — `extract → enrich_urls → summarize → metadata → apply_location_tags → classify_step → store` (`capture.py:1569–1582`) — for ALL files; the only `is_md` branch is *inside* `store()` (line 901), which runs AFTER `metadata` already set `mr.ai_title`. `metadata` sets `ai_title=parsed["title"]` (`:309`), and `_parse_metadata_json` (`:117`) guarantees non-empty: empty/missing/invalid title falls back to the source stem (`:138,143,144`). `extract_metadata.yaml` makes `title` the first required field and instructs the model to "still return valid JSON with your best-effort title … never refuse." So both LOCATED-sibling and CLUELESS-marker paths receive a real `mr.ai_title`. **Keystone holds.** Nuance: when the LLM gives an empty title, `mr.ai_title` is the filename *stem* (e.g. `report`, no extension) — still better than `report.pdf`, and normally the AI returns a true title. |
| A17 | `_derive_title` preferring `metadata.title` carries the value into the existing `documents.title` column with no migration, on BOTH `upsert` and `replace_path`. | ✅ Validated | `documents.title` column already exists (`DocumentRow.title` `:32`; INSERT lists `title` at `:110` and `:267`). BOTH `upsert` (`:101`) and `replace_path` (`:249`) compute `title = _derive_title(outcome)` and bind it into the INSERT. No DDL needed. (Note the CLAUDE.md gotcha: `replace_path` *creates a fresh row* — confirmed it derives its own title via `_derive_title`, so it is covered, not assumed.) |
| **A18 (LOAD-BEARING)** | Adding `title=` to `sibling_meta`/`marker_meta` does not disturb `type: attachment-summary`; reconcile Stage 4 still recognises these siblings (ADR-0008). | ✅ Validated | Both builders set `type="attachment-summary"` (`capture.py:1177`, `:1331`) as a separate keyword; adding `title=` alongside it leaves `type` untouched. Reconcile Stage 4 recognition keys purely on `note.metadata.type != "attachment-summary"` (`reconcile.py:266`, and Stage-7 at `:570`) — independent of `title`. On round-trip, `type:` still parses back as before and the new `title:` lands on `NoteMetadata.title`. Contract intact. |
| A19 | The four capture index call sites already pass `mr.ai_title` to `index_embedding`; R1 must not alter them — no double-write. | ✅ Validated | The four sites (`capture.py:997, 1047, 1243, 1378`) each compute `_title = mr.ai_title or Path(...).stem` and pass it to `index_embedding`/`index_keywords`, which feeds `_build_context_text` (`embeddings.py:24`, prefixes `title: {title}`). These sites read `mr.ai_title` directly — NOT `note_meta`/`sibling_meta`/`marker_meta` or `_derive_title`. R1 touches only the frontmatter field and `_derive_title` (the catalog side); the index sites are untouched, so no double-write and no behavior change on the embedding side. |

### Counts and gate

- **A5: ✅ Resolved.**
- **A14–A19: 5 Validated (A14, A16, A17, A18, A19), 1 Invalidated (A15), 0 Unverifiable.**
- **A16 and A18 (the load-bearing R1 rows): both ✅ Validated.**

**Invalidation set is NOT empty:** A15 remains. But A15 is a *mechanical* stale-test fix (one assertion + docstring), not a design flaw — the R1 mechanism is sound and A5 is genuinely resolved. The planner should fold the `test_parse_minimal_note` update into the Component 0 build step; with that bundled, the loop converges. No new design session is required for A15.
