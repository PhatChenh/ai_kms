# Phase 5 Slice 1 — Data/Config Foundation (Spec)

_Created: 2026-06-12_
_Source design (LOCKED): `docs/1_design/P5_slice1_data_foundation.md`_
_Sequencing law (binding): `docs/architecture/system_adr/0012-additive-rearchitecture-defer-breaking-changes-to-consumer-refactor-phases.md` (ADR-0012)_
_Success criteria: `docs/system_behavior/behavior_inventory.yaml` entries **P5-DATA-01 … P5-DATA-10** (origin: design). This spec REFERENCES those by ID — it does not restate them._

> **Reader note.** Every section leads in plain English. Code references (file paths, function names, line numbers) sit in parentheses or sub-bullets and are anchors for the engineer. Delete every `code`-formatted token and the prose still reads correctly.

---

## Purpose

This slice builds the three foundations the cloud-native "memory" needs, and nothing else. Today the system remembers what it learned by writing notes into files in the user's vault; the rearchitecture moves that memory into the database as a pile of small, structured facts ("Anthony is the Product Lead for Movie Q2"). This slice does **not** build the AI that extracts those facts (a later phase). It builds only: (1) the **place to put the facts** — a new database table plus three new optional fields on the existing document records; (2) the **basic create / read / retire operations** for working with those facts; and (3) the **rulebook** that says which fact categories the AI is allowed to use.

After this slice, the system can store, read back, and retire structured facts via code, and can check a fact's category against an approved list — but nothing produces facts yet, and nothing the system does today changes. Every new database field is optional, so old records keep working untouched, and the existing test suite stays green.

---

## Already built (reuse, do not rebuild)

| Function / Module | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| `get_connection` (context manager) | `storage/db.py:75` | Opens one database connection, commits on clean exit, rolls back on error, closes always. Applies the foreign-key pragma via `_connect`. | The new fact toolbox opens **every** connection through this — never raw `sqlite3.connect`. Satisfies C-04 transitively. | deep |
| `_connect` | `storage/db.py:16` | Sets `PRAGMA foreign_keys=ON`, WAL mode, loads the vector extension. | Reused unchanged. This is where C-04 lives. | deep |
| `_run_migrations` | `storage/db.py:29` | Applies every `NNN_*.sql` file with a number higher than the stored schema version, in numeric order, via `executescript`, then bumps the version. | Runs the new upgrade automatically; no code change needed in the runner. | deep |
| `init_db` | `storage/db.py:49` | Creates the base schema, then runs migrations. | The new upgrade rides this path; behavior P5-DATA-01/02 verify the table and columns appear after it runs. | deep |
| `Success` / `Failure` / `Result` | `core/result.py:21,37,91` | The standard "did it work?" wrapper every public operation returns. | All five fact operations and the tag-pair check return these (C-12). | deep |
| `append` (audit CRUD) | `storage/audit_log.py:27` | Reference CRUD shape: `get_connection`, `json.dumps` on a list column (`source_ids`), `Result` return, `sqlite3.Error` → `Failure(recoverable=False)`. | The fact toolbox copies this exact shape for its JSON list column (`sources`). | deep |
| `query` (audit CRUD) | `storage/audit_log.py:65` | Reference read shape: builds WHERE clauses, `json.loads` the list column back to a Python list on read. | The fact read operations copy this round-trip pattern. | deep |
| `DocumentRow` + `_row_from_sqlite` | `storage/documents.py:27,47` | The existing file-record dataclass and its `SELECT *` reader, which guards every optional column with `if "<col>" in row.keys()`. | The 3 new optional fields are added here as trailing `None`-defaulted optionals, read by the same guard. | deep |
| `upsert` / `replace_path` (documents) | `storage/documents.py:90,232` | Write paths for file records; both take a `WriteOutcome`. | **Left untouched.** Adding nullable columns forces no change (the INSERT lists columns explicitly; omitted columns default to NULL). P5-DATA-10 asserts the `WriteOutcome` signature is unchanged. | deep |
| `ConfidenceBand` + `.route()` | `core/config.py:396,430` | Maps a score to `AUTO / SUGGEST / CLUELESS` against config thresholds (`auto`, `suggest`). The single authoritative routing gate. | Reused to map a fact's confidence to `confident / pending` — relabelled outputs, same machinery, thresholds stay in config (C-06). | deep |
| `Thresholds.for_pipeline()` | `core/config.py:465` | Returns the threshold band for a named pipeline, falling back to global. | The status-mapping helper accepts an explicit `ConfidenceBand` (caller obtains it here) so tests pass it directly without importing `CONFIG` at module scope (C-17). | deep |
| `route()` (confidence wrapper) | `core/confidence.py:51` | Pure function: takes an `AIDecision` + a `ConfidenceBand`, returns the routing outcome, logs at DEBUG, no side effects. | Pattern reference for the status helper (pure, threshold-in-argument). The helper may reuse `ConfidenceBand.route()` directly. | deep |
| `validate_tags` + `load_taxonomy` + `TagTaxonomy` | `core/tags.py:56,118,21` | The existing tag-rules checker: validates tags against a loaded vocabulary, returns valid/violation lists; `load_taxonomy` reads a YAML file with `yaml.safe_load(path.read_text())`. | The new `validate_dimension_tag` + its standalone loader live in this same module, mirroring this load + validate shape. | deep |
| Migration runner precedent | `storage/migrations/002_batches.sql` | A single migration file that ships `CREATE TABLE` **plus** `ALTER TABLE … ADD COLUMN` together, applied via `executescript`. | Proves the single-file-multi-statement shape for upgrade 008 works. | deep |
| Live config directory | `src/config/` (resolved by `core/config.py:37` `_PROJECT_ROOT / "config"`) | Where `tags.yaml`, `thresholds.yaml`, `config.yaml`, `routing.yaml` live and are loaded from. | The new `dimensions.yaml` lands here, alongside `tags.yaml`. | shallow |
| `core/audit.write` | `core/audit.py` | Records an AI decision in the audit log. | **Not called this slice** — Slice 1 makes no AI decision (CRUD + config only). Listed so the planner does not wire it in. (C-13 not triggered; verify no LLM call sneaks in.) | deep |

---

## Q1 Diagram (from design)

Verbatim from the design doc (`docs/1_design/P5_slice1_data_foundation.md` §Q1). This is the "what happens inside" view the spec builds on.

```
# Knowledge Storage Foundation — What Happens Inside
Scope: Shows how one document's facts become rows in the single knowledge table.
       Slice 1 builds the SOLID boxes (the table, create/read/retire, and the
       dimension/tag rulebook). The DASHED box (AI extraction) is a later phase,
       shown only for context.

How to read this:
  Solid boxes  = built in this slice
  Dashed box   = future phase, shown for context
  Arrows       = what happens next
  Fork         = a decision with different outcomes

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

Simplified: The confidence-cutoff numbers live in a config file, not shown here.
            "Save as confident/pending" both write to the same one table — split
            only to show the status decision. The dimension/tag check and the
            confidence check run on every fact; drawn once to stay readable.
```

---

## Q2 Diagram — How it connects to others

New for this spec. Same plain-English names, positions, and arrow conventions as Q1. Solid = built this slice or already exists; dashed = future-phase call or loose/soft reference.

```
# Knowledge Storage Foundation — How It Connects
Scope: Shows how the new pieces built this slice (the Knowledge Entry Store,
       the Knowledge Table, the Schema Upgrade, and the Dimension/Tag Rulebook)
       connect to existing and future components. Does NOT show the internal
       save/retire steps — see Q1 for that.

How to read this:
  Center box     = the main new piece built this slice
  Solid boxes    = components that already exist or are built this slice
  Dashed boxes   = future-phase, shown for context
  Solid arrows   = a real, wired connection
  Dashed arrows  = a future call OR a loose/soft reference (no hard DB link)

                  ┌────────────────────┐        ┌─────────────────────┐
                  │ Database Setup     │        │ Dimension/Tag       │
                  │ Starts the DB,     │        │ Rulebook            │
                  │ runs upgrades      │        │ Lists allowed fact  │
                  └─────────┬──────────┘        │ categories + tags   │
                            │ runs              └──────────┬──────────┘
                            ▼                              │ read by
                  ┌────────────────────┐                  ▼
                  │ Schema Upgrade     │       ┌─────────────────────┐
                  │ Creates the table, │       │ Tag Validator       │
                  │ adds 3 optional    │       │ Is this category+   │
                  │ fields             │       │ tag allowed?        │
                  └────┬──────────┬────┘       └──────────┬──────────┘
              creates  │          │ adds 3 fields          ▲
                       │          │ (additive only)        │ checks each
                       ▼          ▼                        │ fact's category
            ┌───────────────┐  ┌────────────────┐          │
            │ Knowledge     │  │ Document       │   ┌ ─ ─ ─ ┴ ─ ─ ─ ─ ┐
            │ Table         │  │ Catalog        │   │ Fact Extractor   │
            │ One fact per  │  │ Captured files,│   │ (FUTURE)         │
            │ row           │  │ +3 optional    │   │ Makes facts from │
            └───────▲───────┘  └───────▲────────┘   │ documents        │
                    │ reads/writes     ┊            └ ─ ─ ─ ┬ ─ ─ ─ ─ ┘
                    │ (via helper)     ┊ loose ref          │ will call
                    │                  ┊ by internal id     │ to save facts
         ┌──────────┴───────────┐      ┊                    │
         │ Knowledge Entry      │ ─ ─ ─┘                    │
         │ Store                │ ◄────────────────────────┘
         │ Create / read /      │
         │ retire facts         │
         └───┬──────────────┬───┘
   opens via │              │ labels each fact
             ▼              ▼  confident / pending
   ┌──────────────────┐  ┌────────────────────┐
   │ Database         │  │ Confidence Gate    │
   │ Connection       │  │ Turns a score into │
   │ Helper           │  │ a trust level      │
   └──────────────────┘  │ (cutoffs in config)│
                         └────────────────────┘

  Every operation the Knowledge Entry Store performs hands back a
  Success/Failure Wrapper (the standard "did it work?" result) — omitted
  as a box above to stay readable; it wraps every arrow leaving the Store.

Simplified: The Success/Failure Wrapper is described in the note above rather
            than drawn as a 7th spoke. The Schema Upgrade's two outputs (creating
            the Knowledge Table and extending the Document Catalog) share one tier.
            The dashed arrows mean two different things — a FUTURE call (Fact
            Extractor → Store, and Fact Extractor → Tag Validator) and a loose
            soft reference (a fact's sources point at Document Catalog rows by
            internal id, with no hard database link). Both stay dashed so the
            reader sees "not a solid wired-now connection" at a glance.
```

**Plain-English component glossary** (diagram name → what it is in code):

| Diagram name | In code |
|---|---|
| Knowledge Entry Store | `src/storage/knowledge_entries.py` (new) |
| Knowledge Table | `knowledge_entries` table (new, via upgrade 008) |
| Schema Upgrade | `src/storage/migrations/008_*.sql` (new) |
| Document Catalog | `src/storage/documents.py` + `documents` table (existing, +3 optional fields) |
| Dimension/Tag Rulebook | `src/config/dimensions.yaml` (new) |
| Tag Validator | `validate_dimension_tag(...)` in `src/core/tags.py` (new function in existing module) |
| Confidence Gate | `ConfidenceBand` / `.route()` in `src/core/config.py` + thresholds (existing, reused) |
| Database Connection Helper | `get_connection` in `src/storage/db.py` (existing) |
| Success/Failure Wrapper | `Success` / `Failure` in `src/core/result.py` (existing) |
| Database Setup | `init_db` + `_run_migrations` in `src/storage/db.py` (existing) |
| Fact Extractor (FUTURE) | the later-phase classify/extraction AI (not built this slice) |

---

## Feature overview

Three additive pieces, each independent enough to build and test on its own.

**1. The place to put the facts — one database upgrade.**
A single new upgrade step (the next one after the search-index upgrade) does two things when the database starts up: it creates a brand-new table that holds one structured fact per row, and it adds three new optional fields to the existing table of captured files. The new fact table records, for each fact: a unique id; which broad category it belongs to (its "dimension" — e.g. people, projects, domains); who or what it is about (its "entity" — free text like "Anthony"); a finer label within that category (its "tag" — e.g. "role"); the fact itself in plain words; whether the fact is trusted or flagged for review (its "status"); how sure we are (a confidence score); the list of source documents it came from (stored as a plain list inside the row); the AI's reasoning; and timestamps for when it was created and last changed. The three new optional fields on the file table are the full text of a file, the file's original name, and its size in bytes — all left empty for now (a later phase fills them). Because they are optional, every file record that already exists keeps working with these fields simply empty; nothing is re-saved.

**2. The toolbox for working with facts — five operations.**
A new code module offers five operations. **Create-or-update** stores a fact (and decides, from its confidence score, whether to mark it trusted or flagged). **Read by category** returns every fact in a given dimension. **Read by entity** returns every fact about a given person/project/thing, across all its finer labels. **Retire** flips a fact's status to "retired" with a reason and never deletes the row — history is preserved. **Fetch the live set** returns the trusted and flagged facts (but never the retired ones) — this is the set a future extraction step will feed to the AI. Every one of these operations clearly reports whether it worked.

**3. The rulebook — one config file plus one check.**
A new config file lists the allowed fact categories (dimensions) and, for each, the finer labels (tags) the AI may use. Every category carries a mandatory catch-all label called "other" so the AI always has a valid fallback. The starter list is marked provisional — it is a first draft to be refined later. A new check confirms that a given (category, tag) pair is actually allowed: it accepts a valid pair, and rejects both an unknown category and an unknown tag within a known category. The check reports its result the same did-it-work way as everything else.

**Happy path.** A future extraction step (not built here) produces a fact, checks its (dimension, tag) pair against the rulebook, hands the fact to the create-or-update operation, which labels it trusted-or-flagged from its confidence score and writes one row. Later, reading by category or entity returns it intact, with its source list round-tripping back as a real list. When newer information supersedes it, retire flips it to retired with a reason; it then drops out of the live set but stays in the table for history.

**Edge cases.** An unknown category or tag is rejected by the rulebook check before any write. A confidence score exactly on a cutoff follows the existing gate's rule (`>=` is trusted). Retiring an id that does not exist reports a clean "nothing changed" rather than crashing. A database error in any operation comes back as a clear failure, not a silent swallow.

---

## Out of scope

Concrete things a reader might assume are included but are not:

- **The AI that extracts facts** — no pipeline produces knowledge entries in this slice; the toolbox has no live caller yet. _Handled by Phase 8 (classify)._
- **Populating the three new file fields** (`full_body`, `original_filename`, `file_size_bytes`) — they ship empty (NULL) and stay empty this slice. _Population rides with Phase 7 (`documents.upsert()` redesign)._
- **Redesigning `documents.upsert()` / `replace_path()`** — their `WriteOutcome` signature stays exactly as-is. _Deferred to Phase 7 per ADR-0012; building the new signature now is an interface ahead of its consumer (C-15)._
- **A junction/linking table for fact-to-document connections** — sources are stored inline as a JSON list, not a separate table. _Resolved as OQ-P5DATA-1 (inline); a link table can be added later without data loss if a hot query ever needs it._
- **Hard foreign key from facts to documents** — the source reference is a loose soft reference by internal id, not a database-enforced link. _Resolved as OQ-P5DATA-1/2; chosen so facts survive the user moving files._
- **Entity name normalization** ("Anthony" vs "Anthony Nguyen") — entity is stored as free text; deciding whether two spellings are the same is not done here. _Deferred — Phase 8 open question (OQ-P5DATA-4); see Deferred Questions._
- **A new dedicated status-threshold config block** — the confident/pending cutoff reuses the existing confidence band. _Resolved as OQ-P5DATA-3 (reuse); a dedicated block is a one-line future addition if the gates diverge._
- **A query path / MCP tool for knowledge (e.g. `kms_knowledge`)** — no chat-facing or search-facing access to facts this slice. _Deferred — no phase assigned yet (rearch doc §14 Q8 undecided)._
- **Splitting the config / removing vault root from config** — config stays whole; the new file loads via a standalone loader. _Deferred to Phases 6/7/9 per ADR-0012._
- **Retiring/deleting any existing module** (`writer`, `frontmatter`, `reader`, `indexer`, `move_guard`, `_move`) — none touched. _Each dies with its last consumer in Phase 6/7/9 per ADR-0012._
- **Backfilling existing documents rows** — clean slate; no backfill (rearch doc §32/§61). The three new fields read NULL on every pre-existing row.

---

## Constraints

Non-negotiable rules this build must respect. Every one is from the design doc's Guardrail Checklist (run via `/guardrail-check Review`) or ADR-0012. Treated as hard stops.

- **C-04 — Foreign-key pragma on every new connection.** The fact toolbox must open every connection through `get_connection` (`storage/db.py:75`), never raw `sqlite3.connect`. The pragma lives in `_connect`. — source: Guardrail Checklist (DB Integrity)
- **C-05 — All schema changes via versioned `.sql` deltas.** The table + 3 columns land as one new `008_*.sql` migration file. No `CREATE`/`ALTER`/`DROP` inside any `.py` file (none in `knowledge_entries.py`). — source: Guardrail Checklist (DB Integrity); `storage/db.py:33` runner comment
- **C-06 — Confidence thresholds live in config, never in code.** The confidence→status mapping reads its cutoff from config (`ConfidenceBand`), not a float literal in an `if/elif`. — source: Guardrail Checklist (LLM & Providers)
- **C-12 — Public functions return `Success` or `Failure`.** All five fact operations **and** `validate_dimension_tag()` return `Result` types. — source: Guardrail Checklist (Architecture); CLAUDE.md Result Type pattern
- **C-13 — Audit log is non-negotiable for AI decisions.** Slice 1 makes NO AI decision (CRUD + config only, no LLM call) — no `audit.write` is required here. Verify no LLM call sneaks in. — source: Guardrail Checklist (Architecture)
- **C-17 — Never import `CONFIG` at module scope in tests.** New tests pass explicit `db_path` / config paths / an explicit `ConfidenceBand`; no module-scope `CONFIG` import. (Hook-enforced: `^from core.config import CONFIG` unindented in `tests/**` is blocked.) — source: Guardrail Checklist (Testing); CLAUDE.md
- **C-15 — No interface ahead of its consumer.** Do not redesign `documents.upsert()` or build a knowledge query path this slice — no live consumer exists yet. — source: ADR-0012; design Options-explored rejected list
- **Additive-only / minimal existing-test impact.** New tables/columns are nullable; new modules and config files only; `documents.upsert()` keeps its `WriteOutcome` signature. **One expected exception (research finding, 2026-06-12):** a new migration bumps the schema version, so the two version-pin assertions in `tests/test_storage/test_migration_007.py` (lines 41, 56, `assert version == 7`) must be updated to `8`. This is the normal, mechanical update every migration requires — NOT a regression and NOT a redesign. With those two edits, `uv run pytest` stays green. No *other* existing test is touched or rewritten. — source: ADR-0012 (sequencing contract) + research A1 (`docs/3_research/P5_slice1_data_foundation.md`)

---

## Assumptions

Each is a falsifiable claim about existing code, traced to a design implication. Research verifies each before planning.

| ID | Assumption | Source implication | What would prove it wrong |
|----|-----------|-------------------|--------------------------|
| A1 | The migration runner (`_run_migrations`, `storage/db.py:29`) applies a **multi-statement** `008_*.sql` (CREATE TABLE + 3× ALTER) cleanly via `executescript` and bumps the stored version to 8, exactly as `002_batches.sql` (CREATE + ALTER) already does. | design implication #1 + Risks "Migration shape" | A multi-statement `008` fails to apply, or leaves `schema_version` < 8, on a fresh `init_db`. (Fallback: split into `008`–`011` single-statement files — identical behavior.) |
| | **RESEARCH VERDICT (2026-06-12): mechanism VALIDATED, single-file `008` confirmed; BUT the "zero existing-test breakage" framing was INVALIDATED (partial).** Bumping to version 8 fails the two version-pin assertions in `tests/test_storage/test_migration_007.py:41,56`. Mechanical fix (bump `7`→`8`) is now a required build step above. No design change. See `docs/3_research/P5_slice1_data_foundation.md`. |
| A2 | `documents` reads via `SELECT *` and `_row_from_sqlite` guards every optional column with `if "<col>" in row.keys()` (`storage/documents.py:47-67`), so adding three nullable columns forces **no change** to `upsert()`/`replace_path()`. | design implication #2 | Adding the columns breaks an existing documents read/write, or requires editing `upsert()`/`replace_path()` to keep tests green. |
| A3 | `ConfidenceBand.route()` (`core/config.py:430`) can be driven by passing an explicit `ConfidenceBand` instance — no `CONFIG` singleton import needed at the helper's module scope or in its tests. | design implication #5 + Risks "Confidence-threshold reuse" | The only way to obtain a working `ConfidenceBand` for the helper drags in the `CONFIG` singleton at import time (violating C-17 in tests). |
| A4 | Adding `full_body`, `original_filename`, `file_size_bytes` as trailing `None`-defaulted optionals to `DocumentRow` breaks no existing test, because every existing `DocumentRow(...)` construction is keyword-based and stops at or before the current last field (`key_topics`). | design Secondary-decisions + Risks "DocumentRow field addition" | A test constructs `DocumentRow(...)` positionally passing all current fields such that appended optionals shift a positional argument. (Checked: `tests/test_storage/test_documents.py:223`, `tests/test_vault/test_watcher.py:1159`, `test_watcher_rehome.py:78`, `test_watcher_settle.py:73` — all keyword/dict, none positional past `batch_id`.) |
| A5 | A standalone loader reading `dimensions.yaml` with `yaml.safe_load(path.read_text())` (mirroring `load_taxonomy`, `core/tags.py:118`) works without the deferred config split and without touching `core/config.py`. | design implication #4 + Risks "dimensions.yaml load path" | Loading the file cleanly requires adding a field to `MainConfig` or otherwise touching `core/config.py` / the config split. |
| A6 | Storing `sources` as a JSON-array TEXT column round-trips to a Python list with `json.dumps` on write / `json.loads` on read, exactly as `audit_log.source_ids` (`storage/audit_log.py:47,102`) and `documents.key_topics` (`storage/documents.py:63,86`) already do. | design implication #3 + Risks "sources JSON round-trip" | The column reads back as a string (not a list), or the established JSON precedent does not apply to a `TEXT` column the same way. |
| A7 | The live config directory loaded at runtime is `src/config/` (`core/config.py:37` `_PROJECT_ROOT / "config"`, `_PROJECT_ROOT = src/`), so `dimensions.yaml` belongs there alongside `tags.yaml`. | design Secondary-decisions (loader) | Code loads config from the repo-root `config/` directory instead of `src/config/` at runtime. |

---

## Component dependency order

Documents what must exist before each component can work — not the order a developer writes code. Execution order is owned by `/plan-from-specs`. All three components are largely independent; the only ordering constraint is that the toolbox's tests need the table to exist (the upgrade), and the toolbox's status helper needs the confidence band (already built).

### 1. Schema Upgrade — the new table + three optional file fields

**Goal.** Give the system a place to store structured facts, and quietly add three optional fields to the existing file records, without disturbing anything that already works.

**Build.** Add ONE new migration file (`src/storage/migrations/008_*.sql`, e.g. `008_knowledge_entries_and_document_columns.sql` — the next number after `007_search_indexes.sql`). It does two things in one file (precedent: `002_batches.sql`):
- **Create** the `knowledge_entries` table with eleven columns: `id` (integer primary key, autoincrement), `dimension` (text), `entity` (text), `tag` (text), `fact` (text), `status` (text), `confidence` (real), `sources` (text — a JSON array), `reasoning` (text), `created_at` (text, default `datetime('now')`), `updated_at` (text, default `datetime('now')`). Match the column-style conventions already used in `schema.sql` and `002_batches.sql`. No foreign key on `sources` (loose reference per OQ-P5DATA-1/2).
- **Add** three nullable columns to `documents`: `full_body TEXT`, `original_filename TEXT`, `file_size_bytes INTEGER`. All NULL by default; nothing populates them this slice.

The migration runner picks the file up automatically and bumps `schema_version` to 8 — no runner change.

**Also update (research A1, required):** bump the two version-pin assertions in `tests/test_storage/test_migration_007.py` (lines 41 and 56) from `assert version == 7` to `8`. These tests run on every default `uv run pytest` (no skip marker), so without this edit they fail the moment migration 008 ships. This is the normal version-pin update any migration requires — a mechanical 2-line edit, not a regression.

**Depends on.** None (additive).

**Assumes.** A1, A2.

**Interface shape.** No code interface — this is a declarative `.sql` delta consumed by the existing runner. The "interface" is the resulting schema: the eleven-column table and the three new nullable columns.

**Decisions.**
- Q: Single multi-statement `008` file vs four single-statement files (`008`–`011`)? Options: [single / four]. Leaning **single** because `002_batches.sql` proves `executescript` handles CREATE + ALTER together and the design chose it; the fallback is documented and behavior-identical. Resolve in research per Risk "Migration shape".
- Q: Should any column carry `NOT NULL` or a `CHECK` (e.g. `status IN (...)`)? Options: [permissive / constrained]. Leaning **permissive** (no NOT NULL beyond `id`, no CHECK) because the design lists plain columns and status values are app-enforced by the toolbox; a CHECK would hard-code the status vocabulary in schema. Flag in research.

**Done when.**
- After initializing a fresh database, inspecting the schema shows a `knowledge_entries` table with all eleven columns named above, and the stored schema version reads 8. (P5-DATA-01)
- After initializing a database that already holds captured-file records, the file table shows the three new fields, every pre-existing row reads them back as empty (NULL), and reading an existing file record still returns a record without error. (P5-DATA-02)

---

### 2. Knowledge Entry Store — the five fact operations

**Goal.** Provide the create-or-update / read-by-category / read-by-entity / retire / fetch-live-set operations for facts, each clearly reporting whether it worked.

**Build.** Add a new module `src/storage/knowledge_entries.py`, modelled on the CRUD shape of `storage/audit_log.py` and `storage/documents.py`. It defines a small row dataclass (mirroring `AuditEntry` / `DocumentRow`) for a fact, and five public functions, each opening its connection through `get_connection` (C-04) and returning `Result` (C-12). It contains **no** `CREATE`/`ALTER`/`DROP` DDL (C-05) and makes **no** LLM call / audit write (C-13).
- **`upsert(...)`** — insert or update one fact. Before writing, it maps the fact's confidence score to a status of `confident` or `pending` using the status-mapping helper (Component 4) — except when an explicit status is supplied (e.g. `retired` is set by `retire`, never by confidence). Serializes `sources` with `json.dumps` (A6). Returns `Success(row id)`.
- **`query_by_dimension(dimension, ...)`** — return all facts in one dimension; `json.loads` the `sources` column back to a list on read (A6).
- **`query_by_entity(entity, ...)`** — return all facts about one entity, across all its tags.
- **`retire(entry_id, reason, ...)`** — set the row's status to `retired`, record `reason` into `reasoning`, refresh `updated_at`; never delete the row. Returns a clean result (e.g. rowcount) so retiring a missing id reports "nothing changed," not a crash (mirror `documents.delete_by_path` returning `Result[int]`).
- **`get_confident_and_pending(...)`** — return facts whose status is `confident` or `pending`, excluding `retired`. This is the live set for future extraction input.

Each function accepts an optional `db_path` override (defaulting to the config singleton like the existing CRUD modules) so tests pass a temp path without importing `CONFIG` at module scope (C-17). On `sqlite3.Error`, return `Failure(recoverable=False, context=...)`, matching `audit_log.append`.

**Depends on.** Component 1 (the table must exist for these to read/write). Component 4 (the status helper) for `upsert`'s confidence→status mapping.

**Assumes.** A6 (JSON round-trip), A3 (status helper drivable without CONFIG at scope).

**Interface shape.** Callers see five plain functions returning `Result` plus the fact row dataclass; hidden behind them is the SQL, the JSON serialize/deserialize, and the status mapping. This is a genuine deep module — delete it and the SQL + JSON round-trip + Result-wrapping reappears in every future caller (classify, web UI, knowledge tool). No `Protocol`/`ABC` introduced — concrete functions with one real future caller pattern, so no speculative seam.

**Dependency category.** in-process (test directly against a temp SQLite db path).

**Decisions.**
- Q: Is `upsert`'s uniqueness key the row `id` only, or a natural key like (dimension, entity, tag)? Options: [id-only insert-or-replace / natural-key dedupe]. Leaning **id-only for Slice 1** (a fresh insert returns a new id; update is by id) because there is no live producer yet to define a dedupe rule, and a natural-key constraint is a schema decision that would belong in Component 1. Flag for research; revisit when Phase 8 defines how the extractor re-files repeat facts.
- Q: Does `get_confident_and_pending` filter by entity/dimension or return the global live set? Options: [filterable / global]. Leaning **filterable with optional args** because P5-DATA-06's step phrases it "for that entity (or dimension)"; keep both query parameters optional so the global set is also reachable.

**Done when.**
- Storing a fact then reading by its dimension returns it with fact, status, confidence, and source list intact, and the source list comes back as a real list (not a string); the create operation reports success with the new row id. (P5-DATA-03)
- Storing two facts for one entity under different tags plus one for a different entity, then reading by the first entity, returns only that entity's two facts (each with its own tag and fact), excluding the other entity. (P5-DATA-04)
- Retiring a stored fact leaves the row present, flips its status to retired, records the supplied reason, and refreshes the last-changed timestamp — the row is never deleted. (P5-DATA-05)
- Storing one confident, one pending, and one retired fact for an entity, then fetching the live set, returns the confident and pending facts and excludes the retired one. (P5-DATA-06)

---

### 3. Dimension/Tag Rulebook — the config file

**Goal.** Give the system an administrator-controlled list of allowed fact categories and their tags, so the AI cannot invent its own categories, with a mandatory catch-all everywhere.

**Build.** Add a new config file `src/config/dimensions.yaml` (in the live config directory, A7), listing a starter taxonomy of dimensions `people`, `projects`, `domains`. Each dimension maps to its set of allowed tags, and every dimension's tag set **must include** a mandatory `other` catch-all. Mark the whole file provisional (a header comment stating the starter set is a first draft to be refined later) so no reader mistakes it for a frozen vocabulary.

**Depends on.** None (additive config file).

**Assumes.** A7.

**Interface shape.** A data file, no code interface. Its shape (dimension → list of tags, with `other` present everywhere) is the contract Component 4 reads.

**Decisions.**
- Q: What exact starter tags per dimension beyond `other`? Options: [minimal / richer]. Leaning **minimal but illustrative** (e.g. `people: role, other`; `projects: status, timeline, other`; `domains: other`) because the file is explicitly provisional and Phase 8 will refine it; keep just enough to exercise the validator's accept/reject. Final tag list is a content decision, not a structural one — settle during planning.

**Done when.**
- For every configured dimension, the catch-all tag `other` validates as a known tag (catch-all present everywhere). (P5-DATA-08, in concert with Component 4)

---

### 4. Tag Validator + status-mapping helper — the two new checks in `core/tags.py`

**Goal.** Provide (a) a check that a given (dimension, tag) pair is allowed by the rulebook, and (b) a pure helper that turns a confidence score into a `confident`/`pending` status using config thresholds.

**Build.** Two additions, both in or alongside `src/core/tags.py` (deepening the existing rulebook-checker rather than spawning a shallow module):
- **`validate_dimension_tag(dimension, tag, config) -> Result`** — mirrors the existing `validate_tags` shape (`core/tags.py:56`). Returns `Success` for an allowed (dimension, tag) pair; `Failure` for an unknown dimension **or** an unknown tag within a known dimension. Reads the rulebook via a **standalone loader** (a small function that does `yaml.safe_load(path.read_text())` on `dimensions.yaml`, mirroring `load_taxonomy`, `core/tags.py:118`) so `core/config.py` and the deferred config split stay untouched (A5, A7). The `config` argument is the loaded rulebook (or its path) — passed explicitly so tests don't import `CONFIG` at module scope (C-17).
- **Status-mapping helper** — a pure function taking a confidence score and an explicit `ConfidenceBand`, returning `confident` (score `>=` the trusted cutoff) or `pending` (below it), reusing `ConfidenceBand.route()` (`core/config.py:430`) with relabelled outputs. No float literal in an `if/elif` (C-06); the cutoff comes from the passed band. No LLM call, no audit write (C-13). The natural reuse: a `route()` result of `AUTO` → `confident`, `SUGGEST`/`CLUELESS` → `pending` (research to confirm the cleanest mapping; `retired` is NOT produced here — it is set by `retire`).

**Depends on.** Component 3 (the rulebook file must exist for `validate_dimension_tag` to read). The status helper depends only on the existing `ConfidenceBand` (already built).

**Assumes.** A3 (band drivable without CONFIG at scope), A5 (standalone loader works), A7 (config dir).

**Interface shape.** Two pure functions returning `Result` (validator) / a status string (mapper). Callers see "is this pair allowed?" and "what status for this score?"; hidden behind them is the YAML load and the threshold comparison. Deepens `core/tags.py` (puts the new rule next to the rule it most resembles) — no new `Protocol`/seam.

**Dependency category.** in-process (test directly: validator against a temp `dimensions.yaml`; mapper against an explicit `ConfidenceBand`).

**Decisions.**
- Q: Should `validate_dimension_tag` take a pre-loaded config object or a path? Options: [object / path]. Leaning **pre-loaded object via the standalone loader**, so the loader is the single read point and the validator stays pure and easily testable — but expose the loader so callers (and the future extractor) load once and pass it. Confirm in research.
- Q: Exact `route()`→status mapping — is `SUGGEST` `confident` or `pending`? Options: [AUTO→confident, else pending / AUTO+SUGGEST→confident, CLUELESS→pending]. Leaning **AUTO→confident, SUGGEST+CLUELESS→pending** because the design frames only two statuses (trusted vs flagged-for-review) and "pending" = "not yet sure." Settle in research against the design's confident/pending intent.

**Done when.**
- A valid (dimension, tag) pair (e.g. people + role) is accepted; an invented tag for a known dimension is rejected; an unknown dimension is also rejected. (P5-DATA-07)
- For every configured dimension, the `other` tag validates as known. (P5-DATA-08)
- Feeding a high confidence score (above the trusted cutoff) maps to status confident, and a low one (below it) maps to pending, with the cutoff read from config — no float literal in an `if/elif` drives the mapping. (P5-DATA-09)

---

### Suite-wide acceptance (spans all components)

**Done when.** Running the full existing test suite (`uv run pytest`) stays green — all previously-passing tests still pass **except the two version-pin assertions in `tests/test_storage/test_migration_007.py` (lines 41, 56), which are updated `7`→`8` as part of this slice (the expected migration-version bump, research A1)**. No *other* existing test is rewritten or deleted, and `documents.upsert()` still accepts a `WriteOutcome` (signature unchanged). (P5-DATA-10)

---

## Handoff notes

What the next stage (research / planning) needs that doesn't fit a single build step.

- **Contract with Phase 7 (capture / `documents.upsert()` redesign):** This slice ships `full_body`, `original_filename`, `file_size_bytes` as nullable and **unpopulated**. Phase 7 owns populating them and any `upsert()` signature change. Phase 7 readers must tolerate NULL (ADR-0012 §3: Phase 9 `_resolve.py` tier-2 degrades to tier-3 when `full_body` is NULL).
- **Contract with Phase 8 (classify / extraction):** This slice ships the storage toolbox and the rulebook but **no producer**. Phase 8 builds the Fact Extractor that calls `upsert` (after checking each fact's pair via `validate_dimension_tag`) and consumes `get_confident_and_pending` as extraction input. Phase 8 also owns: the `upsert` uniqueness/dedupe rule (see Component 2 decision), entity normalization (OQ-P5DATA-4), and any natural-key constraint.
- **Data-format agreements:**
  - `sources` is a **JSON array TEXT** column; each element is a document's **stable internal id** (loose reference, no FK) — `json.dumps` on write, `json.loads` on read (A6, OQ-P5DATA-1/2).
  - Status vocabulary: `confident`, `pending` (confidence-driven), `retired` (set only by `retire`, never confidence-driven).
- **Suggested research** (run `/research` before planning):
  1. Confirm a multi-statement `008_*.sql` applies cleanly and bumps `schema_version` to 8 on a fresh `init_db` (A1; decide single-file vs `008`–`011` split).
  2. Confirm adding the three nullable columns to `documents` + the three optionals to `DocumentRow` breaks no existing test (A2, A4) — re-run the four `DocumentRow(...)` construction sites listed in A4.
  3. Confirm `ConfidenceBand.route()` can be exercised by an explicitly-constructed band with no `CONFIG` import at the helper's module scope (A3, C-17), and settle the `route()`→`confident/pending` mapping.
  4. Confirm the standalone `dimensions.yaml` loader pattern (`yaml.safe_load(path.read_text())`) needs no `core/config.py` change, and that `src/config/` is the runtime config dir (A5, A7).
  5. Confirm the `sources` JSON round-trip matches the `audit_log.source_ids` / `documents.key_topics` precedent so it reads back as a list (A6).
- **Open uncertainty:** the `upsert` uniqueness key (id-only vs natural key) is left to Phase 8's producer; building a natural-key constraint now risks an interface ahead of its consumer (C-15). Flagged, not designed.

---

## Deferred questions

These are recorded for downstream stages; none blocks this slice. (No new questions were raised that the design doc had not already resolved or deferred.)

- **DQ-1 (= OQ-P5DATA-4) — Entity name normalization.** Entity is free text; deciding whether two spellings ("Anthony" vs "Anthony Nguyen") are the same person is out of scope. Owned by Phase 8 (classify). Source: design Open Questions; rearch doc §14 Q4.
- **DQ-2 — `upsert` dedupe / uniqueness rule.** Whether re-filing a repeat fact updates an existing row (by natural key) or always inserts. Deferred to Phase 8, which defines how the extractor re-files facts. Carries a possible schema follow-up (a unique index) if a natural key is chosen — additive, no data loss.
- **DQ-3 — Knowledge query path / MCP tool (`kms_knowledge`).** No chat- or search-facing access to facts is built; no phase is assigned yet. Source: rearch doc §14 Q8 (undecided).
- **DQ-4 — Dedicated status-threshold config block.** Reusing the existing confidence band is chosen for now (OQ-P5DATA-3); if the fact-status gate ever needs to diverge from the routing gate, a dedicated block is a one-line config addition later.
- **DQ-5 — Starter dimension tag content.** The exact tags per dimension (beyond the mandatory `other`) are a provisional content decision to settle during planning and refine in Phase 8; the file is marked provisional.

---

_Next step: Run `/research` to verify the assumptions (A1–A7) against real code before planning._
