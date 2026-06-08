# Design — Classify Inline in Single-File Capture

_Phase 2 — Classify. Behavior-inventory ID prefix: **P2-CIC** (Classify-In-Capture)._
_Status: design — **REVISED 2026-06-08** (routing-model change: destination is now derived from assigned tags + project, not a free AI pick). Next: re-run `/writing-detailed-specs` then `/research`._
_Reader note: every section leads with plain English. Code references (file:line, names) sit in parentheses or sub-bullets — you can read this top to bottom and understand it without reading any code._

> **What changed in this revision (read first).** Previously the AI freely picked *one* destination — it named a project **or** a domain, chosen independently of the note's tags. The new locked model makes the destination **derived** from what the note is tagged with: the AI assigns the note's domain tag(s) and decides a project, designates **one primary (home) domain** when there are several, and the move target then follows a fixed rule — **project field present → move to that Project folder; no project → move to the primary domain folder.** Because the folder is computed from the stamped tags/project, the destination can never disagree with the tags (consistency is structural, not a second free choice). See the **Q5 diagram** and the **Routing model** section for the full picture. All other prior decisions (inline Option A, unified-prompt Option U2, typed candidate fields, R5 human-lock fallback, SUPPRESS, phasing) are unchanged — only the classify *decision shape* and *routing derivation* changed.

---

## Cast of characters (symbols used 3+ times)

| Name | Plain-English role |
|---|---|
| `capture_file()` | The single-file capture pipeline entry point — runs all the per-file steps. |
| `classify()` | The already-built pure function that asks the AI where a note belongs. **Reshaped in this revision:** it now returns the note's assigned domain tag(s), an optional project, and a designated primary domain (plus confidence + reason) — the caller *derives* the folder from those, instead of the AI naming the folder directly. |
| `primary domain` | When a note has no project and more than one domain tag, the single "home" domain the AI picks as the move target. The note still keeps all its domain tags; only the move uses the primary. |
| `derived routing` | The rule that turns the assigned tags/project into a destination folder: project present → that Project folder; else → the primary domain folder. There is no separate free folder pick. |
| `capture_folder()` | The existing whole-folder classify-and-route flow (already does for folders what we now want for single files). |
| `_store_nonmd()` | The capture step that handles non-`.md` files (PDFs, images, spreadsheets) — writes the summary card and moves the binary. |
| `_location_context()` | The folder-detector — looks at where a file sits and says "project Alpha", "domain Finance", "inbox", or "nowhere". |
| `move_guard` | A short-lived note-to-self the pipeline leaves so the file-watcher won't *redundantly re-process* a move the pipeline just made on purpose. |
| `ConfidenceBand.route()` | The rule that turns a confidence score into one of three outcomes: AUTO, SUGGEST, CLUELESS. |
| `format_for_prompt()` | Turns the live list of valid project/domain folders into readable text for the AI prompt. |

**Glossary of outcomes:** **AUTO** = AI is sure, system acts. **SUGGEST** = AI has a guess, system records it and waits for a human. **CLUELESS** = AI is stuck, system records that and waits. **LOCATED** = the file was already in a project/domain folder, so no AI was needed. **batch_id** = a tag grouping files that were dropped together; only files inside a *named subfolder* get one.

---

## Decision

**We add one new "ask the AI where this belongs" step inside the existing single-file capture pipeline, and it runs only for loose inbox drops — never for files already filed in a project or domain folder.** When the AI is confident the file is moved automatically; when it is unsure or stuck the file stays put and the AI's guess is written into the file's notes so a later phase can surface it. We also merge the two separate classify prompts into one shared prompt, in two phases.

**The destination is no longer a free AI pick — it is derived from the tags + project the AI assigns (precedence: project beats primary domain).** The classify step asks the AI to (1) confirm/assign the note's domain tag(s), (2) decide whether the note belongs to a specific project (set the project field) or only to domains, and (3) when there is no project and more than one domain, name one primary (home) domain. The move target is then *computed*: project present → `Projects/<project>/`; else → `Domain/<primary>/`. Because the folder is derived from the stamped tags/project, it always matches them — there is no separate free choice that could disagree (consistency is structural). This is the part that changed in this revision.

- New inline step slots into the per-file pipeline (`pipelines/capture.py::capture_file` — the 6-stage list at line ~977), reusing the built engine (`pipelines/classify.py::classify`), whose result shape is reshaped to carry tags + project + primary domain (see **Routing model** and **classify() reshape** below).
- For the recommended slot/shape, see **Option A** below; for prompt unification see **Option U2** below and **ADR phase2-0002**.
- **The classify step REUSES the domain tags the metadata stage already produced — it does not re-derive them.** The earlier metadata stage already assigns `ai_domain` / `ai_tags` (a full `domain/*` tag list). Classify's job is to pick the project and designate the primary domain *consistent with those tags*, not to re-tag from scratch. See **Wrinkle W1** for the verified mechanics.
- **The folder path keeps a folder-level target shape and shares the prompt's subject mechanism.** The whole-folder flow routes one folder to one destination — the per-note derive-from-tags model does not apply to a folder as a unit — so the folder caller maps the AI's project/primary-domain answer back to a single folder destination. See **Wrinkle W2**.
- **Folder-invoked capture suppresses the new classify step.** When `capture_file` is called from the folder path (`_capture_folder_files`), the new inline classify is **skipped** — the folder is the routing unit and its files inherit the folder verdict. The files are still fully captured (summarized + frontmatter-stamped + batch-stamped); only the per-file destination-classify (and the move it could trigger) is skipped, so a CLUELESS folder's files are never individually scattered. A context signal set by the folder path gates this. **Ships in Phase 1** — the moment inline classify is added, because the existing CLUELESS folder loop already calls `capture_file`. (Batch grouping verified intact under suppression — see Risk R6.)
- **CLAUDE.md is captured but never classified — already consistent.** A project's `CLAUDE.md` index page sits inside `Projects/<A>/`, so `_location_context()` reports it as LOCATED → the classify step is skipped (location wins). No new rule is needed; this is the existing LOCATED behavior and remains correct under the new model.

---

## Q1 Diagram — what happens inside (updated for derive-from-tags routing)

```
# Inline Classify in Single-File Capture — What Happens Inside
Scope: Shows what happens when one file is captured (drop, command, or scan).
       Does NOT cover folder drops (those already classify) or human confirm.
       Updated for the derive-from-tags routing model.

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

              File arrives (one file)
                       │
                       ▼
          ┌──────────────────────────┐
          │ Existing capture work:   │
          │ read text, fetch links,  │
          │ summarize, get title +   │
          │ domain tag(s)            │
          └────────────┬─────────────┘
                       │
                       ▼
          ┌──────────────────────────┐
          │ Where does the file sit? │
          └────────────┬─────────────┘
                       │
            ┌──────────┴───────────┐
            │                      │
   In a project/domain        Loose in inbox
   folder already             (no folder clue)
            │                      │
            ▼                      ▼
   ┌────────────────┐   ┌──────────────────────────┐
   │ Stay filed     │   │ Ask the AI to ASSIGN:     │
   │ here. Skip the │   │ a project (yes/no) + which│
   │ classify step. │   │ domain tag(s) + ONE       │
   └────────────────┘   │ primary domain; how sure? │
                        │ (binary: summarize first, │
                        │  then ask — two AI calls) │
                        └────────────┬─────────────┘
                                     │
                                     ▼
                        ┌──────────────────────────┐
                        │ How confident is the AI?  │
                        └────────────┬─────────────┘
                          ┌──────────┼──────────┐
                          │          │          │
                        HIGH       MEDIUM      LOW
                          │          │          │
                          ▼          ▼          ▼
                   ┌──────────┐ ┌──────────┐ ┌──────────┐
                   │ Stamp    │ │ Stay in  │ │ Stay in  │
                   │ tags +   │ │ inbox.   │ │ inbox.   │
                   │ project; │ │ Record   │ │ Record   │
                   │ DERIVE   │ │ suggested│ │ "AI was  │
                   │ folder,  │ │ tags +   │ │ stuck"   │
                   │ move,    │ │ project +│ │ + reason,│
                   │ index,   │ │ reason,  │ │ log it   │
                   │ log it   │ │ log it   │ │          │
                   └──────────┘ └──────────┘ └──────────┘

Routing rule (HIGH / AUTO only): the move target is DERIVED, not freely picked —
  if a project was assigned → move into that Project folder;
  otherwise → move into the AI's designated primary domain folder.
The note keeps ALL its domain tags either way.

Every outcome (HIGH/MEDIUM/LOW) writes a decision-log entry — nothing is silent.

Simplified: The two existing AI calls for a loose binary (summarize-attachment,
            then classify) are shown as one "ask the AI" box on the inbox branch.
            The located branch's existing sibling-write/move detail is collapsed
            into "stay filed here".

Important: "stay filed here" is NOT a no-op — the file is still summarized and
          its frontmatter stamped by the earlier stages. Only the new classify
          step and the move are skipped (the file is already where it belongs).
```

---

## Q5 Diagram — what changed (routing model)

```
# Inline Classify — What Changed (routing model)
Scope: Shows how the classify step decides WHERE a loose inbox file moves.
       OLD model (free pick) vs NEW model (derive from tags + project).
       This replaces the routing logic shown in the original Q1.

How to read this:
  Two columns = the OLD way (left) and the NEW way (right)
  Boxes       = steps in order
  Arrows      = what happens next

        OLD — AI freely picks a target          │          NEW — destination derived from tags
  ────────────────────────────────────────      │      ────────────────────────────────────────
                                                 │
          Loose inbox file                       │             Loose inbox file
                 │                                │                    │
                 ▼                                │                    ▼
     ┌──────────────────────────┐                │       ┌──────────────────────────┐
     │ Ask the AI: which folder? │                │       │ AI assigns the note's     │
     │ It names a target —       │                │       │ domain tag(s) AND decides │
     │ either a project OR a     │                │       │ a project (yes/no)        │
     │ domain — picked freely    │                │       └────────────┬─────────────┘
     └────────────┬─────────────┘                │                    │
                  │                               │          (more than one domain?)
                  ▼                               │                    │
     ┌──────────────────────────┐                │                    ▼
     │ Move the file to whatever │                │       ┌──────────────────────────┐
     │ folder the AI named       │                │       │ AI also names ONE primary │
     └──────────────────────────┘                │       │ (home) domain among them  │
                                                  │       └────────────┬─────────────┘
   The folder could disagree with                │                    │
   the note's own tags — the pick                │            ┌────────┴────────┐
   was a separate free choice.                   │      Has a project?      No project,
                                                  │            │             only domains
                                                  │            ▼                  ▼
                                                  │     ┌────────────┐    ┌────────────┐
                                                  │     │ Move to     │    │ Move to    │
                                                  │     │ that        │    │ the primary│
                                                  │     │ Project     │    │ domain     │
                                                  │     │ folder      │    │ folder     │
                                                  │     └────────────┘    └────────────┘
                                                  │
                                                  │   The folder ALWAYS matches the stamped
                                                  │   tags/project — no separate pick to disagree.

What changed in one line:
  OLD: the AI picked the destination directly and freely.
  NEW: the AI assigns tags + a project, and the destination is DERIVED from them
       by a fixed rule — project beats domain; with several domains, the AI's
       designated primary domain wins. The folder can never disagree with the tags.
```

---

## Routing model (the locked change)

**In plain terms:** instead of the AI naming a folder, the AI now tells us *what the note is about* (its domain tag(s) and whether it belongs to a project), and the system *computes* the folder from that. The rule is simple and fixed, so the file always lands in the folder that matches its own tags — there is no second, independent "pick a folder" decision that could disagree.

**The derivation rule (precedence):**
1. **Project field set → move to `Projects/<project>/` (the project root).** A project always wins over a domain — the same "prefer the specific project" rule the prompt already states.
2. **No project, one or more domain tags → move to `Domain/<primary>/` (the domain root).** When there is exactly one domain it is the primary by default; when there are several, the AI designates one primary (home) domain and that is the move target. The note keeps *all* its domain tags regardless.
3. **No project and no domain → cannot derive a destination → treated as CLUELESS** (stays in inbox, candidate recorded). This is the same "AI is stuck" outcome as a low-confidence result.

- The destination folder is built with the existing helpers — `vault_cfg.projects_path / <project>` and `vault_cfg.domain_path / <primary>` — the exact construction `_folder_destination()` already uses for the folder path (`pipelines/capture.py:1303-1308`). No new path helper is needed.
- Both destinations are **tree roots** (`Projects/<A>/` or `Domain/<D>/`), so `is_batch_subfolder()` returns False and `batch_id` stays NULL — unchanged from the prior design (Risk R3 still holds).
- **Structural consistency:** because the folder is derived from the stamped project/tags, the stamped frontmatter and the on-disk location cannot diverge. The old model could (the AI could name `Domain/Finance` while tagging `domain/Legal`); the new model removes that whole failure class.

---

## Wrinkle W1 — metadata tags vs classify tags (RESOLVED: reuse, do not re-derive)

**In plain terms:** there are two places the AI could assign domain tags. We use only the first one for tagging, and the classify step just *chooses among* what's already there — it never re-tags from scratch. This avoids two taggers that could disagree.

**Resolution:** The classify step **reuses** the domain tags the metadata stage already produced; it only (a) decides the project and (b) designates the primary domain consistent with those tags. Verified mechanically clean against the code:

- The metadata stage already extracts and validates a full tag list and pulls out a domain (`pipelines/capture.py:223-233`) — `ai_tags` carries every `domain/*` tag; `ai_domain` is the first one.
  - `MetadataResult` (`pipelines/capture.py:68-77`) carries `ai_domain: str | None`, `ai_tags: list[str]`, and `ai_project: str | None`. The classify stage runs *after* `apply_location_tags` (`:297-336`), so by the time classify runs it can read all three off the `MetadataResult` it receives. No plumbing gap.
- `apply_location_tags` sets `ai_project` from **location only** today (`:332-333`); for a loose inbox note it leaves `ai_project = None`. The classify step **extends** this: for an inbox note it is the step that *assigns* `ai_project` (and the primary domain) — the first time `ai_project` is set by anything other than location.
- The classify prompt is fed the note's existing tags as part of its `subject` (the unified-prompt `subject` already includes tags — see Option U2), so the AI sees the domains the metadata stage assigned and picks the primary from among them rather than inventing new ones.
- **Edge case — metadata assigned no domain tag:** then there is nothing to derive a domain destination from. The AI may still assign a project (→ project route); if it assigns neither project nor domain, the result is CLUELESS (rule 3 above). The spec must state that the classify prompt is allowed to *add* a domain tag the metadata stage missed **only** if it also routes there — but the lean default is: classify picks among existing tags, and a missing-domain note with no project is CLUELESS. Deferred detail in OQ-CIC-4.

---

## Wrinkle W2 — folder-path coexistence under the unified prompt (RESOLVED: folder caller maps to a folder target)

**In plain terms:** a whole dropped folder is filed as one unit to one folder — the per-note "tags decide the folder" idea does not apply to a folder. So when we share one prompt between single files and folders, the folder caller takes the AI's project/primary-domain answer and turns it back into a single folder destination, the same way it does today.

**Resolution:** The unified prompt (Option U2) returns the new structure (project / primary-domain / tags + confidence + reasoning) for **both** callers. The **single-file** caller derives a per-note destination via the precedence rule above. The **folder** caller maps the same answer to one folder destination using the existing `_folder_destination()` helper — i.e. project → `Projects/<project>/`, else → `Domain/<primary>/` — which is exactly what it computes today from `target_type/target_name`. Verified:

- The folder path already converts a classify answer into a folder via `_folder_destination(target_type, target_name, vault_cfg)` (`pipelines/capture.py:1303-1308`, called at `:1485`). Under the reshape, the folder caller reads `project`→`Projects/<project>/` or `primary_domain`→`Domain/<primary>/` and calls the same `move_folder` chokepoint — the folder still routes as a single unit; only the *field names* it reads from the AI answer change.
- The folder path does **not** stamp per-note tags from this answer — its files inherit the folder verdict and are captured under SUPPRESS, exactly as today. So "per-note derive from tags" never runs for a folder's files.
- **Phase boundary:** the single-file path adopts the reshaped prompt + result in **Phase 1**; the folder path migrates onto the same prompt in **Phase 2**. All 956 folder tests are the guardrail — Phase 2 must hold them green. Until Phase 2, the folder path keeps `prompts/classify_folder.yaml` and its `target_type/target_name` parse (`_parse_classify_json`, `:1276`); only Phase 2 deletes the folder prompt and switches the folder caller to the reshaped result.
- **Why a folder keeps a "folder target" shape, not per-note tags:** a folder has no single set of note-level tags to derive from — its files are heterogeneous. Forcing per-note derivation onto a folder would mean classifying each file individually, which contradicts the locked "folder is the routing unit" decision and SUPPRESS. Mapping one AI answer → one folder destination keeps the folder as the unit while still sharing the prompt's subject mechanism.

---

## Guardrail Checklist

From `/guardrail-check Review` — the constraints this change must satisfy. The spec writer must mark each as satisfied per build step.

```
Write Safety
[ ] C-01 · Vault is source of truth; documents table is index only
    Check: classify step writes only via write_note/move_note/move_attachment — no direct FS writes.
[ ] C-02 · updated_by_human=1 means hands off
    Check: AUTO move respects move_note's human-lock gate; recording a candidate must not overwrite a human-locked note.
[ ] C-03 · write_note is a pure writer — pipeline owns the merge
    Check: when recording SUGGEST/CLUELESS fields, read_note first and re-pass all existing values.

DB Integrity
[ ] C-04 · PRAGMA foreign_keys=ON on every new connection
    Check: reuse existing documents/batches helpers — no new connection factory.
[ ] C-05 · All schema changes via versioned .sql deltas
    Check: record the AI candidate in EXISTING columns/frontmatter; if a new column is needed, add a numbered migration — never in-code DDL.

LLM & Providers
[ ] C-06 · Confidence thresholds in config/thresholds.yaml, never in code
    Check: route via CONFIG.thresholds.for_pipeline("classify").route(confidence); no inline 0.85/0.60.
[ ] C-07 · All AI prompts are YAML files in prompts/, never inline f-strings
    Check: unified prompt stays in prompts/*.yaml; two input shapes solved by template/vars, not Python string building.
[ ] C-08 · Pipelines use get_provider(task, CONFIG) factory
    Check: classify() already uses get_provider("classify", config) — keep.
[ ] C-09 · Provider config carries model/synthesis_model/embedding_model — N/A (no new provider config).

Architecture
[ ] C-12 · Every public function in pipelines/ returns Success or Failure
    Check: new classify stage + helpers return Result.
[ ] C-13 · Audit log is non-negotiable
    Check: every AUTO/SUGGEST/CLUELESS writes audit.write(... pipeline="capture", stage="classify", source_ids=[vault_path]).
[ ] C-14/C-15/C-16 — N/A (no MCP, no scheduler).

Async & CLI
[ ] C-10 · CLI wraps async pipelines with asyncio.run() — already satisfied by the kms capture path; no change.
[ ] C-11 · load_dotenv once in cli/main.py — N/A.

Testing
[ ] C-17 · Never import CONFIG at module scope in tests
    Check: classify-stage tests pass an explicit PipelineContext/config.
```

---

## Implications

What this change actually means for the codebase. Plain-English lead, code-verified detail in the sub-bullet.

- **Loose inbox drops finally get a home automatically.** Today a note or binary with no folder clue gets parked unclassified; after this change the AI assigns its tags + project, and the file is moved to the folder *derived* from them when the AI is sure.
  - Today the single-file pipeline has no destination-classify step — the 6 stages are `[extract, enrich_urls, summarize, metadata, apply_location_tags, store]` (`pipelines/capture.py:977`). `classify()` is built but has **no production caller** (`pipelines/classify.py:31`).

- **The classify engine's result shape changes — but it has no production caller yet, so the change is safe.** Instead of returning a freely-named target folder, `classify()` will return the assigned domain tag(s), an optional project, and a designated primary domain (plus confidence + reasoning); the caller derives the folder. Because nothing in production calls it today, only its own unit tests move.
  - `ClassifyResult` today is `(target_type, target_name, confidence, reasoning)` (`pipelines/classify.py:16-28`); `classify()` validates `target_type in {"project","domain"}` and parses those JSON fields (`:101-117`). The reshape replaces `target_type/target_name` with `project: str | None`, `domains: list[str]`, `primary_domain: str | None` (names finalized in spec). The unit tests **P2-CL-01..06** assert the old fields and JSON shape (`tests/test_pipelines/test_classify.py`) — they change as part of this reshape; the design notes them as in-bounds because there is no production caller. The folder path still uses its own `_parse_classify_json` until Phase 2, so it is unaffected by the Phase-1 reshape.

- **Files already filed by hand are never re-*classified* — but they are still summarized and stamped.** Dropping a file straight into a project or domain folder skips the new classify step and the move (the file is already where it belongs), but the existing earlier stages still summarize it and write its frontmatter, exactly as today. Only the destination-classify LLM call and the move are skipped — not the whole capture. This is symmetry with how whole-folder drops already work (folder Case B routes by path with no classify LLM, but each file is still captured).
  - The folder flow already branches this way: `capture_folder()` Case B skips the LLM for `project`/`domain` locations and routes by path; Case A (inbox) calls the LLM (`pipelines/capture.py:1447`, `:1461`). The single-file path must gate the new step on the same `_location_context()` result (`vault/paths.py`).

- **A loose binary now costs two AI calls instead of being deferred.** A PDF/image/spreadsheet dropped in the inbox first gets a rich summary written, then gets classified — so the old "pending-routing, do it in Phase 2" placeholder concept retires.
  - The CLUELESS path in `_store_nonmd()` (`pipelines/capture.py:722-791`) currently writes a one-line placeholder marker with `status="pending-routing"` and audits `outcome="CLUELESS"`. The summarize-attachment LLM call already exists on the LOCATED path (`:636-646`); the inbox path must now also run it, then call `classify()`.

- **The watcher must not *redundantly re-process* our move — it will NOT "drag our file back."** There are two separate watcher reflexes, and neither wrongly relocates a classified file:
  - **Misplaced-`.md` sweep** fires *only* for a file at the **bare root** of `Projects/`/`Domain/` (e.g. `Projects/stray.md`) and moves it to inbox. A classify-move targets a real project folder (`Projects/Alpha/`), which is **not** misplaced, so the sweep never touches it (`_is_misplaced`, `vault/paths.py:212-263` — explicitly False for nested files, inbox, and AI-output folders; `_handle_misplaced_md`, `vault/watcher.py:485`). For `.md` notes this reflex is simply irrelevant to our destination.
  - **Binary cross-folder re-home** (`on_moved`, `vault/watcher.py:675`) runs on any binary move, but re-homes to the *correct* placement (where the pipeline already put it). Its real risk is **redundant DB/sibling/batch re-processing**, not relocation. `move_guard` makes it skip pipeline-initiated moves outright: `get_active().register(dst)` before the move (`pipelines/capture.py:676`, `:734`, `:1489`); the re-home checks the guard **first** and returns early (`vault/watcher.py:677`). **Verified (R1):** with the destination registered, the guarded branch exits before any re-processing — no double-fire.
  - **So `move_guard`'s job is to prevent double-processing, not a wrong drag-back.**

- **The index row and the file on disk must agree after a move.** When a note is moved, the recorded location in the index must change to match; for a binary, the summary card, the binary, and the index row must all line up.
  - For `.md` notes the existing rename path uses `move_note` + `documents.replace_path` atomically (`pipelines/capture.py:500`, `:523`). A classify-move can reuse the same writer chokepoints. For binaries the LOCATED path already does sibling-first write + `move_attachment` + `documents.upsert` (`_store_nonmd` `:648-720`) — the inbox AUTO case can route through that same code by feeding it the **derived** destination (project → `Projects/<project>/`, else → `Domain/<primary>/`) instead of the path-derived one.

- **batch grouping stays correct by doing nothing special.** A file the AI files into a project's top folder is not part of a "dropped-together" group, so it correctly gets no batch tag. (See Risk R3 for the verified detail.)
  - `is_batch_subfolder()` returns False for the root of `Projects/<A>/` / `Domain/<D>/` (depth < 2) and for `inbox/` root (`vault/paths.py:316-358`). So an AUTO-move to a tree root leaves `batch_id` NULL, which is consistent. The existing batch-stamp pre-step in `capture_file` keys off the *current* parent before the move (`:945`), so it sees inbox root → no batch.

- **The two classify prompts collapse into one — and the unified prompt's OUTPUT now changes too.** One prompt describes a single note (title + summary + tags); the other describes a whole folder (folder name + file list). The unified prompt must accept both input shapes AND return the new structure (project / primary-domain / tags) instead of a free `target_type/target_name`. This is the one genuinely hard design knot (see Options U1/U2 and ADR phase2-0002).
  - `prompts/classify.yaml` variables today: `title, summary, tags, valid_destinations`; it returns `target_type, target_name, confidence, reasoning` (`prompts/classify.yaml:6-12`). `prompts/classify_folder.yaml` variables: `folder_name, file_manifest, vault_context`. The reshape changes the *returned* JSON to carry the assigned project (or null), the domain tag(s), and a designated primary domain — the AI is instructed to assign-and-designate, not to name a folder. The "prefer the specific project over its parent domain" rule already in the prompt (`prompts/classify.yaml:18`) becomes the project-beats-domain precedence.

- **Module-depth read:** the single-file pipeline is a deep module (small entry, large internals) and we are deepening it further rather than adding a shallow new module. The `classify()` pure function is a real seam — it now gains its first production adapter (the capture stage), and the folder path can become its second adapter in prompt-unification Phase 2, so the seam earns its keep (2 adapters).
  - Deletion test: removing a standalone "classify pipeline" module would just push the same 5 lines of orchestration back into `capture_file` — so we do NOT create one; we keep `classify()` as the engine and call it inline (matches locked Decision 1).

`[UNVERIFIED: how the SUGGEST/CLUELESS candidate is best stored in frontmatter — no dedicated field exists today. NoteMetadata has status/project/confidence/extra (vault/frontmatter.py:51-69) but no "suggested_destination" or "reasoning" field. Resolved into Open Question OQ-CIC-1.]`

---

## Options grid

### (a) WHERE the classify step slots into the single-file path

#### Option A — One classify stage gated on "no location", reusing the existing store paths (Recommended)

**What this means:** We add a single new step to the per-file pipeline that only does work when the file is loose in the inbox. If it is, we ask the AI; if not, the step does nothing and the file is filed exactly as today. When the AI is confident, we hand the file to the *same* moving-and-filing code the LOCATED path already uses, just with the AI's chosen folder instead of the folder the file was sitting in.

**Approach:** Insert a `classify` stage after `apply_location_tags` and before `store`. The stage checks `_location_context()`: if `project`/`domain`, it passes the note through untouched (LOCATED — store behaves as today). If `inbox`/none, it builds `valid_destinations` via the registry, calls `classify()` (which now returns the assigned project / domain tag(s) / primary domain), **derives** the destination via the precedence rule (project → `Projects/<project>/`, else → `Domain/<primary>/`), routes the confidence through `ConfidenceBand.route()`, and:
- AUTO → set `ai_project` (and ensure the domain tag(s)/`ai_domain` reflect the primary) from the AI result, then let `store`/`_store_nonmd` do the move+write+index into the **derived** folder (registering the destination with `move_guard` first);
- SUGGEST/CLUELESS → record the candidate (suggested project / primary domain / reasoning) in frontmatter, audit, leave the file in inbox.

**Files touched:**
- `pipelines/capture.py` — add `classify` stage to the stage list; thread the AI result into `store`/`_store_nonmd`; retire the inbox `pending-routing` placeholder in favour of the recorded candidate.
- `pipelines/classify.py` — no change (reused as-is).
- `vault/registry.py` — no change (call `build_registry` + `format_for_prompt`).
- `config/thresholds.yaml` — optionally add an explicit `pipelines.classify` band (see OQ-CIC-2).
- `vault/frontmatter.py` — maybe one field for the candidate record (see OQ-CIC-1).

**Cost:** Dev: medium. Runtime: +1 LLM call per loose `.md`; +2 per loose binary (summary + classify). Maintenance: one new stage; reuses existing move/index/audit code.

**Risk:** The AUTO binary move must thread through `_store_nonmd`'s LOCATED branch correctly (sibling-first ordering, `move_guard`) or a binary could be moved without its summary card. Mitigated by reusing that exact code, not re-implementing it.

**Module depth:** No new module. Deepens the existing deep pipeline. New interface: none beyond reusing `classify()` (which gains adapter #1). Existing modules affected: `capture.py` (deep — stays deep); `classify()` (deep, now has a caller).

**What it defers:** The human confirm/review action (out of scope). Accurate batch counts (TD-043). Re-classification (out of scope).

**Constraints check:** C-01/02/03 satisfies (reuse writer chokepoints + read-before-write). C-06 satisfies (route via config band). C-07 satisfies (prompt stays YAML). C-08/12/13 satisfies. C-05 satisfies *if* the candidate is stored in existing frontmatter/`extra`; if a new typed field is added it stays in frontmatter (no DB column → no migration), still satisfies.

---

#### Option B — A separate `classify` stage that itself performs the move (does not reuse store)

**What this means:** Same trigger as Option A, but the new step does its own moving and index updates instead of handing off to the existing filing code.

**Approach:** The classify stage, on AUTO, calls `move_note`/`move_attachment` + `documents.replace_path`/`upsert` directly, then returns a terminal outcome that `store` skips.

**Files touched:** `pipelines/capture.py` (more invasive — new move/index logic in the stage), same others as A.

**Cost:** Dev: high. Runtime: same as A. Maintenance: high — duplicates the sibling-first + collision + move_guard logic that already lives in `_store_nonmd`.

**Risk:** Two copies of the binary-move-and-sibling logic drift apart; the LOCATED path and the AUTO path could diverge on collision handling or audit wording.

**Module depth:** No new module but creates a second, parallel move implementation — fails the "don't duplicate" smell. Deletion test: removing it would re-concentrate logic in `_store_nonmd` (which is where it belongs).

**Constraints check:** Same as A but higher risk of a C-01/C-13 gap from the duplicated path.

> Recommended: **Option A.** It reuses the already-tested filing-and-indexing code instead of cloning it, so AUTO routing behaves identically to the existing LOCATED path — the reader trades a small amount of threading-the-AI-result plumbing for not maintaining two move implementations.

---

### (b) HOW the two prompts unify

#### Option U1 — One prompt template with two render contexts (single YAML, conditional body)

**What this means:** Keep one prompt file. It has optional sections; the caller fills in either the "single note" details or the "folder" details, and the unfilled section stays blank.

**Approach:** A single `prompts/classify.yaml` whose user template renders a note block when `title/summary/tags` are present and a folder block when `folder_name/file_manifest` are present (Jinja `{% if %}`). Both callers pass `valid_destinations` (the folder caller renames `vault_context` → `valid_destinations`). Folder caller also starts returning/ignoring `reasoning`.

**Files touched:** `prompts/classify.yaml` (becomes conditional), delete `prompts/classify_folder.yaml`, `pipelines/capture.py::capture_folder` (switch prompt name + variable names), `pipelines/classify.py` (may gain an optional folder-shaped entry or the folder caller calls a thin sibling).

**Cost:** Dev: medium. Runtime: none. Maintenance: one prompt file, but with branching inside it.

**Risk:** A conditional prompt is harder for a non-coder to read and tune; an empty section accidentally rendered as whitespace can confuse the model.

**Module depth:** Real seam — one prompt, two adapters (note caller + folder caller). Passes the seam test.

**Constraints check:** C-07 satisfies (still YAML). Phase 2 of the unification (migrating the folder path) must hold all 956 folder tests green — this is the locked phased requirement.

---

#### Option U2 — One unified prompt fed a normalized "what to classify" text block (Recommended)

**What this means:** Keep one prompt file with no branching. Before calling the AI, each caller turns its own thing — a single note, or a folder of files — into the same short description ("Here is what to classify: …"). The prompt only ever sees one shape.

**Approach:** Introduce a tiny normalizer: the note caller renders "Title / Summary / Tags" into a `subject` block; the folder caller renders "Folder name / file list" into the *same* `subject` block. The unified `prompts/classify.yaml` takes `subject` + `valid_destinations` and now returns the **reshaped** fields — assigned project (or null), domain tag(s), designated primary domain, confidence, reasoning — instead of a free `target_type/target_name`. `classify()` builds the `subject` for the note caller; the folder caller builds its own `subject` and reuses the same engine, then maps the answer to one folder destination (Wrinkle W2).

**Files touched:** `prompts/classify.yaml` (single flat template: `subject`, `valid_destinations`), delete `prompts/classify_folder.yaml`, `pipelines/capture.py` (both `capture_file` inline stage and `capture_folder` build a `subject` string), `pipelines/classify.py` (accept a pre-built `subject` OR keep title/summary/tags and assemble internally — decided in spec).

**Cost:** Dev: medium. Runtime: none. Maintenance: one flat prompt — easiest to read and tune (matches the non-coder default).

**Risk:** The normalizer is new surface; if the folder `subject` loses signal the folder classification quality could dip — must be checked against the 956 folder tests in Phase 2 of the rollout.

**Module depth:** Real seam — one prompt, two adapters via one normalizer. The normalizer is shallow but earns its keep by being the single place the two shapes converge (deletion test: removing it forces branching back into the prompt, i.e. Option U1).

**Constraints check:** C-07 satisfies. Phased: Phase 1 ships the unified prompt for the single-file inline path; Phase 2 migrates the folder path onto it, holding folder tests green.

> Recommended: **Option U2.** A flat prompt with a small "describe the thing" step is easier for a non-technical owner to read and tune than a prompt with hidden `if` branches, at the cost of one extra tiny normalizer — and it keeps the AI's input identical whether the source is a note or a folder.

---

### Rejected alternatives

- **Separate post-capture classify pipeline** — contradicts locked Decision 1 (inline). One-line reason: re-walks the vault and re-reads notes capture already has in hand; adds a second entry point to keep idempotent.
- **Classify every file including located ones** — contradicts locked Decision 2. One-line reason: wastes an LLM call and risks overriding a deliberate human filing.
- **Keep two prompts forever** — contradicts locked Decision 5. One-line reason: two prompts drift; a routing-rule change must be made twice.
- **Aggressive: AUTO-move even on SUGGEST** — contradicts locked Decision 3. One-line reason: medium-confidence auto-moves are exactly the silent mistakes the confidence gate exists to prevent.

---

## Known tradeoffs

- **We pay an LLM call (two for binaries) on every loose inbox drop.** We accept the latency/cost to get zero-effort filing — the target user's whole premise. The alternative (defer to a batch pass) is what we are removing.
- **The unified prompt (U2) adds a normalizer step we didn't have.** We accept a little new code so the prompt itself stays branch-free and tunable by a non-coder.
- **SUGGEST/CLUELESS only *records*; it does not notify.** We accept that the human won't be actively pinged yet — surfacing/notifying is a deliberately separate later phase.
- **We give up the AI's freedom to file a note somewhere its tags don't point.** Deriving the folder from the tags/project means the AI can no longer "feel" that a Legal-tagged note really belongs in Finance and file it there — it must change the tag to change the destination. We accept this on purpose: it makes the filing explainable and keeps the frontmatter and the folder always in agreement (the consistency the new model buys).
- **A note with several domains needs the AI to pick one home.** When a note spans multiple domains, only one becomes the move target (primary). We accept that the file lives in one folder while still carrying every domain tag for search — there is no multi-home filing.

---

## Risks (for research / planning to verify)

- **R1 — Watcher vs pipeline move (verified, handled).** A classify AUTO-move is inbox→project/domain, i.e. cross-folder, so the watcher's `_handle_binary_move` hits its cross-folder branch whose **first** action is the `move_guard` check that returns early (`vault/watcher.py:677`). The pipeline must register the destination **before** moving (the existing pattern at `capture.py:676`/`:734`). For `.md` notes there is no drag-back risk at all: the misplaced-`.md` sweep only targets files at the bare root of `Projects/`/`Domain/`, not a real project folder, so `_is_misplaced` returns False for our `Projects/Alpha/` destination (`paths.py:212-263`). The binary g2 batch-stamp (`watcher.py:906`) only runs *after* the guard check, so it does NOT double-fire for a guarded pipeline move. **Who updates the DB row: the pipeline** (via `replace_path`/`upsert`), not the watcher.
- **R2 — DB-row consistency on classify-move (handled by reuse).** `.md`: `move_note` + `documents.replace_path` (atomic swap, old row deleted + new inserted). Binary: sibling-first `write_note` + `move_attachment` + `documents.upsert` on the sibling row, with `attachment_path` re-pointed. Both are existing chokepoints — the spec must route AUTO through them, not re-implement. **Verify:** no orphan row left at the old inbox path (test P2-CIC-07).
- **R3 — batch_id after AUTO-move (verified).** Destination = project/domain **root** → not batch-worthy (`is_batch_subfolder` depth rule) → `batch_id` correctly NULL. If a future variant routes into a *subfolder*, the destination would be batch-worthy and the pipeline (not the watcher) would need to stamp it; out of scope here since classify routes to roots only.
- **R4 — scan vs watcher double-processing (verified, handled).** `capture_file`'s idempotency guards run first: the `.md` content-hash check and the binary `source_hash` check short-circuit a second processing (`capture.py:877-940`). The retiring `pending-routing` early-exit guard (`:862-872`) must be re-examined: once binaries are classified at drop time, a loose binary will either be moved (AUTO) or carry a recorded candidate (SUGGEST/CLUELESS) — the spec must keep an idempotent re-entry that does not re-classify an already-recorded file (test P2-CIC-08 + the source_hash guard).
- **R5 — `move_note` human-lock on AUTO.** If a human edited the inbox note (`updated_by_human=true`), `move_note` with `actor="ai"` returns `Failure(recoverable=False)` (`writer.py:202`). **RESOLVED (locked):** treat a human-locked note as "do not move" → fall back to the recorded-candidate outcome (leave in place, record candidate + flag, audit), never fail the capture.
- **R6 — `folder_path` NFC mismatch (latent, must verify; logged as tech debt).** The folder branch writes `batches.folder_path` via `str(folder_path.relative_to(root).as_posix())` with **no NFC normalization** (`capture.py:1556`), while `capture_file`'s batch-stamp lookup **does** normalize NFC (`:948`). For a **non-ASCII folder name** with decomposed unicode, the two strings can mismatch → `find_by_folder_path` misses → a *second* batch row is created → files get a different `batch_id` than the folder row, breaking batch grouping in the suppress case. ASCII folder names are unaffected. Research/spec must confirm both paths normalize `folder_path` identically (NFC on both). Pre-existing; surfaced while confirming the suppress decision.

---

## Open questions

**OQ-CIC-1 — Where do we store the AI's SUGGEST/CLUELESS candidate?**

Right now a note's frontmatter has a `status` field and a free-form `extra` bag, but there is no dedicated place to record "the AI suggested Projects/Beta at 0.72 because …".

The question: do we add typed frontmatter fields for the suggested destination + reasoning, or stash them in the existing `extra` bag under agreed keys?

**If typed fields:** cleaner to read and validate; touches `vault/frontmatter.py` (`NoteMetadata` + `_KNOWN_KEYS`) — frontmatter-only, no DB migration. **If `extra` keys:** zero schema change, but the keys are untyped and easy to misspell, and `dumps()` round-trips `extra` so they persist.

Recommendation: **typed fields** (e.g. `suggested_project`, `suggested_primary_domain`, `classify_confidence`, `classify_reasoning`, plus `status: needs-review`). Under the new derive-from-tags model the candidate is the *suggested project and/or primary domain* (not a free `suggested_type/suggested_name`), so the field names should reflect that. One clear place a human or a later phase can read, no DB change, and it keeps the "decision log" legible in Obsidian. Not a blocker — the spec can finalize names.

**OQ-CIC-2 — Do we add an explicit `pipelines.classify` confidence band, or rely on the global fallback?**

Right now `config/thresholds.yaml` has `pipelines: {}`, so a "classify" lookup falls back to the global band (auto 0.85 / suggest 0.60).

The question: add an explicit `pipelines.classify` entry now, or keep using the fallback?

**If explicit:** classify routing can be tuned without affecting other pipelines. **If fallback:** one fewer config knob, but tuning classify also moves every other pipeline's band.

Recommendation: **keep the fallback for now**, add an explicit entry only when real runs show classify needs a different cutoff than capture. The folder path already relies on the same `for_pipeline("classify")` fallback, so behavior stays consistent. Not a blocker.

**OQ-CIC-3 — Does `classify()` keep its title/summary/tags signature, or take a pre-built `subject` (for U2)?**

Right now `classify()` takes `title, summary, tags, valid_destinations, config` and renders the note prompt itself.

The question: for the unified prompt (U2), should the folder caller assemble a `subject` and pass it through a `subject`-shaped `classify()`, or should `classify()` keep note-shaped args and the folder caller call a thin sibling?

**If `subject`-shaped:** one true engine, both callers normalize first — cleanest for unification. **If kept note-shaped:** the existing P2-CL tests (P2-CL-01..06) stay untouched, but the folder caller needs a parallel path.

Recommendation: decide in the spec; lean **`subject`-shaped** so there is exactly one engine, and update the P2-CL unit tests as part of unification Phase 2 (the 956 folder tests are the real guardrail). Not a blocker for Phase 1. **Note (this revision):** the result-shape reshape (drop `target_type/target_name`, add project/domains/primary-domain) already forces the P2-CL tests to change in Phase 1, so the "leave P2-CL untouched" argument for the note-shaped option is weaker than before.

**OQ-CIC-4 — When the metadata stage assigned NO domain tag and the AI sees no project, is that always CLUELESS, or may classify add a domain tag?**

Right now the metadata stage assigns the domain tags and classify (under the new model) picks the project + primary domain from among them.

The question: if a loose note arrived with no domain tag at all and the AI cannot tie it to a project, should classify be allowed to *add* a new domain tag (and route there), or should it always fall to CLUELESS (stay in inbox, record candidate)?

**If classify may add a domain:** fewer notes stuck in inbox, but now two stages can assign tags (re-opening the W1 "two taggers" concern). **If always CLUELESS:** one tagger only (clean), but a note the metadata stage under-tagged can never be auto-filed even if the classify AI is confident.

Recommendation: **always CLUELESS for the no-project/no-domain case** in Phase 1 — keep exactly one tagger (metadata), and let a confident-but-untagged note surface for human review rather than letting classify silently introduce a tag the metadata stage didn't. Revisit if real runs show too many false CLUELESS. Not a blocker — the spec finalizes the prompt instruction.

---

## ADR references

- **ADR phase2-0002 — Unify the two classify prompts via a normalized subject block** (`docs/architecture/phase2_classify/adr/0002-unify-classify-prompts.md`). The prompt-unification approach (Option U2) is hard to reverse (deletes `classify_folder.yaml`, changes the folder path's prompt contract), surprising without context (a future reader will ask why there's a normalizer instead of two prompts), and a real trade-off (U1 vs U2). All three ADR gates pass.
- Inherits batch infrastructure from the TD-040/TD-041 design (`docs/1_design/td040-td041-batch-id-fix.md`).
- Reuses the `classify()` engine from `docs/1_design/phase2/classify.md` (P2-CL).

---

## Options explored (summary)

- **Option A (chosen, slotting)** — one location-gated classify stage that reuses the existing store/`_store_nonmd` filing code. Chosen because it does not duplicate the binary-move-and-sibling logic.
- **Option B (not chosen)** — classify stage performs its own move. Rejected: duplicates tested filing logic, drift risk.
- **Option U2 (chosen, prompt)** — one flat unified prompt fed a normalized subject. Chosen: branch-free prompt is easier for a non-coder to tune.
- **Option U1 (not chosen)** — one conditional prompt template. Rejected: hidden `if` branches are harder to read/tune than a single normalizer.

## Routing-model alternatives explored (this revision)

- **Derive destination from assigned tags + project, precedence project > primary domain (chosen, locked)** — the AI assigns tags + project and designates a primary domain; the system computes the folder. Chosen: the folder always matches the stamped tags/project, so the frontmatter and the location can never disagree (structural consistency).
- **Free target pick (previous model, superseded)** — the AI named a project OR domain folder independently of the note's tags. Superseded: the AI could file a note in a folder its tags don't point to, allowing frontmatter/location drift and an unexplainable filing.
