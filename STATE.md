# STATE.md — Cross-Session Project State
_Created: 2026-05-09_
_Last updated: 2026-06-03 (Phase Pre-2 complete — TD-008 DB columns + TD-038 domain scalar cleanup; 5 commits, 797 tests pass)_

## Current Position
**Phase**: Phase Pre-2 — DB Schema Prep + Domain Scalar Cleanup ✅ **Complete as of 2026-06-03**

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

**Phase 1.5 — Revise Attachment Layout Checklist** _(PENDING; not in roadmap — design-change rework)_:
- [x] `core/config.py` — added `summaries_subdir: str = ".summaries"` Field; removed `attachment_path` @property; temporary callers in `capture.py:456`, `capture.py:627`, `cli/main.py:127` use `.root / .attachment_dir` with `# COUPLING:` comments marking Brief #2/#3 work. 576 tests pass.
- [x] `vault/paths.py` — added `project_attachment`, `project_summaries`, `domain_attachment`, `domain_summaries` helpers reading `attachment_dir` + `summaries_subdir` from VaultConfig. No hardcoded subdir names. 8 new tests; 594 tests pass.
- [x] `vault/indexer.py` — added `_DOT_ALLOWLIST: frozenset[str] = frozenset({".summaries"})`; updated both `dirnames[:] = [...]` prune expressions in `scan_non_md_drops` + `scan_vault` with scoped condition `(not d.startswith(".") or (d in _DOT_ALLOWLIST and dirpath.name == "attachment"))`. 99 vault tests pass, no regressions.
- [x] `vault/frontmatter.py` — added `"attachment_path"` to `_KNOWN_KEYS`; added `attachment_path: str | None = None` field to `NoteMetadata` after `source_file`. 15 frontmatter tests pass.
- [x] 4 architecture decisions recorded (DECISION-021 through -024); 5 TD items recorded (TD-020 through TD-024).
- [ ] Claude CLI provider: metadata JSON parse fails on short DOCX extracts (~29 chars). Prompt hardening or empty-metadata fallback needed. (**TD-028**)
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

**Phase Pre-2 — DB Schema Prep + Domain Scalar Cleanup** _(complete — 2026-06-03, 5 commits, 797 tests pass)_:
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

**Next roadmap work**: Phase Pre-2 (TD-008 + TD-038) — plan written 2026-06-03, then Phase 2 — Classify pipeline. Branch `fix/phase1.5-codereview` awaiting user push.

**[Phase Pre-2 — TD-008 + TD-038 — Plan written 2026-06-03]** _(PENDING implementation)_:
- [ ] Phase 1 — TD-008: Migration files (003/004/005 SQL)
- [ ] Phase 2 — TD-008: Extend documents.py (DocumentRow + _row_from_sqlite + upsert + replace_path)
- [ ] Phase 3 — TD-038: frontmatter.py changes (dumps() filter + remove domain field; fix 2 existing tests)
- [ ] Phase 4 — TD-038: Fix pipeline consumers (capture.py + writer.py; fix 1 existing test)
- [ ] Phase 5 — Full suite green
