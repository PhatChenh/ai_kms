# AI-KMS Testing Guide
_For non-technical testers. No coding required for Smoke and Phase checks._
_Auto-generated from `behavior_inventory.yaml`. Fill in **Current result** fields after testing. All other content is regenerated — do not edit._
_Last generated: 2026-06-14_

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

**Last tested:** 2026-06-10
**Last result:** passed
**Current result:** ___

---

### P1-CAP-03 · Body text of .md file preserved exactly after capture (classify_step runs on inbox files but does not affect body)
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
- [ ] classify_step runs on this inbox file — candidate fields (suggested_project etc.) may appear in frontmatter depending on AI confidence
- [ ] summary: appears only inside frontmatter (between --- markers), never in body

**Last tested:** 2026-06-10
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

**Last tested:** 2026-06-10
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

**Last tested:** 2026-06-10
**Last result:** passed
**Current result:** ___

---

### P2-BAT-04 · scan_capture detects and batch-captures an unprocessed inbox subfolder
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-BAT-04
```

**Run:**
Create the subfolder and two files: `mkdir -p /tmp/ai_kms_test_vault/inbox/test-batch-drop` then drop any two .md files inside it (can be blank or with a line of text).

Run `uv run kms capture --scan` from the repo root.

Check both files were indexed: `sqlite3 data/kb.db "SELECT vault_path, batch_id FROM documents WHERE vault_path LIKE 'inbox/test-batch-drop/%' ORDER BY vault_path"`

Check a batch row was created: `sqlite3 data/kb.db "SELECT folder_path, status FROM batches WHERE folder_path LIKE '%test-batch-drop%'"`

**Check:**
- [ ] Both vault_path rows returned by the first query, both with the same non-NULL batch_id (a UUID-style string).
- [ ] The batches query returns exactly one row with folder_path containing 'test-batch-drop'.

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

## PHASE — Run When Phase Code Changes

---

### Phase 1 — Capture Pipeline

---

### P1-CAP-06 · DOCX file dropped in inbox gets real-summary sibling AND is classified at capture time
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
- [ ] On CLUELESS/SUGGEST: q3-planning-brief.docx stays in inbox/; sibling at inbox/.summaries/
- [ ] On AUTO: binary moves to project attachment/; sibling moves alongside
- [ ] Sibling .md created at inbox/.summaries/q3-planning-brief.docx.md
- [ ] Sibling frontmatter has type: attachment-summary
- [ ] Sibling frontmatter has status: needs-review (NOT pending-routing — concept retired in P2-CIC Phase 7)
- [ ] Sibling frontmatter has source_hash set (idempotent re-entry)
- [ ] Sibling body contains a real AI-generated summary (NOT placeholder text)

**Last tested:** 2026-06-07
**Last result:** passed — behavior changed in P2-CIC Phase 7; re-verify needed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-09 · Idempotent capture — unchanged .md file skipped (content_hash match). Note: inbox fixture may be AUTO-moved by classify_step on first capture.
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
- [ ] If file was AUTO-moved by classify_step on first capture, second capture on original inbox path returns FILE_LOST

**Last tested:** 2026-06-07
**Last result:** passed
**Current result:** ___

---

### P1-CAP-10 · Inbox binary gets real summary AND is classified — may be CLUELESS, SUGGEST, or AUTO
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-10
```

**Run:**
```bash
uv run kms capture inbox/mystery-file.pdf
```

No project/domain hint in filename or content — capture parks binary + writes real-summary sibling.

**Check:**
- [ ] On CLUELESS/SUGGEST: mystery-file.pdf stays in inbox/; sibling at inbox/.summaries/
- [ ] On AUTO: binary moves to project attachment/; sibling moves alongside
- [ ] Sibling .md created at inbox/.summaries/mystery-file.pdf.md
- [ ] Sibling frontmatter has status: needs-review (NOT pending-routing)
- [ ] Sibling frontmatter has type: attachment-summary
- [ ] Sibling frontmatter has source_hash set (non-empty — enables idempotent re-entry)
- [ ] Sibling body contains a real AI-generated summary (NOT a one-line placeholder)

**Last tested:** 2026-06-07
**Last result:** passed — behavior changed in P2-CIC Phase 7; re-verify needed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-11 · URL enrichment — sparse note with URLs gets content fetched (classify_step also runs on inbox files)
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-CAP-11
```

**Run:**
```bash
uv run kms capture inbox/test-url-note.md
```

File body must contain a URL and less than 500 chars of text to trigger URL enrichment.

**Check:**
- [ ] Open file — summary: reflects content from the URL, not just the sparse body text
- [ ] classify_step runs — candidate fields or AUTO-move possible

**Last tested:** 2026-06-07
**Last result:** passed
**Current result:** ___

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

**Last tested:** 2026-06-07
**Last result:** passed
**Current result:** ___

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

**Last tested:** 2026-06-07
**Last result:** passed
**Current result:** ___

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

**Last tested:** 2026-06-07
**Last result:** passed
**Current result:** ___

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

Verify sibling deleted from disk (should return nothing):

```bash
ls /tmp/ai_kms_test_vault/Projects/Alpha/attachment/.summaries/deleted-file.pdf.md 2>&1
```

Verify DB row removed (expected: 0):

```bash
sqlite3 data/kb.db "SELECT count(*) FROM documents WHERE vault_path='Projects/Alpha/attachment/.summaries/deleted-file.pdf.md'"
```

**Check:**
- [ ] Orphan sibling deleted-file.pdf.md deleted from disk
- [ ] DB row for deleted-file.pdf.md removed from documents table

**Last tested:** 2026-06-07
**Last result:** failed
**Current result:** ___

---

### P15-REC-05 · Reconcile clears stale batch_id when doc moved away from batch destination
_Origin: implementation · Granularity: mechanism_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-REC-05
```

**Run:**
**Prerequisite:** TD-040 must be resolved — single-file capture must set batch_id.

**Step 1 — capture the file to seed a batch_id:**

```bash
uv run kms capture /tmp/ai_kms_test_vault/Projects/Alpha/note-batch-test.md
```

**Step 2 — verify batch_id is set:**

```bash
sqlite3 data/kb.db "SELECT vault_path, batch_id FROM documents WHERE vault_path LIKE '%note-batch-test%'"
```

Confirm batch_id column shows a non-NULL integer.

**Step 3 — move the file to a different project:**

```bash
mkdir -p /tmp/ai_kms_test_vault/Projects/Beta && mv /tmp/ai_kms_test_vault/Projects/Alpha/note-batch-test.md /tmp/ai_kms_test_vault/Projects/Beta/note-batch-test.md
```

**Step 4 — run reconcile:**

```bash
uv run kms reconcile
```

**Step 5 — verify batch_id cleared:**

```bash
sqlite3 data/kb.db "SELECT vault_path, batch_id FROM documents WHERE vault_path LIKE '%note-batch-test%'"
```

**Check:**
- [ ] After Step 2: batch_id is a non-NULL integer for Projects/Alpha/note-batch-test.md
- [ ] After Step 5: batch_id is NULL for Projects/Beta/note-batch-test.md

**Last tested:** 2026-06-07
**Last result:** failed - no single-file capturing with batch_id yet (application gap)
**Current result:** ___

---

### P15-HDL-01 · XLSX file dropped in inbox gets real-summary marker with needs-review status
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
- [ ] q2-budget.xlsx stays in inbox/ (not moved — CLUELESS path)
- [ ] Marker .md created at inbox/.summaries/q2-budget.xlsx.md
- [ ] Marker frontmatter has type: attachment-summary
- [ ] Marker frontmatter has status: needs-review (NOT pending-routing — concept retired in P2-CIC Phase 7)
- [ ] Marker frontmatter has source_hash set (idempotent re-entry)
- [ ] Marker body contains a real AI-generated summary (NOT placeholder)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-HDL-02 · PPTX file dropped in inbox gets real-summary marker with needs-review status
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
- [ ] deck.pptx stays in inbox/ (not moved — CLUELESS path)
- [ ] Marker .md created at inbox/.summaries/deck.pptx.md
- [ ] Marker frontmatter has type: attachment-summary
- [ ] Marker frontmatter has status: needs-review (NOT pending-routing)
- [ ] Marker frontmatter has source_hash set (idempotent re-entry)
- [ ] Marker body contains a real AI-generated summary

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-HDL-03 · CSV file dropped in inbox gets real-summary marker with needs-review status
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
- [ ] data.csv stays in inbox/ (not moved — CLUELESS path)
- [ ] Marker .md created at inbox/.summaries/data.csv.md
- [ ] Marker frontmatter has type: attachment-summary
- [ ] Marker frontmatter has status: needs-review (NOT pending-routing)
- [ ] Marker frontmatter has source_hash set
- [ ] Marker body contains a real AI-generated summary

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-HDL-04 · HTML file dropped in inbox gets real-summary marker with needs-review status
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
- [ ] page.html stays in inbox/ (not moved — CLUELESS path)
- [ ] Marker .md created at inbox/.summaries/page.html.md
- [ ] Marker frontmatter has type: attachment-summary
- [ ] Marker frontmatter has status: needs-review (NOT pending-routing)
- [ ] Marker frontmatter has source_hash set
- [ ] Marker body contains a real AI-generated summary

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-HDL-05 · EML email file dropped in inbox gets real-summary marker with needs-review status
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
- [ ] message.eml stays in inbox/ (not moved — CLUELESS path)
- [ ] Marker .md created at inbox/.summaries/message.eml.md
- [ ] Marker frontmatter has type: attachment-summary
- [ ] Marker frontmatter has status: needs-review (NOT pending-routing)
- [ ] Marker frontmatter has source_hash set
- [ ] Marker body contains a real AI-generated summary

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-HDL-06 · MSG Outlook file dropped in inbox gets real-summary marker with needs-review status
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
- [ ] outlook-msg.msg stays in inbox/ (not moved — CLUELESS path)
- [ ] Marker .md created at inbox/.summaries/outlook-msg.msg.md
- [ ] Marker frontmatter has type: attachment-summary
- [ ] Marker frontmatter has status: needs-review (NOT pending-routing)
- [ ] Marker frontmatter has source_hash set
- [ ] Marker body contains a real AI-generated summary

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

Verify batches row created:

```bash
sqlite3 data/kb.db "SELECT folder_name, destination_type, destination_name, confidence, status FROM batches ORDER BY rowid DESC LIMIT 5"
```

If status=ROUTING: verify files captured with batch_id:

```bash
sqlite3 data/kb.db "SELECT vault_path, batch_id FROM documents WHERE batch_id = (SELECT batch_id FROM batches ORDER BY rowid DESC LIMIT 1)"
```

**Check:**
- [ ] batches row created with folder_name=new-project-folder, confidence > 0, status one of: ROUTING | PENDING_REVIEW | CLUELESS
- [ ] AUTO (status=ROUTING): folder moved to Projects/`<name>`/ or Domain/`<name>`/; files captured; documents rows present with matching batch_id
- [ ] SUGGEST (status=PENDING_REVIEW): folder stays in inbox/; no documents rows for its files yet
- [ ] CLUELESS (status=CLUELESS): folder stays in inbox/; per-file needs-review markers with real summaries at inbox/.summaries/; documents rows with batch_id
- [ ] All files inside the folder (including subfolders of Projects/`<A>`/ or Domain/`<D>`/) captured with a batch_id

**Last tested:** 2026-06-07
**Last result:** failed
**Current result:** ___

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

**Last tested:** 2026-06-07
**Last result:** passed
**Current result:** ___

---

### PRE2-DOM-01 · Old domain: scalar stripped by reconcile (lazy migration — Stage 5)
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh PRE2-DOM-01
```

**Run:**
Run reconcile:

```bash
uv run kms reconcile
```

Open `Domain/Finance/test-pre2-dom-01.md` and inspect frontmatter:

**Check:**
- [ ] domain: scalar key GONE from frontmatter
- [ ] domain/Finance still present in tags: list
- [ ] type/capture still present in tags: list
- [ ] Body content unchanged

**Last tested:** 2026-06-07
**Last result:** failed - all frontmatter changed
**Current result:** ___

---

### Phase Vault-Restructure — Editable/No-Edit Split

---

### VR-PLACE-01 · No-edit binary (PDF/PNG/JPG) routed to attachment/
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh VR-PLACE-01
```

**Run:**
```bash
uv run kms capture Projects/Alpha/vr-place-01-test.pdf
```

**Check:**
- [ ] vr-place-01-test.pdf GONE from Projects/Alpha/ (moved to attachment/)
- [ ] Binary appears at Projects/Alpha/attachment/vr-place-01-test.pdf
- [ ] Sibling .md at Projects/Alpha/attachment/.summaries/vr-place-01-test.pdf.md
- [ ] Sibling frontmatter has type: attachment-summary

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### VR-PLACE-02 · Editable binary (XLSX/DOCX/PPTX) routed to project/domain root
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh VR-PLACE-02
```

**Run:**
Run capture on the editable binary:

```bash
uv run kms capture Projects/Alpha/vr-place-02-test.xlsx
```

**Check:**
- [ ] Binary still at Projects/Alpha/vr-place-02-test.xlsx (NOT moved to attachment/)
- [ ] Sibling .md written (NOT under attachment/ — editable stays at root)

**Last tested:** 2026-06-10
**Last result:** passed
**Current result:** ___

---

### VR-SKIP-01 · Watcher and scan skip AI-output folders (Briefings/Synthesis/Documentation)
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh VR-SKIP-01
```

**Run:**
Run scan:

```bash
uv run kms capture --scan
```

Verify no DB row created for the Briefings/ file:

```bash
sqlite3 data/kb.db "SELECT count(*) FROM documents WHERE vault_path LIKE 'Briefings/%'"
```

**Check:**
- [ ] File in Briefings/ NOT captured (skipped)
- [ ] No DB row created in documents table for the file (count = 0)

**Last tested:** 2026-06-10
**Last result:** Passed
**Current result:** ___

---

### VR-REHOME-01 · Misplaced binary re-homed to correct placement
_Origin: implementation · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh VR-REHOME-01
```

**Run:**
Run reconcile (stage 7 detects editable binary in attachment/ and moves it to root):

```bash
uv run kms reconcile
```

**Check:**
- [ ] vr-rehome-01-test.xlsx GONE from Projects/Alpha/attachment/
- [ ] File appears at Projects/Alpha/vr-rehome-01-test.xlsx (project root, visible in Obsidian)
- [ ] Sibling .md path updated to reflect new location

**Last tested:** 2026-06-10
**Last result:** passed
**Current result:** ___

---

### Phase 2

---

### P2-REC-01 · reconcile strips deprecated frontmatter key from a note that already has valid tags and correct project field
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-REC-01
```

**Run:**
Run reconcile:

```bash
uv run kms reconcile
```

Open `Projects/Alpha/note.md` and inspect frontmatter.

**Check:**
- [ ] `domain: finance` key absent from frontmatter
- [ ] `project: Alpha` unchanged
- [ ] `tags:` list unchanged (still empty)

**Last tested:** 2026-06-10
**Last result:** passed
**Current result:** ___

---

### P2-REC-02 · reconcile leaves a human-locked note untouched even if it has a deprecated frontmatter key
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-REC-02
```

**Run:**
Run reconcile:

```bash
uv run kms reconcile
```

Open `Projects/Alpha/locked.md` and inspect frontmatter.

**Check:**
- [ ] `domain: finance` still present in frontmatter (not stripped)
- [ ] `updated_by_human: true` unchanged
- [ ] No other frontmatter fields modified

**Last tested:** 2026-06-10
**Last result:** passed
**Current result:** ___

---

### P2-REC-03 · reconcile does not write a note that has no deprecated keys and no other dirty reason
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-REC-03
```

**Run:**
Run reconcile:

```bash
uv run kms reconcile
```

Check the note's last-modified timestamp before and after — it should not change.

**Check:**
- [ ] Note frontmatter unchanged (no unexpected fields added or removed)
- [ ] File modification time unchanged (reconcile did not rewrite it)

**Last tested:** 2026-06-10
**Last result:** passed
**Current result:** ___

---

### P2-CIC-01 · Loose inbox .md note with a confident destination is moved into the project/domain folder DERIVED from its assigned tags+project (AUTO)
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-CIC-01
```

**Run:**
Place a note in inbox/ whose content clearly belongs to an existing project (e.g. meeting notes for Projects/Alpha).

Ensure Projects/Alpha/ exists with a CLAUDE.md domain tag.

```bash
uv run kms capture inbox/p2cic-01-clear-project-note.md
```

**Check:**
- [ ] Note is GONE from inbox/ (moved to the DERIVED folder, e.g. Projects/Alpha/p2cic-01-clear-project-note.md)
- [ ] Frontmatter has project: Alpha AND the destination folder matches that project field (location derived from the field, not picked independently)
- [ ] No status: needs-review field (AUTO acted, not suggested)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-CIC-02 · Loose inbox note with a medium-confidence destination stays in inbox with suggested destination recorded (SUGGEST)
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-CIC-02
```

**Run:**
Place a note in inbox/ whose content plausibly fits a destination but is not clear-cut.

```bash
uv run kms capture inbox/p2cic-02-ambiguous-note.md
```

**Check:**
- [ ] Note STAYS at inbox/p2cic-02-ambiguous-note.md (not moved)
- [ ] Frontmatter records a suggested destination (e.g. status: needs-review plus a suggested-destination field)
- [ ] Frontmatter records the AI confidence and one-sentence reasoning

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-CIC-03 · Loose inbox note the AI cannot place stays in inbox marked stuck (CLUELESS)
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-CIC-03
```

**Run:**
Place a note in inbox/ whose topic matches no existing project or domain.

```bash
uv run kms capture inbox/p2cic-03-no-match-note.md
```

**Check:**
- [ ] Note STAYS at inbox/p2cic-03-no-match-note.md (not moved)
- [ ] Frontmatter records that the AI was stuck (low/no candidate) with its reasoning
- [ ] No destination move occurred

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-CIC-04 · A file dropped directly into a project/domain folder is filed by location WITHOUT any AI classify call
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-CIC-04
```

**Run:**
Place a note directly inside Projects/Alpha/.

```bash
uv run kms capture Projects/Alpha/p2cic-04-located-note.md
```

**Check:**
- [ ] Note stays in Projects/Alpha/ (location wins; no move)
- [ ] Frontmatter has project: Alpha
- [ ] No suggested-destination / needs-review fields written (classify was skipped)

**Last tested:** 2026-06-10
**Last result:** passed
**Current result:** ___

---

### P2-CIC-05 · Loose inbox PDF gets a rich attachment summary AND is classified at drop time (two AI calls; pending-routing marker retired)
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-CIC-05
```

**Run:**
Place a PDF in inbox/ whose content clearly belongs to an existing project.

```bash
uv run kms capture inbox/p2cic-05-clear-project-report.pdf
```

**Check:**
- [ ] Sibling .md has a real summary (NOT the one-line pending-routing placeholder)
- [ ] On AUTO: binary moved to the chosen folder's attachment/, sibling moved alongside, both findable
- [ ] On SUGGEST/CLUELESS: binary stays in inbox; sibling records the suggested destination or stuck state

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-CIC-10 · A single file dropped in a project/domain folder is still summarized + frontmatter-stamped; only the classify step and the move are skipped (LOCATED is NOT a no-op)
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-CIC-10
```

**Run:**
Place a note directly inside Projects/Alpha/ with body content worth summarizing.

```bash
uv run kms capture Projects/Alpha/p2cic-10-located-summary-note.md
```

**Check:**
- [ ] Note stays in Projects/Alpha/ (no move)
- [ ] Frontmatter has a real AI summary and project: Alpha (earlier capture stages ran)
- [ ] No suggested-destination / classify_confidence / needs-review fields (classify step was skipped)
- [ ] No stage='classify' audit row for this file (LOCATED — no classify AI call)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-CIC-11 · When the AI assigns a project, the note moves to that Project folder even though it also carries domain tags (project beats domain — precedence)
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-CIC-11
```

**Run:**
Place a loose inbox note that clearly belongs to an existing project AND carries one or more domain tags (e.g. a deliverable for Projects/Alpha that is also about domain/Finance).

Ensure both Projects/Alpha/ and Domain/Finance/ exist.

```bash
uv run kms capture inbox/p2cic-11-project-and-domain-note.md
```

**Check:**
- [ ] Note moves to Projects/Alpha/ (the Project root), NOT Domain/Finance/ — project field wins over the domain tag
- [ ] Frontmatter still carries the domain/Finance tag (tags are not stripped; only the move follows precedence)
- [ ] Frontmatter project: Alpha

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-CIC-12 · A loose note with NO project and MULTIPLE domain tags moves to the AI's designated PRIMARY domain folder while keeping all its domain tags
_Origin: design · Granularity: outcome_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-CIC-12
```

**Run:**
Place a loose inbox note that spans two existing domains (e.g. tagged domain/Finance and domain/Legal) and ties to no specific project.

Ensure Domain/Finance/ and Domain/Legal/ both exist.

```bash
uv run kms capture inbox/p2cic-12-multi-domain-note.md
```

**Check:**
- [ ] Note moves to exactly ONE domain folder — the AI's designated primary (e.g. Domain/Finance/), not both
- [ ] Frontmatter still lists BOTH domain tags (domain/Finance AND domain/Legal) — only the move uses the primary
- [ ] Frontmatter records no project (no project field set)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### Phase 3

---

### P3-SRCH-01 · A question finds a semantically related note even when the wording differs (vector match, not just keywords)
_Origin: design · Granularity: outcome_

**Run:**
Capture a note whose summary/body describes "managing pushback in meetings".

Run: uv run kms search "stakeholder resistance"

**Check:**
- [ ] The pushback note appears in the result list
- [ ] Each result block shows the note's real title, a score, and a snippet

**Last tested:** 2026-06-11
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P3-SRCH-02 · A project filter with no question returns every note in that project, newest first (filter-only mode)
_Origin: design · Granularity: outcome_

**Run:**
Capture several notes into Projects/Alpha/ over time.

Run: uv run kms search --project Alpha

**Check:**
- [ ] All Alpha notes are returned with no query term required
- [ ] Results are ordered most-recently-updated first

**Last tested:** 2026-06-11
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P3-SRCH-03 · A question plus a project filter searches semantically but only within that project
_Origin: design · Granularity: outcome_

**Run:**
Capture notes into two projects, both touching the query topic.

Run: uv run kms search "budget Q3" --project Alpha

**Check:**
- [ ] Only Alpha notes appear in the results
- [ ] Results are ranked by relevance to the question

**Last tested:** 2026-06-11
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P3-SRCH-05 · A result for a binary's sibling summary shows a usable title, not the raw "report.pdf.md" filename
_Origin: design · Granularity: outcome_

**Run:**
Capture a PDF/binary so its sibling summary note is indexed.

Run a query that matches the binary's content.

**Check:**
- [ ] The result's title is the human-readable note title from the index

**Last tested:** 2026-06-11
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P3-SRCH-07 · Rebuilding both indexes is idempotent — running reindex twice yields identical results
_Origin: design · Granularity: outcome_

**Run:**
Run: uv run kms search --reindex

Run the same reindex a second time.

**Check:**
- [ ] The reindex reports a count of notes processed
- [ ] Second run produces no duplicate index rows

**Last tested:** 2026-06-11
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P3-SRCH-10 · A captured binary's sibling note carries an AI-generated descriptive title in frontmatter
_Origin: design · Granularity: outcome_

**Run:**
Capture a PDF/binary so its sibling summary note is written and indexed.

Open the sibling .md and inspect its frontmatter.

**Check:**
- [ ] Sibling frontmatter carries a descriptive title field (the AI's title)
- [ ] The documents.title for the sibling row is the descriptive title

**Last tested:** 2026-06-11
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

## FULL — Developer Only (Terminal + DB Access)

---

### Phase 1 — Capture Pipeline

---

### P1-DEV-01 · Audit trail written for every capture decision
_Origin: implementation · Granularity: mechanism_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P1-DEV-01
```

**Run:**
Capture the test file:

```bash
uv run kms capture inbox/p1-dev-01-test.md
```

Then query:

```bash
sqlite3 data/kb.db "SELECT stage, outcome FROM audit_log ORDER BY rowid DESC LIMIT 5"
```

**Check:**
- [ ] audit_log row exists with stage=metadata, outcome=CAPTURED
- [ ] audit_log row exists with stage=classify (AUTO, SUGGEST, or CLUELESS — classify_step runs on inbox files)
- [ ] correlation_id matches the ID shown in terminal output
- [ ] reasoning field is non-empty in both rows

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P1-DEV-02 · Tag violation audited — invalid tags stripped and logged
_Origin: implementation · Granularity: mechanism_

**Run:**
Force a capture where LLM returns a tag not in tags.yaml taxonomy (developer test — requires mocking LLM response). Then query the audit log:

```bash
sqlite3 data/kb.db "SELECT stage, outcome, reasoning FROM audit_log WHERE stage='tag_violation' ORDER BY rowid DESC LIMIT 3"
```

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

Then verify audit log entry and no DB row created:

```bash
sqlite3 data/kb.db "SELECT stage, outcome FROM audit_log WHERE stage='file_lost' ORDER BY rowid DESC LIMIT 3"
```

```bash
sqlite3 data/kb.db "SELECT count(*) FROM documents WHERE vault_path='inbox/nonexistent-file.md'"
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

Then verify rejection was logged (look for 'cooldown' or 'too recent' in logs):

```bash
grep -i 'cooldown\|too_recent\|file_too_recent' logs/app.log | tail -5
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

### Phase 1.5 — Location Tags + Attachment Layout + Reconcile

---

### P15-DEV-01 · Watcher binary-delete sync — sibling cleaned up
_Origin: implementation · Granularity: mechanism_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-DEV-01
```

**Run:**
First, capture the binary to establish sibling in DB:

```bash
uv run kms capture Projects/Alpha/attachment/p15-dev-01-report.pdf
```

In Terminal 1, start the watcher:

```bash
uv run kms watch
```

In Terminal 2, delete the binary and wait ~5 seconds:

```bash
rm $VAULT/Projects/Alpha/attachment/p15-dev-01-report.pdf
```

In Terminal 2, verify sibling removed from DB (count should be 0) and audit entry written:

```bash
sqlite3 data/kb.db "SELECT count(*) FROM documents WHERE vault_path LIKE '%p15-dev-01-report.pdf.md%'"
```

```bash
sqlite3 data/kb.db "SELECT stage, outcome FROM audit_log WHERE stage='watcher:binary_delete' ORDER BY rowid DESC LIMIT 3"
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

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-DEV-02
```

**Run:**
First, capture the binary to establish sibling in DB:

```bash
uv run kms capture Projects/Alpha/attachment/p15-dev-02-old.pdf
```

In Terminal 1, start the watcher:

```bash
uv run kms watch
```

In Terminal 2, rename the binary and wait ~5 seconds:

```bash
mv $VAULT/Projects/Alpha/attachment/p15-dev-02-old.pdf $VAULT/Projects/Alpha/attachment/p15-dev-02-new.pdf
```

In Terminal 2, verify DB row updated with new path and attachment_path:

```bash
sqlite3 data/kb.db "SELECT vault_path, attachment_path FROM documents WHERE vault_path LIKE '%p15-dev-02-new.pdf.md%'"
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

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-DEV-03
```

**Run:**
First, capture the binary to establish sibling in DB:

```bash
uv run kms capture Projects/Alpha/attachment/p15-dev-03-file.pdf
```

Create the destination folder:

```bash
mkdir -p $VAULT/Projects/Beta/attachment
```

In Terminal 1, start the watcher:

```bash
uv run kms watch
```

In Terminal 2, move the binary to a different project's attachment/ and wait ~5 seconds:

```bash
mv $VAULT/Projects/Alpha/attachment/p15-dev-03-file.pdf $VAULT/Projects/Beta/attachment/p15-dev-03-file.pdf
```

In Terminal 2, verify old sibling DB row removed (count should be 0):

```bash
sqlite3 data/kb.db "SELECT count(*) FROM documents WHERE vault_path LIKE '%Projects/Alpha/attachment/.summaries/p15-dev-03-file.pdf.md%'"
```

**Check:**
- [ ] Old sibling .md in Projects/Alpha/attachment/.summaries/ deleted from disk
- [ ] DB row for old sibling removed (count = 0)
- [ ] New orphan binary in Projects/Beta/attachment/ picked up on next `uv run kms reconcile`

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-04 · Reconcile stage 3 — re-summarize stale binaries
_Origin: implementation · Granularity: mechanism_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-DEV-04
```

**Run:**
First, capture the binary to establish sibling in DB and record the initial content_hash:

```bash
uv run kms capture Projects/Alpha/attachment/p15-dev-04-report.pdf
```

```bash
sqlite3 data/kb.db "SELECT vault_path, content_hash FROM documents WHERE vault_path LIKE '%p15-dev-04-report.pdf.md%'"
```

Touch the binary to make it newer than the sibling .md:

```bash
touch $VAULT/Projects/Alpha/attachment/p15-dev-04-report.pdf
```

Run reconcile:

```bash
uv run kms reconcile
```

Verify sibling DB row has an updated content_hash:

```bash
sqlite3 data/kb.db "SELECT vault_path, content_hash FROM documents WHERE vault_path LIKE '%p15-dev-04-report.pdf.md%'"
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

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-DEV-05
```

**Run:**
Clear any prior DB row for the test file:

```bash
sqlite3 data/kb.db "DELETE FROM documents WHERE vault_path='inbox/p15-dev-05-debounce.md'"
```

In Terminal 1, start the watcher:

```bash
uv run kms watch
```

In Terminal 2, touch the file 5 times within 1 second:

```bash
for i in 1 2 3 4 5; do touch $VAULT/inbox/p15-dev-05-debounce.md; done
```

After the debounce window (~3 sec), verify only one DB row exists (count should be 1):

```bash
sqlite3 data/kb.db "SELECT count(*) FROM documents WHERE vault_path='inbox/p15-dev-05-debounce.md'"
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

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P15-DEV-06
```

**Run:**
Run scan:

```bash
uv run kms capture --scan
```

Verify the sibling fixture was NOT captured as a new document (count should be 0):

```bash
sqlite3 data/kb.db "SELECT count(*) FROM documents WHERE vault_path='Projects/Alpha/attachment/.summaries/p15-dev-06-test.pdf.md'"
```

**Check:**
- [ ] Sibling .md files inside .summaries/ NOT re-captured by scan
- [ ] No DB row created for p15-dev-06-test.pdf.md (count = 0)
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
cp tests/fixtures/sample.md /tmp/ai_kms_test_vault/inbox/ && sleep 5 && touch /tmp/ai_kms_test_vault/inbox/sample.md
```

In Terminal 2, check logs for the skip marker and verify only one capture fired:

```bash
grep 'skip_in_flight' logs/app.log | tail -5
```

```bash
grep 'metadata.captured' logs/app.log | tail -5
```

Verify only one audit entry for this path:

```bash
sqlite3 data/kb.db "SELECT count(*), stage, outcome FROM audit_log WHERE outcome='CAPTURED' GROUP BY stage, outcome ORDER BY rowid DESC LIMIT 5"
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

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh VR-CONTENT-01
```

**Run:**
Ensure the binary has been captured first (creates sibling in .summaries/):

```bash
uv run kms capture Projects/Alpha/attachment/vr-content-01-test.xlsx
```

Record the current content_hash:

```bash
sqlite3 data/kb.db "SELECT vault_path, content_hash FROM documents WHERE vault_path LIKE '%vr-content-01%'"
```

In Terminal 1, start the watcher:

```bash
uv run kms watch
```

In Terminal 2, edit the XLSX content (actual data change, not just touch) and wait ~5 seconds for watcher to detect SHA-256 change.

Verify content_hash changed:

```bash
sqlite3 data/kb.db "SELECT vault_path, content_hash FROM documents WHERE vault_path LIKE '%vr-content-01%'"
```

**Check:**
- [ ] Sibling .md re-summarized with updated content
- [ ] source_hash updated in sibling frontmatter (open .summaries/vr-content-01-test.xlsx.md to verify)
- [ ] content_hash in DB row differs from pre-edit value

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### Phase 2

---

### P2-BAT-01 · Single file captured into a project subfolder gets batch_id set in the documents row
_Origin: design · Granularity: outcome_

**Run:**
Drop a file into Projects/Alpha/subdir/ and run `uv run kms capture Projects/Alpha/subdir/report.pdf`. Query the DB: `sqlite3 data/kb.db 'SELECT vault_path, batch_id FROM documents WHERE vault_path LIKE "%report%"'`

**Check:**
- [ ] batch_id column is non-NULL; the value references a row in the batches table with folder_path = 'Projects/Alpha/subdir'

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-BAT-02 · Two files dropped separately into the same subfolder share the same batch_id
_Origin: design · Granularity: outcome_

**Run:**
Capture two files from the same subfolder in separate CLI invocations. Query batch_id for both vault_path rows.

**Check:**
- [ ] Both documents rows have identical batch_id values; the batches row for that folder_path has file_count >= 2

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-BAT-03 · Single file captured directly into inbox root or vault root gets no batch_id
_Origin: design · Granularity: outcome_

**Run:**
Capture a file dropped directly into inbox/ (not inside a subfolder). Query batch_id for the resulting documents row.

**Check:**
- [ ] batch_id column is NULL — inbox root is not batch-worthy

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-BAT-05 · scan_capture skips a subfolder already captured by the watcher (no duplicate batch)
_Origin: design · Granularity: outcome_

**Run:**
Start watcher, drop a subfolder into inbox/. After watcher captures it, stop watcher and run `kms capture --scan`.

**Check:**
- [ ] No second batches row created for the same folder_path. Existing documents rows unchanged. Log shows subfolder skipped.

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-BAT-06 · File moved into a batch-worthy subfolder by the watcher gets batch_id updated in-place
_Origin: design · Granularity: outcome_

**Run:**
Start watcher. Move a previously-captured file from inbox/ into Projects/Alpha/subdir/. Query batch_id for the updated documents row.

**Check:**
- [ ] batch_id updated to the batch for Projects/Alpha/subdir. No re-capture triggered (summary and content_hash unchanged).

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-CIC-06 · Every classify outcome (AUTO/SUGGEST/CLUELESS) writes exactly one decision-log audit row
_Origin: design · Granularity: mechanism_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-CIC-06
```

**Run:**
```bash
uv run kms capture inbox/p2cic-06-audit-note.md
```

```bash
sqlite3 data/kb.db "SELECT pipeline, stage, outcome, reasoning, source_ids FROM audit_log WHERE stage='classify' ORDER BY rowid DESC LIMIT 3"
```

**Check:**
- [ ] An audit_log row exists with pipeline=capture, stage=classify
- [ ] outcome is one of AUTO | SUGGEST | CLUELESS
- [ ] reasoning is non-empty; source_ids contains the note's vault path

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-CIC-07 · A classify AUTO-move to a project/domain root leaves batch_id NULL; index location stays consistent
_Origin: design · Granularity: mechanism_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-CIC-07
```

**Run:**
Capture a loose inbox note that AUTO-routes to a project ROOT (e.g. Projects/Alpha/).

```bash
sqlite3 data/kb.db "SELECT vault_path, batch_id FROM documents WHERE vault_path LIKE '%p2cic-07-batch-note%'"
```

**Check:**
- [ ] documents.vault_path equals the new in-folder path (e.g. Projects/Alpha/p2cic-07-batch-note.md) — matches disk
- [ ] batch_id is NULL (a tree root is not a batch-worthy subfolder)
- [ ] Exactly one documents row exists for the note (no orphan row at the old inbox path)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-CIC-08 · A classify AUTO-move under the running watcher does not double-fire re-home or create a duplicate row
_Origin: design · Granularity: mechanism_

**Run:**
Start kms watch.

Drop a loose inbox file whose content clearly belongs to a project, wait for capture to AUTO-route it.

```bash
sqlite3 data/kb.db "SELECT count(*), vault_path FROM documents WHERE vault_path LIKE '%<that-file>%' GROUP BY vault_path"
```

**Check:**
- [ ] Exactly one documents row, pointing at the new destination path
- [ ] Watcher re-home was suppressed for the pipeline-initiated move (log shows rehome_skip reason=pipeline_initiated)
- [ ] No second move back to inbox; the file stays at its classified destination

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-CIC-09 · A file captured as part of a dropped folder is fully captured but NOT individually re-classified (SUPPRESS — the folder is the routing unit)
_Origin: design · Granularity: mechanism_

**Run:**
Drop a folder containing 2-3 loose files into inbox/ (a folder the system routes or marks CLUELESS).

Let the folder capture flow process it (watcher stable-folder event, or capture_folder).

`sqlite3 data/kb.db "SELECT count(*) FROM audit_log WHERE stage='classify'"` and inspect each file's frontmatter.

**Check:**
- [ ] Each file inside the folder is summarized + frontmatter-stamped + batch-stamped (full capture ran)
- [ ] NO file inside the folder carries suggested_type / suggested_name / classify_confidence / status: needs-review from an individual classify call
- [ ] No per-file stage='classify' audit row is written for the folder's files (the folder verdict is the only classify decision)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P2-CIC-13 · Structural consistency: an AUTO-filed note's on-disk folder always matches its stamped project/primary-domain (no free pick that could disagree)
_Origin: design · Granularity: mechanism_

**Setup:**
```bash
bash docs/system_behavior/setup_test_vault.sh P2-CIC-13
```

**Run:**
Capture any loose inbox note that AUTO-routes.

`sqlite3 data/kb.db "SELECT vault_path, project FROM documents WHERE vault_path LIKE '%p2cic-13-consistency-note%'"` and read the note's frontmatter.

Compare the folder portion of vault_path against the stamped project (or primary domain) field.

**Check:**
- [ ] If a project is stamped, vault_path is under Projects/<that project>/
- [ ] If no project but a primary domain, vault_path is under Domain/<that primary domain>/
- [ ] The folder NEVER points at a project/domain the frontmatter does not name (derived, not free pick)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### Phase 3

---

### P3-SRCH-04 · Every search result carries a triage payload (handle, summary, snippet, score, metadata) and never the full note body
_Origin: design · Granularity: mechanism_

**Run:**
Run any query that returns at least one result.

**Check:**
- [ ] Each result has vault_path, summary, snippet, score, and metadata
- [ ] No result contains the full note body

**Last tested:** 2026-06-11
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P3-SRCH-06 · Search skips index rows whose underlying note was deleted instead of crashing
_Origin: design · Granularity: mechanism_

**Run:**
Leave an index entry whose documents row was removed.

Run a query that would otherwise return that entry.

**Check:**
- [ ] Search returns the other valid results and silently skips the stale entry

**Last tested:** 2026-06-11
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P3-SRCH-08 · A date-range filter with no question returns recent notes (supports a future weekly synthesis caller)
_Origin: design · Granularity: outcome_

**Run:**
Capture notes across several days.

Run: uv run kms search --since 7d

**Check:**
- [ ] Only notes updated within the window are returned, newest first

**Last tested:** 2026-06-11
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P3-SRCH-09 · Classify no longer accepts a domain name as a valid project destination (cross-type leak closed, TD-051)
_Origin: design · Granularity: mechanism_

**Run:**
Drive classify() so the AI response puts a domain name in the project field.

**Check:**
- [ ] classify() returns Failure(recoverable=True)
- [ ] project validated only against project names, primary_domain only against domain names

**Last tested:** 2026-06-11
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### Phase 5

---

### P5-DATA-01 · The new knowledge_entries table exists after the database is initialized, with all eleven columns
_Origin: design · Granularity: outcome_

**Run:**
Initialize a fresh database (run any command that calls init_db, or init_db directly against a temp db path).

Inspect the schema: `sqlite3 <db> "PRAGMA table_info(knowledge_entries)"`

Check the version: `sqlite3 <db> "SELECT version FROM schema_version"`

**Check:**
- [ ] knowledge_entries table exists with columns id, dimension, entity, tag, fact, status, confidence, sources, reasoning, created_at, updated_at
- [ ] schema_version is 8

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DATA-02 · The documents table gains three new nullable columns and every existing row reads back with them NULL (no breakage)
_Origin: design · Granularity: outcome_

**Run:**
Initialize a database that already contains captured documents rows (migration 008 applied on top).

Inspect the schema: `sqlite3 <db> "PRAGMA table_info(documents)"`

Read an existing row: `sqlite3 <db> "SELECT full_body, original_filename, file_size_bytes FROM documents LIMIT 1"`

**Check:**
- [ ] documents has the new columns full_body (TEXT), original_filename (TEXT), file_size_bytes (INTEGER), all nullable
- [ ] The three new columns read back as NULL on every pre-existing row (nothing populates them in Slice 1)
- [ ] get_by_path returns a DocumentRow without error for an existing row

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DATA-03 · A knowledge entry can be created and read back by dimension, carrying its fact, status, confidence, and source list
_Origin: design · Granularity: outcome_

**Run:**
Call upsert() to store an entry (e.g. dimension=people, entity=Anthony, tag=role, fact="Product Lead for Movie Q2", status=confident, a confidence score, sources=[a document reference]).

Call query_by_dimension("people") and inspect the returned rows.

**Check:**
- [ ] upsert returns Success with the new row id
- [ ] query_by_dimension returns the entry with its fact, status, confidence, and source list intact (sources round-trips as a list)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DATA-04 · Querying by entity returns only that entity's entries across its tags
_Origin: design · Granularity: outcome_

**Run:**
Store two entries for one entity under different tags, plus one entry for a different entity.

Call query_by_entity for the first entity.

**Check:**
- [ ] Only the two entries for that entity are returned; the other entity's entry is excluded
- [ ] Each returned entry carries its own tag and fact

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DATA-05 · Retiring an entry flips its status to retired with a reason and never deletes the row
_Origin: design · Granularity: outcome_

**Run:**
Store a confident entry, then call retire() on it with a reason string.

Read the row back by its id.

**Check:**
- [ ] The row still exists (not deleted)
- [ ] status is now retired and reasoning records the supplied reason
- [ ] updated_at is refreshed

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DATA-06 · get_confident_and_pending returns confident and pending entries but excludes retired ones (the set fed to the extraction prompt)
_Origin: design · Granularity: outcome_

**Run:**
Store three entries for one entity — one confident, one pending, one retired.

Call get_confident_and_pending for that entity (or dimension).

**Check:**
- [ ] The confident and pending entries are returned
- [ ] The retired entry is NOT returned (retired entries are excluded from extraction input)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DATA-07 · A valid dimension/tag pair is accepted and an invented one is rejected
_Origin: design · Granularity: outcome_

**Run:**
Load the dimension/tag config.

Call validate_dimension_tag("people", "role", config) and validate_dimension_tag("people", "invented_tag", config).

**Check:**
- [ ] The valid pair returns Success
- [ ] The invalid tag returns Failure (unknown tag for that dimension)
- [ ] An unknown dimension also returns Failure

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DATA-08 · Every dimension in the config carries a mandatory "other" catch-all tag
_Origin: design · Granularity: outcome_

**Run:**
Load the dimension/tag config.

For each configured dimension, call validate_dimension_tag(dimension, "other", config).

**Check:**
- [ ] The tag "other" validates as a known tag for every dimension (catch-all present everywhere)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DATA-09 · A confidence score maps to a confident/pending status using thresholds read from config, not hardcoded floats
_Origin: design · Granularity: mechanism_

**Run:**
Feed a high confidence score (above the configured confident threshold) and a low one (below it) through the status mapping.

Inspect the source: confirm the threshold comes from config, not a literal in code.

**Check:**
- [ ] High score maps to status confident; low score maps to status pending
- [ ] The threshold value is read from config (no float literal in an if/elif drives the mapping)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DATA-10 · The full existing test suite stays green after the additive migration and new modules land, except the expected migration version-pin bump
_Origin: design · Granularity: mechanism_

**Run:**
Run the full suite: `uv run pytest`

**Check:**
- [ ] All previously-passing tests still pass, EXCEPT the two version-pin assertions in tests/test_storage/test_migration_007.py (lines 41, 56) which are updated 7->8 as the expected migration-version bump (research A1, 2026-06-12) — not a regression
- [ ] No other existing test is rewritten or deleted to accommodate Slice 1
- [ ] documents.upsert() still accepts a WriteOutcome (signature unchanged)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-01 · The container image builds for the cloud target platform and the container starts and reports itself alive on the one shared port
_Origin: design · Granularity: outcome_

**Run:**
Build the image targeting linux/amd64.

Run the container mapping port 8080.

Hit the health path: `curl -s http://localhost:8080/health`

**Check:**
- [ ] The image build completes without error
- [ ] The container starts and stays up
- [ ] GET /health returns HTTP 200 with a small ok body (e.g. {"status":"ok"})
- [ ] /health needs no secret key (it is open)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-02 · The existing knowledge-assistant (MCP) interface answers a tool-list request on the same single port, served from the HTTP entry point
_Origin: design · Granularity: outcome_

**Run:**
Start the container.

Send an MCP tools/list request to the MCP path on port 8080.

**Check:**
- [ ] The MCP server responds with the registered tool list (the five existing KMS tools)
- [ ] It is reachable on the same port as /health and /api/* (one port, different paths)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-03 · A first upload of a new file stores a document record carrying its full extracted text and returns the new record id
_Origin: design · Granularity: outcome_

**Run:**
POST a payload to /api/upload with the correct Authorization bearer key: a new vault_path, extracted_text, a content_hash, original_filename, file_size_bytes, and a metadata object.

Read the stored row: `sqlite3 <db> "SELECT full_body, original_filename, file_size_bytes, content_hash FROM documents WHERE vault_path = ?"`

**Check:**
- [ ] The response is HTTP 200 with a document id
- [ ] A documents row exists for that vault_path with full_body equal to the uploaded extracted text, original_filename, file_size_bytes, and content_hash set

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-04 · Re-uploading the identical file (same path, same content fingerprint) is idempotent — the stored record is not duplicated or rewritten
_Origin: design · Granularity: outcome_

**Run:**
Upload a file once (as in P5-DEPLOY-03).

Upload the exact same payload again.

Count rows: `sqlite3 <db> "SELECT COUNT(*) FROM documents WHERE vault_path = ?"`

**Check:**
- [ ] Both requests return HTTP 200
- [ ] There is exactly one documents row for that vault_path (no duplicate)
- [ ] The second upload makes no content change (skip-on-same-hash)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-05 · Uploading the same path with a different content fingerprint updates the stored text and details in place
_Origin: design · Granularity: outcome_

**Run:**
Upload a file once.

Upload the same vault_path again with changed extracted_text and a different content_hash.

Read the row back: `sqlite3 <db> "SELECT full_body, content_hash FROM documents WHERE vault_path = ?"`

**Check:**
- [ ] The single documents row for that vault_path now holds the new extracted text and the new content_hash
- [ ] Still exactly one row (update in place, not a second insert)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-06 · A move/rename event updates the stored file's location, carrying its search-index entries along
_Origin: design · Granularity: outcome_

**Run:**
Upload a file so a documents row (and its search entries) exist at the old path.

POST /api/event with type=moved, old_path, new_path, using a valid secret key.

Read back: `sqlite3 <db> "SELECT vault_path FROM documents WHERE vault_path = <new_path>"` and confirm the old path is gone.

**Check:**
- [ ] The response is HTTP 200
- [ ] The documents row now reads at new_path; no row remains at old_path
- [ ] The search-index entries (keyword + meaning) for that note moved to new_path too

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-07 · A delete event removes the stored file completely, including its search-index entries, within one transaction
_Origin: design · Granularity: outcome_

**Run:**
Upload a file so its row and search entries exist.

POST /api/event with type=deleted and that path, using a valid secret key.

Confirm removal across all three tables: `sqlite3 <db> "SELECT COUNT(*) FROM documents WHERE vault_path = ?"` (and the two search tables).

**Check:**
- [ ] The response is HTTP 200
- [ ] No row remains for that path in documents, the keyword index, or the meaning index
- [ ] Removal is hard delete (no soft-delete flag left behind)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-08 · An event naming a path that was never captured replies not_found instead of erroring
_Origin: design · Granularity: outcome_

**Run:**
With a fresh DB (or a path known to be absent), POST /api/event delete (or move) for that path using a valid secret key.

**Check:**
- [ ] The response is HTTP 200 with a body indicating not_found (e.g. {"status":"not_found"})
- [ ] It is NOT treated as an error (no 4xx/5xx) — the file may simply not have been captured yet

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-09 · A request to a sync endpoint with a wrong or missing secret key is rejected as unauthorized
_Origin: design · Granularity: outcome_

**Run:**
POST to /api/upload with no Authorization header.

POST to /api/upload with an incorrect bearer key.

**Check:**
- [ ] Both requests return HTTP 401 (unauthorized)
- [ ] No documents row is created or changed by the rejected requests
- [ ] /health remains reachable without any key (the gate covers /api/* only)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-10 · First-ever start with no backup creates a fresh database with the correct, fully-migrated schema
_Origin: design · Granularity: outcome_

**Run:**
Start the container with no prior backup available.

Inspect the created database: `sqlite3 <db> "SELECT version FROM schema_version"` and `sqlite3 <db> "SELECT name FROM sqlite_master WHERE type='table'"`.

**Check:**
- [ ] A new database file is created at the dedicated data path
- [ ] The schema is fully migrated (schema_version at the latest, all tables present including documents with full_body and the knowledge_entries table)
- [ ] The app starts and serves requests against the fresh DB

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-11 · After a container restart with cloud backup configured, the database is restored from object storage rather than starting empty
_Origin: design · Granularity: outcome_

**Run:**
Start the container with backup configured; upload one or more files so rows exist and a backup streams up.

Stop the container; start it again with the same backup settings.

Confirm the rows survived: `sqlite3 <db> "SELECT COUNT(*) FROM documents"`

**Check:**
- [ ] On the second start the database is restored from object storage before the app serves traffic
- [ ] The previously-uploaded documents are present (no data loss across restart, modulo the accepted ~1 second crash window)

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P5-DEPLOY-12 · The existing stdio entry point for the knowledge-assistant interface still works unchanged for local desktop use
_Origin: design · Granularity: outcome_

**Run:**
Run the original stdio MCP entry point locally.

Send a tools/list request over stdio (as a desktop client would).

**Check:**
- [ ] The stdio server starts and responds with the registered tool list, exactly as before this slice
- [ ] Adding the HTTP/cloud entry point did not break or alter the stdio path

**Last tested:** never
**Last result:** none
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### Phase 7

---

### P7-CAP-10 · A binary/visual upload (raw bytes, no extracted text) stores the raw bytes in object storage and saves a document row that references the blob, BEFORE any AI runs — the file is safe the moment it is stored
_Origin: design · Granularity: outcome_

**Run:**
Upload one image file as raw bytes with a content fingerprint and no extracted text.

Inspect the object store and the document row for that path.

**Check:**
- [ ] The raw bytes are present in object storage under a content-addressed key (the object name is derived from the raw-byte fingerprint)
- [ ] The document row holds a blob reference (object key) and the file type (mime), not the bytes themselves
- [ ] The store-blob step completes before the vision model is invoked (store-first)
- [ ] On a re-upload of identical bytes the blob is NOT stored again and the vision model is NOT invoked (front-loaded dedup over raw bytes)

**Last tested:** 2026-06-14
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P7-CAP-11 · A describable binary (image or text-less PDF) gets one vision-model description written to both the summary and the full text fields, making it findable by search; on vision failure the blob is kept, the failure is audited, and success is still returned (store-anyway)
_Origin: design · Granularity: outcome_

**Run:**
Upload one chart/image as raw bytes.

With the vision model healthy, confirm the description landed; then force a vision failure and confirm the blob and row survive.

**Check:**
- [ ] On success the document's summary and full-text fields hold the vision description and the title is descriptive (not the filename); the file is findable by keyword and meaning search
- [ ] A success audit entry is written for the describe decision
- [ ] On a forced vision failure the document row and the stored blob both survive, the description stays empty, a failure audit entry states why, and the endpoint still returns success

**Last tested:** 2026-06-14
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P7-CAP-12 · A binary that is over the size cap, or of a type the vision model cannot read (zip, video, etc.), is stored but not described — the description stays empty and an audit entry records the reason (too-big / unsupported-type)
_Origin: design · Granularity: outcome_

**Run:**
Upload one file larger than the configured size cap, and separately one unsupported binary type (e.g. a zip).

Inspect the document row and the audit log.

**Check:**
- [ ] The blob is stored (the file is never lost) and the document row references it
- [ ] The vision model is NOT invoked; the description stays empty (the same "needs-description" state as a vision failure — no new flag column)
- [ ] An audit entry records the reason the description is missing (too-big or unsupported-type)
- [ ] The endpoint returns success

**Last tested:** 2026-06-14
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---

### P7-CAP-13 · When a file delete is reported and its document row is removed, the stored blob is deleted only when no other document row still references it (delete-when-last-reference-gone); a blob shared by two rows survives the deletion of one
_Origin: design · Granularity: outcome_

**Run:**
Capture two binary rows that point at the SAME content-addressed blob (identical bytes, two paths); delete one of them via the event endpoint.

Then delete the surviving row and re-check the object store.

**Check:**
- [ ] After deleting the first row, the blob is still present in object storage (a surviving row still references it)
- [ ] After deleting the last referencing row, the blob is removed from object storage
- [ ] Deleting a text row (no blob reference) removes only the document row and touches no object storage

**Last tested:** 2026-06-14
**Last result:** passed
**Current result:** ___

⚠ AI-authored — not yet human-verified.

---
