# Phase 3 Session B — Query Path (Hybrid Search) — Spec

_Spec step of the build-pipeline. Source design: `docs/1_design/P3_session_b_query_path.md`. ADR: `docs/architecture/system_adr/0009-phase3-search-rrf-rerank-not-tier-dispatcher.md`. Behavior IDs: `P3-SRCH-01`..`P3-SRCH-09` in `docs/system_behavior/behavior_inventory.yaml` (referenced here, not restated)._

_Voice: non-coder readable by default. Each component leads with a plain-English purpose; code references sit in parentheses or sub-bullets. The spec is logically followable without reading any code._

---

## Purpose

This phase gives the system its first ability to **find the right notes for a question**. You ask with an optional question, an optional project, and an optional date window. The system narrows the note pile by project/date first; then — if you gave a question — it runs two different searches (one matches words, one matches meaning), blends their rankings, and has a small "second-opinion" model re-score the top handful against your exact question. What comes back is a **cheap summary card per note** (title/handle, summary, snippet, score, and a small facts block) — never the full note. If you give no question, it simply lists the matching notes newest-first.

After this phase the system can answer "find me notes about X" through a public function (`search(...)`) and a verification command (`kms search`). The **primary consumer is the Phase 4 MCP AI**, not a human at a terminal — the AI reads the cheap cards, decides which notes matter, then opens only those via the full-note reader. A small, isolated cleanup also ships in the same cycle: the classifier stops accepting a domain name as a valid project destination (TD-051).

---

## Glossary (plain English → code)

| Plain-English name | What it is | Code reference |
|---|---|---|
| Search Coordinator | Public entry point; orchestrates filter → branch → rank → rerank | `src/retrieval/search.py` (new) |
| Candidate Filter | Narrows all notes to those matching project/date | new SQL helper (see OQ-P3B-3 for placement) |
| Hybrid Ranker | Runs word + meaning search over candidates and blends rankings | `src/retrieval/ranker.py` (new) |
| Re-ranker | Small in-process model re-scoring top candidates vs the exact question; attaches summary + facts | `src/retrieval/reranker.py` (new) |
| Word Index | Keyword/full-text store; ranks by word overlap | `notes_fts` FTS5 table (Session A) |
| Meaning Index | Vector store; ranks by semantic similarity | `embeddings_vec` vec0 table (Session A) |
| Note Catalog | Note-metadata table: title, project, dates, summary | `documents` table |
| Reciprocal Rank Fusion (RRF) | Blending rule that merges two ranked lists by rank position, not raw scores | constant `60` (config-sourced) |
| Re-rank model (cross-encoder) | The local CPU "second-opinion" model | `CrossEncoder` (ships inside `sentence-transformers`) |
| Search Result | The cheap card per note (handle + summary + snippet + score + metadata), no body | `SearchResult` dataclass (new) |
| Full-Note Reader | Loads a full note from disk on demand | `vault/reader.py::read_note(path) -> Result[Note]` |
| Search Command | Terminal command that calls search and prints cards | `kms search` in `src/cli/main.py` |
| AI Assistant (future caller) | Phase 4 MCP consumer of `search()` | deferred — not built this phase |

---

## Already built (reuse, do not rebuild)

| Component | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| Cached embed model | `retrieval/embeddings.py::_get_model()` | Lazy-loads + module-caches a `SentenceTransformer` | Hybrid Ranker reuses it to embed the query (keeps query/doc embeddings symmetric) | deep |
| Context builder | `retrieval/embeddings.py::_build_context_text(title, note_type, tags, summary)` | Builds the composite string stored docs were embedded from (`title: … \| type: … \| tags: … \| summary`) | Reference point for OQ-P3B-2 (query-side encoding symmetry) | deep |
| Embedding index write | `retrieval/embeddings.py::index_embedding(vault_path, title, note_type, tags, summary, db_path=None) -> Result[None]` | Best-effort semantic indexing at capture | `--reindex` calls it per note | deep |
| Keyword index write | `retrieval/keyword.py::index_keywords(vault_path, title, summary, body, db_path=None) -> Result[None]` | Best-effort FTS5 indexing at capture | `--reindex` calls it per note | deep |
| Word Index table | `notes_fts` (FTS5) — `vault_path UNINDEXED, title, summary, body`, `tokenize='porter unicode61'` | Keyword/BM25 store (migration `007_search_indexes.sql`) | Hybrid Ranker BM25 query; `snippet()` targets **column index 3 = `body`** | deep |
| Meaning Index table | `embeddings_vec` (vec0) — `vault_path TEXT PRIMARY KEY, embedding FLOAT[384]` (migration `007_search_indexes.sql`) | Vector KNN store | Hybrid Ranker KNN query (see A1 for scoping form) | deep |
| DB connection factory | `storage/db.py::get_connection(db_path=None, *, readonly=False)` | Yields a connection with WAL, `PRAGMA foreign_keys=ON`, and sqlite-vec loaded | Every retrieval query goes through this (C-04) | deep |
| Catalog: single-row read | `storage/documents.py::get_by_path(vault_path, db_path=None) -> Result[DocumentRow \| None]` | Fetch one note's metadata row; `Success(None)` if absent | Re-ranker fetches summary + metadata; `Success(None)` → skip stale row | deep |
| Catalog: all paths | `storage/documents.py::all_paths(db_path=None) -> Result[list[tuple[str, str]]]` | Returns all `(vault_path, content_hash)` pairs | `--reindex` enumerates notes | deep |
| Catalog row shape | `storage/documents.py::DocumentRow` — `vault_path, title, summary, note_type, confidence, created_at, updated_at, updated_by_human, content_hash, batch_id, project, status, key_topics` | The metadata record | Source of the result card's `metadata` block; `created_at`/`updated_at` are TEXT `YYYY-MM-DD HH:MM:SS` | deep |
| Full-Note Reader | `vault/reader.py::read_note(path: Path) -> Result[Note]` | Reads a full note from disk; takes an **absolute** `Path` | Not called by `search()`; documented as the caller's lazy-fetch path | deep |
| Search config | `core/config.py::SearchConfig` — `embedding_model=all-MiniLM-L6-v2`, `reranker_model=cross-encoder/ms-marco-MiniLM-L-6-v2`, `max_candidates=20`, `max_results=10`; exposed at `CONFIG.main.search` | Tunable retrieval knobs (C-06) | RRF feed size, result cap, model names — all read from here | shallow (data holder) |
| Result type | `core/result.py::Success(value)` / `Failure(error, recoverable, context)` | Typed success/error envelope | All retrieval functions return `Result` (C-12 by convention; see A7) | deep |
| CLI async pattern | `src/cli/main.py` — `capture`/`reconcile`/`watch` wrap with `asyncio.run(...)` | Project's fixed Click→async contract | `kms search` follows it (C-10) | deep |
| Classify engine | `pipelines/classify.py::classify(subject, valid_destinations: str, config) -> Result[ClassifyResult]` | Pure AI classify; validates `project`/`primary_domain` against ONE pooled name set (`_destination_names`) | TD-051 splits the pooled set into project-names vs domain-names | deep |
| Destination parser | `pipelines/classify.py::_destination_names(valid_destinations) -> set[str]` | Parses the `format_for_prompt` block into one exact-name set (pools projects + domains — the TD-051 leak) | Replaced/supplemented by two separate sets sourced from the registry | shallow |
| Project registry | `vault/registry.py::ProjectRegistry` — `.all_project_names -> frozenset[str]` (project names), `.get_groups() -> dict[str, ProjectGroup]` (keys = domain names; `Uncategorized` is a group key) | Structured project↔domain map | TD-051 sources `project_names` and `domain_names` from here (exclude `Uncategorized`) | deep |

**Not built (this spec creates):** `retrieval/search.py`, `retrieval/ranker.py`, `retrieval/reranker.py`, the `SearchResult` and `RankedResult` dataclasses, the `kms search` command body, the Candidate-Filter SQL, and the cross-encoder lifecycle. `retrieval/__init__.py` exists but is empty — this spec populates the package's public surface.

---

## Q1 Diagram — what happens inside (from design)

```
# Hybrid Search — What Happens Inside
Scope: Shows what happens when one search request runs.
       Does NOT cover how notes get indexed (capture-time work),
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

## Q2 Diagram — how it connects to others

```
# Hybrid Search — How It Connects
Scope: Shows what the search feature touches in the rest of the system.
       Does NOT show the internal filter→rank→rerank steps (see Q1 for that).

How to read this:
  Center cluster = the search feature being built this phase
  Solid boxes    = components that already exist
  Dashed boxes   = planned, not built yet
  Arrow labels   = what passes between them

        ┌ ─ ─ ─ ─ ─ ─ ─ ─ ┐        ┌──────────────────┐
        │ AI Assistant      │        │ Search Command   │
        │ (future caller)   │        │ Runs a query at  │
        │ Phase 4           │        │ the terminal     │
        └ ─ ─ ─ ┬ ─ ─ ─ ─ ─┘        └────────┬─────────┘
                │ asks for notes              │ asks for notes
                │ (planned)                   │ (built now)
                └──────────────┬──────────────┘
                               ▼
              ╔════════════════════════════════════╗
              ║          SEARCH FEATURE             ║
              ║                                     ║
  ┌───────────╫──┐   ┌─────────────────┐   ┌────────╫──────────┐
  │ Note Catalog │   │ Search          │   │ Word Index        │
  │ Note facts:  │◄──╫─┤ Coordinator    │   │ Ranks by word     │
  │ title, dates,│   ║ │ Runs the flow  │   │ overlap           │
  │ project, sum.│   ║ └───────┬────────┘   └────────▲──────────┘
  └──────▲───────┘   ║         │ candidates          │ word search
         │           ║         ▼ + question           │ (scoped)
         │ summary + ║ ┌────────────────┐   ┌─────────┴──────────┐
         │ metadata  ║ │ Hybrid Ranker  ├──►│ Meaning Index      │
         │           ║ │ Word + meaning,│   │ Ranks by semantic  │
         │           ║ │ blend rankings │◄──┤ similarity         │
         │           ║ └───────┬────────┘   └────────────────────┘
         │           ║         │ blended top candidates
         │           ║         ▼
         │           ║ ┌────────────────┐
         └───────────╫─┤ Re-ranker      │
                     ║ │ Re-scores vs   │
                     ║ │ question; adds │
                     ║ │ summary + facts│
                     ║ └───────┬────────┘
                     ╚═════════╪══════════════════════╝
                               │ result cards
                               │ (no full note body)
                               ▼
                      ┌──────────────────┐
                      │ Full-Note Reader │
                      │ Loads a full note│
                      │ on demand        │
                      └──────────────────┘
```

```
How to read the flow:
  1. A caller (Search Command now; AI Assistant later) asks the Search
     Coordinator for notes.
  2. The Coordinator narrows candidates using the Note Catalog, then hands
     them to the Hybrid Ranker.
  3. The Hybrid Ranker consults the Word Index and Meaning Index (both already
     built), scoped to those candidates, and blends the two rankings.
  4. The Re-ranker re-scores the top few and pulls each note's summary +
     metadata from the Note Catalog.
  5. The Coordinator returns cheap result cards (never the full note body).
  6. The caller decides which few notes matter and only then asks the
     Full-Note Reader to load them.

Simplified: The three inner boxes (Search Coordinator, Hybrid Ranker,
            Re-ranker) are the feature being built and live inside the
            double-walled cluster. Note Catalog, Word Index, Meaning Index,
            and Full-Note Reader already exist. AI Assistant is dashed — it
            is the planned Phase 4 caller.
```

---

## Feature overview

**Happy path (question given).** A caller asks `search("stakeholder resistance", project="Alpha")`. The Search Coordinator first asks the Note Catalog for the set of notes that belong to Alpha (and, if a date window were given, that fall inside it) — this is the **candidate set**. It hands the candidate set plus the question to the Hybrid Ranker. The Hybrid Ranker runs two searches **scoped to those candidates only**: a keyword/BM25 search on the Word Index, and a meaning/KNN search on the Meaning Index (the question is encoded with the same cached model that produced the stored vectors). Each search yields a ranked list; the Hybrid Ranker blends them with Reciprocal Rank Fusion — `score = 1/(60 + word_rank) + 1/(60 + meaning_rank)` — and returns the top `max_candidates` as `RankedResult`s (each carrying a `vault_path`, an `rrf_score`, and a `snippet` taken from the note body). The Re-ranker then loads the cross-encoder model, scores each `(question, snippet)` pair, re-orders by that score, and for each survivor pulls the note's `summary` + `metadata` from the Note Catalog. The Coordinator caps the list at `max_results` and returns `SearchResult` cards. The caller reads the cheap cards and, only for the few it judges relevant, calls the Full-Note Reader.

**Happy path (no question).** A caller asks `search(project="Alpha")` or `search(date_range=last_week)`. The Coordinator builds the candidate set the same way, then takes the **filter-only** branch: it sorts candidates by `updated_at` descending, caps at `max_results`, and returns cards. The Hybrid Ranker and Re-ranker are skipped entirely (no model is loaded, no cross-encoder runs).

**Edge cases.**
- **No filter at all** (`search("budget")` with no project/date): candidates = all notes. Do **not** build a giant `IN (...)` list — omit the candidate clause and let the word/meaning searches run globally (avoids the SQLite variable ceiling and is faster). (A4)
- **Filter matched nothing**: return `Success([])` without touching the indexes. (A4)
- **Stale index row** (an index entry exists but the Note Catalog row was deleted out of band): the Re-ranker's `get_by_path` returns `Success(None)` → skip that candidate, do not crash. (P3-SRCH-06, A6)
- **Empty-summary note**: was indexed in Session A with a metadata-only embedding; it still ranks via keyword (body) and metadata-only meaning. No special handling — it simply competes.
- **Binary sibling note**: its title in the Note Catalog is the human title (e.g. "Q3 Budget Report"), not `report.pdf.md`; the card must show that title. (P3-SRCH-05, A5)

**Reindex.** `kms search --reindex` enumerates every note via `all_paths()`, reads each via the Full-Note Reader, and calls `index_embedding(...)` + `index_keywords(...)` (the real Session A writers, both idempotent DELETE-then-INSERT). It reports a processed count. Running it twice yields identical search results (P3-SRCH-07).

**Behavior IDs** for the above are defined in the inventory as `P3-SRCH-01`..`P3-SRCH-09` — referenced, not restated here.

---

## Out of scope

- **MCP `kms_search` tool** — Phase 4, after this CLI/pipeline is built and tested (C-15/C-16). The CLI is the verification proxy for what the tool will later expose.
- **Worker-thread offload of the CPU-bound search** — deferred. Session B runs sync search directly inside the async CLI wrapper; revisit when the Phase 4 daemon is designed. (OQ-P3B-1)
- **Tier dispatcher / `max_cost` budget / hot-warm-cold escalation** — explicitly dead per ADR-0009. Replaced by the cheap-card + lazy-fetch model. A real cost ceiling, if ever needed, is a new feature.
- **Full-content terminal dump** — not built. The card carries no body; the AI consumer uses `read_note`, the human reads in Obsidian.
- **Query-time LLM/Ollama embedding or ranking** — out of scope; all inference is in-process `sentence-transformers` / `CrossEncoder`.
- **TD-010 (Ollama httpx async rewrite)** — out of scope this cycle. Search never calls Ollama. Kept only as a conditional post-ship check on the capture/classify path (rewrite only if measured >200ms/call). Do not rewrite speculatively.
- **Scheduling / automation** — Phase 4+.
- **New audit entries for search** — none. Search makes no AI decision (no `provider.complete`, no confidence gate); it is read-only retrieval (C-13 N/A).

---

## Constraints

Non-negotiable rules the build must respect. Sourced from the design's Guardrail Checklist (`/guardrail-check Review`, re-run this spec step) and `CONSTRAINTS.md`.

- **C-04 · PRAGMA foreign_keys=ON on every connection** — all retrieval DB access goes through `storage/db.py::get_connection` (sets the pragma + loads sqlite-vec). No raw `sqlite3.connect()` in `retrieval/`. Source: CONSTRAINTS.md; hook.
- **C-05 · Schema changes via versioned .sql deltas** — N/A: Session B is read-only against tables shipped in migration `007_search_indexes.sql`. No new DDL.
- **C-06 · Confidence thresholds in config, never in code** — the RRF constant (60) and `max_candidates`/`max_results` read from `CONFIG.main.search`; they live in `retrieval/` (not `pipelines/`). TD-051 introduces only set-membership checks in `pipelines/classify.py` — no float-literal `if/elif`. Source: CONSTRAINTS.md; hook.
- **C-07 · Prompts as YAML, never inline f-strings** — search builds no prompts; `CrossEncoder.predict` takes `(query, snippet)` pairs, not a rendered prompt. Source: CONSTRAINTS.md.
- **C-08 · Pipelines use get_provider() factory** — documented divergence: `SentenceTransformer`/`CrossEncoder` are in-process inference, not chat-completion providers; the factory is for `llm/` providers only. Search never calls `get_provider`/Ollama. Source: CONSTRAINTS.md.
- **C-10 · CLI wraps async pipelines with asyncio.run()** — `kms search` wraps with `asyncio.run`; sync CPU work runs inside the wrapper coroutine (OQ-P3B-1). Source: CONSTRAINTS.md.
- **C-11 · load_dotenv only in cli/main.py** — `retrieval/` modules must not call `load_dotenv`. Source: CONSTRAINTS.md.
- **C-12 · Public functions return Result** — by extension/convention: `retrieval/` is new and not named by the rule, but `search`/`rank`/`rerank` return `Result` for consistency with Session A (see A7). TD-051 keeps `classify()` returning `Result`. Source: CONSTRAINTS.md.
- **C-13 · Audit log for every AI decision** — N/A: search makes no AI decision; no audit entry. Source: CONSTRAINTS.md.
- **C-14 · mcp_server/tools.py logic-free** — N/A: no MCP tool in Session B. Source: CONSTRAINTS.md; hook.
- **C-15 · No MCP tool before its pipeline exists + tested** — satisfied: MCP `kms_search` deferred to Phase 4. Source: CONSTRAINTS.md.
- **C-16 · Schedulers come last** — satisfied: no scheduler; the CLI is the manual verification surface. Source: CONSTRAINTS.md.
- **C-17 · Never import CONFIG at module scope in tests** — the TD-051 test rewrite must keep CONFIG lazy / use the existing `MagicMock` config stub (`_make_config()`), not a module-scope `from core.config import CONFIG`. Source: CONSTRAINTS.md; hook.
- **Zero-logic CLI (C-14 spirit / TD-012)** — `kms search` parses options, calls `retrieval/search.py`, and prints cards. No filter/rank/branch logic in the command body. Source: design Guardrail Checklist; CLAUDE.md.

---

## Assumptions

Claims about existing code or runtime this spec depends on. **Research verifies each — none is pre-validated.** R-IDs and OQ-IDs trace back to the design doc. Load-bearing rows are flagged.

| ID | Assumption | Source | What would prove it wrong |
|----|-----------|--------|---------------------------|
| **A1 (R1) — LOAD-BEARING** | The Meaning Index supports filtered KNN via `embedding MATCH ? AND k = ? AND vault_path IN (...)`, returning real (non-NULL) distances scoped to the candidate set, applying the `IN` set as a partition *before* choosing the k nearest. The no-`MATCH` form (`WHERE vault_path IN (...) ORDER BY distance`) returns NULL distances and must NOT be used. | design Implications (KNN scoping); ADR-0009; R1 | Re-running the probe against the project's real DB on the pinned `sqlite-vec` version returns NULL distances, errors on the combined clause, or returns a near-but-out-of-set candidate / drops a far-but-in-set candidate. |
| **A2 (R2 / OQ-P3B-2) — LOAD-BEARING** | Embedding the **bare** query string (no `_build_context_text` wrapper) yields good-enough first-stage recall, because stored doc vectors come from a composite context string but the re-ranker corrects asymmetry downstream. | design Implications (query symmetry); R2; OQ-P3B-2 | A semantic query that should match a known note (P3-SRCH-01) fails to surface it in the candidate set, and wrapping the query in the stored template materially improves recall. |
| **A3 (R3) — LOAD-BEARING** | `documents.created_at`/`updated_at` are TEXT in fixed-width `YYYY-MM-DD HH:MM:SS` (space separator, UTC, no `T`, no timezone) written by `datetime('now')`, so lexicographic `WHERE updated_at >= ?` is chronological — provided the `--since` bound is formatted identically. | design Implications (dates as text); R3 | A captured row's `updated_at` carries a `T` separator/timezone, or a `--since` bound formatted as `datetime('now')` under/over-selects against real rows. |
| A4 (R4) | "No filter" → candidates = all paths, and the ranker must **omit** the `IN (...)` clause (global KNN/BM25) rather than build a giant list (SQLite default variable limit 32766; a 1500-element `IN` works but all-paths could exceed it). "Filter matched nothing" → return `Success([])` without touching indexes. | design Implications (degenerate case); R4; OQ-P3B-... | A global (no-`IN`) KNN/BM25 query errors or returns nothing, or an all-paths `IN` list is required and does not blow up. |
| ~~A5 (R5)~~ **RESOLVED-BY-R1** | _Original (INVALIDATED by research):_ a binary's sibling `documents.title` already holds the human title. Research proved the opposite — it is `Path("report.pdf.md").stem` = `report.pdf`. **Resolution mechanism (Component 0, root-cause fix at capture, LOCKED by user):** add a first-class `title` field to `NoteMetadata`, set `title = mr.ai_title` at all three capture build sites, and teach `_derive_title` to prefer it — so `documents.title` carries the AI's descriptive title for every future capture. **No card-builder workaround; no prompt change; no migration.** Components 1–6 (Re-ranker etc.) are unchanged: they keep reading the human title from `documents.title` via `get_by_path` — that value is simply correct now. | design Revision R1 (Decisions 1–6); research A5-Invalidated | **Research must re-confirm** the resolution holds: (a) `mr.ai_title` is non-empty for the binary path, (b) `_derive_title` returns the AI title (not the stem) for a sibling outcome carrying `metadata.title`, (c) the round-trip + reconcile-Stage-4 + no-double-write checks below (A14–A19). |
| A6 (decision #5) | A stale index row (note deleted out of band) is detected by `get_by_path` returning `Success(None)`; the Re-ranker skips that candidate without raising. | design Implications (stale rows); P3-SRCH-06; grill #5 | `get_by_path` raises or returns a truthy row for a deleted note, or the KNN/BM25 query itself errors when an index row has no Catalog row. |
| A7 (R7) | Applying the `Result` contract to `retrieval/` is convention (the C-12 hook names only `handlers/`/`pipelines/`), and Session A already returns `Result` from its retrieval functions — so `search`/`rank`/`rerank` returning `Result` matches existing style. | design Implications (C-12 scope); R7 | Session A retrieval functions do not return `Result`, or a hook actually enforces `Result` on `retrieval/` (changing it from convention to hard rule). |
| A8 (R6) | The intended dataclass shapes — `RankedResult(vault_path, rrf_score, snippet)` and `SearchResult(vault_path, summary, snippet, score, metadata)` — and the intended signatures `rank(query, candidate_paths, max_candidates)` / `rerank(query, candidates)` / `search(query=None, project=None, date_range=None, max_results=None, db_path=None)` are buildable as written. | design build targets; R6 | A field is unavailable from the verified interfaces (e.g. no usable `snippet` source), or a signature collides with an existing name in `retrieval/`. |
| A9 (verified, re-confirm) | `notes_fts` has columns `vault_path UNINDEXED, title, summary, body`; `body` is **column index 3**, so `snippet(notes_fts, 3, ...)` targets the body. BM25 ordering is `ORDER BY bm25(notes_fts)` ascending (lower = more relevant). | design Implications (FTS columns); migration 007 | The migration's column order differs, or `snippet(notes_fts, 3, ...)` returns a non-body field. |
| A10 (verified, re-confirm) | The cross-encoder ships inside `sentence-transformers` (already a dependency); `CrossEncoder(CONFIG.main.search.reranker_model)` loads a local CPU model with a `.predict(pairs)` method; the instance can be cached at module level like `_get_model()`. No new package, no API call. | design Implications (second-opinion model) | `CrossEncoder` is not importable from `sentence-transformers`, or `reranker_model` cannot be loaded offline. |
| A11 (R5 / OQ-P3B-3) | The Note Catalog can be filtered by project and/or date with a new query, using `get_connection(readonly=True)`. No such filter function exists today (`documents.py` has only single-path and all-path readers). | design Implications (Candidate Filter is new); OQ-P3B-3 | A `documents` filter-by-project/date function already exists and should be reused, or `get_connection(readonly=True)` cannot run the filter query. |
| A12 (TD-051) | `ProjectRegistry.all_project_names` is a frozenset of project names and `get_groups()` keys are domain names (with `Uncategorized` present and excludable); the capture call sites already build the registry, so two sets can be passed to `classify()` without re-parsing the prompt string. | design Implications (TD-051); registry code | `all_project_names`/`get_groups()` do not return those shapes, or the call sites do not have the registry in scope. |
| A13 (C-10 / OQ-P3B-1) | `search()`/`rank()`/`rerank()` are synchronous; `.encode()`/`.predict()` are CPU-bound and block the event loop for the call duration. Running sync search directly inside the `asyncio.run` wrapper coroutine is acceptable for the single-shot CLI (no concurrency). | design Implications (sync CPU in async CLI); OQ-P3B-1 | A second concurrent caller exists in Session B (it does not), making event-loop blocking observable. |

**Component 0 (Descriptive Title at Capture / Revision R1) assumptions — research re-confirms each.** These back the A5 resolution. A16 and A18 are the riskiest (load-bearing).

| ID | Assumption | Source | What would prove it wrong |
|----|-----------|--------|---------------------------|
| A14 (R1 Assumption 1) | Adding `title` to `_KNOWN_KEYS` plus a `title: str \| None = None` Pydantic `Field` round-trips cleanly: `parse()` reads `title:` back onto the model, `dumps()` writes it when set and omits it when `None`, with **no interaction with `_DEPRECATED_KEYS`** (which holds only `"domain"`). | design R1 Decision 1; R1 Assumption 1; `frontmatter.py:27,52,134,160` | A populated `title` is dropped or mangled on a `dumps()`→`parse()` round-trip, or `_DEPRECATED_KEYS` strips it, or `dumps()` emits a `title:` key when the field is `None`. |
| A15 (R1 Assumption 2) | Adding `title: str \| None = None` to `NoteMetadata` does not break existing `NoteMetadata` / frontmatter tests — no test asserts a fixed field count or an exact-dict equality on the model or on serialized frontmatter; and the addition is consistent with the Field-vs-property rule. | design R1 Decision 1; R1 Assumption 2 | A frontmatter or `NoteMetadata` test asserts an exact dict / field count / `model_dump` shape that a new field changes, turning the addition into a test break. |
| **A16 (R1 Assumption 3) — LOAD-BEARING** | `prompts/extract_metadata.yaml` reliably returns a **non-empty** `title`, and `_parse_metadata_json` (`capture.py:234`) surfaces it as `mr.ai_title` for **both** `.md` and **binary** inputs — i.e. the binary path actually reaches the `metadata` stage and populates `ai_title`, so `title = mr.ai_title` is a real human title there, not empty. | design R1 Decision 2/3; R1 Assumption 3 | The binary capture path does not reach the `metadata` stage (so `ai_title` is empty/None for binaries), or the prompt can return an empty/missing `title` in practice — in which case the sibling card silently falls back to the filename stem and A5 is not actually fixed. |
| A17 (R1 Assumption 4) | `_derive_title` preferring `metadata.title` carries the value into the **existing** `documents.title` column with no DDL/migration, on **both** the `upsert` and the `replace_path` write paths (both call `_derive_title`). | design R1 Decision 3/4; R1 Assumption 4; `documents.py:69` | `replace_path` (or `upsert`) does not route its title through `_derive_title`, so one write path keeps storing the stem; or the `documents.title` column does not already exist and a migration is required. |
| **A18 (R1 Assumption 5) — LOAD-BEARING** | Adding `title=` to the `sibling_meta` (`capture.py:1176`) and `marker_meta` (`capture.py:1330`) builders does **not** disturb their `type: attachment-summary` frontmatter — so reconcile Stage 4 (CLAUDE.md / ADR-0008) still recognises these siblings and does not silently skip them. | design R1 Decision 3; R1 Assumption 5; ADR-0008; CLAUDE.md "Any code writing into `.summaries/` MUST set `type=attachment-summary`" | After the edit a sibling/marker note loses or mutates `type: attachment-summary`, or reconcile Stage 4 stops recognising it (siblings silently skipped). |
| A19 (R1 Assumption 6) | The embedding/index side is unaffected: the four capture index call sites (`capture.py:996,1046,1242,1378`) already pass `mr.ai_title` to `index_embedding`; R1 must **not** alter them and must not add a second title write — no double-indexing. | design R1 Decision 3; R1 Assumption 6 | A title write is added inside an index call site (double-write), or the index sites' existing `mr.ai_title` argument is changed by the R1 edits. |

---

## Component dependency order

_Documents what must exist before each component works — not the order code is written. Execution order is owned by `/plan-from-specs`._

### 0. Descriptive Title at Capture (Session B prerequisite — A5 root-cause fix; independent of the search components)

_Added by Revision R1 (`docs/1_design/P3_session_b_query_path.md` → "## Revision R1"). This component resolves the only invalidated assumption from research (A5). It is a **prerequisite** to the search components only in spirit (it is what makes P3-SRCH-05's "usable title" promise true); mechanically it touches a different part of the codebase (capture + catalog) and can be built and tested in complete isolation from Components 1–6. **The user LOCKED a root-cause fix at capture; the AI prompt is NOT edited (the capture prompt already generates a descriptive title); the descriptive title is applied to ALL captures, not siblings-only.**_

**Goal.** When the system captures any file, give the note a real place to keep the short descriptive name the AI already writes for it ("Q3 Budget Report"), so the Note Catalog — and every search card — shows that human title instead of falling back to the raw file name ("report.pdf"). One link in the chain changes; everything downstream simply receives a better title.

**Build.** Four small, mechanical edits across two files; **no new prompt, no new migration**:

- **A new first-class frontmatter field on the note model** — add `title: str | None = None` to `NoteMetadata` (`vault/frontmatter.py`, alongside the other typed fields at lines 60–77) and add the string `"title"` to `_KNOWN_KEYS` (`frontmatter.py:27`). This is a `Field`, not a `@property`, because the title is a value the capture stage *supplies*, not one computed from other fields (CLAUDE.md Field-vs-property rule). Round-trip is automatic: `parse()` (line 134) routes any `_KNOWN_KEYS` member onto the typed model, and `dumps()` (line 160) serialises every non-None Field via `model_dump(exclude_none=True, ...)` — so a populated `title` is written to disk and an empty one is omitted. **It does NOT interact with `_DEPRECATED_KEYS`** (line 52 holds only `"domain"`); `title` is brand-new and additive, so `dumps()` will not strip it.

- **Carry the AI's existing descriptive title onto the note at ALL THREE capture build sites** — set `title = mr.ai_title or None` on the `NoteMetadata` constructed at each of:
  - `store()` — the standard `.md` path (`pipelines/capture.py:889`).
  - `_store_nonmd` LOCATED sibling — `sibling_meta` (`capture.py:1176`); this is the binary case A5 is about.
  - `_store_nonmd` CLUELESS marker — `marker_meta` (`capture.py:1330`); the inbox needs-review case.
  - `mr.ai_title` is already produced today by `prompts/extract_metadata.yaml` (its first output field) and surfaced on `MetadataResult.ai_title` for **both** `.md` notes and binaries (a binary's extracted text flows through the same `summarize → metadata` stages). Using `or None` lets an empty AI title fall through to the existing filename-stem behaviour, so nothing regresses. Both sibling sites already carry `type="attachment-summary"` (verified) — adding `title=` alongside it leaves that field untouched, so ADR-0008 / reconcile Stage 4 still recognises these siblings (A18).

- **Teach the catalog's title-deriver to prefer the new field** — change `storage/documents.py::_derive_title` (line 69) from `outcome.metadata.extra.get("title") or Path(outcome.vault_path).stem` to prefer the typed field first: `outcome.metadata.title or outcome.metadata.extra.get("title") or Path(outcome.vault_path).stem`. Keeping the existing `extra["title"]` fallback means nothing regresses. This single change is what carries the descriptive title into the `documents.title` column, for both the `upsert` and the `replace_path` write paths that call `_derive_title` (A17).

- **No prompt change (LOCKED).** `extract_metadata.yaml` already emits a descriptive `title`; no YAML is edited. (Any optional title-instruction tightening is deferred — see Open Questions OQ-P3B-R1b.)

- **No DB migration (LOCKED).** The `documents.title` column already exists (`DocumentRow.title`; written by the existing INSERT). R1 only changes *what value* flows into that existing column — no DDL, no migration 008.

- **No embedding double-write.** The four capture index call sites (`capture.py:996, 1046, 1242, 1378`) already pass `mr.ai_title` to `index_embedding`, and `_build_context_text` already prefixes the embedding string with `title: …`. The **index** side already used the AI title; R1 fixes only the **catalog/card** side that the Re-ranker reads via `get_by_path`. Those four index sites stay exactly as-is — do not add a second title write there (A19).

**Depends on.** None — fully independent of Components 1–6 and of the search work. Touches only `vault/frontmatter.py`, `pipelines/capture.py`, and `storage/documents.py`. **Cut cleanly if research finds it bigger than this paragraph** (the same posture as TD-051).

**Assumes.** A5 (now resolved-by-R1 — re-confirm), A14, A15, A16, A17, A18, A19.

**Interface shape.** Adds one optional field to an existing data model (`NoteMetadata.title`) and changes one private helper (`_derive_title`); no new module boundary, no new public function. Callers that already build `NoteMetadata` gain one optional keyword.

**Dependency category.** in-process (test directly: build a `NoteMetadata` with a title, round-trip it through `dumps`/`parse`, and assert `_derive_title` returns the title; capture-level test asserts a captured binary's `documents.title` is the AI title).

**Result/error posture.** No new failure surface. `_derive_title` stays total (always returns a string — title, then `extra["title"]`, then stem). `parse()`/`dumps()` keep their existing `Result`/exception posture; the new optional field cannot make a previously-valid note fail validation (it defaults to `None`). Consistent with the codebase (C-12 by convention).

**Decisions.**
- Q: Field name — `title`, `display_title`, or `ai_title`? Leaning **`title`** (the human-obvious key); confirm at research it collides with no reader's expectations (no existing `title:` semantics in frontmatter). (Design Decision 1.)
- Q: Scope — all captures vs siblings-only? **RESOLVED (locked): ALL captures** (all three build sites). Recorded here so the planner does not re-open it. (Design Decision 6 / OQ-P3B-R1a — locked by the user.)

**Done when.** A captured binary's sibling note carries the AI's descriptive title in its frontmatter (`title: Q3 Budget Report`), and a search card for that note shows "Q3 Budget Report" instead of "report.pdf" (P3-SRCH-05, P3-SRCH-10). A captured `.md` note's catalog title is its descriptive title, not its filename stem. Re-reading any captured note round-trips the `title` field unchanged. The four embedding index sites are unchanged and a note is not double-indexed. Existing frontmatter/`NoteMetadata` tests still pass after updating `test_parse_minimal_note` (which asserts `title` lives in `extra` — the R1 change reroutes it to the typed field, so the test must assert `meta.title == "T"` and `meta.extra == {}` instead; A15 mechanical fix). The `type: attachment-summary` siblings are still recognised by reconcile Stage 4.

---

### 1. Candidate Filter

**Goal.** Turn the optional project + date window into the set of note handles that are eligible for ranking — or "all notes" when nothing is given.

**Build.** A read-only query against the Note Catalog (`documents`) returning candidate `vault_path`s. When `project` is given, filter on the `project` column; when a date window is given, filter on `updated_at >= <lower-bound>` (and optionally an upper bound), with the bound formatted exactly like `datetime('now')` (`YYYY-MM-DD HH:MM:SS`). When neither is given, signal "all notes" — the downstream ranker must then run globally without an `IN (...)` clause (A4). Placement of this query (helper in `documents.py` vs inline in `search.py`) is OQ-P3B-3.

**Depends on.** None (reads existing `documents`).

**Assumes.** A3, A4, A11.

**Interface shape.** Intended (research confirms): a small reader returning `Result[list[str]]` of candidate `vault_path`s, e.g. `filter_paths(project=None, since=None, db_path=None)`. Uses `get_connection(readonly=True)`.

**Dependency category.** in-process (test directly with a temp DB).

**Decisions.**
- Q: Where does this query live? Options: helper in `documents.py` / inline in `search.py`. Leaning **helper in `documents.py`** because every other `documents` query lives there and Phase 8/9 Synthesis can reuse it (OQ-P3B-3).
- Q: Does `--since` need an upper bound, or just a lower bound? Leaning lower-bound-only for Session B (recent-notes use case); confirm at research.

**Done when.** Asking for a project with no question returns exactly that project's notes and nothing from other projects (P3-SRCH-02/03). Asking with a date window returns only notes whose `updated_at` falls inside it (P3-SRCH-08). Asking with neither returns a signal that means "all notes" without building a per-path list.

---

### 2. Hybrid Ranker (`src/retrieval/ranker.py`)

**Goal.** Given a question and a candidate set, find the best-matching notes by combining a word search and a meaning search into one blended ranking.

**Build.** A new module exposing (intended; research confirms) `rank(query, candidate_paths, max_candidates) -> Result[list[RankedResult]]` with `RankedResult(vault_path, rrf_score, snippet)`. It runs:
- **Word search:** FTS5 BM25 over `notes_fts` scoped to candidates via `AND vault_path IN (...)` on the `UNINDEXED` column (omit the `IN` clause when candidates = all — A4); order by `bm25(notes_fts)` ascending; pull the `snippet` from the **body column (index 3)**.
- **Meaning search:** encode the query via the cached `_get_model()` from `retrieval/embeddings.py`, then KNN over `embeddings_vec` using `embedding MATCH ? AND k = ? AND vault_path IN (...)` (A1) — omit the `IN` clause when candidates = all.
- **Blend:** Reciprocal Rank Fusion, `score = 1/(60 + word_rank) + 1/(60 + meaning_rank)`. Never normalize-and-add raw scores. The `60` and `max_candidates` come from config/parameters, never hardcoded as routing literals (C-06 spirit; though note the literal `60` is a defined RRF constant, not a confidence threshold — keep it config-sourced or named).
- Return the top `max_candidates`. `Success`/`Failure`, never raise (A7/C-12).

**Depends on.** Candidate Filter (consumes its `vault_path`s); Meaning Index + Word Index (Session A); cached embed model (Session A).

**Assumes.** A1, A2, A4, A8, A9.

**Interface shape.** Caller (`search.py`) sees `rank(...)`; hidden behind it: dual-index SQL, query encoding, and RRF math. Single caller today (search.py) — justified as a depth/readability boundary, not a speculative seam.

**Dependency category.** in-process (test directly with a temp DB seeded via the Session A index writers).

**Decisions.**
- Q: Is `60` sourced from config or a named module constant? Options: add a config key / module-level named constant. Leaning **named module constant in `retrieval/`** (it is a standard RRF parameter, not a tunable routing threshold) — confirm this does not trip the C-06 hook (the hook targets `pipelines/`, not `retrieval/`).
- Q: How many results does each inner search fetch before fusion (the per-index `k`)? Leaning `max_candidates` (or a small multiple) — research/plan decides.

**Done when.** A question finds a semantically related note despite different wording (P3-SRCH-01), and the blended order reflects both keyword and meaning signals rather than either alone. A far-but-in-candidate-set note can still be returned and a near-but-out-of-set note is excluded (the A1 partition test).

---

### 3. Re-ranker (`src/retrieval/reranker.py`)

**Goal.** Take the blended top candidates, have a small local "second-opinion" model re-score them against the exact question, and build the final cheap result cards.

**Build.** A new module exposing (intended; research confirms) `rerank(query, candidates) -> Result[list[SearchResult]]` with `SearchResult(vault_path, summary, snippet, score, metadata)`. It:
- Loads the cross-encoder from `CONFIG.main.search.reranker_model`; caches the instance at module level (mirror `_get_model()` in `embeddings.py`).
- Scores each `(query, candidate.snippet)` pair via `CrossEncoder.predict`.
- For each candidate, fetches `summary` + `metadata` (project, note_type, updated_at, tags/key_topics) from `documents.get_by_path`. A `Success(None)` (stale row) → skip that candidate (A6, P3-SRCH-06).
- Orders by cross-encoder score descending. The card carries the human **title** in `metadata` (A5, P3-SRCH-05) and **no body** (full content via `read_note`).
- `Success`/`Failure`, never raise (A7/C-12).

**Depends on.** Hybrid Ranker (consumes `RankedResult`s); Note Catalog (`get_by_path`); cross-encoder (ships in `sentence-transformers`).

**Assumes.** A5, A6, A8, A10.

**Interface shape.** Caller sees `rerank(...)`; hidden behind it: model lifecycle (lazy load + cache), scoring, stale-row skipping, and metadata assembly. Single caller today — depth/readability boundary.

**Dependency category.** local-substitutable (the cross-encoder is in-process but heavy; downstream planner may inject a stand-in scorer for fast tests).

**Decisions.**
- Q: What exactly goes in `metadata`? Leaning `{project, note_type, updated_at, key_topics/tags, title}` (the AI triages on this — load-bearing). Confirm field set at research/plan.
- Q: Is `score` the cross-encoder score or a fused score? Leaning the **cross-encoder score** (final relevance), with `rrf_score` discarded after ordering — confirm.

**Done when.** Every returned card has handle + summary + snippet + score + metadata and never the full body (P3-SRCH-04). A binary sibling's card shows a usable title, not `report.pdf.md` (P3-SRCH-05). A deleted-note index row is skipped, not fatal (P3-SRCH-06).

---

### 4. Search Coordinator (`src/retrieval/search.py`)

**Goal.** The single public entry point that runs the whole flow and is the contract the Phase 4 MCP AI will consume.

**Build.** A new module exposing `search(query=None, project=None, date_range=None, max_results=None, db_path=None) -> Result[list[SearchResult]]`:
- Run the Candidate Filter (project and/or date_range → candidate `vault_path`s; neither → all).
- **Filter-only branch** (`query is None`): sort candidates by `updated_at` descending, cap at `max_results`, build cards (summary + metadata from the Catalog), return. Skip the Hybrid Ranker and Re-ranker entirely (no model load).
- **Query branch** (`query` given): candidates + query → Hybrid Ranker → Re-ranker → cap at `max_results` → return cards.
- Defaults: `max_results` / `max_candidates` from `CONFIG.main.search` when not passed.
- Empty candidate set → `Success([])` without touching indexes (A4).
- `Success`/`Failure`, never raise (A7/C-12).

**Depends on.** Candidate Filter, Hybrid Ranker, Re-ranker.

**Assumes.** A3, A4, A6, A8, A13.

**Interface shape.** The real seam (2+ adapters: Search Command in Session B, MCP tool in Phase 4). Callers see `search(...)`; hidden behind it: the filter → branch → rank → rerank orchestration. Two real adapters → not speculative.

**Dependency category.** in-process (test directly with a temp DB).

**Decisions.**
- Q: What concrete type is `date_range`? Options: `tuple[datetime, datetime]` / `tuple[str|None, str|None]` / a small dataclass. Leaning a `(lower, upper)` tuple of `datetime` (CLI converts `--since` to it); the Filter formats it to the `datetime('now')` string shape (A3). Confirm at research.
- Q: Does the filter-only branch also need a Re-ranker-style card builder, or a shared card helper? Leaning a **shared card-from-DocumentRow helper** so both branches build identical cards. Confirm at plan.

**Done when.** `search("...")` returns ranked cards; `search(project="Alpha")` returns Alpha notes newest-first with no ranking; `search(date_range=last_week)` returns recent notes with no query (P3-SRCH-08); all branches return `Result` and never raise on a stale row.

---

### 5. Search Command (`kms search` in `src/cli/main.py`)

**Goal.** The terminal command that exercises `search()` end-to-end — the C-15/C-16 verification proxy for the future MCP tool (not the end-user reading surface; humans read in Obsidian).

**Build.** Replace the `raise NotImplementedError` stub with a Click command supporting:
- `kms search "<query>"`, `--project <Name>`, `"<query>" --project <Name>`, `--since 7d|30d|YYYY-MM-DD`, `--max N`, `--reindex`.
- `--since` parsing converts `7d`/`30d`/`YYYY-MM-DD` into a date-range lower bound formatted as the `datetime('now')` string shape (A3). This parsing is the one piece of presentation logic the CLI owns; the search logic itself stays in `retrieval/search.py` (zero-logic-CLI / C-14 spirit / TD-012).
- Output per result: the real note **title** (from the Catalog — never the raw filename; sibling files are `report.pdf.md`), score, and snippet — one block per result.
- `--reindex`: `documents.all_paths()` → per path `read_note()` → `index_embedding(...)` + `index_keywords(...)` (the **real** Session A names) → report a processed count. Idempotent — the batch self-heal (P3-SRCH-07).
- Wrap with `asyncio.run(...)` (C-10); sync search runs inside the wrapper coroutine (A13/OQ-P3B-1). Closes TD-012 (the search stub).

**Depends on.** Search Coordinator (for query/filter modes); `all_paths` + `read_note` + the Session A index writers (for `--reindex`).

**Assumes.** A3, A5, A13.

**Interface shape.** Modifies the existing Click group; no new module boundary. The command body is glue: parse options → `asyncio.run(search(...))` (or the reindex loop) → print.

**Dependency category.** in-process.

**Decisions.**
- Q: Does `--reindex` accept a query/print results in the same run, or is it a standalone maintenance invocation? Leaning **standalone** (reindex then report count, no search) for simplicity — confirm at plan.
- Q: How are `--since` parse errors surfaced? Leaning a clear CLI error message (not a stack trace) — the command catches the `Failure` and prints it.

**Done when.** `kms search "stakeholder resistance"` surfaces a "managing pushback" note (P3-SRCH-01); `kms search --project Alpha` lists Alpha notes newest-first (P3-SRCH-02); `kms search "budget Q3" --project Alpha` searches semantically within Alpha (P3-SRCH-03); `kms search --since 7d` returns recent notes with no query (P3-SRCH-08); `kms search --reindex` rebuilds both indexes and a repeat run gives identical results (P3-SRCH-07); sibling results show a usable title (P3-SRCH-05).

---

### 6. TD-051 — classify cross-type destination validation split (ISOLATED final component)

**Goal.** Stop the classifier from accepting a domain name as a valid project destination (and vice versa). Today both are pooled into one name set, so a mislabeled response can route to a wrong-kind destination.

**Build.** In `pipelines/classify.py`:
- Replace the single pooled `_destination_names(valid_destinations)` membership check with **two** sets: project names and domain names. Validate `project` against the project-name set, and `primary_domain` against the domain-name set.
- Source the two sets from the structured registry, not the prompt string: `ProjectRegistry.all_project_names` (project names) and `get_groups()` keys (domain names; exclude `Uncategorized`). The capture call sites already build the registry, so they pass the two sets into `classify()` — adjust `classify()`'s signature/params accordingly (research confirms the cleanest shape: e.g. pass `project_names` and `domain_names` sets, or pass the registry).
- In `tests/test_pipelines/test_classify.py`: rewrite the `VALID_DESTINATIONS` fixture (currently the wrong shape `"Projects:\n  - Alpha\nDomains:\n  - Finance"`, line ~126) to the real `format_for_prompt` shape (domain names as headers, project names as items) plus the new split-set inputs. ~6 tests touched. Keep CONFIG lazy / use the existing `MagicMock` config stub `_make_config()` (C-17) — do not add a module-scope `from core.config import CONFIG`.
- No float-literal `if/elif` introduced (C-06): the change is set-membership only.
- `classify()` stays a pure function returning `Result` (C-12); no writes, no audit.

**Depends on.** None — fully independent of the search work. **Cut cleanly if research finds it bigger than this paragraph.**

**Assumes.** A12.

**Interface shape.** Modifies an existing public function (`classify`) — a public-API touch. Research confirms whether the cleanest fix passes two sets or the registry object, and whether the capture call sites change.

**Dependency category.** in-process (test with the existing config stub + a stand-in registry).

**Decisions.**
- Q: Does `classify()` take two name sets, or the `ProjectRegistry` itself? Options: two `set[str]` params / a `registry` param. Leaning **two sets** (keeps `classify` free of vault imports and easy to stub); confirm against the call sites at research.
- Q: Is the old `_destination_names` removed or kept (still used by the prompt string elsewhere)? Confirm at research.

**Done when.** `classify()` rejects a domain name supplied as `project` (and a project name supplied as `primary_domain`) with a `Failure`, while still accepting each name in its correct field (P3-SRCH-09). The rewritten `test_classify.py` fixtures use the real `format_for_prompt` shape and the ~6 affected tests pass.

---

## Handoff notes

- **Contract with Phase 4 (MCP):** `search(query=None, project=None, date_range=None, max_results=None, db_path=None) -> Result[list[SearchResult]]` is the stable public surface the MCP `kms_search` tool will wrap. `SearchResult(vault_path, summary, snippet, score, metadata)` is the AI-triage payload — `metadata` is load-bearing (the AI triages on it). Full content is fetched lazily by the caller via `vault/reader.py::read_note(absolute_path)` — `search()` itself never reads bodies and never calls `read_note`. The card carries `vault_path` (relative); the caller joins `vault.root + vault_path` before `read_note`.
- **Contract with Phase 8/9 (Briefing/Synthesis):** `search(date_range=last_week)` (no query) must return recent notes (P3-SRCH-08). If OQ-P3B-3 lands as a `documents.filter_paths(...)` helper, Synthesis can reuse it directly.
- **Open uncertainty — A1/A2/A3 are the three load-bearing rows.** A1 (filtered-KNN form) is the whole meaning-search design; A2 (bare-query embedding) is a real recall risk; A3 (date-bound format) silently under/over-selects if wrong. Research must clear all three before the spec freezes signatures.
- **Suggested research (in priority order):**
  1. **Re-run the A1 KNN probe against the project's real DB** (not `:memory:`) on the pinned `sqlite-vec` version; have the implementer add a test that fails if a future upgrade changes filtered-KNN semantics (assert a far-but-in-set candidate is returned and a near-but-out-of-set is excluded). Pin `sqlite-vec` accordingly.
  2. **Decide and document A2** — embed the bare query or wrap it; the design leans bare-query (re-ranker absorbs asymmetry).
  3. **Confirm A3** against a real captured row's `updated_at` and the `--since` bound shape.
  4. **Confirm A8/A9/A10** — the intended dataclass/signature shapes, the `notes_fts` column index for `snippet`, and `CrossEncoder` offline loadability.
  5. **Resolve OQ-P3B-3** (Candidate Filter placement) and **OQ-P3B-1** (sync-in-async confirmation; thread offload stays deferred to Phase 4).
  6. **TD-051 (A12):** confirm `ProjectRegistry.all_project_names` / `get_groups()` shapes and the cleanest `classify()` signature change against the real call sites in `capture.py`.
- **Contract from Component 0 (Descriptive Title at Capture) → the search components:** Component 0 guarantees that, for notes captured after R1 ships, `documents.title` holds the AI's descriptive title. The Re-ranker (Component 3) and the Search Command (Component 5) require no change — they keep reading `documents.title` via `get_by_path`; that value is simply correct now. New behavior ID: **P3-SRCH-10** (a captured binary's sibling carries an AI-generated descriptive title in frontmatter; the catalog/card shows it, not `report.pdf`) — defined in the inventory/design, referenced here.
- **Backfill (Component 0, out of scope, recorded):** pre-R1 siblings keep their filename titles. `--reindex` **cannot** regenerate an AI title (it re-runs the index writers from existing frontmatter; it does not re-run the summarizer/`extract_metadata` LLM stage). Backfilling pre-R1 notes requires re-capture (a full `summarize → metadata → store` pass), which is heavier and not part of R1 — logged as tech debt by the design step.
- **Deferred decisions (carried into research/plan, not resolved here):** the exact `date_range` type; whether `60` is a config key or a named constant; the precise `metadata` field set; whether `score` is the cross-encoder or fused score; `--reindex` as standalone vs combined; whether `_destination_names` is removed. **Component 0 (R1) deferred:** the field **name** (`title` vs `display_title`/`ai_title` — leaning `title`, OQ-P3B-R1a-name); whether `extract_metadata.yaml`'s title instruction needs tightening (forbid file extensions / slashes — optional YAML-only tweak, **not done here**, OQ-P3B-R1b). The scope question (all captures vs siblings-only) is **RESOLVED-locked to all captures** — not deferred. See each component's **Decisions** block and the design's **Open questions** (OQ-P3B-1/2/3 and OQ-P3B-R1a/R1b).

---

## Next step

Spec written. Run `/research` to verify the spec assumptions (especially load-bearing A1/A2/A3) against real code before planning.
