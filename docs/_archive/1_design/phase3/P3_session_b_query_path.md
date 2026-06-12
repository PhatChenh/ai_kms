# Phase 3 Session B — Query Path (Hybrid Search) — Design

_ID prefix for behavior inventory: **`P3-SRCH`** (Phase 3, search). Entries `P3-SRCH-01`..`P3-SRCH-09` appended to `docs/system_behavior/behavior_inventory.yaml` with `origin: design`, `granularity: outcome|mechanism`._

_Status: DESIGN (build-pipeline design step, 2026-06-10). Requirements locked by grill 2026-06-10; this doc records the chosen option, the load-bearing KNN-scoping sub-decision, and what the spec/research steps must verify._

---

## In plain terms

This feature lets the system **find the right notes for a question** — and it is built mainly so the AI assistant (a later phase) can pull up relevant notes when it writes reports or tracks a project, not so a person types searches at a terminal. You ask with an optional question, an optional project, and an optional date window. The system narrows the note pile down by project/date first, then — if you gave a question — runs two different searches (one that matches words, one that matches meaning), blends their rankings, and finally has a small "second-opinion" model re-score the top handful against your exact question. What comes back is a **cheap summary card per note** (title, summary, snippet, score, and a small facts block) — never the full note. The AI reads the cards, decides which notes matter, and only then opens the full ones. If you give no question at all, it simply lists the matching notes newest-first.

The one genuinely hard engineering question this design had to answer: **how do we run the "meaning" search but restrict it to only the notes that passed the project/date filter?** We proved by running the real library that the vector store can do this directly — see the Decision below.

---

## Cast of characters

| Name | Plain-English role |
|---|---|
| **Search Function** | The public entry point callers use; orchestrates the whole flow (`src/retrieval/search.py`, new). |
| **Candidate Filter** | Narrows the full note list to the ones matching the project/date filters (a query inside the Search Function over the `documents` table). |
| **Hybrid Ranker** | Runs the word search and the meaning search over the candidates and blends the two rankings (`src/retrieval/ranker.py`, new). |
| **Word Index** | The keyword/full-text store, ranks by word overlap (`notes_fts`, FTS5 table; built in Session A). |
| **Meaning Index** | The vector store, ranks by semantic similarity (`embeddings_vec`, sqlite-vec `vec0` table; built in Session A). |
| **Reciprocal Rank Fusion (RRF)** | The blending rule that merges the two ranked lists into one fused order using rank position, not raw scores. |
| **Re-ranker** | A small in-process model that re-scores the top candidates against the exact question and attaches each note's summary + facts (`src/retrieval/reranker.py`, new). |
| **Search Result** | The cheap card returned per note: handle + summary + snippet + score + metadata. No full body. |
| **File Index** | The note metadata table (`documents`) — title, project, dates, summary; source of the Candidate Filter and the metadata block. |
| **Note Reader** | Loads a full note from disk on demand (`vault/reader.py::read_note`); the AI calls this only for notes it judges relevant. |

---

## Decision

**Build the hybrid search as: metadata pre-filter → (no question) filter-only mode OR (question) RRF blend of word + meaning search → cross-encoder re-rank → capped result cards.** This is the grill-locked architecture; it deliberately replaces the roadmap's stale "tier dispatcher / cost-budget" narrative (see ADR-0009).

**The load-bearing sub-decision — how the meaning search is scoped to the filtered candidates — is resolved as Option A (in-database filtered KNN):** the meaning search runs `embedding MATCH <query-vector> AND k = <n> AND vault_path IN (<candidates>)`. We verified against the **actually installed sqlite-vec v0.1.9** that this form does true nearest-neighbour search *restricted to the candidate set* and returns real distances.

Why this and not the draft's assumed form: the draft assumed `WHERE vault_path IN (...) ORDER BY distance` (no `MATCH`). We ran that exact query against the real table — **it executes but every `distance` comes back `NULL`** (it is a plain metadata scan, no KNN happens). That is a silent correctness trap, not a viable option. The `MATCH ... AND k ... AND vault_path IN (...)` form is the correct one and is a documented sqlite-vec "metadata filtering" feature, confirmed empirically here.

> **Recommended: Option A (in-database filtered KNN).** It keeps the meaning search inside one SQL call the database is built to optimise, needs no Python-side vector math, and was proven correct against the installed library version — at the cost of a hard dependency on a sqlite-vec capability we must pin in tests.

---

## Q1 Diagram — what happens inside

```
# Hybrid Search — What Happens Inside
Scope: Shows what happens when one search request runs.
       Does NOT cover how notes get indexed (that is capture-time work),
       nor how the AI consumer decides which results to open.

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

       Search request arrives
   (question?, project?, dates?, cap)
                │
                ▼
     ┌────────────────────────┐
     │ Candidate Filter       │
     │ Narrow all notes by    │
     │ project + date range   │
     │ (none given → all)     │
     └───────────┬────────────┘
                 │
          ┌──────┴───────┐
          │              │
   "Is there a        "There is
    question?"         no question"
          │ YES          │ NO
          ▼              ▼
 ┌──────────────────┐  ┌────────────────────┐
 │ Hybrid Ranker    │  │ Filter-Only Mode   │
 │ Word + Meaning   │  │ Sort candidates by │
 │ search the       │  │ most-recent, cap,  │
 │ candidates, then │  │ return             │
 │ blend the two    │  └────────────────────┘
 │ rankings into one│
 └────────┬─────────┘
          │ top blended candidates
          ▼
 ┌──────────────────┐
 │ Re-ranker        │
 │ Re-score each vs │
 │ the question;    │
 │ attach summary + │
 │ metadata         │
 └────────┬─────────┘
          │
          ▼
 ┌──────────────────────────┐
 │ Search Results (capped)  │
 │ handle + summary +       │
 │ snippet + score + data   │
 └──────────────────────────┘
```

```
Simplified: The Hybrid Ranker box hides three internal steps —
  Word Index ranking (keyword match), Meaning Index ranking
  (vector similarity), and Reciprocal Rank Fusion that blends them.
  Both inner searches are restricted to the candidate set from the
  Candidate Filter. Drawn as one box to stay within the 7-box limit.
```

**Inside the Hybrid Ranker (expansion of the hidden box):**

```
   candidate vault_paths + question
                │
        ┌───────┴────────┐
        ▼                ▼
 ┌─────────────┐   ┌──────────────┐
 │ Word Index  │   │ Meaning Index│
 │ rank by     │   │ rank by      │
 │ word match  │   │ similarity   │
 │ (candidates)│   │ (candidates) │
 └──────┬──────┘   └──────┬───────┘
        │ ranked list      │ ranked list
        └────────┬─────────┘
                 ▼
        ┌──────────────────┐
        │ Rank Fusion      │
        │ blend by position│
        │ → one fused order│
        └──────────────────┘
```

---

## Implications

- **A brand-new public capability appears: the system can answer "find me notes about X."** This is the contract the Phase 4 MCP AI will consume; until now nothing could query the vault by meaning.
  - New module `src/retrieval/search.py` exposes `search(query=None, project=None, date_range=None, max_results=None, db_path=None) -> Result[list[SearchResult]]`. `retrieval/__init__.py` is currently empty (verified) — Session B populates the package's public surface.

- **The result is a cheap "triage card," not the note itself — so the AI can scan many notes for almost no cost and open only the few it needs.** The `metadata` field is load-bearing: the AI triages on it.
  - `SearchResult(vault_path, summary, snippet, score, metadata)` (intended; carries NO body). Full content fetched lazily via `vault/reader.py::read_note(path) -> Result[Note]` (verified signature; `read_note` takes an absolute `Path`, so callers must join `vault.root + vault_path`).

- **The meaning search can be pinned to just the filtered notes in a single database call.** Proven against the installed library, not assumed.
  - `embeddings_vec` is a `vec0` virtual table `vault_path TEXT PRIMARY KEY, embedding FLOAT[384]` (verified in `src/storage/migrations/007_search_indexes.sql` — note the real filename is `007_search_indexes.sql`, NOT `007_add_search_indexes.sql` as the draft cited).
  - Empirical probe (sqlite-vec **v0.1.9**, the installed version): `SELECT vault_path, distance FROM embeddings_vec WHERE embedding MATCH ? AND k = ? AND vault_path IN (...) ORDER BY distance` returns real distances scoped to the candidate set, and applies the candidate filter *as a partition before* choosing the k nearest (far-but-in-set candidates are returned; near-but-out-of-set are excluded). `k` larger than the candidate count returns only the available candidates (no error). A 1500-element `IN` list works (SQLite default variable limit is 32766).

- **The word search already lives in a 4-column full-text table; snippets must target the right column.** Getting the column index wrong returns a snippet of the wrong field silently.
  - `notes_fts` is FTS5 `vault_path UNINDEXED, title, summary, body` with `tokenize='porter unicode61'` (verified). `body` is **column index 3**; `snippet(notes_fts, 3, ...)` targets the body. BM25 ordering via `ORDER BY bm25(notes_fts)` (ascending — lower is more relevant). Candidate scoping for FTS5 is a plain `AND vault_path IN (...)` on the `UNINDEXED` column.

- **The "second opinion" model is a local CPU model that ships inside a library we already depend on — no new package, no API call.** Search never touches Ollama or any chat provider.
  - `CrossEncoder` ships inside `sentence-transformers` (already in `pyproject.toml`: `sentence-transformers>=2.2.0`). Loaded from `CONFIG.main.search.reranker_model` (= `cross-encoder/ms-marco-MiniLM-L-6-v2`, verified in config). Cache the instance at module level, mirroring `retrieval/embeddings.py::_get_model()` (verified to exist and be reused for the query embedding).

- **The query embedding must be built the same way the stored embeddings were, or the meaning search compares apples to oranges.** Symmetry matters.
  - Reuse `retrieval/embeddings.py::_get_model()` (cached `SentenceTransformer`, verified) to encode the raw query string for the KNN. NOTE: stored document embeddings were built from `_build_context_text(title, note_type, tags, summary)` (a composite string), not raw text. The query has only the user's words. The spec/research step must decide whether to embed the bare query or wrap it — see OQ-P3B-2.

- **The metadata block on each card comes from the file index, and dates there are stored as text.** Date filtering relies on text comparison working for ISO strings.
  - `documents` columns (verified): `vault_path, title, summary, note_type, confidence, created_at, updated_at, updated_by_human, content_hash, batch_id, project, status, key_topics`. `created_at`/`updated_at` are TEXT written by `datetime('now')` → format `YYYY-MM-DD HH:MM:SS`. Lexicographic string comparison **is** chronological for this fixed-width format, so `WHERE updated_at >= ?` works — provided the bound is formatted identically (no `T` separator, no timezone). The CLI `--since 7d` must produce a `datetime('now')`-shaped bound. Confirm at research.
  - `documents.get_by_path(vault_path) -> Result[DocumentRow|None]` and `documents.all_paths() -> Result[list[tuple[str,str]]]` are verified. There is **no** existing "filter documents by project/date" function — the Candidate Filter is new SQL inside `search.py` (or a small helper in `documents.py`). It must use `storage/db.py::get_connection(db_path, readonly=True)` (verified to set the FK pragma and load sqlite-vec).

- **Search must not crash when an index row points at a note that was deleted out of band.** A stale row should be skipped, not fatal. (Carried grill decision #5.)
  - Session A wired index cleanup into `documents.delete_by_path`/`rename`/`replace_path` (verified — both `embeddings_vec` and `notes_fts` are cleaned in the same transaction). But a direct vault delete or an interrupted op can still leave an orphan. The Re-ranker calls `get_by_path`; a `Success(None)` → skip that candidate.

- **The CLI command must wrap the search in the standard async entry, even though the search itself is synchronous CPU work.** This is the project's fixed async contract for Click commands.
  - `kms search` lives in `src/cli/main.py` (currently a `raise NotImplementedError` stub, verified). All existing commands wrap with `asyncio.run(...)` (C-10, verified pattern in `capture`/`reconcile`/`watch`). `search()`/`rank()`/`rerank()` are intended sync; `.encode()`/`.predict()` are CPU-bound and will block the event loop for the duration. Since the CLI runs one command then exits and there is no concurrency, calling sync search directly inside the wrapper coroutine is acceptable for Session B — see OQ-P3B-1 for the Phase-4 daemon implication.

- **The classify cross-type leak (TD-051) is fixed in the same cycle, as an isolated final phase.** Today a domain name can pass as a valid project and vice versa.
  - `pipelines/classify.py::classify(subject, valid_destinations: str, config)` validates both `project` and `primary_domain` against ONE pooled set from `_destination_names(valid_destinations)` (verified). Fix: pass **two** sets — project names and domain names — and validate each field against its own set. Source them from the structured registry: `vault/registry.py::ProjectRegistry.all_project_names` (frozenset of project names, verified) and the registry's group keys (= domain names; `Uncategorized` excluded). The capture call site (`capture.py` ~line 631 and ~line 2040) builds the registry already, so it can pass the two sets without re-parsing the prompt string. Rewrite `VALID_DESTINATIONS` test fixture (`tests/test_pipelines/test_classify.py:126`, currently `"Projects:\n  - Alpha\nDomains:\n  - Finance"` — the wrong shape) to the real `format_for_prompt` shape (domain names as headers, project names as items) plus the new split-set inputs. ~6 tests.

- **Module-depth check.** The new `retrieval/` modules each earn their keep (deletion test): removing `ranker.py` would scatter RRF + dual-index SQL into every caller; removing `reranker.py` would scatter cross-encoder lifecycle + metadata assembly; removing `search.py` would force every caller (CLI now, MCP later) to re-implement the filter→branch→rank→rerank orchestration. `search.py` is the real seam (2+ adapters: CLI in Session B, MCP tool in Phase 4). `ranker.py`/`reranker.py` are currently single-caller (only `search.py`) — justified not as speculative seams but as **depth/readability boundaries**: each hides a distinct, sizeable implementation (SQL + fusion math; model lifecycle + scoring) behind a one-line interface, keeping `search.py` shallow and legible.

---

## Known tradeoffs

- **We bet on a specific sqlite-vec capability.** Choosing in-database filtered KNN means the meaning search depends on `vec0` honouring `MATCH + k + IN (...)`. We proved it on v0.1.9, but a future sqlite-vec upgrade could change KNN-filter semantics. We give up the model-agnostic safety of computing similarity in Python; we gain speed and simplicity. Mitigated by a pinned dependency and a focused test (see Risks).
- **The re-ranker adds CPU latency the filter-only path avoids.** A question-mode search loads and runs a cross-encoder over the top candidates; filter-only mode does neither. We accept per-query model cost for relevance quality, and cap the work via `max_candidates`.
- **No tiered cost budget.** We give up the roadmap's promised "escalate from cheap to expensive within a cost ceiling" behavior. We replace it with a cheaper idea: a lightweight card payload + lazy full-content fetch (ADR-0009). If a real cost ceiling is ever needed, it is a new feature, not a tweak.

---

## Risks (for research / planning / implementation to verify)

- **R1 — vec0 filtered-KNN must be re-confirmed at the pinned version and exercised by a test.** The whole meaning-search design rests on `MATCH + k + vault_path IN (...)` returning real, candidate-scoped distances. Research must re-run the probe against the project's actual DB (not `:memory:`) and the implementer must add a test that fails if a sqlite-vec upgrade silently changes the semantics (e.g. asserts a far-but-in-set candidate is returned and a near-but-out-of-set is not). _Verified today on v0.1.9; pin `sqlite-vec` accordingly._
- **R2 — query/document embedding symmetry.** Stored embeddings come from `_build_context_text(...)` (title+type+tags+summary), not raw text. Embedding the bare query may degrade match quality. Research must decide and document the query-side encoding (OQ-P3B-2).
- **R3 — date bound format.** `--since` must format its lower bound exactly like `datetime('now')` (`YYYY-MM-DD HH:MM:SS`, space separator, UTC) or the string comparison silently under/over-selects. Verify against a captured row.
- **R4 — empty-candidate and all-candidate edges.** "No filter" → candidates = all paths; do NOT build a giant `IN (...)` in that case — omit the `IN` clause and let KNN run globally (faster, and avoids the variable-count ceiling). "Filter matched nothing" → return `Success([])` without touching the indexes. Research/spec must specify both branches explicitly.
- **R5 — sibling-note title.** Results for a binary's sibling must show the indexed title, not `report.pdf.md`. The Word/Meaning indexes were fed the real title at capture time (verified: capture passes `mr.ai_title` / stem). The Re-ranker reads the title from `documents.title` via `get_by_path` — confirm that column holds the human title for sibling rows, not the filename stem.
- **R6 — `RankedResult`/`SearchResult` shapes are intended, not yet code.** The draft's signatures (`rank(query, candidate_paths, max_candidates)`, `rerank(query, candidates)`) are proposals. Research confirms field names/types before the spec freezes them.
- **R7 — C-12 scope.** The Result-type contract names `handlers/` and `pipelines/`; `retrieval/` is new. Apply Result returns to `retrieval/` for consistency (Session A already does), but note it is convention here, not hook-enforced.

---

## Open questions

**OQ-P3B-1 — Should synchronous, CPU-heavy search run on a worker thread, or directly inside the async CLI wrapper?**

Right now every command runs once and the program exits, so nothing else is waiting while the search computes.

The question: do we run the model work directly inside the async wrapper (simple), or push it onto a background worker thread so it never blocks an event loop?

**If we run it directly:** simplest code; fine for the one-shot CLI; but when the Phase 4 MCP daemon serves two requests at once, one search's model work would freeze the other.
**If we use a worker thread now:** future-proof for the daemon, at the cost of thread-handoff complexity the CLI does not yet need.

Recommendation: run directly inside the wrapper for Session B (CLI is single-shot, no concurrency), and revisit thread-offloading when the Phase 4 daemon is designed. One sentence why: building daemon-grade concurrency before the daemon exists is speculative work the grill scope explicitly excludes.

**OQ-P3B-2 — How should the question be turned into a meaning-search vector so it matches how notes were stored?**

Right now, stored note vectors were built from a composite string (title + type + tags + summary), but a search question is just the user's words.

The question: do we embed the bare question, or wrap it in a matching template before embedding?

**If we embed the bare question:** simplest; may match slightly worse because the question and the stored text were shaped differently.
**If we wrap it to mirror the stored format:** more symmetric, potentially better matches, but we are guessing the user's title/type/tags — likely noise.

Recommendation: embed the bare question (no wrapping) and let RRF + the re-ranker absorb any asymmetry. One sentence why: the re-ranker scores the question against real snippets directly, so first-stage recall only needs to be good enough to surface candidates, not perfect.

**OQ-P3B-3 — Should the Candidate Filter SQL live in `search.py` or as a helper in `documents.py`?**

Right now there is no "filter documents by project and date" function anywhere; `documents.py` has only single-path and all-path readers.

The question: put the new filter query inline in the Search Function, or add a reusable reader to the document-access layer?

**If inline in `search.py`:** keeps all search logic in one place; mixes raw SQL into the orchestrator.
**If a helper in `documents.py`:** keeps SQL in the data-access layer (matches the project's "no SQL outside storage/" habit); adds one small function the Briefing phase could reuse later.

Recommendation: add a small helper in `documents.py` (e.g. `filter_paths(project, since, db_path)`). One sentence why: it matches where every other `documents` query already lives and gives Phase 8/9 a reuse point, at near-zero extra cost.

---

## ADR references

- **ADR-0009** (`docs/architecture/system_adr/0009-phase3-search-rrf-rerank-not-tier-dispatcher.md`) — records why Phase 3 search uses RRF + cross-encoder re-rank with a metadata pre-filter instead of the roadmap's `max_cost` tier dispatcher, and the in-database filtered-KNN sub-decision. This design doc is the source of that ADR.

---

## Options explored

### Option A — In-database filtered KNN (CHOSEN)
The meaning search runs `embedding MATCH ? AND k = ? AND vault_path IN (candidates)` directly in sqlite-vec; the word search runs FTS5 BM25 with `AND vault_path IN (candidates)`; RRF blends both. Chosen because it was **proven correct on the installed sqlite-vec v0.1.9**, keeps vector math in the database, and is the simplest correct form.

### Option B — Global KNN, then filter in Python (viable, not chosen)
Run KNN with no candidate constraint, fetch a large `k`, then drop any result not in the candidate set in Python.
- One-sentence summary: let the database find the globally nearest notes, then discard the ones outside the project/date filter afterwards.
- Main reasons not selected: after filtering you can be left with **fewer than the candidates you wanted** (the k nearest globally may all be outside the filter), forcing an unbounded "increase k and retry" loop; it does redundant work the database can do natively. Only attractive if Option A's filtered KNN were unavailable — which the probe showed it is not.

```
# Option B — Global KNN then Python filter (rejected)
       question + candidate set
                │
                ▼
     ┌────────────────────────┐
     │ Meaning Index          │
     │ find globally nearest  │
     │ (no project/date limit)│
     └───────────┬────────────┘
                 ▼
     ┌────────────────────────┐
     │ Python filter          │
     │ drop results not in    │
     │ the candidate set      │
     └───────────┬────────────┘
                 │   may be < wanted
                 ▼
        "Too few? raise k,
         search again"  ── loops
```

### Option C — Store vectors in a normal column, compute cosine in Python (viable, not chosen)
Keep (or duplicate) the 384-dim vector as a plain blob column, read all candidate vectors, and compute cosine similarity in Python over just the candidate set — no `vec0` KNN at query time.
- One-sentence summary: pull the candidate notes' vectors out and do the similarity math ourselves in Python.
- Main reasons not selected: throws away the optimised vector index Session A already built; for large candidate sets it reads and multiplies every vector in Python (slow, memory-heavy); reintroduces vector math the database already does. Its only upside (no dependency on a vec0 capability) is moot now that the capability is proven.

```
# Option C — Manual cosine in Python (rejected)
       question + candidate set
                │
                ▼
     ┌────────────────────────┐
     │ Read every candidate's │
     │ stored vector          │
     └───────────┬────────────┘
                 ▼
     ┌────────────────────────┐
     │ Python cosine vs query │
     │ for each candidate     │
     └───────────┬────────────┘
                 ▼
        sort by similarity
```

### Rejected alternatives (not viable)
- **Draft's `WHERE vault_path IN (...) ORDER BY distance` (no MATCH):** executes but returns `NULL` distances — no KNN happens. Silent garbage; rejected on the empirical probe.
- **Roadmap's tier dispatcher / `max_cost` budget (hot/warm/cold):** explicitly dead per grill; replaced by the cheap-card + lazy-fetch model (ADR-0009).
- **Calling an LLM/Ollama to embed or rank at query time:** out of scope and against the locked decision — all inference is in-process sentence-transformers.

---

## Guardrail Checklist

_(from `/guardrail-check Review`, filtered to touched domains. Domains checked: DB Integrity, LLM & Providers, Async & CLI, Architecture, Testing C-17. Domains skipped: Write Safety — search is read-only.)_

- [ ] **C-04 · PRAGMA foreign_keys=ON on every connection** — satisfies: all DB access goes through `storage/db.py::get_connection` (verified to set the pragma + load sqlite-vec). No raw `sqlite3.connect()` in `retrieval/`.
- [ ] **C-05 · Schema changes via versioned .sql deltas** — not applicable: Session B is read-only against tables shipped in migration 007. No new DDL.
- [ ] **C-06 · Confidence thresholds in config, never in code** — satisfies: the RRF constant (60) and `max_candidates`/`max_results` live in `retrieval/` (not `pipelines/`) and read from `CONFIG.main.search`. TD-051 must not introduce float-literal `if/elif` in `pipelines/classify.py`.
- [ ] **C-08 · Pipelines use get_provider() factory; no direct provider.complete()** — not applicable / documented divergence: `SentenceTransformer` and `CrossEncoder` are in-process inference, not chat-completion providers; the factory is for `llm/` providers only. Search never calls `get_provider`/Ollama.
- [ ] **C-10 · CLI wraps async pipelines with asyncio.run()** — satisfies: `kms search` wraps with `asyncio.run`; sync CPU work runs inside the wrapper (see OQ-P3B-1).
- [ ] **C-12 · Public functions in handlers/ and pipelines/ return Result** — satisfies (by extension): `retrieval/` is new and not named by the rule, but `search`/`rank`/`rerank` return `Result` for consistency with Session A. TD-051 keeps `classify()` returning `Result`.
- [ ] **C-13 · Audit log for every AI decision** — not applicable: search makes no AI decision (no `provider.complete`, no confidence gate); it is read-only retrieval. No audit entry required.
- [ ] **C-14 · mcp_server/tools.py logic-free** — not applicable: no MCP tool in Session B.
- [ ] **C-15 · No MCP tool before its pipeline exists + tested** — satisfies: MCP `kms_search` tool deferred to Phase 4, after this CLI/pipeline is built and tested.
- [ ] **C-16 · Schedulers come last** — satisfies: no scheduler in Session B; the CLI is the manual verification surface.
- [ ] **C-17 · Never import CONFIG at module scope in tests** — watch: the TD-051 test rewrite must keep CONFIG lazy / use the existing `MagicMock` config stub (`_make_config()`), not a module-scope `from core.config import CONFIG`.

---

## Success criteria

Written to `docs/system_behavior/behavior_inventory.yaml` as `P3-SRCH-01`..`P3-SRCH-09` (`origin: design`). Summary:

- **P3-SRCH-01** — a question finds a semantically related note despite different wording (vector match).
- **P3-SRCH-02** — `--project` with no question returns all that project's notes, newest first (filter-only mode).
- **P3-SRCH-03** — question + `--project` searches semantically but only within that project.
- **P3-SRCH-04** — every result carries handle + summary + snippet + score + metadata, and never the full body.
- **P3-SRCH-05** — a binary's sibling result shows a usable title, not `report.pdf.md`.
- **P3-SRCH-06** — search skips index rows whose note was deleted instead of crashing.
- **P3-SRCH-07** — `--reindex` is idempotent (running twice → identical results).
- **P3-SRCH-08** — `--since` with no question returns recent notes (supports a future weekly-synthesis caller).
- **P3-SRCH-09** — TD-051: classify no longer accepts a domain name as a valid project destination.

---

## Next step

Design doc written. Run `/update-arch-story` (or `/architecture-docs`) to fold the `retrieval/` query path into the main architecture designs, then run `/writing-detailed-specs` to structure the chosen option into build steps. Research must clear R1–R7 and resolve OQ-P3B-1/2/3 before the spec freezes.

---

## Revision R1 — A5 resolution: AI-generated descriptive title at capture

_Added 2026-06-10 (build-pipeline design-touch, loop-back from research). Resolves invalidated assumption **A5** (`docs/3_research/P3_session_b_query_path.md`): a binary-sibling note's `documents.title` holds the filename stem (`report.pdf`), not a human title. Root-cause resolution **LOCKED by the user**: "Fix at capture. Add one more frontmatter field for a descriptive title, and adjust the AI's prompt at the capture phase so the AI generates that descriptive title."_

### In plain terms

When the system captures a file, the AI already writes a short descriptive name for it (for example "Q3 Budget Report"). The problem A5 found is that for a PDF or image, this descriptive name was being thrown away — it never got saved into the note, so the catalog had nothing better to show and fell back to the raw file name `report.pdf`. The fix: **give the note a dedicated place to keep that descriptive name (a new frontmatter field), and make sure the AI fills it in at capture time.** Then the catalog reads that field, and a search card shows "Q3 Budget Report" instead of "report.pdf". Only one link in the chain changes; everything downstream simply receives a better title.

A ground-truth note from reading the code: the AI **already generates** a descriptive title for every capture today. The metadata stage (`pipelines/capture.py::metadata`, line 215) runs `prompts/extract_metadata.yaml`, whose first output field is `"title": a concise, descriptive title`. That value lands on `MetadataResult.ai_title` (`capture.py:79, 309`) for **both** `.md` notes and binaries (a binary's extracted text flows through the same `summarize → metadata` stages). What is missing is purely the **save step**: the title is never written into the note's frontmatter, so the catalog's `_derive_title` (`storage/documents.py:69`) falls back to `Path(vault_path).stem` = `report.pdf`. So "adjust the AI's prompt to generate a descriptive title" is, in code terms, **already satisfied** by `extract_metadata.yaml`; the locked fix's real work is the new frontmatter field + the wiring that carries the existing AI title into it. (See Decision 2 for the one prompt-confirmation caveat.)

### The six decisions (grounded in code)

**Decision 1 — The new frontmatter field: name, job, round-trip, model placement.**

- **Proposed name:** `title` (the natural, human-obvious key; not hard-locked — `display_title` or `ai_title` are acceptable alternatives if `title` collides with any reader's expectations).
- **Its job:** carry the AI's descriptive title (`MetadataResult.ai_title`) from capture onto the saved note's YAML frontmatter, so any catalog reader (`documents.title`, search cards, future briefings) gets a human title instead of a filename.
- **Model placement — first-class field, not `extra`.** Today there is **no** `title` field on `NoteMetadata` (`vault/frontmatter.py:55-77`); a title only travels opaquely via `metadata.extra["title"]`, and nothing in capture ever sets it. The locked direction says "add one more frontmatter field," so make it a real Pydantic `Field` on `NoteMetadata` (`title: str | None = None`) and add `"title"` to `_KNOWN_KEYS` (`frontmatter.py:27`). Rationale (matches the CLAUDE.md Field-vs-property rule): the title is a value the capture stage *supplies*, not a value computed from other fields — so it is a `Field`, not a `@property`.
- **Round-trip through `frontmatter.py`:** `parse()` already routes any key in `_KNOWN_KEYS` onto the typed model (line 134); adding `"title"` to that set makes `title:` parse back into `NoteMetadata.title` on re-read. `dumps()` serialises every non-None model field via `model_dump(exclude_none=True, exclude={"extra"})` (line 160), so a populated `title` is written to disk and an empty one is omitted — no schema churn. **It is NOT a deprecated key** — `_DEPRECATED_KEYS` (line 52) only holds `"domain"`; `title` is brand-new and additive, so `dumps()` will not strip it.

**Decision 2 — Capture prompt change.**

- The prompt that produces the descriptive title is `prompts/extract_metadata.yaml`, and it **already emits** `"title"` (line 6: "a concise, descriptive title (max 120 chars, no slashes or colons)"). It is parsed back at `capture.py:234` (`_parse_metadata_json`) and surfaced as `parsed["title"] → MetadataResult.ai_title` (line 309). **No prompt edit is strictly required** to make the AI generate the title — it already does, for every input including binaries.
- The one prompt-confirmation caveat (carried to Open Questions, not a blocker): `prompts/summarize_attachment.yaml` — the prompt that builds the *body* of a binary sibling — does **not** emit a title and does **not** need to, because the binary's title comes from `extract_metadata.yaml` upstream, not from the body prompt. If a reviewer reads the locked phrase "adjust the AI's prompt" literally as "edit a prompt file," the honest answer is: the descriptive-title instruction already lives in `extract_metadata.yaml`; the only optional prompt tweak would be to tighten that title instruction (e.g. forbid file extensions in the title) — recorded as a sub-question below, not done here. (Prompts are YAML-only, C-07 — any tweak edits the YAML, never code.)

**Decision 3 — Wiring: how the AI title reaches `documents.title` (and the embedding context for free).**

- Set the new field at the three `NoteMetadata` construction sites in `capture.py`, each fed the already-available `mr.ai_title`:
  - `store()` (line 889) — the standard `.md` path.
  - `_store_nonmd` LOCATED sibling (`sibling_meta`, line 1176) — the binary case A5 is about.
  - `_store_nonmd` CLUELESS marker (`marker_meta`, line 1330) — the inbox needs-review case.
  - Pattern: `title=mr.ai_title or None` (let an empty AI title fall through to the existing stem behaviour).
- From there, `documents.upsert` / `replace_path` derive the stored column via `_derive_title(outcome)` (`storage/documents.py:69`). **`_derive_title` must be taught to prefer the new field:** `outcome.metadata.title or outcome.metadata.extra.get("title") or Path(outcome.vault_path).stem` (keeping the existing `extra["title"]` fallback so nothing regresses). That single change is what carries the title into the `documents.title` column.
- **Embedding context gets it for free — already wired.** All four capture index call sites already pass `mr.ai_title` to `index_embedding` (`capture.py:996, 1046, 1242, 1378`), and `_build_context_text(title, …)` (`retrieval/embeddings.py:24`) already prefixes the embedding string with `title: {title}`. So the *index* side already used the AI title; R1 only fixes the *catalog/card* side (`documents.title`) that the re-ranker reads via `get_by_path`. No index change needed.
- **No new DB migration.** The `documents.title` column already exists (`DocumentRow.title`, `documents.py:30`; written by the INSERT at line 110/267). R1 changes only what value flows into that existing column — confirmed: no DDL, no migration 008.

**Decision 4 — How this resolves A5 (plainly).**

With the new field populated at capture and `_derive_title` preferring it, a binary sibling's `documents.title` becomes the AI's descriptive title (e.g. "Q3 Budget Report") instead of `Path("report.pdf.md").stem` = `report.pdf`. The search card built by the re-ranker (which reads `documents.title` via `get_by_path`) now shows the human title. **P3-SRCH-05's promise ("a usable title, not `report.pdf.md`") is fully met**, not half-met. The fix is at the source (capture), so every future catalog consumer — search cards, the Phase-4 MCP triage, briefings — benefits, not just the search path.

**Decision 5 — Backfill story (explicit).**

- **Existing siblings keep their filename titles.** Notes captured before R1 ships have no `title:` in frontmatter, so on read their `documents.title` stays the filename stem.
- **`--reindex` CANNOT regenerate the AI title.** This is the precise, load-bearing constraint: `--reindex` reads each note from disk via `read_note` and re-runs the *index writers* (`index_embedding` / `index_keywords`) from existing frontmatter — it does **not** call the summarizer/`extract_metadata` LLM stage, so there is no AI title to recover for a note whose frontmatter never had one. Reindex can only re-use what frontmatter already holds; for pre-R1 siblings that is nothing.
- **Therefore backfill needs re-capture** (re-running the full `summarize → metadata → store` pipeline on the original file), not reindex. That is heavier (an LLM call per file) and is out of scope for this fix.
- **Recorded as tech debt** (logged via guardrail-check): pre-R1 siblings show filename titles until re-captured; a future one-off backfill task could re-capture them, but it is not part of R1.

**Decision 6 — Sub-decision (deferred to Open Questions with a recommendation): AI title for ALL captures vs ONLY binary attachment siblings.**

- The defect A5 surfaced is binary-sibling-specific, but the missing wiring (no `extra["title"]` set) affects **every** capture path — a standard `.md` note also gets `documents.title = stem` today unless the rename gate fires. Two scopes:
  - **All captures (Recommended):** set the new field at all three `NoteMetadata` sites. One uniform rule, fixes the latent `.md`-title gap too, and matches "fix at capture" cleanly.
  - **Siblings only:** set it only on `sibling_meta` / `marker_meta`. Narrowest blast radius, touches exactly the A5 case, but leaves the `.md` path inconsistent (a `.md` note's catalog title stays its filename unless renamed) — a second latent surprise for a future reader.
- **Recommendation: all captures.** One trade-off sentence: the wiring is identical at all three sites and the all-captures version removes a second latent title-mismatch rather than leaving it for the next person to rediscover; the cost is three one-line edits instead of two. _Defer the final call to the human at spec-freeze; do not block on it._

### Q5 Diagram — descriptive title for binary attachments, before vs after

```
# Descriptive Title for Binary Attachments — What Changed
Scope: Shows why an attachment's search card showed the file name before,
       and shows the descriptive title now. Covers capture → catalog → card.
       Does NOT cover the search ranking steps (see the search flow diagram).

How to read this:
  Two columns      = before (left) and after (right)
  Boxes            = steps in order, top to bottom
  Arrows           = what happens next
  [REVISED] marker = the one link that changed

        ORIGINAL (before)                       ADJUSTED (after)

  ┌──────────────────────────┐          ┌──────────────────────────┐
  │ At capture, the AI        │          │ At capture, the AI        │
  │ writes a short            │          │ generates a short         │
  │ descriptive title — but   │          │ descriptive title         │
  │ it is NOT saved as a      │          │ AND saves it as a         │
  │ title field              │          │ dedicated title field     │
  └────────────┬─────────────┘          └────────────┬─────────────┘
               │                                      │
               ▼                                      ▼  [REVISED]
  ┌──────────────────────────┐          ┌──────────────────────────┐
  │ The saved note has no     │          │ The saved note now        │
  │ title field for the       │          │ carries the descriptive   │
  │ catalog to read           │          │ title field               │
  └────────────┬─────────────┘          └────────────┬─────────────┘
               │                                      │
               ▼                                      ▼
  ┌──────────────────────────┐          ┌──────────────────────────┐
  │ Catalog has no title to   │          │ Catalog reads the         │
  │ use, so it falls back to  │          │ descriptive title field   │
  │ the file name:            │          │ and stores it as is:      │
  │ "report.pdf"             │          │ "Q3 Budget Report"        │
  └────────────┬─────────────┘          └────────────┬─────────────┘
               │                                      │
               ▼                                      ▼
  ┌──────────────────────────┐          ┌──────────────────────────┐
  │ Search card shows         │          │ Search card shows         │
  │ "report.pdf"             │          │ "Q3 Budget Report"        │
  │ (the file name)           │          │ (a meaningful title)      │
  └──────────────────────────┘          └──────────────────────────┘

Simplified: Only ONE link changed (marked [REVISED]) — the AI's title is now
            saved as a field at capture, so the catalog uses it instead of
            falling back to the file name. Every step downstream is unchanged;
            it simply receives a better title.
```

### New behavior inventory entries

Appended to `docs/system_behavior/behavior_inventory.yaml`, continuing the `P3-SRCH` prefix with `origin: design`:
- **P3-SRCH-10** — a captured binary's sibling note carries an AI-generated descriptive title in its frontmatter, and the catalog/search card shows that title instead of the `report.pdf` filename.

### Open questions (R1)

**OQ-P3B-R1a — AI descriptive title for ALL captures, or ONLY binary attachment siblings?**

The missing-title wiring affects every capture path, but A5 only surfaced it for binary siblings. **Recommendation: all captures** — identical three-line wiring, and it removes the latent `.md`-title gap (a standard note's catalog title is its filename unless the rename gate fires) rather than leaving a second surprise. Cost: three one-line edits vs two. _Defer the final call to the human at spec-freeze._

**OQ-P3B-R1b — Does `extract_metadata.yaml`'s title need tightening?** The prompt already emits a descriptive `title`. Optional, not done here: forbid file extensions / encourage no-slash titles explicitly (it already says "no slashes or colons"). A YAML-only tweak if the spec author wants it (C-07).

### NEW ASSUMPTIONS for research to verify

1. **Frontmatter round-trip safety** — adding `title` to `_KNOWN_KEYS` + a `title` Pydantic `Field` round-trips cleanly: `parse()` reads `title:` back onto the model, `dumps()` writes it when set and omits it when None, with no interaction with `_DEPRECATED_KEYS`.
2. **NoteMetadata field addition vs existing tests** — adding `title: str | None = None` does not break existing `NoteMetadata` / frontmatter tests (no field-count or exact-dict assertions), and is consistent with the Field-vs-property rule.
3. **Prompt-output parsing** — `extract_metadata.yaml` reliably returns a non-empty `title`; `_parse_metadata_json` (`capture.py:234`) surfaces it as `mr.ai_title` for both `.md` and binary inputs (confirm the binary path actually reaches the `metadata` stage and populates `ai_title`).
4. **`documents.title` flow with no migration** — `_derive_title` preferring `metadata.title` carries the value into the existing `documents.title` column with no DDL/migration; confirm both `upsert` and `replace_path` paths.
5. **No regression to the `type=attachment-summary` rule** — adding `title` to `sibling_meta`/`marker_meta` must not disturb `type: attachment-summary` (CLAUDE.md / ADR-0008) — reconcile Stage 4 still recognises these siblings.
6. **Embedding context unaffected** — the index side already receives `mr.ai_title`; R1 must not double-write or alter `index_embedding` call sites (the four sites stay as-is).
