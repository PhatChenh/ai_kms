# Component Diagram — Phase 1: Capture (incl. Phase 1.5 attachment layout)
Scope: Every module built in Phase 1 and the Phase 1.5 attachment-layout revision.
Covers how dropped files travel from inbox to vault, and how the watcher and
reconcile pipeline maintain consistency over time.

Status: ✅ All components complete. 797 tests pass.
Box standard: ~20 char wide, ~7 row high. Full descriptions in Diagram Notes.

---

## Component Map

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  Phase 1 — Capture (complete)                                                │
 │                                                                              │
 │  ┌──────────────────────┐                                                    │
 │  │ CLI Entry Points     │  kms capture <file>  kms capture --scan           │
 │  │ cli/main.py          │  kms watch           kms reconcile                │
 │  │ ✅ [closed]          │                                                    │
 │  └──────────┬───────────┘                                                    │
 │             │ calls                                                          │
 │   ┌─────────────────────────────────────┬──────────────────┐                                             │
 │   ▼                                     ▼                  ▼                                             │
 │  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────┐  │
 │  │ Handler Registry     │  │ Capture Pipeline     │  │ Reconcile        │  │
 │  │ handlers/            │  │ pipelines/capture.py │  │ Pipeline         │  │
 │  │ ✅ [ext: registry]   │  │ ✅ [closed]          │  │ pipelines/       │  │
 │  │                      │  │                      │  │ reconcile.py     │  │
 │  │ MarkdownHandler      │  │ 6 stages:            │  │ ✅ [closed]      │  │
 │  │ PdfHandler           │  │ extract              │  │                  │  │
 │  │ DocxHandler          │  │ enrich_urls          │  │ 6 stages:        │  │
 │  │ XlsxHandler          │  │ summarize            │  │ sync paths       │  │
 │  │                      │  │ metadata             │  │ orphan binaries  │  │
 │  │ new type = new file  │  │ apply_location_tags  │  │ stale binaries   │  │
 │  │ no existing changes  │  │ store                │  │ orphan siblings  │  │
 │  └──────────┬───────────┘  └──────────┬───────────┘  └──────────────────┘  │
 │             │ used by extract stage   │                                     │
 │             └──────────────┬──────────┘                                     │
 │                            │ all write to                                   │
 │  ┌─────────────────────────▼────────────────────────────────────────────┐   │
 │  │  Vault Layer (Phase 0 — used heavily by Phase 1)                     │   │
 │  │  vault/writer.py   vault/reader.py   vault/paths.py   vault/indexer  │   │
 │  └─────────────────────────┬────────────────────────────────────────────┘   │
 │                            │                                                │
 │  ┌─────────────────────────▼────────────────────────────────────────────┐   │
 │  │ Vault Watcher            vault/watcher.py   ✅ [closed]              │   │
 │  │ continuous mode: watches vault root, debounces events,               │   │
 │  │ calls capture for .md notes, syncs binary↔sibling on move/delete     │   │
 │  └──────────────────────────────────────────────────────────────────────┘   │
 │                                                                              │
 └──────────────────────────────────────────────────────────────────────────────┘
               │                              │
               ▼                              ▼
   ┌──────────────────────┐      ┌──────────────────────┐
   │ Obsidian Vault        │      │ SQLite Database      │
   │ (files on disk)       │      │ (index + audit log)  │
   └──────────────────────┘      └──────────────────────┘
```

---

## Diagram Notes

| Module | What it does |
|---|---|
| **CLI Entry Points** | Thin wrappers. `kms capture <file>` calls `capture_file()` once. `kms capture --scan` calls `scan_capture()` to batch all new/modified notes. `kms watch` starts `VaultWatcher`. `kms reconcile` calls `reconcile()`. No business logic here. |
| **Handler Registry** | Self-registering map of file extensions to handler classes. `HandlerRegistry.get(path)` returns the right handler. Adding a new file type = new class, no changes to existing code. Only handles filesystem paths — URLs/YouTube/email are pipeline stages, not registry handlers. |
| **Capture Pipeline** | 6-stage async pipeline. Each stage is a pure function. If any stage fails, the pipeline stops and returns `Failure`. Stages: extract (text) → enrich (fetch linked URLs if sparse) → summarize (AI) → metadata (AI + tag validation) → apply_location_tags (deterministic, no LLM — adds `domain/<D>` tag + `project:` field from file path) → store (branch on file type). |
| **Reconcile Pipeline** | 6-stage maintenance pipeline. Repairs orphaned files and broken links. Stage 1: sync vault paths with DB. Stage 2: capture binaries with no sibling. Stage 3: fix siblings with broken attachment_path. Stage 4: delete siblings with no binary (two guards: scope + type). Stage 5: fix stale location tags (reconcile_stale_tags). Stage 6: remove stale batch refs (reconcile_stale_batch_refs). |
| **Vault Watcher** | Watches the vault root using filesystem events. Debounces rapid saves. For .md notes: calls capture pipeline. For binary files: runs binary sync BEFORE skip checks — binary moved/deleted always triggers sibling update regardless of skip rules. |
| **URL Fetcher** | Pipeline stage (not a registry handler). Detects URLs in extracted text, fetches their content if the note is URL-heavy and body text is short (< 500 chars). Appends fetched content before passing to AI summarization. |

**Phase 1.5 attachment layout (key design change):**

Each project and domain has its own `attachment/` folder. There is no global `Vault/attachment/`. AI-generated summaries for binaries live in a hidden `.summaries/` subfolder — not next to the binary, and not in the vault root.

```
  Projects/<A>/
    attachment/
      design-spec.pdf          ← binary (reference only)
      .summaries/
        design-spec.pdf.md     ← AI summary note (indexed + searchable)
                                  frontmatter: attachment_path → design-spec.pdf
```

Sibling filename rule: `<binary.name>.md` (full filename including extension).
`report.pdf` → `report.pdf.md`. Prevents collision when `report.pdf` and `report.docx` share a folder.

---

## Feature Promises

What `kms` can do after this phase is complete.

### Capability List

```
  1. Drop a .md note into inbox
     → AI writes summary + metadata into frontmatter, renames file if helpful
     → original body text stays byte-identical
     → audit entry written

  2. Drop a PDF/DOCX/XLSX into a project or domain folder
     → AI reads text, creates sibling .md in attachment/.summaries/
     → binary moved to attachment/ (if not already there)
     → sibling is indexed and searchable; binary is reference-only
     → audit entry written

  3. Drop a PDF/DOCX/XLSX into inbox (no project context known)
     → AI creates a CLUELESS pending-routing marker in inbox/.summaries/
     → binary stays in inbox
     → marker waits for Phase 2 to classify and route both files
     → capture does NOT re-process this file on next scan

  4. Drop a URL-heavy note with little body text
     → AI fetches linked pages and appends their content
     → summary is based on full enriched text, not just the URL list

  5. Run kms watch (continuous mode)
     → any file dropped into inbox is auto-captured within seconds
     → binary moves/deletes inside attachment/ keep sibling in sync
     → no manual intervention needed

  6. Run kms capture --scan (batch mode)
     → all new or modified notes since last scan are captured
     → .summaries/ sibling notes are skipped (not re-captured)

  7. Run kms reconcile (repair mode)
     → binaries with no sibling are re-captured
     → siblings with broken attachment_path are removed
     → orphan siblings (type = attachment-summary, no binary) are deleted
     → vault path changes (renames, moves) are synced to DB
```

---

### Behavior: .md file dropped in inbox

```
  capture_file("inbox/quick-notes.md") called
          │
          ▼
  ① extract
     HandlerRegistry picks MarkdownHandler
     reads file text + existing frontmatter
     detects URLs in body
          │
          ▼
  ② enrich_urls
  ┌────────────────────────────────────────────────────────┐
  │ Is note URL-sparse? (few URLs AND body text < 500 chars) │
  └────────────────────────────────────────────────────────┘
     YES → fetch linked pages, append content
     NO  → skip (note has enough text already)
          │
          ▼
  ③ summarize
     AI reads enriched text
     AI writes a 3-5 sentence summary
          │
          ▼
  ④ metadata
     AI suggests: title, type, tags (domain is NOT a separate AI suggestion — lives only as domain/<D> in tags list)
     validate_tags() checks tags against config/tags.yaml
     violations → TAG_VIOLATION audit entry (not dropped)
          │
          ▼
  ⑤ store → _store_md branch
  ┌────────────────────────────────────────────────┐
  │ Has this exact content been captured before?   │
  │ (check content hash in documents table)        │
  └────────────────────────────────────────────────┘
     YES → write SKIPPED audit entry, done
     NO  → continue
          │
          ▼
  Rename Gate (4 rules, no AI call):
    filename is meaningless (date stamp, "untitled") → FULL_RENAME
    filename is recognizable but topic unclear       → AUGMENT
    filename is already descriptive                  → SKIP
          │
          ▼
  vault/writer.py: write note
    ✓ body text:    UNCHANGED (byte-identical to original)
    ✓ frontmatter:  summary, title, tags, type written/updated
    ✓ safety check: if updated_by_human = true → SKIP write
          │
          ▼
  documents.upsert(outcome)  →  DB row created or updated
  audit.write(CAPTURED)      →  audit entry written

  Before:  "inbox/quick-notes.md"
           body: "Met with Alice. Discussed Q2 API plan. Key risk: rate limits."
           frontmatter: (empty)

  After:   "inbox/quick-notes — Q2 API Planning.md"   ← AUGMENT rename
           body: "Met with Alice. Discussed Q2 API plan. Key risk: rate limits."  ← UNCHANGED
           frontmatter:
             summary: "Meeting with Alice covering Q2 API roadmap with focus on..."
             title:   "Q2 API Planning"
             tags:    ["type/meeting-notes", "domain/engineering"]
```

---

### Behavior: Binary file dropped inside a project folder (LOCATED path)

```
  You drop "design-spec.pdf" into Projects/ZalopayAPI/attachment/
          │
          ▼
  ① extract → PdfHandler reads text
  ② enrich_urls → skipped (binary, no URLs to enrich)
  ③ summarize → AI reads PDF text, writes summary
  ④ metadata → AI labels, tags validated
  ⑤ store → _store_nonmd branch
          │
          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │ Does a sibling already exist at                                │
  │ Projects/ZalopayAPI/attachment/.summaries/design-spec.pdf.md? │
  └────────────────────────────────────────────────────────────────┘
     YES + status ≠ pending-routing → skip (already captured)
     NO → continue
          │
          ▼
  ┌────────────────────────────────────────────────────┐
  │ Is source path inside Projects/<A>/ or Domain/<D>/ │
  │ → YES: we know where this file belongs (LOCATED)   │
  └────────────────────────────────────────────────────┘
          │
          ▼ sibling written FIRST (before binary move)
  vault/writer.py: write sibling .md
    path:            Projects/ZalopayAPI/attachment/.summaries/design-spec.pdf.md
    frontmatter:     summary, title, tags, type=attachment-summary,
                     attachment_path=Projects/ZalopayAPI/attachment/design-spec.pdf
    body:            [[design-spec.pdf]] (Obsidian wikilink to binary)
          │
          ▼
  vault/writer.py: move binary (if not already in attachment/)
    from: wherever it was dropped
    to:   Projects/ZalopayAPI/attachment/design-spec.pdf
          │
          ▼
  documents.upsert(sibling outcome)  →  DB row for sibling
  audit.write(CAPTURED)              →  audit entry

  Result:
    Projects/ZalopayAPI/
      attachment/
        design-spec.pdf               ← reference-only
        .summaries/
          design-spec.pdf.md          ← indexed, searchable, AI summary
            attachment_path: "Projects/ZalopayAPI/attachment/design-spec.pdf"
```

---

### Behavior: Binary file dropped into inbox (CLUELESS path)

```
  You drop "finance.pdf" directly into inbox/
  (no project or domain context in the path)
          │
          ▼
  ① extract → PdfHandler reads text
  ③ summarize → AI writes summary
  ④ metadata → AI labels, tags validated
  ⑤ store → _store_nonmd branch
          │
          ▼
  ┌────────────────────────────────────────────────────────┐
  │ Is source path inside Projects/<A>/ or Domain/<D>/?   │
  │ NO → path is inbox/ → we cannot determine destination  │
  │   → CLUELESS path                                      │
  └────────────────────────────────────────────────────────┘
          │
          ▼
  vault/writer.py: write pending-routing marker
    path:        inbox/.summaries/finance.pdf.md
    frontmatter: summary, tags, type=attachment-summary,
                 status=pending-routing,
                 attachment_path=inbox/finance.pdf
    body:        "_Pending classification — binary at: inbox/finance.pdf_
                  (Phase 2 will classify and route this file)"

  Binary stays in inbox/ (untouched)

  Next scan:
    scan_non_md_drops sees inbox/finance.pdf
    checks → sibling exists + status=pending-routing → SKIP
    does not re-capture

  Phase 2 (Classify) will:
    read inbox/.summaries/finance.pdf.md
    classify by summary + tags
    decide destination project/domain
    move binary + update sibling → LOCATED result
    preserve type=attachment-summary in frontmatter
```

---

### Behavior: URL-heavy note enrichment

```
  You drop a note like:
    "Interesting article about AI classification:
     https://example.com/paper
     https://another.com/post"
    (body is 120 chars, 2 URLs)
          │
          ▼
  ① extract → MarkdownHandler reads text
  ② enrich_urls
  ┌───────────────────────────────────────────────────────────────┐
  │ Is note URL-sparse?                                           │
  │ Check: URL count ≤ config max AND body text < 500 chars      │
  └───────────────────────────────────────────────────────────────┘
     NO (body >= 500 chars)     → skip enrichment, use body as-is
     YES (sparse — few URLs,    → fetch both pages
          short body text)        append fetched content to body
                                  pass enriched text to summarize
          │
          ▼
  ③ summarize → AI reads original note + fetched page content
  → summary captures the actual substance of the linked pages
     not just the URLs themselves
```

---

### Behavior: kms watch — continuous auto-capture

```
  kms watch starts
          │
          ▼
  VaultWatcher starts watching vault root
  filesystem event loop begins

  ─── file event: inbox/new-note.md CREATED ───────────────────────────────
          │
          ▼
  Is this a binary file (not .md)?
    NO → continue
          │
          ▼
  Is this inside .summaries/ under attachment/?  (TD-AS-1)
    YES → skip (sibling .md written by pipeline, not by user)
    NO  → continue
          │
          ▼
  Debounce: wait 100ms for rapid saves to settle
          │
          ▼
  capture_file("inbox/new-note.md") runs full 6-stage pipeline

  ─── file event: Projects/ZalopayAPI/attachment/report.pdf MOVED ──────────
          │
          ▼
  Is this a binary file?
    YES → run binary sync FIRST (before any skip checks)
          │
          ▼
  _handle_binary_move:
    old path: Projects/ZalopayAPI/attachment/report.pdf
    new path: Projects/ZalopayAPI/attachment/reports/report.pdf
          │
    same attachment/ folder → update sibling's attachment_path frontmatter
    different folder → SIBLING_ORPHANED (sibling cannot follow)
          │
          ▼
  audit.write(ATTACHMENT_MOVED or SIBLING_ORPHANED)
          │
          ▼
  _should_skip check → skip user callback (binary is not a .md note)
```

---

### Behavior: kms reconcile — 6-stage repair

```
  kms reconcile
          │
          ▼
  Stage 1 — Sync vault paths
    scan all .md files in vault
    compare paths + content hashes against DB
    ┌──────────────────────────────────────────────────────────┐
    │ file path matches DB row?   → OK                         │
    │ path not in DB, hash matches different path?             │
    │   → file was renamed/moved → UPDATE DB row               │
    │ path not in DB, no hash match?                           │
    │   → new file → INSERT row                                │
    │ DB has path that no longer exists on disk?               │
    │   → DELETE DB row                                        │
    └──────────────────────────────────────────────────────────┘
          │
          ▼
  Stage 2 — Orphan binaries
    walk all attachment/ folders
    for each binary found:
      does .summaries/<binary.name>.md exist?
        YES → OK
        NO  → re-run capture_file(binary) to create sibling
          │
          ▼
  Stage 3 — Stale binaries
    walk all .summaries/ folders
    for each sibling .md:
      does attachment_path field point to a real file on disk?
        YES → OK
        NO  → sibling is stale → delete sibling + remove DB row
          │
          ▼
  Stage 4 — Orphan siblings (two guards)
    walk .summaries/ folders
    for each .md file:
      Guard 1: is this path in a managed summaries area?
               (attachment/.summaries/ OR inbox/.summaries/)
               NO → leave alone (may be a user-placed note)
      Guard 2: does frontmatter have type = "attachment-summary"?
               NO → leave alone (protects user-placed notes)
      BOTH YES → does a binary exist for this sibling?
               YES → OK
               NO  → delete sibling + remove DB row
          │
          ▼
  Stage 5 — Stale location tags (reconcile_stale_tags)
    walk all .md files in vault
    for each note:
      re-derive location tags from current vault path
      if domain/<D> or project: field differs from file path → rewrite tags
      write LOCATION_OVERRIDE audit entry

  Stage 6 — Stale batch refs (reconcile_stale_batch_refs)
    scan documents table for rows with batch_id set
    for each batch_id: verify the batch still exists in batches table
    if batch deleted or missing → clear batch_id on documents row
```

---

## Phase 1 → Phase 2 Handoff

What Phase 1 writes and what Phase 2 expects to find.

| What Phase 1 writes | Where | What Phase 2 reads |
|---|---|---|
| `.md` note with AI summary + tags in frontmatter | `inbox/` | Summary + tags to classify into a project/domain |
| CLUELESS pending-routing marker | `inbox/.summaries/<name>.pdf.md` | `attachment_path` + summary to classify + route both files |
| `status: pending-routing` | CLUELESS markers only | Phase 2 scans for this flag as its input |
| `type: attachment-summary` | All binary siblings | Phase 2 MUST preserve this when resolving CLUELESS markers |
| Audit entry: `CAPTURED` | SQLite audit_log | Phase 2 links its `CLASSIFIED` entry to the same source_id |
