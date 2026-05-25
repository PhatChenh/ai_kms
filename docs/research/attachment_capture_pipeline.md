# Research: attachment_capture_pipeline
_Last updated: 2026-05-23_

> **Supersedes** (for non-md branch): [docs/research/capture_pipeline.md#Non-md branch](capture_pipeline.md) — that section documents the OLD layout (sibling next to source, global attachment_path, bare wikilink). This document covers only the Brief #2 delta. The md-branch and stages 1-4 are unchanged and remain accurate in the old file.

> **Depends on**: [docs/research/revise_attachment_layout.md](revise_attachment_layout.md) (Brief #1) — all four vault path helpers (`project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries`) and the `NoteMetadata.attachment_path` field are already built and shipped in Phase 1.5.

---

## Overview

`pipelines/capture.py` runs a 5-stage pipeline (`extract → enrich_urls → summarize → metadata → store`). Stage 5 (`store`) branches on `raw.is_md`: the md-branch is complete and correct; the non-md branch (`_store_nonmd`, l. 434–496) uses the old global-attachment layout. Three `# COUPLING:` markers at `capture.py:456–458`, `capture.py:628–630`, and `cli/main.py:127–128` flag exactly where Brief #2 must complete the wiring. This research documents what those markers cover, what the fix looks like, and surfaces the design decisions a planner must resolve.

---

## Key Components

| File | Role |
|---|---|
| [pipelines/capture.py](../../pipelines/capture.py) | Core file. `_store_nonmd` (l. 434–496) + `scan_capture` non-md loop (l. 626–648). All 3 COUPLING markers. |
| [vault/paths.py](../../vault/paths.py) | `project_attachment(name)`, `project_summaries(name)`, `domain_attachment(name)`, `domain_summaries(name)` — already built; Brief #2 must call them. |
| [vault/frontmatter.py](../../vault/frontmatter.py) | `NoteMetadata.attachment_path: str | None` — already in `_KNOWN_KEYS` and as a typed field (Phase 1.5). |
| [vault/indexer.py](../../vault/indexer.py) | `scan_non_md_drops(root, attachment_path: Path)` — single-path skip at l. 115 breaks with per-project layout (TD-024). |
| [core/audit.py](../../core/audit.py) | `write(decision, pipeline, stage, outcome, db_path)` — thin facade to `storage/audit_log.py::append`. |
| [storage/audit_log.py](../../storage/audit_log.py) | `AuditEntry` frozen dataclass + `append()`. Outcome column is TEXT, no CHECK constraint — any string valid. |
| [core/confidence.py](../../core/confidence.py) | `AIDecision(action, confidence, reasoning, source_ids)` + `route(decision, thresholds) → RoutingOutcome`. |
| [core/pipeline.py](../../core/pipeline.py) | `run_pipeline(name, stages, initial_input, context)` — sequential; `PipelineContext` carries `config + correlation_id + db_path + taxonomy`. Adding a stage = add one element to the `stages` list in `capture_file`. |
| [handlers/base.py](../../handlers/base.py) | `RawContent(text, source_path, is_md)` frozen dataclass. `source_path` carries the drop location through all stages. |
| [config/thresholds.yaml](../../config/thresholds.yaml) | `global.auto=0.85`, `global.suggest=0.60`. `pipelines: {}` — per-pipeline overrides already supported by config; no active entries yet. |
| [prompts/extract_metadata.yaml](../../prompts/extract_metadata.yaml) | Extracts `title`, `tags` (incl. `domain/<D>` and `type/<T>` prefixed). Does NOT extract project name. |
| [prompts/summarize.yaml](../../prompts/summarize.yaml) | Produces a 2-4 sentence plain-text summary. |
| [cli/main.py](../../cli/main.py) | `capture` command + `watch` command. 3rd COUPLING marker at l. 127–128 (watcher `attachment_path` arg). |

---

## How It Works — Current Non-md Branch (BROKEN for new layout)

```
capture_file(path)
  → run_pipeline([extract, enrich_urls, summarize, metadata, store], path)
  → store(MetadataResult) → _store_nonmd(mr, note_meta, ctx)

_store_nonmd (l. 434–496):
  1. decide_rename() → sanitized_stem
  2. attachment_dir = ctx.config.vault.root / ctx.config.vault.attachment_dir   # ← COUPLING (l. 457)
     # This builds the GLOBAL Vault/attachment/ path that no longer exists
  3. attachment_dst = attachment_dir / f"{sanitized_stem}{suffix}"
  4. collision loop (max 100)
  5. move_attachment(src, attachment_dst) → binary to global attachment/
  6. sibling = src.parent / f"{sanitized_stem}.md"   # ← next to source (inbox/ or wherever)
  7. sibling_body = f"![[{attachment_dst.name}]]"     # ← bare wikilink
  8. write_note(sibling, sibling_body, note_meta)     # ← no attachment_path in note_meta
  9. documents.upsert(sibling_outcome)
```

**`scan_capture` non-md loop (l. 626–648):**
```python
# COUPLING: uses global attachment_dir until Brief #2 wires per-project skip logic  (l. 628)
_attachment_path = CONFIG.main.vault.root / CONFIG.main.vault.attachment_dir
non_md_paths = scan_non_md_drops(_root, _attachment_path)   # passes single global path
```

`scan_non_md_drops` at l. 115 checks `if attachment_path in file_path.parents: continue` — the single-path guard. With per-project layout, binaries already in `Projects/<A>/attachment/` are NOT skipped; they re-trigger capture on every scan.

---

## Entry Points Reaching the Non-md Branch (Q1)

All three paths funnel into `capture_file(path)` → `_store_nonmd`:

| Entry point | How |
|---|---|
| `kms capture <file>` | `cli/main.py:68` calls `asyncio.run(capture_file(Path(file)))` directly |
| `kms capture --scan` | `scan_capture()` → `scan_non_md_drops()` loop (l. 626–648) → `capture_file(path)` per binary |
| `kms watch` | Watcher `on_create(path)` callback → `capture_file(path, _make_ctx())`. Watcher currently uses global `attachment_path` (COUPLING cli/main.py:127–128 — Brief #3 scope TD-023). Non-md drops anywhere fire `on_create` including inside new per-project `attachment/` folders until Brief #3 fixes the watcher skip. |

---

## Routing Decision — Where It Belongs (Q2)

### Current pipeline stage list (capture_file l. 553–558)
```python
await run_pipeline(
    "capture",
    [extract, enrich_urls, summarize, metadata, store],
    path,
    context=context,
)
```

### Option analysis

**Option (a) — New `route` stage after `metadata`** (Recommended)

```
extract → enrich_urls → summarize → metadata → route → store
```

- `route` receives `MetadataResult` (has `ai_domain` and `raw.source_path`)
- For .md drops: passthrough no-op (`raw.is_md=True`, return `MetadataResult` unchanged)
- For non-md drops: compute `(target_type, target_name, needs_move)` from path + `ai_domain`
- Returns new `RoutedMetadataResult(MetadataResult + routing fields)`
- `_store_nonmd` reads the routing fields and calls `project_attachment(name)` / `domain_attachment(name)` directly — no path computation needed at write time
- Audit for routing decision sits in `route` stage (not in `store`)

Why this fits the Pipeline Pattern: routing IS a distinct stage — it makes an AI-assisted decision with confidence, writes an audit row, and passes structured output forward. Bundling it into `store` would make `store` not-a-pure-writer.

**Why Option (b) inline in `_store_nonmd` is wrong**: `store` should be a writer, not a classifier. Embedding routing logic there bundles two concerns.

**Why Option (c) shared with Phase 2 classify** is worth flagging: the routing question ("which project does this binary belong to?") is semantically identical to classify's "which project does this note belong to?" The routing logic in Phase 1 will be a subset of what classify does in Phase 2. Recommend: implement Phase 1 routing self-contained but extract it to a shared helper (`core/routing.py`) so Phase 2 can build on it without re-implementing.

### `route` stage logic — trivial inference (no AI cost)

```python
async def route(mr: MetadataResult, ctx: PipelineContext) -> Result[RoutedMetadataResult]:
    if mr.raw.is_md:
        return Success(RoutedMetadataResult(mr=mr, target=None))  # md branch skips routing

    src = mr.raw.source_path
    vault_cfg = ctx.config.vault
    projects_path = vault_cfg.projects_path
    domain_path = vault_cfg.domain_path

    # Case 1: drop inside Projects/<A>/ tree
    if projects_path in src.parents:
        rel = src.relative_to(projects_path)
        project_name = rel.parts[0]
        # Sub-case: already inside Projects/<A>/attachment/ → no move needed
        in_attachment = len(rel.parts) > 1 and rel.parts[1] == vault_cfg.attachment_dir
        return Success(RoutedMetadataResult(
            mr=mr,
            target=RouteTarget(type="project", name=project_name, needs_move=not in_attachment),
            confidence=1.0,
        ))

    # Case 2: drop inside Domain/<D>/ tree
    if domain_path in src.parents:
        rel = src.relative_to(domain_path)
        domain_name = rel.parts[0]
        in_attachment = len(rel.parts) > 1 and rel.parts[1] == vault_cfg.attachment_dir
        return Success(RoutedMetadataResult(
            mr=mr,
            target=RouteTarget(type="domain", name=domain_name, needs_move=not in_attachment),
            confidence=1.0,
        ))

    # Case 3: domain inferred from metadata stage (inbox drop with domain tag)
    if mr.ai_domain:
        # Route to Domain/<ai_domain>/attachment/ — moderate confidence
        return Success(RoutedMetadataResult(
            mr=mr,
            target=RouteTarget(type="domain", name=mr.ai_domain, needs_move=True),
            confidence=0.75,  # read from config, not hardcoded — see Constraints
        ))

    # Case 4: ambiguous — no path context, no domain tag → CLUELESS, leave in place
    return Success(RoutedMetadataResult(
        mr=mr,
        target=None,  # _store_nonmd handles: writes sibling next to src, binary stays put
        confidence=0.4,
    ))
```

**Note**: confidence 0.75 and 0.4 above must NOT be hardcoded — they must come from `config/thresholds.yaml` per pipeline hook rule. The hook blocks float literals in `if/elif` inside `pipelines/`. The `route` function must read from `ctx.config.thresholds.for_pipeline("capture_route")` (or similar). The `route` logic uses `confidence.route()` to map to AUTO/SUGGEST/CLUELESS.

---

## Trivial vs AI Routing at Stage Entry (Q3)

| Source path | Confidence | Action |
|---|---|---|
| `Projects/<A>/attachment/<file>` | 1.0 | No move. Sibling → `.summaries/`. ROUTED audit. |
| `Projects/<A>/<file>` (loose) | 1.0 | Move binary to `Projects/<A>/attachment/`. Sibling → `.summaries/`. ROUTED audit. |
| `Domain/<D>/attachment/<file>` | 1.0 | No move. Sibling → `.summaries/`. ROUTED audit. |
| `Domain/<D>/<file>` (loose) | 1.0 | Move to `Domain/<D>/attachment/`. Sibling → `.summaries/`. |
| `inbox/<file>` with `ai_domain` set | 0.75 (config) | Move to `Domain/<ai_domain>/attachment/`. SUGGEST if below auto threshold. |
| `inbox/<file>` no domain inferred | 0.4 (config) | Leave binary in place. CLUELESS audit. No sibling yet. |

---

## Sibling Body Shape — Old vs Target (Q4)

### Current (broken)
```python
sibling_body = f"![[{attachment_dst.name}]]"  # bare wikilink — tells AI nothing
```

### Target (from discussion doc, l. 248–268)

```markdown
---
type: attachment-summary
attachment_path: Projects/Strategy/attachment/report.pdf
summary: "One-sentence teaser: Q1 strategy review, 12 pages, covers OKRs and risk register."
project: Strategy
tags:
- type/attachment-summary
- domain/strategy
---

[[Projects/Strategy/attachment/report.pdf]]

## What this file is
[2–3 sentence description: source, date, format, overall purpose]

## Key content
[Structured outline or section map — headings, key figures, tables. For large files: note which sections are most relevant to which query types.]

## Key facts / findings
[Bullet list: numbers, decisions, names, dates]
```

### Implementation approach

The short `summary` (frontmatter field) comes from the existing `summarize` stage (already in `mr.summary`). The body sections ("What this file is", "Key content", "Key facts") require a second LLM call — the existing `summarize` prompt produces a 2-4 sentence narrative, not a structured outline.

**Plan**: add `prompts/summarize_attachment.yaml` — called inside `_store_nonmd` after move_attachment succeeds but before write_note:

```yaml
name: summarize_attachment
system: |
  You are a knowledge management assistant. Produce a structured summary of the
  provided file content for an AI knowledge base. Return plain markdown (no
  frontmatter). Use exactly these three sections:

  ## What this file is
  2–3 sentences: file type, apparent source/date, overall purpose.

  ## Key content
  A structured outline or section map. For documents with clear sections, list
  them. For spreadsheets, list sheets and key columns. Note which sections are
  most relevant to which question types.

  ## Key facts / findings
  Bullet list of extractable facts: numbers, decisions, names, dates, thresholds.
  Be specific. Avoid vague summaries — the AI must be able to answer "does this
  file mention X?" from this list alone.
user: |
  File type: {{ file_type }}
  Short summary: {{ short_summary }}

  Content:
  {{ text }}
variables: [file_type, short_summary, text]
```

`file_type` is derived from `mr.raw.source_path.suffix.lower()` (e.g. `".pdf"`, `".docx"`).

**Wikilink path shape (Q-006 / OQ-AL5)**: use full vault-relative path `[[Projects/A/attachment/report.pdf]]` — matches discussion doc example and is unambiguous when two projects share a basename. Relative `[[../report.pdf]]` is technically valid in Obsidian but requires Obsidian to resolve relative-path wikilinks, which is not guaranteed in all Obsidian versions. Full path is safer.

---

## `NoteMetadata.attachment_path` — Frontmatter Field Status (Q5)

**Already built (Phase 1.5):**
- `attachment_path: str | None = None` is a typed `Field` on `NoteMetadata` (`vault/frontmatter.py:62`)
- `"attachment_path"` is in `_KNOWN_KEYS` (`frontmatter.py:40`)
- `_coerce_bool_to_str` validator already covers it (`frontmatter.py:66`)

Brief #2 only needs to **set the value**:
```python
sibling_meta = NoteMetadata(
    summary=mr.summary,
    type="attachment-summary",
    domain=mr.ai_domain,
    tags=sibling_tags,
    confidence=mr.decision.confidence,
    attachment_path=to_vault_path(attachment_dst),  # vault-relative, NFC-normalized
)
```

`to_vault_path(attachment_dst)` (`vault/paths.py:41–54`) applies NFC normalization — correct.

---

## `source_file` vs `attachment_path` — Overlap Analysis (Q10)

`NoteMetadata.source_file: str | None` — present in current code but **not set** in `_store_nonmd`. The old research doc planned to set it as `str(raw.source_path.name)`. The actual implemented code (`_store_nonmd` l. 329–335) does not set it.

`attachment_path` (Phase 1.5) is the correct dedicated field for the binary pointer (DECISION-022). `source_file` semantics in other contexts ("the original source file before move") do not conflict because:
1. For .md captures: `source_file` could carry the original web URL or email source — not the vault path of another file
2. For non-md captures: use `attachment_path` exclusively. Don't set `source_file` on sibling notes

**Decision**: `source_file` is reserved for .md-capture source tracking (external URL, email ID, etc.). `attachment_path` is the binary pointer for all sibling notes. No field overlap if this convention is followed consistently.

---

## Audit Entries (Q6)

### Current audit types in capture pipeline
| outcome | when |
|---|---|
| `"CAPTURED"` | metadata stage success (l. 226) |
| `"TAG_VIOLATION"` | validate_tags found unknown tags (l. 253) |
| `"RENAME_SKIPPED"`, `"RENAME_APPLIED"`, `"RENAME_COLLISION"` | rename gate (l. 292–296) |

### New type for routing decision

Use `"ROUTED"` for the routing stage audit row. Reasoning:
- `"CLASSIFIED"` collides with Phase 2's classify pipeline (different pipeline, different semantics)
- `"ROUTED"` is distinct and self-documenting
- Phase 8 briefing reads `outcome` to describe what happened — `"ROUTED"` is clear

```python
match audit.write(
    routing_decision,
    pipeline="capture",
    stage="route",
    outcome="ROUTED",
    db_path=ctx.db_path,
):
```

For CLUELESS routing (confidence < suggest threshold):
```python
outcome="CLUELESS"  # already a meaningful term in the system; reuse
```

---

## Confidence Gate for Routing (Q7)

### Existing infrastructure
`config/thresholds.yaml` has `global.auto=0.85`, `global.suggest=0.60`. The `pipelines: {}` key supports per-pipeline overrides via `CONFIG.thresholds.for_pipeline("capture_route")` (falls back to global if no override).

### Routing confidence assignments
| Case | Confidence | Gate result |
|---|---|---|
| Path-inferred (drop in project/domain tree) | 1.0 | AUTO — proceed |
| Domain tag-inferred (inbox drop with `ai_domain`) | Configurable (suggest thresholds.yaml) | AUTO if high, SUGGEST if mid |
| No inference possible | Configurable | CLUELESS |

**Hook rule**: domain-inferred confidence must come from config, not hardcoded in `pipelines/`. Option: add to `thresholds.yaml`:
```yaml
pipelines:
  capture_route:
    domain_inferred: 0.75   # confidence when routing via ai_domain from inbox
    no_context: 0.35        # confidence when no project/domain context available
```

**Critical constraint**: `ctx.config` is `MainConfig` (not the outer `Config`). `thresholds` lives on `Config.thresholds` — not accessible via `ctx.config`. The `route` stage must lazy-import `CONFIG` to access thresholds, exactly like `enrich_urls` (l. 127) and `scan_capture` (l. 583) already do:
```python
from core.config import CONFIG  # lazy — avoids module-scope vault validation
band = CONFIG.thresholds.for_pipeline("capture_route")
```
This is the established pattern for accessing CONFIG from inside pipeline stages.

---

## Low-Confidence Non-md in Inbox (Q8)

When routing confidence < `suggest` threshold (CLUELESS case): **leave binary in original location, no sibling written, no move attempted**. Log `"CLUELESS"` audit entry. Binary stays in inbox on next scan — `scan_non_md_drops` will pick it up again.

This means CLUELESS binaries re-trigger capture on every scan. To prevent re-evaluation, two options:
1. Write a minimal sibling at `inbox/.summaries/<stem>.md` with `status: pending-routing` frontmatter — signals the capture pipeline to skip on re-scan (check `source_file == None and status == "pending-routing"` at entry)
2. Accept re-evaluation (idempotent: same CLUELESS decision, same no-op, another audit row per scan)

Option 2 is simpler for Phase 1. Option 1 (pending marker) avoids audit log spam and is cleaner. Decision for plan stage.

---

## `scan_non_md_drops` Fix (TD-024) (Q — from Brief scope)

### Current signature and logic
```python
def scan_non_md_drops(root: Path, attachment_path: Path) -> list[Path]:
    ...
    if attachment_path in file_path.parents:  # l. 115 — single global path
        continue
```

### Problem
With per-project layout there is no single `attachment_path`. Binaries already in `Projects/<A>/attachment/` are not filtered → every scan re-queues them for capture.

### Fix — generalize skip to "any managed attachment/ folder"

Change signature and guard:

```python
def scan_non_md_drops(root: Path, vault_config=None) -> list[Path]:
    ...
    if vault_config is not None and _is_in_managed_attachment(file_path, vault_config):
        continue
```

Helper (belongs in `vault/indexer.py`):
```python
def _is_in_managed_attachment(file_path: Path, vault_cfg) -> bool:
    """Return True if file_path lives inside a per-project or per-domain attachment/ folder."""
    attachment_dir = vault_cfg.attachment_dir
    for parent in file_path.parents:
        if parent.name == attachment_dir:
            ggp = parent.parent.parent  # parent = attachment/, parent.parent = <project/domain>, ggp = Projects/ or Domain/
            if ggp == vault_cfg.projects_path or ggp == vault_cfg.domain_path:
                return True
    return False
```

Callers change:
- `scan_capture` (l. 629–630): `scan_non_md_drops(_root, ctx.config.vault)` instead of `scan_non_md_drops(_root, _attachment_path)`

Old single-path parameter removed; existing test for `scan_non_md_drops` must be updated.

---

## Sibling Tags — `attachment-summary` Type

The sibling note needs `type/attachment-summary` in its tags. Current `tags.yaml` taxonomy for `type/`:
- `meeting-note, email, report, article, reflection, task-list, transcript, capture`

`attachment-summary` not present. Required addition to `config/tags.yaml`:
```yaml
type:
  - ...existing...
  - attachment-summary
```

Siblings bypass `validate_tags` (they are written in `store`, not via the `metadata` stage). But adding to taxonomy keeps Phase 2/3 searches consistent ("find all attachment-summary notes").

---

## Edge Cases & Silent Failure Modes

### 1. Binary moved to `attachment/` but sibling write fails (already flagged as TD-C6)
`_store_nonmd` comment (l. 483–486): binary is at `attachment_dst`, no sibling note. On next `scan_non_md_drops`, the binary is inside `attachment/` — it will be skipped by the new `_is_in_managed_attachment` check. **Orphaned binary is invisible to future scans**. Fix: write sibling FIRST, move binary second. Or: add a reconciliation pass that checks `attachment/*.pdf` without a matching `.summaries/*.md` (Brief #3 scope).

### 2. Same basename in two projects (`Projects/A/attachment/report.pdf` + `Projects/B/attachment/report.pdf`)
`documents.vault_path` UNIQUE still holds — full paths differ. But if the wikilink in sibling uses only the basename `[[report.pdf]]`, Obsidian's vault-wide search would be ambiguous. Fix: always use full vault-relative path in wikilink (see Q4 analysis above).

### 3. No-move case: binary already in `Projects/<A>/attachment/`
`move_attachment` must not be called (would attempt to move a file onto itself or to a collision). `_store_nonmd` must check `needs_move` flag from the routing stage before calling `move_attachment`.

### 4. Domain tag from metadata not in valid domains
`ai_domain` comes from `validate_tags` — it only passes tags matching `domain/<valid_domain>`. So if `ai_domain` is non-None, it is already validated against `config/tags.yaml`. Domain-inferred routing is safe.

### 5. CLUELESS binary on repeated scans (re-audit spam)
Covered in Q8. Plan stage must decide: pending-marker sibling or accept re-evaluation.

### 6. Watcher fires `on_create` for sibling `.md` after write
Sibling is written to `.summaries/report.md` by the pipeline. Watcher sees a new `.md` file created, fires `on_create`. `on_create` in `cli/main.py:154–159` does a DB check: if vault_path already in documents (it was just upserted), `on_create` returns early. Safe — no double-capture.

### 7. Watcher fires `on_create` for binary dropped into `Projects/<A>/attachment/`
Currently: watcher uses global `attachment_path` to skip (cli/main.py:127–128 COUPLING). This skip will MISS binaries in per-project attachment folders until Brief #3 fixes the watcher. Until then, watcher fires `on_create(path)` for new PDFs in any per-project `attachment/` folder, and `capture_file` is called. With the new routing logic, `route` stage infers `needs_move=False` (already in `attachment/`) and sibling is created. This is CORRECT behavior — the route stage handles the no-move case gracefully, so the watcher bug has no practical impact for attachment/ drops until Brief #3.

---

## Dependencies & Coupling

```
pipelines/capture.py
├── handlers/ (registry, RawContent) — destination-agnostic; no layout coupling
├── vault/paths.py — project_attachment, project_summaries, domain_attachment, domain_summaries
│                    to_vault_path (NFC-normalize binary path for frontmatter)
├── vault/frontmatter.py — NoteMetadata.attachment_path field (already typed, in _KNOWN_KEYS)
├── vault/writer.py — write_note, move_attachment — signature unchanged; caller computes paths
├── vault/indexer.py — scan_non_md_drops — signature MUST change (TD-024)
├── core/audit.py → storage/audit_log.py — new "ROUTED" / "CLUELESS" outcomes
├── core/confidence.py — AIDecision + route() for routing gate
├── core/pipeline.py — run_pipeline; adding route stage = one list element in capture_file
├── prompts/ — new summarize_attachment.yaml needed
└── config/thresholds.yaml — new capture_route block needed
```

**No changes needed in**: `handlers/` (destination-agnostic), `vault/reader.py`, `storage/schema.sql`, `storage/documents.py`.

---

## Extension Points

| Component | Extension mechanism | Status |
|---|---|---|
| `run_pipeline` stage list | Append `route` to stages list in `capture_file` | ✅ Already data-driven (list) |
| Routing confidence thresholds | Add `capture_route` key to `thresholds.yaml` | ✅ Already supported by config |
| Routing cases | Add new `RouteTarget` cases to `route` stage | OK — function, not registry |
| Sibling body prompt | New YAML in `prompts/` | ✅ Prompt-as-config pattern |
| Managed-attachment detection | `_is_in_managed_attachment` helper in indexer | Config-driven (reads attachment_dir) |
| `audit_log` outcome strings | Free-form TEXT column, no constraint | ✅ Any string valid |

---

## Open Questions

| ID | Question | What I checked before marking open |
|---|---|---|
| OQ-AC1 | New `route` stage vs shared routing lib with Phase 2 classify. Brief recommends isolated stage in Phase 1; shared `core/routing.py` for Phase 2 reuse. Should routing be a pipeline stage or a helper called from `store`? | Checked pipeline pattern in `core/pipeline.py` — new stage is a list append. Checked `_store_nonmd` coupling — bundling routing there makes `store` impure. Stage is cleaner. |
| OQ-AC2 | Audit type name for routing: `"ROUTED"` vs another string. `"CLASSIFIED"` reserved for Phase 2. `"ROUTED"` or `"ROUTE"` or `"ATTACHMENT_ROUTED"`. | Checked `audit_log` schema — TEXT column, no constraint. Checked all existing outcome strings: `"CAPTURED"`, `"TAG_VIOLATION"`, `"RENAME_SKIPPED"`, `"RENAME_APPLIED"`, `"RENAME_COLLISION"`. `"ROUTED"` is free. |
| OQ-AC3 | CLUELESS binary state: pending-marker sibling at `inbox/.summaries/<stem>.md` OR accept re-evaluation on every scan. Trade-off: audit log spam vs implementation complexity. | Checked `scan_capture` flow — CLUELESS binary IS re-queued each scan (l. 633–648). Checked that `on_create` DB-check prevents double-capture for .md files but not for binaries (binaries don't get upserted until sibling is written). |
| OQ-AC4 | Sibling body: one LLM call (extend summarize to output both short + extended) vs two LLM calls (summarize produces short; summarize_attachment produces body). Two calls is cleaner but adds latency. | Checked `summarize.yaml` — outputs plain 2-4 sentence text, no JSON structure. Extending it to output structured markdown would complicate parsing. Two separate prompts is cleaner. |
| OQ-AC5 (new) | `attachment-summary` type tag: add to `config/tags.yaml` taxonomy so Phase 2/3 can filter by type, or leave unregistered (sibling bypasses validate_tags anyway). | Checked `core/tags.py` validate_tags is NOT called on manually-constructed sibling metadata. Sibling tags bypass validation. Adding to taxonomy is a one-liner — low cost, high discoverability. |
| OQ-AC6 (new) | Sibling ordering vs binary move ordering. Current code: move binary first, write sibling second. If sibling write fails, binary is orphaned in `attachment/` with no `documents` row and no way for `scan_non_md_drops` to find it (it's inside attachment/ now). Fix: write sibling first, move binary second. But then if binary move fails, sibling exists with a broken `attachment_path` pointer. Trade-off. | Confirmed by reading `_store_nonmd` l. 471–494. Both orderings have a failure mode. Plan stage must decide. |
| OQ-Q006 (from STATE.md) | Wikilink path shape: full vault-relative `[[Projects/A/attachment/report.pdf]]` vs relative `[[../report.pdf]]`. Full path is recommended (discussion doc example + unambiguous). Must be verified on real vault before shipping. | Read Obsidian docs intent; full path is unambiguous. Cannot verify live. |

---

## Reference Project Patterns

The reference project (`knowledge-base-server`) has no equivalent non-md capture concept — it handles only structured documents (no binary attachments). No patterns applicable.

---

## Technical Debt Spotted

| ID | What | Why deferred |
|---|---|---|
| TD-C5 (Brief #2) | `scan_non_md_drops` signature change (`attachment_path: Path` → `vault_config`) breaks existing test `tests/test_vault/test_indexer.py` tests that pass a single path. | Update in implementation; brief #2 scope. |
| TD-C6 (pre-existing) | Binary in `attachment/` with no sibling is invisible to all scans after Brief #2 ships. Orphan reconciliation (walk `attachment/`, compare against `.summaries/`) deferred to Brief #3. | Brief #3 sync mechanics scope. |
| TD-020 (from STATE.md) | `docs/research/capture_pipeline.md` § "Non-md branch" documents OLD layout. | Post-Brief #2 documentation pass. Annotate with pointer to this file. |
| TD-021 (from STATE.md) | `docs/roadmap.md` Phase 1 l. 53–66 describes OLD attachment layout. | Post-Brief #2 documentation pass. |
| TD-022 (from STATE.md) | 3 COUPLING markers: `capture.py:456`, `capture.py:628`, `cli/main.py:127`. | This research clears Brief #2's two markers; cli/main.py:127 is Brief #3 (watcher skip). |
| TD-C7 (new) | `tags.yaml` needs `attachment-summary` type tag added. Low risk, low effort, clean taxonomy. | Brief #2 implementation. |
| TD-C8 (new) | `prompts/summarize_attachment.yaml` does not exist — empty sibling body until prompt is built and tested on real attachments. | Brief #2 implementation. |

---

## Downstream Phase Impact

- **Brief #3 (`attachment_sync_and_archive`)**: consumes (a) the `vault_path = sibling path` convention (DECISION-022), (b) the `attachment_path` frontmatter pointer for binary rename tracking, (c) the `_is_in_managed_attachment` helper from TD-024 fix, (d) OQ-AC3 resolution (CLUELESS pending marker).
- **Phase 2 (Classify)**: routing from `inbox/` to a specific project (not just domain) is Phase 2's core task. Brief #2's domain-inferred routing is a subset — flag the shared logic opportunity (`core/routing.py`).
- **Phase 3 (Retrieval)**: embeddings computed from sibling body — rich extended body produces richer, more discriminative vectors than a bare wikilink. Brief #2's `summarize_attachment` output directly improves Phase 3 embedding quality.
- **Phase 4 (MCP MVP)**: `kms_search` returns `documents` rows; `vault_path` resolves to sibling. To open the binary, consumer reads `metadata.attachment_path`. Brief #2 must populate this field correctly; Phase 4 depends on it.

---

## Self-Review Notes

- **Unsupported claims**: every COUPLING marker referenced by line number from the actual file. Routing logic grounded in the actual path helpers already in `vault/paths.py`. No claims made about behavior without reading the code.
- **Gaps disguised as confidence**: `summarize_attachment` prompt content is a sketch — exact LLM behavior must be validated on real files. Marked as OQ-AC4 scope for plan stage.
- **Missing downstream impact**: section added.
- **Contradictions with existing research**: `capture_pipeline.md` § "Non-md branch" is superseded by this document for the Brief #2 scope. Existing file not edited; annotation "→ see attachment_capture_pipeline.md" to be added post-Brief #2 ship (TD-020).
- **OQ-Q006 (wikilink path shape)**: full vault-relative path recommended based on discussion doc and unambiguity argument. Must be tested on real vault before shipping.
