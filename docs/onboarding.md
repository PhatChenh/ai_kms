# Onboarding — AI-kms Contributors

_For the two contributors (BD + Marketing) joining to build features by steering Claude Code._
_Last updated: 2026-06-04. Reflects: Phase 0 + Phase 1 + 1.5 + Pre-2 + Vault-Restructure complete, 956 tests passing._

---

## 0. Read this first

You are **not** here to write code. You are here to do **software engineering by steering AI**.
Claude Code reads the code, writes the code, and writes the tests. **You drive the thinking.**

Your job, in one sentence: **turn a fuzzy feature idea into a precise spec, let Claude build it, then run the app to confirm it works.**

The hardest part for you will not be technical. It will be **thinking precisely** — pinning down exactly what a feature does, what its edge cases are, and where its boundaries lie. That is why the workflow below front-loads `/grill`. Take it seriously.

You will be slower than the main driver. **That is expected and fine.** Going through every step is the learning exercise. Do not skip steps to go faster.

---

## 1. What AI-kms is (the 90-second version)

AI-kms is a knowledge management system for a busy, **non-technical executive**. The user drops files (notes, PDFs, web links, emails) into one folder. The AI summarizes them, files them in the right place, makes them searchable, and surfaces patterns back as daily briefings.

**The core promise:** zero organizational effort from the human. The AI does the work; the human does the judgment.

Two storage halves:
- **The Vault** — an Obsidian folder of markdown files on disk. This is the **source of truth**. The user sees and edits it through Obsidian.
- **The Database** — a local SQLite file that **indexes** the vault (for search and audit). It is _never_ the source of truth — it is a fast lookup cache that mirrors the vault.

Full architecture picture: [docs/architecture/overall_design.md](architecture/overall_design.md).

---

## 2. The mental model — 7 concepts you MUST understand

You won't read code, but you must understand these **conceptually**, because every spec you write and every behavior you test depends on them. When Claude proposes something that violates one of these, you need to catch it.

### 2.1 The Pipeline Pattern
Every feature is a **pipeline**: a sequence of small steps, each doing exactly one thing, passing its output to the next.

```
extract → summarize → classify → store
```

Each step is a **pure function**: same input always gives same output, no hidden side effects, no surprises. Steps are **never bundled** ("summarize AND classify in one step" is forbidden) and **never skipped**. This is why the system is debuggable and testable.

When you spec a feature, you are really describing **the stages of its pipeline**. The reference implementation to imitate is [src/pipelines/capture.py](../src/pipelines/capture.py).

### 2.2 Result Types — no silent failures
Every function returns one of two things:
- `Success(value)` — it worked, here's the answer.
- `Failure(error, recoverable, context)` — it failed, here's why, and whether retrying might help.

Functions **never** just return a bare value or `None`. This means errors can never be silently ignored — the caller is forced to handle both cases. When Claude writes a function that "just returns the data," that's a bug; the answer must be wrapped in a Result.

### 2.3 Confidence Gates — thresholds live in config, never in code
The AI attaches a **confidence score** to its decisions. The system routes based on that score:
- **High confidence** → do it automatically (auto-file).
- **Medium confidence** → suggest it, flag for the human to confirm.
- **Low confidence** → leave it alone, put it in the human's review queue.

The cutoffs (e.g. "high = above 0.85") live in [config/thresholds.yaml](../config/thresholds.yaml), **never** as a number typed into the code. This lets the team tune behavior without touching code. If Claude ever writes `if confidence > 0.85:` directly in a pipeline, a hook will **block it**. Always read thresholds from config.

### 2.4 Audit Trail — every AI decision is logged
Every time the AI makes a decision, it writes an entry to the **audit log**: timestamp, which note(s), what it decided, the confidence, the reasoning, and the outcome.

This is **non-negotiable**. The Daily Briefing (Phase 8) and Weekly Synthesis (Phase 9) are built _entirely_ on top of the audit log — they read it to tell the user what happened. **No audit entry = the briefing has a silent hole.** If you are building Phase 8 or 9, the audit log is literally your input data.

### 2.5 The `updated_by_human` gate — human edits are sacred
If a human has edited a note, the note carries a flag: `updated_by_human = true`. When that flag is set, **the AI must never overwrite that note.** It surfaces a conflict instead.

This is the single most trust-critical rule in the system. Overwriting a human's edit silently destroys their work with no undo. Every feature that writes to the vault must respect this gate. (Bonus subtlety for Phase 4+: an edit made _through_ our AI pipeline is "AI-owned"; an edit made _outside_ our pipeline — by a person in Obsidian, or by another tool writing the file directly — counts as a human edit.)

### 2.6 Prompts are YAML files, not code
Every instruction sent to the AI lives in a `.yaml` file under [src/prompts/](../src/prompts/), loaded by name. Prompts are **never** written as text inside the Python code. This keeps prompt-tuning a config edit, not a code change. When your feature needs a new AI instruction, it gets a new `prompts/<name>.yaml` file.

### 2.7 MCP tools are thin wrappers — pipeline first, tool second
Later, the boss talks to the vault through Claude Desktop via an "MCP tool." **An MCP tool contains zero logic** — it just calls a pipeline and hands back the result. The iron rule: **never add an MCP tool before its pipeline exists and is tested.** A tool that calls nothing is a lie that makes a demo look like it works when it doesn't.

---

## 3. Glossary — vault & domain vocabulary

You'll see these words constantly. Full version in [CONTEXT.md](../CONTEXT.md).

| Term | Plain meaning |
|---|---|
| **Vault** | The Obsidian folder of markdown files. The source of truth. |
| **Frontmatter** | The little block of metadata at the top of a markdown file (between `---` lines): tags, summary, created date, etc. |
| **inbox/** | The single drop zone. Everything new lands here first. |
| **Sibling `.md`** | When a binary (PDF, image) is captured, the AI can't write metadata _into_ a PDF — so it writes a companion markdown file next to it (e.g. `report.pdf.md`) holding the summary. That companion is the "sibling." |
| **type tag** | Exactly one per note, e.g. `type/report`. Validated against an allowed list. |
| **domain tag** | `domain/<D>` in the tags list, e.g. `domain/Strategy`. A note can have several. |
| **project field** | A `project: "<A>"` field. One per note. Set from the file's location, not guessed by AI. |
| **free tag** | A plain keyword tag, e.g. `q1-review`. 5–10 per note. |
| **LOCATED** | A binary whose folder reveals its project/domain — filed correctly with a rich summary. |
| **CLUELESS** | A binary with no derivable context — parked in inbox with a "pending" marker for Phase 2 to resolve later. |
| **no-edit file** | A binary the user shouldn't edit (pdf, png, jpg…) → hidden in an `attachment/` folder. |
| **editable file** | A non-md file the user _might_ edit (docx, xlsx…) → kept visible in the project folder. |
| **AI-output folder** | `Briefings/`, `Synthesis/`, `Documentation/` — the AI writes here; these are skipped by capture so the AI never re-ingests its own output. **Phases 6, 8, 9 write here.** |
| **reconcile** | A repair command (`kms reconcile`) that fixes drift between the vault and the index. |

---

## 4. What already exists — Phase 0 and Phase 1

You build **on top of** this. It is done, stable, and tested across 956 tests. You will not modify it.

### Phase 0 — Foundations (the toolbox)
The shared primitives everything imports:
- **Config** — validated settings loaded once at startup ([src/core/config.py](../src/core/config.py)).
- **Result type** — `Success`/`Failure` ([src/core/result.py](../src/core/result.py)).
- **Pipeline runner** — runs a list of stages ([src/core/pipeline.py](../src/core/pipeline.py)).
- **Audit log** — records every AI decision ([src/core/audit.py](../src/core/audit.py), [src/storage/audit_log.py](../src/storage/audit_log.py)).
- **Confidence gate** — routes by confidence score ([src/core/confidence.py](../src/core/confidence.py)).
- **Tags** — taxonomy + validation ([src/core/tags.py](../src/core/tags.py)).
- **LLM provider** — one factory for all AI calls ([src/llm/provider.py](../src/llm/provider.py)); prompts loaded from YAML ([src/llm/prompt_loader.py](../src/llm/prompt_loader.py)).
- **Vault layer** — the _only_ code allowed to read/write the vault: [reader](../src/vault/reader.py), [writer](../src/vault/writer.py), [paths](../src/vault/paths.py), [frontmatter](../src/vault/frontmatter.py).
- **Storage** — SQLite index + audit + migrations ([src/storage/](../src/storage/)).

### Phase 1 — Capture (the reference feature)
Drop a file into `inbox/` → the AI writes a summary + metadata and files it. This is the **template every other pipeline copies.**

A 6-stage pipeline ([src/pipelines/capture.py](../src/pipelines/capture.py)):
`extract text → enrich URLs → AI summarize → AI label/tag → apply location tags → write to vault`.

Plus: handlers for many file types (md, PDF, DOCX, XLSX, PPTX, CSV, HTML, EML, images), a folder **watcher** that auto-captures drops, and `kms reconcile` (7 repair stages).

**When in doubt about how to build your feature, read `pipelines/capture.py`.** It demonstrates the pipeline pattern, Result types, audit logging, and tag validation all together.

---

## 5. Stable Interfaces — what you build against

These modules have been stable across 956 tests. Your features **import from these and nothing deeper.** You depend on the foundation, not on Phase 2/3/4 work that's still in flight.

| Module | Key functions | What it does |
|--------|--------------|--------------|
| `vault/reader.py` | `read_note(path)` | Read a note + its frontmatter |
| `vault/writer.py` | `write_note(...)`, `move_note(src, dst)` | Write/move notes (respects `updated_by_human`) |
| `vault/paths.py` | `resolve_placement()`, `project_attachment()`, `domain_attachment()` | Figure out where files go |
| `storage/documents.py` | `upsert()`, `get_by_path()`, `all_paths()`, `delete_by_path()`, `rename()` | Document index CRUD |
| `storage/audit_log.py` | `append(...)`, `query()` | Read/write the audit log |
| `core/audit.py` | `write(decision, source_ids, pipeline, stage, ...)` | High-level audit writer (use this one) |
| `core/tags.py` | `validate_tags(tags, taxonomy)` | Enforce the tag taxonomy |
| `core/pipeline.py` | `run_pipeline(stages, input)` | Run your pipeline |
| `core/result.py` | `Success(value)`, `Failure(error, recoverable, context)` | The Result type |
| `llm/provider.py` | `get_provider(task, config)` | The one way to make an AI call |
| `llm/prompt_loader.py` | `PROMPTS["name"].render(**vars)` | Load a prompt from YAML |
| `core/config.py` | `CONFIG`, `VaultConfig`, `MainConfig` | Validated config |

---

## 6. What you'll build

You each take **one medium + one light** feature. All four below depend on Phase 0+1 only (the stable interfaces above), so they don't wait on anyone's in-flight work, and you won't collide with each other if you stay in your own worktree.

| Phase | Feature | Weight | One-line job | Writes to |
|---|---|---|---|---|
| **5** | Note Promotion | medium | Extract structured knowledge (research notes, templates, lessons) from raw captures | `Domain/<D>/` |
| **6** | Documentation Auto-Update | medium | Keep one living page per active project, AI-proposed, human-approved | `Documentation/` |
| **8** | Daily Briefing | medium | Read the audit log → morning digest of what happened today | `Briefings/` |
| **9** | Weekly Synthesis | light | Connect dots across the week → recurring themes, contradictions, action items | `Synthesis/` |

Recommended split: **one person takes 5 + 9, the other takes 6 + 8** (each gets one medium + one light). But assignment is flexible — pick by availability.

Each phase's full spec — what it does, tech debt to resolve, open questions, and **acceptance criteria written as behavior tests** — is in [docs/roadmap/roadmap.md](roadmap/roadmap.md). Read your phase's section there before you start. The acceptance criteria are how your work gets verified.

---

## 7. Your first 30 minutes (setup)

Do this once, before any feature work. Ask the main driver if any step blocks you.

```bash
# 1. Get the repo + your own isolated worktree.
#    A worktree is your private copy of the repo so you never collide with
#    the other contributor. The main driver will give you the exact branch name.
git worktree add ../ai_kms-<yourname> -b feature/<yourname>-phaseN
cd ../ai_kms-<yourname>

# 2. Install dependencies.
uv sync

# 3. Set your AI key (ask the main driver for one).
export ANTHROPIC_API_KEY=sk-ant-...

# 4. Create your test vault — a fake Obsidian folder with the right structure.
#    There's a script that builds one for you:
bash docs/system_behavior/setup_test_vault.sh

# 5. Point the config at YOUR test vault.
#    Edit config/config.yaml → set vault.root to the path the script printed.

# 6. Confirm everything works — run the existing test suite.
uv run pytest -m "not smoke"      # skip tests that need a real vault on disk
# Expect: a large number of tests, all passing.

# 7. See the system actually work. Drop a note in your test vault's inbox/,
#    then capture it:
kms capture <path-to-that-note.md>
# Then open the vault in Obsidian (or just look at the files) and see the
# AI-written summary + tags appear in the note's frontmatter.
```

If step 6 passes and step 7 produces a summarized note, you're ready. The full setup + reset details live in [docs/system_behavior/TESTING_GUIDE.md](system_behavior/TESTING_GUIDE.md).

---

## 8. How to work a feature — the 6-step pipeline

This **is** the job. Each step is a slash command you run inside Claude Code. Never skip a step. Each step produces an artifact that feeds the next.

| Step | Command | What you do | What comes out |
|---|---|---|---|
| 1 | `/grill` | Pin down the design. Claude interrogates your fuzzy idea until it's precise. **The most important step for you** — practice exact thinking. | A sharp description of what the feature does, its edge cases, its scope boundaries |
| 2 | `/codebase-design-analysis` | Explore _how_ to build it against the real codebase. Claude shows you 2–3 implementation options with tradeoffs; you pick one. | A chosen approach grounded in existing code → `docs/1. design/` |
| 3 | `/writing-detailed-specs` | Turn the chosen option into a buildable spec. | A spec → `docs/2. specs/` |
| 4 | `/research` | **The quality gate.** Claude checks your spec against the 17 hard constraints and real code patterns. Catches bad designs _before_ any code is written. | A validation doc → `docs/2.5 research/` |
| 5 | `/plan-from-specs` | Produce a phased, step-by-step implementation plan. | A plan → `docs/3. plans/` |
| 6 | `/tdd-implement` | Build it phase by phase, tests first. Claude writes a failing test, then the code to pass it. | Working, tested code |
| 7 | **behavior test** | Run the app, do the thing in your phase's acceptance criteria, confirm it works. | Verified feature |

**Why this order matters:** thinking is cheap, code is expensive. Steps 1–5 are all about getting the thinking right so step 6 builds the right thing once. The `/research` step (4) is your safety net — if your spec violates a constraint, it gets caught here, not after you've built the wrong thing.

> Shortcut: `/build-pipeline` runs steps 2→3→4→5 back-to-back for you. Still do `/grill` (step 1) yourself first, and the behavior test (step 7) yourself at the end. When learning, prefer running each step separately so you see what each one produces.

---

## 9. Slash command cheat sheet

| Command | When to use it |
|---|---|
| `/grill` | Start of every feature. Sharpen a fuzzy idea into a precise design. |
| `/think-with-me` | Alternative to `/grill` if you want a gentler, more exploratory thinking session (designed for non-technical users). |
| `/codebase-design-analysis` | After grilling. "What are my options for building this, given the existing code?" |
| `/writing-detailed-specs` | After you've picked an option. Turn it into a buildable spec. |
| `/research` | After the spec. The quality gate — validate against constraints & patterns. |
| `/plan-from-specs` | After research passes. Produce the implementation plan. |
| `/tdd-implement` | After the plan. Build it, phase by phase, with tests. |
| `/build-pipeline` | Runs design→spec→research→plan as one orchestrated flow. |
| `/update-behavior-guide` | After your feature ships — regenerates the behavior-testing guide. |

---

## 10. The rules you must not break

These are enforced. Some are blocked automatically by hooks; all are checked at the `/research` step. You don't need to memorize the code, but if Claude proposes something that smells like a violation, **stop it and ask**. Full text in [CONSTRAINTS.md](../CONSTRAINTS.md) (17 constraints, C-01…C-17).

**The big ones for your phases:**

- **Only `vault/writer.py` writes to the vault.** Your pipeline never writes a file directly — it calls the writer. _(Auto-blocked.)_
- **Never overwrite `updated_by_human = true`.** Propose, don't overwrite. Especially critical for Phase 6 (Documentation). _(C-02.)_
- **Every AI decision writes to the audit log.** Especially critical for Phases 8 & 9, which _read_ that log. _(C-13.)_
- **Confidence thresholds come from config, never typed in code.** _(Auto-blocked. C-06.)_
- **Prompts are YAML files, never text in code.** _(Hook warning. C-07.)_
- **Every pipeline function returns `Success` or `Failure`.** Never a bare value. _(C-12.)_
- **Every tag-producing step calls `validate_tags`.** Bad tags get logged as violations, never silently accepted.
- **Build the manual CLI command first; add the scheduler last.** A daily/weekly job is automated _after_ the manual `kms` command is proven to work. (Relevant to Phases 8 & 9.) _(C-16.)_
- **No MCP tool before its pipeline is tested.** Add `kms_briefing` / `kms_promote` etc. _only_ after the pipeline works. _(C-15.)_

When you must do something that bends a rule, the answer is **not** to work around the hook — it's to surface it to the main driver.

---

## 11. How your work gets verified — behavior testing

**No one reads your code.** Verification is purely behavioral: run the app, do the action the acceptance criteria describe, confirm the result.

Example (a Phase 8 acceptance criterion):
> Capture + classify several notes, run `kms briefing`, then confirm `Briefings/2026/06_DD.md` appears containing today's captures, the classification decisions with confidence, items flagged for review, and patterns across the notes — with wikilinks back to the source notes.

Each phase in [the roadmap](roadmap/roadmap.md) lists its acceptance criteria as a checklist of behavior tests like this. **Before you start a feature, read its acceptance criteria — that is your definition of done.** When all the boxes pass, it ships.

The how-to-test mechanics (setting up a clean vault, resetting between runs, what to look for) are in [docs/system_behavior/TESTING_GUIDE.md](system_behavior/TESTING_GUIDE.md).

---

## 12. Common traps (save yourself an hour)

- **Don't fight the hooks.** If a write gets blocked, it's protecting a rule. Read the message, adjust the approach.
- **Each person stays in their own worktree.** Two people editing the same checkout = merge pain. Worktrees keep you isolated.
- **The vault is the truth, the database is a mirror.** If they disagree, `kms reconcile` re-syncs from the vault. Never "fix" by editing the database directly.
- **Adding a new `type/` tag breaks two count tests on purpose.** They assert the exact number of allowed types. If you add a type tag, you must update those counts. Claude knows this is in CLAUDE.md — let it handle it.
- **AI-output folders are write-only for the AI and capture-excluded.** Phases 6/8/9 write to `Documentation/`, `Briefings/`, `Synthesis/` — and the capture system deliberately ignores those folders so the AI never re-ingests its own reports.
- **Be precise in `/grill`.** Vague answers there ("it should just work nicely") produce vague specs that fail at `/research`. The discipline is the point.

---

## 13. Where to look when stuck

| You want… | Read… |
|---|---|
| Project conventions & patterns (Claude reads this automatically) | [CLAUDE.md](../CLAUDE.md) |
| Domain vocabulary | [CONTEXT.md](../CONTEXT.md) |
| The hard constraints | [CONSTRAINTS.md](../CONSTRAINTS.md) |
| Current project status & history | [STATE.md](../STATE.md) |
| Your phase's spec + acceptance criteria | [docs/roadmap/roadmap.md](roadmap/roadmap.md) |
| The big-picture architecture | [docs/architecture/overall_design.md](architecture/overall_design.md) |
| How to behavior-test | [docs/system_behavior/TESTING_GUIDE.md](system_behavior/TESTING_GUIDE.md) |
| The reference feature to imitate | [src/pipelines/capture.py](../src/pipelines/capture.py) |

When genuinely stuck: ask the main driver. You're learning a new way of working — questions are expected.

---

_Welcome aboard. Drive the thinking; let Claude do the typing._
