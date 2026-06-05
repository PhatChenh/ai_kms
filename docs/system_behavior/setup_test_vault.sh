#!/usr/bin/env bash
# Auto-generated from behavior_inventory.yaml — do not edit manually.
# Run: bash docs/system_behavior/setup_test_vault.sh [test-id|all|smoke|phase]
# No args = full reset + all fixtures.
#
# IMPORTANT: This script operates on the TEST vault, never the real vault.
# Last generated: 2026-06-05

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VAULT="$(cd "$PROJECT_ROOT" && uv run python -c "import yaml; print(yaml.safe_load(open('src/config/config.yaml'))['testing']['vault_path'])")"
FIXTURES="$PROJECT_ROOT/tests/fixtures"
DB="$PROJECT_ROOT/data/kb.db"

# ─── Helpers ───────────────────────────────────────────────────────────────────

reset_db() {
    rm -f "$DB"
    cd "$PROJECT_ROOT"
    uv run python -c "from storage.db import init_db; from pathlib import Path; init_db(Path('$DB'))"
    echo "✓ Database reset"
}

ensure_dir() {
    mkdir -p "$(dirname "$1")"
}

write_md() {
    local path="$1"
    local body="$2"
    ensure_dir "$path"
    printf '%s\n' "$body" > "$path"
}

write_md_with_frontmatter() {
    local path="$1"
    local frontmatter="$2"
    local body="$3"
    ensure_dir "$path"
    printf '%s\n%s\n%s\n%s\n' "---" "$frontmatter" "---" "$body" > "$path"
}

clean_vault() {
    echo "Cleaning test vault at $VAULT ..."
    rm -rf "${VAULT:?}"/*
    mkdir -p "$VAULT/inbox"
    mkdir -p "$VAULT/Projects/Alpha/attachment/.summaries"
    mkdir -p "$VAULT/Domain/Finance"
    mkdir -p "$VAULT/Domain/Engineering"
    mkdir -p "$VAULT/Briefings"
    mkdir -p "$VAULT/Synthesis"
    mkdir -p "$VAULT/Documentation"
    echo "✓ Vault cleaned"
}

# ─── Phase 1 — Capture Pipeline ────────────────────────────────────────────────

# smoke | P1-CAP-01: .md file captured in-place with AI summary in frontmatter
setup_P1_CAP_01() {
    write_md "$VAULT/inbox/test-md-capture.md" \
"Meeting notes from Q3 planning session.

Key decisions:
- Launch MVP by end of month
- Focus on capture pipeline first
- Defer search to Phase 3"
}

# smoke | P1-CAP-02: PDF already in a project folder creates sibling .md and moves binary to attachment/
setup_P1_CAP_02() {
    mkdir -p "$VAULT/Projects/Alpha"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/Projects/Alpha/sample-report.pdf"
    else
        echo "⚠ P1-CAP-02: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# smoke | P1-CAP-03: Body text of .md file preserved exactly after capture
setup_P1_CAP_03() {
    write_md "$VAULT/inbox/test-body-preservation.md" \
"This is my own writing. Do not change this content.

- First important point
- Second important point
- Third important point"
}

# smoke | P1-CAP-04: Re-capture does not rename already-captured .md file (rename gate Rule 1)
setup_P1_CAP_04() {
    write_md "$VAULT/inbox/test-rename-gate.md" \
"Quarterly review notes for the engineering team.

Performance highlights and areas for improvement."
}

# smoke | P1-CAP-05: Scan captures un-indexed .md files, skips already-indexed
# Needs BOTH inbox/test-md-capture.md AND inbox/test-scan-uncaptured.md
setup_P1_CAP_05() {
    write_md "$VAULT/inbox/test-md-capture.md" \
"This is a test note for capture.

It will be indexed in step 1."
    write_md "$VAULT/inbox/test-scan-uncaptured.md" \
"This file has never been captured before.

It should be picked up by kms capture --scan."
}

# phase | P1-CAP-06: DOCX file creates sibling .md summary
setup_P1_CAP_06() {
    mkdir -p "$VAULT/inbox"
    cd "$PROJECT_ROOT" && uv run python -c "
from docx import Document
doc = Document()
doc.add_heading('Q3 Planning Brief', 0)
doc.add_paragraph('Summary of Q3 objectives and deliverables.')
doc.add_paragraph('Key milestones: launch MVP by end of quarter.')
doc.save('$VAULT/inbox/q3-planning-brief.docx')
"
}

# phase | P1-CAP-07: Watcher auto-captures new file drops
# Fixture is created during the test — drop inbox/auto-capture-test.md while watcher runs.
setup_P1_CAP_07() {
    echo "P1-CAP-07: Fixtures are created during the test. Start watcher in Terminal 1, then copy a .md to inbox/ in Terminal 2."
}

# phase | P1-CAP-08: Gibberish-named PDF gets FULL_RENAME to AI-chosen title
setup_P1_CAP_08() {
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/inbox/xkjdhfs83.pdf"
    else
        echo "⚠ P1-CAP-08: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# phase | P1-CAP-09: Idempotent capture — unchanged .md file skipped
setup_P1_CAP_09() {
    write_md "$VAULT/inbox/test-idempotent.md" \
"Content for idempotent capture test.

This should only be processed once if content is unchanged."
}

# phase | P1-CAP-10: CLUELESS routing — inbox binary gets pending-routing marker
setup_P1_CAP_10() {
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/inbox/mystery-file.pdf"
    else
        echo "⚠ P1-CAP-10: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# phase | P1-CAP-11: URL enrichment — sparse note with URLs gets content fetched
setup_P1_CAP_11() {
    write_md "$VAULT/inbox/test-url-note.md" \
"Check this out: https://example.com/article"
}

# full | P1-DEV-01: Audit trail written for every capture decision
# No pre-created fixtures needed — run any capture then query audit_log
setup_P1_DEV_01() {
    echo "P1-DEV-01: No pre-created fixtures. Run: uv run kms capture inbox/test-md-capture.md, then: sqlite3 data/kb.db \"SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 5\""
}

# full | P1-DEV-02: Tag violation audited — invalid tags stripped and logged
# No pre-created fixtures needed — developer test requiring mocked LLM response
setup_P1_DEV_02() {
    echo "P1-DEV-02: No pre-created fixtures. Developer test — requires mocking LLM response to return an invalid tag."
}

# full | P1-DEV-03: File-lost guard — deleted file between event and pipeline
# No pre-created fixtures needed — test uses a nonexistent path
setup_P1_DEV_03() {
    echo "P1-DEV-03: No pre-created fixtures. Run: uv run kms capture inbox/nonexistent-file.md"
}

# full | P1-DEV-04: Cooldown gate rejects file still being written
# No pre-created fixtures needed — write a file and immediately capture
setup_P1_DEV_04() {
    echo "P1-DEV-04: No pre-created fixtures. Write a file to inbox/, then immediately run kms capture on it within the cooldown window."
}

# full | P1-DEV-05: Pending-routing guard blocks re-capture of CLUELESS binary
# No pre-created fixtures needed — depends on P1-CAP-10 mystery-file.pdf being captured first
setup_P1_DEV_05() {
    echo "P1-DEV-05: No pre-created fixtures. First run setup for P1-CAP-10, capture mystery-file.pdf to create the CLUELESS marker, then capture again."
}

# ─── Phase 1.5 — Location Tags + Attachment Layout + Reconcile ────────────────

# smoke | P15-LOC-01: Note in Domain/ folder gets domain/<D> tag
setup_P15_LOC_01() {
    write_md "$VAULT/Domain/Finance/test-domain-tag.md" \
"Finance team budget analysis for Q3."
}

# smoke | P15-LOC-02: Note in Projects/ folder gets project field
setup_P15_LOC_02() {
    write_md "$VAULT/Projects/Alpha/test-project-tag.md" \
"Project Alpha kickoff notes and MVP scope."
}

# phase | P15-REC-01: Reconcile removes stale domain tag when folder deleted
setup_P15_REC_01() {
    write_md_with_frontmatter "$VAULT/inbox/test-stale-domain-tag.md" \
"tags:
  - domain/OldDomain
  - type/note
  - quarterly-review" \
"This note has a stale domain tag pointing to a deleted folder."
}

# phase | P15-REC-02: Reconcile adds missing domain tag for note in Domain/ folder
setup_P15_REC_02() {
    write_md "$VAULT/Domain/Engineering/test-missing-domain-tag.md" \
"Engineering retrospective notes — this file is under Domain/Engineering/ but has no domain tag."
}

# phase | P15-REC-03: Reconcile captures orphan binaries missing sibling .md
setup_P15_REC_03() {
    mkdir -p "$VAULT/Projects/Alpha/attachment"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/Projects/Alpha/attachment/orphan-report.pdf"
    else
        echo "⚠ P15-REC-03: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# phase | P15-REC-04: Reconcile deletes orphan sibling when binary is gone
setup_P15_REC_04() {
    mkdir -p "$VAULT/Projects/Alpha/attachment/.summaries"
    write_md_with_frontmatter "$VAULT/Projects/Alpha/attachment/.summaries/deleted-file.pdf.md" \
"type: attachment-summary
attachment_path: Projects/Alpha/attachment/deleted-file.pdf
summary: Summary of a file that no longer exists" \
"This sibling points to a binary that has been deleted."
}

# phase | P15-REC-05: Reconcile clears stale batch_id when doc moved away from batch destination
# No pre-created fixtures needed — requires manual DB state manipulation
setup_P15_REC_05() {
    echo "P15-REC-05: No pre-created fixtures. Move a document with batch_id set to a non-batch folder, then run: uv run kms reconcile. Verify with: sqlite3 data/kb.db \"SELECT vault_path, batch_id FROM documents WHERE batch_id IS NOT NULL\""
}

# phase | P15-HDL-01: XLSX file captured with sibling .md
setup_P15_HDL_01() {
    mkdir -p "$VAULT/inbox"
    cd "$PROJECT_ROOT" && uv run python -c "
import openpyxl
wb = openpyxl.Workbook()
ws = wb.active
ws.title = 'Q2 Budget'
ws.append(['Department', 'Budget', 'Spent'])
ws.append(['Engineering', 200000, 150000])
ws.append(['Marketing', 80000, 60000])
wb.save('$VAULT/inbox/q2-budget.xlsx')
"
}

# phase | P15-HDL-02: PPTX file captured with sibling .md
setup_P15_HDL_02() {
    mkdir -p "$VAULT/inbox"
    cd "$PROJECT_ROOT" && uv run python -c "
from pptx import Presentation
prs = Presentation()
slide = prs.slides.add_slide(prs.slide_layouts[0])
slide.shapes.title.text = 'Q3 Product Roadmap'
slide.placeholders[1].text = 'Key initiatives and milestones'
prs.save('$VAULT/inbox/deck.pptx')
"
}

# phase | P15-HDL-03: CSV file captured with sibling .md
setup_P15_HDL_03() {
    mkdir -p "$VAULT/inbox"
    printf 'name,department,budget\nAlpha,Engineering,50000\nBeta,Marketing,30000\n' > "$VAULT/inbox/data.csv"
}

# phase | P15-HDL-04: HTML file captured with sibling .md
setup_P15_HDL_04() {
    mkdir -p "$VAULT/inbox"
    printf '<html><body><h1>Test Page</h1><p>This is a test HTML page for capture.</p></body></html>\n' > "$VAULT/inbox/page.html"
}

# phase | P15-HDL-05: EML email file captured with sibling .md
setup_P15_HDL_05() {
    mkdir -p "$VAULT/inbox"
    printf 'From: sender@example.com\nTo: receiver@example.com\nSubject: Test Email\nDate: Wed, 04 Jun 2026 10:00:00 +0700\nMIME-Version: 1.0\nContent-Type: text/plain; charset=UTF-8\n\nThis is a test email body for capture testing.\nIt should be summarised by the EML handler.\n' > "$VAULT/inbox/message.eml"
}

# phase | P15-HDL-06: MSG Outlook file captured with sibling .md
# MSG files require Outlook binary format — cannot be generated programmatically
setup_P15_HDL_06() {
    echo "⚠ P15-HDL-06: MSG files require Outlook format — place a test .msg file at $VAULT/inbox/outlook-msg.msg manually"
}

# phase | P15-HDL-07: PNG/JPG image file captured with sibling .md
# status: conflict — PngHandler returns Failure (not yet implemented)
setup_P15_HDL_07() {
    mkdir -p "$VAULT/inbox"
    if [ -f "$FIXTURES/sample.png" ]; then
        cp "$FIXTURES/sample.png" "$VAULT/inbox/screenshot.png"
    else
        # Minimal valid 1x1 PNG via raw bytes fallback
        printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82' > "$VAULT/inbox/screenshot.png"
        echo "⚠ P15-HDL-07: Created minimal 1x1 PNG (no real image content). Replace with a real screenshot for OCR testing."
    fi
    echo "NOTE: P15-HDL-07 status=conflict — PngHandler.extract() returns Failure (image LLM not yet implemented). No sibling .md will be created."
}

# phase | P15-FOLD-01: Folder dropped in inbox classified and routed by LLM
setup_P15_FOLD_01() {
    mkdir -p "$VAULT/inbox/new-project-folder"
    write_md "$VAULT/inbox/new-project-folder/file1.md" \
"First file in a dropped folder. Project planning notes for the new initiative."
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/inbox/new-project-folder/file2.pdf"
    else
        echo "⚠ P15-FOLD-01: No sample PDF at $FIXTURES/sample_text.pdf — folder test will only have file1.md"
    fi
}

# full | P15-DEV-01: Watcher binary-delete sync — sibling cleaned up
# Fixtures are created during the test (watcher must be running)
setup_P15_DEV_01() {
    echo "P15-DEV-01: Fixtures are created during the test. Start watcher in Terminal 1, then delete a binary from Projects/Alpha/attachment/ in Terminal 2 and wait ~5 seconds."
}

# full | P15-DEV-02: Watcher binary-rename sync — sibling renamed
# Fixtures are created during the test (watcher must be running)
setup_P15_DEV_02() {
    echo "P15-DEV-02: Fixtures are created during the test. Start watcher in Terminal 1, then rename a binary in attachment/ in Terminal 2 and wait ~5 seconds."
}

# full | P15-DEV-03: Watcher binary cross-folder move — old sibling orphaned
# Fixtures are created during the test (watcher must be running)
setup_P15_DEV_03() {
    echo "P15-DEV-03: Fixtures are created during the test. Start watcher in Terminal 1, then move a binary between Projects/A/attachment/ and Projects/B/attachment/ in Terminal 2 and wait ~5 seconds."
}

# full | P15-DEV-04: Reconcile stage 3 — re-summarize stale binaries
# No pre-created fixtures needed — touch an existing binary to update its mtime
setup_P15_DEV_04() {
    echo "P15-DEV-04: No pre-created fixtures. Ensure a binary exists at Projects/Alpha/attachment/ with a sibling .md, then run: touch \$VAULT/Projects/Alpha/attachment/<binary>.pdf and: uv run kms reconcile"
}

# full | P15-DEV-05: Debounce coalescing — rapid file events produce single capture
# Fixtures are created during the test (watcher must be running)
setup_P15_DEV_05() {
    echo "P15-DEV-05: Fixtures are created during the test. Start watcher in Terminal 1, then in Terminal 2 run: for i in 1 2 3 4 5; do touch \$VAULT/inbox/test.md; done"
}

# full | P15-DEV-06: Scan skips .summaries/ paths in added loop
# No pre-created fixtures needed — requires prior captures to have .summaries/ files
setup_P15_DEV_06() {
    echo "P15-DEV-06: No pre-created fixtures. Requires prior capture to have .summaries/ sibling files on disk. Then run: uv run kms capture --scan"
}

# ─── Phase Pre-2 — DB Schema + Domain Scalar Cleanup ──────────────────────────

# phase | PRE2-DB-01: Capture populates project, status, key_topics DB columns
setup_PRE2_DB_01() {
    mkdir -p "$VAULT/Projects/Alpha"
    write_md "$VAULT/Projects/Alpha/test-db-columns.md" \
"Project Alpha database column test.

This note should populate project, key_topics, and status columns in the documents DB table."
}

# phase | PRE2-DOM-01: Old domain: scalar stripped on re-write (lazy migration)
setup_PRE2_DOM_01() {
    write_md_with_frontmatter "$VAULT/inbox/test-old-domain-scalar.md" \
"domain: finance
tags:
  - domain/Finance
  - type/note" \
"This note has the old domain: scalar that should be stripped on capture."
}

# ─── Vault-Restructure — Editable/No-Edit Split ───────────────────────────────

# phase | VR-PLACE-01: No-edit binary (PDF/PNG/JPG) routed to attachment/
# No pre-created fixtures needed — uses any existing PDF or PNG
setup_VR_PLACE_01() {
    echo "VR-PLACE-01: No pre-created fixtures. Run: uv run kms capture inbox/<any>.pdf to verify no-edit routing to attachment/."
}

# phase | VR-PLACE-02: Editable binary (XLSX/DOCX/PPTX) routed to project/domain root
# No pre-created fixtures needed — uses any existing XLSX, DOCX, or PPTX
setup_VR_PLACE_02() {
    echo "VR-PLACE-02: No pre-created fixtures. Run: uv run kms capture inbox/<any>.xlsx to verify editable routing to project root."
}

# phase | VR-SKIP-01: Watcher and scan skip AI-output folders (Briefings/Synthesis/Documentation)
# No pre-created fixtures needed — place a .md in Briefings/ and run scan
setup_VR_SKIP_01() {
    echo "VR-SKIP-01: No pre-created fixtures. Place a .md file in $VAULT/Briefings/ manually, then run: uv run kms capture --scan. Verify no DB row is created."
}

# phase | VR-REHOME-01: Misplaced binary re-homed to correct placement
# No pre-created fixtures needed — place editable file in attachment/ then reconcile
setup_VR_REHOME_01() {
    echo "VR-REHOME-01: No pre-created fixtures. Place an editable file (e.g. .xlsx) inside Projects/Alpha/attachment/ (wrong location), then run: uv run kms reconcile."
}

# full | VR-CONTENT-01: Binary content change detected and re-summarized
# Fixtures are created during the test (watcher must be running)
setup_VR_CONTENT_01() {
    echo "VR-CONTENT-01: Fixtures are created during the test. Start watcher in Terminal 1, then edit an XLSX file's actual content (not just touch) in Terminal 2 and wait for SHA-256 change detection."
}

# ─── Tier runners ──────────────────────────────────────────────────────────────

run_smoke() {
    setup_P1_CAP_01
    setup_P1_CAP_02
    setup_P1_CAP_03
    setup_P1_CAP_04
    setup_P1_CAP_05
    setup_P15_LOC_01
    setup_P15_LOC_02
}

run_phase() {
    run_smoke
    setup_P1_CAP_06
    setup_P1_CAP_07
    setup_P1_CAP_08
    setup_P1_CAP_09
    setup_P1_CAP_10
    setup_P1_CAP_11
    setup_P15_REC_01
    setup_P15_REC_02
    setup_P15_REC_03
    setup_P15_REC_04
    setup_P15_REC_05
    setup_P15_HDL_01
    setup_P15_HDL_02
    setup_P15_HDL_03
    setup_P15_HDL_04
    setup_P15_HDL_05
    setup_P15_HDL_06
    setup_P15_HDL_07
    setup_P15_FOLD_01
    setup_PRE2_DB_01
    setup_PRE2_DOM_01
    setup_VR_PLACE_01
    setup_VR_PLACE_02
    setup_VR_SKIP_01
    setup_VR_REHOME_01
}

run_all() {
    run_phase
    setup_P1_DEV_01
    setup_P1_DEV_02
    setup_P1_DEV_03
    setup_P1_DEV_04
    setup_P1_DEV_05
    setup_P15_DEV_01
    setup_P15_DEV_02
    setup_P15_DEV_03
    setup_P15_DEV_04
    setup_P15_DEV_05
    setup_P15_DEV_06
    setup_VR_CONTENT_01
}

# ─── Main dispatch ─────────────────────────────────────────────────────────────

echo "=== AI-KMS Test Vault Setup ==="
echo "Vault: $VAULT"
echo ""

case "${1:-all}" in
    all)
        reset_db; clean_vault; run_all
        echo ""; echo "✓ All fixtures ready (smoke + phase + full)"
        ;;
    smoke)
        reset_db; clean_vault; run_smoke
        echo ""; echo "✓ Smoke fixtures ready"
        ;;
    phase)
        reset_db; clean_vault; run_phase
        echo ""; echo "✓ Phase fixtures ready (smoke + phase)"
        ;;

    # ── Phase 1 — Capture Pipeline (smoke) ──────────────────────────────────
    P1-CAP-01)  reset_db; clean_vault; setup_P1_CAP_01 ;;
    P1-CAP-02)  reset_db; clean_vault; setup_P1_CAP_02 ;;
    P1-CAP-03)  reset_db; clean_vault; setup_P1_CAP_03 ;;
    P1-CAP-04)  reset_db; clean_vault; setup_P1_CAP_04 ;;
    P1-CAP-05)  reset_db; clean_vault; setup_P1_CAP_05 ;;

    # ── Phase 1 — Capture Pipeline (phase) ──────────────────────────────────
    P1-CAP-06)  reset_db; clean_vault; setup_P1_CAP_06 ;;
    P1-CAP-07)  reset_db; clean_vault; setup_P1_CAP_07 ;;
    P1-CAP-08)  reset_db; clean_vault; setup_P1_CAP_08 ;;
    P1-CAP-09)  reset_db; clean_vault; setup_P1_CAP_09 ;;
    P1-CAP-10)  reset_db; clean_vault; setup_P1_CAP_10 ;;
    P1-CAP-11)  reset_db; clean_vault; setup_P1_CAP_11 ;;

    # ── Phase 1 — Capture Pipeline (full) ───────────────────────────────────
    P1-DEV-01)  reset_db; clean_vault; setup_P1_DEV_01 ;;
    P1-DEV-02)  reset_db; clean_vault; setup_P1_DEV_02 ;;
    P1-DEV-03)  reset_db; clean_vault; setup_P1_DEV_03 ;;
    P1-DEV-04)  reset_db; clean_vault; setup_P1_DEV_04 ;;
    P1-DEV-05)  reset_db; clean_vault; setup_P1_DEV_05 ;;

    # ── Phase 1.5 — Location Tags (smoke) ───────────────────────────────────
    P15-LOC-01) reset_db; clean_vault; setup_P15_LOC_01 ;;
    P15-LOC-02) reset_db; clean_vault; setup_P15_LOC_02 ;;

    # ── Phase 1.5 — Reconcile (phase) ───────────────────────────────────────
    P15-REC-01) reset_db; clean_vault; setup_P15_REC_01 ;;
    P15-REC-02) reset_db; clean_vault; setup_P15_REC_02 ;;
    P15-REC-03) reset_db; clean_vault; setup_P15_REC_03 ;;
    P15-REC-04) reset_db; clean_vault; setup_P15_REC_04 ;;
    P15-REC-05) reset_db; clean_vault; setup_P15_REC_05 ;;

    # ── Phase 1.5 — Handlers (phase) ────────────────────────────────────────
    P15-HDL-01)  reset_db; clean_vault; setup_P15_HDL_01 ;;
    P15-HDL-02)  reset_db; clean_vault; setup_P15_HDL_02 ;;
    P15-HDL-03)  reset_db; clean_vault; setup_P15_HDL_03 ;;
    P15-HDL-04)  reset_db; clean_vault; setup_P15_HDL_04 ;;
    P15-HDL-05)  reset_db; clean_vault; setup_P15_HDL_05 ;;
    P15-HDL-06)  reset_db; clean_vault; setup_P15_HDL_06 ;;
    P15-HDL-07)  reset_db; clean_vault; setup_P15_HDL_07 ;;

    # ── Phase 1.5 — Folder drop (phase) ─────────────────────────────────────
    P15-FOLD-01) reset_db; clean_vault; setup_P15_FOLD_01 ;;

    # ── Phase 1.5 — Watcher / developer tests (full) ────────────────────────
    P15-DEV-01)  reset_db; clean_vault; setup_P15_DEV_01 ;;
    P15-DEV-02)  reset_db; clean_vault; setup_P15_DEV_02 ;;
    P15-DEV-03)  reset_db; clean_vault; setup_P15_DEV_03 ;;
    P15-DEV-04)  reset_db; clean_vault; setup_P15_DEV_04 ;;
    P15-DEV-05)  reset_db; clean_vault; setup_P15_DEV_05 ;;
    P15-DEV-06)  reset_db; clean_vault; setup_P15_DEV_06 ;;

    # ── Phase Pre-2 ──────────────────────────────────────────────────────────
    PRE2-DB-01)  reset_db; clean_vault; setup_PRE2_DB_01 ;;
    PRE2-DOM-01) reset_db; clean_vault; setup_PRE2_DOM_01 ;;

    # ── Vault-Restructure ────────────────────────────────────────────────────
    VR-PLACE-01)   reset_db; clean_vault; setup_VR_PLACE_01 ;;
    VR-PLACE-02)   reset_db; clean_vault; setup_VR_PLACE_02 ;;
    VR-SKIP-01)    reset_db; clean_vault; setup_VR_SKIP_01 ;;
    VR-REHOME-01)  reset_db; clean_vault; setup_VR_REHOME_01 ;;
    VR-CONTENT-01) reset_db; clean_vault; setup_VR_CONTENT_01 ;;

    *)
        echo "Unknown test ID: ${1}"
        echo "Usage: $0 [test-id|all|smoke|phase]"
        echo ""
        echo "Available IDs:"
        echo "  Smoke:  P1-CAP-01..05, P15-LOC-01..02"
        echo "  Phase:  P1-CAP-06..11, P15-REC-01..05, P15-HDL-01..07, P15-FOLD-01,"
        echo "          PRE2-DB-01, PRE2-DOM-01, VR-PLACE-01..02, VR-SKIP-01, VR-REHOME-01"
        echo "  Full:   P1-DEV-01..05, P15-DEV-01..06, VR-CONTENT-01"
        exit 1
        ;;
esac
