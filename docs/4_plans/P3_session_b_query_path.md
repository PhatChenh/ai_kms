# Plan: Phase 3 Session B -- Query Path (Hybrid Search)

_Last updated: 2026-06-10_
_Status: [~] in progress_

**Spec:** `docs/2_specs/P3_session_b_query_path.md`
**Research:** `docs/3_research/P3_session_b_query_path.md`
**Design:** `docs/1_design/P3_session_b_query_path.md`
**ADR:** `docs/architecture/system_adr/0009-phase3-search-rrf-rerank-not-tier-dispatcher.md`
**Behavior IDs:** P3-SRCH-01..P3-SRCH-10

---

## Architecture

### Q1 -- What happens inside (from spec)

See spec Section "Q1 Diagram" -- shows the filter-then-branch-then-rank-then-rerank flow and the Hybrid Ranker expansion (word + meaning + rank fusion).

### Q2 -- How it connects (from spec)

See spec Section "Q2 Diagram" -- shows the search feature cluster connected to the Note Catalog, Word Index, Meaning Index, Full-Note Reader, and future AI Assistant.

### Q3 -- Why build it this way

```
# Hybrid Search -- Why Build It This Way
Scope: Rules, constraints, and prior decisions that shaped this design.
       Uses the same component names as Q1 and Q2.

How to read this:
  Center cluster  = the search feature (same as Q2)
  Surrounding boxes = rules and decisions it must respect
  Lines            = which rule constrains which part
  [ADR-0009]       = recorded architecture decision

  +----------------------------+      +----------------------------+
  | ADR-0009: No tier          |      | Session A built the        |
  | dispatcher, no cost        |      | index tables + writers     |
  | budget, no hot/warm/cold.  |      | -- Session B is READ-ONLY  |
  | Replaced by cheap cards    |      | against them. No new       |
  | + lazy full-note fetch.    |      | tables, no new schema.     |
  +-------------+--------------+      +-----------+----------------+
                |                                 |
                |    applies to                   |  constrains
                |    the whole feature            |  Hybrid Ranker
                |                                 |  + Re-ranker
                v                                 v
  +============================================================+
  ||                    SEARCH FEATURE                          ||
  ||                                                           ||
  ||  Search         Candidate        Hybrid         Re-ranker ||
  ||  Coordinator    Filter           Ranker                   ||
  ||  (THE seam:     (reads Note      (Word Index    (Second-  ||
  ||  CLI now,       Catalog only)    + Meaning      Opinion   ||
  ||  MCP later)                      Index,         Model:    ||
  ||                                  scoped)        ships free||
  ||                                                 inside    ||
  ||                                                 existing  ||
  ||                                                 library)  ||
  ||====+===========+=================+============+==========||
       |           |                  |            |
  +----+-----------+----+   +---------+-------+  +--+------------------------+
  | All database        |   | In-database     |  | Cheap card, not full     |
  | access goes         |   | filtered KNN    |  | note -- the AI reads     |
  | through the         |   | proven on the   |  | cards, decides which     |
  | shared connection   |   | installed       |  | notes matter, then       |
  | factory (rule       |   | library: the    |  | opens only those via     |
  | C-04). No raw       |   | candidate set   |  | Full-Note Reader.        |
  | database calls.     |   | acts as a wall  |  | Search never reads       |
  +---------+-----------+   | BEFORE picking  |  | note bodies.             |
                            | nearest -- this |  +--+------------------------+
                            | drives the      |
  +---------------------+   | whole meaning   |  +--+------------------------+
  | Descriptive Title   |   | search design.  |  | No audit entries         |
  | at Capture (fix     |   +-------+---------+  | needed -- search makes   |
  | ships with this     |                         | no AI decision (no       |
  | phase): the Note    |                         | confidence gate, no      |
  | Catalog's title     |                         | routing). Read-only      |
  | will be the AI's    |                         | retrieval. Rule C-13     |
  | descriptive name,   |                         | does not apply.          |
  | not the filename.   |                         |                          |
  | Makes search        |                         |                          |
  | cards usable for    |                         |                          |
  | binary siblings.    |                         |                          |
  +---------------------+                         +--------------------------+

  +---------------------+                         +--------------------------+
  | CLI wraps search    |                         | Classify validation      |
  | in the standard     |                         | fix (TD-051) ships       |
  | async wrapper       |                         | alongside but is         |
  | (rule C-10).        |                         | fully isolated -- no     |
  | Sync CPU work       |                         | dependency on search.    |
  | runs inside; fine   |                         | Stops the classifier     |
  | for one-shot CLI.   |                         | accepting a domain as    |
  | Thread offload      |                         | a project (or vice       |
  | deferred to the     |                         | versa).                  |
  | Phase 4 daemon.     |                         |                          |
  +---------------------+                         +--------------------------+
```

---

## Approach

Build bottom-up following the spec's dependency order: fix the title data quality first (Component 0), then add the candidate filter, then the two ranking stages, then the coordinator that wires them together, then the CLI that exercises the coordinator. TD-051 ships last as a fully isolated cleanup. Every phase is TDD: write failing tests first, then implement until green. Every public function returns `Result`. No new DB schema -- Session B is read-only against the Session A tables.

---

## Resolved decisions

These open questions from the spec/research are resolved here. Each was "leaning" in the spec; this plan locks them.

| Decision | Resolution | Rationale |
|----------|-----------|-----------|
| `date_range` concrete type | `tuple[datetime, datetime] or None` -- CLI converts `--since 7d` to `(lower_bound, None)` as `tuple[datetime, None]`; the Candidate Filter formats to `YYYY-MM-DD HH:MM:SS` string for SQL. | Matches A3; simple; no new dataclass for a two-element pair. `None` upper bound = open-ended. |
| RRF `60` -- config key vs named constant | Named module constant `RRF_K = 60` in `retrieval/ranker.py`. | It is a standard RRF parameter, not a tunable routing threshold. C-06 hook targets `pipelines/`, not `retrieval/` (confirmed by research A7). |
| `metadata` field set on `SearchResult` | `dict` with keys: `title`, `project`, `note_type`, `updated_at`, `key_topics`, `tags` (the full tag list from the catalog row's key_topics). | This is the AI triage payload. `title` is load-bearing for display. |
| `score` field meaning | Cross-encoder score (the final relevance score from the re-ranker). RRF score is used only for intermediate ordering and is discarded. In filter-only mode (no query), `score` is `0.0` (no ranking happened). | The score consumers (MCP AI, CLI display) care about final relevance, not intermediate fusion. |
| `--reindex` mode | Standalone only: `kms search --reindex` rebuilds indexes, prints a count, and exits. Does not accept a query or `--project` in the same invocation. | Simplest; the reindex loop is maintenance, not search. |
| `--reindex` title/type/tags reconstruction | Read from `read_note` frontmatter: `note.metadata.title or Path(vp).stem` for title, `note.metadata.type` for type, `note.metadata.tags` for tags, `note.metadata.summary` for summary, `note.content` for body. This mirrors what capture passes to the indexers. | The indexers (`index_embedding`, `index_keywords`) need the same inputs they received at capture time. Frontmatter is the single source of truth for what was stored. |
| Candidate Filter placement | Helper function `filter_paths(project, since, until, db_path)` in `storage/documents.py`. | Every other documents query lives there. Phase 8/9 Synthesis can reuse it. Keeps SQL in the data layer. |
| Component 0 field name | `title` (not `display_title` or `ai_title`). | The human-obvious key. No collision with existing frontmatter semantics (research confirmed `title` was in `extra`, now rerouted to the typed field). |

---

## Phases

### Phase 1 -- Descriptive Title at Capture (Component 0)

**Goal:** Give every captured note a first-class `title` field in frontmatter so the Note Catalog (and every search card) shows a real descriptive title instead of a raw filename.

**Depends on:** Nothing -- fully independent of the search components.

**Spec reference:** Component 0 (Descriptive Title at Capture). Covers behavior P3-SRCH-05, P3-SRCH-10. Resolves A5/A15.

**Steps:**

1. **Write tests first (RED):**
   - **Update `test_parse_minimal_note`** (`tests/test_vault/test_frontmatter.py:28-37`): Change the assertion from `meta.extra == {"title": "T"}` to `meta.title == "T"` and `meta.extra == {}`. Update the docstring to say `"title routes to typed field"`. This is the A15 mechanical fix.
   - **New test `test_title_field_roundtrip`** in the same file: Build a `NoteMetadata(title="Q3 Budget Report", type="meeting-notes")`, call `dumps(meta, "body")`, write to a temp file, call `parse()`, assert `meta.title == "Q3 Budget Report"` and `meta.extra == {}`.
   - **New test `test_title_none_omitted_from_yaml`**: Build `NoteMetadata(title=None, type="meeting-notes")`, call `dumps()`, assert the output string does not contain `title:`.
   - **New test `test_derive_title_prefers_metadata_title`** in `tests/test_storage/test_documents.py`: Build a mock `WriteOutcome` with `metadata.title = "Q3 Budget Report"` and `vault_path = "Projects/Alpha/report.pdf.md"`. Call `_derive_title(outcome)`. Assert the result is `"Q3 Budget Report"`, not `"report.pdf"`.
   - **New test `test_derive_title_falls_back_to_extra_then_stem`**: Same file. Two cases: (a) `metadata.title = None`, `extra = {"title": "From Extra"}` -> returns `"From Extra"`; (b) `metadata.title = None`, `extra = {}`, `vault_path` stem -> returns the stem.

2. **Implement (GREEN):**
   - **`src/vault/frontmatter.py`:**
     - Add `"title"` to the `_KNOWN_KEYS` frozenset (line 27).
     - Add `title: str | None = None` to `NoteMetadata` (after line 60, alongside the other typed fields). This is a `Field`-style default, consistent with the existing pattern.
   - **`src/storage/documents.py`:**
     - Change `_derive_title` (line 69-70) from `outcome.metadata.extra.get("title") or Path(outcome.vault_path).stem` to `outcome.metadata.title or outcome.metadata.extra.get("title") or Path(outcome.vault_path).stem`.
   - **`src/pipelines/capture.py`:**
     - At the `store()` build site (line 889), add `title=mr.ai_title or None` to the `NoteMetadata(...)` constructor.
     - At the `sibling_meta` build site (line 1176), add `title=mr.ai_title or None`.
     - At the `marker_meta` build site (line 1330), add `title=mr.ai_title or None`.

3. **Run tests:** `uv run pytest tests/test_vault/test_frontmatter.py tests/test_storage/test_documents.py -x`

4. **Verify no regressions:** `uv run pytest tests/ -x`

**Files to modify:**
- `src/vault/frontmatter.py` -- add `"title"` to `_KNOWN_KEYS`; add `title: str | None = None` to `NoteMetadata`
- `src/storage/documents.py` -- change `_derive_title` to prefer `metadata.title`
- `src/pipelines/capture.py` -- add `title=mr.ai_title or None` at three build sites
- `tests/test_vault/test_frontmatter.py` -- update `test_parse_minimal_note`; add round-trip tests
- `tests/test_storage/test_documents.py` -- add `_derive_title` preference tests

**Test criteria:**
- [ ] `test_parse_minimal_note` passes with `meta.title == "T"` and `meta.extra == {}`
- [ ] A populated title round-trips through `dumps()` -> `parse()` unchanged
- [ ] A `None` title produces no `title:` line in YAML output
- [ ] `_derive_title` returns the metadata title when set, falls back to extra, then to stem
- [ ] Existing frontmatter tests pass (no regressions)
- [ ] Existing capture tests pass (no regressions from the three build-site edits)
- [x] The four embedding index call sites (`capture.py:997, 1047, 1243, 1378`) are NOT touched

**Status:** [x] done

**Completed**: 2026-06-10

---

### Phase 2 -- Candidate Filter (Component 1)

**Goal:** Turn the optional project and/or date window into the set of note handles eligible for ranking, or signal "all notes" when nothing is given.

**Depends on:** Nothing (reads existing `documents` table). Can run in parallel with Phase 1.

**Spec reference:** Component 1 (Candidate Filter). Covers behavior P3-SRCH-02, P3-SRCH-03, P3-SRCH-08.

**Steps:**

1. **Write tests first (RED)** in a new file `tests/test_storage/test_documents_filter.py`:
   - **`test_filter_by_project_returns_only_matching`**: Seed a temp DB with 3 rows (2 in project "Alpha", 1 in project "Beta"). Call `filter_paths(project="Alpha", db_path=db)`. Assert `Success` with exactly 2 vault_paths, all belonging to Alpha.
   - **`test_filter_by_date_returns_recent_only`**: Seed 2 rows: one with `updated_at = "2026-06-01 00:00:00"`, one with `updated_at = "2026-01-01 00:00:00"`. Call `filter_paths(since=datetime(2026, 5, 1))`. Assert only the June row is returned.
   - **`test_filter_by_project_and_date_combined`**: Seed 3 rows across 2 projects and 2 dates. Filter by both project and since. Assert correct intersection.
   - **`test_filter_no_args_returns_none_sentinel`**: Call `filter_paths()` with no project and no since. Assert `Success(None)` -- the `None` value signals "all notes; do not build an IN clause."
   - **`test_filter_no_matches_returns_empty_list`**: Filter with a project that has no rows. Assert `Success([])`.
   - **`test_filter_with_until_upper_bound`**: Seed rows, filter with both `since` and `until`. Assert rows in the window only.
   - All tests use `init_db(tmp_db_path)` then `get_connection(tmp_db_path)` to seed rows directly with SQL INSERTs (no WriteOutcome needed -- we are testing the query, not the upsert path).

2. **Implement (GREEN)** in `src/storage/documents.py`:
   - Add a new function `filter_paths(project=None, since=None, until=None, db_path=None) -> Result[list[str] | None]`:
     - If both `project` and `since` are `None`, return `Success(None)` (sentinel for "all notes").
     - Build a `WHERE` clause dynamically from the non-None arguments.
     - `project` -> `WHERE project = ?`
     - `since: datetime` -> `WHERE updated_at >= ?` with `since.strftime("%Y-%m-%d %H:%M:%S")`
     - `until: datetime` -> `AND updated_at <= ?` with same format
     - Use `get_connection(db_path, readonly=True)`.
     - Return `Success(list_of_vault_paths)` or `Failure` on `sqlite3.Error`.
   - Type hints: `since: datetime | None = None`, `until: datetime | None = None`.
   - Import `datetime` at the top of the file.

3. **Run tests:** `uv run pytest tests/test_storage/test_documents_filter.py -x`

**Files to modify:**
- `src/storage/documents.py` -- add `filter_paths()` function
- `tests/test_storage/test_documents_filter.py` -- new test file

**Test criteria:**
- [ ] Project-only filter returns exactly that project's notes
- [ ] Date-only filter returns only notes with `updated_at >= since`
- [ ] Combined filter returns the intersection
- [ ] No-args returns `Success(None)` (all-notes sentinel)
- [ ] No-match returns `Success([])` (empty list, not None)
- [x] Upper bound (`until`) is respected when given

**Status:** [x] done

**Completed**: 2026-06-10

---

### Phase 3 -- Hybrid Ranker (Component 2)

**Goal:** Given a question and a candidate set, find the best-matching notes by combining a word search (BM25) and a meaning search (KNN) into one blended ranking using Reciprocal Rank Fusion.

**Depends on:** Phase 2 (consumes candidate `vault_path`s). Also depends on Session A's Word Index and Meaning Index tables and the cached embed model.

**Spec reference:** Component 2 (Hybrid Ranker). Covers behavior P3-SRCH-01. Assumes A1, A2, A4, A8, A9.

**Steps:**

1. **Define the data types** in a new file `src/retrieval/ranker.py`:
   - `RankedResult` dataclass (frozen): `vault_path: str`, `rrf_score: float`, `snippet: str`.
   - Module constant `RRF_K = 60` (the standard RRF parameter).

2. **Write tests first (RED)** in a new file `tests/test_retrieval/test_ranker.py`:
   - **Setup helper**: A `_seed_db` function that creates a temp DB via `init_db`, inserts 3 notes into `documents`, indexes them via the real `index_embedding()` and `index_keywords()` functions. Notes should have distinct content:
     - Note A: about "stakeholder resistance and managing pushback"
     - Note B: about "quarterly budget analysis Q3"
     - Note C: about "vacation policy update"
   - **`test_rank_returns_semantically_relevant_note_first`**: Call `rank("stakeholder resistance", candidate_paths=[A, B, C], max_candidates=3, db_path=db)`. Assert `Success`, Note A is in the results, and Note A's `rrf_score` >= Note C's `rrf_score`.
   - **`test_rank_scoped_to_candidates_only`**: Index notes A, B, C. Call `rank("budget", candidate_paths=[A, C], ...)`. Assert Note B (budget note) is NOT in results because it was excluded from candidates, even though it matches the query best.
   - **`test_rank_global_mode_when_candidates_none`**: Call `rank("budget", candidate_paths=None, ...)`. Assert Note B IS in results (global search, no IN clause).
   - **`test_rank_empty_candidates_returns_empty`**: Call `rank("budget", candidate_paths=[], ...)`. Assert `Success([])`.
   - **`test_rank_snippet_comes_from_body`**: Assert the returned `snippet` field contains text from the note body (not the title or summary).
   - **`test_rank_returns_result_type`**: Assert the function returns `Result`, not raises.
   - **`test_rank_rrf_score_is_positive`**: Assert all returned `rrf_score` values are > 0.
   - **Filtered KNN regression test `test_knn_partition_property`**: Seed two notes: one far-but-in-set (distractor content), one near-but-out-of-set (matching content). Query with only the far note in candidates. Assert the far-but-in-set note IS returned and the near-but-out-of-set is excluded. This locks the A1 partition property.
   - Note: These tests load real `SentenceTransformer` -- mark them with a custom marker if needed, or accept the load time (~2-3s on first call, cached after). The model is already a project dependency.

3. **Implement (GREEN)** in `src/retrieval/ranker.py`:
   - `rank(query: str, candidate_paths: list[str] | None, max_candidates: int, db_path: Path | None = None) -> Result[list[RankedResult]]`:
     - If `candidate_paths` is an empty list, return `Success([])`.
     - **Word search**: Build FTS5 query `SELECT vault_path, bm25(notes_fts) as score, snippet(notes_fts, 3, '<mark>', '</mark>', '...', 40) as snip FROM notes_fts WHERE notes_fts MATCH ?`. If `candidate_paths` is not `None`, add `AND vault_path IN (...)` with parameter placeholders. Order by `bm25(notes_fts)` ASC (lower = more relevant). Limit to `max_candidates`.
     - **Meaning search**: Embed the query via `_get_model().encode(query)` (bare query, not wrapped -- A2 validated). Build KNN query `SELECT vault_path, distance FROM embeddings_vec WHERE embedding MATCH ? AND k = ?`. If `candidate_paths` is not `None`, add `AND vault_path IN (...)`. The `MATCH + k + IN` form is the validated A1 pattern.
     - **Fusion**: Assign rank positions (1-based) from each list. For notes appearing in only one list, assign `max_candidates + 1` as the missing rank. Compute `rrf_score = 1/(RRF_K + word_rank) + 1/(RRF_K + meaning_rank)`. Sort descending by `rrf_score`. Take top `max_candidates`.
     - Build `RankedResult` for each, using the snippet from the word search (or empty string if the note appeared only in meaning search).
     - Wrap in try/except, return `Failure` on errors.
     - Use `get_connection(db_path, readonly=True)` for all queries (C-04).
     - Import `_get_model` from `retrieval.embeddings` (lazy, C-17 compliant).

4. **Run tests:** `uv run pytest tests/test_retrieval/test_ranker.py -x`

**Files to modify:**
- `src/retrieval/ranker.py` -- new file: `RankedResult`, `RRF_K`, `rank()`
- `tests/test_retrieval/test_ranker.py` -- new test file

**Test criteria:**
- [ ] Semantically relevant note ranks above an irrelevant one (P3-SRCH-01)
- [ ] Candidates-only scoping works (in-set returned, out-of-set excluded)
- [ ] Global mode (candidates=None) searches everything
- [ ] Empty candidates returns `Success([])`
- [ ] Snippet comes from the body column (index 3)
- [ ] RRF scores are positive floats
- [ ] Filtered KNN partition property holds (A1 regression test)
- [x] Function returns `Result`, never raises

**Status:** [x] done

**Completed**: 2026-06-10

---

### Phase 4 -- Re-ranker (Component 3)

**Goal:** Take the blended top candidates from the Hybrid Ranker, have a small local cross-encoder re-score them against the exact question, and build the final cheap result cards with summary and metadata.

**Depends on:** Phase 3 (consumes `RankedResult`s). Also depends on the cross-encoder model (ships inside `sentence-transformers`).

**Spec reference:** Component 3 (Re-ranker). Covers behaviors P3-SRCH-04, P3-SRCH-05, P3-SRCH-06.

**Steps:**

1. **Define the data types** in a new file `src/retrieval/reranker.py`:
   - `SearchResult` dataclass (frozen): `vault_path: str`, `summary: str | None`, `snippet: str`, `score: float`, `metadata: dict`.

2. **Write tests first (RED)** in a new file `tests/test_retrieval/test_reranker.py`:
   - **Setup helper**: Seed a temp DB with documents rows (including `title`, `summary`, `project`, `note_type`, `updated_at`, `key_topics`).
   - **`test_rerank_returns_search_results_with_metadata`**: Build `RankedResult`s, call `rerank("budget", candidates, db_path=db)`. Assert `Success(list[SearchResult])`, each result has `summary`, `snippet`, `score`, and `metadata` with keys `title`, `project`, `note_type`, `updated_at`, `key_topics`, `tags`.
   - **`test_rerank_score_is_cross_encoder_score`**: Assert the `score` field is a float (the cross-encoder output). Assert results are ordered by score descending.
   - **`test_rerank_skips_stale_row`**: Build a `RankedResult` with a `vault_path` that has no `documents` row. Assert the result list omits that path without crashing (P3-SRCH-06).
   - **`test_rerank_card_has_no_body`**: Assert none of the `SearchResult` fields contain the full note body (cards are cheap).
   - **`test_rerank_title_is_descriptive`**: Seed a documents row with `title="Q3 Budget Report"`. Assert the card's `metadata["title"]` is `"Q3 Budget Report"`, not `"report.pdf"` (P3-SRCH-05).
   - **`test_rerank_returns_result_type`**: Assert return is `Result`.
   - **`test_rerank_empty_candidates_returns_empty`**: Call with empty list, assert `Success([])`.
   - Note: These tests load the real `CrossEncoder`. To keep test speed manageable, use a small candidate list (2-3 items). The cross-encoder is CPU-only and fast on small inputs.

3. **Implement (GREEN)** in `src/retrieval/reranker.py`:
   - Module-level cached cross-encoder loader (same pattern as `_get_model` in `embeddings.py`):
     ```
     _reranker = None
     def _get_reranker():
         global _reranker
         if _reranker is None:
             from core.config import CONFIG
             from sentence_transformers import CrossEncoder
             _reranker = CrossEncoder(CONFIG.main.search.reranker_model)
         return _reranker
     ```
   - `rerank(query: str, candidates: list[RankedResult], db_path: Path | None = None) -> Result[list[SearchResult]]`:
     - If `candidates` is empty, return `Success([])`.
     - Load cross-encoder via `_get_reranker()`.
     - Build `(query, candidate.snippet)` pairs. Call `model.predict(pairs)` to get scores.
     - For each candidate, call `get_by_path(candidate.vault_path, db_path)`. If `Success(None)` (stale row), skip. If `Failure`, skip with a warning log.
     - Build `SearchResult` with: `vault_path`, `summary` from DocumentRow, `snippet` from the RankedResult, `score` = cross-encoder score, `metadata` dict with `title`, `project`, `note_type`, `updated_at`, `key_topics` (parsed from DocumentRow), and `tags` (key_topics is already the filtered list).
     - Sort by score descending.
     - Return `Success(results)`.

4. **Run tests:** `uv run pytest tests/test_retrieval/test_reranker.py -x`

**Files to modify:**
- `src/retrieval/reranker.py` -- new file: `SearchResult`, `_get_reranker()`, `rerank()`
- `tests/test_retrieval/test_reranker.py` -- new test file

**Test criteria:**
- [x] Every returned card has handle + summary + snippet + score + metadata (P3-SRCH-04)
- [x] Score is the cross-encoder score; results ordered by score descending
- [x] Stale-row candidates (no documents row) are skipped without crashing (P3-SRCH-06)
- [x] Card metadata contains `title`, `project`, `note_type`, `updated_at`, `key_topics`
- [x] Title in metadata is the descriptive title, not a filename (P3-SRCH-05)
- [x] Empty input returns `Success([])`
- [x] Function returns `Result`, never raises

**Status:** [x] done

**Completed**: 2026-06-11
**Notes**: Implemented via TDD. 7 tests, 1162 total (0 regressions). Cross-encoder model `cross-encoder/ms-marco-MiniLM-L-6-v2` lazy-loaded and cached at module level (same pattern as `_get_model` in embeddings.py). Cards are cheap -- summary from DocumentRow, snippet from RankedResult, metadata dict with title/project/note_type/updated_at/key_topics/tags. `predict()` return value normalised to list for robustness. Clean lint (ruff 0 findings).

---

### Phase 5 -- Search Coordinator (Component 4)

**Goal:** Wire the Candidate Filter, Hybrid Ranker, and Re-ranker into a single public entry point (`search()`) that is the stable contract for both the CLI and the future Phase 4 MCP tool.

**Depends on:** Phases 2, 3, 4.

**Spec reference:** Component 4 (Search Coordinator). Covers all P3-SRCH behaviors.

**Steps:**

1. **Write tests first (RED)** in a new file `tests/test_retrieval/test_search.py`:
   - **Setup helper**: A `_seed_full_db` function that creates a temp DB, inserts 3-4 notes with varied projects and dates, and indexes them via the real indexers. Reuse the seeding pattern from Phase 3 tests.
   - **`test_search_with_query_returns_ranked_results`**: Call `search(query="stakeholder resistance", db_path=db)`. Assert `Success` with a non-empty list of `SearchResult`s. The semantically relevant note should be present.
   - **`test_search_filter_only_returns_newest_first`**: Call `search(project="Alpha", db_path=db)` (no query). Assert results are sorted by `updated_at` descending. Assert no cross-encoder was loaded (score = 0.0).
   - **`test_search_query_plus_project_scopes_ranking`**: Call `search(query="budget", project="Alpha", db_path=db)`. Assert results come only from Alpha.
   - **`test_search_date_range_filters`**: Call `search(date_range=(recent_datetime, None), db_path=db)`. Assert only recent notes returned (P3-SRCH-08).
   - **`test_search_empty_candidates_returns_empty`**: Call `search(project="NonExistent", db_path=db)`. Assert `Success([])`.
   - **`test_search_no_args_global_search`**: Call `search(query="budget", db_path=db)` with no project/date. Assert it searches all notes (global mode).
   - **`test_search_max_results_caps_output`**: Seed 5 notes. Call `search(query="notes", max_results=2, db_path=db)`. Assert at most 2 results.
   - **`test_search_returns_result_type`**: Assert returns `Result`.
   - **`test_search_filter_only_cards_have_metadata`**: In filter-only mode, assert each card still has `summary` and `metadata` (pulled from the catalog).

2. **Implement (GREEN)** in a new file `src/retrieval/search.py`:
   - Import `filter_paths` from `storage.documents`, `rank` from `retrieval.ranker`, `rerank` from `retrieval.reranker`, `get_by_path` from `storage.documents`.
   - `search(query=None, project=None, date_range=None, max_results=None, db_path=None) -> Result[list[SearchResult]]`:
     - Read `max_results` and `max_candidates` from `CONFIG.main.search` (lazy import inside function body, C-17 pattern).
     - Override `max_results` if the caller passed it.
     - **Step 1 -- Candidate Filter**: Call `filter_paths(project=project, since=date_range[0] if date_range else None, until=date_range[1] if date_range else None, db_path=db_path)`. On `Failure`, return the failure. On `Success(None)`, set `candidate_paths = None` (global). On `Success([])`, return `Success([])`. On `Success(paths)`, set `candidate_paths = paths`.
     - **Step 2 -- Branch**:
       - **Filter-only** (`query is None`): For each candidate path (or all paths if global), fetch the DocumentRow via `get_by_path`. Build `SearchResult` cards with `score=0.0`. Sort by `updated_at` descending. Cap at `max_results`. Return.
       - **Query branch** (`query` given): Call `rank(query, candidate_paths, max_candidates, db_path)`. On failure, return failure. Then call `rerank(query, ranked_results, db_path)`. On failure, return failure. Cap at `max_results`. Return.
   - Add a shared helper `_card_from_row(row: DocumentRow, snippet: str = "", score: float = 0.0) -> SearchResult` for building cards in the filter-only branch (same card shape as the re-ranker produces).

3. **Update `src/retrieval/__init__.py`** to export the public surface:
   - `from retrieval.search import search, SearchResult` (the stable contract).

4. **Run tests:** `uv run pytest tests/test_retrieval/test_search.py -x`

5. **Run full retrieval suite:** `uv run pytest tests/test_retrieval/ -x`

**Files to modify:**
- `src/retrieval/search.py` -- new file: `search()`, `_card_from_row()`
- `src/retrieval/__init__.py` -- export `search`, `SearchResult`
- `tests/test_retrieval/test_search.py` -- new test file

**Test criteria:**
- [x] `search("...")` returns ranked cards (query branch)
- [x] `search(project="Alpha")` returns Alpha notes newest-first (filter-only branch, P3-SRCH-02)
- [x] `search("...", project="Alpha")` scopes the ranking to Alpha (P3-SRCH-03)
- [x] `search(date_range=...)` filters by date (P3-SRCH-08)
- [x] Empty candidates returns `Success([])`
- [x] `max_results` caps the output
- [x] Filter-only cards have summary + metadata (same shape as ranked cards)
- [x] Function returns `Result`, never raises

**Status:** [x] done

**Completed**: 2026-06-11
**Notes**: Implemented via TDD. 9 tests, 1171 total (0 regressions, full suite green). `search()` wraps filter_paths -> rank -> rerank pipeline. Two branches: query branch executes full ranking chain; filter-only branch (query=None) fetches catalog rows via get_by_path, builds SearchResult cards with score=0.0 sorted by updated_at desc. `_card_from_row()` shared helper ensures same card shape in both branches. Lazy CONFIG import (C-17). Clean lint (ruff 0 findings).

---

### Phase 6 -- Search Command (Component 5)

**Goal:** Replace the `kms search` CLI stub with a real command that exercises the search coordinator end-to-end, including a `--reindex` maintenance mode.

**Depends on:** Phase 5 (calls `search()`). Also uses `all_paths`, `read_note`, `index_embedding`, `index_keywords` for `--reindex`.

**Spec reference:** Component 5 (Search Command). Covers all P3-SRCH behaviors. Closes TD-012 (search stub).

**Steps:**

1. **Write tests first (RED)** in a new file `tests/test_cli/test_search_command.py`:
   - These are CLI integration tests using Click's `CliRunner`.
   - **`test_search_query_invokes_search_function`**: Monkeypatch `retrieval.search.search` to return `Success([mock_result])`. Invoke `cli search "budget"`. Assert exit code 0, output contains the mock result's title.
   - **`test_search_project_option`**: Invoke `cli search --project Alpha "budget"`. Assert the monkeypatched `search` was called with `project="Alpha"`.
   - **`test_search_since_7d_parses`**: Invoke `cli search --since 7d "budget"`. Assert `search` was called with a `date_range` tuple where the first element is approximately 7 days ago.
   - **`test_search_since_date_parses`**: Invoke `cli search --since 2026-06-01 "budget"`. Assert `date_range[0]` is `datetime(2026, 6, 1)`.
   - **`test_search_no_query_filter_only`**: Invoke `cli search --project Alpha`. Assert `search` was called with `query=None`.
   - **`test_search_reindex_flag`**: Monkeypatch `all_paths`, `read_note`, `index_embedding`, `index_keywords`. Invoke `cli search --reindex`. Assert indexers were called for each path.
   - **`test_search_reindex_is_standalone`**: Invoke `cli search --reindex "budget"`. Assert usage error (cannot combine `--reindex` with a query).
   - **`test_search_max_option`**: Invoke `cli search --max 5 "budget"`. Assert `search` was called with `max_results=5`.
   - **`test_search_failure_prints_error`**: Monkeypatch `search` to return `Failure`. Assert exit code 1 and error message in output.

2. **Implement (GREEN)** by rewriting the `search` command in `src/cli/main.py` (lines 108-112):
   - Change the Click signature:
     - `@click.argument("query", required=False, default=None)` -- make query optional
     - `@click.option("--project", default=None, help="Filter by project name")`
     - `@click.option("--since", default=None, help="Filter by date: 7d, 30d, or YYYY-MM-DD")`
     - `@click.option("--max", "max_results", default=None, type=int, help="Max results")`
     - `@click.option("--reindex", is_flag=True, default=False, help="Rebuild search indexes")`
   - Command body:
     - If `--reindex`: validate no query given (raise `UsageError` if both). Run the reindex loop:
       - `import asyncio; from storage.documents import all_paths; from vault.reader import read_note; from retrieval.embeddings import index_embedding; from retrieval.keyword import index_keywords; from core.config import CONFIG`
       - Enumerate `all_paths(db_path)`. For each `(vault_path, _hash)`, call `read_note(CONFIG.main.vault.root / vault_path)`. Extract title/type/tags/summary/body from the note. Call `index_embedding(vault_path, title, note_type, tags, summary)` and `index_keywords(vault_path, title, summary, body)`. Count successes.
       - Print count and exit.
     - Otherwise: parse `--since` into a `date_range` tuple (CLI's one piece of presentation logic):
       - `"7d"` -> `(datetime.now() - timedelta(days=7), None)`
       - `"30d"` -> `(datetime.now() - timedelta(days=30), None)`
       - `"YYYY-MM-DD"` -> `(datetime.strptime(value, "%Y-%m-%d"), None)`
       - Invalid format -> `raise click.BadParameter(...)`
     - Wrap `search()` in `asyncio.run()` (C-10).
     - Print each result card: title, score, snippet (one block per result).

3. **Run tests:** `uv run pytest tests/test_cli/test_search_command.py -x`

**Files to modify:**
- `src/cli/main.py` -- rewrite the `search` command (lines 108-112)
- `tests/test_cli/test_search_command.py` -- new test file

**Test criteria:**
- [ ] `kms search "..."` calls `search()` and prints results
- [ ] `--project` is passed through to `search()`
- [ ] `--since 7d` parses to a datetime ~7 days ago
- [ ] `--since 2026-06-01` parses to the exact date
- [ ] No query + `--project` triggers filter-only mode
- [ ] `--reindex` enumerates notes and calls both indexers; prints count
- [ ] `--reindex "query"` is rejected (standalone only)
- [ ] `--max` is passed through as `max_results`
- [ ] Failure from `search()` prints error and exits 1

**Status:** [x] done

**Completed**: 2026-06-11
**Notes**: Implemented via TDD. 9 tests, 1171+ total (0 regressions in relevant suites). Replaced the `NotImplementedError` stub with a real search command. Five Click decorators: optional `query` argument, `--project`, `--since` (7d/30d/YYYY-MM-DD), `--max` (max_results), `--reindex` (standalone flag). Three helpers: `_parse_since()` converts since string to date_range tuple; `_print_result_card()` formats SearchResult cards (title, score, project, type, tags, snippet, summary); `_run_reindex()` reindexes all notes via all_paths/read_note/index_embedding/index_keywords. Divergence: plan specified `asyncio.run(search(...))` but `search()` is synchronous -- called directly instead. C-10 only applies to async entry points. Clean lint (ruff 0 findings).

---

### Phase 7 -- TD-051 Classify Cross-Type Validation (Component 6)

**Goal:** Stop the classifier from accepting a domain name as a valid project destination (and vice versa). Today both are pooled into one name set via `_destination_names()`.

**Depends on:** Nothing -- fully isolated from the search work. Ships alongside but has no dependency on Components 0-5.

**Spec reference:** Component 6 (TD-051). Covers behavior P3-SRCH-09.

**Steps:**

1. **Write tests first (RED)** in `tests/test_pipelines/test_classify.py`:
   - **Update the `VALID_DESTINATIONS` fixture** (line 126): Change from `"Projects:\n  - Alpha\nDomains:\n  - Finance"` to the real `format_for_prompt` shape, e.g. `"Finance:\n  - Alpha\nUncategorized:\n  - No active projects"`. This is the format that `format_for_prompt()` actually produces (domain as header, projects as items under it).
   - **Add `PROJECT_NAMES` and `DOMAIN_NAMES` module constants** for the test file: `PROJECT_NAMES = frozenset({"Alpha"})`, `DOMAIN_NAMES = frozenset({"Finance"})`. These simulate what the call site extracts from the registry.
   - **`test_classify_rejects_domain_as_project`**: AI returns `{"project": "Finance", ...}` (a domain name used as project). Assert `Failure(recoverable=True)`. The error message should indicate cross-type violation.
   - **`test_classify_rejects_project_as_domain`**: AI returns `{"primary_domain": "Alpha", ...}` (a project name used as domain). Assert `Failure(recoverable=True)`.
   - **`test_classify_accepts_project_in_correct_field`**: AI returns `{"project": "Alpha", "primary_domain": "Finance", ...}`. Assert `Success`.
   - **Update existing tests** that call `classify()` to pass the new `project_names` and `domain_names` parameters (the signature change). Approximately 6-8 test methods need updating. Use `PROJECT_NAMES` and `DOMAIN_NAMES` constants. Keep CONFIG lazy via `_make_config()` (C-17).

2. **Implement (GREEN)** in `src/pipelines/classify.py`:
   - **Change the `classify()` signature**: Add two new optional parameters: `project_names: frozenset[str] | None = None` and `domain_names: frozenset[str] | None = None`. When both are `None`, fall back to the existing pooled `_destination_names()` behavior (backward compatible).
   - **Replace the validation logic** (Steps 8-9, lines 197-209):
     - When `project_names` and `domain_names` are provided:
       - Step 8: Validate `project` against `project_names` only (not `valid_names`).
       - Step 9: Validate `primary_domain` against `domain_names` only (not `valid_names`).
     - When they are `None` (backward compat): use the existing `_destination_names()` pooled set.
   - **Keep `_destination_names()`** -- it is still used by the backward-compat path and possibly by the prompt string construction elsewhere.
   - **Update call site 1** (`capture.py:639`): After building the `dest_registry`, extract `project_names = dest_registry.all_project_names` and `domain_names = frozenset(k for k in dest_registry.groups if k != "Uncategorized")`. Pass both to `classify()`.
   - **Update call site 2** (`capture.py:2044`): The `_build_vault_context()` helper returns only a string. Refactor `_build_vault_context` to return `tuple[str, frozenset[str], frozenset[str]]` -- the formatted string, project names, and domain names. Update the caller at line 2040 to unpack the tuple. Update the folder-capture classify call at line 2044 to pass all three.

3. **Run tests:** `uv run pytest tests/test_pipelines/test_classify.py -x`

4. **Verify no capture regressions:** `uv run pytest tests/test_pipelines/ -x`

**Files to modify:**
- `src/pipelines/classify.py` -- add `project_names`/`domain_names` params to `classify()`; update Steps 8-9
- `src/pipelines/capture.py` -- update call site 1 (line 639) and call site 2 (via `_build_vault_context`, line 2040/2044)
- `tests/test_pipelines/test_classify.py` -- update `VALID_DESTINATIONS` fixture; add cross-type tests; update ~6-8 existing tests

**Test criteria:**
- [ ] `classify()` rejects a domain name supplied as `project` (P3-SRCH-09)
- [ ] `classify()` rejects a project name supplied as `primary_domain` (P3-SRCH-09)
- [ ] `classify()` accepts each name in its correct field
- [ ] Backward compat: calling without `project_names`/`domain_names` still validates against the pooled set
- [ ] Existing classify tests pass with the updated fixture shape
- [ ] Existing capture tests pass (call site changes are backward-compatible)

**Status:** [ ] pending

---

## Open Questions

1. **CrossEncoder offline availability** (from research): The `cross-encoder/ms-marco-MiniLM-L-6-v2` model must be in the local Hugging Face cache for offline reranking. The implementer should confirm the model is cached (run `_get_reranker()` once before relying on offline behavior). If not cached, a first-run download is needed. This is an environment concern, not a code concern.

2. **`sqlite-vec` pin tightness**: `pyproject.toml` has `>=0.1.9` (floor pin). The filtered-KNN behavior is verified on v0.1.9. Consider tightening to `==0.1.9` to prevent a future `uv sync` from pulling a version that changes filtered-KNN semantics. The Phase 3 regression test (in test_ranker.py) acts as a safety net regardless.

---

## Out of Scope

- MCP `kms_search` tool -- Phase 4
- Worker-thread offload for CPU-bound search -- Phase 4 daemon
- Tier dispatcher / `max_cost` budget / hot-warm-cold -- dead per ADR-0009
- Full-content terminal dump -- cards carry no body
- Query-time LLM embedding or ranking -- all inference is in-process
- TD-010 (Ollama httpx async rewrite) -- not this cycle
- Scheduling / automation -- Phase 4+
- New audit entries for search -- search makes no AI decision (C-13 N/A)
- Backfilling pre-R1 siblings with descriptive titles -- requires re-capture, out of scope for R1
- New DB migration -- Session B is read-only against Session A schema

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `sqlite-vec` upgrade changes filtered-KNN semantics | Low | High (meaning search breaks silently) | Phase 3 regression test asserts partition property; consider hard-pinning `==0.1.9` |
| Cross-encoder model not in local cache on CI/CD | Medium | Medium (test fails on first run) | Document the model download requirement; add a conftest fixture that skips if model unavailable |
| Large `IN (...)` clause hits SQLite variable limit | Low | Medium (query fails for very large vaults) | Global mode (candidates=None) omits the IN clause entirely; only project+date filtering builds an IN list, which is bounded by the project's note count |
| `_build_vault_context` refactoring for TD-051 breaks folder-capture classify | Low | Medium | Phase 7 tests cover the call site; backward-compat fallback in classify() |
| `title` field name collision with existing frontmatter | None | N/A | Research confirmed no collision; `title` was in `extra`, now rerouted to typed field |
