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
# IMPORTANT: This script operates on the TEST vault, never the real vault.
# Last generated: {date}

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VAULT="$(cd "$PROJECT_ROOT" && uv run python -c "import yaml; print(yaml.safe_load(open('src/config/config.yaml'))['testing']['vault_path'])")"
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
echo "Vault: $VAULT"
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
            # Minimal 1×1 PNG via raw bytes — no real image content
            r'    printf "\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"'
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

    # Unknown extension — warn
    return f'echo "⚠ {eid}: Unknown fixture type {ext} for {fixture_path} — create manually"'


def render_setup_fn(entry: dict) -> str:
    eid = entry["id"]
    fn = fn_name(eid)
    fixtures = entry.get("fixtures") or []
    behavior = entry["behavior"]
    tier = entry.get("tier", "")

    lines = [f"# {tier} | {eid}: {behavior}"]

    if not fixtures:
        trigger = str(entry.get("trigger", "")).replace('"', '\\"')
        lines.append(f"{fn}() {{")
        lines.append(
            f'    echo "{eid}: No pre-created fixtures. Trigger: {trigger}"'
        )
        lines.append("}")
        return "\n".join(lines)

    body_lines = []
    for fp in fixtures:
        code = fixture_bash(entry, fp)
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

    # Case entries
    case_lines = []
    for tier_label, tier_list in [("smoke", smoke), ("phase", phase), ("full", full)]:
        if tier_list:
            case_lines.append(f"    # ── {tier_label.title()} ──")
        for e in tier_list:
            eid = e["id"]
            case_lines.append(
                f"    {eid})  reset_db; clean_vault; {fn_name(eid)} ;;"
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
