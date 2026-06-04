# AI-KMS Testing Guide
_For non-technical testers. No coding required for Tier 1 checks._
_Last updated: 2026-06-03_

---

## Before You Start

### What you need open
1. **Terminal** — to run `kms` commands
2. **Obsidian** — pointed at vault `/Users/phatchenh/ai_kms_test_vault`
3. **Finder or file browser** — to verify files appear / disappear

### Where things live
| What | Path |
|---|---|
| Test vault | `/Users/phatchenh/ai_kms_test_vault` |
| Database (for dev checks) | `/Users/phatchenh/ai_kms/data/kb.db` |
| Logs | `/Users/phatchenh/ai_kms/logs/app.log` |

### One-time setup
Open terminal, go to the project folder, and activate the environment:
```bash
cd /Users/phatchenh/ai_kms
```
All `kms` commands below are run from this folder.

### Reset between test runs
If you want to start completely fresh (wipe all captured state):
```bash
rm -f data/kb.db
uv run python -c "from storage.db import init_db; from pathlib import Path; init_db(Path('data/kb.db'))"
```
Then re-copy the test PDF: `cp tests/fixtures/sample_text.pdf /Users/phatchenh/ai_kms_test_vault/inbox/xkjdhfs83.pdf`

---

## PHASE 1 — Capture Pipeline

---

### Test P1-1 · `.md` file gets AI summary stamped (happy path)

**File:** `inbox/test-p1-01-meeting-notes.md`
This file has a meeting notes body and NO frontmatter yet.

**Run:**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/inbox/test-p1-01-meeting-notes.md
```

**Expected terminal output:** starts with `OK:` — no `FAILED:`

**Open in Obsidian and check:**
- [x] File still exists at `inbox/test-p1-01-meeting-notes.md` (not moved or renamed)
- [x] At the top of the file, a frontmatter block now exists between `---` lines
- [x] Inside frontmatter: `summary:` field has a non-empty sentence (written by AI)
- [x] Inside frontmatter: `tags:` list contains at least one tag starting with `type/` (e.g. `type/meeting-note`)
- [x] The original meeting notes body is unchanged below the second `---`

**What this proves:** The capture pipeline runs end-to-end on a `.md` file.

---

### Test P1-2 · PDF creates a summary note and moves the binary

**File:** `inbox/xkjdhfs83.pdf` (gibberish filename — intentional for FULL_RENAME test)

> **Note:** If you already ran this test and the PDF was moved, restore it first:
> `cp tests/fixtures/sample_text.pdf /Users/phatchenh/ai_kms_test_vault/inbox/xkjdhfs83.pdf`

**Run:**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/inbox/xkjdhfs83.pdf
```

**Expected terminal output:** `OK:`

**Open Finder and check:**
- [ ] `inbox/xkjdhfs83.pdf` is GONE from the inbox
- [ ] A `.md` file appears in `inbox/` — its name is an AI-chosen title (e.g. `Sample Text.md`)
- [ ] That `.md` file contains `![[...pdf]]` in the body — an Obsidian link to the PDF
- [ ] The `.md` file frontmatter has `source_file:` pointing to the attachment location
- [ ] The PDF itself moved somewhere under `Projects/` or `attachment/` folder

**What this proves:** Non-`.md` files get a summary note created and the binary relocated.

---

### Test P1-3 · Body of a `.md` file is preserved exactly

**File:** `inbox/test-p1-03-body-preservation.md`
This file's body text is: _"This is my own writing. Do not change this content."_

**Run:**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/inbox/test-p1-03-body-preservation.md
```

**Open in Obsidian and check:**
- [x] The body text below `---` still reads exactly: `This is my own writing. Do not change this content.`
- [x] The three bullet points are still present unchanged
- [x] `summary:` appears in frontmatter (above the body) — NOT inside the body

**What this proves:** AI summary goes only into frontmatter, never into the note body.

---

### Test P1-4 · Re-capturing a file does not rename it (Rename Gate)

This test has two steps: capture once, then capture again.

**Step A — First capture (creates the DB record):**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/inbox/test-p1-01-meeting-notes.md
```

**Step B — Second capture (should not rename):**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/inbox/test-p1-01-meeting-notes.md
```

**Check after Step B:**
- [x] File is still named `test-p1-01-meeting-notes.md` (not renamed to AI title)
- [x] `summary:` field shows updated content (pipeline ran again successfully)
- [x] No second file with a similar name appeared in `inbox/`

**What this proves:** The rename gate skips renaming on already-captured files (Rule 1).

---

### Test P1-5 · Scan picks up a never-captured file

**File:** `inbox/test-p1-05-unread-drop.md`
This file has body text but no frontmatter — it has never been captured.

**Run:**
```bash
kms capture --scan
```
_(This scans the entire vault for un-captured files.)_

**Open in Obsidian and check:**
- [ ] `inbox/test-p1-05-unread-drop.md` now has a `summary:` field in frontmatter
- [ ] Files that were already captured (from P1-1, P1-3) did NOT get their summary overwritten (check that those files are unchanged)

**What this proves:** `--scan` processes un-indexed files and skips already-indexed ones.

---

### Test P1-6 · DOCX file gets a summary note created

**File:** `inbox/q3-planning-brief.docx`

**Run:**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/inbox/q3-planning-brief.docx
```

**Check:**
- [x] A `.md` file appears in `inbox/` (e.g. `Q3 Planning Brief.md` or similar)
- [x] That `.md` file has `summary:` in frontmatter describing the planning brief content
- [ ] The original `.docx` has moved out of `inbox/`

**What this proves:** DOCX handler works end-to-end.

---

### Test P1-7 · Auto-capture with watcher (kms watch)

**Pre-condition:** `inbox/` does not have `auto-captured-test.md`

**Step A — Start the watcher in one terminal:**
```bash
kms watch
```
Leave this running. You will see: `Watching /Users/phatchenh/ai_kms_test_vault — Ctrl-C to stop`

**Step B — In a second terminal, create a new file:**
```bash
echo "This is an automatic capture test. The watcher should pick this up." > /Users/phatchenh/ai_kms_test_vault/inbox/auto-captured-test.md
```

**Wait ~10 seconds, then check in Obsidian:**
- [ ] `inbox/auto-captured-test.md` now has `summary:` in its frontmatter
- [ ] You did not run any `kms capture` command manually

**Stop the watcher:** Press `Ctrl-C` in the first terminal.

**What this proves:** The filesystem watcher auto-captures new drops without manual commands.

---

## PHASE 1.5 — Attachment Layout + Location Tags

---

### Test P1.5-1 · Note in Domain folder gets domain tag

**File:** `Domain/Finance/test-p15-loc-domain.md`

**Run:**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/Domain/Finance/test-p15-loc-domain.md
```

**Open in Obsidian and check:**
- [x] Frontmatter `tags:` contains `domain/Finance`
- [x] No `domain:` scalar field exists (only `domain/Finance` inside `tags:`)

**What this proves:** Notes under `Domain/<X>/` automatically get the `domain/X` location tag.

---

### Test P1.5-2 · Note in Projects folder gets project field

**File:** `Projects/Alpha/test-p15-loc-project.md`

**Run:**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/Projects/Alpha/test-p15-loc-project.md
```

**Open in Obsidian and check:**
- [x] Frontmatter has `project: Alpha` (or `project` appears in tags/frontmatter matching folder name)

**What this proves:** Notes under `Projects/<A>/` automatically get their project context stamped.

---

### Test P1.5-3 · Stale domain tag removed by reconcile

**File:** `inbox/test-p15-stale-domain.md`
This file already has `domain/OldDomain` in its tags — but there is no `Domain/OldDomain/` folder in the vault.

**Run:**
```bash
kms reconcile
```

**Open in Obsidian and check:**
- [x] `inbox/test-p15-stale-domain.md` frontmatter `tags:` no longer contains `domain/OldDomain`
- [x] Other tags (`type/note`, `quarterly-review`) are still present

**What this proves:** Reconcile removes tags that point to deleted domain folders.

---

### Test P1.5-4 · Missing domain tag added by reconcile

**File:** `Domain/Engineering/test-p15-missing-domain-tag.md`
This file is stored under `Domain/Engineering/` but has NO `domain/Engineering` tag.

**Run:**
```bash
kms reconcile
```

**Open in Obsidian and check:**
- [x] Frontmatter `tags:` now contains `domain/Engineering`
- [x] Original body text is unchanged

**What this proves:** Reconcile adds missing location tags to notes that are in the right folder but lack the tag.

---

### Test P1.5-5 · XLSX and PPTX files are captured

**Files:** `inbox/q2-budget-tracker.xlsx` and `inbox/sea-expansion-deck.pptx`

**Run both:**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/inbox/q2-budget-tracker.xlsx
kms capture /Users/phatchenh/ai_kms_test_vault/inbox/sea-expansion-deck.pptx
```

**Check for each:**
- [ ] A `.md` summary file appears in vault with non-empty `summary:` in frontmatter
- [ ] The original binary has moved out of `inbox/`
- [ ] No `FAILED:` in terminal output

**What this proves:** XLSX and PPTX handlers work (Phase 1.5 handler extensions).

---

### Test P1.5-6 · Idempotent capture — unchanged file not re-processed

**Pre-condition:** Run Test P1.5-1 first (capture the Finance domain note once).

**Run capture again on the same file:**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/Domain/Finance/test-p15-loc-domain.md
```

**Check:**
- [x] Terminal output says `OK:` — no error
- [x] `summary:` field content is IDENTICAL to what was there before (not regenerated)
  - You can compare by noting the exact wording before and after
- [x] No second `.md` file appeared for this note

**What this proves:** Idempotent capture skips re-processing when file content hasn't changed.

---

## PHASE PRE-2 — DB Schema + Domain Scalar Cleanup

---

### Test Pre2-1 · Capturing a note populates new DB columns

**This is a developer-only check.** Run in terminal:

**Step A — Capture a note:**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/Projects/Alpha/test-p15-loc-project.md
```

**Step B — Query the database:**
```bash
uv run python -c "
import sqlite3
con = sqlite3.connect('data/kb.db')
row = con.execute(\"SELECT vault_path, project, status, key_topics FROM documents WHERE vault_path LIKE '%test-p15-loc-project%'\").fetchone()
print('vault_path:', row[0])
print('project:', row[1])
print('key_topics:', row[3])
con.close()
"
```

**Expected output:**
- `project:` shows `Alpha` (or `Projects/Alpha`)
- `key_topics:` shows a JSON list of topic tags (e.g. `["kickoff", "mvp"]`)

**What this proves:** TD-008 — `project`, `status`, `key_topics` columns populated on capture.

---

### Test Pre2-2 · Old `domain:` scalar field stripped on re-write

**File:** `inbox/test-pre2-old-domain-scalar.md`
This file currently has `domain: finance` as a scalar field in frontmatter (old format).

**Run:**
```bash
kms capture /Users/phatchenh/ai_kms_test_vault/inbox/test-pre2-old-domain-scalar.md
```

**Open in Obsidian and check:**
- [x] `domain: finance` scalar key is GONE from frontmatter
- [x] `tags:` still contains `domain/Finance` (unchanged)
- [x] `summary:` has been updated

**What this proves:** TD-038 — the deprecated `domain:` scalar is stripped whenever any pipeline re-writes a note.

---

## Checking What Actually Happened (DB Queries for Developers)

Run these after any capture to verify internal state.

### List all captured documents
```bash
uv run python -c "
import sqlite3
con = sqlite3.connect('data/kb.db')
rows = con.execute('SELECT id, vault_path, project, key_topics FROM documents ORDER BY id DESC LIMIT 20').fetchall()
for r in rows:
    print(r)
con.close()
"
```

### Check audit log for a specific file
```bash
uv run python -c "
import sqlite3
con = sqlite3.connect('data/kb.db')
rows = con.execute(\"SELECT pipeline, stage, outcome, correlation_id FROM audit_log ORDER BY id DESC LIMIT 20\").fetchall()
for r in rows:
    print(r)
con.close()
"
```
Look for rows with:
- `stage='metadata', outcome='CAPTURED'` — normal capture
- `stage='rename_gate', outcome='SKIP'` — file kept its name
- `stage='rename_gate', outcome='FULL_RENAME'` — gibberish name replaced
- `stage='metadata', outcome='TAG_VIOLATION'` — AI returned bad tags (stripped automatically)

### Verify rename gate decision
After capturing `xkjdhfs83.pdf`, check that it was FULL_RENAME'd:
```bash
uv run python -c "
import sqlite3
con = sqlite3.connect('data/kb.db')
rows = con.execute(\"SELECT stage, outcome, reasoning FROM audit_log WHERE stage='rename_gate' ORDER BY id DESC LIMIT 5\").fetchall()
for r in rows:
    print(r)
con.close()
"
```

---

## Common Failures and What They Mean

| What you see | Likely cause | Fix |
|---|---|---|
| `FAILED: metadata JSON parse error` | AI returned non-JSON (rare) | Re-run the same command once |
| `FAILED: no handler for extension` | File type not supported | Only `.md`, `.pdf`, `.docx`, `.xlsx`, `.pptx` supported in Phase 1 |
| `FAILED: file too recent` | File just saved; cooldown active | Wait 60 seconds and re-run (cooldown is 0 in dev config, so this shouldn't happen) |
| `FAILED: PDF contains no extractable text` | Image-only PDF (scanned) | Use a text-based PDF |
| File not renamed despite gibberish name | PDF was already captured (Rule 1) | Reset DB and re-copy PDF |
| Watcher not picking up file | Debounce window (3s) still active | Wait 5–10 seconds after saving the file |
| `domain/OldDomain` tag still present after reconcile | `kms reconcile` not implemented yet | This is Phase 1.5 Phase 3 — check if it's been built |

---

## Test Checklist (print this and tick off)

### Phase 1
- [ ] P1-1: `.md` capture → `summary:` in frontmatter, file not moved
- [ ] P1-2: PDF capture → sibling `.md` created, binary relocated, gibberish name replaced
- [ ] P1-3: Body text unchanged after capture
- [ ] P1-4: Re-capture does not rename already-named file
- [ ] P1-5: `--scan` captures un-indexed file, skips already-indexed
- [ ] P1-6: DOCX capture → sibling `.md` created
- [ ] P1-7: Watcher auto-captures new file drop

### Phase 1.5
- [ ] P1.5-1: Domain folder note gets `domain/Finance` tag
- [ ] P1.5-2: Projects folder note gets `project: Alpha`
- [ ] P1.5-3: Stale `domain/OldDomain` tag removed by reconcile
- [ ] P1.5-4: Missing domain tag added by reconcile
- [ ] P1.5-5: XLSX and PPTX captured successfully
- [ ] P1.5-6: Idempotent — unchanged file not re-processed

### Phase Pre-2
- [ ] Pre2-1: DB `project` + `key_topics` columns populated after capture
- [ ] Pre2-2: Old `domain:` scalar stripped when note re-written

---

## Test Files Reference

All pre-created in `/Users/phatchenh/ai_kms_test_vault/`:

| File | Used in test | Purpose |
|---|---|---|
| `inbox/test-p1-01-meeting-notes.md` | P1-1, P1-4 | `.md` in-place capture |
| `inbox/test-p1-03-body-preservation.md` | P1-3 | Body must stay unchanged |
| `inbox/test-p1-05-unread-drop.md` | P1-5 | Scan capture |
| `inbox/xkjdhfs83.pdf` | P1-2 | PDF → sibling + FULL_RENAME |
| `inbox/q3-planning-brief.docx` | P1-6 | DOCX handler |
| `inbox/q2-budget-tracker.xlsx` | P1.5-5 | XLSX handler |
| `inbox/sea-expansion-deck.pptx` | P1.5-5 | PPTX handler |
| `inbox/test-p15-stale-domain.md` | P1.5-3 | Stale tag reconcile |
| `inbox/test-pre2-old-domain-scalar.md` | Pre2-2 | Old `domain:` scalar |
| `Domain/Finance/test-p15-loc-domain.md` | P1.5-1 | Domain location tag |
| `Domain/Engineering/test-p15-missing-domain-tag.md` | P1.5-4 | Missing tag reconcile |
| `Projects/Alpha/test-p15-loc-project.md` | P1.5-2, Pre2-1 | Project location tag |


Need more check
- Scan not capture nonmd inside attachment
- Scan re-capture .md file inside .summaries, and change its name - which it should not have
- Nonmd capture create sibling md that has no project field in fronmatter, and domain tag is weird
- manually moving nonmd does not trigger recapture
- not verify anything about folder capture or batch id (also folder capture should exclude .summary and attachment)
