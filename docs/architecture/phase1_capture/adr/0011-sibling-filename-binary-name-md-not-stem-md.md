# Sibling marker filename uses <binary.name>.md (full filename incl. extension), not <binary.stem>.md

Capture pipeline writes sibling at `<parent>/<summaries_subdir>/<binary.name>.md` — e.g. `report.pdf` → `.summaries/report.pdf.md`. Replaces earlier `<stem>.md` pattern from Brief #2.

**Status:** accepted (code review 2026-05-24, issues #4 + #5)

**Considered Options**

- `<stem>-<ext>.md` (e.g. `report-pdf.md`) — ugly.
- `<stem>-<hash6>.md` — unique but unreadable.
- `<stem>.md` (original) — broken when two binaries share stem (`report.pdf` and `report.docx` both produce `report.md`, second clobbers first's `attachment_path`).

**Consequences**

- `<binary.name>.md` is bijective with the binary; trivially round-trips via `Path.with_suffix("")`.
- All sibling lookups must use `<binary.name>.md`. Phase 2 Classify resolves markers by reading `attachment_path` from frontmatter, not by stem-matching sibling filename.
- Any new helper that maps binary → sibling must call `_sibling_for(binary, vault_config)` from `vault/watcher.py` — do not duplicate the path math.
