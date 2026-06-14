# Plan: Phase 7.5 -- Issue Resolve

_From spec: docs/2_specs/phase7_5_issue_resolve.md_
_Research: docs/3_research/phase7_5_issue_resolve.md_
_Last updated: 2026-06-14_

## Architecture

### Q3 -- Why build it this way (constraint map)

```
WAVE 1 — DaemonLoop Class Extract
==================================

  Constraint: config injection pattern
    The daemon loads DaemonConfig from YAML + env, passes it as a parameter.
    Every callback in _run_with_stop (cli.py:268-509) captures cfg via closure
    scope.  A class can hold cfg as self._cfg instead, making each method
    independently constructible and testable.

  Constraint: async run pattern (C-09)
    asyncio.run() wraps the entire sync engine.  The _run_with_stop function
    is the coroutine entry point.  Extracting it into DaemonLoop.run() must
    preserve the same asyncio.run(_run_with_stop(...)) call site in both
    the start Click command and app.py's _engine_thread.

  Constraint: app supervisor contract
    daemon/app.py:23 imports _run_with_stop at module level.  app.py:122
    calls asyncio.run(_run_with_stop(cfg, config_path, stop_event)).
    The class extraction must either: (a) re-export a compatibility wrapper,
    or (b) update the import to DaemonLoop.  Option (b) is cleaner —
    app.py:35 also lazy-imports run_uninstall, which stays untouched.

  Constraint: shared state as class (not nonlocal)
    _run_with_stop uses 1 nonlocal capture (_move_timer, line 328) and
    closure-captured locals (cfg, client, cache, move_buffer, loop).
    Promoting these to self._ attributes eliminates the nonlocal and
    closure capture, making each callback a clean instance method.


WAVE 2 — Daemon Fixes (8 items)
================================

  Constraint: reuse retry helper
    daemon/_http_retry.py:33-37 already provides retry_with_backoff(client,
    config, request_fn) -> Result[httpx.Response].  Finding #2 wraps
    _fetch_cloud_state's bare client.get() call (scanner.py:442) in this
    helper.  No new retry mechanism needed.

  Constraint: config-driven ignore list
    DaemonConfig.ignore_patterns (config.py:44-56) is the single source of
    truth for which directories/files to skip.  Finding #3 derives _SKIP_DIRS
    from non-glob entries in this list — no hardcoded directory names.

  Constraint: HTTP client defaults (httpx convention)
    httpx.AsyncClient accepts default headers= at construction time.  All
    requests through that client inherit them automatically.  Finding #5
    sets headers={"Authorization": ...} once at construction, removing 6
    duplicated per-call headers= from uploader.py, event_reporter.py,
    scanner.py, and cli.py.

  Constraint: existing interfaces preserved
    All 8 Wave 2 fixes change internals only.  No function signatures,
    return types, or public APIs change.  Caller code (test or prod) that
    constructs DaemonConfig with scan_batch_size= must be updated when
    that field is removed (M1), but that is the only breaking change.


WAVE 3 — Capture + Prompt (3 items)
=====================================

  Constraint: best-effort indexing (non-fatal)
    Both indexing blocks (capture.py:225-250 and 441-466) use try/except
    to catch and log errors without propagating.  The extracted helper
    _best_effort_index must preserve this pattern exactly.

  Constraint: prompts as config (C-07)
    Finding C5 adds {{mime_type}} to prompts/describe_image.yaml.  No
    Python code changes — the prompt loader already renders the variable
    (capture.py:407 calls prompt.render(mime_type=mime_type)).

  Constraint: Result type pattern (C-12)
    attach_summary (documents.py:494-555) returns Result[int].  Finding
    M11 checks this return and logs on Failure.  The capture function
    still returns Success — attach_summary failure is non-fatal.
```

### Approach

3 waves, 12 fixes, ordered by dependency. Wave 1 (structural extract) must complete before Wave 2 (daemon fixes land on the extracted class). Wave 3 (capture + prompt) is independent -- could run in parallel with Wave 2 but sequenced for cleaner review.

---

## Phase 1 -- Wave 1: DaemonLoop Class Extract

### Goal

Extract the 242-line `_run_with_stop` function (cli.py:268-509) and its 9 nested closures into a `DaemonLoop` class. Each callback becomes an independently testable method. Update the one external caller (app.py:23,122).

### Files to change (with exact line numbers from actual code)

| File | Lines | Change |
|------|-------|--------|
| `src/daemon/cli.py` | 268-509 | Replace `_run_with_stop` function with `DaemonLoop` class. Keep `_run_with_stop` as a thin wrapper that constructs and runs `DaemonLoop` for backward compatibility. |
| `src/daemon/cli.py` | 232-263 | Move `_periodic_reconcile` into `DaemonLoop` as a method (it accesses cfg, client, cache -- all class state). |
| `src/daemon/app.py` | 23 | Update import: `from daemon.cli import _run_with_stop` stays (wrapper preserved). No change needed if wrapper exists; alternatively import `DaemonLoop` directly. |
| `tests/test_daemon/test_cli.py` | Throughout (1989 lines) | Update test setup for any tests that mock `_run_with_stop` internals. Most tests use CliRunner which exercises the function via the `start` command -- these should work unchanged. |

### Steps

1. **Create `DaemonLoop` class** in `daemon/cli.py` (insert above `_run_with_stop`):
   - `__init__(self, cfg: DaemonConfig, config_path: Path, stop_event: asyncio.Event | None = None)` -- stores cfg, config_path, stop_event. Initializes `_move_timer`, `_move_timer_lock`, `_periodic_task` to None.
   - `async def run(self)` -- the body of current `_run_with_stop` (lines 284-509), but using `self._cfg`, `self._cache`, etc. instead of closure-captured locals.

2. **Move closures to methods**:
   - `_refresh_move_timer` (cli.py:326-337) -> `def _refresh_move_timer(self)`
   - `_on_move_window_expired` (cli.py:339-352) -> `def _on_move_window_expired(self)`
   - `_handle_expired` (cli.py:344-351) -> inline in `_on_move_window_expired` or separate async method
   - `_on_create_or_modify` (cli.py:354-427) -> `def _on_create_or_modify(self, vp: str)`
   - `_on_move` (cli.py:429-449) -> `def _on_move(self, old_vp: str, new_vp: str)`
   - `_on_delete` (cli.py:451-466) -> `def _on_delete(self, vp: str)`

3. **Replace `nonlocal _move_timer`** (line 328) with `self._move_timer` attribute access.

4. **Replace closure-captured variables** with `self._` attributes:
   - `cfg` -> `self._cfg`
   - `client` -> `self._client`
   - `cache` -> `self._cache`
   - `move_buffer` -> `self._move_buffer`
   - `loop` -> `self._loop`
   - `candidate_deletes` -> `self._candidate_deletes`
   - `sweep_confirmations` -> `self._sweep_confirmations`

5. **Keep `_run_with_stop` as a thin wrapper** (preserves app.py import and test compatibility):
   ```python
   async def _run_with_stop(
       cfg: DaemonConfig,
       config_path: Path,
       stop_event: asyncio.Event | None = None,
   ) -> None:
       daemon_loop = DaemonLoop(cfg, config_path, stop_event)
       await daemon_loop.run()
   ```

6. **Verify `_periodic_reconcile`** (cli.py:232-263): move into `DaemonLoop` as `async def _periodic_reconcile(self)` since it uses `cfg`, `client`, `cache`, `candidate_deletes`, `sweep_confirmations`.

7. **Verify `app.py` still works**: `app.py:122` calls `asyncio.run(_run_with_stop(cfg, config_path, stop_event))`. The wrapper preserves this call site unchanged. `app.py:35` lazy-imports `run_uninstall` -- unaffected.

### Tests to add/modify

- **Existing tests**: The ~1989-line `test_cli.py` exercises the daemon via `CliRunner` and `_run_with_stop`. Since the wrapper is preserved, most tests should pass without changes. Run full suite to verify.
- **New tests**: Add `TestDaemonLoopInit` class:
  - `test_daemon_loop_stores_config` -- verify `DaemonLoop.__init__` stores cfg, config_path, stop_event.
  - `test_daemon_loop_run_delegates` -- verify `_run_with_stop` wrapper constructs and runs `DaemonLoop`.
- **Patch target updates**: Any tests that patch `daemon.cli._on_create_or_modify` (if such patches exist as module-level mocks) must be updated to patch the class method path.

### Behavior IDs covered

- P7H-FIX-07

### Done when

- [x] `DaemonLoop` class exists with `__init__` + `run` + all callback methods.
- [x] `_run_with_stop` is a 3-line wrapper that constructs and runs `DaemonLoop`.
- [x] `_periodic_reconcile` is a method on `DaemonLoop` (no free-floating function with 5 params).
- [x] `app.py` import and call site unchanged (wrapper compatibility).
- [x] `run_uninstall` (cli.py:129-183) untouched (separate export, not part of sync engine).
- [x] `start` command body (cli.py:515-534) unchanged.
- [x] All existing daemon tests pass (`uv run pytest tests/test_daemon/`).

---

## Phase 2 -- Wave 2: Daemon Fixes (8 items)

All 8 items are independent within this phase. They are listed in a single phase because each is a small, self-contained change. Implementation order within the phase does not matter, except that item 2.3 (#3 prune) should land before item 2.5 (#7 delete twin) since the twin function should have pruning applied before it is deleted.

### Goal

Fix 8 daemon issues across 5 files: add retry to cloud fetch, prune ignored dirs during vault walk, add exception logging to async callbacks, centralize auth headers, delete dead twin function, remove dead config field, fix move-event skip logic, and widen periodic interval type.

### Files to change (with exact line numbers from actual code)

| File | Lines | Change | Finding |
|------|-------|--------|---------|
| `src/daemon/scanner.py` | 429-475 | Wrap `_fetch_cloud_state` HTTP call in `retry_with_backoff`. Return `None` on exhaustion. | #2 |
| `src/daemon/scanner.py` | 92 | In `scan()`, check for `None` return from `_fetch_cloud_state`. If `None`, log warning and return zero-count `ScanResult`. | #2 |
| `src/daemon/scanner.py` | 490, 544 | In both `_build_disk_state` and `_build_disk_entries`, rename `_dirnames` to `dirnames` and add `dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]`. | #3 |
| `src/daemon/scanner.py` | top of file | Add `_SKIP_DIRS` set derived from non-glob patterns in the default ignore list: `_SKIP_DIRS = {".git", ".obsidian", ".trash", ".stversions"}`. | #3 |
| `src/daemon/scanner.py` | 478-520 | Delete entire `_build_disk_state` function. | #7 |
| `src/daemon/scanner.py` | 97-99 | Update `cache is None` path: call `_build_disk_entries(config)` instead, unpack as `_entries, disk_state, unreadable`. | #7 |
| `src/daemon/scanner.py` | 439 | Remove `headers={"Authorization": ...}` from `_fetch_cloud_state` (auth now on client). | #5 |
| `src/daemon/cli.py` | 352, 427, 449, 466 | At all 4 `asyncio.run_coroutine_threadsafe` sites (now class methods after Wave 1), capture returned Future and call `fut.add_done_callback(self._log_future_exception)`. | #4 |
| `src/daemon/cli.py` | new method | Add `_log_future_exception(self, fut)` method to `DaemonLoop` that checks `fut.exception()` and logs at error level. | #4 |
| `src/daemon/cli.py` | 294 (now in DaemonLoop.run) | Set `headers={"Authorization": f"Bearer {cfg.api_key}"}` on `httpx.AsyncClient` constructor. | #5 |
| `src/daemon/cli.py` | 98 | Leave `status` command's auth header in place -- it creates a separate short-lived client (confirmed: cli.py:94). | #5 |
| `src/daemon/uploader.py` | 102, 149 | Remove `headers={"Authorization": ...}` from both `_request()` closures. | #5 |
| `src/daemon/event_reporter.py` | 60, 97 | Remove `headers={"Authorization": ...}` from both `_request()` closures. | #5 |
| `src/daemon/config.py` | 60 | Delete `scan_batch_size: int = Field(default=50, ge=1)`. | M1 |
| `src/daemon/config.py` | 66 | Change `periodic_interval_seconds: int` to `periodic_interval_seconds: float`. | M8 |
| `src/daemon/watcher.py` | 199 | Replace `if self._should_skip(src_path) or self._should_skip(dst_path): return` with new logic (see steps). | M5 |
| `tests/test_daemon/test_config.py` | 199-200 | Delete `test_scan_batch_size_default`. | M1 |
| `tests/test_daemon/test_config.py` | 388-400 | Remove `scan_batch_size: 100` from YAML round-trip test, remove assertion `assert cfg.scan_batch_size == 100`. | M1 |

### Steps

**2.1 -- Cloud fetch retry (#2, P7H-FIX-01)**

1. In `scanner.py`, change `_fetch_cloud_state` return type to `dict[str, str | None] | None`.
2. Replace the bare `client.get()` call (line 442) with a `retry_with_backoff` wrapper:
   ```python
   async def _request() -> httpx.Response:
       return await client.get(url, headers=headers)
   match await retry_with_backoff(client, config, _request):
       case Success(value=response):
           pass  # continue to parse response
       case Failure() as f:
           _log.warning("Cloud state fetch failed after retries: %s", f.error)
           return None
   ```
3. Remove the existing `try/except (HTTPStatusError, RequestError)` block (lines 441-446) -- retry_with_backoff handles this.
4. In `scan()` (line 92), add a guard after `_fetch_cloud_state`:
   ```python
   cloud_state = await _fetch_cloud_state(config, client)
   if cloud_state is None:
       _log.warning("Scan aborted — cloud unreachable after retries")
       return ScanResult()
   ```
5. Add `from daemon._http_retry import retry_with_backoff` to scanner.py imports.

**2.2 -- Ignored directory pruning (#3, P7H-FIX-02)**

1. Add a module-level helper at top of `scanner.py`:
   ```python
   def _skip_dirs_from_patterns(patterns: list[str]) -> set[str]:
       """Extract non-glob directory names for os.walk pruning."""
       return {p for p in patterns if not set(p) & {"*", "?", "[", "]"}}
   ```
2. In `_build_disk_state` (line 490), rename `_dirnames` to `dirnames` and add pruning:
   ```python
   for dirpath_str, dirnames, filenames in os.walk(str(vault_root)):
       skip_dirs = _skip_dirs_from_patterns(ignore_patterns)
       dirnames[:] = [d for d in dirnames if d not in skip_dirs]
   ```
3. In `_build_disk_entries` (line 544), apply the same change.
4. Note: `_build_disk_state` will be deleted in step 2.5, but pruning is applied first so that `_build_disk_entries` gets it (and if deletion order changes, both functions are correct).

**2.3 -- Async callback exception logging (#4, P7H-FIX-03)**

1. Add a `_log_future_exception` method to `DaemonLoop`:
   ```python
   def _log_future_exception(self, fut: asyncio.Future) -> None:
       try:
           exc = fut.exception()
       except asyncio.CancelledError:
           return
       if exc is not None:
           _log.error("Async callback failed", exc_info=exc)
   ```
2. At all 4 `asyncio.run_coroutine_threadsafe` call sites (now in DaemonLoop methods), capture and attach:
   ```python
   fut = asyncio.run_coroutine_threadsafe(_handle(), self._loop)
   fut.add_done_callback(self._log_future_exception)
   ```

**2.4 -- Auth header centralization (#5, P7H-FIX-08)**

1. In `DaemonLoop.run()`, modify the `httpx.AsyncClient` construction (was cli.py:294):
   ```python
   async with httpx.AsyncClient(
       timeout=30,
       headers={"Authorization": f"Bearer {self._cfg.api_key}"},
   ) as client:
   ```
2. In `uploader.py`, remove `headers={"Authorization": ...}` from both `_request()` closures (lines 102 and 149).
3. In `event_reporter.py`, remove `headers={"Authorization": ...}` from both `_request()` closures (lines 60 and 97).
4. In `scanner.py`, remove `headers = {"Authorization": ...}` from `_fetch_cloud_state` (line 439) and the `headers=headers` kwarg from the `client.get()` call (now handled by the retry wrapper's `_request()` closure).
5. Leave `status` command (cli.py:94-98) unchanged -- separate short-lived client.
6. Leave `connection_check.py:38` unchanged -- separate client with `key` parameter.

**2.5 -- Dead twin function deletion (#7, P7H-FIX-10)**

1. Delete the entire `_build_disk_state` function (scanner.py:478-520, ~43 lines).
2. In `scan()`, update the `cache is None` path (line 97-99):
   ```python
   if cache is None:
       _entries, disk_state, unreadable = _build_disk_entries(config)
       return await _scan_2way(config, client, cloud_state, disk_state, unreadable)
   ```

**2.6 -- Dead config field removal (M1, P7H-FIX-11)**

1. Delete `scan_batch_size: int = Field(default=50, ge=1)` from `config.py:60`.
2. In `test_config.py`:
   - Delete `test_scan_batch_size_default` (lines 199-200).
   - In `test_all_optional_fields_can_be_set_in_yaml` (line 388): remove `"scan_batch_size: 100\n"` from the YAML string and remove `assert cfg.scan_batch_size == 100` from the assertions.

**2.7 -- Move-event skip rule fix (M5, P7H-FIX-04)**

1. In `watcher.py:194-204`, replace the skip logic in `on_moved`:
   ```python
   def on_moved(self, event) -> None:
       if event.is_directory:
           return
       src_path = Path(str(event.src_path))
       dst_path = Path(str(event.dest_path))
       src_skip = self._should_skip(src_path)
       dst_skip = self._should_skip(dst_path)

       if src_skip and dst_skip:
           return  # both ignored -- drop silently
       if dst_skip:
           # Moving INTO ignored area -- treat as delete of source
           old_vp = self._to_vault_path(src_path)
           self._on_delete(old_vp)
           return
       if src_skip:
           # Moving OUT OF ignored area -- treat as create at destination
           new_vp = self._to_vault_path(dst_path)
           self._debounce(f"create:{new_vp}", self._on_create, (new_vp,))
           return
       # Neither skipped -- normal move
       old_vp = self._to_vault_path(src_path)
       new_vp = self._to_vault_path(dst_path)
       self._on_move(old_vp, new_vp)
   ```

**2.8 -- Periodic interval type widening (M8, P7H-FIX-12)**

1. In `config.py:66`, change:
   ```python
   periodic_interval_seconds: float = Field(default=21600, ge=0)
   ```

### Tests to add/modify

**#2 (retry)**:
- Modify `TestFetchCloudState` in `test_scanner.py`:
  - Update existing tests to account for retry behavior (responses may be retried).
  - Add `test_fetch_cloud_state_returns_none_after_retry_exhaustion` -- mock client to raise `httpx.TimeoutException` on every attempt. Assert return is `None`.
  - Add `test_scan_aborts_on_none_cloud_state` -- mock `_fetch_cloud_state` to return `None`. Assert `scan()` returns `ScanResult(0,0,0,0,0)`.

**#3 (prune)**:
- Add `TestDirectoryPruning` in `test_scanner.py`:
  - Create a vault with `.git/objects/pack/...` nested deep. Verify `_build_disk_entries` does NOT list files under `.git`.
  - Verify files under non-ignored directories are still listed.

**#4 (callbacks)**:
- Add `TestFutureExceptionLogging` in `test_cli.py`:
  - Create a mock Future with `exception()` returning a `TypeError`. Call `_log_future_exception`. Assert error-level log message is emitted.
  - Create a mock Future with `exception()` returning `None`. Assert no error log.

**#5 (auth)**:
- Verify existing `test_uploader.py` and `test_event_reporter.py` tests still pass (they mock the HTTP client -- default headers are transparent to callers).
- Add one integration-style test verifying `httpx.AsyncClient` is constructed with default auth headers.

**#7 (twin)**:
- Remove or update `TestBuildDiskState` class in `test_scanner.py` (the function is deleted). Tests for `_build_disk_entries` already exist and cover the same logic.
- Verify `test_scan_2way_*` tests still pass with the updated caller.

**M1 (dead field)**:
- Delete `test_scan_batch_size_default` (test_config.py:199-200).
- Remove `scan_batch_size: 100` from YAML round-trip test (test_config.py:388-400).

**M5 (move skip)**:
- Add 4 new tests to `TestOnMoved` in `test_watcher.py`:
  - `test_move_from_ignored_to_watched_fires_create` -- move from `.git/file` to `Projects/file`. Assert `on_create` callback fires with destination path.
  - `test_move_from_watched_to_ignored_fires_delete` -- move from `Projects/file` to `.git/file`. Assert `on_delete` callback fires with source path.
  - `test_move_both_ignored_is_silent` -- move from `.git/a` to `.obsidian/b`. Assert no callback fires.
  - `test_move_neither_ignored_fires_move` -- existing behavior, keep current test.

**M8 (float type)**:
- Add `test_periodic_interval_seconds_accepts_float` in `test_config.py`:
  ```python
  cfg = DaemonConfig(
      vault_root=tmp_path,
      cloud_endpoint="http://localhost:8080",
      api_key="sk-test",
      periodic_interval_seconds=3600.5,
  )
  assert cfg.periodic_interval_seconds == 3600.5
  ```

### Behavior IDs covered

- P7H-FIX-01 (retry)
- P7H-FIX-02 (prune)
- P7H-FIX-03 (callbacks)
- P7H-FIX-08 (auth)
- P7H-FIX-10 (dead twin)
- P7H-FIX-11 (dead config)
- P7H-FIX-04 (move skip)
- P7H-FIX-12 (float type)

### Done when

- [x] `_fetch_cloud_state` retries via `retry_with_backoff`. On exhaustion, returns `None`. `scan()` aborts with warning on `None`.
- [x] `os.walk` loops in scanner prune `.git`, `.obsidian`, `.trash`, `.stversions` via `dirnames[:]` in-place modification.
- [x] All 4 `asyncio.run_coroutine_threadsafe` calls in DaemonLoop have `.add_done_callback(self._log_future_exception)`.
- [x] Auth header `{"Authorization": ...}` appears ONLY in `httpx.AsyncClient` constructor (DaemonLoop.run), `status` command (separate client), and `connection_check.py` (separate client, function param). Removed from uploader.py (2), event_reporter.py (2), scanner.py (1).
- [x] `_build_disk_state` function no longer exists. `cache=None` path calls `_build_disk_entries`.
- [x] `scan_batch_size` does not appear anywhere in codebase (grep clean).
- [x] `on_moved` in watcher: move from ignored to watched fires create; watched to ignored fires delete; both ignored drops silently; neither ignored fires move.
- [x] `periodic_interval_seconds` accepts `float` values.
- [x] All daemon tests pass (`uv run pytest tests/test_daemon/`).

---

## Phase 3 -- Wave 3: Capture + Prompt (3 items)

### Goal

Eliminate duplicated indexing blocks in the capture pipeline, make `attach_summary` failures visible, and use the declared `mime_type` variable in the vision prompt.

### Files to change (with exact line numbers from actual code)

| File | Lines | Change | Finding |
|------|-------|--------|---------|
| `src/pipelines/capture.py` | 225-250 | Replace text-path indexing block with `_best_effort_index(...)` call. | #6 |
| `src/pipelines/capture.py` | 441-466 | Replace binary-path indexing block with `_best_effort_index(...)` call. | #6 |
| `src/pipelines/capture.py` | new function | Add `_best_effort_index(vault_path, title, summary, body, db_path)` private helper. | #6 |
| `src/pipelines/capture.py` | 217-222 | Capture `attach_summary` return value, log on `Failure`. | M11 |
| `src/pipelines/capture.py` | 432-438 | Capture `attach_summary` return value (binary path), log on `Failure`. | M11 |
| `src/prompts/describe_image.yaml` | 9-13 | Add `This file is a {{mime_type}}.` before the description request. | C5 |

### Steps

**3.1 -- Shared indexing helper (#6, P7H-FIX-09)**

1. Add a private helper function in `capture.py` (insert before `capture_upload` or after the audit helpers):
   ```python
   def _best_effort_index(
       vault_path: str,
       title: str,
       summary: str,
       body: str,
       db_path: Path | None,
   ) -> None:
       """Index keywords and embeddings, logging but never propagating errors."""
       try:
           from retrieval.keyword import index_keywords

           index_keywords(
               vault_path=vault_path,
               title=title,
               summary=summary or "",
               body=body,
               db_path=db_path,
           )
       except Exception:
           logger.exception("capture.index_keywords_failed")

       try:
           from retrieval.embeddings import index_embedding

           index_embedding(
               vault_path=vault_path,
               title=title,
               note_type=None,
               tags=[],
               summary=summary or "",
               db_path=db_path,
           )
       except Exception:
           logger.exception("capture.index_embedding_failed")
   ```

2. Replace text-path indexing block (lines 225-250) with:
   ```python
   _best_effort_index(vault_path, title, summary, extracted_text, db_path)
   ```

3. Replace binary-path indexing block (lines 441-466) with:
   ```python
   _best_effort_index(vault_path, title, summary, summary or "", db_path)
   ```

**3.2 -- Attach summary failure logging (M11, P7H-FIX-06)**

1. Text path (line 217-222): replace the bare call with:
   ```python
   attach_result = documents.attach_summary(
       vault_path=vault_path,
       summary=summary,
       title=title,
       db_path=db_path,
   )
   match attach_result:
       case Failure() as af:
           logger.warning(
               "capture.attach_summary_failed vault_path=%s error=%s",
               vault_path,
               af.error,
           )
   ```

2. Binary path (line 432-438): replace the bare call with:
   ```python
   attach_result = documents.attach_summary(
       vault_path=vault_path,
       summary=summary,
       title=title,
       full_body=summary,
       db_path=db_path,
   )
   match attach_result:
       case Failure() as af:
           logger.warning(
               "capture.binary.attach_summary_failed vault_path=%s error=%s",
               vault_path,
               af.error,
           )
   ```

**3.3 -- Vision prompt mime_type (C5, P7H-FIX-05)**

1. In `prompts/describe_image.yaml`, add to the `user:` template (before the description request):
   ```yaml
   user: |
     This file is a {{mime_type}}.

     Please describe this image in detail. Provide a thorough, searchable description of everything visible in the image.

     After the description, on the VERY LAST line, provide a short descriptive title.  The title must be ONE line prefixed with exactly "Title: " and must be a concise, human-readable phrase.  Example:

     Title: Dashboard showing Q2 revenue metrics
   ```

### Tests to add/modify

**#6 (helper)**:
- Add `TestBestEffortIndex` in `tests/test_pipelines/test_capture.py`:
  - `test_best_effort_index_calls_both_indexers` -- mock `index_keywords` and `index_embedding`, call `_best_effort_index`. Assert both are called with correct args.
  - `test_best_effort_index_logs_keyword_error_without_propagating` -- mock `index_keywords` to raise. Assert logger.exception called, no exception propagated.
  - `test_best_effort_index_logs_embedding_error_without_propagating` -- same for `index_embedding`.
  - `test_best_effort_index_body_differs_between_paths` -- verify that the text path passes `extracted_text` as body and binary path passes `summary or ""`.

**M11 (attach_summary)**:
- Add `test_attach_summary_failure_logged_text_path` in `test_capture.py`:
  - Mock `documents.attach_summary` to return `Failure(error="db locked")`.
  - Call `capture_upload` with text. Assert warning log contains "attach_summary_failed".
  - Assert overall result is still `Success` (non-fatal).
- Add `test_attach_summary_failure_logged_binary_path` -- same for binary path.

**C5 (prompt)**:
- Add `test_describe_image_prompt_includes_mime_type` in test for prompt rendering:
  - Load `describe_image` from PROMPTS. Render with `mime_type="image/png"`. Assert `"image/png"` appears in the rendered user prompt.

### Behavior IDs covered

- P7H-FIX-09 (indexing helper)
- P7H-FIX-06 (attach_summary check)
- P7H-FIX-05 (mime_type prompt)

### Done when

- [x] Two 26-line indexing blocks replaced by single-line `_best_effort_index(...)` calls. Zero duplication.
- [x] `_best_effort_index` preserves lazy imports, try/except, logger.exception pattern.
- [x] Both `attach_summary` call sites check return value. `Failure` is logged at warning level. Capture still returns `Success`.
- [x] `describe_image.yaml` user prompt contains `{{mime_type}}`. Rendering with `mime_type="image/png"` produces `"This file is a image/png."` in the prompt text.
- [x] All capture tests pass (`uv run pytest tests/test_pipelines/test_capture.py`).

---

## Open questions

None. All 12 approaches are locked per the design doc. All 14 assumptions (A1-A14) were verified in the research doc, with only A1 having a minor nuance (a second lazy import of `run_uninstall` in app.py, outside sync engine scope) that does not affect any approach.

---

## Out of scope

| Item | Reason | Tracked where |
|------|--------|---------------|
| C1 -- multipart upload reads full body into memory | Deferred to Phase 9 (streaming upload) | Spec out-of-scope |
| C4 -- missing UNIQUE constraint on vault_path | Deferred to Phase 8 (migration concern) | Spec out-of-scope |
| M2 -- structlog vs stdlib Logger inconsistency | Acceptable for MVP -- daemon uses structlog, scanner uses stdlib | Spec out-of-scope |
| M3 -- _periodic_reconcile logs at info/exception inconsistently | Acceptable style variation | Spec out-of-scope |
| M4 -- scan_batch_size unused but "might be needed later" | False positive -- dead code removed as M1 | Spec out-of-scope |
| M6 -- hardcoded retry delays (1s base, 2x multiplier) | Acceptable for MVP, internal retry params | Spec out-of-scope |
| M7 -- event type string enum ("deleted", "moved") | Acceptable -- only 2 sites | Spec out-of-scope |
| M9 -- vault-relative path computation uses different methods in scanner vs watcher | Acceptable -- both produce identical results | Spec out-of-scope |
| M10 -- MoveBuffer.park_delete returns None not Result | Acceptable -- internal helper | Spec out-of-scope |
| C2 -- sync blob blocks event loop | Tracked for Phase 9 | Spec out-of-scope |
| C3 -- vision model not in AgentBase config | Separate concern | Spec out-of-scope |
| C6 -- capture_upload has too many parameters | Cosmetic, no functional impact | Spec out-of-scope |
| C7 -- BlobStore.put is synchronous, called from async context | Separate concern (Phase 9) | Spec out-of-scope |
| C8 -- describe_image prompt could be more specific | False positive per research | Spec out-of-scope |
| Local-only mode watcher tests | test_watcher.py (vault/watcher) deleted on cloud-native | Separate decision |
