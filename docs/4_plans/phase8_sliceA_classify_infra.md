# Plan: Phase 8 Slice A — Classify Infrastructure (no LLM calls)

_Last updated: 2026-06-14_
_Status: [ ] pending_

_Spec: `docs/2_specs/phase8_sliceA_classify_infra.md` (component IDs P8-CLS-A-01…07)_
_Research: `docs/3_research/phase8_sliceA_classify_infra.md` (11 validated/resolved, 0 invalidated)_
_Design: `docs/1_design/phase8_sliceA_classify_infra.md` (Option A chosen)_
_ADR: `docs/architecture/system_adr/0017-classify-in-memory-asyncio-queue-and-classify-content-hash-work-discovery.md`_
_Behavior IDs: P8-CLS-A-01 … P8-CLS-A-07 (already in `docs/system_behavior/behavior_inventory.yaml` — reference, do not duplicate)_

> **For the non-coder reader:** This plan builds the *plumbing* that will later turn captured files into structured facts ("Anthony leads Movie Q2"). Slice A finds which files still need that treatment, loads the right inputs, and runs a single background worker that prepares everything — but **stops right before any AI call**. No AI, no network, no cost. The AI step is Slice B. Every phase here is testable with a plain database and config.

---

## Architecture

The plan implements the spec's seven build components. The diagrams below come from the upstream design (Q1) and spec (Q2) — they are referenced, not redrawn. Q3 (drawn fresh for this plan) shows the *rules* each new piece must obey and why.

### Q1 — What happens inside
See `docs/1_design/phase8_sliceA_classify_infra.md` (Q1 diagram). In one line: container boots → catch-up scan finds unclassified/changed documents → in-memory work queue → single worker pulls one document at a time → prepares its inputs (text-or-summary, dimensions+guidance, ranked+capped facts) → **stops** (no AI, no stamp in Slice A).

### Q2 — How it connects
See `docs/2_specs/phase8_sliceA_classify_infra.md` (Q2 diagram). Container startup starts the Worker and triggers the Catch-up Scan; the Scan uses the Work Finder to fill the Work Queue; the Worker calls three helpers (Content Reader, Dimension Loader, Context Loader) that read the Document Store, the Knowledge Categories config, and the Fact Store. The Capture push, the Housekeeping AI, and the Classified-Stamp happy-path call are dashed Slice B seams.

### Q3 — Why build it this way

```
# Phase 8 Slice A — Classify Infrastructure: Why Build It This Way
Scope: Shows the rules and existing patterns each new piece must conform to,
       and why. Builds on Q1 (what happens inside) and Q2 (how it connects) —
       same names, same positions. Does NOT re-show the step-by-step flow.

How to read this:
  Solid boxes      = the Slice A pieces (same as Q2)
  Sticky notes     = the rule or existing pattern that piece must respect
  ─ ─ rule line ─ ─ = "this rule shapes this box, and here's why"
  Dashed boxes      = Slice B seams (shown for context, not built now)


  ┌ ─ RULE ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  │ Start the worker the RIGHT way:                            │
  │ wrap the app's existing built-in startup routine.          │
  │ NOT the "on-startup handler" (silently ignored once a      │
  │ startup routine is already set) and NOT the per-chat        │
  │ startup (fires only when a human opens a chat — too late   │
  │ for a background housekeeper).                              │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┬ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
                                 │ shapes how it starts
                                 ▼
                       ┌──────────────────┐
                       │ Container Startup│
                       │ Boots the app,   │
                       │ starts machinery │
                       └────┬────────┬────┘
                 starts     │        │  triggers
                 Worker     │        ▼
                            │   ┌──────────────────┐
                            │   │ Catch-up Scan    │
                            │   │ Finds docs still │
                            │   │ needing classify │
                            │   └────────┬─────────┘
                            │            │ uses
                            │            ▼
                            │   ┌──────────────────┐
                            │   │ Work Finder      │
                            │   │ Which docs need  │
                            │   │ work?            │
                            │   └────────┬─────────┘
                            │            │ fills queue with ids
                            │            ▼
                            │   ┌──────────────────┐
   ┌ ─ ─ ─ ─ ─ ─ ─ ┐  - - ─►   │ Work Queue       │
   │ Capture        │ future    │ Waiting doc ids, │
   │ (Slice B seam) │ seam      │ one at a time    │
   └ ─ ─ ─ ─ ─ ─ ─ ┘           └────────┬─────────┘
                            │            │ one id at a time
                            │            ▼
                            │   ┌──────────────────┐
                 ┌──────────┴───┤ Worker           ├────────┐
                 │   ┌──────────┤ Pulls one doc,   │        │
                 │   │          │ prepares inputs  │        │
                 │   │          └────────┬─────────┘        │
            calls│   │calls              │calls             │calls
                 ▼   │                   ▼                  ▼
       ┌──────────────┐        ┌──────────────────┐  ┌──────────────┐
       │ Content      │        │ Dimension Loader │  │ Context      │
       │ Reader       │        │ Reads categories │  │ Loader       │
       │ Full text or │        │ + guidance       │  │ Ranks & caps │
       │ summary      │        └────────┬─────────┘  │ known facts  │
       └──────┬───────┘                 │ reads      └──────┬───────┘
              │ reads                    ▼                   │ reads
              │                ┌──────────────────┐         │
              │                │ Knowledge        │         │
              │                │ Categories config│         │
              │                └────────┬─────────┘         │
              │                         │                   │
              ▼                         ▼                   ▼
       ┌──────────────┐      ┌ ─ RULE ─ ─ ─ ─ ─ ─ ┐  ┌ ─ RULE ─ ─ ─ ─ ─ ┐
       │ Document     │      │ Reuse the existing  │  │ Add a NEW ranked, │
       │ Store        │      │ categories module — │  │ capped query —    │
       │ Files, text, │      │ do NOT build a new  │  │ do NOT change the │
       │ summaries,   │      │ loader. Config gains│  │ existing fact-    │
       │ fingerprints │      │ a nested shape      │  │ store commands.   │
       └──────┬───────┘      │ (tags + guidance);  │  │ Keeps the stable  │
              │              │ the existing checker│  │ contract stable;  │
              │              │ learns it. Known,   │  │ isolates ranking. │
              │              │ mechanical test     │  └ ─ ─ ─ ─ ┬ ─ ─ ─ ─ ┘
              │              │ cascade.            │            │ shapes
              │              └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘            ▼
              │                                          ┌──────────────┐
              │                                          │ Fact Store   │
              │                                          │ Structured   │
              │                                          │ facts (+ new │
              │                                          │ trust &      │
              │                                          │ demand cols) │
              │                                          └──────┬───────┘
              ▼                                                 ▼
       ┌──────────────────────────────────────────────────────────────┐
       │ Schema Update (versioned migration file)                       │
       │ Adds new columns + indexes to both stores                      │
       └────────────────────────────┬───────────────────────────────────┘
                                     │ shaped by
                                     ▼
                    ┌ ─ RULE ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
                    │ Schema changes only via a versioned │
                    │ file the migration runner finds     │
                    │ automatically — drop the file, no   │
                    │ registry to edit. Adding it bumps   │
                    │ the version, so the prior version   │
                    │ checks cascade up (9 → 10).         │
                    └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘


  TWO RULES THAT APPLY TO EVERY NEW PIECE ABOVE
  (Work Finder, Content Reader, Context Loader, Classified-Stamp, Worker):

  ┌ ─ RULE ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐   ┌ ─ RULE ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  │ Every new function reports     │   │ Sizing limits (how much text to  │
  │ success-or-failure explicitly  │   │ feed, how many facts to keep)    │
  │ — no silent failures.          │   │ come from a config block, never  │
  │                                │   │ a hardcoded number in the code.  │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘   └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘

  ┌ ─ DEFERRED TO SLICE B (shown for context) ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  │ Housekeeping AI (extracts facts) → Classified-Stamp (marks   │
  │ doc done on success). In Slice A the Worker STOPS after       │
  │ "prepares inputs" — no AI, no stamp on the happy path.        │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

```
Simplified: The two project-wide rules (report success/failure; limits from
            config) apply to all five new functions, so they are drawn once at
            the bottom instead of pinned to each box. The Housekeeping AI and
            Classified-Stamp are Slice B — shown dashed only to mark where the
            Worker deliberately stops in Slice A.
```

**Extension-point marking for each new piece:**
- Migration 010 — `[extensible: config]` (versioned `.sql`, auto-discovered by `db.py` glob; drop file, no registry).
- `dimensions.yaml` + loader/validator — `[extensible: config]` (behavior is data; the deep module `core/tags.py` is extended, not duplicated).
- New ranked query in the fact store — `[closed]` (one internal caller; deliberately NOT a protocol — see seam check below).
- `ClassifyConfig` — `[extensible: config]`.
- Content Reader / Context Loader / Work Finder / Classified-Stamp / consumer — `[closed]` (single internal caller each; plain `Result`-returning functions, not protocols — the design's seam check confirmed no multi-adapter interface exists, so a protocol would be speculative).

None of the `[closed]` markings hide a variant the spec expects. The worker-start hook is closed to `on_startup` but **open to lifespan composition** — that is the chosen extension surface.

---

## Approach

Build the seven components in **dependency order**: the durable schema first (everything reads the new columns), then the two independent leaf pieces (config-shape change; config block), then the fact-store ranking support, then the two preparation helpers and the two document-store queries, and finally the queue + worker + startup wiring that ties them together. Each phase is independently testable against a temporary SQLite database and explicit config — no AI, no network. The single genuinely-new risk is the `dimensions.yaml` shape change (a known, mechanical test cascade) and the worker-start mechanism (resolved by research: a composed outer lifespan, never `on_startup`).

The order maps to phases:

| Phase | Spec component(s) | Why this position |
|---|---|---|
| 1 | C-01 Schema Update (migration 010) | Foundation — all later phases read the new columns; nothing else can be tested first. |
| 2 | C-02 dimensions.yaml + loader/validator | Independent of the DB; pure config + loader. |
| 3 | C-04 ClassifyConfig block | Independent of the DB; phases 4, 5, 7 read its ints. |
| 4 | C-03 KnowledgeEntry fields + new ranked query | Needs the columns from phase 1. |
| 5 | C-06 Work Finder + Classified-Stamp | Needs the column from phase 1. |
| 6 | C-05 Content Reader + Context Loader | Needs phases 1–4 (reads documents, dimensions, ranked facts; sizes against config). |
| 7 | C-07 Queue + Worker + catch-up scan | Needs phases 1, 2, 5, 6 (and 3, 4 transitively); the integration capstone. |

Phases 2 and 3 are independent of phase 1 and of each other; they are ordered here for a clean linear TDD walk, not because of a hard dependency.

---

## Phases

### Phase 1 — Schema Update (migration 010)
_Implements spec component 1 (P8-CLS-A-07). Reference the spec's Build/Done-when; this plan adds the TDD order, exact line bumps, and commit boundary._

**Goal**: Add the durable database fields the rest of Slice A depends on — a per-document classify-fingerprint, two inert ranking signals on facts, and two supporting indexes — without disturbing existing rows.

**Design** (what changes):
```
documents:         + classify_content_hash  TEXT (nullable)
                   + INDEX idx_docs_classify_hash ON (classify_content_hash)
knowledge_entries: + trust_score      REAL    DEFAULT 0.5   (inert in P8)
                   + retrieval_count  INTEGER DEFAULT 0      (inert in P8)
                   + INDEX idx_ke_trust ON (trust_score DESC)
schema_version: 9 → 10   (auto-bumped by the migration runner glob; no registry)
```

**Steps** (TDD — RED first):
1. **RED** — Write `tests/test_storage/test_migration_010.py`: after `init_db(tmp_db)`, assert `schema_version == 10`; assert `documents` has a nullable `classify_content_hash` column; assert `knowledge_entries` has `trust_score` defaulting to `0.5` and `retrieval_count` defaulting to `0` (insert a row omitting them, read back the defaults); assert both indexes exist (query `sqlite_master` for `idx_ke_trust` and `idx_docs_classify_hash`); assert pre-existing `documents` + `knowledge_entries` rows survive the migration intact. Use explicit `db_path=tmp_path/...`; **no module-scope `CONFIG`** (C-17). Run → fails (no migration file).
2. **GREEN** — Create `src/storage/migrations/010_classify_content_hash_and_ranking.sql` with the five DDL statements from the spec's component 1, plus a header comment stating `trust_score`/`retrieval_count` are intentionally inert in Phase 8 (Phase 9 populates retrieval_count, Phase 10 populates trust_score). Follow the format of `009_add_blob_ref.sql`.
3. **GREEN (cascade)** — Bump the prior version-pin assertions `9 → 10`: `tests/test_storage/test_migration_007.py:41` and `:56`, `test_migration_008.py:47`, `test_migration_009.py:38`. This is the expected migration cascade, NOT a regression (CLAUDE.md "every new migration breaks the previous migration's version-pin test").
4. Run `uv run pytest tests/test_storage/` → all green.

**Files to modify**:
- `src/storage/migrations/010_classify_content_hash_and_ranking.sql` — **new** (5 DDL stmts + inert-columns comment).
- `tests/test_storage/test_migration_010.py` — **new**.
- `tests/test_storage/test_migration_007.py`, `test_migration_008.py`, `test_migration_009.py` — version-pin bump only.

**Notes / coupling**: Auto-discovery confirmed (research A10): `db.py` runs `sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))` and applies files whose number exceeds the stored version — drop-file-only, no registry. `classify_content_hash` is nullable on purpose so legacy rows (NULL) are correctly re-discovered by the Work Finder.

**Test criteria**:
- [ ] `schema_version == 10` after `init_db`.
- [ ] `documents.classify_content_hash` exists, nullable; `knowledge_entries.trust_score` defaults `0.5`, `retrieval_count` defaults `0`.
- [ ] `idx_ke_trust` and `idx_docs_classify_hash` both present in `sqlite_master`.
- [ ] Pre-seeded rows in both tables survive the migration readable.
- [ ] Full `tests/test_storage/` green (version pins bumped).

**Status**: [ ] pending

---

### Phase 2 — Expanded dimensions.yaml + loader/validator extension
_Implements spec component 2 (P8-CLS-A-04). Reference the spec's Build/Done-when._

**Goal**: Give each knowledge category a richer tag set **and** a per-category guidance text, and teach the existing loader/validator to carry the nested shape — without splitting the concept across two files or building a new module.

**Design** (before/after shape):
```
BEFORE (flat):                  AFTER (nested):
people:                         people:
  - role                          tags: [role, ..., other]
  - other                         guidance: "Look for who leads / owns ..."
projects:                       projects:
  - status                        tags: [status, timeline, ..., other]
  - timeline                      guidance: "Look for project status / timeline ..."
  - other                       domains:
domains:                          tags: [..., other]
  - other                         guidance: "Look for the domain / area ..."

validate_dimension_tag reads:  rulebook[dim]          →  rulebook[dim]["tags"]
```

**Steps** (TDD — RED first):
1. **RED** — Update `tests/test_core/test_dimensions.py`: move the inline `RULEBOOK` fixture to the nested shape `{dim: {"tags": [...], "guidance": "..."}}`; update `TestLoadDimensions` so `"other" in loaded["people"]["tags"]` (not `loaded["people"]`); keep `validate_dimension_tag("people", "role", RULEBOOK)` success / `"xyz"` failure / unknown-dimension failure / `"other"` for every dimension. Add a test that a config with a dimension **missing its `other` tag** (or missing `guidance`) is **rejected with a clear `Failure`** (P8-CLS-A-04 expects loud rejection, not silent accept). This is the P5-DATA-07/08 flat-shape cascade — expected, mechanical. Run → fails.
2. **GREEN** — Extend `core/tags.py::load_dimensions` to load the nested shape and validate-on-load: reject (return/raise a clear error surfaced as `Failure` by the validator path, or a dedicated `validate_dimensions(rulebook) -> Result`) when any dimension lacks the mandatory `other` tag or its `guidance` block. **Decision (resolved in spec/OQ-P8A-05):** `guidance` is **required** per dimension; missing `other`/`guidance` fails loudly at load. Keep the exact validation surface minimal — extend the loader or add one small `validate_dimensions` helper; do not scatter checks.
3. **GREEN** — Extend `core/tags.py::validate_dimension_tag` line 178: `allowed_tags = rulebook[dimension]["tags"]` (was `rulebook[dimension]`).
4. **GREEN** — Rewrite `config/dimensions.yaml` to the nested shape with the grill's richer defaults (people / projects / domains, each `{tags: [...], guidance: "..."}`, every tag set keeping the mandatory `other`).
5. Run `uv run pytest tests/test_core/test_dimensions.py` → green.

**Files to modify**:
- `src/core/tags.py` — `load_dimensions` (+ optional `validate_dimensions`), `validate_dimension_tag` line 178.
- `config/dimensions.yaml` — flat → nested + guidance.
- `tests/test_core/test_dimensions.py` — fixture + assertions to nested shape + add reject-malformed test.

**Notes / coupling**: Blast radius confirmed limited (research A4): only `core/tags.py` (definitions) and `test_dimensions.py` read the flat shape. `confidence_to_status` is untouched. `knowledge_entries.py` imports only `confidence_to_status`, not the dimension functions. This is the one backward-incompatible config-shape change in Slice A. The nested break is **loud** (the test fails immediately if the validator isn't updated) — not silent. This deepens the existing `core/tags.py` module rather than adding a new seam (`[extensible: config]`).

**Test criteria**:
- [ ] Loading the expanded config exposes, per category, its allowed tags AND its guidance text.
- [ ] Every tag set still includes the mandatory `other` catch-all.
- [ ] A config with a dimension missing `other` (or missing `guidance`) is rejected with a clear `Failure`, not silently accepted.
- [ ] `validate_dimension_tag` works against the nested shape (known tag → success, invented tag → failure).
- [ ] `tests/test_core/test_dimensions.py` green.

**Status**: [ ] pending

---

### Phase 3 — Classify config block (ClassifyConfig)
_Implements spec component 4. Reference the spec's Build/Done-when._

**Goal**: Make the two Slice A tunables (content-token cap, per-dimension fact cap) configurable, satisfying the C-06 spirit (no hardcoded tunables in pipeline code).

**Design**:
```
core/config.py:
  class ClassifyConfig(BaseModel):     # mirrors CaptureConfig / SearchConfig
      max_content_tokens: int = 10000
      max_entries_per_dimension: int = 50
  MainConfig:
      classify: ClassifyConfig = Field(default_factory=ClassifyConfig)   # alongside capture/search

config/config.yaml (NEW top-level block — note the existing
  "classify: claude_cli" at line 67 lives INSIDE providers:, unrelated):
  classify:
    max_content_tokens: 10000
    max_entries_per_dimension: 50
```

**Steps** (TDD — RED first):
1. **RED** — Add to `tests/test_core/test_config.py`: build a `MainConfig` (or load a temp config) and assert `cfg.classify.max_content_tokens == 10000` and `cfg.classify.max_entries_per_dimension == 50` by default; assert overrides from a YAML block take effect. Pass an explicit config; **no module-scope `CONFIG`** (C-17). Run → fails.
2. **GREEN** — Add `ClassifyConfig` sub-model in `core/config.py` near `SearchConfig` (line ~327); wire `classify: ClassifyConfig = Field(default_factory=ClassifyConfig)` into `MainConfig` (line ~366, alongside `capture`/`search`).
3. **GREEN** — Add the top-level `classify:` block to `config/config.yaml`.
4. Run `uv run pytest tests/test_core/test_config.py` → green.

**Files to modify**:
- `src/core/config.py` — new `ClassifyConfig`; field on `MainConfig`.
- `config/config.yaml` — new top-level `classify:` block.
- `tests/test_core/test_config.py` — defaults + override test.

**Notes / coupling**: Use Pydantic `Field` (these are human-configured values, not computed — CLAUDE.md "Field vs @property"). **Decision (resolved):** plain `config.yaml` block, NOT `thresholds.yaml` — these are sizing ints, not confidence floats, so they don't belong in the band wiring and won't trip the C-06 float hook (but the no-literal rule still binds downstream). `[extensible: config]`.

**Test criteria**:
- [ ] Both caps read from config, defaulting to `10000` and `50`.
- [ ] YAML overrides take effect.
- [ ] No Slice A code (phases 4–7) compares against a literal cap value.
- [ ] `tests/test_core/test_config.py` green.

**Status**: [ ] pending

---

### Phase 4 — KnowledgeEntry ranking support + new ranked query
_Implements spec component 3 (supports P8-CLS-A-05). Reference the spec's Build/Done-when. **Depends on Phase 1** (columns must exist) **and Phase 3** (the cap int)._

**Goal**: Make facts sortable by the new ranking signals, and give the Context Loader a query that returns ranked, capped, non-retired facts per category — each carrying its database id.

**Design**:
```
KnowledgeEntry dataclass  + trust_score: float = 0.5
                          + retrieval_count: int = 0
_row_to_entry             reads both new columns

NEW function (do NOT extend get_confident_and_pending — OQ-P8A-02):
  SELECT * FROM knowledge_entries
  WHERE status != 'retired' AND dimension = ?
  ORDER BY trust_score DESC, confidence DESC, updated_at DESC
  LIMIT ?            ← cap from CONFIG.classify.max_entries_per_dimension
  → Result[list[KnowledgeEntry]]
```

**Steps** (TDD — RED first):
1. **RED** — In `tests/test_storage/test_knowledge_entries.py` (or a new sibling test file): seed a temp DB (migrated to v10) with, for one dimension, more rows than a small cap, mixed `trust_score`/`confidence`/`updated_at`, plus one `retired` row. Assert the new query: excludes the retired row; orders by trust → confidence → recency; returns no more than the cap; each returned entry carries its `id`, `trust_score`, and `retrieval_count` round-tripped from the DB. Also assert `upsert` → read-back round-trips `trust_score`/`retrieval_count`. Pass explicit `db_path`. Run → fails.
2. **GREEN** — Add `trust_score: float = 0.5` and `retrieval_count: int = 0` to `KnowledgeEntry` (`storage/knowledge_entries.py:16`); have `_row_to_entry` read both **defensively** (`row["trust_score"] if "trust_score" in row.keys() else 0.5`, same for `retrieval_count` default `0`) so legacy-shaped rows still parse.
3. **GREEN** — Add the new ranked+capped query function (e.g. `query_ranked_by_dimension(dimension, *, limit, db_path=None) -> Result[list[KnowledgeEntry]]`). The caller passes the limit; the function does NOT read `CONFIG` itself (the Context Loader in Phase 6 supplies `CONFIG.classify.max_entries_per_dimension`). Reuse `get_connection(db_path, readonly=True)` and `_row_to_entry`. Return `Result`.
4. **Decision (resolved by research):** Do **not** add the two columns to `upsert`'s INSERT/UPDATE lists — DB defaults cover omitted inserts, and the `SELECT *` round-trip + `_row_to_entry` read is sufficient for Slice A correctness. Keep `upsert` unchanged (cosmetic-only addition rejected to minimize surface).
5. Run `uv run pytest tests/test_storage/test_knowledge_entries.py` → green.

**Files to modify**:
- `src/storage/knowledge_entries.py` — dataclass fields, `_row_to_entry`, new query function.
- `tests/test_storage/test_knowledge_entries.py` — round-trip + ranked-query tests.

**Notes / coupling**: NEW function, do **not** extend `get_confident_and_pending` (OQ-P8A-02; research A5 confirmed it is unranked/uncapped — adding LIMIT there would silently cap existing callers). The 5-function CRUD contract stays stable. `[closed]` — single internal caller (the Context Loader); a protocol would be speculative.

**Test criteria**:
- [ ] `KnowledgeEntry` round-trips `trust_score`/`retrieval_count` from the DB.
- [ ] New query: excludes retired; ordered trust → confidence → recency; capped at the passed limit; each entry carries its id.
- [ ] `get_confident_and_pending` unchanged (still unranked/uncapped).
- [ ] `tests/test_storage/test_knowledge_entries.py` green.

**Status**: [ ] pending

---

### Phase 5 — Work Finder + Classified-Stamp (storage/documents.py)
_Implements spec component 6 (P8-CLS-A-01, P8-CLS-A-02). Reference the spec's Build/Done-when. **Depends on Phase 1.**_

**Goal**: Give classify a way to discover its own work and a way to mark a document done — the durable backbone of the queue's retry/skip behavior.

**Design**:
```
Work Finder:
  SELECT id FROM documents
  WHERE classify_content_hash IS NULL OR classify_content_hash != content_hash
  → Result[list[int]]

Classified-Stamp:
  UPDATE documents SET classify_content_hash = content_hash WHERE id = ?
  → Result[int]   (rowcount; 0 ⇒ id not found)

DocumentRow            + classify_content_hash: str | None = None
_row_from_sqlite       reads it defensively (same pattern as full_body/blob_ref)
```

**Steps** (TDD — RED first):
1. **RED** — In `tests/test_storage/test_documents.py`: seed a temp DB (v10) with three documents — one with `classify_content_hash IS NULL`, one where `classify_content_hash != content_hash`, one where they match. Assert Work Finder returns exactly the first two ids. Assert: after Classified-Stamp on the NULL-fingerprint doc, Work Finder no longer returns it; an unstamped doc is still returned (retry path); Classified-Stamp on a missing id returns `Success(0)` (or a clear `Failure`). Assert `DocumentRow.classify_content_hash` round-trips. Explicit `db_path`. Run → fails.
2. **GREEN** — Add `classify_content_hash: str | None = None` to `DocumentRow` (`documents.py:34`); have `_row_from_sqlite` read it defensively: `classify_content_hash=row["classify_content_hash"] if "classify_content_hash" in row.keys() else None` (line ~88, mirroring `blob_ref`/`mime_type`).
3. **GREEN** — Add `find_unclassified(*, db_path=None) -> Result[list[int]]` (Work Finder) and `stamp_classified(doc_id, *, db_path=None) -> Result[int]` (Classified-Stamp). Both reuse `get_connection`. Stamp uses a single `UPDATE … SET classify_content_hash = content_hash WHERE id = ?` (sets the fingerprint to the row's own current `content_hash`). Return `Result`.
4. Run `uv run pytest tests/test_storage/test_documents.py` → green.

**Files to modify**:
- `src/storage/documents.py` — `DocumentRow` field, `_row_from_sqlite`, two new functions.
- `tests/test_storage/test_documents.py` — work-discovery + stamp tests.

**Notes / coupling**: Classified-Stamp is built and unit-tested in Slice A but **NOT called on the happy path until Slice B** (no successful classify without the AI). Because nothing is stamped in Slice A, re-running the scan re-discovers the same docs — correct and harmless. `[closed]` — internal queries. Do not touch `replace_path`/`rename`/`delete_by_path` search-table cleanup (out of scope; CLAUDE.md Phase 3 gotcha).

**Test criteria**:
- [ ] Work Finder returns NULL-fingerprint + mismatch docs; not the matching doc (P8-CLS-A-01).
- [ ] After Classified-Stamp, work discovery no longer returns that doc; unstamped docs still returned (P8-CLS-A-02).
- [ ] `DocumentRow.classify_content_hash` round-trips.
- [ ] `tests/test_storage/test_documents.py` green.

**Status**: [ ] pending

---

### Phase 6 — Content Reader + Context Loader (pipelines/classify.py)
_Implements spec component 5 (P8-CLS-A-03, P8-CLS-A-05). Reference the spec's Build/Done-when. **Depends on Phases 1, 2, 3, 4.**_

**Goal**: Build the two preparation helpers that turn a document into the inputs an extractor will later consume — choosing text-vs-summary by size, and loading ranked+capped facts per category.

**Design**:
```
Content Reader (given a DocumentRow or doc id):
  read full_body + summary;
  if len(full_body) // 4  <  CONFIG.classify.max_content_tokens  → use full_body
  else                                                            → use summary
  → Result[str]      (// 4 is the chars→tokens estimate; threshold from config, never a literal)

Context Loader (for each configured dimension):
  call Phase-4 ranked query with cap = CONFIG.classify.max_entries_per_dimension
  → Result[dict[str, list[KnowledgeEntry]]]   (dimension → ranked, capped, non-retired facts)
```

**Steps** (TDD — RED first):
1. **RED** — In `tests/test_pipelines/test_classify_infra.py` (new): seed a temp DB + an explicit `ClassifyConfig`. Content Reader: a doc whose `full_body` length / 4 is under the budget yields `full_body`; a doc whose `full_body` is huge yields `summary`. Context Loader: for a dimension holding more facts than the cap, returns ranked (trust → confidence → recency), capped, no retired, each with id; for a dimension with no facts, returns an empty list (not an error). Pass config/db explicitly; **no module-scope `CONFIG`** (C-17). Run → fails.
2. **GREEN** — Add Content Reader and Context Loader as **new functions in `pipelines/classify.py`**, kept clearly separate from the soon-to-die folder-routing code (`classify`, `ClassifyResult`, `build_subject`, etc. — left untouched; Slice B guts them). Both take the config (or read `CONFIG`) and `db_path`, return `Result`. The token comparison reads `config.classify.max_content_tokens`; the Context Loader passes `config.classify.max_entries_per_dimension` as the cap — never a literal.
3. Run `uv run pytest tests/test_pipelines/test_classify_infra.py` → green.

**Files to modify**:
- `src/pipelines/classify.py` — Content Reader + Context Loader (new functions; old folder code untouched).
- `tests/test_pipelines/test_classify_infra.py` — **new**.

**Notes / coupling**: Each helper has exactly **one** caller (the consumer, Phase 7) — internal pipeline stages, NOT public interfaces with multiple adapters. Per the design seam check: plain functions returning `Result`, no protocols (`[closed]`). The `// 4` chars→tokens heuristic is intentional (no real tokenizer — out of scope). Dimension list comes from the loaded `dimensions.yaml` rulebook (Phase 2). **Threshold/cap MUST come from config, never a literal** (C-06 spirit) — the float hook won't fire on ints, but the rule binds.

**Test criteria**:
- [ ] Content Reader: under-budget doc → full text; over-budget doc → summary (P8-CLS-A-03).
- [ ] Context Loader: per-dimension ranked + capped + no retired, each with id; empty dimension → empty list (P8-CLS-A-05).
- [ ] Both return `Result`; no literal cap/budget in code.
- [ ] `tests/test_pipelines/test_classify_infra.py` green.

**Status**: [ ] pending

---

### Phase 7 — Work Queue + Worker + catch-up scan (build_app)
_Implements spec component 7 (P8-CLS-A-06). Reference the spec's Build/Done-when. **Depends on Phases 1, 2, 5, 6 (and 3, 4 transitively).**_

**Goal**: Wire the in-memory queue, the single sequential consumer, and the startup catch-up scan into the container so classification work flows automatically from boot — with the consumer body a **skeleton** that prepares inputs and stops before the (Slice B) AI call.

**Design**:
```
asyncio.Queue[int]  (doc ids)

consumer coroutine (single):
  while True:
    doc_id = await queue.get()
    load DocumentRow → Content Reader → Dimension Loader → Context Loader
    STOP HERE  ← Slice B seam (no AI call, no stamp on happy path)
    log + propagate Result on any stage failure (fingerprint left untouched → retried)
    queue.task_done()

catch-up scan (one burst — OQ-P8A-03):
  ids = Work Finder();  for id in ids: queue.put_nowait(id)

start mechanism — COMPOSED OUTER LIFESPAN in build_app (shape b):
  inner = app.router.lifespan_context          # the framework's session-manager lifespan
  @asynccontextmanager
  async def composed(app):
      worker = asyncio.create_task(consumer(queue, db_path, config))
      await catch_up_scan(queue, db_path)       # enqueue discoverable ids
      try:
          async with inner(app):                # framework MCP session manager still runs
              yield
      finally:
          worker.cancel()                       # cancel on shutdown
  app.router.lifespan_context = composed        # reassign IN PLACE inside build_app
  # NOT on_startup (silent no-op); NOT the per-chat MCP lifespan
```

**Steps** (TDD — RED first):
1. **RED (logic)** — In `tests/test_pipelines/test_classify_worker.py` (new): with an injected `asyncio.Queue` and a temp DB pre-seeded with several discoverable docs, run the catch-up scan + drive the consumer; assert all discoverable ids are enqueued, the consumer processes ids **one at a time** (never two concurrently — e.g. assert via a per-item marker / no overlap), the queue drains to empty (`queue.join()`), and the worker stops after preparing each doc's inputs (no AI call, no stamp — assert `classify_content_hash` stays NULL). Run → fails.
2. **RED (wiring guard)** — In `tests/test_mcp_server/test_cloud_entry.py` (extend): build the app via `build_app(db_path=tmp)`, enter its lifespan (`async with app.router.lifespan_context(app):`), and assert (a) the worker task is created, (b) the inner FastMCP session-manager lifespan still runs on entry (the MCP server initialises — e.g. session manager state is live / a tool-list works), and (c) the worker is cancelled on exit. A `/health` curl alone would NOT catch a regression here — this is the invisible-failure guard (research edge case). Run → fails.
3. **GREEN** — Add the `asyncio.Queue`, the consumer coroutine, and the catch-up scan (as functions, e.g. in `pipelines/classify.py` or a small `mcp_server/classify_worker.py` — keep them `Result`-returning and unit-testable with injected queue/db). Consumer body calls Content Reader → Dimension Loader → Context Loader, then STOPS at the marked Slice B seam; logs failures with `%s`-style stdlib logging (CLAUDE.md: stdlib logging has no kwargs).
4. **GREEN** — In `mcp_server/cloud_entry.py::build_app`, after `app = mcp.streamable_http_app()` and the route mount, capture `app.router.lifespan_context`, wrap it in the composed `@asynccontextmanager` above, and reassign in place. Forbid `on_startup`. Not wrapped in `asyncio.run` (runs under uvicorn's loop — C-10/C-11 N/A).
5. Run `uv run pytest tests/test_pipelines/test_classify_worker.py tests/test_mcp_server/test_cloud_entry.py` → green.

**Files to modify**:
- `src/pipelines/classify.py` (or new `src/mcp_server/classify_worker.py`) — queue, consumer, catch-up scan.
- `src/mcp_server/cloud_entry.py` — composed outer lifespan in `build_app`.
- `tests/test_pipelines/test_classify_worker.py` — **new** (consumer + scan logic).
- `tests/test_mcp_server/test_cloud_entry.py` — composed-lifespan guard test.

**Notes / coupling** (worker-start trap — the single most important wiring decision):
- The FastMCP-returned app already sets a custom lifespan (`fastmcp/server.py:1044`); Starlette ignores `on_startup` whenever a lifespan is set (`starlette/routing.py:582-599`). **`on_startup` is FORBIDDEN here** — it is a proven silent no-op (research A7).
- The lifespan is read at the ASGI lifespan event (`starlette/routing.py:638`), which fires at uvicorn startup *after* `build_app` returns — so reassigning `app.router.lifespan_context` in `build_app` takes effect (research re-check, verified at file:line).
- The inner lifespan is `session_manager.run()`, an `@asynccontextmanager` that may be entered only once per instance — the composed lifespan enters it exactly once per app boot, satisfying the guard.
- **NOT** the per-chat MCP `_lifespan` (`mcp_server/server.py:101`) — that fires per chat session, too late for a background housekeeper (ADR-0017 consequence #1).
- Composition shape (`asyncio.create_task` inside the lifespan body vs an anyio task group) is an implementation choice the spec/research leave open — `create_task` + cancel-on-exit is the simplest; bounded by "must enter the FastMCP session-manager lifespan AND start the worker, both under uvicorn's loop."
- **Tech-debt to log:** catch-up scan enqueues in one burst — page/batch if a large vault floods the queue at startup (OQ-P8A-03; grill "watch vault size"). Add a TD entry during implementation.

**Test criteria**:
- [ ] Catch-up scan enqueues all currently-discoverable docs; consumer processes one at a time (never two concurrently); queue drains to empty; worker stops after preparing inputs (no AI, no stamp) (P8-CLS-A-06).
- [ ] Composed-lifespan guard: worker task created on entry AND inner FastMCP session-manager lifespan still runs AND worker cancelled on exit.
- [ ] No `on_startup` handler used; not wrapped in `asyncio.run`.
- [ ] Both new/extended test files green.

**Status**: [ ] pending

---

## Open Questions

None blocking. All spec/design open questions are resolved upstream and reflected in the phases:
- **OQ-P8A-01 (worker start)** — resolved: composed outer lifespan in `build_app`, never `on_startup`, never the per-chat MCP lifespan (Phase 7).
- **OQ-P8A-02 (one ranked query vs extend)** — resolved: new query function (Phase 4).
- **OQ-P8A-03 (one burst vs paged scan)** — resolved for Slice A: one burst + tech-debt note (Phase 7).
- **OQ-P8A-05 (guidance mandatory? loud reject?)** — resolved: `guidance` required; missing `other`/`guidance` fails loudly at load (Phase 2).
- **knowledge_entries.upsert column list** — resolved by research: DB defaults + `_row_to_entry` reads suffice; `upsert` SQL unchanged (Phase 4, step 4).

One judgment call left to the implementer (not blocking): whether the queue/consumer/scan live in `pipelines/classify.py` or a small new `mcp_server/classify_worker.py`. Either keeps them `Result`-returning and unit-testable with an injected queue; pick whichever keeps the deletion test clean for Slice B's rewrite.

---

## Out of Scope (Slice B — note seams only, do not build)

- **Any LLM / AI call** — fact extraction, prompt rendering, JSON parsing of AI output.
- **Entity-extraction prompt** (`prompts/entity_extract.yaml`) + the per-dimension extractor.
- **Entry Writer** — routing new/update/retire actions, validating entry ids, writing facts.
- **Calling Classified-Stamp on the happy path** — built + tested in Slice A; invoked only after a successful classify in Slice B.
- **Wiring capture's push seam** — replacing the `capture.classify_ready` log line (`capture.py:267`, `:482`) with `queue.put(doc_id)`. Left as-is, documented.
- **Audit logging of classify decisions** — no AI decisions in Slice A.
- **Document-deletion source cleanup** — removing doc ids from `knowledge_entries.sources`.
- **Deleting the old folder-routing classify code** (`classify`, `ClassifyResult`, `build_subject`, `build_folder_subject`, `_destination_names`) — Slice A adds new functions alongside; Slice B guts it.
- **Populating `trust_score` / `retrieval_count`** — both ship inert (Phase 9 increments retrieval_count; Phase 10 populates trust_score).
- **Paging / batching the catch-up scan's enqueue** — one burst in Slice A (tech-debt note).
- **Back-pressure / retry-count bounding / dead-letter queue** — none in Slice A.
- **A real tokenizer** — Content Reader uses a `chars / 4` estimate.
