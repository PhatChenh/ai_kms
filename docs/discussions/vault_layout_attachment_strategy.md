# Discussion: Vault Layout — Attachment Sibling Strategy & Per-Domain Structure

_Date: 2026-05-22_
_Status: Reached conclusion; no plan written yet._

---

## Design changes to how we handle non binary attachment and their sibling

Im afraid that creating sibling .md file for each non-binary file will cause the trouble of overflooding the vault with so many empty notes (almost empty, except a link to the material). Also, having a separate global `attachment` folder might also not work and need rethinking
My current solution is as follow:
- We delete the global `attachment` folder, and replace its function by having an attachment folder in each Projects' folder. This would fit better to the way my boss navigate the vault, as she expect all related files of a project should be in one place. 
- Next, all the non-binary files of a project will be collected and put into the project's attachment. If the attachment get dropped directly into a project folder (for example, Project/<project A>), then it is clear that it should belong to <project A>, it should be moved into `attachment` folder inside <project A>/ folder. If in the case it was not dropped in places where its belonging could be inferred right away, then AI would review and decide which place it should be (just like with .md file). For non md files that are dropped directly into an attachment folder inside a project, no file moving needed, except a periodic (or human mannually trigger) review of the material to see if their content fit their location (this feature will be deferred until we build a scheduler)
- For sibling files, they are still needed, but now, they will all be collected inside a `.summaries/` subfolder to the `attachment/` folder. The dot-prefix is meant to hide the folder from both the normal user when navigating the vault, and Obsidian, so it would not flood the file system. The only role of a sibling file is to provide metadata and summaries about the attachment, and allow indexer to index the attachment into `documents` table in kb.db. `vault/indexer.py` will still only need to index md file and skip the non md ones, and must traverse the `.summaries` folder. A key decision is: when indexing the sibling md, the sibling is only meant to be proxy to the actual attachment, then the `vault_path` should be the path of the sibling or the path of the attachment (which could be easily derived by moving one level up from the sibling's path)? whichever chosen approach will need to be the best approach to support the function of searching and retrieving the relevant information for AI when working/chatting with human.
- Clean up and sync mechanics also needed between the non-binary files and their siblings md files. If an attachment is deleted or moved, then so does the sibling to be deleted or moved into the corresponding .summaries folder. If a non binary is updated, then the sibling's metadata also need to be updated (especially the summary of the attachment's content)
Other design changes that are not directly related to the above topic, but is thought out during the thinking process:
- Similar to Project, there should be per-Domain `attachment/` folders for each specific domain.
- The global Archive folder is also removed, and replace by per-domain `Archive` folders containing inactive Projects. This decision also enforce that every archived Project lives under exactly one Domain (or `Uncategorized` domain) - I repeat: this enforcement is only applied to archived projects, active projects live in another place and need no Domain. The archiving logic - when and where - will be handled manually by user for now (use decide when a project is done and drop that project to the right archive folder)
- Clarification about CLAUDE.md: I did say that this file is human facing index, but I did not mean that the file will be of db type like `documents` table in `data/kb.db`. Instead, it is a text md file containing project/domain essential context and a general index of the file structure (written in plain text like a ASCII diagram).
Here is the visualization of the structure per Project (and analogously per Domain)

```
Projects/<A>/
├── CLAUDE.md                    ← human-facing context & index
├── <user notes>.md
└── attachment/
    ├── report.pdf
    ├── deck.docx
    └── .summaries/              ← hidden from Obsidian, per-attachment sibling .md
        ├── report.md
        └── deck.md
```

---

# Research Briefs — split into three independent passes (2026-05-23)

User instruction: do NOT research now. Write three research briefs so cold-context AI sessions can carry each out independently. `vault_path` decision (sibling vs attachment) is deferred to plan stage — research surfaces trade-offs only. CLAUDE.md index writer is out of scope (TD-015).

## Why split

Touch surface spans: vault layout primitives + writer/indexer/paths + capture pipeline non-md branch + watcher/sync + Archive layout + Domain/Uncategorized routing. One pass risks surface-cataloging one area. Three briefs let each session deep-trace its slice cold.

## Authoritative design summary (input to all three briefs)

1. Delete global `Vault/attachment/`. Replace with per-`Projects/<A>/attachment/` and per-`Domain/<D>/attachment/`.
2. Non-md routing at capture time:
   - Drop inside `Projects/<A>/attachment/` → no move; sibling created at `.summaries/<x>.md`.
   - Drop in `Projects/<A>/` (loose) → move to `Projects/<A>/attachment/`; sibling in `.summaries/`.
   - Drop in `inbox/` or ambiguous → AI decides target Project/Domain; move + sibling.
   - Periodic fit-review for files in `attachment/` deferred until scheduler exists.
3. Siblings in `.summaries/` (dot-prefix hides from Obsidian + user). Sibling = metadata + summary proxy for indexer.
4. Indexer still indexes only `.md`, but must traverse `.summaries/` (today `scan_vault` prunes any dir starting with `.` — see `vault/indexer.py` l. 142–148 + l. 91–97). One allowlist exception needed.
5. Sync mechanics: attachment delete → sibling delete; attachment move → sibling move; attachment content update → sibling re-summarize (or stale-mark).
6. Per-Domain `Archive/` replaces global `Vault/Archive/`. Archived projects live under exactly one Domain (or `Uncategorized`).
7. `Domain/Uncategorized/` only for archived projects with no domain fit. Active projects without a domain remain in `Projects/` and need no domain.
8. CLAUDE.md is plain prose md, not DB — **out of scope** here.
9. Open: `vault_path` for sibling = sibling path or attachment path? Surface trade-offs, defer.

---

## Research Brief #1 — `attachment_layout`

**Slug:** `attachment_layout`
**Run:** `/research_v3 attachment_layout`

### Goal
Understand vault layout primitives (paths, `move_attachment`, indexer scan loops, `documents` table) deeply enough for a planner to spec: (a) per-Project/Domain attachment layout, (b) `.summaries/` traversal, (c) revised `move_attachment` use, (d) `vault_path` sibling-vs-attachment trade-offs.

### Scope IN
- `vault/writer.py::move_attachment` (l. 239+) + callers' expectations.
- `vault/indexer.py::scan_vault` (l. 119+) + `scan_non_md_drops` (l. 74+) — dotfolder pruning, `IGNORE_DIRS`, attachment-subtree skip.
- `vault/paths.py` — helpers; what per-Project/Domain helpers may need adding.
- `core/config.py::VaultConfig::attachment_path` (l. 100) + `config/config.yaml` vault block.
- `storage/schema.sql` + `documents` table — `vault_path` UNIQUE column (DECISION-001).
- DECISION-017 (NFC), DECISION-018 (indexer skips non-md).

### Scope OUT
- Capture pipeline branching (Brief #2).
- Sync mechanics (Brief #3).
- Archive/Uncategorized (Brief #3).
- CLAUDE.md writer.
- Classify pipeline (Phase 2).

### Files to investigate
**Deep trace:**
- `vault/writer.py` — full file (move_attachment, write_note, `updated_by_human` gate).
- `vault/indexer.py` — full file (dotfolder pruning, NFC, scan_non_md_drops skip).
- `vault/paths.py` — full file.
- `core/config.py` VaultConfig block.
- `storage/db.py::documents.upsert` + `storage/schema.sql`.

**Surface catalog:**
- `pipelines/capture.py` non-md branch (l. 437–495, l. 623–631) — context only; do NOT design replacement.
- `vault/reader.py` — confirm reads any `.md` regardless of path.
- `vault/frontmatter.py` — can a new `attachment_path` frontmatter field be added without schema churn?

**Skip (with reason):**
- `handlers/` — extension dispatch unchanged; no layout coupling.
- `llm/` — orthogonal.
- `retrieval/` — Phase 3, not built; flag forward impact only.

### Specific questions
1. Does `Path.walk` re-enter pruned dirnames after `dirnames[:] = ...`? Confirm semantics so `.summaries/` allowlist (`d != ".summaries"`) is implementable.
2. Where is the "skip dotfolders" rule and what's the minimum-touch change to allowlist `.summaries/`?
3. `move_attachment(src, dst)` — does the per-project target require a signature change, or only caller-side `dst` computation? (Caller in `capture.py:456` builds dst from `attachment_dir`; trace through.)
4. `core/config.py::attachment_path @property` — if global attachment is deleted, what references it? Grep capture.py, tests. Does it stay as fallback or get removed?
5. `documents.vault_path` retrieval implications. Surface trade-offs of:
   - vault_path = `.summaries/report.md` (sibling) — search hit opens summary; embedding sourced from summary; attachment renamed independently breaks proxy unless `attachment_path` frontmatter pointer added.
   - vault_path = `report.pdf` (attachment) — search hit opens binary; embedding still sourced from sibling content; FK stable only if attachment never renamed; conflicts with DECISION-018 (indexer indexes .md, so `vault_path` pointing to `.pdf` is incoherent on a `documents` row produced by markdown indexer).
   - Hybrid: `vault_path = sibling` + frontmatter `attachment_path = "../report.pdf"` (explicit pointer, rename-survivable if updated together).
   Surface all three with reasoning; defer pick.
6. Obsidian wikilink behavior inside hidden `.summaries/` (link `../report.pdf`) — note any docs/clues; do not test live.
7. NFC normalization — `.summaries/` is ASCII safe; Vietnamese folder names in `Projects/<A>` flow unchanged?

### Open questions to surface
- OQ-AL1: `vault_path` sibling vs attachment vs hybrid (Q5).
- OQ-AL2: Per-Project/Domain `attachment_subdir` as config constant vs hardcoded convention.
- OQ-AL3: Same-named PDF in two projects — global namespace gone; confirm `documents.vault_path` UNIQUE still works (full path differs); flag any code assuming global attachment-basename uniqueness.

### Constraints
- DECISIONS 001, 017, 018.
- Hook: `.write_text()`/`open(...,'w')` only in `vault/writer.py`.
- Extension Point Rule: prefer parameterizing caller's target computation over signature explosion.

### Output
`docs/research/attachment_layout.md` — research_v3 template.

### Cross-refs
- Brief #2 consumes `move_attachment` shape + per-project target convention.
- Brief #3 consumes `vault_path` decision + `.summaries/` traversal change.

### Todo list
1. Load STATE.md (DECISION-001, -017, -018), CLAUDE.md, this discussion file.
2. Read `docs/research/vault_layer.md` + `docs/plans/vault_layer.md`.
3. Deep-trace files above.
4. Surface-catalog as listed.
5. Answer the 7 questions; surface OQs.
6. Write `docs/research/attachment_layout.md`.
7. Self-review (research_v3 Step 6).

---

## Research Brief #2 — `attachment_capture_pipeline`

**Slug:** `attachment_capture_pipeline`
**Run:** `/research_v3 attachment_capture_pipeline`

### Goal
Understand current capture pipeline non-md branch deeply enough for a planner to spec the revised branching: drop location → target Project/Domain → move + sibling creation in `.summaries/`. Establish where the AI "decide target" step plugs in without coupling to Phase 2 classify.

### Scope IN
- `pipelines/capture.py` non-md branch (`_handle_non_md` ~l. 437; `scan_capture` ~l. 623–631) + all 5 stages.
- `handlers/base.py`, `handlers/registry.py`, `handlers/{pdf,docx,xlsx}_handler.py` — `RawContent` shape.
- `prompts/summarize.yaml`, `prompts/extract_metadata.yaml`.
- `core/audit.py` — audit entries for the new routing decision.
- `core/tags.py`, `core/confidence.py` + `config/thresholds.yaml` — confidence gating for the routing decision.
- `vault/writer.py` usage (consume Brief #1's output; do not redesign).

### Scope OUT
- Layout primitives (Brief #1).
- Sync on later edits (Brief #3).
- Archive routing (Brief #3 unless surfaced at capture-time; flag cross-cut).
- Phase 2 classify pipeline itself — flag overlap, do not design.
- CLAUDE.md index updates after capture (TD-015).

### Files to investigate
**Deep trace:**
- `pipelines/capture.py` — full file (especially `_handle_non_md` and `scan_capture` non-md loop).
- `handlers/base.py` + `handlers/registry.py`.
- `core/audit.py` + `storage/audit_log.py` shape.
- `prompts/extract_metadata.yaml` — does it emit project/domain hints today?

**Surface catalog:**
- `handlers/pdf_handler.py`, `docx_handler.py`, `xlsx_handler.py` — confirm destination-agnostic.
- `core/confidence.py` + `config/thresholds.yaml`.
- `cli/main.py::capture/--scan/watch`.
- `core/pipeline.py::run_pipeline` — can a new stage be inserted?

**Skip:**
- `vault/indexer.py` — Brief #1.
- `retrieval/` — Phase 3.
- LLM provider internals — orthogonal; use `get_provider(task=...)`.

### Specific questions
1. Map every entry point reaching the non-md branch: `inbox/`, watcher, `--scan`.
2. Where does "drop location → target Project/Domain" decision belong? Options:
   - (a) New pipeline stage `route` between `extract` and `summarize` (or between `metadata` and `store`).
   - (b) Inline in `_handle_non_md` — violates Pipeline Pattern; flag.
   - (c) Shared library for capture (Phase 1) + classify (Phase 2).
   Recommend with reasoning; flag (c) for Phase 2 consolidation.
3. Trivial cases: drop already inside `Projects/<A>/attachment/` → no AI call (path infers target). Loose in `Projects/<A>/` → infer A. `inbox/` → AI decides. How distinguish at stage entry?
4. Sibling body shape today: `f"![[{attachment_dst.name}]]"`. Spec from roadmap l. 53–66: link to source + summary frontmatter. Note current vs spec; plan-stage detail.
5. Can `vault/frontmatter.py` accept arbitrary frontmatter keys (e.g. `attachment_path`)? Pydantic strict vs `extra='allow'`.
6. Audit entries: today `CAPTURED` + `TAG_VIOLATION`. New audit type for routing decision — reuse `CLASSIFIED` (collides with Phase 2)? New `ROUTED`? Other?
7. Confidence gate for routing — reuse `config/thresholds.yaml` (≥0.85 auto / 0.60–0.85 review / <0.60 clueless) or new key?
8. Low-confidence non-md in `inbox/` — where does the binary live until human decides? Stay in `inbox/`? Holding area?
9. If `report.pdf` is renamed, the sync mechanism updates `attachment_path` in the sibling's frontmatter, and if it is moved, then should the sibling be moved to corresponding .summaries too
10. Give details breakdown of this issue: **`source_file` overlap**: `NoteMetadata` already has `source_file: str | None`. Today's `_store_md` doesn't set it for non-md; today's `_store_nonmd` writes the binary name as the wikilink in the body, not a frontmatter field (l. 479). The existing `source_file` field could serve as the attachment pointer with no schema change — but its semantics in the existing capture flow ("the original source file before move") and its semantics here ("the attachment this sibling proxies") may overlap or conflict. Worth confirming in Brief #2; flagging here as a free-pre-existing-field that may be the cleanest landing for the pointer.


### Open questions
- OQ-AC1: New `route` stage vs sharing with classify.
- OQ-AC2: Audit type name for routing.
- OQ-AC3: Inbox-pending state for low-confidence non-md.
- OQ-AC4: Sibling body shape (proxy vs summary mirror).

### Constraints
- CLAUDE.md Coding Patterns 1, 2, 3, 5, 6, 7.
- DECISION-019 (validate_tags in pipeline metadata stage).
- DECISION-013/015 (LLM via `get_provider(task, CONFIG.main)`).
- Roadmap: no MCP tools, no Phase 2 classify design.
- Hook: no float literals in `if`/`elif` in `pipelines/`; no logic in `mcp_server/tools.py`.

### Output
`docs/research/attachment_capture_pipeline.md`.

### Cross-refs
- Depends on Brief #1 (`move_attachment` shape + per-project target).
- Brief #3 reuses audit-entry choices from this brief.
- Routing decision overlaps semantically with Phase 2 classify — flag, do not design.

### Todo list
1. Load STATE.md (DECISION-013, -015, -019), CLAUDE.md, roadmap (Phase 1+2), this discussion file.
2. Read `docs/research/capture_pipeline.md` + `docs/plans/capture_pipeline.md`.
3. Read `docs/research/handlers.md` + `docs/plans/handlers.md`.
4. Deep-trace files above.
5. Surface-catalog handlers, confidence, pipeline runner, CLI entries.
6. Answer 8 questions; surface OQs.
7. Write `docs/research/attachment_capture_pipeline.md`.
8. Self-review.

## Brief #2 Extra-requested Feature — Sibling Body Content

_Raised 2026-05-23. Scope: Brief #2 (`attachment_capture_pipeline`). No Brief #1 tasks required — `write_note` already accepts arbitrary body string. Recorded here so Brief #2 prompt carries the intent._

### What the sibling file should contain

Today's capture writes a bare Obsidian wikilink as the sibling body:
```
[[report.pdf]]
```

This is redundant. The user never opens the sibling directly (she opens the PDF). The AI and search system navigate via `vault_path` → open sibling → read it. A bare wikilink tells the AI nothing.

**Target sibling structure (Brief #2 to implement):**

```markdown
---
type: attachment-summary
attachment_path: Projects/Strategy/attachment/report.pdf
summary: "One-sentence teaser: Q1 strategy review, 12 pages, covers OKRs and risk register."
project: Strategy
tags: [type/attachment-summary, domain/strategy]
---

[[Projects/Strategy/attachment/report.pdf]]

## What this file is
[2–3 sentence description: source, date, format, overall purpose]

## Key content
[Structured outline or section map of the attachment — headings, key figures, tables]
For large files: note which sections are most relevant to which query types.

## Key facts / findings
[Bullet list of extractable facts: numbers, decisions, names, dates]
```

### How each layer uses the two summary artifacts

| Consumer | Uses | Purpose |
|---|---|---|
| Search index (Phase 3 FTS + embeddings) | Full sibling body | Embedding computed from body text — extended summary makes the vector richer and more discriminative than a bare wikilink |
| `kms_search` result (Phase 4 MCP) | `documents.summary` (frontmatter short summary) | One-line result in search hit list — boss sees teasers, picks relevant results |
| AI reading a search hit | Sibling body (extended summary) | **Verify relevance** before deciding to fetch the binary. For a 50-page PDF, the section map tells the AI which pages to request — avoiding full-file reads |
| AI answering a question | Sibling body first; binary fetched on demand | "Is this the right file?" answered by extended summary. "What does section 3 say exactly?" answered by fetching the binary |
| Boss clicking a result in Obsidian | Opens sibling `.md` | Reads the extended summary directly in Obsidian — no need to open the PDF for a quick check |

### Design intent (not a constraint, a guiding principle)

The extended summary is the AI's working memory for the attachment. It should be complete enough that:
- The AI can answer "does this file contain X?" without opening the binary.
- The AI can answer "which part of this file is relevant to Y?" by reading the section map.
- Only when the user needs verbatim quotes or precise figures does the AI fetch the binary.

This pattern keeps MCP tool latency low (summary read = fast) and binary fetches rare (expensive, slow).

### Brief #1 impact

None. `write_note` already accepts a body string of any length. The capture pipeline (Brief #2) is where the prompt is called, the body is composed, and `write_note` is invoked. The vault-layer primitives this plan builds are already sufficient.


---

## Research Brief #3 — `attachment_sync_and_archive`

**Slug:** `attachment_sync_and_archive`
**Run:** `/research_v3 attachment_sync_and_archive`

### Goal
Two coupled concerns:
- (i) Sync mechanics between attachments and `.summaries/` siblings (delete/move/update propagation).
- (ii) Per-Domain `Archive/` + `Domain/Uncategorized/` layout. Archived projects live under exactly one Domain (or Uncategorized).

Coupled because archiving moves attachment + sibling as a unit; indexer/watcher reconciles both.

### Scope IN
- `vault/watcher.py` — event sources (.md only? non-md?), debounce, `kms watch` trigger path.
- `vault/indexer.py::detect_changes` (l. ~193+) — added/modified/deleted/moved. Extend to attachments via sibling reconciliation, not direct indexing.
- `vault/writer.py::move_note`, `move_attachment` — atomic project-move requirements.
- `pipelines/capture.py::scan_capture` modified/deleted/moved loops (STATE l. 35).
- `core/config.py::VaultConfig::archive_path` (l. 98) — per-Domain Archive modeling.
- DECISION-018 — indexer skips non-md; sync detection uses different mechanism.
- Q-001 (move detection edge case).

### Scope OUT
- Capture branching at first drop (Brief #2).
- Layout primitives (Brief #1).
- CLAUDE.md re-writes on sync events (TD-015).
- Archive trigger logic — deferred per discussion; manual.
- Scheduler (Phase 8+).

### Files to investigate
**Deep trace:**
- `vault/watcher.py` — full file. What events; what handlers; debounce.
- `vault/indexer.py::detect_changes` and helpers — added/modified/deleted/moved + `content_hash` move detection.
- `pipelines/capture.py::scan_capture` modified/deleted/moved loops.
- `vault/writer.py::move_note` + `move_attachment` — cross-folder failure modes.
- `core/config.py::VaultConfig` — `archive_dir`/`archive_path` and consumers.

**Surface catalog:**
- `storage/audit_log.py` — events; need for new types (e.g. `SIBLING_ORPHANED`).
- `cli/main.py` — entry points for manual sync command.
- `storage/db.py::documents` — `vault_path` UPDATE on move; DECISION-001 mutability.

**Skip:**
- `mcp_server/` — logic-free wrapper.
- `briefings/` — Phase 8.
- `llm/` — orthogonal except for re-summarize-on-update.

### Specific questions
1. Watcher coverage today: `.md` only or all files? Map exact behavior.
2. Delete sync: `report.pdf` deleted → how detect? (a) watcher event triggers sibling delete + `documents` row delete; (b) periodic reconciliation walks `.summaries/` and removes orphans. Trade-offs.
3. Move sync within vault: `report.pdf` Project/A → Project/B. Sibling must follow. Mechanism — basename + content hash, same as `detect_changes` Q-001?
4. Content-update sync: `report.pdf` edited (mtime/hash changed). Sibling stale. Mark frontmatter `stale=true`, auto-re-summarize, or both? LLM cost vs accuracy.
5. Reverse case: user deletes/moves sibling `.summaries/<x>.md`. Indexer detects missing path → standard deletion path. Confirm attachment NOT auto-deleted (siblings are derivative).
6. Archive layout: current `Vault/Archive/<project>/` → new `Domain/<D>/Archive/<project>/`. Grep `archive_path`/`archive_dir` consumers. Model "Project under Domain" — convention or explicit metadata?
7. Uncategorized: `Domain/Uncategorized/` vs `Vault/Projects/` (no-domain active). Confirm: Uncategorized is for archived-only; active no-domain stays in `Projects/`.
8. Project move on archive: move `Projects/<A>/` whole tree → `Domain/<D>/Archive/<A>/`. `vault/writer.py` has no `move_project` — needed? Signature?
9. Bulk `vault_path` UPDATE on archive: every `documents` row under project. DECISION-001 says path mutable; FKs on integer id — tree-rename safe.
10. Phase 2 classify routing for archived projects — flag, do not design.

### Open questions
- OQ-AS1: Real-time watcher-driven sync vs periodic reconciliation (Q2/Q3).
- OQ-AS2: Stale-mark vs auto-resummarize on update (Q4).
- OQ-AS3: `move_project` writer needed (Q8); signature.
- OQ-AS4: Archive-aware classify routing (Q10) — Phase 2 plan.
- OQ-AS5: User-deleted sibling — opt-out signal or accident-to-restore?

### Constraints
- DECISION-001 (mutable `vault_path`; integer-id FKs).
- DECISION-017 (NFC).
- DECISION-018 (indexer skips non-md — applies to direct detection; sync uses sibling reconciliation).
- Q-001 (edit + move same beat — still open; surface impact on attachment moves).
- Hook: vault writes only in `vault/writer.py`.
- Scheduler last — manual CLI first.
- TD-018 (taxonomy refresh) — Domain list changes when archive structure exists; flag.

### Output
`docs/research/attachment_sync_and_archive.md`.

### Cross-refs
- Depends on Brief #1's `.summaries/` traversal + `vault_path` decision.
- Depends on Brief #2's audit-entry choices.
- Forward-coupled to Phase 2 classify (archive routing) — flag.

### Todo list
1. Load STATE.md (DECISION-001/-017/-018; Q-001, TD-018), CLAUDE.md, roadmap, this discussion file.
2. Read `docs/research/vault_layer.md` + prior watcher/indexer notes.
3. Deep-trace files above.
4. Surface-catalog audit_log, CLI entries, documents schema.
5. Answer 10 questions; surface OQs.
6. Write `docs/research/attachment_sync_and_archive.md`.
7. Self-review.

---

## Recommended execution order

1. **Brief #1 first** — layout primitives. Outputs feed others.
2. **Brief #2 next** — capture branch. Consumes #1; produces routing/audit shapes #3 reuses.
3. **Brief #3 last** — sync + archive. Depends on both; most cross-cutting OQs.

Each brief is self-contained; parallel runs valid but late-merging OQs may surface conflicts.

## Out-of-scope follow-ups (separate research later)

- CLAUDE.md index writer (TD-015 section-merge).
- Archive trigger logic (inactivity detection).
- Phase 2 classify archive-aware routing.
- Scheduler-driven sync passes.
