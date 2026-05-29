# storage/audit_log.py = dumb SQL only; core/audit.py = domain façade

`storage/audit_log.py` owns `AuditEntry`, `append()`, and `query()`. `core/audit.py` translates `AIDecision` + pipeline metadata into an `AuditEntry` and calls `append()`. Neither layer knows the other's internals.

**Status:** accepted

**Consequences**

- Pipelines call `core.audit.write(...)`, NEVER `storage.audit_log.append(...)` directly.
- `storage/audit_log.py` exposes no `update_*` or `delete_*` symbols — absence is the enforcement mechanism, backed by DB triggers.
- Both layers remain independently testable.
