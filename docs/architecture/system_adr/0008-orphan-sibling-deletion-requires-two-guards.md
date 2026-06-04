# Orphan sibling deletion requires two independent guards

`reconcile_orphan_siblings` (Stage 4) will only delete a `.md` file under `.summaries/` if and only if **both** guards pass:

1. **Scope guard** — `_is_managed_summaries_area(path, vault_cfg)` returns True (path is under `attachment/.summaries/` or `inbox/.summaries/`)
2. **Type guard** — frontmatter `type == "attachment-summary"`

**Status:** accepted

**Considered Options**

- Scope guard only — would delete any `.md` a user manually placed inside `.summaries/`. Silent data loss.
- Type guard only — would scope too broadly; could affect `.md` files outside managed areas if a user happens to set `type: attachment-summary`.

**Consequences**

- Any code writing a sibling into `.summaries/` MUST set `type: attachment-summary` in frontmatter, or reconcile will leave it as an orphan indefinitely.
- Phase 2 Classify MUST preserve `type: attachment-summary` when resolving CLUELESS markers — otherwise the resolved sibling becomes invisible to Stage 4 cleanup if the binary is later deleted.
- The two guards are checked in order: scope first, then type. A file outside managed areas is never opened.
