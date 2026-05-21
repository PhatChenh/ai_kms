# Discussion Log — Capture Pipeline
_Started: 2026-05-20_

---

## Planning Session — capture_pipeline
_Captured: 2026-05-20_

### Exchanges

**Q:** In the store stage, what does `sanitized_stem != source_path.stem` mean, and what happens next?

**A:** It means the AI proposed a title different from the current filename stem. Example: file on disk = `meeting notes oct.md`, AI title = `"Q3 Budget Review"` → sanitized stem = `q3-budget-review` → stems differ → rename triggered.

Decision tree:

- Destination already exists (collision) → try suffix (`-1` through `-9`); if all taken, keep original filename and log WARNING
- Destination free → `move_note(src, dst, actor="ai")` → delete old `documents` row → insert new `documents` row

If stems are equal: skip all rename logic, write AI metadata into frontmatter in place, upsert `documents` row.

Critical invariant: the body written back to disk is always re-read from `read_note(source_path)` at the top of `store`, never taken from `RawContent.text`. `enrich_urls` may have augmented `RawContent.text` with fetched URL content — that augmented text feeds the LLM stages only and must never reach disk.

_Key concept: capture pipeline store stage — rename decision and body preservation invariant_

---

**Q:** When there's a rename collision, why not try a slightly different name (e.g. suffix) rather than silently keeping the original filename?

**A:** The suffix approach is correct and should be Phase 1 behavior. The original plan said "keep original name and log WARNING" — that's worse because it leaves a file named `meeting-notes-385.md` even though the AI has a useful name for it. The fix: try `sanitized_stem-1` through `sanitized_stem-9` before giving up. Ten attempts covers any realistic vault. Only if all 10 slots are taken does the pipeline fall back to the original filename.

_Key concept: rename collision resolution — suffix loop before fallback_

---

**Q:** What would the full AI rename disambiguation strategy look like, and why is it deferred?

**A:** The full strategy: when a rename collision is detected, the AI reads the summary of the *existing* note and compares it against the summary of the new note. It then proposes disambiguated names for one or both — for example, if both notes are about Q3 budget but one covers movies and one covers billing, the AI renames them `q3-budget-movie.md` and `q3-budget-billing.md`.

Why deferred: (1) renaming an existing note is a side effect on an already-indexed note — if that note has `updated_by_human=True`, the write is blocked, producing an incoherent half-rename; (2) requires at least two extra LLM calls per collision; (3) cascading risk — the renamed existing note might now collide with a third note; (4) renaming both notes without user confirmation is aggressive.

The feature should only be implemented after TD-015 (co-authoring section-merge) replaces the `updated_by_human` whole-note lock. Once per-section ownership exists, renaming an existing note no longer risks hitting a blanket write block mid-operation.

_Key concept: AI semantic rename disambiguation — precondition on co-authoring feature_

---

**Q:** Why does renaming an existing note require waiting for the co-authoring feature specifically, rather than just being a later phase?

**A:** The `updated_by_human` flag is a whole-note boolean gate: if it's `True`, no AI write to that note is allowed — including a rename. Renaming the new note is safe (it's freshly captured, `updated_by_human=False`). But renaming the *existing* note requires a write to a note the AI did not just capture. That note could have `updated_by_human=True` from a prior human edit, which would block the rename mid-operation — the new note gets its good name, the existing note stays with its old name, and the disambiguation fails silently.

The co-authoring feature (TD-015) replaces the blunt whole-note lock with per-section ownership. Once that exists, an AI rename of the filename (not the body) can be treated as a metadata-only operation that doesn't require holding the whole-note lock. Without co-authoring, there is no clean way to make the rename of an existing note safe.

_Key concept: updated_by_human whole-note lock as a blocker for cross-note AI operations_

---

**Q:** Does the capture pipeline handle files dropped outside the inbox folder, or is it inbox-only?

**A:** The pipeline is designed for any folder — "capture from everywhere" — but the plan's wording and test criteria were inbox-biased. The code logic was always correct:

- `capture_file(path)` takes any `Path` — no folder restriction
- `scan_vault(root)` walks the whole vault root; `detect_changes` returns `added` entries from any subfolder
- `VaultWatcher(root, callback)` watches the vault root, not just `inbox/`
- Non-md branch: `sibling = source_path.parent / (stem + ".md")` — sibling created in same folder as drop, not moved to inbox

The fixes applied to the plan: (1) removed "inbox" from watcher description/docstring; (2) added explicit test in Phase 3 — non-md PDF dropped in `Projects/foo/` must produce sibling in `Projects/foo/`, not `inbox/`; (3) added non-inbox `.md` integration test in Phase 4; (4) added non-inbox scan test in Phase 5; (5) added non-inbox watcher callback test in Phase 6.

_Key concept: capture-from-everywhere — pipeline scope and test coverage for non-inbox drops_

---

## Phase 10 — Modified/deleted/moved reconciliation
_Captured: 2026-05-21_

### Exchanges

**Q:** Why do CLI runs not appear in `kms.log` even though logs are visible in the terminal?

**A:** `setup_logging()` was never called in `cli/main.py`. Without that call, structlog has no file handler configured — the stdlib root logger has no `RotatingFileHandler` pointing at `kms.log`. Logs reach the terminal via structlog's default fallback (it prints to stdout when unconfigured), but nothing routes to the file. Fix is two lines at CLI startup: import and call `setup_logging(log_level="DEBUG", dev_mode=True)` after `load_dotenv`.

```python
# cli/main.py — after load_dotenv
from core.logging_setup import setup_logging
setup_logging(log_level="DEBUG", dev_mode=True)
```

`dev_mode=True` keeps the human-readable console output; the file handler is added on top.

_Key concept: structlog file handler requires explicit setup_logging() call at CLI startup_
