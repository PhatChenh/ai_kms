# Indexer .summaries/ dotfolder allowlist is scoped — parent folder must be named "attachment"

`_DOT_ALLOWLIST = frozenset({".summaries"})` in `vault/indexer.py`. Prune condition: `(not d.startswith(".") or (d in _DOT_ALLOWLIST and dirpath.name == "attachment"))`. A `.summaries/` folder is only traversed when its immediate parent is named `"attachment"`.

**Status:** accepted (Phase 1.5 OQ-AL4 — scoped allowlist)

**Considered Options**

- Global allowlist (traverse any `.summaries/` anywhere in vault) — rejected: any user-created `.summaries/` in `inbox/` or elsewhere would be unexpectedly indexed.

**Consequences**

- Prevents accidental indexing of user-placed `.summaries/` folders outside the managed attachment subtree.
- If a new hidden-but-indexable convention emerges (e.g. `.archive-index/`), add it to `_DOT_ALLOWLIST` with an appropriate parent-folder guard in the same condition.
