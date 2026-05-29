# audit_log columns: pipeline = named workflow, stage = pure-function name, source_ids = JSON list

Three columns (`pipeline`, `stage`, `source_ids`) record "what did the AI look at and at which step". `source_ids` is a JSON list because synthesis pipelines can combine multiple notes in a single decision.

**Status:** accepted

**Consequences**

- Every pipeline stage that makes an AI decision must populate `pipeline`, `stage`, and `source_ids`.
- Phase 8 (daily briefing) reads these columns as its primary input to reconstruct what the AI saw and why it decided what it did.
- Use `json.dumps(list)` — never `str(list)` — to ensure round-trip safety with `json.loads`.
