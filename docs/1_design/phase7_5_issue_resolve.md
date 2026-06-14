# Design: Phase 7.5 — Issue Resolve

_Behavior ID prefix: P7H-FIX (07–12 added here; 01–06 already in inventory)_
_Last updated: 2026-06-14_

## Overview

A nuclear code review of the cloud-native branch found 26 issues across the daemon, capture pipeline, and prompt templates. Seven critical items were fixed in-session. The remaining issues were verified in a follow-up research pass: 12 are confirmed real, the rest are false positives or acceptable trade-offs.

This batch fixes all 12 confirmed issues in three ordered waves. Every fix has a locked approach — no design decisions remain open. The work is entirely internal: no new features, no API changes, no user-visible behavior changes except improved reliability (the daemon no longer re-uploads the entire vault on a transient network glitch) and marginally faster vault scans (ignored directories are pruned instead of traversed).

**Who cares:** A manager reading this should know that a thorough code audit found a dozen loose ends in the desktop sync daemon and the file-processing pipeline. This batch tightens them all. Nothing here changes what the system does — only how reliably and cleanly it does it.

## Q1 Diagram — What happens inside

```
          12 verified issues
          from nuclear review
               │
               ▼
  ┌──────────────────────────────┐
  │  WAVE 1 — Daemon Structure  │
  │  Pull the 243-line god      │
  │  function into its own      │
  │  class; update the one      │
  │  external caller            │
  └──────────────┬───────────────┘
                 │  class exists,
                 │  imports updated
                 ▼
  ┌──────────────────────────────┐
  │  WAVE 2 — Daemon Fixes      │
  │  (8 items, independent)     │
  │                              │
  │  • Retry before aborting    │
  │    cloud fetch              │
  │  • Skip ignored folders     │
  │    during vault walk        │
  │  • Log exceptions from      │
  │    async callbacks          │
  │  • Centralize auth header   │
  │  • Delete dead twin builder │
  │  • Remove dead config field │
  │  • Fix move-event skip rule │
  │  • Widen periodic-interval  │
  │    number type              │
  └──────────────┬───────────────┘
                 │  all daemon
                 │  fixes landed
                 ▼
  ┌──────────────────────────────┐
  │  WAVE 3 — Capture + Prompt  │
  │  (3 items, independent)     │
  │                              │
  │  • Extract shared indexing  │
  │    helper (removes 2x       │
  │    26-line duplicate)       │
  │  • Check and log summary    │
  │    attachment failures      │
  │  • Add file type to the     │
  │    vision prompt template   │
  └──────────────────────────────┘
```

## Fix inventory

| ID | Sev | Finding | File(s) | Approach | Wave | Behavior ID |
|----|-----|---------|---------|----------|------|-------------|
| #1 | IMPORTANT (structural) | `_run_with_stop` is a 243-line god function with 9 nested closures | `daemon/cli.py`, `daemon/app.py` | Extract `DaemonLoop` class owning lifecycle + callbacks. `app.py:23` updates import from `_run_with_stop` to `DaemonLoop`. | 1 | P7H-FIX-07 |
| #2 | HIGH (data safety) | `_fetch_cloud_state` returns `{}` on failure, triggering full vault re-upload | `daemon/scanner.py` | Wrap in `retry_with_backoff`. On exhaustion, return `None` sentinel. Caller aborts scan with warning. | 2 | P7H-FIX-01 |
| #3 | MEDIUM (performance) | `os.walk` never prunes ignored dirs (descends into `.git`, `.obsidian`) | `daemon/scanner.py` | Prune `dirnames[:]` in-place using `_SKIP_DIRS` derived from `config.ignore_patterns`. | 2 | P7H-FIX-02 |
| #4 | IMPORTANT (observability) | Fire-and-forget `asyncio.run_coroutine_threadsafe` — exceptions vanish | `daemon/cli.py` (4 sites) | Add `done_callback` that logs `fut.exception()` at error level. | 2 | P7H-FIX-03 |
| #5 | LOW (maintenance) | Auth header `{"Authorization": f"Bearer {config.api_key}"}` duplicated 6 places | `uploader.py`, `event_reporter.py`, `scanner.py`, `cli.py` | Set default `headers=` on `httpx.AsyncClient` at construction. Remove per-call `headers=` from all 6 sites. | 2 | P7H-FIX-08 |
| #6 | LOW (DRY) | Duplicate 26-line indexing blocks in capture.py (text vs binary paths) | `pipelines/capture.py` | Extract `_best_effort_index(vault_path, title, summary, body, db_path)` helper. Both paths call it. | 3 | P7H-FIX-09 |
| #7 | LOW (dead code) | `_build_disk_state` is near-twin of `_build_disk_entries` | `daemon/scanner.py` | Delete `_build_disk_state`. A1 backward-compat path calls `_build_disk_entries` and discards the extra `entries` dict. | 2 | P7H-FIX-10 |
| M1 | MINOR | Dead `scan_batch_size` config field — defined, never used | `daemon/config.py` | Remove the field. Update any tests that construct `DaemonConfig` with it. | 2 | P7H-FIX-11 |
| M5 | MINOR | Move events skip when src OR dst is ignored — should skip only when dst is ignored | `daemon/watcher.py` | Change `or` to check only dst: `if self._should_skip(dst_path): return`. If only src is ignored, process the event as a create at dst. | 2 | P7H-FIX-04 |
| M8 | MINOR | `periodic_interval_seconds` typed as `int`, inconsistent with other float timing fields | `daemon/config.py` | Change type from `int` to `float`. | 2 | P7H-FIX-12 |
| M11 | MINOR | `attach_summary` result discarded — failures are silent | `pipelines/capture.py` (2 sites) | Check return value. On `Failure`, log at warning level. Capture still returns `Success`. | 3 | P7H-FIX-06 |
| C5 | MINOR | `describe_image.yaml` declares `mime_type` variable but never uses `{{mime_type}}` in template | `prompts/describe_image.yaml` | Add `{{mime_type}}` to the user prompt: "This file is a {{mime_type}}." | 3 | P7H-FIX-05 |

## Wave ordering rationale

**Wave 1 must go first.** The `DaemonLoop` extraction (#1) restructures `cli.py` — every subsequent daemon fix (#2–#5, #7, M1, M5, M8) touches code that will move from nested closures into class methods. Doing them before the extract means merge conflicts; doing them after means clean, isolated method edits.

**Wave 2 items are independent of each other.** Once `DaemonLoop` exists, each daemon fix (#2–#5, #7, M1, M5, M8) touches a different function or module. They can be implemented in any order within the wave.

**Wave 3 items are independent and touch different modules.** The capture pipeline (#6, M11) and the prompt template (C5) have zero overlap with the daemon code. They are sequenced last because Wave 1/2 are the higher-risk changes; Wave 3 is trivial.

## Implications

### Callers affected

| File | Change | Why |
|------|--------|-----|
| `daemon/app.py:23` | Import changes from `_run_with_stop` to `DaemonLoop` | Wave 1 restructure |
| `tests/test_daemon/test_cli.py` | Test restructure — callbacks become class methods, test setup simplifies | Wave 1 |
| `tests/test_daemon/test_scanner.py` | `_build_disk_state` references removed; `_fetch_cloud_state` tests updated for retry+sentinel | Wave 2 |
| `tests/test_daemon/test_config.py` | `scan_batch_size` removed from fixtures; `periodic_interval_seconds` accepts float | Wave 2 |
| `tests/test_daemon/test_watcher.py` | Move-skip logic test updated for dst-only check | Wave 2 |
| `tests/test_pipelines/test_capture.py` | `_best_effort_index` helper tested; `attach_summary` result check tested | Wave 3 |

### What does NOT change

- No new dependencies.
- No migration files.
- No config file format changes (M1 removes a dead field that nobody uses; M8 widens a type that is backward-compatible).
- No user-facing CLI changes.
- No MCP tool changes.

## Option: Single-batch vs multi-PR

| Approach | Pros | Cons |
|----------|------|------|
| **Single batch** (1 PR, all 12 fixes) | All fixes are verified and approaches locked. One review pass. Avoids merge-conflict cascades between waves. | Large diff — reviewer must trust the verification research. |
| **Multi-PR** (3 PRs, one per wave) | Smaller, focused diffs. Easier to revert a single wave. | 3x review overhead. Wave 2/3 PRs may need rebasing if Wave 1 lands with changes. |

**Recommendation: Single batch.** Every fix has been code-verified against the actual source. No design decisions remain. The approaches are mechanical (extract, delete, add a log line, change a type). A single PR with clear commit-per-wave structure gives the reviewer the traceability of multi-PR without the rebase overhead.

## Open questions

None. All 12 approaches are locked. The original research verified each finding against actual code and confirmed severity levels. No design decisions were deferred.

---

## Source references

- Research verification: `docs/3_research/nuclear_review_deferred_findings_verification.md`
- Original findings: `docs/0_draft/nuclear_review_deferred_findings.md`
- Behavior inventory: `docs/system_behavior/behavior_inventory.yaml` (P7H-FIX-01 through P7H-FIX-12)
