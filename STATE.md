# STATE.md — Cross-Session Project State
_Created: 2026-05-09_
_Last updated: 2026-06-16 (Phase 10 Self-Learning & Reports — build-pipeline COMPLETE: design→spec→research→plan) · 2026-06-15 (Phase 8 Issue Resolution COMPLETE) · 2026-06-15 (Phase 8 Slice B COMPLETE) · 2026-06-14 (Phase 8 Slice A COMPLETE)_

## Current Position
**Phase**: Phase 9 (MCP Adaptation) — **DONE** (implemented, merged to cloud-native). Phase 10 (Self-Learning & Reports Backend) — **PLAN READY** (2026-06-16). Full build-pipeline complete: grill→design→spec→research→plan. 10 implementation phases, 20 behavior IDs (P10-SL-01 through P10-SL-20), 3 new tables (fact_corrections, reports, entry_comments), 2 new MCP tools (kms_comment, kms_reports), 4 new files (trust.py, few_shot.py, _comment.py, reports.py). Research: 7 validated, 2 invalidated (A5 retire mechanism, A6 method name), 1 critical gap (upsert missing trust_score in SQL). All corrections incorporated into plan. **Next: implement Phase 10.** (Prior: Phase 8 ALL COMPLETE. Phase 6 Daemon Slice A1+A2 COMPLETE; Slice B plan-written. Phase 7A+7B Capture Refactor COMPLETE. Phase 5 Slice 1+2 COMPLETE.)

**[Phase 10 — Self-Learning & Reports Backend — PLAN READY 2026-06-16]** _(build-pipeline: grill→design→spec→research→plan complete)_:
- [x] Grill — `docs/0_draft/phase10/phase10_self_learning_grill.md`
- [x] Design — `docs/1_design/phase10_self_learning.md` (567 lines, 5 design decisions, ADR-0021)
- [x] Spec — `docs/2_specs/phase10_self_learning.md` (579 lines, 16 components across 2 slices, all 20 behaviors mapped, 9 assumptions)
- [x] Research — `docs/3_research/phase10_self_learning.md` (7 validated, 2 invalidated, 1 critical gap)
- [x] Plan — `docs/4_plans/phase10_self_learning.md` (1550 lines, 10 phases)
- **Key decisions:** separate kms_comment tool (not kms_correct op); few-shot cap=5 hardcoded initially; structural conflict detection (not semantic); trust-floor filter in `query_ranked_for_orientation()` not context.py; upsert trust_score fix as Phase 1 prerequisite
- **Research corrections applied:** A5 (retire helper call not inline SQL), A6 (method name + SQL location), SR-1 (upsert missing trust_score), _should_overwrite pass-body gap
- **New files planned:** `src/pipelines/trust.py`, `src/pipelines/few_shot.py`, `src/mcp_server/_comment.py`, `src/pipelines/reports.py`, `src/config/reports.yaml`, `src/storage/migrations/013_self_learning_tables.sql`
- **New MCP tools:** `kms_comment`, `kms_reports`
- [ ] Implementation — 10 phases pending

**[Phase 8 Issue Resolution — ✅ COMPLETE 2026-06-15]** _(20 issues from nuclear review resolved across 4 phases)_:
- [x] Phase 1 — Quick wins: C4 (nested-dict config default), M10 (log format), L1-L4 (docstrings), M9 (constant rename)
- [x] Phase 2 — DRY helper extraction: C3+M1+M2+M3 (`_merge_sources`, `_compute_status`, `_find_twin`), H2 (`_fail_and_record`), H4+H5 (`_handle_stamp`, `_handle_retry`), M4+M5 (kwarg simplification), H3 (separate stamp vs retry-record), M8 (WriteSummary dataclass)
- [x] Phase 3 — File decomposition H1: `classify.py` 984→222 lines, split into `classify.py` (public API), `classify_extract.py` (extraction), `classify_writer.py` (entry writing), `classify_orchestrator.py` (orchestration+retry)
- [x] Phase 4 — File decomposition H6: `documents_classify.py` extracted from `documents.py` (6 functions, 194 lines: `find_unclassified`, `stamp_classified`, `record_classify_failure`, `clear_classify_retry_state`, `park_document`, `load_classify_retry_state`)
- **Deferred to tech debt (Phase 9 intake):** TD-P9-PERF-01 (dimensions loaded twice per doc), TD-P9-PERF-02 (context_loader re-queries DB per doc), TD-P9-PERF-03 (prune_sources O(N) memory), TD-P9-CLEAN-01 (redundant dedupe), TD-P9-CLEAN-02 (lifecycle comments)
- **Artifact:** `docs/0_draft/phase8_issue_diagnosis.md` (issue inventory + resolution plan)

**[Phase 8 Slice B — Classify Extraction (LLM) — ✅ COMPLETE 2026-06-15]** _(~216 new tests)_:
- [x] Phase 0 — DeepSeek config pre-flight
- [x] Phase 1 — Migration 011 (`documents.classify_attempts` INTEGER + `classify_last_error` TEXT) + version-pin cascade
- [x] Phase 2 — config `classify.max_retries` (K)
- [x] Phase 3 — `prompts/entity_extract.yaml`
- [x] Phase 4 — `find_unclassified` gains `status != 'needs-review'` filter + retry-state helpers
- [x] Phase 5 — Entity Extractor (per-dimension AI call, JSON parse, validation)
- [x] Phase 6 — Entry Writer (new/update/retire routing, source-merge, exact-entity dedup, status re-gate)
- [x] Phase 7 — Orchestrator (per-doc correlation id, per-dimension audit, stamp on all-clean, park at max_retries)
- [x] Phase 8 — Live-enqueue seam (`app.state.classify_queue`, upload handler `put_nowait`)
- [x] Phase 9 — Source-prune on delete (`prune_sources()`, empty→pending)
- [x] Phase 10 — Delete old folder-routing classify code
- **Research:** 16 validated / 0 invalidated / 0 unverifiable (no loop-back).
- **Decisions:** A1 queue on `app.state`; B1 retry state on `documents` columns + park=`needs-review`; reuse `classify` LLM task re-pointed at DeepSeek; orchestrator/queue/worker stay in `pipelines/classify.py`. ADR-0018 (bounded self-correcting retry) + ADR-0019 (live-enqueue seam + exact-entity write-time dedup).
- **Artifacts:** design `docs/1_design/phase8/phase8_sliceB_extraction.md` · spec `docs/2_specs/phase8/phase8_sliceB_extraction.md` · research `docs/3_research/phase8/phase8_sliceB_extraction.md` · plan `docs/4_plans/phase8/phase8_sliceB_extraction.md` · grill `docs/0_draft/phase8/phase8_sliceB_extraction_grill.md`
- **Tech-debt raised:** TD-066 cross-doc batching, TD-067 prompt caching, TD-068 cross-dimension context, TD-069 context_loader per-doc re-query, TD-070 hardcoded dimensions.yaml path.

**[Phase 8 Slice A — Classify Infrastructure (no LLM) — ✅ COMPLETE 2026-06-14]** _(9 commits, merged to cloud-native)_:
- [x] Phase 1 — Migration 010 (`documents.classify_content_hash`; `knowledge_entries.trust_score` DEFAULT 0.5, `retrieval_count` DEFAULT 0; 2 indexes) + version-pin cascade 9→10 (`test_migration_007/008/009`) [P8-CLS-A-01]
- [x] Phase 2 — Nested `dimensions.yaml` `{tags, guidance}` + `core/tags.py` loader/validator (`rulebook[dim]["tags"]`) + `test_dimensions.py` (P5-DATA-07/08) cascade + loud-reject malformed [P8-CLS-A-04]
- [x] Phase 3 — `ClassifyConfig` sub-model (`max_content_tokens`=10000, `max_entries_per_dimension`=50) in `core/config.py` + `classify:` block in `config.yaml` [P8-CLS-A-05/06]
- [x] Phase 4 — `KnowledgeEntry`+`_row_to_entry` gain `trust_score`/`retrieval_count`; NEW ranked+capped query (not extending `get_confident_and_pending`) [P8-CLS-A-03]
- [x] Phase 5 — Work-discovery query + classify-stamp function in `storage/documents.py` [P8-CLS-A-01/07]
- [x] Phase 6 — Content Reader (full_body vs summary by token threshold) + Context Loader in `pipelines/classify.py` [P8-CLS-A-02/03]
- [x] Phase 7 — `asyncio.Queue` + single sequential consumer (skeleton, stops before Slice B AI call) + one-burst startup catch-up scan, started via composed outer lifespan in `mcp_server/cloud_entry.py::build_app` [P8-CLS-A-07]
- **Decisions:** in-memory queue + `classify_content_hash` work discovery (ADR-0017); reuse Phase 5 Slice 1 `core/tags.py` loader (extend, not duplicate); worker via composed outer lifespan, NOT `on_startup` (proven no-op) nor per-chat MCP lifespan; thresholds as config ints; Slice B (LLM extraction, entry writer, audit, capture-push wiring, source cleanup) OUT OF SCOPE.
- **Research:** 11/11 assumptions validated; 1 invalidation found+resolved via loop-back (A7 worker-start mechanism). 0 blocking.
- **Artifacts:** grill `docs/0_draft/phase8/phase8_classify_redesign_grill.md` · design `docs/1_design/phase8_sliceA_classify_infra.md` · spec `docs/2_specs/phase8_sliceA_classify_infra.md` · research `docs/3_research/phase8_sliceA_classify_infra.md` · plan `docs/4_plans/phase8_sliceA_classify_infra.md` · ADR-0017
- **Open (non-blocking):** OQ-P8A-01 (worker hook — resolved: composed lifespan), OQ-P8A-02 (new ranked query — resolved), OQ-P8A-03 (catch-up one-burst — resolved + TD to page later), OQ-P8A-04 (periodic synthesis fact-sheet — DEFERRED to P9/P10), OQ-P8A-05 (guidance mandatory / fail-loud on load).
- **Dependency:** Phase 7A (populates `documents.full_body`) must land before Slice B; Slice A reads full_body but builds/tests stand alone.

**[Phase 6 Slice B — Installable Daemon App — Plan written 2026-06-14]** _(IN PROGRESS — Phases 1–3 + Phase 7 complete)_:
- [x] Phase 1 — Secret Vault wrapper (`keyring`: Keychain / Credential Manager) [P6-SLICEB-01]
- [x] Phase 2 — Cloud Connection Check (live authed test → `GET /api/state`, not `/health`) [P6-SLICEB-02]
- [x] Phase 3 — OS-Glue Seam + 2 adapters (launch-on-login + tray, `pystray`) [P6-SLICEB-03/04]
- [ ] Phase 4 — Setup Wizard (Tkinter, hard-block on connection test) [P6-SLICEB-05]
- [ ] Phase 5 — App Supervisor (setup-vs-run, sync-engine on worker thread, clean stop) [P6-SLICEB-06/09]
- [ ] Phase 6 — Uninstall Cleanup (`daemon uninstall` CLI: wipe key + config + startup reg) [P6-SLICEB-10]
- [x] Phase 7 — Packager (PyInstaller + DMG/Inno, baked default endpoint, 8 extractor hidden-imports) [P6-SLICEB-07/08] — **Completed 2026-06-16**: `main()` entry point, `daemon.spec`, `build_dmg.sh`, `installer.iss` created; no unit tests (manual hardware verification only).
- **Decisions:** native app NOT Docker (ADR-0016); cross-platform Mac+Windows; unsigned + one-time Gatekeeper/SmartScreen override; one generic build per OS + baked editable default endpoint; manual update (auto-update deferred); launch-on-login default ON. New deps: `keyring` (installed), `pystray` (planned), `pyinstaller` (planned).
- **Artifacts:** grill `docs/0_draft/phase6/phase6_sliceB_grill.md` · design `docs/1_design/phase6/phase6_sliceB_installer.md` · spec `docs/2_specs/phase6/phase6_sliceB_installer.md` · research `docs/3_research/phase6/phase6_sliceB_installer.md` (0 invalidated) · plan `docs/4_plans/phase6/phase6_sliceB_installer.md` · ADR-0016
- **Open (non-blocking):** OQ-SB1 (pystray ↔ asyncio main-thread per OS), OQ-SB2 (wizard-skip condition), OQ-SB3 (`daemon uninstall` CLI vs internal helper); plus external field-verify risks (PyInstaller freezing of keyring/watchdog/pystray, macOS quarantine clearing for LaunchAgent relaunch).

**[P5 Slice 2 — Deployment Foundation (AgentBase) — ✅ COMPLETE 2026-06-13]** _(450+ tests passing, Docker verified)_:
- [x] Phase 1 — Save-or-update data routine (`upsert_from_upload`) [C2-1]
- [x] Phase 2 — `VAULT_ROOT` config binding (pre-construction injection) [C2-5]
- [x] Phase 3 — REST handlers + secret-key gate + `/health` (`mcp_server/api.py`) [C2-2]
- [x] Phase 4 — Cloud entry point + startup DB ordering (`mcp_server/cloud_entry.py`) [C2-3/C2-4]
- [x] Phase 5 — Explicit `uvicorn` dependency (`pyproject.toml`) [C2-7]
- [x] Phase 6 — Startup script (`scripts/start.sh`) [C2-6]
- [x] Phase 7 — Dockerfile + `litestream.yml` template [C2-8]
- [x] Phase 8 — End-to-end container verification [all P5-DEPLOY]
-  New tests: 8 (upsert_from_upload) + 3 (VAULT_ROOT) + 13 (API) + 6 (cloud_entry) + 1 (uvicorn) = 31 new tests
- Total: 450 tests pass (mcp_server + storage + core + build)
- **Deviations from plan**: `_resolve.py` CONFIG lazy access fix, `test_resolve.py` fixture path bug fix, Dockerfile uses `--no-editable` install approach, `api.py` uses per-handler gate (Option B), hardcoded version pin relaxed
- **Docker image**: `ai-kms-p5-slice2:latest` — verified on port 8080

**[P5 Slice 1 — Data/Config Foundation — ✅ COMPLETE 2026-06-13]** _(merged to cloud-native, 1275 tests; 18 new tests)_:
- [x] **Phase 1 — Schema Upgrade**: Migration 008 (`knowledge_entries` table, 11 columns + 3 optional `documents` columns: `full_body`, `original_filename`, `file_size_bytes`). Single-file migration. `DocumentRow` +3 trailing fields with guarded reads in `_row_from_sqlite`. Version pin 7→8.
- [x] **Phase 2 — Rulebook Config + Two Pure Checks**: `src/config/dimensions.yaml` (provisional starter taxonomy: people/projects/domains, each with `other` catch-all). `load_dimensions()` standalone loader, `validate_dimension_tag()` Result-returning validator, `confidence_to_status()` pure helper using `band.route()` (no float literal). 8 new tests.
- [x] **Phase 3 — Knowledge Entry Store**: `src/storage/knowledge_entries.py` — `KnowledgeEntry` dataclass + 5 CRUD ops (`upsert`, `query_by_dimension`, `query_by_entity`, `retire`, `get_confident_and_pending`). Sources round-trip via json. retire NEVER deletes. upsert derives status from `confidence_to_status()`. 7 new tests.
- [x] **Code review fixes**: double-commit removed (rely on context manager commit), `readonly=True` added to read queries, upsert UPDATE path checks rowcount (returns Failure for nonexistent IDs).
- [x] **Phase 4 — Suite-wide green**: 1275 passed, 0 failures, 1 skip. Only expected test edit: `test_migration_007.py` version pin 7→8.

**Plan:** `docs/4_plans/P5_slice1_data_foundation.md`
**Spec:** `docs/2_specs/P5_slice1_data_foundation.md`
**Research:** `docs/3_research/P5_slice1_data_foundation.md`
**Design:** `docs/1_design/P5_slice1_data_foundation.md`
**Context:** `docs/0_draft/cloud_native_rearchitecture.md`
**Key decisions locked:**
- Single-file migration 008 (not split) per research A1
- Permissive schema: no NOT NULL beyond id, no CHECK on status
- `route()→status` mapping: AUTO→"confident", SUGGEST+CLUELESS→"pending"
- Validator takes pre-loaded rulebook object (loader exposed)
- `upsert` uniqueness is id-only this slice (natural-key dedupe deferred to Phase 8)
- No module-scope CONFIG import in new tests
- No DDL, LLM calls, or audit writes in knowledge_entries.py
- `sources` stored as JSON array (loose ref, no FK/junction table)

**Phase 0 Final Checklist** _(CLOSED)_:
- [x] core/exceptions.py
- [x] core/result.py
- [x] core/logging_setup.py
- [x] core/config.py
- [x] core/confidence.py
- [x] core/pipeline.py
- [x] core/audit.py
- [x] llm/ (all providers + prompt_loader.py)
- [x] prompts/ (scaffolding + test.yaml)
- [x] storage/schema.sql, migrations/, db.py, audit_log.py
- [x] vault/ (paths.py, frontmatter.py, reader.py, writer.py — complete 2026-05-20)
- [x] smoke test

**Phase 1 — Capture Checklist** _(CLOSED — complete 2026-05-21)_:
- [x] handlers/base.py (BaseHandler ABC, registry pattern)
- [x] handlers/__init__.py (HandlerRegistry export + auto-discovery)
- [x] handlers/markdown_handler.py (extract summary + metadata from .md)
- [x] handlers/pdf_handler.py (PDF text extraction → summary)
- [x] handlers/docx_handler.py (DOCX text extraction → summary)
- [x] handlers/url_fetcher.py (fetch web content; integrated into pipeline stages)
- [x] pipelines/capture.py (5-stage pipeline: extract → enrich_urls → summarize → metadata → store)
- [x] prompts/summarize.yaml + prompts/extract_metadata.yaml
- [x] core/tags.py + config/tags.yaml (tag taxonomy + validate_tags)
- [x] CLI: `kms capture <file>` + `kms capture --scan` + `kms watch`
- [x] vault/watcher.py (VaultWatcher + debounce via threading.Timer)
- [x] vault/indexer.py scan_non_md_drops + scan_capture modified/deleted/moved loops
- [x] 487 tests pass (all capture pipeline phases verified)
- [x] audit_log wired: every capture writes CAPTURED + TAG_VIOLATION entries

**Phase 1.5 — Revise Attachment Layout Checklist** _(CLOSED — superseded by Phase 1.5 Pay-Debt block below)_:
- [x] `core/config.py` — added `summaries_subdir: str = ".summaries"` Field; removed `attachment_path` @property; temporary callers in `capture.py:456`, `capture.py:627`, `cli/main.py:127` use `.root / .attachment_dir` with `# COUPLING:` comments marking Brief #2/#3 work. 576 tests pass.
- [x] `vault/paths.py` — added `project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries` helpers reading `attachment_dir` + `summaries_subdir` from VaultConfig. No hardcoded subdir names. 8 new tests; 594 tests pass.
- [x] `vault/indexer.py` — added `_DOT_ALLOWLIST: frozenset[str] = frozenset({".summaries"})`; updated both `dirnames[:] = [...]` prune expressions in `scan_non_md_drops` + `scan_vault` with scoped condition `(not d.startswith(".") or (d in _DOT_ALLOWLIST and dirpath.name == "attachment"))`. 99 vault tests pass, no regressions.
- [x] `vault/frontmatter.py` — added `"attachment_path"` to `_KNOWN_KEYS`; added `attachment_path: str | None = None` field to `NoteMetadata` after `source_file`. 15 frontmatter tests pass.
- [x] 4 architecture decisions recorded (DECISION-021 through -024); 5 TD items recorded (TD-020 through TD-024).
- [x] Claude CLI provider: metadata JSON parse fails on short DOCX extracts — RESOLVED 2026-05-25, see TECH_DEBT.md. (**TD-028**)
- [ ] Rename gate logic mis-calibrated (too liberal / too conservative). Needs research on competitor approaches + confidence-scored suggestion model. (**TD-029**)

Other in-flight notes:
- Handlers extension: XLSX done; others pending sibling approach finalization.
- Sibling md file handling: DONE.

<!-- Original Claude CLI provider error log (kept for TD-028 reproduction):
    tested with real vault and file, receive this kms capture /Users/lap14806/ai_kms_test_vault/attachment/finance.docx
    2026-05-22T10:07:00.590605Z [warning  ] para_context_path set but not found: /Users/phatchenh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Claude Brain/para-context.yaml — classify pipeline will skip PARA context. [core.config]
    2026-05-22T10:07:00.617190Z [info     ] docx.extract.ok                [handlers.docx_handler] bytes=24045 chars=29 correlation_id=3b1f2067-3b2f-4edf-a190-1e745c566e7e path=/Users/lap14806/ai_kms_test_vault/attachment/finance.docx
    2026-05-22T10:07:22.601077Z [error    ] stage_failed                   [core.pipeline] context={'content_preview': 'Need full note content. Headings alone ("Q1 performance", "Q2 Performance") insufficient for metadata extraction.\n\nProvide:\n- Body text / data / findings\n- Context (meeting? report? personal reflectio'} correlation_id=3b1f2067-3b2f-4edf-a190-1e745c566e7e error='metadata JSON parse error: Expecting value: line 1 column 1 (char 0)' pipeline=capture recoverable=False stage=metadata traceback='Traceback (most recent call last):\n  File "/Users/lap14806/Library/CloudStorage/OneDrive-VNGGroupJSC/Documents/Zalopay 2026/01. Improve productivity/ai_kms/pipelines/capture.py", line 100, in _parse_metadata_json\n    parsed = json.loads(cleaned)\n             ^^^^^^^^^^^^^^^^^^^\n  File "/Users/lap14806/.local/share/uv/python/cpython-3.12.11-macos-aarch64-none/lib/python3.12/json/__init__.py", line 346, in loads\n    return _default_decoder.decode(s)\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/Users/lap14806/.local/share/uv/python/cpython-3.12.11-macos-aarch64-none/lib/python3.12/json/decoder.py", line 338, in decode\n    obj, end = self.raw_decode(s, idx=_w(s, 0).end())\n               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/Users/lap14806/.local/share/uv/python/cpython-3.12.11-macos-aarch64-none/lib/python3.12/json/decoder.py", line 356, in raw_decode\n    raise JSONDecodeError("Expecting value", s, err.value) from None\njson.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)\n'
    FAILED: metadata JSON parse error: Expecting value: line 1 column 1 (char 0)
-->

**Phase Pre-2 — DB Schema Prep + Domain Scalar Cleanup** _(complete — 2026-06-03, 5 commits, 797 tests at completion)_:
- [x] **Phase 1 — TD-008 Migration files**: 3 new SQL migration files (003_add_project, 004_add_status, 005_add_key_topics) + schema-presence test. Commit: e83c7cd.
- [x] **Phase 2 — TD-008 Extend documents.py**: DocumentRow + `project`, `status`, `key_topics` fields; `_row_from_sqlite` reads new columns; `upsert` and `replace_path` write them (key_topics = tags minus domain/ and type/ prefixes). 6 new tests. Commit: e3a52ff.
- [x] **Phase 3 — TD-038 frontmatter.py**: `_DEPRECATED_KEYS = frozenset({"domain"})` + dumps() filter strips domain on write (lazy migration). Removed `domain` field from NoteMetadata, _KNOWN_KEYS, field_validator. Domain in YAML routes to extra. 3 new tests, 2 existing fixed. Commits: f25d64a (3A), f8cd23e (3B).
- [x] **Phase 4 — TD-038 Fix pipeline consumers**: Removed domain kwarg from store(), _merge_metadata(). _store_nonmd() uses tag-based filter `[t for t in tags if t.startswith("domain/")]` with COUPLING comment. Test assertion changed to tag membership. Commit: e87364a.
- [x] **Phase 5 — Full suite green**: 797 passed, 0 failures, 1 skipped (+10 tests from pre-phase baseline of 787).
- **Plan**: `docs/plans/phase_pre_2/td_008_and_td_038.md`
- **TD-008 closed** — documents table now has project, status, key_topics columns.
- **TD-038 closed** — domain scalar removed; lazy migration via _DEPRECATED_KEYS dumps() filter.
- **Deferred**: Backfill existing rows (NULL → Phase 3 Search), status vocabulary CHECK constraint (Phase 2 Classify), one-shot vault migration (C-03 violation), rename() in documents.py (path-only UPDATE, not touched).

**Brief #2 — attachment_capture_pipeline** _(complete — 2026-05-24)_:
- [x] Phase 1 (Taxonomy): `attachment-summary` added to `config/tags.yaml`; count tests updated to 9
- [x] Phase 2 (Prompt): `prompts/summarize_attachment.yaml` created — 3-section system prompt; variables: file_type, short_summary, text
- [x] Phase 3 (Rewrite `_store_nonmd()`): per-project paths, inline destination resolution, sibling-first write, CLUELESS inbox handling _(complete 2026-05-24)_
- [x] Phase 4 (Fix `scan_non_md_drops` + `scan_capture`): rule-based skip, extended `.summaries/` allowlist for inbox/ _(complete 2026-05-24)_

**Brief #3 — attachment_sync_and_archive** _(complete — 2026-05-24)_:
- [x] Phase 1 (Prerequisite Fixes): TD-023 watcher VaultConfig, TD-AS-1 .summaries/ skip, false-success logging — complete
- [x] Phase 2 (Archive Layout Helpers): domain_archive(paths.py), archive_path @property removed (config.py) — complete
- [x] Phase 3 (Watcher Sync Callbacks): _is_binary, _sibling_for helpers; on_delete → SIBLING_ORPHANED; on_move → ATTACHMENT_MOVED (same folder) or SIBLING_ORPHANED (different folder) — complete
- [x] Phase 4 (kms reconcile): 4-stage reconcile command — reconcile_paths, reconcile_orphan_binaries, reconcile_stale_binaries, reconcile_orphan_siblings; ReconcileResult dataclass; CLI wired; TD-026 retired

**Brief #4 — Review Fixes (post Phase 1.5+Briefs #1/#2/#3 review)** _(2026-05-24)_:
Triggered by `/superpowers:requesting-code-review`. Applied subset of review findings.
- [x] Sibling marker naming convention changed to `<binary.name>.md` (e.g. `report.pdf.md`) to prevent collisions between `report.pdf` and `report.docx` — see DECISION-028. Touched: `vault/watcher.py::_sibling_for`, `vault/indexer.py::_has_inbox_sibling`, `pipelines/capture.py` (3 sites: LOCATED sibling, CLUELESS marker, early-exit guard), `pipelines/reconcile.py` (Stages 2+3 sibling lookups), + tests.
- [x] Added `_is_managed_summaries_area(path, vault_cfg)` in `vault/paths.py`. Returns True when path lives under any `attachment/` subtree OR under `inbox/`. Used by reconcile Stage 4 to scope `.summaries/` `rglob`. Distinct from `_is_in_managed_attachment` (kept) which is the binary-pipeline area predicate.
- [x] Reconcile Stage 4 dual guards (DECISION-029): scope guard (`_is_managed_summaries_area`) + type guard (`note.metadata.type == "attachment-summary"`). Prevents accidental deletion of user-placed `.md` inside `.summaries/`.
- [x] `vault/watcher.py` refactor: hoisted lazy imports (logging, unicodedata, audit_write, AIDecision, Failure, Success, delete_by_path, rename_doc, read_note, move_note, write_note) to module top; moved `TYPE_CHECKING` block above `_sibling_for` definition.
- [x] `pipelines/reconcile.py`: top-level `from pipelines.capture import capture_file` (replaces inline lazy import). Test monkeypatch target updated from `pipelines.capture.capture_file` → `pipelines.reconcile.capture_file`. Reordered `__all__` so entry point `reconcile` is last (composition order).
- [x] CLUELESS marker body: replaced empty string with single-line placeholder (`_Pending classification — binary at: <path>_` + handoff note) so markers are FTS-searchable and self-explanatory in Obsidian preview.
- [x] STATE.md label fix: Brief #2 header `in progress` → `complete`. "Re-make work" prose collapsed into TD-028 + TD-029 with full error log preserved in HTML comment.
- [x] Tests updated: `test_watcher.py` monkeypatch targets retargeted to `vault.watcher.<name>` (top-level imports broke source-module patching — same gotcha as Q13); `_sibling_for` tests now assert `<filename>.md`; 1 new test for stem-collision distinctness. Phase-3, phase-9, phase-12, phase-rename, reconcile, indexer tests updated to new sibling pattern. 650 tests pass.

- [x] **Critical #1** (TD-030) resolved 2026-05-24: `on_deleted` reorder — binary sync now runs before `_should_skip`. Regression test added.

**[Phase 1.5 Pay-Debt — ✅ COMPLETE + code-review clean, 2026-06-03]**:
- [x] Phase 1 — FILE_LOST guard (`capture_file` entry + store guards)
- [x] Phase 2 — `_location_context` + `apply_location_tags` capture stage
- [x] Phase 3 — `reconcile_stale_tags` Stage 5 + reconcile signature changes
- [x] Phase 4 — `capture_folder` + watcher `DirCreatedEvent` + `batches` SQLite table (4.1+4.2 done; 4.3 watcher registry done 2026-06-02)
- [x] Phase 5 — Handlers extension (see `docs/research/phase1.5_redesign/handlers_extended.md`)
- [x] Phase 6 — Idempotent capture (content-hash early exit; `source_hash` in sibling frontmatter)
- [x] Phase 7 — `reconcile_stale_batch_refs` Stage 6 (TD-036; requires Phase 4)

**Code-review pass on Phase 1.5 Pay-Debt** _(commit `b41caf1`, branch `fix/phase1.5-codereview`, NOT pushed — 11 files +711/-45; 787 passed / 1 skipped, no new failures)_:
- [x] C1 (critical) — wired `batch_id=ctx.batch_id` into all 4 `documents` write sites in `pipelines/capture.py` (was always NULL → silently neutered Phase 7 `reconcile_stale_batch_refs`).
- [x] C2 (critical) — timer-cancel race in `vault/watcher.py::_fire_folder_stable`: added per-key identity token so stale fires no-op (prevented duplicate folder capture).
- [x] I1 — reconcile Stage 6 derives prefix from `vault_cfg.projects_dir`/`domain_dir` (was hardcoded `Projects/`/`Domain/` → silent batch_id data loss under non-default dirs).
- [x] I2 — `handlers/xlsx_handler.py` gained `max_file_size_bytes` guard + structlog.
- [x] I4 — reconcile human-lock guard keys off `context["vault_path"]`, not error-message substring.
- [x] I5 — corrected Stage 5 docstring (`recoverable=False`).
- [x] I6 — added watcher concurrency tests (`max_workers=1` serialization, C-10 worker-thread).
- [x] M1 — relocated `_move_folder` → `vault/writer.py::move_folder` returning `Result[Path]`; move failure falls through to CLUELESS path.

**New tech debt logged 2026-06-03** (full detail in TECH_DEBT.md):
- **TD-037** — binary modify never re-captures (Office files edited often → stale summaries; formalizes `TD-C6` marker at `watcher.py:236`). Owned by a future watcher-hardening phase.
- **TD-038** — drop redundant scalar `domain:` frontmatter field (domain should live only as a `domain/<D>` tag; scalar drifts because reconcile Stage 5 doesn't re-sync it). Multi-file refactor + existing-note migration.

**Not applied (deferred to user decision)**:
- Issue #8 — `move_attachment` TOCTOU window (existence check then `os.replace`). Tracked as **TD-031**.
- Issue #9 — `kms migrate-attachments` for legacy `Vault/attachment/`+`Vault/Archive/` layout. Deferred greenfield — no production users. Tracked as **TD-032**.

**Vault-Restructure — Editable/No-Edit Split** _(complete — 2026-06-04, merged from worktree-vault-restructure-editable-noedit; 956 tests pass)_:
- [x] Phases 1–7: Core editable/no-edit vault restructure — `no_edit_extensions` config, `resolve_placement()`, binary routing (no-edit→attachment/, editable→root), `_should_skip` updated for AI-output folders, misplaced-file sweep to inbox
- [x] Phase 8: Binary content-change detection — SHA-256 compare on modify events, lock-file filter for Office atomic saves (TD-037 resolved)
- [x] Phase 9: Settle window — coalesces multi-hop binary moves (A→B→C) to single re-home
- [x] Phase 10: Extend `_is_managed_summaries_area()` for root-level `.summaries/` + `reconcile_editable_migration` (Stage 7)
- [x] Lint cleanup + OneDrive sync conflict files removed
- **ADR-0006 status**: Proposed → Accepted
- **TD-037 closed** — binary modify now triggers re-capture via content-change detection
- **New module**: `src/vault/move_guard.py` — thread-safe registry suppressing watcher re-home for pipeline-initiated moves

**[TD-042 — Deprecated-key strip — ✅ COMPLETE 2026-06-07]**:
- [x] 1 lazy import + 3-line dirty check in `src/pipelines/reconcile.py` Stage 5
- [x] 3 new tests (P2-REC-01/02/03) in `tests/test_pipelines/test_reconcile.py`
- [x] 6 new fixture `.md` files in `tests/fixtures/`
- [x] 3 pre-existing `write_text()` hook violations in `test_reconcile.py` fixed
- [x] 959 tests pass; 1 pre-existing flaky watcher test (order-dependent, investigation dispatched)
- **TD-042 CLOSED** — Stage 5 now strips deprecated frontmatter keys via `_DEPRECATED_KEYS` dirty check

**[TD-040 + TD-041 — Batch-ID Fix — ✅ COMPLETE 2026-06-09]**:
- [x] Phase 1 — SQL migration 006: add `folder_path` column to `batches`
- [x] Phase 2 — `storage/batches.py`: extend `insert()` + add `find_by_folder_path()`
- [x] Phase 3 — `vault/paths.py`: add `is_batch_subfolder()` predicate
- [x] Phase 4 — `pipelines/capture.py`: batch-stamp single-file capture + subfolder detection in `scan_capture()`
- [x] Phase 5 — `vault/watcher.py`: batch-stamp on watcher move + add `update_batch_id()` to `storage/documents.py`
- [x] Phase 6 — Integration smoke: full watcher-move + scan path exercised end-to-end

**Plan:** `docs/5_plans/td040-td041-batch-id-fix.md`
**Design:** `docs/1_design/td040-td041-batch-id-fix.md`
**Key decisions locked:**
- `batch_id` = live subfolder membership (not a capture timestamp)
- Applies to subfolders in `inbox/`, `Projects/<A>/`, `Domain/<D>/` — NOT roots, not `attachment/`, `.summaries/`, `Archive/`
- `folder_path` required on `batches.insert()` — schema migration 006 (`006_batches_folder_path.sql`)
- `file_count = 1` for single-file-created batches (TD-043 — approximation; accurate counting deferred to Phase 8)
- `scan_capture()` dispatches via `capture_folder()` — Case B handles LLM-skip for already-located folders
- New `is_batch_subfolder()` predicate in `vault/paths.py` (must unpack `_location_context()` tuple)

**Session notes (2026-06-07):**
- `codebase-design-analysis` skill updated: Implications section now plain-English-first; Open Questions section got explicit format template
- `build-pipeline` skill updated: phase transition gate added between phases (output message + wait for user confirmation)
- TD-043 logged in TECH_DEBT.md (`file_count` approximation for single-file-created batches)

**[Project Registry — Plan written 2026-06-07]** _(✅ SHIPPED — confirmed in use by classify via P4 research 2026-06-11; corrects the earlier "PENDING" staleness)_:
- [x] Phase 1 — `vault/registry.py`: data classes + `build_registry()` + `format_for_prompt()` (P2-REG-01 through P2-REG-04) — verified: used by classify (`_build_vault_context`, `capture.py`); TD-051 resolved against it
- [x] Phase 2 — `LiveRegistry`: thread-safe mutation methods (P2-REG-05, P2-REG-06) — verified shipped (P4 research)
- [x] Phase 3 — `VaultWatcher` hookup: `registry` constructor param + 4 event dispatch points

**Plan:** `docs/5_plans/project-registry.md`
**Spec:** `docs/3_specs/project-registry.md`
**Research:** `docs/4_research/project-registry.md`
**Key decisions locked:**
- Domain declared via `domain/<D>` tag in `tags:` list (not scalar — scalar is deprecated)
- `build_registry()` returns `Result[ProjectRegistry]`, never raises (C-12)
- `LiveRegistry` uses `threading.Lock()` — same pattern as watcher's three locks
- `VaultWatcher.__init__` gains `registry: LiveRegistry | None = None` — all existing callers unaffected
- Directory event registry calls go INSIDE existing `if event.is_directory: return` branches
- TD-034 retired by this plan (project-to-domain registry now exists)

**[P4 — MCP Server: Context Injection & Tool Design — ✅ COMPLETE 2026-06-12]** _(1258 tests; all 7 phases shipped in 6 commits; `src/mcp_server/` package live)_:
- [x] Phase 1 — Prerequisites: `wal_autocheckpoint=100` in `_connect()` (TD-007/OQ-003) + `mcp.context_injection` config block (C-06) + `mcp>=1.27,<2` dep + `location` filter on `filter_paths()`
- [x] Phase 2 — MCP Server Shell: stdio FastMCP bootstrap (mirrors CLI C-10/C-11), connection-lifespan engine, `copy_context().run()` isolation (OQ-004)
- [x] Phase 3 — Context Injection Engine (the C-14 logic home): result-frequency count + project→domain registry lookup + threshold/cap + hash-dedup + context-block assembly
- [x] Phase 4 — Binary Resolver Helper (`kms_inspect` sibling↔binary re-extraction)
- [x] Phase 5 — Note Mover Helper (`kms_move`: `move_note` → `write_note(dst, new_meta)` → `replace_path(old_vault_path, outcome)`)
- [x] Phase 6 — Tool Shim Layer: 5 logic-free shims (`kms_vault_info`, `kms_search`, `kms_read`, `kms_inspect`, `kms_move`) — built LAST (C-14 + C-15)
- [x] Phase 7 — TD-055 AI usage instructions (`AI_INSTRUCTIONS.md` + tool `description=` strings)

**Design:** `docs/1_design/P4_mcp_context_injection.md`
**Spec:** `docs/2_specs/P4_mcp_context_injection.md`
**Research:** `docs/3_research/P4_mcp_context_injection.md`
**Plan:** `docs/4_plans/P4_mcp_context_injection.md`
**ADRs:** 0010 (context injection in tool responses) + 0011 (write-path `kms_write`/`kms_move`) — both **ACCEPTED 2026-06-11**
**Behavior inventory:** P4-MCP-01…09 (origin: design)
**Key decisions locked:**
- `mcp>=1.27,<2` (official Anthropic FastMCP) — dependency **APPROVED by user 2026-06-11**
- Dedup session memory = FastMCP **process-scoped lifespan** (= per-conversation under stdio); engine read via `ctx.request_context.lifespan_context`; one-line shims stay C-14-clean (A1)
- 5 MVP tools; **`kms_write` DEFERRED** (TD-056 field-level metadata guard)
- Context source of truth = live Project Registry (`vault/registry.py`), **NOT** `meta.yaml`
- Result cards carry `note_type` (`attachment-summary` = binary signal), **NOT** `attachment_path`
- `move_note` carries no metadata → relocation = `move_note` → `write_note(dst, new_meta)` → `replace_path(old_vault_path, outcome)` (A7/A7b; `replace_path` 2nd arg is the `WriteOutcome`, not a path)
- Build order bottom-up: tools (logic-free shims) built LAST on tested engine/helpers (satisfies C-14 + C-15 structurally)

**Next roadmap work**: `/tdd-implement` the Phase 4 plan (`docs/4_plans/P4_mcp_context_injection.md`) — Session A first.

**[P3 Session A — Index Layer — ✅ COMPLETE 2026-06-10]** _(merged to main, 1147 tests)_:
- [x] **Phase 1 — Infrastructure**: Migration 007 (embeddings_vec vec0 + notes_fts FTS5), sqlite-vec extension loading in `_connect()`, SearchConfig (4 fields), sentence-transformers dependency, TD-050 timer-leak fixture. Commit: da5a0f5.
- [x] **Phase 2 — Meaning Indexer**: `src/retrieval/embeddings.py` — `index_embedding()` with lazy SentenceTransformer, DELETE+INSERT for vec0 PK semantics, retry on OperationalError. Commit: da5a0f5.
- [x] **Phase 3 — Word Indexer**: `src/retrieval/keyword.py` — `index_keywords()` with DELETE+INSERT in single `get_connection()` transaction. Commit: 9455e76.
- [x] **Phase 4 — Capture wiring + index maintenance** (Components 7+8): 4 best-effort try/except indexing blocks in `capture.py` (2 `_store_md` + 2 `_store_nonmd`), lazy imports, separate try per indexer. Search-table cleanup in `documents.py` (`delete_by_path`, `rename`, `replace_path`) within same transaction. +10 new tests (5 maintenance + 5 capture search). Commits: 940a6c9, 721f78b.
- [x] **Phase 5 — Full suite verification**: 1147 passed, 0 failures. Ruff clean on changed files. Plan updated. Commit: 1dae395.

**Plan:** `docs/4_plans/p3_session_a_index_layer.md`
**Spec:** `docs/2_specs/p3_session_a_index_layer.md`
**Research:** `docs/3_research/p3_session_a_index_layer.md`
**New modules:** `src/retrieval/` (embeddings.py, keyword.py, __init__.py)
**New migration:** `src/storage/migrations/007_search_indexes.sql`
**Key decisions locked:**
- vec0 virtual table for embeddings (float[384] coupled to all-MiniLM-L6-v2), FTS5 for keywords
- Best-effort indexing — failures logged, never block capture
- DELETE+INSERT pattern everywhere (vec0/FTS5 no PK update support)
- `replace_path` cleans old search entries but does NOT create new ones (capture pipeline does that)
- `rename` copies both search tables old→new within same transaction

**[P3 Session B — Query Path (Hybrid Search) — ✅ COMPLETE 2026-06-11]** _(merged to main, ~180 new tests, ~3400 LOC)_:
- [x] Design: `docs/1_design/P3_session_b_query_path.md` — hybrid search architecture, KNN-scoping sub-decision (ADR-0009)
- [x] Spec: `docs/2_specs/P3_session_b_query_path.md` — 7 components (C0-C6), Q1/Q2 diagrams, 19 assumptions
- [x] Research: `docs/3_research/P3_session_b_query_path.md` — all 13 original assumptions validated; A5 invalidated→resolved by R1; A15 invalidated (mechanical stale test)
- [x] Plan: `docs/4_plans/P3_session_b_query_path.md` — 7 phases, bottom-up build order
- [x] **Phase 1 — Descriptive Title at Capture (Component 0)**: `title` typed field on `NoteMetadata`; `_derive_title` prefers `metadata.title`; wired at 3 `NoteMetadata` build sites. +4 tests.
- [x] **Phase 2 — Candidate Filter (Component 1)**: `filter_paths()` in `documents.py`; project + date range → vault_path set; `None` sentinel for global. +6 tests.
- [x] **Phase 3 — Hybrid Ranker (Component 2)**: new `retrieval/ranker.py`; `rank()` with BM25 (FTS5) + KNN (vec0) + RRF fusion (RRF_K=60); `RankedResult` dataclass. +8 tests.
- [x] **Phase 4 — Re-ranker (Component 3)**: new `retrieval/reranker.py`; `rerank()` with CrossEncoder; `SearchResult` cards (vault_path, summary, snippet, score, metadata); stale-row skipping. +7 tests.
- [x] **Phase 5 — Search Coordinator (Component 4)**: new `retrieval/search.py`; `search()` wires filter→rank→rerank; filter-only branch (score=0.0, by updated_at); exported via `retrieval/__init__.py`. +9 tests.
- [x] **Phase 6 — Search Command (Component 5)**: rewritten `kms search` CLI (`--project`, `--since`, `--max`, `--reindex`); replaces stub (TD-012 closed). +9 tests.
- [x] **Phase 7 — TD-051 Classify Split (Component 6)**: `project_names`/`domain_names` frozenset params on `classify()`; cross-type validation; backward-compat fallback. +4 tests.
- [x] **Code review**: 2 Critical (test_registry unpack, None sentinel fallback) + 1 Important (backward-compat test) fixed. See commit `99af877`.

**Plan:** `docs/4_plans/P3_session_b_query_path.md`
**Spec:** `docs/2_specs/P3_session_b_query_path.md`
**Research:** `docs/3_research/P3_session_b_query_path.md`
**Design:** `docs/1_design/P3_session_b_query_path.md`
**ADR:** `docs/architecture/system_adr/0009-phase3-search-rrf-rerank-not-tier-dispatcher.md`
**Key decisions locked:**
- ADR-0009: No tier dispatcher — replaced by cheap cards + lazy full-note fetch
- In-database filtered KNN (`MATCH + k + IN`) — verified on sqlite-vec v0.1.9
- Bare-query embedding (re-ranker absorbs doc/query asymmetry)
- RRF constant `RRF_K = 60` as named module constant in `retrieval/`
- `date_range` as `tuple[datetime, datetime | None]`
- `score` = cross-encoder score (final relevance)
- `--reindex` standalone only
- Candidate Filter in `documents.py` (reusable by Phase 8/9)
- Component 0 (R1): first-class `title` field on NoteMetadata, all captures
- TD-051: split pooled classify validation into project-names vs domain-names
**Phases (7):**
1. Descriptive Title at Capture (Component 0) — `frontmatter.py`, `capture.py`, `documents.py`
2. Candidate Filter (Component 1) — `filter_paths()` in `documents.py`
3. Hybrid Ranker (Component 2) — new `retrieval/ranker.py` (BM25 + KNN + RRF)
4. Re-ranker (Component 3) — new `retrieval/reranker.py` (cross-encoder + card builder)
5. Search Coordinator (Component 4) — new `retrieval/search.py` (public `search()`)
6. Search Command (Component 5) — rewrite `kms search` CLI stub
7. TD-051 Classify Split (Component 6) — `classify()` cross-type validation

**[P2-CL — classify() pure function — ✅ COMPLETE 2026-06-08]** _(implemented on `main`: commits b2d33fa + a28b33c)_:
- [x] Phase 1 — `ClassifyResult` dataclass in `src/pipelines/classify.py`
- [x] Phase 2 — `classify()` async function in `src/pipelines/classify.py`

**Plan:** `docs/4_plans/phase2/classify.md`
**Note:** P2-CIC (below) will RESHAPE `classify()` — subject-shaped input + derive-from-tags output. The existing signature/shape is the pre-reshape baseline.

**[P2-CIC — Classify Inline in Single-File Capture — ✅ COMPLETE 2026-06-08]** _(merged to main, 1080 tests)_:
- [x] Phase 1 — Foundation: candidate frontmatter fields + PipelineContext extensions (C2, C3)
- [x] Phase 2 — Subject Builder (C1)
- [x] Phase 3 — Classify Engine reshape: subject-shaped input, derive-from-tags output (C6)
- [x] Phase 4 — Filer refactors: `_store_nonmd` optional params + `.md` cross-folder move prep (A6 fix)
- [x] Phase 5 — Classify Step: new pipeline stage (C4, convergence point)
- [x] Phase 6 — AUTO move handoff: route derived destination through existing filer (C5)
- [x] Phase 7 — Idempotent re-entry: retire pending-routing, guard re-classify (C7)
- [x] Phase 8 — TD-049 NFC fix: normalize folder_path on both batch paths (C8)
- [x] Phase 9 — [P2] Folder migration: unified prompt for folder classify (C9)

**Plan:** `docs/4_plans/phase2/classify-inline-capture.md`
**Spec:** `docs/2_specs/phase2/classify-inline-capture.md`
**Research:** `docs/3_research/phase2/classify-inline-capture.md`
**Design:** `docs/1_design/phase2/classify-inline-capture.md`
**Key decisions locked:**
- Inline (Option A): classify stage in `capture_file` after `apply_location_tags`, before `store`
- Routing = derived from tags+project (not free AI pick): project → `Projects/<p>/`; domain → `Domain/<d>/`; neither → CLUELESS
- classify() reshaped: drops `target_type/target_name`; gains `project` + `domains` + `primary_domain`
- Gate outcomes: AUTO (move), SUGGEST/CLUELESS (record candidate in frontmatter, leave in inbox)
- SUPPRESS: folder-invoked `capture_file` skips classify via `skip_classify` on PipelineContext
- Prompt unification (Option U2): one flat prompt, phased (P1 single-file, P2 folder migration)
- `_store_nonmd` refactored with optional `target_type`/`target_name` params (A6, confirmed by research)
- `.md` AUTO cross-folder move via `move_note` before store (not through `_store_md` internal rename)
- TD-048: 3 retries with exponential backoff, fall back to CLUELESS on exhaustion
- TD-049: NFC-normalize `folder_path` on both write and read paths

**Session notes (2026-06-09):**
- TD-040/TD-041 Batch-ID Fix confirmed COMPLETE — code already shipped (P2-BAT); docs were stale, synced today
- P2-CIC review fixes applied (2026-06-08): AUTO audit moved post-move, exact-membership destination validation via `_destination_names()`, F841 dead variable removed, duplicate `asyncio` import removed
