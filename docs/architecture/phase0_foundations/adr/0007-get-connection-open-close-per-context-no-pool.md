# get_connection() is a context manager; open/close per-context, no connection pool

Each `with get_connection() as conn:` opens, uses, commits or rolls back, and closes. No thread-local singleton or pool. Single-writer CLI — simplicity trumps pooling overhead at this scale.

**Status:** accepted

**Consequences**

- Phase 4 (MCP server, long-running process) should revisit this. A daemon with many short-lived tool calls will pay per-call connection overhead. At that point a thread-local singleton or connection pool becomes relevant.
