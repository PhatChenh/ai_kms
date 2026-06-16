-- Migration 012: Fact search index for Phase 9
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    entry_id UNINDEXED,
    entity,
    fact
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(
    entry_id INTEGER PRIMARY KEY,
    embedding FLOAT[1024]
);
UPDATE schema_version SET version = 12;
