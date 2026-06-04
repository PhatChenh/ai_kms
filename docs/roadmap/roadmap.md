---
created: 2026-04-26
updated: 2026-05-06
---
---

## created: 2026-04-26 updated: 2026-05-01

# PROJECT CONTEXT

## Problem Statement and Project Overview
Office workers in tech companies are struggling to keep up with the demanding knowledge and an ever-growing list of things they have to do. This get especially terrible when they become managers and take on multiple different responsibilities and business lines. They find that the simple traditional note-taking system does not work due to:
- Notes accumulate fast and hard to find, and often time written down and forgot
- Organization effort is needed to manage the notes, but there is no time for that
- There is synthesis part of note taking, which is productive, and the transcribing part, which is not productive but is essential for the synthesis part. However, the modern workflow requires so much transcribing - meeting notes, reports, etc. that makes the unproductive part is too overwhelming and leave little room for other work
- Ideas and thinking often get lost, and not revisited
This project is built to solve all those issues by building a knowledge management system with AI. The AI will help users with transcribing and organizational tasks, as well as becoming a thinking tool for users by surfacing patterns from the notes, or bringing up interesting ideas for users to revisit
## Key Rule
This project is aimed at professional office workers, like managers and executives, who is too busy to do anything else except using things out of the box. This means that we need to make:
- Aggressive simplification
- Zero organizational effort as the baseline assumption
- AI does work, human does judgment
- Defaults and invisibility layer as the main interface to the office workers, but have flexibility and tinkering layer underneath for customization done by the technical team to adapt better to the office workers' needs
## Key Design principles:
- **Progressive automation with human oversight** — high confidence = auto-file, medium = flag for review, low = stay in inbox
- **Show your work** — every AI decision should be traceable to source materials
- **Never silently overwrite human edits** — trust-breaking moment if it happens
- **Natural emptying mechanisms** — systems without them decay
- **One job per artifact** — if you can't describe a component's job in one sentence, it's too complex
- **Flexibility and Adaptability** - a knowledge system is a highly personalized product, and thus the code base need to be designed in modular, interchangeable patterns so the technical team could add more features as requested by the office worker

## Feature Roadmap
1. **Capture** — Listen for drops of web articles, email, chat sessions, notes, pdf, docs, YouTube video transcripts, and process them by summarize, and input metadata
2. **Classify** — Classify the notes based on their content, and move them to correct folder
3. **Semantic Search** — Find notes by meaning, not just keywords ("what do I know about stakeholder resistance" finds notes about "managing pushback in meetings")
4. **Three-Tier Retrieval** — Get quick summaries (hot), search snippets (warm), or full content (cold) — always starting cheap and going deep only when needed
5.  **MCP Server** — Let Claude (desktop app or web) directly search, classify, promote, and synthesize your notes in natural conversation
6.  **Note Promotion** — Extract structured knowledge (research notes, workflow templates, lessons) from raw captures, turning ore into refined metal
7. **Documentation** - Automatically keep track and write synthesis report (based on notes relating to projects) of current progress of active projects.
8. **Self-Learning** — Track human corrections to AI classifications and use them to improve future accuracy
9. **Weekly Synthesis & Daily Briefing** — Automatically connect dots across the week's notes to surface recurring themes, contradictions, and action items

# Build Order — AI-kms (Revised)

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

### **IMPORTANT NOTE**: The AI need to decide to move the note in WHICH subfolder inside `Domain/`, `Projects/`, `Archive/` - not just the big one, but actually the subfolders. This would require the AI to understand what projects/domains the users is working on, and which it should put the file to

Move notes from `inbox/` → `Domain/`, `Projects/`, `Archive/` based on confidence gates.

- `prompts/classify.yaml`
- `pipelines/classify.py` — classify → confidence-gate → route → move (four pure stages)
- Wire `routing.yaml` + `thresholds.yaml` (thresholds live in config, never hardcoded)
- Confidence gates: ≥0.85 auto-move | 0.60–0.85 stay in inbox, suggest categorization, but flag for human review | <0.60 flag as clueless and need human manual review
- Every decision → `audit_log` with: timestamp, source note, decision, confidence score, reasoning

> **Tag taxonomy enforcement (TD-019):** `pipelines/classify.py` MUST call `validate_tags` from `core/tags.py` on all AI-generated tags before writing. Tag violations are logged as `TAG_VIOLATION` audit entries — not silently dropped. `core/tags.py` is shared infrastructure built in Phase 1 (capture pipeline); classify just wires it in. Do not skip this step.

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
- **Every tag-generating pipeline MUST call `validate_tags` from `core/tags.py`.** This applies to capture (Phase 1), classify (Phase 2), promotion (Phase 5), and synthesis (Phase 9). Violations are logged as `TAG_VIOLATION` audit entries — never silently accepted. `NoteMetadata` field validators do NOT enforce the taxonomy (DECISION-019); the pipeline is the only enforcement point. A pipeline that skips `validate_tags` will silently pollute the taxonomy.