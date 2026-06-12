# Plan: Phase 5 Slice 1 — Data/Config Foundation

_Last updated: 2026-06-12_
_Status: [~] in progress_

_Spec (WHAT to build, source of truth): `docs/2_specs/P5_slice1_data_foundation.md`_
_Research (TRUST over spec where they differ): `docs/3_research/P5_slice1_data_foundation.md`_
_Design (Q1 diagram): `docs/1_design/P5_slice1_data_foundation.md`_
_Sequencing law (binding): ADR-0012 (`docs/architecture/system_adr/0012-additive-rearchitecture-defer-breaking-changes-to-consumer-refactor-phases.md`)_
_Success criteria: behavior inventory `P5-DATA-01 … P5-DATA-10` (referenced per phase; not restated here)_

> **Reader note.** This plan leads in plain English. Code references (file paths, function names, line numbers) sit in parentheses or sub-bullets — anchors for the engineer, verified against the live code on 2026-06-12. The plan owns HOW and the build order; the **spec** owns WHAT (Build steps, file inventory, Done-when). Open the spec's component sections (numbered 1–4) alongside each phase below.

---

## Architecture

### Q1 — What happens inside
_Verbatim from the design doc (`docs/1_design/P5_slice1_data_foundation.md` §Q1). The "what happens to one fact" view this slice builds the SOLID boxes of._

```
# Knowledge Storage Foundation — What Happens Inside
Scope: Shows how one document's facts become rows in the single knowledge table.
       Slice 1 builds the SOLID boxes (the table, create/read/retire, and the
       dimension/tag rulebook). The DASHED box (AI extraction) is a later phase,
       shown only for context.

  Solid boxes  = built in this slice
  Dashed box   = future phase, shown for context

        A document's text + the
        facts we already know  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
                  │                                     │
                  ▼                                     ▼
     ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐              ┌──────────────────────┐
     │ AI pulls out facts,  │              │ Dimension/tag rulebook│
     │ each tied to a person,│             │ checks every fact uses│
     │ project, or domain    │             │ an allowed category   │
     │ (FUTURE PHASE)        │             └──────────┬───────────┘
     └ ─ ─ ─ ─ ─ ┬ ─ ─ ─ ─ ─┘                         │
                 │                  rejects unknown ◄──┘
                 ▼                  category
     ┌───────────────────────┐
     │ How sure are we about  │
     │ this fact?             │
     └──────────┬────────────┘
                │
        ┌───────┴────────┐
        │                │
   Sure enough       Not yet sure
        │                │
        ▼                ▼
  ┌────────────┐   ┌────────────┐
  │ Save as a  │   │ Save as a  │
  │ "confident"│   │ "pending"  │
  │ fact       │   │ fact       │
  └─────┬──────┘   └─────┬──────┘
        │                │
        └───────┬────────┘
                ▼
     ┌────────────────────────┐
     │ Write one row in the    │
     │ knowledge table — the   │
     │ source documents are    │
     │ listed inside that row  │
     └────────────────────────┘

  Separately, when newer information overrides an old fact:
     ┌────────────────────────┐
     │ Flip that fact to       │
     │ "retired" with a reason │
     │ — the row is kept for   │
     │ history, never deleted  │
     └────────────────────────┘
```

### Q2 — How it connects
_Referenced from the spec (`docs/2_specs/P5_slice1_data_foundation.md` §Q2 Diagram). The hub-and-spoke "how the new pieces wire to existing/future ones" view, plus the spec's plain-English component glossary (diagram name → code symbol). Not duplicated here — open the spec._

### Q3 — Why build it this way

```
# Knowledge Storage Foundation — Why Build It This Way
Scope: The same picture as Q1 (what happens inside) and Q2 (how it connects),
       zoomed out once more to show WHICH existing pattern each new piece
       conforms to, and WHY. Does NOT re-show the internal save/retire steps
       (see Q1) or every wiring arrow (see Q2).

How to read this:
  Center boxes      = the new pieces built this slice (same names as Q1/Q2)
  Surrounding boxes = the existing rule or pattern each new piece must follow
  Lines             = "this new piece conforms to this existing rule"
  Dashed box        = future phase, deliberately not built yet

  ┌────────────────────────┐                  ┌────────────────────────┐
  │ EXISTING: The migration │                 │ EXISTING: Additive-only │
  │ runner                  │                 │ sequencing law          │
  │ Applies upgrade files   │                 │ Old records keep working│
  │ in order, bumps the     │                 │ untouched — never       │
  │ version, no code change │                 │ re-saved                │
  └───────────┬─────────────┘                 └───────────┬─────────────┘
              │ so we ship ONE upgrade file                │ so the 3 new
              │ (create table + add 3 columns);            │ document fields
              │ precedent: the batches upgrade             │ are OPTIONAL/empty
              ▼                                            ▼
       ┌─────────────────────────────────────────────────────────┐
       │ Schema Upgrade                                           │
       │ Creates the knowledge table + adds 3 optional fields to  │
       │ the Document Catalog                                     │
       └─────────────────────────────────────────────────────────┘

  ┌────────────────────────┐                  ┌────────────────────────┐
  │ EXISTING: The audit-log │                 │ EXISTING: The           │
  │ CRUD shape              │                 │ Success/Failure wrapper │
  │ Opens every connection  │                 │ Every operation reports │
  │ through the one         │                 │ clearly whether it      │
  │ connection helper (so   │                 │ worked                  │
  │ the safety pragma always│                 └───────────┬─────────────┘
  │ applies) + JSON-list    │                             │ so all five
  │ round-trip for the      │                             │ operations return
  │ sources list            │                             │ it
  └───────────┬─────────────┘                             │
              │ so the new store copies it                │
              │ wholesale (no new plumbing)               │
              ▼                                            ▼
       ┌─────────────────────────────────────────────────────────┐
       │ Knowledge Entry Store                                    │
       │ Create / read / retire facts — center piece of Q2        │
       └──────────────┬──────────────────────────┬────────────────┘
                      │ labels each fact          │ checks each fact's
                      │ confident / pending via   │ category via
                      ▼                           ▼
       ┌────────────────────────┐    ┌────────────────────────────┐
       │ EXISTING: The          │    │ Tag Validator + status      │
       │ confidence gate        │    │ helper                      │
       │ Turns a score into a   │    │ ADDED INTO the existing tag │
       │ trust level; cutoffs   │    │ rulebook (deepened, not a   │
       │ stay in config, never  │    │ new shallow module)         │
       │ hardcoded — REUSED,    │    └─────────────┬───────────────┘
       │ outputs relabelled     │                  │ reads its list via
       └────────────────────────┘                  ▼
                                     ┌────────────────────────────┐
                                     │ EXISTING: The taxonomy YAML │
                                     │ loader pattern              │
                                     │ A standalone file reader —  │
                                     │ so the central config module│
                                     │ stays untouched             │
                                     └────────────────────────────┘

  Deliberately NOT built this slice (no interface ahead of its consumer):
       ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
       │ Fact Extractor (FUTURE)                                  │
       │ Will produce facts and call the store — and the old      │
       │ vault-write-to-index path stays untouched until its own  │
       │ phase rewrites it                                        │
       └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘

Simplified: Q2's loose-reference arrow (Store → Document Catalog by internal id)
            and the future "Extractor → Store" call are folded into the dashed
            future box to keep the focus on rationale, not wiring. Each
            "EXISTING" box names one rule the build conforms to; the rule's
            own deeper detail lives in Q2.
```

### Extension-point marking (per new component)

- **Schema Upgrade** (`008_*.sql`) — `[extensible: config]`. Adding a new dimension is a config edit, not a migration (single universal table, no per-dimension table).
- **Knowledge Entry Store** (`storage/knowledge_entries.py`) — `[closed]` for now, by design. Concrete functions returning `Result`; exactly one future caller pattern (the extractor), so no `Protocol`/`ABC` seam — building one would be an interface ahead of its consumer (C-15). Flagged-and-accepted per design "deletion test" / research Extension Points.
- **Dimension/Tag Rulebook** (`config/dimensions.yaml`) — `[extensible: config]`. Refining the taxonomy is a content edit; the validator reads whatever the file declares.
- **Tag Validator + status helper** (`core/tags.py`) — `[closed]`. Deepens the existing rulebook module (adds the new rule next to the rule it most resembles); no new seam.

No `[closed]` component above hides a variant the spec expects — the only variant point (new dimensions) is config-driven. No design question to raise.

---

## Approach

Build the three additive deliverables in dependency order so every phase is independently testable and the suite stays green at each boundary. The table comes first (everything else reads or writes it), then the two pure helpers that the store depends on (status mapping + dimension/tag validation, alongside the rulebook config they read), then the store that wires them together, and finally a suite-wide green check. The whole slice conforms to existing patterns rather than inventing new ones — the migration runner, the audit-log CRUD shape, the confidence gate, and the taxonomy YAML loader are all reused (see Q3). The single deliberate, expected test edit is the migration version pin (`7`→`8`), budgeted as its own build step so a red suite from those two assertions is never misread as a regression.

**Why this order, not another:** the store (Phase 3) cannot be tested until both the table (Phase 1) and the status helper (Phase 2) exist; the validator and status helper (Phase 2) are pure and depend only on the rulebook config and the already-built confidence band, so they slot before the store. Phases 1 and 2 are independent of each other and could be built in parallel, but are sequenced 1→2 here so the store's tests in Phase 3 have both ready.

---

## Phases

### Phase 1 — Schema Upgrade (the table + 3 optional document fields)

**Goal**: Give the system a place to store structured facts and quietly add three optional fields to the existing file records, without disturbing anything that works today.

_Implements spec Component 1 (Schema Upgrade). Open the spec for the full column list, the `002_batches.sql` precedent, and the Done-when text — not repeated here._

**Design** (folder + version, with the one expected test edit):

```
src/storage/migrations/
  001_initial.sql
  002_batches.sql        ← precedent: CREATE TABLE + ALTER in ONE file
  ...
  007_search_indexes.sql ← current last; schema_version reaches 7
  008_knowledge_entries_and_document_columns.sql   ← NEW (this phase)
        ├─ CREATE TABLE knowledge_entries (11 columns)
        └─ 3× ALTER TABLE documents ADD COLUMN (full_body, original_filename, file_size_bytes)
  → migration runner auto-applies it, bumps schema_version 7 → 8

EXPECTED test edit (research A1):
  tests/test_storage/test_migration_007.py
    line 41:  assert version == 7   →   assert version == 8
    line 56:  assert version == 7   →   assert version == 8
```

**Steps** (TDD, RED → GREEN):
1. **RED** — Write a new test (e.g. `tests/test_storage/test_migration_008.py`) that runs `init_db(tmp_path / "kb.db")` and asserts (a) the `knowledge_entries` table exists with all 11 columns named in the spec, and (b) `schema_version` reads 8. Run it — it fails (no `008` file yet). _(P5-DATA-01)_
2. **GREEN** — Author `src/storage/migrations/008_knowledge_entries_and_document_columns.sql` as a single multi-statement file (CREATE TABLE + 3 ALTER), matching the column-style conventions in `schema.sql` / `002_batches.sql`. Eleven columns: `id INTEGER PRIMARY KEY AUTOINCREMENT, dimension TEXT, entity TEXT, tag TEXT, fact TEXT, status TEXT, confidence REAL, sources TEXT, reasoning TEXT, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))`. No FK on `sources` (loose ref, OQ-P5DATA-1/2). No `NOT NULL` beyond `id`, no `CHECK` on `status` (permissive — research/spec resolved this; status vocabulary is app-enforced by the store, not the schema).
3. Add a second test (or extend) for the documents-side: after `init_db` on a DB that already holds a captured-file row, the file table shows `full_body`, `original_filename`, `file_size_bytes`; every pre-existing row reads them back as NULL; reading an existing file record via `documents._row_from_sqlite` still returns a row without error. _(P5-DATA-02)_
4. **Expected test edit (research A1, NOT a regression):** bump `tests/test_storage/test_migration_007.py` line 41 and line 56 from `assert version == 7` to `assert version == 8`. These run on every default `uv run pytest` (no skip marker); without the edit they fail the moment `008` ships. This is the standard version-pin bump every migration requires.
5. Run `uv run pytest tests/test_storage/` — green.

**Files to modify**:
- `src/storage/migrations/008_knowledge_entries_and_document_columns.sql` — NEW migration (CREATE + 3 ALTER).
- `tests/test_storage/test_migration_008.py` — NEW test for table + version + new columns.
- `tests/test_storage/test_migration_007.py` — EDIT lines 41, 56 (`7`→`8`); expected, mechanical.
- _(No change to `storage/db.py` — the runner picks up `008` automatically; `DocumentRow`/`_row_from_sqlite` are touched in Phase 3, not here.)_

**Test criteria**:
- [x] Fresh `init_db()` produces a `knowledge_entries` table with all 11 columns, `schema_version == 8`. _(P5-DATA-01)_
- [x] On a DB with a pre-existing documents row, the 3 new columns exist and read NULL; the existing row still reads without error. _(P5-DATA-02)_
- [x] `uv run pytest tests/test_storage/` green, including the two updated `test_migration_007.py` assertions.

**Notes**: Single-file `008` is the research-confirmed choice over a `008`–`011` split (the runner applies whole files via `executescript`; `002`/`007` already ship multi-statement; a split would bump to 11 and break the SAME version pins). **Known coupling:** the runner's source comment (`storage/db.py:33-34`) still advises single-statement files — stale, but **do not edit it this phase** (out of scope; logged as tech debt in research §Technical Debt).

**Status**: [x] done
**Completed**: 2026-06-12
**Notes**: Implemented as planned with one deviation: `DocumentRow`/`_row_from_sqlite` was also edited (3 trailing None-defaulted fields + 3 guarded reads) because Phase 1 Step 3 explicitly requires testing through `_row_from_sqlite`. This is a purely additive edit matching the existing guard pattern at `documents.py:59-66`. The plan's Phase 3 note about deferring this edit was contradicted by Phase 1 Step 3's test requirements — the test won.

---

### Phase 2 — Rulebook config + the two pure checks in `core/tags.py`

**Goal**: Ship the allowed-categories config file, plus (a) a check that a (dimension, tag) pair is allowed and (b) a pure helper that turns a confidence score into a `confident`/`pending` status using config thresholds.

_Implements spec Component 3 (Dimension/Tag Rulebook) + Component 4 (Tag Validator + status-mapping helper). Open the spec for the full Build text, the `validate_tags`/`load_taxonomy` mirror shape, and the Done-when text._

**Design** (two pure additions beside the rule they resemble, fed by a new data file):

```
src/config/dimensions.yaml   ← NEW data file (provisional starter taxonomy)
   people:   [ role, other ]
   projects: [ status, timeline, other ]
   domains:  [ other ]
   (header comment: "PROVISIONAL — starter set, refined in Phase 8")
        │ read by (standalone yaml.safe_load loader, mirrors load_taxonomy)
        ▼
src/core/tags.py   (EXISTING module — deepened, two new functions)
   validate_dimension_tag(dimension, tag, rulebook) -> Result
        accept: people + role        → Success
        reject: people + invented     → Failure (unknown tag in known dim)
        reject: unknown_dim + role     → Failure (unknown dimension)
        every dimension: other        → Success (mandatory catch-all)

   confidence→status helper(score, band: ConfidenceBand) -> str
        band.route(score) == AUTO              → "confident"
        band.route(score) in {SUGGEST, CLUELESS} → "pending"
        (cutoff comes from the passed band — NO float literal in if/elif)
```

**Steps** (TDD, RED → GREEN):
1. Author `src/config/dimensions.yaml` with the **provisional minimal-but-illustrative** starter taxonomy above (resolves DQ-5 for this slice): `people: [role, other]`, `projects: [status, timeline, other]`, `domains: [other]`. Every dimension's tag list **must include** `other`. Add a header comment marking the whole file provisional.
2. **RED** — Write `tests/test_core/test_dimensions.py` (new) covering: a valid pair (`people` + `role`) accepts; an invented tag for a known dimension (`people` + `xyz`) rejects; an unknown dimension (`nope` + `role`) rejects; and `other` validates as known for every configured dimension. Pass the rulebook explicitly (a pre-loaded object via the standalone loader, or a temp `dimensions.yaml` path) — **no module-scope `CONFIG` import** (C-17). Run — fails (functions don't exist). _(P5-DATA-07, P5-DATA-08)_
3. **GREEN** — In `src/core/tags.py`, add a standalone loader (a small function doing `yaml.safe_load(path.read_text())` on `dimensions.yaml`, mirroring `load_taxonomy` at `core/tags.py:118`) and `validate_dimension_tag(dimension, tag, rulebook) -> Result`, mirroring the `validate_tags` shape (`core/tags.py:56`): `Success` for an allowed pair; `Failure` for an unknown dimension **or** an unknown tag within a known dimension. Decision (resolved): the validator takes a **pre-loaded rulebook object**, and the loader is exposed so callers/future extractor load once and pass it.
4. **RED** — Add tests for the status helper: a high score (`>=` the band's `auto` cutoff) maps to `confident`; a low score (below `suggest`) maps to `pending`; pass an explicitly-constructed `ConfidenceBand(auto=..., suggest=...)` — no `CONFIG` import. Assert no float literal drives the mapping (it reuses the band). _(P5-DATA-09)_
5. **GREEN** — Add the pure status-mapping helper in/alongside `core/tags.py`: takes a confidence score + an explicit `ConfidenceBand`, calls `band.route(score)` (`core/config.py:430`), and maps `RouteDecision.AUTO → "confident"`, `RouteDecision.SUGGEST | RouteDecision.CLUELESS → "pending"` (research-confirmed mapping). No LLM call, no audit write (C-13). `retired` is NOT produced here.
6. Run `uv run pytest tests/test_core/` — green. Confirm the tag-count tests (`test_tags.py` — the `len(...) == 9` assertions) are untouched (`dimensions.yaml` is a separate file with a different top-level shape and does not feed `load_taxonomy`).

**Files to modify**:
- `src/config/dimensions.yaml` — NEW config (provisional taxonomy, `other` everywhere).
- `src/core/tags.py` — ADD standalone loader + `validate_dimension_tag()` + status-mapping helper (no existing function touched).
- `tests/test_core/test_dimensions.py` — NEW tests for validator + status helper.

**Test criteria**:
- [ ] Valid pair accepted; invented tag for a known dimension rejected; unknown dimension rejected. _(P5-DATA-07)_
- [ ] `other` validates as known for every configured dimension. _(P5-DATA-08)_
- [ ] High score → `confident`, low score → `pending`, cutoff read from the passed `ConfidenceBand` — no float literal in an `if/elif`. _(P5-DATA-09)_
- [ ] `validate_dimension_tag` and the status helper both return `Result` / a status string with no module-scope `CONFIG` import in tests (C-17).
- [ ] `uv run pytest tests/test_core/` green; `test_tags.py` count assertions still pass untouched.

**Notes**: Phase 2 is independent of Phase 1 (no DB needed) — it is sequenced second only so the store in Phase 3 has the status helper ready. **Known coupling:** the status helper's `route()→status` mapping is the one place this slice translates an existing 3-value gate (`AUTO/SUGGEST/CLUELESS`) into a 2-value status (`confident/pending`) — flagged as intentional reuse, not new logic; if the gates ever diverge a dedicated config block is a one-line future addition (DQ-4).

**Status**: [ ] pending

---

### Phase 3 — Knowledge Entry Store (the five fact operations)

**Goal**: Provide create-or-update / read-by-category / read-by-entity / retire / fetch-live-set for facts, each clearly reporting whether it worked, wired to the table (Phase 1) and the status helper (Phase 2).

_Implements spec Component 2 (Knowledge Entry Store). Open the spec for the five function descriptions, the `audit_log.py`/`documents.py` CRUD-shape reference, and the Done-when text._

**Design** (new module modelled on the audit-log CRUD shape; one row dataclass + five `Result`-returning functions):

```
src/storage/knowledge_entries.py   ← NEW module (copies audit_log.py shape)

   @dataclass KnowledgeEntry        ← mirrors AuditEntry / DocumentRow

   upsert(entry, status=None, band=?, db_path=None) -> Result[int]
        if no explicit status: status = status_helper(confidence, band)  ← Phase 2
        sources → json.dumps  (round-trip precedent: audit_log.source_ids)
        opens via get_connection (C-04)            → Success(row id)

   query_by_dimension(dimension, db_path=None) -> Result[list[KnowledgeEntry]]
        sources → json.loads back to a real list on read

   query_by_entity(entity, db_path=None) -> Result[list[KnowledgeEntry]]

   retire(entry_id, reason, db_path=None) -> Result[int]
        status='retired', reason→reasoning, refresh updated_at, NEVER delete
        returns rowcount → retiring a missing id reports "nothing changed"

   get_confident_and_pending(entity=None, dimension=None, db_path=None)
        -> Result[list[KnowledgeEntry]]   excludes 'retired'  (the live set)

   on sqlite3.Error → Failure(recoverable=False, context=...)  (audit_log shape)
```

ALSO this phase — the small additive `DocumentRow` edit the spec under-stated (research A2/A4 Edge Case):

```
src/storage/documents.py
   DocumentRow:  + full_body: str | None = None
                 + original_filename: str | None = None
                 + file_size_bytes: int | None = None   (trailing, None-defaulted)
   _row_from_sqlite:  + 3 guarded reads, e.g.
        full_body = row["full_body"] if "full_body" in row.keys() else None
   upsert() / replace_path():  UNCHANGED — WriteOutcome signature preserved (P5-DATA-10)
```

**Steps** (TDD, RED → GREEN):
1. **RED** — Write `tests/test_storage/test_knowledge_entries.py` against a temp SQLite path (no `CONFIG` import; call `init_db(tmp_path/'kb.db')` then pass that `db_path`). First test: `upsert` a fact then `query_by_dimension` returns it with `fact`, `status`, `confidence`, and a `sources` list that comes back as a **real list** (not a string); `upsert` reports `Success(new row id)`. Run — fails. _(P5-DATA-03)_
2. **GREEN** — Author `src/storage/knowledge_entries.py`: the `KnowledgeEntry` dataclass + the five functions, copying `audit_log.append`/`query` shape (`storage/audit_log.py:27,65`). Every function opens via `get_connection` (C-04), returns `Result` (C-12), `json.dumps`/`json.loads` on `sources` (A6), `sqlite3.Error → Failure(recoverable=False)`. `upsert` maps confidence→status via the Phase 2 helper **unless** an explicit status is passed; it accepts an optional `db_path` (defaults to the config singleton like the other CRUD modules) and an explicit `ConfidenceBand` so the status mapping is testable without `CONFIG` at module scope (C-17). **No DDL** in this file (C-05); **no LLM call / audit write** (C-13). Decision (resolved, DQ-2 deferred to Phase 8): `upsert` uniqueness is **id-only** for this slice — a fresh insert returns a new id; update is by id. No natural-key index.
3. **RED→GREEN** — Add a test: two facts for one entity under different tags + one for a different entity; `query_by_entity(first_entity)` returns only that entity's two facts (each with its own tag/fact), excluding the other. _(P5-DATA-04)_
4. **RED→GREEN** — Add a test: `retire` a stored fact leaves the row present, flips status to `retired`, records the reason into `reasoning`, refreshes `updated_at`, never deletes; retiring a missing id returns a clean "nothing changed" (rowcount 0), not a crash. _(P5-DATA-05)_
5. **RED→GREEN** — Add a test: store one confident, one pending, one retired fact for an entity; `get_confident_and_pending` returns the confident + pending, excludes the retired. Decision (resolved): make `entity`/`dimension` **optional filter args** so the global live set is also reachable (P5-DATA-06 phrases it "for that entity (or dimension)"). _(P5-DATA-06)_
6. Add the three trailing optional fields to `DocumentRow` and three guarded reads to `_row_from_sqlite` (research A2/A4 Edge Case — additive, matches the existing `batch_id`/`project`/`status`/`key_topics` guard pattern at `documents.py:59-66`). Leave `upsert()`/`replace_path()` untouched. Add/confirm a test that reads a documents row and sees the three fields default to `None`.
7. Run `uv run pytest tests/test_storage/` — green.

**Files to modify**:
- `src/storage/knowledge_entries.py` — NEW module (5 ops + dataclass).
- `src/storage/documents.py` — ADD 3 trailing optional fields to `DocumentRow` + 3 guarded reads in `_row_from_sqlite`; `upsert`/`replace_path` UNCHANGED.
- `tests/test_storage/test_knowledge_entries.py` — NEW tests for all five ops.

**Test criteria**:
- [ ] Store then read-by-dimension returns the fact intact; `sources` round-trips as a real list; create reports the new row id. _(P5-DATA-03)_
- [ ] Read-by-entity returns only the queried entity's facts, across its tags, excluding others. _(P5-DATA-04)_
- [ ] Retire keeps the row, flips to `retired`, records the reason, refreshes the timestamp; missing id → clean "nothing changed". _(P5-DATA-05)_
- [ ] Fetch-live-set returns confident + pending, excludes retired. _(P5-DATA-06)_
- [ ] `DocumentRow` reads the 3 new fields as `None` on pre-existing rows; `documents.upsert()` still accepts a `WriteOutcome` (signature unchanged). _(P5-DATA-10, in part)_
- [ ] `uv run pytest tests/test_storage/` green.

**Notes**: This is a genuine deep module (delete it and the SQL + JSON round-trip + Result-wrapping reappears in every future caller). **Known coupling:** `upsert` depends on the Phase 2 status helper and on an explicit `ConfidenceBand` — the band is passed in (not imported at module scope) to keep tests `CONFIG`-free (C-17) and the helper config-driven (C-06). No `Protocol`/seam (C-15) — one real future caller pattern.

**Status**: [x] done
**Completed**: 2026-06-12
**Notes**: Implemented as planned. Two new files: `src/storage/knowledge_entries.py` (KnowledgeEntry dataclass + 5 CRUD functions: upsert, query_by_dimension, query_by_entity, retire, get_confident_and_pending) and `tests/test_storage/test_knowledge_entries.py` (6 tests covering all behaviors P5-DATA-03 through P5-DATA-06). All functions return Result, open via get_connection, json.dumps/loads for sources round-trip. upsert derives status from confidence_to_status() when band is provided. retire never deletes. get_confident_and_pending accepts optional entity/dimension filters. No existing files modified. Storage test suite: 82 passed.

---

### Phase 4 — Suite-wide green check

**Goal**: Confirm the whole slice is additive — the entire existing suite stays green except the two expected version-pin edits already made in Phase 1, and `documents.upsert()` still accepts a `WriteOutcome`.

_Implements spec "Suite-wide acceptance". Open the spec for the exact acceptance text._

**Design** (the one expected delta vs. everything-else-untouched):

```
uv run pytest    →    ALL green
   EXCEPT (expected, done in Phase 1):
     test_migration_007.py:41,56   7 → 8
   NO other existing test rewritten or deleted.
   documents.upsert()  still takes a WriteOutcome  (signature unchanged)
```

**Steps**:
1. Run the full suite: `uv run pytest`. Everything passes; the only diff vs. the pre-slice baseline is the two `test_migration_007.py` assertions updated in Phase 1.
2. Confirm no *other* existing test was rewritten or deleted (`git diff --stat` on `tests/` shows only the two new test files + the 2-line `test_migration_007.py` edit).
3. Confirm `documents.upsert()` and `replace_path()` still take a `WriteOutcome` (grep their signatures; unchanged).
4. Run `uv run ruff check .` and `uv run ruff format --check .` — clean.

**Files to modify**: none (verification phase).

**Test criteria**:
- [ ] `uv run pytest` green; only the two `test_migration_007.py` version-pin assertions differ from baseline; no other existing test rewritten/deleted; `documents.upsert()` signature unchanged. _(P5-DATA-10)_
- [ ] `ruff check` and `ruff format --check` clean.

**Status**: [ ] pending

---

## Open Questions

None block this slice. All decisions the spec/design left "leaning" are resolved by the research doc's recommendations (trusted per the planning brief) and recorded inline in the phases above:
- Single-file `008` (not a `008`–`011` split) — Phase 1.
- Permissive schema (no `NOT NULL` beyond `id`, no `CHECK` on `status`) — Phase 1.
- `route()→status` mapping: `AUTO → confident`, `SUGGEST + CLUELESS → pending` — Phase 2.
- Validator takes a pre-loaded rulebook object (loader exposed) — Phase 2.
- Provisional starter tags: `people: [role, other]`, `projects: [status, timeline, other]`, `domains: [other]` — Phase 2.
- `upsert` uniqueness is id-only this slice (natural-key dedupe deferred to Phase 8, DQ-2) — Phase 3.
- `get_confident_and_pending` takes optional entity/dimension filters — Phase 3.

Deferred (recorded for downstream, none blocking): DQ-1/OQ-P5DATA-4 entity normalization (Phase 8), DQ-2 `upsert` dedupe rule (Phase 8), DQ-3 knowledge query path / `kms_knowledge` (unassigned), DQ-4 dedicated status-threshold block (future, one-line), DQ-5 final starter tags (Phase 8 refines).

## Out of Scope

Per ADR-0012 and the spec's Out-of-scope section — do not build any of these in this slice:
- The AI fact extractor / any producer of knowledge entries (Phase 8).
- Populating `full_body` / `original_filename` / `file_size_bytes` (Phase 7 — they ship NULL here).
- Redesigning `documents.upsert()` / `replace_path()` (Phase 7 — `WriteOutcome` signature preserved).
- A junction/link table for fact↔document, or a hard FK from facts to documents (sources stay inline JSON, loose ref by internal id).
- Entity name normalization.
- A dedicated status-threshold config block (reuse the existing `ConfidenceBand`).
- A knowledge query path / MCP tool (`kms_knowledge`).
- Splitting the config / removing vault root (config stays whole; standalone loader).
- Retiring/deleting any existing module (`writer`, `frontmatter`, `reader`, `indexer`, `move_guard`, `_move`).
- Backfilling existing documents rows.
- Editing the stale runner comment at `storage/db.py:33-34` (tech debt, not this slice).
