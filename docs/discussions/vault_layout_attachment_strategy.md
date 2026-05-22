# Discussion: Vault Layout — Attachment Sibling Strategy & Per-Domain Structure

_Date: 2026-05-22_
_Status: Reached conclusion; no plan written yet._

---

## User's original proposal

> read @STATE.md and @CLAUDE.md and @docs/plans/handlers.md  and discuss with me: Im afraid that creating sibling .md file for each non-binary file will cause the trouble of overflooding the vault with so many empty notes (almost empty, except a link to the material). So my thinking is this: only move to attchment (if out of attachment), still process the content, and then add note into an index file with note name and a short description (as described in @docs/obsidian_vault_layout.md , there will be a CLAUDE.md file in each folder for essential context AND have an index of all files in the folder for both human and AI to navigate).
>
> Now, as I think about it, I think each Domain or Project folder should have their own attachment sub folder - related non-binary material should stay within the parent Project/Domain because this is the natural way that my boss expect to navigate in her file system.
>
> Another folder that also get scattered is Archive. Archive should also sit inside the Domain to contained inactive Projects. This implies that each project MUST HAVE its corresponding Domain. So to back up for cases where the project does not fit any domain, or the boss not want to make a domain for that project, there will be Uncategorized folder in Domain/
>
> What do you think about this approach?

---

## Initial response — trade-offs flagged

> Approach sound, but trade-offs to flag:
>
> **1. Sibling-note flood — agree, valid concern.** Index-only pattern cleaner for boss UX. But cost:
> - Each attachment loses its own searchable summary note. Retrieval (Phase 3) must search index entries instead of full summary bodies → coarser hits.
> - Captured summary lives only in CLAUDE.md index row → length-bounded. Long PDFs get truncated summaries.
> - `documents` table (SQLite) needs schema rethink: today one row per .md note. If attachment has no sibling .md, where does its `vault_path` point? Options: (a) row points to attachment file directly, `is_md=False`; (b) row points to parent CLAUDE.md with `attachment_path` column added. Both touch DECISION-001 + DECISION-018 (indexer skips non-.md).
>
> **2. Per-Domain/Project attachment folders — agree, matches boss mental model.** But:
> - `vault/writer.move_attachment()` currently writes to single `Vault/attachment/`. Needs target-folder param.
> - Attachment dedup across projects harder (same PDF in two projects = two copies).
> - Naming collisions per-folder, not global.
>
> **3. Per-Domain Archive — reasonable.** But:
> - "Project MUST have Domain" is hard constraint. Capture pipeline today routes by AI confidence; if no domain matches, today falls back to inbox-review. Now needs `Domain/Uncategorized/` fallback. Workable.
> - Archive trigger (project inactive N days) needs per-project metadata. Today's plan defers archival entirely.
>
> **4. Hidden cost — CLAUDE.md becomes write-hot.** Every capture mutates parent folder's CLAUDE.md index. TD-015 already flags CLAUDE.md co-authoring needs section-merge. Index-row append = section-merge problem now blocks Phase 1, not Phase 12+.
>
> **Recommend:**
> - Per-Domain attachment + Archive: yes, low-risk structural change.
> - Index-only (no sibling .md): defer until section-merge writer exists. Interim: keep sibling .md but make summary terse (1-2 lines). Avoids flood without blocking on TD-015.
> - Domain/Uncategorized: yes, simple fallback.
>
> Which trade you want push on first — section-merge blocker or schema rework?

---

## Intermediate exchanges (summary)

- Clarified `documents` table role: SQLite index for retrieval (Phase 3) — search index (FTS5 + embeddings → doc IDs), metadata cache (frontmatter mirror), stable FK identity for `audit_log` / `corrections`, move detection via `content_hash`. Not body storage.
- User proposed hybrid: one `sibling_index.md` per `attachment/` folder, indexed by `documents` with `vault_path` pointing at it; attachment path derived from parent dir.
- Flagged cons: granularity loss for search; section-merge blocker hits harder (every capture mutates one file); `updated_by_human` whole-note lock freezes additions; `content_hash` churn → re-embed cost; audit trail muddier.
- Counter-proposed per-attachment sibling `.md` in hidden `.summaries/` subfolder inside `attachment/` — user clarified this was the original idea, misread on my part.

---

## Conclusion reached

**Final structure per Project (and analogously per Domain):**

```
Projects/<A>/
├── CLAUDE.md                    ← human-facing index (1-line per attachment, links to .pdf/.docx directly)
├── <user notes>.md
└── attachment/
    ├── report.pdf
    ├── deck.docx
    └── .summaries/              ← hidden from Obsidian, per-attachment sibling .md
        ├── report.md
        └── deck.md
```

**Design points agreed:**

1. **`.summaries/` hidden from Obsidian.** Dot-prefix hides by default; if not, configure via Obsidian "Files & Links → Excluded files" (per-vault setting in `.obsidian/app.json`). User confirmed will handle.

2. **CLAUDE.md index links to binary directly (e.g. `report.pdf`).** Boss clicks → opens PDF. The `.md` summary is reachable only via AI query system; not for human browsing.

3. **`documents` rows:** one per `.summaries/*.md`. Search granular, embeddings per-attachment. No flood. Schema unchanged — `vault_path = Projects/<A>/attachment/.summaries/report.md`, NFC-normalized (DECISION-017).

4. **Attachment path resolution:** `.summaries/report.md` → either `../report.pdf` by convention, or store explicit `attachment_path` in frontmatter (cleaner, survives rename). To decide at plan time.

5. **Indexer scope (DECISION-018):** must still skip non-.md (attachments stay out); must NOT skip `.summaries/` traversal. Confirm dot-folder traversal not blocked by glob in `vault/indexer.py`.

6. **`vault/writer.move_attachment()` extension:** today writes to single `Vault/attachment/`. New signature needs target project/domain folder + auto-create `.summaries/` sibling. Saved as TODO for plan-writing step.

7. **CLAUDE.md index is plain prose, not row-append.** Earlier framing of "row-append" was wrong — CLAUDE.md is a normal markdown doc rewritten in full. For this phase, AI writes it solo; co-author with human comes later (TD-015 still applies for body co-authoring, but index rewrite is AI-only for now).

**Other agreed structural changes (separate from sibling strategy):**

- **Per-Project / per-Domain `attachment/` folders** instead of single `Vault/attachment/`. Matches boss's natural file-system navigation.
- **Per-Domain `Archive/` folders** containing inactive Projects (instead of single `Vault/Archive/`).
- **`Domain/Uncategorized/` fallback** for projects not fitting any domain. Capture pipeline's no-match path routes here instead of inbox-review.
- **Hard constraint:** every Project lives under exactly one Domain (or `Uncategorized`).

---

## Not yet decided / deferred

- `attachment_path` in frontmatter vs. by-convention (`../<name>.<ext>`). Plan-time decision.
- Cross-project attachment deduplication (same PDF in two projects → two copies). Acceptable cost or worth solving?
- Archive trigger (project inactive N days) — still deferred; needs per-project activity metadata.
- TD-015 (CLAUDE.md body co-authoring with human) untouched — index writing is AI-solo for this phase.

---

## Next step

User will save TODO for `move_attachment()` signature change. Plan-writing not yet requested.
