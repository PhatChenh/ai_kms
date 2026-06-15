# P5 Slice 1 — Data/Config Foundation (Design)

_Created: 2026-06-12_
_Phase: 5 (cloud-native rearchitecture), Slice 1 of 2 — additive Data/Config foundation_
_Source of truth: `docs/0_draft/cloud_native_rearchitecture.md` §7. Sequencing law: ADR-0012._
_Behavior-inventory ID prefix: **`P5-DATA`** (entries `P5-DATA-01` … `P5-DATA-10`)._

> **Reader note.** This doc leads in plain English in every section. Code references (file paths, function names) sit in parentheses or sub-bullets and are anchors for the engineer — delete every `code`-formatted token and the prose still reads correctly.

---

## In plain terms (read this first)

Today the system remembers what it learned by writing notes into files in the user's vault. The cloud-native rearchitecture moves that memory into the database instead, as a pile of small, structured facts — "Anthony is the Product Lead for Movie Q2", "Movie Q2 ships in March", and so on. Each fact knows what it is about, how sure we are, and which document it came from.

This slice does **not** build the AI that extracts those facts (that is a later phase). It builds only the three foundations everything else needs: (1) the **place to put the facts** (a new database table plus three new optional fields on the existing document records), (2) the **basic create / read / retire operations** for working with those facts, and (3) the **rulebook** that says which fact categories the AI is allowed to use. Nothing the system does today changes. No existing test is touched. Every new database field is optional, so old records keep working untouched.

---

## Cast of characters

Project symbols referenced 3+ times across this doc. One-off symbols are glossed inline; language primitives (JSON, SQLite, `datetime('now')`) are explained in plain words where they appear.

| Name | Plain-English role |
|---|---|
| `knowledge_entries` | The new database table that holds one structured fact per row |
| `documents` | The existing table of captured files (one row per file); gains 3 optional fields |
| `storage/knowledge_entries.py` | The new code module with the create / read / retire operations for facts |
| `core/tags.py` | The existing rulebook-checker for tags; gains a "is this dimension+tag allowed?" check |
| `config/dimensions.yaml` | The new config file listing allowed fact categories (dimensions) and their tags |
| `WriteOutcome` | The old data object that connects vault writes to the document index — **left untouched this slice** |
| `Result` / `Success` / `Failure` | The standard "did it work?" wrapper every operation returns |
| `get_connection` | The single database-connection helper every storage module must use |

---

## Decision

**Chosen: a single self-contained knowledge table where each fact stores its source documents inline as a list inside its own row, the facts reference documents loosely (not a hard database link), and the confident/pending status comes from confidence thresholds kept in config.** This keeps the slice purely additive — one new table, three new optional columns, one new module, one new config file — so it ships fast, breaks nothing that exists today, and matches the rearchitecture doc's "single universal table, zero schema change to add a dimension" intent (`cloud_native_rearchitecture.md` §7).

In one sentence: store facts the simplest way that satisfies "every fact must name its sources" and "thresholds live in config," and defer every harder question (hard links, name normalization, junction tables) to the phase that actually needs it.

---

## Q1 Diagram — what happens inside

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

## Guardrail Checklist

From `/guardrail-check Review` on this change (domains touched: DB Integrity, LLM & Providers, Architecture, Testing). This is required input for `/writing-detailed-specs`.

```
DB Integrity
[ ] C-04 · PRAGMA foreign_keys=ON on every new connection
    Danger signal: a new connection path that skips the pragma.
    Check: knowledge_entries CRUD uses storage/db.py::get_connection — never raw sqlite3.connect.

[ ] C-05 · All schema changes via versioned .sql deltas
    Danger signal: ALTER/CREATE/DROP inside .py files.
    Check: table + 3 columns land as new 008_*.sql migration; no DDL in knowledge_entries.py.

LLM & Providers
[ ] C-06 · Confidence thresholds live in config, never in code
    Danger signal: float literal in an if/elif comparison.
    Check: confidence→status mapping reads its cutoff from config, not a code literal.

Architecture
[ ] C-12 · Public functions return Success or Failure
    Danger signal: a def returning a raw value/None at a module boundary.
    Check: all 5 CRUD functions + validate_dimension_tag() return Result types.

[ ] C-13 · Audit log is non-negotiable
    Check: Slice 1 makes NO AI decision (CRUD + config only, no LLM call) — no audit.write
    required here. Verify no LLM call sneaks in.

Testing
[ ] C-17 · Never import CONFIG at module scope in tests
    Check: new tests pass explicit db_path / config paths; no module-scope CONFIG import.

Domains checked: DB Integrity, LLM & Providers, Architecture, Testing
Domains skipped: Write Safety (no vault writes — additive DB/config only), Async & CLI
                 (no CLI command or env loading added in Slice 1)
```

---

## Implications

What this change actually means for the codebase. Plain-English lead first; the code-verified fact is the sub-bullet.

- **The system gains a brand-new place to store small structured facts, separate from the file index it already has.** This is the foundation the whole cloud-native "memory" depends on.
  - New table `knowledge_entries` created by a new migration file (`storage/migrations/008_*.sql`), with columns `id, dimension, entity, tag, fact, status, confidence, sources, reasoning, created_at, updated_at` per `cloud_native_rearchitecture.md` §7. Created via the existing migration runner (`storage/db.py::_run_migrations`), which applies files in numeric order and bumps `schema_version` to 8.

- **Existing document records get three new optional fields, and every record that already exists keeps working with those fields simply empty.** Nothing has to be re-saved; old data is untouched.
  - Three nullable columns added to `documents`: `full_body` (TEXT), `original_filename` (TEXT), `file_size_bytes` (INTEGER). Because the read path uses `SELECT *` and `_row_from_sqlite` already guards optional columns with `if "<col>" in row.keys()` (`storage/documents.py:47-67`), adding nullable columns forces **no change** to `documents.upsert()` or `replace_path()` — confirming the additive-zero-breakage claim. Whether `DocumentRow` gains the three fields now is a minor decision (see "Secondary decisions"); the conservative answer is **add them to the dataclass as `None`-defaulted optionals so reads surface them, but populate nothing** — populating rides with Phase 7.

- **There is a new, small toolbox for working with facts: create-or-update one, look them up by category or by who/what they're about, retire an outdated one, and fetch the live set.** Each operation says clearly whether it worked.
  - New module `storage/knowledge_entries.py` with `upsert()`, `query_by_dimension()`, `query_by_entity()`, `retire()`, `get_confident_and_pending()`. Each returns `Success(value)` or `Failure(...)` (`core/result.py`), matching the CRUD shape of `storage/audit_log.py` and `storage/documents.py`. Uses `get_connection` (C-04 satisfied transitively — the pragma lives in `storage/db.py::_connect`).

- **The AI is fenced in: it can only file facts under categories an administrator has pre-approved in a config file.** It cannot invent its own categories, which keeps the knowledge base tidy and predictable.
  - New config `config/dimensions.yaml` (loaded independently of the deferred config split — same pattern as `_load_yaml` in `core/config.py`, or a tiny loader in `core/tags.py`). New function `validate_dimension_tag(dimension, tag, config) -> Result` added to `core/tags.py`, mirroring the existing `validate_tags()` shape (returns Success for an allowed pair, Failure for an unknown dimension or tag).

- **How sure the AI is about a fact decides whether the fact is trusted or flagged for review — and the cutoff number lives in config, not buried in code.** Tuning trust levels is a config edit, never a code change.
  - The confidence→status mapping (`confident` vs `pending`) reads its threshold from config (C-06). The existing `ConfidenceBand` (`core/config.py:396`) and `route()` (`core/confidence.py:51`) already map a score to `AUTO/SUGGEST/CLUELESS` against config thresholds — Slice 1 reuses that machinery with relabelled outputs (see Decision Q3 below). The mapping helper is a pure function with no LLM call, so no audit write is required (C-13 not triggered).

- **The "old way" of connecting vault writes to the index stays exactly as it is — this slice deliberately does not touch it.** That redesign is a different phase's job, so the build stays green and parallel-safe.
  - `documents.upsert()` and `replace_path()` keep their `WriteOutcome` signature (`storage/documents.py:90, 232`). `WriteOutcome` (`vault/writer.py:39`), `write_note`, `move_note`, frontmatter, and config split are all **out of scope** per ADR-0012.

- **Module-depth check (deletion test):** `storage/knowledge_entries.py` is a genuine deep module, not a pass-through — delete it and the SQL + JSON round-tripping + Result-wrapping reappears in every future caller (classify, the web UI, the knowledge MCP tool). `core/tags.py` is being **deepened**, not widened: it already owns "is this tag allowed?"; adding "is this dimension+tag allowed?" puts the new rule next to the rule it most resembles instead of spawning a new shallow module. No new interface/Protocol is introduced — these are concrete functions with one real caller pattern, so there is no speculative seam.

---

## Known tradeoffs

What we give up by picking inline-source storage + loose references + reused-threshold over the alternatives.

- **We give up database-enforced source integrity.** Because a fact stores its source documents as a plain list inside its own row (not a hard database link), the database will not stop a fact from naming a document that has since been deleted. In return we get a dramatically simpler slice — one table, no join logic, and exactly the shape the rearchitecture doc described. The cost surfaces only if "show me every fact from this document" becomes a hot, indexed query; if it does, a later phase can add the link table without discarding any data.
- **We give up cheap, indexed "facts for document X" lookups.** Searching inside a text list is slower than a real database link with an index. For a personal vault (the rearchitecture doc calls the whole DB "<100MB", §16) this is a non-issue; at larger scale it would need revisiting.
- **We accept that confidence labels are slightly overloaded.** Reusing the existing `AUTO/SUGGEST/CLUELESS` threshold band to drive `confident/pending` means the same config block serves two conceptually different gates. The alternative — a brand-new status-threshold config block — is cleaner conceptually but adds config surface for a mapping the rearchitecture doc says is "the same gating pattern as before" (§7). We chose continuity; if the two gates need to diverge, a dedicated block is a one-line config addition later.

---

## Risks (for research / planning to verify)

- **Migration shape.** The intended single `008_*.sql` does `CREATE TABLE knowledge_entries` **plus** three `ALTER TABLE documents ADD COLUMN`. The runner's own comment (`storage/db.py:_run_migrations`) prefers "a single atomic DDL statement per file," but `002_batches.sql` already ships multi-statement DDL (CREATE + ALTER) and the runner uses `executescript`, which handles it. **Verify in research:** a multi-statement `008` applies cleanly and bumps `schema_version` to 8. If a reviewer objects to multi-statement files, the fallback is `008`–`011` as four single-statement files (see Secondary decisions). Either way the behavior is identical.
- **`sources` JSON round-trip.** Storing `sources` as a JSON-array TEXT column means the CRUD must `json.dumps` on write and `json.loads` on read, exactly as `audit_log.py` does for `source_ids` and `documents.py` does for `key_topics`. Research should confirm the chosen serialization matches those precedents so the column reads back as a Python list, not a string.
- **Confidence-threshold reuse.** Confirm `ConfidenceBand`/`route()` can be driven without dragging in vault-dependent config at import time (C-17 risk for tests). The mapping helper must accept an explicit threshold/config argument so tests don't import the `CONFIG` singleton at module scope.
- **`DocumentRow` field addition.** If `DocumentRow` gains `full_body`/`original_filename`/`file_size_bytes`, verify no existing test constructs `DocumentRow(...)` positionally in a way that the new trailing optional fields would break. (They are `None`-defaulted, so keyword and short-positional construction stay valid — but a positional test that already passes all 14 fields would need the new ones appended at the end, never inserted mid-list.)
- **`config/dimensions.yaml` load path.** The file must load **without** the deferred config split. Research should pick the lighter of: (a) a standalone loader in `core/tags.py` reading the YAML directly (mirrors `load_taxonomy`'s `Path.read_text()` + `yaml.safe_load`), or (b) a new optional field on `MainConfig`. Option (a) keeps `core/config.py` untouched and is preferred; flag if (b) turns out cleaner.

---

## Open questions

**OQ-P5DATA-1 — Should a fact's source documents be stored inside the fact's own row, or in a separate linking table?** _(THE main decision — resolved here, recorded for visibility; reopen only if a hot query proves it wrong.)_

Right now there is no knowledge table at all, so nothing references documents yet.

The question: do we keep each fact's list of source documents inside the fact's own row, or split the fact-to-document connections into their own separate table?

**If inline list (chosen):** the slice stays tiny — one table, no joins. Adding a fact and reading its sources is trivial. The trade is no database-guaranteed integrity and slower "which facts came from this document?" lookups.
**If separate linking table:** the database can enforce that every source points at a real document, and "facts from document X" becomes a fast indexed lookup. The trade is a second table, join logic, and a heavier slice for a benefit no current consumer needs yet.

Recommendation: inline list. It is the simplest thing that satisfies "every fact names its sources," it matches the rearchitecture doc's single-table intent, and the harder option can be added later without throwing away any stored data.

**OQ-P5DATA-2 — Should a source reference point at a document by its stable internal id or by its file path?** _(Resolved here; flagged because the user moves files and paths change.)_

Right now documents have both a permanent internal id and a file path; the file path can change whenever the user moves a file (the daemon will report those moves in a later phase).

The question: when a fact records where it came from, should it record the document's permanent id or its current file path?

**If internal id:** the link survives the user moving or renaming the file — the id never changes. The trade is the reference is opaque (a number, not a readable path) and resolving it to a path is an extra lookup.
**If file path:** the reference is human-readable, but it silently goes stale the moment the user moves the file, leaving facts pointing at a path that no longer exists.

Recommendation: store the permanent internal id (loosely, not as a hard database link per OQ-1). It is the only choice that stays correct when files move — which the rearchitecture explicitly expects.

**OQ-P5DATA-3 — Should the confident/pending cutoff reuse the existing confidence settings, or get its own new settings block?** _(Resolved here; both satisfy C-06.)_

Right now the system already turns a confidence score into a trust decision using cutoff numbers kept in a config file (the same machinery that decides whether to act automatically elsewhere).

The question: should fact-status (confident vs pending) reuse that existing cutoff config, or define a fresh one just for knowledge entries?

**If reuse:** zero new config, immediate consistency with how the rest of the system already gates on confidence — the rearchitecture doc calls this "the same gating pattern as before." The trade is one config block now serves two purposes.
**If new block:** conceptually cleaner separation, and the two gates can diverge freely. The trade is added config surface for a mapping that today behaves identically to the existing one.

Recommendation: reuse the existing confidence settings for now, relabelling its outputs to confident/pending. If the two gates ever need different cutoffs, splitting them out is a one-line config change later.

**OQ-P5DATA-4 — Entity name normalization (e.g. "Anthony" vs "Anthony Nguyen") — DEFERRED, not a Slice 1 blocker.** Entity is stored as free text. Deciding whether two spellings are the same person is real work with real trade-offs and is explicitly out of scope (`cloud_native_rearchitecture.md` §14 Q4). Flagged here as a Phase-8 (classify) open question; do not design it in this slice.

---

## ADR references

- **ADR-0012 — Additive rearchitecture; defer breaking changes to the consumer-refactor phase.** This slice is the direct product of that decision: additive table + nullable columns + new module + new config, touching no live consumer. This doc does **not** propose a new ADR — the schema-shape decisions (inline sources, loose id references, threshold reuse) are reversible additive choices recorded above as OQs, not hard-to-reverse architectural commitments. Per the skill's ADR gate, "hard to reverse" is false for all three (the inline list can gain a link table later without data loss; references can be re-pointed; a status-threshold block can be added), so no second ADR is warranted. ADR-0012 already covers the sequencing strategy and the instruction was explicit not to write a second sequencing ADR.

---

## Options explored

This is a constrained, additive slice with one genuinely open decision (source storage), so the options grid centers there. Each viable shape was considered against the design lens (depth, deletion test, seam discipline).

**Option A — Inline sources + loose id references + reused thresholds (Recommended, chosen).**
One-sentence summary: store each fact's sources as a JSON list inside its own row, reference documents by stable id without a hard database link, and drive confident/pending from the existing confidence-threshold config.
Why chosen: smallest additive footprint, matches the rearchitecture doc's single-table intent, satisfies every hard constraint (C-04/05/06/12/17), and defers nothing it doesn't have to. Its Q1 diagram is the canonical one above.

**Option B — Junction table for sources (`knowledge_entry_sources`) + foreign-key link to documents (Not chosen).**
One-sentence summary: split fact-to-document connections into their own table with a real database link and index.
Main reasons not selected: (1) Adds a second table and join logic for a benefit (fast "facts from document X", integrity enforcement) that **no Slice 1 or near-term consumer needs** — that is a speculative seam (1 hypothetical adapter, not 2). (2) A hard foreign key to `documents` collides with the "files move, paths/ids may be re-pointed by the daemon" reality and with the loose-reference recommendation in OQ-2. (3) Heavier slice for zero current payoff; cleanly addable later without discarding stored data, so deferring loses nothing.

**Option C — New dedicated status-threshold config block instead of reusing the confidence band (Partially rejected — folded into OQ-3).**
One-sentence summary: define a fresh config block mapping confidence to confident/pending, separate from the existing `AUTO/SUGGEST/CLUELESS` band.
Main reasons not selected: the rearchitecture doc says the status gate is "the same gating pattern as before" (§7); a new block adds config surface for an identical mapping. Reuse is the conservative choice; the dedicated block remains a one-line future addition if the gates diverge.

**Rejected alternatives (one line each):**
- _Per-dimension tables (one table per dimension)_ — contradicts the rearchitecture doc's explicit "single universal table, adding a dimension = config change, zero schema change" (§7); would make every new dimension a migration.
- _Store `sources` as comma-joined text instead of JSON_ — diverges from the established JSON-array precedent (`audit_log.source_ids`, `documents.key_topics`); no upside, loses structure.
- _Redesign `documents.upsert()` now to accept structured summary + `full_body`_ — explicitly deferred to Phase 7 by ADR-0012; its only live caller (`capture.py`) is not rewritten until then, and building the new signature now is an interface ahead of its consumer (C-15).
- _Build a `kms_knowledge` query path / MCP tool in this slice_ — out of scope; no pipeline produces entries yet (C-15), and the rearchitecture doc lists the query-tool question as undecided (§14 Q8).

---

## Secondary decisions (resolved in place)

- **Migration file shape:** single `008_knowledge_entries_and_document_columns.sql` doing CREATE TABLE + 3 ALTERs. Precedent: `002_batches.sql` already ships CREATE + ALTER in one file via `executescript`. Fallback if a reviewer wants single-statement files: `008`–`011` split. Behavior identical either way; verify in research.
- **`DocumentRow` fields:** add `full_body: str | None = None`, `original_filename: str | None = None`, `file_size_bytes: int | None = None` as trailing `None`-defaulted optionals so reads surface them; populate nothing this slice (population rides with Phase 7). This keeps `_row_from_sqlite` reading them with the same `if "<col>" in row.keys()` guard already used for `batch_id`/`project`/`status`/`key_topics`.
- **`config/dimensions.yaml` loader:** prefer a standalone loader in `core/tags.py` (direct `yaml.safe_load` of the file, mirroring `load_taxonomy`), so `core/config.py` and the deferred config split stay untouched.

---

## Next step

Design doc written. Run `/architecture-docs` (a.k.a. `/update-arch-story`) to refresh the main architecture designs for the new `knowledge_entries` store, then run `/writing-detailed-specs` to structure the chosen option (Option A) into build steps. The Guardrail Checklist above is the required input for the spec.
