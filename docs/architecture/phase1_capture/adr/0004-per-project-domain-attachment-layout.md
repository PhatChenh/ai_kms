# Per-project/domain attachment layout replaces single global Vault/attachment/

Each `Projects/<A>/` and `Domain/<D>/` has its own `attachment/` subfolder. The global `Vault/attachment/` folder is removed. Non-md binaries live at `Projects/<A>/attachment/<file>`. Sibling `.md` summaries live at `Projects/<A>/attachment/.summaries/<file>.md` (dot-prefix hides folder from Obsidian).

**Status:** accepted (Phase 1.5, complete 2026-05-23)

**Considered Options**

- Global `Vault/attachment/` — rejected: boss expects all project files in one place under the project folder.
- Sibling next to source (old Phase 1 design) — rejected: floods vault with near-empty notes.

**Consequences**

- `pipelines/capture.py` must use `vault/paths.py::project_attachment(name)` / `project_summaries(name)` to compute target paths — never hardcode `"attachment"` or `".summaries"`.
- `vault/watcher.py` must generalize per-project attachment-skip logic (TD-023).
- `scan_non_md_drops` single `attachment_path` skip must generalize (TD-024).
