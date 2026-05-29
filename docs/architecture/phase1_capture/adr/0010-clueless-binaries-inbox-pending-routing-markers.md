# CLUELESS binaries → inbox pending-routing markers; Phase 2 Classify resolves them

When `_store_nonmd()` cannot determine a project/domain from the source path, it writes a minimal sibling at `inbox/.summaries/<stem>.md` with `status=pending-routing`, `type=attachment-summary`, `attachment_path` pointing to the binary location. Binary stays in inbox. Capture does NOT re-process these files on subsequent scans (early-exit guard checks for `status=pending-routing`).

**Status:** accepted

**Considered Options**

- Block capture and surface to user for manual routing — rejected: non-technical user should not have to route files.
- Classify inline in capture — rejected: Phase 2 Classify is the dedicated routing pipeline; bundling violates stage separation.

**Consequences**

- Phase 2 Classify must check `status=pending-routing` on `.md` files in `inbox/.summaries/` as its input scan.
- `scan_non_md_drops` Rule 2 skip (`_has_inbox_sibling`) prevents capture from re-triggering on already-parked CLUELESS binaries.
- Any pipeline writing to `inbox/.summaries/` must set `status=pending-routing` (or clear it — Classify's job).
