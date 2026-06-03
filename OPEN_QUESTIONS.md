# Open Questions

## 🔴 Open

### OQ-001 · Move/rename detection when note is edited AND moved simultaneously
**Blocks:** Phase 1 vault indexer
**Status:** 🔴 Open
**Question:** `content_hash`-based move detection fails when a note is edited and moved simultaneously (hash changes; indexer sees delete + insert rather than rename). Is `content_hash` sufficient for Phase 1, or does Phase 1 need to write `doc_id` to frontmatter?
**Context:** DECISION-001 chose integer PK + `content_hash` over frontmatter `doc_id` for simplicity. Edge case: simultaneous edit + move produces a new hash, which the indexer cannot match to the old record. The cost of writing `doc_id` to frontmatter is a vault write on first capture; the cost of not doing it is spurious orphan detection on rename+edit combos.

---

### OQ-002 · Fine-grained AI vs human authorship per section
**Blocks:** Phase 7+
**Status:** 🔴 Open
**Question:** If an MCP tool needs to show "AI wrote this summary, you wrote this conclusion," what design is needed? `updated_by_human` is whole-note only. A per-section design would require HTML comments or a separate `edits` table.
**Context:** DECISION-002 intentionally chose a blunt whole-note gate — fine-grained tracking is explicitly out of scope. Any future implementation requires a separate design; do not extend the `updated_by_human` column.

---

### OQ-003 · wal_autocheckpoint tuning before Phase 4 MCP daemon
**Blocks:** Phase 4
**Status:** 🔴 Open
**Question:** Reference project sets `wal_autocheckpoint=100` pages; SQLite default is 1000. Worth adding to `_connect()` before Phase 4 MCP (long-running daemon), or accept default for CLI?
**Context:** For the CLI this is irrelevant — process exits cleanly and WAL truncates on close. For the MCP daemon (long-running process with many short-lived tool calls), unchecked WAL growth could cause read latency. Revisit at Phase 4 planning. See also TD-007.

---

### OQ-004 · Concurrent run_pipeline calls in Phase 4 MCP daemon — contextvar bleed
**Blocks:** Phase 4
**Status:** 🔴 Open
**Question:** `clear_contextvars()` in `new_correlation_id()` resets the shared contextvar store. In a concurrent async daemon serving two simultaneous tool calls, call A's `clear_contextvars()` wipes call B's correlation_id mid-flight. How should concurrent runs be isolated?
**Context:** DECISION-010 chose async for Phase 4 concurrency. The fix is per-run contextvar copies (`copy_context().run(...)`) or a scoped contextvar pattern. Phase 1 CLI is single-run — this is safe today. Must be resolved before Phase 4 ships concurrent MCP tool handling.

---

### OQ-005 · updated_by_human locking granularity in Phase 11 watcher
**Blocks:** Phase 11 (watcher)
**Status:** 🔴 Open
**Question:** When the Phase 11 watcher detects a human body edit and sets `updated_by_human=True`, should the AI still be allowed to update frontmatter (metadata-only writes) if the body is unchanged? Or keep the whole-note lock?
**Context:** DECISION-002 specifies whole-note lock. The tension is: a user fixing a typo in the body shouldn't freeze the AI from updating, e.g., the `last_seen` or `summary` frontmatter fields. Relaxing to body-only lock requires distinguishing body edits from frontmatter edits at detection time — a harder filesystem watch problem.

---

### OQ-006 · Obsidian wikilink path shape inside .summaries/<x>.md pointing at binary
**Blocks:** Brief #2
**Status:** 🔴 Open
**Question:** What wikilink format should sibling `.md` files use to point at the binary? `[[report.pdf]]` is vault-wide and ambiguous when two projects both have `report.pdf`. `[[Projects/A/attachment/report.pdf]]` (full path) or `[[../report.pdf]]` (relative) is unambiguous but must be verified against Obsidian's resolver on a real vault.
**Context:** Cannot verify live in research session. Brief #2 capture pipeline must decide and test on a real vault before shipping. Full vault-relative path is the safest option but Obsidian's behavior with leading `/` paths vs bare paths needs confirmation.

---

### OQ-007 · summarize_attachment prompt quality on real diverse binary types
**Blocks:** Brief #2 Phase 2 post-ship
**Status:** 🔴 Open
**Question:** Does the `summarize_attachment` prompt (3-section: What this file is / Key content / Key facts) produce useful output across diverse binary types — PDF financial reports, DOCX meeting notes, XLSX data tables?
**Context:** Prompt structure was designed without testing against real vault files. May need iteration after first real vault run. The 3-section template is a reasonable starting point; thin-content files (short DOCX) already have a fallback via TD-028 fix. Real-world quality assessment deferred to post-Brief-#2 ship.

---

### OQ-008 · Detecting human edits to capture-excluded AI-output folders during co-authoring
**Blocks:** Co-author phase
**Status:** 🔴 Open
**Question:** The vault-restructure design adds a capture-excluded class for AI-output folders (`Briefings/`, `Synthesis/`, `Documentation/`) — the watcher and `scan_capture` skip them entirely so the AI's own outputs are never re-captured. But co-authoring means the human will edit these files (e.g. correct a Documentation page). With capture excluded, how does the system learn a human touched the file? Should a watcher/capture path listen for changes in these folders specifically to flag `updated_by_human`, or is a different detection mechanism needed?
**Context:** Capture-exclusion (decided in the vault-restructure grill) is correct for preventing the briefing→re-capture feedback loop, but it blinds the system to legitimate human edits in those same folders. The two needs conflict: "never re-capture AI output" vs "detect human co-author edits." Related to OQ-005 (updated_by_human locking granularity) and OQ-002 (per-section authorship). Revisit when co-authoring is designed — likely needs an edit-detection path that sets the human-edit flag without triggering a capture.
