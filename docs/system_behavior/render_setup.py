#!/usr/bin/env python3
"""
Render setup_test_vault.sh from behavior_inventory.yaml.

Usage:
    uv run python docs/system_behavior/render_setup.py > docs/system_behavior/setup_test_vault.sh
    chmod +x docs/system_behavior/setup_test_vault.sh

Writes to stdout; caller redirects to file.
"""
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INVENTORY = PROJECT_ROOT / "docs" / "system_behavior" / "behavior_inventory.yaml"
CONFIG = PROJECT_ROOT / "src" / "config" / "config.yaml"

SCRIPT_HEADER = """\
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
# Last generated: {date}

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VAULT="$(cd "$PROJECT_ROOT" && uv run python -c "import yaml; print(yaml.safe_load(open('src/config/config.yaml'))['testing']['vault_path'])")"
STAGING="$(dirname "$VAULT")"
FIXTURES="$PROJECT_ROOT/tests/fixtures"
DB="$PROJECT_ROOT/data/kb.db"

# ─── Helpers ───────────────────────────────────────────────────────────────────

reset_db() {{
    rm -f "$DB"
    cd "$PROJECT_ROOT"
    uv run python -c "from storage.db import init_db; from pathlib import Path; init_db(Path('$DB'))"
    echo "✓ Database reset"
}}

ensure_dir() {{
    mkdir -p "$(dirname "$1")"
}}

write_md() {{
    local path="$1"
    local body="$2"
    ensure_dir "$path"
    printf '%s\\n' "$body" > "$path"
}}

write_md_with_frontmatter() {{
    local path="$1"
    local frontmatter="$2"
    local body="$3"
    ensure_dir "$path"
    printf '%s\\n%s\\n%s\\n%s\\n' "---" "$frontmatter" "---" "$body" > "$path"
}}

clean_vault() {{
    echo "Cleaning test vault at $VAULT ..."
    rm -rf "${{VAULT:?}}"/*
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
}}
"""

SCRIPT_FOOTER_TEMPLATE = """\

# ─── Tier runners ──────────────────────────────────────────────────────────────

run_smoke() {{
{smoke_calls}}}

run_phase() {{
    run_smoke
{phase_calls}}}

run_all() {{
    run_phase
{full_calls}}}

# ─── Main dispatch ─────────────────────────────────────────────────────────────

echo "=== AI-KMS Test Vault Setup ==="
echo "Vault:   $VAULT"
echo "Staging: $STAGING"
echo ""

case "${{1:-all}}" in
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
{case_entries}
    *)
        echo "Unknown test ID: ${{1}}"
        echo "Usage: $0 [test-id|all|smoke|phase]"
        exit 1
        ;;
esac
"""


def fn_name(entry_id: str) -> str:
    return "setup_" + entry_id.replace("-", "_")


def indent(text: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


def md_content(entry: dict, fixture_path: str) -> str:
    behavior = entry["behavior"].rstrip(".")
    return f"{behavior}.\n\nTest fixture for {entry['id']}."


def cleanup_lines(fixture_path: str) -> list:
    """Bash lines to remove a vault fixture + all derived paths + DB records."""
    ext = Path(fixture_path).suffix.lower()
    filename = Path(fixture_path).name
    parts = Path(fixture_path).parts
    parent = str(Path(fixture_path).parent)

    lines = [f'rm -f "$VAULT/{fixture_path}"']

    is_no_edit = ext in (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp")
    is_editable = ext in (".xlsx", ".docx", ".pptx", ".csv", ".html", ".eml", ".msg")

    if is_no_edit or is_editable:
        in_inbox = len(parts) > 0 and parts[0] == "inbox"
        in_project = len(parts) > 1 and parts[0] == "Projects"
        in_domain = len(parts) > 1 and parts[0] == "Domain"

        if is_no_edit and (in_project or in_domain):
            lines.append(f'rm -f "$VAULT/{parent}/attachment/{filename}"')
            lines.append(f'rm -f "$VAULT/{parent}/attachment/.summaries/{filename}.md"')
        elif is_no_edit and in_inbox:
            lines.append(f'rm -f "$VAULT/inbox/.summaries/{filename}.md"')
        elif is_editable and (in_project or in_domain):
            lines.append(f'rm -f "$VAULT/{parent}/.summaries/{filename}.md"')
        elif is_editable and in_inbox:
            lines.append(f'rm -f "$VAULT/inbox/.summaries/{filename}.md"')

        lines.append(
            f"sqlite3 \"$DB\" \"DELETE FROM documents WHERE vault_path LIKE '%{filename}%'\" 2>/dev/null || true"
        )
    else:
        lines.append(
            f"sqlite3 \"$DB\" \"DELETE FROM documents WHERE vault_path = '{fixture_path}'\" 2>/dev/null || true"
        )

    return lines


def staging_cleanup_lines(filename: str) -> list:
    """Bash lines to remove a staging file + its vault inbox copy + DB record."""
    vault_inbox_path = f"inbox/{filename}"
    return [
        f'rm -f "$STAGING/{filename}"',
        f'rm -f "$VAULT/inbox/{filename}"',
        f"sqlite3 \"$DB\" \"DELETE FROM documents WHERE vault_path = '{vault_inbox_path}'\" 2>/dev/null || true",
    ]


def fixture_bash(entry: dict, fixture_path: str) -> str:
    ext = Path(fixture_path).suffix.lower()
    parent = str(Path(fixture_path).parent)
    eid = entry["id"]

    if ext == ".md":
        content = md_content(entry, fixture_path)
        escaped = content.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        return f'write_md "$VAULT/{fixture_path}" \\\n"{escaped}"'

    if ext == ".pdf":
        return (
            f'mkdir -p "$VAULT/{parent}"\n'
            f'if [ -f "$FIXTURES/sample_text.pdf" ]; then\n'
            f'    cp "$FIXTURES/sample_text.pdf" "$VAULT/{fixture_path}"\n'
            f'else\n'
            f'    echo "⚠ {eid}: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF manually"\n'
            f'fi'
        )

    if ext in (".png", ".jpg", ".jpeg"):
        sample = "sample.png" if ext == ".png" else "sample.jpg"
        return (
            f'mkdir -p "$VAULT/{parent}"\n'
            f'if [ -f "$FIXTURES/{sample}" ]; then\n'
            f'    cp "$FIXTURES/{sample}" "$VAULT/{fixture_path}"\n'
            f'else\n'
            # Minimal 1×1 PNG via ANSI-C quoting — avoids backtick in double-quoted string
            r"    printf $'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB\x60\x82'"
            + f' > "$VAULT/{fixture_path}"\n'
            f'    echo "⚠ {eid}: Created minimal 1x1 PNG fallback. Replace with a real image for testing."\n'
            f'fi'
        )

    if ext == ".docx":
        stem = Path(fixture_path).stem
        title = stem.replace("-", " ").replace("_", " ").title()
        behavior_short = entry["behavior"][:60].replace("'", "\\'")
        return (
            f'mkdir -p "$VAULT/{parent}"\n'
            f'cd "$PROJECT_ROOT" && uv run python -c "\n'
            f'from docx import Document\n'
            f"doc = Document()\n"
            f"doc.add_heading('{title}', 0)\n"
            f"doc.add_paragraph('{behavior_short}.')\n"
            f"doc.add_paragraph('Test fixture for {eid}.')\n"
            f"doc.save('$VAULT/{fixture_path}')\n"
            f'"'
        )

    if ext == ".xlsx":
        sheet = Path(fixture_path).stem.replace("-", " ").replace("_", " ").title()
        return (
            f'mkdir -p "$VAULT/{parent}"\n'
            f'cd "$PROJECT_ROOT" && uv run python -c "\n'
            f'import openpyxl\n'
            f"wb = openpyxl.Workbook()\n"
            f"ws = wb.active\n"
            f"ws.title = '{sheet}'\n"
            f"ws.append(['Column A', 'Column B', 'Column C'])\n"
            f"ws.append(['Row 1 A', 'Row 1 B', 'Row 1 C'])\n"
            f"ws.append(['Row 2 A', 'Row 2 B', 'Row 2 C'])\n"
            f"wb.save('$VAULT/{fixture_path}')\n"
            f'"'
        )

    if ext == ".pptx":
        title = Path(fixture_path).stem.replace("-", " ").replace("_", " ").title()
        return (
            f'mkdir -p "$VAULT/{parent}"\n'
            f'cd "$PROJECT_ROOT" && uv run python -c "\n'
            f'from pptx import Presentation\n'
            f"prs = Presentation()\n"
            f"slide = prs.slides.add_slide(prs.slide_layouts[0])\n"
            f"slide.shapes.title.text = '{title}'\n"
            f"slide.placeholders[1].text = 'Test fixture for {eid}.'\n"
            f"prs.save('$VAULT/{fixture_path}')\n"
            f'"'
        )

    if ext == ".csv":
        return (
            f'mkdir -p "$VAULT/{parent}"\n'
            f"printf 'name,value,notes\\nrow1,100,test\\nrow2,200,fixture\\n' > \"$VAULT/{fixture_path}\""
        )

    if ext == ".html":
        stem = Path(fixture_path).stem.replace("-", " ").title()
        return (
            f'mkdir -p "$VAULT/{parent}"\n'
            f"printf '<html><body><h1>{stem}</h1><p>Test fixture for {eid}.</p></body></html>\\n' > \"$VAULT/{fixture_path}\""
        )

    if ext == ".eml":
        return (
            f'mkdir -p "$VAULT/{parent}"\n'
            f"printf 'From: sender@example.com\\nTo: receiver@example.com\\nSubject: Test Email for {eid}\\n"
            f"Date: Wed, 04 Jun 2026 10:00:00 +0700\\nMIME-Version: 1.0\\nContent-Type: text/plain; charset=UTF-8\\n\\n"
            f"Test email body for {eid}.\\n' > \"$VAULT/{fixture_path}\""
        )

    if ext == ".msg":
        return f'echo "⚠ {eid}: MSG files require Outlook format — place a test .msg file at $VAULT/{fixture_path} manually"'

    return f'echo "⚠ {eid}: Unknown fixture type {ext} for {fixture_path} — create manually"'


def fixture_bash_staging(entry: dict, filename: str) -> str:
    """Bash code to create a staging-area file (placed at $STAGING/<filename>)."""
    ext = Path(filename).suffix.lower()
    eid = entry["id"]

    if ext == ".md":
        content = md_content(entry, filename)
        escaped = content.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        return f'mkdir -p "$STAGING"\nwrite_md "$STAGING/{filename}" \\\n"{escaped}"'

    if ext == ".pdf":
        return (
            f'mkdir -p "$STAGING"\n'
            f'if [ -f "$FIXTURES/sample_text.pdf" ]; then\n'
            f'    cp "$FIXTURES/sample_text.pdf" "$STAGING/{filename}"\n'
            f'else\n'
            f'    echo "⚠ {eid}: No sample PDF at $FIXTURES/sample_text.pdf — place a test PDF at $STAGING/{filename} manually"\n'
            f'fi'
        )

    return f'echo "⚠ {eid}: staging fixture type {ext} not auto-generated — create $STAGING/{filename} manually"'


def render_setup_fn(entry: dict) -> str:
    eid = entry["id"]
    fn = fn_name(eid)
    fixtures = entry.get("fixtures") or []
    staging_fixtures = entry.get("staging_fixtures") or []
    behavior = entry["behavior"]
    tier = entry.get("tier", "")

    lines = [f"# {tier} | {eid}: {behavior}"]

    has_any = bool(fixtures or staging_fixtures)

    if not has_any:
        trigger = str(entry.get("trigger", "")).replace('"', '\\"')
        lines.append(f"{fn}() {{")
        lines.append(
            f'    echo "{eid}: No pre-created fixtures. Trigger: {trigger}"'
        )
        lines.append("}")
        return "\n".join(lines)

    body_lines = []

    # Cleanup preamble: remove old files + targeted DB deletes
    all_cleanup = []
    for fp in fixtures:
        all_cleanup.extend(cleanup_lines(fp))
    for sf in staging_fixtures:
        all_cleanup.extend(staging_cleanup_lines(sf))

    if all_cleanup:
        body_lines.append(indent("# ── cleanup ──"))
        for cl in all_cleanup:
            body_lines.append(indent(cl))

    # Create fixtures
    body_lines.append(indent("# ── create ──"))
    for fp in fixtures:
        code = fixture_bash(entry, fp)
        body_lines.append(indent(code))
    for sf in staging_fixtures:
        code = fixture_bash_staging(entry, sf)
        body_lines.append(indent(code))

    lines.append(f"{fn}() {{")
    lines.extend(body_lines)
    lines.append("}")
    return "\n".join(lines)


def phase_section_comment(phase_key: str, tier: str) -> str:
    titles = {
        "1": "Phase 1 — Capture Pipeline",
        "1.5": "Phase 1.5 — Location Tags + Attachment Layout + Reconcile",
        "pre-2": "Phase Pre-2 — DB Schema + Domain Scalar Cleanup",
        "vault-restructure": "Vault-Restructure — Editable/No-Edit Split",
    }
    title = titles.get(str(phase_key), f"Phase {phase_key}")
    return f"# ─── {title} ({tier}) {'─' * max(0, 76 - len(title) - len(tier) - 10)}─"


def main() -> None:
    import datetime

    with INVENTORY.open() as fh:
        entries = yaml.safe_load(fh)

    today = datetime.date.today().isoformat()

    # Only include non-planned, non-retired entries (active + conflict both need setup)
    renderable = [e for e in entries if e.get("status") not in ("planned", "retired")]

    smoke = [e for e in renderable if e.get("tier") == "smoke"]
    phase = [e for e in renderable if e.get("tier") == "phase"]
    full = [e for e in renderable if e.get("tier") == "full"]

    # Build function definitions, grouped by phase for readability
    from collections import OrderedDict

    def group_by_phase(lst):
        d = OrderedDict()
        for e in lst:
            k = str(e.get("phase", "unknown"))
            d.setdefault(k, []).append(e)
        return d

    sections = []

    for tier_label, tier_list in [("smoke", smoke), ("phase", phase), ("full", full)]:
        for pk, elist in group_by_phase(tier_list).items():
            sections.append(phase_section_comment(pk, tier_label))
            for e in elist:
                sections.append("")
                sections.append(render_setup_fn(e))

    # Tier runner call lists
    def call_lines(elist, indent_spaces=4):
        pad = " " * indent_spaces
        return "\n".join(f"{pad}{fn_name(e['id'])}" for e in elist)

    smoke_calls = call_lines(smoke)
    phase_calls = call_lines(phase)
    full_calls = call_lines(full)

    # Case entries — individual test IDs use per-function cleanup (no full reset)
    case_lines = []
    for tier_label, tier_list in [("smoke", smoke), ("phase", phase), ("full", full)]:
        if tier_list:
            case_lines.append(f"    # ── {tier_label.title()} ──")
        for e in tier_list:
            eid = e["id"]
            case_lines.append(
                f"    {eid})  {fn_name(eid)} ;;"
            )

    output_parts = [
        SCRIPT_HEADER.format(date=today),
        "\n".join(sections),
        SCRIPT_FOOTER_TEMPLATE.format(
            smoke_calls=smoke_calls + "\n",
            phase_calls=phase_calls + "\n",
            full_calls=full_calls + "\n",
            case_entries="\n".join(case_lines),
        ),
    ]

    sys.stdout.write("\n".join(output_parts))


if __name__ == "__main__":
    main()
