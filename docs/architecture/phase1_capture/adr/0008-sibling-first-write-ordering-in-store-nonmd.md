# Sibling-first write ordering in _store_nonmd() — binary move is step 2, never step 1

In the LOCATED path, the sibling `.md` is written **before** the binary is moved. Move only happens if `needs_move=True`. If the move fails after the sibling write, the broken `attachment_path` pointer is the accepted failure mode.

**Status:** accepted (OQ-AC6)

**Considered Options**

- Move-first — rejected: if sibling write fails after move, binary is displaced with no index record; harder to reconcile.

**Consequences**

- A sibling with a broken pointer is detectable and reconcilable (Brief #3 orphan pass). A moved binary with no sibling is invisible to search and harder to recover.
- Brief #3 reconciliation pass must handle the case where `attachment_path` in sibling frontmatter points to a file that no longer exists (TD-026).
