# Reconcile Stage 4 unlinks only with two guards: managed-summaries scope + attachment-summary type

`reconcile_orphan_siblings` (Stage 4) filters `summaries_dir` via `_is_managed_summaries_area(summaries_dir, vault_cfg)` AND filters individual sibling entries via `note.metadata.type == "attachment-summary"` before considering unlink. Both guards must pass.

**Status:** accepted (code review 2026-05-24, issues #2 + #3)

**Considered Options**

- Scope guard only — still unlinks user-placed `.md` files if scope happens to match.
- Type guard only — unscoped `rglob` finds stray `.summaries/` folders anywhere in vault.
- Trust the layout — unsafe; data-loss potential in an unattended command.

**Consequences**

- Defense in depth: scope rules out non-managed `.summaries/` directories; type rules out user-placed siblings of a different kind within managed dirs.
- Any new pipeline writing to `.summaries/` must set `type=attachment-summary` in frontmatter, otherwise Stage 4 will leave its output alone.
- Phase 2 Classify, when resolving CLUELESS markers, must preserve the `type` field.
- New stages creating different sibling kinds inside `.summaries/` require either their own type string + Stage 4 exclusion clause, or a separate parallel folder.
