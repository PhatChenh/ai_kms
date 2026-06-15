# Plan: Phase 8 Slice B — Classify Extraction Pipeline (the AI brain)

_Last updated: 2026-06-15_
_Status: [ ] pending_

_Spec: `docs/2_specs/phase8_sliceB_extraction.md` (component IDs P8-CLS-B-01…12)_
_Research: `docs/3_research/phase8_sliceB_extraction.md` (16 validated, 0 invalidated, 0 unverifiable)_
_Design: `docs/1_design/phase8/phase8_sliceB_extraction.md` (choices A1/B1/C/D locked; ADR-0017/0018/0019)_
_Slice A plan (the built infrastructure this extends): `docs/4_plans/phase8_sliceA_classify_infra.md`_
_Behavior IDs: P8-CLS-B-01 … P8-CLS-B-12 (already in `docs/system_behavior/behavior_inventory.yaml` — reference, do not duplicate)_

> **For the non-coder reader:** Slice A built the plumbing — it finds documents that still need classifying, loads their inputs, and stops right before any AI step. **Slice B adds the AI brain.** For each captured document the system asks the AI, one focused question per knowledge category (people / projects / domains), to pull out small structured facts ("Anthony leads Movie Q2"). It writes those facts safely into the knowledge database (add / edit / retire), logs every decision, and marks the document done. Three guards make it robust: a bad fact is logged and retried with feedback (capped so it cannot loop forever); an exact-match check folds duplicate facts together; and capture pushes a new document straight onto the live work queue so it is classified the moment it arrives. **This is the last piece of Phase 8's classify redesign — and the first slice that costs real AI money per document.**

---

## Architecture

The plan implements the spec's ten build components (the spec's component-dependency-order list). The diagrams below come from the upstream design (Q1) and spec (Q2) — they are referenced, not redrawn. Q3 (drawn fresh for this plan) shows the *rules* each new piece must obey and why.

### Q1 — What happens inside (one document)
See `docs/1_design/phase8/phase8_sliceB_extraction.md` (Q1 diagram), also reproduced in the spec. In one line: a doc id arrives on the work queue → prepare inputs (tag the run, read the text, load known facts per category, load last-failure note + tries) → for each category {AI extracts facts → Writer adds/updates/retires/folds → one audit record} → fork "did every category fully succeed?" → YES: stamp done + clear retry state; NO: save the failure reason + add 1 to the try count → fork "tries reached the cap?" → NO: stays in queue to retry; YES: park for human review + audit why.

### Q2 — How it connects
See `docs/2_specs/phase8_sliceB_extraction.md` (Q2 / "Feature overview"). The Classify Module orchestrator pulls one id at a time from the Work Queue; reads the Knowledge Categories config; asks the AI Call Cluster (Entity Extractor + Extraction Prompt + Provider Factory) for facts per category; hands facts to the Entry Writer which writes the Fact Store; records each AI decision to the Audit Log; and writes the done-marker + retry state to the Document Store. Two new hooks plug in: the Upload Handler pushes a new doc id onto the queue the instant a capture finishes, and the Delete Handler prunes a deleted doc's id from every fact it backed.

### Q3 — Why build it this way

```
# Classify Extraction (Slice B) — Why Build It This Way
Scope: Shows the rules and existing patterns each new piece must conform to,
       and why. Builds on Q1 (the one-document flow) and Q2 (how it connects) —
       same names, same positions. Does NOT re-show the step-by-step flow.

How to read this:
  Solid boxes       = pieces that already exist (same as Q2)
  Dashed boxes      = new in this slice (same as Q2)
  RULE sticky notes = the rule or existing pattern that piece must respect
  ─ ─ shapes ─ ─►   = "this rule shapes this box, and here's why"


  ┌ ─ RULE ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  │ Publish the work queue on the running app's   │
  │ shared state INSIDE the app's startup wrapper.│
  │ NOT a fresh "on-startup" hook (silently       │
  │ ignored once a startup routine exists).        │
  │ Why: only then can the Upload Handler reach    │
  │ the SAME queue the worker drains.              │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┬ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
                          │ shapes how it starts
                          ▼
   ┌──────────────────┐        ┌──────────────────┐
   │ Catch-up Scan    │        │ Work Queue       │
   │ At startup finds │──fills─►│ Doc ids waiting, │
   │ unclassified docs│  w/ ids │ one at a time    │
   └──────────────────┘        └────────┬─────────┘
  ┌ ─ ─ ─ ─ ─ ─ ─ ┐                     │ pulls one
  │ Upload Handler│ ─push new doc id──►  │ id at a time
  │ (new hook)    │                     ▼
  └ ─ ─ ─ ┬ ─ ─ ─ ┘            ┌────────────────────┐
          │ shaped by          │  CLASSIFY MODULE   │
          ▼                    │  Orchestrator:     │
  ┌ ─ RULE ─ ─ ─ ─ ─ ─ ─ ┐    │  per category,     │
  │ Degrade silently when │    │  extract → write   │
  │ no queue is present    │    │  → audit, then     │
  │ (command-line, tests): │    │  stamp done        │
  │ skip the push.         │    └─┬───┬────┬────┬──┬─┘
  │ Why: capture must never│      │   │    │    │  │
  │ error just because the │ reads│   │    │    │  │writes done
  │ live queue is absent — │      │   │    │    │  │marker +
  │ the Catch-up Scan is    │     ▼   │    │    │  │retry state
  │ the safety net.        │ ┌─────────────┐│    │  ▼
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘ │ Knowledge   ││    │ ┌──────────────────┐
                            │ Categories  ││    │ │ Document Store   │
   asks/returns facts       │ config      ││    │ │ Per-doc row +    │
   per category │           └─────────────┘│    │ │ done marker +    │
                ▼                  hands    │    │ │ retry columns    │
  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐      facts to  │    │ └────────┬─────────┘
  │ AI Call Cluster (new): │         ▼      │    │          ▲ prunes
  │ Entity Extractor +     │  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ┐ │records   │ doc id
  │ Extraction Prompt +    │  │ Entry Writer    │ │each AI   │ from
  │ AI Provider Factory    │  │ Add/update/     │ │decision  │ each fact
  └ ─ ─ ─ ─ ─ ┬ ─ ─ ─ ─ ─ ┘  │ retire facts,   │ │          │
              │ shaped by      │ fold dups,      │ ▼          │
              ▼                │ prune sources   │┌──────────────────┐
  ┌ ─ RULE ─ ─ ─ ─ ─ ─ ─ ┐    └──┬──────────┬───┘│ Audit Log        │
  │ Go through the single │       │ writes   │    │ Records every    │
  │ AI Provider Factory —  │      │ facts to │    │ AI decision      │
  │ ask for the "classify" │      ▼          │    └────────┬─────────┘
  │ provider; never build  │ ┌──────────────┐│             ▲
  │ a provider directly.   │ │ Fact Store   ││   ┌ ─ ─ ─ ─ ─┴─ ─ ─ ─ ┐
  │ Why: the model is      │ │ One row per  ││   │ Delete Handler    │
  │ swapped by config      │ │ fact         ││   │ (new hook):       │
  │ (re-pointed at DeepSeek)│ └──────────────┘│  │ source-prune      │
  │ — zero code change.    │        ▲         │  └ ─ ─ ─ ┬ ─ ─ ─ ─ ─ ┘
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘        │ shaped by│         │ shaped by
                                    │          ▼         ▼
  ┌ ─ RULE ─ ─ ─ ─ ─ ─ ─ ┐   ┌ ─ RULE ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  │ Extraction Prompt is   │   │ Source-prune: look up the  │
  │ a version-controlled   │   │ doc's id by PATH *before*  │
  │ prompt file, never an  │   │ the row is deleted, then   │
  │ inline string in code. │   │ scan-and-filter facts in   │
  │ Why: reply format can  │   │ plain code. A fact left    │
  │ evolve without code    │   │ with no sources → flagged  │
  │ edits; it also renders │   │ pending, never deleted.    │
  │ the "what you got wrong│   │ Why: the id is gone after  │
  │ last time" feedback.   │   │ delete; provenance is      │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘   │ never silently destroyed.  │
                              └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘

  THREE RULES THAT SHAPE THE ENTRY WRITER (the one piece with real logic)

  ┌ ─ RULE ─ ─ ─ ─ ─ ─ ┐ ┌ ─ RULE ─ ─ ─ ─ ─ ─ ┐ ┌ ─ RULE ─ ─ ─ ─ ─ ─ ┐
  │ Re-decide each      │ │ Merge sources in    │ │ Before inserting a  │
  │ fact's confident/   │ │ plain code on an     │ │ "new" fact, check   │
  │ parked status by    │ │ update: read prior   │ │ for an exact same-  │
  │ handing confidence  │ │ sources, append this │ │ category+entity+tag │
  │ to the shared status│ │ doc, de-dup, write   │ │ non-retired twin    │
  │ helper. Never a     │ │ the merged list.     │ │ and fold into it.   │
  │ hardcoded threshold.│ │ Why: the store write │ │ Why: the AI only    │
  │ Why: project rule + │ │ OVERWRITES sources   │ │ sees a CAPPED list, │
  │ automated block ban │ │ wholesale — a naive  │ │ so it can't always  │
  │ hardcoded thresholds│ │ write erases prior   │ │ know a twin already │
  │ in pipeline code.   │ │ provenance.          │ │ exists.             │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘ └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘ └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘

  TWO RULES THAT SHAPE THE ORCHESTRATOR + ITS STATE

  ┌ ─ RULE ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐   ┌ ─ RULE ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  │ Tag each document run with a │   │ Retry state (try count +        │
  │ FRESH correlation id BEFORE  │   │ last-error) lives as new columns │
  │ any audit write — or every   │   │ on the per-document row, added   │
  │ audit write fails "missing   │   │ via a versioned migration file   │
  │ correlation id" and the doc  │   │ (not an in-code table alter).    │
  │ is never stamped (retries    │   │ "Parked" = a needs-review status │
  │ forever). Why: load-bearing  │   │ the work scan now skips. Why:    │
  │ for the happy path, not just │   │ keeps work discovery one-table;  │
  │ for logging.                 │   │ schema changes are versioned     │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘   │ files the runner auto-applies.   │
                                    └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘

  ┌ ─ DELETION (clears the old meaning) ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  │ The old "which folder?" classify logic and its tests are deleted │
  │ so the Classify Module's single meaning becomes "extract         │
  │ knowledge facts." Removing any new boundary above would scatter  │
  │ its logic back into these same callers, not collapse a layer.    │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

```
Simplified: Rules that apply to one box are pinned to it; the three Entry-Writer
            rules and two Orchestrator rules are grouped at the bottom (same as
            Slice A's "rules that apply to every new piece" block) instead of
            cluttering the central boxes. The per-category extract→write→audit
            loop is one Orchestrator box (it repeats once per knowledge category).
```

**Extension-point marking for each new piece:**
- Migration 011 — `[extensible: config]` (versioned `.sql`, auto-discovered by the `db.py` glob; drop file, no registry).
- `classify.max_retries` (K) — `[extensible: config]` (the retry cap is data, not a literal).
- `prompts/entity_extract.yaml` — `[extensible: config]` (the reply schema can evolve in YAML without touching code).
- Entity Extractor / Entry Writer / Orchestrator — `[closed]` (each has exactly one internal caller; plain `Result`-returning functions, not protocols — the design's seam check confirmed a per-action strategy object would be speculative for a single caller).
- Live-enqueue (queue on `app.state` + Upload Handler push) — `[closed]` (one producer, one consumer; the queue's owner is the app lifespan).
- Source-prune on delete — `[closed]` internally, but the **dedup strategy** (scan-and-filter vs a future JSON query) is `[extensible]` — swappable with no behavior change (OQ-P8B-01).

None of the `[closed]` markings hide a variant the spec expects. The provider is config-swappable (re-point `providers.classify` at DeepSeek) — the chosen extension surface for the M2 model swap.

---

## Approach

Build the ten components in **dependency order** so each phase is independently testable: the durable schema first (every retry-aware piece reads the new columns), then the config cap, then the prompt asset, then the storage round-trip + work-discovery filter, then the two pure pipeline functions (Entity Extractor, Entry Writer), then the orchestrator that composes them with the retry loop, then the two web-layer hooks (live-enqueue, source-prune), and finally the deletion of the old folder-routing classify. Each phase is tested against a temporary SQLite database and explicit config; the AI provider is **mocked in every test** (no real network, no spend in CI). The only phase that talks to a real model is the deploy-time pre-flight check (Phase 0), which is a config verification, not a code phase.

The order maps to phases below. The genuinely new risks are (a) the AI reply-schema contract between the Extraction Prompt and the Entry Writer's parse (resolved in Phase 3 by fixing the schema before either is built), (b) the correlation-id-before-audit ordering (load-bearing for the happy path, not just logging — Phase 7), and (c) the M2 config re-point (Phase 0 pre-flight).

| Phase | Spec component(s) | Why this position |
|---|---|---|
| 0 | M2 config pre-flight (handoff note) | A verification gate, not code — confirm the provider mapping before the first real classify call. |
| 1 | Component 1 — Migration 011 | Foundation — every retry-aware piece reads the new columns. |
| 2 | Component 2 — `classify.max_retries` (K) | Independent leaf; the orchestrator + prompt read it. |
| 3 | Component 3 — `prompts/entity_extract.yaml` | A YAML asset; fixing its reply schema first de-risks Phases 4–5. |
| 4 | Component 4 (storage half) — `find_unclassified` filter + retry-column round-trip + retry helpers | Needs Phase 1 columns; the orchestrator drives the helpers. |
| 5 | Component 5 — Entity Extractor | Needs Phase 3 prompt; mocks the provider. |
| 6 | Component 6 — Entry Writer | Needs Phase 1 (status) + the fact store; applies the facts Phase 5 produces. |
| 7 | Component 7 — Orchestrator (+ retry loop wiring) | Needs Phases 4, 5, 6; the integration capstone for the happy/retry/park paths. |
| 8 | Component 8 — Live-enqueue seam | Needs Phase 7 (an enqueued id must be processable). |
| 9 | Component 9 — Source-prune on delete | Independent of 5–8; needs the fact store only. |
| 10 | Component 10 — Delete the old folder-routing classify | Last, so the module is never left without a classify behavior between delete and rebuild. |

Note: the spec splits the retry loop into its own component 7 and the orchestrator into component 6; this plan folds the retry-loop's storage helpers into **Phase 4** (storage) and its orchestration into **Phase 7** (orchestrator), because the retry decisions are inseparable from the orchestrator's stamp/no-stamp fork. The component IDs are still referenced per phase.

---

## Phases

### Phase 0 — M2 provider pre-flight (config verification, no code)
_Implements the spec's deploy handoff note + design risk #2 ("`config.yaml` routes `classify: claude_cli`"). Reference A15._

**Goal**: Confirm — before any real classify call — that the `classify` task resolves to the intended OpenAI-compatible (DeepSeek) model, so the first paid extraction does not silently hit the wrong provider.

**Design** (what to confirm):
```
config/config.yaml:
  providers:
    classify: <provider key>      ← today "claude_cli"; M2 expects "openai"
  openai_compat:
    base_url: <DeepSeek endpoint>  ← must be the DeepSeek endpoint, not Fireworks
    model:    <DeepSeek model>     ← must be set
    api_key_env: <env var name>    ← must name a populated env var
```

**Steps**:
1. Read `config/config.yaml` `providers.classify` and the `openai_compat` block. Confirm with the deploy owner whether the demo runs against DeepSeek (then `classify` must map to the OpenAI-compatible provider) or stays on `claude_cli` for local dev.
2. If the demo target is DeepSeek: verify `openai_compat.base_url` + `model` are the DeepSeek values and the named API-key env var is populated in the deploy environment. **This is a config change, never a code change** (the code always calls `get_provider("classify", config)` — A15).
3. Record the decision (which provider the demo uses) in the implementation log so Phase 5/7 tests know which model is mocked vs real.

**Files to modify**: `config/config.yaml` — only if the deploy target requires re-pointing `providers.classify` (currently `providers.classify: claude_cli` at `config.yaml:67`; the top-level `classify:` block for the Slice A caps is separate, at `:84`). No source code.

**Notes / coupling**: This is a **pre-flight gate, not a TDD phase** — no test file. The hard line: do not run the first real classify-on-a-document until this is confirmed, or the bill lands on the wrong endpoint. Re-pointing is one YAML line (A15). Out of scope for *this slice's code*: the spec lists "re-pointing `providers.classify` at DeepSeek" as a deploy concern, not a code change.

**Test criteria**:
- [ ] `providers.classify` resolves (via `get_provider`) to the provider the demo intends.
- [ ] If DeepSeek: `openai_compat.base_url`, `model`, and the API-key env var are all set for the deploy environment.
- [ ] Decision recorded; no code touched.

**Status**: [ ] pending

---

### Phase 1 — Migration 011 (retry-state columns)
_Implements spec component 1 (P8-CLS-B-07 schema support). Reference the spec's Build/Done-when; this plan adds the TDD order, exact line bumps, and commit boundary. Assumes A16._

**Goal**: Give each document a durable place to remember how many times its classification has failed and what went wrong last time, so retries can be self-correcting and bounded.

**Design** (what changes):
```
documents:  + classify_attempts    INTEGER  DEFAULT 0
            + classify_last_error  TEXT     (nullable)
schema_version: 10 → 11   (auto-bumped by the migration runner glob; no registry)
```

**Steps** (TDD — RED first):
1. **RED** — Write `tests/test_storage/test_migration_011.py`: after `init_db(tmp_db)`, assert `schema_version == 11`; assert `documents` has `classify_attempts` defaulting to `0` and a nullable `classify_last_error` (insert a row omitting both, read back `0` / `None`); assert pre-existing `documents` rows survive the migration intact. Explicit `db_path`; **no module-scope `CONFIG`** (C-17). Run → fails (no migration file).
2. **GREEN** — Create `src/storage/migrations/011_classify_retry_state.sql` with two `ALTER TABLE documents ADD COLUMN …` statements. Follow the format of `010_classify_content_hash_and_ranking.sql` (header comment naming the slice + behavior id).
3. **GREEN (cascade)** — Bump the prior version-pin assertions `10 → 11`: `tests/test_storage/test_migration_007.py` (`:41`, `:56`), `test_migration_008.py` (`:47`), `test_migration_009.py` (`:38`), `test_migration_010.py` (`:16` — `assert version == 10`; the test name `test_migration_010_sets_schema_version_to_10` is cosmetic, the assertion is what binds). This is the expected migration cascade, NOT a regression (CLAUDE.md "every new migration breaks the previous migration's version-pin test"). Grep `grep -rn "== 10" tests/test_storage/` first to catch any pin missed by line drift.
4. Run `uv run pytest tests/test_storage/` → all green.

**Files to modify**:
- `src/storage/migrations/011_classify_retry_state.sql` — **new** (2 ADD COLUMN stmts + header).
- `tests/test_storage/test_migration_011.py` — **new**.
- `tests/test_storage/test_migration_007.py`, `_008.py`, `_009.py`, `_010.py` — version-pin bump only.

**Notes / coupling**: Auto-discovery confirmed (A16): `db.py:36` runs `sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))` — drop-file-only, no registry. `classify_attempts` defaults to `0` so legacy rows start fresh; `classify_last_error` is nullable so first-attempt docs render an empty feedback block (Phase 3).

**Test criteria**:
- [ ] `schema_version == 11` after `init_db`.
- [ ] `documents.classify_attempts` defaults `0`; `documents.classify_last_error` nullable, defaults `None`.
- [ ] Pre-seeded `documents` rows survive the migration readable.
- [ ] Full `tests/test_storage/` green (version pins bumped to 11).

**Status**: [ ] pending

---

### Phase 2 — Config: `classify.max_retries` (K)
_Implements spec component 2 (P8-CLS-B-07 support). Reference the spec's Build/Done-when._

**Goal**: Make the retry cap a tunable, not a number baked into the code.

**Design**:
```
core/config.py:
  class ClassifyConfig(BaseModel):           # extend the Slice A model
      max_content_tokens: int = 10000        # (existing)
      max_entries_per_dimension: int = 50    # (existing)
      max_retries: int = Field(default=3, ge=1)   # NEW (K)

config/config.yaml (existing top-level classify: block — Slice A):
  classify:
    max_content_tokens: 10000
    max_entries_per_dimension: 50
    max_retries: 3            ← NEW
```

**Steps** (TDD — RED first):
1. **RED** — Add to `tests/test_core/test_config.py`: assert `cfg.classify.max_retries == 3` by default; assert a YAML override takes effect; assert `max_retries = 0` is rejected by validation (`ge=1`). Pass explicit config; **no module-scope `CONFIG`** (C-17). Run → fails.
2. **GREEN** — Add `max_retries: int = Field(default=3, ge=1)` to `ClassifyConfig` in `core/config.py:336` (alongside the two existing caps).
3. **GREEN** — Add `max_retries: 3` to the existing top-level `classify:` block in `config/config.yaml`.
4. Run `uv run pytest tests/test_core/test_config.py` → green.

**Files to modify**:
- `src/core/config.py` — one field on `ClassifyConfig`.
- `config/config.yaml` — one key under `classify:`.
- `tests/test_core/test_config.py` — default + override + `ge=1` reject test.

**Notes / coupling**: Use Pydantic `Field` (human-configured value — CLAUDE.md "Field vs @property"). It is an **int**, not a confidence float, so it lives in the plain `classify:` block, NOT `thresholds.yaml`, and will not trip the C-06 float hook — but the no-literal rule still binds downstream: the orchestrator (Phase 7) compares the try-count against `config.classify.max_retries`, never a literal `3`. `[extensible: config]`.

**Test criteria**:
- [ ] `config.classify.max_retries` reads from config, defaults to `3`.
- [ ] YAML override takes effect; `max_retries = 0` rejected.
- [ ] No Slice B code compares the try-count against a literal.
- [ ] `tests/test_core/test_config.py` green.

**Status**: [ ] pending

---

### Phase 3 — Extraction Prompt (`prompts/entity_extract.yaml`)
_Implements spec component 3 (P8-CLS-B-01 support). Reference the spec's Build/Done-when. Resolves the spec's component-3 schema decision._

**Goal**: Give the AI a single, version-controlled instruction set for pulling structured facts out of one document for one category — including a slot to tell it what it got wrong last time — and **fix the reply schema now** so the Entity Extractor (Phase 5) and Entry Writer (Phase 6) parse exactly one contract.

**Design** (the locked reply contract — resolves the component-3 decision: per-fact `action`, one flat list):
```
prompts/entity_extract.yaml renders (system, user) with template vars:
  - document_text          ← Content Reader output
  - dimension_guidance     ← guidance for this one category (dimensions.yaml)
  - existing_facts         ← each known fact shown WITH its database id
  - previous_attempt_feedback  ← "" on first attempt; saved last-error on retry

Reply contract (JSON only, no markdown — mirror classify.yaml's style):
  [
    { "action": "new",    "entity": "...", "tag": "...",
      "fact": "...", "confidence": 0.0-1.0 },
    { "action": "update", "id": <existing id>, "entity": "...", "tag": "...",
      "fact": "...", "confidence": 0.0-1.0 },
    { "action": "retire", "id": <existing id>, "reason": "..." }
  ]
  - "new"    omits id (a brand-new fact)
  - "update" / "retire" carry the referenced id
  - one flat list the Entry Writer iterates in order
```

**Steps** (TDD — RED first):
1. **RED** — Write `tests/test_prompts/test_entity_extract_prompt.py` (or extend the existing prompt-render test module): render `PROMPTS["entity_extract"]` with a document, a dimension guidance string, a list of existing facts each with an id, and a feedback string. Assert the returned `(system, user)` pair: contains each existing fact's id; contains the guidance text; contains the feedback when non-empty AND renders cleanly (no leftover template markers) when feedback is `""`; instructs JSON-only output with the per-fact `action` schema above. Run → fails (no YAML).
2. **GREEN** — Create `prompts/entity_extract.yaml` with `system` + `user` templates rendering the four vars and stating the reply contract. Mirror the JSON-only, no-markdown phrasing of `prompts/classify.yaml`. The existing-facts block must show each fact's id explicitly so the AI can target `update`/`retire` by id and omit id for `new`.
3. Run the prompt-render test → green.

**Files to modify**:
- `prompts/entity_extract.yaml` — **new** (the ONLY extraction prompt source — C-07).
- `tests/test_prompts/test_entity_extract_prompt.py` — **new** (render assertions).

**Notes / coupling**: C-07 — never an inline f-string; rendered only via `PROMPTS["entity_extract"].render(...)` (the hook warns on prompt-like f-strings). The reply schema fixed here is the contract Phase 5 parses and Phase 6 routes on — **do not change it in Phase 5/6 without updating this file and its test**. `[extensible: config]` (schema evolves in YAML). The known Slice A TDs around `context_loader` (per-doc re-query, hardcoded `dimensions.yaml` path) now matter more because each render precedes a *paid* AI call — see Tech Debt below; not fixed in this phase.

**Test criteria**:
- [ ] Rendering with facts+ids, guidance, and feedback produces a `(system, user)` pair showing the ids, the guidance, and the feedback.
- [ ] Empty feedback renders cleanly (no stray template text).
- [ ] The prompt states the JSON-only per-fact `action` reply contract.
- [ ] Prompt-render test green.

**Status**: [ ] pending

---

### Phase 4 — Storage: work-discovery filter + retry-column round-trip + retry helpers
_Implements spec component 4 (storage half) + component 7's persistence (P8-CLS-B-07, P8-CLS-B-08). Reference the spec's Build/Done-when. **Depends on Phase 1.** Assumes A8, A9._

**Goal**: Teach the document store to (a) skip parked documents when finding work, (b) round-trip the new retry columns, and (c) save-error/increment on failure and clear-on-success — the durable backbone the orchestrator's retry loop drives.

**Design**:
```
find_unclassified  (documents.py:634) — ADD a status filter:
  SELECT id FROM documents
  WHERE (classify_content_hash IS NULL OR classify_content_hash != content_hash)
    AND (status IS NULL OR status != 'needs-review')    ← NEW (park = skipped)
  → Result[list[int]]

DocumentRow         + classify_attempts: int = 0
                    + classify_last_error: str | None = None
_row_from_sqlite    reads both defensively (same pattern as classify_content_hash)

NEW helpers (the orchestrator drives them):
  record_classify_failure(doc_id, error, *, db_path=None) -> Result[int]
     UPDATE documents SET classify_attempts = classify_attempts + 1,
                          classify_last_error = ?  WHERE id = ?
  clear_classify_retry_state(doc_id, *, db_path=None) -> Result[int]
     UPDATE documents SET classify_attempts = 0, classify_last_error = NULL WHERE id = ?
  park_document(doc_id, *, db_path=None) -> Result[int]
     UPDATE documents SET status = 'needs-review' WHERE id = ?
  load_classify_retry_state(doc_id, *, db_path=None) -> Result[tuple[int, str|None]]
     (attempts, last_error)  — for the orchestrator to render feedback + check the cap
```

**Steps** (TDD — RED first):
1. **RED** — In `tests/test_storage/test_documents.py`: seed a v11 temp DB with documents in mixed states; assert (a) `find_unclassified` excludes a `status='needs-review'` row even when its fingerprint is NULL, and still returns NULL/stale-fingerprint non-parked rows; (b) `DocumentRow.classify_attempts`/`classify_last_error` round-trip; (c) `record_classify_failure` increments attempts and saves the error; (d) `clear_classify_retry_state` resets to `0`/`None`; (e) `park_document` sets `status='needs-review'`; (f) `load_classify_retry_state` returns the current `(attempts, last_error)`. Explicit `db_path`. Run → fails.
2. **GREEN** — Add the two fields to `DocumentRow` and read them defensively in `_row_from_sqlite` (`classify_attempts=row[...] if "classify_attempts" in row.keys() else 0`, same for `classify_last_error` → `None`).
3. **GREEN** — Add the `AND (status IS NULL OR status != 'needs-review')` clause to `find_unclassified` (single-table, no join — A8).
4. **GREEN** — Add the four retry helpers, each reusing `get_connection`, each returning `Result`.
5. Run `uv run pytest tests/test_storage/test_documents.py` → green.

**Files to modify**:
- `src/storage/documents.py` — `DocumentRow` fields, `_row_from_sqlite`, `find_unclassified` filter, four new helpers.
- `tests/test_storage/test_documents.py` — filter + round-trip + helper tests.

**Notes / coupling**: The `needs-review` overload is collision-free **today** — research confirmed no src code reads `needs-review` back (A9, edge-case note); capture only *writes* it. If a future capture path branches on `needs-review`, a discriminator (`classify_attempts > 0`) is the documented fallback — note it, do not build it now. Two different guards on two different tables: `find_unclassified`'s `status != 'needs-review'` (documents) is NOT the Entry Writer's `status != 'retired'` dedup (knowledge_entries) — do not conflate (research edge case). `[closed]` — internal queries. Do not touch `replace_path`/`rename`/`delete_by_path` search-table cleanup (out of scope; CLAUDE.md Phase 3 gotcha).

**Test criteria**:
- [ ] `find_unclassified` excludes `needs-review` rows; still returns NULL/stale non-parked rows (P8-CLS-B-08).
- [ ] Retry columns round-trip; failure increments + saves; success clears; park sets `needs-review`; load returns `(attempts, last_error)` (P8-CLS-B-07).
- [ ] `tests/test_storage/test_documents.py` green.

**Status**: [ ] pending

---

### Phase 5 — Entity Extractor
_Implements spec component 5 (P8-CLS-B-01). Reference the spec's Build/Done-when. **Depends on Phase 3.** Assumes A15. Mirror the old `classify()` error pattern (`classify.py:137-251`)._

**Goal**: Ask the AI one focused question per knowledge category and turn its reply into structured facts the writer can apply — failing loudly and recoverably when the reply is unusable.

**Design**:
```
NEW function in pipelines/classify.py:
  extract(dimension, text, existing_facts, guidance, feedback, config)
    -> Result[list[fact]]
  steps:
    1. render PROMPTS["entity_extract"] with the 4 vars (Phase 3)
    2. provider = get_provider("classify", config)        ← C-08, single dispatch
    3. reply = provider.complete(system, user)
    4. json.loads(reply) → validate each fact's fields against the Phase-3 schema
  error mapping (mirror old classify):
    template render error          → Failure(recoverable=False)
    provider failure / JSON parse / field-validation error → Failure(recoverable=True)
    truncate the raw reply to 200 chars in the Failure context
```

**Steps** (TDD — RED first):
1. **RED** — Write `tests/test_pipelines/test_entity_extractor.py`: inject a **fake provider** (a stub whose `complete` returns a canned string) — never a real network call. Assert: a valid-JSON reply → `Success(list of parsed facts)` with `action`/`entity`/`tag`/`fact`/`confidence` (and `id` for update/retire); an unparseable reply → recoverable `Failure` whose context carries a ≤200-char snippet of the raw reply; a reply missing required fields → recoverable `Failure`; a forced template-render error → non-recoverable `Failure`. Pass explicit config; **no module-scope `CONFIG`** (C-17). Run → fails.
2. **GREEN** — Add `extract(...)` as a new function in `pipelines/classify.py`, kept separate from the soon-to-die folder-routing code. Render via `PROMPTS["entity_extract"]`; dispatch via `get_provider("classify", config)` (C-08, never instantiate a provider directly); `json.loads` + per-fact field validation against the Phase-3 schema; map errors per the design; truncate the raw reply to 200 chars in the failure context. Return `Result` (C-12).
3. Run `uv run pytest tests/test_pipelines/test_entity_extractor.py` → green.

**Files to modify**:
- `src/pipelines/classify.py` — `extract(...)` (new function).
- `tests/test_pipelines/test_entity_extractor.py` — **new** (mocked provider; parse + error cases).

**Notes / coupling** (KNOWN COUPLING flag): the extractor is hardcoded to the `"classify"` task name (Grid C — reuse the freed `classify` task; the model behind it is swapped by config, A15). `# COUPLING:` the dimension→prompt→provider call is one focused per-dimension request (D8); cross-dimension holistic extraction is deferred (TD-068). `[closed]` — one caller (the orchestrator); a protocol would be speculative (matches Slice A seam policy). The float hook does not fire here (no `if/elif` float compare); confidence is data passed through to the Entry Writer.

**Test criteria**:
- [ ] Valid-JSON reply → parsed facts; per-fact `action` schema honored (P8-CLS-B-01).
- [ ] Unparseable / field-incomplete reply → recoverable `Failure` with ≤200-char raw-reply snippet.
- [ ] Template error → non-recoverable `Failure`.
- [ ] Provider reached only via `get_provider("classify", config)`; tests mock it.
- [ ] `tests/test_pipelines/test_entity_extractor.py` green.

**Status**: [ ] pending

---

### Phase 6 — Entry Writer
_Implements spec components 5→ "Entry Writer" (P8-CLS-B-02, P8-CLS-B-03, P8-CLS-B-09). Reference the spec's Build/Done-when. **Depends on Phase 1** (status re-gate uses confidence) **+ the fact store.** Assumes A3, A4, A5, A6._

**Goal**: Apply the extracted facts to the Fact Store safely — adding, editing, or retiring — so that re-running after a partial failure does not corrupt or duplicate the table.

**Design**:
```
NEW function in pipelines/classify.py:
  write_entries(facts, doc_id, dimension, band, db_path) -> Result[WriteSummary]
  per fact, route by action:
    new    → query_by_entity(entity); in Python filter to status != 'retired'
             AND dimension == dim AND tag == tag → if a twin exists, FOLD
             (treat as update of that id); else upsert a fresh row with
             sources = [doc_id]
    update → read the referenced id's existing sources; append doc_id; dedupe;
             upsert the MERGED list  (upsert OVERWRITES sources wholesale — A3)
    retire → retire(id, reason)   (never deletes — A5)
  on every write: re-gate status via confidence_to_status(confidence, band)
                  (tags.py:255 — band.route; never a float compare — C-06)
  hallucinated id (update/retire referencing a non-existent id):
                  SKIP + log + record in the summary so the orchestrator
                  WITHHOLDS the stamp (D9)
  WriteSummary distinguishes "all clean" from "≥1 skipped".
```

**Steps** (TDD — RED first):
1. **RED** — Write `tests/test_pipelines/test_entry_writer.py` against a v11 temp DB seeded with known facts. Assert: (a) a `new` fact matching an existing non-retired `dimension+entity+tag` adds NO second row and folds (its sources gain `doc_id`); (b) a `new` fact with no twin inserts a fresh row with `sources == [doc_id]`; (c) an `update` to an existing id keeps its prior sources PLUS `doc_id`, deduped (no duplicate id); (d) a `retire` flips the entry to `retired` without deleting it; (e) every written fact's `status` equals `confidence_to_status(confidence, band)`; (f) a fact referencing a non-existent id is skipped, logged, and the returned summary marks "not all clean". Explicit `db_path`. Run → fails.
2. **GREEN** — Add `write_entries(...)` as a new function in `pipelines/classify.py`. Implement the routing above. The **source merge for update is in Python** — read existing sources, append, dedupe, then `upsert` the merged list (A3). The **dedup for new** uses `query_by_entity` then filters `status != 'retired'` + matches dimension+tag in Python (A4 — `query_by_entity` returns ALL statuses for the entity). Status via `confidence_to_status` (C-06 — no float literal). Return `Result[WriteSummary]` (C-12).
3. Run `uv run pytest tests/test_pipelines/test_entry_writer.py` → green.

**Files to modify**:
- `src/pipelines/classify.py` — `write_entries(...)` + a small `WriteSummary` result shape (clean-vs-skipped).
- `tests/test_pipelines/test_entry_writer.py` — **new** (fold / update-merge / retire / status-regate / hallucinated-id cases).

**Notes / coupling**: This is the one new piece with real internal branching — kept a function in `classify.py` (single caller; a per-action strategy object would be a speculative 1-adapter seam — design note, `[closed]`). **OQ-P8B-02 (locked):** re-gate status on EVERY write, including an update that lowers confidence — flag for observation, not a blocker (a "max confidence seen" rule is a later refinement if demotion thrashes). The fold-on-new dedup is **exact-entity only** — "Anthony" vs "Anthony Nguyen" stay separate (D6; entity resolution deferred). Stdlib logging for skip warnings: `%s`-style, no kwargs.

**Test criteria**:
- [ ] `new` matching an existing non-retired twin folds (no second row, source appended) (P8-CLS-B-03).
- [ ] `update` preserves prior sources + adds `doc_id`, deduped (P8-CLS-B-02).
- [ ] `retire` flips to `retired`, never deletes.
- [ ] Every written fact's status matches `confidence_to_status` (no float compare).
- [ ] Hallucinated id skipped + logged + surfaced so the doc is not stamped (P8-CLS-B-09).
- [ ] `tests/test_pipelines/test_entry_writer.py` green.

**Status**: [ ] pending

---

### Phase 7 — Orchestrator + retry loop
_Implements spec components 6 + 7 (P8-CLS-B-04, P8-CLS-B-05, P8-CLS-B-07, P8-CLS-B-08, M1 correlation id). Reference the spec's Build/Done-when. **Depends on Phases 3, 4, 5, 6.** Assumes A6, A7, A10._

**Goal**: Run one document end-to-end — per category extract → write → audit — mark it done only when everything succeeded, and otherwise drive the bounded self-correcting retry loop (save-feedback → retry → park at the cap).

**Design**:
```
NEW orchestrate(doc_id, config, db_path) -> Result, called by the consumer at
the Slice B seam (classify.py:431):

  new_correlation_id()                       ← ONCE, BEFORE any audit (A7/M1)
  text   = content_reader(doc_id, ...)       ← existing (Slice A)
  facts_by_dim = context_loader(...)         ← existing (Slice A)
  attempts, last_error = load_classify_retry_state(doc_id)   ← Phase 4

  all_clean = True
  for dimension in configured dimensions:
      extracted = extract(dimension, text, facts_by_dim[dimension],
                          guidance, feedback=last_error, config)   ← Phase 5
      if Failure: all_clean = False ; record + break-or-continue
      summary  = write_entries(extracted, doc_id, dimension, band, db_path) ← Phase 6
      if Failure or summary.has_skips: all_clean = False
      core.audit.write(decision, pipeline="classify", stage=dimension,
                       outcome=...)            ← C-13; per dimension
      if audit Failure: all_clean = False

  if all_clean:
      stamp_classified(doc_id)                ← exactly once; rowcount 0 ⇒ FAILURE
      clear_classify_retry_state(doc_id)      ← Phase 4
  else:
      record_classify_failure(doc_id, error)  ← Phase 4 (save + increment)
      attempts, _ = load_classify_retry_state(doc_id)
      if attempts >= config.classify.max_retries:    ← K from config, no literal
          park_document(doc_id)                        ← status='needs-review'
          core.audit.write(... outcome="parked" ...)   ← one "parked" record (D10)
      # else: leave un-stamped → Work Finder re-queues it
```

**Steps** (TDD — RED first):
1. **RED (happy path)** — In `tests/test_pipelines/test_classify_orchestrator.py` (new): inject a fake provider returning valid facts for every dimension; run `orchestrate` on a seeded v11 doc. Assert: exactly one `stamp_classified` (its `classify_content_hash` now equals `content_hash` → `find_unclassified` no longer returns it); one audit record per dimension; the run carried a single fresh correlation id; retry state is cleared (`attempts == 0`, `last_error is None`).
2. **RED (correlation-id guard)** — Assert that WITHOUT a correlation id the audit write fails — i.e. verify the orchestrator calls `new_correlation_id()` before the first audit (a regression here would make every audit return "missing correlation_id", fail the dimension, and never stamp — research edge case). Verify `stamp_classified` returning rowcount 0 (deleted-mid-run id) is treated as a **failure**, not success (research edge case).
3. **RED (retry path)** — Inject a provider that returns a hallucinated id (or unparseable reply) on one dimension; run `orchestrate`. Assert: no stamp; `classify_attempts` incremented by 1; `classify_last_error` saved; the doc is still returned by `find_unclassified`. Then run again with attempts already at `K-1`: assert the doc is parked (`status='needs-review'`, `attempts == K`), a "parked" audit record exists, and `find_unclassified` no longer returns it.
4. **RED (feedback loop)** — Assert that on a retry the saved `classify_last_error` is passed into `extract(... feedback=...)` so the prompt renders `previous_attempt_feedback` (spy on the extractor's `feedback` arg).
5. **GREEN** — Implement `orchestrate(...)` in `pipelines/classify.py` per the design. Call `new_correlation_id()` first. Loop dimensions; extract → write → audit; compute `all_clean`; stamp+clear on success, else save-error+increment and park-at-cap. Compare attempts against `config.classify.max_retries` (no literal — C-06 spirit). Treat `stamp_classified` rowcount 0 as failure. Return `Result` (C-12).
6. **GREEN (wire the seam)** — In `consumer` (`classify.py:388`), replace the Slice B seam comment block (`:431-435`) with `await`/call to `orchestrate(doc_id, config, db_path)`; log + propagate its `Result`; keep `queue.task_done()` in `finally`.
7. Run `uv run pytest tests/test_pipelines/test_classify_orchestrator.py` + the existing worker test → green.

**Files to modify**:
- `src/pipelines/classify.py` — `orchestrate(...)` (new); fill the consumer seam.
- `tests/test_pipelines/test_classify_orchestrator.py` — **new** (happy / correlation-id / retry / park / feedback).
- `tests/test_pipelines/test_classify_worker.py` (Slice A) — extend to assert the consumer now calls `orchestrate` (the seam is filled) without breaking the one-at-a-time / drain assertions.

**Notes / coupling** (the load-bearing ordering trap): `new_correlation_id()` MUST run before any `core.audit.write` or every audit returns `Failure("missing correlation_id")`, the dimension counts as failed, and the doc retries forever until parked (A7, research edge case — it is load-bearing for the happy path, not just logging). Per-dimension audit uses `core.audit.write` (C-13), NOT `storage.audit_log.append`. The stamp is the LAST action and gated on perfection (D4 + D9). `[closed]` — the natural completion of the consumer loop, one file, seam at the queue (Grid D). **TD (re-confirm):** `context_loader` re-reads `dimensions.yaml` + re-queries facts per document; now each enqueued item is a paid AI call, so caching dimensions+facts once per consumer session is the clean fix (Slice A TD, raised priority — see Tech Debt below).

**Test criteria**:
- [ ] Every-category-success → exactly one stamp, one audit per dimension, single correlation id, retry state cleared (P8-CLS-B-04, P8-CLS-B-05).
- [ ] No correlation id ⇒ audit fails ⇒ no stamp (guard); `stamp_classified` rowcount 0 treated as failure.
- [ ] Any-category-failure → no stamp, attempts incremented, last-error saved, still re-queued (P8-CLS-B-07).
- [ ] At the cap → parked (`needs-review`, attempts == K), "parked" audit, not re-queued (P8-CLS-B-08).
- [ ] On retry, saved last-error is passed as feedback into the extractor.
- [ ] The consumer seam now calls `orchestrate`; worker still processes one-at-a-time and drains.
- [ ] New + extended test files green.

**Status**: [ ] pending

---

### Phase 8 — Live-enqueue seam (capture → live queue)
_Implements spec component 8 (P8-CLS-B-06). Reference the spec's Build/Done-when. **Depends on Phase 7.** Assumes A10, A11, A12._

**Goal**: Classify a document the moment it is captured while the container runs — not at the next reboot.

**Design**:
```
cloud_entry.py::_wrap_lifespan (:96):
   queue = asyncio.Queue()                       (existing, :118)
+  app_ref.state.classify_queue = queue          ← publish on app.state (A12)

mcp_server/api.py::upload_handler (:96):
   after capture_upload returns Success(document_id) (:214):
+  queue = getattr(request.app.state, "classify_queue", None)
+  if queue is not None:
+      queue.put_nowait(document_id)             ← live push (row_id IS doc id — A10)
+  # absent queue (CLI/tests) → skip silently; Catch-up Scan is the net (D1)
```

**Steps** (TDD — RED first):
1. **RED (lifespan publishes the queue)** — In `tests/test_mcp_server/test_cloud_entry.py` (extend): build the app, enter its composed lifespan, assert `app.state.classify_queue` is the live `asyncio.Queue` the consumer drains (same object). Run → fails.
2. **RED (upload pushes; absent-queue degrades)** — In `tests/test_mcp_server/test_api.py` (or the upload-handler test module): with a fake `app.state.classify_queue`, post a successful upload and assert the returned `document_id` was `put_nowait` onto that queue; with NO `classify_queue` on `app.state`, assert the same upload still returns `200` and does NOT raise (silent skip). Run → fails.
3. **GREEN** — In `_wrap_lifespan`, assign `app_ref.state.classify_queue = queue` inside `_composed` (where the queue is created, `:118`), before `catch_up_scan`. In `upload_handler`, after the `Success(document_id)` branch (`:214`), read the queue via `getattr(request.app.state, "classify_queue", None)` and `put_nowait(document_id)` only if present; skip silently otherwise.
4. Run `uv run pytest tests/test_mcp_server/test_cloud_entry.py tests/test_mcp_server/test_api.py` → green.

**Files to modify**:
- `src/mcp_server/cloud_entry.py` — publish the queue on `app.state` (one line in `_composed`).
- `src/mcp_server/api.py` — push `document_id` after a successful upload; skip if no queue.
- `tests/test_mcp_server/test_cloud_entry.py` — `app.state.classify_queue` is the live queue.
- `tests/test_mcp_server/test_api.py` — push-on-success + absent-queue-degrades.

**Notes / coupling** (KNOWN COUPLING): the live-enqueue couples `cloud_entry.py` and `api.py` through `app.state` — `# COUPLING:` the queue is created by the lifespan and read by the upload handler; if absent (CLI, tests) the handler MUST skip silently and rely on the next Catch-up Scan (D1, A11/A12). `put_nowait` on the **unbounded** queue cannot block; under extreme load it could grow memory — accepted (single-user scale; the sequential consumer drains it). Only push on the `Success(document_id)` branch — a Failure must not enqueue (and the binary/text paths both return `document_id` on success — A10). `[closed]`.

**Test criteria**:
- [ ] App lifespan publishes the live consumer queue on `app.state.classify_queue` (P8-CLS-B-06).
- [ ] A successful upload `put_nowait`s the new `document_id` onto that queue.
- [ ] An upload with no queue present returns `200` and does not raise (CLI/test degradation).
- [ ] Both test files green.

**Status**: [ ] pending

---

### Phase 9 — Source-prune on delete
_Implements spec component 9 (P8-CLS-B-10). Reference the spec's Build/Done-when. **Independent of Phases 5–8** (fact store only). Assumes A13, A14._

**Goal**: When a source document is deleted, shrink provenance correctly instead of leaving facts pointing at a document that no longer exists — and never silently destroy a fact.

**Design**:
```
api.py::_delete_with_blob_cleanup (:330):
  pre_read = get_by_path(vault_path)            (existing, :366) → row.id BEFORE delete
+ doc_id = row.id                               ← captured before delete_by_path (:377) — A13
  delete_by_path(vault_path)                    (existing, :377)
+ prune_sources(doc_id, db_path=...)            ← NEW (knowledge_entries.py)

NEW prune_sources(doc_id, *, db_path=None) -> Result[int] in knowledge_entries.py:
  for every NON-RETIRED entry whose sources contains doc_id (scan-and-filter
  in Python — OQ-P8B-01):
      remove doc_id ; dedupe
      if sources now empty → set status = 'pending'   (NEVER auto-delete/retire — D3)
      else                 → upsert the shrunk sources list
  → Result[count of entries touched]
```

**Steps** (TDD — RED first):
1. **RED** — In `tests/test_storage/test_knowledge_entries.py`: seed facts whose `sources` contain a target id (some with it alongside other ids, one with it as the SOLE source, one retired fact that also lists it, one fact NOT listing it). Call `prune_sources(target_id)`. Assert: multi-source facts lose only the target id (others intact, deduped); the sole-source fact ends with empty sources AND `status='pending'` (not deleted, not retired); the retired fact is untouched (non-retired filter); the unrelated fact is untouched. Explicit `db_path`. Run → fails.
2. **RED (delete-path integration)** — In `tests/test_mcp_server/test_api.py` (or the delete-path test): seed a doc + a fact sourced by it; call `_delete_with_blob_cleanup(path)`; assert the doc row is gone AND the fact's sources no longer contain the doc id; deleting a doc that backs no facts changes no facts. Run → fails.
3. **GREEN** — Add `prune_sources(...)` to `knowledge_entries.py` (scan non-retired entries via `query_by_dimension`/a full non-retired scan, filter in Python for `doc_id in sources`, rewrite). In `_delete_with_blob_cleanup`, capture `row.id` from the existing `get_by_path` pre-read (`:366`) BEFORE `delete_by_path` (`:377`), then call `prune_sources(doc_id, db_path=...)` after the delete succeeds.
4. Run `uv run pytest tests/test_storage/test_knowledge_entries.py tests/test_mcp_server/test_api.py` → green.

**Files to modify**:
- `src/storage/knowledge_entries.py` — `prune_sources(...)` (new; scan-and-filter).
- `src/mcp_server/api.py` — capture `doc_id` before delete; call `prune_sources` after.
- `tests/test_storage/test_knowledge_entries.py` — prune unit tests.
- `tests/test_mcp_server/test_api.py` — delete-path integration.

**Notes / coupling**: The id lookup MUST precede the delete (the delete signal arrives by path, but `sources` holds ids — A13; the existing pre-read at `:366` already has the row). **OQ-P8B-01 (resolved): scan-and-filter in Python** — no existing query finds facts by a contained source id (A14); JSON1 IS available on the deployed image but scan-and-filter is the dependency-free Slice B route, swappable for a `json_each`/`json_extract` query later with no behavior change. **Empty-sources → `pending`, never delete/retire** (D3) — provenance is never silently destroyed. `[closed]` internally; the dedup strategy is `[extensible]`. **Re-confirm TD:** scan-and-filter reads every non-retired fact per delete — negligible at single-user scale; note for a JSON query if fact counts grow (TD below).

**Test criteria**:
- [ ] Multi-source facts lose only the deleted id (rest intact, deduped) (P8-CLS-B-10).
- [ ] A sole-source fact ends empty + `status='pending'` — not deleted, not retired.
- [ ] Retired facts and unrelated facts are untouched.
- [ ] Delete path captures the id before delete and prunes; deleting a doc backing no facts is a no-op.
- [ ] Both test files green.

**Status**: [ ] pending

---

### Phase 10 — Delete the old folder-routing classify
_Implements spec component 10 (P8-CLS-B-11, P8-CLS-B-12). Reference the spec's Build/Done-when. **Depends on Phases 5–7** (the module must already have its new classify behavior). Assumes A1, A2._

**Goal**: Remove the dead "which folder?" classify so the module's single meaning becomes "extract knowledge facts" — and remove the tests and dead fixture that only exercised it.

**Design** (what is removed):
```
pipelines/classify.py — DELETE: build_subject (:22), build_folder_subject (:52),
   _destination_names (:70), ClassifyResult (:97), classify (:113)
   KEEP: content_reader (:259), context_loader (:320), consumer (:388),
         catch_up_scan (:446), and the new Slice B functions (extract,
         write_entries, orchestrate).
tests/test_pipelines/test_classify.py — DELETE entirely (33 tests, imports
   exactly the five deleted symbols).
tests/test_pipelines/conftest.py — DELETE the dead _stub_classify block +
   ClassifyResult import (:79-96). Verified dead: pipelines.capture has no
   `classify` attr; no test requests the pipeline_ctx fixture — the
   monkeypatch would AttributeError if ever exercised (A2, research).
```

**Steps**:
1. **GREEN (delete code)** — Remove the five symbols from `pipelines/classify.py`. Confirm via grep that no `src/` file imports any of them (A1 — verified: `cloud_entry.py:112` imports only the kept `catch_up_scan, consumer`).
2. **GREEN (delete tests)** — Delete `tests/test_pipelines/test_classify.py`. Delete the `_stub_classify` block + `ClassifyResult` import in `tests/test_pipelines/conftest.py:79-96` (A2 — verified dead landmine, not live behavior; no fixture repair needed).
3. **VERIFY** — Run the full suite `uv run pytest tests/` → green. The ONLY expected changes are the deleted file and the Phase-1 version-pin cascade; any other failure means a missed importer — investigate before committing.

**Files to modify**:
- `src/pipelines/classify.py` — delete five symbols.
- `tests/test_pipelines/test_classify.py` — **delete file**.
- `tests/test_pipelines/conftest.py` — delete dead block (`:79-96`).

**Notes / coupling**: A1/A2 confirmed by research — the five symbols are imported ONLY by `test_classify.py` and the dead conftest block; deleting the conftest block removes a latent landmine (`monkeypatch.setattr` with default `raising=True` would `AttributeError`), not live behavior. Sequence AFTER Phases 5–7 so the module is never left without a classify behavior between delete and rebuild (a planning/ordering concern — not a hard runtime dependency).

**Test criteria**:
- [ ] None of the five old symbols remain in `pipelines/classify.py` (P8-CLS-B-11).
- [ ] `test_classify.py` deleted; dead conftest block deleted (P8-CLS-B-12).
- [ ] Full `tests/` green (only expected breakage: the deleted file + version-pin cascade).

**Status**: [ ] pending

---

## Open Questions

All three carried open questions are resolved upstream (research validated each); they are flagged for observation, not blocking:

- **OQ-P8B-01 — finding facts by a contained source id (on delete).** **Resolved: scan-and-filter in Python** (Phase 9). No existing query does this (A14); JSON1 IS available on the deployed `python:3.12-slim` image, but scan-and-filter is dependency-free and swappable for a `json_each`/`json_extract` query later with zero behavior change. Re-confirm as a TD if fact counts grow (Tech Debt below).
- **OQ-P8B-02 — does an update that lowers confidence demote a fact's status?** **Resolved: re-gate on every write** (Phase 6, the locked broad-grill behavior — confidence is a live signal). Flag for observation: a single hedged mention can demote an established fact; a "max confidence seen" rule is a later refinement if demotion thrashes. Not a blocker.
- **OQ-P8B-03 — should the catch-up scan page its enqueues?** **Resolved: keep one burst** (Slice A behavior, unchanged). In Slice B each enqueued item is now a paid AI call, but the sequential consumer rate-limits spend naturally; paging is a later optimization. Logged as Tech Debt below. Not a blocker.

One judgment call left to the implementer (not blocking): the exact shape of `WriteSummary` (Phase 6) and whether the orchestrator breaks or continues the dimension loop on the first failure — either is correct as long as a partial failure withholds the stamp and records the error (the spec/design leave the loop-control style open).

---

## Out of Scope (deferred — note seams only, do not build)

Slice B tradeoffs (from the design's "Known tradeoffs" + spec "Out of scope"):

- **Cross-document batching of AI calls** — each document still sends its full text once per dimension. Deferred — **TD-066**.
- **Prompt caching for the OpenAI-compatible endpoint** — wired only if research confirms endpoint support (it did not in this pass). Deferred — **TD-067**.
- **Holistic cross-dimension extraction** (one prompt seeing all dimensions' facts) — focused per-dimension calls are intentional; a cross-dimension entity-name header is the upgrade path. Deferred — **TD-068**.
- **Entity resolution / name-variant merging** — "Anthony" and "Anthony Nguyen" stay separate; exact-entity dedup only (D6). Deferred — no phase assigned.
- **Paging / batching the Catch-up Scan's enqueue** — one burst, same as Slice A (OQ-P8B-03 / OQ-P8A-03).
- **Populating `trust_score` / `retrieval_count`** — inert in Phase 8; Phase 9 increments `retrieval_count`, Phase 10 populates `trust_score`. Do not populate here (Phase 9/10 contract).
- **A real tokenizer** — Content Reader keeps the `chars / 4` estimate (Slice A out-of-scope).
- **Any new MCP tool** — Slice B adds none (C-15); work runs on the background worker + existing REST handlers.
- **Re-pointing `providers.classify` at DeepSeek as a code change** — it is a config/deploy concern (Phase 0 pre-flight); the code always calls `get_provider("classify", config)`.

### Tech Debt to (re-)log during implementation

- **`context_loader` re-reads `dimensions.yaml` and re-queries facts per document** (`classify.py:320`, carried Slice A TD) — N×D redundant work; **now higher priority** because each enqueued item is a paid AI call. Clean fix: cache dimensions + facts once per consumer session.
- **`context_loader` hardcodes the path to `dimensions.yaml`** via `Path(__file__)…` (`classify.py:344-346`, Slice A TD) — fragile under package install; use `CONFIG_DIR` or a path parameter. Re-confirm.
- **Catch-up backlog** (OQ-P8B-03 / OQ-P8A-03) — a large unclassified vault at boot floods the queue with paid AI work; the sequential consumer rate-limits naturally, paging is the later optimization. Re-confirm the TD.
- **Source-prune scan cost** (OQ-P8B-01) — `prune_sources` reads every non-retired fact per delete; negligible at single-user scale, swap to a JSON1 query if fact counts grow.
