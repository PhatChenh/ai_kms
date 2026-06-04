---
created: 2026-06-03
phase: Phase 1 — Capture
skill_step: 3.5 — Success Criteria
status: draft
---

# Phase 1 — Capture: Usability Test

Success criteria for the full Phase 1 capture pipeline, covering:
- `.md` capture (in-place frontmatter stamping, body preservation)
- Non-md capture (sibling `.md` creation + binary relocation)
- Rename gate (SKIP / AUGMENT / FULL_RENAME)
- Tag taxonomy validation
- CLI: `kms capture <file>`, `kms capture --scan`, `kms watch`
- Stability gate (cooldown)
- Scan reconciliation (added, modified, deleted, moved)
- Watcher (auto-capture on drop)

---

## Tier 1 — "You Can Verify" (real vault, no terminal required)

Scenarios testable by opening files in Obsidian after running the command.
All `Then` clauses name exact paths or frontmatter fields.

---

### Scenario 1 — Happy path: `.md` drop captured in place

**Given:** `inbox/meeting-notes.md` exists in the vault with body text (e.g. "Q2 budget
discussion") and no `summary:` frontmatter field.

**When:** `kms capture <vault>/inbox/meeting-notes.md` completes without printing `FAILED:`.

**Then:**
- `inbox/meeting-notes.md` still exists at that exact path (file was NOT moved or deleted)
- The file's frontmatter contains a non-empty `summary:` field
- The file's frontmatter contains a `tags:` list with at least one entry beginning with `type/`
  (e.g. `type/meeting-note`)
- The original body text below the closing `---` is byte-identical to what was there before
  (summary is in frontmatter only — never appended to the body)

---

### Scenario 2 — Happy path: PDF drop creates sibling

**Given:** `inbox/xkjdhfs83.pdf` is a text-bearing PDF dropped in the vault inbox.

**When:** `kms capture <vault>/inbox/xkjdhfs83.pdf` completes without printing `FAILED:`.

**Then:**
- `inbox/xkjdhfs83.pdf` no longer exists at that path (binary was moved)
- A `.pdf` file appears under the appropriate `attachment/` folder
  (e.g. `Projects/<project>/attachment/` or `attachment/`) with a filename matching the
  AI-suggested title (e.g. `Q2 Movies Deck.pdf`)
- A sibling `.md` file appears in `inbox/` (the drop folder) with:
  - A body containing an Obsidian wikilink `![[<attachment-filename>.pdf]]`
  - A `source_file:` frontmatter field whose value is the vault-relative path to the moved binary
  - A non-empty `summary:` frontmatter field

---

### Scenario 3 — Body preservation on `.md` capture

**Given:** `inbox/my-notes.md` has body text:
```
This is my own writing. Do not change this content.
```
No frontmatter block exists yet.

**When:** `kms capture <vault>/inbox/my-notes.md` completes.

**Then:**
- Opening `inbox/my-notes.md` shows "This is my own writing. Do not change this content."
  verbatim in the body below the closing `---` separator
- No part of the AI summary appears in the body (summary is only in the `summary:` frontmatter
  field)

---

### Scenario 4 — Re-capture does not rename an already-named file (Rename Gate Rule 1)

**Given:** `inbox/Q2 Strategy.md` was previously captured (it already has `summary:` in its
frontmatter). The AI would suggest a different title on re-capture.

**When:** `kms capture <vault>/inbox/Q2 Strategy.md` runs again.

**Then:**
- The file is still named `Q2 Strategy.md` — filename unchanged
- The `summary:` field contains freshly updated content (re-capture ran)
- No additional file named `Q2 Strategy Review.md` or similar appears

---

### Scenario 5 — Scan captures a new un-indexed `.md` file

**Given:** `inbox/unread-drop.md` was placed in the vault while `kms watch` was not running.
The file has body text but no `summary:` frontmatter field. It has never been captured.

**When:** `kms capture --scan` completes.

**Then:**
- `inbox/unread-drop.md` now has a non-empty `summary:` frontmatter field
- The file is still at `inbox/unread-drop.md` (not moved; `.md` files are stamped in place)
- Already-captured notes in the vault are unchanged (scan does not re-stamp indexed files)

---

## Tier 2 — "Developer Must Verify" (requires terminal, logs, or DB)

---

### Audit log entries

After every `kms capture <file>` call, query `audit_log` table in the SQLite database:

| Scenario | Expected rows |
|---|---|
| `.md` capture, no tag violations | 2 rows: `stage="metadata", outcome="CAPTURED"` + `stage="rename_gate", outcome IN ("SKIP","AUGMENT","FULL_RENAME")` |
| `.md` capture, with tag violations | 3 rows: `CAPTURED` + `TAG_VIOLATION` + `rename_gate` |
| Non-md capture | 2 rows: `stage="metadata", outcome="CAPTURED"` + `stage="rename_gate", outcome IN (...)` |

All rows for a single `capture_file` call share the same `correlation_id` (UUID string).

---

### Documents table rows

Query `documents` table after each capture:

| Scenario | Expected state |
|---|---|
| `.md` in `inbox/note.md` captured | Row exists: `vault_path = "inbox/note.md"` (NFC-normalized POSIX) |
| `.md` renamed by gate (FULL_RENAME) | Old path row gone; new path row present; no stale row |
| PDF in `inbox/report.pdf` captured | Row exists for sibling `.md` (e.g. `vault_path = "inbox/Q2 Report.md"`), NOT for the binary |
| Same `.md` captured twice | Exactly one row (upsert, not insert; integer `id` unchanged on second pass) |
| `.md` deleted from vault + `--scan` run | Row is removed from `documents` table |
| `.md` moved to new folder + `--scan` run | Row `vault_path` updated to new path; integer `id` unchanged |

---

### Log lines (structlog / stderr)

| Condition | Expected level | Message pattern |
|---|---|---|
| File modified < `cooldown_seconds` ago | `warning` | `"file too recent"` with `age_seconds` + `cooldown_seconds` in context |
| Scan skips cooldown file | `info` | `"scan_capture.skip"` with `path` + `reason` |
| Scan file fails fatally | `warning` | `"scan_capture.failed"` with `path` + `error` |
| Each pipeline stage starts | `debug` | `"stage_start"` with `pipeline="capture"`, `stage=<name>` |
| Each pipeline stage succeeds | `debug` | `"stage_ok"` with `pipeline="capture"`, `stage=<name>` |
| Binary already moved (re-capture guard) | `warning` | Contains `"already moved"` or `"source not found"` |
| Tag violations found | `warning` or audit only | `TAG_VIOLATION` audit row + `violations` list in context |

---

### Return values / Result types

| Call | Expected return |
|---|---|
| `capture_file(path)` — normal stale file | `Success(WriteOutcome)` |
| `capture_file(path)` — file touched < cooldown ago | `Failure(recoverable=True)` — error text contains `"file too recent"` |
| `capture_file(path)` — unsupported extension (no handler) | `Failure(recoverable=False)` — error text contains the extension |
| `HandlerRegistry.resolve(Path("file.xyz"))` | `Failure(recoverable=False, error="no handler for extension '.xyz'")` |
| `scan_capture()` — one file fails fatally | `Success(outcomes)` — failures logged as WARNING, loop continues |
| `PdfHandler.extract(image_only_pdf)` | `Failure(recoverable=False)` — error text contains `"no extractable text"` |

---

### Concurrent-actor non-interference

**Pair: `VaultWatcher` debounce + `capture_file`**

Setup: start `VaultWatcher`; write 5 rapid filesystem events (create → modify × 3 → modify)
on the same path within 1 second.

Expected:
- `on_create` callback fires exactly once, after the debounce window (≥ 3 s default)
- `capture_file` is called exactly once for that path (debounce coalesces events)
- No duplicate `audit_log` rows for the same `correlation_id`

**Pair: `scan_capture` + active user edit**

Setup: write a file 5 seconds ago (within 60 s cooldown). Run `scan_capture`.

Expected:
- `capture_file` for that file returns `Failure(recoverable=True)` with `"file too recent"`
- `scan_capture` logs `scan_capture.skip` and includes the file in no outcome list
- Subsequent `scan_capture` after `cooldown_seconds` have elapsed processes the file normally

---

## Notes for Phase 2 / Classify

- `type/` tags stamped by Phase 1 are validated against `config/tags.yaml` `allowed_types`.
  Phase 2 must preserve these tags when re-classifying — do not strip and re-generate `type/`.
- `domain/` tags are validated against vault `Domain/` folder names at capture time.
  New `Domain/` folders added while `kms watch` is running are NOT visible until restart
  (OQ-C6 — accepted for Phase 1).
- Sibling `.md` files written by Phase 1 non-md capture carry `type: attachment-summary`
  in frontmatter. Phase 2 classify must preserve this value when resolving CLUELESS markers.
