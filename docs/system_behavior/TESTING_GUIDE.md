# AI-KMS Testing Guide
_For non-technical testers. No coding required for Smoke and Phase checks._
_Auto-generated from `behavior_inventory.yaml`. Fill in **Current result** fields after testing. All other content is regenerated — do not edit._
_Last generated: 2026-06-05_

---

## How to Read This Guide

Each check is tagged. Here is what the tags mean.

### Origin — who created this check?

| Label | Meaning | What to do |
|-------|---------|------------|
| `design` | Created during design phase — captures what the human INTENDED the system to do, before any code was written | Treat as the human's stated requirement. If this conflicts with what the system actually does, the system might have a bug. |
| `implementation` | Created after code was written — captures what the system ACTUALLY does | Treat as ground truth for current behavior. If this conflicts with design intent, the code might be doing something the human didn't ask for. |

### Granularity — what level of detail?

| Label | Meaning | Example |
|-------|---------|---------|
| `outcome` | What the user sees at the end — the observable result | "When a file is captured, it appears in the project folder with a summary." |
| `mechanism` | How the system gets there — the internal steps | "File goes to inbox first, watcher detects it, pipeline moves it to project folder, summary is written to .summaries/." |

Outcome and mechanism entries for the same feature are **complementary, not conflicting.** One says WHAT should happen, the other says HOW. A conflict only fires when two entries at the **same granularity** disagree.

### Status — what state is this check in?

| Label | Meaning | What to do |
|-------|---------|------------|
| `active` | This check is live — test it | Run the test as described |
| `planned` | This check describes behavior not yet built | Skip — it's a placeholder for future work |
| `retired` | This behavior was removed or replaced | Skip — kept as a historical record |
| `conflict` | Design says one thing, implementation says another | **Priority item.** Read both expectations. Decide which is correct. Tell the developer. |

### What to do with conflicts

When you see a `conflict` entry in the testing guide:

1. **Read both expectations** — "Design says" and "Implementation says" are shown side by side
2. **Test what the system actually does** — run the test and observe
3. **Decide which is right:**
   - If the system matches design intent → no issue, resolve as "both agree"
   - If the system does something different from design intent → flag to developer: "system behaves like [X] but design said [Y]"
   - If you're unsure which is correct → flag to developer: "I see [behavior], design says [X], implementation says [Y] — please clarify"

### Tiers
- **Smoke** (~5 min) — run before any demo or after any change. Must always pass.
- **Phase** — run when that phase's code is touched.
- **Full** — developer-only, requires terminal + DB access.

### Setup
1. Run `bash docs/system_behavior/setup_test_vault.sh` to create all test fixtures
2. Or `bash docs/system_behavior/setup_test_vault.sh P1-CAP-01` for a single test

### Where things live
| What | Path |
|------|------|
| Test vault | Set by `testing.vault_path` in `src/config/config.yaml` (the `actual_test_vault/` subdirectory) |
| Staging area | Parent folder of test vault — staging files live here; tester copies them into vault |
| Database | `data/kb.db` |
| Logs | `logs/app.log` |

### How to record results

1. Run the **Setup** and **Run** commands as shown
2. Go through each item in **Check** — tick `[x]` if it passes, leave `[ ]` if it fails
3. Fill in **Current result:**
   - All items checked → write `pass`
   - Any item unchecked → write `fail` or `fail — <what you observed>`
   - If you write just `fail` without details, the system will infer the reason from your unchecked items next time it updates
4. Leave **Last tested** and **Last result** alone — the system updates those automatically

---

## ⚠ Priority: Resolve These First

### ⚠ CONFLICT: P15-HDL-07 · PNG/JPG image file captured with sibling .md
_Origin: design · Granularity: outcome_

**Design says (origin: design):**

Sibling .md created with summary (OCR text or filename-based).

**Implementation says (origin: implementation):**

NOT implemented. PngHandler/JpgHandler.extract() return Failure("image extraction requires a vision-capable LLM — not yet implemented", recoverable=False) at src/handlers/image_handler.py:21-26 and :30-38. No sibling .md is created; no summary is produced. Images are NOT captured.

**What to do:** Test the system and observe what actually happens. Then tell the developer which expectation is correct — or if neither is right.

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-HDL-07
```

**Run:**
```bash
uv run kms capture inbox/screenshot.png
```

**Check:**
- [ ] Sibling .md created with summary (OCR text or filename-based)

**Last tested:** 2026-06-05
**Last result:** failed - img LLM not yet implemented
**Current result:** ___

---

## SMOKE — Must Always Pass (~5 min)

---

### P1-CAP-01 · .md file captured in-place with AI summary in frontmatter
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-01
```

**Run:**
```bash
uv run kms capture inbox/test-md-capture.md
```

**Check:**
- [ ] File stays at inbox/test-md-capture.md (not moved)
- [ ] Open file — frontmatter block (between --- markers) added at top
- [ ] summary: field present and non-empty
- [ ] tags: includes at least one type/ tag
- [ ] confidence: has a decimal number
- [ ] Body text below --- unchanged from original

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-02 · PDF already in a project folder creates sibling .md and moves binary to attachment/
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-02
```

**Run:**
```bash
uv run kms capture Projects/Alpha/sample-report.pdf
```

**Check:**
- [ ] Projects/Alpha/sample-report.pdf is GONE from Projects/Alpha/ (moved to attachment/)
- [ ] Binary appears at `Projects/Alpha/attachment/sample-report.pdf`
- [ ] Sibling .md at `Projects/Alpha/attachment/.summaries/sample-report.pdf.md`
- [ ] Sibling frontmatter has attachment_path: pointing to binary location
- [ ] Sibling frontmatter has type: attachment-summary
- [ ] Sibling frontmatter has summary: (non-empty)

**Last tested:** 2026-06-05
**Last result:** passed
**Current result:** ___

---

### P1-CAP-03 · Body text of .md file preserved exactly after capture
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-03
```

**Run:**
Note down the body text of inbox/test-body-preservation.md before running

```bash
uv run kms capture inbox/test-body-preservation.md
```

**Check:**
- [ ] Body text below second --- is byte-identical to original
- [ ] summary: appears only inside frontmatter (between --- markers), never in body

**Last tested:** 2026-06-05
**Last result:** passed
**Current result:** ___

---

### P1-CAP-04 · Re-capture does not rename already-captured .md file (rename gate Rule 1)
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-04
```

**Run:**
Run once:

```bash
uv run kms capture inbox/test-rename-gate.md
```

Then run again without editing the file:

```bash
uv run kms capture inbox/test-rename-gate.md
```

**Check:**
- [ ] File keeps original name test-rename-gate.md after second capture
- [ ] summary: may update but filename unchanged
- [ ] No duplicate file appears on disk

**Last tested:** 2026-06-05
**Last result:** passed
**Current result:** ___

---

### P1-CAP-05 · Scan captures un-indexed .md files, skips already-indexed
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-05
```

**Run:**
Both files are in inbox. Capture test-md-capture.md first to get it indexed:

```bash
uv run kms capture inbox/test-md-capture.md
```

Now run scan — test-scan-uncaptured.md is in inbox but not yet indexed:

```bash
uv run kms capture --scan
```

**Check:**
- [ ] test-scan-uncaptured.md gets summary: in frontmatter
- [ ] test-md-capture.md summary: unchanged (not re-processed)

**Last tested:** 2026-06-05
**Last result:** passed
**Current result:** ___

---

### P15-LOC-01 · Note in Domain/ folder gets domain/`<D>` tag
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-LOC-01
```

**Run:**
```bash
uv run kms capture Domain/Finance/test-domain-tag.md
```

**Check:**
- [ ] Open file — frontmatter tags: contains domain/Finance
- [ ] No domain: scalar field in frontmatter (only the tag)

**Last tested:** 2026-06-05
**Last result:** passed
**Current result:** ___

---

### P15-LOC-02 · Note in Projects/ folder gets project field
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-LOC-02
```

**Run:**
```bash
uv run kms capture Projects/Alpha/test-project-tag.md
```

**Check:**
- [ ] Open file — frontmatter has project: Alpha

**Last tested:** 2026-06-05
**Last result:** passed
**Current result:** ___

---

## PHASE — Run When Phase Code Changes

---

### Phase 1 — Capture Pipeline

---

### P1-CAP-06 · DOCX file dropped in inbox creates pending-routing marker (no summary yet)
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-06
```

**Run:**
```bash
uv run kms capture inbox/q3-planning-brief.docx
```

**Check:**
- [ ] q3-planning-brief.docx stays in inbox/ (not moved)
- [ ] Sibling .md created at inbox/.summaries/q3-planning-brief.docx.md
- [ ] Sibling frontmatter has type: attachment-summary
- [ ] Sibling frontmatter has status: pending-routing
- [ ] Sibling frontmatter has NO summary: field (deferred to Phase 2 Classify)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-07 · Watcher auto-captures new file drops
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-07
```

**Run:**
In Terminal 1, start the watcher:

```bash
uv run kms watch
```

In Terminal 2, copy the staging file to inbox/ and wait ~10 seconds:

```bash
cp <staging>/auto-capture-test.md <vault>/inbox/
```

**Check:**
- [ ] auto-capture-test.md gets summary: in frontmatter automatically
- [ ] No manual kms capture was needed
- [ ] Only one capture pipeline fires per drop (in-flight guard prevents concurrent pipelines on same path)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-08 · Gibberish-named PDF gets FULL_RENAME to AI-chosen title
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-08
```

**Run:**
```bash
uv run kms capture inbox/xkjdhfs83.pdf
```

**Check:**
- [ ] Sibling .md has an AI-chosen title (not xkjdhfs83)
- [ ] Audit log shows rename_gate outcome=FULL_RENAME

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-09 · Idempotent capture — unchanged .md file skipped
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-09
```

**Run:**
Run once:

```bash
uv run kms capture inbox/test-idempotent.md
```

Then run again without editing the file:

```bash
uv run kms capture inbox/test-idempotent.md
```

**Check:**
- [ ] Second run returns OK (SKIPPED) in terminal output
- [ ] content_hash match — no LLM call made
- [ ] summary: field identical to first capture

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-10 · CLUELESS routing — inbox binary gets pending-routing marker
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-10
```

**Run:**
`uv run kms capture inbox/mystery-file.pdf` (no project/domain hint in filename or content)

**Check:**
- [ ] mystery-file.pdf stays in inbox/ (not moved)
- [ ] Marker .md created at inbox/.summaries/mystery-file.pdf.md
- [ ] Marker frontmatter has status: pending-routing
- [ ] Marker frontmatter has type: attachment-summary
- [ ] Marker body contains placeholder text (not a real summary)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-11 · URL enrichment — sparse note with URLs gets content fetched
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-11
```

**Run:**
`uv run kms capture inbox/test-url-note.md` (file body has a URL and less than 500 chars of text)

**Check:**
- [ ] Open file — summary: reflects content from the URL, not just the sparse body text

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### Phase 1.5 — Location Tags + Attachment Layout + Reconcile

---

### P15-REC-01 · Reconcile removes stale domain tag when folder deleted
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-REC-01
```

**Run:**
Place inbox/test-stale-domain-tag.md with a domain/OldDomain tag in frontmatter (but no Domain/OldDomain/ folder exists on disk). Then run reconcile:

```bash
uv run kms reconcile
```

**Check:**
- [ ] Open file — domain/OldDomain tag removed from frontmatter
- [ ] Other tags preserved

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-REC-02 · Reconcile adds missing domain tag for note in Domain/ folder
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-REC-02
```

**Run:**
Place Domain/Engineering/test-missing-domain-tag.md without a domain/Engineering tag in frontmatter. Then run reconcile:

```bash
uv run kms reconcile
```

**Check:**
- [ ] Open file — domain/Engineering tag added to frontmatter
- [ ] Body text unchanged

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-REC-03 · Reconcile captures orphan binaries missing sibling .md
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-REC-03
```

**Run:**
Place Projects/Alpha/attachment/orphan-report.pdf on disk without any .summaries/orphan-report.pdf.md sibling. Then run reconcile:

```bash
uv run kms reconcile
```

**Check:**
- [ ] Sibling .md created at Projects/Alpha/attachment/.summaries/orphan-report.pdf.md
- [ ] Sibling frontmatter has summary: (non-empty)
- [ ] Sibling frontmatter has attachment_path: pointing to orphan-report.pdf

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-REC-04 · Reconcile deletes orphan sibling when binary is gone
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-REC-04
```

**Run:**
Place Projects/Alpha/attachment/.summaries/deleted-file.pdf.md on disk (with type: attachment-summary in frontmatter) but NO corresponding binary at the attachment_path. Then run reconcile:

```bash
uv run kms reconcile
```

**Check:**
- [ ] Orphan sibling deleted-file.pdf.md deleted from disk
- [ ] DB row for deleted-file.pdf.md removed from documents table

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-REC-05 · Reconcile clears stale batch_id when doc moved away from batch destination
_Origin: implementation · Granularity: mechanism_

**Run:**
Move a document with batch_id set to a folder not matching the batch destination. Run reconcile:

```bash
uv run kms reconcile
```

Then verify the batch_id was cleared:

```bash
sqlite3 data/kb.db "SELECT vault_path, batch_id FROM documents WHERE batch_id IS NOT NULL"
```

**Check:**
- [ ] batch_id set to NULL in documents table for the moved document

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-HDL-01 · XLSX file captured with sibling .md
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-HDL-01
```

**Run:**
```bash
uv run kms capture inbox/q2-budget.xlsx
```

**Check:**
- [ ] Sibling .md created with summary: in frontmatter
- [ ] q2-budget.xlsx moved out of inbox/

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-HDL-02 · PPTX file captured with sibling .md
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-HDL-02
```

**Run:**
```bash
uv run kms capture inbox/deck.pptx
```

**Check:**
- [ ] Sibling .md created with summary: in frontmatter
- [ ] deck.pptx moved out of inbox/

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-HDL-03 · CSV file captured with sibling .md
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-HDL-03
```

**Run:**
```bash
uv run kms capture inbox/data.csv
```

**Check:**
- [ ] Sibling .md created with summary: in frontmatter

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-HDL-04 · HTML file captured with sibling .md
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-HDL-04
```

**Run:**
```bash
uv run kms capture inbox/page.html
```

**Check:**
- [ ] Sibling .md created with summary: in frontmatter

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-HDL-05 · EML email file captured with sibling .md
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-HDL-05
```

**Run:**
```bash
uv run kms capture inbox/message.eml
```

**Check:**
- [ ] Sibling .md created with summary: in frontmatter
- [ ] Summary mentions from/to/subject from the email

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-HDL-06 · MSG Outlook file captured with sibling .md
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-HDL-06
```

**Run:**
```bash
uv run kms capture inbox/outlook-msg.msg
```

**Check:**
- [ ] Sibling .md created with summary: in frontmatter
- [ ] Summary mentions from/to/subject from the email

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-FOLD-01 · Folder dropped in inbox classified and routed by LLM
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-FOLD-01
```

**Run:**
Create inbox/new-project-folder/ with inbox/new-project-folder/file1.md and inbox/new-project-folder/file2.pdf inside. Then run scan:

```bash
uv run kms capture --scan
```

**Check:**
- [ ] Folder moved to `Projects/<name>/` or `Domain/<name>/` (AUTO confidence) OR stays in inbox/ with PENDING_REVIEW/CLUELESS status
- [ ] Query `sqlite3 data/kb.db "SELECT * FROM batches ORDER BY rowid DESC LIMIT 5"` — row created with destination and confidence

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### Phase Pre-2 — DB Schema + Domain Scalar Cleanup

---

### PRE2-DB-01 · Capture populates project, status, key_topics DB columns
_Origin: implementation · Granularity: mechanism_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh PRE2-DB-01
```

**Run:**
```bash
uv run kms capture Projects/Alpha/test-db-columns.md
```

Then query the DB to verify columns:

```bash
sqlite3 data/kb.db "SELECT vault_path, project, status, key_topics FROM documents WHERE vault_path LIKE '%test-db-columns%'"
```

**Check:**
- [ ] DB row has project=Alpha
- [ ] key_topics contains a JSON array of topic tags
- [ ] status column present (may be NULL)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### PRE2-DOM-01 · Old domain: scalar stripped on re-write (lazy migration)
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh PRE2-DOM-01
```

**Run:**
`uv run kms capture inbox/test-old-domain-scalar.md` (file has domain: finance in existing frontmatter)

**Check:**
- [ ] Open file — domain: scalar key GONE from frontmatter
- [ ] domain/Finance in tags: list preserved (if it was present before)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### Phase Vault-Restructure — Editable/No-Edit Split

---

### VR-PLACE-01 · No-edit binary (PDF/PNG/JPG) routed to attachment/
_Origin: implementation · Granularity: outcome_

**Run:**
Run capture on a no-edit binary such as a PDF or PNG:

```bash
uv run kms capture inbox/<no-edit-file>.pdf
```

**Check:**
- [ ] Binary routed to `Projects/<A>/attachment/<file>` (hidden from Obsidian)
- [ ] Same routing as before vault-restructure

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### VR-PLACE-02 · Editable binary (XLSX/DOCX/PPTX) routed to project/domain root
_Origin: implementation · Granularity: outcome_

**Run:**
Run capture on an editable binary such as an XLSX, DOCX, or PPTX:

```bash
uv run kms capture inbox/<editable-file>.xlsx
```

**Check:**
- [ ] Binary routed to `Projects/<A>/<file>.xlsx` (visible in Obsidian, NOT in attachment/)
- [ ] Sibling .md at `Projects/<A>/.summaries/<file>.xlsx.md`

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### VR-SKIP-01 · Watcher and scan skip AI-output folders (Briefings/Synthesis/Documentation)
_Origin: implementation · Granularity: outcome_

**Run:**
Place a .md file in the Briefings/ folder. Then run scan:

```bash
uv run kms capture --scan
```

**Check:**
- [ ] File in Briefings/ NOT captured (skipped)
- [ ] No DB row created in documents table for the file

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### VR-REHOME-01 · Misplaced binary re-homed to correct placement
_Origin: implementation · Granularity: outcome_

**Run:**
Place an editable file (e.g. .xlsx) in `Projects/<A>/attachment/` (wrong location). Then run reconcile:

```bash
uv run kms reconcile
```

**Check:**
- [ ] File moved from attachment/ to project root (`Projects/<A>/`)
- [ ] Sibling .md path updated to reflect new location

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

## FULL — Developer Only (Terminal + DB Access)

---

### Phase 1 — Capture Pipeline

---

### P1-DEV-01 · Audit trail written for every capture decision
_Origin: implementation · Granularity: mechanism_

**Run:**
Capture any file:

```bash
uv run kms capture inbox/test-md-capture.md
```

Then query the audit log:

```bash
sqlite3 data/kb.db "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 5"
```

**Check:**
- [ ] audit_log row exists with pipeline=capture
- [ ] stage=metadata in the row
- [ ] outcome=CAPTURED
- [ ] correlation_id matches the ID shown in terminal output
- [ ] reasoning field is non-empty

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-DEV-02 · Tag violation audited — invalid tags stripped and logged
_Origin: implementation · Granularity: mechanism_

**Run:**
Force a capture where LLM returns a tag not in tags.yaml taxonomy (developer test — requires mocking LLM response)

**Check:**
- [ ] audit_log row with stage=tag_violation, outcome=TAG_VIOLATION
- [ ] Bad tag removed from final tags list in frontmatter
- [ ] Valid tags preserved in frontmatter

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-DEV-03 · File-lost guard — deleted file between event and pipeline
_Origin: implementation · Granularity: mechanism_

**Run:**
Run capture on a path that does not exist on disk:

```bash
uv run kms capture inbox/nonexistent-file.md
```

**Check:**
- [ ] Terminal shows Failure(recoverable=True)
- [ ] audit_log row with stage=file_lost, outcome=FILE_LOST
- [ ] No DB row created in documents table

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-DEV-04 · Cooldown gate rejects file still being written
_Origin: implementation · Granularity: mechanism_

**Run:**
Write a new file to inbox/, then immediately run capture (within the cooldown window, before mtime ages):

```bash
uv run kms capture inbox/<that-file>.md
```

**Check:**
- [ ] Terminal shows Failure(recoverable=True)
- [ ] No LLM call made
- [ ] No DB row created in documents table

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-DEV-05 · Pending-routing guard blocks re-capture of CLUELESS binary
_Origin: implementation · Granularity: mechanism_

**Run:**
Run once to create the CLUELESS marker:

```bash
uv run kms capture inbox/mystery-file.pdf
```

Then run again without changing anything:

```bash
uv run kms capture inbox/mystery-file.pdf
```

**Check:**
- [ ] Second capture returns Failure(recoverable=True)
- [ ] No duplicate marker .md created
- [ ] No LLM call made on second run

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### Phase 1.5 — Location Tags + Attachment Layout + Reconcile

---

### P15-DEV-01 · Watcher binary-delete sync — sibling cleaned up
_Origin: implementation · Granularity: mechanism_

**Run:**
In Terminal 1, start the watcher:

```bash
uv run kms watch
```

In Terminal 2, delete a binary from `Projects/<A>/attachment/` and wait ~5 seconds:

```bash
rm <vault>/Projects/Alpha/attachment/report.pdf
```

**Check:**
- [ ] Sibling .md at `.summaries/<binary.name>.md` deleted from disk
- [ ] DB row for sibling removed from documents table
- [ ] audit_log row with stage=watcher:binary_delete, outcome=SIBLING_ORPHANED

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-02 · Watcher binary-rename sync — sibling renamed
_Origin: implementation · Granularity: mechanism_

**Run:**
In Terminal 1, start the watcher:

```bash
uv run kms watch
```

In Terminal 2, rename a binary in attachment/ and wait ~5 seconds:

```bash
mv <vault>/Projects/Alpha/attachment/old.pdf <vault>/Projects/Alpha/attachment/new.pdf
```

**Check:**
- [ ] Sibling renamed from old.pdf.md to new.pdf.md in .summaries/
- [ ] Open sibling — attachment_path in frontmatter updated to new.pdf
- [ ] DB row path and attachment_path both updated

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-03 · Watcher binary cross-folder move — old sibling orphaned
_Origin: implementation · Granularity: mechanism_

**Run:**
In Terminal 1, start the watcher:

```bash
uv run kms watch
```

In Terminal 2, move a binary across folders and wait ~5 seconds:

```bash
mv <vault>/Projects/A/attachment/file.pdf <vault>/Projects/B/attachment/file.pdf
```

**Check:**
- [ ] Old sibling .md in Projects/A/attachment/.summaries/ deleted
- [ ] DB row for old sibling removed
- [ ] New orphan binary in Projects/B/attachment/ picked up on next `uv run kms reconcile`

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-04 · Reconcile stage 3 — re-summarize stale binaries
_Origin: implementation · Granularity: mechanism_

**Run:**
Update a binary's mtime so it is newer than its sibling .md:

```bash
touch <vault>/Projects/Alpha/attachment/report.pdf
```

Then run reconcile:

```bash
uv run kms reconcile
```

**Check:**
- [ ] Sibling .md re-summarized with updated content
- [ ] DB row updated with new content_hash

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-05 · Debounce coalescing — rapid file events produce single capture
_Origin: implementation · Granularity: mechanism_

**Run:**
In Terminal 1, start the watcher:

```bash
uv run kms watch
```

In Terminal 2, save the same file 5 times within 1 second:

```bash
for i in 1 2 3 4 5; do touch <vault>/inbox/test.md; done
```

**Check:**
- [ ] Only one capture_file call fires (after 3.0s debounce window)
- [ ] No duplicate DB rows in documents table

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-06 · Scan skips .summaries/ paths in added loop
_Origin: implementation · Granularity: mechanism_

**Run:**
Ensure .summaries/ sibling files exist on disk (from a prior capture). Then run scan:

```bash
uv run kms capture --scan
```

**Check:**
- [ ] Sibling .md files inside .summaries/ NOT re-captured
- [ ] No rename or identity wipe on existing siblings

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-07 · In-flight guard — modify event during running pipeline does not launch second capture
_Origin: bugfix · Granularity: mechanism_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-DEV-07
```

**Run:**
In Terminal 1, start the watcher:

```bash
uv run kms watch
```

In Terminal 2, drop file and wait for pipeline to start (but not finish), then touch it:

```bash
cp tests/fixtures/sample.md <vault>/inbox/ && sleep 5 && touch <vault>/inbox/sample.md
```

**Check:**
- [ ] watcher.skip_in_flight debug log appears for the modify event
- [ ] Only one metadata.captured log line appears
- [ ] No duplicate audit entries for the same path

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### Phase Vault-Restructure — Editable/No-Edit Split

---

### VR-CONTENT-01 · Binary content change detected and re-summarized
_Origin: implementation · Granularity: mechanism_

**Run:**
Start the watcher:

```bash
uv run kms watch
```

Edit an XLSX file's content (not just touch — actual data change) and wait for the watcher to detect the SHA-256 change.

**Check:**
- [ ] Sibling .md re-summarized with updated content
- [ ] source_hash updated in sibling frontmatter and DB row

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---
