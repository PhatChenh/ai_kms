# Open Questions

## 🔴 Open

### OQ-002 · Fine-grained AI vs human authorship per section
**Blocks:** Phase 7+
**Status:** 🔴 Open
**Question:** If an MCP tool needs to show "AI wrote this summary, you wrote this conclusion," what design is needed? `updated_by_human` is whole-note only. A per-section design would require HTML comments or a separate `edits` table.
**Context:** DECISION-002 intentionally chose a blunt whole-note gate — fine-grained tracking is explicitly out of scope. Any future implementation requires a separate design; do not extend the `updated_by_human` column.

---

### OQ-003 · wal_autocheckpoint tuning before Phase 4 MCP daemon
**Blocks:** Phase 4
**Status:** ✅ Closed (2026-06-12) — shipped in Phase 4: `wal_autocheckpoint=100` added to `_connect()` (closes TD-007). See STATE.md "P4 Phase 1".
**Question:** Reference project sets `wal_autocheckpoint=100` pages; SQLite default is 1000. Worth adding to `_connect()` before Phase 4 MCP (long-running daemon), or accept default for CLI?
**Context:** For the CLI this is irrelevant — process exits cleanly and WAL truncates on close. For the MCP daemon (long-running process with many short-lived tool calls), unchecked WAL growth could cause read latency. Revisit at Phase 4 planning. See also TD-007.
**→ Resolution planned (2026-06-11):** folded into the Phase 4 plan as **Phase 1** — add `wal_autocheckpoint=100` to `_connect()` (keep `PRAGMA foreign_keys=ON`, C-04). Implement in `/tdd-implement`. See `docs/4_plans/P4_mcp_context_injection.md`.

---

### OQ-004 · Concurrent run_pipeline calls in Phase 4 MCP daemon — contextvar bleed
**Blocks:** Phase 4
**Status:** ✅ Closed (2026-06-12) — shipped in Phase 4: each MCP tool dispatch wrapped in `copy_context().run(...)`. See STATE.md "P4 Phase 2".
**Question:** `clear_contextvars()` in `new_correlation_id()` resets the shared contextvar store. In a concurrent async daemon serving two simultaneous tool calls, call A's `clear_contextvars()` wipes call B's correlation_id mid-flight. How should concurrent runs be isolated?
**Context:** DECISION-010 chose async for Phase 4 concurrency. The fix is per-run contextvar copies (`copy_context().run(...)`) or a scoped contextvar pattern. Phase 1 CLI is single-run — this is safe today. Must be resolved before Phase 4 ships concurrent MCP tool handling.
**→ Resolution planned (2026-06-11):** folded into the Phase 4 plan as **Phase 2** — wrap each MCP tool dispatch in `copy_context().run(pipeline_fn)` in the server shell. Implement in `/tdd-implement`. See `docs/4_plans/P4_mcp_context_injection.md`.

---

### OQ-005 · updated_by_human locking granularity in Phase 11 watcher
**Blocks:** Phase 11 (watcher)
**Status:** 🔴 Open
**Question:** When the Phase 11 watcher detects a human body edit and sets `updated_by_human=True`, should the AI still be allowed to update frontmatter (metadata-only writes) if the body is unchanged? Or keep the whole-note lock?
**Context:** DECISION-002 specifies whole-note lock. The tension is: a user fixing a typo in the body shouldn't freeze the AI from updating, e.g., the `last_seen` or `summary` frontmatter fields. Relaxing to body-only lock requires distinguishing body edits from frontmatter edits at detection time — a harder filesystem watch problem.

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

---

### OQ-009 · Image capture (PNG/JPG) — build vision-LLM/OCR or drop the behavior?
**Blocks:** Phase 2 (Classify), if it assumes image drops produce summaries
**Status:** 🔴 Open
**Question:** `PngHandler`/`JpgHandler.extract()` are not-implemented stubs that return `Failure("image extraction requires a vision-capable LLM — not yet implemented", recoverable=False)` (`src/handlers/image_handler.py:21-26, :30-38`). Images are NOT captured — no sibling `.md`, no summary. The behavior inventory `P15-HDL-07` claims images get a sibling summary (OCR or filename-based). Build image capture (vision-capable LLM or Tesseract OCR), or drop/defer and retire `P15-HDL-07`?
**Context:** Surfaced during the 2026-06-05 behavior-inventory reconcile as a design-vs-implementation conflict (`P15-HDL-07`, `status: conflict`). Blocks nothing today, but Phase 2 Classify must not assume image drops produce summaries. Decision needed before any feature depends on image content.

---

### OQ-010 · `pipelines/reconcile.py` has no owning phase in the rearchitecture
**Blocks:** Phase 6 (Daemon)
**Status:** 🔴 Open
**Question:** `pipelines/reconcile.py` is a live consumer of dead-module targets (`vault/writer.py`, `reader.py`, `indexer.py`, `frontmatter.py` + `paths.py` placement helpers). Per ADR-0012, a module dies only when its LAST consumer is rewritten — but no rearchitecture phase (5–10) owns rewriting/retiring reconcile. Rearch doc §11 says the 7-stage reconcile is "replaced by daemon scan" and "DB-only reconcile may survive in simplified form," but the transition is unassigned. Which phase owns it, and does DB-only reconcile survive or retire entirely?
**Context:** Surfaced during the 2026-06-12 Phase 5 Slice 1 build-pipeline grill. As long as reconcile stays live and imports the dead modules, those modules cannot be deleted — so reconcile's fate gates the deletion mapping in ADR-0012. Must be resolved before Phase 6 (daemon scan replaces the file-diff portion of reconcile). See ADR-0012 and the Phase 5 "RETIRE DEAD MODULES" roadmap warning.

---

### OQ-011 · Daemon→AgentBase platform auth — shared IAM account vs dedicated per device?
**Blocks:** Phase 6 (Daemon)
**Status:** 🔴 Open
**Question:** Should the Phase 6 laptop daemon authenticate to the AgentBase *platform* using the SAME IAM service account as the cloud container, or its own dedicated account? This is the daemon's platform auth — distinct from the shared `KMS_DAEMON_API_KEY` it sends on `/api/*` (that is app-level request auth, NOT platform auth). Shared = one fewer credential to provision, but a leak exposes both sides. Dedicated = cleaner per-device revocation, more onboarding steps.
**Context:** Surfaced during the 2026-06-13 P5 Slice 2 build-pipeline design as that doc's OQ-2 (`docs/1_design/P5_slice2_deployment_foundation.md`). Out of scope for Slice 2 (which builds only the cloud side + the shared request-key gate). Design recommendation: dedicated account, decided when the daemon is built in Phase 6.

---

### OQ-7D · Blob-reference shape if a document ever needs more than one blob
**Blocks:** Nothing (future multi-blob feature only)
**Status:** 🔴 Open (deferred — design recommendation stands)
**Question:** Phase 7B stores one blob per document via two nullable columns on `documents` (blob reference + `mime_type`). If a future feature needs several blobs per document (e.g. a multi-page scan kept as separate page images), do we migrate to a dedicated `blobs`/`attachments` table?
**Context:** Two columns model the strict one-file = one-blob = one-row relationship with no join. A `blobs` table is a clean one-to-many but adds a join to every reader for flexibility nothing in 7B or Phase 9 needs. Recommendation: keep the two columns; migrate to a table only if a real multi-blob need appears. Source: `docs/1_design/phase7/phase7b_visual_capture.md` Decision 1; seed §A4.

---

### OQ-7E · Where the vision model name lives in provider config (C-09 interaction)
**Blocks:** Phase 7B (vision wiring)
**Status:** 🔴 Open (design recommendation: dedicated `vision_model` field)
**Question:** Does the vision-capable model get its own `vision_model` field on each provider config, or is it folded into an existing field (e.g. routing the `"vision"` task to `synthesis_model` if multimodal)?
**Context:** C-09 lists `model`/`synthesis_model`/`embedding_model` as required provider fields; a dedicated `vision_model` is a same-shape addition but grows that list (note it in the constraint). Reusing `synthesis_model` couples vision to whatever the synthesis model is and breaks if it is text-only. Recommendation: dedicated `vision_model` field. Source: `docs/1_design/phase7/phase7b_visual_capture.md` Decision 3; seed §A5.

---

### OQ-7G · Vision route + PDF describability
**Blocks:** Phase 7B (the `describe_image` implementation and the describable-type set)
**Status:** 🟡 Partially resolved — route DECIDED, capability still to verify at research
**Decision (human, 2026-06-14):** Vision route = **AgentBase MaaS (platform LLM, OpenAI-compatible)** — owner's choice for cost control, over the design's Anthropic-SDK recommendation. So `describe_image` targets the MaaS `image_url` content-block shape.
**Still open (BUILD-BLOCKING, verify at research before vision code):** Does AgentBase MaaS (a) accept image input at all, and (b) accept text-less PDF input? → MaaS no-vision = HARD STOP, escalate; MaaS images-only = drop `application/pdf` from the describable set (config edit), text-less PDFs fall to store-only; MaaS images+PDF = keep PDF in the set. Keep the describable set config-editable. Source: `docs/1_design/phase7/phase7b_visual_capture.md` Decision 6 + OQ-7G.

---

### OQ-7H · Storage-client library + bucket topology (HUMAN-GATED before install)
**Blocks:** Phase 7B (the blob-store module + any dependency install)
**Status:** 🟢 Resolved (human, 2026-06-14)
**Decision:** **`boto3` (sync, thread-wrapped) + a separate `KMS_BLOB_*` bucket inside VNG Object Storage** — the same VNG service Litestream already uses (no new vendor). Matches the design recommendation. `boto3` is the one approved new dependency; the actual `pip`/`pyproject.toml` install happens at implementation time, not before. **Still to verify at research:** VNG Object Storage access details (endpoint, bucket creation, access-key env var names). Source: `docs/1_design/phase7/phase7b_visual_capture.md` Decision 4; requirements decision 7.

---

### OQ-7I · Audit outcome name for a successful vision describe
**Blocks:** Nothing (observability nicety)
**Status:** 🔴 Open (design recommendation: distinct `DESCRIBED`)
**Question:** Should a successful vision describe write a distinct `DESCRIBED` audit outcome, or reuse the text path's `CAPTURED`?
**Context:** `core.audit.write` accepts any outcome string. A distinct `DESCRIBED` lets the briefing / future UI tell a vision describe from a text capture; the skip/failure entries already carry their own reason strings (too-big / unsupported-type / vision-failed). Recommendation: distinct `DESCRIBED`. Source: `docs/1_design/phase7/phase7b_visual_capture.md` Decision; seed §A5.

---

### OQ-P8A-01 · Where does the classify worker start so it survives container boot?
**Blocks:** Phase 8 Slice A (classify worker wiring)
**Status:** 🔴 Open — `[UNVERIFIED]` (routed to research)
**Question:** Right now the cloud container builds its web app in one place at startup, but the system that handles live chat connections starts up separately, later, only when someone connects. The question: do we start the classify worker at container build/startup, or piggyback on the chat-connection startup? **If we start it at container build/startup:** the worker and its catch-up scan run as soon as the container is healthy, even with no chat clients connected — correct for a background housekeeper. **If we piggyback on chat-connection startup:** classification would not run until a human first connects a chat client — wrong; capture would pile up unclassified.
**Recommendation (Phase 8 Slice A design):** start at container build/startup — a background housekeeper must not wait for a human to open a chat. **`[UNVERIFIED]` — research must confirm against `mcp_server/cloud_entry.py` / `server.py` lifecycle** (CLAUDE.md notes the MCP lifespan fires per-chat-session, not at uvicorn boot, so the worker must NOT be hooked there). Routed to the research phase. (Not a blocker — resolvable by reading the startup code.)
**Context:** Raised by the Phase 8 Slice A design (`docs/1_design/phase8_sliceA_classify_infra.md`, OQ-P8A-01 + Risks). See ADR-0017 (in-memory queue + `classify_content_hash` work discovery), whose load-bearing consequence is that the worker must start at container boot.

---

### OQ-P8A-02 · One ranked query function, or extend the existing fact-loader?
**Blocks:** Phase 8 Slice A (Context Loader ranked+capped query)
**Status:** 🔴 Open
**Question:** Right now there is a function that loads non-retired facts (`knowledge_entries.get_confident_and_pending`) but does not rank or cap them. The question: add a new ranked+capped query function, or add sort/limit parameters to the existing one? **If new function:** the existing 5-function fact-store contract stays stable; ranking is isolated and separately testable. **If extend existing:** one fewer function, but the existing callers and tests now carry optional ranking params they don't use.
**Recommendation (Phase 8 Slice A design):** new function — the ranking concern is distinct from "give me the live facts," and isolating it keeps both simple. (Not a blocker.)
**Context:** Raised by the Phase 8 Slice A design (`docs/1_design/phase8_sliceA_classify_infra.md`, OQ-P8A-02). The new query would be `... WHERE status != 'retired' AND dimension = ? ORDER BY trust_score DESC, confidence DESC, updated_at DESC LIMIT ?`.

---

### OQ-P8A-03 · Catch-up scan — enqueue in one burst, or paged?
**Blocks:** Phase 8 Slice A (startup catch-up scan)
**Status:** 🔴 Open
**Question:** Right now there is no scan; Slice A introduces it. The question: on startup, `put` every discoverable id onto the queue at once, or page through them? **If one burst:** simplest; fine for a personal vault of hundreds of docs. **If paged:** safer for a huge backlog, but more code for a case that may never occur in the target (single-user) deployment.
**Recommendation (Phase 8 Slice A design):** one burst for Slice A, with a tech-debt note to page if backlog size becomes a problem — matches the grill's "watch vault size" tech-debt entry. (Not a blocker.)
**Context:** Raised by the Phase 8 Slice A design (`docs/1_design/phase8_sliceA_classify_infra.md`, OQ-P8A-03 + Risks "Sequential-consumer back-pressure"). See ADR-0017 (in-memory queue + catch-up scan).

### OQ-P8A-04 · Periodic synthesis "fact sheet" per dimension vs dynamic scattered-fact injection (retrieval path)
**Blocks:** Phase 9 (context injection) / Phase 10 — NOT Slice A or B
**Status:** 🔴 Open (deferred)
**Question:** For the RETRIEVAL path (user-facing AI answering "what do I know about X?"), should a periodic background session pre-synthesize a coherent per-dimension "fact sheet" (CLAUDE.md-style) instead of dynamically assembling ranked+capped scattered facts on each call? **Dynamic (grill #5, current):** always current, no extra LLM cost, keeps per-fact source/trust/status, but assembles every call. **Periodic sheet:** cheaper + more coherent at injection time, but stale between runs, extra synthesis LLM cost, flattens granularity.
**Decision (2026-06-14):** Keep grill #5 dynamic model. The EXTRACTION loop (Slice A/B Context Loader) must ALWAYS stay scattered structured facts — the extractor references entry `id` for update/retire (grill #9), which a prose sheet cannot carry. Synthesis only ever applies to the retrieval consumer, which does not exist until Phase 9. Revisit pre-computed sheets ONLY if measured token cost bites — already the logged P10 optimization ("dynamic → pre-computed", grill tech-debt + Phase 10 line 243). Structured `knowledge_entries` remain source of truth regardless; any sheet is a derived cache.
**Context:** Raised by user during Phase 8 Slice A design review. Confirms, does not change, Slice A scope.

### OQ-P8A-05 · Is `guidance` mandatory per dimension, and should missing `other`/`guidance` fail loudly at config load?
**Blocks:** Phase 8 Slice A (dimension loader/validator extension)
**Status:** 🔴 Open
**Question:** When the nested `dimensions.yaml` loads, should a dimension missing its `guidance` block — or missing the mandatory `other` tag — be rejected loudly at load time, or tolerated? **Leaning:** validate-on-load and reject (P8-CLS-A-04 expects malformed config to be rejected; matches the config-enforced philosophy in rearch §7 "validation rejects unknown values").
**Recommendation (Phase 8 Slice A spec):** fail loud on load. Confirm the exact validation surface (where in `core/tags.py` the check lives) in research.
**Context:** Raised by the Phase 8 Slice A spec (`docs/2_specs/phase8_sliceA_classify_infra.md`, open questions). Research resolves the validation surface.

---

### OQ-P8B-01 · Find facts by a document id contained in the JSON `sources` column
**Blocks:** Phase 8 Slice B Phase 9 (source-prune on document delete)
**Status:** 🟢 Resolved (2026-06-15) — recommendation locked, non-blocking
**Question:** A fact's source documents are stored as a single JSON list of id numbers inside the fact's row; there is no query that finds "every fact whose source list contains id 9". When a document is deleted, how do we locate the facts that name it as a source — scan every non-retired fact and filter in code, or use a SQLite JSON query?
**Resolution:** Scan-and-filter in Python. Zero dependency risk at single-user vault scale; swappable for a `json_each`/`LIKE` query later without behavior change. Research confirmed the deployed `python:3.12-slim` image ships SQLite JSON1, so a JSON query is *available* — but scan-and-filter is chosen for simplicity.
**Context:** Phase 8 Slice B design OQ-P8B-01; research 2026-06-15. See `docs/1_design/phase8/phase8_sliceB_extraction.md`.

---

### OQ-P8B-02 · Does a low-confidence update demote a previously-confident fact?
**Blocks:** Phase 8 Slice B Entry Writer
**Status:** 🟢 Resolved (2026-06-15) — recommendation locked, flag for observation
**Question:** A fact's confident/pending status is re-computed from its confidence on every write (via `confidence_to_status`), including updates. If a new, lower-confidence mention updates a previously-confident fact, should the fact drop back to pending?
**Resolution:** Re-gate on every write (the locked broad-grill behavior — confidence is a live signal). Flag for observation; if demotion thrashes in practice, add a "max confidence seen" rule as a later refinement.
**Context:** Phase 8 Slice B design OQ-P8B-02. See `docs/1_design/phase8/phase8_sliceB_extraction.md`.

---

### OQ-P8B-03 · Should the startup catch-up scan page its enqueues?
**Blocks:** Phase 8 Slice B startup scan (carries OQ-P8A-03)
**Status:** 🟢 Resolved for Slice B (2026-06-15) — one burst + tech-debt note
**Question:** The catch-up scan puts every unclassified document id onto the queue in one burst at startup. In Slice A that was free; in Slice B each item costs a paid AI call. Should the scan enqueue in pages/batches instead?
**Resolution:** Keep one burst for Slice B. The sequential consumer drains one at a time, so token spend is naturally rate-limited; paging is a later optimization (tech-debt note, carried from OQ-P8A-03). Not a blocker.
**Context:** Phase 8 Slice B design OQ-P8B-03; carries Phase 8 Slice A OQ-P8A-03. See `docs/1_design/phase8/phase8_sliceB_extraction.md`.

---

## ✅ Closed

### OQ-001 · Move/rename detection when note is edited AND moved simultaneously
**Blocks:** Phase 1 vault indexer
**Status:** ✅ Closed (2026-06-04) — Phase 1 shipped with `content_hash` approach; edge case accepted as tolerable. Spurious orphan detection on rename+edit is handled by reconcile Stage 1 (sync paths). Revisit only if real-world false positives warrant `doc_id` in frontmatter.
**Question:** `content_hash`-based move detection fails when a note is edited and moved simultaneously (hash changes; indexer sees delete + insert rather than rename). Is `content_hash` sufficient for Phase 1, or does Phase 1 need to write `doc_id` to frontmatter?
**Context:** DECISION-001 chose integer PK + `content_hash` over frontmatter `doc_id` for simplicity. Edge case: simultaneous edit + move produces a new hash, which the indexer cannot match to the old record. The cost of writing `doc_id` to frontmatter is a vault write on first capture; the cost of not doing it is spurious orphan detection on rename+edit combos.

---

### OQ-006 · Obsidian wikilink path shape inside .summaries/<x>.md pointing at binary
**Blocks:** Brief #2
**Status:** ✅ Closed (2026-06-04) — Implemented in Brief #2. Sibling body uses `[[<binary.name>]]` (bare filename wikilink). Obsidian resolves to nearest match within vault. See `phase1_capture/_OVERVIEW.md` line 238.
**Question:** What wikilink format should sibling `.md` files use to point at the binary? `[[report.pdf]]` is vault-wide and ambiguous when two projects both have `report.pdf`. `[[Projects/A/attachment/report.pdf]]` (full path) or `[[../report.pdf]]` (relative) is unambiguous but must be verified against Obsidian's resolver on a real vault.
**Context:** Cannot verify live in research session. Brief #2 capture pipeline must decide and test on a real vault before shipping. Full vault-relative path is the safest option but Obsidian's behavior with leading `/` paths vs bare paths needs confirmation.
