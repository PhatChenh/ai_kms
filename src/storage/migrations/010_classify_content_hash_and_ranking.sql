-- 010: classify-fingerprint column on documents, inert ranking signals on
-- knowledge_entries, and supporting indexes (Phase 8 Slice A).
--
-- classify_content_hash is a per-document fingerprint used by the classify
-- subsystem; nullable so legacy rows (NULL) are re-discovered by the Work Finder.
--
-- trust_score (REAL, default 0.5) and retrieval_count (INTEGER, default 0) are
-- intentionally inert during Phase 8:
--   - Phase 9 populates retrieval_count.
--   - Phase 10 populates trust_score.
-- They are added now so downstream code can reference them without a later
-- schema change.

ALTER TABLE documents ADD COLUMN classify_content_hash TEXT;

ALTER TABLE knowledge_entries ADD COLUMN trust_score REAL DEFAULT 0.5;
ALTER TABLE knowledge_entries ADD COLUMN retrieval_count INTEGER DEFAULT 0;

CREATE INDEX idx_docs_classify_hash ON documents(classify_content_hash);
CREATE INDEX idx_ke_trust ON knowledge_entries(trust_score DESC);
