# Research: Phase 5 Slice 1 — Data/Config Foundation
_Last updated: 2026-06-12 (re-check pass)_

## Overview

This slice builds the storage and rulebook foundation for the cloud-native "memory": a new database table for structured facts, three new optional fields on the existing file records, a small toolbox of create/read/retire operations, and a config file listing the fact categories the AI is allowed to use. Nothing produces facts yet — this is plumbing only.

This research verified the spec's seven assumptions (A1–A7) against the real code, not against the spec's own claims. On the first pass, six of the seven held as written and one — A1 — was invalidated: the spec's "zero existing-test breakage" framing was false, because the new database upgrade (migration 008) bumps the stored version from 7 to 8 and two existing tests are hard-wired to check that the number equals 7.

**That A1 finding has now been resolved.** The spec was patched (2026-06-12): it no longer promises "zero existing-test breakage." It now states that migration 008 bumps the schema version to 8 and lists the two version-pin assertions in `tests/test_migration_007.py` (lines 41, 56) as a required mechanical build step to update from `7` to `8`. Re-verified against the code, this corrected text now accurately describes reality. **All seven assumptions are now validated/resolved; nothing blocks planning.**

**Bottom line for planning:** the slice is buildable as designed. Budget for the two version-pin test-assertion edits (`7`→`8`) the spec now lists as a build step. The slice is correctly framed as "additive code plus two trivial version-pin test updates," not "zero existing-test breakage."

---

## Key Components

These are the existing pieces the slice leans on, and the new pieces it adds. The slice deepens existing modules rather than spawning shallow new ones.

**Existing, reused unchanged:**
- **Database connection helper** (`src/storage/db.py:75`, `get_connection`) — opens one connection, commits on clean exit, rolls back on error. Applies the foreign-key pragma via `_connect` (`db.py:16`). The new toolbox must open every connection through this.
- **Migration runner** (`src/storage/db.py:29`, `_run_migrations`) — applies each `NNN_*.sql` file with a number higher than the stored version, in numeric order, via `executescript` (`db.py:40`), then bumps the version.
- **Result wrapper** (`src/core/result.py`, `Success`/`Failure`) — the standard "did it work?" envelope every public operation returns.
- **Audit CRUD shape** (`src/storage/audit_log.py:27,65`) — the reference pattern the fact toolbox copies for its JSON-list column.
- **Documents data layer** (`src/storage/documents.py`) — `DocumentRow` dataclass (line 27), `_row_from_sqlite` reader (line 47), `upsert` (line 90), `replace_path` (line 232).
- **Confidence gate** (`src/core/config.py:396`, `ConfidenceBand`; `.route()` at line 430) — maps a score to AUTO/SUGGEST/CLUELESS against config thresholds.
- **Tag rulebook checker** (`src/core/tags.py:56,118`, `validate_tags` + `load_taxonomy`) — validates tags against a YAML-loaded vocabulary.

**New, added this slice:**
- **Schema Upgrade** — `src/storage/migrations/008_*.sql` (confirmed next number is 008).
- **Knowledge Entry Store** — `src/storage/knowledge_entries.py` (new module, five fact operations).
- **Dimension/Tag Rulebook** — `src/config/dimensions.yaml` (new config file, beside `tags.yaml`).
- **Tag Validator + status helper** — two new functions in `src/core/tags.py`.

---

## How It Works

When the database starts, the runner finds the new `008` upgrade file (its number is higher than the last applied), runs it via `executescript`, and bumps the stored version. The upgrade creates the facts table and adds three optional columns to the existing file table. From then on, the fact toolbox can write a fact (deciding "confident" or "pending" from its confidence score), read facts by category or by entity, retire a fact, or fetch the live set. The rulebook check confirms a fact's (category, tag) pair is allowed before any write. No AI runs in this slice — it is create/read/retire plus one config-driven check.

The migration mechanism itself is sound: `_run_migrations` (`db.py:36-43`) globs `[0-9][0-9][0-9]_*.sql`, sorts numerically, and applies each via `executescript`. `executescript` runs every statement in the file, so a single file with `CREATE TABLE` + three `ALTER TABLE` statements applies in one pass — exactly how `002_batches.sql` (CREATE + ALTER) and `007_search_indexes.sql` (two CREATE VIRTUAL TABLE statements) already work in the live suite.

---

## Spec Verification

All seven assumptions now hold. A1 was invalidated on the first pass (the "zero test breakage" framing was false); the spec has since been patched to capture the two version-pin test edits as a required build step, so A1 is now **Resolved** — the upgrade mechanism works AND the spec text now matches code reality.

| Assumption ID | Spec Claim | Verdict | Evidence |
|--------------|-----------|---------|----------|
| A1 | Migration runner applies a multi-statement `008` (CREATE + 3× ALTER) cleanly via `executescript`, bumps version to 8, like `002_batches.sql` already does — and the patched spec now lists the two `test_migration_007.py` version-pin assertions (lines 41, 56) as a required `7`→`8` build step, dropping the "zero existing-test breakage" framing. | ✅ Resolved (was ❌ Invalidated) | Mechanism: `db.py:36-43` `_run_migrations` parses `file_version = int(path.name[:3])` (line 37), applies files where `file_version > version` via `executescript` (line 40), then `UPDATE schema_version SET version = ?` to that number (line 41) — a `008_*.sql` file bumps stored version to 8; dir holds 001–007 so 008 is next ✅. Tests: `tests/test_storage/test_migration_007.py:41` and `:56` both read `assert version == 7` after a real `init_db()`, no skip marker (grep confirmed) → both require the `7`→`8` edit ✅. Spec now captures this as a build step (Component 1 "Also update", Constraints §, P5-DATA-10) instead of promising zero breakage ✅. |
| A2 | `documents` reads via `SELECT *` and `_row_from_sqlite` guards each optional column with `if "<col>" in row.keys()`, so 3 nullable columns force NO change to `upsert()`/`replace_path()`. | ✅ Validated | `documents.py:164` `SELECT * FROM documents`; `_row_from_sqlite` guards `batch_id`/`project`/`status`/`key_topics` with `if "<col>" in row.keys()` (lines 59–66). `upsert`/`replace_path` INSERT explicit column lists (lines 114–119, 271–276) — an unlisted nullable column defaults to NULL, no edit needed. (Caveat below: adding the fields to `DocumentRow` does require a small additive edit to `_row_from_sqlite`.) |
| A3 | `ConfidenceBand.route()` can be driven by an explicit `ConfidenceBand` instance — no `CONFIG` import at the helper's module scope. | ✅ Validated | `ConfidenceBand` is a plain Pydantic model (`config.py:396`); construct directly `ConfidenceBand(auto=0.85, suggest=0.60)` — proven by the docstring example at `confidence.py:82-87`. `.route(score)` (`config.py:430`) is a pure method. `confidence.py:51` `route(decision, thresholds)` takes the band as an explicit argument — the exact pure, threshold-in-argument pattern. |
| A4 | Adding `full_body`/`original_filename`/`file_size_bytes` as trailing None-defaulted optionals to `DocumentRow` breaks no test — every construction site is keyword-based and stops at/before `batch_id`. | ✅ Validated | All 5 construction sites checked: `documents.py:48` (the reader, keyword); `test_documents.py:223` (keyword, stops at `batch_id`); `test_watcher.py:1159` (keyword, stops at `content_hash`); `test_watcher_rehome.py:78` and `test_watcher_settle.py:73` (`DocumentRow(**defaults)` dicts, last key `key_topics`). None positional past `batch_id`. Trailing optionals are safe. |
| A5 | A standalone loader reading `dimensions.yaml` with `yaml.safe_load(path.read_text())` (mirroring `load_taxonomy`) works without touching `core/config.py` or the deferred config split. | ✅ Validated | `tags.py:118` `load_taxonomy` does exactly `yaml.safe_load(tags_yaml_path.read_text())` (line 130), importing only `yaml` + `pathlib` (lines 7,9). No `core/config.py` / `CONFIG` dependency. A mirror loader for `dimensions.yaml` is self-contained. |
| A6 | Storing `sources` as a JSON-array TEXT column round-trips to a Python list via `json.dumps`/`json.loads`, like `audit_log.source_ids` and `documents.key_topics` already do. | ✅ Validated | `audit_log.py:47` `json.dumps(entry.source_ids)` on write, `:101` `json.loads(row[2])` on read. `documents.py:85` `json.dumps([...])` for `key_topics`, `:63` `json.loads(row["key_topics"])` on read. Both store/parse JSON lists through TEXT columns. |
| A7 | The live config dir loaded at runtime is `src/config/`, so `dimensions.yaml` belongs there beside `tags.yaml`. | ✅ Validated | `config.py:33` `_PROJECT_ROOT = Path(__file__).parent.parent` resolves to `src/` (file is `src/core/config.py`); `:37` `_CONFIG_DIR = _PROJECT_ROOT / "config"` = `src/config/`. `ls src/config/` shows `tags.yaml`, `config.yaml`, `routing.yaml`, `thresholds.yaml`. No repo-root `config/` dir exists. |

---

## Edge Cases & Silent Failure Modes

Things that are not obvious from the spec but matter when building.

- **A2 has a hidden additive edit the spec under-states.** The spec says adding 3 columns "forces NO change" — true for `upsert()`/`replace_path()` (their INSERTs list columns explicitly). But the spec ALSO wants the 3 fields ON the `DocumentRow` dataclass (Component 1, A4). If you add fields to `DocumentRow`, you MUST also add three guarded reads to `_row_from_sqlite` (e.g. `full_body=row["full_body"] if "full_body" in row.keys() else None`), or the dataclass silently uses the default and never reflects what's in the database. This is purely additive and matches the existing guard pattern (`documents.py:59-66`), but it IS an edit to existing code — the spec's "no change to the reader" framing is slightly optimistic. Not a breakage; just a build step to plan.
- **`executescript` commits before the version bump.** The runner comment (`db.py:30-34`) warns that `executescript` issues an implicit COMMIT before the `UPDATE schema_version`. If the file's DDL succeeds but the version UPDATE fails, the schema is applied but the version isn't bumped — a partial-apply window. This risk is identical whether 008 is one file or four; it is not a reason to split.
- **Exactly-on-cutoff confidence.** `ConfidenceBand.route()` uses `>=` (`config.py:449,451`), so a score exactly equal to the `auto` threshold routes to AUTO (→ confident). Matches the spec's "`>=` is trusted" claim.
- **Membership tests, not exact-set tests, on the documents table.** `test_documents.py:186-197` checks `"project" in col_names` (membership), not an exact column set or count. Adding 3 columns leaves it green. (Confirmed — this is why A2's column addition is safe on the test side.)

---

## Dependencies & Coupling

What the slice leans on, and what could shift under it.

- **Migration runner** is shared infrastructure — any phase that adds a migration interacts with the same version-pin tests. Adding 008 here is the trigger for the A1 breakage; a future 009 would have the same effect on whatever test pins version 8.
- **`DocumentRow` / `_row_from_sqlite`** are read by every documents consumer (indexer, capture, search filter). The additive field + guard edit is local; nothing downstream needs the new fields this slice (they ship NULL).
- **`ConfidenceBand`** is the single routing gate, reused by classify and the MCP context engine. The new status helper borrows it read-only — no change to the gate itself.
- **`src/config/`** is the runtime config home; `dimensions.yaml` joins `tags.yaml` there. The tag-count tests (`test_tags.py:36,111`) read `tags.yaml` specifically and assert its `allowed_types` has 9 entries — `dimensions.yaml` is a separate file with a different top-level shape (dimensions→tags), so it cannot affect that count. Confirmed untouched.

---

## Extension Points

Where future phases plug in without rework.

- **Knowledge Entry Store** is a deep module of concrete functions returning `Result` — Phase 8 (the fact extractor) calls `upsert` and consumes `get_confident_and_pending` without changing the store. No `Protocol`/`ABC` introduced (correct — only one real future caller pattern).
- **`dimensions.yaml`** is data, not logic — refining the starter taxonomy (Phase 8) is a content edit, no code change.
- **`sources` as inline JSON** (no FK) is deliberately loose — a link table can be added later without data loss if a hot query needs it (OQ-P5DATA-1).
- **Status helper** maps confidence→status via the existing band; if the fact-status gate ever needs to diverge from the routing gate, a dedicated config block is a one-line addition (DQ-4).

---

## Open Questions

Things genuinely undecided — none block this slice (all are deferred by the spec, confirmed against code).

- **`upsert` uniqueness key (id-only vs natural key).** Code has no producer yet, so no dedupe rule can be verified from code. Deferred to Phase 8 (DQ-2). Confirmed: no existing natural-key index on any comparable table that would force a choice now.
- **Exact starter tags per dimension.** A content decision (DQ-5); the validator only needs `other` present everywhere plus enough tags to exercise accept/reject.

---

## Technical Debt Spotted

- **The runner's own comment is now stale.** `db.py:33-34` says "Keep migration files to a single atomic DDL statement … Revisit in Phase 3 when multi-statement migrations land." Multi-statement migrations ALREADY landed (`002_batches.sql`, `007_search_indexes.sql`), and 008 will be another. The comment's advice is contradicted by shipped practice. Worth correcting the comment when touching `db.py` (low priority, not blocking).
- **Version-pin tests are brittle by design.** `tests/test_storage/test_migration_007.py` pins `version == 7`. Every future migration breaks it until updated. Consider (future) asserting "version is at least N" or deriving the expected number from the migration count, so additive migrations stop tripping it. Out of scope for this slice — flag only. _(Note: this slice's specific 7→8 break is now captured as a build step in the patched spec — see A1 Resolved. The general brittleness remains as future tech debt.)_

---

_Note: A2 carries a minor under-statement (adding the 3 fields to `DocumentRow` requires a small additive edit to `_row_from_sqlite`'s reader, using the existing guard pattern) — this is a build step, not a breakage, so A2 remains ✅ Validated. See Edge Cases._

---

## Resolved Research Items (the two minor items the spec flagged)

**1. Migration packaging — single `008` file vs split `008`–`011`.**
**Recommend: single `008` file (CREATE + 3× ALTER).** The runner applies whole files via `executescript` (`db.py:40`), and two shipped files already prove multi-statement works (`002_batches.sql` CREATE+ALTER; `007_search_indexes.sql` two CREATEs). A four-file split would bump the version to 11 (breaking the SAME `test_migration_007` assertions) and add three more files for no behavioral gain. The partial-apply risk the runner comment warns about (`db.py:30-34`) is per-file and identical either way. Single file matches convention and minimizes footprint.

**2. The `route()` → {confident, pending} mapping.**
Confirmed from code: `RouteDecision(str, Enum)` has exactly three members — `AUTO = "auto"`, `SUGGEST = "suggest"`, `CLUELESS = "clueless"` (`config.py:67-69`); `ConfidenceBand.route()` returns one of them by `>=` comparison (`config.py:449-454`). The status only needs two values.
**Recommend: AUTO → confident; SUGGEST + CLUELESS → pending.** Rationale: AUTO is the only band meaning "sure enough to act"; SUGGEST ("uncertain candidate") and CLUELESS ("no candidate") both mean "not yet sure → flag for human" = pending. This matches the design's two-status intent (trusted vs flagged-for-review) and the spec's own leaning (Component 4 decision). `retired` is never produced here — it is set only by `retire()`.

---

## Constraint Sanity-Check (additive-only / zero-breakage claim)

The prompt asked to verify three breakage vectors directly against code:

- **(a) A new nullable column on `documents`** — Safe. `test_documents.py:186-197` uses membership checks (`"project" in col_names`), not exact-set or count. No test asserts the documents column count. ✅ No breakage.
- **(b) A new migration file (008)** — Breaks two version-pin assertions, now captured as a build step. `tests/test_storage/test_migration_007.py:41,56` pin `assert version == 7` after a real `init_db()`; 008 bumps it to 8 → 2 assertions fail. The patched spec lists the `7`→`8` edit as a required build step (Component 1 "Also update", Constraints §, P5-DATA-10), so this is the expected mechanical migration-version bump, not an unaccounted regression. ✅ Resolved (was the A1 invalidation).
- **(c) Extending `core/tags.py`** — Safe. The tag-count tests (`test_tags.py:36` `assert len(allowed) == 9`, `:111` `assert len(taxonomy.allowed_types) == 9`) read `src/config/tags.yaml`'s `allowed_types` (currently 9 entries). `dimensions.yaml` is a SEPARATE new file with a different top-level shape (dimensions→tags map, no `allowed_types` key) and does not feed `load_taxonomy`. Adding `validate_dimension_tag` + a standalone loader as new functions touches no existing function. ✅ No breakage. (Confirmed the CLAUDE.md gotcha: dimensions.yaml is separate → count test untouched.)

Net: the slice is additive in every dimension EXCEPT the migration version pin, which forces two mechanical test-assertion edits — now captured as an explicit build step in the patched spec.

---
## Update — 2026-06-12 (re-check pass)
### Re-check: all assumptions resolved

Re-entered re-check mode after the spec (`docs/2_specs/P5_slice1_data_foundation.md`) was patched to capture the A1 finding. Re-verified A1 against the NOW-PATCHED spec AND the real code. A2–A7 were left untouched by the patch (documentation-only, no code dependency changed) and keep their prior ✅ Validated verdicts.

**Resolved:**

| ID | Was | Now | Evidence |
|----|-----|-----|----------|
| A1 | Spec promised "zero existing-test breakage"; FALSE — migration 008 bumps schema_version 7→8 and two `test_migration_007.py` assertions hard-pin `== 7`, so both fail. | Spec patched: drops the "zero breakage" framing, states 008 bumps version to 8, and lists the two version-pin assertions (lines 41, 56) as a required mechanical `7`→`8` build step. Corrected text matches code reality. | Mechanism: `src/storage/db.py:36-43` parses `file_version=int(path.name[:3])` (`:37`), applies via `executescript` (`:40`), bumps stored version to that number (`:41`) — a `008_*.sql` → version 8; `src/storage/migrations/` holds 001–007 so 008 is next. Tests: `tests/test_storage/test_migration_007.py:41` and `:56` both read `assert version == 7` after a real `init_db()`, NO skip marker (grep confirmed), and hit the real migration dir — both require the `7`→`8` edit. Spec now states this at Component 1 "Also update" (`spec:286`), Constraints § (`spec:251`), A1 verdict row (`spec:262`), and P5-DATA-10 (`spec:386`); behavior_inventory P5-DATA-10 (`behavior_inventory.yaml:2928-2931`) matches. |

**New invalidated assumptions:** none. The patch is documentation-only and introduced no new false claim or contradiction.

**Minor finding (non-blocking, pre-existing — not introduced by the patch):** The patched spec and behavior_inventory refer to the test as `tests/test_migration_007.py`, but the file actually lives at `tests/test_storage/test_migration_007.py` (the bare `tests/test_migration_007.py` path does not exist; this research doc also carried the short form). The line numbers (41, 56) and assertion text (`assert version == 7`) are exactly correct, so a developer reading either doc will find the right assertions — but the directory segment `test_storage/` is dropped. Worth a one-token fix in the spec when next touched. (Note: behavior_inventory line 2267 already uses the correct full path in its `pytest_ref`.) This is a path-precision nit, not a new invalidation — it does not block planning.

**Verdict:** A1 RESOLVED. Counts: **7 validated/resolved (6 ✅ Validated A2–A7 + 1 ✅ Resolved A1), 0 invalidated, 0 unverifiable.** No `## Invalidated Assumptions` section remains. **Ready for /plan P5_slice1_data_foundation.**
