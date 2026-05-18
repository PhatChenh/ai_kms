---
created: 2026-04-26
updated: 2026-05-06
---
---

## created: 2026-04-26 updated: 2026-05-01

# Build Order — AI-kms (Revised)

> **Hard deadline: 30 June 2026** Today is 01 May. That is 60 working days. Day estimates below are per-phase budgets, not calendar days. If Phase 0 is already partially done (config.yaml, llm/ exist), subtract 1 day from the total.

---

## Delivery Milestones

| Milestone                 | Target       | What it unlocks                                                   |
| ------------------------- | ------------ | ----------------------------------------------------------------- |
| **M1 — First Delivery**   | ~15 May      | Capture + Classify + Search working end-to-end. System is useful. |
| **M2 — Boss Demo**        | ~30 May      | MCP MVP live. Claude Desktop can search and discuss the vault.    |
| **M3 — Full Feature Set** | 30 June      | Promotion, Documentation, Self-learning, Briefing complete.       |
| Weekly Synthesis          | Post 30 June | Low urgency. Ship after deadline pressure drops.                  |

---

## Phase 0 — Foundations (no AI yet, just plumbing)


Build everything downstream depends on. Never skip. Every later phase imports from `core/`.

- `core/` — `result.py`, `audit.py`, `confidence.py`, `pipeline.py`, `config.py`, `logging_setup.py`, `exceptions.py`
- `config/` YAML files — `config.yaml`, `thresholds.yaml`, `routing.yaml`
- `llm/prompt_loader.py` — load `prompts/*.yaml` once at startup
- `llm/provider.py` + `claude_provider.py` — one working AI call end-to-end
- `vault/` — `paths.py`, `frontmatter.py`, `reader.py`, `writer.py` (idempotent upsert, respects `updated_by_human`)
- `storage/` — `db.py`, `schema.sql`, `migrations/` (versioned SQL deltas), `audit_log.py`
- **Smoke test before moving on:** write a markdown file with frontmatter → parse → upsert to SQLite → write an audit row. All green = Phase 0 done.

> **Risk flag:** If `config.yaml` and `llm/` already exist from earlier work, Phase 0 is 1 day, not 2. Verify which modules are already wired end-to-end vs. just scaffolded.

---

## Phase 1 — Capture _(Roadmap Feature 1)_


Drop file into `inbox/` → AI writes summary + metadata back into frontmatter. No classification yet.

**Accepted drop types:** `.md` files, PDF, DOCX. **No video files.** YouTube/website content
enters the vault as a `.md` note containing the link — not as a media file. Summarizing the
linked YT/web content is a later enhancement, not a Phase 1 video handler.

**Two input shapes, two flows:**
- **`.md` drop** → AI writes summary + metadata into the note's own frontmatter, in place.
- **non-md drop (PDF, DOCX)** → AI creates a sibling `.md` summary note (body carries an
  Obsidian `[[wikilink]]` to the source + a `source_file` frontmatter field), then the source
  binary is moved to the `attachment/` folder via `vault.writer.move_attachment`. The `.md`
  sibling is what gets classified and searched; the binary is reference-only.

> **Body-preservation discipline (capture pipeline):** `vault/writer.py:write_note`
> replaces the *entire* note body — it does not merge or append. Only frontmatter is
> merged. The `.md`-drop flow MUST pass the original note body unchanged as `content`;
> the AI summary goes into the `summary` frontmatter field, never the body. A pipeline
> that passes the summary as `content` silently wipes the user's note.
> **Acceptance test:** drop a `.md` with a known body → run capture → assert the body is
> byte-identical and the `summary` frontmatter field is populated.

- `handlers/base.py` + `registry.py` (self-registering ABC)
- **Handlers, in build order:** `markdown_handler.py`, `pdf_handler.py`, `docx_handler.py`
- `pipelines/capture.py` — extract → summarize → metadata → store (four pure stages, no
  bundling). The non-md branch adds: create sibling `.md` + `move_attachment` source → `attachment/`.
- `prompts/summarize.yaml`, `prompts/extract_metadata.yaml`
- CLI: `kms capture <file>`
- Every capture writes to `audit_log`
- `attachment/` is the single home for all non-md source files.
- Add web/YT-link summarization, email, chat handlers **only after** the pipeline proves
  itself with markdown + pdf + docx.

> **Rule:** One handler working end-to-end before adding the next. Markdown → PDF → DOCX.

---

## Phase 2 — Classify + Route _(Roadmap Feature 2)_


Move notes from `inbox/` → `Domain/`, `Projects/`, `Archive/` based on confidence gates.

- `prompts/classify.yaml`
- `pipelines/classify.py` — classify → confidence-gate → route → move (four pure stages)
- Wire `routing.yaml` + `thresholds.yaml` (thresholds live in config, never hardcoded)
- Confidence gates: ≥0.85 auto-move | 0.60–0.85 stay in inbox, suggest categorization, but flag for human review | <0.60 flag as clueless and need human manual review
- Every decision → `audit_log` with: timestamp, source note, decision, confidence score, reasoning

> **Do not proceed** to Phase 3 until you can drop a test note into inbox and watch it land in the right folder with an audit entry.

---

## Phase 3 — Search + Three-Tier Retrieval _(Roadmap Features 3 + 4)_


The vault is now queryable. This is what makes the MCP useful.

- `storage/embeddings.py` — write/read vectors using `sentence-transformers` (Python equiv of `Xenova/all-MiniLM-L6-v2`)
- `retrieval/keyword.py` — FTS5 full-text search
- `retrieval/semantic.py` — embedding cosine similarity
- `retrieval/hybrid.py` — merge and re-rank both result sets
- `retrieval/tiers.py` — dispatcher:
    - **Hot** → summary field only (cheap, fast, fits in context)
    - **Warm** → matching snippet + surrounding lines
    - **Cold** → full note content (only when explicitly needed)
- CLI: `kms search "<query>"` — always starts at hot tier, escalates on demand
- Index is built from audit_log + vault scan at startup; incremental updates on each capture

> **Tier rule:** callers never decide the tier — they ask for a query and a max_cost. `tiers.py` decides where to start and whether to escalate.

---

## ★ MILESTONE: First Delivery (~15 May)

At this point: notes flow from inbox → classified → searchable. The system is independently useful. Stop here, test it on real notes, fix what breaks.

---

## Phase 4 — MCP Server MVP _(Roadmap Feature 9, pulled forward)_

Thin layer over the existing pipelines. The boss uses Claude Desktop to talk to the vault.

**MVP scope (enough for the demo — no more):**

- `mcp_server/server.py` — MCP entrypoint, stdio transport first
- `mcp_server/tools.py` — expose **three tools only**:
    - `kms_search(query, tier)` — calls `retrieval/tiers.py`
    - `kms_capture(content, source_type)` — calls `pipelines/capture.py`
    - `kms_classify(note_path)` — calls `pipelines/classify.py`
- `mcp_server/transport.py` — stdio (Claude Desktop compatible); HTTP transport deferred to post-deadline

**Full MCP tools** (`kms_promote`, `kms_synthesize`, `kms_documentation_update`, `kms_briefing`) are added in later phases as those pipelines are built — not before.

> **Scope discipline:** Do not build tools for pipelines that don't exist yet. An MCP tool that calls a stub is worse than no tool — it misleads the demo.

---

## ★ MILESTONE: Boss Demo Ready (~30 May)

Claude Desktop can: capture a note, classify it, search the vault by meaning. That is the demo. Prep two or three seeded notes so the vault has content to search over.

---

## Phase 5 — Note Promotion _(Roadmap Feature 7)_


Extract structured knowledge from raw captures. Turns ore into refined metal.

- `pipelines/promotion.py` — detect promotable notes → extract structure → write to `Domain/<x>/notes/`
- `prompts/promote.yaml`
- Note types to promote: research note | lesson learned | workflow template
- Confidence gate applies: <0.85 proposal goes to human review, never auto-promotes without high confidence
- Every promotion → audit_log
- Add `kms_promote` to `mcp_server/tools.py` once pipeline is tested

---

## Phase 6 — Documentation Auto-Update _(Roadmap Feature 6)_

One living page per active project. AI proposes updates, human approves.

- `pipelines/documentation.py` — read project materials → diff against current doc → propose update
- `prompts/documentation_update.yaml`
- **Hard rule:** never overwrite a field where `updated_by_human = true`. Propose only; write only on explicit approval.
- Conflict resolution rule: newest `updated_at` wins unless `updated_by_human = true`, in which case human always wins.
- Update cycle: triggered on new capture to a project folder (not on a timer — event-driven)
- Output → `Vault/Documentation/<project>.md`
- Add `kms_documentation_update` to MCP tools once tested

---

## Phase 7 — Self-Learning _(Roadmap Feature 8)_

No model fine-tuning. Just prompt augmentation from correction signals.

- `corrections` table already in schema from Phase 0 (zero additional storage work)
- Hook: detect when user manually moves an AI-classified note → log the correction with original classification + confidence + destination chosen by human
- Feed corrections as few-shot examples into the `classify.yaml` prompt at load time (most recent N corrections, configurable)
- Accuracy metric: track classification confidence pre/post corrections in audit_log
- This phase is lightweight because the infrastructure is already in place. The work is wiring the detection hook and the prompt injection.

---

## Phase 8 — Daily Briefing _(Roadmap Feature — pulled to post-MCP)_

Read-only consumer of audit_log. Was Phase 3 in the original plan; moved here because the MCP demo matters more to the boss than the briefing.

- `briefings/classification_report.py` — what got moved where today, what needs human review
- `briefings/daily.py` — compose the full daily report
- `prompts/briefing.yaml`
- Output → `Vault/Briefings/YYYY/MM_DD.md`
- `scheduler/runner.py` + `jobs.yaml` — daily trigger at a configured time
- Add `kms_briefing` to MCP tools

> **Note:** The briefing is useful but not demo-critical. The boss cares about search and capture, not the report. Ship this after M2.

---

## ★ MILESTONE: Full Feature Set (30 June)

Phases 0–8 complete. All MCP tools live. System runs autonomously.

---

## Phase 9 — Weekly Synthesis _(Roadmap Feature 5 — post deadline)_

Low urgency. No stakeholder is waiting for this. Ship it when the deadline pressure is off.

- `pipelines/synthesis_weekly.py`, `prompts/synthesize_weekly.yaml`
- Output → `Vault/Synthesis/`
- Scheduler: Sunday trigger via `jobs.yaml`
- Add `kms_synthesize` to MCP tools

---

## Rules of the road

- **Never skip Phase 0.** Every later phase imports from `core/`. Build it broken once and you rebuild everything.
- **One handler, one pipeline, end-to-end before adding breadth.** Don't write 7 handlers before classify works.
- **Audit log is non-negotiable from Phase 1.** Phase 8 (briefing) reads from it. No audit log = no briefing.
- **Schedulers come last in each phase.** Manual CLI first, then automate.
- **MCP tools are thin wrappers.** If you find yourself writing logic inside `mcp_server/tools.py`, stop. That logic belongs in a pipeline. The tool just calls the pipeline.
- **Never add an MCP tool before its pipeline exists and is tested.** A stub tool is a lie.
- **Scope the MVP hard.** Three MCP tools for the boss demo. Resist the urge to add more before M2.