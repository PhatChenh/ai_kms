# Brief: Single Source of Truth + Non-Coder Docs

_Handoff brief. Self-contained. Give this to an AI agent to execute. Author: design grill, 2026-06-03._

---

## Part 0 — The problem and the model

**Problem.** After Phase 1 + 1.5 + several "Brief" mini-projects, many design decisions were made then overridden. No single doc answers "what does the build do right now." Docs have drifted from code. Two non-coders (business/marketing, zero code) are joining as **testers + design contributors** and need to understand and verify the system without reading code.

**Root insight: there is no single source of truth — there is a *layered* set, each layer with ONE owner doc and ONE anti-drift trigger.** Drift happens when a layer has no owner or no trigger. Fix the layers, not the symptoms.

| Truth layer | Question it answers | Owner artifact | Anti-drift trigger |
|---|---|---|---|
| **Behavior (machine)** | Does the code do what AI thinks is correct? | `tests/` (pytest, 797) | CI |
| **Behavior (human oracle)** | Does the system do what *we intended*? | **Scenario inventory** → Non-coder Behavior Guide | Skill A, every behavior change |
| **Decision** | Why is it built this way? | ADRs in `docs/architecture/system_adr/` with `Status:` + `Superseded by` | Append-only; new ADR supersedes old |
| **Current state** | Where are we now? | `STATE.md` (regenerated, not appended) | `update-project-docs` at session/phase end |
| **Structure (human)** | How does it fit together, for a non-coder? | **System-story** file → Arch Story note | Skill B, phase boundary |

**Critical nuance on the human-oracle layer.** The 797 pytest tests are AI-written and human-unverified (only periodic Opus review). They encode the AI's idea of correct, *including its blind spots*. So the Non-coder Behavior Guide is an **independent human oracle** — it can and should sometimes disagree with the tests. Its expected outcomes come from the **spec/roadmap (human intent)**, never derived from the tests. Where no spec clause covers a behavior, that is a **spec hole** to surface, not a thing to invent.

---

## Part 1 — Workstream A: Reconcile the repo (clear the drift)

A read-only drift audit was already run. Findings below are pre-verified against `src/` + `tests/` — act on them.

### A1. Fix two REAL code bugs (not drift — actual defects). Do these first; they corrupt test results.

- **BUG-1 — `--scan` re-captures and renames `.summaries/` siblings.** `scan_capture` *modified* loop guards `.summaries/` (capture.py ~1011) but the *added* loop (capture.py ~964) has **no skip**. A first-seen sibling flows into `capture_file` → `_store_md` → rename gate (capture.py ~467) and can be renamed, wiping sibling identity. Fix: skip `.summaries/` paths in the added loop too. Add a regression test.
- **BUG-2 — non-md sibling `.md` missing `project` field + wrong/absent domain tag.** In `_store_nonmd`, `sibling_meta` (capture.py ~640) sets tags from `domain/` only and never sets `project=`. For a project-located binary, `apply_location_tags` sets `ai_project` but adds no `domain/` tag (capture.py ~312), so the sibling ends with neither. Breaks Phase 2 routing of located binaries. Fix: propagate `project` (and correct location tag) onto `sibling_meta`. Add a test.

### A2. Reconcile docs against code. Concrete drift to fix (file:line from audit):

1. **CLAUDE.md:15** — milestone line says "Brief #2 / Brief #3 Phase 1" — ~5 phases stale. Set to "Phase Pre-2 complete; next: Phase 2 Classify."
2. **Pipeline stage count 5 → 6** everywhere: `docs/architecture/overall_design.md:73`, `docs/architecture/phase1_capture/_OVERVIEW.md` (header + notes + all flow diagrams). `apply_location_tags` (capture.py:912) is the missing 6th stage and is invisible in arch docs.
3. **Reconcile stage count 4 → 6**: `phase1_capture/_OVERVIEW.md:70`, `overall_design.md`, `CLAUDE.md:323`. Stages 5 (stale-tags) + 6 (stale-batch-refs) added in Phase 1.5.
4. **Scalar `domain:` shown as live** — `CONTEXT.md:41`, `phase1_capture/_OVERVIEW.md:164,202`. It is dropped in code (`frontmatter.py:48` `_DEPRECATED_KEYS={"domain"}`). Domain is only a `domain/<D>` tag now. Remove all "live scalar domain" language.
5. **CLAUDE.md Build progress omits Phase Pre-2** and freezes test count at 787 (actual 797). Add Phase Pre-2 (TD-008 columns, TD-038 domain cleanup); update count.
6. **STATE.md has contradictory blocks** — Phase Pre-2 listed both complete `[x]` (line ~59) and PENDING `[ ]` (line ~124); a stale "Phase 1.5 PENDING" block (~38) contradicts "Phase 1.5 COMPLETE" (~95). Prune the stale in-flight blocks; keep one current state.
7. **TD-028 mismatch** — `STATE.md:44` shows open `[ ]`, `TECH_DEBT.md:384` shows RESOLVED. Make consistent (resolved).
8. **ADR 0006** (`0006-split-binaries-editable-vs-no-edit-by-file-type.md`) is "Proposed" and **unbuilt** (`grep` finds no `no_edit_extensions` in `src/`), yet its vocabulary (editable / no-edit file types) leaked into `CONTEXT.md:63-77` as if current. Either: (a) add a bold "NOT IMPLEMENTED as of Phase Pre-2" banner to the ADR and mark the CONTEXT.md terms as "planned (ADR-0006, unbuilt)," or (b) remove the terms from CONTEXT.md until built. Recommend (a).

### A3. Establish ADR superseded discipline.

- ADRs 0001-0005 match code — leave as accepted.
- Going forward: **never edit/delete a decided ADR.** When a decision changes, write a new ADR, set the old one's header to `Status: Superseded by ADR-NNNN`, link both. This is the decision-layer anti-drift mechanism.
- Audit the CLAUDE.md "What Claude gets wrong" section (~20 bullets): many are really superseded decisions in disguise. Each bullet should become exactly one of: an ADR, a CONSTRAINTS entry, or a test. Delete from CLAUDE.md once relocated. (Do this as a slow drain, not a big-bang rewrite.)

**A2/A3 success check:** no doc references a removed symbol as live; stage counts match `capture.py`; STATE.md has no contradictory blocks; CLAUDE.md milestone + build-progress match STATE.md.

---

## Part 2 — Workstream B: Behavior Guide system (Skill A + inventory + setup script)

**Goal:** a non-coder can verify "what the system does today," the artifact can't silently rot, and it catches both AI-test blind spots and spec holes.

### B1. The scenario inventory — ONE living file (source of truth).

- Location suggestion: `docs/testing/behavior_inventory.yaml` (or `.md` with structured entries).
- **Edited in place, never recreated.** It holds irreplaceable human state.
- Entry schema:

```yaml
- id: P1-2
  behavior: "PDF dropped in inbox -> summary .md created, binary moved out"
  tier: smoke           # smoke | phase | full
  phase: 1
  steps: "kms capture inbox/sample.pdf"
  fixtures: [inbox/sample.pdf]      # what the setup script must create
  expected: "OK printed; a .md appears with non-empty summary; pdf leaves inbox"
  spec_ref: "roadmap §Phase1 'non-md -> sibling summary'"   # or: "NONE -- needs human"
  human_reviewed: yes   # yes | no
  pytest_ref: "tests/test_pipelines/test_capture.py::test_pdf_sibling"   # or null
  status: active        # active | retired
  retired: null         # "<date>: <reason>" when retired
```

### B2. Skill A — `/update-behavior-guide`. Does 5 jobs:

1. **Draft** — pointed at a phase/spec, append candidate scenario entries (AI breadth).
2. **Link** — fill `spec_ref` from the spec/roadmap; where none exists write `spec_ref: NONE -- needs human` (the human gap-hunt queue).
3. **Generate the guide** — render inventory → tiered non-coder doc (`docs/testing/TESTING_GUIDE.md`, regenerated wholesale). Group by tier: **smoke** (~5 min sanity), **per-phase** (run when that phase is touched), **full** (pre-demo sweep). Each entry: plain-English steps, what to look for, "what this proves." Visually flag any `human_reviewed: no` as **"⚠ not yet trusted — AI-authored only."**
4. **Generate the setup/reset script** — read every `fixtures:` line → emit a script that creates exactly those files in a real test vault and resets between runs (per-test isolation). Guide + script derive from the same inventory → cannot desync.
5. **Reconcile + cross-check** — on a behavior-changing run, diff each existing scenario against current code + its `pytest_ref` and mark: **KEEP** / **CHANGED** (rewrite `expected`/`spec_ref`, reset `human_reviewed: no`) / **RETIRED** (`status: retired` + tombstone reason, never delete). Report scenarios with no live `pytest_ref` or no code path.

**Human's only manual job:** resolve `spec_ref: NONE` flags — decide if it's a real spec hole (fix the spec) or accept the behavior.

### B3. Setup/reset script.

- **Replaces the current ad-hoc reset block** in TESTING_GUIDE (the `rm -f data/kb.db` + manual `cp` lines).
- Must implement **per-test isolation** — this is the fix for the existing structural flaw where the `kms scan` whole-vault test consumed the inbox and starved downstream single-file capture tests. Either: (a) each test resets to a known fixture set before running, or (b) tests are grouped so any whole-vault/`scan` test runs in its own isolated pass.
- **LANDMINE:** a Python script using `.write_text()` / `open(..., 'w')` will trip the repo's vault-write hook (blocks those calls in any `.py` except `vault/writer.py`). Avoid by writing the setup script in **bash/shell**, or place it outside `src/` and have it shell out — do NOT author it as a `.py` that writes files. Confirm against `.claude/settings.json` hooks before choosing.
- Operates on the **separate test vault** (`/Users/phatchenh/ai_kms_test_vault`), never the real vault.

### B4. Retire the old TESTING_GUIDE content into the inventory.

The current `docs/testing/TESTING_GUIDE.md` already has good raw scenarios (P1-1..P1-7, P1.5-1..6, Pre2-1..2) and a "Need more check" list at the bottom. **Seed the inventory from these** (don't discard), then regenerate the guide from the inventory. The 5 "Need more check" items map to: BUG-1, BUG-2 (above), plus TD-037 (binary modify/move no re-capture — known, tracked), and two already-RESOLVED concerns (scan skipping managed attachment; folder-capture batch_id wiring) — encode them as scenarios so they stay covered.

---

## Part 3 — Workstream C: Architecture Story (Skill B + system-story)

**Goal:** a marketing non-coder gets oriented and can locate where behavior lives, with zero internal tech. Replaces nothing technical — it's the non-technical sibling of the existing C4 `architecture-docs`.

### C1. The system-story source — ONE tiny curated file (~1 screen).

- Location suggestion: `docs/architecture/system_story.md` (the source) — deliberately small. Contents: the phase list (built vs coming), the file's journey in plain verbs, where a user's stuff is stored (vault vs DB). Authored small on purpose — simplicity must be *authored*, it can't be derived from the 100-entry inventory.

### C2. Skill B — `/update-arch-story`. Same engine as Skill A, different template + source + trigger.

- Reads `system_story.md` → emits a **non-coder narrative note** + Mermaid diagrams into `docs/architecture/` (e.g. `architecture_for_non_coders.md`), opened via Obsidian (renders Mermaid).
- Delegates diagram drawing to the existing `draw-diagram` skill.
- **Trigger: phase boundaries only** (not every behavior change).

### C3. Diagram rules (the old doc failed by violating these).

- **One diagram answers one question.** Max ~6 boxes. Hard cap.
- Boxes = plain verbs in the user's words ("Read it → Summarize it → File it → Find it later"), never tech nouns (no FTS5 / embeddings / MCP).
- **Split into 3 tiny diagrams, never merged:** (1) your file's journey, (2) what's built vs coming, (3) where your stuff is stored.
- Mermaid, viewed in Obsidian.
- Keep the existing technical `overall_design.md` / `system_diagram.md` for you + Opus — do not delete them; they serve the other audience.

---

## Part 4 — Execution order

1. **A1** — fix BUG-1, BUG-2 (+ tests). They corrupt any behavior testing done on top.
2. **A2 + A3** — reconcile docs, set ADR superseded discipline. Cheap, removes the active confusion. Do the ADR/constraint drain slowly.
3. **B1 + B4** — seed the scenario inventory from the existing guide + the audit findings.
4. **B2 + B3** — build Skill A and the setup/reset script; regenerate the guide.
5. **C1 + C2 + C3** — build the system-story + Skill B; regenerate the non-coder arch story.

Rationale: clean the ground (1-2) before building the source of truth on top of it (3-5). Behavior guide (3-4) before arch story (5) because the non-coders' first job is testing.

---

## Part 5 — Landmines (read before coding)

- **Vault-write hook** blocks `.write_text()` / `open(w)` in any `.py` except `vault/writer.py`. Affects the setup/reset script — author it in bash or keep it out of `.py` write paths.
- **Adding a type tag to `config/tags.yaml` breaks two count tests** in `tests/test_core/test_tags.py` (assert `len(allowed_types) == 9`). Update the count integer if types change. Do NOT touch `SAMPLE_TAXONOMY` (separate fixture).
- **`CONFIG` validates vault root at import** — never import `CONFIG` at module scope in tests; pass explicit paths.
- **Two near-twin predicates in `vault/paths.py`:** `_is_in_managed_attachment` vs `_is_managed_summaries_area`. Picking the wrong one is silent. Confirm which scope a new scenario/check needs.
- **Two skills, not one.** Don't merge Skill A and Skill B — different source, trigger, cadence. They share only the `draw-diagram` delegation.
- **Inventory + system_story are living sources** — never regenerate them from scratch; they hold human state (`human_reviewed`, `spec_ref`, the curated narrative).
- **Render = script (0 tokens), LLM = delta-only.** The mechanical steps — inventory→`TESTING_GUIDE.md`, inventory→setup script (Skill A jobs 3+4), system_story→arch note (Skill B) — MUST be deterministic templating, NOT an LLM prompt. Two reasons: (1) cost — wholesale regeneration runs often, keep it free; (2) determinism — "derive from the same source → cannot desync" only holds if it's code, not a prompt (a prompt drifts each run). The LLM jobs (Draft, Link, Reconcile) run on the **delta only** (the new/changed phase + scenarios touching changed files via git diff), never the whole inventory. Trap: building render as "here's the inventory, Claude, write the guide" — that makes wholesale expensive AND non-deterministic. The expensive job is **Reconcile** (job 5): scope it to behavior-changing runs and changed-file scenarios, not all entries every time.

---

## Part 6 — Acceptance criteria

- [ ] BUG-1, BUG-2 fixed with regression tests; full suite green.
- [ ] No doc references a removed symbol as live; stage counts (capture=6, reconcile=6) match code; STATE.md has no contradictory blocks; CLAUDE.md milestone + build-progress match STATE.md.
- [ ] ADR 0006 clearly marked unbuilt; its vocabulary in CONTEXT.md marked planned or removed.
- [ ] `behavior_inventory.yaml` exists, seeded from old guide + audit; every entry has `spec_ref` (clause or `NONE`), `human_reviewed`, `pytest_ref`, `status`.
- [ ] Skill A regenerates TESTING_GUIDE + setup/reset script from the inventory; guide is tiered; untrusted scenarios flagged.
- [ ] Setup/reset script gives per-test isolation; the `scan`-vs-capture interference is gone; script does not trip the vault-write hook.
- [ ] `system_story.md` exists (~1 screen); Skill B regenerates a non-coder arch note with ≤6-box, tech-noun-free, split Mermaid diagrams viewable in Obsidian.
- [ ] Each truth layer in Part 0 has exactly one owner artifact and one named trigger.
