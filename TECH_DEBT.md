# Tech Debt

## Active

### TD-039 · Windows support for binary content-change detection + atomic-save handling
**Status:** OPEN
**Phase:** Vault-restructure (post-Mac-ship)
**Risk if triggered early:** Designing Windows handling before the macOS change-detection layer exists wastes effort; the abstraction must settle on Mac first.
**What:** Content-change detection (draft Task T9) is Mac-first by decision. Windows needs its own work before any Windows user: (1) a real-vault event probe for Windows Office atomic-save sequences (differs from macOS — `~WRDxxxx.tmp` rename dance), (2) a read-when-unlocked retry path (Windows holds an exclusive lock on open Office files, so the file can't be hashed until released), (3) Windows temp-file ignore patterns (`~WRD*.tmp`, `~$*`). `watchdog` unifies the API but NOT the event sequences or locking semantics.
**Why deferred:** Target user confirmed Mac-first for the June demo. No Windows user until later.
**Source:** Grilling session 2026-06-03; `docs/draft/vault-restructure-editable-noedit-split.md` (T9, TD-W1 placeholder).

---

### TD-004 · embeddings table + FTS5 virtual table
**Status:** OPEN
**Phase:** Phase 3
**Risk if triggered early:** No consumer for embeddings exists until retrieval layer; premature schema addition adds migration complexity with no return.
**What:** `embeddings` table and FTS5 virtual table are not yet created; retrieval layer in Phase 3 will require both.
**Why deferred:** No consumer until retrieval layer exists.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-005 · corrections table enrichment with classifier-specific fields
**Status:** OPEN
**Phase:** Phase 7
**Risk if triggered early:** Self-learning schema requires classifier design to be finalized; adding fields before Phase 7 design creates schema churn.
**What:** Placeholder `corrections` table exists; fields for self-learning classifier (feedback type, correction delta, confidence) not yet added.
**Why deferred:** Fields added when self-learning is built in Phase 7.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-006 · Per-section AI vs human authorship tracking
**Status:** OPEN
**Phase:** Phase 7+
**Risk if triggered early:** Hard problem; extending `updated_by_human` or adding `edits` table without the full design risks schema lock-in.
**What:** `updated_by_human` is a whole-note boolean blunt gate. Per-section authorship (e.g. "AI wrote this summary, human wrote this conclusion") requires a separate design — HTML comments or an `edits` table.
**Why deferred:** Fine-grained tracking is explicitly out of scope until Phase 7+. See Open Question Q-002.
**Source:** Open Question Q-002, `plans/storage_level.md`

---

### TD-007 · Daemon-mode WAL checkpoint tuning (wal_autocheckpoint)
**Status:** OPEN
**Phase:** Phase 4
**Risk if triggered early:** WAL checkpoint tuning only matters in long-running daemon; CLI closes cleanly and WAL truncates on exit.
**What:** Reference project sets `wal_autocheckpoint=100`; SQLite default is 1000 pages. Worth revisiting before Phase 4 MCP daemon.
**Why deferred:** CLI exits cleanly; WAL truncates on close. Irrelevant until MCP daemon.
**Source:** Out of Scope + Open Question Q-003, `plans/storage_level.md`

---

### TD-008 · documents columns: project, status, key_topics
**Status:** OPEN
**Phase:** Phase 2+
**Risk if triggered early:** Adding columns before pipelines that populate them creates nullable dead columns with no backfill path.
**What:** `documents` table lacks `project`, `status`, `key_topics` columns. These will be added via migrations when classify/search pipelines need them.
**Why deferred:** Add via migrations when pipelines demand them; not pre-emptively.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-009 · updated_by_human sync between frontmatter and SQLite
**Status:** OPEN
**Phase:** Phase 1 (post-delivery audit)
**Risk if triggered early:** Sync logic depends on watcher being able to detect human edits reliably; premature sync without watcher risks false-positive locks.
**What:** `updated_by_human` exists in both frontmatter and `documents` table. The sync logic (frontmatter → SQLite on human edit detection) lives in `vault/writer.py` and watcher; edge cases around simultaneous human+AI writes are not fully exercised.
**Why deferred:** SQLite mirror exists for cheap queries; sync logic is a `vault/writer.py` concern deferred for post-capture audit.
**Source:** `research/storage_level.md` edge cases

---

### TD-010 · Ollama httpx async rewrite
**Status:** OPEN
**Phase:** Phase 3+
**Risk if triggered early:** `asyncio.to_thread(requests.post)` is sufficient for Phase 0/1/2; rewriting before Ollama becomes a performance bottleneck is speculative work.
**What:** `OllamaProvider` uses `asyncio.to_thread(requests.post)` — a sync HTTP call wrapped in a thread. A native `httpx` async rewrite would eliminate thread overhead.
**Why deferred:** Only worth revisiting if Ollama becomes performance-critical in Phase 3+.
**Source:** Out of Scope, `plans/llm_layer.md`

---

### TD-011 · Per-prompt model and temperature overrides
**Status:** OPEN
**Phase:** Phase 1+
**Risk if triggered early:** Extending `LLMProvider.complete()` signature requires updating all three providers and all call sites simultaneously.
**What:** `Prompt` dataclass has no `model` or `temperature` fields; removed as dead weight in Phase 0. Per-prompt overrides require extending `LLMProvider.complete()` signature.
**Why deferred:** No caller needs per-prompt overrides yet; extend signature when needed.
**Source:** DECISION-016 + review finding #3

---

### TD-012 · cli/main.py stubs: classify, search, briefing commands
**Status:** OPEN
**Phase:** Phase 2+
**Risk if triggered early:** Stubs with no backing pipeline are lies; add commands only when pipeline exists and is tested (C-15).
**What:** `capture`, `capture --scan`, and `watch` delivered in Phase 1. `classify`, `search`, and `briefing` CLI commands are still stubs.
**Why deferred:** Backing pipelines not yet built.
**Source:** `cli/main.py` created as dotenv owner (DECISION-014)

---

### TD-013 · embedding_model field stored but not yet routed
**Status:** OPEN
**Phase:** Phase 3
**Risk if triggered early:** `sentence-transformers` wiring belongs in Phase 3 retrieval; adding it earlier creates unused infrastructure.
**What:** `_embedding_model` field exists on all three provider configs for single-provider portability. Phase 3 retrieval will wire it to `sentence-transformers` or provider embedding endpoint.
**Why deferred:** No retrieval consumer yet.
**Source:** DECISION-015; Out of Scope, `plans/llm_layer.md`

---

### TD-015 · CLAUDE.md section-merge for AI co-authoring
**Status:** OPEN
**Phase:** Phase 12+
**Risk if triggered early:** Section-merge requires understanding wikilink + section structure; implementing before Phase 11 watcher makes the merge logic untestable.
**What:** Watcher delivered (Phase 11, 2026-05-21). Section-merge for `CLAUDE.md` AI co-authoring is still deferred. Interim rule: AI writes `CLAUDE.md` with `actor="ai"`; `updated_by_human` stays `False`; human context edits can be overwritten by AI index writes until section-merge lands.
**Why deferred:** Hard merge problem; watcher is prerequisite.
**Source:** `plans/vault_layer.md` OQ-V8; review session 2026-05-18

---

### TD-016 · User explicit URL flagging in enrich_urls
**Status:** OPEN
**Phase:** Phase 1+ (post-watcher)
**Risk if triggered early:** Requires extending `RawContent` or passing note metadata separately to `enrich_urls`; premature extension before the rest of enrich_urls stabilizes risks rework.
**What:** User marks URLs as crucial via frontmatter `fetch_urls: [url1, url2]` or inline `#fetch` tag. These bypass the structural gate and are always fetched. Implementation: `enrich_urls` reads `fetch_urls` from `RawContent` frontmatter and merges with gate-selected URLs. Isolated in `_build_gate` extension point.
**Why deferred:** Wishlist item; no user demand yet.
**Source:** `docs/research/capture_pipeline.md` Wishlist A

---

### TD-017 · AI URL triage replacing structural heuristic gate
**Status:** OPEN
**Phase:** Phase 2+
**Risk if triggered early:** Adds LLM latency to every capture with URLs; requires new prompt YAML and test coverage before deploy.
**What:** Before fetching, LLM classifies each URL as `primary | citation | skip` using `prompts/url_triage.yaml`. Only `primary` URLs are fetched. Replaces `_should_enrich` structural heuristic with `_ai_triage_urls()` inside `_build_gate`.
**Why deferred:** Adds latency; structural heuristic sufficient for Phase 1.
**Source:** `docs/research/capture_pipeline.md` Wishlist B

---

### TD-018 · Domain list refresh in kms watch
**Status:** OPEN
**Phase:** Post-Phase 11
**Risk if triggered early:** Dynamic refresh requires a file-system watcher on `Domain/` itself, adding complexity to the watcher startup sequence.
**What:** Taxonomy loaded once at watcher startup; new `Domain/` folders added while watcher runs are invisible until restart.
**Why deferred:** Acceptable for Phase 11; dynamic refresh deferred.
**Source:** OQ-C6, `plans/capture_pipeline.md`

---

### TD-019 · Tag taxonomy enforcement in classify pipeline
**Status:** OPEN
**Phase:** Phase 2 (Roadmap)
**Risk if triggered early:** `validate_tags` is shared infrastructure; wiring it into classify before the pipeline exists creates a stub dependency.
**What:** `core/tags.py::validate_tags` is wired in the capture pipeline. Phase 2 classify pipeline must wire it in too.
**Why deferred:** Not in capture plan scope; classify is Phase 2.
**Source:** Out of Scope, `plans/capture_pipeline.md`

---

### TD-020 · docs/research/capture_pipeline.md §"Non-md branch" documents OLD attachment layout
**Status:** OPEN
**Phase:** Brief #2 post-ship
**Risk if triggered early:** Annotating docs before Brief #2 ships risks annotating against a design that still changes.
**What:** `docs/research/capture_pipeline.md` §"Non-md branch" describes the old layout: sibling next to source, global `attachment_path`, bare wikilink. Superseded by `revise_attachment_layout.md`. Needs annotation: "→ see revise_attachment_layout.md for new layout".
**Why deferred:** Deferred until Brief #2 shipped the new behavior.
**Source:** `docs/research/revise_attachment_layout.md` TD-RAL-1

---

### TD-021 · docs/roadmap.md Phase 1 describes OLD attachment layout
**Status:** OPEN
**Phase:** Brief #2 post-ship
**Risk if triggered early:** Same as TD-020 — annotating before Brief #2 ships risks rework.
**What:** `docs/roadmap.md` Phase 1 (lines 53–66) describes global `Vault/attachment/` layout in detail. Needs annotation or rewrite.
**Why deferred:** Documentation pass deferred until Brief #2 shipped.
**Source:** `docs/research/revise_attachment_layout.md` TD-RAL-2

---

### TD-029 · Rename gate logic mis-calibrated
**Status:** OPEN
**Phase:** Phase 2 pre-req
**Risk if triggered early:** Auto-rename without confidence scoring risks renaming files the user intentionally named; fix requires research first.
**What:** `_should_rename_md` fires when title looks generic but skips when title is descriptive-yet-stale. Needs survey of how other KMS tools (Logseq, Reflect, mem.ai) handle filename vs heading drift. Likely fix: confidence-scored rename suggestion + human-review queue rather than auto-rename.
**Why deferred:** Requires research into competitor approaches before calibration.
**Source:** STATE.md "Re-make work" §3

---

### TD-031 · move_attachment TOCTOU window
**Status:** OPEN
**Phase:** Watcher hardening pass
**Risk if triggered early:** Only a real risk in concurrent watcher mode; single-process CLI is safe. Fix requires testing EXDEV cross-filesystem fallback for `os.link`.
**What:** `vault/writer.py::move_attachment` has a TOCTOU window between `dst.exists()` check and `os.replace(src, dst)`. Fix: replace with `os.link(src, dst)` (atomic, raises `FileExistsError` if dst exists) + `src.unlink()`. Keep EXDEV cross-filesystem fallback.
**Why deferred:** Acceptable in single-process CLI; risk is in watcher mode with concurrent drops + scan.
**Source:** Code review 2026-05-24 (issue #8)

---

### TD-032 · No kms migrate-attachments command for legacy vault layout
**Status:** OPEN
**Phase:** Post-Phase 2 (only if needed)
**Risk if triggered early:** No production users with legacy layout; building migration before anyone needs it is waste.
**What:** No migration path from pre-Brief-#1 layout (global `Vault/attachment/` + `Vault/Archive/`) to current per-project/per-domain layout. If/when needed: `kms migrate-attachments` one-shot that walks legacy folders, infers project/domain (via frontmatter or prompt), moves binary + sibling, updates DB `vault_path` rows.
**Why deferred:** Greenfield — no production users with legacy layout.
**Source:** Code review 2026-05-24 (issue #9)

---

### TD-033 · vault.watcher monkeypatching target documentation
**Status:** OPEN
**Phase:** Documentation-only
**Risk if triggered early:** N/A — documentation-only entry. No code change needed; the risk is re-introducing the bug in future tests.
**What:** `vault/watcher.py` hoists all collaborators as top-level imports. Tests MUST patch `vault.watcher.<name>` (e.g. `vault.watcher.move_note`), NOT the source module (`vault.writer.move_note`). Patching the source module leaves `vault.watcher`'s local binding pointing at the original. All existing watcher tests updated 2026-05-24.
**Why deferred:** Documentation-only; code is already correct. Tracked to prevent future test authors from re-introducing the bug.
**Source:** Code review 2026-05-24 (issue #10/#11 follow-on)

---

### TD-035 · Reconcile: location-tag mismatch when human has overridden domain/project
**Status:** OPEN
**Phase:** Phase 1.5 reconcile (deferred)
**Risk if triggered early:** Silent inconsistency. A file can sit in `Projects/Alpha/` with `project: Beta` set by human and the system will never surface the conflict.
**What:** When location-confidence tagging runs, if `updated_by_human: true` guards a `project:` or `domain/X` tag, the pipeline skips the override (human wins). But the mismatch — file location contradicts human-set tags — is never surfaced. The reconcile pipeline should detect this case and either alert the user or offer to override.
**Why deferred:** Reconcile alert/override logic is a separate concern from the capture-time tagging decision. Build capture behavior first, then extend reconcile.
**Unblock condition:** Extend reconcile pipeline to compare file location against `project:` / domain tags when `updated_by_human: true`. Emit a mismatch audit entry or user-visible alert.
**Source:** Phase 1.5 domain/project tagging design session 2026-06-01

---

### TD-036 · Reconcile: stale batch_id on documents after file moves
**Status:** OPEN
**Phase:** Phase 1.5 (folder handling) / post-MVP
**Risk if triggered early:** Low. `batch_id` is observability metadata; stale refs don't affect routing. Phase 8 Briefing would group a moved file under its original batch, which is misleading but not harmful.
**What:** When a file captured as part of a folder batch is later moved out of the batch destination (e.g. from `Projects/A/` to `Projects/B/`), the `documents.batch_id` FK is preserved by `rename_doc` (which only updates `vault_path`). The file now has a stale batch association pointing to its original folder group. Add a reconcile stage (`reconcile_stale_batch_refs`) that: (1) JOINs `documents` with `batches` on `batch_id`, (2) checks if `documents.vault_path` still falls under `batches.destination_type`/`destination_name` prefix, (3) nulls out `batch_id` on rows where location no longer matches, (4) optionally sets `batches.status = STALE` when all member docs have drifted.
**Why deferred:** `batches` table and `batch_id` column not yet implemented. Add this stage when folder-handling spec ships. Eventual consistency (stale for minutes between reconcile runs) is acceptable for `batch_id`.
**Unblock condition:** `batches` table and `documents.batch_id` column exist. Then add Stage 6 to `reconcile()` in `pipelines/reconcile.py` and add `batch_refs_cleared: int = 0` to `ReconcileResult`.
**Source:** Phase 1.5 folder handling design session 2026-06-02

---

### TD-034 · Project-to-domain mapping registry
**Status:** OPEN
**Phase:** Phase 1.5 / deferred
**Risk if triggered early:** No domain→project mapping exists in code. Active projects live flat under `Projects/<A>/` with no domain in path. Any feature that needs "which domain does project A belong to" will silently fall back to `Uncategorized` until this is resolved.
**What:** When a file lands under `Projects/<A>/`, the capture pipeline needs to set a `domain/<D>` tag. Currently no registry stores which domain an active project belongs to. Interim rule: domain tag is set to `domain/Uncategorized` for project-scoped files.
**Why deferred:** Two clean options exist (a `projects` DB table or `Projects/<A>/meta.yaml`), but picking one requires design work. `Projects/<A>/CLAUDE.md` was rejected — it's prose, not structured, and TD-015 means AI can overwrite human edits there.
**Unblock condition:** Design and implement a project registry (DB table preferred) that stores `project_name → domain_name`. Update location-confidence tagging to do a registry lookup instead of defaulting to `Uncategorized`.
**Source:** Phase 1.5 domain/project tagging design session 2026-06-01

---

### TD-037 · Binary modify never re-captures (formalizes the `TD-C6` code marker)
**Status:** OPEN
**Phase:** Watcher hardening pass (own phase, after Phase 1.5 fix batch)
**Risk if triggered early:** Stale knowledge. Office files (`.xlsx`, `.docx`, `.pptx`) get edited frequently; their summary siblings under `attachment/.summaries/` never regenerate, so briefings surface outdated content with no signal that it drifted.
**What:** A binary modified in `Projects/<A>/attachment/` or `Domain/<D>/attachment/` never re-runs capture. `vault/watcher.py::on_modified` (line 229-238) double-blocks it: (1) `_should_skip(path)` drops managed-attachment binaries first, then (2) an explicit `if suffix != ".md": return` guard (the `# Binary modify deferred — TD-C6` comment). The downstream machinery already works — `_store_nonmd` LOCATED path recomputes `source_hash` + overwrites the sibling, and Phase 6 idempotency means unchanged content → `SKIPPED`, changed content → re-run. Only the watcher trigger is missing.
**Why deferred:** Net-new behavior (binary modify never worked), not a regression from the pay_debt plan. Lives in the watcher, which is concurrency-sensitive and already receiving the C2 timer-race fix — stacking a second event-path change raises risk. Needs its own test surface: Office saves emit a *burst* of modify events (debounce must coalesce), re-capture idempotency, and no modify→write→modify loop.
**Unblock condition:** Reorder `on_modified` so binary handling runs before `_should_skip` (mirror the `on_deleted`/`on_moved` pattern from Brief #3), add `_handle_binary_modify` → debounce → `capture_file(binary)`. Sibling path is deterministic via `_sibling_for` — no real "reverse lookup" needed. Add coalescing + loop-safety tests.
**Source:** Code review 2026-06-03 (M3); formalizes the `TD-C6` marker at `vault/watcher.py:236`

---

### TD-038 · Drop redundant scalar `domain:` frontmatter field
**Status:** OPEN
**Phase:** Phase 2 (Classify) pre-req or dedicated cleanup phase
**Risk if triggered early:** Drift. Domain is stored in two places — the scalar `domain:` property (`NoteMetadata.domain`, written by `store()` from `mr.ai_domain`) and the `domain/<D>` tag in the unified `tags:` list (written by `apply_location_tags`). `apply_location_tags` syncs both at capture time, but `reconcile_stale_tags` (Stage 5) only touches `tags` + `project` — it does NOT re-sync the scalar `domain:`. So after a domain folder is renamed/removed, the tag can be corrected while the scalar silently keeps a stale value.
**What:** Per Obsidian convention there is one canonical tag field (`tags:`). The scalar `domain:` field is redundant with the `domain/<D>` tag. Decision (user, 2026-06-03): drop the scalar `domain:` entirely; domain lives only as a `domain/<D>` tag in `tags:`.
**Why deferred:** Multi-file refactor: remove `domain` from `NoteMetadata` (`frontmatter.py`) + `_KNOWN_KEYS` + `field_validator`; remove the `domain=mr.ai_domain` kwarg from `store()`; add `_DEPRECATED_KEYS` filter in `dumps()` for lazy migration of existing notes; migration/cleanup pass for existing notes carrying `domain:`. Out of scope for the Phase 1.5 fix batch.

Note: `MetadataResult.ai_domain` is **kept** as internal pipeline state — `apply_location_tags` uses it to append the `domain/<D>` tag. Only the frontmatter scalar `NoteMetadata.domain` is dropped. (Resolved in design: `docs/design/phase_pre_2/td_038_drop_domain_scalar.md`)
**Unblock condition:** Confirm no consumer (Phase 2 Classify, search, briefing) depends on the scalar `domain:`; then remove the field, update the metadata stage to emit only the `domain/<D>` tag, and add a one-shot pass to strip `domain:` from existing frontmatter.
**Source:** Code review 2026-06-03 (I3); user decision to override AI domain via location tag

---

## Archive

### TD-001 · core/pipeline.py
**Status:** RESOLVED
**Phase:** Phase 0 ✅ (delivered 2026-05-14)
**Risk if triggered early:** N/A
**What:** `core/pipeline.py` was deferred from the storage plan scope.
**Why deferred:** Out of scope for storage_level plan; delivered in Phase 0.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-002 · llm/prompt_loader.py and prompts/
**Status:** RESOLVED
**Phase:** Phase 0 ✅ (delivered 2026-05-14)
**Risk if triggered early:** N/A
**What:** `llm/prompt_loader.py` and `prompts/` (empty scaffold) were deferred from the storage plan scope.
**Why deferred:** Out of scope for storage_level plan; delivered in Phase 0.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-003 · vault/ (paths, frontmatter, reader, writer)
**Status:** RESOLVED
**Phase:** Phase 0 ✅ (delivered 2026-05-20 — confirmed in STATE.md Phase 0 checklist)
**Risk if triggered early:** N/A
**What:** `vault/` module (paths.py, frontmatter.py, reader.py, writer.py) was deferred from the storage plan scope.
**Why deferred:** Outside storage scope; built in Phase 0.
**Source:** Out of Scope, `plans/storage_level.md`

---

### TD-014 · write_note field clearing — Option B merge removed
**Status:** RESOLVED (2026-05-20)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** Removed Option B merge from `vault/writer.py`. `write_note` now writes exactly what the caller passes. Pipelines must `read_note` first if they want to preserve existing fields. Only `created` is preserved automatically. See C-03.
**Why deferred:** Was a design ambiguity; resolved with explicit pure-writer contract.
**Source:** `plans/vault_layer.md` Phase 4 Option B note

---

### TD-022 · capture.py / cli/main.py .root/.attachment_dir workaround callers
**Status:** RESOLVED (Brief #3 Phase 1, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** Three callers in `capture.py:456`, `capture.py:627`, `cli/main.py:127` used `.root / .attachment_dir` with `# COUPLING:` comments as temporary workaround. All three retired.
**Why deferred:** Awaited Brief #2/#3 to replace with `project_attachment(name)` / `domain_attachment(name)` helpers.
**Source:** `docs/plans/revise_attachment_layout.md` Phase 1 Notes

---

### TD-023 · vault/watcher.py single attachment_path constructor arg
**Status:** RESOLVED (Brief #3 Phase 1, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** Watcher took a single `attachment_path: Path` arg to skip events. Replaced with `vault_config: VaultConfig` and `_is_in_managed_attachment` for per-project path check.
**Why deferred:** Awaited Brief #3 Phase 1.
**Source:** `docs/plans/attachment_sync_and_archive.md` Phase 1

---

### TD-024 · vault/indexer.py::scan_non_md_drops single-path skip
**Status:** RESOLVED (Brief #2 Phase 4, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** `scan_non_md_drops(root, attachment_path: Path)` used a single-path skip. Replaced with `vault_config` signature using `_is_in_managed_attachment` + `_has_inbox_sibling` rules.
**Why deferred:** Awaited Brief #2/#3 design finalization.
**Source:** `docs/plans/revise_attachment_layout.md` Phase 3 Notes

---

### TD-025 · tests/test_vault/test_indexer.py old scan_non_md_drops signature
**Status:** RESOLVED (Brief #2 Phase 4, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** 6 old tests used `scan_non_md_drops(root, attachment_path: Path)` signature. All updated to new signature + 4 new tests added.
**Why deferred:** Awaited TD-024 resolution.
**Source:** `docs/plans/attachment_capture_pipeline.md` Phase 4 Steps

---

### TD-026 · Orphan reconciliation — binary with no sibling .md
**Status:** RESOLVED (Brief #3 Phase 4, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** No reconciliation pass for binaries in `attachment/` with no corresponding sibling `.md`. Resolved by `reconcile` Stage 2 (captures binaries with no sibling) and Stage 4 (walks `.summaries/`, unlinks ghosts, removes DB rows).
**Why deferred:** Awaited Brief #3 Phase 4.
**Source:** `docs/plans/attachment_sync_and_archive.md` Phase 4

---

### TD-027 · prompts/summarize_attachment.yaml missing
**Status:** RESOLVED (Brief #2 Phase 2, 2026-05-24)
**Phase:** ✅ Brief #2 Phase 2
**Risk if triggered early:** N/A
**What:** `prompts/summarize_attachment.yaml` was missing; needed for non-md binary capture.
**Why deferred:** Awaited Brief #2 Phase 2.
**Source:** `docs/plans/attachment_capture_pipeline.md` TD-C8

---

### TD-028 · ClaudeCliProvider metadata JSON parse fails on short DOCX extracts
**Status:** RESOLVED (2026-05-25)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** Two-layer fix: (1) `_parse_metadata_json` now catches `JSONDecodeError` and returns `Success({"title": source_stem, "tags": []})` instead of hard `Failure`; (2) `prompts/extract_metadata.yaml` system prompt updated with explicit thin-content instruction — LLM told to return minimal JSON rather than refusing. 4 tests updated/added.
**Why deferred:** Deferred as TD from Brief #3; fixed 2026-05-25.
**Source:** STATE.md "Re-make work" §1

---

### TD-030 · Critical — vault/watcher.py::on_deleted binary sync ran after _should_skip
**Status:** RESOLVED (Brief #4, 2026-05-24)
**Phase:** ✅ Resolved
**Risk if triggered early:** N/A
**What:** `on_deleted` called `_should_skip(path)` before binary-sync dispatch, silently skipping sibling cleanup for managed-attachment deletes. Reordered: `_handle_binary_delete` now runs before `_should_skip` filter. Regression test added.
**Why deferred:** Found in code review 2026-05-24; fixed same day.
**Source:** Code review 2026-05-24 (Critical #1)
