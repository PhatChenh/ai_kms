# Nuclear Review — Deferred Findings

> Branch: `cloud-native` | Reviewed: 2026-06-14
> Scope: All implementation on cloud-native tree (daemon, cloud API, capture, blobs)
> Context: 3 parallel review agents (daemon, cloud/API, test quality) + cross-cutting analysis
> Pre/post test: 1365→1366 passed, 0 regressions

7 critical+important issues were **fixed in-session** (see git diff).
This document captures the **deferred important findings** that need design decisions.

---

## 1. cli.py god function — extract DaemonLoop class

**File:** `src/daemon/cli.py:188-432`
**Severity:** IMPORTANT (structural)

The `start` command is a 245-line async function with 9 nested closures, 4 `nonlocal` captures, mixed sync/async threading, and move-timer lifecycle spread across 3 locations. `_on_create_or_modify` alone is 73 lines with 5 nesting levels.

**Diagnosis:** This is the single hardest function in the daemon package to reason about or test. The callbacks (`_on_create_or_modify`, `_on_move`, `_on_delete`, `_refresh_move_timer`, `_on_move_window_expired`) all close over `cfg`, `client`, `cache`, `move_buffer`, `loop`, `_move_timer`, and `_move_timer_lock`. Testing requires elaborate mock scaffolding (test_cli.py is 1850 lines, 303 mock-related lines).

**Recommended fix:** Extract a `DaemonLoop` class that owns the lifecycle:

```python
class DaemonLoop:
    def __init__(self, cfg, client, cache, move_buffer): ...
    async def on_create_or_modify(self, vp: str): ...
    async def on_move(self, old_vp: str, new_vp: str): ...
    async def on_delete(self, vp: str): ...
    async def start(self): ...
    async def stop(self): ...
```

The `start` Click command becomes ~10 lines. Each callback becomes independently testable without mocking asyncio internals.

**Risk:** Medium refactor — touches the core daemon runtime loop. Needs careful testing.

---

## 2. _fetch_cloud_state returns {} on failure → full vault re-upload

**File:** `src/daemon/scanner.py:384-389`
**Severity:** IMPORTANT (data safety)

```python
except (httpx.HTTPStatusError, httpx.RequestError) as exc:
    _log.error("Failed to fetch cloud state: %s", exc)
    return {}  # ← caller treats this as "cloud has zero documents"
```

**Diagnosis:** If the cloud is unreachable (network hiccup, transient auth error, 500), the scan function sees an empty cloud manifest and uploads every file in the vault. This is also the only HTTP call in the daemon that does NOT use `retry_with_backoff`, making it the weakest link in the retry chain.

**Recommended fix:** Either:
- (a) Return a sentinel (e.g., `None`) and have the caller abort the scan with a warning, OR
- (b) Wrap the call in `retry_with_backoff` and only return `{}` after all retries are exhausted, OR
- (c) Raise an exception so the caller can decide (scan aborts, periodic reconcile retries next interval)

Option (b) is simplest — add retry, then the empty-dict fallback is last-resort.

**Risk:** Low — adding retry is mechanical. The sentinel approach needs caller changes.

---

## 3. os.walk never prunes ignored directories

**File:** `src/daemon/scanner.py:433,482` (both `_build_disk_state` and `_build_disk_entries`)
**Severity:** IMPORTANT (performance)

```python
for dirpath_str, _dirnames, filenames in os.walk(str(vault_root)):
    # _dirnames is never pruned — os.walk descends into .git, .obsidian, etc.
```

**Diagnosis:** `should_skip_path()` filters individual files, but `os.walk` still recurses into every ignored directory tree. On a vault with `.git` (common — people version-control Obsidian vaults), this reads thousands of git objects pointlessly. On a large vault with `.obsidian` plugins, this can add seconds to every scan.

**Recommended fix:** Prune `_dirnames` in-place before the inner loop:

```python
for dirpath_str, dirnames, filenames in os.walk(str(vault_root)):
    dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
    # ... rest of loop
```

Where `_SKIP_DIRS` is derived from `config.ignore_patterns` (the non-glob patterns like `.git`, `.obsidian`, `.trash`).

**Risk:** Low — purely additive optimization. Needs a test that .git contents are not walked.

---

## 4. Fire-and-forget async futures (exceptions vanish silently)

**File:** `src/daemon/cli.py:271,346,368,385`
**Severity:** IMPORTANT (observability)

```python
asyncio.run_coroutine_threadsafe(_handle(), loop)
# ← Future returned but never checked. If _handle() raises, exception is lost.
```

**Diagnosis:** All 4 watcher callbacks (`_on_create_or_modify`, `_on_move`, `_on_delete`, `_on_move_window_expired`) schedule async coroutines but discard the Future. If any coroutine raises an unexpected exception (not a Result Failure, but a genuine RuntimeError or TypeError), it vanishes silently. The daemon appears healthy but is silently dropping events.

**Recommended fix:** Add a done callback:

```python
def _log_future_exception(fut):
    if fut.exception() is not None:
        _log.error("watcher callback failed: %s", fut.exception())

fut = asyncio.run_coroutine_threadsafe(_handle(), loop)
fut.add_done_callback(_log_future_exception)
```

**Risk:** Very low — 4 lines added, no behavior change on success path.

---

## 5. Auth header duplicated in 6 places across 4 files

**Files:** `event_reporter.py` (2x), `scanner.py` (1x), `uploader.py` (2x), `cli.py` (1x via httpx)
**Severity:** IMPORTANT (maintenance)

`{"Authorization": f"Bearer {config.api_key}"}` is constructed at each HTTP call site.

**Diagnosis:** If the auth scheme ever changes (custom header, token refresh, mTLS), 6 call sites across 4 files need updating. Also a minor DRY violation.

**Recommended fix:** Set default headers on the `httpx.AsyncClient` at construction in `cli.py`:

```python
async with httpx.AsyncClient(
    timeout=30,
    headers={"Authorization": f"Bearer {cfg.api_key}"},
) as client:
```

Then remove the `headers=` kwarg from every `retry_with_backoff` / direct HTTP call.

**Risk:** Low — mechanical removal. Need to verify no call site overrides headers.

---

## 6. Duplicate indexing code in capture.py (text vs binary paths)

**File:** `src/pipelines/capture.py:243-268` and `src/pipelines/capture.py:459-484`
**Severity:** IMPORTANT (DRY violation)

Both the text capture success path and the binary vision-describe success path have identical 26-line blocks:

```python
try:
    from retrieval.keyword import index_keywords
    index_keywords(vault_path=..., title=..., summary=..., body=..., db_path=...)
except Exception:
    logger.exception("capture.*.index_keywords_failed")

try:
    from retrieval.embeddings import index_embedding
    index_embedding(vault_path=..., title=..., note_type=None, tags=[], summary=..., db_path=...)
except Exception:
    logger.exception("capture.*.index_embedding_failed")
```

The only difference is the `body` argument (text path uses `extracted_text`, binary path uses `summary`).

**Recommended fix:** Extract a helper:

```python
def _best_effort_index(
    vault_path: str, title: str, summary: str, body: str, db_path: Path | None
) -> None:
    """Best-effort keyword + embedding indexing. Logs and swallows errors."""
    try:
        from retrieval.keyword import index_keywords
        index_keywords(vault_path=vault_path, title=title, summary=summary or "", body=body, db_path=db_path)
    except Exception:
        logger.exception("capture.index_keywords_failed")
    try:
        from retrieval.embeddings import index_embedding
        index_embedding(vault_path=vault_path, title=title, note_type=None, tags=[], summary=summary or "", db_path=db_path)
    except Exception:
        logger.exception("capture.index_embedding_failed")
```

**Risk:** Very low — pure extraction, no behavior change.

---

## 7. _build_disk_state is a near-twin of _build_disk_entries

**File:** `src/daemon/scanner.py:421-460` vs `src/daemon/scanner.py:463-515`
**Severity:** IMPORTANT (dead code / DRY)

Both functions walk the vault, compute vault_path (NFC-normalized), read bytes, SHA-256 hash. The only difference: `_build_disk_entries` also calls `stat()` and returns `{hash, size, mtime}` entries alongside the hash-only dict.

`_build_disk_state` is only called in the `cache=None` backward-compat (A1) path.

**Recommended fix:** Delete `_build_disk_state`. Have the A1 path call `_build_disk_entries` and ignore the extra `entries` dict:

```python
if cache is None:
    _entries, disk_state, unreadable = _build_disk_entries(config)
    return await _scan_2way(config, client, cloud_state, disk_state, unreadable)
```

Saves ~40 lines and eliminates the risk of the two functions drifting apart.

**Risk:** Very low — the extra stat() call per file is negligible overhead.

---

## Minor findings (noted, no action needed now)

| # | Finding | File | Notes |
|---|---------|------|-------|
| M1 | Dead `scan_batch_size` config field | config.py:60 | Defined, never used anywhere |
| M2 | Float mtime equality in cache check | cli.py:287 | Fast-path only; falls through to hash |
| M3 | Double file read (bail-early hash then extract) | cli.py:292+305 | Acceptable trade-off |
| M4 | Debounce timers not marked daemon threads | watcher.py:157 | `_shutdown()` handles cleanup |
| M5 | Move events skip when src OR dst ignored | watcher.py:194-204 | Should skip only if dst ignored |
| M6 | Hardcoded retry delays (1s base, 2x multiplier) | _http_retry.py:84 | Convention says config, but internal retry |
| M7 | Event type strings hardcoded ("deleted", "moved") | event_reporter.py:54,91 | Enum would centralize |
| M8 | `periodic_interval_seconds` is int, others are float | config.py:66 | Inconsistent but harmless |
| M9 | `closefd=False` pattern in cache save | cache.py:158 | Works but unnecessarily complex |
| M10 | `snapshot()` shares inner dicts with internal state | cache.py:219-223 | Safe if set_after_ack replaces (it does) |
| M11 | `attach_summary` result discarded in both paths | capture.py:234,450 | Should check, at minimum log |

---

## Cloud/API minor findings (from cloud agent)

| # | Finding | File | Notes |
|---|---------|------|-------|
| C1 | API key re-read from env on every request | api.py:61 | Read once at startup instead |
| C2 | Sync blob_store.put() blocks event loop | capture.py:360 | Use async_put or to_thread |
| C3 | Sync _delete_with_blob_cleanup in async handler | api.py:298-380 | Make event_handler sync or wrap |
| C4 | knowledge_entries upsert lacks UNIQUE constraint | knowledge_entries.py:50 | Allows duplicate (dim,entity,tag) |
| C5 | describe_image.yaml declares unused mime_type var | describe_image.yaml:15 | Either use or remove |
| C6 | openai_provider error context reports wrong model | openai_provider.py:155 | Should be self._vision_model |
| C7 | Lazy CONFIG singleton has benign threading race | config.py:637-663 | Correct but wastes work |
| C8 | vault/watcher.py:1094 imports deleted capture_folder | vault/watcher.py:1094 | Legacy code, not used in cloud path |

---

## Test coverage note

The test quality agent found that `tests/test_vault/test_watcher.py` (1342 lines, 48 tests) was deleted on this branch. The vault watcher production code (`vault/watcher.py`, 1100+ lines) is unchanged. This is expected for the cloud-native rearchitecture — the daemon replaces the vault watcher — but the vault watcher code still ships and could be exercised in local-only mode. If local-only mode is being retired, the vault watcher module should be explicitly deprecated or removed. If it's being kept, tests need restoring.
