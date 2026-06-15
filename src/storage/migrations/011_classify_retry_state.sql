-- 011: classify retry-state columns on documents (Phase 8 Slice B).
--
-- classify_attempts (INTEGER, default 0) tracks how many times the classify
-- orchestrator has tried and failed to extract knowledge facts from this
-- document.  classify_last_error (TEXT, nullable) stores the last failure
-- reason so it can be fed back as self-correcting context on the next attempt.
--
-- Behavior IDs: P8-CLS-B-07 (retry loop), P8-CLS-B-08 (park at cap).

ALTER TABLE documents ADD COLUMN classify_attempts INTEGER DEFAULT 0;
ALTER TABLE documents ADD COLUMN classify_last_error TEXT;
