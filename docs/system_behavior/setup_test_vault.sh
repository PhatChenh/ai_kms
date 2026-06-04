#!/usr/bin/env bash
# Auto-generated from behavior_inventory.yaml — do not edit manually.
# Run: bash docs/system_behavior/setup_test_vault.sh [test-id|all|smoke|phase]
# No args = full reset + all fixtures. Pass test ID for per-test isolation.
#
# IMPORTANT: This script operates on the TEST vault, never the real vault.
# Last generated: 2026-06-04

set -euo pipefail

VAULT="/Users/phatchenh/ai_kms_test_vault"
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FIXTURES="$PROJECT_ROOT/tests/fixtures"
DB="$PROJECT_ROOT/data/kb.db"

# ─── Helpers ───

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
    echo "✓ Vault cleaned"
}

# ─── Per-test fixture setup ───

setup_P1_CAP_01() {
    write_md "$VAULT/inbox/test-md-capture.md" \
"Meeting notes from Q3 planning session.

Key decisions:
- Launch MVP by end of month
- Focus on capture pipeline first
- Defer search to Phase 3"
}

setup_P1_CAP_02() {
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/inbox/sample-report.pdf"
    else
        echo "⚠ No sample PDF at $FIXTURES/sample_text.pdf — create a test PDF manually"
    fi
}

setup_P1_CAP_03() {
    write_md "$VAULT/inbox/test-body-preservation.md" \
"This is my own writing. Do not change this content.

- First important point
- Second important point
- Third important point"
}

setup_P1_CAP_04() {
    write_md "$VAULT/inbox/test-rename-gate.md" \
"Quarterly review notes for the engineering team.

Performance highlights and areas for improvement."
}

setup_P1_CAP_05() {
    write_md "$VAULT/inbox/test-scan-uncaptured.md" \
"This file has never been captured before.

It should be picked up by kms capture --scan."
}

setup_P1_CAP_06() {
    if [ -f "$FIXTURES/sample.docx" ]; then
        cp "$FIXTURES/sample.docx" "$VAULT/inbox/q3-planning-brief.docx"
    else
        echo "⚠ No sample DOCX at $FIXTURES/sample.docx — create a test DOCX manually"
    fi
}

setup_P1_CAP_07() {
    # Fixture created during test (dropped into inbox while watcher runs)
    echo "P1-CAP-07: No pre-created fixture — file is created during the test."
}

setup_P1_CAP_08() {
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/inbox/xkjdhfs83.pdf"
    else
        echo "⚠ No sample PDF at $FIXTURES/sample_text.pdf — create a test PDF manually"
    fi
}

setup_P1_CAP_09() {
    write_md "$VAULT/inbox/test-idempotent.md" \
"Content for idempotent capture test.

This should only be processed once if content is unchanged."
}

setup_P1_CAP_10() {
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/inbox/mystery-file.pdf"
    else
        echo "⚠ No sample PDF — create a test PDF manually"
    fi
}

setup_P1_CAP_11() {
    write_md "$VAULT/inbox/test-url-note.md" \
"Check this out: https://example.com/article"
}

setup_P15_LOC_01() {
    write_md "$VAULT/Domain/Finance/test-domain-tag.md" \
"Finance team budget analysis for Q3."
}

setup_P15_LOC_02() {
    write_md "$VAULT/Projects/Alpha/test-project-tag.md" \
"Project Alpha kickoff notes and MVP scope."
}

setup_P15_REC_01() {
    write_md_with_frontmatter "$VAULT/inbox/test-stale-domain-tag.md" \
"tags:
  - domain/OldDomain
  - type/note
  - quarterly-review" \
"This note has a stale domain tag pointing to a deleted folder."
}

setup_P15_REC_02() {
    write_md "$VAULT/Domain/Engineering/test-missing-domain-tag.md" \
"Engineering retrospective notes — this file is under Domain/Engineering/ but has no domain tag."
}

setup_P15_REC_03() {
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        mkdir -p "$VAULT/Projects/Alpha/attachment"
        cp "$FIXTURES/sample_text.pdf" "$VAULT/Projects/Alpha/attachment/orphan-report.pdf"
    else
        echo "⚠ No sample PDF — create a test PDF manually"
    fi
}

setup_P15_REC_04() {
    mkdir -p "$VAULT/Projects/Alpha/attachment/.summaries"
    write_md_with_frontmatter "$VAULT/Projects/Alpha/attachment/.summaries/deleted-file.pdf.md" \
"type: attachment-summary
attachment_path: Projects/Alpha/attachment/deleted-file.pdf
summary: Summary of a file that no longer exists" \
"This sibling points to a binary that has been deleted."
}

setup_P15_HDL_01() {
    if [ -f "$FIXTURES/sample.xlsx" ]; then
        cp "$FIXTURES/sample.xlsx" "$VAULT/inbox/q2-budget.xlsx"
    else
        echo "⚠ No sample XLSX at $FIXTURES/sample.xlsx — create one manually"
    fi
}

setup_P15_HDL_02() {
    if [ -f "$FIXTURES/sample.pptx" ]; then
        cp "$FIXTURES/sample.pptx" "$VAULT/inbox/deck.pptx"
    else
        echo "⚠ No sample PPTX at $FIXTURES/sample.pptx — create one manually"
    fi
}

setup_P15_HDL_03() {
    printf 'name,department,budget\nAlpha,Engineering,50000\nBeta,Marketing,30000\n' > "$VAULT/inbox/data.csv"
}

setup_P15_HDL_04() {
    printf '<html><body><h1>Test Page</h1><p>This is a test HTML page for capture.</p></body></html>\n' > "$VAULT/inbox/page.html"
}

setup_P15_HDL_05() {
    # Minimal .eml structure
    printf 'From: sender@example.com\nTo: receiver@example.com\nSubject: Test Email\nDate: Wed, 04 Jun 2026 10:00:00 +0700\n\nThis is a test email body for capture testing.\n' > "$VAULT/inbox/message.eml"
}

setup_P15_HDL_06() {
    echo "⚠ MSG files require Outlook format — cannot generate from bash. Place a test .msg file manually."
}

setup_P15_HDL_07() {
    if [ -f "$FIXTURES/sample.png" ]; then
        cp "$FIXTURES/sample.png" "$VAULT/inbox/screenshot.png"
    else
        # Create a minimal 1x1 PNG
        printf '\x89PNG\r\n\x1a\n' > "$VAULT/inbox/screenshot.png"
        echo "⚠ Created minimal PNG — replace with a real image for OCR testing"
    fi
}

setup_P15_FOLD_01() {
    mkdir -p "$VAULT/inbox/new-project-folder"
    write_md "$VAULT/inbox/new-project-folder/file1.md" \
"First file in a dropped folder. Project planning notes."
    if [ -f "$FIXTURES/sample_text.pdf" ]; then
        cp "$FIXTURES/sample_text.pdf" "$VAULT/inbox/new-project-folder/file2.pdf"
    else
        echo "⚠ No sample PDF for folder drop test"
    fi
}

setup_PRE2_DB_01() {
    write_md "$VAULT/Projects/Alpha/test-db-columns.md" \
"Project Alpha database column test. Should populate project, key_topics in DB."
}

setup_PRE2_DOM_01() {
    write_md_with_frontmatter "$VAULT/inbox/test-old-domain-scalar.md" \
"domain: finance
tags:
  - domain/Finance
  - type/note" \
"This note has the old domain: scalar that should be stripped on capture."
}

# ─── Tier runners ───

setup_smoke() {
    setup_P1_CAP_01
    setup_P1_CAP_02
    setup_P1_CAP_03
    setup_P1_CAP_04
    setup_P1_CAP_05
    setup_P15_LOC_01
    setup_P15_LOC_02
}

setup_phase() {
    setup_smoke
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
}

setup_all() {
    setup_phase
}

# ─── Main dispatch ───

echo "=== AI-KMS Test Vault Setup ==="
echo "Vault: $VAULT"
echo ""

case "${1:-all}" in
    all)      reset_db; clean_vault; setup_all;   echo ""; echo "✓ All fixtures ready" ;;
    smoke)    reset_db; clean_vault; setup_smoke;  echo ""; echo "✓ Smoke fixtures ready" ;;
    phase)    reset_db; clean_vault; setup_phase;  echo ""; echo "✓ Phase fixtures ready" ;;
    P1-CAP-01)  reset_db; clean_vault; setup_P1_CAP_01 ;;
    P1-CAP-02)  reset_db; clean_vault; setup_P1_CAP_02 ;;
    P1-CAP-03)  reset_db; clean_vault; setup_P1_CAP_03 ;;
    P1-CAP-04)  reset_db; clean_vault; setup_P1_CAP_04 ;;
    P1-CAP-05)  reset_db; clean_vault; setup_P1_CAP_05 ;;
    P1-CAP-06)  reset_db; clean_vault; setup_P1_CAP_06 ;;
    P1-CAP-07)  reset_db; clean_vault; setup_P1_CAP_07 ;;
    P1-CAP-08)  reset_db; clean_vault; setup_P1_CAP_08 ;;
    P1-CAP-09)  reset_db; clean_vault; setup_P1_CAP_09 ;;
    P1-CAP-10)  reset_db; clean_vault; setup_P1_CAP_10 ;;
    P1-CAP-11)  reset_db; clean_vault; setup_P1_CAP_11 ;;
    P15-LOC-01) reset_db; clean_vault; setup_P15_LOC_01 ;;
    P15-LOC-02) reset_db; clean_vault; setup_P15_LOC_02 ;;
    P15-REC-01) reset_db; clean_vault; setup_P15_REC_01 ;;
    P15-REC-02) reset_db; clean_vault; setup_P15_REC_02 ;;
    P15-REC-03) reset_db; clean_vault; setup_P15_REC_03 ;;
    P15-REC-04) reset_db; clean_vault; setup_P15_REC_04 ;;
    P15-REC-05) reset_db; clean_vault; echo "P15-REC-05 requires manual DB state setup" ;;
    P15-HDL-01) reset_db; clean_vault; setup_P15_HDL_01 ;;
    P15-HDL-02) reset_db; clean_vault; setup_P15_HDL_02 ;;
    P15-HDL-03) reset_db; clean_vault; setup_P15_HDL_03 ;;
    P15-HDL-04) reset_db; clean_vault; setup_P15_HDL_04 ;;
    P15-HDL-05) reset_db; clean_vault; setup_P15_HDL_05 ;;
    P15-HDL-06) reset_db; clean_vault; setup_P15_HDL_06 ;;
    P15-HDL-07) reset_db; clean_vault; setup_P15_HDL_07 ;;
    P15-FOLD-01) reset_db; clean_vault; setup_P15_FOLD_01 ;;
    PRE2-DB-01) reset_db; clean_vault; setup_PRE2_DB_01 ;;
    PRE2-DOM-01) reset_db; clean_vault; setup_PRE2_DOM_01 ;;
    *) echo "Unknown test ID: $1"; echo "Usage: $0 [test-id|all|smoke|phase]"; exit 1 ;;
esac
