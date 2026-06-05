#!/usr/bin/env python3
"""
Render TESTING_GUIDE.md from behavior_inventory.yaml.

Usage:
    uv run python docs/system_behavior/render_guide.py > docs/system_behavior/TESTING_GUIDE.md

Writes to stdout; caller redirects to file.
"""
import datetime
import re
import sys
from collections import OrderedDict
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INVENTORY = PROJECT_ROOT / "docs" / "system_behavior" / "behavior_inventory.yaml"

TODAY = datetime.date.today().isoformat()

PHASE_TITLES = {
    "1": "Phase 1 — Capture Pipeline",
    "1.5": "Phase 1.5 — Location Tags + Attachment Layout + Reconcile",
    "pre-2": "Phase Pre-2 — DB Schema + Domain Scalar Cleanup",
    "vault-restructure": "Phase Vault-Restructure — Editable/No-Edit Split",
}

SETUP_CMD = "bash docs/system_behavior/setup_test_vault.sh"

HEADER = """\
# AI-KMS Testing Guide
_For non-technical testers. No coding required for Smoke and Phase checks._
_Auto-generated from `behavior_inventory.yaml`. Fill in **Current result** fields after testing. All other content is regenerated — do not edit._
_Last generated: {date}_

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
"""


def escape_placeholders(text: str) -> str:
    """Wrap bare <placeholder> in backticks; skip spans already inside `...`."""
    parts = re.split(r"(`[^`\n]*`)", text)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(part)
        else:
            out.append(re.sub(r"<([^>\s/]+)>", r"`<\1>`", part))
    return "".join(out)


def render_steps(steps) -> str:
    """Render steps field (string or list) → markdown blocks."""
    if isinstance(steps, str):
        s = steps.strip()
        if s.startswith("`") and s.endswith("`") and len(s) > 2:
            return f"```bash\n{s[1:-1]}\n```"
        return escape_placeholders(s)
    parts = []
    for item in steps:
        s = str(item).strip()
        if s.startswith("`") and s.endswith("`") and len(s) > 2:
            parts.append(f"```bash\n{s[1:-1]}\n```")
        else:
            parts.append(escape_placeholders(s))
    return "\n\n".join(parts)


def render_checklist(items: list) -> str:
    return "\n".join(f"- [ ] {escape_placeholders(str(i))}" for i in items)


def last_tested(entry) -> str:
    v = entry.get("last_tested")
    return str(v) if v is not None else "never"


def last_result(entry) -> str:
    v = entry.get("last_result")
    return str(v) if v is not None else "none"


def is_human_reviewed(entry) -> bool:
    v = entry.get("human_reviewed")
    if isinstance(v, bool):
        return v
    return str(v).lower() == "yes"


def setup_block(entry_id: str) -> str:
    return f"**Setup:**\n```bash\n{SETUP_CMD} {entry_id}\n```"


def render_conflict(e: dict) -> str:
    lines = [
        f"### ⚠ CONFLICT: {e['id']} · {escape_placeholders(str(e['behavior']))}",
        f"_Origin: {e['origin']} · Granularity: {e['granularity']}_",
        "",
        "**Design says (origin: design):**",
        "",
        str(e.get("expected_design", "")).strip(),
        "",
        "**Implementation says (origin: implementation):**",
        "",
        str(e.get("expected_implementation", "")).strip(),
        "",
        "**What to do:** Test the system and observe what actually happens. Then tell the developer which expectation is correct — or if neither is right.",
        "",
    ]
    if e.get("fixtures") or e.get("staging_fixtures"):
        lines += [setup_block(e["id"]), ""]
    lines += [
        "**Run:**",
        render_steps(e["steps"]),
        "",
        "**Check:**",
        render_checklist(e["expected"]),
        "",
        f"**Last tested:** {last_tested(e)}",
        f"**Last result:** {last_result(e)}",
        "**Current result:** ___",
        "",
    ]
    if not is_human_reviewed(e):
        lines += ["⚠ AI-authored — not yet human-verified.", ""]
    lines += ["---", ""]
    return "\n".join(lines)


def render_active(e: dict) -> str:
    lines = [
        f"### {e['id']} · {escape_placeholders(str(e['behavior']))}",
        f"_Origin: {e['origin']} · Granularity: {e['granularity']}_",
        "",
    ]
    if e.get("fixtures") or e.get("staging_fixtures"):
        lines += [setup_block(e["id"]), ""]
    lines += [
        "**Run:**",
        render_steps(e["steps"]),
        "",
        "**Check:**",
        render_checklist(e["expected"]),
        "",
        f"**Last tested:** {last_tested(e)}",
        f"**Last result:** {last_result(e)}",
        "**Current result:** ___",
        "",
    ]
    if not is_human_reviewed(e):
        lines += ["⚠ AI-authored — not yet human-verified.", ""]
    lines += ["---", ""]
    return "\n".join(lines)


def phase_key(entry) -> str:
    return str(entry.get("phase", "unknown"))


def phase_title(key: str) -> str:
    return PHASE_TITLES.get(key, f"Phase {key}")


def main() -> None:
    with INVENTORY.open() as fh:
        entries = yaml.safe_load(fh)

    conflicts = [e for e in entries if e.get("status") == "conflict"]
    active = [e for e in entries if e.get("status") == "active"]

    smoke = [e for e in active if e.get("tier") == "smoke"]
    phase_entries = [e for e in active if e.get("tier") == "phase"]
    full = [e for e in active if e.get("tier") == "full"]

    # Group phase entries by phase key, preserving insertion order
    by_phase: OrderedDict = OrderedDict()
    for e in phase_entries:
        pk = phase_key(e)
        by_phase.setdefault(pk, []).append(e)

    # Group full entries by phase key, preserving insertion order
    full_by_phase: OrderedDict = OrderedDict()
    for e in full:
        pk = phase_key(e)
        full_by_phase.setdefault(pk, []).append(e)

    out = [HEADER.format(date=TODAY)]

    # Conflicts section
    if conflicts:
        out.append("## ⚠ Priority: Resolve These First\n")
        for e in conflicts:
            out.append(render_conflict(e))

    # Smoke section
    out.append("## SMOKE — Must Always Pass (~5 min)\n")
    out.append("---\n")
    for e in smoke:
        out.append(render_active(e))

    # Phase section
    out.append("## PHASE — Run When Phase Code Changes\n")
    out.append("---\n")
    for pk, entries_in_phase in by_phase.items():
        out.append(f"### {phase_title(pk)}\n")
        out.append("---\n")
        for e in entries_in_phase:
            out.append(render_active(e))

    # Full section
    out.append("## FULL — Developer Only (Terminal + DB Access)\n")
    out.append("---\n")
    for pk, entries_in_phase in full_by_phase.items():
        out.append(f"### {phase_title(pk)}\n")
        out.append("---\n")
        for e in entries_in_phase:
            out.append(render_active(e))

    sys.stdout.write("\n".join(out))


if __name__ == "__main__":
    main()
