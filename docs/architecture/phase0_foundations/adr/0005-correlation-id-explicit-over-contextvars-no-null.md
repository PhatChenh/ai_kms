# correlation_id on AuditEntry: explicit > contextvars > hard Failure (no NULL inserts)

Precedence for filling `correlation_id`:
1. `entry.correlation_id` if set explicitly
2. `structlog.contextvars.get_contextvars().get("correlation_id")`
3. `Failure(recoverable=False)` — never insert a NULL

**Status:** accepted

**Consequences**

- Every pipeline entry point must call `new_correlation_id()` from `core/logging_setup.py`. The contextvars fallback means callers don't need to thread the ID explicitly — but they must set it at the top.
- NULL `correlation_id` makes Phase 8 briefing unable to group events by pipeline run, breaking the daily digest.
