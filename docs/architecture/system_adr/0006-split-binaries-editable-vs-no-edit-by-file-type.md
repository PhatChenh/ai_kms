# Split captured binaries into editable (root) vs no-edit (attachment/) by file type

**Status:** Proposed

> **NOT IMPLEMENTED as of Phase Pre-2 (2026-06-03).** The `no_edit_extensions` config key and editable-file routing do not exist in `src/`. Vocabulary from this ADR (editable file, no-edit file) appears in CONTEXT.md marked as "planned." Do not reference these terms as current behavior.

## Context

Target user is a non-technical executive whose real working files are office documents (xlsx, docx, pptx). The current pipeline hides every captured binary in the Obsidian-hidden `Projects/<A>/attachment/` folder, which removes editable files from the user's view. Reference files (pdf, images) are fine to hide since the user does not edit them in place.

## Decision

Classify non-`.md` files via a config-driven `no_edit_extensions` list. 
- **No-edit files** (pdf, images) → `Projects/<A>/attachment/` or `Domain/<D>/attachment/` (Obsidian-hidden)
- **Editable files** (xlsx, docx, pptx, etc. — everything else non-md) → project/domain root (visible in Obsidian file browser)
- **Siblings** `.md` summaries → remain next to their binary at `<parent>/.summaries/<binary.name>.md`

## Alternatives Considered

- All binaries in `attachment/` (current) — hides editable files from user's view, rejected.
- All binaries in root — clutters root with pdfs/images the user never opens, rejected.
- Centralized single `.summaries/` per project — larger refactor, no real gain since Obsidian hides dotfolders anyway; keep next-to-binary placement.

## Consequences

- **Visibility:** Editable files are now visible and editable in place by the user; no need for file export/import.
- **Config dependency:** Capture pipeline must read `no_edit_extensions` list from config and call a shared placement helper — introduces tight config coupling at capture entry point.
- **Dual path predicates:** The two near-twin predicates `_is_in_managed_attachment` and `_is_managed_summaries_area` (in `vault/paths.py`) must now recognize root-level `.summaries/` folders (new scope: any `<Projects|Domain>/<name>/.summaries/` location).
- **Change detection:** Requires content-change detection so edits to captured editable files refresh siblings. Mac-first implementation; Windows deferred (TD-039).
- **Reconcile migration:** Existing editable files currently in `attachment/` need a one-time reconcile pass to move them to root (new stage, handled as part of Phase 2).
- **Phase 2 Classify dependency:** Phase 2 must use the same placement helper when resolving CLUELESS binaries, ensuring consistent routing for both capture and classify pipelines.
