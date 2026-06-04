# Design — Workflow Orchestrator (`build-pipeline` skill)

_Date: 2026-06-04_
_Status: Design — for review. No skill edits made yet._
_Scope: the design→spec→research→plan **workflow tooling** (skills in `~/.claude/skills/`), NOT ai_kms product code._

---

## Problem

Each workflow step (`/codebase-design-analysis` → `/writing-detailed-specs` → `/research` → `/plan-from-specs`) reads enough of the codebase to fill the context window. The user opens a new session per step. A fresh session has no codebase view, and the user does not trust Claude Code's compaction. Two symptoms:

1. **Codebase re-read 3×** (CDA survey, WDS inventory, research deep-trace) — necessary work, but it fills the window.
2. **Artifact duplication** — the plan re-states ~35% of the spec (Build steps, Done-when≈Test-criteria, Out-of-scope, Files-to-modify), verified against the real `phase_pre_2/td_008_and_td_038` doc pair.

## Decision

Build an **orchestrator skill** (`build-pipeline`) that runs in the main thread, holds only the small markdown artifacts, and **quarantines every read-heavy code survey into an `Agent` subagent** with its own context window. HITL gates stay in the main thread (fork 3 — hybrid). The main thread never accumulates raw code, so the whole chain can run in one lean session and the cross-session handoff problem disappears.

Chosen via: direction **B** (orchestrator + subagents), HITL fork **3** (hybrid per-step).

---

## Load-bearing constraint (verified, not assumed)

**The `Skill` tool does NOT isolate context — only the `Agent` tool does.** Invoking a skill loads its content into the current window. So an orchestrator that *calls the 4 skills in sequence* buys zero context savings. Real isolation requires dispatching the read-heavy work as `Agent` subagents that read code in their own window, write the artifact to disk, and return a ≤1-page summary.

**Corollary:** `Agent` subagents are non-interactive — they cannot call `AskUserQuestion`. Therefore interactive HITL gates cannot live inside a subagent. This is the entire reason for fork 3.

---

## Architecture

```
build-pipeline  (main thread — stays lean: artifacts + summaries only)
│
├─ FRONT DOOR
│   └─ scope-tier gate:  tiny / medium / large  → which steps run
│        tiny   (1–2 files, reversible, no new interface) → skip to plan or implement
│        medium (one phase, no cross-module contract)     → spec + plan
│        large  (risky / irreversible / multi-phase)      → full chain
│
├─ per step:  [interactive front: main thread] → [survey: SUBAGENT] → [review gate: main thread]
│
└─ artifact store on disk:  design.md → spec.md → research.md → plan.md
        (each subagent reads upstream artifacts by PATH, writes its own, returns summary)
```

### Per-step split (fork 3)

| Step | Main thread (HITL — cannot be subagent'd) | Subagent (read-heavy — isolated) |
|---|---|---|
| **CDA** | Phase-1 interview (sequential Socratic), options-pick, doc-write gate | code survey + implications + 3-option draft → returns options summary |
| **WDS** | phase-boundary confirm (light) | reuse inventory + spec draft → returns spec summary |
| **research** | calibration-plan gate (one `AskUserQuestion`) | deep-trace + assumption verification → returns Spec-Verification table + invalidated list |
| **plan** | annotation handling (revision runs only) | draft phases + exact-line grep → returns phase outline |

Rule: a gate stays in the main thread only if it is **inherently sequential or needs user judgment**. Everything else (reading, tracing, drafting) goes to a subagent.

---

## Two invocation modes (skills are NOT merged)

The orchestrator is a thin layer on top of the 4 skills, not a container. Skills stay 4 separate files and remain the single source of truth — the orchestrator dispatches a subagent that *reads the skill file by path and follows it* (proven by R1). No skill logic is copied into the orchestrator.

| Invoke | Behavior |
|---|---|
| `/codebase-design-analysis` etc. (direct) | Runs as today — main thread, user manages sessions/context. Unchanged, backward compatible. |
| `/build-pipeline` (orchestrator) | Scope-tier gate → dispatches each skill to an isolated subagent → lean main thread, one session. |

The Phase-2 `[MAIN-THREAD]`/`[SUBAGENT-SURVEY]` markers are metadata: a direct call ignores them (user runs the whole skill); the orchestrator reads them to split front/back. Additive — does not break standalone use.

**Locked decisions:** Q1 name = `build-pipeline`. Q2 tiny-tier → orchestrator stops and tells the user (no auto-dispatch to implement). Q3 STATE.md written once at chain end, not per step.

## Components to build

1. **`build-pipeline` orchestrator skill** (new). Front-door scope-tier gate; per-step loop; dispatch logic; artifact-path bookkeeping; surfaces subagent summaries + questions to the user.
2. **Subagent dispatch prompts** (one per step). Each hands the subagent: CLAUDE.md path, upstream artifact paths, the step's skill path to follow, and "write artifact to disk, return ≤1-page summary, do NOT ask questions — defer them into the artifact."
3. **Edits to the 4 existing skills** (moderate): split each into an interactive-front section the orchestrator runs, and a survey section the subagent runs. Skills remain individually runnable (backward compatible) — the split is additive sectioning, not a rewrite of logic.

---

## What folds in automatically

- **Direction A (strengthen artifacts) becomes a dependency, not an alternative.** Subagents start cold; the dispatch prompt must hand them complete artifacts. So each artifact gains a **"Codebase Orientation"** header (entry points, the handful of files this step touches, key symbols) that lets a cold subagent navigate without a full re-sweep. A is now a required sub-component of B.
- **De-dup (plan references, not restates).** The plan subagent receives the spec by path and **references spec component IDs** for Build/Files/Done-when instead of re-reading and restating. Plan keeps only its unique content: architecture diagram, TDD RED→GREEN sequencing, exact line numbers, commit boundaries, status. Out-of-scope is linked, not copied. Expected plan shrink ~35%.

---

## Known tradeoffs

- **Subagent spawns are the expensive path on this plan** (per harness guidance). We pay cold-start re-derivation per step in exchange for a main thread that survives the whole chain. Net win only because the survey work is large relative to the cold-start cost.
- **Two-pass on question-heavy steps.** A subagent that hits a decision defers it into a "Decisions needed" block; the orchestrator surfaces it; a second dispatch finalizes. CDA's interview avoids this by staying fully main-thread, but research/plan may incur a second dispatch when the survey surfaces a question.
- **Moderate rewrite of 4 skills.** Less than fork 2 (full split) but more than fork 1 (defer-and-resume wrapper). Skills stay runnable standalone.

## Risks (for research / planning to verify)

- **R1 — Can a subagent reliably "follow a skill at a path"? ✅ RESOLVED 2026-06-04 — PASS.** Probe: dispatched a general-purpose subagent with `codebase-design-analysis/SKILL.md` by path + an inline 6-line fake codebase + a design question. The on-disk artifact (`/tmp/r1_probe_artifact.md`) reproduced the skill's exact fingerprint — `[UNVERIFIED]` markers, Design Lens (deletion test / seam discipline / module depth per option), Step 3.5 two-tier criteria, the full Step-4 option template, Step-5 cross-check, Step-6 doc body — not generic training-data output. It skipped Step 0/Step 2 *and flagged the skip*, deferred 6 questions into the doc instead of asking, wrote to the exact path, and returned a 12-line summary. Conclusion: dispatch-prompt = "read and follow `<skill path>`" works; no need to inline survey instructions.
- **R2 — Summary fidelity. ✅ RESOLVED 2026-06-04 — PASS (same probe).** The 12-line return summary was accurate and compact. Confirmed design rule: **the artifact on disk is the handoff; the orchestrator reads only the summary** (cost shape: subagent spent 47.6k tokens in its own window, main thread ingested ~12 lines). Next subagent reads the upstream *artifact*, never the summary.
- **R5 — Subagent model is environment config, NOT a per-dispatch param. ✅ CONFIRMED 2026-06-04.** Five spawns failed at launch resolving to an unavailable model; the `Agent` tool's `model` override (`sonnet`/`opus`) did **not** change it. The orchestrator cannot pin a model at dispatch time — the operating environment must have the subagent model set to a capable Claude model in settings. Document this as a setup prerequisite for `build-pipeline`.
- **R3 — Orchestrator context still grows** across 4 steps (4 summaries + 4 artifact references). Lean, but not zero. Verify it survives a large feature.
- **R4 — Bias guard preserved?** research subagent must read code independently, NOT inherit CDA's narrative. Its dispatch prompt passes the spec's *assumptions table* (falsifiable claims) + "verify against code", never CDA's prose framing. This keeps the verify-gate honest (the reason fork-C / temp-handoff was rejected).

## Open questions

- **Q1 — Skill name.** `build-pipeline`? `feature-pipeline`? `orchestrate`? (`architecture` collides with `architecture-docs`.)
- **Q2 — Where does the scope-tier gate's "tiny → just implement" path hand off?** Directly to `/tdd-implement`, or stop and tell the user?
- **Q3 — Does the orchestrator itself need a STATE.md write per step, or only at chain end?**

## Options explored (rejected)

- **Fork 1 — defer & resume (skills whole, batch questions).** Rejected as primary: CDA's sequential interview can't collapse into one deferred batch. Kept as the mechanism for incidental mid-survey questions only.
- **Fork 2 — full front/back split of every skill.** Rejected for now: cleanest but largest rewrite; over-investment before 30 June product deadline. Revisit post-M3 if fork 3 friction is high.
- **Direction C — temp handoff file per step.** Rejected: invisible side-channel biases the verifier (research) with the verifyee's (spec's) framing; duplicates what the artifact already carries.

---

## Next step

Run `/research` against this design to verify R1 (subagent-follows-skill-by-path) and R4 (bias-guard wiring) before planning the skill edits. R1 is the gate — if subagents can't follow a skill by path, the dispatch design changes shape.
