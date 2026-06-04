# AI-KMS Testing Guide
_For non-technical testers. No coding required for Smoke and Phase checks._
_Auto-generated from `behavior_inventory.yaml` — do not edit manually._
_Last generated: 2026-06-04_

---

## How to Use This Guide

### Tiers
- **Smoke** (~5 min) — run before any demo or after any change. Must always pass.
- **Phase** — run when that phase's code is touched.
- **Full** — developer-only, requires terminal + DB access.

### Setup
1. Run `bash docs/system_behavior/setup_test_vault.sh` to create all test fixtures
2. Or run `bash docs/system_behavior/setup_test_vault.sh P1-CAP-01` for a single test

### Where things live
| What | Path |
|---|---|
| Test vault | `/Users/phatchenh/ai_kms_test_vault` |
| Database | `data/kb.db` |
| Logs | `logs/app.log` |

---

## SMOKE — Must Always Pass (~5 min)

---

### P1-CAP-01 · .md file captured in-place with AI summary in frontmatter

**Run:**
Run kms capture on a .md file with body text and no frontmatter

**Check:**
File stays at same path. Frontmatter block added with summary: (non-empty), tags: (at least one type/ tag), confidence: (float). Body text below --- unchanged.

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-02 · PDF creates sibling .md summary + binary relocated to attachment/

**Run:**
Run kms capture on a PDF in inbox/

**Check:**
inbox/sample-report.pdf is GONE. Binary appears at Projects/\<A\>/attachment/sample-report.pdf or stays in inbox/ (CLUELESS). Sibling .md at \<parent\>/attachment/.summaries/sample-report.pdf.md with frontmatter: attachment_path pointing to binary, type: attachment-summary, summary: non-empty.

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-03 · Body text of .md file preserved exactly after capture

**Run:**
Run kms capture on .md with known body text. Compare body before/after.

**Check:**
Body text below second --- is byte-identical to original. summary: appears only in frontmatter, never injected into body.

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-04 · Re-capture does not rename already-captured .md file (rename gate Rule 1)

**Run:**
Capture file once (creates DB record). Capture same file again.

**Check:**
File keeps original name after second capture. summary: may update but filename unchanged. No duplicate file appears.

⚠ AI-authored — not yet human-verified.

---

### P1-CAP-05 · Scan captures un-indexed .md files, skips already-indexed

**Run:**
Capture one file manually. Drop a second file. Run kms capture --scan.

**Check:**
Second file gets summary: in frontmatter. First file's summary: unchanged (not re-processed).

⚠ AI-authored — not yet human-verified.

---

### P15-LOC-01 · Note in Domain/ folder gets domain/\<D\> tag

**Run:**
Run kms capture on a .md file under Domain/Finance/

**Check:**
Frontmatter tags: contains domain/Finance. No domain: scalar field (only tag).

⚠ AI-authored — not yet human-verified.

---

### P15-LOC-02 · Note in Projects/ folder gets project field

**Run:**
Run kms capture on a .md file under Projects/Alpha/

**Check:**
Frontmatter has project: Alpha.

⚠ AI-authored — not yet human-verified.

---

## PHASE — Run When That Phase Is Touched

---

### Phase 1 — Capture Pipeline

---

#### P1-CAP-06 · DOCX file creates sibling .md summary

**Run:**
Run kms capture on a .docx file in inbox/

**Check:**
Sibling .md created with summary: in frontmatter. Original .docx moved out of inbox/.

⚠ AI-authored — not yet human-verified.

---

#### P1-CAP-07 · Watcher auto-captures new file drops

**Run:**
Start kms watch in one terminal. Create a new .md file in inbox/ from another terminal. Wait ~10 seconds.

**Check:**
New file gets summary: in frontmatter without manual kms capture.

⚠ AI-authored — not yet human-verified.

---

#### P1-CAP-08 · Gibberish-named PDF gets FULL_RENAME to AI-chosen title

**Run:**
Run kms capture on a PDF with a gibberish filename

**Check:**
Sibling .md has AI-chosen title (not xkjdhfs83). Audit log shows rename_gate outcome=FULL_RENAME.

⚠ AI-authored — not yet human-verified.

---

#### P1-CAP-09 · Idempotent capture — unchanged .md file skipped

**Run:**
Capture a .md file. Run capture again without editing.

**Check:**
Second run returns OK (SKIPPED). content_hash match → no LLM call. summary: field identical.

⚠ AI-authored — not yet human-verified.

---

#### P1-CAP-10 · CLUELESS routing — inbox binary gets pending-routing marker

**Run:**
Run kms capture on a PDF in inbox/ with no project/domain hint

**Check:**
Binary stays in inbox/. Marker .md at inbox/.summaries/\<filename\>.md with status: pending-routing in frontmatter, type: attachment-summary. Body contains placeholder text.

⚠ AI-authored — not yet human-verified.

---

#### P1-CAP-11 · URL enrichment — sparse note with URLs gets content fetched

**Run:**
Capture a .md file with a URL and minimal body text

**Check:**
Summary reflects content from the URL, not just the sparse body.

⚠ AI-authored — not yet human-verified.

---

### Phase 1.5 — Location Tags + Reconcile + Handlers

---

#### P15-REC-01 · Reconcile removes stale domain tag when folder deleted

**Run:**
Create a note with domain/OldDomain tag but no Domain/OldDomain/ folder exists. Run kms reconcile.

**Check:**
domain/OldDomain tag removed from frontmatter. Other tags preserved.

⚠ AI-authored — not yet human-verified.

---

#### P15-REC-02 · Reconcile adds missing domain tag for note in Domain/ folder

**Run:**
Place a note under Domain/Engineering/ without domain/Engineering tag. Run kms reconcile.

**Check:**
domain/Engineering tag added to frontmatter. Body unchanged.

⚠ AI-authored — not yet human-verified.

---

#### P15-REC-03 · Reconcile captures orphan binaries missing sibling .md

**Run:**
Place a PDF in attachment/ without any .summaries/\<name\>.md sibling. Run kms reconcile.

**Check:**
Sibling .md created at Projects/Alpha/attachment/.summaries/orphan-report.pdf.md with summary and attachment_path pointer.

⚠ AI-authored — not yet human-verified.

---

#### P15-REC-04 · Reconcile deletes orphan sibling when binary is gone

**Run:**
Create a sibling .md with type: attachment-summary but no corresponding binary at attachment_path. Run kms reconcile.

**Check:**
Orphan sibling .md deleted from disk and DB row removed.

⚠ AI-authored — not yet human-verified.

---

#### P15-REC-05 · Reconcile clears stale batch_id when doc moved away from batch destination

**Run:**
Move a document that has batch_id set to a folder not matching batch destination. Run kms reconcile.

**Check:**
batch_id set to NULL in documents table.

⚠ AI-authored — not yet human-verified.

---

#### P15-HDL-01 · XLSX file captured with sibling .md

**Run:**
Run kms capture on an .xlsx file

**Check:**
Sibling .md created with summary. Binary moved out of inbox/.

⚠ AI-authored — not yet human-verified.

---

#### P15-HDL-02 · PPTX file captured with sibling .md

**Run:**
Run kms capture on a .pptx file

**Check:**
Sibling .md created with summary. Binary moved out of inbox/.

⚠ AI-authored — not yet human-verified.

---

#### P15-HDL-03 · CSV file captured with sibling .md

**Run:**
Run kms capture on a .csv file

**Check:**
Sibling .md created with summary.

⚠ AI-authored — not yet human-verified.

---

#### P15-HDL-04 · HTML file captured with sibling .md

**Run:**
Run kms capture on an .html file

**Check:**
Sibling .md created with summary.

⚠ AI-authored — not yet human-verified.

---

#### P15-HDL-05 · EML email file captured with sibling .md

**Run:**
Run kms capture on an .eml file

**Check:**
Sibling .md created with summary including from/to/subject.

⚠ AI-authored — not yet human-verified.

---

#### P15-HDL-06 · MSG Outlook file captured with sibling .md

**Run:**
Run kms capture on a .msg file

**Check:**
Sibling .md created with summary including from/to/subject.

⚠ AI-authored — not yet human-verified.

---

#### P15-HDL-07 · PNG/JPG image file captured with sibling .md

**Run:**
Run kms capture on an image file

**Check:**
Sibling .md created with summary (OCR text or filename-based).

⚠ AI-authored — not yet human-verified.

---

#### P15-FOLD-01 · Folder dropped in inbox classified and routed by LLM

**Run:**
Create a folder in inbox/ with 2+ files. Run kms capture --scan or wait for watcher folder-stable event.

**Check:**
Folder moved to Projects/\<name\>/ or Domain/\<name\>/ (AUTO), or stays in inbox/ with PENDING_REVIEW/CLUELESS status. batches table row created with destination + confidence.

⚠ AI-authored — not yet human-verified.

---

### Phase Pre-2 — DB Schema + Domain Scalar

---

#### PRE2-DB-01 · Capture populates project, status, key_topics DB columns

**Run:**
Capture a note under Projects/Alpha/. Query documents table.

**Check:**
DB row has project=Alpha. key_topics contains JSON array of topic tags. status column present (may be NULL).

⚠ AI-authored — not yet human-verified.

---

#### PRE2-DOM-01 · Old domain: scalar stripped on re-write (lazy migration)

**Run:**
Capture a .md that has domain: finance in frontmatter

**Check:**
domain: scalar key GONE from frontmatter after capture. domain/Finance in tags: list preserved (if present).

⚠ AI-authored — not yet human-verified.

---

## FULL — Developer Only (Terminal + DB Required)

---

### P1-DEV-01 · Audit trail written for every capture decision

**Run:**
Capture any file. Query audit_log table.

**Check:**
audit_log row with pipeline=capture, stage=metadata, outcome=CAPTURED. correlation_id matches log output. Reasoning field non-empty.

⚠ AI-authored — not yet human-verified.

---

### P1-DEV-02 · Tag violation audited — invalid tags stripped and logged

**Run:**
Force a capture where LLM returns a tag not in tags.yaml taxonomy

**Check:**
audit_log row with stage=tag_violation, outcome=TAG_VIOLATION. Bad tag removed from final tags list. Valid tags preserved.

⚠ AI-authored — not yet human-verified.

---

### P1-DEV-03 · File-lost guard — deleted file between event and pipeline

**Run:**
Trigger capture on a path that no longer exists on disk

**Check:**
Failure(recoverable=True) returned. audit_log row with stage=file_lost, outcome=FILE_LOST. No DB row created.

⚠ AI-authored — not yet human-verified.

---

### P1-DEV-04 · Cooldown gate rejects file still being written

**Run:**
Capture a file immediately after writing it (within cooldown window)

**Check:**
Failure(recoverable=True). No LLM call. No DB row.

⚠ AI-authored — not yet human-verified.

---

### P1-DEV-05 · Pending-routing guard blocks re-capture of CLUELESS binary

**Run:**
Capture a CLUELESS binary once, then capture again

**Check:**
Second capture returns Failure(recoverable=True). No duplicate marker created. No LLM call.

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-01 · Watcher binary-delete sync — sibling cleaned up

**Run:**
Start watcher. Delete a binary from attachment/. Wait 5s.

**Check:**
Sibling .md at .summaries/\<binary.name\>.md deleted. DB row for sibling removed. audit_log: watcher:binary_delete (SIBLING_ORPHANED).

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-02 · Watcher binary-rename sync — sibling renamed

**Run:**
Start watcher. Rename old.pdf to new.pdf in same attachment/ folder.

**Check:**
Sibling renamed from old.pdf.md to new.pdf.md. attachment_path in sibling frontmatter updated. DB row path + attachment_path updated.

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-03 · Watcher binary cross-folder move — old sibling orphaned

**Run:**
Start watcher. Move file.pdf from Projects/A/attachment/ to Projects/B/attachment/.

**Check:**
Old sibling .md deleted. DB row for old sibling removed. New orphan binary picked up on next reconcile.

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-04 · Reconcile stage 3 — re-summarize stale binaries

**Run:**
Modify a binary (change mtime > sibling mtime). Run reconcile.

**Check:**
Sibling .md re-summarized with updated content. DB row updated.

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-05 · Debounce coalescing — rapid file events produce single capture

**Run:**
Start watcher. Save same file 5 times within 1 second.

**Check:**
Only one capture_file call fires (after 3.0s debounce window). No duplicate DB rows.

⚠ AI-authored — not yet human-verified.

---

### P15-DEV-06 · Scan skips .summaries/ paths in added loop

**Run:**
Run scan with .summaries/ sibling files already on disk

**Check:**
Sibling .md files inside .summaries/ NOT re-captured. No rename or identity wipe.

⚠ AI-authored — not yet human-verified.

---

## Test Files Reference

All created by `setup_test_vault.sh` in `/Users/phatchenh/ai_kms_test_vault/`:

| File | Used in test |
|---|---|
| `inbox/test-md-capture.md` | P1-CAP-01 |
| `inbox/sample-report.pdf` | P1-CAP-02 |
| `inbox/test-body-preservation.md` | P1-CAP-03 |
| `inbox/test-rename-gate.md` | P1-CAP-04 |
| `inbox/test-scan-uncaptured.md` | P1-CAP-05 |
| `inbox/q3-planning-brief.docx` | P1-CAP-06 |
| `inbox/auto-capture-test.md` | P1-CAP-07 |
| `inbox/xkjdhfs83.pdf` | P1-CAP-08 |
| `inbox/test-idempotent.md` | P1-CAP-09 |
| `inbox/mystery-file.pdf` | P1-CAP-10 |
| `inbox/test-url-note.md` | P1-CAP-11 |
| `Domain/Finance/test-domain-tag.md` | P15-LOC-01 |
| `Projects/Alpha/test-project-tag.md` | P15-LOC-02 |
| `inbox/test-stale-domain-tag.md` | P15-REC-01 |
| `Domain/Engineering/test-missing-domain-tag.md` | P15-REC-02 |
| `Projects/Alpha/attachment/orphan-report.pdf` | P15-REC-03 |
| `Projects/Alpha/attachment/.summaries/deleted-file.pdf.md` | P15-REC-04 |
| `inbox/q2-budget.xlsx` | P15-HDL-01 |
| `inbox/deck.pptx` | P15-HDL-02 |
| `inbox/data.csv` | P15-HDL-03 |
| `inbox/page.html` | P15-HDL-04 |
| `inbox/message.eml` | P15-HDL-05 |
| `inbox/outlook-msg.msg` | P15-HDL-06 |
| `inbox/screenshot.png` | P15-HDL-07 |
| `inbox/new-project-folder/file1.md` | P15-FOLD-01 |
| `inbox/new-project-folder/file2.pdf` | P15-FOLD-01 |
| `Projects/Alpha/test-db-columns.md` | PRE2-DB-01 |
| `inbox/test-old-domain-scalar.md` | PRE2-DOM-01 |
