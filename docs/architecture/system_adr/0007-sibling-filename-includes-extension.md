# Sibling summary filename uses full binary name including extension

When capturing a non-md binary, the sibling `.md` summary is named `<binary.name>.md` — the full filename including extension — not `<binary.stem>.md`.

Example: `report.pdf` → `.summaries/report.pdf.md` (not `report.md`).

**Status:** accepted

**Considered Options**

- `<binary.stem>.md` (stem only, no extension) — simpler to read, but causes silent collision when `report.pdf` and `report.docx` share the same `attachment/` folder. Both would map to `report.md`, with the second write silently overwriting the first.

**Consequences**

- `_sibling_for(binary, vault_config)` in `vault/watcher.py` is the canonical helper — use it everywhere, never recompute the path inline.
- Phase 2 Classify must use the same naming convention when routing CLUELESS markers.
- `reconcile_orphan_siblings` (Stage 4) uses `<binary.name>.md` as the lookup key.
