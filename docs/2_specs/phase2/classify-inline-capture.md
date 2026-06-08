# Spec -- Classify Inline in Single-File Capture (P2-CIC)

_Phase 2 -- Classify. Behavior-inventory ID prefix: **P2-CIC** (Classify-In-Capture)._
_Source design: `docs/1_design/phase2/classify-inline-capture.md` (revised 2026-06-08, derive-from-tags routing model)._
_Reader note: every section leads with plain English. Code references sit in parentheses or sub-bullets -- you can read this top to bottom without reading any code._

> **This is a SPEC, not a plan.** It says WHAT to build, in what order things must exist, and what already exists to reuse. It does not write code or pick exact commits -- that is `/plan-from-specs`. Next step after this: run `/research` to verify the assumptions table against the live code.

---

## Glossary (plain-English names used throughout)

| Plain-English name | What it is in code |
|---|---|
| **Capture Pipeline** | The single-file capture flow (`pipelines/capture.py::capture_file`) -- runs every per-file step. |
| **Classify Step** | The new step this feature adds inside the Capture Pipeline. |
| **Classify Engine** | The already-built pure function (`pipelines/classify.py::classify`) that asks the AI where a note belongs. **Reshaped by this feature** to take a flat `subject` block and return tags + project + primary domain instead of a free folder name. |
| **Subject Builder** | A tiny new helper that turns a note (or a folder) into one normalized "here is what to classify" text block for the prompt. |
| **Confidence Gate** | The rule that turns a confidence score into AUTO / SUGGEST / CLUELESS (`CONFIG.thresholds.for_pipeline("classify").route(score)`). |
| **Destinations List** (Project Registry) | The live list of valid project/domain folders (`vault/registry.py` -- `build_registry` / `LiveRegistry.get_groups()` + `format_for_prompt`). |
| **Filer / Mover** | The existing code that moves a file into a folder and updates the index (`_store_nonmd` for binaries, the rename/move path in `_store_md` for notes; `move_note`, `move_attachment`, `documents.replace_path`, `documents.upsert`). |
| **Folder Detector** | The folder-detector (`vault/paths.py::_location_context`) -- returns `("project", name)`, `("domain", name)`, `("inbox", None)`, or `(None, None)`. |
| **Move Guard** | The short-lived registry (`vault/move_guard.py`) that tells the watcher "I moved this on purpose -- don't redundantly re-process it." |
| **Decision Log** | The audit log (`core/audit.py::write`). |
| **Note Writer** | The vault writer (`vault/writer.py::write_note`) used to record candidate fields on SUGGEST/CLUELESS. |
| **Folder Capture flow** | The whole-folder classify-and-route flow (`pipelines/capture.py::capture_folder` + `_capture_folder_files`). |

**Outcome words:** **AUTO** = AI is sure, system derives the folder from the stamped tags/project and moves the file there. **SUGGEST** = AI has a guess, system records it in frontmatter and leaves the file in inbox. **CLUELESS** = AI is stuck, system records that and leaves the file in inbox. **LOCATED** = file was already in a project/domain folder, classify step is skipped (but the file is still summarized and stamped). **SUPPRESS** = file arrived inside a dropped folder, classify step is turned off (the folder is the routing unit).

**Derived routing:** The move target is computed from the note's assigned tags + project, not freely picked by the AI. Precedence: project field present -> move to `Projects/<project>/`; no project, has domain(s) -> move to `Domain/<primary>/`; neither -> CLUELESS.

---

## Purpose

Today a note or attachment dropped loose in the inbox with no folder clue is captured (summarized + frontmatter-stamped) but never filed -- it sits there. This feature adds one new step to the single-file Capture Pipeline that, **only for loose inbox drops**, asks the AI to assign the note's project and designate a primary domain. The destination folder is then **derived** from those assignments (project beats domain). When the AI is confident the file is moved there automatically; when it is unsure or stuck the AI's guess is written into the file's own frontmatter and the file stays in the inbox for a later human-review phase. Files already filed by hand into a project/domain folder are still summarized and stamped exactly as today -- only the AI classify call and the move are skipped.

It also reshapes the Classify Engine to accept a flat `subject` block (instead of separate title/summary/tags) and return the new derive-from-tags fields, and begins unifying the two separate classify prompts into one branch-free prompt in two phases.

**After this feature the system can:** auto-file a confident loose drop end-to-end (capture -> classify -> derive folder -> move -> index -> audit) with zero human effort, and record a legible "the AI suggested project X / domain Y at confidence Z because W" candidate on every uncertain drop. The filed note's folder always matches its stamped tags/project (structural consistency).

---

## Phasing (this spec covers BOTH phases of the feature)

- **Phase 1 -- single-file inline classify.** The new Classify Step, the Classify Engine reshape (subject-shaped + derive-from-tags result), the SUPPRESS signal for folder-invoked capture, the typed candidate frontmatter fields, AUTO/SUGGEST/CLUELESS handling, and the unified prompt used by the single-file path. Entry points covered: watcher single-file drop, `kms capture <file>`, `kms capture --scan` loose files.
- **Phase 2 -- folder-path migration onto the unified prompt.** Move the working Folder Capture flow onto the same subject-shaped Classify Engine + unified prompt; delete the separate folder prompt; hold all 956 folder tests green.

Component sections below are tagged **[P1]** or **[P2]**.

---

## Already built (reuse, do not rebuild)

| Component | Location | What it does | How this spec uses it | Depth |
|---|---|---|---|---|
| Classify Engine | `pipelines/classify.py::classify` + `ClassifyResult` | Async pure function -- asks AI which folder; currently returns `target_type`/`target_name`/`confidence`/`reasoning`; all failures `recoverable=True` except template-render. No vault writes, no audit, no routing. | Reshaped to take `subject` and return `project`/`domains`/`primary_domain` (see component 6). The Classify Step's first production caller. | deep |
| Classify prompt | `prompts/classify.yaml` | The note-classify prompt: vars `title, summary, tags, valid_destinations`; returns four JSON fields. | Reshaped in Phase 1 to take a flat `subject` block + `valid_destinations` and return the new derive-from-tags fields (Option U2). | shallow |
| Folder prompt | `prompts/classify_folder.yaml` | Separate prompt for whole-folder classify: vars `folder_name, file_manifest, vault_context`; returns 3 fields (no `reasoning`). | **Deleted in Phase 2** once the folder path builds a `subject`. Unchanged in Phase 1. | shallow |
| Confidence Gate | `core/config.py::ConfidenceBand.route` + `Thresholds.for_pipeline` | Maps a score to AUTO/SUGGEST/CLUELESS; `for_pipeline("classify")` falls back to the `global` band (auto 0.85 / suggest 0.60) since `pipelines: {}`. | Route the AI's confidence -- never compare floats in pipeline code (C-06). | deep |
| Destinations List | `vault/registry.py::build_registry`, `LiveRegistry.get_groups`, `format_for_prompt`, `ProjectRegistry` | Builds the live project-to-domain map; `format_for_prompt` renders it as the AI's destination menu. | Build `valid_destinations` for the loose-inbox branch. | deep |
| Folder Detector | `vault/paths.py::_location_context` | Returns the `(type, name)` tuple for where a path sits. | Gate the Classify Step: project/domain -> pass through; inbox/none -> classify. | deep |
| Binary Filer | `pipelines/capture.py::_store_nonmd` (LOCATED branch, lines ~590-720) | Sibling-first write + `move_attachment` + `documents.upsert`, with `move_guard` register before the move and a LOCATED audit row. | AUTO binary case routes through this code by feeding it the AI-derived destination instead of the path-derived one. | deep |
| Note Filer | `pipelines/capture.py::_store_md` (rename/move path, lines ~495-541) | `move_note` + `documents.replace_path` (atomic old-row-delete + new-row-insert) with rollback. | AUTO `.md` case reuses the move + `replace_path` chokepoints. | deep |
| Move Guard | `vault/move_guard.py::get_active().register(dst)` | Suppresses watcher re-home for pipeline-initiated moves; checked first in the watcher's binary cross-folder branch. | Register the destination before any AUTO move (existing pattern). | deep |
| Decision Log | `core/audit.py::write` | Writes one audit row per AI decision with `pipeline`, `stage`, `outcome`, `source_ids`, reasoning. | Every AUTO/SUGGEST/CLUELESS writes one `stage="classify"` row. | deep |
| Idempotency guards | `pipelines/capture.py:877-940` (`.md` content-hash + binary `source_hash`) | Short-circuit a re-run of an unchanged file. | Kept as-is; they run before the pipeline so they protect re-entry. | deep |
| Batch-stamp pre-step | `pipelines/capture.py:945-973` | If the parent folder is batch-worthy, look up/create a batch row and stamp `ctx.batch_id`. | Keep; keys off the current parent before the move -> inbox root is not batch-worthy -> no batch (R3). | deep |
| `is_batch_subfolder` | `vault/paths.py::is_batch_subfolder` | True only for named subfolders under inbox/Projects/Domain, not roots. | Confirms an AUTO move to a project/domain root leaves `batch_id` NULL. | deep |
| Frontmatter model | `vault/frontmatter.py::NoteMetadata` + `_KNOWN_KEYS` | Typed frontmatter; `status`, `confidence`, `attachment_path`, `source_hash`, `extra` already present. | Gains the new typed candidate fields (see component 2). | deep |
| `PipelineContext` | `core/pipeline.py::PipelineContext` | Carries `config`, `correlation_id`, `db_path`, `taxonomy`, `batch_id` through a run. | Gains a SUPPRESS signal field (see component 3). | deep |
| `read_note` | `vault/reader.py::read_note` | Reads a note's metadata + body. | Read-before-write for the candidate record (C-03 -- pipeline owns the merge). | deep |
| `MetadataResult` | `pipelines/capture.py` (line 68-77) | Carries `ai_domain`, `ai_tags`, `ai_project` plus summary/title from earlier stages. | The Classify Step reads these to feed the AI and to derive the destination from them. | deep |
| `apply_location_tags` | `pipelines/capture.py::apply_location_tags` | Sets `ai_project` from location (project folders), adds `domain/<D>` tag from location (domain folders). For inbox, leaves `ai_project = None`. | Classify Step runs AFTER this stage; for inbox drops, the Classify Step is the first place `ai_project` gets set by anything other than location. | deep |
| Pending-routing guard | `pipelines/capture.py:860-872` | Early-exit for CLUELESS binaries already parked with `status=pending-routing`. | **Retired** by this feature -- loose binaries are now classified at drop time. See component 7. | deep |

---

## Q1 Diagram -- what happens inside (from design, updated for derive-from-tags)

```
# Inline Classify in Single-File Capture -- What Happens Inside
Scope: Shows what happens when one file is captured (drop, command, or scan).
       Does NOT cover folder drops (those already classify) or human confirm.
       Updated for the derive-from-tags routing model.

How to read this:
  Boxes  = steps in order
  Arrows = what happens next
  Forks  = a decision with different outcomes

              File arrives (one file)
                       |
                       v
          +----------------------------+
          | Existing capture work:     |
          | read text, fetch links,    |
          | summarize, get title +     |
          | domain tag(s)              |
          +------------+---------------+
                       |
                       v
          +----------------------------+
          | Where does the file sit?   |
          +------------+---------------+
                       |
            +----------+-----------+
            |                      |
   In a project/domain        Loose in inbox
   folder already             (no folder clue)
            |                      |
            v                      v
   +----------------+   +--------------------------+
   | Stay filed     |   | Ask the AI to ASSIGN:    |
   | here. Skip the |   | a project (yes/no) +     |
   | classify step. |   | which domain tag(s) + ONE|
   +----------------+   | primary domain; how sure?|
                        | (binary: summarize first,|
                        |  then ask -- two AI calls)|
                        +------------+-------------+
                                     |
                                     v
                        +--------------------------+
                        | How confident is the AI? |
                        +------------+-------------+
                          +----------+----------+
                          |          |          |
                        HIGH       MEDIUM      LOW
                          |          |          |
                          v          v          v
                   +----------+ +----------+ +----------+
                   | Stamp    | | Stay in  | | Stay in  |
                   | tags +   | | inbox.   | | inbox.   |
                   | project; | | Record   | | Record   |
                   | DERIVE   | | suggested| | "AI was  |
                   | folder,  | | tags +   | | stuck"   |
                   | move,    | | project +| | + reason,|
                   | index,   | | reason,  | | log it   |
                   | log it   | | log it   | |          |
                   +----------+ +----------+ +----------+

Routing rule (HIGH / AUTO only): the move target is DERIVED, not freely picked --
  if a project was assigned -> move into that Project folder;
  otherwise -> move into the AI's designated primary domain folder.
The note keeps ALL its domain tags either way.

Every outcome (HIGH/MEDIUM/LOW) writes a decision-log entry -- nothing is silent.

Simplified: The two existing AI calls for a loose binary (summarize-attachment,
            then classify) are shown as one "ask the AI" box on the inbox branch.
            The located branch's existing sibling-write/move detail is collapsed
            into "stay filed here".

Important: "stay filed here" is NOT a no-op -- the file is still summarized and
          its frontmatter stamped by the earlier stages. Only the new classify
          step and the move are skipped (the file is already where it belongs).
```

## Q2 Diagram -- how it connects to others

```
# Inline Classify in Single-File Capture -- How It Connects
Scope: Shows what the new Classify Step touches inside and around the
       Capture Pipeline. Does NOT show the internal HIGH/MEDIUM/LOW
       flow (see Q1 for that).

How to read this:
  Center box     = the new step being built
  Solid boxes    = components that already exist
  Dashed boxes   = planned / future reuse, not built in Phase 1
  Arrow labels   = what passes between them

         +------------------+        +------------------+
         | Folder Detector  |        | Destinations List|
         | "where does this |        | Live menu of     |
         | file sit?"       |        | project/domain   |
         +--------+---------+        +--------+---------+
                  | project/domain             | valid folders
                  | -> skip; inbox -> go       | to choose from
                  v                            v
         +----------------------------------------------+
         |              CLASSIFY STEP                   |
         |  For a loose inbox file: asks the AI to      |
         |  assign a project + designate a primary      |
         |  domain, then derives the folder from them   |
         +---+----------+------------+----------+-------+
             |          |            |          |
   builds a  |  project +  confidence          | every outcome
   short     |  domains + |  score   |         | (AUTO/SUGGEST/
   subject   |  primary   |          |         |  CLUELESS)
             v          |           v          v
      +------------+    |    +------------+ +------------+
      | Subject    |    |    | Confidence | | Decision   |
      | Builder    |    |    | Gate       | | Log        |
      | Normalizes |    |    | AUTO /     | | Records    |
      | note->text |    |    | SUGGEST /  | | the call   |
      +------------+    |    | CLUELESS   | +------------+
             |          v    +-----+------+
        feeds|    +------------+   |
             +--->| Classify   |   | AUTO          SUGGEST/CLUELESS
                  | Engine     |   | v                   |
                  | AI "which  |   v                     v
                  | project or |+------------+   +----------------+
                  | domain?"   || Filer /    |   | Note Writer    |
                  +------------+| Mover      |   | Records guess  |
                       ^        | Moves file,|   | + needs-review,|
                       .        | updates    |   | leaves in inbox|
                  . . .+. . .  | index      |   +----------------+
                  . Folder   .  +-----+------+
                  . Capture  .        | registers move first
                  . flow     .        v
                  . (Phase 2).  +------------+
                  . . . . . .   | Move Guard |
                                | Tells File |
                                | Watcher to |
                                | skip our   |
                                | own move   |
                                +------------+

Solid = already built (Classify Engine, Filer/Mover, Move Guard, Decision Log,
        Destinations List, Folder Detector, Note Writer all exist). Subject
        Builder is the one small NEW piece this feature adds.
Dotted = Folder Capture flow reuses the Subject Builder + Classify Engine +
         unified prompt in Phase 2 of this feature.

Simplified: the AUTO path (Filer/Mover -> Move Guard) and the SUGGEST/CLUELESS
            path (Note Writer/Frontmatter) are both shown as outcomes of the
            Confidence Gate. The folder-drop SUPPRESS signal (skip the Classify
            Step entirely) is not drawn -- it simply turns this whole center box off.
```

---

## Feature overview

**Happy path (loose `.md` note, AUTO).** A note lands in `inbox/`. The Capture Pipeline runs its existing steps (extract -> enrich URLs -> summarize -> metadata -> apply-location-tags). The new Classify Step then runs. It asks the Folder Detector where the file sits; "inbox" -> it builds the destination menu from the live Destinations List, builds a short `subject` from the note's title/summary/tags via the Subject Builder, and calls the Classify Engine. The AI returns the assigned project (or null), domain tag(s), a designated primary domain, confidence, and reasoning. The Confidence Gate says AUTO. The Step stamps the project field and ensures the domain tags reflect the AI's assignment, then **derives** the destination: project present -> `Projects/<project>/`; else -> `Domain/<primary>/`. The existing store step moves the note (registering the destination with the Move Guard first), swapping the index row atomically. One AUTO audit row is written.

**Happy path (loose `.md` note, domain-only).** Same as above but the AI assigns no project -- only domain tags and designates one primary domain. Destination derived as `Domain/<primary>/`. The note keeps all its domain tags; only the move uses the primary.

**Precedence (project beats domain).** When the AI assigns both a project AND domain tags, the project field wins. The note moves to `Projects/<project>/`. It still carries all its domain tags for search and briefing; only the on-disk location follows the project.

**Loose binary (PDF/image/spreadsheet), AUTO.** Same trigger, but the binary first gets a rich attachment summary written (the summarize-attachment AI call that the LOCATED path already does), then gets classified -- two AI calls. On AUTO, the existing Binary Filer writes the sibling card first, moves the binary into the derived folder's `attachment/`, and upserts the sibling's index row with `attachment_path` re-pointed. The old "pending-routing" placeholder concept retires.

**SUGGEST / CLUELESS.** The Confidence Gate says SUGGEST (medium) or CLUELESS (low). The file is NOT moved. The Step records the AI's guess into the file's own frontmatter -- `suggested_project`, `suggested_primary_domain`, `classify_confidence`, `classify_reasoning`, and `status: needs-review` -- reading the note first and re-passing existing values (the pipeline owns the merge, C-03). One SUGGEST or CLUELESS audit row is written. For CLUELESS, `suggested_project` and `suggested_primary_domain` may be null when the AI had no candidate.

**LOCATED (file already in a project/domain folder).** The Folder Detector returns project/domain. The Classify Step does nothing -- but the earlier capture steps still summarized the file and stamped its frontmatter (this is not a no-op for the pipeline; only classify + move are skipped). No classify call, no move, no `suggested_*`/`needs-review` fields.

**SUPPRESS (file arrived inside a dropped folder).** When the Folder Capture flow captures a folder's files one-by-one, it sets a SUPPRESS signal in the context. The Classify Step sees the signal and skips itself entirely -- the folder is the routing unit and its files inherit the folder verdict. The files are still fully captured (summarized + frontmatter-stamped + batch-stamped).

**Human-locked note on AUTO (R5).** If a human edited the inbox note (`updated_by_human=true`), `move_note` with `actor="ai"` returns `Failure(recoverable=False)`. The Step does NOT fail the capture -- it falls back to the recorded-candidate outcome: leave the file in place, record the candidate fields + `needs-review`, write an audit row.

**Re-entry / idempotency (R4).** A second capture of the same unchanged file short-circuits at the existing content-hash / `source_hash` guards before the pipeline runs. The retiring "pending-routing" early-exit is replaced by the idempotent re-entry -- see component 7.

**Structural consistency (P2-CIC-13).** Because the folder is derived from the stamped project/tags, the on-disk location and the frontmatter always agree. There is no separate free-pick step that could place a note in a folder its tags don't point to.

---

## Out of scope

- **Human confirm / review ACTION** -- this feature only *records* the candidate; surfacing it, notifying, and acting on a human's confirm is a separate later phase. _Deferred -- no phase assigned yet._
- **Re-classification of already-filed notes** -- once a note is in a project/domain folder it is never re-classified. _Out of scope by locked Decision 2._
- **An MCP tool for classify** -- no tool until the pipeline exists and is tested (C-15). _Deferred to MCP phase._
- **Batch infrastructure changes** -- `batches` table, `batch_id` semantics, and `file_count` accuracy (TD-043) are inherited as-is; this feature does not change them. AUTO routes to roots only, so `batch_id` stays NULL (R3). _Inherited from TD-040/TD-041._
- **An explicit `pipelines.classify` confidence band** -- keep the global fallback for now (locked OQ-CIC-2). _Add only when real runs show classify needs a different cutoff._
- **Image capture (PNG/JPG)** -- image handlers are not-implemented stubs (OQ-009). This feature must not assume image drops produce summaries; a loose image that fails extraction follows the existing failure path, not a classify path. _Blocked by OQ-009._
- **Routing into a subfolder** -- classify routes to project/domain ROOTS only. Subfolder routing (and the batch-stamp it would require) is not built. _Out of scope._
- **Classify adding new domain tags** -- when the metadata stage assigned no domain tag and the AI cannot tie the note to a project, the result is CLUELESS. Classify does not invent new domain tags the metadata stage missed (locked OQ-CIC-4). _Revisit if real runs show too many false CLUELESS._

---

## Constraints

Every component below must satisfy these. Sourced from the design's Guardrail Checklist and `CONSTRAINTS.md`.

- **C-01 - Vault is source of truth** -- the Classify Step writes only via `write_note` / `move_note` / `move_attachment`; no direct filesystem writes. _Source: CONSTRAINTS.md C-01; hook hard-block._
- **C-02 - `updated_by_human=1` means hands off** -- the AUTO move must respect `move_note`'s human-lock gate; recording a candidate must not overwrite a human-locked note. Resolved fallback: human-locked -> recorded-candidate outcome (Decision R5). _Source: C-02._
- **C-03 - `write_note` is a pure writer** -- when recording candidate fields, `read_note` first and re-pass all existing values. _Source: C-03._
- **C-04 / C-05 - DB integrity** -- reuse existing `documents` / `batches` helpers; the candidate lives in frontmatter only (no DB column, no migration). _Source: C-04/C-05._
- **C-06 - Thresholds in config** -- route via `CONFIG.thresholds.for_pipeline("classify").route(confidence)`; no inline `0.85` / `0.60` in the pipeline. _Source: C-06; hook hard-block on float literals in `pipelines/` if/elif._
- **C-07 - Prompts are YAML** -- the unified prompt stays in `prompts/classify.yaml`; the two input shapes are solved by the Subject Builder feeding one `subject` var, not by Python string building or `{% if %}` branches in the prompt. _Source: C-07._
- **C-08 - Provider factory** -- `classify()` already uses `get_provider("classify", config)`; keep. _Source: C-08._
- **C-12 - Result returns** -- the new Classify Step and any new helpers in `pipelines/` return `Success`/`Failure`. _Source: C-12._
- **C-13 - Audit non-negotiable** -- every AUTO/SUGGEST/CLUELESS calls `audit.write(... pipeline="capture", stage="classify", source_ids=[vault_path])`. _Source: C-13._
- **C-17 - No module-scope CONFIG in tests** -- Classify-Step tests pass an explicit `PipelineContext`/config; no top-level `from core.config import CONFIG`. _Source: C-17; hook block._

---

## Assumptions

Each is a falsifiable claim about existing code. `/research` verifies every one.

| ID | Assumption | Source implication | What would prove it wrong |
|----|-----------|-------------------|--------------------------|
| A1 | The single-file pipeline stage list is `[extract, enrich_urls, summarize, metadata, apply_location_tags, store]` and a new stage can be inserted after `apply_location_tags`, before `store`, using the same `(value, ctx) -> Result` contract. | Design impl. "Loose inbox drops get a home" | The stage list is different, or stages cannot be inserted without changing the value/return contract. |
| A2 | `classify()` takes `(title, summary, tags, valid_destinations, config)` today and returns `Success(ClassifyResult)` with `target_type`/`target_name`/`confidence`/`reasoning`; all failures `recoverable=True` except render. No production caller exists. | Design impl. "classify engine result shape changes" | `classify()` has a different signature or already has a production caller. |
| A3 | `_location_context(path, vault_cfg)` returns a `(type, name)` tuple where `type` is one of `"project"`, `"domain"`, `"inbox"`, or `None`. | Design impl. "files already filed are never re-classified" | It returns a single string, or uses different type labels. |
| A4 | `format_for_prompt(ProjectRegistry(groups=live.get_groups()))` produces the AI destination menu; `LiveRegistry.get_groups()` returns a thread-safe copy. | Design impl. "loose inbox drops get a home" | The registry is not reachable from the pipeline, or `format_for_prompt` needs more context. |
| A5 | `CONFIG.thresholds.for_pipeline("classify")` falls back to the `global` band (auto 0.85 / suggest 0.60) because `pipelines: {}` is empty; `.route(score)` returns AUTO/SUGGEST/CLUELESS. | Design OQ-CIC-2 | A `pipelines.classify` entry already exists, or `for_pipeline` does not fall back. |
| A6 | ~~The binary LOCATED branch in `_store_nonmd` can be driven by passing an externally-derived destination.~~ **INVALIDATED (type-b).** `_store_nonmd` hard-derives `target_type`/`target_name` from the source path as local variables — no parameter accepts an external destination. **Fix:** add optional `target_type: str | None = None` and `target_name: str | None = None` parameters; when None, existing derivation runs; when supplied, use the provided values. LOCATED branch internals (sibling-first write, move_guard, move_attachment, upsert) are fully reusable. | Design R2 | The destination is hard-derived from the source path in a way that cannot be overridden from outside. **Confirmed: this IS the case — refactor required.** |
| A7 | The `.md` rename/move path (`_store_md`, lines ~495-541) uses `move_note` + `documents.replace_path` with rollback, leaving no orphan row at the old path. | Design R2 | `replace_path` leaves the old row, or there is no atomic swap. |
| A8 | `move_note(src, dst, actor="ai")` returns `Failure(recoverable=False)` when the note has `updated_by_human=true`. | Design R5 | The gate returns a different recoverability, or `move_note` does not check the flag. |
| A9 | Registering the destination via `get_active().register(dst)` before a binary move makes the watcher's cross-folder re-home return early, preventing redundant re-processing -- and the binary g2 batch-stamp runs only AFTER the guard check. | Design R1 | The guard check is not first, or the batch-stamp runs before it. |
| A10 | An AUTO move to a project/domain ROOT leaves `batch_id` NULL because `is_batch_subfolder` is False for roots, and the batch-stamp pre-step keys off the inbox parent before the move. | Design R3 | `is_batch_subfolder` returns True for roots, or the pre-step keys off the destination. |
| A11 | The idempotency guards (lines 877-940) run before the pipeline and short-circuit an unchanged re-run; the "pending-routing" early-exit (lines 862-872) is the only thing that special-cases an already-parked binary. | Design R4 | Another early-exit re-classifies, or the guards run after the pipeline. |
| A12 | `capture_folder` writes `batches.folder_path` WITHOUT NFC normalization while `capture_file`'s batch-stamp lookup normalizes NFC -- so non-ASCII decomposed folder names mismatch. | Design R6 / TD-049 | Both paths already normalize identically. |
| A13 | `PipelineContext` is a mutable `@dataclass` that can gain a new optional field without breaking existing callers (all construct it by keyword). | SUPPRESS (Decision 9) | A field cannot be added without touching every caller, or PipelineContext is frozen. |
| A14 | `NoteMetadata` accepts new typed optional fields and `_KNOWN_KEYS` controls what survives a round-trip through `parse`/`dumps`; adding fields needs both updated. **Additionally**, any new string field must be added to the `_coerce_bool_to_str` validator decorator (handles PyYAML 1.1 `yes`/`no`→bool coercion). Already covered in Component 2 build steps. | OQ-CIC-1 | A new field round-trips without being in `_KNOWN_KEYS`, or `dumps` drops typed fields. |
| A15 | `MetadataResult` carries `ai_domain: str | None`, `ai_tags: list[str]`, and `ai_project: str | None`; the Classify Step runs after `apply_location_tags`, so for inbox drops `ai_tags` may contain domain tags from the metadata stage and `ai_project` is None. | Design W1 | `MetadataResult` does not carry these fields, or `apply_location_tags` runs after the classify slot. |

---

## Component dependency order

_Documents what must exist before each component works -- NOT the order to write code. Execution order is `/plan-from-specs`'s job._

### 1. [P1] Subject Builder -- normalize a note into one classify input block

**Goal.** A tiny helper that turns a note's title, summary, and tags into one short `subject` text block, so the AI prompt only ever sees one shape regardless of source (Option U2).

**Build.** A small pure helper (in `pipelines/capture.py` or a thin sibling module -- planner decides) that takes the note's title, summary, and tags and renders a "Title / Summary / Tags" block the prompt expects. It is the single place the note shape converges; in Phase 2 the folder shape converges here too. Returns a plain string.

**Depends on.** None.

**Assumes.** A2.

**Interface shape.** Callers pass note fields (title, summary, tags), get back a `subject` string. Hidden: the exact template text. New seam: 1 adapter in Phase 1 (note caller); 2nd adapter (folder caller) lands in Phase 2 -- so the seam earns its keep then. Flag for the planner: if Phase 2 is deferred indefinitely, this is a speculative 1-adapter seam -- keep it inline until the folder caller exists.

**Done when.** A loose inbox note produces a `subject` string containing its title, summary, and tags, and that string is what gets sent to the AI (verifiable by capturing the rendered prompt in a test).

---

### 2. [P1] Candidate frontmatter fields -- typed places to record the AI's guess

**Goal.** Give SUGGEST/CLUELESS a clear, typed home in the note's own frontmatter so a human or a later phase can read "the AI suggested project X / domain Y at confidence Z because W" (resolves OQ-CIC-1; locked to typed fields under the derive-from-tags model, no DB migration).

**Build.** Add four typed optional fields to `NoteMetadata` (`vault/frontmatter.py`) and add their keys to `_KNOWN_KEYS`:
- `suggested_project: str | None` -- the AI's guessed project (or None if no project suggested).
- `suggested_primary_domain: str | None` -- the AI's guessed primary domain (or None if AI was stuck).
- `classify_confidence: float | None` (0.0-1.0) -- the AI's confidence.
- `classify_reasoning: str | None` -- the AI's one-sentence reason.

Plus reuse the existing `status` field set to `"needs-review"` on SUGGEST/CLUELESS. Add the four string/float fields to the `_coerce_bool_to_str` validator list as appropriate (the str ones only -- `suggested_project`, `suggested_primary_domain`, `classify_reasoning`). Do NOT add a DB column.

**Depends on.** None.

**Assumes.** A14.

**Decisions.**
- Q: Should CLUELESS (no candidate) leave `suggested_project`/`suggested_primary_domain` as `None` and still set `status: needs-review` + `classify_reasoning`? Options: A) yes, null suggested + reasoning explains "stuck"; B) a separate `status` value like `needs-review-stuck`. Leaning **A** because the design treats SUGGEST and CLUELESS as the same "record + leave" outcome differing only by whether a candidate exists; finalize the exact `status` vocabulary in research (it intersects the deferred status CHECK constraint from Phase Pre-2).

**Done when.** After capturing a medium-confidence loose note, opening it in Obsidian shows `suggested_project`, `suggested_primary_domain`, `classify_confidence`, `classify_reasoning`, and `status: needs-review` in its frontmatter; reading it back via `read_note` and writing it again preserves all four fields (round-trip through `_KNOWN_KEYS`).

---

### 3. [P1] SUPPRESS signal -- let the folder path turn off the per-file Classify Step

**Goal.** When the Capture Pipeline is invoked file-by-file by the Folder Capture flow, the new Classify Step must skip itself -- the folder is the routing unit (locked Decision 9 / SUPPRESS). Must ship the moment the Classify Step is added, because the existing CLUELESS folder loop already calls `capture_file`.

**Build.** Add one optional boolean signal to `PipelineContext` (`core/pipeline.py`), e.g. `skip_classify: bool = False`. The Folder Capture flow (`_capture_folder_files`, and the CLUELESS loop in `capture_folder`) builds its per-file context with this set True. The Classify Step reads it and passes the note through untouched when set. All existing single-file callers (CLI, scan loose files, watcher single drop) leave it False.

**Depends on.** None (but the Classify Step in component 4 must read it).

**Assumes.** A13.

**Done when.** A folder dropped in inbox that the folder flow routes (or marks CLUELESS) has each of its files summarized + frontmatter-stamped + batch-stamped, but NONE of them carry `suggested_*`/`needs-review` from an individual classify call, and no per-file classify audit row appears for them.

---

### 4. [P1] Classify Step -- the new pipeline stage (gated, with derive-from-tags routing)

**Goal.** The one new stage that does the work: for a loose inbox file, ask the AI to assign the note's project + designate a primary domain consistent with its existing domain tags; derive the destination folder from those assignments; route the answer through the Confidence Gate. For a located file (or under SUPPRESS), pass through untouched.

**Build.** Add a `classify` stage to the pipeline stage list in `capture_file`, inserted after `apply_location_tags` and before `store`. It takes the `MetadataResult` and `ctx`, returns `Result[MetadataResult]` (it must hand off to `store`, so it threads the AI's choice forward). Behavior:
1. If `ctx.skip_classify` is True -> return the input untouched (SUPPRESS).
2. Ask the Folder Detector (`_location_context`). If project/domain -> return untouched (LOCATED -- `store` files it as today).
3. If inbox/none -> build `valid_destinations` from the live Destinations List; build the `subject` (component 1) from the `MetadataResult`'s title/summary/tags; call `classify()` (reshaped, component 6); route `confidence` via `CONFIG.thresholds.for_pipeline("classify").route(...)`.
   - **AUTO** -> derive the destination folder: project present -> `Projects/<project>/`, else -> `Domain/<primary>/`. Set `ai_project` on the `MetadataResult` if the AI assigned one; ensure domain tags on `ai_tags` are consistent. Let `store`/`_store_nonmd` do the move + write + index into the derived folder (the AUTO move-handoff is component 5).
   - **SUGGEST / CLUELESS** -> record the candidate fields (component 2) via read-before-write, write the audit row, leave the file in inbox; signal `store` not to move it (it still writes/upserts in place).
   - **No project AND no domain from AI** -> CLUELESS regardless of confidence (locked OQ-CIC-4).
4. Every branch that called the AI writes exactly one audit row (`stage="classify"`, outcome AUTO/SUGGEST/CLUELESS).
5. On `classify()` `Failure(recoverable=True)` -> a bounded retry loop (TD-048) then fall back to the CLUELESS-style recorded-candidate outcome when exhausted.

**Depends on.** Components 1 (Subject Builder), 2 (candidate fields), 3 (SUPPRESS signal). Reaches into 5 (AUTO handoff) and 6 (engine reshape).

**Assumes.** A1, A2, A3, A4, A5, A15.

**Dependency category.** in-process -- test directly with a stubbed `classify()`/provider via an explicit `PipelineContext` (C-17).

**Decisions.**
- Q: Where does the bounded retry loop for `classify()` `recoverable=True` live (TD-048 requires one)? Options: A) inside the Classify Step; B) a thin retry wrapper helper reused by both callers. Leaning **A** for Phase 1 (one caller) and promote to **B** in Phase 2 when the folder caller joins. Confirm max-attempts + backoff numbers in research; on exhaustion, fall back to recorded-candidate (CLUELESS-style), never fail the whole capture.
- Q: How does the Classify Step tell `store` "don't move this" for SUGGEST/CLUELESS without `store` re-deriving a destination? Options: A) leave `ai_project`/derive-target unset so `store`/`_store_nonmd` takes its existing inbox/CLUELESS branch; B) a new "terminal" flag. Leaning **A** -- it matches how the binary path already routes CLUELESS by absence of a target. Verify the `.md` in-place write path does the same.
- Q: Does the AUTO move need to stamp destination tags (a `domain/<D>` tag or `project:` field) to match `apply_location_tags`' located behavior (TD-019)? The derive-from-tags model means the AI already assigned them, but `apply_location_tags` only runs based on location BEFORE the move. After the move, location would produce the right tags on a re-capture, but the first capture should stamp them directly. Confirm in research.

**Done when.** (P2-CIC-01) A loose inbox note with a clear project destination is GONE from inbox and lands in the project folder with `project:` field and domain tags intact, and NO `needs-review`. (P2-CIC-02) A medium-confidence note STAYS in inbox with `suggested_project` / `suggested_primary_domain` / `classify_confidence` / `classify_reasoning` / `needs-review`. (P2-CIC-03) A no-match note STAYS in inbox marked stuck. (P2-CIC-04) A note dropped directly into `Projects/Alpha/` stays there with `project: Alpha` and NO `suggested_*`/`needs-review`. (P2-CIC-11) Project beats domain -- a note with both project and domain tags moves to the project folder. (P2-CIC-12) A note with multiple domains and no project moves to the primary domain folder while keeping all domain tags.

---

### 5. [P1] AUTO move handoff -- route the derived destination through the existing Filer/Mover

**Goal.** On AUTO, move the file to the folder derived from the AI's assigned tags/project and keep disk + index in agreement, reusing the existing filing code (Option A -- do NOT re-implement the move, that was rejected Option B).

**Build.** Thread the AI-derived destination into the existing store handoff. Two refactors required (confirmed by research — A6 invalidated, `_store_md` cross-folder gap surfaced):

- **`.md` AUTO move (cross-folder):** `_store_md` only renames within the source directory — it cannot do cross-folder moves. The Classify Step performs the cross-folder move itself: register destination with Move Guard, call `move_note(src, dst, actor="ai")` + `documents.replace_path(old, new)` (atomic swap, no orphan). Then update the pipeline's source reference to the new location (via `dataclasses.replace` on `MetadataResult` since it is frozen) and let `store` handle the in-place write + `documents.upsert` at the new path. This reuses the existing move chokepoints without modifying `_store_md`.
- **Binary AUTO move (refactor `_store_nonmd`):** `_store_nonmd` currently hard-derives the destination from the source path as local variables — it has no parameter for an external destination. **Refactor:** add optional `target_type: str | None = None` and `target_name: str | None = None` parameters to `_store_nonmd`. When None → existing path-derivation runs (all current callers unaffected). When supplied → uses the provided values. The LOCATED branch internals (sibling-first `write_note`, `move_guard.register(dst)`, `move_attachment`, `documents.upsert` with `attachment_path` re-pointed) are fully reusable as-is. Destination: project present → `vault_cfg.projects_path / <project>`; else → `vault_cfg.domain_path / <primary>`.
- **Human-locked (R5):** if `move_note` returns `Failure(recoverable=False)` (note has `updated_by_human=true`), do NOT fail the capture -- fall back to the recorded-candidate outcome (component 2) + a SUGGEST-style audit row, leave the file in place.

**Depends on.** Component 4 (it supplies the derived destination). Reuses the existing Binary Filer / Note Filer / Move Guard.

**Assumes.** A6, A7, A8, A9, A10.

**Done when.** (P2-CIC-07) After an AUTO move to a project ROOT: `documents.vault_path` equals the new in-folder path (matches disk), `batch_id` is NULL, and exactly ONE documents row exists (no orphan at the old inbox path). (P2-CIC-05) A loose PDF that AUTO-routes has its binary moved to the derived folder's `attachment/` with the summary card alongside, both findable. (R5) Capturing a human-locked loose note does not error -- it leaves the note in place with recorded candidate fields. (P2-CIC-13) The on-disk folder matches the stamped project/primary-domain.

---

### 6. [P1] Classify Engine reshape + unified prompt -- subject-shaped input, derive-from-tags output

> **Note (from research):** P2-CL is already implemented on `main` (commits `b2d33fa` + `a28b33c`). The `classify()` function and `ClassifyResult` dataclass exist in `pipelines/classify.py`. This component is a **modification** of existing code, not a greenfield build. P2-CL-01..06 unit tests exist and must be updated to the new shape.

**Goal.** Reshape the Classify Engine so it takes one flat `subject` block instead of separate title/summary/tags (Option U2), and return the new derive-from-tags fields instead of a free `target_type/target_name`. This is the engine all callers share.

**Build.**
- **`ClassifyResult`**: Replace `target_type: str` and `target_name: str` with:
  - `project: str | None` -- the AI's assigned project (or None if domain-only).
  - `domains: list[str]` -- the domain tag(s) the AI confirms/reuses from the metadata stage.
  - `primary_domain: str | None` -- the one "home" domain for routing (required when there is no project and there are domains; None only when the AI has no domains at all).
  - Keep `confidence: float` and `reasoning: str`.

- **`classify()` signature**: Change from `(title, summary, tags, valid_destinations, config)` to `(subject, valid_destinations, config)`. The `subject` is a pre-built text block from the Subject Builder. Render: `PROMPTS["classify"].render(subject=..., valid_destinations=...)`.

- **`prompts/classify.yaml`**: Replace the `title`/`summary`/`tags` user-template variables with a single `subject` variable (+ keep `valid_destinations`). No `{% if %}` branches (C-07). Change the output JSON contract to return `project`, `domains`, `primary_domain`, `confidence`, `reasoning`. Add an instruction that the AI should pick the project and designate a primary domain *consistent with the provided tags*, not invent new tags (W1).

- **Validation**: `target_type in {"project","domain"}` check becomes: if `project` is set, verify it appears in `valid_destinations`; if `primary_domain` is set, verify it appears in `valid_destinations`; if neither, treat as CLUELESS. All failures remain `recoverable=True`.

- **P2-CL unit tests**: Update P2-CL-01..06 in `tests/test_pipelines/test_classify.py` to the new signature and result shape -- they are unit guardrails, acceptable to change here (the 956 folder tests are the real guardrail and are untouched in Phase 1).

**Depends on.** Component 1 (Subject Builder produces the `subject`).

**Assumes.** A2.

**Interface shape.** `classify(subject, valid_destinations, config)` -> `Result[ClassifyResult]`. Callers see a `subject` string; the prompt template is hidden. This is the seam that gains its 2nd adapter in Phase 2 when the folder caller builds its own `subject`.

**Done when.** Capturing a loose inbox note still classifies correctly through `classify()` now driven by a `subject` string; the rendered prompt contains the note's title/summary/tags inside one `subject` block and the destination menu; the AI returns the new fields; P2-CL unit tests pass against the new signature and result shape.

---

### 7. [P1] Idempotent re-entry -- retire "pending-routing", don't re-classify recorded files

**Goal.** Replace the retiring inbox "pending-routing" early-exit so a re-run does not re-classify a file that already has a recorded SUGGEST/CLUELESS candidate, while keeping the content-hash / source_hash short-circuits intact (R4).

**Build.**
- Remove (or repurpose) the `pending-routing` early-exit at `capture.py:862-872` now that loose binaries are classified at drop time rather than parked.
- Ensure a loose file already carrying `status: needs-review` + `suggested_*` (recorded SUGGEST/CLUELESS) is not re-classified on a second capture of unchanged content -- the existing `.md` content-hash and binary `source_hash` guards should already short-circuit it; confirm they fire for a needs-review note/sibling.
- The CLUELESS marker body that `_store_nonmd` writes (the one-line placeholder) is superseded -- the inbox branch now writes a real summary + recorded candidate instead.

**Depends on.** Components 2 (candidate fields), 4 (Classify Step), 5 (binary handoff).

**Assumes.** A11.

**Done when.** (P2-CIC-08) Under `kms watch`, dropping a loose file that AUTO-routes produces exactly one documents row at the destination, the watcher re-home is suppressed (log shows the pipeline-initiated skip), and no second move back to inbox occurs. Re-running `kms capture` on an unchanged needs-review note does NOT issue a second classify AI call (verifiable by audit-row count staying at 1).

---

### 8. [P1] TD-049 -- normalize `folder_path` identically on both batch paths

**Goal.** Make the folder-batch write path and the single-file batch-lookup path produce the same `folder_path` string for non-ASCII folder names, so the SUPPRESS case's files reach the folder's batch row instead of spawning a duplicate batch (R6 / TD-049).

**Build.** NFC-normalize `folder_path` on BOTH:
- the write path -- the `_insert_batch(... folder_path=...)` call sites in `capture_folder`, which currently pass `str(... .as_posix())` with no normalization;
- the read path -- `capture_file`'s batch-stamp lookup, which already normalizes NFC.

Ideally via one shared helper (e.g. a `to_folder_path(path, root)` in `vault/paths.py`) so the two can never drift again.

**Depends on.** None (independent latent-bug fix; folded in because SUPPRESS relies on the folder `batch_id` reaching each file).

**Assumes.** A12.

**Decisions.**
- Q: Is a shared helper worth adding vs inlining `unicodedata.normalize("NFC", ...)` at all call sites? Leaning **shared helper** so drift is structurally impossible; confirm there is no other `folder_path` producer in research.

**Done when.** Dropping a folder whose name contains a decomposed non-ASCII character (e.g. an accented Vietnamese folder name) creates exactly ONE `batches` row, and every file inside it carries that same `batch_id` (no second batch row, no mismatched `batch_id`).

---

### 9. [P2] Migrate the Folder Capture flow onto the unified prompt + engine

**Goal.** Make the whole-folder path build a `subject` and reuse the same `classify()` engine + unified `classify.yaml`, then delete the separate folder prompt -- one prompt, one engine (Option U2, prompt-unification Phase 2). Hold all 956 folder tests green.

**Build.**
- In `capture_folder`, replace the `classify_folder` prompt call with: build a folder `subject` (folder name + file manifest) via the Subject Builder (now its 2nd adapter), call `classify(subject, valid_destinations, config)`.
- The folder caller reads the reshaped `ClassifyResult` and maps `project` -> `Projects/<project>/` or `primary_domain` -> `Domain/<primary>/` via the same derivation rule (this is the folder caller's equivalent of `_folder_destination`).
- Delete `prompts/classify_folder.yaml` and the now-unused `_parse_classify_json` / `_build_vault_context` if nothing else uses them (confirm in research).
- Keep the folder routing, batch rows, and audit wording unchanged; only the AI-call mechanism changes.

**Depends on.** Components 1, 6 (the Subject Builder and the subject-shaped engine must already exist from Phase 1).

**Assumes.** A2, A4.

**Decisions.**
- Q: The folder `subject` loses the dedicated "folder name + manifest + vault_context" framing -- does classification quality dip? The 956 folder tests are the guardrail.
- Q: Does the folder caller need `reasoning` (the folder prompt didn't return it)? Leaning: accept and log `reasoning` on the folder path. Confirm against the folder tests in research.

**Done when.** All 956 folder tests pass with `classify_folder.yaml` deleted and the folder flow calling the same `classify()` engine; a folder dropped in inbox still classifies and routes exactly as before.

---

## Handoff notes

- **Contract with the Folder Capture flow (Phase 1 <-> Phase 2):** Phase 1 must ship the SUPPRESS signal (component 3) so the existing folder CLUELESS loop does not start scattering individual files the moment the Classify Step exists. The Subject Builder (component 1) and the subject-shaped engine (component 6) are built in Phase 1 so Phase 2 can reuse them -- do not defer them.

- **Contract with the Project Registry (P2-REG):** This feature assumes the Project Registry is built and a `LiveRegistry` is reachable from the pipeline/watcher (STATE.md lists it as PENDING implementation). **The single biggest implementation dependency.** If the registry is not yet wired, the Classify Step cannot build `valid_destinations`. Confirm how a `PipelineContext`-driven `capture_file` gets the live registry in `/research`. Surfaced as OQ-CIC-A below.

- **Contract with P2-CL (Classify Engine):** P2-CL's plan is written but PENDING `/tdd-implement` (STATE.md). This feature reshapes `classify()`'s signature in component 6 -- coordinate so the reshape lands as part of P2-CL implementation or immediately after, not as a conflicting parallel edit. If P2-CL is implemented first with the old signature, component 6 changes it; if this feature is implemented first, P2-CL's plan must be adjusted. TD-048 (retry loop) is explicitly this feature's responsibility (component 4).

- **Open uncertainty -- status vocabulary:** `status: needs-review` intersects the deferred "status vocabulary CHECK constraint" noted in Phase Pre-2 (STATE.md). No DB constraint exists yet, so frontmatter is free-form -- but pick the value deliberately so a future CHECK constraint won't reject it.

- **Suggested research order:** (1) Verify the Project Registry wiring into capture -- how does the Classify Step get the live registry? (biggest unknown). (2) Verify `_store_nonmd` LOCATED branch can be driven by an externally-derived destination (A6) -- the AUTO binary path depends on it. (3) Verify the idempotency guards fire for a needs-review note (A11). (4) Confirm the `folder_path` producers for TD-049 (A12). (5) Verify whether an AUTO-filed note needs destination tags stamped separately (TD-019 intersection).

- **Tech debt touched:** TD-048 (resolved by component 4's retry loop), TD-049 (resolved by component 8). Both already recorded in `TECH_DEBT.md`.

---

## Behavior-inventory entries

Entries P2-CIC-01 through P2-CIC-13 already exist in `docs/system_behavior/behavior_inventory.yaml` from the design step with `origin: design`. They have been updated in this revision to reflect the derive-from-tags routing model:
- P2-CIC-01 now specifies "DERIVED from its assigned tags+project" (not a free pick)
- P2-CIC-11 (new in this revision) tests project-beats-domain precedence
- P2-CIC-12 (new in this revision) tests multi-domain -> primary domain routing
- P2-CIC-13 (new in this revision) tests structural consistency (folder matches frontmatter)

No new entries needed beyond what exists.

---

## Open questions (deferred -- not blockers)

- **OQ-CIC-A -- Project Registry wiring into `capture_file`.** ✅ **RESOLVED.** Add `registry: LiveRegistry | None = field(default=None)` to `PipelineContext`. Watcher passes its live registry; CLI callers pass `build_registry(vault_cfg)` or `None` (classify step falls back to one-shot `build_registry`). Aligns with how `batch_id` is already carried on context.
- **OQ-CIC-B -- retry-loop numbers (TD-048).** ✅ **RESOLVED.** 3 attempts, 1-second base backoff with exponential growth. On exhaustion → CLUELESS-style recorded-candidate with `classify_reasoning: "classify failed after 3 attempts: <last error>"`. Retry loop lives in the Classify Step (component 4), not in `classify()` itself.
- **OQ-CIC-C -- `status` vocabulary.** ✅ **RESOLVED.** Use `"needs-review"` for both SUGGEST and CLUELESS. The absence of `suggested_project`/`suggested_primary_domain` distinguishes CLUELESS from SUGGEST. Existing `"pending-routing"` is retired. No DB CHECK constraint yet (deferred in Phase Pre-2).
- **OQ-CIC-D -- destination tags on AUTO-filed notes (TD-019).** ✅ **RESOLVED.** The Classify Step stamps `ai_project` and ensures domain tags on `ai_tags` in the `MetadataResult` before handing off to `store`. `store` builds `NoteMetadata` from these fields. After the move, the file has correct project/domain tags matching its new location. No separate "destination tag stamping" step needed — the classify step IS the stamp.
- **OQ-CIC-E -- `_store_nonmd` LOCATED branch external destination override.** ✅ **RESOLVED.** Confirmed A6 invalidated — refactor needed. Add optional `target_type`/`target_name` params to `_store_nonmd`. See Component 5 build steps for details.
