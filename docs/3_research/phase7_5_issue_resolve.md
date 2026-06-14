# Research: Phase 7.5 Issue Resolve — Spec Verification

_Last updated: 2026-06-14_

## Overview

This document verifies all 14 assumptions (A1-A14) in the Phase 7.5 spec (`docs/2_specs/phase7_5_issue_resolve.md`) against the actual code on the `cloud-native` branch. Phase 6 Slice B was just merged, so all line numbers were re-derived from the current source.

**Bottom line: 13 validated, 1 invalidated (minor), 0 unverifiable.** The single invalidation (A1) is a minor nuance that does not affect the fix approach or require redesign. No Q4 conflict diagram is needed.

---

## Spec Verification Table

| ID | Claim | Verdict | Actual Finding |
|----|-------|---------|----------------|
| A1 | `_run_with_stop` is the only `cli.py` export used by `app.py` for the sync engine | **PARTIAL — minor nuance** | `app.py:23` imports `_run_with_stop` (module-level, sync engine). But `app.py:35` also does a lazy import of `run_uninstall` from `cli.py` inside `main()`. The spec qualifier "for the sync engine" is correct — `run_uninstall` is for uninstall, not sync. However, Wave 1's DaemonLoop extraction must preserve `run_uninstall` as a separate export. No design impact. |
| A2 | `_fetch_cloud_state` calls `client.get()` directly without `retry_with_backoff` | **VALIDATED** | Verified at `scanner.py:429-475`. Calls `await client.get(url, headers=headers)` directly at line 442. No `retry_with_backoff` wrapper. Confirmed as the only daemon HTTP call without retry. |
| A3 | `retry_with_backoff` takes `(client, config, request_fn)` and returns `Result[httpx.Response]` | **VALIDATED** | Verified at `_http_retry.py:33-37`. Signature is `async def retry_with_backoff(client: httpx.AsyncClient, config: DaemonConfig, request_fn: Callable[[], Awaitable[httpx.Response]]) -> Result[httpx.Response]`. Exact match. |
| A4 | Both `_build_disk_state` and `_build_disk_entries` use `_dirnames` (leading underscore, explicitly unused) in `os.walk` | **VALIDATED** | `_build_disk_state` at `scanner.py:490`: `for dirpath_str, _dirnames, filenames in os.walk(...)`. `_build_disk_entries` at `scanner.py:544`: identical pattern `_dirnames`. Neither function prunes directories. |
| A5 | Exactly 4 sites in `cli.py` call `asyncio.run_coroutine_threadsafe` and discard the Future | **VALIDATED** | Four sites confirmed: line 352 (`_on_move_window_expired`), line 427 (`_on_create_or_modify`), line 449 (`_on_move`), line 466 (`_on_delete`). All discard the returned Future. |
| A6 | Auth header appears in 6 places across `uploader.py` (2), `event_reporter.py` (2), `scanner.py` (1), `cli.py` (1); 7th in `connection_check.py` using `key` not `config.api_key` | **VALIDATED** | Exact match. `uploader.py:102,149` (2x). `event_reporter.py:60,97` (2x). `scanner.py:439` (1x). `cli.py:98` (1x, `status` command). `connection_check.py:38` (1x, uses `key` parameter, creates own client). Total: 6 centralizable + 1 separate = 7. |
| A7 | Duplicate indexing blocks at approximately lines 224-250 (text) and 440-466 (binary) with identical structure except the `body` argument | **VALIDATED** | Text path: lines 225-250 (index_keywords at 226-236, index_embedding at 239-250). Binary path: lines 441-466 (index_keywords at 442-452, index_embedding at 454-466). Structurally identical. Only difference: text path uses `body=extracted_text` (line 233), binary path uses `body=summary or ""` (line 449). |
| A8 | `_build_disk_state` is called only in the `cache is None` backward-compat path | **VALIDATED** | Only call site: `scanner.py:98` inside `if cache is None:`. No other references in `src/` except the function definition (line 478) and a docstring mention in `_build_disk_entries` (line 535). |
| A9 | `scan_batch_size` is defined but never used outside the config module | **VALIDATED** | Defined at `config.py:60`. Grep finds usage only in: config.py (definition), test_config.py (4 references: default test, YAML round-trip test), and behavior_inventory.yaml (documentation). Zero production usage outside the config module. Test fixtures will need updating. |
| A10 | `on_moved` uses `if self._should_skip(src_path) or self._should_skip(dst_path): return` | **VALIDATED** | Verified at `watcher.py:199`: `if self._should_skip(src_path) or self._should_skip(dst_path):` followed by `return` on the same line. Exact `or` condition as described. |
| A11 | `periodic_interval_seconds` is typed as `int` | **VALIDATED** | Verified at `config.py:66`: `periodic_interval_seconds: int = Field(default=21600, ge=0)`. Confirmed `int`, not `float`. |
| A12 | `attach_summary` returns a `Result` type and its return value is discarded at 2 sites in `capture.py` | **VALIDATED** | `attach_summary` at `documents.py:494-555` returns `Result[int]` (Success(rowcount) or Failure). Two call sites in capture.py: line 217 (text path) and line 432 (binary path). Both discard the return value — no assignment, no match statement. |
| A13 | `describe_image.yaml` declares `variables: [mime_type]` but never uses `{{mime_type}}` in the template | **VALIDATED** | Verified at `describe_image.yaml:15`: `variables: [mime_type]`. The template text (lines 1-14) contains no `{{mime_type}}` anywhere. The variable is declared but unused. |
| A14 | Line numbers from the nuclear review may have shifted after Phase 6 Slice B merge | **VALIDATED (numbers are still accurate)** | Post-merge line numbers match spec claims: `_run_with_stop` at line 268 (spec says 268), `_fetch_cloud_state` at line 429 (spec cites scanner.py:429), `_build_disk_state` at line 478, `_build_disk_entries` at line 523. The Slice B merge restructured cli.py but the function positions happen to coincide with the spec's stated locations. |

---

## Invalidated Assumptions

### A1 — Minor nuance (does not affect approach)

**What the spec says:** `_run_with_stop` is the only function `app.py` imports from `cli.py` for the sync engine.

**What the code shows:** `app.py` has two imports from `cli.py`:
1. Line 23: `from daemon.cli import _run_with_stop` (module-level, for the sync engine)
2. Line 35: `from daemon.cli import run_uninstall` (lazy, inside `main()`, for uninstall only)

**Impact on the fix:** None. The spec's qualifier "for the sync engine" makes the statement technically correct. Wave 1's DaemonLoop extraction targets only `_run_with_stop`. The `run_uninstall` function is a standalone top-level function at `cli.py:129-183` that has nothing to do with the sync engine and will not be affected by the extraction. The planner should note this second import exists but no approach changes are needed.

**Escalation level:** Type-a (minor correction to spec text, no approach change).

---

## Additional Findings (discovered during verification)

### 1. `_run_with_stop` size and structure confirmed post-Slice-B

- Function spans lines 268-509 (242 lines, consistent with the "243-line" claim)
- Contains 9 named closures: `_refresh_move_timer` (326), `_on_move_window_expired` (339), `_handle_expired` (344), `_on_create_or_modify` (354), `_handle` (356), `_on_move` (429), `_handle` (431), `_on_delete` (451), `_handle` (460)
- Only 1 `nonlocal` capture (line 328: `nonlocal _move_timer`), not 4 as the original finding claimed. The other shared state (`cfg`, `client`, `cache`, `move_buffer`, `loop`) is captured by closure scope, not via `nonlocal`.
- `cli.py` is now 533 lines total

### 2. `status` command creates its own short-lived `httpx.AsyncClient`

The `status` command at `cli.py:87-123` creates a separate `httpx.AsyncClient(timeout=10)` with its own auth header at line 98. This is independent of the long-lived client in `_run_with_stop` (which uses `timeout=30`). The spec's decision to leave the `status` command's header in place is correct.

### 3. `scan_batch_size` test fixtures that need updating

Two test locations reference `scan_batch_size`:
- `tests/test_daemon/test_config.py:199-200` — `test_scan_batch_size_default` asserts default value is 50
- `tests/test_daemon/test_config.py:388-400` — YAML round-trip test includes `scan_batch_size: 100`

Both must be removed when the field is deleted. The spec correctly anticipated this.

### 4. `connection_check.py` uses a different auth pattern

`connection_check.py:38` uses `headers={"Authorization": f"Bearer {key}"}` where `key` is a function parameter, not `config.api_key`. It either takes an injected client or creates its own (`httpx.AsyncClient(timeout=10)`). It does not share the daemon's long-lived client. The spec correctly excludes it from centralization scope.

---

## Summary

| Category | Count |
|----------|-------|
| Validated | 13 |
| Invalidated | 1 (A1, minor nuance only) |
| Unverifiable | 0 |

All 12 fix approaches in the spec are sound. No redesign is required. The single A1 nuance (a second lazy import of `run_uninstall` in `app.py`) does not affect the DaemonLoop extraction approach. No Q4 conflict diagram is needed.

The spec is ready for planning.
