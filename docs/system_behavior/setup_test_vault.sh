#!/usr/bin/env bash
# Auto-generated from behavior_inventory.yaml — do not edit manually.
# Run: bash docs/system_behavior/setup_test_vault.sh [test-id|all|smoke|phase]
# No args = full reset + all fixtures.
#
# Directory layout:
#   VAULT   = testing.vault_path in config.yaml — kms operates here
#   STAGING = parent of VAULT — staging files the tester copies/drags into VAULT
#
# IMPORTANT: This script operates on the TEST vault, never the real vault.
# Last generated: 2026-06-09

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VAULT="$(cd "$PROJECT_ROOT" && uv run python -c "import yaml; print(yaml.safe_load(open('src/config/config.yaml'))['testing']['vault_path'])")"
STAGING="$(dirname "$VAULT")"
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
    find "$STAGING" -maxdepth 1 -type f -delete 2>/dev/null || true
    mkdir -p "$STAGING"
    mkdir -p "$VAULT/inbox"
    mkdir -p "$VAULT/Projects/Alpha/attachment/.summaries"
    mkdir -p "$VAULT/Domain/Finance"
    mkdir -p "$VAULT/Domain/Engineering"
    mkdir -p "$VAULT/Briefings"
    mkdir -p "$VAULT/Synthesis"
    mkdir -p "$VAULT/Documentation"
    echo "✓ Vault cleaned"
}

# ─── Phase 1 — Capture Pipeline (smoke) ────────────────────────────────────

# smoke | P1-CAP-01: .md file captured in-place with AI summary in frontmatter
setup_P1_CAP_01() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/test-md-capture.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/test-md-capture.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/test-md-capture.md" \
    ".md file captured in-place with AI summary in frontmatter.

    Test fixture for P1-CAP-01."
}

# smoke | P1-CAP-02: PDF already in a project folder creates sibling .md and moves binary to attachment/
setup_P1_CAP_02() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/sample-report.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/sample-report.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/.summaries/sample-report.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%sample-report.pdf%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/Projects/Alpha"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/Projects/Alpha/sample-report.pdf"
    else
        echo "⚠ P1-CAP-02: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# smoke | P1-CAP-03: Body text of .md file preserved exactly after capture
setup_P1_CAP_03() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/test-body-preservation.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/test-body-preservation.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/test-body-preservation.md" \
    "Body text of .md file preserved exactly after capture.

    Test fixture for P1-CAP-03."
}

# smoke | P1-CAP-04: Re-capture does not rename already-captured .md file (rename gate Rule 1)
setup_P1_CAP_04() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/test-rename-gate.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/test-rename-gate.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/test-rename-gate.md" \
    "Re-capture does not rename already-captured .md file (rename gate Rule 1).

    Test fixture for P1-CAP-04."
}

# smoke | P1-CAP-05: Scan captures un-indexed .md files, skips already-indexed
setup_P1_CAP_05() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/test-md-capture.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/test-md-capture.md'" 2>/dev/null || true
    rm -f "$VAULT/inbox/test-scan-uncaptured.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/test-scan-uncaptured.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/test-md-capture.md" \
    "Scan captures un-indexed .md files, skips already-indexed.

    Test fixture for P1-CAP-05."
    write_md "$VAULT/inbox/test-scan-uncaptured.md" \
    "Scan captures un-indexed .md files, skips already-indexed.

    Test fixture for P1-CAP-05."
}
# ─── Phase 1.5 — Location Tags + Attachment Layout + Reconcile (smoke) ─────

# smoke | P15-LOC-01: Note in Domain/ folder gets domain/<D> tag
setup_P15_LOC_01() {
    # ── cleanup ──
    rm -f "$VAULT/Domain/Finance/test-domain-tag.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Domain/Finance/test-domain-tag.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/Domain/Finance/test-domain-tag.md" \
    "Note in Domain/ folder gets domain/<D> tag.

    Test fixture for P15-LOC-01."
}

# smoke | P15-LOC-02: Note in Projects/ folder gets project field
setup_P15_LOC_02() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/test-project-tag.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Projects/Alpha/test-project-tag.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/Projects/Alpha/test-project-tag.md" \
    "Note in Projects/ folder gets project field.

    Test fixture for P15-LOC-02."
}
# ─── Phase 1 — Capture Pipeline (phase) ────────────────────────────────────

# phase | P1-CAP-06: DOCX file dropped in inbox creates pending-routing marker (no summary yet)
setup_P1_CAP_06() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/q3-planning-brief.docx"
    rm -f "$VAULT/inbox/.summaries/q3-planning-brief.docx.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%q3-planning-brief.docx%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/inbox"
    cd "$PROJECT_ROOT" && uv run python -c "from docx import Document; doc = Document(); doc.add_heading('Q3 Planning Brief', 0); doc.add_paragraph('DOCX file dropped in inbox creates pending-routing marker (n.'); doc.add_paragraph('Test fixture for P1-CAP-06.'); doc.save('$VAULT/inbox/q3-planning-brief.docx')"
}

# phase | P1-CAP-07: Watcher auto-captures new file drops
setup_P1_CAP_07() {
    # ── cleanup ──
    rm -f "$STAGING/auto-capture-test.md"
    rm -f "$VAULT/inbox/auto-capture-test.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/auto-capture-test.md'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$STAGING"
    write_md "$STAGING/auto-capture-test.md" \
    "Watcher auto-captures new file drops.

    Test fixture for P1-CAP-07."
}

# phase | P1-CAP-08: Gibberish-named inbox PDF gets pending-routing marker (rename deferred to Phase 2 Classify)
setup_P1_CAP_08() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/xkjdhfs83.pdf"
    rm -f "$VAULT/inbox/.summaries/xkjdhfs83.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%xkjdhfs83.pdf%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/inbox"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/inbox/xkjdhfs83.pdf"
    else
        echo "⚠ P1-CAP-08: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# phase | P1-CAP-09: Idempotent capture — unchanged .md file skipped
setup_P1_CAP_09() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/test-idempotent.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/test-idempotent.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/test-idempotent.md" \
    "Idempotent capture — unchanged .md file skipped.

    Test fixture for P1-CAP-09."
}

# phase | P1-CAP-10: CLUELESS routing — inbox binary gets real summary marker with needs-review status
setup_P1_CAP_10() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/mystery-file.pdf"
    rm -f "$VAULT/inbox/.summaries/mystery-file.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%mystery-file.pdf%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/inbox"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/inbox/mystery-file.pdf"
    else
        echo "⚠ P1-CAP-10: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# phase | P1-CAP-11: URL enrichment — sparse note with URLs gets content fetched
setup_P1_CAP_11() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/test-url-note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/test-url-note.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/test-url-note.md" \
    "Quick note — read this later: https://example.com

    Test fixture for P1-CAP-11.
    "
}
# ─── Phase 1.5 — Location Tags + Attachment Layout + Reconcile (phase) ─────

# phase | P15-REC-01: Reconcile removes stale domain tag when folder deleted
setup_P15_REC_01() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/test-stale-domain-tag.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/test-stale-domain-tag.md'" 2>/dev/null || true
    # ── create ──
    write_md_with_frontmatter "$VAULT/inbox/test-stale-domain-tag.md" \
    $'type: capture\ntags:\n- domain/OldDomain\n- type/capture' \
    "Reconcile removes stale domain tag when folder deleted.

    Test fixture for P15-REC-01."
}

# phase | P15-REC-02: Reconcile adds missing domain tag for note in Domain/ folder
setup_P15_REC_02() {
    # ── cleanup ──
    rm -f "$VAULT/Domain/Engineering/test-missing-domain-tag.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Domain/Engineering/test-missing-domain-tag.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/Domain/Engineering/test-missing-domain-tag.md" \
    "Reconcile adds missing domain tag for note in Domain/ folder.

    Test fixture for P15-REC-02."
}

# phase | P15-REC-03: Reconcile captures orphan binaries missing sibling .md
setup_P15_REC_03() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/attachment/orphan-report.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/attachment/orphan-report.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/attachment/.summaries/orphan-report.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%orphan-report.pdf%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/Projects/Alpha/attachment"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/Projects/Alpha/attachment/orphan-report.pdf"
    else
        echo "⚠ P15-REC-03: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# phase | P15-REC-04: Reconcile deletes orphan sibling when binary is gone
setup_P15_REC_04() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/attachment/.summaries/deleted-file.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Projects/Alpha/attachment/.summaries/deleted-file.pdf.md'" 2>/dev/null || true
    # ── create ──
    write_md_with_frontmatter "$VAULT/Projects/Alpha/attachment/.summaries/deleted-file.pdf.md" \
    $'type: attachment-summary\nattachment_path: Projects/Alpha/attachment/deleted-file.pdf' \
    "Reconcile deletes orphan sibling when binary is gone.

    Test fixture for P15-REC-04."
}

# phase | P15-REC-05: Reconcile clears stale batch_id when doc moved away from batch destination
setup_P15_REC_05() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/note-batch-test.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Projects/Alpha/note-batch-test.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/Projects/Alpha/note-batch-test.md" \
    "Reconcile clears stale batch_id when doc moved away from batch destination.

    Test fixture for P15-REC-05."
}

# phase | P15-HDL-01: XLSX file dropped in inbox gets pending-routing marker
setup_P15_HDL_01() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/q2-budget.xlsx"
    rm -f "$VAULT/inbox/.summaries/q2-budget.xlsx.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%q2-budget.xlsx%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/inbox"
    cd "$PROJECT_ROOT" && uv run python -c "import openpyxl; wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Q2 Budget'; ws.append(['Column A', 'Column B', 'Column C']); ws.append(['Row 1 A', 'Row 1 B', 'Row 1 C']); ws.append(['Row 2 A', 'Row 2 B', 'Row 2 C']); wb.save('$VAULT/inbox/q2-budget.xlsx')"
}

# phase | P15-HDL-02: PPTX file dropped in inbox gets pending-routing marker
setup_P15_HDL_02() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/deck.pptx"
    rm -f "$VAULT/inbox/.summaries/deck.pptx.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%deck.pptx%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/inbox"
    cd "$PROJECT_ROOT" && uv run python -c "from pptx import Presentation; prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[0]); slide.shapes.title.text = 'Deck'; slide.placeholders[1].text = 'Test fixture for P15-HDL-02.'; prs.save('$VAULT/inbox/deck.pptx')"
}

# phase | P15-HDL-03: CSV file dropped in inbox gets pending-routing marker
setup_P15_HDL_03() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/data.csv"
    rm -f "$VAULT/inbox/.summaries/data.csv.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%data.csv%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/inbox"
    printf 'name,value,notes\nrow1,100,test\nrow2,200,fixture\n' > "$VAULT/inbox/data.csv"
}

# phase | P15-HDL-04: HTML file dropped in inbox gets pending-routing marker
setup_P15_HDL_04() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/page.html"
    rm -f "$VAULT/inbox/.summaries/page.html.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%page.html%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/inbox"
    printf '<html><body><h1>Page</h1><p>Test fixture for P15-HDL-04.</p></body></html>\n' > "$VAULT/inbox/page.html"
}

# phase | P15-HDL-05: EML email file dropped in inbox gets pending-routing marker
setup_P15_HDL_05() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/message.eml"
    rm -f "$VAULT/inbox/.summaries/message.eml.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%message.eml%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/inbox"
    printf 'From: sender@example.com\nTo: receiver@example.com\nSubject: Test Email for P15-HDL-05\nDate: Wed, 04 Jun 2026 10:00:00 +0700\nMIME-Version: 1.0\nContent-Type: text/plain; charset=UTF-8\n\nTest email body for P15-HDL-05.\n' > "$VAULT/inbox/message.eml"
}

# phase | P15-HDL-06: MSG Outlook file dropped in inbox gets pending-routing marker
setup_P15_HDL_06() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/outlook-msg.msg"
    rm -f "$VAULT/inbox/.summaries/outlook-msg.msg.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%outlook-msg.msg%'" 2>/dev/null || true
    # ── create ──
    echo "⚠ P15-HDL-06: MSG files require Outlook format — place a test .msg file at $VAULT/inbox/outlook-msg.msg manually"
}

# phase | P15-HDL-07: PNG/JPG image file captured with sibling .md
setup_P15_HDL_07() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/screenshot.png"
    rm -f "$VAULT/inbox/.summaries/screenshot.png.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%screenshot.png%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/inbox"
    if [ -f "$FIXTURES/sample.png" ]; then
        cp "$FIXTURES/sample.png" "$VAULT/inbox/screenshot.png"
    else
        printf $'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB\x60\x82' > "$VAULT/inbox/screenshot.png"
        echo "⚠ P15-HDL-07: Created minimal 1x1 PNG fallback. Replace with a real image for testing."
    fi
}

# phase | P15-FOLD-01: Folder dropped in inbox classified and routed by LLM
setup_P15_FOLD_01() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/new-project-folder/file1.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/new-project-folder/file1.md'" 2>/dev/null || true
    rm -f "$VAULT/inbox/new-project-folder/file2.pdf"
    rm -f "$VAULT/inbox/.summaries/file2.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%file2.pdf%'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/new-project-folder/file1.md" \
    "Folder dropped in inbox classified and routed by LLM.

    Test fixture for P15-FOLD-01."
    mkdir -p "$VAULT/inbox/new-project-folder"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/inbox/new-project-folder/file2.pdf"
    else
        echo "⚠ P15-FOLD-01: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}
# ─── Phase Pre-2 — DB Schema + Domain Scalar Cleanup (phase) ───────────────

# phase | PRE2-DB-01: Capture populates project, status, key_topics DB columns
setup_PRE2_DB_01() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/test-db-columns.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Projects/Alpha/test-db-columns.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/Projects/Alpha/test-db-columns.md" \
    "Capture populates project, status, key_topics DB columns.

    Test fixture for PRE2-DB-01."
}

# phase | PRE2-DOM-01: Old domain: scalar stripped by reconcile (lazy migration — Stage 5)
setup_PRE2_DOM_01() {
    # ── cleanup ──
    rm -f "$VAULT/Domain/Finance/test-pre2-dom-01.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Domain/Finance/test-pre2-dom-01.md'" 2>/dev/null || true
    # ── create ──
    write_md_with_frontmatter "$VAULT/Domain/Finance/test-pre2-dom-01.md" \
    $'domain: finance\ntags:\n- domain/Finance\n- type/capture' \
    "Old domain: scalar stripped by reconcile (lazy migration — Stage 5).

    Test fixture for PRE2-DOM-01."
}
# ─── Vault-Restructure — Editable/No-Edit Split (phase) ────────────────────

# phase | VR-PLACE-01: No-edit binary (PDF/PNG/JPG) routed to attachment/
setup_VR_PLACE_01() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/vr-place-01-test.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/vr-place-01-test.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/.summaries/vr-place-01-test.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%vr-place-01-test.pdf%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/Projects/Alpha"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/Projects/Alpha/vr-place-01-test.pdf"
    else
        echo "⚠ VR-PLACE-01: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# phase | VR-PLACE-02: Editable binary (XLSX/DOCX/PPTX) routed to project/domain root
setup_VR_PLACE_02() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/vr-place-02-test.xlsx"
    rm -f "$VAULT/Projects/Alpha/.summaries/vr-place-02-test.xlsx.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%vr-place-02-test.xlsx%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/Projects/Alpha"
    cd "$PROJECT_ROOT" && uv run python -c "import openpyxl; wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Vr Place 02 Test'; ws.append(['Column A', 'Column B', 'Column C']); ws.append(['Row 1 A', 'Row 1 B', 'Row 1 C']); ws.append(['Row 2 A', 'Row 2 B', 'Row 2 C']); wb.save('$VAULT/Projects/Alpha/vr-place-02-test.xlsx')"
}

# phase | VR-SKIP-01: Watcher and scan skip AI-output folders (Briefings/Synthesis/Documentation)
setup_VR_SKIP_01() {
    # ── cleanup ──
    rm -f "$VAULT/Briefings/vr-skip-01-test.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Briefings/vr-skip-01-test.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/Briefings/vr-skip-01-test.md" \
    "Watcher and scan skip AI-output folders (Briefings/Synthesis/Documentation).

    Test fixture for VR-SKIP-01."
}

# phase | VR-REHOME-01: Misplaced binary re-homed to correct placement
setup_VR_REHOME_01() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/attachment/vr-rehome-01-test.xlsx"
    rm -f "$VAULT/Projects/Alpha/attachment/.summaries/vr-rehome-01-test.xlsx.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%vr-rehome-01-test.xlsx%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/Projects/Alpha/attachment"
    cd "$PROJECT_ROOT" && uv run python -c "import openpyxl; wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Vr Rehome 01 Test'; ws.append(['Column A', 'Column B', 'Column C']); ws.append(['Row 1 A', 'Row 1 B', 'Row 1 C']); ws.append(['Row 2 A', 'Row 2 B', 'Row 2 C']); wb.save('$VAULT/Projects/Alpha/attachment/vr-rehome-01-test.xlsx')"
}
# ─── Phase 2 (phase) ───────────────────────────────────────────────────────

# phase | P2-REC-01: reconcile strips deprecated frontmatter key from a note that already has valid tags and correct project field
setup_P2_REC_01() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Projects/Alpha/note.md'" 2>/dev/null || true
    # ── create ──
    write_md_with_frontmatter "$VAULT/Projects/Alpha/note.md" \
    $'type: note\ntags: []\nproject: Alpha\ndomain: finance' \
    "reconcile strips deprecated frontmatter key from a note that already has valid tags and correct project field.

    Test fixture for P2-REC-01."
}

# phase | P2-REC-02: reconcile leaves a human-locked note untouched even if it has a deprecated frontmatter key
setup_P2_REC_02() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/locked.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Projects/Alpha/locked.md'" 2>/dev/null || true
    # ── create ──
    write_md_with_frontmatter "$VAULT/Projects/Alpha/locked.md" \
    $'type: note\ntags: []\nproject: Alpha\ndomain: finance\nupdated_by_human: true' \
    "reconcile leaves a human-locked note untouched even if it has a deprecated frontmatter key.

    Test fixture for P2-REC-02."
}

# phase | P2-REC-03: reconcile does not write a note that has no deprecated keys and no other dirty reason
setup_P2_REC_03() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/clean.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Projects/Alpha/clean.md'" 2>/dev/null || true
    # ── create ──
    write_md_with_frontmatter "$VAULT/Projects/Alpha/clean.md" \
    $'type: note\ntags: []\nproject: Alpha' \
    "reconcile does not write a note that has no deprecated keys and no other dirty reason.

    Test fixture for P2-REC-03."
}

# phase | P2-CIC-01: Loose inbox .md note with a confident destination is moved into the project/domain folder DERIVED from its assigned tags+project (AUTO)
setup_P2_CIC_01() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/p2cic-01-clear-project-note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/p2cic-01-clear-project-note.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/p2cic-01-clear-project-note.md" \
    "Loose inbox .md note with a confident destination is moved into the project/domain folder DERIVED from its assigned tags+project (AUTO).

    Test fixture for P2-CIC-01."
}

# phase | P2-CIC-02: Loose inbox note with a medium-confidence destination stays in inbox with suggested destination recorded (SUGGEST)
setup_P2_CIC_02() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/p2cic-02-ambiguous-note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/p2cic-02-ambiguous-note.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/p2cic-02-ambiguous-note.md" \
    "Loose inbox note with a medium-confidence destination stays in inbox with suggested destination recorded (SUGGEST).

    Test fixture for P2-CIC-02."
}

# phase | P2-CIC-03: Loose inbox note the AI cannot place stays in inbox marked stuck (CLUELESS)
setup_P2_CIC_03() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/p2cic-03-no-match-note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/p2cic-03-no-match-note.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/p2cic-03-no-match-note.md" \
    "Loose inbox note the AI cannot place stays in inbox marked stuck (CLUELESS).

    Test fixture for P2-CIC-03."
}

# phase | P2-CIC-04: A file dropped directly into a project/domain folder is filed by location WITHOUT any AI classify call
setup_P2_CIC_04() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/p2cic-04-located-note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Projects/Alpha/p2cic-04-located-note.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/Projects/Alpha/p2cic-04-located-note.md" \
    "A file dropped directly into a project/domain folder is filed by location WITHOUT any AI classify call.

    Test fixture for P2-CIC-04."
}

# phase | P2-CIC-05: Loose inbox PDF gets a rich attachment summary AND is classified at drop time (two AI calls; pending-routing marker retired)
setup_P2_CIC_05() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/p2cic-05-clear-project-report.pdf"
    rm -f "$VAULT/inbox/.summaries/p2cic-05-clear-project-report.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%p2cic-05-clear-project-report.pdf%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/inbox"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/inbox/p2cic-05-clear-project-report.pdf"
    else
        echo "⚠ P2-CIC-05: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# phase | P2-CIC-10: A single file dropped in a project/domain folder is still summarized + frontmatter-stamped; only the classify step and the move are skipped (LOCATED is NOT a no-op)
setup_P2_CIC_10() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/p2cic-10-located-summary-note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Projects/Alpha/p2cic-10-located-summary-note.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/Projects/Alpha/p2cic-10-located-summary-note.md" \
    "A single file dropped in a project/domain folder is still summarized + frontmatter-stamped; only the classify step and the move are skipped (LOCATED is NOT a no-op).

    Test fixture for P2-CIC-10."
}

# phase | P2-CIC-11: When the AI assigns a project, the note moves to that Project folder even though it also carries domain tags (project beats domain — precedence)
setup_P2_CIC_11() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/p2cic-11-project-and-domain-note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/p2cic-11-project-and-domain-note.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/p2cic-11-project-and-domain-note.md" \
    "When the AI assigns a project, the note moves to that Project folder even though it also carries domain tags (project beats domain — precedence).

    Test fixture for P2-CIC-11."
}

# phase | P2-CIC-12: A loose note with NO project and MULTIPLE domain tags moves to the AI's designated PRIMARY domain folder while keeping all its domain tags
setup_P2_CIC_12() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/p2cic-12-multi-domain-note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/p2cic-12-multi-domain-note.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/p2cic-12-multi-domain-note.md" \
    "A loose note with NO project and MULTIPLE domain tags moves to the AI's designated PRIMARY domain folder while keeping all its domain tags.

    Test fixture for P2-CIC-12."
}
# ─── Phase 1 — Capture Pipeline (full) ─────────────────────────────────────

# full | P1-DEV-01: Audit trail written for every capture decision
setup_P1_DEV_01() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/p1-dev-01-test.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/p1-dev-01-test.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/p1-dev-01-test.md" \
    "Audit trail written for every capture decision.

    Test fixture for P1-DEV-01."
}

# full | P1-DEV-02: Tag violation audited — invalid tags stripped and logged
setup_P1_DEV_02() {
    echo "P1-DEV-02: No pre-created fixtures. Trigger: kms capture <file.md> (LLM returns bad tag)"
}

# full | P1-DEV-03: File-lost guard — deleted file between event and pipeline
setup_P1_DEV_03() {
    echo "P1-DEV-03: No pre-created fixtures. Trigger: kms capture <path> (file deleted before pipeline reads)"
}

# full | P1-DEV-04: Cooldown gate rejects file still being written
setup_P1_DEV_04() {
    echo "P1-DEV-04: No pre-created fixtures. Trigger: kms capture <file> (mtime < cooldown_seconds ago)"
}
# ─── Phase 1.5 — Location Tags + Attachment Layout + Reconcile (full) ──────

# full | P15-DEV-01: Watcher binary-delete sync — sibling cleaned up
setup_P15_DEV_01() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/attachment/p15-dev-01-report.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/attachment/p15-dev-01-report.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/attachment/.summaries/p15-dev-01-report.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%p15-dev-01-report.pdf%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/Projects/Alpha/attachment"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/Projects/Alpha/attachment/p15-dev-01-report.pdf"
    else
        echo "⚠ P15-DEV-01: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# full | P15-DEV-02: Watcher binary-rename sync — sibling renamed
setup_P15_DEV_02() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/attachment/p15-dev-02-old.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/attachment/p15-dev-02-old.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/attachment/.summaries/p15-dev-02-old.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%p15-dev-02-old.pdf%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/Projects/Alpha/attachment"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/Projects/Alpha/attachment/p15-dev-02-old.pdf"
    else
        echo "⚠ P15-DEV-02: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# full | P15-DEV-03: Watcher binary cross-folder move — old sibling orphaned
setup_P15_DEV_03() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/attachment/p15-dev-03-file.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/attachment/p15-dev-03-file.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/attachment/.summaries/p15-dev-03-file.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%p15-dev-03-file.pdf%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/Projects/Alpha/attachment"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/Projects/Alpha/attachment/p15-dev-03-file.pdf"
    else
        echo "⚠ P15-DEV-03: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# full | P15-DEV-04: Reconcile stage 3 — re-summarize stale binaries
setup_P15_DEV_04() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/attachment/p15-dev-04-report.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/attachment/p15-dev-04-report.pdf"
    rm -f "$VAULT/Projects/Alpha/attachment/attachment/.summaries/p15-dev-04-report.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%p15-dev-04-report.pdf%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/Projects/Alpha/attachment"
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/Projects/Alpha/attachment/p15-dev-04-report.pdf"
    else
        echo "⚠ P15-DEV-04: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"
    fi
}

# full | P15-DEV-05: Debounce coalescing — rapid file events produce single capture
setup_P15_DEV_05() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/p15-dev-05-debounce.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/p15-dev-05-debounce.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/p15-dev-05-debounce.md" \
    "Debounce coalescing — rapid file events produce single capture.

    Test fixture for P15-DEV-05."
}

# full | P15-DEV-06: Scan skips .summaries/ paths in added loop
setup_P15_DEV_06() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/attachment/.summaries/p15-dev-06-test.pdf.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'Projects/Alpha/attachment/.summaries/p15-dev-06-test.pdf.md'" 2>/dev/null || true
    # ── create ──
    write_md_with_frontmatter "$VAULT/Projects/Alpha/attachment/.summaries/p15-dev-06-test.pdf.md" \
    $'type: attachment-summary\nattachment_path: Projects/Alpha/attachment/p15-dev-06-test.pdf' \
    "Scan skips .summaries/ paths in added loop.

    Test fixture for P15-DEV-06."
}

# full | P15-DEV-07: In-flight guard — modify event during running pipeline does not launch second capture
setup_P15_DEV_07() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/sample.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/sample.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/sample.md" \
    "In-flight guard — modify event during running pipeline does not launch second capture.

    Test fixture for P15-DEV-07."
}
# ─── Vault-Restructure — Editable/No-Edit Split (full) ─────────────────────

# full | VR-CONTENT-01: Binary content change detected and re-summarized
setup_VR_CONTENT_01() {
    # ── cleanup ──
    rm -f "$VAULT/Projects/Alpha/attachment/vr-content-01-test.xlsx"
    rm -f "$VAULT/Projects/Alpha/attachment/.summaries/vr-content-01-test.xlsx.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path LIKE '%vr-content-01-test.xlsx%'" 2>/dev/null || true
    # ── create ──
    mkdir -p "$VAULT/Projects/Alpha/attachment"
    cd "$PROJECT_ROOT" && uv run python -c "import openpyxl; wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Vr Content 01 Test'; ws.append(['Column A', 'Column B', 'Column C']); ws.append(['Row 1 A', 'Row 1 B', 'Row 1 C']); ws.append(['Row 2 A', 'Row 2 B', 'Row 2 C']); wb.save('$VAULT/Projects/Alpha/attachment/vr-content-01-test.xlsx')"
}
# ─── Phase 2 (full) ────────────────────────────────────────────────────────

# full | P2-CIC-06: Every classify outcome (AUTO/SUGGEST/CLUELESS) writes exactly one decision-log audit row
setup_P2_CIC_06() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/p2cic-06-audit-note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/p2cic-06-audit-note.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/p2cic-06-audit-note.md" \
    "Every classify outcome (AUTO/SUGGEST/CLUELESS) writes exactly one decision-log audit row.

    Test fixture for P2-CIC-06."
}

# full | P2-CIC-07: A classify AUTO-move to a project/domain root leaves batch_id NULL; index location stays consistent
setup_P2_CIC_07() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/p2cic-07-batch-note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/p2cic-07-batch-note.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/p2cic-07-batch-note.md" \
    "A classify AUTO-move to a project/domain root leaves batch_id NULL; index location stays consistent.

    Test fixture for P2-CIC-07."
}

# full | P2-CIC-08: A classify AUTO-move under the running watcher does not double-fire re-home or create a duplicate row
setup_P2_CIC_08() {
    echo "P2-CIC-08: No pre-created fixtures. Trigger: kms watch + a loose inbox file that classify AUTO-routes"
}

# full | P2-CIC-09: A file captured as part of a dropped folder is fully captured but NOT individually re-classified (SUPPRESS — the folder is the routing unit)
setup_P2_CIC_09() {
    echo "P2-CIC-09: No pre-created fixtures. Trigger: kms watch / capture_folder on a folder dropped in inbox/"
}

# full | P2-CIC-13: Structural consistency: an AUTO-filed note's on-disk folder always matches its stamped project/primary-domain (no free pick that could disagree)
setup_P2_CIC_13() {
    # ── cleanup ──
    rm -f "$VAULT/inbox/p2cic-13-consistency-note.md"
    sqlite3 "$DB" "DELETE FROM documents WHERE vault_path = 'inbox/p2cic-13-consistency-note.md'" 2>/dev/null || true
    # ── create ──
    write_md "$VAULT/inbox/p2cic-13-consistency-note.md" \
    "Structural consistency: an AUTO-filed note's on-disk folder always matches its stamped project/primary-domain (no free pick that could disagree).

    Test fixture for P2-CIC-13."
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
    setup_P2_REC_01
    setup_P2_REC_02
    setup_P2_REC_03
    setup_P2_CIC_01
    setup_P2_CIC_02
    setup_P2_CIC_03
    setup_P2_CIC_04
    setup_P2_CIC_05
    setup_P2_CIC_10
    setup_P2_CIC_11
    setup_P2_CIC_12
}

run_all() {
    run_phase
    setup_P1_DEV_01
    setup_P1_DEV_02
    setup_P1_DEV_03
    setup_P1_DEV_04
    setup_P15_DEV_01
    setup_P15_DEV_02
    setup_P15_DEV_03
    setup_P15_DEV_04
    setup_P15_DEV_05
    setup_P15_DEV_06
    setup_P15_DEV_07
    setup_VR_CONTENT_01
    setup_P2_CIC_06
    setup_P2_CIC_07
    setup_P2_CIC_08
    setup_P2_CIC_09
    setup_P2_CIC_13
}

# ─── Main dispatch ─────────────────────────────────────────────────────────────

echo "=== AI-KMS Test Vault Setup ==="
echo "Vault:   $VAULT"
echo "Staging: $STAGING"
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
    # ── Smoke ──
    P1-CAP-01)  reset_db; clean_vault; setup_P1_CAP_01 ;;
    P1-CAP-02)  reset_db; clean_vault; setup_P1_CAP_02 ;;
    P1-CAP-03)  reset_db; clean_vault; setup_P1_CAP_03 ;;
    P1-CAP-04)  reset_db; clean_vault; setup_P1_CAP_04 ;;
    P1-CAP-05)  reset_db; clean_vault; setup_P1_CAP_05 ;;
    P15-LOC-01)  reset_db; clean_vault; setup_P15_LOC_01 ;;
    P15-LOC-02)  reset_db; clean_vault; setup_P15_LOC_02 ;;
    # ── Phase ──
    P1-CAP-06)  reset_db; clean_vault; setup_P1_CAP_06 ;;
    P1-CAP-07)  reset_db; clean_vault; setup_P1_CAP_07 ;;
    P1-CAP-08)  reset_db; clean_vault; setup_P1_CAP_08 ;;
    P1-CAP-09)  reset_db; clean_vault; setup_P1_CAP_09 ;;
    P1-CAP-10)  reset_db; clean_vault; setup_P1_CAP_10 ;;
    P1-CAP-11)  reset_db; clean_vault; setup_P1_CAP_11 ;;
    P15-REC-01)  reset_db; clean_vault; setup_P15_REC_01 ;;
    P15-REC-02)  reset_db; clean_vault; setup_P15_REC_02 ;;
    P15-REC-03)  reset_db; clean_vault; setup_P15_REC_03 ;;
    P15-REC-04)  reset_db; clean_vault; setup_P15_REC_04 ;;
    P15-REC-05)  reset_db; clean_vault; setup_P15_REC_05 ;;
    P15-HDL-01)  reset_db; clean_vault; setup_P15_HDL_01 ;;
    P15-HDL-02)  reset_db; clean_vault; setup_P15_HDL_02 ;;
    P15-HDL-03)  reset_db; clean_vault; setup_P15_HDL_03 ;;
    P15-HDL-04)  reset_db; clean_vault; setup_P15_HDL_04 ;;
    P15-HDL-05)  reset_db; clean_vault; setup_P15_HDL_05 ;;
    P15-HDL-06)  reset_db; clean_vault; setup_P15_HDL_06 ;;
    P15-HDL-07)  reset_db; clean_vault; setup_P15_HDL_07 ;;
    P15-FOLD-01)  reset_db; clean_vault; setup_P15_FOLD_01 ;;
    PRE2-DB-01)  reset_db; clean_vault; setup_PRE2_DB_01 ;;
    PRE2-DOM-01)  reset_db; clean_vault; setup_PRE2_DOM_01 ;;
    VR-PLACE-01)  reset_db; clean_vault; setup_VR_PLACE_01 ;;
    VR-PLACE-02)  reset_db; clean_vault; setup_VR_PLACE_02 ;;
    VR-SKIP-01)  reset_db; clean_vault; setup_VR_SKIP_01 ;;
    VR-REHOME-01)  reset_db; clean_vault; setup_VR_REHOME_01 ;;
    P2-REC-01)  reset_db; clean_vault; setup_P2_REC_01 ;;
    P2-REC-02)  reset_db; clean_vault; setup_P2_REC_02 ;;
    P2-REC-03)  reset_db; clean_vault; setup_P2_REC_03 ;;
    P2-CIC-01)  reset_db; clean_vault; setup_P2_CIC_01 ;;
    P2-CIC-02)  reset_db; clean_vault; setup_P2_CIC_02 ;;
    P2-CIC-03)  reset_db; clean_vault; setup_P2_CIC_03 ;;
    P2-CIC-04)  reset_db; clean_vault; setup_P2_CIC_04 ;;
    P2-CIC-05)  reset_db; clean_vault; setup_P2_CIC_05 ;;
    P2-CIC-10)  reset_db; clean_vault; setup_P2_CIC_10 ;;
    P2-CIC-11)  reset_db; clean_vault; setup_P2_CIC_11 ;;
    P2-CIC-12)  reset_db; clean_vault; setup_P2_CIC_12 ;;
    # ── Full ──
    P1-DEV-01)  reset_db; clean_vault; setup_P1_DEV_01 ;;
    P1-DEV-02)  reset_db; clean_vault; setup_P1_DEV_02 ;;
    P1-DEV-03)  reset_db; clean_vault; setup_P1_DEV_03 ;;
    P1-DEV-04)  reset_db; clean_vault; setup_P1_DEV_04 ;;
    P15-DEV-01)  reset_db; clean_vault; setup_P15_DEV_01 ;;
    P15-DEV-02)  reset_db; clean_vault; setup_P15_DEV_02 ;;
    P15-DEV-03)  reset_db; clean_vault; setup_P15_DEV_03 ;;
    P15-DEV-04)  reset_db; clean_vault; setup_P15_DEV_04 ;;
    P15-DEV-05)  reset_db; clean_vault; setup_P15_DEV_05 ;;
    P15-DEV-06)  reset_db; clean_vault; setup_P15_DEV_06 ;;
    P15-DEV-07)  reset_db; clean_vault; setup_P15_DEV_07 ;;
    VR-CONTENT-01)  reset_db; clean_vault; setup_VR_CONTENT_01 ;;
    P2-CIC-06)  reset_db; clean_vault; setup_P2_CIC_06 ;;
    P2-CIC-07)  reset_db; clean_vault; setup_P2_CIC_07 ;;
    P2-CIC-08)  reset_db; clean_vault; setup_P2_CIC_08 ;;
    P2-CIC-09)  reset_db; clean_vault; setup_P2_CIC_09 ;;
    P2-CIC-13)  reset_db; clean_vault; setup_P2_CIC_13 ;;
    *)
        echo "Unknown test ID: ${1}"
        echo "Usage: $0 [test-id|all|smoke|phase]"
        exit 1
        ;;
esac
